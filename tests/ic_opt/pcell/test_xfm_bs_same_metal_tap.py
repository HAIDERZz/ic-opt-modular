"""T19.4 (``docs/refactor/T19_4_SAME_METAL_TAP_SPEC.md``): same-metal center taps on xfm_bs.

A tap metal equal to its winding's draws the tap lead on the winding's own metal -- no via stack, the lead over the same
span as the via-stack tap's, the port at its far tip -- optionally narrower than the winding (``ct_*_width_um``). Below
the winding the via-stack tap is unchanged; above it is refused. Every existing configuration (no taps, via-stack taps)
builds byte for byte as before. demo_6m only: the golden bs winds its primary on M6 and its secondary on M5.
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
from ic_opt.em.pcell._pcell_xfm_bs import xfm_bs
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
from tests.ic_opt.pcell.test_connectivity import expected_topology
from tests.ic_opt.pcell.test_golden import CASES, FIXTURE, GOLDEN, XFM_PORTS, build
from tests.ic_opt.pcell.test_manufacturing_grid import gridded
from tests.ic_opt.pcell.test_profile_validation import write_profile

PROFILE = "demo_6m"
TAPS = [*XFM_PORTS, "CTP", "CTS"]
SAME = {"ct_primary_metal": "6", "ct_secondary_metal": "5", "port_order": TAPS}
STACK = {"ct_primary_metal": "4", "ct_secondary_metal": "3", "port_order": TAPS}          # golden xfm_bs_ct's taps
#: the golden bs (``CASES["xfm_bs"]``) as the pcell takes it
GEOMETRY = {"OD_P": 100.0, "OD_S": 100.0, "W_P": 5.0, "W_S": 5.0, "OPENING_P": 8.0, "OPENING_S": 8.0, "LEAD_P": 20.0,
            "LEAD_S": 20.0, "CENTER_SPACING": 0.0, "PRI_ME": "6", "SEC_ME": "5"}
AUTO_STUBS = {k: v for k, v in FIXTURE.items() if k != "stub_width_um"}                   # each stub as wide as its lead
WIDTHS = ("ct_primary_width_um", "ct_secondary_width_um")


def generate(tmp_path: Path, name: str = "bs", fixture: dict | None = None, **overrides):
    gen = get_generator("clean_port_xfm_bs", plugin_module="builtin:clean_port")
    config = {**CASES["xfm_bs"][1], "process_profile": PROFILE, "ground_fixture": dict(FIXTURE if fixture is None else fixture),
              **overrides}
    model = gen.config_model.model_validate(config)
    return gen, model, gen.generate(model, outdir=tmp_path / name, gds_name=f"{name}.gds")


def validate(**overrides):
    gen = get_generator("clean_port_xfm_bs", plugin_module="builtin:clean_port")
    return gen.config_model.model_validate({**CASES["xfm_bs"][1], "process_profile": PROFILE, "ground_fixture": dict(FIXTURE),
                                            **overrides})


def layer(metal: str, kind: str = "drawing") -> tuple[int, int]:
    return tuple(getattr(get_geometry_rule_adapter(PROFILE).layer(metal), kind))


def gate(gen, model, result) -> dict:
    """The product DRC gate's verdict on a build (``stages.em_chain._audit``): the generator's conductors drawn, no finding
    but max_width on the fixture's metal -- and spacing there when the build chose that metal under ``free`` (T19.5)."""
    with use_stack(PROFILE):
        return product_scope_record(audit_gds(result.gds_path, PROFILE), expected_conductors(gen, model),
                                    ignore_findings=fixture_exemptions(PROFILE, result.fixture_metal, result.fixture_metal_rule))


# 1. the construction


@pytest.mark.parametrize("extension", [0.0, 6.0])
def test_same_metal_taps_run_on_the_windings_own_metals(extension):
    """CTP on the primary's metal, CTS on the secondary's, each on its metal's pin layer at the point of the via-stack
    build's tap port (the far ends do not move); the device draws nothing on any metal below the two windings and no via,
    where the via-stack build of the same geometry draws its stacks and leads; each tap lead is one polygon with its
    winding."""
    ctx = process_rule_context(PROFILE)
    same = xfm_bs(**GEOMETRY, CT_P_ME="6", CT_S_ME="M5", STRAIGHT_EXTENSION=extension, process=ctx)
    stack = xfm_bs(**GEOMETRY, CT_P_ME="4", CT_S_ME="3", STRAIGHT_EXTENSION=extension, process=ctx)
    assert [p["name"] for p in same.emx_ports] == TAPS
    ports = {p["name"]: p for p in same.emx_ports}
    stacked = {p["name"]: p for p in stack.emx_ports}
    for tap, metal in (("CTP", "M6"), ("CTS", "M5")):
        assert ports[tap]["metal"] == metal and tuple(ports[tap]["label_layer"]) == layer(metal, "pin")
        assert ports[tap]["point_nm"] == stacked[tap]["point_nm"] and ports[tap]["point_nm"][1] == 0
        assert ports[tap]["orientation_deg"] == stacked[tap]["orientation_deg"]
    assert {lay for lay, _ in same.flat_shapes()} == {layer("M6"), layer("M5")}
    vias = {tuple(v.drawing) for v in get_geometry_rule_adapter(PROFILE).profile.layer_catalog.vias.values()}
    drawn = {lay for lay, _ in stack.flat_shapes()}
    assert {layer("M4"), layer("M3")} <= drawn and len(drawn & vias) == 3                 # VIA5 / VIA4 / VIA3
    for metal in ("M6", "M5"):
        assert same.region(layer(metal)).merged().count() == 1                         # winding + leads + tap: one polygon


def test_the_taps_are_on_their_windings_nets(tmp_path):
    """Read back from the written GDS (``connectivity.nets``): CTP on the primary's net, CTS on the secondary's, the two
    windings apart, every G pin on the fixture."""
    _, _, result = generate(tmp_path, **SAME)
    expected_topology("xfm_bs_same_metal", nets(result.gds_path, PROFILE))


# 2. the tap width


@pytest.mark.parametrize("width", [None, 3.0])
def test_the_tap_width_sets_the_lead_about_the_centre_line(tmp_path, width):
    """``ct_*_width_um = 3`` draws each tap lead +-1.5 um about the centre line (the port's width and zone say the same);
    absent, the lead is as wide as its winding, 5 um. The width enters the cell's name and params only when set."""
    widths = {} if width is None else dict.fromkeys(WIDTHS, width)
    _, _, result = generate(tmp_path, **SAME, **widths)
    lead = 5.0 if width is None else width
    ports = {p["name"]: p for p in json.loads(result.manifest_path.read_text())["ports"]}
    layout = kdb.Layout()
    layout.read(str(result.gds_path))
    top = layout.top_cell()
    # beyond each ring's outer edge (|x| = 50) up to the far tip (|x| = 70) the tap lead is all that winding draws
    for tap, metal, x0, x1 in (("CTP", "M6", 50_000, 70_000), ("CTS", "M5", -70_000, -50_000)):
        assert ports[tap]["width_um"] == lead and ports[tap]["lead_zone_um"][1::2] == [-lead / 2, lead / 2]
        drawn = kdb.Region(top.begin_shapes_rec(layout.layer(*layer(metal)))) & kdb.Region(kdb.Box(x0, -100_000, x1, 100_000))
        box = drawn.bbox()
        assert (box.left, box.right, box.bottom, box.top) == (x0, x1, round(-lead * 500), round(lead * 500))
    cell = xfm_bs(**GEOMETRY, CT_P_ME="6", CT_S_ME="5", CT_P_W=width, CT_S_W=width, process=process_rule_context(PROFILE))
    if width is None:
        assert "CTPW" not in cell.name and not {"CT_P_W", "CT_S_W"} & set(cell.params)
    else:
        assert cell.name.endswith("_CTPW3_CTSW3") and cell.params["CT_P_W"] == cell.params["CT_S_W"] == 3.0


def test_the_automatic_stub_of_a_tap_port_is_as_wide_as_its_lead(tmp_path):
    """With ``stub_width_um`` left out every stub is as wide as the lead it lands on (M13 ticket 09): 3 um same-metal taps
    get 3 um stubs and the windings' ports 5 um -- byte for byte the GDS that spells those widths out, and not the GDS of
    5 um stubs everywhere. (One file name in two directories: the top cell is named after the file.)"""
    taps = {**SAME, **dict.fromkeys(WIDTHS, 3.0)}
    auto = generate(tmp_path / "auto", "bs", AUTO_STUBS, **taps)[2].gds_path.read_bytes()
    spelled = {**AUTO_STUBS, "stub_width_um": 5.0, "stub_width_by_port_um": {"CTP": 3.0, "CTS": 3.0}}
    assert auto == generate(tmp_path / "spelled", "bs", spelled, **taps)[2].gds_path.read_bytes()
    assert auto != generate(tmp_path / "five", "bs", FIXTURE, **taps)[2].gds_path.read_bytes()


# 3. refusals


def test_the_config_refuses_with_the_fields_named():
    """A tap metal above its winding; a width with a via-stack tap; a width without its tap metal. The same metal spelled
    any way is a same-metal tap."""
    with pytest.raises(ValidationError, match="ct_secondary_metal '6' must sit at or below secondary_metal '5'"):
        validate(ct_secondary_metal="6", port_order=[*XFM_PORTS, "CTS"])
    with pytest.raises(ValidationError, match=r"ct_primary_width_um 3\.0 applies to a same-metal tap only.*ct_primary_metal '4' sits below primary_metal '6'"):
        validate(ct_primary_metal="4", ct_primary_width_um=3.0, port_order=[*XFM_PORTS, "CTP"])
    with pytest.raises(ValidationError, match="ct_secondary_width_um 3.0 needs ct_secondary_metal"):
        validate(ct_secondary_width_um=3.0)
    for bad in (0.0, -1.0, 3.005):
        with pytest.raises(ValidationError, match="ct_primary_width_um"):
            validate(ct_primary_metal="6", ct_primary_width_um=bad, port_order=[*XFM_PORTS, "CTP"])
    for spelling in ("6", "M6", "m6"):
        assert validate(ct_primary_metal=spelling, ct_primary_width_um=3.0, port_order=[*XFM_PORTS, "CTP"]).ct_primary_width_um == 3.0


def test_the_pcell_refuses_fail_closed():
    """The pcell repeats the rules at build: a tap metal above its winding, a width with a via-stack tap or without a tap,
    and a width below the winding metal's min width (demo_6m M6: 1 um)."""
    ctx = process_rule_context(PROFILE)
    with pytest.raises(PortError, match="CT_S metal M6 must sit at or below the secondary M5"):
        xfm_bs(**GEOMETRY, CT_S_ME="6", process=ctx)
    with pytest.raises(PortError, match="CT_P_W=3.0 applies to a same-metal tap only"):
        xfm_bs(**GEOMETRY, CT_P_ME="4", CT_P_W=3.0, process=ctx)
    with pytest.raises(PortError, match="CT_S_W=3.0 needs CT_S_ME"):
        xfm_bs(**GEOMETRY, CT_S_W=3.0, process=ctx)
    with pytest.raises(PortError, match="below the M6 min width"):
        xfm_bs(**GEOMETRY, CT_P_ME="6", CT_P_W=0.5, process=ctx)


@pytest.fixture
def grid10(tmp_path, monkeypatch):
    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(write_profile(tmp_path / "profiles", gridded(0.01, "demo_grid10"), "demo_grid10")))
    return "demo_grid10"


def test_half_the_tap_width_stays_on_the_profile_grid(grid10):
    """The lead is centred on the centre line, so half its width is a coordinate (D9): on a 10 nm grid the width is a
    multiple of 0.02 um."""
    with pytest.raises(ValidationError, match="ct_primary_width_um 3.01 must be a multiple of 0.02 um"):
        validate(process_profile=grid10, ct_primary_metal="6", ct_primary_width_um=3.01, port_order=[*XFM_PORTS, "CTP"])
    validate(process_profile=grid10, ct_primary_metal="6", ct_primary_width_um=3.02, port_order=[*XFM_PORTS, "CTP"])


# 4. every existing configuration, byte for byte

PORT_FILES = {
    "xfm_bs": "-p N1=N1:G02\n-p N2=N2:G04\n-p P1=P1:G01\n-p P2=P2:G03\n",
    "xfm_bs_ct": "-p CTP=CTP:G05\n-p CTS=CTS:G06\n-p N1=N1:G02\n-p N2=N2:G04\n-p P1=P1:G01\n-p P2=P2:G03\n",
}

#: the demo_6m bs configurations the other tests build (golden cases, T19.1 auto fixture, M3.1 port spacing, the footprint's
#: unequal ODs, em_circuit's taps), no taps or via-stack taps
EXISTING = {
    "golden": {},
    "golden_ct": STACK,
    "auto": {"ground_fixture": {**FIXTURE, "metal": "auto"}},
    "auto_ct": {**STACK, "ground_fixture": {**FIXTURE, "metal": "auto"}},
    "port_spacing_wide": {"secondary_port_spacing_um": 31.0},
    "port_spacing_close": {"secondary_port_spacing_um": 15.0},
    "unequal_od": {"secondary_outer_diameter_um": 120.0},
    "ct_4_4": {"ct_primary_metal": "4", "ct_secondary_metal": "4", "port_order": TAPS},
    "pgs": {"pgs": {"strip_width_um": 1.0, "strip_spacing_um": 2.0, "margin_um": 5.0}},
}


@pytest.mark.parametrize("name", ["xfm_bs", "xfm_bs_ct"])
def test_the_golden_bs_cases_are_unchanged_byte_for_byte(name, tmp_path):
    """No taps and via-stack taps: the GDS is the golden file byte for byte, the port file is the one these cases always
    wrote, and the manifest's config carries no tap width."""
    gds = build(name, tmp_path)
    assert gds.read_bytes() == (GOLDEN / f"{name}.gds").read_bytes()
    assert (gds.parent / "emx_ports.txt").read_text() == PORT_FILES[name]
    manifest = json.loads((gds.parent / "geometry_manifest.json").read_text())
    assert not set(WIDTHS) & set(manifest["geometry"]["config"]) and manifest["via_landing_audit"]["vias_checked"] == (3 if name == "xfm_bs_ct" else 0)


@pytest.mark.parametrize("name", sorted(EXISTING))
def test_widths_left_out_or_null_change_no_byte(name, tmp_path):
    """Each existing configuration with the tap widths left out and spelled out as null: the GDS, the port file and the
    manifest are the same bytes, and the manifest's config has no width key (the serializer drops it)."""
    over = dict(EXISTING[name])
    fixture = over.pop("ground_fixture", None)
    _, _, absent = generate(tmp_path / "absent", "bs", fixture, **over)
    _, _, null = generate(tmp_path / "null", "bs", fixture, **over, **dict.fromkeys(WIDTHS))
    for a, b in ((absent.gds_path, null.gds_path), (absent.emx_ports_path, null.emx_ports_path),
                 (absent.manifest_path, null.manifest_path)):
        assert a.read_bytes() == b.read_bytes(), a.name
    assert not set(WIDTHS) & set(json.loads(absent.manifest_path.read_text())["geometry"]["config"])


# 5. the fixture's metal under auto


def test_auto_takes_the_same_fixture_metal_with_same_metal_taps(tmp_path):
    """A same-metal tap adds no metal to the device: ``auto`` picks M4 for the untapped and the same-metal-tapped device
    alike; the via-stack taps (M4, M3) push it down to M2, and so does one via-stack tap beside a same-metal one (whose
    stack the via audit then finds and expects)."""
    fixture = {**FIXTURE, "metal": "auto"}
    plain = generate(tmp_path, "plain", fixture)[2]
    same = generate(tmp_path, "same", fixture, **SAME, **dict.fromkeys(WIDTHS, 3.0))[2]
    stack = generate(tmp_path, "stack", fixture, **STACK)[2]
    mixed = generate(tmp_path, "mixed", fixture, **{**SAME, "ct_secondary_metal": "3"}, ct_primary_width_um=3.0)[2]
    assert plain.fixture_metal == same.fixture_metal == "M4"
    assert stack.fixture_metal == mixed.fixture_metal == "M2"
    assert json.loads(mixed.manifest_path.read_text())["via_landing_audit"]["vias_checked"] == 2           # VIA4 / VIA3


# 6. the DRC gate


def test_the_drc_gate_passes_a_same_metal_tapped_device_on_the_auto_metal(tmp_path):
    """The golden geometry with 3 um same-metal taps on both windings and the fixture on ``auto`` (M4): the gate's
    conductors are the two windings' metals, no via is drawn or expected, and the verdict is pass."""
    gen, model, result = generate(tmp_path, fixture={**FIXTURE, "metal": "auto"}, **SAME, **dict.fromkeys(WIDTHS, 3.0))
    assert result.fixture_metal == "M4" and expected_conductors(gen, model) == ["M6", "M5"]
    assert json.loads(result.manifest_path.read_text())["via_landing_audit"] == {"status": "pass", "vias_checked": 0}
    record = gate(gen, model, result)
    assert record["outcome"] == "pass", record


def test_a_narrow_opening_needs_the_other_windings_port_spacing_widened(tmp_path):
    """The tap port sits between the other winding's port pair and its ground stub between theirs on the fixture's metal. A
    1.55 um secondary opening puts the 3 um CTP stub 0.05 um from the P2 and N2 stubs on M4 (min space 0.1 um). With M4
    chosen under ``metal_rule: shared`` (``auto`` takes M4 under either rule here) the gate fails on min_space there, twice;
    widening the secondary's port pair by 2 um (10.1 um, the natural pitch is 8.1 um) makes room and the gate passes. Under
    ``free`` M4 holds the fixture alone, so the three stubs' spacing is the fixture's own and the gate passes the natural
    pitch too (T19.5). The via-stack tap cannot draw this geometry at all: its M5 pad sits in the secondary's opening."""
    narrow = {"secondary_opening_um": 1.55, "ct_primary_metal": "6", "ct_primary_width_um": 3.0, "port_order": [*XFM_PORTS, "CTP"]}
    shared = {**AUTO_STUBS, "metal": "auto", "metal_rule": "shared"}
    gen, model, natural = generate(tmp_path, "natural", shared, **narrow)
    assert (natural.fixture_metal, natural.fixture_metal_rule) == ("M4", "shared")
    record = gate(gen, model, natural)
    assert record["outcome"] == "fail" and record["violations"] == [{"kind": "min_space", "layer": "M4", "count": 2}], record
    gen, model, widened = generate(tmp_path, "widened", shared, **narrow, secondary_port_spacing_um=10.1)
    assert gate(gen, model, widened)["outcome"] == "pass"
    gen, model, free = generate(tmp_path / "free", "natural", {**AUTO_STUBS, "metal": "auto"}, **narrow)
    assert (free.fixture_metal, free.fixture_metal_rule) == ("M4", "free") and gate(gen, model, free)["outcome"] == "pass"
    assert free.gds_path.read_bytes() == natural.gds_path.read_bytes()   # the same drawing (one file name: the top cell's)
    with pytest.raises(PortError, match="primary/secondary nets short"):
        generate(tmp_path, "stack", {**AUTO_STUBS, "metal": "auto"}, **{**narrow, "ct_primary_metal": "4", "ct_primary_width_um": None})


# 7. xfm_ms: T19.4 kept its single-turn primary refusing a tap on its own metal; T19.6 lifted that guard (the same
# _bs_center_tap construction), tested in test_xfm_ms_same_metal_tap.py
