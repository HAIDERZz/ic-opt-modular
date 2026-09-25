"""M0.5: D4 status on the current code. Build an OD x NT x W x S grid of ind_sym on a profile (no EMX) and, for every
accepted build, (1) audit it with coordinates and (2) apply the September review's D4 containment predicate: on TOP_ME the
raw (un-merged) small axis-aligned squares are the crossover landing pads, everything else is the ring, and a pad "hangs"
by the fraction of its area outside the ring. Optionally replays recorded NT>=3 points too.
Usage: d4_grid.py PROFILE METAL OUT.json [PNG_DIR] [RECORD_DIR]   (METAL: the ring metal, "6" or "M6"; N-14: M2.2 names)"""
import collections
import itertools
import json
import sys
import tempfile
from pathlib import Path

import klayout.db as kdb

from ic_opt.em.pcell import get_generator
from ic_opt.em.pcell.drc_audit import audit_gds
from ic_opt.em.pcell.render import layer_names, render
from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter


def pad_hang(gds_path, drawing, w_um) -> list[float]:
    """Fraction of each crossover landing pad (raw <=1.5W axis-aligned square on the ring metal) lying outside the ring."""
    layout = kdb.Layout()
    layout.read(str(gds_path))
    top = layout.top_cells()[0]
    index = layout.layer(*drawing)
    limit = round(1.5 * w_um / layout.dbu)
    pads, ring = [], kdb.Region()
    it = top.begin_shapes_rec(index)
    while not it.at_end():
        shape = it.shape()
        if not shape.is_text():
            poly = shape.polygon.transformed(it.trans())
            box = poly.bbox()
            if poly.num_points() == 4 and box.width() <= limit and box.height() <= limit and poly.is_box():
                pads.append(box)
            else:
                ring.insert(poly)
        it.next()
    ring.merge()
    return [(kdb.Region(b) - ring).area() / b.area() for b in pads]

profile, metal, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
png_dir = Path(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] != "-" else None
record_dir = Path(sys.argv[5]) if len(sys.argv) > 5 else None
drawing = get_geometry_rule_adapter(profile).layer(f"M{metal}" if metal.isdigit() else metal).drawing
FIX = {"inner_margin_um": 15.0, "ring_width_um": 20.0, "stub_length_um": 2.0, "stub_chamfer_um": 0.0}
g = get_generator("clean_port_ind_sym", plugin_module="builtin:clean_port")
grid = [(od, nt, w, s, "grid") for od, nt, w, s in itertools.product([60, 65, 70, 80, 90, 100, 120, 150], [3, 4, 5], [4.0, 5.0, 6.0, 8.0, 10.0], [2.0, 2.5, 3.0, 4.0])]
if record_dir is not None:                        # every recorded NT>=3 point of this body, rebuilt with the current code
    for point in sorted(record_dir.iterdir()):
        m = point / "geometry_manifest.json"
        if m.exists():
            c = json.loads(m.read_text())["geometry"]["config"]
            if c["turns"] >= 3 and c.get("metal", c.get("top_metal")) == metal:
                grid.append((c["outer_diameter_um"], c["turns"], c["width_um"], c["spacing_um"], point.name))
tally = collections.Counter()
rows = []
rendered = 0
hangs = collections.Counter()
for od, nt, w, s, origin in grid:
    cfg = {"outer_diameter_um": od, "width_um": w, "spacing_um": s, "opening_um": 8.0, "lead_length_um": 20.0, "turns": nt,
           "metal": metal, "ground_fixture": FIX, "process_profile": profile, "port_order": ["P1", "N1"]}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            r = g.generate(g.config_model.model_validate(cfg), outdir=Path(tmp), gds_name="x.gds")
        except Exception as exc:  # noqa: BLE001 -- a refusal is a row of the grid, recorded with its reason
            tally["refused"] += 1
            rows.append({"od": od, "nt": nt, "w": w, "s": s, "origin": origin, "status": "refused", "why": f"{type(exc).__name__}: {str(exc)[:100]}"})
            continue
        hang = pad_hang(r.gds_path, drawing, w)
        worst = max(hang) if hang else 0.0
        hangs["pads"] += len(hang)
        hangs["hanging>0"] += sum(1 for h in hang if h > 1e-9)
        hangs["hanging>=40%"] += sum(1 for h in hang if h >= 0.4)
        findings = [v for v in audit_gds(r.gds_path, profile).violations if (v.kind, v.layer) != ("max_width", "M1")]
        if not findings:
            tally["clean"] += 1
            rows.append({"od": od, "nt": nt, "w": w, "s": s, "origin": origin, "status": "clean", "pads": len(hang), "worst_hang": worst})
            continue
        kinds = sorted({(v.kind, v.layer) for v in findings})
        tally["violations"] += 1
        row = {"od": od, "nt": nt, "w": w, "s": s, "origin": origin, "status": "violations", "kinds": kinds, "pads": len(hang), "worst_hang": worst,
               "findings": [{"kind": v.kind, "layer": v.layer, "count": v.count, "boxes": [list(b) for b in v.boxes_um[:6]]} for v in findings]}
        rows.append(row)
        if png_dir is not None and rendered < 8:
            rendered += 1
            boxes = [b for v in findings for b in v.boxes_um]
            render(r.gds_path, png_dir / f"od{od}_nt{nt}_w{w}_s{s}.png", boxes=boxes, names=layer_names(profile),
                   title=f"ind_sym OD={od} NT={nt} W={w} S={s}: " + ", ".join(f"{k}:{l}" for k, l in kinds))
            b = boxes[0]
            render(r.gds_path, png_dir / f"od{od}_nt{nt}_w{w}_s{s}_zoom.png", boxes=boxes, zoom=(b[0] - 6, b[1] - 6, b[2] + 6, b[3] + 6), names=layer_names(profile),
                   title="zoom on the first finding")
kind_tally = collections.Counter(k for row in rows if row["status"] == "violations" for k in map(tuple, row["kinds"]))
print(dict(tally))
print("pads:", dict(hangs), "worst:", max((row.get("worst_hang", 0.0) for row in rows), default=0.0))
print({f"{k}:{l}": n for (k, l), n in kind_tally.items()})
by_kind_nt = collections.Counter((row["nt"], k) for row in rows if row["status"] == "violations" for k, _ in map(tuple, row["kinds"]))
print(sorted(by_kind_nt.items()))
out.write_text(json.dumps({"profile": profile, "metal": metal, "tally": dict(tally), "pads": dict(hangs),
                           "kinds": {f"{k}:{l}": n for (k, l), n in kind_tally.items()}, "rows": rows}, indent=1))
