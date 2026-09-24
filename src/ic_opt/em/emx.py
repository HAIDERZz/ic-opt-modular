"""EMX invocation: the command line (kernel moved from em-opt ``emx_runner.build_emx_argv``) and one run through an Executor.

The sNp column order EMX produces is the lexicographic order of the port
*names*; the names are therefore ``p01, p02, ...`` in the order the caller wants
the columns, each mapped to the device's semantic label and its reference.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from ic_opt.em.pcell.base import EmxPort
from ic_opt.em.touchstone import format_number, header_issues
from ic_opt.eval.stage import StageContext, StageFailure
from ic_opt.spec import EmGrid, EmSettings, EmSweep

if TYPE_CHECKING:
    from ic_opt.executor import Executor


def numbered_ports(labels: list[str], references: dict[str, str | None]) -> list[EmxPort]:
    """``p01=<label>:<reference>`` in column order — the names pin the sNp order (em-opt library convention)."""
    return [EmxPort(name=f"p{i:02d}", signal=label, reference=references.get(label)) for i, label in enumerate(labels, 1)]


def argv(em: EmSettings, *, gds_file: str, top_cell: str, s_file: str, log_file: str, ports: list[EmxPort]) -> list[str]:
    """em-opt's flag order, verbatim; ``--parallel`` / ``--max-memory`` / ``--simultaneous-frequencies`` come from the dedicated fields."""
    out = [em.binary, "--quasistatic" if em.mode == "quasistatic" else "--full-wave", "--format=touchstone",
           f"--s-impedance={format_number(em.s_impedance)}", f"--s-file={s_file}"]
    sweep = em.frequencies if isinstance(em.frequencies, EmSweep) else None
    if sweep is not None:
        out.append("--sweep")
        if sweep.step_hz is not None:
            out.append(f"--sweep-stepsize={format_number(sweep.step_hz)}")
        if sweep.num_steps is not None:
            out.append(f"--sweep-num-steps={sweep.num_steps}")
    grid = em.accuracy if isinstance(em.accuracy, EmGrid) else EmGrid()
    if grid.thickness_um is not None:
        out.append(f"--thickness={format_number(grid.thickness_um)}")
    if grid.max_splits is not None:
        out.append(f"--max-splits={grid.max_splits}")
    if em.via_separation_um is not None:
        out.append(f"--via-separation={format_number(em.via_separation_um)}")
    out.append("--include-command-line")           # the Touchstone header then carries "EMX was run", which validation relies on
    if em.verbose is not None:
        out.append(f"--verbose={em.verbose}")
    out += [f"--log-file={log_file}", f"--parallel={em.threads}"]
    if em.simultaneous_frequencies is not None:
        out.append(f"--simultaneous-frequencies={em.simultaneous_frequencies}")
    out.append(f"--max-memory={format_number(em.memory_gb)}G")
    if grid.edge_width_um is not None:
        out.append(f"--edge-width={format_number(grid.edge_width_um)}")
    if isinstance(em.accuracy, str):
        out.append(f"--accuracy={em.accuracy}")
    if em.three_d_metals:
        out.append(f"--3d={','.join(em.three_d_metals)}")
    if em.via_inductance:
        out.append(f"--via-inductance={','.join(em.via_inductance)}")
    if em.via_sidewalls:
        out.append(f"--via-sidewalls={','.join(em.via_sidewalls)}")
    out += [f"--mode={mode}" for mode in em.modes]
    out += list(em.extra_args)
    for port in ports:
        out += ["-p", port.argument()]
    if sweep is not None:
        positional = [format_number(sweep.start_hz), format_number(sweep.stop_hz)] if sweep.start_hz > 0 else [format_number(sweep.stop_hz)]
    else:
        positional = [format_number(f) for f in em.frequencies]
    return [*out, gds_file, top_cell, em.process_file, *positional]


_MACHINE_FIELDS = frozenset({"threads", "memory_gb", "timeout_s", "verbose", "binary", "process_file"})   # facts of the host


def physics_key(em: EmSettings, *, proc_sha256: str) -> dict:
    """What changes EMX's answer: the physics settings and the process file's content (``proc_sha256``, its sha256 on the
    executor host) -- not the file's path, the binary, threads, memory, timeout or verbosity, which belong to the machine.
    Unset (None) settings are left out, so an optional setting added later leaves existing generations alone."""
    return {**em.model_dump(mode="json", exclude=_MACHINE_FIELDS, exclude_none=True), "proc_sha256": proc_sha256}


def process_file_digest(em: EmSettings, executor: Executor) -> str:
    """sha256 of the process file on the executor host: identities and cache keys see its content, never its path."""
    result = executor.run(f"sha256sum {shlex.quote(em.process_file)}", timeout_s=120)
    digest = result.stdout.split()[:1]
    if not result.ok or not digest:
        raise StageFailure(f"emx process file unreadable on {executor.host}: {em.process_file}", result.stderr.strip())
    return digest[0]


def fingerprint(em: EmSettings, *, gds_sha256: str, ports: list[EmxPort], proc_sha256: str) -> str:
    """The EMX cache key: GDS, ports and ``physics_key`` -- the process file by content, as in the stage's identity."""
    payload = {"gds": gds_sha256, "ports": [p.argument() for p in ports], "em": physics_key(em, proc_sha256=proc_sha256)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def run(em: EmSettings, ctx: StageContext, *, device: str, gds_path: Path, top_cell: str, ports: list[EmxPort]) -> Path:
    """Upload the GDS, run EMX in ``<remote>/em/<device>/`` with relative file names, bring back the sNp and log; return the local sNp."""
    local = ctx.workdir / "em" / device
    remote = ctx.executor.scratch(f"{ctx.obs_id}/em/{device}")      # created on the host (scp cannot make directories)
    s_file = f"{device}.s{len(ports)}p"
    ctx.executor.put(gds_path, f"{remote}/{gds_path.name}")
    command = " ".join(shlex.quote(a) for a in argv(em, gds_file=gds_path.name, top_cell=top_cell, s_file=s_file, log_file="emx.log", ports=ports))
    (local / "emx.cmd").write_text(command + "\n", encoding="utf-8")
    result = ctx.executor.run(command, cwd=remote, timeout_s=em.timeout_s, cshrc=ctx.cshrc)
    ctx.record(f"emx:{device}", result)
    (local / "emx.stdout").write_text(result.stdout, encoding="utf-8")
    (local / "emx.stderr").write_text(result.stderr, encoding="utf-8")
    for name in (s_file, "emx.log"):
        if ctx.executor.exists(f"{remote}/{name}"):
            ctx.executor.get(f"{remote}/{name}", local / name)
    if not result.ok:
        raise StageFailure(f"emx exited {result.returncode} for {device}", _tail(result.stderr or result.stdout))
    issues = header_issues(local / s_file, expected_ports=len(ports), z0=em.s_impedance)
    if issues:
        raise StageFailure(f"emx output for {device} rejected", *issues)
    return local / s_file


def _tail(text: str, lines: int = 8) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])
