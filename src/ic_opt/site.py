"""Site limits the agent cannot change: ``~/.ic-opt/site.yaml``.

    max_threads: 128       # total simulator threads across concurrent jobs
    max_memory_gb: 128     # total memory across concurrent jobs
    cshrc: /path/to/cadence_env.csh   # optional default Cadence environment
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

SITE_FILE = Path("~/.ic-opt/site.yaml").expanduser()


@dataclass(frozen=True)
class Site:
    max_threads: int = 128
    max_memory_gb: float = 128.0
    cshrc: str | None = None

    def slots(self, threads_per_job: int, memory_gb_per_job: float = 0.0) -> int:
        """How many jobs may run concurrently within the envelope."""
        by_threads = self.max_threads // max(1, threads_per_job)
        by_memory = int(self.max_memory_gb // memory_gb_per_job) if memory_gb_per_job > 0 else by_threads
        return max(1, min(by_threads, by_memory))


def load(path: Path = SITE_FILE) -> Site:
    if not path.exists():
        return Site()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Site(**{k: v for k, v in data.items() if k in Site.__dataclass_fields__})
