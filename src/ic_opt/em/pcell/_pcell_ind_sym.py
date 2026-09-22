# Auto-split from pcell_inductor_port_clean.py (wrap-up P1,
# 2026-07-28): verbatim segment move, no behavior change. The
# facade module re-exports every name; see its docstring for
# provenance and the KNOWN_DEVIATIONS record.
from __future__ import annotations

import math

import klayout.db as kdb

from ic_opt.em.pcell._pcell_core import (
    _EPS,
    GRID_UM,
    Cell,
    PortError,
    ProcessRuleContext,
    _metal,
    _metal_below,
    _metal_index,
    _metal_name,
    _ms_layer_regions,
    _nm,
    _pin,
    add_wide_path,
    ceiltogrid,
    chamfer_staircase_delta,
    cross_endpoint_offset,
    finalize_emx_ports,
    floortogrid,
    max_opening,
    octagon,
    roundtogrid,
    vias,
)
from ic_opt.em.pcell._pcell_guards import (
    _check_ind_winding_segments,
    _check_landing_pads,
    _check_opening,
    _check_trace_rules,
    _check_winding_fit,
    _heal_seam_notches,
)
from ic_opt.em.pcell._pcell_primitives import (
    base_ind_hud_cross,
    base_lead,
    base_lead_pair,
    base_oct,
)
from ic_opt.em.pcell._pcell_straight_extension import (
    extend_straight_x,
)
from ic_opt.em.pcell.fixture import (
    GroundFixtureConfig,
    add_ground_fixture,
)

# ---------------------------------------------------------------------------
# ind_sym (gdsgen_ref/pcell/inductor/ind_sym.il)
# ---------------------------------------------------------------------------


def _ind_ct_adjacency_guard(
    device: str, TOP_ME: str, CT_ME: str,
    process: ProcessRuleContext | None = None,
) -> None:
    """Fail closed on CT metals that short the tap net at y=0 (bug review
    2026-07-17 N1). The winding crossunder of this construction chain is
    drawn on the actual adjacent lower conductor (base_ind_hud_cross ignores
    its BTM_ME — a documented dead parameter). It crosses y=0 where the CT
    lead runs, so the tap must lie below that crossunder plane. Process mode
    follows the profile stack; reference mode keeps numeric layer order."""
    top = _metal_index(TOP_ME)
    ct = _metal_index(CT_ME)
    crossunder = _metal_below(top, process)
    if ct >= crossunder:
        raise PortError(
            f"{device}: CT metal {_metal_name(ct)} must "
            f"sit at least two levels below the coil top metal "
            f"{_metal_name(top)}: the winding crossunder "
            f"occupies {_metal_name(crossunder)} and crosses "
            "y=0 where the CT lead runs, so a CT on that layer galvanically "
            "shorts the tap net to the mid-winding bridges (bug review "
            "2026-07-17 N1)"
        )


def _ind_ct_tap(cell, OD, W, LEAD, S, NT, TOP_ME, CT_ME, port_name,
                dummy, DUMMYL, process, PITCH, port_logical_name="CT"):
    """Center-tap construction of ind_sym(CT_ME=...) (M13 unified; the
    legacy ind_sym_ct wrapper was removed in ticket 04): a W x W vias
    stack at the winding symmetry column
    (the innermost turn's closed column) + a CT lead leaving on CT_ME
    beneath the turns + one EMX port at the far lead tip (even NT: lead /
    right side; odd NT: crossover / left side). Geometry follows ind_ref.gds
    (see KNOWN_DEVIATIONS).

    ``PITCH`` (xfm_il ticket 01) is the already-resolved turn-to-turn radial
    pitch computed once by ``ind_sym`` (``W + S`` by default) -- the tap
    column and CT lead length are derived from the same single pitch value
    the winding itself was built with, never a second ``W + S`` here.

    ``port_logical_name`` (port contract 2026-09-21, default ``"CT"`` ->
    byte-identical): ``ind_sym``'s own ``semantic_port_roles`` threads its
    caller's requested logical name here -- see that parameter's
    docstring."""
    P = PITCH
    lead_len = LEAD + (NT - 1) * P + W
    if NT % 2 == 0:
        tap_x = OD / 2 - (NT - 1) * P - W
        lead_x = tap_x
    else:
        tap_x = -OD / 2 + (NT - 1) * P
        lead_x = -OD / 2 - LEAD
    # CT port at the far-from-tap lead tip on the CT_ME pin layer: even NT
    # exits on the lead (right) side -- base_lead's own default port_x_um
    # (L, the far end) -- odd NT on the crossover (left) side, where the
    # tap sits at the lead's NEAR end instead (port_x_um=0.0): lead_x
    # itself IS the tip there (algebraic identity, -OD/2-LEAD either way
    # you compute it -- not a second lookup of the same value; port
    # contract 2026-09-21).
    ct_met = _metal_index(CT_ME)
    cell.inst(
        base_lead(
            L=lead_len,
            W=W,
            WD=1.0,
            PINTXT="CT",
            PINP="dummy1",
            TOP_ME=CT_ME,
            BTM_ME=CT_ME,
            DUMMYL=DUMMYL,
            dummy=dummy,
            process=process,
            port_name=port_name,
            port_logical_name=port_logical_name,
            port_metal=ct_met,
            port_label_layer=_pin(ct_met, process),
            port_x_um=(0.0 if NT % 2 == 1 else None),
        ),
        (lead_x, -W / 2),
        "R0",
    )
    cell.inst(
        vias(
            Length=W,
            Width=W,
            TOP_ME=_metal_index(TOP_ME),
            BTM_ME=_metal_index(CT_ME),
            process=process,
        ),
        (tap_x, -W / 2),
        "R0",
    )


def _ind_ring_turns(cell, OD, W, OPENING, S, NT, TOP_ME, BTM_ME, DUMMYL,
                    process, PITCH, LEG2_BTM_ME=None,
                    bridge_side="alternate", chamfer_biases=None):
    """Direct NT=1 ring or stacked hud-cross turns + innermost closure:
    ind_sym's winding kernel (radial turn bookkeeping + crossunder bridges), factored out of ind_sym
    so a caller can build one bare ring (no lead pair, no EMX ports) on the
    same lattice. The NT>=2 path remains extracted verbatim (same
    instructions, same order) from ind_sym when ``LEG2_BTM_ME`` is left at
    its default ``None``. NT=1 intentionally takes the direct-ring deviation
    recorded in ``KNOWN_DEVIATIONS``.

    xfm_il (ticket 02) needs exactly this: its S winding cannot reuse
    ind_sym wholesale for NT_S>=2 (xfm_balun's nested-mode guard already
    established "ind_sym leads cannot be rerouted" -- ind_sym always draws
    its own straight lead pair, which would same-layer-cross the
    interleaved P winding's outer band), so it builds the ring here and
    attaches its own crossunder-escaped leads instead. ``LEG2_BTM_ME``
    (threaded to every ``base_ind_hud_cross`` call here -- see that
    function's docstring) is what xfm_il also needs on the P side: with the
    interleaved lattice's doubled PITCH, the reference crossunder's
    same-layer second leg would occupy the *other* winding's own band, and
    (ticket 02d) even routing it to the SAME SL-1 layer as leg1 lets one
    winding's leg2 collide with the OTHER winding's leg1 there -- xfm_il
    passes ``LEG2_BTM_ME=SL-2`` so every turn's leg2 lands on a third,
    distinct layer.

    ``bridge_side`` (xfm_il ticket 02b, default ``"alternate"`` ->
    byte-identical): the reference winding alternates each middle turn's own
    orientation (``R0``/``MY`` by parity) so consecutive turns' bridges
    zigzag left-right-left-right -- every turn RECEIVES the previous turn's
    bridge on one arm and EMITS its own to the next turn from the OTHER arm
    (``base_ind_hud_cross`` itself always emits from its local ``-OD/2``/
    ``LOP`` arm; the zigzag comes entirely from mirroring alternate turns
    ``MY`` here). ``"left"`` (opposite-exit xfm_il's same-side bridge
    stacking; ticket 02c retired the mirrored ``"right"`` variant -- see
    ``base_ind_hud_cross``'s docstring for why) drops the per-turn
    orientation flip entirely (every turn instanced ``"R0"``): every turn's
    OWN bridge then lands on the SAME physical arm (``LOP``, local
    ``-OD/2``) instead of zigzagging. Critically, this is NOT simply
    "thread a fixed side into every hud_cross call" -- the caller-supplied
    ``OPENING`` on a MIDDLE turn must additionally be forced to ``0.0``
    (never the reference's ``facing``): under the zigzag, a middle turn's
    ``ROP`` (``facing``-sized) exists to RECEIVE the previous turn's bridge,
    which lands there only because that previous turn was mirrored. In
    fixed ``"left"`` mode nothing is ever mirrored, so a turn's own bridge
    (on its OWN ``LOP``) already reaches the next turn directly (via SL-1,
    landing flush on solid ring metal -- see ``cross_endpoint_offset``); an
    un-closed ``ROP`` on that same turn would then be a second, unrelated
    gap that nothing ever plugs, splitting that turn's SL-only ring into an
    disconnected top arc and bottom arc (confirmed empirically -- ticket
    02c's series-topology test is exactly what catches this). Closing it
    (``ROP=0.0``) keeps every middle turn a single connected polygon with
    its one bridge-hosting gap, so only the OUTERMOST turn (2 gaps: its own
    bridge arm plus the caller's real lead/escape ``OPENING`` on the
    opposite arm -- necessarily 2 SEPARATE polygons, since the two winding
    terminals must never land on the same closed loop) and an even NT's
    dangling-``LOP`` innermost turn keep a second opening.

    Returns ``(pitch, facing)`` -- the resolved turn-to-turn pitch and the
    crossunder-facing OPENING that used to be applied to every non-outermost
    turn under the reference zigzag (still returned for callers that need
    the raw value -- e.g. ``_check_winding_fit`` -- even though fixed
    ``"left"`` mode itself now closes the middle turns' far arm instead of
    opening it to ``facing``)."""
    if bridge_side not in ("alternate", "left"):
        raise PortError(
            f"_ind_ring_turns: bridge_side {bridge_side!r} must be "
            f"'alternate' or 'left' (ticket 02c retired 'right' -- see "
            f"base_ind_hud_cross's docstring)"
        )
    pitch = W + S if PITCH is None else PITCH
    cross_gap = pitch - W
    # .il original: inner-turn OPENING = 2*W and odd-innermost ROP = 2*W.
    # Replaced by the exact cross endpoint edge so the neighbouring
    # crossover's via blocks land flush inside these arms (alignment
    # correction referenced on ind_ref.gds, see cross_endpoint_offset).
    facing = cross_endpoint_offset(W, cross_gap, _metal_index(TOP_ME), process)
    if chamfer_biases is None:
        # Self-contained winding (ind_sym / xfm_ms's multi coil): the
        # adjacent physical rings are exactly this loop's own turns.
        # Interleaved callers (xfm_il) pass explicit biases computed over
        # the MERGED physical radial order instead.
        delta = chamfer_staircase_delta(
            [OD - 2 * k * pitch for k in range(NT)], W,
            _metal_index(TOP_ME), process)
        chamfer_biases = [k * delta for k in range(NT)]
    if NT == 1:
        # A single turn has no adjacent turn to reach, hence no crossunder,
        # landing pad, or via stack.  Its only opening is the right-side
        # P1/N1 lead gap; the left arm stays closed so the two leads are the
        # endpoints of one continuous C-shaped winding.
        cell.inst(
            base_oct(
                OD=OD,
                W=W,
                LOP=0.0,
                ROP=OPENING,
                MET=_metal_index(TOP_ME),
                process=process,
                chamfer_bias=chamfer_biases[0],
            ),
            (0.0, 0.0),
            "R0",
        )
        return pitch, facing
    if NT > 2:
        # alternate: facing-sized ROP receives the previous (mirrored)
        # turn's bridge. left (ticket 02c): nothing is ever mirrored, so
        # that far arm hosts nothing and must stay closed -- see docstring.
        middle_opening = facing if bridge_side == "alternate" else 0.0
        for i in range(1, NT - 1):
            if bridge_side == "alternate":
                orient = "R0" if i % 2 == 0 else "MY"
            else:
                orient = "R0"
            cell.inst(
                base_ind_hud_cross(
                    OD=OD - 2 * i * pitch,
                    W=W,
                    S=S,
                    OPENING=middle_opening,
                    TOP_ME=TOP_ME,
                    BTM_ME=BTM_ME,
                    DUMMYL=DUMMYL,
                    process=process,
                    PITCH=pitch,
                    LEG2_BTM_ME=LEG2_BTM_ME,
                    chamfer_bias=chamfer_biases[i],
                    # issue 04: a middle turn's near pads sit on its own
                    # arm right where the NEXT-OUTER ring's inner chamfer
                    # passes -- bound them against that ring too.
                    outer_corridor=(OD - 2 * (i - 1) * pitch,
                                    chamfer_biases[i - 1]),
                    landing=(OD - 2 * (i + 1) * pitch, chamfer_biases[i + 1]),
                ),
                (0.0, 0.0),
                orient,
            )
    cell.inst(
        base_ind_hud_cross(
            OD=OD,
            W=W,
            S=S,
            OPENING=OPENING,
            TOP_ME=TOP_ME,
            BTM_ME=BTM_ME,
            DUMMYL=DUMMYL,
            process=process,
            PITCH=pitch,
            LEG2_BTM_ME=LEG2_BTM_ME,
            chamfer_bias=chamfer_biases[0],
            landing=(OD - 2 * pitch, chamfer_biases[1]),
        ),
        (0.0, 0.0),
        "R0",
    )
    if NT % 2 == 0:
        cell.inst(
            base_ind_hud_cross(
                OD=OD - 2 * (NT - 1) * pitch,
                W=W,
                S=S,
                OPENING=0.0,
                TOP_ME=TOP_ME,
                BTM_ME=BTM_ME,
                DUMMYL=DUMMYL,
                under=False,
                process=process,
                PITCH=pitch,
                LEG2_BTM_ME=LEG2_BTM_ME,
                chamfer_bias=chamfer_biases[NT - 1],
            ),
            (0.0, 0.0),
            "R0",
        )
    else:
        # alternate: unconditional facing-sized ROP opening nothing ever
        # lands on (reference-faithful quirk, see base_ind_hud_cross's
        # under=False sibling below). left: the innermost turn is simply
        # closed (both arms 0) -- its bridge-facing LOP is never opened
        # since it has no bridge of its own (NT-1 is the last turn).
        lop, rop = (0.0, facing) if bridge_side == "alternate" else (0.0, 0.0)
        cell.inst(
            base_oct(
                OD=OD - 2 * (NT - 1) * pitch,
                W=W,
                LOP=lop,
                ROP=rop,
                MET=_metal_index(TOP_ME),
                process=process,
                chamfer_bias=chamfer_biases[NT - 1],
            ),
            (0.0, 0.0),
            "R0",
        )
    return pitch, facing


def _needs_full_width_lead_landing(
    cell: Cell,
    *,
    center_x: float,
    OD: float,
    W: float,
    OPENING: float,
    top_met: int,
    side: str,
    process: ProcessRuleContext | None,
) -> bool:
    """Whether either open arm lacks its full W x W lead landing.

    The decision is made from the actual snapped winding polygons, not an
    OD/W threshold.  Rule-clean conventional rings therefore retain their
    byte-identical boundary lead, while compact octagons whose chamfer cuts
    into the landing receive one trace-width of inward overlap.
    """
    if process is None:
        return False
    drawing = _metal(top_met, process)
    body = kdb.Region()
    for layer, points in cell.flat_shapes():
        if layer == drawing and points:
            body.insert(kdb.Polygon([kdb.Point(x, y) for x, y in points]))
    body.merge()
    edge_x = center_x + (OD / 2.0 if side == "right" else -OD / 2.0)
    if side == "right":
        x0, x1 = edge_x - W, edge_x
    elif side == "left":
        x0, x1 = edge_x, edge_x + W
    else:
        raise PortError(f"lead landing side must be left/right, got {side!r}")
    for y0 in (OPENING, -OPENING - W):
        landing = kdb.Region(kdb.Box(
            _nm(x0), _nm(y0), _nm(x1), _nm(y0 + W)
        ))
        if not (landing - body).is_empty():
            return True
    return False


def ind_sym(
    OD: float = 50.0,
    W: float = 2.0,
    OPENING: float = 5.0,
    LEAD: float = 10.0,
    S: float = 2.0,
    NT: int = 2,
    TOP_ME: str = "9",
    BTM_ME: str = "8",
    CT_ME: str | None = None,
    strName: str = "ind_sym",
    NT_N: bool = False,
    dummy: bool = True,
    DUMMYL: str = "RFVLSI",
    port_order: list[str] | None = None,
    ground_fixture: GroundFixtureConfig | None = None,
    process: ProcessRuleContext | None = None,
    PITCH: float | None = None,
    LEG2_BTM_ME: str | None = None,
    bridge_side: str = "alternate",
    STRAIGHT_EXTENSION: float = 0.0,
    semantic_port_roles: bool = True,
    CT_LEAD: float | None = None,
    PORT_SPACING: float | None = None,
) -> Cell:
    """Symmetric inductor: stacked hud crosses + innermost turn + lead pair.

    CT_LEAD is the tap lead's length beyond the ring (default LEAD, the
    historical geometry). A composing transformer passes the length that
    carries the tap to the device's outer edge on its exit side, so the
    ground fixture's stub stays the designed length instead of a strip
    running under the other winding (CT interior-port fix, 2026-09-22).

    STRAIGHT_EXTENSION adds total X width in non-negative 0.01 um steps.
    Side routing, vias and optional CT translate rigidly; central horizontal
    winding straights grow. Zero preserves the historical cell unchanged.

    CT_ME (M13 ticket 02) optionally adds the center tap: a vias stack at
    the winding symmetry column + a CT lead on CT_ME + a third EMX port
    (default semantic port order [P1, N1, CT]; without CT_ME it is
    [P1, N1]). The public contract keeps CT_ME at least two levels below
    TOP_ME. For NT>=2 this is required because the crossunder occupies
    TOP_ME-1 and crosses the tap path at y=0 (BTM_ME does not affect it; see
    base_ind_hud_cross). NT=1 has no crossunder but retains the same
    conservative tap-layer contract; changing that is outside this fix.

    ``PITCH`` (xfm_il ticket 01) is the radial distance between adjacent
    turns of this one winding -- ``None`` (the default) keeps the reference
    ``W + S`` pitch (byte-identical geometry). Every radial turn placement
    (the stacked hud crosses' ``OD`` offsets, the innermost turn, the CT tap
    column) and the crossunder bridge each hud cross draws (whose far
    endpoint vias must land flush on the *next* turn's ring) are derived
    from this single resolved pitch -- there is no second ``W + S``
    hardcoded anywhere downstream. Passing ``PITCH=2*(W+S)`` (double the
    reference step) is what lets a future two-winding construction
    interleave this same winding kernel with a second one offset by
    ``W + S``, each winding's own turns still spaced ``2*(W+S)`` apart with
    the other winding's ring sitting in the gap between them.

    The RFVLSI/base_oct_fill/base_em_gr dummy instances, labels and
    rfvlsiEMVport calls of the reference are not ported (documented
    deviation). ind_sym.il hardcodes TOP_ME="9"/BTM_ME="8"; this port keeps
    the 9/8 defaults but accepts TOP_ME/BTM_ME as a user-directed metal
    generalization (see KNOWN_DEVIATIONS). The reference lead-pair call
    passes BTM_ME/P2TXT parameters that base_lead_pair does not declare
    (dropped here as Virtuoso drops unknown CDF params).

    ``LEG2_BTM_ME`` (xfm_il ticket 02/02d, default ``None`` -> byte-identical)
    threads straight to ``_ind_ring_turns``/``base_ind_hud_cross`` (see their
    docstrings): xfm_il's P winding reuses ind_sym wholesale but needs its
    crossunder's second leg on a THIRD, distinct metal (SL-2) so it neither
    occupies the interleaved S winding's own band (SL) nor collides with
    S's own leg1/leg2 footprint on SL-1 (ticket 02d's dual-layer-leg fix).

    ``bridge_side`` (xfm_il ticket 02b/02c, default ``"alternate"`` ->
    byte-identical) threads straight to ``_ind_ring_turns`` (see its
    docstring): kept for callers that still need every bridge stacked on
    ONE arm (``"left"``) instead of the reference zigzag; xfm_il's v4
    topology (ticket 02d) no longer uses it -- both P and S now reuse the
    reference ``"alternate"`` zigzag directly (S via a whole-cell MY mirror
    of the same construction), the same well-tested topology every
    standalone multi-turn ``ind_sym`` already relies on.

    ``semantic_port_roles`` (port contract 2026-09-21, default ``True`` ->
    byte-identical): whether this winding's own P1/N1/CT logical roles
    stay fixed regardless of ``port_order``'s display names (this
    function's own public contract for a direct/standalone caller --
    ``port_order`` renames "what EMX sees", never "which physical
    terminal a downstream reader means by logical_name"). xfm_ms/
    xfm_balun's own multi-turn sub-windings instead pass ``False``: they
    are relabelling this whole winding under THEIR OWN scheme (P2/N2 or
    whatever their caller's ``port_order`` says), so each port's
    logical_name falls back to its own registered name instead -- the
    structural replacement for the pre-contract transplant loops'
    ``"logical_name": q["name"]`` line (see xfm_ms/_ci_winding)."""
    if CT_ME is not None:
        _ind_ct_adjacency_guard("ind_sym", TOP_ME, CT_ME, process=process)
    _check_opening(OD, W, OPENING, "ind_sym")
    _check_trace_rules(W, OPENING, LEAD, _metal_index(TOP_ME), process, "ind_sym")
    # Same inner-ring bound xfm_il already enforces on its windings: past it
    # the crossunder pads land half outside the ring (17 geom5 library
    # points, all at ~50% hanging pad area; gdsfactory review 2026-09-21).
    _check_winding_fit(OD, W, S, NT, W + S if PITCH is None else PITCH,
                       TOP_ME, process, "ind_sym")
    # Explicit PITCH/bridge_side overrides exist only on the generic path; the
    # compact planner would drop them silently (gdsfactory review 2026-09-21).
    if (process is not None and NT == 2 and CT_ME is None
            and LEG2_BTM_ME is None and PITCH is None
            and bridge_side == "alternate"):
        compact = _compact_two_turn_winding(
            OD=OD,
            W=W,
            OPENING=OPENING,
            LEAD=LEAD,
            S=S,
            top_met=_metal_index(TOP_ME),
            port_order=port_order or ["P1", "N1"],
            process=process,
            family="ind_sym",
            semantic_port_roles=semantic_port_roles,
            port_spacing=PORT_SPACING,
        )
        compact = extend_straight_x(compact, STRAIGHT_EXTENSION, process=process)
        compact.emx_ports = finalize_emx_ports(compact)
        if ground_fixture is not None:
            add_ground_fixture(compact, ground_fixture, process)
        return compact
    params = {
        "OD": OD,
        "W": W,
        "OPENING": OPENING,
        "LEAD": LEAD,
        "S": S,
        "NT": NT,
        "TOP_ME": TOP_ME,
        "BTM_ME": BTM_ME,
        "strName": strName,
        "NT_N": NT_N,
    }
    if CT_ME is not None:
        params["CT_ME"] = CT_ME
    if process is not None:
        params["process"] = process.profile_id
    if PITCH is not None:
        params["PITCH"] = PITCH
    if LEG2_BTM_ME is not None:
        params["LEG2_BTM_ME"] = LEG2_BTM_ME
    if bridge_side != "alternate":
        params["bridge_side"] = bridge_side
    cell_name = f"ind_sym_OD{OD}_W{W}_O{OPENING}_L{LEAD}_S{S}_NT{NT}"
    if CT_ME is not None:
        cell_name += f"_CT{CT_ME}"
    cell = Cell(cell_name, "ind_sym", params)
    pitch, _facing = _ind_ring_turns(cell, OD, W, OPENING, S, NT, TOP_ME,
                                     BTM_ME, DUMMYL, process, PITCH,
                                     LEG2_BTM_ME, bridge_side)
    overlap_lead = _needs_full_width_lead_landing(
        cell,
        center_x=0.0,
        OD=OD,
        W=W,
        OPENING=OPENING,
        top_met=_metal_index(TOP_ME),
        side="right",
        process=process,
    )
    top_met = _metal_index(TOP_ME)
    top_pin = _pin(top_met, process)
    if CT_ME is None:
        order = port_order or ["P1", "N1"]
    else:
        order = port_order or ["P1", "N1", "CT"]
        if len(order) < 3:
            raise PortError(
                f"ind_sym: CT_ME={CT_ME!r} adds a tap port, so port_order "
                f"needs a third entry; got {order!r}"
            )
    # P1/N1 register on base_lead_pair's own two base_lead legs (port
    # contract 2026-09-21): each leg's far tip is OD/2+LEAD regardless of
    # overlap_lead (the origin shifts back by exactly W when LEAD grows by
    # W, an algebraic wash -- see base_lead_pair's docstring), so the
    # x_um=OD/2+LEAD expression is no longer recomputed and registered
    # separately here. port_order can rename P1/N1 (M13); the physical
    # ROLE stays fixed via port_p1_logical_name/port_n1_logical_name,
    # matching what this function has always promised regardless of the
    # display name.
    cell.inst(
        base_lead_pair(
            LEAD=LEAD + W if overlap_lead else LEAD,
            W=W,
            OPENING=OPENING,
            P1TXT=order[0],
            N1TXT=order[1],
            TOP_ME=TOP_ME,
            LEAD_ME=TOP_ME,
            dummy=dummy,
            process=process,
            port_metal=top_met,
            port_label_layer=top_pin,
            port_p1_logical_name=("P1" if semantic_port_roles else None),
            port_n1_logical_name=("N1" if semantic_port_roles else None),
            PORT_SPACING=PORT_SPACING,
        ),
        (OD / 2 - W if overlap_lead else OD / 2, 0.0),
        "R0",
    )
    # D2/D3 (M12 Phase 0.5): close crossover-to-ring acute-wedge seam notches
    # (process mode only; a no-op on a clean coil so numeric-metal geometry is
    # byte-identical). Runs before the ground fixture so it only sees the
    # single-net winding metal, never the M1 fixture. The CT tap is added
    # after the heal for parity with the legacy ind_sym_ct build (whose
    # inner winding healed before the outer tap existed).
    _heal_seam_notches(cell, process)
    _check_landing_pads(cell, met=top_met, where="ind_sym", process=process)
    _check_ind_winding_segments(
        cell,
        top_met=top_met,
        turns=NT,
        expected_segments=(2 * NT - 1 if LEG2_BTM_ME is not None else NT),
        process=process,
    )
    if CT_ME is not None:
        _ind_ct_tap(cell, OD=OD, W=W, LEAD=LEAD if CT_LEAD is None else CT_LEAD,
                    S=S, NT=NT, TOP_ME=TOP_ME,
                    CT_ME=CT_ME, port_name=order[2], dummy=dummy,
                    DUMMYL=DUMMYL, process=process, PITCH=pitch,
                    port_logical_name=("CT" if semantic_port_roles else order[2]))
    cell = extend_straight_x(cell, STRAIGHT_EXTENSION, process=process)
    # (port contract 2026-09-21) the one composition point: walks the
    # cell.inst() chain down to every base_lead_pair/base_lead leg
    # (P1/N1, CT) and derives emx_ports from it -- no family-level
    # add_emx_port call remains in this function.
    cell.emx_ports = finalize_emx_ports(cell)
    if ground_fixture is not None:
        add_ground_fixture(cell, ground_fixture, process)
    return cell


def _compact_two_turn_via_length(
    W: float,
    top_met: int,
    process: ProcessRuleContext,
    family: str,
) -> float:
    """Smallest rule-derived tangential landing for both bridge stacks.

    The winding width remains ``W`` in the radial direction.  Only the
    landing's tangential length is reduced, because a square ``W x W`` pad
    is what consumes the entire short inner octagon flat at small OD.  The
    search starts at the largest metal ``min_width`` in the two-level
    stack and accepts the first mask-grid length for which the real
    ``vias`` primitive can place every required cut with enclosure and
    array-count rules intact.
    """
    metal_widths = []
    bottom_met = _metal_below(top_met, process)
    for met in (bottom_met, top_met):
        width = process.adapter.metal_rule(_metal_name(met)).min_width_um
        if width is None:
            raise PortError(
                f"{process.profile_id}: {_metal_name(met)} has no min_width "
                f"rule for {family} compact bridge planning"
            )
        metal_widths.append(width)
    start = ceiltogrid(max(metal_widths))
    first_step = math.ceil((start - _EPS) / GRID_UM)
    last_step = math.floor((W + _EPS) / GRID_UM)
    for step in range(first_step, last_step + 1):
        length = step * GRID_UM
        try:
            vias(
                Length=length,
                Width=W,
                TOP_ME=top_met,
                BTM_ME=bottom_met,
                process=process,
            )
        except PortError:
            continue
        return roundtogrid(length)
    raise PortError(
        f"{family}: W={W:.3f} um cannot host the two compact bridge via "
        f"stacks on {_metal_name(top_met)}..{_metal_name(bottom_met)}"
    )


def _memoized(memo: dict | None):
    """``build(fn, **kw)``: ``fn(**kw)``, cached in ``memo`` by the call (the process by its profile id)."""
    if memo is None:
        return lambda fn, **kw: fn(**kw)

    def build(fn, **kw):
        key = (fn.__name__, tuple(sorted((k, v.profile_id if k == "process" else v) for k, v in kw.items())))
        cell = memo.get(key)
        if cell is None:
            cell = memo[key] = fn(**kw)
        return cell

    return build


def _compact_two_turn_candidate(
    *,
    OD: float,
    W: float,
    OPENING: float,
    LEAD: float,
    S: float,
    top_met: int,
    pad_length: float,
    outer_g: float,
    inner_g: float,
    port_order: list[str],
    process: ProcessRuleContext,
    render_via_cuts: bool = True,
    semantic_port_roles: bool = True,
    memo: dict | None = None,
    port_spacing: float | None = None,
) -> Cell:
    """Render one compact two-turn MS winding candidate.

    ``memo`` (M1.5, the lane search only): rings and the lead pair are pure
    functions of their parameters, and most of them repeat from candidate to
    candidate, so the search reuses the Cell objects (and their memoized
    ``region``) instead of rebuilding and re-flattening them thousands of
    times; the final render passes no memo and owns fresh children.

    The two exact straight-45-straight bridge legs use the coil plane and
    its actual adjacent lower metal. Their endpoint lanes are equal-and-opposite
    about y=0, while the outer and inner magnitudes may differ; this is the
    smallest symmetry-preserving degree of freedom that lets a short inner
    flat clear the other leg's intermediate via landings.

    ``semantic_port_roles`` (port contract 2026-09-21, default ``True`` ->
    byte-identical): this winding's own P1/N1 logical roles, independent
    of ``port_order``'s display names -- ind_sym's own public contract
    (``test_ind_sym_ct_port_order_override``). xfm_ms/xfm_balun's own
    compact branch instead reuses this winding as an unlabelled multi-turn
    sub-component under THEIR OWN semantic scheme (P2/N2 or whatever the
    caller's ``port_order`` says), so they pass ``False``: each leg's
    logical_name then falls back to its own registered name (the
    ``base_lead``/``base_lead_pair`` default when no override is given),
    matching what the pre-contract ``xfm_ms``/``_ci_winding`` transplant
    loop's ``"logical_name": q["name"]`` line used to do by hand.
    """
    bottom_met = _metal_below(top_met, process)
    pitch = W + S
    inner_od = OD - 2.0 * pitch
    outer_opening = outer_g - pad_length / 2.0
    inner_opening = inner_g - pad_length / 2.0
    # Chamfer staircase (six-family tight-spacing clearance): the
    # feasibility search self-DRC-checks THESE rendered polygons, so at
    # S == the effective floor the rings' own chamfer quantization loss
    # (a few nm) would fail every candidate before the bridge is even
    # judged; the same bias applies to search candidates and the final
    # winding alike (both render through this function).
    cb_delta = chamfer_staircase_delta(
        [OD, inner_od], W, top_met, process)
    cell = Cell(
        f"ind_sym_OD{OD}_W{W}_O{OPENING}_L{LEAD}_S{S}_NT2",
        "ind_sym",
        {
            "OD": OD,
            "W": W,
            "OPENING": OPENING,
            "LEAD": LEAD,
            "S": S,
            "NT": 2,
            "TOP_ME": str(top_met),
            "BTM_ME": str(bottom_met),
            "LEG2_BTM_ME": str(top_met),
            "compact_bridge_pad_length_um": pad_length,
            "compact_bridge_outer_offset_um": outer_g,
            "compact_bridge_inner_offset_um": inner_g,
            "process": process.profile_id,
        },
    )
    build = _memoized(memo)
    cell.inst(
        build(base_oct, OD=OD, W=W, LOP=outer_opening, ROP=OPENING, MET=top_met, process=process),
        (0.0, 0.0),
        "R0",
    )
    cell.inst(
        build(base_oct, OD=inner_od, W=W, LOP=inner_opening, ROP=0.0, MET=top_met, process=process, chamfer_bias=cb_delta),
        (0.0, 0.0),
        "R0",
    )
    pin = _pin(top_met, process)
    cell.inst(
        build(base_lead_pair, W=W, OPENING=OPENING, LEAD=LEAD + W, TOP_ME=str(top_met), LEAD_ME=str(top_met),
              P1TXT=port_order[0], N1TXT=port_order[1], process=process, port_metal=top_met, port_label_layer=pin,
              port_p1_logical_name=("P1" if semantic_port_roles else None),
              port_n1_logical_name=("N1" if semantic_port_roles else None), PORT_SPACING=port_spacing),
        (OD / 2.0 - W, 0.0),
        "R0",
    )

    outer_h = OD / 2.0 - W / 2.0
    inner_h = inner_od / 2.0 - W / 2.0
    outer_up = (-outer_h, outer_g)
    outer_down = (-outer_h, -outer_g)
    inner_up = (-inner_h, inner_g)
    inner_down = (-inner_h, -inner_g)
    straight = (outer_g + inner_g - pitch) / 2.0
    leg1 = [
        outer_up,
        (outer_up[0], outer_up[1] - straight),
        (inner_down[0], inner_down[1] + straight),
        inner_down,
    ]
    leg2 = [
        outer_down,
        (outer_down[0], outer_down[1] + straight),
        (inner_up[0], inner_up[1] - straight),
        inner_up,
    ]
    # Reference ind bridge scheme (design-region issue 03, 2026-07-31):
    # leg2 stays on the coil's OWN layer, crossing OVER, while leg1 dives
    # exactly one level -- two layers total, like base_ind_hud_cross's
    # reference behavior. leg2's endpoint "pads" degenerate to metal-only
    # rectangles on the top layer (TOP_ME == BTM_ME suppresses cuts, the
    # reference-honored convention), flush with the ring arm ends; only
    # leg1 keeps real via stacks. The former top_met-2 routing spent an
    # extra layer + longer stacks without necessity.
    add_wide_path(cell, _metal(bottom_met, process), leg1, W)
    add_wide_path(cell, _metal(top_met, process), leg2, W)
    for bottom, endpoints in (
        (bottom_met, (outer_up, inner_down)),
        (top_met, (outer_down, inner_up)),
    ):
        for px, py in endpoints:
            origin_x = px - W / 2.0
            origin_y = py - pad_length / 2.0
            if render_via_cuts:
                cell.inst(
                    vias(
                        Length=pad_length,
                        Width=W,
                        TOP_ME=top_met,
                        BTM_ME=bottom,
                        process=process,
                    ),
                    (origin_x, origin_y),
                    "R0",
                )
            else:
                # Lane search only needs the metal footprint.  The exact
                # same pad window was already accepted by ``vias`` in
                # _compact_two_turn_via_length; defer its potentially hundreds of
                # cut rectangles until the one chosen candidate is rendered.
                for met in ((top_met,) if bottom == top_met else (bottom, top_met)):
                    cell.add_rect(
                        _metal(met, process),
                        origin_x,
                        origin_y,
                        origin_x + W,
                        origin_y + pad_length,
                    )

    # (port contract 2026-09-21) P1/N1 already registered on the
    # base_lead_pair legs above -- no independent x_um=OD/2.0+LEAD
    # recompute, and no separate port-registration call, here any more.
    return cell


def _compact_two_turn_candidate_is_qualified(
    cell: Cell,
    *,
    top_met: int,
    process: ProcessRuleContext,
) -> bool:
    metals = (top_met, _metal_below(top_met, process))
    regions = _ms_layer_regions(cell, metals, process)
    # Top: leg2 joins one outer arc to the inner ring (one blob) + the
    # other outer arc = 2 components.  Mn-1: leg1 with its own endpoint
    # pads merged = 1.  Any other count is a topology bypass even if
    # same-net DRC is clean (reference bridge scheme, design-region
    # issue 03: leg2 lives on the top layer, top_met-2 is not drawn).
    if [region.count() for region in regions] != [2, 1]:
        return False
    # A lane can put the inner pads across y=0, closing the inner ring
    # without changing these component counts. Such a same-layer loop
    # shorts a turn; spacing checks cannot detect it after polygons merge.
    if any(polygon.holes() for region in regions for polygon in region.each()):
        return False
    combined = kdb.Region()
    for region in regions:
        combined += region
    if combined.merged().count() != 1:
        return False
    for met, region in zip(metals, regions):
        name = _metal_name(met)
        rule = process.adapter.metal_rule(name)
        if rule.min_width_um is None or rule.min_space_um is None:
            raise PortError(
                f"{process.profile_id}: {name} lacks width/space rules for "
                "compact two-turn bridge planning"
            )
        if region.width_check(
            _nm(rule.min_width_um), False, kdb.Metrics.Euclidian
        ).count():
            return False
        if region.space_check(
            _nm(rule.min_space_um), False, kdb.Metrics.Euclidian
        ).count():
            return False
    return True


def _compact_two_turn_lane_offsets(
    *,
    OD: float,
    W: float,
    OPENING: float,
    LEAD: float,
    S: float,
    top_met: int,
    pad_length: float,
    port_order: list[str],
    process: ProcessRuleContext,
    candidate_qualifier=None,
    memo: dict | None = None,
    port_spacing: float | None = None,
) -> tuple[float, float] | None:
    """Return the closest DRC-clean symmetric lane pair for one pad length.

    Lane pairs are ordered by their total (outer_g + inner_g, ascending) and
    then by their difference (ascending); the answer is the first qualifying
    pair. Only each total's most balanced admissible pair is judged (M1.5):
    a larger difference moves the inner lane toward y=0 and the outer lane
    toward the chamfer and never rescues a total whose balanced pair fails --
    over 3,951 totals on demo_6m and N28 the first admissible difference was
    the first qualifying one every time, so this is the same answer with ~30
    times fewer candidates.
    """
    pitch = W + S
    inner_od = OD - 2.0 * pitch
    # A lane's pad (centre g, length pad_length) must keep the ring opening real
    # (max_opening) AND sit entirely on its ring's flat: its far edge at or
    # below BA (D4, M1.6 -- the same bound base_ind_hud_cross enforces).
    cb_delta = chamfer_staircase_delta([OD, inner_od], W, top_met, process)
    outer_max = min(max_opening(OD, W) + pad_length / 2.0, octagon(OD, W).BA - pad_length / 2.0)
    inner_max = min(max_opening(inner_od, W) + pad_length / 2.0, octagon(inner_od, W, cb_delta).BA - pad_length / 2.0)
    minimum_total = ceiltogrid(pitch + pad_length)
    maximum_total = floortogrid(outer_max + inner_max)
    # Fifty mask-grid units retain 250-nm lane resolution while keeping this
    # real-polygon feasibility search cheap enough for query-library sweeps;
    # every candidate coordinate remains on the 0.005-um mask grid.
    lane_step = 50 * GRID_UM
    total_steps = math.floor(
        (maximum_total - minimum_total + _EPS) / lane_step
    )
    for total_step in range(total_steps + 1):
        total = roundtogrid(minimum_total + total_step * lane_step)
        minimum_difference = max(0.0, total - 2.0 * inner_max)
        maximum_difference = min(total, 2.0 * outer_max - total)
        if minimum_difference > maximum_difference + _EPS:
            continue
        first_difference = math.ceil(
            (minimum_difference - _EPS) / lane_step
        )
        last_difference = math.floor(
            (maximum_difference + _EPS) / lane_step
        )
        for difference_step in range(first_difference, last_difference + 1):
            difference = difference_step * lane_step
            outer_g = roundtogrid((total + difference) / 2.0)
            inner_g = roundtogrid((total - difference) / 2.0)
            if outer_g > outer_max + _EPS or inner_g > inner_max + _EPS:
                continue
            candidate = _compact_two_turn_candidate(
                OD=OD,
                W=W,
                OPENING=OPENING,
                LEAD=LEAD,
                S=S,
                top_met=top_met,
                pad_length=pad_length,
                outer_g=outer_g,
                inner_g=inner_g,
                port_order=port_order,
                process=process,
                render_via_cuts=False,
                memo=memo,
                port_spacing=port_spacing,
            )
            if not _compact_two_turn_candidate_is_qualified(
                candidate, top_met=top_met, process=process
            ):
                break                         # this total's balanced pair fails: no larger difference will pass
            if candidate_qualifier is None:
                return outer_g, inner_g
            # The optional qualifier represents an OUTER nested-net
            # obstacle.  The coarse self-DRC search above advances both
            # lanes in 250-nm steps; refine the first self-clean pair inward
            # on the 5-nm mask grid so a 50-nm conditional-spacing repair is
            # not skipped.  Later coarse pairs only move farther toward that
            # same outer obstacle, so they cannot recover qualification.
            refine_steps = int(round(lane_step / GRID_UM))
            for refine_step in range(refine_steps + 1):
                adjustment = refine_step * GRID_UM
                refined_outer = roundtogrid(outer_g - adjustment)
                refined_inner = roundtogrid(inner_g - adjustment)
                if refined_outer < pad_length / 2.0 - _EPS:
                    break
                if refined_inner < pad_length / 2.0 - _EPS:
                    break
                refined = _compact_two_turn_candidate(
                    OD=OD,
                    W=W,
                    OPENING=OPENING,
                    LEAD=LEAD,
                    S=S,
                    top_met=top_met,
                    pad_length=pad_length,
                    outer_g=refined_outer,
                    inner_g=refined_inner,
                    port_order=port_order,
                    process=process,
                    render_via_cuts=False,
                    memo=memo,
                    port_spacing=port_spacing,
                )
                if _compact_two_turn_candidate_is_qualified(
                    refined, top_met=top_met, process=process
                ) and candidate_qualifier(refined):
                    return refined_outer, refined_inner
            return None
    return None


def _compact_two_turn_winding(
    *,
    OD: float,
    W: float,
    OPENING: float,
    LEAD: float,
    S: float,
    top_met: int,
    port_order: list[str],
    process: ProcessRuleContext,
    family: str,
    max_pad_length: float | None = None,
    candidate_qualifier=None,
    semantic_port_roles: bool = True,
    port_spacing: float | None = None,
) -> Cell:
    """Find the closest DRC-clean symmetric lane pair for compact NT=2.

    ``semantic_port_roles`` (port contract 2026-09-21, default ``True`` ->
    byte-identical) only reaches the FINAL rendered candidate below --
    every candidate probed during the lane/pad search renders with the
    default (their logical_name is never read by the DRC/qualification
    checks), so threading it through the search itself would be a no-op
    plumbing exercise, not a behavior difference."""
    _check_opening(OD, W, OPENING, family)
    # xfm_ms (NT_M=2) and xfm_balun (NT=2) enter here without passing through
    # ind_sym, and the candidate qualifier below ignores wide-line spacing.
    _check_trace_rules(W, OPENING, LEAD, top_met, process, family)
    pitch = W + S
    inner_od = OD - 2.0 * pitch
    if inner_od <= 2.0 * W:
        raise PortError(
            f"{family}: OD={OD:.3f} um leaves inner OD={inner_od:.3f} um "
            f"for W={W:.3f}, S={S:.3f}; increase OD or reduce W/S"
        )
    minimum_pad = _compact_two_turn_via_length(W, top_met, process, family)
    maximum_pad = W if max_pad_length is None else min(W, max_pad_length)
    if maximum_pad < minimum_pad - _EPS:
        raise PortError(
            f"{family}: compact bridge pad cap {maximum_pad:.3f} um is "
            f"below the minimum legal landing {minimum_pad:.3f} um"
        )
    pad_step = 50 * GRID_UM
    pad_steps = math.floor((maximum_pad - minimum_pad + _EPS) / pad_step)
    pad_candidates = [
        roundtogrid(minimum_pad + step * pad_step)
        for step in range(pad_steps + 1)
    ]
    if pad_candidates[-1] < maximum_pad - _EPS:
        pad_candidates.append(roundtogrid(maximum_pad))

    # A shorter centred landing preserves the same endpoint connection while
    # removing metal and via rows, so feasibility is monotonic on the shared
    # 250-nm planner grid.  Binary-search the largest DRC-clean footprint;
    # retain the exact W endpoint so unconstrained structures use a full W x W
    # landing, matching the established crossover primitive.
    best: tuple[float, float, float] | None = None
    low, high = 0, len(pad_candidates) - 1
    memo: dict = {}
    while low <= high:
        middle = (low + high) // 2
        pad = pad_candidates[middle]
        offsets = _compact_two_turn_lane_offsets(
            OD=OD,
            W=W,
            OPENING=OPENING,
            LEAD=LEAD,
            S=S,
            top_met=top_met,
            pad_length=pad,
            port_order=port_order,
            process=process,
            candidate_qualifier=candidate_qualifier,
            memo=memo,
            port_spacing=port_spacing,
        )
        if offsets is None:
            high = middle - 1
        else:
            best = (pad, offsets[0], offsets[1])
            low = middle + 1

    if best is not None:
        pad, outer_g, inner_g = best
        return _compact_two_turn_candidate(
            OD=OD,
            W=W,
            OPENING=OPENING,
            LEAD=LEAD,
            S=S,
            top_met=top_met,
            pad_length=pad,
            outer_g=outer_g,
            inner_g=inner_g,
            port_order=port_order,
            process=process,
            semantic_port_roles=semantic_port_roles,
            render_via_cuts=True,
            port_spacing=port_spacing,
        )
    raise PortError(
        f"{family}: no DRC-clean compact two-turn bridge fits OD={OD:.3f}, "
        f"W={W:.3f}, S={S:.3f} on {_metal_name(top_met)}; increase OD or "
        "reduce W/S (bridge spacing is never relaxed)"
    )
