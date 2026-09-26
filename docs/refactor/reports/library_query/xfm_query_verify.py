"""Verify the transformer query strata with the ic_opt.library kernels (T13.7), on the protocol of ind_query_verify.py.

  0 integrity           dataset.check per stratum: rows, generation, duplicates, passivity, stored values, sweep grids
  1 forward prediction  held-out CV (5 seeds x 20 %) per query column with the library's own model settings; for the k
                        columns the dimensionless map against the identity map (and, for xfm_ms, identity per turns
                        level); 2-sigma coverage before calibration, and after it out of sample (factor from seeds 0-2,
                        coverage on seeds 3-4)
  2 SRF                 the GP (log GHz) against the mean of the 5 nearest measured neighbours, held-out
  3 domain guard        library points, midpoints of measured neighbour pairs, outside the box, non-integer turns
  4 inverse query       offline: train on 80 %, recommend from the held-out 20 % (true values known): Lp_lf and Ls_lf
                        windows +-5 % around a held-out design, maximise k_lf; first / top-3 precision and k regret
  5 example queries     lib.suggest on the full library (Sobol pool, calibrated conservative bounds, built and audited)

Usage: xfm_query_verify.py LIBRARY_ROOT OUT_JSON [STRATUM ...] [--workers 16] [--threads 2] [--columns Lp_lf,Lp@60]
       [--steps forward,srf,guard,inverse,examples]
Parallel by default (N-20, 2026-09-26): every (stratum, column, variant) of step 1 is one job in a process pool of
``--workers`` processes with ``--threads`` BLAS threads each (16 x 2 = the 32-thread budget of library compute; the
128-thread / 256 GB envelope is the simulators'); steps 2-5 then run per stratum in parallel. ``--columns`` restricts
step 1 to the columns named (verify what changed), ``--steps`` picks the steps. Results are identical to the serial run
(seeded splits, same kernels).
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from ic_opt.library import dataset, domain, gp, query
from ic_opt.library import suggest as sg

SEEDS_A, SEEDS_B = (0, 1, 2), (3, 4)


def settings(lib: query.Library, stratum: str, q: str, y: np.ndarray) -> dict:
    """What Library.model fits for this column (the same code path, spelled out so variants can differ in one key)."""
    ds = lib.dataset(stratum)
    fm = lib.manifest.strata[stratum].quantities[q.split("@")[0]].feature_map
    return {"dims": ds.dims, "ranges": lib.ranges(stratum), "log_target": bool((y > 0).all()),
            "nt_mode": "per_nt" if ds.nt_dim and not fm else "joint", "kernel": "matern52", "nt_dim": ds.nt_dim, "feature_map": fm}


def xy(lib: query.Library, stratum: str, q: str) -> tuple[np.ndarray, np.ndarray]:
    ds = lib.dataset(stratum)
    rows = ds.usable(q)
    y = ds.values(q, rows)
    return ds.matrix(rows), (y / 1e9 if q.startswith("SRF") else y)


def evaluate(x: np.ndarray, y: np.ndarray, st: dict) -> dict:
    a, b = gp.holdout(x, y, seeds=SEEDS_A, **st), gp.holdout(x, y, seeds=SEEDS_B, **st)
    rel = np.array(a["rel"] + b["rel"])
    z = np.array(a["z"] + b["z"])
    k_a = gp.calibration_scale(a)
    return {"n": len(y), "n_scored": len(rel), "skipped_unfitted_level": a["skipped_unfitted_level"] + b["skipped_unfitted_level"],
            "median_rel": float(np.median(rel)), "p90_rel": float(np.quantile(rel, 0.9)), "max_rel": float(rel.max()),
            "coverage_2sigma": float(np.mean(z <= 2.0)), "k_scale_seeds_0_2": k_a,
            "coverage_calibrated_seeds_3_4": float(np.mean(np.array(b["z"]) <= 2.0 * k_a)),
            "k_scale_all": gp.calibration_scale({"z": z.tolist()})}


def forward_jobs(lib: query.Library, stratum: str, columns: set[str] | None) -> list[tuple]:
    """Step 1 as independent jobs: (stratum, column, variant name, model settings) -- what forward() used to loop over."""
    ds, jobs = lib.dataset(stratum), []
    for q in ds.columns:
        if columns and q not in columns:
            continue
        _x, y = xy(lib, stratum, q)
        base = settings(lib, stratum, q, y)
        variants = {"library": base}
        if q.split("@")[0] in ("k", "k_lf") and base["feature_map"]:
            variants["identity"] = {**base, "feature_map": None, "nt_mode": "per_nt" if ds.nt_dim else "joint"}
            if ds.nt_dim:
                variants["identity-joint"] = {**base, "feature_map": None, "nt_mode": "joint"}
        for name, st in variants.items():
            jobs.append((stratum, q, name, st))
    return jobs


_LIBS: dict[str, query.Library] = {}


def _lib(root: str) -> query.Library:
    if root not in _LIBS:
        _LIBS[root] = query.Library(root, calibrate=False)
    return _LIBS[root]


def _worker_init(threads: int) -> None:
    os.environ["OMP_NUM_THREADS"] = os.environ["OPENBLAS_NUM_THREADS"] = os.environ["MKL_NUM_THREADS"] = str(threads)
    from threadpoolctl import threadpool_limits

    threadpool_limits(threads, user_api="blas")


def forward_job(root: str, stratum: str, q: str, name: str, st: dict) -> tuple:
    """One (column, variant) of step 1 in a worker process."""
    lib = _lib(root)
    x, y = xy(lib, stratum, q)
    t0 = time.time()
    r = {**evaluate(x, y, st), "feature_map": st["feature_map"], "nt_mode": st["nt_mode"], "seconds": round(time.time() - t0, 1)}
    return stratum, q, name, r


def print_forward(stratum: str, q: str, name: str, r: dict) -> None:
    print(f"  {stratum} {q:8s} {name:15s} n={r['n']:5d} median={r['median_rel'] * 100:.3f}% p90={r['p90_rel'] * 100:.3f}% "
          f"cov2s={r['coverage_2sigma']:.3f} k={r['k_scale_seeds_0_2']:.2f} cov_cal={r['coverage_calibrated_seeds_3_4']:.3f} ({r['seconds']:.0f}s)",
          flush=True)


def rest_job(root: str, stratum: str, steps: set[str]) -> tuple:
    """Steps 2-5 of one stratum in a worker process."""
    lib = _lib(root)
    out = {}
    if "srf" in steps:
        out["srf_knn"] = srf_knn(lib, stratum)
    if "guard" in steps:
        out["guard"] = guard_cases(lib, stratum)
    if "inverse" in steps:
        out["inverse"] = inverse_offline(lib, stratum)
    if "examples" in steps:
        out["examples"] = examples(query.Library(root), stratum)
    return stratum, out


def srf_knn(lib: query.Library, stratum: str) -> dict:
    """The 5-nearest-neighbour mean (scaled achieved box) on the same held-out splits the GP rows of `forward` used."""
    out = {}
    lo = np.array([lib.ranges(stratum)[d][0] for d in lib.dataset(stratum).dims])
    span = np.array([lib.ranges(stratum)[d][1] for d in lib.dataset(stratum).dims]) - lo
    for q in ("SRF", "SRF_p", "SRF_s"):
        if q not in lib.dataset(stratum).columns:
            continue
        x, y = xy(lib, stratum, q)
        rel, brackets = [], []
        for seed in SEEDS_A + SEEDS_B:
            test, train = gp.split(len(y), seed)
            _, idx = cKDTree((x[train] - lo) / span).query((x[test] - lo) / span, k=5)
            near = y[train][idx]
            rel += (np.abs(near.mean(axis=1) - y[test]) / y[test]).tolist()
            brackets += ((near.min(axis=1) <= y[test]) & (y[test] <= near.max(axis=1))).tolist()
        out[q] = {"n": len(y), "knn5_median_rel": float(np.median(rel)), "knn5_p90_rel": float(np.quantile(rel, 0.9)),
                  "knn5_brackets_truth": float(np.mean(brackets))}
    return out


def guard_cases(lib: query.Library, stratum: str) -> dict:
    ds = lib.dataset(stratum)
    x = ds.matrix()
    guard = domain.DomainGuard(x, ds.dims, lib.ranges(stratum), nt_dim=ds.nt_dim, ids=list(range(len(x))))
    rng = np.random.default_rng(0)
    _, idx = cKDTree((x - x.min(0)) / np.maximum(np.ptp(x, 0), 1e-9)).query((x - x.min(0)) / np.maximum(np.ptp(x, 0), 1e-9), k=2)
    mids = (x + x[idx[:, 1]]) / 2
    if ds.nt_dim:                                   # a midpoint keeps an integer turns level: pair rows within a level only
        nt = ds.dims.index(ds.nt_dim)
        mids = mids[np.abs(mids[:, nt] - np.round(mids[:, nt])) < 1e-9]
    cases = {"library points": x, "midpoints of nearest measured pairs": mids}
    box_lo, box_hi = x.min(0), x.max(0)
    outside = []
    for j in range(len(ds.dims)):
        for edge, step in ((box_lo, -0.1), (box_hi, 0.1)):
            p = x[rng.integers(len(x))].copy()
            p[j] = edge[j] + step * max(abs(edge[j]), 1.0)
            outside.append(p)
    cases["outside the box (one dim 10 % beyond)"] = np.array(outside)
    if ds.nt_dim:
        p = x[rng.integers(len(x), size=5)].copy()
        p[:, ds.dims.index(ds.nt_dim)] += 0.5
        cases["non-integer turns"] = p
    out = {}
    for name, pts in cases.items():
        inside = guard.inside(pts)
        tally = {"n": len(pts), "accept": int(inside.sum())}
        for p in pts[~inside][:200]:
            try:
                guard.check(dict(zip(ds.dims, p)))
            except domain.OutOfDomainError as exc:
                tally[f"reject:c{exc.criterion}"] = tally.get(f"reject:c{exc.criterion}", 0) + 1
        out[name] = tally
    return out


def inverse_offline(lib: query.Library, stratum: str, tol: float = 0.05) -> dict:
    """Held-out rows as the candidate pool (truth known); models trained on the rest, gated exactly as lib.suggest gates."""
    ds = lib.dataset(stratum)
    cols = ("Lp_lf", "Ls_lf", "k_lf")
    rows = [r for r in ds.rows if all(r.values.get(c) is not None for c in cols)]
    x = ds.matrix(rows)
    truth = {c: np.array([r.values[c] for r in rows]) for c in cols}
    results = {"conservative": [], "mean only": []}
    for seed in SEEDS_A + SEEDS_B:
        test, train = gp.split(len(rows), seed)
        models = {}
        for c in cols:
            st = settings(lib, stratum, c, truth[c][train])
            model = gp.StratumGP(**st).fit(x[train], truth[c][train])
            guard = domain.DomainGuard(x[train], ds.dims, st["ranges"], nt_dim=ds.nt_dim, ids=list(range(len(train))))
            models[c] = query.Model(stratum, c, [], model, guard, {"k_scale": 1.0})
        xt = x[test]
        for i in np.random.default_rng(seed).choice(len(test), size=min(40, len(test)), replace=False):
            lp0, ls0 = truth["Lp_lf"][test][i], truth["Ls_lf"][test][i]
            goals = [sg.Target("Lp_lf", "target", lp0, tol), sg.Target("Ls_lf", "target", ls0, tol)]
            truly = ((np.abs(truth["Lp_lf"][test] / lp0 - 1) <= tol) & (np.abs(truth["Ls_lf"][test] / ls0 - 1) <= tol))
            oracle = float(truth["k_lf"][test][truly].max())
            for mode, k in (("conservative", 2.0), ("mean only", 0.0)):
                scored = sg.score(xt, models, goals, ("max", "k_lf"), k=k, rel_sigma_max=np.inf if k == 0 else domain.DEFAULT_SIGMA_REL_MAX)
                ranked = scored["ranked"]
                if not ranked:
                    results[mode].append({"found": 0})
                    continue
                first = ranked[0]
                results[mode].append({"found": len(ranked), "first_is_hit": bool(truly[first]),
                                      "top3_precision": float(np.mean(truly[ranked[:3]])),
                                      "k_regret": float((oracle - truth["k_lf"][test][first]) / oracle) if truly[first] else None})
    summary = {}
    for mode, rs in results.items():
        answered = [r for r in rs if r["found"]]
        regrets = [r["k_regret"] for r in answered if r["k_regret"] is not None]
        summary[mode] = {"queries": len(rs), "answered": len(answered),
                         "first_hit_rate": float(np.mean([r["first_is_hit"] for r in answered])) if answered else None,
                         "top3_precision": float(np.mean([r["top3_precision"] for r in answered])) if answered else None,
                         "median_k_regret": float(np.median(regrets)) if regrets else None}
    return summary


EXAMPLES = {   # stratum -> (name, targets, objective)
    "xfm_bs_ap": [("Lp 0.30 nH +-5 %, Ls 0.30 nH +-5 %, max k_lf", {"Lp_lf": {"target": 0.30e-9, "tol": 0.05}, "Ls_lf": {"target": 0.30e-9, "tol": 0.05}}, "max:k_lf"),
                  ("k@28 >= 0.7, Lp 0.5 nH +-5 %, max Qp_peak", {"k@28": {"min": 0.7}, "Lp_lf": {"target": 0.5e-9, "tol": 0.05}}, "max:Qp_peak")],
    "xfm_bs_m10": [("Lp 0.25 nH +-5 %, Ls 0.25 nH +-5 %, max k_lf", {"Lp_lf": {"target": 0.25e-9, "tol": 0.05}, "Ls_lf": {"target": 0.25e-9, "tol": 0.05}}, "max:k_lf"),
                   ("k@28 >= 0.7, Lp 0.4 nH +-5 %, max Qp_peak", {"k@28": {"min": 0.7}, "Lp_lf": {"target": 0.4e-9, "tol": 0.05}}, "max:Qp_peak")],
    "xfm_ms_ap": [("Lp 0.2 nH +-5 %, Ls 0.6 nH +-5 % (1:1.7), max k_lf", {"Lp_lf": {"target": 0.2e-9, "tol": 0.05}, "Ls_lf": {"target": 0.6e-9, "tol": 0.05}}, "max:k_lf")],
    "xfm_ms_m10": [("Lp 0.2 nH +-5 %, Ls 0.6 nH +-5 % (1:1.7), max k_lf", {"Lp_lf": {"target": 0.2e-9, "tol": 0.05}, "Ls_lf": {"target": 0.6e-9, "tol": 0.05}}, "max:k_lf")],
}


def examples(lib: query.Library, stratum: str) -> list[dict]:
    out = []
    for name, targets, objective in EXAMPLES.get(stratum, []):
        try:
            answer = sg.suggest(lib, stratum, targets, objective, n=3)
        except ValueError as exc:
            out.append({"name": name, "error": str(exc)})
            continue
        out.append({"name": name, **{k: answer[k] for k in ("pool", "satisfying", "satisfying_measured", "notes")},
                    "measured": answer["measured"][:3], "candidates": answer["candidates"]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("strata", nargs="*")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--threads", type=int, default=2, help="BLAS threads per worker (workers x threads = the compute budget, 32)")
    ap.add_argument("--columns", default="", help="comma list: restrict step 1 to these columns")
    ap.add_argument("--steps", default="forward,srf,guard,inverse,examples")
    args = ap.parse_args()
    root, out_path = args.root, args.out
    columns = {c for c in args.columns.split(",") if c} or None
    steps = {s for s in args.steps.split(",") if s}
    lib = query.Library(root, calibrate=False)
    strata = args.strata or [s for s in lib.strata() if s.startswith("xfm_")]
    report, t0 = {"library": str(root), "strata": strata, "workers": args.workers, "threads_per_worker": args.threads,
                  "columns": sorted(columns) if columns else "all", "steps": sorted(steps)}, time.time()
    jobs = []
    for stratum in strata:
        ds = lib.dataset(stratum)
        report[stratum] = {"integrity": dataset.check(ds), "forward": {}}
        print(f"{stratum}: {len(ds.rows)} rows, generation {ds.generations}", flush=True)
        if "forward" in steps:
            jobs += forward_jobs(lib, stratum, columns)
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx, initializer=_worker_init, initargs=(args.threads,)) as pool:
        futures = [pool.submit(forward_job, str(root), *job) for job in jobs]
        for fut in as_completed(futures):
            stratum, q, name, r = fut.result()
            report[stratum]["forward"][f"{q}|{name}"] = r
            print_forward(stratum, q, name, r)
        for stratum in strata:                          # the dict in column order, as the serial run wrote it
            order = [f"{q}|{name}" for _s, q, name, _st in jobs if _s == stratum]
            report[stratum]["forward"] = {k: report[stratum]["forward"][k] for k in order if k in report[stratum]["forward"]}
        print(f"forward done at {time.time() - t0:.0f} s ({len(jobs)} jobs, {args.workers} x {args.threads} threads)", flush=True)
        out_path.write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    rest = steps - {"forward"}
    if rest:
        per = max(1, min(args.workers * args.threads // max(1, len(strata)), args.workers * args.threads))
        with ProcessPoolExecutor(max_workers=len(strata), mp_context=ctx, initializer=_worker_init, initargs=(per,)) as pool:
            for stratum, out in (f.result() for f in as_completed([pool.submit(rest_job, str(root), st, rest) for st in strata])):
                report[stratum].update(out)
                print(f"{stratum}: {' / '.join(sorted(out))} done at {time.time() - t0:.0f} s", flush=True)
    report["seconds"] = round(time.time() - t0, 1)
    out_path.write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    print("wrote", out_path, f"{report['seconds']:.0f} s", flush=True)


if __name__ == "__main__":
    main()
