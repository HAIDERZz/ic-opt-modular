"""T17.12: the threads a run really uses -- the strategy's own computation (N-73) and the extraction process (N-78)."""

from __future__ import annotations

import re

import pytest
from threadpoolctl import threadpool_info

from ic_opt import suggesters
from ic_opt.blocks.doctor import _envelope_check, plan_line
from ic_opt.blocks.evaluate import plan_shape
from ic_opt.blocks.optimize import strategy_threads, suggest
from ic_opt.deck import Deck
from ic_opt.eval import engine
from ic_opt.executor import LocalExecutor
from ic_opt.recipe import Run
from ic_opt.site import EXTRACTION_NOTE, EnvelopeError, HostLimits, Site
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.stages import spectre_pipeline
from ic_opt.stages.em_chain import emx_stages
from ic_opt.store import RunStore
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


# -- N-78: the extraction process counts ----------------------------------------------------------------------------


def devices_spec(*, testbenches: bool, threads_per_run: int = 1, em_threads: int = 4) -> Spec:
    """One inductor under EMX (``em.threads``, 64 GB per run), with the minimal spec's testbench or without any."""
    d = minimal_spec(simulator={"parallel_jobs": 2, "threads_per_run": threads_per_run, "timeout_s": 60})
    d["devices"] = [{"id": "ind", "generator": "clean_port_ind_sym", "profile": "demo_6m", "ports": ["P1", "N1"]}]
    d["em"] = {"process_file": "/site/demo.proc", "frequencies": [1e10], "threads": em_threads, "memory_gb": 64,
               "timeout_s": 600}
    if not testbenches:
        d.update(testbenches=[], metrics=[], constraints=[], objective=None)
    return Spec.model_validate(d)


def test_the_doctor_envelope_counts_threads_per_run_plus_one_and_names_it_when_it_no_longer_fits():
    spec = threaded(parallel_jobs=10, threads_per_run=1)
    fits = _envelope_check(spec, HostLimits(max_threads=20, max_memory_gb=64), "local")
    assert fits.ok and fits.detail == ("10 jobs × (1 + 1) threads / 0 GB per job → 20 threads / 0 GB of 20 / 64 "
                                       "(max_threads / max_memory_gb of local)")
    refused = _envelope_check(spec, HostLimits(max_threads=16, max_memory_gb=64), "local")    # 10 x 1 fitted before
    assert not refused.ok and refused.detail == (
        "10 jobs × (1 + 1) threads / 0 GB per job → 20 threads / 0 GB of 16 / 64 (max_threads / max_memory_gb of local); "
        "each testbench job is counted as threads_per_run + 1 threads: Spectre at one thread runs at up to two cores at "
        "times, and the metric extraction takes up to two cores for a moment after each simulation")


def test_the_workers_fit_threads_per_run_plus_one_and_a_job_that_no_longer_fits_is_refused(tmp_path):
    spec = threaded(parallel_jobs=10, threads_per_run=2)
    pipeline = spectre_pipeline(spec, Deck())
    assert engine.workers_for(spec, pipeline, None, HostLimits(max_threads=16, max_memory_gb=64)) == 5    # 16 // (2 + 1)
    assert engine.workers_for(spec, pipeline, None, HostLimits(max_threads=96, max_memory_gb=64)) == 10   # parallel_jobs
    assert plan_shape(spec, pipeline, "all", LocalExecutor(tmp_path), None, HostLimits(max_threads=16, max_memory_gb=64)) == (
        "(1 testbench sims) = 1 simulations per point on local, 5 workers × (2 + 1) threads (spectre)")
    whole = threaded(parallel_jobs=1, threads_per_run=4)                   # the whole host: it fitted before
    with pytest.raises(EnvelopeError, match=re.escape(
            "a testbench job needs 4 + 1 threads but the executor host allows max_threads 4 (site.yaml): " + EXTRACTION_NOTE)):
        engine.workers_for(whole, spectre_pipeline(whole, Deck()), None, HostLimits(max_threads=4, max_memory_gb=64))


def test_the_header_prints_the_per_job_figure_and_the_strategy_threads_apart_from_the_peak(tmp_path):
    spec = threaded(4, parallel_jobs=10, threads_per_run=1)
    assert plan_line(spec, LocalExecutor(tmp_path), HostLimits(max_threads=96, max_memory_gb=64)) == (
        "host=local jobs=10 × (1 + 1) threads → peak_threads=20 (max_threads 96), strategy 4 threads between batches, "
        "budget=10 sims, preset=ax")


def test_run_jobs_trims_to_threads_per_run_plus_one_and_names_it_when_it_no_longer_fits(tmp_path):
    hosts = Site({"local": HostLimits(max_threads=8, max_memory_gb=16), "big": HostLimits(max_threads=27, max_memory_gb=16)})

    def run(spec: Spec, host: str) -> Run:
        return Run(tmp_path, spec, RunStore(tmp_path), LocalExecutor(tmp_path / "sims"), None, hosts, hosts.host(host))

    spec = threaded(parallel_jobs=4, threads_per_run=8)
    with pytest.raises(EnvelopeError, match=re.escape("simulator.threads_per_run (8 + 1) exceeds max_threads 8 of host "
                                                      "'local'")) as refused:
        _ = run(spec, "local").jobs
    assert EXTRACTION_NOTE in str(refused.value)
    assert run(spec, "big").jobs == 3                                                   # 27 // (8 + 1)
    assert run(devices_spec(testbenches=False, threads_per_run=8), "local").jobs == 1  # no testbench, no extraction


def test_a_spec_with_devices_keeps_the_emx_accounting(tmp_path):
    only = devices_spec(testbenches=False)                                    # em.threads 4, 64 GB per run
    check = _envelope_check(only, HostLimits(max_threads=64, max_memory_gb=128), "local")
    assert check.ok and check.detail.startswith("2 jobs × 4 threads / 64 GB per job → 8 threads / 128 GB of 64 / 128")
    assert engine.workers_for(only, emx_stages(only), None, HostLimits(max_threads=4, max_memory_gb=128)) == 1
    assert plan_line(only, LocalExecutor(tmp_path), HostLimits(max_threads=64, max_memory_gb=128)).startswith(
        "host=local jobs=2 × 4 threads → peak_threads=8 (max_threads 64)")
    both = devices_spec(testbenches=True, threads_per_run=1)                  # EMX's 4 above the testbench job's 1 + 1
    assert _envelope_check(both, HostLimits(max_threads=64, max_memory_gb=128), "local").detail.startswith(
        "2 jobs × 4 threads / 64 GB per job → 8 threads / 128 GB")
    wide = devices_spec(testbenches=True, threads_per_run=4)                  # the testbench job's 4 + 1 above EMX's 4
    assert _envelope_check(wide, HostLimits(max_threads=64, max_memory_gb=128), "local").detail.startswith(
        "2 jobs × (4 + 1) threads / 64 GB per job → 10 threads / 128 GB")
    pipeline = [*emx_stages(wide), *spectre_pipeline(wide, Deck())]
    assert engine.workers_for(wide, pipeline, None, HostLimits(max_threads=9, max_memory_gb=1024)) == 1    # 9 // (4 + 1)
