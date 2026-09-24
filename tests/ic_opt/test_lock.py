"""T15.4: the run-store lock on any controller (fcntl on Linux / macOS, msvcrt on Windows), and no ic_opt module that
imports a platform-only module when it is itself imported."""
from __future__ import annotations

import ast
import errno
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import ic_opt
from ic_opt._lock import LockHeld, exclusive_lock
from ic_opt.store import RunStore

PLATFORM_ONLY = {"fcntl", "grp", "pwd", "pty", "resource", "termios", "tty", "msvcrt", "winreg", "_winapi"}


class FakeMsvcrt:
    """``msvcrt.locking`` as Windows treats byte-range locks: one handle per range at a time; locking a held range, or
    unlocking a range the handle does not hold, fails with EACCES."""

    LK_UNLCK, LK_LOCK, LK_NBLCK, LK_RLCK, LK_NBRLCK = range(5)

    def __init__(self) -> None:
        self.held: dict[tuple[int, int, int, int], int] = {}             # (device, inode, offset, length) -> fd
        self.calls: list[tuple[int, int, int]] = []                      # (mode, offset, length)

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        st, offset = os.fstat(fd), os.lseek(fd, 0, os.SEEK_CUR)
        key = (st.st_dev, st.st_ino, offset, nbytes)
        self.calls.append((mode, offset, nbytes))
        if mode == self.LK_NBLCK and key not in self.held:
            self.held[key] = fd
        elif mode == self.LK_UNLCK and self.held.get(key) == fd:
            del self.held[key]
        else:
            raise PermissionError(errno.EACCES, "Permission denied")


def test_a_held_lock_refuses_every_other_handle_until_released(tmp_path):
    path = tmp_path / "lock"
    with exclusive_lock(path):
        with pytest.raises(LockHeld) as refused, exclusive_lock(path, what="project"):
            pass
        assert str(refused.value) == f"project is locked by another run: {path}" and isinstance(refused.value, RuntimeError)
    with pytest.raises(ValueError), exclusive_lock(path):
        raise ValueError("the body failed")
    with exclusive_lock(path):                                          # released on exit, with or without an error
        pass


def test_another_process_is_refused_while_the_lock_is_held(tmp_path):
    """The operating system's lock, not bookkeeping in this process: a second interpreter tries the same file."""
    path = tmp_path / "lock"
    probe = ("import sys\nfrom ic_opt._lock import LockHeld, exclusive_lock\ntry:\n    with exclusive_lock(sys.argv[1]):\n"
             "        print('acquired')\nexcept LockHeld as exc:\n    print(exc)\n")
    env = {**os.environ, "PYTHONPATH": str(Path(ic_opt.__file__).parents[1])}   # this ic_opt, whatever is installed

    def other_process() -> str:
        done = subprocess.run([sys.executable, "-c", probe, str(path)], capture_output=True, text=True, env=env, timeout=120, check=True)
        return done.stdout.strip()

    with exclusive_lock(path):
        assert other_process() == f"file is locked by another run: {path}"
    assert other_process() == "acquired"


def test_without_fcntl_the_lock_is_taken_with_msvcrt(tmp_path, monkeypatch):
    """What a Windows controller does: ``import fcntl`` fails, msvcrt locks the lock file's first byte."""
    fake = FakeMsvcrt()
    monkeypatch.setitem(sys.modules, "fcntl", None)                     # `import fcntl` raises ImportError, as on Windows
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    path = tmp_path / "lock"
    with exclusive_lock(path), pytest.raises(LockHeld, match="locked by another run"), exclusive_lock(path):
        pass
    assert fake.held == {} and fake.calls == [(fake.LK_NBLCK, 0, 1), (fake.LK_NBLCK, 0, 1), (fake.LK_UNLCK, 0, 1)]
    project = tmp_path / "project"
    with RunStore(project).lock(), pytest.raises(RuntimeError, match="locked"), RunStore(project).lock():
        pass
    with RunStore(project).lock():
        pass
    assert fake.held == {}


def test_without_fcntl_or_msvcrt_the_lock_says_so(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "fcntl", None)
    monkeypatch.setitem(sys.modules, "msvcrt", None)
    with pytest.raises(OSError, match="neither fcntl nor msvcrt"), exclusive_lock(tmp_path / "lock"):
        pass


def module_level_imports(tree: ast.Module) -> Iterator[str]:
    """Top-level names of the modules imported outside any function body: what importing the module itself pulls in."""
    stack: list[ast.AST] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        if isinstance(node, ast.Import):
            yield from (alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0]
        stack.extend(ast.iter_child_nodes(node))


def test_no_ic_opt_module_imports_a_platform_only_module_when_imported():
    """fcntl, pwd, resource ... exist only on POSIX, msvcrt and winreg only on Windows: imported at module level, any of
    them would stop ``ic-opt`` from starting on the other platforms (store.py's ``import fcntl`` did, on Windows)."""
    src = Path(ic_opt.__file__).parent
    found = sorted(f"{path.relative_to(src).as_posix()}: {name}" for path in src.rglob("*.py")
                   for name in module_level_imports(ast.parse(path.read_text(encoding="utf-8"))) if name in PLATFORM_ONLY)
    assert found == []
    probe = ast.parse("import os\nif os.name != 'nt':\n    import fcntl\ndef lock():\n    import msvcrt\n")
    assert set(module_level_imports(probe)) == {"os", "fcntl"}                   # nested blocks count, function bodies do not
