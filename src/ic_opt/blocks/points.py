"""points.* — where candidate points come from when no optimizer is involved."""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from decimal import Decimal

from ic_opt import space
from ic_opt.observation import Observation
from ic_opt.space import Point, format_value, parse_scalar
from ic_opt.spec import Spec
from ic_opt.suggesters.base import scale, unit_design


def fixed(spec: Spec, rows: Sequence[dict[str, str]]) -> list[Point]:
    """User-listed points (fix-run); every row must be complete, in bounds and on the grid."""
    return space.points_from_params(spec, rows, origin="user")


def sobol(spec: Spec, n: int, *, seed: int = 0, method: str = "sobol") -> list[Point]:
    """``n`` space-filling points (sobol / latin_hypercube / random), snapped and deduplicated."""
    seen, points = set(), []
    for raw in scale(unit_design(method, n, len(spec.variables), seed), spec):
        point = Point(space.snap(spec, raw), f"points:{method}")
        if point.key not in seen:
            seen.add(point.key)
            points.append(point)
    return points


def grid(spec: Spec, per_dim: int | None = None) -> list[Point]:
    """The full grid, or ``per_dim`` evenly spread grid values per variable."""
    axes = []
    for v in spec.variables:
        lower, unit = parse_scalar(v.lower)
        step = parse_scalar(v.step)[0]
        count = space.grid_count(v)
        picks = range(count) if per_dim is None or per_dim >= count else [round(i * (count - 1) / (per_dim - 1)) for i in range(per_dim)]
        axes.append([(v.name, format_value(lower + Decimal(i) * step, unit)) for i in dict.fromkeys(picks)])
    return [Point(dict(combo), "points:grid") for combo in itertools.product(*axes)]


def one_at_a_time(spec: Spec, center: dict[str, str], *, steps: int = 1) -> list[Point]:
    """Sensitivity design: the center plus ±``steps`` grid steps along each variable."""
    space.check(spec, center)
    points, seen = [Point(dict(center), "points:oat")], {space.point_key(center)}
    for v in spec.variables:
        lower, unit = parse_scalar(v.lower)
        upper = parse_scalar(v.upper)[0]
        step = parse_scalar(v.step)[0]
        value = parse_scalar(center[v.name])[0]
        for k in (-steps, steps):
            candidate = value + Decimal(k) * step
            if lower <= candidate <= upper:
                params = {**center, v.name: format_value(candidate, unit)}
                point = Point(params, "points:oat")
                if point.key not in seen:
                    seen.add(point.key)
                    points.append(point)
    return points


def from_observations(observations: Sequence[Observation], k: int | None = None) -> list[Point]:
    """Re-use points from observations (e.g. the top-k of an earlier step for a sign-off)."""
    rows = list(observations)[: k if k is not None else None]
    return [Point(o.params, f"from:{o.obs_id}") for o in rows]
