"""The recipe that turns a survey into benchmark problems (plan section 4.3), on made-up surveys."""

from __future__ import annotations

import math

import numpy as np
import pytest
from icopt_bench import calibrate as cal

from ic_opt.space import parse_scalar

VARIABLES = [
    {"name": "w", "kind": "continuous_step", "lower": "0.5", "upper": "10", "step": "0.01"},
    {"name": "l", "kind": "continuous_step", "lower": "0.5", "upper": "5", "step": "0.01"},
    {"name": "m", "kind": "integer", "lower": "1", "upper": "50", "step": "1"},
    {"name": "ib", "kind": "continuous_step", "lower": "1u", "upper": "30u", "step": "0.1u"},
]
TARGETS = [{"metric": "GAIN", "direction": "ge"}, {"metric": "POWER", "direction": "le"}]
OBJECTIVE = {"direction": "maximize", "expression": "GAIN/POWER"}


def _survey(n: int = 2048, dead_share: float = 0.3, seed: int = 0) -> list[dict]:
    """GAIN rises with w, POWER rises with m; ``l`` and ``ib`` do nothing. A share of the points gives no GAIN."""
    rng = np.random.default_rng(seed)
    rows = []
    for point in cal.survey_points("made_up", VARIABLES, n):
        w, m = float(point["w"]), float(point["m"])
        metrics = {"POWER": 0.1 * m + 0.01 * rng.random()}
        if rng.random() >= dead_share:
            metrics["GAIN"] = 10 * math.log10(w) + 40 + 0.1 * rng.random()
        rows.append({"params": point, "metrics": metrics})
    return rows


def test_survey_points_lie_on_the_grid_and_cover_both_coordinates():
    points = cal.survey_points("made_up", VARIABLES, 2048)
    assert 2000 < len(points) <= 2048
    assert len({tuple(p.values()) for p in points}) == len(points)
    for p in points:
        assert p["ib"].endswith("u") and 1 <= float(p["ib"][:-1]) <= 30
        assert float(p["m"]) == int(p["m"]) and 1 <= int(p["m"]) <= 50
        assert abs(float(p["w"]) * 100 - round(float(p["w"]) * 100)) < 1e-9
    m = np.array([int(p["m"]) for p in points])
    # uniform in m puts 1/5 of the points below 10.8; uniform in log m puts 3/5 there: the mix sits between
    assert 0.3 < np.mean(m[:1024] < 10.8) + np.mean(m[1024:] < 10.8) < 1.0
    assert np.mean(m[:1024] <= 10) < 0.3 < np.mean(m[1024:] <= 10)


def test_survey_points_are_the_same_every_time_and_differ_between_circuits():
    assert cal.survey_points("a", VARIABLES, 64) == cal.survey_points("a", VARIABLES, 64)
    assert cal.survey_points("a", VARIABLES, 64) != cal.survey_points("b", VARIABLES, 64)


def test_the_thresholds_leave_the_stated_share_of_the_survey_feasible():
    rows = _survey()
    found = cal.calibrate(rows, VARIABLES, ["GAIN", "POWER"], TARGETS, OBJECTIVE)
    need = math.ceil(cal.FEASIBLE_SHARE * len(rows))
    assert found.total == len(rows) and found.working < found.total
    assert need <= found.feasible <= need + 3                 # rounding a threshold can only let a few more in
    by_metric = {c["metric"]: c for c in found.constraints}
    assert by_metric["GAIN"]["op"] == "ge" and by_metric["POWER"]["op"] == "le"
    count = sum(1 for r in rows if "GAIN" in r["metrics"]
                and r["metrics"]["GAIN"] >= float(by_metric["GAIN"]["value"])
                and r["metrics"]["POWER"] <= float(by_metric["POWER"]["value"]))
    assert count == found.feasible


def test_one_step_stricter_would_leave_too_few():
    rows = _survey()
    found = cal.calibrate(rows, VARIABLES, ["GAIN", "POWER"], TARGETS, OBJECTIVE)
    working = [r for r in rows if "GAIN" in r["metrics"]]
    columns = {m: np.array([r["metrics"][m] for r in working]) for m in ("GAIN", "POWER")}
    stricter = cal._thresholds(columns, TARGETS, found.quantile + 1 / cal.QUANTILE_GRID)
    assert sum(cal._meets(r["metrics"], stricter) for r in working) < math.ceil(cal.FEASIBLE_SHARE * len(rows))


def test_the_reference_design_is_the_best_feasible_point_of_the_survey():
    rows = _survey()
    found = cal.calibrate(rows, VARIABLES, ["GAIN", "POWER"], TARGETS, OBJECTIVE)
    feasible = [r for r in rows if cal._meets(r["metrics"], found.constraints)]
    best = max(feasible, key=lambda r: r["metrics"]["GAIN"] / r["metrics"]["POWER"])
    assert found.reference == best["params"]
    assert found.reference_objective == pytest.approx(best["metrics"]["GAIN"] / best["metrics"]["POWER"])


def test_the_variables_that_matter_are_chosen_for_fine_tuning():
    found = cal.calibrate(_survey(), VARIABLES, ["GAIN", "POWER"], TARGETS, OBJECTIVE)
    assert found.influence["w"] > 0.9 and found.influence["m"] > 0.9
    assert found.influence["l"] < 0.2 and found.influence["ib"] < 0.2
    assert found.fine_variables[:2] in (("w", "m"), ("m", "w"))
    assert set(found.fine_ranges) == set(found.fine_variables)


@pytest.mark.parametrize("name,reference,expected", [
    ("w", "5", ("3.5", "6.5", "0.5")),              # a tenth of the value, three levels to each side
    ("w", "0.6", ("0.54", "0.78", "0.06")),         # cut at the lower bound 0.5: one level below
    ("m", "4", ("3", "5", "1")),                    # an integer keeps at least one level
    ("m", "1", ("1", "2", "1")),
    ("m", "50", ("35", "50", "5")),                 # cut at the upper bound
    ("ib", "10u", ("7u", "13u", "1u")),
])
def test_fine_range(name, reference, expected):
    variable = next(v for v in VARIABLES if v["name"] == name)
    found = cal.fine_range(variable, reference)
    assert found == expected
    lower, upper, step = (parse_scalar(t)[0] for t in found)
    assert (parse_scalar(reference)[0] - lower) % step == 0 and (upper - lower) % step == 0


def test_a_variable_with_no_room_is_not_chosen():
    pinned = {"name": "x", "kind": "integer", "lower": "3", "upper": "3", "step": "1"}
    assert cal.fine_range(pinned, "3") is None


def test_too_few_working_points_is_a_stated_reason():
    rows = _survey(dead_share=0.99)
    with pytest.raises(cal.NotCalibrated, match="give every metric"):
        cal.calibrate(rows, VARIABLES, ["GAIN", "POWER"], TARGETS, OBJECTIVE)


def test_thresholds_are_loosened_never_tightened():
    assert cal._loosened(61.2789, "ge") == "61.2"
    assert cal._loosened(61.2789, "le") == "61.3"
    assert cal._loosened(-61.2789, "ge") == "-61.3"
    assert cal._loosened(-61.2789, "le") == "-61.2"
    assert cal._loosened(1.23456e-5, "le") == "0.0000124"
    assert cal._loosened(1.23456e9, "ge") == "1230000000"
    assert cal._loosened(0.0, "ge") == "0"


def test_split_holds_out_two_of_five_within_each_kind():
    circuits = [(f"amp{i:02d}", "amplifier", 10 + i) for i in range(16)] + [(f"ldo{i}", "ldo", 20 + i) for i in range(4)]
    development, held = cal.split(circuits, seed=20260928)
    assert (development, held) == cal.split(list(reversed(circuits)), seed=20260928)      # the order given is nothing
    assert set(development) | set(held) == {c[0] for c in circuits} and not set(development) & set(held)
    assert sum(h.startswith("amp") for h in held) == 6 and sum(h.startswith("ldo") for h in held) == 2
    for start in range(0, 15, 5):                                                          # each size group gives two
        group = {f"amp{i:02d}" for i in range(start, start + 5)}
        assert len(group & set(held)) == 2
    assert cal.split(circuits, seed=1) != (development, held)
