"""T16.2a: how should the library model the transformer result columns taken at a fixed frequency (Lp@40, Ls@40, k@40)
and the system SRF, so that designs between the sampled outer-diameter levels are predicted as well as the levels?
A comparison study -- no product code changes.

Background (docs/refactor/T16_PLAN_CN.md sections 2 and 3.2): the single-turn transformer tables xfm_bs_ap / xfm_bs_m10
are accurate on their sampled levels (held-out median error well under 1 %) but not between them: ten real designs
between the levels (EMX 2026-09-24, since added to the tables as obs_1577..obs_1581 of each part) missed Lp@40 by up to
+22 %, k@40 by up to +9 % and SRF by down to -18 %, while Lp_lf stayed within 2 %. The plan's hypothesis:
L@f0 = L_lf x a resonance factor, where L_lf is smooth in the geometry and the factor is steep near the resonance.

Data: every table's dataset through ic_opt.library (cached). The rows each part lists in .icopt/adopted.yaml (the ten
real designs between the levels; xfm_ms has none) are left out of every training set and form test set (iii).

Protocols, for every table x result column x method (identical rows for every method of a column):
  (i)   5 x 20 % seeded hold-out of the column's usable rows (gp.split, seeds 0-4: the library's calibration protocol);
  (ii)  leave one level out: every row of one secondary_outer_diameter_um value (resp. primary_outer_diameter_um) is held
        out and predicted by models fitted on the others, for every value with at least 25 usable rows that lies strictly
        inside the column's range (holding out the smallest or the largest value is extrapolation, not the question);
        pooled over the values, again over the rows with 1/1.2 <= OD_S/OD_P <= 1.2 only (the strongly coupled band the
        designs use), and kept per value in the JSON;
  (iii) the adopted designs between the levels (xfm_bs only).
Metrics: median / p90 / max relative error |pred - meas| / meas; 2-sigma coverage of the calibrated interval; median
|z|. Each method is calibrated the way the library calibrates a model: k_scale = gp.calibration_scale over the method's
own protocol-(i) z, sigma floored at its protocol-(i) median relative error, interval 2 k_scale sigma (log space); z is
taken against the floored sigma, as lib_signoff reports it.

Methods (every target is fitted in log space; the relative sigmas of independent parts add in quadrature):
  A   direct            StratumGP on log L@f0 with the settings Library._fit_inputs builds (the status quo)
  F   direct, map       A with the table's dimensionless feature map (the one k uses) -- added to the plan's list
  B   ratio             log L_lf and log(L@f0 / L_lf), one GP each, multiplied
  BF  ratio, map        B with that map on the ratio's GP (L_lf keeps the library's inputs) -- added
  C   resonance         r = L@f0 / L_lf; rows with r > 1 give f_r = f0 / sqrt(1 - 1/r); GP on log f_r;
                        r = 1 / (1 - (f0/f_r)^2); where B's ratio predicts r <= 1, C answers with B
  D   SRF prior         r_phys = 1 / (1 - (f0/SRF_pred)^2) with SRF_pred from the SRF model (A settings) fitted on the
                        same training rows; GP on log(r / r_phys); sigma of SRF_pred carried by the delta method
  DF  SRF prior, map    D with SRF_pred from the SRF model with the map, and the map on the residual's GP (xfm_bs) -- added
  D*  measured SRF      D with the measured SRF instead of SRF_pred (training and test rows): not usable for a query,
                        it shows how much of D's error is the SRF model's
  E   stacked           A with log SRF_pred as one more input: only with --with-e (tried in the pilot, not in the study)
  k@f0: A (the manifest's map) and, on xfm_bs, B (ratio to k_lf, both with the map). On xfm_ms B is left out: its k_lf
  part is a joint fit over all 2440 rows (7 min each on the reference host) and B was already worse than A on xfm_bs.
  SRF: A and, on xfm_bs, F and MIN = the smaller of two GPs on log SRF_p and log SRF_s (on xfm_ms the system SRF equals
  SRF_s on every row, so MIN would repeat A; F would be a joint fit over all turns and all 2436 rows).
  Reference rows: Lp_lf, Ls_lf (and k_lf on xfm_bs) evaluated on the same rows as the anchored columns.

Usage:
  xfm_anchor_model_study.py run LIBRARY_ROOT OUT_JSON [--tables a,b] [--workers 48] [--cache DIR] [--pilot] [--with-e] [--dry-run]
  xfm_anchor_model_study.py page OUT_JSON OUT_HTML
The fits run in spawned worker processes with 2 BLAS threads each; run under nice with OMP_NUM_THREADS=2. --cache keeps
every fit's result (keyed by its data, settings and the gp code), so a later run that only changes the evaluation refits
nothing; its runs.jsonl records each run's fits and wall time, which the JSON and the page report. --pilot takes one fold
per protocol; --dry-run prints the folds and the fits they need.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import inspect
import json
import multiprocessing
import pickle
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

import numpy as np
import sklearn
import yaml
from threadpoolctl import threadpool_limits

from ic_opt.library import gp, query

ANCHOR_GHZ = {"xfm_bs_ap": 40.0, "xfm_bs_m10": 40.0, "xfm_ms_ap": 60.0, "xfm_ms_m10": 60.0}
FEATURE_MAP = {"xfm_bs": "xfm_bs_dimensionless", "xfm_ms": "xfm_ms_dimensionless"}
LEVEL_DIMS = ("secondary_outer_diameter_um", "primary_outer_diameter_um")
MIN_LEVEL_ROWS = 25                                   # the brief: every level with at least 25 rows
U_MAX = 0.8                                           # (f0 / f_res)^2 cap: a resonance predicted below 1.118 f0 is clamped there
NEAR_RATIO = 1.2                                      # (ii) is also pooled over the rows with 1/1.2 <= OD_S/OD_P <= 1.2 (strong coupling)
THREADS = 2
SEEDS = gp.SEEDS
SHORT = {"primary_outer_diameter_um": "OD_P", "secondary_outer_diameter_um": "OD_S", "primary_width_um": "W_P",
         "secondary_width_um": "W_S", "center_spacing_um": "CS", "secondary_spacing_um": "S_S", "secondary_turns": "N_S",
         "log_srf_pred": "log SRF_pred"}
MAP_FEATURES = {"xfm_bs_dimensionless": ["log mean OD", "log OD_P/OD_S", "W_P/OD_P", "W_S/OD_S", "4CS/(OD_P+OD_S)"],
                "xfm_ms_dimensionless": ["log mean OD", "log OD_P/OD_S", "W_P/OD_P", "W_S/OD_S", "4CS/(OD_P+OD_S)", "S_S/OD_S", "N_S"]}


# -- worker ----------------------------------------------------------------------------------------------------------------

def _kernel_one(model: gp.StratumGP, inputs: list[str]) -> dict:
    k = model._gp.kernel_                                        # Constant * Matern + White (gp._kernel)
    return {"inputs": inputs, "length_scale": [float(v) for v in np.atleast_1d(k.k1.k2.length_scale)],
            "amplitude": float(np.sqrt(k.k1.k1.constant_value)), "noise": float(k.k2.noise_level)}


def kernel_summary(model: gp.StratumGP) -> dict:
    if model.nt_mode == "per_nt":
        return {"per_nt": {str(level): _kernel_one(sub, [SHORT.get(d, d) for d in sub.dims]) for level, sub in model._sub.items()},
                "unavailable_nt": {str(k): v for k, v in model.unavailable_nt.items()}}
    inputs = MAP_FEATURES[model.feature_map] if model.feature_map else [SHORT.get(d, d) for d in model.dims]
    return _kernel_one(model, inputs)


def fit_one(job: dict) -> dict:
    """One fit in a spawned worker: StratumGP on the training rows, predicted at every row of the table (log mean, log sigma)."""
    t0 = time.time()
    with threadpool_limits(limits=job["threads"]):
        x, y, train = job["x"], job["y"], job["train"]
        model = gp.StratumGP(**job["settings"]).fit(x[train], y[train])
        seconds = time.time() - t0
        mu, sigma = model.predict(x)
    with np.errstate(invalid="ignore", divide="ignore"):
        m, s = np.log(mu), sigma / mu
    return {"id": job["id"], "m": m, "s": s, "kernel": kernel_summary(model), "seconds": round(seconds, 2), "n_train": len(train)}


# -- data ------------------------------------------------------------------------------------------------------------------

def adopted_rows(root: Path, lib: query.Library, stratum: str) -> set[tuple[str, str]]:
    """(part, obs id) of every row a part took in from a real-EMX check of the library (its .icopt/adopted.yaml)."""
    out = set()
    for part in lib.manifest.strata[stratum].parts:
        path = root / part.store / ".icopt" / "adopted.yaml"
        if path.is_file():
            for entry in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
                out.update((part.store, str(i)) for i in entry["ids"])
    return out


class Table:
    """One table's rows, columns and model settings; the components (GP targets) the methods are built from."""

    def __init__(self, lib: query.Library, stratum: str, *, with_e: bool = False):
        self.lib, self.name, self.f0 = lib, stratum, ANCHOR_GHZ[stratum]
        self.ds = lib.dataset(stratum)
        self.family = stratum.rsplit("_", 1)[0]                     # xfm_bs | xfm_ms
        self.map = FEATURE_MAP[self.family]
        self.bs = self.family == "xfm_bs"
        self.with_e = with_e
        adopted = adopted_rows(lib.root, lib, stratum)
        self.rows = self.ds.rows
        self.n = len(self.rows)
        self.test = np.array([(r.part, r.obs_id) in adopted for r in self.rows])   # set (iii); never trained on
        self.base = ~self.test
        self.x = self.ds.matrix()
        self.ranges = lib.ranges(stratum)
        self.cols = {c: self.ds.values(c) for c in self.ds.columns}
        self.cols["SRF_GHz"] = self.cols["SRF"] / 1e9
        self.stop_ghz = np.array([r.stop_hz for r in self.rows]) / 1e9
        f = f"{self.f0:g}"
        self.anch = [f"Lp@{f}", f"Ls@{f}", f"k@{f}"]
        usable = {q: np.isfinite(self.cols[q]) for q in self.anch}
        if not (usable[self.anch[0]] == usable[self.anch[1]]).all() or not (usable[self.anch[0]] == usable[self.anch[2]]).all():
            raise SystemExit(f"{stratum}: Lp/Ls/k at {f} GHz have different usable rows; the study assumes one row set")
        self.anch_rows = usable[self.anch[0]]
        self.srf_rows = np.isfinite(self.cols["SRF"])
        self.targets = self._targets()
        wanted = {c for t in self.targets.values() for cs in t["needs"].values() for c in cs}
        self.comp = {name: comp for name, comp in self._components().items() if name in wanted}

    def settings(self, quantity: str, feature_map: str | None | bool = True) -> dict:
        """Library._fit_inputs' settings for ``quantity``; feature_map True keeps the manifest's, else the one given."""
        fm = self.lib.manifest.strata[self.name].quantities[quantity.split("@")[0]].feature_map if feature_map is True else feature_map
        return {"dims": list(self.ds.dims), "ranges": dict(self.ranges), "log_target": True,
                "nt_mode": "per_nt" if self.ds.nt_dim and not fm else "joint", "kernel": "matern52", "nt_dim": self.ds.nt_dim,
                "feature_map": fm}

    def _components(self) -> dict[str, dict]:
        """name -> {y (every row, NaN where not usable), settings, depends (a component whose fold predictions it needs), kind}."""
        f0, f, c = self.f0, f"{self.f0:g}", self.cols
        out: dict[str, dict] = {}
        with np.errstate(invalid="ignore", divide="ignore"):
            srf_meas = np.where(np.isfinite(c["SRF_GHz"]), c["SRF_GHz"], self.stop_ghz)      # no resonance in the sweep: its stop
            u_meas = np.minimum((f0 / srf_meas) ** 2, U_MAX)
            for L in ("Lp", "Ls"):
                q, lf = f"{L}@{f}", f"{L}_lf"
                r = c[q] / c[lf]
                st, fm = self.settings(q), self.settings(q, self.map)
                out[f"{q}:A"] = {"y": c[q], "settings": st}
                out[f"{q}:Afm"] = {"y": c[q], "settings": fm}
                out[lf] = {"y": c[lf], "settings": self.settings(lf)}
                out[f"{q}:ratio"] = {"y": r, "settings": st}
                out[f"{q}:ratiofm"] = {"y": r, "settings": fm}
                out[f"{q}:fr"] = {"y": np.where(r > 1, f0 / np.sqrt(1 - 1 / r), np.nan), "settings": st}
                out[f"{q}:resid"] = {"y": r, "settings": st, "depends": "SRF:A", "kind": "resid"}          # r / r_phys(SRF_pred)
                out[f"{q}:resid_fm"] = {"y": r, "settings": fm, "depends": "SRF:Afm", "kind": "resid"}
                out[f"{q}:resid_meas"] = {"y": r * (1 - u_meas), "settings": st}                           # r / r_phys(SRF measured)
                out[f"{q}:stack"] = {"y": c[q], "settings": st, "depends": "SRF:A", "kind": "stack"}       # + log SRF_pred input
            kq = f"k@{f}"
            out[f"{kq}:A"] = {"y": c[kq], "settings": self.settings(kq)}
            out["k_lf"] = {"y": c["k_lf"], "settings": self.settings("k_lf")}
            out[f"{kq}:ratio"] = {"y": c[kq] / c["k_lf"], "settings": self.settings(kq)}
            out["SRF:A"] = {"y": c["SRF_GHz"], "settings": self.settings("SRF")}
            out["SRF:Afm"] = {"y": c["SRF_GHz"], "settings": self.settings("SRF", self.map)}
            out["SRF_p:A"] = {"y": c["SRF_p"] / 1e9, "settings": self.settings("SRF_p")}
            out["SRF_s:A"] = {"y": c["SRF_s"] / 1e9, "settings": self.settings("SRF_s")}
        for name, comp in out.items():
            y = comp["y"]
            comp["usable"] = np.isfinite(y) & self.base
            if (y[comp["usable"]] <= 0).any():
                raise SystemExit(f"{self.name}: component {name} has non-positive targets")
        return out

    def _targets(self) -> dict[str, dict]:
        """target -> {group (whose hold-out split it uses), methods, rows (usable), needs per method}."""
        f = f"{self.f0:g}"
        out = {}
        for L in ("Lp", "Ls"):
            q, lf = f"{L}@{f}", f"{L}_lf"
            needs = {"A": [f"{q}:A"], "F": [f"{q}:Afm"], "B": [lf, f"{q}:ratio"], "BF": [lf, f"{q}:ratiofm"],
                     "C": [lf, f"{q}:ratio", f"{q}:fr"], "D": [lf, f"{q}:resid", "SRF:A"]}
            if self.bs:
                needs["DF"] = [lf, f"{q}:resid_fm", "SRF:Afm"]
            if self.with_e:
                needs["E"] = [f"{q}:stack"]
            needs["D*"] = [lf, f"{q}:resid_meas"]
            out[q] = {"kind": "L", "group": "anch", "rows": self.anch_rows & self.base, "needs": needs}
        kq = f"k@{f}"
        out[kq] = {"kind": "k", "group": "anch", "rows": self.anch_rows & self.base,
                   "needs": {"A": [f"{kq}:A"], "B": ["k_lf", f"{kq}:ratio"]} if self.bs else {"A": [f"{kq}:A"]}}
        out["SRF"] = {"kind": "SRF", "group": "srf", "rows": self.srf_rows & self.base,
                      "needs": {"A": ["SRF:A"], "F": ["SRF:Afm"], "MIN": ["SRF_p:A", "SRF_s:A"]} if self.bs else {"A": ["SRF:A"]}}
        for lf in ("Lp_lf", "Ls_lf", "k_lf") if self.bs else ("Lp_lf", "Ls_lf"):      # reference: the low-frequency parts, same rows
            out[lf] = {"kind": "ref", "group": "anch", "rows": self.anch_rows & self.base, "needs": {"A": [lf]}}
        return out

    def folds(self, pilot: bool = False) -> list[dict]:
        """Every fold: key, protocol, the rows it holds out, the targets evaluated in it (each on its own usable rows there)."""
        out = []
        groups = {"anch": self.anch_rows & self.base, "srf": self.srf_rows & self.base}
        for g, rows in groups.items():
            idx = np.flatnonzero(rows)
            for seed in SEEDS[:1] if pilot else SEEDS:
                test_i, _ = gp.split(len(idx), seed)
                held = np.zeros(self.n, dtype=bool)
                held[idx[test_i]] = True
                out.append({"key": f"hold:{g}:{seed}", "protocol": "i", "held": held,
                            "targets": [t for t, d in self.targets.items() if d["group"] == g]})
        for dim in LEVEL_DIMS:
            j = self.ds.dims.index(dim)
            per_target = {}
            for t, d in self.targets.items():                   # a column's levels: >= 25 usable rows, strictly inside its range
                vals = self.x[d["rows"], j]
                levels, counts = np.unique(vals, return_counts=True)
                per_target[t] = {float(v) for v, n in zip(levels, counts) if n >= MIN_LEVEL_ROWS and vals.min() < v < vals.max()}
            values = sorted(set().union(*per_target.values()))
            if pilot:
                values = values[len(values) // 3:len(values) // 3 + 1]
            for v in values:
                held = self.base & (self.x[:, j] == v)
                out.append({"key": f"lvl:{SHORT[dim]}:{v:g}", "protocol": f"ii-{SHORT[dim]}", "held": held, "level": v,
                            "targets": [t for t, levels in per_target.items() if v in levels]})
        out.append({"key": "full", "protocol": "iii", "held": self.test.copy(), "targets": list(self.targets)})
        return out

    def needed(self, fold: dict) -> list[str]:
        """Components a fold must fit: those of every method of every target evaluated there."""
        return sorted({c for t in fold["targets"] for cs in self.targets[t]["needs"].values() for c in cs})


# -- jobs ------------------------------------------------------------------------------------------------------------------

def make_job(table: Table, fold: dict, name: str, srf: dict | None) -> dict:
    comp = table.comp[name]
    y, x, st = comp["y"].copy(), table.x, comp["settings"]
    kind = comp.get("kind")
    if kind == "resid":                                        # r / r_phys with the fold's predicted SRF, on every row
        u = np.minimum((table.f0 / np.exp(srf["m"])) ** 2, U_MAX)
        y = y * (1 - u)
    elif kind == "stack":
        extra = srf["m"]
        x = np.column_stack([table.x, extra])
        finite = extra[np.isfinite(extra)]
        st = {**st, "dims": st["dims"] + ["log_srf_pred"], "ranges": {**st["ranges"], "log_srf_pred": (float(finite.min()), float(finite.max()))}}
        y = np.where(np.isfinite(extra), y, np.nan)
    train = np.flatnonzero(comp["usable"] & ~fold["held"] & np.isfinite(y))
    return {"id": (table.name, fold["key"], name), "x": x, "y": y, "train": train, "settings": st, "threads": THREADS}


def job_key(table: Table, job: dict) -> str:
    h = hashlib.sha256()
    h.update(repr(job["id"]).encode())
    h.update(table.ds.key.encode())
    h.update(json.dumps(job["settings"], sort_keys=True, default=str).encode())
    h.update(job["train"].tobytes())
    h.update(np.ascontiguousarray(job["y"][job["train"]]).tobytes())
    h.update(np.ascontiguousarray(job["x"]).tobytes())
    h.update(hashlib.sha256(inspect.getsource(gp).encode()).digest())
    h.update(sklearn.__version__.encode())
    return h.hexdigest()[:24]


def cost(job: dict) -> float:
    """Rough fit cost for longest-first ordering: rows^3 (per turns level for per_nt models)."""
    x, train, st = job["x"], job["train"], job["settings"]
    if st["nt_mode"] == "per_nt":
        _, counts = np.unique(np.round(x[train, st["dims"].index(st["nt_dim"])]), return_counts=True)
        return float((counts.astype(float) ** 3).sum())
    return float(len(train)) ** 3


def run_fits(tables: list[Table], folds: dict[str, list[dict]], workers: int, cache: Path | None) -> tuple[dict, dict]:
    """Fit every (table, fold, component) in a spawn pool; the components that need the fold's SRF prediction are submitted
    when that fit returns. Returns results by id and bookkeeping."""
    by_name = {t.name: t for t in tables}
    fold_by = {(t.name, f["key"]): f for t in tables for f in folds[t.name]}
    independent, dependent = [], {}
    for t in tables:
        for f in folds[t.name]:
            for name in t.needed(f):
                dep = t.comp[name].get("depends")
                if dep:
                    dependent.setdefault((t.name, f["key"], dep), []).append(name)
                else:
                    independent.append(make_job(t, f, name, None))
    results: dict[tuple, dict] = {}
    stats = {"fits": 0, "cached": 0, "fit_seconds": 0.0}
    pending = {}
    t0 = time.time()

    def load(job):
        if cache is None:
            return None
        path = cache / f"{job_key(by_name[job['id'][0]], job)}.pkl"
        if path.is_file():
            with path.open("rb") as fh:
                return pickle.load(fh)
        return None

    def save(job, res):
        if cache is not None:
            cache.mkdir(parents=True, exist_ok=True)
            path = cache / f"{job_key(by_name[job['id'][0]], job)}.pkl"
            tmp = path.with_suffix(".tmp")
            with tmp.open("wb") as fh:
                pickle.dump(res, fh, protocol=pickle.HIGHEST_PROTOCOL)
            tmp.replace(path)

    def done(job, res, fitted: bool):
        results[res["id"]] = res
        if fitted:
            stats["fits"] += 1
            stats["fit_seconds"] += res["seconds"]
        else:
            stats["cached"] += 1
        for name in dependent.get(res["id"], []):
            t = by_name[res["id"][0]]
            submit(make_job(t, fold_by[(t.name, res["id"][1])], name, res))

    def submit(job):
        hit = load(job)
        if hit is not None:
            done(job, hit, False)
            return
        pending[pool.submit(fit_one, job)] = job

    first = sorted(independent, key=lambda j: (j["id"][2] not in ("SRF:A",), -cost(j)))   # SRF first: others wait on it
    total = len(independent) + sum(len(v) for v in dependent.values())
    print(f"{total} fits planned ({len(independent)} independent, {total - len(independent)} after their SRF fit), {workers} workers", flush=True)
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        for job in first:
            submit(job)
        last = time.time()
        while pending:
            finished, _ = wait(list(pending), return_when=FIRST_COMPLETED)
            for fut in finished:
                job = pending.pop(fut)
                res = fut.result()
                save(job, res)
                done(job, res, True)
            if time.time() - last > 60:
                last = time.time()
                print(f"  {stats['fits'] + stats['cached']}/{total} done ({stats['cached']} from cache), {len(pending)} queued or running, "
                      f"{(time.time() - t0) / 60:.1f} min", flush=True)
    stats["wall_seconds"] = round(time.time() - t0, 1)
    stats["fit_seconds"] = round(stats["fit_seconds"], 1)
    stats["planned"] = total
    return results, stats


# -- combining components into methods --------------------------------------------------------------------------------------

def predict(table: Table, target: str, method: str, R: dict, idx: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
    """(log mean, log sigma, notes) of ``method`` for ``target`` at rows ``idx`` from the fold's component predictions R."""
    f0 = table.f0
    get = (lambda name: (R[name]["m"][idx], R[name]["s"][idx]))
    kind = table.targets[target]["kind"]
    notes: dict = {}
    if kind in ("ref",) or method == "A":
        return (*get(table.targets[target]["needs"]["A"][0]), notes)
    if kind == "SRF":
        if method == "F":
            return (*get("SRF:Afm"), notes)
        (mp, sp), (ms, ss) = get("SRF_p:A"), get("SRF_s:A")
        p_lower = mp <= ms
        notes["primary_lower"] = p_lower
        return np.where(p_lower, mp, ms), np.where(p_lower, sp, ss), notes
    if kind == "k":
        (ml, sl), (mr, sr) = get("k_lf"), get(f"{target}:ratio")
        return ml + mr, np.hypot(sl, sr), notes
    lf = target.split("@")[0] + "_lf"
    ml, sl = get(lf)
    if method == "B" or method == "C":
        mr, sr = get(f"{target}:ratio")
        mb, sb = ml + mr, np.hypot(sl, sr)
        if method == "B":
            return mb, sb, notes
        mf, sf = get(f"{target}:fr")
        with np.errstate(over="ignore", invalid="ignore"):
            u = (f0 / np.exp(mf)) ** 2
            use = (mr > 0) & np.isfinite(u) & (u < U_MAX)
            logr = -np.log1p(-np.where(use, u, 0.0))
            slogr = 2 * u / (1 - u) * sf
        notes["fallback_to_B"] = ~use
        notes["clamped"] = (mr > 0) & ~(u < U_MAX)
        return np.where(use, ml + logr, mb), np.where(use, np.hypot(sl, slogr), sb), notes
    if method in ("D", "DF"):
        mres, sres = get(f"{target}:resid" if method == "D" else f"{target}:resid_fm")
        msrf, ssrf = get("SRF:A" if method == "D" else "SRF:Afm")
        u_raw = (f0 / np.exp(msrf)) ** 2
        u = np.minimum(u_raw, U_MAX)
        notes["clamped"] = u_raw > U_MAX
        return ml - np.log1p(-u) + mres, np.sqrt(sl ** 2 + sres ** 2 + (2 * u / (1 - u) * ssrf) ** 2), notes
    if method == "D*":
        mres, sres = get(f"{target}:resid_meas")
        srf = np.where(np.isfinite(table.cols["SRF_GHz"][idx]), table.cols["SRF_GHz"][idx], table.stop_ghz[idx])
        u = np.minimum((f0 / srf) ** 2, U_MAX)
        return ml - np.log1p(-u) + mres, np.hypot(sl, sres), notes
    if method == "E":
        return (*get(f"{target}:stack"), notes)
    if method == "F":
        return (*get(f"{target}:Afm"), notes)
    if method == "BF":
        mr, sr = get(f"{target}:ratiofm")
        return ml + mr, np.hypot(sl, sr), notes
    raise ValueError(method)


def target_values(table: Table, target: str) -> np.ndarray:
    return table.cols["SRF_GHz"] if target == "SRF" else table.cols[target]


def stats(y, m, s, floor: float, k_scale: float) -> dict:
    y, m, s = map(np.asarray, (y, m, s))
    if not len(y):
        return {"n": 0}
    mu = np.exp(m)
    rel = np.abs(mu - y) / y
    sf = np.maximum(s, floor)
    z = (np.log(y) - m) / sf
    return {"n": len(y), "median_rel": float(np.median(rel)), "p90_rel": float(np.quantile(rel, 0.9)), "max_rel": float(rel.max()),
            "coverage": float(np.mean(np.abs(z) <= 2 * k_scale)), "median_abs_z": float(np.median(np.abs(z))),
            "coverage_uncalibrated": float(np.mean(np.abs(np.log(y) - m) <= 2 * s)),
            "median_log_error": float(np.median(np.log(y) - m)), "median_rel_sigma": float(np.median(sf))}


def worst_decile(table: Table, bucket: dict) -> dict:
    """Where a method misses most: the measured SRF / f0 (median) of the rows whose error is in the method's worst 10 %,
    against all its scored rows (NaN SRF = no resonance in the sweep, left out of the median)."""
    if len(bucket["y"]) < 10:
        return {}
    y, m, i = np.array(bucket["y"]), np.array(bucket["m"]), np.array(bucket["i"], dtype=int)
    rel = np.abs(np.exp(m) - y) / y
    worst = rel >= np.quantile(rel, 0.9)
    ratio = table.cols["SRF_GHz"][i] / table.f0
    return {"worst10_srf_over_f0_median": float(np.nanmedian(ratio[worst])) if np.isfinite(ratio[worst]).any() else None,
            "all_srf_over_f0_median": float(np.nanmedian(ratio)) if np.isfinite(ratio).any() else None}


def evaluate(table: Table, folds: list[dict], results: dict, signoff: dict) -> dict:
    """Per target and method: calibration from protocol (i), metrics per protocol, per-level and per-point details."""
    out = {}
    dims = table.ds.dims
    ratio = table.x[:, dims.index("secondary_outer_diameter_um")] / table.x[:, dims.index("primary_outer_diameter_um")]
    near = np.abs(np.log(ratio)) <= np.log(NEAR_RATIO)           # the strongly coupled band designs use
    for target, tdef in table.targets.items():
        methods = list(tdef["needs"])
        y_all = target_values(table, target)
        pooled = {p: {m: {"y": [], "m": [], "s": [], "i": []} for m in methods}
                  for p in ("i", "ii-OD_S", "ii-OD_P", "iii", "ii-OD_S-near", "ii-OD_P-near")}
        per_level, points, notes, skipped = [], [], {m: {} for m in methods}, {}
        for fold in folds:
            if target not in fold["targets"]:
                continue
            idx = np.flatnonzero(fold["held"] & (tdef["rows"] if fold["key"] != "full" else np.isfinite(y_all)))
            if not len(idx):
                continue
            R = {name: results[(table.name, fold["key"], name)] for name in table.needed(fold)}
            preds = {m: predict(table, target, m, R, idx) for m in methods}
            ok = np.ones(len(idx), dtype=bool)
            for m in methods:
                ok &= np.isfinite(preds[m][0]) & np.isfinite(preds[m][1]) & (preds[m][1] > 0)
            for m in methods:
                tally = notes[m].setdefault(fold["protocol"], {"rows": 0})
                tally["rows"] += int(ok.sum())
                for k, v in preds[m][2].items():
                    tally[k] = tally.get(k, 0) + int(np.sum(np.asarray(v)[ok])) if np.ndim(v) else tally.get(k, 0) + int(v)
            skipped[fold["protocol"]] = skipped.get(fold["protocol"], 0) + int((~ok).sum())
            idx_ok = idx[ok]
            y = y_all[idx_ok]
            level_entry = None
            if fold["protocol"].startswith("ii-"):
                level_entry = {"dim": fold["protocol"][3:], "level": fold["level"], "n": len(y), "skipped": int((~ok).sum()), "methods": {}}
            close = near[idx_ok]
            for m in methods:
                mm, ss = preds[m][0][ok], preds[m][1][ok]
                bucket = pooled[fold["protocol"]][m]
                bucket["y"] += y.tolist()
                bucket["m"] += mm.tolist()
                bucket["s"] += ss.tolist()
                bucket["i"] += idx_ok.tolist()
                if level_entry is not None:
                    sub = pooled[fold["protocol"] + "-near"][m]
                    sub["y"] += y[close].tolist()
                    sub["m"] += mm[close].tolist()
                    sub["s"] += ss[close].tolist()
                    sub["i"] += idx_ok[close].tolist()
                if level_entry is not None and len(y):
                    rel = np.abs(np.exp(mm) - y) / y
                    level_entry["methods"][m] = {"median_rel": float(np.median(rel)), "max_rel": float(rel.max())}
            if level_entry is not None:
                per_level.append(level_entry)
            if fold["protocol"] == "iii":
                for j, i in enumerate(idx_ok):
                    r = table.rows[i]
                    points.append({"obs_id": r.obs_id, "params": {SHORT[d]: r.coords[d] for d in table.ds.dims}, "measured": float(y[j]),
                                   "methods": {m: {"m": float(preds[m][0][ok][j]), "s": float(preds[m][1][ok][j])} for m in methods}})
        cal = {}
        for m in methods:
            b = pooled["i"][m]
            y, mm, ss = np.array(b["y"]), np.array(b["m"]), np.array(b["s"])
            rel = np.abs(np.exp(mm) - y) / y
            z = np.abs(np.log(y) - mm) / ss
            cal[m] = {"k_scale": gp.calibration_scale({"z": z.tolist()}), "floor": float(np.median(rel)), "n": len(y)}
        protocols = {}
        for p, by_method in pooled.items():
            protocols[p] = {m: {**stats(b["y"], b["m"], b["s"], cal[m]["floor"], cal[m]["k_scale"]), **worst_decile(table, b)}
                            for m, b in by_method.items()}
        for pt in points:                                     # per point: value, calibrated interval, signed error, z
            for m, e in pt["methods"].items():
                sf = max(e["s"], cal[m]["floor"])
                k = 2 * cal[m]["k_scale"]
                mu = float(np.exp(e["m"]))
                e.update({"predicted": mu, "lo": mu * float(np.exp(-k * sf)), "hi": mu * float(np.exp(k * sf)),
                          "error": pt["measured"] / mu - 1, "z": (np.log(pt["measured"]) - e["m"]) / sf,
                          "inside": bool(abs(np.log(pt["measured"]) - e["m"]) <= k * sf)})
            ref = signoff.get(tuple(sorted((k, float(v)) for k, v in pt["params"].items())), {}).get("SRF" if target == "SRF" else target)
            if ref and ref.get("predicted"):
                scale = 1e9 if target == "SRF" else 1.0
                pt["signoff_predicted"] = ref["predicted"] / scale
                pt["A_vs_signoff"] = pt["methods"]["A"]["predicted"] / (ref["predicted"] / scale) - 1
        out[target] = {"kind": tdef["kind"], "methods": methods, "calibration": cal, "protocols": protocols, "levels": per_level,
                       "points": points, "notes": notes, "skipped_rows": skipped}
    return out


def component_facts(table: Table, results: dict) -> dict:
    """Per component: the full fit's kernel (length scales on the scaled inputs), training rows and fit time."""
    out = {}
    for name in table.comp:
        res = results.get((table.name, "full", name))
        if res is not None:
            out[name] = {"n_train": res["n_train"], "seconds": res["seconds"], "kernel": res["kernel"]}
    c, f = table.cols, f"{table.f0:g}"
    with np.errstate(invalid="ignore", divide="ignore"):
        for L in ("Lp", "Ls"):
            r = c[f"{L}@{f}"] / c[f"{L}_lf"]
            use = table.anch_rows & table.base
            out[f"{L}@{f}:fr"]["share_rows_r_le_1"] = float(np.mean(r[use] <= 1))
            out[f"{L}@{f}:ratio"]["ratio_quantiles"] = [float(v) for v in np.quantile(r[use], [0, 0.1, 0.5, 0.9, 1])]
        both = np.isfinite(c["SRF_p"]) & np.isfinite(c["SRF_s"]) & table.base
        if "SRF_p:A" in out:                                   # how often the two ports see the same resonance
            out["SRF_p:A"]["share_rows_p_s_within_1pct"] = float(np.mean(np.abs(c["SRF_p"][both] / c["SRF_s"][both] - 1) < 0.01))
    return out


def offset_samples(table: Table) -> dict:
    """How the training rows sample the two windings' relative position: counts of (OD_S - OD_P, CS, W_S - W_P) over the
    rows the study trains on, and the same coordinates of the held-out designs between the levels."""
    x, dims = table.x, table.ds.dims
    col = {SHORT[d]: x[:, dims.index(d)] for d in dims}
    rel = np.column_stack([col["OD_S"] - col["OD_P"], col["CS"], col["W_S"] - col["W_P"]])
    keys, counts = np.unique(np.round(rel[table.base], 6), axis=0, return_counts=True)
    return {"columns": ["OD_S-OD_P", "CS", "W_S-W_P", "rows"], "train": [[*map(float, k), int(n)] for k, n in zip(keys, counts)],
            "held": [[float(v) for v in r] for r in rel[table.test]]}


def signoff_predictions(path: Path) -> dict:
    """lib_signoff predictions made before the ten designs were added to the tables, keyed by geometry."""
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {tuple(sorted((SHORT[k], float(v)) for k, v in pt["params"].items())): pt["quantities"] for pt in data["points"]}


def run(args) -> None:
    root, out_path = Path(args.library), Path(args.out)
    lib = query.Library(root, calibrate=False)
    names = args.tables.split(",") if args.tables else list(ANCHOR_GHZ)
    tables = [Table(lib, name, with_e=args.with_e) for name in names]
    folds = {t.name: t.folds(pilot=args.pilot) for t in tables}
    for t in tables:
        print(f"{t.name}: {t.n} rows ({int(t.test.sum())} held for set iii), {int((t.anch_rows & t.base).sum())} usable at {t.f0:g} GHz, "
              f"{int((t.srf_rows & t.base).sum())} with SRF; {len(folds[t.name])} folds, "
              f"{sum(len(t.needed(f)) for f in folds[t.name])} fits: " + ", ".join(f"{f['key']}={len(t.needed(f))}" for f in folds[t.name]), flush=True)
    if args.dry_run:
        return
    t0 = time.time()
    cache = Path(args.cache) if args.cache else None
    results, fit_stats = run_fits(tables, folds, args.workers, cache)
    runs = [{"date": time.strftime("%Y-%m-%d %H:%M"), "tables": names, "workers": args.workers, **fit_stats}]
    if cache is not None:                                # every run that filled this cache: fits made, wall time
        log = cache / "runs.jsonl"
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(runs[0]) + "\n")
        runs = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    here = Path(__file__).resolve().parent
    report = {"library": str(root), "tables": {}, "fits": {"jobs": len(results), "fit_seconds": round(sum(r["seconds"] for r in results.values()), 1),
                                                          "runs": runs},
              "workers": args.workers, "threads_per_worker": THREADS, "pilot": bool(args.pilot), "with_e": bool(args.with_e), "u_max": U_MAX, "near_ratio": NEAR_RATIO,
              "min_level_rows": MIN_LEVEL_ROWS, "seeds": list(SEEDS), "sklearn": sklearn.__version__, "date": time.strftime("%Y-%m-%d %H:%M")}
    for t in tables:
        signoff = signoff_predictions(here / f"xfm_signoff_{t.name.removeprefix('xfm_')}.json")
        per_fold = {}
        for f in folds[t.name]:
            per_fold[f["key"]] = {"protocol": f["protocol"], "held": int(f["held"].sum()), "targets": f["targets"],
                                  "fits": len(t.needed(f))}
        report["tables"][t.name] = {"anchor_ghz": t.f0, "rows": t.n, "held_for_iii": [t.rows[i].obs_id for i in np.flatnonzero(t.test)],
                                    "usable_anchor_rows": int((t.anch_rows & t.base).sum()), "usable_srf_rows": int((t.srf_rows & t.base).sum()),
                                    "feature_map": t.map, "dataset_key": t.ds.key, "folds": per_fold,
                                    "ranges": {SHORT[d]: list(v) for d, v in t.ranges.items()},
                                    "offset_samples": offset_samples(t),
                                    "components": component_facts(t, results), "targets": evaluate(t, folds[t.name], results, signoff)}
    report["seconds_total"] = round(time.time() - t0, 1)
    out_path.write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    print(f"wrote {out_path} ({out_path.stat().st_size / 1e3:.0f} kB); {fit_stats}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("library")
    r.add_argument("out")
    r.add_argument("--tables", default="")
    r.add_argument("--workers", type=int, default=48)
    r.add_argument("--cache", default="")
    r.add_argument("--pilot", action="store_true")
    r.add_argument("--with-e", action="store_true")
    r.add_argument("--dry-run", action="store_true", help="print the folds and the fits they need, fit nothing")
    p = sub.add_parser("page")
    p.add_argument("json")
    p.add_argument("html")
    args = ap.parse_args()
    if args.cmd == "run":
        run(args)
    else:
        page(Path(args.json), Path(args.html))


# -- review page -----------------------------------------------------------------------------------------------------------

LABEL = {"A": "A 直接（现状）", "F": "F 直接·无量纲输入", "B": "B 比值", "BF": "BF 比值·无量纲输入", "C": "C 等效谐振频率",
         "D": "D SRF 先验+残差", "DF": "DF SRF 先验+残差·无量纲输入", "E": "E 加 SRF 输入", "D*": "D* 用实测 SRF（诊断）",
         "MIN": "两绕组谐振取小"}
KIND_LABEL = {"SRF": {"F": "F 无量纲输入"}, "k": {"A": "A 直接（现状，已是无量纲输入）", "B": "B 比值 k_lf ×（k@f0 / k_lf）"},
              "ref": {"A": "直接（现状）"}}
PROTOCOLS = (("i", "(i) 随机留出 20%"), ("ii-OD_S", "(ii) 整档留出 OD_S"), ("ii-OD_P", "(ii) 整档留出 OD_P"), ("iii", "(iii) 格点间实测"))
INK2, MUTED, GRID, ACCENT, OTHER = "#5d5f55", "#8a8c80", "#dcdad2", "#2a78d6", "#b9b7ae"


def _label(kind: str, method: str) -> str:
    return KIND_LABEL.get(kind, {}).get(method, LABEL.get(method, method))


def _pct(v: float | None) -> str:
    if v is None or not np.isfinite(v):
        return "–"
    return f"{v * 100:.2f}" if v < 0.01 else f"{v * 100:.1f}"


def _usable_methods(methods: list[str]) -> list[str]:
    return [m for m in methods if m != "D*"]


def _best(target: dict, protocol: str, key: str = "p90_rel") -> str | None:
    rows = [(target["protocols"][protocol][m].get(key), m) for m in _usable_methods(target["methods"]) if target["protocols"][protocol][m].get("n")]
    rows = [(v, m) for v, m in rows if v is not None]
    return min(rows)[1] if rows else None


def _cells(s: dict, strong: bool) -> str:
    if not s.get("n"):
        return "<td class='num'>–</td><td class='num'>–</td>"
    errs = f"{_pct(s['median_rel'])} / {_pct(s['p90_rel'])} / {_pct(s['max_rel'])}"
    return (f"<td class='num{' best' if strong else ''}'>{errs}</td>"
            f"<td class='num{' bad' if s['coverage'] < 0.9 else ''}'>{s['coverage'] * 100:.0f}% · {s['median_abs_z']:.2f}</td>")


def _head(protocols, n_of) -> str:
    head = "".join(f"<th class='num' colspan='2'>{html.escape(name)}<br><span class='small'>{n_of(p)} 行</span></th>" for p, name in protocols)
    sub = "".join("<th class='num'>中位 / p90 / 最大 %</th><th class='num'>覆盖 · |z|</th>" for _ in protocols)
    return head, sub


def _method_table(target: dict, protocols) -> str:
    """One row per method: per protocol 'median / p90 / max' (%) and 'coverage · median |z|'; bold blue = lowest p90."""
    best = {p: _best(target, p) for p, _ in protocols if p != "i"}
    head, sub = _head(protocols, lambda p: target["protocols"][p][target["methods"][0]].get("n", 0))
    body = []
    for m in target["methods"]:
        cells = "".join(_cells(target["protocols"][p][m], best.get(p) == m) for p, _ in protocols)
        cls = " class='diag'" if m == "D*" else ""
        body.append(f"<tr{cls}><td>{html.escape(_label(target['kind'], m))}</td>{cells}</tr>")
    return (f"<div class='table-wrap'><table><thead><tr><th rowspan='2'>方法</th>{head}</tr><tr>{sub}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table></div>")


def _ref_table(tdata: dict, names: list[str], protocols) -> str:
    """The low-frequency columns (the parts B, C, D multiply) on the same rows as the anchored columns."""
    first = tdata["targets"][names[0]]
    head, sub = _head(protocols, lambda p: first["protocols"][p]["A"].get("n", 0))
    body = "".join(f"<tr><td><code>{q}</code></td>{''.join(_cells(tdata['targets'][q]['protocols'][p]['A'], False) for p, _ in protocols)}</tr>"
                   for q in names)
    return (f"<div class='table-wrap'><table><thead><tr><th rowspan='2'>低频结果列（现状模型）</th>{head}</tr><tr>{sub}</tr></thead>"
            f"<tbody>{body}</tbody></table></div>")


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = ["Noto Sans CJK JP", "Droid Sans Fallback", "DejaVu Sans"]   # the SC face hides in a .ttc listed under JP
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _save(fig, path: Path, dpi: int = 130) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi)
    import matplotlib.pyplot as plt
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def _tidy(ax) -> None:
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def _fig_offlattice(tdata: dict, name: str, path: Path) -> str:
    """Per table: signed error (measured / predicted - 1) of every method at the adopted designs, one panel per column."""
    plt = _plt()
    f = f"{tdata['anchor_ghz']:g}"
    grid = [[f"Lp@{f}", f"Ls@{f}"], [f"k@{f}", "SRF"]]
    heights = [max(len(tdata["targets"][c]["methods"]) for c in row) + 1.6 for row in grid]
    fig, axes = plt.subplots(2, 2, figsize=(12.5, 0.42 * sum(heights) + 1.2), gridspec_kw={"height_ratios": heights})
    for ax, c in zip(axes.flat, [c for row in grid for c in row]):
        t = tdata["targets"][c]
        methods = t["methods"]
        worst = {m: max(abs(pt["methods"][m]["error"]) for pt in t["points"]) for m in methods}
        best = min(_usable_methods(methods), key=worst.get)
        for i, m in enumerate(methods):
            y = len(methods) - 1 - i
            errs = [pt["methods"][m]["error"] * 100 for pt in t["points"]]
            color = INK2 if m == "A" else ACCENT if m == best else OTHER
            ax.plot([min(errs), max(errs)], [y, y], color=color, lw=2, solid_capstyle="round", zorder=2)
            if m == "D*":
                ax.scatter(errs, [y] * len(errs), s=36, facecolor="white", edgecolor=MUTED, linewidth=1.2, zorder=3)
            else:
                ax.scatter(errs, [y] * len(errs), s=36, color=color, edgecolor="white", linewidth=1.0, zorder=3)
        ax.axvline(0, color=MUTED, lw=0.9, zorder=1)
        lo, hi = ax.get_xlim()
        span = hi - lo
        ax.set_xlim(lo, hi + span * 0.22)
        for i, m in enumerate(methods):                         # the largest |error| at the right edge
            ax.text(hi + span * 0.20, len(methods) - 1 - i, f"{worst[m] * 100:.1f}%", va="center", ha="right", fontsize=7.5,
                    color=ACCENT if m == best else INK2, fontweight="bold" if m == best else "normal")
        ax.set_yticks(range(len(methods)), [_label(t["kind"], m) for m in reversed(methods)], fontsize=8.5)
        ax.set_ylim(-0.7, len(methods) - 0.3)
        ax.set_xlabel("实测 / 预测 − 1（%）", fontsize=9)
        ax.set_title(c, fontsize=10.5)
        ax.grid(axis="x", color=GRID, lw=0.7)
        _tidy(ax)
    fig.suptitle(f"{name}：5 个格点间器件上各方法的偏差（右侧数字 = 最大 |偏差|）\n深灰 = 现状 A，蓝 = 最大偏差最小的可用方法，空心 = 诊断用（不可用于查询）",
                 fontsize=10)
    fig.tight_layout()
    return _save(fig, path)


def _fig_levels(tables: dict, target_of: dict, methods: list[str], path: Path) -> str:
    """Leave-one-level-out: the largest relative error of each held-out level against the level's value, per table and method."""
    plt = _plt()
    names = list(tables)
    fig, axes = plt.subplots(len(names), 2, figsize=(11.5, 3.2 * len(names)), squeeze=False)
    colors = [INK2, ACCENT, "#eb6834"]
    for row, name in zip(axes, names):
        t = tables[name]["targets"][target_of[name]]
        for ax, dim in zip(row, ("OD_S", "OD_P")):
            levels = sorted((e for e in t["levels"] if e["dim"] == dim and e["methods"]), key=lambda e: e["level"])
            for m, color in zip(methods, colors):
                if m not in t["methods"]:
                    continue
                xs = [e["level"] for e in levels]
                ys = [e["methods"][m]["max_rel"] * 100 for e in levels]
                ax.plot(xs, ys, "-o", color=color, lw=1.8, ms=5, label=_label(t["kind"], m), markeredgecolor="white")
            ax.set_yscale("log")
            lo, hi = ax.get_ylim()
            ax.set_yticks([v for v in (0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500) if lo <= v <= hi])
            ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _pos: f"{v:g}"))
            ax.yaxis.set_minor_formatter(plt.NullFormatter())
            ax.set_xlabel(f"整档留出的 {dim}（µm）", fontsize=9)
            ax.set_ylabel("该档行的最大误差（%，对数）", fontsize=9)
            ax.set_title(f"{name} · {target_of[name]}", fontsize=10)
            ax.grid(color=GRID, lw=0.7)
            _tidy(ax)
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=len(labels), fontsize=9, frameon=False)
    fig.tight_layout(rect=(0, 0, 1, 1 - 0.35 / fig.get_figheight()))
    return _save(fig, path, dpi=105)


def _fig_offset_gap(T: dict, names: list[str], path: Path) -> str:
    """Where the table samples the windings' relative position (OD_S - OD_P against CS), and where the ten real designs sit."""
    plt = _plt()
    fig, axes = plt.subplots(1, len(names), figsize=(6.2 * len(names), 4.2), squeeze=False)
    for ax, name in zip(axes[0], names):
        s = T[name]["offset_samples"]
        tr = np.array(s["train"])
        keep = (np.abs(tr[:, 0]) <= 60) & (tr[:, 1] <= 40)
        agg: dict[tuple[float, float], int] = {}
        for dx, cs, _dw, n in tr[keep]:
            agg[(dx, cs)] = agg.get((dx, cs), 0) + int(n)
        xs, ys, ns = zip(*[(k[0], k[1], n) for k, n in agg.items()])
        ax.scatter(xs, ys, s=[12 + 4 * np.sqrt(n) for n in ns], color=OTHER, edgecolor="white", linewidth=0.6, zorder=2,
                   label="表里的行（点越大行越多）")
        held = np.array(s["held"])
        ax.scatter(held[:, 0], held[:, 1], s=70, color=ACCENT, edgecolor="white", linewidth=1.0, zorder=3, label="格点间实测器件")
        spots: dict[tuple[float, float], list[str]] = {}
        for (dx, cs, _dw), pt in zip(held, T[name]["targets"][f"Lp@{T[name]['anchor_ghz']:g}"]["points"]):
            spots.setdefault((dx, cs), []).append(pt["obs_id"].removeprefix("obs_"))
        for (dx, cs), ids in spots.items():
            crowded = any(abs(ox - dx) < 3 and 0 < oc - cs < 4 for ox, oc in spots)     # a point just above: label below
            ax.annotate("、".join(ids), (dx, cs), xytext=(0, -12 if crowded else 7), textcoords="offset points", ha="center",
                        fontsize=7.5, color=INK2)
        ax.set_xlabel("OD_S − OD_P（µm）：两个线圈外径之差", fontsize=9)
        ax.set_ylabel("CS（µm）：两个线圈中心的偏移", fontsize=9)
        ax.set_title(f"{name}：CS > 0 的行全部在 OD_S = OD_P 且 W_S = W_P 上", fontsize=10)
        ax.grid(color=GRID, lw=0.7)
        _tidy(ax)
        ax.legend(fontsize=8, frameon=False, loc="upper right")
    fig.tight_layout()
    return _save(fig, path)


def _kernel_of(c: dict) -> tuple[dict, str]:
    k = c["kernel"]
    if "per_nt" in k:                                            # xfm_ms: the N_S = 2 sub-model answers (nearly) every 60 GHz row
        level = "2" if "2" in k["per_nt"] else min(k["per_nt"])
        return k["per_nt"][level], f"（N_S = {level} 子模型）"
    return k, ""


def _ls_table(tdata: dict, names: list[tuple[str, str]]) -> str:
    """Fitted Matern length scales of the full fits, as a fraction of each input's range (outer diameters also in um)."""
    comps, ranges = tdata["components"], tdata.get("ranges", {})
    rows, inputs = [], None
    for key, label in names:
        c = comps.get(key)
        if c is None:
            continue
        k, suffix = _kernel_of(c)
        inputs = inputs or k["inputs"]
        cells = []
        for name, v in zip(k["inputs"], k["length_scale"]):
            um = f"<br><span class='small'>≈{v * (ranges[name][1] - ranges[name][0]):.0f} µm</span>" if name in ("OD_P", "OD_S") and name in ranges else ""
            cells.append(f"<td class='num{' short' if v < 0.15 else ''}'>{v:.3g}{um}</td>")
        rows.append(f"<tr><td>{html.escape(label + suffix)}</td>{''.join(cells)}<td class='num'>{c['n_train']}</td></tr>")
    if not rows:
        return ""
    head = "".join(f"<th class='num'>{html.escape(i)}</th>" for i in inputs)
    return (f"<div class='table-wrap'><table><thead><tr><th>模型（全部训练行拟合）</th>{head}<th class='num'>训练行</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div>")


CSS = """
:root { --ground:#f3f2ee; --surface:#fff; --surface-2:#e7e5de; --ink:#1b1c18; --muted:#5d5f55; --line:#d2d0c6; --accent:#2f6b4f; --warn:#9a4a12; --code-bg:#ebe9e2; --blue:#2a78d6;
  --sans:"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif; --mono:"IBM Plex Mono",Menlo,Consolas,monospace; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --ground:#141512; --surface:#1d1e1a; --surface-2:#262822; --ink:#e7e8e0; --muted:#a2a497; --line:#383a32; --accent:#7cc0a0; --warn:#e3a066; --code-bg:#24261f; --blue:#6da7ec; } }
:root[data-theme="dark"] { --ground:#141512; --surface:#1d1e1a; --surface-2:#262822; --ink:#e7e8e0; --muted:#a2a497; --line:#383a32; --accent:#7cc0a0; --warn:#e3a066; --code-bg:#24261f; --blue:#6da7ec; }
body { background:var(--ground); color:var(--ink); font-family:var(--sans); font-size:15px; line-height:1.7; margin:0; padding-block:0 64px; padding-inline:16px; }
.wrap { max-width:1180px; margin:0 auto; }
header { padding-block:36px 18px; border-bottom:2px solid var(--accent); }
.eyebrow { font-family:var(--mono); font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }
h1 { font-size:31px; margin:8px 0 10px; text-wrap:balance; }
h2 { font-size:21px; margin:38px 0 10px; }
h3 { font-size:17px; margin:26px 0 8px; }
p, li { max-width:92ch; }
p { margin:0 0 12px; }
code { font-family:var(--mono); font-size:.86em; background:var(--code-bg); padding:1px 5px; border-radius:3px; }
.kpis { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin:16px 0; }
.kpi { background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:11px 14px; }
.kpi .v { font-family:var(--mono); font-size:18px; }
.kpi .l { font-size:12.5px; color:var(--muted); }
.table-wrap { overflow-x:auto; border:1px solid var(--line); border-radius:6px; background:var(--surface); margin:0 0 14px; }
table { border-collapse:collapse; width:100%; font-size:12.5px; }
th, td { text-align:left; padding:5px 9px; border-bottom:1px solid var(--line); vertical-align:top; }
th { background:var(--surface-2); font-weight:500; }
td.num, th.num { text-align:right; font-family:var(--mono); font-variant-numeric:tabular-nums; white-space:nowrap; }
td.best { color:var(--blue); font-weight:600; }
td.bad { color:var(--warn); }
td.short { color:var(--warn); font-weight:600; }
tr.diag td { color:var(--muted); font-style:italic; }
.small { font-size:11.5px; color:var(--muted); font-weight:400; }
.finding { border:1px solid var(--line); border-left:4px solid var(--warn); background:var(--surface); border-radius:6px; padding:10px 14px; margin:0 0 10px; }
.finding b { color:var(--warn); }
.ok { border-left-color:var(--accent); }
.ok b { color:var(--accent); }
.note { font-size:13px; color:var(--muted); }
dl.methods { display:grid; grid-template-columns:max-content 1fr; gap:6px 16px; margin:0 0 14px; }
dl.methods dt { font-weight:600; white-space:nowrap; }
dl.methods dd { margin:0; max-width:92ch; }
img { width:100%; max-width:100%; border:1px solid var(--line); border-radius:6px; background:#fff; }
@media (max-width:760px) { .kpis { grid-template-columns:1fr; } dl.methods { grid-template-columns:1fr; } }
"""


INTRO_HTML = (
    "问题：单圈变压器表（xfm_bs_ap、xfm_bs_m10）在采样过的外径档上很准（随机留出的中位误差远小于 1%），在档与档之间却不准："
    "2026-09-24 用真实 EMX 复核的 10 个格点间器件上，Lp@40 偏到 +22%、k@40 到 +9%、SRF 到 −18%，而 Lp_lf 在 2% 以内。"
    "T16 方案的设想是 Lp@40 = Lp_lf × 谐振因子：低频电感随几何光滑，谐振因子在谐振附近很陡，分开建模。"
    "做法：每张表、每个结果列、每种建法用同一批行做三种评价——(i) 随机留出，(ii) 整档留出（"
    "“档与档之间”的替身），(iii) 那 10 个真实格点间器件（从所有训练中去掉）；每种建法都按库的办法校准自己的区间。"
    "多圈表 xfm_ms_ap / xfm_ms_m10 在 60 GHz 的可用行上做 (i)(ii)。库只读，不跑 EMX，不改产品代码。")

METHOD_TEXT = [
    ("A 直接（现状）", "把 Lp@40 当作一个独立的结果列，直接对五个几何参数（OD_P、OD_S、W_P、W_S、CS）插值——库今天的做法。"),
    ("F 直接·无量纲输入", ("还是直接插值 Lp@40，只把输入坐标换成 k 已经在用的那一组：平均外径、外径比 OD_P/OD_S（都取对数）、线宽/外径、"
                         "偏移/平均半径。这样“两个线圈错开多少”成了单独一个坐标，“整体放大”是另一个坐标。")),
    ("B 比值", "Lp@40 = Lp_lf ×（Lp@40 / Lp_lf）：低频电感和“40 GHz 比低频高出多少”各插值一个模型再相乘；两部分的相对不确定度平方相加再开方。"),
    ("BF 比值·无量纲输入", "同 B，但“高出多少”这一部分用 F 的坐标；低频电感仍用原来的五个参数。"),
    ("C 等效谐振频率", ("把高出的部分看成一个并联谐振：比值 r 折算成等效谐振频率 f_r = f0 / √(1 − 1/r) 去插值，再用 1 / (1 − (f0/f_r)²) 换回比值。"
                      "r ≤ 1 的器件（电感随频率下降）无法折算，这些器件用 B 的结果。")),
    ("D SRF 先验+残差", ("先用库的 SRF 模型预测系统 SRF，按 1 / (1 − (f0/SRF)²) 算出“理想谐振抬高”，模型只学实际比值与它之比；"
                       "SRF 的不确定度按导数传到结果。")),
    ("DF SRF 先验+残差·无量纲输入", "同 D，但 SRF 模型和“与理想之比”的模型都用 F 的坐标（仅 bs 表）。"),
    ("D* 用实测 SRF（诊断）", "把 D 里的预测 SRF 换成该器件仿真出来的 SRF。查新器件时拿不到实测值，所以它不是可用的方法，只用来量出 D 的误差有多少来自 SRF 预测。"),
    ("SRF 的 F", "系统 SRF 用 F 的坐标直接插值（仅 bs 表）。"),
    ("SRF 的两绕组谐振取小", ("初级、次级各自看到的谐振 SRF_p、SRF_s 分别插值，取较小者作为系统 SRF——数据里系统 SRF 正是两者中较小的那个（仅 bs 表；"
                           "ms 表每一行的系统 SRF 都等于 SRF_s，取小就是 A 本身）。")),
    ("k 的 B", "k@40 = k_lf ×（k@40 / k_lf），两部分都用无量纲坐标（现状 A 本来就用它）；仅 bs 表。"),
]

PROTOCOL_HTML = (
    "<p><b>(i) 随机留出</b>：随机拿走 20% 的行，用其余 80% 拟合、预测被拿走的行，换 5 个随机种子（0–4）。被拿走的行几乎总有同一外径档上的邻居——"
    "这正是库今天校准区间用的办法，衡量的是“档上”的精度。</p>"
    "<p><b>(ii) 整档留出</b>：把某一个次级外径 OD_S（或初级外径 OD_P）取值的全部行一起拿走，用其余的档预测它们。被预测的行在自己那一档上没有任何邻居，"
    "最近的邻居在相邻的档上——这是“档与档之间”的替身。逐一做所有 ≥ 25 行、且不在取值范围两端的档（两端是外推，不是本题），把各档的误差合在一起统计。"
    "单圈表的 OD_P 只有 8 档（60、80、100、120、150、180、210、240 µm），OD_S 在每个 OD_P 附近按 4 µm 加密——所以留出一档 OD_P 最接近"
    "格点间实测器件的处境（它们的 OD_P 在 86–114 µm 之间，10 个里 9 个不在档上）。</p>"
    "<p><b>(iii) 格点间实测</b>：2026-09-24 真实 EMX 的 10 个器件（每张 bs 表 5 个；外径在档与档之间，偏移 CS = 6–12 µm），"
    "现为表里的 obs_1577–1581；本研究把它们从所有训练中去掉，当作测试集。</p>"
    "<p><b>指标</b>：相对误差 |预测 − 实测| / 实测 的中位 / p90 / 最大。<b>覆盖</b>：实测落在校准后 2σ 区间内的比例——每种建法都按库的办法"
    "用自己在 (i) 里的结果校准：σ 不低于 (i) 的中位误差，区间放宽到 (i) 中 95% 的行落在内。<b>|z|</b>：实测偏离 / σ 的中位数，区间宽度刚好时约 0.67，"
    "远小于它说明区间偏宽，远大于说明偏窄。表中蓝色粗体 = 该评价下 p90 最小的可用建法（D* 不参评）；橙色 = 覆盖低于 90%。"
    "图中的“偏差”= 实测 / 预测 − 1，与格点间复核页同一口径（所以现状一行对得上 +22%），表中误差用 |预测 − 实测| / 实测。</p>")

WHY_MAP_HTML = (
    "<p>单圈变压器的两个线圈上下叠放。OD_P 与 OD_S 相近时，两条走线上下重叠：耦合强、两线圈之间的电容大、系统 SRF 低；外径差超过线宽后走线错开，"
    "耦合与电容很快下降。所以 k、SRF 以及靠近谐振的 Lp@40 主要取决于“两个外径差多少（相对线宽与尺寸）”——这个方向上很陡；"
    "把整个器件等比例放大，结果只是平缓地变化。表也是按这个特点采样的：OD_P 取 8 档，每档附近的 OD_S 以 4 µm 加密。</p>"
    "<p>用原始坐标（OD_P、OD_S 各一个轴）时，“两外径相等”这条陡峭的山脊斜穿坐标系，模型只能在每个 OD_P 档附近学到它；OD_P 落在两档之间时，"
    "只能在相隔 20–30 µm 的两档之间硬插值。换成“平均外径 + 外径比”两个轴后，山脊与“外径比”轴垂直，相邻 OD_P 档上学到的形状可以沿“平均外径”轴直接挪过来——"
    "这就是 F / BF / DF 在整档留出 OD_P 时大幅好转的原因（下文长度尺度一节有数字）。</p>")

GAP_HTML = (
    "<p>10 个格点间实测器件都带 6–12 µm 的中心偏移 CS，且两个外径（多数还有线宽）不相等；而表里 CS > 0 的 96 行全部是 OD_S = OD_P 且 W_S = W_P 的器件"
    "（每档 OD_P 12 行）——“有偏移 + 外径不等”的组合从没采过（下图）。所以这些器件上 k_lf、SRF 本身就不准，与用哪种模型无关；"
    "要改善只能补行（T16.1 的 <code>lib.densify</code> 会在模型不确定度大的地方选点，这一带正是）。</p>")

LS_INTRO_HTML = (
    "<p>GP 的长度尺度表示模型认为结果沿某个输入变化得有多快，单位是该输入取值范围的比例：0.08 表示沿该输入走过范围的 8%，结果就明显变了"
    "（OD_S 的范围 48–252 µm，8% 约 16 µm）；数值越大越平滑，橙色 = 小于 0.15。上面一张是用原始五个参数的模型，下面一张是用无量纲坐标的模型"
    "（输入不同，两张表之间不能逐列比较）。ms 表显示 N_S = 2 的子模型（60 GHz 可用行几乎全是 N_S = 2）。</p>")


def _ls_fmt(v: float) -> str:
    return f"{v:.2g}" if v < 1 else f"{v:.1f}"


class _Num:
    """Number lookups for the narrative: v(table, column, protocol, method, key); col uses {f} for the table's frequency."""

    def __init__(self, T: dict, st, pc):
        self.T, self.st, self.pc = T, st, pc

    def col(self, name: str, column: str) -> str:
        return column.format(f=f"{self.T[name]['anchor_ghz']:g}")

    def v(self, name: str, column: str, p: str, m: str, key: str = "max_rel") -> float | None:
        return self.st(name, self.col(name, column), p, m, key)

    def s(self, names: list[str], column: str, p: str, m: str, key: str = "max_rel", digits: int = 1) -> str:
        """The value on each table, joined with ' / ' (bs: ap / m10)."""
        return " / ".join(self.pc(self.v(n, column, p, m, key), digits) for n in names)

    def plain(self, names: list[str], column: str, p: str, m: str, key: str) -> str:
        """A unitless value on each table (e.g. median |z|), two decimals, joined with ' / '."""
        return " / ".join(f"{self.v(n, column, p, m, key):.2f}" for n in names)

    def ls(self, name: str, comp: str, inp: str) -> float | None:
        c = self.T[name]["components"].get(self.col(name, comp))
        if c is None:
            return None
        k, _ = _kernel_of(c)
        return k["length_scale"][k["inputs"].index(inp)] if inp in k["inputs"] else None

    def lss(self, names: list[str], comp: str, inp: str) -> str:
        return " / ".join(_ls_fmt(self.ls(n, comp, inp)) for n in names)


def _near_table(T: dict, bs: list[str], st, pc) -> str:
    """Leave-one-level-out restricted to the strongly coupled band 1/1.2 <= OD_S/OD_P <= 1.2: p90 / max per method."""
    n = _Num(T, st, pc)
    rows = [("Lp@{f}", m) for m in ("A", "F", "BF", "DF", "D*")] + [("Ls@{f}", m) for m in ("A", "F", "BF", "DF", "D*")] + [("SRF", m) for m in ("A", "F")]
    head = "".join(f"<th class='num'>{b}<br><span class='small'>{p[3:7]} 整档留出</span></th>" for b in bs for p in ("ii-OD_S-near", "ii-OD_P-near"))
    body = []
    for col, m in rows:
        cells = "".join(f"<td class='num'>{n.pc(n.v(b, col, p, m, 'p90_rel'))} / {n.pc(n.v(b, col, p, m))}</td>"
                        for b in bs for p in ("ii-OD_S-near", "ii-OD_P-near"))
        kind = T[bs[0]]["targets"][n.col(bs[0], col)]["kind"]
        cls = " class='diag'" if m == "D*" else ""
        body.append(f"<tr{cls}><td><code>{n.col(bs[0], col)}</code> {html.escape(_label(kind, m))}</td>{cells}</tr>")
    rows_n = " / ".join(str(T[b]["targets"][n.col(b, "Lp@{f}")]["protocols"]["ii-OD_P-near"]["A"]["n"]) for b in bs)
    return (f"<div class='table-wrap'><table><thead><tr><th>结果列与方法（p90 / 最大误差）</th>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
            f"<p class='note'>只统计 1/1.2 ≤ OD_S/OD_P ≤ 1.2 的行（两个线圈大小相近、耦合强，设计实际用的一带；整档留出 OD_P 时 {rows_n} 行）。</p>")


def _findings(T: dict, bs: list[str], ms: list[str], st, pc, gap_img: str) -> str:
    n = _Num(T, st, pc)
    L = "Lp@{f}"
    out = ["<h2>结论</h2>"]
    out.append(
        f"<div class='finding'><b>现状 A 在档与档之间确实不准，而且不只是那 10 个器件。</b>Lp@40 随机留出的中位误差 {n.s(bs, L, 'i', 'A', 'median_rel', 2)}"
        f"（两张 bs 表，依次为 ap / m10），但把整档 OD_P 拿掉再预测时，p90 误差 {n.s(bs, L, 'ii-OD_P', 'A', 'p90_rel')}、最大 {n.s(bs, L, 'ii-OD_P', 'A')}；"
        f"SRF 的 p90 更到 {n.s(bs, 'SRF', 'ii-OD_P', 'A', 'p90_rel')}。10 个格点间实测器件上 A 的最大误差 {n.s(bs, L, 'iii', 'A')}"
        f"（A 的预测与 9-24 复核时库给出的预测逐位相同，本研究复现了那次复核）。</div>")
    out.append(
        f"<div class='finding'><b>“低频电感光滑”成立，“把谐振因子单独拿出来就好插值”不成立。</b>Lp_lf 整档留出 OD_P 的 p90 只有 "
        f"{n.s(bs, 'Lp_lf', 'ii-OD_P', 'A', 'p90_rel')}，它的长度尺度（越大越平滑）沿 OD_P 为 {n.lss(bs, 'Lp_lf', 'OD_P')}、沿 OD_S 为 "
        f"{n.lss(bs, 'Lp_lf', 'OD_S')}；但比值 Lp@40 / Lp_lf 在原始坐标里比 Lp@40 本身还陡（沿 OD_P {n.lss(bs, L + ':ratio', 'OD_P')}、沿 OD_S "
        f"{n.lss(bs, L + ':ratio', 'OD_S')}；A 为 {n.lss(bs, L + ':A', 'OD_P')} 与 {n.lss(bs, L + ':A', 'OD_S')}）——陡的部分整个搬进了比值。所以只拆不换坐标的 B 改善有限："
        f"整档留出 OD_P 的 p90 {n.s(bs, L, 'ii-OD_P', 'B', 'p90_rel')}，格点间最大 {n.s(bs, L, 'iii', 'B')}。C、D 同理（见失败模式）。</div>")
    out.append(
        "<div class='finding ok'><b>关键在输入坐标：陡的方向是“两个线圈错开多少”。</b>" + WHY_MAP_HTML.replace("<p>", "").replace("</p>", " ")
        + f"SRF 只换坐标（F）：整档留出 OD_P 的 p90 {n.s(bs, 'SRF', 'ii-OD_P', 'A', 'p90_rel')} → {n.s(bs, 'SRF', 'ii-OD_P', 'F', 'p90_rel')}，"
        f"沿“平均外径”的长度尺度从 OD_P 轴的 {n.lss(bs, 'SRF:A', 'OD_P')} 变成 {n.lss(bs, 'SRF:Afm', 'log mean OD')}。</div>")
    out.append(
        f"<div class='finding ok'><b>最好的是 DF：低频电感 × 理想谐振抬高（用换了坐标的 SRF）× 换了坐标的残差。</b>Lp@40 整档留出 OD_P 的 p90 "
        f"{n.s(bs, L, 'ii-OD_P', 'A', 'p90_rel')} → {n.s(bs, L, 'ii-OD_P', 'DF', 'p90_rel')}，最大 {n.s(bs, L, 'ii-OD_P', 'A')} → "
        f"{n.s(bs, L, 'ii-OD_P', 'DF')}；整档留出 OD_S 的 p90 {n.s(bs, L, 'ii-OD_S', 'A', 'p90_rel')} → {n.s(bs, L, 'ii-OD_S', 'DF', 'p90_rel')}；"
        f"随机留出的 p90 也从 {n.s(bs, L, 'i', 'A', 'p90_rel')} 降到 {n.s(bs, L, 'i', 'DF', 'p90_rel')}。它的区间也最窄且诚实：整档留出 OD_P 时 σ 的中位 "
        f"{n.s(bs, L, 'ii-OD_P', 'DF', 'median_rel_sigma')}（BF {n.s(bs, L, 'ii-OD_P', 'BF', 'median_rel_sigma')}，A {n.s(bs, L, 'ii-OD_P', 'A', 'median_rel_sigma')}），"
        f"覆盖 {n.s(bs, L, 'ii-OD_P', 'DF', 'coverage', 0)}；格点间实测上 |z| 中位 {n.plain(bs, L, 'iii', 'DF', 'median_abs_z')}"
        "（区间宽度刚好时约 0.67）——对按区间筛器件的可行区域查找，这比只看误差更要紧。"
        f"次好的是 BF（p90 {n.s(bs, L, 'ii-OD_P', 'BF', 'p90_rel')}），但它在整档留出时有个别行偏得很多（最大 {n.s(bs, L, 'ii-OD_P', 'BF')}）。"
        f"下表只看设计常用的强耦合一带（外径比 0.83–1.2），结论不变。</div>" + _near_table(T, bs, st, pc))
    out.append(
        f"<div class='finding ok'><b>谐振因子的形状本身是对的，D 的误差主要来自 SRF 预测。</b>把 D 用的预测 SRF 换成仿真实测的 SRF（D*，仅诊断），"
        f"Lp@40 整档留出 OD_P 的 p90 {n.s(bs, L, 'ii-OD_P', 'D', 'p90_rel')} → {n.s(bs, L, 'ii-OD_P', 'D*', 'p90_rel')}，格点间最大 "
        f"{n.s(bs, L, 'iii', 'D')} → {n.s(bs, L, 'iii', 'D*')}。DF 换坐标后 SRF 在档与档之间已经很准，所以整档留出时 DF 甚至好于 D*（D* 的残差仍用原始坐标）。</div>")
    out.append(
        "<div class='finding'><b>10 个格点间实测器件还踩在一个采样空白上，那里任何建法都受限。</b>" + GAP_HTML.replace("<p>", "").replace("</p>", "")
        + f"这些器件上 k_lf 的最大误差 {n.s(bs, 'k_lf', 'iii', 'A')}、SRF {n.s(bs, 'SRF', 'iii', 'A')}（换坐标的 F 为 {n.s(bs, 'SRF', 'iii', 'F')}）。"
        f"于是依赖 SRF 的 DF 在这里偏得比不经过 SRF 的 BF 多：Lp@40 最大 DF {n.s(bs, L, 'iii', 'DF')}、BF {n.s(bs, L, 'iii', 'BF')}（A {n.s(bs, L, 'iii', 'A')}）；"
        f"k@40 仍以现状 A 最好（A {n.s(bs, 'k@{f}', 'iii', 'A')}，B {n.s(bs, 'k@{f}', 'iii', 'B')}）。</div>"
        f"<img src='{gap_img}' alt='表里的行在（外径差，偏移）平面上的分布与格点间实测器件的位置'>"
        "<p class='note'>图：横轴 OD_S − OD_P，纵轴中心偏移 CS（只画 |OD_S − OD_P| ≤ 60 µm、CS ≤ 40 µm 的部分）。灰点 = 表里的行，点越大行越多；"
        "蓝点 = 格点间实测器件（标 obs 编号，几何见下文各表的逐器件表）。CS > 0 的灰点全在 OD_S − OD_P = 0 这条竖线上（而且都是 W_S = W_P）。</p>")
    if ms:
        out.append(
            f"<div class='finding'><b>多圈表（60 GHz）：换坐标同样有效，但借 SRF 的 D 会坏事。</b>Lp@60 整档留出 OD_S 的 p90：A {n.s(ms, 'Lp@{f}', 'ii-OD_S', 'A', 'p90_rel')}"
            f" → BF {n.s(ms, 'Lp@{f}', 'ii-OD_S', 'BF', 'p90_rel')}（F {n.s(ms, 'Lp@{f}', 'ii-OD_S', 'F', 'p90_rel')}）；Ls@60：A {n.s(ms, 'Ls@{f}', 'ii-OD_S', 'A', 'p90_rel')}"
            f" → BF {n.s(ms, 'Ls@{f}', 'ii-OD_S', 'BF', 'p90_rel')}。D 比 A 还差（Lp@60 整档留出 OD_S 的最大误差 {n.s(ms, 'Lp@{f}', 'ii-OD_S', 'D')}）："
            f"多圈表的 SRF 模型（按匝数分开的原始坐标模型）在档与档之间自己就偏 p90 {n.s(ms, 'SRF', 'ii-OD_S', 'A', 'p90_rel')}，而初级只感受到次级谐振的一部分。"
            f"D* 对 Ls@60 很准（p90 {n.s(ms, 'Ls@{f}', 'ii-OD_S', 'D*', 'p90_rel')}），说明多圈表也值得先把 SRF 模型做好（本次未试 ms 的无量纲 SRF：它是跨匝数、2440 行的单个大模型）。"
            f"强耦合一带（外径比 0.83–1.2）在 60 GHz 的可用行很少，最好的 BF 在那里 p90 仍有 {n.s(ms, 'Ls@{f}', 'ii-OD_S-near', 'BF', 'p90_rel')}（Ls@60）。</div>")
    return "".join(out)


def _recommend(T: dict, bs: list[str], ms: list[str], st, pc) -> str:
    n = _Num(T, st, pc)
    L = "Lp@{f}"
    ms_item = (f"<li><b>多圈表（xfm_ms）的 Lp、Ls：<code>ratio</code> + <code>feature_map: xfm_ms_dimensionless</code>（BF）。</b>整档留出 OD_S 的 p90 "
               f"Lp@60 {n.s(ms, 'Lp@{f}', 'ii-OD_S', 'A', 'p90_rel')} → {n.s(ms, 'Lp@{f}', 'ii-OD_S', 'BF', 'p90_rel')}，Ls@60 "
               f"{n.s(ms, 'Ls@{f}', 'ii-OD_S', 'A', 'p90_rel')} → {n.s(ms, 'Ls@{f}', 'ii-OD_S', 'BF', 'p90_rel')}。在多圈表的 SRF 模型改好之前不要用 "
               "<code>resonance</code>（D 在那里比 A 还差）；ms 的 SRF 换坐标另做一次对照。</li>") if ms else ""
    return (
        "<h2>推荐：T16.2b 做什么</h2>"
        "<ol>"
        f"<li><b>先做、只改清单：xfm_bs 两表的 <code>SRF</code> 加 <code>feature_map: xfm_bs_dimensionless</code>（F）。</b>清单与代码已支持，无需改代码。"
        f"整档留出 OD_P 的 p90 {n.s(bs, 'SRF', 'ii-OD_P', 'A', 'p90_rel')} → {n.s(bs, 'SRF', 'ii-OD_P', 'F', 'p90_rel')}，"
        f"随机留出 p90 {n.s(bs, 'SRF', 'i', 'A', 'p90_rel')} → {n.s(bs, 'SRF', 'i', 'F', 'p90_rel')}。副作用：整档留出时偶有大误差（最大 "
        f"{n.s(bs, 'SRF', 'ii-OD_P', 'F')}），但全在强耦合一带之外——误差最大的是外径比 0.5 或 2 的行（OD 120 与 240 配对，整档拿走后这个比值只剩一个尺寸），"
        f"强耦合一带内最大只有 {n.s(bs, 'SRF', 'ii-OD_P-near', 'F')}；格点间实测器件上与 A 相当（最大 {n.s(bs, 'SRF', 'iii', 'F')} 对 {n.s(bs, 'SRF', 'iii', 'A')}）。</li>"
        "<li><b>曲线结果列（Lp、Ls）增加清单选项 <code>model: direct | ratio | resonance</code>，默认 <code>direct</code>（库行为不变）。</b>"
        "<ul>"
        "<li><code>ratio</code>（= 本研究的 BF）：值 = 低频电感模型（按它自己的设置）× 比值模型 L@f0 / L_lf（用该曲线自己的 <code>feature_map</code>）；"
        "相对 σ 平方和开方。</li>"
        "<li><code>resonance</code>（= DF）：值 = 低频电感 × 1/(1 − (f0/SRF)²) × 残差模型；SRF 取同一张表 <code>SRF</code> 结果列的模型（第 1 条之后它带 feature_map），"
        "残差模型的训练目标用该 SRF 模型在训练行上的预测算出、用曲线的 <code>feature_map</code>；σ 含 SRF 的导数项 2u/(1−u)·σ_SRF（u = (f0/SRF)²），"
        "u 上限 0.8。拟合顺序：先 SRF 与低频电感，再残差；残差模型的缓存键要包含 SRF 模型的键。</li>"
        "<li>两者都在组合后的预测上做校准（5×20% 留出，与本研究相同：各部分在同一 80% 上拟合），σ 下限 = 组合预测的留出中位误差；"
        "对外仍是一个带 <code>predict / predict_bounds / available / k_scale / log_target</code> 的模型对象，<code>predict_all</code>、<code>query</code>、"
        "<code>suggest</code>、<code>lib.region</code> 不用改；<code>query</code> 的答案里注明组成（“由 Lp_lf × 谐振因子 × 残差得到”）。</li>"
        "<li>C（等效谐振频率）、E（SRF 当输入）、两绕组谐振取小都不做。</li>"
        "</ul></li>"
        f"<li><b>xfm_bs 的 Lp、Ls 设 <code>model: resonance</code> + <code>feature_map: xfm_bs_dimensionless</code>（DF）。</b>"
        f"三种评价里两种最好、第三种也好于现状：整档留出 OD_P 的 p90 {n.s(bs, L, 'ii-OD_P', 'A', 'p90_rel')} → {n.s(bs, L, 'ii-OD_P', 'DF', 'p90_rel')}、"
        f"最大 {n.s(bs, L, 'ii-OD_P', 'A')} → {n.s(bs, L, 'ii-OD_P', 'DF')}；格点间实测最大 {n.s(bs, L, 'iii', 'A')} → {n.s(bs, L, 'iii', 'DF')}"
        f"（BF 为 {n.s(bs, L, 'iii', 'BF')}，那里 SRF 本身偏 ~20%）；区间最窄且诚实（整档留出 OD_P 时 σ 中位 "
        f"{n.s(bs, L, 'ii-OD_P', 'DF', 'median_rel_sigma')}、覆盖 {n.s(bs, L, 'ii-OD_P', 'DF', 'coverage', 0)}）。"
        f"如果想分两步落地，先做 <code>ratio</code>（改动小、不依赖 SRF），"
        f"它把整档留出 OD_P 的 p90 降到 {n.s(bs, L, 'ii-OD_P', 'BF', 'p90_rel')}、格点间最大降到 {n.s(bs, L, 'iii', 'BF')}，但个别行仍会偏到 "
        f"{n.s(bs, L, 'ii-OD_P', 'BF')}。</li>"
        + ms_item +
        f"<li><b>k 保持现状</b>（direct，已有 feature_map）：B 的格点间最大误差 {n.s(bs, 'k@{f}', 'iii', 'B')}，比 A 的 {n.s(bs, 'k@{f}', 'iii', 'A')} 差。"
        "Q 列本研究没有涉及。</li>"
        "<li><b>补点（T16.1）</b>：在“有偏移 + 两外径不等”的组合上补行，否则格点间实测那一类器件的 k、SRF 仍会偏 10–20%，DF 也跟着偏；"
        "补点回流后用本脚本重跑同一套评价，确认 DF 在格点间实测上的误差下降。</li>"
        "<li><b>T16.2b 的验收</b>：用本研究 JSON 里 DF / BF 的行（整档留出 OD_S、OD_P 与格点间实测的中位 / p90 / 最大误差、覆盖）作为复现目标——"
        "同一数据、同一设置下应逐位相同；未设 <code>model</code> 的表（电感表、k、SRF 以外的列）逐位不变。</li>"
        "</ol>")


def _failures(T: dict, bs: list[str], ms: list[str], st, pc) -> str:
    n = _Num(T, st, pc)
    L = "Lp@{f}"
    shares = " / ".join(f"{T[b]['components'][n.col(b, L + ':fr')]['share_rows_r_le_1'] * 100:.0f}%" for b in bs)
    same = " / ".join(f"{T[b]['components']['SRF_p:A']['share_rows_p_s_within_1pct'] * 100:.0f}%" for b in bs)
    def worst(m: str, key: str = "worst10_srf_over_f0_median") -> str:
        return " / ".join(f"{n.v(b, L, 'ii-OD_P', m, key):.2f}" for b in bs)

    items = [
        ("A 直接（现状）", (
            "“错开量”这条陡峭的山脊只在每个 OD_P 档附近学得到，OD_P 落在两档之间就只能跨 20–30 µm 硬插值；区间会变宽，"
            f"但整档留出 OD_P 时覆盖只有 {n.s(bs, L, 'ii-OD_P', 'A', 'coverage', 0)}（目标 95%），个别行偏到 {n.s(bs, L, 'ii-OD_P', 'A')}。")),
        ("F 直接·无量纲输入", (
            "一个模型要同时装下平缓的低频电感（它随 OD_P 本身变化，在新坐标里也沿“外径比”变化）和陡的谐振因子，"
            f"“外径比”轴只能取很短的长度尺度（{n.lss(bs, L + ':Afm', 'log OD_P/OD_S')}）；p90 大幅改善，但个别行仍偏到 "
            f"{n.s(bs, L, 'ii-OD_P', 'F')}，格点间实测上 ap 表还不如 A（{n.s(bs, L, 'iii', 'F')}）。")),
        ("B 比值", "比值在原始坐标里比 Lp@40 还陡，只去掉了低频电感那部分变化，档与档之间仍偏几十个百分点。"),
        ("BF 比值·无量纲输入", (
            f"不依赖 SRF，格点间实测最好（{n.s(bs, L, 'iii', 'BF')}）；但整档留出时靠近谐振的行偏得很多（最大 {n.s(bs, L, 'ii-OD_P', 'BF')}）："
            f"整档留出 OD_P 时 BF 误差最大的 10% 行，SRF 的中位只有 f0 的 {worst('BF')} 倍（全部行为 {worst('BF', 'all_srf_over_f0_median')} 倍），"
            "正是谐振抬高最陡的地方——比值模型不知道谐振在哪，DF 知道。")),
        ("C 等效谐振频率", (
            f"{shares} 的行 Lp@40 ≤ Lp_lf（导体的趋肤 / 邻近效应先让电感下降，谐振的抬高还没压过它），无法折算，只能用 B；"
            "10 个格点间器件全部落到 B。比值略大于 1 时 f_r 趋于无穷，目标有长尾；f_r 预测偏低时 1/(1 − (f0/f_r)²) 急剧放大，"
            f"最大误差达 {n.s(bs, L, 'ii-OD_S', 'C')}。")),
        ("D SRF 先验+残差", (
            "完全受制于 SRF 模型：原始坐标的 SRF 在两档之间偏几十个百分点，经 1/(1 − (f0/SRF)²) 放大"
            f"（整档留出 OD_S 最大 {n.s(bs, L, 'ii-OD_S', 'D')}）；残差在原始坐标里也不平滑。在多圈表上系统 SRF 是次级的谐振，初级只感受到一部分，D 比 A 还差。")),
        ("DF SRF 先验+残差·无量纲输入", (
            "三个模型串联：SRF 的误差直接传给电感，区间只按导数计入。SRF 本身没采到的地方（格点间实测器件那一带）"
            f"DF 跟着偏（{n.s(bs, L, 'iii', 'DF')}，BF {n.s(bs, L, 'iii', 'BF')}）；实现也最复杂（拟合有先后、缓存键相互依赖）。")),
        ("D* 用实测 SRF（诊断）", "不能用于查询（要先仿真才有 SRF），只说明“低频电感 × 理想谐振抬高 × 平缓残差”这个形状是对的。"),
        ("E 加 SRF 输入（只在试跑里做过）", (
            "模型把预测的 SRF 当成精确输入：试跑（xfm_bs_m10，一个随机种子、每个方向一档）中误差介于 A 与 BF 之间"
            "（格点间 Lp@40 最大 7.4%），但区间严重偏窄——校准放宽 2.8 倍后格点间覆盖仍只有 60%（Lp@40）/ 80%（Ls@40），正式对照不再计入。")),
        ("两绕组谐振取小", (
            f"SRF_p 与 SRF_s 大多是同一个系统谐振从两端口看到的（{same} 的行两者相差不到 1%），两个模型和系统 SRF 的模型一样陡；"
            f"两者不同时，一个外推错了，取小就取到错的那个（随机留出最大 {n.s(bs, 'SRF', 'i', 'MIN')}，A 为 {n.s(bs, 'SRF', 'i', 'A')}）。")),
        ("k 的 B", f"k_lf 在采样空白处本身就偏（格点间最大 {n.s(bs, 'k_lf', 'iii', 'A')}），乘上比值救不回来。"),
    ]
    return "<ul>" + "".join(f"<li><b>{html.escape(k)}</b>：{v}</li>" for k, v in items) + "</ul>"


def _location_html(d: dict) -> str:
    runs = d["fits"]["runs"]
    fitted = [r for r in runs if r["fits"]]
    again = len(runs) - len(fitted)
    made = "；".join(f"{r['date']} 结束的一次运行：{'、'.join(r['tables'])}，新拟合 {r['fits']} 次，拟合阶段墙钟 {r['wall_seconds'] / 60:.0f} min"
                    for r in fitted)
    return (f"<p class='note'>脚本 <code>docs/refactor/reports/library_query/xfm_anchor_model_study.py</code>（<code>run</code> 做拟合与评价，写 "
            f"<code>xfm_anchor_model_study.json</code>；<code>page</code> 生成本页与 <code>figs/XFM_ANCHOR_MODEL_STUDY_CN_*.png</code>）。"
            f"库 <code>{html.escape(d['library'])}</code> 只读（数据集走库的缓存，不跑 EMX）。拟合在 {d['workers']} 个 spawn 工作进程里并行，每个 "
            f"{d['threads_per_worker']} 个 BLAS 线程、nice；每次拟合的设置与库相同（Matern 5/2、4 次重启、random_state=0）。"
            f"共 {d['fits']['jobs']} 个（表, 评价折, 模型）拟合，GP 拟合累计 {d['fits']['fit_seconds'] / 3600:.0f} 进程·小时；{made}"
            + (f"；之后 {again} 次只从拟合缓存重算评价（0 次拟合）" if again else "") +
            f"。试跑（xfm_bs_m10 + xfm_ms_ap，每种评价各取一折，含 E）另做了约 200 次拟合、约 9 min。scikit-learn {d['sklearn']}。</p>")


def _point_table(t: dict) -> str:
    """The figure's numbers: per adopted design, the signed error of the main methods (table view of the figure)."""
    f = f"{t['anchor_ghz']:g}"
    cols = [(f"Lp@{f}", ["A", "BF", "DF"]), (f"Ls@{f}", ["A", "BF", "DF"]), (f"k@{f}", ["A", "B"]), ("SRF", ["A", "F", "MIN"])]
    cols = [(q, [m for m in ms if m in t["targets"][q]["methods"]]) for q, ms in cols]
    head = "".join(f"<th class='num' colspan='{len(ms)}'>{q}</th>" for q, ms in cols)
    sub = "".join(f"<th class='num'>{m}</th>" for _, ms in cols for m in ms)
    body = []
    for i, pt in enumerate(t["targets"][f"Lp@{f}"]["points"]):
        geo = ", ".join(f"{k}={v:g}" for k, v in pt["params"].items())
        cells = []
        for q, ms in cols:
            p = t["targets"][q]["points"][i]
            assert p["obs_id"] == pt["obs_id"]
            for m in ms:
                e = p["methods"][m]
                cells.append(f"<td class='num{'' if e['inside'] else ' bad'}'>{e['error'] * 100:+.1f}{'' if e['inside'] else ' ✗'}</td>")
        body.append(f"<tr><td>{pt['obs_id']}<br><span class='small'>{html.escape(geo)}</span></td>{''.join(cells)}</tr>")
    return (f"<div class='table-wrap'><table><thead><tr><th rowspan='2'>器件（µm）</th>{head}</tr><tr>{sub}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table></div>")


def page(json_path: Path, html_path: Path) -> None:
    d = json.loads(json_path.read_text(encoding="utf-8"))
    T = d["tables"]
    stem, figs = html_path.stem, html_path.parent / "figs"
    bs = [n for n in T if n.startswith("xfm_bs")]
    ms = [n for n in T if n.startswith("xfm_ms")]

    def st(name: str, target: str, p: str, m: str, key: str = "max_rel") -> float | None:
        return T[name]["targets"][target]["protocols"][p][m].get(key)

    def pc(v: float | None, digits: int = 1) -> str:
        return "–" if v is None else f"{v * 100:.{digits}f}%"

    def pair(target: str, p: str, m: str, key: str, names: list[str], digits: int = 1) -> str:
        return " / ".join(pc(st(n, target.format(f=f"{T[n]['anchor_ghz']:g}"), p, m, key), digits) for n in names)

    runs = [r for r in d["fits"]["runs"] if r["fits"]]
    fitted = sum(r["fits"] for r in runs)
    wall_min = sum(r["wall_seconds"] for r in runs) / 60
    kpis = [
        (f"{pair('Lp@{f}', 'ii-OD_P', 'A', 'p90_rel', bs)} → {pair('Lp@{f}', 'ii-OD_P', 'DF', 'p90_rel', bs)}",
         "Lp@40 整档留出 OD_P 的 p90 误差：现状 A → DF（两张 bs 表：ap / m10）"),
        (f"{pair('Lp@{f}', 'iii', 'A', 'max_rel', bs)} → {pair('Lp@{f}', 'iii', 'DF', 'max_rel', bs)}（BF {pair('Lp@{f}', 'iii', 'BF', 'max_rel', bs)}）",
         "Lp@40 在 10 个格点间实测器件上的最大误差：现状 A → DF（括号内 BF）"),
        (f"{pair('SRF', 'ii-OD_P', 'A', 'p90_rel', bs)} → {pair('SRF', 'ii-OD_P', 'F', 'p90_rel', bs)}",
         "SRF 整档留出 OD_P 的 p90 误差：现状 A → F（只换输入坐标，只改清单）"),
        (f"{fitted} 次 · {wall_min:.0f} min", f"GP 拟合次数 · 墙钟时间（{runs[0]['workers']} 个进程 × 每个 {d['threads_per_worker']} 线程）"),
    ]
    gap_img = _fig_offset_gap(T, bs, figs / f"{stem}_offset_gap.png")
    sections = [_findings(T, bs, ms, st, pc, gap_img), _recommend(T, bs, ms, st, pc)]
    method_list = "".join(f"<dt>{html.escape(k)}</dt><dd>{v}</dd>" for k, v in METHOD_TEXT)
    body = []
    for name in bs + ms:
        t = T[name]
        f = f"{t['anchor_ghz']:g}"
        protos = PROTOCOLS if t["targets"][f"Lp@{f}"]["points"] else PROTOCOLS[:3]
        refs = [q for q in ("Lp_lf", "Ls_lf", "k_lf") if q in t["targets"]]
        body.append(f"<h2 id='{name}'>{name}：{f} GHz 结果列</h2>")
        body.append(f"<p class='note'>{t['rows']} 行；{f} GHz 可用 {t['usable_anchor_rows']} 行（系统 SRF 高于 1.25 × {f} GHz 的行），"
                    f"有 SRF 的 {t['usable_srf_rows']} 行；无量纲输入 = <code>{t['feature_map']}</code>。"
                    + ("被测 5 个格点间器件 " + "、".join(t["held_for_iii"]) + " 不参加任何训练。" if t["held_for_iii"] else "本表没有格点间实测行，只做 (i)(ii)。")
                    + "</p>")
        for q in (f"Lp@{f}", f"Ls@{f}", f"k@{f}", "SRF"):
            body.append(f"<h3><code>{q}</code></h3>" + _method_table(t["targets"][q], protos))
        body.append("<h3>低频结果列（B、C、D 乘的那一部分）</h3>" + _ref_table(t, refs, protos))
        if t["held_for_iii"]:
            img = _fig_offlattice(t, name, figs / f"{stem}_{name}_offlattice.png")
            body.append(f"<h3>格点间实测：逐器件偏差</h3><img src='{img}' alt='{name} 格点间器件上各方法的偏差'>"
                        "<p class='note'>下表是图中的数字（实测 / 预测 − 1，%）；✗ = 实测落在该方法校准后的 2σ 区间之外。</p>" + _point_table(t))
    levels_img = _fig_levels({n: T[n] for n in bs + ms}, {n: f"Lp@{T[n]['anchor_ghz']:g}" for n in bs + ms},
                             ["A", "BF", "DF"], figs / f"{stem}_levels.png")
    ls_rows_id = [("{q}:A", "A：{q} 直接（现状）"), ("{L}_lf", "{L}_lf（B/C/D 的低频部分）"), ("{q}:ratio", "B：{q} / {L}_lf"),
                  ("{q}:fr", "C：等效谐振频率 f_r"), ("{q}:resid", "D：比值 ÷ 理想谐振抬高"), ("{q}:resid_meas", "D*：同上，实测 SRF"),
                  ("SRF:A", "SRF 直接（现状）"), ("SRF_p:A", "SRF_p（MIN 的一半）"), ("SRF_s:A", "SRF_s（MIN 的另一半）")]
    ls_rows_map = [("{q}:Afm", "F：{q} 直接"), ("{q}:ratiofm", "BF：{q} / {L}_lf"), ("{q}:resid_fm", "DF：比值 ÷ 理想谐振抬高"),
                   ("SRF:Afm", "SRF（F）"), ("k@{f}:A", "k@{f} 直接（现状）"), ("k_lf", "k_lf"), ("k@{f}:ratio", "k@{f} / k_lf（k 的 B）")]
    ls = []
    for name in bs + ms:
        t = T[name]
        f = f"{t['anchor_ghz']:g}"
        fill = {"q": f"Lp@{f}", "L": "Lp", "f": f}
        ls.append(f"<h3>{name}</h3>"
                  + _ls_table(t, [(k.format(**fill), v.format(**fill)) for k, v in ls_rows_id])
                  + _ls_table(t, [(k.format(**fill), v.format(**fill)) for k, v in ls_rows_map]))
    page_html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>单圈变压器锚定量建模对照</title>
<style>{CSS}</style></head><body>
<div class="wrap">
<header>
  <div class="eyebrow">ic-opt-modular · T16.2a · 变压器表 · 模型对照研究（不改产品代码）</div>
  <h1>40 GHz 结果列怎么建模：直接插值、拆成低频值 × 比值，还是借 SRF</h1>
  <p class="note">{INTRO_HTML}</p>
  <p><b>一句话：</b>把输入坐标换成“平均外径 + 外径比”，并把 Lp@40 拆成“低频电感 × 理想谐振抬高 × 残差”（DF），档与档之间的误差从几十个百分点降到约 3%；
  剩下的误差集中在表里没采过的“有偏移 + 两外径不等”一带，那要靠补行。SRF 只换坐标（改清单）就从 p90 80–95% 降到 1.5% 左右。</p>
</header>
<div class="kpis">{''.join(f"<div class='kpi'><div class='v'>{v}</div><div class='l'>{html.escape(label)}</div></div>" for v, label in kpis)}</div>
{''.join(sections)}
<h2 id="methods">方法（每种一句话）</h2>
<dl class="methods">{method_list}</dl>
<h2 id="protocols">怎么评</h2>
{PROTOCOL_HTML}
<h2 id="levels">整档留出：每一档的最大误差</h2>
<img src="{levels_img}" alt="整档留出时每档的最大误差">
<p class="note">每个点 = 把该档全部行拿出去、用其余档拟合后，这一档行里最大的相对误差。四张表都画了 Lp 列（bs 为 Lp@40，ms 为 Lp@60；ms 没有 DF）。</p>
{''.join(body)}
<h2 id="length-scales">模型认为结果变化有多快：长度尺度</h2>
{LS_INTRO_HTML}
{''.join(ls)}
<h2 id="failures">各方法的失败模式</h2>
{_failures(T, bs, ms, st, pc)}
<h2 id="location">位置与计算量</h2>
{_location_html(d)}
</div></body></html>
"""
    html_path.write_text(page_html, encoding="utf-8")
    print(html_path, f"{html_path.stat().st_size / 1e3:.0f} kB")


if __name__ == "__main__":
    main()
