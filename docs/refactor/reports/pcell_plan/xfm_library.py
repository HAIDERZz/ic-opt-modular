"""Build the N28 transformer library: the approved xfm_bs / xfm_ms grid through the em_only pipeline, full-wave, fine mesh.

Usage: xfm_library.py GRID_JSON LIBRARY_ROOT [--plan] [--only PROJECT ...]

One run store per project under LIBRARY_ROOT: xfm_bs_ap, xfm_bs_m10 (0 -> 200 GHz), xfm_ms_ap, xfm_ms_m10 (0 -> 150 GHz).
EMX: --full-wave, thickness 0.25 / edge 0.2 / splits 5, --3d = both windings (+ the ms crossunder metal),
simultaneous frequencies 0. Each project runs its memory classes in turn (xfm_memory.py writes mem_class into the
grid): the class sets the per-job EMX memory cap and how many jobs run at once, always within 128 threads / 256 GB
(user ceiling). Threads and memory are outside the EMX cache key, so the classes share one store.
Threads per job are 8, except the memory-limited ms classes B-E, which take more threads per job so the run keeps
using ~128 threads (user approval 2026-09-23); 16 at most, the thread count the memory model was fitted at.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from ic_opt.blocks.evaluate import evaluate
from ic_opt.em.pcell import GEOMETRY_VERSION
from ic_opt.eval.engine import workers_for
from ic_opt.executor.local import LocalExecutor
from ic_opt.site import Site
from ic_opt.space import Point
from ic_opt.stages.em_chain import em_only_pipeline
from ic_opt.store import RunStore

sys.path.insert(0, str(Path(__file__).parent))
from xfm_grid import PAIRS
from xfm_memory import CLASSES
from xfm_pilot import CSHRC, MESHES, point, spec_for

THREADS, TIMEOUT_S = 8, 7200
MS_THREADS = {"A": 8, "B": 10, "C": 12, "D": 16, "E": 16}
MAX_THREADS, MAX_MEMORY_GB = 128, 256.0


def threads_for(family: str, cls: str) -> int:
    return MS_THREADS[cls] if family == "ms" else THREADS


def values(family: str, p: dict) -> dict:
    v = {"primary_outer_diameter_um": p["od_p"], "secondary_outer_diameter_um": p["od_s"], "primary_width_um": p["w_p"],
         "secondary_width_um": p["w_s"], "center_spacing_um": round(p["offset"] * (p["od_p"] + p["od_s"]) / 4, 2)}
    if family == "ms":
        v |= {"secondary_turns": p["nt_s"], "secondary_spacing_um": p["s_s"]}
    return v


def projects(grid: dict):
    """(name, [(class, spec, points)]) per project, classes in order A, B, C."""
    out = []
    for family in [f for f in ("bs", "ms") if f in grid["families"]]:          # a densification grid carries one family
        for tag, pm, sm, three_d in PAIRS[family]:
            name = f"xfm_{family}_{tag}"
            pts = [p for p in grid["families"][family]["points"] if p["status"] == "ok" and p["pair"] == tag]
            runs = []
            for cls, (_, cap, jobs) in CLASSES.items():
                sub = [p for p in pts if p["mem_class"] == cls]
                if sub:
                    spec = spec_for(name, family, pm, sm, three_d, MESHES["fine"], threads_for(family, cls), cap, jobs, TIMEOUT_S)
                    runs.append((cls, spec, [Point(point(values(family, p)), "grid") for p in sub]))
            out.append((name, runs))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("grid", type=Path)
    ap.add_argument("root", type=Path)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()
    grid = json.loads(args.grid.read_text())
    todo = [p for p in projects(grid) if not args.only or p[0] in args.only]
    print(f"geometry generation {GEOMETRY_VERSION}; {sum(len(pts) for _, runs in todo for _, _, pts in runs)} points in {len(todo)} projects under {args.root}")
    for name, runs in todo:
        for cls, spec, pts in runs:
            cap, jobs, threads = spec.em.memory_gb, spec.simulator.parallel_jobs, spec.em.threads
            if jobs * threads > MAX_THREADS or jobs * cap > MAX_MEMORY_GB:
                raise SystemExit(f"{name}/{cls}: {jobs} jobs x {threads} threads / {cap:g} GB exceeds the ceiling")
            w = workers_for(spec, em_only_pipeline(spec), jobs, Site(max_threads=jobs * threads, max_memory_gb=jobs * cap))
            print(f"  {name:<11} class {cls} {len(pts):5d} points  full-wave 0-{spec.em.frequencies.stop_hz / 1e9:.0f} GHz  3d={spec.em.three_d_metals}  "
                  f"{w} jobs x {threads} threads = {w * threads} threads, EMX memory caps {w} x {cap:g} = {w * cap:.0f} GB")
    if args.plan:
        return
    args.root.mkdir(parents=True, exist_ok=True)
    shutil.copy(args.grid, args.root / args.grid.name)          # the production grid was xfm_grid.json; later batches keep their own names
    for name, runs in todo:
        store = RunStore(args.root / name)
        (store.root / "spec.json").write_text(runs[0][1].model_dump_json(indent=1))
        for cls, spec, pts in runs:
            cap, jobs, threads = spec.em.memory_gb, spec.simulator.parallel_jobs, spec.em.threads
            site = Site(max_threads=jobs * threads, max_memory_gb=jobs * cap)
            t0 = time.time()
            print(f"[{time.strftime('%m-%d %H:%M:%S')}] {name} class {cls}: {len(pts)} points, {jobs} jobs x {threads} threads x {cap:g} GB", flush=True)
            obs = evaluate(spec, pts, LocalExecutor(store.root / "sims"), store, pipeline=em_only_pipeline(spec),
                           cshrc=CSHRC, parallel_jobs=jobs, site=site)
            bad = [o for o in obs if o.status != "ok"]
            print(f"[{time.strftime('%m-%d %H:%M:%S')}] {name} class {cls}: done in {time.time() - t0:.0f} s, {len(obs) - len(bad)} ok, {len(bad)} not ok", flush=True)
            for o in bad:
                print(f"    {o.obs_id} {o.status} {o.issues[:2]}", flush=True)


if __name__ == "__main__":
    main()
