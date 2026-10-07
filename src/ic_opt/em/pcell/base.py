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
    # The conductor the ground fixture was drawn on (T19.1; the manifest's ``geometry.fixture_metal``): the DRC gate
    # exempts max_width there. None: the profile's fixture conductor, the bottom metal (no fixture, or not reported).
    fixture_metal: str | None = None
    # The box (x0, y0, x1, y1, um, rounded to 0.001) around everything the device drew, taken before the ground fixture
    # was added (T19.2; the manifest's ``geometry.device_bbox_um``): the footprint. None: not reported.
    device_bbox_um: tuple[float, float, float, float] | None = None
    # The rule the fixture's metal was chosen under (T19.2's ``ground_fixture.metal_rule``), for the DRC gate (T19.5):
    # "free" -- the metal holds the ring and the stubs and nothing else, so the gate exempts spacing among them there;
    # "shared" -- the device's internal shapes too. None: no metal was chosen (the fixture conductor, the bottom metal),
    # a patterned ground shield is drawn on it, or not reported -- the gate exempts max_width alone.
    fixture_metal_rule: str | None = None
    # The chamfer (um) each ground stub was drawn with, {port name: {side: chamfer}}, sides named by the way they face
    # ("bottom" / "top" for a stub on the ring's left or right, "left" / "right" on its bottom or top; N-65; the
    # manifest's ``geometry.stub_chamfers_um``): present only when two neighbouring stubs' facing chamfers were
    # shortened to keep the fixture metal's minimum spacing between them at the ring. None: every side has
    # ``ground_fixture.stub_chamfer_um``, or not reported. Never hashed (the dict on the frozen dataclass is safe).
    stub_chamfers_um: dict[str, dict[str, float]] | None = None


class PassiveDeviceGenerator(ABC):
    """A pcell generator: what a spec's device names (``generator:``) inside a plugin (``plugin:`` -- ``builtin:clean_port``
    or the absolute path of a file that exports ``PLUGIN_GENERATORS = {generator_id: instance}``).

    The pcell stage validates the device's config with ``config_model`` (it passes ``process_profile`` and
    ``port_order`` along with the device's fields), calls ``generate`` and then runs the product-scope DRC gate: the GDS
    is audited against the profile ``config.process_profile`` names, every conductor ``expected_conductors(config)``
    lists must be drawn, and no rule may be broken except ``max_width`` on the conductor of the ground ring
    (``drc_audit.fixture_exemptions``): the result's ``fixture_metal``, else the profile's fixture conductor -- and, when
    the result's ``fixture_metal_rule`` is ``"free"`` (the ring and the stubs alone on that metal), the spacing rules
    there too. A config field ``drc_check: false`` skips the gate.
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

