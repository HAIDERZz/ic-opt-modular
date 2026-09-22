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
    generator_id: str
    config_model: type[BaseModel]
    geometry_version: int | None = None      # None: an external plugin that does not account for its geometry's generation

    @abstractmethod
    def generate(
        self,
        config: BaseModel,
        *,
        outdir: Path,
        gds_name: str,
        top_cell: str | None = None,
    ) -> GeometryGenerationResult:
        raise NotImplementedError


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

