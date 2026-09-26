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
uv venv --python 3.11 .venv
uv pip install -e ".[em,turbo]" -e vendor/open-box -e vendor/TuRBO   # one resolver call; add dev / report / prf here too
ic-opt --version                                                     # ic-opt 0.3.0
bash scripts/check_clean_install.sh                                  # optional: the same install in throwaway venvs, smoke-tested
```

The package declares everything it imports at start-up (numpy, scipy, scikit-learn, threadpoolctl, ...).
OpenBox, which serves the `openbox_*` strategies (`opt.optimize`'s default), is vendored in `vendor/open-box`
instead of declared, because a path dependency does not survive into a published wheel. Install it in the
**same** `uv pip install` as the package so the resolver keeps its pins (numpy < 2, scipy < 1.13,
scikit-learn < 1.4, ConfigSpace <= 0.6.1, matplotlib < 3.9); together they have wheels only on Python 3.11.
Extras: `em` (klayout: pcell geometry and the EM stages), `turbo` (torch + gpytorch for the `turbo` strategy
and `latin_hypercube` designs; the CPU build is enough: uv's `--torch-backend cpu` skips the CUDA wheels),
`report` (SHAP parameter importance), `prf` (OpenBox random-forest surrogate, needs swig), `dev` (pytest,
ruff). The `turbo` strategy and `latin_hypercube` designs import TuRBO, which is installed from `vendor/TuRBO`
in the checkout like OpenBox (`-e vendor/TuRBO` above); TuRBO is under Uber's non-commercial licence
(`vendor/TuRBO/LICENSE.md`), which is why it is not copied into the package.

Cadence tools come from an environment file **on the simulation host**, csh or
sh (see [Site envelope](#site-envelope)): pass `--cshrc FILE`, set
`IC_OPT_CADENCE_CSHRC`, or put `cshrc:` in that host's entry of
`~/.ic-opt/site.yaml`. It is never guessed from the controller's disk.
Before the first run, write `~/.ic-opt/site.yaml` (see [Site envelope](#site-envelope)).

### Platforms

The simulation host is Linux: Spectre, OCEAN and EMX run there under `csh` /
`sh`, reached through the executor. The controller (the machine that runs
`ic-opt` and holds the project) can be Linux, macOS or Windows 10+. With
`--ssh-profile` it needs the OpenSSH client (`ssh`, `scp`; on Windows the
built-in "OpenSSH Client" feature) and `tar` (built into Windows 10+ and macOS).
On Windows ic-opt packs and unpacks the directory streams with Python's
`tarfile` instead, and handles the trees it fetches or uploads through
extended-length paths (`\\?\`), because Maestro exports carry names that Win32
path normalization would change (`amap/__dspf_information__.` ends in a dot), as
the 2026-09-26 Windows acceptance found.
Simulating on the controller itself (no `--ssh-profile`: `LocalExecutor`)
needs Linux or macOS. The device library (`lib.*`, `lib_design`) runs on all
three; it fits models in spawned worker processes, so a script that calls it
keeps its top-level work under `if __name__ == "__main__":`.

Every command runs in a process group of its own, so a timeout ends the whole
job and not only its shell: locally the group is killed; on an SSH host the
command runs under `setsid` and, once the local `ssh` is killed, one more `ssh`
sends its group SIGTERM (SIGKILL after a few seconds) and the timeout's message
says how that went. Ctrl-C and a terminal hangup are passed on to the running
commands, as when they shared ic-opt's process group; an SSH host's command that
Ctrl-C cut off gets the same extra `ssh` as a timed-out one. After Ctrl-C,
`sim.evaluate` starts no further point, stage or command: a point it interrupted
is not recorded (it is simulated again when the recipe runs again), and
`.icopt/steps.jsonl` lists the points recorded, interrupted and never started.

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
ic-opt migrate-store PROJECT --dry-run             # a store written by an earlier version: see Results
```

`--plan` is the only approval point: it prints everything a reviewer wants to
confirm before Spectre starts. Re-running a recipe continues it — `opt.optimize`
counts the observations its step already holds and stops at `budget`.

### spec.yaml

`examples/spec.yaml` is a complete three-testbench, three-corner example. Sections:
`testbenches` (Maestro export roots), `corners` + `corner_policy`, `variables`
(grid: lower / upper / step), `metrics` (OCEAN expressions), `constraints`,
`objective`, `simulator` (preset, retention, and the required `threads_per_run`,
`parallel_jobs`, `timeout_s`) and `budget.max_simulations`. A spec with no metrics
is a valid waveform-only fix-run. Resource fields have no defaults: a spec that
leaves one out is refused with the field's name. `simulator.license_queue_timeout_s`
is optional: how many seconds Spectre waits in its license queue, passed as
`+lqtimeout`; left out, the flag is not passed and Spectre waits as it does by
itself. Like `timeout_s`, it says how the problem is run, so it is not part of
the spec's fingerprint. `ic-opt migrate` writes `900` into a spec it converts from
a 0.1 project, which passed `+lqtimeout 900` to every Spectre run.

### Recipes

A recipe is `main(run, **params)`; `run` carries the spec, the run store, the
executor and the executor host's limits (`run.limits`, its site.yaml entry). The
built-ins (`optimize`, `fix_run`, `coarse_to_fine`, `signoff`) are 10–20 lines each
and are the examples:

```python
from ic_opt import blocks as b

def main(run, *, per_dim=3):
    deck = b.import_netlists(run.spec, run.executor, run.store)
    obs = b.evaluate(run.spec, b.points_grid(run.spec, per_dim=per_dim), run.executor, run.store,
                     deck=deck, step="sweep", cshrc=run.cshrc, parallel_jobs=run.jobs, limits=run.limits)
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

The example uses `demo_6m`, the fictitious six-metal profile that ships with the package (metals `M1`..`M6`,
`M6` the thick top one; `M1` carries the ground fixture). Your process has its own profile, found through
`IC_OPT_PROFILE_DIRS`, and its own metal names. Values marked placeholder are yours to fill in.

```yaml
devices:
  - id: xfmr_in
    generator: clean_port_xfm_bs          # six clean-port families ship in ic_opt.em.pcell (plugin: builtin:clean_port)
    profile: demo_6m                      # process rule profile; yours: IC_OPT_PROFILE_DIRS=/path/to/profiles
    ports: [P1, N1, P2, N2]
    fixed: {primary_outer_diameter_um: 90, primary_metal: "6", secondary_metal: "5", ground_fixture: {...}}
    variables: {primary_width_um: xfmr_in.wp}    # default: spec variables named <id>.<field>
em:
  process_file: /path/to/your.proc      # placeholder: your EMX process file, a path on the simulation host
  frequencies: {start_hz: 0, stop_hz: 200e9, step_hz: 1e9}
  accuracy: standard
  three_d_metals: [M6, M5]              # the windings' metals, by their EMX names
  threads: 4                            # required, placeholder: --parallel of one EMX run
  memory_gb: 32                         # required, placeholder: --max-memory; threads and memory bound the worker count
  timeout_s: 3600                       # required, placeholder: one EMX run's limit
bindings:
  - {testbench: lo_xfmr_tb, instance: NPORT0, device: xfmr_in, terminals: [P1, N1, P2, N2]}   # sNp columns follow this order
metrics:
  - {name: gain, unit: dB, testbench: lo_xfmr_tb, expression: 'value(db20(getData("gain" ?result "sp")) <f0_hz>)'}   # placeholder: your frequency
  - {name: Qp, unit: ratio, device: xfmr_in, quantity: Qp_peak}        # Lp/Qp/Ls/Qs/k at frequency_hz, or L*_lf L*_res Q*_peak SRF_* k_lf
```

A device's `topology` states how its S-parameters are measured: `drives`, one `[plus, minus]` port pair
per differential drive (the primary, then the secondary), and `grounded` ports. A device without one gets
it from its ports: two are one drive; four are two drives with the secondary reversed, `[[P1, N1], [N2, P2]]`,
because the built-in families wind it that way and so measure a positive `k`; `CT*` taps are grounded. A
generator of your own whose secondary winds the other way measures `k < 0`: the point's `issues` say so,
and the device needs `topology: {drives: [[P1, N1], [P2, N2]]}` (every port exactly once, taps under `grounded`).

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
`proc=` is a path on the machine running ic-opt; with `--ssh-profile P` it is a path on host `P`
(where EMX runs and the site's `.proc` usually stays), read through SSH into a temporary
directory that is deleted after the check.

A Python script of your own that fits library models (through `Library.models`, `lib.region`,
`lib.suggest`, `lib_design` or `lib_signoff`) must be a file that keeps its top-level work under
`if __name__ == "__main__":`. The fits run in spawned worker processes, and each worker imports the
script again: without the guard it re-runs the script, and code piped in on standard input
(`python - <<EOF`) cannot be imported at all. `ic-opt call` and `ic-opt run`, recipe files included,
need nothing.

Every EMX run is cached under `.icopt/cache/emx:<device>/` (`emx-<device>` on
Windows) by GDS bytes, port order, physics settings and the process file's
content hash. `ic-opt migrate` converts em-opt's `em_opt_requirement.md`
(Geometry Generator / EM Devices / EMX Settings / Nport Bindings). The private
process profiles never enter the repository. The `em` extra (klayout) is part
of the install above.

### Site envelope

`~/.ic-opt/site.yaml` states what each machine may give ic-opt, one entry per
host: `local` (the machine running ic-opt, also the simulation host without
`--ssh-profile`) and one per `--ssh-profile` name. `max_threads` and
`max_memory_gb` are required and have no defaults: a missing file, host entry or
field refuses to start, and so does a job bigger than its host. Every number
below is a placeholder: fill in what each of your hosts may give ic-opt.

```yaml
hosts:
  local:                               # the machine running ic-opt
    max_threads: 16                    # placeholder: fill in this host's
    max_memory_gb: 32                  # placeholder: fill in this host's
  lab:                                 # --ssh-profile lab
    max_threads: 64                    # placeholder: fill in this host's
    max_memory_gb: 128                 # placeholder: fill in this host's
    cshrc: /path/to/cadence_env.csh    # optional, like scratch_root, license_probe, transfer_timeout_s
```

`cshrc` names the host's environment file, whatever its shell (the key keeps
its name): a file whose name ends in `.csh`, `.cshrc`, `.tcsh` or `.tcshrc`
(`~/.cshrc` too) is sourced by `csh` and the tools run there; any other file,
such as `cadence_env.sh`, is sourced by POSIX `sh` (`. FILE`) and the tools run
in that `sh`. `--cshrc` and `IC_OPT_CADENCE_CSHRC` follow the same rule.

`env.doctor` asks the host only for the tools the spec's pipeline runs:
`spectre` and `ocean` (and, with `simulator.license_check`, the license query
`license_probe`, else `lmstat -a`) when the spec has testbenches, and the
spec's EMX binary (`em.binary`) when it has devices; a pure EM spec needs no
Spectre on the host. It prints the envelope (`jobs × threads / GB per job → total of
max_threads / max_memory_gb`) and fails a spec that asks for more; it also
compares the entry with what the host reports (`nproc`, `MemTotal`) and warns,
never blocks, when the entry is larger. `run.jobs` and the engine trim
concurrency to fit; recipes pass `limits=run.limits` to `sim.evaluate` and
`opt.optimize`. A flat 0.2.0 file (top-level `max_threads`) is read as
`hosts.local`, with a note to move it under `hosts:`. `hosts.local` is also
the budget of the device library's own compute (model fits, BLAS threads,
prediction chunks), which always runs on the machine running ic-opt:
[docs/em/library.md](docs/em/library.md#compute).

## Results

Everything lands under `PROJECT/.icopt/`: `observations.jsonl` (one row per
point: parameters, per-testbench/corner children, metrics, fom, feasibility,
status, provenance), `steps.jsonl`, `decks/`, `sims/<obs>/<tb>/<corner>/`,
`reports/report.md` + `report.html` (best observed, top feasible, constraint
margins, parameter importance, corners, space-compression advisory; four figures).

### Stores written by an earlier version

An observation's identity holds no machine facts any more: the spec fingerprint
leaves out how the problem is run (resources, timeouts, retention, the license
check, the budget), and the EMX stage (the EMX cache key, the EM pipeline
fingerprint, a library part's generation) is identified by the process file's
content instead of its path. Until a store written before this change is
restamped, its EM observations are not reused, its Spectre observations only
while `spec.yaml` stays exactly as it was, and those the 0.2.0 release wrote not
at all: the points would be simulated again (the EMX cache keys changed too),
and a library part's new rows would form a generation of their own. Restamp
every such store once, each project and each library part, before its next run
and before editing its spec:

```bash
ic-opt migrate-store PROJECT --dry-run      # what would change; "nothing to change" when the store is current
ic-opt migrate-store PROJECT                # add --ssh-profile P when the EMX process file lives on that host
```

It copies `.icopt/observations.jsonl` to `observations.jsonl.bak-<UTC time>`,
rewrites the two fingerprints of every row stamped by the store's spec as it
stands, moves EMX cache entries to their new keys and repoints a `library.yaml`
above the store that pins a restamped generation (backed up the same way). It
assumes the process file has not changed since the rows were simulated; a
second run changes nothing. Rows the 0.2.0 release wrote are matched by 0.2.0's
own formulas (its spec schema had no EM fields yet, and its pipeline
fingerprint hashed the stage names alone): those of the spec as it stands get
its problem fingerprint and the Spectre pipeline's current one, and are reused
from then on. The Spectre pipeline was the only one 0.2.0 shipped; a row from a
stage list a recipe assembled itself keeps its pipeline fingerprint. Resource
fields are required now, so a `spec.yaml` that left one to its old default is
refused. The old stamps hash that default, and the other resources and the
budget too: write the default in first (`simulator.threads_per_run: 10`;
`em.threads: 4`, `em.memory_gb: 32`, `em.timeout_s: 3600`), restamp with
everything else as it was when the rows were run, and only then set your own
values. Rows it cannot match stay as they are and are reported: rows of another
spec, the spec as it was before an edit included. They are not reused, and
still count against `budget.max_simulations`.

## 0.1 projects

The 0.1 command line, `ic-opt PROJECT --real|--doctor|--continue N`, was translated
for one release (0.2) and is refused since 0.3: it stops with exit code 2 and these
steps. Convert the project once, then run it like any other:

```bash
ic-opt migrate PROJECT NEW_PROJECT                # opt_requirement.md -> spec.yaml, and MIGRATION.md with the recipe to use
ic-opt run RECIPE NEW_PROJECT key=value --plan    # the command MIGRATION.md names, previewed; drop --plan to run
```

A project that 0.2 already converted in place (it has `spec.yaml`) skips the first
step: its NEW_PROJECT is PROJECT. `--doctor` is now `ic-opt doctor NEW_PROJECT`,
`--continue N` a re-run with `budget=` set to the points done plus N,
`--dry-orchestration` is `--plan` and `--cadence-cshrc F` is `--cshrc F`. MIGRATION.md
also lists every resource value it filled in from the 0.1 defaults: review those for
your machines before the first run. One is always there,
`simulator.license_queue_timeout_s: 900`: 0.1 always passed `+lqtimeout 900`, and
the converted project keeps that wait until you remove the line (Spectre then
uses its own). A `spec.yaml` 0.2 wrote is not changed.

## Development

```bash
.venv/bin/python -m pytest -q          # no Cadence needed (fake Spectre host); tests that need private data skip
# replay parity against recordings: IC_OPT_RECORDED_RUNS (Spectre), IC_OPT_EM_RECORDED_RUNS, IC_OPT_EM_RECORDED_ARGV,
# IC_OPT_EM_SWEEPS, IC_OPT_EM_DB (EM), IC_OPT_LIBRARY (a real library), plus IC_OPT_PROFILE_DIRS for the private
# process profiles
.venv/bin/ruff check                   # src, tests and the report scripts in docs/refactor/reports (pyproject include)
```

Design notes and the refactor record live in `docs/refactor/`; the remote
filesystem rule is `docs/adr/0001-remote-filesystem-boundary.md`.
