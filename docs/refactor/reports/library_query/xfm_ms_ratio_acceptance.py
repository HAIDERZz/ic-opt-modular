"""N-16 acceptance on the N28 multi-turn transformer tables (xfm_ms_ap, xfm_ms_m10): the library's own models on the T16.2a
study's recommendation for these tables -- Lp / Ls as ``model: ratio`` with ``feature_map: xfm_ms_dimensionless`` (BF).

The real library is only read. Scratch libraries are built under SCRATCH_DIR per table and variant, their store holding the
part's observations minus one held-out level (and minus adopted rows, if any); ``spec.json`` and ``sims/`` are links to
the real store. Variants of the manifest:

    direct : as it is today (Lp / Ls: one GP per column on the dims, per turns level)        = the study's method A
    mapped : Lp / Ls: {feature_map: xfm_ms_dimensionless}                                    = the study's F
    ratio  : Lp / Ls: {model: ratio, feature_map: xfm_ms_dimensionless}                      = the study's BF (recommended)

  (ii) leave one level out: every OD_P and OD_S level the study held out (>= 25 usable rows) is removed from a fold library
       fitted without calibration; its Lp@60 / Ls@60 rows are predicted; pooled median / p90 / max relative error per dim,
       against the study's numbers for A / F / BF;
  (i)  the full library, calibrated (5 x 20 % hold-out, every composed fold refitting every part), for direct and ratio:
       k_scale, typical error (median_rel), coverage before scaling, and the fit times.

Usage: xfm_ms_ratio_acceptance.py LIBRARY_ROOT SCRATCH_DIR OUT_JSON [--tables xfm_ms_ap,xfm_ms_m10] [--fold-workers 12]
       [--workers 4] [--variants direct,mapped,ratio]
Run with OMP_NUM_THREADS=2 (every fit then uses two BLAS threads, as the study's did).
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import yaml

from ic_opt.library import dataset, manifest, query
from ic_opt.site import HostLimits

HERE = Path(__file__).resolve().parent
F0 = 60
COLUMNS = (f"Lp@{F0}", f"Ls@{F0}")
CURVES = ("Lp", "Ls")
MAPPED = "xfm_ms_dimensionless"
DIM_OF = {"OD_P": "primary_outer_diameter_um", "OD_S": "secondary_outer_diameter_um"}
STUDY_METHOD = {"direct": "A", "mapped": "F", "ratio": "BF"}
CHANGES = {"direct": {}, "mapped": {"feature_map": MAPPED}, "ratio": {"model": "ratio", "feature_map": MAPPED}}
MIN_LEVEL_ROWS = 25


def adopted(root: Path, store: str) -> set[str]:
    path = root / store / ".icopt" / "adopted.yaml"
    return {str(i) for entry in (yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else []) or [] for i in entry["ids"]}


def scratch_library(real: Path, lib: manifest.Library, stratum: str, dest: Path, drop: tuple[str, float] | None, variant: str) -> Path:
    """A scratch library of one stratum: the part's observations minus the adopted rows (and minus one level of one dim),
    spec and sims linked to the real store, the manifest of the variant."""
    s = lib.strata[stratum]
    (store,) = [p.store for p in s.parts]
    icopt = dest / store / ".icopt"
    icopt.mkdir(parents=True, exist_ok=True)
    gone = adopted(real, store)
    kept = []
    for line in (real / store / ".icopt" / "observations.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        if o["obs_id"] in gone or (drop is not None and float(o["params"][drop[0]]) == drop[1]):
            continue
        kept.append(line)
    (icopt / "observations.jsonl").write_text("\n".join(kept) + "\n", encoding="utf-8")
    for name in ("spec.json", "sims"):
        link = icopt / name
        if not link.exists():
            link.symlink_to(real / store / ".icopt" / name)
    doc = lib.model_dump(mode="json")
    quantities = doc["strata"][stratum]["quantities"]
    for curve in CURVES:
        quantities[curve] = {**quantities[curve], **CHANGES[variant]}
    doc["strata"] = {stratum: doc["strata"][stratum]}
    (dest / "library.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return dest


def fold_job(root: str, stratum: str, x_by_column: dict[str, list[list[float]]]) -> dict:
    """In a spawned process: a fold library fitted here (one worker, two BLAS threads) without calibration; the means and
    unfloored sigmas at the held-out rows per column."""
    t0 = time.time()
    lib = query.Library(root, calibrate=False, limits=HostLimits(max_threads=2, max_memory_gb=16))
    rows = len(lib.dataset(stratum).rows)
    t1 = time.time()
    models = lib.models(stratum, list(x_by_column))
    out = {"rows": rows, "dataset_s": round(t1 - t0, 1), "fit_s": round(time.time() - t1, 1), "pred": {}}
    for c, x in x_by_column.items():
        if x:
            mu, sigma = models[c].gp.predict(np.array(x), floor=False)
            out["pred"][c] = {"mu": mu.tolist(), "sigma": sigma.tolist()}
        else:
            out["pred"][c] = {"mu": [], "sigma": []}
    return out


def full_job(root: str, stratum: str, workers: int) -> dict:
    """In a spawned process: the full (calibrated) library; the calibration of the two columns and the fit times."""
    t0 = time.time()
    lib = query.Library(root, limits=HostLimits(max_threads=2 * workers, max_memory_gb=64))
    rows = len(lib.dataset(stratum).rows)
    t1 = time.time()
    lib.models(stratum, ["Lp_lf", "Ls_lf"], workers=min(workers, 2), threads=2 * min(workers, 2))
    t2 = time.time()
    models = lib.models(stratum, list(COLUMNS), workers=workers, threads=2 * workers)
    t3 = time.time()
    return {"rows": rows, "seconds": {"dataset": round(t1 - t0, 1), "low_frequency_models": round(t2 - t1, 1), "curve_models": round(t3 - t2, 1)},
            "calibration": {c: m.calibration for c, m in models.items()}}


def rel_stats(y, mu) -> dict:
    y, mu = np.asarray(y, dtype=float), np.asarray(mu, dtype=float)
    rel = np.abs(mu - y) / y
    return {"n": len(y), "median_rel": float(np.median(rel)), "p90_rel": float(np.quantile(rel, 0.9)), "max_rel": float(rel.max())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("scratch", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--tables", default="xfm_ms_ap,xfm_ms_m10")
    ap.add_argument("--fold-workers", type=int, default=12)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--variants", default="direct,mapped,ratio")
    args = ap.parse_args()
    t_start = time.time()
    lib = manifest.load(args.root)
    tables = args.tables.split(",")
    variants = args.variants.split(",")
    study = json.loads((HERE / "xfm_anchor_model_study.json").read_text(encoding="utf-8"))["tables"]
    folds, fulls, held = [], {}, {}
    for t in tables:
        real = dataset.build(args.root, t, library=lib)                 # read only: a cache hit
        gone = adopted(args.root, lib.strata[t].parts[0].store)
        base = [r for r in real.rows if r.obs_id not in gone]
        levels = [(e["dim"], float(e["level"])) for e in study[t]["targets"][f"Ls@{F0}"]["levels"]
                  if e["dim"] in DIM_OF and not e.get("skipped") and e["n"] >= MIN_LEVEL_ROWS]
        held[t] = {}
        for short, level in levels:
            dim = DIM_OF[short]
            rows = {c: [r for r in base if r.coords[dim] == level and r.values.get(c) is not None] for c in COLUMNS}
            held[t][(short, level)] = rows
            for v in variants:
                root = scratch_library(args.root, lib, t, args.scratch / t / f"{v}_{short}_{level:g}", (dim, level), v)
                folds.append((t, short, level, v, root, {c: [[r.coords[d] for d in real.dims] for r in rs] for c, rs in rows.items()}))
        for v in ("direct", "ratio"):
            if v in variants:
                fulls[(t, v)] = scratch_library(args.root, lib, t, args.scratch / t / f"full_{v}", None, v)
    print(f"scratch libraries ready: {len(folds)} folds, {len(fulls)} full, in {time.time() - t_start:.0f} s", flush=True)
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=args.fold_workers, mp_context=ctx) as fold_pool, \
            ProcessPoolExecutor(max_workers=max(1, len(fulls)), mp_context=ctx) as full_pool:
        full_jobs = {k: full_pool.submit(full_job, str(root), k[0], args.workers) for k, root in fulls.items()}
        fold_jobs = [(t, short, level, v, fold_pool.submit(fold_job, str(root), t, x_by)) for t, short, level, v, root, x_by in folds]
        fold_out = [(t, short, level, v, job.result()) for t, short, level, v, job in fold_jobs]
        print(f"folds done at {time.time() - t_start:.0f} s", flush=True)
        full_out = {k: job.result() for k, job in full_jobs.items()}
    print(f"full libraries done at {time.time() - t_start:.0f} s", flush=True)
    report = {"date": time.strftime("%Y-%m-%d %H:%M"), "library": str(args.root), "anchor_ghz": F0, "variants": CHANGES,
              "omp_num_threads": os.environ.get("OMP_NUM_THREADS"), "fold_workers": args.fold_workers, "workers": args.workers,
              "min_level_rows": MIN_LEVEL_ROWS, "tables": {}}
    for t in tables:
        entry = {"protocol_ii": {}, "protocol_i_calibration": {}, "fold_seconds": {}}
        for c in COLUMNS:
            entry["protocol_ii"][c] = {}
            for short in DIM_OF:
                per_variant = {}
                for v in variants:
                    ys, mus, per_level = [], [], {}
                    for tt, sh, level, vv, out in fold_out:
                        if tt == t and sh == short and vv == v:
                            y = [r.values[c] for r in held[t][(short, level)][c]]
                            ys += y
                            mus += out["pred"][c]["mu"]
                            per_level[f"{level:g}"] = {**rel_stats(y, out["pred"][c]["mu"]), "fold_library_rows": out["rows"],
                                                       "dataset_s": out["dataset_s"], "fit_s": out["fit_s"]} if y else {"n": 0}
                    if ys:
                        per_variant[v] = {"pooled": rel_stats(ys, mus), "levels": per_level,
                                          "study": study[t]["targets"][c]["protocols"][f"ii-{short}"].get(STUDY_METHOD[v])}
                entry["protocol_ii"][c][short] = per_variant
        for v in ("direct", "ratio"):
            if (t, v) in full_out:
                entry["protocol_i_calibration"][v] = full_out[(t, v)]
        entry["fold_seconds"] = {v: [out["fit_s"] for tt, _s, _l, vv, out in fold_out if tt == t and vv == v] for v in variants}
        report["tables"][t] = entry
    report["wall_seconds"] = round(time.time() - t_start, 1)
    args.out.write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    for t in tables:
        for c in COLUMNS:
            for short in DIM_OF:
                parts = []
                for v, e in report["tables"][t]["protocol_ii"][c][short].items():
                    s = e["study"] or {}
                    parts.append(f"{v}: p90 {e['pooled']['p90_rel'] * 100:.1f}% (study {STUDY_METHOD[v]} {s.get('p90_rel', float('nan')) * 100:.1f}%)")
                print(f"{t} {c} leave-one-{short}-out  " + " | ".join(parts), flush=True)
        for v, f in report["tables"][t]["protocol_i_calibration"].items():
            cal = {c: f"k_scale {m['k_scale']:.2f} typical {m.get('median_rel', float('nan')) * 100:.2f}% cov-before {m.get('coverage_2sigma_before', float('nan')):.2f}"
                   for c, m in f["calibration"].items()}
            print(f"{t} full {v}: {cal} seconds {f['seconds']}", flush=True)
    print("wrote", args.out, f"{report['wall_seconds']:.0f} s", flush=True)


if __name__ == "__main__":
    main()
