import json
import multiprocessing
from pathlib import Path

from icopt_bench.cache import EvalCache, cached
from icopt_bench.registry import get

from ic_opt.observation import ChildResult


def test_cache_hit_returns_the_same_children(tmp_path):
    problem = get("syn_small_tight")
    cached_problem = cached(problem, tmp_path)
    params = {"a": "24", "b": "0.8", "c": "40", "d": "340"}
    first = cached_problem.evaluate(params)
    second = cached_problem.evaluate(params)
    assert {u: c.model_dump() for u, c in first.items()} == {u: c.model_dump() for u, c in second.items()}


def test_cache_avoids_recomputing_a_hit(tmp_path):
    problem = get("syn_small_tight")
    calls = []

    def counting(params):
        calls.append(params)
        return problem.evaluate(params)

    import dataclasses
    wrapped = dataclasses.replace(problem, evaluate=counting)
    cached_problem = cached(wrapped, tmp_path)
    params = {"a": "24", "b": "0.8", "c": "40", "d": "340"}
    cached_problem.evaluate(params)
    cached_problem.evaluate(params)
    assert len(calls) == 1


def _write_many(cache_dir: str, start: int, count: int) -> None:
    store = EvalCache("concurrent_test", cache_dir)
    for i in range(start, start + count):
        store.put(str(i), {"tb1": ChildResult(unit="tb1", status="ok", metrics={"x": float(i)})})


def test_cache_survives_two_processes_appending_at_once(tmp_path):
    procs = [multiprocessing.Process(target=_write_many, args=(str(tmp_path), 0, 100)),
             multiprocessing.Process(target=_write_many, args=(str(tmp_path), 100, 100))]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0

    store = EvalCache("concurrent_test", tmp_path)
    for i in range(200):
        hit = store.get(str(i))
        assert hit is not None
        assert hit["tb1"].metrics["x"] == float(i)

    lines = (Path(tmp_path) / "concurrent_test.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 200
    for line in lines:
        json.loads(line)                                              # every line is one complete, valid JSON object
