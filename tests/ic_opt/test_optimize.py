"""suggest / optimize with the three suggesters against a cheap synthetic objective."""

from __future__ import annotations

import inspect
import itertools
import re
import warnings

import numpy as np
import pytest

from ic_opt import objective as objective_contract
from ic_opt.blocks.optimize import adopt, current_design, optimize, suggest, surrogate_points
from ic_opt.deck import Deck
from ic_opt.observation import ChildResult, Observation, Observations
from ic_opt.recipe import PLAN_MODE
from ic_opt.sim.netlist import exported_values
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.store import RunStore
from ic_opt.suggesters import auto_keywords, resolve_auto
from ic_opt.suggesters.base import minimization_objective, unit_design
from ic_opt.suggesters.openbox import OpenBoxSuggester, initial_design_size
from ic_opt.suggesters.turbo import _active_start, _batches, targets
from tests.ic_opt.fakes import (
    FAKE_HOST,
    FakeSpectreExecutor,
    make_spec,
    minimal_spec,
    needs_turbo,
    restamp,
)

TEMPLATE = "simulator lang=spectre\nparameters temperature=27 F={{F}} W={{W}}\ntran tran stop=10n\n"


def bowl(params, tb, corner):
    """NF is a bowl with its minimum at F=26, W=1.0u; always feasible (NF < 9)."""
    f, w = int(params["F"]), float(params["W"].rstrip("u"))
    return {"NF": 1.0 + ((f - 26) / 10) ** 2 + (w - 1.0) ** 2}


def project(tmp_path, **spec_overrides):
    spec = make_spec(budget={"max_simulations": 200}, **spec_overrides)
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims", bowl)
    return spec, store, ex, Deck(templates={("tb", None): TEMPLATE})


def observed(spec, points, metrics_of, first=0) -> Observations:
    """The observations an engine would record for ``points`` whose aggregated metrics are ``metrics_of(params)``."""
    rows = Observations()
    for index, point in enumerate(points, first):
        metrics = metrics_of(point.params)
        ev = objective_contract.evaluate(spec, metrics)
        rows.append(Observation(obs_id=f"obs_{index}", params=point.params, origin=point.origin, metrics=metrics, fom=ev.fom,
                                objective=ev.objective, feasible=ev.feasible, constraint_penalty=ev.constraint_penalty,
                                status=ev.status, spec_fingerprint="s", pipeline_fingerprint="p", started_at="t", finished_at="t"))
    return rows


def run_suggest(spec, strategy, budget, batch, metrics_of, *, seed=0, **kwargs) -> Observations:
    history = Observations()
    while len(history) < budget:
        points = suggest(spec, history, min(batch, budget - len(history)), strategy=strategy, seed=seed, **kwargs)
        history.extend(observed(spec, points, metrics_of, len(history)))
    return history


def wide_spec(**overrides):
    """A 100 x 100 grid, so that a design of ten points never snaps two onto one grid point."""
    return make_spec(variables=[{"name": "F", "kind": "integer", "lower": "0", "upper": "99", "step": "1"},
                                {"name": "W", "kind": "continuous_step", "lower": "0.1u", "upper": "10u", "step": "0.1u"}],
                     **overrides)


def wide_bowl(params):
    return {"NF": 1.0 + ((int(params["F"]) - 50) / 30) ** 2 + (float(params["W"].rstrip("u")) - 5.0) ** 2 / 10}


def row(spec, params, metrics, *, status=None):
    """One observation; ``status`` overrides the evaluated one (a ``failed:<stage>`` point carries no metrics)."""
    obs = observed(spec, [Point(params, "user")], lambda _p: metrics)[0]
    return obs if status is None else obs.model_copy(update={"status": status, "issues": ["spectre did not finish"]})


@pytest.mark.parametrize("strategy", ["sobol", pytest.param("turbo", marks=needs_turbo), "openbox_gp_eic"])
def test_optimize_finds_the_bowl_and_never_repeats_a_point(tmp_path, strategy):
    spec, store, ex, deck = project(tmp_path)
    obs = optimize(spec, ex, store, deck=deck, strategy=strategy, budget=12, batch=4, seed=1, limits=FAKE_HOST)

    assert len(obs) == 12 and len({o.key for o in obs}) == 12
    assert all(o.origin.startswith("suggest:") for o in obs)
    best = obs.best()[0]
    assert best.objective < 1.2, f"{strategy} best {best.objective} at {best.params}"


@needs_turbo
def test_rerun_with_bigger_budget_continues_instead_of_restarting(tmp_path):
    spec, store, ex, deck = project(tmp_path)
    first = optimize(spec, ex, store, deck=deck, strategy="turbo", budget=6, batch=3, seed=2, limits=FAKE_HOST)
    sims_after_first = sum(c.startswith("spectre") for c in ex.commands)
    again = optimize(spec, ex, store, deck=deck, strategy="turbo", budget=6, batch=3, seed=2, limits=FAKE_HOST)
    assert [o.obs_id for o in again] == [o.obs_id for o in first]
    assert sum(c.startswith("spectre") for c in ex.commands) == sims_after_first     # nothing re-simulated

    more = optimize(spec, ex, store, deck=deck, strategy="turbo", budget=9, batch=3, seed=2, limits=FAKE_HOST)
    assert len(more) == 9 and more[:6] == first
    assert sum(c.startswith("spectre") for c in ex.commands) == 9


@needs_turbo
def test_a_store_stamped_by_the_previous_version_continues_under_optimize(tmp_path):
    """T15.2: observations carrying the legacy (whole-spec) fingerprint count as this problem for the budget and the history."""
    spec, store, ex, deck = project(tmp_path)
    first = optimize(spec, ex, store, deck=deck, strategy="turbo", budget=6, batch=3, seed=2, limits=FAKE_HOST)
    restamp(store, spec_fingerprint=spec._legacy_fingerprint())
    sims = sum(c.startswith("spectre") for c in ex.commands)
    more = optimize(spec, ex, store, deck=deck, strategy="turbo", budget=9, batch=3, seed=2, limits=FAKE_HOST)
    assert len(more) == 9 and [o.obs_id for o in more[:6]] == [o.obs_id for o in first]
    assert sum(c.startswith("spectre") for c in ex.commands) == sims + 3                 # only the three new points ran


def test_turbo_batches_and_restart_bookkeeping():
    def obs(i, origin):
        return Observation(obs_id=f"obs_{i}", params={"F": str(20 + 2 * (i % 6)), "W": "0.6u"}, origin=origin,
                           objective=1.0, feasible=True, status="ok", spec_fingerprint="s", pipeline_fingerprint="p",
                           started_at="t", finished_at="t")
    rows = [obs(0, "user"), obs(1, "user"), obs(2, "suggest:turbo:init:0:2"), obs(3, "suggest:turbo:init:0:2"),
            obs(4, "suggest:turbo:tr:0:4"), obs(5, "suggest:turbo:tr:0:5"), obs(6, "suggest:turbo:init:1:6")]
    groups = _batches(rows)
    assert [(kind, len(r)) for kind, r in groups] == [("init", 2), ("init", 2), ("tr", 1), ("tr", 1), ("init", 1)]
    assert _active_start(groups) == 4          # the restart begins a fresh region


def test_suggest_dedupes_against_history_and_initial(tmp_path):
    spec, store, ex, deck = project(tmp_path)
    seen = optimize(spec, ex, store, deck=deck, strategy="sobol", budget=4, batch=4, seed=0, limits=FAKE_HOST)
    points = suggest(spec, seen, 5, strategy="sobol", seed=0)
    assert len(points) == 5 and not ({p.key for p in points} & seen.keys())


def test_adopt_rescores_foreign_observations_under_this_spec(tmp_path):
    spec, *_ = project(tmp_path)
    foreign = [
        Observation(obs_id="x1", params={"F": "24", "W": "0.8u"}, origin="user", metrics={"NF": 8.5}, fom=99.0, objective=99.0,
                    feasible=False, status="constraint_failed", spec_fingerprint="other", pipeline_fingerprint="p",
                    started_at="t", finished_at="t"),
        Observation(obs_id="x2", params={"F": "24", "W": "0.8u"}, origin="user", metrics={"NF": 12.0}, objective=None,
                    feasible=False, status="constraint_failed", spec_fingerprint="other", pipeline_fingerprint="p",
                    started_at="t", finished_at="t"),
        Observation(obs_id="x3", params={"F": "21", "W": "0.8u"}, origin="user", metrics={"NF": 1.0}, objective=1.0,
                    feasible=True, status="ok", spec_fingerprint="other", pipeline_fingerprint="p", started_at="t", finished_at="t"),
    ]
    adopted = adopt(spec, foreign)
    assert [o.obs_id for o in adopted] == ["x1", "x2"]                 # x3 is off-grid for this spec
    assert adopted[0].feasible and adopted[0].objective == 8.5 and adopted[0].origin == "initial:user"
    assert adopted[1].status == "constraint_failed" and not adopted[1].feasible
    assert isinstance(adopted[0].children, dict) and ChildResult  # keeps the model shape


def test_surrogate_points_counts_what_follows_the_design_whatever_the_batch():
    """T17.0b: a batch that reaches the end of the design is completed by the surrogate once the history at its start holds
    the surrogate minimum (here 3); until 0.4.0 a batch that began inside the design was design in full (N-27)."""
    assert surrogate_points(0, 12, 6, 8, needed=5) == 4          # N-27's shape: 6 design, then 2 design + 4 surrogate
    assert surrogate_points(0, 12, 4, 8, needed=3) == 4
    assert surrogate_points(0, 12, 6, 4, needed=3) == 6 and surrogate_points(6, 6, 6, 4, needed=3) == 6
    assert surrogate_points(0, 9, 8, 8, needed=3) == 1 and surrogate_points(0, 8, 8, 8, needed=3) == 0
    assert surrogate_points(0, 30, 10, 22, needed=12) == 8       # "initial design 22" is 22 points, not 30 (2026-09-28)
    assert surrogate_points(0, 4, 4, 2, needed=3) == 0           # one batch from nothing: no successful point to fit on
    assert surrogate_points(0, 10, 5, 4, start=6, needed=3) == 4  # six start points exceed the design; they come first


def test_initial_design_size_defaults_to_the_smaller_of_twice_the_variables_and_half_the_budget():
    """N-30 (user decision 2026-09-27): a small run must reach the surrogate. N-27's 12 points on 4 variables: design 6, not 8."""
    spec = make_spec()                                         # two variables
    four = make_spec(variables=[{"name": n, "kind": "integer", "lower": "1", "upper": "9", "step": "1"} for n in "ABCD"])
    assert initial_design_size(four, None, 12) == 6 and surrogate_points(0, 12, 6, 6, needed=5) == 6
    assert initial_design_size(four, None, 100) == 8 and initial_design_size(four, None) == 8
    assert initial_design_size(spec, None, 12) == 4 and initial_design_size(spec, None, 3) == 1 and initial_design_size(spec, None, 1) == 1
    assert initial_design_size(four, 3, 100) == 3                # an explicit initial_trials wins


def test_openbox_marks_initial_design_points_and_says_when_the_surrogate_never_proposes(tmp_path, capsys):
    """Two variables. A budget of 4 in one batch (design min(4, 2) = 2) starts with nothing to fit a surrogate on, so it is
    design throughout, and the run says so; continuing to 8 (design 4) makes the second batch the surrogate's, and every
    point's origin says which served it (N-27, ISSUE-8)."""
    spec, store, ex, deck = project(tmp_path)
    first = optimize(spec, ex, store, deck=deck, strategy="openbox_gp_eic", budget=4, batch=4, seed=1, limits=FAKE_HOST)
    out = capsys.readouterr().out
    assert "[optimize] openbox initial design 2 points: the surrogate proposes 0 of the 4 new points -- WARNING: none" in out
    assert {o.origin for o in first} == {"suggest:openbox_gp_eic:init"}
    both = optimize(spec, ex, store, deck=deck, strategy="openbox_gp_eic", budget=8, batch=4, seed=1, limits=FAKE_HOST)
    out = capsys.readouterr().out
    assert "[optimize] openbox initial design 4 points: the surrogate proposes 4 of the 4 new points" in out and "WARNING" not in out
    new = [o for o in both if o.obs_id not in {f.obs_id for f in first}]
    assert len(new) == 4 and {o.origin for o in new} == {"suggest:openbox_gp_eic:acq"}
    assert "[optimize] step='optimize': no current design: the deck carries no export of testbench tb" in out


# -- T17.0b: what the strategies are fed -----------------------------------------------------------------------------


def test_openbox_is_fed_true_objectives_and_failed_trials_as_failed():
    """Maximize NF with NF < 9. An infeasible point enters with its true objective (-fom), a failed one as OpenBox's own
    failed trial, whose objective and constraints OpenBox fills with the successful trials' column maxima; nothing is 1e6."""
    from openbox.utils.constants import FAILED, SUCCESS

    spec = make_spec(objective={"direction": "maximize", "expression": "NF"})
    rows = Observations([row(spec, {"F": "20", "W": "0.6u"}, {"NF": 4.0}), row(spec, {"F": "22", "W": "0.6u"}, {"NF": 12.0}),
                         row(spec, {"F": "24", "W": "0.6u"}, {}), row(spec, {"F": "26", "W": "0.6u"}, {}, status="failed:spectre")])
    assert [o.status for o in rows] == ["ok", "constraint_failed", "metric_failed", "failed:spectre"]
    assert [minimization_objective(spec, o) for o in rows] == [-4.0, -12.0, None, None]
    history = OpenBoxSuggester().advisor(spec, rows, seed=0).history
    assert history.trial_states == [SUCCESS, SUCCESS, FAILED, FAILED]
    assert history.objectives[:2] == [[-4.0], [-12.0]] and history.constraints[:2] == [[-5.0], [3.0]]
    assert history.get_objectives(transform="infeasible").ravel().tolist() == [-4.0] * 4      # OpenBox's own handling
    assert history.get_constraints(transform="failed").ravel().tolist() == [-5.0, 3.0, 3.0, 3.0]    # fed nan, filled by OpenBox
    assert history.get_incumbent_value() == -4.0                 # a feasible point: OpenBox's own reference value


def test_openbox_gets_a_finite_reference_value_while_nothing_is_feasible():
    """2026-09-28: with no feasible point EIC's reference was inf and 417 of 2000 candidates scored inf, 1583 scored 0. Now
    it is the largest objective among the successful trials, so candidates are ranked by their chance of being feasible."""
    spec = make_spec(objective={"direction": "maximize", "expression": "NF"})
    rows = Observations([row(spec, {"F": "20", "W": "0.6u"}, {"NF": 12.0}), row(spec, {"F": "22", "W": "0.6u"}, {"NF": 15.0}),
                         row(spec, {"F": "24", "W": "0.6u"}, {"NF": 30.0}), row(spec, {"F": "26", "W": "0.6u"}, {})])
    history = OpenBoxSuggester().advisor(spec, rows, seed=0).history
    assert history.get_feasible_count() == 0 and history.get_incumbent_value() == -12.0
    assert len(suggest(spec, rows, 3, strategy="openbox_gp_eic", seed=0, initial_trials=4)) == 3


def test_the_openbox_method_the_finite_reference_replaces_is_still_there():
    """``finite_reference`` replaces ``History.get_incumbent_value`` on the instance, checked against OpenBox 0.9.0: if the
    method goes, or the advisor stops reading it for EIC's reference value, the replacement silently does nothing."""
    import openbox
    from openbox.core.generic_advisor import Advisor
    from openbox.utils.history import History

    assert callable(History.get_incumbent_value) and openbox.__version__ == "0.9.0"
    assert "history.get_incumbent_value()" in inspect.getsource(Advisor._get_bo_candidates)


def test_a_feasibility_only_spec_feeds_zero_and_reaches_the_surrogate():
    """No objective: every point that is not failed is fed 0.0 (until 0.4.0 they all fell through to the 1e6 penalty)."""
    spec = wide_spec(objective=None)
    rows = Observations([row(spec, {"F": "20", "W": "0.6u"}, {"NF": 4.0}), row(spec, {"F": "22", "W": "0.6u"}, {"NF": 12.0})])
    assert OpenBoxSuggester().advisor(spec, rows, seed=0).history.objectives == [[0.0], [0.0]]
    history = run_suggest(spec, "openbox_gp_eic", 10, 5, wide_bowl, initial_trials=4)
    assert [o.origin.rsplit(":", 1)[1] for o in history] == ["init"] * 5 + ["acq"] * 5


def test_turbo_targets_put_every_point_on_one_scale():
    """Minimize NF, NF < 9. With feasible points (2 and 4: spread 2) infeasible ones sit (1 + min(v, 3)) spreads above the
    worst feasible one (v = sqrt(constraint_penalty): 1 at NF 18, 9 at NF 90, clipped at 3), a failed one at the largest
    target; with none, the target is v, and a failed point the largest v (1.0 when there is none)."""
    spec = make_spec()
    ok2, ok4 = row(spec, {"F": "20", "W": "0.6u"}, {"NF": 2.0}), row(spec, {"F": "22", "W": "0.6u"}, {"NF": 4.0})
    miss1, miss9 = row(spec, {"F": "24", "W": "0.6u"}, {"NF": 18.0}), row(spec, {"F": "26", "W": "0.6u"}, {"NF": 90.0})
    half, failed = row(spec, {"F": "28", "W": "0.6u"}, {"NF": 13.5}), row(spec, {"F": "30", "W": "0.6u"}, {})
    assert targets(spec, Observations([ok2, ok4, miss1, miss9, failed])).tolist() == [2.0, 4.0, 8.0, 12.0, 12.0]
    assert targets(spec, Observations([miss1, half, failed])).tolist() == [1.0, 0.5, 1.0]
    assert targets(spec, Observations([failed, failed])).tolist() == [1.0, 1.0]
    assert targets(spec, Observations([ok2, miss1])).tolist() == [2.0, 2.0 + 2 * 2e-3]     # one feasible point: spread 1e-3


@needs_turbo
def test_turbo_counts_start_points_and_other_untagged_rows_as_its_initial_design():
    """T17.0b: until 0.4.0 untagged rows joined the first region's data but TuRBO still ran its whole design after them."""
    spec = make_spec()
    start = [{"F": "20", "W": "0.6u"}, {"F": "30", "W": "1.2u"}, {"F": "24", "W": "1.0u"}, {"F": "26", "W": "0.8u"}]
    history = observed(spec, suggest(spec, [], 4, strategy="turbo", start=start), lambda p: bowl(p, "tb", None))
    assert [o.origin for o in history] == ["start"] * 4                      # n_init = 2 x 2 variables
    assert {p.origin for p in suggest(spec, history, 2, strategy="turbo", seed=0)} == {"suggest:turbo:tr:0:4"}
    mixed = suggest(spec, history[:2], 3, strategy="turbo", seed=0, start=start[2:3])
    assert mixed[0].origin == "start" and mixed[1].origin == "suggest:turbo:init:0:2"


@pytest.mark.parametrize("strategy", [pytest.param("turbo", marks=needs_turbo), "openbox_gp_eic"])
def test_a_continued_run_proposes_what_an_uninterrupted_one_does(tmp_path, strategy):
    """The same history and seed give the same proposal: 6 points then 3 more is the 9-point run (the last batch is the
    model's: TuRBO's trust region replayed from targets on the whole history, OpenBox's surrogate)."""
    extra = {"initial_trials": 4} if strategy.startswith("openbox") else {}     # the design size must not follow the budget
    spec, store, ex, deck = project(tmp_path / "whole")
    whole = optimize(spec, ex, store, deck=deck, strategy=strategy, budget=9, batch=3, seed=2, limits=FAKE_HOST, **extra)
    spec, store, ex, deck = project(tmp_path / "parts")
    optimize(spec, ex, store, deck=deck, strategy=strategy, budget=6, batch=3, seed=2, limits=FAKE_HOST, **extra)
    parts = optimize(spec, ex, store, deck=deck, strategy=strategy, budget=9, batch=3, seed=2, limits=FAKE_HOST, **extra)
    assert [(o.params, o.origin) for o in parts] == [(o.params, o.origin) for o in whole]
    assert any(tag in whole[-1].origin for tag in (":tr:", ":acq"))


# -- T17.0b: the initial design is ours ---------------------------------------------------------------------------------


def test_the_openbox_design_is_the_same_whatever_the_batch_size():
    """One design of initial_trials points per (spec, seed), served in order; a batch that reaches its end is completed by
    the surrogate (OpenBox 0.4.0 served its design batch by batch, random, and a batch starting inside it was design in full)."""
    spec = wide_spec()
    runs = {b: run_suggest(spec, "openbox_gp_eic", 12, b, wide_bowl, seed=5, initial_trials=10) for b in (1, 4, 10)}
    designs = {b: [o.params for o in history if o.origin == "suggest:openbox_gp_eic:init"] for b, history in runs.items()}
    assert len(designs[1]) == 10 and designs[1] == designs[4] == designs[10]
    assert [o.origin.rsplit(":", 1)[1] for o in runs[4][8:]] == ["init", "init", "acq", "acq"]    # the batch that straddles
    assert all(o.origin.endswith(":acq") for o in runs[1][10:] + runs[10][10:])


def test_the_design_keeps_its_first_points_when_it_grows_and_sobol_never_warns():
    """A continued run sees the same design: the seed gives the same first points whatever the design size. scipy warned
    "balance properties ... require n to be a power of 2" on every design until the next power of two was drawn."""
    spec = wide_spec()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        small = suggest(spec, [], 6, strategy="openbox_gp_eic", seed=3, initial_trials=6)
        large = suggest(spec, [], 10, strategy="openbox_gp_eic", seed=3, initial_trials=10)
        unit = unit_design("sobol", 5, 3, 7)
    assert [p.params for p in large[:6]] == [p.params for p in small]
    assert np.array_equal(unit, unit_design("sobol", 12, 3, 7)[:5]) and unit.shape == (5, 3)
    assert not [w for w in caught if "power of 2" in str(w.message)]


# -- T17.0b: start points and the design as exported ---------------------------------------------------------------------


def test_start_points_come_first_once_and_count_as_initial_design():
    spec = make_spec()
    start = [{"F": "24", "W": "0.8u"}, {"F": "30", "W": "1.2u"}]
    first = suggest(spec, [], 3, strategy="openbox_gp_eic", seed=0, start=start, initial_trials=4)
    assert [p.params for p in first[:2]] == start and [p.origin for p in first] == ["start", "start", "suggest:openbox_gp_eic:init"]
    history = observed(spec, first, lambda p: bowl(p, "tb", None))
    again = suggest(spec, history, 3, strategy="openbox_gp_eic", seed=0, start=start, initial_trials=4)
    assert [p.origin for p in again] == ["suggest:openbox_gp_eic:init"] + ["suggest:openbox_gp_eic:acq"] * 2   # 2 start + 2 design
    assert [p.params for p in suggest(spec, [], 1, strategy="openbox_gp_eic", start=start)] == start[:1]
    with pytest.raises(ValueError, match="not aligned"):
        suggest(spec, [], 2, strategy="openbox_gp_eic", start=[{"F": "21", "W": "0.8u"}])


def test_the_current_design_is_read_from_the_exports():
    two = make_spec(testbenches=[{"id": t, "maestro_point_root": f"/x/{t}", "virtuoso_library": "l", "cell": "c", "test_name": t}
                                 for t in ("tb", "tb2")],
                    metrics=[{"name": "NF", "unit": "dB", "expression": 'value(getData("NF"))', "testbench": "tb"}])

    def export(params):
        return f"simulator lang=spectre\nparameters temperature=27 {params}\ntran tran stop=10n\n"

    assert current_design(two, {"tb": export("F=20 W=0.6u")}) == (
        {"F": "20", "W": "0.6u"}, "current design (the exported netlists) first: F=20 W=0.6u")
    assert current_design(two, {"tb": export("F=20 W=0.6u"), "tb2": export("W=600n F=20")})[0] == {"F": "20", "W": "0.6u"}
    assert current_design(two, {"tb": export("F=20 W=0.6u"), "tb2": export("F=20 W=0.8u")}) == (
        None, "no current design: the testbenches disagree on W (tb 0.6u, tb2 0.8u)")
    assert current_design(two, {"tb": export("F=20 W=0.75u")}) == (
        {"F": "20", "W": "0.8u"}, "current design (the exported netlists) first: F=20 W=0.8u; W=0.75u is between grid points: moved to 0.8u")
    assert current_design(two, {"tb": export("F=20 W=2u")}) == (None, "no current design: W=2u is outside its range [0.6u, 1.2u]")
    assert current_design(two, {"tb": export("F=20 W=wmin*2")})[1] == "no current design: W=wmin*2 is not a number in the unit u of its range"
    assert current_design(two, {"tb": export("F=20")})[1] == "no current design: tb's export: variable W was not found in top-level parameters"


def test_exported_values_reads_the_top_level_parameters_statement():
    text = "parameters F=20 \\\n    W=0.6u\nsubckt amp a b\nparameters W=5u\nends amp\n"
    assert exported_values(text, ["F", "W"]) == {"F": "20", "W": "0.6u"}
    with pytest.raises(ValueError, match="more than once"):
        exported_values(text + "parameters F=22\n", ["F", "W"])


def test_a_continued_optimize_does_not_evaluate_its_start_points_again(tmp_path):
    spec, store, ex, deck = project(tmp_path)
    start = [{"F": "24", "W": "0.8u"}, {"F": "30", "W": "1.2u"}]
    first = optimize(spec, ex, store, deck=deck, strategy="random", budget=3, batch=2, seed=1, start=start, limits=FAKE_HOST)
    assert [o.origin for o in first][:2] == ["start", "start"] and [o.params for o in first][:2] == start
    more = optimize(spec, ex, store, deck=deck, strategy="random", budget=6, batch=2, seed=1, start=start, limits=FAKE_HOST)
    assert len(more) == 6 and [o.origin for o in more].count("start") == 2
    assert sum(c.startswith("spectre") for c in ex.commands) == 6


# -- strategy auto (T17.2) ------------------------------------------------------------------------------------------------

ONE = "no EM devices, one condition"
# What strategy="openbox_gp_eic", budget 8, batch 4, seed 1 proposed on the bowl before the default changed (8048977).
OPENBOX_POINTS = [("22", "1u", "init"), ("28", "0.6u", "init"), ("26", "1.2u", "init"), ("24", "0.8u", "init"),
                  ("28", "1u", "acq"), ("20", "0.6u", "acq"), ("20", "1u", "acq"), ("30", "1.2u", "acq")]


def devices_spec() -> Spec:
    """A spec with an EM device; what is asked of it here is decided before a geometry is built."""
    d = minimal_spec()
    d["devices"] = [{"id": "d", "generator": "demo", "profile": "demo_6m", "ports": ["P1", "N1"], "fixed": {"turns": 1}}]
    d["variables"] = d["variables"] + [{"name": "d.od", "kind": "integer", "lower": "20", "upper": "60", "step": "10"}]
    return Spec.model_validate(d)


def at_corners(rows, *corners) -> list[Observation]:
    return [o.model_copy(update={"children": {f"tb/{c}": ChildResult(unit="tb", corner=c, status="ok") for c in corners}})
            for o in rows]


def some_rows(spec) -> Observations:
    grid = [Point({"F": f, "W": w}, "user") for f, w in (("20", "0.6u"), ("22", "0.8u"), ("24", "1u"), ("26", "1.2u"),
                                                         ("28", "0.8u"), ("30", "1u"))]
    return observed(spec, grid, lambda p: bowl(p, "tb", None))


def planned(fn, *args, **kwargs):
    token = PLAN_MODE.set(True)
    try:
        return fn(*args, **kwargs)
    finally:
        PLAN_MODE.reset(token)


def test_auto_resolves_to_metric_gp_for_a_circuit_at_one_condition_and_to_openbox_otherwise():
    plain, three = make_spec(), make_spec(corners=[{"id": c} for c in ("tt", "ss", "ff")])
    assert resolve_auto(plain, len(plain.corner_ids)) == ("metric_gp", ONE)            # no corners: one condition
    assert resolve_auto(devices_spec(), 1) == ("openbox_gp_eic", "metric_gp does not take EM devices yet")
    assert resolve_auto(three, len(three.corner_ids)) == ("openbox_gp_eic",
                                                          "metric_gp works on one condition; this run covers 3 corners")
    assert resolve_auto(three, len(["tt"])) == ("metric_gp", ONE)                      # corners=["tt"]
    rows = some_rows(plain)
    assert resolve_auto(three, 1, at_corners(rows, "tt")) == ("metric_gp", ONE)
    assert resolve_auto(three, 1, at_corners(rows, "tt", "ss")) == (
        "openbox_gp_eic", "metric_gp works on one condition; the history holds points evaluated at the corners ss, tt")


def test_optimize_without_a_strategy_runs_metric_gp_on_a_circuit_at_one_condition(tmp_path, capsys):
    spec, store, ex, deck = project(tmp_path)
    planned(optimize, spec, ex, store, deck=deck, budget=8, batch=4, current=False, limits=FAKE_HOST)
    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"[plan] strategy auto: metric_gp ({ONE})"
    assert "[plan] opt.optimize step='optimize' strategy=metric_gp: 0/8 points done" in out
    assert "[plan] metric_gp initial design 4 points: the model proposes 4 of the 8 new points" in out
    assert ex.commands == [] and store.observations() == []
    obs = optimize(spec, ex, store, deck=deck, budget=8, batch=4, seed=1, limits=FAKE_HOST)
    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"[optimize] strategy auto: metric_gp ({ONE})"
    assert "[optimize] metric_gp initial design 4 points: the model proposes 4 of the 8 new points" in out
    assert len(obs) == 8 and [o.origin for o in obs] == ["suggest:metric_gp:init"] * 4 + ["suggest:metric_gp:grid:4"] * 4
    three = make_spec(corners=[{"id": c} for c in ("tt", "ss", "ff")], budget={"max_simulations": 200})
    planned(optimize, three, ex, RunStore(tmp_path / "tt"), deck=deck, budget=8, corners=["tt"], current=False,
            limits=FAKE_HOST)
    assert f"[plan] strategy auto: metric_gp ({ONE})" in capsys.readouterr().out


def test_optimize_without_a_strategy_over_two_corners_runs_openbox_and_is_not_refused(tmp_path, capsys):
    spec, store, ex, deck = project(tmp_path / "two", corners=[{"id": "tt"}, {"id": "ss"}])
    obs = optimize(spec, ex, store, deck=deck, budget=4, batch=2, seed=1, current=False, limits=FAKE_HOST)
    out = capsys.readouterr().out
    assert out.splitlines()[0] == ("[optimize] strategy auto: openbox_gp_eic (metric_gp works on one condition; "
                                   "this run covers 2 corners)")
    assert "[optimize] openbox initial design" in out
    assert len(obs) == 4 and all(o.origin.startswith("suggest:openbox_gp_eic:") for o in obs)
    assert all(o.corners() == {"tt", "ss"} for o in obs)       # this deck fails every child: each point stops at tt (T17.8)
    # the same rows handed to a circuit at one condition as initial=: metric_gp would refuse them, auto does not pick it
    single, store, ex, deck = project(tmp_path / "one")
    more = optimize(single, ex, store, deck=deck, budget=2, batch=2, seed=1, initial=obs, current=False, limits=FAKE_HOST)
    assert ("[optimize] strategy auto: openbox_gp_eic (metric_gp works on one condition; the history holds points "
            "evaluated at the corners ss, tt)") in capsys.readouterr().out
    assert len(more) == 2 and all(o.origin.startswith("suggest:openbox_gp_eic:") for o in more)


def test_optimize_without_a_strategy_on_a_spec_with_a_device_runs_openbox(tmp_path, capsys):
    pytest.importorskip("klayout.db")
    from ic_opt.blocks.netlist import import_netlists
    from tests.ic_opt.test_em_circuit import em_circuit_spec

    spec = em_circuit_spec(tmp_path)
    store = RunStore(tmp_path / "proj")
    ex = FakeSpectreExecutor(store.root / "sims", lambda p, tb, c: {"NF": 7.0 + int(p["F"]) / 100})
    obs = optimize(spec, ex, store, deck=import_netlists(spec, ex, store), budget=4, batch=2, seed=1, limits=FAKE_HOST)
    out = capsys.readouterr().out
    assert "[optimize] strategy auto: openbox_gp_eic (metric_gp does not take EM devices yet)" in out
    assert len(obs) == 4 and all(o.status == "ok" and o.origin.startswith("suggest:openbox_gp_eic:") for o in obs)


def test_a_named_strategy_is_taken_as_named(tmp_path, capsys):
    """Nothing changes for an explicit strategy: no auto line, and openbox_gp_eic on a circuit at one condition proposes
    the points it proposed before the default changed."""
    spec, store, ex, deck = project(tmp_path)
    obs = optimize(spec, ex, store, deck=deck, strategy="openbox_gp_eic", budget=8, batch=4, seed=1, limits=FAKE_HOST)
    assert "strategy auto" not in capsys.readouterr().out
    assert [(o.params["F"], o.params["W"], o.origin) for o in obs] == \
        [(f, w, f"suggest:openbox_gp_eic:{tag}") for f, w, tag in OPENBOX_POINTS]


def test_a_keyword_the_resolved_strategy_does_not_take_is_refused_before_anything_runs(tmp_path, capsys):
    assert auto_keywords("metric_gp") == ["initial_trials", "wide_share"]
    assert auto_keywords("openbox_gp_eic") == ["initial_trials", "initialization", "workdir"]
    spec, store, ex, deck = project(tmp_path)
    cases = [
        (spec, "initialization", (f"strategy auto resolved to metric_gp ({ONE}), which does not take the keyword "
         "'initialization' (it takes initial_trials, wide_share); 'initialization' is openbox_gp_eic's: name that strategy "
         "(strategy=openbox_gp_eic) to pass it")),
        (devices_spec(), "wide_share", ("strategy auto resolved to openbox_gp_eic (metric_gp does not take EM devices yet), "
         "which does not take the keyword 'wide_share' (it takes initial_trials, initialization, workdir); 'wide_share' is "
         "metric_gp's: name that strategy (strategy=metric_gp) to pass it")),
        (spec, "n_init", (f"strategy auto resolved to metric_gp ({ONE}), which does not take the keyword 'n_init' "
         "(it takes initial_trials, wide_share)")),
    ]
    for (s, keyword, message), plan in itertools.product(cases, (False, True)):
        token = PLAN_MODE.set(plan)
        try:
            with pytest.raises(ValueError, match=re.escape(message) + "$"):
                optimize(s, ex, store, deck=deck, budget=4, limits=FAKE_HOST, **{keyword: 0.5})
        finally:
            PLAN_MODE.reset(token)
        with pytest.raises(ValueError, match=re.escape(message) + "$"):
            suggest(s, [], 2, **{keyword: 0.5})
    assert ex.commands == [] and store.observations() == [] and capsys.readouterr().out == ""
    assert len(suggest(spec, [], 2, initial_trials=4)) == 2                     # a keyword both take is passed on


def test_suggest_resolves_auto_from_the_spec_and_the_history_it_is_handed():
    """``opt.suggest`` has no corners argument: one corner id in the spec and a history at one condition give metric_gp.
    A history that also holds points at another corner (a store written while the spec had two, handed over whole by
    ``ic-opt call opt.suggest``) would make metric_gp refuse it: auto resolves to openbox_gp_eic instead."""
    tt = make_spec(corners=[{"id": "tt"}])
    rows = at_corners(some_rows(tt), "tt")
    assert all(p.origin.startswith("suggest:metric_gp:") for p in suggest(tt, rows, 3, seed=0, initial_trials=4))
    mixed = at_corners(rows[:2], "tt", "ss") + rows[2:]
    with pytest.raises(ValueError, match="corners ss, tt"):
        suggest(tt, mixed, 3, strategy="metric_gp", seed=0, initial_trials=4)
    assert all(p.origin.startswith("suggest:openbox_gp_eic:") for p in suggest(tt, mixed, 3, seed=0, initial_trials=4))
    two = make_spec(corners=[{"id": "tt"}, {"id": "ss"}])
    assert all(p.origin.startswith("suggest:openbox_gp_eic:") for p in suggest(two, [], 2, seed=0))
    assert all(p.origin.startswith("suggest:openbox_gp_eic:") for p in suggest(devices_spec(), [], 2, seed=0))
    assert all(p.origin.startswith("suggest:metric_gp:") for p in suggest(make_spec(), [], 2, seed=0))


def test_a_continued_auto_run_proposes_what_an_uninterrupted_one_does(tmp_path, capsys):
    """With ``initial_trials`` stated (metric_gp's default design follows the budget), budget 8 then 12 is budget 12."""
    variables = [{"name": "F", "kind": "integer", "lower": "20", "upper": "60", "step": "2"},
                 {"name": "W", "kind": "continuous_step", "lower": "0.6u", "upper": "3u", "step": "0.2u"}]
    spec, store, ex, deck = project(tmp_path / "whole", variables=variables)
    whole = optimize(spec, ex, store, deck=deck, budget=12, batch=4, seed=2, initial_trials=8, limits=FAKE_HOST)
    spec, store, ex, deck = project(tmp_path / "parts", variables=variables)
    optimize(spec, ex, store, deck=deck, budget=8, batch=4, seed=2, initial_trials=8, limits=FAKE_HOST)
    parts = optimize(spec, ex, store, deck=deck, budget=12, batch=4, seed=2, initial_trials=8, limits=FAKE_HOST)
    assert [(o.params, o.origin) for o in parts] == [(o.params, o.origin) for o in whole]
    assert [o.origin for o in whole] == ["suggest:metric_gp:init"] * 8 + ["suggest:metric_gp:grid:8"] * 4
    assert capsys.readouterr().out.count(f"[optimize] strategy auto: metric_gp ({ONE})") == 3
