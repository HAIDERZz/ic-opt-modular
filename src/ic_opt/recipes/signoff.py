"""Built-in recipe: signoff — optimize at one corner, then re-check the top-k across all corners (a point stops at its
first failing corner unless ``full``); with ``rounds`` above 1, a constraint the re-check missed is tightened by the miss
and the search goes again (T18.4, ``docs/refactor/T18_4_TIGHTEN_RECIPE_SPEC.md``)."""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from ic_opt import blocks as b
from ic_opt import space
from ic_opt.digest import SI_UNITS
from ic_opt.observation import Observation, Observations
from ic_opt.recipe import Run
from ic_opt.sim.corner import metrics_per_corner, scored_corners
from ic_opt.spec import Constraint, Spec

ROUNDS_FILE = "signoff_rounds.json"         # in .icopt/reports/: every round's tightening, written when rounds > 1


def main(run: Run, *, corner: str = "tt", budget: int = 60, batch: int = 10, top: int = 5, strategy: str = "auto", seed: int = 0,
         current: bool = True, start: str | None = None, full: bool = False, rounds: int = 1, tighten: float = 1.0) -> None:
    """``current`` / ``start`` as for ``optimize``, for the search step at ``corner``. ``strategy``: ``auto`` searches
    with ``metric_gp`` (one corner) unless the spec has EM devices (then ``openbox_gp_eic``). Unless
    ``simulator.stop_at_first_failure`` says otherwise, a point of the search, at one corner, runs every simulation --
    but one whose EM or library device fails a constraint runs none, its device measured first (N-63,
    ``blocks.evaluate.stop_kinds``) -- and a re-checked point stops at its first failing corner whatever the number of
    corners (:func:`_recheck_stop`; T17.8: on the multi-corner benchmark 550 to 1340 of 3100 simulations per run went to
    points already known to fail. The re-check verifies, it teaches no model, so the stop's only effect is the saving --
    the default of ``blocks.evaluate.stop_wanted``, made for a search, does not apply here); ``full=True`` simulates every
    corner of every re-checked point, for a complete per-corner table, whatever its devices measured.

    Rounds (T18.4). ``rounds=1`` (the default) is the recipe above, as it was. With ``rounds`` above 1, after each
    re-check -- every round's re-check is under the spec as written, the user's constraints:

    1. The best re-checked point: the feasible one with the best objective -- if there is one, the recipe stops: done.
       Otherwise the one with the smallest ``constraint_penalty`` (the aggregate's, over its corners) among the points
       whose constraints were judged (``constraint_failed``); a point that gave no value (a failed simulation, a lost
       metric) carries a penalty of 0 that measured nothing and comes after them; the first of equal ones.
    2. Its *miss* of each constraint: how far the constraint's own worst value at that point, over the corners the
       constraint is judged at (``sim.corner.scored_corners``; the largest for an upper limit, the smallest for a lower
       one -- what ``sim.corner.worst_metrics`` gives a metric bounded on one side; for a metric bounded on both sides
       that function gives one value, the one nearest either bound, and each bound here takes its own), lies outside the
       limit as written, in the metric's unit. A re-checked point that stopped at its first failing corner is measured on
       the corners it ran (``full=true`` runs them all). Each missed constraint is *tightened* by ``tighten × miss``, an
       upper limit down and a lower limit up, from the limit the round's search ran under: the moves add up over the
       rounds (a round whose search ran under a limit already moved and still missed moves it further), and a constraint
       the best point did not miss keeps the limit it has. One line per tightened constraint says the limit as written,
       the miss, the point and corner, and the limit the next round searches under. A constraint whose metric has no
       value at that point (a failed simulation, or one the re-check did not run) cannot be tightened: a line says so,
       and it is left as it is. ``tighten`` is a number above 0: 1.0 moves by exactly the miss.
    3. The next round searches at ``corner`` again (step ``search@<corner>#<round>``) with ``budget`` more points on a
       copy of the spec (``model_copy``) whose missed constraints carry the tightened limits, written in the
       constraint's own form (``9 dB`` -> ``8.7 dB``, ``28e9 Hz`` -> ``28.5e9 Hz``), without the design as exported
       (``current=False``) or the ``start`` rows, handed every search row of the earlier rounds as ``initial=``
       (``opt.optimize`` re-scores them under the tightened constraints; all are at ``corner``, so ``metric_gp`` takes
       them). Its top ``top`` points are re-checked at every corner under the spec as written (step ``signoff#<round>``).
    4. The rounds end when a re-checked point is feasible at every corner or ``rounds`` searches were made, and earlier
       when a round cannot go on: its search found no point feasible under its constraints (nothing to re-check), its
       best point misses no constraint that has a value there (nothing to tighten), or the tightened limits of a metric
       bounded on both sides would cross (no point could meet them). The report covers the last re-check made.
       ``.icopt/reports/signoff_rounds.json`` holds every round -- its steps, the fingerprint and constraints its search
       ran under, the best re-checked point and its verdict, each miss with the numbers above and the next limit -- and
       why the rounds ended; it is written after every round.

    The spec on disk is never rewritten. What the rounds leave in the store: a tightened spec is another problem, so
    its search rows carry its own fingerprint beside the user's in the same observations file, and ``initial=`` is how
    the next round sees the earlier rows. ``ic-opt digest`` reads the problem as written: the first round's search and
    every round's re-check. The later rounds' search rows are the steps ``search@<corner>#2``, ``#3``, ...: a reader of
    ``observations.jsonl`` finds them by step name, and ``signoff_rounds.json`` ties each to its constraints. A block
    handed every row of the store (``ic-opt call analyze.report PROJECT``) gets them too, with verdicts under their
    round's tightened constraints. An advice (``ic-opt advise``) is the problem's as written, so it applies to the first
    round's search only. The budget counts every row. Running the recipe again continues it: the same misses give the
    same tightened spec, so each round's search keeps its points; a re-checked point that failed is simulated again (the
    engine reuses only feasible rows, as for the first round's re-check before T18.4); a larger ``rounds`` adds rounds.
    ``--plan`` prints each round's search and re-check as the first round's print theirs (a later round under the
    constraints as written: nothing is tightened) and changes nothing.

    With ``rounds`` above 1 a constraint whose limit the recipe cannot write back in its own form is refused before
    anything runs, ``--plan`` too: a value that is not a number followed by a unit, and one whose unit is an SI prefix on
    a unit (``50m V``: a constraint's value takes no prefix, and reads as 50 V) other than the metric's own."""
    _check_rounds(rounds, tighten)
    rows = json.loads(Path(run.project, start).read_text(encoding="utf-8")) if start else []
    if rounds > 1:
        _refuse_unwritable_limits(run.spec, rounds)
    b.doctor(run.spec, run.executor, cshrc=run.cshrc, store=run.store, limits=run.limits).require_pass()
    deck = b.import_netlists(run.spec, run.executor, run.store)
    if rounds > 1:
        run.note(f"signoff round 1 of {rounds}: the search at {corner} (step {_search_step(corner, 1)}), then the re-check "
                 f"of its top {top} at every corner (step {_check_step(1)})")
    search = b.optimize(run.spec, run.executor, run.store, deck=deck, strategy=strategy, budget=budget, batch=batch, seed=seed,
                        corners=[corner], step=f"search@{corner}", current=current, start=rows, cshrc=run.cshrc,
                        parallel_jobs=run.jobs, limits=run.limits)
    winners = b.points_from(b.best(run.spec, search, top))
    signoff = b.evaluate(run.spec, winners, run.executor, run.store, deck=deck, corners="all", step="signoff",
                         cshrc=run.cshrc, parallel_jobs=run.jobs, limits=run.limits,
                         stop_at_first_failure=_recheck_stop(run.spec, full))
    title = f"{run.spec.project} — sign-off across all corners"
    if rounds > 1:
        later = _Rounds(run, deck, corner=corner, budget=budget, batch=batch, top=top, strategy=strategy, seed=seed, full=full,
                        rounds=rounds, tighten=tighten)
        signoff, last = later.go(search, signoff)
        title += f" (round {last})" if last > 1 else ""
    if signoff:
        run.note(f"report: {b.report(run.spec, signoff, run.store, title=title)}")


def _recheck_stop(spec, full: bool) -> bool:
    """The re-check's stop: off under ``full``, else the spec's switch when set, else on."""
    if full:
        return False
    return True if spec.simulator.stop_at_first_failure is None else spec.simulator.stop_at_first_failure


# -- rounds (T18.4) ------------------------------------------------------------------------------------------------------


def _check_rounds(rounds: object, tighten: object) -> None:
    """``rounds`` a whole number of at least 1, ``tighten`` a finite number above 0: refused before anything runs."""
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
        raise ValueError(f"signoff: rounds must be a whole number of at least 1, got {rounds!r}")
    if isinstance(tighten, bool) or not isinstance(tighten, int | float) or not math.isfinite(tighten) or tighten <= 0:
        raise ValueError(f"signoff: tighten must be a number above 0 (a missed constraint moves by tighten × its miss), "
                         f"got {tighten!r}")


def _search_step(corner: str, k: int) -> str:
    return f"search@{corner}" if k == 1 else f"search@{corner}#{k}"


def _check_step(k: int) -> str:
    return "signoff" if k == 1 else f"signoff#{k}"


@dataclass
class _Move:
    """A constraint the best re-checked point misses (``index`` in the spec's constraints): its own worst value there and
    the corner of it, the miss (in the metric's unit, from the limit as written), the move (``tighten × miss``), the limit
    the round's search ran under and the one the next round searches under (None while no round follows), in the form of
    the constraint's value (``form``: :func:`_form`)."""

    index: int
    constraint: Constraint          # as written
    form: tuple[str | None, str]
    searched: str
    value: Decimal
    corner: str | None
    miss: Decimal
    move: Decimal
    next: str | None = None

    def text(self, number: Decimal) -> str:
        return _write(self.form, number)

    def record(self) -> dict:
        c = self.constraint
        return {"metric": c.metric, "op": str(c.op), "limit": c.value, "searched": self.searched, "value": float(self.value),
                "corner": self.corner, "miss": float(self.miss), "move": float(self.move), "next": self.next}


class _Rounds:
    """Rounds 2 to ``rounds`` of :func:`main` (its docstring, "Rounds"), after the first round's search and re-check."""

    def __init__(self, run: Run, deck, *, corner: str, budget: int, batch: int, top: int, strategy: str, seed: int, full: bool,
                 rounds: int, tighten: float) -> None:
        self.run, self.deck = run, deck
        self.corner, self.budget, self.batch, self.top = corner, budget, batch, top
        self.strategy, self.seed, self.full = strategy, seed, full
        self.rounds, self.tighten = rounds, tighten
        self.file = {"corner": corner, "rounds_allowed": rounds, "tighten": float(tighten), "top": top,
                     "spec_fingerprint": run.spec.fingerprint(), "outcome": None, "why": None, "report_round": None,
                     "rounds": []}

    def go(self, search: Observations, check: Observations) -> tuple[Observations, int]:
        """Run the rounds after the first; the re-check the report covers and its round (the last re-check made)."""
        if self.run.plan:
            self._plan()
            return check, 1
        run, searched, history = self.run, self.run.spec, Observations(search)
        reported, last, k = check, 1, 1
        while True:
            entry = {"round": k,
                     "search": {"step": _search_step(self.corner, k), "spec_fingerprint": searched.fingerprint(),
                                "constraints": [{"metric": c.metric, "op": str(c.op), "value": c.value}
                                                for c in searched.constraints],
                                "points": len(search)},
                     "recheck": {"step": _check_step(k), "points": len(check)},
                     "best": None, "misses": [], "no_value": []}
            self.file["rounds"].append(entry)
            if not check:
                self._end(k, "nothing_to_recheck", f"no point of step {_search_step(self.corner, k)} is feasible under the "
                                                   "constraints it searched under: nothing to re-check")
                break
            best = _best_rechecked(check)
            entry["best"] = _verdict(best)
            if best.feasible:
                objective = f" (objective {best.fom:g})" if best.fom is not None else ""
                self._end(k, "feasible", f"{best.obs_id} is feasible at every corner{objective}: done")
                break
            moves, no_value = self._misses(searched, best)
            entry["misses"] = [m.record() for m in moves]
            entry["no_value"] = [{"metric": c.metric, "op": str(c.op), "limit": c.value} for c in no_value]
            if k == self.rounds:
                missed = "; ".join(f"{m.constraint.metric} {m.constraint.op} {m.constraint.value} by {m.text(m.miss)}"
                                   for m in moves) or "no constraint that has a value there"
                self._end(k, "rounds_used", f"the {self.rounds} rounds are made and no re-checked point is feasible at every "
                                            f"corner; the best of round {k}, {best.obs_id} ({best.status}), misses {missed}")
                break
            for c in no_value:
                run.note(f"signoff round {k}: {c.metric} {c.op} {c.value} cannot be tightened: {c.metric} has no value at "
                         f"{best.obs_id} (a failed simulation, or one the re-check did not run); left as it is")
            if not moves:
                self._end(k, "nothing_to_tighten", f"nothing to tighten: the best re-checked point, {best.obs_id} "
                                                   f"({best.status}), misses no constraint that has a value there")
                break
            tightened = _tightened(searched, moves)
            crossed = _crossed(tightened)
            if crossed:
                self._end(k, "limits_cross", f"the tightened limits would cross ({crossed}): no point can meet them")
                break
            for m in moves:
                c, at = m.constraint, f"{best.obs_id} at {m.corner}" if m.corner else best.obs_id
                m.next = tightened.constraints[m.index].value
                was = f" (round {k} searched under {m.searched})" if m.searched != c.value else ""
                run.note(f"signoff round {k}: {c.metric} {c.op} {c.value} missed by {m.text(m.miss)} ({at}: "
                         f"{m.text(m.value)}); round {k + 1} searches under {c.metric} {c.op} {m.next}{was}")
            entry["misses"] = [m.record() for m in moves]
            self._write()
            k, searched = k + 1, tightened
            run.note(f"signoff round {k} of {self.rounds}: the search at {self.corner} under the tightened constraints (step "
                     f"{_search_step(self.corner, k)}, spec {searched.fingerprint()}), then the re-check of its top {self.top} "
                     f"at every corner under the constraints as written (step {_check_step(k)})")
            search = self._search(searched, k, history)
            history = Observations([*history, *search])
            check = self._recheck(b.points_from(b.best(searched, search, self.top)), k)
            if check:
                reported, last = check, k
        self.file["report_round"] = last if reported else None
        self._write()
        return reported, last

    def _plan(self) -> None:
        """The later rounds' shape under ``--plan``: each round's search and re-check lines, under the constraints as
        written (nothing is tightened, nothing is written)."""
        for k in range(2, self.rounds + 1):
            self.run.note(f"signoff round {k} of {self.rounds}, only if no point re-checked in round {k - 1} is feasible at "
                          f"every corner: the search at {self.corner} again (step {_search_step(self.corner, k)}) under the "
                          f"constraints that round's best point missed, each moved by {self.tighten:g} × its miss, then the "
                          f"re-check of its top {self.top} at every corner under the constraints as written "
                          f"(step {_check_step(k)})")
            self._search(self.run.spec, k, Observations())
            self._recheck([], k)

    def _search(self, spec: Spec, k: int, history: Observations) -> Observations:
        run = self.run
        return b.optimize(spec, run.executor, run.store, deck=self.deck, strategy=self.strategy, budget=self.budget,
                          batch=self.batch, seed=self.seed, corners=[self.corner], step=_search_step(self.corner, k),
                          current=False, initial=history, cshrc=run.cshrc, parallel_jobs=run.jobs, limits=run.limits)

    def _recheck(self, points, k: int) -> Observations:
        run = self.run
        return b.evaluate(run.spec, points, run.executor, run.store, deck=self.deck, corners="all", step=_check_step(k),
                          cshrc=run.cshrc, parallel_jobs=run.jobs, limits=run.limits,
                          stop_at_first_failure=_recheck_stop(run.spec, self.full))

    def _misses(self, searched: Spec, best: Observation) -> tuple[list[_Move], list[Constraint]]:
        """The constraints ``best`` misses, each with the move it takes from the limit ``searched`` gives it, and those
        whose metric has no value there."""
        spec, moves, no_value = self.run.spec, [], []
        factor = Decimal(repr(float(self.tighten)))
        for i, c in enumerate(spec.constraints):
            value, where = _worst(spec, c, best)
            if value is None:
                no_value.append(c)
                continue
            limit, number = _limit(c), Decimal(repr(value))
            miss = number - limit if c.op in ("lt", "le") else limit - number
            if miss > 0:
                moves.append(_Move(i, c, _form(c), searched.constraints[i].value, number, where, miss, factor * miss))
        return moves, no_value

    def _end(self, k: int, outcome: str, why: str) -> None:
        self.file["outcome"], self.file["why"] = outcome, why
        self.run.note(f"signoff round {k}: {why}")

    def _write(self) -> None:
        """``.icopt/reports/signoff_rounds.json``, replaced whole (written aside, then renamed)."""
        path = self.run.store.reports_dir() / ROUNDS_FILE
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.file, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)


def _best_rechecked(rows: Observations) -> Observation:
    """The best re-checked point (:func:`main`, "Rounds", 1)."""
    feasible = Observations(rows).best(1)
    if feasible:
        return feasible[0]
    judged = [o for o in rows if o.status == "constraint_failed"]
    return min(judged or list(rows), key=lambda o: o.constraint_penalty)


def _verdict(o: Observation) -> dict:
    return {"obs_id": o.obs_id, "params": dict(o.params), "status": o.status, "feasible": o.feasible, "fom": o.fom,
            "constraint_penalty": o.constraint_penalty, "not_run": list(o.not_run)}


def _worst(spec: Spec, constraint: Constraint, o: Observation) -> tuple[float | None, str | None]:
    """The constraint's own worst value at ``o`` over the corners it is judged at, and that corner (:func:`main`,
    "Rounds", 2), the first of equal ones -- no corner for a metric only a corner-less child holds (a device, measured
    once and counted at every corner); the point's own value, with no corner, where no corner holds it; (None, None)
    when it has none."""
    per_corner = metrics_per_corner(spec, o)
    found = [(m[constraint.metric], cid) for cid in scored_corners(spec)
             if (m := per_corner.get(cid)) and constraint.metric in m and math.isfinite(m[constraint.metric])]
    if not found:
        value = o.metrics.get(constraint.metric)
        return (value, None) if value is not None and math.isfinite(value) else (None, None)
    value, corner = (max if constraint.op in ("lt", "le") else min)(found, key=lambda item: item[0])
    cornered = any(constraint.metric in c.metrics for c in o.children.values() if c.corner is not None)
    return value, corner if cornered else None


def _tightened(spec: Spec, moves: list[_Move]) -> Spec:
    """A copy of ``spec`` in which each constraint ``moves`` names carries its limit moved -- an upper limit down, a
    lower one up -- written in the form of the constraint as written."""
    constraints = list(spec.constraints)
    for m in moves:
        c = constraints[m.index]
        limit = _limit(c) - m.move if c.op in ("lt", "le") else _limit(c) + m.move
        constraints[m.index] = c.model_copy(update={"value": _write(m.form, limit)})
    return spec.model_copy(update={"constraints": constraints})


def _crossed(spec: Spec) -> str:
    """The metrics bounded on both sides whose limits leave no value between them (a value at a limit passes it, as the
    verdict judges it: ``objective.constraint_violations``), as ``V ge 1.1 and V le 1.0``; empty when there is none."""
    out = []
    for metric in dict.fromkeys(c.metric for c in spec.constraints):
        lower = [c for c in spec.constraints if c.metric == metric and c.op in ("gt", "ge")]
        upper = [c for c in spec.constraints if c.metric == metric and c.op in ("lt", "le")]
        if lower and upper:
            low, high = max(lower, key=_limit), min(upper, key=_limit)
            if _limit(low) > _limit(high):
                out.append(f"{metric} {low.op} {low.value} and {metric} {high.op} {high.value}")
    return "; ".join(out)


# -- a limit in its constraint's own form -------------------------------------------------------------------------------

_VALUE = re.compile(r"(?P<number>[+-]?(?:\d+(?:\.\d*)?|\.\d+))(?P<exponent>[eE][+-]?\d+)?(?P<tail>.*)", re.DOTALL)
_UNIT = re.compile(r"[A-Za-z]\w*")


def _limit(c: Constraint) -> Decimal:
    """The limit as the verdict reads it (``objective.constraint_violations``): the number, whatever unit follows."""
    return space.parse_scalar(c.value.replace(" ", ""))[0]


def _form(c: Constraint) -> tuple[str | None, str]:
    """How ``c``'s value writes its limit: the exponent it writes (``e9`` of ``28e9 Hz``) or None, and what follows the
    number (`` dB``: the unit and the spaces before it). For a value :func:`_unwritable` accepts."""
    match = _VALUE.fullmatch(c.value)
    return match["exponent"], match["tail"]


def _unwritable(spec: Spec, c: Constraint) -> str | None:
    """Why the recipe cannot write a tightened limit back in the form of ``c``'s value, or None: a value that is not a
    number followed by a unit as the verdict reads it (``space.parse_scalar`` without the spaces), and one whose unit is
    an SI scale prefix, alone or on a unit (``digest.SI_UNITS``), that is not the metric's own unit."""
    match = _VALUE.fullmatch(c.value)
    unit = match["tail"].replace(" ", "") if match else ""
    try:
        read, read_unit = space.parse_scalar(c.value.replace(" ", ""))
    except ValueError:
        read = read_unit = None
    if (match is None or (unit and not _UNIT.fullmatch(unit))
            or read != Decimal(match["number"] + (match["exponent"] or "")) or read_unit != unit):
        return "not a number followed by a unit, as '9 dB' or '28e9 Hz'"
    metric_unit = next(m.unit for m in spec.metrics if m.name == c.metric)
    if unit and unit != metric_unit and (unit in space.SCALE or (unit[0] in space.SCALE and unit[1:] in SI_UNITS)):
        reads = f"{read}" if metric_unit in ("1", "ratio") else f"{read} {metric_unit}"
        return (f"a constraint's value takes no SI prefix, so it reads as {reads}; write the number in the metric's own "
                f"unit ({metric_unit})")
    try:
        _write(_form(c), read)
    except ValueError:                              # a safeguard: every value accepted above writes back
        return "its number cannot be written back in its own form"
    return None


def _refuse_unwritable_limits(spec: Spec, rounds: int) -> None:
    """Every constraint whose limit the recipe cannot write back in its own form, refused at once (one ValueError)."""
    problems = [f"{c.metric} {c.op} {c.value!r}: {problem}" for c in spec.constraints if (problem := _unwritable(spec, c))]
    if problems:
        raise ValueError(f"signoff rounds={rounds} writes a tightened limit in its constraint's own form and cannot for "
                         + "; ".join(problems))


def _write(form: tuple[str | None, str], number: Decimal) -> str:
    """``number`` in a constraint's form (:func:`_form`): with the exponent the value writes, the mantissa scaled to it
    (``28e9 Hz`` -> ``28.5e9 Hz``), else plain (``8.7 dB``); no digit is rounded away. A ValueError when the verdict
    would read the text back as another number."""
    exponent, tail = form
    mantissa = (number.scaleb(-int(exponent[1:])) if exponent else number).normalize()
    text = f"{'0' if mantissa == 0 else f'{mantissa:f}'}{exponent or ''}{tail}"
    if space.parse_scalar(text.replace(" ", ""))[0] != number:
        raise ValueError(f"signoff: {number} written as {text!r} reads back as another number")
    return text
