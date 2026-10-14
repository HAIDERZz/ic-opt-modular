# Auto-split from pcell_inductor_port_clean.py (wrap-up P1,
# 2026-07-28): verbatim segment move, no behavior change. The
# facade module re-exports every name; see its docstring for
# provenance and the KNOWN_DEVIATIONS record.
from __future__ import annotations

import math

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
    transform_point,
)
from ic_opt.em.pcell.stack import size as stack_size


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


def _check_winding_segments(
    cell: Cell, *, met: int, expected: int, where: str, process: ProcessRuleContext | None,
) -> None:
    """Require exactly ``expected`` disconnected segments, none of them a closed loop, on one net's winding metal.

    The lower-metal bridges are what serially join these segments. If
    body-metal crowding merges two of them first, a bridge is bypassed and the
    winding contains an unintended same-layer turn-to-turn short that a
    same-net spacing DRC cannot report; a segment with a hole is a shorted
    turn by itself. Every family runs this on each net (M1.4, D11): ind_sym
    with NT (2 NT - 1 when the second leg dips), xfm_il with 2 NT - 1 (P) and
    2 NT + 1 (S, the escape), xfm_tw with NR per net.
    """
    drawing = _metal(met, process)
    body = kdb.Region()
    for layer, points in cell.flat_shapes():
        if layer == drawing and points:
            body.insert(kdb.Polygon([kdb.Point(x, y) for x, y in points]))
    body.merge()
    found = body.count()
    if found != expected:
        raise PortError(
            f"{where}: {_metal_name(met)} body expected {expected} winding "
            f"segments, found {found}; adjacent turns overlap/self-short "
            "before the lower-metal bridges (increase OD or reduce NT/W/S)"
        )
    loops = sum(1 for polygon in body.each() if polygon.holes())
    if loops:
        raise PortError(
            f"{where}: {_metal_name(met)} body has {loops} closed metal loop(s); "
            "a turn shorts onto itself (increase OD or reduce NT/W/S)"
        )


def landing_pads_nm(cell: Cell, layer: tuple[int, int]) -> list[tuple[int, int, int, int]]:
    """Global rectangles of every crossover endpoint pad on ``layer``: the ``vias`` blocks under a ``base_xfm_cross``."""
    out: list[tuple[int, int, int, int]] = []

    def walk(c: Cell, xf, inside_cross: bool) -> None:
        for inst in c.insts:
            ox, oy, orient = inst.origin_nm[0], inst.origin_nm[1], inst.orient

            def child_xf(p, _ox=ox, _oy=oy, _orient=orient, _outer=xf):
                x, y = transform_point(p, _orient)
                return _outer((x + _ox, y + _oy))

            if inside_cross and inst.cell.function == "vias":
                for shape in inst.cell.shapes:
                    if shape.layer == layer:
                        xs, ys = zip(*(child_xf(pt) for pt in shape.points_nm))
                        out.append((min(xs), min(ys), max(xs), max(ys)))
            walk(inst.cell, child_xf, inside_cross or inst.cell.function == "base_xfm_cross")

    walk(cell, lambda p: p, False)
    return out


def _check_landing_pads(cell: Cell, *, met: int, where: str, process: ProcessRuleContext | None) -> None:
    """Every crossover endpoint pad must lie entirely on the winding metal around it (D4's landing predicate, M1.6).

    A pad that hangs past its ring's flat is metal over free space -- not a
    spacing violation, so DRC never sees it; the construction trims pads to the
    flat (``base_ind_hud_cross``) and this is the check that it did. Reference
    mode (no process) draws the SKILL reference verbatim, hangs included, and
    trims nothing -- so it is not checked either."""
    if process is None:
        return
    layer = _metal(met, process)
    pads = landing_pads_nm(cell, layer)
    if not pads:
        return
    pad_boxes = {p for p in pads}
    others = kdb.Region()
    for shape_layer, points in cell.flat_shapes():
        if shape_layer == layer and points:
            xs, ys = zip(*points)
            if len(points) == 4 and (min(xs), min(ys), max(xs), max(ys)) in pad_boxes:
                continue                                         # the pad itself (drawn by its vias block)
            others.insert(kdb.Polygon([kdb.Point(x, y) for x, y in points]))
    others.merge()
    others.size(1)                      # one database unit of slack: an off-lattice width leaves sub-grid slivers, not geometry
    for x0, y0, x1, y1 in pads:
        pad = kdb.Region(kdb.Box(x0, y0, x1, y1))
        outside = (pad - others).area()
        if outside:
            raise PortError(
                f"{where}: a crossover landing pad at ({x0 * DBU_UM:.3f}, {y0 * DBU_UM:.3f})..({x1 * DBU_UM:.3f}, {y1 * DBU_UM:.3f}) um "
                f"hangs {100.0 * outside / pad.area():.0f}% off the {_metal_name(met)} ring (increase OD or reduce W/NT/S)"
            )


def _check_ind_winding_segments(
    cell: Cell,
    *,
    top_met: int,
    turns: int,
    expected_segments: int | None = None,
    process: ProcessRuleContext | None,
) -> None:
    _check_winding_segments(cell, met=top_met, expected=turns if expected_segments is None else expected_segments,
                            where="ind_sym", process=process)


def _heal_seam_notches(cell: Cell, process: ProcessRuleContext | None) -> None:
    """D2/D3 (M12 Phase 0.5): close same-net acute-wedge *seam notches*.

    The reconstructed multi-turn crossover construction (base_ind_diag's 45-degree edge
    meeting the ring/turn axis-aligned inner edge) leaves an acute empty
    wedge at the crossover-to-ring seam. At crowded inner-turn junctions of
    a small-OD multi-turn coil (D3) that wedge narrows below the rule
    ``min_space`` and flags on *every* metal (the thinner signal metals as
    well as AP), not just AP. The former D2 single-turn case no longer reaches
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
                cell.add_shape(
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
    for met in range(1, stack_size() + 1):
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


def _drawn_on(cell: Cell, layer: tuple[int, int]) -> kdb.Region:
    """Everything ``cell`` draws on ``layer``, its instances flattened, merged."""
    region = kdb.Region()
    for drawn, points in cell.flat_shapes():
        if drawn == layer and points:
            region.insert(kdb.Polygon([kdb.Point(x, y) for x, y in points]))
    return region.merged()


def _run_side_by_side_um(a: kdb.Edge, b: kdb.Edge) -> float:
    """How far two edges run side by side: ``b`` projected onto ``a`` and
    clipped to it when the two are parallel, else 0 -- the parallel run
    length the DRC audit's wide-line rules measure (KLayout's projection)."""
    length = math.hypot(a.dx(), a.dy())
    if not length or a.dx() * b.dy() != a.dy() * b.dx():
        return 0.0
    ux, uy = a.dx() / length, a.dy() / length
    t0, t1 = sorted((p.x - a.p1.x) * ux + (p.y - a.p1.y) * uy for p in (b.p1, b.p2))
    return max(0.0, min(length, t1) - max(0.0, t0)) * DBU_UM


def _tap_stack_gap(tapped: Cell, other: Cell, ct_me, metals: dict[int, str], *,
                   family: str, winding: str, facing: str, ct_label: str,
                   W: float, W_label: str, W_other: float, W_other_label: str,
                   OPENING_other: float, opening_label: str,
                   process: ProcessRuleContext | None, leads: str = "leads",
                   same_metal_tap: bool = True) -> None:
    """N-93 (xfm_bs), N-121 (xfm_ms, xfm_balun): a via-stack tap must keep
    the process's spacing from the other winding on the metals where both
    draw.

    ``tapped`` holds the tapped winding (its tap metal ``ct_me``) and
    ``other`` the other winding, each as drawn in its own sub-cell; the
    tapped one draws nothing below its own metal but its tap, so its
    shapes on each of ``metals`` -- the stack's W x W pad, and the lead
    too on the tap metal -- are the tap's. Each metal is measured where
    ``other`` draws as well: every pair of facing edges closer than the
    widest spacing those widths can need, each against
    ``_required_parallel_spacing`` -- the metal's min space, raised by
    every wide-line rule of the profile whose width threshold both lines
    exceed (``min(W, W_other)``, as the gate reads the rule) and whose
    parallel-run threshold the pair's run exceeds (0 for edges that are
    not parallel). Strict, as the audit is: a gap equal to the rule
    passes. The largest shortfall raises ``PortError`` with the gap, the
    run, the rule value, the widths (``W_label`` / ``W_other_label``) and
    the other winding's opening (``opening_label``, where its ``leads``
    lie); ``metals`` maps each metal to what it is of the other winding's
    ("the secondary's metal"), in the order a tie is reported. The caller
    runs it after the short gate, so a contact is still a short.

    Reference mode (no profile) has no rules: skipped, as in
    ``_check_trace_rules``."""
    if process is None or ct_me is None:
        return
    ct_i = _metal_index(ct_me)
    width = min(W, W_other)
    worst = None                                         # (rule, gap, run, metal) of the largest shortfall
    for met in metals:
        layer = _metal(met, process)
        tap, beside = _drawn_on(tapped, layer), _drawn_on(other, layer)
        if tap.is_empty() or beside.is_empty():
            continue
        reach = _required_parallel_spacing(met, width, math.inf, process)
        for pair in tap.separation_check(beside, _nm(reach) + 1, False,
                                         kdb.Metrics.Euclidian).each():
            gap = pair.to_dtype(DBU_UM).distance()
            run = _run_side_by_side_um(pair.first, pair.second)
            need = _required_parallel_spacing(met, width, run, process)
            if gap < need - _EPS and (worst is None or need - gap > worst[0] - worst[1]):
                worst = (need, gap, run, met)
    if worst is None:
        return
    need, gap, run, met = worst
    name = _metal_name(met)
    drawn, shape = ((f"its {W:g} um wide lead", "lead") if ct_i == met
                    else (f"a {W:g} x {W:g} um pad", "pad"))
    along = f" over a {run:.3f} um parallel run" if run > 0 else " at a corner"
    own = (f"; a tap on the {winding}'s own metal draws nothing on {name}"
           if same_metal_tap else "")
    raise PortError(
        f"{family}: the {winding}'s via-stack tap ({ct_label}={_metal_name(ct_i)}) "
        f"draws {drawn} on {name}, {metals[met]}, {gap:.3f} um from "
        f"the {facing}'s own {name} metal{along}, below the {need:g} um "
        f"profile {process.profile_id} asks between {name} lines {W_label}={W:g} "
        f"and {W_other_label}={W_other:g} um wide{' over that run' if run > 0 else ''}; "
        f"the {facing}'s {leads} lie {opening_label}={OPENING_other:g} um off the "
        f"centre line (a wider {opening_label} or a narrower {W_label} moves the "
        f"{shape} away from them{own})")


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
