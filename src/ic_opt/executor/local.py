"""LocalExecutor: run on this machine; put/get are plain copies."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from ic_opt.executor.base import CommandResult, CommandTimeout, shell_program


class LocalExecutor:
    host = "local"

    def __init__(self, scratch_root: Path) -> None:
        self.scratch_root = Path(scratch_root)

    def run(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout_s: int | None = None,
        cshrc: str | None = None,
    ) -> CommandResult:
        argv = shell_program(command, cwd=cwd, cshrc=cshrc)
        started = time.monotonic()
        try:
            done = subprocess.run(argv, text=True, capture_output=True, timeout=timeout_s, check=False)
        except subprocess.TimeoutExpired as exc:
            raise CommandTimeout(f"timed out after {timeout_s}s: {command}") from exc
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
