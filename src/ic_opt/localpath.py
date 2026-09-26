"""Local file trees taken literally: ``literal(path)`` names exactly that file, on a Windows controller too.

Every Cadence Maestro export carries ``amap/__dspf_information__.``, a file name that ends with a dot. Ordinary Win32
paths strip trailing dots and spaces from names, so on Windows that file, once the local ``tar`` has created it (it
works through extended-length paths itself), is out of reach of ``open``, ``os.stat``, ``shutil.copytree`` and
``shutil.rmtree`` on its ordinary path -- "No such file or directory" -- and ``rmtree(..., ignore_errors=True)``
silently leaves its tree behind. The first Windows acceptance (2026-09-26) died on it in ``Deck.save``. An
extended-length path (``\\\\?\\D:\\...``) reaches the file under its literal name.

Use ``literal`` for every ``shutil.copytree`` / ``shutil.rmtree`` / ``open`` / ``os.walk`` on a local tree that came
from a Maestro export or that will be packed for the host: ``Deck.save``, ``netlist.import``'s staging,
``render_netlist``, ``LocalExecutor``'s copies and ``SshExecutor``'s tree transfers (on Windows these pack and unpack
with ``tarfile``, through ``literal``). On Linux and macOS it returns the path as it is.
"""

from __future__ import annotations

import ntpath
import os

_PREFIX = "\\\\?\\"            # \\?\ : Win32 hands the rest to the file system as it is
_UNC_PREFIX = "\\\\?\\UNC\\"   # \\?\UNC\server\share\... for \\server\share\...


def literal(path: str | os.PathLike[str], *, windows: bool | None = None) -> str:
    """``path`` as a string that names exactly that file.

    On POSIX, the path as it is (``os.fspath``). On Windows (``windows`` overrides ``os.name == "nt"``, so tests can
    take this branch anywhere) the absolute path with the extended-length prefix, ``\\\\?\\D:\\...``, or
    ``\\\\?\\UNC\\server\\share\\...`` for a UNC path: Win32 hands it on without dropping a name's trailing dots or
    spaces. A path that already has the prefix comes back as it is. A relative path (``name``, ``\\name``, ``D:name``)
    is joined to the current directory (of its drive) first; only that directory goes through Win32, never one of the
    path's own names. ``.`` and ``..`` are resolved as text, since behind the prefix they would be names too.
    """
    text = os.fspath(path)
    if not (os.name == "nt" if windows is None else windows):
        return text
    text = text.replace("/", "\\")
    if text.startswith(_PREFIX):
        return text
    drive, rest = ntpath.splitdrive(text)
    if not (drive.startswith("\\\\") or (drive and rest.startswith("\\"))):      # relative: not UNC, no drive and root
        text = ntpath.join(ntpath.abspath(drive or os.curdir), text)
    text = ntpath.normpath(text)
    return _UNC_PREFIX + text[2:] if text.startswith("\\\\") else _PREFIX + text
