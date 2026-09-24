"""Where to simulate next: the geometries whose simulation lowers the models' uncertainty the most (``lib.densify``).

``lib.suggest`` and ``lib.region`` answer target windows. Densifying a library is a different question, independent
of any target: for one stratum and some of its quantities (default: every column), which ``n`` new geometries would
leave the models least unsure over the sampled domain, or over the part of it ``bounds`` keeps? The answer is a
candidate list ``lib_signoff candidates=`` reads (``{"candidates": [{"params": {...}}, ...]}``); simulated and
adopted, the rows make the next dataset build, and the models refitted on it are surer there.

1. models: ``Library.models`` (the uncached ones fitted in parallel processes, as ``lib.region`` fits them);
2. candidate pool: ``suggest.pool`` (scrambled Sobol over the achieved box, snapped to the manifest ``steps``, one
   integer turns level each, a dim fixed within a level kept at its value), minus the points that are measured rows
   (``Dataset.find``'s rule: the same coordinates after rounding to 1e-9), inside every model's domain. ``bounds``
   narrows the box first, per dim a window ``{"min": a, "max": b}`` (clipped to the achieved range; one side may be
   left out) or a fixed value; a pool row left outside them (snapped past a window's edge, or held by its turns level
   at another value) is dropped. An SRF whose nearest measured rows mostly resonate above their sweep is settled
   there, as ``lib.query`` answers it (``above_sweep``): such a point is in that SRF's domain without its model, its
   sigma does not count and a measurement there does not inform the SRF model (no resonance to fit);
3. every model's RAW posterior sigma (``StratumGP.predict(floor=False)``): the sigma floor is the held-out median
   error everywhere, flat, and says nothing about where the model is unsure. Relative: for a log target (every
   positive quantity) the log-space sigma, else sigma / |mu|;
4. score, the largest over the quantities of sigma_rel_q divided by the quantity's norm: ``ceiling`` (the default)
   divides by the quantity's confidence ceiling (the call's ``rel_sigma_max``, else the quantity's in library.yaml,
   else the default) -- how far above the sigma the library calls usable for it, so the picks go where the library
   cannot answer yet; ``typical`` divides by the
   quantity's held-out median relative error from its calibration (sigma_rel itself without one) -- how many typical
   errors the model may be off, which lets a quantity with a tiny typical error lead the ranking even where its sigma
   is already well below the ceiling;
5. selection: the ``top`` best-scoring candidates, at most as many as the memory budget holds (``cov_rows``), get
   each model's posterior covariance among them (``StratumGP.posterior_cov``, in the fitted space; a per_nt model's
   turns levels do not covary). Then ``n`` times: pick the best score and, for every model the pick informs, update
   every candidate's variance exactly -- ``var_i -= cov_ij**2 / var_j`` with the covariance conditioned on the picks
   before (a pivoted Cholesky factorisation of the picks' covariance) -- and rescore. A GP's posterior variance does
   not depend on the measured values, so with the hyperparameters fixed this is what measuring the picks would
   leave: the next pick goes where uncertainty remains, not next to the previous one;
6. ``after``: every pool candidate's sigma once all picks are measured, conditioned on them with the factorisation
   the selection built (``before`` and ``after`` describe the same pool).

The update is exact for the fitted hyperparameters, in the model's own units: a pick is conditioned on with the
kernel's white noise, and the regressor's diagonal jitter (scikit-learn's ``alpha``, 1e-10 of the normalised
variance, which a refit also adds to each new row) is left out -- it shows only next to a pick, where the sigma left
is at the noise floor. A pick left with less than RESOLVED of a model's prior variance there updates nothing (its
rounding would be amplified). After sign-off the library refits: the hyperparameters are optimised again and the
targets renormalised, so the real reduction differs from ``after``, qualitatively the same.

The work is sized by the library's ``limits`` like ``lib.region``: the uncached models are fitted by
``Library.models``, predictions and covariances run with ``query.blas_threads`` BLAS threads within
``suggest.predict_budget`` bytes per call.
"""

from __future__ import annotations

import itertools
import math
import numbers
import time
from dataclasses import dataclass, field

import numpy as np
from scipy.linalg import solve_triangular
from scipy.spatial import cKDTree
from threadpoolctl import threadpool_limits

from ic_opt.library import dataset, gp, query, suggest
from ic_opt.site import EnvelopeError

SCORES = {"ceiling": "max_q sigma_rel_q / rel_sigma_max_q", "typical": "max_q sigma_rel_q / median_rel_q"}
DEFAULT_SCORE = "ceiling"
SELECTION = "greedy exact posterior update"
AFTER = "the pool's sigma once every pick is measured, exact for the fitted hyperparameters"
COV_COPIES = 5                                       # M x M float64 arrays alive while one model's covariance is computed (cov_rows)
AFTER_CHUNK = 256                                    # pool rows per covariance call of the after pass, besides the picks
RESOLVED = 1e-8                                      # a pick left with less of a model's prior variance teaches it nothing: no update
                                                     # (below it the posterior's own rounding is of that order, and would be amplified)
NEAREST = 3


@dataclass
class Selection:
    """What ``select`` found on a candidate matrix: the picks (rows of it, in the order picked) and every quantity's
    relative sigma per row before and after them."""

    picks: list[int]
    score: list[float]                                # each pick's score when it was picked (given the picks before it)
    before: dict[str, np.ndarray]                     # raw relative sigma per row; NaN where the quantity does not count
    after: dict[str, np.ndarray]                      # the same once every pick is measured
    at_pick: dict[str, list[float | None]]            # each pick's relative sigma given the picks before it
    top: int                                          # rows the covariances were computed among
    capped: bool                                      # top was lowered to fit the memory budget
    stopped: bool                                     # fewer picks than asked: every other candidate was already resolved
    seconds: dict[str, float] = field(default_factory=dict)


def cov_rows(budget_bytes: int, models: int, n_train: int) -> int:
    """The most rows M whose posterior covariances fit ``budget_bytes``: the selection keeps one M x M float64 matrix per
    model, and computing one more holds COV_COPIES of that size at once (its own result, the prior kernel, V^T V and the
    rescaled copy scikit-learn makes: tracemalloc measured 3.5 on scikit-learn 1.3; one more for a per_nt model's
    assembly, the rest margin) plus the PREDICT_COPIES arrays of M x n_train of any prediction (``suggest.rows_per_call``)."""
    a = 8 * (max(1, models) - 1 + COV_COPIES)
    b = 8 * suggest.PREDICT_COPIES * max(1, n_train)
    m = int((-b + math.sqrt(b * b + 4 * a * max(0, budget_bytes))) / (2 * a))
    while m > 0 and a * m * m + b * m > budget_bytes:      # the square root's rounding
        m -= 1
    return m


def select(x: np.ndarray, models: dict[str, gp.StratumGP], n: int, *, norm: dict[str, float] | None = None,
           informs: dict[str, np.ndarray] | None = None, top: int = 4000, budget: int | None = None, n_train: int = 1) -> Selection:
    """Pick ``n`` rows of the candidate matrix ``x`` whose measurement lowers the models' uncertainty most (steps 3-6 of
    the module docstring). ``norm[q]`` divides quantity q's relative sigma in the score (the held-out median relative
    error; default 1); ``informs[q]`` marks the rows where q counts (default: all): elsewhere q neither scores nor
    counts in the statistics, and a pick there does not update its model. ``budget`` bounds each GP call's bytes and
    caps ``top`` (``cov_rows`` for the models' largest training set ``n_train``); None: no bound."""
    marks = [time.perf_counter()]
    x = np.asarray(x, dtype=float)
    names = list(models)
    norm = {q: float((norm or {}).get(q) or 1.0) for q in names}
    given = informs or {}
    informs = {q: (np.ones(len(x), dtype=bool) if given.get(q) is None else np.asarray(given[q], dtype=bool)) & m.available(x)
               for q, m in models.items()}
    if not len(x):
        empty = {q: np.empty(0) for q in names}
        return Selection([], [], empty, dict(empty), {q: [] for q in names}, 0, False, False, {"predict": 0.0, "select": 0.0, "after": 0.0})
    chunk = None if budget is None else suggest.rows_per_call(budget, n_train)
    weight, before = {}, {}                               # weight: relative variance per fitted-space variance (1 for a log target)
    for q, m in models.items():
        mu, sigma = _raw(m, x, chunk)
        weight[q] = np.ones(len(x)) if m.log_target else 1.0 / np.maximum(mu * mu, 1e-300)
        before[q] = np.where(informs[q], sigma / np.maximum(np.abs(mu), 1e-300), np.nan)
    marks.append(time.perf_counter())
    capped_at = len(x) if budget is None else cov_rows(budget, len(names), n_train)
    size = min(top, len(x), capped_at)
    if size < min(n, len(x)):
        raise EnvelopeError(f"the covariances of {n} candidates for {len(names)} models do not fit the prediction budget of "
                            f"{budget / query.BYTES_PER_GB:.3g} GB (it holds {capped_at}): raise max_memory_gb or lower n")
    initial = _score({q: before[q] for q in names}, norm)
    rows = np.argsort(-initial, kind="stable")[:size]     # the top candidates, best first
    cov, prior = {}, {}
    for q, m in models.items():
        _mu, c = m.posterior_cov(x[rows])
        dead = ~informs[q][rows]                          # rows where q does not count: no variance, no covariance
        c[dead, :] = 0.0
        c[:, dead] = 0.0
        cov[q], prior[q] = c, np.maximum(np.diagonal(c).copy(), 0.0)
    var = {q: prior[q].copy() for q in names}
    factor = {q: np.empty((min(n, size), size)) for q in names}   # the update vectors u_l of each model, row by row
    used: dict[str, list[int]] = {q: [] for q in names}   # which picks updated each model (positions in picks)
    picks, scores, at_pick = [], [], {q: [] for q in names}
    alive = np.ones(size, dtype=bool)
    stopped = False
    for _ in range(min(n, size)):
        s = _score({q: np.sqrt(var[q] * weight[q][rows]) for q in names}, norm)
        s[~alive] = -np.inf
        j = int(np.argmax(s))
        if not s[j] > 0:
            stopped = True
            break
        scores.append(float(s[j]))
        for q in names:
            at_pick[q].append(float(np.sqrt(var[q][j] * weight[q][rows[j]])) if informs[q][rows[j]] else None)
            k = len(used[q])
            col = cov[q][:, j] - factor[q][:k].T @ factor[q][:k, j]
            if not col[j] > RESOLVED * prior[q][j]:        # not informed here, or nothing left to learn
                continue
            u = col / math.sqrt(col[j])
            var[q] = np.maximum(var[q] - u * u, 0.0)       # var_i -= cov_ij**2 / var_j; rounding never leaves it negative
            factor[q][k] = u
            used[q].append(len(picks))
        alive[j] = False
        picks.append(j)
    marks.append(time.perf_counter())
    del cov
    after = {}
    for q, m in models.items():
        k = len(used[q])
        if not k:
            after[q] = before[q].copy()
            continue
        js = [picks[t] for t in used[q]]
        lower = np.tril(factor[q][:k, js].T)              # L[a, b] = u_b at pick a: the picks' covariance is L L^T
        span = max(1, AFTER_CHUNK if budget is None else min(max(AFTER_CHUNK, n), cov_rows(budget, 1, n_train) - k))
        done = np.empty(len(x))
        for start in range(0, len(x), span):
            block = np.arange(start, min(start + span, len(x)))
            _mu, c = m.posterior_cov(np.vstack([x[rows[js]], x[block]]))
            w = solve_triangular(lower, c[:k, k:], lower=True, check_finite=False)
            done[block] = np.maximum(np.diagonal(c)[k:] - np.einsum("ij,ij->j", w, w), 0.0)
        # measuring more never raises a variance: where the picks do not reach, the covariance's diagonal and predict's
        # sigma are the same variance computed two ways, and only rounding could put it above
        after[q] = np.where(informs[q], np.minimum(np.sqrt(done * weight[q]), before[q]), np.nan)
    marks.append(time.perf_counter())
    return Selection([int(rows[j]) for j in picks], scores, before, after, at_pick, size, capped_at < min(top, len(x)), stopped,
                     dict(zip(("predict", "select", "after"), (round(b - a, 3) for a, b in itertools.pairwise(marks)))))


def norms(score: str, median_rel: dict[str, float | None], rel_sigma_max: float | dict[str, float]) -> dict[str, float]:
    """What divides each quantity's relative sigma in the score: ``ceiling`` -- its confidence ceiling (``rel_sigma_max``,
    one for every quantity or a dict per quantity); ``typical`` -- its held-out median relative error (1 without one: its
    sigma_rel itself)."""
    if score not in SCORES:
        raise ValueError(f"score {score!r}: expected one of {sorted(SCORES)}")
    ceiling = rel_sigma_max if isinstance(rel_sigma_max, dict) else dict.fromkeys(median_rel, rel_sigma_max)
    return {q: float(ceiling[q]) if score == "ceiling" else float(value or 1.0) for q, value in median_rel.items()}


def densify(library: query.Library, stratum: str, quantities: list[str] | None = None, *, n: int, pool_size: int = 65536,
            top: int = 4000, seed: int = 0, k: float = 2.0, rel_sigma_max: float | None = None,
            score: str = DEFAULT_SCORE, bounds: dict | None = None, threads: int | None = None, workers: int | None = None) -> dict:
    """``n`` new geometries of ``stratum`` whose simulation lowers the uncertainty of ``quantities`` (default: every
    column) the most over the sampled domain, with the pool's sigma before and after (see the module docstring), as
    plain JSON data. ``score`` is ``ceiling`` (sigma_rel over the quantity's confidence ceiling, the default) or
    ``typical`` (over the held-out median relative error); ``bounds`` keeps part of the box: per dim a window
    ``{"min": a, "max": b}`` or a fixed value. ``k`` sets each candidate's predicted interval (calibrated, as
    ``lib.query`` reports it). ``rel_sigma_max`` is every quantity's confidence ceiling (None: each quantity's own,
    ``Library.rel_sigma_max``), the one the statistics count against too; ``method`` echoes them. ``threads`` (BLAS
    threads of fitting and prediction) and ``workers`` (fitting processes) are explicit caps within the library's limits,
    as in ``lib.region``."""
    marks = [time.perf_counter()]
    ds = library.dataset(stratum)
    names = list(dict.fromkeys(quantities or ds.columns))
    unknown = [q for q in names if q not in ds.columns]
    if unknown:
        raise ValueError(f"{stratum} has no quantities {unknown}; columns {ds.columns}")
    for label, value in (("n", n), ("pool_size", pool_size), ("top", top)):
        if isinstance(value, bool) or not isinstance(value, numbers.Integral) or value < 1:
            raise ValueError(f"{label} must be a positive integer, got {value!r}")
    if top < n:
        raise ValueError(f"top={top} is below n={n}: the picks are made among the top candidates")
    if score not in SCORES:
        raise ValueError(f"score {score!r}: expected one of {sorted(SCORES)}")
    if rel_sigma_max is not None and not (math.isfinite(rel_sigma_max) and rel_sigma_max > 0):
        raise ValueError(f"rel_sigma_max must be a positive number, got {rel_sigma_max!r}")
    effective, box = _bounds(library, stratum, bounds)
    blas, budget = query.blas_threads(library.limits, threads), suggest.predict_budget(library.limits)
    with threadpool_limits(limits=blas, user_api="blas"):
        models = library.models(stratum, names, workers=workers, threads=threads)
        marks.append(time.perf_counter())
        x, info, floors = _pool(library, stratum, models, pool_size, seed, box)
        informs = {q: ~np.isfinite(floors[q]) if q in floors else np.ones(len(x), dtype=bool) for q in names}
        median_rel = {q: _median_rel(models[q]) for q in names}
        ceilings = {q: models[q].rel_sigma_max if rel_sigma_max is None else float(rel_sigma_max) for q in names}
        norm = norms(score, median_rel, ceilings)
        marks.append(time.perf_counter())
        sel = select(x, {q: models[q].gp for q in names}, n, norm=norm, informs=informs, top=top, budget=budget,
                     n_train=max(len(m.rows) for m in models.values()))
        mark = time.perf_counter()
        candidates = _candidates(library, stratum, models, names, x, sel, floors, k, rel_sigma_max, budget)
        marks.append(time.perf_counter())
    notes = _notes(library, names, info, sel, n, top, budget, informs)
    seconds = {"models": marks[1] - marks[0], "pool": marks[2] - marks[1], **sel.seconds, "report": marks[3] - mark,
               "total": marks[3] - marks[0]}
    return {"stratum": stratum, "quantities": names, "n": n, "bounds": effective, "pool": info,
            "before": {q: _stats(sel.before[q], ceilings[q]) for q in names},
            "after": {q: _stats(sel.after[q], ceilings[q]) for q in names},
            "candidates": candidates,
            "method": {"score": score, "formula": SCORES[score], "norm": norm, "selection": SELECTION, "after": AFTER,
                       "top": sel.top, "top_requested": top, "median_rel": {q: median_rel[q] for q in names},
                       "rel_sigma_max": ceilings, "k": k},
            "seconds": {key: round(float(v), 3) for key, v in seconds.items()}, "notes": notes}


# -- internals ------------------------------------------------------------------------------------------------------------

def _raw(model: gp.StratumGP, x: np.ndarray, chunk_rows: int | None) -> tuple[np.ndarray, np.ndarray]:
    """``model.predict(floor=False)`` in calls of at most ``chunk_rows`` rows (None: one call)."""
    if not len(x):
        return np.empty(0), np.empty(0)
    if chunk_rows is None or len(x) <= chunk_rows:
        return model.predict(x, floor=False)
    parts = [model.predict(x[i:i + chunk_rows], floor=False) for i in range(0, len(x), chunk_rows)]
    return np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts])


def _score(rel: dict[str, np.ndarray], norm: dict[str, float]) -> np.ndarray:
    """max over the quantities of relative sigma / norm, a quantity that does not count (NaN) left out; 0 where none counts."""
    stacked = np.array([np.nan_to_num(rel[q] / norm[q], nan=0.0) for q in rel])
    return stacked.max(axis=0) if len(stacked) else np.zeros(0)


def _median_rel(model: query.Model) -> float | None:
    """The quantity's held-out median relative error from its calibration, or None (calibration off, missing or 0)."""
    value = model.calibration.get("median_rel")
    return float(value) if value is not None and math.isfinite(value) and value > 0 else None


def _bounds(library: query.Library, stratum: str, bounds: dict | None) -> tuple[dict, dict[str, tuple[float, float]]]:
    """``bounds`` checked against the stratum: the effective bounds the answer echoes (per dim the window clipped to the
    achieved range, or the fixed value) and the box ``suggest.pool`` samples. A dim the stratum does not have, a window
    or value that misses the achieved range, and one that holds no value a candidate can take (no multiple of the dim's
    manifest step; for the turns dim, no level with rows) are refused, naming the dim and its achieved range."""
    if bounds is None:
        return {}, {}
    if not isinstance(bounds, dict):                        # the block refuses it first, as a ValueError the command line reports
        raise TypeError(f"bounds: expected a dict of dims, got {bounds!r}")
    ds = library.dataset(stratum)
    steps = library.manifest.strata[stratum].steps
    x = ds.matrix()
    effective, box = {}, {}
    for d, rule in bounds.items():
        if d not in ds.dims:
            raise ValueError(f"bounds: {stratum} has no dim {d!r}; dims {ds.dims}")
        i = ds.dims.index(d)
        lo, hi = float(x[:, i].min()), float(x[:, i].max())
        achieved = f"{d}'s achieved range [{lo:g}, {hi:g}]"
        if isinstance(rule, dict):
            if not rule or set(rule) - {"min", "max"}:
                raise ValueError(f'bounds: {d} takes a window {{"min": a, "max": b}} (either end may be left out) or a fixed value, '
                                 f"got {rule!r}")
            a, b = _real(d, rule.get("min", lo)), _real(d, rule.get("max", hi))
            if a > b:
                raise ValueError(f"bounds: {d} window [{a:g}, {b:g}] has its min above its max")
            if a > hi + 1e-9 or b < lo - 1e-9:
                raise ValueError(f"bounds: {d} window [{a:g}, {b:g}] misses {achieved}")
            a, b = max(a, lo), min(b, hi)
        else:
            a = b = _real(d, rule)
            if not lo - 1e-9 <= a <= hi + 1e-9:
                raise ValueError(f"bounds: {d}={a:g} lies outside {achieved}")
        shown = f"[{a:g}, {b:g}]" if isinstance(rule, dict) else f"={a:g}"
        if d == ds.nt_dim:
            levels = sorted({round(float(v)) for v in x[:, i]})
            if not any(a - 1e-9 <= level <= b + 1e-9 for level in levels) or (a == b and abs(a - round(a)) > 1e-9):
                raise ValueError(f"bounds: {d}{shown} holds none of the turns levels with rows {levels} ({achieved})")
        elif d in steps and math.floor(b / steps[d] + 1e-9) < math.ceil(a / steps[d] - 1e-9):
            raise ValueError(f"bounds: {d}{shown} holds no multiple of its manifest step {steps[d]:g} ({achieved})")
        effective[d] = {"min": a, "max": b} if isinstance(rule, dict) else a
        box[d] = (a, b)
    return effective, box


def _real(dim: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(value):
        raise ValueError(f'bounds: {dim} takes a window {{"min": a, "max": b}} or a fixed value, and numbers only; got {value!r}')
    return float(value)


def _pool(library: query.Library, stratum: str, models: dict[str, query.Model], size: int, seed: int,
          box: dict[str, tuple[float, float]]) -> tuple[np.ndarray, dict, dict[str, np.ndarray]]:
    """The candidates (step 2), what the pool was made of, and per SRF column the sweep stop where it is settled above
    the sweep (NaN elsewhere)."""
    ds = library.dataset(stratum)
    drawn = suggest.pool(library, stratum, size, seed, box=box)
    inside = np.ones(len(drawn), dtype=bool)               # snapped past a window's edge, or held at its level's value
    for d, (a, b) in box.items():
        column = drawn[:, ds.dims.index(d)]
        inside &= (column >= a - 1e-9) & (column <= b + 1e-9)
    bounded = drawn[inside]
    measured = {tuple(r) for r in np.round(ds.matrix(), 9)}
    fresh = np.array([tuple(r) not in measured for r in np.round(bounded, 9)], dtype=bool)
    x = bounded[fresh]
    floors = {q: suggest.srf_floors(library, stratum, x, q) for q in models if q.startswith("SRF")}
    keep = suggest.in_domain(x, models, srf_floor=floors)
    for q, m in models.items():                            # the guard's criterion 2 is the sub-GP's floor; stated anyway
        keep &= m.gp.available(x) | np.isfinite(floors.get(q, np.full(len(x), np.nan)))
    x = x[keep]
    levels = {}
    if ds.nt_dim:
        values, counts = np.unique(np.round(x[:, ds.dims.index(ds.nt_dim)]).astype(int), return_counts=True)
        levels = {int(v): int(c) for v, c in zip(values, counts)}
    info = {"size": size, "distinct": len(drawn), "in_bounds": len(bounded), "measured": int((~fresh).sum()), "in_domain": len(x),
            "levels": levels}
    return x, info, {q: f[keep] for q, f in floors.items()}


def _stats(rel: np.ndarray, ceiling: float) -> dict:
    """Median, p90 and max relative sigma over the rows where the quantity counts, and the share above the ceiling."""
    v = rel[np.isfinite(rel)]
    if not len(v):
        return {"points": 0, "rel_sigma": {"median": None, "p90": None, "max": None}, "above_ceiling_share": None}
    return {"points": len(v), "rel_sigma": {"median": float(np.median(v)), "p90": float(np.quantile(v, 0.9)), "max": float(v.max())},
            "above_ceiling_share": float(np.mean(v > ceiling))}


def _number(value: float | None) -> float | None:
    return None if value is None or not math.isfinite(value) else float(value)


def _candidates(library: query.Library, stratum: str, models: dict[str, query.Model], names: list[str], x: np.ndarray,
                sel: Selection, floors: dict[str, np.ndarray], k: float, rel_sigma_max: float | None, budget: int) -> list[dict]:
    """The picks in ``lib_signoff``'s candidate shape: params, the score, relative sigma before any pick and when picked,
    the current prediction with its calibrated k-sigma interval (SRF in Hz; above the sweep: the stop as its lower
    bound) and the nearest measured rows."""
    if not sel.picks:
        return []
    ds = library.dataset(stratum)
    xp = x[sel.picks]
    _ok, pred = suggest.predict_all(xp, {q: models[q] for q in names}, k=k, rel_sigma_max=rel_sigma_max,
                                    srf_floor={q: f[sel.picks] for q, f in floors.items()}, check_domain=False, chunk_bytes=budget)
    near = _nearest(library, stratum, xp, names)
    out = []
    for t, i in enumerate(sel.picks):
        out.append({"params": {d: float(v) for d, v in zip(ds.dims, xp[t])},
                    "score": sel.score[t],
                    "rel_sigma": {q: _number(sel.before[q][i]) for q in names},
                    "rel_sigma_at_pick": {q: sel.at_pick[q][t] for q in names},
                    "predicted": {q: {key: _number(float(pred[q][key][t])) for key in ("value", "lo", "hi")} for q in names},
                    "nearest": near[t]})
    return out


def _nearest(library: query.Library, stratum: str, xp: np.ndarray, names: list[str]) -> list[list[dict]]:
    """Per pick, the NEAREST measured rows in min-max scaled coordinates (``lib.query``'s evidence), with every asked value."""
    ds = library.dataset(stratum)
    rng = library.ranges(stratum)
    lo = np.array([rng[d][0] for d in ds.dims])
    span = np.array([rng[d][1] - rng[d][0] for d in ds.dims])
    count = min(NEAREST, len(ds.rows))
    dist, idx = cKDTree((ds.matrix() - lo) / span).query((xp - lo) / span, k=list(range(1, count + 1)))
    return [[_evidence(ds.rows[j], ds.dims, names, dd) for dd, j in zip(drow, irow)] for drow, irow in zip(dist, idx)]


def _evidence(row: dataset.Row, dims: list[str], names: list[str], distance: float) -> dict:
    return {"part": row.part, "obs_id": row.obs_id, "params": {d: row.coords[d] for d in dims},
            "values": {q: row.values.get(q) for q in names}, "scaled_distance": round(float(distance), 4)}


def _notes(library: query.Library, names: list[str], info: dict, sel: Selection, n: int, top: int, budget: int,
           informs: dict[str, np.ndarray]) -> list[str]:
    notes = library.notes
    if info["in_bounds"] < info["distinct"]:
        notes.append(f"{info['distinct'] - info['in_bounds']} of {info['distinct']} pool points fell outside the bounds once "
                     "snapped to the manifest steps or held at their turns level's value, and were dropped")
    if not info["in_domain"]:
        notes.append("no pool candidate lies inside every model's domain: nothing to pick")
    elif info["in_domain"] < n:
        notes.append(f"only {info['in_domain']} pool candidates lie inside every model's domain, fewer than n={n}")
    if sel.capped:
        notes.append(f"top lowered from {top} to {sel.top}: {len(names)} covariance matrices of {sel.top} x {sel.top} float64 fill "
                     f"the prediction budget of {budget / query.BYTES_PER_GB:.3g} GB ({suggest.PREDICT_MEMORY_SHARE:.0%} of "
                     f"max_memory_gb {library.limits.max_memory_gb:g} of this machine)")
    if sel.stopped:
        notes.append(f"stopped after {len(sel.picks)} of {n} picks: every other candidate is already resolved for every quantity")
    for q in names:
        above = int((~informs[q]).sum())
        if above:
            notes.append(f"{q}: {above} pool candidates lie above the sweep (most of their 5 nearest measured rows have no "
                         "resonance in it): its model is not asked there, so they neither score for it nor count in its "
                         "statistics, and a pick there does not lower its sigma")
    return notes
