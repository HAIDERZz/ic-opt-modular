"""An exclusive lock on a file, on any controller: Linux, macOS or Windows -- refused at once while another handle holds
it (``exclusive_lock``), or waited for (``waiting_lock``).

``fcntl.flock`` where ``fcntl`` exists (Linux, macOS), ``msvcrt.locking`` on Windows. Each module exists only on its
own platforms, so neither is imported at module level: the lock imports whichever is there when it is taken, and
importing ic_opt works everywhere. A lock belongs to its open handle: any other handle -- in another process or in
this one -- is refused until the holder lets go, and the operating system lets go when the holding process dies, so
a crashed run leaves no stale lock behind.

A run's lock (``exclusive_lock(..., holder=True)``, the project lock of ``ic-opt run``, N-119) names its holder: while
it is held the file holds the holding process's id, which a refusal quotes, and the code running inside it may take
it again through ``reentrant_lock`` -- the blocks that take the project lock themselves -- without being refused by
it.
"""

from __future__ import annotations

import contextvars
import errno
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

POLL_S = 0.2                                         # how often waiting_lock tries again while another handle holds the lock
# The run locks held by the code running in this context (thread): resolved paths, set by exclusive_lock(holder=True).
_HELD: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar("ic_opt_held_locks", default=frozenset())


class LockHeld(RuntimeError):
    """Another handle holds the lock."""


@contextmanager
def exclusive_lock(path: str | os.PathLike[str], *, what: str = "file", holder: bool = False) -> Iterator[None]:
    """Hold an exclusive lock on ``path`` (created if missing; never truncated by a handle that does not hold it) for the
    ``with`` block, or raise ``LockHeld`` ("<what> is locked by another run: <path>", and `` (pid <n>)`` when the file
    names its holder) at once when another handle holds it.

    ``holder``: a run's lock. While it is held the file names this process (:func:`_name_holder`), and the code inside
    the ``with`` -- in this thread -- that asks for the same lock through :func:`reentrant_lock` is not refused by it."""
    path = Path(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        unlock = _try_lock(fd)
        if unlock is None:
            pid = _holder(fd)
            raise LockHeld(f"{what} is locked by another run: {path}" + (f" (pid {pid})" if pid else ""))
        try:
            if not holder:
                yield
                return
            _name_holder(fd, os.getpid())
            token = _HELD.set(_HELD.get() | {_key(path)})
            try:
                yield
            finally:
                _HELD.reset(token)
                _name_holder(fd, None)
        finally:
            unlock()
    finally:
        os.close(fd)


@contextmanager
def reentrant_lock(path: str | os.PathLike[str], *, what: str = "file") -> Iterator[bool]:
    """``exclusive_lock(path, what=what, holder=True)`` -- unless the code calling it runs inside such a lock on ``path``
    taken in this thread (the run that holds its project): then it holds nothing more. Yields whether it took the lock.
    Any other handle's lock refuses it as before: another process's, another thread's, one taken without ``holder``."""
    if _key(path) in _HELD.get():
        yield False
        return
    with exclusive_lock(path, what=what, holder=True):
        yield True


def _key(path: str | os.PathLike[str]) -> str:
    return str(Path(path).resolve())


def _name_holder(fd: int, pid: int | None) -> None:
    """Write ``pid`` into the lock file the holder's ``fd`` has open (None: empty it). It is written from the second byte
    on -- the first is a newline -- because on Windows the holder's lock on the first byte keeps every other handle
    from reading that byte. Best effort: a file that cannot be written leaves a refusal without the pid."""
    try:
        os.ftruncate(fd, 0)
        if pid is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, f"\n{pid}\n".encode("ascii"))
    except OSError:
        pass


def _holder(fd: int) -> int | None:
    """The process id the lock file names (:func:`_name_holder`), read from its second byte; None when it names none."""
    try:
        os.lseek(fd, 1, os.SEEK_SET)
        text = os.read(fd, 64).decode("ascii", errors="replace").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


@contextmanager
def waiting_lock(path: str | os.PathLike[str], *, poll_s: float = POLL_S) -> Iterator[bool]:
    """Hold an exclusive lock on ``path`` (created if missing, never truncated) for the ``with`` block, waiting as long as
    another handle holds it: tried again every ``poll_s`` seconds, so Ctrl-C ends the wait. Yields whether it had to wait.
    A holder that dies lets go with its process, so no wait outlives the work it waits for."""
    path = Path(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        waited = False
        while (unlock := _try_lock(fd)) is None:
            waited = True
            time.sleep(poll_s)
        try:
            yield waited
        finally:
            unlock()
    finally:
        os.close(fd)


def _try_lock(fd: int) -> Callable[[], None] | None:
    """Lock ``fd`` without waiting: the function that unlocks it, or None when another handle holds the lock."""
    try:
        import fcntl
    except ImportError:                                              # Windows
        return _try_lock_msvcrt(fd)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return None
    return lambda: fcntl.flock(fd, fcntl.LOCK_UN)


def _try_lock_msvcrt(fd: int) -> Callable[[], None] | None:
    try:
        import msvcrt
    except ImportError as exc:
        raise OSError("no file lock on this platform: neither fcntl nor msvcrt is available") from exc
    # msvcrt locks bytes from the current position: every holder locks the first byte (a lock may lie past the end of the file)
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        if exc.errno != errno.EACCES:                                # EACCES: another handle holds that byte
            raise
        return None

    def unlock() -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

    return unlock
