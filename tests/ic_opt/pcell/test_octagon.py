"""M1.1: the octagon's chamfer geometry has one source, and crossover diagonals are exactly 45 degrees on the grid (D8)."""

from __future__ import annotations

import math

import pytest

pytest.importorskip("klayout.db")

from ic_opt.em.pcell._pcell_core import (
    GRID_UM,
    PI,
    ceiltogrid,
    chamfer,
    floortogrid,
    junction_half_offset,
    max_opening,
    octagon,
    roundtogrid,
)
from ic_opt.em.pcell._pcell_primitives import base_ind_diag, base_oct_quad, base_xfm_cross


@pytest.mark.parametrize("W", [2.0, 3.0, 4.0, 5.0, 6.5, 7.6, 8.45, 10.0])
def test_diagonal_is_drawn_at_least_w_wide_and_never_more_than_a_grid_step_over(W):
    c = chamfer(W)
    assert W <= c.drawn_width < W + GRID_UM * 1.5 and c.C == ceiltogrid(W * math.tan(PI / 8) + 0.005)


@pytest.mark.parametrize(("OD", "W"), [(60.0, 4.0), (100.0, 5.0), (133.0, 7.6), (239.0, 10.0)])
def test_octagon_reproduces_the_quantized_reference_formulas(OD, W):
    ring = octagon(OD, W)
    A = roundtogrid(OD / (2 + math.sqrt(2)))
    BA = floortogrid((OD - 2 * A) / 2 - 0.005)
    assert (ring.A, ring.BA, ring.C) == (A, BA, chamfer(W).C)
    assert max_opening(OD, W) == BA - chamfer(W).C == ring.max_opening
    assert octagon(OD, W, bias=2).BA == BA - 2 * GRID_UM
    assert ring.inner_chamfer_intercept == OD / 2 + BA - ring.C - W
    inner = octagon(OD - 2 * (W + 2.0), W)
    assert ring.diagonal_gap(inner) == pytest.approx(((W + 2.0) + BA - inner.BA - ring.C - W) / math.sqrt(2))


def _edges_are_manhattan_or_45(cell) -> bool:
    for _layer, pts in cell.flat_shapes():
        for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
            dx, dy = x1 - x0, y1 - y0
            if dx and dy and abs(dx) != abs(dy):
                return False
    return True


@pytest.mark.parametrize("S", [2.0, 2.005, 2.015, 3.7, 3.705])
def test_crossover_diagonals_are_exact_45_degrees_even_for_half_grid_inputs(S):
    """D8: W + S/2 - C2 sits half a grid off for odd-grid S; snapping it keeps every vertex on integer nanometres."""
    assert junction_half_offset(5.0, S) == roundtogrid(5.0 + S / 2 - chamfer(5.0).C2)
    assert _edges_are_manhattan_or_45(base_ind_diag(W=5.0, S=S, MET=9))
    assert _edges_are_manhattan_or_45(base_xfm_cross(WI=S, WO=5.0, S=0.0, TOP_ME=9, BTM_ME=8))
    assert _edges_are_manhattan_or_45(base_oct_quad(OD=100.0, W=5.0, OP=8.0, MET=9))
