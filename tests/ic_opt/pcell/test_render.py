"""M0.4: audit findings carry coordinates and can be drawn on the GDS they came from."""

from __future__ import annotations

import pytest

klayout = pytest.importorskip("klayout.db")

from ic_opt.em.pcell.drc_audit import audit_gds
from ic_opt.em.pcell.render import layer_names, render
from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter


def violating_gds(path):
    """Two M6 traces 0.5 um apart (min_space) and one M1 slab far wider than max_width, at known coordinates."""
    adapter = get_geometry_rule_adapter("demo_6m")
    layout = klayout.Layout()
    layout.dbu = 0.001
    top = layout.create_cell("x")
    m6 = layout.layer(*adapter.layer("M6").drawing)
    m1 = layout.layer(*adapter.layer("M1").drawing)
    top.shapes(m6).insert(klayout.DBox(0.0, 0.0, 30.0, 4.0))
    top.shapes(m6).insert(klayout.DBox(0.0, 4.5, 30.0, 8.5))
    top.shapes(m1).insert(klayout.DBox(-100.0, -100.0, -40.0, -40.0))
    layout.write(str(path))
    return path


def test_findings_locate_the_violating_geometry_and_render_onto_the_drawing(tmp_path):
    gds = violating_gds(tmp_path / "x.gds")
    report = audit_gds(gds, "demo_6m")
    space = [v for v in report.violations if (v.kind, v.layer) == ("min_space", "M6")]
    assert space and len(space[0].boxes_um) == space[0].count == 1
    assert space[0].boxes_um[0] == pytest.approx((0.0, 4.0, 30.0, 4.5))                  # the gap between the two traces
    slab = [v for v in report.violations if (v.kind, v.layer) == ("max_width", "M1")]
    assert slab and slab[0].boxes_um[0][0] > -100 and slab[0].boxes_um[0][2] < -40         # the eroded remainder sits inside the slab
    names = layer_names("demo_6m")
    assert names[f"{get_geometry_rule_adapter('demo_6m').layer('M6').drawing[0]}/0"] == "M6"
    assert any(name.startswith(("V", "RV")) for name in names.values())
    png = render(gds, tmp_path / "fig" / "audit.png", boxes=[b for v in report.violations for b in v.boxes_um], title="synthetic", names=names)
    assert png.exists() and png.stat().st_size > 10_000
    left, bottom, right, top = space[0].boxes_um[0]
    zoomed = render(gds, tmp_path / "zoom.png", zoom=(left - 2, bottom - 2, right + 2, top + 2), boxes=space[0].boxes_um)
    assert zoomed.stat().st_size > 5_000
