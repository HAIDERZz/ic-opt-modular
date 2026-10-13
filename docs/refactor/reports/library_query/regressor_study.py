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

Usage:
  regressor_study.py run LIBRARY_ROOT SCRATCH OUT_JSON [--tables a,b] [--columns a,b] [--variants a,b] [--workers 16]
                     [--threads 2] [--variant-threads v=n,...] [--cache NAME] [--cached-only] [--pilot] [--dry-run]
                     [--gp-cache DIR] [--n104-json FILE]
  regressor_study.py report OUT_JSON --md OUT_MD --fig OUT_PNG
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

from ic_opt.library import gp, query

HERE = Path(__file__).resolve().parent
REGRESSOR_MODULES = ("regressors_kernel", "regressors_trees", "regressors_neural")
MODULE_FAMILY = {"regressors_kernel": "A", "regressors_trees": "B", "regressors_neural": "C"}
FAMILIES = ("A", "B", "C")
UNTUNED = {"gp", "gp_paper", "mlp_paper"}               # the specification's table: no grid
N104_JSON = HERE / "rbf_mean_model_study.json"
N104_GP_CACHE = Path("/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n104_rbf_study/scratch/jobs-rbf_mean_model_study")
PILOT = {"tables": ["ind_sym_ap"], "seeds": (0, 1), "levels": 2}
PROGRESS_S = 60
# relative cost per (rows of a fit)^2, only to start the longest jobs first and for the progress line's estimate
COST_WEIGHT = {"gp": 1.0, "gp_paper": 1.0, "krr": 0.3, "knn": 0.002, "rf": 0.5, "gbm": 1.0, "lgbm": 0.5, "mlp": 3.0,
               "mlp_paper": 1.0}


# -- the registry -------------------------------------------------------------------------------------------------------------

VARIANTS: dict[str, dict] = {"gp": {"cls": _GP, "family": "A", "tuned": False, "grid": None, "grid_cut": None, "params": {}, "module": gp.__name__,
                                    "note": "the library's GP (Library._fit_inputs settings); N-104's folds, from its job cache"}}
MODULE_NOTES: list[str] = []


def _register(name: str, entry, module) -> None:
    spec = {"cls": entry} if isinstance(entry, type) else dict(entry)
    cls = spec.get("cls", spec.get("model"))             # "model": N-104's shape, as B and C register
    if not (isinstance(cls, type) and issubclass(cls, StratumMean)):
        raise TypeError(f"{module.__name__}: variant {name!r} is not a regressor_base.StratumMean subclass: {cls!r}")
    if name in VARIANTS:
        raise ValueError(f"{module.__name__}: variant {name!r} is registered twice")
    VARIANTS[name] = {"cls": cls, "family": spec.get("family") or getattr(cls, "FAMILY", None) or MODULE_FAMILY.get(module.__name__, "?"),
                      "tuned": bool(spec.get("tuned", name not in UNTUNED)), "grid": spec.get("grid", getattr(cls, "GRID", None)),
                      "grid_cut": spec.get("grid_cut") or None, "params": dict(spec.get("params") or {}), "module": module.__name__,
                      "note": spec.get("note", "")}


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
    return {"family": e["family"], "model": e["cls"].__name__, "module": e["module"], "tuned": e["tuned"],
            "grid": json.loads(json.dumps(e["grid"], default=str)), "grid_axes": json.loads(json.dumps(axes, default=str)),
            "n_configs": n_configs, "grid_cut": json.loads(json.dumps(e["grid_cut"], default=str)), "params": e["params"],
            "note": e["note"]}


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
    job["cost"] = COST_WEIGHT.get(variant, 1.0) * sum(float(n) ** 2 for n in sizes)
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
    eligible = [v for v, e in vs.items() if v != "gp" and e["complete"] and e["same_rows_as_gp"] and e["n_scored"]]
    out["eligible"] = eligible
    out["verdict_by_variant"] = {v: verdict(g, vs[v]) if v in eligible else "not_comparable" for v in vs if v != "gp"}
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
    others = [v for v in variants if v != "gp"]
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


# -- run ----------------------------------------------------------------------------------------------------------------------

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
    wanted = set(args.columns.split(",")) if args.columns else None
    for line in MODULE_NOTES:
        print(line, flush=True)
    chosen_levels = n104_levels(args.n104_json)
    lib = query.Library(args.root, calibrate=False, cache_dir=args.scratch)
    report = {"spec": "docs/refactor/N107_REGRESSOR_STUDY_SPEC.md", "date_start": time.strftime("%Y-%m-%d %H:%M:%S"),
              "library": str(args.root), "scratch": str(args.scratch), "argv": [Path(sys.argv[0]).name, *sys.argv[1:]],
              "pilot": bool(args.pilot), "seeds": list(seeds), "levels_from": str(args.n104_json), "gp_cache": str(args.gp_cache),
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
        names = [c for c in n104.COLUMNS[fam] if wanted is None or c in wanted]
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
                                   "library_calibration": n104.library_calibration(lib, t, c)}
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
        if own.is_file():
            found[id(job)] = ("own", own)
        elif job["variant"] == "gp" and n104_file.is_file():
            found[id(job)] = ("n104", n104_file)
    gp_jobs = [jb for jb in jobs if jb["variant"] == "gp"]
    hits = Counter(found[id(jb)][0] for jb in gp_jobs if id(jb) in found)
    print(f"gp: {len(gp_jobs)} jobs, {hits['n104']} from N-104's job cache {args.gp_cache}, {hits['own']} from this study's, "
          f"{len(gp_jobs) - hits['n104'] - hits['own']} to fit", flush=True)
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
    from_cache = Counter()
    for job in jobs:
        if id(job) in found:
            where, path = found[id(job)]
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
            running = {pool.submit(run_job, jb): jb for jb in pending}
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
    report["summary"] = summary(report, variants)
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
    for v, e in s["cost"].items():
        print(f"cost {v}: {e['jobs']} jobs, {e['fits']} fits, fit {e['fit_s']:.0f} s (tune {e['tune_s']:.0f} s), predict {e['predict_s']:.1f} s, "
              f"per job median / p90 / max {e['fit_s_per_job']['median']} / {e['fit_s_per_job']['p90']} / {e['fit_s_per_job']['max']}",
              flush=True)


# -- report -------------------------------------------------------------------------------------------------------------------

VERDICT_CN = {"other_better": "对手更好", "gp_better": "GP 更好", "tie": "持平", "no_other": "无可比变体", "not_comparable": "不可比"}
PROTOCOL_CN = {"i": "(i) 随机留出 5×20%", "ii": "(ii) 整档留出外径"}
FAMILY_CN = {"A": "A 核方法与近邻", "B": "B 树模型", "C": "C 神经网络"}
# the reference categorical palette's first four slots in their validated order: GP, then the families A, B, C
SERIES = {"gp": "#2a78d6", "A": "#eb6834", "B": "#1baf7a", "C": "#eda100"}
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


def markdown(report: dict, fig_rel: str | None) -> str:
    s = report["summary"]
    tabs = report["tables"]
    variants = list(report["variants"])
    others = [v for v in variants if v != "gp"]
    L = []
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
    for name, e in report["variants"].items():
        axes = e.get("grid_axes") or {}
        grid = "—" if not axes else "；".join(f"`{k}` ∈ {{{', '.join(str(x) for x in vals)}}}" for k, vals in axes.items())
        if axes:
            grid += f"（{e.get('n_configs')} 个配置）"
        if e.get("grid_cut"):
            grid += f"；规格网格中未跑的 {len(e['grid_cut'])} 个：" + "，".join(f"`{json.dumps(c, sort_keys=True)}`" for c in e["grid_cut"])
        L.append(f"| `{name}` | {e['family']} | `{e['model']}` | {'是' if e['tuned'] else '否'} | {grid} | {e['note']} |")
    L.append("")
    L.append("各表的外径档（N-104 选定的，读自其 JSON）：\n")
    L.append("| 表 | 行 | 外径维 | 合格档（各列并集） | 选用的档 |")
    L.append("| --- | ---: | --- | --- | --- |")
    for t, te in tabs.items():
        L.append(f"| {t} | {te['rows']} | `{te['level_dim']}` | {', '.join(f'{x:g}' for x in te['eligible_levels']) or '—'} | "
                 f"{', '.join(f'{x:g}' for x in te['levels']) or '—'} |")
    L.append("")
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
        if d:
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


def report_cmd(args) -> None:
    report = json.loads(args.json.read_text(encoding="utf-8"))
    figure(report, args.fig)
    fig_rel = os.path.relpath(args.fig, args.md.parent)
    args.md.parent.mkdir(parents=True, exist_ok=True)
    args.md.write_text(markdown(report, fig_rel), encoding="utf-8")
    print("wrote", args.md, "and", args.fig, flush=True)


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
    p = sub.add_parser("report")
    p.add_argument("json", type=Path)
    p.add_argument("--md", type=Path, required=True)
    p.add_argument("--fig", type=Path, required=True)
    args = ap.parse_args()
    if args.cmd == "run":
        run(args)
    else:
        report_cmd(args)


if __name__ == "__main__":
    main()
