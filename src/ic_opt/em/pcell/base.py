from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel


@dataclass(frozen=True)
class GeometryGenerationResult:
    generator_id: str
    gds_path: Path
    top_cell: str
    manifest_path: Path
    emx_ports_path: Path


class PassiveDeviceGenerator(ABC):
    """A pcell generator: what a spec's device names (``generator:``) inside a plugin (``plugin:`` -- ``builtin:clean_port``
    or the absolute path of a file that exports ``PLUGIN_GENERATORS = {generator_id: instance}``).

    The pcell stage validates the device's config with ``config_model`` (it passes ``process_profile`` and
    ``port_order`` along with the device's fields), calls ``generate`` and then runs the product-scope DRC gate: the GDS
    is audited against the profile ``config.process_profile`` names, every conductor ``expected_conductors(config)``
    lists must be drawn, and no rule may be broken except ``max_width`` on the profile's fixture conductor (the ground
    ring, ``drc_audit.fixture_exemptions``). A config field ``drc_check: false`` skips the gate.
    """

    generator_id: str
    config_model: type[BaseModel]
    geometry_version: int | None = None      # None: an external plugin that does not account for its geometry's generation

    @abstractmethod
    def generate(self, config: BaseModel, *, outdir: Path, gds_name: str) -> GeometryGenerationResult:
        """Write ``<outdir>/<gds_name>`` (its top cell named after the file stem), ``emx_ports.txt`` and ``geometry_manifest.json``."""
        raise NotImplementedError

    def expected_conductors(self, config: BaseModel) -> list[str] | None:
        """The conductors a GDS built from ``config`` draws, by the names of the config's profile (``"M6"``, ``"AP"``):
        the DRC gate fails a build where any of them is missing, the sign that some geometry silently vanished.
        List every metal the device draws -- windings and the crossunders, bridges or tap stacks they imply. It runs
        with the profile's metal stack active (``ic_opt.em.pcell.stack``). None, the default, declares nothing: the
        gate cannot judge such a GDS and fails every build unless the config sets ``drc_check`` to false."""
        return None


@dataclass(frozen=True)
class EmxPort:
    """One ``-p name=signal[:reference]`` EMX port argument, as the generators write them to ``emx_ports.txt``."""

    name: str
    signal: str
    reference: str | None = None

    def argument(self) -> str:
        return f"{self.name}={self.signal}" + (f":{self.reference}" if self.reference else "")


def parse_emx_port_line(line: str) -> EmxPort:
    prefix = "-p "
    if not line.startswith(prefix):
        raise ValueError(f"unsupported EMX port line: {line}")
    name, mapping = line[len(prefix):].split("=", 1)
    signal, _, reference = mapping.partition(":")
    return EmxPort(name=name, signal=signal, reference=reference or None)


def read_emx_ports(path: Path) -> list[EmxPort]:
    return [parse_emx_port_line(line.strip()) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]

