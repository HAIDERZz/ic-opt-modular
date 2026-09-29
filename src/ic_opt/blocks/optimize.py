"""opt.suggest / opt.optimize — propose points from observations; loop suggest ⇄ evaluate.

``suggest`` is stateless: the model is rebuilt from the observations you hand
it, so continuation is "run again with a bigger budget" and warm start is
``initial=<observations from elsewhere>``. Foreign observations are re-scored
under this spec from their metrics before they are used.

Start points (T17.0b, D9): ``start`` rows are proposed first, once, before any strategy is asked; ``opt.optimize`` puts
the design as exported (the values of the circuit variables in the exported netlists) in front of them. In an 80-point
real run of 0.4.0 the best of the first 30 random points scored 0.553 while the exported circuit values scored 0.617
(2026-09-28): the design the user already has was never evaluated.

The default strategy is ``auto`` (T17.2): resolved once per call to ``metric_gp`` for a run inside its stage-1 scope (no EM
devices, one condition) and to ``openbox_gp_eic`` otherwise (``suggesters.resolve_auto``). Nothing downstream sees
``auto``: an observation's origin names the strategy that proposed it.

Advice (T17.1.5, ``ic_opt.advice``): ``advise`` records an advice in ``<project>/.icopt/advice.jsonl``; ``optimize`` reads
that file before every batch and hands its rows to ``suggest``, which applies the advice in effect at the batch's history
size: its start rows first (origin ``advice:<id>``), for every strategy; its ranges, fixed levels and ``vary`` to
``metric_gp`` only. Without the file every proposal is what it was before advice existed.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from ic_opt import advice as advice_rules
from ic_opt import objective as objective_contract
from ic_opt import space, suggesters
from ic_opt._lock import exclusive_lock
from ic_opt.blocks.evaluate import evaluate
from ic_opt.deck import Deck
from ic_opt.eval.stage import Stage
from ic_opt.executor import Executor, ExecutorError
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
    ``advice``: every row of an advice file (``ic_opt.advice``). The advice in effect at this history size (section 2 of
    the T17.1.5 specification) puts its start rows not yet evaluated after ``start``'s, origin ``advice:<id>``, for every
    strategy; ``metric_gp`` also narrows where its models' points look (they carry ``@<id>``); the others take no more.
    ``failure_penalty`` is accepted and ignored since T17.0b: no penalty number reaches a model (``suggesters.base``).
    ``strategy="auto"`` is resolved from the spec (no devices, one corner id) and the history: a history holding points
    evaluated at several corners, which ``metric_gp`` would refuse, resolves to ``openbox_gp_eic``. Nothing is printed
    (``ic-opt call opt.suggest`` prints the points as JSON); the origins name the strategy."""
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
    base = f"suggest:{suggester.name}"
    batch_tag = fill = None
    for attempt in range(4):
        missing = n - len(points)
        if missing <= 0:
            break
        proposal = suggester.propose(spec, history, missing, seed=seed + attempt, pending=[p.params for p in points],
                                     **narrowing)
        if batch_tag is None:
            batch_tag = proposal.tag          # a batch tag is the batch's: replacements share it (TuRBO replays by it)
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
    return points


def optimize(
    spec: Spec,
    executor: Executor,
    store: RunStore,
    *,
    budget: int,
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

    ``strategy="auto"`` (the default) is resolved here, once, before anything runs (``--plan`` too), and one line says to
    what and why; a strategy keyword the resolved strategy does not take is refused there. A named strategy is taken as
    named: ``metric_gp`` out of its scope is refused."""
    from ic_opt.blocks.evaluate import default_pipeline, plan_shape
    from ic_opt.recipe import PLAN_MODE

    plan = PLAN_MODE.get()
    n_corners = len(spec.corner_ids) if corners == "all" else len(list(corners))
    adopted = adopt(spec, initial)
    if strategy == suggesters.AUTO:      # the initial= rows too: _at_corners filters only the store's rows
        strategy, reason = suggesters.resolve_auto(spec, n_corners, adopted)
        suggesters.check_auto_keywords(strategy, reason, strategy_kwargs)
        print(f"{'[plan]' if plan else '[optimize]'} strategy auto: {strategy} ({reason})")
    elif strategy == "metric_gp":        # T17 stage 1 (one condition, no EM devices): refused before anything runs, --plan too
        from ic_opt.suggesters.metric_gp import stage_one_refusal

        reason = stage_one_refusal(spec, n_corners)
        if reason:
            raise ValueError(reason)
    same_problem = {spec.fingerprint(), spec._legacy_fingerprint()}     # a store stamped before T15.2 is this problem too (engine.py, "Identity")
    design = _initial_design(spec, strategy, strategy_kwargs, budget)
    if design is not None and not strategy_kwargs.get("initial_trials"):
        strategy_kwargs = {**strategy_kwargs, "initial_trials": design[0]}   # the suggester runs the design this run printed
    rows = list(start)
    if current:
        row, line = current_design(spec, _exports(spec, deck, executor, plan))
        print(f"{'[plan] opt.optimize' if plan else '[optimize]'} step={step!r}: {line}")
        rows = ([row] if row else []) + rows
    starts = space.points_from_params(spec, rows, origin="start")
    mine = Observations(o for o in store.observations() if o.spec_fingerprint in same_problem)
    history = Observations(list(adopted) + list(mine))
    done = len(mine.by_step(step))
    fresh = len({p.key for p in starts} - history.keys())
    if plan:
        shape = pipeline if pipeline is not None else default_pipeline(spec, deck or Deck(), waveforms)
        print(f"[plan] opt.optimize step={step!r} strategy={strategy}: {done}/{budget} points done, "
              f"up to {max(0, budget - done)} more in batches of {batch} × "
              f"{plan_shape(spec, shape, corners, executor, parallel_jobs, limits)} (spec budget {spec.budget.max_simulations})")
        _print_design(strategy, design, len(history), budget - done, batch, fresh, plan=True)
        handed = _at_corners(spec, mine, corners) if strategy == "metric_gp" else mine
        _announce(advice_rules.of_problem(advice_rules.read(store.root), same_problem), len(adopted) + len(handed),
                  strategy, set(), plan=True)
        return Observations()
    _print_design(strategy, design, len(history), budget - done, batch, fresh, plan=False)
    announced: set[str] = set()
    with exclusive_lock(store.root / RUN_LOCK, what="project"):      # no advice is adopted while the run goes (advise)
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
    is still a row of all of them, T17.8). ``metric_gp`` models one condition (T17 stage 1): a store that also holds the
    same problem at other corners -- the signoff recipe re-checks its best points at all of them -- would put metrics
    aggregated over different conditions into one model. A row without children (an adopted one) stays."""
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
    devices has none: its device variables have no exported value (T17 stage 1 covers circuit-only specs)."""
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
                  plan: bool) -> None:
    if design is None or new <= 0:
        return
    size, needed = design
    proposed = surrogate_points(history, new, batch, size, start=start, needed=needed)
    label, model = ("openbox", "surrogate") if strategy.startswith("openbox") else (strategy, "model")
    tag = "[plan] " if plan else "[optimize] "
    first = f" ({start} start point{'s' if start != 1 else ''} first)" if start else ""
    line = f"{tag}{label} initial design {size} points{first}: the {model} proposes {proposed} of the {new} new points"
    if proposed == 0:
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
