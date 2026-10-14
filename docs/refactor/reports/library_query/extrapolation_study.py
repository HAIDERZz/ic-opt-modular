"""N-115: how far outside the measured range the library's models hold -- each table's edge levels held out, the rest
fitted with the library's own code, the held-out rows predicted -- and the interval scale calibrated by leaving whole
levels of the main size dim out, beside today's random hold-out (``docs/refactor/N115_EXTRAPOLATION_STUDY_SPEC.md``). No
EMX; the library is only read; no product code changes.

Tables and columns: the specification's priority (``TABLES``; per table the scalars ``Lp_lf``, ``Ls_lf``, ``k_lf``,
``SRF``, ``SRF_p``, ``SRF_s``, ``Qp_peak``, ``Qs_peak`` it declares, then the curves ``Lp``, ``Ls``, ``Qp``, ``Qs``, ``k``
at the lowest declared anchor at or above 28 GHz and at 60 GHz -- 112 GHz where 60 is not declared). A column with fewer
than ``MIN_COLUMN_ROWS`` usable rows is left out and listed. Every column is modelled as the library models it today:
the study runs on a copy of the library's ``library.yaml`` taken at its first use (``EVIDENCE/library.yaml.pinned``,
SHA-256 in ``EVIDENCE/manifest.json``), through ``query.Library`` -- ``_fit_inputs`` (settings, ``feature_map``,
per-turns split, SRF in GHz), ``_composed_settings`` / ``_composed_fold`` / ``composed.ComposedGP`` for the ``ratio`` /
``resonance`` curves, ``gp.holdout`` and ``gp.calibration_scale`` for the calibration.

"The library without some rows" is the library's own ``Library`` with its dataset replaced, in memory, by the real
dataset (read from the library's cache, never written) less the rows at the held-out levels of one dim: the scaling
ranges, the per-turns split, the composed curves' parts and the domain guard then follow from the remaining rows exactly
as they would in a library that never measured the held-out ones (``check`` compares that with a real library built
without those rows). Once ``EVIDENCE/datasets/keys.json`` exists, the datasets are read from the copies there
(``study_dataset``): the library's stores may gain rows later, which changes the live datasets and their keys.

Subsets (rows held out), per table:

- edges: for every continuous dim with at least three levels over the table's rows (``nt_dim`` -- turns -- is never
  crossed): (a) the lowest level, (b) the highest, (c) the two lowest, (d) the two highest;
- leave-one-level-out (LOLO): every level of the main size dim (``outer_diameter_um``, else
  ``primary_outer_diameter_um``) in turn; its two end levels are the same subsets as that dim's edges (a) and (b).

Jobs (each in a spawned worker, BLAS capped at ``--threads``; results in ``EVIDENCE/jobs/``):

- ``fit``: one subset and one column group (scalars, curves): every column fitted on the subset as ``Library.models``
  fits it (a composed curve on the subset's own base-scalar / SRF models, refitted in the job when they are not in
  the group), predicted at every held-out row (mean and unfloored sigma); per column the training box and, per held-out
  row, the guard's criterion 2, its criterion 3 (the hull of the row's turns level over the dims that vary there)
  alone, and the same hull without the crossed dim; for the resonance column (``SRF``, else ``SRF_p``) the
  above-the-sweep vote ``query._predict`` takes (3 of the 5 nearest measured rows without a resonance);
- ``cal`` / ``cal_comp``: today's calibration of one column on the full table and on every edge subset -- ``gp.holdout``
  (5 x 20 %), or one seed of ``Library._composed_fold`` for a composed curve -- with each held-out row's (mu, sigma)
  recorded (``recording``), so the k_scale of an edge's model comes from its own training rows and nothing leaks.

``report`` (``collect`` / ``analyse_column`` / ``summary``): per column today's calibration on the full table (protocol
(i), checked against the library's cached one), the LOLO scale (``gp.calibration_scale`` over the z of every level; also
over the interior levels only), (ii) the interior levels, and every edge row with its distance (level steps and fraction
of the training range along the crossed dim and every other dim), error, the query's interval under the edge's own
calibration and under the LOLO scale (the LOLO z of the held-out rows themselves left out), relative sigma, physicality,
the guard verdicts and, for a curve, the N-113 rule. The specification's rule per (table, column, side) on the rows of
every crossed dim together (``verdict``), and besides per crossed dim, below the minimum without the centre offset (0 is
its physical floor) and on the rows inside the training box in every other dim; Q2 also at stricter ceilings
(``q2_ceilings``). Writes the JSON (floats to five digits), three figures, the Markdown tables of RESULT_CN.md and every
scored edge row to ``EVIDENCE/edge_rows.csv.gz``.

Usage:
  extrapolation_study.py plan     LIBRARY EVIDENCE [--tables ..] [--columns ..]
  extrapolation_study.py run      LIBRARY EVIDENCE [--tables ..] [--columns ..] [--only REGEX] [--threads 2] [--workers-max 16]
                                  [--max-hours H] [--deadline "YYYY-MM-DD HH:MM"] [--order longest|priority]
                                  [--max-threads-file FILE] [--label NAME]
  extrapolation_study.py estimate LIBRARY EVIDENCE [--tables ..] [--columns ..] [--budget-hours 4] [--workers 12] [--write-plan F]
  extrapolation_study.py report   LIBRARY EVIDENCE OUT_JSON FIGDIR [--md MD] [--tables ..] [--columns ..]
  extrapolation_study.py n112     LIBRARY EVIDENCE OUT_JSON
  extrapolation_study.py check    LIBRARY EVIDENCE --tables TABLE --subset DIM=LEVEL[+LEVEL] --columns A,B
``--columns`` takes ``column`` or ``table:column`` items (a curve family name such as ``Lp`` stands for both anchors);
``--plan-file`` (every command) restricts the tables and columns to a plan JSON's ``columns`` (``estimate --write-plan``
writes one); its ``analyse`` entries name the columns ``report`` analyses where a run was cut short. A run uses at most
``THREADS_BEFORE // threads`` workers until both pre-fit exit files (``PREFIT_SIGNAL``) exist and hold 0, then
``THREADS_AFTER // threads``; its thread cap file (``--max-threads-file``, default ``EVIDENCE/max_threads``: a number),
when present, lowers that. Long runs: detached, output in ``EVIDENCE/logs/``, the heartbeat in ``EVIDENCE/progress.log``
(at most every 4 min).
"""

from __future__ import annotations

import argparse
import collections
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
from scipy.optimize import nnls
from scipy.spatial import ConvexHull, QhullError
from threadpoolctl import threadpool_limits

from ic_opt.library import composed, dataset, domain, gp, manifest, query
from ic_opt.site import HostLimits

HERE = Path(__file__).resolve().parent
SPEC = "docs/refactor/N115_EXTRAPOLATION_STUDY_SPEC.md"
JOB_VERSION = 1
MEMORY_GB = 32.0                                   # per worker; a fit of 1576 rows over 5 dims needs about 0.42 GB
TABLES = ("xfm_bs_m10", "ind_sym_m10", "xfm_ms_m10", "xfm_bs_m10_hf", "ind_sym_m10_hf", "xfm_ms_m10_hf",
          "xfm_bs_ap", "ind_sym_ap", "xfm_ms_ap", "xfm_bs_ap_hf", "ind_sym_ap_hf", "xfm_ms_ap_hf",
          "xfm_bs_m10_ct", "xfm_bs_m10_ct_hf", "xfm_bs_ap_ct", "xfm_bs_ap_ct_hf")
SCALARS = ("Lp_lf", "Ls_lf", "k_lf", "SRF", "SRF_p", "SRF_s", "Qp_peak", "Qs_peak")
CURVES = ("Lp", "Ls", "Qp", "Qs", "k")
CLASS = {"Lp_lf": "L_lf", "Ls_lf": "L_lf", "k_lf": "k_lf", "SRF": "SRF", "SRF_p": "SRF", "SRF_s": "SRF",
         "Qp_peak": "Q_peak", "Qs_peak": "Q_peak", "Lp": "L_curve", "Ls": "L_curve", "Qp": "Q_curve", "Qs": "Q_curve",
         "k": "k_curve"}
CLASSES = ("L_lf", "k_lf", "SRF", "Q_peak", "L_curve", "Q_curve", "k_curve")
EDGES = {"a": ("below", 1), "b": ("above", 1), "c": ("below", 2), "d": ("above", 2)}
SIDES = ("below", "above")
RULE = {"p90_factor": 2.0, "p90_floor": 0.05, "coverage_min": 0.85, "rel_sigma_max": domain.DEFAULT_SIGMA_REL_MAX}
WIDE_REFERENCE = 0.10                              # an in-range (interior LOLO) p90 above this makes the rule's error limit loose
DIM_TYPES = {"size": ("outer_diameter_um", "primary_outer_diameter_um"), "secondary_size": ("secondary_outer_diameter_um",),
             "width": ("width_um", "primary_width_um", "secondary_width_um"), "offset": ("center_spacing_um",),
             "spacing": ("spacing_um", "secondary_spacing_um")}
MIN_COLUMN_ROWS = 25
LOW_ANCHOR_MIN, HIGH_ANCHOR, HIGH_FALLBACK = 28.0, 60.0, 112.0
PREFIT_DIR = Path("/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/prewarm_20261014")
PREFIT_SIGNAL = ("prefit_A2.exit", "prefit_B.exit")
THREADS_BEFORE, THREADS_AFTER = 24, 40            # in all, before / after the pre-fit signal (40: the user's allowance from 14:13)
HEARTBEAT_S = 240


# -- the manifest the study runs on ---------------------------------------------------------------------------------------

def pinned_manifest(root: Path, evidence: Path) -> tuple[manifest.Library, Path]:
    """Today's library.yaml, copied into EVIDENCE at the first use and read from that copy ever after (a later change of the
    library's own file does not change what "today" means for this study; ``manifest.json`` records both SHA-256)."""
    copy = evidence / "library.yaml.pinned"
    live = (root / manifest.MANIFEST).read_text(encoding="utf-8")
    if not copy.is_file():
        evidence.mkdir(parents=True, exist_ok=True)
        copy.write_text(live, encoding="utf-8")
        _write_json(evidence / "manifest.json", {"library": str(root), "sha256": _sha(live), "copied": time.strftime("%Y-%m-%d %H:%M:%S")})
    text = copy.read_text(encoding="utf-8")
    if _sha(text) != json.loads((evidence / "manifest.json").read_text(encoding="utf-8"))["sha256"]:
        raise SystemExit(f"{copy} does not match the SHA-256 recorded in {evidence / 'manifest.json'}")
    if _sha(live) != _sha(text):
        print(f"[manifest] NOTE: {root / manifest.MANIFEST} changed since the study pinned it; the study keeps {copy}", flush=True)
    return manifest.Library.model_validate(yaml.safe_load(text)), copy


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


SNAPSHOT = "datasets"                               # EVIDENCE/datasets: the datasets the study ran on, copied from the library's cache


def study_dataset(root: Path, evidence: Path, name: str, lib_m: manifest.Library) -> dataset.Dataset:
    """The dataset the study ran on: its copy in EVIDENCE/datasets when one was taken (``snapshot``: the library's stores may gain
    rows afterwards, which changes the live dataset and its key), else the library's own (``dataset.build``, read from its cache)."""
    keys_file = evidence / SNAPSHOT / "keys.json"
    if keys_file.is_file():
        keys = json.loads(keys_file.read_text(encoding="utf-8"))
        if name in keys:
            path = evidence / SNAPSHOT / f"dataset-{name}-{keys[name]}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            return dataset._load(path, lib_m.strata[name], name, "snapshot", keys[name], data["generations"])
    return dataset.build(root, name, library=lib_m, cache_dir=evidence / "cache")


# -- the plan ---------------------------------------------------------------------------------------------------------------

def anchors(stratum: manifest.Stratum) -> tuple[float, float]:
    """(the lowest declared anchor at or above 28 GHz, 60 GHz -- 112 where 60 is not declared)."""
    declared = sorted({f for c in CURVES if c in stratum.quantities for f in stratum.quantities[c].anchors_ghz})
    low = min(f for f in declared if f >= LOW_ANCHOR_MIN)
    return low, HIGH_ANCHOR if HIGH_ANCHOR in declared else HIGH_FALLBACK


def table_columns(stratum: manifest.Stratum, ds: dataset.Dataset) -> tuple[list[str], dict[str, str], dict[str, int]]:
    """(the study's columns of the table in priority order, the ones left out and why, every declared column's usable rows)."""
    low, high = anchors(stratum)
    wanted = [q for q in SCALARS if q in stratum.quantities]
    wanted += [f"{c}@{f:g}" for c in CURVES if c in stratum.quantities for f in (low, high)]
    usable = {c: len(ds.usable(c)) for c in ds.columns}
    cols, left = [], {}
    for c in wanted:
        if usable[c] < MIN_COLUMN_ROWS:
            left[c] = f"{usable[c]} usable rows (< {MIN_COLUMN_ROWS})"
        else:
            cols.append(c)
    return cols, left, usable


def family(column: str) -> str:
    return column.split("@")[0]


def column_wanted(filters: list[tuple[str | None, str]] | None, table: str, column: str) -> bool:
    if not filters:
        return True
    return any((t is None or t == table) and (c == column or c == family(column)) for t, c in filters)


def parse_columns(text: str | None) -> list[tuple[str | None, str]] | None:
    if not text:
        return None
    out = []
    for item in text.split(","):
        t, _, c = item.rpartition(":")
        out.append((t or None, c))
    return out


def make_plan(root: Path, evidence: Path, tables: list[str], filters=None, plan_file: Path | None = None) -> tuple[manifest.Library, dict]:
    lib_m, copy = pinned_manifest(root, evidence)
    plan_doc = json.loads(plan_file.read_text(encoding="utf-8")) if plan_file else {}
    chosen = plan_doc.get("columns")
    analyse = plan_doc.get("analyse", {})                # columns whose jobs ran but are not analysed (a run cut at its deadline)
    plans = {}
    for t in tables:
        if chosen is not None and t not in chosen:
            continue
        stratum = lib_m.strata[t]
        ds = study_dataset(root, evidence, t, lib_m)
        cols, left, usable = table_columns(stratum, ds)
        cols = [c for c in cols if column_wanted(filters, t, c) and (chosen is None or c in chosen[t])]
        x = ds.matrix()
        levels = {d: sorted(set(x[:, i].tolist())) for i, d in enumerate(ds.dims)}
        size_dim = "outer_diameter_um" if "outer_diameter_um" in ds.dims else "primary_outer_diameter_um"
        crossed = [d for d in ds.dims if d != ds.nt_dim and len(levels[d]) >= 3]
        subsets: dict[str, dict] = {}

        def add(dim: str, vals: list[float], role: list, subsets=subsets) -> None:
            name = f"{dim}={'+'.join(f'{v:g}' for v in vals)}"
            s = subsets.setdefault(name, {"name": name, "dim": dim, "levels": list(vals), "roles": []})
            s["roles"].append(role)

        for d in crossed:
            lv = levels[d]
            add(d, [lv[0]], ["edge", d, "a"])
            add(d, [lv[-1]], ["edge", d, "b"])
            add(d, lv[:2], ["edge", d, "c"])
            add(d, lv[-2:], ["edge", d, "d"])
        for v in levels[size_dim]:
            add(size_dim, [v], ["lolo", size_dim, v])
        for s in subsets.values():
            held = set(s["levels"])
            j = ds.dims.index(s["dim"])
            s["held_rows"] = int(sum(1 for v in x[:, j] if v in held))
        keys = [tuple(round(r.coords[d], 9) for d in ds.dims) for r in ds.rows]
        plans[t] = {"dataset": ds, "rows": len(ds.rows), "key": ds.key, "dims": list(ds.dims), "nt_dim": ds.nt_dim,
                    "columns": cols, "left_out": left, "usable": usable, "levels": levels, "size_dim": size_dim,
                    "crossed": crossed, "subsets": subsets, "duplicates": len(keys) - len(set(keys)),
                    "composed": {c: stratum.quantities[family(c)].model != "direct" for c in cols},
                    "analyse": [c for c in cols if t not in analyse or c in analyse[t]], "manifest": str(copy)}
    return lib_m, plans


def groups_of(p: dict) -> dict[str, list[str]]:
    return {g: cols for g, cols in (("scalars", [c for c in p["columns"] if "@" not in c]),
                                    ("curves", [c for c in p["columns"] if "@" in c])) if cols}


def is_edge(s: dict) -> bool:
    return any(r[0] == "edge" for r in s["roles"])


def jobs_for(t: str, p: dict, root: Path, evidence: Path) -> list[dict]:
    base = {"root": str(root), "cache": str(evidence / "cache"), "manifest": p["manifest"], "table": t}
    out = []
    for g, cols in groups_of(p).items():
        for name, s in p["subsets"].items():
            out.append({**base, "kind": "fit", "name": f"{t}/fit/{g}/{name}", "dim": s["dim"], "levels": s["levels"],
                        "columns": cols, "group": g})
    cal_subsets = [None] + [s for s in p["subsets"].values() if is_edge(s)]
    for s in cal_subsets:
        sname = "full" if s is None else s["name"]
        where = {"dim": None, "levels": []} if s is None else {"dim": s["dim"], "levels": s["levels"]}
        for c in p["columns"]:
            if p["composed"][c]:
                for seed in gp.SEEDS:
                    out.append({**base, **where, "kind": "cal_comp", "name": f"{t}/cal_comp/{sname}/{c}/s{seed}", "column": c,
                                "seed": int(seed), "subset": sname})
            else:
                out.append({**base, **where, "kind": "cal", "name": f"{t}/cal/{sname}/{c}", "column": c, "subset": sname})
    return out


# -- workers ------------------------------------------------------------------------------------------------------------------

_STATE: dict = {"full": {}, "manifests": {}}


def _pinned(path: str) -> manifest.Library:
    if path not in _STATE["manifests"]:
        _STATE["manifests"][path] = manifest.Library.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
    return _STATE["manifests"][path]


def subset(full: dataset.Dataset, dim: str, levels: list[float]) -> dataset.Dataset:
    """The dataset less the rows at ``levels`` of ``dim`` (the same Row objects, in the same order)."""
    held = {float(v) for v in levels}
    rows = [r for r in full.rows if r.coords[dim] not in held]
    tag = "+".join(f"{v:g}" for v in levels)
    return dataset.Dataset(full.stratum, list(full.dims), full.nt_dim, list(full.columns), rows, dict(full.generations),
                           dict(full.excluded), "off", f"{full.key}|minus {dim}={tag}")


def _library(job: dict, threads: int, *, keep: bool = False) -> tuple[query.Library, dataset.Dataset]:
    """The library's own handle, the pinned manifest, its dataset replaced by the job's subset (calibration off: nothing of
    ``Library``'s own caching is used; fits and calibrations are the job's). ``keep``: one handle per subset, reused (the
    main process sizing the jobs; a worker builds a fresh one per job)."""
    sub_key = (job["root"], job["table"], job["manifest"], job.get("dim"), tuple(job.get("levels") or ()))
    if keep and sub_key in _STATE.setdefault("libs", {}):
        return _STATE["libs"][sub_key]
    lib = query.Library(job["root"], calibrate=False, cache_dir=job["cache"], limits=HostLimits(max_threads=threads, max_memory_gb=MEMORY_GB))
    lib.manifest = _pinned(job["manifest"])
    key = (job["root"], job["table"], job["manifest"])
    if key not in _STATE["full"]:
        _STATE["full"][key] = study_dataset(Path(job["root"]), Path(job["cache"]).parent, job["table"], lib.manifest)
    full = _STATE["full"][key]
    lib._datasets[job["table"]] = full if job.get("dim") is None else subset(full, job["dim"], job["levels"])
    if keep:
        _STATE["libs"][sub_key] = (lib, full)
    return lib, full


def _units(x: np.ndarray, settings: dict) -> tuple[int, float]:
    """(GP fits, cost units) of one StratumGP fit on ``x``: per_nt one GP per turns level with >= MIN_NT_SAMPLES rows; a unit
    is (rows / 1000)^3 x (inputs + 2), what a likelihood gradient costs."""
    n_in = len(settings["dims"])
    if settings.get("feature_map"):
        n_in = len(gp.FEATURE_MAPS[settings["feature_map"]][1](list(settings["dims"]), x[:1])[0]) if len(x) else n_in
        return 1, (len(x) / 1000) ** 3 * (n_in + 2)
    if settings.get("nt_mode") == "per_nt" and settings.get("nt_dim"):
        j = list(settings["dims"]).index(settings["nt_dim"])
        count = collections.Counter(np.round(x[:, j]).astype(int).tolist())
        big = [n for n in count.values() if n >= gp.StratumGP.MIN_NT_SAMPLES]
        return len(big), sum((n / 1000) ** 3 * (n_in - 1 + 2) for n in big)
    return 1, (len(x) / 1000) ** 3 * (n_in + 2)


def _guard(guards: dict, x: np.ndarray, ds: dataset.Dataset, ranges: dict) -> domain.DomainGuard:
    key = hashlib.sha256(np.ascontiguousarray(x).tobytes()).hexdigest()
    if key not in guards:
        guards[key] = domain.DomainGuard(x, ds.dims, ranges, nt_dim=ds.nt_dim)
    return guards[key]


def _other_hull(guard: domain.DomainGuard, level, dims_idx: list[int]):
    """The hull of the level's training rows over ``dims_idx`` (scaled as the guard scales them); for one dim its range; None
    when degenerate."""
    pts = guard._x[guard._level_rows(level)][:, dims_idx]
    scaled = (pts - guard._lo[dims_idx]) / (guard._hi[dims_idx] - guard._lo[dims_idx])
    if len(dims_idx) == 1:
        return ("range", float(scaled.min()), float(scaled.max()))
    try:
        return ConvexHull(scaled)
    except (QhullError, ValueError):
        return None


def hull_verdicts(guard: domain.DomainGuard, xh: np.ndarray, j: int) -> dict:
    """Per held-out row: refused by criterion 2 (its turns level has too few training rows); refused by criterion 3 alone
    (outside the hull of its level over the dims varying there, or off a dim fixed there -- ``DomainGuard.check``'s third
    block, criterion 1 skipped); and refused by the same test without the crossed dim ``j`` (its own hull; for a single
    remaining dim its range, where qhull cannot build one). None where criterion 2 refuses."""
    n = len(xh)
    c2, c3, c3o = [False] * n, [None] * n, [None] * n
    groups: dict = collections.defaultdict(list)
    for i in range(n):
        if guard._nt_idx is None:
            groups[domain._GLOBAL].append(i)
            continue
        v = xh[i, guard._nt_idx]
        if abs(v - round(v)) > 1e-9 or int((guard._levels == round(v)).sum()) < guard.min_per_level:
            c2[i] = True
        else:
            groups[round(v)].append(i)
    for level, idx in groups.items():
        varying, fixed, hull = guard._hull(level)
        pts = xh[idx]
        ok = np.ones(len(idx), dtype=bool)
        for k, v in fixed.items():
            ok &= np.abs(pts[:, k] - v) <= 1e-9
        if varying:
            if hull is None:
                ok[:] = False
            else:
                sc = (pts[:, varying] - guard._lo[varying]) / (guard._hi[varying] - guard._lo[varying])
                ok &= domain.in_hull(hull, sc)
        ok_o = np.ones(len(idx), dtype=bool)
        for k, v in fixed.items():
            if k != j:
                ok_o &= np.abs(pts[:, k] - v) <= 1e-9
        others = [k for k in varying if k != j]
        if others:
            h = _other_hull(guard, level, others)
            sc = (pts[:, others] - guard._lo[others]) / (guard._hi[others] - guard._lo[others])
            if h is None:
                ok_o[:] = False
            elif isinstance(h, tuple):
                ok_o &= (sc[:, 0] >= h[1] - 1e-9) & (sc[:, 0] <= h[2] + 1e-9)
            else:
                ok_o &= domain.in_hull(h, sc)
        for a, i in enumerate(idx):
            c3[i], c3o[i] = not bool(ok[a]), not bool(ok_o[a])
    return {"c2": c2, "c3": c3, "c3_other": c3o}


def sweep_votes(lib: query.Library, t: str, full: dataset.Dataset, held_idx: list[int], column: str) -> dict:
    """``query._predict``'s above-the-sweep answer for ``column`` (SRF, SRF_p) at each held-out row, among the subset's rows."""
    ds = lib.dataset(t)
    votes, lower = [], []
    for i in held_idx:
        near = query._nearest_rows(lib, t, {d: full.rows[i].coords[d] for d in ds.dims}, 5)
        above = [r for r in near if r.values.get(column) is None]
        votes.append(len(above) >= query.ABOVE_SWEEP_VOTES)
        lower.append(min(r.stop_hz for r in above) if len(above) >= query.ABOVE_SWEEP_VOTES else None)
    return {"column": column, "above_sweep": votes, "lower_bound_hz": lower}


def fit_job(job: dict, threads: int) -> dict:
    """One subset, one column group: every column fitted as ``Library.models`` fits it (calibration aside: k_scale and the
    sigma floor act only at prediction, so ``report`` applies them from the calibration jobs), predicted at every held-out
    row."""
    t0 = time.perf_counter()
    lib, full = _library(job, threads)
    t, dim = job["table"], job["dim"]
    held = {float(v) for v in job["levels"]}
    held_idx = [i for i, r in enumerate(full.rows) if r.coords[dim] in held]
    xh = full.matrix([full.rows[i] for i in held_idx])
    j = full.dims.index(dim)
    ds = lib.dataset(t)
    fitted, fits, guards, out = {}, [], {}, {}

    def direct(q: str):
        if q not in fitted:
            _ds, rows, x, y, settings = lib._fit_inputs(t, q)
            s0 = time.perf_counter()
            model = gp.StratumGP(**settings).fit(x, y)
            n_gp, units = _units(x, settings)
            fits.append({"column": q, "part": "direct", "rows": len(rows), "gps": n_gp, "units": units,
                         "own": q in job["columns"], "seconds": time.perf_counter() - s0})
            fitted[q] = (model, rows, x, settings)
        return fitted[q]

    for c in job["columns"]:
        s0 = time.perf_counter()
        try:
            if lib._plan(t, c) is None:
                model, rows, x, settings = direct(c)
            else:
                parts = [direct(q) for q in lib._parts(t, c)]
                s = lib._composed_settings(t, c)
                _ds, rows, x, y, settings = lib._fit_inputs(t, c)
                model = composed.ComposedGP(kind=s["kind"], f0=s["f0"], dims=s["dims"], ranges=s["ranges"], nt_dim=s["nt_dim"],
                                            part=s["part"], names=s["names"])
                model.attach(parts[0][0], parts[1][0] if len(parts) > 1 else None, n_train=max(len(pp[1]) for pp in parts))
                s1 = time.perf_counter()
                model.fit(x, y, lf_y=ds.values(s["names"]["lf"], rows))
                n_gp, units = _units(x, s["part"])
                fits.append({"column": c, "part": s["kind"], "rows": len(rows), "gps": n_gp, "units": units, "own": True,
                             "seconds": time.perf_counter() - s1})
            with np.errstate(all="ignore"):
                mu, sigma = model.predict(xh, floor=False)
            guard = _guard(guards, x, ds, settings["ranges"])
            out[c] = {"mu": mu.tolist(), "sigma": sigma.tolist(), "available": model.available(xh).tolist(),
                      "log_target": bool(model.log_target), "n_train": len(rows), "box_min": x.min(axis=0).tolist(),
                      "box_max": x.max(axis=0).tolist(), "hull": hull_verdicts(guard, xh, j),
                      "seconds": time.perf_counter() - s0}
        except Exception:  # noqa: BLE001 -- a column that cannot be fitted on this subset is recorded and the job goes on
            out[c] = {"error": traceback.format_exc()}
    res = next((q for q in ("SRF", "SRF_p") if q in job["columns"]), None)
    return {"held": held_idx, "columns": out, "fits": fits, "rows_train": len(ds.rows),
            "above_sweep": sweep_votes(lib, t, full, held_idx, res) if res else None, "seconds": time.perf_counter() - t0}


@contextmanager
def recording(cls):
    """Record each held-out batch a hold-out predicts: the rows ``cls.predict`` gets (called once per fold by ``gp.holdout``
    and ``composed.holdout_fold``) and the (mu, sigma) handed to ``gp.prediction_bounds``; both pass through unchanged and are
    restored on exit."""
    rec: dict = {"x": [], "mu": [], "sigma": [], "log_target": []}
    bounds_orig, predict_orig = gp.prediction_bounds, cls.predict

    def bounds(mu, sigma, *, log_target, k):
        rec["mu"].append(np.asarray(mu, dtype=float).tolist())
        rec["sigma"].append(np.asarray(sigma, dtype=float).tolist())
        rec["log_target"].append(bool(log_target))
        return bounds_orig(mu, sigma, log_target=log_target, k=k)

    def predict(self, x, *, floor=True):
        rec["x"].append(np.asarray(x, dtype=float).tolist())
        return predict_orig(self, x, floor=floor)

    gp.prediction_bounds, cls.predict = bounds, predict
    try:
        yield rec
    finally:
        gp.prediction_bounds, cls.predict = bounds_orig, predict_orig


def _row_index(full: dataset.Dataset) -> dict[tuple, int]:
    return {tuple(round(r.coords[d], 9) for d in full.dims): i for i, r in enumerate(full.rows)}


def _scored_rows(full: dataset.Dataset, xs: list[list[list[float]]]) -> list[int]:
    index = _row_index(full)
    return [index[tuple(round(v, 9) for v in row)] for batch in xs for row in batch]


def cal_job(job: dict, threads: int) -> dict:
    """Today's calibration of a direct column on the subset: the library's ``gp.holdout`` on ``_fit_inputs``."""
    t0 = time.perf_counter()
    lib, full = _library(job, threads)
    _ds, rows, x, y, settings = lib._fit_inputs(job["table"], job["column"])
    with recording(gp.StratumGP) as rec:
        report = gp.holdout(x, y, **settings)
    if len(rec["x"]) != len(gp.SEEDS) or len(rec["mu"]) != len(gp.SEEDS):
        raise RuntimeError(f"expected {len(gp.SEEDS)} recorded folds, got {len(rec['x'])} / {len(rec['mu'])}")
    gps, units = 0, 0.0
    for seed in gp.SEEDS:
        _test, train = gp.split(len(y), seed)
        g, u = _units(x[train], settings)
        gps, units = gps + g, units + u
    return {"report": report, "k_scale": gp.calibration_scale(report), "rows": _scored_rows(full, rec["x"]),
            "mu": [v for b in rec["mu"] for v in b], "sigma": [v for b in rec["sigma"] for v in b], "log_target": bool(settings["log_target"]),
            "n_rows": len(rows), "settings": {k: v for k, v in settings.items() if k != "ranges"}, "ranges": settings["ranges"],
            "gps": gps, "units": units, "seconds": time.perf_counter() - t0}


def cal_comp_job(job: dict, threads: int) -> dict:
    """One seed of today's calibration of a composed curve on the subset: ``Library._composed_fold`` (every part refitted)."""
    t0 = time.perf_counter()
    lib, full = _library(job, threads)
    t, c = job["table"], job["column"]
    with recording(composed.ComposedGP) as rec:
        fold = lib._composed_fold(t, c, job["seed"])
    if len(rec["x"]) != 1 or len(rec["mu"]) != 1:
        raise RuntimeError(f"expected one recorded fold, got {len(rec['x'])} / {len(rec['mu'])}")
    s = lib._composed_settings(t, c)
    x, y, lf_y, srf_y = lib._composed_data(t, c)
    positive = np.flatnonzero(np.isfinite(y) & (y > 0))
    test_i, _ = gp.split(len(positive), job["seed"])
    held = np.zeros(len(y), dtype=bool)
    held[positive[test_i]] = True
    gps, units = 0, 0.0
    for settings, rows in ((s["lf"], np.isfinite(lf_y) & ~held), (s["srf"], None if srf_y is None else np.isfinite(srf_y) & ~held),
                           (s["part"], np.isfinite(y) & ~held)):
        if settings is not None and rows is not None:
            g, u = _units(x[rows], settings)
            gps, units = gps + g, units + u
    return {"fold": fold, "rows": _scored_rows(full, rec["x"]), "mu": rec["mu"][0], "sigma": rec["sigma"][0],
            "kind": s["kind"], "gps": gps, "units": units, "seconds": time.perf_counter() - t0}


RUNNERS = {"fit": fit_job, "cal": cal_job, "cal_comp": cal_comp_job}


def run_job(job: dict, path: str, threads: int) -> dict:
    """In a worker: BLAS capped, run the job, write its result file (atomic), return a short summary."""
    t0 = time.time()
    try:
        with threadpool_limits(limits=threads):
            result = RUNNERS[job["kind"]](job, threads)
    except Exception:  # noqa: BLE001 -- a failed job is reported and the run goes on
        return {"name": job["name"], "ok": False, "error": traceback.format_exc(), "seconds": round(time.time() - t0, 1)}
    _write_json(Path(path), _clean({"version": JOB_VERSION, "job": job, "threads": threads, "pid": os.getpid(), **result}))
    return {"name": job["name"], "ok": True, "seconds": round(result["seconds"], 1)}


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp", delete=False, encoding="utf-8") as f:
        f.write(json.dumps(data))
    os.replace(f.name, path)


def _clean(value):
    """JSON without NaN / inf (None instead) and numpy scalars as Python numbers."""
    if isinstance(value, float | np.floating):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if isinstance(value, np.ndarray):
        return _clean(value.tolist())
    return value


def job_path(evidence: Path, name: str) -> Path:
    return evidence / "jobs" / (re.sub(r"[^A-Za-z0-9_.=+-]", "_", name.replace("/", "__").replace("@", "_at_")) + ".json")


def _same_job(a: dict, b: dict) -> bool:
    return json.loads(json.dumps(a)) == json.loads(json.dumps(b))


def load_result(evidence: Path, job: dict) -> dict | None:
    path = job_path(evidence, job["name"])
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if data.get("version") == JOB_VERSION and _same_job(data.get("job"), job) else None


# -- cost: what a job will take ------------------------------------------------------------------------------------------------

def job_units(job: dict) -> tuple[int, float]:
    """(GP fits, cost units) a job will run, from its subset's rows and the columns' settings -- nothing is fitted."""
    lib, _full = _library(job, 1, keep=True)
    t = job["table"]
    if job["kind"] == "fit":
        gps, units, seen = 0, 0.0, set()

        def add(x, settings):
            nonlocal gps, units
            g, u = _units(x, settings)
            gps, units = gps + g, units + u

        for c in job["columns"]:
            needed = ([c] if lib._plan(t, c) is None else [*lib._parts(t, c), c])
            for q in needed:
                if q in seen:
                    continue
                seen.add(q)
                _ds, _rows, x, _y, settings = lib._fit_inputs(t, q)
                add(x, lib._composed_settings(t, q)["part"] if q == c and lib._plan(t, c) is not None else settings)
        return gps, units
    if job["kind"] == "cal":
        _ds, _rows, x, y, settings = lib._fit_inputs(t, job["column"])
        gps, units = 0, 0.0
        for seed in gp.SEEDS:
            _test, train = gp.split(len(y), seed)
            g, u = _units(x[train], settings)
            gps, units = gps + g, units + u
        return gps, units
    s = lib._composed_settings(t, job["column"])
    x, y, lf_y, srf_y = lib._composed_data(t, job["column"])
    positive = np.flatnonzero(np.isfinite(y) & (y > 0))
    test_i, _ = gp.split(len(positive), job["seed"])
    held = np.zeros(len(y), dtype=bool)
    held[positive[test_i]] = True
    gps, units = 0, 0.0
    for settings, rows in ((s["lf"], np.isfinite(lf_y) & ~held), (s["srf"], None if srf_y is None else np.isfinite(srf_y) & ~held),
                           (s["part"], np.isfinite(y) & ~held)):
        if settings is not None and rows is not None:
            g, u = _units(x[rows], settings)
            gps, units = gps + g, units + u
    return gps, units


def cost_model(evidence: Path, jobs: list[dict]) -> dict:
    """seconds = a x GP fits + b x units, fitted (non-negative least squares) on every timed fit of the finished jobs: the
    fit jobs' single fits and the calibration jobs' totals. A default before any job ran."""
    rows, secs = [], []
    for j in jobs:
        r = load_result(evidence, j)
        if r is None:
            continue
        if j["kind"] == "fit":
            for f in r["fits"]:
                rows.append((f["gps"], f["units"]))
                secs.append(f["seconds"])
        else:
            rows.append((r["gps"], r["units"]))
            secs.append(r["seconds"])
    if len(rows) < 3:
        return {"a": 1.0, "b": 30.0, "n": len(rows), "default": True}
    a_mat, s_vec = np.array(rows, dtype=float), np.array(secs, dtype=float)
    (a, b), _res = nnls(a_mat, s_vec)
    pred = a_mat @ np.array([a, b])
    return {"a": float(a), "b": float(b), "n": len(rows), "default": False,
            "median_ratio": float(np.median(s_vec / np.maximum(pred, 1e-9))), "total_actual": float(s_vec.sum()), "total_pred": float(pred.sum())}


def predict_seconds(units: dict[str, tuple[int, float]], model: dict, overhead: float = 1.0) -> dict[str, float]:
    return {name: overhead + model["a"] * g + model["b"] * u for name, (g, u) in units.items()}


def pack(seconds: list[float], workers: int) -> float:
    """Wall time of the jobs started longest first on ``workers`` workers (each takes the next job when free)."""
    loads = [0.0] * max(1, workers)
    for s in sorted(seconds, reverse=True):
        loads[loads.index(min(loads))] += s
    return max(loads)


# -- run --------------------------------------------------------------------------------------------------------------------------

def prefit_done() -> bool:
    """Both pre-fit exit files exist and hold 0 (the coordinator's signal, 2026-10-14)."""
    for name in PREFIT_SIGNAL:
        path = PREFIT_DIR / name
        try:
            if path.read_text(encoding="utf-8").strip() != "0":
                return False
        except OSError:
            return False
    return True


def allowed_workers(evidence: Path, threads: int, workers_max: int, cap_file: Path | None = None) -> tuple[int, str]:
    budget = THREADS_AFTER if prefit_done() else THREADS_BEFORE
    why = f"{budget} threads ({'pre-fit done' if budget == THREADS_AFTER else 'pre-fit running'})"
    cap_file = cap_file or evidence / "max_threads"
    if cap_file.is_file():
        try:
            cap = int(cap_file.read_text(encoding="utf-8").strip())
            if cap < budget:
                budget, why = cap, f"{cap} threads ({cap_file.name})"
        except ValueError:
            pass
    return max(1, min(workers_max, budget // threads)), why


def _log(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def selected_jobs(args, plans: dict) -> list[dict]:
    root, evidence = args.library.resolve(), args.evidence.resolve()
    jobs = [j for t, p in plans.items() for j in jobs_for(t, p, root, evidence)]
    if getattr(args, "only", None):
        jobs = [j for j in jobs if re.search(args.only, j["name"])]
    return jobs


def cmd_run(args) -> None:
    t_start = time.time()
    root, evidence = args.library.resolve(), args.evidence.resolve()
    _lib_m, plans = make_plan(root, evidence, args.tables.split(","), parse_columns(args.columns), args.plan_file)
    jobs = selected_jobs(args, plans)
    todo = [j for j in jobs if load_result(evidence, j) is None]
    model = cost_model(evidence, jobs)
    units = {j["name"]: job_units(j) for j in todo}
    pred = predict_seconds(units, model)
    if args.order == "priority":                      # fit jobs first, then each column's calibrations in column order, longest first
        rank = {(t, c): i for t, p in plans.items() for i, c in enumerate(p["columns"])}
        order = sorted(todo, key=lambda j: (-1 if j["kind"] == "fit" else rank[(j["table"], j["column"])], -pred[j["name"]]))
    else:
        order = sorted(todo, key=lambda j: -pred[j["name"]])
    label = args.label or "run"
    progress = evidence / "progress.log"
    print(f"[plan] {label}: {len(jobs)} jobs ({len(jobs) - len(todo)} done before); to run {len(todo)}, predicted "
          f"{sum(pred.values()) / 3600:.2f} h of fitting (model a {model['a']:.2f} s/GP, b {model['b']:.2f} s/unit, n {model['n']})", flush=True)
    for t, p in plans.items():
        print(f"[plan]   {t}: rows {p['rows']}, columns {p['columns']}, crossed {p['crossed']}, subsets {len(p['subsets'])}, "
              f"jobs {sum(j['table'] == t for j in todo)}", flush=True)
    deadline = t_start + args.max_hours * 3600 if args.max_hours else None
    if args.deadline:
        until = time.mktime(time.strptime(args.deadline, "%Y-%m-%d %H:%M"))
        deadline = min(deadline, until) if deadline else until
    ctx = multiprocessing.get_context("spawn")
    done = failed = 0
    failures: list[str] = []
    actual, predicted = 0.0, 0.0
    queue = list(order)
    stopped = False
    last_beat = 0.0
    with ProcessPoolExecutor(max_workers=args.workers_max, mp_context=ctx) as pool:
        running: dict = {}
        while queue or running:
            cap, why = allowed_workers(evidence, args.threads, args.workers_max, args.max_threads_file)
            if deadline and time.time() > deadline and queue:
                print(f"[run] deadline passed: {len(queue)} jobs not started", flush=True)
                _log(progress, f"{time.strftime('%Y-%m-%d %H:%M:%S')} {label}: deadline passed, {len(queue)} jobs not started")
                queue, stopped = [], True
            while queue and len(running) < cap:
                j = queue.pop(0)
                running[pool.submit(run_job, j, str(job_path(evidence, j["name"])), args.threads)] = j
            finished, _ = wait(list(running), timeout=20, return_when=FIRST_COMPLETED)
            for fut in finished:
                j = running.pop(fut)
                s = fut.result()
                done += 1
                if s["ok"]:
                    actual += s["seconds"]
                    predicted += pred[j["name"]]
                    print(f"[job] {done}/{len(order)} {j['name']} {s['seconds']:.1f} s (pred {pred[j['name']]:.1f}; "
                          f"elapsed {(time.time() - t_start) / 60:.1f} min; {why})", flush=True)
                else:
                    failed += 1
                    failures.append(j["name"])
                    print(f"[job] FAILED {j['name']}\n{s['error']}", flush=True)
            now = time.time()
            if now - last_beat >= HEARTBEAT_S or not (queue or running):
                last_beat = now
                speed = actual / predicted if predicted > 0 else 1.0
                rest = [pred[j["name"]] * speed for j in queue] + [pred[j["name"]] * speed for j in running.values()]
                eta = now + pack(rest, cap) if rest else now
                _log(progress, f"{time.strftime('%Y-%m-%d %H:%M:%S')} {label}: jobs done {done}/{len(order)} (failed {failed}), "
                               f"running {len(running)} on {cap} workers x {args.threads} threads ({why}); "
                               f"estimated end {time.strftime('%H:%M', time.localtime(eta))}")
    wall = (time.time() - t_start) / 60
    print(f"[run] {label}: {len(order)} jobs, {failed} failed, wall {wall:.1f} min{' (stopped at the deadline)' if stopped else ''}", flush=True)
    _write_json(evidence / f"run_summary_{label}_{time.strftime('%Y%m%dT%H%M%S')}.json", {
        "label": label, "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t_start)), "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tables": list(plans), "columns": args.columns, "only": args.only, "plan_file": str(args.plan_file) if args.plan_file else None,
        "threads": args.threads, "workers_max": args.workers_max, "omp_num_threads": os.environ.get("OMP_NUM_THREADS"),
        "planned": len(jobs), "done_before": len(jobs) - len(todo), "ran": done, "failed": failures, "stopped_at_deadline": stopped,
        "fit_seconds": actual, "wall_minutes": round(wall, 2)})
    if failures:
        raise SystemExit(f"{len(failures)} jobs failed")


def cmd_plan(args) -> None:
    root, evidence = args.library.resolve(), args.evidence.resolve()
    _lib_m, plans = make_plan(root, evidence, args.tables.split(","), parse_columns(args.columns), args.plan_file)
    for t, p in plans.items():
        print(f"== {t}: rows {p['rows']} key {p['key']} dims {p['dims']} nt {p['nt_dim']} duplicates {p['duplicates']}")
        print(f"   columns {p['columns']}")
        print(f"   left out {p['left_out']}")
        print(f"   size dim {p['size_dim']} levels {[f'{v:g}' for v in p['levels'][p['size_dim']]]}")
        print(f"   crossed {[(d, len(p['levels'][d])) for d in p['crossed']]}")
        for name, s in p["subsets"].items():
            print(f"     {name}: held {s['held_rows']} roles {s['roles']}")
        jobs = jobs_for(t, p, root, evidence)
        print(f"   jobs {len(jobs)}: " + ", ".join(f"{k} {v}" for k, v in collections.Counter(j['kind'] for j in jobs).items()))


def cmd_estimate(args) -> None:
    """The cost of every planned job not run yet from the cost model of the jobs run so far; the wall time on ``--workers``;
    with ``--budget-hours``, the drop order of the specification applied per table (columns from the end of the column list,
    the costliest table first), then tables from the end of the table list, until the wall estimate fits; ``--write-plan``
    keeps the chosen tables and columns for ``run --plan-file``."""
    root, evidence = args.library.resolve(), args.evidence.resolve()
    _lib_m, plans = make_plan(root, evidence, args.tables.split(","), parse_columns(args.columns), args.plan_file)
    jobs = selected_jobs(args, plans)
    model = cost_model(evidence, jobs)
    print(f"[estimate] model: seconds = {model['a']:.3f} x GP fits + {model['b']:.3f} x units (n {model['n']}, "
          f"actual/predicted median {model.get('median_ratio', float('nan')):.2f})", flush=True)
    todo = [j for j in jobs if load_result(evidence, j) is None]
    units = {j["name"]: job_units(j) for j in todo}
    pred = predict_seconds(units, model)
    by_tc: dict = collections.defaultdict(float)
    for j in todo:
        cols = [j["column"]] if j["kind"] != "fit" else j["columns"]
        for c in cols:                                     # a fit job's cost shared out over its columns by their own fits
            by_tc[(j["table"], c)] += pred[j["name"]] / len(cols)
    total = sum(pred.values())
    print(f"[estimate] {len(todo)} jobs to run: {total / 3600:.2f} h of fitting; on {args.workers} workers about "
          f"{pack(list(pred.values()), args.workers) / 3600:.2f} h wall", flush=True)
    for t, p in plans.items():
        line = ", ".join(f"{c} {by_tc[(t, c)] / 60:.0f}" for c in p["columns"])
        print(f"[estimate]   {t}: {sum(by_tc[(t, c)] for c in p['columns']) / 3600:.2f} h ({line} min)", flush=True)
    if not args.budget_hours:
        return
    keep = {t: list(p["columns"]) for t, p in plans.items()}

    def wall() -> float:
        secs = []
        for j in todo:
            cols = [j["column"]] if j["kind"] != "fit" else j["columns"]
            kept = [c for c in cols if c in keep.get(j["table"], [])]
            if kept:
                secs.append(pred[j["name"]] * len(kept) / len(cols))
        return pack(secs, args.workers) * args.inefficiency

    dropped: list[str] = []
    while wall() > args.budget_hours * 3600:
        # the column at the end of the list on the table where it costs most; scalars only once no table has a curve left
        candidates = [(t, cols[-1]) for t, cols in keep.items() if cols and any("@" in c for c in cols)]
        if not candidates:
            candidates = [(t, cols[-1]) for t, cols in keep.items() if len(cols) > 1]
        if candidates:
            t, c = max(candidates, key=lambda tc: by_tc[tc])
            keep[t].pop()
            dropped.append(f"{t}:{c}")
            continue
        t = next(t for t in reversed(TABLES) if keep.get(t))
        dropped.append(f"{t}:*")
        keep[t] = []
    w = wall()
    print(f"[estimate] budget {args.budget_hours} h on {args.workers} workers (x{args.inefficiency} for the tail): "
          f"{w / 3600:.2f} h wall with {sum(len(v) for v in keep.values())} (table, column) pairs; dropped {len(dropped)}: {dropped}", flush=True)
    if args.write_plan:
        _write_json(args.write_plan, {"columns": {t: v for t, v in keep.items() if v}, "dropped": dropped, "model": model,
                                      "budget_hours": args.budget_hours, "workers": args.workers, "wall_hours": w / 3600,
                                      "written": time.strftime("%Y-%m-%d %H:%M:%S")})
        print(f"[estimate] wrote {args.write_plan}", flush=True)


def cmd_check(args) -> None:
    """The in-memory subset against a real library without those rows (the method of N-110's scratch libraries): a library
    under EVIDENCE/scratch whose one part store holds the real observations less the held-out rows (spec.json and sims linked
    to the real store) and whose library.yaml is the pinned manifest of that stratum only; ``Library.models`` (calibration
    off) fits the columns there, and their predictions at the held-out rows are compared with the study's fit job."""
    root, evidence = args.library.resolve(), args.evidence.resolve()
    lib_m, _copy = pinned_manifest(root, evidence)
    t = args.tables
    dim, _, text = args.subset.partition("=")
    levels = [float(v) for v in text.split("+")]
    stratum = lib_m.strata[t]
    (part,) = stratum.parts
    dest = evidence / "scratch" / t / args.subset
    icopt = dest / part.store / ".icopt"
    icopt.mkdir(parents=True, exist_ok=True)
    held = set(levels)
    lines = (root / part.store / ".icopt" / "observations.jsonl").read_text(encoding="utf-8").splitlines()
    kept = [ln for ln in lines if ln.strip() and float(json.loads(ln)["params"][dim]) not in held]
    (icopt / "observations.jsonl").write_text("\n".join(kept) + "\n", encoding="utf-8")
    for name in ("spec.json", "sims"):
        if not (icopt / name).is_symlink():
            (icopt / name).symlink_to(root / part.store / ".icopt" / name)
    doc = lib_m.model_dump(mode="json")
    doc["strata"] = {t: doc["strata"][t]}
    (dest / manifest.MANIFEST).write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    cols = args.columns.split(",")
    lim = HostLimits(max_threads=2, max_memory_gb=MEMORY_GB)
    with threadpool_limits(limits=2):
        scratch = query.Library(dest, calibrate=False, cache_dir=dest / ".cache", limits=lim)
        models = scratch.models(t, cols, workers=1, threads=2)
        _lib_m, plans = make_plan(root, evidence, [t])
        p = plans[t]
        out = {"table": t, "subset": args.subset, "scratch": str(dest), "rows_scratch": len(scratch.dataset(t).rows),
               "rows_subset": p["rows"] - p["subsets"][args.subset]["held_rows"], "columns": {}}
        for c in cols:
            group = "curves" if "@" in c else "scalars"
            job = next(j for j in jobs_for(t, p, root, evidence) if j["name"] == f"{t}/fit/{group}/{args.subset}")
            res = load_result(evidence, job)
            if res is None:
                out["columns"][c] = {"status": "fit job not run"}
                continue
            full = p["dataset"]
            xh = full.matrix([full.rows[i] for i in res["held"]])
            mu, sigma = models[c].gp.predict(xh, floor=False)
            a, s = _arr(res["columns"][c]["mu"]), _arr(res["columns"][c]["sigma"])
            same = bool(np.array_equal(a, mu, equal_nan=True) and np.array_equal(s, sigma, equal_nan=True))
            with np.errstate(all="ignore"):
                diff = float(np.nanmax(np.abs(a / mu - 1))) if len(a) else 0.0
            out["columns"][c] = {"identical": same, "max_rel_diff_mu": diff, "rows": len(a)}
            print(f"[check] {t} {args.subset} {c}: {'identical' if same else f'max relative difference {diff:.2e}'} ({len(a)} rows)", flush=True)
    _write_json(evidence / f"check_{t}_{args.subset.replace('=', '_')}.json", out)


# -- analysis -------------------------------------------------------------------------------------------------------------------

def _arr(values) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in values], dtype=float)


def intervals(y, mu, sigma, log_target: bool, k: float, floor: float) -> dict:
    """The calibrated 2-sigma interval as a query gives it (sigma floored at ``floor`` x |mu|, widened by ``k``) and the
    unfloored one: inside or not, relative width (hi - lo) / |mu|, the query's relative sigma."""
    with np.errstate(all="ignore"):
        s_fl = np.maximum(sigma, floor * np.abs(mu))
        lo, hi = gp.prediction_bounds(mu, s_fl, log_target=log_target, k=2.0 * k)
        lo_u, hi_u = gp.prediction_bounds(mu, sigma, log_target=log_target, k=2.0 * k)
        lo_0, hi_0 = gp.prediction_bounds(mu, sigma, log_target=log_target, k=2.0)
        return {"inside": (y >= lo) & (y <= hi), "inside_unfloored": (y >= lo_u) & (y <= hi_u), "inside_before": (y >= lo_0) & (y <= hi_0),
                "width": (hi - lo) / np.abs(mu), "rel_sigma": s_fl / np.abs(mu), "lo": lo, "hi": hi}


def zscores(y, mu, sigma, log_target: bool) -> np.ndarray:
    """|z| against the unfloored sigma, as ``gp.holdout`` and ``composed.holdout_fold`` compute it."""
    with np.errstate(all="ignore"):
        if log_target:
            return np.abs(np.log(y) - np.log(mu)) / np.maximum(sigma / np.maximum(np.abs(mu), 1e-300), 1e-300)
        return np.abs(y - mu) / np.maximum(sigma, 1e-300)


def k_from(z: np.ndarray) -> float | None:
    z = z[np.isfinite(z)]
    return gp.calibration_scale({"z": z.tolist()}) if len(z) else None


def unphysical(column: str, mu: np.ndarray) -> np.ndarray:
    """L, Q and SRF must be positive, k inside (0, 1)."""
    with np.errstate(invalid="ignore"):
        if CLASS[family(column)] in ("k_lf", "k_curve"):
            return ~((mu > 0) & (mu < 1))
        return ~(mu > 0)


def errors(rel: np.ndarray) -> dict:
    rel = rel[np.isfinite(rel)]
    if not len(rel):
        return {"n": 0}
    return {"n": len(rel), "median_rel": float(np.median(rel)), "p90_rel": float(np.quantile(rel, 0.9)), "max_rel": float(rel.max())}


def calibration(results: dict, t: str, sname: str, c: str, is_composed: bool, y_all: np.ndarray) -> dict | None:
    """Today's calibration of a column on a subset (full or an edge), from its job(s): k_scale, the sigma floor (the held-out
    median relative error) and the held-out rows' (y, mu, unfloored sigma)."""
    if is_composed:
        rs = [results.get(f"{t}/cal_comp/{sname}/{c}/s{seed}") for seed in gp.SEEDS]
        if any(r is None for r in rs):
            return None
        folds = []
        for r in rs:
            f = dict(r["fold"])
            f["rel"], f["z"] = _arr(f["rel"]).tolist(), _arr(f["z"]).tolist()
            folds.append(f)
        report = composed.merge(folds)
        rows = [i for r in rs for i in r["rows"]]
        mu, sigma, lt = _arr([v for r in rs for v in r["mu"]]), _arr([v for r in rs for v in r["sigma"]]), True
    else:
        r = results.get(f"{t}/cal/{sname}/{c}")
        if r is None:
            return None
        report = dict(r["report"])
        report["z"] = _arr(report["z"]).tolist()
        rows, mu, sigma, lt = r["rows"], _arr(r["mu"]), _arr(r["sigma"]), r["log_target"]
    rows = np.array(rows, dtype=int)

    def num(v) -> float:
        return float("nan") if v is None else float(v)

    median = num(report["median_rel"])                   # NaN when the hold-out scored no row (a level without its sub-model)
    return {"k": gp.calibration_scale(report), "floor": median if math.isfinite(median) else 0.0, "rows": rows, "y": y_all[rows],
            "mu": mu, "sigma": sigma, "log_target": lt, "n_scored": int(report["n_scored"]), "median_rel": median,
            "p90_rel": num(report["p90_rel"]), "coverage_before": num(report["coverage_2sigma"])}


def held_predictions(res: dict | None, c: str, y_all: np.ndarray) -> dict | None:
    """A fit job's predictions of one column at the held-out rows (all of them; ``scored``: measured, answered, finite)."""
    if res is None or c not in res["columns"] or "error" in res["columns"][c]:
        return None
    col = res["columns"][c]
    held = np.array(res["held"], dtype=int)
    mu, sigma = _arr(col["mu"]), _arr(col["sigma"])
    avail = np.array(col["available"], dtype=bool)
    y = y_all[held]
    scored = avail & np.isfinite(mu) & np.isfinite(sigma) & np.isfinite(y)
    if col["log_target"]:
        scored &= ~(y <= 0)                                # a log-space model neither fits nor scores a value <= 0 (as its calibration)
    return {"held": held, "y": y, "mu": mu, "sigma": sigma, "available": avail, "scored": scored, "log_target": col["log_target"],
            "box_min": np.array(col["box_min"]), "box_max": np.array(col["box_max"]), "hull": col["hull"], "n_train": col["n_train"]}


def steps_outside(v: float, lo: float, hi: float, levels: list[float]) -> tuple[str, int, float]:
    """(side, level steps, fraction of the training range) of value ``v`` against the training box [lo, hi] of one dim: the
    table's levels of that dim between the box and ``v`` (``v``'s own included)."""
    span = hi - lo
    if v < lo - 1e-9:
        return "below", sum(1 for x in levels if v - 1e-9 <= x < lo - 1e-9), (lo - v) / span if span > 0 else math.inf
    if v > hi + 1e-9:
        return "above", sum(1 for x in levels if hi + 1e-9 < x <= v + 1e-9), (v - hi) / span if span > 0 else math.inf
    return "inside", 0, 0.0


def analyse_column(t: str, p: dict, c: str, results: dict, lib_m: manifest.Library) -> dict:
    """Everything the questions need about one (table, column): today's calibration on the full table (i); the levels of the
    main size dim left out in turn (LOLO: k_scale from all levels, and from the interior ones; (ii) is the interior); every
    edge hold-out's rows with their distance, error, intervals under today's scale (the edge's own calibration) and the
    LOLO scale, relative sigma, physicality, guard verdicts and, for a curve, the N-113 rule."""
    ds = p["dataset"]
    y_all = ds.values(c) / query.fit_unit(c)
    is_comp = p["composed"][c]
    group = "curves" if "@" in c else "scalars"
    x_all = ds.matrix()
    size, levels = p["size_dim"], p["levels"]
    out: dict = {"column": c, "class": CLASS[family(c)], "composed": is_comp, "usable": int(np.isfinite(y_all).sum())}
    cal_full = calibration(results, t, "full", c, is_comp, y_all)
    if cal_full is None:
        return {**out, "complete": False}
    k_today, floor_today = cal_full["k"], cal_full["floor"]
    out["i"] = {"k_scale": k_today, "floor": floor_today, "n": cal_full["n_scored"], "median_rel": cal_full["median_rel"],
                "p90_rel": cal_full["p90_rel"], "coverage_before": cal_full["coverage_before"]}
    # LOLO over the main size dim (its end levels are the size dim's edges (a) / (b): the same fits)
    lolo, lolo_failed = [], []
    for v in levels[size]:
        hp = held_predictions(results.get(f"{t}/fit/{group}/{size}={v:g}"), c, y_all)
        if hp is None:                                      # no result, or the column could not be fitted on that subset
            lolo_failed.append(f"{v:g}")
            continue
        s = hp["scored"]
        lolo.append({"level": v, "interior": levels[size][0] < v < levels[size][-1], "rows": hp["held"][s], "y": hp["y"][s],
                     "mu": hp["mu"][s], "sigma": hp["sigma"][s], "lt": hp["log_target"], "unavailable": int((~hp["available"]).sum())})
    if not lolo:
        return {**out, "complete": False, "missing": "every LOLO fold"}
    z_lolo = {L["level"]: zscores(L["y"], L["mu"], L["sigma"], L["lt"]) for L in lolo}
    k_lolo = k_from(np.concatenate(list(z_lolo.values()))) or 1.0
    interior = [L for L in lolo if L["interior"]]
    k_lolo_int = k_from(np.concatenate([z_lolo[L["level"]] for L in interior])) if interior else None
    rel_int = np.concatenate([np.abs(L["mu"] - L["y"]) / np.abs(L["y"]) for L in interior]) if interior else np.array([])
    p90_int = errors(rel_int).get("p90_rel")
    out["lolo"] = {"k_scale": k_lolo, "k_scale_interior": k_lolo_int, "interior": errors(rel_int), "failed_levels": lolo_failed,
                   "levels": {f"{L['level']:g}": {**errors(np.abs(L["mu"] - L["y"]) / np.abs(L["y"])), "interior": L["interior"],
                                                  "unavailable": L["unavailable"]} for L in lolo}}
    # Q3 (i): the random hold-out under both scales; (ii) the interior levels under both (and the LOLO scale without the level)
    lt_full = cal_full["log_target"]
    i_today = intervals(cal_full["y"], cal_full["mu"], cal_full["sigma"], lt_full, k_today, floor_today)
    i_lolo = intervals(cal_full["y"], cal_full["mu"], cal_full["sigma"], lt_full, k_lolo, floor_today)
    q3 = {"i": {"n": len(cal_full["y"]), "coverage_today": float(np.mean(i_today["inside"])), "coverage_lolo": float(np.mean(i_lolo["inside"])),
                "coverage_today_unfloored": float(np.mean(i_today["inside_unfloored"])), "coverage_lolo_unfloored": float(np.mean(i_lolo["inside_unfloored"])),
                "width_today_median": float(np.median(i_today["width"])), "width_lolo_median": float(np.median(i_lolo["width"])),
                "width_ratio_median": float(np.median(i_lolo["width"] / i_today["width"]))}}
    if interior:
        y = np.concatenate([L["y"] for L in interior])
        mu = np.concatenate([L["mu"] for L in interior])
        sg = np.concatenate([L["sigma"] for L in interior])
        lt = interior[0]["lt"]
        a, b = intervals(y, mu, sg, lt, k_today, floor_today), intervals(y, mu, sg, lt, k_lolo, floor_today)
        own = []                                            # the LOLO scale computed without the level it is applied to
        for L in interior:
            k_wo = k_from(np.concatenate([z for v, z in z_lolo.items() if v != L["level"]])) or 1.0
            own.append(intervals(L["y"], L["mu"], L["sigma"], lt, k_wo, floor_today)["inside"])
        q3["ii"] = {"n": len(y), "coverage_today": float(np.mean(a["inside"])), "coverage_lolo": float(np.mean(b["inside"])),
                    "coverage_lolo_without_level": float(np.mean(np.concatenate(own))),
                    "coverage_today_unfloored": float(np.mean(a["inside_unfloored"])), "coverage_lolo_unfloored": float(np.mean(b["inside_unfloored"])),
                    "width_ratio_median": float(np.median(b["width"] / a["width"]))}
    # edges
    rows: dict[str, list] = collections.defaultdict(list)
    edges = {}
    stratum = lib_m.strata[t]
    res_col = "SRF" if "SRF" in stratum.quantities else "SRF_p"
    margin_f0 = None
    if "@" in c:
        margin_f0 = stratum.quantities[family(c)].srf_margin * float(c.split("@")[1])     # GHz
    lolo_rows = np.concatenate([L["rows"] for L in lolo])
    lolo_z = np.concatenate(list(z_lolo.values()))
    for d in p["crossed"]:
        jd = ds.dims.index(d)
        for e in EDGES:
            lv = levels[d]
            held_levels = {"a": lv[:1], "b": lv[-1:], "c": lv[:2], "d": lv[-2:]}[e]
            sname = f"{d}={'+'.join(f'{v:g}' for v in held_levels)}"
            hp = held_predictions(results.get(f"{t}/fit/{group}/{sname}"), c, y_all)
            cal = calibration(results, t, sname, c, is_comp, y_all)
            if hp is None or cal is None:
                why = "no fit result" if hp is None else "no calibration result"
                res_ = results.get(f"{t}/fit/{group}/{sname}")
                if res_ is not None and "error" in res_["columns"].get(c, {}):
                    why = res_["columns"][c]["error"].strip().splitlines()[-1][:200]
                edges[f"{d}/{e}"] = {"complete": False, "subset": sname, "why": why}
                continue
            # the LOLO scale without the held-out rows' own z (leak-reduced): LOLO rows off the held levels of this dim
            keep = ~np.isin(x_all[lolo_rows, jd], held_levels)
            k_lolo_e = k_from(lolo_z[keep]) or 1.0
            s = hp["scored"]
            idx = np.flatnonzero(s)
            iv = intervals(hp["y"][s], hp["mu"][s], hp["sigma"][s], hp["log_target"], cal["k"], cal["floor"])
            iv_l = intervals(hp["y"][s], hp["mu"][s], hp["sigma"][s], hp["log_target"], k_lolo_e, cal["floor"])
            iv_lit = intervals(hp["y"][s], hp["mu"][s], hp["sigma"][s], hp["log_target"], k_lolo, cal["floor"])
            rel = np.abs(hp["mu"][s] - hp["y"][s]) / np.abs(hp["y"][s])
            edges[f"{d}/{e}"] = {"complete": True, "subset": sname, "held": len(hp["held"]), "scored": int(s.sum()),
                                 "unavailable": int((~hp["available"] & np.isfinite(hp["y"])).sum()), "k_scale": cal["k"], "floor": cal["floor"],
                                 "k_lolo_leak_reduced": k_lolo_e, "n_train": hp["n_train"]}
            for a, i in enumerate(idx):
                xr = x_all[hp["held"][i]]
                side, nst, frac = steps_outside(xr[jd], hp["box_min"][jd], hp["box_max"][jd], levels[d])
                other = [steps_outside(xr[k], hp["box_min"][k], hp["box_max"][k], levels[dk]) for k, dk in enumerate(ds.dims)
                         if dk != d and dk != ds.nt_dim]
                rows["row"].append(int(hp["held"][i]))
                rows["y"].append(float(hp["y"][i]))
                rows["mu"].append(float(hp["mu"][i]))
                rows["dim"].append(d)
                rows["edge"].append(e)
                rows["side"].append(side)
                rows["steps"].append(nst)
                rows["frac"].append(frac)
                rows["other_steps"].append(max([o[1] for o in other], default=0))
                rows["rel"].append(rel[a])
                rows["inside"].append(bool(iv["inside"][a]))
                rows["inside_unfloored"].append(bool(iv["inside_unfloored"][a]))
                rows["inside_before"].append(bool(iv["inside_before"][a]))
                rows["inside_lolo"].append(bool(iv_l["inside"][a]))
                rows["inside_lolo_literal"].append(bool(iv_lit["inside"][a]))
                rows["width"].append(iv["width"][a])
                rows["width_lolo"].append(iv_l["width"][a])
                rows["rel_sigma"].append(iv["rel_sigma"][a])
                rows["unphysical"].append(bool(unphysical(c, hp["mu"][s][a:a + 1])[0]))
                rows["c2"].append(bool(hp["hull"]["c2"][i]))
                rows["c3"].append(hp["hull"]["c3"][i])
                rows["c3_other"].append(hp["hull"]["c3_other"][i])
            # Q4 over every held-out row (measured or not): physicality, and for a curve the N-113 rule
            rows_all = {"n": len(hp["held"]), "answered": int(np.isfinite(hp["mu"]).sum()),
                        "unphysical": int(unphysical(c, hp["mu"][np.isfinite(hp["mu"])]).sum())}
            if margin_f0 is not None:
                rows_all.update(n113(results, t, sname, hp, res_col, margin_f0, ds, y_all, c, is_comp, lib_m, p))
            edges[f"{d}/{e}"]["all_rows"] = rows_all
    out["edges"] = edges
    out["rows"] = {k: v for k, v in rows.items()}
    out["p90_lolo_interior"] = p90_int
    out["q3"] = q3
    out["edges_failed"] = [k for k, v in edges.items() if not v.get("complete")]
    out["complete"] = True                                  # an edge that failed is listed and left out of the pools
    return out


def n113(results: dict, t: str, sname: str, hp: dict, res_col: str, margin_f0: float, ds: dataset.Dataset, y_all: np.ndarray,
         c: str, is_comp: bool, lib_m: manifest.Library, p: dict) -> dict:
    """The N-113 rule at an edge's held-out rows for a curve column: the resonance answer there (the subset's SRF -- SRF_p
    for an inductor table -- model, above the sweep where 3 of the 5 nearest training rows have no resonance) against
    srf_margin x f0; set against whether the row has the curve (its measured resonance above that) or not."""
    fit = results.get(f"{t}/fit/scalars/{sname}")
    if fit is None or res_col not in fit["columns"] or "error" in fit["columns"][res_col]:
        return {"n113": None}
    srf_y = ds.values(res_col) / 1e9
    r = held_predictions(fit, res_col, srf_y)
    cal = calibration(results, t, sname, res_col, False, srf_y)
    above = fit["above_sweep"]["above_sweep"] if fit.get("above_sweep") and fit["above_sweep"]["column"] == res_col else [False] * len(r["held"])
    lower = fit["above_sweep"]["lower_bound_hz"] if fit.get("above_sweep") and fit["above_sweep"]["column"] == res_col else [None] * len(r["held"])
    k = cal["k"] if cal else 1.0
    floor = cal["floor"] if cal else 0.0
    with np.errstate(all="ignore"):
        s_fl = np.maximum(r["sigma"], floor * np.abs(r["mu"]))
        lo, _hi = gp.prediction_bounds(r["mu"], s_fl, log_target=r["log_target"], k=2.0 * k)
    has_curve = np.isfinite(y_all[hp["held"]])
    measured_srf = srf_y[hp["held"]]                      # NaN: no resonance inside the sweep
    removed, near = np.zeros(len(hp["held"]), dtype=bool), np.zeros(len(hp["held"]), dtype=bool)
    for i in range(len(hp["held"])):
        if above[i] and (lower[i] is None or lower[i] / 1e9 >= margin_f0):
            continue
        if not np.isfinite(r["mu"][i]):
            removed[i] = True                              # the resonance cannot be answered: the curve fails closed
        elif r["mu"][i] <= margin_f0:
            removed[i] = True
        elif lo[i] <= margin_f0:
            near[i] = True
    below_res = np.isfinite(measured_srf) & (measured_srf <= margin_f0)    # truly above the resonance: no curve value
    mu = hp["mu"]
    unphys = unphysical(c, mu) & np.isfinite(mu)
    return {"n113": {"resonance": res_col, "threshold_ghz": margin_f0, "rows": len(removed),
                     "has_curve": int(has_curve.sum()), "has_curve_removed": int((has_curve & removed).sum()),
                     "above_resonance": int(below_res.sum()), "above_resonance_removed": int((below_res & removed).sum()),
                     "above_resonance_kept": int((below_res & ~removed).sum()), "near_resonance": int(near.sum()),
                     "unphysical_before": int(unphys.sum()), "unphysical_after": int((unphys & ~removed).sum()),
                     "unphysical_kept_on_curve_rows": int((unphys & ~removed & has_curve).sum())}}


def summarise_rows(rows: dict, mask: np.ndarray) -> dict:
    """Error, interval and sigma statistics of a set of edge rows."""
    n = int(mask.sum())
    if not n:
        return {"n": 0}
    rel = np.array(rows["rel"])[mask]
    out = {**errors(rel), "coverage": float(np.mean(np.array(rows["inside"])[mask])),
           "coverage_unfloored": float(np.mean(np.array(rows["inside_unfloored"])[mask])),
           "coverage_before": float(np.mean(np.array(rows["inside_before"])[mask])),
           "coverage_lolo": float(np.mean(np.array(rows["inside_lolo"])[mask])),
           "coverage_lolo_literal": float(np.mean(np.array(rows["inside_lolo_literal"])[mask])),
           "rel_sigma_median": float(np.median(np.array(rows["rel_sigma"])[mask])),
           "width_median": float(np.median(np.array(rows["width"])[mask])),
           "width_ratio_lolo_median": float(np.median(np.array(rows["width_lolo"])[mask] / np.array(rows["width"])[mask])),
           "frac_median": _finite_median(np.array(rows["frac"], dtype=float)[mask]),
           "frac_infinite": int((~np.isfinite(np.array(rows["frac"], dtype=float)[mask])).sum()),
           "unphysical": int(np.array(rows["unphysical"])[mask].sum()),
           "multi_dim": int((np.array(rows["other_steps"])[mask] > 0).sum()),
           "c2": int(np.array(rows["c2"])[mask].sum()),
           "c3": int(sum(1 for v, m in zip(rows["c3"], mask, strict=True) if m and v)),
           "c3_other": int(sum(1 for v, m in zip(rows["c3_other"], mask, strict=True) if m and v))}
    return out


def _finite_median(v: np.ndarray) -> float | None:
    v = v[np.isfinite(v)]
    return float(np.median(v)) if len(v) else None


def verdict(stats: dict[int, dict], p90_int: float | None) -> dict:
    """The specification's rule for one (table, column, side): extrapolable to d when at d the p90 relative error is at most
    max(2 x the LOLO interior p90, 5 %), the calibrated 2-sigma coverage at least 0.85 and no answer unphysical; the largest d
    with every distance up to it passing (0 when one step fails; None when one step has no rows)."""
    limit = max(RULE["p90_factor"] * p90_int, RULE["p90_floor"]) if p90_int is not None else RULE["p90_floor"]
    checks = {}
    for d in (1, 2):
        s = stats.get(d, {"n": 0})
        if not s.get("n"):
            checks[d] = None
            continue
        checks[d] = {"p90": s["p90_rel"] <= limit, "coverage": s["coverage"] >= RULE["coverage_min"], "physical": s["unphysical"] == 0}
        checks[d]["pass"] = all(checks[d].values())
    if checks[1] is None:
        best = None
    elif not checks[1]["pass"]:
        best = 0
    elif checks[2] is None:
        best = 1                                        # passes one step; two steps not measured
    else:
        best = 2 if checks[2]["pass"] else 1
    return {"limit": limit, "p90_lolo_interior": p90_int, "checks": {str(k): v for k, v in checks.items()}, "d": best,
            "measured_two": checks[2] is not None}


ROW_FIELDS = ("dim", "edge", "side", "steps", "frac", "other_steps", "y", "mu", "rel", "rel_sigma", "inside", "inside_lolo",
              "inside_unfloored", "unphysical", "c2", "c3", "c3_other")


def write_rows(f, t: str, c: str, ds: dataset.Dataset, rows: dict) -> None:
    """Every scored edge row of one (table, column): its measured row, distance, error, interval verdicts and guard verdicts."""
    for k in range(len(rows.get("rel", []))):
        r = ds.rows[rows["row"][k]]
        vals = [rows[name][k] for name in ROW_FIELDS]
        f.write(",".join([t, c, r.part, r.obs_id] + ["" if v is None else f"{v:.6g}" if isinstance(v, float) else str(v) for v in vals]) + "\n")


def collect(root: Path, evidence: Path, tables: list[str], filters, plan_file, rows_csv=None) -> dict:
    lib_m, plans = make_plan(root, evidence, tables, filters, plan_file)
    out: dict = {"tables": {}, "incomplete": [], "cost": {"jobs": 0, "fit_seconds": 0.0, "by_kind": collections.Counter()}}
    for t, p in plans.items():
        jobs = jobs_for(t, p, root, evidence)
        results = {}
        for j in jobs:
            r = load_result(evidence, j)
            if r is None:
                if j["kind"] == "fit" or j["column"] in p["analyse"]:
                    out["incomplete"].append(j["name"])
                continue
            results[j["name"]] = r
            out["cost"]["jobs"] += 1
            out["cost"]["fit_seconds"] += r["seconds"]
            out["cost"]["by_kind"][j["kind"]] += r["seconds"]
        cols = {}
        for c in p["analyse"]:
            a = analyse_column(t, p, c, results, lib_m)
            if not a.get("complete"):
                cols[c] = {k: v for k, v in a.items() if k != "rows"}
                continue
            rows = a.pop("rows")
            side = np.array(rows.get("side", []), dtype=object)
            steps = np.array(rows.get("steps", []), dtype=int)
            dims = np.array(rows.get("dim", []), dtype=object)
            pure = np.array(rows.get("other_steps", []), dtype=int) == 0
            pooled, per_dim = {}, {}
            for sd in SIDES:
                pooled[sd] = {d: summarise_rows(rows, (side == sd) & (steps == d)) for d in (1, 2)}
                pooled[sd]["3+"] = summarise_rows(rows, (side == sd) & (steps >= 3))
                pooled[sd]["pure"] = {d: summarise_rows(rows, (side == sd) & (steps == d) & pure) for d in (1, 2)}
                for dm in p["crossed"]:
                    per_dim.setdefault(dm, {})[sd] = {d: summarise_rows(rows, (side == sd) & (steps == d) & (dims == dm)) for d in (1, 2)}
            a["pooled"] = pooled
            a["per_dim"] = per_dim
            a["verdict"] = {sd: verdict({d: pooled[sd][d] for d in (1, 2)}, a["p90_lolo_interior"]) for sd in SIDES}
            a["verdict_per_dim"] = {dm: {sd: verdict({d: per_dim[dm][sd][d] for d in (1, 2)}, a["p90_lolo_interior"]) for sd in SIDES}
                                    for dm in p["crossed"]}
            a["verdict_pure"] = {sd: verdict({d: pooled[sd]["pure"][d] for d in (1, 2)}, a["p90_lolo_interior"]) for sd in SIDES}
            # below the minimum without the centre offset: its minimum is 0 (concentric windings), so no query lies below it
            no_offset = dims != "center_spacing_um"
            a["pooled_below_no_offset"] = {d: summarise_rows(rows, (side == "below") & (steps == d) & no_offset) for d in (1, 2)}
            a["verdict_below_no_offset"] = verdict(a["pooled_below_no_offset"], a["p90_lolo_interior"])
            a["edges_all"] = summarise_rows(rows, np.ones(len(rows.get("rel", [])), dtype=bool))
            a["q2"] = q2_rows(rows, a["p90_lolo_interior"])
            a["q2_rows"] = {"rel": rows.get("rel", []), "rel_sigma": rows.get("rel_sigma", []), "steps": rows.get("steps", []),
                            "side": rows.get("side", [])}
            if rows_csv is not None:
                write_rows(rows_csv, t, c, p["dataset"], rows)
            cols[c] = a
        out["tables"][t] = {"rows": p["rows"], "dataset_key": p["key"], "dims": p["dims"], "nt_dim": p["nt_dim"],
                            "size_dim": p["size_dim"], "crossed": p["crossed"], "levels": {d: p["levels"][d] for d in p["crossed"] + [p["size_dim"]]},
                            "subsets": {k: {"held_rows": v["held_rows"], "roles": v["roles"]} for k, v in p["subsets"].items()},
                            "columns_planned": p["columns"], "left_out": p["left_out"],
                            "declared_below_min": {c: n for c, n in p["usable"].items() if n < MIN_COLUMN_ROWS},
                            "columns": cols}
    out["cost"]["by_kind"] = dict(out["cost"]["by_kind"])
    out["q2_ceilings"] = q2_ceilings(out)
    out["library_calibration_check"] = library_calibration_check(root, evidence, lib_m, out)
    return out


def library_calibration_check(root: Path, evidence: Path, lib_m: manifest.Library, data: dict) -> dict:
    """The study's full-table calibration of every column against the one the library has cached for it (read only): the
    same k_scale and sigma floor show the study fitted the columns with today's settings."""
    lib = query.Library(root, cache_dir=evidence / "cache", limits=HostLimits(max_threads=1, max_memory_gb=MEMORY_GB))
    lib.manifest = lib_m
    same, differ, missing = 0, [], []
    for t in data["tables"]:
        lib._datasets[t] = study_dataset(root, evidence, t, lib_m)       # the study's rows and key, not the live store's
    for t, te in data["tables"].items():
        for c, a in te["columns"].items():
            if not a.get("complete"):
                continue
            if lib._plan(t, c) is not None:
                cal = lib._composed_calibration(t, c, compute=False)
            else:
                ds, _rows, x, y, settings = lib._fit_inputs(t, c)
                cal = lib._calibration(ds, c, x, y, settings, compute=False)
            if cal is None:
                missing.append(f"{t}:{c}")
                continue
            lib_floor = cal.get("median_rel")
            lib_floor = 0.0 if lib_floor is None or not math.isfinite(lib_floor) else lib_floor   # NaN: no scored row (the study floors at 0)
            if cal["k_scale"] == a["i"]["k_scale"] and lib_floor == a["i"]["floor"]:
                same += 1
                continue
            rel = max(abs(cal["k_scale"] / a["i"]["k_scale"] - 1), abs(lib_floor / a["i"]["floor"] - 1) if a["i"]["floor"] else abs(lib_floor))
            differ.append({"column": f"{t}:{c}", "library": [cal["k_scale"], lib_floor], "study": [a["i"]["k_scale"], a["i"]["floor"]],
                           "max_rel_diff": rel})
    return {"identical": same, "different": differ, "not_cached": missing,
            "max_rel_diff": max((x["max_rel_diff"] for x in differ), default=0.0)}


CEILINGS = (0.005, 0.01, 0.02, 0.05, 0.10, 0.15)


def q2_ceilings(data: dict) -> dict:
    """Q2 beyond the default ceiling: per column class and distance, for criterion 4 at other ceilings, the share of edge rows
    it would answer and, among those, the share whose error exceeds twice the column's in-range (interior LOLO) p90 -- and the
    rule's limit max(2 x that p90, 5 %)."""
    out: dict = {}
    for cls in CLASSES:
        rel, rs, st, lim = [], [], [], []
        for te in data["tables"].values():
            for a in te["columns"].values():
                if not a.get("complete") or a["class"] != cls or a.get("p90_lolo_interior") is None:
                    continue
                q = a.get("q2_rows") or {}
                n = len(q.get("rel", []))
                rel += list(q.get("rel", []))
                rs += list(q.get("rel_sigma", []))
                st += list(q.get("steps", []))
                lim += [a["p90_lolo_interior"]] * n
        if not rel:
            continue
        rel_a, rs_a, st_a, lim_a = (np.array(v, dtype=float) for v in (rel, rs, st, lim))
        out[cls] = {}
        for d in (1, 2):
            m = st_a == d
            if not m.any():
                continue
            cell = {"n": int(m.sum())}
            for c in CEILINGS:
                ok = m & (rs_a <= c)
                k = int(ok.sum())
                cell[f"{c:g}"] = {"answered": k / int(m.sum()),
                                  "wrong": float(np.mean(rel_a[ok] > RULE["p90_factor"] * lim_a[ok])) if k else None,
                                  "wrong_rule": float(np.mean(rel_a[ok] > np.maximum(RULE["p90_factor"] * lim_a[ok], RULE["p90_floor"]))) if k else None}
            out[cls][str(d)] = cell
    return out


def q2_rows(rows: dict, p90_int: float | None) -> dict:
    """Q2 on one column's edge rows: does the query's relative sigma follow the error? Per distance: rows with sigma / mu at
    most the ceiling (criterion 4 would answer) and how many of them miss by more than twice the in-range p90 (with and
    without the rule's 5 % floor); rows above the ceiling and how many of those were in fact within it; the rank
    correlation between relative sigma and error."""
    if not rows.get("rel"):
        return {}
    rel, rs, st = np.array(rows["rel"]), np.array(rows["rel_sigma"]), np.array(rows["steps"])
    out = {}
    for d in (1, 2):
        m = st == d
        if not m.any():
            continue
        ok = rs[m] <= RULE["rel_sigma_max"]
        e = rel[m]
        lim = RULE["p90_factor"] * p90_int if p90_int is not None else None
        lim_rule = max(lim, RULE["p90_floor"]) if lim is not None else RULE["p90_floor"]
        out[str(d)] = {"n": int(m.sum()), "c4_pass": int(ok.sum()),
                       "c4_pass_wrong": int((ok & (e > lim)).sum()) if lim is not None else None,
                       "c4_pass_wrong_rule": int((ok & (e > lim_rule)).sum()),
                       "c4_fail": int((~ok).sum()), "c4_fail_fine": int((~ok & (e <= lim_rule)).sum()),
                       "spearman": _spearman(rs[m], e)}
    return out


def _spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 5 or np.ptp(a) == 0 or np.ptp(b) == 0:
        return None
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def _rounded(value):
    """Floats to five significant digits (the JSON is a summary; the per-row numbers are in EVIDENCE/edge_rows.csv.gz)."""
    if isinstance(value, float):
        return float(f"{value:.5g}")
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_rounded(v) for v in value]
    return value


def cmd_report(args) -> None:
    root, evidence = args.library.resolve(), args.evidence.resolve()
    import gzip
    with gzip.open(evidence / "edge_rows.csv.gz", "wt", encoding="utf-8") as rows_csv:
        rows_csv.write(",".join(("table", "column", "part", "obs_id", *ROW_FIELDS)) + "\n")
        data = collect(root, evidence, args.tables.split(","), parse_columns(args.columns), args.plan_file, rows_csv)
    print(f"wrote {evidence / 'edge_rows.csv.gz'}", flush=True)
    data["summary"] = summary(data)
    data["runs"] = [json.loads(r.read_text(encoding="utf-8")) for r in sorted(evidence.glob("run_summary_*.json"))]
    n112_path = evidence / "n112_check.json"
    data["n112"] = n112_with_lolo(json.loads(n112_path.read_text(encoding="utf-8")) if n112_path.is_file() else None, data)
    data.update({"date": time.strftime("%Y-%m-%d %H:%M"), "library": str(root), "specification": SPEC, "rule": RULE,
                 "min_column_rows": MIN_COLUMN_ROWS, "manifest": json.loads((evidence / "manifest.json").read_text(encoding="utf-8"))})
    slim = json.loads(json.dumps(_clean(data)))
    for te in slim["tables"].values():                     # the per-row Q2 arrays go to the figure only
        for a in te["columns"].values():
            a.pop("q2_rows", None)
    args.out.write_text(json.dumps(_rounded(slim), separators=(",", ":")), encoding="utf-8")
    print(f"wrote {args.out}; incomplete jobs: {len(data['incomplete'])}", flush=True)
    figures(data, args.figdir)
    if args.md:
        args.md.write_text(markdown(data), encoding="utf-8")
        print(f"wrote {args.md}", flush=True)


N112 = {"table": "xfm_bs_m10",
        "params": {"primary_outer_diameter_um": 51.0, "secondary_outer_diameter_um": 51.0, "primary_width_um": 4.0,
                   "secondary_width_um": 4.0, "center_spacing_um": 0.0},
        "project": Path("/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n112_emx_verify/xfm_m10_base"),
        "obs": "obs_0001"}


def measured_values(work: Path, device, stratum: manifest.Stratum) -> dict:
    """One sNp's columns under the stratum's definitions -- ``dataset._row``'s loop, on the library's own functions."""
    m = dataset._measure(work, device, stratum)
    q, values = m.q, {}
    for name, rule in stratum.quantities.items():
        if name in manifest.CURVES:
            for f in rule.anchors_ghz:
                values[f"{name}@{f:g}"] = dataset._anchor_value(q, name, rule, f * 1e9, m.start, m.stop)
        elif name in manifest.PEAKS and rule.band_ghz is not None:
            values[name] = dataset._peak(q.freqs, q.curves[name.split("_")[0]], rule.band_ghz * 1e9, q.scalars.get("SRF"))
        else:
            values[name] = q.scalars.get(name)
    return values


def cmd_n112(args) -> None:
    """The independent check: N-112's EMX-measured transformer (xfm_bs_m10, outer diameters 51 / 51 um, 15 % below the
    table's minimum) against the full-table models the library has cached (loaded read only; the guard bypassed), every
    study column whose model is cached. The truth is the run's sNp measured with the stratum's own definitions. ``report``
    adds the LOLO scale's interval."""
    root, evidence = args.library.resolve(), args.evidence.resolve()
    lib_m, _copy = pinned_manifest(root, evidence)
    t = N112["table"]
    lib = query.Library(root, cache_dir=evidence / "cache", limits=HostLimits(max_threads=2, max_memory_gb=MEMORY_GB))
    lib.manifest = lib_m
    stratum = lib_m.strata[t]
    ds = lib.dataset(t)
    cols, _left, _usable = table_columns(stratum, ds)
    device = dataset.part_devices(root, stratum)[stratum.parts[0].store]
    work = N112["project"] / ".icopt" / "sims" / N112["obs"] / "em" / device.id
    truth = measured_values(work, device, stratum)
    x = np.array([[N112["params"][d] for d in ds.dims]])
    cached = lib.cached(t)
    out = {"table": t, "params": N112["params"], "snp": str(work), "truth": {c: truth.get(c) for c in cols}, "columns": {}}
    box = ds.matrix()
    out["box"] = {d: [float(box[:, i].min()), float(box[:, i].max())] for i, d in enumerate(ds.dims)}
    with threadpool_limits(limits=2):
        for c in cols:
            if cached.get(c) != "model":
                out["columns"][c] = {"status": f"not cached ({cached.get(c)})"}
                continue
            m = lib.model(t, c)
            try:
                m.guard.check(N112["params"])
                guard = {"ok": True}
            except domain.OutOfDomainError as exc:
                guard = {"ok": False, "criterion": exc.criterion, "reason": exc.reason}
            mu, sigma = m.gp.predict(x)
            _mu, sigma_u = m.gp.predict(x, floor=False)
            lo, hi = m.gp.predict_bounds(x, 2.0)
            scale = query.fit_unit(c)
            y = truth.get(c)
            out["columns"][c] = {"value": float(mu[0]) * scale, "lo": float(lo[0]) * scale, "hi": float(hi[0]) * scale,
                                 "sigma": float(sigma[0]) * scale, "sigma_unfloored": float(sigma_u[0]) * scale,
                                 "rel_sigma": float(sigma[0] / abs(mu[0])), "k_scale": m.calibration["k_scale"],
                                 "floor": m.calibration.get("median_rel", 0.0), "log_target": bool(m.gp.log_target), "guard": guard,
                                 "truth": y, "rel_error": None if y is None else abs(float(mu[0]) * scale - y) / abs(y),
                                 "inside": None if y is None else bool(lo[0] * scale <= y <= hi[0] * scale)}
            print(f"[n112] {c}: {out['columns'][c]['value']:.6g} [{out['columns'][c]['lo']:.6g}, {out['columns'][c]['hi']:.6g}] "
                  f"truth {y} inside {out['columns'][c]['inside']}", flush=True)
    _write_json(args.out, _clean(out))
    print(f"wrote {args.out}", flush=True)


def n112_with_lolo(n112: dict | None, data: dict) -> dict | None:
    """The N-112 point under the LOLO scale too (its k from the study's LOLO folds of xfm_bs_m10, the floor unchanged)."""
    if not n112:
        return None
    te = data["tables"].get(n112["table"], {})
    out = dict(n112)
    rows = {}
    for c, v in n112["columns"].items():
        a = te.get("columns", {}).get(c, {})
        k_l = (a.get("lolo") or {}).get("k_scale")
        row = dict(v)
        if "value" in v and k_l is not None:
            mu, s = np.array([v["value"]]), np.array([max(v["sigma_unfloored"], v["floor"] * abs(v["value"]))])
            lo, hi = gp.prediction_bounds(mu, s, log_target=v["log_target"], k=2.0 * k_l)
            row.update({"k_lolo": k_l, "lo_lolo": float(lo[0]), "hi_lolo": float(hi[0]),
                        "inside_lolo": None if v["truth"] is None else bool(lo[0] <= v["truth"] <= hi[0])})
        rows[c] = row
    out["columns"] = rows
    return out


def _median(values) -> float | None:
    v = [x for x in values if x is not None and math.isfinite(x)]
    return float(np.median(v)) if v else None


def summary(data: dict) -> dict:
    """Per column class: the rule's verdicts (pooled over the crossed dims, per dim, on rows inside the box in every other
    dim), Q1's medians over the class's (table, column) pairs, Q2's counts, Q3's medians, Q4's counts."""
    out: dict = {}
    for cls in CLASSES:
        members = [(t, c, a) for t, te in data["tables"].items() for c, a in te["columns"].items() if a.get("complete") and a["class"] == cls]
        if not members:
            continue
        e: dict = {"pairs": len(members), "tables": sorted({t for t, _c, _a in members}, key=TABLES.index)}
        for sd in SIDES:
            vs = {f"{t}:{c}": a["verdict"][sd]["d"] for t, c, a in members}
            measured = [v for v in vs.values() if v is not None]
            few = [k for k, (t, c, a) in zip(vs, members, strict=True) if 0 < a["pooled"][sd][1].get("n", 0) < 10]
            per_dim = [a["verdict_per_dim"][dm][sd]["d"] for _t, _c, a in members for dm in a["verdict_per_dim"]]
            pure = [a["verdict_pure"][sd]["d"] for _t, _c, a in members]
            no_off = [a["verdict_below_no_offset"]["d"] for _t, _c, a in members] if sd == "below" else []
            e[sd] = {"verdicts": vs, "counts": dict(collections.Counter("none" if v is None else str(v) for v in vs.values())),
                     "min": min(measured) if measured else None, "few_rows": few,
                     "per_dim_counts": dict(collections.Counter("none" if v is None else str(v) for v in per_dim)),
                     "pure_counts": dict(collections.Counter("none" if v is None else str(v) for v in pure)),
                     "no_offset_counts": dict(collections.Counter("none" if v is None else str(v) for v in no_off)),
                     "no_offset_min": min([v for v in no_off if v is not None], default=None),
                     "rows": {d: sum(a["pooled"][sd][d].get("n", 0) for _t, _c, a in members) for d in (1, 2)},
                     "p90_median": {d: _median([a["pooled"][sd][d].get("p90_rel") for _t, _c, a in members]) for d in (1, 2)},
                     "median_median": {d: _median([a["pooled"][sd][d].get("median_rel") for _t, _c, a in members]) for d in (1, 2)},
                     "coverage_median": {d: _median([a["pooled"][sd][d].get("coverage") for _t, _c, a in members]) for d in (1, 2)},
                     "coverage_lolo_median": {d: _median([a["pooled"][sd][d].get("coverage_lolo") for _t, _c, a in members]) for d in (1, 2)},
                     "rel_sigma_median": {d: _median([a["pooled"][sd][d].get("rel_sigma_median") for _t, _c, a in members]) for d in (1, 2)},
                     "frac_median": {d: _median([a["pooled"][sd][d].get("frac_median") for _t, _c, a in members]) for d in (1, 2)},
                     "unphysical": sum(a["pooled"][sd][d].get("unphysical", 0) for _t, _c, a in members for d in (1, 2))}
        by_type: dict = {}
        for typ, dims_of in DIM_TYPES.items():
            entry = {}
            for sd in SIDES:
                cells = [(a["verdict_per_dim"][dm][sd]["d"], a["per_dim"][dm][sd]) for _t, _c, a in members for dm in a["per_dim"] if dm in dims_of]
                if not cells:
                    continue
                entry[sd] = {"counts": dict(collections.Counter("none" if v is None else str(v) for v, _s in cells)),
                             "p90_median": {d: _median([s[d].get("p90_rel") for _v, s in cells]) for d in (1, 2)},
                             "coverage_median": {d: _median([s[d].get("coverage") for _v, s in cells]) for d in (1, 2)},
                             "frac_median": {d: _median([s[d].get("frac_median") for _v, s in cells]) for d in (1, 2)},
                             "rows": {d: sum(s[d].get("n", 0) for _v, s in cells) for d in (1, 2)}}
            if entry:
                by_type[typ] = entry
        e["by_dim_type"] = by_type
        e["wide_reference"] = [f"{t}:{c}" for t, c, a in members if (a["p90_lolo_interior"] or 0) > WIDE_REFERENCE]
        e["p90_lolo_interior_median"] = _median([a["p90_lolo_interior"] for _t, _c, a in members])
        e["p90_random_median"] = _median([a["i"]["p90_rel"] for _t, _c, a in members])
        q2: dict = {}
        for d in ("1", "2"):
            parts = [a["q2"][d] for _t, _c, a in members if d in a.get("q2", {})]
            q2[d] = {k: sum(x[k] for x in parts if x[k] is not None) for k in ("n", "c4_pass", "c4_pass_wrong_rule", "c4_fail", "c4_fail_fine")}
            ref = [x for x in parts if x["c4_pass_wrong"] is not None]          # columns with an in-range (interior LOLO) p90
            q2[d].update({"c4_pass_wrong": sum(x["c4_pass_wrong"] for x in ref), "c4_pass_ref": sum(x["c4_pass"] for x in ref),
                          "no_reference": len(parts) - len(ref)})
            q2[d]["spearman_median"] = _median([x["spearman"] for x in parts])
        e["q2"] = q2
        e["q3"] = {"k_today_median": _median([a["i"]["k_scale"] for _t, _c, a in members]),
                   "k_lolo_median": _median([a["lolo"]["k_scale"] for _t, _c, a in members]),
                   "k_lolo_interior_median": _median([a["lolo"]["k_scale_interior"] for _t, _c, a in members]),
                   "i_today": _median([a["q3"]["i"]["coverage_today"] for _t, _c, a in members]),
                   "i_lolo": _median([a["q3"]["i"]["coverage_lolo"] for _t, _c, a in members]),
                   "ii_today": _median([a["q3"].get("ii", {}).get("coverage_today") for _t, _c, a in members]),
                   "ii_lolo": _median([a["q3"].get("ii", {}).get("coverage_lolo") for _t, _c, a in members]),
                   "ii_lolo_without_level": _median([a["q3"].get("ii", {}).get("coverage_lolo_without_level") for _t, _c, a in members]),
                   "iii_today": _median([a["edges_all"].get("coverage") for _t, _c, a in members]),
                   "iii_lolo": _median([a["edges_all"].get("coverage_lolo") for _t, _c, a in members]),
                   "iii_lolo_literal": _median([a["edges_all"].get("coverage_lolo_literal") for _t, _c, a in members]),
                   "width_ratio_i": _median([a["q3"]["i"]["width_ratio_median"] for _t, _c, a in members]),
                   "width_ratio_ii": _median([a["q3"].get("ii", {}).get("width_ratio_median") for _t, _c, a in members]),
                   "width_ratio_iii": _median([a["edges_all"].get("width_ratio_lolo_median") for _t, _c, a in members])}
        n113s = [ed["all_rows"]["n113"] for _t, _c, a in members for ed in a["edges"].values() if ed.get("all_rows", {}).get("n113")]
        e["q4"] = {"held_rows": sum(ed["all_rows"]["n"] for _t, _c, a in members for ed in a["edges"].values() if ed.get("all_rows")),
                   "unphysical_all": sum(ed["all_rows"]["unphysical"] for _t, _c, a in members for ed in a["edges"].values() if ed.get("all_rows")),
                   "unphysical_scored": sum(a["edges_all"].get("unphysical", 0) for _t, _c, a in members),
                   "n113": {k: sum(x[k] for x in n113s) for k in ("rows", "has_curve", "has_curve_removed", "above_resonance",
                                                                 "above_resonance_removed", "above_resonance_kept", "near_resonance",
                                                                 "unphysical_before", "unphysical_after", "unphysical_kept_on_curve_rows")} if n113s else None}
        e["hull"] = {"rows": sum(a["edges_all"].get("n", 0) for _t, _c, a in members),
                     "c3": sum(a["edges_all"].get("c3", 0) for _t, _c, a in members),
                     "c3_other": sum(a["edges_all"].get("c3_other", 0) for _t, _c, a in members),
                     "c2": sum(a["edges_all"].get("c2", 0) for _t, _c, a in members),
                     "multi_dim": sum(a["edges_all"].get("multi_dim", 0) for _t, _c, a in members)}
        out[cls] = e
    return out


# -- figures ---------------------------------------------------------------------------------------------------------------------

# the reference categorical palette's first three slots (validated all-pairs), ink and surface
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, SECONDARY, MUTED, GRID, SURFACE, FAINT = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb", "#c9c8c1"
CLASS_CN = {"L_lf": "低频电感 Lp_lf / Ls_lf", "k_lf": "低频耦合系数 k_lf", "SRF": "谐振频率 SRF / SRF_p / SRF_s",
            "Q_peak": "Q 峰值 Qp_peak / Qs_peak", "L_curve": "电感曲线 Lp@f / Ls@f", "Q_curve": "Q 曲线 Qp@f / Qs@f",
            "k_curve": "耦合曲线 k@f"}
SHORT_CN = {"L_lf": "低频电感", "k_lf": "低频耦合", "SRF": "谐振频率", "Q_peak": "Q 峰值", "L_curve": "电感曲线", "Q_curve": "Q 曲线",
            "k_curve": "耦合曲线"}
LOG_TICKS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000)
X_POS = {("below", 2): -2, ("below", 1): -1, ("inside", 0): 0, ("above", 1): 1, ("above", 2): 2}
X_LABELS = ["低出 2 档", "低出 1 档", "范围内\n（整档留出）", "高出 1 档", "高出 2 档"]


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = ["Noto Sans CJK JP", "Droid Sans Fallback", "DejaVu Sans"]   # the SC face hides in a .ttc listed under JP
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, lw=0.7, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#c3c2b7")
    ax.tick_params(colors=MUTED, labelcolor=INK, labelsize=8)


def _members(data: dict, cls: str) -> list[dict]:
    return [a for te in data["tables"].values() for a in te["columns"].values() if a.get("complete") and a["class"] == cls]


def figure_error_coverage(data: dict, path: Path) -> None:
    """Per column class: the p90 relative error and the calibrated 2-sigma coverage against the distance outside the
    measured range, one line per kind of dim crossed (the median over the class's tables and columns of that dim's held-out
    rows at one and two level steps, each side); the middle point is the in-range reference, the interior levels of the
    main size dim left out (the error's; the coverage under today's scale)."""
    plt = _plt()
    summ = data["summary"]
    classes = [c for c in CLASSES if c in summ]
    types = [t for t in DIM_TYPES if any(t in summ[c]["by_dim_type"] for c in classes)]
    colors = dict(zip(types, (BLUE, ORANGE, AQUA, "#eda100", "#e87ba4"), strict=False))
    shapes = dict(zip(types, ("o", "s", "^", "D", "v"), strict=False))
    fig, axes = plt.subplots(2, len(classes), figsize=(3.1 * len(classes) + 1.0, 7.6), facecolor=SURFACE, squeeze=False)
    for k, cls in enumerate(classes):
        e = summ[cls]
        centre = {"p90_rel": e["p90_lolo_interior_median"], "coverage": e["q3"]["ii_today"]}
        for row, key in enumerate(("p90_rel", "coverage")):
            ax = axes[row, k]
            _style(ax)
            stat = "p90_median" if key == "p90_rel" else "coverage_median"
            for typ in types:
                entry = e["by_dim_type"].get(typ, {})
                pts = []
                for sd, sign in (("below", -1), ("above", 1)):
                    for d in (2, 1) if sd == "below" else (1, 2):
                        v = entry.get(sd, {}).get(stat, {}).get(d)
                        if v is not None:
                            pts.append((sign * d, v))
                if centre[key] is not None:
                    pts.append((0, centre[key]))
                pts.sort()
                if pts:
                    ax.plot([x for x, _ in pts], [100 * v for _, v in pts], color=colors[typ], lw=1.6, marker=shapes[typ], ms=6,
                            mec=SURFACE, mew=1.0, zorder=3)
            ax.set_xticks(sorted(X_POS.values()), X_LABELS if row == 1 else [""] * 5, fontsize=7.5)
            ax.set_xlim(-2.4, 2.4)
            if key == "p90_rel":
                ax.set_yscale("log")
                ax.axhline(100 * RULE["p90_floor"], color=SECONDARY, lw=0.9, ls="--", zorder=2)
                lo, hi = ax.get_ylim()
                lo = max([v for v in LOG_TICKS if v <= lo] or [LOG_TICKS[0]])
                hi = min([v for v in LOG_TICKS if v >= hi] or [LOG_TICKS[-1]])
                ax.set_ylim(lo, hi)
                ax.set_yticks([v for v in LOG_TICKS if lo <= v <= hi])
                ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _pos: f"{v:g}"))
                ax.yaxis.set_minor_formatter(plt.NullFormatter())
                ax.set_title(f"{CLASS_CN[cls]}\n（{e['pairs']} 个表×列）", fontsize=9, color=INK)
                if k == 0:
                    ax.set_ylabel("p90 相对误差（%，对数）", fontsize=9, color=INK)
            else:
                ax.set_ylim(0, 102)
                ax.axhline(100 * RULE["coverage_min"], color=SECONDARY, lw=0.9, ls="--", zorder=2)
                ax.axhline(95, color=MUTED, lw=0.8, ls=":", zorder=2)
                if k == 0:
                    ax.set_ylabel("校准后 2σ 区间覆盖率（%）", fontsize=9, color=INK)
    from matplotlib.lines import Line2D
    handles = [Line2D([], [], color=colors[t], lw=1.6, marker=shapes[t], ms=6, mec=SURFACE, label=f"越界的维度：{TYPE_CN[t]}") for t in types]
    handles += [Line2D([], [], color=SECONDARY, lw=0.9, ls="--", label="上排：规则误差门槛的下限 5 %；下排：规则覆盖门槛 85 %"),
                Line2D([], [], color=MUTED, lw=0.8, ls=":", label="下排：名义覆盖 95 %")]
    fig.legend(handles=handles, loc="upper center", ncol=4, fontsize=8.5, frameon=False, labelcolor=INK)
    fig.text(0.5, 0.015, "每个点：该类各表×列在这一维度、这一侧、这一距离上留出行统计量的中位；中间一点是范围内的参照"
             "（主尺寸维整档留出的档间行：误差为 p90，覆盖为今天的系数下的覆盖率）", ha="center", fontsize=8, color=SECONDARY)
    fig.subplots_adjust(left=0.05, right=0.99, bottom=0.12, top=0.83, wspace=0.28, hspace=0.12)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120, facecolor=SURFACE)
    plt.close(fig)
    print(f"wrote {path}", flush=True)


def figure_kscale(data: dict, path: Path) -> None:
    """Today's interval scale (random 5 x 20 % hold-out) against the one from leaving whole levels of the main size dim out:
    coverage on (i) the random hold-out, (ii) the interior levels left out, (iii) the edge hold-outs; and the width ratio."""
    plt = _plt()
    classes = [c for c in CLASSES if _members(data, c)]
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.2), facecolor=SURFACE, gridspec_kw={"width_ratios": [1.6, 1]})
    ax, bx = axes
    for a_ in axes:
        _style(a_)
    keys = (("i", "(i)"), ("ii", "(ii)"), ("iii", "(iii)"))
    xt, xl = [], []
    for k, cls in enumerate(classes):
        members = _members(data, cls)
        for m, (proto, label) in enumerate(keys):
            x = k * 4 + m
            for scale, color, filled, dx in (("today", BLUE, False, -0.14), ("lolo", ORANGE, True, 0.14)):
                if proto == "iii":
                    vals = [a["edges_all"].get("coverage" if scale == "today" else "coverage_lolo") for a in members]
                else:
                    vals = [a["q3"].get(proto, {}).get(f"coverage_{scale}") for a in members]
                vals = [100 * v for v in vals if v is not None]
                if not vals:
                    continue
                q1, q2, q3 = np.percentile(vals, [25, 50, 75])
                ax.plot([x + dx, x + dx], [q1, q3], color=color, lw=1.2, zorder=2)
                ax.plot(x + dx, q2, marker="o", ms=7, color=color, mfc=color if filled else SURFACE, mec=color, mew=1.6, ls="none", zorder=3)
            xt.append(x)
            xl.append(label)
        ax.text(k * 4 + 1, -8, SHORT_CN[cls], ha="center", va="top", fontsize=8.5, color=INK)
    ax.set_xticks(xt, xl, fontsize=7.5)
    ax.set_ylim(0, 102)
    ax.axhline(95, color=MUTED, lw=0.8, ls=":")
    ax.axhline(100 * RULE["coverage_min"], color=SECONDARY, lw=0.9, ls="--")
    ax.set_ylabel("校准后 2σ 区间覆盖率（%）：各表×列的中位，竖线为四分位", fontsize=8.5, color=INK)
    ax.set_title("两种区间放大系数的覆盖率：(i) 随机留出的行，(ii) 整档留出的档间行，(iii) 边缘留出的行", fontsize=10, color=INK, loc="left")
    for m, (proto, label) in enumerate(keys):
        vals = [[a["q3"]["i"]["width_ratio_median"] if proto == "i" else (a["q3"].get("ii", {}).get("width_ratio_median") if proto == "ii"
                 else a["edges_all"].get("width_ratio_lolo_median")) for a in _members(data, cls)] for cls in classes]
        meds = [_median(v) for v in vals]
        xs = [k + (m - 1) * 0.22 for k in range(len(classes))]
        bx.plot(xs, [v if v is not None else np.nan for v in meds], marker=("o", "s", "D")[m], ms=7, ls="none",
                color=(BLUE, ORANGE, AQUA)[m], mec=SURFACE, mew=1.2, label=("(i) 随机留出的行", "(ii) 整档留出的档间行", "(iii) 边缘留出的行")[m],
                zorder=3)
    bx.axhline(1.0, color=SECONDARY, lw=0.9)
    bx.set_xticks(range(len(classes)), [SHORT_CN[c] for c in classes], fontsize=8, rotation=20)
    bx.set_ylabel("区间宽度比：整档留出定的系数 ÷ 今天的（中位）", fontsize=8.5, color=INK)
    bx.set_title("范围内要付出的宽度代价", fontsize=10, color=INK, loc="left")
    bx.legend(fontsize=8, frameon=False, labelcolor=INK)
    from matplotlib.lines import Line2D
    handles = [Line2D([], [], marker="o", ms=7, color=BLUE, mfc=SURFACE, mew=1.6, ls="none", label="今天的系数（随机留出 5×20 % 校准）"),
               Line2D([], [], marker="o", ms=7, color=ORANGE, ls="none", label="整档留出主尺寸维逐档校准的系数"),
               Line2D([], [], color=SECONDARY, lw=0.9, ls="--", label="规则覆盖门槛 85 %"),
               Line2D([], [], color=MUTED, lw=0.8, ls=":", label="名义覆盖 95 %")]
    fig.legend(handles=handles, loc="upper center", ncol=4, fontsize=8.5, frameon=False, labelcolor=INK)
    fig.subplots_adjust(left=0.06, right=0.99, bottom=0.2, top=0.86, wspace=0.18)
    fig.savefig(path, dpi=120, facecolor=SURFACE)
    plt.close(fig)
    print(f"wrote {path}", flush=True)


def figure_sigma(data: dict, path: Path) -> None:
    """Q2: the query's relative sigma against the relative error at the edge hold-outs' rows, per column class."""
    plt = _plt()
    classes = [c for c in CLASSES if _members(data, c)]
    ncol = 4
    nrow = math.ceil(len(classes) / ncol)
    fig, axes = plt.subplots(nrow, ncol, figsize=(16, 4.1 * nrow + 0.8), facecolor=SURFACE, squeeze=False)
    rng = np.random.default_rng(0)
    for k, cls in enumerate(classes):
        ax = axes[k // ncol, k % ncol]
        _style(ax)
        rel, rs, st = [], [], []
        for a in _members(data, cls):
            q = a.get("q2_rows") or {}
            rel += list(q.get("rel", []))
            rs += list(q.get("rel_sigma", []))
            st += list(q.get("steps", []))
        rel, rs, st = np.array(rel, dtype=float), np.array(rs, dtype=float), np.array(st, dtype=int)
        for d, color in ((1, BLUE), (2, ORANGE)):
            m = np.flatnonzero((st == d) & (rel > 0) & (rs > 0))
            if len(m) > 3000:
                m = rng.choice(m, 3000, replace=False)
            ax.scatter(100 * rs[m], 100 * rel[m], s=6, color=color, alpha=0.35, lw=0, label=f"超出 {d} 档", zorder=2)
        lim = (1e-3, 1e3)
        ax.plot(lim, [2 * v for v in lim], color=SECONDARY, lw=0.8, ls="--", zorder=1)
        ax.axvline(100 * RULE["rel_sigma_max"], color=INK, lw=0.9, ls=":", zorder=1)
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlim(1e-2, 1e3)
        ax.set_ylim(1e-3, 1e3)
        ax.set_title(f"{CLASS_CN[cls]}（{len(rel)} 行）", fontsize=9, color=INK)
        ax.set_xlabel("查询给出的相对 σ（%）", fontsize=8, color=INK)
        if k % ncol == 0:
            ax.set_ylabel("实际相对误差（%）", fontsize=8, color=INK)
    for k in range(len(classes), nrow * ncol):
        axes[k // ncol, k % ncol].axis("off")
    from matplotlib.lines import Line2D
    handles = [Line2D([], [], marker="o", ms=6, color=BLUE, ls="none", label="超出实测范围 1 档的留出行"),
               Line2D([], [], marker="o", ms=6, color=ORANGE, ls="none", label="超出 2 档的留出行"),
               Line2D([], [], color=SECONDARY, lw=0.8, ls="--", label="误差 = 2 倍相对 σ"),
               Line2D([], [], color=INK, lw=0.9, ls=":", label="判据 4 的门槛 σ/μ = 0.15")]
    fig.legend(handles=handles, loc="upper center", ncol=4, fontsize=8.5, frameon=False, labelcolor=INK)
    fig.subplots_adjust(left=0.05, right=0.99, bottom=0.08, top=0.9, wspace=0.22, hspace=0.38)
    fig.savefig(path, dpi=120, facecolor=SURFACE)
    plt.close(fig)
    print(f"wrote {path}", flush=True)


def figures(data: dict, figdir: Path) -> None:
    figure_error_coverage(data, figdir / "EXTRAPOLATION_error_coverage.png")
    figure_kscale(data, figdir / "EXTRAPOLATION_kscale.png")
    figure_sigma(data, figdir / "EXTRAPOLATION_sigma.png")


# -- the tables for RESULT_CN.md --------------------------------------------------------------------------------------------------

def _pct(v, digits: int = 1) -> str:
    return f"{100 * v:.{digits}f}" if v is not None and isinstance(v, int | float) and math.isfinite(v) else "—"


def _num(v, digits: int = 3) -> str:
    return f"{v:.{digits}f}" if v is not None and isinstance(v, int | float) and math.isfinite(v) else "—"


D_CN = {None: "无行", 0: "0", 1: "1 档", 2: "2 档"}
TYPE_CN = {"size": "主尺寸", "secondary_size": "次级外径", "width": "线宽", "offset": "中心偏移", "spacing": "线间距"}
DIM_CN = {"primary_outer_diameter_um": "初级外径", "outer_diameter_um": "外径", "secondary_outer_diameter_um": "次级外径",
          "primary_width_um": "初级线宽", "width_um": "线宽", "secondary_width_um": "次级线宽", "center_spacing_um": "中心偏移",
          "spacing_um": "线间距", "secondary_spacing_um": "次级线间距"}


def _cell(s: dict) -> str:
    if not s.get("n"):
        return "—"
    return f"{s['n']} / {_pct(s['p90_rel'])} / {_pct(s['coverage'], 0)}" + (f" / 不物理 {s['unphysical']}" if s.get("unphysical") else "")


def markdown(data: dict) -> str:
    L: list[str] = []
    summ = data["summary"]
    L.append("## 表 1　每类列的判定（事先定下的规则）\n")
    L.append("规则：某张表的某列在某一侧能外推到距离 d，当且仅当在 d 处 p90 相对误差 ≤ max(2 × 该列档间留出的 p90, 5 %)、"
             "校准后 2σ 区间覆盖率 ≥ 85 %、且没有不物理的回答；d 取满足规则的最大距离（超出 1 档就不满足记 0）。"
             "主判定把一张表一列一侧在各个维度上的留出行合在一起判（规格的写法）；右边两栏是另外两种算法："
             "每个维度分开判，以及只用其它维度都还在训练范围内的留出行来判；“低侧不算中心偏移”去掉中心偏移的低侧（它的最小值是 0，"
             "两个绕组同心，不会有更低的查询）。每格是判定为 0 / 1 档 / 2 档 / 无行 的表×列个数。\n")
    L.append("| 列类别 | 表×列数 | 低于实测最小值一侧 | 这一侧最小 | 低侧不算中心偏移 | 其最小 | 高于实测最大值一侧 | 这一侧最小 | 每个维度分开判（两侧合计）| 只用其它维度在范围内的行（两侧合计）|")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for cls, e in summ.items():
        def cnt(cs: dict) -> str:
            return " / ".join(str(cs.get(k, 0)) for k in ("0", "1", "2", "none"))
        per_dim = collections.Counter()
        pure = collections.Counter()
        for sd in SIDES:
            per_dim.update(e[sd]["per_dim_counts"])
            pure.update(e[sd]["pure_counts"])
        L.append(f"| {CLASS_CN[cls]} | {e['pairs']} | {cnt(e['below']['counts'])} | {D_CN[e['below']['min']]} | {cnt(e['below']['no_offset_counts'])} | "
                 f"{D_CN[e['below']['no_offset_min']]} | {cnt(e['above']['counts'])} | {D_CN[e['above']['min']]} | {cnt(per_dim)} | {cnt(pure)} |")
    L.append("")
    L.append("## 表 1b　按越界的维度分开：每类列的判定与数字\n")
    L.append("每个维度分开判（规格规则不变）。每格：判定为 0 / 1 档 / 2 档 / 无行 的（表×列×维度）个数；p90 % 与覆盖 % 是各表×列在超出 1 档 → 2 档处的中位；"
             "占比 = 超出距离占训练范围的比例（中位，1 档 → 2 档）。主尺寸 = 电感外径、变压器初级外径；线宽含初级、次级线宽；线间距含次级线间距。\n")
    L.append("| 列类别 | 维度 | 低侧判定 | 低侧 p90 % | 低侧覆盖 % | 低侧占比 % | 高侧判定 | 高侧 p90 % | 高侧覆盖 % | 高侧占比 % |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for cls, e in summ.items():
        for typ, entry in e["by_dim_type"].items():
            cells = []
            for sd in SIDES:
                s = entry.get(sd)
                if s is None:
                    cells += ["—"] * 4
                    continue
                cells += [" / ".join(str(s["counts"].get(k, 0)) for k in ("0", "1", "2", "none")),
                          f"{_pct(s['p90_median'][1])} → {_pct(s['p90_median'][2])}",
                          f"{_pct(s['coverage_median'][1], 0)} → {_pct(s['coverage_median'][2], 0)}",
                          f"{_pct(s['frac_median'][1], 0)} → {_pct(s['frac_median'][2], 0)}"]
            L.append(f"| {CLASS_CN[cls]} | {TYPE_CN[typ]} | " + " | ".join(cells) + " |")
    L.append("")
    wide = [(cls, e["wide_reference"]) for cls, e in summ.items() if e["wide_reference"]]
    if wide:
        L.append("范围内（档间留出）p90 已超过 10 % 的列——规则的误差门槛随它放宽，这些列“能外推”只说明外面不比里面更差："
                 + "；".join(f"{CLASS_CN[c]}：{', '.join(v)}" for c, v in wide) + "。\n")
    L.append("## 表 2　误差与覆盖随超出距离的变化（每类列：各表×列数值的中位）\n")
    L.append("p90 = 九成留出行的相对误差不超过这个数；覆盖 = 真值落在校准后 2σ 区间内的比例；占比 = 超出距离占训练范围的比例（中位）。\n")
    L.append("| 列类别 | 随机留出 p90 % | 档间留出 p90 % | 低出 1 档 p90 % / 覆盖 % / 占比 | 低出 2 档 | 高出 1 档 | 高出 2 档 | 相对 σ 中位 %（低 1 / 高 1）|")
    L.append("|---|---|---|---|---|---|---|---|")
    for cls, e in summ.items():
        def side(sd: str, d: int, e=e) -> str:
            return f"{_pct(e[sd]['p90_median'][d])} / {_pct(e[sd]['coverage_median'][d], 0)} / {_pct(e[sd]['frac_median'][d], 0)} %"
        L.append(f"| {CLASS_CN[cls]} | {_pct(e['p90_random_median'], 2)} | {_pct(e['p90_lolo_interior_median'], 2)} | {side('below', 1)} | "
                 f"{side('below', 2)} | {side('above', 1)} | {side('above', 2)} | {_pct(e['below']['rel_sigma_median'][1], 2)} / "
                 f"{_pct(e['above']['rel_sigma_median'][1], 2)} |")
    L.append("")
    L.append("## 表 3　（Q2）模型自己的相对 σ 是否跟着误差走\n")
    L.append("“判据 4 放行” = 查询给出的 σ/μ ≤ 0.15；“放行但错” = 放行的行里误差超过该列档间留出 p90 的两倍（只计有档间留出数字的列："
             "错的行 / 放行的行），以及超过规则门槛 max(2 × 档间 p90, 5 %) 的行；“拦下但准” = σ/μ > 0.15 的行里误差在规则门槛内。"
             "秩相关 = 相对 σ 与误差的 Spearman 秩相关（各表×列的中位）。\n")
    L.append("| 列类别 | 距离 | 行数 | 判据 4 放行 | 放行但错 | 拦下 | 拦下但准 | 秩相关 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for cls, e in summ.items():
        for d in ("1", "2"):
            q = e["q2"][d]
            if not q["n"]:
                continue
            wrong = (f"{q['c4_pass_wrong']} / {q['c4_pass_ref']}" + (f"（{_pct(q['c4_pass_wrong'] / q['c4_pass_ref'], 1)} %）" if q["c4_pass_ref"] else "")
                     + f"；规则门槛 {q['c4_pass_wrong_rule']} / {q['c4_pass']}" + (f"（{_pct(q['c4_pass_wrong_rule'] / q['c4_pass'], 1)} %）" if q["c4_pass"] else ""))
            L.append(f"| {CLASS_CN[cls]} | {d} 档 | {q['n']} | {q['c4_pass']} | {wrong} | {q['c4_fail']} | {q['c4_fail_fine']} | {_num(q['spearman_median'], 2)} |")
    L.append("")
    L.append("## 表 3b　（Q2）判据 4 换成更严的门槛会怎样\n")
    L.append("每格：σ/μ 不超过该门槛、会被作答的留出行占比 / 其中误差超过该列档间 p90 两倍的占比（括号内：超过规则门槛 max(2 × 档间 p90, 5 %) 的占比）。"
             "只计有档间留出数字的列。\n")
    L.append("| 列类别 | 距离 | 行数 | " + " | ".join(f"σ/μ ≤ {c:g}" for c in CEILINGS) + " |")
    L.append("|---|---|---|" + "---|" * len(CEILINGS))
    for cls, cells in data.get("q2_ceilings", {}).items():
        for d, cell in cells.items():
            parts = []
            for c in CEILINGS:
                x = cell[f"{c:g}"]
                parts.append(f"{_pct(x['answered'], 0)} / {_pct(x['wrong'], 0)}（{_pct(x['wrong_rule'], 0)}）" if x["answered"] else "0 / —")
            L.append(f"| {CLASS_CN[cls]} | {d} 档 | {cell['n']} | " + " | ".join(parts) + " |")
    L.append("")
    L.append("## 表 4　（Q3）两种区间放大系数 k_scale（每类列：各表×列的中位）\n")
    L.append("今天的 = 随机留出 5×20 % 校准；整档 = 主尺寸维（外径或初级外径）逐档留出校准。(i) 随机留出的行；(ii) 整档留出的档间行"
             "（“不含本档”：系数由其它档算出再用到这一档）；(iii) 边缘留出的行（今天的系数来自该次留出自己的训练行；整档系数去掉了被留出行，"
             "括号内为不去掉的全表系数）。宽度比 = 整档系数给的区间宽度 ÷ 今天的。\n")
    L.append("| 列类别 | k 今天 | k 整档（全部档 / 只档间）| (i) 覆盖 % 今天 / 整档 | (ii) 今天 / 整档 / 不含本档 | (iii) 今天 / 整档（全表）| 宽度比 (i) / (ii) / (iii) |")
    L.append("|---|---|---|---|---|---|---|")
    for cls, e in summ.items():
        q = e["q3"]
        L.append(f"| {CLASS_CN[cls]} | {_num(q['k_today_median'], 2)} | {_num(q['k_lolo_median'], 2)} / {_num(q['k_lolo_interior_median'], 2)} | "
                 f"{_pct(q['i_today'], 0)} / {_pct(q['i_lolo'], 0)} | {_pct(q['ii_today'], 0)} / {_pct(q['ii_lolo'], 0)} / {_pct(q['ii_lolo_without_level'], 0)} | "
                 f"{_pct(q['iii_today'], 0)} / {_pct(q['iii_lolo'], 0)}（{_pct(q['iii_lolo_literal'], 0)}）| "
                 f"{_num(q['width_ratio_i'], 2)} / {_num(q['width_ratio_ii'], 2)} / {_num(q['width_ratio_iii'], 2)} |")
    L.append("")
    L.append("## 表 5　（Q4）外推的回答是否物理；N-113 规则去掉什么\n")
    L.append("不物理 = L、Q、SRF ≤ 0 或 k 不在 (0, 1) 内。N-113 规则：曲线列在 f 处只在预测的谐振频率高于 srf_margin × f 时作答。"
             "“有曲线值” = 留出行实测有这一列；“已过谐振” = 留出行实测谐振 ≤ srf_margin × f（实测没有这一列）。\n")
    L.append("| 列类别 | 留出行（全部，各列合计）| 不物理回答（全部 / 有实测值的行）| 有曲线值 / 其中被去掉 | 已过谐振 / 去掉 / 仍作答 | 近谐振 | 不物理：规则前 / 后 |")
    L.append("|---|---|---|---|---|---|---|")
    for cls, e in summ.items():
        q = e["q4"]
        n = q["n113"]
        n113_cells = (f"{n['has_curve']} / {n['has_curve_removed']} | {n['above_resonance']} / {n['above_resonance_removed']} / {n['above_resonance_kept']} | "
                      f"{n['near_resonance']} | {n['unphysical_before']} / {n['unphysical_after']}") if n else "— | — | — | —"
        L.append(f"| {CLASS_CN[cls]} | {q['held_rows']} | {q['unphysical_all']} / {q['unphysical_scored']} | {n113_cells} |")
    L.append("")
    L.append("## 表 6　守卫的判据 3（凸包）\n")
    L.append("对边缘留出的每一行（有实测值的行）：只用判据 3（该行圈数档的凸包，跳过判据 1 的逐维范围）会不会拒绝；把越界的那一维从凸包里拿掉后还拒不拒绝；"
             "判据 2（圈数档行数不够）拒绝的行；其它维也越界的行。\n")
    L.append("| 列类别 | 行 | 判据 3 单独拒绝 | 去掉越界维后仍拒绝 | 判据 2 拒绝 | 其它维也越界 |")
    L.append("|---|---|---|---|---|---|")
    for cls, e in summ.items():
        h = e["hull"]
        L.append(f"| {CLASS_CN[cls]} | {h['rows']} | {h['c3']} | {h['c3_other']} | {h['c2']} | {h['multi_dim']} |")
    L.append("")
    L.append("## 表 7　每张表每列的判定与数字\n")
    L.append("每格：行数 / p90 相对误差 % / 覆盖 %（有不物理回答时注明个数）。门槛 = max(2 × 档间 p90, 5 %)。判定 = 能外推的最大距离。\n")
    L.append("| 表 | 列 | 随机 p90 % | 档间 p90 % | 门槛 % | 低出 1 档 | 低出 2 档 | 低侧判定 | 高出 1 档 | 高出 2 档 | 高侧判定 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for t, te in data["tables"].items():
        for c, a in te["columns"].items():
            if not a.get("complete"):
                L.append(f"| {t} | {c} | 未完成 | | | | | | | | |")
                continue
            v = a["verdict"]
            L.append(f"| {t} | {c} | {_pct(a['i']['p90_rel'], 2)} | {_pct(a['p90_lolo_interior'], 2)} | {_pct(v['below']['limit'], 1)} | "
                     f"{_cell(a['pooled']['below'][1])} | {_cell(a['pooled']['below'][2])} | {D_CN[v['below']['d']]} | "
                     f"{_cell(a['pooled']['above'][1])} | {_cell(a['pooled']['above'][2])} | {D_CN[v['above']['d']]} |")
    L.append("")
    L.append("## 表 7b　逐维判定（每个越界维分开判；↓ 低侧，↑ 高侧；数字 = 能外推的档数，— = 无行）\n")
    L.append("| 表 | 列 | " + " | ".join(DIM_CN.get(d, d) for d in ("primary_outer_diameter_um", "outer_diameter_um", "secondary_outer_diameter_um",
                                                                    "primary_width_um", "width_um", "secondary_width_um", "center_spacing_um",
                                                                    "spacing_um", "secondary_spacing_um")) + " |")
    L.append("|---|---|" + "---|" * 9)
    for t, te in data["tables"].items():
        for c, a in te["columns"].items():
            if not a.get("complete"):
                continue
            cells = []
            for d in ("primary_outer_diameter_um", "outer_diameter_um", "secondary_outer_diameter_um", "primary_width_um", "width_um",
                      "secondary_width_um", "center_spacing_um", "spacing_um", "secondary_spacing_um"):
                v = a["verdict_per_dim"].get(d)
                cells.append("" if v is None else f"↓{'—' if v['below']['d'] is None else v['below']['d']} ↑{'—' if v['above']['d'] is None else v['above']['d']}")
            L.append(f"| {t} | {c} | " + " | ".join(cells) + " |")
    L.append("")
    L.append("## 表 8　（Q3）每张表每列的两种系数\n")
    L.append("| 表 | 列 | k 今天 | k 整档 | (i) 覆盖 % 今天 / 整档 | (ii) 今天 / 整档 / 不含本档 | (iii) 今天 / 整档 | 宽度比 (i) |")
    L.append("|---|---|---|---|---|---|---|---|")
    for t, te in data["tables"].items():
        for c, a in te["columns"].items():
            if not a.get("complete"):
                continue
            q, ii = a["q3"], a["q3"].get("ii", {})
            L.append(f"| {t} | {c} | {_num(a['i']['k_scale'], 2)} | {_num(a['lolo']['k_scale'], 2)} | {_pct(q['i']['coverage_today'], 0)} / "
                     f"{_pct(q['i']['coverage_lolo'], 0)} | {_pct(ii.get('coverage_today'), 0)} / {_pct(ii.get('coverage_lolo'), 0)} / "
                     f"{_pct(ii.get('coverage_lolo_without_level'), 0)} | {_pct(a['edges_all'].get('coverage'), 0)} / "
                     f"{_pct(a['edges_all'].get('coverage_lolo'), 0)} | {_num(q['i']['width_ratio_median'], 2)} |")
    L.append("")
    n112 = data.get("n112")
    if n112:
        L.append("## 表 9　独立核对：N-112 的实测点（xfm_bs_m10，初级 / 次级外径 51 / 51 µm，线宽 4 / 4，无偏移）\n")
        L.append("全表模型（库缓存里的，只读加载），绕过守卫；真值 = N-112 那次 EMX 的 sNp 用该表自己的定义重测。\n")
        L.append("| 列 | 预测 | 今天的区间 | 真值 | 相对误差 % | 在区间内 | 相对 σ % | 整档系数的区间 | 在区间内 |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for c, v in n112["columns"].items():
            if "value" not in v:
                L.append(f"| {c} | {v.get('status')} | | | | | | | |")
                continue
            sc = 1e12 if c.startswith(("Lp", "Ls")) else 1e-9 if c.startswith("SRF") else 1.0
            unit = " pH" if sc == 1e12 else " GHz" if sc == 1e-9 else ""
            tr = "—" if v["truth"] is None else f"{v['truth'] * sc:.4g}{unit}"
            lol = f"[{v['lo_lolo'] * sc:.4g}, {v['hi_lolo'] * sc:.4g}]" if v.get("lo_lolo") is not None else "—"
            L.append(f"| {c} | {v['value'] * sc:.4g}{unit} | [{v['lo'] * sc:.4g}, {v['hi'] * sc:.4g}] | {tr} | {_pct(v['rel_error'], 2)} | "
                     f"{'是' if v['inside'] else '否' if v['inside'] is not None else '—'} | {_pct(v['rel_sigma'], 2)} | {lol} | "
                     f"{'是' if v.get('inside_lolo') else '否' if v.get('inside_lolo') is not None else '—'} |")
        L.append("")
    chk = data.get("library_calibration_check") or {}
    if chk:
        L.append(f"## 核对：本研究的全表校准与库缓存的校准\n\nk_scale 与 σ 下限逐位相同 {chk['identical']} 列；末位不同 {len(chk['different'])} 列"
                 + (f"（最大相对差 {chk['max_rel_diff']:.1e}：{', '.join(x['column'] for x in chk['different'])}）" if chk["different"] else "")
                 + f"；库里没有缓存 {len(chk['not_cached'])} 列" + (f"（{', '.join(chk['not_cached'])}）" if chk["not_cached"] else "") + "。\n")
    cost = data["cost"]
    L.append(f"## 代价\n\n作业 {cost['jobs']} 个，拟合时间合计 {cost['fit_seconds'] / 3600:.2f} h（" + "，".join(
        f"{k} {v / 3600:.2f} h" for k, v in cost["by_kind"].items()) + f"）；未完成的作业 {len(data['incomplete'])} 个。")
    for r in data.get("runs", []):
        L.append(f"- {r['label']}：{r['started']} → {r['finished']}，{len(r['tables'])} 张表，{r['workers_max']} 个进程上限 × {r['threads']} 线程，"
                 f"跑 {r['ran']} 个（之前已有 {r['done_before']} 个），失败 {len(r['failed'])}，墙钟 {r['wall_minutes']:.1f} min"
                 + ("（到时限停止）" if r.get("stopped_at_deadline") else ""))
    return "\n".join(L) + "\n"


# -- main ---------------------------------------------------------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "run", "estimate", "report", "n112", "check"):
        s = sub.add_parser(name)
        s.add_argument("library", type=Path)
        s.add_argument("evidence", type=Path)
        if name in ("report", "n112"):
            s.add_argument("out", type=Path)
        if name == "report":
            s.add_argument("figdir", type=Path)
            s.add_argument("--md", type=Path)
        s.add_argument("--tables", default=",".join(TABLES) if name != "check" else "")
        if name == "check":
            s.add_argument("--subset", required=True, help="DIM=LEVEL[+LEVEL], a subset of the plan")
        s.add_argument("--columns", default="")
        s.add_argument("--plan-file", type=Path, default=None)
        s.add_argument("--only", default="")
        if name == "run":
            s.add_argument("--threads", type=int, default=2)
            s.add_argument("--workers-max", type=int, default=16)
            s.add_argument("--max-hours", type=float, default=0.0)
            s.add_argument("--label", default="")
            s.add_argument("--order", choices=("longest", "priority"), default="longest")
            s.add_argument("--max-threads-file", type=Path, default=None, help="this run's thread cap file (default EVIDENCE/max_threads)")
            s.add_argument("--deadline", default="", help="local time YYYY-MM-DD HH:MM after which no job starts")
        if name == "estimate":
            s.add_argument("--workers", type=int, default=12)
            s.add_argument("--budget-hours", type=float, default=0.0)
            s.add_argument("--inefficiency", type=float, default=1.1)
            s.add_argument("--write-plan", type=Path, default=None)
    args = ap.parse_args()
    {"plan": cmd_plan, "run": cmd_run, "estimate": cmd_estimate, "report": cmd_report, "n112": cmd_n112, "check": cmd_check}[args.cmd](args)


if __name__ == "__main__":
    main()
