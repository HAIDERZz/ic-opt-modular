"""N-65 (``docs/refactor/N65_STUB_CHAMFER_SPEC.md``): two chamfered stubs that meet leave no sharp wedge in the ring's opening.

When two neighbouring stubs on one side of the ground ring would leave, between their chamfered outlines at the ring's
inner edge, less than the fixture metal's minimum spacing, both facing chamfers are shortened to the largest value that
keeps that gap at the minimum (on the manufacturing grid), never below 0; the result and the manifest record the chamfers
drawn, and only then. Everything else builds byte for byte as before -- here against the same build with the rule taken
out (``fixture_min_space_um`` answering None, what every build did before N-65).

The N-65 geometry is the tapped transformer of the author-spec skill's joint example (demo_6m: xfm_bs, windings on M6 /
M5, both taps on M4, secondary width 6.5 um) with ``stub_chamfer_um: 2``: the primary tap's stub, as wide as the primary,
runs on the right between the P2 and N2 stubs, whose inner edges sit 7.5 um off the centre line. At the ring that leaves
3.5 - w/2 um on either side of it for a primary width w: a 0.5 um flat at 6, a shared corner at 7, an overlap above. The
fixture is on M1 (``metal`` absent), minimum spacing 0.1 um, where the DRC gate counts spacing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("klayout.db")

import klayout.db as kdb

from ic_opt.em.pcell import fixture as fx
from ic_opt.em.pcell import get_generator
from ic_opt.em.pcell._pcell_core import PortError, metal_layer
from ic_opt.em.pcell._pcell_xfm_bs import xfm_bs
from ic_opt.em.pcell.fixture import GroundFixtureConfig, _facing_chamfers
from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter
from ic_opt.spec import Device
from ic_opt.stages.em_chain import DrcRefused, _audit
from tests.ic_opt.pcell.test_golden import CASES
from tests.ic_opt.pcell.test_golden import FIXTURE as GOLDEN_FIXTURE

PROFILE = "demo_6m"
TAPS = ["P1", "N1", "P2", "N2", "CTP", "CTS"]
N65 = {"port_order": TAPS, "primary_outer_diameter_um": 73.0, "secondary_outer_diameter_um": 58.0, "primary_opening_um": 8.75,
       "secondary_opening_um": 7.5, "primary_lead_length_um": 20.75, "secondary_lead_length_um": 21.5, "center_spacing_um": 0.0,
       "primary_metal": "6", "secondary_metal": "5", "ct_primary_metal": "4", "ct_secondary_metal": "4", "secondary_width_um": 6.5}
FIXTURE = {"inner_margin_um": 4.0, "ring_width_um": 70.0, "stub_length_um": 2.0, "stub_chamfer_um": 2.0}
DEVICE = Device(id="xfmr", generator="clean_port_xfm_bs", profile=PROFILE, ports=TAPS)
M1_SPACE_NM = 100


def generate(out: Path, width: float, fixture: dict | None = None, generator_id: str = "clean_port_xfm_bs", config: dict | None = None):
    gen = get_generator(generator_id, plugin_module="builtin:clean_port")
    base = {**N65, "primary_width_um": width} if config is None else config
    model = gen.config_model.model_validate({**base, "process_profile": PROFILE, "ground_fixture": dict(FIXTURE if fixture is None else fixture)})
    return gen, model, gen.generate(model, outdir=out, gds_name="xfmr.gds")


def gate(gen, model, result) -> dict | None:
    """The pcell stage's DRC gate (``stages.em_chain._audit``) on a build: None when it passes, its record when it refuses."""
    try:
        _audit(DEVICE, gen, model, result.gds_path, fixture_metal=result.fixture_metal, fixture_metal_rule=result.fixture_metal_rule)
    except DrcRefused as refused:
        return refused.record
    return None


def written(result) -> tuple[bytes, str, str]:
    """What a build leaves on disk: the GDS bytes, the port file, the manifest."""
    return result.gds_path.read_bytes(), result.emx_ports_path.read_text(), result.manifest_path.read_text()


def without_the_rule(monkeypatch):
    """The fixture as before N-65: no minimum spacing is known, so no chamfer is shortened."""
    monkeypatch.setattr(fx, "fixture_min_space_um", lambda conductor, process: None)


def m1(gds: Path) -> kdb.Region:
    layout = kdb.Layout()
    layout.read(str(gds))
    li = layout.layer(*get_geometry_rule_adapter(PROFILE).layer("M1").drawing)
    return kdb.Region(layout.top_cell().begin_shapes_rec(li)).merged()


def expected(chamfer: float) -> dict[str, dict[str, float]]:
    """The N-65 geometry's record: CTP's two sides, P2's lower and N2's upper at ``chamfer``, every other side 2."""
    return {**{port: {"bottom": 2.0, "top": 2.0} for port in TAPS}, "CTP": {"bottom": chamfer, "top": chamfer},
            "P2": {"bottom": chamfer, "top": 2.0}, "N2": {"bottom": 2.0, "top": chamfer}}


# 1. the N-65 geometry


@pytest.mark.parametrize(("width", "chamfer"), [(7.0, 1.95), (10.0, 1.2)])
def test_the_n65_geometry_builds_with_the_chamfers_that_met_shortened(tmp_path, monkeypatch, width, chamfer):
    """Before N-65 a primary width of 7 put the CTP stub's corners on the P2 / N2 stubs' at the ring (the gate refused two
    M1 min_space wedges, the N-65 finding) and 10 overlapped them by 1.5 um (refused when the fixture was laid out). Now
    the four facing chamfers -- CTP's two, P2's lower, N2's upper -- are (7.5 - w/2 - 0.1) / 2 um, every other side keeps
    2: the build passes the gate, the opening at the ring is exactly M1's 0.1 um, and the result and the manifest record
    the chamfers drawn."""
    gen, model, result = generate(tmp_path / "n65", width)
    assert gate(gen, model, result) is None
    assert result.stub_chamfers_um == expected(chamfer)
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["geometry"]["stub_chamfers_um"] == expected(chamfer)
    ports = {p["name"]: p for p in manifest["ports"]}
    for other, sign in (("P2", 1), ("N2", -1)):       # the opening at the ring: the neighbour's inner edge less its chamfer, CTP's plus its
        gap = sign * (ports[other]["y_um"] - ports["CTP"]["y_um"]) - (ports[other]["width_um"] + ports["CTP"]["width_um"]) / 2 - 2 * chamfer
        assert gap == pytest.approx(0.1)
    drawn = m1(result.gds_path)                       # on the GDS: nothing closer than 0.1 um, the two openings exactly 0.1 um wide
    assert drawn.space_check(M1_SPACE_NM).is_empty() and drawn.space_check(M1_SPACE_NM + 1).count() == 2
    without_the_rule(monkeypatch)
    if width == 7.0:
        assert gate(*generate(tmp_path / "before", width))["violations"] == [{"kind": "min_space", "layer": "M1", "count": 2}]
    else:
        with pytest.raises(PortError, match=r"the stubs of ports P2 and CTP on M1 overlap \(gap -1.5 um"):
            generate(tmp_path / "before", width)


@pytest.mark.parametrize("width", [6.0, 6.8])
def test_a_gap_at_or_above_the_minimum_is_unchanged_byte_for_byte(tmp_path, monkeypatch, width):
    """At 6 the opening ends in a 0.5 um flat, at 6.8 in exactly M1's 0.1 um: nothing is shortened, the GDS, the port file
    and the manifest are what the build without the rule writes, and nothing is recorded."""
    gen, model, result = generate(tmp_path / "n65", width)
    assert gate(gen, model, result) is None
    assert result.stub_chamfers_um is None and "stub_chamfers_um" not in json.loads(result.manifest_path.read_text())["geometry"]
    without_the_rule(monkeypatch)
    assert written(result) == written(generate(tmp_path / "before", width)[2])


def test_the_shortened_chamfer_is_on_the_manufacturing_grid(tmp_path):
    """At a primary width of 6.85 the exact value, (4.075 - 0.1) / 2 = 1.9875 um, is off the 5 nm grid: it floors to
    1.985, which leaves 0.105 um at the ring -- the largest grid value that keeps the minimum."""
    gen, model, result = generate(tmp_path, 6.85)
    assert result.stub_chamfers_um == expected(1.985)
    assert gate(gen, model, result) is None


# 2. chamfer 0 and the golden cases


@pytest.mark.parametrize("width", [7.0, 12.0])
def test_chamfer_zero_is_unchanged_byte_for_byte(tmp_path, monkeypatch, width):
    """With ``stub_chamfer_um: 0`` the stubs are rectangles, which form no wedge: the build is what it was, nothing recorded
    (the golden cases, all at chamfer 0, are ``test_golden.py``'s)."""
    flat = {**FIXTURE, "stub_chamfer_um": 0.0}
    _, _, result = generate(tmp_path / "n65", width, flat)
    assert result.stub_chamfers_um is None
    without_the_rule(monkeypatch)
    assert written(result) == written(generate(tmp_path / "before", width, flat)[2])


# 3. never below the minimum


@pytest.mark.parametrize("width", [4.0 + 0.5 * i for i in range(17)])
def test_a_sweep_of_primary_widths_passes_the_gate_at_every_width(tmp_path, width):
    """Primary widths 4 to 12 um: every build passes the gate, M1 holds no two edges closer than its minimum spacing, and a
    shortened chamfer lies between 0 and 2 um, recorded exactly where the opening would have closed below 0.1 um."""
    gen, model, result = generate(tmp_path, width)
    assert gate(gen, model, result) is None
    assert m1(result.gds_path).space_check(M1_SPACE_NM).is_empty()
    flat = 3.5 - width / 2                            # the opening at the ring with every chamfer at 2
    if flat >= 0.1 - 1e-9:
        assert result.stub_chamfers_um is None
    else:
        chamfer = result.stub_chamfers_um["CTP"]["top"]
        assert 0.0 <= chamfer < 2.0 and result.stub_chamfers_um == expected(chamfer)


def test_top_and_bottom_stubs_name_their_sides_left_and_right(tmp_path):
    """xfm_tw's ports face up and down, their stubs 5 um wide 8 um either side of the centre line: a chamfer of 6 um would
    overlap each pair by 1 um at the ring, so the facing chamfers drop to (11 - 0.1) / 2 = 5.45 um, the sides named
    ``left`` / ``right``; 5 um leaves 1 um and changes nothing."""
    config = CASES["xfm_tw_nr3"][1]
    _, _, deep = generate(tmp_path / "six", 0.0, {**GOLDEN_FIXTURE, "stub_chamfer_um": 6.0}, "clean_port_xfm_tw", config)
    assert deep.stub_chamfers_um == {"P1": {"left": 5.45, "right": 6.0}, "N1": {"left": 5.45, "right": 6.0},
                                     "P2": {"left": 6.0, "right": 5.45}, "N2": {"left": 6.0, "right": 5.45}}
    _, _, room = generate(tmp_path / "five", 0.0, {**GOLDEN_FIXTURE, "stub_chamfer_um": 5.0}, "clean_port_xfm_tw", config)
    assert room.stub_chamfers_um is None


def test_only_neighbours_facing_each_other_are_shortened():
    """``_facing_chamfers`` alone: of three stubs on one side, given in any order, only the two that face each other across
    too small a gap change, and only on their facing sides; a stub alone on its side, ``chamfer`` 0 and no minimum
    spacing change nothing; a pair that would touch even as rectangles gets 0, never less."""
    stubs = [("right", 10.0, 2.0), ("right", 0.0, 2.0), ("right", 5.5, 1.0), ("top", 0.0, 2.0)]
    # gaps at the tips: 0 .. 5.5 is 5.5 - 2 - 1 = 2.5 (at the ring with 1.5: -0.5); 5.5 .. 10 is 10 - 2 - 6.5 = 1.5 (at the ring -1.5)
    assert _facing_chamfers(stubs, 1.5, 0.1, 0.005) == [[0.7, 1.5], [1.5, 1.2], [1.2, 0.7], [1.5, 1.5]]
    assert _facing_chamfers(stubs, 0.5, 0.1, 0.005) == [[0.5, 0.5]] * 4               # 1.5 and 0.5 left at the ring: unchanged
    assert _facing_chamfers(stubs, 0.0, 0.1, 0.005) == [[0.0, 0.0]] * 4
    assert _facing_chamfers(stubs, 1.5, None, None) == [[1.5, 1.5]] * 4
    assert _facing_chamfers([("left", 0.0, 2.0), ("left", 3.9, 2.0)], 1.0, 0.1, 0.005) == [[1.0, 0.0], [0.0, 1.0]]


# 4. reference mode


def test_reference_mode_adjusts_nothing():
    """Without a profile there is no minimum spacing: at a primary width of 7 every side keeps its 2 um chamfer (the CTP
    and P2 stubs share a corner at the ring, as before N-65) and nothing is recorded; at 10 the overlapping stubs are still
    refused when the fixture is laid out."""
    def build(width: float):
        fixture = GroundFixtureConfig(inner_margin_um=4.0, ring_width_um=70.0, stub_width_um=6.5, stub_length_um=2.0, stub_chamfer_um=2.0,
                                      stub_width_by_port_um={"P1": width, "N1": width, "CTP": width})
        return xfm_bs(OD_P=73.0, OD_S=58.0, W_P=width, W_S=6.5, OPENING_P=8.75, OPENING_S=7.5, LEAD_P=20.75, LEAD_S=21.5,
                      CENTER_SPACING=0.0, PRI_ME="6", SEC_ME="5", CT_P_ME="4", CT_S_ME="4", ground_fixture=fixture)

    cell = build(7.0)
    assert cell.fixture_metal == "M1" and cell.stub_chamfers_um is None
    x, y = next(p["point_nm"] for p in cell.emx_ports if p["name"] == "CTP")
    stub = next([tuple(pt) for pt in pts] for layer, pts in cell.flat_shapes()
                if layer == metal_layer(1) and {(x, y - 3500), (x, y + 3500)} <= {tuple(pt) for pt in pts})
    assert sorted(py - y for px, py in stub if px != x) == [-5500, 5500]                     # at the ring: 3.5 + 2 either side
    with pytest.raises(PortError, match=r"the stubs of ports P2 and CTP on M1 overlap \(gap -1.5 um"):
        build(10.0)
