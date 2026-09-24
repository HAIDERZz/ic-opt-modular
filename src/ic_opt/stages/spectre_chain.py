"""The four child-level stages of the Spectre/OCEAN pipeline.

    Point --render--> Netlist --spectre--> RawSim --ocean--> Scalars --extract--> ChildResult

Working directory layout (identical on the local and the remote side):

    <dir>/netlist/input.scs   <dir>/psf/   <dir>/metrics/{probe.ocn, ocean.log, ocean_scalars.tsv, waveforms/}
"""

from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass

from ic_opt.deck import Deck
from ic_opt.eval.stage import Resources, StageContext, StageFailure
from ic_opt.observation import ChildResult
from ic_opt.sim import netlist as netlist_kernel
from ic_opt.sim import ocean as ocean_kernel
from ic_opt.sim.ocean import Scalars, WaveformExport
from ic_opt.space import Point

OCEAN_ATTEMPTS = 3
TRANSIENT_SOCKET_FAILURE = "can't create server socket"


@dataclass
class Netlist:
    text: str


@dataclass
class RawSim:
    psf_dir: str            # executor-side path
    returncode: int


class Render:
    unit = "testbench"
    name = "render"
    level = "child"
    resources = Resources()

    def __init__(self, deck: Deck) -> None:
        self.deck = deck

    def fingerprint(self, point: Point, ctx: StageContext) -> str | None:
        return None

    def run(self, point: Point, ctx: StageContext) -> Netlist:
        return render_netlist(self.deck, point, ctx)


def render_netlist(deck: Deck, point: Point, ctx: StageContext) -> Netlist:
    """The child's netlist: the deck template for (testbench, corner) with the circuit variables filled in; support files copied alongside."""
    try:
        template = deck.template(ctx.unit, ctx.corner)
    except KeyError as exc:
        raise StageFailure(f"deck has no template for {ctx.unit}/{ctx.corner}") from exc
    bundle = deck.bundle(ctx.unit)
    if bundle is not None:   # Maestro's support files (.modelFiles, .designVariables, ...) travel with the deck
        shutil.copytree(bundle, ctx.workdir / "netlist", dirs_exist_ok=True)
    circuit = {name: point.params[name] for name in ctx.spec.circuit_variables}
    return Netlist(netlist_kernel.render(template, circuit))


class Spectre:
    unit = "testbench"
    name = "spectre"
    level = "child"

    def __init__(self, *, preset: str, threads: int, timeout_s: int, output_format: str = "psfxl") -> None:
        self.preset, self.threads, self.timeout_s, self.output_format = preset, threads, timeout_s, output_format
        self.resources = Resources(threads=threads)

    def fingerprint(self, netlist: Netlist, ctx: StageContext) -> str | None:
        return None

    def argv(self) -> list[str]:
        return [
            "spectre", "-64", "input.scs", "+escchars", f"+preset={self.preset}", f"+mt={self.threads}",
            "+lqtimeout", "900", "-maxw", "5", "-maxn", "5", "-env", "ade", "+logstatus",
            "-format", self.output_format, "-raw", "../psf", "+log", "../psf/spectre.out",
        ]

    def run(self, netlist: Netlist, ctx: StageContext) -> RawSim:
        local = ctx.workdir / "netlist"
        local.mkdir(parents=True, exist_ok=True)
        (local / "input.scs").write_text(netlist.text, encoding="utf-8", newline="\n")      # for the Linux host, from any controller
        ctx.executor.put(local, f"{ctx.remote_dir}/netlist")
        command = " ".join(shlex.quote(a) for a in self.argv())
        for attempt in (1, 2):                    # one retry on the transient "can't create server socket" failure (legacy rule)
            result = ctx.executor.run(command, cwd=f"{ctx.remote_dir}/netlist", timeout_s=self.timeout_s, cshrc=ctx.cshrc)
            ctx.record(f"spectre#{attempt}", result)
            if result.ok or TRANSIENT_SOCKET_FAILURE not in (result.stdout + result.stderr):
                break
        (ctx.workdir / "spectre.stdout").write_text(result.stdout, encoding="utf-8")
        (ctx.workdir / "spectre.stderr").write_text(result.stderr, encoding="utf-8")
        if not result.ok:
            raise StageFailure(f"spectre exited {result.returncode}", _tail(result.stderr or result.stdout))
        return RawSim(psf_dir=f"{ctx.remote_dir}/psf", returncode=result.returncode)


class Ocean:
    unit = "testbench"
    name = "ocean"
    level = "child"
    resources = Resources()

    def __init__(self, *, timeout_s: int, waveforms: list[WaveformExport] = ()) -> None:
        self.timeout_s = timeout_s
        self.waveforms = list(waveforms)

    def fingerprint(self, raw: RawSim, ctx: StageContext) -> str | None:
        return None

    def run(self, raw: RawSim, ctx: StageContext) -> Scalars:
        metrics = ctx.spec.metrics_for(ctx.unit)
        waveforms = [w for w in self.waveforms if w.testbench in (None, ctx.unit)]
        local = ctx.workdir / "metrics"
        (local / "waveforms").mkdir(parents=True, exist_ok=True)
        script = ocean_kernel.replay_script(
            metrics, waveforms, psf_dir="psf", scalars_file="metrics/ocean_scalars.tsv", waveform_dir="metrics/waveforms"
        )
        (local / "probe.ocn").write_text(script, encoding="utf-8", newline="\n")
        ctx.executor.put(local, f"{ctx.remote_dir}/metrics")

        attempts = 0
        for attempts in range(1, OCEAN_ATTEMPTS + 1):
            result = ctx.executor.run(
                "ocean -nograph -replay metrics/probe.ocn -log metrics/ocean.log",
                cwd=ctx.remote_dir,
                timeout_s=self.timeout_s,
                cshrc=ctx.cshrc,
            )
            ctx.record(f"ocean#{attempts}", result)
            if result.ok:
                break
        (ctx.workdir / "ocean.stdout").write_text(result.stdout, encoding="utf-8")
        (ctx.workdir / "ocean.stderr").write_text(result.stderr, encoding="utf-8")
        ctx.executor.get(f"{ctx.remote_dir}/metrics", local)

        scalars_path = local / "ocean_scalars.tsv"
        if not scalars_path.exists():
            raise StageFailure(f"ocean produced no scalars after {attempts} attempt(s)", _tail(result.stderr or result.stdout))
        try:
            rows = ocean_kernel.parse_scalars(scalars_path)
        except ValueError as exc:
            raise StageFailure(f"ocean scalars unreadable: {exc}") from exc
        csvs = {w.name: local / "waveforms" / f"{w.name}.csv" for w in waveforms}
        return Scalars(rows, {name: (path if path.exists() else None) for name, path in csvs.items()}, attempts)


class Extract:
    unit = "testbench"
    name = "extract"
    level = "child"
    resources = Resources()

    def fingerprint(self, scalars: Scalars, ctx: StageContext) -> str | None:
        return None

    def run(self, scalars: Scalars, ctx: StageContext) -> ChildResult:
        metrics, issues = {}, []
        for metric in ctx.spec.metrics_for(ctx.unit):
            row = scalars.rows.get(metric.name)
            if row is None:
                issues.append(f"metric {metric.name} missing from OCEAN output")
            elif row.status != "pass" or row.value is None:
                issues.append(f"metric {metric.name} failed: {row.message or row.status}")
            else:
                metrics[metric.name] = row.value
        issues += [f"waveform {name} returned nil" for name, path in scalars.waveforms.items() if path is None]
        return ChildResult(
            unit=ctx.unit, corner=ctx.corner, metrics=metrics, issues=issues,
            status="ok" if not issues else "failed:extract",
        )


def _tail(text: str, lines: int = 8) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def spectre_pipeline(spec, deck: Deck, *, waveforms: list[WaveformExport] = ()) -> list:
    sim = spec.simulator
    return [
        Render(deck),
        Spectre(preset=sim.preset, threads=sim.threads_per_run, timeout_s=sim.timeout_s, output_format=sim.output_format),
        Ocean(timeout_s=sim.timeout_s, waveforms=waveforms),
        Extract(),
    ]
