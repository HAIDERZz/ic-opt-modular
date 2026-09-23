"""Predict every transformer grid point's EMX peak memory and give it a concurrency class, so the library run respects 128 threads / 256 GB.

EMX's --max-memory is only a soft limit (EMX takes what it thinks it needs), so the per-job cap has to sit above
the real peak. The predictor is a power law in the drawn perimeter of the winding metals (every conductor but the M1
ground ring), fitted on the pilot's fine-mesh runs (16 threads -- an upper bound for the library's 8 threads,
since EMX sizes its memory to the thread count; the fit is within +-7% on all four). Each class's cap is 1.1 x its
upper prediction bound (covers the fit error) and its job count is the most that keeps jobs x cap <= 256 GB and
jobs x 8 threads <= 128.

Usage: xfm_memory.py GRID_JSON PILOT_DIR [--regenerate]
Adds perimeter_um / mem_pred_gb / mem_class to every ok point, in place; perimeters already present are reused
unless --regenerate.
"""
from __future__ import annotations

import collections
import json
import re
import sys
import tempfile
from pathlib import Path

import klayout.db as kdb
import numpy as np

from ic_opt.em.pcell import get_generator
from ic_opt.em.pcell.render import layer_names

sys.path.insert(0, str(Path(__file__).parent))
from xfm_grid import config

# class -> (upper bound of predicted peak GB, EMX memory cap GB per job, jobs); jobs x 8 threads <= 128, jobs x cap <= 256
CLASSES = {"A": (14.5, 16.0, 16), "B": (19.0, 21.0, 12), "C": (23.0, 25.5, 10), "D": (29.0, 32.0, 8), "E": (33.5, 36.5, 7)}


def winding_perimeter_um(gds: Path, profile: str = "n28_1p10m") -> float:
    names = layer_names(profile)
    ly = kdb.Layout()
    ly.read(str(gds))
    top = ly.top_cell()
    total = 0.0
    for li in ly.layer_indexes():
        info = ly.get_info(li)
        name = names.get(f"{info.layer}/{info.datatype}", "")
        if name in ("AP", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10"):
            region = kdb.Region(top.begin_shapes_rec(li))
            region.merge()
            total += region.perimeter() * ly.dbu
    return total


def fit(pilot: Path) -> tuple[float, float, list[tuple[str, float, float]]]:
    pts = []
    for arm in ("bs_fine", "ms_fine"):
        meta = json.loads((pilot / arm / ".icopt" / "pilot_arm.json").read_text())
        for i, geom in enumerate(meta["geometries"], 1):
            work = pilot / arm / ".icopt" / "sims" / f"obs_{i:04d}" / "em" / "xfm"
            peak = float(re.search(r"Peak memory usage ([0-9.]+) GB", (work / "emx.log").read_text(errors="replace")).group(1))
            pts.append((geom, winding_perimeter_um(work / "xfm.gds"), peak))
    slope, intercept = np.polyfit(np.log([p for _, p, _ in pts]), np.log([m for _, _, m in pts]), 1)
    return float(np.exp(intercept)), float(slope), pts


def main() -> None:
    grid_path, pilot, regenerate = Path(sys.argv[1]), Path(sys.argv[2]), "--regenerate" in sys.argv[3:]
    grid = json.loads(grid_path.read_text())
    a, b, pts = fit(pilot)
    print(f"peak GB = {a:.4g} * perimeter_um^{b:.3f}")
    for geom, perim, peak in pts:
        print(f"  {geom:<9} perimeter {perim:7.0f} um  peak {peak:5.2f} GB  model {a * perim ** b:5.2f} GB ({100 * (a * perim ** b / peak - 1):+.0f}%)")
    for family, fam in grid["families"].items():
        generator = get_generator(f"clean_port_xfm_{family}", plugin_module="builtin:clean_port")
        count = collections.Counter()
        for p in fam["points"]:
            if p["status"] != "ok":
                continue
            if regenerate or "perimeter_um" not in p:
                with tempfile.TemporaryDirectory() as tmp:
                    generator.generate(generator.config_model.model_validate(config(family, p, grid["profile"])), outdir=Path(tmp), gds_name="x.gds")
                    p["perimeter_um"] = round(winding_perimeter_um(Path(tmp) / "x.gds"), 1)
            p["mem_pred_gb"] = round(a * p["perimeter_um"] ** b, 2)
            p["mem_class"] = next((c for c, (upper, _, _) in CLASSES.items() if p["mem_pred_gb"] <= upper), None)
            if p["mem_class"] is None:
                raise SystemExit(f"xfm_{family} point predicted at {p['mem_pred_gb']} GB is above every class; add one")
            count[p["mem_class"]] += 1
        preds = sorted(p["mem_pred_gb"] for p in fam["points"] if p["status"] == "ok")
        print(f"xfm_{family}: predicted peak {preds[0]:.1f}..{preds[-1]:.1f} GB; classes {dict(sorted(count.items()))}")
    grid["memory_model"] = {"form": "peak_gb = a * winding_perimeter_um ** b", "a": a, "b": b, "pilot_points": pts,
                            "classes": {c: {"max_pred_gb": u, "cap_gb": cap, "jobs": j} for c, (u, cap, j) in CLASSES.items()}}
    grid_path.write_text(json.dumps(grid, indent=1))


if __name__ == "__main__":
    main()
