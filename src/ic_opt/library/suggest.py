"""Inverse queries: which geometries meet these targets, with margin, and can actually be built?

Targets are per quantity: ``{"min": v}``, ``{"max": v}`` or ``{"target": v, "tol": rel}``; an optional
objective ``"max:<quantity>"`` / ``"min:<quantity>"`` ranks the survivors (without one they rank by
closeness to the targets). The pipeline (the T13.0-verified procedure, formerly em-opt's
``inverse.suggest``):

1. candidate pool: the measured rows themselves (exact values, zero-width intervals) plus scrambled Sobol over
   the achieved box, snapped to the manifest's ``steps``, one integer turns level per candidate, dims fixed
   within a level kept at their value. Measured designs that meet the targets are reported on their own list
   (certain, already simulated); the predicted candidates are the interpolated alternatives to sign off;
2. domain: every model involved must accept the candidate (guard criteria 1-3) and have a model there;
3. predict every quantity; criterion 4 (sigma / mu) drops candidates the model is unsure about;
4. conservative constraints on the calibrated k-sigma bounds: ``min`` needs the lower bound above,
   ``max`` the upper bound below, ``target`` the whole interval inside the window. An anchored quantity
   (``Lp@28``) adds ``SRF >= srf_margin x f0`` unless the targets already constrain SRF. SRF whose nearest
   measured rows mostly resonate above their sweep counts as at least that sweep's stop;
5. rank (objective bound, else closeness to targets then uncertainty), keep a minimum scaled spacing;
6. build the leaders with the real generator (the Pcell stage: config validation, geometry, product DRC
   audit, ports) until ``n`` pass.

``score`` is the reusable core (fitted models + candidate matrix in, ranks out) so a hold-out test can rank
known rows exactly as ``suggest`` ranks a Sobol pool.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from scipy.stats import qmc

from ic_opt.eval.stage import StageContext, StageFailure
from ic_opt.executor.local import LocalExecutor
from ic_opt.library import dataset, domain, query
from ic_opt.space import Point
from ic_opt.stages.em_chain import Pcell
from ic_opt.store import RunStore

_ANCHOR = re.compile(r"^(Lp|Qp|Ls|Qs|k)@([0-9.]+)$")


@dataclass(frozen=True)
class Target:
    quantity: str
    kind: str                                          # min | max | target
    value: float
    tol: float = 0.0

    def window(self) -> tuple[float, float]:
        if self.kind == "min":
            return self.value, np.inf
        if self.kind == "max":
            return -np.inf, self.value
        return self.value * (1 - self.tol), self.value * (1 + self.tol)


def parse_targets(spec: dict) -> list[Target]:
    out = []
    for name, rule in spec.items():
        keys = set(rule)
        if keys == {"min"}:
            out.append(Target(name, "min", float(rule["min"])))
        elif keys == {"max"}:
            out.append(Target(name, "max", float(rule["max"])))
        elif keys == {"target", "tol"}:
            if not 0 < float(rule["tol"]) < 1:
                raise ValueError(f"{name}: tol is relative, 0 < tol < 1")
            out.append(Target(name, "target", float(rule["target"]), float(rule["tol"])))
        else:
            raise ValueError(f"{name}: expected {{min}}, {{max}} or {{target, tol}}, got {sorted(keys)}")
    return out


def parse_objective(text: str | None) -> tuple[str, str] | None:
    if not text:
        return None
    sense, _, quantity = text.partition(":")
    if sense not in ("max", "min") or not quantity:
        raise ValueError(f"objective {text!r}: expected max:<quantity> or min:<quantity>")
    return sense, quantity


def implied_srf(targets: list[Target], objective: tuple[str, str] | None, margin: float, columns: list[str] | tuple[str, ...] = ()) -> list[Target]:
    """Anchored quantities are only defined below the resonance: add SRF >= margin x the highest anchor. The system
    ``SRF`` when the stratum has it (what its anchored columns were cut by); else per drive, both drives for ``k``."""
    named = {t.quantity for t in targets}
    extra, highest = [], {}
    for q in [t.quantity for t in targets] + ([objective[1]] if objective else []):
        m = _ANCHOR.match(q)
        if m:
            if "SRF" in columns:
                drives = ("SRF",)
            else:
                drives = {"Lp": ("SRF_p",), "Qp": ("SRF_p",), "Ls": ("SRF_s",), "Qs": ("SRF_s",), "k": ("SRF_p", "SRF_s")}[m.group(1)]
            for srf in drives:
                highest[srf] = max(highest.get(srf, 0.0), float(m.group(2)) * 1e9)
    for srf, f0 in highest.items():
        if srf not in named:
            extra.append(Target(srf, "min", margin * f0))
    return extra


def score(x: np.ndarray, models: dict[str, query.Model], targets: list[Target], objective: tuple[str, str] | None, *,
          k: float = 2.0, rel_sigma_max: float = domain.DEFAULT_SIGMA_REL_MAX, srf_floor: dict[str, np.ndarray] | None = None,
          check_domain: bool = True, exact: dict[str, np.ndarray] | None = None) -> dict:
    """Predict, gate and rank candidate rows ``x``. ``models`` must cover every target and the objective.
    ``srf_floor[q]`` gives a per-candidate lower bound for an SRF known to lie above the sweep (NaN elsewhere);
    ``exact[q]`` gives measured values (NaN where not measured), used as zero-width intervals instead of predictions."""
    n = len(x)
    ok = np.ones(n, dtype=bool)
    pred: dict[str, dict[str, np.ndarray]] = {}
    names = sorted({t.quantity for t in targets} | ({objective[1]} if objective else set()))
    for q in names:
        m = models[q]
        scale = 1e9 if q.startswith("SRF") else 1.0
        mu, sigma = m.gp.predict(x)
        lo, hi = m.gp.predict_bounds(x, k)
        mu, sigma, lo, hi = mu * scale, sigma * scale, lo * scale, hi * scale
        floor = (srf_floor or {}).get(q)
        above = np.zeros(n, dtype=bool) if floor is None else np.isfinite(floor)
        measured = (exact or {}).get(q)
        known_exact = np.zeros(n, dtype=bool) if measured is None else np.isfinite(measured)
        mu, sigma, lo, hi = mu.copy(), sigma.copy(), lo.copy(), hi.copy()
        if above.any():                                           # above the sweep: at least the stop, no finite upper bound
            mu[above], lo[above], hi[above], sigma[above] = floor[above], floor[above], np.inf, 0.0
        if known_exact.any():                                     # measured: the value itself, zero-width interval
            mu[known_exact] = lo[known_exact] = hi[known_exact] = measured[known_exact]
            sigma[known_exact] = 0.0
        settled = above | known_exact
        ok &= (np.isfinite(mu) | above) & (settled | domain.sigma_ok(mu, sigma, rel_sigma_max))
        if check_domain:
            ok &= m.guard.inside(x) | settled
        pred[q] = {"value": mu, "lo": lo, "hi": hi, "rel_sigma": np.where(settled, 0.0, sigma / np.maximum(np.abs(mu), 1e-300))}
    for t in targets:
        a, b = t.window()
        ok &= (pred[t.quantity]["lo"] >= a) & (pred[t.quantity]["hi"] <= b)
    if objective:
        sense, q = objective
        key = -pred[q]["lo"] if sense == "max" else pred[q]["hi"]            # conservative: the bound the objective worries about
        order = np.lexsort((pred[q]["rel_sigma"], key))
    else:                                                      # closeness to the target windows, then certainty
        dist = sum((np.abs(pred[t.quantity]["value"] / t.value - 1) for t in targets if t.kind == "target"), np.zeros(n))
        order = np.lexsort((sum((pred[t.quantity]["rel_sigma"] for t in targets), np.zeros(n)), dist))
    ranked = [int(i) for i in order if ok[i]]
    return {"ok": ok, "ranked": ranked, "pred": pred}


def srf_floors(library: query.Library, stratum: str, x: np.ndarray, quantity: str) -> np.ndarray:
    """Per candidate: the sweep stop when most of its 5 nearest measured rows had no resonance in their sweep, else NaN."""
    ds = library.dataset(stratum)
    rng = library.ranges(stratum)
    lo = np.array([rng[d][0] for d in ds.dims])
    span = np.array([rng[d][1] - rng[d][0] for d in ds.dims])
    _, idx = cKDTree((ds.matrix() - lo) / span).query((x - lo) / span, k=min(5, len(ds.rows)))
    idx = np.atleast_2d(idx)
    out = np.full(len(x), np.nan)
    for i, near in enumerate(idx):
        above = [ds.rows[j] for j in near if ds.rows[j].values.get(quantity) is None]
        if len(above) >= query.ABOVE_SWEEP_VOTES:
            out[i] = min(r.stop_hz for r in above)
    return out


def pool(library: query.Library, stratum: str, n: int, seed: int = 0) -> np.ndarray:
    """``n`` scrambled-Sobol candidates over the achieved box, snapped to the manifest steps; one turns level each."""
    ds = library.dataset(stratum)
    steps = library.manifest.strata[stratum].steps
    x = ds.matrix()
    cont = [i for i, d in enumerate(ds.dims) if d != ds.nt_dim]
    u = qmc.Sobol(d=len(ds.dims), scramble=True, seed=seed).random(n)
    out = np.empty((n, len(ds.dims)))
    lo, hi = x.min(axis=0), x.max(axis=0)
    for i in cont:
        out[:, i] = lo[i] + u[:, i] * (hi[i] - lo[i])
    if ds.nt_dim:
        j = ds.dims.index(ds.nt_dim)
        levels = np.array(sorted({round(v) for v in x[:, j]}))
        out[:, j] = levels[np.minimum((u[:, j] * len(levels)).astype(int), len(levels) - 1)]
        for level in levels:                                 # a dim fixed within a level stays at its value there
            rows = x[np.round(x[:, j]) == level]
            for i in cont:
                if np.ptp(rows[:, i]) <= 1e-9:
                    out[out[:, j] == level, i] = rows[0, i]
    for i, d in enumerate(ds.dims):
        if d in steps:
            out[:, i] = np.round(out[:, i] / steps[d]) * steps[d]
    return np.unique(np.round(out, 9), axis=0)


def diversify(x: np.ndarray, ranked: list[int], ranges: dict[str, tuple[float, float]], dims: list[str], keep: int,
              min_spacing: float = 0.05) -> list[int]:
    lo = np.array([ranges[d][0] for d in dims])
    span = np.array([ranges[d][1] - ranges[d][0] for d in dims])
    scaled = (x - lo) / span
    chosen: list[int] = []
    for i in ranked:
        if all(np.linalg.norm(scaled[i] - scaled[j]) >= min_spacing for j in chosen):
            chosen.append(i)
        if len(chosen) == keep:
            break
    return chosen


def build_check(library: query.Library, stratum: str, params: dict) -> dict:
    """Build the geometry with the stratum's own device definition through the Pcell stage (config, generator, product DRC, ports)."""
    part = library.manifest.strata[stratum].parts[0].store
    spec = dataset._spec(library.root / part)
    point = Point({d: f"{v:g}" if not float(v).is_integer() else str(int(v)) for d, v in params.items()}, "suggest")
    with tempfile.TemporaryDirectory() as tmp:
        store = RunStore(Path(tmp))
        work = store.root / "sims" / "obs_0001"
        work.mkdir(parents=True)
        ctx = StageContext(spec=spec, executor=LocalExecutor(store.root / "sims"), store=store, obs_id="obs_0001", workdir=work, remote_dir="r")
        try:
            geometry = Pcell(spec).run(point, ctx)
        except StageFailure as exc:
            return {"built": False, "why": "; ".join(exc.issues)}
        g = next(iter(geometry.devices.values()))
        return {"built": True, "ports": sorted(p.signal for p in g.ports), "gds_sha256": g.gds_sha256}


def suggest(library: query.Library, stratum: str, targets: dict, objective: str | None = None, *, n: int = 5, pool_size: int = 8192,
            seed: int = 0, k: float = 2.0, rel_sigma_max: float = domain.DEFAULT_SIGMA_REL_MAX, verify_build: bool = True,
            min_spacing: float = 0.05) -> dict:
    ds = library.dataset(stratum)
    goals = parse_targets(targets)
    obj = parse_objective(objective)
    margin = min((r.srf_margin for r in library.manifest.strata[stratum].quantities.values()), default=1.25)
    goals += implied_srf(goals, obj, margin, ds.columns)
    names = sorted({t.quantity for t in goals} | ({obj[1]} if obj else set()))
    unknown = [q for q in names if q not in ds.columns]
    if unknown:
        raise ValueError(f"{stratum} has no quantities {unknown}; columns {ds.columns}")
    models = {q: library.model(stratum, q) for q in names}
    measured_x = ds.matrix()
    x = np.unique(np.round(np.vstack([measured_x, pool(library, stratum, pool_size, seed)]), 9), axis=0)
    index = {tuple(np.round(r, 9)): i for i, r in enumerate(measured_x)}
    row_of = np.array([index.get(tuple(r), -1) for r in x])
    exact = {q: np.array([np.nan if i < 0 or ds.rows[i].values.get(q) is None else ds.rows[i].values[q] for i in row_of]) for q in names}
    floors = {q: srf_floors(library, stratum, x, q) for q in names if q.startswith("SRF")}
    for q, floor in floors.items():                           # a measured row with no resonance in its sweep: at least its stop
        for j, i in enumerate(row_of):
            if i >= 0 and ds.rows[i].values.get(q) is None:
                floor[j] = ds.rows[i].stop_hz
    s = score(x, models, goals, obj, k=k, rel_sigma_max=rel_sigma_max, srf_floor=floors, exact=exact)
    ranges = library.ranges(stratum)
    measured_ranked = [i for i in s["ranked"] if row_of[i] >= 0]
    predicted_ranked = [i for i in s["ranked"] if row_of[i] < 0]
    notes = [f"added {t.quantity} >= {t.value / 1e9:g} GHz: anchored quantities need the resonance above {margin:g} x f0"
             for t in goals if t not in parse_targets(targets)]

    def entry(i: int, **extra) -> dict:
        params = dict(zip(ds.dims, (float(v) for v in x[i])))
        return {"params": params, **extra,
                "predicted": {q: {k2: float(s["pred"][q][k2][i]) for k2 in ("value", "lo", "hi", "rel_sigma")} for q in names},
                "nearest": [query._evidence(r, names[0], ds.dims) for r in query._nearest_rows(library, stratum, params, 3)]}

    measured = [entry(i, measured={"part": ds.rows[row_of[i]].part, "obs_id": ds.rows[row_of[i]].obs_id})
                for i in diversify(x, measured_ranked, ranges, ds.dims, keep=n, min_spacing=min_spacing)]
    candidates, rejected = [], 0
    for i in diversify(x, predicted_ranked, ranges, ds.dims, keep=max(4 * n, 8), min_spacing=min_spacing):
        build = build_check(library, stratum, dict(zip(ds.dims, (float(v) for v in x[i])))) if verify_build else {"built": None}
        if verify_build and not build["built"]:
            rejected += 1
            continue
        candidates.append(entry(i, build=build))
        if len(candidates) == n:
            break
    if rejected:
        notes.append(f"{rejected} leading candidates failed the real build and were skipped")
    return {"stratum": stratum, "targets": [t.__dict__ for t in goals], "objective": objective, "pool": len(x),
            "satisfying": int(s["ok"].sum()), "satisfying_measured": len(measured_ranked), "measured": measured,
            "candidates": candidates, "notes": notes}
