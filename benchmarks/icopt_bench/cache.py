"""An on-disk cache of one problem's children, keyed by its point's ``ic_opt.space.point_key``.

One JSON-lines file per problem (``<cache_dir>/<problem>.jsonl``), an entry per evaluated point. Several processes
may read and append at once (a sweep runs many seeds of the same problem in parallel): both operations take
``ic_opt._lock.waiting_lock`` on a lock file next to the cache file, rather than relying on ``O_APPEND`` alone --
``waiting_lock`` already exists, is cross-platform (``fcntl`` / ``msvcrt``), and serializing the rare read too avoids
a reader ever seeing a line half-written by a concurrent append (a single fixed-size ``O_APPEND`` write is atomic on
POSIX, but children as long as an EM device's issues list can pass ``PIPE_BUF``/4096 bytes, where that guarantee
stops holding). Only simulator-backed problems need this; the eight synthetic ones are cheap enough to recompute.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from ic_opt._lock import waiting_lock
from ic_opt.observation import ChildResult
from ic_opt.space import point_key
from icopt_bench.problem import Problem


class EvalCache:
    def __init__(self, problem_name: str, cache_dir: str | Path) -> None:
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{problem_name}.jsonl"
        self.lock_path = self.dir / f"{problem_name}.lock"

    def get(self, key: str) -> dict[str, ChildResult] | None:
        with waiting_lock(self.lock_path):
            if not self.path.exists():
                return None
            found: dict[str, ChildResult] | None = None
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row["key"] == key:                      # a later line (if any, harmless duplicate) wins
                    found = {unit: ChildResult.model_validate(c) for unit, c in row["children"].items()}
            return found

    def put(self, key: str, children: dict[str, ChildResult]) -> None:
        row = {"key": key, "children": {unit: c.model_dump() for unit, c in children.items()}}
        line = json.dumps(row, separators=(",", ":")) + "\n"
        with waiting_lock(self.lock_path), self.path.open("a", encoding="utf-8") as f:
            f.write(line)


def cached(problem: Problem, cache_dir: str | Path) -> Problem:
    """``problem``, with ``evaluate`` consulting an :class:`EvalCache` for this problem before it computes anything."""
    store = EvalCache(problem.name, cache_dir)
    original = problem.evaluate

    def evaluate(params: dict[str, str]) -> dict[str, ChildResult]:
        key = point_key(params)
        hit = store.get(key)
        if hit is not None:
            return hit
        children = original(params)
        store.put(key, children)
        return children

    return dataclasses.replace(problem, evaluate=evaluate)
