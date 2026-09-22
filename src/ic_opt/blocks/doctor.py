"""env.doctor — is this spec runnable on this executor, inside the site envelope?"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from ic_opt import site as site_module
from ic_opt.executor import Executor, ExecutorError
from ic_opt.spec import Spec
from ic_opt.store import RunStore

_LMSTAT_RE = re.compile(
    r"^Users of\s+(\S+):\s+\(Total\s+(?:of\s+)?(\d+)\s+licenses?\s+issued;\s*(?:Total\s+(?:of\s+))?(\d+)\s+licenses?\s+in use\)"
)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def require_pass(self) -> DoctorReport:
        """Print the checks and raise on any failure — except in plan mode, where the preview must go on."""
        from ic_opt.recipe import PLAN_MODE

        plan = PLAN_MODE.get()
        print("\n".join(f"[{'plan' if plan else 'doctor'}] {line}" for line in str(self).splitlines()))
        failed = [c for c in self.checks if not c.ok]
        if failed and not plan:
            raise RuntimeError("doctor failed: " + "; ".join(f"{c.name}: {c.detail}" for c in failed))
        return self

    def __str__(self) -> str:
        return "\n".join(f"[{'ok' if c.ok else 'FAIL'}] {c.name}: {c.detail}" for c in self.checks)


def doctor(spec: Spec, executor: Executor, *, cshrc: str | None = None, store: RunStore | None = None,
           site: site_module.Site | None = None) -> DoctorReport:
    site = site or site_module.load()
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
        probe = executor.run("lmstat -a", cshrc=cshrc, timeout_s=300)
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

    threads = spec.simulator.parallel_jobs * spec.simulator.threads_per_run
    add(Check("envelope", threads <= site.max_threads,
              f"{spec.simulator.parallel_jobs} jobs × {spec.simulator.threads_per_run} threads = {threads} ≤ {site.max_threads}"))

    if spec.devices:
        for device in spec.devices:
            add(_device_check(device))
    if spec.em is not None:
        emx = executor.run("which emx", cshrc=cshrc, timeout_s=120)
        add(Check("emx", emx.ok, emx.stdout.strip() if emx.ok else "emx not on PATH"))
        add(Check("em:process_file", executor.exists(spec.em.process_file), spec.em.process_file))
        add(_stack_check(spec, executor, cshrc))
        slots = site.slots(spec.em.threads, spec.em.memory_gb)
        add(Check("em:envelope", spec.em.threads <= site.max_threads and spec.em.memory_gb <= site.max_memory_gb,
                  f"{spec.em.threads} threads / {spec.em.memory_gb:g} GB per EMX → {slots} concurrent within {site.max_threads} threads / {site.max_memory_gb:g} GB"))

    if store is not None:
        from ic_opt.eval.engine import simulations

        used = sum(simulations(o) for o in store.observations())
        add(Check("budget", used < spec.budget.max_simulations, f"{used}/{spec.budget.max_simulations} simulations used"))
        store.log_step("doctor", "ok" if report.ok else "fail", checks=[(c.name, c.ok) for c in report.checks])
    return report


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


def plan_line(spec: Spec, executor: Executor, site: site_module.Site | None = None) -> str:
    """One line for --plan: where and how hard this spec will hit the machine."""
    site = site or site_module.load()
    sim = spec.simulator
    return (f"host={executor.host} jobs={sim.parallel_jobs} threads/job={sim.threads_per_run} "
            f"peak_threads={sim.parallel_jobs * sim.threads_per_run} (site max {site.max_threads}) "
            f"budget={spec.budget.max_simulations} sims, preset={shlex.quote(sim.preset)}")
