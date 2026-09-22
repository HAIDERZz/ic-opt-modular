"""Build the N28 inductor library: the approved ind_sym grid through the em_only pipeline, full-wave, fine mesh.

Usage: ind_library.py GRID_JSON LIBRARY_ROOT [--plan] [--only PROJECT ...]

One run store per project under LIBRARY_ROOT:
  ind_sym_ap, ind_sym_m10          NT >= 2, 0 -> 150 GHz
  ind_sym_ap_nt1, ind_sym_m10_nt1  NT = 1,  0 -> 250 GHz (their SRF lies mostly above 150 GHz; decision D2)
EMX: --full-wave, thickness 0.25 / edge 0.2 / splits 5 (pilot 2026-09-23), --3d = winding + crossunder metal,
8 threads and 32 GB per job, 8 jobs at a time (64 threads peak), simultaneous frequencies 0.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from ic_opt.blocks.evaluate import evaluate
from ic_opt.em.pcell import GEOMETRY_VERSION
from ic_opt.eval.engine import workers_for
from ic_opt.executor.local import LocalExecutor
from ic_opt.site import Site
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.stages.em_chain import em_only_pipeline
from ic_opt.store import RunStore

PROC = "/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/gdsgen_ref/n28_proc/tsmcN28_1p10m.proc"
CSHRC = "/home/zzchen/Agent_virtuoso/cadence_ic231_env.csh"
THREADS, MEMORY_GB, JOBS, TIMEOUT_S = 8, 32.0, 8, 7200
MESH = {"thickness_um": 0.25, "edge_width_um": 0.2, "max_splits": 5}
BODIES = {"AP": ("ap", ["AP", "M10"]), "10": ("m10", ["M10", "M9"])}


def spec_for(project: str, metal: str, three_d: list[str], stop_hz: float) -> Spec:
    return Spec.model_validate({
        "project": project,
        "testbenches": [],
        "devices": [{
            "id": "ind", "generator": "clean_port_ind_sym", "plugin": "builtin:clean_port", "profile": "n28_1p10m", "ports": ["P1", "N1"],
            "fixed": {"opening_um": 8.0, "lead_length_um": 20.0, "metal": metal,
                      "ground_fixture": {"inner_margin_um": 15.0, "ring_width_um": 50.0, "stub_length_um": 2.0, "stub_chamfer_um": 0.0}},
            "variables": {k: k for k in ("outer_diameter_um", "width_um", "spacing_um", "turns")},
        }],
        "variables": [
            {"name": "outer_diameter_um", "kind": "continuous_step", "lower": "60", "upper": "240", "step": "1"},
            {"name": "width_um", "kind": "continuous_step", "lower": "4", "upper": "10", "step": "0.1"},
            {"name": "spacing_um", "kind": "continuous_step", "lower": "2", "upper": "4", "step": "0.1"},
            {"name": "turns", "kind": "integer", "lower": "1", "upper": "5", "step": "1"},
        ],
        "em": {"process_file": PROC, "mode": "full_wave", "frequencies": {"start_hz": 0, "stop_hz": stop_hz, "step_hz": 1e9},
               "accuracy": MESH, "three_d_metals": three_d, "via_separation_um": 0.5, "threads": THREADS, "memory_gb": MEMORY_GB,
               "simultaneous_frequencies": 0, "timeout_s": TIMEOUT_S},
        "metrics": [{"name": "L_lf", "unit": "H", "device": "ind", "quantity": "Lp_lf"},
                    {"name": "L_res", "unit": "H", "device": "ind", "quantity": "Lp_res"},
                    {"name": "Q_peak", "unit": "ratio", "device": "ind", "quantity": "Qp_peak"}],
        "constraints": [],
        "objective": {"direction": "maximize", "expression": "Q_peak"},
        "simulator": {"parallel_jobs": JOBS, "timeout_s": TIMEOUT_S},
        "budget": {"max_simulations": 2000},
    })


def projects(grid: dict) -> list[tuple[str, Spec, list[Point]]]:
    out = []
    ok = [p for p in grid["points"] if p["status"] == "ok"]
    for metal, (tag, three_d) in BODIES.items():
        for nt1, suffix, stop in ((False, "", 150e9), (True, "_nt1", 250e9)):
            pts = [p for p in ok if p["metal"] == metal and (p["turns"] == 1) == nt1]
            name = f"ind_sym_{tag}{suffix}"
            points = [Point({"outer_diameter_um": f"{p['outer_diameter_um']:g}", "width_um": f"{p['width_um']:g}",
                             "spacing_um": f"{p['spacing_um']:g}", "turns": str(p["turns"])}, "grid") for p in pts]
            out.append((name, spec_for(name, metal, three_d, stop), points))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("grid", type=Path)
    ap.add_argument("root", type=Path)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()
    grid = json.loads(args.grid.read_text())
    site = Site(max_threads=JOBS * THREADS, max_memory_gb=JOBS * MEMORY_GB)
    todo = [p for p in projects(grid) if not args.only or p[0] in args.only]
    total = sum(len(pts) for _, _, pts in todo)
    print(f"geometry generation {GEOMETRY_VERSION}; {total} points in {len(todo)} projects under {args.root}")
    for name, spec, pts in todo:
        w = workers_for(spec, em_only_pipeline(spec), JOBS, site)
        print(f"  {name:<18} {len(pts):4d} points  full-wave 0-{spec.em.frequencies.stop_hz / 1e9:.0f} GHz step 1 GHz  3d={spec.em.three_d_metals}  "
              f"{w} jobs x {THREADS} threads = {w * THREADS} threads peak, EMX memory caps {w * MEMORY_GB:.0f} GB")
    if args.plan:
        return
    args.root.mkdir(parents=True, exist_ok=True)
    shutil.copy(args.grid, args.root / "grid.json")
    for name, spec, pts in todo:
        store = RunStore(args.root / name)
        (store.root / "spec.json").write_text(spec.model_dump_json(indent=1))
        t0 = time.time()
        print(f"[{time.strftime('%m-%d %H:%M:%S')}] {name}: {len(pts)} points", flush=True)
        obs = evaluate(spec, pts, LocalExecutor(store.root / "sims"), store, pipeline=em_only_pipeline(spec),
                       cshrc=CSHRC, parallel_jobs=JOBS, site=site)
        bad = [o for o in obs if o.status != "ok"]
        print(f"[{time.strftime('%m-%d %H:%M:%S')}] {name}: done in {time.time() - t0:.0f} s, {len(obs) - len(bad)} ok, {len(bad)} not ok", flush=True)
        for o in bad:
            print(f"    {o.obs_id} {o.status} {o.issues[:2]}", flush=True)


if __name__ == "__main__":
    main()
