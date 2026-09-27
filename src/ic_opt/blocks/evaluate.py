"""sim.evaluate — run a pipeline on points and append observations (thin wrapper over the engine)."""

from __future__ import annotations

from ic_opt.deck import Deck
from ic_opt.eval import engine
from ic_opt.eval.stage import Stage
from ic_opt.executor import Executor
from ic_opt.observation import Observations
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
) -> Observations:
    """Evaluate ``points``; pass either a ``deck`` (Spectre pipeline) or an explicit ``pipeline``.

    ``limits`` is the executor host's site.yaml entry (``run.limits`` in a recipe): it caps the concurrency and
    refuses a stage bigger than the host, under ``--plan`` too."""
    from ic_opt.recipe import PLAN_MODE

    if pipeline is None:
        pipeline = default_pipeline(spec, deck, waveforms)
    if PLAN_MODE.get():
        print(f"[plan] sim.evaluate step={step!r}: {len(points)} points × {plan_shape(spec, pipeline, corners, executor, parallel_jobs, limits)}")
        return Observations()
    obs = engine.run(
        spec, pipeline, points, executor, store, corners=corners, step=step, cshrc=cshrc, parallel_jobs=parallel_jobs, limits=limits
    )
    _report_failed_metrics(obs, step)
    return obs


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


def plan_shape(spec: Spec, pipeline: list[Stage], corners, executor: Executor, parallel_jobs: int | None, limits: HostLimits) -> str:
    """'<children per point> ... on <host>, N workers (<heaviest stage> threads/memory)' for the --plan lines;
    refuses (EnvelopeError) a pipeline whose stage does not fit the host, so the preview fails where the run would."""
    workers = engine.workers_for(spec, pipeline, parallel_jobs, limits)
    corner_ids = spec.corner_ids if corners == "all" else list(corners)
    children = engine.children_of(spec, pipeline, corner_ids)
    tb = sum(c.unit_kind == "testbench" for c in children)
    dev = sum(c.unit_kind == "device" for c in children)
    em = engine.point_runs(pipeline)
    parts = [f"{em} EMX runs" if em else "", f"{tb} testbench sims" if tb else "", f"{dev} device measurements" if dev else ""]
    heaviest = max(pipeline, key=lambda s: (s.resources.threads, s.resources.memory_gb))
    return (f"({' + '.join(p for p in parts if p)}) = {len(children) + em} simulations per point on {executor.host}, "
            f"{workers} workers × {heaviest.resources.threads} threads"
            + (f" / {heaviest.resources.memory_gb:g} GB" if heaviest.resources.memory_gb else "") + f" ({heaviest.name})")
