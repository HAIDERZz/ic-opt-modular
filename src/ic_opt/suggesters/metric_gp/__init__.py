"""The ``metric_gp`` strategy: one Gaussian process per metric, the spec's own formulas on their samples, the spec's
grid as the search space (T17.1 specification, ``docs/refactor/T17_1_METRIC_GP_SPEC.md``; this module: sections 1,
10 and 11, and the orchestration of the others).

It never sees a penalty value: every metric the constraints and the objective name is modelled on its own values
(``models.py``), a point that gives no value is modelled apart (the "gives a value" classifier), and what the spec
makes of the metrics is computed by the spec's formulas on posterior samples (``compose.py``, ``select.py``). Stateless:
everything is rebuilt from the history, the search region by replaying the batches its origin tags name
(``region.py``); the same history and seed give the same proposal.

Stage 1 of T17 (``T17_OPTIMIZER_PLAN_CN.md``, D13) took one condition and no EM devices. Since T17.9 it takes a run at
several corners, each point's metrics given to the models at their worst over the corners it was simulated at
(``sim.corner.worst_metrics``, read by ``compose.true_arrays``); nothing else in it knows of corners. EM devices it does
not take yet: ``opt.optimize`` refuses them before anything is simulated (:func:`stage_one_refusal`), and the suggester
refuses them when called directly, as it refuses a history whose points were evaluated at different sets of corners
(:func:`_refuse`).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ic_opt import advice as advice_rules
from ic_opt import space
from ic_opt.observation import Observations
from ic_opt.spec import Spec
from ic_opt.suggesters.base import Proposal, space_filling
from ic_opt.suggesters.metric_gp import candidates, region, select
from ic_opt.suggesters.metric_gp.compose import (
    Composer,
    metric_scales,
    modelled_metrics,
    true_arrays,
)
from ic_opt.suggesters.metric_gp.coords import Coords, keys
from ic_opt.suggesters.metric_gp.models import MetricModel, ValueModel, fit_metric, fit_value_model

DEVICES_REFUSAL = "strategy metric_gp does not take EM devices yet; use openbox_gp_eic"
SETS_REFUSAL = ("strategy metric_gp models points evaluated at one set of corners, and this history holds several ({sets}): "
                "opt.optimize hands it the points of its run's corners; opt.suggest called directly must be handed one set")
ANCHOR_SAMPLE = 2000               # Sobol points a new region's anchor is chosen among

# Streams of the call's random numbers: (seed, history size, stream[, metric index]).
_WIDE, _LOCAL, _SELECT, _ANCHOR_POINTS, _ANCHOR_SAMPLE, _VALUE, _FIT = range(7)


def initial_design_size(spec: Spec, initial_trials: int | None = None, budget: int | None = None) -> int:
    """Observations (start points included) before the models propose: ``initial_trials`` when given, else
    ``min(max(2 d, 8), 20)`` for ``d`` active variables -- and, with the run's ``budget`` known (``opt.optimize`` passes
    it), at most half of it, so at least half of a small run is the models' (as for the OpenBox strategies, N-30)."""
    if initial_trials:
        return int(initial_trials)
    size = min(max(2 * Coords(spec).d, 8), 20)
    return size if budget is None else max(1, min(size, int(budget) // 2))


def stage_one_refusal(spec: Spec, corners: int | None = None) -> str | None:
    """Why a run on ``spec`` is outside what ``metric_gp`` takes, or None: EM devices. Any number of ``corners`` since
    T17.9 (the argument stays for its callers)."""
    return DEVICES_REFUSAL if spec.devices else None


class MetricGpSuggester:
    name = "metric_gp"

    def __init__(self, *, initial_trials: int | None = None, wide_share: float = 0.2) -> None:
        self.initial_trials = initial_trials
        self.wide_share = wide_share      # share of a batch chosen over the whole space when there is a region; under
                                          # an advice, the share of free slots

    def propose(self, spec: Spec, history: Observations, n: int, *, seed: int,
                pending: Sequence[dict[str, str]] = (), advice: Sequence[dict] = ()) -> Proposal:
        """``seed`` is the run's seed: it fixes the initial design; every other random number is drawn from
        (seed, history size), so successive batches differ and the same history and seed give the same proposal.
        ``advice``: every row of the project's advice file (``ic_opt.advice``); the advice in effect at this history
        size narrows where the models' points look (section 6.2 of the T17.1.5 specification). The design's points are
        not moved by an advice: the design is the design."""
        _refuse(spec, history)
        coords = Coords(spec)
        design = initial_design_size(spec, self.initial_trials)
        taken = history.keys() | {space.point_key(p) for p in pending}
        # Until a point has given every metric there is nothing to model and nowhere to search around: the design goes
        # on. (A search region around a failing point keeps most of what made it fail. On a circuit where 85% of random
        # points fail to simulate, 4 of 10 runs whose design held no scored point had none in all their 200 points.)
        nothing_scored = not any(o.status in region.SCORED for o in history)
        space_filled = n if nothing_scored else min(n, max(0, design - len(history) - len(pending)))
        raw = space_filling(spec, taken, space_filled, method="sobol", size=design, seed=seed, to_raw=coords.design_raw)
        tags = ["init"] * len(raw)
        if len(raw) == n:
            return Proposal(raw, tags=tags)
        rows = list(history)
        chosen = [space.snap(spec, r) for r in raw] + list(pending)
        excluded = set(keys(coords.indices([o.params for o in rows] + chosen)))
        idx, more = self._model_batch(spec, coords, rows, n - len(raw), excluded, seed, advice,
                                       place=len(pending) + len(raw))
        return Proposal(raw + coords.raw(idx).tolist() if len(idx) else raw, tags=tags + more)

    def fit(self, spec: Spec, coords: Coords, rows: list, seed: int) -> tuple[list[MetricModel], ValueModel]:
        """Every modelled metric's model and the "gives a value" classifier, on the whole history."""
        x = coords.unit(coords.indices([o.params for o in rows]))[:, coords.active]
        arrays = true_arrays(spec, rows)
        models = []
        for index, name in enumerate(modelled_metrics(spec)):
            finite = np.isfinite(arrays[name])
            models.append(fit_metric(name, x[finite], arrays[name][finite], _rng(seed, len(rows), _FIT, index)))
        scored = np.array([o.status in region.SCORED for o in rows], dtype=bool)
        return models, fit_value_model(x, scored, _rng(seed, len(rows), _VALUE))

    def region_state(self, spec: Spec, history: Observations) -> region.Region:
        """The search region as the history leaves it (the replay of section 9)."""
        return region.replay(spec, list(history), Coords(spec).d)

    def _model_batch(self, spec: Spec, coords: Coords, rows: list, n: int, excluded: set[bytes],
                     seed: int, advice: Sequence[dict] = (), place: int = 0) -> tuple[np.ndarray, list[str]]:
        """``n`` of the models' points; ``place``: the points of the batch before them (start rows, design points), so
        that ``len(rows) + place`` points precede the first."""
        k = len(rows)
        nothing_feasible = not any(o.status == "ok" for o in rows)
        models, value_model = self.fit(spec, coords, rows, seed)
        composer, scales = Composer(spec), metric_scales(spec, rows)
        active = coords.active
        current = advice_rules.in_effect(advice, k)
        advised = candidates.Advised(coords, current) if advice_rules.narrows(current) else None
        if space.grid_size(spec) <= candidates.MAX_CANDIDATES:
            idx = candidates.whole_grid(coords, excluded)
            slots = min(n, len(idx))
            # Without advice every slot chooses among every grid point. With one the free slots still do (D7b), the others
            # among the points inside it -- held variables at the best point's levels, there being no region -- or, once
            # none is left there, among every point again.
            free = np.ones(slots, dtype=bool) if advised is None else self._free_slots(slots, k + place)
            inside = (advised.contains(idx, self._best(spec, coords, rows, composer, scales)) if advised is not None
                      else np.zeros(len(idx), dtype=bool))
            preferred = [None if f else inside for f in free]
            picks = select.select_batch(models, value_model, composer, scales, coords.unit(idx)[:, active],
                                        preferred, _rng(seed, k, _SELECT), nothing_feasible=nothing_feasible)
            return idx[picks], [f"grid:{k}" + (f"@{advised.id}" if not free[b] and inside[j] else "")
                                for b, j in enumerate(picks)]

        state = region.replay(spec, rows, coords.d)
        anchor = None
        anchor_advised = False
        if state.ended:
            # The anchor is the first of the new region's slots: an advice narrows it, unless that slot is a free one (a
            # batch of one or two slots, section 6.2 of the T17.1.5 specification).
            anchor_free = advised is not None and bool(self._free_slots(n, k + place, head=1)[0])
            anchor, anchor_advised = self._anchor(spec, coords, rows, state, models, value_model, composer, scales,
                                                  excluded, seed, None if anchor_free else advised)
            index, length, centre = state.index + 1, region.LENGTH_INIT, anchor
        else:
            position = region.centre(composer, rows, state, scales, true_arrays(spec, rows))
            centre = (coords.indices([rows[position].params])[0] if position is not None
                      else coords.snap(np.full((1, len(coords.counts)), 0.5))[0])
            index, length = state.index, state.length
        before = set(excluded) if advised is not None else excluded
        local = candidates.local(coords, centre, length, region.weights(models, coords), excluded, _rng(seed, k, _LOCAL))
        head = [anchor[None, :]] if anchor is not None else []
        local = local[: candidates.LOCAL - len(head)]          # the anchor is one of the region's candidates
        wide = candidates.wide(coords, excluded, _rng(seed, k, _WIDE))
        if advised is None:
            idx = np.vstack(head + [local, wide])
            is_wide = np.r_[np.zeros(len(idx) - len(wide), dtype=bool), np.ones(len(wide), dtype=bool)]
            is_advised = np.zeros(len(idx), dtype=bool)
            slots = min(n, len(idx))
            n_wide = min(round(self.wide_share * slots), slots - len(head))
            preferred = [None] * len(head) + [is_wide] * n_wide + [~is_wide] * (slots - len(head) - n_wide)
            free = np.ones(slots, dtype=bool)
        else:
            idx, is_advised, is_free, is_wide = _advised_candidates(advised, head, local, wide, centre, before)
            slots = min(n, len(idx))
            free = self._free_slots(slots, k + place, head=len(head))
            preferred = [None] * len(head) + [is_free if f else is_advised for f in free[len(head):]]
        picks = select.select_batch(models, value_model, composer, scales, coords.unit(idx)[:, active], preferred,
                                    _rng(seed, k, _SELECT), nothing_feasible=nothing_feasible,
                                    forced=0 if head else None)
        tags = []
        for b, j in enumerate(picks):
            under = is_advised[j] and not free[b]
            kind = "wide" if is_wide[j] and not under else "tr"
            tags.append(f"{kind}:{index}:{k}" + (f"@{advised.id}" if under else ""))
        if head:
            tags[0] = f"anchor:{index}:{k}" + (f"@{advised.id}" if anchor_advised else "")
        return idx[picks], tags

    def _free_slots(self, slots: int, preceding: int, head: int = 0) -> np.ndarray:
        """Which of a batch's ``slots`` model slots are free under an advice (section 6.2 of the T17.1.5 specification): as
        many as the whole space has in a batch without advice, ``round(wide_share * slots)``, right after the ``head``
        (a new region's anchor). A batch of one or two slots has none by that count; there slot ``b`` is free when the
        point's place in the run, ``preceding + b + 1``, is a multiple of ``round(1 / wide_share)`` (5), so that a fifth
        of the points stays free whatever the batch size. ``preceding``: the points before the first model slot."""
        free = np.zeros(slots, dtype=bool)
        count = round(self.wide_share * slots)
        if count:
            free[head : head + count] = True
        elif self.wide_share > 0:
            free[(preceding + np.arange(slots) + 1) % round(1 / self.wide_share) == 0] = True
        return free

    def _anchor(self, spec: Spec, coords: Coords, rows: list, state: region.Region, models: list[MetricModel],
                value_model: ValueModel, composer: Composer, scales: dict[str, float], excluded: set[bytes],
                seed: int, advised: candidates.Advised | None = None) -> tuple[np.ndarray, bool]:
        """The next region's anchor: among 2000 snapped Sobol points farther than ``0.25 sqrt(d)`` from every earlier
        region's final centre, the winner of one posterior sample under the rule of section 8; when none is that far,
        the farthest one. It leaves ``excluded`` holding it.

        Under an advice that narrows the search the 2000 points are brought inside it first (held variables at the best
        point's levels), and the anchor says so (the second value): the anchor is the first of the region's own slots,
        which an advice narrows; the free slots keep a fifth of every batch. When the advice holds none of them that is
        not evaluated, the anchor is chosen as without advice."""
        k = len(rows)
        pool = candidates.wide(coords, set(excluded), _rng(seed, k, _ANCHOR_POINTS), n=ANCHOR_SAMPLE)
        inside = False
        if advised is not None:
            moved = candidates.fresh(advised.inside(pool, self._best(spec, coords, rows, composer, scales)), set(excluded))
            if len(moved):
                pool, inside = moved, True
        unit = coords.unit(pool)[:, coords.active]
        centres = coords.unit(coords.indices([rows[p].params for p in state.centres]))[:, coords.active]
        far, distance = region.far_from(unit, centres, coords.d)
        if far.any():
            among = np.flatnonzero(far)
            j = among[select.select_batch(models, value_model, composer, scales, unit[among], [None],
                                          _rng(seed, k, _ANCHOR_SAMPLE),
                                          nothing_feasible=not any(o.status == "ok" for o in rows))[0]]
        else:
            j = int(np.argmax(distance))
        excluded.update(keys(pool[j : j + 1]))
        return pool[j], inside

    @staticmethod
    def _best(spec: Spec, coords: Coords, rows: list, composer: Composer, scales: dict[str, float]) -> np.ndarray:
        """Level indices of the history's best point (section 6), where an advice's held variables stay when there is no
        region to take them from; the grid's middle when nothing is scored."""
        best = region.incumbent(composer, rows, list(range(len(rows))), scales, true_arrays(spec, rows))
        if best is None:
            return coords.snap(np.full((1, len(coords.counts)), 0.5))[0]
        return coords.indices([rows[best.position].params])[0]


def _advised_candidates(advised: candidates.Advised, head: list[np.ndarray], local: np.ndarray, wide: np.ndarray,
                        centre: np.ndarray, taken: set[bytes]) -> tuple[np.ndarray, ...]:
    """A region's batch under an advice (section 6.2 of the T17.1.5 specification): the anchor (``head``), the region's
    candidates brought inside the advice, then the candidates of the batch without advice -- the region's unmoved and the
    wide ones -- that are not among those. Returns the candidates and three masks over them: advised (inside the advice),
    free (a candidate of the batch without advice; a moved point that is one of them is both), wide (for the tag of a free
    pick). ``taken``: evaluated or already chosen for this batch, the anchor included."""
    near = candidates.fresh(advised.inside(local, centre), set(taken))
    free = np.vstack([local, wide])
    position = {key: j for j, key in enumerate(keys(near))}
    free_keys = keys(free)
    idx = np.vstack(head + [near, free[[row for row, key in enumerate(free_keys) if key not in position]]])
    start = len(head)
    is_advised = np.zeros(len(idx), dtype=bool)
    is_advised[start : start + len(near)] = True
    is_free = np.zeros(len(idx), dtype=bool)
    is_free[start + len(near) :] = True
    is_free[[start + position[key] for key in free_keys if key in position]] = True
    wide_keys = set(keys(wide))
    is_wide = np.array([key in wide_keys for key in keys(idx)], dtype=bool)
    return idx, is_advised, is_free, is_wide


def _refuse(spec: Spec, history: Observations) -> None:
    """EM devices; a history whose points were evaluated at different sets of corners (the signoff recipe's store holds
    its one-corner search and its all-corner re-check), whose worst values would mix one corner's with several's."""
    if spec.devices:
        raise ValueError(DEVICES_REFUSAL)
    sets: dict[tuple[str, ...], int] = {}
    for o in history:
        if corners := o.corners():                                     # a point stopped early: every corner it was to run at
            key = tuple(sorted(corners))
            sets[key] = sets.get(key, 0) + 1
    if len(sets) > 1:
        raise ValueError(SETS_REFUSAL.format(sets="; ".join(f"{', '.join(key)}: {count}" for key, count in sets.items())))


def _rng(seed: int, k: int, stream: int, index: int = 0) -> np.random.Generator:
    return np.random.default_rng([seed % 2**63, k, stream, index])


__all__ = ["DEVICES_REFUSAL", "SETS_REFUSAL", "MetricGpSuggester", "initial_design_size", "stage_one_refusal"]
