"""Where a library keeps its cache files: datasets, calibrations and fitted models (``Library``, ``dataset.build``).

The files are keyed by content -- what a dataset is made of; the data, settings and code behind a calibration or a
model -- so one copy is as good as another and the directory is safe to delete. By default they live in the library's
own ``<root>/.cache/``. ``cache_dir`` puts new files in another directory. When the library's own directory cannot be
written (a library shared read-only, a ``.cache`` that another user owns), new files go to
``~/.cache/ic-opt/<key>/`` instead, ``<key>`` the first 16 hex digits of the SHA-256 of the resolved root path, and
``Cache.note`` says so: every library answer carries it in its ``notes``. Wherever new files go, the files already in
the library's own ``.cache`` are still read, so the reader of a shared library loads the models its owner fitted
instead of fitting them again.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

OWN = ".cache"                                        # the library's own cache directory, under its root
FALLBACK = (".cache", "ic-opt")                       # under the user's home directory: ~/.cache/ic-opt/<key>/
KEY_DIGITS = 16                                       # of the SHA-256 of the resolved root path


@dataclass(frozen=True)
class Cache:
    """A library's cache: ``directory`` takes the new files and is read first; ``also`` is read after it, never written."""

    directory: Path
    also: tuple[Path, ...] = ()
    note: str | None = None                          # why new files do not go to the library's own .cache (the fallback)

    def find(self, name: str) -> Path | None:
        """The cache file ``name`` where it exists, the directory first; None when it exists nowhere."""
        return next((path for path in (d / name for d in (self.directory, *self.also)) if path.is_file()), None)

    def target(self, name: str) -> Path:
        """Where to write the cache file ``name``: in the directory, created if missing."""
        self.directory.mkdir(parents=True, exist_ok=True)
        return self.directory / name


def locate(root: str | os.PathLike[str], cache_dir: str | os.PathLike[str] | None = None) -> Cache:
    """The cache of the library at ``root``: ``cache_dir`` when given; else the library's own ``.cache`` when new files can
    be created there; else ``fallback(root)``, with the note that says so."""
    own = Path(root) / OWN
    if cache_dir is not None:
        directory = Path(cache_dir).expanduser().resolve()
        return Cache(directory, () if same(directory, own) else (own,))
    if writable(own):
        return Cache(own)
    directory = fallback(root)
    return Cache(directory, (own,), note=f"the library's cache directory {own} cannot be written: datasets, calibrations and "
                                         f"models are cached in {directory} (files already in {own} are still read)")


def fallback(root: str | os.PathLike[str]) -> Path:
    """``~/.cache/ic-opt/<key>``, ``<key>`` the first 16 hex digits of the SHA-256 of the resolved root path: one directory
    per library and user."""
    key = hashlib.sha256(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:KEY_DIGITS]
    return Path.home().joinpath(*FALLBACK, key)


def writable(directory: Path) -> bool:
    """Whether new files can be created in ``directory`` (made if missing, its parent must exist): tried, not read off the
    permission bits, which neither a read-only mount nor a Windows access list shows reliably."""
    try:
        directory.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".writable-", suffix=".tmp"):
            pass
    except OSError:
        return False
    return True


def same(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> bool:
    """Whether two paths name the same directory (after ``~`` and symlinks)."""
    return Path(a).expanduser().resolve() == Path(b).expanduser().resolve()
