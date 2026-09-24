# Auto-split from pcell_inductor_port_clean.py (wrap-up P1,
# 2026-07-28): verbatim segment move, no behavior change. The
# facade module re-exports every name; see its docstring for
# provenance and the KNOWN_DEVIATIONS record.
from __future__ import annotations

from ic_opt.em.pcell._pcell_core import (
    Cell,
    PortError,
    ProcessRuleContext,
    _metal_index,
    _metal_name,
    _xfm_order_ports,
    finalize_emx_ports,
)
from ic_opt.em.pcell._pcell_guards import _xfm_net_short
from ic_opt.em.pcell._pcell_ind_sym import (
    _compact_two_turn_winding,
    _ind_ct_adjacency_guard,
    ind_sym,
)
from ic_opt.em.pcell._pcell_straight_extension import (
    extend_straight_x,
)
from ic_opt.em.pcell._pcell_xfm_bs import (
    _bs_center_tap,
    _bs_winding,
    check_stacked_overlap,
    ct_lead_to_edge,
)
from ic_opt.em.pcell.fixture import (
    GroundFixtureConfig,
    _drawing_bbox_um,
    add_ground_fixture,
)
from ic_opt.em.pcell.stack import builds_on_profile_stack


@builds_on_profile_stack
def xfm_ms(
    OD_S: float = 100.0,
    OD_M: float = 76.0,
    W_S: float = 6.0,
    W_M: float = 3.0,
    OPENING_S: float = 8.0,
    OPENING_M: float = 6.0,
    LEAD_S: float = 20.0,
    LEAD_M: float = 15.0,
    NT_M: int = 3,
    S_M: float = 2.0,
    CENTER_SPACING: float = 0.0,
    SINGLE_ME: str = "AP",
    MULTI_ME: str = "10",
    CT_P_ME: str | None = None,
    CT_S_ME: str | None = None,
    ground_fixture: GroundFixtureConfig | None = None,
    process: ProcessRuleContext | None = None,
    STRAIGHT_EXTENSION: float = 0.0,
    PORT_SPACING_S: float | None = None,
    PORT_SPACING_M: float | None = None,
) -> Cell:
    """Impedance-transforming transformer: a single-turn winding on the higher
    metal (SINGLE_ME) broadside over a multi-turn winding on the lower metal
    (MULTI_ME). Its crossover keeps leg2 on the winding-body plane and
    puts leg1 on the next lower conductor, matching standalone ind_sym.
    It therefore needs one crossunder layer, not the two-layer descent
    used by the separate interleaved transformer. Compact
    NT_M=2 additionally uses rule-sized rectangular via landings and searches
    the smallest symmetric straight-45-straight lane offsets that preserve
    W/S without changing OD. Single strictly above multi so the multi
    crossover never reaches the single plane. Independent OD_S/OD_M and
    CENTER_SPACING (like xfm_bs). Single reuses _bs_winding; multi reuses
    ind_sym (instanced R0 so it opens right, outward). Clean-room
    composition; N28 default AP/M10/M9 is legal (AP coil via-free, M10
    crossover VIA9).

    Optional per-winding center taps (M13 ticket 06). Port-side naming:
    the P side (ports P1/N1, tap CTP via CT_P_ME) is the SINGLE-turn
    winding, tapped at its closed column like xfm_bs; the S side (ports
    P2/N2, tap CTS via CT_S_ME) is the MULTI-turn winding, tapped through
    ind_sym's CT_ME path — so CT_S_ME obeys the same N1 adjacency rule as
    the inductor (at least two levels below MULTI_ME; the crossunder
    occupies MULTI_ME-1). Each winding (plus its tap) builds in its own
    sub-cell and the layer-complete _xfm_net_short gate fails closed on
    any overlap between the two nets.

    STRAIGHT_EXTENSION (same contract as xfm_bs) adds the same total X width
    to each winding about its OWN centre, in non-negative 0.01 um steps;
    crossovers, vias, leads and CT keep their dimensions. Zero preserves
    legacy geometry."""
    si, mi = _metal_index(SINGLE_ME), _metal_index(MULTI_ME)
    if si <= mi:
        raise PortError(
            f"xfm_ms: single-turn metal ({_metal_name(si)}) must be strictly "
            f"above the multi-turn metal ({_metal_name(mi)})")
    if mi < 3:
        raise PortError(
            f"xfm_ms: multi-turn metal {_metal_name(mi)} needs a crossunder "
            f"layer below at M2 or above; M1 is reserved for the ground fixture")
    if NT_M < 2:
        raise PortError(
            f"xfm_ms: NT_M={NT_M} is not multi-turn; use xfm_bs for 1+1")
    if CT_P_ME is not None and _metal_index(CT_P_ME) >= si:
        raise PortError(
            f"xfm_ms: CT_P metal {_metal_name(_metal_index(CT_P_ME))} must "
            f"be below the single-turn winding plane {_metal_name(si)}")
    if CT_S_ME is not None:
        _ind_ct_adjacency_guard("xfm_ms", MULTI_ME, CT_S_ME, process=process)
    check_stacked_overlap("xfm_ms", CENTER_SPACING, OD_S, OD_M)
    xS, xM = -CENTER_SPACING / 2.0, CENTER_SPACING / 2.0
    params = {"OD_S": OD_S, "OD_M": OD_M, "W_S": W_S, "W_M": W_M,
              "OPENING_S": OPENING_S, "OPENING_M": OPENING_M,
              "LEAD_S": LEAD_S, "LEAD_M": LEAD_M, "NT_M": NT_M, "S_M": S_M,
              "CENTER_SPACING": CENTER_SPACING,
              "SINGLE_ME": SINGLE_ME, "MULTI_ME": MULTI_ME,
              "CT_P_ME": CT_P_ME, "CT_S_ME": CT_S_ME}
    if process is not None:
        params["process"] = process.profile_id
    cell = Cell(f"xfm_ms_S{SINGLE_ME}_M{MULTI_ME}_NT{NT_M}", "xfm_ms", params)
    # single-turn winding on the higher metal (opens LEFT, outward); built
    # first because its outer extent sizes the multi winding's tap lead.
    sing = Cell("xfm_ms_single", "xfm_ms_single", {})
    _bs_winding(sing, xS, OD_S, W_S, OPENING_S, LEAD_S, SINGLE_ME, "left",
                "P1", "N1", process, port_spacing=PORT_SPACING_S)
    sing_xmin, _, sing_xmax, _ = _drawing_bbox_um(sing)
    # The multi tap (ind_sym's CT) exits right for even NT_M, left for odd;
    # its lead runs on to the single winding's outer edge on that side so
    # the tap port stays peripheral (2026-09-22).
    multi_ct_lead = None
    if CT_S_ME is not None:
        multi_ct_lead = ct_lead_to_edge(
            LEAD_M, xM + OD_M / 2.0, sing_xmax) if NT_M % 2 == 0 else ct_lead_to_edge(
            LEAD_M, xM - OD_M / 2.0, sing_xmin)
    mult = Cell("xfm_ms_multi", "xfm_ms_multi", {})
    multi_ports = ["P2", "N2"] if CT_S_ME is None else ["P2", "N2", "CTS"]
    if process is not None and NT_M == 2 and CT_S_ME is None:
        multi = _compact_two_turn_winding(
            OD=OD_M,
            W=W_M,
            OPENING=OPENING_M,
            LEAD=LEAD_M,
            S=S_M,
            top_met=mi,
            port_order=multi_ports,
            process=process,
            family="xfm_ms",
            # port contract 2026-09-21: this sub-winding's P1/N1 roles are
            # xfm_ms's OWN P2/N2 (or CTS), not ind_sym's -- see ind_sym's
            # semantic_port_roles docstring.
            semantic_port_roles=False,
            port_spacing=PORT_SPACING_M,
        )
        multi = extend_straight_x(multi, STRAIGHT_EXTENSION, process=process)
    else:
        multi = ind_sym(
            OD=OD_M,
            W=W_M,
            OPENING=OPENING_M,
            LEAD=LEAD_M,
            S=S_M,
            NT=NT_M,
            TOP_ME=str(mi),
            BTM_ME=str(mi - 1),
            CT_ME=CT_S_ME,
            port_order=multi_ports,
            process=process,
            # Reference ind scheme (user directive, design-region issue 03,
            # 2026-07-31): the multi coil IS an ind -- its crossover keeps
            # leg2 on the coil's own layer (crossing OVER) while leg1 dives
            # ONE level (crossing UNDER), exactly like standalone ind_sym.
            # The mi-2 dual-layer routing that shipped with e7f505d was an
            # il-only remedy (interleaved-band collisions) carried over
            # without necessity -- it spent an extra routing layer and
            # longer via stacks purely to sidestep same-layer crossing
            # correctness that ind already implements.
            LEG2_BTM_ME=None,
            STRAIGHT_EXTENSION=STRAIGHT_EXTENSION,
            # port contract 2026-09-21: see the compact branch's own note
            # above -- same reason, same override.
            semantic_port_roles=False,
            PORT_SPACING=PORT_SPACING_M,
            CT_LEAD=multi_ct_lead,
        )
    # (port contract 2026-09-21) mult.inst(multi, ...) alone carries
    # multi's ports (P2/N2, CTS) up through mult -- the SAME cell.inst()
    # integer chain its shapes already travel -- so no separate transplant
    # loop re-shifts an already-rounded label_xy_um by a second, unrounded
    # xM offset any more (this stage's own mechanism-3 fix); xfm_ms's own
    # finalize_emx_ports() call below composes it correctly regardless of
    # nesting depth.
    mult.inst(multi, (xM, 0.0), "R0")
    if CT_P_ME is not None:
        # single opens left -> closed column RIGHT; tap exits right, on to
        # the multi winding's outer edge. The multi is already extended
        # (inside ind_sym) while the single is extended below, after this
        # tap: size the reach against the multi's UNextended edge so both
        # tips move by the same half extension and stay aligned.
        multi_edge = _drawing_bbox_um(mult)[2] - STRAIGHT_EXTENSION / 2.0
        _bs_center_tap(sing, xS, OD_S / 2.0, W_S,
                       ct_lead_to_edge(LEAD_S, xS + OD_S / 2.0, multi_edge),
                       SINGLE_ME, CT_P_ME, "right", "CTP", process)
    sing = extend_straight_x(sing, STRAIGHT_EXTENSION, center_x_um=xS, process=process)
    if STRAIGHT_EXTENSION:
        cell.params["STRAIGHT_EXTENSION"] = sing.params["STRAIGHT_EXTENSION"]
        cell.name += f"_SX{sing.params['STRAIGHT_EXTENSION']:g}"
    _xfm_net_short("xfm_ms", sing, mult)
    cell.inst(sing, (0.0, 0.0), "R0")
    cell.inst(mult, (0.0, 0.0), "R0")
    # (port contract 2026-09-21) replaces the old
    # cell.emx_ports.extend(sing.emx_ports)/extend(mult.emx_ports).
    cell.emx_ports = finalize_emx_ports(cell)
    _xfm_order_ports(cell)
    if ground_fixture is not None:
        add_ground_fixture(cell, ground_fixture, process)
    return cell
