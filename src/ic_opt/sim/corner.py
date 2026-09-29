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
children were not simulated and after which one the point stopped. That line is for the reader; the engine names the
children not run in the observation's ``not_run``, which is what code reads, and :func:`stopper` names, from a recorded
point, the child the line names (T17.9: the digest counts where points stopped).

The point as a strategy sees it (T17.9): :func:`worst_metrics`, each metric the constraints or the objective name at its
worst over the corners the point was simulated at, is what ``metric_gp``'s models are given; the verdict above stays the
point's own. It is computed from a point's metrics per corner and the corners a policy scores
(:func:`metrics_per_corner`, :func:`scored_corners`), which the digest and the report read too.
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass, field

from ic_opt import objective as objective_contract
from ic_opt.observation import ChildResult, Observation
from ic_opt.space import parse_scalar
from ic_opt.spec import Constraint, Spec

NOT_SIMULATED = "not simulated: {not_run} of {wanted} children (stopped after {child})"


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
    """The verdict of a point whose children after the stopper (:func:`_stopper`) never ran, the three cases of the module
    docstring in order."""
    warnings = [f"{_key(c)}: {issue}" for c in children.values() if c.status == "ok" for issue in c.issues]
    stopped = NOT_SIMULATED.format(not_run=not_run, wanted=wanted, child=_stopper(spec, children))

    failed = [c for c in children.values() if c.status not in ("ok", "metric_failed")]
    if failed:
        own = [f"{_key(c)}: {issue}" for c in failed for issue in c.issues]
        return Aggregate(status=failed[0].status, issues=[*own, stopped, *warnings])

    per_corner, corner_ids, nominal = _per_corner(spec, children)
    complete = {cid: objective_contract.evaluate(spec, metrics).objective for cid, metrics in per_corner.items()
                if all(math.isfinite(metrics.get(m.name, math.nan)) for m in spec.metrics)}
    lost = [c for c in children.values() if c.status == "metric_failed"]
    if lost:
        own = [f"{_key(c)}: {issue}" for c in lost for issue in c.issues]
        return Aggregate(status="metric_failed", metrics=dict(per_corner[nominal]), corner_objectives=complete,
                         issues=[*own, stopped, *warnings])

    evaluations, selected = _violated(spec, per_corner, corner_ids, nominal)
    if selected is None:
        raise ValueError(f"{len(children)} of {wanted} children ran and none of them fails the point; the engine stops a "
                         "point only at a child that shows it cannot be feasible")
    ev = evaluations[selected]
    own = [f"{cid}: {issue}" for cid, e in evaluations.items() for issue in e.issues]
    return Aggregate(
        status="constraint_failed", metrics=dict(per_corner[selected]), fom=None, objective=None, feasible=False,
        constraint_penalty=ev.constraint_penalty, selected_corner=selected, corner_objectives=complete,
        issues=[*own, stopped, *warnings],
    )


def stopper(spec: Spec, o: Observation) -> str | None:
    """Where the schedule stopped the point ``o`` (T17.8): the child ``<unit>/<corner>`` after which the others were not
    run, the one its "not simulated" issues line names (:func:`_stopper`, the one rule for both). None for a point that
    ran every child it was to run, and for one whose children show nothing that fails it under ``spec``."""
    return _stopper(spec, o.children) if o.not_run else None


def _stopper(spec: Spec, children: dict[str, ChildResult]) -> str | None:
    """The child of an incomplete set that shows why the point cannot be feasible, by the three cases of
    :func:`_incomplete` in order: the first that did not run to its end; else the first that lost a metric; else the first
    whose own metrics violate a constraint at the corner of the verdict (a corner-less child counts at every corner) --
    that corner itself when no child violates alone. None when nothing fails."""
    shown = ([c for c in children.values() if c.status not in ("ok", "metric_failed")]
             or [c for c in children.values() if c.status == "metric_failed"])
    if shown:
        return _key(shown[0])
    _, selected = _violated(spec, *_per_corner(spec, children))
    if selected is None:
        return None
    child = next((c for c in children.values() if c.corner in (None, selected)
                  and objective_contract.evaluate_partial(spec, c.metrics).status == "constraint_failed"), None)
    return _key(child) if child else selected


def _violated(spec: Spec, per_corner: dict[str, dict[str, float]], corner_ids: list[str],
              nominal: str) -> tuple[dict[str, objective_contract.Evaluation], str | None]:
    """Each corner's evaluation on its present metrics (``objective.evaluate_partial``), and the corner in the constraint
    scope whose violation has the largest penalty, the first of equal ones; None when none violates."""
    evaluations = {cid: objective_contract.evaluate_partial(spec, metrics) for cid, metrics in per_corner.items()}
    scope = [nominal] if spec.corner_policy.constraints == "nominal" else corner_ids
    violating = [cid for cid in scope if evaluations[cid].status == "constraint_failed"]
    if not violating:
        return evaluations, None
    return evaluations, max(violating, key=lambda cid: evaluations[cid].constraint_penalty)


def _key(child: ChildResult) -> str:
    return f"{child.unit}/{child.corner or 'nominal'}"


# -- the point as a strategy sees it (T17.9) ------------------------------------------------------------------------------


def metrics_per_corner(spec: Spec, o: Observation) -> dict[str, dict[str, float]]:
    """A corner's metrics: every corner-less child's (devices; on a spec without corners, the testbenches too) and then its own
    testbench children's. A spec without corners has the one corner ``nominal``."""
    corner_ids = [c.id for c in spec.corners] or ["nominal"]
    shared = {k: v for ch in o.children.values() if ch.corner is None for k, v in ch.metrics.items()}
    out = {cid: dict(shared) for cid in corner_ids}
    for ch in o.children.values():
        if ch.corner is not None:
            out.setdefault(ch.corner, dict(shared)).update(ch.metrics)
    return out


def scored_corners(spec: Spec, policy: str | None = None) -> list[str]:
    """The corners a corner policy scores: every corner under ``all_corners`` (``worst_case`` for the objective), the
    nominal one under ``nominal``. ``policy``: the constraints' unless given."""
    corner_ids = [c.id for c in spec.corners] or ["nominal"]
    if (policy or spec.corner_policy.constraints) != "nominal":
        return corner_ids
    return ["nominal"] if "nominal" in corner_ids else corner_ids[:1]


def worst_metrics(spec: Spec, o: Observation) -> dict[str, float]:
    """The point's metrics as ``metric_gp``'s models are given them: each metric the constraints or the objective name at
    its worst over the corners the point was simulated at (a point stopped early: those it reached).

    - A constrained metric: its worst value over the scored corners (:func:`scored_corners`) -- the smallest for a lower
      bound, the largest for an upper one, as the digest's ``constraint_value`` judges a constraint; under bounds of both
      kinds the value with the smallest margin over all of them, below 0 when one is violated. Where no scored corner
      holds it, the point's own value.
    - A metric only the objective names: its value at the corner whose objective is worst (the largest, minimization
      form) among the corners the objective's policy scores where every metric of the objective is there; absent when
      no corner has them all.
    - A metric neither names is left out: it has no worst, and no model reads it.

    A point evaluated at one corner (a single-condition run, the signoff recipe's search, a spec without corners) keeps
    its own ``metrics`` as they are -- what the verdict made of that corner, none for a point that failed -- so a
    single-condition run proposes what it did before T17.9, byte for byte. At several corners a point that failed gives
    what its other children measured, as a stopped point does; its status tells the models it gave no value.

    Why not the point's own ``metrics`` at several corners: they are one corner's values (the corner with the largest
    penalty, or the worst objective of a feasible point), and a constraint another corner violates alone would reach the
    models as a passing value. The objective is composed from these values, as from every metric in ``metric_gp``."""
    if len(o.corners()) < 2:
        return dict(o.metrics)
    per_corner = metrics_per_corner(spec, o)
    scored = scored_corners(spec)
    bounds: dict[str, list[Constraint]] = {}
    for c in spec.constraints:
        bounds.setdefault(c.metric, []).append(c)
    out = {}
    for name, constraints in bounds.items():
        values = [m[name] for cid in scored if (m := per_corner.get(cid)) and name in m]
        value = _worst(values, constraints) if values else o.metrics.get(name)
        if value is not None:
            out[name] = value
    named = _objective_metrics(spec)
    only = [name for name in named if name not in bounds]
    if only and (corner := _worst_objective_corner(spec, per_corner, named)) is not None:
        out.update({name: per_corner[corner][name] for name in only})
    return out


def _worst(values: list[float], constraints: list[Constraint]) -> float:
    """Of one metric's ``values``, the one its ``constraints`` judge worst (:func:`worst_metrics`)."""
    lower = any(c.op in ("gt", "ge") for c in constraints)
    upper = any(c.op in ("lt", "le") for c in constraints)
    if lower != upper:
        return min(values) if lower else max(values)
    return min(values, key=lambda v: min(_margin(c, v) for c in constraints))


def _margin(constraint: Constraint, value: float) -> float:
    """How far ``value`` is inside the constraint, in the metric's unit: positive passes (``digest.margin``)."""
    limit = float(parse_scalar(constraint.value.replace(" ", ""))[0])
    return (limit - value) if constraint.op in ("lt", "le") else (value - limit)


def _objective_metrics(spec: Spec) -> list[str]:
    """The metrics the objective expression names, in spec order; none without an objective."""
    if spec.objective is None:
        return []
    tree = ast.parse(spec.objective.expression, mode="eval")
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    named = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in called}
    return [m.name for m in spec.metrics if m.name in named]


def _worst_objective_corner(spec: Spec, per_corner: dict[str, dict[str, float]], named: list[str]) -> str | None:
    """The corner the objective's policy scores whose objective, from that corner's metrics, is the largest in
    minimization form -- the first of equal ones -- among those where every metric in ``named`` is there and finite and
    the objective has a value; None when there is none."""
    worst, where = -math.inf, None
    for cid in scored_corners(spec, spec.corner_policy.objective):
        metrics = per_corner.get(cid, {})
        if not all(math.isfinite(metrics.get(name, math.nan)) for name in named):
            continue
        try:
            value = objective_contract.evaluate_expression(spec.objective.expression, metrics)
        except (ArithmeticError, ValueError):
            continue
        value = -value if spec.objective.direction == "maximize" else value
        if where is None or value > worst:
            worst, where = value, cid
    return where
