"""N-121 (``docs/refactor/N121_XFM_MS_TAP_GAP_SPEC.md``): N-93's tap-pad gap check for xfm_ms, and the balun's primary tap.

xfm_ms taps its single-turn primary as xfm_bs taps a winding: a via-stack tap drops from the primary's metal through a
W_S x W_S stack at its closed column, which faces the multi-turn secondary's lead side, and draws that pad on every metal
on the way (its lead too, on the tap metal). The secondary lies below the primary: its metal holds its turns and leads,
the next one down its crossunder, lower ones its own tap's via tower. Where the stack reaches them closer than the
metal's spacing for those widths and that parallel run, the build used to pass the pcell and fail the product DRC gate
afterwards (on demo_6m, ``min_space`` M5 x2 for the case N-93's coder found); now the pcell refuses it with the gap, the
run, the rule value, the widths and the opening, measured on the drawing as N-93 measures xfm_bs's taps. The balun's
primary tap faces the secondary's escape bars the same way; the balun's inter-net gate already refused those builds
before the fixture and the DRC gate, and now the refusal names the numbers. demo_6m (the rules read from the profile)
unless noted; the private profile's case reads its rule at run time.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

pytest.importorskip("klayout.db")

from ic_opt.em.pcell import _pcell_xfm_balun, _pcell_xfm_ms, get_generator
from ic_opt.em.pcell._pcell_core import (
    PortError,
    _required_parallel_spacing,
    process_rule_context,
    write_gds,
)
from ic_opt.em.pcell._pcell_xfm_balun import xfm_balun
from ic_opt.em.pcell._pcell_xfm_ms import xfm_ms
from ic_opt.em.pcell.drc_audit import (
    audit_gds,
    expected_conductors,
    fixture_exemptions,
    product_scope_record,
)
from ic_opt.em.pcell.process_rules import PROFILE_DIRS_ENV_VAR
from ic_opt.em.pcell.stack import use_stack
from tests.ic_opt.pcell.conftest import requires_profile
from tests.ic_opt.pcell.test_golden import CASES, FIXTURE, XFM_PORTS
from tests.ic_opt.pcell.test_manufacturing_grid import demo
from tests.ic_opt.pcell.test_profile_validation import write_profile

PROFILE = "demo_6m"
#: the golden ms (primary on M6, secondary on M5 with its crossunder on M4) with a 10 um primary tapped through M5 to M4:
#: the stack's M5 pad (+-5 um) between the secondary's leads (from +-OPENING_M)
MS = {"OD_S": 100.0, "OD_M": 76.0, "W_S": 10.0, "W_M": 3.0, "OPENING_S": 8.0, "OPENING_M": 6.0, "LEAD_S": 20.0,
      "LEAD_M": 15.0, "NT_M": 3, "S_M": 2.0, "CENTER_SPACING": 0.0, "SINGLE_ME": "6", "MULTI_ME": "5", "CT_P_ME": "4"}
#: the golden balun (both windings on M6, the secondary's escape on M5) with its primary tapped through M5 to M4: the
#: stack's M5 pad (+-2.5 um) between the escape bars (from +-OPENING_S)
BALUN = {"OD_P": 200.0, "OD_S": 184.0, "W_P": 5.0, "W_S": 5.0, "S": 3.0, "OPENING_P": 8.0, "OPENING_S": 12.0,
         "LEAD_P": 20.0, "LEAD_S": 20.0, "BALUN_ME": "6", "CT_P_ME": "4"}


def ms(**over):
    return xfm_ms(**{**MS, **over, "process": process_rule_context(PROFILE)})


def balun(**over):
    return xfm_balun(**{**BALUN, **over, "process": process_rule_context(PROFILE)})


def m5_min_space() -> float:
    return process_rule_context(PROFILE).adapter.metal_rule("M5").min_space_um


def refusal(build, **over) -> str:
    with pytest.raises(PortError, match="via-stack tap") as err:
        build(**over)
    return str(err.value)


# 1. xfm_ms: the case N-93 found, the pad between the secondary's leads


@pytest.mark.parametrize("turns", [3, 2, 4])
def test_a_pad_between_the_secondarys_leads_is_refused_with_its_numbers(turns):
    """The M5 pad (+-5 um) and the secondary's M5 leads (from +-5.2 um) run side by side over the pad's 10 um: 0.2 um
    apart, below the M5 spacing (the DRC audit found ``min_space`` M5 x2 on the build the pcell used to pass). The
    message names the gap, the run, the rule value, both widths and the opening. A gap of exactly the rule builds, and
    so does the golden opening."""
    rule = m5_min_space()
    assert rule > 0.2
    message = refusal(ms, NT_M=turns, OPENING_M=5.2)
    for part in ("xfm_ms: the single-turn primary's via-stack tap (CT_P_ME=M4)",
                 "draws a 10 x 10 um pad on M5, the multi-turn secondary's metal",
                 "0.200 um from the multi-turn secondary's own M5 metal over a 10.000 um parallel run",
                 f"below the {rule:g} um profile demo_6m asks between M5 lines W_S=10 and W_M=3 um wide",
                 "the multi-turn secondary's leads lie OPENING_M=5.2 um off the centre line",
                 "a tap on the single-turn primary's own metal draws nothing on M5"):
        assert part in message, message
    ms(NT_M=turns, OPENING_M=5.0 + rule)                                  # the gap is the rule: builds
    ms(NT_M=turns)


def test_the_rule_follows_the_widths_and_the_parallel_run():
    """demo_6m's M5 asks for more than its min space between lines both wider than its wide-line threshold over a run
    longer than that rule's. A tap metal equal to the secondary's (M5) runs the primary's 8 um lead 36 um between the
    secondary's 6 um leads (40 um long): 1.5 um apart is refused under the wide-line rule; a gap equal to it builds. The
    same 1.5 um from a pad (an 8 um run), or from 5 um leads (not wider than the threshold), is held to the min space
    only, and builds."""
    ctx = process_rule_context(PROFILE)
    wide = _required_parallel_spacing(5, 6.0, 36.0, ctx)
    assert wide > 1.5 > m5_min_space()
    lead = {"CT_P_ME": "5", "W_S": 8.0, "W_M": 6.0, "LEAD_M": 40.0}
    message = refusal(ms, **lead, OPENING_M=5.5)
    assert "draws its 8 um wide lead on M5" in message and "1.500 um from the multi-turn secondary's own M5 metal over a 36.000 um" in message
    assert f"below the {wide:g} um profile demo_6m asks between M5 lines W_S=8 and W_M=6 um wide over that run" in message
    ms(**lead, OPENING_M=4.0 + wide)                                      # the gap is the rule
    ms(**{**lead, "CT_P_ME": "4"}, OPENING_M=5.5)                         # a pad: the run is 8 um
    ms(**{**lead, "W_M": 5.0}, OPENING_M=5.5)                             # 5 um leads are not wide


# 2. xfm_ms: the actual placement, not the concentric formula


def test_the_gap_is_measured_from_the_actual_placement():
    """OPENING_M - W_S/2 is the gap only where the pad lies alongside the secondary's leads. A smaller primary moved by
    CENTER_SPACING sits inside the secondary's turns: its M5 pad (tapped down to M3) faces the innermost turn's closed
    column, 0.2 um away at a centre spacing of 10.2 um, though the opening keeps the leads 2.5 um away (10.4 builds,
    10.0 touches). An inward jog of the secondary's port pair brings its leads to 0.2 um of a pad that their opening
    keeps 1 um away (14.0 builds); the straight extension moves the pad and the leads together."""
    inside = {"OD_S": 60.0, "W_S": 6.0, "NT_M": 4, "CT_P_ME": "3", "OPENING_M": 5.5}
    message = refusal(ms, **inside, CENTER_SPACING=10.2)
    assert "a 6 x 6 um pad on M5" in message and "0.200 um from the multi-turn secondary's own M5 metal over a 6.000 um" in message
    ms(**inside, CENTER_SPACING=10.4)
    with pytest.raises(PortError, match="primary/secondary nets short"):
        ms(**inside, CENTER_SPACING=10.0)
    assert re.search(r"0\.200 um from the multi-turn secondary's own M5 metal over a [\d.]+ um parallel run",
                     refusal(ms, PORT_SPACING_M=13.4))
    ms(PORT_SPACING_M=14.0)
    assert "0.200 um from the multi-turn secondary's own M5 metal over a 10.000 um" in refusal(ms, OPENING_M=5.2, STRAIGHT_EXTENSION=6.0)


@pytest.fixture
def wide_low(tmp_path, monkeypatch):
    """demo_6m with M4 and M3 asking for more spacing (1.0 / 1.2 um) than M5 (0.4 um)."""
    data = copy.deepcopy(demo()) | {"process_id": "demo_wide_low"}
    data["layout_rules"]["metal_width_space"]["M4"]["min_space_um"] = 1.0
    data["layout_rules"]["metal_width_space"]["M3"]["min_space_um"] = 1.2
    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(write_profile(tmp_path / "profiles", data, "demo_wide_low")))
    return "demo_wide_low"


def test_every_metal_of_the_secondarys_the_stack_reaches_is_measured(wide_low, tmp_path, monkeypatch):
    """The secondary draws below its own metal too: its crossunder (M4) and, tapped, its tap's via tower down to its tap
    metal (M2 here; two turns, so the tower stands at the innermost turn's column on the primary's side). A 4 um
    primary whose closed column sits 0.6 um outside that column clears M5 there (0.4 um) but not, on a profile whose M4
    and M3 ask for 1.0 and 1.2 um, M4 (tapped to M4, its lead) or M3 (tapped to M3, the larger shortfall): the refusal
    names that metal and what it is of the secondary's. Built without the check, the audit finds exactly those; 1.2 um
    away both build, and demo_6m builds all of them."""
    ctx = process_rule_context(wide_low)

    def build(od, ct, process=ctx):
        return xfm_ms(**{**MS, "OD_S": od, "W_S": 4.0, "OPENING_M": 4.0, "NT_M": 2, "CT_P_ME": ct, "CT_S_ME": "2"},
                      process=process)

    message = refusal(build, od=75.2, ct="4")
    assert "draws its 4 um wide lead on M4, the multi-turn secondary's crossunder metal, 0.600 um from the multi-turn " \
           "secondary's own M4 metal" in message and "below the 1 um profile demo_wide_low" in message, message
    message = refusal(build, od=75.2, ct="3")
    assert "draws its 4 um wide lead on M3, a metal the multi-turn secondary's tap reaches, 0.600 um" in message, message
    assert "below the 1.2 um profile demo_wide_low" in message
    for ct in ("4", "3"):
        build(76.4, ct)
        build(75.2, ct, process_rule_context(PROFILE))
    monkeypatch.setattr(_pcell_xfm_ms, "_ms_tap_stack_gap", lambda *args, **kwargs: None)
    for ct, expected in (("4", {("min_space", "M4")}), ("3", {("min_space", "M4"), ("min_space", "M3")})):
        gds = tmp_path / f"ms{ct}.gds"
        write_gds(build(75.2, ct), gds)
        with use_stack(wide_low):
            assert {(v.kind, v.layer) for v in audit_gds(gds, wide_low).violations} == expected


def test_contact_stays_a_short_and_nothing_else_changes():
    """A pad that touches the leads is still the short gate's (it runs first); reference mode has no rules and builds
    the refused geometry; a same-metal tap, an untapped primary and the secondary's own tap (two levels below its metal,
    never on the primary's, which lies above everything the secondary draws) build at a 3 um opening."""
    with pytest.raises(PortError, match="primary/secondary nets short"):
        ms(OPENING_M=4.9)
    xfm_ms(**{**MS, "OPENING_M": 5.2}, process=None)
    ms(CT_P_ME="6", OPENING_M=3.0)
    ms(CT_P_ME=None, OPENING_M=3.0)
    ms(CT_P_ME=None, CT_S_ME="3", OPENING_M=3.0)
    assert "0.200 um" in refusal(ms, CT_S_ME="3", OPENING_M=5.2)          # beside the secondary's tap, the same refusal


# 3. the balun: the primary's tap between the escape bars, refused before already, now with its numbers


def test_the_balun_names_its_primary_taps_gap():
    """The balun's primary tap leaves the primary's closed column toward the secondary's escape, whose bars run on M5 at
    +-OPENING_S: a stack tapped to M4 draws its 5 x 5 um pad on M5 between them, tapped to M5 its lead too. 0.1 um /
    0.3 um apart the balun's inter-net gate refused these before (a message without numbers); now they are named. A
    gap of exactly the rule builds."""
    rule = m5_min_space()
    message = refusal(balun, OPENING_S=2.6)
    for part in ("xfm_balun: the primary's via-stack tap (CT_P_ME=M4) draws a 5 x 5 um pad on M5, the secondary's escape metal",
                 "0.100 um from the secondary's own M5 metal over a 5.000 um parallel run",
                 f"below the {rule:g} um profile demo_6m asks between M5 lines W_P=5 and W_S=5 um wide",
                 "the secondary's escape bars lie OPENING_S=2.6 um off the centre line (a wider OPENING_S or a narrower "
                 "W_P moves the pad away from them)"):
        assert part in message, message
    assert "own metal" not in message                                    # the balun has no same-metal tap
    assert "draws its 5 um wide lead on M5" in refusal(balun, CT_P_ME="5", OPENING_S=2.8)
    balun(OPENING_S=2.5 + rule)
    balun(CT_P_ME="5", OPENING_S=2.5 + rule)


def test_the_balun_keeps_its_other_refusals():
    """Only a tap's own shortfall is named: a pad that touches the escape bars, and builds without a primary tap (an
    untapped primary draws nothing below the plane; the secondary's tap leaves on the other side), keep what they did."""
    with pytest.raises(PortError, match="nets are closer than the inter-net"):
        balun(OPENING_S=2.4)
    balun(CT_P_ME=None, OPENING_S=2.6)
    balun(CT_P_ME=None, CT_S_ME="4", OPENING_S=2.6)


# 4. the DRC gate agrees


def generate(tmp_path: Path, family: str, name: str, **fields):
    generator_id, base = CASES["xfm_ms_nt3" if family == "ms" else "xfm_balun"]
    gen = get_generator(generator_id, plugin_module="builtin:clean_port")
    tap = {"primary_width_um": 10.0, "ct_primary_metal": "4"} if family == "ms" else {"ct_primary_metal": "4"}
    config = {**base, "process_profile": PROFILE, "ground_fixture": {k: v for k, v in FIXTURE.items() if k != "stub_width_um"},
              **tap, **fields}
    config["port_order"] = [*XFM_PORTS, *(["CTP"] if config.get("ct_primary_metal") else []),
                            *(["CTS"] if config.get("ct_secondary_metal") else [])]
    model = gen.config_model.model_validate({k: v for k, v in config.items() if v is not None})
    return gen, model, gen.generate(model, outdir=tmp_path / name, gds_name="xfm.gds")       # one file name: the top cell's


GATE_CASES = {
    "ms_pad_0.2": ("ms", {"secondary_opening_um": 5.2}),
    "ms_pad_rule": ("ms", {"secondary_opening_um": 5.4}),
    "ms_nt2_pad_0.2": ("ms", {"secondary_turns": 2, "secondary_opening_um": 5.2}),
    "ms_lead_wide_1.5": ("ms", {"ct_primary_metal": "5", "primary_width_um": 8.0, "secondary_width_um": 6.0,
                                "secondary_lead_length_um": 40.0, "secondary_opening_um": 5.5}),
    "ms_lead_wide_rule": ("ms", {"ct_primary_metal": "5", "primary_width_um": 8.0, "secondary_width_um": 6.0,
                                 "secondary_lead_length_um": 40.0, "secondary_opening_um": 6.0}),
    "ms_inside_10.2": ("ms", {"primary_outer_diameter_um": 60.0, "primary_width_um": 6.0, "secondary_turns": 4,
                              "ct_primary_metal": "3", "secondary_opening_um": 5.5, "center_spacing_um": 10.2}),
    "ms_inside_10.4": ("ms", {"primary_outer_diameter_um": 60.0, "primary_width_um": 6.0, "secondary_turns": 4,
                              "ct_primary_metal": "3", "secondary_opening_um": 5.5, "center_spacing_um": 10.4}),
    "ms_jog_13.4": ("ms", {"secondary_port_spacing_um": 13.4}),
    "ms_jog_14.0": ("ms", {"secondary_port_spacing_um": 14.0}),
    "ms_extended": ("ms", {"secondary_opening_um": 5.2, "straight_extension_um": 6.0}),
    "ms_both_taps": ("ms", {"ct_secondary_metal": "3", "secondary_opening_um": 5.2}),
    "ms_secondary_tap_only": ("ms", {"ct_primary_metal": None, "ct_secondary_metal": "3", "secondary_opening_um": 3.0}),
    "balun_pad_0.1": ("balun", {"secondary_opening_um": 2.6}),
    "balun_pad_rule": ("balun", {"secondary_opening_um": 2.9}),
    "balun_lead_0.3": ("balun", {"ct_primary_metal": "5", "secondary_opening_um": 2.8}),
    "balun_lead_rule": ("balun", {"ct_primary_metal": "5", "secondary_opening_um": 2.9}),
}


@pytest.mark.parametrize("name", sorted(GATE_CASES))
def test_the_pcell_refuses_exactly_what_the_drc_gate_would_fail(name, tmp_path, monkeypatch):
    """Each case built through the generator with the check, and again with it switched off (for the balun, its
    inter-net gate too, which refuses these builds without numbers), the GDS audited: the check refuses it exactly when
    the audit finds a spacing violation on the metals the tap stack reaches; what it builds passes the product DRC gate,
    byte for byte the drawing without the check."""
    family, fields = GATE_CASES[name]
    try:
        gen, model, result = generate(tmp_path, family, "checked", **fields)
        refused = None
    except PortError as exc:
        refused = str(exc)
        assert "via-stack tap" in refused, refused
    if refused is None:
        with use_stack(PROFILE):
            record = product_scope_record(audit_gds(result.gds_path, PROFILE), expected_conductors(gen, model),
                                          ignore_findings=fixture_exemptions(PROFILE, result.fixture_metal, result.fixture_metal_rule))
        assert record["outcome"] == "pass", record
    if family == "ms":
        monkeypatch.setattr(_pcell_xfm_ms, "_ms_tap_stack_gap", lambda *args, **kwargs: None)
    else:
        monkeypatch.setattr(_pcell_xfm_balun, "_balun_tap_stack_gap", lambda *args, **kwargs: None)
        monkeypatch.setattr(_pcell_xfm_balun, "_xfm_nets_are_drc_separate", lambda *args, **kwargs: True)
    _, _, unchecked = generate(tmp_path, family, "unchecked", **fields)
    crossed = {"M5", "M4", "M3"}                                          # below the primary's M6, down to the deepest tap metal
    with use_stack(PROFILE):
        findings = {(v.kind, v.layer) for v in audit_gds(unchecked.gds_path, PROFILE).violations if v.layer in crossed}
    assert findings <= {("min_space", "M5"), ("wide_parallel_spacing", "M5")}, findings
    assert bool(findings) == (refused is not None), (refused, findings)
    if refused is None:
        assert unchecked.gds_path.read_bytes() == result.gds_path.read_bytes()


# 5. the private profile


@requires_profile("n28_1p10m")
def test_the_private_profiles_ms_is_refused_with_its_rule():
    """The product's ms (primary on AP, secondary on M10 with its crossunder on M9) with a 10 um primary tapped down to
    M9: the stack's M10 pad between the secondary's leads, refused below the M10 spacing the profile gives those widths
    over that run (read here, not written down); half a rule more of opening builds."""
    ctx = process_rule_context("n28_1p10m")
    rule = _required_parallel_spacing(10, 3.0, 10.0, ctx)
    geometry = {**MS, "SINGLE_ME": "AP", "MULTI_ME": "10", "CT_P_ME": "9"}
    with pytest.raises(PortError) as err:
        xfm_ms(**{**geometry, "OPENING_M": round(5.0 + rule / 2, 2)}, process=ctx)
    message = str(err.value)
    for part in ("xfm_ms: the single-turn primary's via-stack tap (CT_P_ME=M9) draws a 10 x 10 um pad on M10",
                 "um from the multi-turn secondary's own M10 metal over a 10.000 um parallel run",
                 f"below the {rule:g} um profile n28_1p10m asks between M10 lines W_S=10 and W_M=3 um wide"):
        assert part in message, message
    gap = float(re.search(r"M10, the multi-turn secondary's metal, ([\d.]+) um from", message).group(1))
    assert gap == pytest.approx(rule / 2, abs=0.01)
    xfm_ms(**{**geometry, "OPENING_M": 5.0 + rule}, process=ctx)
