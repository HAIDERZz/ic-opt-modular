"""CLI: fan a (problems x methods x seeds) grid out to ``icopt_bench.loop`` subprocesses, resumable, with a
progress file and a per-job timeout.

    python -m icopt_bench.sweep --problems syn_small_tight,syn_ackley10_c2 --methods random,sobol --seeds 0-19 \\
        --budget 200 --batch 10 --jobs 8 --out DIR [--timeout 3600] [--heldout --reason "..."]

Each job is one child process (``start_new_session=True``, so its whole process group can be killed on timeout),
its own log file, at most ``--jobs`` running at once. A job whose result file already exists without an ``error``
is skipped (resume). Held-out problems (``benchmarks/split.json``) are refused unless ``--heldout --reason`` is
given; their results land under ``DIR/heldout/`` instead of ``DIR``.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from icopt_bench import registry
from icopt_bench.loop import write_atomic

POLL_S = 0.5
_HELDOUT_LOG = Path(__file__).resolve().parent.parent / "HELDOUT_LOG.md"


def parse_seeds(text: str) -> list[int]:
    seeds: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-")
            seeds.extend(range(int(lo), int(hi) + 1))
        else:
            seeds.append(int(part))
    return seeds


def log_heldout(tool: str, problems: list[str], methods: list[str], reason: str) -> None:
    if not _HELDOUT_LOG.exists():
        _HELDOUT_LOG.write_text("| date (UTC) | tool | problems | methods | reason |\n"
                                 "| --- | --- | --- | --- | --- |\n", encoding="utf-8")
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    line = f"| {ts} | {tool} | {', '.join(problems)} | {', '.join(methods)} | {reason} |\n"
    with _HELDOUT_LOG.open("a", encoding="utf-8") as f:
        f.write(line)


def resolve_problems(names: list[str], *, allow_heldout: bool, reason: str | None, tool: str, methods: list[str]) -> list[str]:
    """Guard: refuse held-out problems unless ``allow_heldout``; log the look when it is allowed."""
    held = [n for n in names if registry.is_heldout(n)]
    if held and not allow_heldout:
        raise SystemExit(f"refusing held-out problems without --heldout --reason: {held}")
    if held:
        if not reason:
            raise SystemExit("--heldout needs --reason")
        log_heldout(tool, held, methods, reason)
    return names


class Job:
    def __init__(self, problem: str, method: str, seed: int, out_dir: Path, args: argparse.Namespace) -> None:
        self.problem, self.method, self.seed, self.out_dir = problem, method, seed, out_dir
        self.args = args
        self.name = f"{problem}__{method}__{seed}"
        self.result_path = out_dir / f"{self.name}.json"

    def done(self) -> bool:
        if not self.result_path.exists():
            return False
        try:
            data = json.loads(self.result_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        return not data.get("error")

    def command(self) -> list[str]:
        cmd = [sys.executable, "-m", "icopt_bench.loop", self.problem, self.method, str(self.seed),
               "--budget", str(self.args.budget), "--batch", str(self.args.batch), "--out", str(self.out_dir)]
        if self.args.n_init is not None:
            cmd += ["--n-init", str(self.args.n_init)]
        if self.args.cache:
            cmd += ["--cache", self.args.cache]
        return cmd


def build_jobs(args: argparse.Namespace, problems: list[str], methods: list[str], seeds: list[int], out_dir: Path) -> list[Job]:
    jobs = []
    for problem in problems:
        target = (out_dir / "heldout") if registry.is_heldout(problem) else out_dir
        for method in methods:
            for seed in seeds:
                jobs.append(Job(problem, method, seed, target, args))
    return jobs


def write_progress(path: Path, *, total: int, done: int, failed: int, running: int, started_at: str) -> None:
    payload = {"total": total, "done": done, "failed": failed, "running": running, "started_at": started_at,
               "updated_at": datetime.now(UTC).isoformat()}
    write_atomic(path, payload)


def _timeout_result(job: Job, timeout: int) -> dict:
    return {
        "problem": job.problem, "method": job.method, "seed": job.seed, "budget": job.args.budget, "batch": job.args.batch,
        "n_init": job.args.n_init, "dim": None, "grid_size": None, "family": None, "scenario": None, "points": [],
        "suggest_seconds": [], "evaluate_seconds": [], "wall_seconds": float(timeout), "error": "timeout", "versions": {},
    }


def run_sweep(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    (out_dir / "logs").mkdir(parents=True, exist_ok=True)
    (out_dir / "heldout").mkdir(parents=True, exist_ok=True)

    if args.problems:
        names = [p.strip() for p in args.problems.split(",")]
    else:
        names = registry.names(family=args.family)
    methods = [m.strip() for m in args.methods.split(",")]
    seeds = parse_seeds(args.seeds)
    names = resolve_problems(names, allow_heldout=args.heldout, reason=args.reason, tool="sweep", methods=methods)

    jobs = build_jobs(args, names, methods, seeds, out_dir)
    pending = [j for j in jobs if not j.done()]
    total, done_count, failed_count = len(jobs), len(jobs) - len(pending), 0
    started_at = datetime.now(UTC).isoformat()
    progress_path = out_dir / "progress.json"
    write_progress(progress_path, total=total, done=done_count, failed=failed_count, running=0, started_at=started_at)

    env = os.environ.copy()
    env.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")

    running: dict[int, tuple[Job, subprocess.Popen, float, object]] = {}   # pid -> (job, proc, start_time, log file handle)
    while pending or running:
        while pending and len(running) < args.jobs:
            job = pending.pop(0)
            log_path = out_dir / "logs" / f"{job.name}.log"
            log_file = log_path.open("w", encoding="utf-8")
            proc = subprocess.Popen(job.command(), stdout=log_file, stderr=subprocess.STDOUT, env=env, start_new_session=True)
            running[proc.pid] = (job, proc, time.monotonic(), log_file)

        time.sleep(POLL_S)
        for pid in list(running):
            job, proc, start, log_file = running[pid]
            elapsed = time.monotonic() - start
            code = proc.poll()
            if code is None and args.timeout and elapsed > args.timeout:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                code = 124
            if code is None:
                continue
            log_file.close()
            del running[pid]
            if code == 124 and not job.done():
                write_atomic(job.result_path, _timeout_result(job, args.timeout))
            if not job.done():
                failed_count += 1
            done_count += 1
            write_progress(progress_path, total=total, done=done_count, failed=failed_count, running=len(running),
                            started_at=started_at)

    write_atomic(out_dir / "DONE", {"finished_at": datetime.now(UTC).isoformat(), "total": total, "failed": failed_count})
    return 1 if failed_count else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fan a (problem, method, seed) grid out to icopt_bench.loop subprocesses.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--problems", help="comma-separated problem names")
    group.add_argument("--family", choices=["synthetic", "analoggym"])
    parser.add_argument("--methods", required=True, help="comma-separated strategy names")
    parser.add_argument("--seeds", required=True, help="e.g. 0-19 or 0,1,2")
    parser.add_argument("--budget", type=int, default=200)
    parser.add_argument("--batch", type=int, default=10)
    parser.add_argument("--n-init", type=int, default=None)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=int, default=None, help="seconds; a job past this is killed and recorded as 'timeout'")
    parser.add_argument("--cache", default=None)
    parser.add_argument("--heldout", action="store_true")
    parser.add_argument("--reason", default=None)
    args = parser.parse_args(argv)
    return run_sweep(args)


if __name__ == "__main__":
    raise SystemExit(main())
