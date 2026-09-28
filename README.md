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
ic-opt --version                                                     # ic-opt 0.4.0
bash scripts/check_clean_install.sh                                  # optional: the same install in throwaway venvs, smoke-tested
```

`uv venv` creates the environment without pip: `uv pip install` needs none, and
`python -m pip` in it fails (`No module named pip`) without meaning the install
did; `uv pip list` shows what is there.

The package declares everything it imports at start-up (numpy, scipy, scikit-learn, threadpoolctl, ...).
OpenBox, which serves the `openbox_*` strategies (what the default `auto` runs with EM devices or several corners), is vendored in `vendor/open-box`
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
the 2026-09-26 Windows acceptance found. On Windows the `turbo` extra installs
torch < 2.9: scikit-learn < 1.4 (OpenBox's pin) loads its own older
`msvcp140.dll` at import, and torch 2.9+ cannot initialise on it (`WinError 1114`
from `c10.dll`), as the 2026-09-27 Windows acceptance found. ic-opt writes UTF-8
to stdout and stderr on every platform: Python's own streams follow the Windows
code page when redirected to a file, and `ic-opt blocks` crashed on a Chinese
controller's cp936 in the same acceptance; an explicit `PYTHONIOENCODING` still
wins. Both streams are line-buffered, so a run redirected to a log shows its
per-batch lines while it runs (the 2026-09-28 joint-optimization acceptance
could read them only after the process ended). Every text file ic-opt reads or writes names its encoding (UTF-8). The
plan check's fetched trees are removed through `localpath.literal` too, and a
test keeps every tree copy or removal on export trees there: the 2026-09-27
multi-corner acceptance (N-35) found `--plan` dying in a `TemporaryDirectory`
cleanup on the same trailing-dot file. A
Windows controller driven from Git Bash / MSYS2 must set `MSYS_NO_PATHCONV=1`
(or give the Cadence file through `cshrc:` in site.yaml): that shell rewrites a
remote absolute path such as `--cshrc /home/...` into `C:/Program Files/Git/home/...`
before ic-opt sees it, as the 2026-09-27 real-scenario acceptance found.
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
ic-opt digest PROJECT [--step S] [--top N] [--json]  # what the run found, from its observations; also while it runs
ic-opt advise PROJECT advice.yaml                  # advice for the next batches: start rows, ranges, fixed, vary
ic-opt advise PROJECT --list | --revoke a2 --reason "..."
ic-opt migrate OLD_PROJECT NEW_PROJECT             # 0.1 opt_requirement.md -> spec.yaml (+ MIGRATION.md)
ic-opt migrate-store PROJECT --dry-run             # a store written by an earlier version: see Results
```

`--plan` is the only approval point: it prints everything a reviewer wants to
confirm before Spectre starts, and it fetches every testbench's export and
templates it for every corner, so a variable an export lacks shows there
(`[plan] netlist.import <tb>: FAIL ...`) and not at the run. Re-running a recipe
continues it — `opt.optimize` counts the observations its step already holds and
stops at `budget`. `budget.max_simulations` counts every simulation the
project's store holds, including the observations an edited spec no longer
reuses: a spec edit that leaves earlier runs behind needs a larger budget, or a
new project.

`opt.optimize` evaluates the design as it stands first: the values the
exported netlists give the circuit variables (`current=false` leaves it out;
testbenches that disagree, a value outside a variable's range or a spec with EM
devices mean there is none, and a line says why; a value between grid points is
moved to the nearest one, and the line says so), then the rows of `start=FILE`
(a JSON list of parameter rows, the format of `fix_run`'s points file). They
are evaluated once: a continued run does not repeat them. The `openbox_*`
strategies' first `initial_trials` observations are those start points followed
by one seeded Sobol design, served in order whatever the batch size; the rest of
the batch that reaches the end of the design, and every later point, is the
surrogate's, once the history holds one more successful point than there are
variables (fewer: further space-filling points). The default is the smaller of
twice the number of variables and half the run's `budget`; `--plan` prints how
many of the run's points the surrogate proposes and warns when that is none,
each observation's `origin` is `start` or ends in `:init` or `:acq`, and
`initial_trials=N` overrides the default. The strategies are fed each point's
true objective (a point that misses a constraint included); a point whose
metrics failed is a failed trial for OpenBox and the worst target for `turbo`,
never a penalty number (`failure_penalty` is accepted and ignored). The same
seed therefore proposes other points than 0.4.0 did; recorded observations keep
their meaning and continue as before.

The default strategy is `auto`: `metric_gp` for a spec without EM devices run at
one condition (no corners, or one corner: `corners='["tt"]'`, the `signoff`
recipe's search), `openbox_gp_eic` otherwise, and a line says which and why
(`[optimize] strategy auto: metric_gp (no EM devices, one condition)`, under
`--plan` too). A strategy keyword the chosen strategy does not take is refused
before anything runs (`initial_trials` both take). A strategy named with
`strategy=` runs as named. `coarse_to_fine` runs `metric_gp` in both steps when
`auto` chooses it, else OpenBox then TuRBO; `lib_design` stays on `turbo`. The
comparisons behind the choice: `docs/refactor/T17_OPTIMIZER_PLAN_CN.md`, section 7.

A variable whose range is positive and spans a decade or more
(`upper / lower >= 10`) is searched on a logarithmic scale by `metric_gp`, the
`openbox_*` strategies and `turbo`: evenly per decade, the initial design too; every other
variable linearly. The range alone decides, not what the variable is (a
transistor width, a bias current and a device's turn width alike; no benchmark
covers the variables of EM devices). The grid is the spec's either way, and
`random`, `sobol` and the `points.*` blocks stay linear. On 12 wide-range test
problems TuRBO did better on 23 of 48 measures with it and worse on none,
OpenBox better on 14 and worse on 1 (plan, section 7). A spec without such a
variable is searched exactly as before; one with such a variable, continued by
this version, proposes differently from the version that started the run (the
same history read on another scale); nothing stored becomes invalid.

`metric_gp`: one Gaussian process per metric
the constraints and the objective name, the spec's own formulas applied to their
posterior samples, a search region on the spec's grid (logarithmic for a range
that spans a decade), a separate model of where points fail to give a value, and
no penalty number anywhere; numpy, scipy and scikit-learn only. Its design is
`initial_trials` points (default twice the variables, at least 8, at most 20
and at most half the budget), start points included, and origins end in `:init`, `:grid:<k>`, `:tr:<r>:<k>`,
`:wide:<r>:<k>` or `:anchor:<r>:<k>`. It does not take EM devices or several
corners at once yet: named with `strategy=metric_gp` for such a run, `opt.optimize`
refuses it before anything runs; run one corner or the `signoff` recipe.

Advice. ic-opt calls no language model; whoever reads what a run found (a
person, or the user's own agent) can give the run advice as data, between runs:

```yaml
# advice.yaml
author: "agent: <model>"            # who gives it; required
reason: "the best points sit at W1 >= 2u; L2 does not matter"   # why; required
start:                              # complete grid rows to evaluate first
  - {W1: 2.4u, L2: 0.1u, IB: 40u}
ranges: {W1: [2u, 4u]}              # inside the spec's range, never wider
fixed: {L2: 0.1u}                   # hold a variable at one level
vary: [W1, IB]                      # only these move; the others stay at the search region's centre
```

`ic-opt advise PROJECT advice.yaml` checks it against the spec -- an unknown
variable, a range or value outside the spec's range (the spec's ranges are the
user's to change; an advice never widens them), a start row that does not name
every variable, a range that holds no grid level are refused with what to
change; bounds between grid levels move inward, values between levels to the
nearest level, and a line says so -- then appends it with its id (`a1`, `a2`,
...) and `since` (the observations the next batch's strategy is handed) to
`.icopt/advice.jsonl`, and ends the advice before it. It takes the project's
lock: not while a run goes; continue the run afterwards with a larger budget.
`--list` shows every advice and which is in effect, `--revoke ID --reason ...`
ends one. A store that holds the problem at several sets of corners (the
`signoff` recipe's search corner and its re-check at all) needs `--corners tt`.
`opt.advise` / `opt.revoke_advice` are the same as blocks, for a recipe's own
code.

`opt.optimize` reads the file before every batch; the advice in effect at a
batch follows from the file's rows and the batch's history size alone, so a
continued run replays as it went. Its start rows come first, once, with origin
`advice:<id>`, whatever the strategy. Its ranges, fixed levels and `vary` are
`metric_gp`'s: a fifth of every batch is free -- it chooses among the
candidates the batch would have without advice (the search region's and those
spread over the spec's whole range), so the search the run was making goes on
at a fifth of its pace, whatever the advice says (in batches of one or two, every
fifth point of the run is free). The other slots choose among the search
region's candidates brought inside the advice (on a small grid, among the grid
points inside it), and those points' origins end in `@<id>`
(`suggest:metric_gp:tr:0:40@a2`). The initial design is not moved by an advice.
When the advice holds no point that is not evaluated, the whole batch chooses as
the free slots do. Another strategy uses the start rows only, and a line says
which parts it leaves unused. A run without the file is exactly the run it was
before advice existed.

### spec.yaml

Writing a spec from a design request -- the shape to pick, what to read off
the exports, every section's rules, four complete examples -- is the
[author-spec skill](skills/author-spec/SKILL.md).
`examples/spec.yaml` is a complete three-testbench, three-corner example (the
`MixerCS_*` cells of its exports: another mixer's exports carry other top-level
parameters, so check the `parameters` line before reusing its variables). Every
variable must be a top-level `parameters` entry of every testbench's exported
netlist; a device's variables are consumed by `devices` and are not. A corner
names its model section and, through `options` (values on the netlist's
`simulatorOptions` statement, `temp` above all), the simulation temperature:
an ADE export writes it there as a literal that no parameter follows, as the
2026-09-27 multi-corner acceptance found. Sections:
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
`simulator.operating_points` (default `true`) keeps every transistor's operating
point with each observation: an export without `info what=oppoint where=rawfile`
after a plain DC analysis gets `icoptOpInfo info what=oppoint where=rawfile` right
after its DC analysis, or, without one, `icoptDcOp dc` and that statement at the
end (after every analysis, so no metric changes); the rendered netlist says so in
a comment, and `doctor` / `--plan` print one `operating points:<tb>` line per
testbench (`in the export (<name>)`, `added by ic-opt (statement) ...`, `added by
ic-opt (DC analysis and statement) ...`, `off`). `false` adds and reads nothing.
Not part of the fingerprint: it changes no metric.

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
    variables: {primary_width_um: xfmr_in.wp}    # default: spec variables named <id>.<field>; beside testbenches a
                                                 # name without the prefix is a circuit variable
em:
  process_file: /path/to/your.proc      # placeholder: your EMX process file, a path on the simulation host (POSIX);
                                        # IC_OPT_PROFILE_DIRS is read where ic-opt runs: D:/work/profiles on Windows
  frequencies: {start_hz: 0, stop_hz: 200e9, step_hz: 1e9}
  accuracy: standard
  three_d_metals: [M6, M5]              # the windings' metals, by their EMX names
  threads: 4                            # required, placeholder: --parallel of one EMX run
  memory_gb: 32                         # required, placeholder: --max-memory; threads and memory bound the worker count
  # parallel_jobs: 4                    # optional: EMX runs at once across the workers (else as many as the workers)
  timeout_s: 3600                       # required, placeholder: one EMX run's limit
bindings:
  - {testbench: lo_xfmr_tb, instance: NPORT0, device: xfmr_in, terminals: [P1, N1, P2, N2]}   # sNp columns follow this order
  # a null keeps the export's wiring but drops that nport terminal (a tap wired to two terminals, a grounded one):
  # - {testbench: mixer_tb, instance: NPORT0, device: xfmr_ct, terminals: [P1, N1, CTP, null, P2, N2, CTS, null, null, null]}
metrics:
  - {name: gain, unit: dB, testbench: lo_xfmr_tb, expression: 'value(db20(getData("gain" ?result "sp")) <f0_hz>)'}   # placeholder: your frequency
  - {name: Qp, unit: ratio, device: xfmr_in, quantity: Qp_peak}        # Lp/Qp/Ls/Qs/k at frequency_hz, or L*_lf L*_res Q*_peak SRF_* k_lf
```

A binding lists the device's ports in the order of the instance's terminals; a `null` entry stands for a terminal the
device does not have, and `bind_nport` rewrites the instance to the kept terminals (their order stays the sNp column
order). That binds a six-port tapped transformer into a ten-terminal export whose p3 = p4 are the primary tap,
p7 = p8 the secondary tap and p9 = p10 ground, as in the example above.

A device's `topology` states how its S-parameters are measured: `drives`, one `[plus, minus]` port pair
per differential drive (the primary, then the secondary), and `grounded` ports. A device without one gets
it from its ports: two are one drive; four are two drives with the secondary reversed, `[[P1, N1], [N2, P2]]`,
because the built-in families wind it that way and so measure a positive `k`; `CT*` taps are grounded. A
generator of your own whose secondary winds the other way measures `k < 0`: the point's `issues` say so,
and the device needs `topology: {drives: [[P1, N1], [P2, N2]]}` (every port exactly once, taps under `grounded`).

Every family's fields, constraints and retired names: [docs/em/devices.md](docs/em/devices.md) (generated from the config models).

A **device library** (a directory of em_only run stores plus `library.yaml`) answers
L / Q / SRF / k for a geometry, suggests geometries for targets, and serves as a
surrogate pipeline for optimization before a real-EMX sign-off. A stratum's name
says little about its parts' physics, so `lib.coverage` answers with each part's
device (generator, profile, the metals its windings sit on) and the stratum's
`note` from `library.yaml`, and with `cached`: which quantities already have a
fitted model, which only a calibration, which nothing, so a query's wait is known
beforehand; the first fit of a quantity's model prints its progress on stderr
(minutes per quantity, more for a composed curve):

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

A Spectre run states threads but no memory (`simulator` has no memory field), so the
envelope's memory check covers EMX runs only; a testbench-only spec shows `0 GB per job`
in `doctor` and `--plan`, and its memory is bounded by the host, not by ic-opt.

## Results

Everything lands under `PROJECT/.icopt/`: `observations.jsonl` (one row per
point: parameters, per-testbench/corner children, metrics, fom, feasibility,
status, provenance; `metric_failed` when an OCEAN expression returned nil or a
non-scalar -- the child keeps the metrics that did extract and the point the
nominal corner's, so a wrong expression costs that metric, not the point; a
Spectre child also carries `operating_points`, `{instance: {quantity: value}}`
for the instances that report `gm`, with `region`, `ids`, `vgs`, `vds`, `vbs`,
`vth`, `vdsat`, `gm`, `gds`, `gmoverid`, `cgs`, `cgd` where the simulator gives a
number -- read after the metrics, it never fails a child; left out when there
are none, and no strategy reads it), `steps.jsonl`, `decks/`, `sims/<obs>/<tb>/<corner>/`,
`reports/report.md` + `report.html` (best observed, top feasible, constraint
margins, parameter importance, corners, where the best points are; four figures),
`reports/digest.md` + `digest.json` (`ic-opt digest`: what is optimized, how far the
run is, which constraints bind, where the good points are, where points gave no
value and the issue texts they gave most often, the strategy's state, and a table
of the advice given -- per advice its period, from its adoption to the row that
ended it; the points under it (`@<id>`) and the others proposed in its period,
each counted as points, feasible, no value and best objective; its start points,
and whether each was the run's best when evaluated; a line when the run's best
point lies at a bound of the advice's range that is not the spec's -- every
number computed from the observations).

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
