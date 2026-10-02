"""T19.1 (``docs/refactor/T19_1_FIXTURE_METAL_SPEC.md``): the ground fixture's metal -- the bottom metal by default, byte for
byte as before; ``auto`` the highest metal the device draws nothing on; an explicit metal refused when the device draws on
it; the DRC gate's max_width exemption, the manifest, the footprint and pgs follow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("klayout.db")

import klayout.db as kdb
from pydantic import ValidationError

from ic_opt.em.pcell import footprint as fp
from ic_opt.em.pcell import get_generator
from ic_opt.em.pcell._pcell_core import PortError
from ic_opt.em.pcell.drc_audit import (
    audit_gds,
    expected_conductors,
    fixture_exemptions,
    product_scope_record,
)
from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter
from ic_opt.em.pcell.stack import use_stack
from tests.ic_opt.pcell.test_golden import FIXTURE, XFM_PORTS

PROFILE = "demo_6m"                      # metals M1 .. M6; a device on M6 / M5 leaves M4 as the highest free metal

FAMILIES = {
    "clean_port_ind_sym": {"port_order": ["P1", "N1"], "outer_diameter_um": 100.0, "width_um": 5.0, "spacing_um": 2.0, "opening_um": 8.0,
                           "lead_length_um": 20.0, "turns": 2, "metal": "6"},                                   # M6 winding, M5 crossunder
    "clean_port_xfm_bs": {"port_order": XFM_PORTS, "primary_outer_diameter_um": 100.0, "secondary_outer_diameter_um": 100.0, "primary_width_um": 5.0,
                          "secondary_width_um": 5.0, "primary_opening_um": 8.0, "secondary_opening_um": 8.0, "primary_lead_length_um": 20.0,
                          "secondary_lead_length_um": 20.0, "center_spacing_um": 0.0, "primary_metal": "6", "secondary_metal": "5"},
    "clean_port_xfm_ms": {"port_order": XFM_PORTS, "primary_outer_diameter_um": 120.0, "secondary_outer_diameter_um": 120.0, "primary_width_um": 5.0,
                          "secondary_width_um": 5.0, "primary_opening_um": 8.0, "secondary_opening_um": 8.0, "primary_lead_length_um": 20.0,
                          "secondary_lead_length_um": 20.0, "center_spacing_um": 0.0, "secondary_turns": 2, "secondary_spacing_um": 2.0,
                          "primary_metal": "6", "secondary_metal": "5"},                                           # crossunder on M4
}
HIGHEST_FREE = {"clean_port_ind_sym": "M4", "clean_port_xfm_bs": "M4", "clean_port_xfm_ms": "M3"}


def generate(generator_id: str, tmp_path: Path, fixture: dict, name: str = "x.gds", **overrides):
    gen = get_generator(generator_id, plugin_module="builtin:clean_port")
    model = gen.config_model.model_validate({"process_profile": PROFILE, "ground_fixture": fixture, **FAMILIES[generator_id], **overrides})
    return gen, model, gen.generate(model, outdir=tmp_path, gds_name=name)


def layers_of(gds: Path) -> dict[tuple[int, int], int]:
    layout = kdb.Layout()
    layout.read(str(gds))
    top = layout.top_cell()
    return {(layout.get_info(li).layer, layout.get_info(li).datatype): sum(1 for _ in top.begin_shapes_rec(li).each()) for li in layout.layer_indexes()
            if any(True for _ in top.begin_shapes_rec(li).each())}


@pytest.mark.parametrize("generator_id", sorted(FAMILIES))
def test_the_default_is_the_bottom_metal_byte_for_byte(tmp_path, generator_id):
    """``metal`` absent and ``metal: null`` give the GDS, the ports and the manifest the field-less config gave: the fixture
    on M1, the manifest naming it, nothing else different."""
    _, _, absent = generate(generator_id, tmp_path / "a", FIXTURE)
    _, _, null = generate(generator_id, tmp_path / "b", {**FIXTURE, "metal": None})
    assert absent.gds_path.read_bytes() == null.gds_path.read_bytes()
    assert absent.emx_ports_path.read_text() == null.emx_ports_path.read_text()
    assert absent.fixture_metal == null.fixture_metal == "M1"
    manifest = json.loads(absent.manifest_path.read_text())["geometry"]
    assert manifest["fixture_metal"] == "M1" and "metal" not in manifest["config"]["ground_fixture"]
    m1 = tuple(get_geometry_rule_adapter(PROFILE).layer("M1").drawing)
    assert layers_of(absent.gds_path)[m1] > 0


@pytest.mark.parametrize("generator_id", sorted(FAMILIES))
def test_auto_picks_the_highest_metal_the_device_draws_nothing_on(tmp_path, generator_id):
    """On the demo stack the windings sit on M6 / M5 (the ms crossunder on M4), so ``auto`` lands on the highest metal below
    them; the ring, the stubs and the G pins move there, the bottom metal is left empty, the ports keep their form, and the
    manifest records the conductor and its layer."""
    gen, model, result = generate(generator_id, tmp_path, {**FIXTURE, "metal": "auto"})
    expected = HIGHEST_FREE[generator_id]
    assert result.fixture_metal == expected
    adapter = get_geometry_rule_adapter(PROFILE)
    layers = layers_of(result.gds_path)
    assert layers[tuple(adapter.layer(expected).drawing)] > 0 and tuple(adapter.layer("M1").drawing) not in layers
    assert layers[tuple(adapter.layer(expected).pin)] == len(model.port_order)            # one G pin per port
    manifest = json.loads(result.manifest_path.read_text())["geometry"]
    assert manifest["fixture_metal"] == expected and manifest["fixture_layer"] == list(adapter.layer(expected).drawing)
    assert manifest["config"]["ground_fixture"]["metal"] == "auto"
    lines = result.emx_ports_path.read_text().split()
    assert all(line.startswith("-p") or ":G" in line for line in lines) and len([ln for ln in lines if ":G" in ln]) == len(model.port_order)


def test_an_explicit_metal_that_carries_a_port_lead_or_a_device_shape_is_refused(tmp_path):
    """M6 carries the primary's leads (what EMX refuses, the port named); M5 the secondary's; M4 nothing: accepted. The
    inductor's crossunder on M5 carries no port but a device shape: refused with its layer, before any file is written."""
    with pytest.raises(PortError, match=r"carries the lead of port P1, N1.*EMX refuses"):
        generate("clean_port_xfm_bs", tmp_path / "a", {**FIXTURE, "metal": "6"})
    with pytest.raises(PortError, match=r"carries the lead of port"):
        generate("clean_port_xfm_bs", tmp_path / "b", {**FIXTURE, "metal": "M5"})
    with pytest.raises(PortError, match=r"carries device shapes on layer"):
        generate("clean_port_ind_sym", tmp_path / "c", {**FIXTURE, "metal": "5"})
    assert not list((tmp_path / "c").glob("*.gds"))
    _, _, ok = generate("clean_port_xfm_bs", tmp_path / "d", {**FIXTURE, "metal": "4"})
    assert ok.fixture_metal == "M4"


def test_auto_is_refused_in_reference_mode():
    """Reference mode has no profile stack to choose from."""
    from ic_opt.em.pcell._pcell_core import Cell, finalize_emx_ports, metal_layer
    from ic_opt.em.pcell.fixture import GroundFixtureConfig, add_ground_fixture

    body = Cell("body", "test", {})
    body.add_rect(metal_layer(6), -20.0, -20.0, 20.0, 20.0)
    lead = Cell("lead", "test", {})
    lead.add_rect(metal_layer(6), 0.0, 0.0, 10.0, 4.0)
    lead.add_emx_port(name="P1", logical_name="P1", metal=6, label_layer=(66, 1), x_um=10.0, y_um=2.0, lead_zone_um=(0.0, 0.0, 10.0, 4.0))
    body.inst(lead, (20.0, 0.0), "R0")
    body.emx_ports = finalize_emx_ports(body)
    with pytest.raises(PortError, match="reference mode"):
        add_ground_fixture(body, GroundFixtureConfig(inner_margin_um=15.0, ring_width_um=10.0, stub_width_um=4.0, stub_length_um=2.0,
                                                     stub_chamfer_um=0.0, metal="auto"))


def test_the_drc_gate_exempts_max_width_on_the_fixture_metal_only(tmp_path):
    """With the ring on M4 the gate exempts max_width there and passes; with the exemption left on M1 (an older caller) the
    same build fails on the ring's width; and every other rule on M4 still counts: two shapes closer than its min space, far
    from the ring, fail the gate with the exemption in place."""
    gen, model, result = generate("clean_port_xfm_bs", tmp_path, {**FIXTURE, "metal": "auto"})
    assert result.fixture_metal == "M4"
    with use_stack(PROFILE):
        report = audit_gds(result.gds_path, PROFILE)
        expected = expected_conductors(gen, model)
        assert product_scope_record(report, expected, ignore_findings=fixture_exemptions(PROFILE, result.fixture_metal))["outcome"] == "pass"
        stale = product_scope_record(report, expected, ignore_findings=fixture_exemptions(PROFILE))
        assert stale["outcome"] != "pass" and any(v["kind"] == "max_width" and v["layer"] == "M4" for v in stale["violations"])
    assert fixture_exemptions(PROFILE, "4") == fixture_exemptions(PROFILE, "M4") == frozenset({("max_width", "M4")})
    with pytest.raises(ValueError, match="not a metal of profile"):
        fixture_exemptions(PROFILE, "M9")
    layout = kdb.Layout()
    layout.read(str(result.gds_path))
    top = layout.top_cell()
    adapter = get_geometry_rule_adapter(PROFILE)
    li = layout.layer(*adapter.layer("M4").drawing)
    top.shapes(li).insert(kdb.Box(-300_000, 400_000, -200_000, 405_000))       # two 5 um strips 0.05 um apart (M4 min space 0.1 um)
    top.shapes(li).insert(kdb.Box(-300_000, 405_050, -200_000, 410_050))
    close = tmp_path / "close.gds"
    layout.write(str(close))
    with use_stack(PROFILE):
        record = product_scope_record(audit_gds(close, PROFILE), expected, ignore_findings=fixture_exemptions(PROFILE, result.fixture_metal))
    assert record["outcome"] != "pass" and any(v["layer"] == "M4" and v["kind"] != "max_width" for v in record["violations"])


def test_pgs_keeps_the_bottom_ring(tmp_path):
    """A shielded device refuses a fixture metal other than the bottom metal, at validation."""
    gen = get_generator("clean_port_xfm_bs", plugin_module="builtin:clean_port")
    pgs = {"strip_width_um": 1.0, "strip_spacing_um": 2.0, "margin_um": 5.0}
    with pytest.raises(ValidationError, match="pgs ties its strips"):
        gen.config_model.model_validate({"process_profile": PROFILE, "ground_fixture": {**FIXTURE, "metal": "auto"}, "pgs": pgs, **FAMILIES["clean_port_xfm_bs"]})
    with pytest.raises(ValidationError, match="pgs ties its strips"):
        gen.config_model.model_validate({"process_profile": PROFILE, "ground_fixture": {**FIXTURE, "metal": "4"}, "pgs": pgs, **FAMILIES["clean_port_xfm_bs"]})
    gen.config_model.model_validate({"process_profile": PROFILE, "ground_fixture": {**FIXTURE, "metal": "M1"}, "pgs": pgs, **FAMILIES["clean_port_xfm_bs"]})


@pytest.mark.parametrize("generator_id", sorted(FAMILIES))
def test_the_footprint_reads_the_fixture_metal_from_the_manifest(tmp_path, generator_id):
    """The same device with the fixture on M1 and on the auto metal has the same footprint: the manifest says which layer
    holds the fixture, and that layer is excluded whole."""
    _, _, default = generate(generator_id, tmp_path / "a", FIXTURE)
    _, _, moved = generate(generator_id, tmp_path / "b", {**FIXTURE, "metal": "auto"})
    assert fp.recorded_fixture_metal(moved.gds_path) == moved.fixture_metal != "M1"
    assert fp.recorded_fixture_metal(default.gds_path) == "M1"
    assert fp.footprint(moved.gds_path, profile=PROFILE, generator=generator_id) == fp.footprint(default.gds_path, profile=PROFILE, generator=generator_id)
    (tmp_path / "b" / "geometry_manifest.json").unlink()                          # an older row: no manifest, the fixture conductor assumed
    assert fp.recorded_fixture_metal(moved.gds_path) is None
