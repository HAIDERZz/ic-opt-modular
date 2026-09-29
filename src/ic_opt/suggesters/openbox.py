"""OpenBox suggester (GP / PRF surrogate, EIC acquisition), replayed from the observation list.

The initial design is ours (T17.0b). OpenBox's batch path served its design batch by batch with
``sample_random_configs`` (random, not Sobol), and a batch that started inside the design was design in full: a plan that
said "initial design 22 points" ran 30 random points (2026-09-28). Now the first ``initial_trials`` observations are the
start points followed by the points of one seeded space-filling design (``base.space_filling``), served in order whatever
the batch size; a batch that reaches the end of the design is completed by the surrogate as soon as the history holds
``base.surrogate_minimum`` successful points, else by further space-filling points. Each point's origin says which served
it (``suggest:<strategy>:init`` / ``:acq``).

The scale (T17.7). A variable whose range is positive and spans a decade (``space.log_scale``) is searched as
``log10(value)`` (``base.SearchScale``): OpenBox's space, the history it is fed, our design (even per decade) and its
proposals, converted back before they are returned. OpenBox tells evaluated points apart by configuration, and two
continuous ``log10`` values can snap onto one grid point, so with such a variable a batch is chosen by
:func:`grid_suggestions`. On the 12 wide-range development problems this scale was better on 14 of 48 measures and worse
on 1. A run continued by this version on such a spec reads the same history on another scale, so it
proposes differently from the version that started it; a spec without such a variable proposes exactly as before.

What OpenBox is fed, per observation (checked against the vendored OpenBox 0.9.0, ``vendor/open-box``):

- ``ok`` and ``constraint_failed``: the true objective in minimization form (``base.minimization_objective``) and the
  constraint residuals, as a successful trial. OpenBox itself replaces the objective of every infeasible trial with the
  largest objective among the successful ones before training (``History.get_objectives(transform='infeasible')``); it
  expects true values there, and until 0.4.0 got ``1e6 + penalty`` marked as successes, so that maximum was 1e6.
- ``metric_failed``, ``failed:<stage>`` and a point stopped early (T17.8: ``not_run``, a ``constraint_failed`` point that
  has neither an objective nor every constraint's residual): a trial with ``trial_state=FAILED``. ``History`` then counts it in
  ``len(history)`` but not in ``get_success_count()``, never as feasible, and before training replaces its objective and
  each of its constraints with the largest value of that column among the successful trials (``_get_transformed_values``:
  failed rows are set to nan, the column maxima taken, the rows filled with them): the failed point enters both models
  as the worst seen, not as an invented number. Its objective may be any value (validity is checked only for SUCCESS
  trials); with constraints it must carry ``num_constraints`` numbers: ``constraints=None``, which OpenBox's own
  ``parallel_smbo`` writes for a failed trial, makes the history's float array ragged and every later read raises. We
  write inf and nan.
"""

from __future__ import annotations

import math
import tempfile
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from ic_opt import space
from ic_opt.observation import Observation, Observations
from ic_opt.spec import Spec, VariableKind
from ic_opt.suggesters.base import (
    Proposal,
    SearchScale,
    minimization_objective,
    space_filling,
    surrogate_minimum,
)

SURROGATES = {"openbox_gp_eic": "gp", "openbox_prf_eic": "prf", "openbox_auto": "auto"}


class OpenBoxSuggester:
    def __init__(self, strategy: str = "openbox_gp_eic", *, initial_trials: int | None = None, initialization: str = "sobol",
                 workdir: Path | None = None) -> None:
        if strategy not in SURROGATES:
            raise ValueError(f"unknown OpenBox strategy {strategy!r}; expected one of {sorted(SURROGATES)}")
        self.name = strategy
        self.surrogate = SURROGATES[strategy]
        self.initial_trials = initial_trials
        self.initialization = initialization       # the method of our space-filling design (base.unit_design)
        self.workdir = workdir

    def propose(self, spec: Spec, history: Observations, n: int, *, seed: int,
                pending: Sequence[dict[str, str]] = ()) -> Proposal:
        """``seed`` is the run's seed: it fixes the space-filling design; the advisor's own randomness is offset by the
        history size, so successive batches differ."""
        self._openbox()           # at the first batch, not after the design has been simulated
        design = initial_design_size(spec, self.initial_trials)
        taken = history.keys() | {space.point_key(p) for p in pending}
        search = SearchScale(spec)
        raw = space_filling(spec, taken, min(n, max(0, design - len(history) - len(pending))), method=self.initialization,
                            size=design, seed=seed, to_raw=search.design_raw)
        rest = n - len(raw)
        needed = surrogate_minimum(spec)
        if rest and sum(minimization_objective(spec, o) is not None for o in history) < needed:
            taken |= {space.point_key(space.snap(spec, r)) for r in raw}
            raw += space_filling(spec, taken, rest, method=self.initialization, size=design, seed=seed,
                                 to_raw=search.design_raw)
            rest = 0
        tags = ["init"] * len(raw)
        if not rest:
            return Proposal(raw, tags=tags)
        advisor = self.advisor(spec, history, seed=seed)
        if search.log.any():
            taken |= {space.point_key(space.snap(spec, r)) for r in raw}
            suggestions = grid_suggestions(advisor, spec, search, taken, rest)
        else:
            suggestions = advisor.get_suggestions(batch_size=rest) if rest > 1 else [advisor.get_suggestion()]
        raw += search.raw_of([_numbers(spec, s) for s in suggestions]) if suggestions else []
        return Proposal(raw, tags=tags + ["acq"] * len(suggestions))

    def advisor(self, spec: Spec, history: Observations, *, seed: int):
        """OpenBox's ``Advisor`` with ``history`` fed (see the module docstring) and the finite reference in place, ready
        for its surrogate: its ``initial_trials`` is the surrogate minimum, so it models the history it has."""
        Advisor, OpenBoxObservation, sp, FAILED = self._openbox()
        search = SearchScale(spec)
        cs = _config_space(spec, sp, search)
        advisor = Advisor(
            cs,
            num_objectives=1,
            num_constraints=len(spec.constraints),
            initial_trials=surrogate_minimum(spec),     # its own design is never served: ours comes first (propose)
            init_strategy="random",
            surrogate_type=self.surrogate,
            acq_type="eic" if spec.constraints else ("ei" if self.surrogate != "auto" else "auto"),
            acq_optimizer_type="auto",
            task_id="ic_opt",
            output_dir=str(self.workdir or Path(tempfile.gettempdir()) / "ic_opt_openbox"),
            random_state=seed + len(history),
            logger_kwargs={"level": "WARNING", "logdir": None},
        )
        for obs in history:
            advisor.update_observation(_observation(spec, obs, cs, sp, OpenBoxObservation, FAILED, search))
        finite_reference(advisor.history)
        return advisor

    def _openbox(self):
        try:
            from openbox import Advisor, Observation
            from openbox import space as sp
            from openbox.utils.constants import FAILED
        except ImportError as exc:        # vendored, not declared: a path dependency does not survive into a wheel
            raise ImportError(f"strategy {self.name!r} needs the OpenBox vendored in the ic-opt checkout; install it in the same "
                              'resolver call as the package: uv pip install -e ".[em,turbo]" -e vendor/open-box') from exc
        return Advisor, Observation, sp, FAILED


def finite_reference(history) -> None:
    """While ``history`` holds no feasible trial, give the constrained acquisition a finite reference value: the largest
    objective among the successful trials, so that EIC ranks candidates by their chance of being feasible.

    OpenBox's ``Advisor._get_bo_candidates`` passes ``history.get_incumbent_value()`` to EIC as ``eta``, and that is inf
    until a feasible trial exists: on a real 50-point run 417 of 2000 candidates then scored inf and 1583 scored 0, so the
    choice among them was arbitrary (2026-09-28); in the offline comparison the finite value took the OpenBox path from
    15 to 20 of 20 runs finding the optimum. We replace the method on this one ``History`` instance rather than patch
    ``vendor/``; checked against OpenBox 0.9.0 (``tests/ic_opt/test_optimize.py`` fails if the method or its call goes)."""
    if not len(history) or history.get_feasible_count() or not history.get_success_count():
        return
    objectives = history.get_objectives(transform="none", warn_invalid_value=False)[history.get_success_mask(), 0]
    worst = float(objectives.max())
    history.get_incumbent_value = lambda: worst


def grid_suggestions(advisor, spec: Spec, search: SearchScale, taken: set[str], count: int) -> list:
    """OpenBox's own choice of ``count`` configurations, with "already evaluated" judged on the spec's grid.

    ``Advisor.get_suggestions`` takes the acquisition's ranked candidates in order and skips a configuration already in
    the history or the batch; ``get_suggestion`` (a batch of one) first draws a random configuration with probability
    ``rand_prob``. A variable under the log rule is a continuous ``Real`` without ``q``, so OpenBox's comparison of
    configurations misses two that snap onto one grid point: on coarse grids most of its proposals were evaluated points,
    which ``suggest`` replaced with random ones (T17.7, 2026-09-28: 26 to 35 of 45 on a 20-level or 50-level range). Here
    the same steps run on the same advisor, and a candidate is skipped when its grid point is in ``taken`` (the history,
    this batch) or already chosen. Checked against the vendored OpenBox 0.9.0 (``generic_advisor.py``). Fewer than
    ``count`` come back when the 5000 candidates hold fewer new grid points; ``suggest`` completes the batch."""
    history = advisor.history
    advisor.early_stop_perf(history)
    advisor.alter_model(history)
    if count == 1 and advisor.rng.random() < advisor.rand_prob:
        return advisor.sample_random_configs(advisor.config_space, 1, excluded_configs=history.configurations)
    candidates = advisor._get_bo_candidates(history)
    advisor.early_stop_ei(history, challengers=candidates)
    chosen, seen = [], set(taken)
    for config in candidates:
        key = space.point_key(space.snap(spec, search.raw_of([_numbers(spec, config)])[0]))
        if key not in seen:
            seen.add(key)
            chosen.append(config)
            if len(chosen) == count:
                break
    return chosen


def _numbers(spec: Spec, config) -> list[float]:
    """A configuration's values in the order of the spec's variables (OpenBox's own numbers: ``log10`` under the rule)."""
    values = config.get_dictionary()
    return [float(values[v.name]) for v in spec.variables]


def initial_design_size(spec: Spec, initial_trials: int | None = None, budget: int | None = None) -> int:
    """How many observations form the initial design before the surrogate proposes: ``initial_trials`` when given;
    else, with the run's ``budget`` known (``opt.optimize`` passes it), the smaller of twice the number of variables and
    half the budget, so at least half of a small run reaches the surrogate (N-30, 2026-09-27: 12 points on 4 variables
    used to be design throughout); without a budget, twice the variables. At least one."""
    if initial_trials:
        return int(initial_trials)
    variables = max(2 * len(spec.variables), 1)
    return variables if budget is None else max(1, min(variables, int(budget) // 2))


def _observation(spec: Spec, obs: Observation, cs, sp, make, failed_state, search: SearchScale):
    config = sp.Configuration(cs, values=_values(spec, obs.params, search))
    extra = {"obs_id": obs.obs_id, "status": obs.status}
    value = minimization_objective(spec, obs)
    if value is None:       # FAILED: OpenBox fills objective and constraints with the successful trials' column maxima
        return make(config=config, objectives=[math.inf], constraints=[math.nan] * len(spec.constraints) or None,
                    trial_state=failed_state, extra_info=extra)
    return make(config=config, objectives=[value], constraints=_residuals(spec, obs), extra_info=extra)


def _config_space(spec: Spec, sp, search: SearchScale):
    """OpenBox's space: a variable under the log rule (``space.log_scale``) is a continuous ``Real`` between the
    ``log10`` of its bounds, without ``q`` (two proposals that snap onto one grid point are handled by ``suggest``'s
    loop over duplicates); every other one is the grid as it was. Its default is the middle of the range: ConfigSpace
    rounds a default to 10 digits, and ``log10`` of a bound can round outside the range (``log10(0.5)`` does)."""
    cs = sp.Space()
    for index, v in enumerate(spec.variables):
        if search.log[index]:
            lo, hi = float(search.lower[index]), float(search.upper[index])
            cs.add_variable(sp.Real(v.name, lo, hi, default_value=(lo + hi) / 2))
            continue
        lower, _ = space.parse_scalar(v.lower)
        upper, _ = space.parse_scalar(v.upper)
        step, _ = space.parse_scalar(v.step)
        if v.kind is VariableKind.INTEGER:
            cs.add_variable(sp.Int(v.name, int(lower), int(upper), q=int(step), default_value=int(lower)))
        else:
            top = lower + Decimal(int((upper - lower) / step)) * step      # last grid point, never past the bound
            cs.add_variable(sp.Real(v.name, float(lower), float(top), q=float(step), default_value=float(lower)))
    return cs


def _values(spec: Spec, params: dict[str, str], search: SearchScale) -> dict[str, float | int]:
    """A grid point as OpenBox's configuration values: ``log10`` of the value for a variable under the log rule."""
    logged = search.of_raw(space.to_raw(spec, params))[0]
    values = {}
    for index, v in enumerate(spec.variables):
        if search.log[index]:
            values[v.name] = float(logged[index])
            continue
        number = float(space.parse_scalar(params[v.name])[0])
        values[v.name] = int(number) if v.kind is VariableKind.INTEGER else number
    return values


def _residuals(spec: Spec, obs: Observation) -> list[float]:
    """<= 0 means satisfied (OpenBox EIC convention). Only successful trials carry them: every metric is there (a point
    stopped early, which lacks some, is a failed trial: ``base.minimization_objective``)."""
    out = []
    for c in spec.constraints:
        value = float(obs.metrics[c.metric])
        threshold = float(space.parse_scalar(c.value.replace(" ", ""))[0])
        out.append(value - threshold if c.op in ("lt", "le") else threshold - value)
    return out
