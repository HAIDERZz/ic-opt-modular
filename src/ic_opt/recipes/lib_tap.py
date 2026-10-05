"""Built-in recipe: lib_tap -- tapped twins of a table's rows: each row's own geometry with center taps, through real EMX,
compared with its row and adopted into the library as a new table.

``ic-opt run lib_tap PROJECT library=<root> stratum=<untapped stratum> taps=<json> (window=<json> | rows=<file>)
[adopt=<new stratum>] [threads=N] [memory_gb=G] [process_file=/abs/path.proc] [cache_dir=DIR] --plan``

Why: center taps move L and k by a few per cent at most, so geometries are found on the untapped tables; but taps cost Q
(a same-metal tap about 3 %, a via-stack tap about 10 %), so a circuit that binds a tapped device needs that device's own
S-parameters. lib_tap builds them for the rows of one window of an untapped table, row for row. The stratum's generator
must be ``clean_port_xfm_bs``.

``taps`` (JSON): ``{"primary": "same", "secondary": "same", "primary_width_um": 3, "secondary_width_um": 3, "measure":
"grounded"}``. ``primary`` / ``secondary``: ``"same"`` (a same-metal tap, on the winding's own metal), a metal below the
winding (a via-stack tap) or null (no tap on that winding); at least one tap. The widths: optional, same-metal taps only.
``measure`` (required): ``"grounded"``, both taps AC-grounded as a mixer uses them (the topology's ``grounded`` lists the
tap ports). ``"floating"`` (taps open) is refused for now: a device's topology holds every port in a drive or at 0 V.

The rows: ``window`` (JSON), ``{"frequency_ghz": f, "srf_margin": m, "ranges": {"<index column>": [lower, upper], ...}}``
-- every row of the stratum's index at f (``lib.index``; ``srf_margin`` optional) whose values lie inside every range,
inclusive, in the column's unit -- or ``rows``, a JSON file: a ``lib.index out=`` table (each cell's best row), a
``lib.pick`` answer, or a list of ``{"part": ..., "obs_id": ...}``.

A twin is its row's geometry (the row's own parameters) built with the spec of the row's part -- generator, plugin,
profile, fixed fields, EMX physics -- and three changes. The taps: the tap metals (``"same"`` is the winding's metal),
the widths, the tap ports added (``CTP``, ``CTS``) and grounded. The ground fixture stays where the row's was: on the
conductor the row's build recorded (``geometry_manifest.json`` beside its GDS; the selected rows of a part must agree),
under ``metal_rule: free`` -- ``shared`` when a via-stack tap's stack passes through that metal. And this run's
``threads`` / ``memory_gb`` / ``process_file``, which are not physics: the twins are the library's generation. Nothing
else changes, the device included: a tap port sits between the other winding's two ports, so with a small opening its
stub comes close to theirs on the fixture's metal; that spacing is the fixture's own and the DRC gate exempts it on a
metal chosen under ``free``, while stubs that would touch are refused (``drc_audit.fixture_exemptions``,
``fixture.add_ground_fixture``). Each twin's ``origin`` is ``tap:<part>:<obs id>``, the row it is the twin of.

Preflight, always and all that ``--plan`` does: every twin is drawn by the generator and passed through the pcell stage's
DRC gate (``stages.em_chain.build_device``), on the controller within its ``hosts.local`` entry, no EMX. A point the
generator or the gate refuses is listed with the reason and not simulated. The plan prints the rows per part, the
outcomes, the envelope (jobs, threads, memory cap) and the rows' own EMX peak memory from their ``emx.log``, so
``memory_gb`` can be set from it. Real EMX: run with ``--plan`` first; it is the approval point.

Run and check: the clean points go through ``em_only_pipeline`` into PROJECT's store, one step per part
(``lib_tap:<part>``); PROJECT's spec gives the run's resources (``simulator.parallel_jobs``, ``budget``). Every ok twin is
measured with the library's definitions (the stratum's bands, anchors and low-frequency limit) and compared with its row:
twin / row for ``Lp_lf``, ``Ls_lf``, ``k_lf``, ``Qp_peak``, ``Qs_peak``, ``SRF`` and, with a window, its columns at its
frequency. ``.icopt/reports/lib_tap.json`` holds the call, the rows, the preflight outcomes, every twin's values and
ratios, and the summary: per quantity the median and the 10th / 90th percentiles of the ratio, and the twins whose L or
k moved by more than 5 %.

``adopt=<new stratum>``: after the run, each source part's ok twins (failed ones are not adopted) go into a new part
store ``<library>/<new stratum>__<part>``, created fresh, with the twin spec (``.icopt/spec.json``) and their sims
directories under fresh obs ids, the origin kept; the new stratum takes the source stratum's definition (generator,
dims, steps, quantities: the same bands, anchors, models, feature maps) with the new parts and a ``note`` saying what it
is. ``library.yaml`` is backed up (``library.yaml.bak_<UTC time>``) and the entry appended at its end when ``strata`` is
its last top-level key and the file parses afterwards as the old manifest plus exactly the new stratum; otherwise the
file is left alone and the entry is written to ``<library>/<new stratum>.stratum.yaml``. Refused before anything runs:
a stratum or part store that exists. ``cache_dir`` is where the library's cache files go (as for ``lib_signoff``).
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import statistics
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import yaml

from ic_opt import blocks as b
from ic_opt.em import measure as measure_kernel
from ic_opt.em import touchstone
from ic_opt.em.pcell import footprint as fp
from ic_opt.em.pcell import get_generator
from ic_opt.em.pcell import stack as metal_stack
from ic_opt.em.pcell.process_rules import get_process_rule_profile
from ic_opt.eval import engine
from ic_opt.eval.stage import StageFailure
from ic_opt.library import dataset, index, manifest, query
from ic_opt.observation import Observation
from ic_opt.recipe import Run
from ic_opt.recipes.lib_signoff import _adopt, _signoff_em
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.stages.em_chain import DrcRefused, build_device, em_only_pipeline

SUPPORTED = "clean_port_xfm_bs"                     # the one generator whose tapped twins this version builds
TAP_KEYS = ("primary", "secondary", "primary_width_um", "secondary_width_um", "measure")
MEASURES = ("grounded", "floating")
WINDINGS = (("primary", "CTP"), ("secondary", "CTS"))  # the generator's tap port order
WINDOW_KEYS = ("frequency_ghz", "srf_margin", "ranges")
QUANTITIES = ("Lp_lf", "Ls_lf", "k_lf", "Qp_peak", "Qs_peak", "SRF")   # compared twin / row for every twin
MOVED_QUANTITIES = ("Lp_lf", "Ls_lf", "k_lf")       # a twin is flagged when one of these moved by more than MOVED
MOVED = 0.05
STRATUM_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PEAK = re.compile(r"^Peak memory usage ([0-9.]+) ([KMG]B)\s*$", re.MULTILINE)       # EMX's last line but one
_GB = {"KB": 1 / 1024**2, "MB": 1 / 1024, "GB": 1.0}


class TapError(ValueError):
    """A lib_tap call that cannot be carried out as given: refused before anything is built or simulated."""


# -- the call ---------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Taps:
    primary: str | None                              # "same", a metal spelling, or None (no tap on that winding)
    secondary: str | None
    primary_width_um: float | None
    secondary_width_um: float | None
    measure: str

    def side(self, winding: str) -> str | None:
        return getattr(self, winding)

    def width(self, winding: str) -> float | None:
        return getattr(self, f"{winding}_width_um")


def _json(value, what: str):
    """A JSON parameter as given: the command line hands it parsed; a string (a Python caller) is parsed here."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise TapError(f"{what}: not JSON ({exc}); got {value!r}") from None
    return value


def parse_taps(value) -> Taps:
    """``taps`` (JSON) as a ``Taps``; every key checked, ``measure`` required."""
    data = _json(value, "taps")
    shape = '{"primary": "same", "secondary": "same", "primary_width_um": 3, "secondary_width_um": 3, "measure": "grounded"}'
    if not isinstance(data, dict):
        raise TapError(f"taps: expected a JSON object such as {shape}; got {data!r}")
    unknown = sorted(set(data) - set(TAP_KEYS))
    if unknown:
        raise TapError(f"taps: unknown key(s) {unknown}; the keys are {list(TAP_KEYS)}")
    measure = data.get("measure")
    if measure is None:
        raise TapError("taps.measure is required: \"grounded\" (both taps AC-grounded, as a mixer uses them) -- it decides "
                       "what the table measures")
    if measure == "floating":
        raise TapError("taps.measure \"floating\" (taps open) cannot be measured yet: a device's topology holds every port "
                       "in a drive or at 0 V (the measure kernel, the spec's topology check), so a twin with open tap ports "
                       "has no topology; use \"grounded\"")
    if measure not in MEASURES:
        raise TapError(f"taps.measure {measure!r} is none of {list(MEASURES)}")
    sides: dict[str, str | None] = {}
    for winding, _port in WINDINGS:
        metal = data.get(winding)
        text = None if metal is None or isinstance(metal, bool) or not isinstance(metal, str | int) else str(metal).strip()
        if metal is not None and not text:
            raise TapError(f"taps.{winding}: \"same\", a metal below the {winding} winding (a via-stack tap) or null; got {metal!r}")
        sides[winding] = text
    if not any(sides.values()):
        raise TapError("taps: no tap -- give primary and/or secondary (\"same\", or a metal below the winding)")
    widths: dict[str, float | None] = {}
    for winding, _port in WINDINGS:
        width = data.get(f"{winding}_width_um")
        if width is not None:
            if isinstance(width, bool) or not isinstance(width, int | float) or not (math.isfinite(width) and width > 0):
                raise TapError(f"taps.{winding}_width_um must be a positive number of um, got {width!r}")
            if sides[winding] is None:
                raise TapError(f"taps.{winding}_width_um {width:g} needs taps.{winding}: it is the width of the {winding}'s "
                               f"tap lead, and the call gives the {winding} no tap")
        widths[winding] = None if width is None else float(width)
    return Taps(sides["primary"], sides["secondary"], widths["primary"], widths["secondary"], measure)


@dataclass(frozen=True)
class Window:
    frequency_ghz: float
    srf_margin: float | None
    ranges: dict[str, tuple[float, float]]

    def text(self) -> str:
        """The window as the plan, the report and the stratum's note say it."""
        margin = "" if self.srf_margin is None else f", srf_margin {self.srf_margin:g}"
        ranges = ", ".join(f"{c} {lo:g}..{hi:g}" for c, (lo, hi) in self.ranges.items())
        return f"window {self.frequency_ghz:g} GHz{margin}: {ranges}"


def _finite(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise TapError(f"{what} must be a finite number, got {value!r}")
    return float(value)


def parse_window(value) -> Window:
    """``window`` (JSON) as a ``Window``; the ranges are checked against the index's columns when it is built."""
    data = _json(value, "window")
    shape = '{"frequency_ghz": 112, "srf_margin": 1.5, "ranges": {"Lp": [7e-11, 1.6e-10], "k": [0.35, 0.75]}}'
    if not isinstance(data, dict):
        raise TapError(f"window: expected a JSON object such as {shape}; got {data!r}")
    unknown = sorted(set(data) - set(WINDOW_KEYS))
    if unknown:
        raise TapError(f"window: unknown key(s) {unknown}; the keys are {list(WINDOW_KEYS)}")
    if "frequency_ghz" not in data:
        raise TapError(f"window.frequency_ghz is required: the working frequency the index is taken at ({shape})")
    frequency = _finite(data["frequency_ghz"], "window.frequency_ghz")
    if frequency <= 0:
        raise TapError(f"window.frequency_ghz must be positive, got {frequency:g}")
    margin = data.get("srf_margin")
    if margin is not None:
        margin = _finite(margin, "window.srf_margin")
        if margin <= 0:
            raise TapError(f"window.srf_margin must be positive, got {margin:g}")
    ranges = data.get("ranges")
    if not isinstance(ranges, dict) or not ranges:
        raise TapError(f"window.ranges: expected at least one range {{\"<index column>\": [lower, upper], ...}}; got {ranges!r}")
    out = {}
    for column, bounds in ranges.items():
        if not isinstance(bounds, list | tuple) or len(bounds) != 2:
            raise TapError(f"window.ranges.{column}: expected [lower, upper] in the column's unit, got {bounds!r}")
        lo, hi = (_finite(v, f"window.ranges.{column}") for v in bounds)
        if lo > hi:
            raise TapError(f"window.ranges.{column}: lower {lo:g} is above upper {hi:g}")
        out[str(column)] = (lo, hi)
    return Window(frequency, margin, out)


# -- which rows -------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Selected:
    """A selected row of the source stratum: its part, its obs id and its sNp (relative to the library root)."""

    part: str
    obs_id: str
    snp: str


def select_window(lib: query.Library, stratum: str, window: Window) -> list[Selected]:
    """Every row of the stratum's index at the window's frequency (``index.build``) whose values lie inside every range,
    inclusive; a row without a value for a range's column is outside it. A range over anything but an index column is
    refused, naming the columns. The rows come part by part (the manifest's order), each part's by obs id."""
    built = index.build(lib, stratum, window.frequency_ghz * 1e9, srf_margin=window.srf_margin)
    stray = [c for c in window.ranges if c not in built.columns]
    if stray:
        raise TapError(f"window.ranges: {stray} {'is' if len(stray) == 1 else 'are'} not a column of {stratum}'s index; its "
                       f"columns are {built.columns}")
    inside = [r for r in built.rows
              if all(r.values.get(c) is not None and lo <= r.values[c] <= hi for c, (lo, hi) in window.ranges.items())]
    parts = [p.store for p in lib.manifest.strata[stratum].parts]
    inside.sort(key=lambda r: (parts.index(r.part), r.obs_id))      # a part store lists its rows as they finished
    return [Selected(r.part, r.obs_id, r.snp) for r in inside]


def select_rows(lib: query.Library, stratum: str, rows) -> tuple[list[Selected], str]:
    """The rows a file names -- a ``lib.index out=`` table (each cell's best row), a ``lib.pick`` answer or a list of
    ``{"part", "obs_id"}`` -- each checked to be a row of the stratum's dataset; repeats are taken once. Also returns what
    the rows came from, for the plan, the report and the note."""
    if isinstance(rows, str | os.PathLike):
        path = Path(rows)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TapError(f"rows: cannot read {path} as JSON ({exc})") from None
        source = f"file {path}"
    else:
        doc, source = rows, "the list given"
    if isinstance(doc, dict):
        if doc.get("stratum") not in (None, stratum):
            raise TapError(f"rows: {source} lists rows of stratum {doc['stratum']!r}, not {stratum!r}")
        if isinstance(doc.get("cells"), list):                       # lib.index out=
            items = [cell.get("best") if isinstance(cell, dict) else None for cell in doc["cells"]]
        elif isinstance(doc.get("rows"), list):                      # lib.pick
            items = doc["rows"]
        else:
            items = None
    else:
        items = doc if isinstance(doc, list) else None
    if items is None:
        raise TapError(f"rows: {source} is no lib.index out= table (cells), lib.pick answer (rows) or list of "
                       "{\"part\": ..., \"obs_id\": ...}")
    wanted: list[tuple[str, str]] = []
    for item in items:
        if not (isinstance(item, dict) and isinstance(item.get("part"), str) and isinstance(item.get("obs_id"), str)):
            raise TapError(f"rows: every entry of {source} names a part and an obs_id; got {item!r}")
        key = (item["part"], item["obs_id"])
        if key not in wanted:
            wanted.append(key)
    if not wanted:
        raise TapError(f"rows: {source} names no row")
    known = {(r.part, r.obs_id): r for r in lib.dataset(stratum).rows}
    foreign = [f"{part}/{obs}" for part, obs in wanted if (part, obs) not in known]
    if foreign:
        raise TapError(f"rows: {len(foreign)} of the {len(wanted)} rows of {source} are not rows of {stratum} (its parts: "
                       f"{[p.store for p in lib.manifest.strata[stratum].parts]}): {', '.join(foreign[:10])}"
                       + (" ..." if len(foreign) > 10 else ""))
    return [Selected(part, obs, known[(part, obs)].snp) for part, obs in wanted], source


# -- the twins --------------------------------------------------------------------------------------------------------

@dataclass
class Twins:
    """One source part's twins: the twin spec, and per selected row (in order) its twin point."""

    part: str
    spec: Spec
    rows: list[Selected]
    points: list[Point]
    fixture_metal: str | None                        # None: the fixture conductor, the bottom metal (the default fixture)
    metal_rule: str | None
    taps: dict[str, str]                             # winding -> its tap metal, by the profile's name (M6)
    peaks_gb: list[float]                            # the rows' own EMX peak memory, from their emx.log


def _observations(project: Path, wanted: set[str]) -> dict:
    """The observations ``wanted`` of a library part store, read from its observations.jsonl (no ``RunStore``: opening
    one would make directories in the library)."""
    path = project / ".icopt" / "observations.jsonl"
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            o = Observation.model_validate_json(line)
            if o.obs_id in wanted:
                out[o.obs_id] = o
    return out


def _position(stack: tuple[str, ...], metal, what: str) -> int:
    try:
        return metal_stack.position_in(stack, metal)
    except ValueError:
        raise TapError(f"{what} {metal!r} is not a metal of the profile's stack {list(stack)}") from None


def _recorded_metal(lib: query.Library, part: str, device, rows: list[Selected]) -> str | None:
    """The conductor the rows' builds recorded for their ground fixture (``footprint.recorded_fixture_metal`` on the GDS
    beside each sNp); the selected rows of a part must agree. Rows without a record (an older generation) had the fixture
    conductor, the bottom metal, unless the part's spec chose a metal: a named one is that metal, ``auto`` is refused --
    nothing says where it landed."""
    found: dict[str | None, list[str]] = {}
    for row in rows:
        gds = (lib.root / row.snp).parent / f"{device.id}.gds"
        found.setdefault(fp.recorded_fixture_metal(gds), []).append(row.obs_id)
    if len(found) > 1:
        what = "; ".join(f"{metal or 'no record'}: {len(ids)} ({', '.join(ids[:3])}{' ...' if len(ids) > 3 else ''})"
                         for metal, ids in found.items())
        raise TapError(f"part {part}: the selected rows disagree on the ground fixture's metal ({what}): a twin keeps its "
                       "row's fixture, and one part's twins share one spec; select the rows of one fixture metal")
    (metal,) = found
    if metal is not None:
        return metal
    chosen = (device.fixed.get("ground_fixture") or {}).get("metal")
    if chosen is None:
        return None
    if isinstance(chosen, str) and chosen.strip().lower() == "auto":
        raise TapError(f"part {part}: its spec draws the ground fixture on 'auto', and the selected rows keep no record of "
                       "the metal it landed on (no geometry_manifest.json beside their GDS): a twin cannot keep its row's "
                       "fixture")
    return str(chosen)


def _peak_gb(log: Path) -> float | None:
    """EMX's peak memory from its log (``Peak memory usage <x> GB``), in GB; None without a log or the line."""
    try:
        found = _PEAK.findall(log.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    if not found:
        return None
    value, unit = found[-1]
    return float(value) * _GB[unit]


def twins_of_part(run: Run, lib: query.Library, part: str, rows: list[Selected], taps: Taps, *, threads, memory_gb,
                  process_file) -> Twins:
    """The twin spec of one part (its spec with the taps, the row's fixture kept, this run's EMX machine facts) and the
    twin point of each selected row (the row's own parameters, origin ``tap:<part>:<obs id>``)."""
    source = dataset._spec(lib.root / part)
    (device,) = source.devices
    if source.em is None:
        raise TapError(f"part {part}: its spec has no em section, the EMX settings its twins are simulated with")
    if device.generator != SUPPORTED:
        raise TapError(f"part {part}: generator {device.generator!r}; lib_tap builds tapped twins of {SUPPORTED} only")
    present = [k for k in ("ct_primary_metal", "ct_secondary_metal") if device.fixed.get(k) is not None]
    if present or any(p.upper().startswith("CT") for p in device.ports):
        raise TapError(f"part {part} is tapped already ({', '.join(present) or 'ports ' + str(device.ports)}): lib_tap makes "
                       "tapped twins of an untapped table")
    stack = tuple(get_process_rule_profile(device.profile).metal_stack)
    fixture_metal = _recorded_metal(lib, part, device, rows)
    fixed = dict(device.fixed)
    resolved: dict[str, str] = {}
    stacks: list[tuple[int, int]] = []                # (tap position, winding position) of each via-stack tap
    for winding, _port in WINDINGS:
        want = taps.side(winding)
        if want is None:
            continue
        host = fixed.get(f"{winding}_metal")
        if host is None:
            raise TapError(f"part {part}: its spec fixes no {winding}_metal, the winding a tap goes on")
        h = _position(stack, host, f"part {part}'s {winding}_metal")
        metal = str(host) if want.lower() == "same" else want
        c = _position(stack, metal, f"taps.{winding}")
        if c > h:
            raise TapError(f"taps.{winding} {want!r} ({metal_stack.name_in(stack, c)}) sits above part {part}'s {winding} "
                           f"metal {metal_stack.name_in(stack, h)}: a tap runs on its winding's own metal (\"same\") or "
                           "below it (a via-stack tap)")
        if c < h:
            if taps.width(winding) is not None:
                raise TapError(f"taps.{winding}_width_um applies to a same-metal tap only; taps.{winding} {want!r} "
                               f"({metal_stack.name_in(stack, c)}) sits below part {part}'s {winding} metal "
                               f"{metal_stack.name_in(stack, h)}, a via-stack tap, which keeps the winding width")
            stacks.append((c, h))
        resolved[winding] = metal_stack.name_in(stack, c)
        fixed[f"ct_{winding}_metal"] = metal
        if taps.width(winding) is not None:
            fixed[f"ct_{winding}_width_um"] = taps.width(winding)
    fixture = dict(fixed.get("ground_fixture") or {})
    rule = None
    if fixture_metal is None:
        fixture.pop("metal", None)
        fixture.pop("metal_rule", None)
    else:
        f = _position(stack, fixture_metal, f"part {part}'s recorded fixture metal")
        rule = "shared" if any(c < f < h for c, h in stacks) else "free"
        fixture.update(metal=metal_stack.name_in(stack, f), metal_rule=rule)
    fixed["ground_fixture"] = fixture
    tap_ports = [port for winding, port in WINDINGS if winding in resolved]
    topology = device.topology                        # stated, or the default the spec's validation filled in
    data = source.model_dump(mode="json")
    data["project"] = f"{run.spec.project}_tap_{re.sub(r'[^A-Za-z0-9_]', '_', part)}"
    data["devices"][0].update(ports=[*device.ports, *tap_ports], fixed=fixed,
                              topology={**topology.model_dump(mode="json"),
                                        "grounded": [*topology.grounded, *(tap_ports if taps.measure == "grounded" else [])]})
    data["em"] = _signoff_em(source.em, threads, memory_gb, process_file).model_dump(mode="json")
    data["constraints"] = []                          # a twin is measured, not judged: its status is its measurement's
    data["budget"] = run.spec.budget.model_dump(mode="json")      # the run's resources, PROJECT's spec
    spec = Spec.model_validate(data)
    observations = _observations(lib.root / part, {row.obs_id for row in rows})
    points = [Point(dict(observations[row.obs_id].params), f"tap:{part}:{row.obs_id}") for row in rows]
    peaks = [p for row in rows if (p := _peak_gb((lib.root / row.snp).parent / "emx.log")) is not None]
    return Twins(part, spec, rows, points, None if fixture_metal is None else fixture["metal"], rule, resolved, peaks)


# -- preflight --------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Outcome:
    part: str
    row: str                                          # the row's obs id
    status: str                                       # "clean" | "generator" | "drc"
    reasons: tuple[str, ...] = ()


def preflight(twins: list[Twins], max_workers: int) -> list[Outcome]:
    """Every twin drawn by the generator and passed through the pcell stage's DRC gate (``build_device``), no EMX, in
    worker threads on this machine (at most ``max_workers``, its ``hosts.local`` threads), in a temporary directory removed
    afterwards. Refused by the generator: its message; refused by the gate: the violations (or the missing layers)."""
    jobs = [(k, t, i) for k, t in enumerate(twins) for i in range(len(t.points))]
    generators = {t.part: get_generator(t.spec.devices[0].generator, plugin_module=t.spec.devices[0].plugin) for t in twins}
    with tempfile.TemporaryDirectory(prefix="lib_tap_preflight_") as tmp:

        def one(job: tuple[int, Twins, int]) -> Outcome:
            k, t, i = job
            device = t.spec.devices[0]
            outdir = Path(tmp) / f"{k}_{i}"
            try:
                build_device(t.spec, device, t.points[i], outdir, generators[t.part])
            except DrcRefused as refused:
                record = refused.record
                reasons = ([f"[{v['kind']}] {v['layer']} x{v['count']}" for v in record["violations"]]
                           or [f"missing product layers {record.get('missing')}"])
                return Outcome(t.part, t.rows[i].obs_id, "drc", tuple(reasons))
            except StageFailure as failure:
                return Outcome(t.part, t.rows[i].obs_id, "generator", tuple(failure.issues))
            finally:
                shutil.rmtree(outdir, ignore_errors=True)
            return Outcome(t.part, t.rows[i].obs_id, "clean")

        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(jobs)))) as pool:
            return list(pool.map(one, jobs))


# -- the check --------------------------------------------------------------------------------------------------------

def _scalar(m, name: str, rule: manifest.Stratum) -> float | None:
    """A scalar under the stratum's definition: a declared peak within its band (``dataset._peak``), else the kernel's."""
    q = rule.quantities.get(name)
    if name in manifest.PEAKS and q is not None and q.band_ghz is not None:
        return dataset._peak(m.q.freqs, m.q.curves[name.split("_")[0]], q.band_ghz * 1e9, m.q.scalars.get("SRF"))
    value = m.q.scalars.get(name)
    return None if value is None or not math.isfinite(value) else float(value)


def window_columns(window: Window | None) -> list[str]:
    """The names the report gives the window's columns: a curve or ``Qmin`` at the window's frequency (``Lp@112``)."""
    if window is None:
        return []
    return [c if c in manifest.SCALARS or c == "area" else f"{c}@{window.frequency_ghz:g}" for c in window.ranges]


def measured(work: Path, device, rule: manifest.Stratum, window: Window | None) -> dict[str, float | None]:
    """One sNp measured with the library's definitions (``dataset._measure``: the stratum's low-frequency limit, the
    device's topology): ``QUANTITIES`` and, with a window, its columns at its frequency -- a curve by the index's rule
    (``dataset._anchor_value``), ``Qmin`` the smaller of ``Qp`` and ``Qs`` there, ``area`` the footprint of the GDS beside
    the sNp."""
    m = dataset._measure(work, device, rule)
    out: dict[str, float | None] = {name: _scalar(m, name, rule) for name in QUANTITIES}
    if window is None:
        return out
    f_hz = window.frequency_ghz * 1e9

    def curve(c: str) -> float | None:
        return dataset._anchor_value(m.q, c, rule.quantities[c], f_hz, m.start, m.stop) if c in rule.quantities else None

    for column, name in zip(window.ranges, window_columns(window), strict=True):
        if column in manifest.CURVES:
            out[name] = curve(column)
        elif column in manifest.SCALARS:
            out[name] = _scalar(m, column, rule)
        elif column == "Qmin":
            qp, qs = curve("Qp"), curve("Qs")
            out[name] = None if qp is None or qs is None else min(qp, qs)
        elif column == "area":
            try:
                box = fp.footprint(work / f"{device.id}.gds", profile=device.profile, generator=device.generator, plugin=device.plugin)
            except (fp.FootprintError, OSError, RuntimeError):
                box = None
            out[name] = None if box is None else box["area_um2"]
    return out


def ratio(twin: float | None, row: float | None) -> float | None:
    if twin is None or row is None or not (math.isfinite(twin) and math.isfinite(row)) or row == 0:
        return None
    return twin / row


def summarize(points: list[dict]) -> dict:
    """Per quantity the median and the 10th / 90th percentiles of twin / row over the ok twins, and the twins whose L or k
    (``MOVED_QUANTITIES``) moved by more than ``MOVED``."""
    names = list(dict.fromkeys(q for p in points for q in p.get("ratios", {})))
    ratios = {}
    for q in names:
        values = [p["ratios"][q] for p in points if p.get("ratios", {}).get(q) is not None]
        ratios[q] = ({"n": len(values), "median": float(np.median(values)), "p10": float(np.percentile(values, 10)),
                      "p90": float(np.percentile(values, 90))} if values else {"n": 0, "median": None, "p10": None, "p90": None})
    moved = [{"part": p["part"], "row": p["row"], "twin": p["twin"], "ratios": {q: p["ratios"][q] for q in p["moved"]}}
             for p in points if p.get("moved")]
    return {"ratios": ratios, "moved_beyond": MOVED, "moved": moved}


# -- adopt --------------------------------------------------------------------------------------------------------------

def _check_adopt(lib: query.Library, name: str, parts: list[str]) -> None:
    """Refused before anything runs: a name that is no plain identifier, a stratum that exists, a part store that does."""
    if not isinstance(name, str) or not STRATUM_NAME.match(name):
        raise TapError(f"adopt={name!r}: a new stratum's name is letters, digits and '_' (not starting with a digit)")
    if name in lib.manifest.strata:
        raise TapError(f"adopt={name}: the library has a stratum {name!r} already; choose another name")
    taken = [f"{name}__{part}" for part in parts if (lib.root / f"{name}__{part}").exists()]
    if taken:
        raise TapError(f"adopt={name}: {', '.join(taken)} exist(s) in {lib.root}: a new stratum's part stores are created fresh")


def stratum_entry(raw: dict, stores: list[str], note: str) -> dict:
    """The new stratum's entry: the source's own (as library.yaml spells it: generator, dims, nt_dim, steps, quantities,
    low_freq_max_hz) with the new parts and the note, after the generator."""
    entry: dict = {}
    for key, value in raw.items():
        if key == "note":
            continue
        entry[key] = [{"store": s} for s in stores] if key == "parts" else value
        if key == "generator":
            entry["note"] = note
    return entry


def _appended(text: str, name: str, entry: dict) -> str | None:
    """``library.yaml``'s text with the entry appended under ``strata`` -- None when ``strata`` is not the last top-level
    key or its entries are not a block mapping, where appending text would not add to it."""
    lines = text.splitlines()
    tops = [i for i, line in enumerate(lines) if line[:1] not in ("", " ", "\t", "#") and not line.startswith(("---", "..."))]
    if not tops or not re.match(r"^strata:\s*(#.*)?$", lines[tops[-1]]):
        return None
    indent = next((len(line) - len(line.lstrip(" ")) for line in lines[tops[-1] + 1:] if line.strip() and not line.lstrip().startswith("#")), 0)
    if indent == 0:
        return None
    block = yaml.safe_dump({name: entry}, sort_keys=False, default_flow_style=False, allow_unicode=True, width=1000)
    return (text if text.endswith("\n") else text + "\n") + "".join(" " * indent + line + "\n" for line in block.splitlines())


def insert_stratum(root: Path, name: str, entry: dict) -> dict:
    """Add the stratum to ``<root>/library.yaml``: backed up first, the entry appended as text, the result parsed back to
    the old manifest plus exactly the new stratum (and valid); when that cannot be ensured the file is left untouched and
    the entry goes to ``<root>/<name>.stratum.yaml``. Says which, and where."""
    path = root / manifest.MANIFEST
    text = path.read_text(encoding="utf-8")
    old = yaml.safe_load(text)
    if name in old["strata"]:
        raise TapError(f"adopt={name}: {path} has a stratum {name!r} now; the part stores are adopted, the entry not added")
    expected = {**old, "strata": {**old["strata"], name: entry}}
    new = _appended(text, name, entry)
    ok = False
    if new is not None:
        try:
            ok = yaml.safe_load(new) == expected
            manifest.Library.model_validate(expected)
        except (yaml.YAMLError, ValueError):
            ok = False
    if ok:
        backup = root / f"{manifest.MANIFEST}.bak_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
        shutil.copy2(path, backup)
        temporary = path.with_name(f".{path.name}.lib_tap.tmp")
        temporary.write_text(new, encoding="utf-8")
        os.replace(temporary, path)
        if yaml.safe_load(path.read_text(encoding="utf-8")) == expected:
            return {"manifest": "inserted", "path": str(path), "backup": str(backup)}
        shutil.copy2(backup, path)                     # not expected (the text was checked): the old file back, the snippet
    snippet = root / f"{name}.stratum.yaml"
    snippet.write_text(f"# the stratum lib_tap could not append to {path.name} (strata is not its last top-level block):\n"
                       f"# add it under strata: yourself, indented like the other strata\n"
                       + yaml.safe_dump({name: entry}, sort_keys=False, default_flow_style=False, allow_unicode=True, width=1000),
                       encoding="utf-8")
    return {"manifest": "snippet", "path": str(snippet)}


def _tap_text(taps: Taps, twins: list[Twins]) -> str:
    """What the taps are, for the plan and the note: ``primary same (M6) width 3 um, secondary M3`` (the profile's names)."""
    names: dict[str, list[str]] = {}
    for t in twins:
        for winding, metal in t.taps.items():
            names.setdefault(winding, [])
            if metal not in names[winding]:
                names[winding].append(metal)
    parts = []
    for winding, _port in WINDINGS:
        want = taps.side(winding)
        if want is None:
            parts.append(f"{winding} none")
            continue
        metal = "/".join(names.get(winding, [want]))
        text = f"{winding} same ({metal})" if want.lower() == "same" else f"{winding} {metal}"
        width = taps.width(winding)
        parts.append(text + (f" width {width:g} um" if width is not None else ""))
    return ", ".join(parts)


def _strict(value):
    """``value`` with every non-finite float as None: the report is strict JSON."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _strict(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_strict(v) for v in value]
    return value


def adopt_twins(run: Run, lib: query.Library, name: str, source_stratum: str, twins: list[Twins], ok: dict[str, list],
                taps: Taps, origin_text: str, cache_dir) -> dict:
    """Each source part's ok twins into a fresh part store (``lib_signoff._adopt``'s way, the origin kept), with the twin
    spec; then the new stratum into library.yaml (``insert_stratum``) and, when it is there, its dataset built."""
    parts = [t.part for t in twins if ok.get(t.part)]
    _check_adopt(lib, name, parts)                    # again: a store may have appeared while EMX ran
    adopted: dict[str, list[str]] = {}
    for t in twins:
        if not ok.get(t.part):
            continue
        store_dir = lib.root / f"{name}__{t.part}"
        adopted[store_dir.name] = _adopt(store_dir, run.store, ok[t.part], origin=lambda o: o.origin)
        (store_dir / ".icopt" / "spec.json").write_text(t.spec.model_dump_json(indent=1) + "\n", encoding="utf-8")
    n = sum(len(ids) for ids in adopted.values())
    note = (f"tapped twins of {source_stratum} ({n} rows, {origin_text}): taps {_tap_text(taps, twins)}, measured "
            f"{taps.measure}; lib_tap, {datetime.now(UTC).date().isoformat()}")
    raw = yaml.safe_load((lib.root / manifest.MANIFEST).read_text(encoding="utf-8"))["strata"][source_stratum]
    entry = stratum_entry(raw, list(adopted), note)
    done = {"stratum": name, "parts": adopted, **insert_stratum(lib.root, name, entry)}
    if done["manifest"] == "inserted":
        ds = query.Library(lib.root, limits=lib.limits, cache_dir=cache_dir).dataset(name)
    else:                                            # the stratum is not in library.yaml: built in memory, nothing cached
        declared = manifest.Library.model_validate({**lib.manifest.model_dump(mode="json"),
                                                    "strata": {**lib.manifest.model_dump(mode="json")["strata"], name: entry}})
        ds = dataset.build(lib.root, name, library=declared, cache=False)
    done["dataset"] = {"rows": len(ds.rows), "excluded": ds.excluded}
    return done


# -- the recipe -------------------------------------------------------------------------------------------------------

def _median_max(values: list[float]) -> str:
    return f"median {statistics.median(values):.2f} GB, max {max(values):.2f} GB" if values else "none recorded"


def main(run: Run, *, library: str, stratum: str, taps, window=None, rows=None, adopt: str | None = None,
         threads: int | None = None, memory_gb: float | None = None, process_file: str | None = None,
         cache_dir: str | None = None) -> None:
    tap = parse_taps(taps)
    if (window is None) == (rows is None):
        raise TapError("give exactly one of window=<json> (the rows of the stratum's index inside ranges) and rows=<file> "
                       f"(a lib.index out= table, a lib.pick answer or a list of part / obs id); got {'both' if window is not None else 'neither'}")
    lib = query.Library(library, limits=run.site.host("local"), cache_dir=cache_dir)   # the library computes on the controller
    for note in lib.notes:
        run.note(f"lib_tap: {note}")
    if stratum not in lib.manifest.strata:
        raise TapError(f"no stratum {stratum!r} in {lib.root / manifest.MANIFEST}; have {lib.strata()}")
    rule = lib.manifest.strata[stratum]
    if rule.generator != SUPPORTED:
        raise TapError(f"stratum {stratum} is {rule.generator}; lib_tap builds tapped twins of {SUPPORTED} tables only")
    win = parse_window(window) if window is not None else None
    if win is not None:
        selected, origin_text = select_window(lib, stratum, win), win.text()
    else:
        selected, origin_text = select_rows(lib, stratum, rows)
    if not selected:
        raise TapError(f"no row of {stratum} lies inside the {origin_text}: nothing to build")
    by_part: dict[str, list[Selected]] = {}
    for row in selected:
        by_part.setdefault(row.part, []).append(row)
    if adopt is not None:
        _check_adopt(lib, adopt, list(by_part))
    twins = [twins_of_part(run, lib, part, part_rows, tap, threads=threads, memory_gb=memory_gb, process_file=process_file)
             for part, part_rows in by_part.items()]
    run.note(f"lib_tap: {len(selected)} rows of {stratum} from {origin_text} -- "
             + "; ".join(f"{t.part} {len(t.rows)} ({', '.join(r.obs_id for r in t.rows)})" for t in twins))
    run.note(f"lib_tap: taps {_tap_text(tap, twins)}; measured {tap.measure}: "
             + ", ".join(f"{t.part} ports {t.spec.devices[0].ports}, grounded {t.spec.devices[0].topology.grounded}" for t in twins))
    for t in twins:
        em = t.spec.em
        workers = engine.workers_for(t.spec, em_only_pipeline(t.spec), run.jobs, run.limits)
        fixture = f"{t.fixture_metal} (metal_rule {t.metal_rule})" if t.fixture_metal else "the fixture conductor (the default fixture)"
        run.note(f"lib_tap: {t.part}: fixture kept on {fixture}; EMX {em.threads} thread(s) and a {em.memory_gb:g} GB memory cap per "
                 f"job, up to {workers} jobs at once; the rows' own EMX peak memory (from their emx.log, {len(t.peaks_gb)} of "
                 f"{len(t.rows)}): {_median_max(t.peaks_gb)}")
    outcomes = preflight(twins, run.site.host("local").max_threads)
    clean = {(o.part, o.row) for o in outcomes if o.status == "clean"}
    refused = [o for o in outcomes if o.status != "clean"]
    run.note(f"lib_tap: preflight (generator and DRC gate, no EMX): {len(clean)} of {len(outcomes)} clean, "
             f"{sum(o.status == 'generator' for o in refused)} refused by the generator, {sum(o.status == 'drc' for o in refused)} "
             "by the DRC gate")
    groups: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    for o in refused:
        groups.setdefault((o.status, o.reasons), []).append(f"{o.part}/{o.row}")
    for (status, reasons), ids in groups.items():
        who = "the generator" if status == "generator" else "the DRC gate"
        run.note(f"lib_tap: refused by {who} ({len(ids)}): {'; '.join(reasons)} -- {', '.join(ids)}")
    used = sum(engine.simulations(o) for o in run.store.observations())
    needed = sum(_per_point(t.spec) * sum((t.part, r.obs_id) in clean and not _reusable(run, t, i) for i, r in enumerate(t.rows))
                 for t in twins)
    budget = run.spec.budget.max_simulations
    short = used + needed > budget
    run.note(f"lib_tap: budget: {used} of {budget} simulations used in {run.store.project_dir.name}; this run needs up to {needed} "
             "more (a twin already in its store is reused)"
             + ("; NOT ENOUGH: raise budget.max_simulations in its spec.yaml" if short else ""))
    if adopt is not None:
        text = (lib.root / manifest.MANIFEST).read_text(encoding="utf-8")
        how = ("appended to library.yaml (backed up first)" if _appended(text, adopt, {"generator": SUPPORTED}) is not None else
               f"library.yaml cannot take it as appended text: the entry goes to {adopt}.stratum.yaml")
        run.note(f"lib_tap: adopt as {adopt}: part stores " + ", ".join(f"{adopt}__{t.part}" for t in twins)
                 + f" in {lib.root}, the stratum {how}")
    if short and not run.plan:
        raise TapError(f"budget: {used} of {budget} simulations used in {run.store.project_dir}, and this run needs up to "
                       f"{needed} more; raise budget.max_simulations in its spec.yaml")
    results: dict[tuple[str, str], object] = {}
    for t in twins:
        pts = [p for p, r in zip(t.points, t.rows, strict=True) if (t.part, r.obs_id) in clean]
        if not pts:
            continue
        obs = b.evaluate(t.spec, pts, run.executor, run.store, pipeline=em_only_pipeline(t.spec), step=f"lib_tap:{t.part}",
                         cshrc=run.cshrc, parallel_jobs=run.jobs, limits=run.limits)
        for point, o in zip(pts, obs, strict=False):
            results[(t.part, point.origin.rsplit(":", 1)[1])] = o
    if run.plan:
        return
    report = {"call": {"library": str(lib.root), "stratum": stratum, "taps": tap.__dict__, "window": None if win is None else win.__dict__,
                       "rows": None if rows is None else str(rows), "adopt": adopt, "threads": threads, "memory_gb": memory_gb,
                       "process_file": process_file},
              "selected": {"from": origin_text, "parts": {t.part: [r.obs_id for r in t.rows] for t in twins}},
              **_check(run, lib, rule, win, twins, outcomes, results)}
    out = run.store.root / "reports" / "lib_tap.json"
    ok = {t.part: [results[(t.part, r.obs_id)] for r in t.rows if (t.part, r.obs_id) in results
                   and results[(t.part, r.obs_id)].status == "ok"] for t in twins}
    out.write_text(json.dumps(_strict(report), indent=1, allow_nan=False) + "\n", encoding="utf-8")   # before adopting: kept if it fails
    if adopt is not None and any(ok.values()):
        report["adopted"] = adopt_twins(run, lib, adopt, stratum, twins, ok, tap, origin_text, cache_dir)
        out.write_text(json.dumps(_strict(report), indent=1, allow_nan=False) + "\n", encoding="utf-8")
    medians = ", ".join(f"{q} {s['median']:.3f}" for q, s in report["summary"]["ratios"].items() if s["median"] is not None)
    run.note(f"lib_tap: {report['summary']['ok']}/{report['summary']['attempted']} twins ok; median twin / row {medians or '-'}; "
             f"report {out}")
    if report.get("adopted"):
        a = report["adopted"]
        where = f"library.yaml updated (backup {Path(a['backup']).name})" if a["manifest"] == "inserted" else \
            f"library.yaml left untouched: the stratum's entry is in {a['path']}"
        run.note(f"lib_tap: adopted {sum(len(v) for v in a['parts'].values())} twins as {adopt} ({', '.join(a['parts'])}); {where}; "
                 f"its dataset: {a['dataset']['rows']} rows, excluded {a['dataset']['excluded']}")
    elif adopt is not None:
        run.note(f"lib_tap: nothing adopted as {adopt}: no twin is ok")


def _per_point(spec: Spec) -> int:
    """What one twin costs the budget, as the engine counts it: the EMX run and the device's measurement."""
    pipeline = em_only_pipeline(spec)
    return len(engine.counted_children(pipeline, engine.children_of(spec, pipeline, [None]))) + engine.point_runs(pipeline)


def _reusable(run: Run, t: Twins, i: int) -> bool:
    """Whether PROJECT's store holds an ok observation of this twin point under the twin spec -- the engine reuses it
    instead of simulating (its pipeline's identity checked by the engine itself; here only the problem and the point)."""
    key, problem = t.points[i].key, t.spec.fingerprint()
    return any(o.key == key and o.status == "ok" and o.spec_fingerprint == problem for o in run.store.observations())


def _check(run: Run, lib: query.Library, rule: manifest.Stratum, window: Window | None, twins: list[Twins],
           outcomes: list[Outcome], results: dict) -> dict:
    """The report's twins and summary: each simulated twin with its status, and an ok one with its values, its row's and
    their ratios (``measured`` on both sNp files)."""
    points = []
    for t in twins:
        twin_device = t.spec.devices[0]
        row_device = dataset._spec(lib.root / t.part).devices[0]
        for row in t.rows:
            o = results.get((t.part, row.obs_id))
            if o is None:
                continue
            entry = {"part": t.part, "row": row.obs_id, "twin": o.obs_id, "origin": o.origin, "status": o.status, "params": o.params}
            if o.status != "ok":
                entry["issues"] = list(o.issues)[:5]
                points.append(entry)
                continue
            try:
                twin = measured(run.store.root / "sims" / o.obs_id / "em" / twin_device.id, twin_device, rule, window)
                base = measured((lib.root / row.snp).parent, row_device, rule, window)
            except (measure_kernel.MeasureError, touchstone.TouchstoneError, dataset.DatasetError, OSError) as exc:
                entry["check"] = f"not measured: {type(exc).__name__}: {exc}"      # the dataset would leave such a row out
                points.append(entry)
                continue
            ratios = {q: ratio(twin[q], base[q]) for q in twin}
            entry.update(values={"twin": twin, "row": base}, ratios=ratios,
                         moved=[q for q in MOVED_QUANTITIES if ratios.get(q) is not None and abs(ratios[q] - 1) > MOVED])
            points.append(entry)
    summary = summarize([p for p in points if p["status"] == "ok"])
    summary.update(attempted=len(points), ok=sum(p["status"] == "ok" for p in points),
                   failed=[{"part": p["part"], "row": p["row"], "twin": p["twin"], "status": p["status"]} for p in points if p["status"] != "ok"],
                   refused=sum(o.status != "clean" for o in outcomes))
    return {"preflight": [{"part": o.part, "row": o.row, "status": o.status, "reasons": list(o.reasons)} for o in outcomes],
            "twins": {t.part: {"fixture_metal": t.fixture_metal, "metal_rule": t.metal_rule, "taps": t.taps,
                               "ports": t.spec.devices[0].ports, "grounded": t.spec.devices[0].topology.grounded} for t in twins},
            "points": points, "summary": summary, "adopted": None}
