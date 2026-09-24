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
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    field_validator,
    model_serializer,
    model_validator,
)

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
VARIABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")   # device variables: <device>.<field>
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
        if not VARIABLE_RE.match(value):
            raise ValueError("variable name must match [A-Za-z_][A-Za-z0-9_]* optionally prefixed by <device>.")
        return value

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
    """A scalar the evaluation must produce: an OCEAN expression on a testbench, or a quantity of an EM device."""

    name: str
    unit: str = Field(min_length=1)
    expression: str | None = None            # OCEAN expression (testbench metrics)
    testbench: str | None = None
    result: str | None = None
    required_signals: list[str] = Field(default_factory=list)
    device: str | None = None                # EM device metrics: quantity of the device's S-parameters
    quantity: str | None = None              # e.g. Lp, Qp, k (curves, need frequency_hz) or Lp_res, Qp_peak, SRF_p (scalars)
    frequency_hz: float | None = None

    @field_validator("name")
    @classmethod
    def _n(cls, value: str) -> str:
        return _name(value, "metric name")

    @field_validator("expression")
    @classmethod
    def _expr(cls, value: str | None) -> str | None:
        if value is not None and ("{{" in value or "}}" in value):
            raise ValueError("metric expression must not contain template placeholders")
        return value

    @model_validator(mode="after")
    def _one_source(self) -> Metric:
        if (self.expression is None) == (self.quantity is None):
            raise ValueError(f"metric {self.name}: give either expression (testbench) or quantity (device)")
        if self.quantity is not None and self.device is None:
            raise ValueError(f"metric {self.name}: a quantity metric must name its device")
        if self.expression is not None and self.device is not None:
            raise ValueError(f"metric {self.name}: an expression metric belongs to a testbench, not a device")
        return self


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


# -- EM devices --------------------------------------------------------------------


class Topology(Model):
    """Ideal-balun measurement topology for a device's S-parameters (see ic_opt.em.touchstone)."""

    drives: list[tuple[str, str]] = Field(min_length=1, max_length=2)   # (plus, minus) port labels per drive
    grounded: list[str] = Field(default_factory=list)
    low_freq_max_hz: PositiveFloat | Literal["relative"] | None = None   # top of the L*_lf / k_lf band (ic_opt.em.measure); None: 3 GHz

    @model_serializer(mode="wrap")
    def _dump(self, handler):
        """An unset low-frequency limit stays out of the dump, so the specs written before it existed keep their fingerprint."""
        data = handler(self)
        if self.low_freq_max_hz is None:
            data.pop("low_freq_max_hz", None)
        return data


class Device(Model):
    """One pcell generator instance: what the EM stages build, extract and bind."""

    id: str
    generator: str = Field(min_length=1)                      # generator id inside the plugin, e.g. clean_port_xfm_bs
    plugin: str = "builtin:clean_port"                        # builtin:<name> or an absolute path to a plugin .py
    profile: str = Field(min_length=1)                        # process rule profile id (resolved via IC_OPT_PROFILE_DIRS)
    ports: list[str] = Field(min_length=1)                    # semantic port labels in the generator's fixed order
    fixed: dict[str, object] = Field(default_factory=dict)    # generator fields that are not optimized (passed through)
    variables: dict[str, str] = Field(default_factory=dict)   # generator field -> spec variable name (default "<id>.<field>")
    topology: Topology | None = None                          # default derived from the port set

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _name(value, "device id")

    @field_validator("ports")
    @classmethod
    def _ports(cls, value: list[str]) -> list[str]:
        for port in value:
            _name(port, "device port")
        _unique(value, "device ports")
        return value

    def default_topology(self) -> Topology:
        """2 ports: one differential drive; 4 ports: two drives with the secondary reversed (library parity); extra ports grounded."""
        base = [p for p in self.ports if not p.upper().startswith("CT")]
        extra = [p for p in self.ports if p.upper().startswith("CT")]
        if len(base) == 2:
            return Topology(drives=[(base[0], base[1])], grounded=extra)
        if len(base) == 4:
            return Topology(drives=[(base[0], base[1]), (base[3], base[2])], grounded=extra)
        raise ValueError(f"device {self.id}: no default topology for ports {self.ports}; give topology explicitly")


class EmSweep(Model):
    start_hz: float = Field(ge=0)
    stop_hz: float = Field(gt=0)
    step_hz: float | None = Field(default=None, gt=0)
    num_steps: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _shape(self) -> EmSweep:
        if self.stop_hz <= self.start_hz:
            raise ValueError("em sweep needs stop_hz > start_hz")
        if (self.step_hz is None) == (self.num_steps is None):
            raise ValueError("em sweep needs exactly one of step_hz / num_steps")
        return self


class EmGrid(Model):
    """Explicit mesh control instead of a named accuracy."""

    edge_width_um: float | None = Field(default=None, gt=0)
    max_splits: int | None = Field(default=None, ge=0)
    thickness_um: float | None = Field(default=None, gt=0)


class EmSettings(Model):
    """EMX settings: one set per spec, applied to every device."""

    binary: str = "emx"
    process_file: str = Field(min_length=1)                   # on the executor host, absolute
    mode: Literal["quasistatic", "full_wave"] = "quasistatic"
    frequencies: EmSweep | list[float]
    accuracy: Literal["standard", "high", "higher", "highest"] | EmGrid | None = "standard"
    three_d_metals: list[str] = Field(default_factory=list)
    via_separation_um: float | None = None
    via_inductance: list[str] = Field(default_factory=list)
    via_sidewalls: list[str] = Field(default_factory=list)
    modes: list[str] = Field(default_factory=list)
    s_impedance: float = Field(default=50.0, gt=0)
    threads: int = Field(default=4, ge=1)                     # --parallel
    memory_gb: float = Field(default=32.0, gt=0)              # --max-memory
    simultaneous_frequencies: int | None = 0                  # explicit 0 after the 2026-07-09 incident
    timeout_s: int = Field(default=3600, gt=0)
    verbose: int | None = 2
    extra_args: list[str] = Field(default_factory=list)

    @field_validator("process_file")
    @classmethod
    def _proc(cls, value: str) -> str:
        _compact_token(value, "em process_file")
        if not PurePosixPath(value).is_absolute():
            raise ValueError("em process_file must be an absolute POSIX path on the simulation host")
        return value

    @field_validator("extra_args")
    @classmethod
    def _extra(cls, value: list[str]) -> list[str]:
        for arg in value:
            if arg.split("=", 1)[0] in {"--parallel", "--max-memory", "--simultaneous-frequencies", "--s-file", "--log-file"}:
                raise ValueError(f"em extra_args must not set {arg.split('=', 1)[0]}; use the dedicated field")
        return value

    @field_validator("frequencies")
    @classmethod
    def _freqs(cls, value: EmSweep | list[float]) -> EmSweep | list[float]:
        if isinstance(value, list) and (not value or any(f <= 0 for f in value)):
            raise ValueError("em frequencies must be a non-empty list of positive Hz or a sweep")
        return value


class Binding(Model):
    """A Spectre nport instance in a testbench that takes a device's S-parameters."""

    testbench: str
    instance: str = Field(min_length=1)
    device: str
    terminals: list[str] = Field(min_length=1)                # circuit terminal order == sNp port order (semantic labels)

    @field_validator("instance")
    @classmethod
    def _inst(cls, value: str) -> str:
        return _compact_token(value, "binding instance")


class Spec(Model):
    project: str
    description: str = ""
    testbenches: list[Testbench] = Field(default_factory=list)   # circuit testbenches (Maestro exports)
    devices: list[Device] = Field(default_factory=list)          # EM devices (pcell generators)
    em: EmSettings | None = None
    bindings: list[Binding] = Field(default_factory=list)        # device sNp -> testbench nport instances
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

        if not self.testbenches and not self.devices:
            raise ValueError("spec needs at least one testbench or one device")
        _unique([t.id for t in self.testbenches], "testbench ids")
        _unique([d.id for d in self.devices], "device ids")
        _unique([c.id for c in self.corners], "corner ids")
        _unique([v.name for v in self.variables], "variable names")
        _unique([m.name for m in self.metrics], "metric names")
        tb_ids = {t.id for t in self.testbenches}
        device_ids = {d.id for d in self.devices}
        variable_names = {v.name for v in self.variables}
        for metric in self.metrics:
            if metric.quantity is not None:
                if metric.device not in device_ids:
                    raise ValueError(f"metric {metric.name} references unknown device {metric.device}")
                continue
            if metric.testbench is None:
                if len(tb_ids) != 1:
                    raise ValueError(f"metric {metric.name} must name its testbench")
                metric.testbench = self.testbenches[0].id
            elif metric.testbench not in tb_ids:
                raise ValueError(f"metric {metric.name} references unknown testbench {metric.testbench}")
        for device in self.devices:
            for field, variable in device.variables.items():
                if variable not in variable_names:
                    raise ValueError(f"device {device.id} maps {field} to unknown variable {variable}")
            if device.topology is None:
                device.topology = device.default_topology()
            labels = set(device.ports)
            used = [p for pair in device.topology.drives for p in pair] + list(device.topology.grounded)
            if set(used) - labels or len(used) != len(set(used)) or len(used) != len(device.ports):
                raise ValueError(f"device {device.id}: topology must use each port exactly once ({device.ports})")
        for binding in self.bindings:
            if binding.testbench not in tb_ids:
                raise ValueError(f"binding {binding.instance} references unknown testbench {binding.testbench}")
            if binding.device not in device_ids:
                raise ValueError(f"binding {binding.instance} references unknown device {binding.device}")
            ports = next(d for d in self.devices if d.id == binding.device).ports
            if sorted(binding.terminals) != sorted(ports):
                raise ValueError(f"binding {binding.instance}: terminals must be a permutation of device ports {ports}")
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
    def device_ids(self) -> list[str]:
        return [d.id for d in self.devices]

    def device(self, device_id: str) -> Device:
        return next(d for d in self.devices if d.id == device_id)

    def device_fields(self, device: Device) -> dict[str, str]:
        """Generator field -> spec variable name. Explicit mapping, else ``<id>.<field>`` names,
        else (single device, no prefixed names) every variable is a generator field."""
        if device.variables:
            return dict(device.variables)
        prefix = f"{device.id}."
        mapped = {v.name[len(prefix):]: v.name for v in self.variables if v.name.startswith(prefix)}
        if mapped or len(self.devices) != 1 or any("." in v.name for v in self.variables):
            return mapped
        return {v.name: v.name for v in self.variables}

    def metrics_for_device(self, device_id: str) -> list[Metric]:
        return [m for m in self.metrics if m.device == device_id]

    @property
    def circuit_variables(self) -> list[str]:
        """Variables that reach the netlists: every variable no device consumes."""
        consumed = {name for d in self.devices for name in self.device_fields(d).values()}
        return [v.name for v in self.variables if v.name not in consumed]

    @property
    def corner_ids(self) -> list[str | None]:
        """Corner ids to run; ``[None]`` means the source point's own corner."""
        return [c.id for c in self.corners] or [None]

    def testbench(self, tb_id: str) -> Testbench:
        return next(t for t in self.testbenches if t.id == tb_id)

    def corner(self, corner_id: str) -> Corner:
        return next(c for c in self.corners if c.id == corner_id)

    def metrics_for(self, tb_id: str) -> list[Metric]:
        return [m for m in self.metrics if m.expression is not None and m.testbench == tb_id]

    def fingerprint(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def load_spec(path: str | Path) -> Spec:
    return Spec.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
