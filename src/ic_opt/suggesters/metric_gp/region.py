"""The search region and its replay from the history (T17.1 specification, sections 6, 9 and 10), used only when the
grid holds more than 2000 points.

A region is a box around the best of its own points (section 6: the smallest violation until something is feasible,
then the smallest feasible objective, both from true values). Its side doubles after 3 successful batches in a row and
halves after ``max(2, ceil(d / batch))`` unsuccessful ones; below ``2**-6`` it ends and the next region starts at a new
anchor, far from where the earlier ones ended. All observations stay in the models.

Nothing is stored: the regions are rebuilt on every call by walking the history's batches in order, a batch being the
rows whose origin carries the same batch key ``k`` (the history size when the batch was proposed). A batch is judged
with the violation scales of the history up to and including that batch, i.e. with what the history held when the
next batch was proposed: the replay of a prefix is a prefix of the replay of the whole, so the state a call rebuilds
is the state an uninterrupted run was in, batch by batch (a continued run equals an uninterrupted one), and a batch's
verdict never changes as the history grows.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from ic_opt import space
from ic_opt.observation import Observation
from ic_opt.spec import Spec
from ic_opt.suggesters.metric_gp.compose import Composer, metric_scales, true_arrays
from ic_opt.suggesters.metric_gp.coords import Coords
from ic_opt.suggesters.metric_gp.models import MetricModel

LENGTH_INIT = 0.8
LENGTH_MAX = 1.6
LENGTH_MIN = 2.0**-6
SUCCESS_TOLERANCE = 3              # successful batches in a row that double the side
IMPROVEMENT = 1e-3                 # relative improvement a successful batch needs
WEIGHT_CLIP = (0.2, 5.0)
ANCHOR_DISTANCE = 0.25             # x sqrt(d): how far a new anchor keeps from every earlier region's final centre
SCORED = ("ok", "constraint_failed")

BATCH_TAG = re.compile(r"^suggest:metric_gp:(?P<kind>grid|tr|wide|anchor):(?:\d+:)?(?P<k>\d+)$")


@dataclass(frozen=True)
class Incumbent:
    position: int                  # in the history
    feasible: bool
    value: float                   # the objective when feasible, else the violation


@dataclass
class Region:
    index: int = 0
    length: float = LENGTH_INIT
    successes: int = 0
    failures: int = 0
    own: list[int] = field(default_factory=list)       # history positions of the region's own points
    anchor: int | None = None                          # history position of its anchor (regions after the first)
    ended: bool = False                                # the side fell below LENGTH_MIN: the next batch starts a new region
    centres: list[int] = field(default_factory=list)   # history positions of every earlier region's final centre
    trace: list[tuple[int, int, float, int, int]] = field(default_factory=list)   # (k, index, length, successes, failures) per batch

    def state(self) -> tuple[int, float, int, int]:
        return self.index, self.length, self.successes, self.failures


def batch_key(origin: str) -> tuple[str, int] | None:
    """(kind, k) of a metric_gp batch row, None for every other row (start points, the initial design, user points,
    other strategies, an advice's start points: region 0's, not batches). A point proposed under an advice
    (``tr:0:40@a2``) belongs to its batch as any other: the region's course is judged as without advice."""
    match = BATCH_TAG.match(space.split_origin(origin)[0])
    return (match.group("kind"), int(match.group("k"))) if match else None


def incumbent(composer: Composer, rows: list[Observation], positions: list[int], scales: dict[str, float],
              arrays: dict[str, np.ndarray]) -> Incumbent | None:
    """Section 6 over the rows at ``positions``: the feasible one with the smallest objective, else the scored one that
    ranks first among infeasible points (``Observation.infeasibility_key``, T17.8: the one that got furthest -- a point
    stopped early after one that ran every child -- then the smallest violation over the constraints judged, a metric
    it never got counting 0); None when none is scored. ``arrays``: the true metric values of every row."""
    scored = [p for p in positions if rows[p].status in SCORED]
    if not scored:
        return None
    picked = {name: values[scored] for name, values in arrays.items()}
    feasible = np.array([rows[p].status == "ok" for p in scored])
    if feasible.any():
        objective = np.where(feasible, composer.objective(picked, scales), np.inf)
        best = int(np.argmin(objective))
        return Incumbent(scored[best], True, float(objective[best]))
    violation = composer.known_violation(picked, scales)
    best = min(range(len(scored)), key=lambda i: rows[scored[i]].infeasibility_key(float(violation[i])))
    return Incumbent(scored[best], False, float(violation[best]))


def improved(before: Incumbent | None, after: Incumbent | None) -> bool | None:
    """Did a batch improve the region's best? None when the region had no best to improve on (its first scored
    points): the batch sets the baseline and changes no counter."""
    if before is None:
        return None if after is not None else False
    if not before.feasible:
        return after.feasible or after.value < before.value - IMPROVEMENT * abs(before.value)
    return after.value < before.value - IMPROVEMENT * max(1.0, abs(before.value))


def replay(spec: Spec, rows: list[Observation], d: int) -> Region:
    """The regions and their counters rebuilt from the history (``d``: active variables)."""
    composer = Composer(spec)
    arrays = true_arrays(spec, rows)
    batches: dict[int, list[int]] = {}
    anchors: dict[int, int] = {}
    others: list[int] = []
    for position, obs in enumerate(rows):
        tagged = batch_key(obs.origin)
        if tagged is None:
            others.append(position)
            continue
        batches.setdefault(tagged[1], []).append(position)
        if tagged[0] == "anchor":
            anchors[tagged[1]] = position
    region = Region(own=list(others))
    for k, positions in sorted(batches.items(), key=lambda item: item[1][0]):
        if region.ended:
            region = Region(index=region.index + 1, centres=region.centres, trace=region.trace)
        region.anchor = region.anchor if region.anchor is not None else anchors.get(k) if region.index else None
        scales = metric_scales(spec, rows[: positions[-1] + 1])
        before_positions = [p for p in region.own if p < positions[0]]
        before = incumbent(composer, rows, before_positions, scales, arrays)
        after = incumbent(composer, rows, before_positions + positions, scales, arrays)
        region.own = sorted(set(region.own) | set(positions))
        verdict = improved(before, after)
        if verdict:
            region.successes, region.failures = region.successes + 1, 0
            if region.successes == SUCCESS_TOLERANCE:
                region.length, region.successes = min(2 * region.length, LENGTH_MAX), 0
        elif verdict is False:
            region.successes, region.failures = 0, region.failures + 1
            if region.failures >= max(2, math.ceil(d / len(positions))):
                region.length, region.failures = region.length / 2, 0
        if region.length < LENGTH_MIN:
            region.ended = True
            region.centres.append(centre(composer, rows, region, scales, arrays))
        region.trace.append((k, *region.state()))
    return region


def centre(composer: Composer, rows: list[Observation], region: Region, scales: dict[str, float],
           arrays: dict[str, np.ndarray]) -> int | None:
    """History position of the region's centre: the best of its own points (section 6); while none of them is scored,
    its anchor, else its first own point; None for a region without points (an empty history)."""
    best = incumbent(composer, rows, region.own, scales, arrays)
    if best is not None:
        return best.position
    if region.anchor is not None:
        return region.anchor
    return region.own[0] if region.own else None


def weights(models: list[MetricModel], coords: Coords) -> np.ndarray:
    """Per active variable: the geometric mean over the modelled metrics of the fitted length scales, normalized to
    geometric mean 1 over the variables and clipped to [0.2, 5]; ones when no metric has a fitted model."""
    scales = [m.length_scales for m in models if m.length_scales is not None]
    if not scales:
        return np.ones(coords.d)
    log = np.mean(np.log(np.vstack(scales)), axis=0)
    return np.clip(np.exp(log - log.mean()), *WEIGHT_CLIP)


def far_from(unit: np.ndarray, centres: np.ndarray, d: int) -> tuple[np.ndarray, np.ndarray]:
    """For candidate points ``unit`` (active unit coordinates): which are farther than ``0.25 * sqrt(d)`` from every
    earlier region's final centre, and each one's distance to the nearest of them."""
    if not len(centres):
        return np.ones(len(unit), dtype=bool), np.full(len(unit), np.inf)
    nearest = np.min(np.linalg.norm(unit[:, None, :] - centres[None, :, :], axis=-1), axis=1)
    return nearest > ANCHOR_DISTANCE * math.sqrt(d), nearest
