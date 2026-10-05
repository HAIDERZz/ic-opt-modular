"""T19.2 (``docs/refactor/T19_2_FIXTURE_METAL_SHARED_SPEC.md``): ``ground_fixture.metal_rule`` -- ``free`` (absent, the
default) is T19.1 unchanged; ``shared`` lets the fixture's metal carry the device's internal shapes but no port lead. Every
build records the device's box before the fixture (``geometry.device_bbox_um``) and the footprint reads it, with the
by-layer measurement for a row that has none; the DRC gate keeps its by-layer max_width exemption; a fixture shape that
would touch a device shape on the shared metal is refused."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("klayout.db")

import klayout.db as kdb
from pydantic import ValidationError

from ic_opt.em.pcell import fixture as fixture_module
from ic_opt.em.pcell import footprint as fp
from ic_opt.em.pcell import get_generator
from ic_opt.em.pcell._pcell_core import PortError
from ic_opt.em.pcell.connectivity import nets
from ic_opt.em.pcell.drc_audit import (
    audit_gds,
    expected_conductors,
    fixture_exemptions,
    product_scope_record,
)
from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter
from ic_opt.em.pcell.stack import use_stack
from tests.ic_opt.pcell.test_fixture_metal import FAMILIES, PROFILE, generate, layers_of
from tests.ic_opt.pcell.test_golden import CASES, FIXTURE

ADAPTER = get_geometry_rule_adapter(PROFILE)
SHARED_AUTO = {**FIXTURE, "metal": "auto", "metal_rule": "shared"}
PGS = {"strip_width_um": 1.0, "strip_spacing_um": 2.0, "margin_um": 5.0}


def drawing(metal: str) -> tuple[int, int]:
    return tuple(ADAPTER.layer(metal).drawing)


def pin(metal: str) -> tuple[int, int]:
    return tuple(ADAPTER.layer(metal).pin)


def content(gds: Path) -> tuple[dict, dict]:
    """Per (layer, datatype): the merged region of its shapes, and the set of its labels (text, x, y)."""
    layout = kdb.Layout()
    layout.read(str(gds))
    top = layout.top_cell()
    regions, texts = {}, {}
    for index in layout.layer_indexes():
        info = layout.get_info(index)
        key = (info.layer, info.datatype)
        region = kdb.Region()
        for item in top.begin_shapes_rec(index).each():
            shape = item.shape()
            if shape.is_text():
                texts.setdefault(key, set()).add((shape.text_string, shape.text.x, shape.text.y))
            else:
                region.insert(shape.polygon.transformed(item.trans()))
        if not region.is_empty():
            regions[key] = region.merged()
    return regions, texts


def device_box(gds: Path, fixture: tuple[int, int]) -> list[float]:
    """The box (x0, y0, x1, y1, um) of every shape not on the ``fixture`` layer: what ``footprint.measure`` measures."""
    regions, _texts = content(gds)
    box = kdb.Box()
    for layer, region in regions.items():
        if layer != fixture:
            box += region.bbox()
    return [round(v * 0.001, 3) for v in (box.left, box.bottom, box.right, box.top)]


def build_case(name: str, outdir: Path, **fixture):
    """The golden case ``name`` (test_golden's builds, every family) with ``fixture`` merged into its ground fixture."""
    generator_id, config = CASES[name]
    gen = get_generator(generator_id, plugin_module="builtin:clean_port")
    fx = {**FIXTURE, **fixture}
    if generator_id == "clean_port_xfm_ms":
        fx.update({"stub_width_um": 6.0, "stub_width_by_port_um": {"P2": 3.0, "N2": 3.0}})
    model = gen.config_model.model_validate({**config, "process_profile": PROFILE, "ground_fixture": fx})
    return gen, model, gen.generate(model, outdir=outdir, gds_name=f"{name}.gds")


def test_auto_shared_puts_the_same_fixture_on_the_crossunders_metal(tmp_path):
    """On the demo stack the ms primary is on M6, the secondary on M5 and its crossunder on M4: ``auto`` under ``free``
    takes M3 (T19.1), under ``shared`` M4. The shared build is the free build with the fixture moved from M3 to M4,
    shape for shape and label for label: M4 holds the crossunder and the fixture, M3 and the bottom metal nothing, every
    other layer is unchanged, the G pins are on M4's pin layer, and the manifest says ``metal_rule: shared``."""
    gen, model, shared = generate("clean_port_xfm_ms", tmp_path / "shared", SHARED_AUTO)
    _, _, free = generate("clean_port_xfm_ms", tmp_path / "free", {**FIXTURE, "metal": "auto"})
    assert (shared.fixture_metal, free.fixture_metal) == ("M4", "M3")
    s_regions, s_texts = content(shared.gds_path)
    f_regions, f_texts = content(free.gds_path)
    assert drawing("M1") not in s_regions and drawing("M3") not in s_regions
    assert (s_regions[drawing("M4")] ^ (f_regions[drawing("M4")] + f_regions[drawing("M3")]).merged()).is_empty()
    moved = {drawing("M3"), drawing("M4")}
    assert set(s_regions) - moved == set(f_regions) - moved
    assert all((s_regions[layer] ^ f_regions[layer]).is_empty() for layer in set(s_regions) - moved)
    assert s_texts[pin("M4")] == f_texts[pin("M3")] and {t for t, _x, _y in s_texts[pin("M4")]} == {"G01", "G02", "G03", "G04"}
    assert {k: v for k, v in s_texts.items() if k != pin("M4")} == {k: v for k, v in f_texts.items() if k != pin("M3")}
    assert layers_of(shared.gds_path)[pin("M4")] == len(model.port_order)
    manifest = json.loads(shared.manifest_path.read_text())["geometry"]
    assert manifest["config"]["ground_fixture"]["metal"] == "auto" and manifest["config"]["ground_fixture"]["metal_rule"] == "shared"
    assert manifest["fixture_metal"] == "M4" and manifest["fixture_layer"] == list(drawing("M4"))
    labels = nets(shared.gds_path, PROFILE)
    assert {labels[g] for g in ("G01", "G02", "G03", "G04")}.isdisjoint(labels[p] for p in model.port_order)


@pytest.mark.parametrize("generator_id", ["clean_port_xfm_ms", "clean_port_xfm_bs"])
def test_shared_refuses_only_a_metal_that_carries_a_port_lead(tmp_path, generator_id):
    """M6 carries the primary's leads and M5 the secondary's: refused under ``shared`` as under ``free``, the ports and the
    rule in force named, before any file is written. M4 -- the ms crossunder's metal, nothing for bs -- is accepted under
    ``shared``; for ms it is refused under ``free`` (a device shape on it)."""
    for metal, ports in (("M6", "P1, N1"), ("5", "P2, N2")):
        out = tmp_path / f"{metal}-shared"
        with pytest.raises(PortError, match=rf"carries the lead of port {ports}: EMX refuses.*the rule in force: 'shared'"):
            generate(generator_id, out, {**FIXTURE, "metal": metal, "metal_rule": "shared"})
        assert not list(out.glob("*.gds"))
    _, _, ok = generate(generator_id, tmp_path / "M4-shared", {**FIXTURE, "metal": "M4", "metal_rule": "shared"})
    assert ok.fixture_metal == "M4"
    if generator_id == "clean_port_xfm_ms":
        with pytest.raises(PortError, match=r"carries device shapes on layer .*under metal_rule 'free' the metal must hold nothing "
                                            r"of the device; 'shared' allows internal shapes"):
            generate(generator_id, tmp_path / "M4-free", {**FIXTURE, "metal": "M4"})


@pytest.mark.parametrize("rule", ["free", "shared"])
def test_a_rule_without_a_metal_is_refused_at_validation(rule):
    """``metal_rule`` says how a metal is chosen; with ``metal`` absent or null the default fixture chooses none: refused,
    either spelling, naming both fields. So is a shielded device's rule without a metal, and its ``auto``."""
    gen = get_generator("clean_port_xfm_ms", plugin_module="builtin:clean_port")
    base = {"process_profile": PROFILE, **FAMILIES["clean_port_xfm_ms"]}
    for fixture in ({**FIXTURE, "metal_rule": rule}, {**FIXTURE, "metal": None, "metal_rule": rule}):
        with pytest.raises(ValidationError, match=rf"ground_fixture.metal_rule '{rule}' needs ground_fixture.metal"):
            gen.config_model.model_validate({**base, "ground_fixture": fixture})
        with pytest.raises(ValidationError, match="needs ground_fixture.metal"):
            gen.config_model.model_validate({**base, "ground_fixture": fixture, "pgs": PGS})
    with pytest.raises(ValidationError, match="pgs ties its strips"):
        gen.config_model.model_validate({**base, "ground_fixture": {**FIXTURE, "metal": "auto", "metal_rule": rule}, "pgs": PGS})


def test_the_rule_spelled_out_is_the_rule_left_out(tmp_path):
    """``metal_rule: free`` spelled out serializes like the absent field: the same manifest, config block included, and
    the same GDS bytes as T19.1's ``metal: auto`` build. The rule is read in any case; an unknown rule is refused."""
    _, model, spelled = generate("clean_port_xfm_ms", tmp_path / "spelled", {**FIXTURE, "metal": "auto", "metal_rule": "free"})
    _, _, plain = generate("clean_port_xfm_ms", tmp_path / "plain", {**FIXTURE, "metal": "auto"})
    assert "metal_rule" not in model.model_dump()["ground_fixture"]
    assert spelled.manifest_path.read_text() == plain.manifest_path.read_text()
    assert spelled.gds_path.read_bytes() == plain.gds_path.read_bytes()
    assert spelled.emx_ports_path.read_text() == plain.emx_ports_path.read_text()
    gen = get_generator("clean_port_xfm_ms", plugin_module="builtin:clean_port")
    base = {"process_profile": PROFILE, **FAMILIES["clean_port_xfm_ms"]}
    loud = gen.config_model.model_validate({**base, "ground_fixture": {**FIXTURE, "metal": "auto", "metal_rule": " Shared "}})
    assert loud.model_dump()["ground_fixture"]["metal_rule"] == "shared"
    with pytest.raises(ValidationError, match="'free' or 'shared'"):
        gen.config_model.model_validate({**base, "ground_fixture": {**FIXTURE, "metal": "auto", "metal_rule": "loose"}})


@pytest.mark.parametrize("fixture", [{}, {"metal": "auto"}], ids=["absent", "auto"])
@pytest.mark.parametrize("name", sorted(CASES))
def test_the_recorded_box_is_the_by_layer_footprint(tmp_path, name, fixture):
    """Every family (the golden cases), the fixture on the bottom metal and on the ``auto`` metal: the manifest records the
    device's box, the result carries it, it is the box of every shape off the fixture's layer, and the footprint read from
    it equals the by-layer measurement of the same GDS to the last digit."""
    gen, _model, result = build_case(name, tmp_path, **fixture)
    recorded = json.loads(result.manifest_path.read_text())["geometry"]["device_bbox_um"]
    layer = fp.fixture_layer(PROFILE, gen.generator_id, metal=result.fixture_metal)
    assert recorded == list(result.device_bbox_um) == list(fp.recorded_device_bbox(result.gds_path)) == device_box(result.gds_path, layer)
    assert fp.footprint(result.gds_path, profile=PROFILE, generator=gen.generator_id) == fp.measure(result.gds_path, layer)


def test_the_footprint_reads_the_record_and_falls_back_without_it(tmp_path, monkeypatch):
    """The ms ``shared`` build has the ``free`` build's footprint (the same device; its fixture's layer also holds the
    crossunder). With a record the GDS is not measured; without the manifest, or with a box that is not one, the footprint
    is the by-layer measurement again (a row of an older generation)."""
    _, _, shared = generate("clean_port_xfm_ms", tmp_path / "shared", SHARED_AUTO)
    _, _, free = generate("clean_port_xfm_ms", tmp_path / "free", {**FIXTURE, "metal": "auto"})
    _, _, default = generate("clean_port_xfm_ms", tmp_path / "default", FIXTURE)
    assert shared.device_bbox_um == free.device_bbox_um == default.device_bbox_um
    with monkeypatch.context() as patch:
        patch.setattr(fp, "measure", lambda *a, **k: pytest.fail("a recorded box is read, not measured"))
        boxes = [fp.footprint(r.gds_path, profile=PROFILE, generator="clean_port_xfm_ms") for r in (shared, free, default)]
    assert boxes[0] == boxes[1] == boxes[2]
    m1 = fp.fixture_layer(PROFILE, "clean_port_xfm_ms")
    manifest = default.manifest_path
    data = json.loads(manifest.read_text())
    data["geometry"]["device_bbox_um"] = [1.0, 2.0]
    manifest.write_text(json.dumps(data))
    assert fp.recorded_device_bbox(default.gds_path) is None
    assert fp.footprint(default.gds_path, profile=PROFILE, generator="clean_port_xfm_ms") == fp.measure(default.gds_path, m1) == boxes[2]
    manifest.unlink()
    assert fp.recorded_device_bbox(default.gds_path) is None and fp.recorded_fixture_metal(default.gds_path) is None
    assert fp.footprint(default.gds_path, profile=PROFILE, generator="clean_port_xfm_ms") == fp.measure(default.gds_path, m1) == boxes[2]


def test_the_drc_gate_on_a_shared_build(tmp_path):
    """The ms ``shared`` build passes the product-scope verdict with max_width exempted on M4, where the ring and the
    crossunder both are; with the exemption left on M1 it fails on the ring's width. Under ``shared`` the device's own
    shapes may be on M4, so a spacing finding there is not known to lie between two fixture shapes and still counts
    (T19.5 exempts spacing under ``free`` only): a min-space violation drawn on M4, and a device-like shape drawn 0.05 um
    inside the ring's inner edge, both fail the gate with the build's own exemptions in place."""
    gen, model, result = generate("clean_port_xfm_ms", tmp_path, SHARED_AUTO)
    assert (result.fixture_metal, result.fixture_metal_rule) == ("M4", "shared")
    build = fixture_exemptions(PROFILE, result.fixture_metal, result.fixture_metal_rule)
    assert build == fixture_exemptions(PROFILE, "M4") == frozenset({("max_width", "M4")})
    with use_stack(PROFILE):
        report = audit_gds(result.gds_path, PROFILE)
        expected = expected_conductors(gen, model)
        assert "M4" in expected                                       # the crossunder: the device's own conductor there
        assert product_scope_record(report, expected, ignore_findings=build)["outcome"] == "pass"
        stale = product_scope_record(report, expected, ignore_findings=fixture_exemptions(PROFILE))
        assert stale["outcome"] != "pass" and any(v["kind"] == "max_width" and v["layer"] == "M4" for v in stale["violations"])
    regions, _texts = content(result.gds_path)
    ring = max(regions[drawing("M4")].each(), key=lambda polygon: polygon.area()).bbox()     # the ring and its stubs: one polygon
    inner_left, inner_top = ring.left + 20_000, ring.top - 20_000                           # FIXTURE's ring_width_um, 20 um
    for name, boxes in (("close", [kdb.Box(-300_000, 400_000, -200_000, 405_000),          # two 5 um strips 0.05 um apart
                                   kdb.Box(-300_000, 405_050, -200_000, 410_050)]),        # (M4 min space 0.1 um)
                        ("near_ring", [kdb.Box(inner_left + 50, inner_top - 3_000, inner_left + 2_050, inner_top - 1_000)])):
        layout = kdb.Layout()
        layout.read(str(result.gds_path))
        li = layout.layer(*drawing("M4"))
        for box in boxes:
            layout.top_cell().shapes(li).insert(box)
        path = tmp_path / f"{name}.gds"
        layout.write(str(path))
        with use_stack(PROFILE):
            record = product_scope_record(audit_gds(path, PROFILE), expected, ignore_findings=build)
        assert record["outcome"] != "pass" and any(v["layer"] == "M4" and v["kind"] == "min_space" for v in record["violations"]), name


@pytest.mark.parametrize("name", sorted(CASES))
def test_no_shared_fixture_touches_the_device(tmp_path, name):
    """Every golden case built with ``auto`` + ``shared``: the G pins are on their own net, apart from every port's."""
    _, model, result = build_case(name, tmp_path, metal="auto", metal_rule="shared")
    labels = nets(result.gds_path, PROFILE)
    grounds = {labels[f"G{i:02d}"] for i in range(1, len(model.port_order) + 1)}
    assert grounds.isdisjoint(labels[p] for p in model.port_order), (result.fixture_metal, labels)


def test_a_stub_that_would_touch_a_device_shape_on_the_shared_metal_is_refused(tmp_path, monkeypatch):
    """xfm_il with 2 um leads: each secondary lead runs under the primary on M5 and ends in a 5 um via pad that reaches
    3 um past the port, where that port's stub starts. ``auto`` + ``shared`` takes M5 (no port lead on it) and the stubs
    would overlap the pads -- a short between the windings and their reference that no DRC rule sees. Refused, naming the
    metal and the place; ``free`` builds it on M3, and with 20 um leads ``shared`` builds it on M5. Without the check the
    build would short the windings to the ring."""
    short = {"primary_lead_length_um": 2.0, "secondary_lead_length_um": 2.0}
    gen = get_generator("clean_port_xfm_il", plugin_module="builtin:clean_port")
    config = {**CASES["xfm_il_nt3"][1], **short, "process_profile": PROFILE}
    with pytest.raises(PortError, match=r"under metal_rule 'shared' the ring and stubs on M5 would touch 2 device shape\(s\).*a short"):
        gen.generate(gen.config_model.model_validate({**config, "ground_fixture": SHARED_AUTO}), outdir=tmp_path / "a", gds_name="x.gds")
    assert not list((tmp_path / "a").glob("*.gds"))
    free = gen.generate(gen.config_model.model_validate({**config, "ground_fixture": {**FIXTURE, "metal": "auto"}}), outdir=tmp_path / "b", gds_name="x.gds")
    assert free.fixture_metal == "M3"
    _, _, long_leads = build_case("xfm_il_nt3", tmp_path / "c", metal="auto", metal_rule="shared")
    assert long_leads.fixture_metal == "M5"
    monkeypatch.setattr(fixture_module, "_refuse_contact", lambda *a, **k: None)
    unchecked = gen.generate(gen.config_model.model_validate({**config, "ground_fixture": SHARED_AUTO}), outdir=tmp_path / "d", gds_name="x.gds")
    labels = nets(unchecked.gds_path, PROFILE)
    assert labels["G01"] in {labels[p] for p in ("P1", "N1", "P2", "N2")}


def test_the_rule_in_the_pcell_dataclass():
    """``GroundFixtureConfig`` as a plugin generator builds it: ``shared`` without a metal and an unknown rule are refused;
    in reference mode ``shared`` refuses the lead's metal and accepts another; the cell records the device's box, taken
    before the fixture (the body to x = 20, the lead on to x = 30)."""
    from ic_opt.em.pcell._pcell_core import Cell, finalize_emx_ports, metal_layer
    from ic_opt.em.pcell.fixture import GroundFixtureConfig, add_ground_fixture

    def cell():
        body = Cell("body", "test", {})
        body.add_rect(metal_layer(6), -20.0, -20.0, 20.0, 20.0)
        lead = Cell("lead", "test", {})
        lead.add_rect(metal_layer(6), 0.0, 0.0, 10.0, 4.0)
        lead.add_emx_port(name="P1", logical_name="P1", metal=6, label_layer=(66, 1), x_um=10.0, y_um=2.0, lead_zone_um=(0.0, 0.0, 10.0, 4.0))
        body.inst(lead, (20.0, 0.0), "R0")
        body.emx_ports = finalize_emx_ports(body)
        return body

    def config(**kw):
        return GroundFixtureConfig(inner_margin_um=15.0, ring_width_um=10.0, stub_width_um=4.0, stub_length_um=2.0, stub_chamfer_um=0.0, **kw)

    with pytest.raises(PortError, match="metal_rule 'shared' says how the fixture's metal is chosen"):
        add_ground_fixture(cell(), config(metal_rule="shared"))
    with pytest.raises(PortError, match=r"metal_rule 'loose' is none of \['free', 'shared'\]"):
        add_ground_fixture(cell(), config(metal=4, metal_rule="loose"))
    with pytest.raises(PortError, match="carries the lead of port P1"):
        add_ground_fixture(cell(), config(metal=6, metal_rule="shared"))
    drawn = cell()
    add_ground_fixture(drawn, config(metal=4, metal_rule="shared"))
    assert drawn.fixture_metal == "M4" and drawn.device_bbox_um == (-20.0, -20.0, 30.0, 20.0)
    default = cell()
    add_ground_fixture(default, config())
    assert default.fixture_metal == "M1" and default.device_bbox_um == (-20.0, -20.0, 30.0, 20.0)
