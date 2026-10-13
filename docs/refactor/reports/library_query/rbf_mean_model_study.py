"""N-104: a scipy RBF interpolator as the mean model behind the library's GP interface, measured against the library's own
Gaussian process on the second-generation library with the library's own hold-out protocols. Point errors only: an
interpolator has no uncertainty (sigma is NaN), so nothing here is calibrated. No EMX, no product change; the library is
only read (``query.Library(root, calibrate=False, cache_dir=SCRATCH)``). Specification:
``docs/refactor/N104_RBF_MEAN_MODEL_STUDY_SPEC.md``.

The mean model: ``StratumRBF(gp.StratumGP)`` -- the base class's constructor and attributes plus ``rbf_kernel`` (scipy's
name), ``degree`` and ``smoothing``; ``fit`` splits per turns level exactly as the base class (``MIN_NT_SAMPLES``,
``unavailable_nt``) and fits ``scipy.interpolate.RBFInterpolator`` on ``self._scale(x)`` (min-max on the ranges, or the
feature map's dimensionless features) against log(y) for a log target; ``_predict`` returns exp(mu_log) and sigma NaN.
scipy refusing the fit (more polynomial terms than rows, a singular system) raises ``Unfit``: that fold and column are
recorded ``unfit``; so is a composed column's RBF fold that ``ComposedGP.fit`` refuses because its parts leave R / Res no
training row (an SRF part whose numerically singular fit -- not flagged by scipy -- predicts no training row above f0).
A fit scipy accepts is scored as it predicts, a non-finite prediction as an infinite error. Composed columns (the manifest's ``model: resonance`` / ``ratio``) build every part through the module
attribute ``gp.StratumGP``: a job of an RBF variant rebinds it to ``StratumRBF`` while it runs (and back), so the parts
L_lf, SRF and R / Res are all the variant's; each job records the classes it fitted.

Variants (``VARIANTS``), every one on the rows, splits and target transform the ``gp`` variant sees:
  gp          the library's: ``Library._fit_inputs`` settings (feature map, per_nt, Matern 5/2, 4 restarts), log target
  rbf_d1/3/5  StratumRBF on the same inputs: thin_plate_spline, polynomial degree 1 / 3 / 5, smoothing 1e-6
  rbf_raw_d5  PACOSYT's inputs: the raw dims min-max scaled (feature_map None; per_nt where the table has nt_dim, else joint)

Protocols, on each column's usable rows (a composed column: its curve's positive rows):
  (i)  random hold-out: ``gp.split``, seeds 0-4, fraction 0.2 -- ``gp.holdout``'s splits for a direct column,
       ``composed.holdout_fold``'s for a composed one (every part refitted without the held-out rows);
  (ii) leave one level out of the table's outer-diameter dim: a level with >= 25 usable rows strictly inside the column's
       range of that dim is held out whole (for a composed column from every part's fit: ``composed_fold`` with that
       level's mask); when the table has more than ``--levels`` such levels (union over its columns), that many spread
       evenly over them (the first, the last, the others between), the same for every column and variant.
Verdict per (table, column, protocol): ``rbf_better`` when the best RBF variant's p90 is below the GP's by more than 10 %
of the GP's and its median is not above the GP's; ``gp_better`` with the roles swapped; else ``tie``. The best RBF variant
is the lowest p90 among the variants that fitted every fold and scored exactly the GP's rows.

Usage:
  rbf_mean_model_study.py run LIBRARY_ROOT SCRATCH OUT_JSON [--tables a,b] [--columns a,b] [--variants a,b] [--workers 16]
                          [--threads 2] [--levels 8] [--pilot] [--dry-run]
  rbf_mean_model_study.py report OUT_JSON --md OUT_MD --fig OUT_PNG
Run with OMP_NUM_THREADS=2, IC_OPT_PROFILE_DIRS set and PYTHONPATH at the source tree. The folds run as jobs in a spawn
pool, BLAS capped per worker; every finished job is kept in SCRATCH/jobs-<OUT_JSON stem>/ (keyed by its data, settings
and the model code), so a rerun after an interruption fits only what is missing. --pilot: ind_sym_ap, seeds 0-1, two
levels, every variant. --dry-run: the plan only.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import multiprocessing
import os
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

import numpy as np
import scipy
import sklearn
from scipy.interpolate import RBFInterpolator
from threadpoolctl import threadpool_limits

from ic_opt.library import composed, gp, query

FAMILY = {"ind_sym_ap": "ind", "ind_sym_m10": "ind", "xfm_bs_ap": "xfm_bs", "xfm_bs_m10": "xfm_bs", "xfm_ms_ap": "xfm_ms",
          "xfm_ms_m10": "xfm_ms"}
COLUMNS = {"ind": ["Lp_lf", "Qp_peak", "SRF_p", "Lp@28", "Qp@28", "Lp@112", "Qp@112"],
           "xfm_bs": ["Lp_lf", "k_lf", "Qp_peak", "SRF", "Lp@28", "Qp@28", "k@28", "Lp@112", "Qp@112", "k@112"],
           "xfm_ms": ["Lp_lf", "Ls_lf", "k_lf", "Qs_peak", "SRF", "Ls@28", "Qs@28", "k@28", "Ls@112"]}
LEVEL_DIM = {"ind": "outer_diameter_um", "xfm_bs": "primary_outer_diameter_um", "xfm_ms": "secondary_outer_diameter_um"}
RBF_KERNEL, SMOOTHING = "thin_plate_spline", 1e-6
VARIANTS = {"gp": {"model": "gp"},
            "rbf_d1": {"model": "rbf", "degree": 1, "inputs": "library"},
            "rbf_d3": {"model": "rbf", "degree": 3, "inputs": "library"},
            "rbf_d5": {"model": "rbf", "degree": 5, "inputs": "library"},
            "rbf_raw_d5": {"model": "rbf", "degree": 5, "inputs": "raw"}}
RAW = "rbf_raw_d5"                                    # PACOSYT's own variant: its own line in the summary
MIN_LEVEL_ROWS = 25
BETTER = 0.10                                         # a p90 lower by more than 10 % of the other's
OVERSHOOT = 3.0                                       # an RBF max error above 3 x the GP's is listed
PILOT = {"tables": ["ind_sym_ap"], "seeds": (0, 1), "levels": 2}
PROGRESS_S = 60
COMPOSED_EMPTY = "no row has a positive curve value and base value"   # ComposedGP.fit's refusal (composed.py)


# -- the mean model -----------------------------------------------------------------------------------------------------------

_GP = gp.StratumGP                                    # the library's class, kept before any rebinding


class Unfit(Exception):
    """scipy refused the interpolator: more polynomial terms than rows, or a singular system."""


class StratumRBF(_GP):
    """``gp.StratumGP`` with a scipy ``RBFInterpolator`` as the joint model: same constructor, attributes, scaling and
    per_nt split; sigma is NaN (no uncertainty); ``kernel`` is accepted and ignored."""

    def __init__(self, *, rbf_kernel: str = RBF_KERNEL, degree: int = 1, smoothing: float = SMOOTHING, **settings):
        super().__init__(**settings)
        self.rbf_kernel, self.degree, self.smoothing = rbf_kernel, int(degree), float(smoothing)
        self._rbf: RBFInterpolator | None = None

    def fit(self, x, y) -> StratumRBF:
        x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
        if x.ndim != 2 or x.shape[1] != len(self.dims):
            raise ValueError(f"expected [N, {len(self.dims)}] inputs")
        if self.log_target and (y <= 0).any():
            raise ValueError("log_target requires positive targets")
        if self.nt_mode == "per_nt":                  # as the base class: one sub-model per turns level with enough rows
            self._nt_idx = self.dims.index(self.nt_dim)
            others = [d for i, d in enumerate(self.dims) if i != self._nt_idx]
            levels = np.round(x[:, self._nt_idx]).astype(int)
            self._sub, self.unavailable_nt = {}, {}
            for level in sorted(set(levels.tolist())):
                mask = levels == level
                if mask.sum() < self.MIN_NT_SAMPLES:
                    self.unavailable_nt[level] = int(mask.sum())
                    continue
                sub = StratumRBF(dims=others, ranges={d: self.ranges[d] for d in others}, log_target=self.log_target,
                                 kernel=self.kernel, rbf_kernel=self.rbf_kernel, degree=self.degree, smoothing=self.smoothing)
                self._sub[level] = sub.fit(np.delete(x[mask], self._nt_idx, axis=1), y[mask])
            return self
        try:
            self._rbf = RBFInterpolator(self._scale(x), np.log(y) if self.log_target else y, kernel=self.rbf_kernel,
                                        degree=self.degree, smoothing=self.smoothing)
        except (ValueError, np.linalg.LinAlgError) as exc:
            raise Unfit(f"{len(y)} rows, {len(self._scale(x[:1])[0])} inputs, degree {self.degree}: {type(exc).__name__}: {exc}") from exc
        return self

    def _predict(self, x) -> tuple[np.ndarray, np.ndarray]:
        if self.nt_mode == "per_nt":
            return super()._predict(x)                # the base class's per_nt path, on the sub-models
        x = np.asarray(x, dtype=float)
        if x.ndim != 2 or x.shape[1] != len(self.dims):
            raise ValueError(f"expected [N, {len(self.dims)}] inputs")
        if self._rbf is None:
            raise RuntimeError("fit() first")
        m = self._rbf(self._scale(x))
        sigma = np.full(len(x), np.nan)
        with np.errstate(over="ignore"):
            return (np.exp(m), sigma) if self.log_target else (m, sigma)

    def posterior_cov(self, x):
        raise NotImplementedError("an interpolator has no posterior")


def variant_settings(settings: dict | None, variant: str) -> dict | None:
    """One model's StratumGP settings as the variant fits them (None stays None)."""
    if settings is None:
        return None
    v = VARIANTS[variant]
    if v["model"] == "gp":
        return dict(settings)
    out = {**settings, "rbf_kernel": RBF_KERNEL, "degree": v["degree"], "smoothing": SMOOTHING}
    if v["inputs"] == "raw":
        out["feature_map"] = None
        out["nt_mode"] = "per_nt" if settings.get("nt_dim") else "joint"
    return out


# -- one fold (runs in a worker) --------------------------------------------------------------------------------------------

def _scored(mu: np.ndarray, y: np.ndarray) -> tuple[list[float], int]:
    finite = np.isfinite(mu)
    rel = np.where(finite, np.abs(mu - y) / np.abs(y), np.inf)
    return rel.tolist(), int((~finite).sum())


def direct_fold(job: dict) -> dict:
    """A direct column: ``gp.StratumGP`` (whatever it is bound to) on the training rows, its held-out rows scored -- one
    seed of ``gp.holdout`` for protocol (i), one level for (ii)."""
    x, y, train, test = job["x"], job["y"], job["train"], job["test"]
    t0 = time.perf_counter()
    model = gp.StratumGP(**job["settings"]).fit(x[train], y[train])
    t1 = time.perf_counter()
    ok = model.available(x[test])
    rows = test[ok]
    mu, _sigma = model.predict(x[rows], floor=False)
    t2 = time.perf_counter()
    rel, nonfinite = _scored(mu, y[rows])
    return {"status": "ok", "rows": rows.tolist(), "rel": rel, "nonfinite": nonfinite, "skipped": int((~ok).sum()),
            "n_test": len(test), "n_train": {"model": len(train)}, "fits": 1, "classes": [type(model).__name__],
            "fit_s": t1 - t0, "predict_s": t2 - t1}


def composed_fold(job: dict) -> dict:
    """A composed column: ``composed.holdout_fold`` with an explicit ``held`` mask instead of ``gp.split`` -- every part
    (L_lf, SRF for ``resonance``, R / Res) refitted on its own usable rows minus the held ones, the curve's positive held
    rows scored."""
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
    n_train["part"] = model.n_train                 # the rows R / Res was fitted on
    t1 = time.perf_counter()
    ok = model.available(x[test])
    rows = test[ok]
    mu, _sigma = model.predict(x[rows], floor=False)
    t2 = time.perf_counter()
    rel, nonfinite = _scored(mu, y[rows])
    parts = [lf_model, srf_model, model.part]
    return {"status": "ok", "rows": rows.tolist(), "rel": rel, "nonfinite": nonfinite, "skipped": int((~ok).sum()),
            "n_test": len(test), "n_train": n_train, "fits": sum(p is not None for p in parts),
            "classes": sorted({type(p).__name__ for p in parts if p is not None}), "fit_s": t1 - t0, "predict_s": t2 - t1}


def run_job(job: dict) -> dict:
    """One (table, column, fold, variant) in a spawned worker: BLAS capped, ``gp.StratumGP`` bound to the variant's class
    while the fold runs and restored after it."""
    t0 = time.perf_counter()
    model = VARIANTS[job["variant"]]["model"]
    with threadpool_limits(limits=job["threads"]):
        gp.StratumGP = StratumRBF if model == "rbf" else _GP
        try:
            bound = gp.StratumGP.__name__
            out = direct_fold(job) if job["kind"] == "direct" else composed_fold(job)
        except (Unfit, ValueError) as exc:
            # a composed column whose RBF parts leave the curve's own part no training row (ComposedGP.fit: an SRF part that
            # predicts no training row above f0 -- a numerically singular fit scipy did not flag) is unfit as well; a GP job,
            # a direct fold or any other ValueError is an error of the study
            built = isinstance(exc, ValueError) and model == "rbf" and job["kind"] != "direct" and str(exc).startswith(COMPOSED_EMPTY)
            if not isinstance(exc, Unfit) and not built:
                raise
            reason = f"composed {job['kind']}: {exc}" if built else str(exc)
            out = {"status": "unfit", "reason": reason, "rows": [], "rel": [], "nonfinite": 0, "skipped": 0, "n_test": 0, "n_train": {},
                   "fits": 0, "classes": [], "fit_s": time.perf_counter() - t0, "predict_s": 0.0}
        finally:
            gp.StratumGP = _GP
    out.update({"key": job["key"], "mean_model": bound, "wall_s": time.perf_counter() - t0, "pid": os.getpid()})
    return out


# what a cached job result depends on besides its data and settings: a GP job not on StratumRBF, so the GP folds stay cached
SOURCE = {"gp": hashlib.sha256("".join(inspect.getsource(o) for o in (gp, composed, _scored, direct_fold, composed_fold)).encode()).hexdigest()}
SOURCE["rbf"] = hashlib.sha256((SOURCE["gp"] + "".join(inspect.getsource(o) for o in (StratumRBF.__init__, StratumRBF.fit, StratumRBF._predict,
                                                                                      variant_settings))).encode()).hexdigest()


# -- the plan (main process) ------------------------------------------------------------------------------------------------

def spread(values: list[float], k: int) -> list[float]:
    """``k`` of the sorted ``values`` spread evenly: the first, the last and the others between (all when there are <= k)."""
    if len(values) <= k:
        return list(values)
    idx = np.round(np.linspace(0, len(values) - 1, k)).astype(int)
    return [values[i] for i in sorted(set(idx.tolist()))]


def column_data(lib: query.Library, table: str, column: str, j: int) -> dict:
    """A column's data and settings, exactly as the library builds them; its usable rows and its eligible levels."""
    plan = lib._plan(table, column)
    if plan is None:
        _ds, _rows, x, y, settings = lib._fit_inputs(table, column)
        usable = np.ones(len(y), dtype=bool)
        out = {"kind": "direct", "x": x, "y": y, "settings": settings}
    else:
        x, y, lf_y, srf_y = lib._composed_data(table, column)
        usable = np.isfinite(y) & (y > 0)
        out = {"kind": plan["model"], "x": x, "y": y, "lf_y": lf_y, "srf_y": srf_y, "settings": lib._composed_settings(table, column)}
    vals = x[usable, j]
    levels, counts = np.unique(vals, return_counts=True)
    out["eligible"] = [float(v) for v, n in zip(levels, counts) if n >= MIN_LEVEL_ROWS and vals.min() < v < vals.max()]
    out["usable"], out["n"] = usable, int(usable.sum())
    return out


def folds_of(col: dict, j: int, seeds, levels: list[float]) -> list[dict]:
    out = []
    if col["kind"] == "direct":
        for seed in seeds:
            test, train = gp.split(col["n"], seed)                  # gp.holdout's split of the usable rows
            out.append({"protocol": "i", "fold": f"seed {seed}", "test": test, "train": train})
        for level in levels:
            if level in col["eligible"]:
                held = col["x"][:, j] == level
                out.append({"protocol": "ii", "fold": f"{level:g}", "level": level, "test": np.flatnonzero(held), "train": np.flatnonzero(~held)})
        return out
    curve = np.flatnonzero(col["usable"])
    for seed in seeds:
        test_i, _ = gp.split(len(curve), seed)                      # composed.holdout_fold's split of the curve's positive rows
        held = np.zeros(len(col["y"]), dtype=bool)
        held[curve[test_i]] = True
        out.append({"protocol": "i", "fold": f"seed {seed}", "held": held})
    for level in levels:
        if level in col["eligible"]:
            out.append({"protocol": "ii", "fold": f"{level:g}", "level": level, "held": col["x"][:, j] == level})
    return out


def make_job(table: str, column: str, col: dict, fold: dict, variant: str, threads: int) -> dict:
    job = {"key": [table, column, fold["protocol"], fold["fold"], variant], "variant": variant, "kind": col["kind"], "x": col["x"],
           "y": col["y"], "threads": threads}
    if col["kind"] == "direct":
        job.update(settings=variant_settings(col["settings"], variant), test=fold["test"], train=fold["train"])
        sizes = [len(fold["train"])]
    else:
        s = col["settings"]
        job.update(settings={**s, "part": variant_settings(s["part"], variant), "lf": variant_settings(s["lf"], variant),
                             "srf": variant_settings(s["srf"], variant)},
                   lf_y=col["lf_y"], srf_y=col["srf_y"], held=fold["held"])
        sizes = [int((np.isfinite(col["lf_y"]) & ~fold["held"]).sum()), int((np.isfinite(col["y"]) & ~fold["held"]).sum())]
        if col["kind"] == "resonance":
            sizes.append(int((np.isfinite(col["srf_y"]) & ~fold["held"]).sum()))
    weight = 1.0 if VARIANTS[variant]["model"] == "gp" else 0.02
    job["cost"] = weight * sum(float(n) ** 2 for n in sizes)        # longest first; GP fits grow ~n^2 here (T16.2a timings)
    job["sizes"] = sizes
    return job


def job_hash(job: dict) -> str:
    h = hashlib.sha256(SOURCE[VARIANTS[job["variant"]]["model"]].encode())
    h.update(json.dumps([job["key"], job["kind"], job["settings"]], sort_keys=True, default=str).encode())
    for name in ("x", "y", "lf_y", "srf_y", "test", "train", "held"):
        a = job.get(name)
        if a is not None:
            h.update(name.encode())
            h.update(np.ascontiguousarray(a).tobytes())
    h.update(f"{np.__version__} {scipy.__version__} {sklearn.__version__}".encode())
    return h.hexdigest()[:24]


# -- summaries ----------------------------------------------------------------------------------------------------------------

def rel_stats(rel: list[float]) -> dict:
    """Median, p90 (numpy's linear quantile, as the library's) and max; a non-finite prediction is an infinite error, and a
    quantile that falls between two of them is infinite (numpy would give inf - inf = NaN there)."""
    a = np.asarray(rel, dtype=float)
    if not len(a):
        return {"n": 0, "median_rel": None, "p90_rel": None, "max_rel": None}
    with np.errstate(invalid="ignore"):
        med, p90 = float(np.median(a)), float(np.quantile(a, 0.9))
    if np.isinf(a).any():
        med, p90 = (math.inf if math.isnan(v) else v for v in (med, p90))
    return {"n": len(a), "median_rel": med, "p90_rel": p90, "max_rel": float(a.max())}


def verdict(g: dict, r: dict) -> str:
    if r["p90_rel"] < (1 - BETTER) * g["p90_rel"] and r["median_rel"] <= g["median_rel"]:
        return "rbf_better"
    if g["p90_rel"] < (1 - BETTER) * r["p90_rel"] and g["median_rel"] <= r["median_rel"]:
        return "gp_better"
    return "tie"


def summarise_protocol(results: dict[str, list[dict]], protocol: str) -> dict:
    """Per variant: pooled stats over the protocol's folds, unfit folds, timing; then the best RBF variant and the verdicts."""
    out = {"variants": {}}
    rowsets = {}
    for v, res in results.items():
        res = sorted(res, key=lambda r: (r.get("level") or 0.0, r["key"][3]))
        rel = [x for r in res for x in r["rel"]]
        st = rel_stats(rel)
        unfit = [{"fold": r["key"][3], "reason": r["reason"]} for r in res if r["status"] == "unfit"]
        entry = {"n_scored": st["n"], "median_rel": st["median_rel"], "p90_rel": st["p90_rel"], "max_rel": st["max_rel"],
                 "skipped": sum(r["skipped"] for r in res), "nonfinite": sum(r["nonfinite"] for r in res), "folds": len(res),
                 "unfit_folds": unfit, "complete": not unfit, "fits": sum(r["fits"] for r in res),
                 "fit_s": round(sum(r["fit_s"] for r in res), 3), "predict_s": round(sum(r["predict_s"] for r in res), 3),
                 "fit_s_per_fold": [round(r["fit_s"], 3) for r in res], "predict_s_per_fold": [round(r["predict_s"], 4) for r in res],
                 "classes": sorted({c for r in res for c in r["classes"]}), "mean_model": sorted({r["mean_model"] for r in res})}
        if protocol == "ii":
            entry["per_level"] = {r["key"][3]: {**rel_stats(r["rel"]), "status": r["status"], "skipped": r["skipped"]} for r in res}
        out["variants"][v] = entry
        rowsets[v] = {(r["key"][3], i) for r in res for i in r["rows"]}
    if "gp" not in out["variants"] or not out["variants"]["gp"]["n_scored"]:
        return out
    g = out["variants"]["gp"]
    for v, e in out["variants"].items():
        e["same_rows_as_gp"] = rowsets[v] == rowsets["gp"]
    candidates = [v for v, e in out["variants"].items() if v != "gp" and e["complete"] and e["same_rows_as_gp"] and e["n_scored"]]
    out["eligible_rbf"] = candidates
    if candidates:
        best = min(candidates, key=lambda v: out["variants"][v]["p90_rel"])
        out["best_rbf"] = best
        out["verdict"] = verdict(g, out["variants"][best])
        out["p90_ratio_best_over_gp"] = out["variants"][best]["p90_rel"] / g["p90_rel"]
    else:
        out["best_rbf"], out["verdict"] = None, "no_rbf"
    out["verdict_by_variant"] = {v: verdict(g, out["variants"][v]) if v in candidates else "not_comparable"
                                 for v in out["variants"] if v != "gp"}
    return out


def library_calibration(lib: query.Library, table: str, column: str) -> dict | None:
    """The library's own cached calibration of the column (its gp.holdout / composed.holdout over seeds 0-4), read only."""
    if lib._plan(table, column) is None:
        found = lib.cache.find(query._calibration_name(lib.dataset(table), column))
    else:
        found = lib.cache.find(lib._composed_calibration_name(table, column))
    if found is None:
        return None
    cal = json.loads(found.read_text(encoding="utf-8"))
    return {"median_rel": cal.get("median_rel"), "n_scored": cal.get("n_scored"), "file": found.name}


def git_head() -> str | None:
    try:
        return subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


# -- run ----------------------------------------------------------------------------------------------------------------------

def run(args) -> None:
    t_start = time.time()
    seeds = PILOT["seeds"] if args.pilot else gp.SEEDS
    n_levels = PILOT["levels"] if args.pilot else args.levels
    tables = args.tables.split(",") if args.tables else (PILOT["tables"] if args.pilot else list(FAMILY))
    variants = args.variants.split(",") if args.variants else list(VARIANTS)
    unknown = [v for v in variants if v not in VARIANTS] + [t for t in tables if t not in FAMILY]
    if unknown:
        raise SystemExit(f"unknown tables / variants {unknown}; tables {list(FAMILY)}, variants {list(VARIANTS)}")
    wanted = set(args.columns.split(",")) if args.columns else None
    lib = query.Library(args.root, calibrate=False, cache_dir=args.scratch)
    report = {"spec": "docs/refactor/N104_RBF_MEAN_MODEL_STUDY_SPEC.md", "date_start": time.strftime("%Y-%m-%d %H:%M:%S"),
              "library": str(args.root), "scratch": str(args.scratch), "argv": [Path(sys.argv[0]).name, *sys.argv[1:]],
              "pilot": bool(args.pilot), "seeds": list(seeds), "levels_requested": n_levels, "workers": args.workers,
              "threads": args.threads, "omp_num_threads": os.environ.get("OMP_NUM_THREADS"), "source": git_head(),
              "versions": {"python": sys.version.split()[0], "numpy": np.__version__, "scipy": scipy.__version__,
                           "sklearn": sklearn.__version__},
              "variants": VARIANTS, "rbf": {"kernel": RBF_KERNEL, "smoothing": SMOOTHING}, "min_level_rows": MIN_LEVEL_ROWS,
              "min_nt_samples": _GP.MIN_NT_SAMPLES, "verdict_rule": {"better_by": BETTER, "overshoot_factor": OVERSHOOT}, "tables": {}}
    cols: dict[tuple[str, str], dict] = {}
    jobs = []
    for t in tables:
        ds = lib.dataset(t)
        fam = FAMILY[t]
        j = ds.dims.index(LEVEL_DIM[fam])
        names = [c for c in COLUMNS[fam] if wanted is None or c in wanted]
        data = {c: column_data(lib, t, c, j) for c in names}
        eligible = sorted(set().union(*(d["eligible"] for d in data.values()))) if data else []
        levels = spread(eligible, n_levels)
        entry = {"rows": len(ds.rows), "dataset_key": ds.key, "dims": ds.dims, "nt_dim": ds.nt_dim, "level_dim": LEVEL_DIM[fam],
                 "eligible_levels": eligible, "levels": levels, "columns": {}}
        for c, d in data.items():
            folds = folds_of(d, j, seeds, levels)
            st = d["settings"] if d["kind"] == "direct" else d["settings"]["part"]
            entry["columns"][c] = {"kind": d["kind"], "n_usable": d["n"], "eligible_levels": d["eligible"],
                                   "levels": [f["level"] for f in folds if f["protocol"] == "ii"],
                                   "settings": {"nt_mode": st["nt_mode"], "feature_map": st["feature_map"], "log_target": st["log_target"]},
                                   "library_calibration": library_calibration(lib, t, c)}
            if d["kind"] != "direct":
                s = d["settings"]
                entry["columns"][c]["parts"] = {"lf": s["names"]["lf"], "srf": s["names"]["srf"], "f0_ghz": s["f0"],
                                                "lf_settings": {k: s["lf"][k] for k in ("nt_mode", "feature_map", "log_target")},
                                                "srf_settings": None if s["srf"] is None else {k: s["srf"][k] for k in ("nt_mode", "feature_map", "log_target")}}
            cols[(t, c)] = d
            for f in folds:
                for v in variants:
                    job = make_job(t, c, d, f, v, args.threads)
                    if "level" in f:
                        job["level"] = f["level"]
                    jobs.append(job)
        report["tables"][t] = entry
        print(f"{t}: {len(ds.rows)} rows, {len(names)} columns, levels {levels} of {len(eligible)} eligible "
              f"({LEVEL_DIM[fam]}), {sum(1 for jb in jobs if jb['key'][0] == t)} jobs", flush=True)
    n_gp_fits = sum(len(jb["sizes"]) for jb in jobs if jb["variant"] == "gp")
    print(f"{len(jobs)} jobs ({len(variants)} variants), {n_gp_fits} GP model fits; seeds {list(seeds)}, levels <= {n_levels}; "
          f"plan in {time.time() - t_start:.1f} s", flush=True)
    if args.dry_run:
        for t in tables:
            for v in variants:
                sel = [jb for jb in jobs if jb["key"][0] == t and jb["variant"] == v]
                sizes = [n for jb in sel for n in jb["sizes"]]
                print(f"  {t} {v}: {len(sel)} jobs, {len(sizes)} fits, rows per fit {min(sizes)}..{max(sizes)}, sum n^2 {sum(n * n for n in sizes):.3g}")
        return
    cache = args.scratch / f"jobs-{args.out.stem}"
    cache.mkdir(parents=True, exist_ok=True)
    results, from_cache, pending = [], 0, []
    for job in jobs:
        job["hash"] = job_hash(job)
        path = cache / f"{job['hash']}.json"
        if path.is_file():
            res = json.loads(path.read_text(encoding="utf-8"))
            res["level"] = job.get("level")
            results.append(res)
            from_cache += 1
        else:
            pending.append(job)
    pending.sort(key=lambda jb: -jb["cost"])
    total_cost = sum(jb["cost"] for jb in pending) or 1.0
    print(f"{from_cache} jobs from {cache}, {len(pending)} to run on {args.workers} workers x {args.threads} threads", flush=True)
    t_pool = time.time()
    done_cost, last = 0.0, time.time()
    if pending:
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")) as pool:
            running = {pool.submit(run_job, jb): jb for jb in pending}
            while running:
                finished, _ = wait(list(running), timeout=PROGRESS_S, return_when=FIRST_COMPLETED)
                for fut in finished:
                    job = running.pop(fut)
                    res = fut.result()
                    res["level"] = job.get("level")
                    path = cache / f"{job['hash']}.json"
                    tmp = path.with_suffix(".tmp")
                    tmp.write_text(json.dumps(res), encoding="utf-8")
                    tmp.replace(path)
                    results.append(res)
                    done_cost += job["cost"]
                if time.time() - last >= PROGRESS_S or not running:
                    last = time.time()
                    elapsed = time.time() - t_pool
                    frac = done_cost / total_cost
                    eta = elapsed / frac - elapsed if frac > 0 else float("nan")
                    print(f"  {len(results) - from_cache}/{len(pending)} jobs done, {len(running)} queued or running, "
                          f"{elapsed / 60:.1f} min, cost done {frac * 100:.1f} %, rough remaining {eta / 60:.0f} min", flush=True)
    report["pool_seconds"] = round(time.time() - t_pool, 1)
    report["jobs"] = {"planned": len(jobs), "from_cache": from_cache, "run": len(pending), "cache": str(cache),
                      "unfit": sum(r["status"] == "unfit" for r in results)}
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


def summary(report: dict, variants: list[str]) -> dict:
    """Verdict counts per protocol and per table (best RBF and every RBF variant on its own), the overshoot list, the
    largest p90 ratios, unfit folds and the cost per model."""
    rbf = [v for v in variants if v != "gp"]
    counts = {p: {} for p in ("i", "ii")}
    by_table = {}
    by_variant = {v: {p: {} for p in ("i", "ii")} for v in rbf}
    overshoot, ratios, unfit = [], [], []
    for t, te in report["tables"].items():
        by_table[t] = {p: {} for p in ("i", "ii")}
        for c, ce in te["columns"].items():
            for p, pe in ce.get("protocols", {}).items():
                for v, e in pe["variants"].items():
                    for u in e["unfit_folds"]:
                        unfit.append({"table": t, "column": c, "protocol": p, "variant": v, **u})
                if "verdict" not in pe:
                    continue
                counts[p][pe["verdict"]] = counts[p].get(pe["verdict"], 0) + 1
                by_table[t][p][pe["verdict"]] = by_table[t][p].get(pe["verdict"], 0) + 1
                for v, vv in pe["verdict_by_variant"].items():
                    by_variant[v][p][vv] = by_variant[v][p].get(vv, 0) + 1
                g = pe["variants"]["gp"]
                if pe.get("best_rbf"):
                    b = pe["variants"][pe["best_rbf"]]
                    ratios.append({"table": t, "column": c, "protocol": p, "best_rbf": pe["best_rbf"], "ratio": b["p90_rel"] / g["p90_rel"],
                                   "gp": {k: g[k] for k in ("median_rel", "p90_rel", "max_rel", "n_scored")},
                                   "rbf": {k: b[k] for k in ("median_rel", "p90_rel", "max_rel", "n_scored")}, "verdict": pe["verdict"]})
                for v, e in pe["variants"].items():
                    if v != "gp" and e["max_rel"] is not None and e["max_rel"] > OVERSHOOT * g["max_rel"]:
                        overshoot.append({"table": t, "column": c, "protocol": p, "variant": v, "gp_max_rel": g["max_rel"],
                                          "rbf_max_rel": e["max_rel"], "factor": e["max_rel"] / g["max_rel"], "complete": e["complete"],
                                          "same_rows_as_gp": e.get("same_rows_as_gp"), "nonfinite": e["nonfinite"],
                                          "rbf_p90_rel": e["p90_rel"], "gp_p90_rel": g["p90_rel"]})
    ratios.sort(key=lambda r: r["ratio"])
    cost = {}
    for v in variants:
        per_job, fits, fit_s, pred_s, per_table = [], 0, 0.0, 0.0, {}
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
            per_table[t] = {k: round(x, 2) if isinstance(x, float) else x for k, x in tt.items()}
            fits += tt["fits"]
            fit_s += tt["fit_s"]
            pred_s += tt["predict_s"]
        a = np.asarray(per_job, dtype=float)
        cost[v] = {"jobs": len(a), "fits": fits, "fit_s": round(fit_s, 1), "predict_s": round(pred_s, 2),
                   "fit_s_per_job": {"median": float(np.median(a)) if len(a) else None, "p90": float(np.quantile(a, 0.9)) if len(a) else None,
                                     "max": float(a.max()) if len(a) else None}, "per_table": per_table}
    return {"verdicts": counts, "verdicts_by_table": by_table, "verdicts_by_variant": by_variant, "raw_variant": RAW,
            "raw_verdicts": by_variant.get(RAW), "p90_ratios": ratios, "overshoot": overshoot, "unfit": unfit, "cost": cost}


def _pct(v) -> str:
    if v is None:
        return "—"
    if math.isinf(v) or math.isnan(v):
        return "∞" if math.isinf(v) else "NaN"
    return f"{v * 100:.2f}%" if v < 100 else f"{v * 100:.1e}%"


def print_summary(report: dict) -> None:
    for t, te in report["tables"].items():
        for c, ce in te["columns"].items():
            for p, pe in ce.get("protocols", {}).items():
                parts = [f"{v} p90 {_pct(e['p90_rel'])} max {_pct(e['max_rel'])}{'' if e['complete'] else ' (unfit folds)'}"
                         for v, e in pe["variants"].items()]
                print(f"{t} {c} ({p}) {pe.get('verdict', '-')}: " + " | ".join(parts), flush=True)
    s = report["summary"]
    print("verdicts:", json.dumps(s["verdicts"]), "| raw:", json.dumps(s["raw_verdicts"]), flush=True)
    for v, e in s["cost"].items():
        print(f"cost {v}: {e['jobs']} jobs, {e['fits']} fits, fit {e['fit_s']:.0f} s, predict {e['predict_s']:.1f} s, "
              f"median per job {e['fit_s_per_job']['median']}", flush=True)


# -- report -------------------------------------------------------------------------------------------------------------------

VERDICT_CN = {"rbf_better": "RBF 更好", "gp_better": "GP 更好", "tie": "持平", "no_rbf": "无可比 RBF", "not_comparable": "不可比"}
PROTOCOL_CN = {"i": "(i) 随机留出 5×20%", "ii": "(ii) 整档留出外径"}
GP_COLOR, RBF_COLOR, INK, MUTED, GRID = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e4e3df"


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = ["Noto Sans CJK JP", "Droid Sans Fallback", "DejaVu Sans"]   # the SC face hides in a .ttc listed under JP
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _short(v: str | None) -> str:
    return {"rbf_d1": "d1", "rbf_d3": "d3", "rbf_d5": "d5", "rbf_raw_d5": "raw"}.get(v or "", "—")


def figure(report: dict, path: Path) -> None:
    """p90 relative error per column: gp and the best RBF variant, protocols (i) and (ii) side by side, one panel per table."""
    plt = _plt()
    names = list(report["tables"])
    ncols = 2 if len(names) > 1 else 1
    nrows = (len(names) + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(7.4 * ncols, 3.5 * nrows + 0.8), squeeze=False)
    width = 0.19
    offsets = {("i", "gp"): -1.6 * width, ("i", "rbf"): -0.6 * width, ("ii", "gp"): 0.6 * width, ("ii", "rbf"): 1.6 * width}
    for ax, t in zip(axes.flat, names):
        te = report["tables"][t]
        cols = list(te["columns"])
        for i, c in enumerate(cols):
            pes = te["columns"][c].get("protocols", {})
            for p in ("i", "ii"):
                pe = pes.get(p)
                if pe is None or not pe["variants"].get("gp", {}).get("n_scored"):
                    ax.text(i + (offsets[(p, "gp")] + offsets[(p, "rbf")]) / 2, 0.012, "无档", ha="center", va="bottom", fontsize=6.5,
                            color=MUTED, transform=ax.get_xaxis_transform())
                    continue
                g = pe["variants"]["gp"]["p90_rel"] * 100
                hatch = "////" if p == "ii" else None
                ax.bar(i + offsets[(p, "gp")], g, width, color=GP_COLOR, edgecolor="white", linewidth=1.0, hatch=hatch, zorder=2)
                best = pe.get("best_rbf")
                if best:
                    r = pe["variants"][best]["p90_rel"] * 100
                    ax.bar(i + offsets[(p, "rbf")], r, width, color=RBF_COLOR, edgecolor="white", linewidth=1.0, hatch=hatch, zorder=2)
                    ax.text(i + offsets[(p, "rbf")], r * 1.08, _short(best), ha="center", va="bottom", fontsize=6.5, color=MUTED)
                else:
                    ax.text(i + offsets[(p, "rbf")], g, "×", ha="center", va="bottom", fontsize=8, color=MUTED)
        ax.set_yscale("log")
        lo, hi = ax.get_ylim()
        ax.set_ylim(lo, hi * 1.6)
        ticks = [v for v in (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200) if lo <= v <= hi * 1.6]
        ax.set_yticks(ticks)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _pos: f"{v:g}"))
        ax.yaxis.set_minor_formatter(plt.NullFormatter())
        ax.set_xticks(range(len(cols)), cols, fontsize=8, rotation=30, ha="right")
        ax.set_ylabel("p90 相对误差（%，对数）", fontsize=8.5, color=INK)
        ax.set_title(f"{t}（{te['rows']} 行）", fontsize=10, color=INK)
        ax.grid(axis="y", color=GRID, lw=0.7, zorder=0)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.tick_params(colors=MUTED, labelcolor=INK)
    for ax in list(axes.flat)[len(names):]:
        ax.set_visible(False)
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    handles = [Patch(facecolor=GP_COLOR, edgecolor="white", label="库的 GP"),
               Patch(facecolor=RBF_COLOR, edgecolor="white", label="最好的 RBF 变体（柱上标 d1 / d3 / d5 / raw）"),
               Patch(facecolor="#9a9994", edgecolor="white", label="(i) 随机留出 5×20%"),
               Patch(facecolor="#9a9994", edgecolor="white", hatch="////", label="(ii) 整档留出外径"),
               Line2D([], [], color=MUTED, marker="x", linestyle="none", label="× 没有可比的 RBF 变体（未拟合）")]
    fig.legend(handles=handles, loc="upper center", ncol=5, fontsize=8.5, frameon=False)
    fig.tight_layout(rect=(0, 0, 1, 1 - 0.45 / fig.get_figheight()))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _cnt(d: dict | None) -> str:
    d = d or {}
    return " / ".join(f"{d.get(k, 0)}" for k in ("rbf_better", "tie", "gp_better")) + (f"（无可比 {d['no_rbf']}）" if d.get("no_rbf") else "")


def markdown(report: dict, fig_rel: str | None) -> str:
    s = report["summary"]
    L = []
    L.append("# N-104 结果：scipy RBF 插值器作均值模型，对照库的 GP（第二代库）\n")
    L.append(f"规格：`{report['spec']}`。只量点误差（插值器没有不确定度，不做校准）；不跑 EMX、不改产品代码、库只读"
             f"（`cache_dir` = `{report['scratch']}`）。数字全部来自脚本写出的 JSON。\n")
    L.append("## 跑了什么\n")
    tabs = report["tables"]
    n_cols = sum(len(te["columns"]) for te in tabs.values())
    folds = {p: sum(pe["variants"]["gp"]["folds"] for te in tabs.values() for ce in te["columns"].values()
                    for q, pe in ce.get("protocols", {}).items() if q == p and "gp" in pe["variants"]) for p in ("i", "ii")}
    L.append(f"- 库 `{report['library']}`；{len(tabs)} 张表、{n_cols} 个结果列；变体 {', '.join(report['variants'])}"
             f"（RBF：scipy `RBFInterpolator`，核 `{report['rbf']['kernel']}`，smoothing {report['rbf']['smoothing']:g}）。")
    L.append(f"- 协议 (i)：种子 {report['seeds']}，留出 20%；协议 (ii)：每张表至多 {report['levels_requested']} 个外径档"
             f"（≥ {report['min_level_rows']} 个可用行、严格在该列的外径范围内）。GP 变体的折数：(i) {folds['i']}、(ii) {folds['ii']}。")
    j = report["jobs"]
    L.append(f"- 作业 {j['planned']} 个（每个 = 表 × 列 × 折 × 变体），其中 {j['from_cache']} 个取自作业缓存、{j['run']} 个本次计算；"
             f"未拟合 {j['unfit']} 个。")
    L.append(f"- 并行：{report['workers']} 个 spawn 工作进程 × {report['threads']} 个 BLAS 线程（OMP_NUM_THREADS={report['omp_num_threads']}）；"
             f"开始 {report['date_start']}，结束 {report['date_end']}，总用时 {report['wall_seconds'] / 60:.1f} 分钟（进程池 {report['pool_seconds'] / 60:.1f} 分钟）。")
    v = report["versions"]
    L.append(f"- 版本：源码 `{report['source']}`，Python {v['python']}，numpy {v['numpy']}，scipy {v['scipy']}，scikit-learn {v['sklearn']}。")
    L.append(f"- 命令：`{' '.join(report['argv'])}`\n")
    L.append("各表的外径档：\n")
    L.append("| 表 | 行 | 外径维 | 合格档（各列并集） | 选用的档 |")
    L.append("| --- | ---: | --- | --- | --- |")
    for t, te in tabs.items():
        L.append(f"| {t} | {te['rows']} | `{te['level_dim']}` | {', '.join(f'{x:g}' for x in te['eligible_levels']) or '—'} | "
                 f"{', '.join(f'{x:g}' for x in te['levels']) or '—'} |")
    L.append("")
    L.append("## 判定计数\n")
    L.append("判定规则：最好的 RBF 变体（在拟合了每一折、且与 GP 打分的行完全相同的变体里取 p90 最低者）的 p90 比 GP 低出 GP 的 10% 以上、"
             "且中位数不高于 GP，记 RBF 更好；角色互换记 GP 更好；否则持平。注意“最好的变体”是在同一批误差上从四个里挑的，对 RBF 偏乐观。\n")
    L.append("| 协议 | RBF 更好 / 持平 / GP 更好 |")
    L.append("| --- | --- |")
    for p in ("i", "ii"):
        L.append(f"| {PROTOCOL_CN[p]} | {_cnt(s['verdicts'][p])} |")
    L.append("")
    L.append("按表：\n")
    L.append("| 表 | (i) RBF 更好 / 持平 / GP 更好 | (ii) RBF 更好 / 持平 / GP 更好 |")
    L.append("| --- | --- | --- |")
    for t in tabs:
        L.append(f"| {t} | {_cnt(s['verdicts_by_table'][t]['i'])} | {_cnt(s['verdicts_by_table'][t]['ii'])} |")
    L.append("")
    L.append("每个 RBF 变体单独对 GP（同一规则；该变体有未拟合的折或打分行与 GP 不同时记“不可比”）：\n")
    L.append("| 变体 | (i) RBF 更好 / 持平 / GP 更好 | (ii) RBF 更好 / 持平 / GP 更好 |")
    L.append("| --- | --- | --- |")
    for vname, d in s["verdicts_by_variant"].items():
        tag = "（PACOSYT 的建法）" if vname == s["raw_variant"] else ""
        cells = [_cnt(d[p]) + (f"，不可比 {d[p]['not_comparable']}" if d[p].get("not_comparable") else "") for p in ("i", "ii")]
        L.append(f"| `{vname}`{tag} | {cells[0]} | {cells[1]} |")
    L.append("")
    if fig_rel:
        L.append(f"![p90 相对误差]({fig_rel})\n")
    L.append("## 各表各列\n")
    L.append("每格：中位数 / p90 / 最大相对误差。“最好的 RBF”一栏给出变体名。\n")
    for t, te in tabs.items():
        L.append(f"### {t}（{te['rows']} 行）\n")
        L.append("| 结果列 | 建法 | 协议 | 打分行 | GP | 最好的 RBF | 判定 | 各 RBF 变体的 p90（d1 / d3 / d5 / raw） |")
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
                gcell = "—" if g is None else f"{_pct(g['median_rel'])} / {_pct(g['p90_rel'])} / {_pct(g['max_rel'])}"
                best = pe.get("best_rbf")
                b = pe["variants"][best] if best else None
                bcell = "—" if b is None else f"`{_short(best)}` {_pct(b['median_rel'])} / {_pct(b['p90_rel'])} / {_pct(b['max_rel'])}"
                each = []
                for vname in ("rbf_d1", "rbf_d3", "rbf_d5", "rbf_raw_d5"):
                    e = pe["variants"].get(vname)
                    if e is None:
                        each.append("—")
                    elif not e["complete"]:
                        each.append(f"未拟合 {len(e['unfit_folds'])}/{e['folds']}")
                    else:
                        each.append(_pct(e["p90_rel"]) + ("" if e.get("same_rows_as_gp", True) else "*"))
                n = g["n_scored"] if g else 0
                L.append(f"| {c} | {kind} | {p} | {n} | {gcell} | {bcell} | {VERDICT_CN.get(pe.get('verdict'), '—')} | {' / '.join(each)} |")
        L.append("")
    L.append("`*`：该变体打分的行与 GP 不同（某匝数档在训练行里不足 25 行，该变体答不了），不参与“最好的 RBF”。\n")
    L.append("## 差别最大的结果列（最好的 RBF 的 p90 ÷ GP 的 p90）\n")
    L.append("| 方向 | 表 | 结果列 | 协议 | 变体 | GP 中位 / p90 / 最大 | RBF 中位 / p90 / 最大 | 比值 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | ---: |")
    ratios = s["p90_ratios"]
    picks = [("RBF 低", r) for r in ratios[:4]] + [("RBF 高", r) for r in reversed(ratios[-4:]) if r not in ratios[:4]]
    for label, r in picks:
        g, b = r["gp"], r["rbf"]
        L.append(f"| {label} | {r['table']} | {r['column']} | {r['protocol']} | `{r['best_rbf']}` | "
                 f"{_pct(g['median_rel'])} / {_pct(g['p90_rel'])} / {_pct(g['max_rel'])} | "
                 f"{_pct(b['median_rel'])} / {_pct(b['p90_rel'])} / {_pct(b['max_rel'])} | {r['ratio']:.2f} |")
    L.append("")
    L.append(f"## RBF 的最大误差超过 GP 的 {OVERSHOOT:g} 倍（插值器在档间可能过冲）\n")
    if s["overshoot"]:
        L.append("“非有限”= 预测溢出为 0 或 ∞ 的行数（scipy 接受了、但数值上奇异的拟合，系数可达 1e30）；这类不是档间过冲，是拟合本身坏了。\n")
        L.append("| 表 | 结果列 | 协议 | 变体 | GP 最大 | RBF 最大 | 倍数 | GP p90 | RBF p90 | 非有限 | 全部折拟合 | 打分行同 GP |")
        L.append("| --- | --- | --- | --- | --- | --- | ---: | --- | --- | ---: | --- | --- |")
        for o in sorted(s["overshoot"], key=lambda o: (o["table"], o["column"], o["protocol"], o["variant"])):
            factor = "∞" if math.isinf(o["factor"]) else f"{o['factor']:.1f}" if o["factor"] < 1e4 else f"{o['factor']:.1e}"
            L.append(f"| {o['table']} | {o['column']} | {o['protocol']} | `{o['variant']}` | {_pct(o['gp_max_rel'])} | {_pct(o['rbf_max_rel'])} | "
                     f"{factor} | {_pct(o.get('gp_p90_rel'))} | {_pct(o.get('rbf_p90_rel'))} | {o.get('nonfinite', '—')} | "
                     f"{'是' if o['complete'] else '否'} | {'是' if o['same_rows_as_gp'] else '否'} |")
    else:
        L.append("没有。")
    L.append("")
    L.append("## 未拟合的折\n")
    if s["unfit"]:
        grouped = {}
        for u in s["unfit"]:
            grouped.setdefault((u["table"], u["variant"]), []).append(u)
        L.append("| 表 | 变体 | 折数 | 涉及的结果列 | 例：scipy 的说法 |")
        L.append("| --- | --- | ---: | --- | --- |")
        for (t, vname), us in grouped.items():
            L.append(f"| {t} | `{vname}` | {len(us)} | {', '.join(sorted({u['column'] for u in us}))} | {us[0]['reason'][:160]} |")
    else:
        L.append("没有。")
    L.append("")
    L.append("## 每种模型的代价\n")
    L.append("拟合时间 = 一个作业里所有模型拟合的墙钟时间（组合列含 2–3 个部件；per_nt 含各匝数档的子模型）；预测 = 对留出行的一次预测。\n")
    L.append("| 变体 | 作业 | 模型拟合 | 拟合合计 (s) | 每作业拟合 中位 / p90 / 最大 (s) | 预测合计 (s) |")
    L.append("| --- | ---: | ---: | ---: | --- | ---: |")
    for vname, e in s["cost"].items():
        f = e["fit_s_per_job"]
        L.append(f"| `{vname}` | {e['jobs']} | {e['fits']} | {e['fit_s']:.0f} | {f['median']:.2f} / {f['p90']:.2f} / {f['max']:.2f} | {e['predict_s']:.1f} |")
    L.append("")
    L.append("按表的拟合合计（s）：\n")
    L.append("| 表 | " + " | ".join(f"`{x}`" for x in s["cost"]) + " |")
    L.append("| --- |" + " ---: |" * len(s["cost"]))
    for t in tabs:
        L.append(f"| {t} | " + " | ".join(f"{s['cost'][x]['per_table'][t]['fit_s']:.0f}" for x in s["cost"]) + " |")
    L.append("")
    L.append("## 核对：GP 变体的协议 (i) 与库自己缓存的校准\n")
    L.append("库的校准就是同一协议（`gp.holdout` / `composed.holdout`，种子 0–4）；GP 变体在 (i) 上的合并中位数应与库缓存里的 `median_rel` 相同。\n")
    L.append("| 表 | 结果列 | 本研究 GP (i) 中位 / 打分行 | 库缓存的中位 / 打分行 | 一致 |")
    L.append("| --- | --- | --- | --- | --- |")
    for t, te in tabs.items():
        for c, ce in te["columns"].items():
            cal = ce.get("library_calibration")
            pe = ce.get("protocols", {}).get("i")
            if pe is None or "gp" not in pe["variants"]:
                continue
            g = pe["variants"]["gp"]
            if cal is None or cal.get("median_rel") is None:
                L.append(f"| {t} | {c} | {_pct(g['median_rel'])} / {g['n_scored']} | 缓存里没有 | — |")
                continue
            same = abs(cal["median_rel"] - g["median_rel"]) <= 1e-9 * max(1.0, abs(cal["median_rel"])) and cal["n_scored"] == g["n_scored"]
            L.append(f"| {t} | {c} | {g['median_rel'] * 100:.4f}% / {g['n_scored']} | {cal['median_rel'] * 100:.4f}% / {cal['n_scored']} | "
                     f"{'是' if same else '否'} |")
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
    r.add_argument("--tables", default=None, help=f"comma-separated, default all of {list(FAMILY)}")
    r.add_argument("--columns", default=None, help="comma-separated; default every column the specification names")
    r.add_argument("--variants", default=None, help=f"comma-separated, default {list(VARIANTS)}")
    r.add_argument("--workers", type=int, default=16)
    r.add_argument("--threads", type=int, default=2)
    r.add_argument("--levels", type=int, default=8)
    r.add_argument("--pilot", action="store_true")
    r.add_argument("--dry-run", action="store_true")
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
