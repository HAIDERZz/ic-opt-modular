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

**Branch and base.** `n120-few-rows-columns`, from `main` at 3542196 (`git merge --ff-only main`: already up to date). The
fix, its tests and documents: 07938a9; this record: the commit after it. `gp.py` and `composed.py` are unchanged
(`git diff 3542196 07938a9 -- src/ic_opt/library/gp.py src/ic_opt/library/composed.py` is empty), so every cached model
keeps its key; no file under the library was written. Evidence (scripts, their outputs and logs):
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n120_few_rows/`. `main` has since moved to 58e6924 (N-119: the
command line, the lock, the Spectre chain, the ic-opt skill's lines 21 and 128-138): `git merge-tree` with it reports no
conflict, and the merged tree passes `test_library_few_rows.py`, `test_library_signoff.py`, `test_library_family.py`,
`test_library_docs.py` and `test_cli_recipes.py` (78 passed). N-116, not committed when this was written, edits
`query.py`, `region.py`, `densify.py`, `stage.py`, the query block, `docs/em/library.md` and the ic-opt skill too: the
skill's library paragraph is one line both change, so that merge will be by hand.

**The condition** (`Library.no_model`; `query.fits` for a direct column, `query.composed_fits` for a composed curve). A column
can have a model exactly when its fit and -- in a library that calibrates, the default -- its 5 x 20 % hold-out complete on
its usable rows: the condition is the negation of where they raise, read off `gp.StratumGP`, `gp.holdout` / `gp.split` and
`composed.holdout_fold`.

- A direct column with n usable rows: n >= 1 (without a row `Dataset.matrix` is 1-D and `StratumGP.fit` raises "expected
  [N, 7] inputs"; the domain guard needs a row too). A joint model -- a `feature_map`, or a stratum without a turns dim --
  needs besides a held-out row in every fold of `gp.split(n, seed)`, seeds 0-4: round(0.2 n) >= 1, so n >= 3, because
  `gp.holdout` predicts the held-out rows through scikit-learn, which raises on 0 samples. A per-level model (`per_nt`)
  needs nothing more: `fit` skips a level below `MIN_NT_SAMPLES` (25) and `holdout` predicts only the rows `available`
  admits, level by level, so neither a fold whose held-out rows no sub-model answers nor a model without any sub-model
  raises.
- A composed curve (`ratio` / `resonance`): its parts -- the base scalar and, for `resonance`, SRF -- can have a model (the
  reason then names the part); some row has a positive curve value and a positive base value (else `ComposedGP.fit` raises
  "no row has a positive curve value ..."); and, calibrating, in every fold of `gp.split` over the positive curve rows (as
  `composed.holdout_fold` draws them): such a row outside the held-out ones (the part's fold fit), a row for a joint base or
  SRF fold model, and -- when any of the three models is joint -- a held-out row that every fold model answers (for a
  per-level one, the row's integer level holds 25 of that model's own fold rows): the others are set aside and the rest are
  predicted through scikit-learn. One thing the rows cannot tell: for `resonance` the part is fitted only on rows whose SRF
  the fold's SRF model predicts above f0, which a fit decides (a row has an anchored value only where its measured
  resonance lies above srf_margin x f0, so the count can only be too generous there).

**Why every column fitted today keeps fitting.** The condition fails only where the fit or the calibration raises, so no
column that fits today can fail it. Checked on n28_v2 without fitting (`survey.py` with main's code, `verdicts.py` and
`verdicts_extension.py` with the branch's): of the 580 declared columns of the 16 strata, the 555 with a cached model all
pass; the 23 that raise today all fail -- xfm_ms_m10's ten at 140 / 170 GHz (0 rows: a direct one "expected [N, 7] inputs",
a composed one "no row has a positive curve value") and k@112 (2 rows, joint: the 2026-10-14 prefit's "Found array with 0
sample(s)"), Lp@112 and Ls@112 (2 rows, `ratio` with a joint part: no fold holds out a row); xfm_ms_ap's ten at 140 / 170
GHz. The 8 models of extension columns cached under today's dataset keys pass too. The only uncached columns that pass are
xfm_ms_ap's Lp@112 and Ls@112 (4 rows, `ratio`): each fold holds out one row, which the per-level base answers, so they
raise nothing and are fitted on demand, as before.

**Files and why.**

- `src/ic_opt/library/query.py`: `fits`, `composed_fits`, `_answered` (the condition, from row counts and turns levels with
  `gp.split` / `gp.SEEDS` themselves); `Library.no_model` (the one place; per column, kept per library; the reason
  `TOO_FEW`, "<n> usable rows: too few for a model of this column") and `require_models` (ValueError naming each such column
  with its reason); `models` drops such columns before `_with_needs` (no fit, no worker, none of their parts or resonance)
  and from its answer; `model` refuses one; `_predict` answers it `{"status": "no_model", "rows", "reason", "unit"}` (a
  library row still answers `measured`); `cached` lists it as `too few rows (<n>)`, which replaces `no rows`; `resonance`
  takes the resonance columns that can have a model (before: that have usable rows); docstrings.
- `src/ic_opt/library/suggest.py`, `region.py`: `require_models` on every column the call names -- targets, objective,
  trend and the SRF an anchored target implies -- before any model is loaded or fitted.
- `src/ic_opt/library/densify.py`: `require_models` on named `quantities`; without them such columns are left out with a
  note (and the call refused when none is left).
- `src/ic_opt/recipes/lib_signoff.py`: a line names such columns; the "fitting N models" line and the report leave them out.
- `src/ic_opt/recipes/lib_refine.py`: its "fitting" line leaves them out; `_worse` knows `no_model` (`Qmin` takes it from
  either Q; the neighbour fails `uncertain` naming it); docstring.
- `src/ic_opt/library/stage.py`, `src/ic_opt/blocks/library.py`: docstrings (the Predict stage already fails a metric whose
  answer is not a value as `failed:predict` with its reason; the query block's help names the status).
- `family.py` unchanged: a table's own refusal (lib.suggest / lib.region) becomes its `cannot_take` with the reason.
- `docs/em/library.md`: section 4's table and a paragraph (which columns, what each command then does), sections 5c and 7,
  the Cache paragraph. `skills/ic-opt/SKILL.md`: the statuses, `cached` and the closing sentence.

**Tests.** New `tests/ic_opt/test_library_few_rows.py`, 13 tests on fake strata whose rows are written straight into the
library's datasets (multi-turn transformer dims): `few` -- `Lp_lf` (40 rows at two secondary turns, 4 at three), `k_lf` (2
rows, joint), `Lp@100` (`ratio` with a joint part; 4 rows at three turns, where its base has no sub-model) and `Lp@120` (no
row); `kept` -- `Lp_lf`, `Qp_peak` (4 rows at three turns, per level) and `Qs_peak` (no row). Checked: the verdicts; the
library's own `_fit` raises for exactly the columns `no_model` names (each with the message above) and completes for the
others, `Qp_peak` with no sub-model and nothing scored; the condition in numbers; `models` of every column fits only `Lp_lf`,
`model` refuses with the reason; `query` of every column (`no_model` with rows, reason and unit, the others as when asked
alone, a library row `measured`); `Qp_peak` fitted and out_of_domain (criterion 2) as before; `lib.suggest` (target,
objective), `lib.region` (trend, target) and `lib.densify` (named) refused before any fit; a family request's
`cannot_take`; `lib.densify`'s default and its note; the query block in strict JSON; a 2-row joint `SRF_p` limits no curve
and is not fitted; `lib_signoff` (the inductor fixture with `Lp@70`, past every sweep) through plan and run, its line and
report; lib_refine's `Qmin`. Runs (`OMP_NUM_THREADS=2`, no `-n`) of the 21 `test_library*.py` files, `test_lib_refine.py`
and `test_cli_recipes.py`: at 3542196 (a `git archive` export) 314 passed, 6 skipped (441 s); on the branch 327 passed, 6
skipped (477 s); no existing test changed. `ruff check src tests`: clean.

**The real check** (n28_v2 read-only, the given `IC_OPT_PROFILE_DIRS`, `OMP_NUM_THREADS=4`; `cache_dir` in the evidence
folder, still empty afterwards; the library's `.cache` (3031 files, newest 16:21) and `library.yaml` untouched; every fit
entry point replaced by a stop that names what it would fit: `real_check.py`, `compare.py`, `main_before.py`). The point:
primary OD 80 um -- midway between the rows at 60 and 100 um that agree in every other dim --, secondary 60 um, widths 4 /
4 um, offset 0, secondary spacing 3 um, two secondary turns: no row.

- xfm_ms_m10: `cached` lists 13 columns as `too few rows (<n>)`: the ten at 140 / 170 GHz (0) and Lp@112, Ls@112, k@112
  (2); Qp@112 and Qs@112 (2 rows each, per level) stay `model`. `query` of every column: 13 `no_model`, 28 `predicted`, 2
  `above_resonance` (Qp@112, Qs@112); the other 30 columns bit for bit as main (each column's JSON; `params` and `notes`
  too). `lib.models` of every column returns without fitting. `lib_signoff --plan` (n28_signoff's xfm_bs_m10 spec as
  `n120_signoff_xfm_ms_m10`, the point as candidate, `stratum=xfm_ms_m10 cache_dir=...`) gets through its plan: it names
  the 13 columns, fits nothing and plans one EMX run, not run. With main's code the same query stops at the fit of Lp@112
  and `lib.models` at the fit of 13 models, the 7 direct ones first -- where main raises.
- xfm_ms_ap: `cached` lists the ten at 140 / 170 GHz (0); at 112 GHz Qp, Qs and k (4 rows) stay `model`, Lp and Ls (4
  rows, `ratio`) are `none`. `query` of every column but Lp@112 and Ls@112: 10 `no_model`, 27 `predicted`, 1 `uncertain`, 3
  `above_resonance` (Qp@112, Qs@112, k@112); the other 31 columns bit for bit as main. `lib.models` of every column stopped
  where it would fit Lp@112 and Ls@112, before anything started; of every column but those two it returns without fitting.

**Deviations.**

1. The condition leaves out the specification's "at least one turns level with a sub-model where the model splits by
   level" and its fold clause for per-level models: neither makes the library raise, and on n28_v2 they would change
   cached models -- the first the 9 per-level models whose every level holds fewer than 25 rows (ind_sym_ap and ind_sym_m10
   Lp@170 / Qp@170, 20-21 rows; ind_sym_ap_hf SRF_p, 3; xfm_ms_ap Qp@112 / Qs@112, 4; xfm_ms_m10 Qp@112 / Qs@112, 2: no
   sub-model, every answer off their rows out_of_domain, criterion 2), the second also 5 that answer at their level but
   score no fold (ind_sym_ap / ind_sym_m10 Lp@140 / Qp@140, 28 rows; ind_sym_m10_hf SRF_p, 29) -- and an existing test would
   change (N-114: ind_o2's Lp@50, 11 rows at one level, answers with its measured rows and a not_met_together reason).
   ind_sym_ap_hf's SRF_p is the resonance of that table's curves and the SRF its anchored targets imply: as no model it
   would change their answers too.
2. Hence 13 and 10 columns `no_model` where section 3 expected 15 and 15: Qp@112 / Qs@112 of both tables and xfm_ms_ap's
   k@112 (4 rows, a joint model calibrated on 5 held-out rows, median error 25 %) keep their cached models and answer as on
   main; xfm_ms_ap's Lp@112 / Ls@112 can be fitted and are not cached, so the real check left them out of the query and
   stopped `lib.models` of every column before their fits. A rule at the domain guard's level (no model where no turns level
   holds 25 of a column's rows, whatever the model) would give 15 and 15 at the price of item 1; a decision for the user.
3. The test's "one of 4 whose level has no sub-model" is a `ratio` curve with a joint part on 4 rows at a turns level where
   its base has no sub-model (it raises today); a per-level column of 4 rows without a sub-model is tested as kept.
4. `cached`: `too few rows (0)` replaces `no rows`, one status for what `no_model` decides.
5. A resonance column without a model limits no curve in `lib.query`; a `lib.suggest` / `lib.region` whose anchored target
   implies one is refused naming it. Both only where today's library raises; neither occurs in n28_v2.
6. The condition follows `Library.calibrate`: without calibration only the fit's rows count.
7. Beyond the specification's list: lib_refine (its "fitting" line, `Qmin` and the neighbour's reason) and the docstrings of
   the Predict stage and the query block.
