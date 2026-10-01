"""Touchstone sNp files: header validation, numeric reading (kernel moved from em-opt ``device_db/measure.py``; version
1, and since N-48 version 2 -- its keywords, ``Lower`` / ``Upper`` triangles, the two-port data order, ``[Reference]``)
and writing (T18.2B: a library row's sNp with its ports reordered for the bindings, ``write``, always version 1)."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_FREQ_MULT = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}
_FREQ_UNIT = {"HZ": "Hz", "KHZ": "kHz", "MHZ": "MHz", "GHZ": "GHz"}      # the option line's spelling of each
PAIRS_PER_LINE = 4                                                        # a matrix row of more ports wraps (version 1)


class TouchstoneError(ValueError):
    pass


@dataclass
class Touchstone:
    freqs: np.ndarray            # [F] Hz
    s: np.ndarray                # [F, n, n] complex, row-major S[i, j]
    z0: float                    # the file's scalar reference impedance
    unit: str = "Hz"             # the option line's frequency unit, as read (``write`` keeps it)
    fmt: str = "RI"              # its number format: RI, MA or DB (``write`` keeps it)

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
        if not line.startswith(("!", "[")):      # a version 2 file puts ``[Version] 2.0`` before the option line
            break
    if "EMX was run" not in "\n".join(header):
        issues.append("touchstone header does not include EMX command line")
    option = f"# Hz S RI R {format_number(z0)}"
    if not any(line.strip() == option for line in header if line.strip().startswith("#")):
        issues.append(f"touchstone option line is not '{option}'")
    return issues


_KEYWORD = re.compile(r"^\[([^\]]+)\]\s*(.*)$")
_SKIPPED = {"version", "number of frequencies", "number of noise frequencies", "network data", "end"}


def read(path: Path | str) -> Touchstone:
    """Read a Touchstone sNp file: version 1 (RI / MA / DB, wrapped lines, the 2-port S11 S21 S12 S22 column order) or
    version 2 (N-48: ``[Version] 2.0``; ``[Number of Ports]`` must agree with the suffix; ``[Matrix Format]`` ``Full``,
    ``Lower`` or ``Upper`` -- a triangle is the symmetric matrix; a 2-port needs ``[Two-Port Data Order]``, ``21_12`` being
    version 1's order and ``12_21`` row-major; ``[Reference]`` replaces the option line's R when every port has the same
    value, per-port values that differ are refused; an ``[Information]`` block and ``[Noise Data]`` are passed over;
    ``[Mixed-Mode Order]`` and a keyword this reader does not know are refused). Always the full matrix, row-major."""
    path = Path(path)
    n = n_ports_from_suffix(path)
    fmt, mult, unit, z0 = "RI", 1.0, "Hz", 50.0
    version, matrix_format, two_port_order, reference = 1, "full", None, None
    tokens: list[float] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    at = 0
    while at < len(lines):
        line = lines[at].split("!", 1)[0].strip()
        at += 1
        if not line:
            continue
        if line.startswith("#"):
            parts = line[1:].upper().split()
            mult = _FREQ_MULT.get(parts[0], 1.0) if parts else 1.0
            unit = _FREQ_UNIT.get(parts[0], "Hz") if parts else "Hz"
            fmt = "MA" if "MA" in parts else "DB" if "DB" in parts else "RI"
            if "R" in parts:
                try:
                    z0 = float(parts[parts.index("R") + 1])
                except (IndexError, ValueError) as exc:
                    raise TouchstoneError(f"{path}: invalid reference impedance R") from exc
                if not math.isfinite(z0) or z0 <= 0:
                    raise TouchstoneError(f"{path}: reference impedance R must be finite and positive")
            continue
        keyword = _KEYWORD.match(line)
        if keyword:
            name, value = keyword.group(1).strip().lower(), keyword.group(2).strip()
            version = 2
            if name == "number of ports":
                if value != str(n):
                    raise TouchstoneError(f"{path}: [Number of Ports] {value} but the suffix names {n} ports")
            elif name == "matrix format":
                matrix_format = value.lower()
                if matrix_format not in ("full", "lower", "upper"):
                    raise TouchstoneError(f"{path}: [Matrix Format] {value!r} is not Full, Lower or Upper")
            elif name == "two-port data order":
                two_port_order = value
                if value not in ("12_21", "21_12"):
                    raise TouchstoneError(f"{path}: [Two-Port Data Order] {value!r} is not 12_21 or 21_12")
            elif name == "reference":
                reference = value.split()
                while at < len(lines):         # its values may go on until the next keyword, which the loop then reads
                    line = lines[at].split("!", 1)[0].strip()
                    if line.startswith("["):
                        break
                    reference.extend(line.split())
                    at += 1
            elif name == "begin information":
                while at < len(lines) and lines[at].split("!", 1)[0].strip().lower() != "[end information]":
                    at += 1
                if at == len(lines):
                    raise TouchstoneError(f"{path}: [Begin Information] without [End Information]")
                at += 1
            elif name == "noise data":
                break                          # noise parameters are not read
            elif name == "end":
                break
            elif name in _SKIPPED:
                continue
            else:
                raise TouchstoneError(f"{path}: keyword [{keyword.group(1).strip()}] is not supported by this reader")
            continue
        try:
            tokens.extend(float(t) for t in line.split())
        except ValueError as exc:
            raise TouchstoneError(f"{path}: unreadable data line {line[:40]!r}") from exc
    if reference is not None:
        try:
            values = [float(r) for r in reference]
        except ValueError as exc:
            raise TouchstoneError(f"{path}: unreadable [Reference] values {reference}") from exc
        if len(values) != n:
            raise TouchstoneError(f"{path}: [Reference] lists {len(values)} impedances for {n} ports")
        if any(not math.isfinite(v) or v <= 0 for v in values):
            raise TouchstoneError(f"{path}: every [Reference] impedance must be finite and positive")
        if len(set(values)) != 1:
            raise TouchstoneError(f"{path}: [Reference] impedances differ per port ({reference}); one common reference is supported")
        z0 = values[0]
    if version == 2 and n == 2 and two_port_order is None:
        raise TouchstoneError(f"{path}: a version 2 two-port file needs [Two-Port Data Order]")
    triangle = matrix_format in ("lower", "upper")
    per_freq = 1 + (n * (n + 1) if triangle else 2 * n * n)
    if not tokens or len(tokens) % per_freq != 0:
        raise TouchstoneError(f"{path}: token count {len(tokens)} not a multiple of {per_freq} ({n}-port, {matrix_format})")
    block = np.asarray(tokens, dtype=float).reshape(-1, per_freq)
    freqs = block[:, 0] * mult
    a, b = block[:, 1::2], block[:, 2::2]
    if fmt == "RI":
        values = a + 1j * b
    else:
        magnitude = 10.0 ** (a / 20.0) if fmt == "DB" else a
        values = magnitude * np.exp(1j * np.deg2rad(b))
    if triangle:
        s = np.zeros((len(freqs), n, n), dtype=complex)
        rows, cols = (np.tril_indices(n) if matrix_format == "lower" else np.triu_indices(n))
        s[:, rows, cols] = values                                   # row after row, each with its j <= i (or j >= i)
        s[:, cols, rows] = values                                   # the symmetric half
    else:
        s = values.reshape(-1, n, n)
        if n == 2 and (version == 1 or two_port_order == "21_12"):   # S11 S21 S12 S22 -> transpose to row-major
            s = s.transpose(0, 2, 1)
    if not np.isfinite(freqs).all() or not np.isfinite(s).all():
        raise TouchstoneError(f"{path}: non-finite values")
    return Touchstone(freqs, s, z0, unit, fmt)


def permuted(ts: Touchstone, order: Sequence[int]) -> Touchstone:
    """``ts`` with its ports in ``order`` (new port ``j`` is old port ``order[j]``): every S matrix permuted, rows and
    columns alike; the frequencies, reference impedance, unit and format stay."""
    order = list(order)
    if sorted(order) != list(range(ts.n_ports)):
        raise TouchstoneError(f"a port order of a {ts.n_ports}-port lists each port once, got {order}")
    return Touchstone(ts.freqs.copy(), ts.s[:, order][:, :, order], ts.z0, ts.unit, ts.fmt)


def write(path: Path | str, ts: Touchstone, *, comments: Sequence[str] = ()) -> Path:
    """Write ``ts`` as a Touchstone version 1 file (``read`` reads it back): the ``comments`` as ``!`` lines, the option
    line ``# <unit> S <format> R <z0>`` with ``ts``'s frequency unit, format (RI, MA or DB) and reference impedance -- the
    ones the file it was read from had -- and per frequency the full matrix: a 1- or 2-port on one line (the 2-port in
    the order S11 S21 S12 S22), more ports one matrix row after the other, ``PAIRS_PER_LINE`` pairs a line. Numbers take
    ``format_number``'s text (an integer as an integer, else ``%g``) wherever that text reads back as the same number,
    else the shortest text that does (``repr``): ``read(write(x))`` gives ``x`` back to the text's precision, exactly for
    RI; MA and DB go through their conversion to complex and back."""
    path = Path(path)
    n = ts.n_ports
    if n_ports_from_suffix(path) != n:
        raise TouchstoneError(f"{path.name}: the suffix names another port count than the {n} ports written")
    scale = _FREQ_MULT[ts.unit.upper()]
    lines = [f"! {text}" for text in comments] + [f"# {ts.unit} S {ts.fmt} R {_number(ts.z0)}"]
    for f, s in zip(ts.freqs, ts.s, strict=True):
        matrix = s.T if n == 2 else s                     # 2-port: S11 S21 S12 S22
        pairs = [" ".join(_number(x) for x in _pair(v, ts.fmt)) for v in matrix.reshape(-1)]
        head = _number(float(f) / scale)
        if n <= 2:
            lines.append(" ".join([head, *pairs]))
            continue
        for i in range(n):
            row = pairs[i * n:(i + 1) * n]
            for j in range(0, n, PAIRS_PER_LINE):
                lead = head if i == 0 and j == 0 else " " * len(head)
                lines.append(" ".join([lead, *row[j:j + PAIRS_PER_LINE]]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _pair(value: complex, fmt: str) -> tuple[float, float]:
    """One S value in the file's format: real and imaginary part (RI), magnitude and angle in degrees (MA), magnitude in
    dB and angle (DB)."""
    if fmt == "RI":
        return float(value.real), float(value.imag)
    magnitude, angle = abs(value), math.degrees(math.atan2(value.imag, value.real))
    return (20.0 * math.log10(magnitude) if magnitude > 0 else -math.inf, angle) if fmt == "DB" else (magnitude, angle)


def _number(value: float) -> str:
    """``format_number``'s text when it reads back as ``value``, else the shortest text that does (``repr``)."""
    text = format_number(value)
    return text if float(text) == value else repr(float(value))
