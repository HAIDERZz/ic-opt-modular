"""The spec's objective and constraints on arrays of metric values (T17.1 specification, section 5).

The strategy models metrics, never a score: whatever the objective and the constraints make of the metrics is computed
here, by the spec's own formulas, on the true values of the history and on model samples alike. ``nan`` in an
objective or a residual marks that value as not scored. The history's values are its points' metrics as
``sim.corner.worst_metrics`` gives them: a point evaluated at several corners, each metric at its worst (T17.9).
"""

from __future__ import annotations

import ast
import math

import numpy as np

from ic_opt.objective import evaluate_expression_array
from ic_opt.observation import Observation
from ic_opt.sim.corner import worst_metrics
from ic_opt.space import parse_scalar
from ic_opt.spec import Spec

SCALE_FLOOR = 1e-12


def modelled_metrics(spec: Spec) -> list[str]:
    """The metrics named in the constraints or in the objective expression, in spec order."""
    named = {c.metric for c in spec.constraints}
    if spec.objective is not None:
        tree = ast.parse(spec.objective.expression, mode="eval")
        called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        named |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in called}
    return [m.name for m in spec.metrics if m.name in named]


def metric_scales(spec: Spec, history: list[Observation]) -> dict[str, float]:
    """Per modelled metric the standard deviation of its finite observed values (1 when it has none, at least 1e-12):
    violations are normalized by the metric's own spread, so a constraint whose threshold is 0 or tiny does not
    dominate the sum. The values are the history's as the models see them (:func:`true_arrays`)."""
    scales = {}
    viewed = [worst_metrics(spec, o) for o in history]
    for name in modelled_metrics(spec):
        values = [m[name] for m in viewed if name in m and math.isfinite(m[name])]
        scales[name] = max(float(np.std(values)), SCALE_FLOOR) if values else 1.0
    return scales


class Composer:
    """``arrays`` map each modelled metric to an array; every array in a call broadcasts to the same shape."""

    def __init__(self, spec: Spec) -> None:
        self.spec = spec
        self.constraints = [(c.metric, c.op in ("lt", "le"), float(parse_scalar(c.value.replace(" ", ""))[0]))
                            for c in spec.constraints]

    def residuals(self, arrays: dict[str, np.ndarray]) -> np.ndarray:
        """``(..., constraints)``: ``value - threshold`` for lt / le, ``threshold - value`` for gt / ge; a constraint
        holds where its residual is <= 0."""
        columns = [(np.asarray(arrays[m], dtype=float) - t) if upper else (t - np.asarray(arrays[m], dtype=float))
                   for m, upper, t in self.constraints]
        if not columns:
            shape = np.broadcast_shapes(*(np.shape(a) for a in arrays.values())) if arrays else ()
            return np.zeros((*shape, 0))
        return np.stack(np.broadcast_arrays(*columns), axis=-1)

    def violation(self, arrays: dict[str, np.ndarray], scales: dict[str, float]) -> np.ndarray:
        """``sum_i max(0, residual_i) / scale_i``; nan where a residual is nan."""
        return (np.maximum(self.residuals(arrays), 0.0) / self.scale_vector(scales)).sum(axis=-1)

    def known_violation(self, arrays: dict[str, np.ndarray], scales: dict[str, float]) -> np.ndarray:
        """:meth:`violation` over the residuals that are known: a nan one -- the metric of a child a point stopped early
        never ran (T17.8) -- counts as 0, so the result is never nan. Equal to :meth:`violation` where nothing is nan."""
        return np.nansum(np.maximum(self.residuals(arrays), 0.0) / self.scale_vector(scales), axis=-1)

    def objective(self, arrays: dict[str, np.ndarray], scales: dict[str, float]) -> np.ndarray:
        """The objective in minimization form (negated for maximize). Without an objective, the negative of the smallest
        normalized margin ``-residual_i / scale_i``: a feasible point is pushed away from its nearest constraint."""
        if self.spec.objective is not None:
            value = evaluate_expression_array(self.spec.objective.expression, arrays)
            return -value if self.spec.objective.direction == "maximize" else value
        margins = -self.residuals(arrays) / self.scale_vector(scales)
        if margins.shape[-1] == 0:
            return np.zeros(margins.shape[:-1])
        return -margins.min(axis=-1)

    def scale_vector(self, scales: dict[str, float]) -> np.ndarray:
        return np.array([scales[m] for m, _upper, _t in self.constraints], dtype=float)


def true_arrays(spec: Spec, rows: list[Observation]) -> dict[str, np.ndarray]:
    """The history's true metric values per modelled metric (nan where a row has none): ``sim.corner.worst_metrics`` of
    each row -- its own metrics at one corner, each metric at its worst at several."""
    viewed = [worst_metrics(spec, o) for o in rows]
    return {name: np.array([m.get(name, math.nan) for m in viewed], dtype=float) for name in modelled_metrics(spec)}
