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

**Branch and base.** `n125-shared-part-folds` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n125-shared-part-folds`,
from `main` at 25cc40d (`git merge --ff-only main`: already up to date). Commits: fa3c866 (the change, its tests and
documents) and this record. Not merged, not pushed; `docs/refactor/BACKLOG_CN.md` untouched; `gp.py` and `composed.py`
without a diff. Scratch (scripts, logs, run summaries, fit logs):
`/tmp/claude-1011/-home-zzchen-Agent-virtuoso-EDA-AI-AGENT/22a251ad-03d3-4a55-8437-b0a4e97a3152/scratchpad/n125/`.

**What changed where.**
- `src/ic_opt/library/folds.py` (new): `held(n, seed)` = S_s = `gp.split(n, seed)[0]` over all n rows (`mask` as booleans);
  `fit_part` fits a part's fold model on its finite rows less S_s; `fold` fits the curve's own model (`ComposedGP` on those
  part models) on its finite rows less S_s and scores S_s's rows with a positive value of the curve exactly as
  `composed.holdout_fold` scores (same keys, gp.split's order; a fold holding out none of the curve's rows scores nothing);
  `report` = `composed.merge` (refusing a joint curve whose five folds held out none of its rows); `STABLE`, `SOURCE`.
- `src/ic_opt/library/query.py`: `Library._part_fold` / `_part_fold_name` -- `fold-<stratum>-<part>-s<seed>-<key>.pkl`,
  keyed as `_model_name` keys a model (`MODEL_CACHE_VERSION`, dataset key, the part's settings, gp.py's source,
  scikit-learn's version) plus the seed, `gp.HOLDOUT` and "stable-rows"; loaded, else fitted holding `<name>.lock` and
  written. `_composed_calibration_names` (today's name; the stable one = today's hashed settings plus `"folds":
  "stable-rows"`); `_composed_calibration` looks for today's name first, then the stable one, writes a new one under the
  stable name with today's fields plus `"folds": "stable-rows"` and `source` = `folds.SOURCE` (keyword `folds=` became
  `results=`); `_composed_calibration_name` = the name in use (the lock's); `_composed_fold` = the stable fold.
  `_fit_composed` + `_calibrate_composed` + `_missing_part_folds` + worker `_part_fold_in_worker`: the parallel path (below).
  `_stable` + `_composed_rows` + module function `stable_composed_fits` (deviation 2); `BEFORE` = today's source.
- Tests: `tests/ic_opt/test_library_shared_folds.py` (new, 6), `tests/ic_opt/test_library_cache.py` (+1 test, 2 cases),
  `tests/ic_opt/test_library_composed.py` (two tests' assertions, deviation 1).
- Documents: `docs/em/library.md` (section 2's composed paragraph; Compute: how a composed curve is calibrated, its cost, the
  sparse exception; Cache: the part fold files, their size, the calibration's `folds`); `skills/ic-opt/SKILL.md` (the cost
  of a curve at a new frequency, in the sentence on curves at any frequency -- the skill gave no fitting cost before).

**Parallel path.** The parent holds every uncached calibration's lock (sorted names, as before), then submits each missing
part fold model (one job per part and seed, seed by seed), each curve's fold as soon as its parts' fold models of that seed
exist (the curve's own model only), writes a curve's calibration when its five folds are back, lets that curve's lock go and
submits its fit; a failed job raises with the worker's exception and the queued jobs are dropped (`cancel_futures`). One
worker: `_fit` / `_fit_composed_here` as before, whose folds load or fit the part fold models under their locks.

**Tests.** Targeted files (`tests/ic_opt/test_library_*.py`, `test_cli_recipes.py`, `test_blocks.py`, `test_lock.py`),
`OMP_NUM_THREADS=2`, no `-n`: 344 passed, 7 skipped on the committed code (base 25cc40d, a pristine copy of the same files:
336 passed, 7 skipped); `test_library_docs.py` again after the last prose edits (12 passed with `test_skill_author_spec.py`);
`ruff check src tests` clean. The new tests: a stable fold equals `composed.holdout_fold` bit for bit where every row has the
curve; Lp@20 (100 rows), Lp@40, Ls@40, Qp@40 (68 rows): every scored row absent from the training rows of each model its
fold predicts with (row masks from the actual fits), each of the 20 part fold models (Lp_lf, Ls_lf, SRF, Qp_peak x 5)
fitted exactly once (SRF serving three curves at two frequencies), n_scored = sum of |S_s with the curve|, today's fields
plus `folds`, a second Library fitting nothing and answering identically; a calibration under today's name: no fold, no
part fold model, nothing stable written, the model file named through it, answers bit for bit those of a model fitted as
main fits one, and with main's model file nothing fitted at all; the parallel path fits only the missing part fold models
(SRF's files untouched) and equals one worker (rel 1e-9); the sparse rule; the stable condition in numbers; two spawned
processes asking for one curve -- or for Lp@20 and Ls@20, sharing SRF -- fit each part fold model and each fold once.

**Measurement** (n28_v2; no EMX; the library not written to: no file under it newer than this session's start).
`Library(..., cache_dir=<scratch>/runs/<run>/cache)`, fresh per run, holding only the run's anchors files (measured once
beforehand into scratch, 3-9 s per table and frequency, copied in: the times are the fits'), the library's own `.cache` read
for the scalar and resonance models; `HostLimits(max_threads=16, max_memory_gb=64)` = 8 workers x 2 BLAS threads,
OMP/OPENBLAS/MKL_NUM_THREADS=2; every top-level `StratumGP.fit` logged, in the workers too. No file of either table at 33,
77, 95 or the ten frequencies below in the library's `.cache`. Busy logical CPUs (of 256, over 10 s) before the runs: 25.4,
2.5, 25.5, 25.7, 24.6 (seed study 0.4) -- never above 150; 5-48 during them (logged each minute).

| table (rows) | code | wall | GP fits: part folds / curve folds / curves | summed fit time |
|---|---|---|---|---|
| xfm_bs_m10_hf (635) | main | 129 s | 162: 90 per curve / 60 / 12 | 0.26 h |
| | branch | 87 s | 97: 25 shared / 60 / 12 | 0.18 h |
| xfm_bs_m10 (1736) | main | 1031 s | 162: 90 per curve / 60 / 12 | 2.13 h |
| | branch | 410 s | 97: 25 shared / 60 / 12 | 0.79 h |

On xfm_bs_m10 main's per-curve part folds were 1.71 h of the 2.13 h (68 s each, 1431-1628 rows), the branch's 25 shared ones
0.37 h (53 s, 1384-1389 rows); curve folds (19 s) and curves (30 s) the same in both. Per frequency (four composed curves):
main 344 s, branch 137 s.

Calibrations, k_scale / median_rel / coverage_2sigma_before / n_scored, main | branch. xfm_bs_m10_hf: all 12 bit for bit
equal (every row has the curves at 33-95 GHz, so S_s is today's held-out set; k_scale 1 throughout). xfm_bs_m10:

| column (rows) | main | branch |
|---|---|---|
| Lp@33 (1506) | 1.000 / 0.00218 / 0.985 / 1505 | 1.000 / 0.00245 / 0.981 / 1529 |
| Ls@33 | 1.000 / 0.00221 / 0.990 / 1505 | 1.000 / 0.00237 / 0.984 / 1529 |
| Qp@33 | 1.184 / 0.00465 / 0.935 / 1505 | 1.004 / 0.00502 / 0.949 / 1529 |
| Qs@33 | 1.036 / 0.00470 / 0.943 / 1505 | 1.016 / 0.00454 / 0.948 / 1529 |
| Lp@77 (807) | 1.000 / 0.00272 / 0.999 / 805 | 1.000 / 0.00258 / 0.999 / 773 |
| Ls@77 | 1.000 / 0.00284 / 1.000 / 805 | 1.000 / 0.00240 / 1.000 / 773 |
| Qp@77 | 1.077 / 0.00734 / 0.945 / 805 | 1.014 / 0.00726 / 0.948 / 773 |
| Qs@77 | 1.359 / 0.00705 / 0.929 / 805 | 1.000 / 0.00677 / 0.959 / 773 |
| Lp@95 (540) | 1.000 / 0.00374 / 1.000 / 540 | 1.000 / 0.00361 / 1.000 / 513 |
| Ls@95 | 1.000 / 0.00382 / 1.000 / 540 | 1.000 / 0.00423 / 1.000 / 513 |
| Qp@95 | 1.000 / 0.00747 / 0.959 / 540 | 1.000 / 0.00763 / 0.949 / 513 |
| Qs@95 | 1.000 / 0.00724 / 0.963 / 540 | 1.000 / 0.00809 / 0.957 / 513 |

Predictions, 200 random points per column inside its domain guard (criteria 1-3, no extrapolation), each code's models from
its own cache: xfm_bs_m10_hf mu, sigma, lo, hi identical at all 2400; xfm_bs_m10 mu and sigma identical at all 2400 (the
curve's own GP and the parts are the same fits; the sigma floor did not bind there), the interval half width differing
only by k_scale: Qp@33 -15.2 to -16.0 %, Qs@33 -1.9 to -2.0 %, Qp@77 -5.8 to -6.3 %, Qs@77 -26.4 to -28.9 %, the other 8
columns 0; pooled, branch / main - 1 at quantiles 0/5/25/50/75/95/100 %: -28.9, -26.9, -2.9, 0, 0, 0, 0 %.

Seed check (the Q curves' lower k_scale): the same three columns under both fold designs with seeds 5-19 (three sets of
five; 90 fold jobs, 1017 s). k_scale per seed set 0-4 (the runs) / 5-9 / 10-14 / 15-19, mean:

| column | today's folds | stable rows |
|---|---|---|
| Qp@33 | 1.184 / 1.000 / 1.032 / 1.000, 1.054 | 1.004 / 1.019 / 1.003 / 1.156, 1.045 |
| Qp@77 | 1.077 / 1.106 / 1.066 / 1.051, 1.075 | 1.014 / 1.112 / 1.000 / 1.143, 1.067 |
| Qs@77 | 1.359 / 1.163 / 1.020 / 1.014, 1.139 | 1.000 / 1.152 / 1.062 / 1.189, 1.101 |

median_rel and coverage spread alike under both (coverage 0.936-0.959). Main's 1.18 and 1.36 are the top of today's own
spread; the difference is within the seed-to-seed spread, the stable mean 0.01-0.04 lower (deviation 6).

Ten new frequencies on xfm_bs_m10 with the branch (13, 18, 25, 45, 49, 53, 100, 107, 115, 128 GHz; 1736, 1736, 1651, 1336,
1274, 1225, 495, 452, 344, 257 rows; Lp, Ls, Qp, Qs, k = 50 columns as N-122's): 1427 s (23.8 min), 325 GP fits -- k 50 +
10 (0-356 s), the 25 shared part folds once (358-555 s), 200 curve folds (509-1208 s), 40 curves (1173-1425 s) -- 2.81 h of
fitting: 143 s per frequency including k and the one-time part folds, against main's 344 s per frequency for the four
composed curves alone in the same-condition comparison above, and N-122's 73 min for ten (437 s per frequency, measured
with three other large tables fitting at once). Main's own ten-frequency run was stopped about a minute after its start
and not rerun, on the coordinator's request, to unblock the merge.

Columns calibrated before: xfm_bs_m10's 12 composed columns at 28 / 46 / 112 GHz and xfm_ms_m10's Lp, Ls at 28 / 46,
cached by main in the library: lib.query's whole answers at 200 points each (2400 and 800 column answers: predicted,
uncertain, above_resonance, out_of_domain) identical under main's and the branch's code, the same calibration and model
files, nothing fitted (any fit raised), nothing written.

Which columns can have a model, from the rows alone: on the 8 transformer tables at the 17 frequencies their cache holds
anchors for (408 composed columns) today's rule and the stable rule disagree on 4 two-row columns (xfm_ms_ap Lp/Ls@116,
xfm_ms_m10 Lp/Ls@112: no model today, one on stable rows); at 90-170 GHz every 2 GHz on the four ms tables, 8 two-row
columns (those 4 and Lp/Ls@114 of both tables), the same way; never the other way round there. With deviation 2 none of them
changes.

**Deviations and doubts.**
1. *Existing tests.* Two tests of `test_library_composed.py` pinned today's folds and are updated (the specification asks
   for them unchanged): `test_q_values_at_or_below_zero_...` asserted n_scored = 5 x round(0.2 x 97) = 95; on stable rows it
   is the five S_s of all 100 rows less the three rows <= 0, 96. `test_the_parallel_path_...` asserted today's source string
   (now `folds.SOURCE`, as section 1 asks) and the exact list of cache files and locks (now with the 15 part fold files and
   their locks). Every other existing test passes as it was.
2. *Sparse curves keep today's folds.* Section 1 calibrates every new column on stable rows; verbatim, that changes N-120's
   verdicts (the 2-row columns above gain a model calibrated on one or two rows; a 3-5-row curve no stable fold holds out
   could lose its own). `Library._stable` uses the stable folds only where today's could calibrate the curve too
   (`composed_fits`) and the stable ones can (`stable_composed_fits`); any other curve is calibrated on today's folds,
   under today's name with today's fields. So `no_model` is main's code and a fit raises exactly where it did; on the
   measured tables every curve with a model uses the stable folds.
3. *An empty stable fold.* S_s may hold out none of a sparse curve's rows (about 0.8**n); that fold scores nothing instead of
   handing scikit-learn an empty set (a fold that holds out rows none of its models answer still raises, as today).
4. *Order of the parallel path* as dependencies rather than three barriers, as described above; failure drops queued jobs.
5. *The two-process test* is in `test_library_cache.py` beside N-4's ("extend the existing lock test").
6. *Lower k_scale of Q curves* on xfm_bs_m10 (Qs@77 1.36 -> 1.00, Qp@33 1.18 -> 1.00, intervals 2-29 % narrower): within
   the seed-to-seed spread of both designs. A small systematic part is plausible -- the parts' fold models of a sparse
   curve now also miss S_s's rows the curve does not have, which widens the fold's sigma a little -- but four seed sets
   cannot separate it from noise.
7. *Disk.* 25 part fold files for a table with five parts: 15.5 MB each on xfm_bs_m10 (388 MB), 1.75 MB on xfm_bs_m10_hf
   (44 MB); the library's `.cache` is 6.8 GB.
8. `composed.py`'s module docstring still describes today's calibration (it may not change); `folds.py`, `query.py` and
   `library.md` describe both. Study scripts under `docs/refactor/reports/library_query/` that call
   `Library._composed_fold` now get the stable fold for a curve without a cached calibration.
9. *Conditions.* Some of my lighter jobs overlapped timed runs (a condition scan of 4 single threads, a legacy check of 1-2
   threads and part of a test run during main's xfm_bs_m10 run; 2-thread legacy and prediction checks during the branch's
   runs); the machine stayed at 5-48 busy logical CPUs.
