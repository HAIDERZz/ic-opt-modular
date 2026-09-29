# T17.12 — the threads a run really uses: the strategy's own computation (N-73) and the extraction process (N-78)

Status: specification (2026-09-30), for the coding subagent. Decisions: the user on 2026-09-30 let the coordinator
plan these two items ("允许 ... 总线程不要超 192 就行 自行规划"). Both come from measurements of 2026-09-28/29
(`BACKLOG_CN.md` N-73, N-78): the strategy's numeric libraries take every core of the machine when no environment
variable caps them, and a run of `parallel_jobs: 10, threads_per_run: 1` occupied about 12 cores (Spectre at one
thread takes 1.0 to 1.35 cores, and the OCEAN process that extracts the metrics runs beside it).

## 1. N-73: the strategy's threads

- `Simulator.strategy_threads: int = 1` (`Field(ge=1)`), outside the fingerprint (`_NOT_PROBLEM`), left out of the
  dump when 1 (as the other resource fields with a default are handled; check `_dump`). Meaning: the threads the
  strategy's own computation may use while it proposes a batch — `metric_gp` (numpy / scipy / scikit-learn through
  BLAS and OpenMP), `openbox_*` and `turbo` (torch, and their BLAS).
- Applied in one place, around the strategy call in `blocks/optimize.suggest` (and so under `opt.optimize`,
  `opt.suggest`, the recipes): `threadpoolctl.threadpool_limits(limits=n)` (all user APIs) for the duration of the
  call, and `torch.set_num_threads(n)` inside the torch strategies before they fit (import torch lazily where it is
  imported today; set it once per call). An environment variable that caps lower (`OMP_NUM_THREADS`,
  `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`) keeps its effect: `threadpool_limits` lowers, never raises, beyond what
  the libraries already have (state in the docstring what happens when the variable is set higher than the field).
- The digest / report / analyze paths that fit models (`analyze.report`'s importance, T17.10's mutual information)
  are not strategies; leave them (the importance runs with `n_jobs=1` already).

## 2. N-78: the extraction process counts

- The envelope (`blocks/doctor._envelope_check`, `engine.workers_for`, `blocks/evaluate.plan_shape`, the doctor's
  header line `peak_threads=`) counts one more thread per concurrent testbench job for the process that extracts the
  metrics (OCEAN) beside Spectre: per job `threads_per_run + 1` for a spec with testbenches; the device (EMX) chain
  is as it was (`em.threads`). The strategy's threads are not added to the peak: the strategy runs between batches,
  when no simulation of its run does; the plan line says so once (`strategy N threads between batches`).
- `workers_for`: the slots a host gives are computed from the same per-job figure, so `parallel_jobs` is trimmed to
  fit `(threads_per_run + 1) × jobs ≤ max_threads` (and memory as before). A spec that fitted before and no longer
  does is refused with the message naming the +1 ("each testbench job is counted as threads_per_run + 1 threads: the
  metric extraction runs beside the simulator").
- The doctor's `envelope` check line and the header print the per-job figure explicitly, e.g.
  `10 jobs × (1 + 1) threads → 20 threads`.
- `ic-opt run ... --plan` output changes accordingly (the skill's step 3 text: update the sentence on `jobs × threads`).

## 3. Documents

- `skills/ic-opt/SKILL.md`: step 3 (the plan line's envelope sentence), step 4 (the `simulator.strategy_threads`
  field in the "never exceed the host's entry" sentence), the spec cheat-sheet.
- `skills/author-spec/SKILL.md`: the `simulator` fields list, if it has one.
- `README.md`: the `simulator` block description, if it lists the resource fields.

## 4. Tests

- `tests/ic_opt/test_threads.py` (new): the field's default, dump and fingerprint behaviour; `suggest` runs the
  strategy under `threadpool_limits(n)` (patch `threadpoolctl.threadpool_limits` or read `threadpool_info()` inside a
  fake strategy to see the limit); the torch strategies call `torch.set_num_threads(n)` (patch it); the envelope
  arithmetic (`_envelope_check`, `workers_for`, `plan_shape`) with `threads_per_run + 1`; the refusal message; a spec
  with devices keeps the EMX accounting.
- Update the tests this changes (`test_doctor*.py`, `test_engine.py`, `test_schedule.py`'s `plan_shape` assertions,
  any golden plan text). Targeted run: those plus `test_optimize.py`, then `ruff check src tests`.

## 5. Working rules for the coder

- Branch `t17-12-threads` in its own worktree (`git worktree add /home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/t17-12-threads -b t17-12-threads main`);
  commits on that branch only; never touch `main`, never push.
- Another agent works at the same time on `t17-11-saturation` (the `Metric` model of `spec.py`, `sim/ocean.py`,
  `stages/spectre_chain.py`, the skills' metric sections). In `spec.py` touch the `Simulator` model and
  `_NOT_PROBLEM` only; do not edit those other files.
- Python: `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python` with `PYTHONPATH=<worktree>/src`
  (check `import ic_opt` resolves to the worktree); never `uv run`, `uv sync`, `pip`. No simulator runs, no network.
  Tests count as machine load: run the targeted set once per commit, with `OMP_NUM_THREADS=1`.
- Two commits (N-73, N-78), each message saying what and why; trailer
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Hand back: branch and commits; the verbatim test and ruff output (last 30 lines); what the specification left open
  and how it was read (a numbered list); what could not be done and why.

## 6. Record (2026-09-30)

Implemented by the coding subagent on `t17-12-threads` (`02360a6` N-73, `b1eb72b` N-78), merged `14eddf9` after
T17.11 (two text conflicts in `README.md` and `skills/ic-opt/SKILL.md`, both sides kept); targeted tests 273 passed,
ruff clean. How the open points were read (the coder's list, kept here):

- `threadpool_limits` sets each pool's size rather than lowering it: with `OMP_NUM_THREADS=1` and `limits=4` OpenBLAS
  went to 4. So the limit applied is `blocks.optimize.strategy_threads(spec)`: the field, or the smaller environment
  cap read as `library.query.omp_cap` reads it; an outer `threadpool_limits` of a caller is not detected.
- The limit wraps the strategy's `propose` attempts inside `suggest`; start rows, advice rows and the random filler
  are outside it. TuRBO gets the same capped figure as `threads=` and calls `torch.set_num_threads` once per call
  before it fits (process-wide, set again on every call; torch loaded inside the limit block still sized itself to
  every core when measured, hence the explicit call). The vendored OpenBox never imports torch.
- The +1: `site.EXTRACTION_THREADS` is added by `engine.extraction_threads(stage)` to every child stage whose unit is a
  testbench and that simulates (the engine knows nothing of Spectre); an EMX job stays `em.threads`; the fake
  testbench stages of the tests count it too. `Run.jobs` in `recipe.py` counts it as well (README and the skill say
  `run.jobs` trims to fit). A testbench job that fits alone but not with the +1 is refused with a message naming it.
- The run header (`plan_line`) reads `host=local jobs=10 × (1 + 1) threads → peak_threads=20 (max_threads 96),
  strategy 1 threads between batches, ...`; it prints the field's value, for recipes without a strategy too.
- The skill has no spec cheat-sheet: one line went into its "Recipe cheatsheet", the field into author-spec's
  `simulator` list. `strategy_threads` is not checked against any host entry (the field is `ge=1` only). A spec with
  `strategy_threads` other than 1 has no 0.2.0 fingerprint (as every field added after 0.2.0).
- Not done: a measurement of real core use after the change (no simulator run); `Spec.problem()`'s docstring does
  not name the new field.
