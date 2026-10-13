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
