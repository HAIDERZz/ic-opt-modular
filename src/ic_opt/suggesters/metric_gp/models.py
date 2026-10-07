"""One Gaussian process per metric, fitted by MAP, and the "gives a value" classifier (T17.1 specification, section 4).

A metric's model sees the metric's own values, and only where the metric has one: a point that failed is never turned
into a number for it. Hyperparameters are found by MAP with a length-scale prior that grows with the square root of
the dimension (Hvarfner, Hellsten, Nardi, ICML 2024): what lets a plain Gaussian process work with 20 to 30 variables
and a few dozen points, where maximum likelihood picks short length scales and models noise.

Posterior quantities for the batch selection (section 8) are computed here from the fitted kernel, on the latent
function (the white-noise term left out): the mean over the candidates and their joint covariance.

The search for a metric's hyperparameters, and why it is still sklearn's (N-81, ``docs/refactor/N81_FAST_GP_SPEC.md``).
sklearn's regressor hands the MAP optimizer its log marginal likelihood and gradient, and for the gradient builds an
``(n, n, parameters)`` array at every evaluation: most of a proposal's time on a real circuit's history (12 metrics, 20
variables) once a few hundred points are in it. :class:`Likelihood` is the same function computed without that array;
with :data:`DIRECT_LIKELIHOOD` the search runs on it (the same kernel, bounds, priors, starts and random draws, the same
``alpha`` on the diagonal) and the regressor is fitted once at the hyperparameters found, so that everything read from
a fitted model is computed as before. Its value is sklearn's to the last bit and its gradient to rounding, but L-BFGS-B
amplifies the gradient's rounding from one iteration to the next, and an ill-conditioned kernel (a noise level near
1e-8) moves its stop by an iteration: the optimum is the same only to the optimizer's own tolerance. Measured (the
spec's section 5): on 34 recorded metric_gp runs, 1131 fits of up to 92 points, 82 % of the hyperparameter vectors equal
sklearn's to 1e-8 in log and 99.3 % to 1e-4, one fit reached another local optimum (a higher posterior), and all 249
proposals were the same; on synthetic runs of 100 to 400 points (four benchmark problems, 28 proposals at one BLAS
thread), where 70 of 91 hyperparameter vectors agree to 1e-4 and the largest difference is 1e-3, two proposals
differed -- at 300 points two points of a batch swapped places, at 350 points three of ten were others.
(sklearn's own search is not that stable either: on the same 28 histories its proposal changed once when only the BLAS
threads went from one to two, five of ten points.) A proposal must not change -- the user's condition for N-81 -- so the
default stays sklearn's search. The direct one takes 4 times less CPU on 300 points and 8 variables at one thread
(1.85 s against 0.47 s), 3.3 to 5.9 times less for a proposal's fits at 400 points (2 to 5 metrics, 6 to 20 variables).
Beyond a few hundred points the two searches may differ more (the research measured 0.005 in log at 775 values).
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass

import numpy as np
from scipy.linalg import LinAlgError, cho_solve, cholesky, solve_triangular
from scipy.optimize import minimize
from scipy.spatial.distance import pdist, squareform
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
SK_ALPHA = 1e-10                     # added to the kernel's diagonal: GaussianProcessRegressor(alpha=1e-10), as always
SQRT5 = math.sqrt(5.0)
DIRECT_LIKELIHOOD = False            # N-81: True searches the hyperparameters on Likelihood; False (default) on sklearn's


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
    """The model of one metric from its training inputs (active unit coordinates) and finite values: sklearn's
    regressor with the hyperparameters :func:`map_optimizer` finds, searching them on sklearn's own likelihood and
    gradient (the regressor's fit) -- or, with :data:`DIRECT_LIKELIHOOD`, on :class:`Likelihood`, the regressor then
    fitted once at them (``optimizer=None``)."""
    if len(y) < 2 or np.all(y == y[0]):
        return MetricModel(name, None, None, float(y[0]) if len(y) else math.nan)
    transform = Transform.of(y)
    z = transform.forward(y)
    d = x.shape[1]
    kernel = metric_kernel(d)
    if DIRECT_LIKELIHOOD:
        theta, value = map_optimizer(kernel, d, rng)(Likelihood(x, z), kernel.theta, kernel.bounds)
        gp = GaussianProcessRegressor(kernel.clone_with_theta(theta), alpha=SK_ALPHA, optimizer=None, normalize_y=False,
                                      copy_X_train=False).fit(x, z)
        gp.log_marginal_likelihood_value_ = -value  # what sklearn's own search leaves there: the optimum's log posterior
        return MetricModel(name, gp, transform, math.nan)
    gp = GaussianProcessRegressor(kernel, alpha=SK_ALPHA, optimizer=map_optimizer(kernel, d, rng), normalize_y=False,
                                  copy_X_train=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)     # a hyperparameter at its bound: the bounds are the spec's
        gp.fit(x, z)
    return MetricModel(name, gp, transform, math.nan)


def metric_kernel(d: int) -> Kernel:
    """The metrics' kernel for ``d`` active variables at the priors' modes: ConstantKernel x Matern 5/2 with one length
    scale per variable, plus WhiteKernel. Its ``theta``: ln constant, the ``d`` ln length scales, ln noise level (one
    length scale when ``d`` is 1)."""
    mode, _ = length_prior(d)
    return (ConstantKernel(1.0, CONSTANT_BOUNDS) * Matern(np.full(d, math.exp(mode)), LENGTH_BOUNDS, nu=2.5)
            + WhiteKernel(math.exp(NOISE_PRIOR[0]), NOISE_BOUNDS))


class Likelihood:
    """The negative log marginal likelihood of :func:`metric_kernel`'s Gaussian process on standardized values ``z``
    and its gradient in ``theta`` (ln constant, ln length scales, ln noise level): what sklearn's regressor hands its
    optimizer as ``obj_func`` (``alpha`` :data:`SK_ALPHA`), +inf and a zero gradient where the kernel matrix is not
    positive definite.

    Computed here because sklearn's way is what a proposal's time went into: at every evaluation it builds the kernel's
    gradient as an ``(n, n, parameters)`` array, from an ``(n, n, d)`` array of squared differences per variable, and
    contracts it with the ``(n, n)`` matrix ``W = alpha alpha^T - K^-1``. Here the squared differences per variable are
    computed once, for the pairs ``i < j``, and every gradient entry is one sum over the pairs: for the Matern 5/2
    kernel ``k = c (1 + t + t^2 / 3) exp(-t)``, ``t = sqrt5 r``, ``dk / d ln l_m = c 5/3 (1 + t) exp(-t) (x_im - x_jm)^2
    / l_m^2``. Everything up to ``W`` is sklearn's own arithmetic (the kernel matrix from ``pdist`` of the scaled inputs,
    its Cholesky factor, ``alpha``, ``K^-1`` by ``cho_solve``), so the likelihood is sklearn's to the last bit and the
    kernel matrix fails to factor exactly where sklearn's does. The gradient differs from sklearn's by the order of its
    sums only: 1e-14 of its largest entry at the priors' mode, up to 1e-6 where the noise level nears its bound of 1e-8
    (the gradient is then what is left of sums of terms as large as ``K^-1``'s entries, 1e8, in either computation).
    The sums over pairs are numpy's (``einsum``), not BLAS calls whose order of summation changes with the number of
    threads."""

    def __init__(self, x: np.ndarray, z: np.ndarray) -> None:
        self.x = np.asarray(x, dtype=float)
        self.y = np.asarray(z, dtype=float)[:, np.newaxis]
        self.n = len(self.x)
        # (d, pairs): (x_im - x_jm)^2 for the pairs i < j, in the order of pdist and squareform
        self.squared = np.stack([pdist(self.x[:, m : m + 1], "sqeuclidean") for m in range(self.x.shape[1])])

    def __call__(self, theta: np.ndarray, eval_gradient: bool = True) -> tuple[float, np.ndarray]:
        """``(-log marginal likelihood, its gradient)`` at ``theta``; the gradient is always computed (the optimizer
        asks for it at every evaluation)."""
        n = self.n
        c, length, s = np.exp(theta[0]), np.exp(theta[1:-1]), np.exp(theta[-1])
        t = pdist(self.x / length, metric="euclidean") * SQRT5         # sklearn's Matern: distances of the scaled inputs
        decay = np.exp(-t)
        cm = c * ((1.0 + t + t**2 / 3.0) * decay)                       # the kernel over the pairs
        k = squareform(cm, checks=False)
        k[np.diag_indices(n)] = c + s + SK_ALPHA
        try:
            low = cholesky(k, lower=True, check_finite=False)
        except LinAlgError:
            return math.inf, np.zeros_like(theta)
        alpha = cho_solve((low, True), self.y, check_finite=False)
        lml = -0.5 * np.einsum("ik,ik->k", self.y, alpha)
        lml -= np.log(np.diag(low)).sum()
        lml -= n / 2 * np.log(2 * np.pi)
        w = np.outer(alpha, alpha)
        w -= cho_solve((low, True), np.eye(n), check_finite=False)   # W = alpha alpha^T - K^-1
        w_pairs = squareform(w + w.T, checks=False)                   # W_ij + W_ji, i < j: K^-1 is symmetric to rounding
        w_diag = np.diag(w)
        grad = np.empty_like(theta)
        grad[0] = 0.5 * (float(np.einsum("p,p->", w_pairs, cm)) + c * float(w_diag.sum()))
        slope = w_pairs * ((c * (5.0 / 3.0)) * (1.0 + t) * decay)
        grad[1:-1] = 0.5 * np.einsum("mp,p->m", self.squared, slope) / length**2
        grad[-1] = 0.5 * s * float(w_diag.sum())
        return -float(lml.sum()), -grad


def map_optimizer(kernel: Kernel, d: int, rng: np.random.Generator):
    """An ``optimizer`` for sklearn's Gaussian processes that minimizes the negative log marginal likelihood sklearn
    hands it minus the log prior: normal priors on the log length scales and the log noise level, none on the constant.
    Starts at the prior's mode (the kernel's initial values), then :data:`PRIOR_DRAWS` draws from the prior, L-BFGS-B
    from each; the best is kept. With :data:`DIRECT_LIKELIHOOD` the metrics' fit hands it :class:`Likelihood` in place
    of sklearn's ``obj_func``."""
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
