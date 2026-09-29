"""The evaluation schedule (T17.8, step 1): the order in which a point's children run, and where the point stops.

A point's children -- a simulation per testbench × corner, then a measurement per EM device -- run one after another
(``engine._run_point``). A :class:`Schedule` fixes their order for a batch and ends a point at the first child whose
result shows the point cannot be feasible: nothing more is simulated for it, and ``sim.corner.aggregate`` judges the
children that ran. Measured before it was built (``docs/refactor/T17_8_SCHEDULE_SPEC.md``): one simulation exposes 94%
to 99% of the infeasible designs of the PVT benchmark, and on the user's two mixers stopping after the first testbench
that fails a point keeps 43% to 91% of the simulation time.

The order (:meth:`Schedule.from_history`) is computed once per batch from the observations of the same problem -- every
step, every corner, rows adopted from elsewhere too -- for every child ``unit/corner``::

    score = (failed + 1) / (reached + 2) / seconds

``reached``: the observations that hold the child; ``failed``: those whose child shows a failure (:meth:`stop_after`);
``seconds``: the mean of the child's recorded seconds over them, else the mean over its unit's children, else 1. The
first factor is the child's share of failures with one failure and one pass counted beforehand, so a child few points
reached is neither trusted nor ignored; dividing by the time puts the cheap child that often fails first. Children run
in descending score, ties in the spec's order (:meth:`Schedule.spec_order`); until 10 observations hold any of the
children the order is the spec's. EM device children keep their place after the testbench children (scheduling them is
step 3 of the plan). The order is stored nowhere: the next batch computes it again, as the strategies rebuild theirs.

What shows a failure (:meth:`Schedule.stop_after`): a child that did not run to its end (``failed:<stage>``) or lost a
metric (``metric_failed``); else, at a corner in the constraint scope -- every corner under ``corner_policy.constraints:
all_corners``, the nominal one under ``nominal`` -- a constraint whose metric the child produced and violates. A metric
the child did not produce is no failure: another testbench gives it. Under ``nominal`` another corner stops a point only
by a failed simulation; its constraints do not count there, and it still runs for the worst-case objective.

What the specification leaves open, and how it is read:

- the spec's order: the testbench children in the spec's testbench order, each testbench's corners in the spec's corner
  order with the nominal corner first, then the device children in their present order. Whatever order the children ran
  in, the observation keeps them in the engine's own order, so a point that is not stopped is recorded as before.
- the nominal corner: ``nominal`` when the run's corners hold it, else the first of them in spec order, as
  ``sim.corner.aggregate`` names it, so that the stop and the verdict agree. A corner-less child (an EM device, or a
  testbench of a spec without corners) counts at every corner, so it is always in the scope.
- the observations that hold any child: those holding at least one of the children being ordered.
- a unit's mean: over every recorded seconds of that unit's children in the history, at any corner.
- a child recorded at 0 s (quicker than the millisecond the engine rounds to) counts as 1 ms.
- a stop's reason names every constraint the child violates, joined by ``; ``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ic_opt import objective as objective_contract
from ic_opt.observation import ChildResult, Observation

if TYPE_CHECKING:
    from ic_opt.eval.engine import Child
    from ic_opt.spec import Spec

WARM_UP = 10              # observations holding any of the children before the order is learned from them
RESOLUTION_S = 0.001      # the engine records a child's seconds to the millisecond


@dataclass(frozen=True)
class ChildHistory:
    """What the history says of one child."""

    reached: int          # observations that hold it
    failed: int           # of those, the ones whose result shows a failure
    seconds: float        # its mean recorded seconds, else its unit's, else 1

    @property
    def score(self) -> float:
        return (self.failed + 1) / (self.reached + 2) / self.seconds


class Schedule:
    """The order of a point's children and the rule that stops the point (module docstring); :meth:`from_history` builds
    one per batch. ``corner_scope`` is the spec's ``corner_policy.constraints`` (``all_corners`` or ``nominal``);
    ``histories`` None: the spec's order."""

    def __init__(self, spec: Spec, corner_scope: str, nominal: str | None,
                 histories: dict[str, ChildHistory] | None = None, unit_seconds: dict[str, float] | None = None) -> None:
        self.spec = spec
        self.corner_scope = corner_scope
        self.nominal = nominal
        self.histories = histories
        self.unit_seconds = unit_seconds or {}

    @classmethod
    def from_history(cls, spec: Spec, observations: Iterable[Observation], children: Sequence[Child],
                     corner_scope: str) -> Schedule:
        """The schedule of a batch whose points each run ``children``, learned from ``observations`` (the caller's: the
        same problem's rows)."""
        schedule = cls(spec, corner_scope, _nominal(spec, children))
        rows = list(observations)
        keys = {c.key for c in children}
        holding = [o for o in rows if keys.intersection(o.children)]
        if len(holding) < WARM_UP:
            return schedule
        recorded: dict[str, list[float]] = {}
        for o in rows:
            for result in o.children.values():
                if result.seconds is not None:
                    recorded.setdefault(result.unit, []).append(result.seconds)
        unit_seconds = {unit: _mean(values) for unit, values in recorded.items()}
        histories = {}
        for child in children:
            results = [o.children[child.key] for o in holding if child.key in o.children]
            seconds = [r.seconds for r in results if r.seconds is not None]
            histories[child.key] = ChildHistory(
                reached=len(results), failed=sum(schedule.stop_after(r, child.corner) is not None for r in results),
                seconds=max(_mean(seconds) if seconds else unit_seconds.get(child.unit, 1.0), RESOLUTION_S))
        schedule.histories, schedule.unit_seconds = histories, unit_seconds
        return schedule

    def spec_order(self, children: Sequence[Child]) -> list[Child]:
        """``children`` in the spec's order: testbenches in spec order, each one's corners in spec order with the nominal
        corner first, then the EM devices as given."""
        testbenches = {tb: i for i, tb in enumerate(self.spec.testbench_ids)}
        corners = {c.id: i for i, c in enumerate(self.spec.corners)}

        def place(child: Child) -> tuple[int, int, int]:
            if child.unit_kind == "device":
                return (1, 0, 0)
            corner = -1 if child.corner == self.nominal else corners.get(child.corner, len(corners))
            return (0, testbenches.get(child.unit, len(testbenches)), corner)

        return sorted(children, key=place)

    def order(self, children: Sequence[Child]) -> list[Child]:
        """The order a point's ``children`` run in: descending score, ties in the spec's order, the EM devices last; the
        spec's order while the history is short."""
        ordered = self.spec_order(children)
        if self.histories is None:
            return ordered
        testbenches = [c for c in ordered if c.unit_kind != "device"]
        return sorted(testbenches, key=lambda c: -self.history_of(c).score) + [c for c in ordered if c.unit_kind == "device"]

    def history_of(self, child: Child) -> ChildHistory:
        """The child's record in the history; a child :meth:`from_history` was not given counts as one never reached."""
        if self.histories is not None and child.key in self.histories:
            return self.histories[child.key]
        return ChildHistory(0, 0, max(self.unit_seconds.get(child.unit, 1.0), RESOLUTION_S))

    def stop_after(self, result: ChildResult, corner_id: str | None) -> str | None:
        """Why the point cannot be feasible after this child's result, or None when it still can: ``<unit>/<corner>:
        <status>`` for a child that failed or lost a metric, ``<unit>/<corner>: <metric> <op> <value> violated by <x>``
        for a constraint it violates at a corner in the scope."""
        where = f"{result.unit}/{corner_id or 'nominal'}"
        if result.status != "ok":
            return f"{where}: {result.status}"
        if not self.in_scope(corner_id):
            return None
        judged = objective_contract.evaluate_partial(self.spec, result.metrics)
        return f"{where}: {'; '.join(judged.issues)}" if judged.status == "constraint_failed" else None

    def in_scope(self, corner_id: str | None) -> bool:
        """Whether a constraint violated at this corner fails the point: every corner under ``all_corners``, the nominal
        corner under ``nominal``; a corner-less child's metrics count at every corner."""
        return self.corner_scope != "nominal" or corner_id is None or corner_id == self.nominal


def _nominal(spec: Spec, children: Sequence[Child]) -> str | None:
    """The run's nominal corner, as ``sim.corner.aggregate`` names it: ``nominal`` when the children's corners hold it,
    else the first of them in spec order; None without corners."""
    corners = {c.corner for c in children if c.unit_kind != "device" and c.corner is not None}
    ordered = [c.id for c in spec.corners if c.id in corners]
    if not ordered:
        return None
    return "nominal" if "nominal" in ordered else ordered[0]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)
