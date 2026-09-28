"""Posterior sampling and the choice of a batch (T17.1 specification, section 8).

Every slot of a batch draws its own joint posterior sample of every modelled metric over the candidates, and one of
the "gives a value" classifier; it applies the spec's formulas to the metrics' sample (``compose.py``) and takes the best candidate of that sample. While nothing feasible has been
observed the sample's violation decides and the objective does not; once something is feasible, the candidate the
sample calls feasible with the smallest objective wins. Thompson sampling on the metrics, with the constraints and the
objective as the spec states them.

A batch's points differ because its samples do: the slots' samples are independent draws, and a chosen candidate
leaves the set. (Conditioning a slot's sample on the earlier picks was measured and dropped: it pins the sample to the
low values the earlier slots drew, which pulls the next pick towards them -- a batch was less spread with it, and the
benchmark showed no difference beyond what the seeds differ.)
"""

from __future__ import annotations

import numpy as np
from scipy.linalg import LinAlgError, cholesky

from ic_opt.suggesters.metric_gp.compose import Composer
from ic_opt.suggesters.metric_gp.models import MetricModel, ValueModel

JITTER_FIRST = 1e-8            # relative to the mean posterior variance: trace / len(candidates)
JITTER_LAST = 1e-4


def samples(model: MetricModel, x: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray | None:
    """``(candidates, n)``: ``n`` joint posterior samples of one metric over ``x`` in its standardized scale; None for a
    metric without a model (it predicts its constant). The candidates' square covariance lives only in here."""
    if model.gp is None:
        return None
    return _draw(*model.joint(x), n, rng)


def gives_a_value(value_model: ValueModel, x: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    """``(n, candidates)``, per slot which candidates its sample calls scored: where one joint posterior sample of the
    classifier's latent function is positive. The sample is coherent over the candidates, as the metrics' are: a region
    known to fail is out as a whole, one nothing is known about is in every other time. (Deciding candidate by candidate,
    each with its predicted probability, let a share of every failing region in at every slot, and that is where the
    metrics' models know least and their samples look best: on the benchmark's problem with such a region 84% of the
    points proposed after the design gave no value; 47% with the coherent sample.) Without a classifier: every
    candidate when nothing ever failed, every other one when everything did."""
    latent = value_model.latent(x)
    if latent is None:
        return rng.random((n, len(x))) < value_model.constant
    return (_draw(*latent, n, rng) > 0).T


def _draw(mean: np.ndarray, cov: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    z = rng.standard_normal((len(mean), n))
    factor = _cholesky(cov)
    if factor is None:            # never positive definite within the jitter: independent marginal draws
        return mean[:, None] + np.sqrt(np.maximum(np.diag(cov), 0.0))[:, None] * z
    return mean[:, None] + factor @ z


def _cholesky(cov: np.ndarray) -> np.ndarray | None:
    """Lower Cholesky factor of ``cov`` with jitter ``1e-8 * trace / n``, times 10 up to ``1e-4 * trace / n``, added to
    the diagonal (in place: the matrix is not needed afterwards)."""
    n = len(cov)
    scale = max(float(np.trace(cov)) / n, 1e-300)
    added, jitter = 0.0, JITTER_FIRST
    while jitter <= JITTER_LAST * (1 + 1e-9):
        cov[np.diag_indices(n)] += jitter * scale - added
        added = jitter * scale
        try:
            return cholesky(cov, lower=True, check_finite=False, overwrite_a=False)
        except LinAlgError:
            jitter *= 10
    return None


def select_batch(models: list[MetricModel], value_model: ValueModel, composer: Composer, scales: dict[str, float],
                 x: np.ndarray, preferred: list[np.ndarray | None], rng: np.random.Generator, *,
                 nothing_feasible: bool, forced: int | None = None) -> list[int]:
    """Choose ``len(preferred)`` distinct candidates of ``x`` (active unit coordinates), one per slot. Slot ``b``
    chooses among ``preferred[b]`` (a boolean mask; None: every candidate), or among all remaining candidates once that
    set runs empty. ``nothing_feasible``: the history holds no feasible observation (the first of the two phases).
    ``forced``: the candidate slot 0 takes (a new region's anchor)."""
    n = len(preferred)
    if not n:                   # every grid point is evaluated: nothing to sample over
        return []
    drawn = [samples(m, x, n, rng) for m in models]
    probability = value_model.probability(x)
    gives = gives_a_value(value_model, x, n, rng)
    remaining = np.ones(len(x), dtype=bool)
    chosen: list[int] = []
    for b in range(n):
        if not remaining.any():
            break
        if b == 0 and forced is not None:
            j = forced
        else:
            mask = remaining if preferred[b] is None or not (preferred[b] & remaining).any() else preferred[b] & remaining
            arrays = {m.name: (m.transform.backward(f[:, b]) if f is not None else np.full(len(x), m.constant))
                      for m, f in zip(models, drawn, strict=True)}
            j = choose(composer, scales, arrays, gives[b], probability, mask, nothing_feasible)
        chosen.append(j)
        remaining[j] = False
    return chosen


def choose(composer: Composer, scales: dict[str, float], arrays: dict[str, np.ndarray], gives: np.ndarray,
           probability: np.ndarray, mask: np.ndarray, nothing_feasible: bool) -> int:
    """The rule of one slot, among the candidates in ``mask`` that are scored (``gives``, and no nan in the sample).

    While nothing feasible has been observed: the smallest sampled violation; among candidates the sample calls feasible
    (violation 0), the one deepest inside every constraint (the smallest of its largest normalized residual). The
    objective has no say: what the sample calls feasible is mostly the models' uncertainty at that stage.

    Afterwards: among the candidates whose sampled residuals are all <= 0, the smallest objective; if the sample calls
    none feasible, the smallest violation.

    With no scored candidate: the largest probability of giving a value."""
    residuals = composer.residuals(arrays)
    reachable = mask & gives & np.isfinite(residuals).all(axis=-1)
    if nothing_feasible and residuals.shape[-1]:
        if reachable.any():
            normalized = residuals / composer.scale_vector(scales)
            violation = np.maximum(normalized, 0.0).sum(axis=-1)
            depth = np.where(violation > 0, violation, normalized.max(axis=-1))
            return int(np.argmin(np.where(reachable, depth, np.inf)))
        return int(np.argmax(np.where(mask, probability, -np.inf)))
    objective = composer.objective(arrays, scales)
    scored = reachable & np.isfinite(objective)
    feasible = scored & (residuals <= 0).all(axis=-1)
    if feasible.any():
        return int(np.argmin(np.where(feasible, objective, np.inf)))
    if scored.any():
        return int(np.argmin(np.where(scored, composer.violation(arrays, scales), np.inf)))
    return int(np.argmax(np.where(mask, probability, -np.inf)))
