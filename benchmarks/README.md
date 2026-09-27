# ic-opt benchmark

Compares optimizer strategies on problems that drive the product's own entry point,
`ic_opt.blocks.optimize.suggest` -- nothing here proposes or scores a point; `ic_opt.sim.corner.aggregate`
does that, exactly as a real project would. See `docs/refactor/T17_OPTIMIZER_PLAN_CN.md` section 4 for the design.

## Problems

Eight synthetic problems today (`icopt_bench/synthetic.py`), registered under `icopt_bench.registry`: standard
constrained test functions (Ackley-10, Hartmann-6, Levy-20) plus a few purpose-built ones -- a small, tightly
constrained grid; a multimodal one; a mostly-infeasible one; one with an undefined metric region; an analytic
two-stage amplifier. AnalogGym problems land later, added lazily through `icopt_bench.analoggym.problems()`.

## Running it

```bash
# one (problem, method, seed) run, straight to a JSON file
PYTHONPATH=src:benchmarks .venv/bin/python -m icopt_bench.loop syn_small_tight sobol 0 --budget 200 --batch 10 --out /tmp/run

# a whole grid, resumable, at most --jobs subprocesses at once
PYTHONPATH=src:benchmarks .venv/bin/python -m icopt_bench.sweep \
    --family synthetic --methods random,sobol --seeds 0-19 --budget 200 --batch 10 --jobs 8 --out /tmp/sweep

# tables + figures from a sweep's results
PYTHONPATH=src:benchmarks .venv/bin/python -m icopt_bench.report /tmp/sweep --out /tmp/report --baseline random --candidate sobol
```

## Measures (`icopt_bench/measures.py`)

- **first_feasible**: how many points it took to find one that satisfies every constraint; a run that never does
  counts as `budget + 1`.
- **best_at(n)**: the best (minimization-form) objective among the feasible points found in the first `n`.
- **success rate**: the share of seeds that found a feasible point within a budget.

Every (problem, method) is reported over its seeds as a median and a worst quartile (75th percentile) -- not just a
mean, since one bad seed should show up. `summary.md`/`summary.csv` give these at 50, 100 and 200 points from a
single 200-point run (the model-based strategies' initial design size, `n_init`, is fixed per run so the reading at
50 stays comparable across runs). `comparison.md` (with `--baseline`/`--candidate`) verdicts each measure `better`,
`worse` or `within noise` against the spread between seeds.

## Held-out problems

`benchmarks/split.json` (if present) lists problems held out from routine comparisons, decided once and not looked
at while iterating. `sweep.py` and `report.py` refuse a held-out problem unless given `--heldout --reason "..."`, in
which case the look is appended to `HELDOUT_LOG.md` and its results are read from / written to `DIR/heldout/`.
