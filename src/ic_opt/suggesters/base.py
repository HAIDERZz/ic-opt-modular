"""Suggester protocol: stateless "given these observations, propose n raw vectors".

Every call rebuilds whatever model it needs from the observation list, so
continuation and warm start are not special cases — they are just more rows.
Raw vectors live in the numeric space of :func:`ic_opt.space.bounds`; the
``suggest`` block snaps them to the grid and removes duplicates.

What a model is fed (T17.0b, 2026-09-28). Until 0.4.0 every infeasible point reached the models as
``1e6 + constraint_penalty`` and every failed one as ``1e6``, both as ordinary results: on real data the objective model's
predictions of held-out points then had a rank correlation of about 0 with the truth (0.95 when fed the true values), and
TuRBO's standardization squeezed the spread among the feasible points from 0.165 to 4e-7. Now a point's true objective
(:func:`minimization_objective`) is what a model sees, and a failed point is told apart as failed: OpenBox gets it as a
failed trial (``openbox.py``), TuRBO, which needs one number per point, gets targets built on one scale from the whole
history (``turbo.py``). No strategy uses a penalty number any more.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from ic_opt import space
from ic_opt.observation import Observation, Observations
from ic_opt.spec import Spec

# TuRBO (the `turbo` strategy, `latin_hypercube` designs) is vendored in the checkout, not shipped in the package:
# it is under Uber's non-commercial licence. It is installed from the checkout like OpenBox and imported normally.
TURBO_INSTALL = 'uv pip install -e ".[turbo]" -e vendor/TuRBO'


@dataclass
class Proposal:
    raw: list[list[float]]
    tag: str = ""                    # provenance appended to Point.origin for the whole batch, e.g. TuRBO's "init:0:4"
    tags: list[str] | None = None    # per-point provenance instead of `tag` (OpenBox: "init" / "acq"), one per raw vector


class Suggester(Protocol):
    name: str

    def propose(self, spec: Spec, history: Observations, n: int, *, seed: int,
                pending: Sequence[dict[str, str]] = ()) -> Proposal:
        """``pending``: grid points already chosen for this batch (start points, earlier attempts), not yet evaluated."""
        ...


def minimization_objective(spec: Spec, obs: Observation) -> float | None:
    """A point's true objective in minimization form, what a model is fed: the objective of an ``ok`` point, the
    ``fom`` of a ``constraint_failed`` one with the sign of the spec's direction (``-fom`` for maximize); 0.0 for every
    point that is not failed when the spec has no objective (a pure feasibility problem: before T17.0b its points fell
    through to the failure penalty). None for a failed point (``metric_failed``, ``failed:<stage>``): there is no value."""
    if obs.status not in ("ok", "constraint_failed"):
        return None
    if spec.objective is None:
        return 0.0
    if obs.objective is not None:
        return obs.objective
    if obs.fom is None:
        return None
    return -obs.fom if spec.objective.direction == "maximize" else obs.fom


def surrogate_minimum(spec: Spec) -> int:
    """Successful points (``ok`` or ``constraint_failed``) a history needs before a surrogate is fitted on it: one more
    than the variables, at least two. Fewer, and the batch is completed with further space-filling points."""
    return max(2, len(spec.variables) + 1)


def penalized_objective(obs: Observation, failure_penalty: float) -> float:
    """Deprecated (T17.0b): the 0.4.0 model target, ``failure_penalty (+ constraint_penalty)`` for a point without an
    objective. No strategy uses it; a model is fed :func:`minimization_objective`. Kept importable for callers of 0.4.0."""
    warnings.warn("penalized_objective is deprecated and unused since T17.0b: models are fed minimization_objective",
                  DeprecationWarning, stacklevel=2)
    if obs.objective is not None:
        return obs.objective
    if obs.status == "constraint_failed":
        return failure_penalty + obs.constraint_penalty
    return failure_penalty


def unit_design(method: str, n: int, dim: int, seed: int) -> np.ndarray:
    """``(n, dim)`` samples in the unit cube: sobol (seeded scramble), latin_hypercube (TuRBO's), random.

    Sobol draws the next power of two and keeps the first ``n`` (the same points ``random(n)`` gives, without scipy's
    warning that balance needs a power of two, raised on every design until T17.0b); sobol and random are prefix-stable:
    the same seed gives the same first points whatever ``n``, so a design that grows is the old one continued."""
    if method == "sobol":
        from scipy.stats.qmc import Sobol

        return Sobol(d=dim, scramble=True, seed=seed).random_base2(m=max(0, int(n - 1).bit_length()))[:n]
    if method == "latin_hypercube":
        try:
            from turbo.utils import latin_hypercube
        except ImportError as exc:
            raise turbo_missing("method 'latin_hypercube'") from exc

        return latin_hypercube(n, dim)
    if method == "random":
        return np.random.default_rng(seed).random((n, dim))
    raise ValueError(f"unknown initialization method {method!r}")


def space_filling(spec: Spec, taken: set[str], n: int, *, method: str, size: int, seed: int,
                  to_raw: Callable[[np.ndarray], list[list[float]]] | None = None) -> list[list[float]]:
    """The next ``n`` points of the seeded space-filling sequence ``method`` whose grid point is not in ``taken``, in
    sequence order, one per grid point. The first ``size`` points of the sequence are the initial design; it depends on
    (spec, method, seed) alone, never on the batch size, so the points served so far (in ``taken``) are skipped and the
    design goes on where it stopped. Where the design is used up (its points taken, or snapped onto each other on a
    coarse grid) the sequence is continued past ``size``; on a grid with nothing left fewer than ``n`` come back.
    ``to_raw`` reads the unit cube as raw vectors: linearly between the bounds by default (:func:`scale`); ``metric_gp``
    reads it in its own coordinates, logarithmic where a range spans a decade (T17.1 specification, section 11)."""
    out, seen = [], set(taken)
    grid, drawn, length = space.grid_size(spec), 0, max(size, n, 1)
    to_raw = to_raw or (lambda unit: scale(unit, spec))
    while len(out) < n and len(seen) < grid and length <= 1024 * max(size, n, 1):
        for raw in to_raw(unit_design(method, length, len(spec.variables), seed)[drawn:]):
            key = space.point_key(space.snap(spec, raw))
            if key not in seen:
                seen.add(key)
                out.append(raw)
                if len(out) == n:
                    break
        drawn, length = length, 2 * length
    return out


def scale(unit: np.ndarray, spec: Spec) -> list[list[float]]:
    lb, ub = (np.array(b, dtype=float) for b in space.bounds(spec))
    return (lb + unit * (ub - lb)).tolist()


def turbo_missing(what: str) -> ImportError:
    """The error for ``what`` (e.g. "strategy 'turbo'") when ``import turbo`` fails: what to install, and the licence."""
    return ImportError(f"{what} needs TuRBO, which is vendored in the ic-opt checkout (vendor/TuRBO, Uber's non-commercial "
                       "licence) rather than shipped, and torch + gpytorch from the `turbo` extra; install both from the "
                       f"checkout: {TURBO_INSTALL}")
