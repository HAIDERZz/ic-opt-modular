"""Built-in recipe: signoff — optimize at one corner, then re-check the top-k across all corners."""

from __future__ import annotations

import json
from pathlib import Path

from ic_opt import blocks as b
from ic_opt.recipe import Run


def main(run: Run, *, corner: str = "tt", budget: int = 60, batch: int = 10, top: int = 5, strategy: str = "auto", seed: int = 0,
         current: bool = True, start: str | None = None) -> None:
    """``current`` / ``start`` as for ``optimize``, for the search step at ``corner``. ``strategy``: ``auto`` searches
    with ``metric_gp`` (one corner) unless the spec has EM devices (then ``openbox_gp_eic``)."""
    rows = json.loads(Path(run.project, start).read_text(encoding="utf-8")) if start else []
    b.doctor(run.spec, run.executor, cshrc=run.cshrc, store=run.store, limits=run.limits).require_pass()
    deck = b.import_netlists(run.spec, run.executor, run.store)
    search = b.optimize(run.spec, run.executor, run.store, deck=deck, strategy=strategy, budget=budget, batch=batch, seed=seed,
                        corners=[corner], step=f"search@{corner}", current=current, start=rows, cshrc=run.cshrc,
                        parallel_jobs=run.jobs, limits=run.limits)
    winners = b.points_from(b.best(run.spec, search, top))
    signoff = b.evaluate(run.spec, winners, run.executor, run.store, deck=deck, corners="all", step="signoff",
                         cshrc=run.cshrc, parallel_jobs=run.jobs, limits=run.limits)
    if signoff:
        run.note(f"report: {b.report(run.spec, signoff, run.store, title=f'{run.spec.project} — sign-off across all corners')}")
