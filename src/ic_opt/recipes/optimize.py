"""Built-in recipe: optimize — today's "optimize" mode for any tb × corner shape."""

from __future__ import annotations

import json
from pathlib import Path

from ic_opt import blocks as b
from ic_opt.recipe import Run


def main(run: Run, *, strategy: str = "auto", budget: int = 30, batch: int = 10, seed: int = 0, corners="all",
         current: bool = True, start: str | None = None) -> None:
    """``current``: evaluate the design as exported first; ``start``: JSON list of parameter dicts (the format of
    ``fix_run``'s points file) to evaluate first. Neither is evaluated again when the run continues. ``strategy``: ``auto``
    runs ``metric_gp`` on a spec without EM devices at one condition, else ``openbox_gp_eic`` (a line says which)."""
    rows = json.loads(Path(run.project, start).read_text(encoding="utf-8")) if start else []
    b.doctor(run.spec, run.executor, cshrc=run.cshrc, store=run.store, limits=run.limits).require_pass()
    deck = b.import_netlists(run.spec, run.executor, run.store)
    obs = b.optimize(run.spec, run.executor, run.store, deck=deck, strategy=strategy, budget=budget, batch=batch,
                     seed=seed, corners=corners, current=current, start=rows, cshrc=run.cshrc, parallel_jobs=run.jobs,
                     limits=run.limits)
    if obs:
        run.note(f"report: {b.report(run.spec, obs, run.store)}")
