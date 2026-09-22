"""Test doubles shared by the ic_opt tests."""

from __future__ import annotations

import importlib.util
import shlex
from pathlib import Path

import pytest

from ic_opt.executor import CommandResult, LocalExecutor
from ic_opt.spec import Spec

needs_turbo = pytest.mark.skipif(importlib.util.find_spec("torch") is None, reason="turbo strategy needs the [turbo] extra (torch)")


def minimal_spec(**overrides) -> dict:
    base = {
        "project": "demo",
        "testbenches": [{"id": "tb", "maestro_point_root": "/x", "virtuoso_library": "lib", "cell": "c", "test_name": "t"}],
        "variables": [
            {"name": "F", "kind": "integer", "lower": "20", "upper": "30", "step": "2"},
            {"name": "W", "kind": "continuous_step", "lower": "0.6u", "upper": "1.2u", "step": "0.2u"},
        ],
        "metrics": [{"name": "NF", "unit": "dB", "expression": 'value(getData("NF"))'}],
        "constraints": [{"metric": "NF", "op": "lt", "value": "9 dB"}],
        "objective": {"direction": "minimize", "expression": "NF"},
        "simulator": {"parallel_jobs": 2, "timeout_s": 60},
        "budget": {"max_simulations": 10},
    }
    base.update(overrides)
    return base


def make_spec(**overrides) -> Spec:
    return Spec.model_validate(minimal_spec(**overrides))


class FakeSpectreExecutor(LocalExecutor):
    """A LocalExecutor whose ``run`` fakes ``spectre`` and ``ocean``.

    ``metric_fn(params_text, testbench, corner) -> dict[str, float]`` decides the
    scalars each OCEAN run reports; the netlist text is parsed for ``NAME=value``
    parameters so tests can make metrics depend on the point. Set ``fail_spectre``
    or ``fail_ocean`` to a predicate on (testbench, corner) to inject failures.
    Waveform exports requested by the probe script are written as CSV unless
    ``nil_waveforms`` names them (OCEAN returned nil).
    """

    def __init__(self, scratch_root: Path, metric_fn=None, *, fail_spectre=None, fail_ocean=None, nil_waveforms=(),
                 snp_fn=None, fail_emx=None) -> None:
        super().__init__(scratch_root)
        self.metric_fn = metric_fn or (lambda p, tb, c: {})
        self.fail_spectre = fail_spectre or (lambda tb, corner: False)
        self.fail_ocean = fail_ocean or (lambda tb, corner: False)
        self.nil_waveforms = set(nil_waveforms)
        self.snp_fn = snp_fn or synthetic_snp          # (argv, n_ports, z0) -> touchstone text
        self.fail_emx = fail_emx or (lambda device: False)
        self.commands: list[str] = []
        self.emx_runs = 0

    def run(self, command, *, cwd=None, timeout_s=None, cshrc=None) -> CommandResult:
        self.commands.append(command)
        argv = shlex.split(command)
        if argv[0] == "which":                      # doctor: the fake host has the Cadence tools
            return CommandResult(0, "\n".join(f"/cad/bin/{tool}" for tool in argv[1:]) + "\n", "", argv, 0.01)
        if argv[0] == "lmstat":
            return CommandResult(0, "Users of spectre:  (Total of 10 licenses issued;  Total of 2 licenses in use)\n", "", argv, 0.01)
        if argv[:2] == ["spectre", "-V"]:
            return CommandResult(0, "spectre version 23.1.0.242.isr4 64bit\n", "", argv, 0.01)
        if argv[0] == "sha256sum":                   # the emx stage hashes the process file on the host
            return CommandResult(0, f"{'ab' * 32}  {argv[1]}\n", "", argv, 0.01)
        if argv[0] == "emx":
            work = Path(cwd)
            device = work.name
            self.emx_runs += 1
            if self.fail_emx(device):
                return CommandResult(3, "", "emx: license unavailable", argv, 0.01)
            s_file = next(a for a in argv if a.startswith("--s-file=")).split("=", 1)[1]
            z0 = float(next(a for a in argv if a.startswith("--s-impedance=")).split("=", 1)[1])
            n_ports = sum(a == "-p" for a in argv)
            (work / s_file).write_text(self.snp_fn(argv, n_ports, z0))
            (work / "emx.log").write_text("fake emx\n")
            return CommandResult(0, "", "", argv, 0.01)
        if argv[0] not in ("spectre", "ocean"):
            return super().run(command, cwd=cwd, timeout_s=timeout_s, cshrc=cshrc)
        work = Path(cwd)
        if argv[0] == "spectre":
            tb, corner = _tb_corner(work.parent)
            if self.fail_spectre(tb, corner):
                return CommandResult(1, "", "spectre: license lost", argv, 0.01)
            psf = work.parent / "psf"
            psf.mkdir(exist_ok=True)
            (psf / "spectre.out").write_text("ok\n")
            return CommandResult(0, "spectre done\n", "", argv, 0.01)
        if argv[0] == "ocean":
            tb, corner = _tb_corner(work)
            if self.fail_ocean(tb, corner):
                return CommandResult(2, "", "ocean: replay failed", argv, 0.01)
            params = _params_from_netlist((work / "netlist" / "input.scs").read_text())
            rows = ["metric\tvalue\tunit\tstatus\tmessage"]
            for name, value in self.metric_fn(params, tb, corner).items():
                rows.append(f"{name}\t{value!r}\tx\tpass\t" if value is not None else f"{name}\t\tx\tfail\tnon_scalar")
            (work / "metrics" / "ocean_scalars.tsv").write_text("\n".join(rows) + "\n")
            for line in (work / "metrics" / "probe.ocn").read_text().splitlines():
                if line.startswith("; waveform export: ") and (name := line.split(": ", 1)[1]) not in self.nil_waveforms:
                    (work / "metrics" / "waveforms" / f"{name}.csv").write_text("freq,value\n1e9,1.0\n2e9,1.5\n")
        return CommandResult(0, "", "", argv, 0.01)


def _tb_corner(child_dir: Path) -> tuple[str, str | None]:
    corner = child_dir.name
    return child_dir.parent.name, (None if corner == "nominal" else corner)


def _params_from_netlist(text: str) -> dict[str, str]:
    for line in text.splitlines():
        if line.startswith("parameters"):
            return dict(token.split("=", 1) for token in line.split()[1:])
    return {}


def synthetic_snp(argv: list[str], n_ports: int, z0: float, *, freqs=(1e9, 5e9, 10e9)) -> str:
    """A small, header-valid Touchstone file: a lossy coupled inductor (L=1 nH, R=1 Ω, k=0.5) in the EMX RI format."""
    import numpy as np

    lines = ["! Touchstone simulation data from EMX version 2024.1.0 (fake)", "! EMX was run on fake as:", "! " + " ".join(argv[:3]), f"# Hz S RI R {z0:g}"]
    for f in freqs:
        w = 2 * np.pi * f
        z = np.full((n_ports, n_ports), 0.5j * w * 1e-9, dtype=complex)
        np.fill_diagonal(z, 1.0 + 1j * w * 1e-9)
        s = np.linalg.solve(z + z0 * np.eye(n_ports), z - z0 * np.eye(n_ports))
        if n_ports == 2:
            s = s.T                                   # touchstone's 2-port column order S11 S21 S12 S22
        lines.append(f"{f:.0f} " + " ".join(f"{v.real:.9e} {v.imag:.9e}" for v in s.reshape(-1)))
    return "\n".join(lines) + "\n"

