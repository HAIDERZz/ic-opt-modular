"""The MS multi coil needs M3/M2, so M4/M3 is a usable public stack."""

import klayout.db as kdb

from ic_opt.em.pcell.drc_audit import (
    audit_gds,
    product_scope_record,
    require_layers_from_config,
)
from ic_opt.em.pcell.generator_plugin import PLUGIN_GENERATORS
from ic_opt.em.pcell.process_rules import get_process_rule_profile
from tests.ic_opt.pcell.conftest import requires_profile


def _drawn_conductors(gds_path, profile_id):
    import klayout.db as kdb

    prof = get_process_rule_profile(profile_id)
    rev = {tuple(c.drawing): name for name, c in prof.layer_catalog.conductors.items()}
    ly = kdb.Layout()
    ly.read(str(gds_path))
    top = ly.top_cells()[0]
    used = set()
    for li in ly.layer_indexes():
        info = ly.get_info(li)
        if any(True for _ in top.begin_shapes_rec(li).each()):
            name = rev.get((info.layer, info.datatype))
            if name:
                used.add(name)
    return used


@requires_profile("n28_1p10m")
def test_ms_m4_m3_generates_only_the_required_via2_crossunder(tmp_path):
    generator = PLUGIN_GENERATORS["clean_port_xfm_ms"]
    config = generator.config_model.model_validate({
        "process_profile": "n28_1p10m", "port_order": ["P1", "N1", "P2", "N2"],
        "ground_fixture": {"inner_margin_um": 15., "ring_width_um": 50.,
                           "stub_length_um": 2., "stub_chamfer_um": 0.},
        "single_outer_diameter_um": 180., "multi_outer_diameter_um": 160.,
        "single_width_um": 2., "multi_width_um": 2.,
        "single_opening_um": 8., "multi_opening_um": 8.,
        "single_lead_length_um": 20., "multi_lead_length_um": 20.,
        "multi_turns": 2, "multi_spacing_um": 3., "center_spacing_um": 0.,
        "single_metal": "4", "multi_metal": "3",
    })
    result = generator.generate(config, outdir=tmp_path, gds_name="device.gds")
    assert _drawn_conductors(result.gds_path, config.process_profile) == {"M1", "M2", "M3", "M4"}
    profile = get_process_rule_profile(config.process_profile)
    layout = kdb.Layout()
    layout.read(str(result.gds_path))
    top = layout.top_cell()
    drawn_vias = {name for name, via in profile.layer_catalog.vias.items()
                  if not kdb.Region(top.begin_shapes_rec(layout.layer(*via.drawing))).is_empty()}
    assert drawn_vias == {"VIA2"}
    report = product_scope_record(
        audit_gds(result.gds_path, config.process_profile),
        require_layers_from_config("clean_port_xfm_ms", config.model_dump()),
        ignore_findings=frozenset({("max_width", "M1")}))
    assert report["outcome"] == "pass", report
