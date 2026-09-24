"""Metal positions: the one place a conductor name becomes a stack position and back (T13.11).

Generators address metals by stack position -- 1 is the bottom conductor, the ground-fixture layer; "one level
below" is position - 1 -- and resolve names through ``index`` / ``name``. Two modes:

* profile mode, inside ``use_stack(profile)`` (every generator entry point and audit with a process profile opens
  one): the positions are the profile's ``metal_stack`` (its catalog conductors with a width rule, bottom first).
  Any number of metals and any names work -- RDL, UTM ... A name resolves exactly (case-insensitive); ``"<n>"``
  (or ``"m05"``) is the metal named M<n> -- as the configs' name check reads it -- and only when no metal has that
  name, the n-th metal. The two differ on a stack whose M<n> is not its n-th metal (a bottom metal called LI puts
  M5 sixth, T16 N-13). The existing profiles keep their numbers: M<n> is n and AP is 11 on the 10-metal stack; on
  the 9-metal one AP is 10, where the fixed convention left an empty 10 that the code skipped by name, so the
  conductors the generators reach are the same.
* reference mode (no profile): the fixed 1P10M+AP convention, "M<n>" / "<n>" -> n, "AP" -> 11.

A string is a spelling, read as above; an integer is already a position -- the generators' own arithmetic ("one
level below" is position - 1) -- and passes through as it is, so a position handed on to a primitive is never
re-read as a name.

The same context carries the profile's manufacturing grid (``layout_rules.manufacturing_grid_um``, T16 R-23): the
pcell's snapping (``_pcell_core.ceiltogrid`` and the rest) reads ``grid_um()``, the reference 0.005 um outside a
profile. Both live in context variables, so concurrent builds in threads never see each other's profile.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
import numbers
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ic_opt.em.pcell.process_rules import ProcessRuleProfile

REFERENCE_SIZE = 11                                  # M1..M10 + AP
REFERENCE_GRID_UM = 0.005                            # the manufacturing grid outside a profile, and a profile's default
_ACTIVE: ContextVar[tuple[str, ...] | None] = ContextVar("ic_opt_pcell_metal_stack", default=None)
_GRID: ContextVar[float | None] = ContextVar("ic_opt_pcell_manufacturing_grid", default=None)


@contextlib.contextmanager
def use_stack(profile: ProcessRuleProfile | str | None) -> Iterator[tuple[str, ...] | None]:
    """Resolve metal names against ``profile``'s stack, and snap to its manufacturing grid, inside (a profile, a
    profile id, or None: reference mode)."""
    if isinstance(profile, str):
        from ic_opt.em.pcell.process_rules import get_process_rule_profile

        profile = get_process_rule_profile(profile)
    token = _ACTIVE.set(None if profile is None else tuple(profile.metal_stack))
    grid = _GRID.set(None if profile is None else profile.layout_rules.manufacturing_grid_um)
    try:
        yield _ACTIVE.get()
    finally:
        _GRID.reset(grid)
        _ACTIVE.reset(token)


def grid_um() -> float:
    """The manufacturing grid the active build snaps to, in um: the profile's inside ``use_stack``, else 0.005."""
    grid = _GRID.get()
    return REFERENCE_GRID_UM if grid is None else grid


def builds_on_profile_stack(fn: Callable) -> Callable:
    """Decorate a generator entry point with a ``process`` parameter: with a process context, metal names resolve
    against its profile's stack for the whole build (nested entry points reopen the same stack)."""
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def build(*args, **kwargs):
        process = signature.bind_partial(*args, **kwargs).arguments.get("process")
        if process is None:
            return fn(*args, **kwargs)
        with use_stack(process.adapter.profile):
            return fn(*args, **kwargs)

    return build


def active() -> tuple[str, ...] | None:
    return _ACTIVE.get()


def size() -> int:
    """How many stack positions exist: the active stack's length, or the reference 11."""
    names = _ACTIVE.get()
    return REFERENCE_SIZE if names is None else len(names)


def index(metal) -> int:
    """Name, spelling or position -> stack position. Raises ValueError for a token that is no conductor of the stack."""
    return position_in(_ACTIVE.get(), metal)


def position_in(names: tuple[str, ...] | None, metal) -> int:
    """``index`` on the given stack (``names`` bottom first; None: the reference convention) instead of the active one:
    for callers that judge a metal against a profile without building on it (the generator configs, T16 R-14).

    ``metal`` is a spelling (a name; ``"<n>"`` / ``"M<n>"``: the metal named M<n>, else the n-th metal) or an integer
    position, returned unchanged when the stack has it (T16 N-13)."""
    if isinstance(metal, numbers.Integral) and not isinstance(metal, bool):
        if names is not None and not 1 <= metal <= len(names):
            raise ValueError(f"metal {metal!r} is not a conductor of the stack {list(names)}")
        return int(metal)
    token = str(metal).strip()
    if names is not None:
        folded = {n.upper(): i for i, n in enumerate(names, 1)}
        if token.upper() in folded:
            return folded[token.upper()]
        digits = token[1:] if token[:1] in ("m", "M") else token
        if digits.isdigit():
            named = folded.get(f"M{int(digits)}")
            if named is not None:
                return named
            if 1 <= int(digits) <= len(names):
                return int(digits)
        raise ValueError(f"metal {metal!r} is not a conductor of the stack {list(names)}")
    if token.upper() == "AP":
        return REFERENCE_SIZE
    digits = token[1:] if token[:1] in ("m", "M") else token
    try:
        return int(digits)
    except ValueError:
        raise ValueError(f"metal {metal!r} is not a recognized conductor spelling") from None


def name(position: int) -> str:
    """Stack position -> conductor name. Outside the active stack the answer is a name no profile defines
    ("M<n>"), so a caller probing above the top or below the bottom finds nothing, exactly as with the fixed
    convention's missing layers."""
    return name_in(_ACTIVE.get(), position)


def name_in(names: tuple[str, ...] | None, position: int) -> str:
    """``name`` on the given stack (None: the reference convention) instead of the active one."""
    i = int(position)
    if names is not None:
        return names[i - 1] if 1 <= i <= len(names) else f"M{i}"
    return "AP" if i == REFERENCE_SIZE else f"M{i}"
