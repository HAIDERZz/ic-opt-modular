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
# What ``auto`` resolves to (T17.2, D11 decided by the user 2026-09-28): metric_gp where it applies -- a spec without EM
# devices simulated in the loop, at any corners since T17.9, with devices from library tables since T18.2B -- else the
# strategy that was the default before it.
_AUTO_CHOICES = {"metric_gp": MetricGpSuggester, "openbox_gp_eic": OpenBoxSuggester}
LIBRARY_REASON = "library devices: no EMX in the loop"


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
    """What ``strategy=auto`` runs, and why in words: ``metric_gp`` for a spec without EM devices, whatever the corners
    (T17.9: it takes a run at several), and for one whose devices come from library tables (T18.2B: no EMX in the loop),
    else ``openbox_gp_eic``. ``corners`` (how many the run evaluates) and ``history`` (the rows no corner filter reaches)
    are passed by the callers and no longer decide anything: a history whose points were evaluated at different sets of
    corners is ``metric_gp``'s to refuse, and ``opt.optimize`` hands it none."""
    if stage_one_refusal(spec, corners) == DEVICES_REFUSAL:
        return "openbox_gp_eic", "metric_gp does not take EM devices yet"
    return "metric_gp", LIBRARY_REASON if spec.library_devices else "no EM devices"


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


__all__ = ["AUTO", "LIBRARY_REASON", "MetricGpSuggester", "OpenBoxSuggester", "Proposal", "RandomSuggester", "Suggester",
           "TurboSuggester", "auto_keywords", "check_auto_keywords", "make", "minimization_objective", "penalized_objective",
           "resolve_auto"]
