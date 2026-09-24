"""T14.4 acceptance: ``lib.region`` on the real N28 library against the 40 GHz prototype answer and the time budget.

For every stratum in the prototype output (xfm_bs_region_40g.json) the formal region query is run twice with the
prototype's targets and grid steps: cold (fitted models not cached yet; calibrations are) and warm (everything cached).
The robust / mean counts and per-dim ranges, and the measured hits, are compared with the prototype; the phase timings
are recorded against the plan's budget (P1 warm <= 3 min, P2 cold <= 6 min).

Usage: region_acceptance.py LIBRARY_ROOT PROTOTYPE_JSON OUT_JSON [--threads 8] [--workers 6]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

DIM = {"OD_P": "primary_outer_diameter_um", "OD_S": "secondary_outer_diameter_um", "W_P": "primary_width_um",
       "W_S": "secondary_width_um", "CS": "center_spacing_um"}


def targets_of(proto: dict, f0: str) -> dict:
    c = proto["constraints"]
    return {f"Lp@{f0}": {"min": c["L_pH"][0] * 1e-12, "max": c["L_pH"][1] * 1e-12},
            f"Ls@{f0}": {"min": c["L_pH"][0] * 1e-12, "max": c["L_pH"][1] * 1e-12},
            f"Qp@{f0}": {"min": c["Q_min"]}, f"Qs@{f0}": {"min": c["Q_min"]},
            f"k@{f0}": {"min": c["k"][0], "max": c["k"][1]}}


def compare(proto: dict, got: dict) -> dict:
    """Prototype (feasible_mean / feasible_robust with short dim names) against the region answer (levels.*)."""
    out = {}
    for level, key in (("robust", "feasible_robust"), ("mean", "feasible_mean")):
        p, g = proto[key], got["levels"][level]
        entry = {"prototype": p["count"], "region": g["count"], "same_count": p["count"] == g["count"]}
        if p["count"] and g["count"]:
            entry["ranges"] = {short: {"prototype": p["ranges"][short], "region": g["ranges"][DIM[short]],
                                       "same": [round(a, 6) for a in p["ranges"][short]] == [round(b, 6) for b in g["ranges"][DIM[short]]]}
                               for short in DIM}
        out[level] = entry
    out["measured"] = {"prototype": len(proto["measured_hits"]), "region": len(got["measured"]),
                       "same_ids": sorted(h["obs_id"] for h in proto["measured_hits"]) == sorted(m["obs_id"] for m in got["measured"])}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("prototype", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    from ic_opt.library import query, region

    proto = json.loads(args.prototype.read_text(encoding="utf-8"))
    f0 = f"{proto['f0_ghz']:g}"
    steps = {DIM["OD_P"]: proto["grid_steps"]["OD"], DIM["OD_S"]: proto["grid_steps"]["OD"], DIM["W_P"]: proto["grid_steps"]["W"],
             DIM["W_S"]: proto["grid_steps"]["W"], DIM["CS"]: proto["grid_steps"]["CS"]}
    result = {"f0_ghz": proto["f0_ghz"], "targets": targets_of(proto, f0), "steps": steps, "strata": {}}
    for st, p in proto["strata"].items():
        runs = {}
        for label in ("cold", "warm"):
            lib = query.Library(args.root)                           # a fresh instance each time: the disk caches are what warm means
            t0 = time.time()
            got = region.region(lib, st, result["targets"], steps=steps, group_by=[DIM["W_P"], DIM["W_S"]], trend=(f"k@{f0}", DIM["CS"]),
                                threads=args.threads, workers=args.workers)
            wall = time.time() - t0
            runs[label] = {"wall_s": round(wall, 1), "seconds": got.get("seconds"), "grid": got.get("grid"),
                           "comparison": compare(p, got), "notes": got.get("notes")}
            print(f"{st} {label}: {wall:.0f} s wall, phases {got.get('seconds')}; robust {got['levels']['robust']['count']} vs prototype "
                  f"{p['feasible_robust']['count']}, mean {got['levels']['mean']['count']} vs {p['feasible_mean']['count']}, "
                  f"measured {len(got['measured'])} vs {len(p['measured_hits'])}", flush=True)
            if label == "warm":
                runs["answer"] = got
        runs["budget"] = {"P1_warm_le_180s": runs["warm"]["wall_s"] <= 180, "P2_cold_le_360s": runs["cold"]["wall_s"] <= 360}
        result["strata"][st] = runs
    args.out.write_text(json.dumps(result, indent=1, default=float), encoding="utf-8")
    print("wrote", args.out)


if __name__ == "__main__":
    main()
