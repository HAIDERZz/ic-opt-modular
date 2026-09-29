"""T17.12: the threads a run really uses -- the strategy's own computation (N-73) and the extraction process (N-78)."""

from __future__ import annotations

import pytest
from threadpoolctl import threadpool_info

from ic_opt import suggesters
from ic_opt.blocks.optimize import strategy_threads, suggest
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.suggesters.base import Proposal
from tests.ic_opt.fakes import minimal_spec, needs_turbo
from tests.ic_opt.test_optimize import bowl, observed

CAPS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")


def threaded(n: int | None = None, **simulator) -> Spec:
    """The minimal spec, with ``simulator.strategy_threads: n`` when given (and any other simulator field)."""
    d = minimal_spec()
    d["simulator"] = {**d["simulator"], **simulator, **({} if n is None else {"strategy_threads": n})}
    return Spec.model_validate(d)


@pytest.fixture
def no_caps(monkeypatch):
    """None of the thread variables set: the tests run under OMP_NUM_THREADS=1, which would cap every limit at one."""
    for name in CAPS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# -- N-73: the field ------------------------------------------------------------------------------------------------


def test_strategy_threads_is_one_by_default_and_stays_out_of_the_dump_and_the_fingerprint():
    base, four = threaded(), threaded(4)
    assert base.simulator.strategy_threads == 1
    assert "strategy_threads" not in base.model_dump(mode="json")["simulator"]         # the legacy fingerprint stays
    assert threaded(1).model_dump(mode="json") == base.model_dump(mode="json")         # a stated 1 is the default
    assert four.model_dump(mode="json")["simulator"]["strategy_threads"] == 4
    assert four.fingerprint() == base.fingerprint() and "strategy_threads" not in four.problem()["simulator"]
    with pytest.raises(ValueError, match="strategy_threads"):
        threaded(0)


# -- N-73: the limit around the strategy's calls --------------------------------------------------------------------


class Probe:
    """A strategy that proposes nothing and records the threads of the BLAS / OpenMP pools it is called under."""

    name = "probe"

    def __init__(self) -> None:
        self.seen: list[set[int]] = []

    def propose(self, spec, history, n, *, seed, pending=()):
        self.seen.append({pool["num_threads"] for pool in threadpool_info()})
        return Proposal([])


def probe(monkeypatch) -> Probe:
    strategy = Probe()
    monkeypatch.setattr(suggesters, "make", lambda name, **kwargs: strategy)
    return strategy


def test_suggest_calls_the_strategy_under_its_thread_limit_and_puts_the_pools_back(no_caps):
    strategy = probe(no_caps)
    before = [(pool["filepath"], pool["num_threads"]) for pool in threadpool_info()]
    points = suggest(threaded(3), [], 2, strategy="probe", seed=0)
    assert strategy.seen == [{3}] * 4 and len(points) == 2          # every attempt under the limit; random points fill
    assert [(pool["filepath"], pool["num_threads"]) for pool in threadpool_info()] == before


@pytest.mark.parametrize(("field", "variables", "limit"), [
    (3, {}, 3),                                                      # no variable: the field
    (3, {"OMP_NUM_THREADS": "1"}, 1),                                # a lower variable keeps its effect
    (3, {"OPENBLAS_NUM_THREADS": "2", "MKL_NUM_THREADS": "3"}, 2),   # the smallest one set
    (2, {"OMP_NUM_THREADS": "8"}, 2),                                # a higher one is lowered to the field
    (3, {"OMP_NUM_THREADS": "2,1"}, 2),                              # a nested list: its first value
    (2, {"MKL_NUM_THREADS": "x"}, 2),                                # not a count: ignored
])
def test_a_thread_variable_below_the_field_keeps_its_effect(no_caps, field, variables, limit):
    for name, value in variables.items():
        no_caps.setenv(name, value)
    assert strategy_threads(threaded(field)) == limit
    strategy = probe(no_caps)
    suggest(threaded(field), [], 1, strategy="probe", seed=0)
    assert strategy.seen[0] == {limit}


class Fitted(Exception):
    """Raised where TuRBO fits its GP: the test has seen what it needs by then."""


@needs_turbo
def test_turbo_sets_torch_threads_once_per_call_before_it_fits(no_caps):
    import torch
    from turbo import Turbo1

    calls = []
    no_caps.setattr(torch, "set_num_threads", lambda n: calls.append(("threads", n)))

    def fit(*args, **kwargs):
        calls.append(("fit",))
        raise Fitted

    no_caps.setattr(Turbo1, "_create_candidates", fit)
    spec = threaded(2)
    start = [{"F": "20", "W": "0.6u"}, {"F": "30", "W": "1.2u"}, {"F": "24", "W": "1.0u"}, {"F": "26", "W": "0.8u"}]
    history = observed(spec, [Point(p, "start") for p in start], lambda p: bowl(p, "tb", None))   # n_init = 2 x 2 variables
    with pytest.raises(Fitted):
        suggest(spec, history, 2, strategy="turbo", seed=0)
    assert calls == [("threads", 2), ("fit",)]
    calls.clear()
    assert len(suggest(spec, [], 2, strategy="turbo", seed=0)) == 2                  # a design batch: nothing is fitted
    assert calls == [("threads", 2)]
    no_caps.setenv("OMP_NUM_THREADS", "1")                                          # a lower variable reaches torch too
    calls.clear()
    suggest(spec, [], 2, strategy="turbo", seed=0)
    assert calls == [("threads", 1)]
