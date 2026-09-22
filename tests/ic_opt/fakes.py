"""Test doubles shared by the ic_opt tests."""

from __future__ import annotations

import shlex
from pathlib import Path

from ic_opt.executor import CommandResult, LocalExecutor
from ic_opt.spec import Spec


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

    def __init__(self, scratch_root: Path, metric_fn, *, fail_spectre=None, fail_ocean=None, nil_waveforms=()) -> None:
        super().__init__(scratch_root)
        self.metric_fn = metric_fn
        self.fail_spectre = fail_spectre or (lambda tb, corner: False)
        self.fail_ocean = fail_ocean or (lambda tb, corner: False)
        self.nil_waveforms = set(nil_waveforms)
        self.commands: list[str] = []

    def run(self, command, *, cwd=None, timeout_s=None, cshrc=None) -> CommandResult:
        self.commands.append(command)
        argv = shlex.split(command)
        if argv[0] == "which":                      # doctor: the fake host has the Cadence tools
            return CommandResult(0, "\n".join(f"/cad/bin/{tool}" for tool in argv[1:]) + "\n", "", argv, 0.01)
        if argv[0] == "lmstat":
            return CommandResult(0, "Users of spectre:  (Total of 10 licenses issued;  Total of 2 licenses in use)\n", "", argv, 0.01)
        if argv[0] not in ("spectre", "ocean"):
            return super().run(command, cwd=cwd, timeout_s=timeout_s, cshrc=cshrc)
        if argv[:2] == ["spectre", "-V"]:
            return CommandResult(0, "spectre version 23.1.0.242.isr4 64bit\n", "", argv, 0.01)
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
