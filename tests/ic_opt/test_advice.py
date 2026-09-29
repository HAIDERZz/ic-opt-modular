"""Advice (T17.1.5 specification, section 6.3): the checks, the rule of section 2, start rows, the narrowed batch, an
advice that holds nothing, wrong advice, replay and continuation, the lines of opt.optimize, and a run without advice
being exactly the run it was before advice existed."""

from __future__ import annotations

import hashlib
import json
import math
import re

import numpy as np
import pytest
import yaml
from icopt_bench.loop import run_one
from icopt_bench.synthetic import PROBLEMS
from typer.testing import CliRunner

from ic_opt import advice as advice_rules
from ic_opt import space
from ic_opt._lock import LockHeld, exclusive_lock
from ic_opt.blocks.optimize import RUN_LOCK, advise, history_size, optimize, revoke_advice, suggest
from ic_opt.cli import app
from ic_opt.deck import Deck
from ic_opt.recipe import PLAN_MODE
from ic_opt.store import RunStore
from ic_opt.suggesters.metric_gp import MetricGpSuggester, region, select
from ic_opt.suggesters.metric_gp.compose import Composer, metric_scales, true_arrays
from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor, make_spec, minimal_spec, needs_turbo
from tests.ic_opt.test_metric_gp import (
    START,
    bowl,
    bowl_spec,
    integer,
    region_metrics,
    region_spec,
    run,
    spec_of,
    staged,
    stepped,
)
from tests.ic_opt.test_optimize import observed, run_suggest, wide_bowl, wide_spec

AUTHOR = {"author": "a person", "reason": "a test"}


def adopted(spec, since, rows=(), **fields) -> dict:
    """The adopt row ``ic-opt advise`` would append for ``fields`` at history size ``since``."""
    return advice_rules.adoption(spec, {**AUTHOR, **fields}, list(rows), since)[0]


def value(point, name) -> float:
    return float(space.parse_scalar(point.params[name])[0])


# -- 1. the checks of 6.1 ------------------------------------------------------------------------------------------------


def check_spec():
    """W spans a decade and more (0.5u to 10u): metric_gp searches it logarithmically."""
    return spec_of([integer("N", 1, 9), stepped("W", "0.5u", "10u", "0.5u"), stepped("R", "0", "1", "0.1")], ["m"],
                   objective={"direction": "minimize", "expression": "m"})


def refused(raw, pattern):
    with pytest.raises(ValueError, match=pattern):
        advice_rules.check(check_spec(), raw)


def test_every_check_of_an_advice_says_what_to_change():
    fields, notes = advice_rules.check(check_spec(), {**AUTHOR, "ranges": {"W": ["1u", "3u"], "R": [0.15, 0.62]},
                                                      "fixed": {"N": 4}, "vary": ["W", "R", "W"]})
    assert fields == {**AUTHOR, "start": [], "ranges": {"W": ["1u", "3u"], "R": ["0.2", "0.6"]}, "fixed": {"N": "4"},
                      "vary": ["W", "R"]}
    assert notes == ["ranges: R [0.15, 0.62]: bounds between levels moved inward to [0.2, 0.6]"]
    # between levels: the nearest level in the strategy's own coordinates -- logarithmic for W: 0.74u is nearer 1u
    # there (ln 1/0.74 = 0.30 < ln 0.74/0.5 = 0.39), nearer 0.5u in raw values
    fields, notes = advice_rules.check(check_spec(), {**AUTHOR, "fixed": {"W": "0.74u"},
                                                      "start": [{"N": 3, "W": "2u", "R": 0.33}]})
    assert fields["fixed"] == {"W": "1u"} and fields["start"] == [{"N": "3", "W": "2u", "R": "0.3"}]
    assert notes == ["fixed: W=0.74u lies between levels: moved to 1u", "start row 1: R=0.33 lies between levels: moved to 0.3"]
    refused({**AUTHOR, "ranges": {"L": ["1", "2"]}}, r"unknown variable\(s\) L; the spec's variables are N, W, R")
    refused({**AUTHOR, "vary": ["N", "Q"]}, r"unknown variable\(s\) Q")
    refused({**AUTHOR, "start": [{"N": 1, "W": "1u", "R": 0, "Q": 1}]}, r"unknown variable\(s\) Q")
    refused({**AUTHOR, "fixed": {"W": "1u"}, "ranges": {"W": ["1u", "2u"]}}, "W in both fixed and ranges")
    refused({**AUTHOR, "fixed": {"W": "1u"}, "vary": ["W"]}, "W in both fixed and vary")
    refused({**AUTHOR, "ranges": {"W": ["0.4u", "3u"]}},
            r"reaches outside the spec's range \[0.5u, 10u\]: an advice cannot widen what the spec allows; the spec's "
            r"range is the user's to change")
    refused({**AUTHOR, "ranges": {"R": ["0.21", "0.29"]}}, r"holds no level of the grid \(step 0.1\)")
    refused({**AUTHOR, "ranges": {"R": ["0.6", "0.2"]}}, "the lower bound is above the upper one")
    refused({**AUTHOR, "ranges": {"R": ["0.2"]}}, r"R takes \[lower, upper\]")
    refused({**AUTHOR, "ranges": {"W": ["1e-6", "3u"]}}, r"must be a number in the unit of the spec's range \(u\)")
    refused({**AUTHOR, "fixed": {"W": "11u"}}, r"fixed: W=11u is outside the spec's range \[0.5u, 10u\]")
    refused({**AUTHOR, "start": [{"N": 1, "W": "11u", "R": 0}]}, r"start row 1: W=11u is outside the spec's range")
    refused({**AUTHOR, "start": [{"N": 1, "W": "1u", "R": 0}, {"N": 1}]},
            "start row 2 does not name every variable: missing W, R")
    refused({**AUTHOR}, "the advice names nothing")
    refused({**AUTHOR, "ranges": {}, "fixed": {}, "vary": [], "start": []}, "the advice names nothing")
    refused({"reason": "r", "fixed": {"N": 1}}, "author is missing")
    refused({"author": "a", "reason": " ", "fixed": {"N": 1}}, "reason is missing")
    refused({**AUTHOR, "range": {"N": [1, 2]}}, r"unknown field\(s\) range")
    refused(["N"], "an advice is a mapping")


# -- 2. the rule of section 2 ------------------------------------------------------------------------------------------


def test_the_advice_in_effect_follows_from_the_rows_and_the_history_size_alone():
    spec = check_spec()
    rows = []
    rows.append(adopted(spec, 10, rows, fixed={"N": 2}))
    rows.append(adopted(spec, 30, rows, fixed={"N": 3}))
    rows.append(advice_rules.revocation(rows, "a2", "it did not help", 50))
    rows.append(adopted(spec, 70, rows, fixed={"N": 4}))
    assert [r["id"] for r in rows] == ["a1", "a2", "a2", "a3"]
    effect = {k: (advice_rules.in_effect(rows, k) or {}).get("id") for k in (0, 9, 10, 29, 30, 49, 50, 69, 70, 99)}
    assert effect == {0: None, 9: None, 10: "a1", 29: "a1", 30: "a2", 49: "a2", 50: None, 69: None, 70: "a3", 99: "a3"}
    # a row whose since lies after the batch did not exist for it, whatever its place in the file
    assert advice_rules.in_effect(rows[:3], 40)["id"] == "a2" and advice_rules.in_effect(rows[:1], 5) is None
    assert advice_rules.status(rows) == {"a1": "superseded by a2", "a2": "revoked", "a3": "in effect"}
    with pytest.raises(ValueError, match="a1 is no longer in effect: a2 superseded it"):
        advice_rules.revocation(rows, "a1", "x", 80)
    with pytest.raises(ValueError, match="no advice a9 was adopted; adopted: a1, a2, a3"):
        advice_rules.revocation(rows, "a9", "x", 80)
    with pytest.raises(ValueError, match="a revoke needs a reason"):
        advice_rules.revocation(rows, "a3", " ", 80)
    with pytest.raises(ValueError, match="a2 is already revoked"):
        advice_rules.revocation(rows[:3], "a2", "again", 80)
    other = [adopted(check_spec(), 0, fixed={"N": 2}) | {"id": "a0", "spec_fingerprint": "another spec"},
             {"id": "a0", "event": "revoke", "since": 1, "reason": "x"}]
    assert advice_rules.of_problem(other + rows, {spec.fingerprint()}) == rows


def test_a_refused_advice_is_recorded_and_never_taken_for_an_adopted_one():
    """T17.10 specification, 1.5: the row of an advice the check refused -- the refusal's message, the content as given
    -- takes no adopted advice's number, ends nothing, is never in effect, and stays with its own problem's rows."""
    spec = check_spec()
    rows = [adopted(spec, 10, fixed={"N": 2})]
    raw = yaml.safe_load("author: a person\nreason: wider\nranges: {W: [0.4u, 3u]}\nwhen: 2026-09-29\n")
    with pytest.raises(ValueError, match="unknown field") as refused_by:
        advice_rules.check(spec, raw)
    row = advice_rules.refusal(spec, raw, str(refused_by.value), rows, 20)
    assert row == {"id": "r1", "event": "refuse", "status": "refused", "at": row["at"], "since": 20,
                   "reason": "unknown field(s) when: an advice has author, reason, start, ranges, fixed, vary",
                   "raw": {"author": "a person", "reason": "wider", "ranges": {"W": ["0.4u", "3u"]}, "when": "2026-09-29"},
                   "spec_fingerprint": spec.fingerprint()}
    assert json.loads(json.dumps(row, allow_nan=False)) == row             # a JSON line, whatever YAML read (a date)
    rows.append(row)
    rows.append(adopted(spec, 30, rows, fixed={"N": 3}))
    assert [r["id"] for r in rows] == ["a1", "r1", "a2"]
    assert advice_rules.refusal(spec, ["N"], "an advice is a mapping", rows, 40)["id"] == "r2"
    assert {k: advice_rules.in_effect(rows, k)["id"] for k in (10, 20, 29, 30)} == {10: "a1", 20: "a1", 29: "a1", 30: "a2"}
    assert advice_rules.status(rows) == {"a1": "superseded by a2", "a2": "in effect"}
    with pytest.raises(ValueError, match="no advice r1 was adopted; adopted: a1, a2"):
        advice_rules.revocation(rows, "r1", "x", 40)
    other = row | {"id": "r0", "spec_fingerprint": "another spec"}
    assert advice_rules.of_problem([other, *rows], {spec.fingerprint()}) == rows


def test_split_origin_reads_the_advice_suffix_and_nothing_else():
    assert space.split_origin("suggest:metric_gp:tr:0:40@a2") == ("suggest:metric_gp:tr:0:40", "a2")
    assert space.split_origin("suggest:metric_gp:tr:0:40") == ("suggest:metric_gp:tr:0:40", None)
    assert space.split_origin("advice:a2") == ("advice:a2", None)
    assert space.split_origin("signoff:lab@x:obs_0001") == ("signoff:lab@x:obs_0001", None)
    assert space.split_origin("initial:suggest:metric_gp:grid:8@a13") == ("initial:suggest:metric_gp:grid:8", "a13")
    assert region.batch_key("suggest:metric_gp:tr:0:40@a2") == region.batch_key("suggest:metric_gp:tr:0:40") == ("tr", 40)


# -- 3. start rows ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", ["metric_gp", "openbox_gp_eic", pytest.param("turbo", marks=needs_turbo)])
def test_start_rows_come_first_once_with_their_origin(strategy):
    spec = wide_spec()
    history = run_suggest(spec, strategy, 8, 4, wide_bowl, seed=1, initial_trials=4) if strategy != "turbo" else \
        run_suggest(spec, strategy, 8, 4, wide_bowl, seed=1, n_init=4)
    new = [{"F": "3", "W": "0.2u"}, {"F": "97", "W": "9.9u"}]
    rows = [adopted(spec, 8, start=[new[0], history[0].params, new[0], new[1]])]      # one evaluated, one given twice
    extra = {"n_init": 4} if strategy == "turbo" else {"initial_trials": 4}
    points = suggest(spec, history, 4, strategy=strategy, seed=1, advice=rows, start=[{"F": "50", "W": "5u"}], **extra)
    assert [(p.params, p.origin) for p in points[:3]] == [({"F": "50", "W": "5u"}, "start"), (new[0], "advice:a1"),
                                                          (new[1], "advice:a1")]
    assert points[3].origin.startswith(f"suggest:{strategy}:")
    history.extend(observed(spec, points, wide_bowl, len(history)))
    again = suggest(spec, history, 4, strategy=strategy, seed=1, advice=rows, **extra)
    assert not any(p.origin.startswith("advice:") for p in again)
    assert not {p.key for p in again} & history.keys()
    # not yet in effect for a batch proposed before its since
    assert suggest(spec, history[:4], 2, strategy=strategy, seed=1, advice=rows, **extra)[0].origin != "advice:a1"


def test_an_advice_s_start_rows_count_as_start_points_do():
    """A design of 6 with batches of 4: the advice's two start rows and two design points, then the design's last two
    and two of the models'."""
    spec = wide_spec()
    rows = [adopted(spec, 0, start=[{"F": "3", "W": "0.2u"}, {"F": "97", "W": "9.9u"}])]
    history = run_suggest(spec, "metric_gp", 8, 4, wide_bowl, seed=1, initial_trials=6, advice=rows)
    assert [o.origin.split(":")[-1] if o.origin.startswith("advice") else o.origin.split(":")[2] for o in history] == \
        ["a1", "a1", "init", "init", "init", "init", "tr", "tr"]


# -- 4 / 5. the narrowed batch --------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def region_history():
    """30 points of the region problem (6.8e6 grid points: a search region), run without advice."""
    return run(region_spec(), 30, 10, region_metrics, seed=5)


def split(points, advice_id="a1"):
    suffixed = [p for p in points if space.split_origin(p.origin)[1] == advice_id]
    return suffixed, [p for p in points if p not in suffixed]


def test_ranges_narrow_eight_of_ten_slots_and_two_choose_as_without_advice(region_history, monkeypatch):
    """The two free slots choose among the candidates the batch has without advice -- the region's, not moved, and the
    ones spread over the whole space -- so the search the run was making goes on; the eight others among the region's
    candidates brought inside the ranges."""
    spec = region_spec()
    offered = []
    choose = select.select_batch
    monkeypatch.setattr(select, "select_batch", lambda models, value_model, composer, scales, x, preferred, rng, **kw:
                        offered.append((x, preferred)) or choose(models, value_model, composer, scales, x, preferred, rng, **kw))
    suggest(spec, region_history, 10, strategy="metric_gp", seed=5)
    rows = [adopted(spec, 30, ranges={"A": ["0", "0.3"], "B": ["0", "0.3"]})]
    points = suggest(spec, region_history, 10, strategy="metric_gp", seed=5, advice=rows)
    (plain, _), (x, preferred) = offered
    assert {tuple(r) for r in x[preferred[0]]} == {tuple(r) for r in plain}                # A, B, C, D: unit coordinates
    assert all(preferred[b] is preferred[0] for b in (0, 1)) and all(preferred[b] is preferred[2] for b in range(2, 10))
    assert (x[preferred[2]][:, :2] <= 0.3 + 1e-12).all()
    inside, whole = split(points)
    assert len(inside) == 8 and all(p.origin == "suggest:metric_gp:tr:0:30@a1" for p in inside)
    assert all(value(p, "A") <= 0.3 and value(p, "B") <= 0.3 for p in inside)
    assert len(whole) == 2 and {p.origin for p in whole} <= {"suggest:metric_gp:tr:0:30", "suggest:metric_gp:wide:0:30"}
    assert len({p.key for p in points}) == 10 and not {p.key for p in points} & region_history.keys()


def test_fixed_and_vary_hold_variables_at_their_level_and_at_the_region_s_centre(region_history):
    spec = region_spec()
    points = suggest(spec, region_history, 10, strategy="metric_gp", seed=5, advice=[adopted(spec, 30, fixed={"A": "0.1"})])
    inside, _ = split(points)
    assert len(inside) == 8 and {p.params["A"] for p in inside} == {"0.1"}
    points = suggest(spec, region_history, 10, strategy="metric_gp", seed=5, advice=[adopted(spec, 30, vary=["A", "B"])])
    inside, _ = split(points)
    rows = list(region_history)
    state = MetricGpSuggester().region_state(spec, region_history)
    centre = rows[region.centre(Composer(spec), rows, state, metric_scales(spec, rows), true_arrays(spec, rows))].params
    assert len(inside) == 8 and all((p.params["C"], p.params["D"]) == (centre["C"], centre["D"]) for p in inside)
    assert len({(p.params["A"], p.params["B"]) for p in inside}) == 8


def test_on_a_small_grid_a_fifth_chooses_among_all_points_and_the_rest_inside_the_advice():
    spec = bowl_spec()                               # 41 x 41 grid points: no region, the whole grid is the candidate set
    history = run(spec, 20, 5, bowl, seed=1)
    points = suggest(spec, history, 10, strategy="metric_gp", seed=1, advice=[adopted(spec, 20, ranges={"X": ["0.6", "1"]})])
    inside, _ = split(points)
    assert [p.origin for p in points] == ["suggest:metric_gp:grid:20"] * 2 + ["suggest:metric_gp:grid:20@a1"] * 8
    assert all(value(p, "X") >= 0.6 for p in inside)
    points = suggest(spec, history, 10, strategy="metric_gp", seed=1, advice=[adopted(spec, 20, vary=["X"])])
    inside, _ = split(points)
    best = min((o for o in history if o.feasible), key=lambda o: o.objective)
    assert len(inside) == 8 and {p.params["Y"] for p in inside} == {best.params["Y"]}      # held at the best point's level


@pytest.mark.parametrize("batch", [1, 2])
def test_in_batches_of_one_or_two_slots_a_fifth_of_the_points_is_free(region_history, batch):
    """``round(0.2 * slots)`` is none for one or two slots: slot ``b`` of the batch proposed at history size ``k`` is then
    free when ``k + b + 1`` is a multiple of 5 -- the 35th and the 40th point here, the 25th and the 30th on the small
    grid."""
    spec = region_spec()
    rows = [adopted(spec, 30, ranges={"A": ["0", "0.3"], "B": ["0", "0.3"]})]
    history = run(spec, 40, batch, region_metrics, seed=5, history=list(region_history), advice=rows)
    inside, free = split(history[30:])
    assert [history.index(o) for o in free] == [34, 39] and len(inside) == 8
    assert all(value(o, "A") <= 0.3 and value(o, "B") <= 0.3 for o in inside)
    assert all(re.fullmatch(r"suggest:metric_gp:tr:0:3\d@a1", o.origin) for o in inside)
    spec = bowl_spec()
    rows = [adopted(spec, 20, ranges={"X": ["0.6", "1"]})]
    history = run(spec, 30, batch, bowl, seed=1, history=list(run(spec, 20, 5, bowl, seed=1)), advice=rows)
    inside, free = split(history[20:])
    assert [history.index(o) for o in free] == [24, 29] and all(value(o, "X") >= 0.6 for o in inside)


def test_an_advice_that_holds_no_unevaluated_point_leaves_the_batch_to_the_candidates_without_advice(region_history):
    spec = bowl_spec()
    history = run(spec, 20, 5, bowl, seed=1)
    only = {name: [v, v] for name, v in history[0].params.items()}           # one grid point, already evaluated
    points = suggest(spec, history, 10, strategy="metric_gp", seed=1, advice=[adopted(spec, 20, ranges=only)])
    assert [p.origin for p in points] == ["suggest:metric_gp:grid:20"] * 10
    assert len({p.key for p in points}) == 10 and not {p.key for p in points} & history.keys()
    spec = region_spec()
    only = {name: [v, v] for name, v in region_history[0].params.items()}
    points = suggest(spec, region_history, 10, strategy="metric_gp", seed=5, advice=[adopted(spec, 30, ranges=only)])
    assert {p.origin for p in points} <= {"suggest:metric_gp:tr:0:30", "suggest:metric_gp:wide:0:30"}
    assert len({p.key for p in points}) == 10 and not {p.key for p in points} & region_history.keys()


def test_a_new_region_s_anchor_is_brought_inside_the_advice(monkeypatch):
    """As in test_metric_gp's tag test (the region ends after k = 40 whatever the points are): the anchor at k = 50 is the
    first of the new region's slots, which the advice narrows, so it lies inside and carries the suffix. The region's
    course is the one without advice."""
    monkeypatch.setattr(region, "LENGTH_MIN", 0.5)
    spec = region_spec()
    rows = [adopted(spec, 10, ranges={"A": ["0", "0.3"]})]
    advised = run(spec, 60, 10, staged(30), seed=1, start=START, advice=rows)
    plain = run(spec, 60, 10, staged(30), seed=1, start=START)
    anchor = advised[50]
    assert anchor.origin == "suggest:metric_gp:anchor:1:50@a1" and value(anchor.point, "A") <= 0.3
    assert MetricGpSuggester().region_state(spec, advised).trace == MetricGpSuggester().region_state(spec, plain).trace


def test_in_batches_of_one_an_anchor_at_a_free_place_is_chosen_as_without_advice(monkeypatch):
    """In batches of one the anchor is the batch: at a place of the run that is free (the 35th point) it is chosen as
    without advice and carries no suffix; at another (the 39th) it is brought inside the advice. The plateau decides
    when the region ends: after it, four batches without success halve the side below 0.5."""
    monkeypatch.setattr(region, "LENGTH_MIN", 0.5)
    spec = region_spec()
    rows = [adopted(spec, 10, ranges={"A": ["0", "0.3"]})]
    for plateau, place, suffix in ((26, 34, ""), (30, 38, "@a1")):
        history = run(spec, place + 1, 1, staged(plateau), seed=1, start=START, advice=rows)
        assert [i for i, o in enumerate(history) if ":anchor:" in o.origin] == [place]
        assert history[place].origin == f"suggest:metric_gp:anchor:1:{place}{suffix}"
    assert value(history[place], "A") <= 0.3


# -- 6. wrong advice ------------------------------------------------------------------------------------------------------

# Hartmann-6 with its sum constraint (the benchmark's syn_hartmann6_c1; x* ~ (0.20, 0.15, 0.48, 0.28, 0.31, 0.66)), a
# metric_gp run of 100 points in batches of 10, and an advice adopted at 20 that keeps x0 and x1 in [0.6, 1]: plainly
# wrong, the optimum lies outside it in two of six variables. The run keeps its share when its best at 100 points is no
# worse than the best of the run without advice at 36 points: the 20 before the advice and a fifth of the 80 after it
# (T17.1.5 specification, section 6.3 item 6). The run without advice is one of 40 points, whose first 36 are those of
# a run of 100 (the same design, the same batches). Measured, best feasible objective (the problem's reference -3.3008):
#   seed                        0        1        2        3        4
#   wrong advice, at 100    -3.0722  -3.0378  -2.9806  -3.0479  -3.2806
#   no advice, at 36        -1.9248  -2.3923  -1.8347  -2.4404  -2.8659
#   no advice, at 100       -3.0652  -3.0392  -3.0790  -3.0848  -3.2850
WRONG = {"ranges": {"x0": ["0.6", "1"], "x1": ["0.6", "1"]}}
WRONG_SEEDS = range(5)


@pytest.fixture(scope="module")
def wrong_advice_runs():
    problem = PROBLEMS["syn_hartmann6_c1"]()
    row = adopted(problem.spec, 20, **WRONG)
    return {seed: (run_one(problem, "metric_gp", seed, budget=40, batch=10),
                   run_one(problem, "metric_gp", seed, budget=100, batch=10, advice=lambda _history: [row]))
            for seed in WRONG_SEEDS}


def best(points) -> float:
    return min((p["objective"] for p in points if p["feasible"]), default=math.inf)


def test_wrong_advice_does_not_end_the_search(wrong_advice_runs):
    plain, advised = wrong_advice_runs[0]
    assert plain["error"] is None and advised["error"] is None and len(advised["points"]) == 100
    assert [p["params"] for p in advised["points"][:20]] == [p["params"] for p in plain["points"][:20]]
    assert any(p["feasible"] for p in advised["points"][20:])
    suffixed = [p for p in advised["points"] if "@a1" in p["origin"]]
    assert len(suffixed) == 64 and all(float(p["params"]["x0"]) >= 0.6 and float(p["params"]["x1"]) >= 0.6 for p in suffixed)
    assert not any("@" in p["origin"] for p in advised["points"][:20])
    free = [p for p in advised["points"][20:] if "@" not in p["origin"]]
    assert len(free) == 16 and any(float(p["params"]["x0"]) < 0.6 or float(p["params"]["x1"]) < 0.6 for p in free)


@pytest.mark.parametrize("seed", WRONG_SEEDS)
def test_wrong_advice_keeps_its_share(wrong_advice_runs, seed):
    plain, advised = wrong_advice_runs[seed]
    assert advised["error"] is None and len(advised["points"]) == 100 and best(advised["points"]) < math.inf
    assert best(advised["points"]) <= best(plain["points"][:36]), (best(advised["points"]), best(plain["points"][:36]))


# -- 7. replay and continuation ---------------------------------------------------------------------------------------------


def test_replay_reads_suffixed_origins_and_a_continued_run_is_an_uninterrupted_one():
    spec = region_spec()
    rows = [adopted(spec, 20, ranges={"A": ["0", "0.4"]}, vary=["A", "B", "C"])]
    history = run(spec, 60, 10, region_metrics, seed=2, advice=rows)
    assert all(space.split_origin(o.origin)[1] is None for o in history[:20])
    assert sum(space.split_origin(o.origin)[1] == "a1" for o in history[20:]) == 32
    stripped = type(history)(o.model_copy(update={"origin": space.split_origin(o.origin)[0]}) for o in history)
    assert MetricGpSuggester().region_state(spec, history).trace == MetricGpSuggester().region_state(spec, stripped).trace
    for cut in (20, 40, 50):
        again = suggest(spec, history[:cut], 10, strategy="metric_gp", seed=2, advice=rows)
        assert [(p.params, p.origin) for p in again] == [(o.params, o.origin) for o in history[cut:cut + 10]]


def small_project(tmp_path, spec):
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims", lambda params, tb, corner: {"NF": 1.0 + ((int(params["F"]) - 32) / 10) ** 2
                                                                             + (float(params["W"].rstrip("u")) - 1.4) ** 2})
    return store, ex, Deck(templates={("tb", corner): "parameters F={{F}} W={{W}}\n" for corner in (None, "tt", "ss")})


def small_spec(**overrides):
    return make_spec(variables=[integer("F", 20, 60, 2), stepped("W", "0.6u", "3u", "0.2u")], budget={"max_simulations": 100},
                     **overrides)


def test_optimize_reads_the_advice_file_before_every_batch_and_a_continued_run_is_an_uninterrupted_one(tmp_path, capsys):
    spec = small_spec()
    kwargs = {"strategy": "metric_gp", "batch": 4, "seed": 2, "initial_trials": 8, "current": False, "limits": FAKE_HOST}
    store, ex, deck = small_project(tmp_path / "parts", spec)
    optimize(spec, ex, store, deck=deck, budget=8, **kwargs)
    row = advise(spec, store, {**AUTHOR, "ranges": {"F": ["40", "60"]}, "start": [{"F": "58", "W": "3u"}]})
    assert row["id"] == "a1" and row["since"] == 8
    parts = optimize(spec, ex, store, deck=deck, budget=16, **kwargs)
    out = capsys.readouterr().out
    assert "[advise] adopted a1 (since 8; in effect from the next batch): 1 start row; ranges F [40, 60]" in out
    assert "[optimize] advice a1 in effect (since 8, by a person): 1 start row; ranges F [40, 60]" in out
    whole_store, ex, deck = small_project(tmp_path / "whole", spec)
    advice_rules.path(whole_store.root).write_text(advice_rules.path(store.root).read_text(encoding="utf-8"), encoding="utf-8")
    whole = optimize(spec, ex, whole_store, deck=deck, budget=16, **kwargs)
    assert [(o.params, o.origin) for o in parts] == [(o.params, o.origin) for o in whole]
    assert [o.origin for o in whole[8:]] == (["advice:a1", "suggest:metric_gp:grid:8", "suggest:metric_gp:grid:8@a1",
                                              "suggest:metric_gp:grid:8@a1"] + ["suggest:metric_gp:grid:12"]
                                             + ["suggest:metric_gp:grid:12@a1"] * 3)
    assert all(int(o.params["F"]) >= 40 for o in whole if o.origin.endswith("@a1"))
    # adopted between two steps of a recipe by the recipe's own code: the second step reads it before its first batch
    store, ex, deck = small_project(tmp_path / "steps", spec)
    optimize(spec, ex, store, deck=deck, budget=8, step="one", **kwargs)
    advise(spec, store, {**AUTHOR, "fixed": {"W": "1.4u"}})
    two = optimize(spec, ex, store, deck=deck, budget=4, step="two", **kwargs)
    assert all(o.params["W"] == "1.4u" for o in two if o.origin.endswith("@a1")) and any("@a1" in o.origin for o in two)


def test_since_counts_what_the_strategy_is_handed_on_a_store_that_also_holds_other_corners(tmp_path, monkeypatch):
    """The signoff recipe searches at one corner and re-checks its best points at all: advice for the search counts the
    rows at its corner, which is the history size metric_gp sees, and is refused without --corners."""
    spec = small_spec(corners=[{"id": "tt"}, {"id": "ss"}])
    store, ex, deck = small_project(tmp_path, spec)
    kwargs = {"batch": 4, "seed": 0, "initial_trials": 8, "current": False, "limits": FAKE_HOST}
    optimize(spec, ex, store, deck=deck, strategy="metric_gp", budget=8, corners=["tt"], step="search", **kwargs)
    optimize(spec, ex, store, deck=deck, strategy="random", budget=3, batch=3, corners="all", step="signoff",
             current=False, limits=FAKE_HOST)
    with pytest.raises(ValueError, match=r"several sets of corners \(tt: 8; ss, tt: 3\); say which run the advice is for"):
        advise(spec, store, {**AUTHOR, "ranges": {"F": ["40", "60"]}})
    assert history_size(spec, store.observations(), ["tt"]) == 8 and history_size(spec, store.observations(), "all") == 3
    row = advise(spec, store, {**AUTHOR, "ranges": {"F": ["40", "60"]}}, corners=["tt"])
    seen = []
    propose = MetricGpSuggester.propose
    monkeypatch.setattr(MetricGpSuggester, "propose", lambda self, spec, history, n, **kw: seen.append(len(history))
                        or propose(self, spec, history, n, **kw))
    more = optimize(spec, ex, store, deck=deck, strategy="metric_gp", budget=12, corners=["tt"], step="search", **kwargs)
    assert row["since"] == 8 and seen == [8]
    assert [o.origin for o in more[8:]] == ["suggest:metric_gp:grid:8"] + ["suggest:metric_gp:grid:8@a1"] * 3


def test_no_advice_is_adopted_while_a_run_goes(tmp_path):
    spec = small_spec()
    store, _ex, _deck = small_project(tmp_path, spec)
    with exclusive_lock(store.root / RUN_LOCK, what="project"), pytest.raises(LockHeld, match="project is locked"):
        advise(spec, store, {**AUTHOR, "fixed": {"F": "40"}})
    with store.lock(), pytest.raises(LockHeld):
        revoke_advice(spec, store, "a1", "x")
    assert not advice_rules.path(store.root).exists()


# -- 8. the lines of opt.optimize --------------------------------------------------------------------------------------------


def lines(out: str, *needles: str) -> list[str]:
    return [line for line in out.splitlines() if any(n in line for n in needles)]


def test_the_design_and_auto_lines_are_unchanged_by_an_advice_and_unused_parts_are_named(tmp_path, capsys):
    spec = small_spec()
    kwargs = {"batch": 4, "seed": 2, "initial_trials": 8, "current": False, "limits": FAKE_HOST}
    printed = {}
    for label in ("plain", "advised"):
        store, ex, deck = small_project(tmp_path / label, spec)
        if label == "advised":
            advise(spec, store, {**AUTHOR, "ranges": {"F": ["40", "60"]}})
        capsys.readouterr()
        for plan in (True, False):
            token = PLAN_MODE.set(plan)
            try:
                optimize(spec, ex, store, deck=deck, budget=12, **kwargs)
            finally:
                PLAN_MODE.reset(token)
        printed[label] = capsys.readouterr().out
    needles = ("strategy auto", "initial design")
    assert lines(printed["plain"], *needles) == lines(printed["advised"], *needles) and len(lines(printed["plain"], *needles)) == 4
    assert "advice" not in printed["plain"]
    assert lines(printed["advised"], "advice a1") == [
        "[plan] opt.optimize advice a1 in effect (since 0, by a person): ranges F [40, 60]",
        "[optimize] advice a1 in effect (since 0, by a person): ranges F [40, 60]"]
    store, ex, deck = small_project(tmp_path / "openbox", spec)
    advise(spec, store, {**AUTHOR, "ranges": {"F": ["40", "60"]}, "vary": ["F"], "start": [{"F": "44", "W": "1u"}]})
    capsys.readouterr()
    obs = optimize(spec, ex, store, deck=deck, strategy="openbox_gp_eic", budget=4, **kwargs)
    assert lines(capsys.readouterr().out, "advice a1") == [
        ("[optimize] advice a1 in effect (since 0, by a person): 1 start row; ranges F [40, 60]; vary F (every other "
         "variable held at the search region's centre)"),
        ("[optimize] advice a1: strategy openbox_gp_eic takes its start rows only; its ranges, vary are not used "
         "(narrowing the search is metric_gp's)")]
    assert obs[0].origin == "advice:a1" and all("@" not in o.origin for o in obs)


# -- the command ------------------------------------------------------------------------------------------------------------


def test_the_advise_command_adopts_lists_revokes_and_refuses(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "spec.yaml").write_text(yaml.safe_dump(minimal_spec(budget={"max_simulations": 100})), encoding="utf-8")
    good = tmp_path / "advice.yaml"
    good.write_text("author: a person\nreason: the digest shows the best points at F 26 to 30\n"
                    "ranges:\n  F: [26, 30]\nfixed:\n  W: 0.9u\nstart:\n  - {F: 28, W: 1u}\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(app, ["advise", str(project), str(good)])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "[advise] fixed: W=0.9u lies between levels: moved to 0.8u",
        "[advise] adopted a1 (since 0; in effect from the next batch): 1 start row; ranges F [26, 30]; fixed W=0.8u"]
    row = advice_rules.read(project / ".icopt")[0]
    assert row["since"] == 0 and row["author"] == "a person" and row["start"] == [{"F": "28", "W": "1u"}]
    bad = tmp_path / "bad.yaml"
    bad.write_text("author: a person\nreason: wider\nranges:\n  F: [10, 30]\n", encoding="utf-8")
    result = runner.invoke(app, ["advise", str(project), str(bad)])
    assert result.exit_code == 2 and "an advice cannot widen what the spec allows" in result.output
    # refused, and recorded as such (T17.10 specification, 1.5): the next digest lists it
    assert "[advise] refused; recorded as r1 (since 0): the next digest lists it" in result.output
    widen = ("ranges: F [10, 30] reaches outside the spec's range [20, 30]: an advice cannot widen what the spec allows; "
             "the spec's range is the user's to change (spec.yaml)")
    refused = advice_rules.read(project / ".icopt")[1]
    assert refused == {"id": "r1", "event": "refuse", "status": "refused", "at": refused["at"], "since": 0,
                       "reason": widen, "raw": {"author": "a person", "reason": "wider", "ranges": {"F": [10, 30]}},
                       "spec_fingerprint": row["spec_fingerprint"]}
    result = runner.invoke(app, ["advise", str(project), "--revoke", "a1", "--reason", "the range was too narrow"])
    assert result.exit_code == 0 and "[advise] revoked a1 (since 0): the range was too narrow" in result.output
    result = runner.invoke(app, ["advise", str(project), "--list"])
    assert result.exit_code == 0 and result.output.splitlines() == [
        ("a1  since 0  revoked  by a person: 1 start row; ranges F [26, 30]; fixed W=0.8u -- the digest shows the best "
         "points at F 26 to 30"),
        f"r1  since 0  refused  by a person: {widen}",
        "a1  revoked at since 0: the range was too narrow"]
    assert runner.invoke(app, ["advise", str(project)]).exit_code == 2
    with exclusive_lock(project / ".icopt" / RUN_LOCK, what="project"):
        result = runner.invoke(app, ["advise", str(project), str(good)])
        assert result.exit_code == 2 and "advice is given between runs" in result.output
        result = runner.invoke(app, ["advise", str(project), str(bad)])   # refused; while a run goes, not recorded
    assert result.exit_code == 2 and "an advice cannot widen what the spec allows" in result.output
    assert "[advise] the refusal is not recorded: project is locked by another run" in result.output
    assert len(advice_rules.read(project / ".icopt")) == 3


# -- 9. without advice, everything is as it was -------------------------------------------------------------------------------

# Proposals of three calls, pinned on the commit this task started from (b30c2c7): metric_gp with a search region,
# metric_gp on a small grid, openbox_gp_eic. The random streams must not shift.
PINNED_REGION = [("0.9", "0.62", "0.72", "0.72", "wide"), ("0.86", "0.7", "0.78", "0.8", "wide"),
                 ("0.92", "0.74", "0.7", "0.72", "tr"), ("0.94", "0.72", "0.66", "0.78", "tr"),
                 ("0.94", "0.72", "0.7", "0.68", "tr"), ("0.88", "0.74", "0.7", "0.64", "tr"),
                 ("0.9", "0.64", "0.7", "0.76", "tr"), ("0.86", "0.74", "0.66", "0.64", "tr"),
                 ("0.94", "0.66", "0.7", "0.78", "tr"), ("0.94", "0.7", "0.68", "0.64", "tr")]
PINNED_GRID = [("0.425", "1"), ("0.3", "0.6"), ("0.55", "0.725"), ("1", "0.95"), ("0.15", "0")]
# PINNED_OPENBOX re-pinned in T17.7: wide_spec's W (0.1u to 10u) spans two decades and openbox_* now searches it on a
# logarithmic scale; pinned before it: ("51", "5u"), ("52", "5u"), ("50", "5u"), ("50", "5.1u").
PINNED_OPENBOX = [("56", "10u"), ("57", "9.9u"), ("54", "9.9u"), ("57", "9.7u")]
# benchmarks/icopt_bench/loop.run_one(syn_hartmann6_c1, metric_gp, seed 0, budget 40): sha256 of its [params, origin] rows
PINNED_BENCHMARK = "21d3c5b1bcc9b4f1"


@pytest.mark.parametrize("advice", [(), []])
def test_without_advice_the_proposals_are_the_ones_pinned_before_advice_existed(advice, region_history):
    spec = region_spec()
    points = suggest(spec, region_history, 10, strategy="metric_gp", seed=5, advice=advice)
    assert [(*p.params.values(), p.origin.split(":")[2]) for p in points] == PINNED_REGION
    assert all(p.origin.endswith(":0:30") for p in points)
    spec = bowl_spec()
    points = suggest(spec, run(spec, 20, 5, bowl, seed=1), 5, strategy="metric_gp", seed=1, advice=advice)
    assert [tuple(p.params.values()) for p in points] == PINNED_GRID and {p.origin for p in points} == {"suggest:metric_gp:grid:20"}
    spec = wide_spec()
    points = suggest(spec, run_suggest(spec, "openbox_gp_eic", 12, 4, wide_bowl, seed=2), 4, strategy="openbox_gp_eic", seed=2,
                     advice=advice)
    assert [tuple(p.params.values()) for p in points] == PINNED_OPENBOX


def test_the_benchmark_without_advice_is_the_run_it_was_and_takes_advice_rows_per_batch():
    problem = PROBLEMS["syn_hartmann6_c1"]()
    result = run_one(problem, "metric_gp", 0, budget=40)
    rows = [[p["params"], p["origin"]] for p in result["points"]]
    assert hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16] == PINNED_BENCHMARK
    row = adopted(problem.spec, 20, fixed={"x5": "0.5"})
    sizes = []
    advised = run_one(problem, "metric_gp", 0, budget=40, advice=lambda history: sizes.append(len(history)) or [row])
    assert sizes == [0, 10, 20, 30] and advised["error"] is None
    assert [p["origin"] for p in advised["points"][:20]] == [p["origin"] for p in result["points"][:20]]
    assert all(p["params"]["x5"] == "0.5" for p in advised["points"][20:] if p["origin"].endswith("@a1"))
    assert sum(p["origin"].endswith("@a1") for p in advised["points"]) == 16


def test_a_run_without_advice_writes_no_advice_file_and_an_empty_file_changes_nothing(tmp_path):
    spec = small_spec()
    kwargs = {"strategy": "metric_gp", "batch": 4, "seed": 2, "initial_trials": 8, "current": False, "limits": FAKE_HOST}
    store, ex, deck = small_project(tmp_path / "none", spec)
    none = optimize(spec, ex, store, deck=deck, budget=12, **kwargs)
    assert not advice_rules.path(store.root).exists()
    store, ex, deck = small_project(tmp_path / "empty", spec)
    advice_rules.path(store.root).write_text("", encoding="utf-8")
    empty = optimize(spec, ex, store, deck=deck, budget=12, **kwargs)
    assert [(o.params, o.origin) for o in empty] == [(o.params, o.origin) for o in none]
    assert advice_rules.path(store.root).read_text(encoding="utf-8") == ""
    assert math.isfinite(none.best()[0].objective) and not re.search("@|advice:", " ".join(o.origin for o in none))
    assert np.all([o.status in ("ok", "constraint_failed") for o in none])
