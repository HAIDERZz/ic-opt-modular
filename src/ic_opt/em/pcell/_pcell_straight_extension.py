"""Extend central horizontal winding straights without scaling side routing."""

from __future__ import annotations

import math
from copy import deepcopy

from ic_opt.em.pcell._pcell_core import (
    DBU_UM,
    GRID_UM,
    Cell,
    Label,
    Port,
    PortError,
    ProcessRuleContext,
    Shape,
    _nm,
    transform_point,
    via_layer,
)

_RIGID_FUNCTIONS = {
    "vias", "vias_nomet", "base_ind_diag", "base_xfm_cross",
    "base_lead", "base_lead_pair",
}


def _check_rigid_instances(cell: Cell, center_nm: int, transform) -> None:
    """Protect pads even when same-layer vias contain no actual via cuts."""
    if cell.function in _RIGID_FUNCTIONS:
        xs = [transform(point)[0] for _, points in cell.flat_shapes() for point in points]
        if xs and min(xs) <= center_nm <= max(xs):
            raise PortError(
                f"STRAIGHT_EXTENSION: rigid {cell.function} touches or crosses "
                "the local X cut; its routing/landing dimensions must stay fixed"
            )
        return
    for inst in cell.insts:
        def child_transform(point, inst=inst, parent_transform=transform):
            x, y = transform_point(point, inst.orient)
            ox, oy = inst.origin_nm
            return parent_transform((x + ox, y + oy))

        _check_rigid_instances(inst.cell, center_nm, child_transform)


def extend_straight_x(
    cell: Cell,
    extension_um: float,
    *,
    center_x_um: float = 0.0,
    process: ProcessRuleContext | None = None,
) -> Cell:
    """Add total X width in 10nm steps about one winding's local centre.

    Left/right geometry translates rigidly by half the requested extension;
    points on the cut stay put so horizontal winding edges grow continuously.
    Sloped edges touching the cut and all via cuts touching it fail closed.
    Side-routing instances are additionally protected, including metal-only
    via landings. Ground fixtures must be added after this transformation.

    Zero returns the original object, preserving legacy hierarchy and params.
    Positive extension creates an independent flat cell; its params record the
    transformation rather than retaining a stale untransformed instance tree.
    """
    try:
        valid = not isinstance(extension_um, bool) and math.isfinite(extension_um)
    except (TypeError, ValueError):
        valid = False
    if not valid or extension_um < 0:
        raise PortError("STRAIGHT_EXTENSION must be finite and non-negative")
    step = 2 * GRID_UM
    steps = extension_um / step
    if (not math.isfinite(steps)
            or not math.isclose(steps, round(steps), rel_tol=0.0, abs_tol=1e-7)
            or (extension_um != 0 and round(steps) == 0)):
        raise PortError("STRAIGHT_EXTENSION must use 0.01 um steps")
    if extension_um == 0:
        return cell
    if not math.isfinite(center_x_um):
        raise PortError("STRAIGHT_EXTENSION local X centre must be finite")
    half_nm = round(steps) * _nm(GRID_UM)
    center_nm = _nm(center_x_um)
    _check_rigid_instances(cell, center_nm, lambda point: point)
    cut_layers = (
        {via_layer(i) for i in range(1, 11)} if process is None else
        {tuple(via.drawing) for via in process.adapter.profile.layer_catalog.vias.values()}
    )

    def shifted(point):
        x, y = point
        return (x + (half_nm if x > center_nm else -half_nm if x < center_nm else 0), y)

    shapes = []
    for layer, points in cell.flat_shapes():
        if points and layer in cut_layers:
            xs = [x for x, _ in points]
            if min(xs) <= center_nm <= max(xs):
                raise PortError(
                    "STRAIGHT_EXTENSION: via cut touches or crosses the local X cut"
                )
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1], strict=True):
            if min(x1, x2) <= center_nm <= max(x1, x2) and x1 != x2 and y1 != y2:
                raise PortError(
                    "STRAIGHT_EXTENSION: non-vertical edges touching the local X "
                    "cut must be horizontal winding straights"
                )
        shapes.append(Shape(layer, [shifted(point) for point in points]))

    extension = 2 * half_nm * DBU_UM
    params = deepcopy(cell.params)
    params["STRAIGHT_EXTENSION"] = extension
    result = Cell(f"{cell.name}_SX{extension:g}", cell.function, params, shapes=shapes)
    result.labels = [
        Label(layer, text, shifted(point))
        for _, layer, text, point in cell.flat_labels()
    ]
    # (port contract 2026-09-21) ports are a third kind of object flattened
    # and shifted with the SAME integer translation as shapes/labels above
    # -- not a fourth, independent path that rounds an already-rounded
    # label_xy_um and shifts THAT (the old mechanism-3 pattern this
    # replaces). The caller's own finalize_emx_ports() (always run AFTER
    # extend_straight_x -- see ind_sym/xfm_bs/xfm_ms) derives emx_ports
    # from result.ports, so nothing else needs setting here.
    shifted_ports = []
    for port, point_nm, zone_nm, direction in cell.flat_ports():
        zx0, zy0, zx1, zy1 = zone_nm
        c0, c1 = shifted((zx0, zy0)), shifted((zx1, zy1))
        shifted_ports.append(Port(
            port.name, port.logical_name, port.metal, port.label_layer,
            shifted(point_nm),
            (min(c0[0], c1[0]), min(c0[1], c1[1]),
             max(c0[0], c1[0]), max(c0[1], c1[1])),
            direction, port.width_nm))                     # a translation leaves the direction and width as flattened
    result.ports = shifted_ports
    return result
