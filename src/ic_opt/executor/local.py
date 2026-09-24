"""LocalExecutor: run on this machine; put/get are plain copies.

This machine is then both the controller and the simulation host. Commands run under ``/bin/sh`` or ``csh``
(``shell_program``), so running them needs Linux or macOS; a Windows controller simulates on a Linux host through
``SshExecutor`` (``--ssh-profile``). ``put``, ``get``, ``exists`` and ``scratch`` are file operations and work anywhere.

Each command runs in a process group of its own (``process_group``): when its timeout passes, the whole group is
killed -- the shell and the Spectre, OCEAN or EMX it started, with their children -- before ``CommandTimeout`` is raised,
so no job outlives its deadline to hold cores the next one was sized to have.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from ic_opt.executor import process_group
from ic_opt.executor.base import CommandResult, CommandTimeout, ExecutorError, shell_program

_WINDOWS = os.name == "nt"                                           # no /bin/sh, no csh


class LocalExecutor:
    host = "local"

    def __init__(self, scratch_root: Path) -> None:
        self.scratch_root = Path(scratch_root)
        process_group.forward_signals()          # Ctrl-C still reaches the commands, which run in groups of their own

    def run(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout_s: int | None = None,
        cshrc: str | None = None,
    ) -> CommandResult:
        if _WINDOWS:
            raise ExecutorError(f"cannot run {command!r} on this machine: commands need /bin/sh or csh, which Windows does not have; "
                                "simulate on a Linux host with --ssh-profile")
        argv = shell_program(command, cwd=cwd, cshrc=cshrc)
        started = time.monotonic()
        try:
            done = process_group.run(argv, timeout=timeout_s, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired as exc:
            raise CommandTimeout(f"timed out after {timeout_s}s: {command} (its process group was killed)") from exc
        return CommandResult(done.returncode, done.stdout, done.stderr, argv, time.monotonic() - started)

    def put(self, local: Path, remote: str) -> None:
        _copy(Path(local), Path(remote))

    def get(self, remote: str, local: Path, *, dereference: bool = False) -> None:
        _copy(Path(remote), Path(local), dereference=dereference)

    def exists(self, remote: str) -> bool:
        return Path(remote).exists()

    def scratch(self, key: str) -> str:
        path = self.scratch_root / key
        path.mkdir(parents=True, exist_ok=True)
        return str(path)


def _copy(src: Path, dst: Path, *, dereference: bool = False) -> None:
    if src.resolve() == dst.resolve():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=not dereference)
    else:
        shutil.copy2(src, dst)
