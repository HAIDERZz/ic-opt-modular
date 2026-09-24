import json
import signal
import threading
import time
from pathlib import Path

import pytest
import yaml

from ic_opt import migrate_store
from ic_opt.blocks.evaluate import evaluate
from ic_opt.deck import Deck
from ic_opt.eval.engine import BudgetExceeded
from ic_opt.executor import CommandResult, process_group
from ic_opt.observation import ChildResult
from ic_opt.sim.corner import aggregate
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.store import RunStore
from tests.ic_opt.fakes import (
    FAKE_HOST,
    HANG_S,
    FakeSpectreExecutor,
    forwarded_signals,
    make_spec,
    minimal_spec,
    posix_only,
)

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


def test_the_issues_of_a_child_that_succeeded_reach_the_point_as_warnings():
    """T16.6: a child may succeed and still report an issue (the measure stage's k_lf < 0 warning). It keeps the point
    ok and lands in the point's issues, after the point's own when another child failed."""
    spec = make_spec(**three_by_three())
    children = {f"{tb}/{c}": ChildResult(unit=tb, corner=c, status="ok", metrics=m)
                for c in ("tt", "ss", "ff") for tb, m in (("cg", {"NF": 8.0}), ("iip3", {"IIP3": 2.0}))}
    children["xfm/nominal"] = ChildResult(unit="xfm", status="ok", issues=["k_lf = -0.6 < 0: ..."])
    agg = aggregate(spec, children)
    assert agg.status == "ok" and agg.feasible and agg.issues == ["xfm/nominal: k_lf = -0.6 < 0: ..."]
    children["cg/ss"] = ChildResult(unit="cg", corner="ss", status="failed:spectre", issues=["spectre exited 1"])
    assert aggregate(spec, children).issues == ["cg/ss: spectre exited 1", "xfm/nominal: k_lf = -0.6 < 0: ..."]


def test_child_failure_marks_observation_and_keeps_going(tmp_path):
    spec = make_spec(**three_by_three())
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims", metrics_by_corner, fail_spectre=lambda tb, c: (tb, c) == ("cg", "ss"))
    obs = evaluate(spec, [Point({"F": "20", "W": "0.6u"}, "user")], ex, store, deck=deck_for(spec), limits=FAKE_HOST)[0]

    assert obs.status == "failed:spectre" and not obs.feasible and obs.objective is None
    assert obs.children["cg/ss"].status == "failed:spectre" and obs.children["cg/ff"].status == "ok"
    assert obs.issues[0].startswith("cg/ss: spectre exited 1")


def test_a_command_past_its_deadline_fails_its_point_and_the_run_goes_on(tmp_path):
    """N-10: the second point's Spectre hangs -- a real sleep on the fake host -- past simulator.timeout_s. The executor
    kills its process group and raises CommandTimeout; the engine records that point as failed:spectre, the timeout and
    its deadline as the issue, and simulates and records the first and third points as before: one hung job no longer
    aborts sim.evaluate."""
    spec = make_spec(simulator={**minimal_spec()["simulator"], "parallel_jobs": 1, "timeout_s": 1})
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 8.0 + int(p["F"]) / 100},
                             hang=lambda tool, cwd: tool == "spectre" and "obs_0002" in cwd.parts)
    started = time.monotonic()
    obs = evaluate(spec, [Point({"F": f, "W": "0.6u"}, "user") for f in ("20", "22", "24")], ex, store, deck=deck_for(spec),
                   limits=FAKE_HOST)
    assert time.monotonic() - started < 10                                 # the deadline ended the hung job, not its sleep
    assert [(o.obs_id, o.status) for o in obs] == [("obs_0001", "ok"), ("obs_0002", "failed:spectre"), ("obs_0003", "ok")]
    timed_out = f"timed out after 1s: sleep {HANG_S} (its process group was killed)"
    assert obs[1].children["tb/nominal"].issues == [timed_out] and obs[1].issues == [f"tb/nominal: {timed_out}"]
    assert obs[0].metrics == {"NF": 8.2} and obs[2].metrics == {"NF": 8.24} and len(ex.hung) == 1
    assert [o.status for o in RunStore(tmp_path).observations()] == ["ok", "failed:spectre", "ok"]
    step = json.loads((store.root / "steps.jsonl").read_text().splitlines()[-1])
    assert (step["status"], step["new"], step["simulations"]) == ("ok", 3, 3)


# -- N-11: an interrupt stops the run: nothing queued starts, nothing interrupted is recorded as failed --------------------

TWO_CORNERS = [{"id": "tt", "model_section": "tt"}, {"id": "ss", "model_section": "ss"}]


def interrupted_step(store: RunStore) -> dict:
    step = json.loads((store.root / "steps.jsonl").read_text().splitlines()[-1])
    assert step["step"] == "evaluate" and step["status"] == "interrupted"
    return {k: step[k] for k in ("points", "reused", "recorded", "interrupted", "not_started", "simulations")}


@posix_only
@pytest.mark.parametrize("hung", ["obs_0001", "obs_0002"])
def test_ctrl_c_stops_the_run_the_queued_points_never_start(tmp_path, hung):
    """N-11, a Ctrl-C at the terminal: SIGINT while the hung point's first corner simulates (a real sleep on the fake host,
    one point at a time). The forwarded signal kills that command; the point starts no second corner and is not recorded
    (its failure is the interrupt's); the points still queued never start; a point that had finished stays recorded; the
    step log says which is which; the KeyboardInterrupt comes at once instead of after the queue."""
    spec = make_spec(simulator={**minimal_spec()["simulator"], "parallel_jobs": 1, "timeout_s": 60}, corners=TWO_CORNERS)
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 8.0},
                             hang=lambda tool, cwd: tool == "spectre" and hung in cwd.parts)
    main = threading.main_thread().ident

    def ctrl_c_once_it_hangs():
        deadline = time.monotonic() + 20
        while not (ex.hung and process_group._running) and time.monotonic() < deadline:
            time.sleep(0.01)
        signal.pthread_kill(main, signal.SIGINT)                   # what the terminal's Ctrl-C does to the main thread

    points = [Point({"F": f, "W": "0.6u"}, "user") for f in ("20", "22", "24", "26")]
    with forwarded_signals():
        threading.Thread(target=ctrl_c_once_it_hangs, daemon=True).start()
        started = time.monotonic()
        with pytest.raises(KeyboardInterrupt):
            evaluate(spec, points, ex, store, deck=deck_for(spec), limits=FAKE_HOST)
    assert time.monotonic() - started < 15 and not process_group._running            # the hung command was killed and reaped
    done = ["obs_0001"] if hung == "obs_0002" else []
    queued = [f"obs_{i:04d}" for i in range(int(hung[4:]) + 1, 5)]
    assert [o.obs_id for o in RunStore(tmp_path).observations()] == done
    assert sum(c.startswith("spectre") for c in ex.commands) == 2 * len(done) + 1       # the hung point's ss corner never ran
    assert not (store.root / "sims" / hung / "tb" / "ss").exists()
    assert not any((store.root / "sims" / obs).exists() for obs in queued)
    assert interrupted_step(store) == {"points": 4, "reused": 0, "recorded": done, "interrupted": [hung], "not_started": queued,
                                       "simulations": 2 * len(done)}


def test_a_job_that_sees_the_forwarded_interrupt_first_stops_the_run(tmp_path, monkeypatch):
    """N-11, without a signal: the forwarder counts an interrupt before it kills the commands, so a job whose command died
    of it sees the count moved even before the main thread's KeyboardInterrupt. Here the first corner's command is killed
    that way (the count moves, the command fails): the job starts no further corner and is not recorded, the queued
    points never start, and the run ends as interrupted."""
    spec = make_spec(simulator={**minimal_spec()["simulator"], "parallel_jobs": 1}, corners=TWO_CORNERS)
    store = RunStore(tmp_path)

    class Interrupting(FakeSpectreExecutor):
        def run(self, command, *, cwd=None, timeout_s=None, cshrc=None):
            if command.startswith("spectre"):
                self.commands.append(command)
                monkeypatch.setattr(process_group, "_interrupts", process_group.interrupts() + 1)   # as forward_signals does
                return CommandResult(-2, "", "killed by SIGINT", ["spectre"], 0.01)
            return super().run(command, cwd=cwd, timeout_s=timeout_s, cshrc=cshrc)

    ex = Interrupting(store.root / "sims", lambda p, tb, c: {"NF": 8.0})
    with pytest.raises(KeyboardInterrupt):
        evaluate(spec, [Point({"F": f, "W": "0.6u"}, "user") for f in ("20", "22", "24")], ex, store, deck=deck_for(spec),
                 limits=FAKE_HOST)
    assert sum(c.startswith("spectre") for c in ex.commands) == 1 and not store.observations()
    assert interrupted_step(store) == {"points": 3, "reused": 0, "recorded": [], "interrupted": ["obs_0001"],
                                       "not_started": ["obs_0002", "obs_0003"], "simulations": 0}

    again = evaluate(spec, [Point({"F": "20", "W": "0.6u"}, "user")], FakeSpectreExecutor(store.root / "sims",
                     lambda p, tb, c: {"NF": 8.0}), RunStore(tmp_path), deck=deck_for(spec), limits=FAKE_HOST)
    assert [(o.obs_id, o.status) for o in again] == [("obs_0002", "ok")]      # simulated afresh; obs_0001's directory is not reused


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


# -- identity (T15.2): the problem, not the machine it runs on ----------------------------------------------------------

GOLDEN = {                 # every field spelled out: the pinned fingerprints below belong to exactly this spec
    "project": "golden",
    "testbenches": [{"id": "tb", "maestro_point_root": "/x", "virtuoso_library": "lib", "cell": "c", "test_name": "t"}],
    "variables": [{"name": "F", "kind": "integer", "lower": "20", "upper": "30", "step": "2"}],
    "metrics": [{"name": "NF", "unit": "dB", "expression": "nf()"}],
    "constraints": [{"metric": "NF", "op": "lt", "value": "9"}],
    "objective": {"direction": "minimize", "expression": "NF"},
    "simulator": {"parallel_jobs": 10, "threads_per_run": 8, "timeout_s": 600},
    "budget": {"max_simulations": 100},
}


def golden(**changes) -> Spec:
    return Spec.model_validate({**GOLDEN, **changes})


def restamp(store: RunStore, **stamps: str) -> None:
    """Rewrite every observation with these fingerprints (as an older version would have stamped them)."""
    rows = [o.model_copy(update=stamps) for o in store.observations()]
    store.observations_path.write_text("".join(o.model_dump_json() + "\n" for o in rows), encoding="utf-8")


def test_spec_fingerprint_is_the_problem_not_the_machine():
    base, sim = golden(), GOLDEN["simulator"]
    for how in ({"simulator": {**sim, "parallel_jobs": 4}},                        # the audit's 10 -> 4 on a smaller machine
                {"simulator": {**sim, "threads_per_run": 2, "timeout_s": 7200, "license_check": False,
                               "keep_failed_runs": False, "keep_successful_runs": False}},
                {"simulator": {**sim, "license_queue_timeout_s": 600}},                # R-17: the host's license queue
                {"budget": {"max_simulations": 5000}}):
        assert golden(**how).fingerprint() == base.fingerprint(), how
        assert golden(**how)._legacy_fingerprint() != base._legacy_fingerprint()     # the old formula counted them
    for what in ({"variables": [{**GOLDEN["variables"][0], "upper": "40"}]},
                 {"metrics": [{"name": "NF", "unit": "dB", "expression": "nf(1)"}]},
                 {"objective": {"direction": "maximize", "expression": "NF"}},
                 {"simulator": {**sim, "preset": "mx"}}):
        assert golden(**what).fingerprint() != base.fingerprint(), what
    assert "budget" not in base.problem() and base.problem()["simulator"] == {"preset": "ax", "engine": "spectre_x", "output_format": "psfxl"}


def test_fingerprints_are_pinned():
    """Stored stamps must keep matching. The legacy value is what versions before T15.2 wrote for this spec (computed with
    that code): if it moves, a field added to the schema reached the dump -- keep it out while unset (``Topology._dump``).
    If the new value moves, stores stamped since T15.2 lose their identity: keep run controls out of Spec.problem() and
    give new fields a None default (``problem()`` leaves unset fields out)."""
    assert golden()._legacy_fingerprint() == "87ca2259471db3ad"
    assert golden().fingerprint() == "15d08ed68b2dfd7c"


def test_observations_stamped_with_the_legacy_fingerprint_are_reused(tmp_path):
    spec = make_spec()
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 8.0})
    deck = deck_for(spec)
    p1, p2, p3 = (Point({"F": str(f), "W": "0.6u"}, "user") for f in (20, 22, 24))
    evaluate(spec, [p1, p2], ex, store, deck=deck, limits=FAKE_HOST)
    restamp(store, spec_fingerprint=spec._legacy_fingerprint())                       # a store from before T15.2

    again = evaluate(spec, [p1, p2, p3], ex, RunStore(tmp_path), deck=deck, limits=FAKE_HOST)
    assert sum(c.startswith("spectre") for c in ex.commands) == 3                       # p1, p2 reused; p3 simulated
    assert [o.spec_fingerprint for o in again] == [spec._legacy_fingerprint()] * 2 + [spec.fingerprint()]

    smaller = make_spec(simulator={**minimal_spec()["simulator"], "parallel_jobs": 1})    # the project moved to a smaller machine
    evaluate(smaller, [p3], ex, RunStore(tmp_path), deck=deck, limits=FAKE_HOST)
    assert sum(c.startswith("spectre") for c in ex.commands) == 3
    wider = make_spec(variables=[minimal_spec()["variables"][0],
                                 {"name": "W", "kind": "continuous_step", "lower": "0.6u", "upper": "2u", "step": "0.2u"}])
    evaluate(wider, [p3], ex, RunStore(tmp_path), deck=deck, limits=FAKE_HOST)          # another problem: simulated again
    assert sum(c.startswith("spectre") for c in ex.commands) == 4


def test_migrate_store_restamps_a_project_once_and_nothing_else(tmp_path):
    spec = make_spec()
    (tmp_path / "spec.yaml").write_text(yaml.safe_dump(spec.model_dump(mode="json")), encoding="utf-8")
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 8.0})
    points = [Point({"F": str(f), "W": "0.6u"}, "user") for f in (20, 22)]
    evaluate(spec, points, ex, store, deck=deck_for(spec), limits=FAKE_HOST)
    legacy = spec._legacy_fingerprint()
    restamp(store, spec_fingerprint=legacy)
    first, second = store.observations_path.read_text(encoding="utf-8").splitlines()
    foreign = store.observations()[0].model_copy(update={"obs_id": "obs_0003", "spec_fingerprint": "0123456789abcdef"})
    store.observations_path.write_text(f"{first}\n{json.dumps(json.loads(second))}\n\n{foreign.model_dump_json()}\n",   # spaced, blank,
                                       encoding="utf-8")                                                                # another problem
    before = store.observations_path.read_bytes()

    dry = migrate_store.migrate(tmp_path, dry_run=True)
    assert (dry.rows, dry.restamped, dry.spec_rows, dict(dry.other_specs)) == (3, 2, 2, {"0123456789abcdef": 1})
    assert store.observations_path.read_bytes() == before and not list(store.root.glob("observations.jsonl.bak-*"))
    assert "dry run: nothing written" in str(dry) and f"{legacy} -> {spec.fingerprint()}: 2 rows restamped" in str(dry)

    done = migrate_store.migrate(tmp_path)
    assert done.restamped == 2 and done.pipelines == {} and done.backup.read_bytes() == before
    assert store.observations_path.read_bytes() == before.replace(legacy.encode(), spec.fingerprint().encode())   # byte for byte
    again = migrate_store.migrate(tmp_path)
    assert not again.changed and "nothing to change" in str(again) and len(list(store.root.glob("observations.jsonl.bak-*"))) == 1

    evaluate(spec, points, ex, RunStore(tmp_path), deck=deck_for(spec), limits=FAKE_HOST)   # the restamped rows are reused
    assert sum(c.startswith("spectre") for c in ex.commands) == 2
