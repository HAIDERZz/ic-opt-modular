"""Which port labels are electrically connected in a GDS: metal polygons joined by the vias the profile says join them.

An independent check on a written file (nothing from the in-memory Cell):
every conductor's merged polygons are nodes, a via cut that overlaps a polygon
on each of the two conductors it connects joins them, and a port's net is the
polygon its label sits on. ``nets(gds, profile)`` returns ``{label: net}`` with
nets numbered from 1 in order of first appearance.
"""

from __future__ import annotations

from pathlib import Path

import klayout.db as kdb

from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter


def nets(gds: Path | str, profile_id: str) -> dict[str, int]:
    adapter = get_geometry_rule_adapter(profile_id)
    catalog = adapter.profile.layer_catalog
    layout = kdb.Layout()
    layout.read(str(gds))
    top = layout.top_cells()[0]

    def region(drawing):
        index = layout.find_layer(kdb.LayerInfo(*drawing))
        return kdb.Region() if index is None else kdb.Region(top.begin_shapes_rec(index)).merged()

    polygons: dict[str, list[kdb.Polygon]] = {name: list(region(c.drawing).each()) for name, c in catalog.conductors.items()}
    parent: dict[tuple[str, int], tuple[str, int]] = {}

    def find(node):
        while parent.setdefault(node, node) != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a, b):
        parent[find(a)] = find(b)

    def touching(name: str, shape: kdb.Polygon) -> list[int]:
        probe = kdb.Region(shape)
        return [i for i, poly in enumerate(polygons[name]) if not (kdb.Region(poly) & probe).is_empty()]

    for via in catalog.vias.values():
        lower, upper = via.connects
        for cut in region(via.drawing).each():
            below, above = touching(lower, cut), touching(upper, cut)
            for i in below[1:]:
                union((lower, below[0]), (lower, i))
            for j in above[1:]:
                union((upper, above[0]), (upper, j))
            if below and above:
                union((lower, below[0]), (upper, above[0]))

    labels: list[tuple[str, tuple[str, int] | None]] = []
    for name, conductor in catalog.conductors.items():
        for layer in (conductor.drawing, conductor.pin):
            if layer is None:
                continue
            index = layout.find_layer(kdb.LayerInfo(*layer))
            if index is None:
                continue
            it = top.begin_shapes_rec(index)
            while not it.at_end():
                shape = it.shape()
                if shape.is_text():
                    text = shape.text.transformed(it.trans())
                    point = kdb.Point(text.x, text.y)
                    hit = [i for i, poly in enumerate(polygons[name]) if poly.inside(point)]
                    labels.append((shape.text.string, (name, hit[0]) if hit else None))
                it.next()
    numbering: dict[tuple[str, int], int] = {}
    out: dict[str, int] = {}
    for label, node in sorted(labels, key=lambda item: item[0]):
        if node is None:
            out[label] = 0                                   # a label on no metal: net 0, never equal to anything
            continue
        root = find(node)
        out[label] = numbering.setdefault(root, len(numbering) + 1)
    return out
