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

    same_problem = {spec.fingerprint(), spec._legacy_fingerprint()}     # a store stamped before T15.2 is this problem too (engine.py, "Identity")
    design = _initial_design(spec, strategy, strategy_kwargs, budget)
    if design is not None and not strategy_kwargs.get("initial_trials"):
        strategy_kwargs = {**strategy_kwargs, "initial_trials": design}      # the suggester runs the design this run printed
    if PLAN_MODE.get():
        done = len(Observations(o for o in store.observations() if o.spec_fingerprint in same_problem).by_step(step))
        shape = pipeline if pipeline is not None else default_pipeline(spec, deck or Deck(), waveforms)
        print(f"[plan] opt.optimize step={step!r} strategy={strategy}: {done}/{budget} points done, "
              f"up to {max(0, budget - done)} more in batches of {batch} × "
              f"{plan_shape(spec, shape, corners, executor, parallel_jobs, limits)} (spec budget {spec.budget.max_simulations})")
        _print_design(design, done, budget, batch, plan=True)
        return Observations()
    _print_design(design, len(Observations(o for o in store.observations() if o.spec_fingerprint in same_problem).by_step(step)),
                  budget, batch, plan=False)
    while True:
        mine = Observations(o for o in store.observations() if o.spec_fingerprint in same_problem)
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
    return Observations(o for o in store.observations() if o.spec_fingerprint in same_problem and o.step == step)


def _initial_design(spec: Spec, strategy: str, strategy_kwargs: dict, budget: int) -> int | None:
    """The OpenBox strategies' initial design size: ``initial_trials`` when given, else min(2 x variables, budget // 2), at
    least one (``suggesters.openbox.initial_design_size``); None for the other strategies."""
    if not strategy.startswith("openbox"):
        return None
    from ic_opt.suggesters.openbox import initial_design_size

    return initial_design_size(spec, strategy_kwargs.get("initial_trials"), budget)


def surrogate_points(done: int, budget: int, batch: int, design: int) -> int:
    """How many of the ``budget - done`` new points the surrogate proposes: a batch is served by the surrogate only when the
    history at its start already holds ``design`` points; every earlier batch is initial design in full (OpenBox decides per
    batch). The 2026-09-27 real-scenario acceptance (N-27, ISSUE-8) ran 12 points in batches of 6 on 4 variables (design 8)
    and never reached the surrogate; nothing said so."""
    points, start = 0, done
    while start < budget:
        size = min(batch, budget - start)
        if start >= design:
            points += size
        start += size
    return points


def _print_design(design: int | None, done: int, budget: int, batch: int, *, plan: bool) -> None:
    if design is None or done >= budget:
        return
    proposed = surrogate_points(done, budget, batch, design)
    tag = "[plan] " if plan else "[opt] "
    line = f"{tag}openbox initial design {design} points: the surrogate proposes {proposed} of the {budget - done} new points"
    if proposed == 0:
        line += (" -- WARNING: none; this run is space-filling initial design throughout (every batch starts inside the "
                 f"design). Use a batch smaller than {design}, raise budget, or pass initial_trials=N below the batch")
    print(line)


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
