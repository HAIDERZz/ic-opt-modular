"""N-116: the interval widening of extrapolated answers (``query.WIDENING``), from N-115's edge hold-outs along the outer
diameters.

For every N-115 edge hold-out of an outer-diameter dim (``extrapolation_study.py``: the lowest / highest one or two levels of
outer_diameter_um, primary_outer_diameter_um or secondary_outer_diameter_um held out, the rest fitted and calibrated with
the library's own code), each held-out row's prediction is read from the study's job file (mu, sigma, the training box),
its coordinates from the study's dataset copy, and the product's guard (``ic_opt.library.domain``) is rebuilt on the
training rows -- the column's usable rows less the held ones -- to say what it does there with extrapolate=0.15: refuse
the row, or answer it as extrapolated and how far (``depth``: the distance beyond the measured range in allowances at
0.15, 1 at the allowance's end). For a row it answers, the factor it needs so that its calibrated 2-sigma interval (sigma
floored at the edge's own held-out median error, widened by the edge's own k_scale: the interval the query gives) holds
the measurement: |log(y / mu)| / (2 k_scale sigma_floored / mu) for a log-target model (|y - mu| / (2 k_scale sigma) else).

Per column class (N-115's: L_lf, k_lf, SRF, Q_peak, L_curve, Q_curve, k_curve): the coverage of today's interval on those
rows and the smallest ``a`` (a grid of 0.01) whose interval widened by 1 + a x depth covers 0.95 of them -- 0 where today's
already does. Cross-checks: the job's training box equals the rebuilt rows' (every edge), and "need <= 1" equals N-115's
own ``inside`` flag for every row (``edge_rows.csv.gz``).

Usage: python extrapolation_widening.py N115_EVIDENCE OUT_DIR
  N115_EVIDENCE: ic-opt-accept/n115_extrapolation_study (jobs/, datasets/, edge_rows.csv.gz); writes
  extrapolation_widening.json (the summary) next to this script and widening_rows.json (every row) into OUT_DIR.
"""
from __future__ import annotations

import collections
import csv
import gzip
import hashlib
import itertools
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

from ic_opt.library import domain

HERE = Path(__file__).resolve().parent
STUDY = HERE / "extrapolation_study.json"
OD = domain.EXTRAPOLATE_DIMS
F = domain.EXTRAPOLATE_FRACTION
CLASS = {"Lp_lf": "L_lf", "Ls_lf": "L_lf", "k_lf": "k_lf", "SRF": "SRF", "SRF_p": "SRF", "SRF_s": "SRF",
         "Qp_peak": "Q_peak", "Qs_peak": "Q_peak", "Lp": "L_curve", "Ls": "L_curve", "Qp": "Q_curve", "Qs": "Q_curve",
         "k": "k_curve"}
CLASSES = ("L_lf", "k_lf", "SRF", "Q_peak", "L_curve", "Q_curve", "k_curve")
TARGET = 0.95
A_GRID = np.round(np.arange(0.0, 20.0001, 0.01), 2)
BINS = (0.0, 0.25, 0.5, 0.75, 1.0 + 1e-9)


def needed(y: float, mu: float, s_fl: float, k: float, log_target: bool) -> float:
    """The widening a row needs: its error over the half-width of its calibrated interval (log space for a log target)."""
    if log_target:
        return abs(math.log(y / mu)) / (2.0 * k * s_fl / abs(mu))
    return abs(y - mu) / (2.0 * k * s_fl)


def fit_a(need: np.ndarray, depth: np.ndarray) -> float | None:
    """The smallest a on A_GRID with mean(need <= 1 + a depth) >= TARGET; None when none on the grid."""
    for a in A_GRID:
        if len(need) and np.mean(need <= 1.0 + a * depth + 1e-12) >= TARGET:
            return float(a)
    return None


def rows_of(evidence: Path) -> tuple[list[dict], collections.Counter]:
    study = json.loads(STUDY.read_text(encoding="utf-8"))
    out, checks = [], collections.Counter()
    guards: dict[str, domain.DomainGuard] = {}
    for t, tab in study["tables"].items():
        data = json.loads((evidence / "datasets" / f"dataset-{t}-{tab['dataset_key']}.json").read_text(encoding="utf-8"))["rows"]
        dims, nt_dim = tab["dims"], tab["nt_dim"]
        x = np.array([[r["coords"][d] for d in dims] for r in data], dtype=float)
        for c, col in tab["columns"].items():
            y_all = np.array([np.nan if r["values"].get(c) is None else r["values"][c] for r in data], dtype=float)
            if c.startswith("SRF"):
                y_all = y_all / 1e9                                         # the study scores SRF in its model's GHz
            usable = np.isfinite(y_all)
            group = "curves" if "@" in c else "scalars"
            for name, edge in col.get("edges", {}).items():
                dim, e = name.split("/")
                if dim not in OD or not edge.get("complete"):
                    continue
                job = json.loads((evidence / "jobs" / f"{t}__fit__{group}__{edge['subset']}.json").read_text(encoding="utf-8"))
                jc = job["columns"][c]
                held = np.array(job["held"], dtype=int)
                keep = np.ones(len(x), dtype=bool)
                keep[held] = False
                train = np.flatnonzero(usable & keep)
                if not (np.allclose(x[train].min(axis=0), jc["box_min"]) and np.allclose(x[train].max(axis=0), jc["box_max"])):
                    checks["box_mismatch"] += 1
                    continue
                checks["box_match"] += 1
                rest = x[keep]
                ranges = {d: (float(rest[:, i].min()), float(rest[:, i].max())) if np.ptp(rest[:, i]) > 0 else
                          (float(rest[0, i]) - 0.5, float(rest[0, i]) + 0.5) for i, d in enumerate(dims)}
                gkey = t + hashlib.sha256(train.tobytes()).hexdigest()
                if gkey not in guards:
                    guards[gkey] = domain.DomainGuard(x[train], dims, ranges, nt_dim=nt_dim)
                guard = guards[gkey]
                mu, sigma = np.array(jc["mu"], dtype=float), np.array(jc["sigma"], dtype=float)
                available, log_target = np.array(jc["available"], dtype=bool), bool(jc["log_target"])
                xh = x[held]
                answered, depth = guard.inside(xh, extrapolate=F), guard.depth(xh)
                for a, h in enumerate(held):
                    y = y_all[h]
                    if not (available[a] and np.isfinite(mu[a]) and np.isfinite(sigma[a]) and np.isfinite(y)) or (log_target and y <= 0):
                        continue
                    s_fl = max(sigma[a], edge["floor"] * abs(mu[a]))
                    out.append({"table": t, "column": c, "class": CLASS[c.split("@")[0]], "part": data[h]["part"],
                                "obs_id": data[h]["obs_id"], "dim": dim, "edge": e, "side": "below" if e in ("a", "c") else "above",
                                "answered": bool(answered[a]), "depth": float(depth[a]),
                                "need": float(needed(y, mu[a], s_fl, edge["k_scale"], log_target)),
                                "rel": float(abs(mu[a] - y) / abs(y)), "rel_sigma": float(s_fl / abs(mu[a]))})
    mine = {(r["table"], r["column"], r["dim"], r["edge"], r["part"], r["obs_id"]): r for r in out}
    with gzip.open(evidence / "edge_rows.csv.gz", "rt", encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            r = mine.get((rec["table"], rec["column"], rec["dim"], rec["edge"], rec["part"], rec["obs_id"]))
            if r is not None:
                checks["csv_inside_agree" if (r["need"] <= 1 + 1e-9) == (rec["inside"] == "True") else "csv_inside_disagree"] += 1
    return out, checks


def summary(rows: list[dict]) -> dict:
    out = {}
    for cls in CLASSES:
        sel = [r for r in rows if r["class"] == cls]
        ans = [r for r in sel if r["answered"] and r["depth"] > 0]
        need, depth = np.array([r["need"] for r in ans]), np.array([r["depth"] for r in ans])
        a = fit_a(need, depth)
        wide = 1 + (a or 0.0) * depth
        bins = {}
        for lo, hi in itertools.pairwise(BINS):
            m = (depth > lo) & (depth <= hi)
            bins[f"{lo:g}-{min(hi, 1):g}"] = {"rows": int(m.sum()), "today": float(np.mean(need[m] <= 1 + 1e-12)) if m.any() else None,
                                              "widened": float(np.mean(need[m] <= wide[m] + 1e-12)) if m.any() else None}
        tables = {}
        for t in sorted({r["table"] for r in ans}):
            m = np.array([r["table"] == t for r in ans])
            tables[t] = {"rows": int(m.sum()), "today": float(np.mean(need[m] <= 1 + 1e-12)),
                         "widened": float(np.mean(need[m] <= wide[m] + 1e-12))}
        out[cls] = {"edge_rows": len(sel), "answered": len(ans), "beyond_allowance": sum(r["depth"] > 1 + 1e-9 for r in sel),
                    "refused_within_allowance": sum((not r["answered"]) and 0 < r["depth"] <= 1 + 1e-9 for r in sel),
                    "coverage_today": float(np.mean(need <= 1 + 1e-12)) if len(need) else None, "a": a,
                    "coverage_widened": float(np.mean(need <= wide + 1e-12)) if len(need) else None,
                    "p90_rel": float(np.quantile([r["rel"] for r in ans], 0.9)) if ans else None,
                    "median_rel": float(np.median([r["rel"] for r in ans])) if ans else None,
                    "depth_median": float(np.median(depth)) if len(depth) else None,
                    "sides": {s: {"rows": sum(r["side"] == s for r in ans),
                                  "today": float(np.mean([r["need"] <= 1 + 1e-12 for r in ans if r["side"] == s])) if any(r["side"] == s for r in ans) else None}
                              for s in ("below", "above")},
                    "dims": {d: sum(r["dim"] == d for r in ans) for d in OD}, "by_depth": bins, "by_table": tables}
    return out


def main(evidence: Path, out_dir: Path) -> None:
    t0 = time.time()
    rows, checks = rows_of(evidence)
    result = {"specification": "docs/refactor/N116_EXTRAPOLATION_SPEC.md section 1 (Intervals)", "fraction": F, "target": TARGET,
              "form": "1 + a x depth, depth = distance beyond the measured range / the allowance at 0.15",
              "rows": len(rows), "answered": sum(r["answered"] and r["depth"] > 0 for r in rows), "checks": dict(checks),
              "classes": summary(rows), "seconds": round(time.time() - t0, 1)}
    (HERE / "extrapolation_widening.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "widening_rows.json").write_text(json.dumps(rows), encoding="utf-8")
    print(json.dumps({c: {k: v[k] for k in ("answered", "coverage_today", "a", "coverage_widened")} for c, v in result["classes"].items()},
                     indent=1))
    print(json.dumps(result["checks"]))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
