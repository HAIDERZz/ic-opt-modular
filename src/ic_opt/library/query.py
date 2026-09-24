"""Answer questions about a library: what was measured, what the model predicts where nothing was, and how much is covered.

``Library`` is the runtime handle on a library root: it builds each stratum's dataset once, fits one
model per (stratum, quantity) on that quantity's usable rows (per_nt Matern 5/2, log target for every
positive quantity, the T13.0 choice) with a domain guard over the same rows, widens the model's 2-sigma
interval by the calibration factor derived from held-out residuals, and floors its sigma at the held-out
median relative error (both cached next to the dataset).

Fitted models are cached on disk too (``model-<stratum>-<quantity>-<key>.pkl``), keyed by the data,
the settings, the calibration, the gp code and the scikit-learn version, so a new process loads a model
instead of refitting it: the GP hyperparameter optimisation is sequential, takes ~2 min per quantity on a
1300-row stratum on the reference host and does not get faster with more BLAS threads. For the same reason
``Library.models`` fits the quantities that are not cached yet in parallel processes.

Every cache file -- dataset, calibration, model -- goes to one directory, ``Library.cache`` (``cache.locate``): the
library's own ``.cache``, the ``cache_dir`` it was opened with, or, when its own cannot be written, a directory under
``~/.cache/ic-opt/``; the files in its own ``.cache`` are read in every case. ``Library.notes`` says where the files go
when that is not the library's own ``.cache``, and every answer below carries it in its ``notes``. One process at a
time calibrates and fits a model into a cache directory: the fit holds a lock file next to the calibration file, and
another process that needs the same model waits for it, then loads what it wrote (``Library._fit``).

A curve whose manifest entry says ``model: ratio`` or ``model: resonance`` is composed from the stratum's own
low-frequency (and SRF) models (``ic_opt.library.composed``): ``models`` loads or fits those first, each once
however many curves are built on it, then the composed curves -- their calibration folds as jobs of their own,
since every fold refits every part. A composed model's cache files live in the same cache and hold the same lock
discipline: its calibration file is keyed by the option and the parts' settings, its model file by the parts' model
keys as well; the file holds only its own part, and the shared models are attached when it loads. ``query`` adds to
a composed curve's answer where the value came from.

SRF is fitted in GHz (the log-GP is tamer there) and answered in Hz: ``fit_unit`` is the one place that says so.

The library computes on the machine running ic-opt, within that machine's limits and nothing else: the
``limits`` a library is given, else site.yaml's ``hosts.local``, read the first time a model has to be fitted
or a batch predicted (``Library.limits``; a missing file or entry raises ``SiteError``). Reading needs no
limits: datasets, coverage, measured rows and models loaded from their cache files. ``fit_plan`` turns the
limits into worker processes and BLAS threads per worker, ``blas_threads`` into the BLAS threads of work in
this process; an explicit OMP_NUM_THREADS, OPENBLAS_NUM_THREADS or MKL_NUM_THREADS (``omp_cap``: the smallest set)
only ever lowers those threads.

``query`` answers a geometry: a measured row at exactly those coordinates is returned as measured;
otherwise each quantity is either predicted (mu with calibrated bounds, plus the three nearest measured
rows as evidence), out of domain (the guard's criterion, reason, nearest rows and the clamped point), or
flagged uncertain (criterion 4: sigma/mu above the ceiling -- reported with its numbers, never silently
used). SRF has one more answer: when most of the nearest measured rows had no resonance inside their
sweep, the point's resonance is reported as above the sweep instead of extrapolated.

The ceiling is per column: a call's explicit ``rel_sigma_max``, else the quantity's ``rel_sigma_max`` in
library.yaml, else ``domain.DEFAULT_SIGMA_REL_MAX`` (``Library.rel_sigma_max``). Each ``Model`` carries its own,
so ``suggest.predict_all`` -- the gate of lib.suggest, lib.region and lib.densify -- applies the same one.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import multiprocessing
import os
import pickle
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sklearn
from threadpoolctl import threadpool_limits

from ic_opt import _lock, site
from ic_opt.library import cache, composed, dataset, domain, gp, manifest

UNITS = {"L": "H", "Q": "1", "SRF": "Hz", "k": "1"}
ABOVE_SWEEP_VOTES = 3                                # of the 5 nearest measured rows
MODEL_CACHE_VERSION = 1                              # bump when a fit changes outside gp.py and the settings (e.g. how x, y are prepared)
THREADS_PER_FIT = 2                                  # a fit keeps ~1.5 cores busy whatever BLAS gets (2026-09-24): a worker counts as two threads
FIT_PEAK_COPIES = 3                                  # a fit's peak memory in kernel-gradient arrays (fit_memory_gb)
BYTES_PER_GB = 1024**3                               # GB as site.yaml means it (env.doctor reads MemTotal in these units)
WINDOWS_MAX_WORKERS = 61                             # ProcessPoolExecutor refuses more worker processes on Windows
THREAD_CAP_VARIABLES = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")   # explicit caps: omp_cap


def unit(quantity: str) -> str:
    base = quantity.split("@")[0]
    return UNITS["SRF"] if base.startswith("SRF") else UNITS[base[0]] if base[0] in "LQ" else UNITS["k"]


def fit_unit(quantity: str) -> float:
    """How many of the column's units one unit of its model is: an SRF column is fitted in GHz (1e9 Hz), everything else in
    the column's own unit. Every conversion between a model and its column goes through here."""
    return 1e9 if quantity.startswith("SRF") else 1.0


@dataclass
class Model:
    stratum: str
    quantity: str
    rows: list[dataset.Row]
    gp: gp.StratumGP | composed.ComposedGP
    guard: domain.DomainGuard
    calibration: dict                                # k_scale, held-out median_rel and coverage behind it
    rel_sigma_max: float = domain.DEFAULT_SIGMA_REL_MAX   # its confidence ceiling when a call gives none (Library.rel_sigma_max)


def fit_rows(model: Model) -> int:
    """The most rows any GP behind the model was fitted on (a prediction call's memory grows with them): the quantity's usable
    rows, or for a composed curve the largest of its parts."""
    return max(len(model.rows), getattr(model.gp, "n_train", 0))


class Library:
    def __init__(self, root: str | Path, *, calibrate: bool = True, limits: site.HostLimits | None = None,
                 cache_dir: str | Path | None = None):
        self.root = Path(root)
        self.manifest = manifest.load(self.root)
        self.calibrate = calibrate
        self.cache = cache.locate(self.root, cache_dir)  # where datasets, calibrations and models are cached
        self._limits = limits
        self._datasets: dict[str, dataset.Dataset] = {}
        self._models: dict[tuple[str, str], Model] = {}

    @property
    def notes(self) -> list[str]:
        """What every answer from this library says in its ``notes``: where the cache files go when the library's own
        ``.cache`` cannot be written (``cache.locate``'s fallback); nothing otherwise."""
        return [self.cache.note] if self.cache.note else []

    @property
    def limits(self) -> site.HostLimits:
        """What the library may use of the machine running ic-opt: the ``limits`` it was given, else site.yaml's
        ``hosts.local``, read here on first use -- when a model has to be fitted or a batch predicted, never to read.
        A missing site.yaml or ``local`` entry raises ``SiteError`` with the entry to add."""
        if self._limits is None:
            self._limits = site.load().host("local")
        return self._limits

    def strata(self) -> list[str]:
        return sorted(self.manifest.strata)

    def rel_sigma_max(self, stratum: str, column: str, given: float | None = None) -> float:
        """The confidence ceiling on sigma / mu for one column: ``given`` (a call's explicit value), else the manifest's
        ``rel_sigma_max`` for the column's quantity (a curve's for every anchor), else domain.DEFAULT_SIGMA_REL_MAX."""
        if given is not None:
            return float(given)
        rule = self.manifest.strata[stratum].quantities.get(column.split("@")[0])
        return domain.DEFAULT_SIGMA_REL_MAX if rule is None or rule.rel_sigma_max is None else rule.rel_sigma_max

    def dataset(self, stratum: str) -> dataset.Dataset:
        if stratum not in self._datasets:
            self._datasets[stratum] = dataset.build(self.root, stratum, library=self.manifest, cache_dir=self.cache)
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
        """One quantity's model: in memory, else loaded from its cache files, else fitted within ``limits`` (``models``)."""
        key = (stratum, quantity)
        if key not in self._models:
            self.models(stratum, [quantity])
        return self._models[key]

    def models(self, stratum: str, quantities: list[str], *, workers: int | None = None, threads: int | None = None) -> dict[str, Model]:
        """``model`` for several quantities, in the order asked. The ones whose cache files do not load are fitted first, sized
        by ``fit_plan`` from ``limits``: worker processes and BLAS threads per worker. ``workers`` and ``threads`` are explicit
        caps within those limits; a value above them raises ValueError naming the limit. Each fit is a sequential
        optimisation that no amount of BLAS threads speeds up (~2 min per quantity of a 1300-row stratum on the reference
        host), so processes are what runs several at once: with more than one worker, each fits a quantity in its own
        process with BLAS capped, writes the calibration and model caches and returns only the quantity's name, and every
        model is then loaded here from its cache file, so no fitted model crosses a pipe. With one worker the fits run
        here, one after another, capped the same way.

        The workers are spawned on every platform: spawn is the only start method on Windows and the default on macOS, and
        Linux uses it too so that all three behave alike. A spawned worker is a fresh interpreter that knows nothing of this
        process but ``_fit_in_worker``'s arguments; starting one re-imports ic_opt, numpy, scipy and scikit-learn, about
        0.5 s on the reference host (Linux, Python 3.11: 0.43 s for one worker, 0.46 s for four started together), against
        minutes per fit. Every worker also imports the main module of the program, so a script that gets here (directly or
        through ``lib.region`` / ``lib_signoff``) keeps its top-level work under ``if __name__ == "__main__":``.

        A composed curve (``model: ratio`` / ``resonance``) needs its parts' models: they are loaded or fitted first, with the
        other direct quantities, each once however many curves share it. Then the composed curves: with several workers, the
        five folds of each uncached calibration are jobs of their own (``_calibrate_fold_in_worker``: every fold refits every
        part) while this process holds the curve's lock, and once a curve is calibrated its own part is fitted on the shared
        models (``_fit_in_worker``)."""
        order = self._with_parts(stratum, list(dict.fromkeys(quantities)))
        missing = [q for q in order if not self._load(stratum, q)]
        direct = [q for q in missing if self._plan(stratum, q) is None]
        if direct:
            self._fit_direct(stratum, direct, workers, threads)
        built = [q for q in missing if q not in direct]
        if built:
            self._fit_composed(stratum, built, workers, threads)
        return {q: self._models[(stratum, q)] for q in quantities}

    def _fit_direct(self, stratum: str, names: list[str], workers: int | None, threads: int | None) -> None:
        """Fit the direct models ``names`` (not cached): in spawned workers, or here with one worker."""
        ds = self.dataset(stratum)
        n, per_worker = fit_plan(self.limits, len(names), max(len(ds.usable(q)) for q in names), len(ds.dims), workers=workers, threads=threads)
        if n > 1:
            with ProcessPoolExecutor(max_workers=n, mp_context=multiprocessing.get_context("spawn")) as pool:
                jobs = [pool.submit(_fit_in_worker, self.root, self.calibrate, stratum, q, per_worker, self.cache.directory) for q in names]
                for job in jobs:
                    job.result()                         # a failed fit raises here, with the worker's exception
            self._reload(stratum, names)
        else:
            with threadpool_limits(limits=per_worker):
                for q in names:
                    self._fit(stratum, q)

    def _fit_composed(self, stratum: str, names: list[str], workers: int | None, threads: int | None) -> None:
        """Fit the composed curves ``names`` (not cached; their parts are loaded). With several workers: the folds of every
        uncached calibration, one job each, while this process holds each such curve's lock -- taken in the order of the
        lock files' names, so two processes never wait on each other -- then every curve's own part; with one worker, here,
        one after another (``_fit``)."""
        ds = self.dataset(stratum)
        uncalibrated = [q for q in names if self._composed_calibration(stratum, q, compute=False) is None]
        largest = max(len(ds.usable(c)) for q in names for c in self._parts(stratum, q) + [q])    # a fold refits every part
        n, per_worker = fit_plan(self.limits, max(len(uncalibrated) * len(gp.SEEDS), len(names)), largest, len(ds.dims),
                                 workers=workers, threads=threads)
        if n <= 1:
            with threadpool_limits(limits=per_worker):
                for q in names:
                    self._fit(stratum, q)
            return
        with ProcessPoolExecutor(max_workers=n, mp_context=multiprocessing.get_context("spawn")) as pool:
            with ExitStack() as held:
                for name in sorted(self._composed_calibration_name(stratum, q) for q in uncalibrated):
                    self._hold(held, name)
                uncalibrated = [q for q in uncalibrated if self._composed_calibration(stratum, q, compute=False) is None]  # or written meanwhile
                jobs = {(q, seed): pool.submit(_calibrate_fold_in_worker, self.root, self.calibrate, stratum, q, seed, per_worker,
                                               self.cache.directory) for q in uncalibrated for seed in gp.SEEDS}
                done = {job: future.result() for job, future in jobs.items()}
                for q in uncalibrated:                   # written here, from the folds in seed order
                    self._composed_calibration(stratum, q, folds=[done[(q, seed)] for seed in gp.SEEDS])
            finals = [pool.submit(_fit_in_worker, self.root, self.calibrate, stratum, q, per_worker, self.cache.directory) for q in names]
            for job in finals:
                job.result()
        self._reload(stratum, names)

    def _reload(self, stratum: str, names: list[str]) -> None:
        unread = [q for q in names if not self._load(stratum, q)]
        if unread:
            raise RuntimeError(f"{stratum}: the workers fitted {unread} but their cache files under {self.cache.directory} do not load")

    def _hold(self, held: ExitStack, name: str) -> None:
        """Wait for the lock file ``<name>.lock`` in the cache directory, then hold it until ``held`` closes; unguarded where
        the file system takes no locks (or no lock file can be made)."""
        try:
            held.enter_context(_lock.waiting_lock(self.cache.target(f"{name}.lock")))
        except OSError:                                  # no locks here (or no lock file): unguarded
            pass

    def _load(self, stratum: str, quantity: str) -> bool:
        """Whether the quantity's model is in memory, after putting it there from its cache files when they hold a usable
        one (no calibration file, no model file, or one that does not unpickle: False, fit it). A composed curve loads only
        once its parts do."""
        if (stratum, quantity) in self._models:
            return True
        if self._plan(stratum, quantity) is not None:
            return self._load_composed(stratum, quantity)
        ds, rows, x, y, settings = self._fit_inputs(stratum, quantity)
        calibration = self._calibration(ds, quantity, x, y, settings, compute=False)
        path = None if calibration is None else self.cache.find(self._model_name(ds, quantity, settings, calibration))
        model = None if path is None else _load_model(path, ds.dims)
        if model is None:
            return False
        self._keep(stratum, quantity, ds, rows, x, settings, model, calibration)
        return True

    def _fit(self, stratum: str, quantity: str) -> None:
        """Calibrate (the five hold-out fits, cached), fit, (over)write the model's cache file and keep the model -- holding
        the lock file next to the calibration file (``<calibration file>.lock`` in the cache directory) meanwhile. Another
        process that needs the same model, on this machine or on another sharing the cache directory, waits for the lock
        and then loads what this one wrote instead of computing it again; on a file system that takes no locks both
        compute, as they did without it, and the last write wins. BLAS is capped by the caller: ``models`` in this
        process, ``_fit_in_worker`` in a worker's. A composed curve: ``_fit_composed_here``, under the same discipline."""
        if self._plan(stratum, quantity) is not None:
            self._fit_composed_here(stratum, quantity)
            return
        ds, rows, x, y, settings = self._fit_inputs(stratum, quantity)
        with ExitStack() as held:
            self._hold(held, _calibration_name(ds, quantity))
            if self._load(stratum, quantity):            # fitted by another process while this one waited
                return
            calibration = self._calibration(ds, quantity, x, y, settings)
            model = gp.StratumGP(**settings, k_scale=calibration["k_scale"], sigma_floor_rel=calibration.get("median_rel", 0.0)).fit(x, y)
            _save_model(self.cache.target(self._model_name(ds, quantity, settings, calibration)), model)
            self._keep(stratum, quantity, ds, rows, x, settings, model, calibration)

    def _keep(self, stratum: str, quantity: str, ds: dataset.Dataset, rows: list[dataset.Row], x: np.ndarray, settings: dict,
              model: gp.StratumGP | composed.ComposedGP, calibration: dict) -> None:
        guard = domain.DomainGuard(x, ds.dims, settings["ranges"], nt_dim=ds.nt_dim, ids=list(range(len(rows))))
        self._models[(stratum, quantity)] = Model(stratum, quantity, rows, model, guard, calibration, self.rel_sigma_max(stratum, quantity))

    # -- composed curves (ic_opt.library.composed) ---------------------------------------------------------------------

    def _plan(self, stratum: str, quantity: str) -> dict | None:
        """How a composed curve column is built -- ``{"model", "f0" (in the SRF model's unit), "lf", "srf"}`` -- or None for a
        column modelled directly."""
        base, at, f = quantity.partition("@")
        rule = self.manifest.strata[stratum].quantities.get(base)
        if not at or rule is None or rule.model == "direct":
            return None
        return {"model": rule.model, "f0": float(f) * 1e9 / fit_unit("SRF"), "lf": manifest.LOW_FREQUENCY[base],
                "srf": "SRF" if rule.model == "resonance" else None}

    def _parts(self, stratum: str, quantity: str) -> list[str]:
        """The columns a composed curve is built on (none for a direct one)."""
        plan = self._plan(stratum, quantity)
        return [] if plan is None else [plan["lf"]] + ([plan["srf"]] if plan["srf"] else [])

    def _with_parts(self, stratum: str, quantities: list[str]) -> list[str]:
        """``quantities`` with every composed curve's parts before it, each column once."""
        out: dict[str, None] = {}
        for q in quantities:
            out.update(dict.fromkeys(self._parts(stratum, q)))
            out[q] = None
        return list(out)

    def _composed_settings(self, stratum: str, quantity: str) -> dict:
        """What ``composed.holdout_fold`` and ``ComposedGP`` take besides the data: the plan and the three models' settings."""
        plan = self._plan(stratum, quantity)
        ds = self.dataset(stratum)
        part = {**self._fit_inputs(stratum, quantity)[4], "log_target": True}          # the curve's own settings
        return {"kind": plan["model"], "f0": plan["f0"], "dims": list(ds.dims), "ranges": part["ranges"], "nt_dim": ds.nt_dim,
                "part": part, "lf": self._fit_inputs(stratum, plan["lf"])[4],
                "srf": self._fit_inputs(stratum, plan["srf"])[4] if plan["srf"] else None,
                "names": {"lf": plan["lf"], "srf": plan["srf"]}}

    def _composed_data(self, stratum: str, quantity: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray | None]:
        """Every row of the stratum and, per row, the curve, its low-frequency scalar and the SRF (in the SRF model's unit):
        NaN where a row has no usable value."""
        plan = self._plan(stratum, quantity)
        ds = self.dataset(stratum)
        srf = ds.values(plan["srf"]) / fit_unit(plan["srf"]) if plan["srf"] else None
        return ds.matrix(), ds.values(quantity), ds.values(plan["lf"]), srf

    def _composed_fold(self, stratum: str, quantity: str, seed: int) -> dict:
        """One calibration fold of a composed curve (``composed.holdout_fold``)."""
        return composed.holdout_fold(*self._composed_data(stratum, quantity), seed, **self._composed_settings(stratum, quantity))

    def _composed_calibration_name(self, stratum: str, quantity: str) -> str:
        """The cache file name of a composed curve's calibration: keyed by the data, the option and the three models' settings
        (its lock file adds ``.lock``)."""
        settings = self._composed_settings(stratum, quantity)
        h = hashlib.sha256(b"composed")
        h.update(self.dataset(stratum).key.encode())
        h.update(json.dumps(settings, sort_keys=True).encode())
        return f"calibration-{stratum}-{_file_part(quantity)}-{settings['kind']}-{h.hexdigest()[:20]}.json"

    def _composed_calibration(self, stratum: str, quantity: str, *, compute: bool = True, folds: list[dict] | None = None) -> dict | None:
        """The composed curve's calibration: cached, else from ``folds`` (the parallel path), else computed here (all five
        folds) unless ``compute`` is False."""
        if not self.calibrate:
            return {"k_scale": 1.0, "source": "off"}
        name = self._composed_calibration_name(stratum, quantity)
        found = self.cache.find(name)
        if found is not None:
            return json.loads(found.read_text(encoding="utf-8"))
        if folds is None and not compute:
            return None
        settings = self._composed_settings(stratum, quantity)
        report = composed.merge(folds) if folds is not None else composed.holdout(*self._composed_data(stratum, quantity), **settings)
        out = {"k_scale": gp.calibration_scale(report), "median_rel": report["median_rel"],
               "coverage_2sigma_before": report["coverage_2sigma"], "n_scored": report["n_scored"],
               "source": "holdout 5x20%, every part refitted on each fold", "sigma_floor": "median held-out relative error",
               "model": settings["kind"]}
        _write_json(self.cache.target(name), out)
        return out

    def _part_keys(self, stratum: str, quantity: str) -> list[str] | None:
        """The cache-file names of a composed curve's parts' models (None while a part is not calibrated: its name needs it)."""
        ds = self.dataset(stratum)
        keys = []
        for part in self._parts(stratum, quantity):
            _ds, _rows, x, y, settings = self._fit_inputs(stratum, part)
            calibration = self._calibration(ds, part, x, y, settings, compute=False)
            if calibration is None:
                return None
            keys.append(self._model_name(ds, part, settings, calibration))
        return keys

    def _composed_model_name(self, stratum: str, quantity: str, calibration: dict) -> str | None:
        """The cache file name of a composed curve's model: keyed like a direct one, and by the option, the three settings
        and the parts' own model keys (a refitted part makes it a new file). None while a part is not calibrated."""
        keys = self._part_keys(stratum, quantity)
        if keys is None:
            return None
        h = hashlib.sha256()
        h.update(f"v{MODEL_CACHE_VERSION} composed".encode())
        h.update(self.dataset(stratum).key.encode())
        h.update(json.dumps(self._composed_settings(stratum, quantity), sort_keys=True).encode())
        h.update(json.dumps(calibration, sort_keys=True).encode())
        h.update(json.dumps(keys).encode())
        h.update(hashlib.sha256(inspect.getsource(gp).encode()).digest())
        h.update(hashlib.sha256(inspect.getsource(composed).encode()).digest())
        h.update(sklearn.__version__.encode())
        return f"model-{stratum}-{_file_part(quantity)}-{h.hexdigest()[:20]}.pkl"

    def _load_composed(self, stratum: str, quantity: str) -> bool:
        if not all(self._load(stratum, part) for part in self._parts(stratum, quantity)):
            return False
        calibration = self._composed_calibration(stratum, quantity, compute=False)
        name = None if calibration is None else self._composed_model_name(stratum, quantity, calibration)
        path = None if name is None else self.cache.find(name)
        model = None if path is None else _load_composed_model(path, self.dataset(stratum).dims)
        if model is None:
            return False
        self._keep_composed(stratum, quantity, model, calibration)
        return True

    def _fit_composed_here(self, stratum: str, quantity: str) -> None:
        """The parts (loaded, else fitted here, each under its own lock), then -- holding the lock next to the curve's
        calibration file, as a direct fit does -- the calibration (cached, else its five folds here) and the curve's own part
        on the shared models; the model file is written and the model kept. A process that waited for the lock loads what
        the holder wrote."""
        for part in self._parts(stratum, quantity):
            if not self._load(stratum, part):
                self._fit(stratum, part)
        with ExitStack() as held:
            self._hold(held, self._composed_calibration_name(stratum, quantity))
            if self._load(stratum, quantity):            # fitted by another process while this one waited
                return
            calibration = self._composed_calibration(stratum, quantity)
            ds, rows, x, y, _settings = self._fit_inputs(stratum, quantity)
            s = self._composed_settings(stratum, quantity)
            model = composed.ComposedGP(kind=s["kind"], f0=s["f0"], dims=s["dims"], ranges=s["ranges"], nt_dim=s["nt_dim"], part=s["part"],
                                        names=s["names"], k_scale=calibration["k_scale"], sigma_floor_rel=calibration.get("median_rel", 0.0))
            self._attach(stratum, quantity, model)
            model.fit(x, y, lf_y=ds.values(s["names"]["lf"], rows))
            _save_model(self.cache.target(self._composed_model_name(stratum, quantity, calibration)), model)
            self._keep_composed(stratum, quantity, model, calibration)

    def _attach(self, stratum: str, quantity: str, model: composed.ComposedGP) -> None:
        parts = [self._models[(stratum, p)] for p in self._parts(stratum, quantity)]
        model.attach(parts[0].gp, parts[1].gp if len(parts) > 1 else None, n_train=max(len(p.rows) for p in parts))

    def _keep_composed(self, stratum: str, quantity: str, model: composed.ComposedGP, calibration: dict) -> None:
        self._attach(stratum, quantity, model)
        ds, rows, x, _y, settings = self._fit_inputs(stratum, quantity)
        self._keep(stratum, quantity, ds, rows, x, settings, model, calibration)

    # -- direct models --------------------------------------------------------------------------------------------------

    def _fit_inputs(self, stratum: str, quantity: str) -> tuple[dataset.Dataset, list[dataset.Row], np.ndarray, np.ndarray, dict]:
        """What a fit of ``quantity`` needs: the dataset, the usable rows, x, y (in the model's unit: ``fit_unit``) and the
        StratumGP settings."""
        ds = self.dataset(stratum)
        if quantity not in ds.columns:
            raise ValueError(f"{stratum} has no quantity {quantity!r}; columns {ds.columns}")
        rows = ds.usable(quantity)
        x, y = ds.matrix(rows), ds.values(quantity, rows)
        if fit_unit(quantity) != 1.0:
            y = y / fit_unit(quantity)                   # SRF in GHz keeps the log-GP numerically tame; mapped back on output
        feature_map = self.manifest.strata[stratum].quantities[quantity.split("@")[0]].feature_map
        settings = {"dims": ds.dims, "ranges": self.ranges(stratum), "log_target": bool((y > 0).all()),
                    "nt_mode": "per_nt" if ds.nt_dim and not feature_map else "joint", "kernel": "matern52", "nt_dim": ds.nt_dim,
                    "feature_map": feature_map}
        return ds, rows, x, y, settings

    def _model_name(self, ds: dataset.Dataset, quantity: str, settings: dict, calibration: dict) -> str:
        """The cache file name of a fitted model: keyed by the data, the settings, the calibration (k_scale and the sigma floor
        are part of the model), the gp code and the scikit-learn version the pickle belongs to."""
        h = hashlib.sha256()
        h.update(f"v{MODEL_CACHE_VERSION}".encode())
        h.update(ds.key.encode())
        h.update(json.dumps(settings, sort_keys=True).encode())
        h.update(json.dumps(calibration, sort_keys=True).encode())
        h.update(hashlib.sha256(inspect.getsource(gp).encode()).digest())
        h.update(sklearn.__version__.encode())
        return f"model-{ds.stratum}-{_file_part(quantity)}-{h.hexdigest()[:20]}.pkl"

    def _model_file(self, stratum: str, quantity: str) -> Path | None:
        """The model's cache file if it exists (``Cache.find``: the cache directory, then the library's own ``.cache``). Never
        while the calibration is not cached: the key needs it, and computing it takes the five hold-out fits that are the
        work ``models`` hands to its workers (a composed curve: nor while one of its parts is not)."""
        if self._plan(stratum, quantity) is not None:
            calibration = self._composed_calibration(stratum, quantity, compute=False)
            name = None if calibration is None else self._composed_model_name(stratum, quantity, calibration)
            return None if name is None else self.cache.find(name)
        ds, _rows, x, y, settings = self._fit_inputs(stratum, quantity)
        calibration = self._calibration(ds, quantity, x, y, settings, compute=False)
        if calibration is None:
            return None
        return self.cache.find(self._model_name(ds, quantity, settings, calibration))

    def _calibration(self, ds: dataset.Dataset, quantity: str, x, y, settings: dict, *, compute: bool = True) -> dict | None:
        if not self.calibrate:
            return {"k_scale": 1.0, "source": "off"}
        name = _calibration_name(ds, quantity)
        found = self.cache.find(name)
        if found is not None:
            return json.loads(found.read_text(encoding="utf-8"))
        if not compute:
            return None
        report = gp.holdout(x, y, **settings)
        out = {"k_scale": gp.calibration_scale(report), "median_rel": report["median_rel"],
               "coverage_2sigma_before": report["coverage_2sigma"], "n_scored": report["n_scored"], "source": "holdout 5x20%",
               "sigma_floor": "median held-out relative error"}
        path = self.cache.target(name)
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp", delete=False, encoding="utf-8") as f:
            f.write(json.dumps(out))
        os.replace(f.name, path)                         # a reader sees the old file or the whole new one
        return out


def _file_part(quantity: str) -> str:
    """A quantity as it appears in cache file names: ``Lp@28`` -> ``Lp_at_28``."""
    return quantity.replace("@", "_at_")


def _calibration_name(ds: dataset.Dataset, quantity: str) -> str:
    """The cache file name of a quantity's calibration, keyed by the dataset (its lock file adds ``.lock``)."""
    return f"calibration-{ds.stratum}-{_file_part(quantity)}-{ds.key}.json"


def _write_json(path: Path, data: dict) -> None:
    """Write to a sibling temporary file, then rename it over ``path``: a reader sees the old file or the whole new one."""
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp", delete=False, encoding="utf-8") as f:
        f.write(json.dumps(data))
    os.replace(f.name, path)


def _unpickle(path: Path):
    try:
        with path.open("rb") as f:
            return pickle.load(f)
    except Exception:  # noqa: BLE001 -- missing, truncated, garbage, pickled by other code: unpickling can raise almost anything
        return None


def _load_model(path: Path, dims: list[str]) -> gp.StratumGP | None:
    """The cached model, or None: a missing file, one that does not unpickle, or anything but a StratumGP over these dims
    means refit and overwrite -- a bad cache file costs a fit, never an error."""
    model = _unpickle(path)
    return model if isinstance(model, gp.StratumGP) and model.dims == list(dims) else None


def _load_composed_model(path: Path, dims: list[str]) -> composed.ComposedGP | None:
    """A cached composed model (its own part only; the library attaches the shared ones), or None as ``_load_model``."""
    model = _unpickle(path)
    return model if isinstance(model, composed.ComposedGP) and model.dims == list(dims) and model.part is not None else None


def _save_model(path: Path, model: gp.StratumGP | composed.ComposedGP) -> None:
    """Pickle to a sibling temporary file, then rename it over ``path``: a reader sees the old file or the whole new one, and
    writers of the same model at once (the engine's threads, other processes) each have their own temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp", delete=False) as f:
        pickle.dump(model, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(f.name, path)


def _fit_in_worker(root: Path, calibrate: bool, stratum: str, quantity: str, threads: int, cache_dir: Path | None = None) -> str:
    """One ``Library.models`` worker process: fit a quantity with BLAS capped at ``threads``; the fit writes the calibration
    and model caches (into ``cache_dir``, the parent's cache directory), and only the name goes back (the parent loads the
    model from its cache file). The worker is spawned, so these arguments are all it has: it opens the library itself (the
    parent sized the work, so it needs no limits), and the cap reaches every BLAS / OpenMP pool the fit uses because
    importing this module has loaded numpy, scipy and scikit-learn before it is set."""
    with threadpool_limits(limits=threads):
        lib = Library(root, calibrate=calibrate, cache_dir=cache_dir)
        if not lib._load(stratum, quantity):             # another process may have written it meanwhile
            lib._fit(stratum, quantity)
    return quantity


def _calibrate_fold_in_worker(root: Path, calibrate: bool, stratum: str, quantity: str, seed: int, threads: int,
                              cache_dir: Path | None = None) -> dict:
    """One ``Library.models`` worker job: one calibration fold of a composed curve (every part refitted on the fold's
    training rows), with BLAS capped at ``threads``, reading the dataset from ``cache_dir`` (the parent's cache directory);
    the held-out errors go back (a few kB), and the parent, which holds the curve's lock meanwhile, writes the calibration
    from all five."""
    with threadpool_limits(limits=threads):
        return Library(root, calibrate=calibrate, cache_dir=cache_dir)._composed_fold(stratum, quantity, seed)


# -- compute within the machine's limits ---------------------------------------------------------------------------------

def fit_memory_gb(rows: int, dims: int) -> float:
    """Peak memory of one fit on ``rows`` rows over ``dims`` dims, in GB rounded up to 0.1. The hyperparameter search
    evaluates the log marginal likelihood with its gradient: the kernel gradient holds rows x rows x (dims + 2) float64
    (a length scale per dim, the amplitude and the noise level), and the Matern gradient terms and the kernel sum's
    stacked copies keep two more arrays of that size alive at once, so FIT_PEAK_COPIES = 3 (tracemalloc measured 3.00 on
    scikit-learn 1.3, 3-6 dims, 300-900 rows); everything else in a fit is rows x rows or smaller. A per_nt sub-GP sees
    one turns level's rows and one dim fewer, so this bounds it from above."""
    need = FIT_PEAK_COPIES * rows * rows * (dims + 2) * np.dtype(np.float64).itemsize
    return max(1, -(-need * 10 // BYTES_PER_GB)) / 10


def omp_cap() -> int | None:
    """The explicit thread cap: the smallest of OMP_NUM_THREADS, OPENBLAS_NUM_THREADS and MKL_NUM_THREADS that is set to a
    positive integer (of a nested list such as ``4,2``, the first value), or None when none is. Each of them caps a BLAS /
    OpenMP pool the fits use, so the smallest one is what the user allowed. No library process gets more threads than
    this: an explicit cap is lowered further, never raised."""
    caps = [int(first) for name in THREAD_CAP_VARIABLES
            if (first := os.environ.get(name, "").split(",")[0].strip()).isdigit() and int(first) > 0]
    return min(caps, default=None)


def blas_threads(limits: site.HostLimits, threads: int | None = None) -> int:
    """BLAS threads for library work in this process (a region's predictions): ``threads``, an explicit cap within
    max_threads, else max_threads; never more than the explicit cap (``omp_cap``)."""
    budget = limits.max_threads if threads is None else _thread_budget(limits, threads)
    cap = omp_cap()
    return budget if cap is None else min(budget, cap)


def fit_plan(limits: site.HostLimits, missing: int, rows: int, dims: int, *, workers: int | None = None,
             threads: int | None = None) -> tuple[int, int]:
    """``(worker processes, BLAS threads per worker)`` to fit ``missing`` models of at most ``rows`` usable rows over ``dims``
    dims within ``limits``, the machine running ic-opt:

    - workers: one per model, at most max_threads // THREADS_PER_FIT (a fit keeps about two cores busy whatever BLAS
      gets), max_memory_gb // ``fit_memory_gb`` of the largest model and, on Windows, the WINDOWS_MAX_WORKERS processes
      ProcessPoolExecutor allows; at least one -- but a single fit bigger than max_memory_gb is refused (EnvelopeError);
    - BLAS threads per worker: the thread budget (``threads``, else max_threads) shared out, at least one each, never
      more than the explicit cap (``omp_cap``: OMP_NUM_THREADS, OPENBLAS_NUM_THREADS, MKL_NUM_THREADS).

    ``workers`` and ``threads`` are explicit caps: honoured up to these limits, refused above them with a ValueError that
    names the limit (``threads`` at most max_threads; ``workers`` within every bound above and, with ``threads``, at most
    that many)."""
    budget = limits.max_threads if threads is None else _thread_budget(limits, threads)
    per_fit = fit_memory_gb(rows, dims)
    machine = "of this machine (site.yaml hosts.local)"
    if per_fit > limits.max_memory_gb:                   # like the engine: a job the host cannot hold is refused, not run alone
        raise site.EnvelopeError(f"fitting a model of {rows} rows over {dims} dims needs about {per_fit:g} GB, above max_memory_gb "
                                 f"{limits.max_memory_gb:g} {machine}: raise the entry or reduce the rows")
    bounds = [(max(1, limits.max_threads // THREADS_PER_FIT),
               f"max_threads {limits.max_threads} {machine} // {THREADS_PER_FIT}, a fit keeping about {THREADS_PER_FIT} cores busy"),
              (max(1, int(limits.max_memory_gb // per_fit)),
               f"max_memory_gb {limits.max_memory_gb:g} {machine} // {per_fit:g} GB per fit ({rows} rows, {dims} dims)")]
    if threads is not None:
        bounds.append((threads, f"threads={threads}, one per worker at least"))
    if sys.platform == "win32":
        bounds.append((WINDOWS_MAX_WORKERS, "ProcessPoolExecutor's worker limit on Windows"))
    if workers is not None:
        if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
            raise ValueError(f"workers must be a positive integer, got {workers!r}")
        for bound, why in bounds:
            if workers > bound:
                raise ValueError(f"workers={workers} exceeds {bound}: {why}")
    n = max(1, min(missing, workers if workers is not None else min(bound for bound, _ in bounds)))
    per_worker = max(1, budget // n)
    cap = omp_cap()
    return n, per_worker if cap is None else min(per_worker, cap)


def _thread_budget(limits: site.HostLimits, threads: int) -> int:
    """An explicit ``threads``, checked against the machine's max_threads."""
    if isinstance(threads, bool) or not isinstance(threads, int) or threads < 1:
        raise ValueError(f"threads must be a positive integer, got {threads!r}")
    if threads > limits.max_threads:
        raise ValueError(f"threads={threads} exceeds max_threads {limits.max_threads} of this machine (site.yaml hosts.local); "
                         "lower threads or raise that entry")
    return threads


def query(library: Library, stratum: str, params: dict, quantities: list[str] | None = None, *, k: float = 2.0,
          rel_sigma_max: float | None = None) -> dict:
    """Measured values at an exact library point, else per-quantity predictions with calibrated k-sigma bounds and domain verdicts;
    ``notes`` carries the library's (``Library.notes``). ``rel_sigma_max`` is the confidence ceiling for every quantity; None:
    each quantity's own (``Library.rel_sigma_max``), which a prediction reports with its ``rel_sigma``."""
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
    out: dict = {"stratum": stratum, "params": coords, "measured": None, "quantities": {}, "notes": library.notes}
    if row is not None:
        out["measured"] = {"part": row.part, "obs_id": row.obs_id}
        for q in wanted:
            out["quantities"][q] = {"status": "measured", "value": row.values.get(q), "unit": unit(q)}
        return out
    x = np.array([[coords[d] for d in ds.dims]])
    for q in wanted:
        out["quantities"][q] = _predict(library, stratum, q, coords, x, k, rel_sigma_max)
    return out


def _predict(library: Library, stratum: str, q: str, coords: dict, x: np.ndarray, k: float, rel_sigma_max: float | None) -> dict:
    ds = library.dataset(stratum)
    if q.startswith("SRF"):
        near = _nearest_rows(library, stratum, coords, 5)
        above = [r for r in near if r.values.get(q) is None]
        if len(above) >= ABOVE_SWEEP_VOTES:
            return {"status": "above_sweep", "value": None, "lower_bound": min(r.stop_hz for r in above), "unit": "Hz",
                    "reason": f"{len(above)} of the 5 nearest measured rows have no resonance inside their sweep",
                    "nearest": [_evidence(r, q, ds.dims) for r in near[:3]]}
    m = library.model(stratum, q)
    scale = fit_unit(q)
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
    ceiling = m.rel_sigma_max if rel_sigma_max is None else float(rel_sigma_max)
    out = {"status": "predicted" if domain.sigma_ok(mu, sigma, ceiling)[0] else "uncertain",
           "value": float(mu[0]) * scale, "lo": float(lo[0]) * scale, "hi": float(hi[0]) * scale, "k": k,
           "k_scale": m.calibration["k_scale"], "rel_sigma": rel, "rel_sigma_max": ceiling, "unit": unit(q)}
    if isinstance(m.gp, composed.ComposedGP):          # where the value came from: the parts, the rise, what is left
        out["composition"] = m.gp.explain(x, srf_unit=fit_unit("SRF"))[0]
    out["nearest"] = [_evidence(m.rows[i], q, ds.dims, dist) for i, dist in verdict.nearest]
    return out


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
    """What the stratum covers: rows per part and turns level, the achieved range of every dim, usable rows and value range per
    quantity; ``notes`` carries the library's."""
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
    out["notes"] = library.notes
    return out


def load(library: Library, stratum: str | None = None) -> dict:
    """Dataset summaries (rows, cache state, integrity evidence, the library's notes) for one stratum or all of them."""
    names = [stratum] if stratum else library.strata()
    return {name: {"cache": library.dataset(name).cache, **dataset.check(library.dataset(name)), "notes": library.notes}
            for name in names}
