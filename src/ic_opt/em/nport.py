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


def patch(text: str, *, instance: str, replacement: str, n_ports: int) -> Patch:
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
