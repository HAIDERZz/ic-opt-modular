"""Advice: start points, narrower ranges and variables to hold, given to a run as data (T17.1.5 specification, sections 2,
3 and 6; decisions D7 and D7b of ``docs/refactor/T17_OPTIMIZER_PLAN_CN.md``).

ic-opt calls no language model. Whoever read what a run found -- a person, the user's own agent -- writes an advice
file; ``ic-opt advise`` checks it against the spec (:func:`check`) and appends one row to ``<project>/.icopt/advice.jsonl``,
append only. That file is the whole record: which advice applied to a batch follows from its rows and the batch's history
size alone (:func:`in_effect`), so a run replays without anyone, and a run without the file is the run it was before.

What an advice does (``blocks/optimize.py``, ``suggesters/metric_gp``): its start rows are evaluated first, by every
strategy, with the origin ``advice:<id>``; its ranges, fixed levels and ``vary`` narrow where ``metric_gp`` looks with
four fifths of a batch, and those points carry ``@<id>`` in their origin (``space.split_origin``). The other fifth keeps
looking over the spec's whole range (D7b): an advice changes where part of a batch looks, never what the spec allows.
The spec's ranges are the user's; an advice that reaches outside them is refused, not clipped.

A refused advice is recorded too (T17.10 specification, 1.5), so that whoever reads the next digest sees it: ``ic-opt
advise`` appends a row whose ``event`` is ``refuse`` (:func:`refusal`) -- ``status: "refused"``, the refusal's message
as ``reason``, the file's content as given as ``raw``. It is never in effect, takes no adopted advice's number, and
every reader of adopted advice passes over it; :func:`of_problem` keeps it with this problem's rows for the digest and
``--list``.

Nothing here reads the store's observations or takes a lock: ``blocks.optimize.advise`` does, and computes ``since``
(for a refused advice, the ``advise`` command).
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from ic_opt import space
from ic_opt.store import utc_now

if TYPE_CHECKING:
    from ic_opt.spec import Spec, Variable

FILE = "advice.jsonl"
PARTS = ("start", "ranges", "fixed", "vary")
NARROWING = ("ranges", "fixed", "vary")         # what only metric_gp takes; every strategy takes ``start``
REFUSE = "refuse"                               # the event of a refused advice's row (T17.10 specification, 1.5)
_FIELDS = ("author", "reason", *PARTS)


def path(root: str | Path) -> Path:
    """The advice file of a store whose root (``.icopt``) is ``root``."""
    return Path(root) / FILE


def read(root: str | Path) -> list[dict[str, Any]]:
    """Every row of the advice file, in order; none when there is no file (a run that was never advised)."""
    file = path(root)
    if not file.is_file():
        return []
    return [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]


def append(root: str | Path, row: dict[str, Any]) -> None:
    with path(root).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def of_problem(rows: Sequence[dict[str, Any]], fingerprints: set[str]) -> list[dict[str, Any]]:
    """The rows of advice given for this problem (``spec_fingerprint`` in ``fingerprints``, as ``opt.optimize`` tells its
    observations apart), the revokes that end them, and the rows of advice refused for it (``refuse``: their ids,
    ``r1``, ``r2``, ..., are no adopted advice's). A spec that changed is another problem: its history starts again,
    and an advice checked against the old spec -- its ``since``, its variables -- does not carry over."""
    ids = {r["id"] for r in rows if r["event"] == "adopt" and r["spec_fingerprint"] in fingerprints}
    return [r for r in rows if (r["event"] == REFUSE and r["spec_fingerprint"] in fingerprints)
            or (r["id"] in ids and (r["event"] == "revoke" or r["spec_fingerprint"] in fingerprints))]


def in_effect(rows: Sequence[dict[str, Any]], k: int) -> dict[str, Any] | None:
    """The advice in effect for a batch proposed at history size ``k`` (section 2): the last ``adopt`` row with ``since
    <= k``, unless a ``revoke`` row with ``since <= k`` ends it. Adopting an advice ends the one before, so a revoked
    advice leaves none in effect; a row whose ``since`` lies after ``k`` did not exist yet for that batch. A refused
    advice's row is neither: it ends nothing and is never in effect."""
    adopted = [r for r in rows if r["event"] == "adopt" and r["since"] <= k]
    if not adopted:
        return None
    last = adopted[-1]
    if any(r["event"] == "revoke" and r["id"] == last["id"] and r["since"] <= k for r in rows):
        return None
    return last


def narrows(row: dict[str, Any] | None) -> bool:
    """Does the advice narrow the search (ranges, fixed levels, vary), which only ``metric_gp`` takes?"""
    return bool(row) and any(row.get(part) for part in NARROWING)


# A file's content of the wrong shape is refused as any other finding (ValueError, TRY004 silenced): the command prints
# the message and exits 2, whatever the finding is.
def check(spec: Spec, raw: Any) -> tuple[dict[str, Any], list[str]]:
    """An advice file's content checked against ``spec`` (section 6.1): its fields as they will be recorded -- every
    value a level of the spec's grid, in the spec's text form -- and a note per value that was moved onto the grid.
    Refuses (ValueError, saying what to change) an unknown field or variable, a variable both fixed and ranged or fixed
    and varied, a range or value outside the spec's range, a range that holds no level, a start row that does not name
    every variable, an advice that names nothing, a missing author or reason.

    A value between levels goes to the nearest level in ``metric_gp``'s own unit coordinates, logarithmic where a range
    spans a decade (``suggesters/metric_gp/coords.py``): the level the strategy itself would snap it to. A range's bounds
    between levels move inward, so that the range never holds a level the advice did not cover."""
    from ic_opt.suggesters.metric_gp.coords import Coords

    if not isinstance(raw, dict):
        raise ValueError("an advice is a mapping: author, reason, and any of start, ranges, fixed, vary")  # noqa: TRY004
    unknown = sorted(set(raw) - set(_FIELDS))
    if unknown:
        raise ValueError(f"unknown field(s) {', '.join(unknown)}: an advice has {', '.join(_FIELDS)}")
    for key, what in (("author", "who gives the advice (a person's name, or 'agent: <model>')"),
                      ("reason", "why: the reason is recorded with the advice")):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise ValueError(f"{key} is missing: say {what}")
    ranges, fixed, vary, start = (raw.get(key) or empty for key, empty in
                                  (("ranges", {}), ("fixed", {}), ("vary", []), ("start", [])))
    for key, value, kind in (("ranges", ranges, dict), ("fixed", fixed, dict), ("vary", vary, list), ("start", start, list)):
        if not isinstance(value, kind):
            raise ValueError(f"{key} must be a {'mapping of variable: value' if kind is dict else 'list'}"  # noqa: TRY004
                             + (" of variable: [lower, upper]" if key == "ranges" else ""))
    if not (ranges or fixed or vary or start):
        raise ValueError("the advice names nothing: give start rows, ranges, fixed levels or variables to vary")
    variables = {v.name: v for v in spec.variables}
    named = [*ranges, *fixed, *vary, *(name for row in start if isinstance(row, dict) for name in row)]
    unknown = sorted({str(name) for name in named} - set(variables))
    if unknown:
        raise ValueError(f"unknown variable(s) {', '.join(unknown)}; the spec's variables are {', '.join(variables)}")
    for other, label in ((ranges, "ranges"), (vary, "vary")):
        both = sorted(set(fixed) & set(other))
        if both:
            raise ValueError(f"{', '.join(both)} in both fixed and {label}: a fixed variable has one level; "
                             f"take it out of one of them")

    coords = Coords(spec)
    index = {name: i for i, name in enumerate(variables)}
    notes: list[str] = []
    checked_ranges = {}
    for name, pair in ranges.items():
        variable = variables[name]
        if not isinstance(pair, list | tuple) or len(pair) != 2:
            raise ValueError(f"ranges: {name} takes [lower, upper], e.g. [{variable.lower}, {variable.upper}]")
        lo, hi = (_value(variable, v, f"ranges: {name}") for v in pair)
        lower, upper, step, unit = _grid(variable)
        text = f"[{pair[0]}, {pair[1]}]"
        if lo > hi:
            raise ValueError(f"ranges: {name} {text}: the lower bound is above the upper one")
        if lo < lower or hi > upper:
            raise ValueError(f"ranges: {name} {text} reaches outside the spec's range [{variable.lower}, {variable.upper}]: "
                             "an advice cannot widen what the spec allows; the spec's range is the user's to change "
                             "(spec.yaml)")
        first, last = math.ceil((lo - lower) / step), math.floor((hi - lower) / step)
        if first > last:
            raise ValueError(f"ranges: {name} {text} holds no level of the grid (step {variable.step}); widen it to "
                             f"at least one level")
        moved = [space.format_value(lower + first * step, unit), space.format_value(lower + last * step, unit)]
        if (lower + first * step, lower + last * step) != (lo, hi):
            notes.append(f"ranges: {name} {text}: bounds between levels moved inward to [{moved[0]}, {moved[1]}]")
        checked_ranges[name] = moved
    checked_fixed = {name: _level(coords, index[name], variables[name], value, f"fixed: {name}", notes)
                     for name, value in fixed.items()}
    checked_start = []
    for number, row in enumerate(start, 1):
        if not isinstance(row, dict):
            raise ValueError(f"start row {number} must be a mapping of variable: value")  # noqa: TRY004
        missing = [name for name in variables if name not in row]
        if missing:
            raise ValueError(f"start row {number} does not name every variable: missing {', '.join(missing)}")
        checked_start.append({name: _level(coords, index[name], variables[name], row[name], f"start row {number}: {name}",
                                           notes) for name in variables})
    return {"author": raw["author"].strip(), "reason": raw["reason"].strip(), "start": checked_start,
            "ranges": checked_ranges, "fixed": checked_fixed, "vary": list(dict.fromkeys(str(v) for v in vary))}, notes


def adoption(spec: Spec, raw: Any, rows: Sequence[dict[str, Any]], since: int) -> tuple[dict[str, Any], list[str]]:
    """The ``adopt`` row for an advice file's content (checked, :func:`check`) and the notes of the check. Its id is the
    next of ``a1``, ``a2``, ... (a refused advice takes none); ``since``: the observations of this problem the next
    batch's strategy will be handed."""
    fields, notes = check(spec, raw)
    number = 1 + sum(r["event"] == "adopt" for r in rows)
    return {"id": f"a{number}", "event": "adopt", "at": utc_now(), "since": since, **fields,
            "spec_fingerprint": spec.fingerprint()}, notes


def refusal(spec: Spec, raw: Any, message: str, rows: Sequence[dict[str, Any]], since: int) -> dict[str, Any]:
    """The row that records an advice file's content :func:`check` refused with ``message`` (T17.10 specification, 1.5):
    ``status: "refused"``, ``reason`` the message, ``raw`` the content as given -- its author and reason among it when
    it has them -- and ``since`` as an adoption's. Its id is the next of ``r1``, ``r2``, ...: the ids of adopted advice,
    and so every adopted row, are what they would have been without it."""
    number = 1 + sum(r["event"] == REFUSE for r in rows)
    return {"id": f"r{number}", "event": REFUSE, "status": "refused", "at": utc_now(), "since": since, "reason": message,
            "raw": _as_given(raw), "spec_fingerprint": spec.fingerprint()}


def revocation(rows: Sequence[dict[str, Any]], advice_id: str, reason: str | None, since: int) -> dict[str, Any]:
    """The ``revoke`` row that ends advice ``advice_id``. Only the advice adopted last can be ended: every earlier one
    was ended when the next was adopted."""
    if not reason or not reason.strip():
        raise ValueError("a revoke needs a reason: say why the advice ends (--reason)")
    adopted = [r["id"] for r in rows if r["event"] == "adopt"]
    if advice_id not in adopted:
        raise ValueError(f"no advice {advice_id} was adopted" + (f"; adopted: {', '.join(adopted)}" if adopted else ""))
    if advice_id != adopted[-1]:
        raise ValueError(f"{advice_id} is no longer in effect: {adopted[adopted.index(advice_id) + 1]} superseded it")
    if any(r["event"] == "revoke" and r["id"] == advice_id for r in rows):
        raise ValueError(f"{advice_id} is already revoked")
    return {"id": advice_id, "event": "revoke", "at": utc_now(), "since": since, "reason": reason.strip()}


def describe(row: dict[str, Any]) -> str:
    """One line: what an adopt row asks for."""
    parts = []
    if row["start"]:
        parts.append(f"{len(row['start'])} start row{'s' if len(row['start']) != 1 else ''}")
    if row["ranges"]:
        parts.append("ranges " + ", ".join(f"{name} [{lo}, {hi}]" for name, (lo, hi) in row["ranges"].items()))
    if row["fixed"]:
        parts.append("fixed " + ", ".join(f"{name}={value}" for name, value in row["fixed"].items()))
    if row["vary"]:
        parts.append("vary " + ", ".join(row["vary"]) + " (every other variable held at the search region's centre)")
    return "; ".join(parts)


def status(rows: Sequence[dict[str, Any]]) -> dict[str, str]:
    """Per adopted advice: ``in effect`` (the last adopted and not revoked; from its ``since`` on), ``revoked``, or
    ``superseded by <id>``."""
    adopted = [r["id"] for r in rows if r["event"] == "adopt"]
    revoked = {r["id"] for r in rows if r["event"] == "revoke"}
    return {advice_id: "revoked" if advice_id in revoked else
            f"superseded by {adopted[i + 1]}" if i + 1 < len(adopted) else "in effect"
            for i, advice_id in enumerate(adopted)}


def _as_given(value: Any) -> Any:
    """An advice file's content as a JSON line holds it: mappings (keys as text), lists, text, numbers, true / false and
    null as they are; anything else YAML reads -- a date, a number that is not finite -- as its text."""
    if isinstance(value, dict):
        return {str(key): _as_given(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_as_given(item) for item in value]
    if value is None or isinstance(value, bool | int | str) or (isinstance(value, float) and math.isfinite(value)):
        return value
    return str(value)


def _grid(variable: Variable) -> tuple[Decimal, Decimal, Decimal, str]:
    lower, unit = space.parse_scalar(variable.lower)
    return lower, space.parse_scalar(variable.upper)[0], space.parse_scalar(variable.step)[0], unit


def _value(variable: Variable, value: Any, what: str) -> Decimal:
    """A value of the advice as a number in the unit of the variable's range (the spec's text form: ``0.8u``)."""
    unit = _grid(variable)[3]
    try:
        number, suffix = space.parse_scalar(str(value).strip())
    except ValueError:
        suffix, number = None, None
    if number is None or suffix != unit:
        raise ValueError(f"{what}={value} must be a number in the unit of the spec's range "
                         f"({unit or 'no unit suffix'}), as in [{variable.lower}, {variable.upper}]")
    return number


def _level(coords, i: int, variable: Variable, value: Any, what: str, notes: list[str]) -> str:
    """The grid level of one value (fixed, or of a start row): refused outside the spec's range; between levels, the
    nearest level in the strategy's unit coordinates, and a note."""
    number = _value(variable, value, what)
    lower, upper, step, unit = _grid(variable)
    if not lower <= number <= upper:
        raise ValueError(f"{what}={value} is outside the spec's range [{variable.lower}, {variable.upper}]: an advice "
                         "cannot widen what the spec allows; the spec's range is the user's to change (spec.yaml)")
    if (number - lower) % step == 0:
        return space.format_value(number, unit)
    raw = np.array([levels[0] for levels in coords.raw_levels], dtype=float)
    raw[i] = float(number)
    level = space.format_value(lower + int(coords.snap(coords.unit_of_raw(raw))[0, i]) * step, unit)
    notes.append(f"{what}={value} lies between levels: moved to {level}")
    return level
