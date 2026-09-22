"""M1.2: whatever the generator accepts, the packaged audit accepts — including nominal widths and spacings sitting
exactly on a profile's rule thresholds, where the quantized chamfer decides which side of the rule the drawn geometry is on."""

from __future__ import annotations

import itertools

import pytest

pytest.importorskip("klayout.db")

from ic_opt.em.pcell import get_generator
from ic_opt.em.pcell._pcell_core import PortError
from ic_opt.em.pcell.drc_audit import audit_gds
from tests.ic_opt.pcell.test_golden import FIXTURE

# demo_6m: M5/M6 wide-parallel rule  W > 5.0 um and parallel length > 20 um  =>  space >= 2.0 um
THRESHOLD_W, THRESHOLD_S = 5.0, 2.0
GRID = [(od, nt, THRESHOLD_W + dw, THRESHOLD_S + ds)
        for od, nt, dw, ds in itertools.product([80.0, 100.0], [2, 3, 4], [-0.01, 0.0, 0.01, 0.5], [-0.1, -0.01, 0.0, 0.01, 0.5])]


@pytest.mark.parametrize(("od", "nt", "w", "s"), GRID)
def test_accepted_builds_pass_the_audit_at_the_wide_parallel_threshold(od, nt, w, s, tmp_path):
    g = get_generator("clean_port_ind_sym", plugin_module="builtin:clean_port")
    cfg = {"process_profile": "demo_6m", "port_order": ["P1", "N1"], "outer_diameter_um": od, "width_um": round(w, 3), "spacing_um": round(s, 3),
           "opening_um": 8.0, "lead_length_um": 20.0, "turns": nt, "metal": "6", "ground_fixture": FIXTURE}
    try:
        gds = g.generate(g.config_model.model_validate(cfg), outdir=tmp_path, gds_name="x.gds").gds_path
    except PortError:
        return                                                   # refusing is always allowed; drawing something the audit rejects is not
    findings = {(v.kind, v.layer) for v in audit_gds(gds, "demo_6m").violations} - {("max_width", "M1")}
    assert not findings, findings
