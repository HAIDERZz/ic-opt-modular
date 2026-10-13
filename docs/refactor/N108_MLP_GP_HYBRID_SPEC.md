# N-108: a neural network as the mean, the GP on its residual -- do we get the MLP's accuracy with the GP's interval? (2026-10-13)

N-107 (`N107_REGRESSOR_STUDY_SPEC.md`) measured eight tuned regressors against the library's Gaussian process on the
second-generation library. On the library's own hold-out the GP was best or tied in 47 of 52 columns and the only
model with a calibrated interval; but a tuned MLP (sklearn `MLPRegressor`, lbfgs, two tanh layers) was better on the
transformer tables' Q-peak and SRF columns, and when a whole outer-diameter level is held out it beat the GP on 14
cells, by factors of 3 to 15 where the GP's error was large (Qp_peak, Qp@28, Lp_lf of the single-turn tables; SRF of the
multi-turn tables). The user decided on 2026-10-13 to test the combination: the MLP as the mean, the GP fitted to the
residual so that the interval and the library's calibration stay. Point errors, coverage and seed stability; no EMX;
the library read only; no product code changes.

## 1. The hybrid model

`docs/refactor/reports/library_query/regressors_hybrid.py`, `StratumMLPGP(gp.StratumGP)` -- the library's class
subclassed so that `predict`, `predict_bounds`, `available`, `k_scale` and `sigma_floor_rel` keep their meaning:

- `fit(x, y)`: the per_nt split as the base class (one sub-model per turns level). Joint path: `z = self._scale(x)`,
  the constant inputs dropped as `regressor_base.StratumMean` does; `t = log(y)` for a log target; the MLP of N-107's
  `mlp` variant (`regressors_neural.StratumMLP`'s tuning: `tune` over its 8-configuration grid on the training rows,
  then the refit) with `random_state=seed`; the residual `r = t - mlp(z)`; then the library's GP (`gp._kernel`, Matern
  5/2 ARD, `normalize_y`, 4 restarts, white noise, `random_state=0`) fitted on `(z, r)` -- the same GP the library
  fits, on the residual instead of the target.
- `_predict(x)`: `m = mlp(z) + gp_mu(z)`, `s = gp_sigma(z)`; a log target returns `(exp(m), exp(m) * s)` as the base
  class does, so `prediction_bounds`, `k_scale` and the sigma floor apply unchanged. `posterior_cov` raises.
- `seed` is the MLP's random state (the GP's stays 0). Variants `mlp_gp_s0` ... `mlp_gp_s4` (five seeds) and, for the
  seed spread of the MLP alone, `mlp_s1` ... `mlp_s4` (N-107's `mlp` is seed 0, cached).

## 2. Columns, folds, protocols

The columns where N-107 saw the MLP win or the GP err, every table: inductors `Qp_peak`, `SRF_p`, `Qp@28`, `Qp@112`;
single-turn transformers `Lp_lf`, `Qp_peak`, `SRF`, `Qp@28`, `Qp@112`; multi-turn transformers `Qs_peak`, `SRF`,
`Qs@28` -- 24 columns. The folds of N-104 / N-107 (protocol (i) seeds 0-4 at 20 %, protocol (ii) the same
outer-diameter levels), the composed columns through the rebinding as before (every part of `Qp@28`'s ratio model
becomes the hybrid). `gp` and `mlp` (seed 0) come from N-107's job cache; the new variants are computed.

## 3. What is measured, per column and protocol

- Point errors as before: median, p90, max of the relative error; the verdict rule of N-104 against `gp` and against
  `mlp`.
- Interval: the fraction of held-out rows inside the model's own 2-sigma interval before calibration; the calibration
  factor `k_scale` the library would derive (`gp.calibration_scale` on protocol (i)'s pooled |z| of the five seeds,
  exactly `Library._calibrate`'s rule) and the coverage on protocol (ii) with that `k_scale` applied; the median
  relative half-width of the calibrated interval (`k_scale x sigma / mu`) -- all three for `gp` too (recompute them
  from `gp`'s cached fold results if they hold sigma, else refit `gp` for these 24 columns; say which).
- Seed stability: over the five seeds, the spread of p90 (max / min) for `mlp_gp` and for `mlp`.
- The verdict of the study, per column: the hybrid *holds* when (a) its p90 on both protocols is not more than 10 %
  above the better of `gp` and `mlp` (median of the five seeds), (b) its calibrated coverage on protocol (ii) is at
  least 0.90 and its coverage before calibration on protocol (i) is not below the GP's by more than 0.05, and (c) its
  p90 spread over seeds is below 1.5. Count the columns that hold, per table, and list where it fails and why.

## 4. Deliverables and rules

- The module above; `regressor_study.py` extended only as needed to register it, carry `seed`, report coverage,
  `k_scale`, width and seed spread (a new `report` section and a second figure `figs/MLP_GP_HYBRID_coverage.png`:
  per column, p90 of gp / mlp / hybrid (median over seeds) and the calibrated (ii) coverage); the JSON
  `docs/refactor/reports/library_query/mlp_gp_hybrid_study.json` and `figs/MLP_GP_HYBRID_p90.png`;
  `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n108_mlp_gp_hybrid/RESULT_CN.md` with `run.log`; the INDEX
  line; the `## Record` of this file.
- Branch `n108-mlp-gp-hybrid` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n108-mlp-gp-hybrid` (from
  `main`; `git merge --ff-only main` first); `PYTHONPATH=<worktree>/src`, the main tree's `.venv/bin/python`;
  `git -C <worktree>` and absolute paths; `IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles`;
  the library read only, `cache_dir` and the job cache under `ic-opt-accept/n108_mlp_gp_hybrid/scratch/`; N-107's job
  cache at `ic-opt-accept/n107_regressor_study/scratch/n107-a-harness-kernels/` read only (copy what you reuse).
- Threads: every job at 1 BLAS thread (`--threads 1`, OMP_NUM_THREADS=1: the MLP's result depends on the thread count),
  `--workers 96` (the user's allowance). Pilot first on `ind_sym_ap` (two seeds, two levels), estimate in `run.log`,
  then the full run detached (`setsid`, `run.log`, a `DONE` file), poll DONE with sleeps (never `pgrep -f` your own
  shell). Above 3 hours estimated: drop `mlp_s1..s4` first, then the 112 GHz columns, and say so.
- `ruff check` clean; commits in the repository's style (`Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`):
  the module and harness changes, the results and INDEX line, the record. No merge, no push, no BACKLOG edit, no `src/`
  edit. Report to the coordinator in Chinese after the pilot (with the estimate) and after the run (the counts, the
  per-column verdicts, the largest differences, the coverage numbers, the cost, every deviation), numbers only from the
  JSON.

## Record

Branch `n108-mlp-gp-hybrid`, rebased on `main` at 3400288 (N-105 and N-106 merged; the branch touches only the study
scripts and their results, the rebase was clean). Commits: da454f0 (the module `regressors_hybrid.py` and the harness
extension -- written by the first agent and committed unchanged by the second, as 01bbcdb before the rebase, after it was
checked against this specification), 2cd3072 (results, the two figures, the INDEX line) and this record. Nothing merged,
nothing pushed, nothing under `src/` or in `BACKLOG_CN.md` changed; `ruff check` clean. The library was only read: its
`.cache` holds 1118 files with the directory time 2026-10-13 21:09:40 both before the first run and after this one, and no
file under `n28_v2` is newer. Evidence outside the repository in
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n108_mlp_gp_hybrid/`: `RESULT_CN.md` (the report, a hand-written
supplement at its end, the two figures beside it), `run.log` (the interrupted run, the resumption, the estimate, the full
run), `run_full_64.sh` / `run_measure.sh` / `run_pilot_64.sh` (and the first agent's `run_full.sh` / `run_pilot.sh` /
`run_probe.sh`), `pilot.json` / `pilot_64.json` / `probe.json` / `measure_64.json` with their logs, `estimate.py` /
`estimate2.py`, `speed_probe.sh` + `speed.log`, `dup_rows.py` / `dup_rows.json` / `dup_rows.txt`, `as_run/` (the seven
scripts as the full run started them, `SHA256SUMS`, HEAD da454f0), the job cache `scratch/jobs-mlp_gp_hybrid_study/` and
the 12 throttled jobs in `scratch/throttled_20261013_jobs/`. All numbers are from `mlp_gp_hybrid_study.json` (2cd3072)
unless a line names `run.log`, `dup_rows.json` or a pilot / measurement JSON (all written by scripts).

### What ran

| | |
| --- | --- |
| tables / columns | 6 / 24, the specification's; protocol (ii) on N-104's levels (22 cells: the two inductor Qp@112 columns have no eligible level) |
| folds | per variant 269: (i) 120 (24 x 5 seeds), (ii) 149 |
| variants | gp (N-104's folds), mlp (N-107's seed 0), gp_refit (the library's GP refitted for its sigma), mlp_s1..s4, mlp_gp_s0..s4 |
| jobs | 3228 = 269 gp + 269 mlp copied byte for byte from N-104's / N-107's job caches (sources read only) into this study's cache when the first run started (21:28), + 2690 new: 280 from the first agent's estimate probe (21:05-21:27), 11 from the interrupted run (finished before the throttle), 128 from the speed measurement (23:34-23:38), 2271 in the full run; 0 unfit, 0 errors |
| full run | 64 spawn workers x 1 thread, OMP_NUM_THREADS=1; 2026-10-13 23:39:27 - 2026-10-14 01:15:13, pool 95.7 min (`run.log`) |
| estimate | 122-148 min, written into `run.log` before the start (below) |
| versions | Python 3.11.15, numpy 1.26.4, scipy 1.12.0, scikit-learn 1.3.2, lightgbm 4.6.0; source da454f0 |

### The verdict per column

The specification's rule (a) p90, both protocols, at most 1.10 x the better of gp and mlp (seed medians); (b) calibrated
(ii) coverage >= 0.90 and coverage before calibration on (i) at most 0.05 below the GP's; (c) p90 spread over the five
seeds below 1.5. **The hybrid holds in 4 of 24 columns** -- xfm_bs_ap Qp_peak, SRF and Qp@28, xfm_bs_m10 SRF -- and fails in
20: ind_sym_ap 0 / 4, ind_sym_m10 0 / 4, xfm_bs_ap 3 / 2, xfm_bs_m10 1 / 4, xfm_ms_ap 0 / 3, xfm_ms_m10 0 / 3 (holds / fails).
Conditions failed: (b) in 18 columns, (c) in 9, (a) in 6; only (b) in 8, only (c) in 2. Within (b): the coverage before
calibration on (i) is within 0.05 of the GP's in 10 of 24 columns; the calibrated (ii) coverage reaches 0.90 in 12 of 22.

Per column (p90 and widths in %; hybrid and MLP figures are medians over the five seeds; gp's intervals from gp_refit;
width = the median of k_scale x sigma / mu on (ii), unfloored):

| table | column | p90 % (i) gp / mlp / hybrid | p90 % (ii) gp / mlp / hybrid | k_scale gp / hybrid | coverage before (i) gp / hybrid | calibrated (ii) gp / hybrid | width (ii) % gp / hybrid | p90 spread mlp / hybrid (i); (ii) | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ind_sym_ap | Qp_peak | 2.13 / 1.79 / 1.56 | 1.76 / 1.53 / 1.37 | 1.10 / 6.67 | 0.933 / 0.694 | 0.948 / 0.951 | 0.91 / 1.86 | 1.12 / 1.12; 1.57 / 1.75 | fails (b), (c) |
| ind_sym_ap | SRF_p | 3.26 / 3.19 / 3.04 | 2.87 / 2.86 / 2.83 | 1.04 / 1.40 | 0.944 / 0.892 | 0.987 / 0.892 | 1.64 / 1.65 | 1.05 / 1.14; 1.13 / 1.08 | fails (b) |
| ind_sym_ap | Qp@28 | 2.23 / 2.00 / 1.46 | 3.81 / 9.00 / 9.00 | 1.39 / 3.33 | 0.933 / 0.811 | 1.000 / 0.735 | 2.72 / 1.30 | 1.40 / 1.57; 2.48 / 2.48 | fails (a), (b), (c) |
| ind_sym_ap | Qp@112 | 6.05 / 7.36 / 12.19 | no level | 2.67 / 18.31 | 0.905 / 0.802 | -- / -- | -- / -- | 1.22 / 1.03; -- | fails (a), (b) |
| ind_sym_m10 | Qp_peak | 1.42 / 1.23 / 1.18 | 1.91 / 1.23 / 1.23 | 1.21 / 2.66 | 0.923 / 0.805 | 0.927 / 0.911 | 1.05 / 0.94 | 1.23 / 1.18; 1.19 / 1.19 | fails (b) |
| ind_sym_m10 | SRF_p | 2.28 / 2.31 / 2.34 | 2.14 / 2.44 / 2.41 | 1.03 / 1.45 | 0.944 / 0.894 | 0.971 / 0.804 | 1.74 / 0.96 | 1.09 / 1.14; 1.22 / 1.25 | fails (a), (b) |
| ind_sym_m10 | Qp@28 | 2.22 / 2.26 / 2.08 | 10.02 / 10.02 / 10.02 | 1.64 / 5.73 | 0.909 / 0.775 | 0.947 / 0.830 | 4.73 / 2.52 | 1.25 / 1.46; 1.09 / 1.09 | fails (b) |
| ind_sym_m10 | Qp@112 | 5.38 / 4.86 / 4.98 | no level | 1.30 / 7.25 | 0.908 / 0.745 | -- / -- | -- / -- | 1.29 / 1.27; -- | fails (b) |
| xfm_bs_ap | Lp_lf | 0.78 / 1.61 / 0.75 | 16.22 / 3.36 / 3.36 | 1.00 / 1.00 | 0.966 / 0.965 | 0.785 / 0.811 | 4.36 / 1.01 | 1.20 / 1.09; 2.08 / 2.30 | fails (b), (c) |
| xfm_bs_ap | Qp_peak | 2.10 / 1.74 / 1.40 | 33.54 / 2.83 / 2.78 | 1.00 / 1.00 | 0.971 / 0.959 | 0.982 / 0.928 | 23.73 / 1.74 | 1.21 / 1.12; 1.32 / 1.41 | holds |
| xfm_bs_ap | SRF | 1.92 / 4.94 / 1.84 | 3.12 / 5.06 / 2.53 | 1.00 / 1.00 | 0.955 / 0.951 | 0.960 / 0.970 | 1.66 / 1.57 | 1.07 / 1.07; 1.17 / 1.28 | holds |
| xfm_bs_ap | Qp@28 | 2.28 / 2.36 / 1.77 | 27.11 / 3.26 / 3.26 | 1.00 / 1.00 | 0.971 / 0.957 | 0.996 / 0.928 | 24.51 / 2.11 | 1.09 / 1.14; 1.31 / 1.34 | holds |
| xfm_bs_ap | Qp@112 | 3.61 / 4.24 / 3.83 | 20.21 / 13.64 / 13.64 | 1.74 / 2.29 | 0.921 / 0.881 | 0.995 / 0.840 | 5.07 / 4.11 | 1.18 / 1.17; 2.00 / 1.99 | fails (b), (c) |
| xfm_bs_m10 | Lp_lf | 0.98 / 1.64 / 0.79 | 9.18 / 2.89 / 2.90 | 1.01 / 1.00 | 0.949 / 0.962 | 0.793 / 0.808 | 2.68 / 1.04 | 1.05 / 1.07; 1.63 / 1.69 | fails (b), (c) |
| xfm_bs_m10 | Qp_peak | 2.51 / 2.25 / 1.97 | 37.39 / 3.73 / 3.56 | 1.00 / 1.02 | 0.973 / 0.948 | 0.994 / 0.901 | 28.17 / 1.63 | 1.22 / 1.25; 4.57 / 4.71 | fails (c) |
| xfm_bs_m10 | SRF | 1.97 / 5.33 / 1.86 | 3.05 / 5.72 / 2.48 | 1.00 / 1.00 | 0.965 / 0.960 | 0.961 / 0.958 | 1.60 / 1.52 | 1.15 / 1.04; 1.22 / 1.36 | holds |
| xfm_bs_m10 | Qp@28 | 2.98 / 2.69 / 2.10 | 28.30 / 3.93 / 3.89 | 1.00 / 1.01 | 0.965 / 0.947 | 0.998 / 0.930 | 30.44 / 2.00 | 1.17 / 1.12; 3.46 / 3.50 | fails (c) |
| xfm_bs_m10 | Qp@112 | 3.61 / 4.84 / 3.99 | 12.38 / 14.46 / 14.43 | 1.09 / 1.70 | 0.941 / 0.881 | 0.958 / 0.811 | 4.77 / 3.61 | 1.21 / 1.21; 2.57 / 2.53 | fails (a), (b), (c) |
| xfm_ms_ap | Qs_peak | 5.08 / 2.51 / 2.49 | 14.07 / 2.70 / 2.70 | 1.02 / 4.29 | 0.945 / 0.624 | 0.956 / 0.938 | 10.85 / 2.06 | 1.08 / 1.08; 1.27 / 1.27 | fails (b) |
| xfm_ms_ap | SRF | 5.38 / 3.40 / 3.19 | 21.56 / 3.70 / 3.71 | 1.06 / 1.98 | 0.941 / 0.799 | 0.957 / 0.938 | 17.24 / 2.33 | 1.03 / 1.05; 1.09 / 1.10 | fails (b) |
| xfm_ms_ap | Qs@28 | 7.90 / 9.95 / 9.85 | 25.55 / 18.87 / 18.86 | 1.00 / 29.98 | 0.957 / 0.574 | 0.975 / 0.886 | 18.33 / 49.02 | 1.24 / 1.24; 1.66 / 1.66 | fails (a), (b), (c) |
| xfm_ms_m10 | Qs_peak | 6.52 / 2.78 / 2.76 | 12.89 / 3.30 / 3.34 | 1.08 / 7.65 | 0.933 / 0.535 | 0.954 / 0.947 | 10.18 / 3.19 | 1.11 / 1.12; 1.20 / 1.19 | fails (b) |
| xfm_ms_m10 | SRF | 5.43 / 3.65 / 3.55 | 26.98 / 3.76 / 3.76 | 1.03 / 1.88 | 0.946 / 0.797 | 0.951 / 0.936 | 19.90 / 2.42 | 1.08 / 1.10; 1.12 / 1.13 | fails (b) |
| xfm_ms_m10 | Qs@28 | 7.77 / 8.65 / 8.64 | 34.66 / 25.43 / 25.44 | 1.00 / 26.22 | 0.955 / 0.576 | 0.913 / 0.817 | 13.27 / 35.24 | 1.28 / 1.22; 1.35 / 1.35 | fails (a), (b) |

### Point errors

N-104's rule on the seed medians. Hybrid against gp: (i) hybrid better 11 / tie 13 / gp better 0; (ii) 16 / 3 / 3. Hybrid
against mlp: (i) 11 / 13 / 0; (ii) 3 / 19 / 0 -- the hybrid is never judged worse than the MLP, and on a held-out outer
diameter it is the MLP (19 of 22 cells tie, the p90 equal to two digits in most): far from the training rows the residual
GP's mean returns to the residuals' mean. Between grid neighbours ((i)) it corrects the MLP where the MLP was weak:
against the MLP the lowest p90 ratios are xfm_bs_m10 SRF (i) 0.35, xfm_bs_ap SRF (i) 0.37, xfm_bs_m10 SRF (ii) 0.43,
xfm_bs_ap Lp_lf (i) 0.46, xfm_bs_m10 Lp_lf (i) 0.48; the highest ind_sym_ap Qp@112 (i) 1.65 (the rule says tie: the
hybrid's median is lower), then none above 1.03. Against the GP the lowest are the (ii) cells where the GP fails between
levels: xfm_bs_ap Qp_peak 0.08, xfm_bs_m10 Qp_peak 0.10, xfm_bs_ap Qp@28 0.12, xfm_bs_m10 Qp@28 0.14, xfm_ms_m10 SRF 0.14;
the highest ind_sym_ap Qp@28 (ii) 2.36, ind_sym_ap Qp@112 (i) 2.02, xfm_ms_ap Qs@28 (i) 1.25, xfm_bs_m10 Qp@112 (ii) 1.17,
ind_sym_m10 SRF_p (ii) 1.13.

### Intervals

- gp's intervals come from **gp_refit** (the specification's alternative: N-104's cached GP folds keep no sigma). gp_refit
  scored exactly the cached gp's rows in all 46 (column, protocol) cells; the largest absolute difference of a relative
  error is 2.7e-3 (ind_sym_ap Qp_peak (i)), every other cell at most 3.7e-6. Its k_scale and coverage before calibration on
  (i) equal the library's cached calibration to four decimals in 23 of 24 columns (ind_sym_ap Qp_peak: 1.0997 / 0.9328
  against the cache's 1.1110 / 0.9311) -- the study's interval is `Library._calibrate`'s.
- The hybrid's sigma before calibration is too narrow outside xfm_bs: its (i) coverage is 0.535-0.894 on the ind and xfm_ms
  tables (GP 0.905-0.957), and k_scale (seed median) is 1.40-29.98 there and on the two xfm_bs Qp@112 columns, 1.00-1.02 on
  the other eight xfm_bs columns. Calibrated on (i), the (ii) coverage reaches 0.90 in 12 of 22 cells (GP: 20 of 22, the
  two misses being the GP's own xfm_bs Lp_lf, 0.785 / 0.793, where the hybrid has 0.811 / 0.808).
- Width on (ii): the hybrid's calibrated relative sigma is narrower than the GP's in 18 of 22 cells (ratio 0.06-2.67); the
  narrowest relative to the GP are the cells where the GP's own (ii) error is large (xfm_bs Qp_peak / Qp@28: GP 23.73-30.44 %,
  hybrid 1.63-2.11 %); the widest are xfm_ms Qs@28 (49.02 / 35.24 % against 18.33 / 13.27 %), where k_scale is 29.98 / 26.22.
- The residual GP's hyperparameters (`kernel_fitted` of every hybrid sub-model, five seeds, all folds): white noise at its
  upper bound 0.1 (normalised residual) in 97 % of the xfm_bs fits, 56 % ind, 40 % xfm_ms; at least one length scale at its
  lower bound 0.01 in 77 %, 73 %, 95 %. The residual looks mostly like noise to the GP, and only where the noise term sits at
  its bound (xfm_bs) is the uncalibrated coverage the GP's (within 0.025, Qp@112 aside) -- an association, not a tested cause.
  The mechanism the numbers point at: the GP sees the MLP's residual on the MLP's own training rows, which is smaller than its
  error on held-out rows.

### Seed stability

The p90 spread (max / min over the five seeds) of the hybrid follows the MLP's: on (i) 1.03-1.57 (MLP 1.03-1.40); on (ii)
up to 4.71 (xfm_bs_m10 Qp_peak; the MLP alone 4.57), 3.50 (xfm_bs_m10 Qp@28; 3.46), 2.53 / 1.99 (xfm_bs Qp@112), 2.48
(ind_sym_ap Qp@28), 2.30 / 1.69 (xfm_bs Lp_lf). The residual GP does not damp the MLP's dependence on its random state; the
nine (c) failures are all (ii) cells (plus ind_sym_ap Qp@28 (i) 1.57).

### Cost

Fit seconds summed over the 269 jobs of a variant (the timing conditions differ: gp from N-104 at 2 threads, mlp from N-107,
the probe's jobs under a busy host): gp 34060 s, mlp 34420 s, gp_refit 37710 s, mlp_s1..s4 36932-40064 s, mlp_gp_s0..s4
61853-62419 s (of which the MLP's inner cross-validation 35565-36088 s). A hybrid job's median 159.5-164.5 s against 109.0-129.6 s
for the MLP alone and 42.5 s for gp_refit; the longest 1201-1260 s.

### Deviations

- **Workers 64, not 96** (the user's allowance after the restart). The first agent's full run on 96 workers (2026-10-13 21:28)
  met the host's hardware throttle at 21:40 (PROCHOT, latched until the 22:33 power-off) and was stopped at 23 of 2410 jobs;
  of those 23, the 12 that finished after the throttle's onset (21:40:23) were moved out of the job cache
  (`scratch/throttled_20261013_jobs/`, their seconds throttled, their results deterministic) and recomputed; the 11 before it
  were kept.
- **The estimate** was measured, not watched: the full run starts its longest jobs first, so it finishes nothing in its first
  ~15 minutes. Instead the pilot was rerun on 64 workers into a fresh cache (120 jobs in 138 s; all 144 jobs bit-identical
  to the first pilot's rel / mu / sigma) and xfm_ms's gp_refit and mlp_s1 jobs of the full plan (128 jobs, 4.2 min) were run
  into the full run's cache; today's factors (fit seconds over N-107's for the same folds: ind mlp_gp 1.236, mlp_s 1.298;
  xfm_ms mlp_s 1.320, gp_refit 1.176) gave 122-148 min for 2271 jobs (`run.log`), under 3 hours, so nothing was dropped.
  It ran in 95.7 min: other users' load fell to almost nothing from 23:50.
- A **speed probe** (`speed_probe.sh`: the fixed 2-million-step loop each minute, two samples above 0.40 s stop every job;
  the rule after the 2026-10-13 throttle) ran throughout: 0.093-0.238 s, host busy threads 64-171 of 256 (`speed.log`).
- Readings of the specification: (b) and (c) use the hybrid's seed medians as (a) does; (c) is applied on both protocols;
  a column without a (ii) level would count as `no_level` (none did: the two inductor Qp@112 columns fail on (i) already).
  `figs/MLP_GP_HYBRID_p90.png` is N-107's figure, one bar per family at its lowest-p90 variant, i.e. the best of five seeds;
  the specification's second figure `figs/MLP_GP_HYBRID_coverage.png` shows seed medians with the seeds' range.

### What was not foreseen

- **Duplicate geometries with different values** (`dup_rows.json`): every ind and xfm_bs column holds 2-8 groups of rows with
  identical inputs, in the direct columns each one a `_hf` part's row next to its base part's (`ind_sym_ap` /
  `ind_sym_ap_nt1` / `xfm_bs_ap` ...); within a group the values differ by up to 6.2-7.2 % (Lp_lf), 15.8-23.4 % (Qp_peak),
  7.4-11.6 % (SRF), 11.3-13.8 % (Qp@28), 16.7-31.3 % (Qp@112). On ind_sym_ap Qp@112 (five such groups, outer diameter 60 µm,
  one turn, widths 4-8 µm) the residual GP, at its length-scale bound with little noise, copies a held-out row's training twin
  -- the hybrid's p90 12.19 % against the MLP's 7.36 % and k_scale 18.31. Why the `_hf` and base simulations of one geometry
  disagree, and whether the library should merge them or tell them apart, is the user's decision; N-108 changed nothing.
- The MLP's own seed spread on held-out levels (up to 4.57 on xfm_bs_m10 Qp_peak): N-107's single-seed `mlp` numbers between
  levels carry that much chance.
- The library's GP itself misses 0.90 calibrated (ii) coverage on xfm_bs Lp_lf (0.785 / 0.793).
