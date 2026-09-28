"""One Gaussian process per metric, fitted by MAP, and the "gives a value" classifier (T17.1 specification, section 4).

A metric's model sees the metric's own values, and only where the metric has one: a point that failed is never turned
into a number for it. Hyperparameters are found by MAP with a length-scale prior that grows with the square root of
the dimension (Hvarfner, Hellsten, Nardi, ICML 2024): what lets a plain Gaussian process work with 20 to 30 variables
and a few dozen points, where maximum likelihood picks short length scales and models noise.

Posterior quantities for the batch selection (section 8) are computed here from the fitted kernel, on the latent
function (the white-noise term left out): the mean over the candidates and their joint covariance.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass

import numpy as np
from scipy.linalg import solve_triangular
from scipy.optimize import minimize
from sklearn.exceptions import ConvergenceWarning
from sklearn.gaussian_process import GaussianProcessClassifier, GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, Kernel, Matern, WhiteKernel

LENGTH_BOUNDS = (0.02, 200.0)
NOISE_BOUNDS = (1e-8, 1e-1)
CONSTANT_BOUNDS = (1e-2, 1e2)
NOISE = (-4.0, 1.0)                  # the noise level is log-normal: ln(noise) ~ Normal(-4, 1), on standardized targets
NOISE_PRIOR = (NOISE[0] - NOISE[1] ** 2, NOISE[1])     # what the MAP estimate adds, in terms of ln(noise): see length_prior
PRIOR_DRAWS = 2                      # optimizer starts drawn from the prior, after its mode
LOG_TARGET_SPAN = 100                # max / min of all-positive values from which a metric is modelled as log10


def length_prior(d: int) -> tuple[float, float]:
    """The prior term of the MAP estimate for ``d`` active variables, as mean and standard deviation of a normal in
    ``ln(l)``. The length scale is log-normal, ``ln(l) ~ Normal(m, s)`` with ``m = sqrt(2) + 0.5 ln d`` and
    ``s = sqrt(3)``; the estimate maximizes the density of ``l`` itself (as the paper's reference implementation does),
    and that density, written in ``ln(l)``, is a normal's around ``m - s^2``: the log-normal's mode ``exp(m - s^2)``,
    0.65 of the unit cube's side for 10 variables. A normal around ``m`` instead (the first version) puts the mode at 13
    sides: models that are all but linear at the start, and a slower start on the benchmark's 20-variable problem."""
    s = math.sqrt(3.0)
    return math.sqrt(2.0) + 0.5 * math.log(max(d, 1)) - s * s, s


@dataclass(frozen=True)
class Transform:
    """Target transform decided per call from the training values: log10 when all are positive and span two decades,
    then standardized."""

    log: bool
    mean: float
    std: float

    @classmethod
    def of(cls, y: np.ndarray) -> Transform:
        log = bool(np.all(y > 0) and y.max() / y.min() >= LOG_TARGET_SPAN)
        z = np.log10(y) if log else y
        return cls(log, float(z.mean()), float(z.std()))

    def forward(self, y: np.ndarray) -> np.ndarray:
        return ((np.log10(y) if self.log else y) - self.mean) / self.std

    def backward(self, z: np.ndarray) -> np.ndarray:
        value = np.asarray(z) * self.std + self.mean
        return np.power(10.0, value) if self.log else value


class MetricModel:
    """A fitted metric: a Gaussian process on its standardized values, or a constant (all values equal, fewer than two
    values, or none at all: then nan) that predicts itself with zero variance."""

    def __init__(self, name: str, gp: GaussianProcessRegressor | None, transform: Transform | None, constant: float) -> None:
        self.name, self.gp, self.transform, self.constant = name, gp, transform, constant

    @property
    def length_scales(self) -> np.ndarray | None:
        return None if self.gp is None else np.atleast_1d(self.gp.kernel_.k1.k2.length_scale).astype(float)

    def predict(self, x: np.ndarray) -> np.ndarray:
        """Posterior mean at ``x`` (active unit coordinates), in the metric's own unit."""
        if self.gp is None:
            return np.full(len(x), self.constant)
        return self.transform.backward(self.gp.predict(x))

    def joint(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Latent posterior over the points ``x`` in the standardized scale: mean and covariance."""
        gp = self.gp
        cross = gp.kernel_.k1(gp.X_train_, x)                  # the white term is zero between distinct inputs
        v = solve_triangular(gp.L_, cross, lower=True, check_finite=False)
        return cross.T @ gp.alpha_, gp.kernel_.k1(x) - v.T @ v


def fit_metric(name: str, x: np.ndarray, y: np.ndarray, rng: np.random.Generator) -> MetricModel:
    """The model of one metric from its training inputs (active unit coordinates) and finite values."""
    if len(y) < 2 or np.all(y == y[0]):
        return MetricModel(name, None, None, float(y[0]) if len(y) else math.nan)
    transform = Transform.of(y)
    d = x.shape[1]
    mode, _ = length_prior(d)
    kernel = (ConstantKernel(1.0, CONSTANT_BOUNDS) * Matern(np.full(d, math.exp(mode)), LENGTH_BOUNDS, nu=2.5)
              + WhiteKernel(math.exp(NOISE_PRIOR[0]), NOISE_BOUNDS))
    gp = GaussianProcessRegressor(kernel, alpha=1e-10, optimizer=map_optimizer(kernel, d, rng), normalize_y=False,
                                  copy_X_train=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)     # a hyperparameter at its bound: the bounds are the spec's
        gp.fit(x, transform.forward(y))
    return MetricModel(name, gp, transform, math.nan)


def map_optimizer(kernel: Kernel, d: int, rng: np.random.Generator):
    """An ``optimizer`` for sklearn's Gaussian processes that minimizes the negative log marginal likelihood sklearn
    hands it minus the log prior: normal priors on the log length scales and the log noise level, none on the constant.
    Starts at the prior's mode (the kernel's initial values), then :data:`PRIOR_DRAWS` draws from the prior, L-BFGS-B
    from each; the best is kept."""
    mean, std = _prior_vectors(kernel, d)
    has_prior = ~np.isnan(mean)

    def optimizer(obj_func, initial_theta: np.ndarray, bounds: np.ndarray) -> tuple[np.ndarray, float]:
        def posterior(theta: np.ndarray) -> tuple[float, np.ndarray]:
            nll, grad = obj_func(theta, eval_gradient=True)
            z = np.where(has_prior, (theta - np.nan_to_num(mean)) / std, 0.0)
            return nll + 0.5 * float(z @ z), grad + np.where(has_prior, z / std, 0.0)

        starts = [initial_theta] + [np.clip(np.where(has_prior, rng.normal(np.nan_to_num(mean), std), initial_theta),
                                            bounds[:, 0], bounds[:, 1]) for _ in range(PRIOR_DRAWS)]
        best = None
        for start in starts:
            result = minimize(posterior, start, jac=True, method="L-BFGS-B", bounds=bounds)
            if best is None or result.fun < best.fun:
                best = result
        return best.x, float(best.fun)

    return optimizer


def _prior_vectors(kernel: Kernel, d: int) -> tuple[np.ndarray, np.ndarray]:
    """Prior mean and standard deviation of every entry of ``kernel.theta`` (nan: no prior), by hyperparameter name."""
    length = length_prior(d)
    mean, std = [], []
    for hp in kernel.hyperparameters:
        if hp.fixed:
            continue
        prior = length if hp.name.endswith("length_scale") else NOISE_PRIOR if hp.name.endswith("noise_level") else (math.nan, 1.0)
        mean += [prior[0]] * hp.n_elements
        std += [prior[1]] * hp.n_elements
    return np.array(mean), np.array(std)


class ValueModel:
    """Whether a point is scored (status ``ok`` or ``constraint_failed``): a Gaussian process classifier when the
    history holds failures and scored points; without failures every point is, with failures only nothing is known
    (probability 0.5 everywhere)."""

    def __init__(self, gpc: GaussianProcessClassifier | None, constant: float) -> None:
        self.gpc, self.constant = gpc, constant

    def probability(self, x: np.ndarray) -> np.ndarray:
        if self.gpc is None:
            return np.full(len(x), self.constant)
        return self.gpc.predict_proba(x)[:, 1]

    def latent(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
        """Posterior mean and covariance over ``x`` of the classifier's latent function (positive: scored), from the
        Laplace approximation sklearn fitted (its binary estimator's ``pi_``, ``W_sr_`` and ``L_``; the mean and the
        variances are the ones its ``predict_proba`` integrates over). None without a classifier."""
        if self.gpc is None:
            return None
        fitted = self.gpc.base_estimator_
        cross = fitted.kernel_(fitted.X_train_, x)
        v = solve_triangular(fitted.L_, fitted.W_sr_[:, None] * cross, lower=True, check_finite=False)
        return cross.T @ (fitted.y_train_ - fitted.pi_), fitted.kernel_(x) - v.T @ v


def fit_value_model(x: np.ndarray, scored: np.ndarray, rng: np.random.Generator) -> ValueModel:
    """The classifier of section 4.3. Same kernel family as the metrics' without the white-noise term; its
    hyperparameters by the same MAP optimizer (the specification leaves that open; the length-scale prior serves the
    classifier for the same reason it serves the regressions)."""
    if scored.all():
        return ValueModel(None, 1.0)
    if not scored.any():
        return ValueModel(None, 0.5)
    d = x.shape[1]
    kernel = ConstantKernel(1.0, CONSTANT_BOUNDS) * Matern(np.full(d, math.exp(length_prior(d)[0])), LENGTH_BOUNDS, nu=2.5)
    gpc = GaussianProcessClassifier(kernel, optimizer=map_optimizer(kernel, d, rng), copy_X_train=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        gpc.fit(x, scored.astype(int))
    return ValueModel(gpc, math.nan)
