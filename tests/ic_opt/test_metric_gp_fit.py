"""The metric models' fit by the direct likelihood (N-81, ``docs/refactor/N81_FAST_GP_SPEC.md``) against sklearn's own
computation. The fit before N-81 is kept here (:func:`sklearn_fit`). With ``models.DIRECT_LIKELIHOOD`` off (the
default) ``fit_metric`` is that fit, bit for bit. With it on, on the histories the metric_gp tests build, one synthetic
history of 300 points and the degenerate ones, the two are compared: the likelihood at every point sklearn's optimizer
visits, the optimizer's starts and random draws, the optimum it reaches, the predictions, the proposals. The CPU time
of the 300-point fit is printed.

Why the hyperparameters are compared to 1e-4 and not to the last bits: the likelihood is sklearn's to the last bit, its
gradient differs from sklearn's in the order of its sums only, but L-BFGS-B amplifies such differences from one
iteration to the next, and where the kernel matrix is ill-conditioned (a noise level near its bound) its stopping test
can fall one iteration earlier or later. sklearn's own fit moves by as much when only the number of BLAS threads changes
(at 100 points and more). The proposals on these histories are compared exactly; on longer synthetic runs (300 points
and more) they were measured to differ now and then, which is why the direct fit is not the default (``models.py``'s
docstring, the spec's section 5)."""

from __future__ import annotations

import copy
import math
import time
import warnings

import numpy as np
import pytest
from sklearn.exceptions import ConvergenceWarning
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel

from ic_opt.observation import Observations
from ic_opt.suggesters import metric_gp as mg
from ic_opt.suggesters.metric_gp import MetricGpSuggester, models
from ic_opt.suggesters.metric_gp.compose import modelled_metrics, true_arrays
from ic_opt.suggesters.metric_gp.coords import Coords
from tests.ic_opt.test_metric_gp import (
    bowl,
    bowl_spec,
    grid_points,
    observe,
    region_metrics,
    region_spec,
    smooth,
    spec_of,
    stepped,
    ten_by_ten,
    tt_history,
    tt_spec,
)

THETA = 1e-4          # largest difference of a log hyperparameter (measured on these histories: 1.1e-5)
MEAN = 1e-5           # of the predicted means, standardized values (standard deviation 1; measured: 1.2e-7)
SD = 1e-4             # of the predicted standard deviations, relative to the largest (measured: 6e-6)
POSTERIOR = 1e-7      # the log posterior at the optimum, relative: the direct fit's is not lower (measured: 5e-9)


def sklearn_fit(name: str, x: np.ndarray, y: np.ndarray, rng: np.random.Generator) -> models.MetricModel:
    """``models.fit_metric`` as it was before N-81: sklearn's regressor optimizing with its own likelihood and gradient,
    through the same ``map_optimizer``."""
    if len(y) < 2 or np.all(y == y[0]):
        return models.MetricModel(name, None, None, float(y[0]) if len(y) else math.nan)
    transform = models.Transform.of(y)
    d = x.shape[1]
    mode, _ = models.length_prior(d)
    kernel = (ConstantKernel(1.0, models.CONSTANT_BOUNDS) * Matern(np.full(d, math.exp(mode)), models.LENGTH_BOUNDS, nu=2.5)
              + WhiteKernel(math.exp(models.NOISE_PRIOR[0]), models.NOISE_BOUNDS))
    gp = GaussianProcessRegressor(kernel, alpha=1e-10, optimizer=models.map_optimizer(kernel, d, rng), normalize_y=False,
                                  copy_X_train=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        gp.fit(x, transform.forward(y))
    return models.MetricModel(name, gp, transform, math.nan)


@pytest.fixture
def direct(monkeypatch):
    """``fit_metric`` searches the hyperparameters on the direct likelihood."""
    monkeypatch.setattr(models, "DIRECT_LIKELIHOOD", True)


class Trace:
    """What the MAP optimizer sees during one fit: every evaluation ``(theta, value, gradient)`` handed to it, and the
    start of every L-BFGS-B run."""

    def __init__(self, monkeypatch) -> None:
        self.evaluations: list[tuple[np.ndarray, float, np.ndarray]] = []
        self.starts: list[np.ndarray] = []
        original_optimizer, original_minimize = models.map_optimizer, models.minimize

        def map_optimizer(kernel, d, rng):
            optimizer = original_optimizer(kernel, d, rng)

            def traced(obj_func, initial_theta, bounds):
                def counted(theta, eval_gradient=True):
                    value, grad = obj_func(theta, eval_gradient=eval_gradient)
                    self.evaluations.append((np.array(theta), float(value), np.array(grad)))
                    return value, grad
                return optimizer(counted, initial_theta, bounds)
            return traced

        def minimize(fun, x0, **kwargs):
            self.starts.append(np.array(x0))
            return original_minimize(fun, x0, **kwargs)

        monkeypatch.setattr(models, "map_optimizer", map_optimizer)
        monkeypatch.setattr(models, "minimize", minimize)

    def fit(self, fit, name, x, y, rng) -> tuple[models.MetricModel, list, list]:
        self.evaluations, self.starts = [], []
        return fit(name, x, y, rng), self.evaluations, self.starts


# -- the histories ------------------------------------------------------------------------------------------------------


def strategy_inputs(spec, rows) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """Per modelled metric what ``MetricGpSuggester.fit`` hands ``fit_metric``: the active unit coordinates and the
    finite values."""
    coords = Coords(spec)
    x = coords.unit(coords.indices([o.params for o in rows]))[:, coords.active]
    arrays = true_arrays(spec, rows)
    return [(name, x[np.isfinite(arrays[name])], arrays[name][np.isfinite(arrays[name])]) for name in modelled_metrics(spec)]


def synthetic(n: int = 300, d: int = 8, seed: int = 81) -> tuple[np.ndarray, np.ndarray]:
    """``n`` uniform points in ``d`` variables, a known smooth function of six of them plus noise of 0.05."""
    rng = np.random.default_rng(seed)
    x = rng.random((n, d))
    y = (np.sin(2 * np.pi * x[:, 0]) + 4 * (x[:, 1] - 0.5) ** 2 + x[:, 2] * x[:, 3] + 0.3 * np.cos(3 * x[:, 4])
         + 0.1 * x[:, 5])
    return x, y + 0.05 * rng.standard_normal(n)


def histories() -> list[tuple[str, np.ndarray, np.ndarray]]:
    """The metric histories of ``test_metric_gp.py``'s models, batch choice, failure and refusal tests, and the
    synthetic 300-point one."""
    rng = np.random.default_rng(5)
    x = rng.random((60, 3))
    span = 10 ** (3 * x[:, 0]) * (1 + 0.2 * x[:, 1])
    out = [("smooth", x[:40], smooth(x[:40])), ("three decades", x[:40], span[:40])]
    bowl_rows = observe(bowl_spec(), grid_points(bowl_spec(), np.random.default_rng(3).random((20, 2))), bowl)
    out += [(f"bowl {name}", *xy) for name, *xy in strategy_inputs(bowl_spec(), bowl_rows)]
    region = region_spec()
    region_rows = observe(region, grid_points(region, np.random.default_rng(4).random((30, 4))), region_metrics,
                          status_of=lambda p: "failed:spectre" if float(p["A"]) > 0.8 else None)
    out += [(f"region {name}", *xy) for name, *xy in strategy_inputs(region, region_rows)]
    out += [(f"tt {name}", *xy) for name, *xy in strategy_inputs(tt_spec(), tt_history(tt_spec()))]
    grid = ten_by_ten([{"metric": "g", "op": "lt", "value": "0.5"}], {"direction": "minimize", "expression": "f"}, ("f", "g"))
    chosen = np.random.default_rng(7).choice(99, 30, replace=False)
    cells = [(c // 10, c % 10) for c in range(100) if c != 88]
    grid_rows = observe(grid, grid_points(grid, [cells[i] for i in chosen]),
                        lambda p: {"f": float(int(p["X"]) + int(p["Y"])), "g": math.hypot(int(p["X"]) - 8, int(p["Y"]) - 8)})
    out += [(f"ten by ten {name}", *xy) for name, *xy in strategy_inputs(grid, grid_rows)]
    return out + [("synthetic 300", *synthetic())]


HISTORIES = histories()


# -- 0. the default is the fit it was -------------------------------------------------------------------------------------


@pytest.mark.parametrize(("label", "x", "y"), HISTORIES, ids=[h[0] for h in HISTORIES])
def test_the_default_fit_is_the_fit_before_n81(label, x, y):
    """``DIRECT_LIKELIHOOD`` off: the same hyperparameters, factor and weights as sklearn's own fit, to the last bit, from
    the same random numbers."""
    assert models.DIRECT_LIKELIHOOD is False
    first, second = np.random.default_rng(1), np.random.default_rng(1)
    new, old = models.fit_metric(label, x, y, first), sklearn_fit(label, x, y, second)
    assert first.bit_generator.state == second.bit_generator.state
    assert np.array_equal(new.gp.kernel_.theta, old.gp.kernel_.theta)
    assert np.array_equal(new.gp.L_, old.gp.L_) and np.array_equal(new.gp.alpha_, old.gp.alpha_)
    assert new.gp.log_marginal_likelihood_value_ == old.gp.log_marginal_likelihood_value_


# -- 1. the same likelihood, the same optimizer, the same optimum ---------------------------------------------------------


@pytest.mark.parametrize(("label", "x", "y"), HISTORIES, ids=[h[0] for h in HISTORIES])
def test_the_likelihood_is_sklearn_s_at_every_point_its_optimizer_visits(monkeypatch, label, x, y):
    """At every hyperparameter vector sklearn's own optimization evaluates, the direct likelihood is sklearn's to the
    last bit (+inf where sklearn's kernel matrix fails to factor), and its gradient is sklearn's to rounding: 1e-12 of
    the largest entry at the first point (the priors' mode; measured: 4e-14), 1e-5 anywhere (measured: 1.3e-6). The
    gradient is what is left of sums of terms as large as ``K^-1``'s entries, and with a noise level near its bound of
    1e-8 these are 1e8: the order of the sums then shows in the sixth digit, in sklearn's computation as in this one."""
    trace = Trace(monkeypatch)
    _model, visited, _starts = trace.fit(sklearn_fit, label, x, y, np.random.default_rng(1))
    z = models.Transform.of(y).forward(y)
    likelihood = models.Likelihood(x, z)
    for index, (theta, value, grad) in enumerate(visited):
        mine, slope = likelihood(theta)
        assert mine == value or (math.isinf(value) and math.isinf(mine)), (label, theta, mine, value)
        if math.isfinite(value):
            tolerance = 1e-12 if index == 0 else 1e-5
            assert np.abs(slope - grad).max() <= tolerance * max(1.0, float(np.abs(grad).max())), (label, theta, slope, grad)


@pytest.mark.parametrize(("label", "x", "y"), HISTORIES, ids=[h[0] for h in HISTORIES])
def test_the_fit_starts_where_sklearn_s_does_and_reaches_its_optimum(monkeypatch, direct, label, x, y):
    """Both fits from the same random state: the same starts (the prior's mode and two draws from the prior), the same
    random numbers taken; the direct fit's optimum is at least as high a posterior as sklearn's (to 1e-7), its
    hyperparameters within 1e-4 in log, its predictions within 1e-5 of the values' spread and its standard deviations
    within 1e-4. The likelihood evaluations each optimization took are printed (not asserted: see the module's
    docstring)."""
    trace = Trace(monkeypatch)
    rng = np.random.default_rng(1)
    first, second = copy.deepcopy(rng), copy.deepcopy(rng)
    old, old_visited, old_starts = trace.fit(sklearn_fit, label, x, y, first)
    new, new_visited, new_starts = trace.fit(models.fit_metric, label, x, y, second)
    assert first.bit_generator.state == second.bit_generator.state
    assert len(old_starts) == len(new_starts) == 1 + models.PRIOR_DRAWS
    assert all(np.array_equal(a, b) for a, b in zip(old_starts, new_starts, strict=True))
    old_posterior, new_posterior = old.gp.log_marginal_likelihood_value_, new.gp.log_marginal_likelihood_value_
    assert new_posterior >= old_posterior - POSTERIOR * max(1.0, abs(old_posterior)), (old_posterior, new_posterior)
    assert np.abs(new.gp.kernel_.theta - old.gp.kernel_.theta).max() <= THETA
    assert new.length_scales.shape == (x.shape[1],)
    probe = np.vstack([x, np.random.default_rng(2).random((200, x.shape[1]))])
    old_mean, old_sd = old.gp.predict(probe, return_std=True)
    new_mean, new_sd = new.gp.predict(probe, return_std=True)
    assert np.abs(new_mean - old_mean).max() <= MEAN * np.std(models.Transform.of(y).forward(y))
    assert np.abs(new_sd - old_sd).max() <= SD * max(old_sd.max(), 1e-12)
    print(f"{label}: likelihood evaluations sklearn {len(old_visited)}, direct {len(new_visited)}; largest log "
          f"hyperparameter difference {np.abs(new.gp.kernel_.theta - old.gp.kernel_.theta).max():.1e}")


# -- 2. degenerate histories ----------------------------------------------------------------------------------------------


def test_degenerate_histories_are_what_they_were(direct):
    """No value (nan), one value and equal values: a constant, no Gaussian process, as before. One variable (the
    Matern kernel is then isotropic: one length scale) and two points: a Gaussian process, as sklearn fitted it."""
    rng = np.random.default_rng(0)
    x = rng.random((30, 3))
    for y in (np.array([]), np.array([1.5]), np.full(30, 2.5)):
        new, old = models.fit_metric("m", x[: len(y)], y, rng), sklearn_fit("m", x[: len(y)], y, rng)
        assert new.gp is None and old.gp is None and new.length_scales is None
        assert (math.isnan(new.constant) and math.isnan(old.constant)) or new.constant == old.constant
        assert np.array_equal(new.predict(x[:3]), old.predict(x[:3]), equal_nan=True)
    for xs, ys in ((x[:, :1], smooth(x)), (x[:2], smooth(x[:2]))):
        new, old = models.fit_metric("m", xs, ys, np.random.default_rng(3)), sklearn_fit("m", xs, ys, np.random.default_rng(3))
        assert new.length_scales.shape == old.length_scales.shape == (xs.shape[1],)
        assert np.abs(new.gp.kernel_.theta - old.gp.kernel_.theta).max() <= THETA
        assert np.allclose(new.predict(x[:5, : xs.shape[1]]), old.predict(x[:5, : xs.shape[1]]), rtol=1e-6, atol=0)


# -- 3. the proposals --------------------------------------------------------------------------------------------------------


def big_spec():
    """Eight variables of 101 levels, an objective and a constraint: a search region, and 300 points to fit on."""
    return spec_of([stepped(f"X{i}", 0, 1, 0.01) for i in range(8)], ["f", "g"], [{"metric": "g", "op": "lt", "value": "0.5"}],
                   {"direction": "minimize", "expression": "f"})


def big_metrics(p):
    u = np.array([float(p[f"X{i}"]) for i in range(8)])
    return {"f": float(np.sin(2 * np.pi * u[0]) + 4 * (u[1] - 0.5) ** 2 + u[2] * u[3]), "g": float(np.abs(u[4] - u[5]) + 0.1 * u[6])}


def scenarios():
    tt = tt_spec()
    region = region_spec()
    grid = ten_by_ten([{"metric": "g", "op": "lt", "value": "0.5"}], {"direction": "minimize", "expression": "f"}, ("f", "g"))
    chosen = np.random.default_rng(7).choice(99, 30, replace=False)
    cells = [(c // 10, c % 10) for c in range(100) if c != 88]
    big = big_spec()
    return [
        ("bowl", bowl_spec(), observe(bowl_spec(), grid_points(bowl_spec(), np.random.default_rng(3).random((20, 2))), bowl), 10, 0),
        ("region with failures", region,
         observe(region, grid_points(region, np.random.default_rng(4).random((30, 4))), region_metrics,
                 status_of=lambda p: "failed:spectre" if float(p["A"]) > 0.8 else None), 5, 0),
        ("tt", tt, tt_history(tt), 5, 7),
        ("ten by ten, nothing feasible", grid,
         observe(grid, grid_points(grid, [cells[i] for i in chosen]),
                 lambda p: {"f": float(int(p["X"]) + int(p["Y"])), "g": math.hypot(int(p["X"]) - 8, int(p["Y"]) - 8)}), 3, 0),
        ("region 120", region, observe(region, grid_points(region, np.random.default_rng(12).random((120, 4))), region_metrics), 10, 2),
        ("eight variables 300", big, observe(big, grid_points(big, np.random.default_rng(13).random((300, 8))), big_metrics), 10, 3),
    ]


SCENARIOS = scenarios()


@pytest.mark.parametrize(("label", "spec", "rows", "n", "seed"), SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_the_strategy_proposes_what_it_proposed_with_sklearn_s_fit(monkeypatch, direct, label, spec, rows, n, seed):
    """The same history and seed, every metric fitted by sklearn's computation and then by the direct one: the same
    points with the same tags (on these histories; see the module's docstring for longer runs)."""
    monkeypatch.setattr(mg, "fit_metric", sklearn_fit)
    before = MetricGpSuggester(initial_trials=4).propose(spec, Observations(list(rows)), n, seed=seed)
    monkeypatch.setattr(mg, "fit_metric", models.fit_metric)
    after = MetricGpSuggester(initial_trials=4).propose(spec, Observations(list(rows)), n, seed=seed)
    assert len(after.raw) == n and (after.raw, after.tags) == (before.raw, before.tags)


# -- 4. the time ----------------------------------------------------------------------------------------------------------


def test_the_cpu_time_of_the_300_point_fit(direct, capsys):
    """Not a pass / fail figure: the CPU seconds of one fit of the synthetic 300-point history, sklearn's computation
    against the direct one, under this process's BLAS threads (``threadpoolctl``), printed for the record."""
    from threadpoolctl import threadpool_info

    x, y = synthetic()
    t0 = time.process_time()
    old = sklearn_fit("m", x, y, np.random.default_rng(4))
    t1 = time.process_time()
    new = models.fit_metric("m", x, y, np.random.default_rng(4))
    t2 = time.process_time()
    assert old.gp is not None and new.gp is not None
    threads = sorted({pool["num_threads"] for pool in threadpool_info() if pool["user_api"] == "blas"})
    with capsys.disabled():
        print(f"\nN-81 300 points x 8 variables, BLAS threads {threads}: sklearn {t1 - t0:.2f} CPU s, direct {t2 - t1:.2f} CPU s "
              f"({(t1 - t0) / (t2 - t1):.1f} times less)")
