"""Actual shield masks, ground attachment, and legacy output identity."""
import json

import klayout.db as kdb
import pytest

from ic_opt.em.pcell import generator_plugin as gp
from ic_opt.em.pcell._pcell_core import PortError
from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter
from tests.ic_opt.pcell.conftest import requires_profile
from tests.ic_opt.pcell.test_clean_port_generator_plugin import (
    _ind_sym_config_dict,
    _xfm_bs_config_dict,
    _xfm_ms_config_dict,
)

pytestmark = requires_profile("n28_1p10m")

PGS = {"strip_width_um": 1., "strip_spacing_um": 2., "margin_um": 5.}
CASES = [
    (gp.CleanPortIndSymGenerator, _ind_sym_config_dict),
    (gp.CleanPortXfmBsGenerator, _xfm_bs_config_dict),
    (gp.CleanPortXfmMsGenerator, _xfm_ms_config_dict),
]


def _read(path):
    layout = kdb.Layout()
    layout.read(str(path))
    regions, labels = {}, []
    for li in layout.layer_indexes():
        info = layout.get_info(li)
        layer = (info.layer, info.datatype)
        regions[layer] = kdb.Region(layout.top_cell().begin_shapes_rec(li)).merged()
        for shape in layout.top_cell().shapes(li).each():
            if shape.is_text():
                labels.append((layer, shape.text.string, str(shape.text.trans)))
    return regions, sorted(labels)


@pytest.mark.parametrize("generator,config_dict", CASES)
@pytest.mark.parametrize("profile,lower", [("n28_1p10m", "10"), ("n65_1p9m", "9")])
def test_pgs_keeps_signals_and_forms_one_grounded_tree(tmp_path, generator, config_dict, profile, lower):
    payload = config_dict()
    payload["process_profile"] = profile
    if profile == "n65_1p9m" and generator is gp.CleanPortIndSymGenerator:
        # Existing N65 AP/RV-qualified winding (the N28 5um fixture cannot
        # fit the N65 AP-to-M9 bridge vias, even without a shield).
        payload.update(outer_diameter_um=160., width_um=6., spacing_um=4.)
    if "metal" in payload:
        payload["metal"] = "AP"
    if "primary_metal" in payload:
        payload.update(primary_metal="AP", secondary_metal=lower)
    baseline_config = generator.config_model.model_validate(payload)
    baseline = generator().generate(baseline_config, outdir=tmp_path / "off", gds_name="device.gds")
    none_config = generator.config_model.model_validate({**payload, "pgs": None})
    none = generator().generate(none_config, outdir=tmp_path / "none", gds_name="device.gds")
    assert "pgs" not in baseline_config.model_dump()
    assert baseline_config.model_dump() == none_config.model_dump()
    assert baseline.gds_path.read_bytes() == none.gds_path.read_bytes()

    enabled_config = generator.config_model.model_validate({**payload, "pgs": PGS})
    enabled = generator().generate(enabled_config, outdir=tmp_path / "on", gds_name="device.gds")
    before, labels_before = _read(baseline.gds_path)
    after, labels_after = _read(enabled.gds_path)
    m1 = tuple(get_geometry_rule_adapter(profile).layer("M1").drawing)
    assert labels_before == labels_after
    assert before.keys() == after.keys()
    assert all((before[layer] ^ after[layer]).is_empty() for layer in before if layer != m1)
    assert (before[m1] - after[m1]).is_empty()
    assert after[m1].area() > before[m1].area()
    assert after[m1].count() == 1
    assert sum(p.holes() for p in after[m1].each()) == sum(p.holes() for p in before[m1].each())
    manifest = json.loads(enabled.manifest_path.read_text())
    assert manifest["geometry"]["config"]["pgs"] == PGS
    assert manifest["pgs_geometry"]["finger_count"] >= 3


@pytest.mark.parametrize("width", [0.01, 10.])
def test_pgs_uses_actual_m1_width_rules(tmp_path, width):
    config = gp.CleanPortIndSymConfig.model_validate({
        **_ind_sym_config_dict(), "pgs": {**PGS, "strip_width_um": width},
    })
    with pytest.raises(PortError, match="PGS strip width"):
        gp.CleanPortIndSymGenerator().generate(config, outdir=tmp_path, gds_name="device.gds")
