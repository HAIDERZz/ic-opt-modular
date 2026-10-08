# N-97 — the digest and the report after a re-check: what the agents of the comparison found misleading

Status: specification (2026-10-08), for the coding subagent. Source: the seven findings F1-F7 of `BACKLOG_CN.md` 0.14,
raised independently by the 12 agent sessions of the language-model comparison (`ic-opt-accept/llm_compare/RESULT_CN.md`,
read-only). The user's decision (2026-10-08): do them.

Read before writing: `src/ic_opt/digest.py` (`DIGEST_VERSION`, `_best_of`, the counts, the advice table, the batches
table, the corners section), `src/ic_opt/blocks/analyze.py` (`report`: "Best observed", "Top feasible candidates",
"Corners"), `src/ic_opt/recipes/signoff.py` (`_check_step`, `signoff_rounds.json`), `src/ic_opt/advice.py` (`in_effect`,
the advice table's periods), `src/ic_opt/blocks/evaluate.py` / `sim/corner.py` (the `metric ... failed` lines, `worst_metrics`),
`src/ic_opt/blocks/optimize.py` (`_print_design`, the "no current design" line), `tests/ic_opt/test_digest*.py`,
`test_blocks.py` (the recorded-run report), `test_signoff_tighten.py`, `docs/refactor/T17_10_DIGEST_SPEC.md`,
`skills/ic-opt/SKILL.md` (step 5 "Read").

## 1. What changes

- **F1 The digest reports the re-check.** When the store holds re-check observations (steps `signoff`, `signoff#k`),
  the digest's "How far the run is" names, after the search's best, **the best point feasible at every corner** (its
  obs id, the search point it re-checks, its worst-corner objective, and the corner that binds each failing or
  weakest metric), and a "Re-check" section lists every re-checked point: search point, tt objective, worst-corner
  objective, feasible yes/no, the first failing corner and metric. `digest.json` gains `recheck` (the same, as data).
  The batches table does not count re-check points as a batch (F1's "batch 5 / 45 points"). `DIGEST_VERSION` 5;
  every entry of version 4 stays.
- **F2 report.md labels the corner.** "Best observed" and "Top feasible candidates" give, per metric, the value at the
  metric's worst corner and name that corner (`NF_3G 10.27 dB (ss)`); a one-corner run prints as today.
- **F3 The advice table's period counts only the step's own points.** Re-check points (another step) are neither "under"
  nor "others" of an advice; the share is over the search step's points in the period.
- **F4 The `metric ... failed` line does not say "fix the expression"** unless every point of the batch lost the metric;
  otherwise it says how many points gave no value and that the digest's "no value" split shows where
  (`metric P1dB gave no value on 2 of 10 points (no_value:nil); the digest's "no value" split says where`).
- **F5 The design line is a plain line, not a WARNING, when the batch's new points are all start rows or all inside the
  initial design** (coordinated with N-96, which changes the same function: if N-96 is merged first, take its form;
  otherwise implement it here identically).
- **F6 The "no current design" line names the case:** for a spec whose devices are library rows, `no current design:
  the spec's devices come from a library table (their variables have no exported value)`; the EM wording stays for EM
  devices.
- **F7 A per-point table.** `ic-opt digest PROJECT --points` writes `.icopt/reports/points.md` (and prints its path):
  one row per observation of the problem -- obs id, step, origin, status, objective, every metric (worst corner when
  several), the device row for a library device (part/obs id) -- at most 500 rows (the newest; a note says when
  truncated). `digest.md` names the file in "How far the run is". Not in the JSON.

## 2. Tests

`tests/ic_opt/test_digest.py` (a store with search and re-check observations at three corners: F1, F3; the batches
table; version 5), `test_blocks.py` (F2 on the recorded run: the corner labels; the existing assertions kept),
`test_optimize.py` or `test_evaluate*.py` (F4, F5, F6 lines), a test for `--points` (F7: rows, truncation note, the
library row column with the fake library of `test_library_device_run.py`). `ruff check src tests` clean.

## 3. Documents

`skills/ic-opt/SKILL.md` step 5 (the re-check section, `--points`); `docs/refactor/T17_10_DIGEST_SPEC.md` gets a version-5
note; append `## 4. Record` here.

## 4. Working rules for the coder

Branch `n97-digest-recheck` in the worktree `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n97-digest-recheck` (from
main; `git merge --ff-only main` first). Python `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`
with `PYTHONPATH=<worktree>/src`; never `uv run`, `uv sync` or pip. Run `tests/ic_opt/test_digest*.py`, `test_blocks.py`
(with `IC_OPT_RECORDED_RUNS=/home/zzchen/.ic-opt/remote_runs/zzchen@10.113.216.131/802f8b444b991da2:/home/zzchen/remote_opt/Mixer_CS_validation_second_batch_20260810`),
`test_optimize.py`, `test_signoff_tighten.py`, `test_library_device_run.py`, `test_cli_recipes.py`, `test_advice.py`, and
`ruff check src tests`; all clean before committing. No simulator; nothing under `ic-opt-accept` or `ic-opt-library`
touched (you may read `ic-opt-accept/llm_compare/*/.icopt/reports/digest.md` to see the current output); nothing private.
Do not edit `docs/refactor/BACKLOG_CN.md`. Use a private subdirectory of your scratchpad for helper files. Commit style
of the repository, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not merge or push.

## 4. Record (2026-10-08)

Implemented by the coding subagent on `n97-digest-recheck` (from main `2f90073`, then rebased onto `n96-design-size`
`71ddb0b` at the coordinator's request), not merged, not pushed:

- `81cd180` feat(report) -- F2: `sim.corner.worst_values` (each metric at its worst over the corners a point ran, with
  that corner; constrained metrics as the verdict judges them, the others at the objective's worst corner,
  `sim.corner.worst_objective`; no corner where every corner gives the same value) used by "Best observed" and "Top
  feasible candidates": `NF_3G=10.27 dB (ss)`, a one-corner run prints as before (the recorded run's assertions kept, no
  label). `worst_metrics` (what the models see) unchanged.
- `86670f5` fix(optimize, evaluate) -- F4, F6 (F5 is N-96's rule, below).
- `140be01` feat(digest) -- version 5: F1 (`recheck`, the line under the search's best, "Re-check at every corner",
  re-check points out of `progress` and the batches), F3 (an advice's others: its own step's points only), F7
  (`digest.points_markdown`, `ic-opt digest --points`, `analyze.digest(points=True)`); SKILL.md step 5;
  `T17_10_DIGEST_SPEC.md` section 7.

Tests. The suites of section 4 (`test_digest*.py`, `test_blocks.py` with `IC_OPT_RECORDED_RUNS`, `test_optimize.py`,
`test_signoff_tighten.py`, `test_library_device_run.py`, `test_cli_recipes.py`, `test_advice.py`): before the rebase 176
passed on main and 184 on this branch; after it, 183 passed on `n96-design-size` and 190 on this branch (7 new: four in
`test_digest.py`, one each in `test_digest_library.py`, `test_blocks.py` and `test_optimize.py`). Fifteen further suites
that read the changed code (`test_engine`, `test_saturation_margin`, `test_metric_gp`, `test_replay_parity`,
`test_em_circuit`, `test_space_tables`, `test_multi_corner`, `test_schedule`, ...): 286 passed, 3 skipped before and after
(the skips need EM recordings). `ruff check src tests` clean. Read by eye: the digest and `points.md` of
`ic-opt-accept/llm_compare/B_opus_s1` and `A_s1` computed in memory (nothing written there): B_opus_s1's best at every
corner `obs_0044` -0.7475 (ff), NF_3G 10.27 dB at ss; its advice a1 / a2 back to 80% from the advice.

Deviations and readings:

- **F5 is N-96's rule.** This branch first made the line plain only for start rows (without `total=`, new points all
  inside the design were the warning's own case). Rebased onto `n96-design-size`, its `_print_design` is kept as it is:
  a plain line when the new points are all start points (`(all of them start points)`) or when the model proposes some
  of the points up to the run's `total` (`(none yet: it proposes K of the N points left to the run's total)`); the
  WARNING stays for a run that ends inside the design. N-97's own F5 code and test were dropped; N-96's test in
  `test_optimize.py` pins the start-point case.
- F4's wording follows the example: one reason is named without its count (`(no_value:nil)`), several with counts.
  Updating the line changed two existing assertions (`test_engine.py`, `test_saturation_margin.py`: one point of two);
  `test_library_device_run.py` asserted F6's old wording and now asserts the new one.
- F1: `progress` and `advice` leave the re-check out; every other entry (`counts`, `constraints`, `variables`,
  `failures`, `suggested_ranges`, `strategy`, `operating_points`, `library`) counts every point as in version 4. A
  re-checked point's "objective at its worst corner" is its `fom` when feasible, else the objective at the worst corner
  among those it ran with every objective metric (a point stopped early: the corners it reached); ties name the first
  corner (on the fake signoff, G equal at tt and ss reads "(tt)"). Re-check steps are recognised by name
  (`signoff`, `signoff#<k>`, `digest.RECHECK`, the recipe's `_check_step`), not by reading `signoff_rounds.json`
  (the digest stays pure). A later round's search point (another fingerprint) is named by its id with no objective.
- F3: an advice's own step is the step of its points, else of the first point of its period; points of any other step
  (a re-check, a user `evaluate`) are neither under it nor others. `improved_best` is over the search's points.
- F7: the table has the columns the specification lists -- no parameters (the obs id leads to them). The path is printed
  after the digest; with `--json` on stderr, so stdout stays one JSON document. `digest.md` names the file as
  `reports/points.md` (not `.icopt/...`: the library leak test forbids `.icopt` in digest text), or says how to write it.
- Beyond section 3: `skills/ic-opt/SKILL.md` steps 4 and the troubleshooting line and `skills/author-spec/SKILL.md` say
  which of F4's two lines asks for a fix; `test_digest_leak.py` also checks `points.md`.

Not foreseen by the specification: the comparison's "design 20 points (4 start points first): 0 of the 10 new points
-- WARNING" lines came from the harness passing `initial_trials=20` with `budget=10` per call -- N-96's case, which
`optimize total=` now covers.
