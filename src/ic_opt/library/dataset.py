"""A stratum's dataset: one row per usable observation, its query quantities re-measured from the sNp under one definition.

Rows come from the stratum's parts (run stores). Per part only ``ok`` observations of one generation
(pipeline fingerprint: geometry generation + EMX physics + process file) are used -- the pinned one or
the part's most common -- so generations never mix. Every quantity is recomputed from the stored sNp by
``ic_opt.em.measure``: peaks inside the manifest's band, curves at the anchor frequencies (unusable where
the resonance sits at or below ``srf_margin`` x f0), low-frequency scalars up to the stratum's
``low_freq_max_hz`` (else the part spec's), so parts swept to different frequencies answer with the same
definition; a row whose sweep cannot give a value (no sample in the low-frequency band, no resonance) has
None in that column only. Each row also carries the integrity evidence ``check`` reports: the largest
singular value of S over frequency (passivity) and whether the stored quantities.json reproduces under
the definition the run measured with (the part spec's).

Built datasets are cached in the library's cache (``cache.locate``: ``<library>/.cache/`` unless another
directory is given or that one cannot be written) keyed by what they are made of -- the rows each part keeps and
leaves out, the parts' devices, the quantity definitions and the measure code -- so a query does not re-read
thousands of sNp files. The key ignores how the rows are stamped: restamping their fingerprints
(``ic-opt migrate-store``) keeps it, and with it the calibration and model caches built on it. It ignores a
quantity's confidence ceiling (``rel_sigma_max``) too: that decides what an answer trusts, not what a row holds.
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
from ic_opt.library.cache import Cache, locate
from ic_opt.observation import Observation
from ic_opt.spec import Device, Spec

DATASET_VERSION = 3                                  # 2: anchored curves of a coupled pair stop below the system SRF; 3: so do peaks
UNBANDED = ("Lp_lf", "Lp_res", "SRF_p", "Ls_lf", "Ls_res", "SRF_s", "k_lf")     # compared with the stored quantities.json
_PORT = re.compile(r"^p(\d+)=([^:]+)(?::(.+))?$")


class DatasetError(ValueError):
    """The library cannot yield this stratum's dataset as declared (fail-closed)."""


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
    """Integrity evidence for a built dataset: duplicates, passivity, stored-value reproduction, sweep grids, port counts."""
    keys = [tuple(round(r.coords[d], 9) for d in ds.dims) for r in ds.rows]
    grids = collections.Counter((r.part, r.n_freq, round(r.stop_hz / 1e9, 6)) for r in ds.rows)
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


def _spec(project: Path) -> Spec:
    for path in (project / "spec.yaml", project / ".icopt" / "spec.json"):
        if path.is_file():
            import yaml
            return Spec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    raise DatasetError(f"{project}: no spec.yaml or .icopt/spec.json")


def _most_common(values) -> str | None:
    counts = collections.Counter(v for v in values if v)
    return counts.most_common(1)[0][0] if counts else None


def _snp_columns(work: Path, device) -> list[str]:
    """sNp column order: the ``-p pNN=SIGNAL:REF`` arguments EMX ran with (EMX sorts ports by name), else the device's ports."""
    cmd = work / "emx.cmd"
    if cmd.is_file():
        tokens = cmd.read_text(encoding="utf-8").split()
        ports = [m for t in tokens if (m := _PORT.match(t))]
        if ports:
            return [m.group(2) for m in sorted(ports, key=lambda m: int(m.group(1)))]
    return list(device.ports)


def _row(root: Path, project: Path, part: str, device, o: Observation, stratum: manifest.Stratum) -> Row:
    work = project / ".icopt" / "sims" / o.obs_id / "em" / device.id
    columns = _snp_columns(work, device)
    snp = work / f"{device.id}.s{len(columns)}p"
    ts = touchstone.read(snp)
    topo_spec = device.topology or device.default_topology()
    ran_with = topo_spec.low_freq_max_hz                                   # the run's own definition (quantities.json)
    limit = ran_with if stratum.low_freq_max_hz is None else stratum.low_freq_max_hz      # the manifest's wins
    topo = measure.Topology.from_labels(topo_spec.drives, topo_spec.grounded, columns, low_freq_max_hz=limit)
    q = measure.quantities(ts.freqs, ts.s, topo, z0=ts.z0)
    stop = float(ts.freqs[-1])
    values: dict[str, float | None] = {}
    for name, rule in stratum.quantities.items():
        if name in manifest.CURVES:
            if name not in q.curves:
                raise DatasetError(f"{part}: curve {name} needs two drives; this device has {len(topo.drives)}")
            srf = _drive_srf(q, name)
            for f in rule.anchors_ghz:
                f_hz = f * 1e9
                if f_hz > stop or (srf is not None and srf <= rule.srf_margin * f_hz):
                    values[f"{name}@{f:g}"] = None
                else:
                    v = q.at(name, f_hz)
                    values[f"{name}@{f:g}"] = v if np.isfinite(v) else None
        elif name in manifest.PEAKS and rule.band_ghz is not None:
            band = rule.band_ghz * 1e9
            if stop < band * (1 - 1e-9):
                raise DatasetError(f"{part}: {name} band is {rule.band_ghz:g} GHz but the sweep stops at {stop / 1e9:g} GHz")
            values[name] = _peak(q.freqs, q.curves[name.split("_")[0]], band, q.scalars.get("SRF"))
        else:
            if name not in q.scalars:
                raise DatasetError(f"{part}: {name} needs two drives; this device has {len(topo.drives)}")
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
    through the rows it keeps; the quantities' confidence ceilings do not count), the measure code, and per part its
    device (id, ports, topology: where the sNp is and how it is measured), the rows it keeps (obs id, params, status)
    and what it leaves out. A restamp that keeps the same rows in the same generation (``ic-opt migrate-store``) keeps
    the key, and with it the calibration and model caches."""
    h = hashlib.sha256()
    h.update(f"v{DATASET_VERSION}".encode())
    h.update(stratum.model_dump_json(exclude={"parts": {"__all__": {"pipeline_fingerprint"}},
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
