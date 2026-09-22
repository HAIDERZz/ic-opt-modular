"""The design space: grid contract, snapping, bounds, dedupe keys, Points.

Parameter values stay strings with Spectre-safe SI suffixes (``"0.6u"``) —
the exact text that lands in the netlist. Numeric work happens on the
``Decimal`` value plus the unit suffix, never on floats of the text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from math import prod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ic_opt.spec import Spec, Variable

_VALUE_RE = re.compile(
    r"^(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?P<unit>[A-Za-z]\w*)?$"
)
_INT_RE = re.compile(r"^[+-]?\d+$")


def parse_scalar(raw: str) -> tuple[Decimal, str]:
    """``"0.6u"`` -> ``(Decimal("0.6"), "u")``; ``"20"`` -> ``(Decimal("20"), "")``."""
    match = _VALUE_RE.match(raw)
    if match is None:
        raise ValueError(f"{raw!r} must be numeric with an optional attached unit suffix")
    try:
        return Decimal(match.group("value")), match.group("unit") or ""
    except InvalidOperation as exc:  # pragma: no cover - regex already guards this
        raise ValueError(f"{raw!r} is not a number") from exc


def variable_issue(variable: Variable) -> str | None:
    """First grid-contract violation of one variable, or None."""
    from ic_opt.spec import VariableKind

    name = variable.name
    if variable.kind is VariableKind.INTEGER:
        for label, raw in (("lower", variable.lower), ("upper", variable.upper), ("step", variable.step)):
            if not _INT_RE.match(raw):
                return f"{name} {label} must be an integer without units"
        lower, upper, step = int(variable.lower), int(variable.upper), int(variable.step)
        unit_ok = True
    else:
        try:
            (lower, lu), (upper, uu), (step, su) = (
                parse_scalar(variable.lower), parse_scalar(variable.upper), parse_scalar(variable.step)
            )
        except ValueError as exc:
            return f"{name}: {exc}"
        unit_ok = lu == uu == su
    if not unit_ok:
        return f"{name} lower/upper/step must share one unit suffix"
    if step <= 0:
        return f"{name} step must be positive"
    if lower > upper:
        return f"{name} lower must be <= upper"
    if (upper - lower) % step != 0:
        return f"{name} range must be divisible by step"
    return None


def grid_count(variable: Variable) -> int:
    lower, upper, step, _ = _grid(variable)
    return int((upper - lower) / step) + 1


def grid_size(spec: Spec) -> int:
    return prod(grid_count(v) for v in spec.variables)


def bounds(spec: Spec) -> tuple[list[float], list[float]]:
    """Numeric lower/upper per variable, in each variable's own unit scale."""
    lows, highs = [], []
    for variable in spec.variables:
        lower, upper, _, _ = _grid(variable)
        lows.append(float(lower))
        highs.append(float(upper))
    return lows, highs


def snap(spec: Spec, raw: Sequence[float]) -> dict[str, str]:
    """Snap a raw optimizer vector (same unit scale as :func:`bounds`) to the grid."""
    if len(raw) != len(spec.variables):
        raise ValueError(f"expected {len(spec.variables)} values, got {len(raw)}")
    params: dict[str, str] = {}
    for variable, value in zip(spec.variables, raw, strict=True):
        lower, upper, step, unit = _grid(variable)
        offset = round((Decimal(str(value)) - lower) / step)
        offset = max(0, min(int((upper - lower) / step), offset))
        params[variable.name] = format_value(lower + offset * step, unit)
    return params


def to_raw(spec: Spec, params: dict[str, str]) -> list[float]:
    """Inverse of :func:`snap`: parameter text -> numeric vector."""
    return [float(parse_scalar(params[v.name])[0]) for v in spec.variables]


def check(spec: Spec, params: dict[str, str]) -> None:
    """Require a complete, in-bounds, step-aligned assignment (raises ValueError)."""
    expected = [v.name for v in spec.variables]
    if set(params) != set(expected):
        raise ValueError(f"parameters must be exactly {expected}")
    for variable in spec.variables:
        raw = params[variable.name]
        if not isinstance(raw, str) or raw != raw.strip():
            raise ValueError(f"{variable.name} value must be compact text")
        value, unit = parse_scalar(raw)
        lower, upper, step, expected_unit = _grid(variable)
        if unit != expected_unit:
            raise ValueError(f"{variable.name} unit suffix must be {expected_unit!r}")
        if value < lower or value > upper:
            raise ValueError(f"{variable.name}={raw} is outside [{variable.lower}, {variable.upper}]")
        if (value - lower) % step != 0:
            raise ValueError(f"{variable.name}={raw} is not aligned to step {variable.step}")


def format_value(value: Decimal, unit: str) -> str:
    text = f"{value.normalize():f}"
    return f"{text}{unit}"


def point_key(params: dict[str, str]) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def _grid(variable: Variable) -> tuple[Decimal, Decimal, Decimal, str]:
    lower, unit = parse_scalar(variable.lower)
    upper, _ = parse_scalar(variable.upper)
    step, _ = parse_scalar(variable.step)
    return lower, upper, step, unit


@dataclass(frozen=True)
class Point:
    """One candidate: snapped parameter text plus where it came from."""

    params: dict[str, str]
    origin: str = "user"
    key: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", point_key(self.params))


def points_from_params(spec: Spec, rows: Sequence[dict[str, str]], origin: str = "user") -> list[Point]:
    """Validate user-supplied parameter rows against the spec and wrap them."""
    points = []
    for row in rows:
        params = {k: str(v) for k, v in row.items()}
        check(spec, params)
        points.append(Point(params, origin))
    return points
