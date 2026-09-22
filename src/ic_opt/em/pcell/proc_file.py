"""The little of an EMX ``.proc`` file the profile has to agree with: conductor thicknesses by EMX name.

``conductor <thickness> <sheet-resistance> <NAME> ...`` lines (``assume microns``),
read as text -- no numbers are interpreted beyond that one column.
"""

from __future__ import annotations

import re

_CONDUCTOR = re.compile(r"^\s*conductor\s+(?P<thickness>[0-9]*\.?[0-9]+(?:[eE][-+]?\d+)?)\s+\S+\s+(?P<name>\w+)")


def conductor_thicknesses(text: str) -> dict[str, float]:
    """``{emx_name: thickness_um}`` for every ``conductor`` line; a name that repeats keeps its first value."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        m = _CONDUCTOR.match(line.split("#", 1)[0])
        if m and m.group("name") not in out:
            out[m.group("name")] = float(m.group("thickness"))
    return out


def stack_mismatches(profile_stack: dict[str, float], proc: dict[str, float], *, tolerance_um: float = 1e-6) -> list[str]:
    """One line per conductor whose profile thickness disagrees with the .proc (or is missing there)."""
    out = []
    for name, thickness in sorted(profile_stack.items()):
        if name not in proc:
            out.append(f"{name}: not a conductor in the .proc")
        elif abs(proc[name] - thickness) > tolerance_um:
            out.append(f"{name}: profile thickness {thickness:g} um, .proc {proc[name]:g} um")
    return out
