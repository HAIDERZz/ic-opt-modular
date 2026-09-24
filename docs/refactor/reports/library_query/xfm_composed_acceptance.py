"""T16.2b acceptance on the N28 single-turn transformer tables: the library's own composed models reproduce the T16.2a
study's DF method (xfm_anchor_model_study.json).

The real library is only read (its datasets load from its cache). Scratch libraries are built under SCRATCH_DIR: per table
a copy of its stratum whose store holds the part's observations minus the rows adopted from the 2026-09-24 real-EMX check
(``.icopt/adopted.yaml``) and minus, for a fold, every row of one primary_outer_diameter_um level; ``spec.json`` and
``sims/`` are symbolic links to the real store. Their manifest is the real stratum with

    SRF: {feature_map: xfm_bs_dimensionless}
    Lp / Ls: {..., model: resonance, feature_map: xfm_bs_dimensionless}

-- the study's DF: the low-frequency inductance model as it is, the SRF model and the residual model on the dimensionless
inputs (the real manifest gives Lp / Ls no feature_map, so "their existing feature_map kept" would be none: that is the
study's method D with a better SRF model, not DF).

  (1) leave one OD_P level out: every interior level (80 ... 210 um) held out of a fold library fitted without calibration
      (the mean does not depend on it); its Lp@40 / Ls@40 rows predicted; pooled median / p90 / max relative error, and
      coverage / median |z| with the full library's calibration applied to the fold models' sigma (the study's procedure);
  (2) the full library (calibrated: every composed fold refits every part) predicts the five adopted designs of the table:
      error, calibrated 2-sigma interval, z; against the study's DF prediction of the same rows;
  (3) lib.region at 40 GHz with the T14 targets of region_acceptance_40g_t15.json on the full library: robust / mean counts
      against that file (informational: the models differ).

``--other-anchors`` adds to an existing OUT_JSON what the manifest change does to the curves' other columns: protocol (1)
at 10, 28 and 60 GHz, resonance (the same fold libraries, parts already fitted) against direct (fold libraries of the real
stratum, unchanged), and direct at 40 GHz -- the study's method A, a check of this harness.

Usage: xfm_composed_acceptance.py LIBRARY_ROOT SCRATCH_DIR OUT_JSON [--tables xfm_bs_ap,xfm_bs_m10] [--fold-workers 12]
       [--workers 10] [--other-anchors]
Run under nice with OMP_NUM_THREADS=2: every fit then uses two BLAS threads, as the study's did.
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

from ic_opt.library import dataset, manifest, query, region
from ic_opt.site import HostLimits

HERE = Path(__file__).resolve().parent
F0 = 40
COLUMNS = (f"Lp@{F0}", f"Ls@{F0}")
MAPPED = "xfm_bs_dimensionless"
OD_P = "primary_outer_diameter_um"
LEVELS = (80.0, 100.0, 120.0, 150.0, 180.0, 210.0)       # the study's interior OD_P levels (>= 25 usable rows each)
OTHER_ANCHORS = (10, 28, 60)                               # the curves' other columns the manifest change reaches
REGION_EXTRA = (f"Qp@{F0}", f"Qs@{F0}", f"k@{F0}", "SRF")
SHORT = {"primary_outer_diameter_um": "OD_P", "secondary_outer_diameter_um": "OD_S", "primary_width_um": "W_P",
         "secondary_width_um": "W_S", "center_spacing_um": "CS"}


def adopted(root: Path, store: str) -> set[str]:
    path = root / store / ".icopt" / "adopted.yaml"
    return {str(i) for entry in (yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else []) or [] for i in entry["ids"]}


def scratch_library(real: Path, lib: manifest.Library, stratum: str, dest: Path, drop_level: float | None, *, changed: bool = True) -> Path:
    """A scratch library of one stratum: the part's observations minus the adopted rows (and minus one OD_P level), spec and
    sims linked to the real store, the manifest changed as the module docstring says (``changed``) or as it is."""
    s = lib.strata[stratum]
    (store,) = [p.store for p in s.parts]
    icopt = dest / store / ".icopt"
    icopt.mkdir(parents=True, exist_ok=True)
    drop = adopted(real, store)
    kept = []
    for line in (real / store / ".icopt" / "observations.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        if o["obs_id"] in drop or (drop_level is not None and float(o["params"][OD_P]) == drop_level):
            continue
        kept.append(line)
    (icopt / "observations.jsonl").write_text("\n".join(kept) + "\n", encoding="utf-8")
    for name in ("spec.json", "sims"):
        link = icopt / name
        if not link.exists():
            link.symlink_to(real / store / ".icopt" / name)
    doc = lib.model_dump(mode="json")
    quantities = doc["strata"][stratum]["quantities"]
    if changed:
        quantities["SRF"] = {**quantities["SRF"], "feature_map": MAPPED}
        for curve in ("Lp", "Ls"):
            quantities[curve] = {**quantities[curve], "model": "resonance", "feature_map": MAPPED}
    doc["strata"] = {stratum: doc["strata"][stratum]}
    (dest / "library.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return dest


def fold_job(root: str, stratum: str, level: float, x: list[list[float]]) -> dict:
    """In a spawned process: one fold library, fitted here (one worker, two BLAS threads) without calibration; the held-out
    rows' mean and unfloored sigma per composed column."""
    t0 = time.time()
    lib = query.Library(root, calibrate=False, limits=HostLimits(max_threads=2, max_memory_gb=16))
    rows = len(lib.dataset(stratum).rows)
    t1 = time.time()
    models = lib.models(stratum, list(COLUMNS))
    out = {"level": level, "rows": rows, "dataset_s": round(t1 - t0, 1), "fit_s": round(time.time() - t1, 1), "pred": {}}
    xa = np.array(x)
    for column, m in models.items():
        mu, sigma = m.gp.predict(xa, floor=False)
        out["pred"][column] = {"mu": mu.tolist(), "sigma": sigma.tolist()}
    return out


def columns_job(root: str, stratum: str, x_by_column: dict[str, list[list[float]]]) -> dict:
    """In a spawned process: a fold library fitted here (one worker, two BLAS threads) without calibration; the means at the
    held-out rows per column."""
    t0 = time.time()
    lib = query.Library(root, calibrate=False, limits=HostLimits(max_threads=2, max_memory_gb=16))
    models = lib.models(stratum, list(x_by_column))
    return {"fit_s": round(time.time() - t0, 1),
            "mu": {c: models[c].gp.predict(np.array(x), floor=False)[0].tolist() if x else [] for c, x in x_by_column.items()}}


def other_anchors(args, lib: manifest.Library, tables: list[str]) -> dict:
    """Protocol (1) at the curves' other anchors, resonance against direct, and direct at 40 GHz (see the module docstring)."""
    variants = {"direct": (*OTHER_ANCHORS, F0), "resonance": OTHER_ANCHORS}
    jobs = []
    with ProcessPoolExecutor(max_workers=args.fold_workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        for t in tables:
            real = dataset.build(args.root, t, library=lib)
            gone = adopted(args.root, lib.strata[t].parts[0].store)
            for level in LEVELS:
                held = [r for r in real.rows if r.obs_id not in gone and r.coords[OD_P] == level]
                for variant, anchors in variants.items():
                    columns = [f"{c}@{f}" for f in anchors for c in ("Lp", "Ls")]
                    root = scratch_library(args.root, lib, t, args.scratch / t / f"{'fold' if variant == 'resonance' else 'direct'}_OD_P_{level:g}",
                                           level, changed=variant == "resonance")
                    rows = {c: [r for r in held if r.values.get(c) is not None] for c in columns}
                    x_by = {c: [[r.coords[d] for d in real.dims] for r in rs] for c, rs in rows.items()}
                    y_by = {c: [r.values[c] for r in rs] for c, rs in rows.items()}
                    jobs.append((t, level, variant, y_by, pool.submit(columns_job, str(root), t, x_by)))
        results = [(t, level, variant, y_by, job.result()) for t, level, variant, y_by, job in jobs]
    out = {}
    for t in tables:
        out[t] = {}
        for f in (*OTHER_ANCHORS, F0):
            for c in ("Lp", "Ls"):
                col = f"{c}@{f}"
                entry = {}
                for variant in variants:
                    ys, mus, per_level, seconds = [], [], {}, []
                    for tt, level, v, y_by, res in results:
                        if tt == t and v == variant and col in y_by:
                            ys += y_by[col]
                            mus += res["mu"][col]
                            per_level[f"{level:g}"] = len(y_by[col])
                            seconds.append(res["fit_s"])
                    if ys:
                        rel = np.abs(np.array(mus) - np.array(ys)) / np.array(ys)
                        entry[variant] = {"n": len(ys), "median_rel": float(np.median(rel)), "p90_rel": float(np.quantile(rel, 0.9)),
                                          "max_rel": float(rel.max()), "rows_per_level": per_level}
                out[t][col] = entry
        out[t]["fold_job_seconds"] = {v: [res["fit_s"] for tt, _lv, vv, _y, res in results if tt == t and vv == v] for v in variants}
    return out


def stats(y, mu, sigma, calibration: dict) -> dict:
    """Relative errors; coverage and median |z| with the calibration applied as the library applies it."""
    y, mu, sigma = (np.asarray(v, dtype=float) for v in (y, mu, sigma))
    rel = np.abs(mu - y) / y
    s = np.maximum(sigma / mu, calibration.get("median_rel", 0.0))
    z = np.log(y / mu) / s
    return {"n": len(y), "median_rel": float(np.median(rel)), "p90_rel": float(np.quantile(rel, 0.9)), "max_rel": float(rel.max()),
            "coverage": float(np.mean(np.abs(z) <= 2 * calibration["k_scale"])), "median_abs_z": float(np.median(np.abs(z)))}


def full_job(root: str, stratum: str, adopted_x: list[list[float]], targets: dict, steps: dict, workers: int) -> dict:
    """In a spawned process: the full (calibrated) library -- the direct models first, then the composed ones -- its
    predictions at the adopted rows, and the region answer."""
    t0 = time.time()
    lim = HostLimits(max_threads=2 * workers, max_memory_gb=64)
    lib = query.Library(root, limits=lim)
    rows = len(lib.dataset(stratum).rows)
    t1 = time.time()
    direct = ["Lp_lf", "Ls_lf", *REGION_EXTRA]
    lib.models(stratum, direct, workers=min(workers, len(direct)), threads=2 * min(workers, len(direct)))
    t2 = time.time()
    models = lib.models(stratum, list(COLUMNS), workers=workers, threads=2 * workers)
    t3 = time.time()
    xa = np.array(adopted_x)
    out = {"rows": rows, "seconds": {"dataset": round(t1 - t0, 1), "direct_models": round(t2 - t1, 1), "composed_models": round(t3 - t2, 1)},
           "calibration": {c: m.calibration for c, m in models.items()}, "adopted": {}}
    for column, m in models.items():
        mu, sigma = m.gp.predict(xa)
        lo, hi = m.gp.predict_bounds(xa)
        out["adopted"][column] = {"mu": mu.tolist(), "sigma": sigma.tolist(), "lo": lo.tolist(), "hi": hi.tolist(),
                                  "composition": m.gp.explain(xa, srf_unit=query.fit_unit("SRF"))}
    t4 = time.time()
    got = region.region(lib, stratum, targets, steps=steps, group_by=["primary_width_um", "secondary_width_um"],
                        trend=(f"k@{F0}", "center_spacing_um"), threads=2, workers=workers)
    out["region"] = {"levels": got["levels"], "grid": got["grid"], "binding": got["binding"], "measured": len(got["measured"]),
                     "notes": got["notes"], "wall_s": round(time.time() - t4, 1)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("scratch", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--tables", default="xfm_bs_ap,xfm_bs_m10")
    ap.add_argument("--fold-workers", type=int, default=12)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--other-anchors", action="store_true", help="add the other anchors' protocol (1) to an existing OUT_JSON")
    args = ap.parse_args()
    t_start = time.time()
    lib = manifest.load(args.root)
    tables = args.tables.split(",")
    if args.other_anchors:
        report = json.loads(args.out.read_text(encoding="utf-8"))
        report["other_anchors"] = other_anchors(args, lib, tables)
        report["other_anchors"]["wall_seconds"] = round(time.time() - t_start, 1)
        args.out.write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
        study = json.loads((HERE / "xfm_anchor_model_study.json").read_text(encoding="utf-8"))["tables"]
        for t in tables:
            for col, e in report["other_anchors"][t].items():
                if col.startswith(("Lp@", "Ls@")):
                    line = "  ".join(f"{v}: p90 {s['p90_rel'] * 100:.2f}% max {s['max_rel'] * 100:.2f}% (n {s['n']})" for v, s in e.items())
                    check = f" | study A p90 {study[t]['targets'][col]['protocols']['ii-OD_P']['A']['p90_rel'] * 100:.2f}%" if col.endswith(f"@{F0}") else ""
                    print(f"{t} {col}: {line}{check}", flush=True)
        print("wrote", args.out, f"{time.time() - t_start:.0f} s", flush=True)
        return
    study = json.loads((HERE / "xfm_anchor_model_study.json").read_text(encoding="utf-8"))["tables"]
    t15 = json.loads((HERE / "region_acceptance_40g_t15.json").read_text(encoding="utf-8"))
    truth, fulls, folds = {}, {}, []
    for t in tables:
        real = dataset.build(args.root, t, library=lib)                 # read only: a cache hit
        store = lib.strata[t].parts[0].store
        gone = adopted(args.root, store)
        base = [r for r in real.rows if r.obs_id not in gone]
        extra = [r for r in real.rows if r.obs_id in gone]
        truth[t] = {"base": base, "adopted": extra, "dims": real.dims}
        fulls[t] = scratch_library(args.root, lib, t, args.scratch / t / "full", None)
        for level in LEVELS:
            rows = [r for r in base if r.coords[OD_P] == level and all(r.values.get(c) is not None for c in COLUMNS)]
            folds.append((t, level, rows, scratch_library(args.root, lib, t, args.scratch / t / f"fold_OD_P_{level:g}", level)))
    print(f"scratch libraries ready ({len(fulls)} full, {len(folds)} folds) in {time.time() - t_start:.0f} s", flush=True)
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=args.fold_workers, mp_context=ctx) as fold_pool, \
            ProcessPoolExecutor(max_workers=len(tables), mp_context=ctx) as full_pool:
        full_jobs = {t: full_pool.submit(full_job, str(fulls[t]), t, [[r.coords[d] for d in truth[t]["dims"]] for r in truth[t]["adopted"]],
                                         t15["targets"], t15["steps"], args.workers) for t in tables}
        fold_jobs = [(t, level, rows, fold_pool.submit(fold_job, str(root), t, level, [[r.coords[d] for d in truth[t]["dims"]] for r in rows]))
                     for t, level, rows, root in folds]
        fold_out = [(t, level, rows, job.result()) for t, level, rows, job in fold_jobs]
        print(f"folds done at {time.time() - t_start:.0f} s", flush=True)
        full_out = {t: job.result() for t, job in full_jobs.items()}
    print(f"full libraries done at {time.time() - t_start:.0f} s", flush=True)
    report = {"date": time.strftime("%Y-%m-%d %H:%M"), "library": str(args.root), "anchor_ghz": F0,
              "manifest_changes": {"SRF": {"feature_map": MAPPED}, "Lp": {"model": "resonance", "feature_map": MAPPED},
                                   "Ls": {"model": "resonance", "feature_map": MAPPED}},
              "omp_num_threads": os.environ.get("OMP_NUM_THREADS"), "fold_workers": args.fold_workers, "workers": args.workers,
              "tables": {}}
    for t in tables:
        f = full_out[t]
        sd = study[t]["targets"]
        entry = {"rows_full": f["rows"], "adopted_left_out": [r.obs_id for r in truth[t]["adopted"]], "seconds": f["seconds"],
                 "protocol_i_calibration": {}, "protocol_ii_OD_P": {}, "protocol_iii": {}, "region": {}}
        for column in COLUMNS:
            cal = f["calibration"][column]
            study_cal = sd[column]["calibration"]["DF"]
            entry["protocol_i_calibration"][column] = {"library": cal, "study_DF": {"k_scale": study_cal["k_scale"], "median_rel": study_cal["floor"]},
                                                       "study_DF_protocol_i": sd[column]["protocols"]["i"]["DF"]}
            ys, mus, sigmas, levels = [], [], [], {}
            for tt, level, rows, out in fold_out:
                if tt != t:
                    continue
                y = [r.values[column] for r in rows]
                p = out["pred"][column]
                ys += y
                mus += p["mu"]
                sigmas += p["sigma"]
                levels[f"{level:g}"] = {"rows": len(rows), "fold_library_rows": out["rows"], "dataset_s": out["dataset_s"], "fit_s": out["fit_s"],
                                        **stats(y, p["mu"], p["sigma"], cal)}
            entry["protocol_ii_OD_P"][column] = {"pooled": stats(ys, mus, sigmas, cal), "levels": levels,
                                                 "study_DF": sd[column]["protocols"]["ii-OD_P"]["DF"],
                                                 "study_levels_DF": {f"{e['level']:g}": e["methods"]["DF"] for e in sd[column]["levels"] if e["dim"] == "OD_P"}}
            a = f["adopted"][column]
            y = np.array([r.values[column] for r in truth[t]["adopted"]])
            mu, lo, hi = np.array(a["mu"]), np.array(a["lo"]), np.array(a["hi"])
            s = np.maximum(np.array(a["sigma"]) / mu, cal.get("median_rel", 0.0))
            study_points = {p["obs_id"]: p["methods"]["DF"] for p in sd[column]["points"]}
            points = []
            for i, r in enumerate(truth[t]["adopted"]):
                st = study_points.get(r.obs_id, {})
                points.append({"obs_id": r.obs_id, "params": {SHORT[d]: r.coords[d] for d in truth[t]["dims"]}, "measured": float(y[i]),
                               "predicted": float(mu[i]), "lo": float(lo[i]), "hi": float(hi[i]), "error": float(y[i] / mu[i] - 1),
                               "z": float(np.log(y[i] / mu[i]) / s[i]), "inside": bool(lo[i] <= y[i] <= hi[i]),
                               "composition": a["composition"][i],
                               "study_DF_predicted": st.get("predicted"),
                               "vs_study": float(mu[i] / st["predicted"] - 1) if st.get("predicted") else None})
            rel = np.abs(mu - y) / y
            entry["protocol_iii"][column] = {"points": points, "max_rel": float(rel.max()), "median_rel": float(np.median(rel)),
                                             "max_error_signed": float(max((p["error"] for p in points), key=abs)),
                                             "coverage": float(np.mean([p["inside"] for p in points])),
                                             "study_DF": sd[column]["protocols"]["iii"]["DF"],
                                             "max_abs_vs_study": float(max(abs(p["vs_study"]) for p in points if p["vs_study"] is not None))}
        old = t15["strata"][t]["answer"]
        entry["region"] = {"library": f["region"], "t15": {"levels": {k: v["count"] for k, v in old["levels"].items()},
                                                           "ranges_robust": old["levels"]["robust"]["ranges"], "grid": old["grid"],
                                                           "measured": len(old["measured"])}}
        report["tables"][t] = entry
    report["wall_seconds"] = round(time.time() - t_start, 1)
    args.out.write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    for t, e in report["tables"].items():
        for column in COLUMNS:
            ii, iii = e["protocol_ii_OD_P"][column], e["protocol_iii"][column]
            print(f"{t} {column}: (ii) p90 {ii['pooled']['p90_rel'] * 100:.2f}% (study {ii['study_DF']['p90_rel'] * 100:.2f}%) "
                  f"max {ii['pooled']['max_rel'] * 100:.2f}% (study {ii['study_DF']['max_rel'] * 100:.2f}%) cov {ii['pooled']['coverage']:.3f}; "
                  f"(iii) max {iii['max_rel'] * 100:.2f}% (study {iii['study_DF']['max_rel'] * 100:.2f}%) cov {iii['coverage']:.2f} "
                  f"|pred/study-1| max {iii['max_abs_vs_study']:.2e}", flush=True)
        r = e["region"]
        print(f"{t} region: robust {r['library']['levels']['robust']['count']} (T15 {r['t15']['levels']['robust']}), "
              f"mean {r['library']['levels']['mean']['count']} (T15 {r['t15']['levels']['mean']})", flush=True)
    print("wrote", args.out, f"{report['wall_seconds']:.0f} s", flush=True)


if __name__ == "__main__":
    main()
