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
