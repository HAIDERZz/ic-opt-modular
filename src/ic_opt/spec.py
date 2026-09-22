"""Spec: the WHAT of a design problem, loaded from ``spec.yaml``.

A Spec describes the circuit problem only — testbenches, corners, variables,
metrics, constraints, objective, simulator resources, budget. How to run it
(strategy, fixed points, waveforms, warm start) belongs to recipes and block
parameters, never here.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_UNSAFE_TOKEN_CHARS = ("'", '"', "\\")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _name(value: str, label: str) -> str:
    if not NAME_RE.match(value):
        raise ValueError(f"{label} must match [A-Za-z_][A-Za-z0-9_]*")
    return value


def _compact_token(value: str, label: str) -> str:
    if not value or any(c.isspace() for c in value) or any(c in value for c in _UNSAFE_TOKEN_CHARS):
        raise ValueError(f"{label} must be a compact token without whitespace, quotes or backslashes")
    return value


class VariableKind(StrEnum):
    INTEGER = "integer"
    CONTINUOUS_STEP = "continuous_step"


class ConstraintOp(StrEnum):
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"


class Direction(StrEnum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"


class Testbench(Model):
    id: str
    maestro_point_root: str = Field(min_length=1)
    virtuoso_library: str = Field(min_length=1)
    cell: str = Field(min_length=1)
    test_name: str = Field(min_length=1)
    design_view: str = "schematic"
    maestro_view: str = "maestro"
    corner: str = "Nominal"

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _name(value, "testbench id")


class Corner(Model):
    id: str
    model_section: str | None = None
    model_file: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    description: str = ""

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _name(value, "corner id")

    @field_validator("model_section")
    @classmethod
    def _section(cls, value: str | None) -> str | None:
        return None if value is None else _compact_token(value, "corner model_section")

    @field_validator("model_file")
    @classmethod
    def _file(cls, value: str | None) -> str | None:
        if value is None:
            return None
        _compact_token(value, "corner model_file")
        if not PurePosixPath(value).is_absolute():
            raise ValueError("corner model_file must be an absolute POSIX path")
        return value

    @field_validator("variables")
    @classmethod
    def _vars(cls, value: dict[str, str]) -> dict[str, str]:
        for name, raw in value.items():
            _name(name, "corner variable name")
            _compact_token(raw, f"corner variable {name}")
        return value


class CornerPolicy(Model):
    objective: Literal["nominal", "worst_case"] = "worst_case"
    constraints: Literal["nominal", "all_corners"] = "all_corners"


class Variable(Model):
    name: str
    kind: VariableKind
    lower: str = Field(min_length=1)
    upper: str = Field(min_length=1)
    step: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _n(cls, value: str) -> str:
        return _name(value, "variable name")

    @field_validator("lower", "upper", "step", mode="before")
    @classmethod
    def _as_text(cls, value: object) -> str:
        # YAML happily turns `20` into an int; the grid contract works on the
        # exact text the netlist will carry, so keep everything as strings.
        return str(value)

    @model_validator(mode="after")
    def _grid(self) -> Variable:
        from ic_opt import space

        issue = space.variable_issue(self)
        if issue:
            raise ValueError(issue)
        return self


class Metric(Model):
    name: str
    unit: str = Field(min_length=1)
    expression: str = Field(min_length=1)
    testbench: str | None = None
    result: str | None = None
    required_signals: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _n(cls, value: str) -> str:
        return _name(value, "metric name")

    @field_validator("expression")
    @classmethod
    def _expr(cls, value: str) -> str:
        if "{{" in value or "}}" in value:
            raise ValueError("metric expression must not contain template placeholders")
        return value


class Constraint(Model):
    metric: str
    op: ConstraintOp
    value: str = Field(min_length=1)

    @field_validator("value", mode="before")
    @classmethod
    def _as_text(cls, value: object) -> str:
        return str(value)


class Objective(Model):
    direction: Direction
    expression: str = Field(min_length=1)


class Simulator(Model):
    preset: Literal["cx", "ax", "mx", "lx", "vx"] = "ax"
    threads_per_run: int = Field(default=10, ge=1)
    parallel_jobs: int = Field(ge=1)
    timeout_s: int = Field(gt=0)
    license_check: bool = True
    keep_failed_runs: bool = True
    keep_successful_runs: bool = True

    engine: Literal["spectre_x"] = "spectre_x"
    output_format: Literal["psfxl"] = "psfxl"


class Budget(Model):
    max_simulations: int = Field(ge=1)


class Spec(Model):
    project: str
    description: str = ""
    testbenches: list[Testbench] = Field(min_length=1)
    corners: list[Corner] = Field(default_factory=list)
    corner_policy: CornerPolicy = Field(default_factory=CornerPolicy)
    variables: list[Variable] = Field(min_length=1)
    metrics: list[Metric] = Field(default_factory=list)      # empty: waveform-only runs
    constraints: list[Constraint] = Field(default_factory=list)
    objective: Objective | None = None
    simulator: Simulator
    budget: Budget

    @field_validator("project")
    @classmethod
    def _p(cls, value: str) -> str:
        return _name(value, "project")

    @model_validator(mode="after")
    def _cross_references(self) -> Spec:
        from ic_opt import objective as objective_contract

        _unique([t.id for t in self.testbenches], "testbench ids")
        _unique([c.id for c in self.corners], "corner ids")
        _unique([v.name for v in self.variables], "variable names")
        _unique([m.name for m in self.metrics], "metric names")
        tb_ids = {t.id for t in self.testbenches}
        for metric in self.metrics:
            if metric.testbench is None:
                if len(tb_ids) > 1:
                    raise ValueError(f"metric {metric.name} must name its testbench")
                metric.testbench = self.testbenches[0].id
            elif metric.testbench not in tb_ids:
                raise ValueError(f"metric {metric.name} references unknown testbench {metric.testbench}")
        metric_names = {m.name for m in self.metrics}
        for constraint in self.constraints:
            if constraint.metric not in metric_names:
                raise ValueError(f"constraint references unknown metric {constraint.metric}")
        if self.objective is not None:
            issues = objective_contract.expression_issues(self.objective.expression, metric_names)
            if issues:
                raise ValueError(issues[0])
        return self

    # -- convenience -------------------------------------------------------

    @property
    def testbench_ids(self) -> list[str]:
        return [t.id for t in self.testbenches]

    @property
    def corner_ids(self) -> list[str | None]:
        """Corner ids to run; ``[None]`` means the source point's own corner."""
        return [c.id for c in self.corners] or [None]

    def testbench(self, tb_id: str) -> Testbench:
        return next(t for t in self.testbenches if t.id == tb_id)

    def corner(self, corner_id: str) -> Corner:
        return next(c for c in self.corners if c.id == corner_id)

    def metrics_for(self, tb_id: str) -> list[Metric]:
        return [m for m in self.metrics if m.testbench == tb_id]

    def fingerprint(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def load_spec(path: str | Path) -> Spec:
    return Spec.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
