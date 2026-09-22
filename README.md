# IC-Opt

Composable blocks and recipes for Cadence Spectre/OCEAN design optimization.
A `spec.yaml` says **what** the design problem is; a recipe (a few lines of
Python composing blocks) says **how** to attack it; one CLI runs either.

```text
spec.yaml  ──►  ic-opt run <recipe> <project> [--plan]  ──►  .icopt/observations.jsonl + reports/
                       │
                       └─ recipe = blocks:  env.doctor → netlist.import → points.* / opt.optimize → sim.evaluate → analyze.report
```

## Install

```bash
uv venv .venv && uv pip install -e vendor/open-box -e ".[dev]"     # torch CPU is enough
ic-opt --version                                                    # ic-opt 0.2.0
```

Cadence tools come from a csh environment file **on the simulation host**:
pass `--cshrc FILE`, set `IC_OPT_CADENCE_CSHRC`, or put `cshrc:` in
`~/.ic-opt/site.yaml`. It is never guessed from the controller's disk.

## Use

```bash
ic-opt run optimize PROJECT --plan                 # preview: checks, blocks, simulation count, concurrency — starts nothing
ic-opt run optimize PROJECT budget=60 batch=10 strategy=turbo
ic-opt run fix_run  PROJECT points=points.json waveforms=waveforms.json
ic-opt run signoff  PROJECT corner=tt top=5        # search one corner, re-check the winners across all
ic-opt run my_recipe.py PROJECT --ssh-profile lab  # simulate on a remote host over OpenSSH
ic-opt blocks | ic-opt describe sim.evaluate       # what a recipe can compose
ic-opt call points.sobol PROJECT n=12 seed=3       # one block by name
ic-opt doctor PROJECT                              # tools, license, exports, site envelope, budget
ic-opt migrate OLD_PROJECT NEW_PROJECT             # 0.1 opt_requirement.md -> spec.yaml (+ MIGRATION.md)
```

`--plan` is the only approval point: it prints everything a reviewer wants to
confirm before Spectre starts. Re-running a recipe continues it — `opt.optimize`
counts the observations its step already holds and stops at `budget`.

### spec.yaml

`examples/spec.yaml` is a complete three-testbench, three-corner example. Sections:
`testbenches` (Maestro export roots), `corners` + `corner_policy`, `variables`
(grid: lower / upper / step), `metrics` (OCEAN expressions), `constraints`,
`objective`, `simulator` (preset, threads_per_run, parallel_jobs, timeout,
retention) and `budget.max_simulations`. A spec with no metrics is a valid
waveform-only fix-run.

### Recipes

A recipe is `main(run, **params)`; `run` carries the spec, the run store, the
executor and the site limits. The built-ins (`optimize`, `fix_run`,
`coarse_to_fine`, `signoff`) are 10–20 lines each and are the examples:

```python
from ic_opt import blocks as b

def main(run, *, per_dim=3):
    deck = b.import_netlists(run.spec, run.executor, run.store)
    obs = b.evaluate(run.spec, b.points_grid(run.spec, per_dim=per_dim), run.executor, run.store,
                     deck=deck, step="sweep", cshrc=run.cshrc, parallel_jobs=run.jobs)
    run.note(f"report: {b.report(run.spec, obs, run.store)}")
```

### Evaluation = engine + stages

`sim.evaluate` is a generic engine (dedup, budget, lock, tb × corner fan-out,
corner aggregation, retention) running a pipeline of stages. The Spectre
pipeline is `render → spectre → ocean → extract`; other simulators (EM
extraction, for instance) are new stages, not new engines.

### Site envelope

`~/.ic-opt/site.yaml` caps what one machine may take (`max_threads: 128`,
`max_memory_gb: 128` by default); `env.doctor` refuses specs beyond it and
`run.jobs` trims concurrency to fit.

## Results

Everything lands under `PROJECT/.icopt/`: `observations.jsonl` (one row per
point: parameters, per-testbench/corner children, metrics, fom, feasibility,
status, provenance), `steps.jsonl`, `decks/`, `sims/<obs>/<tb>/<corner>/`,
`reports/report.md` + `report.html` (best observed, top feasible, constraint
margins, parameter importance, corners, space-compression advisory; four figures).

## 0.1 projects

`ic-opt PROJECT --real|--doctor|--continue N [--ssh-profile P] [--cadence-cshrc F]`
still works for one release: it migrates `opt_requirement.md` to `spec.yaml` in
place and runs the matching recipe, printing the 0.2 command it used.

## Development

```bash
.venv/bin/python -m pytest -q          # 70 tests, ~12 s, no Cadence needed (fake Spectre host)
# replay-parity tests against recorded 0.1.10 runs: IC_OPT_RECORDED_RUNS=/path/run1:/path/run2
.venv/bin/ruff check src tests
```

Design notes and the refactor record live in `docs/refactor/`; the remote
filesystem rule is `docs/adr/0001-remote-filesystem-boundary.md`.
