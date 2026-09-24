"""env.doctor — is this spec runnable on this executor, inside the executor host's site.yaml entry?"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from ic_opt.executor import Executor, ExecutorError
from ic_opt.site import HostLimits
from ic_opt.spec import Spec
from ic_opt.store import RunStore

_LMSTAT_RE = re.compile(
    r"^Users of\s+(\S+):\s+\(Total\s+(?:of\s+)?(\d+)\s+licenses?\s+issued;\s*(?:Total\s+(?:of\s+))?(\d+)\s+licenses?\s+in use\)"
)
_MEMTOTAL_RE = re.compile(r"^MemTotal:\s+(\d+)\s+kB", re.MULTILINE)
_NPROC = "env -u OMP_NUM_THREADS -u OMP_THREAD_LIMIT nproc"     # GNU nproc reports OMP_NUM_THREADS when it is set
_TAGS = {"fail": "FAIL", "warn": "WARN", "note": "note"}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    level: str = "fail"          # what a result that is not ok means: "fail" blocks; "warn" and "note" only inform

    @property
    def blocking(self) -> bool:
        return not self.ok and self.level == "fail"

    @property
    def tag(self) -> str:
        return "ok" if self.ok else _TAGS[self.level]


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(c.blocking for c in self.checks)

    def require_pass(self) -> DoctorReport:
        """Print the checks and raise on any failure — except in plan mode, where the preview must go on."""
        from ic_opt.recipe import PLAN_MODE

        plan = PLAN_MODE.get()
        print("\n".join(f"[{'plan' if plan else 'doctor'}] {line}" for line in str(self).splitlines()))
        failed = [c for c in self.checks if c.blocking]
        if failed and not plan:
            raise RuntimeError("doctor failed: " + "; ".join(f"{c.name}: {c.detail}" for c in failed))
        return self

    def __str__(self) -> str:
        return "\n".join(f"[{c.tag}] {c.name}: {c.detail}" for c in self.checks)


def doctor(spec: Spec, executor: Executor, *, cshrc: str | None = None, store: RunStore | None = None,
           limits: HostLimits) -> DoctorReport:
    """``limits`` is the executor host's site.yaml entry (``run.limits``): the envelope checks compare against it,
    and the machine check compares it with what the host reports (advisory)."""
    report = DoctorReport()
    add = report.checks.append

    try:
        add(Check("executor", executor.run("true", timeout_s=60).ok, executor.host))
    except ExecutorError as exc:
        add(Check("executor", False, str(exc)))
        return report

    tools = executor.run("which spectre ocean", cshrc=cshrc, timeout_s=120)
    found = tools.stdout.strip().replace("\n", ", ")
    add(Check("tools", tools.ok, found if tools.ok else "spectre/ocean not on PATH" + (f" (found: {found})" if found else "")))

    if spec.simulator.license_check:      # the legacy rule: spectre -V answers and the license server lists features
        version = executor.run("spectre -V", cshrc=cshrc, timeout_s=300)
        probe = executor.run(limits.license_probe or "lmstat -a", cshrc=cshrc, timeout_s=300)   # lmstat-format output
        features = {m.group(1): (int(m.group(2)), int(m.group(3))) for line in probe.stdout.splitlines()
                    if (m := _LMSTAT_RE.match(line.strip()))}
        spectre = ", ".join(f"{k} {used}/{issued}" for k, (issued, used) in features.items() if k.lower().startswith("spectre"))
        head = (version.stdout or version.stderr).strip().splitlines()[:1]
        add(Check("license", version.ok and probe.ok and bool(features),
                  f"{head[0] if head else 'spectre -V failed'}; lmstat {len(features)} features" + (f" ({spectre})" if spectre else "")
                  if version.ok and probe.ok else (probe.stderr.strip() or version.stderr.strip() or "spectre -V / lmstat failed")))

    for tb in spec.testbenches:
        path = f"{tb.maestro_point_root}/netlist/input.scs"
        add(Check(f"export:{tb.id}", executor.exists(path), path))

    add(_envelope_check(spec, limits, executor.host))
    add(_machine_check(executor, limits))

    if spec.devices:
        for device in spec.devices:
            add(_device_check(device))
    if spec.em is not None:
        emx = executor.run("which emx", cshrc=cshrc, timeout_s=120)
        add(Check("emx", emx.ok, emx.stdout.strip() if emx.ok else "emx not on PATH"))
        add(Check("em:process_file", executor.exists(spec.em.process_file), spec.em.process_file))
        add(_stack_check(spec, executor, cshrc))
        slots = limits.slots(spec.em.threads, spec.em.memory_gb)
        add(Check("em:envelope", spec.em.threads <= limits.max_threads and spec.em.memory_gb <= limits.max_memory_gb,
                  f"{spec.em.threads} threads / {spec.em.memory_gb:g} GB per EMX → {slots} concurrent within {limits.max_threads} threads / {limits.max_memory_gb:g} GB"))

    if store is not None:
        from ic_opt.eval.engine import simulations

        used = sum(simulations(o) for o in store.observations())
        add(Check("budget", used < spec.budget.max_simulations, f"{used}/{spec.budget.max_simulations} simulations used"))
        store.log_step("doctor", "ok" if report.ok else "fail", checks=[(c.name, c.ok) for c in report.checks])
    return report


def _job(spec: Spec) -> tuple[int, float]:
    """One concurrent job of the spec's pipeline, sized as the engine sizes it: its heaviest Spectre / EMX run."""
    runs = [(spec.simulator.threads_per_run, 0.0)] if spec.testbenches else []
    if spec.devices and spec.em is not None:
        runs.append((spec.em.threads, spec.em.memory_gb))
    return max((t for t, _ in runs), default=1), max((m for _, m in runs), default=0.0)


def _envelope_check(spec: Spec, limits: HostLimits, host: str) -> Check:
    """What the spec asks for at once -- parallel_jobs of its heaviest job -- against the host's entry; beyond it fails."""
    jobs = spec.simulator.parallel_jobs
    threads, memory = _job(spec)
    return Check("envelope", jobs * threads <= limits.max_threads and jobs * memory <= limits.max_memory_gb,
                 f"{jobs} jobs × {threads} threads / {memory:g} GB per job → {jobs * threads} threads / {jobs * memory:g} GB "
                 f"of {limits.max_threads} / {limits.max_memory_gb:g} (max_threads / max_memory_gb of {host})")


def _machine_check(executor: Executor, limits: HostLimits) -> Check:
    """D2: the entry against what the host reports (``nproc``, ``MemTotal``). Advisory only: an entry may describe a
    share of a machine, and a probe cannot see every cgroup or queue policy, so the user's numbers stay the rule."""
    try:
        cores = executor.run(_NPROC, timeout_s=60)
        meminfo = executor.run("cat /proc/meminfo", timeout_s=60)
    except ExecutorError as exc:
        return Check("machine", False, f"not probed on {executor.host} ({exc}); limits taken as written", level="note")
    count = int(cores.stdout.strip()) if cores.ok and cores.stdout.strip().isdigit() else None
    match = _MEMTOTAL_RE.search(meminfo.stdout) if meminfo.ok else None
    if count is None or match is None:
        return Check("machine", False, f"nproc / MemTotal not readable on {executor.host}; limits taken as written", level="note")
    memory = int(match.group(1)) / 1024**2
    over = [f"max_threads {limits.max_threads} > {count} cores (nproc)"] if limits.max_threads > count else []
    over += [f"max_memory_gb {limits.max_memory_gb:g} > {memory:.1f} GB (MemTotal)"] if limits.max_memory_gb > memory else []
    if over:
        return Check("machine", False, f"{executor.host}: {'; '.join(over)} -- the entry allows more than the machine has",
                     level="warn")
    return Check("machine", True, f"{executor.host}: {count} cores / {memory:.1f} GB; the entry fits")


def _stack_check(spec: Spec, executor: Executor, cshrc: str | None) -> Check:
    """Every device profile's conductor thicknesses agree with the EMX .proc on the executor host (M2.3)."""
    from ic_opt.em.pcell.proc_file import conductor_thicknesses, stack_mismatches
    from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter

    text = executor.run(f"cat {shlex.quote(spec.em.process_file)}", cshrc=cshrc, timeout_s=120)
    if not text.ok:
        return Check("em:stack", False, f"cannot read {spec.em.process_file} on {executor.host}")
    proc = conductor_thicknesses(text.stdout)
    problems = []
    for profile in sorted({d.profile for d in spec.devices}):
        try:
            stack = get_geometry_rule_adapter(profile).stack_summary()["conductors"]
        except (ValueError, OSError) as exc:
            problems.append(f"{profile}: {str(exc).splitlines()[0]}")
            continue
        problems += [f"{profile} {m}" for m in stack_mismatches(stack, proc)]
    return Check("em:stack", not problems, "; ".join(problems) if problems else f"{len(proc)} .proc conductors agree with every device profile")


def _device_check(device) -> Check:
    """The generator resolves and the process profile loads on the controller, where pcell runs."""
    from ic_opt.em.pcell import get_generator
    from ic_opt.em.pcell.process_rules import get_process_rule_profile

    try:
        generator = get_generator(device.generator, plugin_module=device.plugin)
        get_process_rule_profile(device.profile)
    except (ValueError, OSError, ImportError) as exc:
        return Check(f"device:{device.id}", False, str(exc).splitlines()[0])
    return Check(f"device:{device.id}", True, f"{generator.generator_id} on {device.profile}")


def plan_line(spec: Spec, executor: Executor, limits: HostLimits) -> str:
    """One line for --plan: where and how hard this spec will hit the machine, against that host's entry."""
    sim = spec.simulator
    return (f"host={executor.host} jobs={sim.parallel_jobs} threads/job={sim.threads_per_run} "
            f"peak_threads={sim.parallel_jobs * sim.threads_per_run} (max_threads {limits.max_threads}) "
            f"budget={spec.budget.max_simulations} sims, preset={shlex.quote(sim.preset)}")
