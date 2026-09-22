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


def max_opening(OD: float, W: float) -> float:
    """Largest OPENING that ``base_oct_quad`` still honours as a real gap.

    Above this bound the octagon opening leg no longer descends to ``y=OPENING``
    (base_oct_quad switches to its clamped ``OP > (BA - C)`` branch), so the
    ring detaches from ``base_lead_pair`` and the P/N ports float. Bit-exact
    with that branch condition; depends only on ``OD`` and ``W``
    (metal-independent)."""
    C = ceiltogrid(W * math.tan(PI / 8) + 0.005)
    A = roundtogrid(OD / (2 + math.sqrt(2)))
    B = OD - 2 * A
    BA = floortogrid(B / 2 - 0.005)
    return BA - C


def _nm(x_um: float) -> int:
    """Snap a micron coordinate to integer nanometres (Virtuoso dbu)."""
    return int(round(x_um / DBU_UM))


# ---------------------------------------------------------------------------
# layer mapping (tsmcN28_1p10m.proc: M1=31..M10=40, via1=51..via9=59)
# ---------------------------------------------------------------------------


def _metal_index(me) -> int:
    """Metal spelling -> stack index; 'AP' is the top metal (index 11).

    Accepts every spelling the plugin config layer accepts ("10" / "M10" /
    "m10" / 10): the config validators (generator_plugin's
    ``_metal_stack_index_or_none``) advertise those as equivalent, so the
    construction layer must honor the same set -- before the authoring-kit
    fix an "M10" that had passed config validation crashed here with a
    naked ``int("M10")`` ValueError.

    A spelling that is not even digit-shaped (e.g. a typo'd metal name) hits
    the same ``int()`` conversion; that case now fails closed as
    ``PortError`` naming the offending value (gdsfactory review 2026-09-21,
    api-2) instead of leaking python's own generic "invalid literal for
    int()" ``ValueError`` -- ``PortError`` subclasses ``ValueError``, so
    every existing ``except ValueError`` caller is unaffected."""
    if isinstance(me, str):
        token = me.strip()
        if token.upper() == "AP":
            return 11
        if token[:1] in ("m", "M"):
            token = token[1:]
        try:
            return int(token)
        except ValueError:
            raise PortError(f"metal {me!r} is not a recognized conductor spelling") from None
    return int(me)


def _metal_name(idx: int) -> str:
    """Rule-profile conductor name for a stack index (11 == AP)."""
    return "AP" if int(idx) == 11 else f"M{int(idx)}"


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
# N28 process-backed mode: layer/datatype and via rules come from the
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


@dataclass(frozen=True)
class GroundFixtureConfig:
    """M1 ground reference fixture dimensions (um): an inner-margin gap, a ring
    width, and per-port stub width/length/chamfer. Mirrors the product
    ``single_turn_transformer.GroundFixtureConfig`` shape (this port's own
    non-GPL frozen dataclass, not the product pydantic model).

    ``stub_width_by_port_um`` optionally overrides the stub width for specific
    ports, keyed by EMX port *name* (the identifier in ``-p name=signal``
    lines). Ports not listed fall back to the global ``stub_width_um``;
    unknown keys fail closed in ``add_ground_fixture``. Never hashed and
    serialized via ``dataclasses.asdict`` (manifest), so the dict field is
    safe on the frozen dataclass."""

    inner_margin_um: float
    ring_width_um: float
    stub_width_um: float
    stub_length_um: float
    stub_chamfer_um: float
    stub_width_by_port_um: dict[str, float] | None = None


def process_rule_context(profile_id: str) -> ProcessRuleContext:
    """Load a process rule profile (e.g. "n28_1p10m") for process mode."""
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


def _port_dict(
    name: str,
    logical_name: str,
    metal: int,
    label_layer: tuple[int, int],
    point_nm: tuple[int, int],
    zone_nm: tuple[int, int, int, int],
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

    def add_polygon(self, layer: tuple[int, int], points_um) -> None:
        pts = [(_nm(x), _nm(y)) for x, y in points_um]
        self.shapes.append(Shape(layer, pts))

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
        self.ports.append(
            Port(name, logical_name, metal, label_layer, (x_nm, y_nm), zone_nm)
        )

    def inst(self, child: Cell, origin_um: tuple[float, float], orient: str) -> None:
        if orient not in _ORIENTS:
            raise PortError(f"unsupported orientation {orient!r}")
        self.insts.append(Inst(child, (_nm(origin_um[0]), _nm(origin_um[1])), orient))

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
        """Yield (port, point_nm, zone_nm) with all instance transforms
        applied -- the ``__port__`` analogue of ``flat_shapes``/
        ``flat_labels`` (port contract 2026-09-21)."""
        for item in self._flat(lambda p: p):
            if item[0] == "__port__":
                yield item[1], item[2], item[3]

    def _flat(self, xf):
        for s in self.shapes:
            yield ("__poly__", s.layer, [xf(p) for p in s.points_nm])
        for lb in self.labels:
            yield ("__label__", lb.layer, lb.text, xf(lb.point_nm))
        for p in self.ports:
            yield ("__port__", p, xf(p.point_nm), _transform_box(p.lead_zone_nm, xf))
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
    C = ceiltogrid(W * math.tan(PI / 8) + 0.005)
    C2 = ceiltogrid(C / math.sqrt(2))
    OOCH = W + S / 2 - C2
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
        _tag, port, point_nm, zone_nm = item
        out.append(_port_dict(port.name, port.logical_name, port.metal,
                              port.label_layer, point_nm, zone_nm))
    _check_port_lattice_invariant(out)
    return out


# ---------------------------------------------------------------------------
# ground reference fixture (M1 ring + per-port chamfered stub + G0n pin)
# ---------------------------------------------------------------------------


def _drawing_bbox_um(cell: Cell) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for _layer, pts in cell.flat_shapes():
        for x, y in pts:
            xs.append(x)
            ys.append(y)
    if not xs:
        raise PortError("ground fixture: cell has no drawing geometry")
    return (min(xs) * DBU_UM, min(ys) * DBU_UM,
            max(xs) * DBU_UM, max(ys) * DBU_UM)


def _body_bbox_um(
    cell: Cell, process: ProcessRuleContext | None
) -> tuple[float, float, float, float]:
    """Drawing bbox excluding each port's own lead (port contract
    2026-09-21).

    A lead is identified by its registered ``lead_zone_nm``: subtracted,
    per same-layer polygon, from that polygon's own region (spec.md's
    "用zone做区域布尔减法" -- not "does the port's point fall inside some
    polygon's bbox", which a coordinate a few nm off its own lead could
    silently miss, or which over-matches a bigger fused polygon that only
    partly belongs to the lead -- xfm_tw's ring-0 arc, whose own zone now
    covers only its stub, ``_tw_stub_zone``). What remains after
    subtraction is the winding body, bridges, and via landings that the
    ground-ring ``inner_margin_um`` protects; this distinction lets a
    normal outward lead keep the exact ``stub_length_um`` contract while
    an unequal-OD winding body can still push the ring farther out
    instead of lying underneath it. A layer with no registered port zone
    keeps its whole polygon (nothing to subtract)."""
    port_zones_by_layer: dict[tuple[int, int], list[tuple[int, int, int, int]]] = {}
    for port in cell.emx_ports:
        layer = _metal(port["metal_index"], process)
        port_zones_by_layer.setdefault(layer, []).append(tuple(port["lead_zone_nm"]))

    xs: list[int] = []
    ys: list[int] = []
    for layer, pts in cell.flat_shapes():
        # Radially impossible compact/multi-turn inputs can collapse an
        # intermediate polygon to no vertices.  It contributes no drawing
        # extent; leave feasibility to the product DRC gate instead of
        # leaking a generic ``min() arg is an empty sequence`` exception.
        if not pts:
            continue
        zones = port_zones_by_layer.get(layer)
        if zones:
            poly = kdb.Region(kdb.Polygon([kdb.Point(x, y) for x, y in pts]))
            leads = kdb.Region()
            for x0, y0, x1, y1 in zones:
                leads.insert(
                    kdb.Box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                )
            remainder = (poly - leads).merged()
            if remainder.is_empty():
                continue
            b = remainder.bbox()
            xs.extend((b.left, b.right))
            ys.extend((b.bottom, b.top))
            continue
        xmin = min(x for x, _y in pts)
        ymin = min(y for _x, y in pts)
        xmax = max(x for x, _y in pts)
        ymax = max(y for _x, y in pts)
        xs.extend((xmin, xmax))
        ys.extend((ymin, ymax))
    if not xs:
        raise PortError("ground fixture: cell has no non-port body geometry")
    return (min(xs) * DBU_UM, min(ys) * DBU_UM,
            max(xs) * DBU_UM, max(ys) * DBU_UM)


def add_ground_fixture(cell: Cell, fixture: GroundFixtureConfig,
                       process: ProcessRuleContext | None = None) -> None:
    """Draw an M1 ground ring + one chamfered stub per port + a ``G{index:02d}``
    local-ref pin label on the M1 pin layer at each port's ``(x, y)``, then set
    that port's ``reference`` to its G-pin name. Stub width per port comes from
    ``fixture.stub_width_by_port_um`` (keyed by port name) with fallback to the
    global ``stub_width_um``. Each port is assigned to its nearest body edge,
    so left/right ports get horizontal stubs while top/bottom ports (xfm_tw)
    get vertical stubs and the ring remains outside the body envelope. Fails
    closed with ``PortError`` when the cell has no ``emx_ports``, M1 has no pin
    layer, or the per-port map names a port that does not exist on the cell."""
    if not cell.emx_ports:
        raise PortError("ground fixture: cell has no emx_ports")
    if fixture.stub_width_by_port_um:
        port_names = {p["name"] for p in cell.emx_ports}
        unknown = sorted(set(fixture.stub_width_by_port_um) - port_names)
        if unknown:
            raise PortError(
                f"ground fixture: stub_width_by_port_um names unknown ports "
                f"{unknown}; cell ports are {sorted(port_names)}"
            )
    m1_draw, m1_pin = metal_drawing_pin(1, process)
    xmin, ymin, xmax, ymax = _drawing_bbox_um(cell)
    body_xmin, body_ymin, body_xmax, body_ymax = _body_bbox_um(cell, process)
    ports = cell.emx_ports

    def xy_um(p: dict) -> tuple[float, float]:
        # (port contract 2026-09-21) the classification below and every
        # stub/ring vertex it drives read the authoritative integer nm
        # point directly -- never a re-derived float.
        gx, gy = p["point_nm"]
        return gx * DBU_UM, gy * DBU_UM

    distances_by_port = []
    for p in ports:
        x, y = xy_um(p)
        distances = {
            "left": abs(x - xmin),
            "right": abs(xmax - x),
            "bottom": abs(y - ymin),
            "top": abs(ymax - y),
        }
        distances_by_port.append((p, min(distances, key=distances.get)))
    left_x = [xy_um(p)[0] for p, side in distances_by_port if side == "left"]
    right_x = [xy_um(p)[0] for p, side in distances_by_port if side == "right"]
    bottom_y = [xy_um(p)[1] for p, side in distances_by_port if side == "bottom"]
    top_y = [xy_um(p)[1] for p, side in distances_by_port if side == "top"]
    # Every side obeys both constraints: exact stub reach from any port and
    # inner-margin clearance from the winding body.  The farther-out bound
    # wins.  Normal outward leads already extend beyond the body margin, so
    # their historical exact stub_length geometry remains unchanged; only a
    # protruding unequal-OD body expands the ring.
    inner_xmin = min(
        min((x - fixture.stub_length_um for x in left_x), default=xmin),
        body_xmin - fixture.inner_margin_um,
    )
    inner_xmax = max(
        max((x + fixture.stub_length_um for x in right_x), default=xmax),
        body_xmax + fixture.inner_margin_um,
    )
    inner_ymin = min(
        min((y - fixture.stub_length_um for y in bottom_y), default=ymin),
        body_ymin - fixture.inner_margin_um,
    )
    inner_ymax = max(
        max((y + fixture.stub_length_um for y in top_y), default=ymax),
        body_ymax + fixture.inner_margin_um,
    )
    outer_xmin = inner_xmin - fixture.ring_width_um
    outer_xmax = inner_xmax + fixture.ring_width_um
    outer_ymin = inner_ymin - fixture.ring_width_um
    outer_ymax = inner_ymax + fixture.ring_width_um
    cell.add_rect(m1_draw, outer_xmin, outer_ymin, inner_xmin, outer_ymax)
    cell.add_rect(m1_draw, inner_xmax, outer_ymin, outer_xmax, outer_ymax)
    cell.add_rect(m1_draw, inner_xmin, inner_ymax, inner_xmax, outer_ymax)
    cell.add_rect(m1_draw, inner_xmin, outer_ymin, inner_xmax, inner_ymin)
    by_port = fixture.stub_width_by_port_um or {}
    ch = fixture.stub_chamfer_um
    for index, (p, side) in enumerate(distances_by_port, start=1):
        x, y = xy_um(p)
        half = by_port.get(p["name"], fixture.stub_width_um) / 2.0
        ref_name = f"G{index:02d}"
        if side == "left":
            root = inner_xmin
            cell.add_polygon(m1_draw, [
                (root, y - half - ch), (x, y - half),
                (x, y + half), (root, y + half + ch)])
        elif side == "right":
            root = inner_xmax
            cell.add_polygon(m1_draw, [
                (x, y - half), (root, y - half - ch),
                (root, y + half + ch), (x, y + half)])
        elif side == "bottom":
            root = inner_ymin
            cell.add_polygon(m1_draw, [
                (x - half - ch, root), (x - half, y),
                (x + half, y), (x + half + ch, root)])
        else:
            root = inner_ymax
            cell.add_polygon(m1_draw, [
                (x - half, y), (x - half - ch, root),
                (x + half + ch, root), (x + half, y)])
        cell.add_label(m1_pin, ref_name, x, y)
        p["reference"] = ref_name


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
    wide-parallel rule whose width threshold W exceeds (winding arcs
    always exceed the parallel-length thresholds). Rule-generic -- read
    from the loaded profile, never per-process constants (six-family
    tight-spacing clearance, 2026-07-28)."""
    floor = _min_met_spacing(top_met, process, None)
    if process is None:
        return floor
    name = _metal_name(top_met)
    passive = process.adapter.profile.layout_rules.passive_region
    for rule in passive.wide_parallel_spacing:
        if name in rule.metals and W > rule.when_width_gt_um:
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
    C = ceiltogrid(W * math.tan(PI / 8) + 0.005)
    div = 2.0 + math.sqrt(2.0)
    worst = None
    for od_out, od_in in zip(ring_ods, ring_ods[1:]):
        a_out = roundtogrid(od_out / div)
        a_in = roundtogrid(od_in / div)
        ba_out = floortogrid((od_out - 2.0 * a_out) / 2.0 - 0.005)
        ba_in = floortogrid((od_in - 2.0 * a_in) / 2.0 - 0.005)
        pitch = (od_out - od_in) / 2.0
        sep = (pitch + ba_out - ba_in - C - W) / math.sqrt(2.0)
        worst = sep if worst is None else min(worst, sep)
    if worst >= floor - 1e-9:
        return 0
    delta = math.ceil((floor - worst) * math.sqrt(2.0) / GRID_UM - 1e-9)
    if delta > 4:
        raise PortError(
            f"chamfer staircase cannot recover a "
            f"{floor - worst:.4f} um 45-degree-edge shortfall within 4 "
            f"grid steps on M{top_met} (W={W}, floor={floor}); this is "
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
    """Rule-profile conductor name whose drawing layer is ``layer`` (1P10M+AP
    stack), or None if the layer is not a conductor the profile models (e.g.
    a via/pin layer). No hardcoded layer numbers -- every candidate comes from
    the adapter."""
    for idx in range(1, 12):
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
    """Collect several metal regions in one hierarchy traversal."""
    drawings = {
        _metal(metal, process): index for index, metal in enumerate(metals)
    }
    regions = [kdb.Region() for _ in metals]
    for layer, points in cell.flat_shapes():
        index = drawings.get(layer)
        if index is not None and points:
            regions[index].insert(
                kdb.Polygon([kdb.Point(x, y) for x, y in points])
            )
    return [region.merged() for region in regions]


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


_TW_GRID_DBU = int(round(GRID_UM / DBU_UM))  # 5 (0.005 um mask grid, in nm)


def _tw_snap_dbu_to_grid(v: int) -> int:
    """Snap an integer dbu (nm) coordinate to the nearest 0.005 um mask-grid
    multiple.

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
    return int(round(v / _TW_GRID_DBU)) * _TW_GRID_DBU


def _tw_add_wide_path(cell: Cell, layer: tuple[int, int], pts_um: list,
                      width_um: float) -> None:
    """Draw a width-``width_um`` conductor along the ``pts_um`` centerline
    (mitred polygon via ``kdb.Path``, matching the octagon chamfer vertices
    already baked into the waypoints), snapped to the 0.005 um mask grid
    (``_tw_snap_dbu_to_grid``, see its own docstring for why the mitre
    corners need this even though the centerline waypoints are grid-exact)."""
    pts_nm = [kdb.Point(_nm(x), _nm(y)) for x, y in pts_um]
    poly = kdb.Path(pts_nm, _nm(width_um)).polygon()
    cell.shapes.append(Shape(layer, [
        (_tw_snap_dbu_to_grid(p.x), _tw_snap_dbu_to_grid(p.y))
        for p in poly.each_point_hull()
    ]))


_TW_FIXED_PORT_ORDER = ["P1", "N1", "P2", "N2"]

_LAYER_NAMES = {metal_layer(m): f"M{m}" for m in range(1, 11)}
_LAYER_NAMES.update({via_layer(m): f"via{m}" for m in range(1, 10)})


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
