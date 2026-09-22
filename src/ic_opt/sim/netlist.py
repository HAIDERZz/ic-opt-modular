"""Spectre deck templating: exported deck -> template with ``{{VAR}}`` -> rendered deck.

Transplanted from the legacy ``netlists.py`` / ``real_run.py``. The rules are
the ones proven in production: only top-level ``parameters`` statements are
templated, each approved variable must appear exactly once at top level and
nowhere inside a subckt, continuation lines are respected, and a corner may
only swap the model ``section`` / ``include`` file and top-level parameter
values.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ic_opt.spec import Corner

ASSIGNMENT_RE = re.compile(r"(?<![A-Za-z0-9_])(?P<name>[A-Za-z_][A-Za-z0-9_]*)=")
INCLUDE_SECTION_RE = re.compile(r"^(include\s+\S+\s+section=)\S+", re.MULTILINE)
INCLUDE_FILE_RE = re.compile(r'^(include\s+)("?)[^"\s]+("?\s+section=\S+)', re.MULTILINE)
PLACEHOLDER_RE = re.compile(r"{{(?P<name>[A-Za-z_][A-Za-z0-9_]*)}}")
UNRESOLVED_RE = re.compile(r"{{[^{}]*}}")


def template_deck(deck_text: str, variable_names: list[str]) -> str:
    """Replace the approved variables' values in top-level ``parameters`` with placeholders."""
    lines = deck_text.splitlines(keepends=True)
    output = list(lines)
    wanted = set(variable_names)
    seen = dict.fromkeys(variable_names, 0)
    nested = dict.fromkeys(variable_names, 0)
    depth = 0
    for statement in _logical_statements(lines):
        text = "".join(lines[i] for i in statement)
        first = _first_token(text)
        top_level = depth == 0
        if first == "subckt":
            depth += 1
        elif first == "ends" and depth > 0:
            depth -= 1
        if first != "parameters":
            continue
        if not top_level:
            for name, count in _count_assignments(text, wanted).items():
                nested[name] += count
            continue
        rewritten, found = _rewrite_parameters(text, wanted, lambda name: f"{{{{{name}}}}}")
        for name, count in found.items():
            seen[name] += count
        new_lines = _split_like(rewritten, text)
        if len(new_lines) != len(statement):
            raise ValueError("parameters statement could not be rewritten without changing line structure")
        for index, line in zip(statement, new_lines, strict=True):
            output[index] = line
    issues = [f"variable {n} was not found in top-level parameters" for n, c in seen.items() if c == 0]
    issues += [f"variable {n} appears more than once in top-level parameters" for n, c in seen.items() if c > 1]
    issues += [f"variable {n} was found inside a subckt" for n, c in nested.items() if c]
    if issues:
        raise ValueError("; ".join(issues))
    return "".join(output)


def apply_corner(template_text: str, corner: Corner) -> str:
    """Specialize a template for one process corner (model section/file, parameter values)."""
    result = template_text
    if corner.model_section is not None:
        result, n = INCLUDE_SECTION_RE.subn(r"\g<1>" + corner.model_section, result)
        if n == 0:
            raise ValueError(f"corner {corner.id}: no 'include ... section=...' line to set model_section on")
    if corner.model_file is not None:
        result, n = INCLUDE_FILE_RE.subn(r"\g<1>\g<2>" + corner.model_file + r"\g<3>", result)
        if n != 1:
            raise ValueError(f"corner {corner.id}: model_file must match exactly one include line, matched {n}")
    if corner.variables:
        result, found = _set_top_level_values(result, corner.variables)
        missing = [name for name, count in found.items() if count == 0]
        if missing:
            raise ValueError(f"corner {corner.id}: variables {missing} not found in top-level parameters")
    return result


def render(template_text: str, params: dict[str, str]) -> str:
    """Substitute every ``{{VAR}}``; the placeholder set must equal the parameter set."""
    names = {m.group("name") for m in PLACEHOLDER_RE.finditer(template_text)}
    if names != set(params):
        raise ValueError(f"template placeholders {sorted(names)} != parameters {sorted(params)}")
    rendered = template_text
    for name, value in params.items():
        rendered = rendered.replace(f"{{{{{name}}}}}", value)
    leftover = sorted({m.group(0) for m in UNRESOLVED_RE.finditer(rendered)})
    if leftover:
        raise ValueError(f"unresolved placeholders after render: {leftover}")
    return rendered


# -- statement helpers ----------------------------------------------------------

def _logical_statements(lines: list[str]) -> list[list[int]]:
    statements, current = [], []
    for index, line in enumerate(lines):
        current.append(index)
        if not line.rstrip("\r\n").rstrip().endswith("\\"):
            statements.append(current)
            current = []
    if current:
        statements.append(current)
    return statements


def _first_token(text: str) -> str:
    stripped = text.lstrip()
    return stripped.split(maxsplit=1)[0] if stripped else ""


def _rewrite_parameters(text: str, names: set[str], replacement) -> tuple[str, dict[str, int]]:
    matches = list(ASSIGNMENT_RE.finditer(text))
    found = dict.fromkeys(names, 0)
    pieces, cursor = [], 0
    for index, match in enumerate(matches):
        name = match.group("name")
        value_start = match.end()
        value_end = _value_end(text, matches, index)
        pieces.append(text[cursor:value_start])
        if name in names:
            pieces.append(replacement(name))
            found[name] += 1
        else:
            pieces.append(text[value_start:value_end])
        cursor = value_end
    pieces.append(text[cursor:])
    return "".join(pieces), found


def _count_assignments(text: str, names: set[str]) -> dict[str, int]:
    found = dict.fromkeys(names, 0)
    for match in ASSIGNMENT_RE.finditer(text):
        if match.group("name") in names:
            found[match.group("name")] += 1
    return found


def _value_end(text: str, matches: list[re.Match[str]], index: int) -> int:
    start = matches[index].end()
    end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
    segment = text[start:end]
    if "\\" in segment:
        segment = segment[: segment.index("\\")]
    return start + len(segment.rstrip())


def _split_like(rewritten: str, original: str) -> list[str]:
    if original.count("\n") != rewritten.count("\n"):
        return [rewritten]
    return rewritten.splitlines(keepends=True)


def _set_top_level_values(text: str, values: dict[str, str]) -> tuple[str, dict[str, int]]:
    lines = text.splitlines(keepends=True)
    output = list(lines)
    found = dict.fromkeys(values, 0)
    depth = 0
    for statement in _logical_statements(lines):
        stmt = "".join(lines[i] for i in statement)
        first = _first_token(stmt)
        if first == "subckt":
            depth += 1
            continue
        if first == "ends" and depth > 0:
            depth -= 1
            continue
        if first != "parameters" or depth > 0:
            continue
        for name, count in _count_assignments(stmt, set(values)).items():
            found[name] += count
        rewritten, _ = _rewrite_parameters(stmt, set(values), lambda name: values[name])
        new_lines = _split_like(rewritten, stmt)
        if len(new_lines) != len(statement):
            continue
        for index, line in zip(statement, new_lines, strict=True):
            output[index] = line
    return "".join(output), found
