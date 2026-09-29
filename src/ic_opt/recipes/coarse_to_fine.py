"""Built-in recipe: coarse_to_fine — a global search, then a refinement that goes on from what it found."""

from __future__ import annotations

import json
from pathlib import Path

from ic_opt import blocks as b
from ic_opt import suggesters
from ic_opt.recipe import Run


def main(run: Run, *, strategy: str = "auto", coarse_budget: int = 40, fine_budget: int = 40, batch: int = 10, seed: int = 0,
         current: bool = True, start: str | None = None) -> None:
    """``current`` / ``start`` as for ``optimize``, for the coarse step; the fine step starts from what the coarse found.

    ``strategy``: ``auto`` (the default) resolves as in ``optimize``. To ``metric_gp`` (no EM devices, any corners): both
    steps run it, since its search region narrows onto what it found, which is what the fine step was for; to
    ``openbox_gp_eic``: OpenBox's GP + EIC, then TuRBO warm-started from it (``initial=``), as before T17.2. A named
    strategy runs both steps. A fine step of the coarse step's own strategy continues it from the store, which already
    holds the coarse points: ``initial=`` would hand them over a second time (``metric_gp`` would model every one twice
    and replay its search region with the copies as points before its first batch)."""
    rows = json.loads(Path(run.project, start).read_text(encoding="utf-8")) if start else []
    coarse_strategy = strategy
    if strategy == suggesters.AUTO:          # optimize resolves the coarse step alike (all corners, no initial rows)
        coarse_strategy, _ = suggesters.resolve_auto(run.spec, len(run.spec.corner_ids))
    fine_strategy = "turbo" if strategy == suggesters.AUTO and coarse_strategy == "openbox_gp_eic" else coarse_strategy
    b.doctor(run.spec, run.executor, cshrc=run.cshrc, store=run.store, limits=run.limits).require_pass()
    deck = b.import_netlists(run.spec, run.executor, run.store)
    coarse = b.optimize(run.spec, run.executor, run.store, deck=deck, strategy=strategy, budget=coarse_budget,
                        batch=batch, seed=seed, step="coarse", current=current, start=rows, cshrc=run.cshrc,
                        parallel_jobs=run.jobs, limits=run.limits)
    fine = b.optimize(run.spec, run.executor, run.store, deck=deck, strategy=fine_strategy, budget=fine_budget, batch=batch,
                      seed=seed, step="fine", initial=coarse if fine_strategy != coarse_strategy else (), current=False,
                      cshrc=run.cshrc, parallel_jobs=run.jobs, limits=run.limits)
    if coarse or fine:
        run.note(f"report: {b.report(run.spec, [*coarse, *fine], run.store)}")
