"""M1.4 (D11): every family refuses a winding whose same-layer segments merged or closed into a loop."""

from __future__ import annotations

import pytest

pytest.importorskip("klayout.db")

from ic_opt.em.pcell._pcell_core import Cell, PortError, metal_layer
from ic_opt.em.pcell._pcell_guards import _check_winding_segments


def winding(*rects):
    cell = Cell("w", "test", {})
    for x0, y0, x1, y1 in rects:
        cell.add_rect(metal_layer(6), x0, y0, x1, y1)
    return cell


def test_segment_count_and_loops_are_enforced():
    two = winding((0, 0, 10, 2), (0, 5, 10, 7))
    _check_winding_segments(two, met=6, expected=2, where="t", process=None)
    with pytest.raises(PortError, match="expected 2 winding segments, found 1"):
        _check_winding_segments(winding((0, 0, 10, 2), (0, 1.5, 10, 7)), met=6, expected=2, where="t", process=None)    # merged
    ring = winding((0, 0, 10, 1), (0, 9, 10, 10), (0, 0, 1, 10), (9, 0, 10, 10))                                            # a closed loop
    with pytest.raises(PortError, match="closed metal loop"):
        _check_winding_segments(ring, met=6, expected=1, where="t", process=None)


def test_tw_and_il_run_the_guard_on_each_net(monkeypatch):
    from ic_opt.em.pcell import _pcell_xfm_il as il_mod
    from ic_opt.em.pcell import _pcell_xfm_tw as tw_mod
    from ic_opt.em.pcell._pcell_core import process_rule_context
    seen = []
    real = _check_winding_segments

    def spy(cell, **kw):
        seen.append((kw["where"], kw["expected"]))
        real(cell, **kw)

    monkeypatch.setattr(tw_mod, "_check_winding_segments", spy)
    monkeypatch.setattr(il_mod, "_check_winding_segments", spy)
    ctx = process_rule_context("demo_6m")
    tw_mod.xfm_tw(OD=260.0, W=6.0, S=6.0, NR=3, OPENING_P=10.0, OPENING_N=10.0, LEAD=20.0, SL_ME="6", process=ctx)
    il_mod.xfm_il(OD=200.0, W=5.0, S=2.5, NT_P=3, NT_S=3, OPENING_P=18.0, OPENING_S=18.0, LEAD_P=20.0, LEAD_S=20.0, SL_ME="6", process=ctx)
    assert seen == [("xfm_tw primary", 3), ("xfm_tw secondary", 3), ("xfm_il primary", 5), ("xfm_il secondary", 7)]


def test_crossover_pads_never_hang_off_their_ring(tmp_path):
    """M1.6 (D4): the pads are trimmed to their ring's flat, and a turn too small to host one is refused."""
    import klayout.db as kdb

    from ic_opt.em.pcell import get_generator
    from ic_opt.em.pcell._pcell_guards import landing_pads_nm
    from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter
    from tests.ic_opt.pcell.test_golden import FIXTURE

    g = get_generator("clean_port_ind_sym", plugin_module="builtin:clean_port")
    layer = get_geometry_rule_adapter("demo_6m").layer("M6").drawing

    def build(od, nt, w, s):
        cfg = {"process_profile": "demo_6m", "port_order": ["P1", "N1"], "outer_diameter_um": od, "width_um": w, "spacing_um": s,
               "opening_um": 8.0, "lead_length_um": 20.0, "turns": nt, "top_metal": "6", "bottom_metal": "5", "ground_fixture": FIXTURE}
        return g.generate(g.config_model.model_validate(cfg), outdir=tmp_path / f"{od}_{nt}_{w}_{s}", gds_name="x.gds").gds_path

    with pytest.raises(PortError, match="hang off the ring"):
        build(90.0, 4, 5.0, 4.0)                    # the review's NT=4 half-pad case: the innermost flat cannot host the pad
    gds = build(65.0, 3, 4.0, 4.0)                  # the corner case: the inner far pads are trimmed to the ring's flat
    layout = kdb.Layout()
    layout.read(str(gds))
    top = layout.top_cells()[0]
    ring = kdb.Region(top.begin_shapes_rec(layout.layer(*layer))).merged()
    pads = [p for p in top.begin_shapes_rec(layout.layer(*layer)) if False]  # pads are merged into the ring in the file; use the cell model instead
    from ic_opt.em.pcell import _pcell_ind_sym as ind
    from ic_opt.em.pcell._pcell_core import process_rule_context
    cell = ind.ind_sym(OD=65.0, W=4.0, OPENING=8.0, LEAD=20.0, S=4.0, NT=3, TOP_ME="6", BTM_ME="5", process=process_rule_context("demo_6m"))
    boxes = landing_pads_nm(cell, layer)
    assert boxes and any((y1 - y0) < 4000 for x0, y0, x1, y1 in boxes)      # at least one pad shorter than W: trimmed to the flat
    others = kdb.Region()
    pad_set = set(boxes)
    for shape_layer, pts in cell.flat_shapes():
        if shape_layer == layer and pts:
            xs, ys = zip(*pts)
            if not (len(pts) == 4 and (min(xs), min(ys), max(xs), max(ys)) in pad_set):
                others.insert(kdb.Polygon([kdb.Point(x, y) for x, y in pts]))
    others.merge()
    assert all((kdb.Region(kdb.Box(*b)) - others).is_empty() for b in boxes)
