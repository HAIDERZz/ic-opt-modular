"""The ground reference fixture: a ring around the device, one chamfered stub per port and a G<nn> pin each port references.

Every family adds it last (after ``finalize_emx_ports``); EMX then sees each
port against its own local stub instead of an infinite ground plane.

The fixture's metal (T19.1) is the profile's fixture conductor, the bottom
metal, unless ``GroundFixtureConfig.metal`` names another: ``"auto"``, the
highest metal of the stack on which the device draws nothing, or a metal of
the caller's choice that carries nothing of the device. ``fixture_metal`` is
the one place that resolves and checks it; ``add_ground_fixture`` -- every
family, and any plugin generator that calls it -- draws through it.
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
    metal_drawing_pin,
    metal_layer,
    metal_pin_layer,
)

AUTO = "auto"


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
    or a stack position. ``fixture_metal`` resolves it."""

    inner_margin_um: float
    ring_width_um: float
    stub_width_um: float
    stub_length_um: float
    stub_chamfer_um: float
    stub_width_by_port_um: dict[str, float] | None = None
    metal: str | int | None = None


def is_auto(metal) -> bool:
    """Whether a fixture metal value asks for ``"auto"`` (any case, surrounding blanks ignored)."""
    return isinstance(metal, str) and metal.strip().lower() == AUTO


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


def _device_on(cell: Cell, position: int, process: ProcessRuleContext | None, shape_layers: set, label_layers: set) -> str | None:
    """What of the device is on the metal at ``position``, or None when nothing is: the ports whose lead it carries
    (named: that is what EMX refuses), else a shape on its drawing layer or a label on its pin layer (the layer named)."""
    leads = [p["name"] for p in cell.emx_ports if _metal_index(p["metal"]) == position]
    if leads:
        return f"the lead of port {', '.join(leads)}"
    drawing, pin = _metal_layers(position, process)
    if drawing in shape_layers:
        return f"device shapes on layer {drawing[0]}/{drawing[1]}"
    if pin is not None and pin in label_layers:
        return f"device labels on its pin layer {pin[0]}/{pin[1]}"
    return None


def fixture_metal(cell: Cell, config: GroundFixtureConfig, process: ProcessRuleContext | None = None) -> int:
    """The stack position ``cell``'s ground fixture is drawn on (T19.1), from ``config.metal``, against the cell's drawn
    layers -- call it before the fixture is drawn:

    * None: the fixture conductor, the bottom of the stack (position 1) -- today's fixture, unchanged and unchecked;
    * ``"auto"``: the highest metal of the profile's stack on which the device draws nothing (no winding, bridge,
      crossunder, tap stack, lead or port: no shape on its drawing layer, no label on its pin layer, no port on it)
      and which has a pin layer for the G pins. Refused in reference mode (no profile: no stack to choose from), and
      when every metal carries something of the device;
    * a spelling (``"AP"``, ``"9"``, ``"M9"``) or a position: that metal, refused when it carries a port lead (named:
      EMX refuses a port whose reference stub lies on the port lead's own metal) or any other device shape (its layer
      named: the fixture's layer must hold nothing of the device, which is how the footprint tells the two apart).

    Every refusal is a ``PortError``."""
    metal = config.metal
    if metal is None:
        return 1
    with _profile_stack(process):
        shape_layers = {layer for layer, _pts in cell.flat_shapes()}
        label_layers = {layer for _tag, layer, _text, _pt in cell.flat_labels()}
        if is_auto(metal):
            if process is None:
                raise PortError("ground fixture: metal 'auto' picks the highest free metal of the process profile's stack, "
                                "and reference mode (no profile) has no stack to choose from: name a metal, or build "
                                "with a profile")
            for position in range(_stack.size(), 0, -1):
                if _device_on(cell, position, process, shape_layers, label_layers) is None \
                        and _metal_layers(position, process)[1] is not None:
                    return position
            raise PortError(f"ground fixture: metal 'auto' found no metal of the stack {list(_stack.active())} that the "
                            "device draws nothing on and that has a pin layer for the G pins")
        try:
            position = _metal_index(metal)
            on = _device_on(cell, position, process, shape_layers, label_layers)
        except (PortError, ValueError) as exc:
            raise PortError(f"ground fixture: metal {metal!r}: {exc}") from None
        if on is None:
            return position
        why = ("EMX refuses a port whose reference stub lies on the port lead's own metal" if on.startswith("the lead")
               else "the fixture's metal must hold nothing of the device, which is how the footprint tells the two apart")
        raise PortError(f"ground fixture: metal {metal!r} ({_metal_name(position)}) carries {on}: {why}; name a metal "
                        "the device draws nothing on, or 'auto'")




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


def add_ground_fixture(cell: Cell, fixture: GroundFixtureConfig,
                       process: ProcessRuleContext | None = None) -> None:
    """Draw a ground ring + one chamfered stub per port + a ``G{index:02d}``
    local-ref pin label on the fixture metal's pin layer at each port's
    ``(x, y)``, then set that port's ``reference`` to its G-pin name. The
    metal is ``fixture_metal``'s (T19.1): the fixture conductor, the bottom
    metal, unless ``fixture.metal`` names another; ``cell.fixture_metal``
    records the conductor. Stub width per port comes from
    ``fixture.stub_width_by_port_um`` (keyed by port name) with fallback to the
    global ``stub_width_um``. Each port's stub continues its lead outward --
    the side is the port's own orientation (M1.3), so left/right ports get
    horizontal stubs while top/bottom ports (xfm_tw) get vertical stubs and
    the ring remains outside the body envelope. Fails closed with
    ``PortError``, before anything is drawn, when the cell has no
    ``emx_ports``, the fixture metal has no pin layer or is refused
    (``fixture_metal``), or the per-port map names a port that does not exist
    on the cell."""
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
    cell.fixture_metal = conductor
