"""Corner aggregation: many testbench × corner child results -> one evaluation.

Rules:
- a child that did not run to the end (``failed:<stage>``) fails the point at that stage;
- a child that ran but whose OCEAN expressions returned nil / non-scalar values (``metric_failed``) keeps the metrics
  that did extract; the point is then ``metric_failed`` and keeps the nominal corner's computed metrics (N-31);
- metrics of one corner are the union over its testbenches;
- ``constraints: all_corners`` fails the point if any corner violates,
  ``nominal`` looks at the nominal corner only;
- ``objective: worst_case`` reports the worst corner's objective,
  ``nominal`` the nominal corner's;
- the reported metrics are those of the selected (worst / nominal) corner.

Since T16.6 a child that succeeded may still carry issues, as warnings (a coupled pair measured with k < 0): they reach
the point's issues after its own, whatever its status.

The nominal corner is the one with id ``nominal`` if present, else the first
evaluated corner in spec order (a run may cover only a subset of the corners).

An incomplete set (T17.8): the engine's schedule stops a point at the first child whose result shows the point cannot be
feasible (``ic_opt.eval.schedule``), and the children after it never run. Given ``wanted``, the keys of every child the
point was to run, :func:`aggregate` judges what ran and guesses nothing about the rest: a child that did not run to its
end fails the point at its stage, as above; a child that lost a metric makes it ``metric_failed``, as above; else the
point is ``constraint_failed`` at a corner in the constraint scope whose present metrics violate a constraint
(``objective.evaluate_partial``: the constraints whose metric is there, and only those), with no ``fom`` and no
objective. It is never feasible, and its issues carry, after the failure's own lines, ``NOT_SIMULATED``: how many
children were not simulated and after which one the point stopped (:func:`stopped_early` reads it back).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from ic_opt import objective as objective_contract
from ic_opt.observation import ChildResult
from ic_opt.spec import Spec

NOT_SIMULATED = "not simulated: {not_run} of {wanted} children (stopped after {child})"
_NOT_SIMULATED = re.compile(r"^not simulated: (\d+) of (\d+) children \(stopped after (.+)\)$")


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


def aggregate(spec: Spec, children: dict[str, ChildResult], wanted: list[str] | None = None) -> Aggregate:
    """The point's verdict from its children. ``wanted``: the keys of every child the point was to run; None, or every
    one of them present, is the complete set above. With some of them absent, the verdict of an incomplete set (module
    docstring); a ``ValueError`` when nothing in it fails the point -- the engine stops a point only at a child that does."""
    not_run = [key for key in wanted or () if key not in children]
    if not_run:
        return _incomplete(spec, children, len(not_run), len(wanted))
    failed = [c for c in children.values() if c.status not in ("ok", "metric_failed")]
    warnings = [f"{c.unit}/{c.corner or 'nominal'}: {issue}" for c in children.values() if c.status == "ok" for issue in c.issues]
    if failed:
        worst = failed[0]
        return Aggregate(
            status=worst.status,
            issues=[f"{c.unit}/{c.corner or 'nominal'}: {issue}" for c in failed for issue in c.issues] + warnings,
        )
    partial = [f"{c.unit}/{c.corner or 'nominal'}: {issue}" for c in children.values() if c.status == "metric_failed" for issue in c.issues]

    per_corner, corner_ids, nominal = _per_corner(spec, children)
    evaluations = {cid: objective_contract.evaluate(spec, metrics) for cid, metrics in per_corner.items()}
    objectives = {cid: ev.objective for cid, ev in evaluations.items()}
    issues = partial + [f"{cid}: {issue}" for cid, ev in evaluations.items() for issue in ev.issues] + warnings

    metric_failed = [cid for cid, ev in evaluations.items() if ev.status == "metric_failed"]
    if metric_failed or partial:            # what was computed stays readable: the nominal corner's metrics, no objective
        return Aggregate(status="metric_failed", metrics=dict(per_corner[nominal]), corner_objectives=objectives, issues=issues)

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
        selected_corner=selected, corner_objectives=objectives, issues=warnings,
    )


def stopped_early(issues: Iterable[str]) -> tuple[int, int, str] | None:
    """(children not simulated, children the point was to run, the key of the child it stopped after) from the
    ``NOT_SIMULATED`` line of a point stopped early; None for a point that ran every child."""
    for issue in issues:
        if match := _NOT_SIMULATED.match(issue):
            return int(match.group(1)), int(match.group(2)), match.group(3)
    return None


def _per_corner(spec: Spec, children: dict[str, ChildResult]) -> tuple[dict[str, dict[str, float]], list[str], str]:
    """Each corner's metrics (its testbench children's and every corner-less child's: EM devices), the corners present in
    spec order, and the nominal corner among them."""
    cornered = [c for c in children.values() if c.corner is not None]
    shared = {k: v for c in children.values() if c.corner is None for k, v in c.metrics.items()}   # corner-less children (EM devices)
    present = {c.corner for c in cornered} or {"nominal"}                   # a run may cover a subset of the corners
    corner_ids = [cid for cid in ([c.id for c in spec.corners] or ["nominal"]) if cid in present]
    nominal = "nominal" if "nominal" in corner_ids else corner_ids[0]
    per_corner: dict[str, dict[str, float]] = {cid: dict(shared) for cid in corner_ids}
    for child in cornered:
        per_corner[child.corner].update(child.metrics)
    return per_corner, corner_ids, nominal


def _incomplete(spec: Spec, children: dict[str, ChildResult], not_run: int, wanted: int) -> Aggregate:
    """The verdict of a point whose children after the ``stopper`` never ran, the three cases of the module docstring in
    order. The stopper is the child that shows the failure: the one that did not run to its end, the one that lost a
    metric, or the one whose own metrics violate a constraint at the selected corner."""
    warnings = [f"{_key(c)}: {issue}" for c in children.values() if c.status == "ok" for issue in c.issues]

    def stopped(stopper: ChildResult | None, fallback: str = "") -> str:
        return NOT_SIMULATED.format(not_run=not_run, wanted=wanted, child=_key(stopper) if stopper else fallback)

    failed = [c for c in children.values() if c.status not in ("ok", "metric_failed")]
    if failed:
        own = [f"{_key(c)}: {issue}" for c in failed for issue in c.issues]
        return Aggregate(status=failed[0].status, issues=[*own, stopped(failed[0]), *warnings])

    per_corner, corner_ids, nominal = _per_corner(spec, children)
    complete = {cid: objective_contract.evaluate(spec, metrics).objective for cid, metrics in per_corner.items()
                if all(math.isfinite(metrics.get(m.name, math.nan)) for m in spec.metrics)}
    lost = [c for c in children.values() if c.status == "metric_failed"]
    if lost:
        own = [f"{_key(c)}: {issue}" for c in lost for issue in c.issues]
        return Aggregate(status="metric_failed", metrics=dict(per_corner[nominal]), corner_objectives=complete,
                         issues=[*own, stopped(lost[0]), *warnings])

    evaluations = {cid: objective_contract.evaluate_partial(spec, metrics) for cid, metrics in per_corner.items()}
    scope = [nominal] if spec.corner_policy.constraints == "nominal" else corner_ids
    violating = [cid for cid in scope if evaluations[cid].status == "constraint_failed"]
    if not violating:
        raise ValueError(f"{len(children)} of {wanted} children ran and none of them fails the point; the engine stops a "
                         "point only at a child that shows it cannot be feasible")
    selected = max(violating, key=lambda cid: evaluations[cid].constraint_penalty)
    ev = evaluations[selected]
    stopper = next((c for c in children.values() if c.corner in (None, selected)
                    and objective_contract.evaluate_partial(spec, c.metrics).status == "constraint_failed"), None)
    own = [f"{cid}: {issue}" for cid, e in evaluations.items() for issue in e.issues]
    return Aggregate(
        status="constraint_failed", metrics=dict(per_corner[selected]), fom=None, objective=None, feasible=False,
        constraint_penalty=ev.constraint_penalty, selected_corner=selected, corner_objectives=complete,
        issues=[*own, stopped(stopper, selected), *warnings],
    )


def _key(child: ChildResult) -> str:
    return f"{child.unit}/{child.corner or 'nominal'}"
