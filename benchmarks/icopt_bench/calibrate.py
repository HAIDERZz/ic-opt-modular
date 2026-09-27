"""From one circuit's survey to its two benchmark problems (T17.0a): the recipe of the plan's section 4.3.

Everything here is a pure function of the survey's rows, so the same survey always gives the same thresholds, the same
reference design and the same fine-tuning ranges; no method's result enters. ``tools/survey_analoggym.py`` runs the
simulations and calls this module; ``tests/benchmarks/test_calibrate.py`` checks it on made-up surveys.

A survey row is ``{"params": {name: text}, "metrics": {name: float}}``; a metric without a value is absent.
"""

from __future__ import annotations

import math
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import numpy as np
from scipy.stats import qmc, rankdata

from ic_opt.objective import evaluate_expression
from ic_opt.space import format_value, parse_scalar

SURVEY_POINTS = 2048                 # half uniform in each variable's own coordinate, half in its logarithm
FEASIBLE_SHARE = 0.005               # of ALL survey points: random search meets a feasible point in 200 w.p. 63%
MIN_WORKING = 40                     # 2% of the survey: below this a quantile of the working points says nothing
QUANTILE_GRID = 1000
FINE_VARIABLES = 6
FINE_STEP_SHARE = Decimal("0.1")
FINE_SPAN_SHARE = Decimal("0.3")
FINE_MAX_LEVELS = 3                  # on each side of the reference design


@dataclass(frozen=True)
class Calibration:
    constraints: tuple[dict, ...]    # [{"metric", "op", "value"}], the spec's own form
    quantile: float
    feasible: int                    # survey points meeting every threshold
    working: int
    total: int
    reference: dict[str, str]
    reference_objective: float
    fine_variables: tuple[str, ...]
    fine_ranges: dict[str, tuple[str, str, str]]
    influence: dict[str, float]


class NotCalibrated(ValueError):
    """The circuit cannot become a benchmark problem; the message is the reason recorded in the circuits table."""


# --- the survey's points ------------------------------------------------------------------------------------------

def survey_seed(circuit: str) -> int:
    return zlib.crc32(circuit.encode())


def survey_points(circuit: str, variables: Sequence[dict], n: int = SURVEY_POINTS) -> list[dict[str, str]]:
    """``n`` grid points of the circuit's own ranges: the first half uniform per variable, the second half uniform in
    its logarithm (a variable whose lower bound is not positive stays linear). Duplicates after snapping are dropped,
    so the list can be slightly shorter than ``n``."""
    half = n // 2
    seed = survey_seed(circuit)
    linear = qmc.Sobol(len(variables), scramble=True, seed=seed).random(half)
    log = qmc.Sobol(len(variables), scramble=True, seed=seed + 1).random(n - half)
    points: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for unit, in_log in ((linear, False), (log, True)):
        for row in unit:
            point = {v["name"]: _on_grid(v, float(u), in_log) for v, u in zip(variables, row, strict=True)}
            key = tuple(point.values())
            if key not in seen:
                seen.add(key)
                points.append(point)
    return points


def _grid(variable: dict) -> tuple[Decimal, Decimal, Decimal, str]:
    lower, unit = parse_scalar(str(variable["lower"]))
    upper, _ = parse_scalar(str(variable["upper"]))
    step, _ = parse_scalar(str(variable["step"]))
    return lower, upper, step, unit


def _on_grid(variable: dict, u: float, in_log: bool) -> str:
    lower, upper, step, unit = _grid(variable)
    lo, hi = float(lower), float(upper)
    value = math.exp(math.log(lo) + u * (math.log(hi) - math.log(lo))) if in_log and lo > 0 else lo + u * (hi - lo)
    offset = max(0, min(int((upper - lower) / step), round((Decimal(repr(value)) - lower) / step)))
    return format_value(lower + offset * step, unit)


# --- thresholds ---------------------------------------------------------------------------------------------------

def _loosened(value: float, op: str) -> str:
    """Three significant digits, rounded so the feasible set cannot shrink (down for "ge", up for "le")."""
    if value == 0 or not math.isfinite(value):
        return "0" if value == 0 else repr(value)
    d = Decimal(repr(value))
    quantum = Decimal(1).scaleb(d.adjusted() - 2)
    return f"{d.quantize(quantum, rounding=ROUND_FLOOR if op == 'ge' else ROUND_CEILING).normalize():f}"


def _thresholds(columns: dict[str, np.ndarray], targets: Sequence[dict], q: float) -> list[dict]:
    out = []
    for target in targets:
        op = "ge" if target["direction"] == "ge" else "le"
        level = float(np.quantile(columns[target["metric"]], q if op == "ge" else 1 - q))
        out.append({"metric": target["metric"], "op": op, "value": _loosened(level, op)})
    return out


def _meets(metrics: dict[str, float], constraints: Sequence[dict]) -> bool:
    for c in constraints:
        value = metrics.get(c["metric"])
        if value is None:
            return False
        if (value < float(c["value"])) if c["op"] == "ge" else (value > float(c["value"])):
            return False
    return True


def calibrate(rows: Sequence[dict], variables: Sequence[dict], metric_names: Sequence[str], targets: Sequence[dict],
              objective: dict) -> Calibration:
    total = len(rows)
    working = [r for r in rows if all(m in r["metrics"] and math.isfinite(r["metrics"][m]) for m in metric_names)]
    if len(working) < MIN_WORKING:
        raise NotCalibrated(f"only {len(working)} of {total} survey points give every metric (need {MIN_WORKING})")
    columns = {m: np.array([r["metrics"][m] for r in working], dtype=float) for m in metric_names}
    need = math.ceil(FEASIBLE_SHARE * total)

    def feasible_rows(q: float) -> tuple[list[dict], list[dict]]:
        constraints = _thresholds(columns, targets, q)
        return constraints, [r for r in working if _meets(r["metrics"], constraints)]

    # The count of feasible points does not grow with q, so the largest q that still leaves `need` of them is found
    # by bisection over the grid.
    lo, hi = 0, QUANTILE_GRID - 1
    if len(feasible_rows(0.0)[1]) < need:
        raise NotCalibrated(f"fewer than {need} survey points meet even the loosest thresholds")
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(feasible_rows(mid / QUANTILE_GRID)[1]) >= need:
            lo = mid
        else:
            hi = mid - 1
    q = lo / QUANTILE_GRID
    constraints, feasible = feasible_rows(q)

    sign = 1.0 if objective["direction"] == "maximize" else -1.0
    scored = [(sign * evaluate_expression(objective["expression"], r["metrics"]), i) for i, r in enumerate(feasible)]
    best_score, best_index = max(scored, key=lambda s: (s[0], -s[1]))
    reference = dict(feasible[best_index]["params"])

    objective_column = np.array([evaluate_expression(objective["expression"], r["metrics"]) for r in working])
    influence = _influence(working, variables, [columns[t["metric"]] for t in targets] + [objective_column])
    fine_variables, fine_ranges = _fine(variables, reference, influence)
    return Calibration(constraints=tuple(constraints), quantile=q, feasible=len(feasible), working=len(working),
                       total=total, reference=reference, reference_objective=sign * best_score,
                       fine_variables=fine_variables, fine_ranges=fine_ranges, influence=influence)


# --- the fine-tuning problem --------------------------------------------------------------------------------------

def _influence(working: Sequence[dict], variables: Sequence[dict], columns: Sequence[np.ndarray]) -> dict[str, float]:
    """Per variable, the largest absolute rank correlation with any constrained metric or the objective."""
    ranked = [rankdata(c) for c in columns]
    out: dict[str, float] = {}
    for v in variables:
        x = rankdata([float(parse_scalar(r["params"][v["name"]])[0]) for r in working])
        if np.ptp(x) == 0:
            out[v["name"]] = 0.0
            continue
        rhos = [abs(float(np.corrcoef(x, y)[0, 1])) for y in ranked if np.ptp(y) > 0]
        out[v["name"]] = max(rhos, default=0.0)
    return out


def fine_range(variable: dict, reference: str) -> tuple[str, str, str] | None:
    """The reference design's neighbourhood on a coarser grid that contains it: a step of about a tenth of the
    reference value (a whole number of the variable's own steps), at most 30% to each side and at most three levels,
    cut at the variable's own bounds. None when not even one level fits on either side."""
    lower, upper, base, unit = _grid(variable)
    ref = parse_scalar(reference)[0]
    step = max(base, (FINE_STEP_SHARE * ref / base).to_integral_value() * base)
    levels = min(FINE_MAX_LEVELS, max(1, int(FINE_SPAN_SHARE * ref / step)))
    below = min(levels, int((ref - lower) / step))
    above = min(levels, int((upper - ref) / step))
    if below + above == 0:
        return None
    return format_value(ref - below * step, unit), format_value(ref + above * step, unit), format_value(step, unit)


def _fine(variables: Sequence[dict], reference: dict[str, str], influence: dict[str, float],
          ) -> tuple[tuple[str, ...], dict[str, tuple[str, str, str]]]:
    by_name = {v["name"]: v for v in variables}
    chosen: list[str] = []
    ranges: dict[str, tuple[str, str, str]] = {}
    for name in sorted(by_name, key=lambda n: (-influence[n], n)):
        if len(chosen) == FINE_VARIABLES:
            break
        found = fine_range(by_name[name], reference[name])
        if found is not None:
            chosen.append(name)
            ranges[name] = found
    return tuple(chosen), ranges


# --- the split ----------------------------------------------------------------------------------------------------

def split(circuits: Sequence[tuple[str, str, int]], seed: int) -> tuple[list[str], list[str]]:
    """``circuits``: (name, kind, number of variables). Within each kind, the circuits in order of size are cut into
    groups of five, and two of each five (of a smaller last group: the rounded 40%) are held out, drawn with ``seed``.
    Returns (development, held out), each sorted by name."""
    rng = np.random.default_rng(seed)
    held: list[str] = []
    for kind in sorted({c[1] for c in circuits}):
        ordered = sorted((c for c in circuits if c[1] == kind), key=lambda c: (c[2], c[0]))
        for start in range(0, len(ordered), 5):
            group = ordered[start:start + 5]
            count = int(Decimal(len(group) * 2 / 5).to_integral_value(rounding="ROUND_HALF_UP"))
            held += [group[i][0] for i in sorted(rng.choice(len(group), size=count, replace=False))]
    development = sorted(c[0] for c in circuits if c[0] not in held)
    return development, sorted(held)
