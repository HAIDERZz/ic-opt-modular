"""M1.5: the NT=2 compact planner judges only each total's balanced lane pair -- the same answer as the exhaustive scan, fast."""

from __future__ import annotations

import itertools
import math
import time

import pytest

pytest.importorskip("klayout.db")

from ic_opt.em.pcell import _pcell_ind_sym as ind
from ic_opt.em.pcell._pcell_core import (
    _EPS,
    GRID_UM,
    PortError,
    ceiltogrid,
    floortogrid,
    max_opening,
    process_rule_context,
    roundtogrid,
)


def exhaustive_lane_offsets(*, OD, W, OPENING, LEAD, S, top_met, pad_length, process):
    """The pre-M1.5 search verbatim: every (total, difference) pair in order, first qualifying wins."""
    pitch = W + S
    inner_od = OD - 2.0 * pitch
    outer_max = max_opening(OD, W) + pad_length / 2.0
    inner_max = max_opening(inner_od, W) + pad_length / 2.0
    minimum_total, maximum_total = ceiltogrid(pitch + pad_length), floortogrid(outer_max + inner_max)
    lane_step = 50 * GRID_UM
    memo: dict = {}
    for total_step in range(math.floor((maximum_total - minimum_total + _EPS) / lane_step) + 1):
        total = roundtogrid(minimum_total + total_step * lane_step)
        minimum_difference, maximum_difference = max(0.0, total - 2.0 * inner_max), min(total, 2.0 * outer_max - total)
        if minimum_difference > maximum_difference + _EPS:
            continue
        for difference_step in range(math.ceil((minimum_difference - _EPS) / lane_step), math.floor((maximum_difference + _EPS) / lane_step) + 1):
            difference = difference_step * lane_step
            outer_g, inner_g = roundtogrid((total + difference) / 2.0), roundtogrid((total - difference) / 2.0)
            if outer_g > outer_max + _EPS or inner_g > inner_max + _EPS:
                continue
            candidate = ind._compact_two_turn_candidate(OD=OD, W=W, OPENING=OPENING, LEAD=LEAD, S=S, top_met=top_met, pad_length=pad_length,
                                                        outer_g=outer_g, inner_g=inner_g, port_order=["P1", "N1"], process=process,
                                                        render_via_cuts=False, memo=memo)
            if ind._compact_two_turn_candidate_is_qualified(candidate, top_met=top_met, process=process):
                return outer_g, inner_g
    return None


GRID = list(itertools.product([60.0, 100.0, 200.0], [3.0, 4.0, 6.0], [2.0, 2.5, 3.0]))


@pytest.mark.parametrize(("od", "w", "s"), GRID)
def test_balanced_pair_search_equals_the_exhaustive_scan(od, w, s):
    ctx = process_rule_context("demo_6m")
    try:
        minimum_pad = ind._compact_two_turn_via_length(w, 6, ctx, "test")
    except PortError:
        pytest.skip("no legal landing at this width")
    for pad in sorted({roundtogrid(minimum_pad), roundtogrid(w)}):
        kw = dict(OD=od, W=w, OPENING=8.0, LEAD=20.0, S=s, top_met=6, pad_length=pad, process=ctx)
        assert ind._compact_two_turn_lane_offsets(port_order=["P1", "N1"], **kw) == exhaustive_lane_offsets(**kw)


def test_tight_spacing_cases_plan_in_tens_of_milliseconds():
    ctx = process_rule_context("demo_6m")
    for od, w, s in ((120.0, 3.0, 2.0), (200.0, 4.0, 2.0)):
        start = time.perf_counter()
        ind.ind_sym(OD=od, W=w, OPENING=8.0, LEAD=20.0, S=s, NT=2, TOP_ME="6", BTM_ME="5", process=ctx)
        assert time.perf_counter() - start < 0.15                     # was 0.3-0.5 s: thousands of rendered candidates
