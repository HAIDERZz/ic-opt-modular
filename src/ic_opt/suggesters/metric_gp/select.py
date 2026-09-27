"""Posterior sampling and the choice of a batch (T17.1 specification, section 8).

Every slot of a batch draws one joint posterior sample of every modelled metric over the candidates, applies the
spec's formulas to it (``compose.py``) and takes the best candidate of that sample: feasible with the smallest
objective, else the smallest violation, else the most likely to give a value. Thompson sampling on the metrics, with the
constraints and the objective as the spec states them.

Batch spacing: a slot's sample is conditioned on the candidates the earlier slots chose, each with the value its own
slot's sample gave it (Matheron's rule, hyperparameters unchanged), so a later slot sees the earlier picks as
observed and looks elsewhere. The candidates' square covariance matrix lives only while the base samples are drawn;
what conditioning needs later is the factor ``V`` per metric (observations x candidates) and one covariance column
per pick.
"""

from __future__ import annotations

import numpy as np
from scipy.linalg import LinAlgError, cholesky

from ic_opt.suggesters.metric_gp.compose import Composer
from ic_opt.suggesters.metric_gp.models import MetricModel, ValueModel

JITTER_FIRST = 1e-8            # relative to the mean prior variance: trace / len(candidates)
JITTER_LAST = 1e-4


class MetricSamples:
    """The ``n`` base samples of one metric over the candidates, and the picks it has been conditioned on."""

    def __init__(self, model: MetricModel, x: np.ndarray, n: int, rng: np.random.Generator) -> None:
        self.model, self.x = model, x
        self.picks: list[int] = []
        self.values: list[float] = []
        self.columns: list[np.ndarray] = []
        if model.gp is None:
            self.base = None
            return
        mean, cov, self.v = model.joint(x)
        self.jitter = JITTER_FIRST * max(float(np.trace(cov)) / len(x), 1e-300)
        z = rng.standard_normal((len(x), n))
        factor = _cholesky(cov)
        if factor is None:            # never positive definite within the jitter: independent marginal draws
            self.base = mean[:, None] + np.sqrt(np.maximum(np.diag(cov), 0.0))[:, None] * z
        else:
            self.base = mean[:, None] + factor @ z

    def slot(self, b: int, condition: bool = True) -> np.ndarray:
        """Sample ``b`` in the standardized scale, conditioned on the picks so far:
        ``F[:, b] + S[:, J] @ solve(S[J, J] + jitter, v_J - F[J, b])``."""
        f = self.base[:, b]
        if not condition or not self.picks:
            return f
        columns = np.column_stack(self.columns)
        gram = columns[self.picks] + self.jitter * np.eye(len(self.picks))
        return f + columns @ np.linalg.solve(gram, np.array(self.values) - f[self.picks])

    def pick(self, j: int, standardized: np.ndarray | None) -> None:
        """Candidate ``j`` was chosen with the (standardized) sample ``standardized`` of its slot."""
        if self.base is None:
            return
        self.picks.append(j)
        self.values.append(float(standardized[j]))
        self.columns.append(self.model.cross(self.x, j, self.v))


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
                 forced: int | None = None, condition: bool = True) -> list[int]:
    """Choose ``len(preferred)`` distinct candidates of ``x`` (active unit coordinates), one per slot. Slot ``b``
    chooses among ``preferred[b]`` (a boolean mask; None: every candidate), or among all remaining candidates once that
    set runs empty. ``forced``: the candidate slot 0 takes (a new region's anchor), sample 0 still conditioning the
    later slots on it. ``condition=False`` turns off the batch spacing (tests compare against it)."""
    n = len(preferred)
    if not n:                   # every grid point is evaluated: nothing to sample over
        return []
    samples =[MetricSamples(m, x, n, rng) for m in models]
    probability = value_model.probability(x)
    draws = rng.random((n, len(x)))
    remaining = np.ones(len(x), dtype=bool)
    chosen: list[int] = []
    for b in range(n):
        if not remaining.any():
            break
        standardized = [s.slot(b, condition) if s.base is not None else None for s in samples]
        if b == 0 and forced is not None:
            j = forced
        else:
            mask = remaining if preferred[b] is None or not (preferred[b] & remaining).any() else preferred[b] & remaining
            arrays = {s.model.name: (s.model.transform.backward(f) if f is not None else np.full(len(x), s.model.constant))
                      for s, f in zip(samples, standardized, strict=True)}
            j = choose(composer, scales, arrays, draws[b] < probability, probability, mask)
        chosen.append(j)
        remaining[j] = False
        for s, f in zip(samples, standardized, strict=True):
            s.pick(j, f)
    return chosen


def choose(composer: Composer, scales: dict[str, float], arrays: dict[str, np.ndarray], gives: np.ndarray,
           probability: np.ndarray, mask: np.ndarray) -> int:
    """The rule of one slot: among the candidates in ``mask`` that are scored (``gives`` and no nan in the sample) and
    whose sampled residuals are all <= 0, the smallest objective; else among the scored, the smallest violation; else
    the largest probability of giving a value."""
    objective = composer.objective(arrays, scales)
    residuals = composer.residuals(arrays)
    scored = mask & gives & np.isfinite(objective) & np.isfinite(residuals).all(axis=-1)
    feasible = scored & (residuals <= 0).all(axis=-1)
    if feasible.any():
        return int(np.argmin(np.where(feasible, objective, np.inf)))
    if scored.any():
        return int(np.argmin(np.where(scored, composer.violation(arrays, scales), np.inf)))
    return int(np.argmax(np.where(mask, probability, -np.inf)))
