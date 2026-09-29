"""sim.evaluate — run a pipeline on points and append observations (thin wrapper over the engine)."""

from __future__ import annotations

from collections.abc import Sequence

from ic_opt.deck import Deck
from ic_opt.eval import engine
from ic_opt.eval.schedule import Schedule
from ic_opt.eval.stage import Stage
from ic_opt.executor import Executor
from ic_opt.observation import Observation, Observations
from ic_opt.sim.corner import stopped_early
from ic_opt.sim.ocean import WaveformExport
from ic_opt.site import HostLimits
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
    limits: HostLimits,
    stop_at_first_failure: bool | None = None,
    initial: Sequence[Observation] = (),
) -> Observations:
    """Evaluate ``points``; pass either a ``deck`` (Spectre pipeline) or an explicit ``pipeline``.

    ``limits`` is the executor host's site.yaml entry (``run.limits`` in a recipe): it caps the concurrency and
    refuses a stage bigger than the host, under ``--plan`` too.

    ``stop_at_first_failure`` (None: the spec's ``simulator.stop_at_first_failure``, on unless the spec turns it off):
    a point's children run in an order learned from this problem's observations, and a point stops at the first child
    whose result shows it cannot be feasible (``ic_opt.eval.schedule``, T17.8); False runs every child of every point,
    for a complete per-corner table. ``initial``: observations from elsewhere the order is learned from too -- the rows
    ``opt.optimize`` adopted from its ``initial=``; they are neither evaluated nor stored."""
    from ic_opt.recipe import PLAN_MODE

    if pipeline is None:
        pipeline = default_pipeline(spec, deck, waveforms)
    stop = spec.simulator.stop_at_first_failure if stop_at_first_failure is None else stop_at_first_failure
    if PLAN_MODE.get():
        print(f"[plan] sim.evaluate step={step!r}: {len(points)} points × "
              f"{plan_shape(spec, pipeline, corners, executor, parallel_jobs, limits, stop)}")
        return Observations()
    obs = engine.run(
        spec, pipeline, points, executor, store, corners=corners, step=step, cshrc=cshrc, parallel_jobs=parallel_jobs, limits=limits,
        schedule=_schedule(spec, pipeline, corners, store, initial) if stop else None,
    )
    _report_failed_metrics(obs, step)
    _report_binding_constraints(obs, step)
    _report_stopped(obs, step)
    return obs


def _schedule(spec: Spec, pipeline: list[Stage], corners, store: RunStore, initial: Sequence[Observation]) -> Schedule:
    """The batch's schedule, learned from this problem's observations (``Schedule.from_history``): the store's, read
    under the store's lock -- the engine, which takes the lock next, gets the same rows from the store without reading
    the file again -- and ``initial``."""
    corner_ids = spec.corner_ids if corners == "all" else list(corners)
    children = engine.children_of(spec, pipeline, corner_ids)
    same_problem = {spec.fingerprint(), spec._legacy_fingerprint()}         # as the engine counts them ("Identity")
    with store.lock():
        rows = [o for o in store.observations() if o.spec_fingerprint in same_problem]
    return Schedule.from_history(spec, [*initial, *rows], children, spec.corner_policy.constraints)


def _report_stopped(obs, step: str) -> None:
    """One line per batch in which the schedule stopped points early (T17.8): how many, and the simulations not run."""
    stops = [s for o in obs if (s := stopped_early(o.issues)) is not None]
    if stops:
        print(f"[evaluate] step={step!r}: {len(stops)} of {len(obs)} points stopped early, "
              f"{sum(not_run for not_run, _wanted, _child in stops)} simulations not run")


def _report_binding_constraints(obs, step: str) -> None:
    """One line per batch naming the constraints that failed and on how many points (from the points' issues, `<metric>
    <op> <value> violated by ...`, per corner under all_corners), so the binding constraint is read off the run, not
    computed from observations.jsonl afterwards (N-42, 2026-09-27)."""
    counts: dict[str, int] = {}
    for o in obs:
        seen = set()
        for issue in o.issues:
            body = issue.split(": ", 1)[1] if ": " in issue else issue          # "<corner>: <metric> <op> <value> violated by ..."
            if " violated by " in body:
                name = body.split(" violated by ", 1)[0]
                if name not in seen:
                    seen.add(name)
                    counts[name] = counts.get(name, 0) + 1
    if counts:
        print(f"[evaluate] step={step!r}: constraints violated -- " + ", ".join(f"{k} on {v} of {len(obs)} points" for k, v in counts.items()))


def _report_failed_metrics(obs, step: str) -> None:
    """One line per metric that failed to extract on any point of this batch: a failed metric makes its point
    ``metric_failed`` and the optimizer scores that point with the failure penalty, so an expression wrong for a region of
    the space steers the search away from it in silence (N-35, 2026-09-27: half of a first batch, the wide-band points)."""
    failed: dict[str, dict[str, int]] = {}
    for o in obs:
        seen = set()
        for child in o.children.values():
            for issue in child.issues:
                name = reason = None
                if issue.startswith("metric ") and " failed: " in issue:
                    name, reason = issue[len("metric "):].split(" failed: ", 1)
                elif issue.startswith("metric ") and issue.endswith(" missing from OCEAN output"):
                    name, reason = issue[len("metric "):-len(" missing from OCEAN output")], "missing"
                if name and name not in seen:
                    seen.add(name)
                    failed.setdefault(name, {})[reason] = failed.setdefault(name, {}).get(reason, 0) + 1
    for name, reasons in failed.items():
        n = sum(reasons.values())
        print(f"[evaluate] step={step!r}: metric {name} failed on {n} of {len(obs)} points "
              f"({', '.join(f'{r} {c}' for r, c in reasons.items())}); those points are metric_failed and the optimizer "
              "penalizes them -- fix the expression before spending more budget")


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


def plan_shape(spec: Spec, pipeline: list[Stage], corners, executor: Executor, parallel_jobs: int | None, limits: HostLimits,
               stop_at_first_failure: bool | None = None) -> str:
    """'<children per point> ... on <host>, N workers (<heaviest stage> threads/memory)' for the --plan lines;
    refuses (EnvelopeError) a pipeline whose stage does not fit the host, so the preview fails where the run would.
    ``stop_at_first_failure`` as for :func:`evaluate` (None: the spec's); on, the count per point is a ceiling."""
    stop = spec.simulator.stop_at_first_failure if stop_at_first_failure is None else stop_at_first_failure
    workers = engine.workers_for(spec, pipeline, parallel_jobs, limits)
    corner_ids = spec.corner_ids if corners == "all" else list(corners)
    children = engine.children_of(spec, pipeline, corner_ids)
    tb = sum(c.unit_kind == "testbench" for c in children)
    dev = sum(c.unit_kind == "device" for c in children)
    em = engine.point_runs(pipeline)
    parts = [f"{em} EMX runs" if em else "", f"{tb} testbench sims" if tb else "", f"{dev} device measurements" if dev else ""]
    heaviest = max(pipeline, key=lambda s: (s.resources.threads, s.resources.memory_gb))
    cap = spec.em.parallel_jobs if em and spec.em is not None else None
    count = f"{len(children) + em} simulations per point"
    if stop:
        count = f"up to {count} (a point stops at the first simulation that fails it)"
    return (f"({' + '.join(p for p in parts if p)}) = {count} on {executor.host}, "
            f"{workers} workers × {heaviest.resources.threads} threads"
            + (f" / {heaviest.resources.memory_gb:g} GB" if heaviest.resources.memory_gb else "") + f" ({heaviest.name})"
            + (f", EMX at once ≤ {cap} (em.parallel_jobs)" if cap else "")
            + (", EMX results cached per geometry: a repeated geometry costs no run" if em else ""))
