"""The evaluation engine: points in, observations out. Knows nothing about Spectre or EMX.

For every point it (1) reuses an existing ``ok`` observation of the same
spec/pipeline/point/corners, (2) checks the simulation budget, (3) runs the
point-level stages once, (4) runs the child-level stages for every
testbench × corner, (5) aggregates the children under the corner policy,
(6) appends one Observation, (7) applies the retention policy to raw
simulation directories. Points run in parallel; a point's children run
serially, exactly like the legacy flow.

Stage caching (``Stage.fingerprint``) is reserved for the first cacheable
stage (EM); this engine does not consult it yet.
"""

from __future__ import annotations

import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ic_opt.eval.stage import Stage, StageContext, StageFailure, pipeline_fingerprint
from ic_opt.executor import Executor
from ic_opt.observation import ChildResult, Observation, Observations
from ic_opt.sim.corner import aggregate
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
) -> Observations:
    point_stages = [s for s in pipeline if s.level == "point"]
    child_stages = [s for s in pipeline if s.level == "child"]
    if pipeline != point_stages + child_stages:
        raise ValueError("pipeline must list point-level stages before child-level stages")
    corner_ids = spec.corner_ids if corners == "all" else list(corners)
    spec_fp, pipe_fp = spec.fingerprint(), pipeline_fingerprint(pipeline)
    children_wanted = {f"{tb.id}/{c or 'nominal'}" for tb in spec.testbenches for c in corner_ids}
    sims_per_point = len(children_wanted)
    workers = max(1, parallel_jobs or spec.simulator.parallel_jobs)

    with store.lock():
        existing = store.observations()
        reusable = {o.key: o for o in existing
                    if o.spec_fingerprint == spec_fp and o.pipeline_fingerprint == pipe_fp and o.status == "ok"
                    and set(o.children) == children_wanted}
        used = sum(len(o.children) for o in existing)
        next_index = len(existing) + 1
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
            children = _run_point(spec, point_stages, child_stages, job, corner_ids, executor, store, cshrc)
            agg = aggregate(spec, children)
            job.observation = Observation(
                obs_id=job.obs_id, params=job.point.params, origin=job.point.origin, children=children,
                metrics=agg.metrics, fom=agg.fom, objective=agg.objective, feasible=agg.feasible,
                constraint_penalty=agg.constraint_penalty, status=agg.status, issues=agg.issues,
                spec_fingerprint=spec_fp, pipeline_fingerprint=pipe_fp, step=step,
                started_at=started_at, finished_at=utc_now(),
            )
            job.seconds = time.monotonic() - started
            with append_lock:
                store.append(job.observation)
            _retain(spec, job, children, executor, store)
            return job

        with ThreadPoolExecutor(max_workers=workers) as pool:
            jobs = list(pool.map(evaluate_job, jobs))

    store.log_step(
        step, "ok", points=len(points), new=sum(not j.reused for j in jobs), reused=sum(j.reused for j in jobs),
        simulations=sum(len(j.observation.children) for j in jobs if not j.reused), seconds=round(sum(j.seconds for j in jobs), 1),
    )
    return Observations(j.observation for j in jobs)


def _run_stages(stages: list[Stage], value, ctx: StageContext):
    """Run stages in order; a StageFailure comes back tagged with the stage that raised it."""
    for stage in stages:
        try:
            value = stage.run(value, ctx)
        except StageFailure as failure:
            failure.stage = stage.name
            raise
    return value


def _run_point(spec, point_stages, child_stages, job: Job, corner_ids, executor, store, cshrc) -> dict[str, ChildResult]:
    point_dir = store.root / "sims" / job.obs_id
    point_dir.mkdir(parents=True, exist_ok=True)
    ctx = StageContext(spec=spec, executor=executor, store=store, obs_id=job.obs_id, workdir=point_dir,
                       remote_dir=executor.scratch(job.obs_id), cshrc=cshrc)
    try:
        point_output = _run_stages(point_stages, job.point, ctx)
    except StageFailure as failure:
        return {
            f"{tb}/{corner or 'nominal'}": ChildResult(testbench=tb, corner=corner, status=f"failed:{failure.stage}", issues=failure.issues)
            for tb in spec.testbench_ids for corner in corner_ids
        }

    children: dict[str, ChildResult] = {}
    for tb in spec.testbench_ids:
        for corner in corner_ids:
            key = f"{tb}/{corner or 'nominal'}"
            workdir = store.sim_dir(job.obs_id, tb, corner)
            cctx = StageContext(spec=spec, executor=executor, store=store, obs_id=job.obs_id, workdir=workdir,
                                remote_dir=executor.scratch(f"{job.obs_id}/{tb}/{corner or 'nominal'}"),
                                testbench=tb, corner=corner, cshrc=cshrc)
            started = time.monotonic()
            try:
                child = _run_stages(child_stages, point_output, cctx)
            except StageFailure as failure:
                child = ChildResult(testbench=tb, corner=corner, status=f"failed:{failure.stage}", issues=failure.issues)
            child.seconds = round(time.monotonic() - started, 3)
            child.sim_dir = store.relative(workdir)
            children[key] = child
    return children


def _retain(spec: Spec, job: Job, children: dict[str, ChildResult], executor: Executor, store: RunStore) -> None:
    ok = job.observation is not None and job.observation.status == "ok"
    keep = spec.simulator.keep_successful_runs if ok else spec.simulator.keep_failed_runs
    if keep:
        return
    for key in children:
        tb, corner = key.split("/", 1)
        remote_dir = executor.scratch(f"{job.obs_id}/{tb}/{corner}")
        executor.run(f"rm -rf {remote_dir}/psf")
        shutil.rmtree(store.sim_dir(job.obs_id, tb, None if corner == "nominal" else corner) / "psf", ignore_errors=True)
