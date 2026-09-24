"""Stage protocol: the pluggable unit inside the evaluation engine.

A stage turns one typed input into one typed output for one point (level
``"point"``) or one child (level ``"child"``). A child is a testbench × corner
(``unit = "testbench"``, the Spectre chain) or an EM device (``unit =
"device"``, measure / predict); one pipeline may carry both child chains, fed
by the same point-level output. The engine chains stages, gives each a
:class:`StageContext`, and turns a :class:`StageFailure` into
``status = "failed:<stage>"`` on the observation.

A point-level stage whose ``fingerprint(inp)`` is not None is cached by the
engine under ``.icopt/cache/<name>/<fingerprint>/`` through its ``save(out,
dir)`` / ``load(dir)`` pair.

A stage's ``identity`` (optional) names the settings that change its answer;
the pipeline fingerprint hashes the stages' names and identities, and an
observation is only reused under the same one. A stage whose identity lives on
the simulation host (EMX: the process file's content) also has
``resolve_identity(executor)``, which ``pipeline_fingerprint`` calls first.
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
    unit: str | None = None          # child-level stages only: testbench id or device id
    corner: str | None = None
    point: Any = None                # the Point being evaluated (child stages of EM pipelines render the circuit from it)
    cache: dict[str, str] = field(default_factory=dict)         # point-level stage name -> "hit" | "miss"
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
    # child-level stages also declare ``unit: Literal["testbench", "device"]`` (default "testbench")

    def fingerprint(self, inp: Any, ctx: StageContext) -> str | None:
        """Cache key for this input, or None when the stage must always run (may consult the executor, e.g. to hash a remote file)."""
        ...

    # cacheable point-level stages also implement:
    #   def save(self, out: Any, directory: Path) -> None      persist the output under ``directory``
    #   def load(self, directory: Path, inp: Any, ctx: StageContext) -> Any   rebuild the output from ``directory`` (+ the input)
    # optional: ``identity: str`` (what changes the answer, in the pipeline fingerprint) and, when that lives on the host,
    #   def resolve_identity(self, executor: Executor) -> None   fetch it once; ``identity`` is fixed from then on

    def run(self, inp: Any, ctx: StageContext) -> Any: ...


def pipeline_fingerprint(stages: list[Stage], executor: Executor | None = None) -> str:
    """Stage names plus each stage's ``identity`` (settings that change its answer, e.g. EMX physics + the process file's
    content). With ``executor``, stages whose identity lives on the simulation host resolve it there first, once."""
    import hashlib

    if executor is not None:
        for stage in stages:
            resolve = getattr(stage, "resolve_identity", None)
            if resolve is not None:
                resolve(executor)
    return hashlib.sha256("|".join(f"{s.name}:{getattr(s, 'identity', '')}" for s in stages).encode()).hexdigest()[:16]
