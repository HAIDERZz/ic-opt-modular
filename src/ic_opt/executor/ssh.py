"""SshExecutor: OpenSSH profile transport with fail-closed semantics.

Exit-code contract (from the legacy RemoteSshRunner, ADR-0001):
- ``255`` is an SSH transport failure -> :class:`TransportError`
- ``126`` / ``127`` mean the executable is unusable -> :class:`CommandUnavailable`
- ``exists`` accepts only ``0`` / ``1`` from ``test -e``; anything else raises

Files are uploaded to a temporary name and ``mv``-ed into place so a partial
transfer never masquerades as a complete file. Directories move as tar streams.

The controller may be Linux, macOS or Windows 10+: it needs the OpenSSH client
(``ssh``, ``scp``) and ``tar`` (which packs and unpacks the directory streams
locally), and all three platforms have them. Everything past ``ssh`` runs on
the Linux host under its ``/bin/sh`` or ``csh``; remote paths are POSIX strings
(``PurePosixPath``), never a local ``Path``, and remote output is decoded as
UTF-8 whatever the controller's locale.

A timeout ends the remote command too, not only the local client. A command
run with a timeout starts on the host as the leader of a session of its own
(``setsid``) after writing its process-group id into its working directory (the
scratch root without one), and deletes that record when it ends. When the
timeout passes, the local ``ssh`` is killed with its process group
(``process_group``), then one more ``ssh HOST`` sends the remote group SIGTERM,
SIGKILL if it is still there after a grace, and the ``CommandTimeout`` says how
that went. Killing the client alone leaves the remote job running. A Ctrl-C
that ``process_group`` forwarded to the client (``interrupts`` moved while it
ran) ends the remote group the same way, and the error or result says so.
"""

from __future__ import annotations

import shlex
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path, PurePosixPath

from ic_opt.executor import process_group
from ic_opt.executor.base import (
    CommandResult,
    CommandTimeout,
    CommandUnavailable,
    ExecutorError,
    TransportError,
    shell_program,
)

Execute = Callable[..., subprocess.CompletedProcess]
TERM_GRACE_S = 3                  # seconds a timed-out remote group has between SIGTERM and SIGKILL


def _default_execute(argv: list[str], *, input: str | bytes | None = None, timeout: float | None = None,
                     encoding: str | None = None, errors: str | None = None, capture_output: bool = True) -> subprocess.CompletedProcess:
    """The OpenSSH client on the controller, in a process group of its own: a deadline ends the client and anything it
    started (a ProxyCommand). Output is always captured; standard input is ``input`` or nothing, never the terminal."""
    return process_group.run(argv, timeout=timeout, input=input, encoding=encoding, errors=errors)


def in_own_session(program: str, record: str, *, make_dir: bool = False) -> str:
    """The remote ``sh`` script that runs ``program`` (a shell command line) as the leader of a session of its own, once
    the leader has written its process-group id to ``record``; it waits for the command, deletes the record and exits
    with the command's status. ``setsid`` comes with every Linux (util-linux); a host without it runs the command as
    before, and a timeout can then end that process only. ``make_dir`` creates the record's directory first."""
    q = shlex.quote
    leader = f"{{ echo $$ > {q(record)}; }} 2>/dev/null; exec {program}"
    prepare = f"mkdir -p {q(str(PurePosixPath(record).parent))} 2>/dev/null; " if make_dir else ""
    return (f"{prepare}s=; command -v setsid >/dev/null 2>&1 && s=setsid; "
            f"$s /bin/sh -c {q(leader)}; status=$?; rm -f {q(record)}; exit $status")


def end_group_script(record: str) -> str:
    """The remote ``sh`` script for after a timeout: read the process-group id ``in_own_session`` recorded, delete the
    record, send the group SIGTERM and, if it is still there TERM_GRACE_S seconds on, SIGKILL. It prints one line."""
    return (f"pgid=$(cat {shlex.quote(record)} 2>/dev/null); rm -f {shlex.quote(record)}; "
            'if [ "$pgid" -gt 1 ] 2>/dev/null; then '
            'if kill -TERM -- "-$pgid" 2>/dev/null; then n=0; '
            f'while kill -0 -- "-$pgid" 2>/dev/null && [ $n -lt {TERM_GRACE_S} ]; do sleep 1; n=$((n+1)); done; '
            'if kill -KILL -- "-$pgid" 2>/dev/null; then echo "process group $pgid killed (SIGTERM, then SIGKILL)"; '
            'else echo "process group $pgid ended on SIGTERM"; fi; '
            'elif kill -TERM -- "$pgid" 2>/dev/null; then echo "process $pgid sent SIGTERM (no setsid there: its children may remain)"; '
            'else echo "process group $pgid had already ended"; fi; '
            'else echo "no process group recorded (the command may not have started)"; fi')


class SshExecutor:
    def __init__(
        self,
        profile: str,
        scratch_root: str,
        *,
        execute: Execute | None = None,
        transfer_timeout_s: int = 1800,
    ) -> None:
        if not profile.strip() or profile.startswith("-"):
            raise ValueError("ssh profile must be a plain host alias")
        self.profile = profile
        self.host = profile
        self._scratch_root = PurePosixPath(scratch_root)       # may start with "~": resolved on the remote, once
        self._scratch_lock = threading.Lock()
        self._execute = _default_execute if execute is None else execute
        if execute is None:
            process_group.forward_signals()      # Ctrl-C still reaches the ssh clients, which run in groups of their own
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
        program = " ".join(shlex.quote(p) for p in shell_program(command, cwd=cwd, cshrc=cshrc))
        if timeout_s is None:                    # nothing will cut it short, so nothing has to find it again
            return self._run_argv(self._ssh_argv("exec " + program), timeout_s=None, label=command)
        record = str(PurePosixPath(cwd or self.scratch_root) / f".ic-opt-pgid-{uuid.uuid4().hex[:12]}")
        script = in_own_session(program, record, make_dir=cwd is None)
        interrupts = process_group.interrupts()
        try:
            result = self._run_argv(self._ssh_argv("exec /bin/sh -c " + shlex.quote(script)), timeout_s=timeout_s, label=command)
        except CommandTimeout as exc:
            raise CommandTimeout(f"{exc}; {self._end_group(record)}") from exc
        except TransportError as exc:            # ssh exits 255 when a forwarded Ctrl-C reaches it ("Killed by signal 2.")
            if process_group.interrupts() == interrupts:
                raise
            raise TransportError(f"{exc}; interrupted: {self._end_group(record)}") from exc
        if result.returncode < 0 and process_group.interrupts() != interrupts:   # the client died of the signal itself
            return replace(result, stderr=f"{result.stderr}\ninterrupted: {self._end_group(record)}".lstrip())
        return result

    def _end_group(self, record: str) -> str:
        """After a timeout or an interrupt, the local client already gone: one ssh that ends the remote command's process
        group (``end_group_script``), best-effort. What happened, for the message."""
        try:
            done = self._sh(end_group_script(record), timeout_s=self.transfer_timeout_s, label=f"end the group recorded in {record}")
        except ExecutorError as exc:
            return f"its process group on {self.host} was not ended: {exc}"
        return f"on {self.host}: " + (done.stdout.strip() or done.stderr.strip() or f"the cleanup exited {done.returncode}")

    def _run_argv(
        self, argv: list[str], *, timeout_s: int | None, label: str, input_text: str | None = None
    ) -> CommandResult:
        started = time.monotonic()
        try:
            done = self._execute(argv, input=input_text, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout_s)
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

    def _sh(self, command: str, *, timeout_s: int | None = None, input_text: str | None = None,
            label: str | None = None) -> CommandResult:
        """Run a plain POSIX-sh command on the remote (used for transfers and probes); ``label`` names it in errors."""
        argv = self._ssh_argv(f"exec /bin/sh -c {shlex.quote(command)}")
        return self._run_argv(argv, timeout_s=timeout_s, label=label or command, input_text=input_text)

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
