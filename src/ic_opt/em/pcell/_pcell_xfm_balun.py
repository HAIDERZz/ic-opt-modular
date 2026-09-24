# Auto-split from pcell_inductor_port_clean.py (wrap-up P1,
# 2026-07-28): verbatim segment move, no behavior change. The
# facade module re-exports every name; see its docstring for
# provenance and the KNOWN_DEVIATIONS record.
from __future__ import annotations

import math

from ic_opt.em.pcell._pcell_core import (
    Cell,
    PortError,
    ProcessRuleContext,
    _effective_min_spacing,
    _metal_below,
    _metal_index,
    _metal_name,
    _pin,
    _xfm_order_ports,
    chamfer_staircase_delta,
    finalize_emx_ports,
    floortogrid,
    octagon,
)
from ic_opt.em.pcell._pcell_guards import (
    _check_opening,
    _heal_seam_notches,
    _xfm_net_short,
    _xfm_nets_are_drc_separate,
)
from ic_opt.em.pcell._pcell_ind_sym import (
    _compact_two_turn_winding,
    _needs_full_width_lead_landing,
    ind_sym,
)
from ic_opt.em.pcell._pcell_primitives import (
    _pad_trim_floor,
    base_ind_under,
    base_lead,
    base_lead_pair,
    base_xfm_half,
)
from ic_opt.em.pcell._pcell_xfm_bs import (
    _bs_center_tap,
    ct_lead_to_edge,
)
from ic_opt.em.pcell.fixture import (
    GroundFixtureConfig,
    _drawing_bbox_um,
    add_ground_fixture,
)
from ic_opt.em.pcell.stack import builds_on_profile_stack

# ---------------------------------------------------------------------------
# xfm_balun (classic same-layer coplanar balun; clean-room composition)
# ---------------------------------------------------------------------------


def _ci_winding(cell, center_x, OD, W, OPENING, LEAD, S, NT, side,
                BALUN_ME, p_txt, n_txt, process,
                direct_leads: bool = True,
                compact_pad_max: float | None = None,
                compact_candidate_qualifier=None,
                chamfer_bias: int = 0,
                port_spacing: float | None = None):
    """One coplanar balun winding on BALUN_ME.

    NT>=2 uses ind_sym (axis-aligned VIA crossover on BALUN_ME-1); NT==1 uses
    base_xfm_half R0+MX (degenerates to base_oct when not stacked). When
    direct_leads is True, also adds a lead pair + 2 EMX ports (mirrors
    _bs_winding for the NT==1 path). When False, draws the ring only — the
    caller supplies the escape leads + ports (nested crossunder)."""
    _check_opening(OD, W, OPENING, "_ci_winding")
    me = _metal_index(BALUN_ME)
    if NT >= 2:
        orient = "MY" if side == "left" else "R0"
        if process is not None and NT == 2:
            coil = _compact_two_turn_winding(
                OD=OD,
                W=W,
                OPENING=OPENING,
                LEAD=LEAD,
                S=S,
                top_met=me,
                port_order=[p_txt, n_txt],
                process=process,
                family="xfm_balun",
                max_pad_length=compact_pad_max,
                candidate_qualifier=compact_candidate_qualifier,
                # port contract 2026-09-21: this sub-winding's P1/N1 roles
                # are xfm_balun's OWN p_txt/n_txt, not ind_sym's -- see
                # ind_sym's semantic_port_roles docstring (the structural
                # replacement for this function's own deleted transplant
                # loop's "logical_name": q["name"] line).
                semantic_port_roles=False,
                port_spacing=port_spacing,
            )
        else:
            coil = ind_sym(
                OD=OD,
                W=W,
                OPENING=OPENING,
                LEAD=LEAD,
                S=S,
                NT=NT,
                TOP_ME=str(me),
                BTM_ME=str(me - 1),
                port_order=[p_txt, n_txt],
                process=process,
                semantic_port_roles=False,
                PORT_SPACING=port_spacing,
            )
        cell.inst(coil, (center_x, 0.0), orient)
        # (port contract 2026-09-21) coil's own ports (registered on its
        # leaf primitives, ind_sym/xfm_bs's own migrated base_lead_pair
        # calls) travel through this cell.inst() automatically, the same
        # integer chain shapes/labels already use -- xfm_balun's own
        # finalize_emx_ports() (see its tail) collects them from here, so
        # this branch no longer re-transplants a dict by hand (removes a
        # mechanism-3 float recompute: this used to re-round
        # center_x + coil's own already-rounded label_xy_um instead of
        # composing through cell.inst()). _ci_winding's OWN direct-lead
        # branch below is unchanged -- still the pre-contract API.
        return coil.params.get("compact_bridge_pad_length_um")
    lop, rop = (OPENING, 0.0) if side == "left" else (0.0, OPENING)
    half = base_xfm_half(OD=OD, WO=W, S=S, LOP=lop, ROP=rop, TOP_ME=me,
                         BTM_ME=me, via_to_next=False,
                         process=process, chamfer_bias=chamfer_bias)
    cell.inst(half, (center_x, 0.0), "R0")
    cell.inst(half, (center_x, 0.0), "MX")
    if not direct_leads:
        return None
    overlap_lead = _needs_full_width_lead_landing(
        cell,
        center_x=center_x,
        OD=OD,
        W=W,
        OPENING=OPENING,
        top_met=me,
        side=side,
        process=process,
    )
    # Both legs register on base_lead_pair's own two base_lead legs (port
    # contract 2026-09-21): each leg's far tip lands at the SAME
    # `center_x +/- OD/2 +/- LEAD` this call site used to recompute
    # independently -- overlap_lead's own +W-on-both-LEAD-and-origin shift
    # is an algebraic wash at the far tip (identical to _bs_winding's own
    # migrated proof), so the far-tip default reproduces it exactly
    # regardless of overlap_lead.
    pin = _pin(me, process)
    if side == "left":
        cell.inst(base_lead_pair(W=W, OPENING=OPENING,
                                 LEAD=LEAD + W if overlap_lead else LEAD,
                                 TOP_ME=str(me), LEAD_ME=str(me),
                                 P1TXT=p_txt, N1TXT=n_txt, process=process,
                                 port_metal=me, port_label_layer=pin,
                                 PORT_SPACING=port_spacing),
                  (center_x - OD / 2 + W if overlap_lead
                   else center_x - OD / 2, 0.0), "MY")
    else:
        cell.inst(base_lead_pair(W=W, OPENING=OPENING,
                                 LEAD=LEAD + W if overlap_lead else LEAD,
                                 TOP_ME=str(me), LEAD_ME=str(me),
                                 P1TXT=p_txt, N1TXT=n_txt, process=process,
                                 port_metal=me, port_label_layer=pin,
                                 PORT_SPACING=port_spacing),
                  (center_x + OD / 2 - W if overlap_lead
                   else center_x + OD / 2, 0.0), "R0")
    _heal_seam_notches(cell, process)
    return None


def _escape_tip_pad_width(*, ring_od, ring_bias, ring_w, tip_w, x_tip,
                          opening, me, process, where, corridor, advice):
    """Corridor-trimmed tangential width of a nested escape's arm-tip pad.

    Design-region issue 04 (xfm_balun) / issue 05 (xfm_il): the
    crossunder's arm-tip end via lands on the inner winding's arm at
    ``x_tip`` right where the ENCLOSING ring's inner 45-degree chamfer
    passes; at small OD that arm's flat is shorter than the ``tip_w``-wide
    pad, so the pad's outer corner ``(x_tip, opening + tip_w)`` would cross
    the corridor line ``x + y = ring_od/2 + BA - C - W`` (same quantized
    helpers as ``base_oct_quad``; see ``base_ind_hud_cross``) by less than
    the effective spacing floor.  ``ring_bias`` is the enclosing ring's
    chamfer-staircase bias in manufacturing-grid steps.

    Returns ``None`` when the full ``tip_w`` already clears (the caller
    keeps its byte-identical untrimmed pad), else the trimmed width; fails
    closed below the metal's own min_width (``_pad_trim_floor``) with a
    message built from ``where``/``corridor``/``advice``.  Reference mode
    (``process=None``) never trims."""
    if process is None:
        return None
    floor_sp = _effective_min_spacing(me, max(ring_w, tip_w), process)
    d_line = octagon(ring_od, ring_w, ring_bias).inner_chamfer_intercept
    allow = d_line - floor_sp * math.sqrt(2.0) - x_tip - opening
    if allow >= tip_w - 1e-9:
        return None
    trimmed = floortogrid(allow)
    if trimmed < _pad_trim_floor(me, process):
        raise PortError(
            f"{where}: the secondary escape's arm-tip pad would need "
            f"trimming to {trimmed} um to clear the {corridor} by "
            f"{floor_sp} um; {advice}"
        )
    return trimmed


def _balun_crossunder(cell, x_in, OD_in, W_in, OPENING_in, LEAD_in,
                      x_out, OD_out, me, esc, g, p_txt, n_txt, process,
                      tip_pad_width=None):
    """Nested inner-winding lead escape: near pad on the inner arm tip
    (same net), bridge on the escape metal under the outer ring, far pad +
    BALUN_ME lead outside it. Metal-generic: me/esc are indices resolved by
    the caller; via legality is delegated to vias() (fails closed).

    ``tip_pad_width`` (design-region issue 04, default ``None`` ->
    unchanged W_in): tangential width of the arm-tip end via, trimmed by
    the caller so its outer corner clears the enclosing winding's
    innermost-turn chamfer corridor at small OD."""
    X0 = x_out + OD_out / 2.0 + g
    s_param = X0 - (x_in + OD_in / 2.0)
    if s_param <= 0:
        raise PortError("xfm_balun: crossunder span is non-positive; "
                        "windings are not radially nested")
    pin = _pin(me, process)
    for y0, txt, anchor in ((OPENING_in, p_txt, "low"),
                            (-OPENING_in - W_in, n_txt, "high")):
        cell.inst(base_ind_under(W=W_in, WX=W_in, S=s_param, TOP_ME=me,
                                 BTM_ME=esc, NT=1, process=process,
                                 tip_pad_width=tip_pad_width,
                                 tip_pad_anchor=anchor),
                  (X0, y0), "R90")
        # The lead itself registers its own port (port contract
        # 2026-09-21): base_lead's default far tip (local x=L=LEAD_in)
        # composed through this (X0, y0) R0 instance lands at exactly
        # (X0+LEAD_in, y0+W_in/2.0) -- the same expression this call site
        # used to pass add_emx_port directly, now derived from the SAME
        # already-drawn lead instead of a second macro sum.
        cell.inst(
            base_lead(L=LEAD_in, W=W_in, TOP_ME=str(me), BTM_ME=str(me),
                     process=process, port_name=txt, port_logical_name=txt,
                     port_metal=me, port_label_layer=pin),
            (X0, y0), "R0")


def _add_balun_ct(cell, center_x, OD, W, LEAD, balun_me, CT_ME, side, txt,
                  process, NT=1):
    """Balun center tap on CT_ME (strictly below BALUN_ME): W×W via stack at
    the winding's closed column + CT lead exiting outward + CTP/CTS port.
    Fails closed if CT_ME >= BALUN_ME; the tap is drawn into its winding's
    sub-cell, so the layer-complete _xfm_net_short gate covers every
    tap-vs-other-net overlap (M13 ticket 05). Mirrors xfm_bs CT geometry.

    Single-turn only, fail-closed: the tap lands at ``OD/2`` -- the
    OUTERMOST ring -- which is the winding's electrical midpoint only when
    NT==1. On a multi-turn winding that point is neither the midpoint nor
    free space (the via stack would sit on top of the inner turns), so a
    multi-turn request is refused rather than silently mis-tapped (mirrors
    the ``nested and NT_S >= 2`` guard in ``xfm_balun``)."""
    me = _metal_index(balun_me)
    cm = _metal_index(CT_ME)
    if cm >= me:
        raise PortError(
            f"xfm_balun: CT metal {_metal_name(cm)} must be below BALUN_ME {_metal_name(me)}")
    if NT >= 2:
        raise PortError(
            f"xfm_balun: {txt} center tap requires a single-turn winding "
            f"(got NT={NT}); the tap lands on the outermost ring, which is "
            f"the electrical midpoint only for NT=1")
    tap_side = "right" if side == "left" else "left"
    _bs_center_tap(cell, center_x, OD / 2.0, W, LEAD, balun_me, CT_ME,
                   tap_side, txt, process)


@builds_on_profile_stack
def xfm_balun(
    OD_P: float = 200.0, OD_S: float = 186.0,
    W_P: float = 5.0, W_S: float = 5.0, S: float = 2.0,
    OPENING_P: float = 8.0, OPENING_S: float = 8.0,
    LEAD_P: float = 20.0, LEAD_S: float = 20.0,
    NT_P: int = 1, NT_S: int = 1,
    CENTER_SPACING: float = 0.0,
    BALUN_ME: str = "9",
    ESCAPE_ME: str | None = None,
    CT_P_ME: str | None = None, CT_S_ME: str | None = None,
    ground_fixture: GroundFixtureConfig | None = None,
    process: ProcessRuleContext | None = None,
    PORT_SPACING_P: float | None = None,
) -> Cell:
    """Classic same-layer (coplanar) balun: primary and secondary octagon
    windings on ONE metal-generic plane BALUN_ME, the secondary nested
    inside the primary (CENTER_SPACING offsets the two centres; coplanar
    rings that do not overlap are two inductors, not a balun, and are
    refused -- user directive 2026-09-22 retired the side-by-side mode).
    The inner (secondary) winding's leads escape via a **crossunder** on
    ESCAPE_ME (default the real conductor immediately below BALUN_ME in the
    active stack -- see ``_metal_below``; overridable to any strictly lower
    metal) under the outer ring, then back up outside — the reference
    balun.il base_ind_under mechanism. The layer-complete full-net gate
    (_xfm_net_short, generalized in M13 ticket 05 and shared with
    xfm_bs) runs on every drawn layer in every mode."""
    me = _metal_index(BALUN_ME)
    # Real profile-stack neighbour, not a bare `me - 1` (gdsfactory review
    # 2026-09-21): N65's AP sits directly above M9 (no M10), so the old
    # literal default queried "M10" and failed every nested N65 AP-body
    # balun. Byte-identical wherever the stack is contiguous (reference mode
    # and every N28 body today).
    if ESCAPE_ME is not None:
        esc = _metal_index(ESCAPE_ME)
    else:
        try:
            esc = _metal_below(me, process)
        except PortError as exc:
            raise PortError(
                f"xfm_balun: BALUN_ME {_metal_name(me)} has no conductor below "
                f"it for the crossunder escape: {exc}") from exc
    if esc >= me:
        raise PortError(
            f"xfm_balun: ESCAPE_ME ({_metal_name(esc)}) must be strictly "
            f"below BALUN_ME ({_metal_name(me)})")
    if esc < 1:
        raise PortError("xfm_balun: no metal available below BALUN_ME for "
                        "the crossunder escape")
    xP, xS = -CENTER_SPACING / 2.0, CENTER_SPACING / 2.0
    nested = (xS - OD_S / 2.0 > xP - OD_P / 2.0 + 1e-9
              and xS + OD_S / 2.0 < xP + OD_P / 2.0 - 1e-9)
    if not nested:
        raise PortError(
            f"xfm_balun: the secondary must nest inside the primary "
            f"(OD_P={OD_P}, OD_S={OD_S}, CENTER_SPACING={CENTER_SPACING}): "
            "coplanar rings that do not overlap are two inductors, not a "
            "balun")
    if NT_S >= 2:
        raise PortError("xfm_balun: nested secondary must be single-turn "
                        "(NT_S=1); ind_sym leads cannot be rerouted")
    params = {"OD_P": OD_P, "OD_S": OD_S, "W_P": W_P, "W_S": W_S, "S": S,
              "OPENING_P": OPENING_P, "OPENING_S": OPENING_S,
              "LEAD_P": LEAD_P, "LEAD_S": LEAD_S, "NT_P": NT_P, "NT_S": NT_S,
              "CENTER_SPACING": CENTER_SPACING, "BALUN_ME": BALUN_ME,
              "ESCAPE_ME": ESCAPE_ME, "CT_P_ME": CT_P_ME, "CT_S_ME": CT_S_ME}
    if process is not None:
        params["process"] = process.profile_id
    cell = Cell(
        f"xfm_balun_P{OD_P}_S{OD_S}_C{CENTER_SPACING}_M{BALUN_ME}",
        "xfm_balun", params)
    def build_primary(compact_pad_max=None, compact_candidate_qualifier=None):
        candidate = Cell("xfm_balun_pri", "xfm_balun_pri", {})
        selected_pad = _ci_winding(
            candidate,
            xP,
            OD_P,
            W_P,
            OPENING_P,
            LEAD_P,
            S,
            NT_P,
            "left",
            BALUN_ME,
            "P1",
            "N1",
            process,
            compact_pad_max=compact_pad_max,
            compact_candidate_qualifier=compact_candidate_qualifier,
            port_spacing=PORT_SPACING_P,
        )
        return candidate, selected_pad

    # Each tap exits toward the OTHER winding; its lead runs on to that
    # winding's outer edge (never shorter than LEAD) so the tap port is a
    # true peripheral port and its ground stub keeps the designed length
    # instead of an M1 strip under the other winding (2026-09-22). The
    # primary's tap is therefore added only once the secondary exists.
    def add_ctp(target):
        _add_balun_ct(target, xP, OD_P, W_P,
                      ct_lead_to_edge(LEAD_P, xP + OD_P / 2.0, _drawing_bbox_um(sec)[2]),
                      BALUN_ME, CT_P_ME, "left", "CTP", process, NT=NT_P)

    pri, primary_pad = build_primary()
    sec = Cell("xfm_balun_sec", "xfm_balun_sec", {})
    # Coordinated chamfer staircase across the NESTED radial order
    # (P turns then S ring at the same (W+S) pitch): the primary's
    # own kernels bias their inner turns (self-computed, pure
    # function -- recomputed here identically), and the S ring
    # continues the staircase so the P_last<->S 45-degree pair also
    # clears the effective floor. Without this the two cells
    # quantize independently and the cross-net chamfer gap loses
    # ~10-17 nm exactly at S == floor (six-family tight-spacing
    # clearance, issue 05; the wide-opening escape-landing
    # protrusion is a separate, structural refusal).
    sec_bias = 0
    tip_w = None
    if process is not None and NT_S == 1:
        p_ods = [OD_P - 2.0 * k * (W_P + S) for k in range(NT_P)]
        delta_p = chamfer_staircase_delta(p_ods, W_P, me, process)
        bias_p_last = (NT_P - 1) * delta_p
        delta_ps = chamfer_staircase_delta(
            [p_ods[-1], OD_S], W_S, me, process)
        sec_bias = bias_p_last + delta_ps
        # Escape-tip corridor trim (issue 04): the crossunder's
        # arm-tip end via lands on the secondary arm right where the
        # primary INNERMOST turn's inner 45-degree chamfer passes --
        # shared quantized math in _escape_tip_pad_width (xfm_il calls
        # it against P's OUTERMOST ring, design-region issue 05).
        tip_w = _escape_tip_pad_width(
            ring_od=p_ods[-1], ring_bias=bias_p_last, ring_w=W_P,
            tip_w=W_S, x_tip=(xS + OD_S / 2.0) - xP,
            opening=OPENING_S, me=me, process=process,
            where=f"xfm_balun: OD_P={OD_P}, OD_S={OD_S}, W={W_S}, "
                  f"NT_P={NT_P}",
            corridor="primary innermost turn's chamfer corridor",
            advice="increase OD or reduce W/NT_P")
    if process is not None:
        # The arm-tip pad (OPENING_S .. OPENING_S + W_S along the arm) must sit on the secondary's own flat;
        # past BA the ring's outer boundary turns 45 degrees and the pad's corner would hang outside it (D4).
        flat = octagon(OD_S, W_S, sec_bias).BA
        if OPENING_S + (W_S if tip_w is None else tip_w) > flat + 1e-9:
            raise PortError(
                f"xfm_balun: OD_S={OD_S}, W_S={W_S}, OPENING_S={OPENING_S}: the secondary escape's arm-tip pad "
                f"reaches {OPENING_S + (W_S if tip_w is None else tip_w):.3f} um along the arm, past the ring's flat "
                f"(BA={flat} um) -- the pad would hang off the ring; reduce OPENING_S or W_S, or increase OD_S"
            )
    _ci_winding(sec, xS, OD_S, W_S, OPENING_S, LEAD_S, S, NT_S, "right",
                BALUN_ME, "P2", "N2", process,
                direct_leads=False, chamfer_bias=sec_bias)
    _balun_crossunder(sec, xS, OD_S, W_S, OPENING_S, LEAD_S,
                      xP, OD_P, me, esc, S, "P2", "N2", process,
                      tip_pad_width=tip_w)
    if CT_S_ME is not None:
        _add_balun_ct(sec, xS, OD_S, W_S,
                      ct_lead_to_edge(LEAD_S, xS - OD_S / 2.0, _drawing_bbox_um(pri)[0]),
                      BALUN_ME, CT_S_ME, "right", "CTS", process, NT=NT_S)
    if CT_P_ME is not None:
        add_ctp(pri)

    # A compact primary first maximizes its own via landing.  In a nested
    # balun the secondary is an additional obstacle that the standalone coil
    # cannot see, so rerun the same pad/lane planner with the complete
    # secondary net as an extra qualifier.  This can move the bridge landing
    # as well as shorten it; pad length alone is insufficient at a wide-line
    # conditional-spacing boundary.
    if primary_pad is not None and not _xfm_nets_are_drc_separate(
        pri, sec, process
    ):
        def clears_secondary(coil):
            trial = Cell("xfm_balun_pri_trial", "xfm_balun_pri_trial", {})
            trial.inst(coil, (xP, 0.0), "MY")
            if CT_P_ME is not None:
                add_ctp(trial)
            return _xfm_nets_are_drc_separate(trial, sec, process)

        try:
            pri, _primary_pad = build_primary(
                compact_candidate_qualifier=clears_secondary
            )
            if CT_P_ME is not None:
                add_ctp(pri)
        except PortError as exc:
            raise PortError(
                f"{exc} -- NESTED balun note (design-region-full-coverage): "
                f"the mirrored compact bridge shares the right half-plane "
                f"with the secondary escape, whose bars sit at "
                f"+-[OPENING_S, OPENING_S+W] "
                f"(currently OPENING_S={OPENING_S:.3f}, W={W_P:.3f}); when "
                f"the corridor binds, a slightly LARGER secondary opening "
                f"usually unlocks it (workable window ends where the S "
                f"ring ends leave the flat), before resorting to OD/W/S "
                f"changes"
            ) from exc
    elif primary_pad is None and not _xfm_nets_are_drc_separate(
        pri, sec, process
    ):
        # Multi-turn primary (no compact re-planner to fall back on):
        # the inter-net spacing gate must still fail closed -- before
        # this, an NT_P>=3 nested balun whose escape bridge ran within
        # (wide-parallel) spacing of the primary's own crossunder legs
        # shipped a dirty GDS (design-region issue 04, OD x NT sweep).
        raise PortError(
            f"xfm_balun: primary/secondary nets are closer than the "
            f"inter-net (incl. wide-parallel) spacing floor on a shared "
            f"layer and the NT_P={NT_P} primary has no compact re-plan "
            f"DOF; a larger secondary opening usually moves the escape "
            f"bars off the primary's crossunder legs (currently "
            f"OPENING_S={OPENING_S:.3f}, W={W_P:.3f}), before resorting "
            f"to OD/W/S changes"
        )
    _xfm_net_short("xfm_balun", pri, sec)
    cell.inst(pri, (0.0, 0.0), "R0")
    cell.inst(sec, (0.0, 0.0), "R0")
    # (port contract 2026-09-21) the one composition point: walks the
    # cell.inst() chain down to every base_lead/base_lead_pair leg
    # (P1/N1/P2/N2 from _ci_winding's direct leads or _balun_crossunder's
    # escape leads, CTP/CTS from _bs_center_tap via _add_balun_ct) --
    # replaces the old cell.emx_ports.extend(pri.emx_ports)/
    # extend(sec.emx_ports).
    cell.emx_ports = finalize_emx_ports(cell)
    _xfm_order_ports(cell)
    if ground_fixture is not None:
        add_ground_fixture(cell, ground_fixture, process)
    return cell
