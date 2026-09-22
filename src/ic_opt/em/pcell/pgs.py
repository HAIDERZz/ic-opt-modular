"""Optional fishbone shield on the real M1 layer, tied once to the ground ring.

The existing winding generators remain unchanged. Unlike a floating bitmap
shield, this product geometry has an explicit connection to the EMX reference.
"""
from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field


class CleanPortPgsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    strip_width_um: float = Field(gt=0)
    strip_spacing_um: float = Field(gt=0)
    margin_um: float = Field(gt=0)


def add_pgs(cell, config: CleanPortPgsConfig, process_profile: str,
            ring_width_um: float) -> dict:
    """Add horizontal fingers and a central spine tied to the upper M1 ring.

    The shield is inset from the winding-body envelope. Existing reference
    stubs may occupy that envelope for some asymmetric configurations; reject
    contact with them instead of silently making extra ground loops.
    """
    import klayout.db as kdb

    from ic_opt.em.pcell.drc_audit import wide_parallel_spacing_violations

    from ._pcell_core import (
        DBU_UM,
        GRID_UM,
        Cell,
        PortError,
        Shape,
        _body_bbox_um,
        process_rule_context,
    )

    context = process_rule_context(process_profile)
    rule = context.adapter.metal_rule("M1")
    layer = tuple(rule.drawing)
    grid = round(GRID_UM / DBU_UM)
    # Even grid multiples keep both edges of a centred strip on the mask grid.
    width = 2 * math.ceil(config.strip_width_um / (2 * GRID_UM)) * grid
    spacing = math.ceil(config.strip_spacing_um / GRID_UM) * grid
    if rule.min_width_um is None or rule.min_space_um is None:
        raise PortError("PGS requires M1 width and spacing rules in the process profile")
    if (config.strip_width_um < rule.min_width_um
            or config.strip_spacing_um < rule.min_space_um):
        raise PortError("PGS strip width/spacing is below the process M1 rule")
    if rule.max_width_um is not None and width * DBU_UM > rule.max_width_um:
        raise PortError("PGS strip width exceeds the process M1 maximum")

    ground = kdb.Region()
    body = Cell("pgs_body", "pgs_body", {}, emx_ports=cell.emx_ports)
    for drawing, points in cell.flat_shapes():
        if drawing == layer:
            ground.insert(kdb.Polygon([kdb.Point(x, y) for x, y in points]))
        else:
            body.shapes.append(Shape(drawing, points))
    ground.merge()
    if ground.count() != 1:
        raise PortError("PGS requires one connected M1 ground-reference fixture")

    xmin, ymin, xmax, ymax = _body_bbox_um(body, context)
    inset = config.margin_um
    left = math.ceil((xmin + inset) / GRID_UM) * grid
    right = math.floor((xmax - inset) / GRID_UM) * grid
    bottom = math.ceil((ymin + inset) / GRID_UM) * grid
    top = math.floor((ymax - inset) / GRID_UM) * grid
    half = width // 2
    if right - left < 3 * width or top - bottom < 3 * width + 2 * spacing:
        raise PortError("PGS margin/strip dimensions leave no usable fishbone area")
    cx = round((left + right) / (2 * grid)) * grid
    cy = round((bottom + top) / (2 * grid)) * grid
    pitch = width + spacing
    first = math.ceil((bottom + half - cy) / pitch)
    last = math.floor((top - half - cy) / pitch)
    comb = kdb.Region(kdb.Box(cx - half, bottom, cx + half, top))
    for index in range(first, last + 1):
        y = cy + index * pitch
        comb.insert(kdb.Box(left, y - half, right, y + half))
    comb.merge()
    clearance = math.ceil(rule.min_space_um / DBU_UM)
    if not (comb & ground).is_empty() or not comb.separation_check(ground, clearance).is_empty():
        raise PortError("PGS fingers touch or approach an existing ground stub; increase margin")

    ring_inner_top = ground.bbox().top - round(ring_width_um / DBU_UM)
    tie_end = ring_inner_top + min(width, round(ring_width_um / DBU_UM))
    tie = kdb.Region(kdb.Box(cx - half, top - width, cx + half, tie_end))
    shield = (comb + tie).merged()
    contact = (shield & ground).merged()
    if contact.count() != 1 or contact.bbox().bottom < ring_inner_top:
        raise PortError("PGS spine must connect only to the upper ground ring")
    if shield.count() != 1 or any(p.holes() for p in shield.each()):
        raise PortError("PGS must be a connected fishbone without closed metal loops")

    combined = (ground + shield).merged()
    minimum_width = math.ceil(rule.min_width_um / DBU_UM)
    wide = wide_parallel_spacing_violations(
        combined, metal_name="M1", adapter=context.adapter, dbu=DBU_UM)
    if (not combined.width_check(minimum_width).is_empty()
            or not combined.space_check(clearance).is_empty() or wide.count):
        raise PortError("PGS and ground fixture violate the process M1 width/spacing rules")
    if (rule.max_width_um is not None
            and not shield.sized(-round(rule.max_width_um / (2 * DBU_UM))).is_empty()):
        raise PortError("PGS junction exceeds the process M1 maximum width")
    for polygon in shield.each():
        cell.shapes.append(Shape(layer, [(p.x, p.y) for p in polygon.each_point_hull()]))
    return {
        "kind": "fishbone", "metal": "M1", "drawing": list(layer),
        "connection": "single upper ground-ring connection",
        "strip_width_um": width * DBU_UM,
        "strip_spacing_um": spacing * DBU_UM,
        "finger_count": last - first + 1,
        "body_bounds_um": [v * DBU_UM for v in (left, bottom, right, top)],
        "added_area_um2": (shield - ground).area() * DBU_UM**2,
    }
