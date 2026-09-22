"""SshExecutor: OpenSSH profile transport with fail-closed semantics.

Exit-code contract (from the legacy RemoteSshRunner, ADR-0001):
- ``255`` is an SSH transport failure -> :class:`TransportError`
- ``126`` / ``127`` mean the executable is unusable -> :class:`CommandUnavailable`
- ``exists`` accepts only ``0`` / ``1`` from ``test -e``; anything else raises

Files are uploaded to a temporary name and ``mv``-ed into place so a partial
transfer never masquerades as a complete file. Directories move as tar streams.
"""

from __future__ import annotations

import shlex
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from ic_opt.executor.base import (
    CommandResult,
    CommandTimeout,
    CommandUnavailable,
    ExecutorError,
    TransportError,
    shell_program,
)

Execute = Callable[..., subprocess.CompletedProcess]


def _default_execute(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(argv, check=False, **kwargs)


class SshExecutor:
    def __init__(
        self,
        profile: str,
        scratch_root: str,
        *,
        execute: Execute = _default_execute,
        transfer_timeout_s: int = 1800,
    ) -> None:
        if not profile.strip() or profile.startswith("-"):
            raise ValueError("ssh profile must be a plain host alias")
        self.profile = profile
        self.host = profile
        self._scratch_root = PurePosixPath(scratch_root)       # may start with "~": resolved on the remote, once
        self._scratch_lock = threading.Lock()
        self._execute = execute
        self.transfer_timeout_s = transfer_timeout_s

    # -- commands ----------------------------------------------------------

    def run(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout_s: int | None = None,
        cshrc: str | None = None,
    ) -> CommandResult:
        program = shell_program(command, cwd=cwd, cshrc=cshrc)
        argv = self._ssh_argv("exec " + " ".join(shlex.quote(p) for p in program))
        return self._run_argv(argv, timeout_s=timeout_s, label=command)

    def _run_argv(
        self, argv: list[str], *, timeout_s: int | None, label: str, input_text: str | None = None
    ) -> CommandResult:
        started = time.monotonic()
        try:
            done = self._execute(argv, input=input_text, text=True, capture_output=True, timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise CommandTimeout(f"timed out after {timeout_s}s: {label}") from exc
        result = CommandResult(done.returncode, done.stdout or "", done.stderr or "", argv, time.monotonic() - started)
        if result.returncode == 255:
            raise TransportError(
                f'SSH transport failed for profile "{self.profile}" ({label}): {result.stderr.strip()}'
            )
        return result

    def _checked(self, result: CommandResult, label: str) -> CommandResult:
        if result.returncode in (126, 127):
            raise CommandUnavailable(f"{label}: return code {result.returncode}: {result.stderr.strip()}")
        if result.returncode != 0:
            raise ExecutorError(f"{label}: return code {result.returncode}: {result.stderr.strip()}")
        return result

    def _ssh_argv(self, remote_shell_command: str) -> list[str]:
        return ["ssh", "-o", "BatchMode=yes", self.profile, remote_shell_command]

    def _sh(self, command: str, *, timeout_s: int | None = None, input_text: str | None = None) -> CommandResult:
        """Run a plain POSIX-sh command on the remote (used for transfers and probes)."""
        argv = self._ssh_argv(f"exec /bin/sh -c {shlex.quote(command)}")
        return self._run_argv(argv, timeout_s=timeout_s, label=command, input_text=input_text)

    # -- probes and directories ---------------------------------------------

    def exists(self, remote: str) -> bool:
        result = self._sh(f"test -e {shlex.quote(remote)}", timeout_s=self.transfer_timeout_s)
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        self._checked(result, f"existence probe for {remote}")
        raise AssertionError("unreachable")

    @property
    def scratch_root(self) -> PurePosixPath:
        """The remote scratch directory, with a leading ``~`` replaced by the remote ``$HOME`` (quoting would defeat tilde expansion)."""
        with self._scratch_lock:
            if str(self._scratch_root).startswith("~"):
                home = self._checked(self._sh('printf %s "$HOME"', timeout_s=self.transfer_timeout_s), "resolve $HOME").stdout.strip()
                self._scratch_root = PurePosixPath(home + str(self._scratch_root)[1:])
            return self._scratch_root

    def scratch(self, key: str) -> str:
        path = self.scratch_root / key
        self._checked(self._sh(f"mkdir -p {shlex.quote(str(path))}", timeout_s=self.transfer_timeout_s), f"mkdir {path}")
        return str(path)

    # -- transfers ---------------------------------------------------------

    def put(self, local: Path, remote: str) -> None:
        local = Path(local)
        if local.is_dir():
            self._put_tree(local, remote)
            return
        target = PurePosixPath(remote)
        temporary = target.parent / f".{target.name}.upload-{uuid.uuid4().hex}"
        argv = ["scp", "-o", "BatchMode=yes", str(local), f"{self.profile}:{shlex.quote(str(temporary))}"]
        try:
            self._checked(self._run_argv(argv, timeout_s=self.transfer_timeout_s, label=f"upload {local}"), f"upload {local}")
            self._checked(
                self._sh(f"mv -f -- {shlex.quote(str(temporary))} {shlex.quote(str(target))}", timeout_s=self.transfer_timeout_s),
                f"publish {target}",
            )
        except ExecutorError:
            self._sh(f"rm -f -- {shlex.quote(str(temporary))}", timeout_s=self.transfer_timeout_s)
            raise

    def get(self, remote: str, local: Path, *, dereference: bool = False) -> None:
        local = Path(local)
        if self._sh(f"test -d {shlex.quote(remote)}", timeout_s=self.transfer_timeout_s).returncode == 0:
            self._get_tree(remote, local, dereference=dereference)
            return
        local.parent.mkdir(parents=True, exist_ok=True)
        temporary = local.parent / f".{local.name}.download-{uuid.uuid4().hex}"
        argv = ["scp", "-o", "BatchMode=yes", f"{self.profile}:{shlex.quote(remote)}", str(temporary)]
        try:
            self._checked(self._run_argv(argv, timeout_s=self.transfer_timeout_s, label=f"download {remote}"), f"download {remote}")
            temporary.replace(local)
        finally:
            temporary.unlink(missing_ok=True)

    def _put_tree(self, local: Path, remote: str) -> None:
        pack = subprocess.run(["tar", "-C", str(local), "-cf", "-", "."], check=True, capture_output=True)
        unpack = f"mkdir -p {shlex.quote(remote)} && tar -C {shlex.quote(remote)} -xf -"
        argv = self._ssh_argv(f"exec /bin/sh -c {shlex.quote(unpack)}")
        done = self._execute(argv, input=pack.stdout, capture_output=True, timeout=self.transfer_timeout_s)
        result = CommandResult(done.returncode, "", (done.stderr or b"").decode(errors="replace"), argv, 0.0)
        if result.returncode == 255:
            raise TransportError(f"SSH transport failed while uploading tree to {remote}: {result.stderr.strip()}")
        self._checked(result, f"upload tree {local}")

    def _get_tree(self, remote: str, local: Path, *, dereference: bool = False) -> None:
        local.mkdir(parents=True, exist_ok=True)
        flags = "-chf" if dereference else "-cf"
        argv = self._ssh_argv(f"exec /bin/sh -c {shlex.quote(f'tar -C {shlex.quote(remote)} {flags} - .')}")
        done = self._execute(argv, capture_output=True, timeout=self.transfer_timeout_s)
        result = CommandResult(done.returncode, "", (done.stderr or b"").decode(errors="replace"), argv, 0.0)
        if result.returncode == 255:
            raise TransportError(f"SSH transport failed while downloading tree {remote}: {result.stderr.strip()}")
        self._checked(result, f"download tree {remote}")
        subprocess.run(["tar", "-C", str(local), "-xf", "-"], input=done.stdout, check=True)
