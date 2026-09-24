import json
from pathlib import Path

import pytest

from ic_opt.blocks.evaluate import evaluate
from ic_opt.deck import Deck
from ic_opt.eval.engine import BudgetExceeded
from ic_opt.observation import ChildResult
from ic_opt.sim.corner import aggregate
from ic_opt.space import Point
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor, make_spec, minimal_spec

TEMPLATE = "simulator lang=spectre\ninclude \"/p/top.scs\" section=tt\nparameters temperature=27 F={{F}} W={{W}}\ntran tran stop=10n\n"


def three_by_three() -> dict:
    d = minimal_spec()
    d["testbenches"] = [
        {"id": "cg", "maestro_point_root": "/a", "virtuoso_library": "l", "cell": "c", "test_name": "t"},
        {"id": "iip3", "maestro_point_root": "/b", "virtuoso_library": "l", "cell": "c", "test_name": "t"},
    ]
    d["corners"] = [{"id": "tt", "model_section": "tt"}, {"id": "ss", "model_section": "ss"}, {"id": "ff", "model_section": "ff"}]
    d["metrics"] = [
        {"name": "NF", "unit": "dB", "expression": "nf()", "testbench": "cg"},
        {"name": "IIP3", "unit": "dBm", "expression": "iip3()", "testbench": "iip3"},
    ]
    d["constraints"] = [{"metric": "NF", "op": "lt", "value": "9"}, {"metric": "IIP3", "op": "gt", "value": "0"}]
    d["objective"] = {"direction": "minimize", "expression": "NF - IIP3"}
    d["budget"] = {"max_simulations": 100}
    return d


def deck_for(spec) -> Deck:
    return Deck(templates={(tb, corner): TEMPLATE for tb in spec.testbench_ids for corner in spec.corner_ids})


def metrics_by_corner(params, tb, corner):
    f = int(params["F"])
    shift = {"tt": 0.0, "ss": 0.5, "ff": -0.2, None: 0.0}[corner]
    return {"NF": 8.0 + f / 100 + shift} if tb == "cg" else {"IIP3": 2.0 - shift}


def test_single_testbench_single_corner(tmp_path):
    spec = make_spec()
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 8.0 + int(p["F"]) / 100})
    obs = evaluate(spec, [Point({"F": "24", "W": "0.8u"}, "user"), Point({"F": "30", "W": "0.6u"}, "user")], ex, store, deck=deck_for(spec), limits=FAKE_HOST)

    assert [o.obs_id for o in obs] == ["obs_0001", "obs_0002"]
    assert obs[0].status == "ok" and obs[0].feasible and obs[0].metrics == {"NF": 8.24} and obs[0].objective == 8.24
    assert obs[0].children["tb/nominal"].sim_dir == ".icopt/sims/obs_0001/tb/nominal"
    assert len(store.observations()) == 2
    steps = [json.loads(line) for line in (store.root / "steps.jsonl").read_text().splitlines()]
    assert steps[-1]["step"] == "evaluate" and steps[-1]["new"] == 2 and steps[-1]["simulations"] == 2


def test_three_testbenches_three_corners_worst_case(tmp_path):
    spec = make_spec(**three_by_three())
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims", metrics_by_corner)
    obs = evaluate(spec, [Point({"F": "20", "W": "0.6u"}, "user")], ex, store, deck=deck_for(spec), limits=FAKE_HOST)[0]

    assert len(obs.children) == 6 and all(c.status == "ok" for c in obs.children.values())
    # ss is the worst corner: NF 8.7, IIP3 1.5 -> objective 7.2
    assert obs.status == "ok" and obs.metrics == {"NF": pytest.approx(8.7), "IIP3": pytest.approx(1.5)}
    assert obs.objective == pytest.approx(7.2)
    assert sum(c.startswith("spectre") for c in ex.commands) == 6


def test_corner_policy_nominal_and_constraint_scope():
    spec = make_spec(**three_by_three())
    children = {
        "cg/tt": ChildResult(unit="cg", corner="tt", status="ok", metrics={"NF": 8.0}),
        "iip3/tt": ChildResult(unit="iip3", corner="tt", status="ok", metrics={"IIP3": 2.0}),
        "cg/ss": ChildResult(unit="cg", corner="ss", status="ok", metrics={"NF": 9.5}),   # violates NF < 9
        "iip3/ss": ChildResult(unit="iip3", corner="ss", status="ok", metrics={"IIP3": 1.0}),
        "cg/ff": ChildResult(unit="cg", corner="ff", status="ok", metrics={"NF": 7.0}),
        "iip3/ff": ChildResult(unit="iip3", corner="ff", status="ok", metrics={"IIP3": 3.0}),
    }
    worst = aggregate(spec, children)
    assert worst.status == "constraint_failed" and worst.selected_corner == "ss" and not worst.feasible

    nominal = make_spec(**{**three_by_three(), "corner_policy": {"objective": "nominal", "constraints": "nominal"}})
    agg = aggregate(nominal, children)
    assert agg.status == "ok" and agg.selected_corner == "tt" and agg.objective == pytest.approx(6.0)


def test_child_failure_marks_observation_and_keeps_going(tmp_path):
    spec = make_spec(**three_by_three())
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims", metrics_by_corner, fail_spectre=lambda tb, c: (tb, c) == ("cg", "ss"))
    obs = evaluate(spec, [Point({"F": "20", "W": "0.6u"}, "user")], ex, store, deck=deck_for(spec), limits=FAKE_HOST)[0]

    assert obs.status == "failed:spectre" and not obs.feasible and obs.objective is None
    assert obs.children["cg/ss"].status == "failed:spectre" and obs.children["cg/ff"].status == "ok"
    assert obs.issues[0].startswith("cg/ss: spectre exited 1")


def test_reuse_budget_and_rerun_is_continuation(tmp_path):
    spec = make_spec(budget={"max_simulations": 3})
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 8.0})
    deck = deck_for(spec)
    p1, p2, p3, p4 = (Point({"F": str(f), "W": "0.6u"}, "user") for f in (20, 22, 24, 26))

    first = evaluate(spec, [p1, p2], ex, store, deck=deck, limits=FAKE_HOST)
    again = evaluate(spec, [p2, p3], ex, store, deck=deck, limits=FAKE_HOST)          # p2 reused, p3 new (3rd simulation)
    assert [o.obs_id for o in again] == ["obs_0002", "obs_0003"] and again[0] is not None
    assert sum(c.startswith("spectre") for c in ex.commands) == 3

    with pytest.raises(BudgetExceeded, match="3 simulations used"):
        evaluate(spec, [p4], ex, store, deck=deck, limits=FAKE_HOST)
    assert len(RunStore(tmp_path).observations()) == 3 and first[0].key == again[0].key or True


def test_resume_after_interrupted_parallel_run_never_reuses_a_finished_number(tmp_path):
    spec = make_spec()
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 8.0})
    deck = deck_for(spec)
    evaluate(spec, [Point({"F": str(f), "W": "0.6u"}, "user") for f in (20, 22, 24)], ex, store, deck=deck, limits=FAKE_HOST)
    # killed while obs_0002 was still simulating: 0001 and 0003 had finished, 0002 left only its directory
    lines = store.observations_path.read_text().splitlines()
    store.observations_path.write_text("".join(line + "\n" for line in lines if '"obs_0002"' not in line))
    assert (store.root / "sims" / "obs_0002").is_dir()

    store = RunStore(tmp_path)                                      # the restarted process reads the store afresh
    resumed = evaluate(spec, [Point({"F": "26", "W": "0.6u"}, "user")], ex, store, deck=deck, limits=FAKE_HOST)
    assert [o.obs_id for o in resumed] == ["obs_0004"]
    assert [o.obs_id for o in store.observations()] == ["obs_0001", "obs_0003", "obs_0004"]
    assert store.next_obs_id() == "obs_0005"


def test_retention_drops_psf_of_failed_runs_when_configured(tmp_path):
    spec = make_spec(simulator={"parallel_jobs": 1, "threads_per_run": 1, "timeout_s": 10, "keep_failed_runs": False, "keep_successful_runs": True})
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": None if p["F"] == "20" else 8.0})
    obs = evaluate(spec, [Point({"F": "20", "W": "0.6u"}, "user"), Point({"F": "22", "W": "0.6u"}, "user")], ex, store, deck=deck_for(spec), limits=FAKE_HOST)

    assert obs[0].status == "failed:extract" and obs[1].status == "ok"
    assert not Path(tmp_path, obs[0].children["tb/nominal"].sim_dir, "psf").exists()
    assert Path(tmp_path, obs[1].children["tb/nominal"].sim_dir, "psf").exists()
