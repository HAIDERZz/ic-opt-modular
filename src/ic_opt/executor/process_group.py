"""Commands in a process group of their own: a deadline or an interrupt ends the whole tree, not its first process.

Both executors start their commands here: ``LocalExecutor`` its ``sh`` / ``csh``, and with it Spectre, OCEAN, EMX and
whatever they spawn; ``SshExecutor`` the OpenSSH client and anything it starts (a ProxyCommand). A command starts in a
new session on POSIX (its own process group, no controlling terminal) and in a new process group on Windows. When its
deadline passes, or the thread waiting for it is interrupted, the whole group is killed and the first process reaped
before the exception goes on. ``subprocess.run`` kills the first process only: a shell's Spectre then ran on, holding
the cores, memory and license the next job was sized to have (audit 2026-09-24, open question 4). Standard input is the
``input`` given or nothing, never the terminal.

A group of its own is also out of the terminal's reach: Ctrl-C and a hangup go to the terminal's foreground process
group, which the commands have left. ``forward_signals`` passes them on -- SIGINT and SIGHUP on POSIX, Ctrl-C as
Ctrl-Break on Windows -- to every command still running, then lets Python react as it did before, so an interrupted run
ends its commands as it did when they shared its group. The executors install it once, from the main thread (the only
thread that may set a signal handler). Every signal it passes on is counted (``interrupts``) before anything else
happens, so the threads of a run can tell at once that the user interrupted it and start nothing more.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading

_WINDOWS = os.name == "nt"
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)    # Windows' creation flag (its value, off Windows)
_CTRL_BREAK = getattr(signal, "CTRL_BREAK_EVENT", 1)                   # the one event Windows sends to a process group

_running: set[subprocess.Popen] = set()          # the commands started here and not yet reaped
_lock = threading.RLock()                         # re-entrant: a forwarded signal can arrive while the main thread holds it
_forwarded: set[int] = set()                      # the signals forward_signals has taken over
_interrupts = 0                                   # the signals passed on so far (only the main thread's handler writes it)


def interrupts() -> int:
    """How many interrupts and hangups ``forward_signals`` has passed on so far. A caller notes the count when its work
    starts; a different count later means the user interrupted it (the evaluation engine then stops its jobs)."""
    return _interrupts


def run(argv: list[str], *, timeout: float | None = None, input: str | bytes | None = None,
        encoding: str | None = None, errors: str | None = None) -> subprocess.CompletedProcess:
    """``subprocess.run(argv, input=input, capture_output=True, timeout=timeout, check=False)`` with the command in a
    process group of its own and ``input`` (or nothing) on its standard input. A ``subprocess.TimeoutExpired`` -- and any
    other exception or interrupt while waiting -- comes after the whole group was killed."""
    group = {"creationflags": _NEW_GROUP} if _WINDOWS else {"start_new_session": True}
    with subprocess.Popen(argv, stdin=subprocess.DEVNULL if input is None else subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, encoding=encoding, errors=errors, **group) as process:
        try:                                     # from the first moment the command exists: an interrupt ends its group
            with _lock:
                _running.add(process)
            stdout, stderr = process.communicate(input, timeout=timeout)
        except BaseException:
            kill_group(process)
            raise
        finally:
            with _lock:
                _running.discard(process)
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def kill_group(process: subprocess.Popen) -> None:
    """End ``process`` and every process of its group: SIGKILL to the group on POSIX; on Windows Ctrl-Break to the group,
    then the first process terminated. Best-effort: a group that has already ended is not an error. ``run`` reaps it."""
    try:
        if _WINDOWS:
            os.kill(process.pid, _CTRL_BREAK)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        process.kill()
    except OSError:
        pass


def signal_groups(signum: int) -> None:
    """Send ``signum`` to the group of every command still running (on Windows Ctrl-Break, whatever ``signum``)."""
    with _lock:
        running = list(_running)
    for process in running:
        try:
            if _WINDOWS:
                os.kill(process.pid, _CTRL_BREAK)
            else:
                os.killpg(process.pid, signum)
        except OSError:
            pass


def forward_signals() -> None:
    """Pass the terminal's interrupt and hangup on to the running commands (module docstring). Installed once per process,
    and only from the main thread: elsewhere it does nothing. A signal Python ignores stays ignored, as the commands
    inherited it."""
    if threading.current_thread() is not threading.main_thread():
        return
    with _lock:
        for name in ("SIGINT",) if _WINDOWS else ("SIGINT", "SIGHUP"):
            signum = getattr(signal, name)
            previous = signal.getsignal(signum)
            if signum in _forwarded or previous is None or previous == signal.SIG_IGN:
                continue
            signal.signal(signum, _forwarder(previous))
            _forwarded.add(signum)


def _forwarder(previous):
    """A handler that counts the signal (``interrupts``), signals the commands' groups, then does what ``previous`` did."""
    def forward(signum, frame):
        global _interrupts
        _interrupts += 1                             # first: a thread whose command dies of it already sees the count moved
        signal_groups(signum)
        if callable(previous):
            previous(signum, frame)                  # SIGINT: Python's KeyboardInterrupt
        else:                                        # SIG_DFL (a hangup ends the process): the same, now that the commands have it
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
    return forward
