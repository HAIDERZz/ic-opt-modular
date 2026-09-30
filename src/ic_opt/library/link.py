"""A spec's library devices resolved against their libraries (T18.2B specification, section 2,
``docs/refactor/T18_2B_LIBRARY_DEVICE_SPEC.md``), and the tables of allowed combinations they put on the spec's variables:
``ic_opt.space.tables`` returns :func:`tables` (T18.2A specification, section 1), which the space, the strategies and the
point blocks read without knowing of libraries.

A library device (``Device.library``) names one table (``stratum``) of the library at ``root`` and a working frequency;
its ``variables`` map index columns (``Lp``, ``Ls``, ``k``, ``Qmin``, ...: ``ic_opt.library.index``) to spec variables of
electrical values on ordinary grids. :func:`resolve` opens the library (``query.Library`` without calibration and without
limits: nothing is fitted; its cache files go where ``cache.locate`` puts them, so a library shared read-only works),
builds the index at ``frequency_hz`` with ``srf_margin`` (``index.build``) and the table on the variables' grids read in SI
units (``space.si_value``, then ``index.table`` with ``prefer``), and from it the ``space.Table`` over those variables in
the spec's order, one combination per occupied cell. The levels are computed there, once: everything else finds a row by
its combination (:func:`row_for`), so no second rounding exists. (``index.level`` and ``Coords.snap`` agree on the same
numbers; a grid text with a scale suffix can differ from its SI value only at an exact tie.)

A process resolves a device once and keeps the answer, keyed by the resolved root, the stratum, the frequency, the margin,
``prefer`` and the variables' grids: a run sees one table for its whole life, whatever happens to the library meanwhile.
The next process sees a library that grew: a new generation, since ``Pick``'s identity (:func:`identity`: the index's
content key, the grids, ``prefer``) is part of the pipeline fingerprint. :func:`clear` forgets what this process resolved
(tests).

Refusals, each a ValueError a user can act on: no ``library.yaml`` at the root; an unknown stratum (its strata are listed);
a variable's column that is no column of the index (they are listed); the device's ports that are not the table's (both
are said); no row on the grid (each coordinate's range in the index beside its variable's range); and what the index
refuses (a frequency no row can give, a ``prefer`` column the index lacks), each prefixed with the device.

A device that states no topology is measured as the library measures its rows (``Linked.topologies``): each row by its
part's topology, with the stratum's ``low_freq_max_hz`` when the manifest sets one.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ic_opt import space

if TYPE_CHECKING:
    from ic_opt.library.index import Index, IndexRow
    from ic_opt.library.index import Table as IndexTable
    from ic_opt.library.query import Library
    from ic_opt.spec import Device, Spec, Topology, Variable

LABEL = "device {device} (library rows)"          # what the space's messages call a library device's table

_LOCK = threading.RLock()                        # resolution reads the library once; the engine's threads wait for it
_ROOTS: dict[str, Path] = {}                     # a root as written -> resolved, once per process
_LIBRARIES: dict[Path, Library] = {}
_CORES: dict[tuple, tuple[Index, IndexTable]] = {}
_LINKED: dict[tuple, Linked] = {}


@dataclass(frozen=True)
class Linked:
    """One library device resolved: its library, the index at its frequency, the index's rows on its variables' grids
    (``table``: coordinates in ``names``' order, so a cell is a combination of the variables' level indices), the
    ``space.Table`` of the occupied combinations, the table's port labels and, for a device that states no topology, each
    part's (``topologies``; empty when the device states its own)."""

    device: str
    root: Path
    library: Library = field(repr=False)
    index: Index = field(repr=False)
    table: IndexTable = field(repr=False)
    names: tuple[str, ...]                        # the device's spec variables, in the spec's order
    coordinates: tuple[str, ...]                  # the index column of each of them
    space_table: space.Table = field(repr=False)
    ports: tuple[str, ...]                        # the table's port labels (the first row's order)
    topologies: dict[str, Topology] = field(default_factory=dict, repr=False)

    def rows(self, combination) -> list[IndexRow]:
        """The rows of one combination (level indices over ``names``), best first by ``prefer``; none when it holds none."""
        return self.table.cells.get(tuple(int(k) for k in combination), [])

    def facts(self) -> dict:
        """What ``env.doctor``, ``--plan`` and the digest say of the table: the stratum, the frequency, the margin every
        row satisfies, the rule that ranks a combination's rows, the rows on the grid, the combinations they occupy and
        the grid's size."""
        return {"stratum": self.index.stratum, "frequency_hz": self.index.frequency_hz, "srf_margin": self.index.srf_margin,
                "prefer": self.table.prefer, "rows": sum(len(rows) for rows in self.table.cells.values()),
                "combinations": len(self.table.cells), "of": self.table.size()}

    def sentence(self) -> str:
        """``<stratum> at <f> GHz: <n> rows on the grid in <m> of <total> combinations, prefer <rule>``."""
        f = self.facts()
        text = (f"{f['stratum']} at {f['frequency_hz'] / 1e9:g} GHz: {f['rows']} rows on the grid in {f['combinations']} of "
                f"{f['of']} combinations, prefer {f['prefer']}")
        column = f["prefer"].partition(":")[2]
        if column not in self.index.columns:     # the default max:Qp of a table without a Qp curve (index.notes)
            text += f" (the index has no {column} column: a combination's rows rank by part and obs id)"
        return text


def tables(spec: Spec) -> list[space.Table]:
    """The tables of allowed combinations of ``spec``'s variables: one per library device (its variables, in the spec's
    order; the combinations a row of its table sits on, each row at its own electrical values taken onto the variables'
    grid), none for a spec without library devices. ``ic_opt.space.tables`` returns this and checks it."""
    return [linked.space_table for linked in resolve(spec).values()]


def resolve(spec: Spec) -> dict[str, Linked]:
    """Device id -> its resolution (:class:`Linked`), for every library device of ``spec``, in the spec's order; empty for
    a spec without. Resolved once per process (module docstring); a refusal is a ValueError and is not kept."""
    devices = spec.library_devices
    if not devices:
        return {}
    with _LOCK:
        return {device.id: _linked(spec, device) for device in devices}


def row_for(spec: Spec, device_id: str, params: dict[str, str]) -> IndexRow | None:
    """The row a point takes for a library device: the best row (``prefer``) of the combination its values of the device's
    variables form; None when they form none the table holds (a value off the variable's grid, or a combination no row
    sits on)."""
    linked = resolve(spec)[device_id]
    combination = _combination(spec, linked, params)
    rows = linked.rows(combination) if combination is not None else []
    return rows[0] if rows else None


def missing(spec: Spec, device_id: str, params: dict[str, str]) -> str:
    """Why a point has no row for the device (``Pick``'s failure): the values it gives, and the nearest combination the
    table holds (``space.snap``: the nearest in unit coordinates, as the space measures it) when they can be read."""
    linked = resolve(spec)[device_id]
    given = " ".join(f"{name}={params.get(name)}" for name in linked.names)
    text = (f"device {device_id}: {given} is not a combination of its library table ({linked.index.stratum} at "
            f"{linked.index.frequency_hz / 1e9:g} GHz, {len(linked.table.cells)} combinations)")
    try:
        near = space.snap(spec, [float(space.parse_scalar(params[v.name])[0]) for v in spec.variables])
    except (KeyError, ValueError, TypeError):
        return text
    return text + "; the nearest one is " + " ".join(f"{name}={near[name]}" for name in linked.names)


def identity(spec: Spec) -> str:
    """What ``Pick``'s answer depends on, per library device: the index's content key (the dataset's content, the
    frequency, the margin, the index code), the grid (per coordinate its lower, upper and step in SI units) and
    ``prefer``. The pipeline fingerprint carries it, so a library that grew is a new generation; where the library sits
    is no part of it."""
    return json.dumps({device: {"stratum": linked.index.stratum, "index": linked.index.key, "prefer": linked.table.prefer,
                                "grid": {column: list(values) for column, values in linked.table.grid.items()}}
                       for device, linked in resolve(spec).items()}, sort_keys=True, separators=(",", ":"))


def summary(spec: Spec) -> dict[str, dict]:
    """Device id -> ``Linked.facts()``, for every library device of ``spec``."""
    return {device: linked.facts() for device, linked in resolve(spec).items()}


def sentence(spec: Spec, device_id: str) -> str:
    """The line ``env.doctor`` and ``--plan`` print for a library device (``Linked.sentence``)."""
    return resolve(spec)[device_id].sentence()


def clear() -> None:
    """Forget every library, index and table this process resolved (tests)."""
    with _LOCK:
        _ROOTS.clear()
        _LIBRARIES.clear()
        _CORES.clear()
        _LINKED.clear()


# -- internals ------------------------------------------------------------------------------------------------------------

def _linked(spec: Spec, device: Device) -> Linked:
    """``device``'s resolution, kept per process under what it depends on."""
    source = device.library
    root = _root(source.root)
    grids = _grids(spec, device)
    grid = {column: tuple(space.si_value(text) for text in (v.lower, v.upper, v.step)) for column, v in grids}
    core = (str(root), source.stratum, float(source.frequency_hz), source.srf_margin, source.prefer, tuple(grid.items()))
    topology = None if device.topology is None else device.topology.model_dump_json()
    key = (core, device.id, tuple(device.ports), topology, tuple(v.name for _, v in grids))
    if key not in _LINKED:
        if core not in _CORES:
            _CORES[core] = _core(device, root, grid)
        _LINKED[key] = _device(spec, device, root, grids, *_CORES[core])
    return _LINKED[key]


def _root(text: str) -> Path:
    if text not in _ROOTS:
        _ROOTS[text] = Path(text).expanduser().resolve()
    return _ROOTS[text]


def _grids(spec: Spec, device: Device) -> list[tuple[str, Variable]]:
    """(index column, spec variable) for each of the device's variables, in the spec's order."""
    column_of = {variable: column for column, variable in device.variables.items()}
    return [(column_of[v.name], v) for v in spec.variables if v.name in column_of]


def _library(device: Device, root: Path) -> Library:
    from ic_opt.library import manifest, query

    if root not in _LIBRARIES:
        if not (root / manifest.MANIFEST).is_file():
            raise ValueError(f"device {device.id}: no {manifest.MANIFEST} at {root} (library root: the directory holding "
                             f"{manifest.MANIFEST}, on the machine running ic-opt)")
        try:
            _LIBRARIES[root] = query.Library(root, calibrate=False)      # nothing is fitted: no calibration, no limits
        except ValueError as exc:
            raise ValueError(f"device {device.id}: {root / manifest.MANIFEST}: {exc}") from exc
    return _LIBRARIES[root]


def _core(device: Device, root: Path, grid: dict[str, tuple[float, float, float]]) -> tuple[Index, IndexTable]:
    """The index of the device's table at its frequency, and its rows on ``grid``."""
    from ic_opt.library import index

    source = device.library
    library = _library(device, root)
    if source.stratum not in library.manifest.strata:
        raise ValueError(f"device {device.id}: the library at {root} has no stratum {source.stratum!r}; its strata: "
                         f"{', '.join(library.strata())}")
    try:
        built = index.build(library, source.stratum, source.frequency_hz, srf_margin=source.srf_margin)
    except ValueError as exc:
        raise ValueError(f"device {device.id}: {exc}") from exc
    unknown = [column for column in grid if column not in built.columns]
    if unknown:
        raise ValueError(f"device {device.id}: {', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} no column of the "
                         f"index of {source.stratum} at {source.frequency_hz / 1e9:g} GHz; its columns: "
                         f"{', '.join(built.columns)}")
    try:
        return built, index.table(built, grid, source.prefer)
    except ValueError as exc:
        raise ValueError(f"device {device.id}: {exc}") from exc


def _device(spec: Spec, device: Device, root: Path, grids: list[tuple[str, Variable]], built: Index,
            table: IndexTable) -> Linked:
    """The core resolution checked against the device -- its variables' levels, its ports, a row on the grid -- and
    turned into the space's table."""
    for axis, (_column, variable) in zip(table.axes, grids, strict=True):
        if len(axis.raw) != space.grid_count(variable):
            raise ValueError(f"device {device.id}: {variable.name} has {space.grid_count(variable)} levels as written and "
                             f"{len(axis.raw)} in SI units; give a lower, upper and step that divide exactly")
    sets = {frozenset(row.ports): row.ports for row in built.rows}
    first = list(built.rows[0].ports)
    if len(sets) > 1:
        raise ValueError(f"device {device.id}: the rows of {built.stratum} disagree on their ports "
                         f"({'; '.join(', '.join(p) for p in sets.values())})")
    if set(device.ports) != set(first):
        raise ValueError(f"device {device.id}: its ports {list(device.ports)} are not the table's {first} ({built.stratum}): "
                         "a library device's ports are its table's port labels")
    if not table.cells:
        ranges = []
        for column, variable in grids:
            values = [row.values[column] for row in built.rows if row.values.get(column) is not None]
            held = f"{min(values):.4g} to {max(values):.4g}" if values else "no value"
            ranges.append(f"{column}: the index holds {held}, {variable.name} spans {variable.lower} to {variable.upper}")
        raise ValueError(f"device {device.id}: no row of {built.stratum} at {built.frequency_hz / 1e9:g} GHz lies on the grid "
                         f"of its variables ({table.left_out}) -- " + "; ".join(ranges))
    names = tuple(variable.name for _, variable in grids)
    linked_table = space.Table(names, tuple(table.levels()), LABEL.format(device=device.id))
    return Linked(device.id, root, _LIBRARIES[root], built, table, names, tuple(column for column, _ in grids), linked_table,
                  tuple(first), {} if device.topology is not None else _topologies(device, root, built.stratum))


def _topologies(device: Device, root: Path, stratum: str) -> dict[str, Topology]:
    """Each part's topology as the library measures its rows (``dataset._measure``): the part's device's, stated or
    defaulted at its spec's load, with the stratum's ``low_freq_max_hz`` when the manifest sets one."""
    from ic_opt.library import dataset

    rule = _LIBRARIES[root].manifest.strata[stratum]
    out = {}
    for part, part_device in dataset.part_devices(root, rule).items():
        topology = part_device.topology or part_device.default_topology()
        if rule.low_freq_max_hz is not None:
            topology = topology.model_copy(update={"low_freq_max_hz": rule.low_freq_max_hz})
        out[part] = topology
    return out


def _combination(spec: Spec, linked: Linked, params: dict[str, str]) -> tuple[int, ...] | None:
    """The level indices ``params`` gives the device's variables (``k`` for ``lower + k * step``, the variable's own
    suffix), or None when a value is missing or off its variable's grid."""
    variables = {v.name: v for v in spec.variables}
    out = []
    for name in linked.names:
        variable, text = variables[name], params.get(name)
        try:
            value, unit = space.parse_scalar(str(text))
            lower, lower_unit = space.parse_scalar(variable.lower)
            step = space.parse_scalar(variable.step)[0]
        except ValueError:
            return None
        offset = (value - lower) / step
        if unit != lower_unit or offset != offset.to_integral_value() or not 0 <= offset < space.grid_count(variable):
            return None
        out.append(int(offset))
    return tuple(out)


__all__ = ["LABEL", "Linked", "clear", "identity", "missing", "resolve", "row_for", "sentence", "summary", "tables"]
