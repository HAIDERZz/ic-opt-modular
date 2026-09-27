"""A benchmark problem: a real ``Spec`` plus the function that stands in for the simulators.

A strategy sees a benchmark problem exactly as it sees a project: through ``ic_opt.blocks.optimize.suggest`` and the
observations that ``ic_opt.sim.corner.aggregate`` forms from the children. Nothing here proposes or scores points.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from ic_opt.observation import ChildResult, Observation
from ic_opt.sim.corner import aggregate
from ic_opt.space import Point
from ic_opt.spec import Spec

STAMP = "2026-01-01T00:00:00+00:00"                  # observations of a benchmark carry no clock

Evaluate = Callable[[dict[str, str]], dict[str, ChildResult]]   # grid params -> children keyed "<unit>/nominal"


@dataclass(frozen=True)
class Problem:
    name: str                                        # unique: "syn_ackley10_c2", "ag_amp_nmcf_wide", ...
    family: str                                      # "synthetic" | "analoggym"
    scenario: str                                    # "around_design" | "wide_range"
    spec: Spec
    evaluate: Evaluate                               # deterministic: the same params give the same children
    start: tuple[dict[str, str], ...] = ()           # the design as it stands (grid params); empty when there is none
    reference: float | None = None                   # best known objective, minimization form; None when unknown
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


def make_spec(name: str, *, variables: Sequence[dict], metrics: Sequence[dict], constraints: Sequence[dict],
              objective: dict, description: str = "") -> Spec:
    """A ``Spec`` for a benchmark: the problem's variables, metrics (each names its ``testbench``), constraints and
    objective, with a placeholder testbench per unit and placeholder simulator resources. The placeholders are never
    used: a benchmark's children come from ``Problem.evaluate``."""
    units = list(dict.fromkeys(m["testbench"] for m in metrics))
    return Spec.model_validate({
        "project": name,
        "description": description,
        "testbenches": [{"id": u, "maestro_point_root": f"/benchmark/{name}/{u}", "virtuoso_library": "benchmark",
                         "cell": name, "test_name": u} for u in units],
        "variables": list(variables),
        "metrics": [{"expression": "1", **m} for m in metrics],
        "constraints": list(constraints),
        "objective": objective,
        "simulator": {"threads_per_run": 1, "parallel_jobs": 1, "timeout_s": 3600},
        "budget": {"max_simulations": 10**9},
    })


def child(unit: str, metrics: dict[str, float], *, missing: Sequence[str] = (), failed: str | None = None,
          seconds: float = 0.0) -> ChildResult:
    """One unit's result at the single condition of stage 1. ``missing`` names metrics that produced no value (the unit
    ran: ``metric_failed``); ``failed`` names the stage that did not run to its end (``failed:<stage>``)."""
    if failed is not None:
        return ChildResult(unit=unit, corner=None, status=f"failed:{failed}", issues=[f"{failed} did not finish"], seconds=seconds)
    if missing:
        return ChildResult(unit=unit, corner=None, status="metric_failed", metrics=dict(metrics),
                           issues=[f"metric {m} failed: no_value:nil" for m in missing], seconds=seconds)
    return ChildResult(unit=unit, corner=None, status="ok", metrics=dict(metrics), seconds=seconds)


def observe(problem: Problem, point: Point, index: int) -> Observation:
    """The observation the engine would have written for ``point``."""
    children = problem.evaluate(point.params)
    agg = aggregate(problem.spec, children)
    return Observation(
        obs_id=f"obs_{index:04d}", params=point.params, origin=point.origin, children=children, metrics=agg.metrics,
        fom=agg.fom, objective=agg.objective, feasible=agg.feasible, constraint_penalty=agg.constraint_penalty,
        status=agg.status, issues=agg.issues, spec_fingerprint="benchmark", pipeline_fingerprint="benchmark",
        step="benchmark", simulations=len(children), started_at=STAMP, finished_at=STAMP,
    )
