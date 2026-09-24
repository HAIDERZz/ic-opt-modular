"""Cut a densification grid down to one option and price it (points, classes, hours), writing a grid xfm_library.py runs.

Usage: xfm_densify_select.py FULL_GRID OUT_GRID OPTION   OPTION: sixty | sixty+cells | all
  sixty        every point the SRF model predicts >= 75 GHz (usable at the 60 GHz anchor)
  sixty+cells  ... plus, in the 35-75 GHz band, the points that complete the production grid's own (OD_P, OD_S) cells
               to the full width / spacing factorial (no intermediate outer diameters)
  all          every point predicted >= 35 GHz
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from xfm_library_check import MS_THREADS, PARALLEL_EFFICIENCY, wall_model
from xfm_memory import CLASSES

PRODUCTION_OD = {60.0, 80.0, 100.0, 120.0, 150.0, 180.0, 210.0, 240.0}
WALL_RATIO = 1.75                  # measured / pilot model over the production run


def selected(p: dict, option: str) -> bool:
    if p["status"] != "ok":
        return False
    if option == "all":
        return True
    if p["srf_pred_ghz"] >= 75:
        return True
    return option == "sixty+cells" and p["od_p"] in PRODUCTION_OD and p["od_s"] in PRODUCTION_OD


def main() -> None:
    full, out, option = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    grid = json.loads(full.read_text())
    points = grid["families"]["ms"]["points"]
    hours, count, by_class = collections.defaultdict(float), collections.Counter(), collections.Counter()
    for p in points:
        if selected(p, option):
            count[p["pair"]] += 1
            by_class[p["mem_class"]] += 1
            threads = MS_THREADS[p["mem_class"]]
            speedup = threads / 8 * (PARALLEL_EFFICIENCY if threads > 8 else 1.0)
            hours[p["mem_class"]] += WALL_RATIO * wall_model("ms", p["perimeter_um"]) / speedup / CLASSES[p["mem_class"]][2] / 3600
        elif p["status"] == "ok":
            p["status"] = "skipped:not_selected"
    grid["option"] = option
    out.write_text(json.dumps(grid, indent=1, default=float))
    total = sum(count.values())
    print(f"{option}: {total} points ({dict(count)}), classes {dict(sorted(by_class.items()))}, about {sum(hours.values()):.1f} h "
          f"({', '.join(f'{c} {h:.1f} h' for c, h in sorted(hours.items()))}) -> {out}")


if __name__ == "__main__":
    main()
