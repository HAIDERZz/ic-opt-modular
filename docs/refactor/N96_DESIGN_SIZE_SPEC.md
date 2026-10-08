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
2. **The incremental form is documented and equal to the one-shot form.** With the record, `optimize budget=10` then
   `budget=40` still gives a 5-point design (its first call had 10). The agent's loop needs the one-shot size, so the
   skill's step 7 and the README's "Recipes" say: to advise between batches, give the first call the run's whole budget
   in `initial_trials` terms or -- simpler -- run the first call with the full budget and `batch=10` ... **No**: the
   cleaner rule, and the one to implement, is that `optimize` takes `total=<N>` (an optional parameter: the budget the
   run is meant to reach, default the call's `budget`): the design size is computed from `total`, recorded on the first
   call, and the plan line says `initial design <n> points (sized for a budget of <total>)`. The harness of the
   comparison then becomes `optimize budget=10 total=40`, `budget=20 total=40`, ...; and `signoff`'s search (which calls
   the block with `budget=40` in one go) is unchanged. `total` below `budget` is refused.
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
