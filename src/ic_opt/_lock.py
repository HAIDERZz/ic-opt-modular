"""An exclusive lock on a file, on any controller: Linux, macOS or Windows -- refused at once while another handle holds
it (``exclusive_lock``), or waited for (``waiting_lock``).

``fcntl.flock`` where ``fcntl`` exists (Linux, macOS), ``msvcrt.locking`` on Windows. Each module exists only on its
own platforms, so neither is imported at module level: the lock imports whichever is there when it is taken, and
importing ic_opt works everywhere. A lock belongs to its open handle: any other handle -- in another process or in
this one -- is refused until the holder lets go, and the operating system lets go when the holding process dies, so
a crashed run leaves no stale lock behind.
"""

from __future__ import annotations

import errno
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

POLL_S = 0.2                                         # how often waiting_lock tries again while another handle holds the lock


class LockHeld(RuntimeError):
    """Another handle holds the lock."""


@contextmanager
def exclusive_lock(path: str | os.PathLike[str], *, what: str = "file") -> Iterator[None]:
    """Hold an exclusive lock on ``path`` (created if missing, never truncated) for the ``with`` block, or raise
    ``LockHeld`` ("<what> is locked by another run: <path>") at once when another handle holds it."""
    path = Path(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        unlock = _try_lock(fd)
        if unlock is None:
            raise LockHeld(f"{what} is locked by another run: {path}")
        try:
            yield
        finally:
            unlock()
    finally:
        os.close(fd)


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
