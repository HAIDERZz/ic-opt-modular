"""M0.2: the golden geometry set.

Representative builds of every family on the packaged public ``demo_6m`` profile
are committed as GDS under ``golden/``. Each test rebuilds one case and requires
physical equality (``gds_compare``: per-layer merged XOR empty, labels equal).

A change that is meant to alter default geometry regenerates the goldens with
``IC_OPT_REGENERATE_GOLDEN=1 pytest tests/ic_opt/pcell/test_golden.py`` and, in
the same commit, bumps ``ic_opt.em.pcell.GEOMETRY_VERSION``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("klayout.db")

from ic_opt.em.pcell import get_generator
from ic_opt.em.pcell.drc_audit import audit_gds
from ic_opt.em.pcell.gds_compare import compare_gds

GOLDEN = Path(__file__).parent / "golden"
FIXTURE = {"inner_margin_um": 15.0, "ring_width_um": 20.0, "stub_width_um": 5.0, "stub_length_um": 2.0, "stub_chamfer_um": 0.0}
XFM_PORTS = ["P1", "N1", "P2", "N2"]


def _ind(**over):
    base = {"port_order": ["P1", "N1"], "outer_diameter_um": 100.0, "width_um": 5.0, "spacing_um": 2.0, "opening_um": 8.0,
            "lead_length_um": 20.0, "turns": 1, "top_metal": "6", "bottom_metal": "5"}
    return "clean_port_ind_sym", {**base, **over}


def _bs(**over):
    base = {"port_order": XFM_PORTS, "primary_outer_diameter_um": 100.0, "secondary_outer_diameter_um": 100.0, "primary_width_um": 5.0,
            "secondary_width_um": 5.0, "primary_opening_um": 8.0, "secondary_opening_um": 8.0, "primary_lead_length_um": 20.0,
            "secondary_lead_length_um": 20.0, "center_spacing_um": 0.0, "primary_metal": "6", "secondary_metal": "5"}
    return "clean_port_xfm_bs", {**base, **over}


def _ms(**over):
    base = {"port_order": XFM_PORTS, "single_outer_diameter_um": 100.0, "multi_outer_diameter_um": 76.0, "single_width_um": 6.0,
            "multi_width_um": 3.0, "single_opening_um": 8.0, "multi_opening_um": 6.0, "single_lead_length_um": 20.0,
            "multi_lead_length_um": 15.0, "multi_turns": 3, "multi_spacing_um": 2.0, "center_spacing_um": 0.0,
            "single_metal": "6", "multi_metal": "5"}
    return "clean_port_xfm_ms", {**base, **over}


def _balun(**over):
    base = {"port_order": XFM_PORTS, "primary_outer_diameter_um": 200.0, "secondary_outer_diameter_um": 184.0, "primary_width_um": 5.0,
            "secondary_width_um": 5.0, "spacing_um": 3.0, "primary_opening_um": 8.0, "secondary_opening_um": 12.0,
            "primary_lead_length_um": 20.0, "secondary_lead_length_um": 20.0, "primary_turns": 1, "secondary_turns": 1,
            "center_spacing_um": 0.0, "balun_metal": "6"}
    return "clean_port_xfm_balun", {**base, **over}


def _tw(**over):
    base = {"port_order": XFM_PORTS, "outer_diameter_um": 260.0, "width_um": 6.0, "spacing_um": 6.0, "ring_count": 3,
            "opening_p_um": 10.0, "opening_n_um": 10.0, "lead_length_um": 20.0, "top_metal": "6"}
    return "clean_port_xfm_tw", {**base, **over}


def _il(**over):
    base = {"port_order": XFM_PORTS, "outer_diameter_um": 200.0, "width_um": 5.0, "spacing_um": 2.5, "turns": 3,
            "opening_p_um": 18.0, "opening_s_um": 18.0, "lead_p_um": 20.0, "lead_s_um": 20.0, "top_metal": "6"}
    return "clean_port_xfm_il", {**base, **over}


CASES = {
    "ind_sym_nt1": _ind(),
    "ind_sym_nt2_ct": _ind(turns=2, ct_metal="4", port_order=["P1", "N1", "CT"]),          # the compact two-turn planner + a tap
    "ind_sym_nt3": _ind(turns=3, outer_diameter_um=120.0),
    "xfm_bs": _bs(),
    "xfm_bs_ct": _bs(ct_primary_metal="4", ct_secondary_metal="3", port_order=[*XFM_PORTS, "CTP", "CTS"]),
    "xfm_ms_nt3": _ms(),
    "xfm_ms_nt2": _ms(multi_turns=2),
    "xfm_balun": _balun(),
    "xfm_balun_nt2": _balun(primary_turns=2, primary_outer_diameter_um=220.0),         # the multi-turn side is the primary
    "xfm_tw_nr3": _tw(),
    "xfm_tw_nr5": _tw(ring_count=5, outer_diameter_um=300.0),
    "xfm_il_nt3": _il(),
    "xfm_il_nt2_ct": _il(turns=2, top_metal="5", ct_primary_metal="6", ct_secondary_metal="6", port_order=[*XFM_PORTS, "CTP", "CTS"]),   # upward taps
}

# Findings the packaged audit raises on a golden the generator accepted: the generator's nominal W=5.0 sits on the
# wide-parallel threshold while the quantized chamfer draws the diagonals 5.006 wide and 1.994 apart (plan F2).
# Emptied by M1.1 (single chamfer source); nothing may be added here.
KNOWN_FINDINGS = {"ind_sym_nt2_ct": {("wide_parallel_spacing", "M6")}, "ind_sym_nt3": {("wide_parallel_spacing", "M6")}}


def build(name: str, outdir: Path) -> Path:
    generator_id, config = CASES[name]
    generator = get_generator(generator_id, plugin_module="builtin:clean_port")
    full = {**config, "process_profile": "demo_6m", "ground_fixture": dict(FIXTURE)}
    if generator_id == "clean_port_xfm_ms":
        full["ground_fixture"].update({"stub_width_um": 6.0, "stub_width_by_port_um": {"P2": 3.0, "N2": 3.0}})
    return generator.generate(generator.config_model.model_validate(full), outdir=outdir, gds_name=f"{name}.gds").gds_path


@pytest.mark.parametrize("name", sorted(CASES))
def test_golden_geometry_is_physically_unchanged(name, tmp_path):
    fresh = build(name, tmp_path)
    findings = {(v.kind, v.layer) for v in audit_gds(fresh, "demo_6m").violations} - {("max_width", "M1")}
    assert findings == KNOWN_FINDINGS.get(name, set()), findings          # a golden is clean geometry, not just reproducible geometry
    golden = GOLDEN / f"{name}.gds"
    if os.environ.get("IC_OPT_REGENERATE_GOLDEN"):
        golden.write_bytes(fresh.read_bytes())
        return
    assert golden.exists(), f"missing golden {golden.name}: regenerate with IC_OPT_REGENERATE_GOLDEN=1 (and bump GEOMETRY_VERSION)"
    result = compare_gds(golden, fresh)
    assert result["physical_equal"], {k: result[k] for k in ("changed_layers", "removed_labels", "added_labels")}


def test_every_golden_file_has_a_case():
    assert {p.stem for p in GOLDEN.glob("*.gds")} == set(CASES)
