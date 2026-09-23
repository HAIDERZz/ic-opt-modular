"""Answer questions about a library: what was measured, what the model predicts where nothing was, and how much is covered.

``Library`` is the runtime handle on a library root: it builds each stratum's dataset once, fits one
model per (stratum, quantity) on that quantity's usable rows (per_nt Matern 5/2, log target for every
positive quantity, the T13.0 choice) with a domain guard over the same rows, and widens the model's
2-sigma interval by the calibration factor derived from held-out residuals (cached next to the dataset).

``query`` answers a geometry: a measured row at exactly those coordinates is returned as measured;
otherwise each quantity is either predicted (mu with calibrated bounds, plus the three nearest measured
rows as evidence), out of domain (the guard's criterion, reason, nearest rows and the clamped point), or
flagged uncertain (criterion 4: sigma/mu above the ceiling -- reported with its numbers, never silently
used). SRF has one more answer: when most of the nearest measured rows had no resonance inside their
sweep, the point's resonance is reported as above the sweep instead of extrapolated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ic_opt.library import dataset, domain, gp, manifest

UNITS = {"L": "H", "Q": "1", "SRF": "Hz", "k": "1"}
ABOVE_SWEEP_VOTES = 3                                # of the 5 nearest measured rows


def unit(quantity: str) -> str:
    base = quantity.split("@")[0]
    return UNITS["SRF"] if base.startswith("SRF") else UNITS[base[0]] if base[0] in "LQ" else UNITS["k"]


@dataclass
class Model:
    stratum: str
    quantity: str
    rows: list[dataset.Row]
    gp: gp.StratumGP
    guard: domain.DomainGuard
    calibration: dict                                # k_scale, held-out median_rel and coverage behind it


class Library:
    def __init__(self, root: str | Path, *, calibrate: bool = True):
        self.root = Path(root)
        self.manifest = manifest.load(self.root)
        self.calibrate = calibrate
        self._datasets: dict[str, dataset.Dataset] = {}
        self._models: dict[tuple[str, str], Model] = {}

    def strata(self) -> list[str]:
        return sorted(self.manifest.strata)

    def dataset(self, stratum: str) -> dataset.Dataset:
        if stratum not in self._datasets:
            self._datasets[stratum] = dataset.build(self.root, stratum, library=self.manifest)
        return self._datasets[stratum]

    def ranges(self, stratum: str) -> dict[str, tuple[float, float]]:
        """Scaling ranges: the achieved min/max of every dim (a dim with one value gets a unit span)."""
        x = self.dataset(stratum).matrix()
        out = {}
        for i, d in enumerate(self.dataset(stratum).dims):
            lo, hi = float(x[:, i].min()), float(x[:, i].max())
            out[d] = (lo, hi) if hi > lo else (lo - 0.5, hi + 0.5)
        return out

    def model(self, stratum: str, quantity: str) -> Model:
        key = (stratum, quantity)
        if key not in self._models:
            ds = self.dataset(stratum)
            if quantity not in ds.columns:
                raise ValueError(f"{stratum} has no quantity {quantity!r}; columns {ds.columns}")
            rows = ds.usable(quantity)
            x, y = ds.matrix(rows), ds.values(quantity, rows)
            if quantity.startswith("SRF"):
                y = y / 1e9                                  # GHz keeps the log-GP numerically tame; mapped back on output
            settings = {"dims": ds.dims, "ranges": self.ranges(stratum), "log_target": bool((y > 0).all()),
                        "nt_mode": "per_nt" if ds.nt_dim else "joint", "kernel": "matern52", "nt_dim": ds.nt_dim}
            calibration = self._calibration(ds, quantity, x, y, settings)
            model = gp.StratumGP(**settings, k_scale=calibration["k_scale"]).fit(x, y)
            guard = domain.DomainGuard(x, ds.dims, settings["ranges"], nt_dim=ds.nt_dim, ids=list(range(len(rows))))
            self._models[key] = Model(stratum, quantity, rows, model, guard, calibration)
        return self._models[key]

    def _calibration(self, ds: dataset.Dataset, quantity: str, x, y, settings: dict) -> dict:
        if not self.calibrate:
            return {"k_scale": 1.0, "source": "off"}
        path = self.root / ".cache" / f"calibration-{ds.stratum}-{quantity.replace('@', '_at_')}-{ds.key}.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        report = gp.holdout(x, y, **settings)
        out = {"k_scale": gp.calibration_scale(report), "median_rel": report["median_rel"],
               "coverage_2sigma_before": report["coverage_2sigma"], "n_scored": report["n_scored"], "source": "holdout 5x20%"}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out), encoding="utf-8")
        return out


def query(library: Library, stratum: str, params: dict, quantities: list[str] | None = None, *, k: float = 2.0,
          rel_sigma_max: float = domain.DEFAULT_SIGMA_REL_MAX) -> dict:
    """Measured values at an exact library point, else per-quantity predictions with calibrated k-sigma bounds and domain verdicts."""
    ds = library.dataset(stratum)
    missing = [d for d in ds.dims if d not in params]
    if missing:
        raise ValueError(f"params need every dim of {stratum}: missing {missing}")
    coords = {d: float(params[d]) for d in ds.dims}
    wanted = quantities or ds.columns
    unknown = [q for q in wanted if q not in ds.columns]
    if unknown:
        raise ValueError(f"{stratum} has no quantities {unknown}; columns {ds.columns}")
    row = ds.find(coords)
    out: dict = {"stratum": stratum, "params": coords, "measured": None, "quantities": {}}
    if row is not None:
        out["measured"] = {"part": row.part, "obs_id": row.obs_id}
        for q in wanted:
            out["quantities"][q] = {"status": "measured", "value": row.values.get(q), "unit": unit(q)}
        return out
    x = np.array([[coords[d] for d in ds.dims]])
    for q in wanted:
        out["quantities"][q] = _predict(library, stratum, q, coords, x, k, rel_sigma_max)
    return out


def _predict(library: Library, stratum: str, q: str, coords: dict, x: np.ndarray, k: float, rel_sigma_max: float) -> dict:
    ds = library.dataset(stratum)
    if q.startswith("SRF"):
        near = _nearest_rows(library, stratum, coords, 5)
        above = [r for r in near if r.values.get(q) is None]
        if len(above) >= ABOVE_SWEEP_VOTES:
            return {"status": "above_sweep", "value": None, "lower_bound": min(r.stop_hz for r in above), "unit": "Hz",
                    "reason": f"{len(above)} of the 5 nearest measured rows have no resonance inside their sweep",
                    "nearest": [_evidence(r, q, ds.dims) for r in near[:3]]}
    m = library.model(stratum, q)
    scale = 1e9 if q.startswith("SRF") else 1.0
    try:
        verdict = m.guard.check(coords)
    except domain.OutOfDomainError as exc:
        return {"status": "out_of_domain", "criterion": exc.criterion, "reason": exc.reason, "unit": unit(q),
                "nearest": [_evidence(m.rows[i], q, ds.dims, dist) for i, dist in exc.nearest], "fill_points": exc.fill_points}
    if not m.gp.available(x)[0]:
        return {"status": "out_of_domain", "criterion": 2, "reason": "no model for this turns level (too few usable rows)", "unit": unit(q),
                "nearest": [_evidence(m.rows[i], q, ds.dims, dist) for i, dist in verdict.nearest]}
    mu, sigma = m.gp.predict(x)
    lo, hi = m.gp.predict_bounds(x, k)
    rel = float(sigma[0] / abs(mu[0])) if mu[0] else float("inf")
    return {"status": "predicted" if domain.sigma_ok(mu, sigma, rel_sigma_max)[0] else "uncertain",
            "value": float(mu[0]) * scale, "lo": float(lo[0]) * scale, "hi": float(hi[0]) * scale, "k": k,
            "k_scale": m.calibration["k_scale"], "rel_sigma": rel, "unit": unit(q),
            "nearest": [_evidence(m.rows[i], q, ds.dims, dist) for i, dist in verdict.nearest]}


def _nearest_rows(library: Library, stratum: str, coords: dict, n: int) -> list[dataset.Row]:
    ds = library.dataset(stratum)
    rng = library.ranges(stratum)
    lo = np.array([rng[d][0] for d in ds.dims])
    span = np.array([rng[d][1] - rng[d][0] for d in ds.dims])
    q = (np.array([coords[d] for d in ds.dims]) - lo) / span
    d = np.linalg.norm((ds.matrix() - lo) / span - q, axis=1)
    return [ds.rows[i] for i in np.argsort(d, kind="stable")[:n]]


def _evidence(row: dataset.Row, q: str, dims: list[str], distance: float | None = None) -> dict:
    out = {"part": row.part, "obs_id": row.obs_id, "params": {d: row.coords[d] for d in dims}, "value": row.values.get(q)}
    if distance is not None:
        out["scaled_distance"] = round(float(distance), 4)
    return out


def coverage(library: Library, stratum: str) -> dict:
    """What the stratum covers: rows per part and turns level, the achieved range of every dim, usable rows and value range per quantity."""
    ds = library.dataset(stratum)
    x = ds.matrix()
    out = {"stratum": stratum, "rows": len(ds.rows), "parts": {}, "generations": ds.generations, "excluded": ds.excluded,
           "dims": {}, "levels": {}, "quantities": {}}
    for r in ds.rows:
        out["parts"][r.part] = out["parts"].get(r.part, 0) + 1
    for i, d in enumerate(ds.dims):
        out["dims"][d] = {"min": float(x[:, i].min()), "max": float(x[:, i].max()), "distinct": len(set(x[:, i].tolist()))}
    if ds.nt_dim:
        levels = np.round(x[:, ds.dims.index(ds.nt_dim)]).astype(int)
        out["levels"] = {int(v): int((levels == v).sum()) for v in sorted(set(levels.tolist()))}
    for q in ds.columns:
        v = ds.values(q, ds.usable(q))
        out["quantities"][q] = {"rows": len(v), "min": float(v.min()) if len(v) else None, "max": float(v.max()) if len(v) else None,
                                "unit": unit(q)}
    return out


def load(library: Library, stratum: str | None = None) -> dict:
    """Dataset summaries (rows, cache state, integrity evidence) for one stratum or all of them."""
    names = [stratum] if stratum else library.strata()
    return {name: {"cache": library.dataset(name).cache, **dataset.check(library.dataset(name))} for name in names}
