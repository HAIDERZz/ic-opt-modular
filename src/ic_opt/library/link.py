"""The tables of allowed combinations a spec's library devices put on its variables (T18.2A specification, section 1,
``docs/refactor/T18_2A_ALLOWED_COMBINATIONS_SPEC.md``; filled in by T18.2B).

:func:`tables` is what ``ic_opt.space.tables`` returns. The contract: one ``ic_opt.space.Table`` per library device of
the spec -- ``names``: the device's variables (say ``xfmr.Lp``, ``xfmr.Ls``, ``xfmr.k``), in the spec's order;
``levels``: the combinations of their level indices (``k`` for ``lower + k * step``) that a row of the device's library
sits on, each row at its own electrical values taken onto the variables' grid, distinct and sorted; ``label``: what a
message calls the table, e.g. "device xfmr (library rows)". The variables keep their ordinary grids; only those
combinations of their levels exist. ``ic_opt.space`` checks what comes back where it is first used (every name a variable
of the spec and in one table only, every level index within its variable's levels, no table empty) and is the only
reader: the space, the strategies and the point blocks know tables, never libraries.

Until T18.2B no spec has a library device and there is no table: every grid point is a point of the space. T18.2B reads
the spec's library devices here and caches the tables per process.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ic_opt.space import Table
    from ic_opt.spec import Spec


def tables(spec: Spec) -> list[Table]:
    """The tables of allowed combinations of ``spec``'s variables: none until T18.2B."""
    return []
