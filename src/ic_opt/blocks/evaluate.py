"""sim.evaluate — run a pipeline on points and append observations (thin wrapper over the engine)."""

from __future__ import annotations

from ic_opt.deck import Deck
from ic_opt.eval import engine
from ic_opt.eval.stage import Stage
from ic_opt.executor import Executor
from ic_opt.observation import Observations
from ic_opt.sim.ocean import WaveformExport
from ic_opt.site import Site
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.stages import spectre_pipeline
from ic_opt.store import RunStore


def evaluate(
    spec: Spec,
    points: list[Point],
    executor: Executor,
    store: RunStore,
    *,
    deck: Deck | None = None,
    pipeline: list[Stage] | None = None,
    corners: str | list[str] = "all",
    waveforms: list[WaveformExport] = (),
    step: str = "evaluate",
    cshrc: str | None = None,
    parallel_jobs: int | None = None,
    site: Site | None = None,
) -> Observations:
    """Evaluate ``points``; pass either a ``deck`` (Spectre pipeline) or an explicit ``pipeline``."""
    from ic_opt.recipe import PLAN_MODE

    if pipeline is None:
        pipeline = default_pipeline(spec, deck, waveforms)
    if PLAN_MODE.get():
        print(f"[plan] sim.evaluate step={step!r}: {len(points)} points × {plan_shape(spec, pipeline, corners, executor, parallel_jobs, site)}")
        return Observations()
    return engine.run(
        spec, pipeline, points, executor, store, corners=corners, step=step, cshrc=cshrc, parallel_jobs=parallel_jobs, site=site
    )


def default_pipeline(spec: Spec, deck: Deck | None, waveforms=()) -> list[Stage]:
    """What the spec asks for: EM devices bound into testbenches, EM devices alone, or the plain Spectre chain."""
    if spec.devices:
        from ic_opt.stages.em_chain import em_circuit_pipeline, em_only_pipeline

        if spec.testbenches:
            if deck is None:
                raise ValueError("evaluate needs a deck for the testbenches")
            return em_circuit_pipeline(spec, deck, waveforms=list(waveforms))
        return em_only_pipeline(spec)
    if deck is None:
        raise ValueError("evaluate needs a deck (Spectre pipeline) or an explicit pipeline")
    return spectre_pipeline(spec, deck, waveforms=list(waveforms))


def plan_shape(spec: Spec, pipeline: list[Stage], corners, executor: Executor, parallel_jobs: int | None, site: Site | None) -> str:
    """'<children per point> ... on <host>, N workers (<heaviest stage> threads/memory)' for the --plan lines."""
    corner_ids = spec.corner_ids if corners == "all" else list(corners)
    children = engine.children_of(spec, pipeline, corner_ids)
    tb = sum(c.unit_kind == "testbench" for c in children)
    dev = sum(c.unit_kind == "device" for c in children)
    em = engine.point_runs(pipeline)
    parts = [f"{em} EMX runs" if em else "", f"{tb} testbench sims" if tb else "", f"{dev} device measurements" if dev else ""]
    heaviest = max(pipeline, key=lambda s: (s.resources.threads, s.resources.memory_gb))
    workers = engine.workers_for(spec, pipeline, parallel_jobs, site)
    return (f"({' + '.join(p for p in parts if p)}) = {len(children) + em} simulations per point on {executor.host}, "
            f"{workers} workers × {heaviest.resources.threads} threads"
            + (f" / {heaviest.resources.memory_gb:g} GB" if heaviest.resources.memory_gb else "") + f" ({heaviest.name})")
