"""OpenBox suggester (GP / PRF surrogate, EIC acquisition), replayed from the observation list."""

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path

from ic_opt import space
from ic_opt.observation import Observation, Observations
from ic_opt.spec import Spec, VariableKind
from ic_opt.suggesters.base import Proposal, penalized_objective

SURROGATES = {"openbox_gp_eic": "gp", "openbox_prf_eic": "prf", "openbox_auto": "auto"}


class OpenBoxSuggester:
    def __init__(self, strategy: str = "openbox_gp_eic", *, failure_penalty: float = 1e6, initial_trials: int | None = None,
                 initialization: str = "sobol", workdir: Path | None = None) -> None:
        if strategy not in SURROGATES:
            raise ValueError(f"unknown OpenBox strategy {strategy!r}; expected one of {sorted(SURROGATES)}")
        self.name = strategy
        self.surrogate = SURROGATES[strategy]
        self.failure_penalty = failure_penalty
        self.initial_trials = initial_trials
        self.initialization = initialization
        self.workdir = workdir

    def propose(self, spec: Spec, history: Observations, n: int, *, seed: int) -> Proposal:
        from openbox import Advisor
        from openbox import Observation as OpenBoxObservation
        from openbox import space as sp

        cs = _config_space(spec, sp)
        advisor = Advisor(
            cs,
            num_objectives=1,
            num_constraints=len(spec.constraints),
            initial_trials=self.initial_trials or max(2 * len(spec.variables), 1),
            init_strategy=self.initialization,
            surrogate_type=self.surrogate,
            acq_type="eic" if spec.constraints else ("ei" if self.surrogate != "auto" else "auto"),
            acq_optimizer_type="auto",
            task_id="ic_opt",
            output_dir=str(self.workdir or Path(tempfile.gettempdir()) / "ic_opt_openbox"),
            random_state=seed,
            logger_kwargs={"level": "WARNING", "logdir": None},
        )
        for obs in history:
            config = sp.Configuration(cs, values=_values(spec, obs.params))
            advisor.update_observation(OpenBoxObservation(
                config=config, objectives=[penalized_objective(obs, self.failure_penalty)],
                constraints=_residuals(spec, obs), extra_info={"obs_id": obs.obs_id, "status": obs.status},
            ))
        suggestions = advisor.get_suggestions(batch_size=n) if n > 1 else [advisor.get_suggestion()]
        raw = [[float(s.get_dictionary()[v.name]) for v in spec.variables] for s in suggestions]
        return Proposal(raw, tag=self.name)


def _config_space(spec: Spec, sp):
    cs = sp.Space()
    for v in spec.variables:
        lower, _ = space.parse_scalar(v.lower)
        upper, _ = space.parse_scalar(v.upper)
        step, _ = space.parse_scalar(v.step)
        if v.kind is VariableKind.INTEGER:
            cs.add_variable(sp.Int(v.name, int(lower), int(upper), q=int(step), default_value=int(lower)))
        else:
            top = lower + Decimal(int((upper - lower) / step)) * step      # last grid point, never past the bound
            cs.add_variable(sp.Real(v.name, float(lower), float(top), q=float(step), default_value=float(lower)))
    return cs


def _values(spec: Spec, params: dict[str, str]) -> dict[str, float | int]:
    values = {}
    for v in spec.variables:
        number = float(space.parse_scalar(params[v.name])[0])
        values[v.name] = int(number) if v.kind is VariableKind.INTEGER else number
    return values


def _residuals(spec: Spec, obs: Observation) -> list[float]:
    """<= 0 means satisfied (OpenBox EIC convention); 1.0 when the metric is unavailable."""
    out = []
    for c in spec.constraints:
        if c.metric not in obs.metrics:
            out.append(1.0)
            continue
        value = float(obs.metrics[c.metric])
        threshold = float(space.parse_scalar(c.value.replace(" ", ""))[0])
        out.append(value - threshold if c.op in ("lt", "le") else threshold - value)
    return out
