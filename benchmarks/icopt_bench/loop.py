"""One (problem, method, seed) run: suggest <-> evaluate until the budget is spent, written to one JSON file.

Every strategy is driven through the product's own entry point, ``ic_opt.blocks.optimize.suggest`` -- nothing here
proposes or scores a point. ``run_one`` is the unit both ``sweep.py`` (many runs, out of process) and the tests
drive; the CLI wraps it for a single run.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path

import numpy as np
import scipy
import sklearn

import ic_opt
from ic_opt import space
from ic_opt.blocks.optimize import suggest
from ic_opt.observation import Observation, Observations
from icopt_bench import registry
from icopt_bench.cache import cached
from icopt_bench.problem import Problem, observe

DEFAULT_BUDGET = 200
DEFAULT_BATCH = 10


def run_one(problem: Problem, method: str, seed: int, *, budget: int = DEFAULT_BUDGET, batch: int = DEFAULT_BATCH,
            n_init: int | None = None, cache: str | Path | None = None,
            advice: Callable[[Observations], list[dict]] | None = None) -> dict:
    """Run ``suggest`` <-> ``observe`` until ``budget`` points are gathered (or ``suggest`` stops proposing). Returns
    the run's result dict (schema: see the module docstring of ``benchmarks/icopt_bench/loop.py``'s CLI, or just read
    the keys below) -- never raises: an exception during the run is caught, recorded as ``error``, and the points
    gathered so far are kept. ``advice``: called with the history before every batch, returns the advice rows
    (``ic_opt.advice``) ``suggest`` is handed, as ``opt.optimize`` hands it the project's advice file; none by default,
    and then nothing is handed and every run is what it was before advice existed."""
    if cache is not None:
        problem = cached(problem, cache)
    dim = len(problem.spec.variables)
    if n_init is None:
        n_init = min(2 * dim, 20)
    kwargs: dict = {}
    if method.startswith("openbox") or method == "metric_gp":
        kwargs["initial_trials"] = n_init
    if method in ("turbo", "turbo_trust_region"):
        kwargs["n_init"] = n_init                            # the same initial design size for every model-based method
    # The benchmark drives suggest() as opt.optimize of the code under test does. Since T17.0b suggest() takes the
    # start points and the run's seed unchanged for every batch (the seed fixes the design); 0.4.0 has neither and its
    # opt.optimize passed seed + the number of points done.
    since_0b = "start" in inspect.signature(suggest).parameters
    if since_0b:
        kwargs["start"] = problem.start

    history = Observations()
    suggest_seconds: list[float] = []
    evaluate_seconds: list[float] = []
    error: str | None = None
    wall_start = time.perf_counter()
    try:
        while len(history) < budget:
            n = min(batch, budget - len(history))
            advised = {"advice": advice(history)} if advice is not None else {}
            t0 = time.perf_counter()
            points = suggest(problem.spec, history, n, strategy=method, seed=seed if since_0b else seed + len(history),
                             **kwargs, **advised)
            suggest_seconds.append(time.perf_counter() - t0)
            if not points:
                break
            t1 = time.perf_counter()
            for point in points:
                history.append(observe(problem, point, len(history)))
            evaluate_seconds.append(time.perf_counter() - t1)
    except Exception:  # noqa: BLE001 -- a strategy or an evaluation can fail in any way; the run records it and stops there
        error = traceback.format_exc()
    wall_seconds = time.perf_counter() - wall_start

    return {
        "problem": problem.name,
        "method": method,
        "seed": seed,
        "budget": budget,
        "batch": batch,
        "n_init": n_init,
        "dim": dim,
        "grid_size": str(space.grid_size(problem.spec)),
        "family": problem.family,
        "scenario": problem.scenario,
        "points": [_point_row(problem, obs, index) for index, obs in enumerate(history, start=1)],
        "suggest_seconds": suggest_seconds,
        "evaluate_seconds": evaluate_seconds,
        "wall_seconds": wall_seconds,
        "error": error,
        "versions": {
            "ic_opt": ic_opt.__version__, "numpy": np.__version__, "scipy": scipy.__version__,
            "sklearn": sklearn.__version__, "python": sys.version,
        },
    }


def _point_row(problem: Problem, obs: Observation, index: int) -> dict:
    missing = sorted({m.name for m in problem.spec.metrics} - set(obs.metrics)) if obs.status == "metric_failed" else []
    return {
        "index": index, "params": obs.params, "origin": obs.origin, "status": obs.status, "feasible": obs.feasible,
        "objective": obs.objective, "fom": obs.fom, "constraint_penalty": obs.constraint_penalty, "metrics": obs.metrics,
        "missing": missing,
    }


def write_atomic(path: Path, result: dict) -> None:
    """Write ``result`` as JSON to ``path``, atomically (a temp file next to it, then ``rename``) -- shared with
    ``sweep.py``, which uses it for a timed-out job's result too."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    tmp.rename(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one (problem, method, seed) benchmark point.")
    parser.add_argument("problem")
    parser.add_argument("method")
    parser.add_argument("seed", type=int)
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--n-init", type=int, default=None)
    parser.add_argument("--cache", default=None, help="evaluation cache directory (simulator-backed problems only)")
    parser.add_argument("--out", required=True, help="directory to write <problem>__<method>__<seed>.json into")
    args = parser.parse_args(argv)

    problem = registry.get(args.problem)
    result = run_one(problem, args.method, args.seed, budget=args.budget, batch=args.batch, n_init=args.n_init, cache=args.cache)
    dest = Path(args.out) / f"{args.problem}__{args.method}__{args.seed}.json"
    write_atomic(dest, result)
    return 1 if result["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
