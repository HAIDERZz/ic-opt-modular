"""Physical GDS comparison: per-layer merged Region XOR plus text labels.

Top-cell names, polygon fragmentation, hierarchy and GDS byte hashes are not
geometry: two files are physically equal when every layer's merged drawing
XORs to empty and their text labels (string, position, transformation) match.
GDS properties are not compared.
This is the project's criterion for "default geometry did not drift".

CLI: ``python -m ic_opt.em.pcell.gds_compare LEFT.gds RIGHT.gds``
prints the comparison as JSON and exits 1 when the files differ.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import klayout.db as kdb


@dataclass(frozen=True)
class PhysicalGds:
    dbu_um: float
    top_cell: str
    regions: dict[str, kdb.Region]
    labels: list[tuple]


def load_physical(path: Path | str) -> PhysicalGds:
    layout = kdb.Layout()
    layout.read(str(path))
    tops = list(layout.top_cells())
    if len(tops) != 1:
        raise ValueError(f"expected one GDS top cell, got {len(tops)}: {path}")
    top = tops[0]
    regions: dict[str, kdb.Region] = {}
    labels: list[tuple] = []
    for index in layout.layer_indexes():
        info = layout.get_info(index)
        key = f"{info.layer}/{info.datatype}"
        # insert() copies the shapes: Region(iterator).merged() can stay bound
        # to the layout and reads back EMPTY once the layout is freed (seen
        # when the layer holds a single shape; exact boundary not mapped),
        # which turns real drift into a false "equal".
        region = kdb.Region()
        region.insert(top.begin_shapes_rec(index))
        region.merge()
        regions[key] = region
        iterator = top.begin_shapes_rec(index)
        while not iterator.at_end():
            shape = iterator.shape()
            if shape.is_text():
                text = shape.text.transformed(iterator.trans())
                labels.append((key, text.string, text.x, text.y, text.trans.rot,
                               text.trans.is_mirror(), text.size, text.font))
            iterator.next()
    return PhysicalGds(layout.dbu, top.name, regions, sorted(labels))


def compare_physical(left: PhysicalGds, right: PhysicalGds) -> dict:
    if left.dbu_um != right.dbu_um:
        raise ValueError(
            f"DBU differs ({left.dbu_um} vs {right.dbu_um}); normalize before comparing")
    dbu = left.dbu_um
    changed = []
    for layer in sorted(set(left.regions) | set(right.regions)):
        difference = (left.regions.get(layer, kdb.Region())
                      ^ right.regions.get(layer, kdb.Region()))
        if not difference.is_empty():
            box = difference.bbox()
            changed.append({
                "layer_datatype": layer,
                "xor_area_um2": difference.area() * dbu ** 2,
                "xor_regions": difference.count(),
                "bbox_um": [v * dbu for v in (box.left, box.bottom, box.right, box.top)],
            })
    labels_equal = left.labels == right.labels
    return {
        "physical_equal": not changed and labels_equal,
        "drawing_xor_empty": not changed,
        "labels_equal": labels_equal,
        "changed_layers": changed,
        "left_top_cell": left.top_cell,
        "right_top_cell": right.top_cell,
        "dbu_um": dbu,
        "removed_labels": [list(label) for label in left.labels if label not in right.labels],
        "added_labels": [list(label) for label in right.labels if label not in left.labels],
    }


def compare_gds(left: Path | str, right: Path | str) -> dict:
    return compare_physical(load_physical(left), load_physical(right))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    args = parser.parse_args(argv)
    result = compare_gds(args.left, args.right)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["physical_equal"] else 1


if __name__ == "__main__":
    sys.exit(main())
