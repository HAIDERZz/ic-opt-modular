# Auto-split from pcell_inductor_port_clean.py (wrap-up P1,
# 2026-07-28): verbatim segment move, no behavior change. The
# facade module re-exports every name; see its docstring for
# provenance and the KNOWN_DEVIATIONS record.
from __future__ import annotations

import math

import klayout.db as kdb

from ic_opt.em.pcell._pcell_core import (
    _EPS,
    _ORIENTS,
    GRID_UM,
    PI,
    Cell,
    GroundFixtureConfig,
    PortError,
    ProcessRuleContext,
    _effective_min_spacing,
    _metal,
    _metal_below,
    _metal_index,
    _metal_name,
    _min_met_spacing,
    _nm,
    _pin,
    _required_parallel_spacing,
    _xfm_order_ports,
    add_ground_fixture,
    bridge_y_reach,
    ceiltogrid,
    chamfer_staircase_delta,
    cross_endpoint_offset,
    finalize_emx_ports,
    floortogrid,
    max_opening,
    roundtogrid,
    vias,
)
from ic_opt.em.pcell._pcell_guards import (
    _check_bridge_escape_clearance,
    _check_opening,
    _check_winding_fit,
    _heal_seam_notches,
    _xfm_net_short,
)
from ic_opt.em.pcell._pcell_primitives import (
    _pad_trim_floor,
    base_ind_diag,
    base_lead,
    base_lead_pair,
    base_oct,
    base_xfm_cross,
    xfm_cross_far_pad_y0,
)
from ic_opt.em.pcell._pcell_xfm_balun import (
    _balun_crossunder,
    _escape_tip_pad_width,
)

# ---------------------------------------------------------------------------
# xfm_il (M14 pre-contract, xfm-il-interleaved ticket 02 + 02b + 02c + 02d
# rework): same-layer interleaved (Rabjohn/Frlan-style) transformer -- two
# symmetric multi-turn windings on ONE metal-generic plane SL_ME, alternating
# radial bands (P occupies the outer-starting even bands, S the odd bands),
# each turn's crossunder bridge split across TWO further layers (SL_ME-1,
# SL_ME-2).
#
# Topology (ticket 02d, v4 final -- user-confirmed 2026-07-18,
# proposal_final.png): P is ``ind_sym`` completely unmodified -- native
# port-right + alternating left/right bridge zigzag (the same, long-tested
# topology every standalone multi-turn ind_sym already uses). S is the exact
# same construction (``_ind_ring_turns``, the ring/bridge kernel factored out
# of ind_sym, still zigzagging) built in a local "opens right" frame with a
# crossunder escape (``_balun_crossunder``) attached on that local
# ``+OD_S/2`` arm, then the WHOLE S sub-cell is mirrored ``MY`` so its port
# and escape land on the GLOBAL LEFT (opposite P's right) while its own
# bridges mirror along with it. This retires ticket 02b/02c's
# ``bridge_side="left"`` same-side-stacking construction (still available on
# ``ind_sym``/``_ind_ring_turns`` for any other caller, just unused here):
# 02b's same-side co-location shorted P1 to N1 (both winding terminals on
# ONE closed loop); 02c's fix (bridges opposite the lead/escape arm) instead
# left odd-NT innermost turns fully closed (``LOP=ROP=0``, see
# ``_ind_ring_turns``'s ``"left"`` branch) -- a closed ring is a shorted turn
# (0 holes required, 1 observed; ticket 02d's own "no closed loop" test
# invariant). Reverting to the reference zigzag makes both bugs
# constructively impossible again (every turn keeps exactly one bridge gap
# open, confirmed by ind_sym's own long-standing tests).
#
# Dual-layer legs (ticket 02d's other core fix): each turn's crossunder is
# TWO independent legs (``base_ind_hud_cross``'s cross1/cross2, see its
# docstring) that used to share ONE layer below SL (``crossunder_sl1_only``,
# ticket 02). With the interleaved lattice's doubled PITCH, one winding's
# leg1+leg2 footprint on that single shared layer could collide with the
# OTHER winding's own leg1+leg2 footprint there (found empirically via the
# built geometry: the "M8-merged" region straddling an X). ``LEG2_BTM_ME``
# (see ``base_ind_hud_cross``) now routes leg1 through SL_ME-1 (unchanged)
# and leg2 through SL_ME-2 -- three distinct layers cover P's crossunders,
# S's crossunders and S's escape between them, so no two windings' bridge
# metal ever needs to share a single layer's worth of the same radial gap.
# ---------------------------------------------------------------------------


def _il_ct_adjacency_guard(
    which: str, SL_ME: str, CT_ME: str,
    process: ProcessRuleContext | None = None,
) -> None:
    """Fail closed on a DOWNWARD xfm_il CT metal that shorts the tap net
    against either crossunder leg (ticket 03, promoting
    ``_ind_ct_adjacency_guard``'s N1 rule from two layers to three): ticket
    02d's dual-layer crossunder ALWAYS occupies BOTH the real conductor one
    level below SL_ME (leg1) and the real conductor two levels below it
    (leg2) -- unlike ind_sym's single-layer crossunder, which only ever
    occupies one level below -- so a CT tap's via stack landing on either of
    those two layers galvanically shorts the tap net to a mid-winding
    bridge leg. Only the conductor strictly below leg2 clears both (``02c``
    era's ``<= SL-2`` bound no longer holds under ``02d``'s two-leg
    crossunder).

    Ticket 03c: this bound applies ONLY when CT_ME is BELOW SL_ME (a
    downward tap, still sharing the coil's own leg1/leg2 layers with the
    crossunder legs) -- see ``_il_ct_metal_guard``, which dispatches here
    only in that case. An UPWARD CT_ME (above SL_ME) never touches leg1/
    leg2 at all, so no such floor applies there.

    ``leg1``/``leg2`` are resolved from the real profile stack
    (``_metal_below``, gdsfactory review 2026-09-21), not bare ``SL_ME-1``/
    ``SL_ME-2`` arithmetic: on N65+AP, SL_ME="AP" leaves leg1=M9/leg2=M8
    (N65 has no M10 between M9 and AP). ``xfm_il``'s own SL_ME floor guard
    always runs before this one, so both levels are guaranteed to exist by
    the time this is reached."""
    sl = _metal_index(SL_ME)
    ct = _metal_index(CT_ME)
    leg1 = _metal_below(sl, process, levels=1)
    leg2 = _metal_below(sl, process, levels=2)
    if ct >= leg2:
        raise PortError(
            f"xfm_il: CT{which} metal {_metal_name(ct)} must sit at least "
            f"three levels below the coil top metal {_metal_name(sl)}: "
            f"ticket 02d's dual-layer crossunder occupies both "
            f"{_metal_name(leg1)} (leg1) and {_metal_name(leg2)} "
            f"(leg2), so a CT on either layer galvanically shorts the tap "
            f"net to the mid-winding bridges (ticket 03, N1-style rule)"
        )


def _il_ct_metal_guard(
    which: str, SL_ME: str, CT_ME: str,
    process: ProcessRuleContext | None = None,
) -> str:
    """CT metal legality + direction dispatch (ticket 03c, user directive
    after Virtuoso review): CT_ME ABOVE SL_ME -> ``"up"`` (an upward tap;
    no lower bound -- nothing else this device ever draws touches a metal
    above SL_ME, so there is no adjacency floor to enforce, unlike the
    downward case). CT_ME BELOW SL_ME -> ``"down"`` (still bound by
    ``_il_ct_adjacency_guard``'s real-stack leg2 floor, ticket 02d's
    crossunder still occupies leg1/leg2 there). CT_ME == SL_ME is always
    illegal -- that is the coil ring's own layer, not a separate tap
    plane (``_il_ct_adjacency_guard`` would also reject it, since
    ``ct >= leg2`` is trivially true at ``ct == sl``, but this gives it
    an accurate message instead of the downward-only "three levels below"
    one)."""
    sl = _metal_index(SL_ME)
    ct = _metal_index(CT_ME)
    if ct == sl:
        raise PortError(
            f"xfm_il: CT{which} metal {_metal_name(ct)} cannot equal "
            f"SL_ME {_metal_name(sl)} -- that is the coil ring's own "
            f"layer, not a separate tap plane"
        )
    if ct > sl:
        return "up"
    _il_ct_adjacency_guard(which, SL_ME, CT_ME, process=process)
    return "down"


def _il_ct_region(shapes, layer, mirror=False):
    """Merged kdb.Region of one layer's shapes from a Cell's flat_shapes()
    (nm-domain integer points already), optionally MY-mirrored (x -> -x,
    y unchanged) point-by-point before insertion -- used to view one
    winding's already-built geometry from the OTHER winding's frame (see
    ``_il_ct_tap_lateral``'s docstring) without touching klayout's own
    Trans/mirror convention at all."""
    reg = kdb.Region()
    mirror_fn = _ORIENTS["MY"] if mirror else (lambda x, y: (x, y))
    for lay, pts in shapes:
        if lay == layer:
            reg.insert(kdb.Polygon([kdb.Point(*mirror_fn(x, y)) for x, y in pts]))
    reg.merge()
    return reg


def _il_ct_tap_exact(cell, other_sl1, other_sl2, which, OD, W, LEAD, NT,
                     SL_ME, CT_ME, port_name, process, PITCH,
                     other_ct_lead=None):
    """Center-tap construction (ticket 03c, replacing 03b's tangential
    offset after Virtuoso review): taps the innermost turn's TRUE
    ELECTRICAL MIDPOINT, exactly ``_ind_ct_tap``'s own LEFT (odd NT) /
    RIGHT (even NT) closed-column ``tap_x``, at a FIXED y -- never offset.

    **Why the midpoint must be exact (user directive, restating 03b's own
    finding even more strictly):** a symmetric center-tapped winding's CT
    is an AC ground / the differential symmetry plane -- P and N must see
    EQUAL inductance looking in, which requires tapping the exact point
    that splits the winding's Euler PATH into two equal halves. The
    innermost turn is a single-gap ring, LEFT-RIGHT symmetric about y=0
    (``base_oct_half`` + its own ``MX`` mirror), so that point is
    precisely y=0 on the ring's closed arm (opposite its one gap) -- see
    ticket 03b's own docstring history for the full arc-length argument.
    03b treated a bounded TANGENTIAL offset off that point as an
    acceptable compromise when SL_ME-1/SL_ME-2 were blocked there; the
    user, after inspecting a rendered sample, correctly identified that
    compromise as itself still a deviation from the true midpoint and
    asked for the ALTERNATIVE ticket 03b's own docstring already named
    but did not take: go UP instead of sideways. This function drops the
    search entirely -- the tap is always exactly at native y=0 (via_y =
    ``-W/2``, the SAME single position ``_ind_ct_tap`` and 03b's own k=0
    candidate both used), never anywhere else.

    **Direction is decided by comparing CT_ME to SL_ME (ticket 03c),
    never a caller-supplied flag:**

    - CT_ME ABOVE SL_ME ("up"): the via stack runs SL_ME -> CT_ME
      upward, and the CT lead runs on CT_ME horizontally OVER every ring
      this device ever draws (P and S only ever occupy SL_ME and below --
      SL_ME-1/SL_ME-2 for the crossunder legs -- so every metal above
      SL_ME is structurally empty except for what a CT tap itself adds).
      No adjacency floor applies (unlike downward) and no collision check
      against the other winding's legs is needed -- there is nothing to
      collide with up there. ``_il_ct_metal_guard`` (called by ``xfm_il``
      before this function) is what enforces "CT_ME must actually be
      above SL_ME to take this path"; this function just re-derives the
      same comparison to route the via stack's TOP_ME/BTM_ME correctly.
    - CT_ME BELOW SL_ME ("down"): unchanged legality from ticket 03 --
      ``_il_ct_adjacency_guard`` (CT<=SL_ME-3) already ran before this
      function is reached, so the via stack legitimately spans SL_ME-1
      AND SL_ME-2 as intermediate landing pads (``vias()`` fills every
      level). Because the tap is now pinned to the SINGLE native y=0
      point (no search), this function checks that ONE window against
      the OTHER winding's SL_ME-1/SL_ME-2 region there and, if blocked,
      fails closed BY NAME instead of silently drifting off the true
      midpoint (03b's own compromise) -- the message suggests switching
      to an upward CT_ME instead (unless SL_ME is already the top of the
      stack, "AP", in which case no upward option exists and that is
      correctly reported as this body's own real limit).

      **CTP's downward tap is structurally blocked EVERY TIME** (proven,
      not merely common -- swept across NT in 2..6 and six OD/W/S/OPENING
      combinations spanning two orders of magnitude in the ticket 03c
      test suite, zero exceptions): P's own innermost ring sits, BY THE
      DEFINITION of an interleaved lattice built at half-pitch offset,
      exactly in the radial gap between two consecutive S rings -- and
      S's own inter-turn bridge spanning that SAME gap has a y-reach
      (``bridge_y_reach``) that scales with the SAME ``W``/``S`` terms
      that size the via's own W x W footprint, so the via's y-span is
      mathematically always a SUBSET of the blocking region, never a
      matter of tuning OD/OPENING wider. A downward CTP is therefore not
      a rare corner case to work around -- it should be read as simply
      unavailable, and an upward CT_P_ME (or none at all, on an SL_ME="AP"
      body, where CTP genuinely cannot be tapped in either direction) is
      the expected, not exceptional, choice. CTS's downward tap is the
      mirror opposite: S is always the device's OVERALL innermost
      winding, so no P bridge (which only ever spans between P's OWN,
      strictly larger-radius ring pairs) ever reaches in far enough to
      block it -- confirmed clear across the same sweep, zero exceptions.

    Either direction: if CT_P_ME and CT_S_ME resolve to the SAME metal,
    both taps' LEADS live on that one shared layer and each lead's
    horizontal run typically spans most of the device, so the two CAN in
    principle mutually overlap there (found empirically in ticket 03b on
    an AP-body CT_P_ME=CT_S_ME="8" build). ``other_ct_lead``, when given
    (only the SECOND-placed tap -- CTS, since CTP always builds first --
    ever receives one, and only when the two CT metals match), is the
    already-drawn FIRST tap's own CT_ME-layer region in the frame this
    call builds in; this is checked regardless of direction. (Ticket 03c's
    own equal-turns constraint, NT_P==NT_S, makes CTP and CTS structurally
    land on OPPOSITE global sides in every configuration -- see
    ``xfm_il``'s docstring -- so this check is not expected to ever fire
    through the public API any more; it is kept as a defensive backstop,
    not dead code, since it is still reachable from a direct unit call
    with a contrived ``other_ct_lead``.) Both this check and the downward
    SL_ME-1/SL_ME-2 check grow the obstacle by that layer's OWN min_space
    (``_min_met_spacing``) before testing, not a bare zero-margin overlap
    test: two same-layer polygons merely TOUCHING (zero-area intersection,
    which a plain ``&`` treats as "clear") still galvanically merge on a
    real layer -- found empirically in ticket 03b.

    **Exit side is NT-parity dependent** (odd NT exits LEFT, even NT
    exits RIGHT -- ``_ind_ct_tap``'s own native intent): CTP builds
    directly in xfm_il's global P frame, so its side is P's own parity.
    CTS builds in S's *local* pre-mirror frame; ticket 02d's whole-cell MY
    mirror flips x (preserves y), so a LOCAL left/right exit becomes the
    OPPOSITE side globally -- xfm_il's own CTS call site documents the
    resulting global side per parity. Unaffected by up/down direction:
    that is purely a layer (Z) choice, orthogonal to the X exit side."""
    sl_met = _metal_index(SL_ME)
    ct_met = _metal_index(CT_ME)
    upward = ct_met > sl_met
    # Real profile-stack leg1/leg2, not bare `sl_met - 1`/`sl_met - 2`
    # (gdsfactory review 2026-09-21) -- see xfm_il's own SL_ME floor guard,
    # which already proved both levels exist before this function is ever
    # reached.
    leg1_met = _metal_below(sl_met, process, levels=1)
    leg2_met = _metal_below(sl_met, process, levels=2)
    if NT % 2 == 0:
        tap_x = OD / 2 - (NT - 1) * PITCH - W
        lead_x = tap_x
    else:
        tap_x = -OD / 2 + (NT - 1) * PITCH
        lead_x = -OD / 2 - LEAD
    lead_len = LEAD + (NT - 1) * PITCH + W
    via_y = -W / 2.0

    if not upward:
        # Downward: the true midpoint is now a FIXED point (no tangential
        # search, ticket 03c) -- check that ONE window against the other
        # winding's SL_ME-1/SL_ME-2 crossunder, margin-grown (see
        # docstring), and fail closed by name if blocked.
        sl1_margin_nm = _nm(_min_met_spacing(leg1_met, process, None))
        sl2_margin_nm = _nm(_min_met_spacing(leg2_met, process, None))
        x0_nm, x1_nm = _nm(tap_x), _nm(tap_x + W)
        y0_nm, y1_nm = _nm(via_y), _nm(via_y + W)
        box = kdb.Region(kdb.Box(x0_nm, y0_nm, x1_nm, y1_nm))
        blocked = (
            not (box.sized(sl1_margin_nm) & other_sl1).is_empty()
            or not (box.sized(sl2_margin_nm) & other_sl2).is_empty()
        )
        if blocked:
            if sl_met < 11:
                # A specific next-metal-up example is not offered here: the
                # real profile stack may skip numbers above SL_ME too (e.g.
                # N65's own M9 -> AP has no M10), so only 'AP' -- always the
                # real top of every stack this codebase models -- is named
                # unconditionally (gdsfactory review 2026-09-21).
                hint = (
                    f"try an upward CT{which}_ME instead (above SL_ME, "
                    f"e.g. 'AP', or another real conductor above SL_ME in "
                    f"this profile) -- nothing else on this device is ever "
                    f"drawn above SL_ME, so an upward tap is never blocked"
                )
            else:
                hint = (
                    "SL_ME='AP' has no metal above it, so a downward tap "
                    "is the only option here -- widen OPENING/OD/S so the "
                    "two windings' crossunders clear at the true midpoint "
                    "instead (this is a real limit of the AP body, not a "
                    "bug)"
                )
            raise PortError(
                f"xfm_il: CT{which} exact-midpoint tap at the innermost "
                f"ring's closed column (x={tap_x:.3f} um) is blocked on "
                f"{_metal_name(leg1_met)}/{_metal_name(leg2_met)} by "
                f"the other winding's crossunder there (ticket 03c: the "
                f"true midpoint is a FIXED point, no tangential offset); "
                f"{hint}"
            )

    if other_ct_lead is not None:
        ct_margin_nm = _nm(_min_met_spacing(ct_met, process, None))
        lx0_nm, lx1_nm = sorted((_nm(lead_x), _nm(lead_x + lead_len)))
        y0_nm, y1_nm = _nm(via_y), _nm(via_y + W)
        lead_box = kdb.Region(kdb.Box(lx0_nm, y0_nm, lx1_nm, y1_nm))
        if not (lead_box.sized(ct_margin_nm) & other_ct_lead).is_empty():
            raise PortError(
                f"xfm_il: CT{which}'s lead on {_metal_name(ct_met)} is "
                f"blocked by the other CT tap's own already-drawn lead on "
                f"that same shared metal -- choose different CT_P_ME/"
                f"CT_S_ME metals, or adjust OD/turn counts so the two "
                f"taps' opposite-side exits (ticket 03c's equal-turns "
                f"constraint keeps them on opposite global sides) clear "
                f"with margin"
            )

    top_met_for_via, btm_met_for_via = (
        (ct_met, sl_met) if upward else (sl_met, ct_met)
    )
    # The lead itself registers its own port (port contract 2026-09-21):
    # base_lead(L=lead_len, W=W, TOP_ME=BTM_ME=str(ct_met)) draws the exact
    # same vias(Length=W, Width=lead_len, TOP_ME=ct_met, BTM_ME=ct_met)
    # this call site drew directly before -- zero geometry change, only the
    # port now rides the same local integer-nm frame as that lead instead
    # of a second, independent `ct_tip_x = OD/2+LEAD if ... else -OD/2-LEAD`
    # macro sum (mechanism (2), the segmented-sum-vs-macro-sum divergence
    # this migration closes). `port_x_um`: even NT lands on base_lead's own
    # default far tip (`lead_x + lead_len == OD/2 + LEAD`, verified by the
    # same algebra as `lead_x`/`lead_len` below); odd NT instead sits at
    # the lead's NEAR end (`lead_x` itself, local x=0) -- `lead_x ==
    # -OD/2-LEAD` there already, the identical odd/even split
    # `_ind_ct_tap` uses for its own CT lead (see that function's
    # docstring).
    cell.inst(
        base_lead(
            L=lead_len, W=W, TOP_ME=str(ct_met), BTM_ME=str(ct_met),
            process=process,
            port_name=port_name, port_logical_name=port_name,
            port_metal=ct_met, port_label_layer=_pin(ct_met, process),
            port_x_um=(0.0 if NT % 2 == 1 else None),
        ),
        (lead_x, via_y), "R0",
    )
    cell.inst(
        vias(Length=W, Width=W, TOP_ME=top_met_for_via,
             BTM_ME=btm_met_for_via, process=process),
        (tap_x, via_y), "R0",
    )


def _il_bridge_target_gap(
    W: float, S: float, sl: int, process: ProcessRuleContext | None
) -> float:
    """Common P/S diagonal gap for both xfm_il bridge layers.

    ``S`` remains the public coil-band edge spacing.  In process mode a
    bridge layer may require more, so both layers use the stricter common
    target to keep the SL-1/SL-2 weave geometrically symmetric.  The result
    is rounded upward to the mask grid; no process number lives in source.
    """
    target = S
    if process is not None:
        # Real leg1/leg2 conductors, not bare `sl - 1`/`sl - 2` (gdsfactory
        # review 2026-09-21) -- see xfm_il's own SL_ME floor guard.
        for met in (_metal_below(sl, process, levels=1),
                    _metal_below(sl, process, levels=2)):
            target = max(
                target,
                _required_parallel_spacing(met, W, W, process),
            )
    return ceiltogrid(target)


def _il_bridge_escape_lane_offset(
    W: float,
    S: float,
    sl: int,
    process: ProcessRuleContext | None,
    opening_p: float,
    opening_s: float,
) -> tuple[float, float]:
    """Smallest P/S outer shifts clearing their opposite lead channels."""
    # Real leg1 conductor, not a bare `sl - 1` (gdsfactory review 2026-09-21).
    leg1 = _metal_below(sl, process, levels=1)
    reach = bridge_y_reach(W, S, leg1, process)
    required = _required_parallel_spacing(leg1, W, W, process)
    return (
        ceiltogrid(max(0.0, required - (opening_s - reach))),
        ceiltogrid(max(0.0, required - (opening_p - reach))),
    )


def _il_bridge_diagonal_regions(
    W: float,
    S: float,
    sl: int,
    source_od: float,
    leg1_dy: float,
    leg2_dy: float,
    process: ProcessRuleContext | None,
) -> tuple[kdb.Region, kdb.Region]:
    """Actual snapped SL-1/SL-2 diagonal polygons for one local-left bridge.

    This is the diagonal-only planning twin of ``_il_shifted_hud_cross``.
    It deliberately reuses ``base_ind_diag`` with the exact arguments that
    ``base_xfm_cross`` uses, so lane sizing is derived from the real W/S
    outline rather than an unsnapped centre-line approximation.
    """
    pitch = 2.0 * (W + S)
    cross_gap = pitch - W
    # Real leg1/leg2 conductors, not bare `sl - 1`/`sl - 2` (gdsfactory
    # review 2026-09-21).
    leg1 = _metal_below(sl, process, levels=1)
    leg2 = _metal_below(sl, process, levels=2)
    probe = Cell("xfm_il_bridge_diag_probe", "xfm_il_bridge_diag_probe", {})
    probe.inst(
        base_ind_diag(
            W=W,
            S=cross_gap,
            MET=leg1,
            process=process,
            clearance_met=sl,
        ),
        (-source_od / 2.0, leg1_dy),
        "R0",
    )
    probe.inst(
        base_ind_diag(
            W=W,
            S=cross_gap,
            MET=leg2,
            process=process,
            clearance_met=sl,
        ),
        (-source_od / 2.0 + pitch + W, leg2_dy),
        "MY",
    )
    return (
        _il_ct_region(probe.flat_shapes(), _metal(leg1, process)),
        _il_ct_region(probe.flat_shapes(), _metal(leg2, process)),
    )


def _il_bridge_lane_offset(
    W: float, S: float, sl: int, process: ProcessRuleContext | None
) -> tuple[float, float]:
    """Return ``(q, target_gap)`` for the symmetric xfm_il bridge weave.

    In every colliding pair the even-band bridge moves ``+q/-q`` on
    SL-1/SL-2 and the odd-band bridge moves ``-q/+q``.  ``q`` is the
    smallest 0.005-um grid value for which the REAL snapped diagonal
    polygons clear by ``target_gap``.  A binary search is safe here because
    the two parallel polygons translate monotonically apart; full landing-
    pad DRC is checked separately on the completed windings.
    """
    target = _il_bridge_target_gap(W, S, sl, process)
    target_nm = _nm(target)
    pitch = 2.0 * (W + S)
    even_od = 4.0 * pitch
    odd_od = even_od - pitch

    def clears(step: int) -> bool:
        q = step * GRID_UM
        even = _il_bridge_diagonal_regions(
            W, S, sl, even_od, +q, -q, process
        )
        odd = _il_bridge_diagonal_regions(
            W, S, sl, odd_od, -q, +q, process
        )
        return all(
            a.separation_check(
                b, target_nm, False, kdb.Metrics.Euclidian
            ).count() == 0
            for a, b in zip(even, odd)
        )

    facing = cross_endpoint_offset(W, pitch - W, sl, process)
    # Keep a positive physical gap between the two SL landing pads at the
    # smaller opening.  q==facing would collapse that opening to zero and
    # turn the intended series break into a same-layer bypass.
    max_step = math.floor((facing - GRID_UM) / GRID_UM + _EPS)
    if max_step < 0 or not clears(max_step):
        raise PortError(
            f"xfm_il: W={W}, S={S} needs a symmetric bridge-lane offset "
            f"larger than the available {max(facing - GRID_UM, 0.0):.3f} "
            f"um to preserve target gap {target:.3f} um; increase OD/S or "
            "reduce W (lane target is never relaxed)"
        )
    lo, hi = -1, max_step
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if clears(mid):
            hi = mid
        else:
            lo = mid
    return hi * GRID_UM, target


def _il_shifted_hud_cross(
    *,
    OD: float,
    W: float,
    S: float,
    TOP_ME: str,
    LEG2_BTM_ME: str,
    PITCH: float,
    LOP: float,
    ROP: float,
    leg1_dy: float,
    process: ProcessRuleContext | None,
    under: bool = True,
    chamfer_bias: int = 0,
    corridor_bias_step: int = 0,
) -> Cell:
    """One xfm_il turn with a rule-derived, endpoint-aligned bridge shift.

    The public ``base_ind_hud_cross`` remains untouched.  This private
    sibling composes the same ``base_oct``/``base_xfm_cross`` family
    primitives, but translates leg1 by ``leg1_dy`` and leg2 by its exact
    opposite.  LOP/ROP are supplied explicitly so the SL endpoints move
    together with their via landings instead of leaving a skew junction.

    ``corridor_bias_step`` (design-region issue 04): the interleaved
    lattice's staircase step between adjacent MERGED rings (cb_delta).
    The bridge's inner endpoint pads land past the OTHER winding's ring
    at ``OD - PITCH`` (merged bias = this turn's + step); at small ring
    OD their outer corner would poke into that ring's inner 45-degree
    chamfer corridor, so the pad Length is trimmed exactly like
    ``base_ind_hud_cross`` does for its own-ring corridor -- including
    the ``leg1_dy`` lane shift, which moves one pad's corner outward by
    ``|leg1_dy|``.
    """
    sl = _metal_index(TOP_ME)
    # Real leg1 conductor, not a bare `sl - 1` (gdsfactory review
    # 2026-09-21); resolved once up front so both the params-dict metadata
    # below and the actual base_xfm_cross BTM_ME reuse the same value.
    leg1 = _metal_below(sl, process, levels=1)
    params = {
        "OD": OD,
        "W": W,
        "S": S,
        "OPENING": ROP,
        "TOP_ME": TOP_ME,
        "BTM_ME": str(leg1),
        "PITCH": PITCH,
        "LEG2_BTM_ME": LEG2_BTM_ME,
        "bridge_lane_shift_um": leg1_dy,
    }
    if process is not None:
        params["process"] = process.profile_id
    if chamfer_bias:
        params["chamfer_bias"] = chamfer_bias
    cb = f"_cb{chamfer_bias}" if chamfer_bias else ""
    if corridor_bias_step:
        params["corridor_bias_step"] = corridor_bias_step
        cb += f"_cs{corridor_bias_step}"
    cell = Cell(
        f"xfm_il_hud_OD{OD}_W{W}_S{S}_Q{leg1_dy}{cb}",
        "base_ind_hud_cross",
        params,
    )
    cell.inst(
        base_oct(
            OD=OD,
            W=W,
            LOP=LOP,
            ROP=ROP,
            MET=_metal_index(TOP_ME),
            process=process,
            chamfer_bias=chamfer_bias,
        ),
        (0.0, 0.0),
        "R0",
    )
    if not under:
        return cell
    cross_gap = PITCH - W
    # Corridor trim (issue 04): inner endpoint pads vs the intermediate
    # (other winding's) ring at OD - PITCH; same quantized math as
    # base_ind_hud_cross, widened by the lane shift.
    far_pad_len = None
    if process is not None:
        cor_od = OD - PITCH
        cor_bias = chamfer_bias + corridor_bias_step
        A_c = roundtogrid(cor_od / (2 + math.sqrt(2)))
        BA_c = floortogrid((cor_od - 2 * A_c) / 2 - 0.005) \
            - cor_bias * GRID_UM
        C_w = ceiltogrid(W * math.tan(PI / 8) + 0.005)
        floor_sp = _effective_min_spacing(sl, W, process)
        y0 = xfm_cross_far_pad_y0(cross_gap, W, 0.0, sl, process)
        y_allow = (cor_od / 2 + BA_c - C_w - W) \
            - floor_sp * math.sqrt(2.0) - (OD / 2 - PITCH)
        corner = y0 + abs(leg1_dy) + W
        if y_allow < corner - 1e-9:
            far_pad_len = floortogrid(y_allow - y0 - abs(leg1_dy))
            if far_pad_len < _pad_trim_floor(sl, process):
                raise PortError(
                    f"xfm_il: OD={OD}, W={W}, PITCH={PITCH}, "
                    f"q-shift={leg1_dy} um on M{sl}: the bridge endpoint "
                    f"pad would need trimming to {far_pad_len} um to clear "
                    f"the intermediate ring OD={cor_od}'s inner chamfer by "
                    f"{floor_sp} um -- the turn is too small to host the "
                    f"bridge landing; increase OD or reduce W/NT"
                )
    cell.inst(
        base_xfm_cross(
            WI=cross_gap,
            WO=W,
            S=0.0,
            TOP_ME=sl,
            BTM_ME=leg1,
            process=process,
            far_pad_length=far_pad_len,
        ),
        (-OD / 2.0, leg1_dy),
        "R0",
    )
    cell.inst(
        base_xfm_cross(
            WI=cross_gap,
            WO=W,
            S=0.0,
            TOP_ME=sl,
            BTM_ME=_metal_index(LEG2_BTM_ME),
            viat=False,
            viad=False,
            process=process,
            # MY-mirrored: its inner-ring pad is the near one.
            near_pad_length=far_pad_len,
        ),
        (-OD / 2.0 + PITCH + W, -leg1_dy),
        "MY",
    )
    return cell


def _il_staggered_ring_turns(
    cell: Cell,
    *,
    role: str,
    OD: float,
    W: float,
    OPENING: float,
    S: float,
    NT: int,
    TOP_ME: str,
    DUMMYL: str,
    process: ProcessRuleContext | None,
    PITCH: float,
    LEG2_BTM_ME: str,
    lane_offset: float,
    outer_escape_offset: float,
    chamfer_biases=None,
    corridor_bias_step: int = 0,
    partner_outer_offset: float = 0.0,
) -> tuple[float, float]:
    """xfm_il-only alternating ring kernel with symmetric P/S lane offsets.

    Transition ``k`` joins winding turn k to k+1.  Every P transition uses
    ``-q/+q`` except that P's outer transition moves inward by its separate
    rule-derived escape offset, and P's turn-1 transition -- the inner
    partner of S's OUTER bridge in the colliding pair (S_0, P_1) -- takes
    ``-max(q, partner_outer_offset)`` so that pair stays symmetric about
    its corridor (design-region issue 05: a symmetric pair's SL-1
    pad-corner clearance is S/sqrt(2) whatever q is, but an asymmetric one
    trails by (e - q)/sqrt(2) -- the W8 S3 truth-guard refusal at 0.795
    um).  S uses ``+q/-q`` for its colliding transitions; its outer
    transition uses whichever is larger of that lane offset and its escape
    offset -- the very value the caller hands P as
    ``partner_outer_offset`` -- while its non-colliding innermost
    transition remains centred.  Every moved leg carries its SL endpoint
    and landing with it, so the source/destination ring openings become
    ``facing-leg1_dy`` and ``facing+leg1_dy`` respectively.
    """
    del DUMMYL  # fidelity-only parameter, matching the shared kernel
    if role not in ("P", "S"):
        raise PortError(f"xfm_il: unknown staggered winding role {role!r}")
    pitch = PITCH
    facing = cross_endpoint_offset(
        W, pitch - W, _metal_index(TOP_ME), process
    )
    shifts = []
    for k in range(NT - 1):
        if role == "P":
            if k == 0:
                shifts.append(+outer_escape_offset)
            elif k == 1:
                shifts.append(-max(lane_offset, partner_outer_offset))
            else:
                shifts.append(-lane_offset)
        else:
            shifts.append(
                +max(lane_offset, outer_escape_offset)
                if k == 0
                else (+lane_offset if k <= NT - 3 else 0.0)
            )

    source_openings = [facing - shift for shift in shifts]
    dest_openings = [facing + shift for shift in shifts]
    for k, (source_open, dest_open) in enumerate(
        zip(source_openings, dest_openings)
    ):
        source_od = OD - 2.0 * k * pitch
        dest_od = source_od - 2.0 * pitch
        for label, opening, ring_od in (
            ("source", source_open, source_od),
            ("destination", dest_open, dest_od),
        ):
            available = max_opening(ring_od, W)
            if opening < GRID_UM - _EPS or opening > available + _EPS:
                raise PortError(
                    f"xfm_il: {role} transition {k}->{k + 1}, W={W}, "
                    f"S={S}, q={lane_offset:.3f} um needs {label} bridge "
                    f"opening {opening:.3f} um, but turn OD={ring_od:.3f} "
                    f"allows [{GRID_UM:.3f}, {available:.3f}] um; increase "
                    "OD/S or reduce W (lane target is never relaxed)"
                )

    if chamfer_biases is None:
        # Interleaved windings: the caller (xfm_il) computes the staircase
        # over the MERGED physical radial order; a bare standalone call
        # keeps the legacy geometry.
        chamfer_biases = [0] * NT

    def shifted_turn(k: int, lop: float, rop: float, under: bool = True) -> Cell:
        return _il_shifted_hud_cross(
            OD=OD - 2.0 * k * pitch,
            W=W,
            S=S,
            TOP_ME=TOP_ME,
            LEG2_BTM_ME=LEG2_BTM_ME,
            PITCH=pitch,
            LOP=lop,
            ROP=rop,
            leg1_dy=shifts[k] if under else 0.0,
            process=process,
            under=under,
            chamfer_bias=chamfer_biases[k],
            corridor_bias_step=corridor_bias_step,
        )

    # Preserve _ind_ring_turns' established hierarchy/order: middle turns,
    # outer turn, innermost closure.  Existing topology probes deliberately
    # use that ordering to split S's own winding from its later escape.
    for k in range(1, NT - 1):
        cell.inst(
            shifted_turn(k, source_openings[k], dest_openings[k - 1]),
            (0.0, 0.0),
            "R0" if k % 2 == 0 else "MY",
        )
    cell.inst(
        shifted_turn(0, source_openings[0], OPENING),
        (0.0, 0.0),
        "R0",
    )
    incoming = dest_openings[-1]
    inner_od = OD - 2.0 * (NT - 1) * pitch
    if NT % 2 == 0:
        cell.inst(
            _il_shifted_hud_cross(
                OD=inner_od,
                W=W,
                S=S,
                TOP_ME=TOP_ME,
                LEG2_BTM_ME=LEG2_BTM_ME,
                PITCH=pitch,
                LOP=incoming,
                ROP=0.0,
                leg1_dy=0.0,
                process=process,
                under=False,
                chamfer_bias=chamfer_biases[NT - 1],
            ),
            (0.0, 0.0),
            "R0",
        )
    else:
        cell.inst(
            base_oct(
                OD=inner_od,
                W=W,
                LOP=0.0,
                ROP=incoming,
                MET=_metal_index(TOP_ME),
                process=process,
                chamfer_bias=chamfer_biases[NT - 1],
            ),
            (0.0, 0.0),
            "R0",
        )
    return pitch, facing


def _il_primary_coil(
    *,
    OD: float,
    W: float,
    OPENING: float,
    LEAD: float,
    S: float,
    NT: int,
    TOP_ME: str,
    DUMMYL: str,
    dummy: bool,
    process: ProcessRuleContext | None,
    PITCH: float,
    LEG2_BTM_ME: str,
    lane_offset: float,
    outer_escape_offset: float,
    chamfer_biases=None,
    corridor_bias_step: int = 0,
    partner_outer_offset: float = 0.0,
) -> Cell:
    """xfm_il P winding with ind_sym's leads/ports and private ring kernel."""
    params = {
        "OD": OD,
        "W": W,
        "OPENING": OPENING,
        "LEAD": LEAD,
        "S": S,
        "NT": NT,
        "TOP_ME": TOP_ME,
        # Real leg1 conductor, not a bare `TOP_ME - 1` (gdsfactory review
        # 2026-09-21); this is metadata only (see base_ind_hud_cross's own
        # dead-BTM_ME note) but should still name the real plane.
        "BTM_ME": str(_metal_below(_metal_index(TOP_ME), process, levels=1)),
        "PITCH": PITCH,
        "LEG2_BTM_ME": LEG2_BTM_ME,
        "bridge_lane_offset_um": lane_offset,
        "bridge_escape_offset_um": outer_escape_offset,
        "bridge_pair_offset_um": max(lane_offset, partner_outer_offset),
    }
    if process is not None:
        params["process"] = process.profile_id
    cell = Cell(
        f"ind_sym_OD{OD}_W{W}_O{OPENING}_L{LEAD}_S{S}_NT{NT}",
        "ind_sym",
        params,
    )
    _il_staggered_ring_turns(
        cell,
        role="P",
        OD=OD,
        W=W,
        OPENING=OPENING,
        S=S,
        NT=NT,
        TOP_ME=TOP_ME,
        DUMMYL=DUMMYL,
        process=process,
        PITCH=PITCH,
        LEG2_BTM_ME=LEG2_BTM_ME,
        lane_offset=lane_offset,
        outer_escape_offset=outer_escape_offset,
        chamfer_biases=chamfer_biases,
        corridor_bias_step=corridor_bias_step,
        partner_outer_offset=partner_outer_offset,
    )
    # P1/N1 register on base_lead_pair's own two base_lead legs (port
    # contract 2026-09-21) -- the same far-tip default xfm_bs/ind_sym's own
    # migrated base_lead_pair calls use; the independent
    # x_um=OD/2.0+LEAD macro sum this replaced no longer feeds add_emx_port
    # here.
    sl = _metal_index(TOP_ME)
    pin = _pin(sl, process)
    cell.inst(
        base_lead_pair(
            LEAD=LEAD,
            W=W,
            OPENING=OPENING,
            P1TXT="P1",
            TOP_ME=TOP_ME,
            LEAD_ME=TOP_ME,
            dummy=dummy,
            process=process,
            port_metal=sl,
            port_label_layer=pin,
        ),
        (OD / 2.0, 0.0),
        "R0",
    )
    _heal_seam_notches(cell, process)
    return cell


def _il_bridge_spacing_guard(
    pri: Cell,
    sec: Cell,
    sl: int,
    process: ProcessRuleContext | None,
) -> None:
    """Fail closed on P/S bridge-layer min-space after all landing stacks."""
    if process is None:
        return
    # Real leg1/leg2 conductors, not bare `sl - 1`/`sl - 2` (gdsfactory
    # review 2026-09-21); the min-space lookup now goes through
    # ``_min_met_spacing`` (already ValueError->PortError-safe) instead of
    # a second, unwrapped ``process.adapter.metal_rule()`` call.
    for met in (_metal_below(sl, process, levels=1),
                _metal_below(sl, process, levels=2)):
        name = _metal_name(met)
        space = _min_met_spacing(met, process, None)
        layer = _metal(met, process)
        p_region = _il_ct_region(pri.flat_shapes(), layer)
        s_region = _il_ct_region(sec.flat_shapes(), layer)
        findings = p_region.separation_check(
            s_region, _nm(space), False, kdb.Metrics.Euclidian
        )
        if findings.count():
            raise PortError(
                f"xfm_il: staggered P/S bridges violate {name} min_space "
                f"{space:.3f} um after grid snapping ({findings.count()} "
                "finding(s)); adjust OD/W/S/OPENING (lane target is never "
                "relaxed)"
            )


def xfm_il(
    OD: float,
    W: float,
    S: float,
    NT_P: int,
    NT_S: int,
    OPENING_P: float,
    OPENING_S: float,
    LEAD_P: float,
    LEAD_S: float,
    SL_ME: str = "9",
    CT_P_ME: str | None = None,
    CT_S_ME: str | None = None,
    dummy: bool = True,
    DUMMYL: str = "RFVLSI",
    port_order: list[str] | None = None,
    ground_fixture: GroundFixtureConfig | None = None,
    process: ProcessRuleContext | None = None,
) -> Cell:
    """Same-layer interleaved transformer: P and S alternate radial bands on
    one metal plane SL_ME (ring pitch ``2*(W+S)``, shared W/S -- unequal
    winding widths are out of scope, see the ticket spec) with each turn's
    crossunder split across SL_ME-1 (leg1) and SL_ME-2 (leg2). Counting
    radial bands from one at the outside, P occupies 1/3/5/... (zero-based
    lattice indices are even; outer radius ``OD/2 - k*2*(W+S)``) and S
    occupies 2/4/6/... (zero-based indices are odd; outer radius
    ``OD/2-(W+S) - k*2*(W+S)``, i.e. an ind_sym-style
    winding built at ``OD_S = OD - 2*(W+S)`` -- the radius derivation in the
    ticket 01 handoff, corrected there from an earlier ``OD - (W+S)`` guess
    that would only offset by half the band pitch and self-overlap when
    W > S).

    Topology (ticket 02d plus ticket 06's spacing repair): both windings keep
    the native alternating left/right zigzag on doubled
    ``PITCH=2*(W+S)`` and keep leg1 on SL_ME-1 / leg2 on SL_ME-2. Ticket 02d
    proved only that P/S polygons did not INTERSECT; ticket 06 found the
    adjacent 2->4 and 3->5 diagonal pairs could still sit below min-space.
    ``_il_bridge_lane_offset`` now sizes a common symmetric stagger from the
    actual snapped W/S diagonal outlines. Its target is S in reference mode,
    or the maximum of S and every applicable base/wide-parallel rule on
    SL_ME-1/SL_ME-2 in process mode. For every colliding pair, S's even-band
    bridge takes ``+q/-q`` on leg1/leg2 while P's odd-band bridge takes the
    opposite; q is split equally about the old y=0 corridor. The bridge,
    endpoint via landing, and matching SL opening move together, so the
    family ``base_xfm_cross`` junction remains flush. NT=2 has no
    same-half-plane bridge pair and therefore keeps the internal q=0.
    Design-region issue 05 (2026-08-05): the OUTER colliding pair
    (S_0, P_1) is the one place the stagger is not q -- S_0's shift is
    ``max(q, escape_s)`` (see below), so P_1 mirrors that same value
    (``bridge_pair_offset_um``) instead of q. Every colliding pair is
    therefore symmetric about its corridor, which is what keeps the SL-1
    pad rectangle of each leg2 via stack S/sqrt(2) clear of the partner's
    SL-1 diagonal; an asymmetric pair trails by (e - q)/sqrt(2) -- 0.795
    um at W8 S3, the ``_il_bridge_spacing_guard`` refusal that had closed
    W8/W10 for every NT>=3.

    P still has ind_sym's port-right lead pair and semantic ports, but its
    rings use the private ``_il_staggered_ring_turns`` composition so the
    internal bridges can receive those derived offsets without changing
    public ``ind_sym``/``base_ind_hud_cross`` behavior or signatures. P is
    always the outermost band on every level, so its straight lead pair never
    crosses another band outward. S is built with the same private bare-ring
    kernel in a local "opens right" frame, and its P2/N2 leads escape via
    ``_balun_crossunder``
    (xfm_balun's nested-secondary crossunder mechanism, reused verbatim --
    including issue 04's ``tip_pad_width`` corridor trim, here against P's
    OUTERMOST ring (design-region issue 05) --
    anchored on that same local ``+OD_S/2`` arm: S's outermost band always
    has exactly ONE band -- P's outermost -- between it and open space,
    precisely the situation that helper already solves generically). The
    whole S sub-cell -- rings, bridges AND escape together -- is then
    instanced with ``MY`` (not ticket 02's ``R90``) so it mirrors onto the
    GLOBAL half-planes: local ``+OD_S/2`` (port + escape) -> global LEFT
    (opposite P's port).

    Because the reference zigzag never mirrors the OUTERMOST turn of either
    winding (``_ind_ring_turns`` always instances it ``"R0"``), that turn's
    own bridge is -- for BOTH P (unmirrored) and S (whole-cell MY-mirrored)
    -- always on the SAME half-plane as the OTHER winding's lead/escape
    channel: P's outermost bridge at global left (same as S's port+escape,
    also left after the mirror); S's outermost bridge, local ``-OD_S/2``,
    lands at global RIGHT after the mirror (same as P's own lead channel,
    also right). ``_il_bridge_escape_lane_offset`` derives the smallest
    independent P/S outer-pad shifts from W, both OPENING values, and the
    applicable base/wide-parallel spacing. The outer bridge, its via landing,
    and its SL opening move together; ``_check_bridge_escape_clearance`` then
    verifies the repaired gap before the generic ``_xfm_net_short`` gate.

    CT_P_ME / CT_S_ME (ticket 03c, both default ``None`` -> byte-identical:
    every new code path below is gated behind ``is not None`` and adds
    nothing -- no shape, no label, no params key -- when left at the
    default) optionally add a true electrical-midpoint center tap per
    winding, built by ``_il_ct_tap_exact`` (see its docstring for the full
    symmetry argument) at a FIXED position -- never offset (ticket 03b's
    tangential-offset compromise was itself rejected on a follow-up
    Virtuoso review: the user correctly read a rendered 03b sample's small
    y-offset as "the true midpoint, minus a workaround" and asked for the
    alternative 03b's own docstring already named but did not take).
    CTP taps P's own innermost ring directly in the global P frame, exit
    side = NT_P parity (odd -> global LEFT, same side as S's
    port+escape; even -> global RIGHT, same side as P's own P1/N1). CTS
    taps S's innermost ring in S's *local* pre-mirror frame, exit side =
    NT_S parity there, THEN FLIPPED by the whole-cell MY mirror ticket
    02d already applies (MY flips x): local odd (LEFT) -> global RIGHT
    (P1/N1's side); local even (RIGHT) -> global LEFT (S's own
    port+escape side). Because ticket 03c also requires NT_P==NT_S (see
    the guards below), CTP and CTS always share the SAME local parity
    rule but CTS's own side is always the mirror-flipped OPPOSITE of
    CTP's -- so the two taps structurally land on OPPOSITE global sides
    in every legal configuration, never the same one. Both taps reuse
    their own winding's LEAD_P/LEAD_S for the tap lead's reach beyond the
    ring (family precedent: xfm_bs's ``_bs_center_tap`` and xfm_balun's
    ``_add_balun_ct`` both reuse the winding's own LEAD rather than adding
    a dedicated CT-lead-length parameter).

    **Direction (ticket 03c, replacing 03b's tangential search):** CT_ME
    compared to SL_ME picks UP or DOWN (``_il_ct_metal_guard``, called
    below before any geometry is built). CT_ME ABOVE SL_ME taps upward --
    the via stack runs SL_ME->CT_ME and the CT lead runs on CT_ME over
    every ring this device ever draws; nothing else is ever drawn above
    SL_ME, so an upward tap is NEVER blocked and has no adjacency floor.
    CT_ME BELOW SL_ME taps downward exactly as ticket 03 defined it (CT <=
    SL_ME-3, ``_il_ct_adjacency_guard``, promoting ``_ind_ct_adjacency_
    guard``'s two-layer N1 rule to three layers since ticket 02d's
    crossunder occupies BOTH SL_ME-1 and SL_ME-2) -- but because the tap
    is now pinned to the exact midpoint with no offset available, a
    downward tap blocked by the OTHER winding's crossunder there (the
    interleaved lattice places every P ring exactly between two S rings
    and vice versa, so this is common, not a corner case) fails closed BY
    NAME, suggesting an upward CT_ME instead (unless SL_ME is already
    "AP", the top of the stack, in which case downward is the body's only
    option and a block there is reported as that body's own real limit,
    not a bug). CT_ME == SL_ME is always illegal (the coil's own layer).
    Both taps are added to their winding's own sub-cell BEFORE
    ``_xfm_net_short`` runs, so the layer-complete short gate covers the
    full CT lead + via stack on every layer it touches (SL_ME, SL_ME-1,
    SL_ME-2 and CT_ME itself, in EITHER direction), not merely the
    winding rings. Port order: the fixed base [P1,N1,P2,N2] then
    whichever of CTP/CTS is enabled, appended in that order
    (``_xfm_order_ports``, the same family contract xfm_bs/xfm_ms/
    xfm_balun already use) -- the function's own optional ``port_order``
    override is sized to match (4, 5 or 6 entries depending on which CT
    taps are enabled).

    Guards (fail closed, every message names the offending rule/value):
    SL_ME needs at least two metals below it (SL_ME-1 for leg1, SL_ME-2 for
    leg2); CT_P_ME/CT_S_ME (if given) direction-dispatched by
    ``_il_ct_metal_guard`` (see above: downward <= SL_ME-3, upward
    unrestricted, equal-to-SL_ME always illegal); NT_P>=2 (a single
    interleaved turn has no crossover to interleave -- xfm_bs/xfm_balun
    already cover 1+1 same-layer windings); NT_P==NT_S (ticket 03c, user
    directive: primary and secondary turn counts must be EQUAL -- tightens
    ticket 01's original ``|NT_P-NT_S|<=1``);
    OPENING_P/OPENING_S each against their own winding's ``max_opening`` (P
    at OD, S at OD_S -- the same bound ``ind_sym``/``_bs_winding``/
    ``_ci_winding`` already enforce, applied per-winding since P and S have
    different effective OD); the ``_check_winding_fit`` per winding (an
    NT>=3 winding's innermost facing-bearing turn must still have room for
    the crossunder-facing OPENING -- ticket 01 comment (2));
    ``_check_bridge_escape_clearance`` each half-axis (see above); and
    ``_xfm_net_short`` unconditionally (every drawn layer -- now SL_ME,
    SL_ME-1, SL_ME-2 and, when enabled, the CT_ME(s) in either direction
    too -- shared with xfm_bs/xfm_ms/xfm_balun). On N28, every body builds
    under geometric-only enforcement (n28-rules-slim, user directive
    2026-07-19: N28 no longer separately restricts any VIA1..VIA7 level,
    so no via-crossing CT metal is N28-specific-rejected any more -- only
    this function's own direction/adjacency guards and the exact-midpoint
    blocked-window check ever reject a CT metal now): SL_ME="9" can tap
    downward to its SL_ME-3 floor (CT_ME="6") or upward to "10"/"AP";
    SL_ME="10" downward to "7" or upward to "AP"; SL_ME="AP" has no metal
    above it, so only downward (to its own SL_ME-3 floor "8") is possible.

    **A downward CTP is structurally blocked for EVERY legal
    configuration** (proven by sweep, see ``_il_ct_tap_exact``'s
    docstring for the geometric reason), so in practice CTP needs an
    UPWARD CT_P_ME; on an SL_ME="AP" body specifically -- no metal exists
    above "AP" -- CTP therefore cannot be tapped in EITHER direction at
    all, a genuine architectural limit of that body (not a bug, and not
    tunable via OD/OPENING/S). CTS's downward tap, by contrast, is
    structurally CLEAR for every legal configuration (S is always this
    device's overall innermost winding), so CT_S_ME works downward on
    every SL_ME body including "AP"."""
    sl = _metal_index(SL_ME)
    # Real profile-stack depth, not a bare `sl < 3` (gdsfactory review
    # 2026-09-21): leg1/leg2 are the real conductors 1/2 levels below SL_ME
    # (``_metal_below``) -- on N65+AP that is M9/M8 (N65 has no M10 between
    # M9 and AP). Resolved once here and reused by every leg1/leg2 call
    # site below instead of re-deriving `sl - 1`/`sl - 2`.
    try:
        leg1, leg2 = (_metal_below(sl, process, levels=1),
                      _metal_below(sl, process, levels=2))
    except PortError as exc:
        raise PortError(
            f"xfm_il: SL_ME {_metal_name(sl)} needs at least two metals "
            f"below it (SL_ME-1 for the crossunder's leg1, SL_ME-2 for "
            f"leg2 -- ticket 02d's dual-layer bridge legs): {exc}"
        ) from exc
    if CT_P_ME is not None:
        _il_ct_metal_guard("P", SL_ME, CT_P_ME, process=process)
    if CT_S_ME is not None:
        _il_ct_metal_guard("S", SL_ME, CT_S_ME, process=process)
    if NT_P < 2:
        raise PortError(
            f"xfm_il: NT_P={NT_P} is not multi-turn; a single interleaved "
            f"turn has no crossover to interleave against -- use xfm_bs or "
            f"xfm_balun for 1+1 same-layer windings"
        )
    if NT_P != NT_S:
        raise PortError(
            f"xfm_il: NT_P={NT_P} != NT_S={NT_S}; primary and secondary "
            f"turn counts must be EQUAL (ticket 03c, user directive: a "
            f"differential center-tapped winding needs symmetric turn "
            f"counts on both sides, and interleaved bands alternate P/S "
            f"one-for-one)"
        )
    pitch = 2.0 * (W + S)
    OD_S = OD - pitch
    if OD_S <= 0:
        raise PortError(
            f"xfm_il: secondary effective OD={OD_S} (OD - 2*(W+S)) is not "
            f"positive for OD={OD}, W={W}, S={S}; increase OD or reduce W/S"
        )
    _check_opening(OD, W, OPENING_P, "xfm_il P")
    _check_opening(OD_S, W, OPENING_S, "xfm_il S")
    _check_winding_fit(OD, W, S, NT_P, pitch, SL_ME, process, "xfm_il P")
    _check_winding_fit(OD_S, W, S, NT_S, pitch, SL_ME, process, "xfm_il S")
    # ticket 02d: the reference zigzag never mirrors either winding's
    # OUTERMOST turn (_ind_ring_turns always instances it "R0"), so that
    # turn's own bridge always lands on the SAME half-plane as the OTHER
    # winding's lead/escape channel -- P's outermost bridge at global left
    # (same as S's port+escape, also left after S's whole-cell MY mirror)
    # and S's outermost bridge (local -OD_S/2) at global right after the
    # mirror (same as P's own lead channel, also right). Unchanged by the
    # 02c->02d bridge_side switch: driven entirely by the fixed "R0"
    # outermost-turn placement both modes share.
    if NT_P >= 3:
        lane_offset, lane_target = _il_bridge_lane_offset(W, S, sl, process)
    else:
        # With two turns the sole P bridge and sole S bridge live on
        # opposite half-planes, so there is no chained-bridge pair to lane.
        lane_offset = 0.0
        lane_target = _il_bridge_target_gap(W, S, sl, process)
    escape_p, escape_s = _il_bridge_escape_lane_offset(
        W, S, sl, process, OPENING_P, OPENING_S
    )
    # S's OUTER bridge shift: escape-driven whenever P's lead channel is
    # tighter than the lane.  Design-region issue 05: that same value is
    # what P's turn-1 bridge -- S_0's partner in the colliding pair
    # (S_0, P_1) -- must mirror (``partner_outer_offset``), so the pair
    # stays symmetric about its corridor; NT=2 has no such pair.
    s_outer_shift = max(lane_offset, escape_s)
    pair_offset = s_outer_shift if NT_P >= 3 else 0.0
    _check_bridge_escape_clearance(
        W, S, leg1, process, OPENING_S, escape_p,
        "xfm_il", "P", "S",
    )
    _check_bridge_escape_clearance(
        W, S, leg1, process, OPENING_P, s_outer_shift,
        "xfm_il", "S", "P",
    )

    params = {
        "OD": OD, "W": W, "S": S, "NT_P": NT_P, "NT_S": NT_S,
        "OPENING_P": OPENING_P, "OPENING_S": OPENING_S,
        "LEAD_P": LEAD_P, "LEAD_S": LEAD_S, "SL_ME": SL_ME,
        "bridge_lane_offset_um": lane_offset,
        "bridge_lane_target_um": lane_target,
        "bridge_escape_offset_primary_um": escape_p,
        "bridge_escape_offset_secondary_um": escape_s,
        "bridge_pair_offset_um": pair_offset,
    }
    if CT_P_ME is not None:
        params["CT_P_ME"] = CT_P_ME
    if CT_S_ME is not None:
        params["CT_S_ME"] = CT_S_ME
    if process is not None:
        params["process"] = process.profile_id
    cell = Cell(
        f"xfm_il_OD{OD}_W{W}_S{S}_NTP{NT_P}_NTS{NT_S}_{SL_ME}",
        "xfm_il", params,
    )

    # Primary keeps ind_sym's public lead/port contract, but xfm_il now owns
    # its ring kernel privately: colliding odd-band bridges must take -q/+q
    # on SL-1/SL-2 while S's even-band bridges take the exact opposite.  The
    # shared ind_sym/base_ind_hud_cross public geometry remains byte-stable.
    leg2_me = str(leg2)
    # Interleaved bands alternate P/S one-for-one at a physical radial
    # step of (W+S), so the chamfer staircase must be computed over the
    # MERGED radial order (P turn k = physical ring 2k, S turn k =
    # physical ring 2k+1) -- each winding's own kernel only ever sees
    # every OTHER ring (see chamfer_staircase_delta).
    merged_ods = [OD - 2.0 * m * (W + S) for m in range(NT_P + NT_S)]
    cb_delta = chamfer_staircase_delta(merged_ods, W, sl, process)
    p_biases = [cb_delta * (2 * k) for k in range(NT_P)]
    s_biases = [cb_delta * (2 * k + 1) for k in range(NT_S)]
    pri = Cell("xfm_il_pri", "xfm_il_pri", {})
    p_coil = _il_primary_coil(
        OD=OD,
        W=W,
        OPENING=OPENING_P,
        LEAD=LEAD_P,
        S=S,
        NT=NT_P,
        TOP_ME=SL_ME,
        DUMMYL=DUMMYL,
        dummy=dummy,
        process=process,
        PITCH=pitch,
        LEG2_BTM_ME=leg2_me,
        lane_offset=lane_offset,
        outer_escape_offset=escape_p,
        chamfer_biases=p_biases,
        corridor_bias_step=cb_delta,
        partner_outer_offset=pair_offset,
    )
    pri.inst(p_coil, (0.0, 0.0), "R0")
    # (port contract 2026-09-21) p_coil's own P1/N1 (registered on its
    # base_lead_pair legs) travel through this cell.inst() automatically --
    # xfm_il's own finalize_emx_ports() call (see its tail) walks the whole
    # cell.inst() chain and finds them regardless of nesting depth, so no
    # transplant here any more.

    # secondary: bare ring (_ind_ring_turns, no lead pair, same native
    # alternating zigzag and same LEG2_BTM_ME=SL_ME-2 dual-layer-leg fix as
    # P) in its own local "opens right" frame + a crossunder escape past P's
    # outer band, then MIRRORED (not ticket 02's R90) as a whole: local
    # right -> global left, so S's port and escape land on the LEFT
    # half-plane (opposite P's right) along with S's own (mirrored) bridges.
    s_local = Cell("xfm_il_sec_local", "xfm_il_sec_local", {})
    _il_staggered_ring_turns(
        s_local,
        role="S",
        OD=OD_S,
        W=W,
        OPENING=OPENING_S,
        S=S,
        NT=NT_S,
        TOP_ME=SL_ME,
        DUMMYL=DUMMYL,
        process=process,
        PITCH=pitch,
        LEG2_BTM_ME=leg2_me,
        chamfer_biases=s_biases,
        lane_offset=lane_offset,
        outer_escape_offset=escape_s,
        corridor_bias_step=cb_delta,
    )
    # Escape-tip corridor trim (design-region issue 05, xfm_il's twin of
    # xfm_balun's issue-04 trim -- previously never passed, hence the
    # NT=2 W6 S2 OD120 dirty build on every metal system): S's P2/N2
    # escape lands its arm-tip via on S's outermost arm at x = OD_S/2,
    # right where P's OUTERMOST ring's inner chamfer passes (that ring
    # heads the merged staircase, so its bias p_biases[0] is 0).
    tip_w = _escape_tip_pad_width(
        ring_od=OD, ring_bias=p_biases[0], ring_w=W, tip_w=W,
        x_tip=OD_S / 2.0, opening=OPENING_S, me=sl, process=process,
        where=f"xfm_il: OD={OD}, OD_S={OD_S}, W={W}, OPENING_S={OPENING_S}",
        corridor="primary outermost ring's chamfer corridor",
        advice="increase OD or reduce OPENING_S/W")
    _balun_crossunder(s_local, 0.0, OD_S, W, OPENING_S, LEAD_S,
                      0.0, OD, sl, leg1, S, "P2", "N2", process,
                      tip_pad_width=tip_w)
    _heal_seam_notches(s_local, process)

    # ticket 03c: BOTH windings' bare geometry (rings + bridges + escape,
    # already healed) exists above BEFORE either CT tap is placed -- a
    # DOWNWARD tap's exact-midpoint check (_il_ct_tap_exact) needs the
    # OTHER winding's already-built SL_ME-1/SL_ME-2 regions, in the tapped
    # winding's OWN frame (an UPWARD tap never uses them, but they are
    # cheap to compute unconditionally here, matching ticket 03b's own
    # pattern). P is built directly in the global frame (no transform); S
    # is built in its own LOCAL pre-mirror frame, later mirrored MY into
    # `sec`. So: CTP (global P frame) needs S's legs MIRRORED into the
    # global frame; CTS (S's local frame) needs P's legs mirrored INTO
    # that same local frame -- MY is its own inverse, so mirroring P's
    # already-global legs once yields exactly their local-frame
    # appearance from S's side.
    sl1_layer, sl2_layer = _metal(leg1, process), _metal(leg2, process)
    if CT_P_ME is not None:
        # CTP: exact-midpoint tap on P's own winding (OD, NT_P, LEAD_P),
        # exit side = NT_P parity (odd -> left, even -> right, native
        # _ind_ct_tap intent -- see _il_ct_tap_exact's docstring). Added
        # directly to `pri` (P's global frame) AFTER p_coil's own heal,
        # matching ind_sym's own "heal before CT" ordering (p_coil already
        # healed itself internally).
        s_sl1_global = _il_ct_region(s_local.flat_shapes(), sl1_layer, mirror=True)
        s_sl2_global = _il_ct_region(s_local.flat_shapes(), sl2_layer, mirror=True)
        _il_ct_tap_exact(pri, s_sl1_global, s_sl2_global, "P",
                         OD=OD, W=W, LEAD=LEAD_P, NT=NT_P, SL_ME=SL_ME,
                         CT_ME=CT_P_ME, port_name="CTP", process=process,
                         PITCH=pitch)
    if CT_S_ME is not None:
        # CTS: exact-midpoint tap, built in S's LOCAL pre-mirror frame on
        # S's own effective winding (OD_S, NT_S, LEAD_S); exit side there
        # = NT_S parity, THEN flipped globally by the whole-cell MY mirror
        # below (local left -> global right and vice versa -- xfm_il's own
        # docstring documents the resulting global side). Added after
        # s_local's own heal (same "heal before CT" ordering as P above);
        # its EMX port travels through sec.inst(s_local, (0,0), "MY") the
        # same integer transform chain shapes/labels use (port contract
        # 2026-09-21), identically to P2/N2 -- no separate mirror step
        # needed.
        p_sl1_local = _il_ct_region(p_coil.flat_shapes(), sl1_layer, mirror=True)
        p_sl2_local = _il_ct_region(p_coil.flat_shapes(), sl2_layer, mirror=True)
        # CTP (if enabled) already built into `pri`, on CT_P_ME -- when
        # CT_S_ME resolves to the SAME metal, CTS's own lead run must also
        # dodge CTP's already-drawn lead there (see _il_ct_tap_exact's
        # docstring: both leads typically span most of the device, so a
        # shared CT metal risks a genuine tap-to-tap short, found in
        # ticket 03b on an AP-body CT_P_ME=CT_S_ME="8" build -- ticket
        # 03c's own equal-turns constraint now keeps CTP/CTS on opposite
        # global sides always, so this is a defensive backstop rather
        # than an expected-to-fire check). Mirrored into S's local frame
        # exactly like the SL_ME-1/SL_ME-2 regions above.
        other_ct_lead = None
        if (CT_P_ME is not None
                and _metal_index(CT_S_ME) == _metal_index(CT_P_ME)):
            ct_layer = _metal(_metal_index(CT_P_ME), process)
            other_ct_lead = _il_ct_region(pri.flat_shapes(), ct_layer,
                                          mirror=True)
        _il_ct_tap_exact(s_local, p_sl1_local, p_sl2_local, "S",
                         OD=OD_S, W=W, LEAD=LEAD_S, NT=NT_S, SL_ME=SL_ME,
                         CT_ME=CT_S_ME, port_name="CTS", process=process,
                         PITCH=pitch, other_ct_lead=other_ct_lead)
    sec = Cell("xfm_il_sec", "xfm_il_sec", {})
    sec.inst(s_local, (0.0, 0.0), "MY")
    # (port contract 2026-09-21) s_local's own ports (P2/N2 from
    # _balun_crossunder, CTS from _il_ct_tap_exact) travel through this
    # single cell.inst() the same MY-mirror integer transform its
    # shapes/labels already use -- replaces the old per-port mirror loop
    # that re-rounded an already-rounded label_xy_um through _ORIENTS["MY"]
    # a second time (mechanism (3)); xfm_il's own finalize_emx_ports() call
    # below composes the mirrored coordinate correctly regardless of
    # nesting depth.

    try:
        _xfm_net_short("xfm_il", pri, sec)
        _il_bridge_spacing_guard(pri, sec, sl, process)
    except PortError as exc:
        raise PortError(
            f"{exc} -- escape-corridor note (design-region-full-coverage): "
            f"at wide W the P outer bridge and the S escape share the left "
            f"half-plane; the escape bars sit at +-[OPENING_S, OPENING_S+W] "
            f"(currently OPENING_S={OPENING_S:.3f}, W={W:.3f}), so a "
            f"LARGER secondary opening usually unlocks the corridor before "
            f"any OD/W/S change is needed"
        ) from exc
    cell.inst(pri, (0.0, 0.0), "R0")
    cell.inst(sec, (0.0, 0.0), "R0")
    # (port contract 2026-09-21) the one composition point: walks the
    # cell.inst() chain down to every base_lead/base_lead_pair leg
    # (P1/N1/P2/N2, CTP/CTS) -- replaces the old
    # cell.emx_ports.extend(pri.emx_ports)/extend(sec.emx_ports).
    cell.emx_ports = finalize_emx_ports(cell)
    _xfm_order_ports(cell)
    base_order = ["P1", "N1", "P2", "N2"]
    if CT_P_ME is not None:
        base_order.append("CTP")
    if CT_S_ME is not None:
        base_order.append("CTS")
    order = port_order or base_order
    if len(order) != len(cell.emx_ports):
        raise PortError(
            f"xfm_il: port_order needs {len(cell.emx_ports)} entries "
            f"(got {len(order)})"
        )
    for q, name in zip(cell.emx_ports, order):
        q["name"] = name
        q["signal"] = name
    if ground_fixture is not None:
        add_ground_fixture(cell, ground_fixture, process)
    return cell
