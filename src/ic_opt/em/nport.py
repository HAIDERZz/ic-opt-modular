"""Bind an S-parameter file into a Spectre netlist's ``nport`` instance (kernel moved from em-opt ``nport_binding.py``, text in / text out).

A logical statement is a line plus its backslash continuations; the instance
statement is the one whose first token is the instance name and which contains
`` nport``. Only the quoted ``file="..."`` path is replaced, so other options
(``interp=bbspice`` and friends) survive untouched; the terminal list must hold
exactly two nodes per port (signal, reference).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

FILE_RE = re.compile(r'file\s*=\s*"(?P<path>[^"]+)"')
TERMINALS_RE = re.compile(r"\((?P<nodes>.*?)\)", re.DOTALL)


class NportError(ValueError):
    pass


@dataclass(frozen=True)
class Patch:
    text: str                    # the patched netlist
    original_file: str           # what the instance pointed at before
    signal_nodes: list[str]      # the instance's signal-side node names, in terminal order


def patch(text: str, *, instance: str, replacement: str, n_ports: int, keep: list[int] | None = None) -> Patch:
    """Point the instance at ``replacement``. ``n_ports`` is the instance's port count (two nodes each). ``keep`` lists
    the instance's ports, 0-based and in order, that the new file drives: the others leave the terminal list (the
    statement keeps its line count: the pairs before the last sit on the first line, the last pair on the last), so
    a 6-port device replaces a 10-port instance whose extra ports were a tap wired twice and grounded spares (N-49)."""
    lines = text.splitlines(keepends=True)
    matches = [s for s in _logical_statements(lines) if _first_token(_join(lines, s)) == instance and " nport" in _join(lines, s)]
    if len(matches) != 1:
        raise NportError(f"expected exactly one nport instance {instance}, found {len(matches)}")
    statement = matches[0]
    statement_text = _join(lines, statement)
    file_match = FILE_RE.search(statement_text)
    if file_match is None:
        raise NportError(f"nport instance {instance} has no file field")
    nodes_match = TERMINALS_RE.search(statement_text)
    if nodes_match is None:
        raise NportError(f"nport instance {instance} has no terminal list")
    nodes = nodes_match.group("nodes").replace("\\", " ").split()
    if len(nodes) != 2 * n_ports:
        raise NportError(f"nport instance {instance} has {len(nodes)} terminal nodes, expected {2 * n_ports} for {n_ports} ports")
    patched = statement_text[: file_match.start("path")] + replacement + statement_text[file_match.end("path"):]
    if keep is not None and list(keep) != list(range(n_ports)):
        if not keep or sorted(set(keep)) != sorted(keep) or min(keep) < 0 or max(keep) >= n_ports:
            raise NportError(f"nport instance {instance}: keep must list distinct ports out of {n_ports}, got {keep}")
        nodes = [n for i in keep for n in nodes[2 * i:2 * i + 2]]
        original = nodes_match.group("nodes")
        lead, trail = original[: len(original) - len(original.lstrip())], original[len(original.rstrip()):]
        breaks = original.count("\n")
        pairs = [" ".join(nodes[2 * i:2 * i + 2]) for i in range(len(keep))]
        if breaks == 0:
            new_nodes = lead + " ".join(pairs) + trail
        else:
            last = original.split("\n")[-1]
            indent = last[: len(last) - len(last.lstrip())]
            new_nodes = (lead + " ".join(pairs[:-1]) + " \\\n" + "".join(indent + "\\\n" for _ in range(breaks - 1))
                         + indent + pairs[-1] + trail)
        span = TERMINALS_RE.search(patched)
        patched = patched[: span.start("nodes")] + new_nodes + patched[span.end("nodes"):]
    patched_lines = patched.splitlines(keepends=True)
    if len(patched_lines) != len(statement):
        raise NportError("patch changed the statement's line count")
    for index, line in zip(statement, patched_lines, strict=True):
        lines[index] = line
    return Patch("".join(lines), file_match.group("path"), nodes[::2])


def _logical_statements(lines: list[str]) -> list[list[int]]:
    statements: list[list[int]] = []
    current: list[int] = []
    for index, line in enumerate(lines):
        current.append(index)
        if line.rstrip("\r\n").rstrip().endswith("\\"):
            continue
        statements.append(current)
        current = []
    if current:
        statements.append(current)
    return statements


def _join(lines: list[str], statement: list[int]) -> str:
    return "".join(lines[i] for i in statement)


def _first_token(statement_text: str) -> str:
    stripped = statement_text.lstrip()
    return stripped.split(maxsplit=1)[0] if stripped else ""
