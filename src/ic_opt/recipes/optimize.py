"""Built-in recipe: optimize — today's "optimize" mode for any tb × corner shape."""

from __future__ import annotations

import json
from pathlib import Path

from ic_opt import blocks as b
from ic_opt.recipe import Run


def main(run: Run, *, strategy: str = "auto", budget: int = 30, total: int | None = None, batch: int = 10, seed: int = 0,
         corners="all", current: bool = True, start: str | None = None, initial_trials: int | None = None) -> None:
    """``current``: evaluate the design as exported first; ``start``: JSON list of parameter dicts (the format of
    ``fix_run``'s points file) to evaluate first. Neither is evaluated again when the run continues. ``strategy``: ``auto``
    runs ``metric_gp`` on a spec without EM devices, at any corners, else ``openbox_gp_eic`` (a line says which). ``total``:
    the budget a run advanced in increments is meant to reach (``budget=10 total=40``, then ``budget=20 total=40``, ...):
    the initial design is sized for it, as one call with ``budget=<total>`` sizes it (N-96). ``initial_trials``: the
    initial design's size of ``metric_gp`` / ``openbox_*``, stated (recorded for the step as a sized one is); left out,
    the strategy gets no such keyword."""
    rows = json.loads(Path(run.project, start).read_text(encoding="utf-8")) if start else []
    b.doctor(run.spec, run.executor, cshrc=run.cshrc, store=run.store, limits=run.limits).require_pass()
    deck = b.import_netlists(run.spec, run.executor, run.store)
    obs = b.optimize(run.spec, run.executor, run.store, deck=deck, strategy=strategy, budget=budget, total=total,
                     batch=batch, seed=seed, corners=corners, current=current, start=rows, cshrc=run.cshrc,
                     parallel_jobs=run.jobs, limits=run.limits,
                     **({"initial_trials": initial_trials} if initial_trials else {}))
    if obs:
        run.note(f"report: {b.report(run.spec, obs, run.store)}")
