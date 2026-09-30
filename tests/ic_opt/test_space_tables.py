"""Allowed combinations (T18.2A specification, section 5): some variables may take only the combinations of their levels
a table lists (``space.Table``, what ``ic_opt.library.link.tables`` gives; replaced here by hand-built tables). The
space, metric_gp's coordinates and candidates, every strategy through ``opt.suggest``, the advice and the point blocks
hand out valid points only; without a table nothing changes.

The oracles here are written from the specification, not from the code: the valid points by testing every point of the
full grid, the nearest combination by measuring the distance to every combination of the table."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
import time
from decimal import Decimal

import numpy as np
import pytest

from ic_opt import advice as advice_rules
from ic_opt import space
from ic_opt.blocks import points
from ic_opt.blocks.optimize import advise, suggest
from ic_opt.library import link
from ic_opt.observation import Observations
from ic_opt.space import Point, Table
from ic_opt.store import RunStore
from ic_opt.suggesters.base import unit_design
from ic_opt.suggesters.metric_gp import MetricGpSuggester, candidates, region, select
from ic_opt.suggesters.metric_gp.compose import Composer, metric_scales, true_arrays
from ic_opt.suggesters.metric_gp.coords import Coords, keys
from tests.ic_opt.fakes import needs_turbo
from tests.ic_opt.test_metric_gp import integer, observe, run, spec_of, stepped

AUTHOR = {"author": "a person", "reason": "a test"}
STRATEGIES = ["metric_gp", "random", "sobol", "openbox_gp_eic", pytest.param("turbo", marks=needs_turbo)]


def six_spec():
    """Three free variables (F, W, R) and a device's three (x.Lp from 0.1n to 2n: a logarithmic range; x.Ls, x.k
    linear), interleaved: 4 x 20 x 4 x 11 x 5 x 9 = 158400 grid points."""
    return spec_of([integer("F", 1, 4), stepped("x.Lp", "0.1n", "2n", "0.1n"), stepped("W", "0.5u", "2u", "0.5u"),
                    stepped("x.Ls", "0.2n", "1.2n", "0.1n"), stepped("R", 0, 1, 0.25), stepped("x.k", 0.5, 0.9, 0.05)],
                   ["f", "g"], [{"metric": "g", "op": "lt", "value": "0.15"}], {"direction": "minimize", "expression": "f"})


def small_spec():
    """F and the device's three variables; with a table of 30 combinations, 120 valid points: the whole grid is metric_gp's
    candidate set."""
    return spec_of([integer("F", 1, 4), stepped("x.Lp", "0.1n", "2n", "0.1n"), stepped("x.Ls", "0.2n", "1.2n", "0.1n"),
                    stepped("x.k", 0.5, 0.9, 0.05)],
                   ["f", "g"], [{"metric": "g", "op": "lt", "value": "0.15"}], {"direction": "minimize", "expression": "f"})


def metrics(params):
    """f: a bowl whose bottom is near F=3, x.Lp=0.5n (in decades), W=1.2u, x.Ls=0.8n, R=0.6, x.k=0.75; g < 0.15 holds
    while x.k stays within 0.15 of 0.7."""
    v = {name: float(space.parse_scalar(text)[0]) for name, text in params.items()}
    f = (((v["F"] - 3) / 3) ** 2 + (v.get("W", 1.2) - 1.2) ** 2 + (v.get("R", 0.6) - 0.6) ** 2
         + math.log10(v["x.Lp"] / 0.5) ** 2 + (v["x.Ls"] - 0.8) ** 2 + (v["x.k"] - 0.75) ** 2)
    return {"f": f, "g": abs(v["x.k"] - 0.7)}


def drawn(count, sizes, seed):
    """``count`` distinct combinations of level indices of variables of ``sizes`` levels."""
    every = list(itertools.product(*(range(size) for size in sizes)))
    return tuple(every[i] for i in np.random.default_rng(seed).choice(len(every), count, replace=False))


DEVICE = Table(("x.Lp", "x.Ls", "x.k"), drawn(40, (20, 11, 9), 0), "device x (library rows)")     # 40 of 1980
PAIR = Table(("x.Lp", "x.Ls"), drawn(15, (20, 11), 1), "device x (library rows)")                 # 15 of 220
SECOND = Table(("R", "x.k"), drawn(12, (5, 9), 2), "device y (library rows)")                     # 12 of 45
CASES = {"one table": [DEVICE], "two tables": [PAIR, SECOND]}


def linking(monkeypatch, found):
    monkeypatch.setattr(link, "tables", lambda spec: list(found))


@pytest.fixture(params=list(CASES))
def tabled(request, monkeypatch):
    """The six-variable spec with one table over three of its variables, or with two tables."""
    linking(monkeypatch, CASES[request.param])
    return six_spec(), CASES[request.param]


# -- oracles ---------------------------------------------------------------------------------------------------------------


def variable(spec, name):
    return next(v for v in spec.variables if v.name == name)


def level(spec, name, text) -> int:
    v = variable(spec, name)
    return int((space.parse_scalar(text)[0] - space.parse_scalar(v.lower)[0]) / space.parse_scalar(v.step)[0])


def value(spec, name, k) -> float:
    v = variable(spec, name)
    return float(space.parse_scalar(v.lower)[0] + Decimal(int(k)) * space.parse_scalar(v.step)[0])


def text_of(spec, row) -> dict[str, str]:
    """A row of level indices as parameter text."""
    out = {}
    for v, k in zip(spec.variables, row, strict=True):
        lower, unit = space.parse_scalar(v.lower)
        out[v.name] = space.format_value(lower + Decimal(int(k)) * space.parse_scalar(v.step)[0], unit)
    return out


def row_of(spec, params) -> tuple[int, ...]:
    return tuple(level(spec, v.name, params[v.name]) for v in spec.variables)


def brute_valid(spec, found) -> set[tuple[int, ...]]:
    """Every valid point as level indices, found by testing every point of the full grid."""
    counts = [space.grid_count(v) for v in spec.variables]
    grid = np.indices(counts).reshape(len(counts), -1).T
    names = [v.name for v in spec.variables]
    ok = np.ones(len(grid), dtype=bool)
    for table in found:
        allowed = set(table.levels)
        ok &= np.array([tuple(r) in allowed for r in grid[:, [names.index(n) for n in table.names]].tolist()])
    return {tuple(r) for r in grid[ok].tolist()}


def is_valid(spec, found, params) -> bool:
    return all(tuple(level(spec, n, params[n]) for n in table.names) in set(table.levels) for table in found)


def unit_of(spec, name, x) -> float:
    """``x`` in unit coordinates as section 1 states them: clipped to the range, which is mapped to [0, 1],
    logarithmically when it is positive and spans a decade."""
    v = variable(spec, name)
    lo, hi = float(space.parse_scalar(v.lower)[0]), float(space.parse_scalar(v.upper)[0])
    x = min(max(float(x), lo), hi)
    if lo > 0 and hi / lo >= 10:
        return float((np.log(x) - np.log(lo)) / (np.log(hi) - np.log(lo)))
    return (x - lo) / (hi - lo)


def nearest_of(spec, table, values) -> tuple[int, ...]:
    """The combination of ``table`` nearest to ``values`` (numbers by variable name): the smallest Euclidean distance in
    unit coordinates, ties to the first in the table's sorted order."""
    def distance(combination):
        return sum((unit_of(spec, n, values[n]) - unit_of(spec, n, value(spec, n, k))) ** 2
                   for n, k in zip(table.names, combination, strict=True))

    return min(sorted(table.levels), key=lambda combination: (distance(combination), combination))


def projected(spec, found, row) -> tuple[int, ...]:
    """The valid point nearest to a grid point: each table's variables at the combination nearest to their levels."""
    out = dict(zip([v.name for v in spec.variables], row, strict=True))
    for table in found:
        if tuple(out[n] for n in table.names) not in set(table.levels):
            out.update(zip(table.names, nearest_of(spec, table, {n: value(spec, n, out[n]) for n in table.names}),
                           strict=True))
    return tuple(out[v.name] for v in spec.variables)


# -- 1. the space ----------------------------------------------------------------------------------------------------------


def test_snap_gives_the_nearest_combination_to_the_raw_values_and_the_free_variables_as_without_tables(tabled, monkeypatch):
    spec, found = tabled
    lows, highs = (np.array(b) for b in space.bounds(spec))
    raw = (lows - 0.2 * (highs - lows) + np.random.default_rng(3).random((300, 6)) * 1.4 * (highs - lows)).tolist()
    snapped = [space.snap(spec, r) for r in raw]                                   # some raw values outside the range
    with monkeypatch.context() as plain:
        plain.setattr(link, "tables", lambda spec: [])
        without = [space.snap(spec, r) for r in raw]
    names = [v.name for v in spec.variables]
    linked = {n for table in found for n in table.names}
    for r, params, grid in zip(raw, snapped, without, strict=True):
        assert is_valid(spec, found, params)
        assert {n: params[n] for n in names if n not in linked} == {n: grid[n] for n in names if n not in linked}
        for table in found:                                                          # measured from the raw values
            assert tuple(level(spec, n, params[n]) for n in table.names) == nearest_of(spec, table, dict(zip(names, r)))
    for row in sorted(brute_valid(spec, found))[::41]:                               # a valid point stays as it is
        params = text_of(spec, row)
        assert space.snap(spec, space.to_raw(spec, params)) == params


def test_ties_go_to_the_combination_first_in_the_table_s_sorted_order(monkeypatch):
    """A and B are linear with levels 1/8 apart: the distances are exact."""
    spec = spec_of([integer("A", 0, 8), integer("B", 0, 8), integer("C", 1, 3)], ["f"],
                   objective={"direction": "minimize", "expression": "f"})
    table = Table(("A", "B"), ((4, 7), (4, 5), (2, 5), (2, 3), (4, 5)), "t")        # given unsorted, with a repeat
    assert table.levels == ((2, 3), (2, 5), (4, 5), (4, 7))
    linking(monkeypatch, [table])
    assert space.snap(spec, [3, 5, 2]) == {"A": "2", "B": "5", "C": "2"}             # (2, 5) and (4, 5): 1/8 each
    assert space.snap(spec, [3, 6, 2]) == {"A": "2", "B": "5", "C": "2"}             # three at sqrt(2)/8
    assert space.snap(spec, [4, 6, 3]) == {"A": "4", "B": "5", "C": "3"}             # (4, 5) before (4, 7)
    coords = Coords(spec)
    assert coords.project(np.array([[3, 5, 1], [3, 6, 1], [4, 6, 2]])).tolist() == [[2, 5, 1], [2, 5, 1], [4, 5, 2]]
    assert space.project(spec, {"A": "3", "B": "6", "C": "1"}) == {"A": "2", "B": "5", "C": "1"}


def test_check_accepts_valid_points_and_names_the_table_the_values_and_the_nearest_combination(tabled):
    spec, found = tabled
    valid = brute_valid(spec, found)
    for row in sorted(valid)[::53]:
        space.check(spec, text_of(spec, row))
    counts = [space.grid_count(v) for v in spec.variables]
    refused = 0
    for row in np.random.default_rng(4).integers(0, counts, size=(120, len(counts))).tolist():
        params = text_of(spec, row)
        if tuple(row) in valid:
            space.check(spec, params)
            continue
        table = next(t for t in found if tuple(level(spec, n, params[n]) for n in t.names) not in set(t.levels))
        near = nearest_of(spec, table, {n: value(spec, n, level(spec, n, params[n])) for n in table.names})
        texts = text_of(spec, [dict(zip(table.names, near, strict=True)).get(v.name, 0) for v in spec.variables])
        message = (f"{table.label}: {' '.join(f'{n}={params[n]}' for n in table.names)} is not one of its combinations; "
                   f"the nearest one is {' '.join(f'{n}={texts[n]}' for n in table.names)}")
        with pytest.raises(ValueError, match=re.escape(message)):
            space.check(spec, params)
        refused += 1
    assert refused > 100
    assert space.points_from_params(spec, [text_of(spec, min(valid))])[0].params == text_of(spec, min(valid))


def test_grid_size_is_the_number_of_valid_points(tabled):
    spec, found = tabled
    assert space.grid_size(spec) == len(brute_valid(spec, found)) == (80 * 40 if len(found) == 1 else 16 * 15 * 12)


@pytest.mark.parametrize(("found", "message"), [
    ([Table(("x.Lp", "Q"), ((0, 0),), "device q")], "device q: Q is not a variable of the spec (its variables: F, x.Lp"),
    ([Table(("x.Lp",), ((0,),), "device a"), Table(("x.Ls", "x.Lp"), ((0, 0),), "device b")],
     "device b names x.Lp and device a names it too: a variable is in one table only"),
    ([Table(("x.k", "x.k"), ((0, 0),), "device c")], "device c names x.k twice"),
    ([Table(("x.Lp", "x.k"), ((0, 9),), "device d")], "device d: level index 9 of x.k is outside its levels (0 to 8)"),
    ([Table(("x.Lp", "x.k"), ((-1, 0),), "device d")], "device d: level index -1 of x.Lp is outside its levels (0 to 19)"),
    ([Table(("x.Lp",), (), "device e")], "device e holds no combination"),
    ([Table((), (), "device f")], "device f names no variable"),
    ([Table(("x.k",), ((9,),))], "the table of x.k: level index 9"),
])
def test_a_table_is_checked_where_it_is_first_used_and_the_message_names_it(monkeypatch, found, message):
    linking(monkeypatch, found)
    spec = six_spec()
    params = text_of(spec, [0] * 6)
    for use in (space.grid_size, space.tables, Coords, lambda s: space.snap(s, space.to_raw(s, params)),
                lambda s: space.check(s, params), lambda s: points.grid(s)):
        with pytest.raises(ValueError, match=re.escape(message)):
            use(spec)
    with pytest.raises(ValueError, match=re.escape("device g: a combination gives one level index per variable (2)")):
        Table(("x.Lp", "x.Ls"), ((0, 1), (2,)), "device g")


# -- 2. metric_gp's coordinates --------------------------------------------------------------------------------------------


def test_coords_project_as_snap_does_say_which_rows_are_valid_and_the_design_is_valid(tabled):
    spec, found = tabled
    coords = Coords(spec)
    valid = brute_valid(spec, found)
    assert coords.size == space.grid_size(spec) == len(valid)
    rng = np.random.default_rng(5)
    rows = np.vstack([rng.integers(0, coords.counts, size=(400, len(coords.counts))),
                      np.array(sorted(valid))[rng.choice(len(valid), 50, replace=False)]])
    assert coords.valid(rows).tolist() == [tuple(r) in valid for r in rows.tolist()]
    moved = coords.project(rows)
    assert [tuple(r) for r in moved.tolist()] == [projected(spec, found, r) for r in rows.tolist()]
    assert coords.valid(moved).all() and np.array_equal(moved[coords.valid(rows)], rows[coords.valid(rows)])
    snapped = coords.indices([space.snap(spec, r) for r in coords.raw(rows).tolist()])
    assert np.array_equal(moved, snapped)                                            # the one distance, both ways
    assert np.array_equal(snapped, coords.indices([space.project(spec, text_of(spec, r)) for r in rows.tolist()]))
    design = coords.design_raw(unit_design("sobol", 64, len(coords.counts), 7))
    assert all(row_of(spec, space.snap(spec, r)) in valid and space.to_raw(spec, space.snap(spec, r)) == r for r in design)
    proposal = MetricGpSuggester(initial_trials=16).propose(spec, Observations(), 16, seed=3)
    chosen = [space.snap(spec, r) for r in proposal.raw]
    assert proposal.tags == ["init"] * 16 and len({space.point_key(p) for p in chosen}) == 16
    assert all(row_of(spec, p) in valid for p in chosen)


# -- 3. candidates ---------------------------------------------------------------------------------------------------------


def test_the_whole_grid_is_the_valid_points_less_the_excluded_ones(tabled):
    spec, found = tabled
    coords = Coords(spec)
    valid = sorted(brute_valid(spec, found))
    excluded_rows = valid[::7]
    got = candidates.whole_grid(coords, set(keys(np.array(excluded_rows))))
    assert len(got) == len(valid) - len(excluded_rows) == len({tuple(r) for r in got.tolist()})
    assert {tuple(r) for r in got.tolist()} == set(valid) - set(excluded_rows)


def test_the_whole_grid_never_builds_the_full_product(monkeypatch):
    """4 x 1000 x 1000 x 500 = 2e9 grid points, 160 of them valid: they come back at once."""
    spec = spec_of([integer("F", 1, 4), stepped("x.Lp", "0.01n", "10n", "0.01n"), stepped("x.Ls", "0.01n", "10n", "0.01n"),
                    stepped("x.k", "0.5", "0.999", "0.001")], ["f"], objective={"direction": "minimize", "expression": "f"})
    combinations = np.random.default_rng(6).integers(0, (1000, 1000, 500), (40, 3))
    table = Table(("x.Lp", "x.Ls", "x.k"), tuple(map(tuple, combinations)), "device x (library rows)")
    linking(monkeypatch, [table])
    assert len(table.levels) == 40 and math.prod(space.grid_count(v) for v in spec.variables) == 2 * 10**9
    coords = Coords(spec)
    start = time.perf_counter()
    got = candidates.whole_grid(coords, set())
    every = points.grid(spec)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, elapsed
    assert len(got) == len(every) == coords.size == space.grid_size(spec) == 160
    assert coords.valid(got).all() and len(set(keys(got))) == 160
    assert {tuple(r) for r in got.tolist()} == {row_of(spec, p.params) for p in every}


def test_local_and_wide_candidates_are_valid_points(tabled):
    spec, found = tabled
    coords = Coords(spec)
    valid = brute_valid(spec, found)
    rng = np.random.default_rng(8)
    centre = np.array(sorted(valid)[len(valid) // 2])
    excluded = set(keys(centre[None, :]))                          # the centre is an evaluated point
    for length in (0.8, 0.2, 1e-6):                                # perturbations; the centre's levels and neighbours
        local = candidates.local(coords, centre, length, np.ones(coords.d), set(excluded), rng)
        assert len(local) and all(tuple(r) in valid for r in local.tolist())
        assert len(set(keys(local))) == len(local) and not set(keys(local)) & excluded
    wide = candidates.wide(coords, set(excluded), rng)
    assert len(wide) and all(tuple(r) in valid for r in wide.tolist())
    assert len(set(keys(wide))) == len(wide) and not set(keys(wide)) & excluded


def test_a_region_centre_the_table_no_longer_holds_is_projected_before_candidates_are_drawn(monkeypatch):
    """30 points evaluated under one table, then the table changes: the region's centre, an evaluated point, is not a
    valid point any more; the candidates are drawn around the valid point nearest to it."""
    linking(monkeypatch, [DEVICE])
    spec = six_spec()
    history = run(spec, 30, 10, metrics, seed=5)
    changed = Table(DEVICE.names, drawn(40, (20, 11, 9), 7), "device x (library rows)")
    linking(monkeypatch, [changed])
    rows = list(history)
    state = MetricGpSuggester().region_state(spec, history)
    position = region.centre(Composer(spec), rows, state, metric_scales(spec, rows), true_arrays(spec, rows))
    assert not state.ended and not is_valid(spec, [changed], rows[position].params)
    centres = []
    local = candidates.local
    monkeypatch.setattr(candidates, "local", lambda coords, centre, *args: centres.append(tuple(centre.tolist()))
                        or local(coords, centre, *args))
    batch = suggest(spec, history, 10, strategy="metric_gp", seed=5)
    assert centres == [projected(spec, [changed], row_of(spec, rows[position].params))]
    assert len(batch) == 10 and all(is_valid(spec, [changed], p.params) for p in batch)
    assert not {p.key for p in batch} & history.keys()


# -- 4 / 5. every strategy -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_every_strategy_hands_out_full_batches_of_new_valid_points(tabled, strategy):
    """Five batches of 8 from an empty history: the design, then the strategy's model."""
    spec, found = tabled
    history = Observations()
    for _ in range(5):
        batch = suggest(spec, history, 8, strategy=strategy, seed=2)
        assert len(batch) == 8 and len({p.key for p in batch}) == 8 and not {p.key for p in batch} & history.keys()
        assert all(is_valid(spec, found, p.params) for p in batch)
        history.extend(observe(spec, batch, metrics, len(history)))
    assert any(":init" not in o.origin for o in history)                           # the model proposed


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_with_every_valid_point_evaluated_but_a_few_the_last_batch_is_those_few(monkeypatch, strategy):
    spec = small_spec()
    table = Table(("x.Lp", "x.Ls", "x.k"), drawn(30, (20, 11, 9), 3), "device x (library rows)")
    linking(monkeypatch, [table])
    every = [text_of(spec, row) for row in sorted(brute_valid(spec, [table]))]
    assert len(every) == space.grid_size(spec) == 120
    order = np.random.default_rng(9).permutation(len(every))
    history = observe(spec, [Point(every[i], "user") for i in order[3:]], metrics)
    batch = suggest(spec, history, 10, strategy=strategy, seed=4)
    assert sorted(p.key for p in batch) == sorted(space.point_key(every[i]) for i in order[:3])
    assert suggest(spec, history + observe(spec, batch, metrics, len(history)), 10, strategy=strategy, seed=4) == []


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_the_same_spec_history_and_seed_give_the_same_batch(tabled, strategy):
    spec, found = tabled
    valid = sorted(brute_valid(spec, found))
    rows = [valid[i] for i in np.random.default_rng(10).choice(len(valid), 20, replace=False)]
    history = observe(spec, [Point(text_of(spec, r), "user") for r in rows], metrics)
    first = suggest(spec, history, 6, strategy=strategy, seed=5)
    again = suggest(spec, Observations(list(history)), 6, strategy=strategy, seed=5)
    assert [(p.params, p.origin) for p in first] == [(p.params, p.origin) for p in again]
    assert len(first) == 6 and all(is_valid(spec, found, p.params) for p in first)


# What every strategy and point block gave on six_spec without tables at 088a182, before this task: sha256 of the
# [params, origin] rows (three batches of 8 at seed 1; sobol, grid per_dim=2 and one_at_a_time around CENTRE; the grid).
PINNED = {"metric_gp": "e8369ad4e6789b9a", "random": "d0e7f7ed690dfc4f", "sobol": "0824b2eb23aad67a",
          "openbox_gp_eic": "fccd38e2da68b10b", "turbo": "1e9d624f4ec3ad12", "points": "784811b73b9962f2",
          "grid": "28b471c7daf55192"}
CENTRE = {"F": "2", "x.Lp": "0.5n", "W": "1u", "x.Ls": "0.8n", "R": "0.5", "x.k": "0.7"}


def digest(rows) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16]


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_without_tables_every_strategy_proposes_what_it_did_before(strategy):
    assert link.tables(six_spec()) == []                                            # as shipped
    spec = six_spec()
    history = Observations()
    for _ in range(3):
        history.extend(observe(spec, suggest(spec, history, 8, strategy=strategy, seed=1), metrics, len(history)))
    assert digest([[o.params, o.origin] for o in history]) == PINNED[strategy]


def test_without_tables_the_point_blocks_give_what_they_did_before():
    spec = six_spec()
    blocks = points.sobol(spec, 32, seed=2) + points.grid(spec, per_dim=2) + points.one_at_a_time(spec, CENTRE)
    assert digest([[p.params, p.origin] for p in blocks]) == PINNED["points"]
    assert digest([[p.params, p.origin] for p in points.grid(spec)]) == PINNED["grid"]
    coords = Coords(spec)
    grid = np.indices(coords.counts).reshape(len(coords.counts), -1).T
    assert coords.size == len(grid) and np.array_equal(coords.valid_points(), grid) and coords.valid(grid).all()
    assert np.array_equal(coords.project(grid[::97]), grid[::97])


# -- 6. advice -------------------------------------------------------------------------------------------------------------


@pytest.fixture
def device_history(monkeypatch):
    """30 points of the six-variable spec with the device's table (3200 valid points: a search region)."""
    linking(monkeypatch, [DEVICE])
    spec = six_spec()
    return spec, run(spec, 30, 10, metrics, seed=5)


def test_a_range_on_a_linked_variable_narrows_the_advised_slots_and_the_free_ones_keep_the_whole_space(device_history,
                                                                                                        monkeypatch):
    spec, history = device_history
    offered = []
    choose = select.select_batch
    monkeypatch.setattr(select, "select_batch", lambda models, value_model, composer, scales, x, preferred, rng, **kw:
                        offered.append((x, preferred))
                        or choose(models, value_model, composer, scales, x, preferred, rng, **kw))
    suggest(spec, history, 10, strategy="metric_gp", seed=5)
    row = advice_rules.adoption(spec, {**AUTHOR, "ranges": {"x.Lp": ["0.1n", "0.5n"]}}, [], 30)[0]
    batch = suggest(spec, history, 10, strategy="metric_gp", seed=5, advice=[row])
    (plain, _), (x, preferred) = offered
    assert {tuple(r) for r in x[preferred[0]]} == {tuple(r) for r in plain}       # the free slots: the batch without advice
    assert all(preferred[b] is preferred[0] for b in (0, 1)) and all(preferred[b] is preferred[2] for b in range(2, 10))
    lp = Coords(spec).unit_levels[1][4]                                              # 0.5n, level 4, in unit coordinates
    assert len(x[preferred[2]]) and (x[preferred[2]][:, 1] <= lp + 1e-12).all()
    inside = [p for p in batch if space.split_origin(p.origin)[1] == "a1"]
    assert len(inside) == 8 and all(float(space.parse_scalar(p.params["x.Lp"])[0]) <= 0.5 for p in inside)
    assert all(is_valid(spec, [DEVICE], p.params) for p in batch)
    assert len({p.key for p in batch}) == 10 and not {p.key for p in batch} & history.keys()


def test_a_candidate_brought_inside_an_advice_is_projected_and_left_out_when_the_projection_leaves_it(monkeypatch):
    """A range on x.Lp around one of the table's combinations and x.k fixed at its level: every row is moved into the
    bands, projected, and kept only when the valid point it lands on lies inside them (``contains``)."""
    linking(monkeypatch, [DEVICE])
    spec = six_spec()
    coords = Coords(spec)
    lp, _, k = DEVICE.levels[len(DEVICE.levels) // 2]
    low, high = max(lp - 3, 0), min(lp + 3, 19)
    texts = [text_of(spec, [0, level, 0, 0, 0, 0])["x.Lp"] for level in (low, high)]
    row = advice_rules.adoption(spec, {**AUTHOR, "ranges": {"x.Lp": texts},
                                       "fixed": {"x.k": text_of(spec, [0] * 5 + [k])["x.k"]}}, [], 0)[0]
    advised = candidates.Advised(coords, row)
    rows = np.random.default_rng(12).integers(0, coords.counts, size=(600, 6))
    centre = coords.project(rows[:1])[0]
    moved = advised.inside(rows, centre)
    expected = []
    for r in rows.tolist():
        landed = projected(spec, [DEVICE], [r[0], min(max(r[1], low), high), r[2], r[3], r[4], k])
        if low <= landed[1] <= high and landed[5] == k:
            expected.append(landed)
    assert expected and [tuple(r) for r in moved.tolist()] == expected
    assert advised.contains(moved, centre).all() and coords.valid(moved).all()
    assert not advised.contains(np.array([[0, low, 0, 0, 0, (k + 1) % 9]]), centre).any()


def test_a_start_row_that_is_not_a_valid_point_is_refused_with_the_nearest_combination(tmp_path, monkeypatch):
    linking(monkeypatch, [DEVICE])
    spec = six_spec()
    store = RunStore(tmp_path)
    good = text_of(spec, sorted(brute_valid(spec, [DEVICE]))[500])
    others = ({**good, "x.k": text_of(spec, [0] * 5 + [k])["x.k"]} for k in range(9))
    bad = next(params for params in others if not is_valid(spec, [DEVICE], params))
    near = text_of(spec, projected(spec, [DEVICE], row_of(spec, bad)))
    message = (f"start row 1: device x (library rows): x.Lp={bad['x.Lp']} x.Ls={bad['x.Ls']} x.k={bad['x.k']} is not one "
               f"of its combinations; the nearest one is x.Lp={near['x.Lp']} x.Ls={near['x.Ls']} x.k={near['x.k']}")
    with pytest.raises(ValueError, match=re.escape(message)):
        advise(spec, store, {**AUTHOR, "start": [bad]})
    assert not advice_rules.path(store.root).exists()
    row = advise(spec, store, {**AUTHOR, "start": [good], "ranges": {"x.Ls": ["0.2n", "0.6n"]}, "fixed": {"x.k": "0.7"}})
    assert row["fixed"] == {"x.k": "0.7"} and row["ranges"] == {"x.Ls": ["0.2n", "0.6n"]}     # taken as they are
    batch = suggest(spec, Observations(), 4, strategy="metric_gp", seed=0, advice=advice_rules.read(store.root))
    assert batch[0].params == good and batch[0].origin == "advice:a1"
    assert all(is_valid(spec, [DEVICE], p.params) for p in batch)


# -- 7. the point blocks ---------------------------------------------------------------------------------------------------


def test_the_point_blocks_hand_out_valid_points(tabled):
    spec, found = tabled
    valid = brute_valid(spec, found)
    drawn_points = points.sobol(spec, 64, seed=3)
    assert drawn_points and all(row_of(spec, p.params) in valid for p in drawn_points)
    assert len({p.key for p in drawn_points}) == len(drawn_points)
    every = points.grid(spec)
    assert len(every) == space.grid_size(spec) and {row_of(spec, p.params) for p in every} == valid
    few = points.grid(spec, per_dim=3)
    picks = [sorted({round(i * (c - 1) / 2) for i in range(3)}) for c in (space.grid_count(v) for v in spec.variables)]
    expected = {projected(spec, found, combo) for combo in itertools.product(*picks)}
    assert [row_of(spec, p.params) for p in few] == list(dict.fromkeys(row_of(spec, p.params) for p in few))
    assert {row_of(spec, p.params) for p in few} == expected
    centre = sorted(valid)[len(valid) // 3]
    oat = points.one_at_a_time(spec, text_of(spec, centre))
    moves = set()
    for i, count in enumerate(space.grid_count(v) for v in spec.variables):
        for step in (-1, 1):
            if 0 <= centre[i] + step < count:
                moves.add(projected(spec, found, tuple(k + step * (j == i) for j, k in enumerate(centre))))
    assert oat[0].params == text_of(spec, centre) and len({p.key for p in oat}) == len(oat)
    assert {row_of(spec, p.params) for p in oat} == moves | {centre}
    grid_rows = np.random.default_rng(11).integers(0, 4, size=(50, 6)).tolist()
    invalid = next(tuple(r) for r in grid_rows if tuple(r) not in valid)
    for block in (points.one_at_a_time, lambda s, params: points.fixed(s, [params])):
        with pytest.raises(ValueError, match="is not one of its combinations; the nearest one is"):
            block(spec, text_of(spec, invalid))
    assert points.fixed(spec, [text_of(spec, centre)])[0].params == text_of(spec, centre)
