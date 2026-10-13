"""A pcell device's allowed combinations, from a file (N-106, ``docs/refactor/N106_REGION_COMBINATIONS_SPEC.md``).

A spec can state only a rectangular range per variable, while the geometries that meet a set of targets often form a band:
for every width, the outer diameters that give one inductance. ``lib.region ... out=PATH`` writes the grid points of one of
its levels (robust unless ``level=mean``) as a combinations file (:func:`write`, through :func:`from_region`) and the device
and variables blocks to paste into a spec; a pcell device that names the file (``Device.combinations``) then takes only
those combinations: ``space.tables`` returns :func:`tables` (through ``link.tables``) beside a library device's rows, and the
space and every strategy honour the table as T18.2A taught them.

The file (:data:`FORMAT`)::

    {"format": "ic-opt-combinations-v1",
     "dims": ["outer_diameter_um", "width_um", "spacing_um", "turns"],       # generator field names
     "rows": [[52.0, 2.0, 2.0, 1], ...],                                     # one allowed geometry per row, in dims order
     "source": {"library": ..., "stratum": ..., "level": ..., "targets": {...}, "written": ...}}

Against a device (:func:`resolve`): a file dim the device maps to a spec variable (``Spec.device_fields``) becomes that
variable; a file dim it holds in ``fixed`` keeps only the rows whose value equals the fixed one (within :data:`TOLERANCE`),
and the plan line says how many that dropped; a file dim that is neither, or a device variable the file lacks, is refused.
Every kept row's value must be a level of its variable's grid (``lower + k * step``, within :data:`TOLERANCE`); rows on one
combination of levels collapse to one; an empty table is refused. A refusal is a ``link.LinkError``, which the command line
reports as an error line, in plan mode too.

A process reads a file once and keeps the table it gives a device, keyed by the file's path, the device's mapping and
fixed fields and its variables' grids: a run sees one table for its whole life, as with library devices. ``link.clear``
forgets them. The file is part of no identity (``Spec.problem``): it says which points are proposed, not what a point
evaluates to.
"""

from __future__ import annotations

import json
import math
import textwrap
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import yaml

from ic_opt import space
from ic_opt.library.link import LinkError

if TYPE_CHECKING:
    from ic_opt.library.query import Library
    from ic_opt.spec import Device, Spec

FORMAT = "ic-opt-combinations-v1"
TOLERANCE = 1e-9                                      # a value equals a fixed value or a grid level within this
LABEL = "device {device} (combinations {name})"     # what the space's messages call such a table

_LOCK = threading.RLock()                            # the engine's threads may ask for the tables at once
_FILES: dict[Path, tuple[list[str], np.ndarray]] = {}
_RESOLVED: dict[tuple[str, str], Combined] = {}


@dataclass(frozen=True)
class Combined:
    """One device's combinations file resolved against the spec: the file, the table over the device's variables (in the
    spec's order), the rows the file holds, and the rows its fixed fields dropped (``held``: file dim -> fixed value)."""

    device: str
    path: Path
    table: space.Table = field(repr=False)
    rows: int
    dropped: int
    held: dict[str, object]

    def sentence(self) -> str:
        """``device <id>: <n> allowed combinations of <variables> from <file> (<m> rows dropped by fixed <dim>=<value>)``."""
        text = (f"device {self.device}: {len(self.table.levels)} allowed combinations of {', '.join(self.table.names)} "
                f"from {self.path}")
        if self.held:
            text += (f" ({self.dropped} row{'' if self.dropped == 1 else 's'} dropped by fixed "
                     + ", ".join(f"{d}={_shown(v)}" for d, v in self.held.items()) + ")")
        return text


def tables(spec: Spec) -> list[space.Table]:
    """The tables of allowed combinations the spec's combinations files give, one per pcell device that names one; none
    for a spec without. ``link.tables`` returns them beside the library devices' (``space.tables`` is the one entry)."""
    return [combined.table for combined in resolve(spec).values()]


def resolve(spec: Spec) -> dict[str, Combined]:
    """Device id -> its combinations file resolved (:class:`Combined`), for every device of ``spec`` that names one, in the
    spec's order; read once per process (module docstring). A refusal is a ``LinkError`` and is not kept."""
    devices = [d for d in spec.devices if d.combinations is not None]
    if not devices:
        return {}
    with _LOCK:
        return {device.id: _combined(spec, device) for device in devices}


def lines(spec: Spec) -> list[str]:
    """What ``--plan`` prints for the combinations files: one line per device (``Combined.sentence``) and the number of
    valid points the search space then holds (``space.grid_size``) beside the variables' grids'; none without a file."""
    found = resolve(spec)
    if not found:
        return []
    full = math.prod(space.grid_count(v) for v in spec.variables)
    return [combined.sentence() for combined in found.values()] + [
        f"search space: {space.grid_size(spec)} valid points (the variables' grids hold {full})"]


def read(path: str | Path) -> tuple[list[str], np.ndarray]:
    """A combinations file's dims and rows (an (m, len(dims)) float array), read once per process."""
    path = _path(path)
    with _LOCK:
        if path not in _FILES:
            _FILES[path] = _parse(path)
        return _FILES[path]


def write(path: str | Path, dims: list[str], rows, source: dict) -> Path:
    """Write a combinations file (:data:`FORMAT`): ``dims`` (generator field names), ``rows`` (one allowed geometry per
    row, in ``dims`` order, in the generator's units) and ``source`` (where they come from); one row per line, strict JSON.
    What this process read from that path before is forgotten. Returns the absolute path."""
    path = _path(path)
    body = [json.dumps([_plain(v) for v in row], allow_nan=False) for row in rows]
    text = "\n".join(["{", f' "format": {json.dumps(FORMAT)},', f' "dims": {json.dumps(list(dims))},', ' "rows": [',
                      *(f"  {row}{',' if i < len(body) - 1 else ''}" for i, row in enumerate(body)),
                      " ],", f' "source": {json.dumps(source, allow_nan=False)}', "}"]) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    with _LOCK:
        _FILES.pop(path, None)
        for key in [key for key in _RESOLVED if key[0] == str(path)]:
            del _RESOLVED[key]
    return path


def clear() -> None:
    """Forget every file and table this process read (``link.clear`` calls it)."""
    with _LOCK:
        _FILES.clear()
        _RESOLVED.clear()


# -- lib.region out= ------------------------------------------------------------------------------------------------------

def from_region(library: Library, stratum: str, answer: dict, out: str | Path, *, targets: dict,
                device: str | None = None) -> dict:
    """``lib.region ... out=``: write the grid points of one level (``answer["level_points"]``, ``region``'s) as a
    combinations file at ``out`` and say how a spec takes it. Returns ``combinations`` (``{path, level, rows}``),
    ``device_block`` -- the YAML of a pcell device to paste under ``devices:``: ``id`` (``device``, else the stratum's
    name), the stratum's ``generator``, the library's ``profile``, the parts' port labels as ``ports``, ``fixed`` holding
    every dim with one value in the level and every other field the parts' devices fix to one value, ``variables`` mapping
    each other dim to ``<id>.<dim>``, and ``combinations`` -- and ``variables_block``, the YAML of those variables to paste
    under ``variables:``: the level's range per dim on the region's grid step (turns by 1), each with its unit as a
    comment. Both are indented as the repository's specs indent a list (``devices:`` then ``  - id: ...``). The blocks are
    a start, not a guarantee: a field the parts do not share is named in a comment, for the user to state."""
    from ic_opt.library import dataset
    from ic_opt.spec import DEFAULT_PLUGIN

    found = answer["level_points"]
    level, dims, rows = found["level"], list(found["dims"]), found["rows"]
    if not rows:
        other = "mean" if level == "robust" else "robust"
        raise ValueError(f"lib.region out=: the {level} level holds no grid point, so there is no combination to write "
                         f"(the {other} level holds {answer['levels'][other]['count']}; level={other}, or other targets)")
    ds = library.dataset(stratum)
    rows = [[round(float(v)) if d == ds.nt_dim else float(v) for d, v in zip(dims, row, strict=True)] for row in rows]
    path = write(out, dims, rows, {"library": str(library.root), "stratum": stratum, "level": level, "targets": targets,
                                   "written": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")})
    ident = device or stratum
    columns = {d: [row[j] for row in rows] for j, d in enumerate(dims)}
    single = {d: values[0] for d, values in columns.items() if max(values) - min(values) <= TOLERANCE}
    parts = list(dataset.part_devices(library.root, library.manifest.strata[stratum]).values())
    first = parts[0]
    shared = {k: v for k, v in first.fixed.items() if k not in dims and all(p.fixed.get(k) == v for p in parts)}
    unshared = {k: sorted({json.dumps(p.fixed.get(k)) for p in parts}) for p in parts for k in p.fixed
                if k not in dims and k not in shared}
    block = {"id": ident, "generator": library.manifest.strata[stratum].generator}
    if first.plugin != DEFAULT_PLUGIN:
        block["plugin"] = first.plugin
    block.update({"profile": library.manifest.process_profile, "ports": list(first.ports), "fixed": {**single, **shared},
                  "variables": {d: f"{ident}.{d}" for d in dims if d not in single}, "combinations": str(path)})
    device_block = textwrap.indent(yaml.safe_dump([block], sort_keys=False, default_flow_style=None, width=120), "  ")
    if unshared:
        device_block += ("  # not in fixed -- the parts differ, state the value this device takes: "
                         + "; ".join(f"{k} ({', '.join(values)})" for k, values in unshared.items()) + "\n")
    variables = []
    for d in dims:
        if d in single:
            continue
        integer = d == ds.nt_dim
        step = 1 if integer else answer["grid"]["steps"][d]
        variables.append(f'  - {{name: {ident}.{d}, kind: {"integer" if integer else "continuous_step"}, '
                         f'lower: "{_text(min(columns[d]))}", upper: "{_text(max(columns[d]))}", step: "{_text(step)}"}}'
                         f"   # {'um' if d.endswith('_um') else 'count'}")
    return {"combinations": {"path": str(path), "level": level, "rows": len(rows)}, "device_block": device_block,
            "variables_block": "\n".join(variables) + ("\n" if variables else "")}


def printed(answer: dict) -> str:
    """The blocks of ``lib.region out=`` as the command line prints them after the answer."""
    return (f"# paste under devices: (lib.region out={answer['combinations']['path']}; edit the id and the fixed fields)\n"
            f"{answer['device_block']}\n# paste under variables:\n{answer['variables_block']}")


# -- internals ------------------------------------------------------------------------------------------------------------

def _path(text: str | Path) -> Path:
    return Path(text).expanduser().absolute()


def _parse(path: Path) -> tuple[list[str], np.ndarray]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LinkError(f"combinations file {path}: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise LinkError(f"combinations file {path}: not an {FORMAT} document (lib.region out= writes one)")
    dims = doc.get("dims")
    if not isinstance(dims, list) or not dims or not all(isinstance(d, str) for d in dims) or len(set(dims)) != len(dims):
        raise LinkError(f"combinations file {path}: dims must be a list of distinct generator field names")
    try:
        rows = np.array(doc.get("rows", []), dtype=float)
    except (TypeError, ValueError) as exc:
        raise LinkError(f"combinations file {path}: rows must be lists of {len(dims)} numbers, one per dim") from exc
    rows = rows.reshape(0, len(dims)) if rows.size == 0 else rows
    if rows.ndim != 2 or rows.shape[1] != len(dims):
        raise LinkError(f"combinations file {path}: rows must be lists of {len(dims)} numbers, one per dim")
    return list(dims), rows


def _combined(spec: Spec, device: Device) -> Combined:
    path = _path(device.combinations)
    fields = spec.device_fields(device)
    grids = [(v.name, v.lower, v.upper, v.step) for v in spec.variables if v.name in fields.values()]
    key = (str(path), json.dumps([device.id, sorted(fields.items()), device.fixed, grids], sort_keys=True, default=str))
    if key not in _RESOLVED:
        _RESOLVED[key] = _build(spec, device, path, fields)
    return _RESOLVED[key]


def _build(spec: Spec, device: Device, path: Path, fields: dict[str, str]) -> Combined:
    """The device's table: its variables' level indices of the file's rows that its fixed fields keep."""
    dims, rows = read(path)
    label = LABEL.format(device=device.id, name=path.name)
    stray = [d for d in dims if d not in fields and d not in device.fixed]
    if stray:
        raise LinkError(f"{label}: the file's dim {stray[0]} is neither a variable of the device ({_mapping(fields)}) nor one "
                        f"of its fixed fields: map it to a spec variable in variables, or hold it in fixed")
    missing = [f for f in fields if f not in dims]
    if missing:
        raise LinkError(f"{label}: the device maps {missing[0]} to {fields[missing[0]]}, and the file has no dim "
                        f"{missing[0]} (its dims: {', '.join(dims)})")
    held = {d: device.fixed[d] for d in dims if d not in fields}
    keep = np.ones(len(rows), dtype=bool)
    for d, value in held.items():
        keep &= np.abs(rows[:, dims.index(d)] - float(value)) <= TOLERANCE
    kept = np.nonzero(keep)[0]
    if not len(kept):
        holds = "; ".join(f"{d} {_span(rows[:, dims.index(d)])}" for d in held)
        raise LinkError(f"{label}: no combination is left: the file holds {len(rows)} rows"
                        + (f", none at fixed {', '.join(f'{d}={_shown(v)}' for d, v in held.items())} ({holds})" if held else ""))
    variables = [v for v in spec.variables if v.name in fields.values()]          # the spec's order
    field_of = {name: f for f, name in fields.items()}
    columns = []
    for variable in variables:
        values = rows[kept, dims.index(field_of[variable.name])]
        levels = space.grid_levels(variable)[0]
        above = np.clip(np.searchsorted(levels, values), 0, len(levels) - 1)
        below = np.clip(above - 1, 0, len(levels) - 1)
        k = np.where(np.abs(levels[below] - values) <= np.abs(levels[above] - values), below, above)
        off = ~(np.abs(levels[k] - values) <= TOLERANCE)
        if off.any():
            i = int(np.argmax(off))
            near = sorted({float(levels[below[i]]), float(levels[above[i]])})
            raise LinkError(f"{label}: row {kept[i] + 1}: {field_of[variable.name]}={values[i]:.12g} is not on the grid of "
                            f"{variable.name} ({variable.lower} to {variable.upper} step {variable.step}); the nearest "
                            f"{'levels are' if len(near) > 1 else 'level is'} {' and '.join(f'{x:.12g}' for x in near)}")
        columns.append(k)
    combinations = tuple(map(tuple, np.column_stack(columns).tolist())) if columns else ()
    return Combined(device.id, path, space.Table(tuple(v.name for v in variables), combinations, label), len(rows),
                    len(rows) - len(kept), held)


def _mapping(fields: dict[str, str]) -> str:
    return ", ".join(f"{f} -> {name}" for f, name in fields.items()) or "none"


def _span(values: np.ndarray) -> str:
    lo, hi = float(values.min()), float(values.max())
    return f"{lo:.12g}" if lo == hi else f"{lo:.12g} to {hi:.12g}"


def _shown(value: object) -> str:
    return f"{value:.12g}" if isinstance(value, float) else str(value)


def _plain(value):
    """A row value as JSON takes it: an int stays an int, anything else becomes a float."""
    return value if isinstance(value, int) and not isinstance(value, bool) else float(value)


def _text(value: float) -> str:
    """A number as a grid text without exponent or trailing zeros: 52.0 -> "52", 0.4 -> "0.4"."""
    return format(Decimal(repr(round(float(value), 9))).normalize(), "f")


__all__ = ["FORMAT", "LABEL", "TOLERANCE", "Combined", "clear", "from_region", "lines", "printed", "read", "resolve",
           "tables", "write"]
