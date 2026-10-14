"""Fail-closed domain guard: may the model answer at this point? A pure geometric gate over the rows a model was fitted on.

Ported from em-opt ``surrogate/domain.py`` (2026-09-23). A query is in domain iff, in this order:
  1. every dim lies inside the ACHIEVED per-dim min/max of the rows (not the nominal ranges);
  2. its integer turns level has at least ``min_per_level`` rows (the model's sub-GP floor); skipped when the
     family has no turns dim;
  3. it lies inside the convex hull of its level (or of all rows without a turns dim), in min-max scaled
     coordinates -- the achieved box can have notches the per-dim test misses. A degenerate hull fails closed.
Criterion 4 (relative sigma) asks a different question -- is the model confident here -- and is the
separate ``sigma_ok``.

Changed after the T13.0 verification: each level's hull uses only the dims that VARY inside that level. The
shipped guard built every level's hull over all continuous dims, so a level with one value of some dim
(single-turn inductors all have one spacing: spacing does not change their geometry) was degenerate and
every query at that level was rejected. A dim fixed within a level now has to match that value exactly
(criterion 1: the level has no data elsewhere along it).

Extrapolation (N-116, 2026-10-14): ``check`` and ``inside`` take ``extrapolate``, a fraction F (0, their default, is the
guard above, unchanged). With F > 0 a point may lie beyond the achieved range along the outer diameters
(``EXTRAPOLATE_DIMS``, whichever the model has) and nowhere else, by at most the dim's allowance on that side,
F x min(range, boundary) (``allowances``) -- ``range`` the achieved max - min, ``boundary`` the achieved min (below) or
max (above), so the small end stays conservative -- and criterion 3 is then judged on the point with those dims clamped
back onto the achieved range (the device a scaled version of one the data surround). Criterion 2 is unchanged. N-115
(``docs/refactor/reports/library_query/extrapolation_study.py``; table 1b of its results) measured that the models hold
along the outer diameters one to two level steps out and along no other dim. ``Verdict.extrapolated`` (and
``extrapolation`` for a point already in domain) names each dim beyond the range -- side, how far, the allowance, the
measured range -- and ``Verdict.depth`` (``depth`` for many points) how far in allowances at ``EXTRAPOLATE_FRACTION``,
what an answer's interval widens with (``query.widening``); ``query`` and ``suggest.predict_all`` pass the call's F.

A rejection carries the criterion, a reason, the three nearest rows and ``[query, query clamped onto the
achieved box]`` as the closest point the library would vouch for (with F > 0, onto that box widened by the allowances).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.spatial import ConvexHull, QhullError

DEFAULT_MIN_PER_LEVEL = 25                       # mirrors StratumGP.MIN_NT_SAMPLES
DEFAULT_SIGMA_REL_MAX = 0.15
EXTRAPOLATE_DIMS = ("outer_diameter_um", "primary_outer_diameter_um", "secondary_outer_diameter_um")   # N-116: the only dims
EXTRAPOLATE_FRACTION = 0.15                      # N-116: the allowance per side, F x min(range, boundary); 0 switches it off
_GLOBAL = "__global__"


class OutOfDomainError(Exception):
    def __init__(self, *, criterion: int, reason: str, nearest: list[tuple[Any, float]], fill_points: list[dict]):
        super().__init__(f"out of domain (criterion {criterion}): {reason}")
        self.criterion, self.reason, self.nearest, self.fill_points = criterion, reason, nearest, fill_points


@dataclass
class Verdict:
    ok: bool = True
    nearest: list[tuple[Any, float]] = field(default_factory=list)     # (row id, scaled distance), nearest first
    extrapolated: dict[str, dict] = field(default_factory=dict)       # dim beyond the measured range -> side, beyond, allowance, measured_range
    depth: float = 0.0                                                 # the farthest of them, in allowances at EXTRAPOLATE_FRACTION


def sigma_ok(mu, sigma, rel_max: float = DEFAULT_SIGMA_REL_MAX) -> np.ndarray:
    """Criterion 4 element-wise: |sigma| / |mu| <= rel_max (mu == 0 with sigma != 0 fails closed)."""
    mu, sigma = np.abs(np.asarray(mu, dtype=float)), np.abs(np.asarray(sigma, dtype=float))
    return sigma / np.maximum(mu, 1e-30) <= rel_max


def in_hull(hull: ConvexHull, points: np.ndarray, tol: float = 1e-9) -> np.ndarray:
    """Membership in a convex hull by its facet half-spaces (normal . x + offset <= 0 for every facet), a boolean
    per row. The same set Delaunay.find_simplex >= 0 answers, but as one matrix product instead of a simplex walk
    per point: in 5-6 dims a library level triangulates into 1e5-1e6 simplices and the walk cost milliseconds per
    point, while its hull has a few hundred to a few thousand facets. Chunked so a large batch stays within memory."""
    points = np.asarray(points, dtype=float)
    normals, offsets = hull.equations[:, :-1], hull.equations[:, -1]
    out = np.empty(len(points), dtype=bool)
    chunk = max(1, int(2e7 // max(len(offsets), 1)))
    for start in range(0, len(points), chunk):
        block = points[start:start + chunk]
        out[start:start + chunk] = (block @ normals.T + offsets <= tol).all(axis=1)
    return out


def allowances(dims: list[str], lo, hi, extrapolate: float) -> tuple[np.ndarray, np.ndarray]:
    """Per dim, how far beyond the range [lo, hi] a point may lie, below and above: ``extrapolate`` x min(hi - lo, |lo|)
    and x min(hi - lo, |hi|) along ``EXTRAPOLATE_DIMS``, 0 along every other dim (and everywhere for 0). The guard's
    rule; ``suggest.pool`` and ``lib.region``'s grid extend their boxes by the same amounts."""
    lo, hi = np.asarray(lo, dtype=float), np.asarray(hi, dtype=float)
    along = np.array([d in EXTRAPOLATE_DIMS for d in dims], dtype=bool)
    span = hi - lo
    f = max(float(extrapolate), 0.0)
    return np.where(along, f * np.minimum(span, np.abs(lo)), 0.0), np.where(along, f * np.minimum(span, np.abs(hi)), 0.0)


def _label(side: str, beyond: float, allowance: float, lo: float, hi: float) -> dict:
    """One dim's extrapolation label, its lengths rounded to 1e-9 (the library's coordinate resolution)."""
    return {"side": side, "beyond": round(float(beyond), 9), "allowance": round(float(allowance), 9),
            "measured_range": [float(lo), float(hi)]}


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
        self._hulls: dict[Any, tuple[list[int], dict[int, float], ConvexHull | None]] = {}
        self._scaled = (x - self._lo) / (self._hi - self._lo)
        self._unit_below, self._unit_above = allowances(self.dims, self._min, self._max, EXTRAPOLATE_FRACTION)   # what depth counts in

    def _level_rows(self, level) -> np.ndarray:
        return np.ones(len(self._x), dtype=bool) if level == _GLOBAL else self._levels == level

    def _hull(self, level) -> tuple[list[int], dict[int, float], ConvexHull | None]:
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
                    hull = ConvexHull(scaled)
                except (QhullError, ValueError):
                    hull = None
            self._hulls[level] = (varying, fixed, hull)
        return self._hulls[level]

    def allowance(self, extrapolate: float = EXTRAPOLATE_FRACTION) -> dict[str, tuple[float, float]]:
        """Per extrapolated dim of this guard (``EXTRAPOLATE_DIMS``): how far below and above its measured range a point
        may lie with ``extrapolate``."""
        below, above = allowances(self.dims, self._min, self._max, extrapolate)
        return {d: (float(below[i]), float(above[i])) for i, d in enumerate(self.dims) if d in EXTRAPOLATE_DIMS}

    def nearest(self, params: dict, k: int = 3) -> list[tuple[Any, float]]:
        q = (self._vector(params) - self._lo) / (self._hi - self._lo)
        d = np.linalg.norm(self._scaled - q, axis=1)
        return [(self._ids[i], float(d[i])) for i in np.argsort(d, kind="stable")[:k]]

    def check(self, params: dict, *, extrapolate: float = 0.0) -> Verdict:
        """The verdict at one point (criteria 1-3), raising OutOfDomainError on a refusal; ``extrapolate`` as in the module
        docstring."""
        q = self._vector(params)
        below, above = allowances(self.dims, self._min, self._max, extrapolate)
        extrapolated: dict[str, dict] = {}
        for i, d in enumerate(self.dims):                                          # criterion 1
            if q[i] < self._min[i] - 1e-9 or q[i] > self._max[i] + 1e-9:
                side, beyond, allow = (("below", self._min[i] - q[i], below[i]) if q[i] < self._min[i] else
                                       ("above", q[i] - self._max[i], above[i]))
                if allow > 0 and self._min[i] - below[i] - 1e-9 <= q[i] <= self._max[i] + above[i] + 1e-9:
                    extrapolated[d] = _label(side, beyond, allow, self._min[i], self._max[i])
                    continue
                raise self._reject(1, self._outside(i, q[i], side, beyond, allow, extrapolate), q, extrapolate)
        level: Any = _GLOBAL
        if self._nt_idx is not None:                                               # criterion 2
            v = q[self._nt_idx]
            if abs(v - round(v)) > 1e-9:
                raise self._reject(2, f"{self.nt_dim}={v:g} is not an integer level", q, extrapolate)
            level = round(v)
            n = int((self._levels == level).sum())
            if n < self.min_per_level:
                raise self._reject(2, f"{self.nt_dim}={level} has {n} rows (< {self.min_per_level})", q, extrapolate)
        varying, fixed, hull = self._hull(level)                                   # criterion 3 (and fixed dims, criterion 1)
        where = "all rows" if level == _GLOBAL else f"{self.nt_dim}={level}"
        p = q.copy()                                                               # judged with the extrapolated dims clamped
        for d in extrapolated:
            i = self.dims.index(d)
            p[i] = min(max(q[i], self._min[i]), self._max[i])
        for i, value in fixed.items():
            if abs(p[i] - value) > 1e-9:
                raise self._reject(1, f"{self.dims[i]} is {value:g} for every row at {where}; {q[i]:g} has no data", q, extrapolate)
        if varying:
            if hull is None:
                raise self._reject(3, f"degenerate hull at {where} (no reliable coverage)", q, extrapolate)
            scaled = (p[varying] - self._lo[varying]) / (self._hi[varying] - self._lo[varying])
            if not in_hull(hull, scaled[None, :])[0]:
                clamped = ", ".join(f"{d}={p[self.dims.index(d)]:g}" for d in extrapolated)
                raise self._reject(3, f"outside the convex hull at {where}" +
                                   (f" (judged with {clamped}: clamped onto the measured range)" if extrapolated else ""), q, extrapolate)
        depth = float(self.depth(q[None, :])[0]) if extrapolated else 0.0
        return Verdict(True, self.nearest(params), extrapolated, depth)

    def inside(self, x, *, extrapolate: float = 0.0) -> np.ndarray:
        """``check``'s criteria 1-3 for many points at once, without the evidence: a boolean per row of ``x``."""
        x = np.asarray(x, dtype=float)
        below, above = allowances(self.dims, self._min, self._max, extrapolate)
        ok = ((x >= self._min - below - 1e-9) & (x <= self._max + above + 1e-9)).all(axis=1)
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
                sel &= np.abs(self._clamp(x[:, i], i) - value) <= 1e-9
            if varying:
                if hull is None:
                    sel[:] = False
                else:
                    idx = np.nonzero(sel)[0]
                    pts = x[np.ix_(idx, varying)]                                  # the extrapolated dims clamped (criterion 3)
                    for k, i in enumerate(varying):
                        pts[:, k] = self._clamp(pts[:, k], i)
                    scaled = (pts - self._lo[varying]) / (self._hi[varying] - self._lo[varying])
                    sel[idx[~in_hull(hull, scaled)]] = False
            ok &= ~mask | sel
        return ok

    def _clamp(self, values: np.ndarray, i: int) -> np.ndarray:
        """Dim ``i``'s ``values`` with those beyond the measured range (by more than the tolerance) clamped onto it: what
        criterion 3 judges an extrapolated point by (``check``); the values themselves where none is beyond."""
        out = (values < self._min[i] - 1e-9) | (values > self._max[i] + 1e-9)
        return np.where(out, np.clip(values, self._min[i], self._max[i]), values) if out.any() else values

    def depth(self, x) -> np.ndarray:
        """Per row of ``x``, how far it lies beyond the measured range along the extrapolated dims: the largest distance
        beyond, over that side's allowance at ``EXTRAPOLATE_FRACTION`` (1 at the default allowance's end); 0 inside the range
        and along every other dim. Per box alone: whether the row is in domain is ``inside``'s answer."""
        x = np.asarray(x, dtype=float)
        lo = np.where(x < self._min - 1e-9, self._min - x, 0.0)
        hi = np.where(x > self._max + 1e-9, x - self._max, 0.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.maximum(np.where(lo > 0, lo / self._unit_below, 0.0), np.where(hi > 0, hi / self._unit_above, 0.0))
        r = np.where([d in EXTRAPOLATE_DIMS for d in self.dims], r, 0.0)
        return r.max(axis=1) if r.shape[1] else np.zeros(len(x))

    def extrapolation(self, params: dict, extrapolate: float = EXTRAPOLATE_FRACTION) -> dict[str, dict]:
        """``check``'s label for a point already in domain: per extrapolated dim beyond the measured range, its side, how far,
        the allowance and the measured range (criterion 1's part only; nothing is refused here)."""
        q = self._vector(params)
        below, above = allowances(self.dims, self._min, self._max, extrapolate)
        out = {}
        for i, d in enumerate(self.dims):
            if d in EXTRAPOLATE_DIMS and (q[i] < self._min[i] - 1e-9 or q[i] > self._max[i] + 1e-9):
                low = q[i] < self._min[i]
                out[d] = _label("below" if low else "above", self._min[i] - q[i] if low else q[i] - self._max[i],
                                below[i] if low else above[i], self._min[i], self._max[i])
        return out

    def _outside(self, i: int, value: float, side: str, beyond: float, allow: float, extrapolate: float) -> str:
        """Criterion 1's reason for dim ``i``: as before N-116 when nothing is extrapolated (``extrapolate`` 0); else how far
        beyond the measured range, and the allowance where the dim has one."""
        d, lo, hi = self.dims[i], self._min[i], self._max[i]
        head = f"{d}={value:g} outside the measured range [{lo:g}, {hi:g}]"
        if not extrapolate > 0:
            return head
        if d in EXTRAPOLATE_DIMS:
            return (f"{head}: {beyond:g} {side} it, beyond the allowance of {allow:g} there "
                    f"(extrapolate={extrapolate:g} x min(range, boundary))")
        own = [e for e in self.dims if e in EXTRAPOLATE_DIMS]
        tail = (f"only {' and '.join(own)} {'is' if len(own) == 1 else 'are'} extrapolated" if own else
                "this model extrapolates along no dim")
        return f"{head}: {beyond:g} {side} it; {tail}"

    def _vector(self, params: dict) -> np.ndarray:
        try:
            return np.array([float(params[d]) for d in self.dims], dtype=float)
        except KeyError as exc:
            raise ValueError(f"params missing dim {exc}") from exc

    def _reject(self, criterion: int, reason: str, q: np.ndarray, extrapolate: float = 0.0) -> OutOfDomainError:
        query = {d: float(q[i]) for i, d in enumerate(self.dims)}
        below, above = allowances(self.dims, self._min, self._max, extrapolate)
        clamped = np.clip(q, self._min - below, self._max + above)
        return OutOfDomainError(criterion=criterion, reason=reason, nearest=self.nearest(query),
                                fill_points=[query, {d: float(clamped[i]) for i, d in enumerate(self.dims)}])
