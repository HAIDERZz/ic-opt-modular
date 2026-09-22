"""Check the inductor library as it fills: every finished point's L / Q / SRF, cost, and outliers against the grid estimate.

Usage: ind_library_check.py LIBRARY_ROOT [--json OUT]
Prints per-project counts, cost statistics and the points whose EMX inductance departs from the
calibrated current-sheet estimate by more than 15% (a geometry or port problem shows up there first).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PROJECTS = ("ind_sym_ap", "ind_sym_ap_nt1", "ind_sym_m10", "ind_sym_m10_nt1")


def rows(root: Path) -> list[dict]:
    grid = json.loads((root / "grid.json").read_text())
    est = {(p["metal"], p["turns"], p["outer_diameter_um"], p["width_um"], p["spacing_um"]): p["L_est_nH"] for p in grid["points"] if p["status"] == "ok"}
    out = []
    for project in PROJECTS:
        store = root / project / ".icopt"
        if not (store / "observations.jsonl").exists():
            continue
        metal = "AP" if "_ap" in project else "10"
        for line in (store / "observations.jsonl").read_text().splitlines():
            o = json.loads(line)
            p = o["params"]
            key = (metal, int(p["turns"]), float(p["outer_diameter_um"]), float(p["width_um"]), float(p["spacing_um"]))
            work = store / "sims" / o["obs_id"]
            q = json.loads((work / "ind" / "nominal" / "quantities.json").read_text()) if (work / "ind" / "nominal" / "quantities.json").exists() else {}
            log = (work / "em" / "ind" / "emx.log").read_text(errors="replace") if (work / "em" / "ind" / "emx.log").exists() else ""
            grab = lambda pat, text=log: (float(m.group(1)) if (m := re.search(pat, text)) else None)
            l_nh = q["Lp_lf"] * 1e9 if q.get("Lp_lf") else None
            out.append({"project": project, "obs": o["obs_id"], "status": o["status"], "issues": o["issues"], **{k: key[i] for i, k in enumerate(("metal", "turns", "od", "w", "s"))},
                        "L_nH": l_nh, "L_est_nH": est.get(key), "Q_peak": q.get("Qp_peak"), "SRF_GHz": q["SRF_p"] / 1e9 if q.get("SRF_p") else None,
                        "wall_s": grab(r"Wall-clock time ([0-9.]+) sec"), "peak_gb": grab(r"Peak memory usage ([0-9.]+) GB")})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()
    rs = rows(args.root)
    for project in PROJECTS:
        v = [r for r in rs if r["project"] == project]
        if not v:
            continue
        ok = [r for r in v if r["status"] == "ok"]
        walls = sorted(r["wall_s"] for r in ok if r["wall_s"])
        mems = sorted(r["peak_gb"] for r in ok if r["peak_gb"])
        srf = sum(1 for r in ok if r["SRF_GHz"])
        print(f"{project:<18} done {len(v):4d} ok {len(ok):4d} | wall s med {walls[len(walls) // 2] if walls else 0:6.1f} max {walls[-1] if walls else 0:6.1f} | "
              f"peak GB med {mems[len(mems) // 2] if mems else 0:5.2f} max {mems[-1] if mems else 0:5.2f} | SRF in band {srf}/{len(ok)} | "
              f"L {min((r['L_nH'] for r in ok), default=0):.3f}..{max((r['L_nH'] for r in ok), default=0):.2f} nH")
    bad = [r for r in rs if r["status"] != "ok"]
    for r in bad:
        print("  NOT OK", r["project"], r["obs"], r["status"], r["issues"][:2])
    off = [r for r in rs if r["L_nH"] and r["L_est_nH"] and abs(r["L_nH"] / r["L_est_nH"] - 1) > 0.15]
    errs = sorted(abs(r["L_nH"] / r["L_est_nH"] - 1) for r in rs if r["L_nH"] and r["L_est_nH"])
    if errs:
        print(f"L vs estimate: |err| median {100 * errs[len(errs) // 2]:.1f}% p90 {100 * errs[int(0.9 * len(errs))]:.1f}% max {100 * errs[-1]:.1f}%; >15%: {len(off)}")
    for r in off[:10]:
        print(f"  L off  {r['project']} {r['obs']} NT={r['turns']} OD={r['od']:g} W={r['w']:g} S={r['s']:g}: EMX {r['L_nH']:.3f} vs est {r['L_est_nH']:.3f}")
    if args.json:
        args.json.write_text(json.dumps(rs, indent=1))


if __name__ == "__main__":
    main()
