"""Objective and constraints: safe expression evaluation and feasibility.

The expression language is deliberately tiny: metric names, numeric literals,
``+ - * / ** %``, unary sign, and ``min`` / ``max`` / ``ln``.
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import TYPE_CHECKING

from ic_opt.space import parse_scalar

if TYPE_CHECKING:
    from ic_opt.spec import Spec

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.UAdd, ast.USub,
)
_FUNCTIONS = frozenset({"min", "max", "ln"})


def expression_issues(expression: str, metric_names: set[str]) -> list[str]:
    """Statically validate an expression without metric values."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        return [f"invalid expression: {exc.msg}"]
    issues: list[str] = []
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return [f"unsupported expression node {type(node).__name__}"]
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
                issues.append("unsupported function call")
            elif node.keywords or not node.args or (node.func.id == "ln" and len(node.args) != 1):
                issues.append(f"bad arguments for {node.func.id}")
        elif isinstance(node, ast.Name) and id(node) not in called:
            if node.id in _FUNCTIONS:
                issues.append(f"function {node.id} must be called")
            elif node.id not in metric_names:
                hint = _closest(node.id, metric_names)
                issues.append(f"unknown metric {node.id}" + (f"; did you mean {hint}?" if hint else ""))
        elif isinstance(node, ast.Constant) and (
            isinstance(node.value, bool) or not isinstance(node.value, int | float) or not math.isfinite(node.value)
        ):
            issues.append(f"unsupported literal {node.value!r}")
    return issues


def evaluate_expression(expression: str, metrics: dict[str, float]) -> float:
    value = float(_eval(ast.parse(expression, mode="eval").body, metrics))
    if not math.isfinite(value):
        raise ValueError("expression returned a non-finite value")
    return value


@dataclass(frozen=True)
class Evaluation:
    """What one point's aggregated metrics mean under the spec."""

    status: str                      # "ok" | "metric_failed" | "constraint_failed"
    fom: float | None                # objective expression value as written
    objective: float | None          # minimization form (negated for maximize)
    feasible: bool
    constraint_penalty: float = 0.0  # sum of squared normalized violations
    issues: list[str] = field(default_factory=list)


def evaluate(spec: Spec, metrics: dict[str, float]) -> Evaluation:
    missing = [m.name for m in spec.metrics if m.name not in metrics or not math.isfinite(metrics[m.name])]
    if missing:
        return Evaluation("metric_failed", None, None, False, issues=[f"metric {n} missing or non-finite" for n in missing])

    penalty, violations = constraint_violations(spec, metrics)
    fom: float | None = None
    if spec.objective is not None:
        try:
            fom = evaluate_expression(spec.objective.expression, metrics)
        except (ArithmeticError, ValueError, KeyError) as exc:
            return Evaluation("metric_failed", None, None, False, penalty, [f"objective: {exc}"])
    if violations:
        return Evaluation("constraint_failed", fom, None, False, penalty, violations)
    objective = None if fom is None else (-fom if spec.objective.direction == "maximize" else fom)
    return Evaluation("ok", fom, objective, True)


def constraint_violations(spec: Spec, metrics: dict[str, float]) -> tuple[float, list[str]]:
    penalty, issues = 0.0, []
    for c in spec.constraints:
        value = float(metrics[c.metric])
        threshold = float(parse_scalar(c.value.replace(" ", ""))[0])
        scale = abs(threshold) or 1.0
        gap = {
            "lt": value - threshold if value >= threshold else 0.0,
            "le": value - threshold if value > threshold else 0.0,
            "gt": threshold - value if value <= threshold else 0.0,
            "ge": threshold - value if value < threshold else 0.0,
        }[c.op]
        if gap > 0:
            v = gap / scale
            penalty += v * v
            issues.append(f"{c.metric} {c.op} {c.value} violated by {value:g}")
    return penalty, issues


def _eval(node: ast.AST, metrics: dict[str, float]) -> float:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return float(metrics[node.id])
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, metrics)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, metrics), _eval(node.right, metrics)
        op = type(node.op)
        try:
            if op is ast.Add:
                return a + b
            if op is ast.Sub:
                return a - b
            if op is ast.Mult:
                return a * b
            if op is ast.Div:
                return a / b
            if op is ast.Mod:
                return a % b
            if op is ast.Pow:
                result = a**b
                if isinstance(result, complex):
                    raise ValueError("power produced a complex value")
                return result
        except (ArithmeticError, TypeError) as exc:
            raise ValueError(str(exc)) from exc
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        args = [_eval(a, metrics) for a in node.args]
        if node.func.id == "min":
            return min(args)
        if node.func.id == "max":
            return max(args)
        if node.func.id == "ln":
            if args[0] <= 0:
                raise ValueError("ln argument must be positive")
            return math.log(args[0])
    raise ValueError(f"unsupported expression node {type(node).__name__}")


def _closest(name: str, candidates: set[str]) -> str | None:
    norm = {"".join(ch.lower() for ch in c if ch.isalnum()): c for c in candidates}
    hit = get_close_matches("".join(ch.lower() for ch in name if ch.isalnum()), sorted(norm), n=1, cutoff=0.75)
    return norm[hit[0]] if hit else None
