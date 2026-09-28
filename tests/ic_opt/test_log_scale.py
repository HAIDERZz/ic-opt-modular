"""The existing strategies search on a logarithmic scale where a range spans a decade (T17.7 specification, section 5):
the one rule, a spec without a logarithmic variable proposing exactly what it proposed before, the grid points, the design
per decade, OpenBox's space, and a continued run being an uninterrupted one."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ic_opt import digest, space
from ic_opt.blocks.optimize import optimize, suggest
from ic_opt.suggesters.base import SearchScale
from ic_opt.suggesters.metric_gp.coords import Coords
from ic_opt.suggesters.openbox import OpenBoxSuggester, _config_space, _values
from ic_opt.suggesters.turbo import TurboSuggester
from tests.ic_opt.fakes import FAKE_HOST, make_spec, needs_turbo
from tests.ic_opt.test_metric_gp import integer, stepped
from tests.ic_opt.test_optimize import observed, project, run_suggest

STRATEGIES = [pytest.param("turbo", marks=needs_turbo), "openbox_gp_eic"]


def strategy_kwargs(strategy, **openbox):
    return openbox if strategy.startswith("openbox") else {}


# -- 1. the rule ---------------------------------------------------------------------------------------------------------


def test_a_positive_range_spanning_a_decade_is_logarithmic_and_every_other_is_linear():
    assert space.log_scale(1, 10) and space.log_scale(0.5, 10) and space.log_scale(5e-7, 1e-5) and space.log_scale(1, 50)
    assert not space.log_scale(1, 9.9) and not space.log_scale(0, 99) and not space.log_scale(-1, 1)
    assert not space.log_scale(-100, -1) and not space.log_scale(5, 5)


def test_metric_gp_s_coordinates_the_strategies_and_the_digest_read_the_one_rule(monkeypatch):
    spec = make_spec(variables=[integer("N", 1, 9), stepped("W", "0.5u", "10u", "0.5u"), stepped("R", "0", "1", "0.1"),
                                integer("K", 5, 5), stepped("V", "-1", "1", "0.1"), integer("M", 1, 50)])
    expected = [False, True, False, False, False, True]              # 9 / 1; 10 / 0.5; lower 0; one level; negative; 50 / 1

    def grids():
        return [digest._Grid(v).log for v in spec.variables]

    assert Coords(spec).log.tolist() == SearchScale(spec).log.tolist() == grids() == expected
    monkeypatch.setattr(space, "log_scale", lambda lower, upper: lower > 0)      # the rule changed in its one place
    changed = [True, True, False, True, False, True]
    assert Coords(spec).log.tolist() == SearchScale(spec).log.tolist() == grids() == changed


# -- 2. without a variable under the rule nothing changes ----------------------------------------------------------------


def linear_spec():
    """No variable under the rule: F starts at 0, W spans 9.9 / 1, R is negative."""
    return make_spec(variables=[{"name": "F", "kind": "integer", "lower": "0", "upper": "99", "step": "1"},
                                {"name": "W", "kind": "continuous_step", "lower": "1u", "upper": "9.9u", "step": "0.1u"},
                                {"name": "R", "kind": "continuous_step", "lower": "-1", "upper": "1", "step": "0.05"}])


def linear_bowl(params):
    f, w, r = int(params["F"]), float(params["W"].rstrip("u")), float(params["R"])
    return {"NF": 1.0 + ((f - 60) / 30) ** 2 + (w - 4.0) ** 2 / 10 + (r - 0.3) ** 2}


# Proposals of three calls per strategy, pinned on the commit this task started from (a330460): the design on an empty
# history, then the model on 12 Sobol points, then the model again with the second call's points added.
PINNED = {
    "openbox_gp_eic": [
        [("69", "5.8u", "-0.15", "init"), ("34", "1.6u", "0.45", "init"), ("2", "8.4u", "-0.9", "init"),
         ("92", "3.6u", "0.65", "init")],
        [("57", "4.1u", "0.2", "acq"), ("57", "4.1u", "0.15", "acq"), ("57", "4.1u", "0.1", "acq"), ("58", "4.1u", "0.1", "acq")],
        [("58", "4.9u", "0.3", "acq"), ("58", "4.8u", "0.35", "acq"), ("60", "4.9u", "0.3", "acq"), ("58", "5.1u", "0.35", "acq")]],
    "turbo": [
        [("57", "9.4u", "0.75", "init:0:0"), ("94", "7.8u", "-0.65", "init:0:0"), ("75", "2.9u", "0.15", "init:0:0"),
         ("21", "5u", "0.5", "init:0:0")],
        [("57", "5u", "0.05", "tr:0:12"), ("78", "6.7u", "0.85", "tr:0:12"), ("71", "3u", "0.4", "tr:0:12"),
         ("62", "4.5u", "0.2", "tr:0:12")],
        [("52", "4.4u", "0.3", "tr:0:16"), ("57", "3.1u", "0", "tr:0:16"), ("56", "4.6u", "0.15", "tr:0:16"),
         ("59", "4u", "0.5", "tr:0:16")]],
}


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_without_a_logarithmic_variable_the_proposals_are_the_ones_pinned_before_the_scale_existed(strategy):
    spec = linear_spec()
    kwargs = strategy_kwargs(strategy, initial_trials=6)
    calls = [suggest(spec, [], 4, strategy=strategy, seed=3, **kwargs)]
    history = run_suggest(spec, "sobol", 12, 12, linear_bowl, seed=4)
    calls.append(suggest(spec, history, 4, strategy=strategy, seed=3, **kwargs))
    history.extend(observed(spec, calls[-1], linear_bowl, len(history)))
    calls.append(suggest(spec, history, 4, strategy=strategy, seed=3, **kwargs))
    prefix = f"suggest:{strategy}:"
    assert all(p.origin.startswith(prefix) for points in calls for p in points)
    assert [[(*p.params.values(), p.origin.removeprefix(prefix)) for p in points] for points in calls] == PINNED[strategy]
    assert not SearchScale(spec).log.any()


# -- 3. with a variable under the rule ------------------------------------------------------------------------------------


def log_spec(**overrides):
    """W spans two decades, M (an integer) more than one; F is linear."""
    return make_spec(variables=[{"name": "F", "kind": "integer", "lower": "20", "upper": "30", "step": "2"},
                                {"name": "W", "kind": "continuous_step", "lower": "0.1u", "upper": "10u", "step": "0.01u"},
                                {"name": "M", "kind": "integer", "lower": "1", "upper": "50", "step": "1"}], **overrides)


def log_bowl(params):
    f, w, m = int(params["F"]), float(params["W"].rstrip("u")), int(params["M"])
    return {"NF": 1.0 + ((f - 26) / 10) ** 2 + math.log10(w / 0.3) ** 2 + math.log10(m / 4) ** 2}


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_every_proposal_is_a_grid_point_inside_the_range_and_the_search_reaches_the_low_decade(strategy):
    spec = log_spec()
    history = run_suggest(spec, strategy, 24, 6, log_bowl, seed=1, **strategy_kwargs(strategy, initial_trials=8))
    assert len(history) == 24 and len(history.keys()) == 24
    for obs in history:
        space.check(spec, obs.params)
    tags = [o.origin.split(":")[2] for o in history]
    design, model = (8, "acq") if strategy.startswith("openbox") else (6, "tr")      # initial_trials; TuRBO's 2 x variables
    assert tags == ["init"] * design + [model] * (24 - design)                        # no random fill
    assert history.best()[0].objective < 1.5
    assert sum(float(o.params["W"].rstrip("u")) < 1 for o in history) >= 4          # the lowest of the two decades
    # what the strategy hands back is in the numeric space of space.bounds, inside the range up to rounding
    suggester = OpenBoxSuggester(initial_trials=8) if strategy.startswith("openbox") else TurboSuggester()
    raw = np.array(suggester.propose(spec, history, 4, seed=1).raw)
    lower, upper = (np.array(b) for b in space.bounds(spec))
    assert raw.shape == (4, 3) and np.all(raw >= lower * (1 - 1e-9)) and np.all(raw <= upper * (1 + 1e-9))


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_the_initial_design_is_even_per_decade(strategy):
    """64 design points on a range 1 to 1000: about a third in each decade (on the linear scale nine in ten would be in
    the top one)."""
    spec = make_spec(variables=[{"name": "X", "kind": "continuous_step", "lower": "1", "upper": "1000", "step": "0.01"},
                                {"name": "Y", "kind": "continuous_step", "lower": "0", "upper": "1", "step": "0.001"}])
    kwargs = {"initial_trials": 64} if strategy.startswith("openbox") else {"n_init": 64}
    points = suggest(spec, [], 64, strategy=strategy, seed=0, **kwargs)
    assert len(points) == 64 and all(":init" in p.origin for p in points)
    x = np.array([float(p.params["X"]) for p in points])
    per_decade = [int(np.sum((x >= lo) & (x < 10 * lo))) for lo in (1, 10, 100)]
    assert sum(per_decade) == 64 - int(np.sum(x == 1000)) and all(16 <= k <= 27 for k in per_decade), per_decade
    y = np.array([float(p.params["Y"]) for p in points])
    assert all(12 <= int(np.sum((y >= q / 4) & (y < (q + 1) / 4))) <= 20 for q in range(4))     # Y stays linear


@pytest.mark.parametrize("lower, upper, step", [("0.5", "10", "0.5"), ("0.18", "18", "0.01"), ("5e-7", "1e-5", "5e-8"),
                                                ("1", "50", "1")])
def test_openbox_s_space_builds_whichever_way_the_log_of_a_bound_rounds(lower, upper, step):
    """ConfigSpace rounds a default to 10 digits: log10(0.5), log10(0.18) and log10(5e-7) round below the bound."""
    from openbox import space as sp

    spec = make_spec(variables=[{"name": "X", "kind": "continuous_step", "lower": lower, "upper": upper, "step": step},
                                {"name": "W", "kind": "continuous_step", "lower": "0.6u", "upper": "1.2u", "step": "0.2u"}])
    search = SearchScale(spec)
    assert search.log.tolist() == [True, False]
    cs = _config_space(spec, sp, search)
    x = cs.get_hyperparameter("X")
    assert (x.lower, x.upper) == (math.log10(float(lower)), math.log10(float(upper))) and x.lower < x.default_value < x.upper
    assert cs.get_hyperparameter("W").q == pytest.approx(0.2)                     # a linear variable keeps its grid
    for text in (lower, upper):
        sp.Configuration(cs, values=_values(spec, {"X": text, "W": "0.6u"}, search))       # a point at either bound is taken

    def metrics(params):
        return {"NF": 1.0 + math.log10(float(params["X"]) / (3 * float(lower))) ** 2}

    history = run_suggest(spec, "openbox_gp_eic", 10, 5, metrics, seed=0, initial_trials=4)
    assert len(history.keys()) == 10 and [o.origin.split(":")[2] for o in history] == ["init"] * 5 + ["acq"] * 5


def test_openbox_s_choice_is_its_own_with_grid_points_already_taken_skipped():
    """OpenBox compares configurations, and two continuous log10 values can snap onto one grid point: grid_suggestions
    keeps OpenBox's ranked choice (Advisor.get_suggestions) and skips a candidate whose grid point is taken."""
    from ic_opt.suggesters.openbox import _numbers, grid_suggestions

    spec = log_spec()
    search = SearchScale(spec)
    history = run_suggest(spec, "openbox_gp_eic", 12, 4, log_bowl, seed=0, initial_trials=4)

    def key(config):
        return space.point_key(space.snap(spec, search.raw_of([_numbers(spec, config)])[0]))

    theirs = OpenBoxSuggester().advisor(spec, history, seed=0).get_suggestions(batch_size=4)
    ours = grid_suggestions(OpenBoxSuggester().advisor(spec, history, seed=0), spec, search, history.keys(), 4)
    kept, seen = [], set(history.keys())
    for config in theirs:
        if key(config) not in seen:
            seen.add(key(config))
            kept.append(config.get_dictionary())
    assert len(ours) == 4 and len({key(c) for c in ours} | history.keys()) == 16
    assert [c.get_dictionary() for c in ours][:len(kept)] == kept


@needs_turbo
def test_turbo_takes_a_history_on_bounds_whose_log_rounds_either_way():
    spec = make_spec(variables=[{"name": "X", "kind": "continuous_step", "lower": "5e-7", "upper": "1e-5", "step": "5e-8"},
                                {"name": "Y", "kind": "continuous_step", "lower": "0.18", "upper": "18", "step": "0.01"}])
    history = run_suggest(spec, "turbo", 10, 5, lambda p: {"NF": 1.0 + float(p["Y"]) / 18}, seed=0)
    assert len(history.keys()) == 10 and {o.origin.split(":")[2] for o in history} == {"init", "tr"}


# -- 4. continuation ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_a_continued_run_with_a_logarithmic_variable_is_an_uninterrupted_one(tmp_path, strategy):
    variables = [{"name": "F", "kind": "integer", "lower": "20", "upper": "30", "step": "2"},
                 {"name": "W", "kind": "continuous_step", "lower": "0.1u", "upper": "10u", "step": "0.1u"}]
    extra = strategy_kwargs(strategy, initial_trials=4)
    spec, store, ex, deck = project(tmp_path / "whole", variables=variables)
    assert SearchScale(spec).log.tolist() == [False, True]
    whole = optimize(spec, ex, store, deck=deck, strategy=strategy, budget=9, batch=3, seed=2, limits=FAKE_HOST, **extra)
    spec, store, ex, deck = project(tmp_path / "parts", variables=variables)
    optimize(spec, ex, store, deck=deck, strategy=strategy, budget=6, batch=3, seed=2, limits=FAKE_HOST, **extra)
    parts = optimize(spec, ex, store, deck=deck, strategy=strategy, budget=9, batch=3, seed=2, limits=FAKE_HOST, **extra)
    assert [(o.params, o.origin) for o in parts] == [(o.params, o.origin) for o in whole]
    assert any(tag in whole[-1].origin for tag in (":tr:", ":acq"))
