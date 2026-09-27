"""Conditions 1-3 of D11 on made-up runs."""

from __future__ import annotations

from icopt_bench.gates import gates, judge, write_md


def _run(problem: str, method: str, seed: int, feasible_at: dict[int, float], budget: int = 200) -> dict:
    """A run whose only feasible points are ``feasible_at`` (index -> objective)."""
    points = [{"index": i, "feasible": i in feasible_at, "objective": feasible_at.get(i), "status": "ok",
               "constraint_penalty": 0.0} for i in range(1, budget + 1)]
    return {"problem": problem, "method": method, "seed": seed, "budget": budget, "points": points}


def _runs(method: str, firsts: list[int | None], value: float = 1.0) -> list[dict]:
    return [_run("p", method, s, {} if f is None else {f: value}) for s, f in enumerate(firsts)]


def test_a_clear_gain_is_a_step_forward_and_no_step_back():
    results = _runs("old", [60, 70, 80, 90, 100, 110, 120, 130, None, None]) + \
        _runs("new", [11, 12, 13, 14, 15, 16, 17, 18, 19, 20])
    (row,) = gates(results, "old", "new")
    ff = row["measures"]["first_feasible"]
    assert ff["baseline_median"] == 110 and ff["candidate_median"] == 16      # the value of a run, not between two
    assert ff["baseline_p75"] == 130 and ff["candidate_p75"] == 18          # two of ten runs are worse than it
    assert row["no_step_back"] and row["step_forward"] and row["steady"] and not row["step_back_beyond_seeds"]
    assert row["measures"]["best_at@50"]["baseline_median"] is None
    assert row["measures"]["best_at@50"]["step_forward"]


def test_the_same_runs_are_neither_forward_nor_back():
    firsts = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    (row,) = gates(_runs("old", firsts) + _runs("new", firsts), "old", "new")
    assert row["no_step_back"] and row["steady"] and not row["step_forward"] and not row["step_back_beyond_seeds"]


def test_a_difference_within_what_the_seeds_differ_is_not_a_step_forward():
    old = [10, 30, 50, 70, 90, 110, 130, 150, 170, 190]
    new = [8, 28, 48, 68, 88, 108, 128, 148, 168, 188]
    (row,) = gates(_runs("old", old) + _runs("new", new), "old", "new")
    assert row["no_step_back"] and not row["step_forward"]


def test_a_method_that_rarely_succeeds_does_not_pass_on_its_lucky_runs():
    old = [50] * 10
    new = [5, 5, None, None, None, None, None, None, None, None]
    (row,) = gates(_runs("old", old) + _runs("new", new), "old", "new")
    ff = row["measures"]["first_feasible"]
    assert ff["candidate_median"] is None and not ff["no_step_back"] and not ff["steady"]
    assert row["step_back_beyond_seeds"] and not row["step_forward"]


def test_a_worse_tail_fails_only_the_third_condition():
    old = [20, 20, 20, 20, 20, 20, 20, 20, 20, 20]
    new = [10, 10, 10, 10, 10, 10, 10, 150, 150, 150]
    (row,) = gates(_runs("old", old) + _runs("new", new), "old", "new")
    ff = row["measures"]["first_feasible"]
    assert ff["no_step_back"] and not ff["steady"]


def test_all_runs_at_one_value_on_both_sides():
    j = judge([1.0] * 5, [1.0] * 5)
    assert j["no_step_back"] and j["steady"] and not j["step_forward"] and not j["step_back_beyond_seeds"]


def test_a_problem_with_one_method_only_is_left_out(tmp_path):
    results = _runs("old", [10, 20]) + [_run("q", "old", 0, {5: 1.0})]
    assert gates(results, "old", "new") == []
    rows = gates(results + _runs("new", [10, 20]), "old", "new")
    assert [r["problem"] for r in rows] == ["p"]
    write_md(rows, "old", "new", tmp_path / "gates.md")
    text = (tmp_path / "gates.md").read_text()
    assert "| p | first_feasible | 20 / 20 |" in text and "Problems: 1." in text
