"""The EM stages: pcell (point) -> emx (point, cached) -> measure (device child) / bind_nport (testbench child).

    Point --pcell--> Geometry --emx--> SParams --measure-----> ChildResult          (unit = device)
                                               \\--bind_nport--> Netlist --spectre... (unit = testbench)

Working directory layout for a point: ``<sims/obs>/em/<device>/{<device>.gds, emx_ports.txt, geometry_manifest.json}``.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ic_opt import space
from ic_opt.deck import Deck
from ic_opt.em import emx as emx_kernel
from ic_opt.em import measure as measure_kernel
from ic_opt.em import nport as nport_kernel
from ic_opt.em import touchstone
from ic_opt.em.pcell import get_generator
from ic_opt.em.pcell.base import EmxPort, read_emx_ports
from ic_opt.eval.stage import Resources, StageContext, StageFailure
from ic_opt.observation import ChildResult
from ic_opt.sim.ocean import WaveformExport
from ic_opt.space import Point
from ic_opt.spec import Device, Spec, VariableKind
from ic_opt.stages.spectre_chain import Extract, Netlist, Ocean, Spectre, render_netlist

if TYPE_CHECKING:
    from ic_opt.executor import Executor


@dataclass
class DeviceGeometry:
    device: str
    gds_path: Path                      # local file, <device>.gds; the top cell is the device id
    top_cell: str
    ports: list[EmxPort]                # generator-declared ports: label -> reference, as written to emx_ports.txt
    snp_order: list[str]                # semantic labels in the order the sNp columns will carry
    config: dict[str, object]           # the validated generator config
    gds_sha256: str


@dataclass
class DeviceSParams:
    device: str
    path: Path                          # local sNp
    port_labels: list[str]              # semantic labels in sNp column order
    z0: float


@dataclass
class Geometry:
    """Point-level state of the EM chain: every device's geometry, then its S-parameters as the emx stages fill them in."""

    devices: dict[str, DeviceGeometry] = field(default_factory=dict)
    sparams: dict[str, DeviceSParams] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {k: {**asdict(v), "gds_path": str(v.gds_path), "ports": [asdict(p) for p in v.ports]} for k, v in self.devices.items()}


def device_config(spec: Spec, device: Device, point: Point) -> dict[str, object]:
    """The generator's full configuration for one point: fixed fields + the device's variables + profile + ports."""
    values: dict[str, object] = {}
    for gen_field, variable in spec.device_fields(device).items():
        raw = point.params[variable]
        number, unit = space.parse_scalar(raw)
        if unit:
            raise StageFailure(f"device {device.id}: variable {variable}={raw!r} carries a unit suffix; generator fields are plain numbers")
        kind = next(v.kind for v in spec.variables if v.name == variable)
        values[gen_field] = int(number) if kind is VariableKind.INTEGER else float(number)
    return {**device.fixed, **values, "process_profile": device.profile, "port_order": list(device.ports)}


def snp_order(spec: Spec, device: Device) -> list[str]:
    """sNp column order for a device: the nport bindings' terminal order when it is bound, else the device's port order."""
    orders = {tuple(b.terminals) for b in spec.bindings if b.device == device.id}
    if len(orders) > 1:
        raise StageFailure(f"device {device.id}: its bindings disagree on terminal order {sorted(orders)}")
    return list(orders.pop()) if orders else list(device.ports)


class Pcell:
    """Build every device's GDS for the point; the product-scope DRC audit is part of building (``fixed.drc_check`` turns it off)."""

    name = "pcell"
    level = "point"
    resources = Resources()

    def __init__(self, spec: Spec) -> None:
        self.generators = {d.id: get_generator(d.generator, plugin_module=d.plugin) for d in spec.devices}
        # each device's geometry generation: a bump retires every observation built on the older geometry
        self.identity = json.dumps({d: g.geometry_version for d, g in sorted(self.generators.items())}, separators=(",", ":"))

    def fingerprint(self, point: Point, ctx: StageContext) -> str | None:
        return None

    def run(self, point: Point, ctx: StageContext) -> Geometry:
        geometry = Geometry()
        for device in ctx.spec.devices:
            generator = self.generators[device.id]
            config = device_config(ctx.spec, device, point)
            try:
                model = generator.config_model.model_validate(config)
            except ValidationError as exc:
                raise StageFailure(f"device {device.id}: invalid generator config", *[e["msg"] for e in exc.errors()][:3]) from exc
            outdir = ctx.workdir / "em" / device.id
            outdir.mkdir(parents=True, exist_ok=True)
            try:
                result = generator.generate(model, outdir=outdir, gds_name=f"{device.id}.gds")
                _audit(device, generator, model, result.gds_path)
            except (ValueError, OSError, RuntimeError) as exc:
                raise StageFailure(f"device {device.id}: {type(exc).__name__}: {exc}") from exc
            ports = read_emx_ports(result.emx_ports_path)
            labels = {p.signal for p in ports}
            missing = [label for label in device.ports if label not in labels]
            if missing:
                raise StageFailure(f"device {device.id}: generator drew no port for {missing} (has {sorted(labels)})")
            geometry.devices[device.id] = DeviceGeometry(
                device=device.id, gds_path=result.gds_path, top_cell=result.top_cell, ports=ports,
                snp_order=snp_order(ctx.spec, device), config=model.model_dump(mode="json"),
                gds_sha256=hashlib.sha256(result.gds_path.read_bytes()).hexdigest(),
            )
        (ctx.workdir / "em" / "geometry.json").write_text(json.dumps(geometry.to_json(), indent=1), encoding="utf-8")
        return geometry


def _audit(device: Device, generator, model, gds_path: Path) -> None:
    """em-opt's product-scope DRC gate: every conductor the generator declares for the config must be drawn
    (``PassiveDeviceGenerator.expected_conductors``: the built-in families and plugin generators alike), no rule
    violation except max_width on the profile's fixture conductor (the ground fixture, ``drc_audit.fixture_exemptions``)."""
    if getattr(model, "drc_check", True) is False:
        return
    from ic_opt.em.pcell.drc_audit import (
        audit_gds,
        expected_conductors,
        fixture_exemptions,
        product_scope_record,
    )

    profile = getattr(model, "process_profile", None)
    if profile is None:
        raise ValueError(f"generator {device.generator!r}: its config has no process_profile field, the profile the DRC gate audits against")
    expected = expected_conductors(generator, model)
    record = product_scope_record(audit_gds(gds_path, profile), expected, ignore_findings=fixture_exemptions(profile))
    if record["outcome"] == "missing_layer":
        raise StageFailure(f"device {device.id}: DRC audit found missing product layers {record['missing']}")
    if record["outcome"] != "pass":
        raise StageFailure(f"device {device.id}: DRC audit found {len(record['violations'])} violation(s)",
                           *[f"[{v['kind']}] {v['layer']} x{v['count']}" for v in record["violations"]])


class Emx:
    """One EMX run per device: ``Geometry`` -> ``Geometry`` with ``sparams[device]`` filled; cached by the engine on geometry +
    physics + the process file's content. The content is hashed on the executor host once, by ``resolve_identity`` (the
    engine calls it before it forms the pipeline fingerprint); the identity and every point's cache key use that value."""

    name = "emx"
    level = "point"
    runs = 1                            # one EMX simulation per point (unless the engine's cache serves it)

    def __init__(self, spec: Spec, device: str) -> None:
        if spec.em is None:
            raise ValueError("emx stage needs the spec's em section")
        self.em = spec.em
        self.device = device
        self.name = f"emx:{device}"
        self.resources = Resources(threads=spec.em.threads, memory_gb=spec.em.memory_gb)
        self.proc_sha256: str | None = None          # the process file's sha256 on the executor host, once resolved

    def resolve_identity(self, executor: Executor) -> None:
        """Hash the process file on the executor host, once: the identity carries its content, not its path."""
        if self.proc_sha256 is None:
            self.proc_sha256 = emx_kernel.process_file_digest(self.em, executor)

    @property
    def identity(self) -> str:
        """EMX physics + the process file's content: with the pcell's geometry generation, what a library generation is."""
        if self.proc_sha256 is None:
            raise RuntimeError(f"{self.name}: the identity carries the process file's content; call resolve_identity(executor) first")
        return json.dumps(emx_kernel.physics_key(self.em, proc_sha256=self.proc_sha256), sort_keys=True, separators=(",", ":"))

    def ports(self, geometry: Geometry) -> list[EmxPort]:
        g = geometry.devices[self.device]
        return emx_kernel.numbered_ports(g.snp_order, {p.signal: p.reference for p in g.ports})

    def fingerprint(self, geometry: Geometry, ctx: StageContext) -> str:
        self.resolve_identity(ctx.executor)          # already done when the engine formed the pipeline fingerprint
        g = geometry.devices[self.device]
        return emx_kernel.fingerprint(self.em, gds_sha256=g.gds_sha256, ports=self.ports(geometry), proc_sha256=self.proc_sha256)

    def run(self, geometry: Geometry, ctx: StageContext) -> Geometry:
        g = geometry.devices[self.device]
        snp = emx_kernel.run(self.em, ctx, device=self.device, gds_path=g.gds_path, top_cell=g.top_cell, ports=self.ports(geometry))
        geometry.sparams[self.device] = DeviceSParams(self.device, snp, list(g.snp_order), self.em.s_impedance)
        return geometry

    def save(self, geometry: Geometry, directory: Path) -> None:
        sp = geometry.sparams[self.device]
        (directory / sp.path.name).write_bytes(sp.path.read_bytes())
        log = sp.path.parent / "emx.log"
        if log.exists():
            (directory / "emx.log").write_bytes(log.read_bytes())

    def load(self, directory: Path, geometry: Geometry, ctx: StageContext) -> Geometry:
        g = geometry.devices[self.device]
        local = ctx.workdir / "em" / self.device
        local.mkdir(parents=True, exist_ok=True)
        cached = next(directory.glob("*.s*p"))
        (local / cached.name).write_bytes(cached.read_bytes())
        geometry.sparams[self.device] = DeviceSParams(self.device, local / cached.name, list(g.snp_order), self.em.s_impedance)
        return geometry


def emx_stages(spec: Spec) -> list[Emx]:
    return [Emx(spec, d.id) for d in spec.devices]


class BindNport:
    """Testbench child: render the circuit, drop each bound device's sNp under ``netlist/models/`` and point the nport instance at it."""

    name = "bind_nport"
    level = "child"
    unit = "testbench"
    resources = Resources()

    def __init__(self, deck: Deck) -> None:
        self.deck = deck

    def fingerprint(self, geometry: Geometry, ctx: StageContext) -> str | None:
        return None

    def run(self, geometry: Geometry, ctx: StageContext) -> Netlist:
        netlist = render_netlist(self.deck, ctx.point, ctx)
        text = netlist.text
        models = ctx.workdir / "netlist" / "models"
        for binding in [b for b in ctx.spec.bindings if b.testbench == ctx.unit]:
            sp = geometry.sparams.get(binding.device)
            if sp is None:
                raise StageFailure(f"{binding.instance}: device {binding.device} has no S-parameters")
            if sp.port_labels != list(binding.terminals):        # by construction (snp_order) unless the spec changed under the cache
                raise StageFailure(f"{binding.instance}: sNp columns are {sp.port_labels} but the instance's terminals are {binding.terminals}")
            models.mkdir(parents=True, exist_ok=True)
            target = models / f"{binding.device}{sp.path.suffix}"
            target.write_bytes(sp.path.read_bytes())
            try:
                patched = nport_kernel.patch(text, instance=binding.instance, replacement=f"models/{target.name}", n_ports=len(binding.terminals))
            except nport_kernel.NportError as exc:
                raise StageFailure(f"{ctx.unit}: {exc}") from exc
            text = patched.text
            ctx.trace.append({"label": f"bind:{binding.instance}", "device": binding.device, "signal_nodes": patched.signal_nodes,
                              "terminals": list(binding.terminals), "replaced": patched.original_file})
        return Netlist(text)


def em_circuit_pipeline(spec: Spec, deck: Deck, *, waveforms: list[WaveformExport] = ()) -> list:
    """pcell -> emx per device -> bind_nport -> spectre -> ocean -> extract, plus the measure chain when the spec has device metrics."""
    sim = spec.simulator
    devices = [Measure()] if any(m.quantity is not None for m in spec.metrics) else []
    return [Pcell(spec), *emx_stages(spec), BindNport(deck),
            Spectre(preset=sim.preset, threads=sim.threads_per_run, timeout_s=sim.timeout_s,
                    license_queue_timeout_s=sim.license_queue_timeout_s),
            Ocean(timeout_s=sim.timeout_s, waveforms=list(waveforms)), Extract(), *devices]


class Measure:
    """Device child: the spec's quantity metrics for this device from its S-parameters (curves at a frequency, read as
    ``measure.Quantities.at`` reads them, or scalars)."""

    name = "measure"
    level = "child"
    unit = "device"
    resources = Resources()

    def fingerprint(self, geometry: Geometry, ctx: StageContext) -> str | None:
        return None

    def run(self, geometry: Geometry, ctx: StageContext) -> ChildResult:
        device = ctx.spec.device(ctx.unit)
        sp = geometry.sparams.get(ctx.unit)
        if sp is None:
            raise StageFailure(f"device {ctx.unit} has no S-parameters")
        try:
            ts = touchstone.read(sp.path)
            topo = measure_kernel.Topology.from_labels(device.topology.drives, device.topology.grounded, sp.port_labels,
                                                       low_freq_max_hz=device.topology.low_freq_max_hz)
            q = measure_kernel.quantities(ts.freqs, ts.s, topo, z0=ts.z0)
        except (touchstone.TouchstoneError, measure_kernel.MeasureError, KeyError) as exc:
            raise StageFailure(f"device {ctx.unit}: {exc}") from exc
        metrics, issues = {}, []
        for metric in ctx.spec.metrics_for_device(ctx.unit):
            try:
                value = q.at(metric.quantity, metric.frequency_hz) if metric.frequency_hz is not None else q.scalars[metric.quantity]
            except (KeyError, measure_kernel.MeasureError) as exc:
                issues.append(f"metric {metric.name}: {exc}")
                continue
            if value is None or not math.isfinite(value):
                issues.append(f"metric {metric.name}: {metric.quantity} is {value}")
            else:
                metrics[metric.name] = float(value)
        (ctx.workdir / "quantities.json").write_text(json.dumps({k: v for k, v in q.scalars.items()}, indent=1), encoding="utf-8")
        return ChildResult(unit=ctx.unit, corner=None, metrics=metrics, issues=issues, status="ok" if not issues else "failed:measure")


def em_only_pipeline(spec: Spec) -> list:
    """pcell -> emx per device -> measure (device chain): characterization, library sweeps, device-level optimization."""
    return [Pcell(spec), *emx_stages(spec), Measure()]

