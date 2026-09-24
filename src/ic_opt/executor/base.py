"""Executor: the one seam between the workflow and the machine that runs EDA tools.

Five operations — ``run``, ``put``, ``get``, ``exists``, ``scratch`` — and one
rule inherited from ADR-0001: a remote path is only ever touched through
these operations, a transport failure is an error (never "file missing"),
and nothing falls back to a same-named local path.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

CSH_HOOKS = (".csh", ".cshrc", ".tcsh", ".tcshrc")    # environment files csh sources; any other name is sourced by sh


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


def hook_shell(path: str) -> str:
    """The shell that sources an environment file: ``csh`` when its name ends in .csh, .cshrc, .tcsh or .tcshrc
    (``~/.cshrc`` included, any case), else ``sh``."""
    return "csh" if PurePosixPath(path).name.lower().endswith(CSH_HOOKS) else "sh"


def shell_program(command: str, *, cwd: str | None = None, cshrc: str | None = None) -> list[str]:
    """Turn a command line into an argv that runs it in the right environment.

    ``cshrc`` is the environment file on the simulation host. It keeps the name of the site.yaml key (and of
    ``--cshrc`` / ``IC_OPT_CADENCE_CSHRC``) but need not be a csh file: a csh file (``hook_shell``) is sourced in
    ``csh`` and the command runs there, any other file is sourced by POSIX ``sh`` (``. FILE``) and the command runs in
    that ``sh``.
    Without one POSIX ``sh`` runs the command. ``cwd`` is entered inside that shell so it works identically for local
    and SSH execution. Either shell is the simulation host's: ``SshExecutor`` hands this argv to ``ssh``, and only
    ``LocalExecutor`` (Linux / macOS) starts it on the controller.
    """
    if cshrc and hook_shell(cshrc) == "csh":
        script = f"source {shlex.quote(cshrc)}; "
        script += f"cd {shlex.quote(cwd)}; " if cwd else ""
        return ["csh", "-fc", script + command]
    # `.` looks a bare name up on PATH, csh's `source` in the working directory: name the file the way csh finds it.
    # The file's status does not gate the command (`;`): an environment script may well end on a failed test.
    script = f". {shlex.quote(cshrc if '/' in cshrc else './' + cshrc)}; " if cshrc else ""
    script += f"cd {shlex.quote(cwd)} && " if cwd else ""
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
