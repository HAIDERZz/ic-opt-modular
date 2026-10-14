"""A curve column between two knots, answered from the knots' models without a fit of its own (N-127).

With a frequency grid in library.yaml (N-126) a curve's knots are its anchors and the grid's frequencies where its column
can have a model, and ``lib.prefit`` fits them ahead. A column ``<curve>@<f>`` asked at an f that is no knot but lies
between two of them is answered here, from the knots' models: ``Library.models`` (unless ``exact``) hands out a
``query.Model`` whose ``gp`` is a ``Between`` and whose ``guard`` is a ``Guard``, so every caller -- ``query.query``,
``suggest.predict_all`` and through them ``lib.suggest``, ``lib.region``, ``lib.densify`` and ``lib_design`` -- takes it
like a fitted column. Only what depends on the resonance predicted at the point -- the knots the interpolation may use, its
allowance and the refusals -- is asked of ``Between.answer`` by the two prediction paths, which predict that resonance
anyway (N-113).

The stencil at a point: the two knots below f (one where there is no second), the knot above f, and the second knot above
where the point's resonance lies above its curve's ``srf_margin`` x that knot (the N-113 rule per knot), its model answers
there and its guard accepts the point. The next knot above must pass the same rule: a point whose resonance lies at or
below ``srf_margin`` x that knot is refused (``RESONANCE``) -- a measured row there would have no value at that knot. Each
knot's model gives its mean and its own calibrated bounds at the call's k (its k_scale, and the extrapolation widening of
N-116 by the farthest of the knots' depths); the answer is their PCHIP interpolation in frequency over the stencil, the
bounds then widened by the allowance a to lo / (1 + a) and hi / (1 - a), and never narrower than the mean. Its relative
sigma, which the confidence ceiling judges, interpolates the two bracketing knots' squared relative sigmas linearly in f.

The allowance: N-126 measured PCHIP between the grid's frequencies on the library's own rows within 1.7 % at the 99th
percentile, but not near the rows' resonance limits: a row whose resonance lies just above srf_margin x the next knot (or
just above the sweep, where nothing measures it) bends fast there, and single points were up to 49 % off. So the allowance
is measured per knot interval and per proximity band u = srf_margin x f_next / SRF (``EDGES``; a row or a point whose
resonance lies above the sweep at the bound srf_margin x f_next / the sweep's end): the ``QUANTILE`` percentile of
|PCHIP / measured - 1| at ``PROBES`` inside the interval over the stratum's rows in that band, each row interpolated over
the stencil a point of its own resonance would use, a band with fewer than ``N_MIN`` rows merged with the bands nearer the
resonance until it has them. ``tables`` computes it at first use from the rows' sNp files -- one pass over a stratum's rows
for every declared curve, seconds, the extension measurement's own rule (``dataset._anchor_value``) -- and caches it per
dataset key, curve and knots (``between-<stratum>-<curve>-<dataset key>-<digest>.json``). A point takes the band of its
resonance's calibrated lower bound (the sweep's end above the sweep), so a resonance that may lie closer counts as closer.
Refused besides: a band whose rows are too few even merged (``ROWS``), and an allowance above ``CAP`` (``LARGE``) -- the
interpolation would add more than the knots' own error there. Every refusal says that ``exact=true`` fits the frequency's
own model. A point whose resonance no model gives (a stratum declaring no SRF column) takes the interval's allowance over
all its rows.
"""

from __future__ import annotations

import bisect
import copy
import hashlib
import json
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

import numpy as np
from scipy.interpolate import PchipInterpolator

from ic_opt.em import measure, touchstone
from ic_opt.library import composed, dataset, domain, gp, manifest
from ic_opt.library.cache import Cache, held, read_json, write_json

VERSION = 1                                        # the layout and the method of a between-* file; another version is computed again
PROBES = tuple(k / 8 for k in range(1, 8))         # where inside a knot interval the rows' curves are compared, as fractions of it
QUANTILE = 99.0                                    # the allowance: this percentile of the probes' |PCHIP / measured - 1|
EDGES = (0.0, 0.8, 0.9, 0.95, 1.0)                 # proximity bands of u = srf_margin x f_next / SRF; [1, inf): above the sweep only
N_MIN = 20                                         # rows a band's allowance is measured on, at least (merged towards the resonance)
CAP = 0.05                                         # an allowance above it: no interpolated answer there (exact=true)
BELOW_ONE = 1 - 1e-9                               # a resonance inside the sweep is in a band below 1 (u of its lower bound, clipped)
SOURCE = "N-127: PCHIP in frequency of the knots' models, bounds widened by the measured allowance"
OK, RESONANCE, ROWS, LARGE = 0, 1, 2, 3            # Between.answer's verdict per point


def span(knots: list[float], ghz: float) -> list[float] | None:
    """The stencil's candidate knots for ``ghz`` -- up to two knots below it and up to two above, ascending -- or None when
    ``ghz`` is a knot itself or lies below the first knot or above the last (no interpolation: the column's own model)."""
    if len(knots) < 2 or ghz <= knots[0] or ghz >= knots[-1] or ghz in knots:
        return None
    j = bisect.bisect_left(knots, ghz) - 1                  # knots[j] < ghz < knots[j + 1]
    return list(knots[max(0, j - 1):j + 3])


# -- the rows' own curves: what an interpolation errs by, per knot interval and proximity band ----------------------------

def at(q: measure.Quantities, name: str, x_hz) -> np.ndarray:
    """``Quantities.at`` for many frequencies at once, value for value (``tests``: bit for bit): the sample within
    ``measure.SAMPLE_REL_TOL`` of a frequency (the nearer of two, the lower on a tie), else the curve interpolated linearly
    between the samples around it, between 0 Hz and the first positive sample ``measure._below_first``'s rule; NaN outside
    the sweep (where ``Quantities.at`` refuses)."""
    f, curve = np.asarray(q.freqs, dtype=float), np.asarray(q.curves[name], dtype=float)
    x = np.asarray(x_hz, dtype=float)
    out = np.full(x.shape, np.nan)
    if not len(f):
        return out
    upper = np.searchsorted(f, x)                           # f[upper - 1] < x <= f[upper]
    lo, hi = upper - 1, upper
    has_lo, has_hi = lo >= 0, hi < len(f)
    lo_c, hi_c = np.clip(lo, 0, len(f) - 1), np.clip(hi, 0, len(f) - 1)
    d_lo, d_hi = np.abs(f[lo_c] - x), np.abs(f[hi_c] - x)
    near_lo = has_lo & (d_lo <= measure.SAMPLE_REL_TOL * np.maximum(np.abs(f[lo_c]), np.abs(x)))
    near_hi = has_hi & (d_hi <= measure.SAMPLE_REL_TOL * np.maximum(np.abs(f[hi_c]), np.abs(x)))
    take_lo = near_lo & (~near_hi | (d_lo <= d_hi))
    take_hi = near_hi & ~take_lo
    out[take_lo] = curve[lo_c[take_lo]]
    out[take_hi] = curve[hi_c[take_hi]]
    rest = ~(take_lo | take_hi) & has_lo & has_hi
    first = rest & (f[lo_c] == 0)
    if first.any():
        v1, f1 = curve[hi_c[first]], f[hi_c[first]]
        out[first] = v1 * x[first] / f1 if name.startswith("Q") else v1
    inner = rest & ~first
    if inner.any():
        a, b = lo_c[inner], hi_c[inner]
        t = (x[inner] - f[a]) / (f[b] - f[a])
        out[inner] = curve[a] + t * (curve[b] - curve[a])
    return out


def values(m, curve: str, rule: manifest.Quantity, f_hz) -> np.ndarray:
    """``dataset._anchor_value`` at many frequencies of one measured row ``m`` (``dataset._measure``): NaN outside the row's
    sweep, where its system SRF is at or below the curve's srf_margin x f, and where the curve is not finite."""
    f_hz = np.asarray(f_hz, dtype=float)
    out = at(m.q, curve, f_hz)
    out[(f_hz < m.start) | (f_hz > m.stop)] = np.nan
    srf = dataset._drive_srf(m.q, curve)
    if srf is not None:
        out[srf <= rule.srf_margin * f_hz] = np.nan
    out[~np.isfinite(out)] = np.nan
    return out


def table_file(ds: dataset.Dataset, curve: str, knots: list[float], margin: float) -> str:
    """The cache file of a curve's allowances: keyed by the dataset (its rows, definitions and measure code), the curve, its
    knots and srf_margin, and the method (``VERSION``, ``PROBES``, ``QUANTILE``, ``EDGES``, ``N_MIN``)."""
    digest = hashlib.sha256(json.dumps({"version": VERSION, "knots": [float(k) for k in knots], "srf_margin": float(margin),
                                        "probes": PROBES, "quantile": QUANTILE, "edges": EDGES, "n_min": N_MIN},
                                       sort_keys=True).encode()).hexdigest()[:16]
    return f"between-{ds.stratum}-{curve}-{ds.key}-{digest}.json"


def _valid(data: object, ds: dataset.Dataset, curve: str, knots: list[float]) -> bool:
    return (isinstance(data, dict) and data.get("version") == VERSION and data.get("key") == ds.key and data.get("curve") == curve
            and data.get("knots") == [float(k) for k in knots])


def tables(root: str | Path, ds: dataset.Dataset, rule: manifest.Stratum, knots: dict[str, list[float]], *,
           cache: Cache | None) -> dict[str, dict]:
    """Every curve of ``knots`` (curve -> its knots, ``Library.knots``) with its allowances (``measure_tables``): read from its
    cache file where that holds it, the others measured together in one pass over the rows' sNp files -- holding each one's
    lock file meanwhile, taken in the order of their names, and re-read once held -- and written each to its own file.
    ``cache`` None: measured, nothing read or written."""
    names = {c: table_file(ds, c, k, rule.quantities[c].srf_margin) for c, k in knots.items()}
    if cache is None:
        return measure_tables(root, ds, rule, knots)

    def cached(c: str) -> dict | None:
        data = read_json(cache.find(names[c]))
        return data if _valid(data, ds, c, knots[c]) else None

    out = {c: data for c in knots if (data := cached(c)) is not None}
    todo = [c for c in knots if c not in out]
    if todo:
        with ExitStack() as stack:
            for name in sorted(names[c] for c in todo):
                stack.enter_context(held(cache, name))
            out.update({c: data for c in todo if (data := cached(c)) is not None})
            todo = [c for c in todo if c not in out]
            if todo:
                measured = measure_tables(root, ds, rule, {c: knots[c] for c in todo})
                for c in todo:
                    write_json(cache.target(names[c]), measured[c])
                out.update(measured)
    return {c: out[c] for c in knots}


def measure_tables(root: str | Path, ds: dataset.Dataset, rule: manifest.Stratum, knots: dict[str, list[float]]) -> dict[str, dict]:
    """Each curve's allowance per knot interval and proximity band (module docstring), from one pass over the rows' sNp files:
    every row measured as the dataset measured it (``dataset._measure``) and its curves read at the knots and at ``PROBES``
    of every interval by the extension measurement's rule (``values``)."""
    root = Path(root)
    devices = dataset.part_devices(root, rule)
    n = len(ds.rows)
    data = {}
    for c, ks in knots.items():
        kk = np.asarray(ks, dtype=float)
        probes = kk[:-1, None] + np.asarray(PROBES)[None, :] * np.diff(kk)[:, None] if len(kk) > 1 else np.empty((0, len(PROBES)))
        data[c] = {"knots": kk, "probes": probes, "at_knots": np.full((n, len(kk)), np.nan), "at_probes": np.full((n, *probes.shape), np.nan),
                   "srf": np.full(n, np.nan)}
    stop = np.full(n, np.nan)
    for i, r in enumerate(ds.rows):
        try:
            m = dataset._measure((root / r.snp).parent, devices[r.part], rule)
        except (measure.MeasureError, touchstone.TouchstoneError, OSError) as exc:
            raise dataset.DatasetError(f"{ds.stratum}: {r.part}/{r.obs_id}: its sNp {r.snp} cannot be measured again "
                                       f"({type(exc).__name__}: {exc}); the dataset was built from it") from exc
        stop[i] = m.stop
        for c, d in data.items():
            s = dataset._drive_srf(m.q, c)
            d["srf"][i] = np.nan if s is None else s
            d["at_knots"][i] = values(m, c, rule.quantities[c], d["knots"] * 1e9)
            if d["probes"].size:
                d["at_probes"][i] = values(m, c, rule.quantities[c], d["probes"].ravel() * 1e9).reshape(d["probes"].shape)
    return {c: _table(ds, c, d, stop, rule.quantities[c].srf_margin) for c, d in data.items()}


def _table(ds: dataset.Dataset, curve: str, d: dict, stop: np.ndarray, margin: float) -> dict:
    """One curve's allowances from its rows' values at the knots and the probes and their resonance (``measure_tables``)."""
    kk, probes, vk, vp, srf = d["knots"], d["probes"], d["at_knots"], d["at_probes"], d["srf"]
    intervals = []
    for j in range(len(kk) - 1):
        core = [i for i in (j - 1, j, j + 1) if i >= 0]
        with np.errstate(invalid="ignore"):
            ok = np.isfinite(vk[:, core]).all(axis=1) & np.isfinite(vp[:, j]).all(axis=1) & (vp[:, j] != 0).all(axis=1)
        second = np.isfinite(vk[:, j + 2]) if j + 2 < len(kk) else np.zeros(len(ok), dtype=bool)
        err = np.full((len(ok), len(PROBES)), np.nan)
        for use2 in (True, False):
            rows = ok & (second == use2)
            if not rows.any():
                continue
            stencil = core + ([j + 2] if use2 else [])
            interp = PchipInterpolator(kk[stencil], vk[np.ix_(rows, stencil)].T, axis=0)(probes[j])   # (probes, rows)
            err[rows] = np.abs(interp.T / vp[rows, j] - 1)
        with np.errstate(divide="ignore", invalid="ignore"):
            u = margin * kk[j + 1] * 1e9 / np.where(np.isfinite(srf), srf, stop)
        ok &= np.isfinite(u) & (u >= 0)
        band = np.searchsorted(EDGES, np.where(ok, u, 0.0), side="right") - 1
        e = err[ok]
        entry = {"from": float(kk[j]), "to": float(kk[j + 1]), "rows": int(ok.sum()),
                 "all": float(np.percentile(e, QUANTILE)) if ok.sum() >= N_MIN else None,
                 "p50": float(np.percentile(e, 50)) if ok.any() else None, "max": float(e.max()) if ok.any() else None, "bands": []}
        for b in range(len(EDGES)):
            own = int((ok & (band == b)).sum())
            got = None
            for top in range(b, len(EDGES)):                  # merged with the bands nearer the resonance until N_MIN rows
                rows = ok & (band >= b) & (band <= top)
                if rows.sum() >= N_MIN:
                    got = {"rows": own, "used": int(rows.sum()), "to": top, "allowance": float(np.percentile(err[rows], QUANTILE))}
                    break
            entry["bands"].append(got or {"rows": own, "used": int((ok & (band >= b)).sum()), "to": None, "allowance": None})
        intervals.append(entry)
    return {"version": VERSION, "stratum": ds.stratum, "curve": curve, "key": ds.key, "knots": [float(k) for k in kk],
            "srf_margin": float(margin), "probes": list(PROBES), "quantile": QUANTILE, "edges": list(EDGES), "n_min": N_MIN,
            "intervals": intervals}


def band(u: np.ndarray) -> np.ndarray:
    """The proximity band (index into ``EDGES``; the last is [1, inf)) of each u; -1 where u is unknown (NaN)."""
    u = np.asarray(u, dtype=float)
    out = np.searchsorted(EDGES, np.where(np.isfinite(u), np.maximum(u, 0.0), 0.0), side="right") - 1
    return np.where(np.isfinite(u), out, -1)


# -- the column's model: the knots' models, interpolated -------------------------------------------------------------------

def _rows_of(model) -> int:
    """``query.fit_rows`` (the most rows any GP behind the model was fitted on), without importing query."""
    return max(len(model.rows), getattr(model.gp, "n_train", 0))


def _row_set(model) -> tuple:
    """The rows a knot's model -- and its domain guard -- is built on, as (part, obs id) pairs: knots with the same rows have
    the same domain, which is then checked once."""
    return tuple((r.part, r.obs_id) for r in model.rows)


def _chunk(chunk_bytes: int | None, model) -> int | None:
    if chunk_bytes is None:
        return None
    from ic_opt.library import suggest

    return suggest.rows_per_call(chunk_bytes, _rows_of(model))


def _predict(g, x: np.ndarray, chunk_rows: int | None, *, floor: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """A knot's model ``g`` (its gp) at the rows of ``x``: (mu, sigma), in calls of at most ``chunk_rows`` rows."""
    if not len(x):
        return np.empty(0), np.empty(0)
    if chunk_rows is None or len(x) <= chunk_rows:
        return g.predict(x, floor=floor)
    parts = [g.predict(x[i:i + chunk_rows], floor=floor) for i in range(0, len(x), chunk_rows)]
    return np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts])


class _Shared:
    """A fitted model several composed knots are built on -- their base value, their SRF: the very same objects for every
    knot of a curve (``composed.ComposedGP.attach``) --, predicted once per block of rows within one ``Between`` call
    instead of once per knot. ``ComposedGP`` asks each of its parts ``predict(x, floor=False)``; a block is told apart by
    its buffer, shape and strides (the views of one call's rows), and kept with its rows so no other array takes its
    place meanwhile. Every other attribute is the model's own."""

    def __init__(self, model):
        self.model, self._seen = model, {}

    def predict(self, x, *, floor: bool = True):
        x = np.asarray(x)
        key = (x.__array_interface__["data"][0], x.shape, x.strides, floor)
        if key not in self._seen:
            self._seen[key] = (x, self.model.predict(x, floor=floor))
        return self._seen[key][1]

    def __getattr__(self, name):
        return getattr(self.model, name)


def _sharing(models: list) -> list:
    """The knots' gps for one call: a composed knot as a copy whose base and SRF models are shared (``_Shared``) with the
    other knots built on them; any other as it is. The answers are the knots' own, bit for bit."""
    shared: dict[int, _Shared] = {}
    out = []
    for m in models:
        g = m.gp
        if isinstance(g, composed.ComposedGP) and g.lf is not None:
            lf, srf = g.lf, g.srf
            g = copy.copy(g)                                  # its state without lf / srf (ComposedGP.__getstate__): set them
            g.lf = shared.setdefault(id(lf), _Shared(lf))
            g.srf = None if srf is None else shared.setdefault(id(srf), _Shared(srf))
        out.append(g)
    return out


class Between:
    """A curve column at ``ghz`` between two knots: the ``gp.StratumGP`` interface the library's callers use, on the stencil
    candidates' models (``models``, at ``knots``: up to two below ``ghz`` and up to two above). ``interval`` is the curve's
    allowance table entry for the knot interval around ``ghz`` (``tables``); ``names`` the knots' column names."""

    nt_mode = "between"

    def __init__(self, column: str, ghz: float, knots: list[float], models: list, names: list[str], interval: dict, srf_margin: float):
        self.column, self.ghz = column, float(ghz)
        self.knots, self.models, self.names = [float(k) for k in knots], list(models), list(names)
        self.below = [i for i, k in enumerate(self.knots) if k < self.ghz]
        self.above = [i for i, k in enumerate(self.knots) if k > self.ghz]
        if not self.below or not self.above or len(self.below) > 2 or len(self.above) > 2:
            raise ValueError(f"{column}: a stencil holds one or two knots on each side of {ghz:g} GHz, got {self.knots}")
        self.core = self.below + self.above[:1]
        self.second = self.above[1] if len(self.above) > 1 else None
        self.interval, self.srf_margin = interval, float(srf_margin)
        a, b = self.below[-1], self.above[0]
        self.bracket = (a, b)
        self.t = (self.ghz - self.knots[a]) / (self.knots[b] - self.knots[a])
        self.log_target = all(m.gp.log_target for m in self.models)
        self.k_scale = max(float(m.gp.k_scale) for m in self.models)
        self.n_train = max(_rows_of(m) for m in self.models)
        self.dims = list(self.models[0].gp.dims)
        core_rows = {_row_set(self.models[i]) for i in self.core}
        self._second_own_domain = self.second is not None and _row_set(self.models[self.second]) not in core_rows

    @property
    def f_next(self) -> float:
        """The knot above ``ghz``, in GHz."""
        return self.knots[self.above[0]]

    @property
    def unavailable_nt(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for m in self.models:
            for level, rows in getattr(m.gp, "unavailable_nt", {}).items():
                out[level] = max(out.get(level, 0), rows)
        return out

    def available(self, x) -> np.ndarray:
        """Rows every knot of the stencil's core (the knots below ``ghz`` and the next above) answers."""
        x = np.asarray(x, dtype=float)
        ok = np.ones(len(x), dtype=bool)
        for i in self.core:
            ok &= self.models[i].gp.available(x)
        return ok

    def allowance(self, u) -> np.ndarray:
        """Per point, the interval's allowance for proximity ``u`` (a fraction; NaN where its band's rows are too few), u NaN:
        the interval's allowance over all its rows."""
        bands = band(u)
        table = [np.nan if e["allowance"] is None else e["allowance"] for e in self.interval["bands"]]
        everything = np.nan if self.interval.get("all") is None else self.interval["all"]
        return np.where(bands >= 0, np.asarray(table)[np.maximum(bands, 0)], everything)

    def _interpolate(self, ys: dict[int, np.ndarray], second: np.ndarray) -> np.ndarray:
        """PCHIP at ``ghz`` of the knots' values ``ys`` (knot index -> per row), over the core and, where ``second``, the
        second knot above too; NaN where a knot of the stencil has no value (its model does not answer there)."""
        n = len(second)
        out = np.full(n, np.nan)
        for use2 in (True, False) if self.second is not None else (False,):
            stencil = self.core + ([self.second] if use2 else [])
            y = np.vstack([ys[i] for i in stencil])                           # (knots, rows)
            rows = (second if use2 else ~second) & np.isfinite(y).all(axis=0)
            if rows.any():
                out[rows] = PchipInterpolator(np.asarray([self.knots[i] for i in stencil]), y[:, rows], axis=0)(self.ghz)
        return out

    def _rel(self, mus: dict[int, np.ndarray], sigmas: dict[int, np.ndarray]) -> np.ndarray:
        """The relative sigma at ``ghz``: the two bracketing knots' squared relative sigmas interpolated linearly in f."""
        a, b = self.bracket
        with np.errstate(divide="ignore", invalid="ignore"):
            ra, rb = sigmas[a] / np.abs(mus[a]), sigmas[b] / np.abs(mus[b])
        return np.sqrt((1 - self.t) * ra * ra + self.t * rb * rb)

    def _second(self, x: np.ndarray, use: np.ndarray | None, extrapolate: float) -> np.ndarray:
        """Rows the second knot above joins the stencil at: ``use`` (its resonance rule; None: everywhere), where its model
        answers and its guard accepts the point."""
        if self.second is None or not len(x):
            return np.zeros(len(x), dtype=bool)
        m = self.models[self.second]
        out = np.ones(len(x), dtype=bool) if use is None else np.asarray(use, dtype=bool).copy()
        out &= m.gp.available(x)
        if out.any() and (self._second_own_domain or use is None):     # on a core knot's rows: the core's domain already
            out[out] &= m.guard.inside(x[out], extrapolate=extrapolate)
        return out

    def _knots(self, x: np.ndarray, second: np.ndarray, chunk_bytes: int | None, *, floor: bool = True):
        """Each knot's (mu, sigma): the core's at every row, the second knot's where ``second`` (NaN elsewhere); the parts
        composed knots share predicted once (``_sharing``)."""
        gps = _sharing(self.models)
        mus, sigmas = {}, {}
        for i in self.core:
            mus[i], sigmas[i] = _predict(gps[i], x, _chunk(chunk_bytes, self.models[i]), floor=floor)
        if self.second is not None:
            mu, sigma = np.full(len(x), np.nan), np.full(len(x), np.nan)
            if second.any():
                mu[second], sigma[second] = _predict(gps[self.second], x[second], _chunk(chunk_bytes, self.models[self.second]),
                                                     floor=floor)
            mus[self.second], sigmas[self.second] = mu, sigma
        return mus, sigmas

    def predict(self, x, *, floor: bool = True) -> tuple[np.ndarray, np.ndarray]:
        """(mu, sigma) without the resonance at the points (``answer`` has it): mu the PCHIP of the knots' means -- the second
        knot above wherever its model answers --, sigma mu x the relative sigma (``_rel``); ``floor`` as the knots' own
        ``predict`` takes it (False: their raw posterior sigma, what ``lib.densify`` scores)."""
        x = np.asarray(x, dtype=float)
        second = self._second(x, None, 0.0)
        mus, sigmas = self._knots(x, second, None, floor=floor)
        mu = self._interpolate(mus, second)
        return mu, self._rel(mus, sigmas) * np.abs(mu)

    def predict_bounds(self, x, k: float = 2.0) -> tuple[np.ndarray, np.ndarray]:
        """``answer``'s bounds without the resonance at the points (the interval's allowance over all its rows)."""
        got = self.answer(np.asarray(x, dtype=float), k)
        return got["lo"], got["hi"]

    def posterior_cov(self, x) -> tuple[np.ndarray, np.ndarray]:
        """(mean, covariance) at the rows of ``x`` in the space the column is answered in -- log for a log target, else the
        column's unit --: the two bracketing knots' posterior covariances weighted as ``_rel`` weights their variances, so
        its diagonal is the variance ``predict(floor=False)`` reports (``lib.densify``'s selection)."""
        x = np.asarray(x, dtype=float)
        a, b = self.bracket
        mu, _sigma = self.predict(x, floor=False)
        covs = []
        for i in (a, b):
            m_i, c_i = self.models[i].gp.posterior_cov(x)
            if not self.log_target and self.models[i].gp.log_target:     # into the column's unit (delta method)
                scale = np.exp(m_i)
                c_i = np.outer(scale, scale) * c_i
            covs.append(c_i)
        cov = (1 - self.t) * covs[0] + self.t * covs[1]
        with np.errstate(divide="ignore", invalid="ignore"):
            mean = np.log(mu) if self.log_target else mu
        return mean, cov

    def answer(self, x: np.ndarray, k, *, resonance_hz: np.ndarray | None = None, resonance_lo_hz: np.ndarray | None = None,
               above: np.ndarray | None = None, extrapolate: float = 0.0, chunk_bytes: int | None = None) -> dict[str, np.ndarray]:
        """The column at the rows of ``x``: ``value``, ``sigma``, ``lo``, ``hi`` (the knots' bounds at ``k`` -- a number, or one
        per row: the call's k times its extrapolation widening --, interpolated and widened by ``allowance``), ``allowance``
        (a fraction), ``second`` (the second knot above used), ``u`` (the proximity its band was taken at) and ``verdict``
        (``OK``, ``RESONANCE``, ``ROWS`` or ``LARGE``; module docstring). ``resonance_hz``: per row the resonance that limits
        the curve (the lowest of its resonance columns' values; +inf where they lie above the sweep), ``resonance_lo_hz`` its
        calibrated lower bound (the sweep's end above the sweep), ``above`` where every one lies above the sweep; None: no
        model gives the resonance, so no knot is refused for it and the allowance is the interval's over all its rows."""
        x = np.asarray(x, dtype=float)
        n = len(x)
        verdict = np.full(n, OK)
        if resonance_hz is None:
            use2, u = None, np.full(n, np.nan)
        else:
            res = np.asarray(resonance_hz, dtype=float)
            settled = np.zeros(n, dtype=bool) if above is None else np.asarray(above, dtype=bool)
            next_hz = self.srf_margin * self.f_next * 1e9
            verdict[~(settled | (res > next_hz))] = RESONANCE
            use2 = (settled | (res > self.srf_margin * self.knots[self.second] * 1e9)) if self.second is not None else None
            low = np.asarray(resonance_lo_hz if resonance_lo_hz is not None else res, dtype=float)
            with np.errstate(divide="ignore", invalid="ignore"):
                u = next_hz / low
            u = np.where(settled, u, np.minimum(u, BELOW_ONE))
        second = self._second(x, use2, extrapolate)
        mus, sigmas = self._knots(x, second, chunk_bytes)
        k = np.broadcast_to(np.asarray(k, dtype=float), (n,))
        los, his = {}, {}
        for i, m in enumerate(self.models):
            if i in mus:
                los[i], his[i] = gp.prediction_bounds(mus[i], sigmas[i], log_target=m.gp.log_target, k=k * float(m.gp.k_scale))
        mu = self._interpolate(mus, second)
        lo, hi = self._interpolate(los, second), self._interpolate(his, second)
        a = self.allowance(u)
        fine = verdict == OK
        verdict[fine & ~np.isfinite(a)] = ROWS
        with np.errstate(invalid="ignore"):
            verdict[(verdict == OK) & (a > CAP)] = LARGE
        widen = np.where(np.isfinite(a), np.clip(a, 0.0, min(CAP, 0.5)), 0.0)    # a refused point's bounds stay finite
        with np.errstate(invalid="ignore"):
            lo = np.minimum(lo - np.abs(lo) * widen / (1 + widen), mu)
            hi = np.maximum(hi + np.abs(hi) * widen / (1 - widen), mu)
        rel = self._rel(mus, sigmas)
        return {"value": mu, "sigma": rel * np.abs(mu), "lo": lo, "hi": hi, "allowance": a, "second": second, "u": u, "verdict": verdict}

    def used(self, second: bool) -> list[float]:
        """The knots a point's interpolation used: the core and, where ``second``, the second knot above."""
        return [self.knots[i] for i in self.core + ([self.second] if second and self.second is not None else [])]

    def refusal(self, verdict: int, *, resonance_hz: float | None = None, u: float | None = None, allowance: float | None = None) -> str:
        """The reason of an answer this interpolation refuses (``answer``'s verdict), naming ``exact=true``."""
        a, b = self.knots[self.bracket[0]], self.knots[self.bracket[1]]
        head = f"between the knots {a:g} and {b:g} GHz and next to its resonance"
        tail = "exact=true fits this frequency's own model"
        if verdict == RESONANCE:
            shown = "" if resonance_hz is None else f" ({resonance_hz / 1e9:.4g} GHz, at or below srf_margin x {b:g} GHz = {self.srf_margin * b:.4g} GHz)"
            return f"{head}{shown}; {tail}"
        if verdict == ROWS:
            where = "" if u is None or not np.isfinite(u) else f" (u = srf_margin x {b:g} GHz / resonance = {u:.3g})"
            return (f"{head}: fewer than {N_MIN} of the table's rows lie as near their resonance in this interval{where}, too few "
                    f"to measure how far an interpolation errs there; {tail}")
        shown = "" if allowance is None else f" {100 * allowance:.3g} %"
        return (f"{head}: interpolated between its knots, the table's rows as near their resonance err by{shown} at the "
                f"{QUANTILE:g}th percentile, more than the {100 * CAP:g} % an interpolated answer may add; {tail}")


# -- the domain: the knots' guards ----------------------------------------------------------------------------------------

class Guard:
    """The domain of a ``Between`` column: every knot of its stencil's core (``models``, named ``names``) must accept the
    point -- the second knot above is checked where it is used (``Between.answer``). ``own`` is a guard over the column's
    own rows, which gives the nearest rows (the column's ``Model.rows``) and nothing else."""

    def __init__(self, models: list, names: list[str], own: domain.DomainGuard):
        self.models, self.names, self.own = list(models), list(names), own
        self.dims = own.dims
        seen: dict[tuple, object] = {}
        for m in self.models:                            # knots on the same rows have the same domain: checked once in a batch
            seen.setdefault(_row_set(m), m)
        self._distinct = list(seen.values())

    def check(self, params: dict, *, extrapolate: float = 0.0) -> domain.Verdict:
        verdicts = []
        for m, name in zip(self.models, self.names):
            try:
                verdicts.append(m.guard.check(params, extrapolate=extrapolate))
            except domain.OutOfDomainError as exc:
                raise domain.OutOfDomainError(criterion=exc.criterion, reason=f"knot {name}: {exc.reason}", nearest=self.own.nearest(params),
                                              fill_points=exc.fill_points) from exc
        extrapolated: dict[str, dict] = {}
        for v in verdicts:
            for d, e in v.extrapolated.items():
                if d not in extrapolated or e["beyond"] > extrapolated[d]["beyond"]:
                    extrapolated[d] = e
        return domain.Verdict(True, self.own.nearest(params), extrapolated, max((v.depth for v in verdicts), default=0.0))

    def inside(self, x, *, extrapolate: float = 0.0) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        ok = np.ones(len(x), dtype=bool)
        for m in self._distinct:
            if ok.any():
                ok[ok] &= m.guard.inside(x[ok], extrapolate=extrapolate)
        return ok

    def depth(self, x) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        return np.max([m.guard.depth(x) for m in self._distinct], axis=0) if len(x) else np.zeros(0)

    def nearest(self, params: dict, k: int = 3):
        return self.own.nearest(params, k)

    def extrapolation(self, params: dict, extrapolate: float = domain.EXTRAPOLATE_FRACTION) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for m in self.models:
            for d, e in m.guard.extrapolation(params, extrapolate).items():
                if d not in out or e["beyond"] > out[d]["beyond"]:
                    out[d] = e
        return out

    def allowance(self, extrapolate: float = domain.EXTRAPOLATE_FRACTION) -> dict[str, tuple[float, float]]:
        """Per extrapolated dim, the shortest of the knots' allowances below and above."""
        out: dict[str, tuple[float, float]] = {}
        for m in self.models:
            for d, (below, above) in m.guard.allowance(extrapolate).items():
                was = out.get(d, (below, above))
                out[d] = (min(below, was[0]), min(above, was[1]))
        return out


def resonance_at(answers: dict[str, dict]) -> tuple[float | None, float | None, bool]:
    """From ``lib.query``'s answers of a curve's resonance columns at one point: (the resonance that limits the curve -- the
    lowest predicted value, +inf where every one lies above the sweep --, its lower bound -- the calibrated ``lo``, the
    sweep's end above the sweep --, whether every one lies above the sweep). (None, None, False) without answers."""
    if not answers:
        return None, None, False
    values, lows, settled = [], [], []
    for a in answers.values():
        if a["status"] == "above_sweep":
            values.append(np.inf)
            lows.append(float(a["lower_bound"]))
            settled.append(True)
        else:
            values.append(float(a["value"]))
            lows.append(float(a["lo"]))
            settled.append(False)
    return min(values), min(lows), all(settled)


def notes(models: dict, describe: Callable[[str], str] | None = None) -> list[str]:
    """One line per column among ``models`` answered between its knots: which knots, and that ``exact=true`` fits its own."""
    out = []
    for q, m in models.items():
        g = getattr(m, "gp", None)
        if isinstance(g, Between):
            out.append(f"{q}: answered between its knots {g.knots[g.bracket[0]]:g} and {g.knots[g.bracket[1]]:g} GHz from their models "
                       f"(knots {', '.join(f'{k:g}' for k in g.knots)} GHz), its interval widened by the interpolation's allowance; "
                       "exact=true fits this frequency's own model")
    return out
