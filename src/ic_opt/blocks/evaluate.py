"""sim.evaluate — run a pipeline on points and append observations (thin wrapper over the engine)."""

from __future__ import annotations

from collections.abc import Sequence

from ic_opt.deck import Deck
from ic_opt.eval import engine
from ic_opt.eval.schedule import Schedule
from ic_opt.eval.stage import Stage
from ic_opt.executor import Executor
from ic_opt.observation import Observation, Observations
from ic_opt.sim.corner import stopped_at_device
from ic_opt.sim.ocean import WaveformExport
from ic_opt.site import HostLimits, per_job
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

    ``stop_at_first_failure`` (None: :func:`stop_kinds` decides by the spec's ``simulator.stop_at_first_failure`` and the
    simulations a point needs): a point stops at the first child whose result shows it cannot be feasible
    (``ic_opt.eval.schedule``, T17.8) -- a testbench child when :func:`stop_wanted` says so, its testbench children then
    running in an order learned from this problem's observations, and an EM device's or a library row's measurement,
    which runs first, unless the spec's switch or this override is False (N-63); False runs every child of every point,
    for a complete per-corner table. ``initial``: observations from elsewhere the order is learned from too -- the rows
    ``opt.optimize`` adopted from its ``initial=``; they are neither evaluated nor stored."""
    from ic_opt.recipe import PLAN_MODE

    if pipeline is None:
        pipeline = default_pipeline(spec, deck, waveforms)
    corner_ids = spec.corner_ids if corners == "all" else list(corners)
    children = engine.children_of(spec, pipeline, corner_ids)
    kinds = stop_kinds(spec, children, stop_at_first_failure)
    if PLAN_MODE.get():
        print(f"[plan] sim.evaluate step={step!r}: {len(points)} points × "
              f"{plan_shape(spec, pipeline, corners, executor, parallel_jobs, limits, stop_at_first_failure)}")
        return Observations()
    obs = engine.run(
        spec, pipeline, points, executor, store, corners=corners, step=step, cshrc=cshrc, parallel_jobs=parallel_jobs, limits=limits,
        schedule=_schedule(spec, pipeline, corners, store, initial, kinds) if kinds else None,
    )
    _report_failed_metrics(obs, step)
    _report_binding_constraints(obs, step)
    _report_stopped(spec, obs, step, devices=any(c.unit_kind == "device" for c in children))
    return obs


STOP_FROM_SIMULATIONS = 20   # a point that runs this many simulations or more stops at its first failing one by default
DEVICE_FIRST = "the device measured first: a point whose device fails a constraint stops before any testbench simulation"
TESTBENCH_STOP = "a point stops at the first simulation that fails it"     # the plan line's words for each kind of stop


def stop_wanted(spec: Spec, children: Sequence[engine.Child], override: bool | None = None) -> bool:
    """Whether the points of a batch stop at their first failing simulation (``ic_opt.eval.schedule``): ``override`` (a
    recipe's ``stop_at_first_failure=``) when given, else the spec's ``simulator.stop_at_first_failure`` when set, else on
    when ``children`` -- what one point runs at the run's corners, ``engine.children_of`` -- hold at least
    :data:`STOP_FROM_SIMULATIONS` testbench simulations (testbenches × corners; an EM device does not count) and off
    below that.

    Why the default follows the simulations a point needs (T17.9 revision 2, N-92; the research benchmark, the same
    proposer throughout, the stop against every point at every corner, final objective in paired runs): at 62
    simulations per point (31 corners) the stop was better in 27 of 30 and found a feasible design in 30 of 30 runs
    against 22; at 18 per point the two were even (11 to 7, the first feasible design later in 13 of 18); at 6 per
    point the stop was worse in 15 of 18 (p = 0.008), at 2 per point in 12 of 18: a point stopped early loses the
    metrics of the simulations it did not run, and when a point needs few simulations that loss outweighs what the
    stop saves. Between 20 and 61 nothing was measured; 20 is where the loss had disappeared. The spec's switch
    forces either way.

    This is the rule for a testbench child's failure; a device child's has its own (:func:`stop_kinds`, N-63)."""
    if override is not None:
        return override
    if spec.simulator.stop_at_first_failure is not None:
        return spec.simulator.stop_at_first_failure
    return sum(1 for c in children if c.unit_kind != "device") >= STOP_FROM_SIMULATIONS


def stop_kinds(spec: Spec, children: Sequence[engine.Child], override: bool | None = None) -> frozenset[str]:
    """The kinds of child whose failure stops a point of the batch (``Schedule.stop_kinds``), two independent rules over
    ``children`` (what one point runs at the run's corners, ``engine.children_of``):

    - ``testbench`` when :func:`stop_wanted` says so: ``override``, else the spec's switch, else the count rule;
    - ``device`` when the children hold a device child and a testbench child and neither ``override`` nor, without one,
      the spec's ``simulator.stop_at_first_failure`` is False (N-63, ``docs/refactor/N63_DEVICE_FIRST_SPEC.md``). The
      device is measured first, at no simulation's cost, and a point whose device fails a constraint is known infeasible
      before any simulation ran; the metrics its simulations would give the models come from a device that cannot be
      used. The loss the count rule weighs (T17.9 revision 2) was measured for testbench stops, where a stopped point
      loses the metrics of a device that is fine. Without a testbench child there is nothing to stop before.

    Empty: no schedule (``sim.evaluate`` builds none), the engine's order, no stop -- a circuit-only run below the count
    rule and any run under ``stop_at_first_failure: false`` (or the override False: ``signoff full=true``)."""
    kinds = {"testbench"} if stop_wanted(spec, children, override) else set()
    device_rule = override if override is not None else spec.simulator.stop_at_first_failure is not False
    unit_kinds = {c.unit_kind for c in children}
    if device_rule and "device" in unit_kinds and unit_kinds - {"device"}:
        kinds.add("device")
    return frozenset(kinds)


def _schedule(spec: Spec, pipeline: list[Stage], corners, store: RunStore, initial: Sequence[Observation],
              kinds: frozenset[str]) -> Schedule:
    """The batch's schedule with ``kinds`` (:func:`stop_kinds`), its testbench order learned from this problem's
    observations (``Schedule.from_history``) when testbench children may stop a point: the store's, read under the
    store's lock -- the engine, which takes the lock next, gets the same rows from the store without reading the file
    again -- and ``initial``. Otherwise the spec's order, and the store is not read."""
    corner_ids = spec.corner_ids if corners == "all" else list(corners)
    children = engine.children_of(spec, pipeline, corner_ids)
    scope = spec.corner_policy.constraints
    if "testbench" not in kinds:
        return Schedule.from_history(spec, (), children, scope, stop_kinds=kinds)
    same_problem = {spec.fingerprint(), spec._legacy_fingerprint()}         # as the engine counts them ("Identity")
    with store.lock():
        rows = [o for o in store.observations() if o.spec_fingerprint in same_problem]
    return Schedule.from_history(spec, [*initial, *rows], children, scope, stop_kinds=kinds)


def _report_stopped(spec: Spec, obs, step: str, *, devices: bool = False) -> None:
    """One line per batch in which the schedule stopped points early (T17.8): how many -- and, when the points have
    device children, how many of them a device stopped (N-63, ``sim.corner.stopped_at_device``) -- and the simulations
    not run."""
    stopped = [o for o in obs if o.not_run]
    if stopped:
        at_device = f" ({sum(stopped_at_device(spec, o) for o in stopped)} at the device)" if devices else ""
        print(f"[evaluate] step={step!r}: {len(stopped)} of {len(obs)} points stopped early{at_device}, "
              f"{sum(len(o.not_run) for o in stopped)} simulations not run")


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
    the space steers the search away from it in silence (N-35, 2026-09-27: half of a first batch, the wide-band points).

    The line says to fix the expression only when every point of the batch lost the metric. When some points gave a
    value, the expression works and the others may lie where the circuit gives none (N-97, F4: a P1dB that does not
    compress at a low bias, ``no_value:nil``): the line says how many points gave no value and that the digest's "no
    value" split (where points gave no value, by variable) shows where."""
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
        if n < len(obs):
            why = next(iter(reasons)) if len(reasons) == 1 else ", ".join(f"{r} {c}" for r, c in reasons.items())
            print(f"[evaluate] step={step!r}: metric {name} gave no value on {n} of {len(obs)} points ({why}); the "
                  "digest's \"no value\" split says where")
            continue
        print(f"[evaluate] step={step!r}: metric {name} failed on {n} of {len(obs)} points "
              f"({', '.join(f'{r} {c}' for r, c in reasons.items())}); those points are metric_failed and the optimizer "
              "penalizes them -- fix the expression before spending more budget")


def default_pipeline(spec: Spec, deck: Deck | None, waveforms=()) -> list[Stage]:
    """What the spec asks for: EM devices bound into testbenches, EM devices alone, or the plain Spectre chain. Devices
    taken from a library table (T18.2B) run the library pipelines instead of the EMX ones: ``pick`` in place of ``pcell``
    and ``emx``, the rest as they are."""
    if spec.library_devices:
        from ic_opt.library.stage import library_circuit_pipeline, library_only_pipeline

        if spec.testbenches:
            if deck is None:
                raise ValueError("evaluate needs a deck for the testbenches")
            return library_circuit_pipeline(spec, deck, waveforms=list(waveforms))
        return library_only_pipeline(spec)
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
    """'<children per point> ... on <host>, N workers (<heaviest stage> threads/memory)' for the --plan lines, a
    testbench stage's threads with the extra core a testbench job takes (``(4 + 1)``: ``engine.extraction_threads``,
    N-78); refuses (EnvelopeError) a pipeline whose job does not fit the host, so the preview fails where the run would.
    ``stop_at_first_failure`` as for :func:`evaluate` (None: :func:`stop_kinds`, as the run decides it); when a kind of
    child may stop a point the count per point is a ceiling, and the line says which: the device measured first (N-63),
    then a testbench's stop (T17.8). A library pipeline (T18.2B) counts its testbench simulations only -- a library row's
    measurement is none, the budget does not count it -- and says that no EMX runs."""
    workers = engine.workers_for(spec, pipeline, parallel_jobs, limits)
    corner_ids = spec.corner_ids if corners == "all" else list(corners)
    children = engine.children_of(spec, pipeline, corner_ids)
    kinds = stop_kinds(spec, children, stop_at_first_failure)
    tb = sum(c.unit_kind == "testbench" for c in children)
    dev = sum(c.unit_kind == "device" for c in children)
    em = engine.point_runs(pipeline)
    library = any(s.name == "pick" for s in pipeline)          # T18.2B: the devices are library rows, measured, not simulated
    simulated = len(engine.counted_children(pipeline, children)) if library else len(children)
    measured = f"{dev} device measurements" + (" of library rows, no simulation" if library else "")
    parts = [f"{em} EMX runs" if em else "", f"{tb} testbench sims" if tb else "", measured if dev else ""]
    heaviest = max(pipeline, key=lambda s: (s.resources.threads + engine.extraction_threads(s), s.resources.memory_gb))
    cap = spec.em.parallel_jobs if em and spec.em is not None else None
    count = f"{simulated + em} simulations per point"
    stops = ([DEVICE_FIRST] if "device" in kinds else []) + ([TESTBENCH_STOP] if "testbench" in kinds else [])
    if stops:
        count = f"up to {count} ({'; '.join(stops)})"
    return (f"({' + '.join(p for p in parts if p)}) = {count} on {executor.host}, "
            f"{workers} workers × {per_job(heaviest.resources.threads, engine.extraction_threads(heaviest))} threads"
            + (f" / {heaviest.resources.memory_gb:g} GB" if heaviest.resources.memory_gb else "") + f" ({heaviest.name})"
            + (f", EMX at once ≤ {cap} (em.parallel_jobs)" if cap else "")
            + (", EMX results cached per geometry: a repeated geometry costs no run" if em else "")
            + (", no EMX runs (the devices are library rows)" if library else ""))
