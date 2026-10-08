"""The four child-level stages of the Spectre/OCEAN pipeline.

    Point --render--> Netlist --spectre--> RawSim --ocean--> Scalars --extract--> ChildResult

Working directory layout (identical on the local and the remote side):

    <dir>/netlist/input.scs   <dir>/psf/   <dir>/metrics/{probe.ocn, ocean.log, ocean_scalars.tsv, oppoints.tsv,
                                                       ocean_timing.tsv, waveforms/}

Waveforms (N-100, ``docs/waveform_export.md``): the OCEAN stage writes each requested waveform as a real CSV from its
vectors at ``%.17g``, with a ``<name>.meta.json`` (a family: one CSV per member and ``<name>.families.json``); the
extract stage reads every file back against its meta file. The export and the operating-point read are timed inside
OCEAN: a line each in ``metrics/ocean.log``, a row each in ``metrics/ocean_timing.tsv``, and a trace record each
(``ocean:waveform:<name>``, ``ocean:oppoints``) beside the OCEAN run's own (``ocean#<attempt>``), so the time of an
export is told apart from the metrics'.

Operating points (T17.5, ``simulator.operating_points``): the render stage adds what the netlist lacks for Spectre to
write them (``sim.netlist.with_operating_points``), the OCEAN stage reads them after the metrics, the extract stage
keeps them in the child's result. None of it can fail a child or change a metric OCEAN computes.

A saturation-margin metric (T17.11, ``Metric.saturation_margin``) is the one metric read from them: the extract stage
computes it, and it fails as an OCEAN expression does when the table lacks a transistor it names (:class:`Extract`).
The cache: a spec that gains such a metric is another problem (``Spec.fingerprint``: the metric is in it), so no
observation recorded without it is reused for it; a spec without such a metric extracts what it did before.

Identity (N-99). ``Render.identity`` is the deck's fingerprint (``Deck.fingerprint``: the templates and every support
file's content) and ``Extract.identity`` the waveform exports requested (name, expression and testbench, in order) with
the operating-point setting: the pipeline fingerprint changes when the netlist, a support file or the requested exports
change, and the engine reuses no observation across that change (``eval.engine``, "Identity").
"""

from __future__ import annotations

import json
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

from ic_opt.deck import Deck
from ic_opt.eval.stage import Resources, StageContext, StageFailure
from ic_opt.localpath import literal
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

    @property
    def identity(self) -> str:
        """The deck's fingerprint (N-99): the netlist and its support files, as Maestro exported them."""
        return self.deck.fingerprint()

    def fingerprint(self, point: Point, ctx: StageContext) -> str | None:
        return None

    def run(self, point: Point, ctx: StageContext) -> Netlist:
        return render_netlist(self.deck, point, ctx)


def render_netlist(deck: Deck, point: Point, ctx: StageContext) -> Netlist:
    """The child's netlist: the deck template for (testbench, corner) with the circuit variables filled in and, unless the
    spec turns them off, the statements for the operating points added; support files copied alongside."""
    try:
        template = deck.template(ctx.unit, ctx.corner)
    except KeyError as exc:
        raise StageFailure(f"deck has no template for {ctx.unit}/{ctx.corner}") from exc
    bundle = deck.bundle(ctx.unit)
    if bundle is None and ctx.unit in deck.digests:
        raise StageFailure(f"the deck's support files of {ctx.unit} are not there (a preview's deck snapshot cannot run)")
    if bundle is not None:   # Maestro's support files (.modelFiles, .designVariables, ...) travel with the deck
        shutil.copytree(literal(bundle), literal(ctx.workdir / "netlist"), dirs_exist_ok=True)     # names may end in a dot
    circuit = {name: point.params[name] for name in ctx.spec.circuit_variables}
    text = netlist_kernel.render(template, circuit)
    if ctx.spec.simulator.operating_points:
        text, _ = netlist_kernel.with_operating_points(text)
    return Netlist(text)


class Spectre:
    unit = "testbench"
    name = "spectre"
    level = "child"

    def __init__(self, *, preset: str, threads: int, timeout_s: int, output_format: str = "psfxl",
                 license_queue_timeout_s: int | None = None) -> None:
        self.preset, self.threads, self.timeout_s, self.output_format = preset, threads, timeout_s, output_format
        self.license_queue_timeout_s = license_queue_timeout_s        # None: no +lqtimeout, Spectre's own license queue wait
        self.resources = Resources(threads=threads)

    def fingerprint(self, netlist: Netlist, ctx: StageContext) -> str | None:
        return None

    def argv(self) -> list[str]:
        queue = [] if self.license_queue_timeout_s is None else ["+lqtimeout", str(self.license_queue_timeout_s)]
        return [
            "spectre", "-64", "input.scs", "+escchars", f"+preset={self.preset}", f"+mt={self.threads}",
            *queue, "-maxw", "5", "-maxn", "5", "-env", "ade", "+logstatus",
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
        oppoints = None       # the netlist as it ran names the result (render_netlist added the statement where it lacked one)
        if ctx.spec.simulator.operating_points:
            oppoints = netlist_kernel.operating_points((ctx.workdir / "netlist" / "input.scs").read_text(encoding="utf-8")).result
        script = ocean_kernel.replay_script(
            metrics, waveforms, psf_dir="psf", scalars_file="metrics/ocean_scalars.tsv", waveform_dir="metrics/waveforms",
            oppoint_result=oppoints, oppoints_file="metrics/oppoints.tsv",
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
        timing = ocean_kernel.parse_timing(local / ocean_kernel.TIMING_FILE)
        for row in timing:
            ctx.trace.append({"label": f"ocean:{row.part}", "seconds": round(row.seconds, 3), "outcome": row.outcome})
        metas = {w.name: local / "waveforms" / f"{w.name}.meta.json" for w in waveforms}
        return Scalars(rows, {name: (path if path.exists() else None) for name, path in metas.items()}, attempts,
                       oppoints=local / "oppoints.tsv" if oppoints else None, outcomes={r.part: r.outcome for r in timing})


class Extract:
    unit = "testbench"
    name = "extract"
    level = "child"
    resources = Resources()

    def __init__(self, *, waveforms: list[WaveformExport] = (), operating_points: bool = True) -> None:
        self.waveforms = list(waveforms)                 # what the run asked OCEAN to export: part of the identity only
        self.operating_points = operating_points        # simulator.operating_points: whether a child keeps them

    @property
    def identity(self) -> str:
        """The waveform exports requested -- name, expression and testbench, in order -- and the operating-point setting
        (N-99): an observation that lacks an export, or the operating points, is not reused for a run that asks for it."""
        return json.dumps({"waveforms": [[w.name, w.expression, w.testbench] for w in self.waveforms],
                           "operating_points": self.operating_points}, separators=(",", ":"))

    def fingerprint(self, scalars: Scalars, ctx: StageContext) -> str | None:
        return None

    def run(self, scalars: Scalars, ctx: StageContext) -> ChildResult:
        """Every metric OCEAN gave a scalar for is kept. One that came back nil, non-scalar or not at all, or a requested
        waveform that came back nil, makes the child ``metric_failed`` with that as its issue -- the simulation ran and the
        other metrics stand (N-31, 2026-09-27: a wrong expression used to fail the child and lose every metric).
        A waveform is read back from its files (``sim.ocean.read_waveform``): one that was not written says why
        (``waveform Vout returned nil``, ``waveform Vout not written: not_a_waveform:flonum``) and one whose files do not
        match their meta file says that (``waveform Vout unreadable: ...``), each an issue as a nil does.
        Operating points are kept when OCEAN wrote them; none, or a file that does not parse, is ``None``, never an issue
        by itself.

        A saturation-margin metric of this testbench (T17.11) is computed here from those operating points
        (``sim.ocean.saturation_margin``). An instance it names that the table lacks -- no row, no ``vds`` or ``vdsat``,
        no table at all -- leaves it out and makes the child ``metric_failed``, as an expression that returned nil does,
        with one issue naming every such instance: ``metric SAT_MARGIN failed: no operating point for M7``, the form the
        per-batch line of ``sim.evaluate`` and the digest read for an expression's failure."""
        metrics, issues = {}, []
        for metric in ctx.spec.metrics_for(ctx.unit):
            row = scalars.rows.get(metric.name)
            if row is None:
                issues.append(f"metric {metric.name} missing from OCEAN output")
            elif row.status != "pass" or row.value is None:
                issues.append(f"metric {metric.name} failed: {row.message or row.status}")
            else:
                metrics[metric.name] = row.value
        operating_points = None
        if scalars.oppoints is not None:
            try:
                operating_points = ocean_kernel.parse_oppoints(scalars.oppoints)
            except ValueError:
                pass
        for metric in ctx.spec.metrics:
            if metric.saturation_margin is None or metric.testbench != ctx.unit:
                continue
            value, missing = ocean_kernel.saturation_margin(operating_points, metric.saturation_margin.instances)
            if missing:
                issues.append(f"metric {metric.name} failed: no operating point for {', '.join(missing)}")
            else:
                metrics[metric.name] = value
        issues += [_waveform_issue(name, path, scalars.outcomes.get(f"waveform:{name}"))
                   for name, path in scalars.waveforms.items()]
        issues = [issue for issue in issues if issue]
        return ChildResult(
            unit=ctx.unit, corner=ctx.corner, metrics=metrics, issues=issues,
            status="ok" if not issues else "metric_failed", operating_points=operating_points,
        )


def _waveform_issue(name: str, meta: Path | None, outcome: str | None) -> str | None:
    if meta is None:
        return f"waveform {name} returned nil" if outcome in (None, "nil") else f"waveform {name} not written: {outcome}"
    try:
        ocean_kernel.read_waveform(meta)
    except ValueError as exc:
        return f"waveform {name} unreadable: {exc}"
    return None


def _tail(text: str, lines: int = 8) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def spectre_pipeline(spec, deck: Deck, *, waveforms: list[WaveformExport] = ()) -> list:
    sim = spec.simulator
    return [
        Render(deck),
        Spectre(preset=sim.preset, threads=sim.threads_per_run, timeout_s=sim.timeout_s, output_format=sim.output_format,
                license_queue_timeout_s=sim.license_queue_timeout_s),
        Ocean(timeout_s=sim.timeout_s, waveforms=waveforms),
        Extract(waveforms=waveforms, operating_points=sim.operating_points),
    ]
