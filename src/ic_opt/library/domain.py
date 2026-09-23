"""Fail-closed domain guard: may the model answer at this point? A pure geometric gate over the rows a model was fitted on.

Ported from em-opt ``surrogate/domain.py`` (2026-09-23). A query is in domain iff, in this order:
  1. every dim lies inside the ACHIEVED per-dim min/max of the rows (not the nominal ranges);
  2. its integer turns level has at least ``min_per_level`` rows (the model's sub-GP floor); skipped when the
     family has no turns dim;
  3. it lies inside the Delaunay hull of its level (or of all rows without a turns dim), in min-max scaled
     coordinates -- the achieved box can have notches the per-dim test misses. A degenerate hull fails closed.
Criterion 4 (relative sigma) asks a different question -- is the model confident here -- and is the
separate ``sigma_ok``.

Changed after the T13.0 verification: each level's hull uses only the dims that VARY inside that level. The
shipped guard built every level's hull over all continuous dims, so a level with one value of some dim
(single-turn inductors all have one spacing: spacing does not change their geometry) was degenerate and
every query at that level was rejected. A dim fixed within a level now has to match that value exactly
(criterion 1: the level has no data elsewhere along it).

A rejection carries the criterion, a reason, the three nearest rows and ``[query, query clamped onto the
achieved box]`` as the closest point the library would vouch for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.spatial import Delaunay, QhullError

DEFAULT_MIN_PER_LEVEL = 25                       # mirrors StratumGP.MIN_NT_SAMPLES
DEFAULT_SIGMA_REL_MAX = 0.15
_GLOBAL = "__global__"


class OutOfDomainError(Exception):
    def __init__(self, *, criterion: int, reason: str, nearest: list[tuple[Any, float]], fill_points: list[dict]):
        super().__init__(f"out of domain (criterion {criterion}): {reason}")
        self.criterion, self.reason, self.nearest, self.fill_points = criterion, reason, nearest, fill_points


@dataclass
class Verdict:
    ok: bool = True
    nearest: list[tuple[Any, float]] = field(default_factory=list)     # (row id, scaled distance), nearest first


def sigma_ok(mu, sigma, rel_max: float = DEFAULT_SIGMA_REL_MAX) -> np.ndarray:
    """Criterion 4 element-wise: |sigma| / |mu| <= rel_max (mu == 0 with sigma != 0 fails closed)."""
    mu, sigma = np.abs(np.asarray(mu, dtype=float)), np.abs(np.asarray(sigma, dtype=float))
    return sigma / np.maximum(mu, 1e-30) <= rel_max


class DomainGuard:
    def __init__(self, x, dims: list[str], ranges: dict[str, tuple[float, float]], *, nt_dim: str | None = None,
                 min_per_level: int = DEFAULT_MIN_PER_LEVEL, ids=None):
        x = np.asarray(x, dtype=float)
        if x.ndim != 2 or x.shape[1] != len(dims) or len(x) == 0:
            raise ValueError(f"expected [N>0, {len(dims)}] inputs, got {x.shape}")
        if nt_dim is not None and nt_dim not in dims:
            raise ValueError(f"nt_dim {nt_dim!r} not among the dims {dims}")
        self.dims, self.ranges, self.nt_dim, self.min_per_level = list(dims), dict(ranges), nt_dim, min_per_level
        self._x, self._ids = x, list(ids) if ids is not None else list(range(len(x)))
        self._lo = np.array([ranges[d][0] for d in dims], dtype=float)
        self._hi = np.array([ranges[d][1] for d in dims], dtype=float)
        if not (self._hi > self._lo).all():
            raise ValueError(f"degenerate range for dims {[d for d, ok in zip(dims, self._hi > self._lo) if not ok]}")
        self._min, self._max = x.min(axis=0), x.max(axis=0)
        self._nt_idx = dims.index(nt_dim) if nt_dim is not None else None
        self._levels = np.round(x[:, self._nt_idx]).astype(int) if nt_dim is not None else None
        self._hulls: dict[Any, tuple[list[int], dict[int, float], Delaunay | None]] = {}
        self._scaled = (x - self._lo) / (self._hi - self._lo)

    def _level_rows(self, level) -> np.ndarray:
        return np.ones(len(self._x), dtype=bool) if level == _GLOBAL else self._levels == level

    def _hull(self, level) -> tuple[list[int], dict[int, float], Delaunay | None]:
        """(varying dim indices, {fixed dim index: value}, hull over the varying dims) for one level, built lazily."""
        if level not in self._hulls:
            pts = self._x[self._level_rows(level)]
            candidates = [i for i in range(len(self.dims)) if i != self._nt_idx]
            varying = [i for i in candidates if np.ptp(pts[:, i]) > 1e-9]
            fixed = {i: float(pts[0, i]) for i in candidates if i not in varying}
            hull = None
            if varying:
                scaled = (pts[:, varying] - self._lo[varying]) / (self._hi[varying] - self._lo[varying])
                try:
                    hull = Delaunay(scaled)
                except (QhullError, ValueError):
                    hull = None
            self._hulls[level] = (varying, fixed, hull)
        return self._hulls[level]

    def nearest(self, params: dict, k: int = 3) -> list[tuple[Any, float]]:
        q = (self._vector(params) - self._lo) / (self._hi - self._lo)
        d = np.linalg.norm(self._scaled - q, axis=1)
        return [(self._ids[i], float(d[i])) for i in np.argsort(d, kind="stable")[:k]]

    def check(self, params: dict) -> Verdict:
        q = self._vector(params)
        for i, d in enumerate(self.dims):                                          # criterion 1
            if q[i] < self._min[i] - 1e-9 or q[i] > self._max[i] + 1e-9:
                raise self._reject(1, f"{d}={q[i]:g} outside the measured range [{self._min[i]:g}, {self._max[i]:g}]", q)
        level: Any = _GLOBAL
        if self._nt_idx is not None:                                               # criterion 2
            v = q[self._nt_idx]
            if abs(v - round(v)) > 1e-9:
                raise self._reject(2, f"{self.nt_dim}={v:g} is not an integer level", q)
            level = round(v)
            n = int((self._levels == level).sum())
            if n < self.min_per_level:
                raise self._reject(2, f"{self.nt_dim}={level} has {n} rows (< {self.min_per_level})", q)
        varying, fixed, hull = self._hull(level)                                   # criterion 3 (and fixed dims, criterion 1)
        where = "all rows" if level == _GLOBAL else f"{self.nt_dim}={level}"
        for i, value in fixed.items():
            if abs(q[i] - value) > 1e-9:
                raise self._reject(1, f"{self.dims[i]} is {value:g} for every row at {where}; {q[i]:g} has no data", q)
        if varying:
            if hull is None:
                raise self._reject(3, f"degenerate hull at {where} (no reliable coverage)", q)
            scaled = (q[varying] - self._lo[varying]) / (self._hi[varying] - self._lo[varying])
            if hull.find_simplex(scaled) < 0:
                raise self._reject(3, f"outside the convex hull at {where}", q)
        return Verdict(True, self.nearest(params))

    def inside(self, x) -> np.ndarray:
        """``check``'s criteria 1-3 for many points at once, without the evidence: a boolean per row of ``x``."""
        x = np.asarray(x, dtype=float)
        ok = ((x >= self._min - 1e-9) & (x <= self._max + 1e-9)).all(axis=1)
        if self._nt_idx is None:
            groups = {_GLOBAL: np.ones(len(x), dtype=bool)}
        else:
            v = x[:, self._nt_idx]
            ok &= np.abs(v - np.round(v)) <= 1e-9
            groups = {}
            for level in sorted(set(np.round(v[ok]).astype(int).tolist())):
                mask = ok & (np.round(v).astype(int) == level)
                if int((self._levels == level).sum()) < self.min_per_level:
                    ok &= ~mask
                else:
                    groups[level] = mask
        for level, mask in groups.items():
            if not mask.any():
                continue
            varying, fixed, hull = self._hull(level)
            sel = mask & ok
            for i, value in fixed.items():
                sel &= np.abs(x[:, i] - value) <= 1e-9
            if varying:
                if hull is None:
                    sel[:] = False
                else:
                    idx = np.nonzero(sel)[0]
                    scaled = (x[np.ix_(idx, varying)] - self._lo[varying]) / (self._hi[varying] - self._lo[varying])
                    sel[idx[hull.find_simplex(scaled) < 0]] = False
            ok &= ~mask | sel
        return ok

    def _vector(self, params: dict) -> np.ndarray:
        try:
            return np.array([float(params[d]) for d in self.dims], dtype=float)
        except KeyError as exc:
            raise ValueError(f"params missing dim {exc}") from exc

    def _reject(self, criterion: int, reason: str, q: np.ndarray) -> OutOfDomainError:
        query = {d: float(q[i]) for i, d in enumerate(self.dims)}
        clamped = np.clip(q, self._min, self._max)
        return OutOfDomainError(criterion=criterion, reason=reason, nearest=self.nearest(query),
                                fill_points=[query, {d: float(clamped[i]) for i, d in enumerate(self.dims)}])
