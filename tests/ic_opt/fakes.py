"""Test doubles shared by the ic_opt tests."""

from __future__ import annotations

import contextlib
import importlib.util
import os
import shlex
import signal
from pathlib import Path

import pytest

from ic_opt.executor import CommandResult, LocalExecutor, process_group
from ic_opt.site import EXTRACTION_THREADS, HostLimits
from ic_opt.spec import Spec
from ic_opt.store import RunStore

needs_turbo = pytest.mark.skipif(any(importlib.util.find_spec(name) is None for name in ("turbo", "torch")),
                                 reason='turbo strategy needs TuRBO and torch: uv pip install -e ".[turbo]" -e vendor/TuRBO')
posix_only = pytest.mark.skipif(os.name == "nt", reason="the local executor runs commands on Linux / macOS only")

# The fake host's site.yaml entry: room for every test pipeline's heaviest stage several times over (tests about the
# envelope itself build their own HostLimits). The fake host reports exactly this size to env.doctor's machine probe.
FAKE_HOST = HostLimits(max_threads=96, max_memory_gb=384)
HANG_S = 120            # how long a hanging fake tool sleeps: far past any deadline a test sets, never waited out


@contextlib.contextmanager
def forwarded_signals():
    """``process_group.forward_signals`` installed afresh, as an executor installs it, and the handlers it replaced put
    back afterwards: inside, a SIGINT to this process is what a Ctrl-C at the terminal does (POSIX)."""
    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGHUP)}
    forwarded, process_group._forwarded = process_group._forwarded, set()
    try:
        process_group.forward_signals()
        yield
    finally:
        for s, handler in saved.items():
            signal.signal(s, handler)
        process_group._forwarded = forwarded


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
        "simulator": {"parallel_jobs": 2, "threads_per_run": 2, "timeout_s": 60},
        "budget": {"max_simulations": 10},
    }
    base.update(overrides)
    return base


def make_spec(**overrides) -> Spec:
    return Spec.model_validate(minimal_spec(**overrides))


def host_for(spec: Spec, jobs: int) -> HostLimits:
    """A fake host entry that fits ``jobs`` of this spec's heaviest Spectre / EMX run at once (replays of recorded specs),
    a Spectre run with the metric extraction beside it (N-78)."""
    spectre = spec.simulator.threads_per_run + (EXTRACTION_THREADS if spec.testbenches else 0)
    threads = max([spectre] + ([spec.em.threads] if spec.em else []))
    return HostLimits(max_threads=jobs * threads, max_memory_gb=jobs * (spec.em.memory_gb if spec.em else 1.0))


# An EMX .proc in demo_6m's stack: only the conductor lines matter to the stack check (thicknesses as in
# ic_opt/em/pcell/profiles/demo_6m/rule.yaml; a real .proc carries sheet resistances and vias too).
DEMO_PROC = """assume microns
conductor 0.2 m1_rsh M1 bias 0
conductor 0.2 m2_rsh M2 bias 0
conductor 0.2 m3_rsh M3 bias 0
conductor 0.2 m4_rsh M4 bias 0
conductor 0.9 m5_rsh M5 bias 0
conductor 3.0 m6_rsh M6 bias 0
via M1 M2 { 0.1 => 0.12, 1e6 S/m } VIA1
"""


class FakeSpectreExecutor(LocalExecutor):
    """A LocalExecutor whose ``run`` fakes ``spectre`` and ``ocean``.

    ``metric_fn(params_text, testbench, corner) -> dict[str, float]`` decides the
    scalars each OCEAN run reports; the netlist text is parsed for ``NAME=value``
    parameters so tests can make metrics depend on the point. Set ``fail_spectre``
    or ``fail_ocean`` to a predicate on (testbench, corner) to inject failures.
    Waveform exports requested by the probe script are written as the script writes
    them (N-100: ``<name>.csv`` and ``<name>.meta.json``, a row each in
    ``ocean_timing.tsv``) unless ``nil_waveforms`` names them (OCEAN returned nil:
    no file, the timing row says ``nil``). ``machine`` is the host's
    size as ``nproc`` / ``/proc/meminfo`` report it (None: the probe fails).
    ``tools`` names the tools on the host's PATH (None: every one): ``which``
    finds only those, and running one that is missing answers 127.
    ``hang(tool, cwd) -> bool`` makes a spectre / ocean / emx run hang: the fake
    host then runs a real ``sleep`` in its place, under ``LocalExecutor`` in a
    process group of its own, so only the command's deadline or an interrupt
    ends it; ``hung`` lists those runs as (tool, cwd).
    ``oppoints_fn(params_text, testbench, corner) -> {instance: {quantity: value}} | None`` gives the operating points
    an OCEAN run writes to ``metrics/oppoints.tsv`` when its script asks for them (T17.5), escaped as the script's SKILL
    escapes them; like Spectre, the fake has them only when the netlist it ran holds the info statement the script
    selects. ``oppoint_results`` lists the result each such script selected. Without ``oppoints_fn`` no file is written.
    """

    def __init__(self, scratch_root: Path, metric_fn=None, *, fail_spectre=None, fail_ocean=None, nil_waveforms=(),
                 snp_fn=None, fail_emx=None, machine=(FAKE_HOST.max_threads, FAKE_HOST.max_memory_gb), tools=None,
                 hang=None, oppoints_fn=None) -> None:
        super().__init__(scratch_root)
        self.machine = machine
        self.tools = None if tools is None else set(tools)
        self.hang = hang or (lambda tool, cwd: False)
        self.hung: list[tuple[str, str]] = []
        self.metric_fn = metric_fn or (lambda p, tb, c: {})
        self.fail_spectre = fail_spectre or (lambda tb, corner: False)
        self.fail_ocean = fail_ocean or (lambda tb, corner: False)
        self.nil_waveforms = set(nil_waveforms)
        self.snp_fn = snp_fn or synthetic_snp          # (argv, n_ports, z0) -> touchstone text
        self.fail_emx = fail_emx or (lambda device: False)
        self.commands: list[str] = []
        self.emx_runs = 0
        self.oppoints_fn = oppoints_fn
        self.oppoint_results: list[str] = []

    def run(self, command, *, cwd=None, timeout_s=None, cshrc=None) -> CommandResult:
        self.commands.append(command)
        argv = shlex.split(command)
        if argv[0] == "which":                      # doctor: the fake host has the Cadence tools (``tools``: only those)
            found = [tool for tool in argv[1:] if self._installed(tool)]
            return CommandResult(0 if len(found) == len(argv) - 1 else 1, "".join(f"/cad/bin/{tool}\n" for tool in found), "", argv, 0.01)
        if argv[0] in ("spectre", "ocean", "emx", "lmstat") and not self._installed(argv[0]):
            return CommandResult(127, "", f"{argv[0]}: Command not found.\n", argv, 0.01)
        if argv[0] in ("spectre", "ocean", "emx") and cwd is not None and self.hang(argv[0], Path(cwd)):   # a run, not a probe
            self.hung.append((argv[0], cwd))
            return super().run(f"sleep {HANG_S}", cwd=cwd, timeout_s=timeout_s)      # a real process: a deadline or an interrupt ends it
        if argv[-1] == "nproc" or argv == ["cat", "/proc/meminfo"]:    # doctor's machine probe: the fake host's size
            if self.machine is None:
                return CommandResult(127, "", f"{argv[-1]}: not found", argv, 0.01)
            cores, memory_gb = self.machine
            out = f"{cores}\n" if argv[-1] == "nproc" else f"MemTotal:       {int(memory_gb * 1024**2)} kB\nMemFree:  1 kB\n"
            return CommandResult(0, out, "", argv, 0.01)
        if argv[0] == "lmstat":
            return CommandResult(0, "Users of spectre:  (Total of 10 licenses issued;  Total of 2 licenses in use)\n", "", argv, 0.01)
        if argv[:2] == ["spectre", "-V"]:
            return CommandResult(0, "spectre version 23.1.0.242.isr4 64bit\n", "", argv, 0.01)
        if argv[0] == "sha256sum":                   # the emx stage hashes the process file on the host
            return CommandResult(0, f"{'ab' * 32}  {argv[1]}\n", "", argv, 0.01)
        if argv[0] == "cat" and argv[1].endswith(".proc"):    # the doctor reads the EMX process file: the fake host serves demo_6m's stack
            return CommandResult(0, DEMO_PROC, "", argv, 0.01)
        if argv[0] == "emx":
            work = Path(cwd)
            device = work.name
            self.emx_runs += 1
            if self.fail_emx(device):
                return CommandResult(3, "", "emx: license unavailable", argv, 0.01)
            s_file = next(a for a in argv if a.startswith("--s-file=")).split("=", 1)[1]
            z0 = float(next(a for a in argv if a.startswith("--s-impedance=")).split("=", 1)[1])
            n_ports = sum(a == "-p" for a in argv)
            (work / s_file).write_text(_call(self.snp_fn, argv, n_ports, z0, cwd=str(work)))
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
            for name, value in _call(self.metric_fn, params, tb, corner, cwd=str(work)).items():
                rows.append(f"{name}\t{value!r}\tx\tpass\t" if value is not None else f"{name}\t\tx\tfail\tnon_scalar")
            (work / "metrics" / "ocean_scalars.tsv").write_text("\n".join(rows) + "\n")
            script = (work / "metrics" / "probe.ocn").read_text().splitlines()
            timing = []
            for line in script:
                if line.startswith("; waveform export: "):
                    name = line.split(": ", 1)[1]
                    timing.append(f"waveform:{name}\t0.001\t{'nil' if name in self.nil_waveforms else 'written'}\n")
                    if name not in self.nil_waveforms:
                        write_waveform(work / "metrics" / "waveforms", name)
            if timing:
                (work / "metrics" / "ocean_timing.tsv").write_text("".join(timing))
            self._oppoints(work, script, params, tb, corner)
        return CommandResult(0, "", "", argv, 0.01)

    def _oppoints(self, work: Path, script: list[str], params: dict[str, str], tb: str, corner: str | None) -> None:
        part = [line for line in script[script.index("close(out)"):] if 'when(!icoptOpResult && (equal(name "' in line]
        if self.oppoints_fn is None or not part:
            return
        result = part[0].split('equal(name "', 1)[1].split('"', 1)[0]      # the statement's name the script looks up
        self.oppoint_results.append(result)
        table = _call(self.oppoints_fn, params, tb, corner, cwd=str(work))
        netlist = (work / "netlist" / "input.scs").read_text()
        text = ""                                       # the script's file when the results hold no operating points
        if table and f"\n{result} info what=oppoint where=rawfile" in "\n" + netlist:
            rows = [f"{_skill_escape(i)}\t{q}\t{v!r}" for i, quantities in table.items() for q, v in quantities.items()]
            text = "".join(f"{r}\n" for r in ["instance\tquantity\tvalue", *rows])
        (work / "metrics" / "oppoints.tsv").write_text(text, newline="")

    def _installed(self, tool: str) -> bool:
        return self.tools is None or tool in self.tools


def age_store(project: Path, spec: Spec, executor) -> None:
    """Leave a store as a version before T15.2 would have: the spec's legacy fingerprint, the em_only pipeline's legacy
    fingerprint (EM specs) and the EMX cache under legacy keys. The legacy formulas are pinned against that code's own
    values in test_engine and test_em_engine."""
    import json

    from ic_opt import migrate_store
    from ic_opt.em import emx
    from ic_opt.eval.stage import pipeline_fingerprint
    from ic_opt.stages.em_chain import Emx, em_only_pipeline
    from ic_opt.store import RunStore

    stamps = {"spec_fingerprint": spec._legacy_fingerprint()}
    stages = em_only_pipeline(spec) if spec.devices else []
    if stages:
        pipeline_fingerprint(stages, executor)                  # resolves the process file digest
        stamps["pipeline_fingerprint"] = migrate_store.legacy_pipeline_fingerprint(stages)
    store = RunStore(project)
    rows = [o.model_copy(update=stamps) for o in store.observations()]
    store.observations_path.write_text("".join(o.model_dump_json() + "\n" for o in rows), encoding="utf-8")
    for stage in [s for s in stages if isinstance(s, Emx)]:
        for geometry in sorted((store.root / "sims").glob("*/em/geometry.json")):
            g = json.loads(geometry.read_text(encoding="utf-8"))[stage.device]
            key = {"gds_sha256": g["gds_sha256"], "proc_sha256": stage.proc_sha256,
                   "ports": emx.numbered_ports(g["snp_order"], {p["signal"]: p["reference"] for p in g["ports"]})}
            entry = store.root / "cache" / stage.name / emx.fingerprint(stage.em, **key)
            if entry.exists():
                entry.rename(entry.with_name(migrate_store.legacy_emx_cache_key(stage.em, **key)))


def _call(fn, *args, cwd: str):
    """Call a test hook with ``cwd`` only when it takes it (replay hooks do; the simple metric functions do not)."""
    import inspect

    positional = [p for p in inspect.signature(fn).parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return fn(*args, cwd) if len(positional) > len(args) else fn(*args)


def write_waveform(folder: Path, name: str, rows=((1e9, 1.0), (2e9, 1.5)), *, columns=("freq_Hz", "y"),
                   units=("Hz", "V"), expression: str = "fake") -> Path:
    """A waveform export as the replay script writes it (N-100, ``docs/waveform_export.md``): ``<name>.csv`` (header,
    then one row per point, ``%.17g``) and ``<name>.meta.json``; returns the meta file."""
    import json

    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.csv").write_text(",".join(columns) + "\n" + "".join(",".join(f"{v:.17g}" for v in r) + "\n" for r in rows))
    meta = {"name": name, "expression": expression, "result": None, "kind": "waveform", "file": f"{name}.csv",
            "columns": list(columns), "units": list(units), "points": len(rows), "separator": ",", "precision": "%.17g"}
    (folder / f"{name}.meta.json").write_text(json.dumps(meta) + "\n")
    return folder / f"{name}.meta.json"


def _skill_escape(name: str) -> str:
    """What the replay script's icoptOpEscape does to an instance name."""
    return name.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


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



def rlc_snp(argv: list[str], n_ports: int, z0: float, cwd: str) -> str:
    """A geometry-dependent 2-port for library tests: R + jωL between the ports, C/2 to ground at each.

    L, R and C follow the outer diameter, width and turns in the pcell's geometry manifest (next to the
    GDS in ``cwd``), so a small demo_6m sweep gives distinct points, resonances inside and above the sweep,
    and passive S-parameters on EMX's own frequency grid (0 .. stop in ``--sweep-stepsize`` steps).
    """
    import json

    import numpy as np

    if n_ports != 2:
        return synthetic_snp(argv, n_ports, z0)
    cfg = json.loads((Path(cwd) / "geometry_manifest.json").read_text())["geometry"]["config"]
    od, width, turns = float(cfg["outer_diameter_um"]), float(cfg["width_um"]), int(cfg.get("turns", 1))
    ind = 0.4e-9 * turns**2 * (od / 100) ** 1.3 * (5 / width) ** 0.15
    res = 0.3 + 0.02 * turns * od / width
    cap = 30e-15 * turns * (od / 100) ** 2
    step = float(next(a for a in argv if a.startswith("--sweep-stepsize=")).split("=", 1)[1])
    stop = float(argv[-1])
    lines = ["! Touchstone simulation data from EMX version 2024.1.0 (fake rlc)", "! EMX was run on fake as:", "! " + " ".join(argv[:3]), f"# Hz S RI R {z0:g}"]
    eye = np.eye(2)
    for f in np.arange(0.0, stop + step / 2, step):
        w = 2 * np.pi * f
        ys = 1 / (res + 1j * w * ind)
        yc = 1j * w * cap / 2
        y = np.array([[ys + yc, -ys], [-ys, ys + yc]])
        s = (eye - z0 * y) @ np.linalg.inv(eye + z0 * y)
        lines.append(f"{f:.0f} " + " ".join(f"{v.real:.12e} {v.imag:.12e}" for v in s.T.reshape(-1)))
    return "\n".join(lines) + "\n"


TAP_LOSS = 1.03          # coupled_snp: what a centre tap adds to its winding's series resistance (the Q a tap costs)


def coupled_snp(argv: list[str], n_ports: int, z0: float, cwd: str) -> str:
    """A geometry-dependent coupled pair for transformer library tests (xfm_bs): each winding an R + jωL branch -- the
    primary from P1 to N1, the secondary from N2 to P2, so the drives (P1, N1), (N2, P2) measure k > 0 -- coupled by k,
    C/2 to ground at each winding port, on EMX's own frequency grid (0 .. stop in ``--sweep-stepsize`` steps).

    L, R, C and k follow the diameters, widths and centre offset in the pcell's geometry manifest (next to the GDS in
    ``cwd``); the larger devices resonate below 40 GHz. A centre tap -- a ``CTP`` / ``CTS`` port among the EMX ports --
    splits its winding into two halves at the tap port (each 0.3 L, coupled to the other by 0.2 L: L in all) and adds
    ``TAP_LOSS`` to its resistance. A differential drive leaves a symmetric winding's midpoint at 0 V, so with the tap
    grounded L, k and the resonance stay as they were and Q drops by about that factor."""
    import json

    import numpy as np

    if n_ports < 4:
        return synthetic_snp(argv, n_ports, z0)
    cfg = json.loads((Path(cwd) / "geometry_manifest.json").read_text())["geometry"]["config"]
    names = sorted(argv[i + 1].split(":", 1)[0].split("=", 1) for i, a in enumerate(argv) if a == "-p")    # EMX sorts by port name
    labels = [label for _name, label in names]
    op, os_ = float(cfg["primary_outer_diameter_um"]), float(cfg["secondary_outer_diameter_um"])
    wp, ws, cs = float(cfg["primary_width_um"]), float(cfg["secondary_width_um"]), float(cfg["center_spacing_um"])
    offset = 4 * cs / (op + os_)
    k = 0.75 * np.exp(-1.5 * np.log(op / os_) ** 2) * (1 - 1.2 * offset**2) * (1 - 0.3 * (wp / op + ws / os_))
    windings = [("P1", "N1", "CTP", 0.35e-9 * (op / 100) ** 1.25 * (5 / wp) ** 0.12, 0.4 + 0.02 * op / wp, 80e-15 * (op / 100) ** 2),
                ("N2", "P2", "CTS", 0.35e-9 * (os_ / 100) ** 1.25 * (5 / ws) ** 0.12, 0.4 + 0.02 * os_ / ws, 80e-15 * (os_ / 100) ** 2)]
    mutual = k * np.sqrt(windings[0][3] * windings[1][3])
    branches = []                                   # (from, to, winding)
    for w, (plus, minus, tap, *_rest) in enumerate(windings):
        branches += [(plus, tap, w), (tap, minus, w)] if tap in labels else [(plus, minus, w)]
    count = [sum(b[2] == w for b in branches) for w in range(2)]
    nb = len(branches)
    ind, res = np.zeros((nb, nb)), np.zeros(nb)
    for i, (_a, _b, wi) in enumerate(branches):
        l_self, r_self = windings[wi][3], windings[wi][4]
        res[i] = r_self * (TAP_LOSS if count[wi] == 2 else 1.0) / count[wi]
        for j, (_c, _d, wj) in enumerate(branches):
            if wi != wj:
                ind[i, j] = mutual / (count[wi] * count[wj])
            elif count[wi] == 1:
                ind[i, j] = l_self
            else:
                ind[i, j] = 0.3 * l_self if i == j else 0.2 * l_self
    incidence = np.zeros((len(labels), nb))
    for j, (a, b, _w) in enumerate(branches):
        incidence[labels.index(a), j], incidence[labels.index(b), j] = 1.0, -1.0
    caps = np.array([windings[0][5] / 2 if p in ("P1", "N1") else windings[1][5] / 2 if p in ("P2", "N2") else 0.0 for p in labels])
    step = float(next(a for a in argv if a.startswith("--sweep-stepsize=")).split("=", 1)[1])
    stop = float(argv[-1])
    lines = ["! Touchstone simulation data from EMX version 2024.1.0 (fake coupled)", "! EMX was run on fake as:", "! " + " ".join(argv[:3]),
             f"# Hz S RI R {z0:g}"]
    eye = np.eye(len(labels))
    for f in np.arange(0.0, stop + step / 2, step):
        w = 2 * np.pi * f
        y = incidence @ np.linalg.inv(np.diag(res) + 1j * w * ind) @ incidence.T + np.diag(1j * w * caps)
        s = (eye - z0 * y) @ np.linalg.inv(eye + z0 * y)
        lines.append(f"{f:.0f} " + " ".join(f"{v.real:.12e} {v.imag:.12e}" for v in s.reshape(-1)))
    return "\n".join(lines) + "\n"


def restamp(store: RunStore, **stamps: str) -> None:
    """Rewrite every observation of ``store`` with these fingerprints, as an older version would have stamped them."""
    rows = [o.model_copy(update=stamps) for o in store.observations()]
    store.observations_path.write_text("".join(o.model_dump_json() + "\n" for o in rows), encoding="utf-8")
