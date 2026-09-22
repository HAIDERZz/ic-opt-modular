from ic_opt.suggesters.base import Proposal, Suggester, penalized_objective
from ic_opt.suggesters.openbox import OpenBoxSuggester
from ic_opt.suggesters.random import RandomSuggester
from ic_opt.suggesters.turbo import TurboSuggester


def make(strategy: str, *, failure_penalty: float = 1e6, **kwargs) -> Suggester:
    """Strategy name -> suggester. Legacy names stay valid."""
    if strategy in ("turbo", "turbo_trust_region"):
        return TurboSuggester(failure_penalty=failure_penalty, **kwargs)
    if strategy.startswith("openbox"):
        return OpenBoxSuggester(strategy, failure_penalty=failure_penalty, **kwargs)
    if strategy in ("random", "random_baseline"):
        return RandomSuggester("random")
    if strategy in ("sobol", "latin_hypercube"):
        return RandomSuggester(strategy)
    raise ValueError(f"unknown strategy {strategy!r}")


__all__ = ["OpenBoxSuggester", "Proposal", "RandomSuggester", "Suggester", "TurboSuggester", "make", "penalized_objective"]
