"""M0.4: audit findings carry coordinates and can be drawn on the GDS they came from."""

from __future__ import annotations

import pytest

pytest.importorskip("klayout.db")

from ic_opt.em.pcell.drc_audit import audit_gds
from ic_opt.em.pcell.render import layer_names, render
from tests.ic_opt.pcell.test_golden import build


def test_findings_locate_the_violating_geometry_and_render_onto_the_drawing(tmp_path):
    gds = build("ind_sym_nt3", tmp_path)                        # the F2 golden: diagonals 5.006 wide, 1.994 apart
    report = audit_gds(gds, "demo_6m")
    wide = [v for v in report.violations if v.kind == "wide_parallel_spacing"]
    assert wide and len(wide[0].boxes_um) == wide[0].count
    left, bottom, right, top = wide[0].boxes_um[0]
    assert right - left > 1.0 and top - bottom > 1.0 and abs(right - left - (top - bottom)) < 0.02   # a 45-degree edge pair spans a square box
    ring = [v for v in report.violations if (v.kind, v.layer) == ("max_width", "M1")]
    assert ring and ring[0].boxes_um and ring[0].boxes_um[0][0] < -50                                 # the ground ring's eroded remainder
    names = layer_names("demo_6m")
    assert names["66/0"] == "M6" and any(v.startswith("V") or v.startswith("RV") for v in names.values())
    png = render(gds, tmp_path / "fig" / "audit.png", boxes=[b for v in wide for b in v.boxes_um], title="F2", names=names)
    assert png.exists() and png.stat().st_size > 10_000
    zoomed = render(gds, tmp_path / "zoom.png", zoom=(left - 2, bottom - 2, right + 2, top + 2), boxes=wide[0].boxes_um[:1])
    assert zoomed.stat().st_size > 5_000
