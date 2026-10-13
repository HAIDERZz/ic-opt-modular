# N-101, N-102, N-103: three small items from the advising sessions (2026-10-13)

The 24 sessions of agents advising a run between its batches (`docs/refactor/BACKLOG_CN.md` 0.14, the comparison of
2026-10-08 and the designer's-notes check of 2026-10-09) left three small items. The user decided on 2026-10-13: N-101
and N-103 are product changes, N-102 is one sentence in the skill. Nothing else changes. ic-opt still calls no language
model.

## N-101 `points.md` carries the parameters

Today `digest.points_markdown` (`src/ic_opt/digest.py`, N-97 F7) writes one row per point with obs id, step, origin,
status, objective, every metric at its worst corner and per library device the row the point took. It has no parameter
column: the N-97 specification said the obs id leads to them. Three sessions found that this sends the agent back to
`observations.jsonl` to learn which design a row is -- the very thing the table was made to spare.

1. After the `status` column and before `objective`, one column per spec variable, in `spec.variables` order, headed by
   the variable's name, the cell the point's grid text as the store holds it (`o.params[name]`); a point whose `params`
   lack the variable (a row an older spec wrote) prints `—`. Everything after (objective, metrics, library rows) as before.
2. The table's rule line (`_rule`) counts the new columns. The introductory sentence says "the parameters" where it
   lists the columns. `POINTS_LIMIT` and the "newest n of m" line are unchanged.
3. `digest.md`'s line that names `points.md` is unchanged. `skills/ic-opt/SKILL.md` step 5 (the sentence beginning
   "`--points` also writes `reports/points.md`") lists the parameters among the columns; step 7 item 2's bullet on
   `--points` says "per-point parameters and metrics". The README does not describe `points.md`'s columns; leave it.
4. Tests (`tests/ic_opt/test_digest.py`, the F7 test `--points`): the header holds every variable name of the spec in
   order between `status` and `objective`; one row's parameter cells equal that observation's `params`; a row written
   without one variable prints `—` for it. `tests/ic_opt/test_digest_leak.py` still passes as it is (parameters are grid
   texts: no issue text, no `.icopt` path).

## N-102 The skill: revoke an advice that holds only start rows once they ran

An advice has a period, from its adoption to the row that ended it, and the digest's advice table counts the period's
points on two sides, under the advice (`@<id>`, `advice:<id>`) and the others. An advice that holds only start rows acts
once -- when its rows are evaluated it influences nothing after -- but its period goes on until it is revoked or
replaced, so every later point is counted as an "other" of an advice that did nothing, and its verdict says nothing.

1. `skills/ic-opt/SKILL.md`, step 7, item 3, the bullet "**Review the advice in effect after every batch, before
   anything else**": add one sentence at its end: "An advice that holds only start rows acts once: when its rows have
   run (the digest's advice table lists each with its status), revoke it (`--revoke <id> --reason "its rows ran"`) or
   replace it, or its period goes on counting every later point as the other side of an advice that no longer does
   anything."
2. No code, no test (no test reads the `ic-opt` skill's text; `test_skill_author_spec.py` reads the author-spec skill
   only). The `advise` command, `advice.jsonl`, `advice_rules.in_effect` and the digest are unchanged.

## N-103 A start file's duplicate rows are said

`optimize` and `signoff` take `start=FILE`, a JSON list of parameter rows; `opt.optimize` validates them
(`space.points_from_params`) and `suggest` proposes each distinct point once (`taken` holds the keys), so a duplicate
row costs nothing and is never evaluated twice -- but nothing says so, and the comparison's platform ran for days with
a 5-row file holding 2 duplicates that no one noticed.

1. In `opt.optimize` (`src/ic_opt/blocks/optimize.py`), where the start rows are turned into points (`starts = ...`):
   when two or more of the `start` rows (the rows handed to the block: the file's, not the current design) share a
   point key, print one line, under `--plan` too:
   `[optimize] step='search@tt': start rows: 5 given, 2 duplicate rows dropped (3 distinct)` (the `[plan] opt.optimize
   step=...:` prefix under `--plan`, as the block's other lines). No line when every row is distinct, and none when no
   start rows are given. Duplicates are counted by `Point.key` (the grid text after validation), so two spellings of one
   grid point count as duplicates. The behaviour of the run is unchanged: the same points are proposed and evaluated as
   before (the distinct ones, once); the design line's "n start points first" keeps its meaning (distinct rows not yet
   evaluated).
2. Tests (`tests/ic_opt/test_optimize.py`, fakes): a `start` list with one duplicated row prints the line once with the
   right counts, under `--plan` and in the run, and the run evaluates the same points, in the same order, as the same
   list without the duplicate; a list without duplicates prints no such line; no `start` prints none.
3. `skills/ic-opt/SKILL.md` and the README: no change (the line explains itself).

## Working rules for the coder

- Branch `n101-advising-small-items` in the worktree `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n101-advising-small-items`
  (made from `main` by the coordinator; start with `git merge --ff-only main` to confirm the base). The main checkout is
  the editable install: run the tests with `PYTHONPATH=<worktree>/src` and the main tree's
  `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python -m pytest`.
- One commit per item, in the order N-101, N-102, N-103, each with its tests and documents; do not merge, do not push,
  do not touch `docs/refactor/BACKLOG_CN.md`. Commit messages in the repository's style (`feat(digest): ... (N-101)`).
- Run before each commit: the suites the item touches (`tests/ic_opt/test_digest.py`, `test_digest_leak.py`,
  `test_digest_library.py` for N-101; `test_optimize.py`, `test_cli_recipes.py`, `test_signoff_total.py`,
  `test_advice.py` for N-103) and `ruff check src tests`; before the last commit the whole of those with
  `test_replay_parity.py` under `IC_OPT_RECORDED_RUNS=/home/zzchen/.ic-opt/remote_runs/zzchen@10.113.216.131/802f8b444b991da2:/home/zzchen/remote_opt/Mixer_CS_validation_second_batch_20260810`
  (the recorded runs must replay unchanged: N-103 adds a line, never a point).
- No simulator, no EMX, no language model; the fakes of `tests/ic_opt/fakes.py` only. Keep protective code out; a
  root-cause fix over a tolerance (CONTRIBUTING).
- Write the record as section `## Record` at the end of this file (what was done, tests before and after, deviations
  and readings, anything the specification did not foresee), in a final commit `docs(N-101..103): record`.
