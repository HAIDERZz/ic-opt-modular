"""Tests for the clean PCell inductor Python port.

The module under test ships inside the installed package (M11 R2:
src/ic_opt.em.pcell/devices/clean_port/pcell_inductor_port_clean.py --
see that directory's README.md for the provenance/licensing note), and is
still loaded from its file path here (not a normal package import) so this
test exercises the exact module object in isolation. All geometry
assertions go through KLayout (klayout.db) parsing of generated GDS files,
never through screenshots or hand-read coordinates.

Every expected coordinate in this file is derived from the PCell SKILL
sources under gdsgen_ref/pcell/ (see the docstring of each test).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import math
import pathlib
import re
import sys

import klayout.db as kdb
import pytest

from tests.ic_opt.pcell.conftest import PACKAGE_DIR, requires_profile

pytestmark = requires_profile("n28_1p10m")
ROOT = pathlib.Path(__file__).resolve().parents[3]
MOD_PATH = PACKAGE_DIR / "pcell_inductor_port_clean.py"

_spec = importlib.util.spec_from_file_location("pcell_inductor_port_clean", MOD_PATH)
port = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = port
_spec.loader.exec_module(port)

GRID_NM = 5  # 0.005 um mask grid in dbu (dbu = 0.001 um)
_nm = port._nm  # um -> dbu (nm) grid snap, reused by xfm_bs placement tests


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def write_and_parse(cell, tmp_path, name):
    """Write a port Cell to GDS and parse it back with KLayout.

    Returns {(layer, datatype): [ [ (x_nm, y_nm), ... ], ... ]} of the
    flattened top cell.
    """
    gds = tmp_path / f"{name}.gds"
    port.write_gds(cell, gds)
    return parse_gds(gds)


def parse_gds(gds_path):
    ly = kdb.Layout()
    ly.read(str(gds_path))
    assert abs(ly.dbu - 0.001) < 1e-12
    top = ly.top_cell()
    top.flatten(True)
    out = {}
    for li in ly.layer_indexes():
        info = ly.get_info(li)
        polys = []
        for s in top.shapes(li).each():
            if s.is_text():
                continue
            poly = s.polygon
            pts = [(p.x, p.y) for p in poly.each_point_hull()]
            polys.append(pts)
        if polys:
            out[(info.layer, info.datatype)] = polys
    return out


def canon(points):
    """Canonical form of a polygon point list (rotation/direction invariant)."""
    pts = list(points)
    best = None
    for seq in (pts, pts[::-1]):
        for i in range(len(seq)):
            rot = tuple(seq[i:] + seq[:i])
            if best is None or rot < best:
                best = rot
    return best


def polys_geometrically_equal(a, b):
    """Geometric equality (XOR emptiness) tolerating collinear-vertex removal."""
    ra = kdb.Region([kdb.Polygon([kdb.Point(x, y) for x, y in a])])
    rb = kdb.Region([kdb.Polygon([kdb.Point(x, y) for x, y in b])])
    return (ra ^ rb).is_empty()


def bbox_of(polys):
    xs = [x for poly in polys for x, _ in poly]
    ys = [y for poly in polys for _, y in poly]
    return min(xs), min(ys), max(xs), max(ys)


def assert_on_grid(layer_polys):
    for layer, polys in layer_polys.items():
        for poly in polys:
            for x, y in poly:
                assert x % GRID_NM == 0 and y % GRID_NM == 0, (
                    f"off-grid point ({x},{y}) nm on layer {layer}"
                )


def cluster_boxes(boxes, gap_nm=500):
    """Group axis-aligned boxes into clusters of mutually-near boxes."""
    clusters = []
    for box in boxes:
        placed = False
        for cl in clusters:
            for other in cl:
                if (
                    box[0] <= other[2] + gap_nm
                    and other[0] <= box[2] + gap_nm
                    and box[1] <= other[3] + gap_nm
                    and other[1] <= box[3] + gap_nm
                ):
                    cl.append(box)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            clusters.append([box])
    merged = True
    while merged:
        merged = False
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                near = any(
                    a[0] <= b[2] + gap_nm
                    and b[0] <= a[2] + gap_nm
                    and a[1] <= b[3] + gap_nm
                    and b[1] <= a[3] + gap_nm
                    for a in clusters[i]
                    for b in clusters[j]
                )
                if near:
                    clusters[i].extend(clusters.pop(j))
                    merged = True
                    break
            if merged:
                break
    return clusters


def rect_bbox(poly):
    xs = [x for x, _ in poly]
    ys = [y for _, y in poly]
    return min(xs), min(ys), max(xs), max(ys)


# ---------------------------------------------------------------------------
# grid + transform primitives
# ---------------------------------------------------------------------------


def test_grid_helpers_match_skill_semantics():
    """ceiltogrid/roundtogrid/floortogrid operate on the 0.005 um mask grid."""
    assert port.GRID_UM == pytest.approx(0.005)
    assert port.ceiltogrid(0.8284) == pytest.approx(0.830)
    assert port.ceiltogrid(0.8334271) == pytest.approx(0.835)
    assert port.roundtogrid(0.8284) == pytest.approx(0.830)
    assert port.roundtogrid(0.5869) == pytest.approx(0.585)
    assert port.floortogrid(12.42) == pytest.approx(12.42)
    assert port.floortogrid(12.4249) == pytest.approx(12.42)


def test_transform_orientations():
    """Virtuoso orientations R0/R90/R180/R270/MX/MY on nm integer points."""
    p = (10, 5)
    assert port.transform_point(p, "R0") == (10, 5)
    assert port.transform_point(p, "R90") == (-5, 10)
    assert port.transform_point(p, "R180") == (-10, -5)
    assert port.transform_point(p, "R270") == (5, -10)
    assert port.transform_point(p, "MX") == (10, -5)  # mirror across X axis
    assert port.transform_point(p, "MY") == (-10, 5)  # mirror across Y axis


# ---------------------------------------------------------------------------
# base_ind_diag (gdsgen_ref/pcell/inductor/base_ind_diag.il)
# ---------------------------------------------------------------------------


def test_base_ind_diag_extended_branch_points_bbox_grid(tmp_path):
    """W=2, S=2 (S <= 2*sqrt(2)) -> 8-point polygon on MetalVec(MET-1).

    From base_ind_diag.il: C=ceiltogrid(2*tan(pi/8)+0.005)=0.835,
    C2=ceiltogrid(C/sqrt(2))=0.595, OO=6, OOCH=2.405,
    ext=ceiltogrid(2.01*sqrt(2)-2)=0.845, P=4.
    """
    cell = port.base_ind_diag(W=2.0, S=2.0, MET=9)
    layers = write_and_parse(cell, tmp_path, "diag_ext")
    m9 = port.metal_layer(9)
    assert set(layers) == {m9}
    assert len(layers[m9]) == 1
    poly = layers[m9][0]
    assert len(poly) == 8
    expected = [
        (0, -1570),
        (0, -3250),
        (2000, -3250),
        (2000, -2405),
        (6000, 1595),
        (6000, 3250),
        (4000, 3250),
        (4000, 2430),
    ]
    assert canon(poly) == canon(expected)
    assert bbox_of([poly]) == (0, -3250, 6000, 3250)
    assert_on_grid(layers)


def test_base_ind_diag_wide_spacing_branch(tmp_path):
    """W=2, S=4 (S > 2*sqrt(2)) -> 6-point polygon.

    C=0.835, C2=0.595, OO=8, OOCH=3.405, P=6.
    """
    cell = port.base_ind_diag(W=2.0, S=4.0, MET=8)
    layers = write_and_parse(cell, tmp_path, "diag_wide")
    m8 = port.metal_layer(8)
    assert set(layers) == {m8}
    assert len(layers[m8]) == 1
    poly = layers[m8][0]
    assert len(poly) == 6
    expected = [
        (0, -2595),
        (0, -3405),
        (2000, -3405),
        (8000, 2595),
        (8000, 3405),
        (6000, 3405),
    ]
    assert canon(poly) == canon(expected)
    assert bbox_of([poly]) == (0, -3405, 8000, 3405)
    assert_on_grid(layers)


# ---------------------------------------------------------------------------
# base_xfm_cross (gdsgen_ref/pcell/transformer/base_xfm_cross.il)
# ---------------------------------------------------------------------------


def _xfm_cross_layers(tmp_path, **kwargs):
    params = dict(WI=2.0, WO=2.0, S=0.0, TOP_ME=9, BTM_ME=8)
    params.update(kwargs)
    cell = port.base_xfm_cross(**params)
    return write_and_parse(cell, tmp_path, "xfm_cross")


def test_base_xfm_cross_single_diag_and_two_via_arrays(tmp_path):
    """base_xfm_cross = base_ind_diag(W=WO, S=2S+WI, MET=BTM_ME) + endpoint vias.

    With WI=2, WO=2, S=0, TOP=9, BTM=8 (the base_ind_hud_cross underpass
    configuration): the diagonal is drawn on M8, each endpoint carries a
    WOxWO vias block (M8 pad + M9 pad + via8 cuts).
    OOCH=2.405, OOCHD=0.845 -> blocks at y in [-5.25,-3.25] (x 0..2) and
    y in [3.25,5.25] (x 4..6).
    """
    layers = _xfm_cross_layers(tmp_path)
    m8, m9, v8 = port.metal_layer(8), port.metal_layer(9), port.via_layer(8)
    assert set(layers) == {m8, m9, v8}

    m8_diags = [p for p in layers[m8] if len(p) == 8]
    m8_pads = [p for p in layers[m8] if len(p) == 4]
    assert len(m8_diags) == 1, "exactly one diagonal polygon expected"
    assert len(m8_pads) == 2, "one endpoint pad per diagonal end expected"
    assert len(layers[m9]) == 2
    assert all(len(p) == 4 for p in layers[m9])

    cuts = [rect_bbox(p) for p in layers[v8]]
    assert all(
        (x2 - x1, y2 - y1) == (360, 360) for x1, y1, x2, y2 in cuts
    ), "via8 cut size must match the 0.36 um reference cut"
    clusters = cluster_boxes(cuts)
    assert len(clusters) == 2, "via cuts must form exactly two endpoint arrays"
    assert_on_grid(layers)


def test_base_xfm_cross_via_arrays_align_with_diag_endpoints(tmp_path):
    """Via blocks abut the diagonal endpoint bboxes and share their x-span."""
    layers = _xfm_cross_layers(tmp_path)
    m8, m9, v8 = port.metal_layer(8), port.metal_layer(9), port.via_layer(8)
    diag = next(p for p in layers[m8] if len(p) == 8)
    dx1, dy1, dx2, dy2 = rect_bbox(diag)
    pads = sorted((rect_bbox(p) for p in layers[m9]), key=lambda b: b[1])
    bot, top = pads
    # bottom block: x span equals the lower diagonal endpoint strip [0, WO]
    assert (bot[0], bot[2]) == (0, 2000)
    assert bot[3] == dy1, "bottom via block must abut the diagonal bottom edge"
    assert bot[1] == dy1 - 2000
    # top block: x span equals the upper endpoint strip [WI+WO+2S, +WO]
    assert (top[0], top[2]) == (4000, 6000)
    assert top[1] == dy2, "top via block must abut the diagonal top edge"
    assert top[3] == dy2 + 2000
    assert (dx1, dx2) == (0, 6000)

    cuts = [rect_bbox(p) for p in layers[v8]]
    clusters = sorted(
        cluster_boxes(cuts), key=lambda cl: min(b[1] for b in cl)
    )
    for cluster, pad in zip(clusters, pads):
        cx1 = min(b[0] for b in cluster)
        cy1 = min(b[1] for b in cluster)
        cx2 = max(b[2] for b in cluster)
        cy2 = max(b[3] for b in cluster)
        assert cx1 >= pad[0] and cy1 >= pad[1] and cx2 <= pad[2] and cy2 <= pad[3]
        # cut array is centered on the pad (and thus on the endpoint strip)
        assert (cx1 + cx2) == (pad[0] + pad[2])
        assert (cy1 + cy2) == (pad[1] + pad[3])


def test_base_xfm_cross_same_layer_has_no_cuts(tmp_path):
    """TOP_ME == BTM_ME (the mirrored same-layer cross) produces zero via cuts."""
    layers = _xfm_cross_layers(tmp_path, TOP_ME=9, BTM_ME=9, viat=False, viad=False)
    m9 = port.metal_layer(9)
    assert set(layers) == {m9}
    assert len([p for p in layers[m9] if len(p) == 8]) == 1
    assert len([p for p in layers[m9] if len(p) == 4]) == 2


def test_base_xfm_cross_top_true_draws_diag_on_top_metal(tmp_path):
    """top=True routes the diagonal onto TOP_ME instead of BTM_ME."""
    layers = _xfm_cross_layers(tmp_path, top=True)
    m9 = port.metal_layer(9)
    assert len([p for p in layers[m9] if len(p) == 8]) == 1


# ---------------------------------------------------------------------------
# base_oct_quad / base_oct_half / base_oct
# (gdsgen_ref/pcell/common/base_oct_quad.il, base_oct_half.il, base_oct.il)
# ---------------------------------------------------------------------------


def test_base_oct_quad_normal_branch_points_grid(tmp_path):
    """OD=60, W=2, OP=0 (OP <= BA-C branch).

    C=0.835, A=roundtogrid(60/(2+sqrt(2)))=17.575, B=24.85, BA=12.42,
    so the quad runs from the arm (0,0)..(2,12.42) to the top edge y=29.995.
    """
    cell = port.base_oct_quad(OD=60.0, W=2.0, OP=0.0, MET=9)
    layers = write_and_parse(cell, tmp_path, "oct_quad")
    m9 = port.metal_layer(9)
    assert set(layers) == {m9}
    assert len(layers[m9]) == 1
    poly = layers[m9][0]
    expected = [
        (0, 12420),
        (0, 0),
        (2000, 0),
        (2000, 11585),
        (18410, 27995),
        (30000, 27995),
        (30000, 29995),
        (17575, 29995),
    ]
    assert canon(poly) == canon(expected)
    assert_on_grid(layers)


def test_base_oct_quad_large_opening_branch(tmp_path):
    """OP > BA-C keeps a half-width stub instead of the full arm.

    The SKILL vertex (W, W+BA) is collinear on the 45-degree edge between
    (W/2, W/2+BA) and (A, BA+A); KLayout drops it on write, so the polygon
    is compared geometrically instead of vertex-by-vertex.
    """
    cell = port.base_oct_quad(OD=60.0, W=2.0, OP=12.0, MET=9)
    layers = write_and_parse(cell, tmp_path, "oct_quad_op")
    poly = layers[port.metal_layer(9)][0]
    expected = [
        (2000, 14420),
        (1000, 13420),
        (1000, 11585),
        (2000, 11585),
        (18410, 27995),
        (30000, 27995),
        (30000, 29995),
        (17575, 29995),
    ]
    assert polys_geometrically_equal(poly, expected)
    assert_on_grid(layers)


def test_base_oct_half_mirrors_quads_and_stays_on_grid(tmp_path):
    """base_oct_half = quad(LOP) at (-OD/2,0) R0 + quad(ROP) at (OD/2,0) MY."""
    cell = port.base_oct_half(OD=60.0, W=2.0, LOP=5.0, ROP=10.0, MET=9)
    layers = write_and_parse(cell, tmp_path, "oct_half")
    m9 = port.metal_layer(9)
    assert len(layers[m9]) == 2
    left, right = sorted(layers[m9], key=lambda p: min(x for x, _ in p))
    # left arm bottom at y=LOP on the outer edge x=-30
    assert (-30000, 5000) in left and (-28000, 5000) in left
    # right arm bottom at y=ROP mirrored to x=+30
    assert (30000, 10000) in right and (28000, 10000) in right
    assert_on_grid(layers)


def test_base_oct_transforms_stay_on_grid(tmp_path):
    """base_oct = half R0 + half MX; all four quads land on the 0.005 grid."""
    cell = port.base_oct(OD=60.0, W=2.0, LOP=0.0, ROP=4.0, MET=9)
    layers = write_and_parse(cell, tmp_path, "oct")
    m9 = port.metal_layer(9)
    assert len(layers[m9]) == 4
    assert bbox_of(layers[m9]) == (-30000, -29995, 30000, 29995)
    pts = {p for poly in layers[m9] for p in poly}
    # left side closes at y=0 (LOP=0), right side opens at y=+/-ROP
    assert (-30000, 0) in pts and (-28000, 0) in pts
    assert (30000, 4000) in pts and (30000, -4000) in pts
    assert (28000, 4000) in pts and (28000, -4000) in pts
    assert_on_grid(layers)


# ---------------------------------------------------------------------------
# base_lead / base_lead_pair
# (gdsgen_ref/pcell/common/base_lead.il, base_lead_pair.il)
# ---------------------------------------------------------------------------


def test_base_lead_via_and_metal_bboxes_align(tmp_path):
    """base_lead draws its lead with the vias PCell: Length=W, Width=L.

    With TOP_ME != BTM_ME the metal bboxes on both layers coincide and all
    cuts stay inside them with the reconstructed 0.22 um enclosure.
    """
    cell = port.base_lead(L=10.0, W=2.0, TOP_ME="9", BTM_ME="8")
    layers = write_and_parse(cell, tmp_path, "lead")
    m8, m9, v8 = port.metal_layer(8), port.metal_layer(9), port.via_layer(8)
    assert bbox_of(layers[m9]) == (0, 0, 10000, 2000)
    assert bbox_of(layers[m8]) == (0, 0, 10000, 2000)
    cuts = [rect_bbox(p) for p in layers[v8]]
    assert len(cuts) == 28  # 14 columns x 2 rows for a 10 x 2 um block
    for x1, y1, x2, y2 in cuts:
        assert x1 >= 220 and y1 >= 220 and x2 <= 10000 - 220 and y2 <= 2000 - 220
    assert_on_grid(layers)


def test_base_lead_same_layer_draws_metal_only(tmp_path):
    cell = port.base_lead(L=10.0, W=2.0, TOP_ME="9", BTM_ME="9")
    layers = write_and_parse(cell, tmp_path, "lead_same")
    assert set(layers) == {port.metal_layer(9)}


def test_base_lead_pair_ports_align_with_via_blocks(tmp_path):
    """Leads at y=OPENING and y=-OPENING-W; TOP_ME != LEAD_ME adds WxW vias."""
    cell = port.base_lead_pair(
        W=2.0, OPENING=10.0, LEAD=10.0, TOP_ME="9", LEAD_ME="8"
    )
    layers = write_and_parse(cell, tmp_path, "lead_pair")
    m8, m9, v8 = port.metal_layer(8), port.metal_layer(9), port.via_layer(8)
    lead_boxes = sorted(
        (rect_bbox(p) for p in layers[m8] if rect_bbox(p)[2] == 10000),
        key=lambda b: b[1],
    )
    assert lead_boxes == [(0, -12000, 10000, -10000), (0, 10000, 10000, 12000)]
    pads = sorted((rect_bbox(p) for p in layers[m9]), key=lambda b: b[1])
    assert pads == [(0, -12000, 2000, -10000), (0, 10000, 2000, 12000)]
    cuts = [rect_bbox(p) for p in layers[v8]]
    clusters = sorted(cluster_boxes(cuts), key=lambda cl: min(b[1] for b in cl))
    assert len(clusters) == 2
    for cluster, pad, lead in zip(clusters, pads, lead_boxes):
        for x1, y1, x2, y2 in cluster:
            assert x1 >= pad[0] and y1 >= pad[1] and x2 <= pad[2] and y2 <= pad[3]
            assert x1 >= lead[0] and y1 >= lead[1] and x2 <= lead[2] and y2 <= lead[3]
    assert_on_grid(layers)


def test_base_lead_pair_same_lead_metal_has_no_vias(tmp_path):
    cell = port.base_lead_pair(
        W=2.0, OPENING=10.0, LEAD=10.0, TOP_ME="9", LEAD_ME="9"
    )
    layers = write_and_parse(cell, tmp_path, "lead_pair_same")
    assert set(layers) == {port.metal_layer(9)}
    assert len(layers[port.metal_layer(9)]) == 2


def _sxy_sign(poly):
    """Sign of the vertex covariance: >0 rising diagonal, <0 falling."""
    n = len(poly)
    mx = sum(x for x, _ in poly) / n
    my = sum(y for _, y in poly) / n
    sxy = sum((x - mx) * (y - my) for x, y in poly)
    return math.copysign(1.0, sxy)


def test_diag_orientation_helper_self_check():
    rising = [(0, 0), (10, 10), (10, 12), (0, 2)]
    falling = [(0, 12), (10, 2), (10, 0), (0, 10)]
    assert _sxy_sign(rising) > 0 > _sxy_sign(falling)


# ---------------------------------------------------------------------------
# base_ind_hud_cross (gdsgen_ref/pcell/inductor/base_ind_hud_cross.il)
# ---------------------------------------------------------------------------

# Expected diagonals for OD=60, W=2, S=2, TOP_ME="9", BTM_ME="8":
# hud-local: C=roundtogrid(2*tan(pi/8))=0.830, C2=0.585, OOCH=2.415,
# LOP=OOCH+roundtogrid(2*sqrt(2)-2)+0.01=3.255; underpass cross R0 at
# (-OD/2,0); same-layer cross MY at (-OD/2+P+W,0)=(-24,0).
M8_DIAG_EXPECTED = [
    (-30000, -1570),
    (-30000, -3250),
    (-28000, -3250),
    (-28000, -2405),
    (-24000, 1595),
    (-24000, 3250),
    (-26000, 3250),
    (-26000, 2430),
]
M9_DIAG_EXPECTED = [
    (-24000, -1570),
    (-24000, -3250),
    (-26000, -3250),
    (-26000, -2405),
    (-30000, 1595),
    (-30000, 3250),
    (-28000, 3250),
    (-28000, 2430),
]


def _hud_cross_layers(tmp_path):
    cell = port.base_ind_hud_cross(
        OD=60.0, W=2.0, S=2.0, OPENING=10.0, TOP_ME="9", BTM_ME="8", under=True
    )
    return write_and_parse(cell, tmp_path, "hud_cross")


def _split_diags(polys, expected):
    matches = [p for p in polys if polys_geometrically_equal(p, expected)]
    return matches


def test_hud_cross_contains_underpass_and_same_layer_cross(tmp_path):
    """base_ind_hud_cross = base_oct + underpass cross (BTM=TOP-1, vias) +
    same-layer mirrored cross (BTM=TOP, no cuts)."""
    layers = _hud_cross_layers(tmp_path)
    m8, m9, v8 = port.metal_layer(8), port.metal_layer(9), port.via_layer(8)
    # underpass diagonal on M8 at the exact PCell coordinates
    m8_diags = [p for p in layers[m8] if len(p) == 8]
    assert len(m8_diags) == 1
    assert polys_geometrically_equal(m8_diags[0], M8_DIAG_EXPECTED)
    # mirrored same-layer diagonal on M9 at the exact PCell coordinates
    m9_diags = _split_diags(layers[m9], M9_DIAG_EXPECTED)
    assert len(m9_diags) == 1
    # ring: four base_oct_quad polygons on M9 (MET hardcoded to 9 in the
    # reference hud_cross source)
    ring = [p for p in layers[m9] if len(p) == 8 and rect_bbox(p)[2] - rect_bbox(p)[0] > 10000]
    assert len(ring) == 4
    assert bbox_of(ring) == (-30000, -29995, 30000, 29995)
    # Endpoint pads: the outer-arm pads (x in [-30,-28]) overlap this ring;
    # the inner-side pads (x in [-26,-24]) are the interface to the next
    # inner turn and only make contact once composed inside ind_sym.
    pads = [rect_bbox(p) for p in layers[m9] if len(p) == 4]
    assert len(pads) == 4  # 2 with cuts (underpass) + 2 metal-only (same-layer)
    ring_region = kdb.Region(
        [kdb.Polygon([kdb.Point(x, y) for x, y in p]) for p in ring]
    )
    outer_pads = [b for b in pads if b[0] == -30000]
    inner_pads = [b for b in pads if b[0] == -26000]
    assert len(outer_pads) == 2 and len(inner_pads) == 2
    for b in outer_pads:
        assert not (
            ring_region & kdb.Region([kdb.Polygon(kdb.Box(b[0], b[1], b[2], b[3]))])
        ).is_empty(), "outer endpoint pad must land on the ring arm"
    assert v8 in layers
    assert_on_grid(layers)


def test_hud_cross_diagonals_have_opposite_angles(tmp_path):
    """Top-metal (M9) and under-metal (M8) diagonals cross with opposite slopes.

    Note: ind_sym.il hardcodes TOP_ME="9"/BTM_ME="8" and base_ind_hud_cross.il
    hardcodes the ring on M9, so with PCell defaults the crossover pair is
    M9 (same-layer, mirrored) over M8 (underpass) -- exactly the relationship
    seen in ind_ref.gds (M9=39 diagonals, M8=38, via8=58 arrays).
    """
    layers = _hud_cross_layers(tmp_path)
    m8, m9 = port.metal_layer(8), port.metal_layer(9)
    m8_diag = next(p for p in layers[m8] if len(p) == 8)
    m9_diag = _split_diags(layers[m9], M9_DIAG_EXPECTED)[0]
    assert _sxy_sign(m8_diag) > 0, "underpass diagonal must rise to the right"
    assert _sxy_sign(m9_diag) < 0, "same-layer diagonal must fall to the right"
    # both diagonals cross in projection
    ra = kdb.Region([kdb.Polygon([kdb.Point(x, y) for x, y in m8_diag])])
    rb = kdb.Region([kdb.Polygon([kdb.Point(x, y) for x, y in m9_diag])])
    assert not (ra & rb).is_empty(), "diagonals must overlap in projection"


def test_hud_cross_no_via_at_central_crossing(tmp_path):
    """Via cuts exist only at the underpass endpoints, never at the crossing."""
    layers = _hud_cross_layers(tmp_path)
    m8, m9, v8 = port.metal_layer(8), port.metal_layer(9), port.via_layer(8)
    m8_diag = next(p for p in layers[m8] if len(p) == 8)
    m9_diag = _split_diags(layers[m9], M9_DIAG_EXPECTED)[0]
    crossing = kdb.Region(
        [kdb.Polygon([kdb.Point(x, y) for x, y in m8_diag])]
    ) & kdb.Region([kdb.Polygon([kdb.Point(x, y) for x, y in m9_diag])])
    assert not crossing.is_empty()
    cuts = [rect_bbox(p) for p in layers[v8]]
    cut_region = kdb.Region(
        [kdb.Polygon(kdb.Box(x1, y1, x2, y2)) for x1, y1, x2, y2 in cuts]
    )
    assert (crossing & cut_region).is_empty(), "no via may sit on the crossing"
    clusters = sorted(cluster_boxes(cuts), key=lambda cl: min(b[1] for b in cl))
    assert len(clusters) == 2
    bounds = [
        (
            min(b[0] for b in cl),
            min(b[1] for b in cl),
            max(b[2] for b in cl),
            max(b[3] for b in cl),
        )
        for cl in clusters
    ]
    # endpoint blocks: (-30..-28, -5.25..-3.25) and (-26..-24, 3.25..5.25)
    lo, hi = bounds
    assert -30000 <= lo[0] and lo[2] <= -28000 and -5250 <= lo[1] and lo[3] <= -3250
    assert -26000 <= hi[0] and hi[2] <= -24000 and 3250 <= hi[1] and hi[3] <= 5250


def test_hud_cross_under_false_omits_crossover(tmp_path):
    cell = port.base_ind_hud_cross(
        OD=60.0, W=2.0, S=2.0, OPENING=0.0, TOP_ME="9", BTM_ME="8", under=False
    )
    layers = write_and_parse(cell, tmp_path, "hud_cross_no_under")
    assert set(layers) == {port.metal_layer(9)}
    assert len(layers[port.metal_layer(9)]) == 4


# ---------------------------------------------------------------------------
# ind_sym (gdsgen_ref/pcell/inductor/ind_sym.il)
# ---------------------------------------------------------------------------


def _ind_sym_3t():
    return port.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0, NT=3)


def test_ind_sym_nt3_instantiation_order_matches_pcell():
    """NT=3: inner i=1 hud cross (OD-2P, MY), outer hud cross (OD, R0),
    odd innermost base_oct (OD-4P), then base_lead_pair at OD/2:0."""
    log = _ind_sym_3t().instantiation_log()
    seq = [(e["function"], e["orient"]) for e in log]
    assert seq == [
        ("base_ind_hud_cross", "MY"),
        ("base_ind_hud_cross", "R0"),
        ("base_oct", "R0"),
        ("base_lead_pair", "R0"),
    ]
    inner, outer, oct_inner, leads = log
    assert inner["params"]["OD"] == pytest.approx(52.0)  # OD - 2*1*P
    # crossover-facing openings use the exact cross endpoint edge
    # (OOCH+OOCHD = 3.25 for W=2, S=2) so via blocks sit flush inside the
    # arms, matching ind_ref.gds (alignment correction; the .il uses 2*W)
    assert inner["params"]["OPENING"] == pytest.approx(3.25)
    assert outer["params"]["OD"] == pytest.approx(60.0)
    assert outer["params"]["OPENING"] == pytest.approx(5.0)
    assert oct_inner["params"]["OD"] == pytest.approx(44.0)  # OD - 2*(NT-1)*P
    assert oct_inner["params"]["LOP"] == pytest.approx(0.0)
    assert oct_inner["params"]["ROP"] == pytest.approx(3.25)
    assert oct_inner["params"]["MET"] == 9
    assert leads["origin_um"] == [30.0, 0.0]
    assert leads["params"]["LEAD"] == pytest.approx(10.0)
    assert leads["params"]["OPENING"] == pytest.approx(5.0)
    assert leads["params"]["TOP_ME"] == "9"
    assert leads["params"]["LEAD_ME"] == "9"  # default -> no lead vias


def test_ind_sym_nt3_geometry(tmp_path):
    """Three ring levels, two crossovers, one lead pair, all on grid."""
    layers = write_and_parse(_ind_sym_3t(), tmp_path, "ind_sym_3t")
    m8, m9, v8 = port.metal_layer(8), port.metal_layer(9), port.via_layer(8)
    ring = [p for p in layers[m9] if len(p) == 8 and rect_bbox(p)[2] - rect_bbox(p)[0] > 10000]
    assert len(ring) == 12  # 4 quads x (outer + inner ring + innermost oct)
    m9_diags = [p for p in layers[m9] if len(p) == 8 and p not in ring]
    assert len(m9_diags) == 2  # one same-layer cross per hud cross
    m8_diags = [p for p in layers[m8] if len(p) == 8]
    assert len(m8_diags) == 2  # one underpass per hud cross
    clusters = cluster_boxes([rect_bbox(p) for p in layers[v8]])
    assert len(clusters) == 4  # two endpoint arrays per underpass cross
    strips = [rect_bbox(p) for p in layers[m9] if len(p) == 4]
    assert (30000, 5000, 40000, 7000) in strips  # P1 lead
    assert (30000, -7000, 40000, -5000) in strips  # N1 lead
    # composed: every crossover endpoint pad must sit FLUSH inside a ring
    # arm (full containment, as in ind_ref.gds), not merely overlap it
    ring_region = kdb.Region(
        [kdb.Polygon([kdb.Point(x, y) for x, y in p]) for p in ring]
    )
    pads = [
        rect_bbox(p)
        for p in layers[m9]
        if len(p) == 4 and rect_bbox(p)[2] - rect_bbox(p)[0] == 2000
    ]
    assert len(pads) == 8  # 4 pads per hud cross x 2 crossovers
    for b in pads:
        pad_region = kdb.Region([kdb.Polygon(kdb.Box(b[0], b[1], b[2], b[3]))])
        assert (pad_region - ring_region).is_empty(), (
            f"crossover pad {b} protrudes beyond its ring arm"
        )
    assert_on_grid(layers)


# ---------------------------------------------------------------------------
# ind_sym_ct (gdsgen_ref/pcell/inductor/ind_sym_ct.il)
# ---------------------------------------------------------------------------


def test_ind_sym_ct_nt3_taps_winding_midpoint_on_m7(tmp_path):
    """ind_sym_ct = ind_sym + M9->M7 tap stack at the winding symmetry point
    + CT lead leaving on M7 under the rings (ind_ref.gds-style center tap).

    For odd NT the symmetry point is the closed left column of the innermost
    base_oct (LOP=0): x in [-OD/2+(NT-1)*P, +W] = [-22, -20]. The CT lead is
    a pure BTM_ME(M7) strip from -OD/2-LEAD to the tap; via8+via7 cut arrays
    exist only at the tap block, exactly as in ind_ref.gds where the center
    tap exits on M7 beneath the turns on the crossover side.
    """
    cell = port.ind_sym(
        OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0, NT=3, TOP_ME="9", CT_ME="7"
    )
    log = cell.instantiation_log()
    # unified build: the winding primitives are instanced inline (no nested
    # ind_sym child any more); the tap lead + vias stack append last.
    assert [e["function"] for e in log][-2:] == ["base_lead", "vias"]
    lead, tap = log[-2], log[-1]
    assert lead["origin_um"] == [-40.0, -1.0]  # (-OD/2-LEAD, -W/2)
    assert lead["params"]["L"] == pytest.approx(20.0)  # LEAD + (NT-1)*P + W
    assert lead["params"]["PINTXT"] == "CT"
    assert lead["params"]["TOP_ME"] == "7" and lead["params"]["BTM_ME"] == "7"
    assert tap["origin_um"] == [-22.0, -1.0]  # (-OD/2+(NT-1)*P, -W/2)
    assert tap["params"]["TOP_ME"] == 9 and tap["params"]["BTM_ME"] == 7

    layers = write_and_parse(cell, tmp_path, "ind_sym_ct_3t")
    m7, m9 = port.metal_layer(7), port.metal_layer(9)
    v7, v8 = port.via_layer(7), port.via_layer(8)
    # CT lead + tap plate live on M7 only between -40 and -20
    assert bbox_of(layers[m7]) == (-40000, -1000, -20000, 1000)
    # via7 cuts exist only inside the tap block
    tap_box = (-22000, -1000, -20000, 1000)
    for x1, y1, x2, y2 in (rect_bbox(p) for p in layers[v7]):
        assert x1 >= tap_box[0] and y1 >= tap_box[1]
        assert x2 <= tap_box[2] and y2 <= tap_box[3]
    # the tap M9 plate sits flush inside the innermost ring column
    ring = [
        p
        for p in layers[m9]
        if len(p) == 8 and rect_bbox(p)[2] - rect_bbox(p)[0] > 10000
    ]
    ring_region = kdb.Region(
        [kdb.Polygon([kdb.Point(x, y) for x, y in p]) for p in ring]
    )
    tap_region = kdb.Region([kdb.Polygon(kdb.Box(*tap_box))])
    assert (tap_region - ring_region).is_empty(), "tap must sit on ring metal"
    # one extra via8 cluster (the tap) on top of the 4 crossover arrays
    clusters = cluster_boxes([rect_bbox(p) for p in layers[v8]])
    assert len(clusters) == 5
    # the wrapped ind_sym still provides its own P1/N1 leads
    strips = [rect_bbox(p) for p in layers[m9] if len(p) == 4]
    assert (30000, 5000, 40000, 7000) in strips
    assert (30000, -7000, 40000, -5000) in strips
    assert_on_grid(layers)


def test_ind_sym_ct_nt2_taps_closed_column_on_lead_side(tmp_path):
    """Even NT: the innermost turn closes on the right (ROP=0), so the tap
    column and the CT lead exit sit on the P1/N1 lead side.

    NT=2, OD=60, W=2, S=2: tap column x in [OD/2-(NT-1)*P-W, OD/2-(NT-1)*P]
    = [24, 26]; lead runs from the tap to OD/2+LEAD = 40.
    """
    cell = port.ind_sym(
        OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0, NT=2, TOP_ME="9", CT_ME="7"
    )
    log = cell.instantiation_log()
    # unified build: the winding primitives are instanced inline (no nested
    # ind_sym child any more); the tap lead + vias stack append last.
    assert [e["function"] for e in log][-2:] == ["base_lead", "vias"]
    lead, tap = log[-2], log[-1]
    assert lead["origin_um"] == [24.0, -1.0]
    assert lead["params"]["L"] == pytest.approx(16.0)  # LEAD + (NT-1)*P + W
    assert tap["origin_um"] == [24.0, -1.0]

    layers = write_and_parse(cell, tmp_path, "ind_sym_ct_2t")
    m7, m9 = port.metal_layer(7), port.metal_layer(9)
    v7, v8 = port.via_layer(7), port.via_layer(8)
    assert bbox_of(layers[m7]) == (24000, -1000, 40000, 1000)
    tap_box = (24000, -1000, 26000, 1000)
    for x1, y1, x2, y2 in (rect_bbox(p) for p in layers[v7]):
        assert x1 >= tap_box[0] and y1 >= tap_box[1]
        assert x2 <= tap_box[2] and y2 <= tap_box[3]
    ring = [
        p
        for p in layers[m9]
        if len(p) == 8 and rect_bbox(p)[2] - rect_bbox(p)[0] > 10000
    ]
    ring_region = kdb.Region(
        [kdb.Polygon([kdb.Point(x, y) for x, y in p]) for p in ring]
    )
    tap_region = kdb.Region([kdb.Polygon(kdb.Box(*tap_box))])
    assert (tap_region - ring_region).is_empty(), "tap must sit on ring metal"
    # NT=2: one crossover (2 endpoint arrays) + 1 tap array
    clusters = cluster_boxes([rect_bbox(p) for p in layers[v8]])
    assert len(clusters) == 3
    # CT lead (y in [-1,1]) stays clear of the P1/N1 leads (y in +/-[5,7])
    strips = [rect_bbox(p) for p in layers[m9] if len(p) == 4]
    assert (30000, 5000, 40000, 7000) in strips
    assert (30000, -7000, 40000, -5000) in strips
    assert_on_grid(layers)


def test_vias_rejects_extent_too_small_for_enclosure():
    """Fail closed: no silent under-enclosed single-cut fallback."""
    with pytest.raises(port.PortError):
        port.vias(Length=0.5, Width=2.0, TOP_ME=9, BTM_ME=8)


def test_gds_cell_names_use_strict_charset(tmp_path):
    """Top cell names must stay inside the GDSII name charset (no dots)."""
    cell = port.ind_sym(
        OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0, NT=3, TOP_ME="9", CT_ME="7"
    )
    gds = tmp_path / "name_check.gds"
    port.write_gds(cell, gds)
    ly = kdb.Layout()
    ly.read(str(gds))
    name = ly.top_cell().name
    assert re.fullmatch(r"[A-Za-z0-9_$?]+", name), name


def test_gds_top_cell_name_always_equals_file_stem(tmp_path):
    """write_gds itself must name the top cell after the OUTPUT FILE stem,
    unconditionally (user default, requested repeatedly: Virtuoso import
    expects gds filename == top cell name; the plugin path already resolved
    this via _write_geometry_outputs but the raw write_gds path -- samples,
    demos -- kept leaking parametric cell names)."""
    cell = port.ind_sym(
        OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0, NT=2, TOP_ME="9"
    )
    for fname in ("xfm_il_ct_none.gds", "some_sample_v2.gds"):
        gds = tmp_path / fname
        port.write_gds(cell, gds)
        ly = kdb.Layout()
        ly.read(str(gds))
        assert ly.top_cell().name == gds.stem, (ly.top_cell().name, gds.stem)


# ---------------------------------------------------------------------------
# N28 process-backed mode (rule.yaml via GeometryRuleAdapter) vs reference
# ---------------------------------------------------------------------------


def test_reference_mode_still_uses_reference_via_cut_size(tmp_path):
    """Without process=, the reconstructed 0.36 um reference cuts remain."""
    cell = port.base_xfm_cross(WI=2.0, WO=2.0, S=0.0, TOP_ME=9, BTM_ME=8)
    layers = write_and_parse(cell, tmp_path, "reference_xfm_cross")
    cuts = [rect_bbox(p) for p in layers[port.via_layer(8)]]
    assert cuts
    assert all((x2 - x1, y2 - y1) == (360, 360) for x1, y1, x2, y2 in cuts)


def test_n28_process_mode_uses_via8_rule_cut_size(tmp_path):
    """N28 mode draws VIA8 cuts at the rule.yaml 0.46 um size, on (58, 80)."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.base_xfm_cross(
        WI=2.0, WO=2.0, S=0.0, TOP_ME=9, BTM_ME=8, process=ctx
    )
    layers = write_and_parse(cell, tmp_path, "n28_xfm_cross")
    via8_layer = (58, 80)
    cuts = [rect_bbox(p) for p in layers[via8_layer]]
    assert cuts
    assert all((x2 - x1, y2 - y1) == (460, 460) for x1, y1, x2, y2 in cuts)
    assert (58, 0) not in layers, "reference via layer must not leak into N28 mode"


def test_n28_process_mode_uses_via7_rule_cut_size(tmp_path):
    """N28 mode draws VIA7 cuts at the rule.yaml 0.1 um size / 0.1 um
    spacing / 0.04 um enclosure, on (57, 20) -- geometric-only enforcement
    (n28-rules-slim, user directive 2026-07-19): VIA7 is a lower via with
    complete via_primitives geometry, no longer gated by the retired
    via_restrictions (IND.R.1) or passive_via_array_coverage policy gates.
    Mirrors test_n28_process_mode_uses_via8_rule_cut_size above."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.base_xfm_cross(
        WI=2.0, WO=2.0, S=0.0, TOP_ME=8, BTM_ME=7, process=ctx
    )
    layers = write_and_parse(cell, tmp_path, "n28_xfm_cross_via7")
    via7_layer = (57, 20)
    cuts = [rect_bbox(p) for p in layers[via7_layer]]
    assert cuts
    assert all((x2 - x1, y2 - y1) == (100, 100) for x1, y1, x2, y2 in cuts)
    xs = sorted({b[0] for b in cuts})
    if len(xs) > 1:
        assert xs[1] - xs[0] == 200  # rule pitch 0.1+0.1 = 0.2 um
    assert (57, 0) not in layers, "reference via layer must not leak into N28 mode"


def test_n28_process_mode_via7_array_follows_adapter_plan(tmp_path):
    """VIA7 array placement (spacing/enclosure/centring), mirroring
    test_n28_process_mode_via_array_follows_adapter_plan's VIA9 coverage:
    for a 2x2 um window VIA7 gives a centred array at rule pitch 0.2 um
    with >=0.04 um enclosure -- geometric-only enforcement (n28-rules-slim)
    plans this straight from via_primitives, no via_array_rules entry
    needed (VIA7 has none)."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.vias(Length=2.0, Width=2.0, TOP_ME=8, BTM_ME=7, process=ctx)
    layers = write_and_parse(cell, tmp_path, "n28_vias_via7_block")
    cuts = sorted(rect_bbox(p) for p in layers[(57, 20)])
    assert len(cuts) >= 4
    xs = sorted({b[0] for b in cuts})
    ys = sorted({b[1] for b in cuts})
    assert xs[1] - xs[0] == 200 and ys[1] - ys[0] == 200  # rule pitch 0.2 um
    x1, y1 = cuts[0][0], cuts[0][1]
    x2, y2 = cuts[-1][2], cuts[-1][3]
    assert (x1 + x2) == 2000 and (y1 + y2) == 2000  # centred in the block
    assert x1 >= 40 and y1 >= 40  # rule enclosure 0.04 um


def test_n28_process_mode_uses_rule_profile_layer_datatypes(tmp_path):
    """N28 mode draws M8 on (38,20), M9 on (39,80), VIA8 on (58,80)."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.base_xfm_cross(
        WI=2.0, WO=2.0, S=0.0, TOP_ME=9, BTM_ME=8, process=ctx
    )
    layers = write_and_parse(cell, tmp_path, "n28_xfm_cross_layers")
    assert set(layers) == {(38, 20), (39, 80), (58, 80)}
    # diagonal on M8 (38,20), endpoint pads on both metals
    assert any(len(p) == 8 for p in layers[(38, 20)])
    assert len(layers[(39, 80)]) == 2


def test_n28_process_mode_via_array_follows_adapter_plan(tmp_path):
    """Cut spacing/enclosure come from plan_passive_via_array: for a 2x2 um
    window VIA8 gives a 2x2 array, 0.9 um pitch, centred with >=0.08 um
    enclosure."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.vias(Length=2.0, Width=2.0, TOP_ME=9, BTM_ME=8, process=ctx)
    layers = write_and_parse(cell, tmp_path, "n28_vias_block")
    cuts = sorted(rect_bbox(p) for p in layers[(58, 80)])
    assert len(cuts) == 4
    xs = sorted({b[0] for b in cuts})
    ys = sorted({b[1] for b in cuts})
    assert xs[1] - xs[0] == 900 and ys[1] - ys[0] == 900  # rule pitch 0.9 um
    x1, y1 = cuts[0][0], cuts[0][1]
    x2, y2 = cuts[-1][2], cuts[-1][3]
    assert (x1 + x2) == 2000 and (y1 + y2) == 2000  # centred in the block
    assert x1 >= 80 and y1 >= 80  # rule enclosure 0.08 um
    metal_boxes = [rect_bbox(p) for p in layers[(38, 20)] + layers[(39, 80)]]
    assert all(b == (0, 0, 2000, 2000) for b in metal_boxes)


def test_n28_process_mode_rejects_window_below_min_cut_count():
    """A 1x1 um window fits only one VIA8 cut, below the passive min_count
    of 4: the adapter refusal is surfaced as a PortError (fail closed)."""
    ctx = port.process_rule_context("n28_1p10m")
    with pytest.raises(port.PortError, match="cannot fit VIA8 passive via array"):
        port.vias(Length=1.0, Width=1.0, TOP_ME=9, BTM_ME=8, process=ctx)


def test_n28_process_mode_m9_coil_with_m7_ct_builds():
    """n28-rules-slim (user directive 2026-07-19): the M9->M8->M7 CT stack
    needs VIA7, which has complete via_primitives geometry (cut 0.1um,
    spacing 0.1um, enclosure 0.04um) -- generator enforcement is
    geometric-only, so this now builds (previously fell closed on the
    retired via_restrictions/IND.R.1 policy gate, VIAy class, no LOWMEDN
    exception). OPENING must stay <= max_opening(80,4)=14.9 (M7U guard);
    10.0 keeps the rest of the geometry valid."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.ind_sym(
        OD=80.0,
        W=4.0,
        OPENING=10.0,
        LEAD=20.0,
        S=2.0,
        TOP_ME="9",
        CT_ME="7",
        NT=3,
        process=ctx,
    )
    assert {q["name"] for q in cell.emx_ports} == {"P1", "N1", "CT"}


def test_n28_process_mode_ind_sym_3t(tmp_path):
    """The three-turn inductor is constructible in N28 mode: M8/M9 rule
    layers only, VIA8 0.46 um cuts at the crossover endpoints."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.ind_sym(
        OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0, NT=3, process=ctx
    )
    layers = write_and_parse(cell, tmp_path, "n28_ind_sym_3t")
    assert set(layers) == {(38, 20), (39, 80), (58, 80)}
    cuts = [rect_bbox(p) for p in layers[(58, 80)]]
    assert all((x2 - x1, y2 - y1) == (460, 460) for x1, y1, x2, y2 in cuts)
    assert len(cluster_boxes(cuts)) == 4  # two endpoint arrays per crossover


def test_n28_process_mode_m10_coil_with_m8_ct(tmp_path):
    """N28: M10/M9 coil with an M8 CT uses only modeled rules -- M10
    (40,80) ring, M9 (39,80) underpass, VIA9 (59,80) endpoint arrays and a
    VIA9+VIA8 tap stack; via8 cuts exist only at the tap."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.ind_sym(
        OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0, NT=3,
        TOP_ME="10", CT_ME="8", process=ctx,
    )
    layers = write_and_parse(cell, tmp_path, "n28_ct_m10")
    assert set(layers) == {(40, 80), (39, 80), (38, 20), (59, 80), (58, 80)}
    for via_layer_key in ((59, 80), (58, 80)):
        for x1, y1, x2, y2 in (rect_bbox(p) for p in layers[via_layer_key]):
            assert (x2 - x1, y2 - y1) == (460, 460)
    tap_box = (-22000, -1000, -20000, 1000)
    for x1, y1, x2, y2 in (rect_bbox(p) for p in layers[(58, 80)]):
        assert x1 >= tap_box[0] and y1 >= tap_box[1]
        assert x2 <= tap_box[2] and y2 <= tap_box[3]
    clusters = cluster_boxes([rect_bbox(p) for p in layers[(59, 80)]])
    assert len(clusters) == 5  # 4 crossover endpoint arrays + 1 tap level


def test_n28_process_mode_m9_coil_m4_ct_builds():
    """n28-rules-slim (user directive 2026-07-19): the M9->..->M4 CT stack
    crosses VIA8/VIA7/VIA6/VIA5/VIA4, all with complete via_primitives
    geometry -- this now builds (previously fell closed citing "VIA4 is
    restricted by IND.R.1 ... not implemented by this generator", the
    VIAx class's unimplemented LOWMEDN exception)."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.ind_sym(
        OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0, NT=3,
        TOP_ME="9", CT_ME="4", process=ctx,
    )
    assert {q["name"] for q in cell.emx_ports} == {"P1", "N1", "CT"}


# ---------------------------------------------------------------------------
# M12 Phase 0.5 -- pcell seam remediation (user-authorized, 2026-07-12)
#
# D1: rule-driven crossover-junction clearance (byte-identical for numeric).
# D2/D3: process-mode acute-wedge seam-notch healing (NT=1 lead-ring seam and
# small-OD inner junctions) -- fixes every metal's own min_space.
# All DRC assertions go through KLayout space_check with the rule profile's
# own min_space (never a hardcoded value), self-contained (no audit import).
# ---------------------------------------------------------------------------


def _drawing_layer(ctx, conductor):
    return tuple(ctx.adapter.layer(conductor).drawing)


def _min_space_um(ctx, conductor):
    return ctx.adapter.metal_rule(conductor).min_space_um


def _min_space_findings(cell, tmp_path, name, layer_dt, min_space_um):
    """Count Euclidian min_space edge pairs on one drawing layer of a built
    coil -- the same check ap_drc_audit performs, kept self-contained here."""
    layers = write_and_parse(cell, tmp_path, name)
    region = kdb.Region()
    for pts in layers.get(layer_dt, []):
        region.insert(kdb.Polygon([kdb.Point(x, y) for x, y in pts]))
    region.merge()
    pairs = region.space_check(
        int(round(min_space_um / 0.001)), False, kdb.Metrics.Euclidian)
    return pairs.count()


def test_d1_ind_sym_ap_body_junction_clean_after_phase05(tmp_path):
    """D1: an AP-body ind_sym coil clears AP's own min_space at the crossover
    junctions. This is the representative point (OD=120/W=5/S=2.5/NT=3) that
    flagged 4 edge pairs at 1.9905 um BEFORE Phase 0.5 (pinned in
    tests/device_db/test_ap_drc_audit.py); the rule-driven junction clearance
    widens the AP-body junction so it now audits clean."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.ind_sym(OD=120.0, W=5.0, OPENING=8.0, LEAD=20.0, S=2.5, NT=3,
                        TOP_ME="AP", BTM_ME="10", process=ctx)
    n = _min_space_findings(cell, tmp_path, "d1_ap_s25",
                            _drawing_layer(ctx, "AP"), _min_space_um(ctx, "AP"))
    assert n == 0


def test_xfm_balun_ap_body_builds_and_audits_clean_after_phase05(tmp_path):
    """An AP-body xfm_balun (both windings coplanar on AP) builds and audits
    clean at a valid nested config. NOTE (M12 Phase 0.5 scope): the balun's
    single-turn winding is drawn via base_xfm_half (not ind_sym), so the
    ind_sym seam heal does not reach its crossunder seam -- smaller OD_P balun
    bodies retain a base_xfm_half seam residual and keep an OD_P floor
    (Phase 0 domain: OD_P>=160/240 for the balun). This pins the clean
    corner; the residual is documented in AP_DRC_AUDIT.md's Phase 0.5 section.
    Both AP and the M10 crossunder audit clean here."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.xfm_balun(OD_P=240.0, OD_S=224.0, W_P=5.0, W_S=5.0, S=2.5,
                          NT_P=1, NT_S=1, BALUN_ME="AP", process=ctx)
    assert _min_space_findings(cell, tmp_path, "balun_ap_ap",
                               _drawing_layer(ctx, "AP"),
                               _min_space_um(ctx, "AP")) == 0
    assert _min_space_findings(cell, tmp_path, "balun_ap_m10",
                               _drawing_layer(ctx, "M10"),
                               _min_space_um(ctx, "M10")) == 0


def test_d1_ind_sym_ap_s2p0_turn_gap_cleared_by_chamfer_staircase(tmp_path):
    """D1's documented residual, RESOLVED (six-family tight-spacing
    clearance, 2026-07-28): at S == AP min_space (2.0 um) each ring's
    independent A/BA/C quantization used to snap-erode the turn-to-turn
    45-degree gap ~6 nm below the rule -- this test originally PINNED
    that shortfall as a known limitation (practical floor >= ~2.05 um).
    ``chamfer_staircase_delta`` now biases the inner rings' chamfer
    baseline so the diagonal separation clears the floor at S == rule
    exactly; both at-floor and just-above builds audit clean, and the
    practical floor IS the rule value."""
    ctx = port.process_rule_context("n28_1p10m")
    at_floor = port.ind_sym(OD=120.0, W=5.0, OPENING=8.0, LEAD=20.0, S=2.0,
                            NT=3, TOP_ME="AP", BTM_ME="10", process=ctx)
    assert _min_space_findings(at_floor, tmp_path, "d1_ap_s20",
                               _drawing_layer(ctx, "AP"),
                               _min_space_um(ctx, "AP")) == 0
    above = port.ind_sym(OD=120.0, W=5.0, OPENING=8.0, LEAD=20.0, S=2.1,
                         NT=3, TOP_ME="AP", BTM_ME="10", process=ctx)
    assert _min_space_findings(above, tmp_path, "d1_ap_s21",
                               _drawing_layer(ctx, "AP"),
                               _min_space_um(ctx, "AP")) == 0


def test_d2_ind_sym_nt1_seam_clean_each_metal_after_phase05(tmp_path):
    """The direct NT=1 ring is clean on each numeric body metal."""
    ctx = port.process_rule_context("n28_1p10m")
    m10 = port.ind_sym(OD=120.0, W=5.0, OPENING=8.0, LEAD=20.0, S=2.5, NT=1,
                       TOP_ME="10", BTM_ME="9", process=ctx)
    assert _min_space_findings(m10, tmp_path, "d2_m10_nt1",
                               _drawing_layer(ctx, "M10"),
                               _min_space_um(ctx, "M10")) == 0
    m9 = port.ind_sym(OD=120.0, W=5.0, OPENING=8.0, LEAD=20.0, S=2.5, NT=1,
                      TOP_ME="9", BTM_ME="8", process=ctx)
    assert _min_space_findings(m9, tmp_path, "d2_m9_nt1",
                               _drawing_layer(ctx, "M9"),
                               _min_space_um(ctx, "M9")) == 0


def test_ind_sym_nt1_ap_direct_ring_is_clean(tmp_path):
    """NT=1 has no crossover, so the direct AP ring clears AP spacing."""
    ctx = port.process_rule_context("n28_1p10m")
    ap = port.ind_sym(OD=120.0, W=5.0, OPENING=8.0, LEAD=20.0, S=2.5, NT=1,
                      TOP_ME="AP", BTM_ME="10", process=ctx)
    n = _min_space_findings(ap, tmp_path, "d2_ap_nt1",
                            _drawing_layer(ctx, "AP"), _min_space_um(ctx, "AP"))
    assert n == 0


def test_d3_ind_sym_small_od_builds_clean_after_corridor_trim(tmp_path):
    """D3 flip (design-region issue 04): a small-OD NT=3 coil used to fail
    the winding-segment count because the UNTRIMMED crossover endpoint pad
    bridged two turns (a script artifact, not physics).  With the
    chamfer-corridor pad trim the point builds, stays topologically
    correct (3 segments -- the builder's own guard passes), and is
    min_space-clean on the coil metal. OD=70 rather than the original 60:
    at 60 the inner ring cannot host the crossunder facing opening and the
    pads hang 50% off the ring, which _check_winding_fit now rejects
    (gdsfactory review 2026-09-21)."""
    ctx = port.process_rule_context("n28_1p10m")
    for top, btm, cond in (("10", "9", "M10"), ("9", "8", "M9")):
        cell = port.ind_sym(OD=70.0, W=5.0, OPENING=8.0, LEAD=20.0, S=2.5,
                            NT=3, TOP_ME=top, BTM_ME=btm, process=ctx)
        n = _min_space_findings(cell, tmp_path, f"d3_trim_{cond}",
                                _drawing_layer(ctx, cond),
                                _min_space_um(ctx, cond))
        assert n == 0


def test_cross_endpoint_offset_default_met_under_process_is_reference():
    """Gate A' P2-1: cross_endpoint_offset(W, S, process=ctx) WITHOUT met must
    honor its documented default -- 'with the default met the clearance is the
    reference literal' -- instead of crashing (pre-fix: met=None was forwarded
    into _junction_clearance_const -> _metal_name(None) TypeError). The
    default-met process-mode value therefore equals the pure reference value;
    an explicit met on a min_space<=2.0 conductor (M9) also equals it (the
    literal dominates); only an explicit AP met widens."""
    ctx = port.process_rule_context("n28_1p10m")
    ref = port.cross_endpoint_offset(5.0, 2.5)
    assert port.cross_endpoint_offset(5.0, 2.5, process=ctx) == ref
    assert port.cross_endpoint_offset(5.0, 2.5, 9, ctx) == ref
    assert port.cross_endpoint_offset(5.0, 2.5, 11, ctx) > ref


def test_seam_heal_is_a_no_op_in_reference_mode(tmp_path):
    """Reference mode (process=None) is never healed: an NT=1 reference coil is
    byte-identical whether or not the heal code path exists (the heal early-
    returns on process is None). Asserted by structural equality of the
    flattened geometry to a direct base-cell build."""
    ref = port.ind_sym(OD=120.0, W=5.0, OPENING=8.0, LEAD=20.0, S=2.5, NT=1,
                       TOP_ME="9", BTM_ME="8")
    # no process context -> the winding is drawn exactly as the reference port
    # would (the heal adds nothing); the coil still builds and has its ports.
    assert [p["logical_name"] for p in ref.emx_ports] == ["P1", "N1"]
    layers = write_and_parse(ref, tmp_path, "ref_nt1")
    assert (31, 0) not in layers  # M1 never drawn without a ground fixture


# --- byte-invariance: numeric-metal multi-turn coils (NT>=2, OD>=100) --------
#
# Canonical-geometry digest = sha256 over the KLayout-parsed, sorted,
# flattened polygons of a process-mode build (NOT GDS file bytes, which carry
# nondeterministic timestamps). The baseline constants below were captured
# from the pre-Phase-0.5 pcell (git HEAD d574ab4) BEFORE the D1 clearance and
# D2/D3 seam heal landed. Every config here is a numeric-metal coil at
# min_space 1.0 (M9/M10) with NT>=2 and OD>=100, where the rule does NOT
# require any change: D1's clearance evaluates back to the 2.01 literal and
# the seam heal qualifies no pair -> geometry must be byte-identical.  The
# sole NT=2 entry intentionally pins the newer rule-derived three-layer
# compact bridge with its maximum DRC-clean via landing; NT>=3 entries retain
# the pre-Phase-0.5 digest unchanged.

_BYTE_INVARIANCE_BASELINE = {
    # key: (OD, W, OPENING, LEAD, S, NT, TOP_ME, BTM_ME)  ->  digest
    (120.0, 5.0, 8.0, 20.0, 2.5, 3, "9", "8"):
        "a6d69cb0f466fd1b09d2d82d64227d32c916cef7f15ed0a972199539f2fecb4a",
    (120.0, 5.0, 8.0, 20.0, 2.0, 3, "10", "9"):
        "75d5fa2299e001802c66b0b7908d9cdbc3c12447c182b4dd9753ca8bb0370d9c",
    (180.0, 4.0, 8.0, 20.0, 3.0, 4, "9", "8"):
        "74730c2951c65d7020270ef7409a8c683c8c13508c3cc47b645463d57345947b",
    # NT=2 digest re-pinned 2026-07-31 (reference bridge scheme,
    # design-region issue 03): the compact winding's leg2 moved from
    # top-2 to the coil's own layer -- a deliberate, user-directed
    # geometry change covered by the geom_version 3 boundary.
    (100.0, 6.0, 10.0, 15.0, 2.0, 2, "10", "9"):
        "50882ae52c6e68ef3e012d5903f4186ffd9dbdd88f0fce1a295996400ba6b9e5",
    (140.0, 5.0, 8.0, 20.0, 2.5, 5, "9", "8"):
        "81406a787aa0953b159850dd53ce194c8792baa0b3d538e0aef28619c8eb7da0",
}


def _canonical_geometry_digest(cell, tmp_path, name):
    import hashlib

    gds = tmp_path / f"{name}.gds"
    port.write_gds(cell, gds)
    ly = kdb.Layout()
    ly.read(str(gds))
    top = ly.top_cells()[0]
    top.flatten(True)
    items = []
    for li in ly.layer_indexes():
        info = ly.get_info(li)
        for s in top.shapes(li).each():
            if s.is_text():
                continue
            poly = s.polygon
            hull = tuple((p.x, p.y) for p in poly.each_point_hull())
            holes = tuple(
                tuple((p.x, p.y) for p in poly.each_point_hole(h))
                for h in range(poly.holes()))
            items.append((info.layer, info.datatype, hull, holes))
    items.sort()
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()


@pytest.mark.parametrize("cfg,expected", list(_BYTE_INVARIANCE_BASELINE.items()))
def test_phase05_byte_invariant_for_numeric_metals(cfg, expected, tmp_path):
    """Pin the resolved process geometry for numeric-metal coils.

    NT>=3 remains byte-identical to the pre-Phase-0.5 port.  NT=2 is the
    explicit compact-bridge exception: it now uses two lower planes and its
    new digest prevents that topology from silently drifting.
    """
    OD, W, OPENING, LEAD, S, NT, TOP_ME, BTM_ME = cfg
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.ind_sym(OD=OD, W=W, OPENING=OPENING, LEAD=LEAD, S=S, NT=NT,
                        TOP_ME=TOP_ME, BTM_ME=BTM_ME, process=ctx)
    got = _canonical_geometry_digest(cell, tmp_path, f"binv_{TOP_ME}_{OD}_{NT}")
    assert got == expected


# ---------------------------------------------------------------------------
# generated outputs: GDS + PNG + coordinates JSON + report
# ---------------------------------------------------------------------------

REQUIRED_BASENAMES = [
    "base_ind_diag",
    "base_xfm_cross",
    "base_ind_hud_cross",
    "base_oct_quad",
    "base_oct_half",
    "base_oct",
    "base_lead",
    "base_lead_pair",
    "ind_sym_3t",
    "ind_sym_ct_3t",
    "ind_sym_3t_n28",
    "ind_sym_ct_3t_m10m9_ct_m8_n28",
    "ind_sym_ct_3t_m10m9_ct_m8_n28_gnd",
    "base_balun_sec",
    "base_balun_sec_n28",
    "xfm_bs",
    "xfm_bs_n28",
    "xfm_bs_ct",
    "xfm_bs_ct_n28",
    "xfm_ms",
    "xfm_ms_spaced",
    "xfm_ms_n28",
    "xfm_bs_m10ap",
    "xfm_balun",
    "xfm_balun_n28",
    "xfm_balun_2t1t_n28",
    "ind_sym_ct_3t_m7_n28",
]


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    out = tmp_path_factory.mktemp("outputs")
    port.generate_all(out)
    return out


def test_generate_all_produces_required_files(generated):
    for base in REQUIRED_BASENAMES:
        for ext in (".gds", ".png", ".coordinates.json"):
            f = generated / f"{base}{ext}"
            assert f.is_file(), f"missing {f.name}"
            assert f.stat().st_size > 0
    assert (generated / "pcell_inductor_python_port_report.md").is_file()
    assert (generated / "pcell_inductor_python_port_report.json").is_file()


def test_coordinates_json_matches_gds(generated):
    """Every coordinates JSON reproduces the GDS polygons exactly."""
    for base in REQUIRED_BASENAMES:
        doc = json.loads((generated / f"{base}.coordinates.json").read_text())
        assert doc["dbu_um"] == pytest.approx(0.001)
        json_polys = {
            (poly["gds_layer"], poly["gds_datatype"], canon(map(tuple, poly["points_nm"])))
            for poly in doc["polygons"]
        }
        gds_polys = {
            (layer[0], layer[1], canon(p))
            for layer, polys in parse_gds(generated / f"{base}.gds").items()
            for p in polys
        }
        assert json_polys == gds_polys, f"{base}: JSON/GDS polygon mismatch"


def test_output_json_marks_reference_and_process_modes(generated):
    """Reference JSON: reference_mode true, no N28 DRC claim. N28 JSON:
    process profile + rule source recorded. No n28_center_tap_status field
    any more (n28-rules-slim, user directive 2026-07-19): that field
    recorded a standing "expected failure" for a lower-via CT probe that no
    longer fails, since generation is geometric-only now."""
    ref = json.loads((generated / "ind_sym_3t.coordinates.json").read_text())
    assert ref["reference_mode"] is True
    assert ref["process_profile"] is None
    assert ref["via_rule_source"] == "ind_ref_reconstructed"
    assert ref["n28_drc_proof"] is False

    n28 = json.loads((generated / "ind_sym_3t_n28.coordinates.json").read_text())
    assert n28["reference_mode"] is False
    assert n28["process_profile"] == "n28_1p10m"
    assert n28["via_rule_source"] == "process_rule_profile"
    assert "n28_center_tap_status" not in n28
    layer_pairs = {(p["gds_layer"], p["gds_datatype"]) for p in n28["polygons"]}
    assert layer_pairs == {(38, 20), (39, 80), (58, 80)}


def test_n28_m7_ct_demo_builds_with_via7_and_via8(generated):
    """n28-rules-slim (user directive 2026-07-19): the M9-body M7-CT ind_sym
    demo (formerly recorded as an "expected failure" citing the retired
    via_restrictions/IND.R.1 policy gate) now builds as a normal DEMOS
    output, VIA7 (M7<->M8) and VIA8 (M8<->M9) cuts both present in its tap
    stack. No more .expected_failure.json artifact is produced."""
    doc = json.loads(
        (generated / "ind_sym_ct_3t_m7_n28.coordinates.json").read_text()
    )
    layer_pairs = {(p["gds_layer"], p["gds_datatype"]) for p in doc["polygons"]}
    assert (37, 20) in layer_pairs  # M7 CT lead + tap metal
    assert (57, 20) in layer_pairs  # VIA7 tap cuts
    assert (58, 80) in layer_pairs  # VIA8 tap cuts
    assert not (generated / "ind_sym_ct_3t_n28.expected_failure.json").exists()


def test_readme_states_reference_mode_is_not_n28_drc_proof():
    readme = " ".join((MOD_PATH.parent / "README.md").read_text().split())
    assert "not N28 DRC proof" in readme, (
        "README must state that reference mode is not N28 DRC proof"
    )


def test_report_maps_python_functions_to_pcell_sources(generated):
    report = json.loads(
        (generated / "pcell_inductor_python_port_report.json").read_text()
    )
    mapping = {e["python_function"]: e for e in report["function_mapping"]}
    expected_sources = {
        "base_ind_diag": "gdsgen_ref/pcell/inductor/base_ind_diag.il",
        "base_xfm_cross": "gdsgen_ref/pcell/transformer/base_xfm_cross.il",
        "base_ind_hud_cross": "gdsgen_ref/pcell/inductor/base_ind_hud_cross.il",
        "base_oct_quad": "gdsgen_ref/pcell/common/base_oct_quad.il",
        "base_oct_half": "gdsgen_ref/pcell/common/base_oct_half.il",
        "base_oct": "gdsgen_ref/pcell/common/base_oct.il",
        "base_lead": "gdsgen_ref/pcell/common/base_lead.il",
        "base_lead_pair": "gdsgen_ref/pcell/common/base_lead_pair.il",
        "ind_sym": "gdsgen_ref/pcell/inductor/ind_sym.il",
        "ind_sym (CT_ME)": "gdsgen_ref/pcell/inductor/ind_sym_ct.il",
    }
    for fn, source in expected_sources.items():
        assert fn in mapping, f"report misses {fn}"
        assert mapping[fn]["pcell_source"] == source
        assert mapping[fn]["parameters"], f"report misses parameters of {fn}"
    assert mapping["vias"]["pcell_source"].startswith("(library PCell")
    md = (generated / "pcell_inductor_python_port_report.md").read_text()
    assert "GPL" in md
    assert "must not be merged" in md.lower()
    assert report["known_deviations"], "deviations must be listed (fail closed)"


# ---------------------------------------------------------------------------
# generic metals: the coil follows TOP_ME, the CT metal is free
# ---------------------------------------------------------------------------


def test_hud_cross_top_me_10_builds_m10_ring_with_via9(tmp_path):
    """TOP_ME="10": ring/same-layer cross on M10, underpass on M9, VIA9
    endpoint arrays -- no hardcoded MET 9 anywhere."""
    cell = port.base_ind_hud_cross(
        OD=60.0, W=2.0, S=2.0, OPENING=10.0, TOP_ME="10", BTM_ME="9", under=True
    )
    layers = write_and_parse(cell, tmp_path, "hud_cross_m10")
    m10, m9 = port.metal_layer(10), port.metal_layer(9)
    ring = [
        p
        for p in layers[m10]
        if len(p) == 8 and rect_bbox(p)[2] - rect_bbox(p)[0] > 10000
    ]
    assert len(ring) == 4
    assert len([p for p in layers[m9] if len(p) == 8]) == 1  # underpass diag
    assert port.via_layer(9) in layers
    assert port.via_layer(8) not in layers
    assert_on_grid(layers)


def test_ind_sym_top_me_parameter_propagates_to_children():
    log = port.ind_sym(
        OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0, NT=3, TOP_ME="10", BTM_ME="9"
    ).instantiation_log()
    huds = [e for e in log if e["function"] == "base_ind_hud_cross"]
    assert huds and all(e["params"]["TOP_ME"] == "10" for e in huds)
    oct_inner = next(e for e in log if e["function"] == "base_oct")
    assert oct_inner["params"]["MET"] == 10
    leads = next(e for e in log if e["function"] == "base_lead_pair")
    assert leads["params"]["TOP_ME"] == "10"


def test_ind_sym_ct_reference_mode_ct_on_m5(tmp_path):
    """Reference mode: an M9/M8 coil with an M5 CT builds a via5..via8 tap
    stack and an M5 CT lead. The via5..via7 levels exist only at the tap
    column (via8 additionally fills the crossover endpoint blocks, so it
    is not asserted tap-confined)."""
    cell = port.ind_sym(
        OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0, NT=3, TOP_ME="9", CT_ME="5"
    )
    layers = write_and_parse(cell, tmp_path, "ct_m5")
    assert bbox_of(layers[port.metal_layer(5)]) == (-40000, -1000, -20000, 1000)
    tap_box = (-22000, -1000, -20000, 1000)
    for level in (5, 6, 7):
        cuts = [rect_bbox(p) for p in layers[port.via_layer(level)]]
        assert cuts, f"via{level} cuts missing from the tap stack"
        for x1, y1, x2, y2 in cuts:
            assert x1 >= tap_box[0] and y1 >= tap_box[1]
            assert x2 <= tap_box[2] and y2 <= tap_box[3]
    assert_on_grid(layers)






# ---------------------------------------------------------------------------
# M7L: EMX port labels (pin-layer text labels + per-cell .emx_ports artifact)
#
# klayout.db quirk: a text shape's string is Shape.text_string and its
# position is Shape.text_trans.disp; Shape.text returns a Text object, not a
# string.
# ---------------------------------------------------------------------------


def test_cell_label_round_trips_on_layer_and_grid(tmp_path):
    """add_label lands a kdb.Text on the pin layer, grid-snapped, after GDS I/O."""
    cell = port.Cell("label_probe", "probe", {})
    cell.add_rect(port.metal_layer(9), 0.0, 0.0, 4.0, 2.0)
    cell.add_label(port.metal_pin_layer(9), "P1", 4.0, 1.0)
    gds = tmp_path / "label_probe.gds"
    port.write_gds(cell, gds)
    ly = kdb.Layout()
    ly.read(str(gds))
    top = ly.top_cell()
    li = ly.layer(139, 0)
    texts = [t.text_string for t in top.shapes(li).each() if t.is_text()]
    assert texts == ["P1"]
    for t in top.shapes(li).each():
        if t.is_text():
            assert t.text_trans.disp.x % 5 == 0 and t.text_trans.disp.y % 5 == 0


def test_process_pin_layer_from_rule_profile():
    ctx = port.process_rule_context("n28_1p10m")
    assert port.process_pin_layer(ctx, 8) == (138, 0)
    assert port.process_pin_layer(ctx, 9) == (139, 0)
    assert port.process_pin_layer(ctx, 10) == (140, 0)


def test_pin_layer_dispatch_reference_vs_process():
    ctx = port.process_rule_context("n28_1p10m")
    assert port._pin(9, None) == (139, 0)
    assert port._pin(9, ctx) == (139, 0)
    assert port._pin(8, None) == (138, 0)


def test_process_pin_layer_fails_closed_when_pin_missing():
    """A metal whose rule profile leaves pin unset cannot host a port label."""
    class _PinlessLayer:
        pin = None

    class _StubAdapter:
        def layer(self, metal):
            return _PinlessLayer()

    ctx = port.ProcessRuleContext(profile_id="synthetic", adapter=_StubAdapter())
    with pytest.raises(port.PortError, match="no pin layer"):
        port.process_pin_layer(ctx, 9)


def write_and_parse_labels(cell, tmp_path, name):
    """Write a cell to GDS and parse back only its text labels.

    Returns {(layer, datatype): {text: (x_nm, y_nm)}} of flattened labels.
    """
    gds = tmp_path / f"{name}.gds"
    port.write_gds(cell, gds)
    ly = kdb.Layout()
    ly.read(str(gds))
    top = ly.top_cell()
    top.flatten(True)
    out = {}
    for li in ly.layer_indexes():
        info = ly.get_info(li)
        texts = {
            t.text_string: (t.text_trans.disp.x, t.text_trans.disp.y)
            for t in top.shapes(li).each()
            if t.is_text()
        }
        if texts:
            out[(info.layer, info.datatype)] = texts
    return out


def test_ind_sym_emits_p1_n1_port_labels(tmp_path):
    """P1/N1 land on the M(TOP_ME) pin layer at the right lead tip midpoints
    (semantic default names since M13 ticket 02)."""
    cell = port.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0, NT=3)
    assert [p["logical_name"] for p in cell.emx_ports] == ["P1", "N1"]
    layers = write_and_parse_labels(cell, tmp_path, "ind_sym_ports")
    assert layers[(139, 0)] == {"P1": (40000, 6000), "N1": (40000, -6000)}


def test_ind_sym_n28_uses_rule_pin_layer(tmp_path):
    """Process mode resolves the pin layer from the rule profile (M10 -> 140,0)."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.ind_sym(
        OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0, NT=3,
        TOP_ME="10", BTM_ME="9", process=ctx,
    )
    layers = write_and_parse_labels(cell, tmp_path, "ind_sym_ports_n28")
    assert set(layers[(140, 0)]) == {"P1", "N1"}


def test_ind_sym_ct_odd_nt_ct_label_left_tip(tmp_path):
    """Odd NT: CT exits on the crossover (left) side at -OD/2-LEAD on the
    BTM_ME pin layer."""
    cell = port.ind_sym(
        OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0,
        NT=3, TOP_ME="9", CT_ME="7",
    )
    assert [p["logical_name"] for p in cell.emx_ports] == ["P1", "N1", "CT"]
    layers = write_and_parse_labels(cell, tmp_path, "ct_odd_ports")
    assert layers[(137, 0)] == {"CT": (-40000, 0)}
    assert set(layers[(139, 0)]) == {"P1", "N1"}


def test_ind_sym_ct_even_nt_ct_label_right_tip(tmp_path):
    """Even NT: CT exits on the lead (right) side at +OD/2+LEAD.

    BTM_ME is M7 (not M8): the adjacency guard forbids top-1 CT metals
    (bug review 2026-07-17 N1), so the label lands on the M7 pin layer."""
    cell = port.ind_sym(
        OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0,
        NT=2, TOP_ME="9", CT_ME="7",
    )
    layers = write_and_parse_labels(cell, tmp_path, "ct_even_ports")
    assert layers[(137, 0)] == {"CT": (40000, 0)}


def test_ind_sym_ct_m10m9_m8ct_n28_ports(tmp_path):
    """N28 M10/M9 coil + M8 CT: P1/N1 on the M10 pin layer, CT on the M8 pin
    layer at the even-NT right tip."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.ind_sym(
        OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0,
        NT=4, TOP_ME="10", CT_ME="8", process=ctx,
    )
    layers = write_and_parse_labels(cell, tmp_path, "ct_m10_ports")
    assert set(layers[(140, 0)]) == {"P1", "N1"}
    assert layers[(138, 0)] == {"CT": (40000, 0)}


def test_emx_port_lines_parse_with_product_parser():
    """emx_port_lines yields sorted '-p name=signal' lines consumable by the
    product M2 parser, with reference None throughout."""
    from ic_opt.em.pcell.base import parse_emx_port_line as _parse_emx_port_line

    cell = port.ind_sym(
        OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0,
        NT=2, TOP_ME="9", CT_ME="7",
    )
    lines = port.emx_port_lines(cell.emx_ports)
    assert lines == ["-p CT=CT", "-p N1=N1", "-p P1=P1"]
    parsed = [_parse_emx_port_line(ln) for ln in lines]
    assert {p.name for p in parsed} == {"CT", "N1", "P1"}
    assert all(p.reference is None for p in parsed)


def test_generate_all_writes_emx_ports_and_signals_in_labels(generated):
    for base in ("ind_sym_3t", "ind_sym_ct_3t", "ind_sym_3t_n28"):
        lines = (generated / f"{base}.emx_ports").read_text().splitlines()
        assert lines and all(ln.startswith("-p ") for ln in lines)
        doc = json.loads((generated / f"{base}.coordinates.json").read_text())
        signals = {p["signal"] for p in doc["emx_ports"]}
        labels = {layer["text"] for layer in doc["labels"]}
        assert signals <= labels, f"{base}: port signal missing from GDS labels"


def test_drawing_layers_unchanged_by_port_labels(tmp_path):
    """Port labels live on pin layers; no drawing layer may carry a text and
    the drawing-layer polygon set must be untouched by the port feature."""
    cell = port.ind_sym(
        OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0,
        NT=3, TOP_ME="9", CT_ME="7",
    )
    layers = write_and_parse(cell, tmp_path, "ct_drawing")
    drawing = {
        k for k in layers
        if k[0] in (37, 38, 39, 40) and k[1] in (0, 20, 80)
    }
    pin = {k for k in layers if k[0] in (137, 138, 139, 140)}
    assert drawing and not pin


# ---------------------------------------------------------------------------
# M13 ticket 02: unified ind_sym with optional CT_ME (semantic ports)
# ---------------------------------------------------------------------------


def _sorted_layers(layers):
    return {k: sorted(v) for k, v in layers.items()}





def test_ind_sym_ct_me_adjacency_guard_uniform_across_nt():
    """CT_ME at TOP_ME-1 shorts to the winding crossunder: fail closed at
    every NT with the unified device name (same rule as ind_sym_ct N1)."""
    for nt in (2, 3, 4, 5):
        with pytest.raises(port.PortError, match="two levels below"):
            port.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0,
                         NT=nt, TOP_ME="9", CT_ME="8")
    with pytest.raises(port.PortError, match="ind_sym: CT metal"):
        port.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0,
                     NT=2, TOP_ME="9", CT_ME="8")


def test_ind_sym_ct_me_ignores_dead_bottom_metal():
    """BTM_ME is a documented dead parameter of the winding chain (the
    crossunder is always drawn at TOP_ME-1), so the N28-blessed M10/M9 coil
    + M8 CT must build even when the caller passes the dead BTM_ME="8" —
    the guard compares CT_ME against the real crossunder (TOP_ME-1), never
    against BTM_ME."""
    cell = port.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0,
                        NT=4, TOP_ME="10", BTM_ME="8", CT_ME="8")
    assert [p["name"] for p in cell.emx_ports] == ["P1", "N1", "CT"]
    assert cell.emx_ports[2]["metal"] == "M8"


def test_ind_sym_ct_me_requires_three_port_names():
    with pytest.raises(port.PortError, match="port_order"):
        port.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0,
                     NT=2, TOP_ME="9", CT_ME="7", port_order=["P1", "N1"])


def test_ind_sym_no_ct_stays_two_port_no_low_metal(tmp_path):
    """CT_ME=None (default) keeps exactly two ports and never draws the tap
    metal below the crossunder."""
    cell = port.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0, NT=2)
    assert [p["name"] for p in cell.emx_ports] == ["P1", "N1"]
    layers = write_and_parse(cell, tmp_path, "no_ct")
    assert (37, 0) not in layers


def test_ind_sym_ct_me_ground_fixture_reference_chain():
    """Fixture G-refs follow the port order: P1->G01, N1->G02, CT->G03."""
    cell = port.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0,
                        NT=2, TOP_ME="9", CT_ME="7", ground_fixture=_fixture())
    lines = port.emx_port_lines(cell.emx_ports)
    assert lines == ["-p CT=CT:G03", "-p N1=N1:G02", "-p P1=P1:G01"]


# ---------------------------------------------------------------------------
# M7M/M13: configurable port names (semantic defaults) + reference-aware
# EMX lines
# ---------------------------------------------------------------------------


def test_ind_sym_default_port_names_are_semantic(tmp_path):
    """ind_sym default port names equal the physical roles (P1/N1) since M13
    ticket 02; the pin-layer label text is the port name (still overridable
    via port_order)."""
    cell = port.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0, NT=3)
    assert [(p["name"], p["logical_name"]) for p in cell.emx_ports] == [
        ("P1", "P1"), ("N1", "N1")]
    layers = write_and_parse_labels(cell, tmp_path, "sym_names")
    assert set(layers[(139, 0)]) == {"P1", "N1"}


def test_ind_sym_ct_port_order_override():
    """port_order is configurable; logical_name roles are preserved."""
    cell = port.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0,
                           NT=3, TOP_ME="9", CT_ME="7",
                           port_order=["a1", "a2", "a3"])
    assert [p["name"] for p in cell.emx_ports] == ["a1", "a2", "a3"]
    assert [p["logical_name"] for p in cell.emx_ports] == ["P1", "N1", "CT"]


def test_emx_port_lines_reference_aware():
    """reference set -> '-p name=signal:reference'; None -> '-p name=signal'."""
    ports = [
        {"name": "p01", "signal": "p01", "reference": "G01"},
        {"name": "p02", "signal": "p02", "reference": None},
    ]
    assert port.emx_port_lines(ports) == ["-p p01=p01:G01", "-p p02=p02"]


# ---------------------------------------------------------------------------
# M7M: M1 ground reference fixture (ring + per-port chamfered stub + G0n pin)
# ---------------------------------------------------------------------------


def _fixture():
    return port.GroundFixtureConfig(
        inner_margin_um=5.0, ring_width_um=3.0,
        stub_width_um=4.0, stub_length_um=3.0, stub_chamfer_um=1.0,
    )


def test_ground_fixture_ring_and_stubs_and_g_pins(tmp_path):
    """Even NT + fixture: M1 ring + 3 stubs; G01/G02/G03 on the M1 pin layer
    at the port (x,y); each port's reference is wired to its G name."""
    cell = port.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0,
                           NT=2, TOP_ME="9", CT_ME="7",
                           ground_fixture=_fixture())
    refs = {p["logical_name"]: p["reference"] for p in cell.emx_ports}
    assert refs == {"P1": "G01", "N1": "G02", "CT": "G03"}
    labels = write_and_parse_labels(cell, tmp_path, "gnd_even")
    g = labels[(131, 0)]
    assert g["G01"] == (40000, 6000)
    assert g["G02"] == (40000, -6000)
    assert g["G03"] == (40000, 0)
    polys = write_and_parse(cell, tmp_path, "gnd_even")
    m1 = polys[(31, 0)]
    assert len(m1) >= 4  # four ring rectangles + one chamfered stub per port
    assert_on_grid(polys)


def test_ground_fixture_odd_nt_ct_stub_on_left(tmp_path):
    """Odd NT: the CT port sits at x<0, so its G03 stub roots on the left
    ring and the G03 label lands at (-OD/2-LEAD, 0)."""
    cell = port.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0,
                           NT=3, TOP_ME="9", CT_ME="7",
                           ground_fixture=_fixture())
    labels = write_and_parse_labels(cell, tmp_path, "gnd_odd")
    assert labels[(131, 0)]["G03"] == (-40000, 0)


def test_ground_fixture_port_side_uses_stub_length_not_body_margin(tmp_path):
    """A port-bearing side must root the ring exactly stub_length_um past the
    port tip -- never the (larger) body inner_margin_um. _fixture() sets
    inner_margin_um=5.0 > stub_length_um=3.0, so this pins the two apart:
    if the ring incorrectly used inner_margin_um on the port side, the
    measured gap would be 5.0um, not 3.0um."""
    cell = port.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0,
                           NT=2, TOP_ME="9", CT_ME="7",
                           ground_fixture=_fixture())
    port_x = 30.0 + 10.0  # OD/2 + LEAD, matches the P1/N1 lead tip (x=40)
    polys = write_and_parse(cell, tmp_path, "gnd_stub_length")
    m1 = polys[(31, 0)]
    # the right-side ring segment: a thin vertical sliver at large +x,
    # width == ring_width_um (3.0um = 3000nm), unlike the stub (4000nm wide).
    ring_width_nm = 3000
    right_ring = next(
        poly for poly in m1
        if max(x for x, y in poly) - min(x for x, y in poly) == ring_width_nm
        and min(x for x, y in poly) > port_x * 1000
    )
    inner_xmax_nm = min(x for x, y in right_ring)
    gap_um = (inner_xmax_nm / 1000.0) - port_x
    assert abs(gap_um - 3.0) < 1e-9, (
        f"port-side ring gap must equal stub_length_um=3.0, got {gap_um}"
    )


def _stub_poly_at(m1_polys, x_nm, y_nm):
    """The M1 stub polygon whose bbox contains a port tip point (nm).

    The stub spans ring-inner-edge -> port tip, so the tip lies on the stub's
    inner-end edge; the ring segments start stub_length_um farther out and
    never contain the tip. Exactly one hit is a correctness assertion."""
    hits = [
        poly for poly in m1_polys
        if min(px for px, _ in poly) <= x_nm <= max(px for px, _ in poly)
        and min(py for _, py in poly) <= y_nm <= max(py for _, py in poly)
    ]
    assert len(hits) == 1, f"expected exactly 1 stub at ({x_nm},{y_nm}), got {len(hits)}"
    return hits[0]


def test_ground_fixture_top_bottom_ports_use_vertical_stubs_outside_body(tmp_path):
    """xfm_tw ports leave through the top/bottom edges, so its M1 fixture
    must surround the body and meet those ports with vertical stubs.  The
    old left/right-only fixture put both vertical ring rails through the
    winding projection and drew horizontal stubs instead."""
    cell = port.xfm_tw(
        OD=200.0,
        W=4.0,
        S=2.0,
        NR=3,
        OPENING_P=8.0,
        OPENING_N=8.0,
        LEAD=20.0,
        SL_ME="9",
        ground_fixture=_fixture(),
    )
    polys = write_and_parse(cell, tmp_path, "gnd_tw_vertical")
    m1 = polys[port.metal_layer(1)]
    body_points = [
        point
        for layer, layer_polys in polys.items()
        if layer != port.metal_layer(1)
        for polygon in layer_polys
        for point in polygon
    ]
    body_xmin = min(x for x, _y in body_points)
    body_xmax = max(x for x, _y in body_points)
    body_height = max(y for _x, y in body_points) - min(y for _x, y in body_points)
    vertical_ring_rails = [
        polygon
        for polygon in m1
        if max(y for _x, y in polygon) - min(y for _x, y in polygon) > body_height
    ]
    assert len(vertical_ring_rails) == 2
    left, right = sorted(vertical_ring_rails, key=lambda polygon: min(x for x, _y in polygon))
    assert max(x for x, _y in left) < body_xmin
    assert min(x for x, _y in right) > body_xmax

    for emx_port in cell.emx_ports:
        x_um, y_um = emx_port["label_xy_um"]
        stub = _stub_poly_at(m1, round(x_um * 1000), round(y_um * 1000))
        stub_width = max(x for x, _y in stub) - min(x for x, _y in stub)
        stub_length = max(y for _x, y in stub) - min(y for _x, y in stub)
        assert stub_width == 6000  # 4 um port width + 1 um chamfer per side
        assert stub_length == 3000


def test_ground_fixture_per_port_stub_width(tmp_path):
    """xfm_ms motivating case: single side (W_S=6) keeps the global 6.0 um
    stubs via fallback; multi side (W_M=3) gets mapped 3.0 um stubs. Widths
    are measured on the actual M1 polygons (bbox height of each port's stub),
    continuing the 03eb764 geometric-assertion methodology."""
    gfc = port.GroundFixtureConfig(
        inner_margin_um=5.0, ring_width_um=4.0, stub_width_um=6.0,
        stub_length_um=2.0, stub_chamfer_um=0.0,
        stub_width_by_port_um={"P2": 3.0, "N2": 3.0},
    )
    cell = _ms(ground_fixture=gfc)
    tips = {p["name"]: p["label_xy_um"] for p in cell.emx_ports}
    polys = write_and_parse(cell, tmp_path, "gnd_perport")
    m1 = polys[(31, 0)]
    expected_w_nm = {"P1": 6000, "N1": 6000, "P2": 3000, "N2": 3000}
    for name, w_nm in expected_w_nm.items():
        x_um, y_um = tips[name]
        stub = _stub_poly_at(m1, round(x_um * 1000), round(y_um * 1000))
        height = max(py for _, py in stub) - min(py for _, py in stub)
        assert height == w_nm, f"{name}: stub width {height} nm, expected {w_nm}"
    assert_on_grid(polys)


def test_ground_fixture_per_port_unknown_key_fails_closed():
    """A typo'd port name must raise PortError naming the key -- never be
    silently ignored (standing fail-closed discipline)."""
    gfc = port.GroundFixtureConfig(
        inner_margin_um=5.0, ring_width_um=4.0, stub_width_um=6.0,
        stub_length_um=2.0, stub_chamfer_um=0.0,
        stub_width_by_port_um={"PP2": 3.0},
    )
    with pytest.raises(port.PortError, match="PP2"):
        _ms(ground_fixture=gfc)


def test_ground_fixture_per_port_default_is_behavior_preserving(tmp_path):
    """Omitting the new field must reproduce today's geometry exactly; an
    empty map is falsy and behaves identically (global width everywhere)."""
    def build(extra):
        gfc = port.GroundFixtureConfig(
            inner_margin_um=5.0, ring_width_um=3.0, stub_width_um=4.0,
            stub_length_um=3.0, stub_chamfer_um=1.0, **extra)
        return port.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0,
                               NT=2, TOP_ME="9", CT_ME="7",
                               ground_fixture=gfc)
    a = write_and_parse(build({}), tmp_path, "gnd_default_a")
    b = write_and_parse(build({"stub_width_by_port_um": None}), tmp_path,
                        "gnd_default_b")
    c = write_and_parse(build({"stub_width_by_port_um": {}}), tmp_path,
                        "gnd_default_c")
    ca = {lay: sorted(canon(pl) for pl in a[lay]) for lay in a}
    assert ca == {lay: sorted(canon(pl) for pl in b[lay]) for lay in b}
    assert ca == {lay: sorted(canon(pl) for pl in c[lay]) for lay in c}


def test_emx_lines_grounded_parse_with_product_parser():
    """Grounded ports emit '-p p0n=p0n:G0n'; the product parser recovers a
    non-None reference for every grounded port."""
    from ic_opt.em.pcell.base import parse_emx_port_line as _parse_emx_port_line

    cell = port.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0,
                           NT=2, TOP_ME="9", CT_ME="7",
                           ground_fixture=_fixture())
    lines = port.emx_port_lines(cell.emx_ports)
    parsed = {p.name: p for p in (_parse_emx_port_line(ln) for ln in lines)}
    assert parsed["P1"].reference == "G01"
    assert parsed["CT"].reference == "G03"
    assert all(p.reference is not None for p in parsed.values())


def test_ground_fixture_fails_closed_without_ports():
    """A cell with no emx_ports (e.g. base_oct) cannot receive a fixture."""
    cell = port.base_oct(OD=60.0, W=2.0, LOP=0.0, ROP=4.0, MET=9)
    with pytest.raises(port.PortError):
        port.add_ground_fixture(cell, _fixture())


def test_ground_fixture_fails_closed_when_m1_has_no_pin():
    """A process profile whose M1 carries no pin layer cannot host a G label."""
    class _PinlessM1Layer:
        drawing = (31, 0)
        pin = None

    class _StubAdapter:
        def layer(self, metal):
            if metal == "M1":
                return _PinlessM1Layer()
            raise ValueError(metal)

    cell = port.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0, NT=3)
    ctx = port.ProcessRuleContext(profile_id="synthetic", adapter=_StubAdapter())
    with pytest.raises(port.PortError, match="no pin layer"):
        port.add_ground_fixture(cell, _fixture(), process=ctx)


def test_ground_fixture_n28_uses_rule_m1_layers(tmp_path):
    """N28 process + fixture: G labels on the rule-profile M1 pin (131,0)
    and the ring on the M1 drawing (31,0)."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0,
                           NT=4, TOP_ME="10", CT_ME="8", process=ctx,
                           ground_fixture=_fixture())
    labels = write_and_parse_labels(cell, tmp_path, "gnd_n28")
    assert set(labels[(131, 0)]) == {"G01", "G02", "G03"}
    polys = write_and_parse(cell, tmp_path, "gnd_n28")
    assert (31, 0) in polys


# ---------------------------------------------------------------------------
# M7M: regression + grounded demo
# ---------------------------------------------------------------------------


def test_no_fixture_drawing_layers_unchanged(tmp_path):
    """Without a fixture no M1 ring appears; the port rename touches only pin
    labels, never a drawing polygon."""
    cell = port.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0,
                           NT=3, TOP_ME="9", CT_ME="7")
    polys = write_and_parse(cell, tmp_path, "nofix")
    assert (31, 0) not in polys


def test_generated_gnd_demo_has_grounded_ports(generated):
    """The N28 grounded demo emits p0n=p0n:G0n lines, wires references onto
    the emx_ports block, and carries G01/G02/G03 labels."""
    base = "ind_sym_ct_3t_m10m9_ct_m8_n28_gnd"
    lines = (generated / f"{base}.emx_ports").read_text().splitlines()
    assert any(":" in ln for ln in lines)
    doc = json.loads((generated / f"{base}.coordinates.json").read_text())
    refs = {p["reference"] for p in doc["emx_ports"]}
    assert refs == {"G01", "G02", "G03"}
    label_texts = {lb["text"] for lb in doc["labels"]}
    assert {"G01", "G02", "G03"} <= label_texts


# ---------------------------------------------------------------------------
# M7N: via-array primitives (vias_nomet + base_oct_quad_vias +
# base_oct_half_vias)
# ---------------------------------------------------------------------------


def test_vias_nomet_reference_cuts_only_no_metal(tmp_path):
    """vias_nomet draws the cut array only; no metal rectangles."""
    cell = port.vias_nomet(Length=4.0, Width=4.0, TOP_ME=9, BTM_ME=8)
    layers = write_and_parse(cell, tmp_path, "vnm_ref")
    assert set(layers) == {port.via_layer(8)}
    cuts = [rect_bbox(p) for p in layers[port.via_layer(8)]]
    assert all((x2 - x1, y2 - y1) == (360, 360) for x1, y1, x2, y2 in cuts)


def test_vias_nomet_n28_via8_cuts_only(tmp_path):
    """N28 process mode: VIA8 0.46 um cuts on (58,80), no metal."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.vias_nomet(Length=4.0, Width=4.0, TOP_ME=9, BTM_ME=8, process=ctx)
    layers = write_and_parse(cell, tmp_path, "vnm_n28")
    assert set(layers) == {(58, 80)}
    assert all((x2 - x1, y2 - y1) == (460, 460)
               for x1, y1, x2, y2 in (rect_bbox(p) for p in layers[(58, 80)]))


def test_vias_nomet_matches_vias_cuts_minus_metal(tmp_path):
    """vias_nomet cuts are identical to vias cuts; vias_nomet has no metal."""
    a = write_and_parse(port.vias(Length=4.0, Width=4.0, TOP_ME=9, BTM_ME=8), tmp_path, "v")
    b = write_and_parse(port.vias_nomet(Length=4.0, Width=4.0, TOP_ME=9, BTM_ME=8), tmp_path, "vn")
    assert b[port.via_layer(8)] == a[port.via_layer(8)]
    assert port.metal_layer(8) in a and port.metal_layer(8) not in b


def test_base_oct_quad_vias_axis_block(tmp_path):
    """Axis-aligned BB VIA8 block occupies the corner region near (OD/2, OD/2)."""
    cell = port.base_oct_quad_vias(OD=60.0, W=2.0, OP=0.0, MET=9)
    layers = write_and_parse(cell, tmp_path, "oqv")
    cuts = [rect_bbox(p) for p in layers[port.via_layer(8)]]
    assert cuts
    x1 = min(b[0] for b in cuts)
    x2 = max(b[2] for b in cuts)
    y1 = min(b[1] for b in cuts)
    y2 = max(b[3] for b in cuts)
    assert x2 <= 30000 and y2 <= 30000
    assert x1 >= 30000 - 12430 - 500
    assert y1 >= 28000 - 500
    assert_on_grid(layers)


def test_base_oct_quad_vias_diagonal_fails_closed():
    with pytest.raises(port.PortError, match="vias_diagonal_nomet"):
        port.base_oct_quad_vias(OD=60.0, W=2.0, OP=0.0, MET=9, diagonal_vias=True)


def test_base_oct_half_vias_mirror(tmp_path):
    """R0 quad (left, x<0) + MY quad (right, x>0) produce via cuts on both sides."""
    cell = port.base_oct_half_vias(OD=60.0, W=2.0, LOP=0.0, ROP=0.0, MET=9)
    layers = write_and_parse(cell, tmp_path, "ohv")
    cuts = [rect_bbox(p) for p in layers[port.via_layer(8)]]
    xs = [b[0] for b in cuts]
    assert min(xs) < 0 and max(xs) > 0
    assert_on_grid(layers)


# ---------------------------------------------------------------------------
# M7P: balun secondary primitive (shared with xfm_balun)
# ---------------------------------------------------------------------------


def test_base_balun_sec_is_single_m9_octagon_reference(tmp_path):
    """base_balun_sec = single MET=9 octagon ring; structurally identical
    to a standalone base_oct(OD, W=WI, LOP, ROP=0, MET=9)."""
    layers = write_and_parse(port.base_balun_sec(OD=60.0, WI=4.0, LOP=10.0),
                             tmp_path, "balun_sec")
    assert set(layers) == {port.metal_layer(9)}
    ref = write_and_parse(
        port.base_oct(OD=60.0, W=4.0, LOP=10.0, ROP=0.0, MET=9),
        tmp_path, "balun_ref")
    got = sorted(canon(p) for p in layers[port.metal_layer(9)])
    exp = sorted(canon(p) for p in ref[port.metal_layer(9)])
    assert len(got) == len(exp)
    assert all(polys_geometrically_equal(g, e) for g, e in zip(got, exp))
    assert_on_grid(layers)


def test_base_balun_sec_n28_layers(tmp_path):
    ctx = port.process_rule_context("n28_1p10m")
    layers = write_and_parse(
        port.base_balun_sec(OD=60.0, WI=4.0, LOP=10.0, process=ctx),
        tmp_path, "balun_sec_n28")
    assert set(layers) == {port.process_metal_layer(ctx, 9)}
    assert_on_grid(layers)


def test_generated_xfm_demos(generated):
    """Both balun-secondary demos generate GDS + coordinates JSON with polygons."""
    for base in ("base_balun_sec", "base_balun_sec_n28"):
        assert (generated / f"{base}.gds").is_file()
        doc = json.loads((generated / f"{base}.coordinates.json").read_text())
        assert doc["polygons"]


# ---------------------------------------------------------------------------
# M7Q: base_ind_under bridge primitive (shared with the xfm_balun escape)
# ---------------------------------------------------------------------------


def test_base_ind_under_reference_bridge_layers(tmp_path):
    """base_ind_under: 3 vias bridge on M5-M9 + VIA5-VIA8, no dummy layers."""
    layers = write_and_parse(
        port.base_ind_under(W=10.0, WX=10.0, S=9.1, TOP_ME=9, BTM_ME=5, NT=1),
        tmp_path, "ind_under")
    expected = ({port.metal_layer(m) for m in range(5, 10)}
                | {port.via_layer(m) for m in range(5, 9)})
    assert set(layers) == expected
    assert_on_grid(layers)


def test_base_ind_under_n28_builds(tmp_path):
    """n28-rules-slim (user directive 2026-07-19): VIA5..VIA8 all have
    complete via_primitives geometry, so this now builds the same M5-M9
    bridge as the reference-mode test above (previously fell closed on the
    retired VIA5 via_restrictions/IND.R.1 policy gate). W=4 rather than
    the reference test's 10: every level of the stack must respect its own
    max-width rule, and the thin M5/M6 admit far less than M9 (gdsfactory
    review 2026-09-21)."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.base_ind_under(W=4.0, WX=4.0, S=3.0, TOP_ME=9, BTM_ME=5,
                               NT=1, process=ctx)
    layers = write_and_parse(cell, tmp_path, "ind_under_n28")
    expected = ({port.process_metal_layer(ctx, m) for m in range(5, 10)}
                | {port.process_via_layer(ctx, m) for m in range(5, 9)})
    assert set(layers) == expected
    assert_on_grid(layers)


# ---------------------------------------------------------------------------
# xfm_bs: broadside single-turn two-layer transformer (M7R / M7R2)
# Clean-room composition of ported primitives (base_oct/base_lead_pair/vias/
# base_lead); NOT a .il transcription. Independent OD_P/OD_S + CENTER_SPACING
# (M7R2); CENTER_SPACING=0, OD_P==OD_S reduces to the M7R concentric build.
# ---------------------------------------------------------------------------


def _bs(**kw):
    base = dict(OD_P=90.0, OD_S=90.0, W_P=6.0, W_S=6.0, OPENING_P=8.0,
               OPENING_S=8.0, LEAD_P=20.0, LEAD_S=20.0, CENTER_SPACING=0.0,
               PRI_ME="10", SEC_ME="9")
    base.update(kw)
    return port.xfm_bs(**base)


def test_xfm_bs_concentric_matches_m7r(tmp_path):
    # Openings outward (product convention): primary/single left, secondary/multi right
    cell = _bs()
    p = {q["logical_name"]: (tuple(q["label_xy_um"]), q["metal"])
         for q in cell.emx_ports}
    assert p["P1"] == ((-65.0, 11.0), "M10")
    assert p["N1"] == ((-65.0, -11.0), "M10")
    assert p["P2"] == ((65.0, 11.0), "M9")
    assert p["N2"] == ((65.0, -11.0), "M9")
    assert port.emx_port_lines(cell.emx_ports) == [
        "-p N1=N1", "-p N2=N2", "-p P1=P1", "-p P2=P2"]
    assert_on_grid(write_and_parse(cell, tmp_path, "bs"))


def test_xfm_bs_independent_od_and_spacing(tmp_path):
    cell = _bs(OD_P=100.0, OD_S=76.0, CENTER_SPACING=8.0)
    layers = write_and_parse(cell, tmp_path, "bs_indep")
    # primary opens left at xP=-CS/2=-4 -> solid right edge at xP+OD_P/2=46
    m10 = layers[port.metal_layer(10)]
    xs = [x for poly in m10 for (x, y) in poly]
    assert max(xs) == pytest.approx(_nm(-4.0 + 50.0))   # xP + OD_P/2
    # secondary opens right at xS=+CS/2=+4 -> solid left edge at xS-OD_S/2=-34
    m9 = layers[port.metal_layer(9)]
    xs9 = [x for poly in m9 for (x, y) in poly]
    assert min(xs9) == pytest.approx(_nm(4.0 - 38.0))    # xS - OD_S/2
    p = {q["logical_name"]: tuple(q["label_xy_um"]) for q in cell.emx_ports}
    assert p["P1"] == (-4.0 - 50.0 - 20.0, 11.0)         # (-74, 11)
    assert p["P2"] == (4.0 + 38.0 + 20.0, 11.0)          # (62, 11)
    assert_on_grid(layers)


def test_xfm_bs_n28_layers(tmp_path):
    ctx = port.process_rule_context("n28_1p10m")
    cell = _bs(process=ctx)
    layers = write_and_parse(cell, tmp_path, "xfm_bs_n28")
    assert {port.process_metal_layer(ctx, 9),
            port.process_metal_layer(ctx, 10)} <= set(layers)
    # coil-only build carries no via layers (same-metal leads, no CT)
    assert not any(50 <= lay <= 59 for (lay, _dt) in layers)
    assert_on_grid(layers)


def test_xfm_bs_failclosed_same_metal_and_ct_rules():
    with pytest.raises(port.PortError):
        port.xfm_bs(PRI_ME="9", SEC_ME="9")
    with pytest.raises(port.PortError):
        port.xfm_bs(CT_P_ME="10")              # CT not below PRI=M10
    with pytest.raises(port.PortError):
        port.xfm_bs(CT_S_ME="9")               # CT not below SEC=M9
    # CT_P geometric short: the tap (W_P=6) overhangs the secondary's left
    # opening gap (width 2*OPENING_S); shorts when OPENING_S < W_P/2 (=3)
    with pytest.raises(port.PortError):
        port.xfm_bs(OPENING_S=2.0, W_P=6.0, CT_P_ME="8")


def test_xfm_bs_center_taps(tmp_path):
    cell = _bs(CT_P_ME="8", CT_S_ME="8")
    layers = write_and_parse(cell, tmp_path, "xfm_bs_ct")
    p = {q["logical_name"]: (tuple(q["label_xy_um"]), q["metal"])
         for q in cell.emx_ports}
    # outward: primary opens left (CT on right), secondary opens right (CT on left)
    assert p["CTP"] == ((65.0, 0.0), "M8")
    assert p["CTS"] == ((-65.0, 0.0), "M8")
    # tap stacks M10->M8 / M9->M8 add VIA8/VIA9 cut arrays
    assert port.via_layer(8) in layers and port.via_layer(9) in layers
    assert_on_grid(layers)


def test_xfm_bs_ct_n28_via8_via9_only(tmp_path):
    ctx = port.process_rule_context("n28_1p10m")
    cell = _bs(CT_P_ME="8", CT_S_ME="8", process=ctx)
    layers = write_and_parse(cell, tmp_path, "xfm_bs_ct_n28")
    vias_present = {lay for (lay, dt) in layers if 50 <= lay <= 59}
    assert vias_present <= {port.process_via_layer(ctx, 8)[0],
                            port.process_via_layer(ctx, 9)[0]}
    assert_on_grid(layers)


def test_xfm_bs_ground_fixture_local_refs():
    cfg = port.GroundFixtureConfig(inner_margin_um=5.0, ring_width_um=6.0,
                                   stub_width_um=4.0, stub_length_um=3.0,
                                   stub_chamfer_um=0.0)
    cell = port.xfm_bs(ground_fixture=cfg)
    refs = {q["logical_name"]: q["reference"] for q in cell.emx_ports}
    assert refs["P1"] == "G01" and all(v is not None for v in refs.values())
    assert all(":" in line for line in port.emx_port_lines(cell.emx_ports))


def test_xfm_bs_ct_concentric_builds(tmp_path):
    # concentric same-OD CT clears the geometric check (gap 2*OPENING_S=16
    # fits the W_P=6 tap) and builds with CTP/CTS on M8 + VIA8
    cell = _bs(CT_P_ME="8", CT_S_ME="8")
    p = {q["logical_name"]: (tuple(q["label_xy_um"]), q["metal"])
         for q in cell.emx_ports}
    # outward: primary opens left (CT on right), secondary opens right (CT on left)
    assert p["CTP"] == ((65.0, 0.0), "M8")
    assert p["CTS"] == ((-65.0, 0.0), "M8")
    assert port.via_layer(8) in write_and_parse(cell, tmp_path, "bs_ct")


def test_xfm_bs_ct_geometric_clearance_failclosed():
    # CT_P: tap (W_P=6) overhangs the secondary's right opening gap (width
    # 2*OPENING_S=4 < tap 6) -> real short on the shared M9
    with pytest.raises(port.PortError):
        port.xfm_bs(OD_P=90.0, OD_S=90.0, CENTER_SPACING=0.0,
                    W_P=6.0, W_S=6.0, OPENING_P=8.0, OPENING_S=2.0,
                    PRI_ME="10", SEC_ME="9", CT_P_ME="8")
    # CT_S: only shorts when the primary metal is inside the CT_S tap stack.
    # PRI=9, SEC=10 -> primary M9 is in [CT_S=8, SEC=10]; OPENING_P=2 shorts.
    with pytest.raises(port.PortError):
        port.xfm_bs(OD_P=90.0, OD_S=90.0, CENTER_SPACING=0.0,
                    W_P=6.0, W_S=6.0, OPENING_P=2.0, OPENING_S=8.0,
                    PRI_ME="9", SEC_ME="10", CT_S_ME="8")


def test_xfm_bs_ct_skips_out_of_stack_metal():
    # Default PRI=10/SEC=9/CT_S=8: the CT_S tap stack [8,9] excludes the
    # primary M10, so the geometric check correctly allows a tiny OPENING_P
    # (the M7R numeric guard OPENING_P>=W_S falsely rejected this case).
    cell = port.xfm_bs(OD_P=90.0, OD_S=90.0, CENTER_SPACING=0.0,
                       W_P=6.0, W_S=6.0, OPENING_P=2.0, OPENING_S=8.0,
                       PRI_ME="10", SEC_ME="9", CT_S_ME="8")
    assert {"CTS"} <= {q["logical_name"] for q in cell.emx_ports}


def test_xfm_bs_center_spacing_is_intuitive():
    # outward convention: larger CENTER_SPACING -> terminals farther apart
    def term(cs):
        c = port.xfm_bs(OD_P=90.0, OD_S=90.0, W_P=6.0, W_S=6.0, OPENING_P=8.0,
                        OPENING_S=8.0, LEAD_P=20.0, LEAD_S=20.0,
                        CENTER_SPACING=cs, PRI_ME="10", SEC_ME="9")
        p = {q["logical_name"]: q["label_xy_um"][0] for q in c.emx_ports}
        return p["P2"] - p["P1"]
    assert term(40) > term(0) > term(-20)
    assert term(0) == pytest.approx(130.0)


# ---------------------------------------------------------------------------
# AP layer support: metal index 11, reference & N28 (M7S Part A)
# ---------------------------------------------------------------------------


def test_ap_metal_index_and_layers():
    assert port._metal_index("AP") == 11
    assert port._metal_index("10") == 10
    assert port._metal_name(11) == "AP"
    assert port.metal_layer(11) == (41, 0)
    assert port.metal_pin_layer(11) == (141, 0)
    assert port.via_layer(10) == (60, 0)
    ctx = port.process_rule_context("n28_1p10m")
    assert port.process_metal_layer(ctx, 11) == (74, 0)
    assert port.process_pin_layer(ctx, 11) == (126, 0)
    assert port.process_via_layer(ctx, 10) == (85, 0)   # RV


def test_ap_single_turn_via_xfm_bs(tmp_path):
    # AP is a metal now: the product's M10/AP single-turn transformer builds
    cell = port.xfm_bs(OD_P=90.0, OD_S=90.0, W_P=6.0, W_S=6.0, OPENING_P=8.0,
                       OPENING_S=8.0, LEAD_P=20.0, LEAD_S=20.0,
                       CENTER_SPACING=0.0, PRI_ME="10", SEC_ME="AP")
    layers = write_and_parse(cell, tmp_path, "bs_ap")
    assert port.metal_layer(10) in layers and port.metal_layer(11) in layers
    aps = [q for q in cell.emx_ports if q["metal"] == "AP"]
    assert len(aps) == 2   # the AP winding's two ports (metal name "AP", not "M11")
    assert_on_grid(layers)


# ---------------------------------------------------------------------------
# xfm_ms: multi-turn + single-turn different-layer transformer (M7S)
# ---------------------------------------------------------------------------


def _ms(**kw):
    base = dict(OD_S=100.0, OD_M=76.0, W_S=6.0, W_M=3.0, OPENING_S=8.0,
                OPENING_M=6.0, LEAD_S=20.0, LEAD_M=15.0, NT_M=3, S_M=2.0,
                CENTER_SPACING=0.0, SINGLE_ME="AP", MULTI_ME="10")
    base.update(kw)
    return port.xfm_ms(**base)


def test_xfm_ms_reference_layers_and_ports(tmp_path):
    cell = _ms()
    layers = write_and_parse(cell, tmp_path, "xfm_ms")
    # single on AP (index 11), multi coil M10 + crossover M9 + VIA9
    assert port.metal_layer(11) in layers          # AP single
    assert port.metal_layer(10) in layers           # M10 multi coil
    assert port.metal_layer(9) in layers            # M9 crossover
    assert port.via_layer(9) in layers              # VIA9
    p = {q["logical_name"]: tuple(q["label_xy_um"]) for q in cell.emx_ports}
    # outward: single opens left (exits -x), multi opens right (exits +x)
    assert p["P1"] == (0.0 - 100.0 / 2 - 20.0, 8.0 + 6.0 / 2)      # (-70, 11)
    assert p["P2"] == (0.0 + 76.0 / 2 + 15.0, 6.0 + 3.0 / 2)       # (53, 7.5)
    assert {q["logical_name"] for q in cell.emx_ports} == {"P1", "N1", "P2", "N2"}
    assert_on_grid(layers)


def test_xfm_ms_n28_builds(tmp_path):
    ctx = port.process_rule_context("n28_1p10m")
    cell = _ms(process=ctx)
    layers = write_and_parse(cell, tmp_path, "xfm_ms_n28")
    assert port.process_metal_layer(ctx, 11) in layers   # AP (74,0)
    assert port.process_metal_layer(ctx, 10) in layers
    assert port.process_metal_layer(ctx, 9) in layers    # leg1 (multi-1)
    # reference bridge scheme (design-region issue 03): the multi coil's
    # leg2 crosses OVER on its own layer; multi-2 and its via class are
    # never drawn.
    assert port.process_metal_layer(ctx, 8) not in layers
    assert port.process_via_layer(ctx, 9) in layers      # VIA9
    assert port.process_via_layer(ctx, 8) not in layers
    assert_on_grid(layers)


def test_xfm_ms_failclosed():
    with pytest.raises(port.PortError):
        port.xfm_ms(SINGLE_ME="9", MULTI_ME="10")   # single not higher
    with pytest.raises(port.PortError):
        port.xfm_ms(SINGLE_ME="10", MULTI_ME="10")  # equal
    with pytest.raises(port.PortError):
        port.xfm_ms(NT_M=1)                          # not multi-turn
    with pytest.raises(port.PortError):
        port.xfm_ms(MULTI_ME="1")                    # crossover needs layer below


# ---------------------------------------------------------------------------
# base_xfm_half (M7T Task 1): metal-generic stacked half-ring primitive
# ---------------------------------------------------------------------------


def _xfmhalf_layers(tmp_path, **kw):
    base = dict(OD=200.0, WO=5.0, S=2.0, LOP=15.0, ROP=15.0)
    base.update(kw)
    return write_and_parse(port.base_xfm_half(**base), tmp_path, "bxh")


def test_base_xfm_half_single_metal(tmp_path):
    layers = _xfmhalf_layers(tmp_path, TOP_ME=9, BTM_ME=9)
    assert set(layers) == {port.metal_layer(9)}          # (39,0) only, no via
    assert_on_grid(layers)


def test_base_xfm_half_stacked_via_to_next(tmp_path):
    layers = _xfmhalf_layers(tmp_path, TOP_ME=9, BTM_ME=8, via_to_next=True)
    assert set(layers) == {port.metal_layer(8), port.metal_layer(9),
                           port.via_layer(8)}             # M8+M9+VIA8
    assert_on_grid(layers)


def test_base_xfm_half_via_diag_fails_closed():
    with pytest.raises(port.PortError):
        port.base_xfm_half(OD=200.0, WO=5.0, S=2.0, TOP_ME=9, BTM_ME=8,
                           via_diag=True)


def test_base_xfm_half_top_below_btm_fails_closed():
    with pytest.raises(port.PortError):
        port.base_xfm_half(OD=200.0, WO=5.0, S=2.0, TOP_ME=8, BTM_ME=9)


def test_base_xfm_half_n28(tmp_path):
    ctx = port.process_rule_context("n28_1p10m")
    layers = _xfmhalf_layers(tmp_path, TOP_ME=9, BTM_ME=8, via_to_next=True,
                             process=ctx)
    assert {port.process_metal_layer(ctx, 8), port.process_metal_layer(ctx, 9),
            port.process_via_layer(ctx, 8)} <= set(layers)
    assert_on_grid(layers)


# ---------------------------------------------------------------------------
# xfm_balun (M7T Task 2): classic same-layer coplanar balun
# ---------------------------------------------------------------------------


def _balun(**kw):
    base = dict(OD_P=200.0, OD_S=186.0, W_P=5.0, W_S=5.0, S=2.0,
                OPENING_P=8.0, OPENING_S=8.0, LEAD_P=20.0, LEAD_S=20.0,
                NT_P=1, NT_S=1, CENTER_SPACING=0.0, BALUN_ME="9")
    base.update(kw)
    return port.xfm_balun(**base)


def test_xfm_balun_concentric_same_metal_shortfree(tmp_path):
    cell = _balun()
    layers = write_and_parse(cell, tmp_path, "xfm_balun")
    me, esc = 9, 8
    assert port.metal_layer(me) in layers
    assert port.metal_layer(esc) in layers
    assert port.via_layer(esc) in layers
    p = {q["logical_name"]: tuple(q["label_xy_um"]) for q in cell.emx_ports}
    assert p["P1"] == (-120.0, 10.5)
    assert p["N1"] == (-120.0, -10.5)
    assert p["P2"] == (122.0, 10.5)
    assert p["N2"] == (122.0, -10.5)
    assert port.emx_port_lines(cell.emx_ports) == [
        "-p N1=N1", "-p N2=N2", "-p P1=P1", "-p P2=P2"]
    assert_on_grid(layers)


def test_xfm_balun_overlap_fails_closed():
    with pytest.raises(port.PortError):
        port.xfm_balun(OD_P=200.0, OD_S=198.0, W_P=5.0, W_S=5.0, S=2.0,
                       NT_P=1, NT_S=1, CENTER_SPACING=0.0, BALUN_ME="9")


# ---------------------------------------------------------------------------
# xfm_balun multi-turn / stack / center tap (M7T Task 3)
# ---------------------------------------------------------------------------


def test_xfm_balun_2t1t_n28(tmp_path):
    ctx = port.process_rule_context("n28_1p10m")
    cell = _balun(OD_P=200.0, OD_S=172.0, NT_P=2, NT_S=1, OPENING_S=16.0,
                  process=ctx)
    layers = write_and_parse(cell, tmp_path, "xfm_balun_2t1t")
    assert port.process_metal_layer(ctx, 9) in layers
    assert port.process_via_layer(ctx, 8) in layers
    assert_on_grid(layers)


def test_xfm_balun_multiturn_balun_me8_n28_builds(tmp_path):
    """n28-rules-slim (user directive 2026-07-19): a 2T+1T balun's
    crossunder needs BALUN_ME-1=M7 (VIA7), which now has complete
    via_primitives geometry -- this builds (previously fell closed on the
    retired VIA7 via_restrictions/IND.R.1 policy gate). OPENING_S=16.0
    (the same widened opening test_xfm_balun_2t1t_n28 uses for its own
    2T+1T build) clears the unrelated net-short guard a 2T+1T crossunder
    needs regardless of via legality."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.xfm_balun(OD_P=200.0, OD_S=172.0, NT_P=2, NT_S=1,
                          OPENING_S=16.0, BALUN_ME="8", process=ctx)
    layers = write_and_parse(cell, tmp_path, "xfm_balun_me8_n28")
    assert port.process_via_layer(ctx, 7) in layers
    assert_on_grid(layers)


def test_xfm_balun_center_tap(tmp_path):
    # nested (the classic balun): both taps, each lead reaching the device
    # edge on its exit side (2026-09-22)
    cell = _balun(CT_P_ME="8", CT_S_ME="8")
    got = {q["logical_name"]: q["metal"] for q in cell.emx_ports}
    assert got.get("CTP") == "M8" and got.get("CTS") == "M8"
    xs = {q["logical_name"]: q["point_nm"][0] for q in cell.emx_ports}
    assert xs["CTP"] == xs["P2"] and xs["CTS"] == xs["P1"]


def test_xfm_balun_refuses_rings_that_do_not_nest():
    """Coplanar rings that do not overlap are two inductors, not a balun:
    the former side-by-side mode is refused by name (user directive
    2026-09-22); so is an offset that pushes the secondary partly outside."""
    with pytest.raises(port.PortError, match="nest"):
        _balun(OD_P=90.0, OD_S=90.0, CENTER_SPACING=200.0)
    with pytest.raises(port.PortError, match="nest"):
        _balun(CENTER_SPACING=40.0)  # OD 200/186: the secondary would poke out


def test_xfm_balun_ct_above_balun_me_fails_closed():
    with pytest.raises(port.PortError):
        _balun(CT_P_ME="9")
    with pytest.raises(port.PortError):
        _balun(CT_S_ME="10")


def test_xfm_balun_ground_fixture_local_refs():
    cell = _balun(ground_fixture=port.GroundFixtureConfig(
        inner_margin_um=5.0, ring_width_um=3.0, stub_width_um=4.0,
        stub_length_um=3.0, stub_chamfer_um=1.0))
    refs = {p["reference"] for p in cell.emx_ports}
    assert refs == {f"G{i:02d}" for i in range(1, 5)}


# ---------------------------------------------------------------------------
# M7T2: layer-complete full-net short gate (the check whose absence caused
# the concentric-mode hard short)
# ---------------------------------------------------------------------------


def _net_regions(cell):
    regs = {}
    for lay, pts in cell.flat_shapes():
        regs.setdefault(lay, kdb.Region()).insert(
            kdb.Polygon([kdb.Point(x, y) for x, y in pts]))
    return regs


def _fullnet_overlap(pri, sec):
    a, b = _net_regions(pri), _net_regions(sec)
    worst = 0.0
    for lay in set(a) | set(b):
        if lay in a and lay in b:
            worst = max(worst, (a[lay] & b[lay]).area() / 1e6)
    return worst


def test_xfm_balun_concentric_fullnet_short_free():
    full = port.xfm_balun(OD_P=200.0, OD_S=186.0, W_P=5.0, W_S=5.0, S=2.0,
                          OPENING_P=8.0, OPENING_S=8.0, LEAD_P=20.0,
                          LEAD_S=20.0, NT_P=1, NT_S=1, CENTER_SPACING=0.0,
                          BALUN_ME="9")
    pri, sec = full.insts[0].cell, full.insts[1].cell
    assert _fullnet_overlap(pri, sec) == 0.0


def test_xfm_balun_crossunder_metal_generic():
    for balun_me in ("10", "AP"):
        full = port.xfm_balun(OD_P=200.0, OD_S=186.0, W_P=5.0, W_S=5.0,
                              S=2.0, OPENING_P=8.0, OPENING_S=8.0,
                              LEAD_P=20.0, LEAD_S=20.0, NT_P=1, NT_S=1,
                              CENTER_SPACING=0.0, BALUN_ME=balun_me)
        assert _fullnet_overlap(full.insts[0].cell, full.insts[1].cell) == 0.0
    full = port.xfm_balun(OD_P=200.0, OD_S=186.0, W_P=5.0, W_S=5.0, S=2.0,
                          OPENING_P=8.0, OPENING_S=8.0, LEAD_P=20.0,
                          LEAD_S=20.0, NT_P=1, NT_S=1, CENTER_SPACING=0.0,
                          BALUN_ME="10", ESCAPE_ME="8")
    assert _fullnet_overlap(full.insts[0].cell, full.insts[1].cell) == 0.0
    layers = {lay for lay, _ in full.insts[1].cell.flat_shapes()}
    assert port.metal_layer(8) in layers


def test_xfm_balun_escape_me_failclosed():
    with pytest.raises(port.PortError):
        port.xfm_balun(OD_P=200.0, OD_S=186.0, CENTER_SPACING=0.0,
                       BALUN_ME="9", ESCAPE_ME="9")
    # (M7V: AP on N28 now BUILDS via the RV M10<->AP via -- see
    # test_xfm_balun_ap_builds_in_n28 -- so it is no longer a fail-closed case.)


def test_xfm_balun_2t1t_opening16_clean_and_opening8_shorts():
    ok = port.xfm_balun(OD_P=200.0, OD_S=172.0, W_P=5.0, W_S=5.0, S=2.0,
                        OPENING_P=8.0, OPENING_S=16.0, LEAD_P=20.0,
                        LEAD_S=20.0, NT_P=2, NT_S=1, CENTER_SPACING=0.0,
                        BALUN_ME="9")
    assert _fullnet_overlap(ok.insts[0].cell, ok.insts[1].cell) == 0.0
    with pytest.raises(port.PortError):
        port.xfm_balun(OD_P=200.0, OD_S=172.0, W_P=5.0, W_S=5.0, S=2.0,
                       OPENING_P=8.0, OPENING_S=8.0, LEAD_P=20.0,
                       LEAD_S=20.0, NT_P=2, NT_S=1, CENTER_SPACING=0.0,
                       BALUN_ME="9")


def test_xfm_balun_nested_rules():
    with pytest.raises(port.PortError):
        port.xfm_balun(OD_P=200.0, OD_S=172.0, NT_P=1, NT_S=2,
                       CENTER_SPACING=0.0, BALUN_ME="9")
    with pytest.raises(port.PortError):
        port.xfm_balun(OD_P=172.0, OD_S=200.0, NT_P=1, NT_S=1,
                       CENTER_SPACING=0.0, BALUN_ME="9")


# ---------------------------------------------------------------------------
# M7U: OPENING upper-bound fail-closed guard
# (docs/superpowers/specs/2026-07-06-m7u-opening-upper-bound-guard-design.md)
#
# Above OPENING = max_opening(OD, W) = BA - C, base_oct_quad stops honouring
# the opening leg, so it detaches from base_lead_pair and the P/N ports float
# silently. max_opening is bit-exact with base_oct_quad's OP>(BA-C) branch and
# depends only on OD and W (metal-independent). Three winding sites are
# guarded: ind_sym (with or without CT_ME), _bs_winding, and _ci_winding.
# ---------------------------------------------------------------------------


def test_max_opening_exact_bound():
    # bit-exact with base_oct_quad's OP>(BA-C) branch; metal-independent.
    assert abs(port.max_opening(100.0, 5.0) - 18.625) < 1e-9
    assert abs(port.max_opening(50.0, 2.0) - 9.515) < 1e-9
    assert abs(port.max_opening(60.0, 5.0) - 10.340) < 1e-9


def test_ind_sym_opening_over_bound_raises():
    # Over the bound the octagon opening leg detaches from the lead pair,
    # floating P1/N1. Must fail closed instead of emitting bad GDS.
    with pytest.raises(port.PortError, match="OPENING"):
        port.ind_sym(OD=100.0, W=5.0, OPENING=20.0, LEAD=20.0, S=2.0, NT=2,
                     TOP_ME="9", BTM_ME="8")


def test_ind_sym_opening_at_bound_builds(tmp_path):
    mx = port.max_opening(100.0, 5.0)  # 18.625, on grid
    cell = port.ind_sym(OD=100.0, W=5.0, OPENING=mx, LEAD=20.0, S=2.0, NT=2,
                        TOP_ME="9", BTM_ME="8")
    layers = write_and_parse(cell, tmp_path, "ind_sym_at_bound")
    assert layers
    assert_on_grid(layers)


def test_ind_sym_opening_just_over_bound_raises():
    # bit-exact boundary in the RAISING direction: bound + one grid step must
    # fail closed, for >=2 (OD,W) pairs. Guards against an off-by-one-grid
    # regression in max_opening that a loose OPENING=20 test would miss.
    for OD, W in ((100.0, 5.0), (50.0, 2.0)):
        over = round(port.max_opening(OD, W) + 0.005, 3)  # 18.630, 9.520
        with pytest.raises(port.PortError, match="OPENING"):
            port.ind_sym(OD=OD, W=W, OPENING=over, LEAD=20.0, S=2.0, NT=2,
                         TOP_ME="9", BTM_ME="8")


def test_ind_sym_ct_opening_over_bound_raises():
    with pytest.raises(port.PortError, match="OPENING"):
        port.ind_sym(OD=100.0, W=5.0, OPENING=20.0, LEAD=20.0, S=2.0, NT=2,
                        TOP_ME="9", BTM_ME="7")


def test_opening_guard_metal_generic():
    # bound is geometry-only. ind_sym is int(TOP_ME)-based (numeric metals
    # only), so exercise it at "9"/"10"; AP is covered via the _metal_index-
    # based xfm_balun. Same OPENING, same bound, regardless of metal.
    for me in ("9", "10"):
        with pytest.raises(port.PortError, match="OPENING"):
            port.ind_sym(OD=100.0, W=5.0, OPENING=20.0, LEAD=20.0, S=2.0, NT=2,
                         TOP_ME=me, BTM_ME=str(int(me) - 1))


def _merged_metal(cell, metal_index, tmp_path, name="conn"):
    """Merged kdb.Region of layer metal_layer(metal_index) from a written cell."""
    gds = tmp_path / f"{name}.gds"
    port.write_gds(cell, gds)
    ly = kdb.Layout()
    ly.read(str(gds))
    top = ly.top_cell()
    top.flatten(True)
    li = ly.find_layer(*port.metal_layer(int(metal_index)))
    reg = kdb.Region()
    if li is not None:
        for s in top.shapes(li).each():
            if not s.is_text():
                reg.insert(s.polygon)
    reg.merge()
    return reg


def _same_component(reg, ax, ay, bx, by):
    """True iff (ax,ay) and (bx,by) [um] land on one connected metal blob."""
    comps = [kdb.Region(p) for p in reg.each()]

    def idx(x, y):
        pt = kdb.Point(_nm(x), _nm(y))
        probe = kdb.Region(kdb.Box(pt - kdb.Vector(1, 1), pt + kdb.Vector(1, 1)))
        for i, c in enumerate(comps):
            if not (c & probe).is_empty():
                return i
        return None

    ia, ib = idx(ax, ay), idx(bx, by)
    return ia is not None and ia == ib


def test_xfm_bs_opening_over_bound_raises():
    # single-turn broadside winding shares the base_oct opening-leg bug.
    with pytest.raises(port.PortError, match="OPENING"):
        port.xfm_bs(OD_P=100.0, OD_S=100.0, W_P=5.0, W_S=5.0,
                    OPENING_P=20.0, OPENING_S=8.0, LEAD_P=20.0, LEAD_S=20.0,
                    CENTER_SPACING=0.0, PRI_ME="10", SEC_ME="9")


def test_xfm_balun_opening_over_bound_raises():
    # concentric balun, OD_P=100 -> bound 18.625; OPENING_P=20 crosses it.
    with pytest.raises(port.PortError, match="OPENING"):
        port.xfm_balun(OD_P=100.0, OD_S=86.0, W_P=5.0, W_S=5.0,
                       OPENING_P=20.0, OPENING_S=8.0, LEAD_P=20.0, LEAD_S=20.0,
                       S=2.0, NT_P=1, NT_S=1, CENTER_SPACING=0.0, BALUN_ME="9")


def test_xfm_balun_opening_guard_ap_metal():
    # AP metal case (the _metal_index-based cell supports it): same bound,
    # same fail-closed -> proves the guard is metal-generic at the top metal.
    with pytest.raises(port.PortError, match="OPENING"):
        port.xfm_balun(OD_P=100.0, OD_S=86.0, W_P=5.0, W_S=5.0,
                       OPENING_P=20.0, OPENING_S=8.0, LEAD_P=20.0, LEAD_S=20.0,
                       S=2.0, NT_P=1, NT_S=1, CENTER_SPACING=0.0, BALUN_ME="AP")


def test_xfm_balun_ap_builds_in_n28(tmp_path):
    # M7V: concentric AP balun in N28 escapes AP->M10 via RV (single 3um cut).
    # Must build on real layers (AP 74/0, M10 40/80, RV 85/0) -> importable.
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.xfm_balun(OD_P=200.0, OD_S=186.0, CENTER_SPACING=0.0,
                          BALUN_ME="AP", process=ctx)
    layers = write_and_parse(cell, tmp_path, "ap_n28")
    assert (74, 0) in layers      # AP rings/leads
    assert (40, 80) in layers     # M10 crossunder bridge
    assert (85, 0) in layers      # RV single cut (M10<->AP)
    assert_on_grid(layers)


def test_ind_sym_ap_builds_in_n28(tmp_path):
    # M12: AP coil + M10 crossunder escapes via RV (single 3um cut).
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.ind_sym(OD=120.0, W=5.0, OPENING=8.0, LEAD=20.0, S=2.5,
                        NT=3, TOP_ME="AP", BTM_ME="10", process=ctx)
    layers = write_and_parse(cell, tmp_path, "ind_ap_n28")
    assert (74, 0) in layers      # AP coil
    assert (40, 80) in layers     # M10 crossunder
    assert (85, 0) in layers      # RV cuts
    assert_on_grid(layers)


def test_xfm_bs_ap_m10_builds_in_n28(tmp_path):
    # M12: broadside 1:1, AP primary over M10 secondary, via-free windings.
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.xfm_bs(OD_P=120.0, OD_S=120.0, W_P=4.0, W_S=4.0,
                       OPENING_P=8.0, OPENING_S=8.0, LEAD_P=20.0, LEAD_S=20.0,
                       CENTER_SPACING=0.0, PRI_ME="AP", SEC_ME="10",
                       process=ctx)
    layers = write_and_parse(cell, tmp_path, "bs_ap_n28")
    assert (74, 0) in layers and (40, 80) in layers
    assert (85, 0) not in layers  # no RV anywhere in a via-free bs pair
    assert_on_grid(layers)


def test_ind_sym_ap_rv_landing_pins_w_floor(tmp_path):
    # M12: RV needs 3.0 cut + 2x0.5 enclosure -> 4.0 um landing on both
    # AP and M10 at the crossunder junction. W at the floor builds; below
    # it fails closed. Measured floor = 4.0 (probed W in {4.0, 3.9, 3.5,
    # 3.0, 2.5, 2.0}: only 4.0 builds), matching the rule math exactly.
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.ind_sym(OD=120.0, W=4.0, OPENING=8.0, LEAD=20.0, S=2.5,
                        NT=3, TOP_ME="AP", BTM_ME="10", process=ctx)
    assert cell is not None
    with pytest.raises(port.PortError):
        port.ind_sym(OD=120.0, W=3.9, OPENING=8.0, LEAD=20.0, S=2.5,
                     NT=3, TOP_ME="AP", BTM_ME="10", process=ctx)


def test_xfm_balun_ap_rv_landing_pins_w_floor(tmp_path):
    # M12: same 4.0 um RV landing floor on the balun's AP->M10 crossunder
    # escape. Measured floor = 4.0 (probed W_P=W_S in {5.0, 4.5, 4.0, 3.9,
    # 3.5, 3.0, 2.5, 2.0}: 4.0 builds, 3.9 fails closed), matching the
    # rule math (3.0 cut + 2x0.5 enclosure) exactly.
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.xfm_balun(OD_P=200.0, OD_S=186.0, W_P=4.0, W_S=4.0,
                          CENTER_SPACING=0.0, BALUN_ME="AP", process=ctx)
    assert cell is not None
    with pytest.raises(port.PortError):
        port.xfm_balun(OD_P=200.0, OD_S=186.0, W_P=3.9, W_S=3.9,
                       CENTER_SPACING=0.0, BALUN_ME="AP", process=ctx)


def test_bs_winding_below_bound_connected(tmp_path):
    # positive control: at a legal OPENING the primary port stays wired to the
    # ring (proves the guard defends a real connectivity failure, not a no-op).
    cell = port.xfm_bs(OD_P=100.0, OD_S=100.0, W_P=5.0, W_S=5.0,
                       OPENING_P=8.0, OPENING_S=8.0, LEAD_P=20.0, LEAD_S=20.0,
                       CENTER_SPACING=0.0, PRI_ME="10", SEC_ME="9")
    reg = _merged_metal(cell, 10, tmp_path, "bs_conn")
    # primary P tip (-70, 10.5) and a ring-body point (0, 47.5), both on M10
    assert _same_component(reg, -(50.0 + 20.0), 8.0 + 2.5, 0.0, 50.0 - 2.5)


# ---------------------------------------------------------------------------
# M13 ticket 05: xfm_bs/xfm_balun CT exposure — layer-complete tap gate +
# canonical port order (base [P1,N1,P2,N2] then CTP, CTS)
# ---------------------------------------------------------------------------


def test_xfm_bs_separated_windings_are_refused():
    """Two rings side by side are two inductors, not a broadside
    transformer: the pcell enforces the product's overlap bound itself
    (user directive 2026-09-22), which also rules out the facing-tap
    short this configuration used to produce."""
    with pytest.raises(port.PortError, match="overlap"):
        port.xfm_bs(OD_P=90.0, OD_S=90.0, W_P=4.0, W_S=4.0,
                    OPENING_P=8.0, OPENING_S=8.0, LEAD_P=20.0, LEAD_S=20.0,
                    CENTER_SPACING=100.0, PRI_ME="10", SEC_ME="9",
                    CT_P_ME="8", CT_S_ME="8")


def test_xfm_bs_ct_port_order_base_then_taps():
    """Contract: fixed base [P1, N1, P2, N2], enabled taps appended CTP
    before CTS (sNp column order = port creation order)."""
    cell = _bs(CT_P_ME="8", CT_S_ME="7")
    assert [q["name"] for q in cell.emx_ports] == [
        "P1", "N1", "P2", "N2", "CTP", "CTS"]
    single = _bs(CT_S_ME="7")
    assert [q["name"] for q in single.emx_ports] == [
        "P1", "N1", "P2", "N2", "CTS"]


def test_xfm_balun_ct_port_order_base_then_taps():
    """Balun taps must also append after the full 4-port base (the balun
    build assembles pri+sec sub-cells, so without reordering CTP would sit
    between N1 and P2)."""
    cell = _balun(CT_P_ME="7", CT_S_ME="7")
    assert [q["name"] for q in cell.emx_ports] == [
        "P1", "N1", "P2", "N2", "CTP", "CTS"]


# ---------------------------------------------------------------------------
# M13 ticket 06: xfm_ms dual-side optional CT (P side = single winding via
# the bs closed-column tap; S side = multi winding via ind_sym's CT_ME)
# ---------------------------------------------------------------------------


def test_xfm_ms_dual_ct_ports_and_positions(tmp_path):
    """Both taps enabled: canonical port set [P1,N1,P2,N2,CTP,CTS]; CTP at
    the single winding's outward tap exit, CTS at the multi winding's
    ind_sym tap exit (odd NT: crossover/left side, offset by the multi
    center)."""
    cell = port.xfm_ms(CT_P_ME="9", CT_S_ME="8")
    assert [q["name"] for q in cell.emx_ports] == [
        "P1", "N1", "P2", "N2", "CTP", "CTS"]
    p = {q["name"]: (tuple(q["label_xy_um"]), q["metal"])
         for q in cell.emx_ports}
    # defaults: OD_S=100 single opens left -> tap exits right at +50+20
    assert p["CTP"] == ((70.0, 0.0), "M9")
    # multi OD_M=76, NT_M=3 (odd -> left exit): the tap lead runs on to
    # the single winding's outer edge (P1/N1 at -50-20) so its port is
    # peripheral (2026-09-22), not the interior -76/2-15 = -53 it used to be.
    assert p["CTS"] == ((-70.0, 0.0), "M8")
    layers = write_and_parse(cell, tmp_path, "ms_dual_ct")
    assert (39, 0) in layers and (38, 0) in layers


def test_xfm_ms_single_side_ct_only():
    cell = port.xfm_ms(CT_S_ME="8")
    assert [q["name"] for q in cell.emx_ports] == [
        "P1", "N1", "P2", "N2", "CTS"]


def test_xfm_ms_multi_ct_adjacency_guard_same_rule_as_ind():
    """The multi winding is an ind_sym: its crossunder occupies MULTI_ME-1,
    so CT_S_ME must sit at least two levels below MULTI_ME — same N1 rule,
    same wording, xfm_ms device label."""
    with pytest.raises(port.PortError, match="two levels below"):
        port.xfm_ms(CT_S_ME="9")
    with pytest.raises(port.PortError, match="xfm_ms"):
        port.xfm_ms(CT_S_ME="9")


def test_xfm_ms_ct_p_must_sit_below_single_plane():
    with pytest.raises(port.PortError, match="below"):
        port.xfm_ms(CT_P_ME="AP")


def test_xfm_ms_dual_ct_mutual_lead_short_fails_closed():
    """Side-by-side: CTP exits right toward the multi winding while the
    odd-NT CTS exits left toward the single winding; same CT metal +
    overlapping y=0 spans -> tap-to-tap short caught by the net gate."""
    with pytest.raises(port.PortError, match="overlap"):
        port.xfm_ms(CENTER_SPACING=120.0, CT_P_ME="8", CT_S_ME="8")


def test_xfm_ms_primary_side_ct_only():
    """仅P combo: the single-winding tap alone appends just CTP."""
    cell = port.xfm_ms(CT_P_ME="9")
    assert [q["name"] for q in cell.emx_ports] == [
        "P1", "N1", "P2", "N2", "CTP"]
    p = {q["name"]: (tuple(q["label_xy_um"]), q["metal"])
         for q in cell.emx_ports}
    assert p["CTP"] == ((70.0, 0.0), "M9")


# ---------------------------------------------------------------------------
# ind_sym / base_ind_hud_cross PITCH parameterization (xfm_il ticket 01):
# the radial step between adjacent same-winding turns becomes an explicit,
# overridable parameter instead of the hardcoded W+S. PITCH=None (the
# default) stays byte-identical (the pinned NT=3 tests above never pass
# PITCH and still pass unmodified). An explicit PITCH=2*(W+S) is what a
# future two-winding construction needs: this winding's own turns then
# space out twice the normal step, leaving room for a second winding's ring
# interleaved between them.
# ---------------------------------------------------------------------------


def test_base_ind_hud_cross_pitch_param_scales_bridge_span(tmp_path):
    """Standalone base_ind_hud_cross(PITCH=2*(W+S)): the crossunder bridge
    (base_oct's LOP notch + both base_xfm_cross legs) must span exactly one
    PITCH -- from this ring's own edge (-OD/2) to where the *next* turn's
    ring would sit (-OD/2+PITCH) -- not the old hardcoded W+S. W=2, S=2,
    OD=100 -> default P=4 would land the far pad/diagonal edge at -46000;
    PITCH=8 must move it out to -40000 (-OD/2+PITCH+W) while the near pad
    slot (this ring's own edge, unrelated to the next ring) stays put."""
    W, S = 2.0, 2.0
    PITCH = 2 * (W + S)
    cell = port.base_ind_hud_cross(
        OD=100.0, W=W, S=S, OPENING=10.0, TOP_ME="9", BTM_ME="8", PITCH=PITCH
    )
    layers = write_and_parse(cell, tmp_path, "hud_pitch")
    m8, m9, v8 = port.metal_layer(8), port.metal_layer(9), port.via_layer(8)
    # the M8 crossunder diagonal spans the full new pitch, not W+S
    m8_diag = [p for p in layers[m8] if len(p) == 6]
    assert len(m8_diag) == 1
    assert rect_bbox(m8_diag[0]) == (-50000, -4405, -40000, 4405)
    # near pad slot (this ring's own edge) is unaffected by PITCH
    near_pads = [
        rect_bbox(p) for p in layers[m9]
        if len(p) == 4 and rect_bbox(p)[2] - rect_bbox(p)[0] == 2000
        and rect_bbox(p)[0] == -50000
    ]
    assert len(near_pads) == 2
    for b in near_pads:
        assert b[0] == -50000 and b[2] == -48000
    # far pad slot now sits at -OD/2+PITCH (default pitch would put it at
    # -OD/2+(W+S) = -46000)
    far_pads = [
        rect_bbox(p) for p in layers[m9]
        if len(p) == 4 and rect_bbox(p)[2] - rect_bbox(p)[0] == 2000
        and rect_bbox(p)[0] == -42000
    ]
    assert len(far_pads) == 2
    for b in far_pads:
        assert b[0] == -42000 and b[2] == -40000
    # via8 cut clusters land inside the same near/far x envelopes
    clusters = cluster_boxes([rect_bbox(p) for p in layers[v8]])
    assert len(clusters) == 2
    xs = sorted(min(b[0] for b in cl) for cl in clusters)
    assert -50000 < xs[0] < -48000
    assert -42000 < xs[1] < -40000


def test_base_ind_hud_cross_pitch_none_matches_default_w_plus_s(tmp_path):
    """PITCH=None (the documented default) must be byte-identical to the
    pre-PITCH geometry: same as calling with PITCH explicitly set to W+S."""
    W, S = 2.0, 2.0
    default = port.base_ind_hud_cross(OD=100.0, W=W, S=S, OPENING=10.0,
                                      TOP_ME="9", BTM_ME="8")
    explicit = port.base_ind_hud_cross(OD=100.0, W=W, S=S, OPENING=10.0,
                                       TOP_ME="9", BTM_ME="8", PITCH=W + S)
    a = write_and_parse(default, tmp_path, "hud_default")
    b = write_and_parse(explicit, tmp_path, "hud_explicit_wplus_s")
    assert set(a) == set(b)
    for layer in a:
        assert sorted(map(canon, a[layer])) == sorted(map(canon, b[layer]))


def test_ind_sym_pitch_param_scales_turn_radii():
    """ind_sym(PITCH=2*(W+S)) steps every turn's OD by the new pitch, not
    the hardcoded W+S: NT=3, OD=200, W=2, S=2 -> default P=4 would give
    inner=192/oct_inner=184; PITCH=8 instead gives inner=184/oct_inner=168.
    The CT tap column (trap 3: NT-odd/even + CT derivation must follow the
    same single pitch, not a second hardcoded W+S) follows suit."""
    W, S = 2.0, 2.0
    PITCH = 2 * (W + S)
    cell = port.ind_sym(
        OD=200.0, W=W, OPENING=5.0, LEAD=10.0, S=S, NT=3, TOP_ME="9",
        BTM_ME="8", PITCH=PITCH,
    )
    log = cell.instantiation_log()
    seq = [(e["function"], e["orient"]) for e in log[:3]]
    assert seq == [
        ("base_ind_hud_cross", "MY"),
        ("base_ind_hud_cross", "R0"),
        ("base_oct", "R0"),
    ]
    inner, outer, oct_inner = log[0], log[1], log[2]
    assert inner["params"]["OD"] == pytest.approx(184.0)  # OD - 2*1*PITCH
    assert outer["params"]["OD"] == pytest.approx(200.0)
    assert oct_inner["params"]["OD"] == pytest.approx(168.0)  # OD-2*(NT-1)*PITCH

    ct_cell = port.ind_sym(
        OD=200.0, W=W, OPENING=5.0, LEAD=10.0, S=S, NT=3, TOP_ME="9",
        CT_ME="7", PITCH=PITCH,
    )
    ct_log = ct_cell.instantiation_log()
    lead, tap = ct_log[-2], ct_log[-1]
    assert lead["origin_um"] == [-110.0, -1.0]  # -OD/2-LEAD
    assert lead["params"]["L"] == pytest.approx(28.0)  # LEAD+(NT-1)*PITCH+W
    assert tap["origin_um"] == [-84.0, -1.0]  # -OD/2+(NT-1)*PITCH


def test_ind_sym_pitch_param_bridge_vias_land_on_adjacent_ring(tmp_path):
    """Full ind_sym(PITCH=2*(W+S)) geometry: every crossover endpoint pad
    must sit flush inside a ring arm of the ADJACENT same-winding turn (the
    same containment property test_ind_sym_nt3_geometry pins for the
    default pitch), proving the doubled-pitch bridge really lands on the
    next turn's ring and not on stale W+S-spaced geometry."""
    W, S = 2.0, 2.0
    PITCH = 2 * (W + S)
    cell = port.ind_sym(
        OD=200.0, W=W, OPENING=5.0, LEAD=10.0, S=S, NT=3, TOP_ME="9",
        BTM_ME="8", PITCH=PITCH,
    )
    layers = write_and_parse(cell, tmp_path, "ind_sym_pitch_flush")
    m9 = port.metal_layer(9)
    ring = [
        p for p in layers[m9]
        if len(p) == 8 and rect_bbox(p)[2] - rect_bbox(p)[0] > 10000
    ]
    assert len(ring) == 12  # 4 quads x 3 ring levels (outer/inner/innermost)
    xs_all = set()
    for p in ring:
        b = rect_bbox(p)
        xs_all.add(abs(b[0]))
        xs_all.add(b[2])
    # OD/2, OD/2-PITCH, OD/2-2*PITCH (nm) -- radial intervals follow PITCH,
    # not the old hardcoded W+S (which would give 96000/92000 instead)
    assert sorted(xs_all) == [0, 84000, 92000, 100000]
    ring_region = kdb.Region(
        [kdb.Polygon([kdb.Point(x, y) for x, y in p]) for p in ring]
    )
    pads = [
        rect_bbox(p) for p in layers[m9]
        if len(p) == 4 and rect_bbox(p)[2] - rect_bbox(p)[0] == 2000
    ]
    assert len(pads) == 8  # 4 pads per hud cross x 2 crossovers
    for b in pads:
        pad_region = kdb.Region([kdb.Polygon(kdb.Box(b[0], b[1], b[2], b[3]))])
        assert (pad_region - ring_region).is_empty(), (
            f"pitch-scaled crossover pad {b} does not land flush on the "
            "adjacent same-winding ring"
        )
    assert_on_grid(layers)


# ---------------------------------------------------------------------------
# xfm_il (ticket 02 + 02b + 02c + 02d v4 final rework): same-layer
# interleaved transformer.
#
# Ring pitch 2*(W+S); P occupies outer-starting even bands (outer radius
# OD/2 - k*2*(W+S)), S occupies odd bands (outer radius OD/2-(W+S) -
# k*2*(W+S), i.e. an ind_sym winding built at OD_S = OD - 2*(W+S)).
#
# Topology (ticket 02d, v4 final -- user-confirmed 2026-07-18): P is
# ``ind_sym`` completely unmodified -- native port-right, native alternating
# bridge zigzag (``bridge_side="alternate"``, the default); S is the exact
# same construction (``_ind_ring_turns``) built in a local "opens right"
# frame with a crossunder escape (``_balun_crossunder``) attached on that
# local ``+OD_S/2`` arm, the WHOLE S sub-cell then mirrored ``MY`` so its
# port+escape land on the GLOBAL LEFT (opposite P's right) along with its
# own (mirrored) bridges. This retires 02b/02c's ``bridge_side="left"``
# same-side-stacking construction, which fixed 02b's P1-N1 short at the cost
# of a NEW bug 02d's own visual review caught: odd-NT innermost turns came
# out fully closed (0 open arms) under "left" mode -- a closed ring is a
# shorted turn (see ``test_xfm_il_no_closed_loop_holes`` below, red against
# 02c). Reverting to the reference zigzag makes both bugs constructively
# impossible again (every turn keeps exactly one bridge gap open).
#
# Dual-layer legs (ticket 02d's other core fix): each turn's crossunder is
# two independent legs (``base_ind_hud_cross``'s cross1/cross2) that used to
# share ONE layer below SL (``crossunder_sl1_only``, ticket 02) -- at the
# interleaved lattice's doubled PITCH, one winding's leg1+leg2 footprint on
# that single shared layer could collide with the OTHER winding's own
# leg1+leg2 footprint there (red against 02c: SL-2 is entirely empty, i.e.
# the "second, distinct layer" the v4 topology needs simply does not exist
# yet -- see ``test_xfm_il_dual_layer_legs_disjoint_per_layer`` below).
# ``LEG2_BTM_ME`` (see ``base_ind_hud_cross``) now routes leg1 through
# SL_ME-1 (unchanged) and leg2 through SL_ME-2 -- three distinct layers.
#
# See base_ind_hud_cross / _ind_ring_turns / ind_sym / xfm_il's own
# docstrings in pcell_inductor_port_clean.py for the full mechanism.
# ---------------------------------------------------------------------------


def _il(**kw):
    # OPENING_P/OPENING_S=14.0 (carried over from ticket 02c): the
    # outermost turn's own bridge (never mirrored, in EITHER bridge_side
    # mode -- see xfm_il's docstring) reaches bridge_y_reach=10.82 um at
    # W=4/S=2, so the channel's own OPENING must clear that reach with
    # margin -- _check_bridge_escape_clearance enforces this fail-closed.
    # The bound is unchanged by the ticket 02d bridge_side switch (it is
    # driven entirely by the fixed "R0" outermost-turn placement both
    # "left" and "alternate" modes share), so the same 14.0 default still
    # clears it.
    base = dict(OD=200.0, W=4.0, S=2.0, NT_P=3, NT_S=3, OPENING_P=14.0,
                OPENING_S=14.0, LEAD_P=20.0, LEAD_S=20.0, SL_ME="9")
    base.update(kw)
    return port.xfm_il(**base)


def _il_pri_coil(cell):
    """The ind_sym Cell backing the P winding (cell -> pri -> p_coil)."""
    return cell.insts[0].cell.insts[0].cell


def _il_sec_local(cell):
    """S's own bare-ring local Cell, pre-MY-mirror (cell -> sec -> s_local)."""
    return cell.insts[1].cell.insts[0].cell


def _region_on(subcell, met):
    reg = kdb.Region()
    for lay, pts in subcell.flat_shapes():
        if lay == port.metal_layer(met):
            reg.insert(kdb.Polygon([kdb.Point(x, y) for x, y in pts]))
    return reg


def _sl1_region(subcell, sl):
    return _region_on(subcell, sl - 1)


def _sl2_region(subcell, sl):
    return _region_on(subcell, sl - 2)


def _sl_region(subcell, sl):
    return _region_on(subcell, sl)


def _il_sec_own_and_escape_local(cell, NT_S):
    """S's own-bridge sub-cell and its escape sub-cell, split by
    construction order inside ``s_local`` -- ``_ind_ring_turns`` (own rings
    + bridges) instances first (``NT_S`` of them), ``_balun_crossunder``
    (escape) after. Pre-mirror (local) frame; see ``_il_sec_local``. Order/
    count is unaffected by the ticket 02d bridge_side switch (it changes
    per-turn ORIENTATION only, never how many instances are made)."""
    s_local = _il_sec_local(cell)
    own = port.Cell("own", "own", {})
    own.insts = s_local.insts[:NT_S]
    escape = port.Cell("esc", "esc", {})
    escape.insts = s_local.insts[NT_S:]
    return own, escape


def test_xfm_il_p_band_radii_nt3():
    """P: outer radius OD/2 - k*2*(W+S) for k=0..NT_P-1 (nm-level, via the
    same instantiation_log OD-param assertions ind_sym's own PITCH tests
    use). Unaffected by the ticket 02d bridge_side switch (radii/pitch
    derivation is topology-independent)."""
    cell = _il(NT_P=3, NT_S=3)
    log = _il_pri_coil(cell).instantiation_log()
    ods = [e["params"]["OD"] for e in log if e["function"] in
           ("base_ind_hud_cross", "base_oct")]
    # k=1 (inner loop), k=0 (outer), k=2 (innermost octagon)
    assert ods == [pytest.approx(176.0), pytest.approx(200.0),
                   pytest.approx(152.0)]
    # outer RADIUS_k = OD/2 - k*pitch (pitch=2*(W+S)=12) -> diameter_k =
    # OD - 2*k*pitch = OD - k*4*(W+S)
    for od, k in ((200.0, 0), (176.0, 1), (152.0, 2)):
        assert od == pytest.approx(200.0 - k * 4 * (4.0 + 2.0))


def test_xfm_il_s_band_radii_nt3():
    """S: outer radius OD/2-(W+S) - k*2*(W+S) -- an ind_sym-style winding at
    OD_S = OD - 2*(W+S), asserted the same way in S's own pre-rotation local
    frame (rotation only changes axis, never the radii)."""
    cell = _il(NT_P=3, NT_S=3)
    s_local = _il_sec_local(cell)
    log = s_local.instantiation_log()
    ring_ods = [e["params"]["OD"] for e in log[:3]
               if e["function"] in ("base_ind_hud_cross", "base_oct")]
    OD_S = 200.0 - 2 * (4.0 + 2.0)  # 188.0
    assert OD_S == pytest.approx(188.0)
    assert ring_ods == [pytest.approx(164.0), pytest.approx(188.0),
                        pytest.approx(140.0)]
    for od, k in ((188.0, 0), (164.0, 1), (140.0, 2)):
        assert od == pytest.approx(OD_S - k * 4 * (4.0 + 2.0))
    # relative to P: S's outermost band is inset by exactly W+S from P's
    assert 200.0 / 2 - (4.0 + 2.0) == pytest.approx(OD_S / 2)


def test_xfm_il_band_radii_nt2():
    cell = _il(NT_P=2, NT_S=2)
    p_log = _il_pri_coil(cell).instantiation_log()
    p_ods = sorted({e["params"]["OD"] for e in p_log
                    if e["function"] in ("base_ind_hud_cross", "base_oct")})
    assert p_ods == [pytest.approx(176.0), pytest.approx(200.0)]
    s_log = _il_sec_local(cell).instantiation_log()[:2]
    s_ods = sorted({e["params"]["OD"] for e in s_log
                    if e["function"] in ("base_ind_hud_cross", "base_oct")})
    assert s_ods == [pytest.approx(164.0), pytest.approx(188.0)]


def test_xfm_il_ports_semantic_names_and_emx_lines():
    cell = _il()
    names = [q["logical_name"] for q in cell.emx_ports]
    assert names == ["P1", "N1", "P2", "N2"]
    p = {q["logical_name"]: tuple(q["label_xy_um"]) for q in cell.emx_ports}
    # P1/N1: ind_sym's own formula, opens right (unrotated) -- unaffected by
    # the bridge_side mechanism entirely (only the bridge legs move, never
    # the lead pair).
    assert p["P1"] == pytest.approx((200.0 / 2 + 20.0, 14.0 + 4.0 / 2))
    assert p["N1"] == pytest.approx((200.0 / 2 + 20.0, -(14.0 + 4.0 / 2)))
    # P2/N2: _balun_crossunder's tip, MIRRORED (ticket 02b, MY not R90) --
    # exits "left" near x=0, y unchanged by the mirror. Escape geometry
    # itself is untouched by the ticket 02c->02d bridge-side switch.
    X0 = 200.0 / 2 + 2.0  # x_out + OD_out/2 + g(=S)
    tip = X0 + 20.0  # + LEAD_S
    assert p["P2"] == pytest.approx((-tip, 14.0 + 4.0 / 2))
    assert p["N2"] == pytest.approx((-tip, -(14.0 + 4.0 / 2)))
    assert p["P1"][0] > 0 and p["N1"][0] > 0, "P ports must exit right"
    assert p["P2"][0] < 0 and p["N2"][0] < 0, "S ports must exit left"
    assert port.emx_port_lines(cell.emx_ports) == [
        "-p N1=N1", "-p N2=N2", "-p P1=P1", "-p P2=P2"]


def test_xfm_il_port_order_and_ground_fixture():
    cell = _il(port_order=["A1", "A2", "A3", "A4"])
    assert [q["name"] for q in cell.emx_ports] == ["A1", "A2", "A3", "A4"]
    assert [q["logical_name"] for q in cell.emx_ports] == [
        "P1", "N1", "P2", "N2"]
    gf = _il(ground_fixture=port.GroundFixtureConfig(
        inner_margin_um=5.0, ring_width_um=3.0, stub_width_um=4.0,
        stub_length_um=3.0, stub_chamfer_um=1.0))
    refs = {q["reference"] for q in gf.emx_ports}
    assert refs == {f"G{i:02d}" for i in range(1, 5)}


def test_xfm_il_nt_p_below_2_fails_closed():
    with pytest.raises(port.PortError, match="NT_P"):
        _il(NT_P=1, NT_S=1)


def test_xfm_il_nt_mismatch_fails_closed():
    """Ticket 03c (user directive): NT_P must EQUAL NT_S -- tightened from
    ticket 01's original |NT_P-NT_S|<=1 (a differential center-tapped
    winding needs symmetric turn counts on both sides). Any mismatch,
    including the old |diff|==1 boundary that used to be legal, must now
    fail closed."""
    with pytest.raises(port.PortError, match="NT_P.*NT_S"):
        _il(NT_P=4, NT_S=2)
    with pytest.raises(port.PortError, match="NT_P.*NT_S"):
        _il(NT_P=2, NT_S=4)
    with pytest.raises(port.PortError, match="NT_P.*NT_S"):
        _il(NT_P=3, NT_S=2)  # the old |diff|==1 boundary, legal pre-03c
    with pytest.raises(port.PortError, match="NT_P.*NT_S"):
        _il(NT_P=2, NT_S=3)


def test_xfm_il_sl_metal_budget_too_low_fails_closed():
    """Ticket 02d: SL_ME needs two metals below it (SL_ME-1 for leg1,
    SL_ME-2 for leg2). SL_ME="2" only has one metal (M1) below it."""
    with pytest.raises(port.PortError, match="two metals below"):
        _il(SL_ME="2")


def test_xfm_il_sl_metal_budget_exact_floor_builds():
    """SL_ME="3" (M1, M2 below) is the exact floor and must build."""
    cell = _il(SL_ME="3")
    assert cell.emx_ports


def test_xfm_il_bridge_escape_left_axis_derives_minimum_outer_shift():
    """P's outer bridge moves inward instead of rejecting a fixable gap."""
    cell = _il(NT_P=3, NT_S=3, OPENING_P=20.0, OPENING_S=11.81)

    assert cell.params["bridge_escape_offset_primary_um"] == pytest.approx(0.01)
    assert cell.params["bridge_escape_offset_secondary_um"] == 0.0


def test_xfm_il_bridge_escape_clearance_guard_left_axis_boundary_builds():
    """Same configuration at the exact boundary (OPENING_S=11.82) must
    build cleanly -- the guard is a >= (not a strict >) bound."""
    cell = _il(NT_P=3, NT_S=3, OPENING_P=20.0, OPENING_S=11.82)
    assert cell.emx_ports


def test_xfm_il_bridge_escape_right_axis_derives_minimum_outer_shift():
    """S's outer bridge uses the symmetric adaptive escape offset."""
    cell = _il(NT_P=3, NT_S=3, OPENING_P=11.81, OPENING_S=20.0)

    assert cell.params["bridge_escape_offset_primary_um"] == 0.0
    assert cell.params["bridge_escape_offset_secondary_um"] == pytest.approx(0.01)


def test_xfm_il_bridge_escape_clearance_guard_right_axis_boundary_builds():
    cell = _il(NT_P=3, NT_S=3, OPENING_P=11.82, OPENING_S=20.0)
    assert cell.emx_ports


def test_xfm_il_opening_p_over_bound_fails_closed():
    # OD=100, W=5 -> max_opening = 18.625 (known bound, shared with ind_sym).
    with pytest.raises(port.PortError, match="OPENING"):
        _il(OD=100.0, W=5.0, S=2.0, NT_P=2, NT_S=2, OPENING_P=20.0,
            OPENING_S=8.0, LEAD_P=20.0, LEAD_S=20.0)


def test_xfm_il_opening_s_over_bound_fails_closed():
    OD, W, S = 100.0, 5.0, 2.0
    OD_S = OD - 2 * (W + S)
    mx = port.max_opening(OD_S, W)
    with pytest.raises(port.PortError, match="OPENING"):
        _il(OD=OD, W=W, S=S, NT_P=2, NT_S=2, OPENING_P=8.0,
            OPENING_S=mx + 2.0, LEAD_P=20.0, LEAD_S=20.0)


def test_xfm_il_fit_inequality_rejects_crushed_innermost_p_turn():
    """OD=52, W=S=2, PITCH=8, NT_P=NT_S=3 (ticket 03c: equal turns only,
    so the pre-03c isolation trick of exempting S via NT_S=2 no longer
    exists): the innermost facing-bearing P turn (OD=52-2*2*8=20) can
    host at most max_opening(20,2)=3.3 um, but the crossunder facing
    opening cross_endpoint_offset(2, 8-2)=4.405 um needs more -- the
    octagon opening leg would detach from the crossunder (ticket 01
    comment (2)). At equal turns S's own effective OD (OD-pitch=44) is
    ALWAYS smaller than P's, so S crushes here too (inner OD 12,
    max_opening 1.645 -- see the S-turn test below) -- but P's own
    ``_check_winding_fit`` call runs FIRST in xfm_il's guard chain, so
    this specifically pins THAT check firing (message names "xfm_il P"),
    not merely that some check eventually fails."""
    with pytest.raises(port.PortError, match="xfm_il P.*innermost"):
        _il(OD=52.0, W=2.0, S=2.0, NT_P=3, NT_S=3, OPENING_P=3.0,
            OPENING_S=3.0, LEAD_P=10.0, LEAD_S=10.0)


def test_xfm_il_fit_inequality_rejects_crushed_innermost_s_turn():
    """Same crushed-innermost failure mode, isolated to S only: OD=60 (not
    52) at equal NT_P=NT_S=3 keeps P's own inner turn comfortably clear
    (inner OD 28, max_opening 4.96 > facing 4.405) while S's smaller
    effective OD (OD-pitch=52, inner OD 20, max_opening 3.3 < facing
    4.405) still crushes -- found by sweeping OD in 2 um steps from the
    original 52 (both crushed) until only S's own check fires (verified
    directly: at OD=52/54/56 both crush, OD=58+ only S does)."""
    with pytest.raises(port.PortError, match="xfm_il S.*innermost"):
        _il(OD=60.0, W=2.0, S=2.0, NT_P=3, NT_S=3, OPENING_P=3.0,
            OPENING_S=3.0, LEAD_P=10.0, LEAD_S=10.0)


def test_xfm_il_fit_inequality_nt2_never_flags():
    """NT<=2 never draws a facing-bearing turn (the sole crossover uses the
    caller's own OPENING on the outer ring only) -- the fit check must be a
    silent no-op there. OD=52 is the exact OD the P/S-side fit-collapse
    tests above use at NT=3 to force a rejection; at NT=2 (same OD, W, S)
    it must build cleanly, proving the guard really is NT-gated and not
    just "OD=52 always fails". OPENING=8.0 (not the crushed-innermost
    tests' 3.0): unrelated to _check_winding_fit, but must still clear the
    bridge/escape guard (reach=6.405 um at W=S=2.0 here, OPENING=3.0 would
    trip THAT guard first and defeat the point of this test, which is
    specifically NT-gating)."""
    cell = _il(OD=52.0, W=2.0, S=2.0, NT_P=2, NT_S=2, OPENING_P=8.0,
              OPENING_S=8.0, LEAD_P=10.0, LEAD_S=10.0)
    assert cell.emx_ports


def test_xfm_il_constructed_same_layer_collision_fails_closed():
    """S=-0.5 (a deliberately negative shared spacing, reachable through the
    public signature alone -- xfm_il exposes no override for the
    OD_S=OD-2*(W+S) derivation) shrinks the P-inner/S-outer gap to -0.5 um:
    a genuine 0.5 um same-layer overlap between the two nets. The
    unconditional _xfm_net_short gate must catch it (M13 ticket 05's
    layer-complete short gate, shared with every other xfm_* device, now
    covering SL, SL-1 AND SL-2)."""
    with pytest.raises(port.PortError, match="short"):
        _il(S=-0.5, NT_P=2, NT_S=2, LEAD_P=20.0, LEAD_S=20.0)


def test_xfm_il_n28_sl_m9_process_mode_builds_and_on_grid(tmp_path):
    """n28-rules-slim (user directive 2026-07-19): SL_ME="9" needs leg2 on
    M7, so leg2's own via stack crosses VIA7 (M7<->M8) -- VIA7 has complete
    via_primitives geometry, so this now builds (previously fell closed
    citing the retired via_restrictions/IND.R.1 policy gate; ``vias()`` no
    longer fails closed on it, see its own docstring)."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = _il(OD=200.0, W=5.0, S=2.5, NT_P=3, NT_S=3, OPENING_P=18.0,
              OPENING_S=18.0, LEAD_P=20.0, LEAD_S=20.0, SL_ME="9",
              process=ctx)
    layers = write_and_parse(cell, tmp_path, "xfm_il_n28_m9")
    assert port.process_metal_layer(ctx, 9) in layers
    assert port.process_metal_layer(ctx, 8) in layers
    assert port.process_metal_layer(ctx, 7) in layers
    assert port.process_via_layer(ctx, 8) in layers
    assert port.process_via_layer(ctx, 7) in layers
    assert_on_grid(layers)


def test_xfm_il_n28_sl_m10_process_mode_builds_and_on_grid(tmp_path):
    """SL_ME="10": legs land on M9 (leg1) and M8 (leg2) -- both unrestricted
    passive-region vias, so this must build (no CT here; ticket 03)."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = _il(OD=200.0, W=5.0, S=2.5, NT_P=3, NT_S=3, OPENING_P=18.0,
              OPENING_S=18.0, LEAD_P=20.0, LEAD_S=20.0, SL_ME="10",
              process=ctx)
    layers = write_and_parse(cell, tmp_path, "xfm_il_n28_m10")
    assert port.process_metal_layer(ctx, 10) in layers
    assert port.process_metal_layer(ctx, 9) in layers
    assert port.process_metal_layer(ctx, 8) in layers
    assert port.process_via_layer(ctx, 9) in layers
    assert port.process_via_layer(ctx, 8) in layers
    assert_on_grid(layers)


def test_xfm_il_n28_right_bridge_layers_meet_own_spacing_rules(tmp_path):
    """The interleaved right-side bridge diagonals must clear each other.

    The representative NT=3 sample places alternating bridge legs on M9
    (SL-1) and M8 (SL-2).  Non-overlap alone is insufficient: on each layer,
    the two diagonal conductors must meet that conductor's N28 min-space
    rule after grid snapping.
    """
    ctx = port.process_rule_context("n28_1p10m")
    cell = _il(
        OD=200.0,
        W=5.0,
        S=2.5,
        NT_P=3,
        NT_S=3,
        OPENING_P=18.0,
        OPENING_S=18.0,
        LEAD_P=20.0,
        LEAD_S=20.0,
        SL_ME="10",
        process=ctx,
    )
    findings = {
        conductor: _min_space_findings(
            cell,
            tmp_path,
            f"xfm_il_right_bridge_{conductor.lower()}",
            _drawing_layer(ctx, conductor),
            _min_space_um(ctx, conductor),
        )
        for conductor in ("M9", "M8")
    }
    assert findings == {"M9": 0, "M8": 0}


def _diag_region_on_layer(subcell, layer):
    region = kdb.Region()
    for actual_layer, pts in subcell.flat_shapes():
        if actual_layer == layer and len(pts) > 4:
            region.insert(kdb.Polygon([kdb.Point(x, y) for x, y in pts]))
    return region.merged()


@pytest.mark.parametrize(
    ("width", "spacing"),
    ((3.0, 2.0), (5.0, 2.5), (8.0, 3.5)),
)
def test_xfm_il_reference_bridge_diagonal_spacing_tracks_w_and_s(
    width, spacing
):
    """The stagger is derived from both W and S, even without a profile.

    ``S`` keeps its public meaning as the desired edge-to-edge spacing.  On
    each bridge layer the P/S 45-degree diagonal bodies must therefore clear
    by at least S for narrow, representative, and wide conductors alike.
    """
    cell = _il(
        OD=400.0,
        W=width,
        S=spacing,
        OPENING_P=50.0,
        OPENING_S=50.0,
        SL_ME="9",
    )
    pri, sec = cell.insts[0].cell, cell.insts[1].cell
    for met in (8, 7):
        p_diags = _diag_region_on_layer(pri, port.metal_layer(met))
        s_diags = _diag_region_on_layer(sec, port.metal_layer(met))
        findings = p_diags.separation_check(
            s_diags, _nm(spacing), False, kdb.Metrics.Euclidian
        )
        assert findings.count() == 0, (
            f"W={width} S={spacing}: M{met} bridge diagonals do not "
            f"preserve the requested S spacing"
        )


def test_xfm_il_right_bridge_lane_offsets_are_equal_and_opposite():
    """The colliding even/odd bridge pair splits the offset symmetrically.

    For SL=M10, the odd-band P bridge moves down on M9 and up on M8; the
    even-band S bridge does the exact opposite.  Their per-layer diagonal
    centres must remain centred on the original y=0 bridge corridor, and the
    common offset magnitude must be identical on both bridge layers.
    """
    cell = _il(
        OD=200.0,
        W=5.0,
        S=2.5,
        OPENING_P=18.0,
        OPENING_S=18.0,
        SL_ME="10",
    )
    pri, sec = cell.insts[0].cell, cell.insts[1].cell

    def right_diag_center_y(subcell, met):
        boxes = []
        for layer, pts in subcell.flat_shapes():
            if layer != port.metal_layer(met) or len(pts) <= 4:
                continue
            box = kdb.Polygon([kdb.Point(x, y) for x, y in pts]).bbox()
            if box.left > 0:
                boxes.append(box)
        assert len(boxes) == 1
        return (boxes[0].bottom + boxes[0].top) // 2

    centres = {
        met: (right_diag_center_y(pri, met), right_diag_center_y(sec, met))
        for met in (9, 8)
    }
    assert centres[9][0] < 0 < centres[9][1]
    assert centres[8][1] < 0 < centres[8][0]
    assert centres[9][0] == -centres[9][1]
    assert centres[8][0] == -centres[8][1]
    assert abs(centres[9][0]) == abs(centres[8][0])


def test_xfm_il_n28_sl_ap_process_mode_builds_and_on_grid(tmp_path):
    """SL_ME="AP": legs land on M10 (leg1) and M9 (leg2) -- the AP-body
    ticket 02d also requires to build cleanly (no CT; ticket 03)."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = _il(OD=200.0, W=5.0, S=2.5, NT_P=3, NT_S=3, OPENING_P=18.0,
              OPENING_S=18.0, LEAD_P=20.0, LEAD_S=20.0, SL_ME="AP",
              process=ctx)
    layers = write_and_parse(cell, tmp_path, "xfm_il_n28_ap")
    assert port.process_metal_layer(ctx, 11) in layers  # AP
    assert port.process_metal_layer(ctx, 10) in layers
    assert port.process_metal_layer(ctx, 9) in layers
    assert port.process_via_layer(ctx, 10) in layers
    assert port.process_via_layer(ctx, 9) in layers
    assert_on_grid(layers)


def test_xfm_il_p1_lead_connected_to_p_outer_ring(tmp_path):
    """Connectivity positive control: the P1 lead tip and a point on the P
    outer ring metal land on the same connected SL component."""
    cell = _il(NT_P=3, NT_S=3)
    reg = _merged_metal(cell, 9, tmp_path, "xfm_il_conn")
    p1x, p1y = 200.0 / 2 + 20.0, 14.0 + 4.0 / 2  # OPENING_P=14.0
    assert _same_component(reg, p1x, p1y, 0.0, 200.0 / 2 - 4.0 / 2)


def test_xfm_il_p_outer_open_arm_has_no_sl1_bridge_pad():
    """Parallel-collapse probe (ticket 02c originally; still a valid
    regression pin under the ticket 02d native-alternate topology, since
    the OUTERMOST turn of either winding is NEVER mirrored under either
    bridge_side mode -- see _ind_ring_turns's docstring): the region right
    around P's OUTER ring's OWN open arm (physical +OD/2, where the P1/N1
    lead pair attaches) must contain NO SL-1 bridge pad/via at all -- P's
    outermost bridge must instead show up on the OPPOSITE (left, -OD/2)
    arm. A 02b-style co-located bridge+lead-pair arm would fail this
    probe (P1-N1 direct short, not a winding)."""
    cell = _il(NT_P=3, NT_S=3)
    OD = 200.0
    p_sl1 = _sl1_region(_il_pri_coil(cell), 9)
    right_arm_nm = int(OD / 2 * 1000)
    right_probe = kdb.Region(
        kdb.Box(right_arm_nm - 3000, -6000, right_arm_nm + 3000, 6000))
    hit = p_sl1 & right_probe
    assert hit.is_empty(), (
        "P's own lead-pair (open) arm has an SL-1 bridge pad -- this is "
        "exactly the 02b parallel-collapse signature (P1-N1 direct short)"
    )
    # Positive control: P's outermost bridge really did land on the
    # OPPOSITE (left) arm, not vanish -- the probe above isn't vacuously
    # true.
    left_probe = kdb.Region(
        kdb.Box(-right_arm_nm - 3000, -6000, -right_arm_nm + 3000, 6000))
    assert not (p_sl1 & left_probe).is_empty(), (
        "P's outer-ring bridge did not land on the opposite (left) arm"
    )


def test_xfm_il_s_escape_crosses_under_p_outer_band_only():
    """S's SL-1 escape crossunder overlaps P's OUTERMOST band in
    xy-projection (that is the one band standing between S's outermost band
    and open space) and the two SL rings stay disjoint. S's SL-2 never
    carries the escape at all (_balun_crossunder only ever touches SL and
    SL-1 -- it is unaffected by the ticket 02d dual-layer-leg change, which
    only moves each winding's OWN inter-turn bridge leg2)."""
    cell = _il(NT_P=3, NT_S=3)
    pri, sec = cell.insts[0].cell, cell.insts[1].cell
    sl = 9
    p_ring_polys = [pts for lay, pts in pri.flat_shapes()
                    if lay == port.metal_layer(sl) and len(pts) == 8]
    outer_radius_nm = 200.0 / 2 * 1000
    outer = [pts for pts in p_ring_polys
            if max(abs(rect_bbox(pts)[0]), abs(rect_bbox(pts)[2]))
            >= outer_radius_nm - 1000]  # P's OD=200 outer band quadrants
    assert outer
    p_outer_region = kdb.Region(
        [kdb.Polygon([kdb.Point(x, y) for x, y in p]) for p in outer])
    s_sl1 = _sl1_region(sec, sl)
    s_sl2 = _sl2_region(sec, sl)
    assert not (p_outer_region & s_sl1).is_empty(), (
        "S's escape must pass under P's outer band on SL-1"
    )
    assert (p_outer_region & s_sl2).is_empty(), (
        "S's escape must never touch SL-2 -- only S's own inter-turn "
        "bridges use leg2"
    )
    p_ring = _sl_region(pri, sl)
    s_ring = _sl_region(sec, sl)
    assert (p_ring & s_ring).is_empty()


def test_xfm_il_s_escape_and_own_bridges_disjoint_in_local_frame():
    """S's escape crossunder and S's own inter-turn bridges are built as two
    separate groups of instances inside s_local (_ind_ring_turns first,
    NT_S instances -- rings + own bridges on BOTH SL-1 and SL-2, ticket
    02d's dual-layer legs -- then _balun_crossunder, the escape, SL-1
    only). Ticket 02d's native alternate zigzag no longer confines S's own
    bridges to one local arm (unlike 02c's "left" mode), so this checks
    disjointness directly rather than via a single-arm bounding-box
    assumption -- on EACH layer the escape ever touches (SL-1; it never
    reaches SL-2 at all)."""
    cell = _il(NT_P=3, NT_S=3)
    own_local, escape_local = _il_sec_own_and_escape_local(cell, NT_S=3)
    own_sl1 = _sl1_region(own_local, 9)
    own_sl2 = _sl2_region(own_local, 9)
    esc_sl1 = _sl1_region(escape_local, 9)
    esc_sl2 = _sl2_region(escape_local, 9)
    assert not own_sl1.is_empty()
    assert not own_sl2.is_empty(), "S's own bridges must use leg2 (SL-2) too"
    assert not esc_sl1.is_empty()
    assert esc_sl2.is_empty(), "S's escape must never touch SL-2"
    assert (own_sl1 & esc_sl1).is_empty(), (
        "S's escape and S's own bridges must never overlap on SL-1"
    )


def test_xfm_il_p_full_winding_one_connected_component_sl_sl1_sl2():
    """Connectivity control (ticket 02b handoff, updated for ticket 02d's
    dual-layer legs): P's own crossunder is now split across TWO layers
    below SL (leg1 on SL-1, leg2 on SL-2 -- each connects one of the turn's
    two crossover tips independently, see base_ind_hud_cross's docstring),
    so BOTH must be included for the whole winding to read back as ONE
    connected polygon -- SL merged with SL-1 ALONE splits into multiple
    pieces now (red against this test's premise before the fix: SL-2 does
    not exist yet, so "SL+SL-1+SL-2" degenerates to "SL+SL-1", which is
    exactly the stale claim this test supersedes). Checked for both NT_P
    parities (ticket 03c: NT_P==NT_S is now mandatory, so both orderings
    collapse to just the parity sweep)."""
    for NT_P, NT_S in ((2, 2), (3, 3), (4, 4), (5, 5)):
        cell = _il(NT_P=NT_P, NT_S=NT_S)
        pri = cell.insts[0].cell
        reg = kdb.Region()
        for lay, pts in pri.flat_shapes():
            if lay in (port.metal_layer(9), port.metal_layer(8),
                      port.metal_layer(7)):
                reg.insert(kdb.Polygon([kdb.Point(x, y) for x, y in pts]))
        reg.merge()
        comps = list(reg.each())
        assert len(comps) == 1, (
            f"NT_P={NT_P} NT_S={NT_S}: P winding split into "
            f"{len(comps)} disconnected SL/SL-1/SL-2 components"
        )


def test_xfm_il_s_full_winding_one_connected_component_sl_sl1_sl2():
    """Same connectivity control as above, for S (escape + own bridges, all
    landing on the same local arm before the top-level MY mirror). Ticket
    03c: NT_P==NT_S is now mandatory."""
    for NT_P, NT_S in ((2, 2), (3, 3), (4, 4), (5, 5)):
        cell = _il(NT_P=NT_P, NT_S=NT_S)
        sec = cell.insts[1].cell
        reg = kdb.Region()
        for lay, pts in sec.flat_shapes():
            if lay in (port.metal_layer(9), port.metal_layer(8),
                      port.metal_layer(7)):
                reg.insert(kdb.Polygon([kdb.Point(x, y) for x, y in pts]))
        reg.merge()
        comps = list(reg.each())
        assert len(comps) == 1, (
            f"NT_P={NT_P} NT_S={NT_S}: S winding split into "
            f"{len(comps)} disconnected SL/SL-1/SL-2 components"
        )


# ---------------------------------------------------------------------------
# Ticket 02d's four topology invariants (spec.md "拓扑不变量测试" -- these
# permanently pin down the three topology-level bugs the user found across
# 02/02b/02c from sample-image review: same-side common-arm parallel
# collapse (02b), closed-loop shorted turns (02c's "left" mode fallout,
# caught here as invariant 2), and same-layer crossover shorts (02's
# original crossunder_sl1_only gap, caught here as invariant 3/4). Verified
# red against the pre-02d (02c) state before the LEG2_BTM_ME/bridge_side
# fix landed:
#   - invariant 2 (no closed loop): at NT_P=NT_S=3 under 02c's
#     bridge_side="left", P's and S's innermost (odd-NT) turn was a FULLY
#     CLOSED ring (holes()==1) -- a shorted turn, not an open winding.
#   - invariant 3 (dual-layer legs): SL-2 (M7 at SL_ME="9") was completely
#     EMPTY for both P and S under 02c (area=0.0 um^2) -- leg2 had nowhere
#     to go but SL-1, defeating the "own layer per leg" premise entirely.
# invariants 1 and 4 already held under 02c (that topology's own accepted
# criteria -- see the 02c ticket's Comments); they are re-verified here
# unchanged so a future regression on ANY of the four is caught by this one
# block.
# ---------------------------------------------------------------------------


_IL_NT_COMBOS = ((2, 2), (3, 3), (4, 4), (5, 5))  # ticket 03c: NT_P==NT_S
# is now a hard guard (user directive), so every combo here must be equal;
# still covers both parities and a range of turn counts like the original
# ((2,2),(3,3),(3,2),(2,3)) did.


def _sl_component_index(cell, sl, x_um, y_um):
    """Index into the merged SL-only region's connected components that
    (x_um, y_um) lands on, or None. Layer-(SL-1)/(SL-2) shapes are never
    inserted, so this is exactly "SL merged with every bridge layer
    removed"."""
    reg = _sl_region(cell, sl)
    reg.merge()
    comps = list(reg.each())
    pt = kdb.Point(_nm(x_um), _nm(y_um))
    probe = kdb.Region(kdb.Box(pt - kdb.Vector(1, 1), pt + kdb.Vector(1, 1)))
    found = None
    for i, c in enumerate(comps):
        if not (kdb.Region(c) & probe).is_empty():
            found = i
    return len(comps), found


def test_xfm_il_invariant1_series_p1_n1_p2_n2_different_sl_components():
    """Invariant 1 (spec.md): remove every bridge layer (SL-1 AND SL-2) and
    merge what is left on SL alone -- a genuinely SERIES winding must fall
    apart into multiple disconnected arcs, with P1/N1 (resp. P2/N2) landing
    on DIFFERENT ones (never the SAME closed loop, which would mean a
    parallel/short collapse, not a series chain).

    The exact component count under ticket 02d's native alternate zigzag is
    NOT the 02c "left"-mode formula (NT_P+1 / NT_S+3) -- empirically
    confirmed (>=7 NT_P/NT_S combinations) to be P: 2*NT_P-1, S: 2*NT_S+1
    (the ring/bridge kernel alone contributes 2*NT_S-1 components, exactly
    like P; the escape crossunder folds ITS two separate SL pieces -- P2's
    lead + near pad, N2's lead + near pad, which never physically merge
    onto the ring's own SL metal, only bridge to it via SL-1 -- into a net
    +2 on top of that kernel count, minus 0 shared pieces)."""
    for NT_P, NT_S in _IL_NT_COMBOS:
        cell = _il(NT_P=NT_P, NT_S=NT_S)
        pri, sec = cell.insts[0].cell, cell.insts[1].cell
        p = {q["logical_name"]: tuple(q["label_xy_um"]) for q in cell.emx_ports}

        n_p, p1_idx = _sl_component_index(pri, 9, *p["P1"])
        _, n1_idx = _sl_component_index(pri, 9, *p["N1"])
        assert n_p == 2 * NT_P - 1, (
            f"NT_P={NT_P} NT_S={NT_S}: P has {n_p} SL-only components, "
            f"expected 2*NT_P-1={2 * NT_P - 1}"
        )
        assert p1_idx is not None and n1_idx is not None
        assert p1_idx != n1_idx, (
            f"NT_P={NT_P} NT_S={NT_S}: P1 and N1 land on the SAME SL-only "
            "component -- the winding is a direct short/parallel collapse, "
            "not a series chain"
        )

        n_s, p2_idx = _sl_component_index(sec, 9, *p["P2"])
        _, n2_idx = _sl_component_index(sec, 9, *p["N2"])
        assert n_s == 2 * NT_S + 1, (
            f"NT_P={NT_P} NT_S={NT_S}: S has {n_s} SL-only components, "
            f"expected 2*NT_S+1={2 * NT_S + 1}"
        )
        assert p2_idx is not None and n2_idx is not None
        assert p2_idx != n2_idx, (
            f"NT_P={NT_P} NT_S={NT_S}: P2 and N2 land on the SAME SL-only "
            "component -- the winding is a direct short/parallel collapse, "
            "not a series chain"
        )


def test_xfm_il_invariant2_no_closed_loop_holes():
    """Invariant 2 (spec.md): every SL-only connected component of either
    winding must have ZERO holes -- a closed (donut-shaped) ring is a
    shorted turn, not an open winding segment. RED against 02c's
    bridge_side="left": with odd NT, the innermost turn was closed on BOTH
    arms (``_ind_ring_turns``'s ``"left"`` branch: ``lop, rop = (0.0,
    0.0)``, since it has no bridge of its own and nothing lands on its
    facing arm under fixed-side stacking) -- confirmed empirically
    (holes()==1 for P's and S's innermost component at NT_P=NT_S=3).
    Ticket 02d's native alternate zigzag instead opens that arm
    (``lop, rop = (0.0, facing)``), so no turn is ever fully closed."""
    for NT_P, NT_S in _IL_NT_COMBOS:
        cell = _il(NT_P=NT_P, NT_S=NT_S)
        for label, sub in (("P", cell.insts[0].cell), ("S", cell.insts[1].cell)):
            reg = _sl_region(sub, 9)
            reg.merge()
            for i, comp in enumerate(reg.each()):
                assert comp.holes() == 0, (
                    f"NT_P={NT_P} NT_S={NT_S}: {label} SL-only component "
                    f"{i} has {comp.holes()} hole(s) -- a closed ring is a "
                    "shorted turn"
                )


def test_xfm_il_invariant3_dual_layer_legs_disjoint_per_layer():
    """Invariant 3 (spec.md): each turn's crossunder legs live on distinct
    layers (leg1 SL-1, leg2 SL-2 -- see base_ind_hud_cross's docstring), and
    the "chained X" guard: on EACH of those layers, P's own bridge
    footprint and S's own bridge footprint must never intersect (P and S
    interleave radially, so a bridge from one winding can sit right next to
    -- but must never touch -- a bridge from the other, on the SAME layer).
    RED against 02c (pre-02d): SL-2 (M7 at SL_ME="9") was entirely EMPTY
    (area=0.0 um^2) for both windings, so this invariant was structurally
    unsatisfiable (there was only ever ONE bridge layer, SL-1, shared by
    both legs of both windings -- the union of the "chained X" fail-closed
    check below is over an empty SL-2 set, i.e. vacuously true there but
    genuinely failing on "SL-2 nonempty")."""
    for NT_P, NT_S in _IL_NT_COMBOS:
        cell = _il(NT_P=NT_P, NT_S=NT_S)
        pri, sec = cell.insts[0].cell, cell.insts[1].cell
        sl = 9
        p_sl1, s_sl1 = _sl1_region(pri, sl), _sl1_region(sec, sl)
        p_sl2, s_sl2 = _sl2_region(pri, sl), _sl2_region(sec, sl)
        assert not p_sl1.is_empty() and not s_sl1.is_empty()
        assert not p_sl2.is_empty(), (
            f"NT_P={NT_P} NT_S={NT_S}: P has no leg2 (SL-2) metal at all"
        )
        assert not s_sl2.is_empty(), (
            f"NT_P={NT_P} NT_S={NT_S}: S has no leg2 (SL-2) metal at all"
        )
        assert (p_sl1 & s_sl1).is_empty(), (
            f"NT_P={NT_P} NT_S={NT_S}: P's and S's leg1 (SL-1) footprints "
            "intersect -- chained-X collision"
        )
        assert (p_sl2 & s_sl2).is_empty(), (
            f"NT_P={NT_P} NT_S={NT_S}: P's and S's leg2 (SL-2) footprints "
            "intersect -- chained-X collision"
        )


def test_xfm_il_invariant4_via_aware_net_count_is_two(tmp_path):
    """Invariant 4 (spec.md): a full via-aware klayout LayoutToNetlist
    connectivity extraction (connect set = SL, SL-1, SL-2 metal + the two
    via classes between them -- via(SL-1) between SL-1/SL, via(SL-2)
    between SL-2/SL-1 -- each metal self-connects, each via class connects
    to the metal immediately above AND below it) must find EXACTLY 2 nets
    for the whole device (P and S, each a single coherent winding, never
    shorted to each other and never split into extra floating pieces).
    Already held under 02c (that topology's own accepted criterion -- see
    the 02c ticket's Comments); re-verified unchanged here so a future
    regression on the electrical topology is caught alongside the other
    three invariants."""
    for NT_P, NT_S in _IL_NT_COMBOS:
        cell = _il(NT_P=NT_P, NT_S=NT_S)
        sl = 9
        gds = tmp_path / f"xfm_il_l2n_{NT_P}_{NT_S}.gds"
        port.write_gds(cell, gds)
        ly = kdb.Layout()
        ly.read(str(gds))
        top = ly.top_cell()
        top.flatten(True)

        def region_for(layer_tuple, ly=ly, top=top):
            li = ly.find_layer(*layer_tuple)
            if li is None:
                return kdb.Region()
            return kdb.Region(top.begin_shapes_rec(li))

        r_sl = region_for(port.metal_layer(sl))
        r_sl1 = region_for(port.metal_layer(sl - 1))
        r_sl2 = region_for(port.metal_layer(sl - 2))
        r_via1 = region_for(port.via_layer(sl - 1))
        r_via2 = region_for(port.via_layer(sl - 2))
        assert not r_sl.is_empty() and not r_sl1.is_empty()
        assert not r_sl2.is_empty(), (
            f"NT_P={NT_P} NT_S={NT_S}: no SL-2 metal in the written GDS"
        )
        assert not r_via1.is_empty() and not r_via2.is_empty()

        l2n = kdb.LayoutToNetlist(top.name, ly.dbu)
        l2n.register(r_sl, "SL")
        l2n.register(r_sl1, "SL1")
        l2n.register(r_sl2, "SL2")
        l2n.register(r_via1, "VIA_SL1")
        l2n.register(r_via2, "VIA_SL2")
        l2n.connect(r_sl)
        l2n.connect(r_sl1)
        l2n.connect(r_sl2)
        l2n.connect(r_via1)
        l2n.connect(r_via2)
        l2n.connect(r_sl, r_via1)
        l2n.connect(r_sl1, r_via1)
        l2n.connect(r_sl1, r_via2)
        l2n.connect(r_sl2, r_via2)
        l2n.extract_netlist()
        circuit = l2n.netlist().circuit_by_name(top.name)
        nets = list(circuit.each_net())
        assert len(nets) == 2, (
            f"NT_P={NT_P} NT_S={NT_S}: via-aware extraction found "
            f"{len(nets)} nets, expected exactly 2 (P and S)"
        )


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# xfm_il ticket 03c: NT_P==NT_S (equal turns) + CT exact midpoint with
# up/down direction, superseding ticket 03b's tangential offset after a
# follow-up Virtuoso review (see spec/issue
# 03c-equal-turns-ct-upward.md). CT metal legality downward is
# UNCHANGED (CT<=SL_ME-3); upward has no floor. Both defaults None ->
# byte-identical is UNCHANGED from ticket 03.
# ---------------------------------------------------------------------------


def test_xfm_il_ct_none_is_byte_identical(tmp_path):
    """CT_P_ME/CT_S_ME both default None -> every new ticket 03/03b/03c
    code path is gated behind `is not None` and adds nothing (no shape,
    no label, no params key). write_gds is deterministic (no timestamps
    -- see its own docstring), so the written bytes for a CT-less build
    must be identical whether CT_P_ME/CT_S_ME are omitted entirely or
    passed explicitly as None, across every SL_ME body this ticket
    touches."""
    for sl_me in ("9", "10", "AP"):
        omitted = _il(SL_ME=sl_me)
        explicit = _il(SL_ME=sl_me, CT_P_ME=None, CT_S_ME=None)
        # same stem in two directories: write_gds names the top cell after
        # the file stem, so byte-comparisons must hold the stem constant
        (tmp_path / "omitted").mkdir(exist_ok=True)
        (tmp_path / "explicit").mkdir(exist_ok=True)
        gds_a = tmp_path / "omitted" / f"ct_none_{sl_me}.gds"
        gds_b = tmp_path / "explicit" / f"ct_none_{sl_me}.gds"
        port.write_gds(omitted, gds_a)
        port.write_gds(explicit, gds_b)
        assert gds_a.read_bytes() == gds_b.read_bytes(), (
            f"SL_ME={sl_me}: omitted vs explicit-None CT params produced "
            "different GDS bytes"
        )
        assert "CT_P_ME" not in omitted.params
        assert "CT_S_ME" not in omitted.params
        assert len(omitted.emx_ports) == 4


def test_xfm_il_ct_none_matches_pre_ticket03_reference_bytes(tmp_path):
    """Stronger byte-identical pin: hashes the CT-less GDS bytes at the
    default _il() configuration against a literal recorded digest (taken
    from this exact build BEFORE ticket 03's CT code existed, re-pinned
    when write_gds switched to file-stem top-cell naming, then intentionally
    re-pinned by ticket 06 when every xfm_il mode adopted the W/S-derived
    symmetric bridge-lane stagger.  CT still must not change these bytes;
    this digest now protects the corrected CT-less geometry."""
    import hashlib

    cell = _il()
    gds = tmp_path / "ct_none_reference.gds"
    port.write_gds(cell, gds)
    digest = hashlib.sha256(gds.read_bytes()).hexdigest()
    assert digest == "31a15bd5bb69ea5aacc887b5b025c740ce236fd229481d727c709df6048b970a", (
        f"CT-less xfm_il() GDS bytes changed (digest {digest}); if this is "
        "an intentional geometry change to the CT-less path, update the "
        "expected digest -- ticket 06 owns the stagger; CT-only changes "
        "must not touch it"
    )


# ---------------------------------------------------------------------------
# CT geometry (ticket 03c): EXACT electrical midpoint, ZERO offset, no
# search -- supersedes ticket 03b's bounded tangential offset, itself
# rejected on a follow-up Virtuoso review ("ct 一定是在初级或次级线圈的中心
# 点...不在中心的话，p 和 n 看进去就不是对称的"): the user read 03b's own
# rendered sample (a small y-offset visible on CTP) as still a deviation
# from the true midpoint and asked for the alternative ticket 03b's own
# docstring already named but did not take -- when the exact midpoint's
# downward via stack is blocked by the OTHER winding's crossunder, go
# UPWARD instead of sideways. Direction (up vs down) is now decided purely
# by comparing CT_ME to SL_ME (_il_ct_metal_guard / _il_ct_tap_exact); CT
# metal legality downward is UNCHANGED (CT<=SL_ME-3); upward has no floor
# at all. Ticket 03c also tightens NT_P<=NT_S<=NT_P+1 to NT_P==NT_S (user
# directive), which as a side effect keeps CTP/CTS on structurally
# OPPOSITE global sides whenever both are enabled (see xfm_il's docstring).
# ---------------------------------------------------------------------------


def test_xfm_il_ctp_downward_always_blocked_structural_sweep():
    """PROVEN (not merely common) structural fact, central to ticket 03c's
    whole design: a DOWNWARD CTP is blocked in EVERY legal configuration.
    P's own innermost ring sits, by the definition of an interleaved
    lattice built at half-pitch offset, exactly in the radial gap between
    two consecutive S rings; S's own inter-turn bridge spanning that same
    gap has a y-reach that scales with the SAME W/S terms sizing the via's
    own W x W footprint, so the via's y-span is mathematically always a
    SUBSET of the blocking region -- never a matter of tuning OD/OPENING
    wider. Swept across NT in 2..6 and OD/W/S/OPENING spanning almost an
    order of magnitude: zero exceptions."""
    for NT in (2, 3, 4, 5, 6):
        for OD, W, S, OPEN in (
            (200.0, 4.0, 2.0, 30.0), (150.0, 3.0, 1.5, 25.0),
            (120.0, 2.0, 1.0, 20.0), (400.0, 2.0, 8.0, 50.0),
        ):
            with pytest.raises(port.PortError, match="CTP.*blocked"):
                port.xfm_il(OD=OD, W=W, S=S, NT_P=NT, NT_S=NT,
                           OPENING_P=OPEN, OPENING_S=OPEN, LEAD_P=20.0,
                           LEAD_S=20.0, SL_ME="9", CT_P_ME="6")


def test_xfm_il_cts_downward_always_clear_structural_sweep():
    """Structural mirror of the CTP sweep above: a DOWNWARD CTS is clear in
    EVERY legal configuration -- S is always this device's overall
    innermost winding, so no P bridge (which only ever spans between P's
    OWN, strictly larger-radius ring pairs) ever reaches in far enough to
    block it."""
    for NT in (2, 3, 4, 5, 6):
        for OD, W, S, OPEN in (
            (200.0, 4.0, 2.0, 30.0), (150.0, 3.0, 1.5, 25.0),
            (120.0, 2.0, 1.0, 20.0), (400.0, 2.0, 8.0, 50.0),
        ):
            cell = port.xfm_il(OD=OD, W=W, S=S, NT_P=NT, NT_S=NT,
                               OPENING_P=OPEN, OPENING_S=OPEN, LEAD_P=20.0,
                               LEAD_S=20.0, SL_ME="9", CT_S_ME="6")
            assert cell.emx_ports


def test_xfm_il_ctp_upward_exit_side_matches_nt_p_parity_zero_offset():
    """CTP exits on P's own true-midpoint arm at EXACTLY y=0 (no offset,
    ticket 03c) via an UPWARD CT_P_ME -- the only direction that ever
    builds for CTP (see the structural sweep above): LEFT for odd NT_P
    (same side as S's global port+escape), RIGHT for even NT_P (same
    side as P's own P1/N1). Also pins the negative: x must never be 0
    (ticket 03's axial design, rejected twice over)."""
    for NT_P, want_right in ((2, True), (3, False), (4, True), (5, False),
                             (6, True)):
        cell = _il(NT_P=NT_P, NT_S=NT_P, CT_P_ME="10")
        p = {q["logical_name"]: tuple(q["label_xy_um"])
             for q in cell.emx_ports}
        x, y = p["CTP"]
        assert x != 0.0, f"NT_P={NT_P}: CTP landed at x=0 (axial, rejected)"
        assert (x > 0) == want_right, (
            f"NT_P={NT_P}: CTP exit side wrong (x={x}, want_right={want_right})"
        )
        assert y == pytest.approx(0.0), (
            f"NT_P={NT_P}: CTP upward tap must be at the EXACT midpoint "
            f"y=0, no offset (got y={y})"
        )


def test_xfm_il_cts_downward_exit_side_matches_nt_s_parity_zero_offset():
    """CTS exits on S's true-midpoint arm at EXACTLY y=0 (no offset) via a
    DOWNWARD CT_S_ME (always clear, see the structural sweep above):
    LOCAL side = NT_S parity, then FLIPPED globally by the whole-cell MY
    mirror: local odd (left) -> global RIGHT; local even (right) ->
    global LEFT. x must never be 0."""
    for NT_S, want_right in ((2, False), (3, True), (4, False), (5, True),
                             (6, False)):
        cell = _il(NT_P=NT_S, NT_S=NT_S, CT_S_ME="6")
        p = {q["logical_name"]: tuple(q["label_xy_um"])
             for q in cell.emx_ports}
        x, y = p["CTS"]
        assert x != 0.0, f"NT_S={NT_S}: CTS landed at x=0 (axial, rejected)"
        assert (x > 0) == want_right, (
            f"NT_S={NT_S}: CTS exit side wrong (x={x}, want_right={want_right})"
        )
        assert y == pytest.approx(0.0), (
            f"NT_S={NT_S}: CTS downward tap must be at the EXACT midpoint "
            f"y=0, no offset (got y={y})"
        )


def test_xfm_il_ctp_position_pinned_exact_midpoint_upward():
    """Default _il() config (NT_P=3, OD=200/W=4/S=2/PITCH=12): P's true
    midpoint tap_x=-76 (native odd-NT formula, _il_ct_tap_x), lead exits
    LEFT to -(OD/2+LEAD)=-120, upward onto CT_P_ME="10" -- EXACT y=0, not
    merely "close to it"."""
    OD, W, S, NT_P, LEAD_P = 200.0, 4.0, 2.0, 3, 20.0
    tap_x = _il_ct_tap_x(OD, W, S, NT_P)
    assert tap_x == pytest.approx(-76.0)
    cell = _il(OD=OD, W=W, S=S, NT_P=NT_P, NT_S=NT_P, LEAD_P=LEAD_P,
              CT_P_ME="10")
    p = {q["logical_name"]: tuple(q["label_xy_um"]) for q in cell.emx_ports}
    assert p["CTP"] == pytest.approx((-(OD / 2 + LEAD_P), 0.0))
    by_name = {q["name"]: q for q in cell.emx_ports}
    assert by_name["CTP"]["metal"] == "M10"


def test_xfm_il_cts_position_pinned_exact_midpoint_downward():
    """S's own true midpoint (NT_S=3, OD_S=188): local tap_x=-70, lead
    exits (after the MY mirror) RIGHT to OD_S/2+LEAD_S=114, downward onto
    CT_S_ME="6" -- EXACT y=0."""
    OD, W, S, NT_S, LEAD_S = 200.0, 4.0, 2.0, 3, 20.0
    pitch = 2.0 * (W + S)
    OD_S = OD - pitch
    tap_x_local = _il_ct_tap_x(OD_S, W, S, NT_S)
    assert tap_x_local == pytest.approx(-70.0)
    cell = _il(OD=OD, W=W, S=S, NT_P=NT_S, NT_S=NT_S, LEAD_S=LEAD_S,
              CT_S_ME="6")
    p = {q["logical_name"]: tuple(q["label_xy_um"]) for q in cell.emx_ports}
    assert p["CTS"] == pytest.approx((OD_S / 2 + LEAD_S, 0.0))
    by_name = {q["name"]: q for q in cell.emx_ports}
    assert by_name["CTS"]["metal"] == "M6"


def _il_ct_tap_x(OD, W, S, NT):
    """Test-side mirror of _il_ct_tap_exact's own tap_x formula (=
    _ind_ct_tap's native odd/even closed-column tap_x) -- kept independent
    here (not imported) so position-pin tests exercise the SAME formula
    computed twice, catching an accidental drift in either copy."""
    pitch = 2.0 * (W + S)
    if NT % 2 == 0:
        return OD / 2 - (NT - 1) * pitch - W
    return -OD / 2 + (NT - 1) * pitch


def test_xfm_il_ct_both_enabled_port_order_and_metal_labels():
    """Both CTP (upward, the only direction it ever builds) and CTS
    (downward) enabled together -- opposite global sides, per xfm_il's
    own docstring (ticket 03c's equal-turns constraint)."""
    cell = _il(CT_P_ME="10", CT_S_ME="6")
    assert [q["name"] for q in cell.emx_ports] == [
        "P1", "N1", "P2", "N2", "CTP", "CTS"]
    by_name = {q["name"]: q for q in cell.emx_ports}
    assert by_name["CTP"]["metal"] == "M10"
    assert by_name["CTS"]["metal"] == "M6"
    assert by_name["CTP"]["label_xy_um"][0] * by_name["CTS"]["label_xy_um"][0] < 0, (
        "CTP and CTS must land on OPPOSITE global sides (equal turns, "
        "ticket 03c)"
    )
    single_p = _il(CT_P_ME="10")
    assert [q["name"] for q in single_p.emx_ports] == [
        "P1", "N1", "P2", "N2", "CTP"]
    single_s = _il(CT_S_ME="6")
    assert [q["name"] for q in single_s.emx_ports] == [
        "P1", "N1", "P2", "N2", "CTS"]


def test_xfm_il_ct_port_order_override_sized_to_enabled_taps():
    cell = _il(CT_P_ME="10", port_order=["A1", "A2", "A3", "A4", "A5"])
    assert [q["name"] for q in cell.emx_ports] == [
        "A1", "A2", "A3", "A4", "A5"]
    with pytest.raises(port.PortError, match="5 entries"):
        _il(CT_P_ME="10", port_order=["A1", "A2", "A3", "A4"])
    with pytest.raises(port.PortError, match="6 entries"):
        _il(CT_P_ME="10", CT_S_ME="6", port_order=["A1", "A2", "A3", "A4"])


# ---------------------------------------------------------------------------
# CT metal legality + direction guard (_il_ct_metal_guard /
# _il_ct_adjacency_guard): downward CT<=SL_ME-3 UNCHANGED from ticket 03;
# upward has NO floor; CT_ME==SL_ME is always illegal.
# ---------------------------------------------------------------------------


def test_xfm_il_ctp_downward_at_sl_minus_2_fails_closed():
    """CT_P at SL_ME-2 (leg2's own layer) must fail -- the old 02c-era
    <=SL-2 bound no longer holds under 02d's dual-layer crossunder.
    Unchanged by ticket 03c: still applies only to DOWNWARD metals."""
    with pytest.raises(port.PortError, match="three levels below"):
        _il(CT_P_ME="7")  # SL_ME="9" default -> SL-2 = M7


def test_xfm_il_ctp_downward_at_sl_minus_1_fails_closed():
    with pytest.raises(port.PortError, match="three levels below"):
        _il(CT_P_ME="8")  # SL_ME="9" default -> SL-1 = M8


def test_xfm_il_cts_downward_at_sl_minus_2_fails_closed():
    with pytest.raises(port.PortError, match="three levels below"):
        _il(CT_S_ME="7")


def test_xfm_il_cts_downward_at_sl_minus_1_fails_closed():
    with pytest.raises(port.PortError, match="three levels below"):
        _il(CT_S_ME="8")


def test_xfm_il_ct_at_sl_minus_3_boundary_still_a_legal_downward_metal():
    """SL_ME-3 (M6 at SL_ME="9") clears the adjacency floor for BOTH CTP
    and CTS -- CTS actually builds there (always clear downward); CTP
    still fails, but on the STRUCTURAL blocked-window check, not the
    adjacency floor (proves the floor itself is satisfied at M6, matching
    ticket 03's own boundary)."""
    assert _il(CT_S_ME="6").emx_ports
    with pytest.raises(port.PortError, match="blocked"):
        _il(CT_P_ME="6")


def test_xfm_il_ct_guard_message_names_both_occupied_legs():
    with pytest.raises(port.PortError) as excinfo:
        _il(CT_P_ME="8")
    msg = str(excinfo.value)
    assert "CTP" in msg
    assert "M8" in msg and "M7" in msg  # leg1 (SL-1) and leg2 (SL-2) named
    assert "leg1" in msg and "leg2" in msg


def test_xfm_il_ct_equal_to_sl_me_always_illegal():
    """Ticket 03c: CT_ME == SL_ME is neither "up" nor "down" -- it is the
    coil ring's own layer, always rejected with a dedicated message (not
    the downward-only "three levels below" one, which would be
    misleading here)."""
    with pytest.raises(port.PortError, match="cannot equal SL_ME"):
        _il(CT_P_ME="9")  # SL_ME="9" default
    with pytest.raises(port.PortError, match="cannot equal SL_ME"):
        _il(CT_S_ME="9")


def test_xfm_il_ct_upward_has_no_adjacency_floor():
    """Unlike downward, an upward CT_ME immediately adjacent to SL_ME
    (SL_ME+1, the tightest possible upward choice) is legal -- no
    equivalent of the three-levels-below floor applies above SL_ME."""
    cell = _il(CT_P_ME="10")  # SL_ME="9" default; "10" = SL_ME+1
    assert cell.emx_ports


def test_xfm_il_sl_me_1_still_fails_closed_with_or_without_ct():
    """SL_ME="1"/"2" fail via the existing 'two metals below' guard
    regardless of CT (family precedent: SL_ME can never resolve to a metal
    that leaves no room for the two crossunder legs, so no separate
    CT-specific M1/M2 exclusion is needed)."""
    with pytest.raises(port.PortError, match="two metals below"):
        _il(SL_ME="1")


# ---------------------------------------------------------------------------
# N28 CT direction/feasibility matrix (ticket 03c, geometric-only
# enforcement already in effect -- n28-rules-slim): SL=M9 -> CTP upward to
# M10/AP, CTS downward to M6; SL=M10 -> CTP upward to AP, CTS downward to
# M7; SL=AP -> no metal above it, so CTS downward to M8 works but CTP is
# impossible in EITHER direction (downward always blocked, no upward
# option) -- a genuine architectural limit of the AP body, not a bug.
# ---------------------------------------------------------------------------


def _il_n28(**kw):
    ctx = port.process_rule_context("n28_1p10m")
    base = dict(OD=200.0, W=5.0, S=2.5, NT_P=3, NT_S=3, OPENING_P=18.0,
                OPENING_S=18.0, LEAD_P=20.0, LEAD_S=20.0, process=ctx)
    base.update(kw)
    return port.xfm_il(**base)


def test_xfm_il_n28_sl_m9_ctp_upward_to_m10_or_ap_builds(tmp_path):
    ctx = port.process_rule_context("n28_1p10m")
    for ct_p_me, ct_p_idx in (("10", 10), ("AP", 11)):
        cell = _il_n28(SL_ME="9", CT_P_ME=ct_p_me)
        assert [q["name"] for q in cell.emx_ports] == [
            "P1", "N1", "P2", "N2", "CTP"]
        layers = write_and_parse(cell, tmp_path, f"xfm_il_n28_m9_ctp_{ct_p_me}")
        assert port.process_metal_layer(ctx, ct_p_idx) in layers
        assert_on_grid(layers)


def test_xfm_il_n28_sl_m9_cts_downward_to_m6_builds(tmp_path):
    # W=4: the tap stack down to M6 must fit that thin metal's own
    # max-width rule (gdsfactory review 2026-09-21).
    ctx = port.process_rule_context("n28_1p10m")
    cell = _il_n28(SL_ME="9", CT_S_ME="6", W=4.0)
    layers = write_and_parse(cell, tmp_path, "xfm_il_n28_m9_cts_m6")
    assert port.process_metal_layer(ctx, 6) in layers
    assert port.process_via_layer(ctx, 8) in layers  # SL-1
    assert port.process_via_layer(ctx, 7) in layers  # SL-2
    assert port.process_via_layer(ctx, 6) in layers  # SL-3 -> CT_ME
    assert_on_grid(layers)


def test_xfm_il_n28_sl_m9_ctp_downward_fails_closed():
    with pytest.raises(port.PortError, match="CTP.*blocked"):
        _il_n28(SL_ME="9", CT_P_ME="6")


def test_xfm_il_n28_sl_m10_ctp_upward_to_ap_builds(tmp_path):
    ctx = port.process_rule_context("n28_1p10m")
    cell = _il_n28(SL_ME="10", CT_P_ME="AP")
    layers = write_and_parse(cell, tmp_path, "xfm_il_n28_m10_ctp_ap")
    assert port.process_metal_layer(ctx, 11) in layers
    assert_on_grid(layers)


def test_xfm_il_n28_sl_m10_cts_downward_to_m7_builds(tmp_path):
    ctx = port.process_rule_context("n28_1p10m")
    cell = _il_n28(SL_ME="10", CT_S_ME="7")
    layers = write_and_parse(cell, tmp_path, "xfm_il_n28_m10_cts_m7")
    assert port.process_metal_layer(ctx, 7) in layers
    assert_on_grid(layers)


def test_xfm_il_n28_sl_m10_ctp_downward_fails_closed():
    with pytest.raises(port.PortError, match="CTP.*blocked"):
        _il_n28(SL_ME="10", CT_P_ME="7")


def test_xfm_il_n28_sl_m10_body_without_ct_still_builds(tmp_path):
    """SL_ME="10" with NO CT still builds -- enabling CTP is what fails,
    not the SL_ME="10" body itself."""
    cell = _il_n28(SL_ME="10")
    layers = write_and_parse(cell, tmp_path, "xfm_il_n28_m10_no_ct")
    assert_on_grid(layers)


def test_xfm_il_n28_sl_ap_cts_downward_to_m8_builds(tmp_path):
    """SL_ME="AP" has no metal above it, so only downward ever applies --
    CTS (structurally always clear downward) builds fine there."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = _il_n28(SL_ME="AP", CT_S_ME="8")
    layers = write_and_parse(cell, tmp_path, "xfm_il_n28_ap_cts_m8")
    assert port.process_metal_layer(ctx, 8) in layers
    assert_on_grid(layers)


def test_xfm_il_n28_sl_ap_ctp_impossible_in_either_direction():
    """SL_ME="AP" is the top of the stack: an upward CT_P_ME cannot exist
    (there is no metal above AP -- _metal_index/metal_layer's own 1..11
    stack bound fails it), and a downward one is structurally always
    blocked (the same universal fact as every other SL_ME body). CTP is
    therefore genuinely impossible on an AP body, in EITHER direction --
    this is the real, documented limit of that body (ticket 03c), not a
    bug to work around."""
    with pytest.raises(port.PortError, match="CTP.*blocked"):
        _il_n28(SL_ME="AP", CT_P_ME="8")  # downward, SL_ME-3 floor
    with pytest.raises(port.PortError):
        _il_n28(SL_ME="AP", CT_P_ME="12")  # "upward" -- no such metal exists


def test_xfm_il_n28_sl_m9_body_without_ct_still_builds(tmp_path):
    """n28-rules-slim (user directive 2026-07-19): SL_ME="9" with no CT
    builds cleanly (leg2's own via stack crossing VIA7 is fully modeled
    geometry now, no policy-gate rejection)."""
    cell = _il_n28(SL_ME="9")
    layers = write_and_parse(cell, tmp_path, "xfm_il_n28_m9_no_ct")
    assert_on_grid(layers)


# ---------------------------------------------------------------------------
# Collision handling (ticket 03c): a downward tap's SINGLE exact-midpoint
# window must clear (a) the OTHER winding's SL_ME-1/SL_ME-2 crossunder and
# (b), when CT_P_ME==CT_S_ME, the OTHER tap's own already-drawn CT_ME
# lead. No tangential search exists any more -- a blocked window fails
# closed by name, it is never silently offset (ticket 03b's own
# compromise, itself superseded here).
# ---------------------------------------------------------------------------


def test_xfm_il_ct_no_clear_window_fails_closed_by_name():
    """Ticket 03c simplification over 03b: since a downward CTP is now
    PROVEN always blocked (see the structural sweep above), demonstrating
    the fail-closed path no longer needs a contrived direct unit call --
    ANY downward CT_P_ME through the public xfm_il() API already
    reproduces it."""
    with pytest.raises(port.PortError, match="CTP.*blocked.*true midpoint is a FIXED point"):
        _il(CT_P_ME="6")


def test_xfm_il_ct_blocked_message_suggests_upward_alternative():
    with pytest.raises(port.PortError, match="try an upward CTP_ME"):
        _il(CT_P_ME="6")


def test_xfm_il_ct_blocked_at_ap_body_cites_real_limit_not_upward_hint():
    """At SL_ME="AP" (no metal above it), the blocked-window message must
    NOT suggest an upward CT_ME (there isn't one) -- it must instead
    name this as the AP body's own real limit."""
    ctx = port.process_rule_context("n28_1p10m")
    with pytest.raises(port.PortError,
                       match="SL_ME='AP' has no metal above it"):
        port.xfm_il(OD=200.0, W=5.0, S=2.5, NT_P=3, NT_S=3, OPENING_P=18.0,
                   OPENING_S=18.0, LEAD_P=20.0, LEAD_S=20.0, SL_ME="AP",
                   CT_P_ME="8", process=ctx)


def test_xfm_il_ct_shared_metal_mutual_lead_short_direct_unit_backstop():
    """Ticket 03c's equal-turns constraint keeps CTP/CTS on opposite
    global sides in every legal public-API configuration (see xfm_il's
    docstring), so the CT_P_ME==CT_S_ME mutual-lead collision ticket 03b
    found empirically (an AP-body CT_P_ME=CT_S_ME="8" build) is no longer
    reachable through xfm_il() itself. The defensive check inside
    _il_ct_tap_exact is still real code, not dead code, though: this
    exercises it directly with a contrived ``other_ct_lead`` region that
    DOES overlap the second tap's own lead span, proving the mechanism
    still functions as a backstop."""
    OD, W, NT = 100.0, 4.0, 2
    pitch = 12.0
    # NT=2 (even): tap_x=lead_x=OD/2-(NT-1)*pitch-W=34; lead_len=
    # LEAD+(NT-1)*pitch+W=26 -> lead spans x:[34,60]. A contrived
    # "already-drawn" lead region overlapping that span:
    contrived_lead = kdb.Region(
        kdb.Box(_nm(34.0), _nm(-5.0), _nm(90.0), _nm(5.0)))
    cell = port.Cell("t", "t", {})
    with pytest.raises(port.PortError, match="blocked by the other CT tap"):
        port._il_ct_tap_exact(
            cell, kdb.Region(), kdb.Region(), "S", OD=OD, W=W, LEAD=10.0,
            NT=NT, SL_ME="9", CT_ME="6", port_name="CTS", process=None,
            PITCH=pitch, other_ct_lead=contrived_lead,
        )


def test_xfm_net_short_catches_ct_tap_overlap_directly():
    """Direct unit check that _xfm_net_short's layer-complete overlap scan
    covers CT-drawn shapes (via stack + lead) from _il_ct_tap_exact, not
    merely winding rings -- ticket 03/03b/03c requires the CT chain to be
    part of the net-short gate's coverage, pinned generically rather than
    relying on finding real xfm_il parameters that happen to trigger it.
    Uses an UPWARD tap (CT_ME="10" above SL_ME="9") since that is the
    direction that structurally always builds for a P-side tap (see the
    structural sweep above); the net-short gate itself is direction-
    agnostic (checks whatever layers were actually drawn)."""
    OD, W, NT = 100.0, 4.0, 2
    pitch = 12.0
    a = port.Cell("a", "a", {})
    port._il_ct_tap_exact(a, kdb.Region(), kdb.Region(), "P", OD=OD, W=W,
                          LEAD=10.0, NT=NT, SL_ME="9", CT_ME="10",
                          port_name="CTP", process=None, PITCH=pitch)
    b = port.Cell("b", "b", {})
    # deliberately overlapping rectangle inside CTP's own lead span
    # (tap_x=34 for NT=2/OD=100/PITCH=12 -> lead runs [34, 34+26]=[34,60])
    b.add_rect(port.metal_layer(10), 40.0, -1.0, 42.0, 1.0)
    with pytest.raises(port.PortError, match="short"):
        port._xfm_net_short("test", a, b)


# ---------------------------------------------------------------------------
# Ticket 02d's four topology invariants, re-verified under CT-enabled
# configs (spec.md "在含 CT 配置下四不变量全绿") + net-ownership: CTP lands
# on the SAME net as P1/N1, CTS on the SAME net as P2/N2, and the device
# stays exactly 2 nets overall (LayoutToNetlist, CT chain layers/vias
# included). Ticket 03c moved the CT tap's direction/offset handling but
# the invariants themselves are topology-level and unaffected; CTP here
# always uses an UPWARD metal (the only direction it ever builds under
# _IL_NT_COMBOS's equal-turn pairs -- downward is proven always blocked).
# ---------------------------------------------------------------------------


def test_xfm_il_ct_invariant1_series_component_count_unchanged_by_ct():
    """Invariant 1 under CT: component counts/formula unchanged (the CT
    via only adds area to an EXISTING SL-only component, never a new
    one), P1 still != N1's component, and the CT via's own SL-layer
    touchpoint (its via centre -- x=tap_x+W/2, y=0 exactly, ticket 03c's
    zero-offset guarantee) lands on the SAME SL-only component as their
    own winding's ports (net-ownership, geometry level)."""
    OD, W, S = 200.0, 4.0, 2.0
    pitch = 2.0 * (W + S)
    for NT_P, NT_S in _IL_NT_COMBOS:
        cell = _il(NT_P=NT_P, NT_S=NT_S, CT_P_ME="10", CT_S_ME="6")
        pri, sec = cell.insts[0].cell, cell.insts[1].cell
        p = {q["logical_name"]: tuple(q["label_xy_um"])
             for q in cell.emx_ports}
        tap_x_p = _il_ct_tap_x(OD, W, S, NT_P)
        OD_S = OD - pitch
        tap_x_s_local = _il_ct_tap_x(OD_S, W, S, NT_S)
        ctp_touch = (tap_x_p + W / 2, 0.0)
        # Local via x-span [tap_x_s_local, tap_x_s_local+W] mirrors (MY:
        # x->-x) to global [-tap_x_s_local-W, -tap_x_s_local]; its centre
        # is -tap_x_s_local - W/2, not +W/2.
        cts_touch = (-tap_x_s_local - W / 2, 0.0)

        n_p, p1_idx = _sl_component_index(pri, 9, *p["P1"])
        _, n1_idx = _sl_component_index(pri, 9, *p["N1"])
        assert n_p == 2 * NT_P - 1, (
            f"NT_P={NT_P} NT_S={NT_S}: CT changed P's SL-only component "
            f"count (got {n_p}, expected {2 * NT_P - 1})"
        )
        assert p1_idx != n1_idx
        _, ctp_idx = _sl_component_index(pri, 9, *ctp_touch)
        assert ctp_idx is not None, (
            f"NT_P={NT_P} NT_S={NT_S}: CTP's SL touchpoint lands on no "
            "SL-only component at all -- the via missed the ring metal"
        )

        n_s, p2_idx = _sl_component_index(sec, 9, *p["P2"])
        _, n2_idx = _sl_component_index(sec, 9, *p["N2"])
        assert n_s == 2 * NT_S + 1, (
            f"NT_P={NT_P} NT_S={NT_S}: CT changed S's SL-only component "
            f"count (got {n_s}, expected {2 * NT_S + 1})"
        )
        assert p2_idx != n2_idx
        _, cts_idx = _sl_component_index(sec, 9, *cts_touch)
        assert cts_idx is not None, (
            f"NT_P={NT_P} NT_S={NT_S}: CTS's SL touchpoint lands on no "
            "SL-only component at all -- the via missed the ring metal"
        )


def test_xfm_il_ct_invariant2_no_closed_loop_holes():
    for NT_P, NT_S in _IL_NT_COMBOS:
        cell = _il(NT_P=NT_P, NT_S=NT_S, CT_P_ME="10", CT_S_ME="6")
        for label, sub in (("P", cell.insts[0].cell), ("S", cell.insts[1].cell)):
            reg = _sl_region(sub, 9)
            reg.merge()
            for i, comp in enumerate(reg.each()):
                assert comp.holes() == 0, (
                    f"NT_P={NT_P} NT_S={NT_S}: {label} CT-enabled SL-only "
                    f"component {i} has {comp.holes()} hole(s)"
                )


def test_xfm_il_ct_invariant3_dual_layer_legs_disjoint_per_layer():
    for NT_P, NT_S in _IL_NT_COMBOS:
        cell = _il(NT_P=NT_P, NT_S=NT_S, CT_P_ME="10", CT_S_ME="6")
        pri, sec = cell.insts[0].cell, cell.insts[1].cell
        sl = 9
        p_sl1, s_sl1 = _sl1_region(pri, sl), _sl1_region(sec, sl)
        p_sl2, s_sl2 = _sl2_region(pri, sl), _sl2_region(sec, sl)
        assert not p_sl1.is_empty() and not s_sl1.is_empty()
        assert not p_sl2.is_empty() and not s_sl2.is_empty()
        assert (p_sl1 & s_sl1).is_empty()
        assert (p_sl2 & s_sl2).is_empty()


def test_xfm_il_ct_invariant4_via_aware_net_count_is_two_and_ct_owns_net(tmp_path):
    """Invariant 4 under CT: the via-aware LayoutToNetlist extraction (now
    including BOTH CT_ME layers -- CTP's upward M10 and CTS's downward M6
    -- and their own via classes) must still find exactly 2 nets, AND
    probing CTP/CTS confirms they sit on the SAME net as P1/P2
    respectively (the net-ownership assertion spec.md asks for)."""
    for NT_P, NT_S in _IL_NT_COMBOS:
        cell = _il(NT_P=NT_P, NT_S=NT_S, CT_P_ME="10", CT_S_ME="6")
        sl = 9
        gds = tmp_path / f"xfm_il_ct_l2n_{NT_P}_{NT_S}.gds"
        port.write_gds(cell, gds)
        ly = kdb.Layout()
        ly.read(str(gds))
        top = ly.top_cell()
        top.flatten(True)

        def region_for(layer_tuple, ly=ly, top=top):
            li = ly.find_layer(*layer_tuple)
            if li is None:
                return kdb.Region()
            return kdb.Region(top.begin_shapes_rec(li))

        r_sl = region_for(port.metal_layer(sl))
        r_sl1 = region_for(port.metal_layer(sl - 1))
        r_sl2 = region_for(port.metal_layer(sl - 2))
        r_ctp = region_for(port.metal_layer(10))  # upward
        r_cts = region_for(port.metal_layer(6))  # downward
        r_via_sl1 = region_for(port.via_layer(sl - 1))  # SL<->SL-1
        r_via_sl2 = region_for(port.via_layer(sl - 2))  # SL-1<->SL-2
        r_via_ctp = region_for(port.via_layer(sl))  # SL<->CTP(SL+1)
        r_via_cts = region_for(port.via_layer(6))  # CTS(SL-3)<->SL-2
        assert not r_ctp.is_empty() and not r_via_ctp.is_empty(), (
            f"NT_P={NT_P} NT_S={NT_S}: no upward CTP metal/via in the "
            "written GDS"
        )
        assert not r_cts.is_empty() and not r_via_cts.is_empty(), (
            f"NT_P={NT_P} NT_S={NT_S}: no downward CTS metal/via in the "
            "written GDS"
        )

        l2n = kdb.LayoutToNetlist(top.name, ly.dbu)
        for name, r in (("SL", r_sl), ("SL1", r_sl1), ("SL2", r_sl2),
                        ("CTP", r_ctp), ("CTS", r_cts),
                        ("VIA_SL1", r_via_sl1), ("VIA_SL2", r_via_sl2),
                        ("VIA_CTP", r_via_ctp), ("VIA_CTS", r_via_cts)):
            l2n.register(r, name)
            l2n.connect(r)
        l2n.connect(r_sl, r_via_sl1)
        l2n.connect(r_sl1, r_via_sl1)
        l2n.connect(r_sl1, r_via_sl2)
        l2n.connect(r_sl2, r_via_sl2)
        # CTP: upward, SL_ME(9) <-> SL_ME+1(10) via via_layer(9)
        l2n.connect(r_sl, r_via_ctp)
        l2n.connect(r_ctp, r_via_ctp)
        # CTS: downward, SL_ME-2(7) <-> SL_ME-3(6) via via_layer(6)
        l2n.connect(r_sl2, r_via_cts)
        l2n.connect(r_cts, r_via_cts)
        l2n.extract_netlist()
        circuit = l2n.netlist().circuit_by_name(top.name)
        nets = list(circuit.each_net())
        assert len(nets) == 2, (
            f"NT_P={NT_P} NT_S={NT_S}: CT-enabled via-aware extraction "
            f"found {len(nets)} nets, expected exactly 2"
        )

        p = {q["logical_name"]: tuple(q["label_xy_um"])
             for q in cell.emx_ports}

        def probe(region, xy):
            pt = kdb.Point(_nm(xy[0]), _nm(xy[1]))
            return l2n.probe_net(region, pt)

        n_p1 = probe(r_sl, p["P1"])
        n_ctp = probe(r_ctp, p["CTP"])
        n_p2 = probe(r_sl, p["P2"])
        n_cts = probe(r_cts, p["CTS"])
        assert n_p1 is not None and n_ctp is not None
        assert n_p1.cluster_id == n_ctp.cluster_id, (
            f"NT_P={NT_P} NT_S={NT_S}: CTP is not on P1's net"
        )
        assert n_p2 is not None and n_cts is not None
        assert n_p2.cluster_id == n_cts.cluster_id, (
            f"NT_P={NT_P} NT_S={NT_S}: CTS is not on P2's net"
        )
        assert n_p1.cluster_id != n_p2.cluster_id


# ---------------------------------------------------------------------------
# Red-line: FUNCTION_MAPPING's xfm_il entry must stay in sync with the
# actual signature (bug review history: this drifted twice already).
# Unaffected by ticket 03c (no new xfm_il-level parameters -- CT_P_ME/
# CT_S_ME's semantics extended, names unchanged).
# ---------------------------------------------------------------------------


def test_xfm_il_function_mapping_parameters_match_signature():
    """FUNCTION_MAPPING's xfm_il entry must stay in sync with the actual
    signature (bug review history: this drifted twice already). Every
    xfm_* FUNCTION_MAPPING entry omits the trailing infrastructure params
    (ground_fixture, process) -- e.g. xfm_ms/xfm_balun's own entries list
    stops at CT_P_ME/CT_S_ME too -- so this compares against the signature
    with only those two dropped, not a literal full match."""
    import inspect

    sig_params = [p for p in inspect.signature(port.xfm_il).parameters
                 if p not in ("ground_fixture", "process")]
    entry = next(e for e in port.FUNCTION_MAPPING
                if e["python_function"] == "xfm_il")
    assert entry["parameters"] == sig_params


# bridge_side="right" retirement (xfm_il ticket 02c): co-locating a
# winding's own bridge with its own lead/escape opening on one arm was the
# root cause of the 02b parallel-collapse bug (see the invariant-1 series
# topology test above). Neither function's "right" branch was ever
# resurrected -- ticket 02d retired "left" from ACTIVE USE too (xfm_il now
# builds both P and S with the native "alternate" zigzag instead), but kept
# it available on ``ind_sym``/``_ind_ring_turns`` for any other caller (see
# their docstrings) rather than deleting a still-tested, still-correct
# mode -- so this fail-closed guard on the truly retired "right" value
# remains exactly as before.
# ---------------------------------------------------------------------------


def test_base_ind_hud_cross_bridge_side_right_retired_fails_closed():
    with pytest.raises(port.PortError, match="bridge_side"):
        port.base_ind_hud_cross(bridge_side="right")


def test_ind_ring_turns_bridge_side_right_retired_fails_closed():
    cell = port.Cell("t", "t", {})
    with pytest.raises(port.PortError, match="bridge_side"):
        port._ind_ring_turns(cell, 60.0, 2.0, 5.0, 2.0, 3, "9", "8",
                             "RFVLSI", None, None, bridge_side="right")


# ---------------------------------------------------------------------------
# xfm_tw (ticket 01: path planner + geometry kernel) -- see
# .scratch/xfm-tw-twisted/spec.md and issues/01-path-planner-geometry-kernel.md.
# The planner is a direct port of .scratch/xfm-tw-twisted/gen_topology.py's
# build_p/build_s RULE (boundary slot angles + dive assignment), decoupled
# from concrete coordinates. NR=3's leg table is pinned exactly to the v4
# baseline the user confirmed (spec.md "NR=3 基准").
# ---------------------------------------------------------------------------


def _tw_leg_table(segments):
    return [(s.boundary, s.direction, s.angle, s.dive)
            for s in segments if isinstance(s, port.TwLeg)]


def test_tw_plan_nr3_matches_v4_baseline():
    """spec.md NR=3 基准: pinned leg table for both windings."""
    p_segments, s_segments = port._tw_plan(3)
    assert _tw_leg_table(p_segments) == [
        (1, "desc", 0, True),
        (2, "desc", 90, False),
        (2, "asc", 270, True),
        (1, "asc", 0, False),
    ]
    assert _tw_leg_table(s_segments) == [
        (1, "desc", 180, True),
        (2, "desc", 90, True),
        (2, "asc", 270, False),
        (1, "asc", 180, False),
    ]


def _tw_arc_span(segments):
    total = 0
    for seg in segments:
        if isinstance(seg, port.TwArc):
            if seg.ccw:
                total += (seg.angle_to - seg.angle_from) % 360
            else:
                total += (seg.angle_from - seg.angle_to) % 360
    return total


@pytest.mark.parametrize("NR", [3, 5, 7, 9])
def test_tw_plan_angular_span_is_nr_times_180(NR):
    p_segments, s_segments = port._tw_plan(NR)
    assert _tw_arc_span(p_segments) == NR * 180
    assert _tw_arc_span(s_segments) == NR * 180


@pytest.mark.parametrize("NR", [3, 5, 7, 9])
def test_tw_plan_dive_leg_count_is_nr_minus_1_each(NR):
    p_segments, s_segments = port._tw_plan(NR)
    p_dive = sum(1 for s in p_segments if isinstance(s, port.TwLeg) and s.dive)
    s_dive = sum(1 for s in s_segments if isinstance(s, port.TwLeg) and s.dive)
    assert p_dive == NR - 1
    assert s_dive == NR - 1
    p_legs = [s for s in p_segments if isinstance(s, port.TwLeg)]
    s_legs = [s for s in s_segments if isinstance(s, port.TwLeg)]
    assert len(p_legs) == 2 * (NR - 1)
    assert len(s_legs) == 2 * (NR - 1)


@pytest.mark.parametrize("NR", [3, 5, 7])
def test_tw_plan_slot_occupancy_no_conflict(NR):
    """No more than 2 leg-endpoints (the physical +-G tangential sides of
    one cardinal slot notch) ever share the same (ring, angle, layer)."""
    p_segments, s_segments = port._tw_plan(NR)
    occ: dict = {}
    for segments in (p_segments, s_segments):
        for seg in segments:
            if not isinstance(seg, port.TwLeg):
                continue
            for ring in (seg.boundary - 1, seg.boundary):
                key = (ring, seg.angle, seg.dive)
                occ[key] = occ.get(key, 0) + 1
    assert occ, "no leg endpoints recorded"
    assert max(occ.values()) <= 2, occ


@pytest.mark.parametrize("NR", [2, 4, 6])
def test_tw_plan_even_nr_fails_closed(NR):
    with pytest.raises(port.PortError, match="NR"):
        port._tw_plan(NR)


@pytest.mark.parametrize("NR", [1, 0, -1])
def test_tw_plan_below_3_fails_closed(NR):
    with pytest.raises(port.PortError, match="NR"):
        port._tw_plan(NR)


def test_tw_plan_first_and_last_segments_are_ring0_port_arcs():
    p_segments, _ = port._tw_plan(5)
    first, last = p_segments[0], p_segments[-1]
    assert isinstance(first, port.TwArc) and first.ring == 0
    assert isinstance(last, port.TwArc) and last.ring == 0
    assert first.angle_from == 270  # P1 side (bottom)
    assert last.angle_to == 90  # N1 side (top)


# ---------------------------------------------------------------------------
# xfm_tw geometry kernel: guards, ports, FUNCTION_MAPPING, and the four
# topology invariants from spec.md. Renderer design notes (rendering choice,
# G/GA derivation) are recorded in _tw_render_winding's own docstring.
#
# Known limitation (see the ticket handoff / final report): the P x S
# same-layer leg at a device's SECOND (or later) even boundary can still
# collide with its paired dive leg's base_xfm_cross via pads on SL_ME --
# confirmed geometrically (klayout region overlap) at NR=5. NR=3 (the only
# case with a single even boundary) is fully clean on every invariant at
# both SL_ME=9 and SL_ME=10; the invariant tests below cover NR=3 x
# SL_ME in {9, 10} as passing and pin the NR=5 gap down with an explicit,
# strict xfail rather than silently narrowing the required NR in {3, 5}
# matrix or leaving the residual undocumented.
# ---------------------------------------------------------------------------


def _tw(**kw):
    base = dict(OD=200.0, W=4.0, S=2.0, NR=3, OPENING_P=8.0, OPENING_N=8.0,
                LEAD=20.0, SL_ME="9")
    base.update(kw)
    return port.xfm_tw(**base)


def _tw_nr(NR, **kw):
    """Like _tw but scales OD with NR so every ring stays comfortably
    positive-radius for the larger invariant-matrix cases (NR=5, NR=7)."""
    return _tw(OD=60.0 + NR * 60.0, NR=NR, **kw)


# ---------------------------------------------------------------------------
# xfm_tw leg shape (ticket 02b -- .scratch/xfm-tw-twisted/issues/
# 02b-leg-straight-diag-straight.md): user目检 on ticket 02's gallery flagged
# the leg as a single end-to-end oblique wide path (slope atan2(2*G, pitch),
# not 45 degrees) butting into the ring arc's tangential end at a skewed cap
# -- "生硬拼接". This section pins the required shape (tangential-straight +
# exact-45-degree-diagonal + tangential-straight, collinear with the arc at
# both junctions) BEFORE the rework -- both tests below fail red against the
# pre-02b single-diagonal ``_tw_leg``/``_tw_leg_waypoints`` (the latter does
# not even exist yet pre-rework, so its test fails on AttributeError, which
# is still a valid red signal for "the required API/shape is missing").
# ---------------------------------------------------------------------------

# A small 3-ring H list (W=4, S=2 pitch=6, matching the family's own tight
# reference dimensions) reused by the isolated leg-shape tests below --
# independent of xfm_tw's own OD/NR wiring, so these tests probe _tw_leg /
# _tw_leg_waypoints directly rather than through the full device.
_TW_LEG_TEST_H = [80.0, 74.0, 68.0]
_TW_LEG_TEST_W = 4.0
_TW_LEG_TEST_S = 2.0
_TW_LEG_TEST_SL = 9


def _tw_leg_test_g():
    return port._tw_slot_half_width(
        _TW_LEG_TEST_W, _TW_LEG_TEST_S, _TW_LEG_TEST_SL, None
    )


def test_tw_leg_polygon_edges_are_45_degree_multiples():
    """02b: every edge of a boundary-crossing leg's own drawn polygon (dive
    leg on SL_ME-1, same-layer leg on SL_ME) must run along a multiple of
    45 degrees -- the family's own chamfered-octagon convention. The
    pre-02b single end-to-end oblique segment's two long edges (and its two
    end caps, perpendicular to them) sit at atan2(2*G, pitch) -- for
    W=4/S=2/M9 that's atan2(2*G, 6) with G > pitch/2 = 3, i.e. > 45 degrees
    off-vertical and not a 45-degree multiple -- so this fails red against
    the un-reworked shape. Grid-snap perturbation is <=2.5 nm
    (``_tw_snap_dbu_to_grid``), negligible against multi-um edges, so a
    tight 0.05-degree tolerance cannot be satisfied by grid noise alone."""
    G = _tw_leg_test_g()
    for dive in (True, False):
        seg = port.TwLeg(boundary=1, direction="desc", angle=0, dive=dive)
        cell = port.Cell("t", "t", {})
        port._tw_leg(cell, seg, _TW_LEG_TEST_H, _TW_LEG_TEST_W, G,
                     _TW_LEG_TEST_SL, None, False)
        # the wide-path leg polygon is always the FIRST shape appended
        # (dive legs additionally instance two vias() blocks, which are
        # separate Inst entries, not Shape entries, on this cell)
        pts = cell.shapes[0].points_nm
        n = len(pts)
        for i in range(n):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % n]
            dx, dy = x2 - x1, y2 - y1
            if dx == 0 and dy == 0:
                continue
            ang = math.degrees(math.atan2(dy, dx)) % 45.0
            ang = min(ang, 45.0 - ang)
            assert ang <= 0.05, (
                f"dive={dive}: leg polygon edge ({x1},{y1})->({x2},{y2}) "
                f"is {ang:.3f} degrees off the nearest 45-degree multiple"
            )


def test_tw_leg_waypoints_are_straight_diagonal_straight():
    """02b: ``_tw_leg_waypoints`` (the leg's own centerline, ticket 02b's
    new API) must return tangential-straight + exact-45-degree-diagonal +
    tangential-straight: the first and last legs of the path hold the
    radial coordinate fixed at the outer/inner ring's own H (collinear
    with -- same direction as -- the ring arc's own tangential run, so the
    junction has no kink); the middle segment's radial and tangential
    spans are both exactly ``pitch`` (H[boundary-1] - H[boundary], the
    ring-to-ring step) -- a true 45-degree diagonal, not the pre-02b
    atan2(2*G, pitch) slope."""
    G = _tw_leg_test_g()
    seg = port.TwLeg(boundary=1, direction="desc", angle=0, dive=True)
    pts = port._tw_leg_waypoints(seg, _TW_LEG_TEST_H, G, False)
    assert len(pts) == 4
    p0, p1, p2, p3 = pts
    pitch = _TW_LEG_TEST_H[0] - _TW_LEG_TEST_H[1]
    assert p0[0] == pytest.approx(_TW_LEG_TEST_H[0])
    assert p1[0] == pytest.approx(_TW_LEG_TEST_H[0])
    assert p2[0] == pytest.approx(_TW_LEG_TEST_H[1])
    assert p3[0] == pytest.approx(_TW_LEG_TEST_H[1])
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    assert abs(dx) == pytest.approx(pitch)
    assert abs(dy) == pytest.approx(pitch)


def test_tw_slot_half_width_pitch_half_bound_dominates_ap_case():
    """02b: new G >= pitch/2 lower bound (the diagonal must reach the full
    ring-to-ring pitch tangentially too). At the AP body's own W=6/S=6
    (K=W+min_space=8.0, AP min_space=2.0, pitch=12.0), the diagonal-
    clearance bound alone (sqrt(2)*K - pitch/2 = 5.3137...) undershoots
    pitch/2 = 6.0 -- without the new bound G would come out too small for
    a 45-degree diagonal to span the full pitch, which is geometrically
    impossible (the straight-run length (2*G-pitch)/2 would be negative).
    G must be pinned at (the grid-snapped) pitch/2, not the smaller
    diagonal-only bound; this is a strictly LOOSER (larger) G than ticket
    01/02's own formula gave for this exact case (5.370 um), a documented
    consequence of the new bound, not a regression."""
    ctx = port.process_rule_context("n28_1p10m")
    G = port._tw_slot_half_width(6.0, 6.0, port._metal_index("AP"), ctx)
    assert G >= 6.0 - 1e-9


# ---------------------------------------------------------------------------
# xfm_tw X-degeneracy + port joint (ticket 02c -- .scratch/xfm-tw-twisted/
# issues/02c-port-joint-and-x-degeneracy.md): user 二轮目检 found two more
# defects in 02b's own gallery: (1) the AP body's G lands EXACTLY on 02b's
# own new pitch/2 bound, collapsing the straight run to zero length -- the
# 45-degree diagonal starts right at the endpoint via pad's own centre, so
# the pad only PARTIALLY overlaps the (now nonexistent) straight conductor
# it's centred on, reproducing the same "partial corner overlap" 02b was
# supposed to eliminate; (2) every port stub is a SEPARATE rectangle from
# the ring arc it attaches to, touching only at a single corner (radial
# stub width realized in x, tangential arc width realized in y at that
# point -- two perpendicular thin bands meeting at a point, not a flush
# merge), and the OPENING_P/OPENING_N stub-centreline formula (g =
# OPENING + W/2) put the two ports' own inner edges 2*OPENING apart, not
# OPENING as the replica/family convention intends.
# ---------------------------------------------------------------------------


def test_tw_slot_half_width_pad_containment_bound_dominates_ap_case():
    """02c: G's bound (c) must be pitch/2 + W/2 (W = the tangential extent
    of the leg endpoint's own W x W ``vias()`` landing pad, see ``_tw_leg``)
    so the straight run (length G - pitch/2) fully contains the pad's own
    tangential half-width BEFORE the 45-degree diagonal begins -- 02b's
    bare pitch/2 bound let the AP body's straight run collapse to exactly
    zero, landing the diagonal's start at the pad's own centre (only a
    partial pad/conductor overlap -- the tw_ap_nr3 defect). At the AP
    body's own W=6/S=6 (pitch=12.0), the new bound is pitch/2 + W/2 = 9.0,
    strictly above 02b's own pitch/2 = 6.0 and its diagonal-clearance bound
    5.3137."""
    ctx = port.process_rule_context("n28_1p10m")
    W = 6.0
    G = port._tw_slot_half_width(W, 6.0, port._metal_index("AP"), ctx)
    pitch = 12.0
    assert G >= pitch / 2.0 + W / 2.0 - 1e-9


def test_tw_leg_waypoints_straight_run_covers_pad_half_width():
    """02c: the leg's own straight-run length (G - pitch/2) must be >= the
    endpoint via pad's own tangential half-width (W/2) -- otherwise the
    pad's W x W footprint spills past the straight run into the diagonal
    portion, only partially overlapping the conductor it's centred on."""
    ctx = port.process_rule_context("n28_1p10m")
    W = 6.0
    G = port._tw_slot_half_width(W, 6.0, port._metal_index("AP"), ctx)
    H = [200.0, 188.0, 176.0]  # pitch = H[0]-H[1] = 12.0 == W+S
    seg = port.TwLeg(boundary=1, direction="desc", angle=0, dive=True)
    pts = port._tw_leg_waypoints(seg, H, G, False)
    p0, p1 = pts[0], pts[1]
    straight_len = abs(p1[1] - p0[1])  # tangential axis for angle=0 is y
    assert straight_len >= W / 2.0 - 1e-9


def test_tw_port_stub_inner_edge_gap_equals_opening():
    """02c OPENING semantics (family/replica v4 口径): the P1/P2 (or
    N1/N2) stubs' own tangential centreline sits at +-(OPENING/2 + W/2),
    so their INNER edges (facing the coil centre) are exactly +-OPENING/2
    apart -- a total inner-edge gap of OPENING. Ticket 01/02's own
    g=OPENING+W/2 formula put the inner edges at +-OPENING, a gap of
    2*OPENING -- double what the family/replica convention intends."""
    OPENING_P, OPENING_N, W = 10.0, 6.0, 4.0
    cell = _tw(OPENING_P=OPENING_P, OPENING_N=OPENING_N, W=W, S=2.0)
    p = {q["logical_name"]: tuple(q["label_xy_um"]) for q in cell.emx_ports}
    # the tip sits on the stub's own centreline (a purely radial run from
    # the ring out to the tip never changes the tangential coordinate)
    p1_x, p2_x = p["P1"][0], p["P2"][0]
    n1_x, n2_x = p["N1"][0], p["N2"][0]
    assert p1_x == pytest.approx(OPENING_P / 2.0 + W / 2.0)
    assert p2_x == pytest.approx(-(OPENING_P / 2.0 + W / 2.0))
    assert n1_x == pytest.approx(OPENING_N / 2.0 + W / 2.0)
    assert n2_x == pytest.approx(-(OPENING_N / 2.0 + W / 2.0))
    p_inner_gap = (p1_x - W / 2.0) - (p2_x + W / 2.0)
    n_inner_gap = (n1_x - W / 2.0) - (n2_x + W / 2.0)
    assert p_inner_gap == pytest.approx(OPENING_P)
    assert n_inner_gap == pytest.approx(OPENING_N)


def test_tw_port_stub_merges_flush_with_arc_as_one_path():
    """02c: the P1 port stub and ring-0's own P1-side arc hop must render
    as ONE continuous wide path (tip -> ring-attach -> ...chamfer
    vertices...), not two independently-drawn rectangles that only touch
    at a corner. Ground truth is built HERE from the same primitives the
    render path itself uses (_tw_edge_point / _tw_oct_walk_pts /
    _tw_add_wide_path) -- an independent re-derivation, not a copy of the
    implementation -- then compared by exact region XOR against whatever
    the actual SL_ME shapes in the rendered ``pri`` sub-cell cover in that
    same footprint. Fails red under ticket 01/02's split-rectangle design:
    that pair's own union leaves a corner-only overlap (measured
    independently: 4.0 um^2 actual vs 16.0 um^2 for a flush W x W overlap),
    which XORs non-empty against the ideal single-path footprint."""
    OD, W, S, NR, OPENING_P, LEAD, sl = 200.0, 4.0, 2.0, 3, 10.0, 20.0, 9
    cell = _tw(OD=OD, W=W, S=S, NR=NR, OPENING_P=OPENING_P, OPENING_N=OPENING_P,
               LEAD=LEAD, SL_ME=str(sl))
    pri, _sec = _tw_pri_sec(cell)

    H = [(OD - W) / 2.0 - i * (W + S) for i in range(NR)]
    G = port._tw_slot_half_width(W, S, sl, None)
    p_segments, _s_segments = port._tw_plan(NR)
    first = p_segments[0]
    port_start_g = OPENING_P / 2.0 + W / 2.0
    attach = port._tw_edge_point(H[0], first.angle_from, port_start_g)
    ux, uy = port._TW_CARDINAL_UNIT[first.angle_from]
    tip = (attach[0] + LEAD * ux, attach[1] + LEAD * uy)
    nxt = p_segments[1]
    nxt_start, _nxt_end = port._tw_leg_endpoints(nxt, H, G, False)
    _, BA = port._tw_oct_chamfer(H[first.ring])
    arc_pts = port._tw_oct_walk_pts(H[first.ring], BA, first.angle_from, attach,
                                    first.angle_to, nxt_start, first.ccw)
    ideal_cell = port.Cell("ideal", "ideal", {})
    port._tw_add_wide_path(ideal_cell, port.metal_layer(sl), [tip] + arc_pts, W)
    ideal_poly = kdb.Polygon(
        [kdb.Point(x, y) for x, y in ideal_cell.shapes[0].points_nm]
    )
    ideal_region = kdb.Region([ideal_poly])

    actual = kdb.Region()
    for shp in pri.shapes:
        if shp.layer != port.metal_layer(sl):
            continue
        poly = kdb.Polygon([kdb.Point(x, y) for x, y in shp.points_nm])
        if not (kdb.Region([poly]) & ideal_region).is_empty():
            actual.insert(poly)
    actual.merge()
    xor = actual ^ ideal_region
    assert xor.is_empty(), (
        f"P1 arc+stub footprint (area {actual.area() * 1e-6:.3f} um^2) "
        f"does not match the ideal single-path joint (area "
        f"{ideal_region.area() * 1e-6:.3f} um^2); XOR area "
        f"{xor.area() * 1e-6:.3f} um^2"
    )


def test_tw_nr_even_fails_closed():
    with pytest.raises(port.PortError, match="NR=4"):
        _tw(NR=4)


def test_tw_nr_below_3_fails_closed():
    with pytest.raises(port.PortError, match="NR=1"):
        _tw(NR=1)


def test_tw_sl_me_too_low_fails_closed():
    with pytest.raises(port.PortError, match="SL_ME"):
        _tw(SL_ME="2")


def test_tw_sl_me_floor_builds():
    # SL_ME-1 == M2 is the documented floor -- must still build.
    cell = _tw(SL_ME="3")
    assert cell is not None


def test_tw_opening_p_over_bound_fails_closed():
    with pytest.raises(port.PortError, match="OPENING"):
        _tw(OPENING_P=90.0)


def test_tw_opening_n_over_bound_fails_closed():
    with pytest.raises(port.PortError, match="OPENING"):
        _tw(OPENING_N=90.0)


def test_tw_innermost_ring_slot_too_tight_fails_closed():
    with pytest.raises(port.PortError, match="innermost ring"):
        _tw(NR=11, OD=80.0)


def test_tw_port_order_override_rejected():
    with pytest.raises(port.PortError, match="fixed port names"):
        _tw(port_order=["A1", "A2", "A3", "A4"])


def test_tw_port_order_canonical_value_accepted():
    cell = _tw(port_order=["P1", "N1", "P2", "N2"])
    assert [p["name"] for p in cell.emx_ports] == ["P1", "N1", "P2", "N2"]


def test_tw_ports_fixed_names_and_positions():
    """P1/P2 at the bottom (y=-H0), N1/N2 at the top (y=+H0); P1/N1 on the
    +x side, P2/N2 on the -x side (spec.md's fixed port contract)."""
    cell = _tw()
    by_name = {p["name"]: p["label_xy_um"] for p in cell.emx_ports}
    assert set(by_name) == {"P1", "N1", "P2", "N2"}
    assert by_name["P1"][0] > 0 and by_name["P1"][1] < 0
    assert by_name["P2"][0] < 0 and by_name["P2"][1] < 0
    assert by_name["N1"][0] > 0 and by_name["N1"][1] > 0
    assert by_name["N2"][0] < 0 and by_name["N2"][1] > 0
    # P1/P2 share the same bottom y; N1/N2 share the same top y.
    assert by_name["P1"][1] == by_name["P2"][1]
    assert by_name["N1"][1] == by_name["N2"][1]


def test_tw_ports_semantic_names_and_emx_lines():
    cell = _tw()
    assert [(p["name"], p["logical_name"]) for p in cell.emx_ports] == [
        ("P1", "P1"), ("N1", "N1"), ("P2", "P2"), ("N2", "N2")]


def _tw_pri_sec(cell):
    return cell.insts[0].cell, cell.insts[1].cell


def _tw_sl_region(subcell, sl):
    reg = kdb.Region()
    for lay, pts in subcell.flat_shapes():
        if lay == port.metal_layer(sl):
            reg.insert(kdb.Polygon([kdb.Point(x, y) for x, y in pts]))
    return reg


def _tw_sl1_region(subcell, sl):
    reg = kdb.Region()
    for lay, pts in subcell.flat_shapes():
        if lay == port.metal_layer(sl - 1):
            reg.insert(kdb.Polygon([kdb.Point(x, y) for x, y in pts]))
    return reg


_TW_CLEAN_MATRIX = [(3, "9"), (3, "10"), (5, "9"), (5, "10"), (7, "9"), (7, "10")]


@pytest.mark.parametrize("NR,SL_ME", _TW_CLEAN_MATRIX)
def test_tw_invariant1_series_p1_n1_p2_n2_different_sl_components(NR, SL_ME):
    """Invariant 1 (spec.md): removing SL_ME-1 (every dive leg) and looking
    at SL_ME alone, P1 and N1 must land on DIFFERENT components (P is a
    series winding, not a shorted loop) -- likewise P2/N2."""
    cell = _tw_nr(NR, SL_ME=SL_ME)
    sl = port._metal_index(SL_ME)
    pri, sec = _tw_pri_sec(cell)
    p = {q["logical_name"]: tuple(q["label_xy_um"]) for q in cell.emx_ports}

    def comp_index(subcell, x_um, y_um):
        reg = _tw_sl_region(subcell, sl)
        reg.merge()
        comps = list(reg.each())
        pt = kdb.Point(port._nm(x_um), port._nm(y_um))
        probe = kdb.Region(kdb.Box(pt - kdb.Vector(1, 1), pt + kdb.Vector(1, 1)))
        for i, c in enumerate(comps):
            if not (kdb.Region(c) & probe).is_empty():
                return i
        return None

    p1_idx = comp_index(pri, *p["P1"])
    n1_idx = comp_index(pri, *p["N1"])
    assert p1_idx is not None and n1_idx is not None
    assert p1_idx != n1_idx, "P1 and N1 land on the same SL-only component"

    p2_idx = comp_index(sec, *p["P2"])
    n2_idx = comp_index(sec, *p["N2"])
    assert p2_idx is not None and n2_idx is not None
    assert p2_idx != n2_idx, "P2 and N2 land on the same SL-only component"


@pytest.mark.parametrize("NR,SL_ME", _TW_CLEAN_MATRIX)
def test_tw_invariant2_no_closed_loop_holes(NR, SL_ME):
    """Invariant 2 (spec.md): every SL-only connected component of either
    winding must have zero holes -- a closed ring is a shorted turn."""
    cell = _tw_nr(NR, SL_ME=SL_ME)
    sl = port._metal_index(SL_ME)
    pri, sec = _tw_pri_sec(cell)
    for label, sub in (("P", pri), ("S", sec)):
        reg = _tw_sl_region(sub, sl)
        reg.merge()
        for i, comp in enumerate(reg.each()):
            assert comp.holes() == 0, (
                f"NR={NR} SL_ME={SL_ME}: {label} SL-only component {i} has "
                f"{comp.holes()} hole(s)"
            )


@pytest.mark.parametrize("NR,SL_ME", _TW_CLEAN_MATRIX)
def test_tw_invariant3_dive_legs_disjoint_per_x_and_layer(NR, SL_ME):
    """Invariant 3 (spec.md): SL_ME-1 dive legs of P and S never intersect
    each other, and (structural corollary of the plan) every boundary
    contributes exactly one dive leg per winding footprint on SL_ME-1 --
    i.e. P's and S's SL_ME-1 footprints are each individually non-empty
    (there ARE dive legs) and disjoint from one another."""
    cell = _tw_nr(NR, SL_ME=SL_ME)
    sl = port._metal_index(SL_ME)
    pri, sec = _tw_pri_sec(cell)
    p_sl1 = _tw_sl1_region(pri, sl)
    s_sl1 = _tw_sl1_region(sec, sl)
    assert not p_sl1.is_empty() and not s_sl1.is_empty()
    assert (p_sl1 & s_sl1).is_empty(), (
        f"NR={NR} SL_ME={SL_ME}: P's and S's SL_ME-1 dive-leg footprints "
        "intersect"
    )
    # every X has exactly one dive leg: P + S dive-leg count == 2*(NR-1)
    # (spec.md: "下潜段数恒 P/S 平衡（各 NR-1）"), verified at the plan level
    # already by test_tw_plan_dive_leg_count_is_nr_minus_1_each; here we
    # additionally confirm SL_ME (the OTHER layer) is never empty either,
    # i.e. every boundary's OTHER leg (the same-layer half) is really drawn.
    assert not _tw_sl_region(pri, sl).is_empty()
    assert not _tw_sl_region(sec, sl).is_empty()


@pytest.mark.parametrize("NR,SL_ME", _TW_CLEAN_MATRIX)
def test_tw_invariant4_via_aware_net_count_is_two(NR, SL_ME, tmp_path):
    """Invariant 4 (spec.md): a full via-aware klayout LayoutToNetlist
    extraction (connect set = SL_ME, SL_ME-1 metal + the via class between
    them) must find EXACTLY 2 nets (P and S), with P1/N1 on one net and
    P2/N2 on the other, never shorted together."""
    cell = _tw_nr(NR, SL_ME=SL_ME)
    sl = port._metal_index(SL_ME)
    gds = tmp_path / f"xfm_tw_l2n_{NR}_{SL_ME}.gds"
    port.write_gds(cell, gds)
    ly = kdb.Layout()
    ly.read(str(gds))
    top = ly.top_cell()
    top.flatten(True)

    def region_for(layer_tuple):
        li = ly.find_layer(*layer_tuple)
        if li is None:
            return kdb.Region()
        return kdb.Region(top.begin_shapes_rec(li))

    r_sl = region_for(port.metal_layer(sl))
    r_sl1 = region_for(port.metal_layer(sl - 1))
    r_via = region_for(port.via_layer(sl - 1))
    assert not r_sl.is_empty() and not r_sl1.is_empty() and not r_via.is_empty()

    l2n = kdb.LayoutToNetlist(top.name, ly.dbu)
    l2n.register(r_sl, "SL")
    l2n.register(r_sl1, "SL1")
    l2n.register(r_via, "VIA")
    l2n.connect(r_sl)
    l2n.connect(r_sl1)
    l2n.connect(r_via)
    l2n.connect(r_sl, r_via)
    l2n.connect(r_sl1, r_via)
    l2n.extract_netlist()
    circuit = l2n.netlist().circuit_by_name(top.name)
    nets = list(circuit.each_net())
    assert len(nets) == 2, (
        f"NR={NR} SL_ME={SL_ME}: via-aware extraction found {len(nets)} "
        f"nets, expected exactly 2 (P and S)"
    )

    # P1/N1 must probe to the same net, P2/N2 to the same net, and the two
    # nets must differ -- probed directly on the SL_ME layer at each port's
    # own label point (every port sits on SL_ME by construction).
    p = {q["logical_name"]: tuple(q["label_xy_um"]) for q in cell.emx_ports}
    sl_layer_idx = l2n.layer_by_name("SL")

    def probe(label):
        # klayout's Net wrapper has no __eq__ (Python identity only, even
        # for the same underlying net -- two probe_net calls at the same
        # point return unequal-by-== objects), so compare cluster_id, the
        # net's own stable identity, instead of the Net object itself.
        x_um, y_um = p[label]
        net = l2n.probe_net(sl_layer_idx, kdb.Point(port._nm(x_um), port._nm(y_um)))
        assert net is not None, f"{label}: probe_net found no net at {(x_um, y_um)}"
        return net.cluster_id

    p1_net, n1_net = probe("P1"), probe("N1")
    p2_net, n2_net = probe("P2"), probe("N2")
    assert p1_net == n1_net, "P1 and N1 are on different nets"
    assert p2_net == n2_net, "P2 and N2 are on different nets"
    assert p1_net != p2_net, "P and S nets are shorted together"


def test_tw_function_mapping_entry_present():
    """FUNCTION_MAPPING red line (ticket 01): xfm_tw must be listed with a
    provenance note and a parameter list matching its own signature."""
    mapping = {e["python_function"]: e for e in port.FUNCTION_MAPPING}
    assert "xfm_tw" in mapping
    entry = mapping["xfm_tw"]
    assert entry["parameters"] == [
        "OD", "W", "S", "NR", "OPENING_P", "OPENING_N", "LEAD", "SL_ME",
        "dummy", "DUMMYL", "port_order",
    ]
    assert entry["pcell_source"]


# ---------------------------------------------------------------------------
# xfm_tw N28 domain (ticket 02 -- .scratch/xfm-tw-twisted/issues/
# 02-n28-domain-sample-gallery.md; see spec.md's own "N28 域" section).
# Unaffected by n28-rules-slim (user directive 2026-07-19): xfm_tw never
# touched a via_restrictions-cited via in the first place, so its own N28
# body matrix (below) is a pure regression, byte-for-byte unchanged.
#
# spec.md's N28 域 note: xfm_tw's connectivity model (ticket 01) only ever
# needs ONE dive layer per crossing (SL_ME-1) -- unlike xfm_il, whose leg2
# needs a SECOND dive layer (SL_ME-2) and, at SL_ME="9", therefore crosses
# VIA7 (M7<->M8) as well as VIA8 (M8<->M9). Both xfm_il's M9 body and
# xfm_tw's M9 body build under N28 process mode (VIA7, like every via down
# through ODCONT/POLYCONT, has complete via_primitives geometry in
# n28_1p10m; generator enforcement is geometric-only, so a lower via class
# no longer fails closed on its own -- see
# test_xfm_il_n28_sl_m9_process_mode_builds_and_on_grid above). The two
# devices' N28 domains are therefore no longer differentiated by DRC
# legality, only by geometry: xfm_il crosses two via levels per boundary
# (VIA8 then VIA7) against xfm_tw's one (VIA8 only), so xfm_il's
# per-boundary via-stack footprint is larger, not structurally forbidden.
# This is confirmed below by the SAME plumbing (_tw_slot_half_width's own
# ring-pitch guard, vias()'s passive-array planner), not a new
# xfm_tw-specific special case.
#
# Body dimensions (validator's own N28 pre-scan, reproduced here and by
# .scratch/xfm-tw-twisted/samples/gen_samples.py): AP's rule min_space is
# 2.0 um (process_data/profiles/n28_1p10m/rule.yaml), far above M9/M10's
# 1.0 um, so the AP body needs looser W=6/S=6 dimensions to clear
# _tw_slot_half_width's ring-pitch guard (pitch=W+S=12.0 > K=W+min_space=8.0)
# where the tight W=4/S=2 the M10/M9 bodies use (pitch=6.0 > K=5.0 there,
# but pitch=6.0 <= K=6.0 for AP -- exactly the boundary the rejection test
# below pins down) would fail closed.
# ---------------------------------------------------------------------------


def test_tw_n28_ap_body_ring_pitch_guard_equality_and_shortfall():
    """AP body (SL_ME="AP", dives to M10) around the ring-pitch guard's
    boundary. S == min_space (W=4/S=2: pitch=6.0 == K=W+AP min_space=6.0)
    is a passing DRC equality -- spacing exactly at the rule is legal --
    so since the six-family tight-spacing-clearance equality fix the
    build must PROCEED (the guard used to reject it too). A STRICT
    shortfall (S=1.5: pitch=5.5 < 6.0) still fails closed, naming the
    pitch value and the clearance it fails to clear."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.xfm_tw(OD=260.0, W=4.0, S=2.0, NR=3, OPENING_P=10.0,
                       OPENING_N=10.0, LEAD=20.0, SL_ME="AP", process=ctx)
    assert cell is not None
    with pytest.raises(port.PortError) as excinfo:
        port.xfm_tw(OD=260.0, W=4.0, S=1.5, NR=3, OPENING_P=10.0,
                    OPENING_N=10.0, LEAD=20.0, SL_ME="AP", process=ctx)
    msg = str(excinfo.value)
    assert "ring pitch 5.500" in msg
    assert "does not clear" in msg
    assert "min_space=6.000" in msg


# (body label, SL_ME, NR, OD, W, S, expected total flattened shape count).
# Shape counts pinned from a fresh independent build (write_gds + KLayout
# parse, summed across every layer) -- an exact-count regression guard, not
# just an order-of-magnitude sanity check.
#
# Ticket 02c note: every count here is exactly 4 LOWER than ticket 02b's own
# pinned values (46->42, 86->82, 166->162, 326->322). This is the expected,
# re-measured consequence of retiring the standalone ``_tw_port_lead``: the
# 4 port stubs (P1/N1 on `pri`, P2/N2 on `sec`) are no longer their own
# Shape entries -- each is now merged into the SAME ``_tw_add_wide_path``
# call as the ring-0 arc it attaches to (``_tw_render_winding``), so total
# polygon COUNT drops by exactly 4 (one per port) while the device's own
# topology/shape is unchanged. Re-verified via a fresh independent build
# (write_gds + KLayout parse) after the ticket 02c rework, not hand-derived.
_TW_N28_BODY_MATRIX = [
    ("ap", "AP", 3, 260.0, 6.0, 6.0, 42),
    ("ap", "AP", 5, 340.0, 6.0, 6.0, 82),
    ("m10", "10", 3, 200.0, 4.0, 2.0, 162),
    ("m10", "10", 5, 230.0, 4.0, 2.0, 322),
    ("m9", "9", 3, 200.0, 4.0, 2.0, 162),
    ("m9", "9", 5, 230.0, 4.0, 2.0, 322),
]


@pytest.mark.parametrize(
    "body,sl_me,nr,od,w,s,n_shapes", _TW_N28_BODY_MATRIX,
    ids=[f"{b}_nr{nr}" for b, _, nr, *_ in _TW_N28_BODY_MATRIX],
)
def test_tw_n28_body_builds_on_grid_and_no_reference_leak(
    body, sl_me, nr, od, w, s, n_shapes, tmp_path
):
    """Every constructible N28 body (AP/M10/M9 dive-target, NR in {3, 5})
    builds, stays on the 0.005 um mask grid, draws only rule-profile
    layer/datatype pairs (never the reference-mode via layer -- the family's
    own "must not leak into N28 mode" convention, see
    test_n28_process_mode_uses_via8_rule_cut_size above), and matches the
    exact flattened shape count from an independent pre-scan."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.xfm_tw(OD=od, W=w, S=s, NR=nr, OPENING_P=10.0,
                       OPENING_N=10.0, LEAD=20.0, SL_ME=sl_me, process=ctx)
    layers = write_and_parse(cell, tmp_path, f"xfm_tw_n28_{body}_nr{nr}")
    sl = port._metal_index(sl_me)
    assert port.process_metal_layer(ctx, sl) in layers
    assert port.process_metal_layer(ctx, sl - 1) in layers
    assert port.process_via_layer(ctx, sl - 1) in layers
    assert port.via_layer(sl - 1) not in layers, (
        "reference via layer must not leak into N28 mode"
    )
    assert_on_grid(layers)
    assert sum(len(polys) for polys in layers.values()) == n_shapes


_TW_N28_BODIES = [
    ("ap", "AP", 260.0, 6.0, 6.0),
    ("m10", "10", 200.0, 4.0, 2.0),
    ("m9", "9", 200.0, 4.0, 2.0),
]


def _tw_n28(ctx, sl_me, od, w, s, nr=3, **kw):
    base = dict(OD=od, W=w, S=s, NR=nr, OPENING_P=10.0, OPENING_N=10.0,
                LEAD=20.0, SL_ME=sl_me, process=ctx)
    base.update(kw)
    return port.xfm_tw(**base)


def _tw_n28_metal_region(subcell, ctx, met):
    reg = kdb.Region()
    layer = port.process_metal_layer(ctx, met)
    for lay, pts in subcell.flat_shapes():
        if lay == layer:
            reg.insert(kdb.Polygon([kdb.Point(x, y) for x, y in pts]))
    return reg


@pytest.mark.parametrize(
    "body,sl_me,od,w,s", _TW_N28_BODIES,
    ids=[b for b, *_ in _TW_N28_BODIES],
)
def test_tw_n28_invariant1_series_p1_n1_p2_n2_different_sl_components(
    body, sl_me, od, w, s
):
    """N28-mode invariant 1 (process_metal_layer mapping, spec.md's
    generic-mode invariant 1 above): removing SL_ME-1 leaves P1/N1 and
    P2/N2 on different SL-only components."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = _tw_n28(ctx, sl_me, od, w, s)
    sl = port._metal_index(sl_me)
    pri, sec = _tw_pri_sec(cell)
    p = {q["logical_name"]: tuple(q["label_xy_um"]) for q in cell.emx_ports}

    def comp_index(subcell, x_um, y_um):
        reg = _tw_n28_metal_region(subcell, ctx, sl)
        reg.merge()
        comps = list(reg.each())
        pt = kdb.Point(port._nm(x_um), port._nm(y_um))
        probe = kdb.Region(kdb.Box(pt - kdb.Vector(1, 1), pt + kdb.Vector(1, 1)))
        for i, c in enumerate(comps):
            if not (kdb.Region(c) & probe).is_empty():
                return i
        return None

    p1_idx = comp_index(pri, *p["P1"])
    n1_idx = comp_index(pri, *p["N1"])
    assert p1_idx is not None and n1_idx is not None
    assert p1_idx != n1_idx, "P1 and N1 land on the same SL-only component"

    p2_idx = comp_index(sec, *p["P2"])
    n2_idx = comp_index(sec, *p["N2"])
    assert p2_idx is not None and n2_idx is not None
    assert p2_idx != n2_idx, "P2 and N2 land on the same SL-only component"


@pytest.mark.parametrize(
    "body,sl_me,od,w,s", _TW_N28_BODIES,
    ids=[b for b, *_ in _TW_N28_BODIES],
)
def test_tw_n28_invariant2_no_closed_loop_holes(body, sl_me, od, w, s):
    """N28-mode invariant 2: every SL-only component of either winding has
    zero holes (no shorted closed ring)."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = _tw_n28(ctx, sl_me, od, w, s)
    sl = port._metal_index(sl_me)
    pri, sec = _tw_pri_sec(cell)
    for label, sub in (("P", pri), ("S", sec)):
        reg = _tw_n28_metal_region(sub, ctx, sl)
        reg.merge()
        for i, comp in enumerate(reg.each()):
            assert comp.holes() == 0, (
                f"body={body}: {label} SL-only component {i} has "
                f"{comp.holes()} hole(s)"
            )


@pytest.mark.parametrize(
    "body,sl_me,od,w,s", _TW_N28_BODIES,
    ids=[b for b, *_ in _TW_N28_BODIES],
)
def test_tw_n28_invariant3_dive_legs_disjoint_per_x_and_layer(
    body, sl_me, od, w, s
):
    """N28-mode invariant 3: P's and S's SL_ME-1 dive-leg footprints are
    each non-empty and mutually disjoint; SL_ME itself is non-empty for
    both windings too (the paired same-layer leg at every boundary)."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = _tw_n28(ctx, sl_me, od, w, s)
    sl = port._metal_index(sl_me)
    pri, sec = _tw_pri_sec(cell)
    p_sl1 = _tw_n28_metal_region(pri, ctx, sl - 1)
    s_sl1 = _tw_n28_metal_region(sec, ctx, sl - 1)
    assert not p_sl1.is_empty() and not s_sl1.is_empty()
    assert (p_sl1 & s_sl1).is_empty(), (
        f"body={body}: P's and S's SL_ME-1 dive-leg footprints intersect"
    )
    assert not _tw_n28_metal_region(pri, ctx, sl).is_empty()
    assert not _tw_n28_metal_region(sec, ctx, sl).is_empty()


@pytest.mark.parametrize(
    "body,sl_me,od,w,s", _TW_N28_BODIES,
    ids=[b for b, *_ in _TW_N28_BODIES],
)
def test_tw_n28_invariant4_via_aware_net_count_is_two(
    body, sl_me, od, w, s, tmp_path
):
    """N28-mode invariant 4: a full via-aware klayout LayoutToNetlist
    extraction (SL_ME + SL_ME-1 metal + the rule-profile via class between
    them) finds exactly 2 nets, with P1/N1 on one and P2/N2 on the other."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = _tw_n28(ctx, sl_me, od, w, s)
    sl = port._metal_index(sl_me)
    gds = tmp_path / f"xfm_tw_n28_l2n_{body}.gds"
    port.write_gds(cell, gds)
    ly = kdb.Layout()
    ly.read(str(gds))
    top = ly.top_cell()
    top.flatten(True)

    def region_for(layer_tuple):
        li = ly.find_layer(*layer_tuple)
        if li is None:
            return kdb.Region()
        return kdb.Region(top.begin_shapes_rec(li))

    r_sl = region_for(port.process_metal_layer(ctx, sl))
    r_sl1 = region_for(port.process_metal_layer(ctx, sl - 1))
    r_via = region_for(port.process_via_layer(ctx, sl - 1))
    assert not r_sl.is_empty() and not r_sl1.is_empty() and not r_via.is_empty()

    l2n = kdb.LayoutToNetlist(top.name, ly.dbu)
    l2n.register(r_sl, "SL")
    l2n.register(r_sl1, "SL1")
    l2n.register(r_via, "VIA")
    l2n.connect(r_sl)
    l2n.connect(r_sl1)
    l2n.connect(r_via)
    l2n.connect(r_sl, r_via)
    l2n.connect(r_sl1, r_via)
    l2n.extract_netlist()
    circuit = l2n.netlist().circuit_by_name(top.name)
    nets = list(circuit.each_net())
    assert len(nets) == 2, (
        f"body={body}: via-aware extraction found {len(nets)} nets, "
        f"expected exactly 2 (P and S)"
    )

    p = {q["logical_name"]: tuple(q["label_xy_um"]) for q in cell.emx_ports}
    sl_layer_idx = l2n.layer_by_name("SL")

    def probe(label):
        x_um, y_um = p[label]
        net = l2n.probe_net(sl_layer_idx, kdb.Point(port._nm(x_um), port._nm(y_um)))
        assert net is not None, f"{label}: probe_net found no net at {(x_um, y_um)}"
        return net.cluster_id

    p1_net, n1_net = probe("P1"), probe("N1")
    p2_net, n2_net = probe("P2"), probe("N2")
    assert p1_net == n1_net, "P1 and N1 are on different nets"
    assert p2_net == n2_net, "P2 and N2 are on different nets"
    assert p1_net != p2_net, "P and S nets are shorted together"


# ---------------------------------------------------------------------------
# _render_png AP/RV legend name resolution (ticket 04 --
# .scratch/xfm-tw-twisted/issues/04-docs-gallery-renderer.md, the gap ticket
# 02 flagged and deferred: the N28 AP body's AP conductor and its AP<->M10
# via ("RV") used to fall to the generic "?" legend label, because their
# gds layer numbers -- unlike every M1-M10/VIA1-VIA9 layer, reference or
# process mode -- fall outside the 31-40 (metal) / 51-59 (via) generic
# ranges _layer_display_name guesses names from. The fix reads names from
# the process rule profile's own layer catalog at render time, through the
# existing ProcessRuleContext/GeometryRuleAdapter API
# (``_process_layer_names``) -- no N28 layer number is ever written into
# this module (M11 IP-strip audit); every literal below is read back from
# the profile through that same API, never hand-typed.
# ---------------------------------------------------------------------------


def test_layer_display_name_ap_rv_fall_to_unknown_without_process():
    """Baseline gap (still true with no name_map, by design -- "no profile
    info -> stays the '?' fallback, no regression"): AP/RV's gds layer
    numbers sit outside the generic 31-40/51-59 ranges, so with no
    name_map they resolve to "?", same as any other unrecognized layer."""
    ctx = port.process_rule_context("n28_1p10m")
    ap_layer = port.process_metal_layer(ctx, 11)
    rv_layer = port.process_via_layer(ctx, 10)
    assert port._layer_display_name(ap_layer) == "?"
    assert port._layer_display_name(rv_layer) == "?"


def test_process_layer_names_resolves_ap_and_rv_from_profile():
    """_process_layer_names reads the profile's own layer catalog (via the
    existing ProcessRuleContext/GeometryRuleAdapter API) and resolves AP's
    conductor name and the AP<->M10 via's name ("RV") entirely from data,
    never from a name baked into this module."""
    ctx = port.process_rule_context("n28_1p10m")
    names = port._process_layer_names(ctx)
    ap_layer = port.process_metal_layer(ctx, 11)
    rv_layer = port.process_via_layer(ctx, 10)
    assert names[ap_layer] == "AP"
    assert names[rv_layer] == "RV"


def test_layer_display_name_prefers_generic_pattern_over_name_map():
    """A name_map must not change the pre-existing M1-M10/via1-9
    resolution (reference or process mode, both land in the generic
    31-40/51-59 ranges) -- name_map is only a last-resort fallback, so the
    already-working M9/M10/M8-body legends render exactly as before
    whether or not a process context is supplied (no regression there)."""
    ctx = port.process_rule_context("n28_1p10m")
    names = port._process_layer_names(ctx)
    m9_layer = port.process_metal_layer(ctx, 9)
    via8_layer = port.process_via_layer(ctx, 8)
    assert port._layer_display_name(m9_layer, names) == "M9"
    assert port._layer_display_name(via8_layer, names) == "via8"


def test_layer_display_name_ap_rv_resolve_with_process_name_map():
    ctx = port.process_rule_context("n28_1p10m")
    names = port._process_layer_names(ctx)
    ap_layer = port.process_metal_layer(ctx, 11)
    rv_layer = port.process_via_layer(ctx, 10)
    assert port._layer_display_name(ap_layer, names) == "AP"
    assert port._layer_display_name(rv_layer, names) == "RV"


def test_render_legend_label_ap_rv_named_with_process():
    """The exact per-layer legend-label seam _render_png draws its legend
    entries from, exercised the way _render_png itself calls it -- not by
    reading PNG pixels (avoids a brittle image-diff test)."""
    ctx = port.process_rule_context("n28_1p10m")
    names = port._process_layer_names(ctx)
    ap_layer = port.process_metal_layer(ctx, 11)
    rv_layer = port.process_via_layer(ctx, 10)
    assert (
        port._render_legend_label(ap_layer, names)
        == f"AP ({ap_layer[0]}/{ap_layer[1]})"
    )
    assert (
        port._render_legend_label(rv_layer, names)
        == f"RV ({rv_layer[0]}/{rv_layer[1]})"
    )
    # no name_map -> unchanged "?" fallback (no-profile-info non-regression)
    assert port._render_legend_label(ap_layer) == f"? ({ap_layer[0]}/{ap_layer[1]})"


def test_render_png_optional_process_param_renders_ap_body(tmp_path):
    """_render_png's new process param is optional (default None, so
    every existing caller -- generate_all, visual_review.py,
    gen_samples.py's old call sites -- keeps working unmodified) and, when
    given, does not raise while rendering an N28 AP-body xfm_tw GDS."""
    ctx = port.process_rule_context("n28_1p10m")
    cell = port.xfm_tw(OD=260.0, W=6.0, S=6.0, NR=3, OPENING_P=10.0,
                        OPENING_N=10.0, LEAD=20.0, SL_ME="AP", process=ctx)
    gds = tmp_path / "xfm_tw_ap_render.gds"
    port.write_gds(cell, gds)

    png_with_process = tmp_path / "xfm_tw_ap_render_with_process.png"
    port._render_png(gds, png_with_process, "xfm_tw AP render smoke", process=ctx)
    assert png_with_process.is_file()

    # unchanged default behaviour: the pre-ticket 3-positional-arg call
    # still works with no process kwarg at all.
    png_no_process = tmp_path / "xfm_tw_ap_render_no_process.png"
    port._render_png(gds, png_no_process, "xfm_tw AP render smoke (no process)")
    assert png_no_process.is_file()


def test_xfm_balun_multi_turn_ct_fails_closed():
    """Wrap-up review: `_add_balun_ct` taps at OD/2 -- the OUTERMOST ring --
    regardless of turn count. For a multi-turn winding that is not the
    electrical midpoint, and the via stack lands on top of the inner turns.
    Refuse rather than emit a silently wrong center tap (mirrors the
    existing `nested and NT_S >= 2` guard)."""
    with pytest.raises(port.PortError, match="single-turn"):
        _balun(NT_P=2, OPENING_S=16.0, CT_P_ME="8")
    with pytest.raises(port.PortError, match="single-turn"):
        _balun(NT_S=2, CT_S_ME="8")
    # single-turn windings keep working with taps (nested: both taps;
    # side by side: one tap, see test_xfm_balun_side_by_side_facing_taps_are_refused)
    cell = _balun(CT_P_ME="8", CT_S_ME="8")
    names = {q["logical_name"] for q in cell.emx_ports}
    assert {"CTP", "CTS"} <= names


# ---------------------------------------------------------------------------
# Port lattice contract (2026-09-21): the port's authoritative coordinate
# comes from the same local integer-nm frame as the primitive that draws
# its lead (.scratch/port-lattice-contract-2026-09-21/spec.md). There is
# exactly one way to create a port (a lead primitive registers it,
# finalize_emx_ports composes it) and the add_emx_port( scan below covers
# all six family files, not a hand-picked subset.
# ---------------------------------------------------------------------------


def _assert_ports_on_lead_zone_tip_edge(cell, *, require_nondegenerate_zone=False):
    """Every finalized port's point_nm sits ON an edge of its own
    lead_zone_nm (not merely inside it), and label_xy_um is that same
    integer re-expressed in microns -- never an independently rounded
    value (the D5 divergence this contract closes).

    ``require_nondegenerate_zone``: a single-point zone satisfies "point
    on an edge" VACUOUSLY (x0==x1==x) without covering the drawn lead, so
    the planar families' call sites (xfm_balun's direct-lead branch and
    crossunder, xfm_il's P1/N1 and CT, xfm_tw's stub) must show a real
    WxL (or WxLEAD) rectangle, the property this flag pins."""
    assert cell.emx_ports
    for p in cell.emx_ports:
        x, y = p["point_nm"]
        x0, y0, x1, y1 = p["lead_zone_nm"]
        assert min(x0, x1) <= x <= max(x0, x1), p
        assert min(y0, y1) <= y <= max(y0, y1), p
        assert x in (x0, x1) or y in (y0, y1), p
        assert p["label_xy_um"] == [
            round(x * port.DBU_UM, 3), round(y * port.DBU_UM, 3)], p
        if require_nondegenerate_zone:
            assert x0 != x1 and y0 != y1, (
                f"{p['name']}: lead_zone_nm={p['lead_zone_nm']} is "
                "degenerate (a single point) -- this port was not "
                "registered through its own primitive's real lead "
                "rectangle (port contract 2026-09-21)"
            )


@pytest.mark.parametrize("extension", [0.0, 12.0])
@pytest.mark.parametrize("with_ct", [False, True])
def test_ind_sym_port_point_on_lead_zone_tip_edge_off_lattice(with_ct, extension):
    """An off-half-nm OD/W/LEAD/OPENING/S combination -- mechanism (1)/(2)'s
    trigger condition -- still lands every port, CT included, exactly on
    its own zone's tip edge, with or without STRAIGHT_EXTENSION."""
    cell = port.ind_sym(OD=54.4855, W=2.015, OPENING=5.005, LEAD=10.045,
                        S=2.005, NT=3, TOP_ME="9", BTM_ME="8",
                        CT_ME=("7" if with_ct else None),
                        STRAIGHT_EXTENSION=extension)
    _assert_ports_on_lead_zone_tip_edge(cell)


@pytest.mark.parametrize("extension", [0.0, 9.0])
@pytest.mark.parametrize("with_ct", [False, True])
def test_xfm_bs_port_point_on_lead_zone_tip_edge_off_lattice(with_ct, extension):
    """CENTER_SPACING=27.485 is the ratio*(od_a+od_b)/4 shape the library
    actually samples (coord-inventory), off the 10 nm lattice by design."""
    cell = port.xfm_bs(
        OD_P=90.0, OD_S=90.0, W_P=4.0, W_S=4.0, OPENING_P=8.0, OPENING_S=8.0,
        LEAD_P=20.0, LEAD_S=20.0, CENTER_SPACING=27.485,
        PRI_ME="10", SEC_ME="9",
        CT_P_ME=("8" if with_ct else None), CT_S_ME=("7" if with_ct else None),
        STRAIGHT_EXTENSION=extension)
    _assert_ports_on_lead_zone_tip_edge(cell)


@pytest.mark.parametrize("extension", [0.0, 8.0])
@pytest.mark.parametrize("with_ct", [False, True])
def test_xfm_ms_port_point_on_lead_zone_tip_edge_off_lattice(with_ct, extension):
    process = port.process_rule_context("n28_1p10m")
    cell = port.xfm_ms(
        OD_S=90.0, OD_M=90.0, W_S=4.0, W_M=4.0, OPENING_S=8.0, OPENING_M=6.0,
        NT_M=3, S_M=2.0, CENTER_SPACING=27.485, SINGLE_ME="AP", MULTI_ME="10",
        CT_P_ME=("8" if with_ct else None), CT_S_ME=("7" if with_ct else None),
        process=process, STRAIGHT_EXTENSION=extension)
    _assert_ports_on_lead_zone_tip_edge(cell)


def _m1_ring_hole_bbox_nm(cell, process):
    """The M1 ground ring is four rectangles that merge into one polygon
    with a hole (the winding-body cutout); that hole's own bbox is the
    ring's inner edge on every side -- read back directly instead of
    re-deriving it from GroundFixtureConfig math a second time."""
    m1_layer = port._metal(1, process)
    region = kdb.Region()
    for layer, pts in cell.flat_shapes():
        if layer == m1_layer:
            region.insert(kdb.Polygon([kdb.Point(x, y) for x, y in pts]))
    region.merge()
    assert region.count() == 1
    (poly,) = list(region.each())
    assert poly.holes() == 1
    xs = [pt.x for pt in poly.each_point_hole(0)]
    ys = [pt.y for pt in poly.each_point_hole(0)]
    return min(xs), min(ys), max(xs), max(ys)


def _ground_ring_regime(cell, process, fixture):
    """('stub'|'margin', 'stub'|'margin') for the left/right ring edges:
    'stub' when the ring sits exactly stub_length_um past that side's
    farthest port (add_ground_fixture's port-driven term winning its own
    max()); 'margin' when it instead sits inner_margin_um past the
    non-port winding body. Read back from the drawn ring, not
    recomputed -- this is the observable a caller (or EMX) would see."""
    ix0, _, ix1, _ = _m1_ring_hole_bbox_nm(cell, process)
    stub_nm = port._nm(fixture.stub_length_um)
    left_x = [p["point_nm"][0] for p in cell.emx_ports if p["point_nm"][0] < 0]
    right_x = [p["point_nm"][0] for p in cell.emx_ports if p["point_nm"][0] > 0]
    assert left_x and right_x, cell.emx_ports
    left = "stub" if ix0 == min(left_x) - stub_nm else "margin"
    right = "stub" if ix1 == max(right_x) + stub_nm else "margin"
    return left, right


# inner_margin_um/stub_length_um are tuned (not arbitrary) so the two
# candidate ring positions land close enough together that a 1 nm port-
# position error -- D5's own scale -- can flip which one a strict max()
# picks; a large stub_length_um/inner_margin_um gap would make the regime
# stub-only regardless of the bug and this pairing test vacuous.
_REGIME_FIXTURE = port.GroundFixtureConfig(
    inner_margin_um=10.0, ring_width_um=3.0, stub_width_um=2.0,
    stub_length_um=1.0, stub_chamfer_um=0.5)


@pytest.mark.parametrize("delta_nm", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("with_ct", [False, True])
def test_xfm_bs_ground_ring_regime_stable_across_nm_perturbation(with_ct, delta_nm):
    """D5's own symptom, isolated (design section 13 test 2): OD_P==OD_S
    rules out the unequal-OD 'margin regime' as an unrelated confound. A
    pair of CENTER_SPACING values 1..5 nm apart must land in the SAME
    regime on each side -- never flip just because one of the two happens
    to sit closer to a half-nm boundary than the other."""
    kwargs = dict(OD_P=90.0, OD_S=90.0, W_P=4.0, W_S=4.0, OPENING_P=8.0,
                  OPENING_S=8.0, LEAD_P=20.0, LEAD_S=20.0,
                  PRI_ME="10", SEC_ME="9",
                  CT_P_ME=("8" if with_ct else None),
                  CT_S_ME=("7" if with_ct else None),
                  ground_fixture=_REGIME_FIXTURE)
    base = port.xfm_bs(CENTER_SPACING=27.485, **kwargs)
    shifted = port.xfm_bs(CENTER_SPACING=27.485 + delta_nm * port.DBU_UM, **kwargs)
    assert (_ground_ring_regime(shifted, None, _REGIME_FIXTURE)
            == _ground_ring_regime(base, None, _REGIME_FIXTURE))


@pytest.mark.parametrize("delta_nm", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("with_ct", [False, True])
def test_xfm_ms_ground_ring_regime_stable_across_nm_perturbation(with_ct, delta_nm):
    process = port.process_rule_context("n28_1p10m")
    kwargs = dict(OD_S=90.0, OD_M=90.0, W_S=4.0, W_M=4.0, OPENING_S=8.0,
                  OPENING_M=6.0, LEAD_S=20.0, LEAD_M=15.0, NT_M=3, S_M=2.0,
                  SINGLE_ME="AP", MULTI_ME="10",
                  CT_P_ME=("8" if with_ct else None),
                  CT_S_ME=("7" if with_ct else None),
                  process=process, ground_fixture=_REGIME_FIXTURE)
    base = port.xfm_ms(CENTER_SPACING=27.485, **kwargs)
    shifted = port.xfm_ms(CENTER_SPACING=27.485 + delta_nm * port.DBU_UM, **kwargs)
    assert (_ground_ring_regime(shifted, process, _REGIME_FIXTURE)
            == _ground_ring_regime(base, process, _REGIME_FIXTURE))


# ---------------------------------------------------------------------------
# Port lattice contract (2026-09-21): the planar families.
# xfm_balun's two remaining pre-contract call sites (_ci_winding's
# direct-lead branch, _balun_crossunder), xfm_il (P1/N1 via
# base_lead_pair, CTP/CTS via _il_ct_tap_exact -> base_lead) and xfm_tw
# (P1/N1/P2/N2 via _tw_render_winding's own wide-path stub, registered
# with a real _tw_stub_zone instead of the pre-contract degenerate point).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("with_ct", [False, True])
def test_xfm_balun_port_point_on_lead_zone_tip_edge_off_lattice(with_ct):
    """Off-half-nm OD/W/LEAD/S/CENTER_SPACING covers xfm_balun's nested
    nt=1 body (default OD_P>OD_S), which exercises `_balun_crossunder`;
    a small off-lattice CENTER_SPACING keeps the secondary nested (the
    former side-by-side mode is gone, 2026-09-22)."""
    kwargs = dict(OD_P=200.045, OD_S=186.045, W_P=5.015, W_S=5.015,
                  S=2.005, OPENING_P=8.005, OPENING_S=8.005,
                  LEAD_P=20.045, LEAD_S=20.045, NT_P=1, NT_S=1,
                  CENTER_SPACING=0.485, BALUN_ME="9")
    if with_ct:
        kwargs.update(CT_P_ME="7", CT_S_ME="6")
    cell = port.xfm_balun(**kwargs)
    _assert_ports_on_lead_zone_tip_edge(cell, require_nondegenerate_zone=True)


@pytest.mark.parametrize("with_ct", [False, True])
def test_xfm_il_port_point_on_lead_zone_tip_edge_off_lattice(with_ct):
    """Off-half-nm OD/W/LEAD/S/OPENING: P1/N1 (`_il_primary_coil`'s
    base_lead_pair) and, when enabled, CTP (upward onto CT_P_ME="10") /
    CTS (downward onto CT_S_ME="6") from `_il_ct_tap_exact` -- the contract's
    own migration to base_lead, replacing the direct vias()+add_emx_port
    pair that was mechanism (2)'s (segmented-sum rounding) own trigger
    shape."""
    cell = port.xfm_il(
        OD=200.045, W=4.015, S=2.005, NT_P=3, NT_S=3,
        OPENING_P=14.005, OPENING_S=14.005, LEAD_P=20.045, LEAD_S=20.045,
        SL_ME="9",
        CT_P_ME=("10" if with_ct else None),
        CT_S_ME=("6" if with_ct else None))
    _assert_ports_on_lead_zone_tip_edge(cell, require_nondegenerate_zone=True)


def test_xfm_tw_port_point_on_lead_zone_tip_edge_off_lattice():
    """Off-half-nm OD/W/LEAD/S/OPENING: all four ports (P1/N1/P2/N2) come
    from `_tw_render_winding`'s own wide-path stub -- the contract registers a
    `_tw_stub_zone` covering only the stub instead of leaving the zone
    degenerate (the pre-contract state, which let `_body_bbox_um`'s
    now-deleted point-in-bbox fallback carry the whole fused ring+stub
    polygon instead)."""
    cell = port.xfm_tw(OD=204.045, W=4.015, S=2.005, NR=3,
                       OPENING_P=14.005, OPENING_N=14.005, LEAD=20.045,
                       SL_ME="9")
    _assert_ports_on_lead_zone_tip_edge(cell, require_nondegenerate_zone=True)


def test_xfm_tw_body_bbox_zone_is_stub_sized():
    """Design document section 10/13 (test plan item 4): `_body_bbox_um`'s
    port-zone subtraction must shrink the drawing bbox by roughly `LEAD`
    on the port-facing (N/S) axis -- never by the ring's own full
    quarter-arc extent, the pre-contract point-in-bbox exclusion's bug
    (spec.md section 10), where a port's zone defaulted to the WHOLE fused
    ring-arc+stub polygon (`_tw_render_winding` draws the stub and ring-0
    arc as one mitred polygon) instead of just the stub, over-shrinking
    the ground fixture by the ring's own radius-scale extent (tens of um,
    independent of LEAD) rather than LEAD itself. A LEAD small relative
    to OD makes the two hypotheses (LEAD-scale vs ring-radius-scale)
    numerically distinguishable: only the correct, stub-only zone stays
    bounded by LEAD."""
    LEAD = 6.0
    cell = port.xfm_tw(OD=200.0, W=4.0, S=2.0, NR=3, OPENING_P=8.0,
                       OPENING_N=8.0, LEAD=LEAD, SL_ME="9")
    draw_xmin, draw_ymin, draw_xmax, draw_ymax = port._drawing_bbox_um(cell)
    body_xmin, body_ymin, body_xmax, body_ymax = port._body_bbox_um(cell, None)
    # Every port is N/S-facing at this OD/NR (xfm_tw's own fixed port
    # layout, test_tw_ports_fixed_names_and_positions elsewhere in this
    # file) -- x is margin-driven and lead-zone subtraction must not touch
    # it at all.
    assert (body_xmin, body_xmax) == (draw_xmin, draw_xmax)
    bottom_shrink = body_ymin - draw_ymin
    top_shrink = draw_ymax - body_ymax
    assert 0.0 < bottom_shrink <= LEAD, (bottom_shrink, LEAD)
    assert 0.0 < top_shrink <= LEAD, (top_shrink, LEAD)


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_tw_stub_zone_matches_algebra_all_four_cardinal_directions(angle):
    """`_tw_stub_zone` (port contract 2026-09-21) must be verified
    for all four cardinal directions -- the design document (section 15,
    "已知局限") admits it was never checked, only sketched. Only angles 90
    (N1/N2, ring-0 END) and 270 (P1/P2, ring-0 START) are ever reachable
    through the public xfm_tw() API (`_tw_plan_p`'s own fixed
    ``angle=270``/``angle_to=90``, verified by
    ``test_tw_ports_fixed_names_and_positions`` elsewhere in this file);
    0/180 never occur for a real port, only for interior boundary-crossing
    legs. This test therefore drives `_tw_stub_zone` directly (by
    EXECUTION, not merely on paper) with the SAME primitives
    `_tw_render_winding` builds `start_tip`/`end_tip` from
    (`_tw_edge_point`/`_TW_CARDINAL_UNIT`) at a synthetic (H, g, LEAD) for
    each of the four angles, independently re-deriving the expected
    rectangle algebraically rather than importing it."""
    H, g, LEAD, W = 80.0, 3.0, 20.0, 4.0
    base = port._tw_edge_point(H, angle, g)
    ux, uy = port._TW_CARDINAL_UNIT[angle]
    tip = (base[0] + LEAD * ux, base[1] + LEAD * uy)
    zone = port._tw_stub_zone(base, tip, W)
    zx0, zy0, zx1, zy1 = (
        port._nm(zone[0]), port._nm(zone[1]),
        port._nm(zone[2]), port._nm(zone[3]),
    )
    bx, by = port._nm(base[0]), port._nm(base[1])
    tx, ty = port._nm(tip[0]), port._nm(tip[1])
    half_w = port._nm(W / 2.0)
    # the tip sits exactly on the zone's OUTER (away-from-ring) edge, and
    # the ring-side edge is flush with `base` -- never past it into the
    # ring arc's own polygon, and never short of it either.
    if ux > 0:
        assert (zx0, zx1) == (bx, tx)
        assert (zy0, zy1) == (by - half_w, by + half_w)
    elif ux < 0:
        assert (zx0, zx1) == (tx, bx)
        assert (zy0, zy1) == (by - half_w, by + half_w)
    elif uy > 0:
        assert (zy0, zy1) == (by, ty)
        assert (zx0, zx1) == (bx - half_w, bx + half_w)
    else:
        assert (zy0, zy1) == (ty, by)
        assert (zx0, zx1) == (bx - half_w, bx + half_w)
    assert tx in (zx0, zx1) or ty in (zy0, zy1)
    # the zone's own width (perpendicular to the stub) is exactly W --
    # neither the ring arc's full chamfer flat nor a wider guess.
    assert (zx1 - zx0 == 2 * half_w) or (zy1 - zy0 == 2 * half_w)


def test_add_emx_port_does_not_populate_emx_ports_directly():
    """Port contract 2026-09-21: deletes add_emx_port's eager
    (transitional) `self.emx_ports.append` -- populating `cell.emx_ports`
    is now exclusively `finalize_emx_ports(cell)`'s job, the 'exactly one
    way to create a port' obligation spec.md sets for this stage.
    add_emx_port still registers the port STRUCTURALLY (`cell.ports`, the
    same list `cell.inst()`'s integer transform chain and
    `finalize_emx_ports` both read)."""
    cell = port.Cell("bare_port_contract_probe", "test", {})
    cell.add_emx_port(
        name="P1", logical_name="P1", metal=9,
        label_layer=port.metal_pin_layer(9), x_um=2.0, y_um=1.0,
        lead_zone_um=(0.0, 0.0, 2.0, 2.0),
    )
    assert cell.emx_ports == []
    assert len(cell.ports) == 1
    assert cell.ports[0].point_nm == (2000, 1000)
    assert cell.ports[0].direction_nm == (1, 0) and cell.ports[0].width_nm == 2000   # M2.1: it faces out of the edge it sits on
    cell.emx_ports = port.finalize_emx_ports(cell)
    assert len(cell.emx_ports) == 1
    assert cell.emx_ports[0]["name"] == "P1"
    assert cell.emx_ports[0]["orientation_deg"] == 0 and cell.emx_ports[0]["width_um"] == 2.0 and cell.emx_ports[0]["pair"] is None


def _add_emx_port_calls_outside(tree: ast.AST, allowed_function: str) -> list[int]:
    """Line numbers of every ``<x>.add_emx_port(`` call in ``tree`` whose
    innermost enclosing function is not ``allowed_function`` (a
    module-level call, if any, counts as outside too) -- the AST-level
    half of xfm_tw's own exception check below."""
    offenders: list[int] = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack: list[str] = []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "add_emx_port":
                current = self.stack[-1] if self.stack else None
                if current != allowed_function:
                    offenders.append(node.lineno)
            self.generic_visit(node)

    _Visitor().visit(tree)
    return offenders


def test_add_emx_port_called_only_from_leaf_primitives():
    """Build-time invariant C (spec.md): no family
    file recomputes a port coordinate in floats anywhere -- add_emx_port(
    is called only where a primitive registers its own lead's port
    (_pcell_core.py's definition, _pcell_primitives.py's base_lead), or,
    for xfm_tw ONLY, inside `_tw_render_winding` itself (xfm_tw has no
    separate rectangular-lead primitive to delegate to: its port sits on
    a vertex of the SAME wide-path polygon the ring arc is drawn from --
    see that function's own docstring). Every OTHER family file
    (_pcell_ind_sym.py/_pcell_xfm_bs.py/_pcell_xfm_ms.py/
    _pcell_xfm_balun.py/_pcell_xfm_il.py) has ZERO occurrences.

    Discovers every family file with a glob against the clean_port
    directory (not a hand-maintained per-stage list) so a future 7th
    family file is covered automatically without editing this test --
    the 'automatic scan' spec.md itself asks for."""
    src = MOD_PATH.parent
    primitive_files = {src / "_pcell_core.py", src / "_pcell_primitives.py"}
    tw_file = src / "_pcell_xfm_tw.py"
    pattern = re.compile(r"\badd_emx_port\(")
    family_files = sorted(
        f for f in src.glob("_pcell_*.py") if f not in primitive_files
    )
    assert len(family_files) >= 6, (
        f"expected at least the six family files, glob found {family_files}"
    )
    for path in family_files:
        text = path.read_text(encoding="utf-8")
        if path == tw_file:
            tree = ast.parse(text, filename=str(path))
            offenders = _add_emx_port_calls_outside(tree, "_tw_render_winding")
            assert not offenders, (
                f"{path} calls add_emx_port( outside _tw_render_winding at "
                f"line(s) {offenders} -- xfm_tw's own exception (port "
                "contract 2026-09-21) is scoped to that one function"
            )
            assert pattern.search(text), (
                f"{path} should still register its port stubs via "
                "add_emx_port( inside _tw_render_winding"
            )
            continue
        assert not pattern.search(text), (
            f"{path} still recomputes a port coordinate directly via "
            "add_emx_port(); route it through base_lead/base_lead_pair's "
            "port_* keywords instead (port contract 2026-09-21)"
        )
    for path in primitive_files:
        assert pattern.search(path.read_text(encoding="utf-8")), (
            f"{path} should still be one of add_emx_port's registration "
            "points"
        )
