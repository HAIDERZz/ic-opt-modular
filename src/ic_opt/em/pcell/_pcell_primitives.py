# Auto-split from pcell_inductor_port_clean.py (wrap-up P1,
# 2026-07-28): verbatim segment move, no behavior change. The
# facade module re-exports every name; see its docstring for
# provenance and the KNOWN_DEVIATIONS record.
from __future__ import annotations

import math

from ic_opt.em.pcell._pcell_core import (
    _EPS,
    GRID_UM,
    PI,
    Cell,
    PortError,
    ProcessRuleContext,
    _effective_min_spacing,
    _junction_clearance_const,
    _metal,
    _metal_below,
    _metal_index,
    _metal_name,
    add_wide_path,
    ceiltogrid,
    chamfer,
    cross_endpoint_offset,
    floortogrid,
    junction_half_offset,
    octagon,
    vias,
    vias_nomet,
)

# ---------------------------------------------------------------------------
# base_ind_diag (gdsgen_ref/pcell/inductor/base_ind_diag.il)
# ---------------------------------------------------------------------------


def base_ind_diag(
    W: float = 2.0,
    S: float = 2.0,
    MET: int = 9,
    dummy: bool = True,
    process: ProcessRuleContext | None = None,
    clearance_met=None,
) -> Cell:
    """45-degree crossover diagonal on MetalVec(MET-1).

    ``dummy`` mirrors the dead pass-through parameter base_xfm_cross.il sends
    to base_ind_diag (which itself declares no such parameter); it is ignored.

    ``clearance_met`` (M12 Phase 0.5, D1) selects the conductor whose rule
    ``min_space`` drives the endpoint extension (``ext``): base_xfm_cross
    passes its ``TOP_ME`` so the diagonal reaches the widened endpoint pad
    that lands on the top metal. ``None`` (the default / direct callers)
    keys on ``MET`` -- which for reference mode and numeric metals returns
    the reference literal, so the diagonal is byte-identical."""
    params = {"W": W, "S": S, "MET": MET}
    if process is not None:
        params["process"] = process.profile_id
    cell = Cell(f"base_ind_diag_W{W}_S{S}_M{MET}", "base_ind_diag", params)
    P = S + W
    C = chamfer(W).C
    OO = 2 * W + S
    OOCH = junction_half_offset(W, S)
    layer = _metal(MET, process)
    if S <= 2.0 * math.sqrt(2):
        clr_met = MET if clearance_met is None else clearance_met
        ext = ceiltogrid(_junction_clearance_const(clr_met, process) * math.sqrt(2.0) - S)
        cell.add_polygon(
            layer,
            [
                (0, -OOCH + C),
                (0, -(OOCH + ext)),
                (W, -(OOCH + ext)),
                (W, -OOCH),
                (W + P, -OOCH + P),
                (OO, OOCH + ext),
                (OO - W, OOCH + ext),
                (P, -OOCH + C + P),
            ],
        )
    else:
        cell.add_polygon(
            layer,
            [
                (0, OOCH - P),
                (0, -OOCH),
                (W, -OOCH),
                (W + P, -OOCH + P),
                (OO, OOCH),
                (P, OOCH),
            ],
        )
    return cell


# ---------------------------------------------------------------------------
# base_xfm_cross (gdsgen_ref/pcell/transformer/base_xfm_cross.il)
# ---------------------------------------------------------------------------


def _pad_trim_floor(TOP_ME, process: ProcessRuleContext | None) -> float:
    """Minimum legal tangential length for a corridor-trimmed pad
    (design-region issue 04): the trimmed rect is still drawn metal, so
    it must respect the metal's own min_width (falling back to two grid
    steps when the rule is absent)."""
    mw = 0.0
    if process is not None:
        mw = process.adapter.metal_rule(
            _metal_name(_metal_index(TOP_ME))).min_width_um or 0.0
    return max(2 * GRID_UM, mw)


def xfm_cross_far_pad_y0(
    WI: float, WO: float, S: float, TOP_ME: int,
    process: ProcessRuleContext | None,
) -> float:
    """Tangential start offset of base_xfm_cross's FAR endpoint pad.

    Exactly the OOCH + OOCHD the primitive itself places that pad at --
    exposed so a caller (base_ind_hud_cross, issue 04) can bound the
    pad's outer corner against an adjacent ring's chamfer corridor
    without replicating the quantized math out of sync."""
    OOCH = junction_half_offset(WO, 2 * S + WI)
    if 2 * S + WI <= 2.0 * math.sqrt(2.0):
        OOCHD = ceiltogrid(
            _junction_clearance_const(TOP_ME, process) * math.sqrt(2.0) - WI)
    else:
        OOCHD = 0.0
    return OOCH + OOCHD


def base_xfm_cross(
    WI: float = 2.0,
    WO: float = 3.0,
    S: float = 2.0,
    TOP_ME: int = 9,
    BTM_ME: int = 8,
    dummy: bool = True,
    viat: bool = True,
    viad: bool = True,
    top: bool = False,
    process: ProcessRuleContext | None = None,
    far_pad_length: float | None = None,
    near_pad_length: float | None = None,
) -> Cell:
    """One crossover leg: base_ind_diag(W=WO, S=2*S+WI) + one vias block per endpoint.

    Faithful to base_xfm_cross.il: both endpoint vias instances are created
    unconditionally (viat/viad are declared but never read in the reference
    source); a same-layer call (TOP_ME == BTM_ME) therefore yields metal-only
    endpoint pads and no cuts.

    ``far_pad_length`` / ``near_pad_length`` (design-region issue 04,
    default ``None`` -> byte-identical WO x WO): tangential Length of the
    FAR endpoint pad (at ``WI + WO + 2*S``) resp. the NEAR one (at the
    origin). Each pad's junction-side edge stays at ``+-(OOCH + OOCHD)``;
    only its outer edge retracts, so a caller can keep the pad's corner
    clear of an adjacent ring's inner 45-degree chamfer corridor at small
    ring OD where the octagon flat is shorter than the full pad. Which
    endpoint lands on the smaller ring depends on the instance's mirror
    (hud cross1 ``R0`` -> far, cross2 ``MY`` -> near), hence both knobs."""
    params = {
        "WI": WI,
        "WO": WO,
        "S": S,
        "TOP_ME": TOP_ME,
        "BTM_ME": BTM_ME,
        "viat": viat,
        "viad": viad,
        "top": top,
    }
    if process is not None:
        params["process"] = process.profile_id
    fp_suffix = ""
    if far_pad_length is not None:
        params["far_pad_length"] = far_pad_length
        fp_suffix = f"_fp{far_pad_length}"
    if near_pad_length is not None:
        params["near_pad_length"] = near_pad_length
        fp_suffix += f"_np{near_pad_length}"
    cell = Cell(
        f"base_xfm_cross_WI{WI}_WO{WO}_S{S}_T{TOP_ME}_B{BTM_ME}_top{top}"
        f"{fp_suffix}",
        "base_xfm_cross",
        params,
    )
    OOCH = junction_half_offset(WO, 2 * S + WI)
    diag_met = TOP_ME if top else BTM_ME
    # D1 (M12 Phase 0.5): the endpoint via block spans BTM_ME..TOP_ME, so its
    # top-metal pad is what a same-layer ring can crowd -- key the endpoint
    # offset (and the diagonal's matching extension) on TOP_ME so an AP-body
    # ring clears the pad, while numeric/reference stacks keep the 2.01
    # literal (byte-identical). The diagonal itself stays connected to the
    # widened pad via the shared clearance_met=TOP_ME.
    if 2 * S + WI <= 2.0 * math.sqrt(2.0):
        OOCHD = ceiltogrid(
            _junction_clearance_const(TOP_ME, process) * math.sqrt(2.0) - WI)
    else:
        OOCHD = 0.0
    cell.inst(
        base_ind_diag(W=WO, S=2 * S + WI, MET=diag_met, dummy=dummy,
                      process=process, clearance_met=TOP_ME),
        (0.0, 0.0),
        "R0",
    )
    cell.inst(
        vias(Length=WO if near_pad_length is None else near_pad_length,
             Width=WO, TOP_ME=TOP_ME, BTM_ME=BTM_ME, process=process),
        (0.0, -OOCH - OOCHD),
        "MX",
    )
    cell.inst(
        vias(Length=WO if far_pad_length is None else far_pad_length,
             Width=WO, TOP_ME=TOP_ME, BTM_ME=BTM_ME, process=process),
        (WI + WO + 2 * S, OOCH + OOCHD),
        "R0",
    )
    return cell


# ---------------------------------------------------------------------------
# base_oct_quad (gdsgen_ref/pcell/common/base_oct_quad.il)
# ---------------------------------------------------------------------------


def base_oct_quad(
    OD: float = 200.0,
    W: float = 5.0,
    OP: float = 0.0,
    MET: int = 9,
    bCons: bool = False,
    process: ProcessRuleContext | None = None,
    chamfer_bias: int = 0,
) -> Cell:
    """Upper-left octagon quadrant on MetalVec(MET-1), local x in [0, OD/2].

    ``chamfer_bias`` (six-family tight-spacing clearance, 2026-07-28;
    default 0 = byte-identical): pulls the chamfer baseline BA inward by
    that many GRID_UM steps, moving ONLY the 45-degree edges and their
    flat junctions -- the cardinal flats/arm openings derive from OD and
    do not move. Callers use it as a per-ring staircase
    (``chamfer_staircase_delta``) so adjacent concentric rings' diagonal
    separation clears the effective spacing floor despite each ring's
    independent A/BA/C quantization."""
    params = {"OD": OD, "W": W, "OP": OP, "MET": MET, "bCons": bCons}
    if process is not None:
        params["process"] = process.profile_id
    suffix = f"_cb{chamfer_bias}" if chamfer_bias else ""
    if chamfer_bias:
        params["chamfer_bias"] = chamfer_bias
    cell = Cell(
        f"base_oct_quad_OD{OD}_W{W}_OP{OP}_M{MET}{suffix}",
        "base_oct_quad", params
    )
    ring = octagon(OD, W, chamfer_bias)
    A, BA, C = ring.A, ring.BA, ring.C
    layer = _metal(MET, process)
    if OP > (BA - C):
        if bCons:
            cell.add_polygon(
                layer,
                [
                    (W, W + BA),
                    (W / 2, W / 2 + BA),
                    (W / 2, OP),
                    (W + (OP - (BA - C)), OP),
                    (A + C, BA + A - W),
                    (OD / 2, BA + A - W),
                    (OD / 2, BA + A),
                    (A, BA + A),
                ],
            )
        else:
            cell.add_polygon(
                layer,
                [
                    (W, W + BA),
                    (W / 2, W / 2 + BA),
                    (W / 2, BA - C),
                    (W, BA - C),
                    (A + C, BA + A - W),
                    (OD / 2, BA + A - W),
                    (OD / 2, BA + A),
                    (A, BA + A),
                ],
            )
    else:
        cell.add_polygon(
            layer,
            [
                (0, BA),
                (0, OP),
                (W, OP),
                (W, BA - C),
                (A + C, BA + A - W),
                (OD / 2, BA + A - W),
                (OD / 2, BA + A),
                (A, BA + A),
            ],
        )
    return cell


# ---------------------------------------------------------------------------
# base_oct_half (gdsgen_ref/pcell/common/base_oct_half.il)
# ---------------------------------------------------------------------------


def base_oct_half(
    OD: float = 200.0,
    W: float = 5.0,
    LOP: float = 0.0,
    ROP: float = 0.0,
    bCons: bool = False,
    MET: int = 9,
    process: ProcessRuleContext | None = None,
    chamfer_bias: int = 0,
) -> Cell:
    """Upper octagon half: quad(LOP) at (-OD/2, 0) R0 + quad(ROP) at (OD/2, 0) MY."""
    params = {"OD": OD, "W": W, "LOP": LOP, "ROP": ROP, "bCons": bCons, "MET": MET}
    if process is not None:
        params["process"] = process.profile_id
    suffix = f"_cb{chamfer_bias}" if chamfer_bias else ""
    if chamfer_bias:
        params["chamfer_bias"] = chamfer_bias
    cell = Cell(
        f"base_oct_half_OD{OD}_W{W}_L{LOP}_R{ROP}_M{MET}{suffix}",
        "base_oct_half", params
    )
    cell.inst(
        base_oct_quad(OD=OD, W=W, OP=LOP, MET=MET, bCons=bCons,
                      process=process, chamfer_bias=chamfer_bias),
        (-OD / 2, 0.0),
        "R0",
    )
    cell.inst(
        base_oct_quad(OD=OD, W=W, OP=ROP, MET=MET, bCons=bCons,
                      process=process, chamfer_bias=chamfer_bias),
        (OD / 2, 0.0),
        "MY",
    )
    return cell


# ---------------------------------------------------------------------------
# base_oct (gdsgen_ref/pcell/common/base_oct.il)
# ---------------------------------------------------------------------------


def base_oct(
    OD: float = 200.0,
    W: float = 5.0,
    LOP: float = 0.0,
    ROP: float = 0.0,
    MET: int = 9,
    process: ProcessRuleContext | None = None,
    chamfer_bias: int = 0,
) -> Cell:
    """Full octagon ring: base_oct_half at R0 plus its MX mirror."""
    params = {"OD": OD, "W": W, "LOP": LOP, "ROP": ROP, "MET": MET}
    if process is not None:
        params["process"] = process.profile_id
    suffix = f"_cb{chamfer_bias}" if chamfer_bias else ""
    if chamfer_bias:
        params["chamfer_bias"] = chamfer_bias
    cell = Cell(
        f"base_oct_OD{OD}_W{W}_L{LOP}_R{ROP}_M{MET}{suffix}",
        "base_oct", params)
    half = base_oct_half(OD=OD, W=W, LOP=LOP, ROP=ROP, MET=MET,
                         process=process, chamfer_bias=chamfer_bias)
    cell.inst(half, (0.0, 0.0), "R0")
    cell.inst(half, (0.0, 0.0), "MX")
    return cell


def base_balun_sec(
    OD: float = 100.0,
    WI: float = 4.0,
    LOP: float = 10.0,
    process: ProcessRuleContext | None = None,
) -> Cell:
    """Balun secondary: a single MET=9 octagon ring, verbatim from
    base_balun_sec.il (which computes P/C/A/B/BA/BB but instantiates only
    base_oct). The .il params WO/S/CW/B_TK/A_TK/DIV/LEADDYI/LEADDYO/dummy/
    DUMMYL drive nothing in the source let body and are omitted."""
    params = {"OD": OD, "WI": WI, "LOP": LOP}
    if process is not None:
        params["process"] = process.profile_id
    cell = Cell(f"base_balun_sec_OD{OD}_WI{WI}", "base_balun_sec", params)
    cell.inst(
        base_oct(OD=OD, W=WI, LOP=LOP, ROP=0.0, MET=9, process=process),
        (0.0, 0.0), "R0")
    return cell


# ---------------------------------------------------------------------------
# base_oct_quad_vias / base_oct_half_vias
# (gdsgen_ref/pcell/common/base_oct_quad_vias.il — axis-aligned via coverage)
# ---------------------------------------------------------------------------


def base_oct_quad_vias(
    OD: float = 50.0,
    W: float = 5.0,
    OP: float = 0.0,
    MET: int = 9,
    diagonal_vias: bool = False,
    process: ProcessRuleContext | None = None,
) -> Cell:
    """Upper-left octagon quadrant axis-aligned VIA coverage.

    Places a ``BB``-wide VIA block (``vias_nomet``) at ``(OD/2-BB, OD/2-W)``
    following base_oct_quad_vias.il. The diagonal VIA arm needs the sourceless
    ``vias_diagonal_nomet`` library PCell (no .il, no GDS evidence — ind_ref
    VIA8 cuts are all axis-aligned); it fails closed.
    """
    params = {"OD": OD, "W": W, "OP": OP, "MET": MET}
    if process is not None:
        params["process"] = process.profile_id
    cell = Cell(
        f"base_oct_quad_vias_OD{OD}_W{W}_OP{OP}_M{MET}", "base_oct_quad_vias", params
    )
    ring = octagon(OD, W)
    BB = OD - 2 * ring.A - ring.BA
    cell.inst(
        vias_nomet(Length=W, Width=BB, TOP_ME=MET, BTM_ME=MET - 1, process=process),
        (OD / 2 - BB, OD / 2 - W),
        "R0",
    )
    if diagonal_vias:
        raise PortError(
            "base_oct_quad_vias: diagonal via arm needs vias_diagonal_nomet, "
            "which has no source and no GDS evidence (fail closed)"
        )
    return cell


def base_oct_half_vias(
    OD: float = 200.0,
    W: float = 5.0,
    LOP: float = 0.0,
    ROP: float = 0.0,
    MET: int = 9,
    process: ProcessRuleContext | None = None,
) -> Cell:
    """Upper octagon half VIA coverage: quad_vias(LOP) at (-OD/2,0) R0 +
    quad_vias(ROP) at (OD/2,0) MY, mirroring base_oct_half."""
    params = {"OD": OD, "W": W, "LOP": LOP, "ROP": ROP, "MET": MET}
    if process is not None:
        params["process"] = process.profile_id
    cell = Cell(
        f"base_oct_half_vias_OD{OD}_W{W}_L{LOP}_R{ROP}_M{MET}",
        "base_oct_half_vias",
        params,
    )
    cell.inst(
        base_oct_quad_vias(OD=OD, W=W, OP=LOP, MET=MET, process=process),
        (-OD / 2, 0.0),
        "R0",
    )
    cell.inst(
        base_oct_quad_vias(OD=OD, W=W, OP=ROP, MET=MET, process=process),
        (OD / 2, 0.0),
        "MY",
    )
    return cell


# ---------------------------------------------------------------------------
# base_xfm_half (gdsgen_ref/pcell/transformer/base_xfm_half.il)
# ---------------------------------------------------------------------------


def base_xfm_half(
    OD: float = 200.0,
    WO: float = 5.0,
    WI: float = 5.0,
    S: float = 2.0,
    LOP: float = 15.0,
    ROP: float = 15.0,
    TOP_ME: int = 9,
    BTM_ME: int = 9,
    via_to_next: bool = False,
    via_diag: bool = False,
    process: ProcessRuleContext | None = None,
    chamfer_bias: int = 0,
) -> Cell:
    """Metal-generic stacked octagon half-ring (base_xfm_half.il port):
    one base_oct_half per metal level in [BTM_ME, TOP_ME], optionally stitched
    by one axis-aligned vias() block (via_to_next). via_diag uses the sourceless
    'vias_diagonal' PCell (no source, no GDS evidence; ind_ref.gds VIA8 cuts
    are all axis-aligned), so via_diag=True fails closed. WI/BB/PA/PB are dead
    (signature fidelity). The reference's A_TK/B_TK via-placement offsets and
    its P/C feed only dead or unported branches and are dropped."""
    top, btm = _metal_index(TOP_ME), _metal_index(BTM_ME)
    if top < btm:
        raise PortError(f"base_xfm_half: TOP_ME {_metal_name(top)} below BTM_ME {_metal_name(btm)}")
    if via_diag:
        raise PortError(
            "base_xfm_half: via_diag needs the sourceless 'vias_diagonal' "
            "PCell (no source, no GDS evidence); fail closed")
    ring = octagon(OD, WO)
    A, BA = ring.A, ring.BA
    B = OD - 2 * A
    params = {"OD": OD, "WO": WO, "WI": WI, "S": S, "LOP": LOP, "ROP": ROP,
              "TOP_ME": TOP_ME, "BTM_ME": BTM_ME, "via_to_next": via_to_next,
              "via_diag": via_diag}
    if process is not None:
        params["process"] = process.profile_id
    if chamfer_bias:
        params["chamfer_bias"] = chamfer_bias
    cb = f"_cb{chamfer_bias}" if chamfer_bias else ""
    cell = Cell(f"base_xfm_half_OD{OD}_WO{WO}_T{top}_B{btm}{cb}",
                "base_xfm_half", params)
    for i in range(btm, top + 1):
        cell.inst(base_oct_half(OD=OD, W=WO, LOP=LOP, ROP=ROP, MET=i,
                                process=process, chamfer_bias=chamfer_bias),
                  (0.0, 0.0), "R0")
    if via_to_next and top > btm:
        cell.inst(
            vias(Length=WO, Width=B, TOP_ME=top, BTM_ME=btm,
                 process=process),
            (A - OD / 2, BA + A - WO), "R0")
    return cell


# ---------------------------------------------------------------------------
# base_lead (gdsgen_ref/pcell/common/base_lead.il)
# ---------------------------------------------------------------------------


def base_lead(
    L: float = 10.0,
    W: float = 8.0,
    WD: float = 1.0,
    PINTXT: str = "p1",
    PINP: str = "dummy1",
    TOP_ME: str = "9",
    BTM_ME: str = "9",
    DUMMYL: str = "RFVLSI",
    DUMMYP: str = "drawing",
    dummy: bool = True,
    process: ProcessRuleContext | None = None,
    port_name: str | None = None,
    port_logical_name: str | None = None,
    port_metal: int | None = None,
    port_label_layer: tuple[int, int] | None = None,
    port_x_um: float | None = None,
) -> Cell:
    """Port lead drawn by the vias PCell (Length=W, Width=L at origin R0).

    The RFVLSI dummy rectangles, pin rectangle and pin label of the
    reference are not ported (documented deviation); WD/PINTXT/PINP only
    parametrise those and are therefore accepted but unused.

    ``port_name`` (port contract 2026-09-21, default ``None`` -> no port
    registered, byte-identical to every call site this stage does not
    migrate): this is the ONE primitive that draws a bare rectangular
    lead, so it is the ONE place its own EMX port belongs. The port is
    registered in THIS cell's own local R0 frame -- the same frame the
    ``vias()`` instance above just drew into -- at ``x_um=port_x_um``
    (default ``L``, the lead's far/outward tip; the two CT taps that
    instead sit at the near end pass ``port_x_um=0.0``) and
    ``y_um=W/2.0`` (every existing call site's port sits on the lead's
    own centreline; verified algebraically against all 16 pre-contract
    ``add_emx_port`` call sites in the port-lattice design). The
    registered ``lead_zone_um`` is exactly ``[0, L] x [0, W]`` -- the
    same rectangle ``vias()`` draws, not a separate bbox computation."""
    params = {
        "L": L,
        "W": W,
        "WD": WD,
        "PINTXT": PINTXT,
        "PINP": PINP,
        "TOP_ME": TOP_ME,
        "BTM_ME": BTM_ME,
    }
    if process is not None:
        params["process"] = process.profile_id
    cell = Cell(f"base_lead_L{L}_W{W}_T{TOP_ME}_B{BTM_ME}", "base_lead", params)
    cell.inst(
        vias(
            Length=W,
            Width=L,
            TOP_ME=_metal_index(TOP_ME),
            BTM_ME=_metal_index(BTM_ME),
            process=process,
        ),
        (0.0, 0.0),
        "R0",
    )
    if port_name is not None:
        px = L if port_x_um is None else port_x_um
        cell.add_emx_port(
            name=port_name,
            logical_name=port_logical_name or port_name,
            metal=port_metal if port_metal is not None else _metal_index(TOP_ME),
            label_layer=port_label_layer,
            x_um=px,
            y_um=W / 2.0,
            lead_zone_um=(0.0, 0.0, L, W),
        )
    return cell


def base_lead_jog(
    L: float,
    W: float,
    DY: float,
    LEAD_ME: str,
    *,
    port_name: str,
    port_logical_name: str | None,
    port_metal: int,
    port_label_layer: tuple[int, int],
    process: ProcessRuleContext | None = None,
    RUN: float = 0.0,
) -> Cell:
    """``base_lead`` whose tip is shifted ``DY`` across (M3.1, ``port_spacing``): straight out of the ring for
    W + ``RUN``, a 45-degree jog of |DY|, then straight to ``L``. The port sits at the tip; its zone is the
    straight tail, which must be at least W long (``L >= 2 W + RUN + |DY|``).

    ``RUN`` keeps an outward jog's 45-degree edge clear of the ring arm it leaves: the outline's diagonal
    begins ``(W/2) tan(pi/8)`` before the centreline vertex and that corner is the closest point to the
    arm's outer edge, so the caller sets ``RUN`` to the metal's spacing floor plus that offset."""
    tail = L - W - RUN - abs(DY)
    if tail < W - _EPS:
        raise PortError(
            f"base_lead_jog: lead {L} um cannot hold a {abs(DY):.3f} um jog plus a W={W} um tail "
            f"(needs LEAD >= {2 * W + RUN + abs(DY):.3f} um); increase the lead length or reduce the port spacing change"
        )
    cell = Cell(f"base_lead_jog_L{L}_W{W}_DY{DY}_R{RUN}_M{LEAD_ME}", "base_lead_jog", {"L": L, "W": W, "DY": DY, "RUN": RUN, "LEAD_ME": LEAD_ME})
    yc = W / 2.0
    add_wide_path(cell, _metal(_metal_index(LEAD_ME), process), [(0.0, yc), (W + RUN, yc), (W + RUN + abs(DY), yc + DY), (L, yc + DY)], W)
    cell.add_emx_port(name=port_name, logical_name=port_logical_name or port_name, metal=port_metal, label_layer=port_label_layer,
                      x_um=L, y_um=yc + DY, lead_zone_um=(L - tail, DY, L, DY + W))
    return cell


# ---------------------------------------------------------------------------
# base_lead_pair (gdsgen_ref/pcell/common/base_lead_pair.il)
# ---------------------------------------------------------------------------


def base_lead_pair(
    W: float = 2.0,
    OPENING: float = 20.0,
    LEAD: float = 20.0,
    TOP_ME: str = "9",
    LEAD_ME: str = "9",
    dummy: bool = True,
    P1TXT: str = "P1",
    N1TXT: str = "N1",
    process: ProcessRuleContext | None = None,
    port_metal: int | None = None,
    port_label_layer: tuple[int, int] | None = None,
    port_p1_logical_name: str | None = None,
    port_n1_logical_name: str | None = None,
    PORT_SPACING: float | None = None,
) -> Cell:
    """P/N lead pair on LEAD_ME plus WxW via blocks when TOP_ME != LEAD_ME.

    ``port_metal``/``port_label_layer`` (port contract 2026-09-21, default
    ``None`` -> no port registered on either leg, byte-identical to every
    call site this stage does not migrate): P1TXT/N1TXT become each leg's
    registered EMX port name only when the caller opts in this way -- the
    two are always the SAME metal/label_layer at every migrated call site,
    so one pair of keywords drives both legs' ``base_lead``. Each leg's
    port lands at its own ``base_lead`` far tip (``port_x_um`` left at its
    default) -- the lane offset that makes the LEFT copy exit -x instead
    of +x is the caller's own ``"MY"`` instance orientation below, not a
    different local zone edge (verified algebraically for both the R0 and
    MY call sites in the port-lattice design).

    ``port_p1_logical_name``/``port_n1_logical_name`` (default ``None`` ->
    each leg's logical name equals its own P1TXT/N1TXT, xfm_bs's own
    convention): ind_sym's ``port_order`` can rename a port while keeping
    its logical role fixed at "P1"/"N1" (M13's semantic-name contract,
    pinned by ``test_ind_sym_ct_port_order_override``) -- these two let a
    caller separate "the GDS/EMX name" from "the role" without adding a
    third independent registration path.
    """
    params = {
        "W": W,
        "OPENING": OPENING,
        "LEAD": LEAD,
        "TOP_ME": TOP_ME,
        "LEAD_ME": LEAD_ME,
        "P1TXT": P1TXT,
        "N1TXT": N1TXT,
    }
    if process is not None:
        params["process"] = process.profile_id
    suffix = ""
    if PORT_SPACING is not None:
        params["PORT_SPACING"] = PORT_SPACING
        suffix = f"_PS{PORT_SPACING}"
    cell = Cell(
        f"base_lead_pair_W{W}_O{OPENING}_L{LEAD}_T{TOP_ME}_LM{LEAD_ME}{suffix}",
        "base_lead_pair",
        params,
    )
    TM = _metal_index(TOP_ME)
    LM = _metal_index(LEAD_ME)
    port_name_p1 = P1TXT if port_metal is not None else None
    port_name_n1 = N1TXT if port_metal is not None else None
    # M3.1: an independent tip spacing. The ring opening fixes where the leads
    # leave (centres +-(OPENING + W/2)); a PORT_SPACING that differs jogs each
    # lead by half the difference on its way out. Two leads closer than the
    # metal's spacing floor are refused here, not discovered by DRC.
    natural = 2 * OPENING + W
    if PORT_SPACING is not None and abs(PORT_SPACING - natural) > _EPS:
        if port_metal is None:
            raise PortError("base_lead_pair: PORT_SPACING needs registered ports (port_metal)")
        floor = _effective_min_spacing(LM, W, process) if process is not None else 0.0
        if PORT_SPACING - W < floor - _EPS:
            raise PortError(
                f"base_lead_pair: port spacing {PORT_SPACING} um leaves {PORT_SPACING - W:.3f} um between the two "
                f"W={W} um leads, below the {_metal_name(LM)} spacing floor {floor} um"
            )
        dy = (PORT_SPACING - natural) / 2.0
        # an outward jog must clear the ring arm it leaves: the mitred outline starts its diagonal (W/2) tan(pi/8)
        # before the centreline vertex, and that corner is the closest point to the arm's outer edge
        run = ceiltogrid(floor + W * math.tan(PI / 8) / 2.0) if dy > 0 else 0.0
        for txt, logical, y0, sign in ((P1TXT, port_p1_logical_name, OPENING, 1.0), (N1TXT, port_n1_logical_name, -OPENING - W, -1.0)):
            cell.inst(
                base_lead_jog(L=LEAD, W=W, DY=sign * dy, LEAD_ME=LEAD_ME, port_name=txt, port_logical_name=logical,
                              port_metal=port_metal, port_label_layer=port_label_layer, process=process, RUN=run),
                (0.0, y0),
                "R0",
            )
        return cell
    cell.inst(
        base_lead(
            L=LEAD,
            W=W,
            WD=1.0,
            PINTXT=P1TXT,
            PINP="dummy1",
            TOP_ME=LEAD_ME,
            BTM_ME=LEAD_ME,
            DUMMYL="RFVLSI",
            dummy=dummy,
            process=process,
            port_name=port_name_p1,
            port_logical_name=port_p1_logical_name,
            port_metal=port_metal,
            port_label_layer=port_label_layer,
        ),
        (0.0, OPENING),
        "R0",
    )
    cell.inst(
        base_lead(
            L=LEAD,
            W=W,
            WD=1.0,
            PINTXT=N1TXT,
            PINP="dummy1",
            TOP_ME=LEAD_ME,
            BTM_ME=LEAD_ME,
            DUMMYL="RFVLSI",
            dummy=dummy,
            process=process,
            port_name=port_name_n1,
            port_logical_name=port_n1_logical_name,
            port_metal=port_metal,
            port_label_layer=port_label_layer,
        ),
        (0.0, -OPENING - W),
        "R0",
    )
    if TM != LM:
        cell.inst(
            vias(Length=W, Width=W, TOP_ME=TM, BTM_ME=LM, process=process),
            (0.0, OPENING),
            "R0",
        )
        cell.inst(
            vias(Length=W, Width=W, TOP_ME=TM, BTM_ME=LM, process=process),
            (0.0, -OPENING - W),
            "R0",
        )
    return cell


def base_ind_under(
    W: float = 4.0,
    WX: float = 4.0,
    S: float = 2.0,
    TOP_ME: int = 9,
    BTM_ME: int = 8,
    NT: int = 3,
    process: ProcessRuleContext | None = None,
    tip_pad_width: float | None = None,
    tip_pad_anchor: str = "low",
) -> Cell:
    """Cross-under bridge, verbatim from base_ind_under.il: three ``vias``
    instances (a long M[BTM..TOP-1] bridge plus two short M[TOP-1..TOP] end
    vias). The ``if(dummy)`` RFVLSI/drawing + RFVLSI_LVS_Vec rectangles are
    the same unmappable density-fill / LVS layers dropped in M7P and are not
    ported (the dummy / marker-layer params are omitted). Reference mode
    draws metals M5..M9 + cut arrays VIA5..VIA8; process mode builds the same
    stack from each via's own via_primitives geometry (geometric-only
    enforcement, n28-rules-slim, user directive 2026-07-19 -- previously this
    fell closed on a retired via_restrictions policy gate).

    The transit layer between the caller's own BTM_ME and TOP_ME is the
    REAL conductor immediately below TOP_ME (``_metal_below``), not a bare
    ``TOP_ME - 1`` (gdsfactory review 2026-09-21): on N65+AP, TOP_ME=AP's
    real neighbour is M9, since N65 has no M10 between M9 and AP -- the old
    literal `-1` queried the nonexistent "M10" and failed every caller that
    ever escapes below AP on that stack (xfm_balun's nested crossunder,
    reused verbatim by xfm_il). Byte-identical wherever the stack is
    contiguous (every reference-mode call, and every N28 call today)."""
    TOP_ME, BTM_ME = _metal_index(TOP_ME), _metal_index(BTM_ME)
    transit = _metal_below(TOP_ME, process)
    if BTM_ME == TOP_ME:
        btm_chosen, top_chosen = transit, TOP_ME
    else:
        btm_chosen, top_chosen = BTM_ME, transit
    P = W + S
    params = {"W": W, "WX": WX, "S": S, "TOP_ME": TOP_ME,
              "BTM_ME": BTM_ME, "NT": NT}
    if process is not None:
        params["process"] = process.profile_id
    if tip_pad_anchor not in ("low", "high"):
        raise PortError(
            f"base_ind_under: tip_pad_anchor {tip_pad_anchor!r} must be "
            f"'low' or 'high'")
    tp_suffix = ""
    if tip_pad_width is not None:
        # design-region issue 04: the FAR end via (at NT*P - W, the ring
        # arm tip a nested escape lands on) gets its Width -- the axis a
        # caller's R90 placement maps to the tangential direction --
        # trimmed so its corner clears an adjacent ring's inner chamfer
        # corridor. ``tip_pad_anchor`` picks which Width edge stays put
        # (the opening-side one): 'low' keeps x=0, 'high' keeps x=WX.
        # The bridge and the near end via keep full WX.
        params["tip_pad_width"] = tip_pad_width
        params["tip_pad_anchor"] = tip_pad_anchor
        tp_suffix = f"_tp{tip_pad_width}{tip_pad_anchor[0]}"
    cell = Cell(f"base_ind_under_W{W}_T{TOP_ME}_B{BTM_ME}_NT{NT}{tp_suffix}",
                "base_ind_under", params)
    cell.inst(
        vias(Length=W + NT * P, Width=WX, TOP_ME=top_chosen, BTM_ME=btm_chosen,
             process=process),
        (0.0, -W), "R0")
    tip_x0 = 0.0
    if tip_pad_width is not None and tip_pad_anchor == "high":
        tip_x0 = WX - tip_pad_width
    cell.inst(
        vias(Length=W, Width=WX if tip_pad_width is None else tip_pad_width,
             TOP_ME=TOP_ME, BTM_ME=top_chosen,
             process=process),
        (tip_x0, NT * P - W), "R0")
    cell.inst(
        vias(Length=W, Width=WX, TOP_ME=TOP_ME, BTM_ME=top_chosen,
             process=process),
        (0.0, -W), "R0")
    return cell


def base_ind_hud_cross(
    OD: float = 60.0,
    W: float = 2.0,
    S: float = 2.0,
    OPENING: float = 10.0,
    TOP_ME: str = "9",
    BTM_ME: str = "9",
    under: bool = True,
    dummy: bool = True,
    DUMMYL: str = "RFVLSI",
    process: ProcessRuleContext | None = None,
    PITCH: float | None = None,
    LEG2_BTM_ME: str | None = None,
    bridge_side: str = "left",
    chamfer_bias: int = 0,
    corridor: tuple[float, int] | None = None,
    outer_corridor: tuple[float, int] | None = None,
    landing: tuple[float, int] | None = None,
) -> Cell:
    """One inductor turn with crossover: base_oct + two base_xfm_cross legs.

    ``landing`` (M1.6, D4): the ``(OD, chamfer_bias)`` of the NEXT-INNER ring
    the far pads land on (default: ``OD - 2*pitch`` with this turn's own
    bias, exact whenever no staircase is active). Both endpoint pads must lie
    entirely on their ring's flat: the far pad's top edge stays at or below
    the landing ring's BA and the near pad's at or below this ring's BA --
    past BA the ring's outer boundary turns 45 degrees inward and the pad's
    outer corner would hang in free space (the review's D4 landing predicate).
    A pad is trimmed to fit and refused below the metal's min_width.

    ``outer_corridor`` (issue 04, second mechanism): the NEXT-OUTER
    ring's ``(OD, chamfer_bias)``, bounding the legs' NEAR endpoint pads
    (the ones on THIS turn's own arm at ``OD/2``) the same way
    ``corridor`` bounds the far ones -- at deep small turns the near
    pad's corner pokes into the outer neighbour's inner chamfer.
    ``None`` (outermost turn, or callers without a neighbour) skips it.

    ``corridor`` (design-region issue 04, default ``None`` -> this turn's
    own ``(OD, chamfer_bias)``): the ring whose INNER 45-degree chamfer
    bounds the crossover legs' far endpoint pads. At small ring OD the
    octagon flat (BA ~ 0.207*OD) is shorter than the W x W pad's
    tangential extent, so the pad's outer corner would poke past the
    flat into that ring's chamfer corridor -- a real min_space violation
    (the OD x NT extended sweep's VIOL class). The far pad Length is
    trimmed just enough for its corner to clear the corridor line
    ``x + y = OD_c/2 + BA_c - C - W`` by the effective spacing floor;
    geometry that already clears (every fixed-large-OD sweep cell) is
    byte-identical. An interleaved caller (xfm_il) passes the ring that
    physically sits between this turn and its bridge target -- the OTHER
    winding's ring at ``OD - PITCH`` with its own merged-order bias --
    since that is the ring whose chamfer the far pad actually crowds.

    ``chamfer_bias`` (six-family tight-spacing clearance, default 0 =
    byte-identical) is forwarded to the ring's ``base_oct`` only -- the
    crossover legs live on the cardinal arms and are unaffected; see
    ``chamfer_staircase_delta``.

    Faithful quirks of the reference source kept here:
    * the reference .il hardcodes the ring on MET 9; this port derives it
      from TOP_ME (metal generalization, user-directed -- see
      KNOWN_DEVIATIONS).
    * BTM_ME only feeds the dead ``via_to_next`` computation and never
      reaches the crosses (the underpass always uses TOP_ME-1);
    * the same-layer mirrored cross passes TOP_ME==BTM_ME, which is what
      suppresses its via cuts.

    ``LEG2_BTM_ME`` (xfm_il ticket 02d, default ``None`` -> byte-identical
    to every existing caller): the reference "hud cross" is NOT a single
    clean SL-1 underpass -- it is TWO legs that connect the turn's two
    (top/bottom-mirrored) crossover tips independently (ind_sym's
    "symmetric" topology; dropping either leg splits the winding into
    multiple disconnected components, confirmed empirically). The first leg
    (``cross1``) always dips to TOP_ME-1 for its whole span. The *second*
    leg (``cross2``) has its ``BTM_ME`` controlled by this parameter:
    ``None`` reproduces the reference quirk of drawing it
    ``TOP_ME==BTM_ME`` (same layer as the ring), which for the default
    single-winding pitch spans empty inter-turn space harmlessly. Ticket 02
    first tried routing it to TOP_ME-1 instead (a boolean
    ``crossunder_sl1_only`` flag, now retired) so it wouldn't collide with
    an interleaved neighbour's ring sitting in that doubled-pitch gap -- but
    ticket 02d's user-driven visual review found that with BOTH legs on the
    SAME SL-1 layer, an interleaved xfm_il's two windings' own SL-1
    footprints (each winding's leg1 AND leg2) can collide with each other
    across the shared inter-turn gap (the "same-layer crossing short" the
    ``crossunder_sl1_only`` fix never actually addressed). ``LEG2_BTM_ME``
    generalizes the old flag to an explicit metal: xfm_il now passes
    TOP_ME-2 so leg2's diagonal (``diag_met`` follows ``BTM_ME`` when
    ``top=False``, so this single ``BTM_ME`` change moves its diagonal *and*
    both endpoint via blocks) lands on a THIRD, distinct layer from leg1's
    SL-1 -- each leg gets its own plane, so the two legs of one X can never
    touch each other's layer, and the interleaved neighbour's own two legs
    (also split SL-1/SL-2) stay separable per layer too (see xfm_il's
    docstring for the per-layer "chained X" guard this enables). The
    resulting via stack at leg2's two endpoints spans every metal from
    LEG2_BTM_ME up through TOP_ME inclusive (``vias`` draws every
    intervening layer) -- e.g. TOP_ME=SL, LEG2_BTM_ME=SL-2 lands a small
    transit pad on SL-1 at each of leg2's own endpoints (not leg1's), which
    is legal and necessary (the via stack's own intermediate landing, not a
    stray short) as long as it stays clear of the OTHER winding's SL-1
    footprint at that location -- exactly what the per-layer guard checks.
    Connectivity is unaffected either way (both legs still complete their
    own turn-to-turn connection, verified by the same merged-region
    connectivity check ind_sym's own tests use).

    ``PITCH`` (xfm_il ticket 01) is the radial distance from this turn's own
    ring edge (at ``OD/2``) to the *next* same-winding turn's ring edge (at
    ``OD/2 - PITCH``, i.e. the ring drawn with ``OD - 2*PITCH`` by the
    caller). ``None`` (the default) falls back to ``W + S`` -- the reference
    turn-to-turn pitch -- which makes every derived value below identical to
    the pre-``PITCH`` formulas (byte-identical geometry). The crossunder
    bridge (base_oct's LOP opening + both base_xfm_cross legs) spans exactly
    one ``PITCH`` so its far endpoint vias land flush on the next turn's ring
    regardless of how far that ring actually sits -- the effective local gap
    fed to those legs is ``cross_gap = PITCH - W`` (recovering the original
    ``S`` when ``PITCH`` is left at its default), not the raw ``S`` argument,
    which after this change only seeds the default pitch and the cell
    name/params for identification. A caller that widens ``PITCH`` past
    ``W + S`` (xfm_il: ``2*(W+S)`` so two interleaved windings' turns share a
    lattice) gets a bridge that reaches across the intervening ring without
    a second hardcoded span.

    ``bridge_side`` (xfm_il ticket 02b, default (and now only) ``"left"``
    -> byte-identical to every existing caller): this turn's OWN crossunder
    always emits from its local ``-OD/2`` arm (the ``LOP`` opening,
    ``cross_endpoint_offset``-sized) and reserves the opposite ``+OD/2`` arm
    (``ROP``) for whatever else the caller needs there (the P1/N1 lead-pair
    gap on the outermost turn; the *receiving* end of the previous turn's
    bridge, under the alternating ``_ind_ring_turns`` orientation zigzag, on
    a middle turn). Ticket 02b briefly added a ``bridge_side="right"``
    variant (mirroring the whole mapping so the crossunder and the caller's
    own ``OPENING`` shared the SAME physical arm) for xfm_il's same-side
    bridge stacking -- ticket 02c retired it: co-locating a winding's own
    bridge with its own lead/escape opening on one arm leaves the OTHER arm
    fully closed, so the ring never splits at that far tip and P1/N1 (or
    P2/N2) end up on the SAME closed-loop polygon -- a direct short, not a
    winding (see xfm_il's docstring and the ticket 02c handoff). xfm_il now
    gets same-side stacking by keeping every bridge on this SAME ``"left"``
    arm while the caller's OWN opening (lead pair or escape) stays on
    ``ROP`` as always -- i.e. bridge and opening are opposite arms of the
    SAME physical side once the whole sub-winding is placed/mirrored, never
    co-located. The parameter is kept (rather than dropped outright) only
    so ``_ind_ring_turns``'s own ``"alternate"``/``"left"`` selector has a
    single explicit value to thread through; no caller may pass anything
    else (fail closed)."""
    if bridge_side != "left":
        raise PortError(
            f"base_ind_hud_cross: bridge_side {bridge_side!r} must be "
            f"'left' (ticket 02c retired 'right' -- co-locating a "
            f"winding's own bridge with its own lead/escape opening on one "
            f"arm collapses the ring into a single closed loop and shorts "
            f"its two terminals; see the base_ind_hud_cross docstring)"
        )
    params = {
        "OD": OD,
        "W": W,
        "S": S,
        "OPENING": OPENING,
        "landing": landing,
        "TOP_ME": TOP_ME,
        "BTM_ME": BTM_ME,
        "under": under,
    }
    if process is not None:
        params["process"] = process.profile_id
    if PITCH is not None:
        params["PITCH"] = PITCH
    if LEG2_BTM_ME is not None:
        params["LEG2_BTM_ME"] = LEG2_BTM_ME
    if chamfer_bias:
        params["chamfer_bias"] = chamfer_bias
    cb_suffix = f"_cb{chamfer_bias}" if chamfer_bias else ""
    if corridor is not None:
        params["corridor"] = corridor
        cb_suffix += f"_co{corridor[0]}b{corridor[1]}"
    if outer_corridor is not None:
        params["outer_corridor"] = outer_corridor
        cb_suffix += f"_oc{outer_corridor[0]}b{outer_corridor[1]}"
    cell = Cell(
        f"base_ind_hud_cross_OD{OD}_W{W}_S{S}_O{OPENING}_T{TOP_ME}{cb_suffix}",
        "base_ind_hud_cross",
        params,
    )
    pitch = W + S if PITCH is None else PITCH
    cross_gap = pitch - W
    # Far-pad corridor trim (issue 04, see the docstring): bound the far
    # endpoint pad's outer corner against the corridor ring's inner
    # chamfer line using the SAME quantized helpers base_oct_quad draws
    # with (call-site duplication would drift; these are one-liners on
    # the shared grid functions).
    far_pad_len = None
    if process is not None:
        cor_od, cor_bias = (OD, chamfer_bias) if corridor is None else corridor
        floor_sp = _effective_min_spacing(_metal_index(TOP_ME), W, process)
        y0 = xfm_cross_far_pad_y0(
            cross_gap, W, 0.0, _metal_index(TOP_ME), process)
        # far pad outer |x| = OD/2 - pitch; corner constraint:
        # x_outer + y_top <= inner chamfer intercept - floor*sqrt(2)
        y_allow = octagon(cor_od, W, cor_bias).inner_chamfer_intercept \
            - floor_sp * math.sqrt(2.0) - (OD / 2 - pitch)
        if y_allow < y0 + W - 1e-9:
            far_pad_len = floortogrid(y_allow - y0)
            # Floor: the trimmed pad is still a drawn metal rect, so it
            # must respect the metal's own min_width; vias() additionally
            # fails closed on enclosure / via-array legality.
            if far_pad_len < _pad_trim_floor(TOP_ME, process):
                raise PortError(
                    f"base_ind_hud_cross: OD={OD}, W={W}, PITCH={pitch} on "
                    f"{_metal_name(_metal_index(TOP_ME))}: the crossover "
                    f"far pad would need trimming to {far_pad_len} um to "
                    f"clear the corridor ring OD={cor_od}'s inner chamfer "
                    f"by {floor_sp} um -- the turn is too small to host "
                    f"the bridge landing; increase OD or reduce W/NT"
                )
    if process is not None:
        # far pad flat containment (D4): y_top = y0 + length <= landing ring's BA
        land_od, land_bias = (OD - 2 * pitch, chamfer_bias) if landing is None else landing
        far_flat = floortogrid(octagon(land_od, W, land_bias).BA - y0)
        if far_flat < (W if far_pad_len is None else far_pad_len) - 1e-9:
            far_pad_len = far_flat
            if far_pad_len < _pad_trim_floor(TOP_ME, process):
                raise PortError(
                    f"base_ind_hud_cross: OD={OD}, W={W}, PITCH={pitch} on "
                    f"{_metal_name(_metal_index(TOP_ME))}: the crossover "
                    f"far pad (starting {y0} um from the axis) does not fit "
                    f"the landing ring OD={land_od}'s flat (BA="
                    f"{octagon(land_od, W, land_bias).BA} um) -- the pad "
                    f"would hang off the ring; increase OD or reduce W/NT/S"
                )
    near_pad_len = None
    if process is not None and outer_corridor is not None:
        oc_od, oc_bias = outer_corridor
        floor_sp = _effective_min_spacing(_metal_index(TOP_ME), W, process)
        y0 = xfm_cross_far_pad_y0(
            cross_gap, W, 0.0, _metal_index(TOP_ME), process)
        # near pad outer |x| = OD/2 (this turn's own arm)
        y_allow_o = octagon(oc_od, W, oc_bias).inner_chamfer_intercept \
            - floor_sp * math.sqrt(2.0) - OD / 2
        if y_allow_o < y0 + W - 1e-9:
            near_pad_len = floortogrid(y_allow_o - y0)
            if near_pad_len < _pad_trim_floor(TOP_ME, process):
                raise PortError(
                    f"base_ind_hud_cross: OD={OD}, W={W}, PITCH={pitch} on "
                    f"{_metal_name(_metal_index(TOP_ME))}: the crossover "
                    f"near pad would need trimming to {near_pad_len} um "
                    f"to clear the outer ring OD={oc_od}'s inner chamfer "
                    f"by {floor_sp} um -- the turn is too small to host "
                    f"the bridge landing; increase OD or reduce W/NT"
                )
    # .il original: LOP = OOCH + roundtogrid(2*sqrt(2.0)-S) + 0.01 with
    # OOCH from roundtogrid'd C/C2 -- replaced by the exact cross endpoint
    # edge so the ring arm ends flush with the crossover legs (alignment
    # correction, see cross_endpoint_offset and KNOWN_DEVIATIONS).
    if process is not None:
        # near pad flat containment (D4): on this turn's own arm, y_top <= own BA
        y0_near = xfm_cross_far_pad_y0(cross_gap, W, 0.0, _metal_index(TOP_ME), process)
        near_flat = floortogrid(octagon(OD, W, chamfer_bias).BA - y0_near)
        if near_flat < (W if near_pad_len is None else near_pad_len) - 1e-9:
            near_pad_len = near_flat
            if near_pad_len < _pad_trim_floor(TOP_ME, process):
                raise PortError(
                    f"base_ind_hud_cross: OD={OD}, W={W}, PITCH={pitch} on "
                    f"{_metal_name(_metal_index(TOP_ME))}: the crossover "
                    f"near pad (starting {y0_near} um from the axis) does "
                    f"not fit this ring's flat (BA="
                    f"{octagon(OD, W, chamfer_bias).BA} um) -- the pad would "
                    f"hang off the ring; increase OD or reduce W/NT/S"
                )
    facing_val = cross_endpoint_offset(W, cross_gap, _metal_index(TOP_ME), process)
    lop_val, rop_val = facing_val, OPENING
    cross1_xy, cross1_or = (-OD / 2, 0.0), "R0"
    cross2_xy, cross2_or = (-OD / 2 + pitch + W, 0.0), "MY"
    cell.inst(
        base_oct(
            OD=OD,
            W=W,
            LOP=lop_val,
            ROP=rop_val,
            MET=_metal_index(TOP_ME),
            process=process,
            chamfer_bias=chamfer_bias,
        ),
        (0.0, 0.0),
        "R0",
    )
    if under:
        cell.inst(
            base_xfm_cross(
                WI=cross_gap,
                WO=W,
                S=0.0,
                TOP_ME=_metal_index(TOP_ME),
                BTM_ME=_metal_below(TOP_ME, process),
                dummy=dummy,
                process=process,
                far_pad_length=far_pad_len,
                near_pad_length=near_pad_len,
            ),
            cross1_xy,
            cross1_or,
        )
        cell.inst(
            base_xfm_cross(
                WI=cross_gap,
                WO=W,
                S=0.0,
                TOP_ME=_metal_index(TOP_ME),
                BTM_ME=(_metal_index(LEG2_BTM_ME) if LEG2_BTM_ME is not None
                       else _metal_index(TOP_ME)),
                viat=False,
                viad=False,
                dummy=dummy,
                process=process,
                # cross2 is MY-mirrored: ITS pad on the smaller ring is
                # the near one, and its own-arm pad the far one (see
                # base_xfm_cross's docstring).
                near_pad_length=far_pad_len,
                far_pad_length=near_pad_len,
            ),
            cross2_xy,
            cross2_or,
        )
    return cell
