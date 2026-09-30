"""A table's rows seen by their electrical values at one working frequency (T18.1): what stage L2 draws a device's
candidates from, and what ``lib.index`` / ``lib.pick`` show.

``build`` makes the index of one stratum at one frequency. Its columns are every declared curve at that frequency under
its bare name (``Lp``, ``Qp``; for a coupled pair ``Ls``, ``Qs``, ``k`` too) -- a declared anchor or an extension column,
``Library.columns`` -- every declared scalar under its own name, ``Qmin`` = min(``Qp``, ``Qs``) for a stratum with both,
and ``area``, the footprint's (``Library.footprints``; None without one). Its rows are the dataset's rows that have every
curve there: a curve's own rule already leaves out a row whose sweep does not reach the frequency or whose resonance
lies within the curve's ``srf_margin``; a ``srf_margin`` given above the manifest's also leaves out the rows whose known
system SRF is at or below it x f (a row with no resonance in its sweep stays). ``Index.dropped`` counts the rows left
out, by reason.

``level``, ``table`` and ``pick`` put the rows on a grid of electrical values -- per coordinate (an index column)
``(lower, upper, step)`` in the column's unit (H, 1, Hz), levels ``lower + i x step`` for ``i = 0 .. round((upper -
lower) / step)``, searched logarithmically when ``space.log_scale`` says so for the grid's ends and linearly otherwise:

- ``level``: the nearest level in the coordinate's search scale, a tie to the lower level -- the rule ``metric_gp``'s
  ``Coords.snap`` applies to a variable's grid, computed the same way (levels exact from their decimal text, unit
  coordinates by ``space.unit_coordinates``, the one formula ``Coords`` and the space's tables use), so L2's strategies
  and this table agree -- or None beyond an end by more than half the end interval in that scale;
- ``table``: each row in the cell of its coordinates' levels, ranked within the cell by ``prefer``;
- ``pick``: the occupied cell nearest to target values, in the grid's unit coordinates.
"""

from __future__ import annotations

import collections
import hashlib
import json
import math
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import numpy as np

from ic_opt import space
from ic_opt.library import dataset, manifest, query

INDEX_VERSION = 1                                    # bump when what an index holds changes (it is part of Index.key)
MAX_LEVELS = 100_000                                 # per coordinate of a grid: beyond it a step is surely mistyped
DROPPED = {"sweep": "outside the row's sweep",
           "resonance": "system SRF at or below the curve's srf_margin x f",
           "value": "no finite value",
           "margin": "system SRF at or below the given srf_margin x f"}


@dataclass(frozen=True)
class IndexRow:
    stratum: str
    part: str
    obs_id: str
    params: dict[str, float]                          # the row's geometry: the stratum's dims
    values: dict[str, float | None]                   # the index's columns
    footprint: dict | None
    snp: str                                          # relative to the library root
    ports: list[str]                                  # the sNp's column labels, in the file's order


@dataclass(frozen=True)
class Index:
    stratum: str
    frequency_hz: float
    srf_margin: float                                 # the margin every row satisfies: the manifest's curves' largest, or a larger one given
    columns: list[str]
    rows: list[IndexRow]
    key: str                                          # content: the dataset key, the frequency, the margin, INDEX_VERSION
    dropped: dict[str, int] = field(default_factory=dict)       # rows left out, per reason (DROPPED's texts)


def build(library: query.Library, stratum: str, frequency_hz: float, *, srf_margin: float | None = None) -> Index:
    """The index of ``stratum`` at ``frequency_hz`` (see the module docstring). ``srf_margin`` above the manifest's (the
    largest of its curves') also drops the rows whose system SRF is at or below it x f; below it, it changes nothing.
    Refused: a stratum without a curve, a frequency no row can give (``Library.columns``: the parts' sweeps), and one
    where no row keeps every curve."""
    rule = library.manifest.strata[stratum]
    curves = dataset.curves(rule)
    if not curves:
        raise ValueError(f"{stratum} declares no curve ({', '.join(manifest.CURVES)}): an index holds the curves at a working frequency")
    f = float(frequency_hz)
    if not (math.isfinite(f) and f > 0):
        raise ValueError(f"frequency_hz must be a positive frequency, got {frequency_hz!r}")
    if srf_margin is not None and not (math.isfinite(float(srf_margin)) and float(srf_margin) > 0):
        raise ValueError(f"srf_margin must be a positive number, got {srf_margin!r}")
    ghz = f / 1e9
    at = dict(zip(curves, library.columns(stratum, [f"{c}@{ghz!r}" for c in curves])))
    ds = library.dataset(stratum)
    facts = library.anchors(stratum, ghz)
    own = max(rule.quantities[c].srf_margin for c in curves)
    margin = own if srf_margin is None else max(own, float(srf_margin))
    scalars = [s for s in manifest.SCALARS if s in rule.quantities]
    both = "Qp" in curves and "Qs" in curves
    columns = curves + scalars + (["Qmin"] if both else []) + ["area"]
    boxes = library.footprints(stratum)
    devices = dataset.part_devices(library.root, rule)
    rows, dropped = [], collections.Counter()
    for i, r in enumerate(ds.rows):
        missing = [c for c in curves if r.values.get(at[c]) is None]
        srf = facts["srf_hz"][i]
        if missing:
            dropped[_why(facts, i, [rule.quantities[c].srf_margin for c in missing], f)] += 1
            continue
        if srf is not None and srf <= margin * f:
            dropped[DROPPED["margin"]] += 1
            continue
        values: dict[str, float | None] = {c: r.values[at[c]] for c in curves}
        values.update({s: r.values.get(s) for s in scalars})
        if both:
            values["Qmin"] = min(values["Qp"], values["Qs"])
        box = boxes[(r.part, r.obs_id)][0]
        values["area"] = None if box is None else box["area_um2"]
        ports = dataset.snp_columns((library.root / r.snp).parent, devices[r.part])
        rows.append(IndexRow(stratum, r.part, r.obs_id, {d: r.coords[d] for d in ds.dims}, values, box, r.snp, ports))
    if not rows:
        raise ValueError(f"{stratum}: no row keeps every curve ({', '.join(curves)}) at {ghz:g} GHz within srf_margin {margin:g}: "
                         f"of {len(ds.rows)} rows, {dict(dropped)}; the parts' sweeps: {dataset.describe_sweeps(library.sweeps(stratum))}")
    key = hashlib.sha256(json.dumps([ds.key, f, margin, INDEX_VERSION]).encode()).hexdigest()[:20]
    return Index(stratum, f, margin, columns, rows, key, dict(dropped))


def _why(facts: dict, i: int, margins: list[float], f: float) -> str:
    """Why row ``i`` has no value for a curve at ``f`` (``dataset._anchor_value``'s rule, the missing curves' margins)."""
    if f < facts["start_hz"][i] or f > facts["stop_hz"][i]:
        return DROPPED["sweep"]
    srf = facts["srf_hz"][i]
    if srf is not None and any(srf <= m * f for m in margins):
        return DROPPED["resonance"]
    return DROPPED["value"]


# -- a grid of electrical values ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Axis:
    """One coordinate's grid: its levels (``raw``, exact from their decimal text), their unit coordinates in the search
    scale (``unit``: [0, 1], logarithmic when ``log``) and the step."""

    name: str
    lower: float
    upper: float
    step: float
    raw: np.ndarray = field(repr=False, compare=False)
    unit: np.ndarray = field(repr=False, compare=False)
    log: bool = False

    @classmethod
    def of(cls, name: str, lower: float, upper: float, step: float) -> Axis:
        lo, hi, st = (_number(name, label, v) for label, v in (("lower", lower), ("upper", upper), ("step", step)))
        if not st > 0 or hi < lo:
            raise ValueError(f"grid {name}: expected lower <= upper and step > 0, got [{lo:g}, {hi:g}, {st:g}]")
        count = round((hi - lo) / st) + 1
        if count > MAX_LEVELS:
            raise ValueError(f"grid {name}: {count} levels from {lo:g} to {hi:g} by {st:g}; at most {MAX_LEVELS}")
        first, by = Decimal(repr(lo)), Decimal(repr(st))
        raw = np.array([float(first + i * by) for i in range(count)])
        return cls(name, lo, hi, st, raw, space.unit_coordinates(raw, raw[0], raw[-1]), space.log_scale(float(raw[0]), float(raw[-1])))

    def to_unit(self, value: float) -> float | None:
        """``value`` in unit coordinates (continuous; outside [0, 1] beyond the ends), None where the scale has none
        (a value <= 0 on a logarithmic axis)."""
        if self.log and not value > 0:
            return None
        return float(space.unit_coordinates([float(value)], self.raw[0], self.raw[-1])[0])

    def level(self, value: float | None) -> int | None:
        """The nearest level in unit coordinates, a tie to the lower level (``Coords.snap``'s rule); None for no value and
        beyond an end by more than half the end interval in the search scale (a single level: half a step)."""
        if value is None or not math.isfinite(value):
            return None
        u = self.to_unit(value)
        if u is None:
            return None
        n = len(self.raw) - 1
        if n == 0:
            return 0 if abs(value - self.raw[0]) <= self.step / 2 else None
        levels = self.unit
        if u < levels[0] - (levels[1] - levels[0]) / 2 or u > levels[-1] + (levels[-1] - levels[-2]) / 2:
            return None
        right = int(np.clip(np.searchsorted(levels, u), 0, n))
        left = max(right - 1, 0)
        return left if abs(u - levels[left]) <= abs(levels[right] - u) else right

    def position(self, value: float) -> int:
        """``level``, and beyond the ends the nearest level of the grid continued past each end by its end interval (so
        below the first level it is negative, above the last one larger than the last index)."""
        inside = self.level(value)
        if inside is not None:
            return inside
        u = self.to_unit(value)
        n = len(self.raw) - 1
        if n == 0:
            p = (value - self.raw[0]) / self.step
        elif u < self.unit[0]:
            p = (u - self.unit[0]) / (self.unit[1] - self.unit[0])
        else:
            p = n + (u - self.unit[-1]) / (self.unit[-1] - self.unit[-2])
        return math.ceil(p - 0.5)                    # nearest, a tie to the lower level


def _number(name: str, label: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"grid {name}: {label} must be a finite number, got {value!r}")
    return float(value)


def level(value: float, lower: float, upper: float, step: float) -> int | None:
    """The index of the level nearest to ``value`` on the grid ``lower + i x step`` in its search scale (``Axis.level``)."""
    return Axis.of("value", lower, upper, step).level(value)


@dataclass(frozen=True)
class Table:
    """An index's rows on a grid: ``cells`` maps each occupied cell (its levels, in the grid's order) to its rows, best
    first by ``prefer``; ``left_out`` counts the rows no cell holds (no value for a coordinate, or beyond the grid)."""

    coordinates: list[str]
    grid: dict[str, tuple[float, float, float]]
    prefer: str
    cells: dict[tuple[int, ...], list[IndexRow]]
    left_out: dict[str, int] = field(default_factory=dict)
    axes: tuple[Axis, ...] = field(default=(), repr=False, compare=False)

    def levels(self) -> list[tuple[int, ...]]:
        """The occupied cells, sorted."""
        return sorted(self.cells)

    def values(self, cell: tuple[int, ...]) -> dict[str, float]:
        """The cell's level values per coordinate."""
        return {axis.name: float(axis.raw[i]) for axis, i in zip(self.axes, cell, strict=True)}

    def size(self) -> int:
        """The number of cells of the grid, occupied or not."""
        return math.prod(len(axis.raw) for axis in self.axes)


def table(index: Index, grid: dict[str, tuple[float, float, float]], prefer: str | None = None) -> Table:
    """The index's rows on ``grid`` (coordinate -> ``(lower, upper, step)``, coordinates being index columns), ranked within
    each cell by ``prefer``, ``max:<column>`` or ``min:<column>`` (default ``max:Qmin`` when the index has it, else
    ``max:Qp``); a row without that value ranks last, and the remaining ties go by ``(part, obs_id)``."""
    if not isinstance(grid, dict) or not grid:
        raise ValueError(f"grid: expected {{coordinate: [lower, upper, step], ...}} over the index's columns {index.columns}, got {grid!r}")
    axes = []
    for name, spec in grid.items():
        if name not in index.columns:
            raise ValueError(f"grid: {name!r} is not a column of the index; its columns are {index.columns}")
        if not isinstance(spec, list | tuple) or len(spec) != 3:
            raise ValueError(f"grid {name}: expected [lower, upper, step], got {spec!r}")
        axes.append(Axis.of(name, *spec))
    sense, column = _prefer(index, prefer)
    cells: dict[tuple[int, ...], list[IndexRow]] = {}
    left_out: collections.Counter[str] = collections.Counter()
    for row in index.rows:
        values = [row.values.get(axis.name) for axis in axes]
        if any(v is None for v in values):
            left_out["no value for a coordinate"] += 1
            continue
        cell = tuple(axis.level(v) for axis, v in zip(axes, values))
        if any(i is None for i in cell):
            left_out["beyond the grid"] += 1
            continue
        cells.setdefault(cell, []).append(row)

    def rank(row: IndexRow) -> tuple:
        v = row.values.get(column)
        missing = v is None or not math.isfinite(v)
        return (missing, 0.0 if missing else (-v if sense == "max" else v), row.part, row.obs_id)

    ranked = {cell: sorted(rows, key=rank) for cell, rows in sorted(cells.items())}
    return Table([a.name for a in axes], {a.name: (a.lower, a.upper, a.step) for a in axes}, f"{sense}:{column}", ranked,
                 dict(left_out), tuple(axes))


def _prefer(index: Index, prefer: str | None) -> tuple[str, str]:
    if prefer is None or prefer == "":
        return "max", "Qmin" if "Qmin" in index.columns else "Qp"
    sense, _, column = str(prefer).partition(":")
    if sense not in ("max", "min") or column not in index.columns:
        raise ValueError(f"prefer {prefer!r}: expected max:<column> or min:<column> over the index's columns {index.columns}")
    return sense, column


@dataclass(frozen=True)
class Pick:
    """``pick``'s answer: the occupied ``cell`` nearest to the targets, its level ``values`` and ``rows`` (best first),
    whether it is the targets' own cell (``exact``), the targets' own levels (``target``: None for a coordinate the
    target lies beyond) and ``distance``, per coordinate the cell's level minus the target's in levels (the grid
    continued past its ends for a target beyond them)."""

    cell: tuple[int, ...]
    values: dict[str, float]
    rows: list[IndexRow]
    exact: bool
    target: dict[str, int | None]
    distance: dict[str, int]


def pick(table: Table, targets: dict[str, float]) -> Pick:
    """The occupied cell nearest to ``targets`` (a value per coordinate of the table): the Euclidean distance over the
    coordinates in unit coordinates of the grid (each coordinate's levels mapped to [0, 1] in its search scale), a tie to
    the smallest cell in sorted order."""
    if not isinstance(targets, dict) or set(targets) != set(table.coordinates):
        raise ValueError(f"targets: expected one value for each coordinate of the grid {table.coordinates}, got {targets!r}")
    if not table.cells:
        raise ValueError(f"no row lies on the grid {table.grid} (left out: {table.left_out}): nothing to pick")
    wanted = []
    for axis in table.axes:
        value = targets[axis.name]
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise ValueError(f"target {axis.name}: expected a finite number, got {value!r}")
        u = axis.to_unit(float(value))
        if u is None:
            raise ValueError(f"target {axis.name}={value:g}: its grid is searched logarithmically, which takes positive values")
        wanted.append(u)
    own = {axis.name: axis.level(float(targets[axis.name])) for axis in table.axes}
    cell = tuple(own[c] for c in table.coordinates)
    exact = None not in cell and cell in table.cells
    if not exact:                                    # the targets' own cell, when occupied, is the nearest by construction
        cell, nearest = None, math.inf
        for candidate in table.levels():
            d = math.sqrt(sum((axis.unit[i] - u) ** 2 for axis, i, u in zip(table.axes, candidate, wanted)))
            if d < nearest:
                cell, nearest = candidate, d
    distance = {axis.name: int(i - axis.position(float(targets[axis.name]))) for axis, i in zip(table.axes, cell)}
    return Pick(cell, table.values(cell), table.cells[cell], exact, own, distance)


# -- answers ------------------------------------------------------------------------------------------------------------

def row_json(row: IndexRow, why: str | None = None) -> dict:
    """An index row as an answer shows it: electrical values, geometry, footprint (``footprint_why`` beside a None),
    part and obs id, the sNp path relative to the library root and its column labels."""
    out = {"part": row.part, "obs_id": row.obs_id, "values": dict(row.values), "params": dict(row.params), "footprint": row.footprint}
    if row.footprint is None and why:
        out["footprint_why"] = why
    return {**out, "snp": row.snp, "ports": list(row.ports)}


def summary(library: query.Library, index: Index) -> dict:
    """``lib.index`` without a grid: rows kept and dropped (and why), each column's range, the footprints, the parts' sweeps."""
    ranges = {}
    for c in index.columns:
        v = [r.values[c] for r in index.rows if r.values.get(c) is not None]
        ranges[c] = {"rows": len(v), "min": min(v) if v else None, "max": max(v) if v else None, "unit": _unit_of(c)}
    missing = collections.Counter(why for (box, why) in _whys(library, index) if box is None)
    return {"stratum": index.stratum, "frequency_hz": index.frequency_hz, "srf_margin": index.srf_margin, "key": index.key,
            "columns": index.columns, "rows": {"kept": len(index.rows), "dropped": sum(index.dropped.values()), "why": index.dropped},
            "ranges": ranges, "footprints": {"rows": len(index.rows) - sum(missing.values()), "none": dict(missing)},
            "sweeps": {part: {"start_hz": a, "stop_hz": b} for part, (a, b) in library.sweeps(index.stratum).items()}}


def table_summary(t: Table) -> dict:
    """``lib.index`` with a grid: cells occupied of how many, rows per occupied cell (median, largest), and per coordinate
    its levels, the span of the occupied ones (index and value) and how many hold no row."""
    per_cell = [len(rows) for rows in t.cells.values()]
    coordinates = {}
    for j, axis in enumerate(t.axes):
        used = sorted({cell[j] for cell in t.cells})
        coordinates[axis.name] = {"levels": len(axis.raw), "log": axis.log,
                                  "occupied": [used[0], used[-1]] if used else None,
                                  "occupied_values": [float(axis.raw[used[0]]), float(axis.raw[used[-1]])] if used else None,
                                  "empty_levels": len(axis.raw) - len(used)}
    return {"coordinates": t.coordinates, "grid": {c: list(v) for c, v in t.grid.items()}, "prefer": t.prefer,
            "cells": {"occupied": len(t.cells), "of": t.size()},
            "rows": {"in_cells": sum(per_cell), "left_out": t.left_out},
            "rows_per_cell": {"median": float(np.median(per_cell)) if per_cell else None, "largest": max(per_cell, default=None)},
            "per_coordinate": coordinates}


def table_json(library: query.Library, index: Index, t: Table) -> dict:
    """The table as ``lib.index out=`` writes it: per occupied cell its levels, the level values, how many rows it holds
    and its best row."""
    whys = {(r.part, r.obs_id): why for r, (_box, why) in zip(index.rows, _whys(library, index))}
    cells = [{"levels": dict(zip(t.coordinates, cell)), "values": t.values(cell), "rows": len(t.cells[cell]),
              "best": row_json(t.cells[cell][0], whys[(t.cells[cell][0].part, t.cells[cell][0].obs_id)])} for cell in t.levels()]
    return {"stratum": index.stratum, "frequency_hz": index.frequency_hz, "srf_margin": index.srf_margin, "index_key": index.key,
            "grid": {c: list(v) for c, v in t.grid.items()}, "prefer": t.prefer, "cells": cells}


def pick_json(library: query.Library, index: Index, t: Table, p: Pick, n: int) -> dict:
    """``lib.pick``'s answer: the nearest occupied cell and its best ``n`` rows."""
    whys = {(r.part, r.obs_id): why for r, (_box, why) in zip(index.rows, _whys(library, index))}
    return {"stratum": index.stratum, "frequency_hz": index.frequency_hz, "srf_margin": index.srf_margin, "index_key": index.key,
            "grid": {c: list(v) for c, v in t.grid.items()}, "prefer": t.prefer,
            "cell": {"levels": dict(zip(t.coordinates, p.cell)), "values": p.values, "rows": len(p.rows)},
            "exact": p.exact, "target_levels": p.target, "distance": p.distance,
            "rows": [row_json(r, whys[(r.part, r.obs_id)]) for r in p.rows[:n]]}


def notes(index: Index, t: Table) -> list[str]:
    """What an answer on a table should say besides its numbers: a ``prefer`` column the index lacks (the default
    ``max:Qp`` of a stratum without a Qp curve), under which the rows of a cell rank by part and obs id alone."""
    column = t.prefer.partition(":")[2]
    if column in index.columns:
        return []
    return [(f"prefer {t.prefer}: the index has no {column} column, so the rows of a cell rank by part and obs id; give "
             f"prefer=max:<column> or min:<column> over {index.columns}")]


def _whys(library: query.Library, index: Index) -> list[tuple[dict | None, str | None]]:
    """Each index row's footprint and why it has none (``Library.footprints``)."""
    known = library.footprints(index.stratum)
    return [known[(r.part, r.obs_id)] for r in index.rows]


def _unit_of(column: str) -> str:
    if column == "area":
        return "um2"
    if column in ("Qmin",):
        return "1"
    return query.unit(column)


def write(path: str | Path, data: dict) -> Path:
    """Write ``data`` as strict JSON (no NaN or Infinity) to ``path``, its directory made if missing."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, allow_nan=False) + "\n", encoding="utf-8")
    return out
