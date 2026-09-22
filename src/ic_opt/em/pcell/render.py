"""Render a GDS top cell to PNG: one colour per layer, port labels, an optional zoom window and red boxes for audit findings.

``render(gds, png, boxes=[b for v in report.violations for b in v.boxes_um])``
puts a DRC report on the drawing it was made from; ``layer_names(profile_id)``
labels the legend with the profile's conductor / via / marker names.
"""

from __future__ import annotations

from pathlib import Path

import klayout.db as kdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon
from matplotlib.patches import Rectangle

from ic_opt.em.pcell.drc_audit import Box
from ic_opt.em.pcell.process_rules import get_process_rule_profile

PALETTE = ("#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#b07aa1", "#9c755f", "#76b7b2", "#edc948", "#ff9da7", "#bab0ac")


def layer_names(profile_id: str) -> dict[str, str]:
    """``{"66/0": "M6", "66/1": "M6.pin", "65/0": "V56", ...}`` from the profile's layer catalog."""
    catalog = get_process_rule_profile(profile_id).layer_catalog
    names: dict[str, str] = {}
    for name, conductor in catalog.conductors.items():
        names[f"{conductor.drawing[0]}/{conductor.drawing[1]}"] = name
        if conductor.pin is not None:
            names[f"{conductor.pin[0]}/{conductor.pin[1]}"] = f"{name}.pin"
    for name, via in catalog.vias.items():
        names[f"{via.drawing[0]}/{via.drawing[1]}"] = name
    for name, marker in catalog.markers.items():
        names[f"{marker.drawing[0]}/{marker.drawing[1]}"] = name
    return names


def render(gds: Path | str, out: Path | str, *, boxes: list[Box] | tuple[Box, ...] = (), zoom: Box | None = None,
           title: str = "", names: dict[str, str] | None = None, dpi: int = 110, size_in: float = 6.4) -> Path:
    """Draw ``gds``'s top cell into ``out`` (PNG); ``zoom`` and ``boxes`` are (left, bottom, right, top) in um."""
    layout = kdb.Layout()
    layout.read(str(gds))
    top = layout.top_cells()[0]
    dbu = layout.dbu
    fig, ax = plt.subplots(figsize=(size_in, size_in), dpi=dpi)
    drawn = sorted((layout.get_info(i).layer, layout.get_info(i).datatype, i) for i in layout.layer_indexes())
    texts: list[tuple[float, float, str]] = []
    for k, (layer, datatype, index) in enumerate(drawn):
        colour, first = PALETTE[k % len(PALETTE)], True
        label = (names or {}).get(f"{layer}/{datatype}", f"{layer}/{datatype}")
        iterator = top.begin_shapes_rec(index)
        while not iterator.at_end():
            shape = iterator.shape()
            if shape.is_text():
                text = shape.text.transformed(iterator.trans())
                texts.append((text.x * dbu, text.y * dbu, shape.text.string))
            else:
                points = [(p.x * dbu, p.y * dbu) for p in shape.polygon.transformed(iterator.trans()).each_point_hull()]
                ax.add_patch(MplPolygon(points, closed=True, facecolor=colour, edgecolor="black", linewidth=0.3, alpha=0.55, label=label if first else None))
                first = False
            iterator.next()
    for x, y, string in texts:
        ax.plot(x, y, marker="+", color="black", markersize=6)
        ax.annotate(string, (x, y), fontsize=7, xytext=(2, 2), textcoords="offset points")
    for left, bottom, right, top_ in boxes:
        pad = 0.02 * max(right - left, top_ - bottom, 1.0)      # a degenerate (edge-thin) box still shows
        ax.add_patch(Rectangle((left - pad, bottom - pad), right - left + 2 * pad, top_ - bottom + 2 * pad, fill=False, edgecolor="red", linewidth=1.8))
    if zoom is None:
        bbox = top.bbox()
        margin = 0.05 * max(bbox.width(), bbox.height()) * dbu
        zoom = (bbox.left * dbu - margin, bbox.bottom * dbu - margin, bbox.right * dbu + margin, bbox.top * dbu + margin)
    ax.set_xlim(zoom[0], zoom[2])
    ax.set_ylim(zoom[1], zoom[3])
    ax.set_aspect("equal")
    ax.set_xlabel("µm")
    ax.set_ylabel("µm")
    ax.set_title(title, fontsize=10)
    if drawn:
        ax.legend(loc="upper right", fontsize=7, framealpha=0.8)
    fig.tight_layout()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out
