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
    """One process / environment corner: the model section (and file) the include line takes, top-level parameter values,
    and ``options`` -- ``key=value`` pairs set on the netlist's ``simulatorOptions`` statement, ``temp`` above all: an ADE
    export writes the simulation temperature there as a literal that no parameter follows (N-38, 2026-09-27)."""

    id: str
    model_section: str | None = None
    model_file: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    options: dict[str, str] = Field(default_factory=dict)     # simulatorOptions key -> value, e.g. temp: "125"
    description: str = ""

    @model_serializer(mode="wrap")
    def _dump(self, handler):
        """Empty options stay out of the dump, so the specs written before the field existed keep their fingerprints."""
        data = handler(self)
        if not self.options:
            data.pop("options", None)
        return data

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

    @field_validator("options", mode="before")
    @classmethod
    def _opts(cls, value: dict) -> dict[str, str]:
        out = {}
        for name, raw in (value or {}).items():
            _name(name, "corner option name")
            out[name] = _compact_token(str(raw), f"corner option {name}")   # YAML turns 125 into an int; Spectre gets the text
        return out


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


class SaturationMargin(Model):
    """The transistors a saturation-margin metric watches (T17.11), by the instance names of the testbench's
    operating-point table (``ChildResult.operating_points``, which the digest prints). The metric is the smallest
    ``|vds| - |vdsat|`` over them, computed by the extract stage. A switch, or any device meant to leave saturation, is
    not listed. ``Metric`` checks the list, so that its messages name the metric."""

    instances: list[str]


class Metric(Model):
    """A scalar the evaluation must produce: an OCEAN expression on a testbench, a quantity of an EM device, or the worst
    saturation margin of named transistors, which the extract stage computes from the operating points of the testbench's
    DC analysis (T17.11) instead of OCEAN."""

    name: str
    unit: str = Field(min_length=1)
    expression: str | None = None            # OCEAN expression (testbench metrics)
    testbench: str | None = None
    result: str | None = None
    required_signals: list[str] = Field(default_factory=list)
    device: str | None = None                # EM device metrics: quantity of the device's S-parameters
    quantity: str | None = None              # e.g. Lp, Qp, k (curves, need frequency_hz) or Lp_res, Qp_peak, SRF_p (scalars)
    frequency_hz: float | None = None
    saturation_margin: SaturationMargin | None = None   # T17.11: from the operating points; no expression, device, quantity

    @model_serializer(mode="wrap")
    def _dump(self, handler):
        """An unset saturation margin stays out of the dump, so the specs written before it existed keep their legacy
        fingerprint (``Spec._legacy_fingerprint``); ``Spec.problem()`` leaves every unset field out anyway."""
        data = handler(self)
        if self.saturation_margin is None:
            data.pop("saturation_margin", None)
        return data

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
        if self.saturation_margin is not None:
            return self._saturation_source()
        if (self.expression is None) == (self.quantity is None):
            raise ValueError(f"metric {self.name}: give either expression (testbench) or quantity (device)")
        if self.quantity is not None and self.device is None:
            raise ValueError(f"metric {self.name}: a quantity metric must name its device")
        if self.expression is not None and self.device is not None:
            raise ValueError(f"metric {self.name}: an expression metric belongs to a testbench, not a device")
        return self

    def _saturation_source(self) -> Metric:
        """A saturation-margin metric reads one testbench's operating points: it names that testbench -- in a spec of one
        testbench too, where an expression metric is given it by default (T17.11 asks for it present) -- and at least one
        transistor, each once."""
        named = [field for field in ("expression", "device", "quantity") if getattr(self, field) is not None]
        if named:
            raise ValueError(f"metric {self.name}: a saturation_margin metric takes no {', '.join(named)}; the extract "
                             "stage computes it from the operating points")
        if self.testbench is None:
            raise ValueError(f"metric {self.name}: a saturation_margin metric must name its testbench")
        instances = self.saturation_margin.instances
        if not instances:
            raise ValueError(f"metric {self.name}: saturation_margin needs at least one instance")
        if any(not name for name in instances):
            raise ValueError(f"metric {self.name}: saturation_margin instances must be non-empty names")
        twice = sorted({name for name in instances if instances.count(name) > 1})
        if twice:
            raise ValueError(f"metric {self.name}: saturation_margin instances must be distinct ({', '.join(twice)} twice)")
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
    """Spectre resources are the user's to state (T15): no thread count, job count or timeout is assumed. Nor is a
    license queue wait: ``license_queue_timeout_s`` is passed as ``+lqtimeout`` only when set; unset, Spectre waits
    as it does by itself.

    ``strategy_threads`` (N-73): the threads the strategy's own computation may use while it proposes a batch, on the
    machine running ic-opt -- ``metric_gp`` (numpy / scipy / scikit-learn through BLAS and OpenMP), ``openbox_*`` (their
    BLAS) and ``turbo`` (torch too). Without a limit these libraries take every core of that machine. One by default:
    the least a strategy can run on, which assumes nothing about the machine (``opt.suggest`` applies it)."""

    preset: Literal["cx", "ax", "mx", "lx", "vx"] = "ax"
    threads_per_run: int = Field(ge=1)
    parallel_jobs: int = Field(ge=1)
    timeout_s: int = Field(gt=0)
    strategy_threads: int = Field(default=1, ge=1)   # N-73: the strategy's BLAS / OpenMP / torch threads (opt.suggest)
    license_check: bool = True
    license_queue_timeout_s: int | None = Field(default=None, ge=0)   # Spectre +lqtimeout <s>; None: the flag is not passed
    keep_failed_runs: bool = True
    keep_successful_runs: bool = True
    operating_points: bool = True        # T17.5: add the statements Spectre needs to write them, read them per child
    # T17.8: a point stops at its first child that fails it (ic_opt.eval.schedule); None: a testbench stops it when a point
    # needs 20 or more simulations (T17.9 revision 2, blocks.evaluate.stop_wanted), an EM or library device -- measured
    # first -- whatever the count (N-63, blocks.evaluate.stop_kinds); false: nothing stops a point
    stop_at_first_failure: bool | None = None

    engine: Literal["spectre_x"] = "spectre_x"
    output_format: Literal["psfxl"] = "psfxl"

    @model_serializer(mode="wrap")
    def _dump(self, handler):
        """An unset license queue timeout and stop at the first failure, operating points left on and one strategy thread
        stay out of the dump, so the specs written before they existed keep their legacy fingerprint
        (``Spec._legacy_fingerprint``)."""
        data = handler(self)
        if self.license_queue_timeout_s is None:
            data.pop("license_queue_timeout_s", None)
        if self.strategy_threads == 1:
            data.pop("strategy_threads", None)
        if self.operating_points:
            data.pop("operating_points", None)
        if self.stop_at_first_failure is None:
            data.pop("stop_at_first_failure", None)
        return data


class Budget(Model):
    max_simulations: int = Field(ge=1)


# -- EM devices --------------------------------------------------------------------


class Topology(Model):
    """Ideal-balun measurement topology for a device's S-parameters (ic_opt.em.measure): ``drives``, one (plus, minus) port
    pair per differential drive -- the primary, then the secondary -- and ``grounded``, the ports held at 0 V.

    A drive pushes its current in at plus and out at minus. Only the sign of k (``k_lf``, ``k`` at a frequency) depends on
    that: k > 0 when the two drives' currents make the windings' fluxes add. A device without a topology gets
    ``Device.default_topology``, whose four-port form reverses the secondary -- (P1, N1) and (N2, P2) for ports P1, N1, P2,
    N2 -- because that is how the built-in families wind it. A generator of your own whose secondary winds the other way
    measures k < 0 under that default, and the measure stage says so in the point's issues: state its drives instead,
    ``topology: {drives: [[P1, N1], [P2, N2]]}`` (every port exactly once: taps under ``grounded``)."""

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


DEFAULT_PLUGIN = "builtin:clean_port"
_PREFER_RE = re.compile(r"^(max|min):[A-Za-z_][A-Za-z0-9_]*$")


class LibrarySource(Model):
    """Where a library device comes from (T18.2B, ``docs/refactor/T18_2B_LIBRARY_DEVICE_SPEC.md``): one table (``stratum``)
    of the library at ``root``, its rows seen by their electrical values at ``frequency_hz`` (``ic_opt.library.index``).

    ``root`` says where the library sits on the machine running ic-opt, not which problem this is: it is left out of
    ``Spec.problem()`` (as ``em.binary`` is), so a library moved or mounted elsewhere keeps the problem's identity; the
    stratum, the frequency, the margin and ``prefer`` are part of it. ``srf_margin`` (>= 1) also leaves out the rows whose
    system SRF is at or below margin x frequency (the index's own margin, the manifest's, holds anyway); ``prefer``
    (``max:<column>`` / ``min:<column>``, an index column) says which row of a combination holding several is taken, the
    index's default (``max:Qmin``, else ``max:Qp``) when unset."""

    root: str = Field(min_length=1)                   # the directory holding library.yaml, on the machine running ic-opt
    stratum: str = Field(min_length=1)                # one table
    frequency_hz: float = Field(gt=0, allow_inf_nan=False)    # the working frequency the electrical values are taken at
    srf_margin: float | None = Field(default=None, ge=1.0, allow_inf_nan=False)
    prefer: str | None = None                         # max:<column> / min:<column>; None: the index's default

    @field_validator("root")
    @classmethod
    def _root(cls, value: str) -> str:
        if not Path(value).expanduser().is_absolute():
            raise ValueError(f"library root {value!r} must be an absolute path: the directory holding library.yaml, on the "
                             "machine running ic-opt")
        return value

    @field_validator("prefer")
    @classmethod
    def _prefer(cls, value: str | None) -> str | None:
        if value is not None and not _PREFER_RE.match(value):
            raise ValueError(f"library prefer {value!r}: expected max:<column> or min:<column>, an index column (max:Qmin)")
        return value


class Device(Model):
    """One EM device: a pcell generator instance the EM stages build, simulate with EMX and bind -- ``generator`` and
    ``profile`` -- or, with ``library`` (T18.2B), a device taken from a library table: its ``variables`` map index columns
    (``Lp``, ``Ls``, ``k``, ...) to spec variables of electrical values, the combinations that exist are the ones a row sits
    on (``ic_opt.library.link``), and evaluating a point binds that row's own sNp. A library device's generator, profile,
    fixed fields and plugin are the table's; its ``topology``, when it states none, too (the table's part's, measured as
    the library measures it)."""

    id: str
    generator: str | None = Field(default=None, min_length=1)    # generator id inside the plugin, e.g. clean_port_xfm_bs
    plugin: str = DEFAULT_PLUGIN                              # builtin:<name> or an absolute path to a plugin .py
    profile: str | None = Field(default=None, min_length=1)      # process rule profile id (resolved via IC_OPT_PROFILE_DIRS)
    ports: list[str] = Field(min_length=1)                    # semantic port labels in the generator's fixed order
    fixed: dict[str, object] = Field(default_factory=dict)    # generator fields that are not optimized (passed through)
    variables: dict[str, str] = Field(default_factory=dict)   # generator field (library: index column) -> spec variable
    topology: Topology | None = None                          # default derived from the port set (library: the table's)
    library: LibrarySource | None = None                      # T18.2B: the device comes from a library table's rows

    @model_serializer(mode="wrap")
    def _dump(self, handler):
        """An unset library stays out of the dump, so every spec written before it existed keeps its dump and both
        fingerprints."""
        data = handler(self)
        if self.library is None:
            data.pop("library", None)
        return data

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

    @model_validator(mode="after")
    def _source(self) -> Device:
        """Drawn and simulated (generator and profile) or taken from a library table (library), never both."""
        if self.library is None:
            missing = [name for name in ("generator", "profile") if getattr(self, name) is None]
            if missing:
                raise ValueError(f"device {self.id}: {' and '.join(missing)} missing -- give generator and profile (a device "
                                 "the pcell draws and EMX simulates) or library (a device taken from a library table's rows)")
            return self
        stated = [name for name in ("generator", "profile") if getattr(self, name) is not None]
        stated += ["fixed"] if self.fixed else []
        stated += ["plugin"] if self.plugin != DEFAULT_PLUGIN else []
        if stated:
            raise ValueError(f"device {self.id} comes from library table {self.library.stratum}: its generator, profile, "
                             f"fixed fields and plugin are the table's -- leave out {', '.join(stated)}")
        if not self.variables:
            raise ValueError(f"device {self.id} comes from library table {self.library.stratum}: give its variables, index "
                             f"column -> spec variable (e.g. variables: {{Lp: {self.id}.Lp, k: {self.id}.k}})")
        return self

    def default_topology(self) -> Topology:
        """The topology of a device that states none, from its ports other than the ``CT*`` taps (which are grounded): two
        ports, one differential drive (ports[0], ports[1]); four ports, two drives, the primary (ports[0], ports[1]) and
        the secondary reversed, (ports[3], ports[2]) -- the built-in families' winding sense, so their k is positive
        (``Topology``: a generator whose secondary winds the other way states its topology)."""
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
    """EMX settings: one set per spec, applied to every device. Threads, memory and timeout have no default:
    what one EMX run may take is the user's statement about their machine (T15, D1)."""

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
    threads: int = Field(ge=1)                                # --parallel; required
    memory_gb: float = Field(gt=0)                            # --max-memory; required
    parallel_jobs: int | None = Field(default=None, ge=1)     # EMX runs at once across the workers; unset: the workers
    simultaneous_frequencies: int | None = 0                  # explicit 0 after the 2026-07-09 incident
    timeout_s: int = Field(gt=0)                              # required
    verbose: int | None = 2
    extra_args: list[str] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _dump(self, handler):
        """An unset parallel_jobs stays out of the dump: the stamps written before the field existed keep their values."""
        data = handler(self)
        if self.parallel_jobs is None:
            data.pop("parallel_jobs", None)
        return data

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
    """A Spectre nport instance in a testbench that takes a device's S-parameters. ``terminals`` names the device port at
    each of the instance's ports, in the instance's order; a ``null`` entry is a port of the instance the device does not
    take (a tap the schematic wires twice, a grounded spare), which the bound instance loses. The sNp's columns follow
    the named entries in order (``kept``). N-49, 2026-09-27: the mixer's tapped transformer is a 10-port nport around a
    6-port device."""

    testbench: str
    instance: str = Field(min_length=1)
    device: str
    terminals: list[str | None] = Field(min_length=1)         # instance port -> device port label, or null (dropped)

    @property
    def kept(self) -> list[str]:
        """The device ports the instance takes, in the instance's order: the sNp column order."""
        return [t for t in self.terminals if t is not None]

    @property
    def kept_positions(self) -> list[int]:
        return [i for i, t in enumerate(self.terminals) if t is not None]

    @field_validator("instance")
    @classmethod
    def _inst(cls, value: str) -> str:
        return _compact_token(value, "binding instance")


class Spec(Model):
    project: str
    description: str = ""
    testbenches: list[Testbench] = Field(default_factory=list)   # circuit testbenches (Maestro exports)
    devices: list[Device] = Field(default_factory=list)          # EM devices (pcell generators, or library tables: T18.2B)
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
            if metric.saturation_margin is not None and not self.simulator.operating_points:   # T17.11: nothing to read
                raise ValueError(f"metric {metric.name} reads the operating points; set simulator.operating_points true")
            if metric.testbench is None:
                if len(tb_ids) != 1:
                    raise ValueError(f"metric {metric.name} must name its testbench")
                metric.testbench = self.testbenches[0].id
            elif metric.testbench not in tb_ids:
                raise ValueError(f"metric {metric.name} references unknown testbench {metric.testbench}")
        _library_devices(self)
        for device in self.devices:
            for field, variable in device.variables.items():
                if variable not in variable_names:
                    raise ValueError(f"device {device.id} maps {field} to unknown variable {variable}")
            if device.topology is None:
                if device.library is not None:         # the table's (ic_opt.library.link): nothing to default or check here
                    continue
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
            if sorted(binding.kept) != sorted(ports):
                raise ValueError(f"binding {binding.instance}: the named terminals must be a permutation of device ports {ports} "
                                 f"(null marks an instance port the device does not take)")
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

    @property
    def library_devices(self) -> list[Device]:
        """The devices taken from a library table (T18.2B): every device of the spec, or none (a mixture is refused)."""
        return [d for d in self.devices if d.library is not None]

    def device_fields(self, device: Device) -> dict[str, str]:
        """Generator field -> spec variable name. Explicit mapping, else ``<id>.<field>`` names, else -- in a spec of one
        device and no testbench, its names without a prefix -- every variable is a generator field. With testbenches the
        unprefixed names are the circuit's: taking them all for the device left the netlists without their parameters
        (N-51, 2026-09-28). A library device (T18.2B) always maps explicitly: index column -> spec variable."""
        if device.variables:
            return dict(device.variables)
        prefix = f"{device.id}."
        mapped = {v.name[len(prefix):]: v.name for v in self.variables if v.name.startswith(prefix)}
        if mapped or len(self.devices) != 1 or self.testbenches or any("." in v.name for v in self.variables):
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

    def problem(self) -> dict:
        """The problem this spec states: ``model_dump(mode="json")`` without how it is run -- the simulator's parallel jobs,
        threads per run, timeout, license check, license queue timeout, retention, operating points and stop at the first
        failure, EMX threads, memory cap, timeout and verbosity, the budget, and where a library device's library sits
        (``library.root``: the library's content is the pipeline's identity, ``Pick``'s). Unset (None) fields are left out
        too, so an optional field added to the schema later leaves every existing problem's identity alone."""
        return self.model_dump(mode="json", exclude=_NOT_PROBLEM, exclude_none=True)

    def fingerprint(self) -> str:
        """The problem's identity, a hash of ``problem()``: observations are reused and counted per problem, so a project
        moved to a smaller machine or given a bigger budget keeps its history."""
        return _digest(self.problem())

    def _legacy_fingerprint(self) -> str:
        """The identity versions before T15.2 stamped: a hash of the whole spec, resources and budget included. The engine
        still takes it as this problem until ``ic-opt migrate-store`` restamps the observations. Frozen: it must keep
        reproducing those stamps, so a field added to the schema stays out of the dump while unset (``Topology._dump``)."""
        return _digest(self.model_dump(mode="json"))


def _unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def _library_devices(spec: Spec) -> None:
    """T18.2B: a spec's devices all come from a library table or none does (a mixture is refused in this first version),
    and a library device's variables are its own -- one spec variable per index column, none shared -- and hold electrical
    values, whose text may carry a Spectre scale suffix (``space.si_value``: ``T G M k m u n p f a``, nothing else)."""
    from ic_opt import space

    library = [d.id for d in spec.devices if d.library is not None]
    if not library:
        return
    drawn = [d.id for d in spec.devices if d.library is None]
    if drawn:
        raise ValueError(f"a spec's devices all come from a library table or none does: {', '.join(library)} from a library, "
                         f"{', '.join(drawn)} drawn and simulated with EMX -- take every device from a library, or none")
    variables = {v.name: v for v in spec.variables}
    taken: dict[str, str] = {}
    for device in spec.devices:
        for column, name in device.variables.items():
            if name in taken:
                raise ValueError(f"device {device.id} maps {column} to {name}, which {taken[name]} maps already: each index "
                                 "column takes a spec variable of its own")
            taken[name] = f"device {device.id} ({column})"
            if name not in variables:
                continue                                  # the unknown variable is named by the check that follows
            for label in ("lower", "upper", "step"):
                try:
                    space.si_value(getattr(variables[name], label))
                except ValueError as exc:
                    raise ValueError(f"device {device.id}: variable {name} {label}: {exc}") from exc


# -- identity -------------------------------------------------------------------------

# How a problem is run, not which problem it is: left out of Spec.problem() and so of Spec.fingerprint().
_NOT_PROBLEM = {
    "simulator": {"parallel_jobs", "threads_per_run", "timeout_s", "license_check", "license_queue_timeout_s",
                  "keep_failed_runs", "keep_successful_runs", "operating_points",   # operating points: read beside the metrics, never change one
                  "stop_at_first_failure",     # which children of a point run, not what any of them gives: a stopped point is never reused
                  "strategy_threads"},         # how fast a batch is proposed, on the machine running ic-opt
    "em": {"threads", "memory_gb", "parallel_jobs", "timeout_s", "verbose", "binary"},      # the binary: a path on the host, not physics
    "devices": {"__all__": {"library": {"root"}}},     # T18.2B: where the library sits, not which problem (its content: the pipeline's)
    "budget": True,
}


def _digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def load_spec(path: str | Path) -> Spec:
    return Spec.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
