from ic_opt.suggesters.base import Proposal, Suggester, minimization_objective, penalized_objective
from ic_opt.suggesters.metric_gp import MetricGpSuggester
from ic_opt.suggesters.openbox import OpenBoxSuggester
from ic_opt.suggesters.random import RandomSuggester
from ic_opt.suggesters.turbo import TurboSuggester


def make(strategy: str, *, failure_penalty: float | None = None, **kwargs) -> Suggester:
    """Strategy name -> suggester. Legacy names stay valid. ``failure_penalty`` is accepted and ignored: since T17.0b no
    penalty number reaches a model (``suggesters.base``)."""
    if strategy in ("turbo", "turbo_trust_region"):
        return TurboSuggester(**kwargs)
    if strategy == "metric_gp":
        return MetricGpSuggester(**kwargs)
    if strategy.startswith("openbox"):
        return OpenBoxSuggester(strategy, **kwargs)
    if strategy in ("random", "random_baseline"):
        return RandomSuggester("random")
    if strategy in ("sobol", "latin_hypercube"):
        return RandomSuggester(strategy)
    raise ValueError(f"unknown strategy {strategy!r}")


__all__ = ["MetricGpSuggester", "OpenBoxSuggester", "Proposal", "RandomSuggester", "Suggester", "TurboSuggester", "make", "minimization_objective",
           "penalized_objective"]
