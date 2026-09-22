"""Inductor library pilot: mesh convergence and full-wave cost on three geometries, through the real em_only pipeline.

Usage: ind_pilot.py OUT_DIR [--only NAME ...]
Runs strictly one EMX at a time (site envelope = one job's threads), cheapest
arms first, so a surprise in full-wave cost shows up before the expensive runs.
Each arm is its own spec + run store under OUT_DIR/<arm>/; ind_pilot_report.py reads them.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ic_opt.blocks.evaluate import evaluate
from ic_opt.executor.local import LocalExecutor
from ic_opt.site import Site
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.stages.em_chain import em_only_pipeline
from ic_opt.store import RunStore

PROC = "/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/gdsgen_ref/n28_proc/tsmcN28_1p10m.proc"
CSHRC = "/home/zzchen/Agent_virtuoso/cadence_ic231_env.csh"
THREADS, MEMORY_GB, TIMEOUT_S = 8, 48.0, 7200

GEOMETRIES = {
    "small": {"outer_diameter_um": "60", "width_um": "4", "spacing_um": "2", "turns": "1"},
    "mid": {"outer_diameter_um": "120", "width_um": "6", "spacing_um": "2", "turns": "3"},
    "large": {"outer_diameter_um": "240", "width_um": "10", "spacing_um": "4", "turns": "5"},
}
MESHES = {"coarse": (0.5, 0.5, 3), "medium": (0.3, 0.3, 4), "fine": (0.25, 0.2, 5), "xfine": (0.15, 0.1, 6)}      # thickness, edge width, max splits

# cheapest first: every quasistatic arm, then full-wave coarse -> fine, then the ground-ring 3D check on the large coil
BASE = ("coarse", "medium", "fine")
ARMS = [(f"qs_{m}", "quasistatic", m, ["AP", "M10"], list(GEOMETRIES)) for m in BASE] + \
       [(f"fw_{m}", "full_wave", m, ["AP", "M10"], list(GEOMETRIES)) for m in BASE] + \
       [("fw_medium_m1_3d", "full_wave", "medium", ["AP", "M10", "M1"], ["large"])] + \
       [("fw_xfine", "full_wave", "xfine", ["AP", "M10"], ["small", "mid"])]      # convergence check where Q moved (small, mid)


def spec_for(mode: str, mesh: str, three_d: list[str]) -> Spec:
    thickness, edge, splits = MESHES[mesh]
    return Spec.model_validate({
        "project": f"ind_pilot_{mode}_{mesh}",
        "testbenches": [],
        "devices": [{
            "id": "ind", "generator": "clean_port_ind_sym", "plugin": "builtin:clean_port", "profile": "n28_1p10m", "ports": ["P1", "N1"],
            "fixed": {"opening_um": 8.0, "lead_length_um": 20.0, "metal": "AP",
                      "ground_fixture": {"inner_margin_um": 15.0, "ring_width_um": 50.0, "stub_length_um": 2.0, "stub_chamfer_um": 0.0}},
            "variables": {k: k for k in ("outer_diameter_um", "width_um", "spacing_um", "turns")},
        }],
        "variables": [
            {"name": "outer_diameter_um", "kind": "continuous_step", "lower": "60", "upper": "240", "step": "1"},
            {"name": "width_um", "kind": "continuous_step", "lower": "4", "upper": "10", "step": "0.1"},
            {"name": "spacing_um", "kind": "continuous_step", "lower": "2", "upper": "4", "step": "0.1"},
            {"name": "turns", "kind": "integer", "lower": "1", "upper": "5", "step": "1"},
        ],
        "em": {"process_file": PROC, "mode": mode, "frequencies": {"start_hz": 0, "stop_hz": 150e9, "step_hz": 1e9},
               "accuracy": {"thickness_um": thickness, "edge_width_um": edge, "max_splits": splits},
               "three_d_metals": three_d, "via_separation_um": 0.5, "threads": THREADS, "memory_gb": MEMORY_GB,
               "simultaneous_frequencies": 0, "timeout_s": TIMEOUT_S},
        "metrics": [{"name": "L_lf", "unit": "H", "device": "ind", "quantity": "Lp_lf"},
                    {"name": "Q_peak", "unit": "ratio", "device": "ind", "quantity": "Qp_peak"}],
        "constraints": [],
        "objective": {"direction": "maximize", "expression": "Q_peak"},
        "simulator": {"parallel_jobs": 1, "timeout_s": TIMEOUT_S},
        "budget": {"max_simulations": 100},
    })


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()
    site = Site(max_threads=THREADS, max_memory_gb=MEMORY_GB)      # exactly one EMX job fits: strictly serial
    for arm, mode, mesh, three_d, geoms in ARMS:
        if args.only and arm not in args.only:
            continue
        store = RunStore(args.out / arm)
        spec = spec_for(mode, mesh, three_d)
        points = [Point(GEOMETRIES[g], "user") for g in geoms]
        t0 = time.time()
        print(f"[{time.strftime('%H:%M:%S')}] {arm}: {mode} mesh={mesh} {MESHES[mesh]} 3d={three_d} x {len(points)} points", flush=True)
        obs = evaluate(spec, points, LocalExecutor(store.root / "sims"), store, pipeline=em_only_pipeline(spec),
                       cshrc=CSHRC, parallel_jobs=1, site=site)
        (store.root / "pilot_arm.json").write_text(json.dumps({"arm": arm, "mode": mode, "mesh": mesh, "mesh_values": MESHES[mesh],
                                                                "three_d": three_d, "geometries": geoms, "wall_s": time.time() - t0}, indent=1))
        for g, o in zip(geoms, obs):
            print(f"    {g:<6} {o.obs_id} {o.status} {o.metrics}", flush=True)
        print(f"    arm wall {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
