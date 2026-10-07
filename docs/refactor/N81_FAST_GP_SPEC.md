# N-81 — the metric models' fit without sklearn's gradient array: the same model, 4 to 14 times less CPU

Status: specification (2026-10-07), for the coding subagent; the polish batch (`BACKLOG_CN.md` 0.13, item 8). The
user's condition (2026-10-07): "不影响现有功能的情况下按照建议" -- the proposals must not change.

What it is built on: `metric_gp` fits one Gaussian process per metric (`ic_opt.suggesters.metric_gp.models.fit_metric`:
`ConstantKernel × Matern 5/2 + WhiteKernel`, the length and noise priors, the MAP optimizer `map_optimizer`, three
L-BFGS-B starts) through sklearn's `GaussianProcessRegressor`, which builds an `(n, n, parameters)` gradient array at
every likelihood evaluation. On a real circuit's history (12 constrained metrics, 20 variables) a proposal costs about
100 seconds once 400 designs are in the history, almost all of it in these fits (`optimizer_research/pvt_bench/METHODS_REPORT_CN.md`
section 6: 254 of 269 seconds on 3000 designs). The research copy `optimizer_research/pvt_bench/offline/models4.py`
(`class FastGP`) computes the same log marginal likelihood and its gradient directly: the per-coordinate squared
differences kept once for the upper triangle, every gradient entry one matrix-vector product; the same kernel, bounds,
priors and the product's own `map_optimizer` (same starts, same random draws). Checked against the product's fit
(`s4_fastgp_check.py`, `results/s4_fastgp_check.csv`): on 25 to 200 points the hyperparameters agree to 1e-11 or
better; on 775 points they differ by at most 0.005 in log, the predicted means by at most 0.2 % of the target's spread;
CPU 3.9 to 14.4 times less.

Read before writing: `src/ic_opt/suggesters/metric_gp/models.py` (whole file), `__init__.py` and `select.py` (how
`MetricModel.predict` and the value model are used), `optimizer_research/pvt_bench/offline/models4.py` (`FastGP`,
read-only; the research directory is not part of the repository), `optimizer_research/pvt_bench/offline/s4_fastgp_check.py`,
`tests/ic_opt/test_metric_gp*.py`, `tests/ic_opt/test_replay_parity.py`, `tests/ic_opt/test_strategy_threads.py` (or
whatever pins `simulator.strategy_threads`, T17.12), `docs/refactor/T17_12_THREADS_SPEC.md`.

## 1. What changes

`fit_metric` fits its Gaussian process with the direct computation (port `FastGP` into `models.py`, product style:
no research naming, docstrings in the repository's voice), keeping:

- the kernel, its bounds, the priors (`length_prior`, `NOISE_PRIOR`), `map_optimizer` with its starts and random
  draws, the standardization of the targets, `SK_ALPHA` on the diagonal;
- `MetricModel`'s interface and what it holds (`predict` mean and standard deviation per point, the length scales
  `region.weights` reads, anything else `__init__.py` / `select.py` / `compose.py` / `digest.py` read from a fitted
  model) -- find every reader with grep and keep each one working;
- the thread accounting of T17.12 (`strategy_threads`: BLAS threads limited through threadpoolctl): the new fit uses
  numpy / scipy only, within the same limit;
- the value model (`fit_value_model`, sklearn's `GaussianProcessClassifier`) unchanged: it is not in this ticket.

A fitted model's hyperparameters and predictions must equal the sklearn fit's on the histories the product's tests and
replays hold (a few hundred points at most): the replay-parity tests (`test_replay_parity.py`, every recorded run) must
pass unchanged -- they are the proof that the proposals did not change. Beyond those sizes the two fits may differ at
the level the research measured; document it in `models.py`.

## 2. Tests (`tests/ic_opt/test_metric_gp_fit.py`)

1. On the metric histories the existing metric_gp tests build (and one synthetic history of 300 points, 8 variables,
   a known function plus noise, seeded): the new fit's hyperparameters equal the sklearn fit's to 1e-8 (keep a
   test-only sklearn fit, or the old `fit_metric` under a private name, for the comparison), the predicted means and
   standard deviations to 1e-9 relative; the likelihood evaluations counted by the optimizer are the same number.
2. Degenerate histories (fewer than 2 points, constant targets, a single variable) behave as before.
3. The replay-parity tests and every `test_metric_gp*.py` pass unchanged.
4. Timing (not a pass/fail assertion -- a printed number): the fit of the 300-point history, sklearn against the new
   one, CPU seconds, so the record states the gain measured here.
5. `ruff check src tests` clean.

## 3. Documents

`models.py` module docstring (what is computed directly and why, the agreement measured); `docs/refactor/T17_OPTIMIZER_PLAN_CN.md`
is the coordinator's; this file gets `## 5. Record`.

## 4. Working rules for the coder

Branch `n81-fast-gp` in the worktree `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n81-fast-gp` (from main;
`git merge --ff-only main` first). Python `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python` with
`PYTHONPATH=<worktree>/src`; never `uv run`, `uv sync` or pip. Run `tests/ic_opt/test_metric_gp*.py`, the new test,
`tests/ic_opt/test_replay_parity.py`, `tests/ic_opt/test_optimize*.py` and `ruff check src tests`; all clean before
committing. No simulator. Nothing outside the worktree is written (the research directory is read-only). Do not edit
`docs/refactor/BACKLOG_CN.md`. Commit style of the repository, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
Do not merge or push. If the replay-parity tests cannot pass with the new fit (a proposal differs), stop, keep the old
fit as the default behind a module switch, and report exactly where the proposals diverged.
