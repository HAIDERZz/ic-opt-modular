# Auto-split from pcell_inductor_port_clean.py (wrap-up P1,
# 2026-07-28): verbatim segment move, no behavior change. The
# facade module re-exports every name; see its docstring for
# provenance and the KNOWN_DEVIATIONS record.
from __future__ import annotations

from ic_opt.em.pcell._pcell_core import (
    _EPS,
    Cell,
    PortError,
    ProcessRuleContext,
    _metal_index,
    _pin,
    _xfm_order_ports,
    finalize_emx_ports,
    vias,
)
from ic_opt.em.pcell._pcell_guards import (
    _check_opening,
    _check_trace_rules,
    _xfm_net_short,
)
from ic_opt.em.pcell._pcell_primitives import (
    base_lead,
    base_lead_pair,
    base_oct,
)
from ic_opt.em.pcell._pcell_straight_extension import (
    extend_straight_x,
)
from ic_opt.em.pcell.fixture import (
    GroundFixtureConfig,
    _drawing_bbox_um,
    add_ground_fixture,
)
from ic_opt.em.pcell.stack import builds_on_profile_stack

# ---------------------------------------------------------------------------
# xfm_bs: broadside single-turn two-layer transformer (M7R / M7R2)
# Clean-room composition of the ported primitives (no single .il models this
# device): two open-octagon windings on two different metals, independent
# OD_P/OD_S + CENTER_SPACING (M7R2), openings opposite, optional per-winding
# center tap with a geometric Region clearance, optional M1 ground fixture.
# Replaces an earlier gdsfactory placeholder.
# ---------------------------------------------------------------------------


def _bs_winding(cell, center_x, OD, W, OPENING, LEAD, MET, side,
                p_txt, n_txt, process, port_spacing=None):
    """Single-turn open octagon on MET at center_x + P/N lead pair; 2 ports.

    ``side='right'`` opens/exits +x; ``side='left'`` opens/exits -x
    (the lead pair is mirrored with MY). xfm_bs uses left for primary.

    Both ports register on base_lead_pair's own two base_lead legs (port
    contract 2026-09-21): each leg's far tip lands at
    ``center_x +/- OD/2 +/- LEAD`` purely from the R0/MY instance
    orientation and origin below -- the same "MY negates local x" identity
    base_lead_pair's own docstring proves -- so no independent
    ``px = center_x ... `` recompute feeds add_emx_port here any more.
    """
    _check_opening(OD, W, OPENING, "_bs_winding")
    met = _metal_index(MET)
    _check_trace_rules(W, OPENING, LEAD, met, process, "_bs_winding")
    pin = _pin(met, process)
    if side == "right":
        cell.inst(base_oct(OD=OD, W=W, LOP=0.0, ROP=OPENING, MET=met,
                           process=process), (center_x, 0.0), "R0")
        cell.inst(base_lead_pair(W=W, OPENING=OPENING, LEAD=LEAD + W,
                                 TOP_ME=str(met), LEAD_ME=str(met),
                                 P1TXT=p_txt, N1TXT=n_txt, process=process,
                                 port_metal=met, port_label_layer=pin,
                                 PORT_SPACING=port_spacing),
                  (center_x + OD / 2 - W, 0.0), "R0")
    else:
        cell.inst(base_oct(OD=OD, W=W, LOP=OPENING, ROP=0.0, MET=met,
                           process=process), (center_x, 0.0), "R0")
        cell.inst(base_lead_pair(W=W, OPENING=OPENING, LEAD=LEAD + W,
                                 TOP_ME=str(met), LEAD_ME=str(met),
                                 P1TXT=p_txt, N1TXT=n_txt, process=process,
                                 port_metal=met, port_label_layer=pin,
                                 PORT_SPACING=port_spacing),
                  (center_x - OD / 2 + W, 0.0), "MY")


def _bs_center_tap(cell, center_x, radius, W, LEAD, WINDING_ME, CT_ME, side,
                   txt, process):
    """W x W via stack at the winding's closed column (center_x ± radius) +
    CT lead on CT_ME exiting outward; registers a CT port.

    ``side='left'`` taps the closed column at center_x-radius (exits -x);
    ``side='right'`` taps at center_x+radius (exits +x).

    The lead's own base_lead call registers the port (port contract
    2026-09-21): ``side='right'`` sits at the lead's default far tip
    (``lead_x + L == center_x+radius+LEAD``); ``side='left'`` sits at the
    NEAR end instead (``port_x_um=0.0``, since here ``lead_x`` -- not
    ``lead_x + L`` -- is the tip, algebraically: the tap sits at the far
    end of the lead's own span when the lead is built outward-to-inward).
    """
    wm, cm = _metal_index(WINDING_ME), _metal_index(CT_ME)
    if side == "left":
        tap_x = center_x - radius
        lead_x = tap_x - LEAD
        port_x_um = 0.0
    else:
        tap_x = center_x + radius - W
        lead_x = tap_x
        port_x_um = None
    cell.inst(vias(Length=W, Width=W, TOP_ME=wm, BTM_ME=cm, process=process),
              (tap_x, -W / 2), "R0")
    cell.inst(
        base_lead(L=LEAD + W, W=W, TOP_ME=str(cm), BTM_ME=str(cm),
                 process=process,
                 port_name=txt, port_logical_name=txt, port_metal=cm,
                 port_label_layer=_pin(cm, process), port_x_um=port_x_um),
        (lead_x, -W / 2), "R0")


def ct_lead_to_edge(lead: float, ring_edge_x: float, device_edge_x: float) -> float:
    """Tap lead length that reaches ``device_edge_x`` (the other winding's
    outer extent on the tap's exit side) from the ring edge at
    ``ring_edge_x``, never shorter than the winding's own ``lead``."""
    return max(lead, abs(device_edge_x - ring_edge_x))


def check_stacked_overlap(where: str, center_spacing: float, od_a: float,
                          od_b: float) -> None:
    """A stacked transformer couples through the vertical overlap of its
    two windings: the product caps CENTER_SPACING at (od_a+od_b)/4 (user
    decision 2026-07-17, M13 ticket 10 -- mirrored by the config
    validators). Enforced here too so the pcell can never draw two
    separated rings with facing taps, a form real devices do not take
    (user directive 2026-09-22)."""
    bound = (od_a + od_b) / 4.0
    if center_spacing > bound + _EPS:
        raise PortError(
            f"{where}: CENTER_SPACING={center_spacing} exceeds (OD_a + OD_b)/4 "
            f"= {bound:.3f}; beyond that the windings lose the overlap that "
            "makes this a transformer")


@builds_on_profile_stack
def xfm_bs(
    OD_P: float = 90.0,
    OD_S: float = 90.0,
    W_P: float = 4.0,
    W_S: float = 4.0,
    OPENING_P: float = 8.0,
    OPENING_S: float = 8.0,
    LEAD_P: float = 20.0,
    LEAD_S: float = 20.0,
    CENTER_SPACING: float = 0.0,
    PRI_ME: str = "10",
    SEC_ME: str = "9",
    CT_P_ME: str | None = None,
    CT_S_ME: str | None = None,
    ground_fixture: GroundFixtureConfig | None = None,
    process: ProcessRuleContext | None = None,
    STRAIGHT_EXTENSION: float = 0.0,
    PORT_SPACING_P: float | None = None,
    PORT_SPACING_S: float | None = None,
) -> Cell:
    """Broadside single-turn two-layer transformer with independent primary/
    secondary outer diameters (OD_P/OD_S) and adjustable center-to-center
    distance (CENTER_SPACING). STRAIGHT_EXTENSION adds the same total X width
    to each winding about its OWN centre, in non-negative 0.01 um steps;
    vias, leads and CT keep their dimensions. Zero preserves legacy geometry.
    The base winding keeps the conventions of the single-turn transformer it
    replaces. Primary at (-CENTER_SPACING/2,0) opens LEFT (outward); secondary
    at (+CENTER_SPACING/2,0) opens RIGHT (outward); ports P1/N1 left, P2/N2
    right. Larger CENTER_SPACING moves both coil centers and lead terminals
    farther apart. Optional per-winding center tap on a lower metal. Each
    winding (plus its tap) is built in its own sub-cell and the
    layer-complete net gate (_xfm_net_short, shared with xfm_balun) fails
    closed on ANY overlap between the two nets — tap leads and winding
    leads included (M13 ticket 05; replaces the old box-only clearance).
    Clean-room composition of ported primitives; replaces the gdsfactory
    placeholder."""
    pri_i, sec_i = _metal_index(PRI_ME), _metal_index(SEC_ME)
    if pri_i == sec_i:
        raise PortError(
            f"xfm_bs: primary and secondary must be different metals "
            f"(got M{pri_i} for both)")
    check_stacked_overlap("xfm_bs", CENTER_SPACING, OD_P, OD_S)
    xP, xS = -CENTER_SPACING / 2.0, CENTER_SPACING / 2.0
    if CT_P_ME is not None and _metal_index(CT_P_ME) >= pri_i:
        raise PortError(
            f"xfm_bs: CT_P metal M{_metal_index(CT_P_ME)} must be below "
            f"the primary M{pri_i}")
    if CT_S_ME is not None and _metal_index(CT_S_ME) >= sec_i:
        raise PortError(
            f"xfm_bs: CT_S metal M{_metal_index(CT_S_ME)} must be below "
            f"the secondary M{sec_i}")
    params = {"OD_P": OD_P, "OD_S": OD_S, "W_P": W_P, "W_S": W_S,
              "OPENING_P": OPENING_P, "OPENING_S": OPENING_S,
              "LEAD_P": LEAD_P, "LEAD_S": LEAD_S,
              "CENTER_SPACING": CENTER_SPACING, "PRI_ME": PRI_ME,
              "SEC_ME": SEC_ME, "CT_P_ME": CT_P_ME, "CT_S_ME": CT_S_ME}
    if process is not None:
        params["process"] = process.profile_id
    cell = Cell(f"xfm_bs_P{OD_P}_S{OD_S}_C{CENTER_SPACING}_{PRI_ME}{SEC_ME}",
                "xfm_bs", params)
    pri = Cell("xfm_bs_pri", "xfm_bs_pri", {})
    _bs_winding(pri, xP, OD_P, W_P, OPENING_P, LEAD_P, PRI_ME, "left",
                "P1", "N1", process, port_spacing=PORT_SPACING_P)
    sec = Cell("xfm_bs_sec", "xfm_bs_sec", {})
    _bs_winding(sec, xS, OD_S, W_S, OPENING_S, LEAD_S, SEC_ME, "right",
                "P2", "N2", process, port_spacing=PORT_SPACING_S)
    # Each tap exits toward the OTHER winding; its lead runs on to that
    # winding's outer edge (never shorter than LEAD), so the tap port is a
    # true peripheral port and its ground stub keeps the designed length
    # instead of an M1 strip under the other winding (2026-09-22).
    if CT_P_ME is not None:
        # primary opens left -> closed column RIGHT; tap exits right
        _bs_center_tap(pri, xP, OD_P / 2.0, W_P,
                       ct_lead_to_edge(LEAD_P, xP + OD_P / 2.0, _drawing_bbox_um(sec)[2]),
                       PRI_ME, CT_P_ME, "right", "CTP", process)
    if CT_S_ME is not None:
        # secondary opens right -> closed column LEFT; tap exits left
        _bs_center_tap(sec, xS, OD_S / 2.0, W_S,
                       ct_lead_to_edge(LEAD_S, xS - OD_S / 2.0, _drawing_bbox_um(pri)[0]),
                       SEC_ME, CT_S_ME, "left", "CTS", process)
    pri = extend_straight_x(pri, STRAIGHT_EXTENSION, center_x_um=xP, process=process)
    sec = extend_straight_x(sec, STRAIGHT_EXTENSION, center_x_um=xS, process=process)
    if STRAIGHT_EXTENSION:
        cell.params["STRAIGHT_EXTENSION"] = pri.params["STRAIGHT_EXTENSION"]
        cell.name += f"_SX{pri.params['STRAIGHT_EXTENSION']:g}"
    _xfm_net_short("xfm_bs", pri, sec)
    cell.inst(pri, (0.0, 0.0), "R0")
    cell.inst(sec, (0.0, 0.0), "R0")
    # (port contract 2026-09-21) walks the cell.inst() chain down to both
    # windings' base_lead_pair/base_lead legs (P1/N1/P2/N2, CTP/CTS) and
    # derives emx_ports from it -- replaces the old
    # cell.emx_ports.extend(pri.emx_ports)/extend(sec.emx_ports).
    cell.emx_ports = finalize_emx_ports(cell)
    _xfm_order_ports(cell)
    if ground_fixture is not None:
        add_ground_fixture(cell, ground_fixture, process)
    return cell
