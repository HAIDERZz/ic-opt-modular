"""T19.6 (``docs/refactor/T19_6_MS_SAME_METAL_TAP_SPEC.md``): the same-metal tap on xfm_ms's single-turn primary.

``ct_primary_metal`` equal to ``primary_metal`` draws T19.4's same-metal tap on the single-turn primary -- no via stack,
the lead on the primary's metal over the via-stack tap's span, ``CTP`` at its far tip -- optionally narrower than the
winding (``ct_primary_width_um``). Below the primary the via-stack tap is unchanged; above it is refused. The multi-turn
secondary's tap (ind_sym's, through a via stack to a metal at least two levels below) is not changed. Every existing
configuration builds byte for byte as before. demo_6m only: the golden ms winds its primary on M6 and its secondary on M5,
with the secondary's crossunder on M4.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("klayout.db")

import klayout.db as kdb
from pydantic import ValidationError

from ic_opt.em.pcell import get_generator
from ic_opt.em.pcell._pcell_core import PortError, process_rule_context
from ic_opt.em.pcell._pcell_xfm_ms import xfm_ms
from ic_opt.em.pcell.connectivity import nets
from ic_opt.em.pcell.drc_audit import (
    audit_gds,
    expected_conductors,
    fixture_exemptions,
    product_scope_record,
)
from ic_opt.em.pcell.process_rules import PROFILE_DIRS_ENV_VAR
from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter
from ic_opt.em.pcell.stack import use_stack
from tests.ic_opt.pcell.test_golden import CASES, FIXTURE, GOLDEN, XFM_PORTS, build
from tests.ic_opt.pcell.test_manufacturing_grid import gridded
from tests.ic_opt.pcell.test_profile_validation import write_profile

PROFILE = "demo_6m"
CTP = [*XFM_PORTS, "CTP"]
TAPS = [*XFM_PORTS, "CTP", "CTS"]
SAME = {"ct_primary_metal": "6", "port_order": CTP}
#: the golden ms (``CASES["xfm_ms_nt3"]``) as the pcell takes it
GEOMETRY = {"OD_S": 100.0, "OD_M": 76.0, "W_S": 6.0, "W_M": 3.0, "OPENING_S": 8.0, "OPENING_M": 6.0, "LEAD_S": 20.0,
            "LEAD_M": 15.0, "NT_M": 3, "S_M": 2.0, "CENTER_SPACING": 0.0, "SINGLE_ME": "6", "MULTI_ME": "5"}
#: the golden ms cases' fixture: a 6 um stub on the primary's ports, 3 um on the secondary's (``test_golden.build``)
STUBS = {**FIXTURE, "stub_width_um": 6.0, "stub_width_by_port_um": {"P2": 3.0, "N2": 3.0}}
AUTO_STUBS = {k: v for k, v in FIXTURE.items() if k != "stub_width_um"}                   # each stub as wide as its lead
WIDTH = "ct_primary_width_um"


def generate(tmp_path: Path, name: str = "ms", fixture: dict | None = None, case: str = "xfm_ms_nt3", **overrides):
    gen = get_generator("clean_port_xfm_ms", plugin_module="builtin:clean_port")
    config = {**CASES[case][1], "process_profile": PROFILE, "ground_fixture": dict(STUBS if fixture is None else fixture),
              **overrides}
    model = gen.config_model.model_validate(config)
    return gen, model, gen.generate(model, outdir=tmp_path / name, gds_name=f"{name}.gds")


def validate(**overrides):
    gen = get_generator("clean_port_xfm_ms", plugin_module="builtin:clean_port")
    return gen.config_model.model_validate({**CASES["xfm_ms_nt3"][1], "process_profile": PROFILE, "ground_fixture": dict(STUBS),
                                            **overrides})


def layer(metal: str, kind: str = "drawing") -> tuple[int, int]:
    return tuple(getattr(get_geometry_rule_adapter(PROFILE).layer(metal), kind))


def gate(gen, model, result) -> dict:
    """The product DRC gate's verdict on a build (``stages.em_chain._audit``)."""
    with use_stack(PROFILE):
        return product_scope_record(audit_gds(result.gds_path, PROFILE), expected_conductors(gen, model),
                                    ignore_findings=fixture_exemptions(PROFILE, result.fixture_metal, result.fixture_metal_rule))


# 1. the construction


@pytest.mark.parametrize(("turns", "extension"), [(3, 0.0), (2, 0.0), (3, 6.0)])
def test_a_same_metal_primary_tap_runs_on_the_primarys_own_metal(turns, extension):
    """CTP on the primary's metal and its pin layer, at the point of the via-stack build's tap port (the far end does not
    move); the other ports where the untapped build has them. The tap adds nothing but its lead on M6: every other layer
    -- the secondary on M5, its crossunder on M4 and the crossunder's vias -- is the untapped build's, shape for shape, and
    no lower metal or via appears (the via-stack build draws its stack and its lead on M3). The lead is one polygon with
    the primary."""
    ctx = process_rule_context(PROFILE)
    geometry = {**GEOMETRY, "NT_M": turns, "STRAIGHT_EXTENSION": extension}
    plain = xfm_ms(**geometry, process=ctx)
    same = xfm_ms(**geometry, CT_P_ME="M6", process=ctx)
    stack = xfm_ms(**geometry, CT_P_ME="3", process=ctx)
    assert [p["name"] for p in same.emx_ports] == CTP
    ports, stacked, untapped = ({p["name"]: p for p in c.emx_ports} for c in (same, stack, plain))
    assert ports["CTP"]["metal"] == "M6" and tuple(ports["CTP"]["label_layer"]) == layer("M6", "pin")
    assert ports["CTP"]["point_nm"] == stacked["CTP"]["point_nm"] and ports["CTP"]["point_nm"][1] == 0
    assert ports["CTP"]["orientation_deg"] == stacked["CTP"]["orientation_deg"]
    assert all(ports[name] == untapped[name] for name in XFM_PORTS)
    drawn = {lay for lay, _ in same.flat_shapes()}
    assert drawn == {lay for lay, _ in plain.flat_shapes()}
    for lay in drawn - {layer("M6")}:
        assert (same.region(lay) ^ plain.region(lay)).is_empty(), lay
    assert (plain.region(layer("M6")) - same.region(layer("M6"))).is_empty()
    assert same.region(layer("M6")).merged().count() == 1                              # winding + leads + tap: one polygon
    assert layer("M3") in {lay for lay, _ in stack.flat_shapes()} and layer("M3") not in drawn


def test_beside_a_secondary_tap_only_the_primarys_metal_changes():
    """With the secondary tapped as before (ind_sym's via stack to M3), a same-metal primary tap still adds its lead on M6
    and nothing else: every other layer is the build with the secondary's tap alone."""
    ctx = process_rule_context(PROFILE)
    secondary = xfm_ms(**GEOMETRY, CT_S_ME="3", process=ctx)
    both = xfm_ms(**GEOMETRY, CT_P_ME="6", CT_S_ME="3", process=ctx)
    assert [p["name"] for p in both.emx_ports] == TAPS
    drawn = {lay for lay, _ in both.flat_shapes()}
    assert drawn == {lay for lay, _ in secondary.flat_shapes()} and layer("M3") in drawn
    for lay in drawn - {layer("M6")}:
        assert (both.region(lay) ^ secondary.region(lay)).is_empty(), lay


@pytest.mark.parametrize("taps", [SAME, {"ct_primary_metal": "M6", "ct_secondary_metal": "3", "port_order": TAPS}])
def test_the_primarys_tap_is_on_the_primarys_net(tmp_path, taps):
    """Read back from the written GDS (``connectivity.nets``): CTP on the primary's net (P1, N1), CTS on the secondary's,
    the two windings apart, every G pin on the fixture."""
    _, _, result = generate(tmp_path, **taps)
    found = nets(result.gds_path, PROFILE)
    ground = {found[name] for name in found if name.startswith("G")}
    assert len(ground) == 1 and 0 not in found.values(), found                 # every stub on the ring, every label on metal
    assert found["CTP"] == found["P1"] == found["N1"] and found["P2"] == found["N2"] == found.get("CTS", found["P2"]), found
    assert len({found["P1"], found["P2"], *ground}) == 3, found                 # the windings apart, neither on the ring


# 2. the tap width


@pytest.mark.parametrize("width", [None, 3.0])
def test_the_tap_width_sets_the_lead_about_the_centre_line(tmp_path, width):
    """``ct_primary_width_um = 3`` draws the tap lead +-1.5 um about the centre line (the port's width and zone say the
    same); absent, the lead is as wide as the primary, 6 um. The width enters the cell's name and params only when set."""
    widths = {} if width is None else {WIDTH: width}
    _, _, result = generate(tmp_path, **SAME, **widths)
    lead = 6.0 if width is None else width
    port = {p["name"]: p for p in json.loads(result.manifest_path.read_text())["ports"]}["CTP"]
    assert port["width_um"] == lead and port["lead_zone_um"][1::2] == [-lead / 2, lead / 2]
    layout = kdb.Layout()
    layout.read(str(result.gds_path))
    top = layout.top_cell()
    # beyond the primary's outer edge (x = 50) up to the tap's far tip (x = 70) the tap lead is all M6 holds
    drawn = kdb.Region(top.begin_shapes_rec(layout.layer(*layer("M6")))) & kdb.Region(kdb.Box(50_000, -100_000, 70_000, 100_000))
    box = drawn.bbox()
    assert (box.left, box.right, box.bottom, box.top) == (50_000, 70_000, round(-lead * 500), round(lead * 500))
    cell = xfm_ms(**GEOMETRY, CT_P_ME="6", CT_P_W=width, process=process_rule_context(PROFILE))
    if width is None:
        assert "CTPW" not in cell.name and "CT_P_W" not in cell.params
    else:
        assert cell.name.endswith("_CTPW3") and cell.params["CT_P_W"] == 3.0


def test_the_automatic_stub_of_the_tap_port_is_as_wide_as_its_lead(tmp_path):
    """With ``stub_width_um`` left out every stub is as wide as the lead it lands on: a 3 um same-metal tap gets a 3 um
    stub, the primary's ports 6 um and the secondary's 3 um -- byte for byte the GDS that spells those widths out. (One
    file name in two directories: the top cell is named after the file.)"""
    taps = {**SAME, WIDTH: 3.0}
    auto = generate(tmp_path / "auto", "ms", AUTO_STUBS, **taps)[2].gds_path.read_bytes()
    spelled = {**AUTO_STUBS, "stub_width_um": 6.0, "stub_width_by_port_um": {"P2": 3.0, "N2": 3.0, "CTP": 3.0}}
    assert auto == generate(tmp_path / "spelled", "ms", spelled, **taps)[2].gds_path.read_bytes()
    assert auto != generate(tmp_path / "six", "ms", STUBS, **taps)[2].gds_path.read_bytes()


# 3. refusals


def test_the_config_refuses_with_the_fields_named():
    """A primary tap above the primary; a width with a via-stack tap or without the tap metal; a secondary tap on the
    secondary's metal or one level below (the rule and its message unchanged); no width for the secondary's tap. The
    primary's own metal spelled any way is a same-metal tap."""
    with pytest.raises(ValidationError, match="ct_primary_metal '6' must sit at or below primary_metal '5'"):
        validate(primary_metal="5", secondary_metal="4", ct_primary_metal="6", port_order=CTP)
    with pytest.raises(ValidationError, match=r"ct_primary_width_um 3\.0 applies to a same-metal tap only.*ct_primary_metal '4' sits below primary_metal '6'"):
        validate(ct_primary_metal="4", ct_primary_width_um=3.0, port_order=CTP)
    with pytest.raises(ValidationError, match="ct_primary_width_um 3.0 needs ct_primary_metal"):
        validate(ct_primary_width_um=3.0)
    for metal in ("5", "4"):
        with pytest.raises(ValidationError, match=f"ct_secondary_metal '{metal}' must sit at least two levels below secondary_metal '5': "
                                                  "the secondary's crossunder occupies secondary_metal-1"):
            validate(ct_secondary_metal=metal, port_order=[*XFM_PORTS, "CTS"])
    with pytest.raises(ValidationError, match="ct_secondary_width_um"):
        validate(ct_secondary_metal="3", ct_secondary_width_um=3.0, port_order=[*XFM_PORTS, "CTS"])
    for bad in (0.0, -1.0, 3.005):
        with pytest.raises(ValidationError, match=WIDTH):
            validate(ct_primary_metal="6", ct_primary_width_um=bad, port_order=CTP)
    for spelling in ("6", "M6", "m6"):
        assert validate(ct_primary_metal=spelling, ct_primary_width_um=3.0, port_order=CTP).ct_primary_width_um == 3.0


def test_the_pcell_refuses_fail_closed():
    """The pcell repeats the rules at build: a primary tap above the primary; a width with a via-stack tap, without a tap,
    or below the primary metal's min width (demo_6m M6: 1 um); the secondary's tap on its metal or one level below."""
    ctx = process_rule_context(PROFILE)
    with pytest.raises(PortError, match="xfm_ms: CT_P metal M6 must sit at or below the single-turn primary M5"):
        xfm_ms(**{**GEOMETRY, "SINGLE_ME": "5", "MULTI_ME": "4"}, CT_P_ME="6", process=ctx)
    with pytest.raises(PortError, match="xfm_ms: CT_P_W=3.0 applies to a same-metal tap only"):
        xfm_ms(**GEOMETRY, CT_P_ME="4", CT_P_W=3.0, process=ctx)
    with pytest.raises(PortError, match="xfm_ms: CT_P_W=3.0 needs CT_P_ME"):
        xfm_ms(**GEOMETRY, CT_P_W=3.0, process=ctx)
    with pytest.raises(PortError, match="below the M6 min width"):
        xfm_ms(**GEOMETRY, CT_P_ME="6", CT_P_W=0.5, process=ctx)
    for metal in ("5", "4"):
        with pytest.raises(PortError, match=f"xfm_ms: CT metal M{metal} must sit at least two levels below the coil top metal M5"):
            xfm_ms(**GEOMETRY, CT_S_ME=metal, process=ctx)


@pytest.fixture
def grid10(tmp_path, monkeypatch):
    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(write_profile(tmp_path / "profiles", gridded(0.01, "demo_grid10"), "demo_grid10")))
    return "demo_grid10"


def test_half_the_tap_width_stays_on_the_profile_grid(grid10):
    """The lead is centred on the centre line, so half its width is a coordinate (D9): on a 10 nm grid the width is a
    multiple of 0.02 um."""
    with pytest.raises(ValidationError, match="ct_primary_width_um 3.01 must be a multiple of 0.02 um"):
        validate(process_profile=grid10, ct_primary_metal="6", ct_primary_width_um=3.01, port_order=CTP)
    validate(process_profile=grid10, ct_primary_metal="6", ct_primary_width_um=3.02, port_order=CTP)


# 4. every existing configuration, byte for byte

PORT_FILE = "-p N1=N1:G02\n-p N2=N2:G04\n-p P1=P1:G01\n-p P2=P2:G03\n"

#: the demo_6m ms configurations the other tests build (golden cases, T19.1 auto fixture, M3.1 port spacing, straight
#: extension, the shield) with no taps or the taps that existed before (a via-stack primary tap, the secondary's tap)
EXISTING = {
    "golden_nt3": {},
    "golden_nt2": {"secondary_turns": 2},
    "auto": {"ground_fixture": {**STUBS, "metal": "auto"}},
    "primary_stack": {"ct_primary_metal": "3", "port_order": CTP},
    "secondary_tap": {"ct_secondary_metal": "3", "port_order": [*XFM_PORTS, "CTS"]},
    "both_stacks": {"ct_primary_metal": "3", "ct_secondary_metal": "3", "port_order": TAPS},
    "port_spacing": {"primary_port_spacing_um": 30.0, "secondary_port_spacing_um": 20.0},
    "extension": {"straight_extension_um": 6.0},
    "pgs": {"pgs": {"strip_width_um": 1.0, "strip_spacing_um": 2.0, "margin_um": 5.0}},
}


@pytest.mark.parametrize("name", ["xfm_ms_nt3", "xfm_ms_nt2"])
def test_the_golden_ms_cases_are_unchanged_byte_for_byte(name, tmp_path):
    """The GDS is the golden file byte for byte, the port file the one these cases always wrote, and the manifest's config
    carries no tap width."""
    gds = build(name, tmp_path)
    assert gds.read_bytes() == (GOLDEN / f"{name}.gds").read_bytes()
    assert (gds.parent / "emx_ports.txt").read_text() == PORT_FILE
    assert WIDTH not in json.loads((gds.parent / "geometry_manifest.json").read_text())["geometry"]["config"]


@pytest.mark.parametrize("name", sorted(EXISTING))
def test_a_width_left_out_or_null_changes_no_byte(name, tmp_path):
    """Each existing configuration with the tap width left out and spelled out as null: the GDS, the port file and the
    manifest are the same bytes, and the manifest's config has no width key (the serializer drops it)."""
    over = dict(EXISTING[name])
    fixture = over.pop("ground_fixture", None)
    _, _, absent = generate(tmp_path / "absent", "ms", fixture, **over)
    _, _, null = generate(tmp_path / "null", "ms", fixture, **over, **{WIDTH: None})
    for a, b in ((absent.gds_path, null.gds_path), (absent.emx_ports_path, null.emx_ports_path),
                 (absent.manifest_path, null.manifest_path)):
        assert a.read_bytes() == b.read_bytes(), a.name
    assert WIDTH not in json.loads(absent.manifest_path.read_text())["geometry"]["config"]


# 5. the fixture's metal and the DRC gate


@pytest.mark.parametrize("turns", [3, 2])
def test_the_drc_gate_passes_a_same_metal_tapped_ms_on_the_auto_metal(tmp_path, turns):
    """A 3 um same-metal primary tap with the fixture on ``auto``: the fixture lands on M3, as for the untapped device (the
    secondary's crossunder holds M4); the gate's conductors are the windings' metals and the crossunder's, the vias drawn
    are the crossunder's alone, and the verdict is pass."""
    fixture = {**AUTO_STUBS, "metal": "auto"}
    _, _, plain = generate(tmp_path, "plain", fixture, secondary_turns=turns)
    gen, model, same = generate(tmp_path, "same", fixture, secondary_turns=turns, **SAME, **{WIDTH: 3.0})
    assert plain.fixture_metal == same.fixture_metal == "M3" and same.fixture_metal_rule == "free"
    assert expected_conductors(gen, model) == ["M6", "M5", "M4"]
    audits = [json.loads(r.manifest_path.read_text())["via_landing_audit"] for r in (plain, same)]
    assert audits[0] == audits[1] and audits[1]["status"] == "pass" and audits[1]["vias_checked"] >= 1
    record = gate(gen, model, same)
    assert record["outcome"] == "pass", record
