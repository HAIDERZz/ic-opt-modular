# Auto-split from pcell_inductor_port_clean.py (wrap-up P1,
# 2026-07-28): verbatim segment move, no behavior change. The
# facade module re-exports every name; see its docstring for
# provenance and the KNOWN_DEVIATIONS record.
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path

from ic_opt.em.pcell._pcell_core import (
    _LAYER_COLORS,
    DBU_UM,
    GRID_UM,
    ProcessRuleContext,
    _layer_display_name,
    _mode_metadata,
    _process_layer_names,
    _read_gds_polygons,
    _render_legend_label,
    emx_port_lines,
    process_rule_context,
    write_gds,
)
from ic_opt.em.pcell._pcell_ind_sym import ind_sym
from ic_opt.em.pcell._pcell_primitives import (
    base_balun_sec,
    base_ind_diag,
    base_ind_hud_cross,
    base_lead,
    base_lead_pair,
    base_oct,
    base_oct_half,
    base_oct_quad,
    base_xfm_cross,
)
from ic_opt.em.pcell._pcell_xfm_balun import xfm_balun
from ic_opt.em.pcell._pcell_xfm_bs import xfm_bs
from ic_opt.em.pcell._pcell_xfm_il import xfm_il
from ic_opt.em.pcell._pcell_xfm_ms import xfm_ms
from ic_opt.em.pcell._pcell_xfm_tw import xfm_tw
from ic_opt.em.pcell.fixture import (
    GroundFixtureConfig,
)

# ---------------------------------------------------------------------------
# output generation: GDS + PNG (rendered from the GDS) + coordinates JSON
# ---------------------------------------------------------------------------

FUNCTION_MAPPING = [
    {
        "python_function": "vias",
        "pcell_source": (
            "(library PCell, no .il source in gdsgen_ref; interface "
            "reconstructed from call sites and ind_ref.gds)"
        ),
        "parameters": ["Length", "Width", "TOP_ME", "BTM_ME", "bPP"],
    },
    {
        "python_function": "base_ind_diag",
        "pcell_source": "gdsgen_ref/pcell/inductor/base_ind_diag.il",
        "parameters": ["W", "S", "MET"],
    },
    {
        "python_function": "base_xfm_cross",
        "pcell_source": "gdsgen_ref/pcell/transformer/base_xfm_cross.il",
        "parameters": ["WI", "WO", "S", "TOP_ME", "BTM_ME", "dummy", "viat", "viad", "top"],
    },
    {
        "python_function": "base_oct_quad",
        "pcell_source": "gdsgen_ref/pcell/common/base_oct_quad.il",
        "parameters": ["OD", "W", "OP", "MET", "bCons"],
    },
    {
        "python_function": "base_oct_half",
        "pcell_source": "gdsgen_ref/pcell/common/base_oct_half.il",
        "parameters": ["OD", "W", "LOP", "ROP", "bCons", "MET"],
    },
    {
        "python_function": "base_oct",
        "pcell_source": "gdsgen_ref/pcell/common/base_oct.il",
        "parameters": ["OD", "W", "LOP", "ROP", "MET"],
    },
    {
        "python_function": "base_ind_hud_cross",
        "pcell_source": "gdsgen_ref/pcell/inductor/base_ind_hud_cross.il",
        "parameters": ["OD", "W", "S", "OPENING", "TOP_ME", "BTM_ME", "under", "dummy", "DUMMYL"],
    },
    {
        "python_function": "base_lead",
        "pcell_source": "gdsgen_ref/pcell/common/base_lead.il",
        "parameters": ["L", "W", "WD", "PINTXT", "PINP", "TOP_ME", "BTM_ME", "DUMMYL", "DUMMYP", "dummy"],
    },
    {
        "python_function": "base_lead_pair",
        "pcell_source": "gdsgen_ref/pcell/common/base_lead_pair.il",
        "parameters": ["W", "OPENING", "LEAD", "TOP_ME", "LEAD_ME", "dummy", "P1TXT", "N1TXT"],
    },
    {
        "python_function": "ind_sym",
        "pcell_source": "gdsgen_ref/pcell/inductor/ind_sym.il",
        "parameters": [
            "OD",
            "W",
            "OPENING",
            "LEAD",
            "S",
            "NT",
            "TOP_ME",
            "BTM_ME",
            "strName",
            "NT_N",
            "dummy",
        ],
    },
    {
        "python_function": "ind_sym (CT_ME)",
        "pcell_source": "gdsgen_ref/pcell/inductor/ind_sym_ct.il",
        "parameters": ["OD", "W", "OPENING", "LEAD", "S", "TOP_ME", "NT", "CT_ME", "dummy", "DUMMYL", "NT_N"],
    },
    {
        "python_function": "vias_nomet",
        "pcell_source": (
            "(library PCell, no .il source; reconstructed as vias cut array "
            "minus metal rectangles)"
        ),
        "parameters": ["Length", "Width", "TOP_ME", "BTM_ME"],
    },
    {
        "python_function": "base_oct_quad_vias",
        "pcell_source": "gdsgen_ref/pcell/common/base_oct_quad_vias.il",
        "parameters": ["OD", "W", "OP", "MET", "diagonal_vias"],
    },
    {
        "python_function": "base_oct_half_vias",
        "pcell_source": "gdsgen_ref/pcell/common/base_oct_half_vias.il",
        "parameters": ["OD", "W", "LOP", "ROP", "MET"],
    },
    {
        "python_function": "base_balun_sec",
        "pcell_source": "gdsgen_ref/pcell/transformer/base_balun_sec.il",
        "parameters": ["OD", "WI", "LOP"],
    },
    {
        "python_function": "base_ind_under",
        "pcell_source": "gdsgen_ref/pcell/inductor/base_ind_under.il",
        "parameters": ["W", "WX", "S", "TOP_ME", "BTM_ME", "NT"],
    },
    {
        "python_function": "xfm_bs",
        "pcell_source": (
            "(clean-room composition; no single .il models a broadside "
            "two-layer single-turn transformer. Reuses ported base_oct.il / "
            "base_lead_pair.il / base_lead.il / vias primitives; replaces "
            "src/.../single_turn_transformer.py placeholder)"
        ),
        "parameters": ["OD_P", "OD_S", "W_P", "W_S", "OPENING_P", "OPENING_S",
                       "LEAD_P", "LEAD_S", "CENTER_SPACING", "PRI_ME",
                       "SEC_ME", "CT_P_ME", "CT_S_ME"],
    },
    {
        "python_function": "xfm_ms",
        "pcell_source": (
            "(clean-room composition; multi-turn winding reuses ind_sym.il, "
            "single-turn reuses base_oct.il/base_lead_pair.il. No .il models a "
            "multi+single two-layer transformer)"
        ),
        "parameters": ["OD_S", "OD_M", "W_S", "W_M", "OPENING_S", "OPENING_M",
                       "LEAD_S", "LEAD_M", "NT_M", "S_M", "CENTER_SPACING",
                       "SINGLE_ME", "MULTI_ME", "CT_P_ME", "CT_S_ME"],
    },
    {
        "python_function": "base_xfm_half",
        "pcell_source": "gdsgen_ref/pcell/transformer/base_xfm_half.il",
        "parameters": ["OD", "WO", "WI", "S", "B_TK", "A_TK", "LOP", "ROP",
                       "TOP_ME", "BTM_ME", "via_to_next", "via_diag"],
    },
    {
        "python_function": "xfm_balun",
        "pcell_source": (
            "(clean-room composition; primary+secondary on one metal-generic "
            "plane reuses base_xfm_half/base_oct/ind_sym. No single .il "
            "models a same-layer coplanar balun)"
        ),
        "parameters": ["OD_P", "OD_S", "W_P", "W_S", "S", "OPENING_P",
                       "OPENING_S", "LEAD_P", "LEAD_S", "NT_P", "NT_S",
                       "CENTER_SPACING", "BALUN_ME", "ESCAPE_ME",
                       "CT_P_ME", "CT_S_ME"],
    },
    {
        "python_function": "xfm_il",
        "pcell_source": (
            "(clean-room composition; xfm-il-interleaved ticket 02 + 02b + "
            "02c + 02d v4-final rework + ticket 06 bridge-spacing repair. "
            "P and S alternate radial bands on one metal-generic plane "
            "SL_ME. Both use xfm_il's private _il_staggered_ring_turns "
            "composition of the unchanged base_oct/base_xfm_cross/vias "
            "family primitives, retaining ind_sym's native alternating "
            "bridge zigzag while symmetrically shifting every colliding "
            "odd/even bridge pair by a W/S/rule-derived +/-q. The target "
            "diagonal gap is S in reference mode and max(S, minSpace of "
            "both bridge layers) in process mode; bridge, via landing and "
            "SL opening move together. P retains ind_sym's lead/port "
            "contract. S adds xfm_balun's nested-secondary crossunder escape "
            "(_balun_crossunder, base_ind_under.il) in a private 'opens "
            "right' local frame, the whole S sub-cell then mirrored (MY) "
            "so its port+escape land on the GLOBAL LEFT (opposite P's "
            "right) along with its own (mirrored) bridges. Ticket 02d "
            "retires 02b/02c's bridge_side='left' same-side-stacking "
            "construction from ACTIVE USE here (still available on "
            "ind_sym/_ind_ring_turns for other callers): 02b's same-side "
            "co-location shorted P1 to N1 (both terminals on one closed "
            "loop); 02c's opposite-arm fix instead left odd-NT innermost "
            "turns fully closed (a closed ring is a shorted turn, caught "
            "by the ticket 02d 'no closed loop' invariant test). Each "
            "turn's crossunder is ALSO now split across TWO further "
            "layers (LEG2_BTM_ME on base_ind_hud_cross/_ind_ring_turns/ "
            "ind_sym, ticket 02d): leg1 on SL_ME-1 (unchanged since ticket "
            "02), leg2 on SL_ME-2 (previously crossunder_sl1_only routed "
            "leg2 to the SAME SL_ME-1 as leg1, which let one winding's "
            "leg1+leg2 collide with the OTHER winding's own leg1+leg2 on "
            "that single shared layer -- the 'same-layer crossing short' "
            "the ticket 02d dual-layer-legs invariant test pins down). "
            "_check_bridge_escape_clearance (unchanged formula: driven by "
            "the fixed 'R0' outermost-turn placement both bridge_side "
            "modes share) still enforces the minimum spacing between one "
            "winding's outermost bridge and the OTHER winding's lead/ "
            "escape channel on the half-plane they share. No single .il "
            "models a same-layer interleaved (Rabjohn/Frlan) transformer. "
            "Optional CT_P_ME/CT_S_ME (ticket 03c, superseding 03b's "
            "tangential-offset design after a follow-up Virtuoso review) "
            "add a true electrical-midpoint center tap per winding "
            "(_il_ct_tap_exact: taps the innermost ring's own closed "
            "column -- _ind_ct_tap's own LEFT(odd NT)/RIGHT(even NT) "
            "tap_x, the ring's true Euler-path midpoint by its own "
            "left-right symmetry about the one-gap arm's opposite point -- "
            "at a FIXED position, never offset). Direction is decided by "
            "comparing CT_ME to SL_ME (_il_ct_metal_guard): above SL_ME "
            "taps UPWARD (no adjacency floor, nothing else this device "
            "draws is ever above SL_ME); below SL_ME taps DOWNWARD "
            "(CT<=SL_ME-3, _il_ct_adjacency_guard, promoting ind_sym's "
            "two-layer N1 rule to three layers now that ticket 02d's "
            "crossunder occupies both SL_ME-1 and SL_ME-2) and fails "
            "closed by name if the OTHER winding's crossunder blocks the "
            "exact midpoint there, suggesting an upward CT_ME instead. A "
            "downward CTP is proven structurally blocked in EVERY legal "
            "configuration (the interleaved lattice's half-pitch offset "
            "always sandwiches a P ring inside a same-reach S bridge "
            "gap), so CTP in practice needs an upward CT_P_ME -- "
            "impossible on an SL_ME='AP' body, a genuine limit of that "
            "body; CTS's downward tap is the structural mirror opposite, "
            "always clear (S is always the device's innermost winding). "
            "Ticket 03c also tightens NT_P<=NT_S<=NT_P+1 (ticket 01) to "
            "NT_P==NT_S (user directive), which as a side effect keeps "
            "CTP/CTS on structurally OPPOSITE global sides whenever both "
            "are enabled. Ticket 02's R90-rotated orthogonal layout and 02b's "
            "same-side layout are preserved only in git history at "
            "3f6058e / a8de62e)"
        ),
        "parameters": ["OD", "W", "S", "NT_P", "NT_S", "OPENING_P",
                       "OPENING_S", "LEAD_P", "LEAD_S", "SL_ME", "CT_P_ME",
                       "CT_S_ME", "dummy", "DUMMYL", "port_order"],
    },
    {
        "python_function": "xfm_tw",
        "pcell_source": (
            "(clean-room composition; ticket 01, xfm-tw-twisted. Type 3 "
            "same-layer overlapping-inductor transformer: NR concentric "
            "octagon rings shared half-and-half by P (CCW) and S (P's "
            "x-mirror, CW), connected across the NR-1 ring boundaries by "
            "explicit two-endpoint legs (_tw_leg) whose dive layer (SL_ME "
            "vs SL_ME-1) alternates per a path planner (_tw_plan) ported "
            "from the spec's own connectivity authority, "
            ".scratch/xfm-tw-twisted/gen_topology.py's build_p/build_s -- "
            "boundary slot angles, the odd-boundary self-crossing / "
            "even-boundary P x S true-crossing dive rule, and S as P's "
            "x-mirror with the even-boundary dive flag flipped, re-derived "
            "here in size-decoupled (ring/angle/desc-or-asc/dive) terms "
            "instead of that script's literal square-ring coordinates. "
            "Rings are drawn as octagon-perimeter wide paths (explicit "
            "centerline waypoints through the family's own "
            "DIV=2+sqrt(2) chamfer vertices, widened via kdb.Path) rather "
            "than base_oct_quad/base_oct_half, which are hard-coded for "
            "one opening per quadrant pair -- xfm_tw needs four "
            "independent per-ring slots as NR grows. Legs are likewise "
            "drawn directly from their own two endpoints (a single "
            "computed sign rule, see _tw_leg_endpoints) rather than "
            "instancing base_xfm_cross.il: that wrapper's own via-pad "
            "geometry is rigid (a fixed pitch/offset relationship reachable "
            "through only ONE of its two mirror-symmetric orientations at "
            "cardinal angle 90/270), which an earlier iteration of this "
            "device papered over with a growing set of angle-specific "
            "special cases and still left a real short at NR>=5 (see git "
            "history on this file/its test for that design and its "
            "post-mortem). Ticket 02b (user 目检 on ticket 02's sample "
            "gallery: the leg-arc junction looked like a generic-slope "
            "wide path butting into a tangential arc end) reworked the "
            "path BETWEEN those same two endpoints from a single oblique "
            "segment into a tangential-straight + exact-45-degree-diagonal "
            "+ tangential-straight run (_tw_leg_waypoints), collinear with "
            "the ring arc at both junctions -- the family's own ind_sym/ "
            "xfm_il 45-degree bridge look (base_ind_diag-shaped), without "
            "reintroducing base_xfm_cross. _tw_slot_half_width's own G "
            "derivation was re-derived for this new shape in the same "
            "pass (a new pitch/2 lower bound plus a re-derived diagonal- "
            "clearance bound; still no tuned constants, see that "
            "function's own docstring). Ticket 02c (user 二轮目检 on 02b's "
            "own gallery) tightened that pitch/2 bound to pitch/2 + W/2 "
            "-- 02b's bare bound let the AP body's own straight run "
            "collapse to zero length, landing the endpoint via() pad's "
            "centre exactly on the 45-degree diagonal's own start point "
            "(measured directly: the pad only overlapped a quarter of a "
            "flush W x W footprint), the same partial-corner-overlap "
            "defect 02b was meant to eliminate. 02c also retired the "
            "standalone _tw_port_lead: each port stub is now an extra "
            "radially-outward waypoint prepended/appended to ring 0's own "
            "first/last arc hop (_tw_render_winding), drawn as ONE mitred "
            "wide path instead of a separate rectangle that only touched "
            "the arc at a corner, and OPENING_P/OPENING_N were redefined "
            "to the family/replica v4 convention (stub centreline at "
            "+-(OPENING/2+W/2), so the two ports' own inner edges are "
            "OPENING apart in total, not 2*OPENING as ticket 01/02's own "
            "g=OPENING+W/2 formula gave). The boundary slot half-width G "
            "is a closed-form derivation from explicit SL_ME clearance "
            "constraints (_tw_slot_half_width), not a tuned constant. No "
            "single .il models this device; no CT (spec.md's own "
            "non-goal). port_order is fixed, not caller-configurable "
            "(spec.md: xfm devices have fixed port names, mirroring "
            "generator_plugin.py's _FixedXfmPortOrderMixin one layer down)."
        ),
        "parameters": ["OD", "W", "S", "NR", "OPENING_P", "OPENING_N",
                       "LEAD", "SL_ME", "dummy", "DUMMYL", "port_order"],
    },
]

KNOWN_DEVIATIONS = [
    "User-authorized straight-extension exploration (2026-09-20): ind_sym "
    "and xfm_bs accept non-negative STRAIGHT_EXTENSION in 0.01 um steps, "
    "adding total X width through central horizontal winding straights. "
    "Each winding's left/right routing, via landings and CT translate "
    "rigidly about its own centre; BS retains its original centre spacing. "
    "Edges or protected vias/routing crossing the cut fail closed. Ground "
    "fixtures are rebuilt afterwards. Positive extension records a flat "
    "transformed cell; zero preserves legacy geometry, hierarchy and params. "
    "MS and other public family interfaces are unchanged.",
    "User-authorized extension of the same contract to xfm_ms (gdsfactory "
    "review 2026-09-21): the single turn and the multi-turn winding (compact "
    "two-turn and generic) each extend about their own centre by the same "
    "total; zero preserves legacy geometry. balun/tw/il remain unchanged.",
    "User-authorized compact two-turn correction (2026-09-12): reject "
    "candidate metal regions containing closed holes. The old component "
    "count check admitted negative inner openings that shorted the inner "
    "turn; the existing lane/pad search now finds a hole-free layout or "
    "rejects the unchanged parameters. This changes affected process-mode "
    "ind_sym/MS geometry; balun shares the helper and needs a separate "
    "historical-library audit. Reference mode is unchanged.",
    "User-authorized ind_sym/xfm_ms modeling review (2026-09-12): the "
    "inductor crossunder and CT clearance follow actual profile conductor "
    "adjacency (N65 AP -> M9 via RV; N28 AP -> M10). Compact two-turn "
    "metadata now describes its real body-plane second leg. xfm_ms permits "
    "multi=M3 with its single M2 crossunder; M1 stays reserved for ground. "
    "Reference-mode and existing valid N28 conductor geometry are preserved.",
    "vias is a PDK library PCell without SKILL source in gdsgen_ref; its "
    "geometry (metal stack in [0,Width]x[0,Length], centred 0.36 um cuts, "
    "0.34 um spacing, 0.22 um enclosure) is reconstructed from call sites "
    "and from the via cells inside ind_ref.gds. bPP is accepted but ignored. "
    "ind_ref.gds additionally shows a layer-pair-dependent pitch (via7 "
    "0.7 um, via8 0.9 um); lacking the vias source, this port applies the "
    "single 0.7 um rule to every pair instead of guessing per-pair tables.",
    "Reference-mode GDS datatypes are always 0; tsmcN28_1p10m.proc lists "
    "EMX-oriented datatypes (e.g. l39t80) that are irrelevant for the "
    "structural comparison reference mode serves. Process mode is exempt: "
    "it draws on the rule-profile datatypes (e.g. N28 M9 (39,80)).",
    "RFVLSI/DMEXCL dummy layers, labels, base_oct_fill, base_em_gr and the "
    "rfvlsiEMVport/rfvlsiEMBoundary/rfvlsiEMDie EM helpers are not ported; "
    "dummy parameters are accepted for signature fidelity and ignored.",
    "viat/viad of base_xfm_cross are declared but never read in the "
    "reference source; both endpoint vias blocks are created "
    "unconditionally, and the same-layer cross has no cuts only because "
    "TOP_ME == BTM_ME. The port mirrors this exactly.",
    "base_ind_hud_cross.il hardcodes its ring metal to MET 9 and derives "
    "the underpass metal as TOP_ME-1; its BTM_ME parameter is dead in the "
    "reference source (only feeds the unused via_to_next flag). The port "
    "keeps the underpass relationship but derives the ring from TOP_ME.",
    "ind_sym.il passes BTM_ME/P2TXT to base_lead_pair although "
    "base_lead_pair declares neither; like Virtuoso, the port drops them. "
    "The port sets LEAD_ME to TOP_ME so generalized P1/N1 coil leads stay "
    "on the coil top metal.",
    "Single-turn topology correction (user-directed, 2026-07-21): the "
    "reference ind_sym turn loop instantiates base_ind_hud_cross even at "
    "NT=1, then overlays its odd-turn closure at the same OD. With no "
    "adjacent turn to connect, this creates a lower-metal bridge, vias, "
    "and an isolated top-metal landing pad. The port instead draws one "
    "direct open base_oct from P1 to N1 for NT=1; NT>=2 is unchanged.",
    "Alignment correction (user-directed, referenced on ind_ref.gds where "
    "every crossover via pad is fully covered by ring metal): all ring "
    "openings that face a crossover use the exact cross endpoint edge "
    "cross_endpoint_offset(W,S) = OOCH+OOCHD. The .il values -- hud LOP = "
    "OOCH + roundtogrid(2*sqrt(2)-S) + 0.01 (5 nm slop) and ind_sym "
    "inner-turn OPENING / odd-innermost ROP = 2*W (0.75 um pad protrusion "
    "at W=2, S=2) -- are documented here and replaced.",
    "Center-tap correction (user-directed, referenced on ind_ref.gds where "
    "the tap exits on M7 beneath the turns with a via stack at the tap "
    "point only): the CT construction (ind_sym CT_ME; the standalone ind_sym_ct generator was unified away in M13) places a W x W vias stack at "
    "the closed column of the innermost turn (the winding symmetry point) "
    "and routes the CT lead on BTM_ME (default M7) out on that column's "
    "side -- the crossover side for odd NT; for even NT (the PCell default "
    "NT=2) the closed column sits on the P1/N1 lead side and the CT lead "
    "exits there. ind_sym_ct.il instead draws a TOP_ME base_lead of length "
    "LEAD+P at (OD/2-P, -W/2), which for odd NT reaches no ring at all; "
    "that construction is documented here and replaced.",
    "Metal generalization (user-directed): the coil is generic over "
    "TOP_ME -- ring, same-layer mirrored cross, endpoint pads and P1/N1 "
    "leads on M(TOP_ME); underpass diagonal on M(TOP_ME-1). "
    "base_ind_hud_cross.il hardcodes the ring on MET 9 and ind_sym.il "
    "hardcodes TOP_ME='9'/BTM_ME='8' in every child call; both "
    "hardcodings are documented here and replaced. Defaults keep the "
    "original 9/8 behavior.",
    "The SKILL grid helpers ceiltogrid/roundtogrid/floortogrid have no "
    "source in gdsgen_ref (only call sites); like vias they are "
    "reconstructions: 0.005 um mask grid with ceil/round-half-up/floor "
    "semantics. Round-half-even would differ only at exact half-grid "
    "values, which no demo parameter set produces.",
    "GDS output is flattened into a single top cell; the PCell hierarchy "
    "is preserved in the Python call graph and recorded in the "
    "instantiation_log of each coordinates JSON instead.",
    "pcRound-based helpers of base_ind_turn.il (not part of the required "
    "coverage) are not ported; no substitute geometry was invented.",
    "Cited passive-region restrictions (M7K): IND.R.1 restricts "
    "VIA1..VIA7 inside the profile's passive-region marker sizing with a "
    "LOWMEDN band exception for the VIAx class (VIA1..VIA5) that this "
    "generator does not implement; IND.R.5 (single intermediate metal) "
    "is transcribed as data only. These supersede the earlier blanket "
    "statements that no "
    "lower via is restricted in the passive region.",
    "Geometric-only generator enforcement (n28-rules-slim, user directive "
    "2026-07-19): supersedes the M7K restriction gate above. "
    "plan_passive_via_array no longer fails closed on via_restrictions "
    "(IND.R.1) or on passive_via_array_coverage's not_yet_modeled "
    "classification -- only on missing via geometry (a via with no "
    "via_primitives entry, or an incomplete min_enclosure_um map) does it "
    "still fail closed. IND.R.1/IND.R.5 remain transcribed as cited deck "
    "data in rule.yaml for documentation; the N28 domain of every "
    "CT-bearing device in this module (ind_sym, xfm_il, xfm_bs, xfm_ms, "
    "xfm_balun) widened accordingly (e.g. the M9-body ind_sym M7 CT tap "
    "and xfm_il's SL_ME=\"9\" body now build; see the device tests and "
    "docs/guide/07-device-inventory.md for the current matrix).",
    "Diagonal via fail-closed (M7N): base_oct_quad_vias.il places two via "
    "groups — an axis-aligned BB block (vias_nomet, faithfully reconstructed) "
    "and a diagonal VIA8 arm (vias_diagonal_nomet, a sourceless library PCell "
    "with no .il and no GDS evidence: ind_ref.gds VIA8 cuts are all "
    "axis-aligned). The diagonal arm fails closed with PortError; only the "
    "axis-aligned block is drawn. A future milestone may supply a verified "
    "diagonal-cut rule.",
    "xfm_ms dual-side center taps (M13 ticket 06, user-authorized "
    "2026-07-17): xfm_ms gained optional per-winding taps and the same "
    "sub-cell + _xfm_net_short structure as xfm_bs. The P side (ports "
    "P1/N1) is the SINGLE-turn winding, tapped at its closed column "
    "via _bs_center_tap (CT_P_ME strictly below SINGLE_ME); the S side "
    "(ports P2/N2) is the MULTI-turn winding, tapped through ind_sym's "
    "CT_ME path, so CT_S_ME obeys the inductor's N1 adjacency rule "
    "(two levels below MULTI_ME). The multi-winding port-copy loop "
    "moved onto the multi sub-cell unchanged.",
    "xfm_bs sub-cell restructure + generalized net gate (M13 ticket 05, "
    "user-authorized 2026-07-17): each xfm_bs winding (plus its optional "
    "center tap) now builds in its own sub-cell and the balun's "
    "layer-complete net gate was generalized to _xfm_net_short and "
    "applied to both devices. The earlier box-only _bs_ct_short "
    "clearance was removed: its ring-only probe passed two proven "
    "short classes (CT lead crossing the other winding on a shared "
    "metal; two same-metal CT leads crossing each other). Tap ports "
    "now append after the fixed [P1, N1, P2, N2] base (CTP before "
    "CTS).",
    "Classic balun via_diag fail-closed (M7T): base_xfm_half.il has a "
    "via_diag branch that uses vias_diagonal, a sourceless library PCell "
    "with no .il and no GDS evidence (ind_ref.gds VIA8 cuts are all "
    "axis-aligned). via_diag=True fails closed with PortError; the "
    "via_to_next axis-aligned stitch alone connects the stack. A_TK/B_TK "
    "move only the via placement (A/B), never the drawn octagon. WI/BB/PA/"
    "PB are dead (signature fidelity).",
    "Concentric crossunder + layer-complete gate (M7T2): M7T's initial "
    "concentric mode shorted — the inner winding's leads crossed the outer "
    "ring on the shared metal (50 µm² on the default instance). The ring-only "
    "_coplanar_ring_short gate missed it (checked rings, not leads). M7T2 "
    "replaces it with the layer-complete Region intersection gate "
    "of the two windings' FULL nets (ring + leads + crossunder pads/bridge) "
    "across EVERY layer either net draws (no pinned layer list). The inner "
    "winding's leads now escape via base_ind_under crossunders on the "
    "parameterized ESCAPE_ME (default derived one level below BALUN_ME; "
    "overridable to any strictly lower metal) — the reference balun.il "
    "mechanism. Nested NT_S>=2 and primary-inner fail closed.",
    "Metal generalization completed for the multi-turn/CT paths (M12, "
    "user-authorized functional fix, 2026-07-12): every user-visible metal "
    "parameter -> stack index conversion (vias/vias_nomet, base_lead, "
    "base_lead_pair, base_ind_under, base_ind_hud_cross, ind_sym, "
    "ind_sym_ct) now goes through _metal_index instead of bare int(), so "
    "the 'AP' alias resolves to index 11 on these paths exactly as it "
    "already did on the single-turn broadside paths (_bs_winding, xfm_bs, "
    "xfm_ms, xfm_balun). Numeric metal strings are bit-identical to the "
    "previous int() behavior (golden-invariance verified on 11 "
    "numeric-metal configs); the CT guard message now prints AP as "
    "'AP' via _metal_name rather than 'M11'.",
    "D1 crossover-junction clearance is rule-driven (M12 Phase 0.5, "
    "user-authorized, 2026-07-12): base_ind_diag / base_xfm_cross / "
    "cross_endpoint_offset key their endpoint extension (ext / OOCHD / OOCH) "
    "on _junction_clearance_const(met, process) = max(the reference "
    "literal, the metal's own min_space + a snap guard) read from the rule "
    "profile instead of the bare reference literal. The reference PCell's "
    "nominal crossover-junction net distance erodes a few nm under nm-grid "
    "snapping of "
    "the 45-degree edges, marginally failing the AP body's own min_space; "
    "the rule-driven clearance widens the AP-body junction to clear it. "
    "Reference mode and any conductor whose min_space fits under the "
    "reference literal get that literal back -> byte-identical geometry "
    "(verified on the "
    "numeric-metal canonical-geometry digest harness); only the AP body "
    "(its min_space plus the snap guard) widens. Ports/labels are "
    "unchanged.",
    "D2/D3 crossover seam-notch healing is rule-driven (M12 Phase 0.5, "
    "user-authorized, 2026-07-12): ind_sym runs _heal_seam_notches in process "
    "mode to close the acute empty wedge where base_ind_diag's 45-degree "
    "crossover edge meets the ring/turn axis-aligned inner edge -- a "
    "0-distance seam on the single-turn (NT=1) lead-ring seam (D2) and a "
    "0-0.064 um sliver at crowded small-OD inner junctions (D3). Both violate "
    "EVERY metal's own min_space (the thinner signal metals as well as AP), "
    "so the fix "
    "necessarily changes NT=1 and small-OD process-mode geometry on all "
    "metals. The heal fills only edge pairs whose two edges share a vertex "
    "(the wedge apex, within SEAM_APEX_TOL_NM) -- never the intended parallel "
    "gaps (inter-turn spiral spacing, D1 junction net distance), so it cannot "
    "short turns (the coil is one galvanic net) -- and commits a fill only "
    "when it strictly lowers that metal's violation count, so a genuinely "
    "infeasible AP crowding corner is left flagged rather than traded for a "
    "different violation. Reference mode is never healed (byte-identical); a "
    "clean coil qualifies no pair and is byte-identical (numeric-metal digest "
    "verified). This is a process-mode-only additive deviation from the "
    "reference PCell, which draws the wedge as-is.",
]

DEMOS = [
    ("base_ind_diag", base_ind_diag, {"W": 2.0, "S": 2.0, "MET": 9}),
    (
        "base_xfm_cross",
        base_xfm_cross,
        {"WI": 2.0, "WO": 2.0, "S": 0.0, "TOP_ME": 9, "BTM_ME": 8},
    ),
    (
        "base_ind_hud_cross",
        base_ind_hud_cross,
        {
            "OD": 60.0,
            "W": 2.0,
            "S": 2.0,
            "OPENING": 10.0,
            "TOP_ME": "9",
            "BTM_ME": "8",
            "under": True,
        },
    ),
    ("base_oct_quad", base_oct_quad, {"OD": 60.0, "W": 2.0, "OP": 0.0, "MET": 9}),
    (
        "base_oct_half",
        base_oct_half,
        {"OD": 60.0, "W": 2.0, "LOP": 5.0, "ROP": 10.0, "MET": 9},
    ),
    ("base_oct", base_oct, {"OD": 60.0, "W": 2.0, "LOP": 0.0, "ROP": 4.0, "MET": 9}),
    ("base_lead", base_lead, {"L": 10.0, "W": 2.0, "TOP_ME": "9", "BTM_ME": "8"}),
    (
        "base_lead_pair",
        base_lead_pair,
        {"W": 2.0, "OPENING": 10.0, "LEAD": 10.0, "TOP_ME": "9", "LEAD_ME": "8"},
    ),
    (
        "ind_sym_3t",
        ind_sym,
        {"OD": 60.0, "W": 2.0, "OPENING": 5.0, "LEAD": 10.0, "S": 2.0, "NT": 3},
    ),
    (
        "ind_sym_ct_3t",
        ind_sym,
        {
            "OD": 60.0,
            "W": 2.0,
            "OPENING": 5.0,
            "LEAD": 10.0,
            "S": 2.0,
            "NT": 3,
            "TOP_ME": "9",
            "CT_ME": "7",
        },
    ),
    # N28 process-backed three-turn demo: every layer/datatype and via rule
    # comes from the rule profile ("process_profile" is expanded to a
    # ProcessRuleContext by generate_all).
    (
        "ind_sym_3t_n28",
        ind_sym,
        {
            "OD": 60.0,
            "W": 2.0,
            "OPENING": 5.0,
            "LEAD": 10.0,
            "S": 2.0,
            "NT": 3,
            "process_profile": "n28_1p10m",
        },
    ),
    # N28 process-backed center-tapped variant that needs only modeled
    # rules: M10/M9 coil, VIA9 crossover arrays, VIA9+VIA8 tap, M8 CT lead.
    (
        "ind_sym_ct_3t_m10m9_ct_m8_n28",
        ind_sym,
        {
            "OD": 60.0,
            "W": 2.0,
            "OPENING": 5.0,
            "LEAD": 10.0,
            "S": 2.0,
            "NT": 3,
            "TOP_ME": "10",
            "CT_ME": "8",
            "process_profile": "n28_1p10m",
        },
    ),
    # N28 process-backed center-tapped inductor with an M1 ground reference
    # fixture: ring + per-port chamfered stub + G0n local-ref pin on the
    # rule-profile M1 layers (ports become EMX p0n=p0n:G0n local-ref ports).
    (
        "ind_sym_ct_3t_m10m9_ct_m8_n28_gnd",
        ind_sym,
        {
            "OD": 60.0,
            "W": 2.0,
            "OPENING": 5.0,
            "LEAD": 10.0,
            "S": 2.0,
            "NT": 4,
            "TOP_ME": "10",
            "CT_ME": "8",
            "process_profile": "n28_1p10m",
            "ground_fixture": GroundFixtureConfig(
                inner_margin_um=5.0, ring_width_um=3.0, stub_width_um=4.0,
                stub_length_um=3.0, stub_chamfer_um=1.0),
        },
    ),
    # N28 process-backed M9-body coil with an M7 CT (n28-rules-slim, user
    # directive 2026-07-19): the M9->M8->M7 tap stack crosses VIA7, a lower
    # via with complete via_primitives geometry (cut/space/enclosure read
    # from the profile) but no via_array_rules entry. Under geometric-only
    # enforcement this now builds -- it used to be recorded as an
    # "expected failure" citing the retired IND.R.1 policy gate
    # (ind_sym_ct_3t_n28.expected_failure.json, removed with this ticket).
    (
        "ind_sym_ct_3t_m7_n28",
        ind_sym,
        {
            "OD": 80.0,
            "W": 4.0,
            "OPENING": 10.0,
            "LEAD": 20.0,
            "S": 2.0,
            "NT": 3,
            "TOP_ME": "9",
            "CT_ME": "7",
            "process_profile": "n28_1p10m",
        },
    ),
    # Balun secondary primitive (M7P, shared with xfm_balun): single MET=9
    # octagon ring.
    (
        "base_balun_sec",
        base_balun_sec,
        {"OD": 60.0, "WI": 4.0, "LOP": 10.0},
    ),
    (
        "base_balun_sec_n28",
        base_balun_sec,
        {"OD": 60.0, "WI": 4.0, "LOP": 10.0, "process_profile": "n28_1p10m"},
    ),
    # Broadside single-turn two-layer transformer (M7R/M7R2): independent
    # OD_P/OD_S + CENTER_SPACING. Clean-room composition (no single .il);
    # N28 builds on VIA8/VIA9 only.
    (
        "xfm_bs",
        xfm_bs,
        {"OD_P": 100.0, "OD_S": 76.0, "W_P": 6.0, "W_S": 6.0,
         "OPENING_P": 8.0, "OPENING_S": 8.0, "LEAD_P": 20.0, "LEAD_S": 20.0,
         "CENTER_SPACING": 8.0, "PRI_ME": "10", "SEC_ME": "9"},
    ),
    (
        "xfm_bs_n28",
        xfm_bs,
        {"OD_P": 100.0, "OD_S": 76.0, "W_P": 6.0, "W_S": 6.0,
         "OPENING_P": 8.0, "OPENING_S": 8.0, "LEAD_P": 20.0, "LEAD_S": 20.0,
         "CENTER_SPACING": 8.0, "PRI_ME": "10", "SEC_ME": "9",
         "process_profile": "n28_1p10m"},
    ),
    (
        "xfm_bs_ct",
        xfm_bs,
        {"OD_P": 90.0, "OD_S": 90.0, "W_P": 6.0, "W_S": 6.0,
         "OPENING_P": 8.0, "OPENING_S": 8.0, "LEAD_P": 20.0, "LEAD_S": 20.0,
         "CENTER_SPACING": 0.0, "PRI_ME": "10", "SEC_ME": "9",
         "CT_P_ME": "8", "CT_S_ME": "8"},
    ),
    (
        "xfm_bs_ct_n28",
        xfm_bs,
        {"OD_P": 90.0, "OD_S": 90.0, "W_P": 6.0, "W_S": 6.0,
         "OPENING_P": 8.0, "OPENING_S": 8.0, "LEAD_P": 20.0, "LEAD_S": 20.0,
         "CENTER_SPACING": 0.0, "PRI_ME": "10", "SEC_ME": "9",
         "CT_P_ME": "8", "CT_S_ME": "8", "process_profile": "n28_1p10m"},
    ),
    # Multi+single transformer with AP (M7S): single-turn AP winding +
    # NT_M=3 multi-turn M10 winding (crossover M9). Clean-room composition.
    (
        "xfm_ms",
        xfm_ms,
        {"OD_S": 100.0, "OD_M": 76.0, "W_S": 6.0, "W_M": 3.0,
         "OPENING_S": 8.0, "OPENING_M": 6.0, "LEAD_S": 20.0, "LEAD_M": 15.0,
         "NT_M": 3, "S_M": 2.0, "CENTER_SPACING": 0.0,
         "SINGLE_ME": "AP", "MULTI_ME": "10"},
    ),
    (
        "xfm_ms_spaced",
        xfm_ms,
        {"OD_S": 100.0, "OD_M": 76.0, "W_S": 6.0, "W_M": 3.0,
         "OPENING_S": 8.0, "OPENING_M": 6.0, "LEAD_S": 20.0, "LEAD_M": 15.0,
         "NT_M": 3, "S_M": 2.0, "CENTER_SPACING": 12.0,
         "SINGLE_ME": "AP", "MULTI_ME": "10"},
    ),
    (
        "xfm_ms_n28",
        xfm_ms,
        {"OD_S": 100.0, "OD_M": 76.0, "W_S": 6.0, "W_M": 3.0,
         "OPENING_S": 8.0, "OPENING_M": 6.0, "LEAD_S": 20.0, "LEAD_M": 15.0,
         "NT_M": 3, "S_M": 2.0, "CENTER_SPACING": 0.0,
         "SINGLE_ME": "AP", "MULTI_ME": "10", "process_profile": "n28_1p10m"},
    ),
    (
        "xfm_bs_m10ap",
        xfm_bs,
        {"OD_P": 90.0, "OD_S": 90.0, "W_P": 6.0, "W_S": 6.0,
         "OPENING_P": 8.0, "OPENING_S": 8.0, "LEAD_P": 20.0, "LEAD_S": 20.0,
         "CENTER_SPACING": 0.0, "PRI_ME": "10", "SEC_ME": "AP"},
    ),
    # Classic same-layer coplanar balun (M7T): primary+secondary on one
    # metal-generic plane BALUN_ME. CS=0 = concentric interleave.
    (
        "xfm_balun",
        xfm_balun,
        {"OD_P": 200.0, "OD_S": 186.0, "W_P": 5.0, "W_S": 5.0, "S": 2.0,
         "OPENING_P": 8.0, "OPENING_S": 8.0, "LEAD_P": 20.0, "LEAD_S": 20.0,
         "NT_P": 1, "NT_S": 1, "CENTER_SPACING": 0.0, "BALUN_ME": "9"},
    ),
    (
        "xfm_balun_n28",
        xfm_balun,
        {"OD_P": 200.0, "OD_S": 186.0, "W_P": 5.0, "W_S": 5.0, "S": 2.0,
         "OPENING_P": 8.0, "OPENING_S": 8.0, "LEAD_P": 20.0, "LEAD_S": 20.0,
         "NT_P": 1, "NT_S": 1, "CENTER_SPACING": 0.0, "BALUN_ME": "9",
         "process_profile": "n28_1p10m"},
    ),
    (
        "xfm_balun_2t1t_n28",
        xfm_balun,
        {"OD_P": 200.0, "OD_S": 172.0, "W_P": 5.0, "W_S": 5.0, "S": 2.0,
         "OPENING_P": 8.0, "OPENING_S": 16.0, "LEAD_P": 20.0, "LEAD_S": 20.0,
         "NT_P": 2, "NT_S": 1, "CENTER_SPACING": 0.0, "BALUN_ME": "9",
         "process_profile": "n28_1p10m"},
    ),
    # Type 3 same-layer overlapping-inductor ("twisted") transformer
    # (ticket 03 recipe/argv passthrough -- .scratch/xfm-tw-twisted/):
    # NR=3 concentric rings shared half-and-half by P/S, no CT (spec.md
    # "非目标"). Clean-room composition (no single .il source).
    (
        "xfm_tw",
        xfm_tw,
        {"OD": 200.0, "W": 4.0, "S": 2.0, "NR": 3, "OPENING_P": 10.0,
         "OPENING_N": 10.0, "LEAD": 20.0, "SL_ME": "9"},
    ),
    # N28 AP body (dives to M10): the pre-scanned good-build dims from
    # test_pcell_inductor_python_port_clean.py's _TW_N28_BODY_MATRIX
    # ("ap", "AP", 3, 260.0, 6.0, 6.0, ...) -- AP's own rule min_space
    # needs the looser W=6/S=6 to clear _tw_slot_half_width's ring-pitch
    # guard (see that matrix's own comment).
    (
        "xfm_tw_n28",
        xfm_tw,
        {"OD": 260.0, "W": 6.0, "S": 6.0, "NR": 3, "OPENING_P": 10.0,
         "OPENING_N": 10.0, "LEAD": 20.0, "SL_ME": "AP",
         "process_profile": "n28_1p10m"},
    ),
    # Type 3 same-layer interleaved ("Rabjohn/Frlan") transformer (ticket
    # 04, .scratch/xfm-il-interleaved/): P and S alternate radial bands on
    # one metal plane SL_ME, no CT -- the SAME reference geometry
    # test_pcell_inductor_python_port_clean.py's `_il()` helper defaults
    # to. Clean-room composition (no single .il source).
    (
        "xfm_il",
        xfm_il,
        {"OD": 200.0, "W": 4.0, "S": 2.0, "NT_P": 3, "NT_S": 3,
         "OPENING_P": 14.0, "OPENING_S": 14.0, "LEAD_P": 20.0,
         "LEAD_S": 20.0, "SL_ME": "9"},
    ),
    # N28 M9 body with an UPWARD CTP tap to M10 (ticket 03c: a downward CTP
    # is structurally blocked in every legal configuration, so the upward
    # direction is the representative CT sample here) -- the SAME dims
    # test_pcell_inductor_python_port_clean.py's `_il_n28()` helper uses.
    (
        "xfm_il_n28_m9_ctp_m10",
        xfm_il,
        {"OD": 200.0, "W": 5.0, "S": 2.5, "NT_P": 3, "NT_S": 3,
         "OPENING_P": 18.0, "OPENING_S": 18.0, "LEAD_P": 20.0,
         "LEAD_S": 20.0, "SL_ME": "9", "CT_P_ME": "10",
         "process_profile": "n28_1p10m"},
    ),
]


def _write_coordinates_json(cell, gds_path, json_path, mode_metadata):
    """Record the polygons and labels exactly as present in the written GDS."""
    import json

    polygons = []
    for layer, pts_list in sorted(_read_gds_polygons(gds_path).items()):
        for pts in pts_list:
            polygons.append(
                {
                    "gds_layer": layer[0],
                    "gds_datatype": layer[1],
                    "layer_name": _layer_display_name(layer),
                    "points_nm": [list(p) for p in pts],
                    "points_um": [[p[0] * DBU_UM, p[1] * DBU_UM] for p in pts],
                }
            )
    labels = []
    for _tag, layer, text, pt in cell.flat_labels():
        labels.append(
            {
                "gds_layer": layer[0],
                "gds_datatype": layer[1],
                "text": text,
                "point_nm": [pt[0], pt[1]],
                "point_um": [pt[0] * DBU_UM, pt[1] * DBU_UM],
            }
        )
    doc = {
        "cell": cell.name,
        "function": cell.function,
        "params": cell.params,
        "dbu_um": DBU_UM,
        "grid_um": GRID_UM,
        **mode_metadata,
        "polygons": polygons,
        "labels": labels,
        "emx_ports": cell.emx_ports,
        "instantiation_log": cell.instantiation_log(),
    }
    json_path.write_text(json.dumps(doc, indent=2))


def _render_png(gds_path, png_path, title, process: ProcessRuleContext | None = None):
    """Render the polygons of a GDS file (parsed via KLayout) to PNG.

    ``process`` is optional (default ``None``, matching every call site
    that predates this parameter -- generate_all's reference-mode demos,
    experiments/m13_ct_validation/visual_review.py, gen_samples.py's
    original call): when supplied, its process rule profile's own layer
    catalog additionally names layers the generic M1-M10/via1-9 patterns
    can't reach (N28's AP/RV). With no ``process``, rendering is
    byte-identical to before this parameter existed."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MplPolygon

    name_map = _process_layer_names(process) if process is not None else None
    polygons = _read_gds_polygons(gds_path)
    fig, ax = plt.subplots(figsize=(9, 9))
    # draw metals first, cuts on top
    for layer in sorted(polygons, key=lambda k: (k[0] >= 51, k[0])):
        name = _layer_display_name(layer, name_map)
        color = _LAYER_COLORS.get(name, "#7f7f7f")
        is_cut = layer[0] >= 51
        for pts in polygons[layer]:
            ax.add_patch(
                MplPolygon(
                    [(x * DBU_UM, y * DBU_UM) for x, y in pts],
                    closed=True,
                    facecolor=color,
                    alpha=0.9 if is_cut else 0.45,
                    edgecolor=color,
                    linewidth=0.6,
                )
            )
        ax.plot([], [], color=color, label=_render_legend_label(layer, name_map))
    ax.autoscale_view()
    ax.set_aspect("equal")
    ax.set_xlabel("x (um)")
    ax.set_ylabel("y (um)")
    ax.set_title(title)
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, linewidth=0.3, alpha=0.4)
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _write_report(out_dir, manifest):
    import json

    report = {
        "title": "PCell inductor Python port (clean-port families)",
        "provenance": {
            "license": "MIT (original code; construction conventions follow the reference SKILL PCells)",
            "status": "in-package product implementation (ic_opt.em.pcell)",
            "independent_gds_reference": "/home/zzchen/Prj/Prj_For_N65/ind_ref.gds",
        },
        "layer_mapping": {
            "metal_m": "GDS layer 30+m, datatype 0 (tsmcN28_1p10m.proc: M1=31..M10=40)",
            "via_m_to_m_plus_1": "GDS layer 50+m, datatype 0 (via1=51..via9=59)",
        },
        "modes": {
            "reference": (
                "process=None: reconstructed ind_ref via rule (0.36/0.34/0.22 "
                "um) on datatype-0 layers; PCell/ind_ref shape study only, "
                "NOT N28 DRC proof"
            ),
            "process": (
                "process=process_rule_context(profile): every layer/datatype "
                "and via array from the process rule profile via "
                "GeometryRuleAdapter.plan_passive_via_array; geometric-only "
                "enforcement (n28-rules-slim, user directive 2026-07-19) -- "
                "fails closed only on missing via geometry (no "
                "via_primitives entry, or an incomplete enclosure map) for "
                "the requested via, not on the retired via_restrictions "
                "(IND.R.1) or passive_via_array_coverage policy gates"
            ),
        },
        "function_mapping": FUNCTION_MAPPING,
        "known_deviations": KNOWN_DEVIATIONS,
        "outputs": manifest,
    }
    (out_dir / "pcell_inductor_python_port_report.json").write_text(
        json.dumps(report, indent=2)
    )

    lines = [
        "# PCell Inductor Python Port (clean)",
        "",
        "The six clean-port families' construction chain (original code,",
        "licensed with the repository under MIT); its function names follow the",
        "reference SKILL PCells each construction was first studied against.",
        "",
        "## Function mapping",
        "",
        "| Python function | PCell source | Parameters |",
        "| --- | --- | --- |",
    ]
    for entry in FUNCTION_MAPPING:
        lines.append(
            f"| `{entry['python_function']}` | `{entry['pcell_source']}` "
            f"| {', '.join(entry['parameters'])} |"
        )
    lines += [
        "",
        "## Crossover layer relationship",
        "",
        "The reference ind_sym.il hardcodes TOP_ME=\"9\"/BTM_ME=\"8\" and",
        "base_ind_hud_cross.il hardcodes the octagon ring on MET 9. This",
        "port defaults to that same local crossover: an M9 same-layer mirrored",
        "diagonal over an M8 underpass diagonal with via8 arrays only at the",
        "underpass endpoints. When TOP_ME is overridden, the coil follows",
        "TOP_ME and the underpass follows TOP_ME-1. The independent reference",
        "ind_ref.gds shows the default relationship (M9=39 diagonals, M8=38,",
        "via8=58 arrays) and no M10 at all.",
        "",
        "## Known deviations (fail closed)",
        "",
    ]
    lines += [f"- {d}" for d in KNOWN_DEVIATIONS]
    lines += ["", "## Generated outputs", ""]
    for entry in manifest:
        lines.append(
            f"- `{entry['basename']}.gds/.png/.coordinates.json` -- "
            f"`{entry['function']}({entry['params']})`"
        )
    lines.append("")
    (out_dir / "pcell_inductor_python_port_report.md").write_text("\n".join(lines))


def generate_all(out_dir):
    """Build every demo cell, write GDS, render PNG from the GDS, dump JSON."""

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    contexts: dict[str, ProcessRuleContext] = {}
    for basename, fn, params in DEMOS:
        kwargs = dict(params)
        profile = kwargs.pop("process_profile", None)
        if profile is not None:
            if profile not in contexts:
                contexts[profile] = process_rule_context(profile)
            context = contexts[profile]
            kwargs["process"] = context
        cell = fn(**kwargs)
        gds_path = out_dir / f"{basename}.gds"
        write_gds(cell, gds_path)
        _write_coordinates_json(
            cell,
            gds_path,
            out_dir / f"{basename}.coordinates.json",
            _mode_metadata(profile),
        )
        _render_png(
            gds_path,
            out_dir / f"{basename}.png",
            f"{basename}: {cell.function} {params}",
        )
        if cell.emx_ports:
            (out_dir / f"{basename}.emx_ports").write_text(
                "\n".join(emx_port_lines(cell.emx_ports)) + "\n"
            )
        manifest.append(
            {
                "basename": basename,
                "function": cell.function,
                "params": {
                    k: asdict(v) if is_dataclass(v) and not isinstance(v, type) else v
                    for k, v in params.items()
                },
                "mode": "process" if profile else "reference",
                "process_profile": profile,
            }
        )
    _write_report(out_dir, manifest)
    return manifest


def main():

    out_dir = Path(__file__).resolve().parent / "outputs"
    manifest = generate_all(out_dir)
    print(f"wrote {len(manifest)} demo cells to {out_dir}")
