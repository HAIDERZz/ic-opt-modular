"""T18.2B section 4: a circuit spec with a library device through the recipes -- ``auto`` takes ``metric_gp``, every point
is a valid one, the same seed gives the same points -- the doctor's ``library:<device>`` check and the plan's lines, and
the schedule: the device child, cheap and known at once, runs first and stops a point that fails it."""

from __future__ import annotations

from pathlib import Path

import pytest

from ic_opt import recipe, space
from ic_opt.blocks.doctor import doctor, plan_line
from ic_opt.blocks.evaluate import default_pipeline, evaluate
from ic_opt.blocks.netlist import import_netlists
from ic_opt.eval import engine
from ic_opt.eval.schedule import Schedule
from ic_opt.library import link
from ic_opt.observation import Observations
from ic_opt.recipes import optimize
from ic_opt.site import HostLimits, Site
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.store import RunStore
from ic_opt.suggesters import LIBRARY_REASON, resolve_auto
from ic_opt.suggesters.metric_gp import DEVICES_REFUSAL, MetricGpSuggester, stage_one_refusal
from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor
from tests.ic_opt.library_device_fixtures import (
    NETLIST,
    library_device,
    library_spec_dict,
    xfm_library,
)
from tests.ic_opt.test_blocks import maestro_export

SITE = Site({"local": HostLimits(max_threads=16, max_memory_gb=64)})
EM = {"process_file": "/site/demo.proc", "frequencies": {"start_hz": 0, "stop_hz": 60e9, "step_hz": 1e9}, "threads": 64,
      "memory_gb": 1000, "timeout_s": 600}                       # would not fit the host: the envelope must not count it


@pytest.fixture(autouse=True)
def fresh():
    link.clear()
    yield
    link.clear()


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    return xfm_library(tmp_path_factory.mktemp("run") / "lib")


def spec_at(root: Path, where: Path, **overrides) -> Spec:
    """The library circuit spec with its Maestro export under ``where`` (the netlist carries the transformer's nport)."""
    export = maestro_export(where / "maestro", "tb", params="temperature=27 F=20")
    (export / "netlist" / "input.scs").write_text(NETLIST, encoding="utf-8")
    d = library_spec_dict(root, export=export, **overrides)
    d["simulator"] = {**d["simulator"], "license_check": False}
    return Spec.model_validate(d)


def nf(params, tb, corner):
    """The fake testbench: NF falls with F, a little worse at ss."""
    return {"NF": 9.5 - int(params["F"]) / 10 + (0.2 if corner == "ss" else 0.0)}


def run_of(spec: Spec, where: Path, metric_fn=nf) -> recipe.Run:
    store = RunStore(where / "proj")
    return recipe.Run(store.root, spec, store, FakeSpectreExecutor(store.root / "sims", metric_fn), None, SITE,
                      SITE.host("local"))


def test_auto_takes_metric_gp_and_every_point_of_every_batch_is_a_valid_one(root, tmp_path, capsys):
    spec = spec_at(root, tmp_path)
    assert resolve_auto(spec, 1) == ("metric_gp", LIBRARY_REASON) == ("metric_gp", "library devices: no EMX in the loop")
    assert stage_one_refusal(spec) is None
    run = run_of(spec, tmp_path)
    optimize.main(run, budget=14, batch=4, seed=3)
    out = capsys.readouterr().out
    assert "[optimize] strategy auto: metric_gp (library devices: no EMX in the loop)" in out
    assert "no current design: the spec has EM devices" in out
    obs = run.store.observations()
    assert len(obs) == 14 and len({o.key for o in obs}) == 14 and {o.status for o in obs} <= {"ok", "constraint_failed"}
    assert all(o.origin.startswith("suggest:metric_gp:") for o in obs)
    assert any(":init" in o.origin for o in obs) and any(":init" not in o.origin for o in obs)   # the design, then the model
    for o in obs:
        space.check(spec, o.params)                                              # a valid point: its combination is a row's
        row = link.row_for(spec, "xfmr", o.params)                             # that combination's best row
        assert o.children["xfmr/nominal"].library_row["obs_id"] == row.obs_id and o.simulations == 1
        assert o.metrics["k"] == row.values["k"] and o.metrics["Lp"] == row.values["Lp"]    # measured from its sNp
    again = run_of(spec_at(root, tmp_path / "again"), tmp_path / "again")
    optimize.main(again, budget=14, batch=4, seed=3)
    assert [o.params for o in again.store.observations()] == [o.params for o in obs]      # the same seed, the same points


def test_every_recipe_takes_a_library_spec_unchanged(root, tmp_path, capsys):
    """signoff (a search at one corner, then its best points at every corner, stopping at the first failure), coarse_to_fine
    and fix_run run a library spec as they run any other: every point a valid one, every device child a row."""
    import itertools
    import json

    from ic_opt.recipes import coarse_to_fine, fix_run, signoff

    corners = [{"id": "tt", "model_section": "tt"}, {"id": "ss", "model_section": "ss"}]
    run = run_of(spec_at(root, tmp_path / "signoff", corners=corners), tmp_path / "signoff")
    signoff.main(run, corner="tt", budget=6, batch=3, top=2, seed=1)
    obs = run.store.observations()
    search, checked = obs.by_step("search@tt"), obs.by_step("signoff")
    assert len(search) == 6 and len(checked) == 2 and all(o.corners() == {"tt"} for o in search)   # the device: no corner
    assert all(o.corners() == {"tt", "ss"} and o.status == "ok" and o.simulations == 2 for o in checked)
    assert "[optimize] strategy auto: metric_gp (library devices: no EMX in the loop)" in capsys.readouterr().out
    run = run_of(spec_at(root, tmp_path / "c2f"), tmp_path / "c2f")
    coarse_to_fine.main(run, coarse_budget=4, fine_budget=3, batch=2, seed=1)
    obs = run.store.observations()
    assert len(obs.by_step("coarse")) == 4 and len(obs.by_step("fine")) == 3 and len({o.key for o in obs}) == 7
    assert all(o.origin.startswith("suggest:metric_gp:") for o in obs)
    spec = spec_at(root, tmp_path / "fix")
    run = run_of(spec, tmp_path / "fix")
    rows = list(itertools.islice(space.valid_points(spec), 2))
    (run.project / "points.json").write_text(json.dumps(rows), encoding="utf-8")
    fix_run.main(run, points="points.json")
    obs = run.store.observations()
    assert [o.params for o in obs] == rows and all(o.status == "ok" and o.children["xfmr/nominal"].library_row for o in obs)
    for where in ("signoff", "c2f", "fix"):                   # one table, one grid: every point is one of its valid points
        for o in RunStore(tmp_path / where / "proj").observations():
            space.check(spec, o.params)


def test_the_plan_names_each_table_and_says_that_no_emx_runs(root, tmp_path, capsys):
    spec = spec_at(root, tmp_path, em=EM)
    run = run_of(spec, tmp_path)
    token = recipe.PLAN_MODE.set(True)
    try:
        optimize.main(run, budget=12, batch=4)
    finally:
        recipe.PLAN_MODE.reset(token)
    out = capsys.readouterr().out
    facts = link.summary(spec)["xfmr"]
    assert (f"[plan] [ok] library:xfmr: xfm_demo at 20 GHz: {facts['rows']} rows on the grid in {facts['combinations']} of "
            "1536 combinations, prefer max:Qmin") in out
    assert "[plan] [note] em: not used: the devices are library rows, no EMX runs" in out
    assert "[plan] strategy auto: metric_gp (library devices: no EMX in the loop)" in out
    assert ("[plan] opt.optimize step='optimize' strategy=metric_gp: 0/12 points done, up to 12 more in batches of 4 × "
            "(1 testbench sims + 1 device measurements of library rows, no simulation) = 1 simulations per point on local, "
            "2 workers × (2 + 1) threads (spectre), no EMX runs (the devices are library rows)") in out
    assert "device:xfmr" not in out and "[plan] [ok] emx" not in out and "em:envelope" not in out
    assert not (run.store.root / "observations.jsonl").exists()


def test_the_doctor_checks_the_table_instead_of_the_generator_and_emx(root, tmp_path):
    spec = spec_at(root, tmp_path, em=EM)
    host = FakeSpectreExecutor(tmp_path / "sims")
    report = doctor(spec, host, limits=FAKE_HOST)
    checks = {c.name: c for c in report.checks}
    assert report.ok and checks["library:xfmr"].ok and checks["library:xfmr"].detail == link.sentence(spec, "xfmr")
    assert not {"device:xfmr", "emx", "em:process_file", "em:stack", "em:envelope"} & set(checks)
    assert checks["em"].tag == "note" and not checks["em"].blocking
    assert checks["envelope"].ok and checks["envelope"].detail.startswith("2 jobs × (2 + 1) threads / 0 GB per job")
    assert not any(c.startswith(("which emx", "sha256sum")) for c in host.commands)
    assert "jobs=2 × (2 + 1) threads → peak_threads=6" in plan_line(spec, host, FAKE_HOST)
    missing = spec_at(root, tmp_path / "missing", devices=[library_device(tmp_path / "nowhere")])
    failed = {c.name: c for c in doctor(missing, host, limits=FAKE_HOST).checks}["library:xfmr"]
    assert not failed.ok and failed.blocking and failed.detail.startswith("device xfmr: no library.yaml at ")
    off_grid = spec_at(root, tmp_path / "far", grid={"xfmr.Lp": ("1n", "2n", "0.1n"), "xfmr.Ls": ("1n", "2n", "0.1n"),
                                                      "xfmr.k": ("0.85", "0.95", "0.05")})
    failed = {c.name: c for c in doctor(off_grid, host, limits=FAKE_HOST).checks}["library:xfmr"]
    assert not failed.ok and "no row of xfm_demo at 20 GHz lies on the grid of its variables" in failed.detail


def test_metric_gp_refuses_only_a_device_simulated_in_the_loop_and_says_both_ways_out(root, tmp_path):
    from tests.ic_opt.test_optimize import devices_spec

    assert stage_one_refusal(devices_spec()) == DEVICES_REFUSAL
    assert "name openbox_gp_eic" in DEVICES_REFUSAL and "take each device from a library table" in DEVICES_REFUSAL
    with pytest.raises(ValueError, match="take each device from a library table"):
        MetricGpSuggester().propose(devices_spec(), Observations(), 2, seed=0)
    assert resolve_auto(devices_spec(), 1) == ("openbox_gp_eic", "metric_gp does not take EM devices yet")
    spec = spec_at(root, tmp_path)
    proposal = MetricGpSuggester().propose(spec, Observations(), 3, seed=0)      # taken: the design over the valid points
    assert len(proposal.raw) == 3 and all(space.check(spec, space.snap(spec, raw)) is None for raw in proposal.raw)


# -- the schedule -----------------------------------------------------------------------------------------------------------

def test_the_library_device_runs_first_and_a_point_that_fails_its_constraint_simulates_nothing(root, tmp_path, capsys):
    corners = [{"id": "tt", "model_section": "tt"}, {"id": "ss", "model_section": "ss"}]
    probe = spec_at(root, tmp_path / "probe")
    linked = link.resolve(probe)["xfmr"]
    best = {cell: rows[0].values["Qp"] for cell, rows in linked.table.cells.items()}
    limit = sorted(best.values())[len(best) // 2]                            # half the combinations fail Qp > limit
    spec = spec_at(root, tmp_path, corners=corners, simulator={"parallel_jobs": 2, "threads_per_run": 2, "timeout_s": 60,
                                                               "license_check": False, "stop_at_first_failure": True},
                   constraints=[{"metric": "NF", "op": "lt", "value": "9 dB"}, {"metric": "Qp", "op": "gt", "value": f"{limit!r}"}])
    store = RunStore(tmp_path / "proj")
    ex = FakeSpectreExecutor(store.root / "sims", nf)
    deck = import_netlists(spec, ex, store)

    def point(cell) -> Point:
        texts = {n: space.format_value(space.parse_scalar(v.lower)[0] + k * space.parse_scalar(v.step)[0],
                                       space.parse_scalar(v.lower)[1])
                 for n, k, v in zip(linked.names, cell, [next(v for v in spec.variables if v.name == n) for n in linked.names],
                                    strict=True)}
        return Point({"F": "28", **texts}, "user")

    failing = [c for c in linked.table.levels() if best[c] <= limit]
    passing = [c for c in linked.table.levels() if best[c] > limit]
    history = evaluate(spec, [point(c) for c in failing[:6] + passing[:6]], ex, store, deck=deck, limits=FAKE_HOST)
    assert len(history) == 12 and sum(o.status == "constraint_failed" for o in history) == 6
    children = engine.children_of(spec, default_pipeline(spec, deck), spec.corner_ids)
    schedule = Schedule.from_history(spec, history, children, "all_corners")
    assert schedule.histories is not None                                            # learned: past the warm-up
    assert schedule.order(children)[0].key == "xfmr/nominal"                          # the device child first
    assert schedule.history_of(next(c for c in children if c.unit == "xfmr")).failed == 6
    for o in history:                                                                 # from the first batch on, too
        if o.status == "constraint_failed":
            assert set(o.children) == {"xfmr/nominal"} and o.not_run == ["tb/tt", "tb/ss"] and o.simulations == 0
            assert "not simulated: 2 of 3 children (stopped after xfmr/nominal)" in o.issues
        else:
            assert set(o.children) == {"tb/tt", "tb/ss", "xfmr/nominal"} and o.simulations == 2
    capsys.readouterr()
    (stopped,) = evaluate(spec, [point(failing[6])], ex, store, deck=deck, limits=FAKE_HOST)
    assert stopped.status == "constraint_failed" and stopped.not_run == ["tb/tt", "tb/ss"] and stopped.simulations == 0
    assert stopped.corners() == {"tt", "ss"} and any(f"Qp gt {limit!r} violated by" in i for i in stopped.issues)
    assert "1 of 1 points stopped early, 2 simulations not run" in capsys.readouterr().out
    assert not list((store.root / "sims" / stopped.obs_id).glob("tb"))                 # no simulation ran for it
