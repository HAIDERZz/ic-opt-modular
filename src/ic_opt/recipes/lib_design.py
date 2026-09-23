"""Built-in recipe: lib_design -- optimize a device on library predictions (no EMX), report the leaders with their bounds.

``ic-opt run lib_design PROJECT library=<library root> [stratum=...] budget=200 strategy=turbo top=5``

The project's spec is an em_only spec (one pcell device, its variables, device metrics, constraints,
objective). Every candidate is built by the pcell (real generator, product DRC) and scored from the
library; the leaders are written to ``reports/lib_design.json`` with each metric's calibrated interval.
They are predictions: ``lib_signoff`` runs them through real EMX (after ``--plan`` and approval).
"""

from __future__ import annotations

import json

from ic_opt import blocks as b
from ic_opt.library import query, stage
from ic_opt.recipe import Run


def main(run: Run, *, library: str, stratum: str | None = None, budget: int = 200, batch: int = 20, strategy: str = "turbo",
         top: int = 5, seed: int = 0, k: float = 2.0) -> None:
    lib = query.Library(library)
    strata = {d.id: stratum or stage.match_stratum(lib, d) for d in run.spec.devices}
    pipeline = stage.surrogate_pipeline(run.spec, lib, strata, k=float(k))
    run.note(f"lib_design: {', '.join(f'{d} -> {s}' for d, s in strata.items())} ({library})")
    found = b.optimize(run.spec, run.executor, run.store, budget=int(budget), batch=int(batch), strategy=strategy, pipeline=pipeline,
                       step="lib_design", seed=int(seed), parallel_jobs=run.jobs, site=run.site)
    if run.plan:
        return
    leaders = b.best(run.spec, found, int(top))
    rows = []
    for o in leaders:
        child = next(iter(o.children.values()))
        answer = json.loads((run.store.project_dir / child.sim_dir / "predictions.json").read_text(encoding="utf-8")) if child.sim_dir else {}
        rows.append({"obs_id": o.obs_id, "params": o.params, "metrics": o.metrics, "objective": o.objective, "feasible": o.feasible,
                     "library": answer.get("quantities", {}), "measured_at": answer.get("measured")})
    out = run.store.root / "reports" / "lib_design.json"
    out.write_text(json.dumps({"strata": strata, "library": str(library), "evaluated": len(found), "leaders": rows}, indent=1, default=float),
                   encoding="utf-8")
    run.note(f"lib_design: {len(found)} candidates scored, {sum(o.status == 'ok' for o in found)} in the library's domain; leaders -> {out}")
