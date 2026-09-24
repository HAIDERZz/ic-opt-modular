"""The clean-port PCell facade: every construction name of the six families, re-exported from the family modules.

Provenance
----------
The construction chain was first studied against the reference SKILL PCells
under ``gdsgen_ref/pcell/`` (em-opt workspace); the function names and
coordinate conventions keep that vocabulary so the two can be compared. The
code itself is original and licensed with the repository (MIT). Each Python
function corresponds to one reference PCell:

==================  =====================================================
Python function     PCell source file
==================  =====================================================
vias                (library PCell, no .il source in gdsgen_ref; interface
                    reconstructed from every call site and from the via
                    geometry observed in ind_ref.gds, an independent
                    reference layout that is not shipped)
base_ind_diag       gdsgen_ref/pcell/inductor/base_ind_diag.il
base_xfm_cross      gdsgen_ref/pcell/transformer/base_xfm_cross.il
base_oct_quad       gdsgen_ref/pcell/common/base_oct_quad.il
base_oct_half       gdsgen_ref/pcell/common/base_oct_half.il
base_oct            gdsgen_ref/pcell/common/base_oct.il
base_ind_hud_cross  gdsgen_ref/pcell/inductor/base_ind_hud_cross.il
base_lead           gdsgen_ref/pcell/common/base_lead.il
base_lead_pair      gdsgen_ref/pcell/common/base_lead_pair.il
ind_sym             gdsgen_ref/pcell/inductor/ind_sym.il
ind_sym_ct          gdsgen_ref/pcell/inductor/ind_sym_ct.il (unified
                    into ind_sym's CT_ME path in M13)
==================  =====================================================

Coordinate conventions
----------------------
* All PCell parameters are micrometres, exactly as in SKILL.
* Internally every point is snapped to integer nanometres (Virtuoso dbu
  0.001 um); the SKILL grid helpers (ceiltogrid/roundtogrid/floortogrid)
  operate on the 0.005 um mask grid as in the reference environment.
* In reference mode, ``MetalVec(MET-1)`` (metal MET) maps to GDS layer
  ``30 + MET`` and the via layer between metal m and m+1 maps to GDS layer
  ``50 + m`` following the reference proc file under ``gdsgen_ref/``
  with datatype 0 (the proc file's own datatypes exist for EMX purposes;
  the datatype is irrelevant for the structural comparison reference mode
  is used for and is documented as a deviation). Process mode instead
  takes every layer/datatype pair from the process rule profile.
* Virtuoso instance orientations R0/R90/R180/R270/MX/MY are supported.
  MX mirrors across the X axis (y -> -y), MY across the Y axis (x -> -x).

Known deviations (fail-closed, see the generated report for the full list)
---------------------------------------------------------------------------
* ``vias`` is a PDK library PCell whose SKILL source is not part of
  gdsgen_ref. Reconstructed semantics: a stack of metal rectangles spanning
  x in [0, Width], y in [0, Length] on every metal BTM_ME..TOP_ME plus
  centred square-cut arrays on each intermediate via layer. Cut size,
  cut spacing and enclosure come from this module's reference via
  constants (reconstructed from the via cells inside ind_ref.gds). When TOP_ME == BTM_ME only metal is drawn
  (base_lead.il relies on exactly this to draw its lead).
* RFVLSI/DMEXCL dummy layers, labels, base_oct_fill, base_em_gr and the
  rfvlsiEM* EM-port helpers are not ported; ``dummy`` parameters are
  accepted for signature fidelity but ignored.
* ``viat``/``viad`` of base_xfm_cross are accepted but unused: in the
  reference base_xfm_cross.il both endpoint vias instances are created
  unconditionally; "no cuts on the same-layer cross" is a consequence of
  TOP_ME == BTM_ME, not of viat/viad.
"""

# Wrap-up P1 (2026-07-28): the construction code now lives in the
# _pcell_* sibling modules, split by device family; this module is
# the stable facade -- every historical name is re-exported below,
# so package imports AND the by-path loaders (sys.modules
# ["clean_port_mod"]) keep working unchanged. Imports are absolute
# because a by-path load has no package context for relative ones.

from ic_opt.em.pcell._pcell_core import (  # noqa: F401
    _EPS,
    _LAYER_COLORS,
    _LAYER_NAMES,
    _ORIENTS,
    _SEAM_HEAL_MAX_PASSES,
    DBU_UM,
    GRID_DBU,
    GRID_UM,
    JUNCTION_SNAP_GUARD_UM,
    PI,
    REFERENCE_JUNCTION_CLEARANCE_UM,
    REFERENCE_MIN_MET_SPACING_UM,
    SEAM_APEX_TOL_NM,
    VIA_CUT_UM,
    VIA_ENC_UM,
    VIA_SPACE_UM,
    Cell,
    Inst,
    Label,
    Port,
    PortError,
    ProcessRuleContext,
    Shape,
    _add_process_via_cuts,
    _add_via_cuts,
    _check_port_lattice_invariant,
    _conductor_for_drawing,
    _cut_positions,
    _edge_pair_apex_gap,
    _junction_clearance_const,
    _layer_display_name,
    _metal,
    _metal_index,
    _metal_name,
    _min_met_spacing,
    _mode_metadata,
    _ms_layer_regions,
    _nm,
    _pin,
    _port_dict,
    _process_layer_names,
    _read_gds_polygons,
    _render_legend_label,
    _required_parallel_spacing,
    _transform_box,
    _xfm_order_ports,
    add_wide_path,
    bridge_y_reach,
    ceiltogrid,
    cross_endpoint_offset,
    emx_port_lines,
    finalize_emx_ports,
    floortogrid,
    max_opening,
    metal_drawing_pin,
    metal_layer,
    metal_pin_layer,
    process_metal_layer,
    process_pin_layer,
    process_rule_context,
    process_via_layer,
    roundtogrid,
    snap_nm_to_grid,
    transform_point,
    via_layer,
    vias,
    vias_nomet,
    write_gds,
)
from ic_opt.em.pcell._pcell_demo import (  # noqa: F401
    DEMOS,
    FUNCTION_MAPPING,
    KNOWN_DEVIATIONS,
    _render_png,
    _write_coordinates_json,
    _write_report,
    generate_all,
    main,
)
from ic_opt.em.pcell._pcell_guards import (  # noqa: F401
    _check_bridge_escape_clearance,
    _check_ind_winding_segments,
    _check_opening,
    _check_trace_rules,
    _check_winding_fit,
    _heal_seam_notches,
    _xfm_net_overlap,
    _xfm_net_regions,
    _xfm_net_short,
    _xfm_nets_are_drc_separate,
)
from ic_opt.em.pcell._pcell_ind_sym import (  # noqa: F401
    _compact_two_turn_candidate,
    _compact_two_turn_candidate_is_qualified,
    _compact_two_turn_lane_offsets,
    _compact_two_turn_via_length,
    _compact_two_turn_winding,
    _ind_ct_adjacency_guard,
    _ind_ct_tap,
    _ind_ring_turns,
    _needs_full_width_lead_landing,
    ind_sym,
)
from ic_opt.em.pcell._pcell_primitives import (  # noqa: F401
    base_balun_sec,
    base_ind_diag,
    base_ind_hud_cross,
    base_ind_under,
    base_lead,
    base_lead_pair,
    base_oct,
    base_oct_half,
    base_oct_half_vias,
    base_oct_quad,
    base_oct_quad_vias,
    base_xfm_cross,
    base_xfm_half,
)
from ic_opt.em.pcell._pcell_xfm_balun import (  # noqa: F401
    _add_balun_ct,
    _balun_crossunder,
    _ci_winding,
    xfm_balun,
)
from ic_opt.em.pcell._pcell_xfm_bs import (  # noqa: F401
    _bs_center_tap,
    _bs_winding,
    xfm_bs,
)
from ic_opt.em.pcell._pcell_xfm_il import (  # noqa: F401
    _il_bridge_diagonal_regions,
    _il_bridge_escape_lane_offset,
    _il_bridge_lane_offset,
    _il_bridge_spacing_guard,
    _il_bridge_target_gap,
    _il_ct_adjacency_guard,
    _il_ct_metal_guard,
    _il_ct_region,
    _il_ct_tap_exact,
    _il_primary_coil,
    _il_shifted_hud_cross,
    _il_staggered_ring_turns,
    xfm_il,
)
from ic_opt.em.pcell._pcell_xfm_ms import (  # noqa: F401
    xfm_ms,
)
from ic_opt.em.pcell._pcell_xfm_tw import (  # noqa: F401
    _TW_CARDINAL_UNIT,
    _TW_FIXED_PORT_ORDER,
    _TW_MIRROR_ANGLE,
    _TW_TANGENT_CCW,
    TwArc,
    TwLeg,
    _tw_cardinal_point,
    _tw_check_opening,
    _tw_edge_point,
    _tw_leg,
    _tw_leg_endpoints,
    _tw_leg_frame,
    _tw_leg_waypoints,
    _tw_max_opening,
    _tw_mirror_angle,
    _tw_mirror_segments,
    _tw_oct_chamfer,
    _tw_oct_vertices,
    _tw_oct_walk_pts,
    _tw_plan,
    _tw_plan_p,
    _tw_render_winding,
    _tw_slot_half_width,
    _tw_stub_zone,
    xfm_tw,
)
from ic_opt.em.pcell.fixture import (  # noqa: F401
    GroundFixtureConfig,
    _body_bbox_um,
    _drawing_bbox_um,
    add_ground_fixture,
)

if __name__ == "__main__":
    main()
