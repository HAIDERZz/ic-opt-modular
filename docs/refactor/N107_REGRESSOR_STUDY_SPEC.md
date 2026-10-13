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
