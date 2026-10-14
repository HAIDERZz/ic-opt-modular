"""N-93 (``docs/refactor/N93_N94_N53_SMALL_ITEMS_SPEC.md``, section C): a via-stack tap too close to the other winding.

A via-stack centre tap of xfm_bs drops from its winding's metal through a W x W stack at the closed column, which points
at the other winding's lead side. When the other winding's metal lies on the way down, the stack draws its pad there
(and its lead, when the tap metal is that metal). Closer to the other winding than the metal's spacing for those widths
and that parallel run, the build used to pass the pcell and fail the product DRC gate afterwards, without saying why;
now the pcell refuses it with the gap, the run, the rule value, the widths and the opening. The gap is measured on the
drawing, so every placement counts, not only the concentric one. demo_6m: windings on M6 / M5, the M5 rules read from
the profile; the N-93 case itself needs the private profile.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("klayout.db")

from ic_opt.em.pcell import _pcell_xfm_bs, get_generator
from ic_opt.em.pcell._pcell_core import PortError, _required_parallel_spacing, process_rule_context
from ic_opt.em.pcell._pcell_xfm_bs import xfm_bs
from ic_opt.em.pcell.drc_audit import (
    audit_gds,
    expected_conductors,
    fixture_exemptions,
    product_scope_record,
)
from ic_opt.em.pcell.stack import use_stack
from tests.ic_opt.pcell.conftest import requires_profile
from tests.ic_opt.pcell.test_golden import FIXTURE, XFM_PORTS

PROFILE = "demo_6m"
#: primary on M6 tapped through M5 (the secondary's metal) down to M4: the stack's M5 pad faces the secondary's leads
N93_LIKE = {"OD_P": 100.0, "OD_S": 100.0, "W_P": 10.0, "W_S": 5.0, "OPENING_P": 8.0, "OPENING_S": 8.0, "LEAD_P": 20.0,
            "LEAD_S": 20.0, "CENTER_SPACING": 0.0, "PRI_ME": "6", "SEC_ME": "5", "CT_P_ME": "4"}


def build(**over):
    return xfm_bs(**{**N93_LIKE, **over, "process": process_rule_context(PROFILE)})


def m5_min_space() -> float:
    return process_rule_context(PROFILE).adapter.metal_rule("M5").min_space_um


def refusal(**over) -> str:
    with pytest.raises(PortError, match="via-stack tap") as err:
        build(**over)
    return str(err.value)


# 1. the N-93 shape: the stack's pad between the other winding's leads


def test_a_pad_too_close_to_the_other_windings_leads_is_refused_with_its_numbers():
    """The M5 pad (+-5 um) and the secondary's M5 leads (from +-5.2 um) overlap over 5 um in x: 0.2 um apart, below the
    M5 spacing. The message names the gap, the run, the rule value, both widths and the opening. A gap of exactly the
    rule builds, and so does a wider one."""
    rule = m5_min_space()
    assert rule > 0.2
    message = refusal(OPENING_S=5.2)
    for part in ("the primary's via-stack tap (CT_P_ME=M4)", "a 10 x 10 um pad on M5, the secondary's metal",
                 "0.200 um from the secondary's own M5 metal over a 5.000 um parallel run",
                 f"below the {rule:g} um profile demo_6m asks between M5 lines W_P=10 and W_S=5 um wide",
                 "OPENING_S=5.2 um"):
        assert part in message, message
    build(OPENING_S=5.0 + rule)                                           # the gap is the rule: builds
    build(OPENING_S=5.5)


def test_the_secondarys_tap_is_measured_the_same_way():
    """Secondary on M6 tapped through the primary's M5: its pad faces the primary's leads (their opening is OPENING_P)."""
    message = refusal(PRI_ME="5", SEC_ME="6", CT_P_ME=None, CT_S_ME="4", W_P=5.0, W_S=10.0, OPENING_P=5.2)
    assert "the secondary's via-stack tap (CT_S_ME=M4) draws a 10 x 10 um pad on M5, the primary's metal, 0.200 um" in message
    assert "W_S=10 and W_P=5 um wide" in message and "OPENING_P=5.2 um" in message


# 2. the rule: those widths, that parallel run


def test_the_rule_follows_the_widths_and_the_parallel_run():
    """demo_6m's M5 asks for more than its min space between lines both wider than its wide-line threshold over a run
    longer than that rule's. A tap metal equal to the secondary's (M5) runs the primary's 8 um lead 26 um between the
    secondary's 6 um leads: 1.5 um apart is refused under the wide-line rule, its width-and-run rule is named. The same
    1.5 um gap from a pad (a 6 um run), or from 5 um leads (not wider than the threshold), is only held to the min
    space, and builds."""
    ctx = process_rule_context(PROFILE)
    wide = _required_parallel_spacing(5, 6.0, 26.0, ctx)
    assert wide > 1.5 > m5_min_space()
    message = refusal(CT_P_ME="5", W_P=8.0, W_S=6.0, OPENING_S=5.5)
    assert "draws its 8 um wide lead on M5" in message and "1.500 um from the secondary's own M5 metal over a 26.000 um" in message
    assert f"below the {wide:g} um profile demo_6m asks between M5 lines W_P=8 and W_S=6 um wide over that run" in message
    build(CT_P_ME="5", W_P=8.0, W_S=6.0, OPENING_S=4.0 + wide)          # the gap is the rule
    build(CT_P_ME="4", W_P=8.0, W_S=6.0, OPENING_S=5.5)                 # a pad: the run is 6 um
    build(CT_P_ME="5", W_P=8.0, W_S=5.0, OPENING_S=5.5)                 # 5 um leads are not wide


# 3. the actual placement, not the concentric formula


def test_the_gap_is_measured_from_the_actual_placement():
    """OPENING_S - W_P/2 is the gap only where the pad overlaps the leads in x. A smaller primary (OD_P 88) puts the pad
    1 um short of where the leads begin: the corner is 1.02 um away and the build stands, though that formula says 0.2.
    OD_P 90 brings the corners to 0.2 um. Moved apart by CENTER_SPACING 19 the pad meets the secondary's ring instead of
    its leads: 0.092 um from its inner chamfer (18 builds). And an inward port-spacing jog brings the secondary's leads
    to 0.3 um of a pad that their opening (8 um) keeps 3 um away."""
    build(OD_P=88.0, OPENING_S=5.2)
    assert "0.200 um from the secondary's own M5 metal at a corner" in refusal(OD_P=90.0, OPENING_S=5.2)
    apart = {"OD_P": 40.0, "OD_S": 40.0, "W_S": 10.0, "OPENING_P": 4.0, "OPENING_S": 4.0}
    build(**apart, CENTER_SPACING=18.0)
    assert "0.092 um from the secondary's own M5 metal at a corner" in refusal(**apart, CENTER_SPACING=19.0)
    build(OD_P=110.0, PORT_SPACING_S=15.8)
    assert re.search(r"0\.300 um from the secondary's own M5 metal over a [\d.]+ um parallel run",
                     refusal(OD_P=110.0, PORT_SPACING_S=15.6))


def test_contact_stays_a_short_and_nothing_else_changes():
    """A pad that touches the leads is still the short gate's (it runs first); reference mode has no rules and builds
    the refused geometry; a same-metal tap and an untapped primary draw nothing on M5 and build at any opening the
    windings allow."""
    with pytest.raises(PortError, match="primary/secondary nets short"):
        build(OPENING_S=4.9)
    xfm_bs(**{**N93_LIKE, "OPENING_S": 5.2}, process=None)
    build(CT_P_ME="6", OPENING_S=1.0)
    build(CT_P_ME=None, OPENING_S=1.0)


# 4. the DRC gate agrees


def generate(tmp_path: Path, name: str, **fields):
    gen = get_generator("clean_port_xfm_bs", plugin_module="builtin:clean_port")
    config = {"process_profile": PROFILE, "primary_outer_diameter_um": 100.0, "secondary_outer_diameter_um": 100.0,
              "primary_width_um": 10.0, "secondary_width_um": 5.0, "primary_opening_um": 8.0, "secondary_opening_um": 8.0,
              "primary_lead_length_um": 20.0, "secondary_lead_length_um": 20.0, "center_spacing_um": 0.0,
              "primary_metal": "6", "secondary_metal": "5", "ct_primary_metal": "4", "ground_fixture": dict(FIXTURE), **fields}
    config["port_order"] = [*XFM_PORTS, *(["CTP"] if config.get("ct_primary_metal") else []),
                            *(["CTS"] if config.get("ct_secondary_metal") else [])]
    model = gen.config_model.model_validate(config)
    return gen, model, gen.generate(model, outdir=tmp_path / name, gds_name="bs.gds")       # one file name: the top cell's


GATE_CASES = {
    "pad_0.2": {"secondary_opening_um": 5.2},
    "pad_rule": {"secondary_opening_um": 5.4},
    "lead_wide_1.5": {"ct_primary_metal": "5", "primary_width_um": 8.0, "secondary_width_um": 6.0, "secondary_opening_um": 5.5},
    "lead_wide_rule": {"ct_primary_metal": "5", "primary_width_um": 8.0, "secondary_width_um": 6.0, "secondary_opening_um": 6.0},
    "short_of_the_leads": {"primary_outer_diameter_um": 88.0, "secondary_opening_um": 5.2},
    "corner": {"primary_outer_diameter_um": 90.0, "secondary_opening_um": 5.2},
    "ring_apart_18": {"primary_outer_diameter_um": 40.0, "secondary_outer_diameter_um": 40.0, "secondary_width_um": 10.0,
                      "primary_opening_um": 4.0, "secondary_opening_um": 4.0, "center_spacing_um": 18.0},
    "ring_apart_19": {"primary_outer_diameter_um": 40.0, "secondary_outer_diameter_um": 40.0, "secondary_width_um": 10.0,
                      "primary_opening_um": 4.0, "secondary_opening_um": 4.0, "center_spacing_um": 19.0},
    "jog_15.8": {"primary_outer_diameter_um": 110.0, "secondary_port_spacing_um": 15.8},
    "jog_15.6": {"primary_outer_diameter_um": 110.0, "secondary_port_spacing_um": 15.6},
    "extended": {"secondary_opening_um": 5.2, "straight_extension_um": 6.0},
    "secondary_tap": {"primary_metal": "5", "secondary_metal": "6", "ct_primary_metal": None, "ct_secondary_metal": "4",
                      "primary_width_um": 5.0, "secondary_width_um": 10.0, "primary_opening_um": 5.2},
}


@pytest.mark.parametrize("name", sorted(GATE_CASES))
def test_the_pcell_refuses_exactly_what_the_drc_gate_would_fail(name, tmp_path, monkeypatch):
    """Each case built through the generator with the new check, and again with it switched off, the GDS audited: the
    check refuses it exactly when the audit finds a spacing violation on M5, the metal the tap stack crosses; what it
    builds passes the product DRC gate, byte for byte the drawing without the check."""
    fields = GATE_CASES[name]
    try:
        gen, model, result = generate(tmp_path, "checked", **fields)
        refused = None
    except PortError as exc:
        refused = str(exc)
    if refused is None:
        with use_stack(PROFILE):
            record = product_scope_record(audit_gds(result.gds_path, PROFILE), expected_conductors(gen, model),
                                          ignore_findings=fixture_exemptions(PROFILE, result.fixture_metal, result.fixture_metal_rule))
        assert record["outcome"] == "pass", record
    monkeypatch.setattr(_pcell_xfm_bs, "_bs_tap_stack_gap", lambda *args, **kwargs: None)
    _, _, unchecked = generate(tmp_path, "unchecked", **fields)
    with use_stack(PROFILE):
        findings = {(v.kind, v.layer) for v in audit_gds(unchecked.gds_path, PROFILE).violations if v.layer == "M5"}
    assert findings <= {("min_space", "M5"), ("wide_parallel_spacing", "M5")}, findings
    assert bool(findings) == (refused is not None), (refused, findings)
    if refused is None:
        assert unchecked.gds_path.read_bytes() == result.gds_path.read_bytes()


# 5. the N-93 case on its own profile


@requires_profile("n28_1p10m")
def test_the_n93_case_is_refused_and_opening_7_builds(tmp_path):
    """The tapped-table pilot's failed point (primary on M10, secondary on M9, both taps on M8; OD 50 / 42.5, W 10 / 5,
    concentric, openings 6): the M8 tap's M9 pad sits 1.0 um from the secondary's M9 leads over the 8.75 um they
    overlap. Refused with the rule value the profile gives those widths and that run (read here, not written down). A
    concentric W 10 / 5 point of the table that builds at opening 7 (OD 60) is refused at 6 the same way."""
    gen = get_generator("clean_port_xfm_bs", plugin_module="builtin:clean_port")
    ctx = process_rule_context("n28_1p10m")

    def config(od_p, od_s, opening):
        return gen.config_model.model_validate({
            "process_profile": "n28_1p10m", "port_order": [*XFM_PORTS, "CTP", "CTS"],
            "primary_outer_diameter_um": od_p, "secondary_outer_diameter_um": od_s, "primary_width_um": 10.0,
            "secondary_width_um": 5.0, "primary_opening_um": opening, "secondary_opening_um": opening,
            "primary_lead_length_um": 20.0, "secondary_lead_length_um": 20.0, "center_spacing_um": 0.0,
            "primary_metal": "10", "secondary_metal": "9", "ct_primary_metal": "8", "ct_secondary_metal": "8",
            "ground_fixture": {"inner_margin_um": 15.0, "ring_width_um": 50.0, "stub_length_um": 2.0, "stub_chamfer_um": 0.0}})

    rule = _required_parallel_spacing(9, 5.0, 8.75, ctx)
    with pytest.raises(PortError) as err:
        gen.generate(config(50.0, 42.5, 6.0), outdir=tmp_path / "n93", gds_name="n93.gds")
    message = str(err.value)
    for part in ("the primary's via-stack tap (CT_P_ME=M8) draws a 10 x 10 um pad on M9, the secondary's metal",
                 "1.000 um from the secondary's own M9 metal over a 8.750 um parallel run",
                 f"below the {rule:g} um profile n28_1p10m asks between M9 lines W_P=10 and W_S=5 um wide",
                 "OPENING_S=6 um"):
        assert part in message, message
    with pytest.raises(PortError, match=r"1\.000 um from the secondary's own M9 metal"):
        gen.generate(config(60.0, 60.0, 6.0), outdir=tmp_path / "six", gds_name="six.gds")
    gen.generate(config(60.0, 60.0, 7.0), outdir=tmp_path / "seven", gds_name="seven.gds")
