"""RunStore: everything a project accumulates lives under ``<project>/.icopt/``.

- ``observations.jsonl`` — the fact table, one Observation per line, append only
- ``steps.jsonl``        — one line per block call (name, args digest, outcome)
- ``decks/``, ``sims/``, ``cache/``, ``reports/`` — artifacts, all rebuildable
- ``lock``               — one writer per project
"""

from __future__ import annotations

import fcntl
import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ic_opt.observation import Observation, Observations


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


class RunStore:
    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.root = self.project_dir / ".icopt"
        for sub in ("decks", "sims", "cache", "reports"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self._observations: Observations | None = None

    # -- observations ------------------------------------------------------

    @property
    def observations_path(self) -> Path:
        return self.root / "observations.jsonl"

    def observations(self) -> Observations:
        """All observations in obs_id order (the file itself is in completion order: parallel points land as they finish)."""
        if self._observations is None:
            rows = Observations()
            if self.observations_path.exists():
                for line in self.observations_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        rows.append(Observation.model_validate_json(line))
            rows.sort(key=lambda o: o.obs_id)
            self._observations = rows
        return self._observations

    def append(self, observation: Observation) -> Observation:
        with self.observations_path.open("a", encoding="utf-8") as handle:
            handle.write(observation.model_dump_json() + "\n")
        rows = self.observations()
        rows.append(observation)
        rows.sort(key=lambda o: o.obs_id)
        return observation

    def next_obs_index(self) -> int:
        """The first free observation number: past every recorded observation and every ``sims/obs_*`` directory.

        Parallel points finish out of order, so a run interrupted mid-batch leaves holes; counting the observations
        would hand a new point the number, and the directory, of one that already finished.
        """
        names = [o.obs_id for o in self.observations()] + [p.name for p in (self.root / "sims").glob("obs_*")]
        return 1 + max((int(n[4:]) for n in names if n[4:].isdigit()), default=0)

    def next_obs_id(self) -> str:
        return f"obs_{self.next_obs_index():04d}"

    # -- directories -------------------------------------------------------

    def sim_dir(self, obs_id: str, testbench: str, corner: str | None) -> Path:
        path = self.root / "sims" / obs_id / testbench / (corner or "nominal")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def deck_dir(self, fingerprint: str) -> Path:
        return self.root / "decks" / fingerprint

    def cache_dir(self, stage: str, fingerprint: str) -> Path:
        path = self.root / "cache" / stage / fingerprint
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def reports_dir(self) -> Path:
        return self.root / "reports"

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.project_dir).as_posix()

    # -- steps log ---------------------------------------------------------

    def log_step(self, name: str, status: str, **fields: Any) -> None:
        row = {"at": utc_now(), "step": name, "status": status, **fields}
        with (self.root / "steps.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    # -- lock --------------------------------------------------------------

    @contextmanager
    def lock(self) -> Iterator[None]:
        with (self.root / "lock").open("w") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(f"project is locked by another run: {self.root / 'lock'}") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
