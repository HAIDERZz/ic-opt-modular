# T17.1 — the `metric_gp` strategy: specification

Status: specification for implementation (2026-09-28). Decisions behind it: `T17_OPTIMIZER_PLAN_CN.md`.
Scope of this stage: one condition (no corners, or a run restricted to one corner), schematic level (no EM devices).

## 1. What it is

A strategy for `ic_opt.blocks.optimize.suggest`, name `metric_gp`, built on numpy, scipy and scikit-learn only.
It models every metric the spec's constraints and objective use, applies the spec's own formulas to samples of
those models, and searches on the spec's grid. It never sees a penalty value.

It is a `Suggester` (`src/ic_opt/suggesters/base.py`): `propose(spec, history, n, *, seed) -> Proposal`, stateless:
everything it needs is rebuilt from `history` (observations and their origin tags). The same history and seed give
the same proposal.

## 2. Files

| file | content |
| --- | --- |
| `src/ic_opt/suggesters/metric_gp/__init__.py` | `MetricGpSuggester` |
| `.../coords.py` | grid levels, unit coordinates (linear / logarithmic), snapping |
| `.../models.py` | one Gaussian process per metric (MAP fit), the "gives a value" model |
| `.../compose.py` | the spec's objective and constraints on arrays of metric values |
| `.../region.py` | the search region and its replay from the history |
| `.../candidates.py` | candidate points: whole grid, region, whole space |
| `.../select.py` | posterior sampling and the choice of a batch |
| `src/ic_opt/objective.py` | `evaluate_expression_array` (section 5) |
| `src/ic_opt/suggesters/__init__.py` | `make("metric_gp")` |
| `src/ic_opt/blocks/optimize.py` | the stage-1 refusals (section 11), the initial design size |
| `tests/ic_opt/test_metric_gp.py` | section 13 |

## 3. Coordinates (`coords.py`)

- A variable's levels are `lower + k * step`, `k = 0 .. K-1` (as `ic_opt.space` defines them; use its parsing).
- Unit coordinate of a value `x`: logarithmic, `(ln x - ln lower) / (ln upper - ln lower)`, when `lower > 0` and
  `upper / lower >= 10`; linear, `(x - lower) / (upper - lower)`, otherwise. A variable with one level has
  coordinate 0 and takes no part in models or search.
- Everything downstream (design, models, region, candidates) lives in unit coordinates. Points leave the strategy
  as raw vectors in the numeric space of `ic_opt.space.bounds`, on the grid (the strategy snaps in its own
  coordinates; `suggest` snaps again, which must then change nothing — test it).
- Rationale: a width that may be 0.5 to 10 is searched evenly per octave, not per micrometre.

## 4. What is modelled (`models.py`)

### 4.1 Metrics
The modelled metrics are the ones named in `spec.constraints` or in `spec.objective.expression`. For metric `m`
the training set is every observation that has a finite value for `m` in `Observation.metrics` — also observations
whose status is `metric_failed`, which keep the metrics that did extract.

Target transform, decided per call from the training values `y`:
- `log10(y)` when all `y > 0` and `max(y) / min(y) >= 100`;
- identity otherwise;
then standardized (mean 0, standard deviation 1). Predictions and samples are transformed back before the spec's
formulas see them.

A metric whose training values are all equal (or that has fewer than 2 values) has no model: it predicts that
value with zero variance.

### 4.2 The Gaussian process
`sklearn.gaussian_process.GaussianProcessRegressor` on the standardized targets, kernel
`ConstantKernel * Matern(nu=2.5, one length scale per active variable) + WhiteKernel`.

Hyperparameters by MAP, not by maximum likelihood: pass a custom `optimizer` callable that adds the log prior to
the objective sklearn hands it.
- length scales: log-normal, `ln(l) ~ Normal(sqrt(2) + 0.5 * ln(d), sqrt(3))`, `d` = number of active variables;
  bounds `(0.02, 200)`.
- noise level (on standardized targets): log-normal, `ln(noise) ~ Normal(-4, 1)`; bounds `(1e-8, 1e-1)`.
- constant: no prior; bounds `(1e-2, 1e2)`.
- start points of the optimizer: the prior's mode first, then 2 draws from the prior (generator seeded from the
  call's seed and the metric's index); L-BFGS-B; keep the best.

The dimension-scaled prior is what lets a plain Gaussian process work with 20 to 30 variables and a few dozen
points (Hvarfner, Hellsten, Nardi, "Vanilla Bayesian Optimization Performs Great in High Dimensions", ICML 2024).

### 4.3 "Gives a value"
When the history holds at least one observation whose status is `metric_failed` or `failed:<stage>`, fit one
classifier for "this point is scored" (status `ok` or `constraint_failed`): `GaussianProcessClassifier` with the
same kernel family (no white noise term), inputs in unit coordinates. With no such observation the probability is 1
everywhere. With failures only (nothing scored yet) the probability is 0.5 everywhere.

Its use: section 8. It is never turned into a target value of another model.

## 5. The spec's formulas on arrays (`compose.py`, `objective.py`)

`ic_opt.objective.evaluate_expression_array(expression, arrays) -> np.ndarray`: the same tiny language as
`evaluate_expression` (metric names, numbers, `+ - * / ** %`, unary sign, `min`, `max`, `ln`), evaluated on numpy
arrays by walking the same syntax tree. Where the scalar evaluator would raise (division by zero, `ln` of a
non-positive number, a complex power, a non-finite result) the array result is `nan`. Test: on random metric
values the array evaluator equals the scalar one element by element, and is `nan` exactly where the scalar one
raises.

`Composer(spec)`:
- `objective(arrays) -> array`: the objective in minimization form (`-` expression for `maximize`). A spec without
  an objective: the negative of the smallest normalized margin (see below), so that a feasible point is pushed away
  from its nearest constraint.
- `residuals(arrays) -> array (points, constraints)`: `value - threshold` for `lt` / `le`, `threshold - value` for
  `gt` / `ge`: a constraint holds where its residual is `<= 0`.
- `violation(arrays, scales) -> array`: `sum_i max(0, residual_i) / scale_i`, where `scale_i` is the standard
  deviation of metric `i`'s observed values (1 when it has none, and at least `1e-12`). Normalizing by the metric's
  own spread keeps a constraint whose threshold is 0 or tiny from dominating.
- margins for the objective-less case: `-residual_i / scale_i`.
- `nan` in the objective or in a residual marks the sample as not scored for that point.

## 6. The incumbent and the two phases

From the history's true values (not from models):
- phase 1 — no feasible observation: the incumbent is the scored observation with the smallest violation;
- phase 2 — at least one feasible observation: the incumbent is the feasible observation with the smallest
  objective.

## 7. Candidates (`candidates.py`)

Let `G` be the number of grid points (`ic_opt.space.grid_size`), `E` the evaluated points.

| case | candidates |
| --- | --- |
| `G <= 2000` | every grid point not in `E`. No search region; every slot of the batch chooses among all of them. |
| `G > 2000` | `local`: up to 1500 points inside the search region (section 9); `wide`: up to 500 points over the whole space. |

The candidate set never holds more than 2000 points: the selection keeps one square matrix of that size at a time.

`local`: per variable the levels inside `[c_i - L * w_i / 2, c_i + L * w_i / 2]` (unit coordinates, `c` the
region's centre), always including the centre's level and its two neighbours. If the product of these level counts
is at most 1500 take them all; else draw 1500 by perturbation: start from the centre, and for each candidate
change each variable with probability `min(1, 20 / d)` to a level drawn uniformly from its allowed levels (at least
one variable changes).

`wide`: a scrambled Sobol sample of 500 points in unit coordinates (seeded from the call), snapped to the grid.

Both sets exclude `E` and duplicates.

## 8. Choosing a batch (`select.py`)

For the candidate set `C` (all candidates of the call, local and wide together) and the `n` slots of the batch:

1. For every modelled metric, one metric at a time: the posterior mean `mu` and covariance `S` over `C`; a
   Cholesky factor of `S` (add jitter `1e-8 * trace / len(C)`, multiplying by 10 up to `1e-4 * trace / len(C)`
   until the factorization succeeds; if it never does, fall back to independent draws from the marginal
   variances); the `n` base samples `F = mu + chol @ Z` with `Z` standard normal; then drop `S` and the factor and
   keep `F` and what section 8.4 needs. Do NOT use `GaussianProcessRegressor.sample_y` (it takes a singular value
   decomposition of the full covariance).
2. For slot `b`, with sample `b` of every metric:
   - `scored[j]`: a uniform draw per candidate is below the "gives a value" probability of candidate `j`, and the
     sample has no `nan` there;
   - among candidates that are `scored` and whose sampled residuals are all `<= 0`: the smallest sampled objective
     wins;
   - if there is none: among the `scored` candidates the smallest sampled violation wins;
   - if there is none: the candidate with the largest "gives a value" probability wins.
3. A chosen candidate leaves the set. Slots are assigned in this order when the space is large (`G > 2000`): the
   first `n_wide = round(wide_share * n)` slots choose among `wide` only, the others among `local` only (when a set
   runs empty the slot chooses among the other). `wide_share` is a constructor argument, default `0.2`.
4. Batch spacing: the sample a slot uses is conditioned on the points the earlier slots chose, each with the
   metric values its own slot's sample gave it, as pretended observations (hyperparameters unchanged). By
   Matheron's rule, for the picks `J` with pretended values `v_J`, slot `b`'s sample is
   `F[:, b] + S[:, J] @ solve(S[J, J] + jitter, v_J - F[J, b])`. The columns `S[:, j]` are posterior covariances
   between every candidate and the pick `j`: compute each from the model when the pick is made
   (`k(C, c_j) - V.T @ V[:, j]` with `V = solve_triangular(chol(K_train), K(train, C))`, kept per metric,
   observations x candidates), so that no square matrix outlives step 1.

Each chosen point carries a tag (section 10).

## 9. The search region (`region.py`) — only when `G > 2000`

State of a region: its index `r`, its anchor, its side `L`, its success and failure counters.
- side: `L` starts at `0.8`, doubles after `3` successful batches in a row (at most `1.6`), halves after
  `fail_tol = max(2, ceil(d / batch))` unsuccessful batches in a row, where `batch` is the size of the batch being
  replayed;
- per-variable weights `w_i`: from the fitted length scales of the modelled metrics, the geometric mean over the
  metrics, normalized to geometric mean 1 over the variables and clipped to `[0.2, 5]`;
- the region's own points: the observations proposed while it was active, local and wide alike, plus, for region
  0, the start points and the initial design;
- the region's centre: the best of its own points under the phase rule of section 6;
- a batch is successful when it improved the region's best: in phase 1 the violation fell by more than `1e-3`
  relative (or the first feasible point appeared); in phase 2 the feasible objective fell by more than
  `1e-3 * max(1, |best|)`;
- the region ends when `L < 2**-6`. All observations stay in the models. The next region `r + 1` starts with
  `L = 0.8` around a new anchor: the candidate of a wide sample (2000 Sobol points, snapped) that wins a posterior
  sample under the rule of section 8, among those farther than `0.25 * sqrt(d)` (unit coordinates) from every
  earlier region's final centre; when none is that far, the farthest one. The anchor is the first point of the next
  batch.

Replay: the regions and their counters are rebuilt from the history on every call, by walking the batches in
order. A batch is the set of rows that carry the same batch key in their origin tag (section 10). Rows of other
origins (start points, the initial design, user points, other strategies) belong to region 0 and are not batches.
A continued run must give the same proposals as an uninterrupted one.

## 10. Origin tags

`suggest` writes `suggest:metric_gp:<tag>` per point. Tags:

| tag | meaning |
| --- | --- |
| `init` | the space-filling design |
| `grid:<k>` | chosen among all grid points (small space); `k` = number of observations when the batch was proposed |
| `tr:<r>:<k>` | chosen inside region `r` |
| `wide:<r>:<k>` | chosen over the whole space while region `r` was active |
| `anchor:<r>:<k>` | the anchor of region `r` |

The batch key is `k`.

## 11. The initial design and the stage-1 refusals

- Initial design: while the history holds fewer than `n_init` observations, propose the next points of one Sobol
  design in unit coordinates (so: logarithmic where section 3 says so), seeded from the run's seed, prefix-stable
  (the first points do not change when more are drawn). Start points (the `start` keyword of `suggest`) count
  towards `n_init`. `n_init` is the constructor argument `initial_trials`; default `min(max(2 * d, 8), 20)`.
  `blocks/optimize.py` prints the design line for this strategy as it does for the OpenBox strategies.
  A batch that reaches the end of the design is completed by the model.
- `opt.optimize` refuses, before anything is simulated and also under `--plan`:
  - a spec with devices: "strategy metric_gp does not take EM devices yet; use openbox_gp_eic";
  - a run that covers more than one corner: "strategy metric_gp works on one condition; run one corner
    (corners='["tt"]'), or the signoff recipe, which searches at one corner and re-checks the best points at all".
  The suggester itself refuses a history whose children carry more than one corner.

## 12. Cost

`propose` for 24 variables, 200 observations, 12 modelled metrics, a batch of 10, on 2 threads: at most 60 s.
Measure it and report it. The fits of different metrics are independent; do not parallelize inside the strategy
(the caller owns the machine's budget).

## 13. Tests (`tests/ic_opt/test_metric_gp.py`, in the style of the existing tests, fast: under 90 s in total)

A threshold below that cannot be met is a finding: report the measured number and what you think causes it. Do not
lower a threshold, shrink a test problem or pick seeds to make a test pass.

1. Coordinates: linear and logarithmic variables; round trip value → unit → value on every level; snapping is
   idempotent; a one-level variable.
2. Array evaluator against the scalar one (section 5).
3. Composer: residual signs for the four operators; violation scaling; the objective-less case.
4. Model: on 40 points of a smooth function of 3 variables the held-out predictions (20 points) correlate above
   0.95 with the truth; a constant metric; the logarithmic target transform is chosen for a metric spanning three
   decades and predictions come back in the metric's own unit.
5. "Gives a value": with failures in one half-space the probability is above 0.7 deep in the scored half and below
   0.3 deep in the other.
6. Selection on a 10 x 10 grid with a known optimum and 30 observations: over 20 seeds the first slot chooses the
   optimum or one of its grid neighbours in at least 15.
7. Phase 1: with no feasible observation the chosen point's true violation is smaller than the median violation of
   the candidates in at least 15 of 20 seeds.
8. Batch spacing: a batch of 10 has 10 distinct points, none evaluated before; on a smooth problem its mean
   pairwise distance is larger with the conditioning of section 8.4 than without.
9. Region replay: build a history by calling `suggest` batch by batch (evaluating with a test function) for 60
   points; then call `propose` once on the full history: region index, side and counters equal those of the last
   incremental step; the proposal for the next batch equals the uninterrupted one.
10. Tags: every point of a batch carries a tag of section 10 with the right `k`.
11. Refusals of section 11.
12. Determinism: two calls with the same history and seed return the same points; a different seed returns others.
13. The strategy never reads `failure_penalty` and no model is ever given a value above the largest observed metric
    value (guard against penalties creeping back in).

## 14. What is deliberately left open

The numbers marked as constructor arguments (`wide_share`, `initial_trials`) and the constants of section 9 are
first values. They are set on the development problems of the benchmark, never on the held-out ones, and every
change is recorded in `T17_OPTIMIZER_PLAN_CN.md` section 7 with the measurement that motivated it.
