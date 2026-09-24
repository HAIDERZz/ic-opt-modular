"""Answer questions about a library: what was measured, what the model predicts where nothing was, and how much is covered.

``Library`` is the runtime handle on a library root: it builds each stratum's dataset once, fits one
model per (stratum, quantity) on that quantity's usable rows (per_nt Matern 5/2, log target for every
positive quantity, the T13.0 choice) with a domain guard over the same rows, widens the model's 2-sigma
interval by the calibration factor derived from held-out residuals, and floors its sigma at the held-out
median relative error (both cached next to the dataset).

Fitted models are cached on disk too (``.cache/model-<stratum>-<quantity>-<key>.pkl``), keyed by the data,
the settings, the calibration, the gp code and the scikit-learn version, so a new process loads a model
instead of refitting it: the GP hyperparameter optimisation is sequential, takes ~2 min per quantity on a
1300-row stratum and does not get faster with more BLAS threads. For the same reason ``Library.models``
fits the quantities that are not cached yet in parallel processes.

``query`` answers a geometry: a measured row at exactly those coordinates is returned as measured;
otherwise each quantity is either predicted (mu with calibrated bounds, plus the three nearest measured
rows as evidence), out of domain (the guard's criterion, reason, nearest rows and the clamped point), or
flagged uncertain (criterion 4: sigma/mu above the ceiling -- reported with its numbers, never silently
used). SRF has one more answer: when most of the nearest measured rows had no resonance inside their
sweep, the point's resonance is reported as above the sweep instead of extrapolated.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import multiprocessing
import os
import pickle
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sklearn

from ic_opt.library import dataset, domain, gp, manifest

UNITS = {"L": "H", "Q": "1", "SRF": "Hz", "k": "1"}
ABOVE_SWEEP_VOTES = 3                                # of the 5 nearest measured rows
MODEL_CACHE_VERSION = 1                              # bump when a fit changes outside gp.py and the settings (e.g. how x, y are prepared)
FIT_WORKERS = max(1, (os.cpu_count() or 2) // 2)    # by default every uncached model is fitted at once: a fit keeps ~1.5 cores and
                                                     # a few hundred MB busy whatever BLAS gets, so half the cores is the only cap (2026-09-24)


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
            ds, rows, x, y, settings = self._fit_inputs(stratum, quantity)
            calibration = self._calibration(ds, quantity, x, y, settings)
            path = self._model_path(ds, quantity, settings, calibration)
            model = _load_model(path, ds.dims)
            if model is None:                            # not cached yet, or the file is unusable: fit and (over)write it
                model = gp.StratumGP(**settings, k_scale=calibration["k_scale"], sigma_floor_rel=calibration.get("median_rel", 0.0)).fit(x, y)
                _save_model(path, model)
            guard = domain.DomainGuard(x, ds.dims, settings["ranges"], nt_dim=ds.nt_dim, ids=list(range(len(rows))))
            self._models[key] = Model(stratum, quantity, rows, model, guard, calibration)
        return self._models[key]

    def models(self, stratum: str, quantities: list[str], *, workers: int | None = None, threads: int | None = None) -> dict[str, Model]:
        """``model`` for several quantities, in the order asked. The ones without a cache file are fitted first in up to
        ``workers`` forked processes (default one per missing quantity, at most FIT_WORKERS = half the cores): each fit is a sequential
        optimisation that no amount of BLAS threads speeds up, so processes are what runs several at once. Each worker caps
        BLAS at ``threads // workers`` (``threads`` defaults to $OMP_NUM_THREADS or 8, and to at least two per worker), writes
        the calibration and model caches and returns only the quantity's name; every model is then loaded here from its
        cache file, so no fitted model crosses a pipe."""
        missing = [q for q in dict.fromkeys(quantities) if (stratum, q) not in self._models and self._model_file(stratum, q) is None]
        workers = min(FIT_WORKERS if workers is None else workers, len(missing))
        threads = max(int(os.environ.get("OMP_NUM_THREADS", "8")), 2 * workers) if threads is None else threads
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("fork")) as pool:
                jobs = [pool.submit(_fit_in_worker, self.root, self.calibrate, stratum, q, max(1, threads // workers)) for q in missing]
                for job in jobs:
                    job.result()                         # a failed fit raises here, with the worker's exception
        return {q: self.model(stratum, q) for q in quantities}

    def _fit_inputs(self, stratum: str, quantity: str) -> tuple[dataset.Dataset, list[dataset.Row], np.ndarray, np.ndarray, dict]:
        """What a fit of ``quantity`` needs: the dataset, the usable rows, x, y and the StratumGP settings."""
        ds = self.dataset(stratum)
        if quantity not in ds.columns:
            raise ValueError(f"{stratum} has no quantity {quantity!r}; columns {ds.columns}")
        rows = ds.usable(quantity)
        x, y = ds.matrix(rows), ds.values(quantity, rows)
        if quantity.startswith("SRF"):
            y = y / 1e9                                  # GHz keeps the log-GP numerically tame; mapped back on output
        feature_map = self.manifest.strata[stratum].quantities[quantity.split("@")[0]].feature_map
        settings = {"dims": ds.dims, "ranges": self.ranges(stratum), "log_target": bool((y > 0).all()),
                    "nt_mode": "per_nt" if ds.nt_dim and not feature_map else "joint", "kernel": "matern52", "nt_dim": ds.nt_dim,
                    "feature_map": feature_map}
        return ds, rows, x, y, settings

    def _model_path(self, ds: dataset.Dataset, quantity: str, settings: dict, calibration: dict) -> Path:
        """The cache file of a fitted model: keyed by the data, the settings, the calibration (k_scale and the sigma floor are
        part of the model), the gp code and the scikit-learn version the pickle belongs to."""
        h = hashlib.sha256()
        h.update(f"v{MODEL_CACHE_VERSION}".encode())
        h.update(ds.key.encode())
        h.update(json.dumps(settings, sort_keys=True).encode())
        h.update(json.dumps(calibration, sort_keys=True).encode())
        h.update(hashlib.sha256(inspect.getsource(gp).encode()).digest())
        h.update(sklearn.__version__.encode())
        return self.root / ".cache" / f"model-{ds.stratum}-{quantity.replace('@', '_at_')}-{h.hexdigest()[:20]}.pkl"

    def _model_file(self, stratum: str, quantity: str) -> Path | None:
        """The model's cache file if it exists. Never while the calibration is not cached: the key needs it, and computing it
        takes the five hold-out fits that are the work ``models`` hands to its workers."""
        ds, _rows, x, y, settings = self._fit_inputs(stratum, quantity)
        calibration = self._calibration(ds, quantity, x, y, settings, compute=False)
        if calibration is None:
            return None
        path = self._model_path(ds, quantity, settings, calibration)
        return path if path.is_file() else None

    def _calibration(self, ds: dataset.Dataset, quantity: str, x, y, settings: dict, *, compute: bool = True) -> dict | None:
        if not self.calibrate:
            return {"k_scale": 1.0, "source": "off"}
        path = self.root / ".cache" / f"calibration-{ds.stratum}-{quantity.replace('@', '_at_')}-{ds.key}.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        if not compute:
            return None
        report = gp.holdout(x, y, **settings)
        out = {"k_scale": gp.calibration_scale(report), "median_rel": report["median_rel"],
               "coverage_2sigma_before": report["coverage_2sigma"], "n_scored": report["n_scored"], "source": "holdout 5x20%",
               "sigma_floor": "median held-out relative error"}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out), encoding="utf-8")
        return out


def _load_model(path: Path, dims: list[str]) -> gp.StratumGP | None:
    """The cached model, or None: a missing file, one that does not unpickle, or anything but a StratumGP over these dims
    means refit and overwrite -- a bad cache file costs a fit, never an error."""
    try:
        with path.open("rb") as f:
            model = pickle.load(f)
    except Exception:  # noqa: BLE001 -- missing, truncated, garbage, pickled by other code: unpickling can raise almost anything
        return None
    return model if isinstance(model, gp.StratumGP) and model.dims == list(dims) else None


def _save_model(path: Path, model: gp.StratumGP) -> None:
    """Pickle to a sibling temporary file, then rename it over ``path``: a reader sees the old file or the whole new one, and
    writers of the same model at once (the engine's threads, other processes) each have their own temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp", delete=False) as f:
        pickle.dump(model, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(f.name, path)


def _fit_in_worker(root: Path, calibrate: bool, stratum: str, quantity: str, threads: int) -> str:
    """One ``Library.models`` worker process: fit a quantity with BLAS capped at ``threads``; the fit writes the calibration
    and model caches, and only the name goes back (the parent loads the model from its cache file)."""
    from threadpoolctl import threadpool_limits

    with threadpool_limits(limits=threads):
        Library(root, calibrate=calibrate).model(stratum, quantity)
    return quantity


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
