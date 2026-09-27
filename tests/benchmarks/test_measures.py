from icopt_bench.measures import best_at, compare, first_feasible, summarize, violation_at


def _point(index, *, feasible=False, objective=None, penalty=0.0, status="ok"):
    return {"index": index, "feasible": feasible, "objective": objective, "constraint_penalty": penalty, "status": status,
            "params": {}, "origin": "test", "fom": objective, "metrics": {}, "missing": []}


def _result(problem, method, seed, points, *, budget=6, error=None, suggest_seconds=None):
    return {"problem": problem, "method": method, "seed": seed, "budget": budget, "batch": 2, "n_init": 2, "dim": 1,
            "grid_size": "10", "family": "synthetic", "scenario": "wide_range", "points": points,
            "suggest_seconds": suggest_seconds if suggest_seconds is not None else [0.1, 0.1, 0.1],
            "evaluate_seconds": [0.0, 0.0, 0.0], "wall_seconds": 0.3, "error": error,
            "versions": {"ic_opt": "0", "numpy": "0", "scipy": "0", "sklearn": "0", "python": "0"}}


def test_first_feasible_finds_the_first_hit():
    points = [_point(1), _point(2), _point(3, feasible=True), _point(4, feasible=True)]
    assert first_feasible(_result("p", "m", 0, points)) == 3


def test_first_feasible_is_none_without_a_hit():
    points = [_point(1), _point(2)]
    assert first_feasible(_result("p", "m", 0, points)) is None


def test_best_at_takes_the_smallest_feasible_objective_before_n():
    points = [_point(1, feasible=True, objective=5.0), _point(2, feasible=True, objective=1.0),
              _point(3, feasible=True, objective=-9.0)]
    assert best_at(_result("p", "m", 0, points), n=3) == 1.0            # index 3 excluded (index < n)
    assert best_at(_result("p", "m", 0, points), n=4) == -9.0


def test_best_at_is_none_without_a_feasible_point():
    points = [_point(1), _point(2)]
    assert best_at(_result("p", "m", 0, points), n=3) is None


def test_violation_at_takes_the_smallest_penalty_among_ok_or_constraint_failed():
    points = [_point(1, status="constraint_failed", penalty=2.0), _point(2, status="constraint_failed", penalty=0.5),
              _point(3, status="failed:sim", penalty=0.0)]               # excluded: not ok / constraint_failed
    assert violation_at(_result("p", "m", 0, points), n=3) == 0.5


def test_violation_at_is_none_when_nothing_qualifies():
    points = [_point(1, status="failed:sim")]
    assert violation_at(_result("p", "m", 0, points), n=2) is None


def _seeded_results(problem, method, per_seed_points, budget=6):
    return [_result(problem, method, seed, points, budget=budget) for seed, points in enumerate(per_seed_points)]


def test_summarize_counts_success_rate_and_missing_feasible_as_budget_plus_one():
    hit = [_point(1), _point(2, feasible=True, objective=1.0)]
    miss = [_point(1), _point(2)]
    results = _seeded_results("p", "m", [hit, miss], budget=6)
    rows = summarize(results, budgets=(2, 3, 6))
    row = rows[0]
    assert row["problem"] == "p" and row["method"] == "m"
    assert row["n_seeds"] == 2
    assert row["first_feasible"]["median"] == (2 + 7) / 2               # one run hits at 2, one never (-> 6+1=7)
    assert row["success_rate"][2] == 0.5
    assert row["best_at"][2]["median"] is None                          # best_at(n=2) only sees index < 2 (index 1)
    assert row["best_at"][3]["median"] == 1.0                           # best_at(n=3) sees index < 3, including index 2
    assert row["best_at"][3]["n_missing"] == 1


def test_summarize_counts_errors():
    ok_run = _result("p", "m", 0, [_point(1, feasible=True, objective=0.0)])
    failed_run = _result("p", "m", 1, [], error="boom")
    rows = summarize([ok_run, failed_run], budgets=(6,))
    assert rows[0]["n_errors"] == 1
    assert rows[0]["n_seeds"] == 2


def test_summarize_groups_by_problem_and_method():
    a = _result("p1", "m1", 0, [_point(1, feasible=True, objective=0.0)])
    b = _result("p1", "m2", 0, [_point(1, feasible=True, objective=0.0)])
    c = _result("p2", "m1", 0, [_point(1, feasible=True, objective=0.0)])
    rows = summarize([a, b, c], budgets=(6,))
    assert {(r["problem"], r["method"]) for r in rows} == {("p1", "m1"), ("p1", "m2"), ("p2", "m1")}


def test_compare_three_verdicts():
    # "better": candidate finds feasible points sooner and with tighter spread, well past half the IQR.
    better_base = _seeded_results("better", "base", [[_point(i, feasible=(i >= 5)) for i in range(1, 7)] for _ in range(5)])
    better_cand = _seeded_results("better", "cand", [[_point(i, feasible=(i >= 1)) for i in range(1, 7)] for _ in range(5)])
    # "worse": the reverse.
    worse_base = _seeded_results("worse", "base", [[_point(i, feasible=(i >= 1)) for i in range(1, 7)] for _ in range(5)])
    worse_cand = _seeded_results("worse", "cand", [[_point(i, feasible=(i >= 5)) for i in range(1, 7)] for _ in range(5)])
    # "within noise": identical runs both sides.
    same_points = [[_point(i, feasible=(i >= 3)) for i in range(1, 7)] for _ in range(5)]
    noise_base = _seeded_results("noise", "base", same_points)
    noise_cand = _seeded_results("noise", "cand", same_points)

    rows = summarize(better_base + better_cand + worse_base + worse_cand + noise_base + noise_cand, budgets=(6,))
    verdicts = {v["problem"]: v for v in compare(rows, "base", "cand")}

    assert verdicts["better"]["measures"]["first_feasible"] == "better"
    assert verdicts["worse"]["measures"]["first_feasible"] == "worse"
    assert verdicts["noise"]["measures"]["first_feasible"] == "within noise"


def test_compare_skips_problems_missing_one_of_the_two_methods():
    only_base = _seeded_results("solo", "base", [[_point(1, feasible=True, objective=0.0)]], budget=1)
    rows = summarize(only_base, budgets=(1,))
    assert compare(rows, "base", "cand") == []
