from __future__ import annotations

import inspect
from collections.abc import Iterable

from ic_opt.observation import Observation
from ic_opt.spec import Spec
from ic_opt.suggesters.base import Proposal, Suggester, minimization_objective, penalized_objective
from ic_opt.suggesters.metric_gp import DEVICES_REFUSAL, MetricGpSuggester, stage_one_refusal
from ic_opt.suggesters.openbox import OpenBoxSuggester
from ic_opt.suggesters.random import RandomSuggester
from ic_opt.suggesters.turbo import TurboSuggester

AUTO = "auto"
# What ``auto`` resolves to (T17.2, D11 decided by the user 2026-09-28): metric_gp inside its stage-1 scope, else the
# strategy that was the default before it.
_AUTO_CHOICES = {"metric_gp": MetricGpSuggester, "openbox_gp_eic": OpenBoxSuggester}


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
    if strategy == AUTO:          # a suggester must name the strategy that proposed its points (origin tags)
        raise ValueError("strategy 'auto' is resolved by opt.suggest / opt.optimize (suggesters.resolve_auto) before a "
                         "suggester is made")
    raise ValueError(f"unknown strategy {strategy!r}")


def resolve_auto(spec: Spec, corners: int, history: Iterable[Observation] = ()) -> tuple[str, str]:
    """What ``strategy=auto`` runs, and why in words: ``metric_gp`` for a run inside stage 1 of T17 (no EM devices, one
    condition; ``stage_one_refusal`` decides it from the ``corners`` the run evaluates), else ``openbox_gp_eic``.
    ``history``: the rows the strategy will be handed that no corner filter reaches (adopted ``initial=`` rows, or all of
    them for ``opt.suggest`` called directly); rows evaluated at more than one corner would make ``metric_gp`` refuse the
    history, so they resolve to ``openbox_gp_eic`` too."""
    refusal = stage_one_refusal(spec, corners)
    if refusal == DEVICES_REFUSAL:
        return "openbox_gp_eic", "metric_gp does not take EM devices yet"
    if refusal:
        return "openbox_gp_eic", f"metric_gp works on one condition; this run covers {corners} corners"
    held = sorted({c.corner or "nominal" for o in history for c in o.children.values()})
    if len(held) > 1:
        return "openbox_gp_eic", ("metric_gp works on one condition; the history holds points evaluated at the corners "
                                  + ", ".join(held))
    return "metric_gp", "no EM devices, one condition"


def auto_keywords(strategy: str) -> list[str]:
    """The keyword arguments a strategy ``auto`` can resolve to takes (its constructor's keyword-only parameters)."""
    parameters = inspect.signature(_AUTO_CHOICES[strategy].__init__).parameters.values()
    return sorted(p.name for p in parameters if p.kind is inspect.Parameter.KEYWORD_ONLY)


def check_auto_keywords(strategy: str, reason: str, kwargs: dict) -> None:
    """Refuse a keyword the strategy ``auto`` resolved to does not take, before anything runs: the constructor's
    TypeError would come from inside the first batch, and dropping the keyword would run something else than asked."""
    taken = auto_keywords(strategy)
    for name in kwargs:
        if name not in taken:
            others = [s for s in _AUTO_CHOICES if s != strategy and name in auto_keywords(s)]
            raise ValueError(
                f"strategy auto resolved to {strategy} ({reason}), which does not take the keyword {name!r} "
                f"(it takes {', '.join(taken)})"
                + (f"; {name!r} is {others[0]}'s: name that strategy (strategy={others[0]}) to pass it" if others else ""))


__all__ = ["AUTO", "MetricGpSuggester", "OpenBoxSuggester", "Proposal", "RandomSuggester", "Suggester", "TurboSuggester",
           "auto_keywords", "check_auto_keywords", "make", "minimization_objective", "penalized_objective", "resolve_auto"]
