"""N-110: the multi-turn transformer tables' Q curves as "Q peak x ratio" and their SRF / Q peaks on the dimensionless
coordinates, and the single-turn tables' Q columns on the coordinates -- measured on the second-generation library with the
library's own fitting and calibration code, decided by the rule the specification fixed before the run
(``docs/refactor/N110_MS_Q_SRF_MODELS_SPEC.md``). Nothing of the real library is written; ``report`` says what the rule
decides, and the manifest is changed by hand afterwards (the specification's section 4).

Variants (the specification's section 1): the real library.yaml with these quantity entries changed.

    xfm_ms_*  M0  as today: SRF, Qp_peak, Qs_peak and the Qp / Qs curves direct (one GP per secondary-turns level)
              M1  SRF, Qp_peak, Qs_peak {feature_map: xfm_ms_dimensionless} (one joint GP on the dimensionless inputs)
              M4  Qp, Qs {model: ratio} (Q@f0 = Q peak model x a ratio GP, N-19 option 2)
              M2  M1 + M4
              M3  M1 + Qp, Qs {model: ratio, feature_map: xfm_ms_dimensionless}
    xfm_bs_*  B0  as today: Qp_peak, Qs_peak direct; Qp, Qs {model: ratio}
              B1  Qp_peak, Qs_peak {feature_map: xfm_bs_dimensionless}
              B3  Qp, Qs {model: ratio, feature_map: xfm_bs_dimensionless}
              B2  B1 + B3

Columns: xfm_ms SRF, Qp_peak, Qs_peak, Qp@28, Qs@28, Qp@60, Qs@60, their _hf strata SRF, Qp_peak, Qs_peak and Qp / Qs at
every declared anchor with at least 50 usable rows; xfm_bs (and _hf) Qp_peak, Qs_peak, Qp@28, Qs@28, Qp@112, Qs@112.

Scratch libraries (the method of xfm_bs_q_ratio_acceptance.py / xfm_ms_ratio_acceptance.py): SCRATCH/libs/<table>/<variant>/
<full | L<level>> is a library root whose library.yaml holds the variant (that stratum only) and whose part store holds
the real part's observations (all of them, or all but one outer-diameter level), spec.json and sims/ linked to the real
store. The real library is only read: its datasets through the cache directory SCRATCH/real_cache, which reads the
library's own .cache and writes nothing there. Every scratch dataset is checked against the real one: the same rows in
the same order (less the dropped level) with the same values in every scored column; a variant whose manifest equals the
real one (M0, B0) has the real dataset's cache key.

(i)  The library's own calibration of each column on the variant's full scratch library: ``gp.holdout`` with the settings
     ``Library._fit_inputs`` gives -- what ``Library._calibration`` runs -- or, for a composed curve, the five
     ``Library._composed_fold`` folds merged by ``composed.merge`` -- what ``Library._fit_composed`` runs. Reported: median,
     p90 and max relative error, the 2-sigma coverage before calibration, ``k_scale`` (``gp.calibration_scale``) and the
     median calibrated relative half-width, (hi - lo) / (2 mu) of ``gp.prediction_bounds(mu, sigma, k = 2 k_scale)`` with
     each held-out row's own, unfloored sigma (the floored width, sigma >= median_rel x mu as a query floors it, is kept
     besides). The hold-out hands each fold's (mu, sigma) to ``gp.prediction_bounds``; a wrapper that records them and
     passes everything through is installed around it for the call only. A variant whose entries for a column equal an
     earlier variant's (M0 / M4 SRF, ...) shares that variant's calibration: same settings, same rows, same numbers.
(ii) Leave one outer-diameter level out (OD_S for xfm_ms, OD_P for xfm_bs): per column the levels strictly inside the
     column's OD range with at least 25 usable rows, up to 8 spread evenly over the eligible ones (``--max-levels``), the
     same for every variant. One fold per level: each variant's fold library is fitted without calibration as
     ``Library.models`` fits it -- ``gp.StratumGP(**settings)`` on ``_fit_inputs``, a composed curve's ``ComposedGP`` on its
     ``_composed_settings`` with the base model attached -- and a fit with the same settings on the same rows is made once
     per fold and shared by the variants that have it (``check`` compares the result with ``Library.models`` itself).
     Reported over the held-out rows the model answers: median, p90 and max relative error, the coverage of the interval
     calibrated with (i)'s ``k_scale`` (``gp.prediction_bounds(mu, sigma, k = 2 k_scale)``, unfloored sigma), besides the
     coverage before calibration, the floored one and the calibrated half-width.

Decision (the specification's section 3, fixed before the run): per table, in order, each against the configuration chosen
so far -- SRF mapped or not (xfm_ms), the Q peaks mapped or not, the Q curves given the peaks' choice ({as today, ratio}
with direct peaks, {as today, ratio, ratio-mapped} with mapped peaks; xfm_bs {as today, ratio-mapped}). A change wins
when (a) the median over its columns of p90(ii) changed / p90(ii) today is at most 0.9, (b) no changed column's p90(i)
is more than 10 % above today's, (c) every changed column's calibrated (ii) coverage is at least 0.90, or, where today's
is below 0.90, not below today's by more than 0.02; several winners: the lowest median ratio; none: today's. Two readings
the text leaves open are fixed here and reported both ways: a column without an eligible level has no (ii) numbers and
enters (b) only; on xfm_bs the peaks' change also changes the ratio curves built on them, so (b) and (c) -- the "no
changed column" conditions -- are applied to those curves too, while (a) stays on the peaks as step 2 names them.

Usage:
  ms_q_srf_models_study.py run LIBRARY SCRATCH [--tables T,..] [--only REGEX] [--workers 16] [--threads 2] [--max-levels 8]
                                               [--variants M0,..] [--plan-only]
  ms_q_srf_models_study.py check LIBRARY SCRATCH TABLE LEVEL [--calibrated COL,..] [--threads 2]
  ms_q_srf_models_study.py estimate LIBRARY SCRATCH [--tables T,..] [--workers 16] [--max-levels 8] [--variants ..]
  ms_q_srf_models_study.py report LIBRARY SCRATCH OUT_JSON FIG [--md MD] [--tables T,..] [--max-levels 8] [--variants ..]
Run with OMP_NUM_THREADS equal to --threads: every job runs in a spawned worker with BLAS capped at --threads, so the
study uses at most workers x threads threads. --manifest (every command) is the library.yaml the study runs on; the
scratch directory records its SHA-256 and refuses another (N-110 ran on the manifest N-109 left, kept as
library.yaml.bak_20261013T193513Z after the adopted entries went in).
"""

from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import json
import math
import multiprocessing
import os
import re
import tempfile
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import yaml
from threadpoolctl import threadpool_limits

from ic_opt.library import composed, dataset, gp, manifest, query
from ic_opt.site import HostLimits

HERE = Path(__file__).resolve().parent
JOB_VERSION = 1
MEMORY_GB = 32.0                                   # per worker: a fit of 1600 rows over 7 dims needs about 0.5 GB
FAMILY = {"xfm_ms_ap": "ms", "xfm_ms_m10": "ms", "xfm_ms_ap_hf": "ms", "xfm_ms_m10_hf": "ms",
          "xfm_bs_ap": "bs", "xfm_bs_m10": "bs", "xfm_bs_ap_hf": "bs", "xfm_bs_m10_hf": "bs"}
TABLES = tuple(FAMILY)
LEVEL_DIM = {"ms": "secondary_outer_diameter_um", "bs": "primary_outer_diameter_um"}
MAP = {"ms": "xfm_ms_dimensionless", "bs": "xfm_bs_dimensionless"}
SCALARS = {"ms": ("SRF", "Qp_peak", "Qs_peak"), "bs": ("Qp_peak", "Qs_peak")}
ANCHORS = {"ms": (28, 60), "bs": (28, 112)}         # the ms _hf strata: every declared anchor with >= HF_MIN_ROWS usable rows
HF_MIN_ROWS = 50
CURVES = ("Qp", "Qs")
PEAKS = ("Qp_peak", "Qs_peak")
VARIANTS = {"ms": ("M0", "M1", "M4", "M2", "M3"), "bs": ("B0", "B1", "B3", "B2")}
TODAY = {"ms": "M0", "bs": "B0"}
MIN_LEVEL_ROWS, MAX_LEVELS = 25, 8
RULE = {"p90_ratio_max": 0.9, "p90_i_rise_max": 0.10, "coverage_min": 0.90, "coverage_slack": 0.02}
CURVE_CHOICE = {"M0": "as today", "M1": "as today", "M4": "ratio", "M2": "ratio", "M3": "ratio-mapped",
                "B0": "as today", "B1": "as today", "B3": "ratio-mapped", "B2": "ratio-mapped"}


def changes(family: str, variant: str) -> dict[str, dict]:
    """The quantity entries a variant changes, as updates of the real manifest's entries."""
    fm = {"feature_map": MAP[family]}
    ratio = {"model": "ratio"}
    if family == "ms":
        scalars = {q: fm for q in SCALARS["ms"]}
        return {"M0": {}, "M1": scalars, "M4": {c: ratio for c in CURVES}, "M2": {**scalars, **{c: ratio for c in CURVES}},
                "M3": {**scalars, **{c: {**ratio, **fm} for c in CURVES}}}[variant]
    peaks = {q: fm for q in PEAKS}
    mapped_curves = {c: {**ratio, **fm} for c in CURVES}
    return {"B0": {}, "B1": peaks, "B3": mapped_curves, "B2": {**peaks, **mapped_curves}}[variant]


_DOCS: dict[tuple[int, str, str], dict] = {}


def variant_doc(lib: manifest.Library, table: str, variant: str) -> dict:
    """The variant's manifest: the real one, that stratum only, its entries changed (a fresh copy per call)."""
    key = (id(lib), table, variant)
    if key not in _DOCS:
        doc = lib.model_dump(mode="json")
        stratum = doc["strata"][table]
        for q, update in changes(FAMILY[table], variant).items():
            stratum["quantities"][q] = {**stratum["quantities"][q], **update}
        doc["strata"] = {table: stratum}
        _DOCS[key] = doc
    return copy.deepcopy(_DOCS[key])


def entry(lib: manifest.Library, table: str, variant: str, quantity: str) -> dict:
    key = (id(lib), table, variant)
    if key not in _DOCS:
        variant_doc(lib, table, variant)
    return dict(_DOCS[key]["strata"][table]["quantities"][quantity])


def is_composed(lib: manifest.Library, table: str, variant: str, column: str) -> bool:
    return entry(lib, table, variant, column.split("@")[0]).get("model", "direct") != "direct"


def config_key(lib: manifest.Library, table: str, variant: str, column: str) -> str:
    """What a column's model depends on besides the rows: its quantity's entry and, for a composed curve, its base scalar's."""
    q = column.split("@")[0]
    e = entry(lib, table, variant, q)
    parts = [e] + ([entry(lib, table, variant, manifest.LOW_FREQUENCY[q])] if e.get("model", "direct") != "direct" else [])
    return json.dumps(parts, sort_keys=True)


def representative(lib: manifest.Library, table: str, variants: tuple[str, ...], variant: str, column: str) -> str:
    """The first variant (in the family's order) with the same entries for the column: whose calibration it shares."""
    key = config_key(lib, table, variant, column)
    return next(v for v in variants if config_key(lib, table, v, column) == key)


# -- the plan: columns, levels, held-out rows ------------------------------------------------------------------------------

def table_columns(table: str, stratum: manifest.Stratum, ds: dataset.Dataset) -> list[str]:
    family = FAMILY[table]
    if family == "ms" and table.endswith("_hf"):
        anchors = stratum.quantities["Qp"].anchors_ghz
        curves = [f"{c}@{f:g}" for f in anchors for c in CURVES if len(ds.usable(f"{c}@{f:g}")) >= HF_MIN_ROWS]
    else:
        curves = [f"{c}@{f:g}" for f in ANCHORS[family] for c in CURVES]
    return list(SCALARS[family]) + curves


def column_levels(ds: dataset.Dataset, column: str, dim: str, max_levels: int) -> dict:
    """The column's outer-diameter levels for protocol (ii): strictly inside its range, >= MIN_LEVEL_ROWS usable rows, up to
    ``max_levels`` spread evenly over the eligible ones."""
    count = collections.Counter(r.coords[dim] for r in ds.usable(column))
    lo, hi = min(count), max(count)
    eligible = sorted(v for v, n in count.items() if lo < v < hi and n >= MIN_LEVEL_ROWS)
    chosen = eligible
    if len(eligible) > max_levels:
        chosen = [eligible[i] for i in sorted({round(float(i)) for i in np.linspace(0, len(eligible) - 1, max_levels)})]
    return {"eligible": eligible, "chosen": chosen, "rows": {f"{v:g}": count[v] for v in sorted(count)}}


def pinned_manifest(root: Path, scratch: Path, path: Path | None) -> manifest.Library:
    """The manifest the study runs on: ``path`` (default: the library's own library.yaml). Its SHA-256 is recorded in
    SCRATCH/manifest.json at the first use and every later use must give the same file content: the job cache is keyed by
    the jobs, not by the manifest, so a library.yaml changed afterwards (the adopted entries, section 4) must not be read
    as "today" against results computed before -- give the backup taken before the change instead."""
    path = (path or root / manifest.MANIFEST).resolve()
    text = path.read_text(encoding="utf-8")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    record = scratch / "manifest.json"
    if record.is_file():
        known = json.loads(record.read_text(encoding="utf-8"))
        if known["sha256"] != sha:
            raise SystemExit(f"{path} is not the manifest this scratch directory's jobs ran on ({known['path']}, sha256 "
                             f"{known['sha256'][:16]}...); pass --manifest with that file (the library.yaml backup taken before "
                             "the adopted entries went in)")
    else:
        _write_json(record, {"path": str(path), "sha256": sha, "recorded": time.strftime("%Y-%m-%d %H:%M:%S")})
    return manifest.Library.model_validate(yaml.safe_load(text))


def make_plan(root: Path, scratch: Path, tables: list[str], max_levels: int, variants: dict[str, tuple[str, ...]],
              manifest_path: Path | None = None) -> tuple:
    lib = pinned_manifest(root, scratch, manifest_path)
    plan = {}
    for t in tables:
        family = FAMILY[t]
        ds = dataset.build(root, t, library=lib, cache_dir=scratch / "real_cache")
        cols = table_columns(t, lib.strata[t], ds)
        dim = LEVEL_DIM[family]
        levels = {c: column_levels(ds, c, dim, max_levels) for c in cols}
        union = sorted({v for c in cols for v in levels[c]["chosen"]})
        plan[t] = {"family": family, "dataset": ds, "columns": cols, "dim": dim, "levels": levels, "union": union,
                   "variants": variants[family]}
    return lib, plan


def held_rows(ds: dataset.Dataset, column: str, dim: str, level: float) -> dict:
    rows = [r for r in ds.usable(column) if r.coords[dim] == level]
    return {"x": [[r.coords[d] for d in ds.dims] for r in rows], "y": [r.values[column] / query.fit_unit(column) for r in rows],
            "obs": [r.obs_id for r in rows]}


def lib_root(scratch: Path, table: str, variant: str, level: float | None) -> Path:
    return scratch / "libs" / table / variant / ("full" if level is None else f"L{level:g}")


def scratch_library(real: Path, lib: manifest.Library, table: str, variant: str, dest: Path, drop: float | None) -> Path:
    """A scratch library of one stratum: the part's observations (minus the level ``drop`` of the table's OD dim), spec and
    sims linked to the real store, the variant's manifest. Files are rewritten only when their content changes."""
    (store,) = [p.store for p in lib.strata[table].parts]
    icopt = dest / store / ".icopt"
    icopt.mkdir(parents=True, exist_ok=True)
    dim = LEVEL_DIM[FAMILY[table]]
    kept = []
    for line in (real / store / ".icopt" / "observations.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip() and not (drop is not None and float(json.loads(line)["params"][dim]) == drop):
            kept.append(line)
    _write_if_changed(icopt / "observations.jsonl", "\n".join(kept) + "\n")
    for name in ("spec.json", "sims"):
        link = icopt / name
        if not link.is_symlink():
            link.symlink_to(real / store / ".icopt" / name)
    _write_if_changed(dest / manifest.MANIFEST, yaml.safe_dump(variant_doc(lib, table, variant), sort_keys=False))
    manifest.load(dest)
    return dest


def _write_if_changed(path: Path, text: str) -> None:
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")


# -- jobs (run in spawned workers) -----------------------------------------------------------------------------------------

_FULL: dict[str, query.Library] = {}


def _limits(threads: int) -> HostLimits:
    return HostLimits(max_threads=threads, max_memory_gb=MEMORY_GB)


def _full_library(root: str, threads: int) -> query.Library:
    if root not in _FULL:
        _FULL[root] = query.Library(root, limits=_limits(threads))
    return _FULL[root]


@contextmanager
def recording():
    """Record every (mu, sigma) handed to ``gp.prediction_bounds`` (a hold-out passes each fold's held-out predictions there)
    while passing the call through unchanged; the original is put back on exit."""
    records: list[dict] = []
    original = gp.prediction_bounds

    def wrapper(mu, sigma, *, log_target, k):
        records.append({"mu": np.asarray(mu, dtype=float).tolist(), "sigma": np.asarray(sigma, dtype=float).tolist(),
                        "log_target": bool(log_target)})
        return original(mu, sigma, log_target=log_target, k=k)

    gp.prediction_bounds = wrapper
    try:
        yield records
    finally:
        gp.prediction_bounds = original


def _digest(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, np.ndarray):
            h.update(f"{p.dtype}{p.shape}".encode())
            h.update(np.ascontiguousarray(p).tobytes())
        else:
            h.update(json.dumps(p, sort_keys=True, default=str).encode())
    return h.hexdigest()


def _ids_digest(rows: list[dataset.Row]) -> str:
    return _digest([[r.part, r.obs_id] for r in rows])


def _values_digest(rows: list[dataset.Row], columns: list[str]) -> str:
    return _digest([[r.values.get(c) for c in columns] for r in rows])


def build_job(job: dict, threads: int) -> dict:
    with threadpool_limits(limits=threads):
        t0 = time.time()
        lib = query.Library(job["root"], calibrate=False, limits=_limits(threads))
        ds = lib.dataset(job["table"])
        return {"root": job["root"], "rows": len(ds.rows), "key": ds.key, "cache": ds.cache, "ids": _ids_digest(ds.rows),
                "values": _values_digest(ds.rows, job["columns"]), "generations": ds.generations, "seconds": round(time.time() - t0, 1)}


def cal_direct_job(job: dict, threads: int) -> dict:
    """Protocol (i) of a direct column: the library's ``gp.holdout`` on ``_fit_inputs``."""
    with threadpool_limits(limits=threads):
        lib = _full_library(job["root"], threads)
        ds, rows, x, y, settings = lib._fit_inputs(job["table"], job["column"])
        t0 = time.time()
        with recording() as rec:
            report = gp.holdout(x, y, **settings)
        seconds = time.time() - t0
    if len(rec) != len(gp.SEEDS):
        raise RuntimeError(f"expected {len(gp.SEEDS)} recorded folds, got {len(rec)}")
    return {"rows": len(rows), "dataset_key": ds.key, "settings": settings, "report": report, "pred": rec, "seconds": round(seconds, 1)}


def cal_comp_job(job: dict, threads: int) -> dict:
    """Protocol (i) of a composed curve, one seed: the library's ``_composed_fold`` (every part refitted on the fold)."""
    with threadpool_limits(limits=threads):
        lib = _full_library(job["root"], threads)
        settings = lib._composed_settings(job["table"], job["column"])
        t0 = time.time()
        with recording() as rec:
            fold = lib._composed_fold(job["table"], job["column"], job["seed"])
        seconds = time.time() - t0
    if len(rec) != 1:
        raise RuntimeError(f"expected one recorded fold, got {len(rec)}")
    return {"dataset_key": lib.dataset(job["table"]).key, "settings": settings, "fold": fold, "pred": rec[0], "seconds": round(seconds, 1)}


def _direct(lib: query.Library, table: str, quantity: str, fits: dict, timing: list) -> tuple:
    """A direct model as ``Library.models`` fits it without calibration (k_scale 1, no sigma floor); made once per fold for
    the same settings and rows."""
    _ds, rows, x, y, settings = lib._fit_inputs(table, quantity)
    key = _digest(settings, x, y)
    if key not in fits:
        t0 = time.time()
        fits[key] = (gp.StratumGP(**settings, k_scale=1.0, sigma_floor_rel=0.0).fit(x, y), len(rows))
        timing.append({"quantity": quantity, "part": "direct", "nt_mode": settings["nt_mode"], "feature_map": settings["feature_map"],
                       "rows": len(rows), "seconds": round(time.time() - t0, 2)})
    return fits[key]


def _assemble(lib: query.Library, table: str, column: str, fits: dict, parts: dict, timing: list):
    """The column's model on this fold library as ``Library.models`` (calibration off) builds it: direct, or a ComposedGP on
    the composed settings with the base model attached and its own part fitted -- that part made once per fold for the same
    settings and rows, and shared by the variants whose base model differs (it is fitted on the measured base values)."""
    plan = lib._plan(table, column)
    if plan is None:
        return _direct(lib, table, column, fits, timing)[0]
    lf, n_lf = _direct(lib, table, plan["lf"], fits, timing)
    s = lib._composed_settings(table, column)
    ds, rows, x, y, _settings = lib._fit_inputs(table, column)
    lf_y = ds.values(s["names"]["lf"], rows)
    own = {k: s[k] for k in ("kind", "f0", "dims", "ranges", "nt_dim", "part", "names")}
    key = _digest(own, x, y, lf_y)
    if key in parts:
        return copy.copy(parts[key]).attach(lf, None, n_train=n_lf)       # the copy drops lf / srf (ComposedGP.__getstate__)
    t0 = time.time()
    model = composed.ComposedGP(kind=s["kind"], f0=s["f0"], dims=s["dims"], ranges=s["ranges"], nt_dim=s["nt_dim"], part=s["part"],
                                names=s["names"], k_scale=1.0, sigma_floor_rel=0.0)
    model.attach(lf, None, n_train=n_lf)
    model.fit(x, y, lf_y=lf_y)
    parts[key] = model
    with np.errstate(invalid="ignore"):
        trained = int((np.isfinite(y) & np.isfinite(lf_y) & (y > 0) & (lf_y > 0)).sum())   # the rows ComposedGP.fit keeps
    timing.append({"quantity": column, "part": "ratio", "nt_mode": model.part.nt_mode, "feature_map": s["part"]["feature_map"],
                   "rows": trained, "seconds": round(time.time() - t0, 2)})
    return model


def fold_job(job: dict, threads: int) -> dict:
    """Protocol (ii), one level: every variant's fold library, the held-out rows' (mu, unfloored sigma) per column."""
    t0 = time.time()
    fits: dict = {}
    parts: dict = {}
    timing: list = []
    out = {}
    with threadpool_limits(limits=threads):
        for v, root in job["roots"].items():
            lib = query.Library(root, calibrate=False, limits=_limits(threads))
            ds = lib.dataset(job["table"])
            preds = {}
            for c, held in job["held"].items():
                model = _assemble(lib, job["table"], c, fits, parts, timing)
                x = np.array(held["x"], dtype=float)
                mu, sigma = model.predict(x, floor=False)
                preds[c] = {"mu": mu.tolist(), "sigma": sigma.tolist(), "available": model.available(x).tolist(),
                            "log_target": bool(model.log_target), "composed": isinstance(model, composed.ComposedGP)}
            out[v] = {"rows": len(ds.rows), "key": ds.key, "ids": _ids_digest(ds.rows), "pred": preds}
    return {"variants": out, "fits": timing, "seconds": round(time.time() - t0, 1)}


RUNNERS = {"cal_direct": cal_direct_job, "cal_comp": cal_comp_job, "fold": fold_job}


def run_job(job: dict, path: str, threads: int) -> dict:
    """In a worker: run the job, write its result file (atomic), return a short summary."""
    t0 = time.time()
    try:
        result = RUNNERS[job["kind"]](job, threads)
    except Exception:  # noqa: BLE001 -- a failed job is reported and the run goes on
        return {"name": job["name"], "ok": False, "error": traceback.format_exc(), "seconds": round(time.time() - t0, 1)}
    _write_json(Path(path), {"version": JOB_VERSION, "job": job, **result})
    return {"name": job["name"], "ok": True, "seconds": result["seconds"], "wall": round(time.time() - t0, 1)}


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp", delete=False, encoding="utf-8") as f:
        f.write(json.dumps(data))
    os.replace(f.name, path)


# -- the job list --------------------------------------------------------------------------------------------------------

def job_path(scratch: Path, name: str) -> Path:
    return scratch / "jobs" / (name.replace("/", "__").replace("@", "_at_") + ".json")


def jobs_for(lib: manifest.Library, scratch: Path, t: str, p: dict) -> list[dict]:
    variants = p["variants"]
    ds = p["dataset"]
    out, seen = [], set()
    for c in p["columns"]:
        for v in variants:
            rep = representative(lib, t, variants, v, c)
            if (c, rep) in seen:
                continue
            seen.add((c, rep))
            root = str(lib_root(scratch, t, rep, None))
            if is_composed(lib, t, rep, c):
                for seed in gp.SEEDS:
                    out.append({"kind": "cal_comp", "name": f"{t}/cal_comp/{c}/{rep}/s{seed}", "table": t, "column": c, "variant": rep,
                                "seed": int(seed), "root": root})
            else:
                out.append({"kind": "cal_direct", "name": f"{t}/cal_direct/{c}/{rep}", "table": t, "column": c, "variant": rep, "root": root})
    for level in p["union"]:
        held = {c: held_rows(ds, c, p["dim"], level) for c in p["columns"] if level in p["levels"][c]["chosen"]}
        out.append({"kind": "fold", "name": f"{t}/fold/{level:g}", "table": t, "level": level,
                    "roots": {v: str(lib_root(scratch, t, v, level)) for v in variants}, "held": held})
    return out


def _cube(rows: list[dataset.Row], column: str, nt_dim: str | None, joint: bool, frac: float) -> float:
    """A fit's cost in units of (1000 rows)^3: one GP over the rows, or one per turns level with >= 25 rows."""
    if joint or nt_dim is None:
        return (frac * len(rows) / 1000) ** 3
    count = collections.Counter(round(r.coords[nt_dim]) for r in rows)
    return sum((frac * n / 1000) ** 3 for n in count.values() if frac * n >= gp.StratumGP.MIN_NT_SAMPLES)


def weight(lib: manifest.Library, plan: dict, job: dict) -> float:
    """The job's fitting work in (1000 rows)^3 units: for the order the jobs start in, longest first, and the estimate."""
    t = job["table"]
    ds = plan[t]["dataset"]

    def fit(q: str, v: str, frac: float) -> float:
        e = entry(lib, t, v, q.split("@")[0])
        return _cube(ds.usable(q), q, ds.nt_dim, bool(e.get("feature_map")), frac)

    if job["kind"] == "cal_direct":
        return len(gp.SEEDS) * fit(job["column"], job["variant"], 0.8)
    if job["kind"] == "cal_comp":
        c, v = job["column"], job["variant"]
        lf = manifest.LOW_FREQUENCY[c.split("@")[0]]
        held = 0.2 * len(ds.usable(c)) / max(1, len(ds.usable(lf)))
        return fit(lf, v, 1 - held) + fit(c, v, 0.8)
    keys = {}
    for c in job["held"]:
        for v in plan[t]["variants"]:
            q = c.split("@")[0]
            if is_composed(lib, t, v, c):
                lf = manifest.LOW_FREQUENCY[q]
                keys[(lf, json.dumps(entry(lib, t, v, lf), sort_keys=True))] = fit(lf, v, 1.0)
                keys[(c, "ratio", json.dumps(entry(lib, t, v, q), sort_keys=True))] = fit(c, v, 1.0)
            else:
                keys[(c, json.dumps(entry(lib, t, v, q), sort_keys=True))] = fit(c, v, 1.0)
    return sum(keys.values())


# -- run ------------------------------------------------------------------------------------------------------------------

def _parse_variants(text: str | None) -> dict[str, tuple[str, ...]]:
    if not text:
        return dict(VARIANTS)
    wanted = set(text.split(","))
    return {f: tuple(v for v in vs if v in wanted or v == TODAY[f]) for f, vs in VARIANTS.items()}


def cmd_run(args) -> None:
    t_start = time.time()
    root, scratch = args.library.resolve(), args.scratch.resolve()
    tables = args.tables.split(",")
    lib, plan = make_plan(root, scratch, tables, args.max_levels, _parse_variants(args.variants), args.manifest)
    jobs = [j for t in tables for j in jobs_for(lib, scratch, t, plan[t])]
    if args.only:
        jobs = [j for j in jobs if re.search(args.only, j["name"])]
    todo = [j for j in jobs if not _cached(scratch, j)]
    print(f"[plan] {len(jobs)} jobs ({len(jobs) - len(todo)} cached): " + ", ".join(
        f"{t}: {sum(j['table'] == t for j in jobs)}" for t in tables), flush=True)
    for t in tables:
        p = plan[t]
        print(f"[plan] {t}: rows {len(p['dataset'].rows)}, key {p['dataset'].key}, columns {p['columns']}", flush=True)
        for c in p["columns"]:
            print(f"[plan]   {c}: levels {[f'{v:g}' for v in p['levels'][c]['chosen']]} of eligible "
                  f"{[f'{v:g}' for v in p['levels'][c]['eligible']]}", flush=True)
    if args.plan_only:
        return
    # scratch libraries and their datasets (built in the pool; checked against the real dataset here)
    needed: dict[str, tuple[str, float | None, str]] = {}
    for j in todo:
        if j["kind"] == "fold":
            for v, r in j["roots"].items():
                needed[r] = (j["table"], j["level"], v)
        else:
            needed[j["root"]] = (j["table"], None, j["variant"])
    for r, (t, level, v) in needed.items():
        scratch_library(root, lib, t, v, Path(r), level)
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx) as pool:
        t0 = time.time()
        builds = {r: pool.submit(build_job, {"root": r, "table": t, "columns": plan[t]["columns"]}, args.threads)
                  for r, (t, _level, _v) in needed.items()}
        problems = []
        for r, fut in builds.items():
            b = fut.result()
            t, level, v = needed[r]
            ds = plan[t]["dataset"]
            rows = [row for row in ds.rows if level is None or row.coords[plan[t]["dim"]] != level]
            if b["ids"] != _ids_digest(rows) or b["values"] != _values_digest(rows, plan[t]["columns"]):
                problems.append(f"{r}: rows or values differ from the real dataset's")
            if level is None and v == TODAY[plan[t]["family"]] and b["key"] != ds.key:
                problems.append(f"{r}: dataset key {b['key']} is not the real {ds.key}")
        datasets_seconds = time.time() - t0
        print(f"[datasets] {len(builds)} scratch datasets built and checked in {datasets_seconds:.0f} s; "
              f"{len(problems)} problems", flush=True)
        for p_ in problems:
            print(f"[datasets] PROBLEM {p_}", flush=True)
        if problems:
            raise SystemExit("scratch datasets do not match the real library")
        order = sorted(todo, key=lambda j: -weight(lib, plan, j))
        running = {pool.submit(run_job, j, str(job_path(scratch, j["name"])), args.threads): j for j in order}
        done, failed = 0, []
        while running:
            finished, _ = wait(list(running), return_when=FIRST_COMPLETED)
            for fut in finished:
                j = running.pop(fut)
                s = fut.result()
                done += 1
                if not s["ok"]:
                    failed.append(j["name"])
                    print(f"[job] FAILED {j['name']}\n{s['error']}", flush=True)
                else:
                    print(f"[job] {done}/{len(order)} {j['name']} {s['seconds']:.1f} s (elapsed {(time.time() - t_start) / 60:.1f} min)", flush=True)
    print(f"[run] {len(order)} jobs, {len(failed)} failed, wall {(time.time() - t_start) / 60:.1f} min", flush=True)
    _write_json(scratch / f"run_summary_{time.strftime('%Y%m%dT%H%M%S')}.json", {
        "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t_start)), "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tables": tables, "only": args.only, "workers": args.workers, "threads": args.threads, "max_levels": args.max_levels,
        "variants": args.variants, "omp_num_threads": os.environ.get("OMP_NUM_THREADS"), "planned": len(jobs), "cached": len(jobs) - len(todo),
        "ran": len(order), "failed": failed, "datasets_built": len(builds), "datasets_seconds": round(datasets_seconds, 1),
        "wall_minutes": round((time.time() - t_start) / 60, 2)})
    if failed:
        raise SystemExit(f"{len(failed)} jobs failed")


def _cached(scratch: Path, job: dict) -> bool:
    path = job_path(scratch, job["name"])
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return False
    return data.get("version") == JOB_VERSION and json.loads(json.dumps(data.get("job"))) == json.loads(json.dumps(job))


def load_result(scratch: Path, job: dict) -> dict | None:
    return json.loads(job_path(scratch, job["name"]).read_text(encoding="utf-8")) if _cached(scratch, job) else None


# -- check: the (ii) assembly against Library.models; (i) against the library's own calibration ------------------------------

def cmd_check(args) -> None:
    root, scratch = args.library.resolve(), args.scratch.resolve()
    t, level = args.table, float(args.level)
    lib, plan = make_plan(root, scratch, [t], args.max_levels, _parse_variants(args.variants), args.manifest)
    p = plan[t]
    (job,) = [j for j in jobs_for(lib, scratch, t, p) if j["kind"] == "fold" and j["level"] == level]
    for v, r in job["roots"].items():
        scratch_library(root, lib, t, v, Path(r), level)
    mine = load_result(scratch, job)
    if mine is None:
        mine = {"job": job, **fold_job(job, args.threads)}
    out = {"table": t, "level": level, "variants": {}}
    with threadpool_limits(limits=args.threads):
        for v, r in job["roots"].items():
            library = query.Library(r, calibrate=False, limits=_limits(args.threads))
            models = library.models(t, list(job["held"]), workers=1, threads=args.threads)
            diffs = {}
            for c, held in job["held"].items():
                x = np.array(held["x"], dtype=float)
                mu, sigma = models[c].gp.predict(x, floor=False)
                a, b = np.array(mine["variants"][v]["pred"][c]["mu"]), np.asarray(mu)
                sa, sb = np.array(mine["variants"][v]["pred"][c]["sigma"]), np.asarray(sigma)
                diffs[c] = {"identical": bool(np.array_equal(a, b, equal_nan=True) and np.array_equal(sa, sb, equal_nan=True)),
                            "max_abs_rel_mu": float(np.nanmax(np.abs(a / b - 1))) if len(a) else 0.0}
            out["variants"][v] = diffs
            print(f"[check] {t} L{level:g} {v}: " + ", ".join(f"{c} {'identical' if d['identical'] else d['max_abs_rel_mu']}"
                                                            for c, d in diffs.items()), flush=True)
    if args.calibrated:
        out["calibrated"] = {}
        for c in args.calibrated.split(","):
            for v in p["variants"]:
                rep = representative(lib, t, p["variants"], v, c)
                if rep != v:
                    continue
                library = query.Library(str(lib_root(scratch, t, rep, None)), limits=_limits(args.threads))
                with threadpool_limits(limits=args.threads):
                    cal = library.models(t, [c], workers=1, threads=args.threads)[c].calibration
                ours = column_i(scratch, lib, t, p, rep, c)
                same = {k: (cal.get(k), ours.get(k2)) for k, k2 in (("k_scale", "k_scale"), ("median_rel", "median_rel"),
                                                                     ("coverage_2sigma_before", "coverage_before"), ("n_scored", "n"))}
                out["calibrated"][f"{c}/{rep}"] = {"library": cal, "study": ours, "pairs": same,
                                                   "identical": all(a == b for a, b in same.values())}
                print(f"[check] calibration {t} {c} {rep}: " + ", ".join(f"{k} {a} vs {b}" for k, (a, b) in same.items()), flush=True)
    _write_json(scratch / f"check_{t}_L{level:g}.json", out)


# -- statistics -------------------------------------------------------------------------------------------------------------

def _rel(y: np.ndarray, mu: np.ndarray) -> np.ndarray:
    return np.abs(mu - y) / np.abs(y)


def _halfwidth(mu: np.ndarray, sigma: np.ndarray, log_target: bool, k: float) -> np.ndarray:
    lo, hi = gp.prediction_bounds(mu, sigma, log_target=log_target, k=k)
    return (hi - lo) / (2.0 * np.abs(mu))


def column_i(scratch: Path, lib: manifest.Library, t: str, p: dict, variant: str, c: str) -> dict:
    """Protocol (i) of one column under one variant, from the job results (the representative variant's)."""
    rep = representative(lib, t, p["variants"], variant, c)
    root = str(lib_root(scratch, t, rep, None))
    if is_composed(lib, t, rep, c):
        results = [load_result(scratch, {"kind": "cal_comp", "name": f"{t}/cal_comp/{c}/{rep}/s{s}", "table": t, "column": c,
                                         "variant": rep, "seed": int(s), "root": root}) for s in gp.SEEDS]
        if any(r is None for r in results):
            return {"complete": False}
        report = composed.merge([r["fold"] for r in results])
        preds = [r["pred"] for r in results]
        seconds = sum(r["seconds"] for r in results)
        extra = {"dropped_nonpositive": report["dropped_nonpositive"], "model": results[0]["settings"]["kind"]}
    else:
        r = load_result(scratch, {"kind": "cal_direct", "name": f"{t}/cal_direct/{c}/{rep}", "table": t, "column": c, "variant": rep,
                                  "root": root})
        if r is None:
            return {"complete": False}
        report, preds, seconds = r["report"], r["pred"], r["seconds"]
        extra = {"model": "direct", "nt_mode": r["settings"]["nt_mode"], "feature_map": r["settings"]["feature_map"]}
    k_scale = gp.calibration_scale(report)
    mu = np.concatenate([np.array(q["mu"], dtype=float) for q in preds])
    sigma = np.concatenate([np.array(q["sigma"], dtype=float) for q in preds])
    lt = preds[0]["log_target"]
    floor = report["median_rel"] * np.abs(mu)
    return {"complete": True, "shared_with": rep, "n": report["n_scored"], "rows": report["n"],
            "skipped_unfitted_level": report["skipped_unfitted_level"], "median_rel": report["median_rel"],
            "p90_rel": report["p90_rel"], "max_rel": report["max_rel"], "coverage_before": report["coverage_2sigma"],
            "k_scale": k_scale, "halfwidth_cal_median": float(np.median(_halfwidth(mu, sigma, lt, 2.0 * k_scale))),
            "halfwidth_cal_floored_median": float(np.median(_halfwidth(mu, np.maximum(sigma, floor), lt, 2.0 * k_scale))),
            "seconds": seconds, **extra}


def column_ii(fold_results: dict[float, dict], p: dict, variant: str, c: str, cal: dict) -> dict:
    """Protocol (ii) of one column under one variant: the held-out rows of its levels, pooled."""
    ys, mus, sigmas, per_level, unavailable = [], [], [], {}, 0
    lt = True
    for level in p["levels"][c]["chosen"]:
        res = fold_results.get(level)
        if res is None:
            return {"complete": False}
        held = res["job"]["held"][c]
        pr = res["variants"][variant]["pred"][c]
        y, mu, sigma = np.array(held["y"], dtype=float), np.array(pr["mu"], dtype=float), np.array(pr["sigma"], dtype=float)
        ok = np.array(pr["available"], dtype=bool) & np.isfinite(mu) & np.isfinite(sigma)
        unavailable += int((~ok).sum())
        lt = pr["log_target"]
        per_level[f"{level:g}"] = {"n": int(ok.sum()), "held": len(y), **(_errors(y[ok], mu[ok]) if ok.any() else {})}
        ys.append(y[ok])
        mus.append(mu[ok])
        sigmas.append(sigma[ok])
    if not p["levels"][c]["chosen"]:
        return {"complete": True, "n": 0, "levels": {}}
    y, mu, sigma = np.concatenate(ys), np.concatenate(mus), np.concatenate(sigmas)
    if not len(y):
        return {"complete": True, "n": 0, "unavailable": unavailable, "levels": per_level}
    k = cal.get("k_scale", 1.0)
    floor = cal.get("median_rel", 0.0) * np.abs(mu)

    def covered(s: np.ndarray, kk: float) -> float:
        lo, hi = gp.prediction_bounds(mu, s, log_target=lt, k=kk)
        return float(np.mean((y >= lo) & (y <= hi)))

    return {"complete": True, "n": len(y), "unavailable": unavailable, **_errors(y, mu), "coverage_before": covered(sigma, 2.0),
            "coverage_cal": covered(sigma, 2.0 * k), "coverage_cal_floored": covered(np.maximum(sigma, floor), 2.0 * k),
            "halfwidth_cal_median": float(np.median(_halfwidth(mu, sigma, lt, 2.0 * k))), "levels": per_level}


def _errors(y: np.ndarray, mu: np.ndarray) -> dict:
    rel = _rel(y, mu)
    return {"median_rel": float(np.median(rel)), "p90_rel": float(np.quantile(rel, 0.9)), "max_rel": float(rel.max())}


# -- decision -----------------------------------------------------------------------------------------------------------------

def _finite(v) -> bool:
    return v is not None and isinstance(v, float | int) and math.isfinite(v)


def judge(cand: dict, base: dict, cols: list[str], harm_cols: list[str] | None = None, coverage: str = "coverage_cal") -> dict:
    """The rule's three conditions for a change over ``cols`` against the configuration chosen so far (``base``): (a) the
    median over the columns of p90(ii) cand / base <= 0.9, (b) no column's p90(i) > 1.1 x base, (c) every column's calibrated
    (ii) coverage >= 0.90, or, where the base's is below 0.90, >= base - 0.02. ``harm_cols`` (default ``cols``): the columns
    (b) and (c) cover. A column without (ii) numbers enters (b) only. ``coverage``: the (ii) coverage (c) reads -- the
    specification's, ``coverage_cal``; ``coverage_cal_floored`` (sigma floored as a query floors it) is reported besides."""
    harm_cols = cols if harm_cols is None else harm_cols
    ratios = {}
    for c in cols:
        a, b = cand[c]["ii"].get("p90_rel"), base[c]["ii"].get("p90_rel")
        if _finite(a) and _finite(b) and b > 0:
            ratios[c] = a / b
    median_ratio = float(np.median(list(ratios.values()))) if ratios else float("nan")
    b_fail = [c for c in harm_cols if not cand[c]["i"]["p90_rel"] <= (1 + RULE["p90_i_rise_max"]) * base[c]["i"]["p90_rel"]]
    c_fail = []
    for c in harm_cols:
        a, b = cand[c]["ii"].get(coverage), base[c]["ii"].get(coverage)
        if not _finite(a):
            continue
        need = RULE["coverage_min"] if not (_finite(b) and b < RULE["coverage_min"]) else min(RULE["coverage_min"], b - RULE["coverage_slack"])
        if a < need:
            c_fail.append(c)
    a_ok = bool(ratios) and median_ratio <= RULE["p90_ratio_max"]
    return {"columns": cols, "harm_columns": harm_cols, "ratios": ratios, "median_ratio": median_ratio, "a": a_ok,
            "b": not b_fail, "b_fail": b_fail, "c": not c_fail, "c_fail": c_fail, "wins": a_ok and not b_fail and not c_fail}


def decide(t: str, p: dict, stats: dict[str, dict]) -> dict:
    """The rule, in order, per table; ``stats[variant][column] = {"i": ..., "ii": ...}``."""
    family = p["family"]
    cols = p["columns"]
    curve_cols = [c for c in cols if "@" in c]
    peaks = list(PEAKS)
    steps, config = {}, {}
    have = set(stats)
    if family == "ms":
        if {"M0", "M1"} <= have:
            j = judge(stats["M1"], stats["M0"], ["SRF"])
            steps["SRF"] = {"candidates": {"M1": j}, "chosen": "mapped" if j["wins"] else "direct"}
            j = judge(stats["M1"], stats["M0"], peaks)
            steps["peaks"] = {"candidates": {"M1": j}, "chosen": "mapped" if j["wins"] else "direct"}
        else:
            steps["SRF"] = steps["peaks"] = {"candidates": {}, "chosen": "direct"}
        config["SRF"], config["peaks"] = steps["SRF"]["chosen"], steps["peaks"]["chosen"]
        base, cands = ("M1", ["M2", "M3"]) if config["peaks"] == "mapped" else ("M0", ["M4"])
    else:
        if {"B0", "B1"} <= have:
            j = judge(stats["B1"], stats["B0"], peaks, harm_cols=peaks + curve_cols)
            literal = judge(stats["B1"], stats["B0"], peaks)
            everything = judge(stats["B1"], stats["B0"], peaks + curve_cols)
            steps["peaks"] = {"candidates": {"B1": j}, "chosen": "mapped" if j["wins"] else "direct",
                              "readings": {"peaks_only": literal["wins"], "all_changed_columns": everything["wins"],
                                           "applied": j["wins"]}, "all_changed_columns": everything}
        else:
            steps["peaks"] = {"candidates": {}, "chosen": "direct"}
        config["peaks"] = steps["peaks"]["chosen"]
        base, cands = ("B1", ["B2"]) if config["peaks"] == "mapped" else ("B0", ["B3"])
    cands = [v for v in cands if v in have]
    judged = {v: judge(stats[v], stats[base], curve_cols) for v in cands}
    winners = [v for v in cands if judged[v]["wins"]]
    pick = min(winners, key=lambda v: judged[v]["median_ratio"]) if winners else base
    steps["curves"] = {"base": base, "candidates": judged, "chosen": CURVE_CHOICE[pick], "variant": pick,
                       "floored": {v: judge(stats[v], stats[base], curve_cols, coverage="coverage_cal_floored") for v in cands}}
    for name, (cand, ref, cols_, harm) in {"SRF": ("M1", "M0", ["SRF"], None), "peaks": ("M1", "M0", peaks, None)}.items():
        if family == "ms" and name in steps and {cand, ref} <= have:
            steps[name]["floored"] = {cand: judge(stats[cand], stats[ref], cols_, harm, coverage="coverage_cal_floored")}
    if family == "bs" and {"B0", "B1"} <= have:
        steps["peaks"]["floored"] = {"B1": judge(stats["B1"], stats["B0"], peaks, peaks + curve_cols, coverage="coverage_cal_floored")}
    config["curves"] = CURVE_CHOICE[pick]
    # the chosen configuration's numbers per column: each column from the variant that has it as chosen
    chosen = {}
    for c in cols:
        if c == "SRF":
            v = "M1" if config["SRF"] == "mapped" else "M0"
        elif c in PEAKS:
            v = {"ms": {"mapped": "M1", "direct": "M0"}, "bs": {"mapped": "B1", "direct": "B0"}}[family][config["peaks"]]
        else:
            v = pick
        chosen[c] = v
    sensitivity = {}
    if family == "ms" and t.endswith("_hf"):              # the anchors read as the specification's example, 60 and 112 only
        band = [c for c in curve_cols if c.split("@")[1] in ("60", "112")]
        j2 = {v: judge(stats[v], stats[base], band) for v in cands}
        w2 = [v for v in cands if j2[v]["wins"]]
        sensitivity["anchors_60_112"] = {"candidates": j2, "chosen": CURVE_CHOICE[min(w2, key=lambda v: j2[v]["median_ratio"])]
                                         if w2 else CURVE_CHOICE[base]}
    changed = config != ({"SRF": "direct", "peaks": "direct", "curves": "as today"} if family == "ms"
                         else {"peaks": "direct", "curves": "as today"})
    return {"config": config, "steps": steps, "column_variant": chosen, "changed": changed, "sensitivity": sensitivity}


def manifest_entries(family: str, config: dict) -> dict[str, dict]:
    """The quantity entries the chosen configuration changes (updates of today's)."""
    fm = {"feature_map": MAP[family]}
    out = {}
    if config.get("SRF") == "mapped":
        out["SRF"] = fm
    if config["peaks"] == "mapped":
        out.update({q: fm for q in PEAKS})
    if config["curves"] == "ratio":
        out.update({c: {"model": "ratio"} for c in CURVES})
    elif config["curves"] == "ratio-mapped":
        out.update({c: {"model": "ratio", **fm} for c in CURVES})
    return out


# -- estimate --------------------------------------------------------------------------------------------------------------

def cmd_estimate(args) -> None:
    """Fit seconds = a + b x weight per job kind on the jobs already run, predict the others, and pack them longest first on
    the workers: the wall time the rest of the run should take."""
    root, scratch = args.library.resolve(), args.scratch.resolve()
    tables = args.tables.split(",")
    lib, plan = make_plan(root, scratch, tables, args.max_levels, _parse_variants(args.variants), args.manifest)
    jobs = [j for t in tables for j in jobs_for(lib, scratch, t, plan[t])]
    done = {k: [] for k in RUNNERS}
    todo = []
    for j in jobs:
        r = load_result(scratch, j)
        if r is None:
            todo.append(j)
        else:
            done[j["kind"]].append((weight(lib, plan, j), r["seconds"]))
    model = {}
    for kind, pts in done.items():
        if len(pts) >= 2:
            w, s = np.array(pts).T
            b, a = np.polyfit(w, s, 1) if np.ptp(w) > 0 else (0.0, float(np.mean(s)))
            model[kind] = (max(a, 0.0), max(b, 0.0))
        elif pts:
            model[kind] = (0.0, pts[0][1] / max(pts[0][0], 1e-9))
    print("[estimate] seconds = a + b x weight per kind, from the jobs done: " + ", ".join(
        f"{k}: a {a:.1f} s, b {b:.1f} s/unit (n {len(done[k])})" for k, (a, b) in model.items()), flush=True)
    secs = []
    for j in todo:
        a, b = model.get(j["kind"], model.get("cal_comp", (0.0, 100.0)))
        secs.append(a + b * weight(lib, plan, j))
    loads = [0.0] * args.workers
    for s in sorted(secs, reverse=True):
        loads[loads.index(min(loads))] += s
    by_table = collections.defaultdict(float)
    for j, s in zip(todo, secs, strict=True):
        by_table[j["table"]] += s
    print(f"[estimate] {len(todo)} jobs to run, {sum(secs) / 3600:.2f} h of fitting on {args.workers} workers: about "
          f"{max(loads) / 3600:.2f} h wall (longest job {max(secs, default=0) / 60:.1f} min); per table "
          + ", ".join(f"{t} {s / 3600:.2f} h" for t, s in by_table.items()), flush=True)


# -- report ----------------------------------------------------------------------------------------------------------------

def _clean(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    return value


def library_calibration(root: Path, scratch: Path, t: str, c: str, pinned: manifest.Library) -> dict | None:
    """The real library's cached calibration of a column as it stands today (read only; None when not cached)."""
    library = query.Library(root, cache_dir=scratch / "real_cache")
    library.manifest = pinned                                # the manifest the study ran on, not a later library.yaml
    if library._plan(t, c) is not None:
        return library._composed_calibration(t, c, compute=False)
    ds, _rows, x, y, settings = library._fit_inputs(t, c)
    return library._calibration(ds, c, x, y, settings, compute=False)


def library_calibration_file(root: Path, scratch: Path, t: str, c: str, pinned: manifest.Library) -> Path | None:
    """Where the real library's cached calibration of a column is (read only)."""
    library = query.Library(root, cache_dir=scratch / "real_cache")
    library.manifest = pinned
    if library._plan(t, c) is not None:
        return library.cache.find(library._composed_calibration_name(t, c))
    return library.cache.find(query._calibration_name(library.dataset(t), c))


def collect(root: Path, scratch: Path, tables: list[str], max_levels: int, variants: dict, manifest_path: Path | None) -> dict:
    lib, plan = make_plan(root, scratch, tables, max_levels, variants, manifest_path)
    out = {"tables": {}, "cost": {"jobs": 0, "fit_seconds": 0.0, "by_kind": collections.Counter()}, "incomplete": []}
    for t in tables:
        p = plan[t]
        jobs = jobs_for(lib, scratch, t, p)
        folds = {}
        for j in jobs:
            r = load_result(scratch, j)
            if r is None:
                out["incomplete"].append(j["name"])
                continue
            out["cost"]["jobs"] += 1
            out["cost"]["fit_seconds"] += r["seconds"]
            out["cost"]["by_kind"][j["kind"]] += r["seconds"]
            if j["kind"] == "fold":
                folds[j["level"]] = r
        stats = {}
        for v in p["variants"]:
            stats[v] = {}
            for c in p["columns"]:
                ci = column_i(scratch, lib, t, p, v, c)
                cii = column_ii(folds, p, v, c, ci) if ci.get("complete") else {"complete": False}
                stats[v][c] = {"i": ci, "ii": cii}
        complete = all(s["i"].get("complete") and s["ii"].get("complete") for sv in stats.values() for s in sv.values())
        decision = decide(t, p, stats) if complete else None
        cached = {}
        for c in p["columns"]:
            cal = library_calibration(root, scratch, t, c, lib)
            today = stats[TODAY[p["family"]]][c]["i"]
            pairs = [(cal.get("k_scale"), today.get("k_scale")), (cal.get("median_rel"), today.get("median_rel")),
                     (cal.get("coverage_2sigma_before"), today.get("coverage_before"))] if cal is not None else []
            cached[c] = None if cal is None else {
                "k_scale": cal.get("k_scale"), "median_rel": cal.get("median_rel"), "coverage_2sigma_before": cal.get("coverage_2sigma_before"),
                "n_scored": cal.get("n_scored"), "identical_to_study": all(a == b for a, b in pairs),
                "max_rel_diff": max(abs(a / b - 1) if b else abs(a - b) for a, b in pairs) if all(_finite(a) and _finite(b) for a, b in pairs) else None,
                "file": str(library_calibration_file(root, scratch, t, c, lib))}
        fits = [f for r in folds.values() for f in r["fits"]]
        out["tables"][t] = {"family": p["family"], "rows": len(p["dataset"].rows), "dataset_key": p["dataset"].key,
                            "columns": p["columns"], "level_dim": p["dim"], "levels": p["levels"], "variants": list(p["variants"]),
                            "variant_changes": {v: changes(p["family"], v) for v in p["variants"]}, "complete": complete,
                            "stats": stats, "decision": decision,
                            "manifest_entries": manifest_entries(p["family"], decision["config"]) if decision else None,
                            "library_cached_today": cached, "fold_fits": fits}
    out["cost"]["by_kind"] = dict(out["cost"]["by_kind"])
    return out


def cmd_report(args) -> None:
    root, scratch = args.library.resolve(), args.scratch.resolve()
    tables = args.tables.split(",")
    data = collect(root, scratch, tables, args.max_levels, _parse_variants(args.variants), args.manifest)
    runs = sorted(scratch.glob("run_summary_*.json"))
    data["runs"] = [json.loads(r.read_text(encoding="utf-8")) for r in runs]
    data.update({"date": time.strftime("%Y-%m-%d %H:%M"), "library": str(root), "specification": "docs/refactor/N110_MS_Q_SRF_MODELS_SPEC.md",
                 "rule": RULE, "min_level_rows": MIN_LEVEL_ROWS, "max_levels": args.max_levels, "hf_min_rows": HF_MIN_ROWS,
                 "omp_num_threads": os.environ.get("OMP_NUM_THREADS"),
                 "manifest": json.loads((scratch / "manifest.json").read_text(encoding="utf-8"))})
    args.out.write_text(json.dumps(_clean(data), indent=1), encoding="utf-8")
    print(f"wrote {args.out}; incomplete jobs: {len(data['incomplete'])}", flush=True)
    if args.fig:
        figure(data, args.fig)
    if args.md:
        args.md.write_text(markdown(data), encoding="utf-8")
        print(f"wrote {args.md}", flush=True)


# the reference categorical palette's first five slots in their validated order (adjacent pairs, light surface; the
# sub-3:1 slots are relieved by the tables of the Markdown report), one marker shape per slot as the second channel
SLOTS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4")
SHAPES = ("o", "s", "^", "D", "p")
INK, SECONDARY, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb"
LAYOUT = (("xfm_ms_ap", "xfm_ms_m10"), ("xfm_ms_ap_hf",), ("xfm_ms_m10_hf",), ("xfm_bs_ap", "xfm_bs_m10"), ("xfm_bs_ap_hf", "xfm_bs_m10_hf"))
CONFIG_CN = {"mapped": "无量纲坐标", "direct": "直接（今天）", "as today": "今天的", "ratio": "Q 峰值 × 比值",
             "ratio-mapped": "Q 峰值 × 比值（比值用无量纲坐标）"}
LEGEND_CN = ("M0 / B0：今天的配置",
             "M1：SRF、Q 峰值用无量纲坐标 / B1：Q 峰值用无量纲坐标",
             "M4：Q 曲线改 Q 峰值 × 比值 / B3：比值改用无量纲坐标",
             "M2 = M1 + M4 / B2 = B1 + B3",
             "M3 = M1 + Q 曲线改 Q 峰值 × 比值（比值用无量纲坐标）")


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = ["Noto Sans CJK JP", "Droid Sans Fallback", "DejaVu Sans"]   # the SC face hides in a .ttc listed under JP
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _config_text(family: str, config: dict | None) -> str:
    if config is None:
        return "未完成"
    parts = ([f"SRF {CONFIG_CN[config['SRF']]}"] if family == "ms" else []) + [f"Q 峰值 {CONFIG_CN[config['peaks']]}",
                                                                              f"Q 曲线 {CONFIG_CN[config['curves']]}"]
    return "；".join(parts)


def _panel(ax, t: str, te: dict) -> None:
    cols, vs = te["columns"], te["variants"]
    step = 0.8 / len(vs)
    for i, c in enumerate(cols):
        if i:
            ax.axvline(i - 0.5, color=GRID, lw=0.8, zorder=0)
        for k, v in enumerate(vs):
            x = i + (k - (len(vs) - 1) / 2) * step
            st = te["stats"][v][c]
            a, b = st["i"].get("p90_rel"), st["ii"].get("p90_rel")
            if _finite(a) and _finite(b):
                ax.plot([x, x], [100 * a, 100 * b], color=SLOTS[k], lw=0.9, alpha=0.55, zorder=2)
            if _finite(a):
                ax.plot(x, 100 * a, marker=SHAPES[k], ms=5.5, mfc=SURFACE, mec=SLOTS[k], mew=1.4, ls="none", zorder=3)
            if _finite(b):
                ax.plot(x, 100 * b, marker=SHAPES[k], ms=6.5, mfc=SLOTS[k], mec=SURFACE, mew=0.9, ls="none", zorder=4)
    chosen = (te.get("decision") or {}).get("column_variant", {})
    ax.set_xticks(range(len(cols)), [f"{c}\n选 {chosen.get(c, '—')}" for c in cols], fontsize=8)
    ax.set_xlim(-0.6, len(cols) - 0.4)
    ax.set_yscale("log")
    lo, hi = ax.get_ylim()
    grid = (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200)
    lo, hi = max([v for v in grid if v <= lo] or [grid[0]]), min([v for v in grid if v >= hi] or [grid[-1]])
    ax.set_ylim(lo, hi)                                   # labelled at both ends
    ax.set_yticks([v for v in grid if lo <= v <= hi])
    ax.yaxis.set_major_formatter(_plt().FuncFormatter(lambda v, _pos: f"{v:g}"))
    ax.yaxis.set_minor_formatter(_plt().NullFormatter())
    ax.set_ylabel("p90 相对误差（%，对数）", fontsize=8.5, color=INK)
    dec = te.get("decision") or {}
    ax.set_title(f"{t}（{te['rows']} 行）  采用：{_config_text(te['family'], dec.get('config')) if dec.get('changed') else '不改（今天的）'}",
                 fontsize=9.5, color=INK, loc="left")
    ax.grid(axis="y", color=GRID, lw=0.7, zorder=0)
    ax.set_axisbelow(True)
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#c3c2b7")
    ax.tick_params(colors=MUTED, labelcolor=INK)


def figure(data: dict, path: Path) -> None:
    """p90 relative error per column and variant: hollow = protocol (i), filled = protocol (ii), one panel per table; under
    each column the variant whose number the chosen configuration has."""
    plt = _plt()
    from matplotlib.gridspec import GridSpec
    from matplotlib.lines import Line2D
    rows = [tuple(t for t in row if t in data["tables"]) for row in LAYOUT]
    rows = [r for r in rows if r]
    fig = plt.figure(figsize=(16, 3.4 * len(rows) + 1.3), facecolor=SURFACE)
    grid = GridSpec(len(rows), 2, figure=fig, hspace=0.62, wspace=0.12)
    for r, row in enumerate(rows):
        for k, t in enumerate(row):
            _panel(fig.add_subplot(grid[r, :] if len(row) == 1 else grid[r, k]), t, data["tables"][t])
    handles = [Line2D([], [], marker=SHAPES[k], color=SLOTS[k], mfc=SLOTS[k], mec=SURFACE, ms=7, ls="none", label=LEGEND_CN[k])
               for k in range(len(SLOTS))]
    handles += [Line2D([], [], marker="o", color=SECONDARY, mfc=SURFACE, mec=SECONDARY, mew=1.4, ms=6, ls="none",
                       label="空心：(i) 库自己的随机留出 5×20 % 的 p90"),
                Line2D([], [], marker="o", color=SECONDARY, mfc=SECONDARY, mec=SURFACE, ms=7, ls="none",
                       label="实心：(ii) 整档留出外径（ms 次级、bs 初级）的 p90")]
    fig.legend(handles=handles, loc="upper center", ncol=3, fontsize=8.5, frameon=False, labelcolor=INK)
    height = fig.get_figheight()
    fig.subplots_adjust(left=0.05, right=0.99, bottom=0.62 / height, top=1 - 1.15 / height)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120, facecolor=SURFACE)
    plt.close(fig)
    print(f"wrote {path}", flush=True)


def _pct(v, digits: int = 2) -> str:
    return f"{100 * v:.{digits}f}" if _finite(v) else "—"


def _num(v, digits: int = 3) -> str:
    return f"{v:.{digits}f}" if _finite(v) else "—"


def _pair(a: str, b: str) -> str:
    return a if a == b else f"{a} → {b}"


def _judged(j: dict) -> str:
    ratio = _num(j["median_ratio"], 3)
    flags = [f"(a) {ratio} {'✓' if j['a'] else '✗'}", f"(b) {'✓' if j['b'] else '✗ ' + ', '.join(j['b_fail'])}",
             f"(c) {'✓' if j['c'] else '✗ ' + ', '.join(j['c_fail'])}"]
    return "；".join(flags)


def markdown(data: dict) -> str:
    """The report's tables (Chinese, for RESULT_CN.md): the decisions, today against the chosen configuration per column, every
    variant's p90 under both protocols, the rule's numbers per step, the library's cached calibration against the study's."""
    L = []
    L.append("## 决定（按规格第 3 节事先定下的规则）\n")
    L.append("| 表 | 行数 | 第 1 步 SRF | 第 2 步 Q 峰值 | 第 3 步 Q 曲线 | 结果 |")
    L.append("|---|---|---|---|---|---|")
    for t, te in data["tables"].items():
        dec = te.get("decision")
        if dec is None:
            L.append(f"| {t} | {te['rows']} | 未完成 | | | |")
            continue
        st = dec["steps"]
        srf = (f"{CONFIG_CN[st['SRF']['chosen']]}：" + "；".join(_judged(j) for j in st["SRF"]["candidates"].values())) if "SRF" in st else "—"
        peaks = f"{CONFIG_CN[st['peaks']['chosen']]}：" + "；".join(_judged(j) for j in st["peaks"]["candidates"].values())
        curves = f"{CONFIG_CN[st['curves']['chosen']]}（对照 {st['curves']['base']}）：" + " ／ ".join(
            f"{v} {_judged(j)}" for v, j in st["curves"]["candidates"].items())
        L.append(f"| {t} | {te['rows']} | {srf} | {peaks} | {curves} | {'改：' + _config_text(te['family'], dec['config']) if dec['changed'] else '不改'} |")
    L.append("")
    L.append("(a) = 改动的各列 p90(ii) 比值（改后 ÷ 今天）的中位数，≤ 0.9 才算；(b) = 改动的各列 p90(i) 不比今天高 10 % 以上；"
             "(c) = 改动的各列 (ii) 校准后覆盖 ≥ 0.90（今天低于 0.90 的列：不比今天低 0.02 以上）。✗ 后面是不满足的列。\n")
    for t, te in data["tables"].items():
        dec = te.get("decision")
        if dec is None:
            continue
        today = TODAY[te["family"]]
        L.append(f"### {t}：今天（{today}）→ 选定配置，每列\n")
        L.append("| 列 | 选定 | (i) 中位 % | (i) p90 % | (i) 最大 % | (i) 校准前覆盖 % | k_scale | (i) 校准后半宽中位 % | "
                 "(ii) 中位 % | (ii) p90 % | (ii) 最大 % | (ii) 校准后覆盖 % | (ii) 行数 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for c in te["columns"]:
            v = dec["column_variant"][c]
            a, b = te["stats"][today][c], te["stats"][v][c]
            L.append(f"| {c} | {v} | {_pair(_pct(a['i']['median_rel']), _pct(b['i']['median_rel']))} | "
                     f"{_pair(_pct(a['i']['p90_rel']), _pct(b['i']['p90_rel']))} | {_pair(_pct(a['i']['max_rel'], 1), _pct(b['i']['max_rel'], 1))} | "
                     f"{_pair(_pct(a['i']['coverage_before'], 1), _pct(b['i']['coverage_before'], 1))} | "
                     f"{_pair(_num(a['i']['k_scale']), _num(b['i']['k_scale']))} | "
                     f"{_pair(_pct(a['i']['halfwidth_cal_median']), _pct(b['i']['halfwidth_cal_median']))} | "
                     f"{_pair(_pct(a['ii'].get('median_rel')), _pct(b['ii'].get('median_rel')))} | "
                     f"{_pair(_pct(a['ii'].get('p90_rel')), _pct(b['ii'].get('p90_rel')))} | "
                     f"{_pair(_pct(a['ii'].get('max_rel'), 1), _pct(b['ii'].get('max_rel'), 1))} | "
                     f"{_pair(_pct(a['ii'].get('coverage_cal'), 1), _pct(b['ii'].get('coverage_cal'), 1))} | "
                     f"{_pair(str(a['ii'].get('n', 0)), str(b['ii'].get('n', 0)))} |")
        L.append("")
    L.append("## 每个变体：p90(i) / p90(ii) / (ii) 校准后覆盖（%）\n")
    for t, te in data["tables"].items():
        vs = te["variants"]
        L.append(f"### {t}\n")
        L.append("| 列 | " + " | ".join(vs) + " |")
        L.append("|---|" + "---|" * len(vs))
        for c in te["columns"]:
            cells = []
            for v in vs:
                st = te["stats"][v][c]
                cells.append(f"{_pct(st['i'].get('p90_rel'))} / {_pct(st['ii'].get('p90_rel'))} / {_pct(st['ii'].get('coverage_cal'), 1)}")
            L.append(f"| {c} | " + " | ".join(cells) + " |")
        L.append("")
    L.append("## 第 3 步与第 2 步的逐列比值（p90(ii) 改后 ÷ 对照）\n")
    for t, te in data["tables"].items():
        dec = te.get("decision")
        if dec is None:
            continue
        for name, step in dec["steps"].items():
            for v, j in step["candidates"].items():
                ratios = ", ".join(f"{c} {r:.3f}" for c, r in j["ratios"].items())
                L.append(f"- {t} {name} {v}：{ratios or '无 (ii) 数'}；中位 {_num(j['median_ratio'])}")
        for key, sens in dec.get("sensitivity", {}).items():
            L.append(f"- {t} 敏感性（{key}）：选 {CONFIG_CN[sens['chosen']]}；" + "；".join(
                f"{v} {_judged(j)}" for v, j in sens["candidates"].items()))
        for name, step in dec["steps"].items():
            for v, j in step.get("floored", {}).items():
                if j["wins"] != step["candidates"][v]["wins"] or j["c_fail"] != step["candidates"][v]["c_fail"]:
                    L.append(f"- {t} {name} {v}：若 (c) 用库查询时加了 σ 下限的区间 → {_judged(j)}（规则用的是不加下限的，见方法）")
        readings = dec["steps"].get("peaks", {}).get("readings")
        if readings:
            L.append(f"- {t} 第 2 步的三种读法：只看 Q 峰值 {readings['peaks_only']}，所有被改变的列都按 (a)(b)(c) {readings['all_changed_columns']}，"
                     f"采用的读法（(a) 在 Q 峰值上，(b)(c) 也覆盖建在峰值上的比值曲线）{readings['applied']}")
    L.append("")
    L.append("## 库里已缓存的校准（今天的配置）与本研究 (i) 的对照\n")
    L.append("| 表 | 列 | 库：k_scale / 中位 % / 校准前覆盖 % / 行数 | 本研究 | 相同？ |")
    L.append("|---|---|---|---|---|")
    for t, te in data["tables"].items():
        today = TODAY[te["family"]]
        for c, cal in te["library_cached_today"].items():
            if cal is None:
                continue
            ours = te["stats"][today][c]["i"]
            L.append(f"| {t} | {c} | {_num(cal['k_scale'], 4)} / {_pct(cal['median_rel'], 3)} / {_pct(cal['coverage_2sigma_before'], 2)} / {cal['n_scored']} | "
                     f"{_num(ours.get('k_scale'), 4)} / {_pct(ours.get('median_rel'), 3)} / {_pct(ours.get('coverage_before'), 2)} / {ours.get('n')} | "
                     f"{'逐位相同' if cal['identical_to_study'] else '最大相对差 ' + format(cal['max_rel_diff'], '.1e')} |")
    L.append("")
    cost = data["cost"]
    L.append(f"## 代价\n\n作业 {cost['jobs']} 个，拟合时间合计 {cost['fit_seconds'] / 3600:.2f} h（" + "，".join(
        f"{k} {v / 3600:.2f} h" for k, v in cost["by_kind"].items()) + "）。")
    for r in data.get("runs", []):
        L.append(f"- 运行 {r['started']} → {r['finished']}：{r['tables']}，过滤 `{r['only'] or '无'}`，{r['workers']} worker × {r['threads']} 线程，"
                 f"跑 {r['ran']} 个（缓存 {r['cached']} 个），失败 {len(r['failed'])}，墙钟 {r['wall_minutes']:.1f} min")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "check", "estimate", "report"):
        s = sub.add_parser(name)
        s.add_argument("library", type=Path)
        s.add_argument("scratch", type=Path)
        if name == "check":
            s.add_argument("table")
            s.add_argument("level")
            s.add_argument("--calibrated", default="")
        if name == "report":
            s.add_argument("out", type=Path)
            s.add_argument("fig", type=Path, nargs="?")
            s.add_argument("--md", type=Path)
        s.add_argument("--tables", default=",".join(TABLES))
        s.add_argument("--max-levels", type=int, default=MAX_LEVELS)
        s.add_argument("--variants", default="")
        s.add_argument("--threads", type=int, default=2)
        s.add_argument("--workers", type=int, default=16)
        s.add_argument("--manifest", type=Path, default=None,
                       help="the library.yaml the study runs on (default: the library's own; after the adopted entries went in: the backup)")
        if name == "run":
            s.add_argument("--only", default="")
            s.add_argument("--plan-only", action="store_true")
    args = ap.parse_args()
    {"run": cmd_run, "check": cmd_check, "estimate": cmd_estimate, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
