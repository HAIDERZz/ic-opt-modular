# Auto-split from pcell_inductor_port_clean.py (wrap-up P1,
# 2026-07-28): verbatim segment move, no behavior change. The
# facade module re-exports every name; see its docstring for
# provenance and the KNOWN_DEVIATIONS record.
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import klayout.db as kdb

from ic_opt.em.pcell import stack as _stack

if TYPE_CHECKING:
    from ic_opt.em.pcell.rule_adapter import GeometryRuleAdapter

GRID_UM = 0.005
DBU_UM = 0.001
PI = 3.141592  # the SKILL sources hardcode this value

# Reconstructed "vias" PCell fill rule (measured in ind_ref.gds).
VIA_CUT_UM = 0.36
VIA_SPACE_UM = 0.34
VIA_ENC_UM = 0.22

_EPS = 1e-9


class PortError(ValueError):
    """Raised when a construction cannot be ported faithfully (fail closed)."""


# ---------------------------------------------------------------------------
# grid helpers (SKILL ceiltogrid / roundtogrid / floortogrid)
# ---------------------------------------------------------------------------


def ceiltogrid(x: float) -> float:
    return math.ceil(x / GRID_UM - _EPS) * GRID_UM


def roundtogrid(x: float) -> float:
    return math.floor(x / GRID_UM + 0.5 + _EPS) * GRID_UM


def floortogrid(x: float) -> float:
    return math.floor(x / GRID_UM + _EPS) * GRID_UM


# ---------------------------------------------------------------------------
# the octagon's quantized chamfer geometry, computed once (M1.1)
#
# Every primitive that draws a ring's 45-degree edges, lands a pad on its
# flats or reasons about the gap between concentric rings' diagonals takes
# A / BA / C from here. Before, nine call sites each quantized their own copy
# of the same formulas and disagreed by grid steps (plan F1/F2, D8).
# ---------------------------------------------------------------------------

SQRT2 = math.sqrt(2.0)
OCT_DIV = 2.0 + SQRT2                    # OD / OCT_DIV is a regular octagon's chamfer projection


@dataclass(frozen=True)
class Chamfer:
    """The 45-degree corner of a W-wide octagon trace (independent of OD)."""

    W: float
    C: float                             # inner-chamfer offset; ceil(W tan(pi/8)) + half a grid: the diagonal is drawn AT LEAST W wide
    C2: float                            # ceil(C / sqrt2): the crossover diagonals' 45-degree junction offset

    @property
    def drawn_width(self) -> float:
        """What the diagonal segment measures: (W + C) / sqrt2 >= W, never W exactly (C is quantized upward)."""
        return (self.W + self.C) / SQRT2


def chamfer(W: float) -> Chamfer:
    C = ceiltogrid(W * math.tan(PI / 8) + 0.005)
    return Chamfer(W, C, ceiltogrid(C / SQRT2))


@dataclass(frozen=True)
class Octagon:
    """One octagon ring of outer diameter OD and trace width W, as ``base_oct_quad`` quantizes it.

    In the ring's own frame (centre at the origin): flats at |x| or |y| = OD/2,
    each flat's half-length is BA, the outer chamfer runs from (BA, OD/2) to
    (OD/2, BA) (A = OD/2 - BA up to quantization), the inner chamfer is offset by
    C + W. ``bias`` pulls BA inward by grid steps (the staircase clearance).
    """

    OD: float
    W: float
    bias: int
    A: float
    BA: float
    C: float

    @property
    def max_opening(self) -> float:
        """Largest OPENING ``base_oct_quad`` still honours as a real gap (above it the opening leg detaches; metal-independent)."""
        return self.BA - self.C

    @property
    def inner_chamfer_intercept(self) -> float:
        """x + y along the inner chamfer edge (first quadrant): the corridor line a landing pad's corner must stay inside."""
        return self.OD / 2 + self.BA - self.C - self.W

    def diagonal_gap(self, inner: Octagon) -> float:
        """Perpendicular gap between this ring's inner chamfer and the concentric ``inner`` ring's outer chamfer."""
        pitch = (self.OD - inner.OD) / 2
        return (pitch + self.BA - inner.BA - self.C - self.W) / SQRT2


def octagon(OD: float, W: float, bias: int = 0) -> Octagon:
    A = roundtogrid(OD / OCT_DIV)
    BA = floortogrid((OD - 2 * A) / 2 - 0.005) - bias * GRID_UM
    return Octagon(OD, W, bias, A, BA, chamfer(W).C)


def max_opening(OD: float, W: float) -> float:
    """``octagon(OD, W).max_opening`` for the callers that only have the two numbers."""
    return octagon(OD, W).max_opening


def junction_half_offset(W: float, S: float) -> float:
    """OOCH of base_ind_diag / base_xfm_cross: half the distance between a crossover's two diagonal junctions, on the grid.

    ``W + S/2 - C2`` lands half a grid off whenever S is an odd multiple of the
    grid; snapping keeps every diagonal vertex on integer nanometres (D8: the
    44.9959-degree edges came from the two endpoints rounding differently)."""
    return roundtogrid(W + S / 2 - chamfer(W).C2)


def _nm(x_um: float) -> int:
    """Snap a micron coordinate to integer nanometres (Virtuoso dbu)."""
    return int(round(x_um / DBU_UM))


# ---------------------------------------------------------------------------
# reference-mode layer mapping, the reference process's: M1=31..M10=40, AP=41,
# the via above M<m> = 50+m; in process mode every profile carries its own
# ---------------------------------------------------------------------------


def _metal_index(me) -> int:
    """Metal spelling -> stack position (``ic_opt.em.pcell.stack``): the active profile's metal stack when a build
    opened one (1 = the bottom, the ground-fixture layer; any names, any number of metals), else the fixed
    1P10M+AP convention ("10" / "M10" / "m10" -> 10, "AP" -> 11).

    Every spelling the config layer accepts works; a token that names no conductor fails closed as
    ``PortError`` (a ``ValueError``, so existing ``except ValueError`` callers are unaffected)."""
    try:
        return _stack.index(me)
    except ValueError as exc:
        raise PortError(str(exc)) from None


def _metal_name(idx: int) -> str:
    """Stack position -> rule-profile conductor name (the active stack's, else the fixed convention's: 11 == AP)."""
    return _stack.name(idx)


def metal_layer(met: int) -> tuple[int, int]:
    if not 1 <= met <= 11:
        raise PortError(f"metal {_metal_name(met)} outside the 1P10M+AP stack")
    return (30 + met, 0)


def metal_pin_layer(met: int) -> tuple[int, int]:
    """Reference-mode pin layer: proc ``l1XXt0`` (drawing ``30+m`` + pin ``130+m``)."""
    if not 1 <= met <= 11:
        raise PortError(f"metal {_metal_name(met)} outside the 1P10M+AP stack")
    return (130 + met, 0)


def via_layer(bottom_met: int) -> tuple[int, int]:
    if not 1 <= bottom_met <= 10:
        raise PortError(f"via{bottom_met} outside the 1P10M+AP stack")
    return (50 + bottom_met, 0)


# ---------------------------------------------------------------------------
# Process-backed mode: layer/datatype and via rules come from the
# process rule profile (ic_opt/em/pcell/profiles or IC_OPT_PROFILE_DIRS)
# through the existing GeometryRuleAdapter. Reference mode (process=None)
# keeps the reconstructed PCell/ind_ref behavior and is NOT N28 DRC proof.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessRuleContext:
    """Rule-profile handle for process mode.

    ``passive_region=False`` is not supported yet: via planning always goes
    through plan_passive_via_array, so only the passive-region rule set is
    meaningful here (the flag exists for interface fidelity with the spec).
    """

    profile_id: str
    adapter: GeometryRuleAdapter
    passive_region: bool = True


def process_rule_context(profile_id: str) -> ProcessRuleContext:
    """Load a process rule profile (e.g. the packaged "demo_6m") for process mode."""
    from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter

    return ProcessRuleContext(
        profile_id=profile_id, adapter=get_geometry_rule_adapter(profile_id)
    )


def _metal_below(met, process: ProcessRuleContext | None, levels: int = 1) -> int:
    """Return a lower conductor in the actual stack (N65 AP is above M9)."""
    top = _metal_index(met)
    candidates = list(range(1, top))
    if process is not None:
        process_metal_layer(process, top)
        conductors = process.adapter.profile.layer_catalog.conductors
        candidates = [m for m in candidates if _metal_name(m) in conductors]
    if levels < 1 or len(candidates) < levels:
        raise PortError(f"{_metal_name(top)} has no conductor {levels} levels below")
    return candidates[-levels]


def process_metal_layer(process: ProcessRuleContext, met: int) -> tuple[int, int]:
    try:
        return tuple(process.adapter.layer(_metal_name(met)).drawing)
    except ValueError as exc:
        raise PortError(f"{process.profile_id}: {exc}") from exc


def process_via_layer(process: ProcessRuleContext, bottom_met: int) -> tuple[int, int]:
    try:
        via = process.adapter.via_between(_metal_name(bottom_met),
                                          _metal_name(bottom_met + 1))
    except ValueError as exc:
        raise PortError(f"{process.profile_id}: {exc}") from exc
    return tuple(via.drawing)


def _metal(met: int, process: ProcessRuleContext | None) -> tuple[int, int]:
    if process is None:
        return metal_layer(met)
    return process_metal_layer(process, met)


def process_pin_layer(process: ProcessRuleContext, met: int) -> tuple[int, int]:
    """Rule-profile pin layer for a metal; fail closed when the profile leaves
    ``pin`` unset (a port label then cannot be placed honestly)."""
    try:
        pin = process.adapter.layer(_metal_name(met)).pin
    except ValueError as exc:
        raise PortError(f"{process.profile_id}: {exc}") from exc
    if pin is None:
        raise PortError(
            f"{process.profile_id}: {_metal_name(met)} has no pin layer; "
            "cannot place an EMX port label"
        )
    return tuple(pin)


def _pin(met: int, process: ProcessRuleContext | None) -> tuple[int, int]:
    if process is None:
        return metal_pin_layer(met)
    return process_pin_layer(process, met)


def metal_drawing_pin(met: int, process: ProcessRuleContext | None):
    """(drawing, pin) layer pair for a metal in the active mode; fails closed
    when the process profile leaves pin unset for that metal."""
    if process is None:
        return metal_layer(met), metal_pin_layer(met)
    spec = process.adapter.layer(_metal_name(met))
    if spec.pin is None:
        raise PortError(f"{process.profile_id}: {_metal_name(met)} has no pin "
                        "layer")
    return tuple(spec.drawing), tuple(spec.pin)


# ---------------------------------------------------------------------------
# transforms (Virtuoso instance orientations)
# ---------------------------------------------------------------------------

_ORIENTS = {
    "R0": lambda x, y: (x, y),
    "R90": lambda x, y: (-y, x),
    "R180": lambda x, y: (-x, -y),
    "R270": lambda x, y: (y, -x),
    "MX": lambda x, y: (x, -y),
    "MY": lambda x, y: (-x, y),
}
# the same six maps as klayout transformations (rotation code, mirror): exact on integer coordinates
_KDB_ORIENTS = {"R0": (0, False), "R90": (1, False), "R180": (2, False), "R270": (3, False), "MX": (0, True), "MY": (2, True)}


def transform_point(point_nm: tuple[int, int], orient: str) -> tuple[int, int]:
    try:
        fn = _ORIENTS[orient]
    except KeyError:
        raise PortError(f"unsupported orientation {orient!r}") from None
    return fn(*point_nm)


def _transform_box(
    box_nm: tuple[int, int, int, int], xf
) -> tuple[int, int, int, int]:
    """A port's lead zone through the same point transform ``_flat`` uses
    for shapes/labels (port contract 2026-09-21). Every ``_ORIENTS`` entry
    is a translation plus a 90-degree-multiple rotation/mirror (axis-
    preserving), so transforming the box's two corner points and
    re-deriving (min, max) recovers the exact transformed rectangle --
    no clipping or general-angle handling needed."""
    x0, y0, x1, y1 = box_nm
    px0, py0 = xf((x0, y0))
    px1, py1 = xf((x1, y1))
    return (min(px0, px1), min(py0, py1), max(px0, px1), max(py0, py1))


ORIENTATION_DEG = {(1, 0): 0, (0, 1): 90, (-1, 0): 180, (0, -1): 270}      # the direction a port faces, as gdsfactory names it
_PAIR_RE = re.compile(r"^([PN])(\d+)$")


def port_direction(point_nm: tuple[int, int], zone_nm: tuple[int, int, int, int]) -> tuple[tuple[int, int], int]:
    """(unit vector the port faces, lead width in nm) from the one zone edge the port point sits on.

    A lead's port is registered at the tip of the lead it was drawn into, so
    the edge it touches IS its orientation (outward, along the lead) and the
    zone's extent across that edge is the lead's width. A point on a corner
    or inside the zone has no orientation and violates the port contract."""
    x, y = point_nm
    x0, y0, x1, y1 = zone_nm
    xlo, xhi, ylo, yhi = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
    edges = [d for d, on in (((1, 0), x == xhi), ((-1, 0), x == xlo), ((0, 1), y == yhi), ((0, -1), y == ylo)) if on]
    if len(edges) != 1 or not (xlo <= x <= xhi and ylo <= y <= yhi):
        raise PortError(f"port contract: point {point_nm} must sit on exactly one edge of its lead zone {zone_nm}, found {len(edges)}")
    direction = edges[0]
    return direction, (yhi - ylo) if direction[0] else (xhi - xlo)


def port_pair(logical_name: str) -> str | None:
    """The differential partner's logical name (P1 <-> N1); taps and single-ended ports have none."""
    m = _PAIR_RE.match(logical_name)
    return None if m is None else ("N" if m.group(1) == "P" else "P") + m.group(2)


def _port_dict(
    name: str,
    logical_name: str,
    metal: int,
    label_layer: tuple[int, int],
    point_nm: tuple[int, int],
    zone_nm: tuple[int, int, int, int],
    direction: tuple[int, int],
    width_nm: int,
) -> dict:
    """The one ``emx_ports`` dict shape (port contract 2026-09-21) --
    ``finalize_emx_ports``'s own builder, called once per composed port."""
    gx, gy = point_nm
    return {
        "name": name,
        "signal": name,
        "reference": None,
        "logical_name": logical_name,
        "metal": _metal_name(metal),
        "metal_index": metal,
        "label_layer": list(label_layer),
        "point_nm": [gx, gy],
        "lead_zone_nm": list(zone_nm),
        "label_xy_um": [round(gx * DBU_UM, 3), round(gy * DBU_UM, 3)],
        "orientation_deg": ORIENTATION_DEG[direction],
        "width_um": round(width_nm * DBU_UM, 3),
        "pair": port_pair(logical_name),
    }


# ---------------------------------------------------------------------------
# cell model (mirrors dbCreatePolygon / dbCreateRect / dbCreateParamInst)
# ---------------------------------------------------------------------------


@dataclass
class Shape:
    layer: tuple[int, int]
    points_nm: list[tuple[int, int]]


@dataclass
class Label:
    layer: tuple[int, int]
    text: str
    point_nm: tuple[int, int]


@dataclass
class Port:
    """One EMX port, registered by the primitive that draws its lead, in
    that primitive's own local integer-nm frame (port contract
    2026-09-21). ``point_nm`` is the port's authoritative coordinate;
    ``lead_zone_nm`` is the (xmin, ymin, xmax, ymax) rectangle the SAME
    primitive just drew that lead into -- not necessarily any drawn
    polygon's own bbox (xfm_tw's stub and its ring arc are one fused
    polygon; the zone there covers only the stub, not the whole fused
    ring arc a pre-contract point-in-bbox exclusion used to carry).
    Both travel through ``cell.inst()``/``_flat()`` the same integer
    transform chain ``Shape``/``Label`` do, never a second, independently
    rounded copy -- see ``Cell.add_emx_port``."""

    name: str
    logical_name: str
    metal: int
    label_layer: tuple[int, int]
    point_nm: tuple[int, int]
    lead_zone_nm: tuple[int, int, int, int]
    direction_nm: tuple[int, int]          # unit vector the port faces, local frame (see ``port_direction``)
    width_nm: int                          # the lead's width across that direction


@dataclass
class Inst:
    cell: Cell
    origin_nm: tuple[int, int]
    orient: str


@dataclass
class Cell:
    name: str
    function: str
    params: dict
    shapes: list[Shape] = field(default_factory=list)
    insts: list[Inst] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    ports: list[Port] = field(default_factory=list)
    # Populated EXCLUSIVELY by an explicit `cell.emx_ports =
    # finalize_emx_ports(cell)` call at the end of each of the six family
    # functions (port contract 2026-09-21) -- never by add_emx_port itself
    # (see that method's docstring). Replaces the pre-contract path, where
    # add_emx_port appended here directly and every family hand-rolled its
    # own transplant loop to carry a child cell's ports upward.
    emx_ports: list[dict] = field(default_factory=list)
    _regions: dict = field(default_factory=dict, repr=False, compare=False)     # ``region`` memo, dropped on every mutation

    def add_shape(self, shape: Shape) -> None:
        """Append an already-snapped shape (the seam heal and the PGS build produce those)."""
        self.shapes.append(shape)
        self._regions.clear()

    def add_polygon(self, layer: tuple[int, int], points_um) -> None:
        self.add_shape(Shape(layer, [(_nm(x), _nm(y)) for x, y in points_um]))

    def add_rect(self, layer, x1_um, y1_um, x2_um, y2_um) -> None:
        self.add_polygon(
            layer,
            [(x1_um, y1_um), (x2_um, y1_um), (x2_um, y2_um), (x1_um, y2_um)],
        )

    def add_label(self, layer: tuple[int, int], text: str, x_um: float, y_um: float) -> None:
        self.labels.append(Label(layer, text, (_nm(x_um), _nm(y_um))))

    def add_emx_port(
        self,
        *,
        name: str,
        logical_name: str,
        metal: int,
        label_layer: tuple[int, int],
        x_um: float,
        y_um: float,
        lead_zone_um: tuple[float, float, float, float],
    ) -> None:
        """Register one EMX port (port contract 2026-09-21): ``x_um``/
        ``y_um`` are snapped to integer nm exactly ONCE, here, and every
        consumer -- the GDS glyph (``self.labels``) and the structural
        ``Port`` (``self.ports``, carried through ``cell.inst()``/
        ``_flat()`` like any shape or label) -- share that same pair of
        integers. A caller passes whatever float expression it has;
        nothing downstream re-derives its own copy from an already-snapped
        local coordinate plus a macro offset (the historical bug class
        this contract closes -- see the port-lattice design's "mechanism
        (1)/(2)/(3)").

        This is the ONLY place a port coordinate is ever computed (every
        family calls it only through ``base_lead``/``base_lead_pair``, or,
        for xfm_tw, once per stub inside ``_tw_render_winding`` --
        ``test_add_emx_port_called_only_from_leaf_primitives`` pins this).
        It does NOT write ``self.emx_ports`` -- that list is populated
        exclusively by ``finalize_emx_ports``, called once at the end of a
        family function, so a port several ``cell.inst()`` levels down
        (a CT tap's ``base_lead``, an ind_sym sub-winding transplanted
        into xfm_bs/xfm_ms/xfm_balun) is composed through the SAME
        integer transform chain shapes/labels use, never a second,
        independently re-derived copy.

        ``lead_zone_um`` is the local rectangle (xmin, ymin, xmax, ymax)
        the calling primitive just drew this port's lead into. It is
        required: a port without its lead has no meaning to the ground
        fixture, and an optional zone would reopen a second way to
        register one.
        """
        x_nm, y_nm = _nm(x_um), _nm(y_um)
        self.labels.append(Label(label_layer, name, (x_nm, y_nm)))
        zone_nm = tuple(_nm(v) for v in lead_zone_um)
        direction, width_nm = port_direction((x_nm, y_nm), zone_nm)
        self.ports.append(
            Port(name, logical_name, metal, label_layer, (x_nm, y_nm), zone_nm, direction, width_nm)
        )

    def inst(self, child: Cell, origin_um: tuple[float, float], orient: str) -> None:
        if orient not in _ORIENTS:
            raise PortError(f"unsupported orientation {orient!r}")
        self.insts.append(Inst(child, (_nm(origin_um[0]), _nm(origin_um[1])), orient))
        self._regions.clear()

    def region(self, layer: tuple[int, int]) -> kdb.Region:
        """Every polygon on ``layer`` under this cell, in its own frame, as one (unmerged) klayout Region.

        The same integer transform chain ``_flat`` applies, done by klayout
        (``Region.transformed`` with the six axis-preserving orientations is
        exact on integer coordinates) and memoized per cell, so a child that
        several parents or candidates share is flattened once (M1.5)."""
        cached = self._regions.get(layer)
        if cached is not None:
            return cached
        region = kdb.Region()
        for shape in self.shapes:
            if shape.layer == layer and shape.points_nm:
                region.insert(kdb.Polygon([kdb.Point(x, y) for x, y in shape.points_nm]))
        for inst in self.insts:
            child = inst.cell.region(layer)
            if not child.is_empty():
                rot, mirror = _KDB_ORIENTS[inst.orient]
                region.insert(child.transformed(kdb.Trans(rot, mirror, inst.origin_nm[0], inst.origin_nm[1])))
        self._regions[layer] = region
        return region

    def flat_shapes(self):
        """Yield (layer, points_nm) with all instance transforms applied."""
        for item in self._flat(lambda p: p):
            if item[0] == "__poly__":
                yield item[1], item[2]

    def flat_labels(self):
        """Yield ('__label__', layer, text, point_nm) with transforms applied."""
        for item in self._flat(lambda p: p):
            if item[0] == "__label__":
                yield item

    def flat_ports(self):
        """Yield (port, point_nm, zone_nm, direction_nm) with all instance
        transforms applied -- the ``__port__`` analogue of ``flat_shapes``/
        ``flat_labels`` (port contract 2026-09-21)."""
        for item in self._flat(lambda p: p):
            if item[0] == "__port__":
                yield item[1], item[2], item[3], item[4]

    def _flat(self, xf):
        for s in self.shapes:
            yield ("__poly__", s.layer, [xf(p) for p in s.points_nm])
        for lb in self.labels:
            yield ("__label__", lb.layer, lb.text, xf(lb.point_nm))
        for p in self.ports:
            gx, gy = xf(p.point_nm)
            tx, ty = xf((p.point_nm[0] + p.direction_nm[0], p.point_nm[1] + p.direction_nm[1]))
            yield ("__port__", p, (gx, gy), _transform_box(p.lead_zone_nm, xf), (tx - gx, ty - gy))
        for inst in self.insts:
            ox, oy = inst.origin_nm
            orient = inst.orient

            def child_xf(p, _ox=ox, _oy=oy, _orient=orient, _outer=xf):
                x, y = transform_point(p, _orient)
                return _outer((x + _ox, y + _oy))

            yield from inst.cell._flat(child_xf)

    def instantiation_log(self) -> list[dict]:
        log = []
        for order, inst in enumerate(self.insts):
            log.append(
                {
                    "order": order,
                    "cell": inst.cell.name,
                    "function": inst.cell.function,
                    "params": inst.cell.params,
                    "origin_um": [
                        inst.origin_nm[0] * DBU_UM,
                        inst.origin_nm[1] * DBU_UM,
                    ],
                    "orient": inst.orient,
                    "children": inst.cell.instantiation_log(),
                }
            )
        return log


def write_gds(cell: Cell, path) -> None:
    layout = kdb.Layout()
    layout.dbu = DBU_UM
    # The top cell is ALWAYS named after the output file's own stem (user
    # default, requested repeatedly: Virtuoso import expects gds filename ==
    # top cell name). cell.name stays the in-memory identity only; deriving
    # the GDS name from the path here makes a mismatch structurally
    # impossible on every write path (plugin, samples, demos alike).
    # Charset: keep the name inside the strict GDSII set.
    stem = Path(path).stem
    top = layout.create_cell(re.sub(r"[^A-Za-z0-9_$?]", "_", stem))
    for layer, pts in cell.flat_shapes():
        li = layout.layer(layer[0], layer[1])
        top.shapes(li).insert(kdb.Polygon([kdb.Point(x, y) for x, y in pts]))
    for _tag, layer, text, pt in cell.flat_labels():
        li = layout.layer(layer[0], layer[1])
        top.shapes(li).insert(
            kdb.Text(text, kdb.Trans(kdb.Vector(pt[0], pt[1])))
        )
    opts = kdb.SaveLayoutOptions()
    # no wall-clock BGNLIB/BGNSTR stamps: identical geometry must produce
    # identical bytes regardless of when the two writes happen
    opts.gds2_write_timestamps = False
    layout.write(str(path), opts)


# ---------------------------------------------------------------------------
# vias (PDK library PCell; interface reconstructed -- see module docstring)
# ---------------------------------------------------------------------------


def _cut_positions(extent_um: float) -> list[float]:
    """Centred via-cut offsets along one axis of a Width x Length block."""
    usable = extent_um - 2 * VIA_ENC_UM
    pitch = VIA_CUT_UM + VIA_SPACE_UM
    n = math.floor((usable + VIA_SPACE_UM) / pitch + _EPS)
    if n < 1:
        # fail closed: no under-enclosed single-cut fallback
        raise PortError(
            f"vias: extent {extent_um} um cannot host a {VIA_CUT_UM} um cut "
            f"with {VIA_ENC_UM} um enclosure"
        )
    array = n * VIA_CUT_UM + (n - 1) * VIA_SPACE_UM
    start = roundtogrid((extent_um - array) / 2)
    return [start + i * pitch for i in range(n)]


def _add_process_via_cuts(
    cell: Cell,
    process: ProcessRuleContext,
    bottom_met: int,
    Width: float,
    Length: float,
    upper_met: int | None = None,
) -> None:
    """Fill one via level from the process rule profile (fail closed)."""
    # _metal_name so the top of the stack is "AP" (index 11), not "M11" --
    # otherwise an M10->AP span (e.g. an AP balun crossunder) fails to resolve
    # the RV via. Identical to f"M{n}" for metals 1..10.
    lower = _metal_name(bottom_met)
    upper = _metal_name(bottom_met + 1 if upper_met is None else upper_met)
    try:
        via = process.adapter.via_between(lower, upper)
        plan = process.adapter.plan_passive_via_array(
            lower_metal=lower,
            upper_metal=upper,
            available_width_um=Width,
            available_height_um=Length,
        )
    except ValueError as exc:
        raise PortError(f"{process.profile_id}: {lower}->{upper}: {exc}") from exc
    cut_w, cut_h = plan.cut_size_um
    pitch_x, pitch_y = plan.center_pitch_um
    array_w, array_h = plan.array_size_um
    x0 = roundtogrid((Width - array_w) / 2)
    y0 = roundtogrid((Length - array_h) / 2)
    enclosure = max(
        plan.enclosure_um[plan.lower_metal], plan.enclosure_um[plan.upper_metal]
    )
    # grid snapping must never eat into the rule enclosure
    if x0 < enclosure - _EPS or y0 < enclosure - _EPS:
        raise PortError(
            f"{process.profile_id}: {plan.via} array snap violates the "
            f"{enclosure} um enclosure in a {Width} x {Length} um window"
        )
    layer = tuple(via.drawing)
    for column in range(plan.columns):
        for row in range(plan.rows):
            x = x0 + column * pitch_x
            y = y0 + row * pitch_y
            cell.add_rect(layer, x, y, x + cut_w, y + cut_h)


def _add_via_cuts(cell, met, Width, Length, process, upper_met=None):
    """Centred VIA cut array for one metal level (no metal rectangles).

    Shared by ``vias`` (metal + cuts) and ``vias_nomet`` (cuts only) so both
    produce identical cut placement on a given via level."""
    if process is None:
        xs, ys = _cut_positions(Width), _cut_positions(Length)
        for x in xs:
            for y in ys:
                cell.add_rect(via_layer(met), x, y, x + VIA_CUT_UM, y + VIA_CUT_UM)
    else:
        _add_process_via_cuts(cell, process, met, Width, Length, upper_met)


def vias(
    Length: float,
    Width: float,
    TOP_ME: int,
    BTM_ME: int,
    bPP: bool = True,
    process: ProcessRuleContext | None = None,
) -> Cell:
    """Library "vias" PCell: metal stack + centred cut arrays in [0,Width]x[0,Length].

    TOP_ME == BTM_ME draws metal only (base_lead.il draws its lead this way).
    ``bPP`` is accepted for signature fidelity and ignored (semantics unknown,
    fail-closed: documented as a deviation).

    Reference mode (process=None) uses the ind_ref-reconstructed cut rule
    (0.36/0.34/0.22 um) on datatype-0 layers; process mode takes every
    layer/datatype, cut size, spacing and enclosure from the process rule
    profile via plan_passive_via_array -- geometric-only enforcement
    (n28-rules-slim, user directive 2026-07-19): line width, via cut size,
    line spacing, via-to-metal-edge enclosure and via-to-via spacing are the
    only categories that fail generation closed. A via with a modeled
    via_array_rules entry (currently VIA8/VIA9/RV) additionally enforces its
    own min-count/max-spacing legality unchanged. Neither a cited
    passive-region restriction (e.g. IND.R.1, which named VIA1..VIA7) nor an
    unmodeled passive_via_array_coverage classification fails generation
    closed any more; both remain in the rule profile as cited/declared
    data. Only missing via geometry itself -- no via_primitives entry, or an
    incomplete enclosure map -- still fails closed, since that is a
    data-availability gap, not a policy restriction.
    """
    TOP_ME, BTM_ME = _metal_index(TOP_ME), _metal_index(BTM_ME)
    if TOP_ME < BTM_ME:
        raise PortError(f"vias: TOP_ME {TOP_ME} below BTM_ME {BTM_ME}")
    params = {"Length": Length, "Width": Width, "TOP_ME": TOP_ME, "BTM_ME": BTM_ME}
    if process is not None:
        params["process"] = process.profile_id
    cell = Cell(f"vias_L{Length}_W{Width}_T{TOP_ME}_B{BTM_ME}", "vias", params)
    metals = list(range(BTM_ME, TOP_ME + 1))
    if process is not None:
        # Endpoints must exist; intermediate levels follow this profile.
        # N65 has M9->AP (RV), while N28 has M9->M10->AP.
        _metal(BTM_ME, process)
        _metal(TOP_ME, process)
        metals = [met for met in metals
                  if _metal_name(met) in process.adapter.profile.layer_catalog.conductors]
    for met in metals:
        _check_rect_width(met, Width, Length, process, "vias")
        cell.add_rect(_metal(met, process), 0.0, 0.0, Width, Length)
    for lower, upper in zip(metals, metals[1:]):
        _add_via_cuts(cell, lower, Width, Length, process, upper)
    return cell


def _check_rect_width(met: int, width_um: float, length_um: float,
                      process: ProcessRuleContext | None, where: str) -> None:
    """A rectangle drawn on M(met) must respect THAT metal's own width rule
    (the product DRC's max_width predicate: a region that survives erosion
    by max_width/2, i.e. whose shorter side exceeds max_width). Tap stacks
    and CT leads inherit the winding's W on every level down to the CT
    metal, and thin lower metals allow far less than a thick winding metal
    -- 76% of CT-on-M2..M6 variants violated it while the family-level
    trace check only looked at the winding metal (gdsfactory review
    2026-09-21)."""
    if process is None:
        return
    name = _metal_name(met)
    try:
        rule = process.adapter.metal_rule(name)
    except ValueError as exc:
        raise PortError(f"{process.profile_id}: {exc}") from exc
    narrow = min(width_um, length_um)
    if rule.max_width_um is not None and narrow > rule.max_width_um + _EPS:
        raise PortError(
            f"{where}: a {width_um:g} x {length_um:g} um rectangle on {name} "
            f"exceeds the {name} max width {rule.max_width_um} of profile "
            f"{process.profile_id}; a tap stack or lead on this metal cannot "
            "carry the winding trace width")


def vias_nomet(Length, Width, TOP_ME, BTM_ME, process=None) -> Cell:
    """Cut-only VIA array (metal drawn by the enclosing octagon).

    Reconstruction of the sourceless library ``vias_nomet`` PCell: the M7I
    ``vias`` cut placement with the metal rectangles omitted. Reference mode
    uses the reconstructed 0.36/0.34/0.22 um cut rule; process mode uses
    ``plan_passive_via_array`` cuts on the rule-profile via layer, failing
    closed only on missing via geometry exactly like ``vias`` (geometric-only
    enforcement, n28-rules-slim, user directive 2026-07-19).
    """
    TOP_ME, BTM_ME = _metal_index(TOP_ME), _metal_index(BTM_ME)
    if TOP_ME <= BTM_ME:
        raise PortError(f"vias_nomet: TOP_ME {TOP_ME} not above BTM_ME {BTM_ME}")
    params = {"Length": Length, "Width": Width, "TOP_ME": TOP_ME, "BTM_ME": BTM_ME}
    if process is not None:
        params["process"] = process.profile_id
    cell = Cell(f"vias_nomet_L{Length}_W{Width}_T{TOP_ME}_B{BTM_ME}",
                "vias_nomet", params)
    for met in range(BTM_ME, TOP_ME):
        _add_via_cuts(cell, met, Width, Length, process)
    return cell


# ---------------------------------------------------------------------------
# base_ind_hud_cross (gdsgen_ref/pcell/inductor/base_ind_hud_cross.il)
# ---------------------------------------------------------------------------


def cross_endpoint_offset(
    W: float, S: float, met=None, process: ProcessRuleContext | None = None
) -> float:
    """|y| of the outer edge of a base_xfm_cross(WI=S, WO=W, S=0) endpoint.

    This is OOCH + OOCHD of base_xfm_cross.il for the configuration used by
    base_ind_hud_cross. Ring openings that face a crossover use exactly this
    value so the endpoint via blocks sit flush inside the ring arms (the
    alignment relationship shown by ind_ref.gds, where every via pad is
    fully covered by ring metal). The .il instead uses independently rounded
    values (LOP = OOCH + roundtogrid(2*sqrt(2)-S) + 0.01 and OPENING = 2*W)
    that leave the pads protruding past the arm ends; see KNOWN_DEVIATIONS.

    ``met``/``process`` (M12 Phase 0.5, D1) drive the OOCHD clearance from the
    ring conductor's own rule ``min_space`` so the flush ring opening tracks
    the same-layer crossover diagonal after its junction clearance widens.
    With ``process=None`` (or the default ``met``) the clearance is the
    reference literal -> byte-identical geometry (see
    ``_junction_clearance_const``)."""
    OOCH = junction_half_offset(W, S)
    if S <= 2.0 * math.sqrt(2.0):
        OOCHD = ceiltogrid(
            _junction_clearance_const(met, process) * math.sqrt(2.0) - S)
    else:
        OOCHD = 0.0
    return OOCH + OOCHD


# ---------------------------------------------------------------------------
# port lattice contract (2026-09-21): compose every port registered
# anywhere under a cell into the flat emx_ports list, and pin the one
# structural invariant that makes a mis-registered port impossible to ship
# silently. See .scratch/port-lattice-contract-2026-09-21/spec.md.
# ---------------------------------------------------------------------------


def _check_port_lattice_invariant(finalized_ports: list[dict]) -> None:
    """Build-time invariant (port contract 2026-09-21): every finalized
    port's point must lie on (or inside) its own registered lead zone.
    True by construction for anything routed through ``add_emx_port``'s
    ``lead_zone_um`` -- a caller that bypasses the primitive and hands
    ``finalize_emx_ports`` a coordinate the zone does not actually cover
    fails HERE, at generation time, not as a silently wrong library row."""
    for p in finalized_ports:
        x, y = p["point_nm"]
        x0, y0, x1, y1 = p["lead_zone_nm"]
        if not (min(x0, x1) <= x <= max(x0, x1) and min(y0, y1) <= y <= max(y0, y1)):
            raise PortError(
                f"port lattice contract violated: {p['name']} "
                f"point_nm={p['point_nm']} outside its own "
                f"lead_zone_nm={p['lead_zone_nm']} -- a caller registered a "
                "port coordinate that was not derived from the same local "
                "zone (port contract 2026-09-21)"
            )


def finalize_emx_ports(cell: Cell) -> list[dict]:
    """Compose every port registered anywhere under ``cell`` (port
    contract 2026-09-21): walks the same ``cell._flat()`` integer
    transform chain ``flat_shapes()``/``flat_labels()`` use, so a port
    several ``cell.inst()`` levels down (a CT tap's ``base_lead``, an
    ind_sym sub-winding transplanted into xfm_bs/xfm_ms) lands at the
    SAME coordinate its drawn lead does -- never a second, independently
    re-rounded one. Call this exactly once, at the end of a family
    function (in place of any hand-rolled
    ``cell.emx_ports.extend(...)``/transplant loop), right before
    ``add_ground_fixture``."""
    out = []
    for item in cell._flat(lambda p: p):
        if item[0] != "__port__":
            continue
        _tag, port, point_nm, zone_nm, direction = item
        out.append(_port_dict(port.name, port.logical_name, port.metal,
                              port.label_layer, point_nm, zone_nm, direction, port.width_nm))
    names = {p["logical_name"] for p in out}
    for p in out:
        if p["pair"] not in names:
            p["pair"] = None
    _check_port_lattice_invariant(out)
    return out


def bridge_y_reach(W: float, S: float, met, process: ProcessRuleContext | None) -> float:
    """|y| of the farthest point a same-side-stacked winding's crossunder
    pad reaches from the winding's own centreline (xfm_il ticket 02c).

    Every ``base_ind_hud_cross`` call in a winding shares the SAME
    ``cross_gap = PITCH - W`` (``PITCH`` is constant across all of a
    winding's turns), so ``cross_endpoint_offset`` -- the ring-opening edge
    every ``LOP``/facing notch is sized to -- is identical for every turn's
    bridge, not just the outermost. The via PAD placed at that offset is
    itself ``W`` wide and anchored at its near corner (see
    ``base_xfm_cross``), so the pad's own far edge reaches
    ``cross_endpoint_offset(...) + W`` -- confirmed empirically against the
    built SL-1 region's bbox, not merely the notch-alignment literal.

    Used by the ticket 02c bridge/escape clearance guard: since ``W``/``S``
    (hence ``cross_gap``) are shared between P and S, this single value is
    every winding's bridge reach on whichever half-plane its bridges stack
    on."""
    pitch = 2.0 * (W + S)
    cross_gap = pitch - W
    return cross_endpoint_offset(W, cross_gap, met, process) + W


REFERENCE_MIN_MET_SPACING_UM = 1.0


def _min_met_spacing(top_met, process, override):
    """Min metal spacing of M(top_met): override > process rule > reference default.

    ``process.adapter.metal_rule`` raises a bare ``ValueError`` for a
    conductor the loaded profile does not have (e.g. "M10" queried against
    n65_1p9m, which has no M10 between M9 and AP); wrapped to ``PortError``
    naming the profile the same way ``process_metal_layer`` already does
    (gdsfactory review 2026-09-21, api-3) instead of leaking past every
    caller (``_required_parallel_spacing``/``_effective_min_spacing`` and
    every family that calls them only ever reach the profile through this
    function, so fixing it here covers them all)."""
    if override is not None:
        return override
    if process is None:
        return REFERENCE_MIN_MET_SPACING_UM
    try:
        rule = process.adapter.metal_rule(_metal_name(top_met))
    except ValueError as exc:
        raise PortError(f"{process.profile_id}: {exc}") from exc
    if rule.min_space_um is None:
        raise PortError(
            f"{process.profile_id}: {_metal_name(top_met)} has no min space rule"
        )
    return rule.min_space_um


def _effective_min_spacing(top_met, W: float, process) -> float:
    """The spacing floor DRC actually enforces between W-wide parallel
    windings on M(top_met): the base min_space raised by every
    wide-parallel rule whose width threshold the winding's DRAWN width
    exceeds (winding arcs always exceed the parallel-length thresholds).

    The width DRC measures is the octagon diagonal's ``chamfer(W).drawn_width``
    (a few nanometres over W, never W itself), so a nominal W sitting exactly
    on a rule threshold is already on the wide side of it -- the generator
    must believe what it draws (M1.2; plan F2). Rule-generic -- read from the
    loaded profile, never per-process constants."""
    floor = _min_met_spacing(top_met, process, None)
    if process is None:
        return floor
    name = _metal_name(top_met)
    drawn = chamfer(W).drawn_width
    passive = process.adapter.profile.layout_rules.passive_region
    for rule in passive.wide_parallel_spacing:
        if name in rule.metals and drawn > rule.when_width_gt_um:
            floor = max(floor, rule.min_space_um)
    return floor


def chamfer_staircase_delta(ring_ods, W: float, top_met, process) -> int:
    """Uniform BA-staircase step (in GRID_UM counts) for concentric
    octagon rings drawn by ``base_oct_quad``'s quantized chamfer math.

    Each ring's chamfer parameters quantize independently (A rounds, BA
    floors, C ceils), so the 45-degree edges of an ADJACENT ring pair can
    sit single-digit nanometres closer than the flats' spacing --
    measured 6-9 nm short of a 2.0 um floor at S == min_space, invisible
    whenever the process leaves >= ~15 nm of margin. Biasing ring k's BA
    inward by ``k * delta`` grid steps grows EVERY adjacent pair's
    diagonal separation by exactly ``delta * GRID_UM / sqrt(2)`` (the
    difference of consecutive biases) while cardinal flats/arms -- where
    all bridges and leads land -- derive from OD alone and do not move.

    The pair separation is evaluated with the SAME grid helpers the
    primitive uses (call, not replicate): for the outer ring's inner
    chamfer edge against the inner ring's outer chamfer edge,
        sep = (pitch + BA_out - BA_in - C - W) / sqrt(2).
    Returns 0 when every pair already clears the effective floor -- the
    entire pre-2026-07-28 sweep territory, byte-identical by
    construction. Fail-closed above 4 steps (20 nm recovers every
    quantization-loss case; anything larger is not a snapping artifact)."""
    if process is None or len(ring_ods) < 2:
        return 0
    floor = _effective_min_spacing(top_met, W, process)
    rings = [octagon(od, W) for od in ring_ods]
    worst = min(outer.diagonal_gap(inner) for outer, inner in zip(rings, rings[1:]))
    if worst >= floor - 1e-9:
        return 0
    delta = math.ceil((floor - worst) * math.sqrt(2.0) / GRID_UM - 1e-9)
    if delta > 4:
        raise PortError(
            f"chamfer staircase cannot recover a "
            f"{floor - worst:.4f} um 45-degree-edge shortfall within 4 "
            f"grid steps on {_metal_name(top_met)} (W={W}, floor={floor}); this is "
            "not a quantization artifact -- increase S"
        )
    return delta


def _required_parallel_spacing(
    met: int,
    width: float,
    parallel_length: float,
    process: ProcessRuleContext | None,
) -> float:
    """Base spacing promoted by every applicable profile wide-line rule."""
    target = _min_met_spacing(met, process, None)
    if process is None:
        return target
    name = _metal_name(met)
    rules = (
        process.adapter.profile.layout_rules.passive_region.wide_parallel_spacing
    )
    for rule in rules:
        if (
            name in rule.metals
            and width > rule.when_width_gt_um
            and parallel_length > rule.when_parallel_length_gt_um
        ):
            target = max(target, rule.min_space_um)
    return target


# Reference crossover-junction clearance literal (the OOCH/OOCHD/ext base of
# base_ind_diag, base_xfm_cross and cross_endpoint_offset): 2.0 um nominal
# net distance + 0.01 um grid margin.  The reconstructed .il value.
REFERENCE_JUNCTION_CLEARANCE_UM = 2.01
# Grid-snap erosion of the 45-degree junction edge is <=10 nm after the nm
# grid snaps the diagonal; a 20 nm guard covers it so the widened AP-body
# junction still clears its rule after snapping.
JUNCTION_SNAP_GUARD_UM = 0.02


def _junction_clearance_const(met, process) -> float:
    """D1 (M12 Phase 0.5): rule-driven crossover-junction clearance constant.

    The reconstructed .il fixes the crossover-junction net distance with the
    literal ``2.01`` (see ``REFERENCE_JUNCTION_CLEARANCE_UM``).  In reference
    mode and for any conductor whose ``min_space`` sits at or below that
    literal (the thinner signal metals), this returns the literal
    unchanged -> the junction geometry is byte-identical to the reference
    port.  Only a conductor whose rule ``min_space`` would push past it
    (the AP body) widens the constant to ``min_space + snap_guard``, so the
    same-layer crossover diagonal clears the profile's own AP rule after
    grid snapping.  Keyed
    on ``met`` -- the conductor the junction geometry actually lands on --
    and drawn from the rule profile through the existing ``_min_met_spacing``
    adapter surface (no hardcoded rule value).

    ``met=None`` (the documented default of ``cross_endpoint_offset``) selects
    the reference literal even under a process context (Gate A' P2-1): with no
    conductor named there is no rule to key on, and the function's documented
    default is 'the clearance is the reference literal' -- never a crash."""
    if process is None or met is None:
        return REFERENCE_JUNCTION_CLEARANCE_UM
    space = _min_met_spacing(met, process, None)
    return max(REFERENCE_JUNCTION_CLEARANCE_UM, space + JUNCTION_SNAP_GUARD_UM)


# D2/D3 (M12 Phase 0.5): shared-vertex tolerance (dbu) that classifies a
# min_space edge pair as an acute-wedge *seam notch* (the two edges emanate
# from one polygon vertex, so their nearest endpoints coincide) versus an
# intended parallel gap (inter-turn spiral / crossover-junction net distance,
# whose nearest endpoints are ~one min_space apart). Measured separation:
# seam wedges 0 nm, parallel gaps >= ~700 nm even at the AP rule floor -- so
# 60 nm (12 grid steps) sits safely between the two populations.
SEAM_APEX_TOL_NM = 60
# Max heal passes: each pass closes one wedge layer; multi-vertex crowding
# converges in a handful. Bounded so a pathological input cannot loop.
_SEAM_HEAL_MAX_PASSES = 8


def _conductor_for_drawing(process: ProcessRuleContext, layer: tuple[int, int]):
    """Rule-profile conductor name whose drawing layer is ``layer`` (the active
    metal stack), or None if the layer is not a conductor the profile models (e.g.
    a via/pin layer). No hardcoded layer numbers -- every candidate comes from
    the adapter."""
    for idx in range(1, _stack.size() + 1):
        name = _metal_name(idx)
        try:
            if tuple(process.adapter.layer(name).drawing) == layer:
                return name
        except ValueError:
            continue
    return None


def _edge_pair_apex_gap(e1: kdb.Edge, e2: kdb.Edge) -> float:
    """Nearest-endpoint distance (dbu) between two edges: ~0 for the two edges
    of an acute wedge (they share the notch apex vertex), ~min_space for an
    intended parallel gap."""
    a = ((e1.p1.x, e1.p1.y), (e1.p2.x, e1.p2.y))
    b = ((e2.p1.x, e2.p1.y), (e2.p2.x, e2.p2.y))
    return min(math.hypot(p[0] - q[0], p[1] - q[1]) for p in a for q in b)


def _xfm_order_ports(cell):
    """Canonical xfm port order: fixed base [P1, N1, P2, N2], then the
    enabled taps CTP before CTS (the spec contract: taps append to the
    base order). EMX writes sNp columns in port-name order; generated p01,
    p02, ... aliases preserve this physical-port order."""
    by_name = {q["name"]: q for q in cell.emx_ports}
    order = [n for n in ("P1", "N1", "P2", "N2", "CTP", "CTS")
             if n in by_name]
    if len(order) == len(cell.emx_ports):
        cell.emx_ports[:] = [by_name[n] for n in order]


def _ms_layer_regions(
    cell: Cell,
    metals: tuple[int, ...],
    process: ProcessRuleContext,
) -> list[kdb.Region]:
    """The merged region of each metal under ``cell`` (``Cell.region``, memoized per sub-cell)."""
    return [cell.region(_metal(metal, process)).merged() for metal in metals]



_LAYER_NAMES = {metal_layer(m): f"M{m}" for m in range(1, 11)}
_LAYER_NAMES.update({via_layer(m): f"via{m}" for m in range(1, 10)})


# ---------------------------------------------------------------------------
# path extrusion: a width along a centreline, mitred and snapped to the mask grid
# ---------------------------------------------------------------------------

GRID_DBU = int(round(GRID_UM / DBU_UM))  # 5 (0.005 um mask grid, in nm)


def snap_nm_to_grid(v: int) -> int:
    """Snap an integer dbu (nm) coordinate to the nearest 0.005 um mask-grid
    multiple (every path-extruded conductor: xfm_tw's rings and legs, the NT=2 compact bridge legs).

    ``kdb.Path(...).polygon()`` mitres each turn by offsetting the
    centerline perpendicular to its own local direction; for anything other
    than a cardinal (0/90/180/270 degree) segment -- every xfm_tw
    boundary-crossing leg is a diagonal (ring-to-ring radial step vs a
    tangential +-G shift, see ``_tw_leg_endpoints``), and every octagon
    chamfer corner is a 45-degree turn -- that perpendicular offset has
    irrational trig components even when every centerline waypoint itself
    is already grid-exact (``_tw_oct_chamfer``'s A/B pair, ``H``). The
    corner is therefore off the 0.005 um mask grid before this snap, by
    construction, not by a missed grid call anywhere upstream; snapping the
    rendered OUTLINE here (to the nearest 5 dbu, <=2.5 nm of perturbation --
    two-plus orders of magnitude below any clearance margin this module
    derives) is the correct point to fix it, mirroring how every other
    device in this file grid-snaps its own hand-computed polygon vertices."""
    return int(round(v / GRID_DBU)) * GRID_DBU


def add_wide_path(cell: Cell, layer: tuple[int, int], pts_um: list,
                      width_um: float) -> None:
    """Draw a width-``width_um`` conductor along the ``pts_um`` centerline
    (mitred polygon via ``kdb.Path``, matching the octagon chamfer vertices
    already baked into the waypoints), snapped to the 0.005 um mask grid
    (``snap_nm_to_grid``, see its own docstring for why the mitre
    corners need this even though the centerline waypoints are grid-exact)."""
    pts_nm = [kdb.Point(_nm(x), _nm(y)) for x, y in pts_um]
    poly = kdb.Path(pts_nm, _nm(width_um)).polygon()
    cell.add_shape(Shape(layer, [
        (snap_nm_to_grid(p.x), snap_nm_to_grid(p.y))
        for p in poly.each_point_hull()
    ]))


def _process_layer_names(process: ProcessRuleContext) -> dict[tuple[int, int], str]:
    """Process-mode layer names, read at render time from the process rule
    profile's own layer catalog (conductors + vias) through the existing
    ProcessRuleContext/GeometryRuleAdapter API.

    This is a pure runtime lookup -- no process-specific gds layer number is
    ever written into this module (M11 IP-strip audit). It exists to name
    layers a numeric-metal-body render never needs: N28's AP conductor and
    its AP<->M10 via ("RV") sit outside the 31-40/51-59 gds-layer ranges
    ``_layer_display_name``'s generic fallback already covers for every
    M1-M10/VIA1-VIA9 layer (reference or process mode alike), so without
    this they render as the generic "?" placeholder (ticket 02's deferred
    gap, .scratch/xfm-tw-twisted/issues/02-n28-domain-sample-gallery.md)."""
    catalog = process.adapter.profile.layer_catalog
    names: dict[tuple[int, int], str] = {}
    for rule in catalog.conductors.values():
        names[rule.drawing] = rule.name
    for rule in catalog.vias.values():
        names[rule.drawing] = rule.name
    return names


def _layer_display_name(
    layer: tuple[int, int], name_map: dict[tuple[int, int], str] | None = None
) -> str:
    """Human name for a GDS layer pair in either mode (datatype-agnostic).

    ``name_map`` (built by ``_process_layer_names`` for a given process
    context) is consulted only as a last-resort fallback, after the generic
    31-40/51-59 patterns -- so the pre-existing M1-M10/via1-9 resolution
    (reference or process mode) is unchanged whether or not a name_map is
    supplied; name_map only reaches layers those generic patterns can't,
    such as N28's AP/RV. With no name_map (the default), behavior is
    byte-identical to before this parameter existed."""
    name = _LAYER_NAMES.get(layer)
    if name is not None:
        return name
    if 31 <= layer[0] <= 40:
        return f"M{layer[0] - 30}"
    if 51 <= layer[0] <= 59:
        return f"via{layer[0] - 50}"
    if name_map is not None:
        name = name_map.get(layer)
        if name is not None:
            return name
    return "?"


def _read_gds_polygons(gds_path):
    """Parse a GDS with KLayout into {(layer, datatype): [point lists in nm]}."""
    layout = kdb.Layout()
    layout.read(str(gds_path))
    top = layout.top_cell()
    top.flatten(True)
    polygons = {}
    for li in layout.layer_indexes():
        info = layout.get_info(li)
        pts_list = [
            [(p.x, p.y) for p in s.polygon.each_point_hull()]
            for s in top.shapes(li).each()
            if not s.is_text()
        ]
        if pts_list:
            polygons[(info.layer, info.datatype)] = pts_list
    return polygons


def _mode_metadata(profile: str | None) -> dict:
    """Reference/process provenance block for coordinate JSON files.

    No longer carries an ``n28_center_tap_status`` field (n28-rules-slim,
    user directive 2026-07-19): that field recorded whether a specific
    lower-via center-tap probe was expected to fail closed on the retired
    via_restrictions/coverage policy gates. Generation is geometric-only
    now, so there is no longer a standing "expected failure" to report."""
    if profile is None:
        return {
            "reference_mode": True,
            "process_profile": None,
            "via_rule_source": "ind_ref_reconstructed",
            "n28_drc_proof": False,
        }
    return {
        "reference_mode": False,
        "process_profile": profile,
        "via_rule_source": "process_rule_profile",
    }


def emx_port_lines(ports: list[dict]) -> list[str]:
    """EMX ``-p name=signal[:reference]`` lines, sorted by port name.

    ``reference`` present -> ``-p name=signal:reference`` (local-ref port);
    absent -> ``-p name=signal`` (edge port vs the infinite ground plane).
    """
    lines = []
    for p in sorted(ports, key=lambda p: p["name"]):
        ref = p.get("reference")
        lines.append(
            f"-p {p['name']}={p['signal']}:{ref}" if ref
            else f"-p {p['name']}={p['signal']}"
        )
    return lines


_LAYER_COLORS = {
    "M8": "#1f77b4",
    "M9": "#d62728",
    "M10": "#9467bd",
    "via7": "#17becf",
    "via8": "#2ca02c",
    "via9": "#bcbd22",
    # N28 AP-body names (process mode only; see _process_layer_names). Names,
    # not layer numbers -- "AP"/"RV" are already ordinary strings elsewhere
    # in this module (_metal_name), so listing them here does not touch the
    # M11 IP-strip line the way a gds layer number would.
    "AP": "#ff7f0e",
    "RV": "#8c564b",
}


def _render_legend_label(
    layer: tuple[int, int], name_map: dict[tuple[int, int], str] | None = None
) -> str:
    """The exact text _render_png puts in one legend entry -- split out so
    a test can assert on it directly instead of reading PNG pixels."""
    return f"{_layer_display_name(layer, name_map)} ({layer[0]}/{layer[1]})"
