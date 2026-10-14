# N-125: a composed curve's calibration fits its parts once per fold, shared by all its columns (2026-10-14)

N-122 (`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n122_random_requirements/RESULT_CN.md`, problem 4) measured
what a curve at a frequency nobody asked before costs: 10 new frequencies on the 8 untapped transformer tables, 384 models, the
two large single-turn stacked tables 64 and 73 minutes at 16 threads -- about 1.8 thread-hours per frequency each, 4.9 for the
8 tables. Most of it is the five-fold calibration of the composed curves (`model: resonance` -- Lp, Ls of xfm_bs; `model:
ratio` -- Qp, Qs of xfm_bs, Lp, Ls of xfm_ms): `composed.holdout_fold` refits every part (the low-frequency value or the Q
peak, and SRF for a resonance curve) on each fold, and the fold's held-out rows are `gp.split`'s of the curve's own usable rows,
which differ from frequency to frequency -- so the same part models are fitted again for every column. User decision
2026-10-14 ("按这三步执行"): step 1 of 3, before the grid of N-126 is fitted ahead and N-127 answers between its frequencies.

## 1. What changes

- **The fold design of a composed curve's calibration computed from now on ("stable rows").** Fold s holds out
  S_s = the first round(0.2 x N) entries of `gp.split`'s seeded permutation of all N rows of the dataset (s in `gp.SEEDS`,
  fraction `gp.HOLDOUT`) -- the same rows for every column of the stratum. For a composed column c: the scored held-out rows are
  S_s intersected with the rows that have a positive value of c; each part p is fitted on (the rows usable for p) minus S_s; the
  column's own model (residual or ratio) on (the rows with a finite value of c) minus S_s; the held-out rows are scored exactly as
  `composed.holdout_fold` scores them today (relative error, |z| against the unfloored sigma, inside 2 sigma, levels, skipped,
  n, nonpositive) and the five folds merged by `composed.merge`. No held-out row is seen by any model its fold predicts with.
- **A part's fold model is fitted once.** Part p's model of fold s depends only on the dataset, p's settings and s: fit it once,
  keep it as a file in the cache directory (a name keyed by stratum, part, seed, the dataset key, the part's settings, the
  hold-out fraction and the source of `gp.py`, as `_model_name` keys a model), under a lock as `_fit` holds one, and use it for
  every composed column of the stratum that has p as a part -- every frequency, and both Lp and Ls for SRF.
- **The parallel path** (`Library._fit_composed`): first the missing part fold models (one job per part and fold), then the
  columns' folds (the column's own model only), then the finals, all within `fit_plan`'s workers. The single-worker path does
  the same in order.
- **Identity, and nothing cached changes.** A column calibrated this way gets a calibration file of its own name (today's
  composed calibration name with `"folds": "stable-rows"` among the hashed settings), whose JSON says `"folds": "stable-rows"`
  and whose `source` says how it was made. `_composed_calibration` first looks for today's name: a column calibrated before keeps
  its calibration, its model file and its answers bit for bit, and only a column without a calibration is calibrated the new
  way (a fresh cache directory calibrates everything the new way). The final model of a column calibrated the new way is named
  through its calibration as today.
- **Do not change `src/ic_opt/library/gp.py` or `composed.py`**: their source is part of every cached model's key. The new fold
  lives in `query.py` or in a new module under `src/ic_opt/library/`; it may call `composed.ComposedGP`, `composed.merge`,
  `gp.split`, `gp.StratumGP`, `gp.prediction_bounds`.
- Direct columns (`model: direct`, every inductor curve, xfm k, xfm_ms Q) are unchanged.

## 2. Tests (fakes, no EMX)

- A stratum whose composed column has a calibration under today's name: `models` loads it, runs no fold, and its answers are
  `main`'s bit for bit.
- A fake stratum with two or more composed columns at frequencies whose usable rows differ: every held-out row of every fold is
  absent from the training rows of every model that fold predicts with (assert on the masks); each part's fold model is fitted
  exactly once per (part, seed) across the columns (count the fits); the calibration has today's fields plus `folds`; a second
  `Library` on the same cache directory fits nothing; two processes asking for the same column at once fit it once (extend the
  existing lock test if there is one).
- The existing library tests unchanged (`test_library_composed.py`, `test_library_cache.py`, `test_library_anyfreq.py`, ...).

## 3. Real measurement (no EMX; the library is not written to)

Library `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28_v2`,
`IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles`. Give `Library` a
`cache_dir` under your scratch directory: the library's own `.cache` is still read (so the strata's scalar models are found
and only the new columns are fitted) and every new file goes to the scratch directory. Pick three frequencies that no cache
holds a column of (check `lib.cached` and the file names; for example 33, 77 and 95 GHz).

1. `xfm_bs_m10_hf` (635 rows) and `xfm_bs_m10` (1736 rows): fit the four composed curves (Lp, Ls, Qp, Qs) at the three
   frequencies with `main`'s code and with the branch's, each in a fresh scratch cache directory, the same limits (16 threads):
   wall time per table, the number of GP fits, per column k_scale / median_rel / coverage under both, and both codes'
   predictions at 200 random in-domain points (the distribution of their relative difference -- they differ only by the folds).
2. `xfm_bs_m10` with the branch: ten new frequencies at 16 threads, to set against N-122's 73 minutes for ten on `main`.

At most 32 threads in all. Before each run read the machine's busy logical CPUs from `/proc/stat` over 10 s: another user's
Spectre batches take up to about 130 of the 256; above 150, wait and say so in the record.

## 4. Documents

`docs/em/library.md` (Compute and Cache: how a composed curve is calibrated, the part fold files), the ic-opt skill where it
gives fitting costs.

## 5. Working rules for the coder

Branch `n125-shared-part-folds` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n125-shared-part-folds` (from `main`;
`git merge --ff-only main` first and check the base). N-126 is developed at the same time (manifest, `Library.knots`, a new block
`lib.prefit`, `lib.cached`): keep this change to the calibration path. `PYTHONPATH=<worktree>/src` with
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`; never `uv run` or `uv sync`; `git -C <worktree>` and
absolute paths. Targeted tests (library modules, cache, docs, CLI) plus `ruff check src tests`; pytest with
`OMP_NUM_THREADS=2`, no `-n`. Scratch files only under `<session scratchpad>/n125/`. Append one line to
`<session scratchpad>/n125/progress.log` at every milestone (start, design settled, tests green, each measurement, done) --
Claude reads it to follow you. One commit for the change with its tests and documents, one for the `## Record` section below. No
merge, no push, no edit of `docs/refactor/BACKLOG_CN.md`. Messages in the repository's style, ending with
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Record

(to be filled by the coder: branch and base, commits, what changed where, tests run, the measurement's numbers, deviations)
