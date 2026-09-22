"""sim.evaluate — run a pipeline on points and append observations (thin wrapper over the engine)."""

from __future__ import annotations

from ic_opt.deck import Deck
from ic_opt.eval import engine
from ic_opt.eval.stage import Stage
from ic_opt.executor import Executor
from ic_opt.observation import Observations
from ic_opt.sim.ocean import WaveformExport
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
) -> Observations:
    """Evaluate ``points``; pass either a ``deck`` (Spectre pipeline) or an explicit ``pipeline``."""
    from ic_opt.recipe import PLAN_MODE

    if PLAN_MODE.get():
        corner_ids = spec.corner_ids if corners == "all" else list(corners)
        sims = len(points) * len(spec.testbenches) * len(corner_ids)
        print(f"[plan] sim.evaluate step={step!r}: {len(points)} points × {len(spec.testbenches)} tb × {len(corner_ids)} corner "
              f"= {sims} simulations on {executor.host}, {spec.simulator.parallel_jobs} jobs × {spec.simulator.threads_per_run} threads")
        return Observations()
    if pipeline is None:
        if deck is None:
            raise ValueError("evaluate needs a deck (Spectre pipeline) or an explicit pipeline")
        pipeline = spectre_pipeline(spec, deck, waveforms=list(waveforms))
    return engine.run(
        spec, pipeline, points, executor, store, corners=corners, step=step, cshrc=cshrc, parallel_jobs=parallel_jobs
    )
