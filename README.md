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
uv venv .venv && uv pip install -e vendor/open-box -e ".[dev,report]"
ic-opt --version                      # ic-opt 0.2.0
```

Extras: `em` (klayout for the pcell geometry library), `report` (SHAP parameter
importance), `turbo` (torch + gpytorch for the `turbo` strategy; the CPU build
is enough), `prf` (OpenBox random-forest surrogate, needs swig). Install everything in **one** `uv pip install` so the
resolver keeps numpy < 2, which the vendored OpenBox requires.

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

`sim.evaluate` is a generic engine (dedup, budget, lock, testbench × corner
and device fan-out, corner aggregation, point-stage cache, retention) running
a pipeline of stages. The Spectre pipeline is `render → spectre → ocean →
extract`; the EM pipelines add stages in front of it, not another engine:

```text
Point ─pcell─▶ Geometry ─emx (per device, cached)─▶ ┬─ bind_nport ─▶ spectre ─▶ ocean ─▶ extract   (testbench children)
                                                     └─ measure                                       (device children)
```

`evaluate` picks the pipeline from the spec: `devices` + `testbenches` →
EM circuit, `devices` only → EM characterization (`pcell → emx → measure`),
neither → Spectre. The recipes do not change.

### EM devices

```yaml
devices:
  - id: xfmr_in
    generator: clean_port_xfm_bs          # six clean-port families ship in ic_opt.em.pcell (plugin: builtin:clean_port)
    profile: n28_1p10m                    # process rule profile: IC_OPT_PROFILE_DIRS=/path/to/profiles (demo_6m ships)
    ports: [P1, N1, P2, N2]
    fixed: {primary_outer_diameter_um: 90, primary_metal: "10", secondary_metal: "9", ground_fixture: {...}}
    variables: {primary_width_um: xfmr_in.wp}    # default: spec variables named <id>.<field>
em:
  process_file: /site/tsmcN28.proc      # on the simulation host
  frequencies: {start_hz: 0, stop_hz: 200e9, step_hz: 1e9}
  accuracy: standard
  three_d_metals: [M10, AP]
  threads: 4                            # --parallel;  memory_gb: 32 -> --max-memory; both bound the worker count
bindings:
  - {testbench: lo_xfmr_tb, instance: NPORT0, device: xfmr_in, terminals: [P1, N1, P2, N2]}   # sNp columns follow this order
metrics:
  - {name: gain, unit: dB, testbench: lo_xfmr_tb, expression: 'value(db20(getData("gain" ?result "sp")) 4e10)'}
  - {name: Qp, unit: ratio, device: xfmr_in, quantity: Qp_peak}        # Lp/Qp/Ls/Qs/k at frequency_hz, or L*_lf L*_res Q*_peak SRF_* k_lf
```

Every family's fields, constraints and retired names: [docs/em/devices.md](docs/em/devices.md) (generated from the config models).

A **device library** (a directory of em_only run stores plus `library.yaml`) answers
L / Q / SRF / k for a geometry, suggests geometries for targets, and serves as a
surrogate pipeline for optimization before a real-EMX sign-off:

```bash
ic-opt call lib.query LIBRARY stratum=ind_sym_top 'params={"outer_diameter_um": 150, "width_um": 5, "spacing_um": 3, "turns": 2}'
ic-opt call lib.suggest LIBRARY stratum=ind_sym_top 'targets={"Lp_lf": {"target": 1.2e-9, "tol": 0.03}}' objective=max:Qp_peak
ic-opt run lib_design PROJECT library=LIBRARY          # optimize on predictions (no EMX)
ic-opt run lib_signoff PROJECT library=LIBRARY candidates=REPORT --plan
ic-opt call em.validate_profile PROFILE_DIR proc=SITE.proc generate=true    # bring up a new process profile
```

Building, querying and growing a library: [docs/em/library.md](docs/em/library.md); writing a
process profile: [skills/author-process-rule/SKILL.md](skills/author-process-rule/SKILL.md).

Every EMX run is cached under `.icopt/cache/emx:<device>/` by GDS bytes, port
order, physics settings and the process file's content hash. `ic-opt migrate`
converts em-opt's `em_opt_requirement.md` (Geometry Generator / EM Devices /
EMX Settings / Nport Bindings). The private process profiles never enter the
repository. Install with `uv pip install -e ".[em]"` (klayout).

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
# replay parity against recordings: IC_OPT_RECORDED_RUNS (Spectre), IC_OPT_EM_RECORDED_RUNS, IC_OPT_EM_SWEEPS,
# IC_OPT_EM_OPT_REPO, IC_OPT_EM_DB (EM), plus IC_OPT_PROFILE_DIRS for the private process profiles
.venv/bin/ruff check src tests
```

Design notes and the refactor record live in `docs/refactor/`; the remote
filesystem rule is `docs/adr/0001-remote-filesystem-boundary.md`.
