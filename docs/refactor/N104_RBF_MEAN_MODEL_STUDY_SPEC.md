# N-104: a scipy RBF interpolator as the mean model, measured against the library's GP (2026-10-13)

`docs/refactor/PACOSYT_COMPARISON_CN.md` read PACOSYT (Passos et al. 2023) against the device library. PACOSYT found a
scipy `RBFInterpolator` the most accurate of six techniques on its own random hold-out. Our library fits one Matern 5/2
Gaussian process per (table, column) because it needs an interval that can be calibrated. The user decided on
2026-10-13: measure, on the second-generation private library and with the library's own hold-out protocols, whether an
RBF interpolator plugged into the same model interface gives lower point errors than the GP. Even if it does, it could
only serve as the mean model under the existing calibration; this study measures point errors only. No EMX, no change to
product code, the library read only.

## 1. The mean model behind the library's interface

A study-only class `StratumRBF(gp.StratumGP)` in the study script (section 4), nothing in `src/`:

- Same constructor and attributes as `gp.StratumGP` (dims, ranges, log_target, nt_mode, kernel -- ignored --, nt_dim,
  feature_map, k_scale, sigma_floor_rel), plus the RBF settings `rbf_kernel` (scipy's name, default
  `thin_plate_spline`), `degree` (the polynomial degree) and `smoothing`.
- `fit`: `per_nt` splits by turns level exactly as the base class (the same `MIN_NT_SAMPLES`, the same `unavailable_nt`),
  each level a `StratumRBF` on the other dims; the joint path fits `scipy.interpolate.RBFInterpolator` on `self._scale(x)`
  -- the base class's scaling: min-max on the ranges, or the feature map's dimensionless features -- against
  `log(y)` for a log target, else `y`.
- `_predict`: the per_nt path as the base class (sub-models); the joint path returns `exp(mu_log)` (or `mu`) and
  **sigma NaN** everywhere: an interpolator has no uncertainty, and this study scores the mean only.
- `available`, `_scale`, `predict`, `predict_bounds` inherited. `posterior_cov` is not needed and raises.
- The composed models (`composed.ComposedGP`, the manifest's `model: resonance` / `ratio` columns) build their parts
  through the module attribute `gp.StratumGP`; the study switches the mean model by rebinding that attribute
  (`gp.StratumGP = StratumRBF` / back to the original) in the process that fits -- the worker processes included, each
  fold job stating which model it runs. `composed.holdout_fold` and `ComposedGP.fit` then build every part (lf, srf,
  part) as RBF; nothing else changes. Keep a reference to the original class before rebinding; `StratumRBF` subclasses
  the original.

## 2. Variants

| name | inputs | target | degree | smoothing |
| --- | --- | --- | --- | --- |
| `gp` | the library's (`Library._fit_inputs` settings: feature map, per_nt, Matern 5/2, 4 restarts) | log | -- | -- |
| `rbf_d1` | the same inputs as `gp` | log | 1 | 1e-6 |
| `rbf_d3` | the same | log | 3 | 1e-6 |
| `rbf_d5` | the same | log | 5 | 1e-6 |
| `rbf_raw_d5` | PACOSYT's: the raw dims min-max scaled (no feature map: `feature_map=None`, `nt_mode` per_nt where the table has `nt_dim`, else joint) | log | 5 | 1e-6 |

A degree the row count cannot support (scipy raises when the polynomial has more terms than points, or the fit is
singular) is recorded as `unfit` for that fold and column, not an error of the study. `rbf_raw_d5` isolates the effect
of the coordinates: PACOSYT models in raw dimensions. Every variant sees exactly the rows, the hold-out splits and the
target transform the `gp` variant sees.

## 3. Tables, columns, protocols

Library: `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28_v2` (read only; open it with
`query.Library(root, calibrate=False, cache_dir=<scratch>)` so nothing is written under the library;
`IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles`). Six tables:

| table | columns (the manifest's model for each applies: direct, `resonance`, `ratio`) |
| --- | --- |
| `ind_sym_ap`, `ind_sym_m10` | `Lp_lf`, `Qp_peak`, `SRF_p`, `Lp@28`, `Qp@28`, `Lp@112`, `Qp@112` |
| `xfm_bs_ap`, `xfm_bs_m10` | `Lp_lf`, `k_lf`, `Qp_peak`, `SRF`, `Lp@28`, `Qp@28`, `k@28`, `Lp@112`, `Qp@112`, `k@112` |
| `xfm_ms_ap`, `xfm_ms_m10` | `Lp_lf`, `Ls_lf`, `k_lf`, `Qs_peak`, `SRF`, `Ls@28`, `Qs@28`, `k@28`, `Ls@112` |

Data and settings of each column come from the library itself: direct columns through `Library._fit_inputs(stratum,
quantity)` (x, y in the fit unit, settings), composed columns through `Library._composed_data` and
`_composed_settings` (x, y, lf_y, srf_y and the three parts' settings). A column whose target is not all positive (so the
library would not use a log target) keeps the library's choice.

Protocols, both on the column's usable rows:

- **(i) random hold-out**: the library's calibration protocol, `gp.split` with seeds 0-4 and fraction 0.2 -- for a
  direct column the splits of `gp.holdout`, for a composed column those of `composed.holdout_fold` (the held-out rows are
  the curve's positive rows' split; every part refitted without them). Score the held-out rows' relative error.
- **(ii) leave one level out**: every level of the table's outer-diameter dim (`outer_diameter_um` for inductors,
  `primary_outer_diameter_um` for xfm_bs, `secondary_outer_diameter_um` for xfm_ms) that has at least 25 usable rows and
  lies strictly inside the column's range of that dim is held out whole, the models fitted on the rest, its rows scored;
  when a table has more than 8 such levels, take 8 spread evenly over the sorted eligible levels (the first, the last and
  six in between), the same levels for every column of the table and every variant. For a composed column write a
  `level_fold` that mirrors `composed.holdout_fold` with an explicit held mask instead of `gp.split`.

Per (table, column, protocol, variant): the pooled relative errors' median, p90, max, `n_scored`, the rows skipped
(no model for their turns level) and `unfit` folds; per (ii) also per level. Time every fit and prediction (wall clock,
per job) so the report can say what each model costs.

Verdict per (table, column, protocol): `rbf_better` when the best RBF variant's p90 is below the GP's by more than 10 %
of the GP's and its median is not above the GP's; `gp_better` the other way round; else `tie`. Summarise the counts and
list every column where the max error of an RBF variant exceeds 3 x the GP's (interpolators can overshoot between levels).
Give PACOSYT's own variant (`rbf_raw_d5`) its own line in the summary.

## 4. Deliverables

- `docs/refactor/reports/library_query/rbf_mean_model_study.py`: `run` (argparse: `LIBRARY_ROOT SCRATCH OUT_JSON
  [--tables ...] [--columns ...] [--variants ...] [--workers 16] [--threads 2] [--levels 8] [--pilot]`), the folds as jobs
  in a spawn `ProcessPoolExecutor` (`multiprocessing.get_context("spawn")`; each worker caps BLAS with
  `threadpoolctl.threadpool_limits(threads)`; the main work under `if __name__ == "__main__":` -- the library spawns too);
  `report` (`OUT_JSON --md OUT_MD --fig OUT_PNG`): the Markdown tables and one figure, p90 relative error per column for
  `gp` and the best RBF variant, protocols (i) and (ii) side by side, one panel per table. Style and structure like
  `xfm_bs_q_ratio_acceptance.py` (ruff-clean; `docs/refactor/reports` is in the ruff include). `--pilot`: one table
  (`ind_sym_ap`), two seeds, two levels, every variant, to measure the cost before the full run.
- Results: `docs/refactor/reports/library_query/rbf_mean_model_study.json` and
  `figs/RBF_MEAN_MODEL_STUDY_p90.png` (into the repository, as the other studies);
  `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n104_rbf_study/RESULT_CN.md` (outside the repository: the
  tables, the verdict counts, the cost per model, what was run and when, the exact commands), with `run.log` and the pilot's
  output beside it.
- `docs/refactor/reports/INDEX_CN.md`: one line under "非页面产物" naming the script, the JSON and the figure.
- This file's `## Record` section (section 5).

## 5. Working rules for the coder

- Branch `n104-rbf-mean-model-study` in the worktree
  `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n104-rbf-mean-model-study` (made from `main`; confirm with
  `git merge --ff-only main`). Run with `PYTHONPATH=<worktree>/src` and the main tree's `.venv/bin/python`; the main
  checkout stays untouched. Use `git -C <worktree>` and absolute paths everywhere (a `cd` inside a compound command does
  not take effect in this environment).
- Resources: at most 32 threads in all (`--workers 16 --threads 2` with `OMP_NUM_THREADS=2`); the pilot first, then
  estimate the full run from its timings and write the estimate into the log before starting it; if the estimate exceeds
  3 hours, lower `--levels` to 4 and say so. Start the full run detached (`setsid`, output to `run.log`, a `DONE` file at
  the end) and poll the `DONE` file with sleeps of a few minutes; never match your own shell with `pgrep -f` (use the pid
  the launcher printed, or `kill -0`).
- The library is read only: no EMX, no `lib_signoff`, no writes under the library root (`cache_dir` points at the scratch
  directory `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n104_rbf_study/scratch/`). No product code changes
  (`src/` untouched); no new dependencies (scipy and scikit-learn are installed).
- Commits: one for the script, one for the results, documents and the INDEX line, one for the record; messages in the
  repository's style, each ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Do not merge, do not
  push, do not touch `docs/refactor/BACKLOG_CN.md`.
- The record (`## Record` at the end of this file): what ran (tables, columns, folds, fits, wall time, threads), the
  verdict counts per protocol and per table, the three or four columns where the difference is largest in either
  direction with their numbers, the cost per model, every deviation from this specification, and anything it did not
  foresee. Numbers only from the JSON the script wrote.

## Record

Branch `n104-rbf-mean-model-study`, from `main` at 6e3a46f (`git merge --ff-only main`: already up to date). Commits: the
script d594e17, the first results and the INDEX line c5e6163, the first record ce93b83; then the follow-up approved on
2026-10-13 (StratumRBF drops the inputs constant on each fit's training rows, below): fa51b04 (that change), d2dee59 (a
deterministic tie-break in the summary), aa582b9 (the results again), and this record's update. Nothing merged, nothing
pushed, `docs/refactor/BACKLOG_CN.md` untouched, nothing under `src/` changed; the INDEX line needed no change. Evidence
outside the repository in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n104_rbf_study/`: `RESULT_CN.md` (+ the
figure beside it), `run.log`, `pilot.json` / `pilot.log`, the job caches under `scratch/`, the script as each run started
it (`rbf_mean_model_study.as_run*.py` with their sha256). All numbers are from `rbf_mean_model_study.json` (aa582b9) unless
a line names the previous one (c5e6163, "before the constant-column handling"), `pilot.json`,
`superseded_run1_rbf_mean_model_study.json` (all written by the script) or `run.log`.

### What ran

| | |
| --- | --- |
| library | `ic-opt-library/n28_v2`, read only (`cache_dir` = the scratch directory; its `.cache` held 1102 files before and after, directory time 2026-10-09, `find -newer` empty) |
| tables / columns | 6 / 52 (the specification's lists; 14 inductor, 20 xfm_bs, 18 xfm_ms) |
| folds (gp) | (i) 260 = 52 x 5 seeds, 60 967 rows scored; (ii) 327 in 48 cells, 29 924 rows scored. Levels per table: ind_sym_ap 6 of 6, ind_sym_m10 7 of 7, xfm_bs 8 of 17 (24, 30, 40, 48, 56, 80, 150, 210), xfm_ms 8 of 11 (32, 36, 44, 52, 100, 120, 180, 210) |
| jobs | 2935 (table x column x fold x variant): 587 gp, 2348 RBF; 1117 RBF jobs unfit (1303 before the constant-column handling) |
| model fits | gp 771 (composed folds fit 2 or 3 parts); successful RBF fits: d1 771, d3 355, d5 188, raw_d5 399 (before: 610, 340, 175, 386) |
| threads | 48 spawn workers x 2 BLAS threads = 96 (the user raised the allowance to 96 at 16:40:04); OMP_NUM_THREADS=2 |
| wall time | the GP jobs: 27.9 min (16:40:04-17:07:59, the 2935-job run, `wall_seconds` 1675.3 in c5e6163's JSON); the RBF jobs with the constant inputs dropped: 6.0 s (17:17:33-17:17:39, this JSON, the 587 GP jobs read from that run's cache) |
| versions | Python 3.11.15, numpy 1.26.4, scipy 1.12.0, scikit-learn 1.3.2 |

Pilot (`pilot.json`): ind_sym_ap, seeds 0-1, levels 80 / 210, five variants: 105 jobs in 2.1 s on 16 x 2 threads, gp 16.6 s
of fits (median 0.90 s per job), all 84 RBF jobs unfit. The estimate written into `run.log` before the first full run
(1.4 h on 16 workers, from the pilot and the T16.2a fit times) was below 3 h, so `--levels 8` as specified.

### Verdicts

Counts are rbf_better / tie / gp_better (+ cells without a comparable RBF variant):

| | (i) random hold-out | (ii) leave one level out | before the constant-column handling: (i) | (ii) |
| --- | --- | --- | --- | --- |
| all tables (best RBF variant) | 0 / 3 / 49 | 10 / 11 / 27 | 0 / 0 / 36 (+16) | 10 / 9 / 17 (+12) |
| ind_sym_ap | 0 / 3 / 4 | 0 / 1 / 4 | 0 / 0 / 0 (+7) | 0 / 0 / 0 (+5) |
| ind_sym_m10 | 0 / 0 / 7 | 0 / 1 / 4 | 0 / 0 / 0 (+7) | 0 / 0 / 0 (+5) |
| xfm_bs_ap | 0 / 0 / 10 | 5 / 2 / 3 | same | same |
| xfm_bs_m10 | 0 / 0 / 10 | 3 / 3 / 4 | same | same |
| xfm_ms_ap, xfm_ms_m10 (each) | 0 / 0 / 9 | 1 / 2 / 6 | 0 / 0 / 8 (+1: Ls@112) | 1 / 2 / 5 (+1: Ls@112) |
| `rbf_d1` alone | 0 / 1 / 51 | 10 / 11 / 27 | 0 / 0 / 36, 16 not comparable | 10 / 9 / 17, 12 not comparable |
| `rbf_raw_d5` alone (PACOSYT's) | 0 / 2 / 20, 30 not comparable | 4 / 4 / 12, 28 not comparable | 0 / 0 / 20, 32 not comparable | 4 / 4 / 12, 28 not comparable |

Every cell now has a comparable RBF variant. The best variant is `rbf_d1` in 49 of 52 (i) cells (d3 2, d5 1) and 46 of 48
(ii) cells (d3 1, d5 1). RBF better in (ii): xfm_bs_ap Lp_lf, Qp_peak, Qp@28, Lp@112, Qp@112; xfm_bs_m10 Lp_lf, Qp_peak,
Qp@28; xfm_ms_ap and xfm_ms_m10 Qs_peak -- the same ten as before.

The inductor columns (median / p90 / max relative error, gp against the best RBF variant; all in %):

| table | column | (i) gp | (i) best RBF | verdict | (ii) gp | (ii) best RBF | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ind_sym_ap | Lp_lf | 0.08 / 0.47 / 4.87 | d1 0.34 / 2.11 / 10.76 | gp | 0.37 / 1.33 / 6.72 | d1 0.59 / 2.99 / 9.55 | gp |
| ind_sym_ap | Qp_peak | 0.30 / 2.13 / 14.94 | d1 0.38 / 1.86 / 22.43 | tie | 0.32 / 1.76 / 13.57 | d1 0.30 / 1.63 / 6.39 | tie |
| ind_sym_ap | SRF_p | 0.53 / 3.26 / 15.25 | d1 0.91 / 4.54 / 15.29 | gp | 0.50 / 2.87 / 7.54 | d1 0.88 / 3.74 / 8.70 | gp |
| ind_sym_ap | Lp@28 | 0.10 / 0.86 / 7.60 | d1 0.47 / 3.30 / 9.22 | gp | 0.68 / 3.39 / 7.92 | d1 0.94 / 4.26 / 12.88 | gp |
| ind_sym_ap | Qp@28 | 0.47 / 2.23 / 11.22 | d1 0.65 / 5.17 / 23.94 | gp | 0.61 / 3.81 / 10.05 | d1 1.58 / 9.33 / 33.91 | gp |
| ind_sym_ap | Lp@112 | 0.21 / 1.25 / 4.98 | d5 0.13 / 1.70 / 5.59 | tie | no level | | |
| ind_sym_ap | Qp@112 | 0.71 / 6.05 / 12.67 | d3 0.52 / 12.75 / 16.67 | tie | no level | | |
| ind_sym_m10 | Lp_lf | 0.10 / 0.44 / 5.03 | d1 0.37 / 2.08 / 12.46 | gp | 0.32 / 1.35 / 6.35 | d1 0.59 / 3.31 / 11.02 | gp |
| ind_sym_m10 | Qp_peak | 0.27 / 1.42 / 12.74 | d1 0.30 / 1.59 / 15.09 | gp | 0.29 / 1.91 / 15.39 | d1 0.30 / 1.66 / 15.14 | tie |
| ind_sym_m10 | SRF_p | 0.43 / 2.28 / 7.59 | d1 0.81 / 3.28 / 10.87 | gp | 0.46 / 2.14 / 6.42 | d1 0.75 / 3.26 / 8.72 | gp |
| ind_sym_m10 | Lp@28 | 0.12 / 0.89 / 6.59 | d1 0.60 / 3.95 / 7.84 | gp | 0.96 / 4.81 / 10.75 | d1 1.00 / 5.81 / 9.23 | gp |
| ind_sym_m10 | Qp@28 | 0.36 / 2.22 / 9.03 | d1 0.86 / 7.58 / 18.58 | gp | 0.91 / 10.02 / 21.73 | d1 1.83 / 14.57 / 36.22 | gp |
| ind_sym_m10 | Lp@112 | 0.24 / 1.99 / 14.79 | d1 0.30 / 2.92 / 21.83 | gp | no level | | |
| ind_sym_m10 | Qp@112 | 0.68 / 5.38 / 19.36 | d1 0.94 / 7.67 / 22.71 | gp | no level | | |

The inductor ties are a lower RBF median with a p90 not 10 % lower (Qp_peak), or a lower gp p90 with a higher gp median
(ind_sym_ap Lp@112, Qp@112); where gp is better its p90 is 0.21-0.89 x the best RBF's. xfm_ms Ls@112 (d1 now fits): gp
better in all four cells, e.g. xfm_ms_ap (ii) gp 0.53 / 2.03 / 5.75 % against d1 2.08 / 9.40 / 21.73 %.

Largest differences (best RBF p90 / gp p90; cells are median / p90 / max relative error) -- unchanged by the follow-up:

| direction | table, column, protocol | gp | best RBF | ratio |
| --- | --- | --- | --- | ---: |
| RBF lower | xfm_bs_ap Lp_lf (ii) | 2.32 / 16.22 / 21.29 % | d1 0.56 / 1.64 / 5.88 % | 0.10 |
| RBF lower | xfm_bs_m10 Lp_lf (ii) | 1.24 / 9.18 / 13.16 % | d1 0.58 / 1.84 / 4.96 % | 0.20 |
| RBF lower | xfm_bs_ap Qp@112 (ii) | 1.19 / 20.21 / 63.25 % | d1 0.94 / 13.49 / 45.06 % | 0.67 |
| RBF lower | xfm_bs_ap Qp@28 (ii) | 5.80 / 27.11 / 74.83 % | d5 3.37 / 19.24 / 100.26 % | 0.71 |
| GP lower | xfm_bs_m10 SRF (ii) | 0.90 / 3.05 / 11.14 % | d1 18.03 / 72.10 / 110.94 % | 23.67 |
| GP lower | xfm_bs_ap SRF (ii) | 0.79 / 3.12 / 15.00 % | d1 16.38 / 63.21 / 94.53 % | 20.25 |
| GP lower | xfm_bs_m10 k_lf (ii) | 0.58 / 3.84 / 24.35 % | d1 7.86 / 30.64 / 43.69 % | 7.99 |
| GP lower | xfm_ms_m10 k_lf (ii) | 0.55 / 2.44 / 16.53 % | d1 5.45 / 14.76 / 34.12 % | 6.05 |

In (i) the ratio now runs from 0.87 (ind_sym_ap Qp_peak, a tie) to 6.04 (xfm_ms_m10 k_lf); before, from 1.14. The Lp_lf gain
in (ii) sits in the held-out level 80 um (between 60 and 100): gp p90 18.9 % (ap) / 10.8 % (m10) there, d1 3.6 % / 3.1 %;
the SRF loss sits at the large levels: d1 p90 at 150 / 210 um 61.0 / 82.2 % (ap), 70.1 / 95.3 % (m10), gp 1.4-2.3 %.
Overshoot (an RBF variant's max above 3 x gp's): 109 cells (104 before), 26 of them with non-finite predictions; 17 are the
best RBF variant of their cell (15 before; the two new ones ind_sym_ap Qp@28 (ii) d1 max 33.91 % against 10.05 % and
xfm_ms_ap Ls@112 (ii) d1 21.73 % against 5.75 %).

### Cost per model

| variant | jobs | fits | fit total (s) | per job median / p90 / max (s) | predict total (s) |
| --- | ---: | ---: | ---: | --- | ---: |
| gp | 587 | 771 | 79 268 | 38.13 / 402.09 / 747.26 | 5.4 |
| rbf_d1 | 587 | 771 | 34.7 | 0.019 / 0.180 / 0.329 | 2.7 |
| rbf_d3 | 587 | 355 | 36.9 | 0.028 / 0.168 / 0.326 | 1.9 |
| rbf_d5 | 587 | 188 | 52.5 | 0.042 / 0.238 / 0.367 | 1.1 |
| rbf_raw_d5 | 587 | 399 | 50.5 | 0.040 / 0.252 / 0.429 | 2.2 |

Wall-clock times with 48 jobs at once (the RBF jobs ran on their own, the GP jobs among each other). The same GP jobs at 16
at once (`superseded_run1_...json`): 45 706.5 s in all, per job median 20.0 s, max 424 s -- a GP fit slows down by about
1.7-1.9 x at 48 concurrent fits; the RBF fits are three to four orders of magnitude cheaper either way.

### Deviations from this specification

1. **Levels (ii).** "The same levels for every column" read as: the table's eligible levels are the union of its columns'
   eligible levels, `--levels` of them are spread over that union (numpy's rounding of `linspace` indices), and each
   column is held out at the chosen levels that are eligible for it. The inductor tables' Lp@112 / Qp@112 have no eligible
   level, so no (ii) cell.
2. **Best RBF variant.** Only a variant that fitted every fold of the cell and scored exactly the gp's (fold, row) set may be
   "best", the first in the variants' order on a tie (d2dee59; before it, the job that finished first named it); a cell
   without one is `no_rbf` (none remain now). `gp_better` is the rule with the roles swapped: gp's p90 below the best RBF's
   by more than 10 % of the best RBF's, gp's median not above.
3. **More unfit causes.** Besides scipy raising (too few rows for the polynomial, a rank-deficient monomial matrix), a
   composed RBF fold that `ComposedGP.fit` refuses ("no row has a positive curve value and base value (and a predicted
   SRF above f0)") is unfit: 18 folds (d3 4, d5 14), all xfm_bs Lp@28 / Lp@112 (resonance), where the SRF part is
   numerically singular on the feature map without scipy flagging it. GP jobs and direct folds still raise. A fit scipy
   accepts is scored as it predicts; a non-finite prediction is an infinite error, and a p90 or median that falls between
   two infinite errors is infinite (numpy's quantile gives NaN there). The JSON holds 177 `Infinity` tokens (Python's json).
4. **Constant inputs dropped (the approved follow-up, fa51b04).** Section 1 says the joint path fits on `self._scale(x)`;
   StratumRBF now drops the scaled inputs that are constant on that (sub-)model's training rows, at the fit and again at
   the prediction, for all four RBF variants; a rank-deficient monomial matrix or too few rows stay unfit. It changes
   nothing for a fit without a constant input: the 1045 RBF jobs that fitted before are bit-identical (rows and errors);
   186 jobs fit now that did not (inductors: d1 129, d3 15, d5 13, raw_d5 13; xfm_ms Ls@112: d1 16), 1117 stay unfit.
   Only the RBF jobs were refitted (their cache key covers StratumRBF); the GP jobs are the 16:40 run's.
5. **Resources.** The GP jobs ran on 48 workers x 2 threads (96) on the user's raised allowance, not 16 x 2.
6. **Run history.** The first full run (16 x 2, from 15:40:03) stopped at 16:27:51 after 484 jobs on cause 3; it was
   resumed from its job cache (16:32:19-16:32:41). Its summary had NaN p90s (cause 3's last sentence), so it was set aside
   (`scratch/jobs-rbf_mean_model_study.run1`, `superseded_run1_rbf_mean_model_study.json`) and everything refitted from
   16:34:35; that run was stopped at 16:40:04 with 1 job done for the 96-thread restart, which ran 16:40:04-17:07:59
   (c5e6163). Same job keys in the set-aside cache and that run: 2926 of 2935 results identical, 9 gp jobs differ by at most
   6.9e-8 in a relative error (BLAS summation order); the verdict counts were the same. The follow-up's RBF jobs ran
   17:16:04-17:16:10; after the tie-break change they were set aside (`scratch/jobs-rbf_1716`) and fitted once more,
   17:17:33-17:17:39, so that the JSON comes from an invocation that fitted them: all 2348 results identical to 17:16's.
7. **Additions to the script.** `--dry-run` (the plan only); a per-job result cache in `SCRATCH/jobs-<OUT stem>/` keyed by
   data, settings and model code (a gp job's key leaves out StratumRBF); `report` also prints the gp variant's (i) median
   against the library's own cached calibration: all 52 columns agree in rows scored and within 2.7e-5 relative (15
   exactly), so the gp variant is the library's model on the library's splits.
8. **Commits.** The script commit was amended before the first results commit (unfit handling of cause 3, the p90 fix,
   report formatting); the first results commit also carries two report-only touches to the script. The follow-up is in new
   commits only (no amend).
9. **RESULT_CN.md** is `report`'s output plus a hand-written section at its end (run history and the points above); the
   figure is written beside it and copied into `figs/`.

### Not foreseen

- **The inductor tables could not take any RBF variant as specified.** The single-turn level has one spacing value: a
  constant input makes even the degree-1 monomial matrix rank 3/4, so every fold of every inductor column was unfit for all
  four variants (516 jobs; the "before" counts). With the constant input dropped (deviation 4) `rbf_d1` fits every
  inductor fold; d3 / d5 / raw_d5 still fit only a few (the other levels have three spacing values, too few for degree 3).
  On the inductors the GP is better in 11 of 14 (i) cells and 8 of 10 (ii) cells, the rest ties; no RBF win.
- **Degree 3 and 5 meet the library's grids.** Unfit RBF jobs by cause now: d3 332 rank + 2 too few rows + 4 composed, d5
  264 rank + 169 too few rows + 14 composed, raw_d5 154 rank + 178 too few rows, d1 none (before: d1 145, d3 347 + 2 + 4,
  d5 270 + 176 + 14, raw_d5 160 + 185). xfm_ms per_nt sub-models over 6 raw inputs need 462 rows for degree 5. On the
  feature maps d3 / d5 also produce numerically singular fits that scipy accepts (coefficients ~1e30; relative errors up to
  1.2e+236 % or overflow); they appear in the overshoot list with their non-finite counts.
- **`rbf_raw_d5` is `rbf_d5`** wherever the library already fits the raw dims (xfm_bs Lp_lf, Qp_peak and the ratio columns
  Qp@28 / Qp@112, all inductor columns, the xfm_ms per_nt columns): the same model. Where both fitted -- 18 cells: the four
  xfm_bs columns in both tables and protocols, and ind_sym_ap Lp@112 / Qp@112 (i) -- the numbers are identical.
- **The GP's weak spot is a gap in the outer-diameter grid**, not the interpolator's choice of kernel: holding out 80 um
  leaves 60 -> 100 and the GP's Lp_lf errors there (p90 18.9 %) are where RBF d1 wins most; RBF in turn fails badly on
  SRF and k at the large levels, and on protocol (i) -- the library's calibration protocol -- it is never better.
- During the first run another user's Spectre jobs took about 112 cores of the host; the GP times are under 48 concurrent
  fits of our own and are higher per job than at 16 (cost table above).
