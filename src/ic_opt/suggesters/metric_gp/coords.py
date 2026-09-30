"""Grid levels and unit coordinates of the ``metric_gp`` strategy (T17.1 specification, section 3).

A point inside the strategy is a row of level indices, ``k = 0 .. K-1`` per variable (``lower + k * step``, as
``ic_opt.space`` defines the grid); models, regions and candidates see it in unit coordinates, logarithmic for a
variable whose range is positive and spans at least a decade (a width of 0.5 to 10 is searched evenly per octave, not
per micrometre), linear otherwise (``space.log_scale``: the one rule, which ``openbox_*`` and ``turbo`` search by too).
Snapping is to the nearest level in unit coordinates, so a logarithmic variable snaps in its own scale; a raw vector
leaves the strategy as the level's exact value, which ``space.snap`` keeps.

Allowed combinations (T18.2A specification, section 3). When some variables may take only the combinations a table
lists (``space.tables``), a point of the space is a *valid* one: :meth:`Coords.project` moves rows of level indices to
the nearest valid points -- ``space.nearest`` on the levels' unit coordinates, the one distance ``space.snap`` measures
with -- :meth:`Coords.valid` says which rows are valid, :attr:`Coords.size` counts the valid points and
:meth:`Coords.valid_points` enumerates them. The levels and their unit coordinates are ``space.grid_levels``', the same
for the space and for this class. Without tables every grid point is valid, ``project`` returns its rows unchanged and
nothing differs.
"""

from __future__ import annotations

from collections.abc import Iterable
from math import prod

import numpy as np

from ic_opt import space
from ic_opt.spec import Spec


class Coords:
    """The grid of one spec: per variable its level values, their unit coordinates and whether it takes part; the
    tables of allowed combinations placed on it (``space.placements``) and the number of valid points."""

    def __init__(self, spec: Spec) -> None:
        self.raw_levels: list[np.ndarray] = []
        self.unit_levels: list[np.ndarray] = []
        log, lower, upper = [], [], []
        for variable in spec.variables:
            levels, units = space.grid_levels(variable)     # exactly the grid's text; space.unit_coordinates
            lo, hi = levels[0], levels[-1]
            log.append(space.log_scale(lo, hi))
            lower.append(lo)
            upper.append(hi)
            self.raw_levels.append(levels)
            self.unit_levels.append(units)
        self.log = np.array(log, dtype=bool)
        self.lower, self.upper = np.array(lower), np.array(upper)
        self.counts = np.array([len(levels) for levels in self.raw_levels])
        self.active = self.counts > 1                     # a one-level variable takes no part in models or search
        self.names = [v.name for v in spec.variables]
        self._grid = [(space.parse_scalar(v.lower)[0], space.parse_scalar(v.step)[0]) for v in spec.variables]
        self.placements = space.placements(spec)
        self._allowed = [set(keys(placement.table.rows)) for placement in self.placements]
        linked = {i for placement in self.placements for i in placement.columns}
        # the number of valid points, space.grid_size's: the free variables' levels times each table's combinations
        self.size = (prod(int(count) for i, count in enumerate(self.counts) if i not in linked)
                     * prod(len(placement.table.levels) for placement in self.placements))

    @property
    def d(self) -> int:
        """Number of active variables."""
        return int(self.active.sum())

    def unit_of_raw(self, raw: np.ndarray) -> np.ndarray:
        """``(n, D)`` raw values -> unit coordinates (the section-3 formula, ``space.unit_coordinates``; a one-level
        variable is 0)."""
        raw = np.atleast_2d(np.asarray(raw, dtype=float))
        return np.column_stack([space.unit_coordinates(raw[:, i], self.lower[i], self.upper[i])
                                for i in range(len(self.names))])

    def raw_of_unit(self, unit: np.ndarray) -> np.ndarray:
        """Inverse of :meth:`unit_of_raw`, off the grid (continuous)."""
        unit = np.atleast_2d(np.asarray(unit, dtype=float))
        out = np.empty_like(unit)
        for i in range(len(self.names)):
            lo, hi = self.lower[i], self.upper[i]
            if self.log[i]:
                out[:, i] = np.exp(np.log(lo) + unit[:, i] * (np.log(hi) - np.log(lo)))
            else:
                out[:, i] = lo + unit[:, i] * (hi - lo)
        return out

    def snap(self, unit: np.ndarray) -> np.ndarray:
        """``(n, D)`` unit coordinates -> level indices, nearest level in unit coordinates (ties to the lower level)."""
        unit = np.atleast_2d(np.asarray(unit, dtype=float))
        idx = np.empty(unit.shape, dtype=np.int64)
        for i, levels in enumerate(self.unit_levels):
            right = np.clip(np.searchsorted(levels, unit[:, i]), 0, len(levels) - 1)
            left = np.clip(right - 1, 0, len(levels) - 1)
            idx[:, i] = np.where(np.abs(unit[:, i] - levels[left]) <= np.abs(levels[right] - unit[:, i]), left, right)
        return idx

    def unit(self, idx: np.ndarray) -> np.ndarray:
        """Level indices -> unit coordinates of those levels."""
        idx = np.atleast_2d(idx)
        return np.column_stack([self.unit_levels[i][idx[:, i]] for i in range(len(self.names))])

    def raw(self, idx: np.ndarray) -> np.ndarray:
        """Level indices -> the levels' exact raw values (the numeric space of ``space.bounds``)."""
        idx = np.atleast_2d(idx)
        return np.column_stack([self.raw_levels[i][idx[:, i]] for i in range(len(self.names))])

    def indices(self, rows: Iterable[dict[str, str]]) -> np.ndarray:
        """Grid parameter rows (``Point.params``) -> ``(n, D)`` level indices."""
        out = [[int((space.parse_scalar(params[name])[0] - low) / step) for name, (low, step) in zip(self.names, self._grid, strict=True)]
               for params in rows]
        return np.array(out, dtype=np.int64).reshape(-1, len(self.names))

    def level(self, i: int, text: str) -> int:
        """The level index of variable ``i``'s grid text."""
        low, step = self._grid[i]
        return int((space.parse_scalar(text)[0] - low) / step)

    def design_raw(self, unit: np.ndarray) -> list[list[float]]:
        """Unit-cube samples (a space-filling design) -> raw vectors of valid points: the unit cube is read in the
        strategy's own coordinates, so a logarithmic variable's design is even per decade, and each snapped point is
        projected (:meth:`project`)."""
        return self.raw(self.project(self.snap(unit))).tolist()

    def project(self, idx: np.ndarray) -> np.ndarray:
        """Rows of level indices -> the nearest valid points (T18.2A specification, section 3): each table's variables
        take its combination nearest to their levels (``space.nearest`` on the levels' unit coordinates, as ``space.snap``
        measures), the others stay; a valid row stays as it is. A new array: without tables, a copy of the rows."""
        idx = np.array(np.atleast_2d(idx), dtype=np.int64)
        for placement, allowed in zip(self.placements, self._allowed, strict=True):
            columns = list(placement.columns)
            part = idx[:, columns]
            moved = np.flatnonzero([key not in allowed for key in keys(part)])
            if len(moved):
                at = np.column_stack([self.unit_levels[i][part[moved, j]] for j, i in enumerate(columns)])
                idx[np.ix_(moved, columns)] = placement.table.rows[space.nearest(at, placement.units)]
        return idx

    def valid(self, idx: np.ndarray) -> np.ndarray:
        """Which rows of level indices are valid points: table by table, their levels one of its combinations (every row
        without tables)."""
        idx = np.atleast_2d(idx)
        ok = np.ones(len(idx), dtype=bool)
        for placement, allowed in zip(self.placements, self._allowed, strict=True):
            ok &= np.array([key in allowed for key in keys(idx[:, list(placement.columns)])], dtype=bool)
        return ok

    def valid_points(self) -> np.ndarray:
        """Every valid point as a row of level indices, :attr:`size` of them, the full product never built: the free
        variables' levels times each table's combinations, one block per free variable and per table in the order of its
        first variable, the last block varying fastest (``space.valid_points``' order). Without tables, the grid in the
        order of ``np.indices``."""
        owner = {i: t for t, placement in enumerate(self.placements) for i in placement.columns}
        blocks: list[tuple[list[int], np.ndarray]] = []
        added: set[int] = set()
        for i, count in enumerate(self.counts):
            t = owner.get(i)
            if t is None:
                blocks.append(([i], np.arange(count, dtype=np.int64)[:, None]))
            elif t not in added:
                added.add(t)
                blocks.append((list(self.placements[t].columns), np.asarray(self.placements[t].table.rows)))
        choice = np.indices([len(rows) for _, rows in blocks]).reshape(len(blocks), -1).T
        out = np.empty((len(choice), len(self.counts)), dtype=np.int64)
        for b, (columns, rows) in enumerate(blocks):
            out[:, columns] = rows[choice[:, b]]
        return out


def keys(idx: np.ndarray) -> list[bytes]:
    """Hashable identity of each row of level indices."""
    idx = np.ascontiguousarray(np.atleast_2d(idx), dtype=np.int64)
    return [row.tobytes() for row in idx]
