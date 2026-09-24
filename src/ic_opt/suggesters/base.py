"""Suggester protocol: stateless "given these observations, propose n raw vectors".

Every call rebuilds whatever model it needs from the observation list, so
continuation and warm start are not special cases — they are just more rows.
Raw vectors live in the numeric space of :func:`ic_opt.space.bounds`; the
``suggest`` block snaps them to the grid and removes duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from ic_opt import space
from ic_opt.observation import Observation, Observations
from ic_opt.spec import Spec

# TuRBO (the `turbo` strategy, `latin_hypercube` designs) is vendored in the checkout, not shipped in the package:
# it is under Uber's non-commercial licence. It is installed from the checkout like OpenBox and imported normally.
TURBO_INSTALL = 'uv pip install -e ".[turbo]" -e vendor/TuRBO'


@dataclass
class Proposal:
    raw: list[list[float]]
    tag: str = ""            # provenance appended to Point.origin, e.g. "init:0" / "tr:0"


class Suggester(Protocol):
    name: str

    def propose(self, spec: Spec, history: Observations, n: int, *, seed: int) -> Proposal: ...


def penalized_objective(obs: Observation, failure_penalty: float) -> float:
    """Minimization target a model can consume for any observation (legacy semantics)."""
    if obs.objective is not None:
        return obs.objective
    if obs.status == "constraint_failed":
        return failure_penalty + obs.constraint_penalty
    return failure_penalty


def history_arrays(spec: Spec, history: Observations, failure_penalty: float) -> tuple[np.ndarray, np.ndarray]:
    x = np.array([space.to_raw(spec, o.params) for o in history], dtype=float).reshape(len(history), len(spec.variables))
    y = np.array([penalized_objective(o, failure_penalty) for o in history], dtype=float)
    return x, y


def unit_design(method: str, n: int, dim: int, seed: int) -> np.ndarray:
    """``(n, dim)`` samples in the unit cube: sobol (seeded scramble), latin_hypercube (TuRBO's), random."""
    if method == "sobol":
        from scipy.stats.qmc import Sobol

        return Sobol(d=dim, scramble=True, seed=seed).random(n=n)
    if method == "latin_hypercube":
        try:
            from turbo.utils import latin_hypercube
        except ImportError as exc:
            raise turbo_missing("method 'latin_hypercube'") from exc

        return latin_hypercube(n, dim)
    if method == "random":
        return np.random.default_rng(seed).random((n, dim))
    raise ValueError(f"unknown initialization method {method!r}")


def scale(unit: np.ndarray, spec: Spec) -> list[list[float]]:
    lb, ub = (np.array(b, dtype=float) for b in space.bounds(spec))
    return (lb + unit * (ub - lb)).tolist()


def turbo_missing(what: str) -> ImportError:
    """The error for ``what`` (e.g. "strategy 'turbo'") when ``import turbo`` fails: what to install, and the licence."""
    return ImportError(f"{what} needs TuRBO, which is vendored in the ic-opt checkout (vendor/TuRBO, Uber's non-commercial "
                       "licence) rather than shipped, and torch + gpytorch from the `turbo` extra; install both from the "
                       f"checkout: {TURBO_INSTALL}")
