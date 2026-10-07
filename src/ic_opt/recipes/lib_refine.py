"""Built-in recipe: lib_refine -- the library grows around a run's best point: the geometries next to the row its library
device took that the table does not hold, predicted, ranked, measured with a few real EMX runs and adopted into the row's
part, so that the run's next round has rows between the table's own.

``ic-opt run lib_refine PROJECT [device=<id>] [steps=1] [n=8] [prefer=<max|min>:<column>] [threads=N] [memory_gb=G]
[process_file=/abs/path.proc] [cache_dir=DIR] --plan``

Why: a circuit optimization whose devices come from library tables (``devices[*].library``, T18.2B) moves between the rows
the table holds and nothing else. lib_refine measures the geometries around the best point's row that the table lacks and
adopts them as rows; the run then goes on with the grown table. Every adopted row is a real measurement of the library's
generation and the circuit re-optimizes on it directly: no correction of the models at the device's ports.

PROJECT is the optimization run's project: its spec names the library devices, its store holds the observations. The best
point is the feasible observation of this problem (the spec's fingerprint, every step) with the best objective, a point
re-checked at every corner (the steps ``signoff`` and ``signoff#<k>`` of the signoff recipe) first; without a feasible
point the call is refused, naming how many points there are. ``device``: one library device of the spec, by id; every
library device when not given, one pass each (two devices on one table: a geometry one pass chose is left out of the
next's, so none is simulated and adopted twice). A device's row is the one the best point's child recorded
(``ChildResult.library_row``: stratum, part, obs id, geometry, values).

The local grid: every geometry whose dims differ from the row's by at most ``steps`` steps of the stratum's ``steps``
(library.yaml); the turns dim keeps the row's level (another turns level is another model, not a neighbour), and so does a
dim without a step; the row itself and every geometry the table holds are left out (they are candidates already).
``steps=1`` gives at most 3^d - 1 neighbours over the d dims with a step, ``steps=2`` 5^d - 1.

The window: each neighbour goes through ``query.query`` (the calibrated 2-sigma interval, each column's confidence ceiling)
for the index columns the device's variables map to, at the device's frequency -- a curve ``Lp`` is the column ``Lp@<f>``,
``Qmin`` the smaller of ``Qp@<f>`` and ``Qs@<f>``, a scalar itself, ``area`` the footprint of the geometry drawn by the
generator -- for the ``prefer`` column, and for the system ``SRF``. A neighbour is kept when every variable's column is
``predicted``, its predicted value lies inside the spec variable's range -- the window the run searched, as the table takes
a row onto the variable's grid (``index.Axis.level``: the end levels reach half an end interval beyond the bounds, in the
variable's search scale), so that a row adopted at that value is a candidate of the run -- and its predicted SRF lies above
the index's margin x the frequency (the margin every row of the device's table satisfies: the largest of the manifest's
curves', or the device's ``srf_margin`` when that is larger; an SRF above the sweep is above it). The first rule a
neighbour fails drops it, in this order: ``out_of_domain`` (a variable's column or the SRF), ``uncertain`` (a variable's
column or the SRF not ``predicted`` otherwise: too uncertain, no value), ``outside_range``, ``srf_margin``. A stratum
that declares no ``SRF`` cannot have the last rule applied, and a line says so. ``area`` is drawn only for the neighbours
the models leave in the window (a pcell build each).

Ranked by ``prefer`` (``max:<column>`` / ``min:<column>``, an index column; default the device's own ``prefer``, else the
index's ``max:Qmin`` / ``max:Qp``) on the predicted value, a neighbour without a predicted value for it last, then the one
fewer steps from the row; then the preflight of ``lib_tap`` (``lib_tap.preflight``: the generator and the pcell stage's DRC
gate, no EMX, on the controller within its ``hosts.local`` entry) in rank order: a refused neighbour is listed with the
reason and the next in rank takes its place, until ``n`` are clean or the list runs out. Each candidate also names the
combination of the device's variables its predicted values sit on (the level nearest each value, as the table takes a row
onto the grid), whether the table holds that combination already (an adopted row there competes with the rows it holds,
by ``prefer``) and whether the run evaluated it (a combination already evaluated keeps its observation and is not
proposed again).

``--plan`` stops there and prints the best point and each device's row, the local grid and what each rule dropped, the
candidates with their predictions, combinations and preflight outcomes, the envelope (jobs, EMX threads, memory cap), the
part's rows' own EMX peak memory (from their ``emx.log``, as ``lib_tap`` prints it) and the budget. Real EMX: run with
``--plan`` first; it is the approval point. The models of the columns asked are fitted first when they are not cached, in
``--plan`` too (minutes per column on a large stratum).

EMX and the check, as ``lib_signoff``'s: the clean candidates go through ``em_only_pipeline`` into PROJECT's store, one step
per device (``lib_refine:<part>``), with the spec of the row's part -- its generator, fixed fields, EMX physics and ground
fixture -- and this run's ``threads`` / ``memory_gb`` / ``process_file``, which are not physics: the candidates are the
library's generation. PROJECT's spec gives the run's resources (``simulator.parallel_jobs``) and its budget counts them
(``budget.max_simulations``: a shortfall is refused before any EMX runs). The predictions are taken before EMX; each ok
candidate is then measured with the library's definitions (the stratum's bands, anchors, low-frequency limit) and
compared: per model column the predicted mu, the calibrated bounds, the measured value, z in the model's space and whether
it lies inside; per index column the measured value at the device's frequency and its ratio to the row's; the combination
the measured values sit on and whether the device's index would keep the row (every curve has a value there and the
system SRF lies above the margin).

Adopt, always (the point of the step): every ok candidate goes into the row's part store under a fresh obs id with the
origin ``refine:<project>:<best obs id>`` (``lib_signoff._adopt``; the part's ``adopted.yaml`` appended); ``library.yaml``
is never touched. The stratum's dataset is then built again (``Library.dataset``, cached) and its rows reported. A failed
candidate, or one whose sNp the library cannot measure, is reported and not adopted.

What next, the last line: the grown table is a new generation for the next process (``link.identity``, part of the
pipeline fingerprint); the run continues with its own recipe and a larger budget -- the command, with the project's path:
the recipe read from the best point's step (``optimize``, ``coarse`` / ``fine``, ``search@<corner>`` / ``signoff``), the
budget the points of its search step so far plus one per adopted row, the run's own strategy, batch and seed to be added
-- the adopted rows being candidates of its next batch, the previous observations staying (the same spec fingerprint).
lib_refine does not continue the run: that is the user's call, and the next lib_refine after it is another round.

``.icopt/reports/lib_refine.json`` holds the call, the best point, and per device its row, the local grid and what each
rule dropped, the candidates with their predictions and preflight outcomes, per measured candidate its values, ratios, z
and inside, the adopted obs ids and the dataset's rows after; one note line per device gives candidates / ok / adopted
and the row's ``prefer`` value against the best adopted one. ``cache_dir`` is where the library's cache files go (as for
``lib_signoff``).
"""

from __future__ import annotations

import itertools
import json
import math
import re
import statistics
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from ic_opt import blocks as b
from ic_opt import space
from ic_opt.digest import _best_of
from ic_opt.em import measure as measure_kernel
from ic_opt.em import touchstone
from ic_opt.em.pcell import footprint as fp
from ic_opt.eval import engine
from ic_opt.library import dataset, index, link, manifest, query
from ic_opt.observation import Observation
from ic_opt.recipe import Run
from ic_opt.recipes.lib_signoff import _adopt, _signoff_em, _z
from ic_opt.recipes.lib_tap import (
    Selected,
    Twins,
    _median_max,
    _observations,
    _peak_gb,
    _per_point,
    _scalar,
    _strict,
    preflight,
)
from ic_opt.space import Point
from ic_opt.spec import Device, Spec
from ic_opt.stages.em_chain import em_only_pipeline

K = 2.0                                               # the calibrated interval in model sigmas (lib_signoff's default k)
RECHECK = re.compile(r"^signoff(#\d+)?$")            # the signoff recipe's re-check steps: points evaluated at every corner
RULES = ("out_of_domain", "uncertain", "outside_range", "srf_margin")      # the window's rules, in the order they are tried
REPORT = "lib_refine.json"


class RefineError(ValueError):
    """A lib_refine call that cannot be carried out as given: refused before anything is built or simulated."""


# -- the best point ----------------------------------------------------------------------------------------------------

def best_point(spec: Spec, observations) -> tuple[Observation, bool, list[Observation]]:
    """The run's best point: the feasible observation of this problem (the spec's fingerprints, every step) with the lowest
    objective (``digest._best_of``: minimization form, the first of equals), a re-checked one (``RECHECK``) first. Also
    whether it was re-checked, and this problem's observations. Refused without a feasible point."""
    same = {spec.fingerprint(), spec._legacy_fingerprint()}
    rows = [o for o in observations if o.spec_fingerprint in same]
    checked = _best_of([o for o in rows if RECHECK.match(o.step)])
    best = checked or _best_of(rows)
    if best is None:
        raise RefineError(f"no feasible point among the {len(rows)} points of this problem: lib_refine refines around the run's "
                          "best feasible point; run the optimization until it finds one")
    return best, checked is not None, rows


def row_of(o: Observation, device_id: str) -> dict:
    """The library row the point's device child recorded (``ChildResult.library_row``)."""
    child = next((c for c in o.children.values() if c.unit == device_id and c.library_row), None)
    if child is None:
        raise RefineError(f"the best point {o.obs_id} recorded no library row for device {device_id} (its children: "
                          f"{sorted(o.children)}): a point of a library device's run names the row it took")
    return dict(child.library_row)


# -- the local grid ----------------------------------------------------------------------------------------------------

@dataclass
class Neighbour:
    params: dict[str, float]                          # the stratum's dims
    offset: dict[str, int]                            # steps from the row, per dim with a step
    label: str = ""                                   # c1, c2, ... in rank order
    point: Point | None = None
    answers: dict[str, dict] = field(default_factory=dict)       # model column -> its answer (query.query)
    predicted: dict[str, dict] = field(default_factory=dict)     # index column -> its answer
    srf: dict | None = None                           # the system SRF's answer (None: the stratum declares no SRF)
    verdict: str | None = None                        # the rule that dropped it; None: kept
    detail: str = ""                                  # what the rule found
    combination: tuple[int | None, ...] = ()
    preflight: str | None = None                      # "clean" | "generator" | "drc"; None: not preflighted
    reasons: tuple[str, ...] = ()

    @property
    def steps(self) -> int:
        return sum(abs(k) for k in self.offset.values())


def _decimal(value: float) -> Decimal:
    return Decimal(repr(float(value)))


def _text(value: float) -> str:
    """A dim's value as a point's parameter text: the shortest exact decimal (``101``, ``5.1``)."""
    text = format(_decimal(value).normalize(), "f")
    return "0" if text in ("-0", "") else text


def local_grid(ds: dataset.Dataset, rule: manifest.Stratum, geometry: dict[str, float], steps: int) -> dict:
    """The geometries whose dims differ from ``geometry`` by at most ``steps`` of the stratum's steps (the turns dim and the
    dims without a step held), without ``geometry`` itself and the table's own rows: ``{"varied", "held", "size",
    "in_table", "neighbours"}``, ``size`` the grid's geometries but the row (``(2 steps + 1)^d - 1``)."""
    varied = [d for d in ds.dims if d != ds.nt_dim and d in rule.steps]
    held = [d for d in ds.dims if d not in varied]
    if not varied:
        raise RefineError(f"stratum {ds.stratum} declares no steps for its dims {[d for d in ds.dims if d != ds.nt_dim]} "
                          "(library.yaml, steps: the candidate resolution per dim): lib_refine moves a dim by its step")
    table = {tuple(round(r.coords[d], 9) for d in ds.dims) for r in ds.rows}
    out, size, in_table = [], 0, 0
    for offsets in itertools.product(range(-steps, steps + 1), repeat=len(varied)):
        if not any(offsets):
            continue
        size += 1
        params = dict(geometry)
        for d, k in zip(varied, offsets, strict=True):
            params[d] = float(_decimal(geometry[d]) + k * _decimal(rule.steps[d]))
        if tuple(round(params[d], 9) for d in ds.dims) in table:
            in_table += 1
            continue
        out.append(Neighbour(params, dict(zip(varied, offsets, strict=True))))
    return {"varied": {d: rule.steps[d] for d in varied}, "held": held, "size": size, "in_table": in_table, "neighbours": out}


# -- predictions and the window ------------------------------------------------------------------------------------------

def model_columns(lib: query.Library, stratum: str, columns: list[str], ghz: float) -> dict[str, list[str]]:
    """Index column -> the model columns it is predicted from, at ``ghz`` GHz (a curve's extension column where the stratum
    does not anchor it, ``Library.columns``); ``area`` has none (it is drawn)."""
    out: dict[str, list[str]] = {}
    for c in columns:
        if c in manifest.CURVES:
            out[c] = [lib.column(stratum, f"{c}@{ghz!r}")]
        elif c == "Qmin":
            out[c] = lib.columns(stratum, [f"Qp@{ghz!r}", f"Qs@{ghz!r}"])
        elif c == "area":
            out[c] = []
        else:
            out[c] = [lib.column(stratum, c)]
    return out


def _worse(statuses: list[str]) -> str:
    for status in ("out_of_domain", "uncertain", "above_sweep"):
        if status in statuses:
            return status
    return statuses[0]


def _answer(c: str, cols: list[str], answers: dict) -> dict:
    """An index column's answer from the model columns' (``query.query``): one column's own, or ``Qmin`` from both Q's."""
    if c != "Qmin":
        return answers[cols[0]]
    qp, qs = answers[cols[0]], answers[cols[1]]
    status = _worse([qp["status"], qs["status"]])
    if any(a.get("value") is None for a in (qp, qs)):
        return {"status": status, "value": None}
    return {"status": status, "value": min(qp["value"], qs["value"]), "lo": min(qp.get("lo", math.nan), qs.get("lo", math.nan)),
            "hi": min(qp.get("hi", math.nan), qs.get("hi", math.nan)), "from": cols}


def verdict(predicted: dict[str, dict], srf: dict | None, axes: dict[str, index.Axis], margin_hz: float) -> tuple[str | None, str]:
    """The window's rule a neighbour fails first (``RULES``) and what it found, or ``(None, "")`` when it is kept.
    ``predicted``: the answers of the device's variables' index columns; ``srf``: the system SRF's (None: the stratum
    declares none); ``axes``: index column -> the spec variable's grid in SI units (``Linked.table.axes``): a value is inside
    the variable's range when the table would take it onto that grid (``Axis.level``: the end levels reach half an end
    interval beyond the bounds, in the variable's search scale); ``margin_hz``: the index's margin x
    the frequency. An SRF above the sweep (``above_sweep``) is above the margin, as the index keeps a row without a
    resonance in its sweep."""
    for c, a in predicted.items():
        if a["status"] == "out_of_domain":
            return "out_of_domain", f"{c}: {a.get('reason', 'out of domain')}"
    if srf is not None and srf["status"] == "out_of_domain":
        return "out_of_domain", f"SRF: {srf.get('reason', 'out of domain')}"
    for c, a in predicted.items():
        if a["status"] not in ("predicted", "drawn") or a.get("value") is None:
            return "uncertain", f"{c}: {a['status']}"
    if srf is not None and srf["status"] not in ("predicted", "above_sweep"):
        return "uncertain", f"SRF: {srf['status']}"
    for c, a in predicted.items():
        axis = axes[c]
        if axis.level(a["value"]) is None:
            return "outside_range", f"{c} {a['value']:.4g} outside [{axis.lower:.4g}, {axis.upper:.4g}]"
    if srf is not None and srf["status"] == "predicted" and srf["value"] <= margin_hz:
        return "srf_margin", f"SRF {srf['value']:.4g} Hz at or below {margin_hz:.4g} Hz"
    return None, ""


def _drawn(lib: query.Library, stratum: str, params: dict) -> dict:
    """``area`` for a geometry the table does not hold: the footprint of the geometry the generator draws (no EMX)."""
    box, why = query.footprint_at(lib, stratum, params, build=True)
    return {"status": "drawn", "value": box["area_um2"]} if box is not None else {"status": "no footprint", "value": None, "reason": why}


# -- combinations on the device's grid -----------------------------------------------------------------------------------

def combination(linked: link.Linked, values: dict[str, float | None]) -> tuple[int | None, ...]:
    """The level each variable's index column value sits on (``index.Axis.level``, the table's own rule); None beyond its
    grid or without a value."""
    return tuple(axis.level(values.get(column)) for axis, column in zip(linked.table.axes, linked.coordinates, strict=True))


def combination_text(spec: Spec, linked: link.Linked, cell: tuple[int | None, ...]) -> dict[str, str | None]:
    """A combination as the variables' own text (``150p``), None for a level beyond the grid."""
    variables = {v.name: v for v in spec.variables}
    out: dict[str, str | None] = {}
    for name, level in zip(linked.names, cell, strict=True):
        if level is None:
            out[name] = None
            continue
        lower, unit = space.parse_scalar(variables[name].lower)
        out[name] = space.format_value(lower + level * space.parse_scalar(variables[name].step)[0], unit)
    return out


def visited(spec: Spec, linked: link.Linked, rows: list[Observation]) -> set[tuple[int, ...]]:
    """The combinations this problem's observations evaluated."""
    return {cell for o in rows if (cell := link._combination(spec, linked, o.params)) is not None}


# -- measuring a candidate ---------------------------------------------------------------------------------------------

def measured(work: Path, device, rule: manifest.Stratum, f_hz: float, index_columns: list[str], model_cols: list[str]) -> dict:
    """One candidate's sNp measured with the library's definitions (``dataset._measure``): the index columns at ``f_hz`` by
    the index's rule (a curve as ``dataset._anchor_value`` takes it, ``Qmin`` the smaller Q, a scalar under the stratum's
    band, ``area`` the footprint of the GDS beside the sNp), every model column (``Lp@20``, ``SRF``), and the system SRF."""
    m = dataset._measure(work, device, rule)

    def curve(c: str, hz: float) -> float | None:
        return dataset._anchor_value(m.q, c, rule.quantities[c], hz, m.start, m.stop) if c in rule.quantities else None

    def column(c: str) -> float | None:
        if c in manifest.CURVES:
            return curve(c, f_hz)
        if c == "Qmin":
            qp, qs = curve("Qp", f_hz), curve("Qs", f_hz)
            return None if qp is None or qs is None else min(qp, qs)
        if c == "area":
            try:
                box = fp.footprint(work / f"{device.id}.gds", profile=device.profile, generator=device.generator, plugin=device.plugin)
            except (fp.FootprintError, OSError, RuntimeError):
                return None
            return None if box is None else box["area_um2"]
        return _scalar(m, c, rule)

    models = {}
    for c in model_cols:
        name, _, ghz = c.partition("@")
        models[c] = curve(name, float(ghz) * 1e9) if ghz else _scalar(m, name, rule)
    srf = m.q.scalars.get("SRF")
    return {"index": {c: column(c) for c in index_columns}, "models": models,
            "curves": {c: curve(c, f_hz) for c in dataset.curves(rule)},
            "srf_hz": None if srf is None or not math.isfinite(srf) else float(srf)}


def ratio(value: float | None, base: float | None) -> float | None:
    if value is None or base is None or not (math.isfinite(value) and math.isfinite(base)) or base == 0:
        return None
    return value / base


# -- one device's pass -------------------------------------------------------------------------------------------------

@dataclass
class Pass:
    """Everything one library device's pass found before EMX, and what came after."""

    device: Device
    lib: query.Library
    linked: link.Linked
    stratum: str
    part: str
    row: dict
    spec: Spec                                        # the part's spec, this run's EMX machine facts, PROJECT's budget
    grid: dict
    columns: dict[str, list[str]]                     # index column -> model columns (variables', prefer's)
    variables: dict[str, str]                         # index column -> spec variable
    ranges: dict[str, tuple[float, float]]            # index column -> the variable's range, SI units
    margin_hz: float
    has_srf: bool
    prefer: tuple[str, str]
    neighbours: list[Neighbour]
    ranked: list[Neighbour]
    chosen: list[Neighbour]
    peaks: list[float]
    part_rows: int
    rows_before: int
    visited: set
    results: dict = field(default_factory=dict)       # point key -> observation
    report: dict = field(default_factory=dict)


def _check_int(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RefineError(f"{name} must be a whole number of at least 1, got {value!r}")
    return value


def _libraries(run: Run, devices: list[Device], cache_dir) -> dict[Path, query.Library]:
    out: dict[Path, query.Library] = {}
    for d in devices:
        root = Path(d.library.root).expanduser().resolve()
        if root not in out:
            out[root] = query.Library(root, limits=run.site.host("local"), cache_dir=cache_dir)   # computes on the controller
    return out


def plan_device(run: Run, device: Device, lib: query.Library, best: Observation, rows: list[Observation], *, steps: int,
                n: int, prefer: str | None, threads, memory_gb, process_file, taken: set | None = None) -> Pass:
    """One device's pass up to EMX: its row, the local grid, the predictions and the window, the ranking and the preflight
    (module docstring), with the notes the plan prints. ``taken``: (part, point key) of the candidates an earlier device's
    pass chose (two devices on one table): left out here, so that no geometry is simulated and adopted twice."""
    linked = link.resolve(run.spec)[device.id]
    row = row_of(best, device.id)
    stratum, part = device.library.stratum, row["part"]
    rule = lib.manifest.strata[stratum]
    parts = [p.store for p in rule.parts]
    if part not in parts:
        raise RefineError(f"device {device.id}: the best point's row {part}/{row['obs_id']} names part {part!r}, which stratum "
                          f"{stratum} does not list now (its parts: {parts})")
    ds = lib.dataset(stratum)
    geometry = {d: float(row["geometry"][d]) for d in ds.dims}
    f_hz = linked.index.frequency_hz
    ghz = f_hz / 1e9
    run.note(f"lib_refine: {device.id}: its row {part}/{row['obs_id']} of {stratum}: "
             + " ".join(f"{d}={_text(v)}" for d, v in geometry.items())
             + f"; its values (the index at {ghz:g} GHz): " + ", ".join(f"{c} {v:.4g}" for c, v in row["values"].items() if v is not None))
    grid = local_grid(ds, rule, geometry, steps)
    run.note(f"lib_refine: {device.id}: the local grid: {grid['size']} geometries within {steps} step(s) of the row over "
             + ", ".join(f"{d} ({s:g})" for d, s in grid["varied"].items()) + " (library.yaml steps)"
             + (f", {', '.join(grid['held'])} held at the row's value" if grid["held"] else "")
             + f"; {grid['in_table']} of them rows of the table already, {len(grid['neighbours'])} neighbours")
    sense, column = index._prefer(linked.index, prefer if prefer is not None else device.library.prefer)
    variables = dict(zip(linked.coordinates, linked.names, strict=True))
    axes = dict(zip(linked.coordinates, linked.table.axes, strict=True))
    ranges = {c: (axis.lower, axis.upper) for c, axis in axes.items()}
    wanted = list(dict.fromkeys([*linked.coordinates, column]))
    columns = model_columns(lib, stratum, wanted, ghz)
    has_srf = "SRF" in rule.quantities
    asked = list(dict.fromkeys([q for c in wanted for q in columns[c]] + (["SRF"] if has_srf else [])))
    margin_hz = linked.index.srf_margin * f_hz
    missing = [q for q in asked if lib._model_file(stratum, q) is None]
    if missing:
        run.note(f"lib_refine: {device.id}: fitting {len(missing)} model(s) of {stratum} ({', '.join(missing)}) in parallel "
                 "(first use; minutes per column on a large stratum)")
    lib.models(stratum, asked)
    for nb in grid["neighbours"]:
        nb.answers = query.query(lib, stratum, nb.params, asked, k=K)["quantities"]
        nb.predicted = {c: _answer(c, columns[c], nb.answers) for c in wanted if c != "area"}
        nb.srf = nb.answers["SRF"] if has_srf else None
        nb.verdict, nb.detail = verdict({c: nb.predicted[c] for c in linked.coordinates if c != "area"}, nb.srf, axes, margin_hz)
    if "area" in wanted:                     # drawn (no EMX) for the neighbours the models leave in the window, then judged
        for nb in grid["neighbours"]:
            if nb.verdict is None:
                nb.predicted["area"] = _drawn(lib, stratum, nb.params)
                if "area" in variables:
                    nb.verdict, nb.detail = verdict({c: nb.predicted[c] for c in linked.coordinates}, nb.srf, axes, margin_hz)
    dropped = {r: sum(nb.verdict == r for nb in grid["neighbours"]) for r in RULES}
    kept = [nb for nb in grid["neighbours"] if nb.verdict is None]
    outside = {}
    for nb in grid["neighbours"]:
        if nb.verdict == "outside_range":
            name = variables[nb.detail.split(" ", 1)[0]]
            outside[name] = outside.get(name, 0) + 1
    srf_text = (f"SRF above {linked.index.srf_margin:g} x {ghz:g} GHz" if has_srf else
                f"no SRF rule: {stratum} declares no SRF to predict")
    run.note(f"lib_refine: {device.id}: predicted at {ghz:g} GHz ({', '.join(f'{c} -> {v}' for c, v in variables.items())}; "
             f"{srf_text}): of {len(grid['neighbours'])} neighbours, out_of_domain {dropped['out_of_domain']}, uncertain "
             f"{dropped['uncertain']}, outside_range {dropped['outside_range']}"
             + (f" ({', '.join(f'{v} {k}' for v, k in outside.items())})" if outside else "")
             + f", srf_margin {dropped['srf_margin']}; {len(kept)} kept")

    def rank(nb: Neighbour) -> tuple:
        a = nb.predicted.get(column)
        v = a.get("value") if a is not None and a["status"] in ("predicted", "drawn") else None
        missing_value = v is None or not math.isfinite(v)
        return (missing_value, 0.0 if missing_value else (-v if sense == "max" else v), nb.steps,
                tuple(nb.params[d] for d in ds.dims))

    ranked = sorted(kept, key=rank)
    source = dataset._spec(lib.root / part)
    if source.em is None:
        raise RefineError(f"part {part}: its spec has no em section, the EMX settings its candidates are simulated with")
    spec = source.model_copy(update={"project": f"{run.spec.project}_refine_{re.sub(r'[^A-Za-z0-9_]', '_', part)}",
                                     "em": _signoff_em(source.em, threads, memory_gb, process_file),
                                     "budget": run.spec.budget})            # the run's budget counts the candidates
    part_row = _observations(lib.root / part, {row["obs_id"]}).get(row["obs_id"])
    if part_row is None:
        raise RefineError(f"device {device.id}: part {part} holds no observation {row['obs_id']} (the best point's row)")
    origin = f"refine:{run.store.project_dir.name}:{best.obs_id}"
    for nb in ranked:
        nb.point = Point({**part_row.params, **{d: _text(nb.params[d]) for d in ds.dims}}, origin)
    shared = [nb for nb in ranked if (part, nb.point.key) in (taken or set())]
    if shared:
        run.note(f"lib_refine: {device.id}: {len(shared)} kept neighbour(s) are candidates of another device's pass already "
                 "(the same table): left out here")
        ranked = [nb for nb in ranked if nb not in shared]
    for i, nb in enumerate(ranked, 1):
        nb.label = f"c{i}"
        nb.combination = combination(linked, {c: nb.predicted[c].get("value") for c in linked.coordinates if c in nb.predicted})
    chosen = _preflight_ranked(run, part, spec, ranked, n)
    refused = [nb for nb in ranked if nb.preflight not in (None, "clean")]
    seen = visited(run.spec, linked, rows)
    run.note(f"lib_refine: {device.id}: ranked by {sense}:{column} on the prediction; preflight (generator and DRC gate, no "
             f"EMX) of {sum(nb.preflight is not None for nb in ranked)}: {len(chosen)} clean, "
             f"{sum(nb.preflight == 'generator' for nb in refused)} refused by the generator, "
             f"{sum(nb.preflight == 'drc' for nb in refused)} by the DRC gate"
             + (f"; {len(chosen)} of the n={n} asked" if len(chosen) < n else ""))
    for nb in refused:
        who = "the generator" if nb.preflight == "generator" else "the DRC gate"
        run.note(f"lib_refine: {device.id}: {nb.label} refused by {who}: {'; '.join(nb.reasons)}")
    for nb in chosen:
        values = ", ".join(f"{c} {a['value']:.4g}" for c, a in nb.predicted.items() if a.get("value") is not None)
        cell = nb.combination
        where = ("beyond the grid" if None in cell else
                 ("a combination the table holds" if cell in linked.table.cells else "a combination the table does not hold")
                 + (", evaluated by the run" if cell in seen else ""))
        changed = " ".join(f"{d}={_text(nb.params[d])}" for d, k in nb.offset.items() if k)
        run.note(f"lib_refine: {device.id}: {nb.label} {changed} -- predicted {values}"
                 + (f", SRF {nb.srf['value']:.4g} Hz" if nb.srf and nb.srf.get("value") is not None else "")
                 + f"; on {where}: " + " ".join(f"{k}={v}" for k, v in combination_text(run.spec, linked, cell).items()))
    workers = engine.workers_for(spec, em_only_pipeline(spec), run.jobs, run.limits)
    part_rows = [r for r in ds.rows if r.part == part]
    peaks = [p for r in part_rows if (p := _peak_gb((lib.root / r.snp).parent / "emx.log")) is not None]
    run.note(f"lib_refine: {device.id}: EMX with part {part}'s spec: {spec.em.threads} thread(s) and a {spec.em.memory_gb:g} GB "
             f"memory cap per job, up to {workers} jobs at once; the part's rows' own EMX peak memory (from their emx.log, "
             f"{len(peaks)} of {len(part_rows)}): {_median_max(peaks)}")
    return Pass(device, lib, linked, stratum, part, row, spec, grid, {c: columns[c] for c in wanted}, variables, ranges,
                margin_hz, has_srf, (sense, column), grid["neighbours"], ranked, chosen, peaks, len(part_rows), len(ds.rows), seen)


def _preflight_ranked(run: Run, part: str, spec: Spec, ranked: list[Neighbour], n: int) -> list[Neighbour]:
    """``lib_tap.preflight`` in rank order until ``n`` are clean or the list runs out: each round preflights as many as are
    still missing."""
    chosen: list[Neighbour] = []
    done = 0
    while len(chosen) < n and done < len(ranked):
        batch = ranked[done:done + n - len(chosen)]
        done += len(batch)
        twins = Twins(part=part, spec=spec, rows=[Selected(part, nb.label, "") for nb in batch], points=[nb.point for nb in batch],
                      fixture_metal=None, metal_rule=None, taps={}, peaks_gb=[])
        for nb, outcome in zip(batch, preflight([twins], run.site.host("local").max_threads), strict=True):
            nb.preflight, nb.reasons = outcome.status, outcome.reasons
            if outcome.status == "clean":
                chosen.append(nb)
    return chosen


# -- the check, the adoption, the report ---------------------------------------------------------------------------------

def _compact(answer: dict | None) -> dict | None:
    """An answer as the report keeps it: no evidence rows."""
    if answer is None:
        return None
    return {k: v for k, v in answer.items() if k in ("status", "value", "lo", "hi", "rel_sigma", "lower_bound", "reason", "from")}


def check_device(run: Run, p: Pass) -> list[Observation]:
    """Every simulated candidate's entry: status, and for an ok one its measured values, the comparison with the predictions
    (z, inside) and the ratios to the row. Returns the ok candidates the library can measure (the ones adopted)."""
    rule = p.lib.manifest.strata[p.stratum]
    device = p.spec.devices[0]
    f_hz = p.linked.index.frequency_hz
    model_cols = list(dict.fromkeys([q for cols in p.columns.values() for q in cols] + (["SRF"] if p.has_srf else [])))
    entries, adoptable = [], []
    for nb in p.chosen:
        o = p.results.get(nb.point.key)
        if o is None:
            continue
        entry = {"candidate": nb.label, "obs_id": o.obs_id, "status": o.status, "params": {d: nb.params[d] for d in nb.params}}
        if o.status != "ok":
            entry["issues"] = list(o.issues)[:5]
            entries.append(entry)
            continue
        try:
            got = measured(run.store.root / "sims" / o.obs_id / "em" / device.id, device, rule, f_hz, list(p.row["values"]), model_cols)
        except (measure_kernel.MeasureError, touchstone.TouchstoneError, dataset.DatasetError, OSError) as exc:
            entry["check"] = f"not measured: {type(exc).__name__}: {exc}"      # the dataset would leave such a row out
            entries.append(entry)
            continue
        quantities = {}
        for q in model_cols:                       # as lib_signoff compares them: every model column asked
            pred, y = nb.answers[q], got["models"].get(q)
            e = {"measured": y, "predicted": pred.get("value"), "lo": pred.get("lo"), "hi": pred.get("hi"), "status": pred["status"]}
            if pred["status"] == "predicted" and y is not None:
                e["z"] = _z(y, pred, K)
                e["inside"] = pred["lo"] <= y <= pred["hi"]
            quantities[q] = e
        values = {c: got["index"].get(c) for c in p.row["values"]}
        srf = got["srf_hz"]
        in_index = all(v is not None for v in got["curves"].values()) and (srf is None or srf > p.margin_hz)
        cell = combination(p.linked, values)
        entry.update(values=values, ratios={c: ratio(values[c], p.row["values"].get(c)) for c in values}, quantities=quantities,
                     in_index=in_index, combination=combination_text(run.spec, p.linked, cell),
                     held=None not in cell and cell in p.linked.table.cells, visited=cell in p.visited)
        entries.append(entry)
        adoptable.append(o)
    p.report["points"] = entries
    return adoptable


def _summary_line(p: Pass) -> str:
    points = p.report.get("points", [])
    ok = [e for e in points if e["status"] == "ok"]
    adopted = [e for e in ok if e.get("adopted")]
    sense, column = p.prefer
    own = p.row["values"].get(column)
    values = [e["values"].get(column) for e in adopted if e.get("values", {}).get(column) is not None]
    best = (max(values) if sense == "max" else min(values)) if values else None
    text = f"{len(p.chosen)} candidates, {len(ok)} ok, {len(adopted)} adopted"
    if own is not None:
        text += f"; {sense}:{column} the row {own:.4g}, the best adopted " + (f"{best:.4g}" if best is not None else "-")
    if adopted:
        new = sum(1 for e in adopted if e.get("in_index") and not e.get("held") and None not in e["combination"].values())
        held = sum(1 for e in adopted if e.get("held"))
        seen = sum(1 for e in adopted if e.get("held") and e.get("visited"))
        text += (f"; in the device's next index on combinations the table did not hold: {new}, on held ones: {held}"
                 + (f" ({seen} evaluated by the run already, not proposed again)" if seen else ""))
    return text


def _continuation(run: Run, rows: list[Observation], best: Observation, adopted: int) -> str:
    """The command that continues the run with its own recipe and a larger budget: the recipe from the step of the best
    point (``optimize``, ``coarse`` / ``fine``, ``search@<corner>`` / ``signoff``), the budget the points of its search step so
    far plus one per adopted row."""
    count = {}
    for o in rows:
        count[o.step] = count.get(o.step, 0) + 1
    more = max(adopted, 1)
    project = run.store.project_dir
    if best.step in ("coarse", "fine"):
        command = (f"ic-opt run coarse_to_fine {project} coarse_budget={count.get('coarse', 0)} "
                   f"fine_budget={count.get('fine', 0) + more}")
    elif best.step.startswith("search@") or RECHECK.match(best.step):
        corner = best.step.split("@", 1)[1].split("#", 1)[0] if best.step.startswith("search@") else next(
            (s.split("@", 1)[1] for s in sorted(count) if s.startswith("search@") and "#" not in s), "tt")
        command = f"ic-opt run signoff {project} corner={corner} budget={count.get(f'search@{corner}', 0) + more}"
    else:
        command = f"ic-opt run optimize {project} budget={count.get('optimize', 0) + more}"
    return command


def main(run: Run, *, device: str | None = None, steps: int = 1, n: int = 8, prefer: str | None = None, threads: int | None = None,
         memory_gb: float | None = None, process_file: str | None = None, cache_dir: str | None = None) -> None:
    steps, n = _check_int(steps, "steps"), _check_int(n, "n")
    devices = run.spec.library_devices
    if not devices:
        raise RefineError(f"{run.spec.project}: its spec has no library device (devices[*].library): lib_refine grows the "
                          "library around the best point of a run whose devices come from library tables")
    if device is not None:
        found = [d for d in devices if d.id == str(device)]
        if not found:
            raise RefineError(f"device={device}: no library device of that id; the spec's library devices: "
                              f"{[d.id for d in devices]}")
        devices = found
    best, checked, rows = best_point(run.spec, run.store.observations())
    objective = "-" if best.fom is None else f"{best.fom:.6g}"
    run.note(f"lib_refine: the best point {best.obs_id} of the {len(rows)} points of this problem (step {best.step}"
             + (", re-checked at every corner" if checked else "") + f", objective {objective})")
    libraries = _libraries(run, devices, cache_dir)
    for lib in libraries.values():
        for note in lib.notes:
            run.note(f"lib_refine: {note}")
    passes: list[Pass] = []
    taken: set[tuple[str, str]] = set()
    for d in devices:
        p = plan_device(run, d, libraries[Path(d.library.root).expanduser().resolve()], best, rows, steps=steps, n=n,
                        prefer=prefer, threads=threads, memory_gb=memory_gb, process_file=process_file, taken=taken)
        taken |= {(p.part, nb.point.key) for nb in p.chosen}
        passes.append(p)
    observations = run.store.observations()
    used = sum(engine.simulations(o) for o in observations)
    reusable = {(o.key, o.spec_fingerprint) for o in observations if o.status == "ok"}
    needed = sum(_per_point(p.spec) * sum((nb.point.key, p.spec.fingerprint()) not in reusable for nb in p.chosen) for p in passes)
    budget = run.spec.budget.max_simulations
    short = used + needed > budget
    run.note(f"lib_refine: budget: {used} of {budget} simulations used in {run.store.project_dir.name}; this run needs up to "
             f"{needed} more (a candidate already in its store is reused)"
             + ("; NOT ENOUGH: raise budget.max_simulations in its spec.yaml" if short else ""))
    if short and not run.plan:
        raise RefineError(f"budget: {used} of {budget} simulations used in {run.store.project_dir}, and this run needs up to "
                          f"{needed} more; raise budget.max_simulations in its spec.yaml")
    for p in passes:
        if not p.chosen:
            run.note(f"lib_refine: {p.device.id}: no candidate to simulate")
            continue
        obs = b.evaluate(p.spec, [nb.point for nb in p.chosen], run.executor, run.store, pipeline=em_only_pipeline(p.spec),
                         step=f"lib_refine:{p.part}", cshrc=run.cshrc, parallel_jobs=run.jobs, limits=run.limits)
        p.results = {o.key: o for o in obs}
    if run.plan:
        return
    out = run.store.root / "reports" / REPORT
    report = {"call": {"device": device, "steps": steps, "n": n, "prefer": prefer, "threads": threads, "memory_gb": memory_gb,
                       "process_file": process_file, "cache_dir": cache_dir},
              "best": {"obs_id": best.obs_id, "step": best.step, "rechecked": checked, "objective": best.fom,
                       "params": dict(best.params), "points": len(rows)},
              "devices": {}}
    adoptable = {}
    for p in passes:
        adoptable[p.device.id] = check_device(run, p)
        report["devices"][p.device.id] = _device_report(p)
    out.write_text(json.dumps(_strict(report), indent=1, allow_nan=False) + "\n", encoding="utf-8")    # before adopting: kept if it fails
    total, origin = 0, f"refine:{run.store.project_dir.name}:{best.obs_id}"
    for p in passes:
        ok = adoptable[p.device.id]
        ids = _adopt(p.lib.root / p.part, run.store, ok, origin=lambda o: origin) if ok else []
        total += len(ids)
        for e, new in zip([e for e in p.report["points"] if e["status"] == "ok" and "values" in e], ids, strict=True):
            e["adopted"] = new
        grown = query.Library(p.lib.root, limits=p.lib.limits, cache_dir=cache_dir).dataset(p.stratum)
        p.report["dataset"] = {"rows_before": p.rows_before, "rows": len(grown.rows), "excluded": grown.excluded}
        p.report["adopted"] = {"part": p.part, "ids": ids, "origin": origin}
        report["devices"][p.device.id] = _device_report(p)
        run.note(f"lib_refine: {p.device.id}: {_summary_line(p)}; {p.stratum}'s dataset {p.rows_before} -> {len(grown.rows)} rows"
                 + (f", excluded {grown.excluded}" if grown.excluded else ""))
    out.write_text(json.dumps(_strict(report), indent=1, allow_nan=False) + "\n", encoding="utf-8")
    run.note(f"lib_refine: report {out}")
    if total:
        run.note(f"lib_refine: next: the grown table is a new generation for the next process (its index's content is part of "
                 f"the pipeline fingerprint); continue the run with its own recipe and a larger budget -- "
                 f"{_continuation(run, rows, best, total)} with the parameters it ran with (strategy, batch, seed; --plan "
                 f"first) -- the {total} adopted row(s) are candidates of its next batch and its {len(rows)} observations stay "
                 f"(the same spec fingerprint); its spec's budget: {sum(engine.simulations(o) for o in run.store.observations())} of "
                 f"{budget} simulations used")
    else:
        run.note("lib_refine: next: nothing adopted, the table did not grow; the run continues as it was")


def _device_report(p: Pass) -> dict:
    """One device's part of the report."""
    sense, column = p.prefer
    candidates = []
    for nb in p.ranked:
        if nb.preflight is None:
            continue
        candidates.append({"candidate": nb.label, "params": dict(nb.params), "offset": {d: k for d, k in nb.offset.items() if k},
                           "predicted": {c: _compact(a) for c, a in nb.predicted.items()}, "srf": _compact(nb.srf),
                           "preflight": nb.preflight, "reasons": list(nb.reasons)})
    return {"stratum": p.stratum, "part": p.part, "frequency_hz": p.linked.index.frequency_hz,
            "row": {"obs_id": p.row["obs_id"], "geometry": p.row["geometry"], "values": p.row["values"]},
            "grid": {"varied": p.grid["varied"], "held": p.grid["held"], "size": p.grid["size"], "in_table": p.grid["in_table"],
                     "neighbours": len(p.neighbours), "dropped": {r: sum(nb.verdict == r for nb in p.neighbours) for r in RULES},
                     "kept": len(p.ranked)},
            "window": {"variables": p.variables, "ranges": p.ranges, "srf_margin": p.linked.index.srf_margin,
                       "srf_rule": p.has_srf},
            "prefer": f"{sense}:{column}", "candidates": candidates,
            "peak_memory_gb": {"rows": p.part_rows, "recorded": len(p.peaks),
                               "median": statistics.median(p.peaks) if p.peaks else None,
                               "max": max(p.peaks) if p.peaks else None},
            **{k: v for k, v in p.report.items()}}
