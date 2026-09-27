import json

import pytest
from icopt_bench.loop import main, run_one
from icopt_bench.registry import get
from icopt_bench.synthetic import PROBLEMS

_SCHEMA_KEYS = {"problem", "method", "seed", "budget", "batch", "n_init", "dim", "grid_size", "family", "scenario",
                "points", "suggest_seconds", "evaluate_seconds", "wall_seconds", "error", "versions"}
_POINT_KEYS = {"index", "params", "origin", "status", "feasible", "objective", "fom", "constraint_penalty", "metrics",
               "missing"}


@pytest.mark.parametrize("method", ["random", "sobol"])
def test_run_one_schema(method):
    result = run_one(PROBLEMS["syn_small_tight"](), method, seed=0, budget=30, batch=10)
    assert set(result) == _SCHEMA_KEYS
    assert result["problem"] == "syn_small_tight"
    assert result["budget"] == 30
    assert result["batch"] == 10
    assert result["dim"] == 4
    assert result["grid_size"] == "504"
    assert result["error"] is None
    assert len(result["points"]) == 30
    for point in result["points"]:
        assert set(point) == _POINT_KEYS
    assert len(result["suggest_seconds"]) == 3                        # 3 batches of 10
    assert {"ic_opt", "numpy", "scipy", "sklearn", "python"} <= set(result["versions"])


@pytest.mark.parametrize("method", ["random", "sobol"])
def test_run_one_is_deterministic_and_has_no_repeated_points(method):
    r1 = run_one(PROBLEMS["syn_small_tight"](), method, seed=0, budget=30, batch=10)
    r2 = run_one(PROBLEMS["syn_small_tight"](), method, seed=0, budget=30, batch=10)
    params1 = [p["params"] for p in r1["points"]]
    params2 = [p["params"] for p in r2["points"]]
    assert params1 == params2
    keys = [tuple(sorted(p.items())) for p in params1]
    assert len(set(keys)) == len(keys)


def test_run_one_default_n_init():
    result = run_one(PROBLEMS["syn_small_tight"](), "random", seed=0, budget=5, batch=5)
    assert result["n_init"] == min(2 * 4, 20)                          # dim=4 -> 8


def test_run_one_records_explicit_n_init():
    result = run_one(PROBLEMS["syn_small_tight"](), "random", seed=0, budget=5, batch=5, n_init=3)
    assert result["n_init"] == 3


def test_cli_writes_atomic_result_file(tmp_path):
    code = main(["syn_small_tight", "random", "0", "--budget", "10", "--batch", "10", "--out", str(tmp_path)])
    assert code == 0
    dest = tmp_path / "syn_small_tight__random__0.json"
    assert dest.exists()
    assert not list(tmp_path.glob("*.tmp*"))                          # no leftover temp file
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data["error"] is None
    assert len(data["points"]) == 10


def test_run_one_records_an_exception_as_error():
    problem = get("syn_small_tight")

    def broken(params):
        raise RuntimeError("boom")

    import dataclasses
    broken_problem = dataclasses.replace(problem, evaluate=broken)
    result = run_one(broken_problem, "random", seed=0, budget=10, batch=10)
    assert result["error"] is not None
    assert "boom" in result["error"]
    assert result["points"] == []
