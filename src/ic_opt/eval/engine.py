"""The evaluation engine: points in, observations out. Knows nothing about Spectre or EMX.

For every point it (1) reuses an existing ``ok`` observation of the same
spec / pipeline / point / children, (2) checks the simulation budget, (3) runs
the point-level stages once (a stage with a fingerprint is served from
``.icopt/cache/<stage>/<fingerprint>/`` when it has run before), (4) runs the
child-level chains — the testbench chain for every testbench × corner, the
device chain for every device — (5) aggregates the children under the corner
policy, (6) appends one Observation, (7) applies the retention policy to raw
simulation directories. Points run in parallel, capped by the site envelope
for the heaviest stage; a point's children run serially, like the legacy flow.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from ic_opt.eval.stage import Stage, StageContext, StageFailure, pipeline_fingerprint
from ic_opt.executor import Executor
from ic_opt.observation import ChildResult, Observation, Observations
from ic_opt.sim.corner import aggregate
from ic_opt.site import Site
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.store import RunStore, utc_now


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Job:
    obs_id: str
    point: Point
    observation: Observation | None = None
    reused: bool = False
    seconds: float = 0.0


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


def simulations(observation: Observation) -> int:
    """What an observation cost: as recorded, else (older records) its children plus every point-level stage that ran instead of hitting the cache."""
    if observation.simulations is not None:
        return observation.simulations
    return len(observation.children) + sum(1 for v in observation.cache.values() if v == "miss")


def workers_for(spec: Spec, pipeline: list[Stage], parallel_jobs: int | None, site: Site | None) -> int:
    """Concurrent points: the requested parallelism, capped by what the site allows for the heaviest stage."""
    wanted = max(1, parallel_jobs or spec.simulator.parallel_jobs)
    if site is None:
        return wanted
    threads = max([s.resources.threads for s in pipeline] + [1])
    memory = max([s.resources.memory_gb for s in pipeline] + [0.0])
    return min(wanted, site.slots(threads, memory))


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
    site: Site | None = None,
) -> Observations:
    point_stages = [s for s in pipeline if s.level == "point"]
    child_stages = [s for s in pipeline if s.level == "child"]
    if pipeline != point_stages + child_stages:
        raise ValueError("pipeline must list point-level stages before child-level stages")
    corner_ids = spec.corner_ids if corners == "all" else list(corners)
    children = children_of(spec, pipeline, corner_ids)
    if not children:
        raise ValueError("pipeline produces no children for this spec (no testbenches for its testbench chain, no devices for its device chain)")
    spec_fp, pipe_fp = spec.fingerprint(), pipeline_fingerprint(pipeline)
    children_wanted = {c.key for c in children}
    child_sims = child_simulates(pipeline)
    sims_per_point = (len(children) if child_sims else 0) + point_runs(pipeline)         # worst case: every cacheable point stage misses
    workers = workers_for(spec, pipeline, parallel_jobs, site)

    with store.lock():
        existing = store.observations()
        reusable = {o.key: o for o in existing
                    if o.spec_fingerprint == spec_fp and o.pipeline_fingerprint == pipe_fp and o.status == "ok"
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

        def evaluate_job(job: Job) -> Job:
            if job.reused:
                return job
            started = time.monotonic()
            started_at = utc_now()
            results, cache = _run_point(spec, point_stages, child_stages, job, children, executor, store, cshrc)
            agg = aggregate(spec, results)
            job.observation = Observation(
                obs_id=job.obs_id, params=job.point.params, origin=job.point.origin, children=results,
                metrics=agg.metrics, fom=agg.fom, objective=agg.objective, feasible=agg.feasible,
                constraint_penalty=agg.constraint_penalty, status=agg.status, issues=agg.issues,
                spec_fingerprint=spec_fp, pipeline_fingerprint=pipe_fp, step=step, cache=cache,
                simulations=(len(results) if child_sims else 0) + sum(1 for v in cache.values() if v == "miss"),
                started_at=started_at, finished_at=utc_now(),
            )
            job.seconds = time.monotonic() - started
            with append_lock:
                store.append(job.observation)
            _retain(spec, job, results, executor, store)
            return job

        with ThreadPoolExecutor(max_workers=workers) as pool:
            jobs = list(pool.map(evaluate_job, jobs))

    store.log_step(
        step, "ok", points=len(points), new=sum(not j.reused for j in jobs), reused=sum(j.reused for j in jobs),
        simulations=sum(simulations(j.observation) for j in jobs if not j.reused), workers=workers,
        seconds=round(sum(j.seconds for j in jobs), 1),
    )
    return Observations(j.observation for j in jobs)


def _run_stages(stages: list[Stage], value, ctx: StageContext):
    """Run stages in order; a StageFailure comes back tagged with the stage that raised it."""
    for stage in stages:
        try:
            value = _run_cached(stage, value, ctx) if stage.level == "point" else stage.run(value, ctx)
        except StageFailure as failure:
            failure.stage = stage.name
            raise
    return value


def _run_cached(stage: Stage, value, ctx: StageContext):
    """Point-level stages with a fingerprint are served from the store's cache; the engine owns the cache, the stage its format."""
    fingerprint = stage.fingerprint(value, ctx)
    if fingerprint is None:
        return stage.run(value, ctx)
    entry = ctx.store.cache_dir(stage.name, fingerprint)
    if (entry / ".complete").exists():
        ctx.cache[stage.name] = "hit"
        return stage.load(entry, value, ctx)
    out = stage.run(value, ctx)
    ctx.cache[stage.name] = "miss"
    staging = Path(tempfile.mkdtemp(prefix=f".{fingerprint}.", dir=entry.parent))
    stage.save(out, staging)
    (staging / ".complete").touch()
    if (entry / ".complete").exists():           # another point finished the same work first; keep the first writer
        shutil.rmtree(staging, ignore_errors=True)
    else:
        staging.rename(entry)
    return out


def _run_point(spec, point_stages, child_stages, job: Job, children: list[Child], executor, store, cshrc):
    point_dir = store.root / "sims" / job.obs_id
    point_dir.mkdir(parents=True, exist_ok=True)
    ctx = StageContext(spec=spec, executor=executor, store=store, obs_id=job.obs_id, workdir=point_dir,
                       remote_dir=executor.scratch(job.obs_id), cshrc=cshrc, point=job.point)
    try:
        point_output = _run_stages(point_stages, job.point, ctx)
    except StageFailure as failure:
        failed = {c.key: ChildResult(unit=c.unit, corner=c.corner, status=f"failed:{failure.stage}", issues=failure.issues) for c in children}
        return failed, dict(ctx.cache)

    results: dict[str, ChildResult] = {}
    for child in children:
        chain = [s for s in child_stages if getattr(s, "unit", "testbench") == child.unit_kind]
        workdir = store.sim_dir(job.obs_id, child.unit, child.corner)
        cctx = StageContext(spec=spec, executor=executor, store=store, obs_id=job.obs_id, workdir=workdir,
                            remote_dir=executor.scratch(f"{job.obs_id}/{child.key}"),
                            unit=child.unit, corner=child.corner, cshrc=cshrc, point=job.point)
        started = time.monotonic()
        try:
            result = _run_stages(chain, point_output, cctx)
        except StageFailure as failure:
            result = ChildResult(unit=child.unit, corner=child.corner, status=f"failed:{failure.stage}", issues=failure.issues)
        result.seconds = round(time.monotonic() - started, 3)
        result.sim_dir = store.relative(workdir)
        results[child.key] = result
    return results, dict(ctx.cache)


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
