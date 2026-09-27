"""The survey behind the AnalogGym benchmark problems, and what is derived from it (plan section 4.3, T17.0a).

    python benchmarks/tools/survey_analoggym.py split                     # benchmarks/split.json, before any result
    python benchmarks/tools/survey_analoggym.py survey --jobs 32 --out DIR
    python benchmarks/tools/survey_analoggym.py calibrate --survey DIR    # benchmarks/analoggym_problems.json

``survey`` simulates every circuit's survey points (``icopt_bench.calibrate.survey_points``) and appends one row per
point to ``DIR/<circuit>.jsonl``; started again, it simulates only the points that are not there yet. ``calibrate``
reads those files and writes thresholds, reference designs and fine-tuning ranges. Neither looks at any method's
result. For a held-out circuit ``calibrate`` prints only whether it became a problem.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from icopt_bench import analoggym, calibrate

from ic_opt.space import point_key

BENCHMARKS = Path(__file__).resolve().parent.parent
SPLIT_PATH = BENCHMARKS / "split.json"
PROBLEMS_PATH = BENCHMARKS / "analoggym_problems.json"
SPLIT_SEED = 20260928


def _simulate(circuit_name: str, params: dict[str, str]) -> dict:
    circuit = analoggym.circuits()[circuit_name]
    started = time.perf_counter()
    children = analoggym.simulate(circuit, params)
    metrics: dict[str, float] = {}
    for result in children.values():
        metrics.update(result.metrics)
    return {"params": params, "metrics": metrics, "status": {unit: c.status for unit, c in children.items()},
            "seconds": round(time.perf_counter() - started, 3)}


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def survey(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    registry = analoggym.circuits()
    names = args.circuits.split(",") if args.circuits else sorted(registry)
    todo: list[tuple[str, dict[str, str]]] = []
    for name in names:
        have = {point_key(r["params"]) for r in _rows(out / f"{name}.jsonl")}
        points = calibrate.survey_points(name, registry[name].variables, args.points)
        todo += [(name, p) for p in points if point_key(p) not in have]
    print(f"survey: {len(names)} circuits, {len(todo)} points to simulate, {args.jobs} at a time", flush=True)
    done, started = 0, time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(_simulate, name, point): name for name, point in todo}
        for future in as_completed(futures):
            name = futures[future]
            with (out / f"{name}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(future.result(), separators=(",", ":")) + "\n")
            done += 1
            if done % 500 == 0 or done == len(todo):
                print(f"survey: {done}/{len(todo)} after {time.perf_counter() - started:.0f} s", flush=True)
    (out / "DONE").write_text(f"{len(todo)} points simulated in this run\n", encoding="utf-8")
    return 0


def _circuit_problems(name: str) -> list[str]:
    return [f"ag_{name}_wide", f"ag_{name}_fine", f"ag_{name}_native"]


def split(args: argparse.Namespace) -> int:
    registry = analoggym.circuits()
    development, held = calibrate.split([(c.name, c.kind, len(c.variables)) for c in registry.values()], SPLIT_SEED)
    doc = {
        "seed": SPLIT_SEED,
        "rule": "within each kind, circuits in order of their number of variables are cut into groups of five; two of "
                "each five are held out (icopt_bench.calibrate.split)",
        "circuits": {"development": development, "heldout": held},
        "development": sorted(p for c in development for p in _circuit_problems(c)),
        "heldout": sorted(p for c in held for p in _circuit_problems(c)),
    }
    if SPLIT_PATH.exists() and not args.force:
        print(f"{SPLIT_PATH} exists; the split is made once (--force to write it again)", file=sys.stderr)
        return 1
    SPLIT_PATH.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"development ({len(development)}): {', '.join(development)}")
    print(f"held out ({len(held)}): {', '.join(held)}")
    return 0


def calibrate_all(args: argparse.Namespace) -> int:
    registry = analoggym.circuits()
    held = set(json.loads(SPLIT_PATH.read_text(encoding="utf-8"))["circuits"]["heldout"])
    problems: dict[str, dict] = {}
    excluded: dict[str, str] = {}
    for name in sorted(registry):
        circuit = registry[name]
        rows = _rows(Path(args.survey) / f"{name}.jsonl")
        metric_names = [m["name"] for m in analoggym.metric_defs(circuit)]
        try:
            found = calibrate.calibrate(rows, circuit.variables, metric_names, circuit.targets,
                                        analoggym.native_objective(circuit))
        except calibrate.NotCalibrated as exc:
            excluded[name] = str(exc)
            print(f"{name}: excluded" + ("" if name in held else f" -- {exc}"))
            continue
        problems[name] = {
            "survey": {"points": found.total, "working": found.working, "feasible": found.feasible,
                       "quantile": found.quantile},
            "constraints": list(found.constraints),
            "reference": found.reference,
            "reference_objective": found.reference_objective,
            "fine_variables": list(found.fine_variables),
            "fine_ranges": {k: list(v) for k, v in found.fine_ranges.items()},
        }
        if name in held:
            print(f"{name}: a problem (held out; nothing else shown)")
        else:
            thresholds = ", ".join(f"{c['metric']} {c['op']} {c['value']}" for c in found.constraints)
            print(f"{name}: working {found.working}/{found.total}, feasible {found.feasible}, q={found.quantile:.3f}, "
                  f"reference objective {found.reference_objective:.4g}; {thresholds}; fine: "
                  f"{', '.join(found.fine_variables)}")
    doc = {"recipe": "docs/refactor/T17_OPTIMIZER_PLAN_CN.md section 4.3", "feasible_share": calibrate.FEASIBLE_SHARE,
           "problems": problems, "excluded": excluded}
    PROBLEMS_PATH.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(problems)} circuits are problems, {len(excluded)} excluded -> {PROBLEMS_PATH}")
    return 0


def main(argv: list[str] | None = None) -> int:
    os.environ.setdefault("OMP_NUM_THREADS", "1")            # one ngspice, one thread: --jobs is the whole load
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("survey")
    p.add_argument("--out", required=True)
    p.add_argument("--jobs", type=int, required=True)
    p.add_argument("--circuits", default="")
    p.add_argument("--points", type=int, default=calibrate.SURVEY_POINTS)
    p.set_defaults(run=survey)
    p = sub.add_parser("split")
    p.add_argument("--force", action="store_true")
    p.set_defaults(run=split)
    p = sub.add_parser("calibrate")
    p.add_argument("--survey", required=True)
    p.set_defaults(run=calibrate_all)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
