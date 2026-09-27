"""Built-in recipe: coarse_to_fine — global search with OpenBox, then TuRBO refinement warm-started from it."""

from __future__ import annotations

import json
from pathlib import Path

from ic_opt import blocks as b
from ic_opt.recipe import Run


def main(run: Run, *, coarse_budget: int = 40, fine_budget: int = 40, batch: int = 10, seed: int = 0, current: bool = True,
         start: str | None = None) -> None:
    """``current`` / ``start`` as for ``optimize``, for the coarse step; the fine step starts from what the coarse found."""
    rows = json.loads(Path(run.project, start).read_text(encoding="utf-8")) if start else []
    b.doctor(run.spec, run.executor, cshrc=run.cshrc, store=run.store, limits=run.limits).require_pass()
    deck = b.import_netlists(run.spec, run.executor, run.store)
    coarse = b.optimize(run.spec, run.executor, run.store, deck=deck, strategy="openbox_gp_eic", budget=coarse_budget,
                        batch=batch, seed=seed, step="coarse", current=current, start=rows, cshrc=run.cshrc,
                        parallel_jobs=run.jobs, limits=run.limits)
    fine = b.optimize(run.spec, run.executor, run.store, deck=deck, strategy="turbo", budget=fine_budget, batch=batch,
                      seed=seed, step="fine", initial=coarse, current=False, cshrc=run.cshrc, parallel_jobs=run.jobs,
                      limits=run.limits)
    if coarse or fine:
        run.note(f"report: {b.report(run.spec, [*coarse, *fine], run.store)}")
