"""Grid levels and unit coordinates of the ``metric_gp`` strategy (T17.1 specification, section 3).

A point inside the strategy is a row of level indices, ``k = 0 .. K-1`` per variable (``lower + k * step``, as
``ic_opt.space`` defines the grid); models, regions and candidates see it in unit coordinates, logarithmic for a
variable whose range is positive and spans at least a decade (a width of 0.5 to 10 is searched evenly per octave, not
per micrometre), linear otherwise. Snapping is to the nearest level in unit coordinates, so a logarithmic variable
snaps in its own scale; a raw vector leaves the strategy as the level's exact value, which ``space.snap`` keeps.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

import numpy as np

from ic_opt import space
from ic_opt.spec import Spec

LOG_SPAN = 10          # upper / lower at which a positive range is searched logarithmically


class Coords:
    """The grid of one spec: per variable its level values, their unit coordinates and whether it takes part."""

    def __init__(self, spec: Spec) -> None:
        self.raw_levels: list[np.ndarray] = []
        self.unit_levels: list[np.ndarray] = []
        log, lower, upper = [], [], []
        for variable in spec.variables:
            low, _ = space.parse_scalar(variable.lower)
            step, _ = space.parse_scalar(variable.step)
            count = space.grid_count(variable)
            levels = np.array([float(low + Decimal(k) * step) for k in range(count)])   # Decimal: exactly the grid's text
            lo, hi = levels[0], levels[-1]
            is_log = bool(lo > 0 and hi / lo >= LOG_SPAN)
            log.append(is_log)
            lower.append(lo)
            upper.append(hi)
            self.raw_levels.append(levels)
            self.unit_levels.append(self._unit_1d(levels, lo, hi, is_log))
        self.log = np.array(log, dtype=bool)
        self.lower, self.upper = np.array(lower), np.array(upper)
        self.counts = np.array([len(levels) for levels in self.raw_levels])
        self.active = self.counts > 1                     # a one-level variable takes no part in models or search
        self.names = [v.name for v in spec.variables]
        self._grid = [(space.parse_scalar(v.lower)[0], space.parse_scalar(v.step)[0]) for v in spec.variables]

    @property
    def d(self) -> int:
        """Number of active variables."""
        return int(self.active.sum())

    @staticmethod
    def _unit_1d(x: np.ndarray, lo: float, hi: float, is_log: bool) -> np.ndarray:
        if hi == lo:
            return np.zeros_like(x, dtype=float)
        if is_log:
            return (np.log(x) - np.log(lo)) / (np.log(hi) - np.log(lo))
        return (x - lo) / (hi - lo)

    def unit_of_raw(self, raw: np.ndarray) -> np.ndarray:
        """``(n, D)`` raw values -> unit coordinates (the section-3 formula; a one-level variable is 0)."""
        raw = np.atleast_2d(np.asarray(raw, dtype=float))
        return np.column_stack([self._unit_1d(raw[:, i], self.lower[i], self.upper[i], self.log[i])
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

    def design_raw(self, unit: np.ndarray) -> list[list[float]]:
        """Unit-cube samples (a space-filling design) -> raw grid vectors: the unit cube is read in the strategy's
        own coordinates, so a logarithmic variable's design is even per decade."""
        return self.raw(self.snap(unit)).tolist()


def keys(idx: np.ndarray) -> list[bytes]:
    """Hashable identity of each row of level indices."""
    idx = np.ascontiguousarray(np.atleast_2d(idx), dtype=np.int64)
    return [row.tobytes() for row in idx]
