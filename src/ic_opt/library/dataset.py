"""A stratum's dataset: one row per usable observation, its query quantities re-measured from the sNp under one definition.

Rows come from the stratum's parts (run stores). Per part only ``ok`` observations of one generation
(pipeline fingerprint: geometry generation + EMX physics + process file) are used -- the pinned one or
the part's most common -- so generations never mix. Every quantity is recomputed from the stored sNp by
``ic_opt.em.measure``: peaks inside the manifest's band, curves at the anchor frequencies (unusable where
the resonance sits at or below ``srf_margin`` x f0), so parts swept to different frequencies answer with
the same definition. Each row also carries the integrity evidence ``check`` reports: the largest singular
value of S over frequency (passivity) and whether the stored quantities.json reproduces.

Built datasets are cached under ``<library>/.cache/`` keyed by the observation files, the quantity
definitions and the measure code, so a query does not re-read thousands of sNp files.
"""

from __future__ import annotations

import collections
import hashlib
import inspect
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from ic_opt.em import measure, touchstone
from ic_opt.library import manifest
from ic_opt.observation import Observation
from ic_opt.spec import Spec

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


def build(root: str | Path, name: str, *, library: manifest.Library | None = None, cache: bool = True) -> Dataset:
    """The dataset of stratum ``name`` of the library at ``root`` (cached unless ``cache=False``)."""
    root = Path(root)
    lib = library or manifest.load(root)
    if name not in lib.strata:
        raise DatasetError(f"no stratum {name!r} in {root / manifest.MANIFEST}; have {sorted(lib.strata)}")
    stratum = lib.strata[name]
    obs_files = []
    for part in stratum.parts:
        path = root / part.store / ".icopt" / "observations.jsonl"
        if not path.is_file():
            raise DatasetError(f"{name}: part {part.store} has no observations ({path})")
        obs_files.append(path)
    key = _cache_key(stratum, obs_files)
    cached = root / ".cache" / f"dataset-{name}-{key}.json"
    if cache and cached.is_file():
        return _load(cached, stratum, name, "hit", key)

    columns = stratum.columns()
    rows: list[Row] = []
    generations: dict[str, str] = {}
    excluded: collections.Counter[str] = collections.Counter()
    for part, path in zip(stratum.parts, obs_files):
        project = root / part.store
        spec = _spec(project)
        if len(spec.devices) != 1:
            raise DatasetError(f"{name}: part {part.store} has {len(spec.devices)} devices; a library store holds one")
        device = spec.devices[0]
        observations = [Observation.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        ok = [o for o in observations if o.status == "ok"]
        for o in observations:
            if o.status != "ok":
                excluded[f"status:{o.status}"] += 1
        generation = part.pipeline_fingerprint or _most_common(o.pipeline_fingerprint for o in ok)
        if generation is None:
            continue
        generations[part.store] = generation
        for o in ok:
            if o.pipeline_fingerprint != generation:
                excluded["other generation"] += 1
                continue
            missing = [d for d in stratum.dims if d not in o.params]
            if missing:
                raise DatasetError(f"{name}: {part.store}/{o.obs_id} lacks the dims {missing}")
            try:
                rows.append(_row(root, project, part.store, device, o, stratum))
            except (measure.MeasureError, touchstone.TouchstoneError, OSError) as exc:
                excluded[f"measure: {type(exc).__name__}"] += 1
    ds = Dataset(name, list(stratum.dims), stratum.nt_dim, columns, rows, generations, dict(excluded), "miss" if cache else "off", key)
    if cache:
        cached.parent.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": DATASET_VERSION, "generations": generations, "excluded": dict(excluded),
                                   "rows": [asdict(r) for r in rows]}), encoding="utf-8")
        tmp.replace(cached)
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
    topo = measure.Topology.from_labels(topo_spec.drives, topo_spec.grounded, columns)
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
        stored_match = all(stored.get(k) == q.scalars.get(k) for k in UNBANDED if k in stored or k in q.scalars)
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


def _cache_key(stratum: manifest.Stratum, obs_files: list[Path]) -> str:
    h = hashlib.sha256()
    h.update(f"v{DATASET_VERSION}".encode())
    h.update(stratum.model_dump_json().encode())
    h.update(hashlib.sha256(inspect.getsource(measure).encode()).digest())
    for path in obs_files:
        h.update(str(path).encode())
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()[:20]


def _load(path: Path, stratum: manifest.Stratum, name: str, how: str, key: str) -> Dataset:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = [Row(**r) for r in data["rows"]]
    return Dataset(name, list(stratum.dims), stratum.nt_dim, stratum.columns(), rows, data["generations"], data["excluded"], how, key)
