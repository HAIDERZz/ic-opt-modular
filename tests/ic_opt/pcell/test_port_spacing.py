"""M3.1: an independent port spacing -- the leads jog to the requested tip pitch, refused when they cannot."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("klayout.db")

from ic_opt.em.pcell import get_generator
from ic_opt.em.pcell._pcell_core import PortError
from ic_opt.em.pcell.drc_audit import audit_gds
from ic_opt.em.pcell.gds_compare import compare_gds
from tests.ic_opt.pcell.test_golden import CASES, FIXTURE, build


def generate(case: str, outdir, **over):
    generator_id, config = CASES[case]
    g = get_generator(generator_id, plugin_module="builtin:clean_port")
    full = {**config, **over, "process_profile": "demo_6m", "ground_fixture": dict(FIXTURE)}
    if generator_id == "clean_port_xfm_ms":
        full["ground_fixture"].update({"stub_width_um": 6.0, "stub_width_by_port_um": {"P2": 3.0, "N2": 3.0}})
    return g.generate(g.config_model.model_validate(full), outdir=outdir, gds_name=f"{case}.gds")


def ports_of(result):
    return {p["logical_name"]: p for p in json.loads(result.manifest_path.read_text())["ports"]}


@pytest.mark.parametrize(("case", "field", "natural"), [("ind_sym_nt1", "port_spacing_um", 2 * 8.0 + 5.0), ("ind_sym_nt2_ct", "port_spacing_um", 21.0),
                                                        ("xfm_bs", "secondary_port_spacing_um", 21.0), ("xfm_ms_nt3", "secondary_port_spacing_um", 2 * 6.0 + 3.0),
                                                        ("xfm_balun", "primary_port_spacing_um", 21.0)])
def test_leads_jog_to_the_requested_pitch_and_stay_audit_clean(case, field, natural, tmp_path):
    reference = build(case, tmp_path / "ref")
    for pitch in (natural + 10.0, natural - 6.0):
        r = generate(case, tmp_path / f"p{pitch}", **{field: pitch})
        ports = ports_of(r)
        a, b = ("P2", "N2") if field.startswith("secondary") else ("P1", "N1")
        assert ports[a]["y_um"] - ports[b]["y_um"] == pytest.approx(pitch) and ports[a]["width_um"] == ports[b]["width_um"]
        assert ports[a]["x_um"] == pytest.approx(ports_of_reference(reference)[a]["x_um"])              # the tip stays at the lead's length
        findings = {(v.kind, v.layer) for v in audit_gds(r.gds_path, "demo_6m").violations} - {("max_width", "M1")}
        assert not findings, findings
        assert not compare_gds(reference, r.gds_path)["physical_equal"]
    same = generate(case, tmp_path / "same", **{field: natural})
    assert compare_gds(reference, same.gds_path)["physical_equal"]                                    # the natural pitch draws the plain leads


def ports_of_reference(gds_path):
    return {p["logical_name"]: p for p in json.loads((gds_path.parent / "geometry_manifest.json").read_text())["ports"]}


def test_impossible_pitches_are_refused(tmp_path):
    with pytest.raises(PortError, match="needs LEAD >="):
        generate("ind_sym_nt1", tmp_path / "long", port_spacing_um=80.0)                                # a 29.5 um jog does not fit a 20 um lead
    with pytest.raises(PortError, match="below the M6 spacing floor"):
        generate("ind_sym_nt1", tmp_path / "close", port_spacing_um=5.5)                                # 0.5 um between two 5 um leads
    with pytest.raises(PortError, match="needs LEAD >="):
        generate("xfm_bs", tmp_path / "bs", primary_port_spacing_um=60.0)
