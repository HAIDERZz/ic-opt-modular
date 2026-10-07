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
- *the history size a point was proposed at*: the batch key of its origin where it has one (a ``metric_gp`` or TuRBO
  batch); for any other point (a start point, an advice's start point, the initial design, OpenBox, user points) its
  position among every observation handed to :func:`digest`, before the step is picked. The position equals the history
  size the strategy was handed while the store holds this one problem and the run adopted no rows from elsewhere; an
  advice's ``since`` counts the same way.
- *an advice's period* (T17.3b specification, 2.1): the history sizes from its ``since`` up to, not including, the
  ``since`` of the row that ended it -- its ``revoke`` row or the next ``adopt`` row, whichever comes first -- or on
  while it is in effect: the batches :func:`ic_opt.advice.in_effect` gives it. *The others* of an advice: the points
  proposed at a history size inside its period that are neither under it (``@<id>``) nor its start points.
- *the best then*: a feasible point whose objective is lower than that of every feasible point before it in
  observation-number order.
- *what the unscored points said*: the texts of their ``issues`` as stored, each counted once per point that holds it;
  not the line of a point stopped early that says how many of its children were not simulated (below).
- *stopped early* (T17.8): a point whose ``not_run`` names children it did not run: the schedule stopped it at its first
  failing child. ``counts`` gives how many (``stopped_early``), those children added up (``simulations_not_run``) and
  where they stopped (``stopped_at``, T17.9: per child ``<unit>/<corner>`` the points stopped after it, most first; the
  child ``sim.corner.stopper`` names, as the point's "not simulated" line does). Such a point holds only the children
  that ran, so its metrics are those of the corners it reached. ``stopped_at_device`` (N-63): how many of them a device
  child stopped -- an EM device's or a library row's measurement, which runs first, so such a point ran no testbench
  (``sim.corner.stopped_at_device``).

The value helpers (units, SI prefixes, a constraint as a reader says it, a point's value per corner) live here and the
report (``blocks/analyze.py``) imports them: importing ``ic_opt.blocks`` loads every block and the strategies'
libraries, and this module imports only numpy, scipy and the core of ic-opt. One exception, taken only when an origin
names ``metric_gp``: the search region is what ``MetricGpSuggester.region_state`` gives, and that package loads
scikit-learn (a declared dependency).

Version 3 (``docs/refactor/T17_10_DIGEST_SPEC.md``: what an agent outside the tool needs to advise) adds entries, and
changes one of version 2's: the issue texts of ``failures.messages`` are kept to their first ``TEXT_LIMIT`` (200)
characters -- a run's own output, but a simulator's message can go on to name files of the host. Nothing else the
digest carries comes from the project's or the host's text: no file path, no metric's expression, nothing of the
spec's ``simulator`` block or its descriptions (``tests/ic_opt/test_digest_leak.py``). The variables' importance takes
scikit-learn's mutual information estimate, imported only when there are points enough to compute it. Its definitions:

- *a stall* (``progress.stall``): counted in the batches of ``progress.batches``. The best improves in a batch where the
  point it names changes, the first feasible point included; ``stalled`` from ``STALL_BATCHES`` batches without an
  improvement on (TopoSizing asks its agent after three). ``region_restarts``: the search regions of ``metric_gp`` whose
  anchor (``anchor:<r>:<k>``) was evaluated -- the first region starts from the initial design and has none -- and
  ``last_restart_batch`` the batch the last of those anchors belongs to; ``None`` when no origin names ``metric_gp``.
- *an advice's result* (``advice[i]``): ``improved_best`` compares the run's best feasible objective over the points
  proposed before the advice's period with the best over those proposed up to its end (``by``: how much, in the
  objective's own units; ``None`` when there was no feasible point before it). ``share_kept`` divides the points
  proposed in the period into those that came from the advice (under it, and its start points) and the free ones (the
  others of its period). ``verdict``: ``helped`` when the advice's side has the better best (a best where the other side
  has none is better) and the run's best improved, ``no_help`` when neither, ``mixed`` when one of the two; ``None``
  while the period holds no point.
- *a refused advice* (``advice_refused``): a row ``ic-opt advise`` wrote for an advice file it refused
  (``ic_opt.advice.refusal``, ``status: "refused"``): its id (``r1``, ``r2``, ...), the history size it was given at and
  the refusal's message. What the file said is not copied; the row keeps it (``raw``). No reader of adopted advice takes
  such a row for one.
- *a variable's importance* (``variables[].importance``): for a variable of more than one level and per metric the
  constraints or the objective name, the mutual information between the variable's unit coordinate (``metric_gp``'s
  ``Coords``: 0 to 1 over the range, logarithmic where it spans a decade) and the metric's values over the points that
  gave one (``sklearn.feature_selection.mutual_info_regression``, ``n_neighbors=3``, ``random_state=0``: a k-nearest-
  neighbour estimate in nats, no model fitted); ``None`` for a metric fewer than ``MIN_IMPORTANCE`` points gave, and for
  the whole entry when every metric is so. ``rank``: by the sum over the metrics, ties in the spec's order. A
  constraint's ``violations``: the points whose value of its metric (the worst over the scored corners, as the
  constraint is judged) violates it, whatever their status.
- *where points failed* (``failures.by_stage``): a point counts under the stage of its ``failed:<stage>`` status (its
  first failed child's, as ``sim.corner.aggregate`` sets it; a child's where the point's status names none), else under
  its status ``metric_failed`` or ``constraint_failed``; a point stopped early counts in ``stopped_early`` besides.

Version 4 (T18.2B, ``docs/refactor/T18_2B_LIBRARY_DEVICE_SPEC.md``, section 5) keeps every entry of version 3 and adds
``library``: ``None`` for a spec without library devices; else per library device its table (the stratum), the working
frequency, the margin, the rule that ranks a combination's rows (``prefer``), the index columns its variables map, the
combinations on the grid (``combinations`` of ``of``, holding ``rows`` rows) and how many of them the run visited
(``visited``: distinct combinations among the points); and for each of the ``top`` best feasible points, the best first,
the row each device took -- part, obs id, its geometry, its electrical values, its footprint -- as the device child
recorded it (``ChildResult.library_row``). The table's size is the library's: read on the machine computing the digest
(``ic_opt.library.link``, the process's resolution), else ``None`` with a note. Neither the library's root nor an sNp
path reaches the digest.
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
from ic_opt.sim.corner import (
    NOT_SIMULATED,
    metrics_per_corner,
    scored_corners,
    stopped_at_device,
    stopper,
)
from ic_opt.space import split_origin
from ic_opt.spec import Spec

DIGEST_VERSION = 4
SCORED = ("ok", "constraint_failed")
MIN_CORRELATED = 10                # scored points below which no rank correlation is given
MIN_SIDE = 5                       # points on either side of a split
MIN_FEASIBLE_RANGES = 3            # feasible points below which no range is suggested
SEPARATING = 3                     # variables listed as separating scored from unscored points
MESSAGES = 3                       # issue texts listed for the points that gave no value
TEXT_LIMIT = 200                   # characters of an issue text the digest keeps (T17.10 specification, 1.6)
STALL_BATCHES = 3                  # batches without improvement from which a run is stalled (T17.10 specification, 1.1)
MIN_IMPORTANCE = 20                # points with a metric's value below which no mutual information is given (1.3)
MI_NEIGHBOURS = 3                  # the k of the mutual information's k-nearest-neighbour estimate (1.3)
STAGES = ("render", "spectre", "ocean", "pcell", "emx", "bind_nport", "measure")   # failed:<stage>, always counted (1.4)
REGION_WEIGHTS = (0.2, 5.0)        # metric_gp's per-variable weights of the region's side (region.WEIGHT_CLIP)
QUANTITIES = ("region", "ids", "vgs", "vds", "vbs", "vth", "vdsat", "gm", "gds", "gmoverid", "cgs", "cgd")   # section 4
QUANTITY_UNITS = {"ids": "A", "vgs": "V", "vds": "V", "vbs": "V", "vth": "V", "vdsat": "V", "gm": "S", "gds": "S",
                  "gmoverid": "1/V", "cgs": "F", "cgd": "F"}

OPS = {"gt": ">", "ge": "≥", "lt": "<", "le": "≤"}
SI_UNITS = {"Hz", "H", "F", "s", "A", "V", "W", "Ohm", "m", "S"}               # units that take an SI prefix when printed
SI_PREFIXES = [(1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"),
               (1e-12, "p"), (1e-15, "f")]
_BATCH_KEY = re.compile(r"^suggest:.*:(\d+)$")
_OBS_NUMBER = re.compile(r"^(.*?)(\d+)$")
_ANCHOR = re.compile(r"^suggest:metric_gp:anchor:(\d+):\d+$")     # the first point of a metric_gp search region after the first


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
    """The digest of ``observations`` (only step ``step``'s when given) as a JSON-ready dict, ``"digest_version": 4``.
    ``advice``: the rows of ``.icopt/advice.jsonl`` in file order (section 2); ``top``: how many of the best feasible
    points the spans and suggested ranges describe. The order of ``observations`` does not matter: they are taken in
    observation-number order, as the store and the strategies take them."""
    ordered = sorted(observations, key=_obs_order)
    position = {o.obs_id: i for i, o in enumerate(ordered)}
    rows = [o for o in ordered if step is None or o.step == step]
    grid = [_Grid(v) for v in spec.variables]
    feasible = sorted((o for o in rows if o.feasible), key=lambda o: o.objective if o.objective is not None else math.inf)
    sizes = [key if (key := batch_key(o.origin)) is not None else position[o.obs_id] for o in rows]
    return {
        "digest_version": DIGEST_VERSION,
        "project": spec.project,
        "step": step,
        "top": top,
        "problem": _problem(spec, rows, grid),
        "counts": _counts(spec, rows),
        "progress": _progress(spec, rows, feasible),
        "constraints": _constraints(spec, rows, feasible),
        "variables": _variables(spec, rows, feasible[:top], grid),
        "failures": _failures(spec, rows, grid),
        "suggested_ranges": suggested_ranges(spec, rows, top=top),
        "strategy": _strategy(spec, rows),
        "advice": _advice(rows, sizes, advice, grid, feasible[0] if feasible else None),
        "advice_refused": _advice_refused(advice),
        "operating_points": _operating_points(rows, feasible),
        "library": _library(spec, rows, feasible[:top], grid),
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
    corners = sorted(set().union(*(o.corners() for o in rows)))      # a point stopped early: every corner it was to run at
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


def _counts(spec: Spec, rows: list[Observation]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    per_step: dict[str, int] = {}
    stopped_at: dict[str, int] = {}
    for o in rows:
        by_status[o.status] = by_status.get(o.status, 0) + 1
        per_step[o.step] = per_step.get(o.step, 0) + 1
        if (child := stopper(spec, o)) is not None:
            stopped_at[child] = stopped_at.get(child, 0) + 1
    unknown = sum(1 for o in rows if o.simulations is None)
    return {"points": len(rows), "by_status": dict(sorted(by_status.items())),
            "simulations": sum(o.simulations or 0 for o in rows), "per_step": per_step,
            "stopped_early": sum(1 for o in rows if o.not_run), "simulations_not_run": sum(len(o.not_run) for o in rows),
            "stopped_at": dict(sorted(stopped_at.items(), key=lambda kv: (-kv[1], kv[0]))),
            "stopped_at_device": sum(1 for o in rows if o.not_run and stopped_at_device(spec, o)),
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
        "stall": _stall(rows, batches),
        "notes": notes,
    }


def _stall(rows: list[Observation], batches: list[dict[str, Any]]) -> dict[str, Any]:
    """``progress.stall`` (T17.10 specification, 1.1): how many batches ago the run's best feasible objective last
    improved and the first feasible point came (``None`` before it), whether that is a stall, and how often the search
    region of ``metric_gp`` restarted. ``batches``: ``progress.batches``, whose ``best_id`` changes where the best
    improved."""
    improved = [i for i, b in enumerate(batches)
                if b["best_id"] is not None and b["best_id"] != (batches[i - 1]["best_id"] if i else None)]
    last = len(batches) - 1
    since = last - improved[-1] if improved else None
    anchors: dict[int, int] = {}                     # region index -> position of its anchor
    for i, o in enumerate(rows):
        if match := _ANCHOR.match(split_origin(o.origin)[0]):
            anchors.setdefault(int(match.group(1)), i)
    restart_batch = None
    if anchors:                                      # the batch of the last region's anchor: where its batch ends
        anchor = rows[anchors[max(anchors)]]
        key = batch_key(anchor.origin)
        end = max(i for i, o in enumerate(rows) if o.step == anchor.step and batch_key(o.origin) == key)
        restart_batch = next((int(b["batch"]) for b in batches if b["points"] == end + 1), None)
    return {"batches_since_improvement": since, "batches_since_first_feasible": last - improved[0] if improved else None,
            "stalled": since is not None and since >= STALL_BATCHES,
            "region_restarts": len(anchors) if any(origin_source(o.origin) == "metric_gp" for o in rows) else None,
            "last_restart_batch": restart_batch}


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
            "violations": sum(1 for o in rows if (v := constraint_value(spec, c, o)) is not None and margin(c, v) < 0),
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
    importance = _importance(spec, rows, grid)
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
                    "no_value": thirds, "correlation": correlation, "importance": importance[g.name]})
    return out


def _importance(spec: Spec, rows: list[Observation], grid: list[_Grid]) -> dict[str, dict[str, Any] | None]:
    """``variables[].importance`` (T17.10 specification, 1.3), per variable name: per modelled metric the mutual
    information between the variable's unit coordinate and the metric's values over the points that gave one, those
    points' count (``rows_used``), the sum over the metrics (``total``) and the variable's ``rank`` by it. Nothing is
    fitted: the digest is read while a run goes. ``None`` for a variable of one level (it takes no part), and for every
    variable when no metric has ``MIN_IMPORTANCE`` points with a value."""
    names = _modelled(spec)
    active = [g for g in grid if g.count > 1]
    used = {name: [o for o in rows if math.isfinite(o.metrics.get(name, math.nan))] for name in names}
    measured = [name for name in names if len(used[name]) >= MIN_IMPORTANCE]
    out: dict[str, dict[str, Any] | None] = dict.fromkeys((g.name for g in grid), None)
    if not active or not measured:
        return out
    # scikit-learn, a declared dependency: loaded only here, so a digest with too few points does without it
    from sklearn.feature_selection import mutual_info_regression

    mi = {}
    for name in measured:                              # one call per metric: each variable's estimate is its own
        x = np.array([[g.coordinate(o.params[g.name]) for g in active] for o in used[name]])
        y = np.array([o.metrics[name] for o in used[name]])
        mi[name] = mutual_info_regression(x, y, n_neighbors=MI_NEIGHBOURS, random_state=0)
    totals = {g.name: float(sum(mi[name][j] for name in measured)) for j, g in enumerate(active)}
    ranks = {g.name: r for r, g in enumerate(sorted(active, key=lambda g: -totals[g.name]), 1)}   # stable: spec order
    for j, g in enumerate(active):
        out[g.name] = {"rank": ranks[g.name], "total": _num(totals[g.name]),
                       "mi": {name: _num(float(mi[name][j])) if name in mi else None for name in names},
                       "rows_used": {name: len(used[name]) for name in names}}
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
    said: dict[str, int] = {}
    for o in rows:
        if o.status not in SCORED:
            for text in _causes(o):
                said[text] = said.get(text, 0) + 1
    messages = [{"text": _clip(text), "count": count}
                for text, count in sorted(said.items(), key=lambda kv: (-kv[1], kv[0]))[:MESSAGES]]
    return {"by_status": dict(sorted(by_status.items())), "missing_in_partial": missing,
            "partial_points": len(partial), "separating": separating, "messages": messages,
            "by_stage": _by_stage(rows), "notes": notes}


def _by_stage(rows: list[Observation]) -> dict[str, int]:
    """``failures.by_stage`` (T17.10 specification, 1.4): the points counted by what failed them -- the stage of their
    ``failed:<stage>`` status (every stage of ``STAGES`` present, 0 when none failed there; another stage, ``extract`` or
    ``predict``, where one did), an expression that gave no value (``metric_failed``), a constraint
    (``constraint_failed``) -- and the points stopped early (``not_run``), which count under what failed them too."""
    stages: dict[str, int] = dict.fromkeys(STAGES, 0)
    others: dict[str, int] = {}
    ends = {"metric_failed": 0, "constraint_failed": 0, "stopped_early": 0}
    for o in rows:
        stage = _failed_stage(o)
        if stage is not None:
            counts = stages if stage in stages else others
            counts[stage] = counts.get(stage, 0) + 1
        elif o.status in ("metric_failed", "constraint_failed"):
            ends[o.status] += 1
        if o.not_run:
            ends["stopped_early"] += 1
    return {**stages, **dict(sorted(others.items())), **ends}


def _failed_stage(o: Observation) -> str | None:
    """The stage that failed a point: the one its status names (its first failed child's), else a child's."""
    for status in (o.status, *(ch.status for ch in o.children.values())):
        if status.startswith("failed:"):
            return status.split(":", 1)[1]
    return None


def _clip(text: str) -> str:
    """An issue text as the digest keeps it: at most ``TEXT_LIMIT`` characters, the last of a longer one an ellipsis.
    Texts are counted whole; only what is printed is cut."""
    return text if len(text) <= TEXT_LIMIT else text[: TEXT_LIMIT - 1] + "…"


_CHILD_SAID = re.compile(r"metric (\w+) failed")
_POINT_SAID = re.compile(r"metric (\w+) missing or non-finite$")
_NOT_SIMULATED = NOT_SIMULATED.split("{", 1)[0]          # how the line of a point stopped early begins


def _causes(o: Observation) -> list[str]:
    """A point's issue texts, each once, without the point's own "metric X missing or non-finite" where a child's text
    says why X failed: the two are one cause, and the child's text is the one that names it. Nor, for a point stopped
    early (``not_run``), its line saying what was not simulated: not why the point gave no value (``counts`` has it)."""
    texts = [text for text in dict.fromkeys(o.issues) if not (o.not_run and text.startswith(_NOT_SIMULATED))]
    named = {m.group(1) for text in texts if (m := _CHILD_SAID.search(text))}
    return [text for text in texts if not ((m := _POINT_SAID.search(text)) and m.group(1) in named)]


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
        try:
            out["metric_gp"], note = _metric_gp_region(spec, rows)
        except (ValueError, OSError, KeyError):
            if not spec.library_devices:
                raise
            # the region lies on the valid points, which a library device's table gives (T18.2B); its message may name
            # the library's path, so it is not kept
            out["metric_gp"], note = None, "the library could not be read where this digest was computed: no search region"
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


def _advice(rows: list[Observation], sizes: list[int], advice: Sequence[dict], grid: list[_Grid],
            best: Observation | None) -> list[dict[str, Any]]:
    """Per adopted advice (T17.3b specification, 2.1-2.4): its row and status, its period, the points under it and the
    others of its period counted alike, its start points, and where the run's best point lies against its ranges; and
    (T17.10 specification, 1.2) whether the run's best improved in its period, the share of the period's points that
    came from it, and the verdict. ``sizes``: the history size each row was proposed at."""
    adopted = [r for r in advice if r.get("event") == "adopt"]
    revoked = {r.get("id"): r for r in advice if r.get("event") == "revoke"}
    best_then = _best_then(rows)
    out = []
    for i, row in enumerate(adopted):
        ident = row.get("id")
        since = int(row.get("since", 0))
        ends = [int(r.get("since", 0)) for r in (revoked.get(ident), adopted[i + 1] if i + 1 < len(adopted) else None)
                if r is not None]
        until = min(ends) if ends else None
        under = [o for o in rows if split_origin(o.origin)[1] == ident]
        starts = [o for o in rows if split_origin(o.origin)[0] == f"advice:{ident}"]
        mine = {o.obs_id for o in under} | {o.obs_id for o in starts}
        others = [o for o, k in zip(rows, sizes, strict=True)
                  if since <= k and (until is None or k < until) and o.obs_id not in mine]
        if ident in revoked:
            status = "revoked"
        elif i + 1 < len(adopted):
            status = f"superseded by {adopted[i + 1].get('id')}"
        else:
            status = "in effect"
        improved = _improved_best([o for o, k in zip(rows, sizes, strict=True) if k < since],
                                  [o for o, k in zip(rows, sizes, strict=True) if until is None or k < until])
        out.append({"id": ident, "row": dict(row), "status": status,
                    "revoke": dict(revoked[ident]) if ident in revoked else None,
                    "period": [since, until], "under": _side_counts(under), "others": _side_counts(others),
                    "start_points": [{"id": o.obs_id, "status": o.status, "objective": _num(o.fom) if o.feasible else None,
                                      "best_then": o.obs_id in best_then} for o in starts],
                    "best_at_bound": _best_at_bound(row, grid, best), "improved_best": improved,
                    "share_kept": _share_kept(under + starts, others), "verdict": _verdict(under + starts, others, improved)})
    return out


def _side_counts(rows: list[Observation]) -> dict[str, Any]:
    return {"points": len(rows), "feasible": sum(1 for o in rows if o.feasible),
            "no_value": sum(1 for o in rows if o.status not in SCORED), "best": _best_fom(rows)}


def _improved_best(before: list[Observation], through: list[Observation]) -> dict[str, Any]:
    """Whether the run's best feasible objective improved in an advice's period (1.2): the best of the points proposed
    before it against the best of those proposed up to its end, as the spec states the objective; ``by`` how much, in
    its own units (``None`` when nothing was feasible before: the first feasible point has no size of improvement)."""
    start, end = _best_of(before), _best_of(through)
    improved = end is not None and (start is None or end.objective < start.objective)
    by = abs(end.fom - start.fom) if improved and start is not None and None not in (start.fom, end.fom) else None
    return {"improved": improved, "from": _num(start.fom) if start else None, "to": _num(end.fom) if end else None,
            "by": _num(by)}


def _share_kept(advised: list[Observation], others: list[Observation]) -> dict[str, Any] | None:
    """Of the points proposed in an advice's period, the share that came from it and the free share (1.2)."""
    total = len(advised) + len(others)
    if not total:
        return None
    return {"points": total, "advised": _num(len(advised) / total), "free": _num(len(others) / total)}


def _verdict(advised: list[Observation], others: list[Observation], improved: dict[str, Any]) -> str | None:
    """``helped``, ``no_help`` or ``mixed`` (1.2): whether the advice's side found the better best, and whether the run's
    best improved in its period; ``None`` while the period holds no point."""
    if not advised and not others:
        return None
    mine, theirs = _best_of(advised), _best_of(others)
    better = mine is not None and (theirs is None or mine.objective < theirs.objective)
    if better and improved["improved"]:
        return "helped"
    return "mixed" if better or improved["improved"] else "no_help"


def _advice_refused(advice: Sequence[dict]) -> list[dict[str, Any]]:
    """``advice_refused`` (T17.10 specification, 1.5): per advice ``ic-opt advise`` refused, in file order, its id, the
    history size it was given at and why it was refused -- not what it said (``raw``, which the advice file keeps)."""
    return [{"id": r.get("id"), "since": r.get("since"), "reason": r.get("reason")} for r in advice
            if r.get("status") == "refused"]


def _best_of(rows: list[Observation]) -> Observation | None:
    """The feasible point of the lowest objective (minimization form) among ``rows``, the first of equals."""
    feasible = [o for o in rows if o.feasible and o.objective is not None]
    return min(feasible, key=lambda o: o.objective) if feasible else None


def _best_then(rows: list[Observation]) -> set[str]:
    """The points that were the run's best feasible point when they were evaluated (observation-number order)."""
    out, best = set(), None
    for o in rows:
        if o.feasible and o.objective is not None and (best is None or o.objective < best):
            best = o.objective
            out.add(o.obs_id)
    return out


def _best_at_bound(row: dict, grid: list[_Grid], best: Observation | None) -> list[dict[str, str]] | None:
    """The run's best feasible point against an advice's ranges (2.4): per variable and side, where its level is the
    advice's bound and that bound is not the spec's -- better points may lie beyond it, and only a wider advice looks
    there. ``None`` when there is no best point, the advice has no ranges, or the best point was neither proposed under
    the advice (``@<id>``, or one of its start points) nor lies inside its ranges."""
    ranges = row.get("ranges") or {}
    by_name = {g.name: g for g in grid}
    bounds = {name: (by_name[name].index(lo), by_name[name].index(hi)) for name, (lo, hi) in ranges.items()
              if name in by_name}
    if best is None or not bounds:
        return None
    base, suffix = split_origin(best.origin)
    under = suffix == row.get("id") or base == f"advice:{row.get('id')}"
    levels = {name: by_name[name].index(best.params[name]) for name in bounds}
    if not under and not all(lo <= levels[name] <= hi for name, (lo, hi) in bounds.items()):
        return None
    out = []
    for name, (lo, hi) in bounds.items():
        g = by_name[name]
        for side, bound, spec_bound in (("lower", lo, 0), ("upper", hi, g.count - 1)):
            if levels[name] == bound != spec_bound:
                out.append({"variable": name, "side": side, "value": g.text(bound)})
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
    recorded = sum(1 for o in rows if any(getattr(ch, "operating_points", None) is not None for ch in o.children.values()))
    return {"best": _tables(feasible[0]) if feasible else None, "start": _tables(start) if start else None,
            "recorded": recorded, "notes": notes}


def _tables(o: Observation) -> dict[str, Any]:
    """Per child the operating-point table of section 4; ``None`` where the store holds none. Read defensively: the
    field arrives with T17.5, and a store written before it has no such field."""
    return {"id": o.obs_id, "children": {key: _table(getattr(ch, "operating_points", None))
                                         for key, ch in sorted(o.children.items())}}


def _table(table: dict[str, dict[str, float]] | None) -> dict[str, dict[str, float]] | None:
    if table is None:
        return None
    return {inst: {q: _num(values[q]) for q in QUANTITIES if q in values} for inst, values in sorted(table.items())}


LIBRARY_UNREAD = ("the library could not be read where this digest was computed: the size of each table on its grid is "
                  "not given")


def _library(spec: Spec, rows: list[Observation], best: list[Observation], grid: list[_Grid]) -> dict[str, Any] | None:
    """``library`` (version 4, module docstring): ``None`` without library devices."""
    devices = spec.library_devices
    if not devices:
        return None
    notes: dict[str, str] = {}
    try:
        # the library's own modules load only for a spec that has one
        from ic_opt.library import link

        facts = link.summary(spec)
    except (ValueError, OSError, KeyError):                 # its message may name the library's path: not kept
        facts, notes["combinations"] = {}, LIBRARY_UNREAD
    by_name = {g.name: g for g in grid}
    tables = []
    for d in devices:
        names = [v.name for v in spec.variables if v.name in set(d.variables.values())]
        visited = {tuple(by_name[n].index(o.params[n]) for n in names) for o in rows}
        f = facts.get(d.id, {})
        tables.append({"device": d.id, "table": d.library.stratum, "frequency_hz": d.library.frequency_hz,
                       "srf_margin": f.get("srf_margin", d.library.srf_margin), "prefer": f.get("prefer", d.library.prefer),
                       "variables": dict(d.variables), "combinations": f.get("combinations"), "rows": f.get("rows"),
                       "of": f.get("of"), "visited": len(visited)})
    top = [{"id": o.obs_id, "objective": _num(o.fom), "rows": {d.id: _library_row(o, d.id) for d in devices}} for o in best]
    if not best:
        notes["top"] = "no feasible point: no row to show"
    return {"devices": tables, "top": top, "notes": notes}


def _library_row(o: Observation, device: str) -> dict[str, Any] | None:
    """The row a point's device child took (``ChildResult.library_row``) as the digest shows it: part, obs id, geometry,
    electrical values (a non-finite one as None) and footprint; None when the child recorded none."""
    child = next((c for c in o.children.values() if c.unit == device and getattr(c, "library_row", None)), None)
    if child is None:
        return None
    row = child.library_row
    return {"part": row.get("part"), "obs_id": row.get("obs_id"),
            "geometry": {k: _num(v) for k, v in (row.get("geometry") or {}).items()},
            "values": {k: _num(v) for k, v in (row.get("values") or {}).items()}, "footprint": row.get("footprint")}


# -- small pieces -------------------------------------------------------------------------------------------------------

class _Grid:
    """One variable's levels: index <-> text, and where a value lies in the range's own scale."""

    def __init__(self, variable) -> None:
        self.variable, self.name = variable, variable.name
        self.lower, self.unit = space.parse_scalar(variable.lower)
        self.step = space.parse_scalar(variable.step)[0]
        self.count = space.grid_count(variable)
        lo, hi = float(self.lower), float(self.lower + (self.count - 1) * self.step)
        self.log = space.log_scale(lo, hi)    # the strategies' rule
        self._lo, self._hi = lo, hi

    def index(self, text: str) -> int:
        return int((space.parse_scalar(text)[0] - self.lower) / self.step)

    def text(self, index: int) -> str:
        return space.format_value(self.lower + Decimal(index) * self.step, self.unit)

    def value(self, text: str) -> float:
        return float(space.parse_scalar(text)[0])

    def coordinate(self, text: str) -> float:
        """The value's unit coordinate as ``metric_gp``'s ``Coords`` gives it: 0 to 1 over the range, logarithmic where
        the range spans a decade; 0 for a range of one level."""
        if self._hi == self._lo:
            return 0.0
        v = self.value(text)
        if self.log:
            return (math.log(v) - math.log(self._lo)) / (math.log(self._hi) - math.log(self._lo))
        return (v - self._lo) / (self._hi - self._lo)

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
    head += f" · {counts['simulations']} simulations"
    if counts["stopped_early"]:
        at_device = f"{counts['stopped_at_device']} at the device; " if counts.get("stopped_at_device") else ""
        head += f" · {counts['stopped_early']} stopped early ({at_device}{counts['simulations_not_run']} simulations not run)"
    head += f" · step `{d['step']}`" if d["step"] else ""
    parts = [f"# Run digest — {d['project']}", head, *_md_stopped_at(counts),
             "## What is optimized", _md_problem(problem),
             "## How far the run is", _md_progress(d["progress"]),
             "## What is in the way", _md_constraints(d["constraints"]),
             "## Where the good points are", _md_variables(d),
             "## What failed and where", _md_failures(d["failures"]),
             "## Strategy", _md_strategy(d["strategy"]),
             "## Advice", _md_advice(d["advice"], d["progress"]["best"], d["advice_refused"]),
             "## Operating points of the best point", _md_operating_points(d["operating_points"]),
             *(["## Library devices", _md_library(d["library"])] if d.get("library") else []),
             "## What these numbers are not", NOT_SAID]
    return "\n\n".join(parts) + "\n"


def _md_stopped_at(counts: dict[str, Any]) -> list[str]:
    """The line under the counts that says where points stopped early: the three children that stopped the most."""
    if not counts["stopped_at"]:
        return []
    most = ", ".join(f"{child}: {n}" for child, n in list(counts["stopped_at"].items())[:3])
    return [f"stopped early: {counts['stopped_early']} points; at {most}" + (", ..." if len(counts["stopped_at"]) > 3 else "")]


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
        lines += [f"- best feasible point: {p['notes']['best']}", _md_stall(p["stall"], len(p["batches"]))]
        return "\n".join(lines)
    lines += [(f"- best feasible point: #{best['index']} `{best['id']}` (origin `{best['origin']}`), objective "
               f"**{quantity(best['objective'], '')}** ({p['direction'] or 'no objective'})"),
              _md_stall(p["stall"], len(p["batches"])),
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


def _md_stall(s: dict[str, Any], batches: int) -> str:
    """``progress.stall`` in one line: improving, or for how many batches the best has not improved, and where the
    search region last restarted."""
    since, restarts = s["batches_since_improvement"], s["region_restarts"]
    if since is None:
        line = f"- stall: not counted before the first feasible point ({batches} batches so far)"
    elif since == 0:
        line = "- stall: improving (the last batch improved the best)"
    else:
        line = f"- stall: no improvement for {since} batch{'' if since == 1 else 'es'}"
        line += f": **stalled** ({STALL_BATCHES} or more)" if s["stalled"] else ""
    if restarts:
        line += f"; region restarted at batch {s['last_restart_batch']} ({restarts} restart{'' if restarts == 1 else 's'})"
    return line


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
    return "\n".join([*lines, "", "### Variables by importance", "", *_md_importance(d)])


def _md_importance(d: dict[str, Any]) -> list[str]:
    """The table of ``variables[].importance``: rank, variable, its two metrics of the most mutual information, and the
    points those were computed over."""
    ranked = sorted((v for v in d["variables"] if v["importance"] is not None), key=lambda v: v["importance"]["rank"])
    if not any(m["modelled"] for m in d["problem"]["metrics"]):
        return ["_no metric is named by the constraints or the objective_"]
    if not ranked:
        return [f"_needs at least {MIN_IMPORTANCE} points that gave a metric the constraints or the objective name_"]
    lines = [("Mutual information between the variable (in unit coordinates, logarithmic where its range spans a decade) "
              "and each metric the spec names, over the points that gave the metric: 0 when the metric says nothing "
              "about the variable, in nats. Ranked by its sum over the metrics. Like a correlation, it is not a cause."),
             "", _row(["rank", "variable", "top metrics (mutual information)", "points"]), _rule(4)]
    for v in ranked:
        importance = v["importance"]
        top = sorted(((m, x) for m, x in importance["mi"].items() if x is not None), key=lambda mx: -mx[1])[:2]
        lines.append(_row([str(importance["rank"]), v["name"], " · ".join(f"{m} {x:.2f}" for m, x in top),
                           " · ".join(dict.fromkeys(str(importance["rows_used"][m]) for m, _x in top))]))
    return lines


def _md_failures(f: dict[str, Any]) -> str:
    lines = ["- statuses other than ok: " + (", ".join(f"{k} {v}" for k, v in f["by_status"].items()) or "none")]
    if f["partial_points"]:
        missing = ", ".join(f"{k} {v}" for k, v in sorted(f["missing_in_partial"].items(), key=lambda kv: -kv[1]) if v)
        lines.append(f"- metrics missing on the {f['partial_points']} points that ran but gave only some values: {missing}")
    if f["messages"]:
        lines.append(f"- what the points that gave no value said (the {len(f['messages'])} most frequent texts of their "
                     "issues, and how many points hold each):")
        lines += [f"  - {m['count']} × `{m['text'].replace('`', chr(39))}`" for m in f["messages"]]
    if not f["separating"]:
        lines += [f"- where points gave no value: {f['notes'].get('separating', 'no split separates them')}",
                  _md_by_stage(f["by_stage"])]
        return "\n".join(lines)
    lines += ["", ("Where points gave no value: the variables one split of which separates them from the scored points "
                   "best (no value / points on each side)."), "",
              _row(["variable", "at most", "from", "no value below", "no value from"]), _rule(5)]
    for s in f["separating"]:
        lines.append(_row([s["variable"], s["at_most"], s["from"], _share(s["below"]), _share(s["above"])]))
    lines += ["", _md_by_stage(f["by_stage"])]
    return "\n".join(lines)


def _md_by_stage(by_stage: dict[str, int]) -> str:
    """``failures.by_stage`` in one line: what failed the points, and how many of them were stopped early."""
    failed = ", ".join(f"{k} {v}" for k, v in by_stage.items() if v and k != "stopped_early")
    stopped = by_stage.get("stopped_early", 0)
    return ("- where points failed, by stage (else the status they ended with): " + (failed or "none")
            + (f"; stopped early {stopped} (counted there too)" if stopped else ""))


def _share(side: dict[str, Any]) -> str:
    return f"{side['no_value']} / {side['points']} ({100 * side['share']:.0f}%)"


def _md_strategy(s: dict[str, Any]) -> str:
    lines = ["- points by origin: " + (", ".join(f"{k} {v}" for k, v in s["origins"].items()) or "none")]
    gp = s["metric_gp"]
    if gp is None and "metric_gp" in s["notes"]:
        lines.append(f"- metric_gp: {s['notes']['metric_gp']}")
    if gp is not None:
        r = gp["region"]
        if r is None:
            lines.append(f"- metric_gp: {s['notes']['metric_gp']}; initial design points {gp['design_points']}")
        else:
            low, high = REGION_WEIGHTS
            lines += [f"- metric_gp search region {r['index']} (regions ended before it: {r['regions_ended']}), centred on "
                      f"`{r['centre']}`, side {r['side']:.4g}"
                      + (", ended: the next batch starts a new region" if r["ended"] else ""),
                      (f"- the side: per variable the region holds the levels within side × weight / 2 of the centre, in "
                       f"unit coordinates (every range 1 long, logarithmic where it spans a decade), and always the "
                       f"centre's level and its two neighbours; a variable's weight, from the models' length scales, lies "
                       f"between {low:g} and {high:g}; where side × weight reaches 2 the region holds every level of "
                       "the variable, whatever the centre"),
                      (f"- batches in a row that improved on the region's best: {r['successes']}, that did not: "
                       f"{r['failures']}; initial design points (start points and space-filling): {gp['design_points']}")]
    return "\n".join(lines)


def _md_advice(rows: list[dict[str, Any]], best: dict[str, Any] | None, refused: Sequence[dict[str, Any]] = ()) -> str:
    refusals = [f"- {r['id']}, refused at {r['since']}: {r['reason']}" for r in refused]      # one line each (1.5)
    if not rows:
        return "\n".join(["_no advice adopted_", "", *refusals]) if refusals else "_no advice given_"
    lines = [("*Period*: the history sizes (points in the store) from the advice's adoption up to the row that ended it. "
              "*Under it*: the points whose origin ends in `@<id>`. *Others*: the points proposed in its period that are "
              "neither under it nor its start points. *Best so far*: the run's best feasible point when it was evaluated. "
              "*Verdict*: `helped` when the points from the advice (under it and its start points) found a better best "
              "than the others and the run's best improved in its period, `no_help` when neither, `mixed` when one of "
              "the two; then the run's best at the period's start and end, and the share of the period's points that "
              "came from the advice."),
             "", _row(["advice", "given by", "period", "status", "what", "under it", "others in its period",
                       "its start points", "verdict"]), _rule(9)]
    for a in rows:
        r = a["row"]
        what = "; ".join(part for part in (
            f"start {len(r['start'])}" if r.get("start") else "",
            "ranges " + ", ".join(f"{k} {lo}..{hi}" for k, (lo, hi) in r["ranges"].items()) if r.get("ranges") else "",
            "fixed " + ", ".join(f"{k}={v}" for k, v in r["fixed"].items()) if r.get("fixed") else "",
            "vary " + ", ".join(r["vary"]) if r.get("vary") else "") if part)
        starts = ", ".join(f"`{s['id']}` {s['status']}" + (f" {quantity(s['objective'], '')}" if s["objective"] is not None
                                                            else "") + (" (best so far)" if s["best_then"] else "")
                           for s in a["start_points"]) or "—"
        since, until = a["period"]
        lines.append(_row([a["id"], str(r.get("author", "")), f"{since} to {until}" if until is not None else f"from {since}",
                           a["status"], what or "—", _md_side(a["under"]), _md_side(a["others"]), starts,
                           _md_verdict(a)]))
    at_bound = [a for a in rows if a["best_at_bound"]]
    if at_bound:
        lines.append("")
    for a in at_bound:
        sides = ", ".join(f"{b['variable']} = {b['value']} ({b['side']})" for b in a["best_at_bound"])
        lines.append(f"- the best point `{best['id']}` lies at {a['id']}'s bound, which is not the spec's: {sides}. Better "
                     "points may lie beyond it; only a wider advice looks there.")
    if refusals:
        lines += ["", *refusals]
    lines += ["", "Reasons given:", "", *(f"- {a['id']}: {a['row'].get('reason', '')}" for a in rows)]
    return "\n".join(lines)


def _md_verdict(a: dict[str, Any]) -> str:
    """An advice's verdict, the run's best at its period's start and end, and the share of the period from it."""
    if a["verdict"] is None:
        return "— (no point in its period yet)"
    improved, share = a["improved_best"], a["share_kept"]
    best = (f"best {quantity(improved['from'], '')} → {quantity(improved['to'], '')}" if improved["improved"]
            else "best not improved")
    return f"{a['verdict']}: {best}, {100 * share['advised']:.0f}% from the advice"


def _md_side(counts: dict[str, Any]) -> str:
    if not counts["points"]:
        return "none"
    return (f"{counts['points']} points, {counts['feasible']} feasible, {counts['no_value']} no value, "
            f"best {quantity(counts['best'], '')}")


def _md_operating_points(op: dict[str, Any]) -> str:
    best = op["best"]
    if best is None:
        return f"_{op['notes']['best']}_"
    if not op["recorded"]:
        return ("_No point of this run holds operating points (`ic-opt doctor` says per testbench whether the netlist "
                "asks for them)._")
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


def _md_library(entry: dict[str, Any]) -> str:
    """``library``: per device its table on the grid and what the run visited, then the rows the best points took --
    geometry, the electrical values of the device's variables (and the column its rows are ranked by), footprint."""
    lines = [("Each device is a table of a library: a point's values of its variables form a combination, and the point "
              "takes that combination's row (the best by the rule when it holds several), with the row's real sNp. No EMX "
              "runs."), "",
             _row(["device", "table", "frequency", "rows ranked by", "combinations on the grid", "visited"]), _rule(6)]
    for t in entry["devices"]:
        held = f"{t['combinations']} of {t['of']} ({t['rows']} rows)" if t["combinations"] is not None else "—"
        lines.append(_row([t["device"], t["table"], quantity(t["frequency_hz"], "Hz"), t["prefer"] or "—", held,
                           str(t["visited"])]))
    for note in entry["notes"].values():
        lines += ["", f"_{note}_"]
    if not entry["top"]:
        return "\n".join(lines)
    shown = {t["device"]: [*t["variables"], *([t["prefer"].split(":", 1)[1]] if t["prefer"] else [])] for t in entry["devices"]}
    lines += ["", "The rows the best feasible points took, the best first:", "",
              _row(["point", "objective", "device", "row (part/obs)", "geometry", "electrical values", "footprint"]), _rule(7)]
    for point in entry["top"]:
        for device, r in point["rows"].items():
            if r is None:
                lines.append(_row([f"`{point['id']}`", quantity(point["objective"], ""), device, "—", "—", "—", "—"]))
                continue
            values = ", ".join(f"{c}={quantity(r['values'].get(c), _column_unit(c))}"
                               for c in dict.fromkeys(shown[device]) if c in r["values"])
            lines.append(_row([f"`{point['id']}`", quantity(point["objective"], ""), device, f"{r['part']}/{r['obs_id']}",
                               ", ".join(f"{k}={v:g}" for k, v in r["geometry"].items() if v is not None), values,
                               footprint_text(r["footprint"])]))
    return "\n".join(lines)


def _column_unit(column: str) -> str:
    """The unit an index column prints with (``ic_opt.library.query.unit``'s rule, without loading the library's models):
    inductances in H, resonances in Hz, the area in µm², Q and k bare."""
    if column == "area":
        return "µm²"
    return "Hz" if column.startswith("SRF") else "H" if column.startswith("L") else ""


def footprint_text(footprint: dict | None) -> str:
    """A footprint for reading: ``width × height µm (area µm²)``; ``—`` without one."""
    if not footprint:
        return "—"
    return f"{footprint['width_um']:g} × {footprint['height_um']:g} µm ({footprint['area_um2']:g} µm²)"


def _row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(str(c).replace("|", "\\|") for c in cells) + " |"


def _rule(n: int) -> str:
    return "|" + " --- |" * n
