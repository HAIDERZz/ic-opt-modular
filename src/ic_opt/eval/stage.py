"""Stage protocol: the pluggable unit inside the evaluation engine.

A stage turns one typed input into one typed output for one point (level
``"point"``) or one testbench × corner child (level ``"child"``). The engine
chains stages, gives each a :class:`StageContext`, and turns a
:class:`StageFailure` into ``status = "failed:<stage>"`` on the observation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from ic_opt.executor import Executor
    from ic_opt.spec import Spec
    from ic_opt.store import RunStore


class StageFailure(Exception):
    def __init__(self, *issues: str) -> None:
        super().__init__("; ".join(issues))
        self.issues = list(issues)
        self.stage = "stage"          # set by the engine to the name of the stage that raised


@dataclass
class StageContext:
    spec: Spec
    executor: Executor
    store: RunStore
    obs_id: str
    workdir: Path                    # local directory for this point/child under .icopt/sims/
    remote_dir: str                  # executor-side working directory (same path for LocalExecutor)
    testbench: str | None = None     # child-level stages only
    corner: str | None = None
    cshrc: str | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)   # command records for the child

    def record(self, label: str, result: Any) -> None:
        self.trace.append(
            {"label": label, "argv": result.argv, "returncode": result.returncode, "seconds": round(result.seconds, 3)}
        )


@dataclass(frozen=True)
class Resources:
    threads: int = 1
    memory_gb: float = 0.0


class Stage(Protocol):
    name: str
    level: Literal["point", "child"]
    resources: Resources

    def fingerprint(self, inp: Any) -> str | None:
        """Cache key for this input, or None when the stage must always run."""
        ...

    def run(self, inp: Any, ctx: StageContext) -> Any: ...


def pipeline_fingerprint(stages: list[Stage]) -> str:
    import hashlib

    return hashlib.sha256("|".join(s.name for s in stages).encode()).hexdigest()[:16]
