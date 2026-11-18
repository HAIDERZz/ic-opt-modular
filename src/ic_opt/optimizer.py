"""Propose parameter batches and score results supplied by the caller's evaluator.

The caller owns execution, scheduling and storage. Pass completed observations to
ask and include outstanding trials when asking before a batch has finished.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from pydantic import Field, field_validator

from ic_opt import space
from ic_opt.blocks.optimize import suggest
from ic_opt.observation import ChildResult, Observation
from ic_opt.sim.corner import aggregate
from ic_opt.spec import Model, OptimizationProblem, ScalarMetric
from ic_opt.store import digest, utc_now


class Problem(OptimizationProblem):
    """A grid, externally reported scalar metrics, constraints and an objective.

    Set evaluator to a version of the measurement procedure when its meaning can
    change. It is part of the identity used to match trials and history.
    """

    metrics: list[ScalarMetric] = Field(min_length=1)
    evaluator: str = Field(default="external", min_length=1)

    @field_validator("metrics", mode="before")
    @classmethod
    def _names(cls, values):
        return [{"name": value, "unit": "1"} if isinstance(value, str) else value for value in values]

    @field_validator("corners", mode="before")
    @classmethod
    def _corners(cls, values):
        return [{"id": value} if isinstance(value, str) else value for value in values]


class Trial(Model):
    """A proposed point, with its issue order and the origin used by the algorithm."""
    obs_id: str = Field(pattern=r"^obs_[0-9]+$")
    params: dict[str, str]
    origin: str
    problem_id: str
    issued_at: str


class Result(Model):
    """An evaluator's scalar outputs for one condition, or the reason evaluation failed."""
    metrics: dict[str, float | None] = Field(default_factory=dict)
    error: str | None = Field(default=None, min_length=1)


def ask(problem: Problem, history: Sequence[Observation], n: int = 1, *,
        pending: Sequence[Trial] = (), strategy: str = "metric_gp", seed: int = 0,
        start: Sequence[dict[str, str]] = (), total: int | None = None, **strategy_kwargs) -> list[Trial]:
    """Propose up to n new points. With identical inputs the parameters and origins repeat.

    total is the planned point budget for sizing the initial design, as in opt.optimize.
    A caller collecting results in parallel can supply history in completion order.
    """
    if n < 1:
        raise ValueError("n must be positive")
    fingerprint = problem.fingerprint()
    order: set[int] = set()
    for row in history:
        if row.spec_fingerprint != fingerprint:
            raise ValueError(f"{row.obs_id} belongs to a different problem or evaluator")
    for trial in pending:
        if trial.problem_id != fingerprint:
            raise ValueError(f"{trial.obs_id} belongs to a different problem or evaluator")
    for row in (*history, *pending):
        prefix, sep, number = row.obs_id.partition("_")
        if prefix != "obs" or not sep or not number.isdecimal() or int(number) in order:
            raise ValueError(f"invalid or repeated trial ID {row.obs_id!r}")
        order.add(int(number))
    history = sorted(history, key=lambda row: int(row.obs_id[4:]))
    points = suggest(problem, history, n, strategy=strategy, seed=seed, start=start, total=total,
                     pending=[trial.params for trial in pending], **strategy_kwargs)
    issued_at = utc_now()
    return [Trial(obs_id=f"obs_{i:04d}", params=point.params, origin=point.origin, problem_id=fingerprint,
                  issued_at=issued_at) for i, point in enumerate(points, max(order, default=0) + 1)]


def _child(problem: Problem, corner: str | None, values: Mapping[str, float | None] | Result) -> ChildResult:
    result = values if isinstance(values, Result) else Result(metrics=dict(values))
    declared = {m.name for m in problem.metrics}
    unknown = result.metrics.keys() - declared
    if unknown:
        raise ValueError(f"unknown metrics: {', '.join(sorted(unknown))}")
    metrics = {k: v for k, v in result.metrics.items() if v is not None and math.isfinite(v)}
    if result.error:
        return ChildResult(unit="external", corner=corner, metrics=metrics, status="failed:external", issues=[result.error])
    missing = declared - metrics.keys()
    return ChildResult(unit="external", corner=corner, metrics=metrics, status="metric_failed" if missing else "ok",
                       issues=[f"metric {key} missing or non-finite" for key in sorted(missing)])


def tell(problem: Problem, trial: Trial, metrics: Mapping[str, float | None] | Result | None = None, *,
         by_corner: Mapping[str, Mapping[str, float | None] | Result] | None = None) -> Observation:
    """Turn a trial and external measurements into a native, scored observation.

    Pass scalar metrics at one condition or results keyed by corner. An unrun corner
    may be omitted after a recorded failure. A successful partial PVT result cannot
    certify the design. Store the returned observation once, keyed by obs_id.
    """
    if trial.problem_id != problem.fingerprint():
        raise ValueError(f"{trial.obs_id} belongs to a different problem or evaluator")
    space.check(problem, trial.params)
    if (metrics is None) == (by_corner is None):
        raise ValueError("provide either metrics or by_corner")
    corners = {key or "nominal": key for key in problem.corner_ids}
    if metrics is not None:
        if len(corners) != 1:
            raise ValueError("provide by_corner for a problem with several corners")
        by_corner = {next(iter(corners)): metrics}
    if not by_corner or by_corner.keys() - corners.keys():
        raise ValueError(f"by_corner must name one or more configured corners: {', '.join(corners)}")
    children = {f"external/{key}": _child(problem, corner, by_corner[key]) for key, corner in corners.items()
                if key in by_corner}
    wanted = [f"external/{key}" for key in corners]
    score = aggregate(problem, children, wanted)
    return Observation(obs_id=trial.obs_id, params=trial.params, origin=trial.origin, children=children,
                       not_run=[key for key in wanted if key not in children], metrics=score.metrics,
                       status=score.status, feasible=score.feasible, fom=score.fom, objective=score.objective,
                       constraint_penalty=score.constraint_penalty, issues=score.issues,
                       spec_fingerprint=trial.problem_id, pipeline_fingerprint=digest({"external": problem.evaluator}),
                       step="external", started_at=trial.issued_at, finished_at=utc_now())
