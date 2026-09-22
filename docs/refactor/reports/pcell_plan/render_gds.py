"""Render a GDS top cell to PNG with matplotlib (one colour per layer), optional zoom box and red highlights."""
import sys, json
from pathlib import Path
import klayout.db as kdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon, Rectangle

PALETTE = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf", "#aec7e8", "#ffbb78"]

def render(gds: Path, out: Path, *, title: str = "", zoom=None, highlight=None, layer_names=None, dpi=110, figsize=(6.4, 6.4)):
    ly = kdb.Layout(); ly.read(str(gds))
    top = ly.top_cells()[0]
    dbu = ly.dbu
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    layers = []
    for li in ly.layer_indexes():
        info = ly.get_info(li)
        polys = [p for p in top.begin_shapes_rec(li).each() if p.shape().is_polygon() or p.shape().is_box() or p.shape().is_path()]
        if not polys:
            continue
        layers.append((info.layer, info.datatype, li))
    layers.sort()
    for k, (l, d, li) in enumerate(layers):
        color = PALETTE[k % len(PALETTE)]
        name = (layer_names or {}).get(f"{l}/{d}", f"{l}/{d}")
        first = True
        it = top.begin_shapes_rec(li)
        while not it.at_end():
            sh = it.shape()
            if sh.is_text():
                it.next(); continue
            poly = sh.polygon.transformed(it.trans())
            for hull in [poly.each_point_hull()]:
                pts = [(p.x * dbu, p.y * dbu) for p in hull]
                ax.add_patch(MplPolygon(pts, closed=True, facecolor=color, edgecolor="black", linewidth=0.3, alpha=0.55, label=name if first else None))
                first = False
            it.next()
    # texts (ports)
    for li in ly.layer_indexes():
        it = top.begin_shapes_rec(li)
        while not it.at_end():
            sh = it.shape()
            if sh.is_text():
                t = sh.text.transformed(it.trans())
                ax.plot(t.x * dbu, t.y * dbu, marker="+", color="black", markersize=6)
                ax.annotate(sh.text.string, (t.x * dbu, t.y * dbu), fontsize=7, xytext=(2, 2), textcoords="offset points")
            it.next()
    bbox = top.bbox()
    if zoom:
        x0, y0, x1, y1 = zoom
    else:
        m = 0.05 * max(bbox.width(), bbox.height()) * dbu
        x0, y0, x1, y1 = bbox.left * dbu - m, bbox.bottom * dbu - m, bbox.right * dbu + m, bbox.top * dbu + m
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal")
    ax.set_xlabel("µm"); ax.set_ylabel("µm")
    if highlight:
        for hx0, hy0, hx1, hy1, label in highlight:
            ax.add_patch(Rectangle((hx0, hy0), hx1 - hx0, hy1 - hy0, fill=False, edgecolor="red", linewidth=2))
            ax.annotate(label, (hx0, hy1), color="red", fontsize=9, fontweight="bold", xytext=(0, 3), textcoords="offset points")
    ax.set_title(title, fontsize=10)
    ax.legend(loc="upper right", fontsize=7, framealpha=0.8)
    fig.tight_layout(); fig.savefig(out); plt.close(fig)

if __name__ == "__main__":
    spec = json.loads(Path(sys.argv[1]).read_text())
    for job in spec:
        render(Path(job["gds"]), Path(job["out"]), title=job.get("title", ""), zoom=job.get("zoom"), highlight=job.get("highlight"), layer_names=job.get("layer_names"))
        print("wrote", job["out"])
