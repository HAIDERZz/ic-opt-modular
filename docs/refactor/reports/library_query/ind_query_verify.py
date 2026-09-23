"""Verify the N28 inductor query library end to end with the current query kernels (em-opt's StratumGP and DomainGuard,
imported read-only from the em-opt workspace; nothing there is modified).

  1 forward prediction   held-out CV (5 seeds x 20%), per body and query quantity, default model per_nt-matern52 vs
                         joint-matern52; relative error and 2-sigma coverage of the prediction bounds
  2 SRF                  GP on log SRF and em-opt's 5-nearest-neighbour rule, held-out
  3 domain guard         library points, cell midpoints, outside the box, unbuildable (inner < 30 um), non-integer NT;
                         as shipped and with degenerate dims dropped per NT level
  4 inverse query        offline: train on 80%, recommend from the held-out 20% (true values known), precision of the
                         recommended designs and regret against the best true design, with and without 2-sigma bounds
  5 example queries      full-data model on a Sobol pool, conservative bounds, top candidates built with the ic-opt
                         generator (geometry 7) and audited; EMX sign-off of these is left for later (needs approval)

Usage: ind_query_verify.py DATASET_JSON OUT_DIR
"""
from __future__ import annotations

import itertools
import json
import sys
import tempfile
import time
import warnings
from pathlib import Path

import numpy as np

EMOPT_SRC = "/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/src"
sys.path.insert(0, EMOPT_SRC)
from em_ic_opt_workflow.surrogate.domain import DomainGuard, OutOfDomainError
from em_ic_opt_workflow.surrogate.model import StratumGP

DIMS = ["outer_diameter_um", "width_um", "spacing_um", "turns"]
COLS = {"outer_diameter_um": "od", "width_um": "w", "spacing_um": "s", "turns": "nt"}
RANGES = {"outer_diameter_um": (60.0, 240.0), "width_um": (4.0, 10.0), "spacing_um": (2.0, 4.0), "turns": (1.0, 5.0)}
# query quantity -> anchor frequency (GHz) or None; every one is fitted on log values (all strictly positive)
TARGETS = {"L_lf": None, "L_res": None, "Q_peak150": None, "L@28": 28, "Q@28": 28, "L@60": 60, "Q@60": 60}
SRF_ANCHOR_MARGIN = 1.25          # em-opt: rows whose SRF <= 1.25 x f0 are not trained / scored at f0
MODELS = {"per_nt-matern52": ("per_nt", "matern52"), "joint-matern52": ("joint", "matern52")}
SEEDS, HOLDOUT, K_SIGMA = (0, 1, 2, 3, 4), 0.2, 2.0
warnings.filterwarnings("ignore", module="sklearn")      # GP optimiser convergence chatter; the CV numbers are the evidence


def fitted_levels(gp: StratumGP, x: np.ndarray) -> np.ndarray:
    """Rows the model can answer: every row for a joint model, rows whose NT level got a sub-GP for per_nt."""
    if gp.nt_mode != "per_nt":
        return np.ones(len(x), dtype=bool)
    return np.array([round(v) in gp._sub_gps for v in x[:, DIMS.index("turns")]], dtype=bool)


def matrix(rows: list[dict]) -> np.ndarray:
    return np.array([[r[COLS[d]] for d in DIMS] for r in rows], dtype=float)


def usable(rows: list[dict], target: str) -> list[dict]:
    f0 = TARGETS[target]
    out = [r for r in rows if r.get(target) is not None and r[target] > 0]
    if f0:
        out = [r for r in out if r["SRF"] is None or r["SRF"] > SRF_ANCHOR_MARGIN * f0 * 1e9]
    return out


def fit(rows: list[dict], target: str, model: str) -> StratumGP:
    nt_mode, kernel = MODELS[model]
    gp = StratumGP(dims=DIMS, ranges=RANGES, log_target=True, nt_mode=nt_mode, kernel=kernel, nt_dim="turns")
    return gp.fit(matrix(rows), np.array([r[target] for r in rows]))


def split(n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    order = np.random.default_rng(seed).permutation(n)
    k = round(HOLDOUT * n)
    return order[:k], order[k:]


# --- 1 forward prediction ------------------------------------------------------------------------------------------

def forward_cv(rows: list[dict]) -> dict:
    out = {}
    for target in TARGETS:
        data = usable(rows, target)
        for model in MODELS:
            rel, inside, by_nt, skipped = [], [], {}, 0
            for seed in SEEDS:
                test, train = split(len(data), seed)
                gp = fit([data[i] for i in train], target, model)
                keep = fitted_levels(gp, matrix([data[i] for i in test]))
                skipped += int((~keep).sum())
                test = test[keep]
                xt = matrix([data[i] for i in test])
                yt = np.array([data[i][target] for i in test])
                mu, _ = gp.predict(xt)
                lo, hi = gp.predict_bounds(xt, K_SIGMA)
                e = np.abs(mu - yt) / yt
                rel += e.tolist()
                inside += ((yt >= lo) & (yt <= hi)).tolist()
                for i, ei in zip(test, e):
                    by_nt.setdefault(data[i]["nt"], []).append(float(ei))
            rel_a = np.array(rel)
            out[(target, model)] = {"n": len(data), "n_scored": len(rel), "skipped_unfitted_level": skipped,
                                    "median_rel": float(np.median(rel_a)),
                                    "p90_rel": float(np.quantile(rel_a, 0.9)), "max_rel": float(rel_a.max()),
                                    "coverage_2sigma": float(np.mean(inside)),
                                    "median_rel_by_nt": {int(k): float(np.median(v)) for k, v in sorted(by_nt.items())}}
    return out


# --- 2 SRF ---------------------------------------------------------------------------------------------------------

def srf_cv(rows: list[dict]) -> dict:
    data = [dict(r, SRF_GHz=r["SRF"] / 1e9) for r in rows if r["SRF"] is not None]
    rel_gp, inside, rel_nn, nn_all_side = [], [], [], []
    for seed in SEEDS:
        test, train = split(len(data), seed)
        tr, te = [data[i] for i in train], [data[i] for i in test]
        gp = StratumGP(dims=DIMS, ranges=RANGES, log_target=True, nt_mode="per_nt", kernel="matern52", nt_dim="turns")
        gp.fit(matrix(tr), np.array([r["SRF_GHz"] for r in tr]))
        te = [r for r, a in zip(te, fitted_levels(gp, matrix(te))) if a]
        xt, yt = matrix(te), np.array([r["SRF_GHz"] for r in te])
        mu, _ = gp.predict(xt)
        lo, hi = gp.predict_bounds(xt, K_SIGMA)
        rel_gp += (np.abs(mu - yt) / yt).tolist()
        inside += ((yt >= lo) & (yt <= hi)).tolist()
        # em-opt suggest: a SRF constraint passes only if all 5 nearest measured neighbours pass (scaled distance, all dims)
        x_tr = matrix(tr)
        for r, y in zip(te, yt):
            vals = [tr[j]["SRF_GHz"] for j in nearest_idx(x_tr, r)]
            rel_nn.append(abs(np.mean(vals) - y) / y)
            nn_all_side.append(min(vals) <= y <= max(vals))
    return {"n": len(data), "gp_median_rel": float(np.median(rel_gp)), "gp_p90_rel": float(np.quantile(rel_gp, 0.9)),
            "gp_coverage_2sigma": float(np.mean(inside)), "knn5_mean_median_rel": float(np.median(rel_nn)),
            "knn5_mean_p90_rel": float(np.quantile(rel_nn, 0.9)), "knn5_brackets_truth": float(np.mean(nn_all_side))}


def scaled(x: np.ndarray) -> np.ndarray:
    lo = np.array([RANGES[d][0] for d in DIMS])
    hi = np.array([RANGES[d][1] for d in DIMS])
    return (x - lo) / (hi - lo)


def nearest_idx(x_train: np.ndarray, r: dict, k: int = 5) -> list[int]:
    q = scaled(matrix([r]))[0]
    d = np.linalg.norm(scaled(x_train) - q, axis=1)
    return list(np.argsort(d)[:k])


# --- 3 domain guard ------------------------------------------------------------------------------------------------

def verdict(guard, params: dict) -> str:
    try:
        guard.check(params)
        return "accept"
    except OutOfDomainError as exc:
        return f"reject:c{exc.criterion}"


class LevelGuard:
    """DomainGuard per NT level on the dims that actually vary within that level (NT=1 has one spacing)."""

    def __init__(self, rows: list[dict]):
        self.guards = {}
        for nt in sorted({r["nt"] for r in rows}):
            lv = [r for r in rows if r["nt"] == nt]
            dims = [d for d in DIMS if d != "turns" and len({r[COLS[d]] for r in lv}) > 1]
            self.guards[nt] = (dims, DomainGuard(np.array([[r[COLS[d]] for d in dims] for r in lv], dtype=float), dims,
                                                 {d: RANGES[d] for d in dims}, nt_dim=None))
        self.fixed = {nt: {d: next(iter({r[COLS[d]] for r in rows if r["nt"] == nt})) for d in DIMS
                           if d != "turns" and d not in self.guards[nt][0]} for nt in self.guards}

    def check(self, params: dict) -> None:
        nt = float(params["turns"])
        if abs(nt - round(nt)) > 1e-9 or round(nt) not in self.guards:
            raise OutOfDomainError(criterion=2, reason=f"turns {nt} is not a library level", nearest_samples=[], suggested_fill_points=[])
        dims, guard = self.guards[round(nt)]
        for d, v in self.fixed[round(nt)].items():
            if abs(float(params[d]) - v) > 1e-9:
                raise OutOfDomainError(criterion=1, reason=f"{d}={params[d]} but the NT={round(nt)} level only has {v}",
                                       nearest_samples=[], suggested_fill_points=[])
        guard.check({d: params[d] for d in dims})


def guard_tests(rows: list[dict]) -> dict:
    shipped = DomainGuard(matrix(rows), DIMS, RANGES, nt_dim="turns")
    fixed = LevelGuard(rows)
    ods = sorted({r["od"] for r in rows})
    ws = sorted({r["w"] for r in rows})
    have = {(r["od"], r["w"], r["s"], r["nt"]) for r in rows}
    cases = {"library points": [{"outer_diameter_um": r["od"], "width_um": r["w"], "spacing_um": r["s"], "turns": r["nt"]} for r in rows]}
    mids = []
    for (a, b) in itertools.pairwise(ods):
        for (c, e) in itertools.pairwise(ws):
            for s in (2.0, 3.0, 4.0):
                for nt in (1, 2, 3, 4, 5):
                    if nt == 1 and s != 2.0:
                        continue
                    corners = [(o, w, s, nt) for o in (a, b) for w in (c, e)]
                    if all(k in have for k in corners):
                        mids.append({"outer_diameter_um": (a + b) / 2, "width_um": (c + e) / 2, "spacing_um": s, "turns": nt})
    cases["cell midpoints (4 measured corners)"] = mids
    cases["outside the box"] = [{"outer_diameter_um": 50.0, "width_um": 6.0, "spacing_um": 2.0, "turns": 2},
                                {"outer_diameter_um": 250.0, "width_um": 6.0, "spacing_um": 2.0, "turns": 2},
                                {"outer_diameter_um": 150.0, "width_um": 3.5, "spacing_um": 2.0, "turns": 2},
                                {"outer_diameter_um": 150.0, "width_um": 10.5, "spacing_um": 2.0, "turns": 2},
                                {"outer_diameter_um": 150.0, "width_um": 6.0, "spacing_um": 4.5, "turns": 2}]
    unbuildable = []
    for nt in (2, 3, 4, 5):
        for od in (80.0, 100.0, 120.0, 150.0):
            for w in (6.0, 8.0, 10.0):
                inner = od - 2 * nt * w - 2 * (nt - 1) * 3.0
                if inner < 25:
                    unbuildable.append({"outer_diameter_um": od, "width_um": w, "spacing_um": 3.0, "turns": nt})
    cases["unbuildable (inner < 25 um)"] = unbuildable
    cases["non-integer turns"] = [{"outer_diameter_um": 150.0, "width_um": 6.0, "spacing_um": 2.0, "turns": 2.5}]
    out = {}
    for name, pts in cases.items():
        for label, guard in (("shipped", shipped), ("per-level dims", fixed)):
            tally = {}
            for p in pts:
                v = verdict(guard, p)
                tally[v] = tally.get(v, 0) + 1
            out[(name, label)] = {"n": len(pts), **tally}
    nt1 = [r for r in rows if r["nt"] == 1][:1]
    out["nt1_example_shipped"] = verdict(shipped, {"outer_diameter_um": nt1[0]["od"], "width_um": nt1[0]["w"], "spacing_um": 2.0, "turns": 1})
    return out


# --- 4 inverse query, offline ----------------------------------------------------------------------------------------

def inverse_offline(rows: list[dict]) -> dict:
    """Targets L_lf = L0 +-5 %, maximise Q_peak150; candidates = held-out rows (true values known)."""
    tol = 0.05
    results = {"conservative": [], "mean only": []}
    for seed in SEEDS:
        test, train = split(len(rows), seed)
        tr, te = [rows[i] for i in train], [rows[i] for i in test]
        gl, gq = fit(tr, "L_lf", "per_nt-matern52"), fit(tr, "Q_peak150", "per_nt-matern52")
        te = [r for r, a in zip(te, fitted_levels(gl, matrix(te)) & fitted_levels(gq, matrix(te))) if a]
        xt = matrix(te)
        mu_l, _ = gl.predict(xt)
        lo_l, hi_l = gl.predict_bounds(xt, K_SIGMA)
        mu_q, _ = gq.predict(xt)
        lo_q, _ = gq.predict_bounds(xt, K_SIGMA)
        true_l = np.array([r["L_lf"] for r in te])
        true_q = np.array([r["Q_peak150"] for r in te])
        for l0 in np.geomspace(0.15e-9, 8e-9, 25):
            truly = (true_l >= l0 * (1 - tol)) & (true_l <= l0 * (1 + tol))
            if not truly.any():
                continue
            oracle = float(true_q[truly].max())
            for mode, ok, score in (("conservative", (lo_l >= l0 * (1 - tol)) & (hi_l <= l0 * (1 + tol)), lo_q),
                                    ("mean only", (mu_l >= l0 * (1 - tol)) & (mu_l <= l0 * (1 + tol)), mu_q)):
                idx = np.nonzero(ok)[0]
                if len(idx) == 0:
                    results[mode].append({"L0": l0, "seed": seed, "found": 0})
                    continue
                top = idx[np.argsort(-score[idx])][:3]
                hit = truly[top]
                best = top[0]
                results[mode].append({"L0": l0, "seed": seed, "found": len(idx), "top3_precision": float(hit.mean()),
                                      "first_is_hit": bool(truly[best]),
                                      "regret": float((oracle - true_q[best]) / oracle) if truly[best] else None,
                                      "first_true_L_err": float(true_l[best] / l0 - 1)})
    summary = {}
    for mode, rs in results.items():
        answered = [r for r in rs if r["found"]]
        summary[mode] = {"queries": len(rs), "answered": len(answered),
                         "top3_precision": float(np.mean([r["top3_precision"] for r in answered])) if answered else None,
                         "first_hit_rate": float(np.mean([r["first_is_hit"] for r in answered])) if answered else None,
                         "median_regret": float(np.median([r["regret"] for r in answered if r["regret"] is not None])) if answered else None,
                         "max_first_L_err": float(max(abs(r["first_true_L_err"]) for r in answered)) if answered else None}
    return summary


# --- 5 example queries on the full library -----------------------------------------------------------------------------

def sobol_pool(n: int, seed: int = 0) -> list[dict]:
    from scipy.stats import qmc
    u = qmc.Sobol(d=4, scramble=True, seed=seed).random(n)
    pts = []
    for a, b, c, d in u:
        od = round(60 + 180 * a)
        w = round(4 + 6 * b, 1)
        nt = 1 + int(d * 5) if d < 1 else 5
        s = 2.0 if nt == 1 else round(2 + 2 * c, 1)
        if od - 2 * nt * w - 2 * (nt - 1) * s >= 30:
            pts.append({"outer_diameter_um": float(od), "width_um": w, "spacing_um": s, "turns": nt})
    return pts


def build_check(body: str, p: dict) -> dict:
    from ic_opt.em.pcell import get_generator
    from ic_opt.em.pcell.connectivity import nets
    from ic_opt.em.pcell.drc_audit import audit_gds
    gen = get_generator("clean_port_ind_sym", plugin_module="builtin:clean_port")
    cfg = {"process_profile": "n28_1p10m", "port_order": ["P1", "N1"], "outer_diameter_um": p["outer_diameter_um"],
           "width_um": p["width_um"], "spacing_um": p["spacing_um"], "turns": int(p["turns"]), "opening_um": 8.0,
           "lead_length_um": 20.0, "metal": "AP" if body == "AP" else "10",
           "ground_fixture": {"inner_margin_um": 15.0, "ring_width_um": 50.0, "stub_length_um": 2.0, "stub_chamfer_um": 0.0}}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            gen.generate(gen.config_model.model_validate(cfg), outdir=Path(tmp), gds_name="x.gds")
        except Exception as exc:  # noqa: BLE001 -- a refusal is the answer here
            return {"built": False, "why": f"{type(exc).__name__}: {str(exc).splitlines()[0]}"}
        gds = Path(tmp) / "x.gds"
        viol = [(v.kind, v.layer) for v in audit_gds(gds, "n28_1p10m").violations if v.kind != "max_width"]
        return {"built": True, "drc_violations": viol, "nets": nets(gds, "n28_1p10m")}


EXAMPLES = [
    {"body": "AP", "name": "L_lf 0.5 nH +-3%, SRF >= 60 GHz, max Q_peak150",
     "window": ("L_lf", 0.5e-9, 0.03), "srf_min_ghz": 60, "objective": "Q_peak150"},
    {"body": "AP", "name": "L@28 0.30 nH +-3%, max Q@28",
     "window": ("L@28", 0.30e-9, 0.03), "srf_min_ghz": None, "objective": "Q@28"},
    {"body": "M10", "name": "L_lf 2.0 nH +-3%, SRF >= 20 GHz, max Q_peak150",
     "window": ("L_lf", 2.0e-9, 0.03), "srf_min_ghz": 20, "objective": "Q_peak150"},
]


def example_queries(rows_by_body: dict[str, list[dict]]) -> list[dict]:
    out = []
    pool = sobol_pool(16384)
    for ex in EXAMPLES:
        rows = rows_by_body[ex["body"]]
        guard = LevelGuard(rows)
        target, value, tol = ex["window"]
        f0 = TARGETS[target]
        cands = []
        for p in pool:
            try:
                guard.check(p)
            except OutOfDomainError:
                continue
            cands.append(p)
        x = np.array([[p[d] for d in DIMS] for p in cands], dtype=float)
        gl = fit(usable(rows, target), target, "per_nt-matern52")
        go = fit(usable(rows, ex["objective"]), ex["objective"], "per_nt-matern52")
        answerable = fitted_levels(gl, x) & fitted_levels(go, x)
        cands = [p for p, a in zip(cands, answerable) if a]
        x = x[answerable]
        lo_l, hi_l = gl.predict_bounds(x, K_SIGMA)
        mu_l, _ = gl.predict(x)
        mu_o, _ = go.predict(x)
        lo_o, _ = go.predict_bounds(x, K_SIGMA)
        ok = (lo_l >= value * (1 - tol)) & (hi_l <= value * (1 + tol))
        xs = matrix(rows)
        if ex["srf_min_ghz"] or f0:
            need = max(ex["srf_min_ghz"] or 0, SRF_ANCHOR_MARGIN * (f0 or 0))
            for i in np.nonzero(ok)[0]:
                near = nearest_idx(xs, {COLS[d]: x[i][j] for j, d in enumerate(DIMS)})
                srf_near = [rows[j]["SRF"] for j in near]
                ok[i] = all(s is None or s / 1e9 >= need for s in srf_near)
        idx = np.nonzero(ok)[0]
        ranked = idx[np.argsort(-lo_o[idx])]
        chosen = []
        for i in ranked:
            if all(np.linalg.norm(scaled(x[i][None, :])[0] - scaled(x[j][None, :])[0]) >= 0.05 for j in chosen):
                chosen.append(i)
            if len(chosen) == 3:
                break
        picks = []
        for i in chosen:
            p = cands[i]
            near = nearest_idx(xs, {COLS[d]: p[d] for d in DIMS}, k=3)
            picks.append({"params": p, "pred": {target: [float(mu_l[i]), float(lo_l[i]), float(hi_l[i])],
                                                 ex["objective"]: [float(mu_o[i]), float(lo_o[i])]},
                          "build": build_check(ex["body"], p),
                          "nearest_measured": [{"od": rows[j]["od"], "w": rows[j]["w"], "s": rows[j]["s"], "nt": rows[j]["nt"],
                                                target: rows[j].get(target), ex["objective"]: rows[j].get(ex["objective"]),
                                                "SRF_GHz": rows[j]["SRF"] / 1e9 if rows[j]["SRF"] else None} for j in near]})
        out.append({**ex, "pool": len(pool), "in_domain": len(cands), "satisfy": int(ok.sum()), "picks": picks})
    return out


def main() -> None:
    dataset, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = json.loads(dataset.read_text())["rows"]
    by_body = {b: [r for r in rows if r["body"] == b] for b in ("AP", "M10")}
    report, t0 = {"dataset": str(dataset)}, time.time()
    for body, rs in by_body.items():
        cv = forward_cv(rs)
        report[f"forward_{body}"] = {f"{t}|{m}": v for (t, m), v in cv.items()}
        report[f"srf_{body}"] = srf_cv(rs)
        report[f"guard_{body}"] = {("|".join(k) if isinstance(k, tuple) else k): v for k, v in guard_tests(rs).items()}
        report[f"inverse_{body}"] = inverse_offline(rs)
        print(f"{body}: forward/SRF/guard/inverse done at {time.time() - t0:.0f} s", flush=True)
    report["examples"] = example_queries(by_body)
    report["seconds"] = round(time.time() - t0, 1)
    (out_dir / "ind_query_verify.json").write_text(json.dumps(report, indent=1, default=float))
    print(json.dumps({k: v for k, v in report.items() if not k.startswith("examples")}, indent=1, default=float)[:6000])


if __name__ == "__main__":
    main()
