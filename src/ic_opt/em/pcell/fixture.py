"""The ground reference fixture: a ring around the device, one chamfered stub per port and a G<nn> pin each port references.

Every family adds it last (after ``finalize_emx_ports``); EMX then sees each
port against its own local stub instead of an infinite ground plane.

The fixture's metal (T19.1) is the profile's fixture conductor, the bottom
metal, unless ``GroundFixtureConfig.metal`` names another: ``"auto"``, the
highest metal of the stack on which the device draws nothing, or a metal of
the caller's choice that carries nothing of the device. ``metal_rule``
(T19.2) relaxes "nothing of the device" to "no port lead" when it says
``"shared"``: the metal may then carry the device's internal shapes
(crossunders, bridges), which is all EMX's own constraint asks. ``fixture_metal``
is the one place that resolves and checks it; ``add_ground_fixture`` -- every
family, and any plugin generator that calls it -- draws through it, and
records the box of the device it drew around (``Cell.device_bbox_um``: the
footprint's record, since under ``shared`` the fixture's layer no longer tells
the two apart). Two neighbouring stubs whose chamfers would close the opening
between them, at the ring, below the fixture metal's minimum spacing are drawn
with shorter chamfers on their facing sides (N-65: no sharp wedge in the ring's
opening); the build records the chamfers it drew (``Cell.stub_chamfers_um``)
only then. Stubs that would still touch or overlap are refused before anything
is drawn (T19.5): their merged edge would hold two G pins, which EMX refuses.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

import klayout.db as kdb

from ic_opt.em.pcell import stack as _stack
from ic_opt.em.pcell._pcell_core import (
    DBU_UM,
    Cell,
    PortError,
    ProcessRuleContext,
    _metal,
    _metal_index,
    _metal_name,
    _nm,
    metal_drawing_pin,
    metal_layer,
    metal_pin_layer,
)

AUTO = "auto"
FREE = "free"            # metal_rule: the fixture's metal holds nothing of the device (T19.1's rule, the default)
SHARED = "shared"        # metal_rule: it may hold the device's internal shapes, never a port lead (T19.2)
METAL_RULES = (FREE, SHARED)
#: The two chamfered sides of a stub, (low, high) along the ring's side it sits on, named by the way each faces: a stub
#: on the left or right side of the ring runs along x, its sides face down and up; on the bottom or top, left and right.
STUB_SIDES = {"left": ("bottom", "top"), "right": ("bottom", "top"), "bottom": ("left", "right"), "top": ("left", "right")}


@dataclass(frozen=True)
class GroundFixtureConfig:
    """Ground reference fixture dimensions (um): an inner-margin gap, a ring
    width, and per-port stub width/length/chamfer.

    ``stub_width_by_port_um`` optionally overrides the stub width for specific
    ports, keyed by EMX port *name* (the identifier in ``-p name=signal``
    lines). Ports not listed fall back to the global ``stub_width_um``;
    unknown keys fail closed in ``add_ground_fixture``. Never hashed and
    serialized via ``dataclasses.asdict`` (manifest), so the dict field is
    safe on the frozen dataclass.

    ``metal`` (T19.1) is the conductor the ring, the stubs and the G pins go
    on: None, the fixture conductor, the bottom of the stack (the geometry
    every existing configuration and library row has); ``"auto"``, the
    highest metal of the profile's stack on which the device draws nothing;
    or a metal spelling ``_metal_index`` takes (``"AP"``, ``"9"``, ``"M9"``)
    or a stack position. ``fixture_metal`` resolves it.

    ``metal_rule`` (T19.2) says what that metal may carry of the device:
    ``"free"`` (the default) nothing; ``"shared"`` its internal shapes
    (crossunders, bridges) but no port lead. It applies to a ``metal`` that
    is set: with ``metal`` None the fixture stays on the fixture conductor,
    and ``"shared"`` is refused there (nothing is chosen)."""

    inner_margin_um: float
    ring_width_um: float
    stub_width_um: float
    stub_length_um: float
    stub_chamfer_um: float
    stub_width_by_port_um: dict[str, float] | None = None
    metal: str | int | None = None
    metal_rule: str = FREE


def is_auto(metal) -> bool:
    """Whether a fixture metal value asks for ``"auto"`` (any case, surrounding blanks ignored)."""
    return isinstance(metal, str) and metal.strip().lower() == AUTO


def metal_rule_of(config: GroundFixtureConfig) -> str:
    """``config.metal_rule`` as one of ``METAL_RULES`` (any case, surrounding blanks ignored). Anything else is a
    ``PortError``: a plugin generator builds the dataclass itself, past the config model's validation."""
    rule = config.metal_rule.strip().lower() if isinstance(config.metal_rule, str) else config.metal_rule
    if rule not in METAL_RULES:
        raise PortError(f"ground fixture: metal_rule {config.metal_rule!r} is none of {list(METAL_RULES)}")
    return rule


def _profile_stack(process: ProcessRuleContext | None):
    """Positions on the profile's own stack while a fixture metal is resolved. The families already build inside it
    (``stack.builds_on_profile_stack``); a plugin generator that calls ``add_ground_fixture`` gets the same answer
    either way."""
    return contextlib.nullcontext() if process is None else _stack.use_stack(process.adapter.profile)


def _metal_layers(position: int, process: ProcessRuleContext | None) -> tuple[tuple[int, int], tuple[int, int] | None]:
    """(drawing, pin or None) of the metal at ``position`` of the active stack; reference mode: the fixed map."""
    if process is None:
        return metal_layer(position), metal_pin_layer(position)
    spec = process.adapter.layer(_metal_name(position))
    return tuple(spec.drawing), None if spec.pin is None else tuple(spec.pin)


def _device_on(cell: Cell, position: int, process: ProcessRuleContext | None, shape_layers: set, label_layers: set,
               rule: str = FREE) -> str | None:
    """What of the device on the metal at ``position`` ``rule`` refuses, or None when there is none: the ports whose
    lead it carries (named: that is what EMX refuses, under either rule); under ``free`` also a shape on its drawing
    layer or a label on its pin layer (the layer named) -- ``shared`` lets the metal carry the device's internal shapes."""
    leads = [p["name"] for p in cell.emx_ports if _metal_index(p["metal"]) == position]
    if leads:
        return f"the lead of port {', '.join(leads)}"
    if rule == SHARED:
        return None
    drawing, pin = _metal_layers(position, process)
    if drawing in shape_layers:
        return f"device shapes on layer {drawing[0]}/{drawing[1]}"
    if pin is not None and pin in label_layers:
        return f"device labels on its pin layer {pin[0]}/{pin[1]}"
    return None


def fixture_metal(cell: Cell, config: GroundFixtureConfig, process: ProcessRuleContext | None = None) -> int:
    """The stack position ``cell``'s ground fixture is drawn on (T19.1), from ``config.metal`` under
    ``config.metal_rule`` (T19.2), against the cell's drawn layers -- call it before the fixture is drawn:

    * None: the fixture conductor, the bottom of the stack (position 1) -- today's fixture, unchanged and unchecked
      (``metal_rule`` ``"shared"`` is refused here: it says how a metal is chosen, and None chooses none);
    * ``"auto"``: under ``free``, the highest metal of the profile's stack on which the device draws nothing (no
      winding, bridge, crossunder, tap stack, lead or port: no shape on its drawing layer, no label on its pin layer,
      no port on it); under ``shared``, the highest metal that carries no port lead (no port of ``cell.emx_ports`` has
      it as its lead metal), whatever internal shapes it carries. Either way the metal must have a pin layer for the G
      pins. Refused in reference mode (no profile: no stack to choose from), and when no metal qualifies;
    * a spelling (``"AP"``, ``"9"``, ``"M9"``) or a position: that metal, refused when it carries a port lead (named:
      EMX refuses a port whose reference stub lies on the port lead's own metal) under either rule, and under ``free``
      also when it carries any other device shape or label (its layer named).

    Every refusal is a ``PortError``."""
    rule = metal_rule_of(config)
    metal = config.metal
    if metal is None:
        if rule == SHARED:
            raise PortError("ground fixture: metal_rule 'shared' says how the fixture's metal is chosen, and metal None "
                            "chooses none (the fixture conductor, the bottom metal): name a metal or 'auto', or leave the "
                            "rule at 'free'")
        return 1
    with _profile_stack(process):
        shape_layers = {layer for layer, _pts in cell.flat_shapes()} if rule == FREE else set()
        label_layers = {layer for _tag, layer, _text, _pt in cell.flat_labels()} if rule == FREE else set()
        if is_auto(metal):
            if process is None:
                raise PortError("ground fixture: metal 'auto' picks the highest free metal of the process profile's stack, "
                                "and reference mode (no profile) has no stack to choose from: name a metal, or build "
                                "with a profile")
            for position in range(_stack.size(), 0, -1):
                if _device_on(cell, position, process, shape_layers, label_layers, rule) is None \
                        and _metal_layers(position, process)[1] is not None:
                    return position
            what = "that the device draws nothing on" if rule == FREE else "that carries no port lead"
            raise PortError(f"ground fixture: metal 'auto' under metal_rule {rule!r} found no metal of the stack "
                            f"{list(_stack.active())} {what} and that has a pin layer for the G pins")
        try:
            position = _metal_index(metal)
            on = _device_on(cell, position, process, shape_layers, label_layers, rule)
        except (PortError, ValueError) as exc:
            raise PortError(f"ground fixture: metal {metal!r}: {exc}") from None
        if on is None:
            return position
        if on.startswith("the lead"):
            why = (f"EMX refuses a port whose reference stub lies on the port lead's own metal, and no metal_rule allows it "
                   f"(the rule in force: {rule!r}); name a metal that carries no port lead, or 'auto'")
        else:
            why = ("under metal_rule 'free' the metal must hold nothing of the device; 'shared' allows internal shapes; "
                   "name a metal the device draws nothing on, 'auto', or set metal_rule 'shared'")
        raise PortError(f"ground fixture: metal {metal!r} ({_metal_name(position)}) carries {on}: {why}")


def _drawing_bbox_um(cell: Cell) -> tuple[float, float, float, float]:
    """The box (x0, y0, x1, y1, um) around every point of every shape of ``cell`` on every layer, leads included. Taken
    by ``add_ground_fixture`` before it draws, it is the device's box: the footprint's record (T19.2)."""
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
    per same-layer polygon, from that polygon's own region (a region
    boolean with the zone -- not "does the port's point fall inside some
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
        # by the port's conductor NAME: a stack position is only meaningful inside the build's metal stack
        layer = tuple(process.adapter.layer(port["metal"]).drawing) if process is not None else _metal(port["metal_index"], None)
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


def _refuse_contact(cell: Cell, layer: tuple[int, int], shapes: list, conductor: str) -> None:
    """Under ``metal_rule: shared`` the fixture's metal may carry the device's internal shapes, but no fixture shape may
    touch one: the ring and the stubs would short the device to its own reference, and no DRC rule sees that (touching
    shapes on one layer merge into one polygon). The ring keeps ``inner_margin_um`` from the body, but a stub starts at
    its port, and a device shape on the shared metal can reach past the port (xfm_il with 2 um leads: a secondary lead's
    under-pass on the metal below the winding ends in a 5 um via pad, 3 um beyond the port). ``PortError``, before
    anything is drawn, naming where; touching edges and corners count."""
    device = cell.region(layer)                     # the device's own shapes on that metal (nothing of the fixture yet)
    if device.is_empty():
        return
    fixture = kdb.Region()
    for points in shapes:
        fixture.insert(kdb.Polygon([kdb.Point(_nm(x), _nm(y)) for x, y in points]))
    touched = device.interacting(fixture)
    if touched.is_empty():
        return
    box = touched.bbox()
    raise PortError(f"ground fixture: under metal_rule 'shared' the ring and stubs on {conductor} would touch "
                    f"{touched.count()} device shape(s) on that metal, within ({box.left * DBU_UM:g}, "
                    f"{box.bottom * DBU_UM:g}) - ({box.right * DBU_UM:g}, {box.top * DBU_UM:g}) um: a short between the "
                    "device and its reference; name another metal, or use metal_rule 'free'")


def fixture_min_space_um(conductor: str, process: ProcessRuleContext | None) -> float | None:
    """The minimum spacing of the fixture's metal ``conductor`` in the build's profile (its ``metal_width_space`` rule's
    ``min_space_um``): what two neighbouring stubs' chamfers must leave between them at the ring (N-65). None in
    reference mode (no profile) and when the profile states no minimum spacing for that metal: no adjustment then."""
    if process is None:
        return None
    rule = process.adapter.profile.layout_rules.metal_width_space.get(process.adapter.layer(conductor).name)
    return None if rule is None else rule.min_space_um


def _facing_chamfers(stubs: list[tuple[str, float, float]], chamfer: float, min_space_um: float | None,
                     grid_um: float | None) -> list[list[float]]:
    """The chamfer each stub is drawn with on its two sides, ``[low, high]`` per stub (``STUB_SIDES``), in the order of
    ``stubs``: (the ring's side the stub sits on, its centre along that side, its half width), one per port (N-65).

    Every side keeps ``chamfer``, except where two neighbouring stubs on one side of the ring leave a gap between their
    chamfered outlines at the ring's inner edge -- the narrowest place of the opening between them -- below
    ``min_space_um``: both facing chamfers are then shortened, to one value, the largest on the manufacturing grid
    ``grid_um`` at which that gap is at least ``min_space_um`` (exactly it whenever the numbers fall on the grid), never
    below 0 -- at 0 the two sides are plain rectangle edges, which form no wedge. Each side faces one neighbour at most,
    so each is decided once. The gap is measured on the drawn nanometres, each end snapped as ``Cell.add_polygon`` snaps
    it, from the expressions ``add_ground_fixture`` draws. A pair already at or above the minimum, a stub without a
    neighbour, ``chamfer`` 0 and ``min_space_um`` None (reference mode) change nothing. Stubs that still touch at 0 are
    left to ``_refuse_touching_stubs``, a gap between 0 and the minimum at 0 to the DRC gate."""
    chamfers = [[chamfer, chamfer] for _ in stubs]
    if min_space_um is None or chamfer <= 0:
        return chamfers
    least = _nm(min_space_um)
    step = max(1, _nm(grid_um)) if grid_um else 1
    for side in STUB_SIDES:
        row = sorted((centre, i) for i, (on, centre, _half) in enumerate(stubs) if on == side)
        for (low, a), (high, b) in zip(row, row[1:]):
            half_a, half_b = stubs[a][2], stubs[b][2]

            def gap(c: float, low=low, high=high, half_a=half_a, half_b=half_b) -> int:
                return _nm(high - half_b - c) - _nm(low + half_a + c)

            if gap(chamfer) >= least:
                continue
            shortened = min(_nm(chamfer), (gap(0.0) - least) // 2 // step * step)
            while shortened > 0 and gap(round(shortened * DBU_UM, 3)) < least:
                shortened -= step
            chamfers[a][1] = chamfers[b][0] = round(max(shortened, 0) * DBU_UM, 3)
    return chamfers


def _refuse_touching_stubs(stubs: list[tuple[str, str, list]], conductor: str) -> None:
    """Two stubs that touch or overlap make one polygon whose edge holds two ``G`` pins, which EMX refuses (it allows no
    two ports' pins on one edge). ``stubs``: (port name, side, outline) in drawing order. ``PortError`` before anything
    is drawn, naming the two ports and the gap between their outlines, chamfers included -- across the stubs for two on
    one side (their roots, the widest part of each, lie on one line), the separation of their boxes otherwise; 0 is
    touching, below 0 overlapping (T19.5). Exact on the drawn nanometres: each outline is snapped as ``Cell.add_polygon``
    snaps it, and a touching edge or corner counts. Stubs closer than the metal's minimum spacing without touching are the
    DRC gate's to judge (``drc_audit.fixture_exemptions``)."""
    outlines = []
    for name, side, points in stubs:
        polygon = kdb.Polygon([kdb.Point(_nm(x), _nm(y)) for x, y in points])
        outlines.append((name, side, kdb.Region(polygon), polygon.bbox()))
    for i, (name_a, side_a, region_a, a) in enumerate(outlines):
        for name_b, side_b, region_b, b in outlines[i + 1:]:
            if region_a.interacting(region_b).is_empty():
                continue
            across_y = max(b.bottom - a.top, a.bottom - b.top)
            across_x = max(b.left - a.right, a.left - b.right)
            if side_a == side_b:
                gap = across_y if side_a in ("left", "right") else across_x
            else:
                gap = max(across_x, across_y)
            raise PortError(f"ground fixture: the stubs of ports {name_a} and {name_b} on {conductor} "
                            f"{'touch' if gap == 0 else 'overlap'} (gap {gap * DBU_UM:g} um between their outlines, chamfers "
                            "included): one edge would hold both G pins, which EMX refuses; give the two ports more room or "
                            "their stubs less width")


def add_ground_fixture(cell: Cell, fixture: GroundFixtureConfig,
                       process: ProcessRuleContext | None = None) -> None:
    """Draw a ground ring + one chamfered stub per port + a ``G{index:02d}``
    local-ref pin label on the fixture metal's pin layer at each port's
    ``(x, y)``, then set that port's ``reference`` to its G-pin name. The
    metal is ``fixture_metal``'s (T19.1): the fixture conductor, the bottom
    metal, unless ``fixture.metal`` names another (under ``fixture.metal_rule``,
    T19.2); ``cell.fixture_metal`` records the conductor and
    ``cell.device_bbox_um`` the box around everything the device drew, taken
    before the fixture is drawn (``_drawing_bbox_um``: the footprint's
    record, which under ``shared`` the fixture's layer can no longer give).
    Stub width per port comes from
    ``fixture.stub_width_by_port_um`` (keyed by port name) with fallback to the
    global ``stub_width_um``. Each port's stub continues its lead outward --
    the side is the port's own orientation (M1.3), so left/right ports get
    horizontal stubs while top/bottom ports (xfm_tw) get vertical stubs and
    the ring remains outside the body envelope. Fails closed with
    ``PortError``, before anything is drawn, when the cell has no
    ``emx_ports``, the fixture metal has no pin layer or is refused
    (``fixture_metal``), the per-port map names a port that does not exist
    on the cell, two stubs would touch or overlap (``_refuse_touching_stubs``,
    T19.5: one edge would hold two G pins), or -- under ``metal_rule:
    shared`` -- a fixture shape would touch a device shape on the shared
    metal (``_refuse_contact``). Before that check, two neighbouring stubs on
    one side whose chamfers would leave less than the fixture metal's minimum
    spacing between them at the ring get shorter chamfers on their facing
    sides (``_facing_chamfers``, N-65; a shorter chamfer only widens a gap);
    ``cell.stub_chamfers_um`` then records the chamfer drawn per port and
    side, and stays None when every side has ``stub_chamfer_um``."""
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
    # The default (metal None) is today's fixture to the call: position 1 on whatever stack the build opened.
    with _profile_stack(process) if fixture.metal is not None else contextlib.nullcontext():
        met = fixture_metal(cell, fixture, process)
        m1_draw, m1_pin = metal_drawing_pin(met, process)
        conductor = _metal_name(met)
    xmin, ymin, xmax, ymax = _drawing_bbox_um(cell)
    body_xmin, body_ymin, body_xmax, body_ymax = _body_bbox_um(cell, process)
    ports = cell.emx_ports

    def xy_um(p: dict) -> tuple[float, float]:
        # (port contract 2026-09-21) every stub/ring vertex reads the
        # authoritative integer nm point directly -- never a re-derived float.
        gx, gy = p["point_nm"]
        return gx * DBU_UM, gy * DBU_UM

    side_of = {0: "right", 180: "left", 90: "top", 270: "bottom"}
    distances_by_port = [(p, side_of[p["orientation_deg"]]) for p in ports]
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
    # Every fixture shape is laid out first and drawn only once it is accepted, in this order (the ring's four sides,
    # then one stub per port), so the cell -- and the GDS -- is what drawing them one by one gave.
    shapes = [[(x1, y1), (x2, y1), (x2, y2), (x1, y2)] for x1, y1, x2, y2 in (
        (outer_xmin, outer_ymin, inner_xmin, outer_ymax),
        (inner_xmax, outer_ymin, outer_xmax, outer_ymax),
        (inner_xmin, inner_ymax, inner_xmax, outer_ymax),
        (inner_xmin, outer_ymin, inner_xmax, inner_ymin))]
    pins = []
    stubs = []                                              # (port, side, outline): no two may touch (T19.5)
    by_port = fixture.stub_width_by_port_um or {}
    ch = fixture.stub_chamfer_um
    laid = []                                               # (port, side, x, y, half width), in drawing order
    for p, side in distances_by_port:
        x, y = xy_um(p)
        laid.append((p, side, x, y, by_port.get(p["name"], fixture.stub_width_um) / 2.0))
    # N-65: neighbours whose chamfers would leave less than the metal's minimum spacing at the ring get shorter facing
    # chamfers -- before T19.5's check, since a shorter chamfer only widens a gap
    chamfers = _facing_chamfers(
        [(side, y if side in ("left", "right") else x, half) for _p, side, x, y, half in laid], ch,
        fixture_min_space_um(conductor, process),
        None if process is None else process.adapter.profile.layout_rules.manufacturing_grid_um)
    for index, ((p, side, x, y, half), (lo, hi)) in enumerate(zip(laid, chamfers), start=1):
        if side == "left":
            root = inner_xmin
            shapes.append([
                (root, y - half - lo), (x, y - half),
                (x, y + half), (root, y + half + hi)])
        elif side == "right":
            root = inner_xmax
            shapes.append([
                (x, y - half), (root, y - half - lo),
                (root, y + half + hi), (x, y + half)])
        elif side == "bottom":
            root = inner_ymin
            shapes.append([
                (x - half - lo, root), (x - half, y),
                (x + half, y), (x + half + hi, root)])
        else:
            root = inner_ymax
            shapes.append([
                (x - half, y), (x - half - lo, root),
                (x + half + hi, root), (x + half, y)])
        stubs.append((p["name"], side, shapes[-1]))
        pins.append((p, f"G{index:02d}", x, y))
    _refuse_touching_stubs(stubs, conductor)
    if metal_rule_of(fixture) == SHARED:
        _refuse_contact(cell, m1_draw, shapes, conductor)
    for points in shapes:
        cell.add_polygon(m1_draw, points)
    for p, ref_name, x, y in pins:
        cell.add_label(m1_pin, ref_name, x, y)
        p["reference"] = ref_name
    cell.fixture_metal = conductor
    cell.device_bbox_um = (xmin, ymin, xmax, ymax)          # taken above, before the first fixture shape
    # the chamfer drawn per port and side, recorded only when N-65 shortened one (an unchanged build records nothing)
    cell.stub_chamfers_um = ({p["name"]: dict(zip(STUB_SIDES[side], pair)) for (p, side, *_), pair in zip(laid, chamfers)}
                             if any(c != ch for pair in chamfers for c in pair) else None)
