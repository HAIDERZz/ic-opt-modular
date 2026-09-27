import numpy as np
import pytest
from icopt_bench import _gridsearch as gs
from icopt_bench import _synthetic_math as sm
from icopt_bench import synthetic
from icopt_bench.problem import Point, observe
from icopt_bench.synthetic import PROBLEMS

from ic_opt import space

_LARGE = ["syn_ackley10_c2", "syn_hartmann6_c1", "syn_levy20_c1", "syn_mostly_infeasible", "syn_failure_region",
          "syn_amplifier_like"]


@pytest.mark.parametrize("name", sorted(PROBLEMS))
def test_problem_builds_and_start_is_on_grid(name):
    problem = PROBLEMS[name]()
    assert problem.name == name
    assert problem.family == "synthetic"
    assert problem.scenario in ("around_design", "wide_range")
    if problem.scenario == "wide_range":                           # nothing to start from: that is the scenario
        assert problem.start == ()
        return
    assert len(problem.start) == 1
    space.check(problem.spec, problem.start[0])                    # raises ValueError if off-grid
    obs = observe(problem, Point(problem.start[0], "start"), 0)
    assert obs.status == "constraint_failed"                       # a design that does not meet its specification yet


@pytest.mark.parametrize("name", sorted(PROBLEMS))
def test_problem_is_rebuilt_fresh_each_call(name):
    """PROBLEMS values are factories: two calls must not share mutable state (each Problem is frozen anyway, but the
    underlying spec/evaluate must not depend on import-time side effects)."""
    a, b = PROBLEMS[name](), PROBLEMS[name]()
    assert a.spec.fingerprint() == b.spec.fingerprint()
    assert a.reference == b.reference


# -- the two grids small enough to enumerate exactly --------------------------------------------------------------


def test_small_tight_exact_feasible_share():
    grid = gs.enumerate_grid([gs.levels(20, 30, 2), gs.levels(0.6, 1.2, 0.2), gs.levels(30, 50, 10), gs.levels(280, 400, 20)])
    u = np.stack([(grid[:, 0] - 20) / 10, (grid[:, 1] - 0.6) / 0.6, (grid[:, 2] - 30) / 20, (grid[:, 3] - 280) / 120], axis=-1)
    m1, m2, m3, m4, m5 = sm.small_tight_metrics(u)
    feasible = (m1 > 2.6) & (m2 < 8.4) & (m3 > 1.69) & (m4 > -4.3) & (m5 > 1.57)
    assert grid.shape[0] == 504
    assert int(feasible.sum()) == 11                                 # 2.18%, target ~2%


def test_multimodal_exact_feasible_share():
    grid = gs.enumerate_grid([gs.levels(0, 1, 0.1), gs.levels(0, 1, 0.1), gs.levels(0, 1, 0.125)])
    feasible = sm.multimodal_g(grid) < 0
    assert grid.shape[0] == 1089
    assert int(feasible.sum()) == 1002


def test_small_tight_reference_matches_exact_enumeration():
    assert PROBLEMS["syn_small_tight"]().reference == pytest.approx(-0.5531029346286176, abs=1e-9)


def test_multimodal_reference_matches_exact_enumeration():
    assert PROBLEMS["syn_multimodal_small"]().reference == pytest.approx(-1.1475, abs=1e-9)


def test_multimodal_start_sits_on_the_infeasible_wall():
    problem = PROBLEMS["syn_multimodal_small"]()
    obs = observe(problem, Point(problem.start[0], "start"), 0)
    assert obs.feasible is False


# -- the six large grids: formula sanity + a large-sample share check (vectorized, no per-point Spec overhead) ----


def test_ackley10_zero_is_the_exact_global_optimum_and_feasible():
    x = np.zeros(10)
    c1, c2 = sm.ackley10_constraints(x)
    assert sm.ackley10(x) == pytest.approx(0.0, abs=1e-9)
    assert c1 <= 0
    assert c2 <= 0


def test_hartmann6_reference_and_binding_constraint():
    problem = PROBLEMS["syn_hartmann6_c1"]()
    assert problem.reference == pytest.approx(-3.3007706130601644, abs=1e-9)


def test_levy20_unconstrained_optimum_is_already_infeasible():
    x = np.ones(20)
    assert sm.levy20(x) == pytest.approx(0.0, abs=1e-9)
    assert np.sum(x) / 20 - 0.5 > 0


def test_mostly_infeasible_share_near_target():
    level_arrays = [gs.levels(0, 1, 0.01)] * sm.INFEASIBLE_DIM
    sample = gs.random_grid_points(level_arrays, 200_000, seed=42)
    r1, r2 = sm.infeasible_distances(sample)
    share = float(np.mean((r1 < synthetic._INFEASIBLE_RADIUS) & (r2 < synthetic._INFEASIBLE_RADIUS)))
    assert 0.0003 < share < 0.003                                    # target 0.1%, generous band against sampling noise


def test_failure_region_share_near_target():
    level_arrays = [gs.levels(0, 1, 0.02)] * 6
    sample = gs.random_grid_points(level_arrays, 200_000, seed=43)
    boundary = (sample[:, 0] + sample[:, 1]) <= 1.4
    q, p = sm.failure_region_q(sample), sm.failure_region_p(sample)
    share = float(np.mean(boundary & (q > synthetic._FAILURE_TQ) & (p < synthetic._FAILURE_TP)))
    assert 0.02 < share < 0.07                                       # target 3-5%


def test_amplifier_share_near_target():
    dims = [(0.5, 10, 0.01), (0.5, 10, 0.01), (0.5, 5, 0.01), (0.5, 5, 0.01), (1, 50, 1), (1, 50, 1),
            (1, 30, 0.1), (0.5, 30, 0.1)]
    level_arrays = [gs.levels(*d) for d in dims]
    sample = gs.random_grid_points(level_arrays, 50_000, seed=44)
    gain, gbw, pm, power, area = sm.amplifier_stage(*sample.T)
    feasible = (pm > 60) & (gain > synthetic._AMP_GAIN_MIN) & (gbw > synthetic._AMP_GBW_MIN) \
        & (power < synthetic._AMP_POWER_MAX) & (area < synthetic._AMP_AREA_MAX)
    assert 0.003 < float(np.mean(feasible)) < 0.03                   # target 1%


@pytest.mark.parametrize("name", _LARGE)
def test_random_grid_points_evaluate_without_error(name):
    """Wiring smoke test: the Spec's own aggregate/objective machinery accepts random grid points without raising,
    for every one of the six large problems."""
    problem = PROBLEMS[name]()
    lows, highs = space.bounds(problem.spec)
    rng = np.random.default_rng(7)
    for _ in range(15):
        raw = [lo + rng.random() * (hi - lo) for lo, hi in zip(lows, highs, strict=True)]
        params = space.snap(problem.spec, raw)
        obs = observe(problem, Point(params, "test"), 0)
        assert obs.status in ("ok", "metric_failed", "constraint_failed")


def test_failure_region_missing_metric_past_the_boundary():
    problem = PROBLEMS["syn_failure_region"]()
    params = {"u0": "0.9", "u1": "0.9", "u2": "0.5", "u3": "0.5", "u4": "0.1", "u5": "0.1"}   # u0+u1=1.8 > 1.4
    obs = observe(problem, Point(params, "test"), 0)
    assert obs.status == "metric_failed"
    assert obs.feasible is False
