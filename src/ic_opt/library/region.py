"""Region questions: which part of a stratum's geometry space lands inside a set of target windows?

``lib.suggest`` answers "the best few geometries for these targets". A sweep plan needs the other half of the
answer: the extent of everything that meets them. A pre-simulation with ideal elements yields windows (say, an
inductance window per winding, a minimum Q and a coupling window, all at one of the stratum's anchor frequencies);
the real devices inside those windows form a region of the geometry space, and its bounds are the sweep ranges.
``region`` grids that region and describes it:

1. targets as in ``suggest`` (``{min}``, ``{max}``, ``{min, max}``, ``{target, tol}``); anchored quantities add
   ``SRF >= srf_margin x f0``;
2. a coarse pass over ``suggest``'s candidates (the library's rows and ``suggest.pool``'s Sobol points), against the
   stated windows 10 % wider, brackets the region: the survivors' per-dim extent, padded by one grid step. The rows
   anchor it on the faces and corners of the domain, where a Sobol pool is sparse;
3. a grid inside the bracket on multiples of the manifest steps (automatically about ``levels_per_dim`` values per
   dim; every step multiplied by the smallest integer that keeps the grid under ``max_points``); turns go by
   integer level, and a dim fixed within a level stays at its value there (``suggest.pool``'s rule);
4. the grid points inside every model's domain are predicted and gated for confidence;
5. two levels of feasibility: **robust** -- the calibrated k-sigma interval lies inside every window, ``suggest``'s
   own test and where a sweep should centre; **mean** -- the predicted value does, the optimistic envelope;
6. summaries: counts and per-dim ranges per level, the count meeting each target alone (the smallest binds),
   whether the region reaches the library's coverage, conditional ranges per ``group_by`` combination, one
   quantity's trend along one dim, diversified candidates, the measured rows that already meet every target, and a
   seeded sample of points to plot.

Every prediction goes through ``suggest.predict_all``: one GP prediction per quantity and point, with SRF mapped
from the GHz it is fitted in back to Hz in that one place (the T14 prototype predicted every point twice, and
compared SRF in the wrong unit, which emptied a whole run).

The work is sized by the library's ``limits`` (site.yaml's hosts.local unless the library was given its own): the
uncached models are fitted by ``Library.models``, predictions run with ``query.blas_threads`` BLAS threads in chunks
of ``suggest.predict_budget`` bytes.
"""

from __future__ import annotations

import dataclasses
import itertools
import math
import time

import numpy as np
from threadpoolctl import threadpool_limits

from ic_opt.library import dataset, domain, query, suggest

RELAX = 0.10                                         # the coarse pass widens every stated window by this fraction
STAGES = ("models", "coarse", "grid", "predict", "summarize")


def region(library: query.Library, stratum: str, targets: dict, objective: str | None = None, *,
           steps: dict[str, float] | None = None, levels_per_dim: int = 20, max_points: int = 2_000_000,
           pool_size: int = 32768, seed: int = 0, k: float = 2.0, rel_sigma_max: float = domain.DEFAULT_SIGMA_REL_MAX,
           group_by: list[str] | None = None, trend: tuple[str, str] | None = None, n: int = 8, min_spacing: float = 0.05,
           verify_build: bool = False, sample_size: int = 5000, threads: int | None = None,
           workers: int | None = None) -> dict:
    """The region of ``stratum`` whose predictions meet ``targets`` (see the module docstring), as plain JSON data.

    ``steps`` gives grid steps for some or all continuous dims (multiples of the manifest steps); ``group_by`` names
    dims to tabulate the mean set by; ``trend`` is ``(quantity, dim)``. ``threads`` (the BLAS threads of fitting and
    prediction) and ``workers`` (the fitting processes) are explicit caps within the library's limits, refused above
    them; by default both follow from the limits (``Library.models``, ``query.blas_threads``), and an explicit
    OMP_NUM_THREADS lowers the threads."""
    marks = [time.perf_counter()]
    ds = library.dataset(stratum)
    stated = suggest.parse_targets(targets)
    obj = suggest.parse_objective(objective)
    margin = min((r.srf_margin for r in library.manifest.strata[stratum].quantities.values()), default=1.25)
    goals = stated + suggest.implied_srf(stated, obj, margin, ds.columns)
    by, trend = list(group_by or []), tuple(trend) if trend else None
    if trend is not None and len(trend) != 2:
        raise ValueError(f"trend is (quantity, dim), got {trend}")
    names = sorted({t.quantity for t in goals} | ({obj[1]} if obj else set()) | ({trend[0]} if trend else set()))
    _check(ds, stated, names, by + ([trend[1]] if trend else []), levels_per_dim, max_points)
    explicit = _explicit_steps(library, stratum, steps)
    notes = [f"added {t.quantity} >= {t.value / 1e9:g} GHz: anchored quantities need the resonance above {margin:g} x f0"
             for t in goals if t not in stated]
    blas, budget = query.blas_threads(library.limits, threads), suggest.predict_budget(library.limits)
    with threadpool_limits(limits=blas, user_api="blas"):
        models = library.models(stratum, names, workers=workers, threads=threads)
        marks.append(time.perf_counter())
        survivors, pooled, confident, alone = _coarse(library, stratum, models, stated, goals, pool_size, seed, k, rel_sigma_max, budget)
        marks.append(time.perf_counter())
        if len(survivors):
            x, grid, grid_notes = _grid(library, stratum, survivors, explicit, levels_per_dim, max_points)
            notes += grid_notes
        else:
            x, grid = np.empty((0, len(ds.dims))), {"steps": {}, "bracket": {}, "points": 0, "coarsened": 1}
            notes.append(_empty_note(pooled, confident, alone))
        x, floors = _inside(library, stratum, x, models)
        marks.append(time.perf_counter())
        ok, pred = suggest.predict_all(x, models, k=k, rel_sigma_max=rel_sigma_max, srf_floor=floors, check_domain=True, chunk_bytes=budget)
        robust, mean = ok & suggest.satisfy(pred, goals, "robust"), ok & suggest.satisfy(pred, goals, "mean")
        marks.append(time.perf_counter())
        binding = {t.quantity: int((ok & suggest.satisfy(pred, [t], "mean")).sum()) for t in goals}
        if len(survivors) and not mean.any():
            tight = min(binding, key=binding.get)
            notes.append(f"no grid point meets every target at the mean level; alone, {tight} is met by the fewest "
                         f"({binding[tight]} of {int(ok.sum())} confident points)")
        level = "robust" if robust.any() else "mean"
        candidates, rejected = _candidates(library, stratum, x, pred, names, goals, obj, robust if robust.any() else mean,
                                           n, min_spacing, verify_build)
        if rejected:
            notes.append(f"{rejected} leading candidates failed the real build and were skipped")
        measured = _measured(ds, goals, names)
        missed = _unanswered(library, stratum, measured, models, k, rel_sigma_max, budget)
        if missed:
            notes.append(f"{len(missed)} of {len(measured)} measured designs meeting every target lie where a model has no confident "
                         f"answer, so the region cannot contain them: {', '.join(missed[:3])}{' ...' if len(missed) > 3 else ''}")
        step = {**grid["steps"], **({ds.nt_dim: 1.0} if ds.nt_dim else {})}
        out = {"stratum": stratum, "targets": [dataclasses.asdict(t) for t in goals], "objective": objective,
               "grid": {"steps": grid["steps"], "bracket": grid["bracket"], "points": grid["points"], "in_domain": len(x),
                        "confident": int(ok.sum()), "coarsened": grid["coarsened"], "auto_steps": not steps},
               "levels": {"robust": _level(x, robust, ds.dims), "mean": _level(x, mean, ds.dims)},
               "binding": binding, "edge": _edge(x, mean, ds, step), "group_by": _group_by(x, ds.dims, by, robust, mean, pred, obj),
               "trend": _trend(x, ds.dims, trend, ok, pred, goals), "candidates": candidates, "candidates_level": level,
               "measured": measured, "points_sample": _sample(x, ds.dims, robust, mean, pred, names, sample_size, seed)}
        marks.append(time.perf_counter())
    out["seconds"] = {**dict(zip(STAGES, (round(b - a, 3) for a, b in itertools.pairwise(marks)))), "total": round(marks[-1] - marks[0], 3)}
    out["notes"] = notes
    return out


# -- inputs -------------------------------------------------------------------------------------------------------------

def _check(ds: dataset.Dataset, stated: list[suggest.Target], names: list[str], dims: list[str], levels_per_dim: int,
           max_points: int) -> None:
    if not stated:
        raise ValueError("a region needs at least one target")
    unknown = [q for q in names if q not in ds.columns]
    if unknown:
        raise ValueError(f"{ds.stratum} has no quantities {unknown}; columns {ds.columns}")
    stray = [d for d in dims if d not in ds.dims]
    if stray:
        raise ValueError(f"{ds.stratum} has no dims {stray}; dims {ds.dims}")
    if levels_per_dim < 1 or max_points < 1:
        raise ValueError(f"levels_per_dim and max_points must be positive, got {levels_per_dim} and {max_points}")


def _explicit_steps(library: query.Library, stratum: str, steps: dict | None) -> dict[str, float]:
    """``steps`` checked: continuous dims only, each a positive multiple of the manifest step (within 1e-9)."""
    ds = library.dataset(stratum)
    lattice = library.manifest.strata[stratum].steps
    out = {}
    for d, value in (steps or {}).items():
        if d not in ds.dims or d == ds.nt_dim:
            raise ValueError(f"steps: {d!r} is not a continuous dim of {stratum} {[c for c in ds.dims if c != ds.nt_dim]} "
                             "(turns go by integer level)")
        v, base = float(value), lattice.get(d)
        ratio = v / base if base else 1.0
        if not v > 0 or round(ratio) < 1 or abs(ratio - round(ratio)) > 1e-9:
            raise ValueError(f"steps: {d}={v:g} must be a positive multiple of the manifest step {base:g}" if base
                             else f"steps: {d}={v:g} must be positive")
        out[d] = v
    return out


# -- the coarse pass and the grid ---------------------------------------------------------------------------------------

def _relaxed(t: suggest.Target) -> suggest.Target:
    """``t`` RELAX wider: a bound moves out by that fraction of itself, a relative tolerance grows by it."""
    if t.kind == "min":
        return dataclasses.replace(t, value=t.value - RELAX * abs(t.value))
    if t.kind == "max":
        return dataclasses.replace(t, value=t.value + RELAX * abs(t.value))
    if t.kind == "window":
        return dataclasses.replace(t, value=t.value - RELAX * abs(t.value), upper=t.upper + RELAX * abs(t.upper))
    return dataclasses.replace(t, tol=t.tol + RELAX)


def _coarse(library: query.Library, stratum: str, models: dict, stated: list[suggest.Target], goals: list[suggest.Target],
            pool_size: int, seed: int, k: float, rel_sigma_max: float, budget: int) -> tuple[np.ndarray, int, int, dict[str, int]]:
    """The coarse candidates meeting every target at the mean level with the stated windows RELAX wider (the implied
    SRF as it is); the candidate count, the confident count, and the count meeting each relaxed target alone.

    The candidates are ``suggest``'s: the library's rows and the Sobol pool. The rows matter here: they sit on the
    faces and corners of the sampled domain (concentric windings, the extreme widths), where a Sobol pool is sparse,
    so a bracket from the pool alone can cut off part of a region that measured designs prove is there."""
    x = np.unique(np.round(np.vstack([library.dataset(stratum).matrix(), suggest.pool(library, stratum, pool_size, seed)]), 9), axis=0)
    floors = {q: suggest.srf_floors(library, stratum, x, q) for q in models if q.startswith("SRF")}
    ok, pred = suggest.predict_all(x, models, k=k, rel_sigma_max=rel_sigma_max, srf_floor=floors, chunk_bytes=budget)
    loose = [_relaxed(t) if t in stated else t for t in goals]
    alone = {t.quantity: int((ok & suggest.satisfy(pred, [t], "mean")).sum()) for t in loose}
    return x[ok & suggest.satisfy(pred, loose, "mean")], len(x), int(ok.sum()), alone


def _empty_note(pooled: int, confident: int, alone: dict[str, int]) -> str:
    if not confident:
        return f"none of the {pooled} coarse candidates (library rows and Sobol points) is inside the domain and confident"
    zero = [q for q, c in alone.items() if not c]
    head = (f"no coarse candidate meets {', '.join(zero)} even with the stated windows {RELAX:.0%} wider" if zero else
            f"every target alone is met by coarse candidates but never all together, even {RELAX:.0%} wider "
            f"(the tightest: {min(alone, key=alone.get)})")
    counts = ", ".join(f"{q} {c}" for q, c in alone.items())
    return f"{head}; {confident} of {pooled} candidates (library rows and Sobol points) are in domain and confident, and alone meet {counts}"


def _grid(library: query.Library, stratum: str, survivors: np.ndarray, explicit: dict[str, float], levels_per_dim: int,
          max_points: int) -> tuple[np.ndarray, dict, list[str]]:
    """The grid over the survivors' bracket, its description (steps, bracket, points, coarsened) and notes. An automatic
    step follows from the survivors' span (the bracket before its padding; the library range if they share one value);
    the padding is one grid step at the coarsening ``m`` in force, so the grid always reaches past the survivors."""
    ds = library.dataset(stratum)
    ranges = library.ranges(stratum)
    lattice = library.manifest.strata[stratum].steps
    lo, hi = survivors.min(axis=0), survivors.max(axis=0)
    step = {}
    for i, d in enumerate(ds.dims):
        if d != ds.nt_dim:
            span = hi[i] - lo[i] if hi[i] - lo[i] > 1e-9 else ranges[d][1] - ranges[d][0]
            step[d] = explicit.get(d) or _auto_step(span, lattice.get(d), levels_per_dim)

    def bracket_at(m: int) -> dict[str, tuple[float, float]]:
        pad = {d: step[d] * m if d in step else 1.0 for d in ds.dims}          # turns: one level
        return {d: (max(float(lo[i]) - pad[d], ranges[d][0]), min(float(hi[i]) + pad[d], ranges[d][1])) for i, d in enumerate(ds.dims)}

    slices = _slices(ds, bracket_at(1))
    m = 1
    while True:                                          # ends: clipped to the ranges, every axis shrinks to one value
        bracket = bracket_at(m)
        axes = _axes(ds.dims, slices, bracket, step, lattice, m)
        if _size(axes) <= max_points or all(len(a) == 1 for axis in axes for a in axis):
            break
        m += 1
    x = np.vstack([_product(axis) for axis in axes]) if axes else np.empty((0, len(ds.dims)))
    notes = []
    if explicit and len(explicit) < len(step):
        notes.append(f"automatic steps for {[d for d in step if d not in explicit]}")
    if m > 1:
        notes.append(f"the grid would exceed max_points={max_points}: every step multiplied by {m}")
    if len(x) > max_points:
        notes.append(f"the grid keeps {len(x)} points above max_points={max_points}: every dim is down to one value")
    info = {"steps": {d: round(s * m, 9) for d, s in step.items()},
            "bracket": {d: [float(a), float(b)] for d, (a, b) in bracket.items()}, "points": len(x), "coarsened": m}
    return x, info, notes


def _auto_step(span: float, base: float | None, levels: int) -> float:
    """About ``levels`` values over ``span``, rounded up to a multiple of the manifest step ``base``."""
    if not base:
        return span / levels
    return round(max(1, math.ceil(span / levels / base - 1e-9)) * base, 9)


def _slices(ds: dataset.Dataset, bracket: dict[str, tuple[float, float]]) -> list[dict[int, float]]:
    """The grid's slices as the {dim index: value} each fixes: one per integer turns level inside the bracket (the
    level itself), else a single slice; plus every dim with one value among the slice's rows (``pool``'s rule; the
    guard admits no other value there)."""
    x = ds.matrix()
    cont = [i for i, d in enumerate(ds.dims) if d != ds.nt_dim]
    if ds.nt_dim is None:
        parts = [({}, x)]
    else:
        j = ds.dims.index(ds.nt_dim)
        lo, hi = bracket[ds.nt_dim]
        turns = sorted({round(float(v)) for v in x[:, j] if lo - 1e-9 <= v <= hi + 1e-9})
        parts = [({j: float(t)}, x[np.round(x[:, j]) == t]) for t in turns]
    return [{**fixed, **{i: float(rows[0, i]) for i in cont if np.ptp(rows[:, i]) <= 1e-9}} for fixed, rows in parts]


def _axes(dims: list[str], slices: list[dict[int, float]], bracket: dict, step: dict[str, float], lattice: dict[str, float],
          m: int) -> list[list[np.ndarray]]:
    """Per slice, every dim's values: the fixed value, else ``_axis`` at ``m`` times its step."""
    return [[np.array([fixed[i]]) if i in fixed else _axis(*bracket[d], step[d] * m, lattice.get(d)) for i, d in enumerate(dims)]
            for fixed in slices]


def _axis(lo: float, hi: float, step: float, base: float | None) -> np.ndarray:
    """Multiples of ``step`` inside [lo, hi] (the centre when none fits), on the manifest lattice ``base``."""
    first, last = math.ceil(lo / step - 1e-9), math.floor(hi / step + 1e-9)
    values = np.arange(first, last + 1) * step if last >= first else np.array([(lo + hi) / 2])
    if base:
        values = np.round(values / base) * base
    return np.unique(np.round(values, 9))


def _size(axes: list[list[np.ndarray]]) -> int:
    return sum(math.prod(len(a) for a in axis) for axis in axes)


def _product(axis: list[np.ndarray]) -> np.ndarray:
    return np.stack(np.meshgrid(*axis, indexing="ij"), axis=-1).reshape(-1, len(axis))


def _inside(library: query.Library, stratum: str, x: np.ndarray, models: dict) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """The grid points inside every model's domain (``suggest.in_domain``, the gate ``predict_all`` applies) and their
    SRF floors, so that only those are predicted."""
    if not len(x):
        return x, {}
    floors = {q: suggest.srf_floors(library, stratum, x, q) for q in models if q.startswith("SRF")}
    keep = suggest.in_domain(x, models, srf_floor=floors)
    return x[keep], {q: f[keep] for q, f in floors.items()}


# -- summaries ----------------------------------------------------------------------------------------------------------

def _level(x: np.ndarray, mask: np.ndarray, dims: list[str]) -> dict:
    sel = x[mask]
    return {"count": int(mask.sum()),
            "ranges": {d: [float(sel[:, i].min()), float(sel[:, i].max())] for i, d in enumerate(dims)} if len(sel) else {}}


def _edge(x: np.ndarray, mean: np.ndarray, ds: dataset.Dataset, step: dict[str, float]) -> dict:
    """Per dim, whether the mean set reaches the library's coverage: no grid step fits between it and the achieved
    range (``Library.ranges`` except for a dim with one value, which the set then touches on both sides)."""
    sel, achieved = x[mean], ds.matrix()
    out = {}
    for i, d in enumerate(ds.dims):
        if len(sel):
            out[d] = {"at_min": bool(sel[:, i].min() - step[d] < achieved[:, i].min() - 1e-9),
                      "at_max": bool(sel[:, i].max() + step[d] > achieved[:, i].max() + 1e-9)}
        else:
            out[d] = {"at_min": False, "at_max": False}
    return out


def _group_by(x: np.ndarray, dims: list[str], by: list[str], robust: np.ndarray, mean: np.ndarray, pred: dict,
              obj: tuple[str, str] | None) -> dict:
    """Per combination of the ``by`` dims in the mean set: both counts, the other dims' ranges over its mean points and
    the objective's predicted range there (null without an objective)."""
    sel = np.nonzero(mean)[0]
    if not by or not len(sel):
        return {"dims": by, "rows": []}
    cols = [dims.index(d) for d in by]
    rest = [i for i in range(len(dims)) if i not in cols]
    keys, group = np.unique(x[np.ix_(sel, cols)], axis=0, return_inverse=True)
    group = group.reshape(-1)
    order = np.argsort(group, kind="stable")
    starts = np.searchsorted(group[order], np.arange(len(keys)))
    xs = x[sel][order]
    lo, hi = np.minimum.reduceat(xs, starts), np.maximum.reduceat(xs, starts)
    count_mean, count_robust = np.bincount(group, minlength=len(keys)), np.bincount(group[robust[sel]], minlength=len(keys))
    if obj:
        value = pred[obj[1]]["value"][sel][order]
        v_lo, v_hi = np.minimum.reduceat(value, starts), np.maximum.reduceat(value, starts)
    rows = [{"key": {d: float(key[j]) for j, d in enumerate(by)}, "count_robust": int(count_robust[g]), "count_mean": int(count_mean[g]),
             "ranges": {dims[i]: [float(lo[g, i]), float(hi[g, i])] for i in rest},
             "objective": [float(v_lo[g]), float(v_hi[g])] if obj else None} for g, key in enumerate(keys)]
    return {"dims": by, "rows": rows}


def _trend(x: np.ndarray, dims: list[str], trend: tuple[str, str] | None, ok: np.ndarray, pred: dict, goals: list[suggest.Target]) -> dict:
    """``(quantity, dim)``: over the confident points meeting every other target at the mean level, the quantity's
    predicted value per value of the dim."""
    if not trend:
        return {"quantity": None, "dim": None, "rows": []}
    q, d = trend
    mask = ok & suggest.satisfy(pred, [t for t in goals if t.quantity != q], "mean")
    col, value = x[mask, dims.index(d)], pred[q]["value"][mask]
    rows = []
    for v in np.unique(col):
        at = value[col == v]
        rows.append({"value": float(v), "count": len(at), "min": float(at.min()), "median": float(np.median(at)), "max": float(at.max())})
    return {"quantity": q, "dim": d, "rows": rows}


def _candidates(library: query.Library, stratum: str, x: np.ndarray, pred: dict, names: list[str], goals: list[suggest.Target],
                obj: tuple[str, str] | None, pick: np.ndarray, n: int, min_spacing: float, verify_build: bool) -> tuple[list[dict], int]:
    """Up to ``n`` of the ``pick`` points, ranked as ``score`` ranks and kept ``min_spacing`` apart, in ``suggest``'s entry
    shape; with ``verify_build`` each is built with the real generator first and a failure is skipped and counted."""
    if n < 1:
        return [], 0
    ds = library.dataset(stratum)
    ranked = suggest.rank(pred, goals, obj, pick)
    out, rejected = [], 0
    for i in suggest.diversify(x, ranked, library.ranges(stratum), ds.dims, keep=max(4 * n, 8) if verify_build else n,
                               min_spacing=min_spacing):
        params = dict(zip(ds.dims, (float(v) for v in x[i])))
        entry: dict = {"params": params}
        if verify_build:
            entry["build"] = suggest.build_check(library, stratum, params)
            if not entry["build"]["built"]:
                rejected += 1
                continue
        entry["predicted"] = {q: {key: float(pred[q][key][i]) for key in ("value", "lo", "hi", "rel_sigma")} for q in names}
        entry["nearest"] = [query._evidence(r, names[0], ds.dims) for r in query._nearest_rows(library, stratum, params, 3)]
        out.append(entry)
        if len(out) == n:
            break
    return out, rejected


def _measured(ds: dataset.Dataset, goals: list[suggest.Target], names: list[str]) -> list[dict]:
    """The dataset rows whose measured values meet every goal (``satisfy`` on the measurements); a row without a
    value for some goal quantity is skipped."""
    values = {t.quantity: ds.values(t.quantity) for t in goals}
    hit = np.all([np.isfinite(v) for v in values.values()], axis=0) & suggest.satisfy({q: {"value": v} for q, v in values.items()}, goals, "mean")
    return [{"part": r.part, "obs_id": r.obs_id, "params": {d: float(r.coords[d]) for d in ds.dims},
             "values": {q: r.values.get(q) for q in names}} for r, h in zip(ds.rows, hit) if h]


def _unanswered(library: query.Library, stratum: str, measured: list[dict], models: dict, k: float, rel_sigma_max: float,
                budget: int) -> list[str]:
    """``part/obs_id`` of the measured designs where ``predict_all``'s gate fails (a model's domain, its confidence): no
    grid point there can be feasible, whatever the design measured."""
    if not measured:
        return []
    dims = library.dataset(stratum).dims
    x = np.array([[m["params"][d] for d in dims] for m in measured])
    floors = {q: suggest.srf_floors(library, stratum, x, q) for q in models if q.startswith("SRF")}
    ok, _ = suggest.predict_all(x, models, k=k, rel_sigma_max=rel_sigma_max, srf_floor=floors, chunk_bytes=budget)
    return [f"{m['part']}/{m['obs_id']}" for m, good in zip(measured, ok) if not good]


def _sample(x: np.ndarray, dims: list[str], robust: np.ndarray, mean: np.ndarray, pred: dict, names: list[str], size: int,
            seed: int) -> list[dict]:
    """At most ``size`` points of the mean set, drawn with a seeded generator, for plots."""
    idx = np.nonzero(mean)[0]
    if len(idx) > size:
        idx = np.sort(np.random.default_rng(seed).choice(idx, size=max(size, 0), replace=False))
    return [{"params": {d: float(x[i, j]) for j, d in enumerate(dims)}, "level": "robust" if robust[i] else "mean",
             "predicted": {q: float(pred[q]["value"][i]) for q in names}} for i in idx]
