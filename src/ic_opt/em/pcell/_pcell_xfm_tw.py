# Auto-split from pcell_inductor_port_clean.py (wrap-up P1,
# 2026-07-28): verbatim segment move, no behavior change. The
# facade module re-exports every name; see its docstring for
# provenance and the KNOWN_DEVIATIONS record.
from __future__ import annotations

import math
from dataclasses import dataclass

from ic_opt.em.pcell._pcell_core import (
    _EPS,
    DBU_UM,
    GRID_UM,
    Cell,
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
    _xfm_order_ports,
    add_wide_path,
    ceiltogrid,
    chamfer,
    finalize_emx_ports,
    floortogrid,
    max_opening,
    octagon,
    snap_nm_to_grid,
    vias,
)
from ic_opt.em.pcell._pcell_guards import (
    _check_winding_segments,
    _xfm_net_short,
)
from ic_opt.em.pcell._pcell_primitives import (
    _pad_trim_floor,
)
from ic_opt.em.pcell.fixture import (
    GroundFixtureConfig,
    add_ground_fixture,
)

# ---------------------------------------------------------------------------
# xfm_tw (ticket 01: path planner + geometry kernel) -- Type 3 same-layer
# overlapping-inductor transformer ("twisted"): NR concentric rings shared
# half-and-half by P (CCW) and S (x-mirror of P, CW), crossing between rings
# through four classes of X (see .scratch/xfm-tw-twisted/spec.md, approved
# 2026-07-18). No single .il source models this device.
#
# Path planner
# ------------
# ``_tw_plan`` is a direct port of the RULE encoded in
# .scratch/xfm-tw-twisted/gen_topology.py's ``build_p``/``build_s`` (the
# connectivity authority the spec names) -- boundary slot angles, the
# odd-boundary "self-crossing" / even-boundary "P x S crossing" dive
# assignment, and S = x-mirror of P with the even-boundary dive flag
# flipped -- but expressed in SIZE-DECOUPLED terms (ring index / cardinal
# angle / desc-or-asc / dive flag) instead of gen_topology's literal
# coordinates (that script's own G/PSTUB/NSTUB/EXT constants and square-ring
# ``_walk`` are a diagram-only stand-in; this port's renderer draws real
# octagon rings sized from OD/W/S and a rule-derived slot half-width, see
# below). gen_topology.py itself asserts its K=3 output reproduces the
# user-confirmed v4 replica exactly; ``test_tw_plan_nr3_matches_v4_baseline``
# pins the same case here as the leg table quoted in the spec.
#
# gen_topology's own mirror step (``build_s``) literally mirrors every
# already-computed (x, y) point and flips the tag ("b"/"c", i.e. dive/
# same-layer) only on even-boundary legs. Working in (ring, angle) space
# instead of (x, y), mirroring x negates a point's *signed tangential
# offset* from its cardinal axis and swaps angle 0<->180 (90/270 fixed) --
# proved once here and reused by both the planner's ``_tw_mirror_segments``
# and the renderer's leg/port placement (same tables, same sign rule, so a
# mirror-derived S is geometrically guaranteed to be the P construction's
# x-mirror, never a separately-invented topology).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TwLeg:
    """One boundary-crossing leg: connects ring ``boundary-1`` (outer) and
    ring ``boundary`` (inner) at cardinal ``angle`` (0/90/180/270). ``dive``
    True -> SL_ME-1 (via-ended crossover, ``base_xfm_cross`` TOP_ME=SL/
    BTM_ME=SL-1); False -> same layer (SL_ME, no cuts)."""

    boundary: int
    direction: str  # "desc" | "asc"
    angle: int
    dive: bool


@dataclass(frozen=True)
class TwArc:
    """Same-layer (SL_ME) ring conductor on ``ring``, walking the octagon
    perimeter from ``angle_from`` to ``angle_to`` CCW (``ccw=True``) or CW."""

    ring: int
    angle_from: int
    angle_to: int
    ccw: bool


_TW_MIRROR_ANGLE = {0: 180, 90: 90, 180: 0, 270: 270}


# ---------------------------------------------------------------------------
# xfm_tw rendering geometry
#
# Renderer choice (ticket 01 "renderer 二选一"): rings are drawn as
# octagon-perimeter WIDE PATHS (explicit centerline waypoints through the
# family's own 45-degree chamfer vertices, widened via ``kdb.Path``), not by
# reusing ``base_oct_quad``/``base_oct_half``. Those two primitives are hard
# -coded for exactly ONE opening per quadrant pair (ind_sym's own single
# lead/crossunder gap per ring); xfm_tw needs FOUR independent slots per
# ring (one per cardinal edge, since boundary angles rotate through all of
# 0/90/180/270 as NR grows -- see spec.md's boundary rule), which does not
# fit that algebra without forking it per-edge. A direct centerline walk
# generalizes to any number/position of slots for free, stays visually and
# electrically consistent with the family's chamfered-octagon convention
# (same DIV=2+sqrt(2) chamfer ratio as ``base_oct_quad``), and composes
# cleanly with the explicit two-endpoint legs (``_tw_leg``, below) since
# both share one abstraction: a signed tangential offset from a ring's
# cardinal axis.
# ---------------------------------------------------------------------------

_TW_CARDINAL_UNIT = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}
_TW_TANGENT_CCW = {0: (0, 1), 90: (-1, 0), 180: (0, -1), 270: (1, 0)}


_TW_FIXED_PORT_ORDER = ["P1", "N1", "P2", "N2"]


def _tw_mirror_angle(a: int) -> int:
    return _TW_MIRROR_ANGLE[a % 360]


def _tw_plan_p(NR: int) -> list:
    """Port of gen_topology.build_p's rule, decoupled from coordinates.

    Boundary Bk (k=1..NR-1) descend slot angle = (k-1)*90 deg; ascend slot
    angle = same (k odd) or +180 (k even). k odd = winding self-crossing
    (descend leg dives, ascend leg stays same-layer); k even = P x S true
    crossing (dive iff angle==270, matching gen_topology's ``a == 270``
    branch -- P dives in the bottom quadrant there, S -- via the mirror's
    even-boundary flip -- in the top one). The innermost ring resides for
    exactly half a turn (its descend-landing angle to ascend-departure angle
    is always 180 deg apart, since NR-1 is even whenever NR is odd)."""
    segments: list = []
    ring = 0
    angle = 270  # P1 attaches on ring 0's bottom edge
    for k in range(1, NR):
        a = ((k - 1) * 90) % 360
        segments.append(TwArc(ring=ring, angle_from=angle, angle_to=a, ccw=True))
        dive = (k % 2 == 1) or (a == 270)
        segments.append(TwLeg(boundary=k, direction="desc", angle=a, dive=dive))
        ring, angle = k, a
    for k in range(NR - 1, 0, -1):
        a_desc = ((k - 1) * 90) % 360
        a = a_desc if k % 2 == 1 else (a_desc + 180) % 360
        segments.append(TwArc(ring=ring, angle_from=angle, angle_to=a, ccw=True))
        dive = (k % 2 == 0) and (a == 270)
        segments.append(TwLeg(boundary=k, direction="asc", angle=a, dive=dive))
        ring, angle = k - 1, a
    segments.append(TwArc(ring=ring, angle_from=angle, angle_to=90, ccw=True))
    return segments


def _tw_mirror_segments(segments: list) -> list:
    """S = x-mirror of P: mirror every angle, reverse each arc's rotational
    sense (x-mirroring an CCW walk yields a CW one), and flip the dive flag
    of even-boundary legs only (gen_topology.build_s's own ``meta[1] % 2 ==
    0`` condition -- odd-boundary self-crossings keep their own dive side
    under mirroring, even-boundary P x S crossings swap which winding
    dives)."""
    out = []
    for seg in segments:
        if isinstance(seg, TwArc):
            out.append(TwArc(
                ring=seg.ring,
                angle_from=_tw_mirror_angle(seg.angle_from),
                angle_to=_tw_mirror_angle(seg.angle_to),
                ccw=not seg.ccw,
            ))
        else:
            dive = seg.dive if seg.boundary % 2 == 1 else (not seg.dive)
            out.append(TwLeg(
                boundary=seg.boundary,
                direction=seg.direction,
                angle=_tw_mirror_angle(seg.angle),
                dive=dive,
            ))
    return out


def _tw_plan(NR: int) -> tuple[list, list]:
    """Abstract path plan for both windings: ``(p_segments, s_segments)``,
    each an ordered list of ``TwArc``/``TwLeg`` from the bottom port arc to
    the top port arc. Fails closed on the connectivity model's own
    precondition (NR rings, NR odd >= 3 -- an even NR leaves the innermost
    ring's half-turn residency and the boundary parity rule ill-defined;
    spec.md "非目标")."""
    if NR < 3 or NR % 2 == 0:
        raise PortError(
            f"xfm_tw: NR={NR} must be an odd integer >= 3 (NR concentric "
            f"rings shared by P/S, NR/2 turns per winding)"
        )
    p_segments = _tw_plan_p(NR)
    s_segments = _tw_mirror_segments(p_segments)
    return p_segments, s_segments

# ---------------------------------------------------------------------------
# Architecture note (xfm-tw ticket 01 rework acceptance, superseding the
# first-round base_xfm_cross-wrapper design): every leg is drawn as an
# EXPLICIT two-endpoint primitive
# (``_tw_leg``, below) instead of instancing ``base_xfm_cross``. That wrapper
# fixes its own two via pads at a rigid (pitch, 2*offset) relationship
# controlled only by a 6-element {R0,R90,R180,R270,MX,MY} orientation choice
# -- at cardinal angle 90/270 only ONE of the two mirror-symmetric solutions
# is reachable at all (the other needs a diagonal reflection outside that
# set), which forced a growing pile of angle-specific special cases (a
# per-boundary orientation table valid only for the very first self-crossing
# boundary, an empirical extra clearance margin, a halved offset for the P x
# S crossing's own same-layer leg, and a hole-filling post-pass papering over
# the mitred-polygon seams that resulted) and still left a real short at
# NR>=5's second even boundary. Building each leg directly from its own two
# ENDPOINTS -- computed once via the single rule below, independent of
# ``base_xfm_cross``'s internal geometry -- removes the constraint that
# forced all of that: the endpoints are exactly the points the adjoining
# arcs already end at (flush by construction, no seam), and there is no
# rigid pitch/offset relationship left to be unreachable at any angle.
#
# Endpoint rule (unchanged from the very first working derivation, directly
# read off gen_topology.py's own build_p/build_s coordinates -- see
# ``_tw_plan_p``'s docstring): walking a winding's own path in order, a leg
# is always ENTERED at tangential offset -G (from the ring/angle it is
# entered on) and LEFT at +G (on the ring/angle it is left on), using
# ``_TW_TANGENT_CCW`` for the sign. Mirroring x (S's plan, see
# ``_tw_mirror_segments``) negates every tangential offset -- provably, for
# any angle: a point at (ring, angle, g) mirrors to (ring, mirror(angle),
# -g) -- so S's render call flips this SAME rule to enter at +G and leave at
# -G (the ``mirrored`` flag on ``_tw_render_winding``/``_tw_leg``); no
# per-angle table is needed for the sign either, since the flip applies
# uniformly at every angle S's mirrored plan can land on.
#
# Leg shape (ticket 02b, xfm-tw-twisted issue 02b-leg-straight-diag-
# straight.md -- user 目检 on ticket 02's sample gallery: topology approved,
# but the leg itself was a single end-to-end oblique wide path butting into
# the ring arc's tangential end at a skewed cap, "生硬拼接"): the ENDPOINT
# rule above is unchanged (a leg still enters/leaves at tangential offset
# -+G on its own outer/inner ring), but the path DRAWN between those two
# endpoints is now tangential-straight + exact-45-degree-diagonal +
# tangential-straight (``_tw_leg_waypoints``) -- collinear with the ring
# arc's own tangential run at both junctions, matching the family's own
# ind_sym/xfm_il 45-degree bridge look (base_ind_diag-shaped), WITHOUT
# reintroducing ``base_xfm_cross`` (still the architecture rejected above:
# its rigid via-pad geometry, not its 45-degree bridge shape, was the
# problem). ``_tw_slot_half_width``'s own G derivation is re-derived for
# this shape in the same ticket (see that function's own docstring).
#
# Ticket 02c (issue 02c-port-joint-and-x-degeneracy.md -- user 二轮目检):
# two further defects in 02b's own gallery. (1) 02b's bare G>=pitch/2
# bound let the AP body land G exactly on it, collapsing the straight run
# to zero -- the endpoint via() pad then sat centred on the diagonal's own
# start point, overlapping only a quarter of a flush footprint (the same
# partial-corner-overlap 02b was meant to fix). ``_tw_slot_half_width``'s
# bound (c) is tightened to pitch/2 + W/2 (the pad's own tangential
# half-width) so the straight run always fully contains the pad before
# the diagonal begins. (2) every port stub was a SEPARATE rectangle from
# the ring arc it attached to (a radial band and a tangential band meeting
# only at a corner); ``_tw_render_winding`` now draws the stub as an extra
# waypoint on the SAME wide-path call as ring 0's own first/last arc hop,
# and the standalone ``_tw_port_lead`` is retired. OPENING_P/OPENING_N
# were also redefined to the family/replica v4 convention in the same
# ticket (stub centreline +-(OPENING/2+W/2), inner-edge gap == OPENING).
# ---------------------------------------------------------------------------


def _tw_cardinal_point(H: float, angle: int) -> tuple[float, float]:
    ux, uy = _TW_CARDINAL_UNIT[angle]
    return (H * ux, H * uy)


def _tw_edge_point(H: float, angle: int, offset: float) -> tuple[float, float]:
    """Point on the ring of half-size ``H`` at cardinal ``angle``, shifted
    ``offset`` along the CCW tangent (gen_topology's own +-G convention)."""
    px, py = _tw_cardinal_point(H, angle)
    tx, ty = _TW_TANGENT_CCW[angle]
    return (px + offset * tx, py + offset * ty)


def _tw_oct_chamfer(H: float) -> tuple[float, float]:
    """(chamfer A, flat half-length BA) for an octagon ring of centerline
    half-size H: ``octagon`` applied to ``2*H`` as base_oct_quad applies it
    to ``OD`` (A and BA do not depend on the trace width)."""
    ring = octagon(2 * H, 0.0)
    return ring.A, ring.BA


def _tw_max_opening(OD: float, W: float) -> float:
    """Largest OPENING_P/OPENING_N whose port stubs still leave ring 0's flat.

    xfm_tw's OPENING is the TOTAL gap between the two stubs' inner edges: each
    stub centreline sits at ``g = OPENING/2 + W/2`` along ring 0's flat edge,
    whose centreline half-length is this ring's own ``BA``. The stub's far
    edge plus the chamfer-corner allowance ``C`` must stay on that flat,
    ``g + W/2 + C <= BA``. ``max_opening`` is calibrated for base_oct_quad's
    HALF-gap OPENING and rejected roughly half of this range (gdsfactory
    review 2026-09-21); it stays as a floor so no previously accepted
    wide-trace/small-ring configuration becomes a rejection."""
    _, BA = _tw_oct_chamfer((OD - W) / 2.0)
    C = chamfer(W).C
    return max(max_opening(OD, W), floortogrid(2 * (BA - W - C)))


def _tw_check_opening(OD: float, W: float, OPENING: float, where: str) -> None:
    mx = _tw_max_opening(OD, W)
    if OPENING > mx + _EPS:
        raise PortError(
            f"{where}: OPENING={OPENING} exceeds the maximum {mx:.3f} for "
            f"OD={OD}, W={W} (above it the port stub leaves ring 0's flat "
            f"edge and lands on the chamfer corner)"
        )


def _tw_oct_vertices(H: float, BA: float) -> dict:
    """The 8 octagon vertices, keyed by (cardinal angle, '-'|'+') where '-'/
    '+' are the CW/CCW-tangent ends of that edge's flat run."""
    return {
        (0, "+"): (H, BA), (90, "-"): (BA, H),
        (90, "+"): (-BA, H), (180, "-"): (-H, BA),
        (180, "+"): (-H, -BA), (270, "-"): (-BA, -H),
        (270, "+"): (BA, -H), (0, "-"): (H, -BA),
    }


def _tw_oct_walk_pts(H, BA, a1, p1, a2, p2, ccw) -> list:
    """Centerline waypoints from point ``p1`` (on edge ``a1``) to ``p2`` (on
    edge ``a2``), walking the octagon ring CCW or CW, inserting the chamfer
    vertices of every edge crossed in between (0, 1 or 3 edges: xfm_tw's
    boundary arcs span exactly one 90-degree edge-to-edge step except the
    innermost ring's own 180-degree half-turn residency)."""
    verts = _tw_oct_vertices(H, BA)
    step = 90 if ccw else -90
    pts = [p1]
    a = a1 % 360
    a2 = a2 % 360
    guard = 0
    while a != a2:
        guard += 1
        if guard > 8:
            raise PortError(f"xfm_tw: octagon walk from {a1} to {a2} did not terminate")
        end_side = "+" if ccw else "-"
        pts.append(verts[(a, end_side)])
        a = (a + step) % 360
        start_side = "-" if ccw else "+"
        pts.append(verts[(a, start_side)])
    pts.append(p2)
    return pts


def _tw_slot_half_width(W: float, S: float, sl: int,
                        process: ProcessRuleContext | None) -> float:
    """Boundary slot half-width G, derived from THREE explicit SL_ME
    clearance constraints (no empirical/tuned constants) -- the largest of
    the three lower bounds is returned.

    Ticket 02b reworked the leg's own drawn path from a single end-to-end
    oblique wide path into a tangential-straight + exact-45-degree-diagonal
    + tangential-straight path (``_tw_leg_waypoints``, replacing the plain
    chord ``_tw_leg_endpoints`` alone used to draw -- see that function's
    own docstring for the path construction). The leg's two ENDPOINTS are
    UNCHANGED (still at tangential offset -G/+G on the outer/inner ring),
    so bound (a) below is untouched by the rework; bound (b) -- the
    clearance between a same-layer leg's own body and the boundary's OTHER
    (dive) leg's landing pad -- is RE-DERIVED for the new path shape, and a
    new bound (c) is added (the diagonal's own tangential reach cannot
    exceed what two straight runs of length >= 0 leave available).

    Ticket 02c (user 二轮目检 on 02b's own gallery, tw_ap_nr3) tightened
    bound (c) further: 02b's bare ``G >= pitch/2`` let the AP body's own
    W=6/S=6 params land G EXACTLY on that bound, collapsing the straight
    run to zero length -- so the 45-degree diagonal started right at the
    endpoint ``vias()`` pad's own CENTRE, and a W x W pad centred on the
    exact point where the conductor turns 45 degrees only overlaps that
    conductor over a quarter of its own footprint (measured directly:
    4.0 um^2 actual vs 16.0 um^2 for a flush overlap) -- the same "partial
    corner overlap" defect 02b was supposed to eliminate, not the "legal
    degenerate case" 02b's own docstring called it. Bound (c) now requires
    the straight run to fully clear the pad's own tangential half-width
    (W/2 -- the pad is a W x W ``vias()`` block, see ``_tw_leg``; its
    tangential extent is exactly the conductor width W passed to that
    call, a closed-form read off the existing pad-sizing code, not a new
    empirical constant) BEFORE the diagonal begins: G >= pitch/2 + W/2.

    (a) Same (ring, angle): a leg's two entering/leaving endpoints (at +G
    and -G, see the module's own "endpoint rule" comment) each carry a
    tangential footprint of half-width W/2 (the wide-path leg itself, or the
    W x W landing pad of a dive leg's ``vias()`` block -- both sized to the
    conductor width W, the family's own convention for a pad matched to its
    trace). For these two NOT to touch: 2*G >= W (W/2 + W/2) + min_space,
    i.e. G >= K/2 (K = W + min_space). Unchanged by the ticket 02b rework
    (it only concerns the leg's own unchanged endpoints, not its interior
    path).

    (b) [RE-DERIVED for the straight-diagonal-straight path] A same-layer
    leg's own drawn body (its FULL SL_ME footprint, since it has no dive)
    must clear the boundary's OTHER leg's SL_ME landing pad by min_space.
    Working in the leg's own local (u, v) frame (u = radial offset along
    the ring's own H axis, v = tangential offset -- an ISOMETRY of global
    (x, y): ``_TW_CARDINAL_UNIT``/``_TW_TANGENT_CCW`` are a proper
    right-angle rotation at every cardinal angle, so distances and 45-degree
    angles carry over unchanged), a same-layer leg (say entering the outer
    ring at v=-G, leaving the inner ring at v=+G -- the other sign
    convention is the mirror image of this one) draws:
        P0=(u=R1,v=-G) -> P1=(u=R1,v=-pitch/2) -> P2=(u=R0,v=+pitch/2)
        -> P3=(u=R0,v=+G)
    (R1/R0 = outer/inner ring H, pitch = R1-R0 = W+S exactly, see
    ``_tw_leg_waypoints``). The OTHER (dive) leg's own two pads sit at the
    SAME rectangle's opposite corners, Q1=(R1,+G) and Q2=(R0,-G) (the
    uniform entering/leaving rule again, just for the other leg -- an
    established structural fact carried over unchanged from the ticket 01
    derivation, only the OWN leg's path shape changed). By the path's own
    180-degree rotational symmetry about the rectangle's centre, the
    distance from Q1 to the path equals the distance from Q2 to the path,
    so only Q1 need be checked.

    The closest point of the polyline P0-P1-P2-P3 to Q1=(R1,+G): candidate
    distances are (i) straight run P2-P3 (u=R0 fixed) has its OWN closest approach
    to Q1 fixed at exactly ``pitch`` (attained AT P3=(R0,+G), which shares
    Q1's v=+G exactly -- a distance independent of G, whose own clearance
    ``pitch - W >= min_space`` is exactly the ``pitch > K`` precondition
    already checked below, so it never separately binds); (ii) the
    diagonal P1-P2 -- by the point-to-line distance formula, the
    perpendicular distance from Q1=(R1,+G) to the infinite line through
    P1-P2 (u-v = R0+pitch/2) is (pitch/2+G)/sqrt(2), and its foot lands
    within the finite P1-P2 segment whenever G <= 1.5*pitch (algebra
    omitted; always true here since G solves to O(K), comfortably below
    1.5*pitch given the pitch > K precondition). The pad is an
    AXIS-ALIGNED W x W square while the P1-P2 edge runs at 45 degrees, so
    the pad's reach toward that edge is its CORNER's projection onto the
    diagonal's normal -- (W/2)*sqrt(2), NOT the face half-width W/2. Requiring the perpendicular distance, minus the diagonal's
    own half-width (W/2) and the pad's corner reach ((W/2)*sqrt(2)), to
    clear min_space:
        (pitch/2 + G)/sqrt(2) - W/2 - (W/2)*sqrt(2) >= min_space
        => G >= sqrt(2)*min_space + W*(1 + sqrt(2)/2) - pitch/2
    (W coefficient 1 + sqrt(2)/2 ~ 1.707, strictly above the original
    sqrt(2) ~ 1.414 -- the fix only ever GROWS G, and only for
    combinations where this corrected (b) exceeds the old max(a, b, c);
    combinations already pinned by (c) remain unchanged.)
    Growing G is precisely the "retract the dead-end pad, lengthen the
    straight runs" remedy: the pad centre sits at +/-G (further from the
    crossing diagonal) and each straight run's length (2*G - pitch)/2
    grows with it, with every flush/collinear invariant preserved by
    construction. Even in corrected form this stays LOOSER than ticket
    01/02's own ``K*pitch/(2*sqrt(pitch^2-K^2))`` in the cases that
    formula applied to with margin to spare -- the new path still hugs
    the rectangle's own edges before cutting diagonally through the
    centre instead of driving at the opposite corner chord-style.

    (c) [ticket 02b: pitch/2; ticket 02c: TIGHTENED to pitch/2 + W/2] The
    diagonal segment spans the leg's FULL radial step (pitch) with a
    MATCHING tangential span of the same magnitude (dx == dy == pitch
    exactly, the 45-degree requirement -- see ``_tw_leg_waypoints``); the
    two straight runs absorb whatever tangential distance is left over,
    (2*G - pitch)/2 each, split symmetrically. 02b required only that this
    not be negative (G >= pitch/2); 02c additionally requires each
    straight run to fully contain the endpoint pad's own tangential
    half-width before the diagonal begins -- (2*G-pitch)/2 >= W/2, i.e.
    G >= pitch/2 + W/2 -- so the pad (centred exactly on the straight
    run's own endpoint, see ``_tw_leg``) sits entirely within the straight,
    tangentially-aligned portion of the conductor it overlaps, never
    spilling into the 45-degree portion where the conductor's own local
    direction no longer matches the pad's axis-aligned footprint. This
    bound CAN DOMINATE (a) and (b): e.g. the AP body's own W=6/S=6
    (min_space=2.0, pitch=12.0) gives corrected (b) = sqrt(2)*2 +
    6*1.7071 - 6 = 7.071 um and 02b's own (c) = pitch/2 = 6.0 um, both
    LESS than 02c's (c) = pitch/2 + W/2 = 9.0 um, so G is pinned at 9.0
    -- a strictly LOOSER (larger) G than either prior ticket's formula
    gave for this exact case (02b: 6.0 um after grid-snap; 02: 5.370 um);
    see ``test_tw_slot_half_width_pad_containment_bound_dominates_ap_case``.
    For the M9/M10 reference case (W=4/S=2, min_space=1.0, pitch=6) the
    corner-corrected (b) = sqrt(2) + 4*1.7071 - 3 = 5.243 um now sits
    just ABOVE 02c's (c) = 3 + 2 = 5.0 um -- (b) dominates there since
    the tw-bridge-corner-clearance fix (it was 4.071 um under the old
    W/2 pad model, below (c); exactly the tight-S/wide-W slice whose
    built devices measured sub-min_space corner gaps).

    Bound (b)'s own derivation still needs pitch > K strictly (the
    ``pitch - W >= min_space`` floor from candidate (i) above); checked
    explicitly and reported by name if it fails, unchanged in form from
    ticket 01/02 even though its underlying justification moved from "the
    old sqrt(pitch^2-K^2) formula is undefined" to "the leg's own straight
    run cannot clear the paired pad's fixed pitch-only separation". Bound
    (b)'s own formula is UNCHANGED by ticket 02c -- G being pinned higher
    by (c) only makes (b)'s own inequality ((pitch/2+G)/sqrt(2) - W >=
    min_space) MORE satisfied, since it is monotonically increasing in G
    ("净空式 b 在新 G 下自动更松" -- looser, i.e. safer, never violated by a
    larger G); the "foot lands within the finite P1-P2 segment" self-
    consistency this bound's own formula assumes (G <= 1.5*pitch) also
    still holds at 02c's own (c): pitch/2 + W/2 <= 1.5*pitch reduces to
    W/2 <= pitch = W+S, i.e. -W/2 <= S, always true for S >= 0.

    No arc-to-own-leg "flush alignment" term is needed here (unlike the
    retired ``cross_endpoint_offset``-based design): every arc endpoint and
    its adjoining leg endpoint are still computed from the IDENTICAL (ring,
    angle, g) formula (see ``_tw_render_winding``), so flush contact is
    automatic by construction, not a separate clearance to solve for -- and
    ticket 02b's straight runs are now literally COLLINEAR with the arc's
    own tangential direction at the junction (not just flush at a point),
    which is the whole point of the rework (see spec's user-facing note).

    The closed forms themselves are irrational (general sqrt/sums), so the
    raw max(g_a, g_b, g_c) is ``ceiltogrid``-ed before returning: G is a
    tangential OFFSET added to an already grid-exact ring half-size (``H``,
    see ``_tw_edge_point``), so every downstream leg/via-block endpoint
    stays exactly on the 0.005 um mask grid as long as G itself is (this is
    a LOWER bound derivation -- rounding up, never down, is what keeps the
    clearance guarantee intact after snapping, and also keeps
    G >= pitch/2 + W/2 intact since ceiltogrid only ever increases the raw
    max())."""
    min_space = _min_met_spacing(sl, process, None)
    K = W + min_space
    pitch = W + S
    # DRC semantics allow spacing == min_space exactly, so pitch == K
    # (S == min_space) is legal: the fixed straight-run-to-pad approach
    # sits at pitch - W == min_space, a passing equality. Only a STRICT
    # shortfall fails (six-family tight-spacing clearance, 2026-07-28;
    # the guard used to reject the equality case too).
    if pitch < K:
        raise PortError(
            f"xfm_tw: ring pitch {pitch:.3f} um (W+S) does not clear the "
            f"same-layer leg's own SL_ME body from the paired dive leg's "
            f"landing pad (needs pitch >= W+min_space={K:.3f} um, "
            f"min_space from M{sl}'s own rule); increase W or S"
        )
    g_a = K / 2.0
    # (b) with the pad's CORNER reach (W/2)*sqrt(2) toward the 45-degree
    # diagonal -- see the docstring's tw-bridge-corner-clearance note.
    g_b = math.sqrt(2.0) * min_space + W * (1.0 + math.sqrt(2.0) / 2.0) \
        - pitch / 2.0
    # pad_tang: the endpoint via() landing pad's own tangential extent --
    # exactly W, the Width=W passed to vias() in _tw_leg (a closed-form
    # read off that existing call, not a new empirical constant).
    pad_tang = W
    g_c = pitch / 2.0 + pad_tang / 2.0
    return ceiltogrid(max(g_a, g_b, g_c))


def _tw_leg_frame(seg: TwLeg, G: float,
                  mirrored: bool) -> tuple[int, int, float, float]:
    """Shared local-frame data for ``_tw_leg_endpoints`` and
    ``_tw_leg_waypoints``: ``(u_start_ring, u_end_ring, enter_g, leave_g)``
    -- which ring index the leg starts/ends on (``asc`` reverses ``desc``'s
    outer->inner order) and the entering/leaving tangential offsets (the
    module's own "endpoint rule": enter -G / leave +G, negated when
    ``mirrored``)."""
    outer_ring, inner_ring = seg.boundary - 1, seg.boundary
    enter_g = G if mirrored else -G
    leave_g = -G if mirrored else G
    if seg.direction == "desc":
        return outer_ring, inner_ring, enter_g, leave_g
    return inner_ring, outer_ring, enter_g, leave_g


def _tw_leg_endpoints(seg: TwLeg, H: list, G: float,
                      mirrored: bool) -> tuple[tuple[float, float],
                                               tuple[float, float]]:
    """The leg's own (start, end) points, using the uniform entering=-G /
    leaving=+G rule (negated when ``mirrored``, see the module's own
    "endpoint rule" comment) -- the SAME formula ``_tw_render_winding``
    uses for the adjoining arcs' own endpoints, so a leg and its neighbour
    arcs are flush by construction. Unchanged by ticket 02b's interior-path
    rework (``_tw_leg_waypoints``) -- only the path BETWEEN these two
    points changed, not the points themselves."""
    u_start, u_end, enter_g, leave_g = _tw_leg_frame(seg, G, mirrored)
    start = _tw_edge_point(H[u_start], seg.angle, enter_g)
    end = _tw_edge_point(H[u_end], seg.angle, leave_g)
    return start, end


def _tw_leg_waypoints(seg: TwLeg, H: list, G: float,
                      mirrored: bool) -> list[tuple[float, float]]:
    """The leg's own full centerline path (ticket 02b, replacing the single
    end-to-end oblique segment ticket 01/02 drew -- see the module's own
    "leg shape" note and ``_tw_slot_half_width``'s docstring for the
    clearance derivation this shape enables): a tangential-straight run, an
    exact 45-degree diagonal, and a second tangential-straight run. Same
    (start, end) endpoints as ``_tw_leg_endpoints`` (the adjoining arcs
    still land flush there); only the interior path changes, and the first/
    last legs of THIS path are now literally collinear with the ring arc's
    own tangential direction at the junction (not just flush at a point),
    which is what fixes the "generic slope butts into a tangential arc end"
    seam ticket 02b's user 目检 flagged.

    The diagonal always spans the leg's FULL radial step -- pitch =
    H[boundary-1] - H[boundary], exact by construction (``xfm_tw`` builds
    every ring at a uniform W+S step) -- with a MATCHING tangential span of
    the same magnitude (dx == dy == pitch exactly), centred on the leg's
    own tangential midpoint: solving for the two straight runs' shared
    length algebraically (they must be symmetric, since the diagonal is
    the enter/leave-independent centred pitch-square described above) gives
    exactly ``(2*G - pitch) / 2`` each (spec 02b: "两段直段长度 = (2G -
    pitch)/2 各半"). Ticket 02b required only 2*G >= pitch (G >= pitch/2,
    non-negative length); ticket 02c tightened this to G >= pitch/2 + W/2
    (``_tw_slot_half_width``'s own bound (c)), so the straight run is now
    ALWAYS long enough to fully contain the endpoint ``vias()`` pad's own
    tangential half-width (W/2) before the diagonal begins -- 02b's own
    "at 2*G == pitch the straight runs collapse to zero length, a legal
    degenerate case" is exactly the tw_ap_nr3 defect ticket 02c fixed (a
    zero-length straight run leaves the pad centred on the diagonal's own
    start point, overlapping only a QUARTER of its footprint, not flush);
    it is no longer reachable through ``xfm_tw``'s own G. The dedup of
    duplicate consecutive waypoints in ``_tw_leg`` is kept as a defensive
    fallback for direct/test callers that pass a smaller G, not because
    the degenerate case is an accepted outcome of the family's own
    guarded path."""
    u_start, u_end, enter_g, leave_g = _tw_leg_frame(seg, G, mirrored)
    pitch = abs(H[u_start] - H[u_end])
    half_straight = G - pitch / 2.0
    sign_v = 1.0 if leave_g > enter_g else -1.0
    v_mid_a = enter_g + sign_v * half_straight
    v_mid_b = leave_g - sign_v * half_straight
    return [
        _tw_edge_point(H[u_start], seg.angle, enter_g),
        _tw_edge_point(H[u_start], seg.angle, v_mid_a),
        _tw_edge_point(H[u_end], seg.angle, v_mid_b),
        _tw_edge_point(H[u_end], seg.angle, leave_g),
    ]


def _tw_leg(cell: Cell, seg: TwLeg, H: list, W: float, G: float,
           sl: int, process: ProcessRuleContext | None,
           mirrored: bool, chamfer: dict | None = None
           ) -> tuple[tuple[float, float],
                      tuple[float, float]]:
    """Draw one boundary-crossing leg along its own full centerline path
    (``_tw_leg_waypoints``, ticket 02b's straight-diagonal-straight shape),
    unified for both the self-crossing and P x S crossing cases (no
    separate code path -- both are just "the other leg at this (ring pair,
    angle)", see ``_tw_slot_half_width``'s own docstring): a same-layer leg
    (``seg.dive`` False) is a single wide path on SL_ME; a dive leg is a
    wide path on SL_ME-1 (the SL_ME-1 body a same-layer leg never has) plus
    a ``vias()`` block at EACH endpoint, TOP_ME=SL_ME/BTM_ME=SL_ME-1,
    centred on that endpoint -- W x W, matching the conductor's own width
    (the family's landing-pad convention; also exactly what
    ``_tw_slot_half_width``'s own clearance derivation assumes for both the
    pad's tangential and radial reach). Centring instead of "extend inward
    from the ring edge" (the retired base_xfm_cross-wrapper design's own
    convention) makes the pad's radial span [H-W/2, H+W/2] match the ring
    arc's own conductor span exactly, so pad and arc overlap flush on both
    axes, not just tangentially. Returns the leg's own (start, end) points
    (unaffected by ticket 02b's interior-path rework) so the caller's
    adjoining arcs can match them exactly."""
    start, end = _tw_leg_endpoints(seg, H, G, mirrored)
    pts = _tw_leg_waypoints(seg, H, G, mirrored)
    # drop duplicate consecutive waypoints (the 2*G == pitch degenerate
    # case collapses each straight run to zero length -- see
    # _tw_leg_waypoints's own docstring); kdb.Path tolerates it either way,
    # but a clean point list keeps the drawn polygon minimal.
    drawn_pts = [pts[0]]
    for p in pts[1:]:
        if p != drawn_pts[-1]:
            drawn_pts.append(p)
    if seg.dive:
        # Real profile-stack neighbour, not a bare `sl - 1` (gdsfactory
        # review 2026-09-21): on N65+AP, SL_ME="AP"'s dive layer is M9 (N65
        # has no M10 between M9 and AP); byte-identical wherever the stack
        # is contiguous (reference mode and every N28 body today).
        dive_met = _metal_below(sl, process)
        layer = _metal(dive_met, process)
        add_wide_path(cell, layer, drawn_pts, W)
        u_start_ring, u_end_ring, _eg, _lg = _tw_leg_frame(seg, G, mirrored)
        for (px, py), ridx in ((start, u_start_ring), (end, u_end_ring)):
            # Corridor trim (design-region issue 04): the pad on the
            # INNER ring of the slot's pair sits where the OUTER ring's
            # inner 45-degree chamfer passes; at small ring OD the flat
            # (BA) is shorter than the pad's tangential reach G + W/2,
            # so the pad's far corner would cross the corridor line
            # |t| + r = H_out + BA_out - W/sqrt(2) (centerline octagon
            # diagonal t + r = H + BA, inner conductor edge W/2 closer).
            # Trim the far tangential edge; the slot-side edge (where
            # the leg's own straight run joins) stays put.
            wx = wy = W
            ox, oy = px - W / 2.0, py - W / 2.0
            if (process is not None and chamfer is not None
                    and ridx == seg.boundary):
                h_out = H[seg.boundary - 1]
                ba_out = chamfer[seg.boundary - 1][1]
                floor_sp = _effective_min_spacing(sl, W, process)
                allow_t = (h_out + ba_out - W / math.sqrt(2.0)
                           - floor_sp * math.sqrt(2.0)
                           - (H[ridx] + W / 2.0))
                if seg.angle in (0, 180):
                    t_lo, t_hi = py - W / 2.0, py + W / 2.0
                else:
                    t_lo, t_hi = px - W / 2.0, px + W / 2.0
                if abs(t_hi) >= abs(t_lo):
                    new_lo, new_hi = t_lo, min(t_hi, allow_t)
                else:
                    new_lo, new_hi = max(t_lo, -allow_t), t_hi
                span = floortogrid(new_hi - new_lo)
                if span < W - 1e-9:
                    if span < _pad_trim_floor(sl, process):
                        raise PortError(
                            f"xfm_tw: dive-leg pad at ring pair "
                            f"{seg.boundary - 1}/{seg.boundary} "
                            f"(H={H[ridx]}, angle={seg.angle}) would need "
                            f"trimming to {span} um to clear the outer "
                            f"ring's chamfer corridor by {floor_sp} um; "
                            f"increase OD or reduce W/NR")
                    anchored_lo = (new_lo if abs(t_hi) >= abs(t_lo)
                                   else new_hi - span)
                    if seg.angle in (0, 180):
                        wy, oy = span, anchored_lo
                    else:
                        wx, ox = span, anchored_lo
            via_cell = vias(Length=wy, Width=wx, TOP_ME=sl, BTM_ME=dive_met,
                            process=process)
            cell.inst(via_cell, (ox, oy), "R0")
    else:
        layer = _metal(sl, process)
        add_wide_path(cell, layer, drawn_pts, W)
    return start, end


def _tw_stub_zone(
    base: tuple[float, float], tip: tuple[float, float], W: float
) -> tuple[float, float, float, float]:
    """Lead zone for one xfm_tw port stub (port contract 2026-09-21):
    covers ONLY the W-wide straight radial run from the ring-0 boundary
    point ``base`` (where ``_tw_render_winding``'s own arc walk hands off
    to the stub) out to the port tip ``tip`` -- never the fused ring arc
    that same wide-path call also draws (spec.md section 10's own finding:
    the pre-contract point-in-bbox exclusion used to carry the WHOLE fused
    polygon, over-shrinking ``_body_bbox_um`` by the ring's own extent, not
    the ~LEAD-sized stub).

    Verified algebraically for all four cardinal directions
    (``test_tw_stub_zone_matches_algebra_all_four_cardinal_directions``,
    since the design document admitted this was never checked): ``base``
    and ``tip`` always differ along exactly ONE axis (``_TW_CARDINAL_UNIT``
    is always a unit step along x XOR y, never both) -- the zone spans
    that axis from ``base`` to ``tip`` exactly (flush with the ring arc's
    own polygon at ``base``, not past it) and the other axis by ``+-W/2``
    about the (constant) coordinate the stub carries along it, matching
    the W-wide wide-path ``add_wide_path`` actually draws."""
    bx, by = base
    tx, ty = tip
    if bx == tx:
        y0, y1 = (by, ty) if by <= ty else (ty, by)
        return (bx - W / 2.0, y0, bx + W / 2.0, y1)
    x0, x1 = (bx, tx) if bx <= tx else (tx, bx)
    return (x0, by - W / 2.0, x1, by + W / 2.0)


def _tw_drawn_tip_um(
    base: tuple[float, float], tip: tuple[float, float]
) -> tuple[float, float]:
    """The port tip AS ``add_wide_path`` ACTUALLY DRAWS IT (port
    contract 2026-09-21), not the unsnapped waypoint ``_tw_render_winding``
    builds its wide-path centerline from: that primitive re-snaps every
    hull vertex of its mitred polygon to the 0.005 um mask grid
    (``snap_nm_to_grid``) after mitring, to fix irrational-trig corner
    noise from the diagonal/45-degree segments elsewhere on the same path
    -- but the blanket per-vertex snap also touches the stub's own flush
    end cap, whose along-stub coordinate is ``LEAD``-derived and so is not
    itself on that 5nm grid in general. Registering the port at the
    unsnapped ``tip`` can therefore land it up to 4nm off the real drawn
    conductor whenever ``LEAD``'s nm value is not a 5nm multiple
    (break-the-contract review: ``tw_mitre_snap_vs_port_probe.py``); this
    predicts the SAME snapped vertex instead, so the registered port
    matches what actually lands in the GDS. It does not change
    ``add_wide_path``'s own input -- ``_tw_render_winding`` still
    passes the unsnapped ``tip`` into that primitive's waypoint list
    unchanged, so the drawn polygon itself is untouched (HARD RULE 5);
    this is a parallel prediction of one of its own vertices, not a
    second copy of the drawing code.

    Only the axis ``LEAD`` moves needs the snap: ``_TW_CARDINAL_UNIT`` is
    always a pure x XOR y unit step, so ``base``/``tip`` already share the
    OTHER axis's value exactly -- that shared coordinate is the stub's own
    width-span midpoint, never a drawn vertex on either primitive's path,
    and keeps the plain ``_nm()`` precision every other port in this file
    registers at."""
    bx, by = base
    tx, ty = tip
    if bx != tx:
        return (snap_nm_to_grid(_nm(tx)) * DBU_UM, ty)
    return (tx, snap_nm_to_grid(_nm(ty)) * DBU_UM)


def _tw_render_winding(cell: Cell, segments: list, H: list, W: float,
                       G: float, sl: int,
                       process: ProcessRuleContext | None,
                       port_start_g: float, port_end_g: float, LEAD: float,
                       start_port_name: str, end_port_name: str,
                       mirrored: bool) -> None:
    """Render one winding's ordered plan (``_tw_plan``'s per-winding segment
    list) as concrete same-layer ring arcs (``add_wide_path``) and
    explicit-endpoint crossing legs (``_tw_leg``), in GLOBAL coordinates.
    Callable for both P's own plan (``mirrored=False``) and S's already
    mirrored one (``mirrored=True``, see ``_tw_mirror_segments`` and the
    module's own "endpoint rule" comment for why the sign flips there) --
    every placement is keyed on (ring, angle, direction, dive) plus that one
    boolean, never on a P/S identity beyond it.

    Every arc's own endpoint, where it meets a leg, uses the IDENTICAL
    (ring, angle, g) formula the leg itself was built from (``_tw_leg``
    returns its own two endpoints for exactly this), so ring metal and leg
    metal/pad always overlap flush -- no seam, no post-hoc hole filling.

    Ticket 02c (retiring the standalone ``_tw_port_lead``): the FIRST and
    LAST segments of ``segments`` are always ring-0 port arcs (pinned by
    ``test_tw_plan_first_and_last_segments_are_ring0_port_arcs``), and each
    now gets an extra radially-outward waypoint (the port TIP, ``LEAD`` um
    out along the arc's own cardinal direction at that end) PREPENDED
    (first arc) or APPENDED (last arc) to its own point list, so the port
    stub is drawn as part of the SAME ``add_wide_path`` call as the
    ring arc it attaches to -- one mitred polygon, not two independently
    -drawn rectangles that only touch at a corner (ticket 01/02's own
    design: a purely tangential arc end and a purely radial stub, two
    perpendicular W-wide bands meeting at a single point, only a quarter
    of a flush W x W overlap -- measured directly in the ticket 02c
    investigation). The EMX port label lands at the same tip point either
    way, so this change is invisible to callers beyond the polygon shape
    itself."""
    layer = _metal(sl, process)
    chamfer = {i: _tw_oct_chamfer(h) for i, h in enumerate(H)}
    # Chamfer staircase (six-family tight-spacing clearance, 2026-07-29):
    # per-ring A/BA quantization lets adjacent rings' 45-degree
    # centerlines drift a few nm tighter than pitch*sqrt(2) -- at S ==
    # the effective floor the drawn chamfer gap loses to DRC by that
    # sliver (measured 1.998 vs 2.0 at W=4/S=2 on an N65-class top
    # metal). Same remedy as base_oct_quad's chamfer_bias: bias ring i's
    # BA inward by i*delta grids, growing every adjacent pair's diagonal
    # separation by delta*GRID_UM/sqrt(2); cardinal flats and the slot
    # legs' +-G endpoints do not move. delta stays 0 whenever the margin
    # already clears -- the entire pre-existing sweep territory.
    if process is not None and len(H) > 1:
        floor_eff = _effective_min_spacing(sl, W, process)
        worst = min(
            ((H[i] - H[i + 1]) + chamfer[i][1] - chamfer[i + 1][1])
            / math.sqrt(2.0) - W
            for i in range(len(H) - 1))
        if worst < floor_eff - 1e-9:
            delta = math.ceil(
                (floor_eff - worst) * math.sqrt(2.0) / GRID_UM - 1e-9)
            if delta > 4:
                raise PortError(
                    f"xfm_tw: ring chamfer separation short of the "
                    f"effective floor by {floor_eff - worst:.4f} um -- "
                    "beyond quantization recovery; increase S")
            chamfer = {i: (a, ba - i * delta * GRID_UM)
                       for i, (a, ba) in chamfer.items()}
    n = len(segments)
    start_angle = segments[0].angle_from
    # start_base/end_base (port contract 2026-09-21): the ring-0 boundary
    # point each port stub extends LEAD further out from -- saved under
    # their own names (not read back off `cur`, which the loop below keeps
    # reassigning) so `_tw_stub_zone` can register a zone spanning exactly
    # base->tip, no more.
    start_base = _tw_edge_point(H[0], start_angle, port_start_g)
    sux, suy = _TW_CARDINAL_UNIT[start_angle]
    start_tip = (start_base[0] + LEAD * sux, start_base[1] + LEAD * suy)
    cur = start_base
    end_base = None
    end_tip = None
    for i, seg in enumerate(segments):
        if isinstance(seg, TwArc):
            if i == n - 1:
                end = _tw_edge_point(H[seg.ring], seg.angle_to, port_end_g)
            else:
                nxt = segments[i + 1]
                nxt_start, _nxt_end = _tw_leg_endpoints(nxt, H, G, mirrored)
                end = nxt_start
            _, BA = chamfer[seg.ring]
            pts = _tw_oct_walk_pts(H[seg.ring], BA, seg.angle_from, cur,
                                   seg.angle_to, end, seg.ccw)
            if i == 0:
                pts = [start_tip, *pts]
            if i == n - 1:
                eux, euy = _TW_CARDINAL_UNIT[seg.angle_to]
                end_base = end
                end_tip = (end[0] + LEAD * eux, end[1] + LEAD * euy)
                pts = [*pts, end_tip]
            add_wide_path(cell, layer, pts, W)
            cur = end
        else:
            _start, end = _tw_leg(cell, seg, H, W, G, sl, process, mirrored,
                                  chamfer=chamfer)
            cur = end
    # segments[-1] is always a TwArc (the N/S-side ring-0 port arc, pinned
    # by test_tw_plan_first_and_last_segments_are_ring0_port_arcs), so
    # end_tip/end_base are always set by the time the loop above finishes.
    assert end_tip is not None
    pin = _pin(sl, process)
    # The wide-path polygon above was built from the UNSNAPPED start_tip/
    # end_tip (untouched -- HARD RULE 5, conductor geometry unchanged);
    # the port registers at _tw_drawn_tip_um's prediction of the SAME
    # vertex after add_wide_path's own per-vertex 0.005 um grid snap,
    # not the unsnapped waypoint, so the point lands on the real drawn
    # conductor even when LEAD's nm value is not a 5nm multiple
    # (port contract 2026-09-21, break-the-contract review). lead_zone_um
    # is built from that same snapped tip, covering just the stub (spec.md
    # section 9), not the whole fused ring-arc+stub polygon
    # _body_bbox_um's pre-contract point-in-bbox exclusion used to carry.
    start_port_tip = _tw_drawn_tip_um(start_base, start_tip)
    end_port_tip = _tw_drawn_tip_um(end_base, end_tip)
    cell.add_emx_port(name=start_port_name, logical_name=start_port_name,
                      metal=sl, label_layer=pin,
                      x_um=start_port_tip[0], y_um=start_port_tip[1],
                      lead_zone_um=_tw_stub_zone(start_base, start_port_tip, W))
    cell.add_emx_port(name=end_port_name, logical_name=end_port_name,
                      metal=sl, label_layer=pin,
                      x_um=end_port_tip[0], y_um=end_port_tip[1],
                      lead_zone_um=_tw_stub_zone(end_base, end_port_tip, W))


def xfm_tw(
    OD: float,
    W: float,
    S: float,
    NR: int,
    OPENING_P: float,
    OPENING_N: float,
    LEAD: float,
    SL_ME: str = "9",
    dummy: bool = True,
    DUMMYL: str = "RFVLSI",
    port_order: list[str] | None = None,
    ground_fixture: GroundFixtureConfig | None = None,
    process: ProcessRuleContext | None = None,
) -> Cell:
    """Type 3 same-layer overlapping-inductor ("twisted") transformer:
    clean-room composition, no single .il source models this device (see
    FUNCTION_MAPPING). NR concentric octagon rings shared half-and-half by P
    (CCW, ports P1 bottom-right/N1 top-right) and S (P's x-mirror, CW, ports
    P2 bottom-left/N2 top-left), each winding NR/2 turns, connected across
    the NR-1 ring boundaries by explicit two-endpoint legs (``_tw_leg``)
    whose dive layer (SL_ME vs SL_ME-1) alternates per the rule ``_tw_plan``
    ports from .scratch/xfm-tw-twisted/gen_topology.py (spec.md, approved
    2026-07-18; see that module's own docstring and ``_tw_plan``'s here for
    the full boundary-parity rule). No CT (spec.md "非目标").

    OPENING_P/OPENING_N semantics (ticket 02c, family/replica v4 口径): the
    two P-side (P1/P2) or N-side (N1/N2) port stubs' own tangential
    centrelines sit at +-(OPENING/2 + W/2), so their INNER edges (facing
    the coil centre) are exactly OPENING_P (or OPENING_N) apart in total --
    NOT ticket 01/02's own g=OPENING+W/2 formula, which put the inner
    edges 2*OPENING apart. Each port stub is drawn as a radially-outward
    extension of ring 0's own arc (one mitred wide path, ``_tw_render_
    winding``), not a separately-drawn rectangle -- ticket 01/02's split
    design left a purely-radial stub and a purely-tangential arc end
    touching only at a corner (a quarter of a flush W x W overlap).

    Guards (fail closed, every message names the offending rule/value):
    ``_tw_plan`` itself rejects NR<3 or even (the connectivity model's own
    precondition); SL_ME needs SL_ME-1 at M2 or above (the dive legs'
    floor); OPENING_P/OPENING_N each against ``_tw_max_opening(OD, W)`` (ring
    0's own flat run under this family's total-gap OPENING semantics); the innermost ring's own flat-edge half-length
    must not be crushed below the boundary slot half-width ``G`` (derived by
    ``_tw_slot_half_width`` from explicit SL_ME clearance constraints, see
    its own docstring for the closed-form derivation) -- past that point a
    slot's +-G tangential offset would spill past the octagon chamfer,
    exactly the ``max_opening``-style failure ``_check_winding_fit`` guards
    elsewhere in this module (the innermost ring is the binding case since
    its half-size shrinks fastest with NR); ``port_order`` is not
    configurable (xfm devices have fixed port names -- see
    ``generator_plugin.py``'s ``_FixedXfmPortOrderMixin``, whose "xfm
    devices have fixed port names" message this mirrors one layer down,
    since xfm_tw's P1/N1/P2/N2 identities come directly from the topology,
    not a display convention any caller may rename); and ``_xfm_net_short``
    covers every drawn layer (SL_ME and SL_ME-1) between the two windings.
    """
    p_segments, s_segments = _tw_plan(NR)
    sl = _metal_index(SL_ME)
    # Real profile-stack dive layer, not a bare `sl - 1` (gdsfactory review
    # 2026-09-21): on N65+AP the dive layer is M9 (N65 has no M10 between M9
    # and AP); `_metal_below` itself fails closed, naming SL_ME, when no
    # conductor at all exists below it. M1 is additionally off limits
    # (reserved for the ground fixture, add_ground_fixture) -- a policy
    # floor on the RESOLVED conductor, unrelated to whether it exists.
    try:
        dive = _metal_below(sl, process)
    except PortError as exc:
        raise PortError(
            f"xfm_tw: SL_ME {_metal_name(sl)} needs a dive layer SL_ME-1 at "
            f"M2 or above (the self/PxS crossing legs' via-ended layer): {exc}"
        ) from exc
    if dive < 2:
        raise PortError(
            f"xfm_tw: SL_ME {_metal_name(sl)} needs a dive layer at M2 or "
            f"above (the self/PxS crossing legs' via-ended layer; M1 is "
            f"reserved for the ground fixture); only {_metal_name(dive)} "
            f"available"
        )
    _tw_check_opening(OD, W, OPENING_P, "xfm_tw P1/P2 (bottom opening)")
    _tw_check_opening(OD, W, OPENING_N, "xfm_tw N1/N2 (top opening)")
    if port_order is not None and list(port_order) != _TW_FIXED_PORT_ORDER:
        raise PortError(
            f"xfm_tw: port_order is not configurable; xfm devices have "
            f"fixed port names {_TW_FIXED_PORT_ORDER}; got {list(port_order)!r}"
        )
    H = [(OD - W) / 2.0 - i * (W + S) for i in range(NR)]
    G = _tw_slot_half_width(W, S, sl, process)
    _, BA_inner = _tw_oct_chamfer(H[-1])
    if BA_inner < G - _EPS:
        raise PortError(
            f"xfm_tw: innermost ring (index {NR - 1}) flat-edge half-length "
            f"{BA_inner:.3f} um is below the boundary slot half-width "
            f"G={G:.3f} um (OD={OD}, W={W}, S={S}, NR={NR}); increase OD or "
            f"reduce NR/W/S"
        )

    params = {"OD": OD, "W": W, "S": S, "NR": NR, "OPENING_P": OPENING_P,
              "OPENING_N": OPENING_N, "LEAD": LEAD, "SL_ME": SL_ME}
    if process is not None:
        params["process"] = process.profile_id
    cell = Cell(f"xfm_tw_OD{OD}_W{W}_S{S}_NR{NR}_{SL_ME}", "xfm_tw", params)

    pri = Cell("xfm_tw_pri", "xfm_tw_pri", {})
    _tw_render_winding(pri, p_segments, H, W, G, sl, process,
                       OPENING_P / 2.0 + W / 2.0, -(OPENING_N / 2.0 + W / 2.0),
                       LEAD, "P1", "N1", False)

    sec = Cell("xfm_tw_sec", "xfm_tw_sec", {})
    _tw_render_winding(sec, s_segments, H, W, G, sl, process,
                       -(OPENING_P / 2.0 + W / 2.0), OPENING_N / 2.0 + W / 2.0,
                       LEAD, "P2", "N2", True)

    _xfm_net_short("xfm_tw", pri, sec)
    # each net holds one segment per ring on SL_ME; the dive legs join them below (D11 guard)
    _check_winding_segments(pri, met=sl, expected=NR, where="xfm_tw primary", process=process)
    _check_winding_segments(sec, met=sl, expected=NR, where="xfm_tw secondary", process=process)
    cell.inst(pri, (0.0, 0.0), "R0")
    cell.inst(sec, (0.0, 0.0), "R0")
    # (port contract 2026-09-21) walks the cell.inst() chain down to both
    # windings' own _tw_render_winding-registered stub ports -- replaces
    # the old cell.emx_ports.extend(pri.emx_ports)/extend(sec.emx_ports).
    cell.emx_ports = finalize_emx_ports(cell)
    _xfm_order_ports(cell)
    if ground_fixture is not None:
        add_ground_fixture(cell, ground_fixture, process)
    return cell
