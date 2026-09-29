"""The metric_gp strategy (T17.1 specification, section 13): coordinates, the array evaluator, the composer, the models,
the batch choice, the region replay, tags, refusals, determinism, and that no penalty reaches a model."""

from __future__ import annotations

import ast
import itertools
import math
import re
from pathlib import Path

import numpy as np
import pytest

from ic_opt import objective as objective_contract
from ic_opt import space
from ic_opt.blocks.optimize import optimize, suggest
from ic_opt.deck import Deck
from ic_opt.observation import ChildResult, Observation, Observations
from ic_opt.recipe import PLAN_MODE
from ic_opt.sim.corner import aggregate
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.store import RunStore
from ic_opt.suggesters import make
from ic_opt.suggesters import metric_gp as mg
from ic_opt.suggesters.metric_gp import (
    DEVICES_REFUSAL,
    MetricGpSuggester,
    candidates,
    models,
    region,
    select,
)
from ic_opt.suggesters.metric_gp.compose import Composer, metric_scales
from ic_opt.suggesters.metric_gp.coords import Coords, keys
from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor, make_spec, minimal_spec

PACKAGE = Path(mg.__file__).parent


def spec_of(variables, metrics, constraints=(), objective=None, **extra) -> Spec:
    return make_spec(variables=variables, metrics=[{"name": m, "unit": "1", "expression": f'value(getData("{m}"))'} for m in metrics],
                     constraints=list(constraints), objective=objective, **extra)


def integer(name, lower, upper, step=1):
    return {"name": name, "kind": "integer", "lower": str(lower), "upper": str(upper), "step": str(step)}


def stepped(name, lower, upper, step):
    return {"name": name, "kind": "continuous_step", "lower": str(lower), "upper": str(upper), "step": str(step)}


def observe(spec, points, metrics_of, first=0, status_of=None) -> Observations:
    """What an engine records for ``points`` whose aggregated metrics are ``metrics_of(params)``; ``status_of(params)``
    may name a failed stage (the point then has no metrics)."""
    rows = Observations()
    for index, point in enumerate(points, first):
        failed = status_of(point.params) if status_of else None
        metrics = {} if failed else metrics_of(point.params)
        ev = objective_contract.evaluate(spec, metrics)
        rows.append(Observation(obs_id=f"obs_{index}", params=point.params, origin=point.origin, metrics=metrics, fom=ev.fom,
                                objective=ev.objective, feasible=ev.feasible, constraint_penalty=ev.constraint_penalty,
                                status=failed or ev.status, spec_fingerprint="s", pipeline_fingerprint="p",
                                started_at="t", finished_at="t"))
    return rows


def grid_points(spec, coords_list, origin="user") -> list[Point]:
    return [Point(space.snap(spec, list(c)), origin) for c in coords_list]


def run(spec, budget, batch, metrics_of, *, seed=0, history=None, record=None, **kwargs) -> Observations:
    history = Observations(history or [])
    while len(history) < budget:
        if record is not None:
            record(history)
        points = suggest(spec, history, min(batch, budget - len(history)), strategy="metric_gp", seed=seed, **kwargs)
        history.extend(observe(spec, points, metrics_of, len(history)))
    return history


# -- 1. coordinates ---------------------------------------------------------------------------------------------------


def coords_spec():
    return spec_of([integer("N", 1, 9), stepped("W", "0.5u", "10u", "0.5u"), stepped("R", "0", "1", "0.1"), integer("K", 5, 5)],
                   ["m"], objective={"direction": "minimize", "expression": "m"})


def test_coordinates_linear_logarithmic_and_one_level():
    spec = coords_spec()
    c = Coords(spec)
    assert c.log.tolist() == [False, True, False, False]           # 9 / 1 < 10; 10 / 0.5 >= 10; lower 0; one level
    assert c.active.tolist() == [True, True, True, False] and c.d == 3
    assert c.unit_levels[1][0] == 0 and c.unit_levels[1][-1] == 1
    assert math.isclose(c.unit_of_raw([[1, 1.0, 0, 5]])[0, 1] - c.unit_of_raw([[1, 0.5, 0, 5]])[0, 1],
                        c.unit_of_raw([[1, 10.0, 0, 5]])[0, 1] - c.unit_of_raw([[1, 5.0, 0, 5]])[0, 1])   # per octave
    assert c.unit_levels[3].tolist() == [0.0] and c.raw_levels[3].tolist() == [5.0]
    for i, levels in enumerate(c.raw_levels):                      # value -> unit -> value on every level, and -> index
        raw = np.tile([1.0, 0.5, 0.0, 5.0], (len(levels), 1))
        raw[:, i] = levels
        unit = c.unit_of_raw(raw)
        assert np.allclose(c.raw_of_unit(unit)[:, i], levels, rtol=1e-12, atol=1e-12)
        assert c.snap(unit)[:, i].tolist() == list(range(len(levels)))
        idx = c.snap(unit)
        snapped = [space.snap(spec, r) for r in c.raw(idx).tolist()]      # suggest snaps again: nothing changes
        assert [space.to_raw(spec, p) for p in snapped] == c.raw(idx).tolist()
        assert c.indices(snapped).tolist() == idx.tolist()
    u = np.random.default_rng(0).random((200, 4))
    idx = c.snap(u)
    assert np.array_equal(c.snap(c.unit(idx)), idx) and (idx[:, 3] == 0).all()          # idempotent; one level: index 0


def test_a_proposal_is_on_the_grid_and_the_log_design_is_even_per_decade():
    spec = spec_of([integer("N", 1, 1000), stepped("R", "0", "1", "0.01")], ["m"], objective={"direction": "minimize", "expression": "m"})
    proposal = MetricGpSuggester(initial_trials=32).propose(spec, Observations(), 32, seed=4)
    assert [space.to_raw(spec, space.snap(spec, r)) for r in proposal.raw] == proposal.raw
    n = np.array([r[0] for r in proposal.raw])
    assert 0.25 < np.mean(n < 10) < 0.42 and 0.25 < np.mean(n >= 100) < 0.42           # a third per decade (linear: 1% < 10)
    more = MetricGpSuggester(initial_trials=40).propose(spec, Observations(), 40, seed=4)
    assert more.raw[:32] == proposal.raw and proposal.tags == ["init"] * 32               # prefix-stable


# -- 2. the array evaluator ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("expression", ["a / b", "a % b", "a ** b", "b ** 0.5", "ln(a) - ln(b * c)", "min(a, b, c) - max(a, 2)",
                                        "-(a * b) + c / 2", "(a - b) ** -1", "10 ** a", "max(0, min(1, (a - 2.6) / 3))",
                                        "(a + b) / (c - 1) % 3", "+a ** 2 / ln(c)"])
def test_the_array_evaluator_equals_the_scalar_one_and_is_nan_where_it_raises(expression):
    rng = np.random.default_rng(1)
    pool = np.array([0.0, 1.0, -1.0, 2.0, -2.5, 0.5, 3.0, 1e-3, 400.0, -400.0, 7.25])
    values = {m: np.r_[rng.choice(pool, 400), rng.normal(0, 5, 200)] for m in "abc"}
    array = objective_contract.evaluate_expression_array(expression, values)
    raised = 0
    for i in range(len(array)):
        try:
            scalar = objective_contract.evaluate_expression(expression, {m: float(v[i]) for m, v in values.items()})
        except (ArithmeticError, ValueError):
            raised += 1
            assert math.isnan(array[i]), (expression, {m: v[i] for m, v in values.items()}, array[i])
            continue
        assert array[i] == pytest.approx(scalar, rel=1e-12, abs=1e-300), (expression, {m: v[i] for m, v in values.items()})
    assert raised == int(np.isnan(array).sum())


# -- 3. the composer ------------------------------------------------------------------------------------------------------


def test_composer_residual_signs_violation_scales_and_the_objective_less_case():
    constraints = [{"metric": "a", "op": "lt", "value": "1"}, {"metric": "b", "op": "le", "value": "2"},
                   {"metric": "c", "op": "gt", "value": "3"}, {"metric": "d", "op": "ge", "value": "0"}]
    spec = spec_of([integer("N", 1, 9)], ["a", "b", "c", "d"], constraints, objective={"direction": "maximize", "expression": "a + c"})
    composer = Composer(spec)
    arrays = {"a": np.array([0.0, 3.0]), "b": np.array([2.0, 5.0]), "c": np.array([4.0, 1.0]), "d": np.array([1.0, -1e-3])}
    assert composer.residuals(arrays).tolist() == [[-1.0, 0.0, -1.0, -1.0], [2.0, 3.0, 2.0, 1e-3]]
    scales = {"a": 2.0, "b": 1.0, "c": 4.0, "d": 1e-3}
    assert composer.violation(arrays, scales).tolist() == [0.0, 2 / 2 + 3 / 1 + 2 / 4 + 1.0]   # d: tiny threshold, own spread
    assert composer.objective(arrays, scales).tolist() == [-4.0, -4.0]                       # maximize: minimization form
    rows = observe(spec, grid_points(spec, [[1], [2], [3]]), lambda p: {"a": float(p["N"]), "b": 1.0, "c": 3.5, "d": 0.0})
    assert metric_scales(spec, rows) == {"a": pytest.approx(np.std([1, 2, 3])), "b": 1e-12, "c": 1e-12, "d": 1e-12}
    bare = Composer(spec_of([integer("N", 1, 9)], ["a", "b", "c", "d"], constraints))
    assert bare.objective(arrays, scales).tolist() == [pytest.approx(0.0), 3.0]    # - min(-residual / scale): b misses by 3 of its spread
    assert bare.objective({**arrays, "a": np.array([-3.0, 3.0])}, scales)[0] == pytest.approx(0.0)
    assert bare.objective({**arrays, "b": np.array([1.0, 5.0])}, scales)[0] == pytest.approx(-0.25)   # c's margin 1/4 is nearest


# -- 4. models -------------------------------------------------------------------------------------------------------------


def smooth(x):
    return np.sin(3 * x[:, 0]) + x[:, 1] ** 2 - 0.5 * x[:, 2]


def test_a_metric_model_predicts_held_out_points_and_constants_and_logs_come_back_in_their_unit():
    rng = np.random.default_rng(5)
    x = rng.random((60, 3))
    model = models.fit_metric("m", x[:40], smooth(x[:40]), rng)
    assert model.gp is not None and not model.transform.log
    assert np.corrcoef(model.predict(x[40:]), smooth(x[40:]))[0, 1] > 0.95
    constant = models.fit_metric("k", x[:40], np.full(40, 2.5), rng)
    assert constant.gp is None and constant.predict(x[40:]).tolist() == [2.5] * 20
    assert select.samples(constant, x[40:], 3, rng) is None           # zero variance: no sampling
    assert math.isnan(models.fit_metric("none", x[:0], np.array([]), rng).predict(x[:2])[0])
    span = 10 ** (3 * x[:, 0]) * (1 + 0.2 * x[:, 1])                  # three decades
    logged = models.fit_metric("g", x[:40], span[:40], rng)
    assert logged.transform.log
    predicted = logged.predict(x[40:])
    assert np.all(np.abs(np.log10(predicted / span[40:])) < 0.15)     # in the metric's own unit, not log10 of it
    assert not models.fit_metric("g", x[:40], span[:40] + 100, rng).transform.log


# -- 5. "gives a value" ----------------------------------------------------------------------------------------------------


def test_the_value_model_learns_where_points_fail():
    spec = spec_of([stepped("X", 0, 1, 0.02), stepped("Y", 0, 1, 0.02)], ["m"], objective={"direction": "minimize", "expression": "m"})
    rng = np.random.default_rng(2)
    points = grid_points(spec, rng.random((60, 2)))
    rows = observe(spec, points, lambda p: {"m": float(p["Y"])}, status_of=lambda p: "failed:spectre" if float(p["X"]) > 0.5 else None)
    coords = Coords(spec)
    _models, value = MetricGpSuggester().fit(spec, coords, list(rows), 0)
    p = value.probability(np.array([[0.1, 0.5], [0.9, 0.5]]))
    assert p[0] > 0.7 and p[1] < 0.3, p
    assert models.fit_value_model(np.zeros((3, 2)), np.array([True] * 3), rng).probability(np.zeros((2, 2))).tolist() == [1.0, 1.0]
    assert models.fit_value_model(np.zeros((3, 2)), np.array([False] * 3), rng).probability(np.zeros((2, 2))).tolist() == [0.5, 0.5]


def test_a_region_known_to_fail_is_out_as_a_whole_and_an_unknown_one_every_other_time():
    """Failures in the half X > 0.5 of two variables, 60 observations. Of 2000 slots the classifier's coherent sample
    calls a candidate deep in the failing half scored in 11%, one deep in the scored half in 91%, one on the border
    in half (the latent there: mean 0); the latent's mean and variances are those sklearn's own probability uses."""
    spec = spec_of([stepped("X", 0, 1, 0.02), stepped("Y", 0, 1, 0.02)], ["m"], objective={"direction": "minimize", "expression": "m"})
    rng = np.random.default_rng(2)
    rows = observe(spec, grid_points(spec, rng.random((60, 2))), lambda p: {"m": float(p["Y"])},
                   status_of=lambda p: "failed:spectre" if float(p["X"]) > 0.5 else None)
    _models, value = MetricGpSuggester().fit(spec, Coords(spec), list(rows), 0)
    x = np.array([[0.1, 0.5], [0.9, 0.5], [0.5, 0.5], [0.92, 0.52]])
    gives = select.gives_a_value(value, x, 2000, np.random.default_rng(0))
    assert gives.shape == (2000, 4)
    share = gives.mean(axis=0)
    assert share[0] > 0.85 and share[1] < 0.15 and 0.35 < share[2] < 0.65, share
    assert (gives[:, 1] == gives[:, 3]).mean() > 0.97    # coherent: two neighbours in the failing half are in or out together
    mean, cov = value.latent(x)
    assert np.allclose(_probability(mean, np.diag(cov)), value.probability(x), atol=1e-9)
    never_failed = models.fit_value_model(np.zeros((3, 2)), np.array([True] * 3), rng)
    assert never_failed.latent(x) is None and select.gives_a_value(never_failed, x, 50, rng).all()
    always_failed = models.fit_value_model(np.zeros((3, 2)), np.array([False] * 3), rng)
    assert 0.3 < select.gives_a_value(always_failed, x, 500, rng).mean() < 0.7


def _probability(mean: np.ndarray, variance: np.ndarray) -> np.ndarray:
    """The logistic link integrated over a normal latent, by the five-term approximation sklearn uses (Williams and
    Barber, 1998): what ``predict_proba`` returns for the latent's mean and variance."""
    from scipy.special import erf

    lambdas = np.array([0.41, 0.4, 0.37, 0.44, 0.39])[:, None]
    coefs = np.array([-1854.8214151, 3516.89893646, 221.29346712, 128.12323805, -2010.49422654])[:, None]
    alpha = 1 / (2 * variance)
    integrals = (np.sqrt(np.pi / alpha) * erf(lambdas * mean * np.sqrt(alpha / (alpha + lambdas**2)))
                 / (2 * np.sqrt(variance * 2 * np.pi)))
    return (coefs * integrals).sum(axis=0) + 0.5 * coefs.sum()


def test_until_a_point_is_scored_the_design_goes_on():
    """Every point with A > 0.05 fails to simulate (3 of A's 51 levels do not). The design of 8 holds no scored point
    (this seed: the first is the 14th of the sequence); it is followed by more of the same sequence, not by a search
    around a failing point. From the first scored point on the models propose. Without this a run whose design fails throughout
    never leaves the failing region."""
    spec = region_spec()

    def fails(p):
        return "failed:spectre" if float(p["A"]) > 0.05 else None

    history = Observations()
    while not any(o.status in region.SCORED for o in history):
        points = suggest(spec, history, 4, strategy="metric_gp", seed=3, initial_trials=8)
        assert [p.origin for p in points] == ["suggest:metric_gp:init"] * 4
        history.extend(observe(spec, points, region_metrics, len(history), status_of=fails))
    assert len(history) == 16 and [o.status in region.SCORED for o in history].index(True) == 13
    sequence = MetricGpSuggester(initial_trials=16).propose(spec, Observations(), 16, seed=3)
    assert [space.snap(spec, r) for r in sequence.raw] == [o.params for o in history]        # one sequence, continued
    after = suggest(spec, history, 4, strategy="metric_gp", seed=3, initial_trials=8)
    assert all(re.match(r"suggest:metric_gp:(tr|wide):0:16$", p.origin) for p in after)


# -- 6 / 7. the choice of a slot -------------------------------------------------------------------------------------------


def ten_by_ten(constraints=(), objective=None, metrics=("f",)):
    return spec_of([integer("X", 0, 9), integer("Y", 0, 9)], list(metrics), constraints, objective)


def test_the_first_slot_finds_a_known_optimum_on_a_small_grid():
    spec = ten_by_ten(objective={"direction": "minimize", "expression": "f"})
    grid = [(x, y) for x in range(10) for y in range(10) if (x, y) != (6, 3)]
    chosen = np.random.default_rng(123).choice(len(grid), 30, replace=False)
    rows = observe(spec, grid_points(spec, [grid[i] for i in chosen]), lambda p: {"f": ((int(p["X"]) - 6) / 4) ** 2 + ((int(p["Y"]) - 3) / 4) ** 2})
    hits = 0
    for seed in range(20):
        proposal = MetricGpSuggester(initial_trials=1).propose(spec, rows, 1, seed=seed)
        x, y = proposal.raw[0]
        hits += max(abs(x - 6), abs(y - 3)) <= 1
    assert hits >= 15, hits


def test_with_nothing_feasible_the_choice_reduces_the_true_violation():
    spec = ten_by_ten([{"metric": "g", "op": "lt", "value": "0.5"}], {"direction": "minimize", "expression": "f"}, ("f", "g"))

    def metrics_of(p):
        x, y = int(p["X"]), int(p["Y"])
        return {"f": float(x + y), "g": math.hypot(x - 8, y - 8)}

    grid = [(x, y) for x in range(10) for y in range(10) if (x, y) != (8, 8)]
    chosen = np.random.default_rng(7).choice(len(grid), 30, replace=False)
    rows = observe(spec, grid_points(spec, [grid[i] for i in chosen]), metrics_of)
    assert not any(o.feasible for o in rows)
    composer, scales = Composer(spec), metric_scales(spec, rows)
    taken = rows.keys()
    candidates_ = [p for p in grid_points(spec, itertools.product(range(10), range(10))) if p.key not in taken]
    violation = {p.key: float(composer.violation({k: np.array([v]) for k, v in metrics_of(p.params).items()}, scales)[0])
                 for p in candidates_}
    median = float(np.median(list(violation.values())))
    better = 0
    for seed in range(20):
        raw = MetricGpSuggester(initial_trials=1).propose(spec, rows, 1, seed=seed).raw[0]
        better += violation[space.point_key(space.snap(spec, raw))] < median
    assert better >= 15, better


# -- 8. batch spacing ------------------------------------------------------------------------------------------------------


def bowl_spec():
    return spec_of([stepped("X", 0, 1, 0.025), stepped("Y", 0, 1, 0.025)], ["f"], objective={"direction": "minimize", "expression": "f"})


def bowl(p):
    return {"f": (float(p["X"]) - 0.3) ** 2 + (float(p["Y"]) - 0.6) ** 2}


def test_a_batch_has_ten_distinct_points_none_evaluated_before():
    spec = bowl_spec()
    rows = observe(spec, grid_points(spec, np.random.default_rng(3).random((20, 2))), bowl)
    proposal = MetricGpSuggester(initial_trials=1).propose(spec, rows, 10, seed=0)
    keys_ = {space.point_key(space.snap(spec, r)) for r in proposal.raw}
    assert len(keys_) == 10 and not keys_ & rows.keys()


def test_a_batch_is_as_spread_as_the_models_are_unsure():
    """The slots draw independent samples: where the models know little the samples' minima lie far apart, where they
    know much they agree. On the bowl, the mean pairwise distance of a batch of 10 over seeds 0-9: 0.16 after 6
    observations, 0.05 after 20 (two grid steps: ten neighbours of the predicted minimum). Nothing else spreads a batch:
    conditioning a slot on the earlier picks (the first version) narrowed it (0.08 after 6 observations), and a rule
    that kept a batch's points apart by the models' length scales made no difference on the benchmark beyond what the
    seeds differ (T17 plan, section 7)."""
    spec = bowl_spec()                                               # 41 x 41 = 1681 points: the whole grid is the candidate set
    spread = {}
    for observations in (6, 20):
        rows = observe(spec, grid_points(spec, np.random.default_rng(3).random((observations, 2))), bowl)
        found = []
        for seed in range(10):
            proposal = MetricGpSuggester(initial_trials=1).propose(spec, rows, 10, seed=seed)
            found.append(_spread(np.array(proposal.raw)))
        spread[observations] = float(np.mean(found))
    assert spread[6] > 2 * spread[20] and spread[20] > 0.025, spread


def _spread(points: np.ndarray) -> float:
    return float(np.mean([np.linalg.norm(a - b) for a, b in itertools.combinations(points, 2)]))


def test_while_nothing_is_feasible_the_violation_decides_and_the_objective_does_not():
    """Five candidates; the sample of g (constraint g < 0) and of the objective f. Nothing feasible observed: the
    candidate deepest inside the constraint wins although its objective is the worst; with something feasible observed,
    the best objective among the candidates the sample calls feasible."""
    spec = ten_by_ten([{"metric": "g", "op": "lt", "value": "0"}], {"direction": "minimize", "expression": "f"}, ("f", "g"))
    composer = Composer(spec)
    arrays = {"g": np.array([0.5, -0.1, -2.0, 0.2, -0.3]), "f": np.array([0.0, 1.0, 9.0, -5.0, 2.0])}
    everything = np.ones(5, dtype=bool)
    args = (composer, {"f": 1.0, "g": 1.0}, arrays, everything, np.ones(5), everything)
    assert select.choose(*args, nothing_feasible=True) == 2
    assert select.choose(*args, nothing_feasible=False) == 1
    outside = {"g": np.array([0.5, 0.1, 2.0, 0.2, 0.3]), "f": arrays["f"]}                  # the sample calls none feasible
    for phase in (True, False):
        assert select.choose(composer, {"f": 1.0, "g": 1.0}, outside, everything, np.ones(5), everything, phase) == 1
    unscored = np.zeros(5, dtype=bool)
    assert select.choose(composer, {"f": 1.0, "g": 1.0}, arrays, unscored, np.array([.1, .2, .9, .3, .4]), everything, True) == 2


# -- 9 / 10. region replay and tags ----------------------------------------------------------------------------------------


def region_spec():
    """Four variables of 51 levels (6.8e6 grid points): a search region. f is smooth, the constraint g binds."""
    return spec_of([stepped(n, 0, 1, 0.02) for n in "ABCD"], ["f", "g"], [{"metric": "g", "op": "lt", "value": "0.2"}],
                   {"direction": "minimize", "expression": "f"})


def region_metrics(p):
    u = np.array([float(p[n]) for n in "ABCD"])
    return {"f": float(np.sum((u - 0.7) ** 2) + 0.3 * np.sin(5 * u[0])), "g": float(np.abs(u[1] - u[2]))}


def staged(plateau):
    """f falls by one with every evaluation up to the ``plateau``-th, then stays (g always holds): the batches of a run
    succeed, then fail, whatever the models do, so the region's course is known in advance."""
    count = itertools.count(1)
    return lambda _p: {"f": -float(min(next(count), plateau)), "g": 0.0}


START = [{n: "0.5" for n in "ABCD"}]


def test_the_region_replayed_from_the_whole_history_is_the_one_each_step_was_in(monkeypatch):
    """Batches of 7 after a start point and a design of 8 (the first batch is start + 6 design, the next 1 design + 6
    model, k = 7); f improves through evaluation 28. Three successes double the side (k = 21), then two failures in a row
    halve it (fail_tol = max(2, ceil(4 / 7)) = 2) until it falls below LENGTH_MIN (raised to 0.3, so that the region
    ends within the run) after k = 63; region 1 starts at an anchor (k = 70), whose first batch sets its baseline.
    The replay of every earlier history is a prefix of the replay of the whole, and a fresh suggester proposes what the
    uninterrupted run proposed."""
    monkeypatch.setattr(region, "LENGTH_MIN", 0.3)
    spec = region_spec()
    suggester = MetricGpSuggester()
    steps = []
    history = run(spec, 91, 7, staged(28), seed=3, start=START,
                  record=lambda h: steps.append(suggester.region_state(spec, h)))
    whole = MetricGpSuggester().region_state(spec, history[:84])
    assert whole.trace == [(7, 0, 0.8, 1, 0), (14, 0, 0.8, 2, 0), (21, 0, 1.6, 0, 0), (28, 0, 1.6, 0, 1), (35, 0, 0.8, 0, 0),
                           (42, 0, 0.8, 0, 1), (49, 0, 0.4, 0, 0), (56, 0, 0.4, 0, 1), (63, 0, 0.2, 0, 0),
                           (70, 1, 0.8, 0, 0), (77, 1, 0.8, 0, 1)]
    assert whole.centres == [27] and whole.anchor == 70 and not whole.ended        # region 0 ended at its first f = -28
    assert [o.origin for o in history[63:71]] == (["suggest:metric_gp:wide:0:63"] + ["suggest:metric_gp:tr:0:63"] * 6
                                                  + ["suggest:metric_gp:anchor:1:70"])      # round(0.2 x 7) = 1 wide slot first
    for state in steps[1:]:
        assert state.trace == whole.trace[: len(state.trace)]
    assert steps[-1].state() == whole.state()                   # the last incremental step's region is the replayed one
    again = suggest(spec, history[:84], 7, strategy="metric_gp", seed=3, start=START)
    assert [(p.params, p.origin) for p in again] == [(o.params, o.origin) for o in history[84:]]


def test_every_point_carries_its_tag_with_the_history_size_of_its_batch(monkeypatch):
    """Batches of 10 after a start point, a design of 8: the first batch is start + 9 points of the design's sequence
    (nothing is scored yet: there is nothing to model). LENGTH_MIN raised to 0.5: the region ends after k = 40 and the
    next batch starts with the anchor of region 1."""
    monkeypatch.setattr(region, "LENGTH_MIN", 0.5)
    history = run(region_spec(), 80, 10, staged(30), seed=1, start=START)
    pattern = re.compile(r"^suggest:metric_gp:(?:(?P<kind>tr|wide|anchor):(?P<r>\d+):(?P<k>\d+)|init)$")
    assert history[0].origin == "start" and [o.origin for o in history[1:10]] == ["suggest:metric_gp:init"] * 9
    for index, obs in enumerate(history[10:], 10):
        match = pattern.match(obs.origin)
        assert match and int(match.group("k")) == 10 * (index // 10), (index, obs.origin)
        assert int(match.group("r")) == (1 if index >= 50 else 0)    # k = 10, 20 improve; k = 30, 40 do not: the side halves
    assert history[50].origin == "suggest:metric_gp:anchor:1:50" and sum(":anchor:" in o.origin for o in history) == 1
    assert sum(":wide:" in o.origin for o in history[10:20]) == 2               # round(0.2 x 10) slots over the whole space
    small = run(ten_by_ten(objective={"direction": "minimize", "expression": "f"}), 30, 10,
                lambda p: {"f": float(p["X"]) + float(p["Y"])}, initial_trials=10)
    assert [o.origin for o in small[10:]] == ["suggest:metric_gp:grid:10"] * 10 + ["suggest:metric_gp:grid:20"] * 10


# -- 11. refusals ----------------------------------------------------------------------------------------------------------


def project(tmp_path, spec):
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims", lambda params, tb, corner: {"NF": 1.0 + int(params["F"]) / 100})
    return store, ex, Deck(templates={("tb", None): "parameters F={{F}} W={{W}}\n"})


def test_refusals_before_anything_runs_and_under_plan(tmp_path):
    """EM devices are refused before anything runs; several corners are not since T17.9 (``test_multi_corner.py``). A
    history whose points were evaluated at different sets of corners is refused when the strategy is called directly."""
    devices = minimal_spec()
    devices["devices"] = [{"id": "d", "generator": "demo", "profile": "demo_6m", "ports": ["P1", "N1"], "fixed": {"turns": 1}}]
    devices["variables"] = devices["variables"] + [{"name": "d.od", "kind": "integer", "lower": "20", "upper": "60", "step": "10"}]
    for index, plan in enumerate((False, True)):
        store, ex, deck = project(tmp_path / str(index), Spec.model_validate(devices))
        token = PLAN_MODE.set(plan)
        try:
            with pytest.raises(ValueError, match=re.escape(DEVICES_REFUSAL)):
                optimize(Spec.model_validate(devices), ex, store, deck=deck, strategy="metric_gp", budget=4, limits=FAKE_HOST)
        finally:
            PLAN_MODE.reset(token)
        assert ex.commands == [] and store.observations() == []
    cornered = make_spec(corners=[{"id": "tt"}, {"id": "ss"}])
    store, ex, deck = project(tmp_path / "one", cornered)
    obs = optimize(cornered, ex, store, deck=deck, strategy="metric_gp", budget=4, batch=4, corners=["tt"], current=False,
                   limits=FAKE_HOST)
    assert len(obs) == 4 and {c.corner for o in obs for c in o.children.values()} == {"tt"}
    both = [o.model_copy(update={"children": {"tb/tt": ChildResult(unit="tb", corner="tt", status="ok"),
                                              "tb/ss": ChildResult(unit="tb", corner="ss", status="ok")}}) for o in obs]
    assert len(MetricGpSuggester().propose(cornered, Observations(both), 2, seed=0).raw) == 2     # one set of corners
    with pytest.raises(ValueError, match=re.escape("this history holds several (tt: 4; ss, tt: 2): opt.optimize")):
        MetricGpSuggester().propose(cornered, Observations([*obs, *both[:2]]), 2, seed=0)
    with pytest.raises(ValueError, match=re.escape(DEVICES_REFUSAL)):
        MetricGpSuggester().propose(Spec.model_validate(devices), Observations(), 2, seed=0)


def test_a_store_that_also_holds_the_problem_at_all_corners_is_searched_at_the_run_s_corner(tmp_path, monkeypatch):
    """The signoff recipe searches at one corner and re-checks the best points at all: its store holds both. A search
    that goes on afterwards is not refused, and the models see the rows of its own corner only."""
    cornered = make_spec(corners=[{"id": "tt"}, {"id": "ss"}], budget={"max_simulations": 100})
    store, ex, deck = project(tmp_path, cornered)
    optimize(cornered, ex, store, deck=deck, strategy="metric_gp", budget=8, batch=4, corners=["tt"], current=False,
             step="search", limits=FAKE_HOST)
    optimize(cornered, ex, store, deck=deck, strategy="random", budget=3, batch=3, corners="all", current=False,
             step="signoff", limits=FAKE_HOST)
    seen = []
    propose = MetricGpSuggester.propose
    monkeypatch.setattr(MetricGpSuggester, "propose", lambda self, spec, history, n, **kw: seen.append(list(history))
                        or propose(self, spec, history, n, **kw))
    more = optimize(cornered, ex, store, deck=deck, strategy="metric_gp", budget=12, batch=4, corners=["tt"], current=False,
                    step="search", limits=FAKE_HOST)
    assert len(more) == 12 and len(store.observations()) == 15
    assert [len(h) for h in seen] == [8] and all({c.corner for c in o.children.values()} == {"tt"} for o in seen[0])


def test_the_default_design_is_at_most_half_of_a_small_run():
    spec = make_spec(variables=[integer("F", 20, 60, 2), stepped("W", "0.6u", "3u", "0.2u")])
    assert mg.initial_design_size(spec) == 8 and mg.initial_design_size(spec, budget=12) == 6
    assert mg.initial_design_size(spec, budget=200) == 8 and mg.initial_design_size(spec, 5, budget=6) == 5


def test_optimize_prints_the_design_line_and_a_continued_run_is_an_uninterrupted_one(tmp_path, capsys):
    spec = make_spec(variables=[integer("F", 20, 60, 2), stepped("W", "0.6u", "3u", "0.2u")], budget={"max_simulations": 100})
    store, ex, deck = project(tmp_path / "whole", spec)
    whole = optimize(spec, ex, store, deck=deck, strategy="metric_gp", budget=12, batch=4, seed=2, initial_trials=8,
                     limits=FAKE_HOST)
    assert "[optimize] metric_gp initial design 8 points: the model proposes 4 of the 12 new points" in capsys.readouterr().out
    store, ex, deck = project(tmp_path / "parts", spec)             # the design's size stated: the default follows the budget
    optimize(spec, ex, store, deck=deck, strategy="metric_gp", budget=8, batch=4, seed=2, initial_trials=8, limits=FAKE_HOST)
    parts = optimize(spec, ex, store, deck=deck, strategy="metric_gp", budget=12, batch=4, seed=2, initial_trials=8,
                     limits=FAKE_HOST)
    assert "the model proposes 4 of the 4 new points" in capsys.readouterr().out
    assert [(o.params, o.origin) for o in parts] == [(o.params, o.origin) for o in whole]
    assert [o.origin for o in whole] == ["suggest:metric_gp:init"] * 8 + ["suggest:metric_gp:grid:8"] * 4
    store, ex, deck = project(tmp_path / "plan", spec)
    token = PLAN_MODE.set(True)
    try:
        optimize(spec, ex, store, deck=deck, strategy="metric_gp", budget=4, batch=4, initial_trials=6, current=False,
                 limits=FAKE_HOST)
    finally:
        PLAN_MODE.reset(token)
    assert ("[plan] metric_gp initial design 6 points: the model proposes 0 of the 4 new points -- WARNING: none; this run is "
            "initial design throughout (the model needs 1 successful point before a batch starts). Raise budget, use a "
            "smaller batch, or pass a smaller initial_trials") in capsys.readouterr().out
    token = PLAN_MODE.set(True)
    try:
        optimize(spec, ex, store, deck=deck, strategy="metric_gp", budget=12, batch=4, current=False, limits=FAKE_HOST)
    finally:
        PLAN_MODE.reset(token)
    assert "[plan] metric_gp initial design 6 points: the model proposes 6 of the 12 new points" in capsys.readouterr().out


# -- 12. determinism --------------------------------------------------------------------------------------------------------


def test_the_same_history_and_seed_give_the_same_points_and_another_seed_others():
    for spec, metrics_of in ((bowl_spec(), bowl), (region_spec(), region_metrics)):
        rows = observe(spec, grid_points(spec, np.random.default_rng(9).random((12, len(spec.variables)))), metrics_of)
        first = MetricGpSuggester(initial_trials=4).propose(spec, rows, 6, seed=11)
        again = MetricGpSuggester(initial_trials=4).propose(spec, Observations(list(rows)), 6, seed=11)
        other = MetricGpSuggester(initial_trials=4).propose(spec, rows, 6, seed=12)
        assert (first.raw, first.tags) == (again.raw, again.tags) and first.raw != other.raw


def tt_spec() -> Spec:
    """Testbench tb gives NF, g gives G, at corners tt and ss: NF < 9 and G > 1, minimize NF - G; 100 x 100 grid points,
    so that the search region is replayed."""
    benches = [{"id": tb, "maestro_point_root": f"/x/{tb}", "virtuoso_library": "l", "cell": "c", "test_name": "t"}
               for tb in ("tb", "g")]
    return make_spec(testbenches=benches, corners=[{"id": "tt"}, {"id": "ss"}],
                     variables=[integer("F", 1, 100), stepped("W", "0.1u", "10u", "0.1u")],
                     metrics=[{"name": "NF", "unit": "dB", "expression": "nf()", "testbench": "tb"},
                              {"name": "G", "unit": "dB", "expression": "g()", "testbench": "g"}],
                     constraints=[{"metric": "NF", "op": "lt", "value": "9"}, {"metric": "G", "op": "gt", "value": "1"}],
                     objective={"direction": "minimize", "expression": "NF - G"})


def tt_history(spec: Spec) -> Observations:
    """16 points searched at tt, recorded as the engine records them (``aggregate``): an initial design of 8, then two
    batches of the region. Point 3 was stopped after tb (its NF fails), point 6's g failed to simulate, point 10's g lost
    G."""
    wanted = ["tb/tt", "g/tt"]
    raw = [(1 + 99 * a, 0.1 + 9.9 * b) for a, b in np.random.default_rng(5).random((16, 2))]
    rows = Observations()
    for i, point in enumerate(grid_points(spec, raw)):
        f, w = int(point.params["F"]), float(point.params["W"].rstrip("u"))
        nf, g = 5.0 + ((f - 60) / 20) ** 2 + (w - 5.0) ** 2 / 4, 3.0 - ((f - 40) / 30) ** 2
        children = {"tb/tt": ChildResult(unit="tb", corner="tt", status="ok", metrics={"NF": 9.5 if i == 3 else nf})}
        if i == 6:
            children["g/tt"] = ChildResult(unit="g", corner="tt", status="failed:spectre", issues=["spectre exited 1"])
        elif i == 10:
            children["g/tt"] = ChildResult(unit="g", corner="tt", status="metric_failed", issues=["metric G failed: nil"])
        elif i != 3:
            children["g/tt"] = ChildResult(unit="g", corner="tt", status="ok", metrics={"G": g})
        agg = aggregate(spec, children, wanted)
        origin = "suggest:metric_gp:init" if i < 8 else f"suggest:metric_gp:tr:0:{8 if i < 12 else 12}"
        rows.append(Observation(obs_id=f"obs_{i:04d}", params=point.params, origin=origin, children=children,
                                not_run=[key for key in wanted if key not in children], metrics=agg.metrics, fom=agg.fom,
                                objective=agg.objective, feasible=agg.feasible, constraint_penalty=agg.constraint_penalty,
                                status=agg.status, issues=agg.issues, spec_fingerprint=spec.fingerprint(),
                                pipeline_fingerprint="p", started_at="t", finished_at="t"))
    return rows


# What metric_gp proposed on ``tt_history`` (initial_trials 8, 5 points, seed 7) before T17.9 handed it several corners.
TT_PROPOSAL = ([[62.0, 5.2], [32.0, 2.4], [27.0, 1.6], [24.0, 0.2], [48.0, 3.8]],
               ["wide:0:16", "tr:0:16", "tr:0:16", "tr:0:16", "tr:0:16"])


def test_a_single_condition_history_gets_the_proposal_it_got_before_several_corners_were_taken():
    """T17.9, section 6, item 5: at one corner every row's metrics reach the models as they are, byte for byte -- a
    stopped point, one whose g failed and one whose g lost G among them."""
    spec = tt_spec()
    rows = tt_history(spec)
    assert [rows[i].status for i in (3, 6, 10)] == ["constraint_failed", "failed:spectre", "metric_failed"]
    assert rows[3].not_run == ["g/tt"] and rows[6].metrics == {} and rows[10].metrics == {"NF": rows[10].metrics["NF"]}
    proposal = MetricGpSuggester(initial_trials=8).propose(spec, rows, 5, seed=7)
    assert (proposal.raw, proposal.tags) == TT_PROPOSAL


# -- 13. no penalty ---------------------------------------------------------------------------------------------------------


def test_no_penalty_reaches_a_model(monkeypatch):
    """The strategy never reads ``failure_penalty`` or ``constraint_penalty``, and every value a metric's model is given is
    one of that metric's observed values: nothing invented, nothing above the largest observed."""
    names = set()
    for path in PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names |= {node.attr} if isinstance(node, ast.Attribute) else {node.id} if isinstance(node, ast.Name) else set()
            names |= {k.arg for k in node.keywords if k.arg} if isinstance(node, ast.Call) else set()
    assert not {n for n in names if "penalty" in n}
    spec = region_spec()
    rng = np.random.default_rng(4)
    points = grid_points(spec, rng.random((30, 4)))
    rows = observe(spec, points, region_metrics, status_of=lambda p: "failed:spectre" if float(p["A"]) > 0.8 else None)
    rows[3] = rows[3].model_copy(update={"status": "metric_failed", "metrics": {"f": rows[3].metrics["f"]} if rows[3].metrics else {}})
    assert {o.status for o in rows} >= {"ok", "constraint_failed", "failed:spectre", "metric_failed"}
    given = []
    fit = models.fit_metric
    monkeypatch.setattr(mg, "fit_metric", lambda name, x, y, rng: given.append((name, y.copy())) or fit(name, x, y, rng))
    plain = MetricGpSuggester(initial_trials=4).propose(spec, rows, 5, seed=0)
    for name, y in given:
        observed = [o.metrics[name] for o in rows if name in o.metrics]
        assert sorted(y.tolist()) == sorted(observed) and y.max() <= max(observed)
    assert suggest(spec, rows, 5, strategy="metric_gp", seed=0, initial_trials=4, failure_penalty=1e6) == \
        suggest(spec, rows, 5, strategy="metric_gp", seed=0, initial_trials=4)
    assert make("metric_gp", failure_penalty=1e6, initial_trials=4).propose(spec, rows, 5, seed=0).raw == plain.raw


def test_candidates_stay_within_the_limits():
    spec = region_spec()
    coords = Coords(spec)
    rng = np.random.default_rng(0)
    centre = coords.snap(np.full((1, 4), 0.5))[0]
    excluded = set(keys(centre[None, :]))                          # the centre is an evaluated point
    local = candidates.local(coords, centre, 0.8, np.ones(4), excluded, rng)
    wide = candidates.wide(coords, excluded, rng)
    assert len(local) <= candidates.LOCAL and len(wide) <= candidates.WIDE and len(local) + len(wide) <= candidates.MAX_CANDIDATES
    assert not set(keys(local)) & set(keys(wide)) and len(set(keys(local))) == len(local)
    assert np.all(np.abs(coords.unit(local) - coords.unit(centre[None, :])) <= 0.4 + 0.01 + 1e-12)   # side 0.8, snapped (step 0.02)
    moved = (local != centre).sum(axis=1)                          # four fine variables, a wide box: one to four of them move
    assert moved.min() >= 1 and all(0.1 < np.mean(moved == k) < 0.5 for k in (1, 2, 3, 4)), np.bincount(moved)
    tiny = candidates.local(coords, centre, 1e-6, np.ones(4), set(), rng)            # the centre's level and its neighbours
    assert len(tiny) == 3**4 and np.abs(tiny - centre).max() == 1
    assert len(candidates.local(coords, centre, 0.8, np.ones(4), set(keys(tiny)), rng)) <= candidates.LOCAL


def test_a_variable_the_region_does_not_reach_is_moved_one_level_at_a_time():
    """Three variables of 4 levels and seven of 31 (a region: 4^3 x 31^7 points). With a side of 0.2 the box covers a
    tenth of the axis to each side: less than half the way to a coarse variable's next level, more than that for a fine
    one. The perturbations move the fine variables inside the box and a coarse one by a level now and then (about one of
    the three per candidate); each coarse variable's one-step moves come first, the rest at the centre. (Redrawing every
    variable among its levels, as the first version did, left no candidate at the centre's coarse levels: on the
    benchmark's 10-variable Ackley problem the candidates then held no point better than the centre.)"""
    spec = spec_of([integer(f"C{i}", 0, 3) for i in range(3)] + [stepped(f"F{i}", 0, 3, 0.1) for i in range(7)], ["f"],
                   objective={"direction": "minimize", "expression": "f"})
    coords = Coords(spec)
    centre = np.array([1, 2, 1] + [15] * 7)
    found = candidates.local(coords, centre, 0.2, np.ones(10), set(), np.random.default_rng(0))
    assert len(found) > 1000
    steps, perturbed = found[:6], found[6:]
    assert (np.abs(steps - centre).sum(axis=1) == 1).all() and (steps[:, 3:] == centre[3:]).all()
    assert np.abs(perturbed[:, :3] - centre[:3]).max() == 1 and np.abs(perturbed[:, 3:] - centre[3:]).max() <= 3
    kept = (perturbed[:, :3] == centre[:3]).all(axis=1).mean()
    assert 0.2 < kept < 0.4, kept                                                      # (1 - 1/3)^3 = 0.30
    assert 0.8 < (perturbed[:, :3] != centre[:3]).sum(axis=1).mean() < 1.2             # about one coarse move per candidate


def test_a_region_smaller_than_the_grid_searches_at_the_grid_s_resolution():
    """Ten variables of 31 levels, a side of 0.01 (the grid step is 0.033): no variable is reached, so a candidate moves
    about one of them by one level -- never more than a level, never all of them."""
    spec = spec_of([stepped(f"F{i}", 0, 3, 0.1) for i in range(10)], ["f"], objective={"direction": "minimize", "expression": "f"})
    coords = Coords(spec)
    centre = np.full(10, 15)
    found = candidates.local(coords, centre, 0.01, np.ones(10), {centre.tobytes()}, np.random.default_rng(0))
    moves = np.abs(found - centre)
    assert moves.max() == 1 and len(found) > 200                 # 1500 draws, few distinct: one or two moves each
    assert (moves.sum(axis=1) == 1).sum() == 20                  # every one-step move, each once
    assert np.median(moves.sum(axis=1)) <= 3 and moves.sum(axis=1).max() <= 6      # the distinct ones: a few variables each
    edge = candidates.local(coords, np.zeros(10, dtype=int), 0.01, np.ones(10), set(), np.random.default_rng(0))
    assert edge.min() == 0 and edge.max() == 1                                         # at the lower end a move goes up
