"""T17.9, step 2 of the evaluation schedule (``docs/refactor/T17_9_MULTI_CORNER_SPEC.md``, section 6): the stop's default
follows the run's corners, and ``metric_gp`` takes a run at several corners, each metric at its worst."""

from __future__ import annotations

import pytest

from ic_opt import digest
from ic_opt.blocks.evaluate import evaluate, plan_shape
from ic_opt.blocks.optimize import optimize
from ic_opt.executor import LocalExecutor
from ic_opt.observation import ChildResult, Observation, Observations
from ic_opt.recipe import PLAN_MODE
from ic_opt.sim.corner import aggregate, stopper, worst_metrics
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.store import RunStore
from ic_opt.suggesters import metric_gp as mg
from ic_opt.suggesters import resolve_auto
from ic_opt.suggesters.metric_gp import MetricGpSuggester
from tests.ic_opt.fakes import FAKE_HOST, minimal_spec
from tests.ic_opt.test_metric_gp import tt_history, tt_spec
from tests.ic_opt.test_optimize import devices_spec
from tests.ic_opt.test_schedule import EVERYWHERE, Prepared, at, child, point, run_batch, two_by_two


def switched(value: bool | None):
    """``two_by_two`` with ``simulator.stop_at_first_failure`` as given; None: a spec that does not name it."""
    if value is None:
        return two_by_two()
    return two_by_two(simulator={**minimal_spec()["simulator"], "stop_at_first_failure": value})


# -- 1. the default ------------------------------------------------------------------------------------------------------------


# (the spec's switch, the run's corners, the recipe's override, the points stopped): at both corners F=20, 24 and 26 stop
# (test_schedule's ``prepared``), at tt only F=20 (F=24 fails at its last child there, F=26 fails at ss). Two testbenches
# at two corners are 4 simulations per point: below STOP_FROM_SIMULATIONS, so the default is off at both corners too
# (T17.9 revision 2); the spec's switch and the recipe's override decide as before.
CASES = [(None, "all", None, 0), (None, ["tt"], None, 0), (True, ["tt"], None, 1), (True, "all", None, 3), (False, "all", None, 0),
         (None, "all", True, 3), (None, ["tt"], True, 1), (False, "all", True, 3), (True, ["tt"], False, 0)]


@pytest.mark.parametrize(("value", "corners", "override", "stopped"), CASES)
def test_the_stop_is_off_below_twenty_simulations_per_point_unless_the_spec_or_the_recipe_says(tmp_path, value, corners,
                                                                                             override, stopped):
    spec = switched(value)
    kwargs = {} if override is None else {"stop_at_first_failure": override}
    _, stage, obs = run_batch(tmp_path, spec, corners=corners, **kwargs)
    assert sum(1 for o in obs if o.not_run) == stopped
    children = 4 if corners == "all" else 2
    assert len(stage.ran) == 5 * children - sum(len(o.not_run) for o in obs)
    line = plan_shape(spec, [Prepared()], corners, LocalExecutor(tmp_path), None, FAKE_HOST, override)
    assert line.startswith(f"({children} testbench sims) = up to {children} simulations per point (a point stops at the "
                           "first simulation that fails it) on local" if stopped else
                           f"({children} testbench sims) = {children} simulations per point on local")


def ten_corners() -> Spec:
    """``two_by_two`` at ten corners (tt, ss and eight more that ``prepared`` answers with passing values): 20 simulations
    per point, the smallest number at which the stop is on by default."""
    return two_by_two(corners=[{"id": "tt"}, {"id": "ss"}] + [{"id": f"c{i}"} for i in range(3, 11)])


def test_the_stop_is_on_from_twenty_simulations_per_point(tmp_path):
    spec = ten_corners()
    _, stage, obs = run_batch(tmp_path, spec, corners="all")
    # F=20 stops at tb/tt (the first child), F=26 at tb/ss (the second), F=24 at g/tt and F=28 at g/ss (the g children
    # come after the ten tb children); F=22 runs its 20 simulations and is feasible
    assert sum(1 for o in obs if o.not_run) == 4
    assert len(stage.ran) == 5 * 20 - sum(len(o.not_run) for o in obs)
    line = plan_shape(spec, [Prepared()], "all", LocalExecutor(tmp_path), None, FAKE_HOST, None)
    assert line.startswith("(20 testbench sims) = up to 20 simulations per point (a point stops at the first simulation that fails it)")
    # nine corners are 18 simulations per point: off again
    nine = [c.id for c in spec.corners][:9]
    _, stage, obs = run_batch(tmp_path / "nine", spec, corners=nine)
    assert sum(1 for o in obs if o.not_run) == 0 and len(stage.ran) == 5 * 18
    assert plan_shape(spec, [Prepared()], nine, LocalExecutor(tmp_path), None, FAKE_HOST, None).startswith(
        "(18 testbench sims) = 18 simulations per point on local")


# -- 2. the dump ---------------------------------------------------------------------------------------------------------------


def test_the_switch_is_written_only_when_set_and_is_never_the_problem():
    unnamed = two_by_two()
    for value in (None, True, False):
        spec = switched(value)
        simulator = spec.model_dump(mode="json")["simulator"]
        assert ("stop_at_first_failure" in simulator) is (value is not None), value
        assert simulator.get("stop_at_first_failure") is value and spec.fingerprint() == unnamed.fingerprint()
    assert switched(None).model_dump(mode="json") == unnamed.model_dump(mode="json")
    assert unnamed.simulator.stop_at_first_failure is None


# -- 3. worst_metrics ------------------------------------------------------------------------------------------------------


def three_corners(**policy) -> Spec:
    """One testbench at tt, ss and ff: G > 1 (a lower bound), NF < 9 (an upper one), 0.5 <= V <= 1.5 (both), and P named
    by the objective alone: minimize NF - G + P."""
    return Spec.model_validate(minimal_spec(
        corners=[{"id": c} for c in ("tt", "ss", "ff")], corner_policy=policy,
        metrics=[{"name": name, "unit": "1", "expression": f"{name.lower()}()"} for name in ("G", "NF", "V", "P")],
        constraints=[{"metric": "G", "op": "gt", "value": "1"}, {"metric": "NF", "op": "lt", "value": "9"},
                     {"metric": "V", "op": "ge", "value": "0.5"}, {"metric": "V", "op": "le", "value": "1.5"}],
        objective={"direction": "minimize", "expression": "NF - G + P"}))


def recorded(spec: Spec, ran: dict[str, dict[str, float]], wanted: list[str] | None = None) -> Observation:
    """What the engine records for a point whose testbench gave ``ran[corner]`` at each corner that ran, of ``wanted``."""
    children = {f"tb/{corner}": ChildResult(unit="tb", corner=corner, status="ok", metrics=metrics)
                for corner, metrics in ran.items()}
    agg = aggregate(spec, children, wanted)
    return Observation(obs_id="obs_0001", params={"F": "20", "W": "0.6u"}, origin="user", children=children,
                       not_run=[key for key in wanted or () if key not in children], metrics=agg.metrics, fom=agg.fom,
                       objective=agg.objective, feasible=agg.feasible, constraint_penalty=agg.constraint_penalty,
                       status=agg.status, issues=agg.issues, spec_fingerprint=spec.fingerprint(), pipeline_fingerprint="p",
                       started_at="t", finished_at="t")


TT = {"G": 3.0, "NF": 7.0, "V": 1.0, "P": 1.0}          # objective 5
SS = {"G": 1.5, "NF": 8.5, "V": 1.45, "P": 0.5}         # objective 7.5; V's margin 0.05, to its upper bound
FF = {"G": 2.0, "NF": 7.5, "V": 0.6, "P": 2.5}          # objective 8, the worst; V's margin 0.1, to its lower bound


def test_each_metric_at_its_worst_over_the_corners_the_point_ran():
    spec = three_corners()
    whole = recorded(spec, {"tt": TT, "ss": SS, "ff": FF})
    assert whole.feasible and whole.metrics == FF                        # the point's own: the worst objective's corner
    assert worst_metrics(spec, whole) == {"G": 1.5, "NF": 8.5, "V": 1.45, "P": 2.5}
    for c in spec.constraints[:2]:                                       # a bound of one kind: the digest's value
        assert worst_metrics(spec, whole)[c.metric] == digest.constraint_value(spec, c, whole)
    assert worst_metrics(three_corners(objective="nominal"), whole)["P"] == 1.0    # the objective's corner: the nominal one

    # two of three corners ran: the worst of those two; P where the objective of the two is worst (tt: 8.8 > 8.5)
    stopped = recorded(spec, {"tt": {**TT, "G": 1.2, "P": 3.0}, "ss": {**SS, "NF": 9.5}}, ["tb/tt", "tb/ss", "tb/ff"])
    assert stopped.status == "constraint_failed" and stopped.not_run == ["tb/ff"] and stopped.metrics == {**SS, "NF": 9.5}
    assert worst_metrics(spec, stopped) == {"G": 1.2, "NF": 9.5, "V": 1.45, "P": 3.0}
    lost = recorded(spec, {"tt": TT, "ss": {k: v for k, v in SS.items() if k != "NF"}}, ["tb/tt", "tb/ss"])
    assert lost.status == "metric_failed" and worst_metrics(spec, lost) == {"G": 1.5, "NF": 7.0, "V": 1.45, "P": 1.0}

    one = recorded(spec, {"tt": TT}, ["tb/tt"])                          # evaluated at one corner: its own metrics
    assert worst_metrics(spec, one) == one.metrics == TT
    rows = tt_history(tt_spec())                                         # a stopped point, a failed g, a lost G among them
    assert all(worst_metrics(tt_spec(), o) == o.metrics for o in rows) and rows[6].metrics == {}


# -- 4. metric_gp at several corners -------------------------------------------------------------------------------------------


def graded(params: dict[str, str], unit: str, corner: str | None) -> ChildResult:
    """NF grows with F and W and is 1 dB worse at ss; G falls with F and is 0.4 dB worse at tt: large F fails NF < 9,
    at ss first. A feasible point's own metrics are ss's (its objective NF - G is worst there), its worst G is tt's."""
    f, w = int(params["F"]), float(params["W"].rstrip("u"))
    if unit == "tb":
        return ChildResult(unit=unit, corner=corner, status="ok", metrics={"NF": 6.0 + (f - 20) / 4 + w + (corner == "ss")})
    return ChildResult(unit=unit, corner=corner, status="ok", metrics={"G": 1.5 + (26 - f) / 10 - 0.4 * (corner == "tt")})


def test_metric_gp_takes_a_history_at_two_corners_with_stopped_points(tmp_path, monkeypatch):
    """Item 4: the history built through the engine, some points stopped early; the models are given each metric at its
    worst, and the same history and seed give the same proposal."""
    spec = two_by_two()
    store = RunStore(tmp_path)
    points = [Point({"F": f, "W": w}, "user") for f, w in zip(("20", "22", "24", "26", "28", "30") * 2,
                                                                ("0.6u",) * 3 + ("0.8u",) * 3 + ("1u",) * 3 + ("1.2u",) * 3,
                                                                strict=True)]
    rows = Observations(evaluate(spec, points, LocalExecutor(store.root / "sims"), store, pipeline=[Prepared(graded)], stop_at_first_failure=True,
                                 limits=FAKE_HOST))
    assert any(o.not_run for o in rows) and any(o.feasible for o in rows) and all(o.corners() == {"tt", "ss"} for o in rows)
    viewed = [worst_metrics(spec, o) for o in rows]
    assert all(m["G"] < o.metrics["G"] for m, o in zip(viewed, rows, strict=True) if o.feasible)    # tt's, not ss's
    given: dict[str, list[float]] = {}
    fit = mg.fit_metric

    def spy(name, x, y, rng):
        given[name] = y.tolist()
        return fit(name, x, y, rng)

    monkeypatch.setattr(mg, "fit_metric", spy)
    first = MetricGpSuggester(initial_trials=4).propose(spec, rows, 3, seed=5)
    assert given == {name: [m[name] for m in viewed if name in m] for name in ("NF", "G")}
    again = MetricGpSuggester(initial_trials=4).propose(spec, Observations(list(rows)), 3, seed=5)
    assert (first.raw, first.tags) == (again.raw, again.tags) and first.tags == ["grid:12"] * 3


def test_auto_is_metric_gp_at_any_corners_and_optimize_runs_it_there(tmp_path, capsys):
    spec = two_by_two()
    assert resolve_auto(spec, len(spec.corner_ids)) == ("metric_gp", "no EM devices")
    assert resolve_auto(devices_spec(), 1) == ("openbox_gp_eic", "metric_gp does not take EM devices yet")
    for strategy in ("metric_gp", "auto"):
        token = PLAN_MODE.set(True)
        try:
            optimize(spec, LocalExecutor(tmp_path / "sims"), RunStore(tmp_path / f"plan_{strategy}"),
                     pipeline=[Prepared(graded)], strategy=strategy, budget=8, batch=4, corners="all", current=False,
                     limits=FAKE_HOST)
        finally:
            PLAN_MODE.reset(token)
        out = capsys.readouterr().out
        assert ("[plan] opt.optimize step='optimize' strategy=metric_gp: 0/8 points done, up to 8 more in batches of 4 × "
                "(4 testbench sims) = 4 simulations per point"      # 4 per point: the stop is off by default (T17.9 revision 2)
                ) in out
        assert ("[plan] strategy auto: metric_gp (no EM devices)" in out) is (strategy == "auto")
    store = RunStore(tmp_path / "run")
    obs = optimize(spec, LocalExecutor(store.root / "sims"), store, pipeline=[Prepared(graded)], strategy="metric_gp",
                   budget=8, batch=4, corners="all", current=False, seed=1, limits=FAKE_HOST)
    assert len(obs) == 8 and all(o.corners() == {"tt", "ss"} for o in obs)
    assert [o.origin for o in obs] == ["suggest:metric_gp:init"] * 4 + ["suggest:metric_gp:grid:4"] * 4


# -- 6. where points stopped -----------------------------------------------------------------------------------------------------


def test_the_digest_says_after_which_child_points_stopped(tmp_path):
    spec = two_by_two()
    _, _, obs = run_batch(tmp_path / "on", spec, stop_at_first_failure=True)   # F=20 stops after tb/tt, F=24 after g/tt, F=26 after tb/ss
    stopped = [o for o in obs if o.not_run]
    assert [stopper(spec, o) for o in stopped] == ["tb/tt", "g/tt", "tb/ss"]
    assert all(f"(stopped after {stopper(spec, o)})" in o.issues[-1] for o in stopped)   # the child its line names
    assert all(stopper(spec, o) is None for o in obs if not o.not_run)
    d = digest.digest(spec, obs)
    assert d["counts"]["stopped_at"] == {"g/tt": 1, "tb/ss": 1, "tb/tt": 1}
    assert "\n\nstopped early: 3 points; at g/tt: 1, tb/ss: 1, tb/tt: 1\n\n## What is optimized" in digest.markdown(d)

    _, _, whole = run_batch(tmp_path / "off", spec, stop_at_first_failure=False)
    d = digest.digest(spec, whole)
    assert d["counts"]["stopped_at"] == {} and "stopped early" not in digest.markdown(d)

    rows = [point(spec, 0, at("20"), child("tb", "tt", NF=8.0), child("tb", "ss", NF=9.5), wanted=EVERYWHERE),
            point(spec, 1, at("22"), child("tb", "tt", NF=8.0), child("tb", "ss", NF=9.6), wanted=EVERYWHERE),
            point(spec, 2, at("24"), child("tb", "tt", NF=9.5), wanted=EVERYWHERE),
            point(spec, 3, at("26"), child("tb", "tt", NF=8.0), child("tb", "ss", NF=8.0), child("g", "tt", G=0.5),
                  wanted=EVERYWHERE),
            point(spec, 4, at("28"), child("g", "ss", G=0.5), wanted=EVERYWHERE)]     # g/ss ran first (a learned order)
    counts = digest.digest(spec, rows)["counts"]
    assert counts["stopped_at"] == {"tb/ss": 2, "g/ss": 1, "g/tt": 1, "tb/tt": 1}
    assert "stopped early: 5 points; at tb/ss: 2, g/ss: 1, g/tt: 1, ..." in digest.markdown(digest.digest(spec, rows))
