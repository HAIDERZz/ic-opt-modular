"""Corner aggregation: many testbench × corner child results -> one evaluation.

Rules (unchanged from the legacy aggregator):
- every child must succeed, otherwise the point failed at that child's stage;
- metrics of one corner are the union over its testbenches;
- ``constraints: all_corners`` fails the point if any corner violates,
  ``nominal`` looks at the nominal corner only;
- ``objective: worst_case`` reports the worst corner's objective,
  ``nominal`` the nominal corner's;
- the reported metrics are those of the selected (worst / nominal) corner.

The nominal corner is the one with id ``nominal`` if present, else the first
evaluated corner in spec order (a run may cover only a subset of the corners).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ic_opt import objective as objective_contract
from ic_opt.observation import ChildResult
from ic_opt.spec import Spec


@dataclass
class Aggregate:
    status: str                                   # "ok" | "metric_failed" | "constraint_failed" | "failed:<stage>"
    metrics: dict[str, float] = field(default_factory=dict)
    fom: float | None = None
    objective: float | None = None
    feasible: bool = False
    constraint_penalty: float = 0.0
    selected_corner: str | None = None
    corner_objectives: dict[str, float | None] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


def aggregate(spec: Spec, children: dict[str, ChildResult]) -> Aggregate:
    failed = [c for c in children.values() if c.status != "ok"]
    if failed:
        worst = failed[0]
        return Aggregate(
            status=worst.status,
            issues=[f"{c.unit}/{c.corner or 'nominal'}: {issue}" for c in failed for issue in c.issues],
        )

    cornered = [c for c in children.values() if c.corner is not None]
    shared = {k: v for c in children.values() if c.corner is None for k, v in c.metrics.items()}   # corner-less children (EM devices)
    present = {c.corner for c in cornered} or {"nominal"}                   # a run may cover a subset of the corners
    corner_ids = [cid for cid in ([c.id for c in spec.corners] or ["nominal"]) if cid in present]
    nominal = "nominal" if "nominal" in corner_ids else corner_ids[0]
    per_corner: dict[str, dict[str, float]] = {cid: dict(shared) for cid in corner_ids}
    for child in cornered:
        per_corner[child.corner].update(child.metrics)

    evaluations = {cid: objective_contract.evaluate(spec, metrics) for cid, metrics in per_corner.items()}
    objectives = {cid: ev.objective for cid, ev in evaluations.items()}
    issues = [f"{cid}: {issue}" for cid, ev in evaluations.items() for issue in ev.issues]

    metric_failed = [cid for cid, ev in evaluations.items() if ev.status == "metric_failed"]
    if metric_failed:
        return Aggregate(status="metric_failed", corner_objectives=objectives, issues=issues)

    constraint_scope = [nominal] if spec.corner_policy.constraints == "nominal" else corner_ids
    violating = [cid for cid in constraint_scope if evaluations[cid].status == "constraint_failed"]
    if violating:
        selected = max(violating, key=lambda cid: evaluations[cid].constraint_penalty)
        ev = evaluations[selected]
        return Aggregate(
            status="constraint_failed", metrics=per_corner[selected], fom=ev.fom, objective=None, feasible=False,
            constraint_penalty=ev.constraint_penalty, selected_corner=selected, corner_objectives=objectives, issues=issues,
        )

    objective_scope = [nominal] if spec.corner_policy.objective == "nominal" else corner_ids
    if spec.objective is None:
        selected = nominal
    else:
        selected = max(objective_scope, key=lambda cid: objectives[cid])   # minimization form: max = worst
    ev = evaluations[selected]
    return Aggregate(
        status="ok", metrics=per_corner[selected], fom=ev.fom, objective=ev.objective, feasible=True,
        selected_corner=selected, corner_objectives=objectives,
    )
