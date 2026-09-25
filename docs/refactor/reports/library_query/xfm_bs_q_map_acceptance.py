"""N-19 option 1 acceptance on the N28 single-turn transformer tables (xfm_bs_ap, xfm_bs_m10): do the Q curve columns get
better when they see the dimensionless inputs (``Qp`` / ``Qs``: ``feature_map: xfm_bs_dimensionless``), as Lp / Ls / k
already do? Nothing else changes; the real library is only read.

Variants of the manifest, on scratch libraries built under SCRATCH_DIR (the part's observations as they are today --
adopted rows included, since the test designs below were never adopted; spec.json and sims/ linked to the real store):

    direct : as it is today (Qp / Qs: one GP per column on the dims)
    mapped : Qp / Qs: {feature_map: xfm_bs_dimensionless}

  (ii)  leave one level out: every OD_P / OD_S level the T16.2a study held out (>= 25 usable rows) is removed from a fold
        library fitted without calibration; its Qp@40 / Qs@40 / Qp@60 / Qs@60 rows are predicted; pooled median / p90 /
        max relative error per dim and variant;
  (iii) independent designs: the full (calibrated) library of each variant predicts the sign-off test designs that were
        measured but never adopted (N-17's ten per table, B-12's ten for xfm_bs_ap): error, calibrated 2-sigma coverage;
  (i)   the full library's calibration for the four columns: k_scale, typical error, coverage before scaling.

Usage: xfm_bs_q_map_acceptance.py LIBRARY_ROOT SCRATCH_DIR OUT_JSON [--tables xfm_bs_ap,xfm_bs_m10] [--fold-workers 12]
       [--workers 4]
Run with OMP_NUM_THREADS=2.
"""
from __future__ import annotations

import argparse
import json
import math
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
F0 = 40
COLUMNS = ("Qp@40", "Qs@40", "Qp@60", "Qs@60")
CURVES = ("Qp", "Qs")
MAPPED = "xfm_bs_dimensionless"
DIM_OF = {"OD_P": "primary_outer_diameter_um", "OD_S": "secondary_outer_diameter_um"}
CHANGES = {"direct": {}, "mapped": {"feature_map": MAPPED}}
MIN_LEVEL_ROWS = 25
TEST_FILES = {"xfm_bs_ap": ["n17_test10_signoff_xfm_bs_ap.json", "b12_test10_signoff_xfm_bs_ap.json"],
              "xfm_bs_m10": ["n17_test10_signoff_xfm_bs_m10.json"]}


def scratch_library(real: Path, lib: manifest.Library, stratum: str, dest: Path, drop: tuple[str, float] | None, variant: str) -> Path:
    s = lib.strata[stratum]
    (store,) = [p.store for p in s.parts]
    icopt = dest / store / ".icopt"
    icopt.mkdir(parents=True, exist_ok=True)
    kept = []
    for line in (real / store / ".icopt" / "observations.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        if drop is not None and float(o["params"][drop[0]]) == drop[1]:
            continue
        kept.append(line)
    (icopt / "observations.jsonl").write_text("\n".join(kept) + "\n", encoding="utf-8")
    for name in ("spec.json", "sims", "adopted.yaml"):
        src = real / store / ".icopt" / name
        link = icopt / name
        if src.exists() and not link.exists():
            link.symlink_to(src)
    doc = lib.model_dump(mode="json")
    quantities = doc["strata"][stratum]["quantities"]
    for curve in CURVES:
        quantities[curve] = {**quantities[curve], **CHANGES[variant]}
    doc["strata"] = {stratum: doc["strata"][stratum]}
    (dest / "library.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return dest


def fold_job(root: str, stratum: str, x_by_column: dict[str, list[list[float]]]) -> dict:
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


def full_job(root: str, stratum: str, workers: int, tests: dict[str, list[list[float]]]) -> dict:
    """The full (calibrated) library of a variant: calibration of the four columns and its predictions at the test designs."""
    t0 = time.time()
    lib = query.Library(root, limits=HostLimits(max_threads=2 * workers, max_memory_gb=64))
    rows = len(lib.dataset(stratum).rows)
    t1 = time.time()
    models = lib.models(stratum, list(COLUMNS), workers=workers, threads=2 * workers)
    t2 = time.time()
    out = {"rows": rows, "seconds": {"dataset": round(t1 - t0, 1), "curve_models": round(t2 - t1, 1)},
           "calibration": {c: m.calibration for c, m in models.items()}, "tests": {}}
    for c, x in tests.items():
        if not x:
            out["tests"][c] = {"mu": [], "lo": [], "hi": []}
            continue
        xa = np.array(x)
        mu, _sigma = models[c].gp.predict(xa)
        lo, hi = models[c].gp.predict_bounds(xa)
        out["tests"][c] = {"mu": mu.tolist(), "lo": lo.tolist(), "hi": hi.tolist()}
    return out


def rel_stats(y, mu) -> dict:
    y, mu = np.asarray(y, dtype=float), np.asarray(mu, dtype=float)
    rel = np.abs(mu - y) / np.abs(y)
    return {"n": len(y), "median_rel": float(np.median(rel)), "p90_rel": float(np.quantile(rel, 0.9)), "max_rel": float(rel.max())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("scratch", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--tables", default="xfm_bs_ap,xfm_bs_m10")
    ap.add_argument("--fold-workers", type=int, default=12)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    t_start = time.time()
    lib = manifest.load(args.root)
    tables = args.tables.split(",")
    variants = list(CHANGES)
    study = json.loads((HERE / "xfm_anchor_model_study.json").read_text(encoding="utf-8"))["tables"]
    folds, fulls, held, tests = [], {}, {}, {}
    for t in tables:
        real = dataset.build(args.root, t, library=lib)
        dims = real.dims
        levels = [(e["dim"], float(e["level"])) for e in study[t]["targets"][f"Lp@{F0}"]["levels"]
                  if e["dim"] in DIM_OF and not e.get("skipped") and e["n"] >= MIN_LEVEL_ROWS]
        held[t] = {}
        for short, level in levels:
            dim = DIM_OF[short]
            rows = {c: [r for r in real.rows if r.coords[dim] == level and r.values.get(c) is not None] for c in COLUMNS}
            held[t][(short, level)] = rows
            for v in variants:
                root = scratch_library(args.root, lib, t, args.scratch / t / f"{v}_{short}_{level:g}", (dim, level), v)
                folds.append((t, short, level, v, root, {c: [[r.coords[d] for d in dims] for r in rs] for c, rs in rows.items()}))
        # the independent test designs (measured, never adopted): params + measured values per column
        pts = []
        for name in TEST_FILES[t]:
            rep = json.loads((HERE / name).read_text(encoding="utf-8"))
            for p in rep["points"]:
                if real.find({d: float(p["params"][d]) for d in dims}) is not None:
                    continue                                       # adopted after all: not a test design
                pts.append({"source": name.split("_")[0], "obs_id": p["obs_id"], "params": {d: float(p["params"][d]) for d in dims},
                            "measured": {c: p["quantities"].get(c, {}).get("measured") for c in COLUMNS}})
        tests[t] = pts
        for v in variants:
            fulls[(t, v)] = scratch_library(args.root, lib, t, args.scratch / t / f"full_{v}", None, v)
    print(f"scratch libraries ready: {len(folds)} folds, {len(fulls)} full, tests {{{', '.join(f'{t}: {len(tests[t])}' for t in tables)}}}, "
          f"in {time.time() - t_start:.0f} s", flush=True)
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=args.fold_workers, mp_context=ctx) as fold_pool, \
            ProcessPoolExecutor(max_workers=max(1, len(fulls)), mp_context=ctx) as full_pool:
        full_jobs = {}
        for (t, v), root in fulls.items():
            dims = dataset.build(args.root, t, library=lib).dims
            x_tests = {c: [[p["params"][d] for d in dims] for p in tests[t] if p["measured"].get(c) is not None] for c in COLUMNS}
            full_jobs[(t, v)] = full_pool.submit(full_job, str(root), t, args.workers, x_tests)
        fold_jobs = [(t, short, level, v, fold_pool.submit(fold_job, str(root), t, x_by)) for t, short, level, v, root, x_by in folds]
        fold_out = [(t, short, level, v, job.result()) for t, short, level, v, job in fold_jobs]
        print(f"folds done at {time.time() - t_start:.0f} s", flush=True)
        full_out = {k: job.result() for k, job in full_jobs.items()}
    print(f"full libraries done at {time.time() - t_start:.0f} s", flush=True)
    report = {"date": time.strftime("%Y-%m-%d %H:%M"), "library": str(args.root), "anchor_ghz": F0, "variants": CHANGES,
              "omp_num_threads": os.environ.get("OMP_NUM_THREADS"), "fold_workers": args.fold_workers, "workers": args.workers,
              "min_level_rows": MIN_LEVEL_ROWS, "tables": {}}
    for t in tables:
        entry = {"protocol_ii": {}, "protocol_iii_tests": {}, "protocol_i_calibration": {}, "fold_seconds": {}}
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
                            per_level[f"{level:g}"] = {**rel_stats(y, out["pred"][c]["mu"]), "fit_s": out["fit_s"]} if y else {"n": 0}
                    if ys:
                        per_variant[v] = {"pooled": rel_stats(ys, mus), "levels": per_level}
                entry["protocol_ii"][c][short] = per_variant
        for c in COLUMNS:
            pts = [p for p in tests[t] if p["measured"].get(c) is not None]
            per_variant = {}
            for v in variants:
                f = full_out[(t, v)]["tests"][c]
                y = np.array([p["measured"][c] for p in pts], dtype=float)
                mu, lo, hi = (np.array(f[k], dtype=float) for k in ("mu", "lo", "hi"))
                if len(y):
                    inside = (lo <= y) & (y <= hi)
                    per_variant[v] = {**rel_stats(y, mu), "coverage": float(inside.mean()),
                                      "points": [{"source": p["source"], "obs_id": p["obs_id"], "measured": float(y[i]), "predicted": float(mu[i]),
                                                  "rel": float(mu[i] / y[i] - 1), "inside": bool(inside[i])} for i, p in enumerate(pts)]}
            entry["protocol_iii_tests"][c] = per_variant
        for v in variants:
            f = full_out[(t, v)]
            entry["protocol_i_calibration"][v] = {"rows": f["rows"], "seconds": f["seconds"], "calibration": f["calibration"]}
        entry["fold_seconds"] = {v: [out["fit_s"] for tt, _s, _l, vv, out in fold_out if tt == t and vv == v] for v in variants}
        report["tables"][t] = entry
    report["wall_seconds"] = round(time.time() - t_start, 1)
    args.out.write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    for t in tables:
        e = report["tables"][t]
        for c in COLUMNS:
            for short in DIM_OF:
                parts = [f"{v}: p90 {s['pooled']['p90_rel'] * 100:.1f}% max {s['pooled']['max_rel'] * 100:.1f}%" for v, s in e["protocol_ii"][c][short].items()]
                print(f"{t} {c} leave-one-{short}-out  " + " | ".join(parts), flush=True)
            parts = [f"{v}: med {s['median_rel'] * 100:.1f}% max {s['max_rel'] * 100:.1f}% cov {s['coverage']:.2f} (n {s['n']})" for v, s in e["protocol_iii_tests"][c].items()]
            print(f"{t} {c} independent test designs  " + " | ".join(parts), flush=True)
        for v in variants:
            cal = {c: f"k {m['k_scale']:.2f} typical {m.get('median_rel', math.nan) * 100:.2f}% cov-before {m.get('coverage_2sigma_before', math.nan):.2f}"
                   for c, m in e["protocol_i_calibration"][v]["calibration"].items()}
            print(f"{t} full {v}: {cal} seconds {e['protocol_i_calibration'][v]['seconds']}", flush=True)
    print("wrote", args.out, f"{report['wall_seconds']:.0f} s", flush=True)


if __name__ == "__main__":
    main()
