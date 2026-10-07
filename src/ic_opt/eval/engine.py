"""The evaluation engine: points in, observations out. Knows nothing about Spectre or EMX.

For every point it (1) reuses an existing ``ok`` observation of the same
problem / pipeline / point / children, (2) checks the simulation budget, (3) runs
the point-level stages once (a stage with a fingerprint is served from
``.icopt/cache/<stage>/<fingerprint>/`` when it has run before), (4) runs the
child-level chains — the testbench chain for every testbench × corner, the
device chain for every device — (5) aggregates the children under the corner
policy, (6) appends one Observation, (7) applies the retention policy to raw
simulation directories. Points run in parallel, capped by the executor host's
site.yaml entry for the heaviest stage, a testbench one counted with one core
more than its Spectre threads (N-78); a stage or job bigger than the whole
entry is refused before anything starts. A point's children run serially, like the
legacy flow. A stage that fails -- a ``StageFailure``, or a command past its
deadline (``CommandTimeout``) -- fails its child, or every child of the point
for a point-level stage, as ``failed:<stage>``; the other points run on.

Schedule (T17.8). Given a ``Schedule`` (``ic_opt.eval.schedule``; ``sim.evaluate``
builds one per batch unless the run turns it off), a point's children run in
its order and the point stops after the first child whose result shows it
cannot be feasible: the children after it are not run, the observation holds
those that ran (``simulations`` counts them) and names the others in
``not_run``, and ``aggregate`` judges the incomplete set. The children keep the
engine's own order in the observation whatever order they ran in, so a point
that is not stopped is recorded as without a schedule. The budget check still
reserves every child of a point. The step log counts the points stopped early
and the children not run. Without a schedule: the engine's order, no stop.
The device children run first (N-63); one that shows the point cannot be
feasible stops it before its first testbench child, while the other device
children still run: a measurement costs no simulation (the EMX run is the
point's), and their metrics are kept. A point whose children are all devices
is never cut short.

Interrupts. Ctrl-C reaches the running commands through the executors' signal
forwarding (``process_group``), which counts it before anything else; the
KeyboardInterrupt comes to the thread waiting for the points. From then on no
job starts a further stage, child or command (``Interrupted``): the points still
queued never start, a running point whose command got the interrupt is not
recorded -- its failure is not its own, and it is simulated again next time --
while one that had finished is. The step log says which were recorded,
interrupted and never started, and the KeyboardInterrupt goes on.

Identity. The problem is ``Spec.fingerprint()``: the spec without how it is run
(resources, timeouts, retention, license check, budget), so a project moved to
a smaller machine or given a bigger budget keeps reusing its observations. An
observation stamped with the spec's pre-T15.2 fingerprint
(``Spec._legacy_fingerprint()``, which hashed those too) counts as the same
problem until ``ic-opt migrate-store`` restamps it; new observations carry the
new one. The pipeline fingerprint is formed once per run, after the stages
whose identity lives on the simulation host resolved it (EMX hashes its process
file there), so every point of a run carries the same value.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ic_opt.eval.stage import Stage, StageContext, StageFailure, pipeline_fingerprint
from ic_opt.executor import CommandTimeout, Executor, process_group
from ic_opt.observation import ChildResult, Observation, Observations
from ic_opt.sim.corner import aggregate
from ic_opt.site import EXTRACTION_NOTE, EXTRACTION_THREADS, EnvelopeError, HostLimits
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.store import RunStore, utc_now

if TYPE_CHECKING:
    from ic_opt.eval.schedule import Schedule


class BudgetExceeded(RuntimeError):
    pass


class Interrupted(KeyboardInterrupt):
    """A job stopped because its run was interrupted (see "Interrupts" above): it starts no further stage or child and
    records nothing. A KeyboardInterrupt, so nothing on the way takes it for an ordinary failure."""


@dataclass
class Job:
    obs_id: str
    point: Point
    observation: Observation | None = None
    reused: bool = False
    seconds: float = 0.0
    started: bool = False                 # a worker took it up
    recorded: bool = False                # its observation is in the store
    stopped_after: str | None = None      # why the schedule stopped the point early (Schedule.stop_after)


@dataclass(frozen=True)
class Child:
    unit_kind: str            # "testbench" | "device"
    unit: str
    corner: str | None

    @property
    def key(self) -> str:
        return f"{self.unit}/{self.corner or 'nominal'}"


def children_of(spec: Spec, pipeline: list[Stage], corner_ids: list[str | None]) -> list[Child]:
    """The children a pipeline produces for one point: testbench × corner for the testbench chain, one per device for the device chain."""
    kinds = {getattr(s, "unit", "testbench") for s in pipeline if s.level == "child"}
    children: list[Child] = []
    if "testbench" in kinds:
        children += [Child("testbench", tb, c) for tb in spec.testbench_ids for c in corner_ids]
    if "device" in kinds:
        children += [Child("device", d, None) for d in spec.device_ids]
    return children


def point_runs(pipeline: list[Stage]) -> int:
    """Simulations a point-level stage costs when it runs (``runs`` attribute; EMX declares 1)."""
    return sum(getattr(s, "runs", 0) for s in pipeline if s.level == "point")


def child_simulates(pipeline: list[Stage]) -> bool:
    """Whether a pipeline's children are simulations (a child stage declares ``simulates = False`` when it is not, e.g. a prediction)."""
    return any(getattr(s, "simulates", True) for s in pipeline if s.level == "child")


def counted_children(pipeline: list[Stage], children: list[Child]) -> list[Child]:
    """The children that are simulations, what the budget counts: those whose chain -- the pipeline's child stages of
    their unit kind -- has a stage that simulates (a child stage declares ``simulates = False`` when it is not: a
    prediction, a library row's measurement, T18.2B). Every child of a pipeline whose child stages all simulate, as
    :func:`child_simulates` counted them before a pipeline mixed both."""
    kinds = {getattr(s, "unit", "testbench") for s in pipeline if s.level == "child" and getattr(s, "simulates", True)}
    return [c for c in children if c.unit_kind in kinds]


def simulations(observation: Observation) -> int:
    """What an observation cost: as recorded, else (older records) its children plus every point-level stage that ran instead of hitting the cache."""
    if observation.simulations is not None:
        return observation.simulations
    return len(observation.children) + sum(1 for v in observation.cache.values() if v == "miss")


def extraction_threads(stage: Stage) -> int:
    """Threads a job running ``stage`` takes beyond the stage's own: ``site.EXTRACTION_THREADS`` for a testbench child that
    simulates -- measured (N-78): a one-thread Spectre runs at up to two cores at times and the OCEAN extraction takes
    up to two for a moment after each simulation; within a job they run one after the other -- and none for any other
    stage (an EMX run counts ``em.threads``, as before)."""
    testbench = stage.level == "child" and getattr(stage, "unit", "testbench") == "testbench"
    return EXTRACTION_THREADS if testbench and getattr(stage, "simulates", True) else 0


def workers_for(spec: Spec, pipeline: list[Stage], parallel_jobs: int | None, limits: HostLimits) -> int:
    """Concurrent points: the requested parallelism, capped by what the executor host allows for the heaviest job -- its
    heaviest stage's threads, a testbench stage's with :func:`extraction_threads` more (per job ``threads_per_run + 1``
    for the Spectre chain), and its largest stage's memory.

    A stage that alone needs more threads or memory than the host's entry is refused here -- also under
    ``--plan`` -- instead of running one at a time past the limit the user wrote down (audit row 2); so is a testbench
    job whose simulator fits the entry alone but not with its extra core."""
    for stage in pipeline:
        need = stage.resources
        if need.threads > limits.max_threads or need.memory_gb > limits.max_memory_gb:
            raise EnvelopeError(
                f"stage {stage.name} needs {need.threads} threads / {need.memory_gb:g} GB but the executor host allows "
                f"max_threads {limits.max_threads} / max_memory_gb {limits.max_memory_gb:g} (site.yaml); lower the "
                "stage's threads / memory in spec.yaml or raise that host's entry")
    wanted = max(1, parallel_jobs or spec.simulator.parallel_jobs)
    threads = max([s.resources.threads + extraction_threads(s) for s in pipeline] + [1])
    memory = max([s.resources.memory_gb for s in pipeline] + [0.0])
    if threads > limits.max_threads:          # every stage fits alone: a testbench one does not with its extra core
        raise EnvelopeError(
            f"a testbench job needs {threads - EXTRACTION_THREADS} + {EXTRACTION_THREADS} threads but the executor host "
            f"allows max_threads {limits.max_threads} (site.yaml): {EXTRACTION_NOTE}; lower threads_per_run in spec.yaml "
            "or raise that host's entry")
    return min(wanted, limits.slots(threads, memory))


def run(
    spec: Spec,
    pipeline: list[Stage],
    points: list[Point],
    executor: Executor,
    store: RunStore,
    *,
    corners: str | list[str] = "all",
    step: str = "evaluate",
    cshrc: str | None = None,
    parallel_jobs: int | None = None,
    limits: HostLimits,
    schedule: Schedule | None = None,
) -> Observations:
    point_stages = [s for s in pipeline if s.level == "point"]
    child_stages = [s for s in pipeline if s.level == "child"]
    if pipeline != point_stages + child_stages:
        raise ValueError("pipeline must list point-level stages before child-level stages")
    corner_ids = spec.corner_ids if corners == "all" else list(corners)
    children = children_of(spec, pipeline, corner_ids)
    if not children:
        raise ValueError("pipeline produces no children for this spec (no testbenches for its testbench chain, no devices for its device chain)")
    children_wanted = {c.key for c in children}
    wanted = [c.key for c in children]
    counted = {c.key for c in counted_children(pipeline, children)}                     # the simulations among them
    sims_per_point = len(counted) + point_runs(pipeline)         # worst case: every cacheable point stage misses
    workers = workers_for(spec, pipeline, parallel_jobs, limits)
    spec_fp, pipe_fp = spec.fingerprint(), pipeline_fingerprint(pipeline, executor)     # after the envelope check: it asks the host
    same_problem = {spec_fp, spec._legacy_fingerprint()}                                # see "Identity" above

    with store.lock():
        existing = store.observations()
        reusable = {o.key: o for o in existing
                    if o.spec_fingerprint in same_problem and o.pipeline_fingerprint == pipe_fp and o.status == "ok"
                    and set(o.children) == children_wanted}
        used = sum(simulations(o) for o in existing)
        next_index = store.next_obs_index()
        jobs: list[Job] = []
        for point in points:
            if point.key in reusable:
                jobs.append(Job(reusable[point.key].obs_id, point, reusable[point.key], reused=True))
                continue
            if used + sims_per_point > spec.budget.max_simulations:
                raise BudgetExceeded(
                    f"budget exhausted: {used} simulations used, {sims_per_point} needed per point, "
                    f"max_simulations={spec.budget.max_simulations}; {len(jobs)} of {len(points)} points scheduled"
                )
            used += sims_per_point
            jobs.append(Job(f"obs_{next_index:04d}", point))
            next_index += 1

        append_lock = threading.Lock()
        stop = threading.Event()                          # set when this thread is interrupted (see "Interrupts" above)
        interrupts = process_group.interrupts()

        def stopping() -> bool:
            return stop.is_set() or process_group.interrupts() != interrupts

        def evaluate_job(job: Job) -> Job:
            if job.reused:
                return job
            _unless_stopping(stopping, job.obs_id)        # taken from the queue after the interrupt: it never starts
            job.started = True
            started = time.monotonic()
            started_at = utc_now()
            results, cache, job.stopped_after = _run_point(
                spec, point_stages, child_stages, job, children, executor, store, cshrc, stopping, schedule)
            if stopping() and any(r.status != "ok" for r in results.values()):
                raise Interrupted(f"{job.obs_id}: interrupted")      # its commands got the interrupt: not the point's own failure
            agg = aggregate(spec, results, wanted)                    # an incomplete set when the schedule stopped the point
            job.observation = Observation(
                obs_id=job.obs_id, params=job.point.params, origin=job.point.origin, children=results,
                not_run=[key for key in wanted if key not in results],   # the children a stop left out, in the engine's order
                metrics=agg.metrics, fom=agg.fom, objective=agg.objective, feasible=agg.feasible,
                constraint_penalty=agg.constraint_penalty, status=agg.status, issues=agg.issues,
                spec_fingerprint=spec_fp, pipeline_fingerprint=pipe_fp, step=step, cache=cache,
                simulations=sum(1 for key in results if key in counted) + sum(1 for v in cache.values() if v == "miss"),
                started_at=started_at, finished_at=utc_now(),
            )
            job.seconds = time.monotonic() - started
            with append_lock:
                store.append(job.observation)
                job.recorded = True
            _retain(spec, job, results, executor, store)
            return job

        began = time.monotonic()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            try:
                jobs = list(pool.map(evaluate_job, jobs))
            except KeyboardInterrupt:                     # Ctrl-C (or a job that saw it first): start nothing more
                stop.set()
                try:
                    pool.shutdown(wait=True, cancel_futures=True)   # the queued points never start; the running ones end
                finally:
                    _log_interrupted(store, step, jobs, workers)
                raise

    store.log_step(
        step, "ok", points=len(points), new=sum(not j.reused for j in jobs), reused=sum(j.reused for j in jobs),
        simulations=sum(simulations(j.observation) for j in jobs if not j.reused), workers=workers,
        stopped=sum(1 for j in jobs if j.observation.not_run), not_run=sum(len(j.observation.not_run) for j in jobs),
        seconds=round(sum(j.seconds for j in jobs), 1),              # the points' own durations added up (they overlap)
        wall_seconds=round(time.monotonic() - began, 1),             # what the batch took on the clock
    )
    return Observations(j.observation for j in jobs)


def _log_interrupted(store: RunStore, step: str, jobs: list[Job], workers: int) -> None:
    """The step log of an interrupted run: the new points recorded before the interrupt, those that were running and are
    not recorded (their commands got it), and those that never started."""
    new = [j for j in jobs if not j.reused]
    done = [j for j in new if j.recorded]
    store.log_step(
        step, "interrupted", points=len(jobs), reused=len(jobs) - len(new), recorded=[j.obs_id for j in done],
        interrupted=[j.obs_id for j in new if j.started and not j.recorded], not_started=[j.obs_id for j in new if not j.started],
        simulations=sum(simulations(j.observation) for j in done), workers=workers, seconds=round(sum(j.seconds for j in done), 1),
        stopped=sum(1 for j in done if j.observation.not_run), not_run=sum(len(j.observation.not_run) for j in done),
    )


def _unless_stopping(stopping, where: str) -> None:
    if stopping is not None and stopping():
        raise Interrupted(f"interrupted before {where}")


def _run_stages(stages: list[Stage], value, ctx: StageContext, stopping=None):
    """Run stages in order; a StageFailure comes back tagged with the stage that raised it. So does a command that outlived
    its deadline (``CommandTimeout``, its process group already killed by the executor): a job that hangs fails its point
    as ``failed:<stage>``, with the timeout and its deadline as the issue, and the other points run on. Once ``stopping()``
    says the run was interrupted, no further stage starts (``Interrupted``)."""
    for stage in stages:
        _unless_stopping(stopping, f"{ctx.obs_id} {stage.name}")
        try:
            value = _run_cached(stage, value, ctx, stopping) if stage.level == "point" else stage.run(value, ctx)
        except StageFailure as failure:
            failure.stage = stage.name
            raise
        except CommandTimeout as timeout:
            failure = StageFailure(str(timeout))            # "timed out after <deadline>s: <command> (...)"
            failure.stage = stage.name
            raise failure from timeout
    return value


_FLIGHTS: dict[str, threading.Lock] = {}
_FLIGHTS_GUARD = threading.Lock()


def _flight(entry: Path) -> threading.Lock:
    """One lock per cache entry, for the life of the process."""
    with _FLIGHTS_GUARD:
        return _FLIGHTS.setdefault(str(entry), threading.Lock())


def _run_cached(stage: Stage, value, ctx: StageContext, stopping=None):
    """Point-level stages with a fingerprint are served from the store's cache; the engine owns the cache, the stage its format.

    Points of one batch with the same fingerprint do the work once: the first runs it, the others wait for its entry and read
    it as a hit. Before N-54 (2026-09-28) each of them missed and ran, the later results thrown away: 61 EMX runs for 55
    geometries in the N-51 campaign."""
    fingerprint = stage.fingerprint(value, ctx)
    if fingerprint is None:
        return stage.run(value, ctx)
    entry = ctx.store.cache_dir(stage.name, fingerprint)
    with _flight(entry):
        if (entry / ".complete").exists():
            ctx.cache[stage.name] = "hit"
            return stage.load(entry, value, ctx)
        _unless_stopping(stopping, f"{ctx.obs_id} {stage.name}")      # interrupted while it waited: the work does not start
        out = stage.run(value, ctx)
        ctx.cache[stage.name] = "miss"
        staging = Path(tempfile.mkdtemp(prefix=f".{fingerprint}.", dir=entry.parent))
        stage.save(out, staging)
        (staging / ".complete").touch()
        if (entry / ".complete").exists():           # another process finished the same work first; keep the first writer
            shutil.rmtree(staging, ignore_errors=True)
        else:
            staging.rename(entry)
    return out


def _run_point(spec, point_stages, child_stages, job: Job, children: list[Child], executor, store, cshrc, stopping=None,
               schedule: Schedule | None = None):
    """The point's results (in the order of ``children``), its point-level cache use, and why ``schedule`` stopped it
    early (None when every child ran). A stop found at a device child takes effect at the first testbench child after
    it: the device children in between still run (N-63, module docstring)."""
    point_dir = store.root / "sims" / job.obs_id
    point_dir.mkdir(parents=True, exist_ok=True)
    ctx = StageContext(spec=spec, executor=executor, store=store, obs_id=job.obs_id, workdir=point_dir,
                       remote_dir=executor.scratch(job.obs_id), cshrc=cshrc, point=job.point)
    try:
        point_output = _run_stages(point_stages, job.point, ctx, stopping)
    except StageFailure as failure:
        failed = {c.key: ChildResult(unit=c.unit, corner=c.corner, status=f"failed:{failure.stage}", issues=failure.issues) for c in children}
        return failed, dict(ctx.cache), None

    ran: dict[str, ChildResult] = {}
    order = children if schedule is None else schedule.order(children)
    reason = None
    for place, child in enumerate(order):
        if reason is not None and child.unit_kind != "device":
            break                                                   # a device showed the point cannot be feasible
        _unless_stopping(stopping, f"{job.obs_id} {child.key}")     # before the child's scratch directory, too
        chain = [s for s in child_stages if getattr(s, "unit", "testbench") == child.unit_kind]
        workdir = store.sim_dir(job.obs_id, child.unit, child.corner)
        cctx = StageContext(spec=spec, executor=executor, store=store, obs_id=job.obs_id, workdir=workdir,
                            remote_dir=executor.scratch(f"{job.obs_id}/{child.key}"),
                            unit=child.unit, corner=child.corner, cshrc=cshrc, point=job.point)
        started = time.monotonic()
        try:
            result = _run_stages(chain, point_output, cctx, stopping)
        except StageFailure as failure:
            result = ChildResult(unit=child.unit, corner=child.corner, status=f"failed:{failure.stage}", issues=failure.issues)
        result.seconds = round(time.monotonic() - started, 3)
        result.sim_dir = store.relative(workdir)
        ran[child.key] = result
        if schedule is None or reason is not None or place + 1 == len(order):
            continue
        reason = schedule.stop_after(result, child.corner)
        if reason is not None and child.unit_kind != "device":
            break                                                   # the point cannot be feasible: nothing more runs for it
    results = {c.key: ran[c.key] for c in children if c.key in ran}  # the engine's order, whatever order they ran in
    return results, dict(ctx.cache), reason if len(results) < len(children) else None


def _retain(spec: Spec, job: Job, children: dict[str, ChildResult], executor: Executor, store: RunStore) -> None:
    ok = job.observation is not None and job.observation.status == "ok"
    keep = spec.simulator.keep_successful_runs if ok else spec.simulator.keep_failed_runs
    if keep:
        return
    for key in children:
        unit, corner = key.split("/", 1)
        remote_dir = executor.scratch(f"{job.obs_id}/{unit}/{corner}")
        executor.run(f"rm -rf {remote_dir}/psf")
        shutil.rmtree(store.sim_dir(job.obs_id, unit, None if corner == "nominal" else corner) / "psf", ignore_errors=True)
