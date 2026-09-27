"""Test-only problem, registered into ``icopt_bench.registry`` through the ``ICOPT_BENCH_EXTRA_PROBLEMS`` environment
variable (a module path whose ``PROBLEMS`` dict is merged in) -- ``test_sweep.py``'s only way to exercise
``sweep.py``'s per-job timeout: real subprocesses, real polling, a problem that sleeps on demand
(``ICOPT_BENCH_TEST_SLEEP_S``, read at evaluate time so the test controls it without editing this file).
"""

from __future__ import annotations

import os
import time

from icopt_bench.problem import Problem, child, make_spec


def _build_slow() -> Problem:
    variables = [{"name": "x", "kind": "integer", "lower": "0", "upper": "9", "step": "1"}]
    metrics = [{"name": "f", "unit": "1", "testbench": "tb1"}]
    spec = make_spec("test_slow", variables=variables, metrics=metrics, constraints=[],
                      objective={"direction": "minimize", "expression": "f"}, description="test-only: sleeps on evaluate")

    def evaluate(params: dict[str, str]) -> dict:
        time.sleep(float(os.environ.get("ICOPT_BENCH_TEST_SLEEP_S", "0")))
        return {"tb1/nominal": child("tb1", {"f": float(params["x"])})}

    return Problem(name="test_slow", family="synthetic", scenario="wide_range", spec=spec, evaluate=evaluate,
                    start=({"x": "0"},), reference=0.0)


PROBLEMS = {"test_slow": _build_slow}
