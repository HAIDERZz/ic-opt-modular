# Auto-split from pcell_inductor_port_clean.py (wrap-up P1,
# 2026-07-28): verbatim segment move, no behavior change. The
# facade module re-exports every name; see its docstring for
# provenance and the KNOWN_DEVIATIONS record.
from __future__ import annotations

import klayout.db as kdb

from ic_opt.em.pcell._pcell_core import (
    _EPS,
    _SEAM_HEAL_MAX_PASSES,
    DBU_UM,
    SEAM_APEX_TOL_NM,
    Cell,  # string-annotation use
    PortError,
    ProcessRuleContext,
    Shape,
    _conductor_for_drawing,
    _edge_pair_apex_gap,
    _metal,
    _metal_index,
    _metal_name,
    _nm,
    _required_parallel_spacing,
    bridge_y_reach,
    cross_endpoint_offset,
    max_opening,
)


def _check_opening(OD: float, W: float, OPENING: float, where: str) -> None:
    """Fail closed when OPENING would detach the ring opening from the leads."""
    mx = max_opening(OD, W)
    if OPENING > mx + _EPS:
        raise PortError(
            f"{where}: OPENING={OPENING} exceeds the maximum {mx:.3f} for "
            f"OD={OD}, W={W} (above it the octagon opening leg detaches from "
            f"the lead pair, floating the P/N ports)"
        )


def _check_trace_rules(W: float, OPENING: float, LEAD: float, met: int,
                       process: ProcessRuleContext | None, where: str) -> None:
    """Fail closed when the trace or its lead-arm gap breaks the winding
    metal's own width/space rule (gdsfactory review 2026-09-21).

    ``_check_opening`` only bounds OPENING from above, and nothing compared W
    with the profile: a single turn has no via window to object, so a
    sub-minimum W or a 2*OPENING arm gap below min space drew silently, while
    multi-turn builds blamed the via array or the compact bridge instead.
    The two arms run parallel for at least the W-long opening leg plus LEAD,
    which is the length the wide-line spacing rules are evaluated against.
    Reference mode (no profile) has no rules to enforce."""
    if process is None:
        return
    name = _metal_name(met)
    min_space = _required_parallel_spacing(met, W, LEAD + W, process)
    min_width = process.adapter.metal_rule(name).min_width_um
    if min_width is not None and W < min_width - _EPS:
        raise PortError(
            f"{where}: W={W} is below the {name} min width {min_width} of "
            f"profile {process.profile_id}")
    if 2 * OPENING < min_space - _EPS:
        raise PortError(
            f"{where}: OPENING={OPENING} leaves a {2 * OPENING:g} um gap between "
            f"the two lead arms, below the {name} min space {min_space} of "
            f"profile {process.profile_id} for W={W}")


def _check_winding_fit(OD, W, S, NT, PITCH, TOP_ME, process, where):
    """Fail closed when the innermost facing-bearing turn of an NT>=3
    winding cannot host the crossunder-facing OPENING that
    ``_ind_ring_turns`` derives from ``cross_endpoint_offset``. Past this
    point ``base_oct_quad``'s ``OP > (BA - C)`` branch takes over and the
    ring opening leg detaches from where the crossunder's flush-alignment
    assumption expects it -- ticket 01 comment (2): a small OD gets crushed
    by the octagon chamfer. ``max_opening`` is exactly the ``BA - C`` bound
    that branch keys on (metal-independent, same function ``_check_opening``
    already uses for the outer ring), so this checks the same bound at the
    smallest OD that ever receives the ``facing`` OPENING.

    NT<=2 never draws a facing-bearing turn (the sole crossover, if any,
    uses the caller's own OPENING on the OUTER ring only -- see
    ``_ind_ring_turns``), so the check is a no-op there regardless of OD."""
    if NT <= 2:
        return
    cross_gap = PITCH - W
    facing = cross_endpoint_offset(W, cross_gap, _metal_index(TOP_ME), process)
    inner_i = NT - 1 if NT % 2 == 1 else NT - 2
    inner_od = OD - 2 * inner_i * PITCH
    mx = max_opening(inner_od, W)
    if facing > mx + _EPS:
        raise PortError(
            f"{where}: innermost facing-bearing turn OD={inner_od:.3f} "
            f"(NT={NT}, PITCH={PITCH}) cannot host the crossunder facing "
            f"opening {facing:.3f} um > max_opening {mx:.3f} um; the "
            f"octagon opening leg would detach from the crossunder "
            f"(increase OD, reduce NT, or reduce W/S)"
        )


def _check_bridge_escape_clearance(
    W, S, met, process, opening_other, bridge_shift, where, who, other_who
):
    """Validate the derived outer-bridge shift against the opposite lead.

    Ticket 02c moves each winding's bridges onto the arm OPPOSITE its own
    lead/escape opening -- but that arm is now the SAME half-plane the
    OTHER winding's lead/escape occupies (P bridges left, S lead+escape
    left; S bridges right, P lead right -- see xfm_il's docstring). The
    lead/escape channel's near edge sits at ``y = opening_other`` (the
    ``OPENING``/escape-``y0`` value itself -- ``_balun_crossunder`` anchors
    its near pad exactly there, and ``base_lead_pair`` anchors its own lead
    the same way); the bridge's own pads reach ``bridge_y_reach(W, S, ...)``
    from the centreline before ``bridge_shift`` moves that outer pad inward.
    Both are SL-1-plane metal (the escape/bridge pads); this checks their
    applicable base or wide-parallel spacing, not merely that they avoid
    outright touching.

    Reproduced empirically (ticket 02c): at the pre-fix defaults (OD=200,
    W=4, S=2, OPENING_P=OPENING_S=8) the bridge reach is 10.82 um, already
    PAST the OPENING=8 um channel edge -- a genuine, unconditional
    ``_xfm_net_short`` (not merely a spacing violation) that first exposed
    the need for this guard.  The current planner repairs a feasible gap
    first; this function remains the fail-closed postcondition."""
    reach = bridge_y_reach(W, S, met, process) - bridge_shift
    min_gap = _required_parallel_spacing(met, W, W, process)
    clearance = opening_other - reach
    if clearance < min_gap - _EPS:
        raise PortError(
            f"{where}: {who}'s same-side-stacked bridge reaches "
            f"|y|={reach:.3f} um from centreline, which leaves only "
            f"{clearance:.3f} um of clearance (need >= {min_gap:.3f} um) "
            f"before {other_who}'s lead/escape channel starting at "
            f"y={opening_other:.3f} um on the same half-plane; increase "
            f"{other_who}'s OPENING (raises the channel's own OPENING vs "
            f"max_opening headroom -- increase OD too if that bound is "
            f"tight) so the two clear"
        )


def _check_ind_winding_segments(
    cell: Cell,
    *,
    top_met: int,
    turns: int,
    expected_segments: int | None = None,
    process: ProcessRuleContext | None,
) -> None:
    """Require one disconnected body-metal segment per nominal turn.

    The lower-metal crossunders are what serially join these segments.  If
    body-metal crowding merges two segments first, the bridge is bypassed and
    the winding contains an unintended same-layer turn-to-turn short that a
    same-net spacing DRC cannot report.
    """
    drawing = _metal(top_met, process)
    body = kdb.Region()
    for layer, points in cell.flat_shapes():
        if layer == drawing and points:
            body.insert(kdb.Polygon([kdb.Point(x, y) for x, y in points]))
    found = body.merged().count()
    expected = turns if expected_segments is None else expected_segments
    if found != expected:
        raise PortError(
            f"ind_sym: {_metal_name(top_met)} body expected {expected} winding "
            f"segments, found {found}; adjacent turns overlap/self-short "
            "before the lower-metal bridges (increase OD or reduce NT/W/S)"
        )


def _heal_seam_notches(cell: Cell, process: ProcessRuleContext | None) -> None:
    """D2/D3 (M12 Phase 0.5): close same-net acute-wedge *seam notches*.

    The reconstructed multi-turn crossover construction (base_ind_diag's 45-degree edge
    meeting the ring/turn axis-aligned inner edge) leaves an acute empty
    wedge at the crossover-to-ring seam. At crowded inner-turn junctions of
    a small-OD multi-turn coil (D3) that wedge narrows below the rule
    ``min_space`` and flags on *every* metal (the thinner signal metals as
    well as AP
    at 2.0 um), not just AP. The former D2 single-turn case no longer reaches
    this crossover construction: NT=1 is now one direct ring.

    This heal is **process-mode only** (reference mode is never called, so the
    reconstructed geometry stays byte-identical) and operates on the coil
    winding -- which is a single galvanic net (a two-terminal inductor is one
    continuous conductor). It fills only edge pairs whose two edges share a
    common vertex (the wedge apex, nearest endpoints within
    ``SEAM_APEX_TOL_NM``); the intended parallel gaps -- the inter-turn spiral
    spacing and the D1 crossover-junction net distance -- have their nearest
    endpoints ~one ``min_space`` apart and are therefore never touched, so the
    heal *cannot* short adjacent turns. A fill is committed only when it
    strictly lowers the ``min_space`` violation count for that metal, so a
    genuinely infeasible multi-turn crowding corner whose wedge cannot be
    closed without breaching a neighbouring diagonal is left
    at its original count rather than trading one violation for another. On a
    clean coil no pair qualifies and nothing is added -> byte-identical to the
    pre-heal geometry (verified on the numeric-metal digest harness).

    Must run on an isolated winding cell BEFORE ``add_ground_fixture`` (the M1
    fixture is a different net and is not a conductor drawing layer here).
    Composite generators call it while each galvanic winding is still
    separate; a fill must never see or bridge two transformer nets."""
    if process is None:
        return
    by_layer: dict[tuple[int, int], list] = {}
    for layer, pts in cell.flat_shapes():
        by_layer.setdefault(layer, []).append(pts)
    for layer, polys in by_layer.items():
        name = _conductor_for_drawing(process, layer)
        if name is None:
            continue
        try:
            min_space = process.adapter.metal_rule(name).min_space_um
        except ValueError:
            continue
        if min_space is None:
            continue
        threshold = _nm(min_space)
        region = kdb.Region()
        for pts in polys:
            region.insert(kdb.Polygon([kdb.Point(x, y) for x, y in pts]))
        region.merge()
        for _ in range(_SEAM_HEAL_MAX_PASSES):
            pairs = region.space_check(threshold, False, kdb.Metrics.Euclidian)
            before = pairs.count()
            fills = [
                pair.polygon(0)
                for pair in pairs.each()
                if _edge_pair_apex_gap(pair.first, pair.second) <= SEAM_APEX_TOL_NM
            ]
            if not fills:
                break
            healed = kdb.Region()
            for f in fills:
                healed.insert(f)
            candidate = (region + healed).merged()
            after = candidate.space_check(
                threshold, False, kdb.Metrics.Euclidian).count()
            if after >= before:
                # closing these wedges would not net-reduce violations (the
                # corner is genuinely infeasible at this rule) -- leave it flagged
                break
            region = candidate
            for f in fills:
                cell.shapes.append(
                    Shape(layer, [(p.x, p.y) for p in f.each_point_hull()]))


def _xfm_net_regions(cell):
    regions = {}
    for layer, points in cell.flat_shapes():
        regions.setdefault(layer, kdb.Region()).insert(
            kdb.Polygon([kdb.Point(x, y) for x, y in points])
        )
    return regions


def _xfm_net_overlap(a, b):
    """Return the first common-layer contact, including zero-area touching."""
    ra, rb = _xfm_net_regions(a), _xfm_net_regions(b)
    for lay in sorted(set(ra) & set(rb)):
        if not ra[lay].interacting(rb[lay]).is_empty():
            ov = ra[lay] & rb[lay]
            return lay, ov.area() / 1e6
    return None


def _xfm_nets_are_drc_separate(a, b, process) -> bool:
    """Whether two nets neither overlap nor violate inter-net metal space."""
    if _xfm_net_overlap(a, b) is not None:
        return False
    if process is None:
        return True
    from ic_opt.em.pcell.drc_audit import (
        wide_parallel_spacing_violations,
    )

    ra, rb = _xfm_net_regions(a), _xfm_net_regions(b)
    for met in range(1, 12):
        try:
            layer = _metal(met, process)
        except PortError:
            # Not every process stack is index-contiguous: n65-class
            # profiles have M1..M9 + AP (index 11) with no M10 -- a metal
            # this profile does not define cannot have been drawn by
            # either net, so there is nothing to separate on it. Before
            # this guard skipped unknown indices, any candidate that
            # cleared the lower metals crashed here and the compact
            # re-planner could never succeed on such stacks (six-family
            # tight-spacing clearance, issue 05).
            continue
        if layer not in ra or layer not in rb:
            continue
        name = _metal_name(met)
        spacing = process.adapter.metal_rule(name).min_space_um
        if spacing is None:
            raise PortError(
                f"{process.profile_id}: {name} has no min_space rule for "
                "transformer inter-net bridge planning"
            )
        if ra[layer].separation_check(
            rb[layer], _nm(spacing), False, kdb.Metrics.Euclidian
        ).count():
            return False
        wide = wide_parallel_spacing_violations(
            ra[layer].merged(),
            metal_name=name,
            adapter=process.adapter,
            dbu=DBU_UM,
            other=rb[layer].merged(),
        )
        if wide.count:
            return False
    return True


def _xfm_net_short(device, a, b):
    """Layer-complete short gate between two winding nets (each a Cell
    holding one winding plus its optional center tap): any contact between
    the two complete nets on ANY drawn layer, including touching edges or
    corners, fails closed. Replaces the old
    box-only _bs_ct_short clearance (round-2 blind spot: it checked the
    W x W tap box against the other RING only, missing the tap LEAD and
    the other winding's leads entirely — M13 ticket 05). No pinned layer
    list — metal-generic by construction."""
    overlap = _xfm_net_overlap(a, b)
    if overlap is not None:
        lay, area_um2 = overlap
        raise PortError(
            f"{device}: primary/secondary nets short on layer {lay} "
            f"({area_um2:.2f} um^2); adjust OD/OPENING/"
            f"CENTER_SPACING, the CT metals, or ESCAPE_ME")
