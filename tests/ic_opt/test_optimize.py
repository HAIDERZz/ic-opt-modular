"""suggest / optimize with the three suggesters against a cheap synthetic objective."""

from __future__ import annotations

import pytest

from ic_opt.blocks.optimize import adopt, optimize, suggest
from ic_opt.deck import Deck
from ic_opt.observation import ChildResult, Observation
from ic_opt.store import RunStore
from ic_opt.suggesters.turbo import _active_start, _batches
from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor, make_spec, needs_turbo, restamp

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
