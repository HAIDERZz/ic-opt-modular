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

## Record (C)

Branch `n107-c-neural` from `main` at 5444ca2: `regressors_neural.py` in 7283573, this note after it. Nothing merged or
pushed, nothing under `src/` or in `BACKLOG_CN.md`, `regressor_base.py` untouched. The library was only read (no file under
`n28_v2` newer than the first run; its `.cache` still 1118 files). Evidence in
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n107_regressor_study/scratch/n107-c-neural/`: the smoke logs (and
`*_fullgrid.log` from before the cut), `timing_composed_*.log`, the side checks `side_*.log` and their scripts in
`side_scripts/`. Python 3.11, numpy 1.26.4, scipy 1.12.0, scikit-learn 1.3.2, `OMP_NUM_THREADS=2`, 2 BLAS threads per fit;
the machine was shared with A's and B's work (load average 16-53), and the times carry that.

For the harness: `VARIANTS[name]` holds `model` (the class), `family` "C", `inputs` "library", `params` {} (constructor
keywords beyond the library's settings), `grid` (None for `mlp_paper`) and `grid_cut`; a model is
`cls(**settings, **params, n_threads=threads)`. A fitted model has `chosen`, `inner_scores` and `fit_info`, JSON-ready; a
per_nt model answers per turns level (`{"1": ..., "2": ...}`), a composed column's parts each answer for themselves.

Smoke (`gp.split` seed 0, 80 / 20; the GP's fold on the identical rows read from N-104's job cache for reference):

| column | variant | chosen | median | p90 | max | fit |
| --- | --- | --- | --- | --- | --- | --- |
| ind_sym_ap Lp_lf (per_nt, 475 / 119 rows) | mlp | turns 1: (128, 128) 1e-5 tanh; 2, 3: (64, 64) 1e-5 tanh; 4, 5: (64, 64) 1e-3 tanh | 0.42 % | 1.31 % | 4.74 % | 14.6 s |
| | mlp_paper | the paper's | 25.4 % | 102.7 % | 1465 % | 2.4 s |
| | gp (N-104) | | 0.088 % | 0.64 % | 3.69 % | 1.0 s |
| xfm_bs_m10 k_lf (joint, feature map, 1769 / 442 rows) | mlp | (128, 128) 1e-3 tanh | 2.04 % | 6.10 % | 12.9 % | 115.8 s |
| | mlp_paper | the paper's | 9.82 % | 40.6 % | 281 % | 1.3 s |
| | gp (N-104) | | 0.40 % | 1.36 % | 15.3 % | 209.8 s |

**Fit times and the grid cut.** With the specification's 12 configurations, xfm_bs_m10 k_lf's inner cross-validation
(three fits on about 1180 rows each) took 6.8-8.9 s per (64, 64) configuration, 15.1-19.4 s per (128, 128) one and 57.0 /
57.5 / 63.3 / 70.6 s per (256, 256) one -- two over the minute, the four together 248 of the 354 s; the fold job 364 s. A
composed fold job builds two or three tuned parts: xfm_bs_m10 Lp@28 (resonance; parts on 1798 / 1499 / 1651 rows) took
651 s, over the 10 minutes. So `mlp` runs without hidden_layer_sizes (256, 256): 8 configurations. Then k_lf's fold job
took 115.8 s (inner 106.1 s, the final fit 9.7 s at 2000 iterations) and Lp@28's 214.2 s, with the held-out errors of
all three runs unchanged (Lp@28 0.8278 % / 2.7474 % both times): no model had chosen (256, 256). The cut is for cost; that
nothing chose it is an observation on three columns, not its reason. `mlp_paper` fits in seconds.

**What the runs showed.**
- `mlp` stays behind the GP on both columns (median 4.8x / 5.1x, p90 2.0x / 4.5x). tanh won every inner
  cross-validation (6 models of 6). lbfgs ran all 2000 iterations on k_lf but stopped at 59-105 on the inductor levels
  (scikit-learn's `tol` 1e-4 is lbfgs's gradient tolerance).
- `mlp_paper` as specified is far off, and adam never reached 2000 epochs: scikit-learn's default stop (training loss not
  better by `tol` 1e-4 for 10 epochs) ended it after 221-285 epochs on the inductor levels and 40 on k_lf. Side checks on
  the same folds, not in the module: with the target standardized, Lp_lf 3.01 % / 13.4 % and k_lf 4.60 % / 50.8 % (max
  3.0e13 %: degree-5 features of a row outside the training rows' standardized range); with all 2000 epochs and the
  target as is, Lp_lf 21.1 % / 105 % and k_lf 1.44 % / 8.60 % (fit 64.7 s). On the inductor the unstandardized log
  target dominates (log henries: mean -20.86, std 1.39 on the training rows), on k_lf (mean -0.88) the early stop.
  PACOSYT modelled S-parameters, of order 1; whether its Keras notebook ran a fixed number of epochs I could not check
  (no copy here). The module keeps the specification's call; changing it is the coordinator's decision.
- Threads: the environment loads two OpenBLAS copies (numpy's, scipy's) and scikit-learn's libgomp. Capped at 2, an lbfgs
  MLP keeps about 3 cores busy (2.99 measured on a 1769 x 5 synthetic fit; the smoke process showed three threads at 100
  %) and is not faster than at 1 ((128, 128) 1.60 s at 2 against 1.09 s at 1; (256, 256) 4.38 against 4.48 s). So
  `--workers 48 --threads 2` would hold about 144 cores while MLP jobs run, above the 96 allowed; and the lbfgs path
  depends on the thread count (281 / 253 iterations at 2 / 1 threads on the same data), so a fold result repeats only at
  the same `--threads`. The GP (scipy's L-BFGS-B and BLAS too) was not measured.
- `torch` 2.12.0+cpu is installed in the environment (section 4 says it is not); nothing here uses it.
- `regressor_base.py`: ruff 0.16.8 reports BLE001 (blind `except Exception`) in `tune`, line 115 -- the catch-all is
  deliberate and wants a `# noqa: BLE001`; left to A.

**Deviations.** The grid cut above. In `mlp_paper` the standardization acts on the library's scaled inputs (after the
feature map or min-max scaling and the dropped constant inputs; standardizing an affine image of a column gives the same
column), `PolynomialFeatures` keeps scikit-learn's bias column, and scikit-learn's `alpha` (0.5 x alpha x sum W^2 / rows)
is not Keras's `l2(1e-6)` (1e-6 x sum W^2 per batch) -- the specification's call, noted. `ConvergenceWarning` is silenced;
the iteration counts are in `fit_info`.
