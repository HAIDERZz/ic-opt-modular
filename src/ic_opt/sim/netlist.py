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
from dataclasses import dataclass
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


def exported_values(deck_text: str, variable_names: list[str]) -> dict[str, str]:
    """The values an exported deck's top-level ``parameters`` statement gives the variables, as written (``"0.6u"``,
    ``"600n"``, or an expression): the statement :func:`template_deck` rewrites, parsed the same way. A variable found
    nowhere at top level, or more than once, is an error, as it is for ``template_deck``."""
    lines = deck_text.splitlines(keepends=True)
    found: dict[str, list[str]] = {name: [] for name in variable_names}
    depth = 0
    for statement in _logical_statements(lines):
        text = "".join(lines[i] for i in statement)
        first = _first_token(text)
        top_level = depth == 0
        if first == "subckt":
            depth += 1
        elif first == "ends" and depth > 0:
            depth -= 1
        if first != "parameters" or not top_level:
            continue
        matches = list(ASSIGNMENT_RE.finditer(text))
        for index, match in enumerate(matches):
            if match.group("name") in found:
                found[match.group("name")].append(text[match.end():_value_end(text, matches, index)].strip())
    issues = [f"variable {n} was not found in top-level parameters" for n, v in found.items() if not v]
    issues += [f"variable {n} appears more than once in top-level parameters" for n, v in found.items() if len(v) > 1]
    if issues:
        raise ValueError("; ".join(issues))
    return {name: values[0] for name, values in found.items()}


def apply_corner(template_text: str, corner: Corner) -> str:
    """Specialize a template for one corner: the model section / file of the include line, top-level parameter values, and
    the ``simulatorOptions`` values (``options``: ``temp`` sets the simulation temperature an ADE export writes as a literal)."""
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
    if corner.options:
        result, n = _set_simulator_options(result, corner.options)
        if n == 0:
            raise ValueError(f"corner {corner.id}: no top-level 'simulatorOptions' statement to set options {sorted(corner.options)} on")
    return result


def _set_simulator_options(text: str, options: dict[str, str]) -> tuple[str, int]:
    """``key=value`` on every top-level ``simulatorOptions`` statement: an existing key is rewritten in place, a missing one
    appended to the statement. Returns the text and the number of statements touched."""
    lines = text.splitlines(keepends=True)
    output, touched, depth = list(lines), 0, 0
    for statement in _logical_statements(lines):
        stmt = "".join(lines[i] for i in statement)
        first = _first_token(stmt)
        if first == "subckt":
            depth += 1
        elif first == "ends" and depth > 0:
            depth -= 1
        if first != "simulatorOptions" or depth:
            continue
        new = stmt
        for key, value in options.items():
            pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(key)}=\S+")
            if pattern.search(new):
                new = pattern.sub(f"{key}={value}", new, count=1)
            else:
                body = new.rstrip("\r\n")
                new = f"{body} {key}={value}{new[len(body):]}"
        output[statement[0]:statement[-1] + 1] = new.splitlines(keepends=True)   # the same lines, no newline added
        touched += 1
    return "".join(output), touched


def unreferenced_parameters(deck_text: str, names: list[str]) -> list[str]:
    """Of ``names``, the ones the deck declares in a top-level ``parameters`` statement but uses nowhere else: setting them
    (a corner's ``variables``) changes nothing. Every other statement is searched for the name as a whole word."""
    lines = deck_text.splitlines(keepends=True)
    rest, depth = [], 0
    for statement in _logical_statements(lines):
        text = "".join(lines[i] for i in statement)
        first = _first_token(text)
        if first == "subckt":
            depth += 1
        elif first == "ends" and depth > 0:
            depth -= 1
        if not (first == "parameters" and depth == 0):
            rest.append(text)
    body = "".join(rest)
    return [n for n in names if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])", body)]


# -- operating points (T17.5) ------------------------------------------------------

OP_INFO_NAME = "icoptOpInfo"          # the statements ic-opt adds; their names are what the replay script selects
OP_DC_NAME = "icoptDcOp"
OP_INFO_STATEMENT = f"{OP_INFO_NAME} info what=oppoint where=rawfile"
OP_DC_STATEMENT = f"{OP_DC_NAME} dc"
# Spectre analysis types: an `info` statement reports the state at the end of the analysis before it (ADE's
# `finalTimeOP info what=oppoint` after `tran` is the final time point), so only an info whose preceding analysis is a
# plain DC gives the operating point.
_ANALYSES = frozenset({
    "dc", "ac", "tran", "noise", "xf", "sp", "stb", "pz", "dcmatch", "acmatch", "sens", "pss", "pac", "pnoise", "pxf",
    "psp", "pstb", "qpss", "qpac", "qpnoise", "qpxf", "qpsp", "hb", "hbac", "hbnoise", "hbxf", "hbsp", "hbstb", "envlp",
    "sweep", "montecarlo", "loadpull", "lss", "tdr",
})
_DC_SWEEP_KEYS = frozenset({"param", "dev", "mod", "sub", "values", "valuesfile", "start", "stop", "center", "span",
                            "step", "lin", "log", "dec"})
_OPENERS = {"subckt": "ends", "library": "endlibrary", "section": "endsection"}
_CLOSERS = frozenset(_OPENERS.values())
_PARAM_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_.]*)=("[^"]*"|\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|\S+)')
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class OperatingPoints:
    """How a netlist gives the operating points: ``mode`` "export" (it asks for them itself), "statement" (it has a plain
    DC analysis; ic-opt adds the info statement right after it) or "analysis" (it has none; ic-opt appends a DC analysis
    and the statement). ``result`` is the info statement's name, the OCEAN result that holds them."""

    mode: str
    result: str
    after: str | None = None           # "statement": the DC analysis the info follows
    insert_at: int | None = None       # line index the added lines go before (None: the end of the text)
    spice_at_end: bool = False         # "analysis": the netlist ends in `simulator lang=spice`; the added lines switch back

    @property
    def added(self) -> list[str]:
        """The lines ic-opt adds, the comment first ([] for "export")."""
        off = "simulator.operating_points: false leaves them out"
        if self.mode == "statement":
            return [f"// ic-opt: operating points of {self.after} ({off})", OP_INFO_STATEMENT]
        if self.mode == "analysis":
            return ([f"// ic-opt: operating points, from a DC analysis run after the netlist's analyses ({off})"]
                    + ["simulator lang=spectre"] * self.spice_at_end + [OP_DC_STATEMENT, OP_INFO_STATEMENT])
        return []

    def describe(self) -> str:
        if self.mode == "export":
            return f"in the export ({self.result})"
        if self.mode == "statement":
            return f"added by ic-opt (statement): `{OP_INFO_STATEMENT}` after {self.after}"
        return f"added by ic-opt (DC analysis and statement): `{OP_DC_STATEMENT}`, `{OP_INFO_STATEMENT}` at the end"


def operating_points(text: str) -> OperatingPoints:
    """Where the operating points of a Spectre netlist come from. Only top-level statements count: not inside a
    ``subckt`` / ``inline subckt`` ... ``ends``, a ``library`` / ``section``, a brace block (``sweep``, ``montecarlo``,
    ``if``) or a ``simulator lang=spice`` stretch. An info statement counts when it has ``what=oppoint`` and
    ``where=rawfile`` (any order, quoted or not) and the analysis before it is a plain DC (no sweep); the first such is
    taken. Otherwise the info statement goes right after the first plain DC analysis -- an ``info`` computes nothing, so
    the analyses keep their results -- and without one a DC analysis and the statement are appended at the end, where they
    run after every analysis of the netlist and so change none of them."""
    lines = text.splitlines(keepends=True)
    depth, braces, spectre = 0, 0, True
    previous: tuple[str, bool] | None = None      # the last top-level analysis: (name, plain DC)
    first_dc: tuple[str, int] | None = None       # (name, index of its last line)
    for indices, code in _statements(lines):
        bare, params = _parse(code)
        if not bare:
            continue
        head = bare[1] if bare[0] == "inline" and len(bare) > 1 else bare[0]
        top = depth == 0 and braces == 0
        braces = max(0, braces + code.count("{") - code.count("}"))
        if head == "simulator" and "lang" in params:
            spectre = params["lang"] == "spectre"
            continue
        if head in _OPENERS:
            depth += 1
            continue
        if head in _CLOSERS:
            depth = max(0, depth - 1)
            continue
        if not (top and spectre) or len(bare) != 2:
            continue
        name, kind = bare
        if kind in _ANALYSES:
            plain_dc = kind == "dc" and not (_DC_SWEEP_KEYS & params.keys()) and "{" not in code
            previous = (name, plain_dc)
            if plain_dc and first_dc is None:
                first_dc = (name, indices[-1])
        elif (kind == "info" and params.get("what") == "oppoint" and params.get("where") == "rawfile"
              and previous is not None and previous[1] and _IDENT_RE.match(name)):
            return OperatingPoints("export", name)
    if first_dc is not None:
        return OperatingPoints("statement", OP_INFO_NAME, after=first_dc[0], insert_at=first_dc[1] + 1)
    return OperatingPoints("analysis", OP_INFO_NAME, spice_at_end=not spectre)


def with_operating_points(text: str) -> tuple[str, OperatingPoints]:
    """``text`` with what :func:`operating_points` says to add, and that answer; otherwise byte for byte ``text`` (a final
    line without a newline gets one before the added lines)."""
    plan = operating_points(text)
    if plan.mode == "export":
        return text, plan
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += "\n"
    at = len(lines) if plan.insert_at is None else plan.insert_at
    return "".join(lines[:at]) + "".join(f"{line}\n" for line in plan.added) + "".join(lines[at:]), plan


def _statements(lines: list[str]):
    """(line indices, code without comments) per logical statement: ``//`` to the end of the line outside a string, a
    line starting with ``*`` whole, and a ``\\`` at the end joining the next line."""
    indices: list[int] = []
    code = ""
    for index, line in enumerate(lines):
        indices.append(index)
        body = _strip_comment(line.rstrip("\r\n"))
        joined = body.rstrip().endswith("\\")
        code += (body.rstrip()[:-1] if joined else body) + " "
        if not joined:
            yield indices, code
            indices, code = [], ""
    if indices:
        yield indices, code


def _strip_comment(line: str) -> str:
    if line.lstrip().startswith("*"):
        return ""
    quoted = False
    for i, c in enumerate(line):
        if c == '"':
            quoted = not quoted
        elif not quoted and line.startswith("//", i):
            return line[:i]
    return line


def _parse(code: str) -> tuple[list[str], dict[str, str]]:
    """The bare words of a statement and its ``key=value`` parameters (spaces around ``=`` allowed, quotes dropped)."""
    code = re.sub(r"\s*=\s*", "=", code)
    params = {m.group(1): m.group(2).strip('"') for m in _PARAM_RE.finditer(code)}
    return _PARAM_RE.sub(" ", code).split(), params


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
