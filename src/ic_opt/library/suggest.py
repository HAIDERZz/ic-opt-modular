"""Inverse queries: which geometries meet these targets, with margin, and can actually be built?

Targets are per quantity: ``{"min": v}``, ``{"max": v}``, a window ``{"min": a, "max": b}`` or
``{"target": v, "tol": rel}``; an optional objective ``"max:<quantity>"`` / ``"min:<quantity>"`` ranks the
survivors (without one they rank by closeness to the targets). The pipeline (the T13.0-verified procedure,
formerly em-opt's ``inverse.suggest``):

1. candidate pool: the measured rows themselves (exact values, zero-width intervals) plus scrambled Sobol over
   the achieved box, snapped to the manifest's ``steps``, one integer turns level per candidate, dims fixed
   within a level kept at their value. Measured designs that meet the targets are reported on their own list
   (certain, already simulated); the predicted candidates are the interpolated alternatives to sign off;
2. domain: every model involved must accept the candidate (guard criteria 1-3) and have a model there;
3. predict every quantity; criterion 4 (sigma / mu) drops candidates the model is unsure about;
4. conservative constraints on the calibrated k-sigma bounds: ``min`` needs the lower bound above,
   ``max`` the upper bound below, ``target`` and a window the whole interval inside. An anchored quantity
   (``Lp@28``) adds ``SRF >= srf_margin x f0`` unless the targets already constrain SRF. SRF whose nearest
   measured rows mostly resonate above their sweep counts as at least that sweep's stop;
5. rank (objective bound, else closeness to targets then uncertainty), keep a minimum scaled spacing;
6. build the leaders with the real generator (the Pcell stage: config validation, geometry, product DRC
   audit, ports) until ``n`` pass.

``score`` is the reusable core (fitted models + candidate matrix in, ranks out) so a hold-out test can rank
known rows exactly as ``suggest`` ranks a Sobol pool. It is three parts ``lib.region`` shares:
``predict_all`` (the one prediction path: one GP call per quantity, the bounds derived from it, SRF mapped
from the GHz it is fitted in back to Hz, settled rows, the domain + confidence gate), ``satisfy`` (robust:
the interval inside every window; mean: the predicted value) and ``rank``.

A GP call's memory grows with (rows predicted) x (training rows), so a large batch is predicted in chunks
sized by bytes, not rows: ``predict_budget`` is a share of the machine's ``max_memory_gb`` (the library's
``limits``), ``rows_per_call`` the rows that fit in it for a given model.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from scipy.stats import qmc
from threadpoolctl import threadpool_limits

from ic_opt.eval.stage import StageContext, StageFailure
from ic_opt.executor.local import LocalExecutor
from ic_opt.library import dataset, domain, gp, query
from ic_opt.site import HostLimits
from ic_opt.space import Point
from ic_opt.stages.em_chain import Pcell
from ic_opt.store import RunStore

_ANCHOR = re.compile(r"^(Lp|Qp|Ls|Qs|k)@([0-9.]+)$")
LEVELS = ("robust", "mean")                          # satisfy: the calibrated interval, or the predicted value, inside every window
PREDICT_MEMORY_SHARE = 0.10                          # of the machine's max_memory_gb: what one GP call may hold by default
PREDICT_COPIES = 7                                   # (rows x training rows) float64 arrays alive at once in a GP call (rows_per_call)


@dataclass(frozen=True)
class Target:
    quantity: str
    kind: str                                          # min | max | target | window
    value: float                                       # the bound, the target value, or a window's lower end
    tol: float = 0.0                                   # target: relative half-width
    upper: float = float("inf")                        # window: the upper end

    def window(self) -> tuple[float, float]:
        if self.kind == "min":
            return self.value, np.inf
        if self.kind == "max":
            return -np.inf, self.value
        if self.kind == "window":
            return self.value, self.upper
        return self.value * (1 - self.tol), self.value * (1 + self.tol)


def parse_targets(spec: dict) -> list[Target]:
    out = []
    for name, rule in spec.items():
        keys = set(rule)
        if keys == {"min"}:
            out.append(Target(name, "min", float(rule["min"])))
        elif keys == {"max"}:
            out.append(Target(name, "max", float(rule["max"])))
        elif keys == {"min", "max"}:
            lo, hi = float(rule["min"]), float(rule["max"])
            if not lo < hi:
                raise ValueError(f"{name}: a window needs min < max, got {lo:g} and {hi:g}")
            out.append(Target(name, "window", lo, upper=hi))
        elif keys == {"target", "tol"}:
            if not 0 < float(rule["tol"]) < 1:
                raise ValueError(f"{name}: tol is relative, 0 < tol < 1")
            out.append(Target(name, "target", float(rule["target"]), float(rule["tol"])))
        else:
            raise ValueError(f"{name}: expected {{min}}, {{max}}, {{min, max}} or {{target, tol}}, got {sorted(keys)}")
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


def _settled(n: int, floor: np.ndarray | None, measured: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
    """(above the sweep, measured): the rows whose value is known without the model."""
    above = np.zeros(n, dtype=bool) if floor is None else np.isfinite(floor)
    known = np.zeros(n, dtype=bool) if measured is None else np.isfinite(measured)
    return above, known


def in_domain(x: np.ndarray, models: dict[str, query.Model], *, srf_floor: dict[str, np.ndarray] | None = None,
              exact: dict[str, np.ndarray] | None = None) -> np.ndarray:
    """The domain half of ``predict_all``'s gate: every model's guard accepts the row (criteria 1-3), or the value is
    settled without the model there. A caller predicting a large grid filters with it first."""
    ok = np.ones(len(x), dtype=bool)
    for q, m in models.items():
        above, known = _settled(len(x), (srf_floor or {}).get(q), (exact or {}).get(q))
        ok &= m.guard.inside(x) | above | known
    return ok


def predict_budget(limits: HostLimits) -> int:
    """Bytes one GP call may hold by default: PREDICT_MEMORY_SHARE of the machine's max_memory_gb (``Library.limits``,
    site.yaml's hosts.local unless the library was given its own)."""
    return int(limits.max_memory_gb * PREDICT_MEMORY_SHARE * query.BYTES_PER_GB)


def rows_per_call(budget_bytes: int, n_train: int) -> int:
    """Rows per GP call within ``budget_bytes`` for a model fitted on ``n_train`` rows, at least one. Predicting m rows with
    sigma keeps PREDICT_COPIES float64 arrays of m x n_train alive at once: the Matern distances and their polynomial and
    exponential terms, the amplitude and noise blocks and their sum, the triangular solve behind sigma (tracemalloc
    measured 6.0 of them on scikit-learn 1.3; the seventh is margin). A per_nt model predicts each turns level with that
    level's rows, fewer than ``n_train``: the chunk is conservative there."""
    return max(1, int(budget_bytes) // (np.dtype(np.float64).itemsize * max(1, n_train) * PREDICT_COPIES))


def _gp_predict(model: gp.StratumGP, x: np.ndarray, chunk_rows: int | None) -> tuple[np.ndarray, np.ndarray]:
    """``model.predict`` in calls of at most ``chunk_rows`` rows (None: one call; a pool is usually one chunk, a region grid many)."""
    if not len(x):
        return np.empty(0), np.empty(0)
    if chunk_rows is None or len(x) <= chunk_rows:
        return model.predict(x)
    parts = [model.predict(x[i:i + chunk_rows]) for i in range(0, len(x), chunk_rows)]
    return np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts])


def predict_all(x: np.ndarray, models: dict[str, query.Model], *, k: float = 2.0, rel_sigma_max: float = domain.DEFAULT_SIGMA_REL_MAX,
                srf_floor: dict[str, np.ndarray] | None = None, exact: dict[str, np.ndarray] | None = None,
                check_domain: bool = True, chunk_bytes: int | None = None) -> tuple[np.ndarray, dict]:
    """``(ok, pred)`` for candidate rows ``x`` and every quantity in ``models``: ``ok`` is the domain (``in_domain``) and
    confidence gate (a finite value, sigma / mu within ``rel_sigma_max``); ``pred[q]`` holds value, lo, hi (calibrated
    k-sigma), rel_sigma and sigma in SI units. One GP prediction per quantity: the bounds come from that (mu, sigma)
    as ``StratumGP.predict_bounds`` derives them, and SRF, fitted in GHz, is mapped back to Hz here and nowhere else.
    ``srf_floor[q]`` gives a per-candidate lower bound for an SRF known to lie above the sweep (NaN elsewhere);
    ``exact[q]`` gives measured values (NaN where not measured), used as zero-width intervals instead of predictions.
    ``chunk_bytes`` bounds each GP call's memory (``rows_per_call`` for the model's training rows; the library's callers
    pass ``predict_budget(library.limits)``); None predicts every row in one call."""
    n = len(x)
    ok = in_domain(x, models, srf_floor=srf_floor, exact=exact) if check_domain else np.ones(n, dtype=bool)
    pred: dict[str, dict[str, np.ndarray]] = {}
    for q, m in models.items():
        mu, sigma = _gp_predict(m.gp, x, None if chunk_bytes is None else rows_per_call(chunk_bytes, len(m.rows)))
        lo, hi = gp.prediction_bounds(mu, sigma, log_target=m.gp.log_target, k=k * m.gp.k_scale)
        scale = 1e9 if q.startswith("SRF") else 1.0            # the library fits SRF in GHz (Library.model)
        mu, sigma, lo, hi = mu * scale, sigma * scale, lo * scale, hi * scale
        floor, measured = (srf_floor or {}).get(q), (exact or {}).get(q)
        above, known = _settled(n, floor, measured)
        if above.any():                                           # above the sweep: at least the stop, no finite upper bound
            mu[above], lo[above], hi[above], sigma[above] = floor[above], floor[above], np.inf, 0.0
        if known.any():                                           # measured: the value itself, zero-width interval
            mu[known] = lo[known] = hi[known] = measured[known]
            sigma[known] = 0.0
        settled = above | known
        ok &= (np.isfinite(mu) | above) & (settled | domain.sigma_ok(mu, sigma, rel_sigma_max))
        pred[q] = {"value": mu, "lo": lo, "hi": hi, "rel_sigma": np.where(settled, 0.0, sigma / np.maximum(np.abs(mu), 1e-300)),
                   "sigma": sigma}
    return ok, pred


def satisfy(pred: dict, targets: list[Target], level: str = "robust") -> np.ndarray:
    """Rows meeting every target: ``robust`` -- the calibrated interval lies inside each window (``score``'s test);
    ``mean`` -- the predicted value does."""
    if level not in LEVELS:
        raise ValueError(f"level {level!r}: expected one of {LEVELS}")
    ok = np.ones(len(next(iter(pred.values()))["value"]) if pred else 0, dtype=bool)
    for t in targets:
        a, b = t.window()
        p = pred[t.quantity]
        lo, hi = (p["lo"], p["hi"]) if level == "robust" else (p["value"], p["value"])
        ok &= (lo >= a) & (hi <= b)
    return ok


def rank(pred: dict, targets: list[Target], objective: tuple[str, str] | None, ok: np.ndarray) -> list[int]:
    """The ``ok`` rows best first: the objective's conservative bound, else closeness to the target values (a window's
    midpoint), each then certainty."""
    n = len(ok)
    if objective:
        sense, q = objective
        key = -pred[q]["lo"] if sense == "max" else pred[q]["hi"]            # conservative: the bound the objective worries about
        order = np.lexsort((pred[q]["rel_sigma"], key))
    else:                                                      # closeness to the target windows, then certainty
        centres = [(t.quantity, t.value if t.kind == "target" else (t.value + t.upper) / 2) for t in targets if t.kind in ("target", "window")]
        dist = sum((np.abs(pred[q]["value"] / c - 1) for q, c in centres), np.zeros(n))
        order = np.lexsort((sum((pred[t.quantity]["rel_sigma"] for t in targets), np.zeros(n)), dist))
    return [int(i) for i in order if ok[i]]


def score(x: np.ndarray, models: dict[str, query.Model], targets: list[Target], objective: tuple[str, str] | None, *,
          k: float = 2.0, rel_sigma_max: float = domain.DEFAULT_SIGMA_REL_MAX, srf_floor: dict[str, np.ndarray] | None = None,
          check_domain: bool = True, exact: dict[str, np.ndarray] | None = None, chunk_bytes: int | None = None) -> dict:
    """Predict, gate and rank candidate rows ``x``: ``predict_all`` over the targets' and the objective's quantities,
    the robust ``satisfy``, ``rank``. ``models`` must cover every target and the objective; ``srf_floor``, ``exact`` and
    ``chunk_bytes`` as in ``predict_all``."""
    names = sorted({t.quantity for t in targets} | ({objective[1]} if objective else set()))
    ok, pred = predict_all(x, {q: models[q] for q in names}, k=k, rel_sigma_max=rel_sigma_max, srf_floor=srf_floor, exact=exact,
                           check_domain=check_domain, chunk_bytes=chunk_bytes)
    if targets:
        ok &= satisfy(pred, targets, "robust")
    return {"ok": ok, "ranked": rank(pred, targets, objective, ok), "pred": pred}


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


def pool(library: query.Library, stratum: str, n: int, seed: int = 0, box: dict[str, tuple[float, float]] | None = None) -> np.ndarray:
    """``n`` scrambled-Sobol candidates over the achieved box, snapped to the manifest steps; one turns level each.
    ``box`` narrows the sampled range of some dims to ``(lo, hi)`` inside the achieved one: lo == hi fixes the dim, and
    for the turns dim only the levels inside it are drawn. A dim fixed within a level keeps that level's value, so a
    caller bounding such a dim drops the rows it leaves outside."""
    ds = library.dataset(stratum)
    steps = library.manifest.strata[stratum].steps
    x = ds.matrix()
    cont = [i for i, d in enumerate(ds.dims) if d != ds.nt_dim]
    u = qmc.Sobol(d=len(ds.dims), scramble=True, seed=seed).random(n)
    out = np.empty((n, len(ds.dims)))
    lo, hi = x.min(axis=0), x.max(axis=0)
    for d, (a, b) in (box or {}).items():
        lo[ds.dims.index(d)], hi[ds.dims.index(d)] = a, b
    for i in cont:
        out[:, i] = lo[i] + u[:, i] * (hi[i] - lo[i])
    if ds.nt_dim:
        j = ds.dims.index(ds.nt_dim)
        levels = np.array(sorted({round(v) for v in x[:, j] if lo[j] - 1e-9 <= v <= hi[j] + 1e-9}))
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
    with threadpool_limits(limits=query.blas_threads(library.limits), user_api="blas"):   # the pool's prediction, like a region's
        s = score(x, models, goals, obj, k=k, rel_sigma_max=rel_sigma_max, srf_floor=floors, exact=exact,
                  chunk_bytes=predict_budget(library.limits))
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
