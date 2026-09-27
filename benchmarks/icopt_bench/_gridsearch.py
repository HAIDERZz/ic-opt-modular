"""Plain-numpy grid search: enumeration for small grids, random sampling + coordinate descent for large ones.

Used by ``synthetic.py`` (exact enumeration of the two problems small enough for it) and by
``benchmarks/tools/calibrate_synthetic.py`` (threshold calibration and "best known" references on the other six,
whose grids run from 2e10 to beyond 1e16 points -- too large to enumerate). Nothing here knows about ``ic_opt``: it
works on plain per-variable level arrays.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np


def levels(lower: float, upper: float, step: float) -> np.ndarray:
    """The grid values of one variable, ``lower`` to ``upper`` inclusive by ``step`` (``step`` must divide the range)."""
    n = round((upper - lower) / step)
    return lower + step * np.arange(n + 1)


def enumerate_grid(level_arrays: Sequence[np.ndarray]) -> np.ndarray:
    """Every combination of the given per-variable levels, as an ``(n, d)`` array (``n`` is the grid size). Only for
    grids small enough to hold in memory -- the two ``around_design`` synthetic problems, a few hundred to ~1e6 rows."""
    mesh = np.meshgrid(*level_arrays, indexing="ij")
    return np.stack([m.ravel() for m in mesh], axis=-1)


def random_grid_points(level_arrays: Sequence[np.ndarray], n: int, seed: int) -> np.ndarray:
    """``n`` points drawn uniformly at random from the grid (one level per variable, independently), as ``(n, d)``."""
    rng = np.random.default_rng(seed)
    columns = [lv[rng.integers(0, len(lv), size=n)] for lv in level_arrays]
    return np.stack(columns, axis=-1)


Score = Callable[[np.ndarray], np.ndarray]   # (n, d) points -> (n,) rank, lower is better, feasible always beats infeasible


def coordinate_descent(level_arrays: Sequence[np.ndarray], score: Score, x0: np.ndarray, *, sweeps: int = 8) -> tuple[np.ndarray, float]:
    """Local search on the grid from ``x0``: one dimension at a time, try every one of its levels with the others held
    fixed, keep the best; repeat until a full sweep improves nothing or ``sweeps`` is used up."""
    x = x0.copy()
    best_rank = float(score(x[None, :])[0])
    for _ in range(sweeps):
        improved = False
        for d, lv in enumerate(level_arrays):
            candidates = np.repeat(x[None, :], len(lv), axis=0)
            candidates[:, d] = lv
            ranks = score(candidates)
            j = int(np.argmin(ranks))
            if ranks[j] < best_rank - 1e-12:
                x, best_rank, improved = candidates[j], float(ranks[j]), True
        if not improved:
            break
    return x, best_rank


def best_known(level_arrays: Sequence[np.ndarray], score: Score, *, seed: int = 0, n_random: int = 100_000,
                top_k: int = 20, sweeps: int = 8, extra_starts: Sequence[np.ndarray] = ()) -> tuple[np.ndarray, float]:
    """``n_random`` random grid points, refined by coordinate descent from the best ``top_k`` of them plus any
    ``extra_starts`` (a structured guess -- the continuous optimum snapped to the grid, an all-equal point, a corner --
    coordinate descent alone can miss a good basin that pure random sampling rarely lands a starting point in).
    Returns the best point found and its rank (``score``'s minimization form: the objective where feasible, else a
    penalty)."""
    x0 = random_grid_points(level_arrays, n_random, seed)
    ranks = score(x0)
    order = np.argsort(ranks)[:top_k]
    starts = [*x0[order], *extra_starts]
    best_x, best_rank = None, np.inf
    for start in starts:
        x, rank = coordinate_descent(level_arrays, score, start, sweeps=sweeps)
        if rank < best_rank:
            best_x, best_rank = x, rank
    return best_x, best_rank
