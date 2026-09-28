"""The run digest: what a run found, computed from its observations (``docs/refactor/T17_1_5_SPEC.md``, section 5; D7).

ic-opt calls no language model. After a run it states, in numbers, what the run found -- what is optimized, how far the
run is, what is in the way, where the good points are, what failed and where, what advice was given and how it fared,
the operating points of the best point -- for whoever reads it next, a person or their own agent, who may answer with
advice (section 2 of the specification). :func:`digest` is pure: no file, no simulator, any list of observations of the
spec. Where there is too little to compute an entry it is ``None`` and the section's ``notes`` say why.

Definitions the specification leaves open:

- *scored*: status ``ok`` or ``constraint_failed`` (every metric has a value). A point that *gives no value*: any other
  status (``metric_failed``, ``failed:<stage>``).
- *batch*: the points whose origin ends in the same batch key ``:<k>`` (``suggest:metric_gp:tr:0:40``, the history
  size when the batch was proposed); an advice's start point (``advice:<id>``) joins the batch of the next such point
  of its step; any other point without one (start points, a Sobol design, OpenBox, user points) belongs to the tens of
  its step: its position among its step's points, divided by 10.
- *a metric's spread*: the interquartile range of its finite observed values. Not the standard deviation: a few
  points far off the rest (an offset thousands of times the typical one, from a design that barely works) make it so
  large that every margin reads as zero.
- *rank correlation*: Spearman's rho (tied values share their average rank; integer variables with few levels tie
  often), per variable and modelled metric, over the scored points that hold the metric; omitted below 10 scored
  points, ``None`` where the variable or the metric takes one value there.
- *the split that separates scored from unscored points best*: over every threshold ``t`` between two visited levels
  of a variable (``<= t`` against ``> t``) that leaves at least 5 points on each side, the largest decrease of the Gini
  impurity, ``G(all) - n_lo / n * G(lo) - n_hi / n * G(hi)`` with ``G = 2 p (1 - p)`` and ``p`` the share of points that
  give no value: the decision stump of a classification tree. It weighs each side by its size, so a split that fences
  off five failures counts less than one that halves the points. Only splits that decrease the impurity are listed;
  ties go to the variable first in the spec, then to the lower threshold.
- *thirds of a range*: in the variable's own scale (logarithmic where ``upper / lower >= 10`` and ``lower > 0``):
  ``[0, 1/3)``, ``[1/3, 2/3)``, ``[2/3, 1]``.
- *suggested range*: the span of the ``top`` best feasible points, one level wider on each side, inside the spec's
  range; ``reaches_bound`` names the spec bound those points themselves lie at (the span before widening).

The value helpers (units, SI prefixes, a constraint as a reader says it, a point's value per corner) live here and the
report (``blocks/analyze.py``) imports them: importing ``ic_opt.blocks`` loads every block and the strategies'
libraries, and this module imports only numpy, scipy and the core of ic-opt. One exception, taken only when an origin
names ``metric_gp``: the search region is what ``MetricGpSuggester.region_state`` gives, and that package loads
scikit-learn (a declared dependency).
"""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any

import numpy as np
from scipy import stats

from ic_opt import space
from ic_opt.observation import Observation
from ic_opt.space import split_origin
from ic_opt.spec import Spec

DIGEST_VERSION = 1
SCORED = ("ok", "constraint_failed")
MIN_CORRELATED = 10                # scored points below which no rank correlation is given
MIN_SIDE = 5                       # points on either side of a split
MIN_FEASIBLE_RANGES = 3            # feasible points below which no range is suggested
SEPARATING = 3                     # variables listed as separating scored from unscored points
LOG_SPAN = 10                      # upper / lower from which a positive range is read logarithmically (metric_gp's)
QUANTITIES = ("region", "ids", "vgs", "vds", "vbs", "vth", "vdsat", "gm", "gds", "gmoverid", "cgs", "cgd")   # section 4
QUANTITY_UNITS = {"ids": "A", "vgs": "V", "vds": "V", "vbs": "V", "vth": "V", "vdsat": "V", "gm": "S", "gds": "S",
                  "gmoverid": "1/V", "cgs": "F", "cgd": "F"}

OPS = {"gt": ">", "ge": "≥", "lt": "<", "le": "≤"}
SI_UNITS = {"Hz", "H", "F", "s", "A", "V", "W", "Ohm", "m", "S"}               # units that take an SI prefix when printed
SI_PREFIXES = [(1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"),
               (1e-12, "p"), (1e-15, "f")]
_BATCH_KEY = re.compile(r"^suggest:.*:(\d+)$")
_OBS_NUMBER = re.compile(r"^(.*?)(\d+)$")


# -- values as the report prints them -----------------------------------------------------------------------------------

def unit_of(spec: Spec, metric: str) -> str:
    """The unit a metric's value prints with; dimensionless ones (``ratio``, ``1``) print bare."""
    m = next((m for m in spec.metrics if m.name == metric), None)
    unit = m.unit if m else ""
    return "" if unit in ("ratio", "1") else unit


def quantity(value: float | None, unit: str) -> str:
    """A value for reading: four significant digits and, for a base unit, an SI prefix (32 GHz, 111.4 pH, -6 dBm)."""
    if value is None or not math.isfinite(value):
        return "—"
    if unit in SI_UNITS and value != 0:
        magnitude = abs(value)
        for scale, prefix in SI_PREFIXES:
            if magnitude >= scale:
                return f"{value / scale:.4g} {prefix}{unit}"
        return f"{value:.4g} {unit}"
    return f"{value:.4g}" + (f" {unit}" if unit else "")


def constraint_text(spec: Spec, constraint) -> str:
    """``BW > 26 GHz``: the constraint as a reader says it."""
    return f"{constraint.metric} {OPS[constraint.op]} {quantity(_limit(constraint), unit_of(spec, constraint.metric))}"


def margin(constraint, value: float) -> float:
    """How far ``value`` is inside the constraint, in the metric's unit: positive passes."""
    limit = _limit(constraint)
    return (limit - value) if constraint.op in ("lt", "le") else (value - limit)


def metrics_per_corner(spec: Spec, o: Observation) -> dict[str, dict[str, float]]:
    """A corner's metrics: every corner-less child's (devices; on a spec without corners, the testbenches too) and then its own
    testbench children's. A spec without corners has the one corner ``nominal``."""
    corner_ids = [c.id for c in spec.corners] or ["nominal"]
    shared = {k: v for ch in o.children.values() if ch.corner is None for k, v in ch.metrics.items()}
    out = {cid: dict(shared) for cid in corner_ids}
    for ch in o.children.values():
        if ch.corner is not None:
            out.setdefault(ch.corner, dict(shared)).update(ch.metrics)
    return out


def scored_corners(spec: Spec) -> list[str]:
    """The corners the constraint policy scores: every corner under ``all_corners``, the nominal one otherwise."""
    corner_ids = [c.id for c in spec.corners] or ["nominal"]
    if spec.corner_policy.constraints == "all_corners":
        return corner_ids
    return ["nominal"] if "nominal" in corner_ids else corner_ids[:1]


def constraint_value(spec: Spec, constraint, o: Observation) -> float | None:
    """The metric's value the constraint is judged on for this point: its worst over the scored corners (the smallest for a
    lower bound, the largest for an upper one), as ``sim.corner.aggregate`` judges it. The point's own ``metrics`` hold one
    corner's values -- the corner with the largest total penalty -- and a constraint another corner violates alone was
    counted as passed before N-35 (2026-09-27: BW 43/50 in the report, 41/50 by the policy)."""
    per_corner = metrics_per_corner(spec, o)
    values = [m[constraint.metric] for cid in scored_corners(spec) if (m := per_corner.get(cid)) and constraint.metric in m]
    if not values:
        return o.metrics.get(constraint.metric)
    return min(values) if constraint.op in ("gt", "ge") else max(values)


def _limit(constraint) -> float:
    return float(space.parse_scalar(constraint.value.replace(" ", ""))[0])


# -- the digest -----------------------------------------------------------------------------------------------------------

def digest(spec: Spec, observations: Iterable[Observation], *, advice: Sequence[dict] = (), top: int = 5,
           step: str | None = None) -> dict[str, Any]:
    """The digest of ``observations`` (only step ``step``'s when given) as a JSON-ready dict, ``"digest_version": 1``.
    ``advice``: the rows of ``.icopt/advice.jsonl`` in file order (section 2); ``top``: how many of the best feasible
    points the spans and suggested ranges describe. The order of ``observations`` does not matter: they are taken in
    observation-number order, as the store and the strategies take them."""
    rows = sorted((o for o in observations if step is None or o.step == step), key=_obs_order)
    grid = [_Grid(v) for v in spec.variables]
    feasible = sorted((o for o in rows if o.feasible), key=lambda o: o.objective if o.objective is not None else math.inf)
    return {
        "digest_version": DIGEST_VERSION,
        "project": spec.project,
        "step": step,
        "top": top,
        "problem": _problem(spec, rows, grid),
        "counts": _counts(rows),
        "progress": _progress(spec, rows, feasible),
        "constraints": _constraints(spec, rows, feasible),
        "variables": _variables(spec, rows, feasible[:top], grid),
        "failures": _failures(spec, rows, grid),
        "suggested_ranges": suggested_ranges(spec, rows, top=top),
        "strategy": _strategy(spec, rows),
        "advice": _advice(rows, advice),
        "operating_points": _operating_points(rows, feasible),
    }


def suggested_ranges(spec: Spec, observations: Iterable[Observation], *, top: int = 5) -> dict[str, Any]:
    """Section 5.2 ``suggested_ranges`` (also the report's "Where the best points are"): ``ranges`` is ``None`` with a
    note below 3 feasible points."""
    feasible = sorted((o for o in sorted(observations, key=_obs_order) if o.feasible),
                      key=lambda o: o.objective if o.objective is not None else math.inf)
    if len(feasible) < MIN_FEASIBLE_RANGES:
        return {"ranges": None, "points": len(feasible),
                "notes": {"ranges": f"needs at least {MIN_FEASIBLE_RANGES} feasible points (have {len(feasible)})"}}
    best = feasible[:top]
    out = []
    for g in (_Grid(v) for v in spec.variables):
        levels = [g.index(o.params[g.name]) for o in best]
        lo, hi = min(levels), max(levels)
        wide_lo, wide_hi = max(0, lo - 1), min(g.count - 1, hi + 1)
        at = [side for side, hit in (("lower", lo == 0), ("upper", hi == g.count - 1)) if hit and g.count > 1]
        out.append({"variable": g.name, "lower": g.text(0), "upper": g.text(g.count - 1), "levels": g.count,
                    "span": [g.text(lo), g.text(hi)], "suggested": [g.text(wide_lo), g.text(wide_hi)],
                    "suggested_levels": wide_hi - wide_lo + 1,
                    "reaches_bound": "both" if len(at) == 2 else at[0] if at else None})
    return {"ranges": out, "points": len(best), "notes": {}}


def _problem(spec: Spec, rows: list[Observation], grid: list[_Grid]) -> dict[str, Any]:
    modelled = set(_modelled(spec))
    corners = sorted({ch.corner or "nominal" for o in rows for ch in o.children.values()})
    return {
        "variables": [{"name": g.name, "lower": g.text(0), "upper": g.text(g.count - 1), "step": g.variable.step,
                       "levels": g.count, "scale": "log" if g.log else "linear"} for g in grid],
        "metrics": [{"name": m.name, "unit": unit_of(spec, m.name), "modelled": m.name in modelled} for m in spec.metrics],
        "constraints": [{"text": constraint_text(spec, c), "metric": c.metric, "op": c.op, "value": c.value,
                         "unit": unit_of(spec, c.metric)} for c in spec.constraints],
        "objective": ({"direction": spec.objective.direction, "expression": spec.objective.expression}
                      if spec.objective else None),
        "corners": corners,
    }


def _counts(rows: list[Observation]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    per_step: dict[str, int] = {}
    for o in rows:
        by_status[o.status] = by_status.get(o.status, 0) + 1
        per_step[o.step] = per_step.get(o.step, 0) + 1
    unknown = sum(1 for o in rows if o.simulations is None)
    return {"points": len(rows), "by_status": dict(sorted(by_status.items())),
            "simulations": sum(o.simulations or 0 for o in rows), "per_step": per_step,
            "notes": {"simulations": f"{unknown} points recorded before 0.2.x carry no count"} if unknown else {}}


def _progress(spec: Spec, rows: list[Observation], feasible: list[Observation]) -> dict[str, Any]:
    notes: dict[str, str] = {}
    first = next(((i, o) for i, o in enumerate(rows) if o.feasible), None)
    best = feasible[0] if feasible else None
    if best is None:
        notes["best"] = f"no feasible point in {len(rows)}" if rows else "no observation"
    position = {o.obs_id: i for i, o in enumerate(rows)}
    batches, best_so_far, done = [], None, 0
    for label, end in _batches(rows):
        for o in rows[done : end + 1]:
            if o.feasible and o.objective is not None and (best_so_far is None or o.objective < best_so_far.objective):
                best_so_far = o
        done = end + 1
        batches.append({"batch": label, "points": end + 1, "best": _num(best_so_far.fom) if best_so_far else None,
                        "best_id": best_so_far.obs_id if best_so_far else None})
    return {
        "first_feasible": {"index": first[0] + 1, "id": first[1].obs_id} if first else None,
        "best": None if best is None else {
            "id": best.obs_id, "index": position[best.obs_id] + 1, "origin": best.origin,
            "params": {v.name: best.params[v.name] for v in spec.variables},
            "metrics": {m.name: {"value": _num(best.metrics[m.name]), "unit": unit_of(spec, m.name)}
                        for m in spec.metrics if m.name in best.metrics},
            "objective": _num(best.fom)},
        "direction": spec.objective.direction if spec.objective else None,
        "batches": batches,
        "notes": notes,
    }


def _batches(rows: list[Observation]) -> list[tuple[str, int]]:
    """(label, position of its last point) per batch, in the order the batches ended. An advice's start points are
    proposed first in the batch whose strategy points follow them: they join the next keyed point of their step."""
    ends: dict[tuple, int] = {}
    in_step: dict[str, int] = {}
    following: dict[int, int | None] = {}              # position -> batch key of the next keyed point of its step
    ahead: dict[str, int | None] = {}
    for i in range(len(rows) - 1, -1, -1):
        following[i] = ahead.get(rows[i].step)
        if (key := batch_key(rows[i].origin)) is not None:
            ahead[rows[i].step] = key
    for i, o in enumerate(rows):
        key = batch_key(o.origin)
        if key is None and split_origin(o.origin)[0].startswith("advice:"):
            key = following[i]
        n = in_step.get(o.step, 0)
        in_step[o.step] = n + 1
        ident = (o.step, "k", key) if key is not None else (o.step, "n", n // 10)
        ends[ident] = i
    ordered = sorted(ends.items(), key=lambda item: item[1])
    return [(f"{i + 1}", end) for i, (_ident, end) in enumerate(ordered)]


def batch_key(origin: str) -> int | None:
    """The history size a strategy's batch was proposed at, read from the end of its origin; None when it carries none."""
    match = _BATCH_KEY.match(split_origin(origin)[0])
    return int(match.group(1)) if match else None


def _constraints(spec: Spec, rows: list[Observation], feasible: list[Observation]) -> list[dict[str, Any]]:
    scored = [o for o in rows if o.status in SCORED]
    # margins[p][i]: scored point p's margin to constraint i (None where the metric has no value)
    margins = [[None if (v := constraint_value(spec, c, o)) is None else margin(c, v) for c in spec.constraints]
               for o in scored]
    best = feasible[0] if feasible else None
    out = []
    for i, c in enumerate(spec.constraints):
        spread = _spread([v for o in rows if (v := o.metrics.get(c.metric)) is not None])
        mine = [(o, m[i]) for o, m in zip(scored, margins, strict=True) if m[i] is not None]
        only = sum(1 for m in margins if [j for j, x in enumerate(m) if x is not None and x < 0] == [i])
        failing = [(o, x) for o, x in mine if x < 0]
        closest = max(failing, key=lambda r: r[1]) if failing else None
        best_value = constraint_value(spec, c, best) if best is not None else None
        out.append({
            "constraint": constraint_text(spec, c), "metric": c.metric, "unit": unit_of(spec, c.metric),
            "scored": len(mine), "met": sum(1 for _o, x in mine if x >= 0), "fails_only_this": only,
            "spread": _num(spread),
            "best_margin": None if best_value is None else _margin_entry(best.obs_id, margin(c, best_value), spread),
            "closest_failing": None if closest is None else _margin_entry(closest[0].obs_id, closest[1], spread),
        })
    return out


def _margin_entry(obs_id: str, value: float, spread: float | None) -> dict[str, Any]:
    return {"id": obs_id, "margin": _num(value), "normalized": _num(value / spread) if spread else None}


def _variables(spec: Spec, rows: list[Observation], best: list[Observation], grid: list[_Grid]) -> list[dict[str, Any]]:
    scored = [o for o in rows if o.status in SCORED]
    modelled = _modelled(spec)
    out = []
    for g in grid:
        visited = {g.index(o.params[g.name]) for o in rows}
        span = [g.index(o.params[g.name]) for o in best]
        thirds: dict[str, dict[str, Any]] = {}
        for third in ("lower", "middle", "upper"):
            inside = [o for o in rows if g.third(o.params[g.name]) == third]
            unscored = sum(1 for o in inside if o.status not in SCORED)
            thirds[third] = {"points": len(inside), "no_value": unscored,
                             "share": _num(unscored / len(inside)) if inside else None}
        at_bound = None
        if best and g.count > 1:
            level = g.index(best[0].params[g.name])
            at_bound = "lower" if level == 0 else "upper" if level == g.count - 1 else None
        correlation = None
        if len(scored) >= MIN_CORRELATED:
            correlation = {}
            for name in modelled:
                pairs = [(g.value(o.params[g.name]), o.metrics[name]) for o in scored
                         if name in o.metrics and math.isfinite(o.metrics[name])]
                correlation[name] = _spearman(pairs)
        out.append({"name": g.name, "levels_visited": len(visited), "levels": g.count,
                    "top_span": [g.text(min(span)), g.text(max(span))] if span else None, "at_bound": at_bound,
                    "no_value": thirds, "correlation": correlation})
    return out


def _spearman(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < MIN_CORRELATED:
        return None
    x, y = np.array(pairs, dtype=float).T
    if np.ptp(x) == 0 or np.ptp(y) == 0:
        return None
    return _num(float(stats.spearmanr(x, y).statistic))


def _failures(spec: Spec, rows: list[Observation], grid: list[_Grid]) -> dict[str, Any]:
    notes: dict[str, str] = {}
    by_status: dict[str, int] = {}
    for o in rows:
        if o.status != "ok":
            by_status[o.status] = by_status.get(o.status, 0) + 1
    partial = [o for o in rows if o.status == "metric_failed"]
    missing = {m.name: sum(1 for o in partial if not math.isfinite(o.metrics.get(m.name, math.nan))) for m in spec.metrics}
    unscored = np.array([o.status not in SCORED for o in rows], dtype=bool)
    separating = None
    if not rows:
        notes["separating"] = "no observation"
    elif unscored.all() or not unscored.any():
        notes["separating"] = "every point gave a value" if not unscored.any() else "no point gave a value"
    else:
        splits = [s for g in grid if (s := _best_split(g, rows, unscored)) is not None]
        splits.sort(key=lambda s: -s["gini_decrease"])          # stable: the spec's order breaks ties
        separating = splits[:SEPARATING]
        if not separating:
            notes["separating"] = f"no split leaves {MIN_SIDE} points on each side and separates them"
    return {"by_status": dict(sorted(by_status.items())), "missing_in_partial": missing,
            "partial_points": len(partial), "separating": separating, "notes": notes}


def _best_split(g: _Grid, rows: list[Observation], unscored: np.ndarray) -> dict[str, Any] | None:
    levels = np.array([g.index(o.params[g.name]) for o in rows])
    n, best = len(rows), None
    total = _gini(unscored)
    for t in sorted(set(levels.tolist()))[:-1]:
        below = levels <= t
        n_lo = int(below.sum())
        if n_lo < MIN_SIDE or n - n_lo < MIN_SIDE:
            continue
        decrease = total - n_lo / n * _gini(unscored[below]) - (n - n_lo) / n * _gini(unscored[~below])
        if decrease > 1e-12 and (best is None or decrease > best[0] + 1e-12):
            best = (decrease, t, below)
    if best is None:
        return None
    decrease, t, below = best

    def side(mask: np.ndarray) -> dict[str, Any]:
        return {"points": int(mask.sum()), "no_value": int(unscored[mask].sum()), "share": _num(float(unscored[mask].mean()))}

    return {"variable": g.name, "at_most": g.text(t), "from": g.text(int(levels[~below].min())), "below": side(below),
            "above": side(~below), "gini_decrease": _num(decrease)}


def _gini(labels: np.ndarray) -> float:
    p = float(labels.mean()) if len(labels) else 0.0
    return 2 * p * (1 - p)


def _strategy(spec: Spec, rows: list[Observation]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for o in rows:
        name = origin_source(o.origin)
        counts[name] = counts.get(name, 0) + 1
    out: dict[str, Any] = {"origins": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))), "metric_gp": None,
                           "notes": {}}
    if "metric_gp" in counts:
        out["metric_gp"], note = _metric_gp_region(spec, rows)
        if note:
            out["notes"]["metric_gp"] = note
    return out


def origin_source(origin: str) -> str:
    """What proposed a point: the strategy of ``suggest:<strategy>:...``, else the origin's first word (``start``,
    ``advice``, ``user``)."""
    parts = split_origin(origin)[0].split(":")
    return parts[1] if parts[0] == "suggest" and len(parts) > 1 else parts[0]


def _metric_gp_region(spec: Spec, rows: list[Observation]) -> tuple[dict[str, Any] | None, str | None]:
    """The search region as ``MetricGpSuggester.region_state`` rebuilds it, on the history with every advice suffix taken
    off (the replay reads origins without one), and the initial design points: ``init``, start points and an advice's
    start points (which count as start points do, section 6.2)."""
    from ic_opt.suggesters.metric_gp import MetricGpSuggester, candidates, region
    from ic_opt.suggesters.metric_gp.compose import Composer, metric_scales, true_arrays

    design = sum(1 for o in rows if (base := split_origin(o.origin)[0]) in ("start", "suggest:metric_gp:init")
                 or base.startswith("advice:"))
    if space.grid_size(spec) <= candidates.MAX_CANDIDATES:
        return ({"region": None, "design_points": design},
                f"the whole grid ({space.grid_size(spec)} points) is the candidate set: no search region")
    plain = [o.model_copy(update={"origin": split_origin(o.origin)[0]}) for o in rows]
    state = MetricGpSuggester().region_state(spec, plain)
    if state.ended:
        position = state.centres[-1]
    else:
        position = region.centre(Composer(spec), plain, state, metric_scales(spec, plain), true_arrays(spec, plain))
    return ({"region": {"index": state.index, "side": _num(state.length), "successes": state.successes,
                        "failures": state.failures, "centre": plain[position].obs_id if position is not None else None,
                        "ended": state.ended, "regions_ended": len(state.centres)},
             "design_points": design}, None)


def _advice(rows: list[Observation], advice: Sequence[dict]) -> list[dict[str, Any]]:
    adopted = [r for r in advice if r.get("event") == "adopt"]
    revoked = {r.get("id"): r for r in advice if r.get("event") == "revoke"}
    out = []
    for i, row in enumerate(adopted):
        ident = row.get("id")
        since = int(row.get("since", 0))
        under = [o for o in rows if split_origin(o.origin)[1] == ident]
        starts = [o for o in rows if split_origin(o.origin)[0] == f"advice:{ident}"]
        mine = {o.obs_id for o in under} | {o.obs_id for o in starts}
        others = [o for o in rows[since:] if o.obs_id not in mine]
        if ident in revoked:
            status = "revoked"
        elif i + 1 < len(adopted):
            status = f"superseded by {adopted[i + 1].get('id')}"
        else:
            status = "in effect"
        out.append({"id": ident, "row": dict(row), "status": status,
                    "revoke": dict(revoked[ident]) if ident in revoked else None,
                    "points": len(under), "best": _best_fom(under), "others_since": len(others),
                    "best_others_since": _best_fom(others),
                    "start_points": [{"id": o.obs_id, "status": o.status, "objective": _num(o.fom)} for o in starts]})
    return out


def _best_fom(rows: list[Observation]) -> float | None:
    feasible = [o for o in rows if o.feasible and o.objective is not None]
    return _num(min(feasible, key=lambda o: o.objective).fom) if feasible else None


def _operating_points(rows: list[Observation], feasible: list[Observation]) -> dict[str, Any]:
    start = next((o for o in rows if split_origin(o.origin)[0] == "start"), None)
    notes = {}
    if not feasible:
        notes["best"] = "no feasible point"
    if start is None:
        notes["start"] = "no start point (the design as exported) was evaluated"
    return {"best": _tables(feasible[0]) if feasible else None, "start": _tables(start) if start else None,
            "notes": notes}


def _tables(o: Observation) -> dict[str, Any]:
    """Per child the operating-point table of section 4; ``None`` where the store holds none. Read defensively: the
    field arrives with T17.5, and a store written before it has no such field."""
    return {"id": o.obs_id, "children": {key: _table(getattr(ch, "operating_points", None))
                                         for key, ch in sorted(o.children.items())}}


def _table(table: dict[str, dict[str, float]] | None) -> dict[str, dict[str, float]] | None:
    if table is None:
        return None
    return {inst: {q: _num(values[q]) for q in QUANTITIES if q in values} for inst, values in sorted(table.items())}


# -- small pieces -------------------------------------------------------------------------------------------------------

class _Grid:
    """One variable's levels: index <-> text, and where a value lies in the range's own scale."""

    def __init__(self, variable) -> None:
        self.variable, self.name = variable, variable.name
        self.lower, self.unit = space.parse_scalar(variable.lower)
        self.step = space.parse_scalar(variable.step)[0]
        self.count = space.grid_count(variable)
        lo, hi = float(self.lower), float(self.lower + (self.count - 1) * self.step)
        self.log = bool(lo > 0 and hi / lo >= LOG_SPAN)
        self._lo, self._hi = lo, hi

    def index(self, text: str) -> int:
        return int((space.parse_scalar(text)[0] - self.lower) / self.step)

    def text(self, index: int) -> str:
        return space.format_value(self.lower + Decimal(index) * self.step, self.unit)

    def value(self, text: str) -> float:
        return float(space.parse_scalar(text)[0])

    def third(self, text: str) -> str:
        if self._hi == self._lo:
            return "lower"
        v = self.value(text)
        u = (math.log(v / self._lo) / math.log(self._hi / self._lo)) if self.log else (v - self._lo) / (self._hi - self._lo)
        return ("lower", "middle", "upper")[min(int(3 * u), 2)]


def _modelled(spec: Spec) -> list[str]:
    """The metrics the constraints or the objective name, in spec order (``metric_gp``'s ``modelled_metrics``, which
    lives in a package that loads the strategy's models)."""
    named = {c.metric for c in spec.constraints}
    if spec.objective is not None:
        tree = ast.parse(spec.objective.expression, mode="eval")
        called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        named |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in called}
    return [m.name for m in spec.metrics if m.name in named]


def _spread(values: list[float]) -> float | None:
    """The interquartile range of the finite values; None when there are none or it is 0."""
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return None
    q1, q3 = np.percentile(finite, [25, 75])
    return float(q3 - q1) or None


def _num(value: float | None) -> float | None:
    """A JSON number: ``None`` for a missing or non-finite value."""
    return float(value) if value is not None and math.isfinite(value) else None


def _obs_order(o: Observation) -> tuple[str, int, str]:
    """Observation-number order, whatever the zero padding (``obs_9`` before ``obs_10``)."""
    match = _OBS_NUMBER.match(o.obs_id)
    return (match.group(1), int(match.group(2)), o.obs_id) if match else (o.obs_id, -1, o.obs_id)


# -- Markdown -----------------------------------------------------------------------------------------------------------

NOT_SAID = """\
- Every number is counted or computed from the points evaluated so far; none is a model's prediction.
- A rank correlation says that a metric tended to rise or fall with a variable over the points found. It is not a \
cause: variables that moved together share it, and it says nothing outside the points found.
- A suggested range describes where the best points found lie, not where the optimum is. A range that reaches the \
spec's bound says the best points lie at it; only the user widens the spec's range.
- A split says where points gave no value, not why."""


def markdown(d: dict[str, Any]) -> str:
    """The digest for a reader who has not seen the project: tables, values with their units."""
    counts, problem = d["counts"], d["problem"]
    head = f"{counts['points']} points · " + (" · ".join(f"{k} {v}" for k, v in counts["by_status"].items()) or "none")
    head += f" · {counts['simulations']} simulations" + (f" · step `{d['step']}`" if d["step"] else "")
    parts = [f"# Run digest — {d['project']}", head,
             "## What is optimized", _md_problem(problem),
             "## How far the run is", _md_progress(d["progress"]),
             "## What is in the way", _md_constraints(d["constraints"]),
             "## Where the good points are", _md_variables(d),
             "## What failed and where", _md_failures(d["failures"]),
             "## Strategy", _md_strategy(d["strategy"]),
             "## Advice", _md_advice(d["advice"]),
             "## Operating points of the best point", _md_operating_points(d["operating_points"]),
             "## What these numbers are not", NOT_SAID]
    return "\n\n".join(parts) + "\n"


def ranges_markdown(entry: dict[str, Any]) -> str:
    """``suggested_ranges`` as a table (the report's "Where the best points are" too)."""
    if entry["ranges"] is None:
        return f"_{entry['notes']['ranges']}_"
    lines = [(f"The span of the {entry['points']} best feasible points, one level wider on each side, inside the spec's "
              "range. A range that reaches the spec's bound: the spec's range, which only the user changes, may be too "
              "narrow there."), "",
             _row(["variable", "spec range", "best points span", "suggested range", "levels", "reaches spec bound"]),
             _rule(6)]
    for r in entry["ranges"]:
        lines.append(_row([r["variable"], f"{r['lower']} .. {r['upper']}", f"{r['span'][0]} .. {r['span'][1]}",
                           f"{r['suggested'][0]} .. {r['suggested'][1]}", f"{r['suggested_levels']} of {r['levels']}",
                           r["reaches_bound"] or ""]))
    return "\n".join(lines)


def _md_problem(p: dict[str, Any]) -> str:
    objective = p["objective"]
    lines = [f"- objective: {objective['direction']} `{objective['expression']}`" if objective
             else "- objective: none (meeting every constraint is the goal)",
             "- constraints: " + (", ".join(f"`{c['text']}`" for c in p["constraints"]) or "none"),
             "- corners evaluated: " + (", ".join(p["corners"]) or "none"), "",
             _row(["variable", "lower", "upper", "step", "levels", "scale"]), _rule(6)]
    lines += [_row([v["name"], v["lower"], v["upper"], v["step"], str(v["levels"]), v["scale"]]) for v in p["variables"]]
    return "\n".join(lines)


def _md_progress(p: dict[str, Any]) -> str:
    first = p["first_feasible"]
    lines = [f"- first feasible point: #{first['index']} (`{first['id']}`)" if first else "- first feasible point: none yet"]
    best = p["best"]
    if best is None:
        lines.append(f"- best feasible point: {p['notes']['best']}")
        return "\n".join(lines)
    lines += [(f"- best feasible point: #{best['index']} `{best['id']}` (origin `{best['origin']}`), objective "
               f"**{quantity(best['objective'], '')}** ({p['direction'] or 'no objective'})"),
              "- its parameters: " + ", ".join(f"{k}={v}" for k, v in best["params"].items()), "",
              _row(list(best["metrics"])), _rule(len(best["metrics"])),
              _row([quantity(m["value"], m["unit"]) for m in best["metrics"].values()]), "",
              "Best objective after each batch (the batches where it changed, and the last):", "",
              _row(["batch", "points so far", "best objective", "point"]), _rule(4)]
    batches, previous = p["batches"], None
    for i, b in enumerate(batches):
        if b["best_id"] != previous or i == len(batches) - 1:
            lines.append(_row([b["batch"], str(b["points"]), quantity(b["best"], ""),
                               f"`{b['best_id']}`" if b["best_id"] else "—"]))
        previous = b["best_id"]
    return "\n".join(lines)


def _md_constraints(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_no constraints_"
    lines = [_row(["constraint", "met / scored", "fail only this one", "margin of the best point", "closest failing point"]),
             _rule(5)]
    for c in rows:
        closest = c["closest_failing"]
        lines.append(_row([f"`{c['constraint']}`", f"{c['met']} / {c['scored']}", str(c["fails_only_this"]),
                           _margin_md(c["best_margin"], c["unit"]),
                           f"{_margin_md(closest, c['unit'])} `{closest['id']}`" if closest else "none fails it"]))
    lines += ["", ("A margin is positive inside the constraint; in brackets, divided by the metric's interquartile range "
                   "over the points (IQR). Scored: the points that gave every metric.")]
    return "\n".join(lines)


def _margin_md(entry: dict[str, Any] | None, unit: str) -> str:
    if entry is None:
        return "—"
    normalized = entry["normalized"]
    return quantity(entry["margin"], unit) + (f" ({normalized:+.2g} IQR)" if normalized is not None else "")


def _md_variables(d: dict[str, Any]) -> str:
    ranges = d["suggested_ranges"]
    suggested = {r["variable"]: r for r in ranges["ranges"] or []}
    lines = [(f"Suggested range: the span of the {ranges['points']} best feasible points, one level wider on each side, "
              "inside the spec's range. *Reaches*: the best points lie at the spec's bound; the spec's range, which only "
              "the user changes, may be too narrow there." if ranges["ranges"] is not None
              else f"No suggested range: it {ranges['notes']['ranges']}.")
             + (" *No value by third*: points that gave no value / points, in the lower · middle · upper third of the "
                "range."), "",
             _row(["variable", "levels visited", f"span of the top {d['top']}", "suggested range", "reaches",
                   "best at bound", "no value by third"]), _rule(7)]
    for v in d["variables"]:
        thirds = " · ".join(f"{t['no_value']}/{t['points']}" if t["points"] else "—" for t in v["no_value"].values())
        span = f"{v['top_span'][0]} .. {v['top_span'][1]}" if v["top_span"] else "—"
        r = suggested.get(v["name"])
        lines.append(_row([v["name"], f"{v['levels_visited']} of {v['levels']}", span,
                           f"{r['suggested'][0]} .. {r['suggested'][1]}" if r else "—",
                           (r["reaches_bound"] or "") if r else "", v["at_bound"] or "", thirds]))
    lines += ["", "### Rank correlation with the metrics the spec names", ""]
    correlated = [v["correlation"] for v in d["variables"] if v["correlation"] is not None]
    metrics = list(correlated[0]) if correlated else []
    if not correlated:
        lines.append(f"_needs at least {MIN_CORRELATED} scored points_")
    elif not metrics:
        lines.append("_no metric is named by the constraints or the objective_")
    else:
        lines += ["Spearman's rho over the scored points: +1 the metric rises with the variable, -1 it falls.", "",
                  _row(["variable", *metrics]), _rule(1 + len(metrics))]
        lines += [_row([v["name"], *(f"{r:+.2f}" if (r := v["correlation"][m]) is not None else "—" for m in metrics)])
                  for v in d["variables"]]
    return "\n".join(lines)


def _md_failures(f: dict[str, Any]) -> str:
    lines = ["- statuses other than ok: " + (", ".join(f"{k} {v}" for k, v in f["by_status"].items()) or "none")]
    if f["partial_points"]:
        missing = ", ".join(f"{k} {v}" for k, v in sorted(f["missing_in_partial"].items(), key=lambda kv: -kv[1]) if v)
        lines.append(f"- metrics missing on the {f['partial_points']} points that ran but gave only some values: {missing}")
    if not f["separating"]:
        lines.append(f"- where points gave no value: {f['notes'].get('separating', 'no split separates them')}")
        return "\n".join(lines)
    lines += ["", ("Where points gave no value: the variables one split of which separates them from the scored points "
                   "best (no value / points on each side)."), "",
              _row(["variable", "at most", "from", "no value below", "no value from"]), _rule(5)]
    for s in f["separating"]:
        lines.append(_row([s["variable"], s["at_most"], s["from"], _share(s["below"]), _share(s["above"])]))
    return "\n".join(lines)


def _share(side: dict[str, Any]) -> str:
    return f"{side['no_value']} / {side['points']} ({100 * side['share']:.0f}%)"


def _md_strategy(s: dict[str, Any]) -> str:
    lines = ["- points by origin: " + (", ".join(f"{k} {v}" for k, v in s["origins"].items()) or "none")]
    gp = s["metric_gp"]
    if gp is not None:
        r = gp["region"]
        if r is None:
            lines.append(f"- metric_gp: {s['notes']['metric_gp']}; initial design points {gp['design_points']}")
        else:
            lines += [f"- metric_gp search region {r['index']} (regions ended before it: {r['regions_ended']}), centred on "
                      f"`{r['centre']}`, side {r['side']:.4g} of the unit cube"
                      + (", ended: the next batch starts a new region" if r["ended"] else ""),
                      (f"- batches in a row that improved on the region's best: {r['successes']}, that did not: "
                       f"{r['failures']}; initial design points (start points and space-filling): {gp['design_points']}")]
    return "\n".join(lines)


def _md_advice(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_no advice given_"
    lines = [_row(["advice", "given by", "since point", "status", "what", "points under it", "best under it",
                   "best of the others since", "its start points"]), _rule(9)]
    for a in rows:
        r = a["row"]
        what = "; ".join(part for part in (
            f"start {len(r['start'])}" if r.get("start") else "",
            "ranges " + ", ".join(f"{k} {lo}..{hi}" for k, (lo, hi) in r["ranges"].items()) if r.get("ranges") else "",
            "fixed " + ", ".join(f"{k}={v}" for k, v in r["fixed"].items()) if r.get("fixed") else "",
            "vary " + ", ".join(r["vary"]) if r.get("vary") else "") if part)
        starts = ", ".join(f"`{s['id']}` {s['status']} {quantity(s['objective'], '')}" for s in a["start_points"]) or "—"
        lines.append(_row([a["id"], str(r.get("author", "")), str(r.get("since", "")), a["status"], what or "—",
                           str(a["points"]), quantity(a["best"], ""), quantity(a["best_others_since"], ""), starts]))
    lines += ["", *(f"- {a['id']}: {a['row'].get('reason', '')}" for a in rows)]
    return "\n".join(lines)


def _md_operating_points(op: dict[str, Any]) -> str:
    best = op["best"]
    if best is None:
        return f"_{op['notes']['best']}_"
    if all(table is None for table in best["children"].values()):
        return (f"_none recorded for `{best['id']}`: its results hold no operating points (a store written before ic-opt "
                "recorded them, a child that is not a Spectre simulation, or a netlist that asked for none)_")
    lines: list[str] = []
    for child, table in best["children"].items():
        if table is None:
            lines.append(f"- `{child}` of `{best['id']}`: none recorded")
            continue
        present = [q for q in QUANTITIES if any(q in values for values in table.values())]
        lines += ["", f"`{child}` of `{best['id']}`:", "", _row(["instance", *present]), _rule(1 + len(present))]
        lines += [_row([inst, *(quantity(values.get(q), QUANTITY_UNITS.get(q, "")) for q in present)])
                  for inst, values in table.items()]
    if op["start"] is not None and any(t is not None for t in op["start"]["children"].values()):
        lines += ["", f"The start point's tables (`{op['start']['id']}`, the design as exported) are in `digest.json`."]
    return "\n".join(lines).strip() or "_the best point has no child results_"


def _row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(str(c).replace("|", "\\|") for c in cells) + " |"


def _rule(n: int) -> str:
    return "|" + " --- |" * n
