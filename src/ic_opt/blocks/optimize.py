"""opt.suggest / opt.optimize — propose points from observations; loop suggest ⇄ evaluate.

``suggest`` is stateless: the model is rebuilt from the observations you hand
it, so continuation is "run again with a bigger budget" and warm start is
``initial=<observations from elsewhere>``. Foreign observations are re-scored
under this spec from their metrics before they are used.

Start points (T17.0b, D9): ``start`` rows are proposed first, once, before any strategy is asked; ``opt.optimize`` puts
the design as exported (the values of the circuit variables in the exported netlists) in front of them. In an 80-point
real run of 0.4.0 the best of the first 30 random points scored 0.553 while the exported circuit values scored 0.617
(2026-09-28): the design the user already has was never evaluated.

The default strategy is ``auto`` (T17.2): resolved once per call to ``metric_gp`` for a spec without EM devices, at any
corners since T17.9, and for one whose devices come from library tables since T18.2B (no EMX in the loop), and to
``openbox_gp_eic`` otherwise (``suggesters.resolve_auto``). Nothing downstream sees ``auto``: an observation's origin names
the strategy that proposed it.

Advice (T17.1.5, ``ic_opt.advice``): ``advise`` records an advice in ``<project>/.icopt/advice.jsonl``; ``optimize`` reads
that file before every batch and hands its rows to ``suggest``, which applies the advice in effect at the batch's history
size: its start rows first (origin ``advice:<id>``), for every strategy; its ranges, fixed levels and ``vary`` to
``metric_gp`` only. Without the file every proposal is what it was before advice existed.

The initial design's size (N-96). It is decided by the first call of a step -- the one that finds no observation of this
problem in the step -- from that call's ``total`` (the budget the run is meant to reach; default its ``budget``) and
recorded in ``<project>/.icopt/steps.json``; every later call of the step keeps it. A run advanced in increments
(``budget=10 total=40``, then ``budget=20 total=40``, ...) therefore proposes the points of one call with ``budget=40``.
Until N-96 each call sized the design from its own budget, so a run continued 10 points at a time got a 5-point design
on its first call and never the 20-point design of the one-shot run, and the two diverged from the second batch on. A
store without the file (written before N-96) sizes the design from the current call's budget, as it always did.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from ic_opt import advice as advice_rules
from ic_opt import objective as objective_contract
from ic_opt import space, suggesters
from ic_opt._lock import exclusive_lock
from ic_opt.blocks.evaluate import evaluate
from ic_opt.deck import Deck
from ic_opt.eval.stage import Stage
from ic_opt.executor import Executor, ExecutorError
from ic_opt.library.query import omp_cap
from ic_opt.localpath import literal
from ic_opt.observation import Observation, Observations
from ic_opt.sim import netlist as kernel
from ic_opt.sim.ocean import WaveformExport
from ic_opt.site import HostLimits
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.store import RunStore
from ic_opt.suggesters.base import surrogate_minimum


def suggest(
    spec: Spec,
    observations: Sequence[Observation],
    n: int,
    *,
    strategy: str = "auto",
    seed: int = 0,
    initial: Sequence[Observation] = (),
    start: Sequence[dict[str, str]] = (),
    advice: Sequence[dict] = (),
    failure_penalty: float | None = None,
    **strategy_kwargs,
) -> list[Point]:
    """Propose ``n`` new grid points not already in ``observations`` or ``initial``.

    ``start``: grid parameter rows (validated like ``points.fixed``); the ones not yet evaluated come first, in order, with
    origin ``start``, and only then is the strategy asked for the rest of the batch. They count as initial design.
    ``seed`` is the run's seed, the same for every batch of a run (``opt.optimize`` passes it unchanged): it fixes the
    space-filling design, and each strategy offsets it by the history size where it needs fresh randomness. Points carry
    their provenance: ``suggest:<strategy>[:<tag>]``, per point where the strategy tags each (OpenBox ``init`` / ``acq``;
    ``fill`` for a random point that replaces one the model kept landing on evaluated points with).
    Where some variables may take only a table's combinations (T18.2A, ``space.tables``) every point is a valid one:
    ``space.snap`` projects each raw vector, and a batch still short after the random points is completed from the
    valid points not yet taken, listed (``space.valid_points``) and drawn in a seeded order, also ``fill``.
    ``advice``: every row of an advice file (``ic_opt.advice``). The advice in effect at this history size (section 2 of
    the T17.1.5 specification) puts its start rows not yet evaluated after ``start``'s, origin ``advice:<id>``, for every
    strategy; ``metric_gp`` also narrows where its models' points look (they carry ``@<id>``); the others take no more.
    ``failure_penalty`` is accepted and ignored since T17.0b: no penalty number reaches a model (``suggesters.base``).
    ``strategy="auto"`` is resolved from the spec: ``metric_gp`` unless it has EM devices simulated in the loop (devices
    from library tables it takes, T18.2B). ``metric_gp`` refuses a
    history whose points were evaluated at different sets of corners (a store holding the signoff recipe's search and
    its re-check, handed over whole): hand it one set. Nothing is printed (``ic-opt call opt.suggest`` prints the points
    as JSON); the origins name the strategy.

    Threads (N-73). The strategy's calls run with ``simulator.strategy_threads`` threads (:func:`strategy_threads`):
    every BLAS / OpenMP pool the process has loaded is limited to them for the duration of the calls
    (``threadpoolctl.threadpool_limits``, all user APIs), and ``turbo``, whose torch sizes its own pool, sets torch's
    threads to them before it fits. An OMP_NUM_THREADS, OPENBLAS_NUM_THREADS or MKL_NUM_THREADS set lower than the field
    keeps its effect: the limit is then that value. One set higher than the field is lowered to the field during the
    calls and applies again after them; torch keeps the limit after the call (it has no scoped setting), and every call
    sets it again."""
    history = Observations(list(adopt(spec, initial)) + list(observations))
    if strategy == suggesters.AUTO:
        strategy, reason = suggesters.resolve_auto(spec, len(spec.corner_ids), history)
        suggesters.check_auto_keywords(strategy, reason, strategy_kwargs)
    taken = history.keys()
    points: list[Point] = []
    current = advice_rules.in_effect(advice, len(history))
    advised = space.points_from_params(spec, current["start"], origin=f"advice:{current['id']}") if current else []
    for point in space.points_from_params(spec, start, origin="start") + advised:
        if point.key not in taken and len(points) < n:
            taken.add(point.key)
            points.append(point)
    if len(points) >= n:
        return points
    suggester = suggesters.make(strategy, **strategy_kwargs)
    narrowing = {"advice": advice} if advice and suggester.name == "metric_gp" else {}
    threads = strategy_threads(spec)
    torch_threads = {"threads": threads} if suggester.name == "turbo" else {}     # torch sizes its own pool
    base = f"suggest:{suggester.name}"
    batch_tag = fill = None
    with threadpool_limits(limits=threads):
        for attempt in range(4):
            missing = n - len(points)
            if missing <= 0:
                break
            proposal = suggester.propose(spec, history, missing, seed=seed + attempt,
                                         pending=[p.params for p in points], **narrowing, **torch_threads)
            if batch_tag is None:
                batch_tag = proposal.tag      # a batch tag is the batch's: replacements share it (TuRBO replays by it)
                fill = f"{base}:fill" if proposal.tags else f"{base}:{batch_tag}" if batch_tag else base
            for index, raw in enumerate(proposal.raw):
                tag = proposal.tags[index] if proposal.tags else batch_tag
                point = Point(space.snap(spec, raw), f"{base}:{tag}" if tag else base)
                if point.key not in taken:
                    taken.add(point.key)
                    points.append(point)
    if len(points) < n:      # the model keeps landing on evaluated grid points: fill with random ones
        filler = suggesters.RandomSuggester("random")
        for raw in filler.propose(spec, history, 4 * (n - len(points)), seed=seed + len(taken)).raw:
            point = Point(space.snap(spec, raw), fill)
            if point.key not in taken and len(points) < n:
                taken.add(point.key)
                points.append(point)
    if len(points) < n and space.tables(spec):
        # T18.2A: snapped onto a table, a random vector reaches a combination only as often as the stretch of the space
        # nearest to it, and the last free valid points may be ones no draw reaches: they are listed and drawn from.
        free = [params for params in space.valid_points(spec) if space.point_key(params) not in taken]
        for j in np.random.default_rng(seed + len(taken)).permutation(len(free))[: n - len(points)]:
            point = Point(free[j], fill)
            taken.add(point.key)
            points.append(point)
    return points


def strategy_threads(spec: Spec) -> int:
    """The threads a strategy's calls may use in :func:`suggest` (N-73): ``simulator.strategy_threads``, or, when it is
    lower, the smallest of OMP_NUM_THREADS, OPENBLAS_NUM_THREADS and MKL_NUM_THREADS that is set, read as the device
    library reads them (``library.query.omp_cap``). ``threadpool_limits`` sets a pool's size, it does not only lower it,
    so a limit above such a variable would raise the pools past it."""
    cap = omp_cap()
    return spec.simulator.strategy_threads if cap is None else min(spec.simulator.strategy_threads, cap)


def optimize(
    spec: Spec,
    executor: Executor,
    store: RunStore,
    *,
    budget: int,
    total: int | None = None,
    batch: int = 10,
    strategy: str = "auto",
    deck: Deck | None = None,
    pipeline: list[Stage] | None = None,
    corners: str | list[str] = "all",
    waveforms: list[WaveformExport] = (),
    initial: Sequence[Observation] = (),
    start: Sequence[dict[str, str]] = (),
    current: bool = True,
    seed: int = 0,
    step: str = "optimize",
    cshrc: str | None = None,
    parallel_jobs: int | None = None,
    limits: HostLimits,
    failure_penalty: float | None = None,
    **strategy_kwargs,
) -> Observations:
    """Run suggest ⇄ evaluate until this step holds ``budget`` observations. Re-running continues.

    ``current``: evaluate the design as exported first (the circuit variables' values in the exported netlists behind
    ``deck``; one line says what it is or why there is none); ``start``: further grid parameter rows to evaluate first.
    Neither is evaluated again once in the history. ``limits`` is the executor host's site.yaml entry (``run.limits``),
    passed on to every ``sim.evaluate``. ``failure_penalty`` is ignored (see ``suggest``).

    ``total``: the budget the run is meant to reach when it is advanced in increments (default ``budget``; below it is
    refused). The initial design of ``metric_gp`` and ``openbox_*`` is sized from it by the step's first call and recorded
    in ``.icopt/steps.json`` (``{step: {"initial_design": n, "budget": total}}``); later calls of the step keep the
    recorded size (N-96, module docstring). ``initial_trials`` given is used and recorded. Not in any fingerprint.

    ``strategy="auto"`` (the default) is resolved here, once, before anything runs (``--plan`` too), and one line says to
    what and why; a strategy keyword the resolved strategy does not take is refused there. A named strategy is taken as
    named: ``metric_gp`` on a spec with EM devices simulated in the loop is refused. ``metric_gp`` is handed the rows
    evaluated at this run's corners, of the store and of ``initial`` alike (``_at_corners``)."""
    from ic_opt.blocks.evaluate import default_pipeline, plan_identity, plan_shape
    from ic_opt.recipe import PLAN_MODE

    plan = PLAN_MODE.get()
    if total is not None and int(total) < budget:
        raise ValueError(f"total={total} is below budget={budget}: total is the budget the run is meant to reach, "
                         "at least this call's")
    sized_for = budget if total is None else int(total)
    n_corners = len(spec.corner_ids) if corners == "all" else len(list(corners))
    adopted = adopt(spec, initial)
    if strategy == suggesters.AUTO:
        strategy, reason = suggesters.resolve_auto(spec, n_corners, adopted)
        suggesters.check_auto_keywords(strategy, reason, strategy_kwargs)
        print(f"{'[plan]' if plan else '[optimize]'} strategy auto: {strategy} ({reason})")
    elif strategy == "metric_gp":        # no EMX device (any corners since T17.9, library devices since T18.2B): refused
                                         # before anything runs, --plan too
        from ic_opt.suggesters.metric_gp import stage_one_refusal

        reason = stage_one_refusal(spec, n_corners)
        if reason:
            raise ValueError(reason)
    if strategy == "metric_gp":          # its rows share one set of corners, the initial= ones as the store's (T17.9)
        initial = _at_corners(spec, Observations(initial), corners)
        adopted = adopt(spec, initial)
    same_problem = {spec.fingerprint(), spec._legacy_fingerprint()}     # a store stamped before T15.2 is this problem too (engine.py, "Identity")
    mine = Observations(o for o in store.observations() if o.spec_fingerprint in same_problem)
    done = len(mine.by_step(step))
    given = strategy_kwargs.get("initial_trials")
    recorded = _recorded_design(store.root, step) if done and not given else None     # N-96: a later call keeps it
    design = _initial_design(spec, strategy, {**strategy_kwargs, "initial_trials": recorded["initial_design"]}
                             if recorded else strategy_kwargs, sized_for)
    if design is not None and not given:
        strategy_kwargs = {**strategy_kwargs, "initial_trials": design[0]}   # the suggester runs the design this run printed
    note = ("" if design is None or given else
            f"recorded by this step's first call, budget {recorded['budget']}" if recorded else
            f"sized for a budget of {sized_for}" if total is not None else "")
    rows = list(start)
    if current:
        row, line = current_design(spec, _exports(spec, deck, executor, plan))
        print(f"{'[plan] opt.optimize' if plan else '[optimize]'} step={step!r}: {line}")
        rows = ([row] if row else []) + rows
    starts = space.points_from_params(spec, rows, origin="start")
    history = Observations(list(adopted) + list(mine))
    fresh = len({p.key for p in starts} - history.keys())
    if plan:
        shape = pipeline if pipeline is not None else default_pipeline(spec, deck or Deck(), waveforms)
        print(f"[plan] opt.optimize step={step!r} strategy={strategy}: {done}/{budget} points done, "
              f"up to {max(0, budget - done)} more in batches of {batch} × "
              f"{plan_shape(spec, shape, corners, executor, parallel_jobs, limits)} (spec budget {spec.budget.max_simulations})")
        for line in plan_identity(spec, shape, store, executor):         # the deck, and the store's history (N-99)
            print(f"[plan] opt.optimize step={step!r}: {line}")
        _print_design(strategy, design, len(history), budget - done, batch, fresh, plan=True, note=note,
                      total=sized_for - done)
        handed = _at_corners(spec, mine, corners) if strategy == "metric_gp" else mine
        _announce(advice_rules.of_problem(advice_rules.read(store.root), same_problem), len(adopted) + len(handed),
                  strategy, set(), plan=True)
        return Observations()
    _print_design(strategy, design, len(history), budget - done, batch, fresh, plan=False, note=note,
                  total=sized_for - done)
    announced: set[str] = set()
    with exclusive_lock(store.root / RUN_LOCK, what="project"):      # no advice is adopted while the run goes (advise)
        if design is not None and (not done or given):     # the step's first call, or a size stated: recorded (N-96)
            _record_design(store.root, step, design[0], sized_for)
        while True:
            mine = Observations(o for o in store.observations() if o.spec_fingerprint in same_problem)
            done = len(mine.by_step(step))
            if done >= budget:
                break
            handed = _at_corners(spec, mine, corners) if strategy == "metric_gp" else mine
            # Read before every batch: a recipe of several steps sees an advice its own code adopted between them.
            advice = advice_rules.of_problem(advice_rules.read(store.root), same_problem)
            _announce(advice, len(adopted) + len(handed), strategy, announced, plan=False)
            points = suggest(
                spec, handed, min(batch, budget - done), strategy=strategy, seed=seed, initial=initial,
                start=[p.params for p in starts], advice=advice, **strategy_kwargs,
            )
            if not points:
                break
            evaluate(
                spec, points, executor, store, deck=deck, pipeline=pipeline, corners=corners, waveforms=waveforms,
                step=step, cshrc=cshrc, parallel_jobs=parallel_jobs, limits=limits, initial=adopted,   # the schedule learns from them too
            )
    return Observations(o for o in store.observations() if o.spec_fingerprint in same_problem and o.step == step)


RUN_LOCK = "run.lock"      # held by opt.optimize for its whole loop; the store's own lock is held per batch (sim.evaluate)
STEPS = "steps.json"       # per step, the initial design its first call sized (N-96): {step: {initial_design, budget}}


def _recorded_design(root: Path, step: str) -> dict | None:
    """The entry ``.icopt/steps.json`` holds for ``step``: ``{"initial_design": n, "budget": b}``, the size the step's
    first call recorded and the budget it was sized for; None without the file or the entry (a store written before
    N-96: the size is then the current call's)."""
    path = root / STEPS
    if not path.is_file():
        return None
    entry = json.loads(path.read_text(encoding="utf-8")).get(step)
    return entry if isinstance(entry, dict) and entry.get("initial_design") else None


def _record_design(root: Path, step: str, size: int, budget: int) -> None:
    """Write ``step``'s entry of ``.icopt/steps.json`` (the other steps' entries kept), replacing the file at once."""
    path = root / STEPS
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data[step] = {"initial_design": int(size), "budget": int(budget)}
    temporary = path.with_name(STEPS + ".tmp")
    temporary.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _announce(advice: Sequence[dict], k: int, strategy: str, announced: set[str], *, plan: bool) -> None:
    """One line when an advice comes into effect for this run's next batch, once per advice: what it asks for, and, for a
    strategy other than ``metric_gp``, which of its parts are not used and why."""
    current = advice_rules.in_effect(advice, k)
    if current is None or current["id"] in announced:
        return
    announced.add(current["id"])
    tag = "[plan] opt.optimize" if plan else "[optimize]"
    print(f"{tag} advice {current['id']} in effect (since {current['since']}, by {current['author']}): "
          f"{advice_rules.describe(current)}")
    if advice_rules.narrows(current) and strategy != "metric_gp":
        unused = [part for part in advice_rules.NARROWING if current[part]]
        print(f"{tag} advice {current['id']}: strategy {strategy} takes its start rows only; its {', '.join(unused)} "
              "are not used (narrowing the search is metric_gp's)")


def _at_corners(spec: Spec, rows: Observations, corners: str | list[str]) -> Observations:
    """The rows evaluated at exactly this run's corners (``Observation.corners``: a re-check stopped at its first corner
    is still a row of all of them, T17.8). ``metric_gp`` models the points of one set of corners, each metric at its
    worst over them (T17.9): a store that also holds the same problem at other corners -- the signoff recipe searches at
    one and re-checks its best points at all of them -- would put one corner's values and the worst of several into one
    model. A row without children stays."""
    wanted = {c or "nominal" for c in (spec.corner_ids if corners == "all" else corners)}
    return Observations(o for o in rows if not o.corners() or o.corners() == wanted)


def history_size(spec: Spec, rows: Sequence[Observation], corners: str | list[str] | None = None) -> int:
    """How many observations the strategy of this problem's next batch is handed -- an advice's ``since``, compared with
    the history size a batch is proposed at: the rows of this problem (the spec's fingerprints, as ``optimize`` counts
    them), and with ``corners`` given the ones evaluated at exactly those corners (``_at_corners``, what ``metric_gp`` is
    handed). Without ``corners`` the rows must all have been evaluated at one set of corners; a store that holds this
    problem at several (the signoff recipe's search corner and its all-corner re-check) is refused, naming them: which
    rows the next batch sees depends on the run, and a ``since`` above it would hold the advice back."""
    same_problem = {spec.fingerprint(), spec._legacy_fingerprint()}
    mine = Observations(o for o in rows if o.spec_fingerprint in same_problem)
    if corners is not None:
        return len(_at_corners(spec, mine, corners))
    held: dict[tuple[str, ...], int] = {}
    for o in mine:
        if corners_of := o.corners():                          # a point stopped early counts at every corner it was to run at
            key = tuple(sorted(corners_of))
            held[key] = held.get(key, 0) + 1
    if len(held) > 1:
        raise ValueError("this problem's observations were evaluated at several sets of corners ("
                         + "; ".join(f"{', '.join(dict.fromkeys(key))}: {count}" for key, count in held.items())
                         + "); say which run the advice is for with --corners (e.g. --corners tt for the signoff "
                         "recipe's search)")
    return len(mine)


def advise(spec: Spec, store: RunStore, advice: dict, *, corners: str | list[str] | None = None) -> dict:
    """Adopt an advice for this problem (``ic_opt.advice``, T17.1.5 specification): ``advice`` holds ``author``,
    ``reason`` and any of ``start`` (complete grid parameter rows to evaluate first), ``ranges`` (``{variable: [lower,
    upper]}`` inside the spec's range), ``fixed`` (``{variable: value}``), ``vary`` (the variables that may move; the
    others stay at the search region's centre). Checked against the spec -- refused with what to change, values between
    levels moved onto the grid and said so -- and appended to ``<project>/.icopt/advice.jsonl`` with the next id and
    ``since`` (:func:`history_size`; ``corners`` as there). It ends the advice before it. Takes the project's run lock and
    its store lock: not while a run goes. Prints what it adopted; returns the recorded row.

    It is in effect from the next batch: its start rows come first (every strategy); its ranges, fixed levels and
    ``vary`` narrow four fifths of each ``metric_gp`` batch after the initial design, the other fifth looks over the
    spec's whole range. An advice never widens the spec's ranges and never moves the initial design's points."""
    same_problem = {spec.fingerprint(), spec._legacy_fingerprint()}
    with exclusive_lock(store.root / RUN_LOCK, what="project"), store.lock():
        rows = advice_rules.read(store.root)
        since = history_size(spec, store.observations(), corners)
        row, notes = advice_rules.adoption(spec, advice, rows, since)
        before = advice_rules.in_effect(advice_rules.of_problem(rows, same_problem), since)
        advice_rules.append(store.root, row)
    for note in notes:
        print(f"[advise] {note}")
    print(f"[advise] adopted {row['id']} (since {since}; in effect from the next batch"
          + (f"; ends {before['id']}" if before else "") + f"): {advice_rules.describe(row)}")
    return row


def revoke_advice(spec: Spec, store: RunStore, advice_id: str, reason: str, *,
                  corners: str | list[str] | None = None) -> dict:
    """End advice ``advice_id`` (the one adopted last) from the next batch on: appends a ``revoke`` row with ``since``
    (:func:`history_size`) and ``reason``. Takes the locks ``advise`` takes. Returns the row."""
    same_problem = {spec.fingerprint(), spec._legacy_fingerprint()}
    with exclusive_lock(store.root / RUN_LOCK, what="project"), store.lock():
        rows = advice_rules.of_problem(advice_rules.read(store.root), same_problem)
        row = advice_rules.revocation(rows, advice_id, reason, history_size(spec, store.observations(), corners))
        advice_rules.append(store.root, row)
    print(f"[advise] revoked {advice_id} (since {row['since']}): {row['reason']}")
    return row


_SI = {"T": 12, "G": 9, "M": 6, "K": 3, "k": 3, "": 0, "m": -3, "u": -6, "n": -9, "p": -12, "f": -15, "a": -18}


def current_design(spec: Spec, exports: dict[str, str] | str) -> tuple[dict[str, str] | None, str]:
    """The design as exported: ``exports`` maps each testbench to its exported netlist text (or is the reason there is
    none). Returns the grid parameter row, or None, and the line that says which, or why not: every testbench must give
    a variable the same value; a value inside the variable's range but between grid points is moved to the nearest one
    (and the line says so); a value outside the range, or that is not a number, means no current design. A spec with EM
    devices has none: its device variables have no exported value (T17 stage 1 covers circuit-only specs); nor has one
    whose devices are library rows (T18.2B), and its line says so (N-97, F6)."""
    if spec.library_devices:
        return None, ("no current design: the spec's devices come from a library table (their variables have no exported "
                      "value)")
    if spec.devices:
        return None, ("no current design: the spec has EM devices, whose variables have no exported value "
                      "(stage 1 of T17 covers circuit-only specs)")
    if isinstance(exports, str):
        return None, f"no current design: {exports}"
    seen: dict[str, dict[str, str]] = {}
    for tb, text in exports.items():
        try:
            values = kernel.exported_values(text, spec.circuit_variables)
        except ValueError as exc:
            return None, f"no current design: {tb}'s export: {exc}"
        for name, value in values.items():
            seen.setdefault(name, {})[tb] = value
    row, moved, problems = {}, [], []
    disagree = {name: by_tb for name, by_tb in seen.items()          # 600n and 0.6u agree
                if len({v if _number(spec, name, v) is None else _number(spec, name, v) for v in by_tb.values()}) > 1}
    if disagree:
        return None, "no current design: the testbenches disagree on " + "; ".join(
            f"{name} ({', '.join(f'{tb} {v}' for tb, v in by_tb.items())})" for name, by_tb in disagree.items())
    for v in spec.variables:
        text = next(iter(seen[v.name].values()))
        lower, unit = space.parse_scalar(v.lower)
        upper, step = space.parse_scalar(v.upper)[0], space.parse_scalar(v.step)[0]
        value = _number(spec, v.name, text)
        if value is None:
            problems.append(f"{v.name}={text} is not a number in the unit {unit or '(none)'} of its range")
        elif not lower <= value <= upper:
            problems.append(f"{v.name}={text} is outside its range [{v.lower}, {v.upper}]")
        else:
            snapped = space.format_value(lower + round((value - lower) / step) * step, unit)
            row[v.name] = snapped
            if (value - lower) % step:
                moved.append(f"{v.name}={text} is between grid points: moved to {snapped}")
    if problems:
        return None, "no current design: " + "; ".join(problems)
    line = "current design (the exported netlists) first: " + " ".join(f"{k}={row[k]}" for k in row)
    return row, line + ("; " + "; ".join(moved) if moved else "")


def _number(spec: Spec, name: str, text: str) -> Decimal | None:
    """``text`` as a number in the unit suffix of the variable's range: an export may write ``600n`` where the spec
    says ``0.6u`` (ADE writes a design variable as the user typed it); None for an expression or an unknown suffix."""
    variable = next(v for v in spec.variables if v.name == name)
    unit = space.parse_scalar(variable.lower)[1]
    try:
        value, suffix = space.parse_scalar(text)
    except ValueError:
        return None
    if suffix == unit:
        return value
    if suffix not in _SI or unit not in _SI:
        return None
    return value.scaleb(_SI[suffix] - _SI[unit])


def _exports(spec: Spec, deck: Deck | None, executor: Executor, plan: bool) -> dict[str, str] | str:
    """Each testbench's exported netlist text: the deck's copy of the export (``Deck.bundle``), or under ``--plan``, where
    ``netlist.import`` returns an empty deck, the export fetched afresh through the executor (nothing reaches the store).
    A string says why there is none."""
    if spec.devices:
        return {}
    if not plan:
        texts = {}
        for tb in spec.testbenches:
            bundle = (deck or Deck()).bundle(tb.id)
            if bundle is None or not (bundle / "input.scs").is_file():
                return f"the deck carries no export of testbench {tb.id}"
            texts[tb.id] = (bundle / "input.scs").read_text(encoding="utf-8")
        return texts
    tmp = Path(tempfile.mkdtemp(prefix="ic_opt_plan_current_"))
    try:
        texts = {}
        for tb in spec.testbenches:
            local = tmp / tb.id / "input.scs"
            try:
                executor.get(f"{tb.maestro_point_root}/netlist/input.scs", local)
                texts[tb.id] = local.read_text(encoding="utf-8")
            except (OSError, ExecutorError) as exc:
                return f"{tb.id}'s export could not be read: {exc}"
        return texts
    finally:
        shutil.rmtree(literal(tmp))


def _initial_design(spec: Spec, strategy: str, strategy_kwargs: dict, budget: int) -> tuple[int, int] | None:
    """The initial design of a strategy whose design ic-opt serves: (its size, the successful points the model needs
    before it completes a batch). OpenBox: ``initial_trials`` when given, else min(2 x variables, budget // 2), at least
    one (``suggesters.openbox.initial_design_size``), and the surrogate minimum; metric_gp: ``initial_trials``, else
    min(max(2 x active variables, 8), 20, budget // 2), and one scored point (until then its design goes on). None for
    the others."""
    if strategy.startswith("openbox"):
        from ic_opt.suggesters.openbox import initial_design_size

        return initial_design_size(spec, strategy_kwargs.get("initial_trials"), budget), surrogate_minimum(spec)
    if strategy == "metric_gp":
        from ic_opt.suggesters import metric_gp

        return metric_gp.initial_design_size(spec, strategy_kwargs.get("initial_trials"), budget), 1
    return None


def surrogate_points(history: int, new: int, batch: int, design: int, *, start: int = 0, needed: int = 2) -> int:
    """How many of the ``new`` points (in batches of ``batch``) the surrogate proposes when the history holds ``history``
    observations, every point succeeding: the first ``design`` observations are the ``start`` points not yet evaluated
    followed by space-filling points (start points beyond ``design`` come first all the same), and the rest of a batch
    that reaches the end of the design is the surrogate's (T17.0b) when the history at the batch's start holds ``needed``
    successful points (``suggesters.base.surrogate_minimum``), else further space filling. Until 0.4.0 a batch that began
    inside the design was design in full: the 2026-09-27 real-scenario acceptance (N-27, ISSUE-8) ran 12 points in
    batches of 6 on 4 variables and never reached the surrogate; a plan saying "initial design 22" ran 30 (2026-09-28)."""
    proposed, done, left = 0, history, new
    while left > 0:
        size = min(batch, left)
        starts = min(size, start)
        first = starts + min(size - starts, max(0, design - done - starts))
        if done >= needed:
            proposed += size - first
        start, done, left = start - starts, done + size, left - size
    return proposed


def _print_design(strategy: str, design: tuple[int, int] | None, history: int, new: int, batch: int, start: int, *,
                  plan: bool, note: str = "", total: int | None = None) -> None:
    """The design line. ``note`` says where the size comes from when not from this call's budget (N-96); ``total``: the
    new points up to the run's ``total`` (default ``new``). A call whose model proposes none of its points is a plain
    line when its new points are all start points, or when the model proposes some of the points up to ``total`` (the
    run goes on past the design: nothing to propose yet is no fault); the WARNING stays for a run that ends without one."""
    if design is None or new <= 0:
        return
    size, needed = design
    proposed = surrogate_points(history, new, batch, size, start=start, needed=needed)
    label, model = ("openbox", "surrogate") if strategy.startswith("openbox") else (strategy, "model")
    tag = "[plan] " if plan else "[optimize] "
    said = "; ".join(([note] if note else []) + ([f"{start} start point{'s' if start != 1 else ''} first"] if start else []))
    line = (f"{tag}{label} initial design {size} points{f' ({said})' if said else ''}: the {model} proposes {proposed} of "
            f"the {new} new points")
    total = new if total is None else total
    later = surrogate_points(history, total, batch, size, start=start, needed=needed) if total > new else 0
    if proposed == 0 and start >= new:
        line += " (all of them start points)"
    elif proposed == 0 and later:
        line += f" (none yet: it proposes {later} of the {total} points left to the run's total)"
    elif proposed == 0:
        before = (f" (the {model} needs {needed} successful point{'s' if needed != 1 else ''} before a batch starts)"
                  if needed else "")
        line += (f" -- WARNING: none; this run is initial design throughout{before}. Raise budget"
                 + (", use a smaller batch," if needed else "") + " or pass a smaller initial_trials")
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
