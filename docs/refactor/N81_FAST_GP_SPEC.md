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

## 5. Record

Status: done on branch `n81-fast-gp` (2026-10-07), not merged. Code and tests: commit `094799d`; this record: the
commit after it. **The condition did not hold for the direct fit as the default, so the old fit stays the default**
(section 4's rule), behind the module switch `models.DIRECT_LIKELIHOOD = False`; the direct search is complete and
tested with the switch on.

What was built (section 1):

1. `models.Likelihood(x, z)`: the negative log marginal likelihood of the metrics' kernel and its gradient in
   `theta`, called by `map_optimizer` exactly as sklearn's `obj_func` (+inf and a zero gradient where the kernel
   matrix does not factor). The squared differences per variable are computed once for the pairs i < j; each
   evaluation is `pdist` of the scaled inputs, the kernel matrix (`squareform`), its Cholesky factor, `alpha`, `K^-1`,
   and einsum sums over the pairs for the gradient. `models.metric_kernel(d)` holds the kernel (factored out of
   `fit_metric`), `SK_ALPHA` the diagonal term.
2. `fit_metric`: with the switch off, the pre-N-81 code path (bit for bit, test 0 of the new file). With it on,
   `map_optimizer(kernel, d, rng)(Likelihood(x, z), kernel.theta, kernel.bounds)` (the same starts and random draws),
   then sklearn's regressor fitted once at the result with `optimizer=None`, and `log_marginal_likelihood_value_` set
   to the optimum's log posterior as sklearn's own search leaves it.
3. Readers (grep): `region.weights` (`length_scales`), `select.samples` (`gp is None`, `joint`: `gp.kernel_.k1`,
   `X_train_`, `L_`, `alpha_`), `select.select_batch` (`transform.backward`, `constant`, `name`), `MetricModel.predict`;
   `compose.py` and `digest.py` read nothing of a fitted model. The fitted model is still sklearn's regressor, so none
   changed. The value model is untouched. Threads: numpy / scipy only, inside T17.12's `threadpool_limits`.

Where the port differs from the research's `FastGP`, and why:

- `K^-1` by `cho_solve(L, I)`, as sklearn, not LAPACK `dpotri`: OpenBLAS's `dpotri` changed its result with the BLAS
  thread count already at 40 points, where sklearn's path is bit for bit the same at any thread count; with it the
  direct fit's hyperparameters differed between one and four threads on the small test histories (7.9e-6). With
  `cho_solve` the direct fit is thread-invariant where sklearn's is. Cost: about 1.5 times the evaluation at 300 points.
- The kernel matrix from `pdist` of the scaled inputs, sklearn's arithmetic, not from the stored squared differences:
  the value is then sklearn's bit for bit and the factorization fails exactly where sklearn's does (with the research's
  route the values differed by 1e-8 and the gradients by 1e-6 at ill-conditioned points).
- Sums over pairs by `einsum`, not BLAS `gemv` / `dot`, whose order of summation follows the number of threads; the
  pair weight is `W_ij + W_ji` (sklearn sums both triangles of a `K^-1` that is symmetric only to rounding).
- The posterior is sklearn's regressor refitted at the hyperparameters (one more factorization per fit), not `FastGP`'s
  own prediction: predictions, `joint` and the length scales come from the same code as before.

Measured:

- The test histories (the ten metric histories of `test_metric_gp.py`'s scenarios and the synthetic 300 x 8): the
  likelihood equals sklearn's bit for bit at every point sklearn's optimizer visits; the gradient within 4e-14 of its
  largest entry at the priors' mode, up to 1.3e-6 where the noise level nears 1e-8. Hyperparameters: 6 of the 10
  histories within 1.1e-11 in log (5 within 4.1e-13) with the same number of evaluations; the three ill-conditioned
  ones (noise 3e-8 to 4.5e-5) 1.5e-6 to 1.1e-5, evaluations 81 / 80, 72 / 66, 81 / 58; the synthetic 300 1.2e-6,
  150 / 150. Log posterior at
  the optimum within 5e-9 relative; means within 1.2e-7 (standardized), standard deviations within 6e-6 relative.
- 34 recorded metric_gp runs in `ic-opt-accept` (read only; the library's cache redirected to a scratch directory,
  nothing written there, checked by modification time), one BLAS thread, at every history size a metric_gp batch was
  proposed at (seed 0): 249 of 249 compared proposals identical (2 batches of `n95/unpacked/mixer_lib` not compared: its
  library path is the macOS machine's). 1131 fits of up to 92 points: 930 (82 %) within 1e-8 in log, 983 within 1e-6,
  1123 (99.3 %) within 1e-4, one above 1e-3: `t17_confirm/mixer112g/metric_gp_s1` at 70 points, metric BW (4
  variables, the noise level at its bound, two length scales at 200), another local optimum with log posterior 286.746
  against sklearn's 285.897 (the direct one higher), predictions apart by up to 2.5 spreads far from the data; that
  batch's proposal was the same. Equal evaluations in 1058 fits.
- **Where the proposals diverged.** Synthetic benchmark runs: `syn_amplifier_like`, `syn_levy20_c1`,
  `syn_ackley10_c2`, `syn_failure_region` (`icopt_bench.synthetic`), seed 1, metric_gp with the direct fit, batches of
  10, one BLAS thread; at 100, 150, ..., 400 points the proposal of each fit: 26 of 28 identical.
  `syn_amplifier_like` at 300 points: slots 5 and 6 swapped (the same ten points, the same tags); `syn_levy20_c1` at
  350 points: slots 0 and 1 (wide) and 9 (region) are other points. Their 91 fits: 70 within 1e-4 in log, the largest
  difference 9.8e-4, equal evaluations in 25. The same 28 histories under one and two BLAS threads: sklearn's own
  proposal changed between the two at `syn_amplifier_like` 100 points (5 of 10 points); at two threads the direct
  search differed from sklearn's 3 times (amplifier 100 and 200, levy20 350).
- CPU (`test_the_cpu_time_of_the_300_point_fit`): 300 points x 8 variables, 1.85 s against 0.47 s at one BLAS thread
  (4.0 times), 6.6 s against 1.35 s at four (4.9 times; at four threads OpenBLAS's waiting threads inflate the CPU
  seconds at these sizes: 2.1 ms wall but 14.7 ms CPU per direct evaluation at 300 points). A proposal's fits at 400
  points, one thread: `syn_levy20_c1` (2 metrics, 20 variables) 13.24 s against 2.25 s (5.9 times),
  `syn_amplifier_like` (5, 8) 13.21 against 3.90 (3.4), `syn_ackley10_c2` (3, 10) 8.27 against 2.39 (3.5),
  `syn_failure_region` (3, 6) 4.40 against 1.33 (3.3). Whole proposals on the recorded runs (up to 92 points): 90.1 s
  against 42.5 s (2.1 times).

Tests (`OMP_NUM_THREADS=4`; `IC_OPT_RECORDED_RUNS` = the two recorded 0.1.10 runs of the refactor plan,
`~/.ic-opt/remote_runs/zzchen@10.113.216.131/802f8b444b991da2` and `~/remote_opt/Mixer_CS_validation_second_batch_20260810`):
`test_metric_gp.py` 36 passed, `test_metric_gp_fit.py` 38 passed, `test_replay_parity.py` 2 passed, `test_optimize.py`
33 passed, `ruff check src tests` clean. Also `test_multi_corner` 15, `test_advice` 32, `test_log_scale` 16,
`test_threads` 14, `test_signoff_tighten` 24, `test_space_tables` 59, `test_digest` 24, `test_schedule` 20,
`test_library_device_run` 6, `test_digest_library` 2, `test_library_index` 9, `test_cli_recipes` 40 passed: the same
counts as main at `dcfcadd`, and the same with the switch on.

Deviations from this specification:

1. The default is the old fit; the direct one is behind `DIRECT_LIKELIHOOD` (section 4's rule).
2. `test_replay_parity.py` does not exercise the strategy: it replays the evaluation of recorded 0.1.10 points
   (render, extract, corner aggregation, objective) and passes with either fit. What shows the proposals: the pinned
   proposals of `test_metric_gp.py` (`TT_PROPOSAL`, the region replay), test 3 of the new file, the replays above.
3. Section 2, item 1's thresholds (hyperparameters 1e-8, predictions 1e-9, equal evaluation counts) are below the
   optimizer's own tolerance and cannot hold. The new file asserts what holds: the likelihood bit for bit along
   sklearn's path, the gradient to 1e-12 at the mode and 1e-5 anywhere, the same starts and random state, a posterior
   not lower than sklearn's (1e-7), hyperparameters within 1e-4, means within 1e-5, standard deviations within 1e-4;
   evaluation counts printed. Item 2's single-variable and two-point histories also within 1e-4 (measured: 1.1e-8).
4. `FastGP`'s own prediction is not ported (above).

What the specification did not foresee:

- L-BFGS-B amplifies rounding: a gradient that differs from sklearn's in the 14th digit sends the search a different
  way after 20 to 50 iterations on an ill-conditioned kernel, and its stopping test moves by an iteration. sklearn's
  kernel matrix bit for bit (adopted) left the hyperparameter differences as they were. The same path would need
  sklearn's own einsum order, which depends on numpy's SIMD loops (the machine), so no faster computation of the
  gradient can promise the same proposals.
- sklearn's own fit is not reproducible across BLAS thread counts from about 100 points (hyperparameters up to 5.7e-6
  apart at 300 points between one and four threads; bit for bit at 40), and its proposal changed once in the 28 above
  between one and two threads. `simulator.strategy_threads` (T17.12) is outside the fingerprint: by this measure the
  current product does not keep its proposals across that setting either.
- Switching the default is one line (`DIRECT_LIKELIHOOD = True`) and test 0's first assertion. The choice is between
  the old proposals exactly (at a given thread count) and 3 to 6 times less CPU in the fits, with proposals that differ
  now and then from about 300 points on, at a rate of the same order as sklearn's own search changes them between thread
  counts. That is the user's call.
