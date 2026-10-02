"""A stratum's dataset: one row per usable observation, its query quantities re-measured from the sNp under one definition.

Rows come from the stratum's parts (run stores). Per part only ``ok`` observations of one generation
(pipeline fingerprint: geometry generation + EMX physics + process file) are used -- the pinned one or
the part's most common -- so generations never mix. Every quantity is recomputed from the stored sNp by
``ic_opt.em.measure``: peaks inside the manifest's band, curves at the anchor frequencies (unusable where
the resonance sits at or below ``srf_margin`` x f0), low-frequency scalars up to the stratum's
``low_freq_max_hz`` (else the part spec's), so parts swept to different frequencies answer with the same
definition; a row whose sweep cannot give a value (an anchor below its first sample or above its last, no
sample in the low-frequency band, no resonance) has None in that column only. Each row also carries the integrity evidence ``check`` reports: the largest
singular value of S over frequency (passivity) and whether the stored quantities.json reproduces under
the definition the run measured with (the part spec's).

Built datasets are cached in the library's cache (``cache.locate``: ``<library>/.cache/`` unless another
directory is given or that one cannot be written) keyed by what they are made of -- the rows each part keeps and
leaves out, the parts' devices, the quantity definitions and the measure code -- so a query does not re-read
thousands of sNp files. The key ignores how the rows are stamped: restamping their fingerprints
(``ic-opt migrate-store``) keeps it, and with it the calibration and model caches built on it. It ignores a
quantity's confidence ceiling (``rel_sigma_max``) too: that decides what an answer trusts, not what a row holds.

A declared curve answers at any frequency, not only at its anchors (T18.1): ``<curve>@<f>`` at another frequency is an
*extension column* (``resolve`` names it). ``anchors`` measures every declared curve at that frequency again from each
row's sNp -- one pass, the rule of a declared anchor (``_anchor_value``), so the column equals what a manifest declaring
that anchor gives -- and caches it beside the dataset as ``anchors-<stratum>-<dataset key>-<f>.json``; ``Dataset.extend``
puts the values into the rows. The dataset itself, its key and its file do not change: a library that never asks for an
extension column never reads or writes such a file. ``footprints`` measures, once per stratum, each row's footprint
from the GDS kept beside its sNp (``ic_opt.em.pcell.footprint``), cached as ``footprint-<stratum>-<dataset key>.json``.
"""

from __future__ import annotations

import collections
import hashlib
import inspect
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from ic_opt.em import measure, touchstone
from ic_opt.library import manifest
from ic_opt.library.cache import Cache, computed, locate
from ic_opt.observation import Observation
from ic_opt.spec import Device, Spec, Topology

DATASET_VERSION = 3                                  # 2: anchored curves of a coupled pair stop below the system SRF; 3: so do peaks
ANCHORS_VERSION = 1                                  # the layout of an anchors-* file (``anchors``); a file of another is computed again
FOOTPRINT_VERSION = 2                                # the layout of a footprint-* file (``footprints``); likewise. 2: each row through
                                                     # ``footprint.footprint`` (its recorded device box, else its recorded fixture metal);
                                                     # 1 measured every row against the bottom metal's layer -- the ring's outer box for
                                                     # a fixture drawn on another metal (T19.1)
UNBANDED = ("Lp_lf", "Lp_res", "SRF_p", "Ls_lf", "Ls_res", "SRF_s", "k_lf")     # compared with the stored quantities.json
_PORT = re.compile(r"^p(\d+)=([^:]+)(?::(.+))?$")


class DatasetError(ValueError):
    """The library cannot yield this stratum's dataset as declared (fail-closed)."""


class UnknownColumn(ValueError):
    """A name that is no column of the stratum: ``kind`` is ``quantity`` (no such quantity at all) or ``curve`` (a curve
    the stratum does not declare, at some frequency)."""

    def __init__(self, name: str, kind: str):
        super().__init__(f"no quantity {name!r}")
        self.name, self.kind = name, kind


@dataclass
class Row:
    part: str
    obs_id: str
    coords: dict[str, float]
    values: dict[str, float | None]
    stop_hz: float
    n_freq: int
    n_ports: int
    max_singular: float
    stored_match: bool | None                         # None: the store has no quantities.json for this point
    snp: str                                          # relative to the library root


@dataclass
class Dataset:
    stratum: str
    dims: list[str]
    nt_dim: str | None
    columns: list[str]
    rows: list[Row]
    generations: dict[str, str]                       # part -> pipeline fingerprint used
    excluded: dict[str, int] = field(default_factory=dict)
    cache: str = "off"                                # hit | miss | off
    key: str = ""                                     # content key of the observations + definitions + measure code
    extensions: dict[str, float] = field(default_factory=dict)   # extension column -> its frequency in GHz, once extended

    def has(self, column: str) -> bool:
        """Whether ``column`` is one of the rows' values: a declared column, or an extension column already extended."""
        return column in self.columns or column in self.extensions

    def extend(self, ghz: float, curves: dict[str, list[float | None]]) -> list[str]:
        """Put each curve's values at ``ghz`` GHz (``anchors``: one per row, in the rows' order) into the rows as the column
        ``<curve>@<ghz>``; a column the stratum declares (a curve anchored there) or one extended before is left as it is.
        Returns the columns added."""
        added = []
        for curve, values in curves.items():
            column = f"{curve}@{ghz:g}"
            if self.has(column):
                continue
            for row, value in zip(self.rows, values, strict=True):
                row.values[column] = value
            self.extensions[column] = ghz
            added.append(column)
        return added

    def matrix(self, rows: list[Row] | None = None) -> np.ndarray:
        return np.array([[r.coords[d] for d in self.dims] for r in (self.rows if rows is None else rows)], dtype=float)

    def values(self, column: str, rows: list[Row] | None = None) -> np.ndarray:
        return np.array([np.nan if r.values[column] is None else r.values[column] for r in (self.rows if rows is None else rows)], dtype=float)

    def usable(self, column: str) -> list[Row]:
        """Rows with a finite value in ``column`` (a missing SRF, an anchor past the resonance margin, ... are left out)."""
        return [r for r in self.rows if r.values.get(column) is not None and np.isfinite(r.values[column])]

    def find(self, coords: dict[str, float]) -> Row | None:
        """The measured row at exactly these coordinates (compared after rounding to 1e-9)."""
        key = tuple(round(float(coords[d]), 9) for d in self.dims)
        return next((r for r in self.rows if tuple(round(r.coords[d], 9) for d in self.dims) == key), None)


def build(root: str | Path, name: str, *, library: manifest.Library | None = None, cache: bool = True,
          cache_dir: Cache | str | Path | None = None) -> Dataset:
    """The dataset of stratum ``name`` of the library at ``root``, cached unless ``cache=False``: in ``cache_dir`` (a
    ``Cache``, or a directory for ``cache.locate``; default: the library's own ``.cache`` when it can be written)."""
    root = Path(root)
    lib = library or manifest.load(root)
    if name not in lib.strata:
        raise DatasetError(f"no stratum {name!r} in {root / manifest.MANIFEST}; have {sorted(lib.strata)}")
    stratum = lib.strata[name]
    parts = [_select(root, name, part) for part in stratum.parts]
    generations = {p.store: p.generation for p in parts if p.generation is not None}
    key = _cache_key(stratum, parts)
    file = f"dataset-{name}-{key}.json"
    store = (cache_dir if isinstance(cache_dir, Cache) else locate(root, cache_dir)) if cache else None
    cached = store.find(file) if store else None
    if cached is not None:
        return _load(cached, stratum, name, "hit", key, generations)

    columns = stratum.columns()
    rows: list[Row] = []
    excluded: collections.Counter[str] = collections.Counter()
    for p in parts:
        excluded.update(p.excluded)
        for o in p.kept:
            missing = [d for d in stratum.dims if d not in o.params]
            if missing:
                raise DatasetError(f"{name}: {p.store}/{o.obs_id} lacks the dims {missing}")
            try:
                rows.append(_row(root, p.project, p.store, p.device, o, stratum))
            except (measure.MeasureError, touchstone.TouchstoneError, OSError) as exc:
                excluded[f"measure: {type(exc).__name__}"] += 1
    ds = Dataset(name, list(stratum.dims), stratum.nt_dim, columns, rows, generations, dict(excluded), "miss" if cache else "off", key)
    if store is not None:                            # a writer's own temporary file, renamed over: a reader sees none or all of it
        target = store.target(file)
        with tempfile.NamedTemporaryFile("w", dir=target.parent, prefix=f".{target.stem}.", suffix=".tmp", delete=False,
                                         encoding="utf-8") as f:
            f.write(json.dumps({"version": DATASET_VERSION, "generations": generations, "excluded": dict(excluded),
                                "rows": [asdict(r) for r in rows]}))
        os.replace(f.name, target)
    return ds


def check(ds: Dataset) -> dict:
    """Integrity evidence for a built dataset: duplicates, passivity, stored-value reproduction, sweep grids, port counts,
    and for a stratum with a ``k_lf`` column the rows measured with k_lf < 0 (``measure.reversed_coupling``: the part
    spec's topology reverses a drive the generator does not; None without that column)."""
    keys = [tuple(round(r.coords[d], 9) for d in ds.dims) for r in ds.rows]
    grids = collections.Counter((r.part, r.n_freq, round(r.stop_hz / 1e9, 6)) for r in ds.rows)
    negative_k = sum(measure.reversed_coupling(r.values.get("k_lf")) for r in ds.rows) if "k_lf" in ds.columns else None
    return {
        "stratum": ds.stratum,
        "rows": len(ds.rows),
        "excluded": ds.excluded,
        "generations": ds.generations,
        "duplicate_coordinates": len(keys) - len(set(keys)),
        "passive_rows": sum(r.max_singular <= 1 + 1e-6 for r in ds.rows),
        "max_singular_value": max((r.max_singular for r in ds.rows), default=None),
        "stored_reproduced": sum(r.stored_match is True for r in ds.rows),
        "stored_mismatch": [f"{r.part}/{r.obs_id}" for r in ds.rows if r.stored_match is False][:20],
        "grids": [{"part": p, "n_freq": n, "stop_ghz": s, "rows": c} for (p, n, s), c in sorted(grids.items())],
        "ports": dict(collections.Counter(r.n_ports for r in ds.rows)),
        "values": {c: sum(r.values.get(c) is not None for r in ds.rows) for c in ds.columns},
        "negative_k_lf": negative_k,
    }


# -- internals ------------------------------------------------------------------------------------------------------------

@dataclass
class _Part:
    """One part's share of a stratum, before any sNp is read: its device, the generation it uses and the rows that
    generation keeps (ok, in file order), and what it leaves out (non-ok statuses, other generations)."""

    store: str
    project: Path
    device: Device
    generation: str | None
    kept: list[Observation]
    excluded: collections.Counter[str]


def _select(root: Path, name: str, part: manifest.Part) -> _Part:
    project = root / part.store
    path = project / ".icopt" / "observations.jsonl"
    if not path.is_file():
        raise DatasetError(f"{name}: part {part.store} has no observations ({path})")
    spec = _spec(project)
    if len(spec.devices) != 1:
        raise DatasetError(f"{name}: part {part.store} has {len(spec.devices)} devices; a library store holds one")
    observations = [Observation.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ok = [o for o in observations if o.status == "ok"]
    excluded = collections.Counter(f"status:{o.status}" for o in observations if o.status != "ok")
    generation = part.pipeline_fingerprint or _most_common(o.pipeline_fingerprint for o in ok)
    kept = [o for o in ok if o.pipeline_fingerprint == generation] if generation is not None else []
    if generation is not None and len(kept) < len(ok):
        excluded["other generation"] += len(ok) - len(kept)
    return _Part(part.store, project, spec.devices[0], generation, kept, excluded)


def devices(root: str | Path, stratum: manifest.Stratum) -> dict[str, dict]:
    """Each part's device as the rows were measured: generator, profile and the fields naming a metal (``metal``,
    ``primary_metal``, ...), read from the part's spec. A stratum's name says little about which metal a winding sits on
    (the 2026-09-27 real-scenario acceptance, N-27, ISSUE-5, queried a part whose windings mirror the device in hand), so
    ``lib.coverage`` answers with this instead of leaving the part's spec files to be read."""
    out = {}
    for part in stratum.parts:
        device = _spec(Path(root) / part.store).devices[0]
        out[part.store] = {"generator": device.generator, "profile": device.profile,
                           "metals": {k: v for k, v in device.fixed.items() if k.endswith("metal")}}
    return out


def part_devices(root: str | Path, stratum: manifest.Stratum) -> dict[str, Device]:
    """Each part's device as its spec defines it (a library store holds one): the sNp's labels and the GDS's name come
    from it."""
    return {p.store: _spec(Path(root) / p.store).devices[0] for p in stratum.parts}


def curves(stratum: manifest.Stratum) -> list[str]:
    """The curves the stratum declares (``manifest.CURVES`` order): the ones that answer at any frequency."""
    return [c for c in manifest.CURVES if c in stratum.quantities]


def resolve(stratum: manifest.Stratum, column: str) -> tuple[str, float | None]:
    """``column`` as a column of ``stratum``: ``(the column's name, None)`` for a declared column, ``(the column's
    name, its frequency in GHz)`` for an extension column -- ``<curve>@<f>`` of a declared curve at a frequency that is
    none of its anchors. The name is canonical: ``<f>`` written ``%g`` (``Lp@28.0`` is ``Lp@28``, a declared anchor).
    UnknownColumn for a name that is no quantity of the stratum (``kind`` "quantity") or a curve it does not declare
    (``kind`` "curve"); ValueError for an ``<f>`` that is no positive number of GHz, or has more than six significant
    digits (the column's name would round it)."""
    if column in stratum.columns():
        return column, None
    base, at, text = column.partition("@")
    if not at or base not in manifest.CURVES:
        raise UnknownColumn(column, "quantity")
    try:
        ghz = float(text)
    except ValueError:
        ghz = float("nan")
    if not (np.isfinite(ghz) and ghz > 0):
        raise ValueError(f"{column}: the part after @ is a frequency in GHz, a positive number (as in {base}@28)")
    canonical = f"{base}@{ghz:g}"
    if float(f"{ghz:g}") != ghz:
        raise ValueError(f"{column}: write the frequency with at most six significant digits -- it names the column, and "
                         f"{canonical} would be {float(f'{ghz:g}'):g} GHz")
    if base not in stratum.quantities:
        raise UnknownColumn(column, "curve")
    return canonical, (None if canonical in stratum.columns() else ghz)


def sweeps(root: str | Path, stratum: manifest.Stratum) -> dict[str, tuple[float, float]]:
    """Each part's frequency sweep, ``(first, last)`` in Hz, as its spec states it (``em.frequencies``: a sweep's start and
    stop, a list's lowest and highest): one EMX setting per part. A part whose spec has no ``em`` section is left out."""
    out = {}
    for part in stratum.parts:
        em = _spec(Path(root) / part.store).em
        if em is None:
            continue
        f = em.frequencies
        out[part.store] = (float(min(f)), float(max(f))) if isinstance(f, list) else (float(f.start_hz), float(f.stop_hz))
    return out


def describe_sweeps(swept: dict[str, tuple[float, float]]) -> str:
    """``part a-b GHz`` per part, for messages."""
    return ", ".join(f"{part} {a / 1e9:g}-{b / 1e9:g} GHz" for part, (a, b) in swept.items()) or "no part states its sweep"


def anchors_file(ds: Dataset, ghz: float) -> str:
    return f"anchors-{ds.stratum}-{ds.key}-{ghz:g}.json"


def anchors(root: str | Path, ds: Dataset, stratum: manifest.Stratum, ghz: float, *, cache: Cache | None) -> dict:
    """Every curve the stratum declares at ``ghz`` GHz, one value per row of ``ds`` in its order, by a declared anchor's rule
    (``_anchor_value``), and the facts that rule used per row: the system SRF (``srf_hz``, None without a resonance in the
    sweep) and the sweep's first and last frequency (``start_hz``, ``stop_hz``). One pass over the rows' sNp files,
    measured as the dataset measured them (``_measure``), cached as ``anchors_file`` (``cache.computed``: one process
    computes it at a time, the lock discipline of the model cache). A row whose sNp can no longer be read is refused:
    the dataset was built from it."""
    root = Path(root)
    names = curves(stratum)
    ids = [[r.part, r.obs_id] for r in ds.rows]

    def compute() -> dict:
        devices = part_devices(root, stratum)
        out = {"version": ANCHORS_VERSION, "frequency_ghz": ghz, "rows": ids, "curves": {c: [] for c in names},
               "srf_hz": [], "start_hz": [], "stop_hz": []}
        for r in ds.rows:
            try:
                m = _measure((root / r.snp).parent, devices[r.part], stratum)
            except (measure.MeasureError, touchstone.TouchstoneError, OSError) as exc:
                raise DatasetError(f"{ds.stratum}: {r.part}/{r.obs_id}: its sNp {r.snp} cannot be measured again "
                                   f"({type(exc).__name__}: {exc}); the dataset was built from it") from exc
            for c in names:
                out["curves"][c].append(_anchor_value(m.q, c, stratum.quantities[c], ghz * 1e9, m.start, m.stop))
            out["srf_hz"].append(m.q.scalars.get("SRF"))
            out["start_hz"].append(m.start)
            out["stop_hz"].append(m.stop)
        return out

    def valid(data: dict) -> bool:
        return (data.get("version") == ANCHORS_VERSION and data.get("frequency_ghz") == ghz and data.get("rows") == ids
                and set(data.get("curves", {})) == set(names))

    return computed(cache, anchors_file(ds, ghz), compute, valid)


def footprints_file(ds: Dataset) -> str:
    return f"footprint-{ds.stratum}-{ds.key}.json"


def footprints(root: str | Path, ds: Dataset, stratum: manifest.Stratum, *, cache: Cache | None) -> dict:
    """Each row's footprint (``ic_opt.em.pcell.footprint.footprint``: the bounding box of the drawn device without its
    ground fixture -- the device box its manifest records, else the GDS measured without the fixture's layer, the
    recorded fixture metal's or the bottom metal's), from the GDS kept beside its sNp (``<device>.gds`` in the same
    directory): ``footprints`` one per row of ``ds`` in its order, ``reasons`` why a row has None (no GDS there; a
    generator whose fixture cannot be told apart; a GDS that cannot be read). One pass per stratum, cached as
    ``footprints_file``; not kept when nothing was read (nothing is worth keeping) or a row's process profile could not
    be loaded here (another machine may). When no row keeps a GDS at all the cache is not touched: no file, no lock file
    -- such a library's cache holds exactly what it held."""
    from ic_opt.em.pcell import footprint as fp

    root = Path(root)
    ids = [[r.part, r.obs_id] for r in ds.rows]
    devices = part_devices(root, stratum)
    paths = [(root / r.snp).parent / f"{devices[r.part].id}.gds" for r in ds.rows]
    if not any(p.is_file() for p in paths):
        return {"version": FOOTPRINT_VERSION, "rows": ids, "footprints": [None] * len(ids), "reasons": ["no GDS beside its sNp"] * len(ids),
                "read": 0, "retry": False}

    def compute() -> dict:
        refused: dict[str, str] = {}                    # part -> why its generator's fixture cannot be told apart
        for part, device in devices.items():
            try:
                fp.require_builtin(device.generator, device.plugin)
            except fp.FootprintError as exc:
                refused[part] = str(exc)
        out = {"version": FOOTPRINT_VERSION, "rows": ids, "footprints": [], "reasons": [], "read": 0, "retry": False}
        for r, gds in zip(ds.rows, paths, strict=True):
            device = devices[r.part]
            box, why = None, None
            if not gds.is_file():
                why = "no GDS beside its sNp"
            elif r.part in refused:
                why = refused[r.part]
            else:
                out["read"] += 1
                try:
                    box = fp.footprint(gds, profile=device.profile, generator=device.generator, plugin=device.plugin)
                except fp.ProfileUnavailable as exc:            # only a row without a recorded box needs the profile
                    why, out["retry"] = str(exc), True
                except fp.FootprintError as exc:
                    why = str(exc)
                except (OSError, RuntimeError) as exc:           # klayout reports a broken GDS as a RuntimeError
                    why = f"its GDS cannot be read: {exc}"
            out["footprints"].append(box)
            out["reasons"].append(why)
        return out

    def valid(data: dict) -> bool:
        return data.get("version") == FOOTPRINT_VERSION and data.get("rows") == ids

    return computed(cache, footprints_file(ds), compute, valid, keep=lambda data: data["read"] > 0 and not data["retry"])


def _spec(project: Path) -> Spec:
    for path in (project / "spec.yaml", project / ".icopt" / "spec.json"):
        if path.is_file():
            import yaml
            return Spec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    raise DatasetError(f"{project}: no spec.yaml or .icopt/spec.json")


def _most_common(values) -> str | None:
    counts = collections.Counter(v for v in values if v)
    return counts.most_common(1)[0][0] if counts else None


def snp_columns(work: Path, device) -> list[str]:
    """sNp column order: the ``-p pNN=SIGNAL:REF`` arguments EMX ran with (EMX sorts ports by name), else the device's ports."""
    cmd = work / "emx.cmd"
    if cmd.is_file():
        tokens = cmd.read_text(encoding="utf-8").split()
        ports = [m for t in tokens if (m := _PORT.match(t))]
        if ports:
            return [m.group(2) for m in sorted(ports, key=lambda m: int(m.group(1)))]
    return list(device.ports)


@dataclass
class _Measured:
    """A row's sNp measured again under the stratum's definition: what ``_row`` and ``anchors`` read from it."""

    snp: Path
    columns: list[str]                                # the sNp's column labels, in the file's order (``snp_columns``)
    ts: touchstone.Touchstone
    topology: Topology                                # the device's measurement topology, as its spec states or implies it
    ran_with: float | str | None                      # the run's own low-frequency limit (quantities.json's definition)
    limit: float | str | None                         # the one the stratum measures with (the manifest's wins)
    q: measure.Quantities
    start: float                                      # the sweep's first and last frequency, Hz
    stop: float


def _measure(work: Path, device, stratum: manifest.Stratum) -> _Measured:
    columns = snp_columns(work, device)
    snp = work / f"{device.id}.s{len(columns)}p"
    ts = touchstone.read(snp)
    topo_spec = device.topology or device.default_topology()
    ran_with = topo_spec.low_freq_max_hz                                   # the run's own definition (quantities.json)
    limit = ran_with if stratum.low_freq_max_hz is None else stratum.low_freq_max_hz      # the manifest's wins
    topo = measure.Topology.from_labels(topo_spec.drives, topo_spec.grounded, columns, low_freq_max_hz=limit)
    q = measure.quantities(ts.freqs, ts.s, topo, z0=ts.z0)
    return _Measured(snp, columns, ts, topo_spec, ran_with, limit, q, float(ts.freqs[0]), float(ts.freqs[-1]))


def _anchor_value(q: measure.Quantities, curve: str, rule: manifest.Quantity, f_hz: float, start: float, stop: float) -> float | None:
    """A curve's column at ``f_hz`` for one row -- a declared anchor's and an extension column's alike: None when the
    frequency lies outside the row's sweep or the row's system SRF is at or below the curve's ``srf_margin`` x f; else
    the curve there (``Quantities.at``) when finite, else None."""
    srf = _drive_srf(q, curve)
    outside = f_hz < start or f_hz > stop                                 # this row's sweep cannot give it
    if outside or (srf is not None and srf <= rule.srf_margin * f_hz):
        return None
    v = q.at(curve, f_hz)
    return v if np.isfinite(v) else None


def _row(root: Path, project: Path, part: str, device, o: Observation, stratum: manifest.Stratum) -> Row:
    work = project / ".icopt" / "sims" / o.obs_id / "em" / device.id
    m = _measure(work, device, stratum)
    snp, columns, ts, topo_spec, ran_with, limit, q = m.snp, m.columns, m.ts, m.topology, m.ran_with, m.limit, m.q
    start, stop = m.start, m.stop
    values: dict[str, float | None] = {}
    for name, rule in stratum.quantities.items():
        if name in manifest.CURVES:
            if name not in q.curves:
                raise DatasetError(f"{part}: curve {name} needs two drives; this device has {len(topo_spec.drives)}")
            for f in rule.anchors_ghz:
                values[f"{name}@{f:g}"] = _anchor_value(q, name, rule, f * 1e9, start, stop)
        elif name in manifest.PEAKS and rule.band_ghz is not None:
            band = rule.band_ghz * 1e9
            if stop < band * (1 - 1e-9):
                raise DatasetError(f"{part}: {name} band is {rule.band_ghz:g} GHz but the sweep stops at {stop / 1e9:g} GHz")
            values[name] = _peak(q.freqs, q.curves[name.split("_")[0]], band, q.scalars.get("SRF"))
        else:
            if name not in q.scalars:
                raise DatasetError(f"{part}: {name} needs two drives; this device has {len(topo_spec.drives)}")
            values[name] = q.scalars[name]
    stored_path = project / ".icopt" / "sims" / o.obs_id / device.id / "nominal" / "quantities.json"
    stored_match = None
    if stored_path.is_file():
        stored = json.loads(stored_path.read_text(encoding="utf-8"))
        ran = q if limit == ran_with else measure.quantities(
            ts.freqs, ts.s, measure.Topology.from_labels(topo_spec.drives, topo_spec.grounded, columns, low_freq_max_hz=ran_with), z0=ts.z0)
        stored_match = all(stored.get(k) == ran.scalars.get(k) for k in UNBANDED if k in stored or k in ran.scalars)
    return Row(part, o.obs_id, {d: float(o.params[d]) for d in stratum.dims}, values, stop, len(ts.freqs), ts.n_ports,
               float(np.linalg.svd(ts.s, compute_uv=False).max()), stored_match, str(snp.relative_to(root)))


def _peak(freqs: np.ndarray, curve: np.ndarray, band_hz: float, srf_hz: float | None) -> float | None:
    """The largest finite value of a Q curve in (0, band], below the system SRF. Above the lowest resonance a coupled
    pair's Q curve can rise again towards the band edge (a multi-turn secondary resonates inside the sweep), which is
    not the device's quality factor; an inductor's peak always lies below its SRF, so for one drive nothing changes."""
    mask = (freqs > 0) & (freqs <= band_hz * (1 + 1e-12)) & np.isfinite(curve)
    if srf_hz is not None:
        mask &= freqs < srf_hz
    return float(np.max(curve[mask])) if mask.any() else None


def _drive_srf(q: measure.Quantities, curve: str) -> float | None:
    """The resonance that limits a curve: the system SRF. For one drive that is its own; for a coupled pair the
    other winding's resonance reflects into this drive's impedance too, so every curve stops below the lowest."""
    return q.scalars.get("SRF")


def _cache_key(stratum: manifest.Stratum, parts: list[_Part]) -> str:
    """What the dataset is made of, not how its rows are stamped: the stratum's definition (a pinned generation counts
    through the rows it keeps; the quantities' confidence ceilings and the stratum's note do not count), the measure code, and per part its
    device (id, ports, topology: where the sNp is and how it is measured), the rows it keeps (obs id, params, status)
    and what it leaves out. A restamp that keeps the same rows in the same generation (``ic-opt migrate-store``) keeps
    the key, and with it the calibration and model caches."""
    h = hashlib.sha256()
    h.update(f"v{DATASET_VERSION}".encode())
    h.update(stratum.model_dump_json(exclude={"note": True, "parts": {"__all__": {"pipeline_fingerprint"}},
                                              "quantities": {"__all__": {"rel_sigma_max"}}}).encode())
    h.update(hashlib.sha256(inspect.getsource(measure).encode()).digest())
    for p in parts:
        content = {"store": p.store, "device": p.device.model_dump(mode="json", include={"id", "ports", "topology"}),
                   "rows": [[o.obs_id, o.params, o.status] for o in p.kept], "excluded": dict(p.excluded)}
        h.update(json.dumps(content, sort_keys=True, separators=(",", ":")).encode())
    return h.hexdigest()[:20]


def _load(path: Path, stratum: manifest.Stratum, name: str, how: str, key: str, generations: dict[str, str]) -> Dataset:
    """A cached dataset; the generations come from the observations as they are now (a restamp keeps the key)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = [Row(**r) for r in data["rows"]]
    return Dataset(name, list(stratum.dims), stratum.nt_dim, stratum.columns(), rows, generations, data["excluded"], how, key)
