"""Machine limits the agent cannot change: ``~/.ic-opt/site.yaml``, one entry per host.

``local`` is the machine running ic-opt (also the simulation host when no ``--ssh-profile`` is
given); every other entry is named like the ``--ssh-profile`` it describes. Each entry states
``max_threads`` and ``max_memory_gb`` -- what that machine may give ic-opt at once. There are no
default numbers: a missing file, host or field refuses to start, because a guessed machine size is
right only on the machine it was guessed on (T15; audit 2026-09-24 rows 1-4).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, fields
from pathlib import Path

import yaml

SITE_FILE = Path("~/.ic-opt/site.yaml")
_LOG = logging.getLogger(__name__)

EXAMPLE = """\
# ~/.ic-opt/site.yaml -- one entry per machine. ic-opt has no default for any of these numbers:
# every number below is a placeholder; write what that machine may give ic-opt.
hosts:
  local:                               # the machine running ic-opt (the simulation host without --ssh-profile)
    max_threads: 16                    # required (placeholder): threads ic-opt may use at once
    max_memory_gb: 32                  # required (placeholder): memory in GB ic-opt may use at once
  lab:                                 # named like --ssh-profile lab
    max_threads: 64                    # required (placeholder)
    max_memory_gb: 128                 # required (placeholder)
    cshrc: /path/to/cadence_env.csh    # optional: the Cadence environment on that host
    scratch_root: /scratch/me/ic-opt   # optional: working directories there (else ~/.ic-opt/scratch)
    license_probe: lmstat -a           # optional: the license query env.doctor runs there
    transfer_timeout_s: 1800           # optional (placeholder): scp / ssh probe timeout in seconds
"""


class SiteError(ValueError):
    """site.yaml is missing, has no entry for the host asked for, or holds a value ic-opt cannot use."""


class EnvelopeError(ValueError):
    """A job needs more threads or memory than its host's entry allows: refused before anything starts."""


@dataclass(frozen=True)
class HostLimits:
    """One machine's entry, as the user wrote it. The two limits have no default on purpose."""

    max_threads: int
    max_memory_gb: float
    cshrc: str | None = None                  # the Cadence environment file on that host (csh or sh: executor.hook_shell)
    scratch_root: str | None = None           # where SSH runs keep their working directories
    license_probe: str | None = None          # the license query env.doctor runs there
    transfer_timeout_s: int | None = None     # scp / ssh probe timeout on that host

    def slots(self, threads_per_job: int, memory_gb_per_job: float = 0.0) -> int:
        """How many jobs of this size fit at once. 0 means a single job does not fit: callers refuse, never round up."""
        by_threads = self.max_threads // threads_per_job
        by_memory = int(self.max_memory_gb // memory_gb_per_job) if memory_gb_per_job else by_threads
        return min(by_threads, by_memory)


_FIELDS = tuple(f.name for f in fields(HostLimits))
_REQUIRED = ("max_threads", "max_memory_gb")


@dataclass(frozen=True)
class Site:
    """Every machine ic-opt may use, by host name (``local`` or an ``--ssh-profile`` name)."""

    hosts: dict[str, HostLimits]
    source: str = str(SITE_FILE)              # the file the entries came from, for messages
    legacy: bool = False                      # read from the flat 0.2.0 format (one machine, taken as hosts.local)

    def host(self, name: str) -> HostLimits:
        """The named host's entry; a host without one refuses, showing the entry to add."""
        if name in self.hosts:
            return self.hosts[name]
        known = ", ".join(sorted(self.hosts)) or "none"
        flat = (" The file uses the flat 0.2.0 format, read as hosts.local: move its keys under 'hosts: local:'"
                " and add one entry per --ssh-profile host." if self.legacy else "")
        raise SiteError(f"{self.source} has no entry for host {name!r} (known: {known}); ic-opt does not guess a "
                        f"machine's limits.{flat} Add:\n\n{_entry_example(name)}")


def load(path: str | Path | None = None) -> Site:
    """Read site.yaml (``~/.ic-opt/site.yaml`` unless ``path`` is given). Nothing is defaulted: a missing file,
    an unknown key or a bad value refuses with the v2 example."""
    path = Path(SITE_FILE if path is None else path).expanduser()
    if not path.is_file():
        raise SiteError(f"{path} not found. ic-opt takes every machine's limits from you (parallel jobs are sized "
                        f"against them) and has no defaults; create it:\n\n{EXAMPLE}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SiteError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict) or not data:
        raise SiteError(f"{path} lists no hosts; write one entry per machine:\n\n{EXAMPLE}")
    if "hosts" not in data and set(data) & set(_FIELDS):            # the flat 0.2.0 format: one machine
        _LOG.warning("%s uses the flat 0.2.0 format; it is read as hosts.local. Move its keys under 'hosts: local:' "
                     "and add one entry per --ssh-profile host (see README, Site envelope).", path)
        return Site({"local": _host(data, f"{path}: hosts.local (flat format)", "local")}, str(path), legacy=True)
    if set(data) != {"hosts"}:
        raise SiteError(f"{path}: unknown top-level key(s) {', '.join(sorted(map(str, set(data) - {'hosts'})))}; "
                        f"site.yaml holds one mapping, hosts:\n\n{EXAMPLE}")
    hosts = data["hosts"]
    if not isinstance(hosts, dict) or not hosts:
        raise SiteError(f"{path}: hosts must map host names to their limits:\n\n{EXAMPLE}")
    return Site({str(name): _host(entry, f"{path}: hosts.{name}", str(name)) for name, entry in hosts.items()}, str(path))


def _host(entry: object, where: str, name: str) -> HostLimits:
    """One host's entry with every value checked: both limits present and positive, optional ones typed, nothing unknown."""
    if not isinstance(entry, dict):
        raise SiteError(f"{where} must be a mapping of limits:\n\n{_entry_example(name)}")
    unknown = sorted(map(str, set(entry) - set(_FIELDS)))
    if unknown:
        raise SiteError(f"{where}: unknown key(s) {', '.join(unknown)}; an entry takes {', '.join(_FIELDS)}")
    missing = [key for key in _REQUIRED if entry.get(key) is None]
    if missing:
        raise SiteError(f"{where} lacks {' and '.join(missing)}: required, no default (what this machine may "
                        f"give ic-opt at once):\n\n{_entry_example(name)}")
    return HostLimits(
        max_threads=_count(entry["max_threads"], f"{where}.max_threads"),
        max_memory_gb=_amount(entry["max_memory_gb"], f"{where}.max_memory_gb"),
        cshrc=_text(entry.get("cshrc"), f"{where}.cshrc"),
        scratch_root=_text(entry.get("scratch_root"), f"{where}.scratch_root"),
        license_probe=_text(entry.get("license_probe"), f"{where}.license_probe"),
        transfer_timeout_s=None if entry.get("transfer_timeout_s") is None
        else _count(entry["transfer_timeout_s"], f"{where}.transfer_timeout_s"),
    )


def _count(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SiteError(f"{where} must be a positive integer, got {value!r}")
    return value


def _amount(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not (value > 0 and math.isfinite(value)):
        raise SiteError(f"{where} must be a positive number, got {value!r}")
    return float(value)


def _text(value: object, where: str) -> str | None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise SiteError(f"{where} must be a non-empty string, got {value!r}")
    return value


def _entry_example(name: str) -> str:
    return (f"hosts:\n  {name}:\n"
            f"    max_threads: 16       # placeholder: threads ic-opt may use at once on {name}\n"
            f"    max_memory_gb: 32     # placeholder: memory in GB ic-opt may use at once on {name}\n")
