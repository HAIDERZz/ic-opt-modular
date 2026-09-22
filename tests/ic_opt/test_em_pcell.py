"""T9.2: the pcell stage on the packaged demo profile, and V1 geometry parity against recorded em-opt sweeps."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ic_opt.em.pcell import GEOMETRY_VERSION
from ic_opt.eval.stage import StageContext, StageFailure, pipeline_fingerprint
from ic_opt.executor import LocalExecutor
from ic_opt.observation import ChildResult
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.stages.em_chain import Geometry, Pcell, device_config, snp_order
from ic_opt.store import RunStore
from tests.ic_opt.fakes import minimal_spec

klayout = pytest.importorskip("klayout.db")

FIXTURE = {"inner_margin_um": 15.0, "ring_width_um": 20.0, "stub_length_um": 2.0, "stub_chamfer_um": 0.0}
IND_FIXED = {"turns": 1, "opening_um": 8.0, "lead_length_um": 20.0, "spacing_um": 2.0, "metal": "6", "ground_fixture": FIXTURE}
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


def test_geometry_version_reaches_the_manifest_and_retires_older_pipelines(tmp_path, monkeypatch):
    spec = demo_spec()
    ctx = point_context(spec, tmp_path)
    Pcell(spec).run(Point({"outer_diameter_um": "100", "width_um": "5"}, "user"), ctx)
    manifest = json.loads((ctx.workdir / "em" / "ind" / "geometry_manifest.json").read_text())
    assert manifest["geometry_version"] == GEOMETRY_VERSION
    stage = Pcell(spec)
    assert stage.identity == json.dumps({"ind": GEOMETRY_VERSION}, separators=(",", ":"))
    before = pipeline_fingerprint([stage])
    monkeypatch.setattr(type(stage.generators["ind"]), "geometry_version", GEOMETRY_VERSION + 1)
    assert pipeline_fingerprint([Pcell(spec)]) != before                          # a bump means no reuse of older observations


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


# V1 (the moved library reproducing em-opt's package byte for byte) served the move; since GEOMETRY_VERSION 7 the
# library deliberately draws differently. Geometry drift is now caught by tests/ic_opt/pcell/test_golden.py and
# measured against recorded points by ic_opt.em.pcell.replay.
