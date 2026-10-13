# N-107: the paper's other regressors, tuned, behind the library's model interface (2026-10-13)

N-104 put a scipy RBF interpolator behind the library's model interface and measured it against the library's Gaussian
process on the second-generation library (`N104_RBF_MEAN_MODEL_STUDY_SPEC.md`): no RBF variant was better on the
library's own hold-out. The user asks the question behind it: is the GP the right model for this data, or would another
machine-learning regressor -- the ones PACOSYT compared (its own GPR configuration, kernel ridge regression, random
forest, nearest neighbour, a neural network) and the strongest tabular method it left out (gradient boosting) -- reach a
higher ceiling once it is tuned? N-104 tuned nothing. This study gives every regressor the same inputs, targets and
hold-out folds as the GP and an inner cross-validation over a stated grid of its hyperparameters on the training rows
alone, so that what is compared is each model class at its tuned best. Point errors only (no intervals); no EMX; the
library read only; no product code changes.

## 1. Shared machinery (in the repository already, `docs/refactor/reports/library_query/`)

- `regressor_base.py` (committed with this specification): `StratumMean`, the base every regressor subclasses -- the
  library's scaling, feature maps, per-turns split and log target, the constant inputs dropped; a subclass implements
  `_fit_joint(z, t)`, `_predict_joint(z)` and `own_params()`; `Unfit` for a fold a model cannot fit; `tune(z, t,
  configs, fit_predict, folds=3, seed=0)`, the inner cross-validation (RMSE in the fitted space, the first best wins).
  Import it before any job rebinds `gp.StratumGP`.
- `rbf_mean_model_study.py` (N-104): the folds, the job runner that rebinds `gp.StratumGP` per job (so composed
  columns build every part as the variant), the per-job cache, the summary, the verdict rule and the figure. The harness
  of this study (part A) is built from it, not beside it.
- The GP's fold results are in N-104's job cache
  (`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n104_rbf_study/scratch/jobs-rbf_mean_model_study/`, the
  587 `gp` jobs): the harness reuses them when its job hash for the `gp` variant is N-104's (verify: 587 hits, no GP
  refit), else refits them once.

## 2. Variants

Every variant sees the library's inputs (feature map, per_nt where the table has `nt_dim`, min-max scaling) and the
library's target transform (log for positive columns), exactly as `gp` does. A tuned variant runs `tune` on the training
rows of each fold with the grid below; the configuration chosen is recorded per job. Seeds fixed at 0.

| name | family | model | grid of the inner cross-validation |
| --- | --- | --- | --- |
| `gp` | A | the library's GP (cached) | -- |
| `gp_paper` | A | sklearn `GaussianProcessRegressor`, PACOSYT's configuration: kernel `0.1**2 * RationalQuadratic(length_scale=1e-8, alpha=1e-8) + 1**2 * RBF(length_scale=5)`, `alpha=1e-8`, no restarts, inputs standardized (their `PreProcess(standardize=True)`) | none: the paper tuned nothing; this shows what the kernel configuration costs |
| `krr` | A | sklearn `KernelRidge` | kernel `rbf`, `gamma` in {0.25, 0.5, 1, 2, 4, 8, 16} / d (d = kept inputs), `alpha` in {1e-8, 1e-6, 1e-4, 1e-2} |
| `knn` | A | sklearn `KNeighborsRegressor`, `weights="distance"` | `n_neighbors` in {1, 2, 3, 5, 8} (1 is the paper's NN) |
| `rf` | B | sklearn `RandomForestRegressor`, `n_estimators=500`, `random_state=0`, `n_jobs=n_threads` | `min_samples_leaf` in {1, 2, 4}, `max_features` in {1.0, 0.5, "sqrt"} |
| `gbm` | B | sklearn `HistGradientBoostingRegressor`, `early_stopping=False`, `random_state=0` | `learning_rate` in {0.03, 0.1}, `max_leaf_nodes` in {7, 15, 31}, `max_iter` in {300, 1000}, `l2_regularization` in {0, 1} |
| `lgbm` | B | LightGBM `LGBMRegressor` (installed with the `report` extra; skip the variant with a note if import fails), `random_state=0`, `n_jobs=n_threads`, `verbose=-1` | `learning_rate` in {0.03, 0.1}, `num_leaves` in {7, 15, 31}, `n_estimators` in {300, 1000}, `min_child_samples` in {5, 20} |
| `mlp` | C | sklearn `MLPRegressor`, `solver="lbfgs"`, `max_iter=2000`, `random_state=0`, inputs as scaled, target standardized inside the model (fit on `(t - mean) / std`, undone at prediction) | `hidden_layer_sizes` in {(64, 64), (128, 128), (256, 256)}, `alpha` in {1e-5, 1e-3}, `activation` in {"relu", "tanh"} |
| `mlp_paper` | C | PACOSYT's ANN as its notebook builds it: `PolynomialFeatures(degree=5)` on the standardized inputs, two dense layers [256, 512] relu with L2 1e-6, adam; the subagent re-implements it with sklearn `MLPRegressor(hidden_layer_sizes=(256, 512), activation="relu", solver="adam", alpha=1e-6, max_iter=2000, random_state=0)` on the polynomial features (TensorFlow is not installed and must not be) | none: as the paper ran it |

A grid may be cut where a configuration cannot run on a fold's rows (too few rows for the polynomial features, ...):
record `unfit` as N-104 does, never silently substitute.

## 3. Tables, columns, protocols, verdicts

As N-104, unchanged: the six tables and their 52 columns, protocol (i) `gp.split` seeds 0-4 at 20 %, protocol (ii)
the same outer-diameter levels N-104 selected (read them from its JSON so the folds are identical), the composed
columns through the rebinding, `rel_stats`, the verdict rule (p90 lower by more than 10 % and the median not higher),
the "3 x the GP's max" overshoot list. Add to the summary, per column and protocol: the best non-GP variant and its
p90 / GP p90 ratio; and over all columns a table "which variant is best how often" and "how often any tuned regressor
beats the GP" -- the ceiling question in one number per protocol. Time every fit (the inner cross-validation included)
and prediction.

## 4. Work split and deliverables

Three subagents, three branches from `main`, each in its own worktree under
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/`; the coordinator merges B and C into A's branch, then A runs
the study.

- **A -- harness and the kernel family** (`n107-a-harness-kernels`): `regressor_study.py` built from
  `rbf_mean_model_study.py` (import or copy its machinery; a registry `VARIANTS` that other modules extend by
  importing their classes -- `regressors_kernel.py`, `regressors_trees.py`, `regressors_neural.py`, each module
  registering its variants on import, the harness importing every module it finds); `regressors_kernel.py` with
  `gp_paper`, `krr`, `knn`; the GP cache reuse of section 1; `--pilot`, `--dry-run`, `--variants`; `report`
  (Markdown, the p90 figure as N-104's with one bar per variant family, the ceiling tables). Smoke: the pilot on
  `ind_sym_ap` with its own variants. Then wait for the coordinator's message that B and C are merged, run the full study
  with `--workers 48 --threads 2` (96 threads, the user's allowance), write
  `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n107_regressor_study/RESULT_CN.md`, `run.log`, the JSON and
  figure into the repository (`regressor_study.json`, `figs/REGRESSOR_STUDY_p90.png`), the INDEX line, and the
  `## Record` of this file.
- **B -- trees** (`n107-b-trees`): `regressors_trees.py` with `rf`, `gbm`, `lgbm` as `StratumMean` subclasses using
  `tune`; a smoke script or `__main__` that fits each on one table's one column from the library (through
  `query.Library(root, calibrate=False, cache_dir=<scratch>)._fit_inputs`) with an 80/20 split and prints the chosen
  configuration, the held-out median / p90 relative error and the fit time; the record of what it saw.
- **C -- neural and the paper's ANN** (`n107-c-neural`): `regressors_neural.py` with `mlp`, `mlp_paper`, the same
  smoke; note the fit times (the lbfgs MLP on 2 000 rows must stay under a minute per configuration; cut the grid and
  say so if not).

Rules for every subagent: use `git -C <worktree>` and absolute paths (a `cd` in a compound command does not take
effect); run with `PYTHONPATH=<worktree>/src` and the main tree's `.venv/bin/python`; `OMP_NUM_THREADS=2`;
`IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles`; the library root
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28_v2` is read only (`cache_dir` under
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n107_regressor_study/scratch/<branch>/`); no new dependencies
(sklearn, scipy, lightgbm are installed; no torch, no tensorflow); `ruff check` clean on every file; commits in the
repository's style ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; no merge, no push, no edit
of `BACKLOG_CN.md` or `src/`. B and C report to the coordinator when their module and smoke are committed; A reports
after the pilot, then again after the full run with the verdict counts, the ceiling tables, the largest differences in
either direction, the cost per model, every deviation, and anything the specification did not foresee. Numbers only
from the JSON the script wrote.

## Record

Part A (harness, family A, the full run). Branch `n107-a-harness-kernels` from `main` at 5444ca2; B and C merged into it by
the coordinator (8296414, c64d188). A's commits: 4cdb540 (`regressor_base.py` ruff-clean: a reasoned `noqa: BLE001`,
behaviour unchanged), b80bb02 (`regressor_study.py`), 68290c6 (`regressors_kernel.py`), 3675e3d (the coordinator's three
decisions on family A), b5eeba2 (the harness after the merge), ee21185 (results, figure, INDEX line), and this record.
Nothing merged by A, nothing pushed, nothing under `src/` or in `BACKLOG_CN.md` changed. The library was only read: its
`.cache` held 1118 files (directory time 17:16:51) after the run, and no file under `n28_v2` is newer than the
worktree's checkout (18:18). Evidence outside the repository in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n107_regressor_study/`:
`RESULT_CN.md` (+ the figure beside it), `run.log`, `run_full.sh`, `pass1.json` / `pass2.json`, `pilot.json` / `pilot.log`
(and the earlier `pilot_kernels.*`, `pilot_before_decisions.*`), `as_run/` (the six scripts as the run started them, with
`SHA256SUMS`, HEAD b5eeba2), the job caches and side checks under `scratch/n107-a-harness-kernels/`. All numbers are from
`regressor_study.json` (ee21185) unless a line names `run.log`, a pilot JSON or a scratch JSON (all written by the script).

### What ran

| | |
| --- | --- |
| tables / columns | 6 / 52, N-104's; levels of protocol (ii) read from N-104's JSON (equal to those recomputed here) |
| folds (gp) | (i) 260 (52 x 5 seeds), (ii) 327 in 48 cells -- N-104's |
| variants | gp (the library's GP, N-104's cached folds), gp_paper, krr, knn (A); rf, gbm, lgbm (B); mlp, mlp_paper (C) |
| jobs | 5283 = 587 gp (all read from N-104's job cache under N-104's key, 0 refitted) + 4696 others: 536 fitted by the estimate probe (19:09-19:20, xfm_bs_ap and xfm_ms_ap, seeds 0-1, first and last level, into the full run's cache), 3120 in pass 1, 1040 in pass 2; 0 unfit, 0 errors |
| model fits | 771 per variant (composed folds fit 2 or 3 parts) |
| pass 1 | gp_paper, krr, knn, rf, gbm, lgbm; 48 spawn workers x 2 threads, OMP_NUM_THREADS=2; 19:20:44-19:52:15 (`run.log`) |
| pass 2 | mlp, mlp_paper; 64 workers x 1 thread, OMP_NUM_THREADS=1; 19:52:15-20:19:54 |
| assembly | `--cached-only --variant-threads mlp=1,mlp_paper=1`, every job from the caches, 20:19:54-20:19:57 (the JSON's own `workers` / `pool_seconds` are this call's) |
| estimate | written into `run.log` before the start: pass 1 about 30 min, pass 2 about 35 min (from the pilot and the probe) |
| check | the gp variant's (i) median equals the library's cached calibration in all 52 columns (same rows scored, relative difference at most 2.7e-5) |
| versions | Python 3.11.15, numpy 1.26.4, scipy 1.12.0, scikit-learn 1.3.2, lightgbm 4.6.0 |

### Verdicts

N-104's rule; counts are other better / tie / GP better (a variant is comparable in a cell when it fitted every fold and
scored exactly the GP's rows -- every variant was comparable in every cell).

| comparison | (i) random hold-out, 52 cells | (ii) leave one level out, 48 cells |
| --- | --- | --- |
| best non-GP variant (lowest p90) | 5 / 20 / 27 | 17 / 28 / 3 |
| best tuned variant | 5 / 20 / 27 | 17 / 23 / 8 |
| family A best (gp_paper, krr, knn) | 0 / 5 / 47 | 10 / 18 / 20 |
| family B best (rf, gbm, lgbm) | 0 / 3 / 49 | 0 / 4 / 44 |
| family C best (mlp, mlp_paper) | 5 / 16 / 31 | 14 / 25 / 9 |
| gp_paper alone | 0 / 0 / 52 | 0 / 0 / 48 |
| krr | 0 / 5 / 47 | 10 / 18 / 20 |
| knn | 0 / 0 / 52 | 0 / 5 / 43 |
| rf | 0 / 0 / 52 | 0 / 3 / 45 |
| gbm | 0 / 2 / 50 | 0 / 3 / 45 |
| lgbm | 0 / 3 / 49 | 0 / 3 / 45 |
| mlp | 5 / 16 / 31 | 14 / 19 / 15 |
| mlp_paper | 0 / 3 / 49 | 12 / 15 / 21 |

Per table (best non-GP variant), (i) and (ii): ind_sym_ap 0 / 4 / 3 and 3 / 2 / 0; ind_sym_m10 0 / 5 / 2 and 1 / 4 / 0;
xfm_bs_ap 1 / 3 / 6 and 4 / 6 / 0; xfm_bs_m10 0 / 3 / 7 and 4 / 6 / 0; xfm_ms_ap 2 / 3 / 4 and 3 / 4 / 2; xfm_ms_m10
2 / 2 / 5 and 2 / 6 / 1.

**The ceiling.** Any tuned regressor beats the GP (by the rule, on its own) in **5 of 52** cells of (i) -- all `mlp`:
xfm_bs_ap Qp_peak, xfm_ms_ap and xfm_ms_m10 Qs_peak and SRF -- and in **18 of 48** cells of (ii) (krr in 10, mlp in 14,
both in 6). Counting the untuned variants too changes neither number. The GP has the lowest p90 of all comparable
variants in 34 of 52 (i) cells and 11 of 48 (ii) cells. The best non-GP variant is mlp in 26 / 18 cells ((i) / (ii)),
mlp_paper 13 / 17, krr 7 / 13, gbm 3 / 0, lgbm 3 / 0. Its p90 over the GP's: (i) 0.43-2.49, median 1.18, below 0.9 in 8
cells; (ii) 0.06-3.31, median 0.69, below 0.9 in 32 cells. Picking the best of eight variants per cell favours the
challenger.

Best non-GP variant per column (p90 / GP p90; the verdict of that variant):

| table | column | (i) best non-GP, p90 / GP p90, verdict | (ii) best non-GP, p90 / GP p90, verdict |
| --- | --- | --- | --- |
| ind_sym_ap | Lp_lf | krr 1.44 gp | krr 0.69 other |
| ind_sym_ap | Qp_peak | mlp 0.76 tie | krr 1.05 tie |
| ind_sym_ap | SRF_p | mlp 0.95 tie | mlp 0.94 tie |
| ind_sym_ap | Lp@28 | mlp 1.88 gp | krr 0.52 other |
| ind_sym_ap | Qp@28 | mlp 1.00 tie | krr 0.65 other |
| ind_sym_ap | Lp@112 | mlp 1.39 gp | no level |
| ind_sym_ap | Qp@112 | krr 0.97 tie | no level |
| ind_sym_m10 | Lp_lf | krr 2.03 gp | krr 0.75 other |
| ind_sym_m10 | Qp_peak | mlp 0.97 tie | mlp 0.76 tie |
| ind_sym_m10 | SRF_p | mlp 1.01 tie | krr 1.08 tie |
| ind_sym_m10 | Lp@28 | krr 2.25 gp | krr 1.33 tie |
| ind_sym_m10 | Qp@28 | mlp 0.97 tie | mlp 1.01 tie |
| ind_sym_m10 | Lp@112 | krr 1.04 tie | no level |
| ind_sym_m10 | Qp@112 | mlp 0.90 tie | no level |
| xfm_bs_ap | Lp_lf | gbm 0.97 tie | krr 0.08 other |
| xfm_bs_ap | k_lf | mlp_paper 1.26 gp | mlp_paper 0.49 tie |
| xfm_bs_ap | Qp_peak | mlp 0.83 other | mlp 0.08 other |
| xfm_bs_ap | SRF | mlp_paper 1.24 gp | mlp_paper 0.98 tie |
| xfm_bs_ap | Lp@28 | lgbm 1.11 tie | mlp_paper 0.19 other |
| xfm_bs_ap | Qp@28 | mlp 1.01 tie | mlp_paper 0.11 other |
| xfm_bs_ap | k@28 | mlp_paper 1.22 gp | mlp_paper 0.49 tie |
| xfm_bs_ap | Lp@112 | lgbm 1.39 gp | mlp_paper 0.38 tie |
| xfm_bs_ap | Qp@112 | mlp_paper 1.15 gp | mlp_paper 0.32 tie |
| xfm_bs_ap | k@112 | mlp_paper 1.19 gp | mlp_paper 0.44 tie |
| xfm_bs_m10 | Lp_lf | gbm 0.86 tie | krr 0.16 other |
| xfm_bs_m10 | k_lf | mlp_paper 1.21 gp | mlp_paper 0.68 tie |
| xfm_bs_m10 | Qp_peak | mlp 0.90 tie | mlp 0.06 other |
| xfm_bs_m10 | SRF | mlp_paper 1.23 gp | mlp_paper 1.06 tie |
| xfm_bs_m10 | Lp@28 | gbm 1.30 gp | mlp_paper 0.30 other |
| xfm_bs_m10 | Qp@28 | mlp 0.90 tie | mlp 0.11 other |
| xfm_bs_m10 | k@28 | mlp_paper 1.34 gp | mlp_paper 0.84 tie |
| xfm_bs_m10 | Lp@112 | mlp_paper 1.20 gp | mlp_paper 1.08 tie |
| xfm_bs_m10 | Qp@112 | mlp_paper 1.20 gp | mlp_paper 0.48 tie |
| xfm_bs_m10 | k@112 | mlp_paper 1.54 gp | mlp_paper 0.60 tie |
| xfm_ms_ap | Lp_lf | mlp 2.23 gp | mlp 0.91 tie |
| xfm_ms_ap | Ls_lf | krr 2.39 gp | krr 0.92 tie |
| xfm_ms_ap | k_lf | mlp_paper 1.37 gp | mlp_paper 1.12 gp |
| xfm_ms_ap | Qs_peak | mlp 0.49 other | mlp 0.19 other |
| xfm_ms_ap | SRF | mlp 0.62 other | mlp 0.17 other |
| xfm_ms_ap | Ls@28 | mlp 0.92 tie | mlp 0.61 other |
| xfm_ms_ap | Qs@28 | mlp 1.33 tie | krr 0.78 tie |
| xfm_ms_ap | k@28 | mlp 1.02 tie | mlp 0.89 tie |
| xfm_ms_ap | Ls@112 | mlp 2.49 gp | mlp 3.31 gp |
| xfm_ms_m10 | Lp_lf | lgbm 1.88 gp | mlp 0.94 tie |
| xfm_ms_m10 | Ls_lf | krr 2.14 gp | krr 1.09 tie |
| xfm_ms_m10 | k_lf | mlp_paper 1.50 gp | mlp_paper 1.07 tie |
| xfm_ms_m10 | Qs_peak | mlp 0.43 other | mlp 0.23 other |
| xfm_ms_m10 | SRF | mlp 0.67 other | mlp 0.14 other |
| xfm_ms_m10 | Ls@28 | mlp 0.91 tie | mlp 0.62 tie |
| xfm_ms_m10 | Qs@28 | mlp 1.11 tie | krr 0.72 tie |
| xfm_ms_m10 | k@28 | mlp 1.16 gp | mlp 0.71 tie |
| xfm_ms_m10 | Ls@112 | mlp 1.32 gp | mlp 2.58 gp |

The (i) wins: xfm_bs_ap Qp_peak GP 0.59 / 2.10 / 17.47 % (median / p90 / max) against mlp 0.54 / 1.74 / 15.52; xfm_ms_ap
Qs_peak 1.67 / 5.08 / 17.03 against 0.70 / 2.51 / 22.07; xfm_ms_ap SRF 1.74 / 5.38 / 22.67 against 1.22 / 3.32 / 17.54;
xfm_ms_m10 Qs_peak 1.99 / 6.52 / 20.99 against 0.73 / 2.78 / 21.84; xfm_ms_m10 SRF 1.82 / 5.43 / 18.20 against
1.18 / 3.61 / 12.21. The GP better in (ii) (3 cells): xfm_ms_ap k_lf (mlp_paper, ratio 1.12), xfm_ms_ap and xfm_ms_m10
Ls@112 (mlp, 3.31 and 2.58).

Largest differences (best non-GP p90 / GP p90; median / p90 / max relative error in %):

| direction | cell | GP | best non-GP | ratio |
| --- | --- | --- | --- | ---: |
| challenger lower | xfm_bs_m10 Qp_peak (ii) | 9.24 / 37.39 / 87.53 | mlp 0.78 / 2.31 / 8.15 | 0.06 |
| challenger lower | xfm_bs_ap Qp_peak (ii) | 7.93 / 33.54 / 58.76 | mlp 0.70 / 2.70 / 8.65 | 0.08 |
| challenger lower | xfm_bs_ap Lp_lf (ii) | 2.32 / 16.22 / 21.29 | krr 0.41 / 1.32 / 3.93 | 0.08 |
| challenger lower | xfm_bs_m10 Qp@28 (ii) | 7.20 / 28.30 / 60.90 | mlp 1.01 / 3.04 / 18.97 | 0.11 |
| challenger lower | xfm_bs_ap Qp@28 (ii) | 5.80 / 27.11 / 74.83 | mlp_paper 1.23 / 3.07 / 18.14 | 0.11 |
| GP lower | xfm_ms_ap Ls@112 (ii) | 0.53 / 2.03 / 5.75 | mlp 1.63 / 6.74 / 10.79 | 3.31 |
| GP lower | xfm_ms_m10 Ls@112 (ii) | 0.49 / 1.76 / 6.83 | mlp 1.36 / 4.54 / 13.36 | 2.58 |
| GP lower | xfm_ms_ap Ls@112 (i) | 0.53 / 2.71 / 139.47 | mlp 1.65 / 6.75 / 276.57 | 2.49 |
| GP lower | xfm_ms_ap Ls_lf (i) | 0.18 / 0.73 / 3.58 | krr 0.62 / 1.75 / 6.64 | 2.39 |
| GP lower | ind_sym_m10 Lp@28 (i) | 0.12 / 0.89 / 6.59 | krr 0.44 / 1.99 / 6.55 | 2.25 |

Overshoot (a variant's max above 3 x the GP's): 288 cell-variants (gp_paper 86, knn 67, lgbm 32, gbm 31, rf 29, krr 24,
mlp_paper 17, mlp 2); 4 are the best non-GP variant of their cell: ind_sym_ap Lp@28 (ii) krr max 27.96 % against
7.92 % and Qp@28 (ii) krr 76.80 % against 10.05 % (both counted as wins above), xfm_ms_ap Qs@28 (i) mlp 112.37 % against
34.52 %, xfm_ms_m10 Lp_lf (i) lgbm 24.79 % against 4.80 %.

### Cost per model

Wall-clock seconds of a job's fits (the inner cross-validation included; composed folds 2-3 parts, per_nt models every
turns level); the GP's from N-104's run (48 concurrent jobs); pass 1 at 48 x 2 threads, the MLPs at 64 x 1.

| variant | jobs | fits | fit total (s) | of it inner CV (s) | per job median / p90 / max (s) | predict total (s) |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| gp | 587 | 771 | 79 268 | -- | 38.13 / 402.09 / 747.26 | 5.4 |
| gp_paper | 587 | 771 | 4 751 | -- | 1.79 / 25.40 / 86.71 | 15.5 |
| krr | 587 | 771 | 2 022 | 1 993 | 1.25 / 8.96 / 24.52 | 1.2 |
| knn | 587 | 771 | 35 | 34 | 0.06 / 0.11 / 0.19 | 0.7 |
| rf | 587 | 771 | 15 805 | 15 099 | 25.68 / 42.30 / 70.25 | 18.6 |
| gbm | 587 | 771 | 68 714 | 68 124 | 97.89 / 207.44 / 324.60 | 15.8 |
| lgbm | 587 | 771 | 9 261 | 9 102 | 12.38 / 29.18 / 43.77 | 2.8 |
| mlp | 587 | 771 | 68 386 | 64 946 | 101.23 / 265.52 / 379.97 | 7.6 |
| mlp_paper | 587 | 771 | 55 034 | -- | 96.73 / 178.13 / 269.49 | 10.7 |

### Deviations from this specification

1. **Family A, the coordinator's decisions of 2026-10-13 (3675e3d).** `gp_paper` standardizes the target inside the model
   (`normalize_y=True`; the paper's kernel, alpha 1e-8, no restarts and standardized inputs kept), so it measures the kernel
   configuration rather than the units of the library's log target. Note, not a variant: as the table first said (target
   not standardized) its p90 was about 1e12 % on the inductance columns (`pilot_before_decisions.json`: ind_sym_ap Lp_lf
   (i) 1.3e+12 %, Lp@112 (i) 3.5e+12 %), 92-96 % on the inductor Q / SRF columns and 248-440 % on the k columns of
   xfm_bs_ap / xfm_ms_ap (`scratch/.../side/side.json`). `krr` fits the centred target (each fit, the inner ones included).
   Grids widened where they hit an edge: `krr` gamma_d {1/16 ... 64} (11) x alpha {1e-10 ... 1e-2} (5) = 55
   configurations, `knn` n_neighbors {1, 2, 3, 5, 8, 12, 16}.
2. **Families B and C**: the grid changes (gbm's min_samples_leaf axis, rf's max_features values), the `mlp` grid cut
   (hidden_layer_sizes (256, 256)) and the rebuilt `mlp_paper` are in Record (B) and Record (C).
3. **Two passes, per-variant threads.** At the coordinator's request the MLPs ran at 1 thread (OMP_NUM_THREADS=1) on 64
   workers, the rest at 2 on 48; both into one job cache, the JSON assembled from it with `--cached-only` (which refuses to
   fit). A non-GP job's thread count is part of its cache key (an lbfgs MLP's result depends on it); the GP keys are
   N-104's, unchanged.
4. **The estimate probe's jobs are in the result.** 536 jobs (xfm_bs_ap and xfm_ms_ap, seeds 0-1, first and last level,
   every non-GP variant) were fitted at 19:09-19:20 with the same code, threads and worker counts, into the full run's
   cache, and the passes reused them; the pilot (ind_sym_ap) ran in its own cache.
5. **Harness additions beyond section 4**: the registry also takes N-104's `{"model": class}` shape and a module's
   `SKIPPED` / `NOTES`; every `regressor_base.tune` call and every fitted model's own report (`chosen`, `cv_rmse`,
   `cv_scores`, `inner_scores`, `timing`, `fit_info`, `kernel_fitted`) recorded per job (the JSON keeps the tuned
   variants' chosen configuration per fold, the job cache everything); warnings counted per job, not printed; a non-GP
   job's unexpected exception would be recorded as `error` and not cached (none occurred); `--variant-threads`, `--cache`,
   `--cached-only`, `--gp-cache`, `--n104-json`. The verdict labels read `other_better` / `tie` / `gp_better`; the rule is
   N-104's. "Best non-GP" includes the untuned `gp_paper` and `mlp_paper`; "best tuned" is counted separately.
6. **The figure** draws, per cell, the GP and the best comparable variant of each family (labelled with its name).
7. **`RESULT_CN.md`** is `report`'s output plus a hand-written run section at its end.

### Not foreseen

- **`gp_paper` loses every cell even with the target standardized** (p90 median 110.37 % in (i), 92.96 % in (ii); range
  18.74-1856.10 %): in 1463 of its 1571 (sub-)model fits the optimiser leaves the RBF length scale at its lower bound
  1e-5, a kernel that correlates no two distinct inputs; 5006 ConvergenceWarnings.
- **The tuned MLP is the strongest challenger, and the wins sit on the GP's weakest columns.** Its (i) wins are the Q-peak
  and SRF columns of the transformer tables, where the GP's (i) p90 is 2.10-6.52 % (its median over all 52 (i) cells:
  2.22 %); its (ii) wins where the GP's held-out-level errors are large (xfm_bs Qp_peak p90 33.54 / 37.39 %, Qp@28 27.11 /
  28.30 %, Lp_lf 16.22 / 9.18 %; xfm_ms SRF 21.56 / 26.98 %). The GP's (ii) p90 has median 6.32 %, max 37.39 %; mlp's
  4.14 % and 32.62 %; mlp_paper's 6.78 % and 40.83 %.
- **The trees never beat the GP** (0 wins per variant in either protocol); in (ii) their p90 medians are 30.81-31.68 %
  against the GP's 6.32 % (a piecewise-constant model has nothing to say between the levels around a held-out one -- a
  reading, not tested). Their grids sit on an edge: rf min_samples_leaf 1 in 1566 of 1571 tuned fits and max_features 1.0
  in 1478 (both the bounds of what the parameter allows); gbm min_samples_leaf 5 in 1551, max_iter 1000 in 1133,
  learning_rate 0.1 in 1076, max_leaf_nodes 7 in 1036; lgbm min_child_samples 5 in 1551, n_estimators 1000 in 1101,
  num_leaves 7 in 1054, learning_rate 0.1 in 1055. Whether a wider grid lifts them is untested.
- **`krr` still meets its widened grid's edges**: alpha 1e-10 in 426 of 1571 fits, gamma_d 1/16 in 250 and 64 in 153;
  `knn` 1 in 59 and 16 in 23. Hypothesis, untested: the GP's per-dimension length scales (KernelRidge's RBF kernel has
  one) are why `krr` is far behind the GP on the transformer tables in (i) (krr p90 median 11.71 % over all (i) cells).
- **`krr` brings the first non-GP wins on the inductor tables** (N-104 had none): (ii) ind_sym_ap Lp_lf (GP 0.37 / 1.33 /
  6.72 %, krr 0.23 / 0.91 / 2.79 %), Lp@28, Qp@28 and ind_sym_m10 Lp_lf; in two of them its max error is over 3 x the GP's
  (above). In (i) the inductor tables give the GP 5 cells and ties the rest.
- **`mlp_paper`'s batch of 64 is larger than some per_nt levels' rows**: scikit-learn clips it (63 UserWarnings, inductor
  and xfm_ms Qs@28 jobs).
- **The host was shared**: other users' Spectre jobs ran throughout (load average 53-128 while the pilot and the probe ran).

## Record (B)

Branch `n107-b-trees` from `main` at 5444ca2: the module `docs/refactor/reports/library_query/regressors_trees.py` in
171ae9f, the first record in 2cd8eed, the coordinator's two grid changes (below) and this record in the commit after it.
Nothing merged, nothing pushed, nothing under `src/` or in `BACKLOG_CN.md` changed, `regressor_base.py` untouched. Logs
and the side-check scripts in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n107_regressor_study/scratch/n107-b-trees/`
(the logs of the grids before the change in `before_grid_change/`). The library's `.cache` held 1118 files (directory
time 17:16:51) before and after every run here; the Library wrote nothing to the scratch directory (it read the datasets
from the library's own cache).

What the module is: `StratumRF`, `StratumGBM`, `StratumLGBM` on a common `TunedMean(StratumMean)` whose `_fit_joint` runs
`tune` (3 folds, seed 0) over the class's grid -- the values in the order listed, the first axis slowest -- and refits
with the configuration chosen; a per_nt model tunes each turns level on that level's rows. A fitted model gives `chosen`
(a dict; `{level: dict}` for per_nt), `cv_rmse`, `cv_scores` and `timing` (`tune_s`, `refit_s`). `VARIANTS`:
`{name: {"model": class, "family": "B", "inputs": "library", "tuned": True, "grid": [...]}}`; `SKIPPED` stays empty
(lightgbm 4.6.0 imports).

Grids changed from section 2's table at the coordinator's request (2026-10-13), so that each model is measured at its
tuned best:
- `gbm`: `min_samples_leaf` in {5, 20} added as the last axis (48 configurations; the other axes unchanged). The side
  probe that prompted it (`t_gbm_leaf.py`: section 2's grid with `min_samples_leaf=5` instead of sklearn's default 20,
  the smoke's split) gave ind_sym_ap Lp_lf median 1.922 % / p90 7.873 % against 7.361 / 29.991 with the default, and
  xfm_bs_m10 k_lf 0.979 / 2.656 against 0.869 / 2.706: the default 20 leaves a per_nt inductor model (475 training rows
  over 5 turns levels, two thirds of a level in each inner fold) a few leaves per tree. The grid now holds both values;
  the smoke below chose 5 at every level and on k_lf.
- `rf`: `max_features` in {1.0, 0.67, 0.33} instead of {1.0, 0.5, "sqrt"}. The old 0.5 and "sqrt" give the same feature
  count up to 5 kept inputs (identical `cv_scores` measured on xfm_bs_m10 k_lf), so 3 of the 9 configurations repeated
  3 others on four of the six tables. The new values give 3 / 2 / 1 features on 3 inputs, 5 / 3 / 1 on 5, 7 / 4 / 2 on
  7 (sklearn: `max(1, int(f x d))`); they coincide only at 2 kept inputs (1 and 1). Nine distinct `cv_scores` on k_lf.

Smoke with the changed grids (`regressors_trees.py ROOT SCRATCH TABLE COLUMN --gp`; seed-0 `gp.split`, 80 / 20; 2 threads,
OMP_NUM_THREADS=2, Python 3.11.15, numpy 1.26.4, scikit-learn 1.3.2; the two columns ran side by side; `gp` is the
library's GP on the same split, for reference). Relative errors in %, times in s (fit = inner cross-validation + refit):

| column | variant | configs | median | p90 | max | fit | tune | refit | chosen |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ind_sym_ap Lp_lf (594 rows, 475 / 119, per_nt over 5 turns levels) | gp | | 0.088 | 0.642 | 3.686 | 0.86 | | | |
| | rf | 9 | 3.952 | 10.312 | 17.959 | 33.54 | 32.33 | 1.21 | max_features 1.0, min_samples_leaf 1 at every level |
| | gbm | 48 | 1.922 | 7.873 | 36.913 | 100.42 | 99.97 | 0.45 | min_samples_leaf 5 at every level; max_leaf_nodes 7 (15 at level 5), 300 iterations, lr 0.1 at levels 1-2, 0.03 at 3-5 |
| | lgbm | 24 | 1.484 | 7.842 | 36.913 | 12.45 | 12.27 | 0.18 | lr 0.03, min_child_samples 5 everywhere; num_leaves 7-15, 300 or 1000 trees |
| xfm_bs_m10 k_lf (2211 rows, 1769 / 442, joint, 5 dimensionless features) | gp | | 0.402 | 1.356 | 15.253 | 96.12 | | | |
| | rf | 9 | 1.650 | 5.100 | 17.023 | 13.91 | 13.10 | 0.81 | max_features 1.0, min_samples_leaf 1 |
| | gbm | 48 | 0.979 | 2.656 | 13.727 | 66.98 | 66.67 | 0.30 | lr 0.1, max_leaf_nodes 7, 1000 iterations, l2 0, min_samples_leaf 5 |
| | lgbm | 24 | 0.964 | 2.541 | 12.278 | 10.06 | 9.97 | 0.10 | lr 0.1, num_leaves 7, 1000 trees, min_child_samples 5 |

`rf` chose max_features 1.0 in both smokes, before and after the change, so its errors are the same as with the old
grid; `gbm`'s fit time grew with its grid (before: 30.54 s on Lp_lf, 31.55 s on k_lf, 24 configurations).

Checks: every configuration of the three changed grids fitted twice on the xfm_bs_m10 k_lf split predicts bitwise the
same, and two whole `rf` fits give identical `cv_scores`; a composed column through the harness's rebinding
(`gp.StratumGP` bound to `StratumLGBM` around `composed.holdout_fold`, xfm_bs_m10 Lp@28, resonance, seed 0) fitted its
three parts and scored 413 rows (median 0.448 %, p90 1.855 %) in 29.3 s, the class restored after it.

Deviations, both in how a fit runs, not in what is fitted: the forest predicts with one thread (`n_jobs=1` set on the
fitted forest; the fit uses `n_threads`) -- sklearn's threads add the trees' predictions in the order they finish, and
two runs were not bitwise equal in any of the 9 configurations (2.2e-16 the largest difference in each of the three the
check printed), enough to flip `tune`'s first-best tie between two configurations that grow the same trees;
`HistGradientBoostingRegressor` has no `n_jobs`, so its OpenMP threads are capped at `n_threads` with
`threadpool_limits` around each fit and prediction.

Not foreseen otherwise: ruff 0.16.8 reported BLE001 on `regressor_base.py` line 115 (`except Exception` in `tune`); part
A has fixed it on its branch.
## Record (C)

Branch `n107-c-neural` from `main` at 5444ca2: `regressors_neural.py` in 7283573, this note in e818345, then the
coordinator's two changes (`mlp_paper` rebuilt as PACOSYT's code builds it; every fit and prediction under
`threadpool_limits(n_threads)`) with this note's update in one commit. Nothing merged or pushed, nothing under `src/` or in
`BACKLOG_CN.md`, `regressor_base.py` untouched. The library was only read (no file under `n28_v2` newer than the first run;
its `.cache` still 1118 files). Evidence in
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n107_regressor_study/scratch/n107-c-neural/`:
`smoke_<table>_<column>_threads{1,2}.log` (the module as committed), `*_fullgrid.log` (before the grid cut),
`superseded_mlp_paper_v1/` (the first `mlp_paper`), `timing_composed_*.log`, `side_blas_threads.log` and the scripts in
`side_scripts/`. Python 3.11, numpy 1.26.4, scipy 1.12.0, scikit-learn 1.3.2, `OMP_NUM_THREADS=2`; the machine was shared
with A's and B's work (load average 16-53), and the times carry that.

For the harness: `VARIANTS[name]` holds `model` (the class), `family` "C", `inputs` "library", `params` {} (constructor
keywords beyond the library's settings), `grid` (None for `mlp_paper`) and `grid_cut`; a model is
`cls(**settings, **params, n_threads=threads)`. A fitted model has `chosen`, `inner_scores` and `fit_info`, JSON-ready; a
per_nt model answers per turns level (`{"1": ..., "2": ...}`), a composed column's parts each answer for themselves.

Smoke (`gp.split` seed 0, 80 / 20; the GP's fold on the identical rows read from N-104's job cache for reference; cores =
CPU seconds of the process over the fit's wall seconds):

| column | variant | threads | chosen | median | p90 | max | fit | cores |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ind_sym_ap Lp_lf (per_nt, 475 / 119 rows) | mlp | 2 | turns 1: (128, 128) 1e-5 tanh; 2, 3: (64, 64) 1e-5 tanh; 4, 5: (64, 64) 1e-3 tanh | 0.42 % | 1.31 % | 4.74 % | 14.8 s | 2.72 |
| | mlp | 1 | the same | 0.42 % | 1.31 % | 4.74 % | 15.0 s | 1.00 |
| | mlp_paper | 2 / 1 | the paper's | 1.57 % | 4.05 % | 7.78 % | 14.7 / 12.8 s | 2.00 / 1.00 |
| | gp (N-104) | | | 0.088 % | 0.64 % | 3.69 % | 1.0 s | |
| xfm_bs_m10 k_lf (joint, feature map, 1769 / 442 rows) | mlp | 2 | (128, 128) 1e-3 tanh | 2.04 % | 6.10 % | 12.9 % | 114.7 s | 2.71 |
| | mlp | 1 | (64, 64) 1e-3 tanh | 1.22 % | 4.09 % | 11.3 % | 106.5 s | 1.00 |
| | mlp_paper | 2 / 1 | the paper's | 0.52 % | 1.80 % | 14.7 % | 57.0 / 50.9 s | 2.00 / 1.00 |
| | gp (N-104) | | | 0.40 % | 1.36 % | 15.3 % | 209.8 s | |

**Fit times and the grid cut.** With the specification's 12 configurations, xfm_bs_m10 k_lf's inner cross-validation
(three fits on about 1180 rows each) took 6.8-8.9 s per (64, 64) configuration, 15.1-19.4 s per (128, 128) one and 57.0 /
57.5 / 63.3 / 70.6 s per (256, 256) one -- two over the minute, the four together 248 of the 354 s; the fold job 364 s. A
composed fold job builds two or three tuned parts: xfm_bs_m10 Lp@28 (resonance; parts on 1798 / 1499 / 1651 rows) took
651 s, over the 10 minutes. So `mlp` runs without hidden_layer_sizes (256, 256): 8 configurations. Then k_lf's fold job
took 115.8 s and Lp@28's 214.2 s, with the held-out errors of all three runs unchanged (Lp@28 0.8278 % / 2.7474 % both
times): no model had chosen (256, 256). The cut is for cost; that nothing chose it is an observation on three columns, not
its reason. `mlp_paper` (1000 epochs) fits k_lf in 51-57 s and Lp@28's composed fold job in 160.2 s (1.43 % / 4.62 %).

**`mlp_paper`.** The first version (7283573, the specification's call: standardized inputs, polynomial features, adam at
scikit-learn's defaults, the target as is) was a strawman: 25.4 % / 102.7 % on Lp_lf and 9.82 % / 40.6 % on k_lf, adam
stopped by scikit-learn's default early stop after 221-285 epochs on the inductor levels and 40 on k_lf. Rebuilt after
the coordinator read PACOSYT's `src/models/ann_tf.py` and the notebook's ANNModel: `MinMaxScaler((1, 2))` on the scaled
library inputs, `PolynomialFeatures(5)` with its bias column, `StandardScaler`; the target standardized inside the model;
`MLPRegressor((256, 512), relu, adam, learning_rate_init 1e-4, batch_size 64, alpha 1e-6, max_iter 1000,
n_iter_no_change 1000, tol 0)`, so all 1000 epochs run (every fit above ran 1000), no early stop, as Keras's fit. It now
reaches 1.57 % / 4.05 % on Lp_lf and 0.52 % / 1.80 % on k_lf -- on k_lf below the tuned `mlp` and near the GP's fold
(0.40 % / 1.36 %). Its results did not change between 1 and 2 threads.

**Threads.** Every fit and prediction of both variants runs under `threadpool_limits(limits=n_threads)`. At 1 thread
both use 1.00 core (the four smoke fits above). At 2 threads `mlp_paper` uses 2.00 cores, `mlp` 2.71-2.72: the
environment loads two OpenBLAS copies (numpy's for the matrix products, scipy's for L-BFGS-B's vector work) and the limit
holds each to 2 threads, so lbfgs keeps a third core busy; adam does not touch scipy's. A second thread does not pay for
these sizes (k_lf: `mlp` 106.5 s at 1 thread against 114.7 s at 2, `mlp_paper` 50.9 s against 57.0 s), so `--threads 1`
for the neural jobs keeps a job at one core. The lbfgs path depends on the thread count: k_lf's `mlp` chose (64, 64) at 1
thread and (128, 128) at 2 (1.22 % / 4.09 % against 2.04 % / 6.10 %; Lp_lf identical), so a fold result repeats only at
the same `--threads`. The GP (scipy's L-BFGS-B and BLAS too) was not measured.

**Also seen.** `mlp` stays behind the GP on both columns; tanh won every inner cross-validation (12 models of 12 across
both thread counts); lbfgs ran all 2000 iterations on k_lf but stopped at 59-105 on the inductor levels (scikit-learn's
`tol` 1e-4 is lbfgs's gradient tolerance). `torch` 2.12.0+cpu is installed in the environment (section 4 says it is
not); nothing here uses it. `regressor_base.py`: ruff 0.16.8 reports BLE001 (blind `except Exception`) in `tune`, line
115 -- the catch-all is deliberate and wants a `# noqa: BLE001`; left to A.

**Deviations.** The grid cut above. In `mlp_paper`: Keras's activity L2 1e-3 on each hidden layer has no scikit-learn
equivalent and is left out; scikit-learn's `alpha` (0.5 x alpha x sum W^2 / rows, on every layer's weights) is not
Keras's kernel `l2(1e-6)` (1e-6 x sum W^2 per batch); the min-max scaling acts on the library's scaled inputs (after the
feature map or min-max scaling and the dropped constant inputs; min-max of a column's increasing affine image is the same
column), so the inputs are the library's, as section 2 asks. `ConvergenceWarning` is silenced; the iteration counts are
in `fit_info`.