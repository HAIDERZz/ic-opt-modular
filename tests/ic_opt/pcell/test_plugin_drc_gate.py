"""T16 R-15: a plugin generator passes the pcell stage's DRC gate by declaring the conductors it draws.

The gate knew only the six built-in generator ids, so a generator from any other plugin file (``Device.plugin``)
failed every build with ``failed:pcell`` unless its config turned the audit off. A generator now declares its
conductors through the plugin interface (``PassiveDeviceGenerator.expected_conductors``); the six built-ins declare
theirs the same way, with the recipes they always had.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ic_opt.eval.stage import Resources, StageFailure
from ic_opt.observation import ChildResult
from ic_opt.space import Point
from ic_opt.spec import Spec

klayout = pytest.importorskip("klayout.db")

TOY_PLUGIN = '''
"""A toy plugin: a straight strip on one metal with a port at each end."""
import json
from pathlib import Path

import klayout.db as kdb
from pydantic import BaseModel, ConfigDict, Field

from ic_opt.em.pcell.base import GeometryGenerationResult, PassiveDeviceGenerator
from ic_opt.em.pcell.process_rules import get_process_rule_profile


class StripConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    process_profile: str
    port_order: list[str]
    metal: str = "M6"
    width_um: float = Field(gt=0)
    length_um: float = 40.0
    drc_check: bool = True


class Strip(PassiveDeviceGenerator):
    """Declares nothing: the gate cannot judge its GDS."""

    generator_id = "toy_strip"
    config_model = StripConfig

    def generate(self, config, *, outdir, gds_name):
        conductor = get_process_rule_profile(config.process_profile).layer_catalog.conductors[config.metal]
        outdir = Path(outdir)
        layout = kdb.Layout()
        layout.dbu = 0.001
        top = layout.create_cell(Path(gds_name).stem)
        top.shapes(layout.layer(*conductor.drawing)).insert(kdb.DBox(0.0, 0.0, config.length_um, config.width_um))
        pin = layout.layer(*conductor.pin)
        for name, x in zip(config.port_order, (0.0, config.length_um)):
            top.shapes(pin).insert(kdb.DText(name, kdb.DTrans(kdb.DVector(x, config.width_um / 2))))
        gds = outdir / gds_name
        layout.write(str(gds))
        ports = outdir / "emx_ports.txt"
        ports.write_text("".join(f"-p {name}={name}\\n" for name in sorted(config.port_order)))
        manifest = outdir / "geometry_manifest.json"
        manifest.write_text(json.dumps({"generator_id": self.generator_id, "geometry": {"config": config.model_dump()}}))
        return GeometryGenerationResult(self.generator_id, gds, top.name, manifest, ports)


class DeclaredStrip(Strip):
    """The same strip, declaring the one metal it draws."""

    generator_id = "toy_strip_declared"

    def expected_conductors(self, config):
        return [config.metal]


class OverDeclaredStrip(Strip):
    """Declares a metal it never draws."""

    generator_id = "toy_strip_over_declared"

    def expected_conductors(self, config):
        return [config.metal, "M5"]


PLUGIN_GENERATORS = {generator.generator_id: generator for generator in (Strip(), DeclaredStrip(), OverDeclaredStrip())}
'''


def toy_spec(tmp_path: Path, generator: str, **fixed) -> Spec:
    from tests.ic_opt.test_em_pcell import demo_spec

    tmp_path.mkdir(parents=True, exist_ok=True)
    plugin = tmp_path / "toy_plugin.py"
    plugin.write_text(TOY_PLUGIN, encoding="utf-8")
    d = demo_spec().model_dump(mode="json")
    d["devices"] = [{"id": "strip", "generator": generator, "plugin": str(plugin), "profile": "demo_6m", "ports": ["P1", "N1"],
                     "fixed": fixed}]
    d["variables"] = [{"name": "width_um", "kind": "continuous_step", "lower": "0.5", "upper": "4", "step": "0.5"}]
    return Spec.model_validate(d)


def build(spec: Spec, tmp_path: Path, width: str):
    from ic_opt.stages.em_chain import Pcell
    from tests.ic_opt.test_em_pcell import point_context

    return Pcell(spec).run(Point({"width_um": width}, "user"), point_context(spec, tmp_path / f"run_{width}"))


def test_a_plugin_generator_that_declares_its_conductors_passes_the_gate(tmp_path):
    geometry = build(toy_spec(tmp_path, "toy_strip_declared"), tmp_path, "2")            # drc_check left on
    g = geometry.devices["strip"]
    assert g.top_cell == "strip" and sorted(p.signal for p in g.ports) == ["N1", "P1"]


def test_the_gate_audits_the_plugin_geometry_and_its_coverage(tmp_path):
    with pytest.raises(StageFailure, match="DRC audit found 1 violation") as failure:
        build(toy_spec(tmp_path, "toy_strip_declared"), tmp_path, "0.5")                 # narrower than M6's min_width
    assert any("[min_width] M6" in line for line in failure.value.args)
    with pytest.raises(StageFailure, match=r"missing product layers \['M5'\]"):
        build(toy_spec(tmp_path / "over", "toy_strip_over_declared"), tmp_path / "over", "2")


def test_a_plugin_generator_that_declares_nothing_fails_closed_unless_the_check_is_off(tmp_path):
    with pytest.raises(StageFailure, match="declares no expected conductors"):
        build(toy_spec(tmp_path, "toy_strip"), tmp_path, "2")
    assert "strip" in build(toy_spec(tmp_path / "off", "toy_strip", drc_check=False), tmp_path / "off", "2").devices


def test_through_the_engine_a_declared_plugin_point_is_ok(tmp_path):
    from ic_opt.eval import engine
    from ic_opt.executor import LocalExecutor
    from ic_opt.stages.em_chain import Pcell
    from ic_opt.store import RunStore
    from tests.ic_opt.fakes import FAKE_HOST

    class Size:
        name, level, unit, resources = "size", "child", "device", Resources()

        def fingerprint(self, inp, ctx):
            return None

        def run(self, geometry, ctx):
            return ChildResult(unit=ctx.unit, status="ok", metrics={"bytes": float(geometry.devices[ctx.unit].gds_path.stat().st_size)})

    store = RunStore(tmp_path / "store")
    points = [Point({"width_um": "2"}, "user")]
    for generator, status in (("toy_strip_declared", "ok"), ("toy_strip", "failed:pcell")):
        d = toy_spec(tmp_path / generator, generator).model_dump(mode="json")
        d["metrics"] = [{"name": "bytes", "unit": "B", "device": "strip", "quantity": "gds_bytes"}]
        d["objective"] = {"direction": "minimize", "expression": "bytes"}
        spec = Spec.model_validate(d)
        (obs,) = engine.run(spec, [Pcell(spec), Size()], points, LocalExecutor(store.root / "sims"), store, limits=FAKE_HOST)
        assert obs.status == status, obs.issues


@pytest.mark.parametrize("family", ["clean_port_ind_sym", "clean_port_xfm_bs", "clean_port_xfm_ms", "clean_port_xfm_balun",
                                    "clean_port_xfm_tw", "clean_port_xfm_il"])
def test_the_built_ins_declare_their_recipes_through_the_same_hook(family):
    from ic_opt.em.pcell._pcell_core import max_opening
    from ic_opt.em.pcell.drc_audit import expected_conductors, require_layers_from_config
    from ic_opt.em.pcell.generator_plugin import PLUGIN_GENERATORS
    from ic_opt.em.pcell.process_rules import get_process_rule_profile
    from ic_opt.em.pcell.profile_validation import _canonical_config

    generator = PLUGIN_GENERATORS[family]
    config = generator.config_model.model_validate(
        _canonical_config(family, get_process_rule_profile("demo_6m"), "demo_6m", 6, max_opening))
    recipe = require_layers_from_config(family, config.model_dump())
    assert generator.expected_conductors(config) == recipe == expected_conductors(generator, config) and recipe[0] == "M6"
