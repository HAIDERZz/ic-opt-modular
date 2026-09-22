"""Local straight extension preserves the existing winding and port contracts."""

import json
from copy import deepcopy

import klayout.db as kdb
import pytest

from ic_opt.em.pcell import generator_plugin as gp
from ic_opt.em.pcell._pcell_core import (
    Cell,
    PortError,
    _nm,
    metal_layer,
    process_rule_context,
    via_layer,
    vias,
)
from ic_opt.em.pcell._pcell_ind_sym import ind_sym
from ic_opt.em.pcell._pcell_straight_extension import (
    extend_straight_x,
)
from ic_opt.em.pcell._pcell_xfm_bs import xfm_bs
from ic_opt.em.pcell._pcell_xfm_ms import xfm_ms
from ic_opt.em.pcell.fixture import (
    GroundFixtureConfig,
    _drawing_bbox_um,
)
from tests.ic_opt.pcell.conftest import requires_profile
from tests.ic_opt.pcell.test_clean_port_generator_plugin import (
    _ind_sym_config_dict,
    _xfm_bs_config_dict,
    _xfm_ms_config_dict,
)

pytestmark = requires_profile("n28_1p10m")


def _snapshot(cell):
    return (
        cell.name, deepcopy(cell.params), list(cell.flat_shapes()),
        list(cell.flat_labels()), deepcopy(cell.emx_ports),
        cell.instantiation_log(),
    )


def _region(cell, layer):
    return kdb.Region([
        kdb.Polygon([kdb.Point(x, y) for x, y in points])
        for drawn_layer, points in cell.flat_shapes() if drawn_layer == layer
    ]).merged()


def _assert_rigid_cuts(before, after, layers, half_nm, center_nm=0):
    old = [(layer, pts) for layer, pts in before.flat_shapes() if layer in layers]
    new = [(layer, pts) for layer, pts in after.flat_shapes() if layer in layers]
    assert old, "this case must exercise real process vias"
    assert len(old) == len(new)
    for (layer, points), (new_layer, new_points) in zip(old, new, strict=True):
        shift = half_nm if min(x for x, _ in points) > center_nm else -half_nm
        assert new_layer == layer
        assert new_points == [(x + shift, y) for x, y in points]


def test_zero_preserves_identity_and_historical_pcell_outputs():
    cell = Cell("unchanged", "test", {})
    assert extend_straight_x(cell, 0.0) is cell
    for factory, kwargs in [
        (ind_sym, {"OD": 60.0, "NT": 3, "CT_ME": "7"}),
        (xfm_bs, {"OD_P": 90.0, "OD_S": 76.0, "CENTER_SPACING": 9.0}),
        (xfm_ms, {"NT_M": 3, "CENTER_SPACING": 9.0}),
    ]:
        assert _snapshot(factory(**kwargs)) == _snapshot(
            factory(**kwargs, STRAIGHT_EXTENSION=0.0)
        )


@pytest.mark.parametrize("profile,ct", [("n28_1p10m", "9"), ("n65_1p9m", "8")])
@pytest.mark.parametrize("turns,with_ct", [(2, False), (3, True), (4, True)])
def test_ind_extension_preserves_process_vias_ct_and_port_order(profile, ct, turns, with_ct):
    process = process_rule_context(profile)
    kwargs = dict(
        OD=160.0, W=6.0, S=4.0, OPENING=8.0, LEAD=20.0, NT=turns,
        TOP_ME="AP", CT_ME=ct if with_ct else None, process=process,
    )
    before = ind_sym(**kwargs)
    baseline = _snapshot(before)
    after = ind_sym(**kwargs, STRAIGHT_EXTENSION=20.0)
    via_layers = {tuple(v.drawing) for v in process.adapter.profile.layer_catalog.vias.values()}
    _assert_rigid_cuts(before, after, via_layers, 10000)
    xmin, ymin, xmax, ymax = _drawing_bbox_um(before)
    assert _drawing_bbox_um(after) == (xmin - 10.0, ymin, xmax + 10.0, ymax)
    assert [p["name"] for p in before.emx_ports] == [p["name"] for p in after.emx_ports]
    for old, new in zip(before.emx_ports, after.emx_ports, strict=True):
        restored = deepcopy(new)
        # port contract 2026-09-21: point_nm/lead_zone_nm are the SAME
        # integer shift extend_straight_x applies to every shape/label
        # (half_nm below is exactly that shift, in nm, on whichever side
        # of the local X centre this port's un-extended point already
        # sat -- the same sign rule _assert_rigid_cuts above pins).
        shift_um = -10.0 if old["label_xy_um"][0] > 0 else 10.0
        shift_nm = _nm(shift_um)
        restored["label_xy_um"][0] += shift_um
        restored["point_nm"][0] += shift_nm
        restored["lead_zone_nm"][0] += shift_nm
        restored["lead_zone_nm"][2] += shift_nm
        assert restored == old
    assert len(list(after.flat_labels())) == len(list(before.flat_labels()))
    assert after.params["STRAIGHT_EXTENSION"] == 20.0
    assert _snapshot(before) == baseline
    for conductor in process.adapter.profile.layer_catalog.conductors.values():
        layer = tuple(conductor.drawing)
        old_region, new_region = _region(before, layer), _region(after, layer)
        assert old_region.count() == new_region.count()
        assert sum(p.holes() for p in old_region.each()) == sum(p.holes() for p in new_region.each())


def test_bs_uses_each_windings_local_center_and_preserves_tap_vias():
    kwargs = dict(
        OD_P=100.0, OD_S=72.0, W_P=6.0, W_S=4.0,
        OPENING_P=8.0, OPENING_S=7.0, CENTER_SPACING=40.0,
        PRI_ME="10", SEC_ME="9", CT_P_ME="8", CT_S_ME="7",
    )
    before = xfm_bs(**kwargs)
    after = xfm_bs(**kwargs, STRAIGHT_EXTENSION=18.0)
    # The global x=0 axis intersects the smaller winding's chamfer; only
    # its own +/-20um local centre gives a legal horizontal cut.
    for layer in [metal_layer(10), metal_layer(9)]:
        old_box, new_box = _region(before, layer).bbox(), _region(after, layer).bbox()
        assert (new_box.left, new_box.right) == (old_box.left - 9000, old_box.right + 9000)
        assert (new_box.bottom, new_box.top) == (old_box.bottom, old_box.top)
    for index, center_nm in [(0, -20000), (1, 20000)]:
        _assert_rigid_cuts(before.insts[index].cell, after.insts[index].cell,
                           {via_layer(i) for i in range(1, 11)}, 9000, center_nm)
    assert [p["name"] for p in after.emx_ports] == ["P1", "N1", "P2", "N2", "CTP", "CTS"]
    assert after.params["STRAIGHT_EXTENSION"] == 18.0


@pytest.mark.parametrize("turns,ct_p,ct_s", [(2, "8", None), (3, "8", "7"), (4, None, None)])
def test_ms_extends_single_and_multi_about_their_own_centers(turns, ct_p, ct_s):
    """NT_M=2 takes the compact multi branch, NT_M>=3 the generic ind_sym one.
    (NT_M=4 has no CTP: its tap stack would land on the inner M10 turn.)"""
    process = process_rule_context("n28_1p10m")
    kwargs = dict(
        OD_S=110.0, OD_M=90.0, W_S=6.0, W_M=4.0, OPENING_S=8.0, OPENING_M=6.0,
        NT_M=turns, S_M=3.0, CENTER_SPACING=30.0, SINGLE_ME="AP", MULTI_ME="10",
        CT_P_ME=ct_p, CT_S_ME=ct_s, process=process,
    )
    before = xfm_ms(**kwargs)
    baseline = _snapshot(before)
    after = xfm_ms(**kwargs, STRAIGHT_EXTENSION=16.0)
    catalog = process.adapter.profile.layer_catalog
    for name in ("AP", "M10"):
        layer = tuple(catalog.conductors[name].drawing)
        old_box, new_box = _region(before, layer).bbox(), _region(after, layer).bbox()
        assert (new_box.left, new_box.right) == (old_box.left - 8000, old_box.right + 8000)
        assert (new_box.bottom, new_box.top) == (old_box.bottom, old_box.top)
    via_layers = {tuple(v.drawing) for v in catalog.vias.values()}
    # an untapped single turn is via-free; the multi always has bridge vias
    for index, center_nm in [(0, -15000), (1, 15000)][0 if ct_p else 1:]:
        _assert_rigid_cuts(before.insts[index].cell, after.insts[index].cell,
                           via_layers, 8000, center_nm)
    assert [p["name"] for p in after.emx_ports] == [p["name"] for p in before.emx_ports]
    for old, new in zip(before.emx_ports, after.emx_ports, strict=True):
        center = -15.0 if old["name"] in ("P1", "N1", "CTP") else 15.0
        shift = 8.0 if old["label_xy_um"][0] > center else -8.0
        assert new["label_xy_um"] == [old["label_xy_um"][0] + shift, old["label_xy_um"][1]]
    for conductor in catalog.conductors.values():
        layer = tuple(conductor.drawing)
        old_region, new_region = _region(before, layer), _region(after, layer)
        assert old_region.count() == new_region.count()
        assert sum(p.holes() for p in old_region.each()) == sum(p.holes() for p in new_region.each())
    assert after.params["STRAIGHT_EXTENSION"] == 16.0
    assert _snapshot(before) == baseline


def test_ground_fixture_is_rebuilt_after_extension():
    fixture = GroundFixtureConfig(
        inner_margin_um=5.0, ring_width_um=3.0, stub_width_um=2.0,
        stub_length_um=5.0, stub_chamfer_um=0.5,
    )
    kwargs = dict(OD=60.0, NT=3, CT_ME="7", ground_fixture=fixture)
    before = ind_sym(**kwargs)
    after = ind_sym(**kwargs, STRAIGHT_EXTENSION=12.0)
    a, b = _region(before, metal_layer(1)).bbox(), _region(after, metal_layer(1)).bbox()
    assert (b.left, b.right, b.bottom, b.top) == (a.left - 6000, a.right + 6000, a.bottom, a.top)
    assert [p["reference"] for p in after.emx_ports] == ["G01", "G02", "G03"]
    labels = {(text, point) for _, _, text, point in after.flat_labels()}
    assert all((p["reference"], tuple(round(v * 1000) for v in p["label_xy_um"])) in labels
               for p in after.emx_ports)


@pytest.mark.parametrize("extension", [-0.01, 0.005, float("inf"), float("nan")])
def test_invalid_extension_is_rejected(extension):
    with pytest.raises(PortError, match="STRAIGHT_EXTENSION"):
        extend_straight_x(Cell("invalid", "test", {}), extension)


@pytest.mark.parametrize("touch", [False, True])
def test_sloped_edge_crossing_or_touching_cut_is_rejected(touch):
    cell = Cell("diagonal", "test", {})
    cell.add_polygon(metal_layer(9), [(0.0 if touch else -1.0, 0.0), (1.0, 1.0), (2.0, 0.0)])
    with pytest.raises(PortError, match="horizontal"):
        extend_straight_x(cell, 10.0)


@pytest.mark.parametrize("x0", [-0.2, 0.0])
def test_via_crossing_or_touching_cut_is_rejected(x0):
    cell = Cell("cut", "test", {})
    cell.add_rect(via_layer(8), x0, 1.0, x0 + 0.4, 1.4)
    with pytest.raises(PortError, match="via"):
        extend_straight_x(cell, 10.0)


def test_metal_only_via_landing_cannot_be_stretched_through_cut():
    cell = Cell("landing", "test", {})
    cell.inst(vias(Length=2.0, Width=2.0, TOP_ME=9, BTM_ME=9), (-1.0, 3.0), "R0")
    with pytest.raises(PortError, match="rigid"):
        extend_straight_x(cell, 10.0)


_GENERATOR_CASES = [
    (gp.CleanPortIndSymGenerator, _ind_sym_config_dict),
    (gp.CleanPortXfmBsGenerator, _xfm_bs_config_dict),
    (gp.CleanPortXfmMsGenerator, _xfm_ms_config_dict),
]


@pytest.mark.parametrize("generator,payload_factory", _GENERATOR_CASES)
def test_generator_default_and_explicit_zero_keep_outputs_equal(tmp_path, generator, payload_factory):
    payload = payload_factory()
    default = generator.config_model.model_validate(payload)
    explicit = generator.config_model.model_validate({**payload, "straight_extension_um": 0.0})
    assert default.model_dump(mode="json") == explicit.model_dump(mode="json")
    assert "straight_extension_um" not in default.model_dump()
    before = generator().generate(default, outdir=tmp_path / "default", gds_name="device.gds")
    after = generator().generate(explicit, outdir=tmp_path / "zero", gds_name="device.gds")
    assert before.gds_path.read_bytes() == after.gds_path.read_bytes()
    before_manifest = json.loads(before.manifest_path.read_text())
    after_manifest = json.loads(after.manifest_path.read_text())
    assert before_manifest["geometry"] == after_manifest["geometry"]
    assert before_manifest["suggested_emx_ports"] == after_manifest["suggested_emx_ports"]


@pytest.mark.parametrize("generator,payload_factory", _GENERATOR_CASES)
def test_generator_positive_extension_is_recorded_in_manifest(tmp_path, generator, payload_factory):
    config = generator.config_model.model_validate({
        **payload_factory(), "straight_extension_um": 20.0,
    })
    result = generator().generate(config, outdir=tmp_path, gds_name="device.gds")
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["geometry"]["config"]["straight_extension_um"] == 20.0
    assert manifest["suggested_emx_ports"]
