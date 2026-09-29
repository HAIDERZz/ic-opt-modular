"""T17.8, step 1 (``docs/refactor/T17_8_SCHEDULE_SPEC.md``, section 11): a point's children run in an order learned from
the history, and the point stops at the first child whose result shows it cannot be feasible."""

from __future__ import annotations

import json
import math

import pytest

from ic_opt import objective
from ic_opt.blocks import analyze
from ic_opt.blocks.evaluate import evaluate, plan_shape
from ic_opt.blocks.optimize import optimize
from ic_opt.eval.engine import Child
from ic_opt.eval.schedule import ChildHistory, Schedule
from ic_opt.eval.stage import Resources, StageContext
from ic_opt.executor import LocalExecutor
from ic_opt.observation import ChildResult, Observation
from ic_opt.recipes import signoff
from ic_opt.sim.corner import aggregate, stopped_early
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.store import RunStore
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
    store, stage, obs = run_batch(tmp_path / "on", spec)
    by_f = {o.params["F"]: o for o in obs}

    first = by_f["20"]
    assert list(first.children) == ["tb/tt"] and first.status == "constraint_failed" and not first.feasible
    assert first.simulations == 1 and first.fom is None and first.objective is None and first.metrics == {"NF": 9.5}
    assert first.constraint_penalty == pytest.approx((0.5 / 9) ** 2)
    assert first.issues == ["tt: NF lt 9 violated by 9.5", "not simulated: 3 of 4 children (stopped after tb/tt)"]
    assert [c for c in stage.ran if c[0] == "20"] == [("20", "tb", "tt")]                 # nothing more ran for it

    feasible = by_f["22"]
    assert list(feasible.children) == ["tb/tt", "tb/ss", "g/tt", "g/ss"] and feasible.status == "ok" and feasible.feasible
    assert feasible.objective == pytest.approx(7.0) and feasible.simulations == 4 and feasible.issues == []

    failed = by_f["24"]
    assert list(failed.children) == ["tb/tt", "tb/ss", "g/tt"] and failed.status == "failed:spectre"
    assert failed.issues == ["g/tt: spectre exited 1", "not simulated: 1 of 4 children (stopped after g/tt)"]
    assert failed.simulations == 3

    at_ss = by_f["26"]
    assert list(at_ss.children) == ["tb/tt", "tb/ss"] and at_ss.status == "constraint_failed" and at_ss.metrics == {"NF": 9.5}
    assert at_ss.issues == ["ss: NF lt 9 violated by 9.5", "not simulated: 2 of 4 children (stopped after tb/ss)"]

    last = by_f["28"]                                               # its failing child was its last: recorded whole
    assert len(last.children) == 4 and last.status == "constraint_failed" and last.fom == pytest.approx(7.5)
    assert stopped_early(last.issues) is None and last.issues == ["ss: G gt 1 violated by 0.5"]

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
    (o,) = evaluate(spec, [Point({"F": "22", "W": "0.6u"}, "user")], LocalExecutor(store.root / "sims"), store,
                    pipeline=[stage], limits=FAKE_HOST)
    assert stage.ran == [("22", "g", "ss"), ("22", "tb", "tt"), ("22", "tb", "ss"), ("22", "g", "tt")]
    assert list(o.children) == ["tb/tt", "tb/ss", "g/tt", "g/ss"] and o.status == "ok"


def test_the_plan_says_a_point_may_stop(tmp_path):
    spec, executor = two_by_two(), LocalExecutor(tmp_path)
    assert plan_shape(spec, [Prepared()], "all", executor, None, FAKE_HOST) == (
        "(4 testbench sims) = up to 4 simulations per point (a point stops at the first simulation that fails it) on local, "
        "2 workers × 1 threads (prepared)")
    assert plan_shape(spec, [Prepared()], "all", executor, None, FAKE_HOST, stop_at_first_failure=False) == (
        "(4 testbench sims) = 4 simulations per point on local, 2 workers × 1 threads (prepared)")


def test_optimize_hands_the_schedule_the_rows_it_adopted(tmp_path, monkeypatch):
    """Section 3: the rows ``opt.optimize`` adopted from ``initial=`` count in the history the order is learned from."""
    spec = two_by_two()
    seen: list[list[Observation]] = []
    learn = Schedule.from_history
    monkeypatch.setattr(Schedule, "from_history", classmethod(lambda cls, spec, rows, children, scope:
                                                              seen.append(list(rows)) or learn(spec, rows, children, scope)))
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
    before = golden()                                                           # GOLDEN: written before the field existed
    default = golden(simulator={**GOLDEN["simulator"], "stop_at_first_failure": True})
    assert before.simulator.stop_at_first_failure and "stop_at_first_failure" not in before.model_dump(mode="json")["simulator"]
    assert default.model_dump(mode="json") == before.model_dump(mode="json")
    assert (default.fingerprint(), default._legacy_fingerprint()) == (before.fingerprint(), before._legacy_fingerprint()) == (
        "15d08ed68b2dfd7c", "87ca2259471db3ad")                                  # test_engine's pinned values
    off = golden(simulator={**GOLDEN["simulator"], "stop_at_first_failure": False})
    assert off.fingerprint() == before.fingerprint() and off.model_dump(mode="json")["simulator"]["stop_at_first_failure"] is False


# -- 7. the digest and the report ---------------------------------------------------------------------------------------------


def test_the_digest_and_the_report_read_stopped_points(tmp_path):
    spec = two_by_two()
    store, _, obs = run_batch(tmp_path, spec)
    analyze.digest(spec, obs, store)
    d = json.loads((store.reports_dir() / "digest.json").read_text(encoding="utf-8"))
    counts = d["counts"]
    assert (counts["points"], counts["simulations"], counts["stopped_early"], counts["simulations_not_run"]) == (5, 14, 3, 6)
    assert counts["by_status"] == {"constraint_failed": 3, "failed:spectre": 1, "ok": 1}
    assert d["failures"]["messages"] == [{"text": "g/tt: spectre exited 1", "count": 1}]     # not the "not simulated" line
    md = (store.reports_dir() / "digest.md").read_text(encoding="utf-8")
    assert "· 14 simulations · 3 stopped early (6 simulations not run)" in md

    report = analyze.report(spec, obs, store).read_text(encoding="utf-8")
    # a stopped point counts at the corner it stopped at (20 and 24 at tt, 26 at ss); 28 ran whole and fails at ss
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
