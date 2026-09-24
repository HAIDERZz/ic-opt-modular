"""opt.suggest / opt.optimize — propose points from observations; loop suggest ⇄ evaluate.

``suggest`` is stateless: the model is rebuilt from the observations you hand
it, so continuation is "run again with a bigger budget" and warm start is
``initial=<observations from elsewhere>``. Foreign observations are re-scored
under this spec from their metrics before they are used.
"""

from __future__ import annotations

from collections.abc import Sequence

from ic_opt import objective as objective_contract
from ic_opt import space, suggesters
from ic_opt.blocks.evaluate import evaluate
from ic_opt.deck import Deck
from ic_opt.eval.stage import Stage
from ic_opt.executor import Executor
from ic_opt.observation import Observation, Observations
from ic_opt.sim.ocean import WaveformExport
from ic_opt.site import HostLimits
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.store import RunStore


def suggest(
    spec: Spec,
    observations: Sequence[Observation],
    n: int,
    *,
    strategy: str = "openbox_gp_eic",
    seed: int = 0,
    initial: Sequence[Observation] = (),
    failure_penalty: float = 1e6,
    **strategy_kwargs,
) -> list[Point]:
    """Propose ``n`` new grid points not already in ``observations`` or ``initial``."""
    history = Observations(list(adopt(spec, initial)) + list(observations))
    suggester = suggesters.make(strategy, failure_penalty=failure_penalty, **strategy_kwargs)
    taken = history.keys()
    points: list[Point] = []
    origin = f"suggest:{suggester.name}"
    for attempt in range(4):
        missing = n - len(points)
        if missing <= 0:
            break
        proposal = suggester.propose(spec, history, missing, seed=seed + attempt)
        if attempt == 0 and proposal.tag:
            origin += f":{proposal.tag}"          # one provenance tag per batch; replacements share it
        for raw in proposal.raw:
            point = Point(space.snap(spec, raw), origin)
            if point.key not in taken:
                taken.add(point.key)
                points.append(point)
    if len(points) < n:      # the model keeps landing on evaluated grid points: fill with random ones
        filler = suggesters.RandomSuggester("random")
        for raw in filler.propose(spec, history, 4 * (n - len(points)), seed=seed + len(taken)).raw:
            point = Point(space.snap(spec, raw), origin)
            if point.key not in taken and len(points) < n:
                taken.add(point.key)
                points.append(point)
    return points


def optimize(
    spec: Spec,
    executor: Executor,
    store: RunStore,
    *,
    budget: int,
    batch: int = 10,
    strategy: str = "openbox_gp_eic",
    deck: Deck | None = None,
    pipeline: list[Stage] | None = None,
    corners: str | list[str] = "all",
    waveforms: list[WaveformExport] = (),
    initial: Sequence[Observation] = (),
    seed: int = 0,
    step: str = "optimize",
    cshrc: str | None = None,
    parallel_jobs: int | None = None,
    limits: HostLimits,
    failure_penalty: float = 1e6,
    **strategy_kwargs,
) -> Observations:
    """Run suggest ⇄ evaluate until this step holds ``budget`` observations. Re-running continues.

    ``limits`` is the executor host's site.yaml entry (``run.limits``), passed on to every ``sim.evaluate``."""
    from ic_opt.blocks.evaluate import default_pipeline, plan_shape
    from ic_opt.recipe import PLAN_MODE

    fp = spec.fingerprint()
    if PLAN_MODE.get():
        done = len(Observations(o for o in store.observations() if o.spec_fingerprint == fp).by_step(step))
        shape = pipeline if pipeline is not None else default_pipeline(spec, deck or Deck(), waveforms)
        print(f"[plan] opt.optimize step={step!r} strategy={strategy}: {done}/{budget} points done, "
              f"up to {max(0, budget - done)} more in batches of {batch} × "
              f"{plan_shape(spec, shape, corners, executor, parallel_jobs, limits)} (spec budget {spec.budget.max_simulations})")
        return Observations()
    while True:
        mine = Observations(o for o in store.observations() if o.spec_fingerprint == fp)
        done = len(mine.by_step(step))
        if done >= budget:
            break
        points = suggest(
            spec, mine, min(batch, budget - done), strategy=strategy, seed=seed + done, initial=initial,
            failure_penalty=failure_penalty, **strategy_kwargs,
        )
        if not points:
            break
        evaluate(
            spec, points, executor, store, deck=deck, pipeline=pipeline, corners=corners, waveforms=waveforms,
            step=step, cshrc=cshrc, parallel_jobs=parallel_jobs, limits=limits,
        )
    return Observations(o for o in store.observations() if o.spec_fingerprint == fp and o.step == step)


def adopt(spec: Spec, foreign: Sequence[Observation]) -> Observations:
    """Re-score observations from another project/spec under this spec; drop incompatible ones."""
    adopted = Observations()
    for obs in foreign:
        try:
            space.check(spec, obs.params)
        except ValueError:
            continue
        ev = objective_contract.evaluate(spec, obs.metrics)
        adopted.append(obs.model_copy(update={
            "fom": ev.fom, "objective": ev.objective, "feasible": ev.feasible, "status": ev.status,
            "constraint_penalty": ev.constraint_penalty, "origin": f"initial:{obs.origin}",
            "spec_fingerprint": spec.fingerprint(),
        }))
    return adopted
