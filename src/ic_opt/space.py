"""The design space: grid contract, snapping, bounds, dedupe keys, Points.

Parameter values stay strings with Spectre-safe SI suffixes (``"0.6u"``) —
the exact text that lands in the netlist. Numeric work happens on the
``Decimal`` value plus the unit suffix, never on floats of the text.

Allowed combinations (T18.2A specification, ``docs/refactor/T18_2A_ALLOWED_COMBINATIONS_SPEC.md``). Some variables may
take only the combinations of their levels that a :class:`Table` lists -- a library device's variables, the combinations
a row of its library sits on; :func:`tables` gives a spec's tables. A *valid point* is a grid point whose linked
variables' levels are, table by table, one of its table's combinations: :func:`snap` and :func:`project` hand out valid
points only, :func:`check` accepts only them, :func:`grid_size` counts them, :func:`valid_points` enumerates them. The
nearest combination is the one at the smallest Euclidean distance in unit coordinates (:func:`unit_coordinates`, those of
``metric_gp``'s ``Coords``), and :func:`nearest` alone measures it. With no table every grid point is valid and nothing
here behaves differently.
"""

from __future__ import annotations

import bisect
import itertools
import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from math import prod
from typing import TYPE_CHECKING, NamedTuple

import numpy as np

if TYPE_CHECKING:
    from ic_opt.spec import Spec, Variable

LOG_SPAN = 10          # upper / lower from which a positive range is searched on a logarithmic scale (log_scale)
NEAREST_CHUNK = 1 << 20    # coordinate differences nearest() holds at once (8 MB of float64)

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
    """The number of valid points: the free variables' levels multiplied together, times each table's number of
    combinations (:func:`tables`); without tables, the grid's size."""
    found = tables(spec)
    linked_names = {name for table in found for name in table.names}
    return (prod(grid_count(v) for v in spec.variables if v.name not in linked_names)
            * prod(len(table.levels) for table in found))


def bounds(spec: Spec) -> tuple[list[float], list[float]]:
    """Numeric lower/upper per variable, in each variable's own unit scale."""
    lows, highs = [], []
    for variable in spec.variables:
        lower, upper, _, _ = _grid(variable)
        lows.append(float(lower))
        highs.append(float(upper))
    return lows, highs


def log_scale(lower: float, upper: float) -> bool:
    """Whether a range is searched on a logarithmic scale (T17.7 specification, section 2): its lower bound is positive
    and ``upper / lower >= LOG_SPAN``, a decade or more. The range alone decides, not what the variable is. The one rule
    of ``metric_gp``'s coordinates (:func:`unit_coordinates`), the ``openbox_*`` and ``turbo`` strategies and the digest's
    thirds of a range; the grid, ``bounds``, ``to_raw`` and ``snap``'s rounding of a variable to its levels stay linear
    whatever it says (the nearest combination of a table is measured in unit coordinates, so in its scale)."""
    return bool(lower > 0 and upper / lower >= LOG_SPAN)


def unit_coordinates(values, lower: float, upper: float) -> np.ndarray:
    """Values of one variable -> unit coordinates: its range ``[lower, upper]`` mapped to [0, 1], logarithmically when
    :func:`log_scale` says so, else linearly, a one-level variable (``lower == upper``) at 0. The one formula of
    ``metric_gp``'s coordinates (``Coords``) and of the nearest combination of a table (T18.2A specification, section 1)."""
    x = np.asarray(values, dtype=float)
    if upper == lower:
        return np.zeros_like(x, dtype=float)
    if log_scale(lower, upper):
        return (np.log(x) - np.log(lower)) / (np.log(upper) - np.log(lower))
    return (x - lower) / (upper - lower)


def grid_levels(variable: Variable) -> tuple[np.ndarray, np.ndarray]:
    """A variable's levels as numbers, ``lower + k * step`` exactly as the grid's text gives them, and their unit
    coordinates (:func:`unit_coordinates`): the coordinates of ``metric_gp``'s ``Coords`` and of the nearest combination.
    Computed once per grid; the arrays are read-only."""
    return _grid_levels(variable.lower, variable.upper, variable.step)


@lru_cache(maxsize=512)
def _grid_levels(lower: str, upper: str, step: str) -> tuple[np.ndarray, np.ndarray]:
    low, high, width = (parse_scalar(text)[0] for text in (lower, upper, step))
    count = int((high - low) / width) + 1
    values = np.array([float(low + Decimal(k) * width) for k in range(count)])     # Decimal: exactly the grid's text
    units = unit_coordinates(values, values[0], values[-1])
    values.flags.writeable = False
    units.flags.writeable = False
    return values, units


def nearest(points, combinations) -> np.ndarray:
    """For each row of ``points`` the index of the nearest row of ``combinations``, both in unit coordinates over one
    table's variables (:func:`unit_coordinates`): the smallest Euclidean distance, ties to the first row, i.e. to the
    table's sorted order (T18.2A specification, section 1). The one distance of the allowed combinations: :func:`snap`,
    :func:`check`, :func:`project` and ``metric_gp``'s ``Coords.project`` all measure with it."""
    points = np.atleast_2d(np.asarray(points, dtype=float))
    combinations = np.atleast_2d(np.asarray(combinations, dtype=float))
    out = np.empty(len(points), dtype=np.int64)
    chunk = max(1, NEAREST_CHUNK // max(1, combinations.size))
    for start in range(0, len(points), chunk):
        difference = points[start : start + chunk, None, :] - combinations[None, :, :]
        out[start : start + chunk] = np.argmin(np.square(difference).sum(axis=-1), axis=1)
    return out


def snap(spec: Spec, raw: Sequence[float]) -> dict[str, str]:
    """Snap a raw optimizer vector (same unit scale as :func:`bounds`) to the grid; with tables, to a valid point: each
    table's variables then take the combination nearest to their raw values, clipped to the bounds first (:func:`nearest`,
    in unit coordinates). The other variables are snapped as without tables."""
    if len(raw) != len(spec.variables):
        raise ValueError(f"expected {len(spec.variables)} values, got {len(raw)}")
    params: dict[str, str] = {}
    for variable, value in zip(spec.variables, raw, strict=True):
        lower, upper, step, unit = _grid(variable)
        offset = round((Decimal(str(value)) - lower) / step)
        offset = max(0, min(int((upper - lower) / step), offset))
        params[variable.name] = format_value(lower + offset * step, unit)
    for placement in placements(spec):
        at = []
        for i in placement.columns:
            values = grid_levels(spec.variables[i])[0]
            at.append(unit_coordinates(np.clip([float(raw[i])], values[0], values[-1]), values[0], values[-1]))
        chosen = placement.table.rows[nearest(np.column_stack(at), placement.units)[0]]
        params.update(_texts(spec, placement.columns, chosen))
    return params


def project(spec: Spec, params: dict[str, str]) -> dict[str, str]:
    """The valid point nearest to the grid point ``params``: each table's variables take its combination nearest to their
    levels (:func:`nearest`, in the levels' unit coordinates), the other variables stay. A valid point comes back as it
    is (a copy), so without tables every grid point does."""
    out = dict(params)
    for placement in placements(spec):
        given = tuple(_level_index(spec.variables[i], params[spec.variables[i].name]) for i in placement.columns)
        if not _allows(placement.table, given):
            near = placement.table.rows[nearest(_unit_rows(spec, placement.columns, [given]), placement.units)[0]]
            out.update(_texts(spec, placement.columns, near))
    return out


def valid_points(spec: Spec) -> Iterator[dict[str, str]]:
    """Every valid point as parameter text, in one fixed order: the free variables' levels times each table's
    combinations, one block per free variable and per table in the order of its first variable in the spec, the last
    block varying fastest; without tables, the grid in the order of ``itertools.product`` over the variables. The full
    product is never built: :func:`grid_size` points come out."""
    found = tables(spec)
    owner = {name: t for t, table in enumerate(found) for name in table.names}
    variables = {v.name: v for v in spec.variables}
    blocks, added = [], set()
    for variable in spec.variables:
        t = owner.get(variable.name)
        if t is None:
            blocks.append([((variable.name, _level_text(variable, k)),) for k in range(grid_count(variable))])
        elif t not in added:
            added.add(t)
            names = found[t].names
            blocks.append([tuple((name, _level_text(variables[name], k)) for name, k in zip(names, row, strict=True))
                           for row in found[t].levels])
    for combination in itertools.product(*blocks):
        text = dict(pair for block in combination for pair in block)
        yield {v.name: text[v.name] for v in spec.variables}


def to_raw(spec: Spec, params: dict[str, str]) -> list[float]:
    """Inverse of :func:`snap`: parameter text -> numeric vector."""
    return [float(parse_scalar(params[v.name])[0]) for v in spec.variables]


def check(spec: Spec, params: dict[str, str]) -> None:
    """Require a complete, in-bounds, step-aligned assignment that is a valid point (raises ValueError): with tables,
    each table's variables must take one of its combinations, and the message names the table, the values given and the
    nearest allowed combination (:func:`nearest`)."""
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
    for placement in placements(spec):
        names = [spec.variables[i].name for i in placement.columns]
        given = tuple(_level_index(spec.variables[i], params[spec.variables[i].name]) for i in placement.columns)
        if _allows(placement.table, given):
            continue
        chosen = placement.table.rows[nearest(_unit_rows(spec, placement.columns, [given]), placement.units)[0]]
        near = _texts(spec, placement.columns, chosen)
        given_text = " ".join(f"{name}={params[name]}" for name in names)
        raise ValueError(f"{placement.table.title}: {given_text} is not one of its combinations; the nearest one is "
                         + " ".join(f"{name}={near[name]}" for name in names))


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


def _level_index(variable: Variable, text: str) -> int:
    """The level index of a grid value's text (``k`` for ``lower + k * step``)."""
    lower, _, step, _ = _grid(variable)
    return int((parse_scalar(text)[0] - lower) / step)


def _level_text(variable: Variable, k: int) -> str:
    """The grid text of level ``k`` of a variable."""
    lower, _, step, unit = _grid(variable)
    return format_value(lower + int(k) * step, unit)


@dataclass(frozen=True)
class Table:
    """Some variables of a spec may take only the combinations of their levels listed here (T18.2A specification,
    section 1): ``names``, variables of the spec; ``levels``, the allowed combinations of their level indices (``k`` for
    ``lower + k * step``), one index per name; ``label``, what messages call the table. The combinations are kept
    distinct and sorted -- given otherwise, they are stored so -- and the table's sorted order breaks the ties of
    :func:`nearest`. :func:`tables` checks a table against its spec where it is first used."""

    names: tuple[str, ...]                  # variables of the spec, in the spec's order
    levels: tuple[tuple[int, ...], ...]     # the allowed combinations of their level indices: distinct, sorted
    label: str = ""                         # for messages, e.g. "device xfmr (library rows)"
    rows: np.ndarray = field(init=False, repr=False, compare=False)     # ``levels`` as an (m, k) int64 array, read-only

    def __post_init__(self) -> None:
        names = tuple(str(name) for name in self.names)
        combinations = tuple(sorted({tuple(int(k) for k in combination) for combination in self.levels}))
        object.__setattr__(self, "names", names)
        object.__setattr__(self, "levels", combinations)
        if any(len(combination) != len(names) for combination in combinations):
            raise ValueError(f"{self.title}: a combination gives one level index per variable ({len(names)})")
        rows = np.array(combinations, dtype=np.int64).reshape(len(combinations), len(names))
        rows.flags.writeable = False
        object.__setattr__(self, "rows", rows)

    @property
    def title(self) -> str:
        """What messages call the table: its label, else the variables it links."""
        return self.label or f"the table of {', '.join(self.names)}"


def tables(spec: Spec) -> list[Table]:
    """The tables of allowed combinations of ``spec``'s variables (:class:`Table`): what ``ic_opt.library.link.tables``
    gives -- none until T18.2B, which reads them from the spec's library devices -- checked against the spec here, where
    they are first used: every name a variable of the spec and in one table only, every level index within its
    variable's levels, no table empty. A violation is a ValueError that names the table."""
    # the library builds on the space: imported where first needed, not when the space is
    from ic_opt.library import link

    found = list(link.tables(spec))
    counts = {v.name: grid_count(v) for v in spec.variables}
    owner: dict[str, int] = {}
    for t, table in enumerate(found):
        if not table.names:
            raise ValueError(f"{table.title} names no variable")
        unknown = [name for name in table.names if name not in counts]
        if unknown:
            raise ValueError(f"{table.title}: {', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} not a variable "
                             f"of the spec (its variables: {', '.join(counts)})")
        for name in table.names:
            if name in owner:
                where = "twice" if owner[name] == t else f"and {found[owner[name]].title} names it too"
                raise ValueError(f"{table.title} names {name} {where}: a variable is in one table only")
            owner[name] = t
        if not table.levels:
            raise ValueError(f"{table.title} holds no combination")
        outside = np.argwhere((table.rows < 0) | (table.rows >= np.array([counts[name] for name in table.names])))
        if len(outside):
            row, column = outside[0]
            name = table.names[column]
            raise ValueError(f"{table.title}: level index {table.rows[row, column]} of {name} is outside its levels "
                             f"(0 to {counts[name] - 1})")
    return found


class Placement(NamedTuple):
    """A table placed on its spec's grid (:func:`placements`)."""

    table: Table
    columns: tuple[int, ...]       # the spec positions of its variables
    units: np.ndarray              # (m, k): its combinations in unit coordinates, what nearest() measures against


def placements(spec: Spec) -> list[Placement]:
    """The spec's tables (:func:`tables`) resolved against it: each with the spec positions of its variables and its
    combinations in unit coordinates. None without tables."""
    position = {v.name: i for i, v in enumerate(spec.variables)}
    out = []
    for table in tables(spec):
        columns = tuple(position[name] for name in table.names)
        out.append(Placement(table, columns, _unit_rows(spec, columns, table.rows)))
    return out


def _unit_rows(spec: Spec, columns: Sequence[int], rows) -> np.ndarray:
    """Rows of level indices of the variables at ``columns`` -> their unit coordinates (:func:`grid_levels`)."""
    rows = np.atleast_2d(np.asarray(rows, dtype=np.int64))
    return np.column_stack([grid_levels(spec.variables[i])[1][rows[:, j]] for j, i in enumerate(columns)])


def _allows(table: Table, combination: tuple[int, ...]) -> bool:
    """Whether ``combination`` (level indices over the table's names) is one of the table's."""
    at = bisect.bisect_left(table.levels, combination)
    return at < len(table.levels) and table.levels[at] == combination


def _texts(spec: Spec, columns: Sequence[int], combination) -> dict[str, str]:
    """The grid text of a combination of level indices of the variables at ``columns``."""
    return {spec.variables[i].name: _level_text(spec.variables[i], k) for i, k in zip(columns, combination, strict=True)}


@dataclass(frozen=True)
class Point:
    """One candidate: snapped parameter text plus where it came from."""

    params: dict[str, str]
    origin: str = "user"
    key: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", point_key(self.params))


_ADVICE_SUFFIX = re.compile(r"^a[1-9]\d*$")


def split_origin(origin: str) -> tuple[str, str | None]:
    """An origin and the advice it was proposed under (T17.1.5 specification, section 3): a point a strategy chose among
    the candidates an advice narrowed carries ``@<id>`` -- ``suggest:metric_gp:tr:0:40@a2`` -> (``suggest:metric_gp:tr:0:
    40``, ``a2``); any other origin -> (origin, None). Every reader of origins reads them through this. Only an advice id
    after the last ``@`` counts, so an origin that holds an ``@`` of its own (a directory name) is left whole. A start
    point of an advice has the origin ``advice:<id>``, with no suffix."""
    head, sep, tail = origin.rpartition("@")
    return (head, tail) if sep and _ADVICE_SUFFIX.match(tail) else (origin, None)


def points_from_params(spec: Spec, rows: Sequence[dict[str, str]], origin: str = "user") -> list[Point]:
    """Validate user-supplied parameter rows against the spec and wrap them."""
    points = []
    for row in rows:
        params = {k: str(v) for k, v in row.items()}
        check(spec, params)
        points.append(Point(params, origin))
    return points
