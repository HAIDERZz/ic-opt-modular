"""Candidate points of the ``metric_gp`` strategy: the whole grid, the search region, the whole space (T17.1
specification, section 7).

Candidates are rows of level indices (``coords.py``), never an evaluated point or one already chosen for this batch, and
never more than :data:`MAX_CANDIDATES` in one call: the selection keeps one square covariance matrix of that size.
"""

from __future__ import annotations

import numpy as np

from ic_opt.suggesters.base import unit_design
from ic_opt.suggesters.metric_gp.coords import Coords, keys

MAX_CANDIDATES = 2000          # a grid this small is searched whole, without a region
LOCAL = 1500                   # candidates inside the search region when the grid is larger
WIDE = 500                     # candidates over the whole space when the grid is larger
PERTURB_VARIABLES = 20         # a perturbed candidate changes each variable with probability min(1, this / d)


def fresh(idx: np.ndarray, excluded: set[bytes]) -> np.ndarray:
    """The rows of ``idx`` not in ``excluded`` and not repeated, in order; ``excluded`` gains them."""
    keep = []
    for row, key in enumerate(keys(idx)):
        if key not in excluded:
            excluded.add(key)
            keep.append(row)
    return idx[keep]


def whole_grid(coords: Coords, excluded: set[bytes]) -> np.ndarray:
    """Every grid point not in ``excluded`` (a grid of at most :data:`MAX_CANDIDATES` points)."""
    grid = np.indices(coords.counts).reshape(len(coords.counts), -1).T
    return fresh(grid, excluded)


def local(coords: Coords, centre: np.ndarray, length: float, weights: np.ndarray, excluded: set[bytes],
          rng: np.random.Generator) -> np.ndarray:
    """Up to :data:`LOCAL` points inside the region: per variable the levels within ``centre +- length * w / 2`` (unit
    coordinates), always the centre's level and its two neighbours. All of them when their product is at most
    :data:`LOCAL`, else :data:`LOCAL` perturbations of the centre, each changing every variable with probability
    ``min(1, 20 / d)`` (at least one) to another of its allowed levels."""
    centre_unit = coords.unit(centre[None, :])[0]
    allowed: list[np.ndarray] = []
    w = iter(weights)
    for i, levels in enumerate(coords.unit_levels):
        if not coords.active[i]:
            allowed.append(np.array([0]))
            continue
        half = length * next(w) / 2
        inside = np.flatnonzero(np.abs(levels - centre_unit[i]) <= half)
        near = np.arange(max(centre[i] - 1, 0), min(centre[i] + 2, len(levels)))
        allowed.append(np.union1d(inside, near))
    sizes = np.array([len(a) for a in allowed], dtype=float)
    if np.prod(sizes) <= LOCAL:
        mesh = np.meshgrid(*allowed, indexing="ij")
        return fresh(np.stack([m.ravel() for m in mesh], axis=1), excluded)
    active = np.flatnonzero(coords.active)
    rate = min(1.0, PERTURB_VARIABLES / len(active))
    out = np.repeat(centre[None, :], LOCAL, axis=0)
    change = rng.random((LOCAL, len(active))) < rate
    none = ~change.any(axis=1)
    change[np.flatnonzero(none), rng.integers(len(active), size=int(none.sum()))] = True
    for column, i in enumerate(active):
        rows = np.flatnonzero(change[:, column])
        others = allowed[i][allowed[i] != centre[i]]       # a change moves the variable: never back to the centre's level
        out[rows, i] = others[rng.integers(len(others), size=len(rows))]
    return fresh(out, excluded)


def wide(coords: Coords, excluded: set[bytes], rng: np.random.Generator, n: int = WIDE) -> np.ndarray:
    """A scrambled Sobol sample of ``n`` points in unit coordinates, snapped to the grid, minus ``excluded``."""
    return fresh(coords.snap(unit_design("sobol", n, len(coords.counts), int(rng.integers(2**63)))), excluded)
