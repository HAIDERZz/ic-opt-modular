"""TuRBO-1 trust-region suggester, rebuilt from the observation list on every call.

Provenance tags on the points it proposes (``init:<r>:<k>`` / ``tr:<r>:<k>``,
r = restart index, k = history size when the batch was proposed) let the next call replay the trust-region history exactly the way the
legacy ``_restore_trace_history`` did: untagged rows (user points, other
strategies, warm-start rows) count as the first initial design; each ``tr``
batch replays ``_adjust_length``; a restart begins a fresh region.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy

import numpy as np

from ic_opt import space
from ic_opt.observation import Observations
from ic_opt.spec import Spec
from ic_opt.suggesters.base import (
    Proposal,
    ensure_turbo_importable,
    history_arrays,
    scale,
    unit_design,
)


class TurboSuggester:
    name = "turbo"

    def __init__(self, *, failure_penalty: float = 1e6, initialization: str = "latin_hypercube", n_init: int | None = None,
                 n_training_steps: int = 50) -> None:
        self.failure_penalty = failure_penalty
        self.initialization = initialization
        self.n_init = n_init
        self.n_training_steps = n_training_steps

    def propose(self, spec: Spec, history: Observations, n: int, *, seed: int) -> Proposal:
        ensure_turbo_importable()
        from turbo import Turbo1
        from turbo.utils import from_unit_cube, to_unit_cube

        dim = len(spec.variables)
        n_init = self.n_init or 2 * dim
        _seed_everything(seed + len(history))     # TuRBO's LHS and GP fit use numpy / torch global RNGs
        lb, ub = (np.array(b, dtype=float) for b in space.bounds(spec))
        turbo = Turbo1(f=lambda _x: math.inf, lb=lb, ub=ub, n_init=n_init, max_evals=10**9, batch_size=n,
                       verbose=False, n_training_steps=self.n_training_steps)

        groups = _batches(history)
        restart = _restart_index(groups)
        active = groups[_active_start(groups):]
        x_all, y_all = history_arrays(spec, history, self.failure_penalty)
        turbo._restart()
        turbo._X, turbo._fX = np.empty((0, dim)), np.empty((0, 1))
        for kind, rows in active:
            xs, ys = history_arrays(spec, Observations(rows), self.failure_penalty)
            if kind == "tr" and len(turbo._fX):
                turbo._adjust_length(ys.reshape(-1, 1))
            turbo._X = np.vstack((turbo._X, xs))
            turbo._fX = np.vstack((turbo._fX, ys.reshape(-1, 1)))
        turbo.X, turbo.fX, turbo.n_evals = deepcopy(x_all), deepcopy(y_all.reshape(-1, 1)), len(history)

        init_done = sum(len(rows) for kind, rows in active if kind == "init" and _tag(rows[0].origin) == ("init", restart))
        region_dead = len(turbo._fX) and turbo.length < turbo.length_min
        if not len(turbo._fX) or region_dead or init_done < n_init:
            if region_dead:
                restart += 1
                init_done = 0
            design = unit_design(self.initialization, n_init, dim, seed + restart)
            chunk = design[init_done : init_done + n]
            if len(chunk) == 0:
                chunk = unit_design("random", n, dim, seed + restart + len(history))
            return Proposal(scale(chunk, spec), tag=f"init:{restart}:{len(history)}")

        x_unit = to_unit_cube(deepcopy(turbo._X), lb, ub)
        y = deepcopy(turbo._fX).ravel()
        x_cand, y_cand, _ = turbo._create_candidates(x_unit, y, length=turbo.length, n_training_steps=self.n_training_steps, hypers={})
        x_next = from_unit_cube(turbo._select_candidates(x_cand, y_cand), lb, ub)
        return Proposal(x_next.tolist(), tag=f"tr:{restart}:{len(history)}")


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
