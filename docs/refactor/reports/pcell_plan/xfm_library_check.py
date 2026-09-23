"""Check the transformer library as it fills: every finished point's L / k / Q / SRF, cost, and EMX peak memory against
the class prediction (the 128-thread / 256 GB ceiling rests on it), plus k against the old-library estimate.

Usage: xfm_library_check.py LIBRARY_ROOT [--json OUT]
The ETA scales the pilot's wall model (16 threads, one job) by the median measured/model ratio of the finished points
and spreads each remaining class over its job count.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

PROJECTS = ("xfm_bs_ap", "xfm_bs_m10", "xfm_ms_ap", "xfm_ms_m10")
WALL_A, WALL_B = 0.0006973, 0.846      # pilot: wall per frequency point at 16 threads, one job = A * perimeter^B
STOP_GHZ = {"bs": 200, "ms": 150}
MS_THREADS = {"A": 8, "B": 10, "C": 12, "D": 16, "E": 16}     # xfm_library.py: threads per job of the ms classes (bs: 8)
PARALLEL_EFFICIENCY = 0.9                                      # assumed when a job runs more than 8 threads


def wall_model(family: str, perimeter_um: float) -> float:
    return WALL_A * perimeter_um ** WALL_B * (STOP_GHZ[family] + 1)


def key(family: str, pair: str, od_p: float, od_s: float, w_p: float, w_s: float, cs: float, nt: int, s: float) -> tuple:
    return (family, pair, round(od_p, 2), round(od_s, 2), round(w_p, 2), round(w_s, 2), round(cs, 2), nt, round(s, 2))


def grid_index(root: Path) -> dict[tuple, dict]:
    grid = json.loads((root / "xfm_grid.json").read_text())
    out = {}
    for family, fam in grid["families"].items():
        for p in fam["points"]:
            if p["status"] == "ok":
                cs = round(p["offset"] * (p["od_p"] + p["od_s"]) / 4, 2)
                out[key(family, p["pair"], p["od_p"], p["od_s"], p["w_p"], p["w_s"], cs, p["nt_s"], p["s_s"])] = {**p, "family": family}
    return out


def eta_hours(root: Path, rs: list[dict]) -> tuple[float, float, dict]:
    """(median measured/model wall ratio, remaining hours, remaining points per class)."""
    grid = json.loads((root / "xfm_grid.json").read_text())
    ratios = sorted(r["wall_s"] / wall_model(r["family"], r["perimeter_um"]) for r in rs if r["wall_s"] and r.get("perimeter_um"))
    if not ratios:
        return 0.0, 0.0, {}
    ratio = ratios[len(ratios) // 2]          # measured at 8 threads per job
    done = {r["grid_key"] for r in rs if r["status"] == "ok"}
    left, count = collections.defaultdict(float), collections.Counter()
    for k, p in grid_index(root).items():
        if k not in done:
            threads = MS_THREADS[p["mem_class"]] if p["family"] == "ms" else 8
            speedup = threads / 8 * (PARALLEL_EFFICIENCY if threads > 8 else 1.0)
            left[p["mem_class"]] += ratio * wall_model(p["family"], p["perimeter_um"]) / speedup
            count[p["mem_class"]] += 1
    jobs = {c: v["jobs"] for c, v in grid["memory_model"]["classes"].items()}
    return ratio, sum(t / jobs[c] for c, t in left.items()) / 3600, dict(sorted(count.items()))


def rows(root: Path) -> list[dict]:
    index = grid_index(root)
    out = []
    for project in PROJECTS:
        store = root / project / ".icopt"
        if not (store / "observations.jsonl").exists():
            continue
        family, pair = project.split("_")[1], project.split("_")[2]
        for line in (store / "observations.jsonl").read_text().splitlines():
            o = json.loads(line)
            p = {k: float(v) for k, v in o["params"].items()}
            gk = key(family, pair, p["primary_outer_diameter_um"], p["secondary_outer_diameter_um"], p["primary_width_um"],
                     p["secondary_width_um"], p["center_spacing_um"], int(p.get("secondary_turns", 1)), p.get("secondary_spacing_um", 0.0))
            g = index.get(gk, {})
            work = store / "sims" / o["obs_id"]
            qf = work / "xfm" / "nominal" / "quantities.json"
            q = json.loads(qf.read_text()) if qf.exists() else {}
            log = (work / "em" / "xfm" / "emx.log").read_text(errors="replace") if (work / "em" / "xfm" / "emx.log").exists() else ""
            grab = lambda pat, text=log: (float(m.group(1)) if (m := re.search(pat, text)) else None)
            nano = lambda v: v * 1e9 if v else None
            giga = lambda v: v / 1e9 if v else None
            out.append({"project": project, "family": family, "obs": o["obs_id"], "status": o["status"], "issues": o["issues"], "params": p,
                        "grid_key": gk, "perimeter_um": g.get("perimeter_um"), "mem_class": g.get("mem_class"), "mem_pred_gb": g.get("mem_pred_gb"), "k_est": g.get("k_est"),
                        "Lp_nH": nano(q.get("Lp_lf")), "Ls_nH": nano(q.get("Ls_lf")), "k": q.get("k_lf"),
                        "Qp": q.get("Qp_peak"), "Qs": q.get("Qs_peak"), "SRFp_GHz": giga(q.get("SRF_p")), "SRFs_GHz": giga(q.get("SRF_s")),
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
        srf = sum(1 for r in ok if r["SRFp_GHz"])
        ks = sorted(r["k"] for r in ok if r["k"] is not None)
        print(f"{project:<11} done {len(v):5d} ok {len(ok):5d} | wall s med {walls[len(walls) // 2] if walls else 0:6.1f} max {walls[-1] if walls else 0:6.1f} | "
              f"SRF_p in band {srf}/{len(ok)} | k {ks[0] if ks else 0:.3f}..{ks[-1] if ks else 0:.3f} | "
              f"Lp {min((r['Lp_nH'] for r in ok), default=0):.3f}..{max((r['Lp_nH'] for r in ok), default=0):.2f} "
              f"Ls {min((r['Ls_nH'] for r in ok), default=0):.3f}..{max((r['Ls_nH'] for r in ok), default=0):.2f} nH")
    by = collections.defaultdict(list)
    for r in rs:
        if r["peak_gb"] and r["mem_pred_gb"]:
            by[r["mem_class"]].append(r["peak_gb"] / r["mem_pred_gb"])
    for cls in sorted(by):
        v = sorted(by[cls])
        top = max((r for r in rs if r["mem_class"] == cls and r["peak_gb"]), key=lambda r: r["peak_gb"])
        print(f"  memory class {cls}: {len(v)} runs, measured/predicted med {v[len(v) // 2]:.2f} max {v[-1]:.2f}; highest peak {top['peak_gb']:.1f} GB ({top['project']} {top['obs']})")
    for r in [r for r in rs if r["status"] != "ok"][:10]:
        print("  NOT OK", r["project"], r["obs"], r["status"], r["issues"][:2])
    dk = sorted(abs(r["k"] - r["k_est"]) for r in rs if r["k"] is not None and r["k_est"] is not None)
    if dk:
        print(f"k vs old-library estimate: |dk| median {dk[len(dk) // 2]:.3f} p90 {dk[int(0.9 * len(dk))]:.3f} max {dk[-1]:.3f}")
    ratio, hours, left = eta_hours(args.root, rs)
    if ratio:
        print(f"wall vs pilot model: measured/model median {ratio:.2f}; remaining {sum(left.values())} points {left} -> about {hours:.1f} h")
    if args.json:
        args.json.write_text(json.dumps([{k: v for k, v in r.items() if k != "grid_key"} for r in rs], indent=1))


if __name__ == "__main__":
    main()
