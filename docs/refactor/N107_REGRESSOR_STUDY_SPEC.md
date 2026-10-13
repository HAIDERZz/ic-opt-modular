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