"""T17.8, step 1 (``docs/refactor/T17_8_SCHEDULE_SPEC.md``, section 11): a point's children run in an order learned from
the history, and the point stops at the first child whose result shows it cannot be feasible."""

from __future__ import annotations

import json
import math
from itertools import pairwise

import numpy as np
import pytest

from ic_opt import digest as digest_module
from ic_opt import objective
from ic_opt.blocks import analyze
from ic_opt.blocks.evaluate import evaluate, plan_shape
from ic_opt.blocks.optimize import _at_corners, history_size, optimize, suggest
from ic_opt.eval.engine import Child
from ic_opt.eval.schedule import ChildHistory, Schedule
from ic_opt.eval.stage import Resources, StageContext
from ic_opt.executor import LocalExecutor
from ic_opt.observation import ChildResult, Observation, Observations
from ic_opt.recipes import signoff
from ic_opt.sim.corner import aggregate
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.store import RunStore
from ic_opt.suggesters import resolve_auto
from ic_opt.suggesters.base import minimization_objective
from ic_opt.suggesters.metric_gp import MetricGpSuggester, region
from ic_opt.suggesters.metric_gp.compose import Composer, metric_scales, true_arrays
from ic_opt.suggesters.openbox import OpenBoxSuggester
from ic_opt.suggesters.turbo import targets
from tests.ic_opt.fakes import FAKE_HOST, minimal_spec
from tests.ic_opt.test_engine import GOLDEN, golden


def bench(tb: str) -> dict:
    return {"id": tb, "maestro_point_root": f"/x/{tb}", "virtuoso_library": "l", "cell": "c", "test_name": "t"}


def two_by_two(**overrides) -> Spec:
    """Two testbenches (tb gives NF, g gives G) at two corners (tt, the nominal one, then ss): NF < 9 and G > 1,
    minimize NF - G."""
    d = minimal_spec(testbenches=[bench("tb"), bench("g")], corners=[{"id": "tt"}, {"id": "ss"}],
                     metrics=[{"name": "NF", "unit": "dB", "expression": "nf()", "testbench": "tb"},
                              {"name": "G", "unit": "dB", "expression": "g()", "testbench": "g"}],
                     constraints=[{"metric": "NF", "op": "lt", "value": "9"}, {"metric": "G", "op": "gt", "value": "1"}],
                     objective={"direction": "minimize", "expression": "NF - G"}, budget={"max_simulations": 1000})
    d.update(overrides)
    return Spec.model_validate(d)


def three_testbenches() -> Spec:
    """Testbenches a, b, c at one condition, each giving one metric that must stay below 1."""
    return Spec.model_validate(minimal_spec(
        testbenches=[bench(tb) for tb in ("a", "b", "c")],
        metrics=[{"name": tb.upper(), "unit": "V", "expression": f"{tb}()", "testbench": tb} for tb in ("a", "b", "c")],
        constraints=[{"metric": tb.upper(), "op": "lt", "value": "1"} for tb in ("a", "b", "c")],
        objective={"direction": "minimize", "expression": "A + B + C"}))


def child(unit: str, corner: str | None = None, status: str = "ok", seconds: float | None = 1.0, issues=(), **metrics) -> ChildResult:
    return ChildResult(unit=unit, corner=corner, status=status, metrics=metrics, issues=list(issues), seconds=seconds)


def row(spec: Spec, i: int, *children: ChildResult, status: str = "ok") -> Observation:
    """A recorded point holding ``children`` (the schedule reads nothing else of it)."""
    return Observation(obs_id=f"obs_{i + 1:04d}", params={"F": "20", "W": "0.6u"}, origin="user", status=status,
                       children={f"{c.unit}/{c.corner or 'nominal'}": c for c in children}, simulations=len(children),
                       spec_fingerprint=spec.fingerprint(), pipeline_fingerprint="recorded", started_at="t", finished_at="t")


def keys(children) -> list[str]:
    return [c.key for c in children]


# -- 1. the order ---------------------------------------------------------------------------------------------------------


def test_the_order_is_learned_from_failures_and_seconds():
    spec = three_testbenches()
    children = [Child("testbench", tb, None) for tb in ("a", "b", "c")]
    history = []
    for i in range(12):
        held = [child("a", seconds=1.0 if i % 2 else 3.0, A=2.0 if i < 3 else 0.5)]
        if i < 8:
            held.append(child("b", seconds=4.0, status="failed:spectre") if i < 2 else child("b", seconds=4.0, B=5.0 if i < 6 else 0.5))
        if i < 6:
            held.append(child("c", seconds=0.5 if i % 2 else 1.5, status="metric_failed") if i == 0 else
                        child("c", seconds=0.5 if i % 2 else 1.5, C=0.5))
        history.append(row(spec, i, *held))
    # a: reached 12, failed 3 (A = 2 on the first three), mean 2 s: (3 + 1) / (12 + 2) / 2 = 0.1429
    # b: reached 8, failed 6 (two failed simulations, four B = 5), 4 s: (6 + 1) / (8 + 2) / 4 = 0.175
    # c: reached 6, failed 1 (metric_failed), mean 1 s: (1 + 1) / (6 + 2) / 1 = 0.25
    schedule = Schedule.from_history(spec, history, children, "all_corners")
    assert schedule.history_of(children[0]) == ChildHistory(reached=12, failed=3, seconds=2.0)
    assert schedule.history_of(children[1]) == ChildHistory(reached=8, failed=6, seconds=4.0)
    assert schedule.history_of(children[2]) == ChildHistory(reached=6, failed=1, seconds=1.0)
    assert [round(schedule.history_of(c).score, 4) for c in children] == [0.1429, 0.175, 0.25]
    assert keys(schedule.order(children)) == ["c/nominal", "b/nominal", "a/nominal"]
    assert keys(schedule.spec_order(children[::-1])) == ["a/nominal", "b/nominal", "c/nominal"]

    few = Schedule.from_history(spec, history[:9], children, "all_corners")      # 9 observations hold any child: too few
    assert few.histories is None and keys(few.order(children)) == ["a/nominal", "b/nominal", "c/nominal"]


def test_ties_keep_the_spec_s_order_with_the_nominal_corner_first():
    spec = three_testbenches()
    children = [Child("testbench", tb, None) for tb in ("a", "b", "c")]
    history = [row(spec, i, child("a", A=0.5), child("b", B=0.5), child("c", C=5.0)) for i in range(10)]
    # c fails on all 10: (10 + 1) / (10 + 2) / 1; a and b never fail: (0 + 1) / (10 + 2) / 1 each, a tie
    schedule = Schedule.from_history(spec, history, children, "all_corners")
    assert keys(schedule.order(children[::-1])) == ["c/nominal", "a/nominal", "b/nominal"]

    cornered = two_by_two(corners=[{"id": "ff"}, {"id": "nominal"}, {"id": "ss"}])
    grid = [Child("testbench", tb, c) for tb in ("g", "tb") for c in ("ss", "nominal", "ff")]
    spec_order = Schedule.from_history(cornered, [], grid, "all_corners").order(grid)
    assert keys(spec_order) == ["tb/nominal", "tb/ff", "tb/ss", "g/nominal", "g/ff", "g/ss"]


def test_a_child_without_recorded_seconds_takes_its_unit_s_mean():
    spec = Spec.model_validate(minimal_spec(
        testbenches=[bench(tb) for tb in ("a", "b", "c")], corners=[{"id": "tt"}, {"id": "ss"}],
        metrics=[{"name": tb.upper(), "unit": "V", "expression": f"{tb}()", "testbench": tb} for tb in ("a", "b", "c")],
        constraints=[], objective={"direction": "minimize", "expression": "A + B + C"}))
    children = [Child("testbench", tb, c) for tb in ("a", "b", "c") for c in ("tt", "ss")]
    history = [row(spec, i, child("a", "tt", seconds=1.0 if i % 2 else 3.0, A=1.0), child("a", "ss", seconds=None, A=1.0),
                   child("b", "tt", seconds=5.0, B=1.0)) for i in range(10)]
    schedule = Schedule.from_history(spec, history, children, "all_corners")
    history_of = {c.key: schedule.history_of(c) for c in children}
    assert history_of["a/tt"] == ChildHistory(10, 0, 2.0)          # its own mean
    assert history_of["a/ss"] == ChildHistory(10, 0, 2.0)          # none recorded: unit a's mean
    assert history_of["b/ss"] == ChildHistory(0, 0, 5.0)           # never reached: unit b's mean
    assert history_of["c/tt"] == history_of["c/ss"] == ChildHistory(0, 0, 1.0)   # unit c has no record: 1
    # scores: c/tt, c/ss 1/2/1 = 0.5 (a tie, the nominal corner first); b/ss 1/2/5 = 0.1; a/tt, a/ss 1/12/2 = 0.0417 (a
    # tie); b/tt 1/12/5 = 0.0167
    assert keys(schedule.order(children)) == ["c/tt", "c/ss", "b/ss", "a/tt", "a/ss", "b/tt"]


# -- 2. the stop rule -----------------------------------------------------------------------------------------------------


def test_what_stops_a_point():
    children = [Child("testbench", tb, c) for tb in ("tb", "g") for c in ("tt", "ss")]
    every = Schedule.from_history(two_by_two(), [], children, "all_corners")
    nominal = Schedule.from_history(two_by_two(corner_policy={"constraints": "nominal"}), [], children, "nominal")
    worse_at_ss = child("tb", "ss", NF=9.5)
    assert every.stop_after(worse_at_ss, "ss") == "tb/ss: NF lt 9 violated by 9.5"
    assert nominal.stop_after(worse_at_ss, "ss") is None                     # ss's constraints do not count
    assert nominal.stop_after(child("tb", "tt", NF=9.5), "tt") == "tb/tt: NF lt 9 violated by 9.5"
    assert every.stop_after(child("tb", "tt", NF=8.0), "tt") is None         # G, in the objective and a constraint: another testbench's
    for schedule in (every, nominal):
        assert schedule.stop_after(child("g", "ss", status="failed:spectre"), "ss") == "g/ss: failed:spectre"
        assert schedule.stop_after(child("tb", "ss", status="metric_failed", G=0.5), "ss") == "tb/ss: metric_failed"

    plain = Spec.model_validate(minimal_spec(corner_policy={"constraints": "nominal"}))      # no corners: the one is nominal
    alone = Schedule.from_history(plain, [], [Child("testbench", "tb", None)], "nominal")
    assert alone.stop_after(child("tb", NF=9.5), None) == "tb/nominal: NF lt 9 dB violated by 9.5"


# -- 3. the engine --------------------------------------------------------------------------------------------------------


def prepared(params: dict[str, str], unit: str, corner: str | None) -> ChildResult:
    """What each child of each point gives (F picks the point):
    F=20 violates NF < 9 at tb/tt, the first child: it stops there, 3 of 4 not run;
    F=22 is feasible;
    F=24 fails to simulate g/tt, the third child: it stops there, g/ss not run;
    F=26 violates NF < 9 at tb/ss, the second child: it stops there, 2 not run;
    F=28 violates G > 1 at g/ss, the last child: nothing is left to stop, the point is recorded whole."""
    f = params["F"]
    if (f, unit, corner) == ("24", "g", "tt"):
        return ChildResult(unit=unit, corner=corner, status="failed:spectre", issues=["spectre exited 1"])
    if unit == "tb":
        nf = {("20", "tt"): 9.5, ("20", "ss"): 9.6, ("22", "ss"): 8.5, ("24", "ss"): 8.2, ("26", "ss"): 9.5}.get((f, corner), 8.0)
        return ChildResult(unit=unit, corner=corner, status="ok", metrics={"NF": nf})
    g = {("22", "ss"): 1.5, ("28", "ss"): 0.5}.get((f, corner), 2.0)
    return ChildResult(unit=unit, corner=corner, status="ok", metrics={"G": g})


class Prepared:
    """The child stage of a fake pipeline: the result :func:`prepared` gives, and the order the children ran in."""

    name = "prepared"
    level = "child"
    unit = "testbench"
    resources = Resources()

    def __init__(self, result_of=prepared) -> None:
        self.result_of = result_of
        self.ran: list[tuple[str, str, str | None]] = []

    def fingerprint(self, inp, ctx: StageContext) -> str | None:
        return None

    def run(self, point: Point, ctx: StageContext) -> ChildResult:
        self.ran.append((point.params["F"], ctx.unit, ctx.corner))
        return self.result_of(point.params, ctx.unit, ctx.corner)


POINTS = [Point({"F": f, "W": "0.6u"}, "user") for f in ("20", "22", "24", "26", "28")]


def run_batch(project, spec: Spec, **kwargs) -> tuple[RunStore, Prepared, list[Observation]]:
    store = RunStore(project)
    stage = Prepared()
    obs = evaluate(spec, POINTS, LocalExecutor(store.root / "sims"), store, pipeline=[stage], limits=FAKE_HOST, **kwargs)
    return store, stage, obs


def last_step(store: RunStore) -> dict:
    return json.loads((store.root / "steps.jsonl").read_text(encoding="utf-8").splitlines()[-1])


def line_of(store: RunStore, obs_id: str) -> dict:
    """A point's observations.jsonl line with its clock readings taken out (the timestamps, each child's seconds)."""
    for text in store.observations_path.read_text(encoding="utf-8").splitlines():
        record = json.loads(text)
        if record["obs_id"] == obs_id:
            record["started_at"] = record["finished_at"] = "t"
            for result in record["children"].values():
                result["seconds"] = 0.0
            return record
    raise KeyError(obs_id)


def test_the_engine_stops_a_point_at_its_first_failing_child(tmp_path, capsys):
    spec = two_by_two()
    store, stage, obs = run_batch(tmp_path / "on", spec, stop_at_first_failure=True)   # 4 simulations per point: on by the override (T17.9 revision 2)
    by_f = {o.params["F"]: o for o in obs}

    first = by_f["20"]
    assert list(first.children) == ["tb/tt"] and first.status == "constraint_failed" and not first.feasible
    assert first.simulations == 1 and first.fom is None and first.objective is None and first.metrics == {"NF": 9.5}
    assert first.constraint_penalty == pytest.approx((0.5 / 9) ** 2)
    assert first.issues == ["tt: NF lt 9 violated by 9.5", "not simulated: 3 of 4 children (stopped after tb/tt)"]
    assert first.not_run == ["tb/ss", "g/tt", "g/ss"] and first.corners() == {"tt", "ss"}
    assert [c for c in stage.ran if c[0] == "20"] == [("20", "tb", "tt")]                 # nothing more ran for it

    feasible = by_f["22"]
    assert list(feasible.children) == ["tb/tt", "tb/ss", "g/tt", "g/ss"] and feasible.status == "ok" and feasible.feasible
    assert feasible.objective == pytest.approx(7.0) and feasible.simulations == 4 and feasible.issues == []
    assert feasible.not_run == [] and "not_run" not in line_of(store, feasible.obs_id)
    assert line_of(store, first.obs_id)["not_run"] == ["tb/ss", "g/tt", "g/ss"]

    failed = by_f["24"]
    assert list(failed.children) == ["tb/tt", "tb/ss", "g/tt"] and failed.status == "failed:spectre"
    assert failed.issues == ["g/tt: spectre exited 1", "not simulated: 1 of 4 children (stopped after g/tt)"]
    assert failed.simulations == 3 and failed.not_run == ["g/ss"]

    at_ss = by_f["26"]
    assert list(at_ss.children) == ["tb/tt", "tb/ss"] and at_ss.status == "constraint_failed" and at_ss.metrics == {"NF": 9.5}
    assert at_ss.issues == ["ss: NF lt 9 violated by 9.5", "not simulated: 2 of 4 children (stopped after tb/ss)"]
    assert at_ss.not_run == ["g/tt", "g/ss"]

    last = by_f["28"]                                               # its failing child was its last: recorded whole
    assert len(last.children) == 4 and last.status == "constraint_failed" and last.fom == pytest.approx(7.5)
    assert last.not_run == [] and last.issues == ["ss: G gt 1 violated by 0.5"]

    step = last_step(store)
    assert (step["stopped"], step["not_run"], step["simulations"], step["new"]) == (3, 6, 14, 5)
    assert "[evaluate] step='evaluate': 3 of 5 points stopped early, 6 simulations not run" in capsys.readouterr().out

    # the switch off -- in the spec, or for one call -- runs every child of every point
    off_store, off_stage, off = run_batch(tmp_path / "off", two_by_two(simulator={**minimal_spec()["simulator"],
                                                                                   "stop_at_first_failure": False}))
    assert all(len(o.children) == 4 for o in off) and len(off_stage.ran) == 20
    assert {o.params["F"]: o.status for o in off} == {"20": "constraint_failed", "22": "ok", "24": "failed:spectre",
                                                      "26": "constraint_failed", "28": "constraint_failed"}
    assert (last_step(off_store)["stopped"], last_step(off_store)["not_run"], last_step(off_store)["simulations"]) == (0, 0, 20)
    _, _, called_off = run_batch(tmp_path / "call", spec, stop_at_first_failure=False)
    assert all(len(o.children) == 4 for o in called_off)
    assert "stopped early" not in capsys.readouterr().out
    # a point that was not stopped is recorded as with the switch off, byte for byte but for the clock
    for obs_id in ("obs_0002", "obs_0005"):
        assert json.dumps(line_of(store, obs_id)) == json.dumps(line_of(off_store, obs_id))


def test_the_engine_runs_the_learned_order_and_records_the_engine_s_order(tmp_path):
    spec = two_by_two()
    store = RunStore(tmp_path)
    for i in range(10):          # g/ss failed on every recorded point: (10 + 1) / (10 + 2) against (0 + 1) / (10 + 2)
        store.append(row(spec, i, child("tb", "tt", NF=8.0), child("tb", "ss", NF=8.0), child("g", "tt", G=2.0),
                         child("g", "ss", G=0.5), status="constraint_failed"))
    stage = Prepared()
    ok, stopped = evaluate(spec, [Point({"F": f, "W": "0.6u"}, "user") for f in ("22", "28")], LocalExecutor(store.root / "sims"),
                           store, pipeline=[stage], limits=FAKE_HOST, stop_at_first_failure=True)
    assert [c for c in stage.ran if c[0] == "22"] == [("22", "g", "ss"), ("22", "tb", "tt"), ("22", "tb", "ss"), ("22", "g", "tt")]
    assert list(ok.children) == ["tb/tt", "tb/ss", "g/tt", "g/ss"] and ok.status == "ok" and ok.not_run == []
    # F=28 fails G > 1 at g/ss, which now runs first: the others are not run, and not_run keeps the engine's order
    assert [c for c in stage.ran if c[0] == "28"] == [("28", "g", "ss")]
    assert list(stopped.children) == ["g/ss"] and stopped.not_run == ["tb/tt", "tb/ss", "g/tt"]


def test_the_plan_says_a_point_may_stop(tmp_path):
    spec, executor = two_by_two(), LocalExecutor(tmp_path)
    assert plan_shape(spec, [Prepared()], "all", executor, None, FAKE_HOST, stop_at_first_failure=True) == (
        "(4 testbench sims) = up to 4 simulations per point (a point stops at the first simulation that fails it) on local, "
        "2 workers × (1 + 1) threads (prepared)")
    assert plan_shape(spec, [Prepared()], "all", executor, None, FAKE_HOST) == (          # 4 per point: off by default (T17.9 revision 2)
        "(4 testbench sims) = 4 simulations per point on local, 2 workers × (1 + 1) threads (prepared)")
    assert plan_shape(spec, [Prepared()], "all", executor, None, FAKE_HOST, stop_at_first_failure=False) == (
        "(4 testbench sims) = 4 simulations per point on local, 2 workers × (1 + 1) threads (prepared)")


def test_optimize_hands_the_schedule_the_rows_it_adopted(tmp_path, monkeypatch):
    """Section 3: the rows ``opt.optimize`` adopted from ``initial=`` count in the history the order is learned from."""
    spec = two_by_two(simulator={**minimal_spec()["simulator"], "stop_at_first_failure": True})   # 4 per point: on by the spec
    seen: list[list[Observation]] = []
    learn = Schedule.from_history
    monkeypatch.setattr(Schedule, "from_history", classmethod(lambda cls, spec, rows, children, scope, **kinds:
                                                              seen.append(list(rows)) or learn(spec, rows, children, scope, **kinds)))
    foreign = row(spec, 99, child("tb", "tt", NF=8.0), child("g", "tt", G=2.0)).model_copy(
        update={"params": {"F": "30", "W": "1.2u"}, "spec_fingerprint": "elsewhere", "metrics": {"NF": 8.0, "G": 2.0}})
    store = RunStore(tmp_path)
    optimize(spec, LocalExecutor(store.root / "sims"), store, pipeline=[Prepared()], strategy="random", budget=2, batch=2,
             initial=[foreign], current=False, limits=FAKE_HOST)
    assert seen and [o.origin for o in seen[0]] == ["initial:user"]


# -- 4. the verdict of an incomplete set ----------------------------------------------------------------------------------


WANTED = ["tb/tt", "tb/ss", "g/tt", "g/ss"]


def test_aggregate_on_an_incomplete_set():
    spec = two_by_two()
    complete = {"tb/tt": child("tb", "tt", NF=8.0), "tb/ss": child("tb", "ss", NF=9.5), "g/tt": child("g", "tt", G=2.0),
                "g/ss": child("g", "ss", G=2.0)}
    assert aggregate(spec, complete, WANTED) == aggregate(spec, complete) == aggregate(spec, complete, None)   # today's

    # 1. a child that did not run to its end
    agg = aggregate(spec, {"tb/tt": child("tb", "tt", NF=8.0),
                           "tb/ss": child("tb", "ss", status="failed:spectre", issues=["spectre exited 1"])}, WANTED)
    assert agg.status == "failed:spectre" and not agg.feasible and agg.fom is None and agg.metrics == {}
    assert agg.issues == ["tb/ss: spectre exited 1", "not simulated: 2 of 4 children (stopped after tb/ss)"]

    # 2. a child that ran but lost a metric: the nominal corner's metrics
    agg = aggregate(spec, {"tb/tt": child("tb", "tt", NF=8.0),
                           "tb/ss": child("tb", "ss", status="metric_failed", issues=["metric NF failed: non_scalar"])}, WANTED)
    assert agg.status == "metric_failed" and not agg.feasible and agg.metrics == {"NF": 8.0} and agg.corner_objectives == {}
    assert agg.issues == ["tb/ss: metric NF failed: non_scalar", "not simulated: 2 of 4 children (stopped after tb/ss)"]

    # 3. the present metrics of a corner in the scope violate: that corner, no fom; objectives of the complete corners only
    ran = {"tb/tt": child("tb", "tt", NF=8.0), "g/tt": child("g", "tt", G=2.0, issues=["a warning"]),
           "tb/ss": child("tb", "ss", NF=9.5)}
    agg = aggregate(spec, ran, WANTED)
    assert (agg.status, agg.feasible, agg.fom, agg.objective, agg.selected_corner) == ("constraint_failed", False, None, None, "ss")
    assert agg.metrics == {"NF": 9.5} and agg.constraint_penalty == pytest.approx((0.5 / 9) ** 2)
    assert agg.corner_objectives == {"tt": pytest.approx(6.0)}
    assert agg.issues == ["ss: NF lt 9 violated by 9.5", "not simulated: 1 of 4 children (stopped after tb/ss)",
                          "g/tt: a warning"]
    nominal = two_by_two(corner_policy={"constraints": "nominal"})
    agg = aggregate(nominal, {"tb/tt": child("tb", "tt", NF=9.5)}, WANTED)
    assert agg.status == "constraint_failed" and agg.selected_corner == "tt"
    assert agg.issues == ["tt: NF lt 9 violated by 9.5", "not simulated: 3 of 4 children (stopped after tb/tt)"]

    # 4. nothing that fails the point: not what the engine records
    with pytest.raises(ValueError, match="none of them fails the point"):
        aggregate(spec, {"tb/tt": child("tb", "tt", NF=8.0)}, WANTED)
    with pytest.raises(ValueError, match="none of them fails the point"):            # ss's constraints do not count
        aggregate(nominal, {"tb/tt": child("tb", "tt", NF=8.0), "tb/ss": child("tb", "ss", NF=9.5)}, WANTED)


# -- 5. evaluate_partial ----------------------------------------------------------------------------------------------------


def test_evaluate_partial_judges_the_metrics_that_are_there():
    spec = two_by_two()
    ev = objective.evaluate_partial(spec, {"NF": 9.5})                          # G absent: its constraint is skipped
    assert (ev.status, ev.fom, ev.objective, ev.feasible) == ("constraint_failed", None, None, False)
    assert ev.issues == ["NF lt 9 violated by 9.5"] and ev.constraint_penalty == pytest.approx((0.5 / 9) ** 2)
    assert objective.evaluate_partial(spec, {"NF": math.nan, "G": 0.5}).issues == ["G gt 1 violated by 0.5"]
    for metrics in ({"NF": 8.0}, {"NF": 8.0, "G": 2.0}, {}):                    # never a fom, never feasible
        ev = objective.evaluate_partial(spec, metrics)
        assert (ev.status, ev.fom, ev.objective, ev.feasible, ev.constraint_penalty, ev.issues) == (
            "incomplete", None, None, False, 0.0, [])
    assert objective.evaluate(spec, {"NF": 9.5, "G": 2.0}).fom == pytest.approx(7.5)      # evaluate is unchanged


# -- 6. the fingerprint -----------------------------------------------------------------------------------------------------


def test_the_switch_is_not_the_problem_and_its_default_stays_out_of_the_dump():
    """The default is unset since T17.9 (the stop follows the run's corners): ``true`` is written like ``false``."""
    before = golden()                                                           # GOLDEN: written before the field existed
    default = golden(simulator={**GOLDEN["simulator"], "stop_at_first_failure": None})
    assert before.simulator.stop_at_first_failure is None
    assert "stop_at_first_failure" not in before.model_dump(mode="json")["simulator"]
    assert default.model_dump(mode="json") == before.model_dump(mode="json")
    assert (default.fingerprint(), default._legacy_fingerprint()) == (before.fingerprint(), before._legacy_fingerprint()) == (
        "15d08ed68b2dfd7c", "87ca2259471db3ad")                                  # test_engine's pinned values
    off = golden(simulator={**GOLDEN["simulator"], "stop_at_first_failure": False})
    assert off.fingerprint() == before.fingerprint() and off.model_dump(mode="json")["simulator"]["stop_at_first_failure"] is False


# -- 7. the digest and the report ---------------------------------------------------------------------------------------------


def test_the_digest_and_the_report_read_stopped_points(tmp_path):
    """Section 14, item 1: they read ``not_run``, never the "not simulated" line. Without that line the numbers are the
    same, and a point that carries the line but ran every child is not counted as stopped."""
    spec = two_by_two()
    store, _, obs = run_batch(tmp_path, spec, stop_at_first_failure=True)
    plain = [o.model_copy(update={"issues": [t for t in o.issues if not t.startswith("not simulated")]}) for o in obs]
    posing = [plain[0], plain[1].model_copy(update={"issues": ["not simulated: 1 of 4 children (stopped after tb/tt)"]}),
              *plain[2:]]
    assert plain[1].status == "ok" and plain[1].not_run == []
    for rows in (obs, plain, posing):
        analyze.digest(spec, rows, store)
        d = json.loads((store.reports_dir() / "digest.json").read_text(encoding="utf-8"))
        counts = d["counts"]
        assert (counts["points"], counts["simulations"], counts["stopped_early"], counts["simulations_not_run"]) == (5, 14, 3, 6)
        assert counts["by_status"] == {"constraint_failed": 3, "failed:spectre": 1, "ok": 1}
        assert d["failures"]["messages"] == [{"text": "g/tt: spectre exited 1", "count": 1}]    # not the "not simulated" line
        md = (store.reports_dir() / "digest.md").read_text(encoding="utf-8")
        assert "· 14 simulations · 3 stopped early (6 simulations not run)" in md

        report = analyze.report(spec, rows, store).read_text(encoding="utf-8")
        # a stopped point is judged on what ran: 20 at tt, 24 at tt (its failed g/tt), 26 at ss; 28 ran whole, fails at ss
        assert "- failures per corner (a point counts at every corner it fails at): tt 2/5, ss 2/5" in report
        assert "- worst corner: tt (2 of 5 observations fail there)" in report
        assert "- NF < 9 dB violated at: tt 1/5, ss 1/5" in report and "- G > 1 dB violated at: tt 0/5, ss 1/5" in report


# -- the signoff recipe ---------------------------------------------------------------------------------------------------------


def test_signoff_stops_a_point_at_its_first_failing_corner_unless_full(tmp_path):
    from tests.ic_opt.test_cli_recipes import fake_run, project

    def worse_at_ss(p, tb, c):
        return {"NF": 5.0 + int(p["F"]) / 10 + (3.0 if c == "ss" else 0.0)}         # 8 + F / 10 >= 10 at ss: fails NF < 9 dB

    for full, ran in ((False, ["tb/tt", "tb/ss"]), (True, ["tb/tt", "tb/ss", "tb/ff"])):
        (tmp_path / str(full)).mkdir()
        run = fake_run(project(tmp_path / str(full), corners=("tt", "ss", "ff")), metric_fn=worse_at_ss)
        signoff.main(run, corner="tt", budget=4, batch=2, top=2, strategy="random", seed=2, full=full)
        check = run.store.observations().by_step("signoff")
        assert len(check) == 2 and all(list(o.children) == ran and o.status == "constraint_failed" for o in check), full


# -- section 14 (revision 1): what a stopped point means to the rest ------------------------------------------------------


EVERYWHERE = ["tb/tt", "tb/ss", "g/tt", "g/ss"]
PAIR = ["tb/nominal", "g/nominal"]


def pair_spec(**overrides) -> Spec:
    """tb gives NF, g gives G, at one condition: NF < 9 and G > 1, minimize NF - G."""
    return Spec.model_validate({**minimal_spec(
        testbenches=[bench("tb"), bench("g")],
        metrics=[{"name": "NF", "unit": "dB", "expression": "nf()", "testbench": "tb"},
                 {"name": "G", "unit": "dB", "expression": "g()", "testbench": "g"}],
        constraints=[{"metric": "NF", "op": "lt", "value": "9"}, {"metric": "G", "op": "gt", "value": "1"}],
        objective={"direction": "minimize", "expression": "NF - G"}), **overrides})


def point(spec: Spec, i: int, params: dict[str, str], *children: ChildResult, wanted: list[str] | None = None) -> Observation:
    """What the engine records for a point whose ``children`` ran, of ``wanted`` (None: every child ran)."""
    ran = {f"{c.unit}/{c.corner or 'nominal'}": c for c in children}
    agg = aggregate(spec, ran, wanted)
    return Observation(obs_id=f"obs_{i + 1:04d}", params=params, origin="user", children=ran,
                       not_run=[key for key in wanted or () if key not in ran], metrics=agg.metrics, fom=agg.fom,
                       objective=agg.objective, feasible=agg.feasible, constraint_penalty=agg.constraint_penalty,
                       status=agg.status, issues=agg.issues, simulations=len(ran), spec_fingerprint=spec.fingerprint(),
                       pipeline_fingerprint="recorded", started_at="t", finished_at="t")


def at(f: str, w: str = "0.6u") -> dict[str, str]:
    return {"F": f, "W": w}


def test_not_run_is_in_the_record_only_when_a_point_stopped():
    spec = two_by_two()
    whole = point(spec, 0, at("20"), child("tb", "tt", NF=8.0), child("tb", "ss", NF=8.0), child("g", "tt", G=2.0),
                  child("g", "ss", G=2.0), wanted=EVERYWHERE)
    stopped = point(spec, 1, at("22"), child("tb", "tt", NF=9.5), wanted=EVERYWHERE)
    assert whole.not_run == [] and "not_run" not in json.loads(whole.model_dump_json())
    assert json.loads(stopped.model_dump_json())["not_run"] == ["tb/ss", "g/tt", "g/ss"]
    assert Observation.model_validate_json(stopped.model_dump_json()) == stopped
    assert Observation.model_validate_json(whole.model_dump_json()).not_run == []     # a line of a store written before


def test_a_stopped_point_is_a_row_of_every_corner_it_was_to_run_at():
    """``Observation.corners``: a re-check at all corners stopped at its first corner stays out of the one-corner search."""
    spec = two_by_two()
    search = [point(spec, i, at(f), child("tb", "tt", NF=8.0), child("g", "tt", G=2.0)) for i, f in enumerate(("20", "22", "24"))]
    recheck = [point(spec, 3, at("20", "0.8u"), child("tb", "tt", NF=8.0), child("tb", "ss", NF=8.2), child("g", "tt", G=2.0),
                     child("g", "ss", G=1.5), wanted=EVERYWHERE),
               point(spec, 4, at("22", "0.8u"), child("tb", "tt", NF=9.5), wanted=EVERYWHERE)]    # stopped at tt
    assert set(recheck[1].children) == {"tb/tt"} and recheck[1].corners() == {"tt", "ss"}
    rows = Observations(search + recheck)
    assert _at_corners(spec, rows, ["tt"]) == search and _at_corners(spec, rows, "all") == recheck
    assert history_size(spec, rows, ["tt"]) == 3 and history_size(spec, rows, "all") == 2
    with pytest.raises(ValueError, match=r"several sets of corners \(tt: 3; ss, tt: 2\)"):
        history_size(spec, rows)
    assert history_size(spec, Observations(recheck)) == 2              # one set of corners: no refusal
    assert digest_module.digest(spec, recheck[1:])["problem"]["corners"] == ["ss", "tt"]
    assert resolve_auto(spec, 1, [recheck[1]]) == ("metric_gp", "no EM devices")     # T17.9: corners do not decide
    with pytest.raises(ValueError, match=r"this history holds several \(tt: 3; ss, tt: 1\)"):
        MetricGpSuggester().propose(spec, Observations(search + recheck[1:]), 2, seed=0)


def test_metric_gp_ranks_a_point_that_ran_every_child_before_a_stopped_one():
    """Rule 3: among infeasible points the one that got furthest, then the smallest violation of the constraints judged
    (a metric the point never got counts 0); a nan violation is never chosen."""
    spec = pair_spec()
    rows = [point(spec, 0, at("20"), child("tb", NF=9.05), wanted=PAIR),                     # stopped: the smallest violation
            point(spec, 1, at("22"), child("tb", NF=12.0), child("g", G=0.2)),
            point(spec, 2, at("24"), child("tb", NF=8.0), child("g", G=0.9))]
    assert [o.status for o in rows] == ["constraint_failed"] * 3 and rows[0].not_run == ["g/nominal"]
    composer, scales, arrays = Composer(spec), metric_scales(spec, rows), true_arrays(spec, rows)
    assert math.isnan(composer.violation(arrays, scales)[0])                                  # what argmin chose before
    known = composer.known_violation(arrays, scales)
    assert np.isfinite(known).all() and known[0] == min(known)
    best = region.incumbent(composer, rows, [0, 1, 2], scales, arrays)
    assert (best.position, best.feasible, best.value) == (2, False, pytest.approx(known[2]))
    assert region.centre(composer, rows, region.Region(own=[0, 1, 2]), scales, arrays) == 2

    spec = three_testbenches()                                                               # two stopped points
    rows = [point(spec, 0, at("20"), child("a", A=1.01), wanted=["a/nominal", "b/nominal", "c/nominal"]),
            point(spec, 1, at("22"), child("a", A=0.5), child("b", B=5.0), wanted=["a/nominal", "b/nominal", "c/nominal"])]
    composer, scales, arrays = Composer(spec), metric_scales(spec, rows), true_arrays(spec, rows)
    assert region.incumbent(composer, rows, [0, 1], scales, arrays).position == 1            # fewer children not run


def test_openbox_takes_a_stopped_point_as_a_failed_trial():
    """No objective and constraints on metrics of two testbenches: a stopped point was fed as a successful trial and
    OpenBox's residuals asked for the metric it never got (KeyError); it goes in as a failed trial."""
    from openbox.utils.constants import FAILED, SUCCESS

    spec = pair_spec(objective=None)
    rows = Observations([point(spec, 0, at("20"), child("tb", NF=8.0), child("g", G=2.0)),
                         point(spec, 1, at("22"), child("tb", NF=10.0), child("g", G=2.0)),
                         point(spec, 2, at("24"), child("tb", NF=12.0), wanted=PAIR),
                         point(spec, 3, at("26", "0.8u"), child("tb", NF=7.0), child("g", G=3.0)),
                         point(spec, 4, at("28", "0.8u"), child("tb", NF=8.0), child("g", G=0.5))])
    assert rows[2].status == "constraint_failed" and rows[2].not_run == ["g/nominal"]
    assert minimization_objective(spec, rows[2]) is None and minimization_objective(pair_spec(), rows[2]) is None
    assert OpenBoxSuggester().advisor(spec, rows, seed=0).history.trial_states == [SUCCESS, SUCCESS, FAILED, SUCCESS, SUCCESS]
    assert len(suggest(spec, rows, 2, strategy="openbox_gp_eic", seed=0, initial_trials=4)) == 2


def test_turbo_never_centres_its_region_on_a_stopped_point():
    """TuRBO centres its region on the smallest target, the first of equal ones: a stopped point is a failed trial, one
    float step above the others, so the centre is a point that ran every child whenever there is one."""
    spec = pair_spec()
    stopped = point(spec, 0, at("20"), child("tb", NF=9.05), wanted=PAIR)                  # the smallest penalty, and first
    infeasible = point(spec, 1, at("22"), child("tb", NF=12.0), child("g", G=2.0))
    failed = point(spec, 2, at("24"), child("tb", status="failed:spectre", issues=["spectre exited 1"]), child("g", G=2.0))
    y = targets(spec, Observations([stopped, infeasible, failed]))
    assert int(np.argmin(y)) == 1 and y[2] == y[1] == pytest.approx(1 / 3) and y[0] == np.nextafter(y[1], np.inf)
    feasible = point(spec, 3, at("26"), child("tb", NF=8.0), child("g", G=2.0))
    y = targets(spec, Observations([stopped, feasible, infeasible]))
    assert int(np.argmin(y)) == 1 and y[0] > max(y[1], y[2])


def improved_as_before(before: region.Incumbent | None, after: region.Incumbent | None) -> bool | None:
    """``region.improved`` as it was before rule 3 reached it, for the comparisons below."""
    if before is None:
        return None if after is not None else False
    if not before.feasible:
        return after.feasible or after.value < before.value - region.IMPROVEMENT * abs(before.value)
    return after.value < before.value - region.IMPROVEMENT * max(1.0, abs(before.value))


def test_improved_is_unchanged_where_no_point_was_stopped(monkeypatch):
    best = region.Incumbent
    pairs = [(None, None), (None, best(0, False, 1.0)),
             (best(0, True, 5.0), best(1, True, 4.0)), (best(0, True, 5.0), best(0, True, 5.0)),
             (best(0, True, 5.0), best(1, True, 4.999)),                 # better by less than max(1, 5) x 1e-3
             (best(0, True, -0.5), best(1, True, -1.6)), (best(0, False, 2.0), best(1, True, 9.0)),
             (best(0, False, 2.0), best(1, False, 1.0)), (best(0, False, 2.0), best(1, False, 1.9995)),
             (best(0, False, 0.0), best(0, False, 0.0))]
    verdicts = [region.improved(b, a) for b, a in pairs]
    assert verdicts == [improved_as_before(b, a) for b, a in pairs]
    assert verdicts == [False, None, True, False, False, True, True, True, False, False]

    # a history without stopped points: the incumbents of its growing prefixes, and the replay of its batches
    spec = pair_spec()
    measured = [(10.0, 2.0), (12.0, 0.2), (9.5, 0.9), (9.6, 0.8), (8.0, 2.0), (8.5, 1.5)]    # NF, G; the 5th is feasible
    origins = ["suggest:metric_gp:init"] * 2 + [f"suggest:metric_gp:grid:{k}" for k in range(2, 6)]
    rows = [point(spec, i, at(f), child("tb", NF=nf), child("g", G=g)).model_copy(update={"origin": origin})
            for i, ((nf, g), f, origin) in enumerate(zip(measured, ("20", "22", "24", "26", "28", "30"), origins, strict=True))]
    composer, scales, arrays = Composer(spec), metric_scales(spec, rows), true_arrays(spec, rows)
    prefixes = [region.incumbent(composer, rows, list(range(k)), scales, arrays) for k in range(1, len(rows) + 1)]
    verdicts = [region.improved(b, a) for b, a in pairwise(prefixes)]
    assert verdicts == [improved_as_before(b, a) for b, a in pairwise(prefixes)]
    assert verdicts == [False, True, False, True, False]
    trace = region.replay(spec, rows, 2).trace
    monkeypatch.setattr(region, "improved", improved_as_before)
    assert region.replay(spec, rows, 2).trace == trace


def test_improved_when_the_best_moves_from_a_stopped_point_to_one_that_ran_every_child():
    spec = pair_spec()
    rows = [point(spec, 0, at("20"), child("tb", NF=9.05), wanted=PAIR).model_copy(update={"origin": "suggest:metric_gp:init"}),
            point(spec, 1, at("22"), child("tb", NF=12.0), child("g", G=2.0)).model_copy(
                update={"origin": "suggest:metric_gp:grid:1"})]
    composer, scales, arrays = Composer(spec), metric_scales(spec, rows), true_arrays(spec, rows)
    before = region.incumbent(composer, rows, [0], scales, arrays)
    after = region.incumbent(composer, rows, [0, 1], scales, arrays)
    assert (before.position, before.not_run, after.position, after.not_run) == (0, 1, 1, 0) and after.value > before.value
    assert region.improved(before, after) is True and improved_as_before(before, after) is False
    state = region.replay(spec, rows, 2)                   # the batch that brought the complete point is a success
    assert (state.successes, state.failures) == (1, 0)


# -- N-63, step 3: the device first (docs/refactor/N63_DEVICE_FIRST_SPEC.md, section 3) -------------------------------------


def srf(od: str) -> float:
    """The fake device's SRF: 60 GHz at an outer diameter of 80 um, 6 GHz less per 10 um more -- 120 um fails SRF > 40 GHz."""
    return 60e9 - (float(od) - 80) / 10 * 6e9


def device_spec(*devices: str, **overrides) -> Spec:
    """pair_spec's two testbenches (tb gives NF, g gives G; NF < 9, G > 1, minimize NF - G) and EM devices (``ind`` unless
    named) whose SRF must exceed 40 GHz, each drawn from its own outer diameter ``<device>.od``: two testbench simulations
    per point, the count rule off."""
    devices = devices or ("ind",)
    d = minimal_spec(
        testbenches=[bench("tb"), bench("g")],
        devices=[{"id": dev, "generator": "demo", "profile": "demo_6m", "ports": ["P1", "N1"], "fixed": {"turns": 1}}
                 for dev in devices],
        variables=[*minimal_spec()["variables"],
                   *({"name": f"{dev}.od", "kind": "integer", "lower": "80", "upper": "120", "step": "10"} for dev in devices)],
        metrics=[{"name": "NF", "unit": "dB", "expression": "nf()", "testbench": "tb"},
                 {"name": "G", "unit": "dB", "expression": "g()", "testbench": "g"},
                 *({"name": f"SRF_{dev}", "unit": "Hz", "device": dev, "quantity": "SRF_p"} for dev in devices)],
        constraints=[{"metric": "NF", "op": "lt", "value": "9"}, {"metric": "G", "op": "gt", "value": "1"},
                     *({"metric": f"SRF_{dev}", "op": "gt", "value": "40e9"} for dev in devices)],
        objective={"direction": "minimize", "expression": "NF - G"}, budget={"max_simulations": 1000})
    d.update(overrides)
    return Spec.model_validate(d)


def switch(value: bool | None) -> dict:
    """``simulator`` with ``stop_at_first_failure`` as given."""
    return {"simulator": {**minimal_spec()["simulator"], "stop_at_first_failure": value}}


class FakeEmx:
    """The point-level stage of a fake EM pipeline: one EMX simulation per geometry (``runs = 1``; the engine caches it on
    the outer diameters), every device's SRF from its outer diameter (:func:`srf`); ``calls`` counts the runs."""

    name = "emx"
    level = "point"
    runs = 1
    resources = Resources()

    def __init__(self) -> None:
        self.calls = 0

    def fingerprint(self, point: Point, ctx: StageContext) -> str:
        return "-".join(point.params[f"{d}.od"] for d in ctx.spec.device_ids)

    def run(self, point: Point, ctx: StageContext) -> dict[str, float]:
        self.calls += 1
        return {d: srf(point.params[f"{d}.od"]) for d in ctx.spec.device_ids}

    def save(self, out: dict[str, float], directory) -> None:
        (directory / "srf.json").write_text(json.dumps(out), encoding="utf-8")

    def load(self, directory, point: Point, ctx: StageContext) -> dict[str, float]:
        return json.loads((directory / "srf.json").read_text(encoding="utf-8"))


class FakeMeasure:
    """The device child of the fake EM pipeline: the SRF the point's EMX stage gave. It declares no ``simulates``, as the
    EM pipeline's ``Measure`` does not: the engine counts its measurement as the EM pipeline counts it."""

    name = "measure"
    level = "child"
    unit = "device"
    resources = Resources()

    def __init__(self, ran: list) -> None:
        self.ran = ran

    def fingerprint(self, inp, ctx: StageContext) -> str | None:
        return None

    def run(self, srfs: dict[str, float], ctx: StageContext) -> ChildResult:
        self.ran.append((ctx.point.params["F"], ctx.unit, ctx.corner))
        return ChildResult(unit=ctx.unit, corner=None, status="ok", metrics={f"SRF_{ctx.unit}": srfs[ctx.unit]})


class Bench:
    """The testbench child of the fake EM pipeline: NF from tb (9.5 at F=26, else 8), G from g (2)."""

    name = "bench"
    level = "child"
    unit = "testbench"
    resources = Resources()

    def __init__(self, ran: list) -> None:
        self.ran = ran

    def fingerprint(self, inp, ctx: StageContext) -> str | None:
        return None

    def run(self, srfs: dict[str, float], ctx: StageContext) -> ChildResult:
        f = ctx.point.params["F"]
        self.ran.append((f, ctx.unit, ctx.corner))
        metrics = {"NF": 9.5 if f == "26" else 8.0} if ctx.unit == "tb" else {"G": 2.0}
        return ChildResult(unit=ctx.unit, corner=ctx.corner, status="ok", metrics=metrics)


def em_pipeline() -> tuple[list, list, FakeEmx]:
    """point-level fake EMX -> device measurement child + testbench children, and the order the children ran in."""
    ran: list = []
    emx = FakeEmx()
    return [emx, FakeMeasure(ran), Bench(ran)], ran, emx


# F picks the testbench results (F=26 fails NF < 9 at tb), ind.od the device's SRF (120 um fails SRF > 40 GHz)
DEVICE_POINTS = [Point({"F": f, "W": "0.6u", "ind.od": od}, "user") for f, od in (("20", "80"), ("22", "120"), ("24", "120"),
                                                                                   ("26", "100"))]


def run_devices(project, spec: Spec, points=DEVICE_POINTS, **kwargs):
    store = RunStore(project)
    pipeline, ran, emx = em_pipeline()
    obs = evaluate(spec, points, LocalExecutor(store.root / "sims"), store, pipeline=pipeline, parallel_jobs=1,
                   limits=FAKE_HOST, **kwargs)
    return store, ran, emx, obs


def test_the_device_children_run_first_whatever_the_history():
    """Item 1: the device children first -- the library devices', then the others', each in the spec's device order --
    then the testbenches; a history that reorders the testbenches never moves a device child behind one."""
    spec = device_spec("ind", "xfm")
    children = [Child("testbench", "tb", None), Child("testbench", "g", None), Child("device", "ind", None),
                Child("device", "xfm", None)]
    every = Schedule.from_history(spec, [], children, "all_corners")
    assert keys(every.spec_order(children[::-1])) == ["ind/nominal", "xfm/nominal", "tb/nominal", "g/nominal"]
    assert keys(every.order(children)) == ["ind/nominal", "xfm/nominal", "tb/nominal", "g/nominal"]

    # a valid spec does not mix library and EM devices (T18.2B refuses it); the order still says which comes first
    from ic_opt.spec import LibrarySource

    mixed = spec.model_copy(update={"devices": [spec.devices[0], spec.devices[1].model_copy(update={
        "library": LibrarySource.model_construct(root="/lib", stratum="xfm", frequency_hz=1e10)})]})
    assert [d.id for d in mixed.library_devices] == ["xfm"]
    assert keys(Schedule.from_history(mixed, [], children, "all_corners").order(children)) == [
        "xfm/nominal", "ind/nominal", "tb/nominal", "g/nominal"]

    # g fails on every recorded point and costs a millisecond, the devices never fail and take 100 s: by the score a
    # device would run last; it runs first, the testbenches in their learned order after it
    history = [row(spec, i, child("ind", seconds=100.0, SRF_ind=50e9), child("xfm", seconds=100.0, SRF_xfm=50e9),
                   child("tb", seconds=5.0, NF=8.0), child("g", seconds=0.001, G=0.5), status="constraint_failed")
               for i in range(12)]
    learned = Schedule.from_history(spec, history, children, "all_corners")
    assert learned.histories is not None and learned.history_of(children[1]).failed == 12
    assert learned.history_of(children[2]).score < learned.history_of(children[0]).score    # the device scores lowest
    assert keys(learned.order(children)) == ["ind/nominal", "xfm/nominal", "g/nominal", "tb/nominal"]
    # the device kind alone (the count rule off): the learned order is not used, the spec's is
    device_only = Schedule.from_history(spec, history, children, "all_corners", stop_kinds=frozenset({"device"}))
    assert device_only.histories is None
    assert keys(device_only.order(children)) == ["ind/nominal", "xfm/nominal", "tb/nominal", "g/nominal"]


def test_a_device_stops_a_point_by_its_own_rule_a_testbench_by_the_count_rule():
    """Item 2: with the count rule off a device's violation stops the point, a testbench's does not; with the count rule
    on both do; under ``stop_at_first_failure: false`` neither; the recipe override False wins over everything."""
    from ic_opt.blocks.evaluate import stop_kinds
    from ic_opt.eval import engine

    pipeline, _, _ = em_pipeline()
    bad_device, bad_bench = child("ind", SRF_ind=36e9), child("tb", NF=9.5)

    def stops(spec: Spec, children, override=None) -> tuple[bool, bool]:
        """Whether the device's violation and the testbench's stop a point of ``spec`` under ``override``."""
        kinds = stop_kinds(spec, children, override)
        schedule = Schedule.from_history(spec, [], children, "all_corners", stop_kinds=kinds)
        return (schedule.stop_after(bad_device, None) is not None,
                schedule.stop_after(bad_bench, children[0].corner) is not None)

    few = device_spec()                                                         # 2 testbench simulations per point
    few_children = engine.children_of(few, pipeline, few.corner_ids)
    assert stop_kinds(few, few_children) == {"device"} and stops(few, few_children) == (True, False)
    schedule = Schedule.from_history(few, [], few_children, "all_corners", stop_kinds=stop_kinds(few, few_children))
    assert schedule.stop_after(bad_device, None) == "ind/nominal: SRF_ind gt 40e9 violated by 3.6e+10"
    assert schedule.failure(bad_bench, None) == "tb/nominal: NF lt 9 violated by 9.5"     # a failure, not a stop
    assert schedule.stop_after(child("ind", status="failed:measure"), None) == "ind/nominal: failed:measure"

    many = device_spec(corners=[{"id": f"c{i}"} for i in range(10)])           # 2 x 10 = 20: the count rule on
    many_children = engine.children_of(many, pipeline, many.corner_ids)
    assert stop_kinds(many, many_children) == {"device", "testbench"} and stops(many, many_children) == (True, True)

    for spec, children in ((few, few_children), (many, many_children)):
        off = spec.model_copy(update={"simulator": spec.simulator.model_copy(update={"stop_at_first_failure": False})})
        on = spec.model_copy(update={"simulator": spec.simulator.model_copy(update={"stop_at_first_failure": True})})
        assert stop_kinds(off, children) == frozenset() and stops(off, children) == (False, False)
        assert stop_kinds(on, children) == {"device", "testbench"}                # true: every child may stop it
        for which in (spec, off, on):                                            # the override decides alone
            assert stop_kinds(which, children, False) == frozenset()
            assert stop_kinds(which, children, True) == {"device", "testbench"}

    circuit = pair_spec()                                                       # no device child: the count rule alone
    assert stop_kinds(circuit, engine.children_of(circuit, [Prepared()], circuit.corner_ids)) == frozenset()
    devices_only = [c for c in few_children if c.unit_kind == "device"]         # no testbench: nothing to stop before
    assert stop_kinds(few, devices_only) == frozenset()


def test_a_point_whose_device_fails_runs_no_testbench(tmp_path, capsys):
    """Item 3: the fake EM pipeline, the count rule off. A point whose device violates its constraint holds its device
    child only and names both testbenches in ``not_run``; one whose device passes runs every child, recorded as without
    a schedule; the batch line counts the points the device stopped."""
    spec = device_spec()
    store, ran, emx, obs = run_devices(tmp_path / "on", spec)
    by_f = {o.params["F"]: o for o in obs}

    assert ran[:3] == [("20", "ind", None), ("20", "tb", None), ("20", "g", None)]          # the device first
    whole = by_f["20"]
    assert list(whole.children) == ["tb/nominal", "g/nominal", "ind/nominal"] and whole.status == "ok" and whole.feasible
    assert whole.not_run == [] and whole.simulations == 4                  # EMX + 2 testbenches + the device's measurement

    stopped = by_f["22"]
    assert list(stopped.children) == ["ind/nominal"] and stopped.not_run == ["tb/nominal", "g/nominal"]
    assert (stopped.status, stopped.feasible, stopped.fom, stopped.objective) == ("constraint_failed", False, None, None)
    assert stopped.metrics == {"SRF_ind": 36e9} and stopped.constraint_penalty == pytest.approx(0.1 ** 2)
    assert stopped.issues == ["nominal: SRF_ind gt 40e9 violated by 3.6e+10",
                              "not simulated: 2 of 3 children (stopped after ind/nominal)"]
    # the point-level runs (one EMX run, a miss) and the device's measurement, which the EM pipeline counts; no testbench
    assert stopped.cache == {"emx": "miss"} and stopped.simulations == 2
    assert [c for c in ran if c[0] == "22"] == [("22", "ind", None)]
    assert not (store.root / "sims" / stopped.obs_id / "tb").exists()
    again = by_f["24"]                                                     # the same geometry: EMX served from the cache
    assert again.cache == {"emx": "hit"} and again.simulations == 1 and again.not_run == ["tb/nominal", "g/nominal"]
    assert emx.calls == 3

    fails_nf = by_f["26"]                                                  # a testbench's violation stops nothing here
    assert list(fails_nf.children) == ["tb/nominal", "g/nominal", "ind/nominal"] and fails_nf.not_run == []
    assert fails_nf.status == "constraint_failed" and fails_nf.issues == ["nominal: NF lt 9 violated by 9.5"]

    out = capsys.readouterr().out
    assert "[evaluate] step='evaluate': 2 of 4 points stopped early (2 at the device), 4 simulations not run" in out
    step = last_step(store)
    assert (step["stopped"], step["not_run"], step["simulations"]) == (2, 4, 4 + 2 + 1 + 4)

    # the switch off: every child of every point, and the points the device did not stop are recorded as with the stop
    off_store, off_ran, _, off = run_devices(tmp_path / "off", spec, stop_at_first_failure=False)
    assert all(len(o.children) == 3 and o.not_run == [] for o in off) and len(off_ran) == 12
    assert off_ran[:3] == [("20", "tb", None), ("20", "g", None), ("20", "ind", None)]      # the engine's order
    assert {o.params["F"]: o.status for o in off} == {"20": "ok", "22": "constraint_failed", "24": "constraint_failed",
                                                      "26": "constraint_failed"}
    for obs_id in ("obs_0001", "obs_0004"):
        assert json.dumps(line_of(store, obs_id)) == json.dumps(line_of(off_store, obs_id))
    assert "stopped early" not in capsys.readouterr().out


def test_several_devices_are_all_measured_before_the_point_stops(tmp_path):
    """A device's failure stops the point before its first testbench; the other devices still run, at no simulation's
    cost, so the record holds every device child and ``not_run`` every testbench child (N63 spec, 1.3)."""
    spec = device_spec("ind", "xfm")
    points = [Point({"F": "20", "W": "0.6u", "ind.od": "120", "xfm.od": "80"}, "user")]
    _, ran, _, (o,) = run_devices(tmp_path, spec, points)
    assert ran == [("20", "ind", None), ("20", "xfm", None)]
    assert list(o.children) == ["ind/nominal", "xfm/nominal"] and o.not_run == ["tb/nominal", "g/nominal"]
    assert o.status == "constraint_failed" and o.metrics == {"SRF_ind": 36e9, "SRF_xfm": 60e9}
    assert o.issues[-1] == "not simulated: 2 of 4 children (stopped after ind/nominal)"


def test_a_device_whose_measurement_fails_stops_the_point_unless_the_spec_says_false(tmp_path):
    """A measurement that fails (``failed:measure``) stops the point as a violated constraint does, its status the
    child's; under the spec's ``stop_at_first_failure: false`` every child of every point runs, the device's too."""
    from ic_opt.eval.stage import StageFailure

    class Unmeasurable(FakeMeasure):
        def run(self, srfs, ctx):
            if ctx.point.params["ind.od"] == "90":
                raise StageFailure("device ind: no resonance found")
            return super().run(srfs, ctx)

    def run(project, spec):
        store = RunStore(project)
        ran: list = []
        pipeline = [FakeEmx(), Unmeasurable(ran), Bench(ran)]
        points = [Point({"F": "20", "W": "0.6u", "ind.od": od}, "user") for od in ("90", "120")]
        return evaluate(spec, points, LocalExecutor(store.root / "sims"), store, pipeline=pipeline, parallel_jobs=1,
                        limits=FAKE_HOST), ran

    (lost, violates), ran = run(tmp_path / "on", device_spec())
    assert lost.status == "failed:measure" and list(lost.children) == ["ind/nominal"]
    assert lost.not_run == ["tb/nominal", "g/nominal"] and lost.metrics == {} and lost.simulations == 2
    assert lost.issues == ["ind/nominal: device ind: no resonance found",
                           "not simulated: 2 of 3 children (stopped after ind/nominal)"]
    assert violates.status == "constraint_failed" and violates.not_run == ["tb/nominal", "g/nominal"]
    assert ran == [("20", "ind", None)]                    # the second point's measurement; the first raised, no testbench ran
    off, ran = run(tmp_path / "off", device_spec(**switch(False)))
    assert [(o.status, list(o.children), o.not_run) for o in off] == [
        ("failed:measure", ["tb/nominal", "g/nominal", "ind/nominal"], []),
        ("constraint_failed", ["tb/nominal", "g/nominal", "ind/nominal"], [])]
    assert len(ran) == 5                                   # 2 + 2 testbenches and the second point's measurement


def test_the_plan_says_the_device_is_measured_first(tmp_path):
    executor = LocalExecutor(tmp_path)
    pipeline, _, _ = em_pipeline()
    device_first = ("up to 4 simulations per point (the device measured first: a point whose device fails a constraint "
                    "stops before any testbench simulation")
    line = plan_shape(device_spec(), pipeline, "all", executor, None, FAKE_HOST)
    assert line.startswith(f"(1 EMX runs + 2 testbench sims + 1 device measurements) = {device_first}) on local")
    assert plan_shape(device_spec(), pipeline, "all", executor, None, FAKE_HOST, stop_at_first_failure=True).startswith(
        f"(1 EMX runs + 2 testbench sims + 1 device measurements) = {device_first}; a point stops at the first simulation "
        "that fails it) on local")
    for spec, override in ((device_spec(**switch(False)), None), (device_spec(), False)):    # the spec's false, the override's
        assert plan_shape(spec, pipeline, "all", executor, None, FAKE_HOST, override).startswith(
            "(1 EMX runs + 2 testbench sims + 1 device measurements) = 4 simulations per point on local")


def test_signoff_full_runs_the_testbenches_of_a_point_its_device_stops(tmp_path):
    """Item 4: what the recipe's re-check passes to ``sim.evaluate`` (``_recheck_stop``): without ``full`` the device
    stops the point, with ``full=True`` every corner of every testbench runs. (The recipe re-checks the feasible points
    of its search, whose device metrics are the same at every corner, so its own re-check meets no such point.)"""
    spec = device_spec(corners=[{"id": "tt"}, {"id": "ss"}])
    failing = [Point({"F": "22", "W": "0.6u", "ind.od": "120"}, "user")]
    for full, children in ((False, ["ind/nominal"]), (True, ["tb/tt", "tb/ss", "g/tt", "g/ss", "ind/nominal"])):
        _, _, _, (o,) = run_devices(tmp_path / str(full), spec, failing, stop_at_first_failure=signoff._recheck_stop(spec, full))
        assert list(o.children) == children and o.status == "constraint_failed", full
        assert o.not_run == ([] if full else ["tb/tt", "tb/ss", "g/tt", "g/ss"]) and o.corners() == {"tt", "ss"}


GOLDEN_LINE = {   # obs_0001 of run_batch(two_by_two()), recorded by the code before N-63 (bf25119), the clock taken out
    "obs_id": "obs_0001", "params": {"F": "20", "W": "0.6u"}, "origin": "user",
    "children": {f"{tb}/{c}": {"unit": tb, "corner": c, "status": "ok", "metrics": {name: value},
                               "issues": [], "sim_dir": f".icopt/sims/obs_0001/{tb}/{c}", "seconds": 0.0}
                 for tb, c, name, value in (("tb", "tt", "NF", 9.5), ("tb", "ss", "NF", 9.6), ("g", "tt", "G", 2.0),
                                            ("g", "ss", "G", 2.0))},
    "metrics": {"NF": 9.6, "G": 2.0}, "fom": 7.6, "objective": None, "feasible": False,
    "constraint_penalty": 0.004444444444444438, "status": "constraint_failed",
    "issues": ["tt: NF lt 9 violated by 9.5", "ss: NF lt 9 violated by 9.6"], "spec_fingerprint": "a1e5b36c782a4bb4",
    "pipeline_fingerprint": "5d0d682d55e76309", "step": "evaluate", "cache": {}, "simulations": 4, "started_at": "t",
    "finished_at": "t"}


def test_a_circuit_only_run_below_the_count_rule_builds_no_schedule_and_records_as_before(tmp_path, monkeypatch):
    """Item 5: no device child and 4 simulations per point -- no schedule is built, the children run in the engine's
    order, and a point a stop would have cut short is recorded field by field as before N-63."""
    built = []
    init = Schedule.__init__
    monkeypatch.setattr(Schedule, "__init__", lambda self, *args, **kwargs: built.append(args) or init(self, *args, **kwargs))
    store, stage, _ = run_batch(tmp_path, two_by_two())
    assert built == []
    for f in ("20", "22", "24", "26", "28"):
        assert [c[1:] for c in stage.ran if c[0] == f] == [("tb", "tt"), ("tb", "ss"), ("g", "tt"), ("g", "ss")]
    line = line_of(store, "obs_0001")
    assert list(line) == list(GOLDEN_LINE)
    for name, value in GOLDEN_LINE.items():
        assert line[name] == value, name


def test_the_digest_and_the_report_count_the_points_a_device_stopped(tmp_path):
    """Item 6: a store with points stopped at the device (F=22, 24) and one stopped at a testbench (F=26, the switch on)."""
    from ic_opt.sim.corner import stopped_at_device

    spec = device_spec()
    store, _, _, obs = run_devices(tmp_path, spec, stop_at_first_failure=True)
    assert [o.not_run for o in obs] == [[], ["tb/nominal", "g/nominal"], ["tb/nominal", "g/nominal"], ["g/nominal"]]
    assert [stopped_at_device(spec, o) for o in obs] == [False, True, True, False]
    analyze.digest(spec, obs, store)
    d = json.loads((store.reports_dir() / "digest.json").read_text(encoding="utf-8"))
    counts = d["counts"]
    assert (counts["stopped_early"], counts["stopped_at_device"], counts["simulations_not_run"]) == (3, 2, 5)
    assert counts["stopped_at"] == {"ind/nominal": 2, "tb/nominal": 1}
    md = (store.reports_dir() / "digest.md").read_text(encoding="utf-8")
    assert "· 3 stopped early (2 at the device; 5 simulations not run)" in md
    assert "stopped early: 3 points; at ind/nominal: 2, tb/nominal: 1" in md
    report = analyze.report(spec, obs, store).read_text(encoding="utf-8")     # judged on what ran: the device's SRF
    assert "- binding constraints: NF < 9 dB (1 of 4), SRF_ind > 40 GHz (2 of 4)" in report
    assert "- SRF_ind > 40 GHz: pass 2/4, best margin 20 GHz (obs_0001), worst -4 GHz (obs_0002)" in report
