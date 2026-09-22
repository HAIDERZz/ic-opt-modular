"""Built-in recipe: coarse_to_fine — global search with OpenBox, then TuRBO refinement warm-started from it."""

from __future__ import annotations

from ic_opt import blocks as b
from ic_opt.recipe import Run


def main(run: Run, *, coarse_budget: int = 40, fine_budget: int = 40, batch: int = 10, seed: int = 0) -> None:
    b.doctor(run.spec, run.executor, cshrc=run.cshrc, store=run.store, site=run.site).require_pass()
    deck = b.import_netlists(run.spec, run.executor, run.store)
    coarse = b.optimize(run.spec, run.executor, run.store, deck=deck, strategy="openbox_gp_eic", budget=coarse_budget,
                        batch=batch, seed=seed, step="coarse", cshrc=run.cshrc, parallel_jobs=run.jobs)
    fine = b.optimize(run.spec, run.executor, run.store, deck=deck, strategy="turbo", budget=fine_budget, batch=batch,
                      seed=seed, step="fine", initial=coarse, cshrc=run.cshrc, parallel_jobs=run.jobs)
    if coarse or fine:
        run.note(f"report: {b.report(run.spec, [*coarse, *fine], run.store)}")
