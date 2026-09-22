"""Example recipe: a thinned grid sweep, then the best feasible point.

    ic-opt run examples/sweep.py PROJECT per_dim=3 --plan
"""

from ic_opt import blocks as b


def main(run, *, per_dim: int = 3) -> None:
    b.doctor(run.spec, run.executor, cshrc=run.cshrc, store=run.store, site=run.site).require_pass()
    deck = b.import_netlists(run.spec, run.executor, run.store)
    obs = b.evaluate(run.spec, b.points_grid(run.spec, per_dim=per_dim), run.executor, run.store,
                     deck=deck, step="sweep", cshrc=run.cshrc, parallel_jobs=run.jobs, site=run.site)
    if obs:
        run.note(f"best: {b.best(run.spec, obs)[0].params}")
        run.note(f"report: {b.report(run.spec, obs, run.store)}")
