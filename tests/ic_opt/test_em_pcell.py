"""T9.2: the pcell stage on the packaged demo profile, and V1 geometry parity against recorded em-opt sweeps."""

from __future__ import annotations

import hashlib
import json
import os
import random
from pathlib import Path

import pytest

from ic_opt.em.pcell import get_generator
from ic_opt.eval.stage import StageContext, StageFailure
from ic_opt.executor import LocalExecutor
from ic_opt.observation import ChildResult
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.stages.em_chain import Geometry, Pcell, device_config, snp_order
from ic_opt.store import RunStore
from tests.ic_opt.fakes import minimal_spec

klayout = pytest.importorskip("klayout.db")

FIXTURE = {"inner_margin_um": 15.0, "ring_width_um": 20.0, "stub_length_um": 2.0, "stub_chamfer_um": 0.0}
IND_FIXED = {"turns": 1, "opening_um": 8.0, "lead_length_um": 20.0, "spacing_um": 2.0, "top_metal": "6", "bottom_metal": "5", "ground_fixture": FIXTURE}
XFM_FIXED = {"primary_outer_diameter_um": 90.0, "secondary_outer_diameter_um": 92.0, "primary_opening_um": 8.0, "secondary_opening_um": 8.0,
             "primary_lead_length_um": 20.0, "secondary_lead_length_um": 20.0, "center_spacing_um": 8.0, "primary_metal": "6", "secondary_metal": "5",
             "ground_fixture": FIXTURE}


def demo_spec(*, two_devices: bool = False, bindings=()) -> Spec:
    d = minimal_spec()
    d["testbenches"] = [{"id": "tb", "maestro_point_root": "/x", "virtuoso_library": "l", "cell": "c", "test_name": "t"}] if bindings else []
    d["devices"] = [{"id": "ind", "generator": "clean_port_ind_sym", "profile": "demo_6m", "ports": ["P1", "N1"], "fixed": IND_FIXED}]
    d["variables"] = [{"name": "outer_diameter_um", "kind": "continuous_step", "lower": "80", "upper": "120", "step": "10"},
                      {"name": "width_um", "kind": "continuous_step", "lower": "3", "upper": "6", "step": "0.5"}]
    if two_devices:
        d["devices"].append({"id": "xfm", "generator": "clean_port_xfm_bs", "profile": "demo_6m", "ports": ["P1", "N1", "P2", "N2"], "fixed": XFM_FIXED})
        d["variables"] = [{"name": "ind.outer_diameter_um", "kind": "continuous_step", "lower": "80", "upper": "120", "step": "10"},
                          {"name": "ind.width_um", "kind": "continuous_step", "lower": "3", "upper": "6", "step": "0.5"},
                          {"name": "xfm.primary_width_um", "kind": "continuous_step", "lower": "4", "upper": "8", "step": "1"},
                          {"name": "xfm.secondary_width_um", "kind": "continuous_step", "lower": "4", "upper": "8", "step": "1"}]
    d["bindings"] = list(bindings)
    d["metrics"], d["constraints"], d["objective"] = [], [], None
    return Spec.model_validate(d)


def point_context(spec: Spec, tmp_path: Path) -> StageContext:
    store = RunStore(tmp_path)
    workdir = store.root / "sims" / "obs_0001"
    workdir.mkdir(parents=True)
    return StageContext(spec=spec, executor=LocalExecutor(store.root / "sims"), store=store, obs_id="obs_0001", workdir=workdir, remote_dir="r")


def test_pcell_builds_the_device_and_records_ports_and_hash(tmp_path):
    spec = demo_spec()
    ctx = point_context(spec, tmp_path)
    geometry = Pcell(spec).run(Point({"outer_diameter_um": "100", "width_um": "5"}, "user"), ctx)
    g = geometry.devices["ind"]
    assert g.gds_path == ctx.workdir / "em" / "ind" / "ind.gds" and g.top_cell == "ind"
    assert [p.argument() for p in g.ports] == ["N1=N1:G02", "P1=P1:G01"] and g.snp_order == ["P1", "N1"]
    assert g.config["outer_diameter_um"] == 100.0 and g.config["width_um"] == 5.0 and g.config["turns"] == 1
    assert g.gds_sha256 == hashlib.sha256(g.gds_path.read_bytes()).hexdigest()
    assert json.loads((ctx.workdir / "em" / "geometry.json").read_text())["ind"]["gds_sha256"] == g.gds_sha256
    again = Pcell(spec).run(Point({"outer_diameter_um": "100", "width_um": "5"}, "user"), point_context(spec, tmp_path / "b"))
    assert again.devices["ind"].gds_sha256 == g.gds_sha256                       # byte-deterministic geometry


def test_pcell_two_devices_take_prefixed_variables_and_the_binding_sets_the_snp_order(tmp_path):
    spec = demo_spec(two_devices=True, bindings=[{"testbench": "tb", "instance": "NPORT0", "device": "xfm", "terminals": ["P1", "P2", "N1", "N2"]}])
    point = Point({"ind.outer_diameter_um": "90", "ind.width_um": "4", "xfm.primary_width_um": "6", "xfm.secondary_width_um": "5"}, "user")
    assert device_config(spec, spec.device("xfm"), point)["primary_width_um"] == 6.0
    assert snp_order(spec, spec.device("xfm")) == ["P1", "P2", "N1", "N2"] and snp_order(spec, spec.device("ind")) == ["P1", "N1"]
    geometry = Pcell(spec).run(point, point_context(spec, tmp_path))
    assert set(geometry.devices) == {"ind", "xfm"} and geometry.devices["xfm"].top_cell == "xfm"
    assert [p.signal for p in geometry.devices["xfm"].ports] == ["N1", "N2", "P1", "P2"]   # generator writes them sorted by name


def test_pcell_failures_are_stage_failures(tmp_path):
    spec = demo_spec()
    ctx = point_context(spec, tmp_path)
    with pytest.raises(StageFailure, match="invalid generator config"):
        Pcell(spec).run(Point({"outer_diameter_um": "100", "width_um": "-5"}, "user"), ctx)
    with pytest.raises(StageFailure, match="unit suffix"):
        Pcell(spec).run(Point({"outer_diameter_um": "100u", "width_um": "5"}, "user"), ctx)
    with pytest.raises(ValueError, match="unknown geometry generator"):
        Pcell(demo_spec().model_copy(update={"devices": [spec.devices[0].model_copy(update={"generator": "nope"})]}))


def test_pcell_runs_through_the_engine_with_a_device_child(tmp_path):
    from ic_opt.eval import engine
    from ic_opt.eval.stage import Resources

    class Size:
        name, level, unit, resources = "size", "child", "device", Resources()

        def fingerprint(self, inp, ctx):
            return None

        def run(self, geometry: Geometry, ctx):
            return ChildResult(unit=ctx.unit, status="ok", metrics={"bytes": float(geometry.devices[ctx.unit].gds_path.stat().st_size)})

    d = demo_spec().model_dump(mode="json")
    d["metrics"] = [{"name": "bytes", "unit": "B", "device": "ind", "quantity": "gds_bytes"}]
    d["objective"] = {"direction": "minimize", "expression": "bytes"}
    spec = Spec.model_validate(d)
    store = RunStore(tmp_path)
    obs = engine.run(spec, [Pcell(spec), Size()], [Point({"outer_diameter_um": "100", "width_um": "5"}, "user"),
                                                   Point({"outer_diameter_um": "100", "width_um": "-1"}, "user")],
                     LocalExecutor(store.root / "sims"), store)
    assert obs[0].status == "ok" and obs[0].metrics["bytes"] > 1000 and obs[0].children["ind/nominal"].status == "ok"
    assert obs[1].status == "failed:pcell" and "invalid generator config" in obs[1].issues[0]


# -- V1: geometry parity — the moved library vs em-opt's own package on recorded sweep configs --------

SWEEPS = Path(os.environ.get("IC_OPT_EM_SWEEPS", "/nonexistent"))     # em-opt experiments/device_db_sweep_n28/outputs/sweep
EM_OPT = Path(os.environ.get("IC_OPT_EM_OPT_REPO", "/nonexistent"))   # em-opt checkout with its own .venv (the reference implementation)

_REFERENCE_SCRIPT = """
import json, sys
from pathlib import Path
from em_ic_opt_workflow.geometry.registry import get_generator
jobs = json.loads(Path(sys.argv[1]).read_text())
outcomes = []
for job in jobs:
    try:
        g = get_generator(job["generator_id"], plugin_module="builtin:clean_port")
        g.generate(g.config_model.model_validate(job["config"]), outdir=Path(job["outdir"]), gds_name=job["gds_name"])
        outcomes.append("ok")
    except Exception as exc:          # a recorded config the current generation refuses: the moved library must refuse it too
        outcomes.append(f"{type(exc).__name__}: {exc}")
Path(sys.argv[2]).write_text(json.dumps(outcomes))
"""


@pytest.mark.skipif(not (SWEEPS.is_dir() and (EM_OPT / ".venv" / "bin" / "python").exists() and os.environ.get("IC_OPT_PROFILE_DIRS")),
                    reason="set IC_OPT_EM_SWEEPS, IC_OPT_EM_OPT_REPO and IC_OPT_PROFILE_DIRS")
@pytest.mark.parametrize("stratum", sorted(p.name for p in SWEEPS.iterdir()) if SWEEPS.is_dir() else [])
def test_moved_library_reproduces_em_opt_bytes(tmp_path, stratum):
    """Same recorded generator configs, generated by em-opt's package and by the moved one: identical GDS bytes.

    (The recorded GDS files themselves were drawn by older geometry generations, so they are not the reference.)"""
    import subprocess

    points = sorted(p for p in (SWEEPS / stratum).iterdir() if (p / "geometry_manifest.json").exists() and list(p.glob("*.gds")))
    sample = random.Random(20260922).sample(points, min(8, len(points)))
    jobs = []
    for point in sample:
        manifest = json.loads((point / "geometry_manifest.json").read_text())
        jobs.append({"generator_id": manifest["generator_id"], "config": manifest["geometry"]["config"],
                     "outdir": str(tmp_path / "ref" / point.name), "gds_name": next(g for g in point.glob("*.gds")).name})
    (tmp_path / "jobs.json").write_text(json.dumps(jobs))
    env = {**os.environ, "EM_IC_OPT_PROFILE_DIRS": os.environ["IC_OPT_PROFILE_DIRS"]}
    subprocess.run([str(EM_OPT / ".venv" / "bin" / "python"), "-c", _REFERENCE_SCRIPT, str(tmp_path / "jobs.json"), str(tmp_path / "outcomes.json")],
                   check=True, env=env, cwd=EM_OPT)
    outcomes = json.loads((tmp_path / "outcomes.json").read_text())
    for job, outcome in zip(jobs, outcomes, strict=True):
        generator = get_generator(job["generator_id"], plugin_module="builtin:clean_port")
        label = f"{stratum}/{Path(job['outdir']).name}"
        if outcome != "ok":
            try:
                generator.generate(generator.config_model.model_validate(job["config"]), outdir=tmp_path / "ours" / Path(job["outdir"]).name, gds_name=job["gds_name"])
            except Exception as exc:  # noqa: BLE001 - the refusal itself is the expected result
                assert f"{type(exc).__name__}: {exc}" == outcome, label
                continue
            pytest.fail(f"{label}: em-opt refused ({outcome}) but the moved library built it")
        ours = generator.generate(generator.config_model.model_validate(job["config"]), outdir=tmp_path / "ours" / Path(job["outdir"]).name, gds_name=job["gds_name"])
        assert ours.gds_path.read_bytes() == (Path(job["outdir"]) / job["gds_name"]).read_bytes(), label
