"""External evaluators use the same point proposals and corner scores as an ic-opt project."""

import pytest

from ic_opt.blocks.optimize import suggest
from ic_opt.observation import Observations
from ic_opt.optimizer import Problem, Result, Trial, ask, tell
from tests.ic_opt.test_metric_gp import observe, region_metrics, region_spec


def problem(**fields):
    return Problem(variables=[{"name": "x", "kind": "integer", "lower": 0, "upper": 40, "step": 1}],
                   metrics=["gain", "power"], objective={"direction": "maximize", "expression": "gain"},
                   constraints=[{"metric": "power", "op": "le", "value": 10}], **fields)


def test_external_batches_match_native_design_and_model_after_out_of_order_measurements():
    native = region_spec()
    external = Problem(variables=native.variables, metrics=["f", "g"], constraints=native.constraints,
                       objective=native.objective)
    rows, history = Observations(), []
    kinds = set()
    for _ in range(4):
        points = suggest(native, rows, 5, strategy="metric_gp", initial_trials=5, seed=4)
        trials = ask(external, history, 5, initial_trials=5, seed=4)
        assert [(t.params, t.origin) for t in trials] == [(p.params, p.origin) for p in points]
        kinds.update(t.origin.split(":")[2] for t in trials)
        rows.extend(observe(native, points, region_metrics, first=len(rows)))
        history.extend(tell(external, t, region_metrics(t.params)) for t in reversed(trials))
    assert {"init", "wide", "tr"} <= kinds
    assert {r.key: (r.fom, r.feasible) for r in rows} == {r.key: (r.fom, r.feasible) for r in history}


def test_external_parallel_asks_can_exclude_inflight_trials():
    p = problem()
    first = ask(p, [], 4, seed=8)
    pending = [Trial.model_validate_json(t.model_dump_json()) for t in first]
    second = ask(p, [], 3, pending=pending, seed=8)
    assert {t.obs_id for t in first}.isdisjoint(t.obs_id for t in second)
    assert {t.params["x"] for t in first}.isdisjoint(t.params["x"] for t in second)
    done = tell(p, second[1], {"gain": 12, "power": 8})
    with pytest.raises(ValueError, match="repeated trial ID"):
        ask(p, [done], 2, pending=second)
    with pytest.raises(ValueError, match="different problem or evaluator"):
        tell(p.model_copy(update={"evaluator": "new procedure"}), first[0], {"gain": 1, "power": 2})


def test_external_pvt_scores_nominal_objective_and_every_corner_constraint():
    p = problem(corners=["nominal", "ss"], corner_policy={"objective": "nominal", "constraints": "all_corners"})
    t = ask(p, [])[0]
    tt, ss = {"gain": 15, "power": 9}, {"gain": 11, "power": 8}
    complete = tell(p, t, by_corner={"ss": ss, "nominal": tt})
    assert complete.feasible and complete.fom == 15 and complete.objective == -15
    assert complete.metrics == tt and complete.children["external/ss"].metrics == ss
    failed = tell(p, t, by_corner={"nominal": tt, "ss": {**ss, "power": 12}})
    assert failed.status == "constraint_failed" and not failed.feasible
    partial = tell(p, t, by_corner={"ss": {**ss, "power": 12}})
    assert partial.status == "constraint_failed" and partial.not_run == ["external/nominal"]
    with pytest.raises(ValueError, match="none of them fails"):
        tell(p, t, by_corner={"nominal": tt})
    with pytest.raises(ValueError, match="by_corner"):
        tell(p, t, tt)


def test_external_failures_preserve_available_metrics_and_reasons():
    p = problem()
    t = ask(p, [])[0]
    missing = tell(p, t, {"gain": 7, "power": float("nan")})
    assert missing.status == "metric_failed" and missing.metrics == {"gain": 7}
    failed = tell(p, t, Result(error="solver timed out"))
    assert failed.status == "failed:external" and not failed.feasible and any("timed out" in s for s in failed.issues)
    assert failed.fom is None
    with pytest.raises(ValueError, match="unknown metrics"):
        tell(p, t, {"gain": 10, "Power": 3})
