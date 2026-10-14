# N-120: a column with too few usable rows is reported, not fitted into a crash (2026-10-14)

Found 2026-10-14 while pre-fitting the tables (`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/prewarm_20261014/`):
on `xfm_ms_m10` and `xfm_ms_ap` the columns `Lp`, `Ls`, `Qp`, `Qs`, `k` at 140 and 170 GHz have 0 usable rows and at 112 GHz 2
(`xfm_ms_m10`) and 4 (`xfm_ms_ap`) -- the multi-turn secondary resonates low. Asking the library to fit them raises from deep inside
the fit: a direct model with 0 rows gets `ValueError: expected [N, 7] inputs` (`StratumGP.fit`); with 2 rows the 5 x 20 % hold-out
rounds its test fold to 0 rows and scikit-learn raises `Found array with 0 sample(s)`; a composed curve raises "no row has a positive
curve value ...". Consequences checked on 2026-10-14: `lib.query` without naming the columns on `xfm_ms_m10` raises; the call
`lib_signoff` makes first, `lib.models(stratum, every column)`, raises -- before any EMX; N-114's family request turns it into a
table's `cannot_take` with scikit-learn's text. User decision 2026-10-14: fix it.

## 1. What changes

- One place decides whether a column can have a model: enough usable rows for its fit and for its calibration -- the hold-out's test
  fold non-empty after the rows whose turns level has no sub-model are set aside, and at least one turns level with a sub-model
  where the model splits by level. Read `gp.StratumGP` (`MIN_NT_SAMPLES`, `nt_mode`, `available`) and `gp.holdout` / `split` and
  `composed` to state the exact condition in the record; it must hold for every column fitted today (so none of them changes) and
  fail for every column that raises today.
- `Library.models` skips such a column (no fit, no worker) and its answer is a new status `no_model` with `reason` ("<n> usable rows:
  too few for a model of this column") and the row count; `query.query` with every column answers the others as before;
  `lib.cached` lists such a column as `too few rows (<n>)` instead of `none`; `suggest` / `region` / `densify` / `family` given a
  target or objective on such a column refuse it with that reason, before anything is fitted (the family request: that table's
  `cannot_take` with the clear reason); `lib_signoff` skips the column in its report.
- **Do not change `src/ic_opt/library/gp.py` or `composed.py`**: their source is part of every cached model's key; a change would
  refit every model of every table. The check lives in `query.py` (and where the callers need it).

## 2. Tests (fakes, no EMX)

A fake stratum with one column of 0 usable rows, one of 2, one of 4 whose level has no sub-model, and a normal one: `models(every
column)` fits only the normal one; `query` answers `no_model` for the others; `cached` reports them; `suggest` with a target on one
of them refuses with the reason; nothing changes for strata without such columns (existing tests unchanged).

## 3. Real check (read-only; models cached, nothing fitted)

Library `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28_v2`,
`IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles`, `OMP_NUM_THREADS=4`: on
`xfm_ms_m10` and `xfm_ms_ap`, `lib.cached` lists the 15 columns; `query` at a point that is no row, every column, answers (the 15
`no_model`, the others as on `main`, bit for bit); `lib.models(stratum, every column)` returns without fitting; `lib_signoff --plan`
on an `xfm_ms_m10` candidate (a copy of `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28_signoff/` with a fresh project
name, under `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n120_few_rows/`) gets through its plan. Stop and report if
anything starts fitting.

## 4. Documents

`docs/em/library.md` (the statuses and `cached`), the ic-opt skill where it lists statuses.

## 5. Working rules for the coder

Branch `n120-few-rows-columns` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n120-few-rows-columns` (from `main`; `git
merge --ff-only main` first). N-116 is developed at the same time (domain, query, suggest, region, family). `PYTHONPATH=<worktree>/src`
with `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`; never `uv run` or `uv sync`; `git -C <worktree>`
and absolute paths. Targeted tests (query, suggest, region, family, densify, signoff, cache, docs, CLI) plus `ruff check src tests`;
pytest with `OMP_NUM_THREADS=2`, no `-n`; at most 8 threads. The library is read-only. One commit for the fix with tests and
documents, one for the `## Record` section below. No merge, no push, no edit of `docs/refactor/BACKLOG_CN.md`. Messages in the
repository's style, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Record

(to be written by the coder: base commit, the exact condition and why it keeps every fitted column, files and why, tests, the real
check, deviations)
