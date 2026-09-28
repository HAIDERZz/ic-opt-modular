"""The ``metric_gp`` strategy: one Gaussian process per metric, the spec's own formulas on their samples, the spec's
grid as the search space (T17.1 specification, ``docs/refactor/T17_1_METRIC_GP_SPEC.md``; this module: sections 1,
10 and 11, and the orchestration of the others).

It never sees a penalty value: every metric the constraints and the objective name is modelled on its own values
(``models.py``), a point that gives no value is modelled apart (the "gives a value" classifier), and what the spec
makes of the metrics is computed by the spec's formulas on posterior samples (``compose.py``, ``select.py``). Stateless:
everything is rebuilt from the history, the search region by replaying the batches its origin tags name
(``region.py``); the same history and seed give the same proposal.

Stage 1 of T17 (``T17_OPTIMIZER_PLAN_CN.md``, D13): one condition, no EM devices. ``opt.optimize`` refuses both
before anything is simulated (:func:`stage_one_refusal`); the suggester refuses a history evaluated at more than one
corner and a spec with devices when called directly.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

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
CORNERS_REFUSAL = ("strategy metric_gp works on one condition; run one corner (corners='[\"tt\"]'), or the signoff recipe, "
                   "which searches at one corner and re-checks the best points at all")
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


def stage_one_refusal(spec: Spec, corners: int) -> str | None:
    """Why a run of ``corners`` corners on ``spec`` is outside stage 1, or None."""
    if spec.devices:
        return DEVICES_REFUSAL
    if corners > 1:
        return CORNERS_REFUSAL
    return None


class MetricGpSuggester:
    name = "metric_gp"

    def __init__(self, *, initial_trials: int | None = None, wide_share: float = 0.2) -> None:
        self.initial_trials = initial_trials
        self.wide_share = wide_share      # share of a batch chosen over the whole space when there is a region

    def propose(self, spec: Spec, history: Observations, n: int, *, seed: int,
                pending: Sequence[dict[str, str]] = ()) -> Proposal:
        """``seed`` is the run's seed: it fixes the initial design; every other random number is drawn from
        (seed, history size), so successive batches differ and the same history and seed give the same proposal."""
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
        idx, more = self._model_batch(spec, coords, rows, n - len(raw), excluded, seed)
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
                     seed: int) -> tuple[np.ndarray, list[str]]:
        k = len(rows)
        nothing_feasible = not any(o.status == "ok" for o in rows)
        models, value_model = self.fit(spec, coords, rows, seed)
        composer, scales = Composer(spec), metric_scales(spec, rows)
        active = coords.active
        if space.grid_size(spec) <= candidates.MAX_CANDIDATES:
            idx = candidates.whole_grid(coords, excluded)
            picks = select.select_batch(models, value_model, composer, scales, coords.unit(idx)[:, active],
                                        [None] * min(n, len(idx)), _rng(seed, k, _SELECT),
                                        nothing_feasible=nothing_feasible)
            return idx[picks], [f"grid:{k}"] * len(picks)

        state = region.replay(spec, rows, coords.d)
        anchor = None
        if state.ended:
            anchor = self._anchor(spec, coords, rows, state, models, value_model, composer, scales, excluded, seed)
            index, length, centre = state.index + 1, region.LENGTH_INIT, anchor
        else:
            position = region.centre(composer, rows, state, scales, true_arrays(spec, rows))
            centre = (coords.indices([rows[position].params])[0] if position is not None
                      else coords.snap(np.full((1, len(coords.counts)), 0.5))[0])
            index, length = state.index, state.length
        local = candidates.local(coords, centre, length, region.weights(models, coords), excluded, _rng(seed, k, _LOCAL))
        head = [anchor[None, :]] if anchor is not None else []
        local = local[: candidates.LOCAL - len(head)]          # the anchor is one of the region's candidates
        wide = candidates.wide(coords, excluded, _rng(seed, k, _WIDE))
        idx = np.vstack(head + [local, wide])
        is_wide = np.r_[np.zeros(len(idx) - len(wide), dtype=bool), np.ones(len(wide), dtype=bool)]
        slots = min(n, len(idx))
        n_wide = min(round(self.wide_share * slots), slots - len(head))
        preferred = [None] * len(head) + [is_wide] * n_wide + [~is_wide] * (slots - len(head) - n_wide)
        picks = select.select_batch(models, value_model, composer, scales, coords.unit(idx)[:, active], preferred,
                                    _rng(seed, k, _SELECT), nothing_feasible=nothing_feasible,
                                    forced=0 if head else None)
        tags = [f"{'wide' if is_wide[j] else 'tr'}:{index}:{k}" for j in picks]
        if head:
            tags[0] = f"anchor:{index}:{k}"
        return idx[picks], tags

    def _anchor(self, spec: Spec, coords: Coords, rows: list, state: region.Region, models: list[MetricModel],
                value_model: ValueModel, composer: Composer, scales: dict[str, float], excluded: set[bytes],
                seed: int) -> np.ndarray:
        """The next region's anchor: among 2000 snapped Sobol points farther than ``0.25 sqrt(d)`` from every earlier
        region's final centre, the winner of one posterior sample under the rule of section 8; when none is that far,
        the farthest one. It leaves ``excluded`` holding it."""
        k = len(rows)
        pool = candidates.wide(coords, set(excluded), _rng(seed, k, _ANCHOR_POINTS), n=ANCHOR_SAMPLE)
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
        return pool[j]


def _refuse(spec: Spec, history: Observations) -> None:
    if spec.devices:
        raise ValueError(DEVICES_REFUSAL)
    corners = {c.corner for o in history for c in o.children.values()}
    if len(corners) > 1:
        raise ValueError(f"{CORNERS_REFUSAL}; the history holds points evaluated at the corners "
                         f"{', '.join(sorted(str(c) for c in corners))}")


def _rng(seed: int, k: int, stream: int, index: int = 0) -> np.random.Generator:
    return np.random.default_rng([seed % 2**63, k, stream, index])


__all__ = ["CORNERS_REFUSAL", "DEVICES_REFUSAL", "MetricGpSuggester", "initial_design_size", "stage_one_refusal"]
