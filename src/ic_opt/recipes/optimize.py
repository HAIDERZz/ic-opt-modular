"""Built-in recipe: optimize — today's "optimize" mode for any tb × corner shape."""

from __future__ import annotations

from ic_opt import blocks as b
from ic_opt.recipe import Run


def main(run: Run, *, strategy: str = "openbox_gp_eic", budget: int = 30, batch: int = 10, seed: int = 0, corners="all") -> None:
    b.doctor(run.spec, run.executor, cshrc=run.cshrc, store=run.store, limits=run.limits).require_pass()
    deck = b.import_netlists(run.spec, run.executor, run.store)
    obs = b.optimize(run.spec, run.executor, run.store, deck=deck, strategy=strategy, budget=budget, batch=batch,
                     seed=seed, corners=corners, cshrc=run.cshrc, parallel_jobs=run.jobs, limits=run.limits)
    if obs:
        run.note(f"report: {b.report(run.spec, obs, run.store)}")
