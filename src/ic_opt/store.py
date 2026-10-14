"""RunStore: everything a project accumulates lives under ``<project>/.icopt/``.

- ``observations.jsonl`` — the fact table, one Observation per line, append only
- ``steps.jsonl``        — one line per block call (name, args digest, outcome)
- ``steps.json``         — per optimize step, the initial design's size its first call recorded (N-96)
- ``decks/``, ``sims/``, ``cache/``, ``reports/`` — artifacts, all rebuildable
- ``run.lock``           — one run per project (:meth:`RunStore.run_lock`): ``ic-opt run`` holds it from before its first
                           write to its end, and while it holds it the file names the run's process id (N-119)
- ``lock``               — one writer at a time: a batch of sim.evaluate, an advice (``fcntl`` or ``msvcrt``: the controller may be Windows)
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ic_opt._lock import exclusive_lock, reentrant_lock
from ic_opt.observation import Observation, Observations

_WINDOWS = os.name == "nt"                                           # reserves ':' in file names
RUN_LOCK = "run.lock"      # the project lock: a run's, whole (RunStore.run_lock); the store's own lock is per batch


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
        """``cache/<stage>/<fingerprint>``; on Windows, which reserves ':' in file names, an EMX stage's ``emx:<device>``
        is ``emx-<device>`` (no built-in stage name or device id contains '-')."""
        path = self.root / "cache" / (stage.replace(":", "-") if _WINDOWS else stage) / fingerprint
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

    def lock(self) -> AbstractContextManager[None]:
        """One writer per project: ``with store.lock():`` holds it, or raises ``LockHeld`` (a RuntimeError, "project is
        locked by another run: ...") at once while another run holds it."""
        return exclusive_lock(self.root / "lock", what="project")

    def run_lock(self) -> AbstractContextManager[bool]:
        """The project lock, ``.icopt/run.lock``: one run per project (N-119). ``ic-opt run`` takes it outright
        (``exclusive_lock(..., holder=True)``) before anything writes to the store -- before the doctor's step row and the
        netlist import -- and holds it to the end; a second run is refused at once in one line, naming the first one's
        pid. The blocks that take it themselves -- ``opt.optimize`` for its loop, ``opt.advise`` / ``opt.revoke_advice``,
        ``ic-opt advise`` -- take it here: inside the run that holds it this holds nothing more (``reentrant_lock``), so
        a recipe never refuses itself; called alone (``ic-opt call``, a script) it is taken as it always was. While
        another process holds it, ``LockHeld`` names that process's pid."""
        return reentrant_lock(self.root / RUN_LOCK, what="project")
