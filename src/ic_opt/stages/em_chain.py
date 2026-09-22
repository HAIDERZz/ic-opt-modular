"""The EM stages: pcell (point) -> emx (point, cached) -> measure (device child) / bind_nport (testbench child).

    Point --pcell--> Geometry --emx--> SParams --measure-----> ChildResult          (unit = device)
                                               \\--bind_nport--> Netlist --spectre... (unit = testbench)

Working directory layout for a point: ``<sims/obs>/em/<device>/{<device>.gds, emx_ports.txt, geometry_manifest.json}``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydantic import ValidationError

from ic_opt import space
from ic_opt.em.pcell import get_generator
from ic_opt.em.pcell.base import EmxPort, read_emx_ports
from ic_opt.eval.stage import Resources, StageContext, StageFailure
from ic_opt.space import Point
from ic_opt.spec import Device, Spec, VariableKind


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
class Geometry:
    devices: dict[str, DeviceGeometry] = field(default_factory=dict)

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

    def fingerprint(self, point: Point) -> str | None:
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
                _audit(device, model, result.gds_path)
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


def _audit(device: Device, model, gds_path: Path) -> None:
    """em-opt's product-scope DRC gate: every conductor the config names must be drawn, no rule violation except M1 max_width (the ground fixture)."""
    if getattr(model, "drc_check", True) is False:
        return
    from ic_opt.em.pcell.drc_audit import (
        audit_gds,
        product_scope_record,
        require_layers_from_config,
    )

    expected = require_layers_from_config(device.generator, model.model_dump())
    record = product_scope_record(audit_gds(gds_path, model.process_profile), expected, ignore_findings=frozenset({("max_width", "M1")}))
    if record["outcome"] == "missing_layer":
        raise StageFailure(f"device {device.id}: DRC audit found missing product layers {record['missing']}")
    if record["outcome"] != "pass":
        raise StageFailure(f"device {device.id}: DRC audit found {len(record['violations'])} violation(s)",
                           *[f"[{v['kind']}] {v['layer']} x{v['count']}" for v in record["violations"]])
