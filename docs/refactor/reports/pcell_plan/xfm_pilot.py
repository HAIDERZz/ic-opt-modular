"""Transformer library pilot: mesh convergence of L / k / Q / SRF and full-wave cost on four geometries, through the real em_only pipeline.

Usage: xfm_pilot.py OUT_DIR [--only ARM ...]
Strictly one EMX at a time (parallel_jobs=1), fine arms before extra-fine, so a surprise in cost
shows up before the expensive runs. Each arm is its own spec + run store under OUT_DIR/<arm>/.
spec_for() is shared with the production driver (xfm_library.py).
"""
# Adapted 2026-09-25 (T15.7) to the T15 API: limits from ~/.ic-opt/site.yaml hosts.local, threads_per_run stated.
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from ic_opt import site
from ic_opt.blocks.evaluate import evaluate
from ic_opt.executor.local import LocalExecutor
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.stages.em_chain import em_only_pipeline
from ic_opt.store import RunStore

# The site EMX process file is never committed: point IC_OPT_EMX_PROC at it.
PROC = os.environ.get("IC_OPT_EMX_PROC", "")
CSHRC = "/home/zzchen/Agent_virtuoso/cadence_ic231_env.csh"
FIXTURE = {"inner_margin_um": 15.0, "ring_width_um": 50.0, "stub_length_um": 2.0, "stub_chamfer_um": 0.0}
MESHES = {"fine": (0.25, 0.2, 5), "xfine": (0.15, 0.1, 6)}      # thickness, edge width, max splits
STOP_GHZ = {"bs": 200, "ms": 150}
VARIABLES = {"bs": ["primary_outer_diameter_um", "secondary_outer_diameter_um", "primary_width_um", "secondary_width_um", "center_spacing_um"],
             "ms": ["primary_outer_diameter_um", "secondary_outer_diameter_um", "primary_width_um", "secondary_width_um", "center_spacing_um",
                    "secondary_turns", "secondary_spacing_um"]}
BOUNDS = {"primary_outer_diameter_um": ("40", "260", "0.01"), "secondary_outer_diameter_um": ("40", "260", "0.01"),
          "primary_width_um": ("4", "10", "0.01"), "secondary_width_um": ("4", "10", "0.01"), "center_spacing_um": ("0", "130", "0.01"),
          "secondary_spacing_um": ("2", "4", "0.01")}


def spec_for(project: str, family: str, primary_metal: str, secondary_metal: str, three_d: list[str], mesh: tuple[float, float, int],
             threads: int, memory_gb: float, jobs: int, timeout_s: int = 7200) -> Spec:
    thickness, edge, splits = mesh
    variables = [{"name": "secondary_turns", "kind": "integer", "lower": "2", "upper": "5", "step": "1"} if v == "secondary_turns" else
                 {"name": v, "kind": "continuous_step", "lower": BOUNDS[v][0], "upper": BOUNDS[v][1], "step": BOUNDS[v][2]} for v in VARIABLES[family]]
    return Spec.model_validate({
        "project": project,
        "testbenches": [],
        "devices": [{
            "id": "xfm", "generator": f"clean_port_xfm_{family}", "plugin": "builtin:clean_port", "profile": "n28_1p10m",
            "ports": ["P1", "N1", "P2", "N2"],
            "fixed": {"primary_opening_um": 8.0, "secondary_opening_um": 8.0, "primary_lead_length_um": 20.0, "secondary_lead_length_um": 20.0,
                      "primary_metal": primary_metal, "secondary_metal": secondary_metal, "ground_fixture": dict(FIXTURE)},
            "variables": {v: v for v in VARIABLES[family]},
        }],
        "variables": variables,
        "em": {"process_file": PROC, "mode": "full_wave", "frequencies": {"start_hz": 0, "stop_hz": STOP_GHZ[family] * 1e9, "step_hz": 1e9},
               "accuracy": {"thickness_um": thickness, "edge_width_um": edge, "max_splits": splits},
               "three_d_metals": three_d, "via_separation_um": 0.5, "threads": threads, "memory_gb": memory_gb,
               "simultaneous_frequencies": 0, "timeout_s": timeout_s},
        "metrics": [{"name": "Lp_lf", "unit": "H", "device": "xfm", "quantity": "Lp_lf"},
                    {"name": "Ls_lf", "unit": "H", "device": "xfm", "quantity": "Ls_lf"},
                    {"name": "k_lf", "unit": "ratio", "device": "xfm", "quantity": "k_lf"},
                    {"name": "Qp_peak", "unit": "ratio", "device": "xfm", "quantity": "Qp_peak"},
                    {"name": "Qs_peak", "unit": "ratio", "device": "xfm", "quantity": "Qs_peak"}],
        "constraints": [],
        "objective": {"direction": "maximize", "expression": "k_lf"},
        "simulator": {"parallel_jobs": jobs, "threads_per_run": 10, "timeout_s": timeout_s},      # 10: 0.2.0's default, as these runs had it
        "budget": {"max_simulations": 10000},
    })


def point(values: dict) -> dict[str, str]:
    return {k: f"{v:g}" if isinstance(v, float) else str(v) for k, v in values.items()}


GEOMETRIES = {
    "bs_small": ("bs", {"primary_outer_diameter_um": 60.0, "secondary_outer_diameter_um": 60.0, "primary_width_um": 4.0, "secondary_width_um": 4.0,
                        "center_spacing_um": 0.0}),
    "bs_large": ("bs", {"primary_outer_diameter_um": 240.0, "secondary_outer_diameter_um": 240.0, "primary_width_um": 10.0, "secondary_width_um": 10.0,
                        "center_spacing_um": 0.0}),
    "ms_mid": ("ms", {"primary_outer_diameter_um": 150.0, "secondary_outer_diameter_um": 180.0, "primary_width_um": 7.0, "secondary_width_um": 7.0,
                      "center_spacing_um": 0.0, "secondary_turns": 3, "secondary_spacing_um": 2.0}),
    "ms_large": ("ms", {"primary_outer_diameter_um": 240.0, "secondary_outer_diameter_um": 240.0, "primary_width_um": 10.0, "secondary_width_um": 10.0,
                        "center_spacing_um": 0.0, "secondary_turns": 5, "secondary_spacing_um": 4.0}),
}
THREE_D = {"bs": ["AP", "M10"], "ms": ["AP", "M10", "M9"]}
ARMS = [(f"{fam}_{mesh}", fam, mesh) for mesh in ("fine", "xfine") for fam in ("bs", "ms")]
THREADS, MEMORY_GB = 16, 64.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()
    if not PROC:
        ap.error("set IC_OPT_EMX_PROC to the site EMX process file (it is never committed)")
    limits = site.load().host("local")                             # parallel_jobs=1 below keeps it strictly serial
    for arm, fam, mesh in ARMS:
        if args.only and arm not in args.only:
            continue
        geoms = [g for g, (f, _) in GEOMETRIES.items() if f == fam]
        spec = spec_for(f"xfm_pilot_{arm}", fam, "AP", "10", THREE_D[fam], MESHES[mesh], THREADS, MEMORY_GB, 1)
        store = RunStore(args.out / arm)
        t0 = time.time()
        print(f"[{time.strftime('%H:%M:%S')}] {arm}: mesh={MESHES[mesh]} 0-{STOP_GHZ[fam]} GHz 3d={THREE_D[fam]} x {len(geoms)} points", flush=True)
        obs = evaluate(spec, [Point(point(GEOMETRIES[g][1]), "user") for g in geoms], LocalExecutor(store.root / "sims"), store,
                       pipeline=em_only_pipeline(spec), cshrc=CSHRC, parallel_jobs=1, limits=limits)
        (store.root / "pilot_arm.json").write_text(json.dumps({"arm": arm, "family": fam, "mesh": mesh, "mesh_values": MESHES[mesh],
                                                                "geometries": geoms, "wall_s": time.time() - t0}, indent=1))
        for g, o in zip(geoms, obs):
            print(f"    {g:<9} {o.obs_id} {o.status} {o.metrics} {o.issues[:2]}", flush=True)
        print(f"    arm wall {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
