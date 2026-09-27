"""TuRBO-1 trust-region suggester, rebuilt from the observation list on every call.

Provenance tags on the points it proposes (``init:<r>:<k>`` / ``tr:<r>:<k>``,
r = restart index, k = history size when the batch was proposed) let the next call replay the trust-region history exactly the way the
legacy ``_restore_trace_history`` did: untagged rows (start points, user points, other
strategies, warm-start rows) count as initial design of the region they fall in (T17.0b: they are its first design
points, and TuRBO's own design is served only for the rest); each ``tr``
batch replays ``_adjust_length``; a restart begins a fresh region.

Targets (T17.0b, 2026-09-28). TuRBO is unconstrained: it needs one number per point, which it standardizes as
``(y - median) / std``. With the 0.4.0 feed (``1e6`` for every infeasible or failed point) that squeezed the spread among
the feasible points from 0.165 to 4e-7. :func:`targets` now builds them from the whole history on every call, on one
scale: with a feasible point, feasible points keep their objective and the others sit just above the worst of them,
ordered by how far they miss; without one, the target is how far a point misses.

The replay and that scale. The targets of earlier rows change as the history grows (the first feasible point moves every
infeasible one above it), so "this batch improved" during the replay is judged on the scale of the current call, for
every batch: a batch improved when its best target beats the best of the region's earlier rows on today's scale, i.e. in
the order feasible before infeasible, then by objective, infeasible ones by violation (clipped at 3, see ``targets``).
Decided so because the proposal must be a function of (history, seed) alone: the same history and seed give the same
proposal, and a continued run is an uninterrupted one, since an uninterrupted run rebuilds from the history on every call
too. The price: a region's replayed length may differ from the length in force when an earlier batch was proposed (a
batch that found the first feasible point counted as a failure then, on the violation scale, if it did not reduce the
violation; it counts as a success now). Keeping each batch's original scale would need the targets of every earlier
call, i.e. a second record beside the observations, which the suggesters must not keep (CONTRIBUTING).
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from copy import deepcopy

import numpy as np

from ic_opt import space
from ic_opt.observation import Observations
from ic_opt.spec import Spec
from ic_opt.suggesters.base import (
    Proposal,
    minimization_objective,
    scale,
    turbo_missing,
    unit_design,
)


class TurboSuggester:
    name = "turbo"

    def __init__(self, *, initialization: str = "latin_hypercube", n_init: int | None = None, n_training_steps: int = 50) -> None:
        self.initialization = initialization
        self.n_init = n_init
        self.n_training_steps = n_training_steps

    def propose(self, spec: Spec, history: Observations, n: int, *, seed: int,
                pending: Sequence[dict[str, str]] = ()) -> Proposal:
        try:
            from turbo import Turbo1
            from turbo.utils import from_unit_cube, to_unit_cube
        except ImportError as exc:                    # vendor/TuRBO, and torch / gpytorch from the `turbo` extra
            raise turbo_missing("strategy 'turbo'") from exc

        dim = len(spec.variables)
        n_init = self.n_init or 2 * dim
        _seed_everything(seed + len(history))     # TuRBO's LHS and GP fit use numpy / torch global RNGs
        lb, ub = (np.array(b, dtype=float) for b in space.bounds(spec))
        turbo = Turbo1(f=lambda _x: math.inf, lb=lb, ub=ub, n_init=n_init, max_evals=10**9, batch_size=n,
                       verbose=False, n_training_steps=self.n_training_steps)

        groups = _batches(history)
        restart = _restart_index(groups)
        active = groups[_active_start(groups):]
        y_all = targets(spec, history)
        target = {id(o): y for o, y in zip(history, y_all, strict=True)}
        turbo._restart()
        turbo._X, turbo._fX = np.empty((0, dim)), np.empty((0, 1))
        for kind, rows in active:
            xs = _raw(spec, rows)
            ys = np.array([target[id(o)] for o in rows], dtype=float).reshape(-1, 1)
            if kind == "tr" and len(turbo._fX):
                turbo._adjust_length(ys)
            turbo._X = np.vstack((turbo._X, xs))
            turbo._fX = np.vstack((turbo._fX, ys))
        turbo.X, turbo.fX, turbo.n_evals = _raw(spec, history), y_all.reshape(-1, 1), len(history)

        init_done = sum(len(rows) for kind, rows in active if kind == "init" and _tag(rows[0].origin) in (("init", restart), None))
        region_dead = len(turbo._fX) and turbo.length < turbo.length_min
        if not len(turbo._fX) or region_dead or init_done < n_init:
            if region_dead:
                restart += 1
                init_done = 0
            design = unit_design(self.initialization, n_init, dim, seed + restart)
            chunk = design[init_done + len(pending) : init_done + len(pending) + n]    # this batch's start points are design too
            if len(chunk) == 0:
                chunk = unit_design("random", n, dim, seed + restart + len(history))
            return Proposal(scale(chunk, spec), tag=f"init:{restart}:{len(history)}")

        x_unit = to_unit_cube(deepcopy(turbo._X), lb, ub)
        y = deepcopy(turbo._fX).ravel()
        x_cand, y_cand, _ = turbo._create_candidates(x_unit, y, length=turbo.length, n_training_steps=self.n_training_steps, hypers={})
        x_next = from_unit_cube(turbo._select_candidates(x_cand, y_cand), lb, ub)
        return Proposal(x_next.tolist(), tag=f"tr:{restart}:{len(history)}")


def targets(spec: Spec, history: Observations) -> np.ndarray:
    """One minimization target per row, from the whole history, on one scale.

    With at least one feasible point: a feasible point's objective (``base.minimization_objective``); a
    ``constraint_failed`` one ``worst + (1 + min(v, 3)) * spread``, where ``worst`` / ``best`` are the largest / smallest
    feasible objectives, ``spread = max(worst - best, 1e-3 * max(1, |best|))`` and ``v = sqrt(constraint_penalty)`` (the
    root of the summed squared normalized violations, ``objective.constraint_violations``): infeasible points sit one to
    four spreads above the worst feasible one, so the standardization keeps the feasible points' spread, and violations
    beyond three times the threshold tie; a failed point the largest target among all other points.
    With none: a ``constraint_failed`` point ``v``; a failed point the largest ``v`` seen (1.0 when there is none)."""
    values = [minimization_objective(spec, o) for o in history]
    violation = np.array([math.sqrt(o.constraint_penalty) if o.status == "constraint_failed" else math.nan for o in history])
    feasible = np.array([v for v, o in zip(values, history, strict=True) if o.status == "ok" and v is not None])
    y = np.full(len(history), math.nan)
    infeasible = ~np.isnan(violation)
    if len(feasible):
        best, worst = float(feasible.min()), float(feasible.max())
        spread = max(worst - best, 1e-3 * max(1.0, abs(best)))
        for i, (v, o) in enumerate(zip(values, history, strict=True)):
            if o.status == "ok" and v is not None:
                y[i] = v
        y[infeasible] = worst + (1.0 + np.minimum(violation[infeasible], 3.0)) * spread
    else:
        y[infeasible] = violation[infeasible]
    failed = np.isnan(y)
    if failed.any():
        y[failed] = np.max(y[~failed]) if (~failed).any() else 1.0
    return y


def _raw(spec: Spec, rows) -> np.ndarray:
    return np.array([space.to_raw(spec, o.params) for o in rows], dtype=float).reshape(len(rows), len(spec.variables))


def _seed_everything(seed: int) -> None:
    import torch

    np.random.seed(seed % (2**32))
    torch.manual_seed(seed)


_TAG_RE = re.compile(r"^suggest:turbo:(?P<kind>init|tr):(?P<restart>\d+):(?P<k>\d+)$")


def _tag(origin: str) -> tuple[str, int] | None:
    """('init'|'tr', restart) for a TuRBO-tagged origin, None for any other row."""
    match = _TAG_RE.match(origin)
    return (match.group("kind"), int(match.group("restart"))) if match else None


def _batches(history: Observations) -> list[tuple[str, list]]:
    """Group rows into TuRBO batches: (kind, rows). Untagged rows form 'init' groups."""
    groups: list[tuple[str, str, list]] = []
    for obs in history:
        tagged = _tag(obs.origin)
        kind = tagged[0] if tagged else "init"
        key = obs.origin if tagged else "untagged"
        if groups and groups[-1][1] == key:
            groups[-1][2].append(obs)
        else:
            groups.append((kind, key, [obs]))
    return [(kind, rows) for kind, _key, rows in groups]


def _active_start(groups: list[tuple[str, list]]) -> int:
    """Index of the group that begins the active trust region (the latest tagged restart)."""
    start, current = 0, -1
    for index, (kind, rows) in enumerate(groups):
        tagged = _tag(rows[0].origin)
        if tagged and tagged[0] == "init" and tagged[1] > current:
            start, current = index, tagged[1]
    return start


def _restart_index(groups: list[tuple[str, list]]) -> int:
    for _kind, rows in reversed(groups):
        tagged = _tag(rows[-1].origin)
        if tagged:
            return tagged[1]
    return 0
