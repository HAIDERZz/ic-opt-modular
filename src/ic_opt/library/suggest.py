"""Inverse queries: which geometries meet these targets, with margin, and can actually be built?

Targets are per quantity: ``{"min": v}``, ``{"max": v}``, a window ``{"min": a, "max": b}`` or
``{"target": v, "tol": rel}``; an optional objective ``"max:<quantity>"`` / ``"min:<quantity>"`` ranks the
survivors (without one they rank by closeness to the targets). The pipeline (the T13.0-verified procedure,
formerly em-opt's ``inverse.suggest``):

1. candidate pool: the measured rows themselves (exact values, zero-width intervals) plus scrambled Sobol over
   the achieved box, snapped to the manifest's ``steps``, one integer turns level per candidate, dims fixed
   within a level kept at their value. Measured designs that meet the targets are reported on their own list
   (certain, already simulated); the predicted candidates are the interpolated alternatives to sign off. When no
   predicted point of that pool meets the targets -- a target region too small for a pool spread over the whole box
   (N-50) -- a second pool takes its place: the grid ``lib.region`` searches for the same targets
   (``region.grid_pool``), each candidate marked with the pool it came from;
2. domain: every model involved must accept the candidate (guard criteria 1-3) and have a model there;
3. predict every quantity; criterion 4 (sigma / mu) drops candidates the model is unsure about, against each
   quantity's ceiling (the call's ``rel_sigma_max``, else the quantity's in library.yaml, else the default);
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
from the GHz it is fitted in back to Hz, settled rows, the domain + confidence gate, and the resonance gate),
``satisfy`` (robust: the interval inside every window; mean: the predicted value) and ``rank``.

The resonance gate (N-113, ``below_resonance``): an anchored column is answered only where the resonance predicted there
lies above its curve's ``srf_margin`` x f -- where a measured row has a value for it (``dataset._anchor_value``) -- whatever
the targets are. Targets and the objective get the robust form of this through the implied SRF target; the gate holds
every other anchored column a caller predicts (a ``lib.region`` trend, ``lib.densify``'s columns) to the rule as well. It
reads the resonance from each curve's model (``query.Model.resonance``, which ``Library.models`` attaches) and its floors
above the sweep from the caller (``srf_floors_for``).

A GP call's memory grows with (rows predicted) x (training rows), so a large batch is predicted in chunks
sized by bytes, not rows: ``predict_budget`` is a share of the machine's ``max_memory_gb`` (the library's
``limits``), ``rows_per_call`` the rows that fit in it for a given model.

A target, the objective or a trend may name a declared curve at any frequency (``Library.columns``: an extension column,
T18.1); ``resolve`` puts them in canonical form first, so the implied SRF follows the frequency asked. Each design carries
its ``footprint`` (``ic_opt.em.pcell.footprint``): a measured one its row's, a candidate the one ``build_check`` drew
(None when the build was not asked for).

Beyond the measured range (N-116): ``extrapolate`` (``domain.EXTRAPOLATE_FRACTION`` unless given; 0: as before) is the
fraction every model answers beyond its measured range along the outer diameters (``domain.DomainGuard.check``).
``predict_all`` lets such rows into the domain, widens their intervals (``query.widening``) and says per quantity how far
(``pred[q]["depth"]``, 0 inside the range) and by what factor (``pred[q]["widening"]``); ``pool`` extends the sampled box
along those dims by the table's allowance, so a design just outside the measured range can be drawn; each design whose
answers are extrapolated carries ``extrapolated`` (``extrapolation``: per dim the farthest of its models' labels) and, per
predicted quantity, ``widening``. The measured designs still come first.

Between the knots (N-127): a curve column between two knots of the library's frequency grid comes as a model built on the
knots' (``query.Library.models``, ``between``), and ``predict_all`` asks it for its answer with the resonance it predicts at
the rows anyway (``Between.answer``): a row it refuses (the knot above f at or past its resonance margin, an allowance it
cannot measure or above ``between.CAP``) is not ok, as one out of the domain is; a design carries per such column
``interpolated`` (the knots used, the allowance). ``exact=True`` (``suggest``) takes the column's own model as before.
"""

from __future__ import annotations

import dataclasses
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
from ic_opt.library import between, dataset, domain, gp, query
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


def resolve(library: query.Library, stratum: str, targets: list[Target], objective: tuple[str, str] | None,
            *extra: str) -> tuple[list[Target], tuple[str, str] | None, list[str]]:
    """The targets, the objective and ``extra`` names (a trend's quantity) as columns of the stratum (``Library.columns``:
    canonical names, extension columns measured), refused together when one is no column."""
    names = [t.quantity for t in targets] + ([objective[1]] if objective else []) + list(extra)
    columns = library.columns(stratum, names)
    goals = [dataclasses.replace(t, quantity=c) for t, c in zip(targets, columns)]
    obj = (objective[0], columns[len(targets)]) if objective else None
    return goals, obj, columns[len(targets) + (1 if objective else 0):]


def implied_srf(targets: list[Target], objective: tuple[str, str] | None, margin: float, columns: list[str] | tuple[str, ...] = ()) -> list[Target]:
    """Anchored quantities are only defined below the resonance: add SRF >= margin x the highest anchor. The system
    ``SRF`` when the stratum has it (what its anchored columns were cut by); else per drive (``query.DRIVE_SRF``), both
    drives for ``k``."""
    named = {t.quantity for t in targets}
    extra, highest = [], {}
    for q in [t.quantity for t in targets] + ([objective[1]] if objective else []):
        m = _ANCHOR.match(q)
        if m:
            drives = ("SRF",) if "SRF" in columns else query.DRIVE_SRF[m.group(1)]
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


def below_resonance(x: np.ndarray, models: dict[str, query.Model], pred: dict | None = None, *,
                    srf_floor: dict[str, np.ndarray] | None = None, exact: dict[str, np.ndarray] | None = None,
                    chunk_bytes: int | None = None) -> dict[str, np.ndarray]:
    """N-113: per anchored column among ``models`` -- a curve column whose model carries its resonance
    (``query.Model.resonance``) -- the rows of ``x`` where it may be answered: its own value is measured there (``exact``), or
    each of its resonance columns is settled above the sweep there (``srf_floor``) or lies above srf_margin x f, the
    measured value (``exact``) where there is one, else its model's mean in Hz. A row where a resonance has no mean (no
    model at its turns level) is left out. A resonance column ``pred`` holds already (``predict_all``'s own, settled rows
    applied) is not predicted again; another is predicted here, in calls of ``chunk_bytes`` as ``predict_all`` does."""
    n = len(x)
    floors, measured = srf_floor or {}, exact or {}
    resonances: dict[str, tuple[np.ndarray, np.ndarray]] = {}    # resonance column -> (settled above the sweep, value in Hz)
    out: dict[str, np.ndarray] = {}
    for q, m in models.items():
        limit = m.resonance
        if limit is None:
            continue
        allowed = np.ones(n, dtype=bool)
        for c in limit.columns:
            if c not in resonances:
                above, known = _settled(n, floors.get(c), measured.get(c))
                if pred is not None and c in pred:
                    value = pred[c]["value"]
                else:
                    r = limit.models[c]
                    mu, _ = _gp_predict(r.gp, x, None if chunk_bytes is None else rows_per_call(chunk_bytes, query.fit_rows(r)))
                    mu = mu * query.fit_unit(c)                     # the library fits SRF in GHz (query.fit_unit)
                    value = np.where(known, measured[c], mu) if known.any() else mu
                resonances[c] = (above, value)
            above, value = resonances[c]
            allowed &= above | (value > limit.gate_hz)                  # N-127: between knots, the knot above f's rule too
        if q in measured:                                            # a measured value of the column itself: the row has it
            allowed |= np.isfinite(measured[q])
        out[q] = allowed
    return out


def srf_floors_for(library: query.Library, stratum: str, x: np.ndarray, models: dict[str, query.Model]) -> dict[str, np.ndarray]:
    """``srf_floors`` per SRF column among ``models`` and per resonance column of an anchored one (``below_resonance``
    settles a row above the sweep with it)."""
    names = [q for q in models if q.startswith("SRF")] + [c for m in models.values() if m.resonance for c in m.resonance.columns]
    return {q: srf_floors(library, stratum, x, q) for q in dict.fromkeys(names)}


def in_domain(x: np.ndarray, models: dict[str, query.Model], *, srf_floor: dict[str, np.ndarray] | None = None,
              exact: dict[str, np.ndarray] | None = None, extrapolate: float = domain.EXTRAPOLATE_FRACTION) -> np.ndarray:
    """The domain half of ``predict_all``'s gate: every model's guard accepts the row (criteria 1-3, the outer diameters
    ``extrapolate`` beyond the measured range: N-116), or the value is settled without the model there. A caller predicting
    a large grid filters with it first."""
    ok = np.ones(len(x), dtype=bool)
    for q, m in models.items():
        above, known = _settled(len(x), (srf_floor or {}).get(q), (exact or {}).get(q))
        ok &= m.guard.inside(x, extrapolate=extrapolate) | above | known
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


def predict_all(x: np.ndarray, models: dict[str, query.Model], *, k: float = 2.0, rel_sigma_max: float | None = None,
                srf_floor: dict[str, np.ndarray] | None = None, exact: dict[str, np.ndarray] | None = None,
                check_domain: bool = True, chunk_bytes: int | None = None,
                extrapolate: float = domain.EXTRAPOLATE_FRACTION) -> tuple[np.ndarray, dict]:
    """``(ok, pred)`` for candidate rows ``x`` and every quantity in ``models``: ``ok`` is the domain (``in_domain``) and
    confidence gate (a finite value, sigma / mu within ``rel_sigma_max`` for every quantity, or when it is None within
    each model's own ``Model.rel_sigma_max``) and the resonance gate (``below_resonance``: an anchored column only where
    the resonance predicted there lies above its srf_margin x f, a row elsewhere not ok as one out of the domain is,
    whether or not SRF is among ``models``); ``pred[q]`` holds value, lo, hi (calibrated
    k-sigma), rel_sigma and sigma in SI units. One GP prediction per quantity: the bounds come from that (mu, sigma)
    as ``StratumGP.predict_bounds`` derives them, and SRF, fitted in GHz, is mapped back to Hz here and nowhere else.
    ``srf_floor[q]`` gives a per-candidate lower bound for an SRF known to lie above the sweep (NaN elsewhere; the
    library's callers give one for every resonance column too, ``srf_floors_for``);
    ``exact[q]`` gives measured values (NaN where not measured), used as zero-width intervals instead of predictions.
    ``chunk_bytes`` bounds each GP call's memory (``rows_per_call`` for the model's training rows; the library's callers
    pass ``predict_budget(library.limits)``); None predicts every row in one call. ``extrapolate`` (N-116): the guard's
    fraction beyond the measured range along the outer diameters; a row there that is no settled value gets its interval
    widened (``query.widening``), ``pred[q]["depth"]`` how far (0 inside the range) and ``pred[q]["widening"]`` the factor
    (1 inside), whether or not ``check_domain`` -- a caller that checked the domain itself passes the same fraction."""
    n = len(x)
    ok = in_domain(x, models, srf_floor=srf_floor, exact=exact, extrapolate=extrapolate) if check_domain else np.ones(n, dtype=bool)
    pred: dict[str, dict[str, np.ndarray]] = {}
    for q, m in models.items():
        if isinstance(m.gp, between.Between):                   # N-127: after the others (its resonance may be among them)
            continue
        mu, sigma = _gp_predict(m.gp, x, None if chunk_bytes is None else rows_per_call(chunk_bytes, query.fit_rows(m)))
        lo, hi = gp.prediction_bounds(mu, sigma, log_target=m.gp.log_target, k=k * m.gp.k_scale)
        depth = m.guard.depth(x) if extrapolate > 0 and m.guard is not None and n else np.zeros(n)
        wide = np.ones(n)
        far = depth > 0
        if far.any():                                           # N-116: beyond the measured range, the interval widened
            wide[far] = query.widening(q, depth[far])
            lo[far], hi[far] = gp.prediction_bounds(mu[far], sigma[far], log_target=m.gp.log_target, k=k * m.gp.k_scale * wide[far])
        scale = query.fit_unit(q)                               # the library fits SRF in GHz (query.fit_unit)
        mu, sigma, lo, hi = mu * scale, sigma * scale, lo * scale, hi * scale
        floor, measured = (srf_floor or {}).get(q), (exact or {}).get(q)
        above, known = _settled(n, floor, measured)
        if above.any():                                           # above the sweep: at least the stop, no finite upper bound
            mu[above], lo[above], hi[above], sigma[above] = floor[above], floor[above], np.inf, 0.0
        if known.any():                                           # measured: the value itself, zero-width interval
            mu[known] = lo[known] = hi[known] = measured[known]
            sigma[known] = 0.0
        settled = above | known
        ceiling = m.rel_sigma_max if rel_sigma_max is None else rel_sigma_max
        ok &= (np.isfinite(mu) | above) & (settled | domain.sigma_ok(mu, sigma, ceiling))
        pred[q] = {"value": mu, "lo": lo, "hi": hi, "rel_sigma": np.where(settled, 0.0, sigma / np.maximum(np.abs(mu), 1e-300)),
                   "sigma": sigma, "depth": np.where(settled, 0.0, depth), "widening": np.where(settled, 1.0, wide)}
    resonances = _between_all(x, models, pred, ok, k=k, rel_sigma_max=rel_sigma_max, srf_floor=srf_floor, exact=exact,
                              chunk_bytes=chunk_bytes, extrapolate=extrapolate)
    for allowed in below_resonance(x, models, {**resonances, **pred}, srf_floor=srf_floor, exact=exact, chunk_bytes=chunk_bytes).values():
        ok &= allowed
    return ok, pred


def _between_all(x: np.ndarray, models: dict[str, query.Model], pred: dict, ok: np.ndarray, *, k: float, rel_sigma_max: float | None,
                 srf_floor: dict[str, np.ndarray] | None, exact: dict[str, np.ndarray] | None, chunk_bytes: int | None,
                 extrapolate: float) -> dict[str, dict]:
    """``predict_all``'s part for the columns answered between knots (N-127): each one's ``Between.answer`` at the rows of
    ``x`` with the resonance predicted there -- its resonance columns' ``pred`` entries, or predicted here as ``predict_all``
    predicts any column (the settled rows applied) --, its interval widened beyond the measured range as a model's is
    (``query.widening`` by the farthest of its knots' depths), measured values of the column itself as zero-width intervals;
    ``ok`` (updated in place) drops the rows it refuses or is unsure of. ``pred`` gains each column's entry, with
    ``interpolated``: the ``core`` knots, the ``second_knot`` above, per row whether it was used (``second``), the
    ``allowance`` (NaN for a measured value) and ``Between.answer``'s ``verdict``. Returns the resonance columns predicted
    here (``below_resonance`` reads them)."""
    inner = {q: m for q, m in models.items() if isinstance(m.gp, between.Between)}
    if not inner:
        return {}
    n = len(x)
    wanted = {c: m.resonance.models[c] for m in inner.values() if m.resonance is not None for c in m.resonance.columns if c not in pred}
    extra = predict_all(x, wanted, k=k, srf_floor=srf_floor, exact=exact, check_domain=False, chunk_bytes=chunk_bytes,
                        extrapolate=extrapolate)[1] if wanted else {}
    known_res = {**extra, **pred}
    for q, m in inner.items():
        g: between.Between = m.gp
        value = low = settled = None
        if m.resonance is not None:
            floors = srf_floor or {}
            above = [np.isfinite(floors[c]) if c in floors else np.zeros(n, dtype=bool) for c in m.resonance.columns]
            value = np.min([np.where(a, np.inf, known_res[c]["value"]) for c, a in zip(m.resonance.columns, above)], axis=0)
            low = np.min([np.where(a, known_res[c]["value"], known_res[c]["lo"]) for c, a in zip(m.resonance.columns, above)], axis=0)
            settled = np.all(above, axis=0)
        depth = m.guard.depth(x) if extrapolate > 0 and n else np.zeros(n)
        wide = np.ones(n)
        far = depth > 0
        if far.any():
            wide[far] = query.widening(q, depth[far])
        got = g.answer(x, k * wide, resonance_hz=value, resonance_lo_hz=low, above=settled, extrapolate=extrapolate, chunk_bytes=chunk_bytes)
        mu, sigma, lo, hi = got["value"], got["sigma"], got["lo"], got["hi"]
        measured = (exact or {}).get(q)
        _above, known = _settled(n, None, measured)
        if known.any():                                           # measured: the value itself, zero-width interval
            mu, sigma, lo, hi = mu.copy(), sigma.copy(), lo.copy(), hi.copy()
            mu[known] = lo[known] = hi[known] = measured[known]
            sigma[known] = 0.0
        ceiling = m.rel_sigma_max if rel_sigma_max is None else rel_sigma_max
        with np.errstate(invalid="ignore"):
            ok &= known | (np.isfinite(mu) & (got["verdict"] == between.OK) & domain.sigma_ok(mu, sigma, ceiling))
        pred[q] = {"value": mu, "lo": lo, "hi": hi, "rel_sigma": np.where(known, 0.0, sigma / np.maximum(np.abs(mu), 1e-300)),
                   "sigma": sigma, "depth": np.where(known, 0.0, depth), "widening": np.where(known, 1.0, wide),
                   "interpolated": {"core": g.used(False), "second_knot": None if g.second is None else g.knots[g.second],
                                    "second": got["second"] & ~known, "allowance": np.where(known, np.nan, got["allowance"]),
                                    "verdict": np.where(known, between.OK, got["verdict"])}}
    return extra


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
          k: float = 2.0, rel_sigma_max: float | None = None, srf_floor: dict[str, np.ndarray] | None = None,
          check_domain: bool = True, exact: dict[str, np.ndarray] | None = None, chunk_bytes: int | None = None,
          extrapolate: float = domain.EXTRAPOLATE_FRACTION) -> dict:
    """Predict, gate and rank candidate rows ``x``: ``predict_all`` over the targets' and the objective's quantities,
    the robust ``satisfy``, ``rank``. ``models`` must cover every target and the objective; ``rel_sigma_max``,
    ``srf_floor``, ``exact``, ``chunk_bytes`` and ``extrapolate`` as in ``predict_all``."""
    names = sorted({t.quantity for t in targets} | ({objective[1]} if objective else set()))
    ok, pred = predict_all(x, {q: models[q] for q in names}, k=k, rel_sigma_max=rel_sigma_max, srf_floor=srf_floor, exact=exact,
                           check_domain=check_domain, chunk_bytes=chunk_bytes, extrapolate=extrapolate)
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


def pool(library: query.Library, stratum: str, n: int, seed: int = 0, box: dict[str, tuple[float, float]] | None = None, *,
         extrapolate: float = 0.0) -> np.ndarray:
    """``n`` scrambled-Sobol candidates over the achieved box, snapped to the manifest steps; one turns level each.
    ``box`` narrows the sampled range of some dims to ``(lo, hi)`` inside the achieved one: lo == hi fixes the dim, and
    for the turns dim only the levels inside it are drawn. A dim fixed within a level keeps that level's value, so a
    caller bounding such a dim drops the rows it leaves outside. ``extrapolate`` (N-116; ``lib.suggest`` and
    ``lib.region`` pass theirs) widens the box along the outer diameters by the table's allowance
    (``domain.allowances`` of the achieved range), before ``box`` narrows it."""
    ds = library.dataset(stratum)
    steps = library.manifest.strata[stratum].steps
    x = ds.matrix()
    cont = [i for i, d in enumerate(ds.dims) if d != ds.nt_dim]
    u = qmc.Sobol(d=len(ds.dims), scramble=True, seed=seed).random(n)
    out = np.empty((n, len(ds.dims)))
    lo, hi = x.min(axis=0), x.max(axis=0)
    if extrapolate > 0:
        below, above = domain.allowances(ds.dims, lo, hi, extrapolate)
        lo, hi = lo - below, hi + above
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
    """Build the geometry with the stratum's own device definition through the Pcell stage (config, generator, product DRC,
    ports), and measure its ``footprint`` from the GDS it drew (None, with ``footprint_why``, when the fixture cannot be told
    from the device: ``ic_opt.em.pcell.footprint``)."""
    from ic_opt.em.pcell import footprint as fp

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
        device = spec.device(g.device)
        try:
            box, why = fp.footprint(g.gds_path, profile=device.profile, generator=device.generator, plugin=device.plugin), None
        except fp.FootprintError as exc:
            box, why = None, str(exc)
        return {"built": True, "ports": sorted(p.signal for p in g.ports), "gds_sha256": g.gds_sha256, "footprint": box,
                "footprint_why": why}


def drawn(build: dict) -> tuple[dict, dict]:
    """``build_check``'s answer split into the build (what an entry's ``build`` shows) and the entry's footprint fields
    (``footprint_fields``), so that an answer has one place for the footprint."""
    shown = {k: v for k, v in build.items() if k not in ("footprint", "footprint_why")}
    return shown, footprint_fields(build.get("footprint"), build.get("footprint_why"))


def footprint_fields(box: dict | None, why: str | None) -> dict:
    """An answer's footprint: ``{"footprint": box}``, and ``footprint_why`` beside a None that has a reason."""
    return {"footprint": box, **({"footprint_why": why} if box is None and why else {})}


def extrapolation(models: dict[str, query.Model], pred: dict, i: int, params: dict, extrapolate: float) -> dict[str, dict]:
    """Design ``i``'s label (N-116): per outer-diameter dim beyond the measured range of a model that answered it there
    (``pred[q]["depth"]`` > 0: not a settled value), the guard's ``extrapolation`` -- side, beyond, allowance, measured
    range -- of the model that lies farthest from it; {} for a design inside every model's range."""
    out: dict[str, dict] = {}
    for q, m in models.items():
        if q in pred and pred[q].get("depth") is not None and pred[q]["depth"][i] > 0:
            for d, e in m.guard.extrapolation(params, extrapolate).items():
                if d not in out or e["beyond"] > out[d]["beyond"]:
                    out[d] = e
    return out


def predicted_entry(pred: dict, i: int, names: list[str]) -> dict:
    """A design's ``predicted``: value, lo, hi, rel_sigma per quantity, ``widening`` where its interval was widened beyond the
    measured range (N-116), and ``interpolated`` (the knots used, the allowance) where the column was answered between knots
    (N-127)."""
    out = {}
    for q in names:
        e = {key: float(pred[q][key][i]) for key in ("value", "lo", "hi", "rel_sigma")}
        if "depth" in pred[q] and pred[q]["depth"][i] > 0:
            e["widening"] = float(pred[q]["widening"][i])
        label = interpolated(pred[q], i)
        if label:
            e["interpolated"] = label
        out[q] = e
    return out


def interpolated(entry: dict, i: int) -> dict | None:
    """Row ``i``'s ``interpolated`` label of a ``predict_all`` entry -- the knots its value was interpolated over and the
    allowance its interval was widened by (N-127) -- or None: a column with its own model, or a measured value."""
    label = entry.get("interpolated")
    if label is None or not np.isfinite(label["allowance"][i]):
        return None
    return {"knots": label["core"] + ([label["second_knot"]] if label["second"][i] else []), "allowance": float(label["allowance"][i])}


def suggest(library: query.Library, stratum: str, targets: dict, objective: str | None = None, *, n: int = 5, pool_size: int = 8192,
            seed: int = 0, k: float = 2.0, rel_sigma_max: float | None = None, verify_build: bool = True,
            min_spacing: float = 0.05, extrapolate: float = domain.EXTRAPOLATE_FRACTION, exact: bool = False) -> dict:
    """The measured designs meeting ``targets`` and the predicted candidates that meet them with margin and build (module
    docstring). Each candidate's ``pool`` says where it was drawn: ``sobol``, the pool of step 1, or ``region_grid`` -- when
    no predicted point of that pool meets every target (a target region too small for 8192 points over the whole achieved
    box, N-50), a second pool: the grid ``lib.region`` searches for the same targets (``region.grid_pool``: its coarse pass
    with the stated windows ``region.RELAX`` wider, its grid over the survivors), scored the same way. The answer then
    describes that grid in ``region_grid`` and says so in a note; ``pool`` and ``satisfying`` stay the first pool's. A
    target or the objective on a column whose usable rows are too few for a model -- or the resonance an anchored target
    implies, if that one's are -- is refused with the reason before anything is fitted (``Library.require_models``, N-120).
    ``extrapolate`` (N-116): both pools reach that far beyond the measured range along the outer diameters, and a candidate
    there carries ``extrapolated`` (module docstring). A curve column between two knots of the library's frequency grid is
    answered from the knots' models (N-127) unless ``exact``; a note names each such column."""
    extrapolate = query.check_extrapolate(extrapolate)
    ds = library.dataset(stratum)
    stated, obj, _ = resolve(library, stratum, parse_targets(targets), parse_objective(objective))
    margin = min((r.srf_margin for r in library.manifest.strata[stratum].quantities.values()), default=1.25)
    goals = stated + implied_srf(stated, obj, margin, ds.columns)
    names = library.columns(stratum, sorted({t.quantity for t in goals} | ({obj[1]} if obj else set())))
    library.require_models(stratum, names)
    models = library.models(stratum, names, exact=exact)
    measured_x = ds.matrix()
    x = np.unique(np.round(np.vstack([measured_x, pool(library, stratum, pool_size, seed, extrapolate=extrapolate)]), 9), axis=0)
    index = {tuple(np.round(r, 9)): i for i, r in enumerate(measured_x)}
    row_of = np.array([index.get(tuple(r), -1) for r in x])
    exact = {q: np.array([np.nan if i < 0 or ds.rows[i].values.get(q) is None else ds.rows[i].values[q] for i in row_of]) for q in names}
    floors = srf_floors_for(library, stratum, x, models)
    for q, floor in floors.items():                           # a measured row with no resonance in its sweep: at least its stop
        for j, i in enumerate(row_of):
            if i >= 0 and ds.rows[i].values.get(q) is None:
                floor[j] = ds.rows[i].stop_hz
    budget = predict_budget(library.limits)
    with threadpool_limits(limits=query.blas_threads(library.limits), user_api="blas"):   # the pools' predictions, like a region's
        s = score(x, models, goals, obj, k=k, rel_sigma_max=rel_sigma_max, srf_floor=floors, exact=exact, chunk_bytes=budget,
                  extrapolate=extrapolate)
        measured_ranked = [i for i in s["ranked"] if row_of[i] >= 0]
        predicted_ranked = [i for i in s["ranked"] if row_of[i] < 0]
        second = None if predicted_ranked else _grid_pool(library, stratum, models, stated, goals, obj, set(index), seed=seed, k=k,
                                                          rel_sigma_max=rel_sigma_max, budget=budget, extrapolate=extrapolate)
    ranges = library.ranges(stratum)
    notes = library.notes + [f"added {t.quantity} >= {t.value / 1e9:g} GHz: anchored quantities need the resonance above {margin:g} x f0"
                             for t in goals[len(stated):]] + between.notes(models)

    def entry(xs: np.ndarray, pred: dict, i: int, **extra) -> dict:
        params = dict(zip(ds.dims, (float(v) for v in xs[i])))
        label = extrapolation({q: models[q] for q in names}, pred, i, params, extrapolate)
        return {"params": params, **extra, **({"extrapolated": label} if label else {}), "predicted": predicted_entry(pred, i, names),
                "nearest": [query._evidence(r, names[0], ds.dims) for r in query._nearest_rows(library, stratum, params, 3)]}

    shown = diversify(x, measured_ranked, ranges, ds.dims, keep=n, min_spacing=min_spacing)
    rows = [ds.rows[row_of[i]] for i in shown]
    boxes = library.footprints(stratum) if rows else {}
    measured = [entry(x, s["pred"], i, measured={"part": r.part, "obs_id": r.obs_id}, **footprint_fields(*boxes[(r.part, r.obs_id)]))
                for i, r in zip(shown, rows)]
    origin, cx, cs = ("sobol", x, s) if second is None else ("region_grid", second[0], second[1])
    ranked = predicted_ranked if second is None else second[1]["ranked"]
    candidates, rejected = [], 0
    for i in diversify(cx, ranked, ranges, ds.dims, keep=max(4 * n, 8), min_spacing=min_spacing):
        build = build_check(library, stratum, dict(zip(ds.dims, (float(v) for v in cx[i])))) if verify_build else {"built": None}
        if verify_build and not build["built"]:
            rejected += 1
            continue
        build, box = drawn(build) if verify_build else (build, {})       # a footprint only for a geometry the generator drew
        candidates.append(entry(cx, cs["pred"], i, pool=origin, build=build, **box))
        if len(candidates) == n:
            break
    if second is not None:
        notes.append(_second_pool_note(len(x) - len(measured_x), second[2]))
    if rejected:
        notes.append(f"{rejected} leading candidates failed the real build and were skipped")
    beyond = sum("extrapolated" in c for c in candidates)
    if beyond:
        notes.append(f"{beyond} of {len(candidates)} candidates lie beyond the measured range along the outer diameters, within "
                     f"extrapolate={extrapolate:g} of it: their answers are extrapolated and their intervals widened (extrapolated)")
    out = {"stratum": stratum, "targets": [t.__dict__ for t in goals], "objective": objective, "pool": len(x),
           "satisfying": int(s["ok"].sum()), "satisfying_measured": len(measured_ranked), "measured": measured,
           "candidates": candidates, "notes": notes}
    if second is not None:
        out["region_grid"] = second[2]
    return out


def _grid_pool(library: query.Library, stratum: str, models: dict[str, query.Model], stated: list[Target], goals: list[Target],
               obj: tuple[str, str] | None, rows: set[tuple], *, seed: int, k: float, rel_sigma_max: float | None,
               budget: int, extrapolate: float = domain.EXTRAPOLATE_FRACTION) -> tuple[np.ndarray, dict, dict]:
    """N-50: the second pool -- ``region.grid_pool``, the grid ``lib.region`` searches for these goals, without the points
    that are library rows (``rows``: their coordinates rounded to 1e-9; a row is a measured design, never a candidate) --
    scored as the first pool is (``score``; every point is inside every model's domain already). Returns the points, their
    score and the grid's description for the answer's ``region_grid`` (``satisfying``: the points meeting every target)."""
    from ic_opt.library import region  # region builds on this module

    gx, floors, grid = region.grid_pool(library, stratum, models, stated, goals, seed=seed, k=k, rel_sigma_max=rel_sigma_max,
                                        budget=budget, extrapolate=extrapolate)
    new = np.array([tuple(np.round(r, 9)) not in rows for r in gx], dtype=bool)
    gx, floors = gx[new], {q: f[new] for q, f in floors.items()}
    s = score(gx, models, goals, obj, k=k, rel_sigma_max=rel_sigma_max, srf_floor=floors, check_domain=False, chunk_bytes=budget,
              extrapolate=extrapolate)
    return gx, s, {**grid, "satisfying": int(s["ok"].sum())}


def _second_pool_note(sobol: int, grid: dict) -> str:
    """What the second pool was and what it found (``suggest``'s notes)."""
    head = f"no predicted point of the Sobol pool ({sobol} points) meets every target, so a second pool was drawn: the grid lib.region "
    wider = f"the stated windows {100 * grid['relax']:.4g}% wider"
    if not grid["survivors"]:
        return head + f"searches, whose coarse pass kept no candidate even with {wider}"
    found = (f"{grid['satisfying']} of them meet every target" if grid["satisfying"] else "none of them meets every target")
    return head + (f"searches over its coarse pass with {wider} ({grid['points']} grid points, {grid['in_domain']} inside every "
                   f"model's domain): {found}")
