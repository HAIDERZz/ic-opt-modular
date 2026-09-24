"""Feasible parameter region of a single-turn / single-turn (xfm_bs) transformer for targets at one frequency.

A design question of 2026-09-24: "at 40 GHz, Lp and Ls both 150-170 pH, Q > 10, k = 0.5-0.7, single-turn primary
and secondary -- what parameter ranges should the sweep cover?" The library's models answer it as a region, not a
point: the stratum's five dims are gridded inside the sampled domain (box + hull of the DomainGuard), every grid
point is predicted for the anchored quantities at f0 and the system SRF, and the points whose predictions satisfy
the targets are summarised per dim, per width pair and per center spacing. Two feasibility levels are reported:

- mean:   the predicted mean satisfies every target;
- robust: the calibrated 2-sigma interval lies entirely inside every target window (what a sweep should center on).

The anchored columns need the manifest to carry f0 as an anchor (``anchors_ghz``); SRF must exceed
``srf_margin`` x f0, the library's own usability rule for anchored quantities.

Usage: xfm_bs_region.py LIBRARY_ROOT OUT_JSON [--f0 40] [--l-window 150 170] [--q-min 10] [--k-window 0.5 0.7]
       [--strata xfm_bs_ap xfm_bs_m10] [--od-step 2] [--w-step 1] [--cs-step 2] [--fig-dir DIR]
"""
from __future__ import annotations

import argparse
import itertools
import json
import time
from pathlib import Path

import numpy as np

QUANTITIES = ("Lp", "Ls", "Qp", "Qs", "k")
DIMS_SHORT = {"primary_outer_diameter_um": "OD_P", "secondary_outer_diameter_um": "OD_S", "primary_width_um": "W_P",
              "secondary_width_um": "W_S", "center_spacing_um": "CS"}


def _fit_one(job: tuple) -> tuple:
    """Worker: one quantity's model (calibration is cached per library; the GP fit itself is what costs ~2 min)."""
    root, stratum, quantity, threads = job
    from threadpoolctl import threadpool_limits

    from ic_opt.library import query

    with threadpool_limits(limits=threads):
        lib = query.Library(root, calibrate=True)
        return quantity, lib.model(stratum, quantity)


def fit_models(root: Path, stratum: str, names: list[str], workers: int) -> dict:
    """The stratum's models for ``names``, fitted in parallel processes (each GP optimisation is sequential and
    does not scale with BLAS threads, so six quantities in six processes cut ~12 min to ~3)."""
    import multiprocessing
    import os
    from concurrent.futures import ProcessPoolExecutor

    threads = max(1, int(os.environ.get("OMP_NUM_THREADS", "8")) // max(1, min(workers, len(names))))
    jobs = [(str(root), stratum, q, threads) for q in names]
    if workers <= 1:
        return dict(_fit_one(j) for j in jobs)
    with ProcessPoolExecutor(max_workers=min(workers, len(names)), mp_context=multiprocessing.get_context("fork")) as ex:
        return dict(ex.map(_fit_one, jobs))


def predict_all(models: dict, x: np.ndarray, k: float = 2.0, chunk: int = 40000) -> dict:
    """mu, sigma, lo, hi per quantity for every row of x, in chunks (n_train x n_query kernel blocks). One GP
    prediction per chunk: the calibrated bounds are derived from (mu, sigma) exactly as StratumGP.predict_bounds does."""
    from ic_opt.library.gp import prediction_bounds

    out = {q: {"mu": np.empty(len(x)), "sigma": np.empty(len(x)), "lo": np.empty(len(x)), "hi": np.empty(len(x))} for q in models}
    t0 = time.time()
    for start in range(0, len(x), chunk):
        sl = slice(start, min(start + chunk, len(x)))
        for q, m in models.items():
            scale = 1e9 if q.startswith("SRF") else 1.0            # the library fits SRF in GHz (query._predict maps it back the same way)
            mu, sigma = m.gp.predict(x[sl])
            lo, hi = prediction_bounds(mu, sigma, log_target=m.gp.log_target, k=k * m.gp.k_scale)
            out[q]["mu"][sl], out[q]["sigma"][sl], out[q]["lo"][sl], out[q]["hi"][sl] = mu * scale, sigma * scale, lo * scale, hi * scale
        print(f"    predicted {min(start + chunk, len(x))}/{len(x)} points x {len(models)} quantities ({time.time() - t0:.0f} s)", flush=True)
    return out


def grid(od_p: np.ndarray, od_s: np.ndarray, w_p: np.ndarray, w_s: np.ndarray, cs: np.ndarray) -> np.ndarray:
    """All combinations with the generator's overlap bound cs <= (OD_P + OD_S) / 4."""
    x = np.array(list(itertools.product(od_p, od_s, w_p, w_s, cs)), dtype=float)
    return x[x[:, 4] <= (x[:, 0] + x[:, 1]) / 4 + 1e-9]


def summarize(x: np.ndarray, mask: np.ndarray, dims: list[str]) -> dict:
    sel = x[mask]
    if not len(sel):
        return {"count": 0}
    return {"count": int(mask.sum()), "ranges": {DIMS_SHORT[d]: [float(sel[:, i].min()), float(sel[:, i].max())] for i, d in enumerate(dims)}}


def by_width(x: np.ndarray, mask: np.ndarray, pred: dict, f0: str) -> list[dict]:
    """For every (W_P, W_S) of the feasible set: how many points, and the OD / CS ranges they span."""
    rows = []
    for wp, ws in sorted({(float(a), float(b)) for a, b in x[mask][:, 2:4]}):
        m = mask & (x[:, 2] == wp) & (x[:, 3] == ws)
        sel = x[m]
        rows.append({"W_P": wp, "W_S": ws, "count": int(m.sum()),
                     "OD_P": [float(sel[:, 0].min()), float(sel[:, 0].max())], "OD_S": [float(sel[:, 1].min()), float(sel[:, 1].max())],
                     "CS": [float(sel[:, 4].min()), float(sel[:, 4].max())],
                     "Qmin": [float(np.minimum(pred[f"Qp@{f0}"]["mu"][m], pred[f"Qs@{f0}"]["mu"][m]).min()),
                              float(np.minimum(pred[f"Qp@{f0}"]["mu"][m], pred[f"Qs@{f0}"]["mu"][m]).max())]})
    return rows


def by_cs(x: np.ndarray, l_ok: np.ndarray, pred: dict, f0: str) -> list[dict]:
    """k at f0 against center spacing, over the points whose Lp and Ls sit in the window (k is what CS controls)."""
    rows = []
    for cs in sorted({float(v) for v in x[l_ok][:, 4]}):
        m = l_ok & (x[:, 4] == cs)
        k = pred[f"k@{f0}"]["mu"][m]
        rows.append({"CS": cs, "count": int(m.sum()), "k": [float(k.min()), float(k.max())], "k_median": float(np.median(k))})
    return rows


def candidates(x: np.ndarray, mask: np.ndarray, pred: dict, dims: list[str], ranges: dict, f0: str, n: int = 8, min_spacing: float = 0.08) -> list[dict]:
    """The feasible points with the highest worst-winding Q, kept apart by min_spacing of the scaled box."""
    idx = np.nonzero(mask)[0]
    if not len(idx):
        return []
    worst = np.minimum(pred[f"Qp@{f0}"]["mu"][idx], pred[f"Qs@{f0}"]["mu"][idx])
    order = idx[np.argsort(-worst)]
    lo = np.array([ranges[d][0] for d in dims]); hi = np.array([ranges[d][1] for d in dims])
    kept: list[int] = []
    for i in order:
        if all(np.abs((x[i] - x[j]) / (hi - lo)).max() >= min_spacing for j in kept):
            kept.append(int(i))
        if len(kept) == n:
            break
    out = []
    for i in kept:
        entry = {"params": {d: float(x[i, j]) for j, d in enumerate(dims)}, "quantities": {}}
        for q, p in pred.items():
            entry["quantities"][q] = {"mu": float(p["mu"][i]), "lo": float(p["lo"][i]), "hi": float(p["hi"][i])}
        out.append(entry)
    return out


def figure(path: Path, stratum: str, x: np.ndarray, dims: list[str], mean_ok: np.ndarray, robust_ok: np.ndarray, l_ok: np.ndarray,
           pred: dict, f0: str, hits: list[dict], k_window: tuple[float, float], q_min: float) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.family"] = ["Noto Sans CJK JP", "Droid Sans Fallback", "DejaVu Sans"]   # the SC face hides in a .ttc matplotlib lists under JP
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(1, 3, figsize=(16.5, 5.0))
    ax = axes[0]
    ax.scatter(x[mean_ok, 0], x[mean_ok, 1], s=9, c="#9fc7b3", label=f"预测均值满足（{int(mean_ok.sum())} 点）")
    ax.scatter(x[robust_ok, 0], x[robust_ok, 1], s=9, c="#2f6b4f", label=f"2σ 区间整体满足（{int(robust_ok.sum())} 点）")
    if hits:
        ax.scatter([h["params"]["primary_outer_diameter_um"] for h in hits], [h["params"]["secondary_outer_diameter_um"] for h in hits],
                   marker="*", s=160, c="#c8102e", edgecolors="k", linewidths=0.5, label=f"库内实测满足（{len(hits)} 行）", zorder=5)
    ax.set_xlabel("OD_P (µm)"); ax.set_ylabel("OD_S (µm)"); ax.set_title(f"{stratum}: 可行外径（各线宽/偏移投影）")
    ax.legend(fontsize=8, loc="upper left"); ax.grid(alpha=0.3)
    ax = axes[1]
    wp = sorted({float(v) for v in x[:, 2]}); ws = sorted({float(v) for v in x[:, 3]})
    counts = np.zeros((len(ws), len(wp)))
    for i, b in enumerate(ws):
        for j, a in enumerate(wp):
            counts[i, j] = (mean_ok & (x[:, 2] == a) & (x[:, 3] == b)).sum()
    im = ax.imshow(counts, origin="lower", cmap="Greens", aspect="auto")
    ax.set_xticks(range(len(wp))); ax.set_xticklabels([f"{v:g}" for v in wp]); ax.set_yticks(range(len(ws))); ax.set_yticklabels([f"{v:g}" for v in ws])
    for i in range(len(ws)):
        for j in range(len(wp)):
            if counts[i, j]:
                ax.text(j, i, f"{int(counts[i, j])}", ha="center", va="center", fontsize=7, color="k")
    ax.set_xlabel("W_P (µm)"); ax.set_ylabel("W_S (µm)"); ax.set_title("可行点数按线宽对（预测均值）")
    fig.colorbar(im, ax=ax, fraction=0.046)
    ax = axes[2]
    worst = np.minimum(pred[f"Qp@{f0}"]["mu"], pred[f"Qs@{f0}"]["mu"])
    sc = ax.scatter(x[l_ok, 4], pred[f"k@{f0}"]["mu"][l_ok], s=6, c=worst[l_ok], cmap="viridis", vmin=max(0, q_min - 4), vmax=q_min + 4)
    ax.axhspan(k_window[0], k_window[1], color="#2f6b4f", alpha=0.12, label=f"k 目标 {k_window[0]:g}–{k_window[1]:g}")
    ax.set_xlabel("CS 中心偏移 (µm)"); ax.set_ylabel(f"预测 k@{f0} GHz"); ax.set_title("电感落窗的点：k 随中心偏移（色 = min(Qp,Qs)）")
    ax.legend(fontsize=8, loc="upper right"); ax.grid(alpha=0.3)
    fig.colorbar(sc, ax=ax, fraction=0.046, label="min(Qp, Qs)")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--f0", type=float, default=40.0, help="GHz; must be an anchor of the strata")
    ap.add_argument("--l-window", type=float, nargs=2, default=(150.0, 170.0), help="pH, both windings")
    ap.add_argument("--q-min", type=float, default=10.0, help="both windings")
    ap.add_argument("--k-window", type=float, nargs=2, default=(0.5, 0.7))
    ap.add_argument("--strata", nargs="+", default=["xfm_bs_ap", "xfm_bs_m10"])
    ap.add_argument("--od-step", type=float, default=2.0)
    ap.add_argument("--w-step", type=float, default=1.0)
    ap.add_argument("--cs-step", type=float, default=2.0)
    ap.add_argument("--fig-dir", type=Path, default=None)
    ap.add_argument("--fit-workers", type=int, default=6, help="processes fitting the quantities in parallel")
    args = ap.parse_args()
    from ic_opt.library import domain, query

    f0 = f"{args.f0:g}"
    lib = query.Library(args.root, calibrate=True)
    l_lo, l_hi = args.l_window[0] * 1e-12, args.l_window[1] * 1e-12
    result = {"f0_ghz": args.f0, "constraints": {"L_pH": list(args.l_window), "Q_min": args.q_min, "k": list(args.k_window)},
              "grid_steps": {"OD": args.od_step, "W": args.w_step, "CS": args.cs_step}, "strata": {}}
    for st in args.strata:
        t0 = time.time()
        ds = lib.dataset(st)
        names = [f"{q}@{f0}" for q in QUANTITIES] + ["SRF"]
        models = fit_models(args.root, st, names, args.fit_workers)
        margin = min(r.srf_margin for r in lib.manifest.strata[st].quantities.values())
        ranges = lib.ranges(st)
        dims = ds.dims

        def ok_measured(r, names=tuple(names)):
            v = r.values
            return all(v.get(q) is not None for q in names) and l_lo <= v[f"Lp@{f0}"] <= l_hi and l_lo <= v[f"Ls@{f0}"] <= l_hi \
                and v[f"Qp@{f0}"] > args.q_min and v[f"Qs@{f0}"] > args.q_min and args.k_window[0] <= v[f"k@{f0}"] <= args.k_window[1]
        hits = [{"obs_id": r.obs_id, "part": r.part, "params": r.coords, "quantities": {q: r.values[q] for q in names}} for r in ds.rows if ok_measured(r)]

        # pass 1: coarse grid over the whole sampled box brackets the outer diameters that can reach the L window
        r_od = (min(ranges[dims[0]][0], ranges[dims[1]][0]), max(ranges[dims[0]][1], ranges[dims[1]][1]))
        od_c = np.arange(r_od[0], r_od[1] + 1e-9, 5.0)
        w_c = np.arange(ranges[dims[2]][0], ranges[dims[2]][1] + 1e-9, 2.0)
        xc = np.array([[a, b, wp, ws, frac * (a + b) / 4] for a in od_c for b in od_c for wp in w_c for ws in w_c for frac in (0, 0.25, 0.5, 0.75)])
        xc = xc[models[f"Lp@{f0}"].guard.inside(xc)]
        pc = predict_all({q: models[q] for q in (f"Lp@{f0}", f"Ls@{f0}")}, xc)
        # 10 % beyond the window brackets the fine pass generously: the models' held-out error on L is ~0.2 %
        loose = (pc[f"Lp@{f0}"]["mu"] >= 0.9 * l_lo) & (pc[f"Lp@{f0}"]["mu"] <= 1.1 * l_hi) & (pc[f"Ls@{f0}"]["mu"] >= 0.9 * l_lo) & (pc[f"Ls@{f0}"]["mu"] <= 1.1 * l_hi)
        print(f"{st}: models fitted and coarse pass done ({time.time() - t0:.0f} s); {len(xc)} coarse points in domain", flush=True)
        if not loose.any():
            result["strata"][st] = {"rows": len(ds.rows), "error": "no grid point of the sampled box predicts both inductances near the window"}
            print(f"{st}: nothing near the window"); continue
        bracket = [(float(xc[loose][:, i].min()) - args.od_step, float(xc[loose][:, i].max()) + args.od_step) for i in (0, 1)]
        bracket = [(max(b[0], ranges[dims[i]][0]), min(b[1], ranges[dims[i]][1])) for i, b in enumerate(bracket)]

        # pass 2: fine grid inside the bracket, every dim on the requested step, inside the domain guard
        od_p = np.arange(np.ceil(bracket[0][0] / args.od_step) * args.od_step, bracket[0][1] + 1e-9, args.od_step)
        od_s = np.arange(np.ceil(bracket[1][0] / args.od_step) * args.od_step, bracket[1][1] + 1e-9, args.od_step)
        w_p = np.arange(ranges[dims[2]][0], ranges[dims[2]][1] + 1e-9, args.w_step)
        w_s = np.arange(ranges[dims[3]][0], ranges[dims[3]][1] + 1e-9, args.w_step)
        cs_max = min(ranges[dims[4]][1], (od_p.max() + od_s.max()) / 4)
        cs = np.arange(0.0, cs_max + 1e-9, args.cs_step)
        x = grid(od_p, od_s, w_p, w_s, cs)
        x = np.round(x, 6)
        inside = models[f"Lp@{f0}"].guard.inside(x)
        print(f"{st}: fine grid OD_P {od_p[0]:g}–{od_p[-1]:g}, OD_S {od_s[0]:g}–{od_s[-1]:g}, {len(x)} points, {int(inside.sum())} inside the domain "
              f"({time.time() - t0:.0f} s)", flush=True)
        x = x[inside]
        pred = predict_all(models, x)
        fin = np.ones(len(x), dtype=bool)
        for q in names:
            fin &= np.isfinite(pred[q]["mu"]) & domain.sigma_ok(pred[q]["mu"], pred[q]["sigma"])
        Lp, Ls, Qp, Qs, K, SRF = (pred[q] for q in names)
        l_ok = fin & (Lp["mu"] >= l_lo) & (Lp["mu"] <= l_hi) & (Ls["mu"] >= l_lo) & (Ls["mu"] <= l_hi)
        mean_ok = l_ok & (Qp["mu"] > args.q_min) & (Qs["mu"] > args.q_min) & (K["mu"] >= args.k_window[0]) & (K["mu"] <= args.k_window[1]) \
            & (SRF["mu"] >= margin * args.f0 * 1e9)
        robust_ok = fin & (Lp["lo"] >= l_lo) & (Lp["hi"] <= l_hi) & (Ls["lo"] >= l_lo) & (Ls["hi"] <= l_hi) \
            & (Qp["lo"] > args.q_min) & (Qs["lo"] > args.q_min) & (K["lo"] >= args.k_window[0]) & (K["hi"] <= args.k_window[1]) \
            & (SRF["lo"] >= margin * args.f0 * 1e9)
        binding = {}
        for label, m in (("L", l_ok), ("Qp", fin & (Qp["mu"] > args.q_min)), ("Qs", fin & (Qs["mu"] > args.q_min)),
                         ("k", fin & (K["mu"] >= args.k_window[0]) & (K["mu"] <= args.k_window[1])), ("SRF", fin & (SRF["mu"] >= margin * args.f0 * 1e9))):
            binding[label] = int(m.sum())
        edge = {}
        for i, d in enumerate(dims):
            sel = x[mean_ok]
            if len(sel):
                edge[DIMS_SHORT[d]] = {"at_min": bool(np.isclose(sel[:, i].min(), ranges[d][0])), "at_max": bool(np.isclose(sel[:, i].max(), ranges[d][1]))}
        entry = {"rows": len(ds.rows), "usable_at_f0": len(ds.usable(f"Lp@{f0}")),
                 "models": {q: {"rows": len(models[q].rows), "k_scale": models[q].calibration.get("k_scale"),
                                "median_rel": models[q].calibration.get("median_rel"), "coverage": models[q].calibration.get("coverage_2sigma_before")} for q in names},
                 "srf_margin": margin, "domain_ranges": {DIMS_SHORT[d]: list(ranges[d]) for d in dims}, "od_bracket": bracket,
                 "grid_points": len(x), "grid_in_domain_and_confident": int(fin.sum()),
                 "measured_hits": hits, "binding": binding, "edge": edge, "l_window_points": int(l_ok.sum()),
                 "feasible_mean": summarize(x, mean_ok, dims), "feasible_robust": summarize(x, robust_ok, dims),
                 "by_width_mean": by_width(x, mean_ok, pred, f0), "by_width_robust": by_width(x, robust_ok, pred, f0),
                 "k_by_cs": by_cs(x, l_ok, pred, f0),
                 "candidates": candidates(x, robust_ok if robust_ok.any() else mean_ok, pred, dims, ranges, f0),
                 "candidates_level": "robust" if robust_ok.any() else "mean", "seconds": round(time.time() - t0)}
        if args.fig_dir:
            args.fig_dir.mkdir(parents=True, exist_ok=True)
            fig_path = args.fig_dir / f"xfm_bs_{f0}g_region_{st}.png"
            figure(fig_path, st, x, dims, mean_ok, robust_ok, l_ok, pred, f0, hits, tuple(args.k_window), args.q_min)
            entry["figure"] = str(fig_path)
        result["strata"][st] = entry
        print(f"{st}: {len(x)} grid points in domain, {int(fin.sum())} confident; L window {int(l_ok.sum())}; feasible mean {int(mean_ok.sum())}, "
              f"robust {int(robust_ok.sum())}; measured hits {len(hits)}; binding {binding}; {time.time() - t0:.0f} s")
        for level in ("feasible_mean", "feasible_robust"):
            s = entry[level]
            if s["count"]:
                print(f"   {level}: " + "  ".join(f"{d} {r[0]:g}–{r[1]:g}" for d, r in s["ranges"].items()))
    args.out.write_text(json.dumps(result, indent=1, default=float), encoding="utf-8")
    print("wrote", args.out)


if __name__ == "__main__":
    main()
