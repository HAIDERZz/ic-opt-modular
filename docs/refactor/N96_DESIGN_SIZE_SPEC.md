# N-96 — a run continued in increments proposes what one call with the final budget proposes

Status: specification (2026-10-08), for the coding subagent. Decision behind it: the user's 2026-10-08 "进行你提议的两个小事"
after the language-model comparison (`BACKLOG_CN.md` 0.14), whose harness had to pass `initial_trials=20` by hand.

The defect. `blocks.optimize._initial_design` sizes `metric_gp`'s initial design as `min(max(2 x active variables, 8),
20, budget // 2)`, so a run advanced in increments (`optimize budget=10`, then `20`, `30`, `40` -- the way an agent advises
between batches, `docs/refactor/T17_1_5_ADVICE_SPEC.md` and the skill's step 7) gets a 5-point design on its first call
and never the 20-point design one call with `budget=40` gets: the two runs diverge from the second batch on. Measured on
the mixer library platform (`ic-opt-accept/llm_compare/equiv_s0` against `A_s0`: 10 of 40 points in common; with
`initial_trials=20` passed by hand, `equiv2_s0`: all 40 identical). OpenBox's design (`initial_design_size`, `min(2 x
variables, budget // 2)`) has the same dependence.

Read before writing: `src/ic_opt/blocks/optimize.py` (`optimize`, `_initial_design`, `surrogate_points`, `_print_design`,
the loop), `src/ic_opt/suggesters/metric_gp/__init__.py` (`initial_design_size`, `MetricGpSuggester.__init__`, how the
design is drawn), `src/ic_opt/suggesters/openbox.py` (`initial_design_size`), `src/ic_opt/suggesters/base.py` (the Sobol
prefix), `tests/ic_opt/test_optimize.py`, `test_metric_gp.py` (the pinned proposals), `test_replay_parity.py`,
`test_space_tables.py`, `skills/ic-opt/SKILL.md` (steps 6 and 7), `README.md` ("Recipes").

## 1. What changes

1. **The initial design's size is recorded by the first call of a step and kept by every later call.** `opt.optimize`
   computes the size as today on the call that finds the step empty (no observation of this problem in this step), from
   that call's budget, and writes it to the store: `.icopt/steps.json`, `{"<step>": {"initial_design": <n>, "budget":
   <that call's budget>}}` (the step id as `step=` names it; a later call of the same step reads its entry and passes
   the recorded size to the strategy as `initial_trials` when the call gives none). A call that gives `initial_trials`
   records that. Not in any fingerprint; a store without the file behaves as today (the size is recomputed from the
   current call's budget, as it always was), so every recorded run and replay reads unchanged.
2. **`optimize` takes `total=<N>`, the budget the run is meant to reach** (optional; default: the call's `budget`;
   below `budget` refused). The design size is computed from `total`, recorded on the first call (item 1), and the plan
   line says `initial design <n> points (sized for a budget of <total>)`. An agent advising between batches runs
   `optimize budget=10 total=40`, `budget=20 total=40`, ... and gets exactly the one-shot run's points; `signoff`'s
   search, which calls the block with the whole budget in one go, is unchanged.
3. **The warning line** `metric_gp initial design N points ...: the model proposes 0 of the M new points -- WARNING`
   (`_print_design`) is printed as a plain line, not a warning, when the call's new points are all start rows or all
   inside the design (nothing for the model to propose yet is not a fault); the WARNING form stays for a budget that
   ends inside the design (the message's original case).

## 2. Tests (`tests/ic_opt/test_optimize.py`, fakes)

1. A step advanced in four calls (`budget` 10, 20, 30, 40, each with `total=40`) proposes exactly the points one call
   with `budget=40` proposes, in the same batches (compare params per batch; metric_gp and, with a fake EM device,
   openbox_gp_eic).
2. Without `total`, the first call records its own budget's size and later calls keep it (a 10-then-40 run keeps the
   5-point design: today's behaviour, now stable across the continuation instead of changing with each budget).
3. `steps.json` absent (a store written before this change): the size is the current call's, as today; the pinned
   proposals of `test_metric_gp.py` and `test_space_tables.py` and the replay-parity tests pass unchanged.
4. `total < budget` refused; `initial_trials` given: recorded and kept.
5. The plan line and the warning rule of 1.3.
6. `ruff check src tests` clean.

## 3. Documents

`skills/ic-opt/SKILL.md` step 7 (the incremental loop: `optimize PROJECT budget=<k x batch> total=<N> ...`, one line);
`README.md` "Recipes" (`total`); the comparison harness is outside the repository and is not touched. Append `## 4.
Record` here.

## 4. Working rules for the coder

Branch `n96-design-size` in the worktree `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n96-design-size` (from main;
`git merge --ff-only main` first). Python `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python` with
`PYTHONPATH=<worktree>/src`; never `uv run`, `uv sync` or pip. Run `tests/ic_opt/test_optimize.py`, `test_metric_gp.py`,
`test_space_tables.py`, `test_replay_parity.py` (with `IC_OPT_RECORDED_RUNS=/home/zzchen/.ic-opt/remote_runs/zzchen@10.113.216.131/802f8b444b991da2:/home/zzchen/remote_opt/Mixer_CS_validation_second_batch_20260810`),
`test_cli_recipes.py`, `test_signoff_tighten.py`, `test_library_device_run.py`, and `ruff check src tests`; all clean
before committing. No simulator; nothing under `ic-opt-accept` or `ic-opt-library` touched; nothing private. Do not edit
`docs/refactor/BACKLOG_CN.md`. Use a private subdirectory of your scratchpad for any helper file (the scratchpad is
shared with other agents). Commit style of the repository, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
Do not merge or push.

## 4. Record

Done 2026-10-08 by the coding subagent, branch `n96-design-size` (not merged, not pushed). Commit `d727bab`
(implementation, tests, README, skill) and this record.

What was done.
- `blocks/optimize.py`: `optimize(..., total=None)`. `done` (this problem's observations in the step) is counted before
  the design is sized. On a call with `done == 0` the size is computed as before from `total` (default `budget`); on a
  later call without `initial_trials` the entry of `.icopt/steps.json` is read (`_recorded_design`) and its size handed
  to the strategy. The entry is written (`_record_design`, whole file replaced at once) under the run lock, before the
  first batch, on the step's first call or whenever `initial_trials` is given; `--plan` writes nothing. Only strategies
  whose design ic-opt serves (`metric_gp`, `openbox_*`) read or write it: `turbo`, `sobol`, `random` are untouched and
  never receive a recorded `initial_trials`. `total < budget` raises `ValueError` before anything runs, `--plan` too.
- The design line: `initial design n points (sized for a budget of <total>)` on a first call with `total`;
  `(recorded by this step's first call, budget <b>)` on a later call that keeps the size; with start points the two
  share one parenthesis (`(sized for a budget of 40; 1 start point first)`). No note when `initial_trials` is given or
  when neither `total` nor a record is involved (the line is then the one of before).
- 1.3: a call whose model proposes none of its new points prints a plain line when they are all start points
  (`... (all of them start points)`) or when the model proposes some of the points left to the run's total
  (`... (none yet: it proposes K of the T points left to the run's total)`); the WARNING form stays otherwise.
- `recipes/optimize.py` takes `total` and passes it on (checked by hand through `ic-opt run optimize ... --plan
  budget=10 total=40`); `store.py`'s docstring lists `steps.json`. README "Recipes" and `skills/ic-opt/SKILL.md` step 7
  name `total`; the skill's cheatsheet line also gained `[total=N]`.

Tests. Before: 200 passed (`test_optimize.py`, `test_metric_gp.py`, `test_space_tables.py`, `test_replay_parity.py` with
the two recorded runs, `test_cli_recipes.py`, `test_signoff_tighten.py`, `test_library_device_run.py`). After: 206 passed
(six new tests in `test_optimize.py`), no skips; `ruff check src tests` clean. Also run, not required by section 4,
because they assert design lines or continue runs: `test_advice.py`, `test_digest.py`, `test_log_scale.py`,
`test_multi_corner.py`, `test_schedule.py`, 116 passed.

Deviations and readings.
- One existing test changed: `test_openbox_marks_initial_design_points_and_says_when_the_surrogate_never_proposes` ran
  budget 4 then 8 without `initial_trials` and expected the second call to resize the design to 4; it now expects the
  recorded 2 (`(recorded by this step's first call, budget 4)`), the behaviour item 1 asks for. Its origins are unchanged.
- The entry's `budget` is the budget the size was computed for (`total` when given, else the call's `budget`), not the
  call's `budget`: with `total` the size depends on `total` only. A call that gives `initial_trials` records the same way.
- Item 1.3 reads two ways without `total` (a budget whose new points are all inside the design is both "all inside the
  design" and "a budget that ends inside the design"). Implemented: plain when all are start points, or when the
  points up to the run's total include some of the model's; WARNING otherwise. Without `total` only the all-start case
  changes (it used to warn). With `total`, a call that proposes none because no point has succeeded yet (not because
  its points are inside the design) is also plain, as long as the model proposes later in the total.
- Tests on the six-variable specs instead of the mixer library platform: `metric_gp` serves its whole first batch from
  the design while nothing is scored, and the Sobol sequence is prefix-stable, so a design of 5 against 8 (two variables,
  batch 10) proposes the same points; the test needed designs above the first batch (12) to tell the two runs apart,
  and it checks that they do part without `total`. The fake EM device spec has four circuit variables plus the device's
  two, built like `test_em_circuit.em_circuit_spec`.

Not foreseen by the specification.
- `ic-opt run optimize PROJECT initial_trials=N`, which the skill's cheatsheet and README describe, raises a TypeError:
  `recipes/optimize.py`'s `main` has no `initial_trials` parameter (nor `**kwargs`); only `ic-opt call opt.optimize` and
  custom recipes reach the keyword. Not changed here (pre-existing; the README paragraph added calls it a keyword of
  `opt.optimize`).
- `total < budget` through `ic-opt run` ends in a Python traceback, like every other `ValueError` the block raises
  there (the command catches only `SiteError`, `EnvelopeError`, `LinkError`).
- `steps.json` is keyed by step name only, as specified. A step that holds no observation of the current problem (the
  spec changed) counts as empty and its entry is overwritten; two problems sharing one step name in one store would
  share the entry only while both hold points in it.
- `coarse_to_fine` and `signoff` take no `total`; a `signoff` re-run with a larger `budget` now keeps its search step's
  recorded size, like any continued step.
