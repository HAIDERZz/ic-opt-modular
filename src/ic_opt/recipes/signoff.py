"""Built-in recipe: signoff — optimize at one corner, then re-check the top-k across all corners (a point stops at its
first failing corner unless ``full``)."""

from __future__ import annotations

import json
from pathlib import Path

from ic_opt import blocks as b
from ic_opt.recipe import Run


def main(run: Run, *, corner: str = "tt", budget: int = 60, batch: int = 10, top: int = 5, strategy: str = "auto", seed: int = 0,
         current: bool = True, start: str | None = None, full: bool = False) -> None:
    """``current`` / ``start`` as for ``optimize``, for the search step at ``corner``. ``strategy``: ``auto`` searches
    with ``metric_gp`` (one corner) unless the spec has EM devices (then ``openbox_gp_eic``). Unless
    ``simulator.stop_at_first_failure`` says otherwise, a point of the search, at one corner, runs every simulation, and a
    re-checked point stops at its first failing corner (T17.9; T17.8: on the multi-corner benchmark 550 to 1340 of 3100
    simulations per run went to points already known to fail); ``full=True`` simulates every corner of every re-checked
    point, for a complete per-corner table."""
    rows = json.loads(Path(run.project, start).read_text(encoding="utf-8")) if start else []
    b.doctor(run.spec, run.executor, cshrc=run.cshrc, store=run.store, limits=run.limits).require_pass()
    deck = b.import_netlists(run.spec, run.executor, run.store)
    search = b.optimize(run.spec, run.executor, run.store, deck=deck, strategy=strategy, budget=budget, batch=batch, seed=seed,
                        corners=[corner], step=f"search@{corner}", current=current, start=rows, cshrc=run.cshrc,
                        parallel_jobs=run.jobs, limits=run.limits)
    winners = b.points_from(b.best(run.spec, search, top))
    signoff = b.evaluate(run.spec, winners, run.executor, run.store, deck=deck, corners="all", step="signoff",
                         cshrc=run.cshrc, parallel_jobs=run.jobs, limits=run.limits,
                         stop_at_first_failure=False if full else None)
    if signoff:
        run.note(f"report: {b.report(run.spec, signoff, run.store, title=f'{run.spec.project} — sign-off across all corners')}")
