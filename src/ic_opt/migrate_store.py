"""``ic-opt migrate-store``: restamp a store's observations with identities that hold no machine facts (T15.2).

Before T15.2 an observation's ``spec_fingerprint`` hashed the whole spec -- parallel jobs, threads, timeouts, retention,
license check and budget included -- and the EMX stage's identity (part of ``pipeline_fingerprint``, a library part's
generation) and its cache key carried the process file's path. Now the spec fingerprint hashes ``Spec.problem()`` and
the EMX identity the process file's content, hashed on the simulation host.

``migrate`` takes a project or a library part store (``.icopt/observations.jsonl`` with ``spec.yaml`` or
``.icopt/spec.json``) and forms, from its spec as it is now, each old identity and its new one:

- ``spec_fingerprint``: rows stamped with the spec's legacy fingerprint get its problem fingerprint;
- ``pipeline_fingerprint``: rows stamped with the legacy fingerprint of an EM pipeline the spec implies (em_only, and
  em_circuit when it has testbenches) get that pipeline's new one, the process file hashed through the executor;
- EMX cache entries still under their legacy key move to the new one (each point's ``em/geometry.json`` holds the GDS
  digest and ports the key is formed from);
- a ``library.yaml`` above the store that pins a restamped generation is repointed.

Anything else stays as it is and is reported: a hash cannot tell which other spec or generation a row came from, and
guessing would merge problems or generations -- so migrate before editing the spec. Rows are restamped on the premise
that the process file has not changed since they were simulated. In a row only the two fingerprint values change (the
line is edited in place); the file is copied to ``observations.jsonl.bak-<UTC time>`` first and replaced atomically,
under the store's lock. Running it again changes nothing. A library's dataset, calibration and model caches survive:
the dataset key sees the rows a part keeps, not their stamps.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import re
import shutil
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from ic_opt.deck import Deck
from ic_opt.em import emx
from ic_opt.em.pcell.base import EmxPort
from ic_opt.eval.stage import Stage, pipeline_fingerprint
from ic_opt.executor import Executor, LocalExecutor
from ic_opt.library.manifest import MANIFEST
from ic_opt.spec import EmSettings, Spec
from ic_opt.stages.em_chain import Emx, em_circuit_pipeline, em_only_pipeline
from ic_opt.store import RunStore

OBSERVATIONS = "observations.jsonl"

# -- the identities versions before T15.2 wrote (frozen: they must keep reproducing those stamps) --------------------


def _legacy_physics(em: EmSettings) -> dict:
    """``emx.physics_key`` before T15.2: the settings minus threads, memory, timeout, verbosity and binary -- the process
    file's path stayed in."""
    return em.model_dump(mode="json", exclude={"threads", "memory_gb", "timeout_s", "verbose", "binary"})


def legacy_emx_identity(em: EmSettings) -> str:
    """``Emx.identity`` before T15.2: the physics settings with the process file's path."""
    return json.dumps(_legacy_physics(em), sort_keys=True, separators=(",", ":"))


def legacy_pipeline_fingerprint(stages: list[Stage]) -> str:
    """``pipeline_fingerprint`` before T15.2: each EMX stage identified by its process file's path."""
    parts = [f"{s.name}:{legacy_emx_identity(s.em) if isinstance(s, Emx) else getattr(s, 'identity', '')}" for s in stages]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def legacy_emx_cache_key(em: EmSettings, *, gds_sha256: str, ports: list[EmxPort], proc_sha256: str) -> str:
    """``emx.fingerprint`` before T15.2: the path-keyed physics settings plus the process file's digest."""
    payload = {"gds": gds_sha256, "ports": [p.argument() for p in ports], "em": _legacy_physics(em), "proc": proc_sha256}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


# -- the migration --------------------------------------------------------------------------------------------------


@dataclass
class Migration:
    """What ``migrate`` changed in one store (with ``dry_run``: would change)."""

    store: Path
    spec_file: Path
    spec_old: str                                            # the spec's legacy fingerprint
    spec_new: str                                            # its problem fingerprint
    pipelines: dict[str, tuple[str, str]]                    # EM pipeline -> (legacy, new) fingerprint
    dry_run: bool = False
    rows: int = 0
    restamped: int = 0                                       # rows with a fingerprint changed
    spec_rows: int = 0
    pipeline_rows: int = 0
    other_specs: collections.Counter = field(default_factory=collections.Counter)     # spec fingerprints left as they are
    before: collections.Counter = field(default_factory=collections.Counter)          # pipeline fingerprint -> rows
    after: collections.Counter = field(default_factory=collections.Counter)
    cache_moves: list[tuple[Path, Path]] = field(default_factory=list)                # (legacy entry, new entry)
    pins: list[tuple[Path, str, str, str]] = field(default_factory=list)              # (library.yaml, part store, old, new)
    backup: Path | None = None

    @property
    def changed(self) -> bool:
        return bool(self.restamped or self.cache_moves or self.pins)

    def __str__(self) -> str:
        lines = [f"{self.store} (spec {self.spec_file.relative_to(self.store)}): {self.rows} observations",
                 f"  spec fingerprint    {self.spec_old} -> {self.spec_new}: {self.spec_rows} rows restamped"]
        lines += [f"  pipeline {name:<11}{old} -> {new}" for name, (old, new) in self.pipelines.items()]
        if self.pipelines:
            lines.append(f"  pipeline rows       {self.pipeline_rows} restamped")
        if self.other_specs:
            lines.append("  left as they are    " + ", ".join(f"{n} rows of spec {fp}" for fp, n in self.other_specs.most_common())
                         + " (stamped under another spec, or other resources before T15.2: a hash cannot be matched)")
        lines.append(f"  generations before  {_counts(self.before)}")
        lines.append(f"  generations after   {_counts(self.after)}")
        if self.pipelines:
            lines.append(f"  emx cache           {len(self.cache_moves)} entries to their new key")
        lines += [f"  library pin         {manifest}: {part} {old} -> {new}" for manifest, part, old, new in self.pins]
        if self.dry_run or not self.changed:
            lines.append("  dry run: nothing written" if self.dry_run else "  nothing to change")
        else:
            done = [f"{self.restamped} rows restamped in {OBSERVATIONS} (backup {self.backup.name})" if self.backup else "",
                    f"{len(self.cache_moves)} emx cache entries moved" if self.cache_moves else "",
                    f"{len(self.pins)} library pins repointed (manifest backed up)" if self.pins else ""]
            lines.append("  done: " + ", ".join(d for d in done if d))
        return "\n".join(lines)


def migrate(project: str | Path, executor: Executor | None = None, *, dry_run: bool = False) -> Migration:
    """Restamp the store at ``project`` (see the module docstring). ``executor`` reaches the host that holds the spec's EMX
    process file (default: this machine); ``dry_run`` reports and writes nothing."""
    project = Path(project).resolve()
    path = project / ".icopt" / OBSERVATIONS
    if not path.is_file():
        raise FileNotFoundError(f"{project}: no .icopt/{OBSERVATIONS}")
    spec, spec_file = _spec(project)
    executor = executor or LocalExecutor(project / ".icopt" / "sims")
    pipelines = _em_pipelines(spec)
    report = Migration(project, spec_file, spec._legacy_fingerprint(), spec.fingerprint(),
                       {name: (legacy_pipeline_fingerprint(stages), pipeline_fingerprint(stages, executor))
                        for name, stages in pipelines.items()}, dry_run=dry_run)
    spec_map = {report.spec_old: report.spec_new}
    pipe_map = {old: new for old, new in report.pipelines.values() if old != new}
    with nullcontext() if dry_run else RunStore(project).lock():
        lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)      # bytes: line endings stay as written
        rows = [_parse(line, number, path) for number, line in enumerate(lines, 1)]
        updates = [_updates(row, spec_map, pipe_map) if row is not None else {} for row in rows]
        for row, update in zip(rows, updates):
            if row is not None:
                _count(report, row, update)
        report.cache_moves = _cache_moves(project, [s for s in pipelines.get("em_only", []) if isinstance(s, Emx)])
        report.pins = _pins(project, pipe_map)
        if not dry_run:
            if report.restamped:
                report.backup = _backup(path)
                _replace(path, "".join(_edit(line, row, update) if update else line for line, row, update in zip(lines, rows, updates)))
            for old, new in report.cache_moves:
                old.rename(new)
            _repin(report.pins)
    return report


def _spec(project: Path) -> tuple[Spec, Path]:
    """The store's spec, with the library dataset's precedence: ``spec.yaml`` (a project), else ``.icopt/spec.json`` (a part)."""
    for path in (project / "spec.yaml", project / ".icopt" / "spec.json"):
        if path.is_file():
            return Spec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))), path
    raise FileNotFoundError(f"{project}: no spec.yaml or .icopt/spec.json")


def _em_pipelines(spec: Spec) -> dict[str, list[Stage]]:
    """The pipelines this spec implies whose identity T15.2 changed: em_only, and em_circuit when it has testbenches."""
    if spec.em is None or not spec.devices:
        return {}
    pipelines = {"em_only": em_only_pipeline(spec)}
    if spec.testbenches:
        pipelines["em_circuit"] = em_circuit_pipeline(spec, Deck())            # the deck is not part of the identity
    return pipelines


def _parse(line: str, number: int, path: Path) -> dict | None:
    if not line.strip():
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}:{number}: not an observation ({exc.msg}); nothing was changed") from exc


def _updates(row: dict, spec_map: dict[str, str], pipe_map: dict[str, str]) -> dict[str, str]:
    update = {}
    if row.get("spec_fingerprint") in spec_map:
        update["spec_fingerprint"] = spec_map[row["spec_fingerprint"]]
    if row.get("pipeline_fingerprint") in pipe_map:
        update["pipeline_fingerprint"] = pipe_map[row["pipeline_fingerprint"]]
    return update


def _count(report: Migration, row: dict, update: dict[str, str]) -> None:
    report.rows += 1
    report.restamped += bool(update)
    report.spec_rows += "spec_fingerprint" in update
    report.pipeline_rows += "pipeline_fingerprint" in update
    if row.get("spec_fingerprint") not in (report.spec_old, report.spec_new):
        report.other_specs[row.get("spec_fingerprint")] += 1
    report.before[row.get("pipeline_fingerprint")] += 1
    report.after[update.get("pipeline_fingerprint", row.get("pipeline_fingerprint"))] += 1


def _edit(line: str, row: dict, update: dict[str, str]) -> str:
    """``line`` with the values in ``update`` replaced where they stand; every other byte stays."""
    body, newline = (line[:-1], "\n") if line.endswith("\n") else (line, "")
    edited = body
    for key, value in update.items():
        pattern = re.compile(rf'("{key}"\s*:\s*){re.escape(json.dumps(row[key]))}')
        edited = pattern.sub(lambda m, v=value: m.group(1) + json.dumps(v), edited, count=1)
    if json.loads(edited) != {**row, **update}:          # the value is spelled some other way: write the row out plainly
        edited = json.dumps({**row, **update}, ensure_ascii=False, separators=(",", ":"))
    return edited + newline


def _cache_moves(project: Path, stages: list[Emx]) -> list[tuple[Path, Path]]:
    """(legacy entry, new entry) for the EMX cache entries still under their pre-T15.2 key; an entry whose new key is
    already taken stays where it is. A point directory without a readable geometry.json is skipped."""
    cache = project / ".icopt" / "cache"
    moves: dict[Path, Path] = {}
    for geometry_file in sorted((project / ".icopt" / "sims").glob("*/em/geometry.json")):
        try:
            devices = json.loads(geometry_file.read_text(encoding="utf-8"))
            keys = {s: _cache_key_inputs(devices[s.device], s.proc_sha256) for s in stages if s.device in devices}
        except (OSError, ValueError, KeyError, TypeError):
            continue
        for stage, key in keys.items():
            old = cache / stage.name / legacy_emx_cache_key(stage.em, **key)
            new = cache / stage.name / emx.fingerprint(stage.em, **key)
            if (old / ".complete").is_file() and not new.exists():
                moves[old] = new
    return list(moves.items())


def _cache_key_inputs(device: dict, proc_sha256: str) -> dict:
    """What ``Emx.fingerprint`` forms a key from, as the pcell stage wrote it to geometry.json."""
    references = {p["signal"]: p["reference"] for p in device["ports"]}
    return {"gds_sha256": device["gds_sha256"], "ports": emx.numbered_ports(device["snp_order"], references), "proc_sha256": proc_sha256}


def _pins(project: Path, pipe_map: dict[str, str]) -> list[tuple[Path, str, str, str]]:
    """(library.yaml, part store, old, new) for each library manifest above the store that pins it to a restamped generation."""
    found = []
    for root in project.parents:
        manifest = root / MANIFEST
        if not manifest.is_file():
            continue
        try:
            strata = yaml.safe_load(manifest.read_text(encoding="utf-8"))["strata"]
            parts = [p for s in strata.values() for p in s["parts"]]
            found += [(manifest, p["store"], p["pipeline_fingerprint"], pipe_map[p["pipeline_fingerprint"]]) for p in parts
                      if p.get("pipeline_fingerprint") in pipe_map and (root / p["store"]).resolve() == project]
        except (OSError, yaml.YAMLError, KeyError, TypeError, AttributeError):
            continue                                     # not a manifest the library could read either
    return found


def _repin(pins: list[tuple[Path, str, str, str]]) -> None:
    for manifest in dict.fromkeys(m for m, *_ in pins):
        text = manifest.read_bytes().decode("utf-8")
        for m, _part, old, new in pins:
            if m == manifest:
                text = text.replace(old, new)
        _backup(manifest)
        _replace(manifest, text)


def _backup(path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup, n = path.with_name(f"{path.name}.bak-{stamp}"), 1
    while backup.exists():
        backup, n = path.with_name(f"{path.name}.bak-{stamp}-{n}"), n + 1
    shutil.copy2(path, backup)
    return backup


def _replace(path: Path, text: str) -> None:
    """Write ``text`` to a sibling temporary file and rename it over ``path``: a reader sees the old file or the new one."""
    tmp = path.with_name(f".{path.name}.migrate-{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _counts(counter: collections.Counter) -> str:
    return ", ".join(f"{fp} x{n}" for fp, n in counter.most_common()) or "-"
