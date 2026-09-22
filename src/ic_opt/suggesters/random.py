"""Space-filling / random baseline suggester (sobol, latin_hypercube, random)."""

from __future__ import annotations

from ic_opt.observation import Observations
from ic_opt.spec import Spec
from ic_opt.suggesters.base import Proposal, scale, unit_design


class RandomSuggester:
    def __init__(self, method: str = "sobol") -> None:
        self.method = method
        self.name = f"random:{method}"

    def propose(self, spec: Spec, history: Observations, n: int, *, seed: int) -> Proposal:
        # Offsetting the seed by the history size keeps successive batches distinct.
        unit = unit_design(self.method, n, len(spec.variables), seed + len(history))
        return Proposal(scale(unit, spec), tag=self.method)
