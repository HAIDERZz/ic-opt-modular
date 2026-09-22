"""Executor: the one seam between the workflow and the machine that runs EDA tools.

Five operations — ``run``, ``put``, ``get``, ``exists``, ``scratch`` — and one
rule inherited from ADR-0001: a remote path is only ever touched through
these operations, a transport failure is an error (never "file missing"),
and nothing falls back to a same-named local path.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    argv: list[str]
    seconds: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class ExecutorError(RuntimeError):
    """A command or transfer could not be carried out."""


class TransportError(ExecutorError):
    """The transport itself (SSH, scp) failed; the remote state is unknown."""


class CommandUnavailable(ExecutorError):
    """The remote shell could not find or invoke the executable (126/127)."""


class CommandTimeout(ExecutorError):
    """A command or transfer exceeded its deadline."""


def shell_program(command: str, *, cwd: str | None = None, cshrc: str | None = None) -> list[str]:
    """Turn a command line into an argv that runs it in the right environment.

    With ``cshrc`` the Cadence environment is sourced in ``csh``; otherwise
    POSIX ``sh`` runs the command. ``cwd`` is entered inside that shell so
    it works identically for local and SSH execution.
    """
    if cshrc:
        script = f"source {shlex.quote(cshrc)}; "
        script += f"cd {shlex.quote(cwd)}; " if cwd else ""
        return ["csh", "-fc", script + command]
    script = f"cd {shlex.quote(cwd)} && " if cwd else ""
    return ["/bin/sh", "-c", script + command]


class Executor(Protocol):
    host: str

    def run(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout_s: int | None = None,
        cshrc: str | None = None,
    ) -> CommandResult: ...

    def put(self, local: Path, remote: str) -> None: ...

    def get(self, remote: str, local: Path, *, dereference: bool = False) -> None: ...

    def exists(self, remote: str) -> bool: ...

    def scratch(self, key: str) -> str: ...
