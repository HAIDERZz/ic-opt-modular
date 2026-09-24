"""Built-in recipe: fix_run — simulate user-listed points once, optionally with waveform exports."""

from __future__ import annotations

import json
from pathlib import Path

from ic_opt import blocks as b
from ic_opt.recipe import Run
from ic_opt.sim.ocean import WaveformExport


def main(run: Run, *, points: str = "points.json", waveforms: str | None = None, corners="all") -> None:
    """``points``: JSON list of parameter dicts; ``waveforms``: JSON list of {name, expression, testbench?}."""
    rows = json.loads(Path(run.project, points).read_text(encoding="utf-8"))
    exports = [WaveformExport(**w) for w in json.loads(Path(run.project, waveforms).read_text(encoding="utf-8"))] if waveforms else []
    b.doctor(run.spec, run.executor, cshrc=run.cshrc, store=run.store, limits=run.limits).require_pass()
    deck = b.import_netlists(run.spec, run.executor, run.store)
    obs = b.evaluate(run.spec, b.points_fixed(run.spec, rows), run.executor, run.store, deck=deck, corners=corners,
                     waveforms=exports, step="fix_run", cshrc=run.cshrc, parallel_jobs=run.jobs, limits=run.limits)
    if obs:
        run.note(f"report: {b.report(run.spec, obs, run.store)}")
