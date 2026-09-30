"""Candidate points of the ``metric_gp`` strategy: the whole grid, the search region, the whole space (T17.1
specification, section 7).

Candidates are rows of level indices (``coords.py``), never an evaluated point or one already chosen for this batch, and
never more than :data:`MAX_CANDIDATES` in one call: the selection keeps one square covariance matrix of that size.

Every candidate is a valid point (T18.2A specification, section 3): where some variables may take only a table's
combinations (``space.tables``), :func:`whole_grid` enumerates the valid points themselves and :func:`local` and
:func:`wide` project what they generate (``Coords.project``) before :func:`fresh` removes repeats and evaluated points.
Their random numbers are drawn as without tables, so a spec without tables gets the candidates it got before.
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
    """Every valid point not in ``excluded`` -- every grid point without tables -- for a space of at most
    :data:`MAX_CANDIDATES` valid points (``Coords.size``). They are enumerated directly (``Coords.valid_points``), never
    as the full product of the variables' levels, which may be millions when the valid points are a few hundred."""
    return fresh(coords.valid_points(), excluded)


def local(coords: Coords, centre: np.ndarray, length: float, weights: np.ndarray, excluded: set[bytes],
          rng: np.random.Generator) -> np.ndarray:
    """Up to :data:`LOCAL` points around the centre. Per variable the region holds the levels within
    ``centre +- length * w / 2`` (unit coordinates) and always the centre's level and its two neighbours; when the
    product of their numbers is at most :data:`LOCAL` the candidates are all of those combinations.

    Otherwise they are perturbations of the centre, and what a perturbation does to a variable depends on whether the
    region's box reaches beyond the variable's own level:

    - it does (the box covers part of a neighbouring level's stretch of the axis): the variable is redrawn, with the
      candidate's own probability, uniformly inside the box and snapped to its nearest level, so a level is drawn as
      often as the box covers it. The probability differs from candidate to candidate, log-uniform between one in the
      number of such variables and ``min(1, 20 / d)``: some candidates change a variable or two, some nearly all (at
      least one). With one probability for all, every candidate of a problem of up to 20 variables moved all of them
      at once, and in the first batches hardly any such move is an improvement;
    - it does not (a coarse variable -- a multiplier with four levels -- or every variable once the region is smaller
      than the grid): the variable stays, except that with probability ``min(1/2, 1 / n)`` it moves by one level, ``n``
      the number of such variables: about one of them moves per candidate. Their one-step moves alone, the centre
      otherwise unchanged, come first.

    On a coarse variable a change is a large move: it is offered, not imposed on every candidate. And a region smaller
    than the grid searches at the grid's resolution, a variable or two at a time.

    With tables every candidate so generated is then projected onto the nearest valid point (``Coords.project``): a
    candidate may leave the box that way, and many collapse onto one, which :func:`fresh` keeps once."""
    centre_unit = coords.unit(centre[None, :])[0]
    active = np.flatnonzero(coords.active)
    halves = length * np.asarray(weights, dtype=float) / 2
    allowed: list[np.ndarray] = []
    reached = np.zeros(len(active), dtype=bool)
    column = 0
    for i, levels in enumerate(coords.unit_levels):
        if not coords.active[i]:
            allowed.append(np.array([0]))
            continue
        inside = np.flatnonzero(np.abs(levels - centre_unit[i]) <= halves[column])
        near = np.arange(max(centre[i] - 1, 0), min(centre[i] + 2, len(levels)))
        allowed.append(np.union1d(inside, near))
        reached[column] = halves[column] > np.abs(levels[near[near != centre[i]]] - centre_unit[i]).min() / 2
        column += 1
    if np.prod([float(len(a)) for a in allowed]) <= LOCAL:
        mesh = np.meshgrid(*allowed, indexing="ij")
        return fresh(coords.project(np.stack([m.ravel() for m in mesh], axis=1)), excluded)

    unit = np.repeat(centre_unit[None, :], LOCAL, axis=0)
    most = min(1.0, PERTURB_VARIABLES / len(active))
    rates = np.exp(rng.uniform(np.log(min(1.0 / max(int(reached.sum()), 1), most)), np.log(most), size=LOCAL))
    change = rng.random((LOCAL, len(active))) < rates[:, None]
    if reached.any():
        none = np.flatnonzero(~change[:, reached].any(axis=1))
        change[none, np.flatnonzero(reached)[rng.integers(int(reached.sum()), size=len(none))]] = True
    for column in np.flatnonzero(reached):
        i, rows = active[column], np.flatnonzero(change[:, column])
        unit[rows, i] = rng.uniform(max(centre_unit[i] - halves[column], 0.0), min(centre_unit[i] + halves[column], 1.0),
                                    size=len(rows))
    out = coords.snap(unit)
    stuck = active[~reached]
    steps = []
    for i in stuck:
        rows = np.flatnonzero(rng.random(LOCAL) < min(0.5, 1.0 / len(stuck)))
        moved = out[rows, i] + rng.choice([-1, 1], size=len(rows))
        out[rows, i] = np.where((moved < 0) | (moved >= coords.counts[i]), 2 * out[rows, i] - moved, moved)   # off the end: the other way
        steps += [centre + move * (np.arange(len(centre)) == i) for move in (-1, 1) if 0 <= centre[i] + move < coords.counts[i]]
    out[:, ~coords.active] = centre[~coords.active]
    generated = np.vstack([np.array(steps, dtype=np.int64).reshape(-1, len(centre)), out])
    return fresh(coords.project(generated), excluded)[:LOCAL]


def wide(coords: Coords, excluded: set[bytes], rng: np.random.Generator, n: int = WIDE) -> np.ndarray:
    """A scrambled Sobol sample of ``n`` points in unit coordinates, snapped to the grid and projected onto the valid
    points (``Coords.project``), minus ``excluded``."""
    sample = coords.snap(unit_design("sobol", n, len(coords.counts), int(rng.integers(2**63))))
    return fresh(coords.project(sample), excluded)


class Advised:
    """An advice's ranges, fixed levels and ``vary`` on the grid (T17.1.5 specification, section 6.2): per variable the
    band of level indices it may take, and which variables may move at all.

    Candidates are brought inside it rather than drawn inside it: they are generated as they are without advice, so the
    random streams stay what they were, then every variable is moved to the nearest level of its band -- the band's end,
    in level indices and therefore in unit coordinates too, logarithmic or not -- and, when ``vary`` is given, every other
    variable to the level of ``centre``. The advice's check (``ic_opt.advice.check``) has put every bound on a level.

    With tables (T18.2A specification, section 3) a row so moved is then projected onto the nearest valid point
    (``Coords.project``), and a row the projection takes out of the advice is not brought inside: :meth:`contains` decides
    on the projected row. Without tables every row stays where the bands put it."""

    def __init__(self, coords: Coords, row: dict) -> None:
        self.id = row["id"]
        self.coords = coords
        self.low = np.zeros(len(coords.counts), dtype=np.int64)
        self.high = coords.counts - 1
        self.held = np.zeros(len(coords.counts), dtype=bool)
        for name, (lo, hi) in row.get("ranges", {}).items():
            i = coords.names.index(name)
            self.low[i], self.high[i] = coords.level(i, lo), coords.level(i, hi)
        for name, value in row.get("fixed", {}).items():
            i = coords.names.index(name)
            self.low[i] = self.high[i] = coords.level(i, value)
        if row.get("vary"):
            self.held = np.array([name not in row["vary"] for name in coords.names])

    def inside(self, idx: np.ndarray, centre: np.ndarray) -> np.ndarray:
        """``idx`` (rows of level indices) brought inside the advice and projected onto the valid points, less the rows the
        projection takes out of it; ``centre``: the level indices the held variables take."""
        out = self.coords.project(self._bands(idx, centre))
        return out[self.contains(out, centre)]

    def contains(self, idx: np.ndarray, centre: np.ndarray) -> np.ndarray:
        """Which rows of ``idx`` lie inside the advice already: every variable within its band, the held ones at the
        centre's level."""
        return (self._bands(idx, centre) == idx).all(axis=1)

    def _bands(self, idx: np.ndarray, centre: np.ndarray) -> np.ndarray:
        """``idx`` with every variable moved to the nearest level of its band and the held ones to the centre's level."""
        out = np.array(idx, dtype=np.int64).reshape(-1, len(self.low))
        out[:, self.held] = centre[self.held]
        return np.clip(out, self.low, self.high)
