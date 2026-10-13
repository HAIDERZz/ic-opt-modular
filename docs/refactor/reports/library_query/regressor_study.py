"""N-107: PACOSYT's other regressors, each tuned by an inner cross-validation on the training rows alone, behind the
device library's model interface, measured against the library's Gaussian process on N-104's folds of the
second-generation library. Point errors only (no intervals); no EMX; the library only read
(``query.Library(root, calibrate=False, cache_dir=SCRATCH)``); no product change. Specification:
``docs/refactor/N107_REGRESSOR_STUDY_SPEC.md``.

Built from N-104's harness (``rbf_mean_model_study.py``), imported, not copied: its tables, columns, outer-diameter dims,
``column_data``, ``folds_of``, ``direct_fold`` / ``composed_fold``, the GP jobs' cache key (``job_hash``), ``rel_stats``
and the verdict rule's constants. What is new here:

- ``VARIANTS``, the registry: ``gp`` (the library's class) plus every variant of the regressor modules beside this file
  -- ``regressors_kernel.py`` (family A), ``regressors_trees.py`` (B), ``regressors_neural.py`` (C) -- each imported if it
  exists (a missing one is skipped with a line). A module registers its variants in a module-level ``VARIANTS`` dict
  (name -> a ``regressor_base.StratumMean`` subclass, or a dict with the class under ``cls`` or ``model`` -- N-104's
  shape -- and optionally ``family``, ``tuned``, ``grid`` (axes or a list of configurations), ``grid_cut``, ``params``,
  ``note``); without one, its ``StratumMean`` subclasses whose ``MODEL`` is not the base's ``"mean"`` are registered
  under ``MODEL``. A module's ``NOTES`` (strings) and ``SKIPPED`` ({variant: why}) are carried into the report.
- A job of a variant rebinds ``gp.StratumGP`` to the variant's class while its fold runs (and back), so a composed
  column builds every part (L_lf, SRF, R / Res) as the variant. The class gets the library's settings of the column plus
  the registry's ``params`` and ``n_threads`` (``--threads``).
- Every ``regressor_base.tune`` call in a worker is recorded (``tune`` is wrapped in ``regressor_base`` and in every
  regressor module that imported it): the configuration chosen, its inner RMSE, the grid's size and failures, the rows
  and inputs, the seconds. A job keeps the list (one entry per tuned (sub-)model: per turns level, per composed part).
  The class a job binds records every instance it fits, so the job also keeps each top-level model's own report
  (``chosen``, ``cv_rmse``, ``cv_scores``, ``inner_scores``, ``timing``, ``fit_info``, ``kernel_fitted`` -- whichever it
  has; per turns level for per_nt; per part for a composed column); the JSON keeps the configuration chosen per fold,
  the job cache everything.
- The ``gp`` jobs' cache key is N-104's, so N-104's 587 GP folds are read from its job cache (``--gp-cache``, read only)
  instead of refitted; a GP job missing there is fitted once and kept in this study's cache. Every other job is keyed by
  its data, settings, its thread count (an lbfgs MLP's result depends on it), the variant's registration and the source
  of ``regressor_base``, its module and this harness's fold runner. ``--variant-threads mlp=1,...`` gives variants their
  own thread count, so the study can run in passes (the MLPs at one thread on more workers) into one job cache
  (``--cache``) and be assembled from it at the end (``--cached-only`` refuses to fit anything).
- Protocol (ii) holds out the levels N-104 selected, read from its JSON (``--n104-json``), so the folds are identical.
- An exception of a non-GP job other than ``Unfit`` (and a composed column's empty part, as N-104) is recorded as an
  ``error`` with its traceback, not cached (a rerun retries it), and the variant counts as not comparable in that cell;
  a GP job's exception still stops the run. Warnings raised in a job are counted per category, not printed.

Per (table, column, protocol): the best comparable non-GP variant (complete and scoring exactly the GP's rows; the lowest
p90, the first in the registry's order on a tie) and its verdict against the GP (N-104's rule: p90 lower by more than
10 % of the other's and the median not higher), the best of each family, the best tuned variant, whether any tuned
variant beats the GP, every variant's own verdict; over all cells which variant is best how often and how often any
tuned regressor beats the GP -- the ceiling question, one number per protocol.

N-108 (``docs/refactor/N108_MLP_GP_HYBRID_SPEC.md``) adds, without changing any N-107 job's key:

- ``regressors_hybrid.py`` (family H): ``mlp_gp_s0`` ... ``mlp_gp_s4``, N-107's ``mlp`` with the network's random state as the
  mean and the library's GP on its residual; ``mlp_s1`` ... ``mlp_s4``, the MLP alone with those seeds; ``gp_refit``, the
  library's GP refitted for its sigma (a reference: never a challenger in the N-107 summaries). A module's ``SEED_GROUPS``
  names the seeds of one model, its ``DEPENDS`` the modules whose source joins its variants' job key.
- A variant registered with ``sigma`` runs its folds through ``direct_fold_sigma`` / ``composed_fold_sigma`` (N-104's fold
  runners plus each scored row's mu, unfloored sigma, measured value and 2-sigma membership): per column, ``k_scale`` from
  protocol (i)'s pooled |z| (``gp.calibration_scale``, the rule of ``Library._calibrate``), the coverage before and after it
  and the median calibrated relative width on both protocols (``intervals``); over the seed groups the N-108 summary
  (``hybrid``: seed medians and spreads, verdicts against ``gp`` and ``mlp``, the three conditions of the specification).
- ``--columns`` takes ``family:column`` or ``table:column`` items besides bare column names; ``--reuse-cache DIR`` (repeatable)
  copies a planned job's result found there into this study's cache (the source only read); ``--spec`` names the
  specification in the JSON; ``report --fig2`` draws the N-108 figure (p90 and calibrated coverage per column).

Usage:
  regressor_study.py run LIBRARY_ROOT SCRATCH OUT_JSON [--tables a,b] [--columns a,fam:b,table:c] [--variants a,b]
                     [--workers 16] [--threads 2] [--variant-threads v=n,...] [--cache NAME] [--cached-only] [--pilot]
                     [--dry-run] [--gp-cache DIR] [--reuse-cache DIR ...] [--n104-json FILE] [--spec PATH]
  regressor_study.py report OUT_JSON --md OUT_MD --fig OUT_PNG [--fig2 OUT_PNG]
Run with OMP_NUM_THREADS=2, IC_OPT_PROFILE_DIRS set and PYTHONPATH at the source tree. The folds run as jobs in a spawn
pool, BLAS / OpenMP capped per worker; every finished job is kept in SCRATCH/<--cache or jobs-<OUT_JSON stem>>/. --pilot: ind_sym_ap,
seeds 0-1, two of N-104's levels, every registered variant. --dry-run: the plan and the GP cache hits only.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import importlib.util
import inspect
import json
import math
import multiprocessing
import os
import shutil
import sys
import time
import traceback
import warnings
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

import numpy as np
import rbf_mean_model_study as n104
import regressor_base
import scipy
import sklearn
from regressor_base import _GP, StratumMean, Unfit
from threadpoolctl import threadpool_limits

from ic_opt.library import composed, gp, query

HERE = Path(__file__).resolve().parent
REGRESSOR_MODULES = ("regressors_kernel", "regressors_trees", "regressors_neural", "regressors_hybrid")
MODULE_FAMILY = {"regressors_kernel": "A", "regressors_trees": "B", "regressors_neural": "C", "regressors_hybrid": "H"}
FAMILIES = ("A", "B", "C", "H")                         # R (gp_refit, a reference) is no family of challengers
UNTUNED = {"gp", "gp_paper", "mlp_paper"}               # the specification's table: no grid
N104_JSON = HERE / "rbf_mean_model_study.json"
N104_GP_CACHE = Path("/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n104_rbf_study/scratch/jobs-rbf_mean_model_study")
N107_SPEC = "docs/refactor/N107_REGRESSOR_STUDY_SPEC.md"
PILOT = {"tables": ["ind_sym_ap"], "seeds": (0, 1), "levels": 2}
PROGRESS_S = 60
# relative cost per (rows of a fit)^2, only to start the longest jobs first and for the progress line's estimate
COST_WEIGHT = {"gp": 1.0, "gp_paper": 1.0, "krr": 0.3, "knn": 0.002, "rf": 0.5, "gbm": 1.0, "lgbm": 0.5, "mlp": 3.0,
               "mlp_paper": 1.0}


# -- the registry -------------------------------------------------------------------------------------------------------------

VARIANTS: dict[str, dict] = {"gp": {"cls": _GP, "family": "A", "tuned": False, "grid": None, "grid_cut": None, "params": {}, "module": gp.__name__,
                                    "note": "the library's GP (Library._fit_inputs settings); N-104's folds, from its job cache"}}
MODULE_NOTES: list[str] = []
SEED_GROUPS: dict[str, list[str]] = {}                 # model -> its seeds' variants (N-108), from the modules' SEED_GROUPS


def _register(name: str, entry, module) -> None:
    spec = {"cls": entry} if isinstance(entry, type) else dict(entry)
    cls = spec.get("cls", spec.get("model"))             # "model": N-104's shape, as B and C register
    if not (isinstance(cls, type) and issubclass(cls, _GP)):   # a StratumMean, or the library's GP itself (gp_refit)
        raise TypeError(f"{module.__name__}: variant {name!r} is not a gp.StratumGP subclass: {cls!r}")
    if name in VARIANTS:
        raise ValueError(f"{module.__name__}: variant {name!r} is registered twice")
    VARIANTS[name] = {"cls": cls, "family": spec.get("family") or getattr(cls, "FAMILY", None) or MODULE_FAMILY.get(module.__name__, "?"),
                      "tuned": bool(spec.get("tuned", name not in UNTUNED)), "grid": spec.get("grid", getattr(cls, "GRID", None)),
                      "grid_cut": spec.get("grid_cut") or None, "params": dict(spec.get("params") or {}), "module": module.__name__,
                      "note": spec.get("note", ""), "sigma": bool(spec.get("sigma")), "reference": bool(spec.get("reference")),
                      "cost_weight": spec.get("cost_weight"), "depends": tuple(getattr(module, "DEPENDS", ()) or ())}


def _load_modules() -> None:
    for name in REGRESSOR_MODULES:
        if importlib.util.find_spec(name) is None:
            MODULE_NOTES.append(f"{name}.py not found: its variants are skipped")
            continue
        try:
            mod = importlib.import_module(name)
        except ImportError as exc:
            MODULE_NOTES.append(f"{name}.py does not import ({type(exc).__name__}: {exc}): its variants are skipped")
            continue
        found = getattr(mod, "VARIANTS", None)
        if isinstance(found, dict):
            entries = list(found.items())
        else:
            entries = [(c.MODEL, c) for c in vars(mod).values() if isinstance(c, type) and issubclass(c, StratumMean)
                       and c.__module__ == mod.__name__ and getattr(c, "MODEL", "mean") != "mean"]
        for vname, entry in entries:
            _register(vname, entry, mod)
        MODULE_NOTES.append(f"{name}.py: {', '.join(v for v, _ in entries) or 'no variant'}")
        MODULE_NOTES.extend(f"{name}.py: {note}" for note in getattr(mod, "NOTES", []) or [])
        MODULE_NOTES.extend(f"{name}.py: {v} skipped -- {why}" for v, why in (getattr(mod, "SKIPPED", {}) or {}).items())
        SEED_GROUPS.update({g: list(vs) for g, vs in (getattr(mod, "SEED_GROUPS", {}) or {}).items()})


_load_modules()


def grid_axes(grid) -> dict[str, list]:
    """A grid as its axes: a dict of axes as it is, a list of configurations as each key's distinct values in order."""
    if not grid:
        return {}
    if isinstance(grid, dict):
        return {k: list(v) for k, v in grid.items()}
    axes: dict[str, list] = {}
    for cfg in grid:
        for k, val in cfg.items():
            if val not in axes.setdefault(k, []):
                axes[k].append(val)
    return axes


def variant_record(v: str) -> dict:
    """A variant's registration as the JSON reports it."""
    e = VARIANTS[v]
    axes = grid_axes(e["grid"])
    n_configs = len(e["grid"]) if isinstance(e["grid"], list) else math.prod(len(x) for x in axes.values()) if axes else 0
    out = {"family": e["family"], "model": e["cls"].__name__, "module": e["module"], "tuned": e["tuned"],
           "grid": json.loads(json.dumps(e["grid"], default=str)), "grid_axes": json.loads(json.dumps(axes, default=str)),
           "n_configs": n_configs, "grid_cut": json.loads(json.dumps(e["grid_cut"], default=str)), "params": e["params"],
           "note": e["note"]}
    out.update({k: True for k in ("sigma", "reference") if e.get(k)})   # only when set: N-107's records (and job keys) unchanged
    return out


# -- every tune call, recorded ------------------------------------------------------------------------------------------------

_ORIGINAL_TUNE = regressor_base.tune
_TUNE = {"log": None}                                    # a list while a job runs in this process


def _numeric(values: list) -> bool:
    return all(isinstance(x, int | float) and not isinstance(x, bool) for x in values)


def _recording_tune(z, t, configs, fit_predict, **kw):
    """``regressor_base.tune``, unchanged, with each call's outcome appended to the running job's log."""
    t0 = time.perf_counter()
    entry = {"n": len(t), "d": int(np.shape(z)[1]), "n_configs": len(configs)}
    axes = {k: sorted({json.dumps(c[k], default=str) for c in configs if k in c}) for k in sorted({k for c in configs for k in c})}
    try:
        best, scores = _ORIGINAL_TUNE(z, t, configs, fit_predict, **kw)
    except Unfit as exc:
        entry.update(config=None, unfit=str(exc), n_failed=len(configs), s=time.perf_counter() - t0)
        if _TUNE["log"] is not None:
            _TUNE["log"].append(entry)
        raise
    key = json.dumps(best, sort_keys=True)
    entry.update(config=json.loads(json.dumps(best, default=str)), rmse=scores[key],
                 n_failed=sum(not math.isfinite(s) for s in scores.values()), s=time.perf_counter() - t0)
    edges = {}
    for k, vals in axes.items():                         # where the chosen value sits on a numeric axis of the grid
        raw = [json.loads(v) for v in vals]
        if len(raw) > 1 and _numeric(raw) and k in best:
            edges[k] = "low" if best[k] == min(raw) else "high" if best[k] == max(raw) else "inside"
    entry["edge"] = edges
    if _TUNE["log"] is not None:
        _TUNE["log"].append(entry)
    return best, scores


def _patch_tune() -> None:
    regressor_base.tune = _recording_tune
    for name in REGRESSOR_MODULES:
        mod = sys.modules.get(name)
        if mod is not None and getattr(mod, "tune", None) is _ORIGINAL_TUNE:
            mod.tune = _recording_tune


_patch_tune()


# -- one fold (runs in a worker) --------------------------------------------------------------------------------------------

def variant_settings(settings: dict | None, variant: str, threads: int) -> dict | None:
    """One model's settings as the variant's class gets them: the library's, plus the registry's params and n_threads (the
    ``gp`` variant: the library's exactly, so its cache key is N-104's)."""
    if settings is None:
        return None
    if variant == "gp":
        return dict(settings)
    return {**settings, **VARIANTS[variant]["params"], "n_threads": threads}


MODEL_ATTRS = ("chosen", "cv_rmse", "cv_scores", "inner_scores", "timing", "fit_info", "kernel_fitted")


def _plain(value):
    return json.loads(json.dumps(value, default=str))


def _attrs(model) -> dict:
    """What a fitted joint (sub-)model says about itself: the attributes of ``MODEL_ATTRS`` it has."""
    out = {}
    for name in MODEL_ATTRS:
        try:
            value = getattr(model, name)
        except (AttributeError, KeyError, TypeError):
            continue
        if value is not None:
            out[name] = _plain(value)
    return out


def model_info(model) -> dict:
    """A top-level fitted model's report: its own attributes, or per turns level its sub-models' for per_nt."""
    if model.nt_mode == "per_nt":
        return {"levels": {str(level): _attrs(sub) for level, sub in sorted(model._sub.items())},
                "unavailable_nt": {str(k): v for k, v in model.unavailable_nt.items()}}
    return _attrs(model)


def _recording(cls: type, fitted: list) -> type:
    """``cls`` with every fitted instance appended to ``fitted`` (a per_nt model's sub-models before the model itself)."""
    def fit(self, x, y):
        out = cls.fit(self, x, y)
        fitted.append(self)
        return out
    return type(cls.__name__, (cls,), {"fit": fit, "__module__": cls.__module__, "__qualname__": cls.__qualname__})


def run_job(job: dict) -> dict:
    """One (table, column, fold, variant) in a spawned worker: BLAS / OpenMP capped, ``gp.StratumGP`` bound to the variant's
    class while N-104's fold runner runs (and restored after it), the tune calls, the fitted models' own reports and the
    warnings recorded."""
    t0 = time.perf_counter()
    variant = job["variant"]
    fitted: list = []
    with threadpool_limits(limits=job["threads"]), warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _TUNE["log"] = []
        gp.StratumGP = VARIANTS[variant]["cls"] if variant == "gp" else _recording(VARIANTS[variant]["cls"], fitted)
        try:
            bound = gp.StratumGP.__name__
            out = n104.direct_fold(job) if job["kind"] == "direct" else n104.composed_fold(job)
        except Exception as exc:                  # sorted below: unfit, an error of one non-GP job, or re-raised for a GP job
            if variant == "gp":
                raise
            built = isinstance(exc, ValueError) and job["kind"] != "direct" and str(exc).startswith(n104.COMPOSED_EMPTY)
            status = "unfit" if isinstance(exc, Unfit) or built else "error"
            reason = f"composed {job['kind']}: {exc}" if built else f"{type(exc).__name__}: {exc}" if status == "error" else str(exc)
            out = {"status": status, "reason": reason[:600], "rows": [], "rel": [], "nonfinite": 0, "skipped": 0, "n_test": 0,
                   "n_train": {}, "fits": 0, "classes": [], "fit_s": time.perf_counter() - t0, "predict_s": 0.0}
            if status == "error":
                out["traceback"] = traceback.format_exc(limit=-8)
        finally:
            gp.StratumGP = _GP
            tuned, _TUNE["log"] = _TUNE["log"], None
        by_category = Counter(w.category.__name__ for w in caught)
        examples = list(dict.fromkeys(f"{w.category.__name__}: {str(w.message)[:160]}" for w in caught))[:3]
    subs = {id(sub) for m in fitted if m.nt_mode == "per_nt" for sub in m._sub.values()}
    tops = [m for m in fitted if id(m) not in subs]   # direct: the model; composed: L_lf, (SRF,) R / Res, in that order
    labels = ["model"] if job["kind"] == "direct" else ["lf", "srf", "part"] if job["kind"] == "resonance" else ["lf", "part"]
    models = [{"part": labels[i] if len(tops) == len(labels) else str(i), **model_info(m)} for i, m in enumerate(tops)]
    out.update({"key": job["key"], "mean_model": bound, "wall_s": time.perf_counter() - t0, "pid": os.getpid(), "tuned": tuned,
                "models": models, "threads": job["threads"], "warnings": {"by_category": dict(by_category), "examples": examples}})
    return out


# -- folds that keep sigma (N-108; run in a worker) ---------------------------------------------------------------------------

def _interval(mu: np.ndarray, sigma: np.ndarray, y: np.ndarray, log_target: bool) -> dict:
    """Per scored row, what an interval needs besides the relative error: the model's mu and unfloored sigma, the measured value
    and whether it lies inside the model's own 2-sigma interval (``gp.prediction_bounds``, k = 2, exact in log space for a log
    target) -- as ``gp.holdout`` and ``composed.holdout_fold`` count it for the library's calibration."""
    mu, sigma, y = (np.asarray(a, dtype=float) for a in (mu, sigma, y))
    lo, hi = gp.prediction_bounds(mu, sigma, log_target=log_target, k=2.0)
    with np.errstate(invalid="ignore"):
        inside = (y >= lo) & (y <= hi)
    return {"mu": mu.tolist(), "sigma": sigma.tolist(), "y": y.tolist(), "inside": inside.tolist(), "log_target": bool(log_target)}


def direct_fold_sigma(job: dict) -> dict:
    """N-104's ``direct_fold`` -- the same fit, held-out rows and relative errors -- keeping each scored row's interval
    (``_interval``)."""
    x, y, train, test = job["x"], job["y"], job["train"], job["test"]
    t0 = time.perf_counter()
    model = gp.StratumGP(**job["settings"]).fit(x[train], y[train])
    t1 = time.perf_counter()
    ok = model.available(x[test])
    rows = test[ok]
    mu, sigma = model.predict(x[rows], floor=False)
    t2 = time.perf_counter()
    rel, nonfinite = n104._scored(mu, y[rows])
    return {"status": "ok", "rows": rows.tolist(), "rel": rel, "nonfinite": nonfinite, "skipped": int((~ok).sum()),
            "n_test": len(test), "n_train": {"model": len(train)}, "fits": 1, "classes": [type(model).__name__],
            "fit_s": t1 - t0, "predict_s": t2 - t1, **_interval(mu, sigma, y[rows], model.log_target)}


def composed_fold_sigma(job: dict) -> dict:
    """N-104's ``composed_fold`` -- every part refitted without the held rows, the curve's positive held rows scored -- keeping
    each scored row's interval in log space (``_interval``), the composed sigma of ``ComposedGP.predict(floor=False)``."""
    s, x, y, lf_y, held = job["settings"], job["x"], job["y"], job["lf_y"], job["held"]
    positive = np.isfinite(y) & (y > 0)
    test = np.flatnonzero(held & positive)
    t0 = time.perf_counter()
    lf_rows = np.isfinite(lf_y) & ~held
    lf_model = gp.StratumGP(**s["lf"]).fit(x[lf_rows], lf_y[lf_rows])
    srf_model, n_train = None, {"lf": int(lf_rows.sum())}
    if s["kind"] == "resonance":
        srf_y = job["srf_y"]
        srf_rows = np.isfinite(srf_y) & ~held
        srf_model = gp.StratumGP(**s["srf"]).fit(x[srf_rows], srf_y[srf_rows])
        n_train["srf"] = int(srf_rows.sum())
    model = composed.ComposedGP(kind=s["kind"], f0=s["f0"], dims=s["dims"], ranges=s["ranges"], nt_dim=s["nt_dim"], part=s["part"],
                                names=s["names"]).attach(lf_model, srf_model)
    train = np.isfinite(y) & ~held
    model.fit(x[train], y[train], lf_y=lf_y[train])
    n_train["part"] = model.n_train
    t1 = time.perf_counter()
    ok = model.available(x[test])
    rows = test[ok]
    mu, sigma = model.predict(x[rows], floor=False)
    t2 = time.perf_counter()
    rel, nonfinite = n104._scored(mu, y[rows])
    parts = [lf_model, srf_model, model.part]
    return {"status": "ok", "rows": rows.tolist(), "rel": rel, "nonfinite": nonfinite, "skipped": int((~ok).sum()),
            "n_test": len(test), "n_train": n_train, "fits": sum(p is not None for p in parts),
            "classes": sorted({type(p).__name__ for p in parts if p is not None}), "fit_s": t1 - t0, "predict_s": t2 - t1,
            **_interval(mu, sigma, y[rows], True)}


def run_sigma_job(job: dict) -> dict:
    """``run_job`` (unchanged) with N-104's fold runners swapped for the sigma-keeping ones while it runs, in this worker."""
    saved = n104.direct_fold, n104.composed_fold
    n104.direct_fold, n104.composed_fold = direct_fold_sigma, composed_fold_sigma
    try:
        return run_job(job)
    finally:
        n104.direct_fold, n104.composed_fold = saved


def _versions() -> str:
    try:
        lgbm = importlib.metadata.version("lightgbm")
    except importlib.metadata.PackageNotFoundError:
        lgbm = None
    return f"{np.__version__} {scipy.__version__} {sklearn.__version__} lightgbm {lgbm}"


def _source_key(v: str) -> str:
    """What a non-GP job's cached result depends on besides its data and settings."""
    mod = sys.modules[VARIANTS[v]["module"]]
    parts = [n104.SOURCE["gp"], inspect.getsource(regressor_base), inspect.getsource(mod),
             *(inspect.getsource(f) for f in (variant_settings, run_job, _recording_tune, _recording, model_info, _attrs)),
             json.dumps(variant_record(v), sort_keys=True, default=str), _versions()]
    # N-108: the modules a variant's module builds on, and the sigma fold runners -- empty for N-107's variants (keys unchanged)
    parts += [inspect.getsource(sys.modules[m]) for m in VARIANTS[v].get("depends", ())]
    if VARIANTS[v].get("sigma"):
        parts += [inspect.getsource(f) for f in (_interval, direct_fold_sigma, composed_fold_sigma, run_sigma_job)]
    return hashlib.sha256("".join(parts).encode()).hexdigest()


def job_hash(job: dict, source: dict[str, str]) -> str:
    """N-104's key for a ``gp`` job (its cache is read); the same construction on this study's source key otherwise."""
    if job["variant"] == "gp":
        return n104.job_hash(job)
    h = hashlib.sha256(source[job["variant"]].encode())
    h.update(json.dumps([job["key"], job["kind"], job["settings"], job["threads"]], sort_keys=True, default=str).encode())
    for name in ("x", "y", "lf_y", "srf_y", "test", "train", "held"):
        a = job.get(name)
        if a is not None:
            h.update(name.encode())
            h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()[:24]


# -- the plan (main process) ------------------------------------------------------------------------------------------------

def make_job(table: str, column: str, col: dict, fold: dict, variant: str, threads: int) -> dict:
    job = {"key": [table, column, fold["protocol"], fold["fold"], variant], "variant": variant, "kind": col["kind"], "x": col["x"],
           "y": col["y"], "threads": threads}
    if col["kind"] == "direct":
        job.update(settings=variant_settings(col["settings"], variant, threads), test=fold["test"], train=fold["train"])
        sizes = [len(fold["train"])]
    else:
        s = col["settings"]
        job.update(settings={**s, "part": variant_settings(s["part"], variant, threads), "lf": variant_settings(s["lf"], variant, threads),
                             "srf": variant_settings(s["srf"], variant, threads)},
                   lf_y=col["lf_y"], srf_y=col["srf_y"], held=fold["held"])
        sizes = [int((np.isfinite(col["lf_y"]) & ~fold["held"]).sum()), int((np.isfinite(col["y"]) & ~fold["held"]).sum())]
        if col["kind"] == "resonance":
            sizes.append(int((np.isfinite(col["srf_y"]) & ~fold["held"]).sum()))
    job["cost"] = COST_WEIGHT.get(variant, VARIANTS[variant].get("cost_weight") or 1.0) * sum(float(n) ** 2 for n in sizes)
    job["sizes"] = sizes
    if "level" in fold:
        job["level"] = fold["level"]
    return job


def n104_levels(path: Path) -> dict[str, list[float]]:
    rep = json.loads(path.read_text(encoding="utf-8"))
    return {t: [float(v) for v in te["levels"]] for t, te in rep["tables"].items()}


# -- summaries ----------------------------------------------------------------------------------------------------------------

def verdict(g: dict, r: dict) -> str:
    """N-104's rule, the roles named for this study: ``other_better`` when r's p90 is below g's by more than 10 % of g's and
    r's median is not above g's; ``gp_better`` the other way round; else ``tie``."""
    if r["p90_rel"] < (1 - n104.BETTER) * g["p90_rel"] and r["median_rel"] <= g["median_rel"]:
        return "other_better"
    if g["p90_rel"] < (1 - n104.BETTER) * r["p90_rel"] and g["median_rel"] <= r["median_rel"]:
        return "gp_better"
    return "tie"


def _order(names) -> list[str]:
    known = list(VARIANTS)
    return sorted(names, key=lambda v: (known.index(v) if v in known else len(known), v))


def _tune_summary(res: list[dict]) -> dict:
    calls = [c for r in res for c in (r.get("tuned") or [])]
    if not calls:
        return {}
    chosen = Counter(json.dumps(c["config"], sort_keys=True) for c in calls if c.get("config") is not None)
    edges: dict[str, Counter] = {}
    for c in calls:
        for k, where in (c.get("edge") or {}).items():
            edges.setdefault(k, Counter())[where] += 1
    return {"calls": len(calls), "unfit_calls": sum(c.get("config") is None for c in calls), "s": round(sum(c["s"] for c in calls), 3),
            "chosen": dict(chosen.most_common()), "edge": {k: dict(v) for k, v in edges.items()}}


def _brief(m: dict, tuned: bool) -> dict:
    """A fitted model's report as the JSON keeps it per fold: the configuration chosen (a tuned variant's), its inner RMSE,
    the fitted kernel, the final fit's iterations (the full inner scores stay in the job cache)."""
    def one(a: dict) -> dict:
        out = {k: a[k] for k in (("chosen", "cv_rmse") if tuned else ()) + ("kernel_fitted",) if k in a}
        if isinstance(a.get("fit_info"), dict) and "n_iter" in a["fit_info"]:
            out["n_iter"] = a["fit_info"]["n_iter"]
        return out
    if "levels" in m:
        return {"part": m["part"], "levels": {lv: one(a) for lv, a in m["levels"].items()}}
    return {"part": m["part"], **one(m)}


def _reference(v: str) -> bool:
    """A reference variant (``gp_refit``): reported, never a challenger of the GP."""
    return bool(VARIANTS.get(v, {}).get("reference"))


def _best(cands: list[str], variants: dict) -> str | None:
    return min(cands, key=lambda v: variants[v]["p90_rel"]) if cands else None   # ties: the first in the registry's order


def summarise_protocol(results: dict[str, list[dict]], protocol: str) -> dict:
    """Per variant: pooled stats over the protocol's folds, unfit / error folds, timing, the tuned configurations; then the
    best comparable variant overall, per family and among the tuned ones, and the verdicts against the GP."""
    out = {"variants": {}}
    rowsets = {}
    for v in _order(results):
        res = sorted(results[v], key=lambda r: (r.get("level") or 0.0, r["key"][3]))
        st = n104.rel_stats([x for r in res for x in r["rel"]])
        unfit = [{"fold": r["key"][3], "reason": r["reason"]} for r in res if r["status"] == "unfit"]
        errors = [{"fold": r["key"][3], "reason": r["reason"]} for r in res if r["status"] == "error"]
        entry = {"n_scored": st["n"], "median_rel": st["median_rel"], "p90_rel": st["p90_rel"], "max_rel": st["max_rel"],
                 "skipped": sum(r["skipped"] for r in res), "nonfinite": sum(r["nonfinite"] for r in res), "folds": len(res),
                 "unfit_folds": unfit, "error_folds": errors, "complete": not unfit and not errors, "fits": sum(r["fits"] for r in res),
                 "fit_s": round(sum(r["fit_s"] for r in res), 3), "predict_s": round(sum(r["predict_s"] for r in res), 3),
                 "fit_s_per_fold": [round(r["fit_s"], 3) for r in res], "predict_s_per_fold": [round(r["predict_s"], 4) for r in res],
                 "classes": sorted({c for r in res for c in r["classes"]}), "mean_model": sorted({r["mean_model"] for r in res}),
                 "tune": _tune_summary(res),
                 "models_per_fold": {r["key"][3]: [_brief(m, VARIANTS.get(v, {}).get("tuned", True)) for m in r.get("models") or []] for r in res},
                 "threads": sorted({r["threads"] for r in res if "threads" in r}),
                 "warnings": dict(sum((Counter((r.get("warnings") or {}).get("by_category", {})) for r in res), Counter()))}
        if protocol == "ii":
            entry["per_level"] = {r["key"][3]: {**n104.rel_stats(r["rel"]), "status": r["status"], "skipped": r["skipped"]} for r in res}
        out["variants"][v] = entry
        rowsets[v] = {(r["key"][3], i) for r in res for i in r["rows"]}
    if "gp" not in out["variants"] or not out["variants"]["gp"]["n_scored"]:
        return out
    vs = out["variants"]
    g = vs["gp"]
    for v, e in vs.items():
        e["same_rows_as_gp"] = rowsets[v] == rowsets["gp"]
    eligible = [v for v, e in vs.items() if v != "gp" and not _reference(v) and e["complete"] and e["same_rows_as_gp"] and e["n_scored"]]
    out["eligible"] = eligible
    out["verdict_by_variant"] = {v: verdict(g, vs[v]) if v in eligible else "not_comparable" for v in vs if v != "gp" and not _reference(v)}
    best = _best(eligible, vs)
    out["best_other"] = best
    out["verdict"] = verdict(g, vs[best]) if best else "no_other"
    if best:
        out["p90_ratio_best_over_gp"] = vs[best]["p90_rel"] / g["p90_rel"] if g["p90_rel"] else math.inf
    out["best_by_family"] = {}
    for fam in FAMILIES:
        fb = _best([v for v in eligible if VARIANTS.get(v, {}).get("family") == fam], vs)
        if fb:
            out["best_by_family"][fam] = {"variant": fb, "verdict": verdict(g, vs[fb]),
                                          "p90_ratio": vs[fb]["p90_rel"] / g["p90_rel"] if g["p90_rel"] else math.inf}
    tuned = [v for v in eligible if VARIANTS.get(v, {}).get("tuned")]
    bt = _best(tuned, vs)
    out["best_tuned"] = bt
    out["verdict_best_tuned"] = verdict(g, vs[bt]) if bt else "no_other"
    out["any_tuned_better"] = any(out["verdict_by_variant"][v] == "other_better" for v in tuned)
    out["any_better"] = any(out["verdict_by_variant"][v] == "other_better" for v in eligible)
    out["best_overall"] = _best(["gp", *eligible], vs)
    return out


def summary(report: dict, variants: list[str]) -> dict:
    """Verdict counts (best non-GP variant; per table, per variant, per family, best tuned), the ceiling tables, the largest
    p90 ratios, the overshoot list, unfit and error folds, the tuned configurations and the cost per model."""
    others = [v for v in variants if v != "gp" and not _reference(v)]
    counts = {p: Counter() for p in ("i", "ii")}
    by_table = {}
    by_variant = {v: {p: Counter() for p in ("i", "ii")} for v in others}
    by_family = {f: {p: Counter() for p in ("i", "ii")} for f in FAMILIES}
    best_tuned = {p: Counter() for p in ("i", "ii")}
    best_other_n = {p: Counter() for p in ("i", "ii")}
    best_overall_n = {p: Counter() for p in ("i", "ii")}
    ceiling = {p: {"cells": 0, "any_tuned_better": 0, "any_better": 0, "cells_any_tuned_better": [], "cells_gp_best": 0} for p in ("i", "ii")}
    overshoot, ratios, unfit, errors = [], [], [], []
    for t, te in report["tables"].items():
        by_table[t] = {p: Counter() for p in ("i", "ii")}
        for c, ce in te["columns"].items():
            for p, pe in ce.get("protocols", {}).items():
                for v, e in pe["variants"].items():
                    unfit += [{"table": t, "column": c, "protocol": p, "variant": v, **u} for u in e["unfit_folds"]]
                    errors += [{"table": t, "column": c, "protocol": p, "variant": v, **u} for u in e.get("error_folds", [])]
                if "verdict" not in pe:
                    continue
                counts[p][pe["verdict"]] += 1
                by_table[t][p][pe["verdict"]] += 1
                for v, vv in pe["verdict_by_variant"].items():
                    by_variant.setdefault(v, {q: Counter() for q in ("i", "ii")})[p][vv] += 1
                for fam in FAMILIES:
                    fb = pe["best_by_family"].get(fam)
                    by_family[fam][p][fb["verdict"] if fb else "no_other"] += 1
                best_tuned[p][pe["verdict_best_tuned"]] += 1
                best_other_n[p][pe["best_other"] or "none"] += 1
                best_overall_n[p][pe["best_overall"]] += 1
                ceil = ceiling[p]
                ceil["cells"] += 1
                ceil["any_tuned_better"] += pe["any_tuned_better"]
                ceil["any_better"] += pe["any_better"]
                ceil["cells_gp_best"] += pe["best_overall"] == "gp"
                if pe["any_tuned_better"]:
                    ceil["cells_any_tuned_better"].append({"table": t, "column": c, "variants": [
                        v for v, vv in pe["verdict_by_variant"].items() if vv == "other_better" and VARIANTS.get(v, {}).get("tuned")]})
                g = pe["variants"]["gp"]
                if pe.get("best_other"):
                    b = pe["variants"][pe["best_other"]]
                    ratios.append({"table": t, "column": c, "protocol": p, "best_other": pe["best_other"], "ratio": pe["p90_ratio_best_over_gp"],
                                   "gp": {k: g[k] for k in ("median_rel", "p90_rel", "max_rel", "n_scored")},
                                   "other": {k: b[k] for k in ("median_rel", "p90_rel", "max_rel", "n_scored")}, "verdict": pe["verdict"]})
                for v, e in pe["variants"].items():
                    if v != "gp" and e["max_rel"] is not None and e["max_rel"] > n104.OVERSHOOT * g["max_rel"]:
                        overshoot.append({"table": t, "column": c, "protocol": p, "variant": v, "gp_max_rel": g["max_rel"],
                                          "other_max_rel": e["max_rel"], "factor": e["max_rel"] / g["max_rel"] if g["max_rel"] else math.inf,
                                          "complete": e["complete"], "same_rows_as_gp": e.get("same_rows_as_gp"),
                                          "nonfinite": e["nonfinite"], "other_p90_rel": e["p90_rel"], "gp_p90_rel": g["p90_rel"],
                                          "best_other": v == pe.get("best_other")})
    ratios.sort(key=lambda r: r["ratio"])
    cost, tuning = {}, {}
    for v in variants:
        per_job, fits, fit_s, pred_s, tune_s, per_table, calls = [], 0, 0.0, 0.0, 0.0, {}, Counter()
        edges: dict[str, Counter] = {}
        n_calls = 0
        for t, te in report["tables"].items():
            tt = {"jobs": 0, "fits": 0, "fit_s": 0.0, "predict_s": 0.0}
            for ce in te["columns"].values():
                for pe in ce.get("protocols", {}).values():
                    e = pe["variants"].get(v)
                    if e is None:
                        continue
                    per_job += e["fit_s_per_fold"]
                    tt["jobs"] += e["folds"]
                    tt["fits"] += e["fits"]
                    tt["fit_s"] += e["fit_s"]
                    tt["predict_s"] += e["predict_s"]
                    tu = e.get("tune") or {}
                    tune_s += tu.get("s", 0.0)
                    n_calls += tu.get("calls", 0)
                    calls.update(tu.get("chosen", {}))
                    for k, d in (tu.get("edge") or {}).items():
                        edges.setdefault(k, Counter()).update(d)
            per_table[t] = {k: round(x, 2) if isinstance(x, float) else x for k, x in tt.items()}
            fits += tt["fits"]
            fit_s += tt["fit_s"]
            pred_s += tt["predict_s"]
        a = np.asarray(per_job, dtype=float)
        cost[v] = {"jobs": len(a), "fits": fits, "fit_s": round(fit_s, 1), "predict_s": round(pred_s, 2), "tune_s": round(tune_s, 1),
                   "fit_s_per_job": {"median": float(np.median(a)) if len(a) else None, "p90": float(np.quantile(a, 0.9)) if len(a) else None,
                                     "max": float(a.max()) if len(a) else None}, "per_table": per_table}
        if n_calls:
            tuning[v] = {"calls": n_calls, "chosen": dict(calls.most_common()), "edge": {k: dict(d) for k, d in edges.items()}}
    def plain(d):
        return {p: dict(c) for p, c in d.items()}
    return {"verdicts": plain(counts), "verdicts_by_table": {t: plain(d) for t, d in by_table.items()},
            "verdicts_by_variant": {v: plain(d) for v, d in by_variant.items()}, "verdicts_by_family": {f: plain(d) for f, d in by_family.items()},
            "verdicts_best_tuned": plain(best_tuned), "best_other_counts": plain(best_other_n), "best_overall_counts": plain(best_overall_n),
            "ceiling": ceiling, "p90_ratios": ratios, "overshoot": overshoot, "unfit": unfit, "errors": errors, "cost": cost, "tuning": tuning}


# -- intervals and the N-108 summary -----------------------------------------------------------------------------------------

#: the specification's section 3: the hybrid holds when (a) its p90 (median of its seeds) is at most 10 % above the better of gp
#: and mlp on both protocols, (b) its calibrated (ii) coverage is at least 0.90 and its (i) coverage before calibration at most
#: 0.05 below the GP's, (c) its p90 spread over the seeds (max / min) is below 1.5 (on both protocols)
HOLD = {"p90_above_best": 0.10, "coverage_ii_min": 0.90, "coverage_i_gap": 0.05, "spread_below": 1.5}


def _z(mu: np.ndarray, sigma: np.ndarray, y: np.ndarray, log_target: bool) -> np.ndarray:
    """|z| of each row as the library's calibration computes it (``gp.holdout``; ``composed.holdout_fold`` in log space)."""
    if log_target:
        return np.abs(np.log(y) - np.log(mu)) / np.maximum(sigma / np.maximum(np.abs(mu), 1e-300), 1e-300)
    return np.abs(y - mu) / np.maximum(sigma, 1e-300)


def _pooled(res: list[dict]) -> dict | None:
    """A sigma variant's scored rows over a protocol's folds (the summaries' fold order); None unless every fold kept them."""
    res = sorted(res, key=lambda r: (r.get("level") or 0.0, r["key"][3]))
    if not res or any(r["status"] != "ok" or "mu" not in r for r in res):
        return None
    spaces = {r["log_target"] for r in res}
    if len(spaces) != 1:
        raise ValueError(f"folds of one column disagree on the fitted space: {res[0]['key']}")
    def cat(name: str, dtype=float) -> np.ndarray:
        return np.asarray([v for r in res for v in r[name]], dtype=dtype)
    return {"mu": cat("mu"), "sigma": cat("sigma"), "y": cat("y"), "inside": cat("inside", bool), "log_target": spaces.pop()}


def interval_stats(rows: dict, k_scale: float, floor_rel: float | None) -> dict:
    """Coverage of the model's own 2-sigma interval before calibration (the folds' count) and with ``k_scale`` applied
    (``gp.prediction_bounds`` with k = 2 k_scale, the library's ``predict_bounds``), the median relative sigma -- raw and
    calibrated (``k_scale x sigma / mu``, the specification's width) -- and the calibrated 2-sigma interval's median relative
    half-width; besides, the calibrated coverage with the library's sigma floor (sigma >= ``floor_rel`` x mu, ``floor_rel``
    the model's own (i) median relative error, as ``Library`` sets ``sigma_floor_rel``)."""
    mu, sigma, y, lt = rows["mu"], rows["sigma"], rows["y"], rows["log_target"]
    if not len(y):
        return {"n": 0}
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        z = _z(mu, sigma, y, lt)
        lo, hi = gp.prediction_bounds(mu, sigma, log_target=lt, k=2.0 * k_scale)
        inside_cal = (y >= lo) & (y <= hi)
        rel_sigma = sigma / np.abs(mu)
        half = (hi - lo) / (2.0 * np.abs(mu))
    out = {"n": len(y), "nonfinite_sigma": int((~np.isfinite(sigma)).sum()), "coverage_before": float(np.mean(rows["inside"])),
           "coverage_before_z": float(np.mean(z <= 2.0)), "coverage_calibrated": float(np.mean(inside_cal)),
           "z_median": float(np.median(z)), "z_q95": float(np.quantile(z, 0.95)), "rel_sigma_median": float(np.median(rel_sigma)),
           "rel_sigma_cal_median": float(np.median(k_scale * rel_sigma)), "rel_halfwidth_2sigma_cal_median": float(np.median(half))}
    if floor_rel:
        floored = np.maximum(sigma, floor_rel * np.abs(mu))
        lo_f, hi_f = gp.prediction_bounds(mu, floored, log_target=lt, k=2.0 * k_scale)
        out["coverage_calibrated_floored"] = float(np.mean((y >= lo_f) & (y <= hi_f)))
        out["rel_sigma_cal_floored_median"] = float(np.median(k_scale * floored / np.abs(mu)))
    return out


def _fold_rel(res: list[dict]) -> dict:
    return {(r["key"][3], i): e for r in res for i, e in zip(r["rows"], r["rel"])}


def _mlp_signature(r: dict) -> list:
    """What decides a job's MLP: per (sub-)model the configuration chosen, the inner cross-validation's scores and the final
    fit's iterations."""
    def one(a: dict) -> list:
        return [a.get("chosen"), a.get("inner_scores"), (a.get("fit_info") or {}).get("n_iter")]
    return [[m["part"], {lv: one(a) for lv, a in m["levels"].items()} if "levels" in m else one(m)] for m in r.get("models") or []]


def column_intervals(by_p: dict[str, dict[str, list[dict]]], protocols: dict) -> dict:
    """Per sigma variant of a column: ``k_scale`` from protocol (i)'s pooled |z| (``gp.calibration_scale``: the 0.95 quantile
    over 2, at least 1 -- ``Library._calibrate``'s rule), then ``interval_stats`` on (i) and (ii) with it. A reference variant
    (``gp_refit``) also says whether it scored the cached ``gp``'s rows with the same relative errors; ``mlp_gp_s0`` whether
    its MLP is N-107's ``mlp`` fold by fold (``_mlp_signature``)."""
    out: dict = {}
    res_i = by_p.get("i", {})
    for v in _order(res_i):
        if not VARIANTS.get(v, {}).get("sigma"):
            continue
        rows_i = _pooled(res_i[v])
        if rows_i is None:
            out[v] = {"complete": False}
            continue
        with np.errstate(divide="ignore", invalid="ignore"):
            z_i = _z(rows_i["mu"], rows_i["sigma"], rows_i["y"], rows_i["log_target"])
        k = gp.calibration_scale({"z": z_i.tolist()})
        floor = protocols["i"]["variants"][v]["median_rel"]
        entry = {"complete": True, "log_target": rows_i["log_target"], "k_scale": k, "nonfinite_z_i": int((~np.isfinite(z_i)).sum()),
                 "sigma_floor_rel": floor, "i": interval_stats(rows_i, k, floor)}
        if v in by_p.get("ii", {}):
            rows_ii = _pooled(by_p["ii"][v])
            entry["ii"] = {"complete": False} if rows_ii is None else interval_stats(rows_ii, k, floor)
        out[v] = entry
    checks: dict = {}
    for p, res in by_p.items():
        for v in res:
            if _reference(v) and "gp" in res:
                a, b = _fold_rel(res["gp"]), _fold_rel(res[v])
                diff = [abs(b[key] - a[key]) for key in a if key in b and math.isfinite(a[key]) and math.isfinite(b[key])]
                checks.setdefault(f"{v}_vs_gp", {})[p] = {"same_rows": set(a) == set(b), "n": len(diff),
                                                         "max_abs_diff_rel": max(diff) if diff else None}
        group = SEED_GROUPS.get("mlp_gp", [])
        if group and group[0] in res and "mlp" in res:
            mine = {r["key"][3]: _mlp_signature(r) for r in res[group[0]]}
            theirs = {r["key"][3]: _mlp_signature(r) for r in res["mlp"]}
            same = [f for f in mine if f in theirs and json.dumps(mine[f], sort_keys=True) == json.dumps(theirs[f], sort_keys=True)]
            checks.setdefault(f"{group[0]}_mlp_is_mlp", {})[p] = {"folds": len(mine), "identical": len(same),
                                                                  "differ": sorted(set(mine) - set(same))}
    if checks:
        out["checks"] = checks
    return out


def _seed_group(pe: dict, names: list[str]) -> dict | None:
    """The seeds of one model in a protocol cell: each seed's median / p90 / max (seeds that fitted every fold and scored the
    GP's rows), their medians and the p90 spread (max / min)."""
    vs = pe["variants"]
    ok = [v for v in names if v in vs and vs[v]["complete"] and vs[v].get("same_rows_as_gp") and vs[v]["n_scored"]]
    if not ok:
        return None
    p90, med, mx = ([vs[v][k] for v in ok] for k in ("p90_rel", "median_rel", "max_rel"))
    return {"seeds": ok, "missing": [v for v in names if v not in ok], "p90": p90, "median": med, "max": mx,
            "p90_median": float(np.median(p90)), "median_median": float(np.median(med)), "max_median": float(np.median(mx)),
            "p90_spread": max(p90) / min(p90) if min(p90) > 0 else math.inf}


def _median_of(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return float(np.median(vals)) if vals else None


def _interval_brief(e: dict | None) -> dict | None:
    """A sigma variant's interval figures the summary keeps: k_scale, coverage before / after calibration on both protocols,
    the calibrated relative sigma and 2-sigma half-width, the floored coverage."""
    if not e or not e.get("complete"):
        return None
    out = {"k_scale": e["k_scale"]}
    for p in ("i", "ii"):
        st = e.get(p)
        if st and st.get("n"):
            for k in ("coverage_before", "coverage_calibrated", "coverage_calibrated_floored", "rel_sigma_cal_median",
                      "rel_halfwidth_2sigma_cal_median", "z_q95"):
                if k in st:
                    out[f"{k}_{p}"] = st[k]
    return out


def hybrid_summary(report: dict) -> dict:
    """N-108, per column: the seed groups' point errors against ``gp`` (N-104's rule on the seed medians, and seed by seed),
    the hybrid against ``mlp`` (seed medians, and each hybrid seed against the MLP of the same seed), the intervals (``gp`` from
    ``gp_refit``) and the specification's three conditions; then the counts per table, the failures and the largest p90
    ratios."""
    hyb, mlps = SEED_GROUPS.get("mlp_gp", []), SEED_GROUPS.get("mlp", [])
    ref = next((v for v in report["variants"] if report["variants"][v].get("reference")), None)
    columns, ratios = [], []
    counts: dict[str, Counter] = {}
    for t, te in report["tables"].items():
        counts[t] = Counter()
        for c, ce in te["columns"].items():
            prot = ce.get("protocols", {})
            if "i" not in prot or not any(v in prot["i"]["variants"] for v in hyb):
                continue
            entry = {"table": t, "column": c, "kind": ce["kind"], "protocols": {}}
            for p, pe in prot.items():
                g = pe["variants"]["gp"]
                m, h = _seed_group(pe, mlps), _seed_group(pe, hyb)
                pp = {"gp": {k: g[k] for k in ("median_rel", "p90_rel", "max_rel", "n_scored")}, "mlp": m, "mlp_gp": h}
                if h:
                    hm = {"p90_rel": h["p90_median"], "median_rel": h["median_median"]}
                    pp["verdict_vs_gp"] = verdict(g, hm)
                    pp["per_seed_vs_gp"] = dict(Counter(verdict(g, pe["variants"][v]) for v in h["seeds"]))
                    pp["ratio_vs_gp"] = h["p90_median"] / g["p90_rel"] if g["p90_rel"] else math.inf
                    best = g["p90_rel"]
                    if m:
                        mm = {"p90_rel": m["p90_median"], "median_rel": m["median_median"]}
                        pp["verdict_vs_mlp"] = verdict(mm, hm)
                        pairs = [(a, b) for a, b in zip(mlps, hyb) if a in m["seeds"] and b in h["seeds"]]
                        pp["per_seed_vs_mlp"] = dict(Counter(verdict(pe["variants"][a], pe["variants"][b]) for a, b in pairs))
                        pp["ratio_vs_mlp"] = h["p90_median"] / m["p90_median"] if m["p90_median"] else math.inf
                        best = min(best, m["p90_median"])
                    pp["best_reference_p90"] = best
                    pp["ratio_vs_best"] = h["p90_median"] / best if best else math.inf
                    ratios.append({"table": t, "column": c, "protocol": p, "ratio_vs_gp": pp["ratio_vs_gp"], "ratio_vs_mlp": pp.get("ratio_vs_mlp"),
                                   "gp": pp["gp"], "mlp_p90_median": m["p90_median"] if m else None, "mlp_gp_p90_median": h["p90_median"],
                                   "mlp_gp_median_median": h["median_median"], "mlp_gp_max_median": h["max_median"]})
                entry["protocols"][p] = pp
            iv = ce.get("intervals", {})
            per_seed = {v: _interval_brief(iv.get(v)) for v in hyb if v in iv}
            keys = sorted({k for b in per_seed.values() if b for k in b})
            entry["intervals"] = {"gp": _interval_brief(iv.get(ref)) if ref else None, "gp_from": ref,
                                  "mlp_gp": {"per_seed": per_seed, "median": {k: _median_of([(b or {}).get(k) for b in per_seed.values()]) for k in keys},
                                             "min": {k: min((b[k] for b in per_seed.values() if b and k in b), default=None) for k in keys}}}
            entry["checks"] = iv.get("checks", {})
            entry["conditions"], entry["holds"] = _conditions(entry)
            counts[t][entry["holds"]] += 1
            columns.append(entry)
    ratios.sort(key=lambda r: r["ratio_vs_gp"])
    total = sum(counts.values(), Counter())
    return {"rule": dict(HOLD), "seed_groups": {"mlp": mlps, "mlp_gp": hyb}, "interval_reference": ref, "columns": columns,
            "counts": {t: dict(n) for t, n in counts.items()}, "counts_total": dict(total), "p90_ratios": ratios,
            "failures": [{"table": e["table"], "column": e["column"], "failed": [k for k, v in e["conditions"].items() if v["ok"] is False]}
                         for e in columns if e["holds"] == "fails"]}


def _conditions(entry: dict) -> tuple[dict, str]:
    """The specification's (a), (b), (c) for one column: each ``{"ok": True | False | None, ...the numbers}`` (None: not
    measurable, as (ii) on a column without an eligible level); the column ``holds`` when all three are True, ``fails`` when one
    is False, else ``no_level``."""
    prot, iv = entry["protocols"], entry["intervals"]
    cond = {}
    a = {}
    for p in ("i", "ii"):
        pp = prot.get(p)
        if pp is None or not pp.get("mlp_gp"):
            a[p] = None
            continue
        a[p] = {"ok": pp["ratio_vs_best"] <= 1 + HOLD["p90_above_best"], "ratio_vs_best": pp["ratio_vs_best"],
                "mlp_gp_p90": pp["mlp_gp"]["p90_median"], "best_reference_p90": pp["best_reference_p90"]}
    cond["a"] = {"ok": _all_ok(a.values()), **{p: a[p] for p in a}}
    hm, gi = iv["mlp_gp"]["median"], iv["gp"] or {}
    cov_ii = hm.get("coverage_calibrated_ii")
    cov_i, gp_i = hm.get("coverage_before_i"), gi.get("coverage_before_i")
    b_ii = None if cov_ii is None else cov_ii >= HOLD["coverage_ii_min"]
    b_i = None if cov_i is None or gp_i is None else cov_i >= gp_i - HOLD["coverage_i_gap"]
    cond["b"] = {"ok": _all_ok([None if b_ii is None else {"ok": b_ii}, None if b_i is None else {"ok": b_i}]),
                 "coverage_calibrated_ii": cov_ii, "coverage_calibrated_ii_ok": b_ii, "coverage_before_i": cov_i,
                 "gp_coverage_before_i": gp_i, "coverage_before_i_ok": b_i}
    c = {}
    for p in ("i", "ii"):
        h = (prot.get(p) or {}).get("mlp_gp")
        c[p] = None if not h else {"ok": h["p90_spread"] < HOLD["spread_below"], "p90_spread": h["p90_spread"], "seeds": len(h["seeds"])}
    cond["c"] = {"ok": _all_ok(c.values()), **{p: c[p] for p in c}}
    oks = [v["ok"] for v in cond.values()]
    return cond, "fails" if False in oks else "holds" if all(oks) else "no_level"


def _all_ok(parts) -> bool | None:
    """False if any part fails, None if any part is missing (and none fails), else True."""
    parts = list(parts)
    if any(x is not None and x["ok"] is False for x in parts):
        return False
    return None if any(x is None for x in parts) else True


# -- run ----------------------------------------------------------------------------------------------------------------------

def library_calibration_full(lib: query.Library, table: str, column: str) -> dict | None:
    """The library's own cached calibration of the column as it stands (read only): ``k_scale``, the coverage before it, the
    median relative error and the rows behind them -- what ``gp_refit``'s protocol (i) should reproduce."""
    found = n104.library_calibration(lib, table, column)
    if found is None:
        return None
    cal = json.loads(lib.cache.find(found["file"]).read_text(encoding="utf-8"))
    return {k: cal.get(k) for k in ("k_scale", "coverage_2sigma_before", "median_rel", "n_scored")} | {"file": found["file"]}


def _column_filter(arg: str | None) -> list[tuple[str | None, str]] | None:
    """``--columns``: bare names apply to every table, ``family:name`` / ``table:name`` to that family's / table's only."""
    if not arg:
        return None
    out = []
    for item in arg.split(","):
        scope, _, name = item.rpartition(":")
        out.append((scope or None, name))
    return out


def _wanted(filt: list[tuple[str | None, str]] | None, table: str, family: str, column: str) -> bool:
    return filt is None or any(name == column and scope in (None, table, family) for scope, name in filt)


def run(args) -> None:
    t_start = time.time()
    seeds = PILOT["seeds"] if args.pilot else gp.SEEDS
    tables = args.tables.split(",") if args.tables else (PILOT["tables"] if args.pilot else list(n104.FAMILY))
    variants = args.variants.split(",") if args.variants else list(VARIANTS)
    unknown = [v for v in variants if v not in VARIANTS] + [t for t in tables if t not in n104.FAMILY]
    if unknown:
        raise SystemExit(f"unknown tables / variants {unknown}; tables {list(n104.FAMILY)}, variants {list(VARIANTS)}")
    variants = _order(variants)
    vthreads = {v: int(n) for v, n in (item.split("=") for item in args.variant_threads.split(",") if item)} if args.variant_threads else {}
    if bad := [v for v in vthreads if v not in VARIANTS]:
        raise SystemExit(f"--variant-threads names unknown variants {bad}")
    wanted = _column_filter(args.columns)
    for line in MODULE_NOTES:
        print(line, flush=True)
    chosen_levels = n104_levels(args.n104_json)
    lib = query.Library(args.root, calibrate=False, cache_dir=args.scratch)
    report = {"spec": args.spec, "date_start": time.strftime("%Y-%m-%d %H:%M:%S"),
              "library": str(args.root), "scratch": str(args.scratch), "argv": [Path(sys.argv[0]).name, *sys.argv[1:]],
              "pilot": bool(args.pilot), "seeds": list(seeds), "levels_from": str(args.n104_json), "gp_cache": str(args.gp_cache),
              "reuse_caches": [str(d) for d in args.reuse_cache], "columns_arg": args.columns, "seed_groups": SEED_GROUPS,
              "workers": args.workers, "threads": args.threads, "variant_threads": vthreads, "cached_only": bool(args.cached_only),
              "omp_num_threads": os.environ.get("OMP_NUM_THREADS"),
              "source": n104.git_head(), "versions": {"python": sys.version.split()[0], "numpy": np.__version__, "scipy": scipy.__version__,
                                                      "sklearn": sklearn.__version__, "all": _versions()},
              "modules": list(MODULE_NOTES), "variants": {v: variant_record(v) for v in variants},
              "min_level_rows": n104.MIN_LEVEL_ROWS, "min_nt_samples": _GP.MIN_NT_SAMPLES,
              "verdict_rule": {"better_by": n104.BETTER, "overshoot_factor": n104.OVERSHOOT}, "tables": {}}
    cols: dict[tuple[str, str], dict] = {}
    jobs = []
    for t in tables:
        ds = lib.dataset(t)
        fam = n104.FAMILY[t]
        j = ds.dims.index(n104.LEVEL_DIM[fam])
        names = [c for c in n104.COLUMNS[fam] if _wanted(wanted, t, fam, c)]
        data = {c: n104.column_data(lib, t, c, j) for c in names}
        eligible = sorted(set().union(*(d["eligible"] for d in data.values()))) if data else []
        n104_t = chosen_levels.get(t, [])
        if wanted is None and n104.spread(eligible, 8) != n104_t:
            print(f"WARNING {t}: N-104's levels {n104_t} differ from those recomputed here {n104.spread(eligible, 8)}; N-104's are used",
                  flush=True)
        levels = n104.spread(n104_t, PILOT["levels"]) if args.pilot else list(n104_t)
        entry = {"rows": len(ds.rows), "dataset_key": ds.key, "dims": ds.dims, "nt_dim": ds.nt_dim, "level_dim": n104.LEVEL_DIM[fam],
                 "eligible_levels": eligible, "levels": levels, "levels_n104": n104_t, "columns": {}}
        for c, d in data.items():
            folds = n104.folds_of(d, j, seeds, levels)
            st = d["settings"] if d["kind"] == "direct" else d["settings"]["part"]
            entry["columns"][c] = {"kind": d["kind"], "n_usable": d["n"], "eligible_levels": d["eligible"],
                                   "levels": [f["level"] for f in folds if f["protocol"] == "ii"],
                                   "settings": {"nt_mode": st["nt_mode"], "feature_map": st["feature_map"], "log_target": st["log_target"]},
                                   "library_calibration": n104.library_calibration(lib, t, c),
                                   "library_calibration_full": library_calibration_full(lib, t, c)}
            if d["kind"] != "direct":
                s = d["settings"]
                entry["columns"][c]["parts"] = {"lf": s["names"]["lf"], "srf": s["names"]["srf"], "f0_ghz": s["f0"],
                                                "lf_settings": {k: s["lf"][k] for k in ("nt_mode", "feature_map", "log_target")},
                                                "srf_settings": None if s["srf"] is None else {k: s["srf"][k] for k in ("nt_mode", "feature_map", "log_target")}}
            cols[(t, c)] = d
            jobs += [make_job(t, c, d, f, v, vthreads.get(v, args.threads)) for f in folds for v in variants]
        report["tables"][t] = entry
        print(f"{t}: {len(ds.rows)} rows, {len(names)} columns, levels {levels} (N-104's {n104_t}), "
              f"{sum(1 for jb in jobs if jb['key'][0] == t)} jobs", flush=True)
    print(f"{len(jobs)} jobs ({len(variants)} variants: {', '.join(variants)}); seeds {list(seeds)}; plan in {time.time() - t_start:.1f} s",
          flush=True)
    source = {v: _source_key(v) for v in variants if v != "gp"}
    cache = args.scratch / (args.cache or f"jobs-{args.out.stem}")
    found = {}
    for job in jobs:
        job["hash"] = job_hash(job, source)
        own = cache / f"{job['hash']}.json"
        n104_file = args.gp_cache / f"{job['hash']}.json"
        reuse = next((d / own.name for d in args.reuse_cache if (d / own.name).is_file()), None)
        if own.is_file():
            found[id(job)] = ("own", own)
        elif reuse is not None:                         # another study's result: copied into this study's cache below
            found[id(job)] = ("copy", reuse)
        elif job["variant"] == "gp" and n104_file.is_file():
            found[id(job)] = ("n104", n104_file)
    gp_jobs = [jb for jb in jobs if jb["variant"] == "gp"]
    hits = Counter(found[id(jb)][0] for jb in gp_jobs if id(jb) in found)
    print(f"gp: {len(gp_jobs)} jobs, {hits['n104']} from N-104's job cache {args.gp_cache}, {hits['copy']} to copy from --reuse-cache, "
          f"{hits['own']} from this study's, {len(gp_jobs) - hits['n104'] - hits['own'] - hits['copy']} to fit", flush=True)
    copies = Counter((jb["variant"], str(found[id(jb)][1].parent)) for jb in jobs if found.get(id(jb), ("",))[0] == "copy")
    for (v, d), n in sorted(copies.items()):
        print(f"  {v}: {n} jobs to copy from {d}", flush=True)
    if args.dry_run:
        for t in tables:
            for v in variants:
                sel = [jb for jb in jobs if jb["key"][0] == t and jb["variant"] == v]
                sizes = [n for jb in sel for n in jb["sizes"]]
                cached = sum(id(jb) in found for jb in sel)
                print(f"  {t} {v}: {len(sel)} jobs ({cached} cached), {len(sizes)} fits, rows per fit {min(sizes)}..{max(sizes)}, "
                      f"sum n^2 {sum(n * n for n in sizes):.3g}", flush=True)
        return
    cache.mkdir(parents=True, exist_ok=True)
    results, pending = [], []
    from_cache, copied = Counter(), Counter()
    for job in jobs:
        if id(job) in found:
            where, path = found[id(job)]
            if where == "copy":                         # the source is only read; the copy is this study's from now on
                own = cache / path.name
                tmp = own.with_suffix(".tmp")
                shutil.copyfile(path, tmp)
                tmp.replace(own)
                copied[str(path.parent)] += 1
                path = own
            res = json.loads(path.read_text(encoding="utf-8"))
            res["level"] = job.get("level")
            results.append(res)
            from_cache[where] += 1
        else:
            pending.append(job)
    if pending and args.cached_only:
        raise SystemExit(f"--cached-only: {len(pending)} jobs are not in the caches, e.g. {[jb['key'] for jb in pending[:5]]}")
    pending.sort(key=lambda jb: -jb["cost"])
    total_cost = sum(jb["cost"] for jb in pending) or 1.0
    print(f"{from_cache['own']} jobs from {cache}, {from_cache['n104']} gp jobs from N-104's cache, {len(pending)} to run on "
          f"{args.workers} workers x {args.threads} threads (per variant: {vthreads or 'none'})", flush=True)
    t_pool = time.time()
    done_cost, last, n_done, n_error = 0.0, time.time(), 0, 0
    if pending:
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")) as pool:
            running = {pool.submit(run_sigma_job if VARIANTS[jb["variant"]].get("sigma") else run_job, jb): jb for jb in pending}
            while running:
                finished, _ = wait(list(running), timeout=PROGRESS_S, return_when=FIRST_COMPLETED)
                for fut in finished:
                    job = running.pop(fut)
                    res = fut.result()
                    res["level"] = job.get("level")
                    if res["status"] == "error":            # not cached: a rerun retries it
                        n_error += 1
                        print(f"  ERROR {job['key']}: {res['reason'][:300]}", flush=True)
                    else:
                        path = cache / f"{job['hash']}.json"
                        tmp = path.with_suffix(".tmp")
                        tmp.write_text(json.dumps(res), encoding="utf-8")
                        tmp.replace(path)
                    results.append(res)
                    done_cost += job["cost"]
                    n_done += 1
                if time.time() - last >= PROGRESS_S or not running:
                    last = time.time()
                    elapsed = time.time() - t_pool
                    frac = done_cost / total_cost
                    eta = elapsed / frac - elapsed if frac > 0 else float("nan")
                    print(f"  {n_done}/{len(pending)} jobs done ({n_error} errors), {len(running)} queued or running, {elapsed / 60:.1f} min, "
                          f"cost done {frac * 100:.1f} %, rough remaining {eta / 60:.0f} min", flush=True)
    report["pool_seconds"] = round(time.time() - t_pool, 1)
    report["jobs"] = {"planned": len(jobs), "from_cache": from_cache["own"], "from_gp_cache": from_cache["n104"], "run": len(pending),
                      "copied": dict(copied), "copied_total": from_cache["copy"],
                      "cache": str(cache), "unfit": sum(r["status"] == "unfit" for r in results),
                      "errors": sum(r["status"] == "error" for r in results), "gp_planned": len(gp_jobs),
                      "gp_fitted_here": sum(1 for jb in pending if jb["variant"] == "gp")}
    by = {}
    for r in results:
        t, c, p, _fold, v = r["key"]
        by.setdefault((t, c, p), {}).setdefault(v, []).append(r)
    for t, c in cols:
        col = report["tables"][t]["columns"][c]
        col["protocols"] = {p: summarise_protocol(by[(t, c, p)], p) for p in ("i", "ii") if (t, c, p) in by}
        if any(VARIANTS[v].get("sigma") for v in variants):
            col["intervals"] = column_intervals({p: by[(t, c, p)] for p in ("i", "ii") if (t, c, p) in by}, col["protocols"])
    report["summary"] = summary(report, variants)
    if any(v in variants for v in SEED_GROUPS.get("mlp_gp", [])):
        report["hybrid"] = hybrid_summary(report)
    report["date_end"] = time.strftime("%Y-%m-%d %H:%M:%S")
    report["wall_seconds"] = round(time.time() - t_start, 1)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print_summary(report)
    print("wrote", args.out, f"{report['wall_seconds']:.0f} s", flush=True)


def _pct(v) -> str:
    return n104._pct(v)


def print_summary(report: dict) -> None:
    for t, te in report["tables"].items():
        for c, ce in te["columns"].items():
            for p, pe in ce.get("protocols", {}).items():
                parts = [f"{v} p90 {_pct(e['p90_rel'])}{'' if e['complete'] else ' (unfit/error folds)'}" for v, e in pe["variants"].items()]
                print(f"{t} {c} ({p}) {pe.get('verdict', '-')} [{pe.get('best_other')}]: " + " | ".join(parts), flush=True)
    s = report["summary"]
    print("verdicts (best other vs gp):", json.dumps(s["verdicts"]), flush=True)
    print("best tuned vs gp:", json.dumps(s["verdicts_best_tuned"]), flush=True)
    print("ceiling:", json.dumps({p: {k: c[k] for k in ("cells", "any_tuned_better", "any_better", "cells_gp_best")} for p, c in s["ceiling"].items()}),
          flush=True)
    if "hybrid" in report:
        h = report["hybrid"]
        for e in h["columns"]:
            iv = e["intervals"]["mlp_gp"]["median"]
            print(f"N-108 {e['table']} {e['column']}: {e['holds']}; " + " | ".join(
                f"{p} gp {_pct(pp['gp']['p90_rel'])} mlp {_pct((pp['mlp'] or {}).get('p90_median'))} hybrid {_pct((pp['mlp_gp'] or {}).get('p90_median'))}"
                for p, pp in e["protocols"].items()) + f" | hybrid k_scale {iv.get('k_scale')} cov(ii) cal {iv.get('coverage_calibrated_ii')}", flush=True)
        print("N-108 counts:", json.dumps(h["counts"]), json.dumps(h["counts_total"]), flush=True)
    for v, e in s["cost"].items():
        print(f"cost {v}: {e['jobs']} jobs, {e['fits']} fits, fit {e['fit_s']:.0f} s (tune {e['tune_s']:.0f} s), predict {e['predict_s']:.1f} s, "
              f"per job median / p90 / max {e['fit_s_per_job']['median']} / {e['fit_s_per_job']['p90']} / {e['fit_s_per_job']['max']}",
              flush=True)


# -- report -------------------------------------------------------------------------------------------------------------------

VERDICT_CN = {"other_better": "对手更好", "gp_better": "GP 更好", "tie": "持平", "no_other": "无可比变体", "not_comparable": "不可比"}
PROTOCOL_CN = {"i": "(i) 随机留出 5×20%", "ii": "(ii) 整档留出外径"}
FAMILY_CN = {"A": "A 核方法与近邻", "B": "B 树模型", "C": "C 神经网络", "H": "H MLP+GP 残差"}
# the reference categorical palette's first four slots in their validated order: GP, then the families A, B, C; the hybrids (H,
# N-108) take B's aqua -- B and H never share a figure, and blue / yellow / aqua pass the validator on all pairs
SERIES = {"gp": "#2a78d6", "A": "#eb6834", "B": "#1baf7a", "C": "#eda100", "H": "#1baf7a"}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def figure(report: dict, path: Path) -> None:
    """p90 relative error per column: the GP and the best comparable variant of each family, protocols (i) and (ii) side by
    side, one panel per table; each family bar is labelled with its variant."""
    plt = n104._plt()
    names = list(report["tables"])
    fams = [f for f in FAMILIES if any(e["family"] == f for v, e in report["variants"].items() if v != "gp")]
    series = ["gp", *fams]
    nrows = len(names)
    fig, axes = plt.subplots(nrows, 1, figsize=(15.5, 3.5 * nrows + 0.9), squeeze=False)
    width, gap = 0.085, 0.07
    slots = [(p, s) for p in ("i", "ii") for s in series]
    half = len(series)
    offsets = {ps: (k - (len(slots) - 1) / 2) * width + (gap / 2 if k >= half else -gap / 2) for k, ps in enumerate(slots)}
    for ax, t in zip(axes.flat, names):
        te = report["tables"][t]
        cols = list(te["columns"])
        for i, c in enumerate(cols):
            pes = te["columns"][c].get("protocols", {})
            for p in ("i", "ii"):
                pe = pes.get(p)
                mid = i + sum(offsets[(p, s)] for s in series) / len(series)
                if pe is None or not pe["variants"].get("gp", {}).get("n_scored"):
                    ax.text(mid, 0.012, "无档", ha="center", va="bottom", fontsize=6.5, color=MUTED, transform=ax.get_xaxis_transform())
                    continue
                hatch = "////" if p == "ii" else None
                g = pe["variants"]["gp"]["p90_rel"] * 100
                ax.bar(i + offsets[(p, "gp")], g, width, color=SERIES["gp"], edgecolor="white", linewidth=0.8, hatch=hatch, zorder=2)
                for fam in fams:
                    fb = pe.get("best_by_family", {}).get(fam)
                    x0 = i + offsets[(p, fam)]
                    if fb is None:
                        ax.text(x0, g, "×", ha="center", va="bottom", fontsize=7, color=MUTED)
                        continue
                    r = pe["variants"][fb["variant"]]["p90_rel"] * 100
                    if not math.isfinite(r):
                        ax.text(x0, g, "∞", ha="center", va="bottom", fontsize=7, color=MUTED)
                        continue
                    ax.bar(x0, r, width, color=SERIES[fam], edgecolor="white", linewidth=0.8, hatch=hatch, zorder=2)
                    ax.text(x0, r * 1.08, fb["variant"], ha="center", va="bottom", fontsize=6, color=MUTED, rotation=90)
        ax.set_yscale("log")
        lo, hi = ax.get_ylim()
        ax.set_ylim(lo, hi * 3.0)
        ticks = [v for v in (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000) if lo <= v <= hi * 3.0]
        ax.set_yticks(ticks)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _pos: f"{v:g}"))
        ax.yaxis.set_minor_formatter(plt.NullFormatter())
        ax.set_xticks(range(len(cols)), cols, fontsize=8.5)
        ax.set_ylabel("p90 相对误差（%，对数）", fontsize=8.5, color=INK)
        ax.set_title(f"{t}（{te['rows']} 行）", fontsize=10, color=INK)
        ax.grid(axis="y", color=GRID, lw=0.7, zorder=0)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.tick_params(colors=MUTED, labelcolor=INK)
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    members = {f: " / ".join(v for v, e in report["variants"].items() if e["family"] == f and v != "gp") for f in fams}
    handles = [Patch(facecolor=SERIES["gp"], edgecolor="white", label="库的 GP")]
    handles += [Patch(facecolor=SERIES[f], edgecolor="white", label=f"{FAMILY_CN[f]}中最好的（{members[f]}）") for f in fams]
    handles += [Patch(facecolor="#9a9994", edgecolor="white", label="(i) 随机留出 5×20%"),
                Patch(facecolor="#9a9994", edgecolor="white", hatch="////", label="(ii) 整档留出外径"),
                Line2D([], [], color=MUTED, marker="x", linestyle="none", label="该族没有可比的变体")]
    fig.legend(handles=handles, loc="upper center", ncol=4, fontsize=8.5, frameon=False)
    fig.tight_layout(rect=(0, 0, 1, 1 - 0.75 / fig.get_figheight()))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _cnt(d: dict | None) -> str:
    d = d or {}
    out = " / ".join(f"{d.get(k, 0)}" for k in ("other_better", "tie", "gp_better"))
    extra = [f"{VERDICT_CN[k]} {d[k]}" for k in ("no_other", "not_comparable") if d.get(k)]
    return out + (f"（{'，'.join(extra)}）" if extra else "")


def _sec(x: float | None) -> str:
    return "—" if x is None else f"{x:.2f}"


def _cell(e: dict | None) -> str:
    if e is None:
        return "—"
    return f"{_pct(e['median_rel'])} / {_pct(e['p90_rel'])} / {_pct(e['max_rel'])}"


def markdown(report: dict, fig_rel: str | None, fig2_rel: str | None = None) -> str:
    s = report["summary"]
    tabs = report["tables"]
    variants = list(report["variants"])
    others = [v for v in variants if v != "gp"]
    L = []
    if "hybrid" in report:
        L.append("# N-108 结果：调过参的 MLP 作均值、库的 GP 拟合它的残差——能否既保住 MLP 的精度、又给出 GP 的校准区间（第二代库）\n")
        L.append(f"规格：`{report['spec']}`。量点误差、区间覆盖与种子稳定性；不跑 EMX、不改产品代码、库只读"
                 f"（`cache_dir` = `{report['scratch']}`）。折与 N-104 / N-107 相同（协议 (ii) 的档读自 N-104 的 JSON）。数字全部来自脚本写出的 JSON。\n")
    else:
        L.append("# N-107 结果：PACOSYT 比较过的其他回归器（各自内层交叉验证调参）接在库的模型接口后，对照库的 GP（第二代库）\n")
        L.append(f"规格：`{report['spec']}`。只量点误差（不做区间、不做校准）；不跑 EMX、不改产品代码、库只读"
                 f"（`cache_dir` = `{report['scratch']}`）。折与 N-104 相同（协议 (ii) 的档读自 N-104 的 JSON）。数字全部来自脚本写出的 JSON。\n")
    L.append("## 跑了什么\n")
    n_cols = sum(len(te["columns"]) for te in tabs.values())
    folds = {p: sum(pe["variants"]["gp"]["folds"] for te in tabs.values() for ce in te["columns"].values()
                    for q, pe in ce.get("protocols", {}).items() if q == p and "gp" in pe["variants"]) for p in ("i", "ii")}
    L.append(f"- 库 `{report['library']}`；{len(tabs)} 张表、{n_cols} 个结果列；种子 {report['seeds']}，留出 20%；GP 变体的折数：(i) {folds['i']}、(ii) {folds['ii']}。")
    j = report["jobs"]
    L.append(f"- 作业 {j['planned']} 个（表 × 列 × 折 × 变体）：GP 作业 {j['gp_planned']} 个中 {j['from_gp_cache']} 个读自 N-104 的作业缓存"
             f"（`{report['gp_cache']}`，只读）、本次拟合 {j['gp_fitted_here']} 个；其余作业 {j['from_cache']} 个取自本研究的作业缓存、"
             f"本次计算 {j['run']} 个；未拟合 {j['unfit']} 个，出错 {j['errors']} 个。")
    if j.get("copied"):
        L.append("- 其中从别的研究的作业缓存复制进本研究缓存（源只读）的作业：" + "；".join(f"`{d}` {n} 个" for d, n in j["copied"].items()) + "。")
    vt = report.get("variant_threads") or {}
    threads = f"{report['threads']} 个线程" + (f"（例外：{'，'.join(f'`{k}` {n} 个' for k, n in vt.items())}）" if vt else "")
    if j["run"] == 0 and report.get("cached_only"):
        L.append(f"- 本 JSON 由 `--cached-only` 从作业缓存汇总（{report['date_start']}，不拟合任何模型）；拟合发生在此前的各轮运行里，"
                 f"每个作业的线程数：{threads}；各轮的进程数、起止时间见 `run.log` 与文末的运行记录。")
    else:
        L.append(f"- 并行：{report['workers']} 个 spawn 工作进程 × {threads}（OMP_NUM_THREADS={report['omp_num_threads']}）；"
                 f"开始 {report['date_start']}，结束 {report['date_end']}，总用时 {report['wall_seconds'] / 60:.1f} 分钟（进程池 {report['pool_seconds'] / 60:.1f} 分钟）。")
    v = report["versions"]
    L.append(f"- 版本：源码 `{report['source']}`，Python {v['python']}，numpy {v['numpy']}，scipy {v['scipy']}，scikit-learn {v['sklearn']}（{v['all']}）。")
    L.append(f"- 命令：`{' '.join(report['argv'])}`")
    L.append("- 模块：" + "；".join(f"`{m}`" for m in report["modules"]) + "\n")
    L.append("变体（每个都用库的输入——特征映射、有匝数维的表按匝数分档、最小-最大缩放、去掉训练行上恒定的输入——与库的目标变换；"
             "调参 = 只在该折训练行上做 3 折内层交叉验证，拟合空间的 RMSE 最低者胜，并列取网格中的第一个）：\n")
    L.append("| 变体 | 族 | 类 | 调参 | 网格 | 说明 |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    seen_grids: dict[str, str] = {}
    for name, e in report["variants"].items():
        axes = e.get("grid_axes") or {}
        grid = "—" if not axes else "；".join(f"`{k}` ∈ {{{', '.join(str(x) for x in vals)}}}" for k, vals in axes.items())
        if axes:
            grid += f"（{e.get('n_configs')} 个配置）"
        if e.get("grid_cut"):
            grid += f"；规格网格中未跑的 {len(e['grid_cut'])} 个：" + "，".join(f"`{json.dumps(c, sort_keys=True)}`" for c in e["grid_cut"])
        if axes and grid in seen_grids:                 # the seeds of one model share a grid: name its first variant
            grid = f"同 `{seen_grids[grid]}`"
        elif axes:
            seen_grids[grid] = name
        L.append(f"| `{name}` | {e['family']} | `{e['model']}` | {'是' if e['tuned'] else '否'} | {grid} | {e['note']} |")
    L.append("")
    L.append("各表的外径档（N-104 选定的，读自其 JSON）：\n")
    L.append("| 表 | 行 | 外径维 | 合格档（各列并集） | 选用的档 |")
    L.append("| --- | ---: | --- | --- | --- |")
    for t, te in tabs.items():
        L.append(f"| {t} | {te['rows']} | `{te['level_dim']}` | {', '.join(f'{x:g}' for x in te['eligible_levels']) or '—'} | "
                 f"{', '.join(f'{x:g}' for x in te['levels']) or '—'} |")
    L.append("")
    if "hybrid" in report:
        L.append(hybrid_markdown(report, fig2_rel))
        L.append("以下各节是 N-107 的口径：每个变体（含各个种子）单独对 GP 判定（`gp_refit` 是参照，不作对手）。\n")
    L.append("## 上限问题：调过参的回归器能否胜过 GP\n")
    L.append("判定规则（N-104 的）：某变体的 p90 比 GP 低出 GP 的 10% 以上、且中位数不高于 GP，记“对手更好”；角色互换记“GP 更好”；否则持平。"
             "只有拟合了每一折、且与 GP 打分的行完全相同的变体可比。“任一调参回归器胜过 GP”= 该格里至少一个调参变体单独对 GP 判为“对手更好”"
             "（调参变体：" + "、".join(f"`{x}`" for x in others if report["variants"][x]["tuned"]) + "）。注意从多个变体里挑，对对手偏乐观。\n")
    L.append("| 协议 | 格数 | 任一调参回归器胜过 GP | 任一变体（含不调参的）胜过 GP | 所有可比变体中 GP 的 p90 最低 |")
    L.append("| --- | ---: | ---: | ---: | ---: |")
    for p in ("i", "ii"):
        c = s["ceiling"][p]
        L.append(f"| {PROTOCOL_CN[p]} | {c['cells']} | {c['any_tuned_better']} | {c['any_better']} | {c['cells_gp_best']} |")
    L.append("")
    for p in ("i", "ii"):
        cells = s["ceiling"][p]["cells_any_tuned_better"]
        if cells:
            L.append(f"{PROTOCOL_CN[p]} 中调参回归器胜过 GP 的格：" + "；".join(f"{x['table']} {x['column']}（{', '.join(x['variants'])}）" for x in cells) + "。\n")
    L.append("哪个变体最好、多少次（每格按 p90 取最低者；左：只在非 GP 变体中取；右：连 GP 一起取）：\n")
    L.append("| 变体 | (i) 非 GP 中最好 | (ii) 非 GP 中最好 | (i) 全体中最好 | (ii) 全体中最好 |")
    L.append("| --- | ---: | ---: | ---: | ---: |")
    for name in [*variants, "none"]:
        row = [s["best_other_counts"]["i"].get(name, 0), s["best_other_counts"]["ii"].get(name, 0),
               s["best_overall_counts"]["i"].get(name, 0), s["best_overall_counts"]["ii"].get(name, 0)]
        if any(row):
            label = "（无可比变体）" if name == "none" else f"`{name}`"
            L.append(f"| {label} | " + " | ".join(str(x) for x in row) + " |")
    L.append("")
    L.append("## 判定计数（对手更好 / 持平 / GP 更好）\n")
    L.append("| 比较 | (i) | (ii) |")
    L.append("| --- | --- | --- |")
    L.append(f"| 最好的非 GP 变体 | {_cnt(s['verdicts']['i'])} | {_cnt(s['verdicts']['ii'])} |")
    L.append(f"| 最好的调参变体 | {_cnt(s['verdicts_best_tuned']['i'])} | {_cnt(s['verdicts_best_tuned']['ii'])} |")
    for fam in FAMILIES:
        if fam in s["verdicts_by_family"] and any(s["verdicts_by_family"][fam][p] for p in ("i", "ii")):
            d = s["verdicts_by_family"][fam]
            if d["i"].get("no_other", 0) + d["ii"].get("no_other", 0) == sum(d["i"].values()) + sum(d["ii"].values()):
                continue
            L.append(f"| {FAMILY_CN[fam]}族中最好的 | {_cnt(d['i'])} | {_cnt(d['ii'])} |")
    for name in others:
        d = s["verdicts_by_variant"].get(name)
        if d and not _reference(name):
            L.append(f"| `{name}` 单独 | {_cnt(d['i'])} | {_cnt(d['ii'])} |")
    L.append("")
    L.append("按表（最好的非 GP 变体）：\n")
    L.append("| 表 | (i) | (ii) |")
    L.append("| --- | --- | --- |")
    for t in tabs:
        L.append(f"| {t} | {_cnt(s['verdicts_by_table'][t]['i'])} | {_cnt(s['verdicts_by_table'][t]['ii'])} |")
    L.append("")
    if fig_rel:
        L.append(f"![p90 相对误差]({fig_rel})\n")
    L.append("## 各表各列\n")
    L.append("每格：中位数 / p90 / 最大相对误差。“最好的对手”= 可比的非 GP 变体中 p90 最低者。右栏是每个变体的 p90"
             "（“未拟合 k/n”：k 折未拟合；“错 k”：k 折出错；`*`：打分行与 GP 不同，不可比）。\n")
    for t, te in tabs.items():
        L.append(f"### {t}（{te['rows']} 行）\n")
        L.append(f"| 结果列 | 建法 | 协议 | 打分行 | GP | 最好的对手 | 判定 | 各变体的 p90（{' / '.join(others)}） |")
        L.append("| --- | --- | --- | ---: | --- | --- | --- | --- |")
        for c, ce in te["columns"].items():
            kind = ce["kind"] + (f"，{ce['settings']['nt_mode']}" if ce["kind"] == "direct" else "") + (
                f"，`{ce['settings']['feature_map']}`" if ce["settings"]["feature_map"] else "")
            for p in ("i", "ii"):
                pe = ce.get("protocols", {}).get(p)
                if pe is None:
                    L.append(f"| {c} | {kind} | {p} | 0 | 无合格档 | — | — | — |")
                    continue
                g = pe["variants"].get("gp")
                best = pe.get("best_other")
                bcell = "—" if not best else f"`{best}` {_cell(pe['variants'][best])}"
                each = []
                for name in others:
                    e = pe["variants"].get(name)
                    if e is None:
                        each.append("—")
                    elif e.get("error_folds"):
                        each.append(f"错 {len(e['error_folds'])}")
                    elif not e["complete"]:
                        each.append(f"未拟合 {len(e['unfit_folds'])}/{e['folds']}")
                    else:
                        each.append(_pct(e["p90_rel"]) + ("" if e.get("same_rows_as_gp", True) else "*"))
                n = g["n_scored"] if g else 0
                L.append(f"| {c} | {kind} | {p} | {n} | {_cell(g)} | {bcell} | {VERDICT_CN.get(pe.get('verdict'), '—')} | {' / '.join(each)} |")
        L.append("")
    L.append("## 差别最大的结果列（最好的对手的 p90 ÷ GP 的 p90）\n")
    L.append("| 方向 | 表 | 结果列 | 协议 | 变体 | GP 中位 / p90 / 最大 | 对手 中位 / p90 / 最大 | 比值 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | ---: |")
    ratios = s["p90_ratios"]
    picks = [("对手低", r) for r in ratios[:5]] + [("对手高", r) for r in reversed(ratios[-5:]) if r not in ratios[:5]]
    for label, r in picks:
        L.append(f"| {label} | {r['table']} | {r['column']} | {r['protocol']} | `{r['best_other']}` | {_cell(r['gp'])} | {_cell(r['other'])} | {r['ratio']:.2f} |")
    L.append("")
    L.append(f"## 对手的最大误差超过 GP 的 {n104.OVERSHOOT:g} 倍\n")
    if s["overshoot"]:
        n_best = sum(o["best_other"] for o in s["overshoot"])
        L.append(f"共 {len(s['overshoot'])} 格·变体，其中 {n_best} 个是该格最好的对手。按变体计数："
                 + "，".join(f"`{k}` {n}" for k, n in Counter(o["variant"] for o in s["overshoot"]).most_common()) + "。最好的对手的那些：\n")
        L.append("| 表 | 结果列 | 协议 | 变体 | GP 最大 | 对手最大 | 倍数 | GP p90 | 对手 p90 |")
        L.append("| --- | --- | --- | --- | --- | --- | ---: | --- | --- |")
        for o in sorted((o for o in s["overshoot"] if o["best_other"]), key=lambda o: (o["table"], o["column"], o["protocol"])):
            factor = "∞" if math.isinf(o["factor"]) else f"{o['factor']:.1f}"
            L.append(f"| {o['table']} | {o['column']} | {o['protocol']} | `{o['variant']}` | {_pct(o['gp_max_rel'])} | {_pct(o['other_max_rel'])} | "
                     f"{factor} | {_pct(o['gp_p90_rel'])} | {_pct(o['other_p90_rel'])} |")
    else:
        L.append("没有。")
    L.append("")
    L.append("## 未拟合与出错的折\n")
    if s["unfit"] or s["errors"]:
        grouped = {}
        for kind, items in (("未拟合", s["unfit"]), ("出错", s["errors"])):
            for u in items:
                grouped.setdefault((kind, u["table"], u["variant"]), []).append(u)
        L.append("| 类别 | 表 | 变体 | 折数 | 涉及的结果列 | 例：原因 |")
        L.append("| --- | --- | --- | ---: | --- | --- |")
        for (kind, t, name), us in grouped.items():
            L.append(f"| {kind} | {t} | `{name}` | {len(us)} | {', '.join(sorted({u['column'] for u in us}))} | {us[0]['reason'][:160]} |")
    else:
        L.append("没有。")
    L.append("")
    L.append("## 调参选中的配置\n")
    L.append("每次内层交叉验证（每个折的每个子模型：按匝数分档的每档、组合列的每个部件各一次）选中的配置的次数；“贴边”= 选中值落在该数值轴的"
             "最小 / 最大值上的次数（贴边多说明网格可能不够宽）。\n")
    for name, tu in s["tuning"].items():
        top = list(tu["chosen"].items())[:8]
        edge = "；".join(f"`{k}` 低端 {d.get('low', 0)} / 中间 {d.get('inside', 0)} / 高端 {d.get('high', 0)}" for k, d in tu["edge"].items())
        L.append(f"- `{name}`：{tu['calls']} 次；最常选中：" + "，".join(f"`{cfg}` {n}" for cfg, n in top) + (f"。贴边：{edge}" if edge else "") + "。")
    L.append("")
    L.append("## 每种模型的代价\n")
    L.append("拟合时间 = 一个作业里所有模型拟合的墙钟时间（含内层交叉验证；组合列含 2–3 个部件；按匝数分档含各档的子模型）；预测 = 对留出行的一次预测。"
             "GP 的时间是 N-104 运行时记下的（48 个作业并发）。\n")
    L.append("| 变体 | 作业 | 模型拟合 | 拟合合计 (s) | 其中内层交叉验证 (s) | 每作业拟合 中位 / p90 / 最大 (s) | 预测合计 (s) |")
    L.append("| --- | ---: | ---: | ---: | ---: | --- | ---: |")
    for name, e in s["cost"].items():
        f = e["fit_s_per_job"]
        L.append(f"| `{name}` | {e['jobs']} | {e['fits']} | {e['fit_s']:.0f} | {e['tune_s']:.0f} | {_sec(f['median'])} / {_sec(f['p90'])} / {_sec(f['max'])} | "
                 f"{e['predict_s']:.1f} |")
    L.append("")
    L.append("按表的拟合合计（s）：\n")
    L.append("| 表 | " + " | ".join(f"`{x}`" for x in s["cost"]) + " |")
    L.append("| --- |" + " ---: |" * len(s["cost"]))
    for t in tabs:
        L.append(f"| {t} | " + " | ".join(f"{s['cost'][x]['per_table'][t]['fit_s']:.0f}" for x in s["cost"]) + " |")
    L.append("")
    L.append("## 核对：GP 变体的协议 (i) 与库自己缓存的校准\n")
    L.append("库的校准就是同一协议（种子 0–4）；打分行数相等、中位数的相对差不超过 1e-4 记“一致”（N-104 的口径）。\n")
    L.append("| 表 | 结果列 | 本研究 GP (i) 中位 / 打分行 | 库缓存的中位 / 打分行 | 中位的相对差 | 一致 |")
    L.append("| --- | --- | --- | --- | ---: | --- |")
    for t, te in tabs.items():
        for c, ce in te["columns"].items():
            cal = ce.get("library_calibration")
            pe = ce.get("protocols", {}).get("i")
            if pe is None or "gp" not in pe["variants"]:
                continue
            g = pe["variants"]["gp"]
            if cal is None or cal.get("median_rel") is None:
                L.append(f"| {t} | {c} | {_pct(g['median_rel'])} / {g['n_scored']} | 缓存里没有 | — | — |")
                continue
            rel_diff = abs(cal["median_rel"] - g["median_rel"]) / abs(cal["median_rel"])
            same = rel_diff <= 1e-4 and cal["n_scored"] == g["n_scored"]
            L.append(f"| {t} | {c} | {g['median_rel'] * 100:.4f}% / {g['n_scored']} | {cal['median_rel'] * 100:.4f}% / {cal['n_scored']} | "
                     f"{rel_diff:.1e} | {'是' if same else '否'} |")
    L.append("")
    return "\n".join(L) + "\n"


# -- the N-108 report -----------------------------------------------------------------------------------------------------------

HOLDS_CN = {"holds": "成立", "fails": "不成立", "no_level": "(ii) 无档，只判 (i)"}
VS_CN = {"other_better": "混合更好", "tie": "持平", "gp_better": "参照更好"}


def _r(x: float | None, nd: int = 2) -> str:
    return "—" if x is None else "∞" if math.isinf(x) else f"{x:.{nd}f}"


def _vs(d: dict | None) -> str:
    """Per-seed verdict counts: hybrid better / tie / reference better."""
    d = d or {}
    return f"{d.get('other_better', 0)}/{d.get('tie', 0)}/{d.get('gp_better', 0)}"


def _fail_text(e: dict) -> str:
    c = e["conditions"]
    out = []
    for p in ("i", "ii"):
        a = c["a"].get(p)
        if a and a["ok"] is False:
            out.append(f"(a) {p}：p90 {_pct(a['mlp_gp_p90'])} 是较好参照 {_pct(a['best_reference_p90'])} 的 {_r(a['ratio_vs_best'])} 倍")
    b = c["b"]
    if b["coverage_calibrated_ii_ok"] is False:
        out.append(f"(b) 校准后 (ii) 覆盖 {_r(b['coverage_calibrated_ii'], 3)} < {HOLD['coverage_ii_min']:g}")
    if b["coverage_before_i_ok"] is False:
        out.append(f"(b) 校准前 (i) 覆盖 {_r(b['coverage_before_i'], 3)} 低于 GP 的 {_r(b['gp_coverage_before_i'], 3)} 超过 {HOLD['coverage_i_gap']:g}")
    for p in ("i", "ii"):
        cc = c["c"].get(p)
        if cc and cc["ok"] is False:
            out.append(f"(c) {p}：种子间 p90 极差比 {_r(cc['p90_spread'])} ≥ {HOLD['spread_below']:g}")
    return "；".join(out) or "—"


def hybrid_markdown(report: dict, fig2_rel: str | None) -> str:
    """The N-108 sections of RESULT_CN.md: the verdict per column and its counts, point errors and seed spreads, intervals,
    the largest differences, the checks."""
    h = report["hybrid"]
    groups = h["seed_groups"]
    rule = h["rule"]
    L = []
    L.append("## N-108 结论：混合模型在哪些结果列上成立\n")
    L.append(f"混合 = `{'`、`'.join(groups['mlp_gp'])}`（MLP 的随机种子 0–4，残差 GP 的种子固定 0）；MLP 单独 = `{'`、`'.join(groups['mlp'])}`"
             "（`mlp` 即 N-107 的种子 0，取自其作业缓存）。凡说“种子中位”都是这五个种子各自的统计量再取中位数。"
             f"GP 的点误差取自 N-104 缓存的 `gp` 折；它的区间来自 `{h['interval_reference']}`——同样的折上重拟合库的 GP"
             "（N-104 缓存的 GP 折只存了相对误差，没有 sigma）。\n")
    L.append(f"成立 = 三条同时满足：(a) 两种协议上混合的 p90（种子中位）都不高于 GP 与 MLP（种子中位）中较好者的 {1 + rule['p90_above_best']:.2f} 倍；"
             f"(b) 用协议 (i) 定出的 `k_scale` 校准后，协议 (ii) 的 2σ 覆盖（种子中位）≥ {rule['coverage_ii_min']:g}，且校准前协议 (i) 的覆盖"
             f"（种子中位）不比 GP 的低 {rule['coverage_i_gap']:g} 以上；(c) 两种协议上五个种子 p90 的最大/最小 < {rule['spread_below']:g}。"
             "没有合格外径档的列（(ii) 无档）只能判 (i) 的部分，单独计数。\n")
    L.append("| 表 | 成立 | 不成立 | (ii) 无档，只判 (i)（(i) 部分都满足） |")
    L.append("| --- | ---: | ---: | ---: |")
    for t, n in h["counts"].items():
        L.append(f"| {t} | {n.get('holds', 0)} | {n.get('fails', 0)} | {n.get('no_level', 0)} |")
    tot = h["counts_total"]
    L.append(f"| 合计 | {tot.get('holds', 0)} | {tot.get('fails', 0)} | {tot.get('no_level', 0)} |")
    L.append("")
    L.append("| 表 | 结果列 | (a) p90 ÷ 较好参照 (i) / (ii) | (b) 校准后 (ii) 覆盖 | (b) 校准前 (i) 覆盖：混合 / GP | (c) p90 种子极差比 (i) / (ii) | 结论 | 不成立的原因 |")
    L.append("| --- | --- | --- | ---: | --- | --- | --- | --- |")
    for e in h["columns"]:
        c = e["conditions"]
        a = " / ".join(_r(c["a"][p]["ratio_vs_best"]) if c["a"].get(p) else "—" for p in ("i", "ii"))
        cc = " / ".join(_r(c["c"][p]["p90_spread"]) if c["c"].get(p) else "—" for p in ("i", "ii"))
        b = c["b"]
        L.append(f"| {e['table']} | {e['column']} | {a} | {_r(b['coverage_calibrated_ii'], 3)} | {_r(b['coverage_before_i'], 3)} / "
                 f"{_r(b['gp_coverage_before_i'], 3)} | {cc} | {HOLDS_CN[e['holds']]} | {_fail_text(e) if e['holds'] == 'fails' else '—'} |")
    L.append("")
    if fig2_rel:
        L.append(f"![N-108：p90 与校准后 (ii) 覆盖]({fig2_rel})\n")
    L.append("## N-108 点误差与种子稳定性\n")
    L.append("每格：中位数 / p90 / 最大相对误差；MLP 与混合取种子中位，方括号里是五个种子 p90 的最大/最小。判定用 N-104 的规则"
             "（p90 低出参照的 10% 以上且中位数不高于参照）：先对种子中位判一次，再逐种子判（混合更好/持平/参照更好的种子数；"
             "对 MLP 时混合的第 k 个种子对 MLP 的第 k 个种子）。\n")
    L.append("| 表 | 结果列 | 协议 | 打分行 | GP | MLP（种子中位）[极差比] | 混合（种子中位）[极差比] | 对 GP：种子中位判定（逐种子） | 对 MLP：种子中位判定（逐种子） |")
    L.append("| --- | --- | --- | ---: | --- | --- | --- | --- | --- |")
    for e in h["columns"]:
        for p in ("i", "ii"):
            pp = e["protocols"].get(p)
            if pp is None:
                L.append(f"| {e['table']} | {e['column']} | {p} | 0 | 无合格档 | — | — | — | — |")
                continue
            g, m, hy = pp["gp"], pp["mlp"], pp["mlp_gp"]
            mcell = "—" if not m else f"{_pct(m['median_median'])} / {_pct(m['p90_median'])} / {_pct(m['max_median'])} [{_r(m['p90_spread'])}]"
            hcell = "—" if not hy else f"{_pct(hy['median_median'])} / {_pct(hy['p90_median'])} / {_pct(hy['max_median'])} [{_r(hy['p90_spread'])}]"
            vg = f"{VS_CN.get(pp.get('verdict_vs_gp'), '—')}（{_vs(pp.get('per_seed_vs_gp'))}）"
            vm = f"{VS_CN.get(pp.get('verdict_vs_mlp'), '—')}（{_vs(pp.get('per_seed_vs_mlp'))}）" if "verdict_vs_mlp" in pp else "—"
            L.append(f"| {e['table']} | {e['column']} | {p} | {g['n_scored']} | {_cell(g)} | {mcell} | {hcell} | {vg} | {vm} |")
    L.append("")
    L.append("## N-108 区间：校准前后的 2σ 覆盖、`k_scale` 与宽度\n")
    L.append("`k_scale` = 协议 (i) 五个种子合并的 |z| 的 0.95 分位数 ÷ 2，至少 1（`gp.calibration_scale`，即 `Library._calibrate` 的规则）；"
             "校准后的区间 = `gp.prediction_bounds(mu, sigma, k = 2·k_scale)`（对数目标在对数空间里精确）。宽度 = 校准后的相对 sigma "
             "`k_scale·σ/μ` 的中位数（规格的口径；2σ 区间的相对半宽约是它的两倍）。sigma 一律不加下限（库查询时还有 "
             "σ ≥ 中位相对误差·μ 的下限，加了下限的覆盖见 JSON 的 `coverage_calibrated_floored_*`）。混合取种子中位，`k_scale` 另列五个种子的范围。\n")
    L.append("| 表 | 结果列 | GP：k_scale | GP：校准前覆盖 (i) / (ii) | GP：校准后覆盖 (i) / (ii) | GP：宽度 (ii) | 混合：k_scale 中位（范围） | 混合：校准前覆盖 (i) / (ii) | 混合：校准后覆盖 (i) / (ii) | 混合：宽度 (ii) |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for e in h["columns"]:
        gi = e["intervals"]["gp"] or {}
        hm = e["intervals"]["mlp_gp"]["median"]
        ks = [b["k_scale"] for b in e["intervals"]["mlp_gp"]["per_seed"].values() if b]
        krange = f"{_r(hm.get('k_scale'))}（{_r(min(ks))}–{_r(max(ks))}）" if ks else "—"
        def two(d: dict, k: str) -> str:
            return f"{_r(d.get(k + '_i'), 3)} / {_r(d.get(k + '_ii'), 3)}"
        L.append(f"| {e['table']} | {e['column']} | {_r(gi.get('k_scale'))} | {two(gi, 'coverage_before')} | {two(gi, 'coverage_calibrated')} | "
                 f"{_pct(gi.get('rel_sigma_cal_median_ii'))} | {krange} | {two(hm, 'coverage_before')} | {two(hm, 'coverage_calibrated')} | "
                 f"{_pct(hm.get('rel_sigma_cal_median_ii'))} |")
    L.append("")
    L.append("## N-108 差别最大的格（混合的 p90 种子中位 ÷ 参照的 p90）\n")
    ratios = h["p90_ratios"]
    L.append("| 方向 | 表 | 结果列 | 协议 | GP 中位 / p90 / 最大 | MLP p90（种子中位） | 混合 中位 / p90 / 最大（种子中位） | ÷ GP | ÷ MLP |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | ---: | ---: |")
    by_mlp = sorted((r for r in ratios if r["ratio_vs_mlp"] is not None), key=lambda r: r["ratio_vs_mlp"])
    picks = ([("对 GP 最低", r) for r in ratios[:5]] + [("对 GP 最高", r) for r in reversed(ratios[-5:])]
             + [("对 MLP 最低", r) for r in by_mlp[:5]] + [("对 MLP 最高", r) for r in reversed(by_mlp[-5:])])
    for label, r in picks:
        hcell = f"{_pct(r['mlp_gp_median_median'])} / {_pct(r['mlp_gp_p90_median'])} / {_pct(r['mlp_gp_max_median'])}"
        L.append(f"| {label} | {r['table']} | {r['column']} | {r['protocol']} | {_cell(r['gp'])} | {_pct(r['mlp_p90_median'])} | {hcell} | "
                 f"{_r(r['ratio_vs_gp'])} | {_r(r['ratio_vs_mlp'])} |")
    L.append("")
    L.append("## N-108 核对\n")
    ref = h["interval_reference"]
    for e in h["columns"]:
        chk = e.get("checks", {})
        parts = []
        rv = chk.get(f"{ref}_vs_gp", {})
        for p, d in rv.items():
            parts.append(f"`{ref}` 对 `gp` ({p})：打分行{'相同' if d['same_rows'] else '不同'}，相对误差最大绝对差 {d['max_abs_diff_rel']:.1e}"
                         if d["max_abs_diff_rel"] is not None else f"`{ref}` 对 `gp` ({p})：无可比行")
        s0 = chk.get(f"{groups['mlp_gp'][0]}_mlp_is_mlp", {})
        for p, d in s0.items():
            parts.append(f"`{groups['mlp_gp'][0]}` 的 MLP 与 `mlp` ({p})：{d['identical']}/{d['folds']} 折逐位相同")
        lib = report["tables"][e["table"]]["columns"][e["column"]].get("library_calibration_full")
        gi = e["intervals"]["gp"] or {}
        if lib and gi:
            parts.append(f"库缓存的校准 k_scale {_r(lib.get('k_scale'), 4)}、校准前覆盖 {_r(lib.get('coverage_2sigma_before'), 4)}；"
                         f"`{ref}` 的 {_r(gi.get('k_scale'), 4)}、{_r(gi.get('coverage_before_i'), 4)}")
        L.append(f"- {e['table']} {e['column']}：" + "；".join(parts) + "。")
    L.append("")
    return "\n".join(L) + "\n"


def figure_hybrid(report: dict, path: Path) -> None:
    """N-108: per table, left the p90 of gp, mlp and the hybrid (seed medians, whiskers over the five seeds) on both protocols,
    right the calibrated coverage of protocol (ii) of gp and the hybrid (seed median, whiskers over the seeds) against the
    specification's 0.90 and the nominal 0.95."""
    plt = n104._plt()
    h = report["hybrid"]
    tables = list(dict.fromkeys(e["table"] for e in h["columns"]))
    series = [("gp", "库的 GP", SERIES["gp"]), ("mlp", "MLP（五个种子的中位）", SERIES["C"]), ("mlp_gp", "MLP + GP 残差（五个种子的中位）", SERIES["H"])]
    fig, axes = plt.subplots(len(tables), 2, figsize=(15.5, 3.3 * len(tables) + 1.0), squeeze=False,
                             gridspec_kw={"width_ratios": [2.3, 1.0]})
    width = 0.13
    for row, t in zip(axes, tables):
        ax, axc = row
        cols = [e for e in h["columns"] if e["table"] == t]
        for i, e in enumerate(cols):
            for j, p in enumerate(("i", "ii")):
                pp = e["protocols"].get(p)
                base = i + (j - 0.5) * 0.42
                if pp is None:
                    ax.text(base, 0.012, "无档", ha="center", va="bottom", fontsize=6.5, color=MUTED, transform=ax.get_xaxis_transform())
                    continue
                for k, (key, _label, color) in enumerate(series):
                    x0 = base + (k - 1) * width
                    if key == "gp":
                        val, lo, hi = pp["gp"]["p90_rel"], None, None
                    elif pp.get(key):
                        val, lo, hi = pp[key]["p90_median"], min(pp[key]["p90"]), max(pp[key]["p90"])
                    else:
                        continue
                    ax.bar(x0, val * 100, width, color=color, edgecolor="white", linewidth=0.8, hatch="////" if p == "ii" else None, zorder=2)
                    if lo is not None:
                        ax.plot([x0, x0], [lo * 100, hi * 100], color=INK, lw=0.9, zorder=3)
        ax.set_yscale("log")
        lo_, hi_ = ax.get_ylim()
        ax.set_ylim(lo_, hi_ * 2.0)
        ticks = [v for v in (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100) if lo_ <= v <= hi_ * 2.0]
        ax.set_yticks(ticks)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _pos: f"{v:g}"))
        ax.yaxis.set_minor_formatter(plt.NullFormatter())
        ax.set_xlim(-0.6, len(cols) - 0.4)
        ax.set_xticks(range(len(cols)), [e["column"] for e in cols], fontsize=8.5)
        ax.set_ylabel("p90 相对误差（%，对数）", fontsize=8.5, color=INK)
        ax.set_title(f"{t}：p90（左半 (i)，右半 (ii)）", fontsize=10, color=INK)
        for i, e in enumerate(cols):
            gi = e["intervals"]["gp"] or {}
            ivh = e["intervals"]["mlp_gp"]
            vals = [b.get("coverage_calibrated_ii") for b in ivh["per_seed"].values() if b and b.get("coverage_calibrated_ii") is not None]
            if gi.get("coverage_calibrated_ii") is not None:
                axc.plot(i - 0.12, gi["coverage_calibrated_ii"], "o", ms=8, color=SERIES["gp"], mec="white", mew=1.5, zorder=3)
            if vals:
                axc.plot([i + 0.12, i + 0.12], [min(vals), max(vals)], color=INK, lw=0.9, zorder=2)
                axc.plot(i + 0.12, ivh["median"]["coverage_calibrated_ii"], "o", ms=8, color=SERIES["H"], mec="white", mew=1.5, zorder=3)
            if gi.get("coverage_calibrated_ii") is None and not vals:
                axc.text(i, 0.04, "无档", ha="center", va="bottom", fontsize=6.5, color=MUTED)
        line = h["rule"]["coverage_ii_min"]
        axc.axhline(line, color=MUTED, lw=0.9, ls="--", zorder=1)
        axc.axhline(0.95, color=MUTED, lw=0.7, ls=":", zorder=1)
        axc.set_ylim(0, 1.03)
        axc.set_xlim(-0.6, len(cols) - 0.4)
        axc.set_xticks(range(len(cols)), [e["column"] for e in cols], fontsize=8.5)
        axc.set_ylabel("校准后 (ii) 的 2σ 覆盖", fontsize=8.5, color=INK)
        axc.set_title(f"{t}：校准后 (ii) 覆盖", fontsize=10, color=INK)
        for a in (ax, axc):
            a.grid(axis="y", color=GRID, lw=0.7, zorder=0)
            a.set_axisbelow(True)
            for side in ("top", "right"):
                a.spines[side].set_visible(False)
            a.tick_params(colors=MUTED, labelcolor=INK)
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    handles = [Patch(facecolor=c, edgecolor="white", label=label) for _k, label, c in series]
    handles += [Patch(facecolor="#9a9994", edgecolor="white", label="(i) 随机留出 5×20%"),
                Patch(facecolor="#9a9994", edgecolor="white", hatch="////", label="(ii) 整档留出外径"),
                Line2D([], [], color=INK, lw=0.9, label="竖线：五个种子的范围"),
                Line2D([], [], color=MUTED, lw=0.9, ls="--", label="规格的 0.90"), Line2D([], [], color=MUTED, lw=0.7, ls=":", label="名义 0.95")]
    fig.legend(handles=handles, loc="upper center", ncol=4, fontsize=8.5, frameon=False)
    fig.tight_layout(rect=(0, 0, 1, 1 - 0.85 / fig.get_figheight()))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)


def report_cmd(args) -> None:
    report = json.loads(args.json.read_text(encoding="utf-8"))
    for v, e in report["variants"].items():          # the reference flag as the JSON recorded it (a report may run without the module)
        VARIANTS.setdefault(v, {}).setdefault("reference", bool(e.get("reference")))
    figure(report, args.fig)
    fig_rel = os.path.relpath(args.fig, args.md.parent)
    fig2_rel = None
    if args.fig2 is not None and "hybrid" in report:
        figure_hybrid(report, args.fig2)
        fig2_rel = os.path.relpath(args.fig2, args.md.parent)
    args.md.parent.mkdir(parents=True, exist_ok=True)
    args.md.write_text(markdown(report, fig_rel, fig2_rel), encoding="utf-8")
    print("wrote", args.md, "and", args.fig, *([args.fig2] if fig2_rel else []), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("root", type=Path)
    r.add_argument("scratch", type=Path)
    r.add_argument("out", type=Path)
    r.add_argument("--tables", default=None, help=f"comma-separated, default all of {list(n104.FAMILY)}")
    r.add_argument("--columns", default=None, help="comma-separated; default every column the specification names")
    r.add_argument("--variants", default=None, help=f"comma-separated, default every registered one: {list(VARIANTS)}")
    r.add_argument("--workers", type=int, default=16)
    r.add_argument("--threads", type=int, default=2)
    r.add_argument("--variant-threads", default=None, help="per-variant thread counts overriding --threads, e.g. mlp=1,mlp_paper=1")
    r.add_argument("--cache", default=None, help="the job cache's directory name under SCRATCH (default jobs-<OUT_JSON stem>)")
    r.add_argument("--cached-only", action="store_true", help="assemble the JSON from the caches; refuse to fit anything")
    r.add_argument("--pilot", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--gp-cache", type=Path, default=N104_GP_CACHE, help="N-104's job cache, read only")
    r.add_argument("--n104-json", type=Path, default=N104_JSON, help="N-104's results: the levels of protocol (ii)")
    r.add_argument("--reuse-cache", type=Path, action="append", default=[],
                   help="another study's job cache (read only): a planned job's result found there is copied into this study's cache")
    r.add_argument("--spec", default=N107_SPEC, help="the specification the JSON names")
    p = sub.add_parser("report")
    p.add_argument("json", type=Path)
    p.add_argument("--md", type=Path, required=True)
    p.add_argument("--fig", type=Path, required=True)
    p.add_argument("--fig2", type=Path, default=None, help="N-108: p90 of gp / mlp / hybrid and the calibrated (ii) coverage per column")
    args = ap.parse_args()
    if args.cmd == "run":
        run(args)
    else:
        report_cmd(args)


if __name__ == "__main__":
    main()
