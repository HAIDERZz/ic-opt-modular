"""Touchstone v1 sNp files: header validation and numeric reading (kernel moved from em-opt ``device_db/measure.py``)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_FREQ_MULT = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}


class TouchstoneError(ValueError):
    pass


@dataclass
class Touchstone:
    freqs: np.ndarray            # [F] Hz
    s: np.ndarray                # [F, n, n] complex, row-major S[i, j]
    z0: float                    # the file's scalar reference impedance

    @property
    def n_ports(self) -> int:
        return int(self.s.shape[1])


def n_ports_from_suffix(path: Path) -> int:
    match = re.fullmatch(r"\.s(\d+)p", Path(path).suffix.lower())
    if match is None:
        raise TouchstoneError(f"not a touchstone file: {path}")
    return int(match.group(1))


def format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def header_issues(path: Path, *, expected_ports: int, z0: float) -> list[str]:
    """em-opt's post-EMX checks: suffix port count, file present, EMX command line in the header, the option line."""
    path = Path(path)
    issues: list[str] = []
    try:
        ports = n_ports_from_suffix(path)
    except TouchstoneError:
        issues.append(f"touchstone extension is not .sNp: {path.name}")
    else:
        if ports != expected_ports:
            issues.append(f"touchstone extension declares {ports} ports, expected {expected_ports}")
    if not path.is_file():
        return [*issues, f"touchstone file is missing: {path}"]
    header: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        header.append(line)
        if not line.startswith("!"):
            break
    if "EMX was run" not in "\n".join(header):
        issues.append("touchstone header does not include EMX command line")
    option = f"# Hz S RI R {format_number(z0)}"
    if not any(line.strip() == option for line in header if line.strip().startswith("#")):
        issues.append(f"touchstone option line is not '{option}'")
    return issues


def read(path: Path | str) -> Touchstone:
    """Read a Touchstone v1 sNp file (RI / MA / DB, wrapped lines, the 2-port S11 S21 S12 S22 column order)."""
    path = Path(path)
    n = n_ports_from_suffix(path)
    fmt, mult, z0 = "RI", 1.0, 50.0
    tokens: list[float] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("!", 1)[0].strip()
        if not line:
            continue
        if line.startswith("#"):
            parts = line[1:].upper().split()
            mult = _FREQ_MULT.get(parts[0], 1.0) if parts else 1.0
            fmt = "MA" if "MA" in parts else "DB" if "DB" in parts else "RI"
            if "R" in parts:
                try:
                    z0 = float(parts[parts.index("R") + 1])
                except (IndexError, ValueError) as exc:
                    raise TouchstoneError(f"{path}: invalid reference impedance R") from exc
                if not math.isfinite(z0) or z0 <= 0:
                    raise TouchstoneError(f"{path}: reference impedance R must be finite and positive")
            continue
        try:
            tokens.extend(float(t) for t in line.split())
        except ValueError as exc:
            raise TouchstoneError(f"{path}: unreadable data line {line[:40]!r}") from exc
    per_freq = 1 + 2 * n * n
    if not tokens or len(tokens) % per_freq != 0:
        raise TouchstoneError(f"{path}: token count {len(tokens)} not a multiple of {per_freq} ({n}-port)")
    block = np.asarray(tokens, dtype=float).reshape(-1, per_freq)
    freqs = block[:, 0] * mult
    a, b = block[:, 1::2], block[:, 2::2]
    if fmt == "RI":
        values = a + 1j * b
    else:
        magnitude = 10.0 ** (a / 20.0) if fmt == "DB" else a
        values = magnitude * np.exp(1j * np.deg2rad(b))
    s = values.reshape(-1, n, n)
    if n == 2:                       # touchstone 2-port order: S11 S21 S12 S22 -> transpose to row-major
        s = s.transpose(0, 2, 1)
    if not np.isfinite(freqs).all() or not np.isfinite(s).all():
        raise TouchstoneError(f"{path}: non-finite values")
    return Touchstone(freqs, s, z0)
