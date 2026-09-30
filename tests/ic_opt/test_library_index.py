"""T18.1 sections 3 and 4: the electrical index -- a stratum's rows by their electrical values at one working frequency --
its grid (``level``: Coords.snap's rule; ``table``: cells ranked by ``prefer``; ``pick``: the nearest occupied cell), and
the blocks lib.index and lib.pick."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml
from typer.testing import CliRunner

from ic_opt import blocks
from ic_opt.cli import app
from ic_opt.library import index
from ic_opt.library import query as q
from ic_opt.spec import Spec
from ic_opt.suggesters.metric_gp.coords import Coords
from tests.ic_opt.fakes import minimal_spec
from tests.ic_opt.library_fixtures import LOCAL, build_library, build_xfm_library, use_site
from tests.ic_opt.test_library_anyfreq import write_big

IND_COLUMNS = ["Lp", "Lp_lf", "Qp_peak", "SRF_p", "area"]


@pytest.fixture(scope="module")
def library(tmp_path_factory) -> Path:
    return build_library(tmp_path_factory.mktemp("index"))


def coupled(root: Path) -> Path:
    """build_xfm_library with every curve of the pair declared at 10 GHz: Lp, Qp, Ls, Qs and k."""
    build_xfm_library(root)
    doc = yaml.safe_load((root / "library.yaml").read_text(encoding="utf-8"))
    doc["strata"]["xfm_demo"]["quantities"].update({c: {"anchors_ghz": [10]} for c in ("Lp", "Qp", "Ls", "Qs")})
    (root / "library.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return root


def test_the_index_holds_every_curve_and_scalar_for_the_rows_that_have_every_curve(library):
    lib = q.Library(library, limits=LOCAL)
    idx = index.build(lib, "ind_demo", 20e9)
    ds = lib.dataset("ind_demo")
    kept = [r for r in ds.rows if r.values["Lp@20"] is not None]
    assert idx.columns == IND_COLUMNS and idx.frequency_hz == 20e9 and idx.srf_margin == 1.25 and idx.stratum == "ind_demo"
    assert [(r.part, r.obs_id) for r in idx.rows] == [(r.part, r.obs_id) for r in kept] and 0 < len(kept) < len(ds.rows)
    assert idx.dropped == {index.DROPPED["resonance"]: len(ds.rows) - len(kept)}   # every part swept to 60 GHz: only resonances drop rows
    for row, r in zip(idx.rows, kept):
        assert row.values == {"Lp": r.values["Lp@20"], "Lp_lf": r.values["Lp_lf"], "Qp_peak": r.values["Qp_peak"], "SRF_p": r.values["SRF_p"],
                              "area": None}
        assert (row.stratum, row.params, row.snp, row.ports, row.footprint) == ("ind_demo", r.coords, r.snp, ["P1", "N1"], None)
    assert idx.key == index.build(lib, "ind_demo", 20e9).key != index.build(lib, "ind_demo", 21e9).key
    at_anchor = index.build(lib, "ind_demo", 10e9)                       # a declared anchor reads the declared column
    assert [r.values["Lp"] for r in at_anchor.rows] == [r.values["Lp@10"] for r in ds.rows if r.values["Lp@10"] is not None]


def test_a_coupled_pair_adds_the_secondary_the_coupling_and_qmin(tmp_path):
    lib = q.Library(coupled(tmp_path / "xfm"), limits=LOCAL)
    idx = index.build(lib, "xfm_demo", 25e9)
    assert idx.columns == ["Lp", "Qp", "Ls", "Qs", "k", "Lp_lf", "Qp_peak", "SRF_p", "Ls_lf", "Qs_peak", "SRF_s", "k_lf", "SRF", "Qmin", "area"]
    assert idx.rows and all(r.values["Qmin"] == min(r.values["Qp"], r.values["Qs"]) for r in idx.rows)
    assert all(r.ports == ["P1", "N1", "P2", "N2"] and r.values["k"] > 0 for r in idx.rows)
    assert all(r.values["SRF"] is None or r.values["SRF"] > 1.25 * 25e9 for r in idx.rows)


def test_a_margin_above_the_manifests_also_drops_the_rows_with_a_known_resonance_below_it(library):
    lib = q.Library(library, limits=LOCAL)
    base = index.build(lib, "ind_demo", 20e9)
    wide = index.build(lib, "ind_demo", 20e9, srf_margin=2.0)
    srf = {(r.part, r.obs_id): s for r, s in zip(lib.dataset("ind_demo").rows, lib.anchors("ind_demo", 20.0)["srf_hz"])}
    expected = [r for r in base.rows if srf[(r.part, r.obs_id)] is None or srf[(r.part, r.obs_id)] > 2.0 * 20e9]
    assert wide.rows == expected and wide.srf_margin == 2.0 and wide.key != base.key
    assert any(srf[(r.part, r.obs_id)] is None for r in wide.rows)      # no resonance inside its sweep: the row stays
    assert wide.dropped[index.DROPPED["margin"]] == len(base.rows) - len(expected) > 0
    low = index.build(lib, "ind_demo", 20e9, srf_margin=1.1)            # below the manifest's 1.25: nothing changes
    assert low.rows == base.rows and low.srf_margin == 1.25 and low.key == base.key


def test_a_frequency_no_row_can_give_is_refused(library, tmp_path):
    lib = q.Library(library, limits=LOCAL)
    with pytest.raises(ValueError, match=r"no part is swept at 90 GHz.*nt1 0-60 GHz, nt2 0-60 GHz"):
        index.build(lib, "ind_demo", 90e9)
    with pytest.raises(ValueError, match="no row has Lp@50"):
        index.build(q.Library(write_big(tmp_path / "big"), limits=LOCAL), "ind", 50e9)
    scalars = build_library(tmp_path / "scalars")
    doc = yaml.safe_load((scalars / "library.yaml").read_text(encoding="utf-8"))
    doc["strata"]["ind_demo"]["quantities"] = {"Lp_lf": {}, "SRF_p": {}}
    (scalars / "library.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="declares no curve"):
        index.build(q.Library(scalars, limits=LOCAL), "ind_demo", 20e9)
    for bad in (0, -1e9, float("nan")):
        with pytest.raises(ValueError, match="positive frequency"):
            index.build(lib, "ind_demo", bad)


# -- the grid ---------------------------------------------------------------------------------------------------------

LINEAR = {"name": "x", "kind": "continuous_step", "lower": "0", "upper": "10", "step": "0.5"}      # lower 0: searched linearly
LOG = {"name": "y", "kind": "continuous_step", "lower": "1", "upper": "100", "step": "1"}          # a hundredfold: logarithmically


def test_level_is_the_rule_coords_snap_applies_on_a_linear_and_a_logarithmic_variable():
    coords = Coords(Spec.model_validate(minimal_spec(variables=[LINEAR, LOG])))
    assert coords.log.tolist() == [False, True]
    rng = np.random.default_rng(7)
    xs = rng.uniform(-0.25, 10.25, 500)                                  # up to half a step beyond either end
    ys = np.exp(rng.uniform(np.log(0.7072), np.log(100.5), 500))        # up to half the end interval beyond, in log
    snapped = coords.snap(coords.unit_of_raw(np.column_stack([xs, ys])))
    assert [index.level(float(x), 0, 10, 0.5) for x in xs] == snapped[:, 0].tolist()
    assert [index.level(float(y), 1, 100, 1) for y in ys] == snapped[:, 1].tolist()
    assert index.level(0.25, 0, 10, 0.5) == 0                            # a tie goes to the lower level ...
    for x in (0.25, 7.75, 3.25):
        assert index.level(x, 0, 10, 0.5) == coords.snap(coords.unit_of_raw([[x, 1.0]]))[0, 0]      # ... as Coords.snap's does
    assert (index.level(-0.25, 0, 10, 0.5), index.level(-0.2501, 0, 10, 0.5)) == (0, None)         # half an interval beyond: kept
    assert (index.level(10.25, 0, 10, 0.5), index.level(10.2501, 0, 10, 0.5)) == (20, None)
    assert (index.level(0.72, 1, 100, 1), index.level(0.70, 1, 100, 1)) == (0, None)               # 1 / sqrt(2): half of log 2 below
    assert (index.level(100.5, 1, 100, 1), index.level(100.6, 1, 100, 1)) == (99, None)            # 100 sqrt(100/99) above
    assert index.level(0.0, 1, 100, 1) is None and index.level(-3.0, 1, 100, 1) is None
    assert (index.level(5.2, 5, 5, 0.5), index.level(5.3, 5, 5, 0.5)) == (0, None)                 # one level: half a step
    assert index.level(2.9e-10, 1e-10, 2e-9, 1e-10) == 2
    assert index.level(1.45e-9, 1e-10, 2e-9, 1e-10) == 14                # halfway linearly, nearer 1.5e-9 in log (13 would be 1.4e-9)
    for bad in ((1, 0, 0.5), (0, 10, 0), (0, 10, -1), (0, float("inf"), 1), (0, 1, 1e-9)):
        with pytest.raises(ValueError, match="grid value"):
            index.level(0.5, *bad)


def hand_row(part: str, obs: str, **values) -> index.IndexRow:
    return index.IndexRow("s", part, obs, {"d": 1.0}, {"Lp": None, "Qp": None, "k": None, "area": None, **values}, None,
                          f"{part}/{obs}.s2p", ["P1", "N1"])


def hand_index(rows: list[index.IndexRow], columns=("Lp", "Qp", "k", "area")) -> index.Index:
    return index.Index("s", 20e9, 1.25, list(columns), rows, "key")


def test_table_ranks_each_cell_by_prefer_and_breaks_ties_by_part_and_obs_id():
    rows = [hand_row("b", "o2", Lp=1.00e-9, Qp=10.0), hand_row("a", "o9", Lp=1.02e-9, Qp=12.0), hand_row("a", "o1", Lp=0.98e-9, Qp=10.0),
            hand_row("c", "o1", Lp=1.01e-9), hand_row("a", "o3", Lp=2.0e-9, Qp=5.0), hand_row("z", "o0", Qp=20.0),
            hand_row("z", "o1", Lp=9e-9, Qp=1.0)]
    grid = {"Lp": [0.5e-9, 3e-9, 0.1e-9]}                                # six-fold: searched linearly
    t = index.table(hand_index(rows), grid)
    assert (t.coordinates, t.prefer, t.levels(), t.size()) == (["Lp"], "max:Qp", [(5,), (15,)], 26)
    assert [(r.part, r.obs_id) for r in t.cells[(5,)]] == [("a", "o9"), ("a", "o1"), ("b", "o2"), ("c", "o1")]   # no Qp: last
    assert t.left_out == {"no value for a coordinate": 1, "beyond the grid": 1} and t.values((15,)) == {"Lp": 2.0e-9}
    lowest = index.table(hand_index(rows), grid, "min:Qp")
    assert [(r.part, r.obs_id) for r in lowest.cells[(5,)]] == [("a", "o1"), ("b", "o2"), ("a", "o9"), ("c", "o1")]
    with_qmin = index.table(hand_index(rows, ("Lp", "Qp", "Qmin", "area")), grid)
    assert with_qmin.prefer == "max:Qmin"
    for bad_grid, message in (({"Ls": [0, 1, 0.1]}, "not a column of the index"), ({"Lp": [0, 1]}, r"expected \[lower, upper, step\]"),
                              ({}, "expected"), ({"Lp": [1e-9, 0.5e-9, 1e-10]}, "lower <= upper")):
        with pytest.raises(ValueError, match=message):
            index.table(hand_index(rows), bad_grid)
    with pytest.raises(ValueError, match="prefer 'best:Qp'"):
        index.table(hand_index(rows), grid, "best:Qp")


def test_pick_gives_the_targets_own_cell_or_the_nearest_occupied_one_in_the_search_scale():
    rows = [hand_row("a", "o1", Lp=2e-10, k=0.25, Qp=9.0), hand_row("a", "o2", Lp=1e-9, k=0.25, Qp=8.0),
            hand_row("a", "o3", Lp=1e-9, k=0.75, Qp=7.0), hand_row("b", "o1", Lp=1.02e-9, k=0.26, Qp=11.0)]
    t = index.table(hand_index(rows), {"Lp": [1e-10, 2e-9, 1e-10], "k": [0.0, 1.0, 0.25]})     # Lp twentyfold: logarithmic
    assert [a.log for a in t.axes] == [True, False]
    own = index.pick(t, {"Lp": 1.04e-9, "k": 0.3})
    assert (own.cell, own.exact, own.target, own.distance) == ((9, 1), True, {"Lp": 9, "k": 1}, {"Lp": 0, "k": 0})
    assert [(r.part, r.obs_id) for r in own.rows] == [("b", "o1"), ("a", "o2")] and own.values == {"Lp": 1e-9, "k": 0.25}
    between = index.pick(t, {"Lp": 5e-10, "k": 0.25})                  # linearly nearer 2e-10, logarithmically nearer 1e-9
    assert (between.cell, between.exact, between.distance) == ((9, 1), False, {"Lp": 5, "k": 0})
    tie = index.pick(t, {"Lp": 1e-9, "k": 0.5})                        # halfway between k 0.25 and 0.75: the smaller cell
    assert (tie.cell, tie.exact, tie.target) == ((9, 1), False, {"Lp": 9, "k": 2})
    beyond = index.pick(t, {"Lp": 8e-9, "k": 0.75})
    assert beyond.cell == (9, 3) and beyond.target["Lp"] is None and not beyond.exact and beyond.distance["Lp"] < -19
    for targets, message in (({"Lp": 1e-9}, "one value for each coordinate"), ({"Lp": -1e-9, "k": 0.5}, "takes positive values"),
                             ({"Lp": 1e-9, "k": "x"}, "finite number")):
        with pytest.raises(ValueError, match=message):
            index.pick(t, targets)
    empty = index.table(hand_index(rows), {"Lp": [3e-9, 4e-9, 1e-10]})
    with pytest.raises(ValueError, match="nothing to pick"):
        index.pick(empty, {"Lp": 3e-9})


# -- the blocks -------------------------------------------------------------------------------------------------------

def test_lib_index_and_lib_pick_print_strict_json_and_are_listed(library, tmp_path, monkeypatch):
    use_site(monkeypatch, tmp_path / "absent.yaml")                   # measuring and indexing are reads: no site entry needed
    runner = CliRunner()
    summary = runner.invoke(app, ["call", "lib.index", str(library), "stratum=ind_demo", "frequency_ghz=20"])
    assert summary.exit_code == 0, summary.output
    body = json.loads(summary.stdout)
    assert body["columns"] == IND_COLUMNS and body["rows"]["kept"] + body["rows"]["dropped"] == 105 and "table" not in body
    assert body["sweeps"] == {"nt1": {"start_hz": 0.0, "stop_hz": 6e10}, "nt2": {"start_hz": 0.0, "stop_hz": 6e10}}
    assert body["ranges"]["Lp"]["unit"] == "H" and body["ranges"]["area"] == {"rows": 0, "min": None, "max": None, "unit": "um2"}
    assert body["footprints"] == {"rows": 0, "none": {"no GDS beside its sNp": body["rows"]["kept"]}}
    grid = {"Lp": [2e-10, 4e-9, 1e-10]}
    written = tmp_path / "tables" / "lp20.json"
    tabled = runner.invoke(app, ["call", "lib.index", str(library), "stratum=ind_demo", "frequency_ghz=20", f"grid={json.dumps(grid)}",
                                 "prefer=max:Qp_peak", f"out={written}"])
    assert tabled.exit_code == 0, tabled.output
    body, table = json.loads(tabled.stdout), json.loads(written.read_text(encoding="utf-8"))
    assert body["out"] == str(written) and body["table"]["prefer"] == "max:Qp_peak" and body["table"]["cells"]["of"] == 39
    assert body["table"]["cells"]["occupied"] == len(table["cells"]) and body["table"]["per_coordinate"]["Lp"]["log"] is True
    assert body["table"]["rows"]["in_cells"] == sum(c["rows"] for c in table["cells"])
    idx = index.build(q.Library(library), "ind_demo", 20e9)
    reference = index.table(idx, grid, "max:Qp_peak")
    for cell, entry in zip(reference.levels(), table["cells"]):
        best = reference.cells[cell][0]
        assert entry["levels"] == {"Lp": cell[0]} and entry["best"]["obs_id"] == best.obs_id and entry["best"]["snp"] == best.snp
        assert list(entry["best"]) == ["part", "obs_id", "values", "params", "footprint", "footprint_why", "snp", "ports"]
    picked = runner.invoke(app, ["call", "lib.pick", str(library), "stratum=ind_demo", "frequency_ghz=20", 'targets={"Lp": 1.23e-9}',
                                 f"grid={json.dumps(grid)}", "n=2"])
    assert picked.exit_code == 0, picked.output
    answer = json.loads(picked.stdout)
    assert answer["exact"] is True and answer["distance"] == {"Lp": 0} and answer["cell"]["levels"] == {"Lp": 10} and len(answer["rows"]) == 2
    assert answer["rows"][0]["snp"].endswith(".s2p") and answer["rows"][0]["ports"] == ["P1", "N1"]
    assert any("has no Qp column" in note for note in answer["notes"])  # the default max:Qp names a column this index lacks
    assert "Infinity" not in picked.stdout and "NaN" not in picked.stdout
    missing = runner.invoke(app, ["call", "lib.pick", str(library), "stratum=ind_demo", "frequency_ghz=20", 'targets={"Lp": 1.23e-9}'])
    assert missing.exit_code == 2 and "grid is required" in missing.output
    no_table = runner.invoke(app, ["call", "lib.index", str(library), "stratum=ind_demo", "frequency_ghz=20", f"out={tmp_path / 'x.json'}"])
    assert no_table.exit_code == 2 and "give grid= too" in no_table.output
    listing = runner.invoke(app, ["blocks"])
    assert {"lib.index", "lib.pick"} <= {line.split()[0] for line in listing.output.splitlines()}
    described = runner.invoke(app, ["describe", "lib.pick"])
    assert described.exit_code == 0 and described.output.startswith("lib.pick(library") and "exact" in described.output
    assert blocks.REGISTRY["lib.index"].fn is blocks.lib_index


def test_index_rows_and_picks_carry_their_rows_footprint(tmp_path):
    """Three rows built by the real pcell keep their GDS: in the index they carry its footprint (``area`` its area), and a
    pick that lands on one shows it; the other rows have none and say why."""
    from ic_opt.blocks import library as library_blocks
    from tests.ic_opt.test_library import run_part

    root = build_library(tmp_path / "lib")
    run_part(root, "gds", 60, [{"outer_diameter_um": od, "width_um": 4.5, "spacing_um": 2, "turns": 2} for od in (105, 135, 165)])
    doc = yaml.safe_load((root / "library.yaml").read_text(encoding="utf-8"))
    doc["strata"]["ind_demo"]["parts"].append({"store": "gds"})
    (root / "library.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    lib = q.Library(root, limits=LOCAL)
    idx = index.build(lib, "ind_demo", 5e9)
    known = lib.footprints("ind_demo")
    drawn = [r for r in idx.rows if r.part == "gds"]
    assert len(drawn) == 3
    for r in drawn:
        assert r.footprint == known[(r.part, r.obs_id)][0] and r.footprint is not None and r.values["area"] == r.footprint["area_um2"]
    assert all(r.footprint is None and r.values["area"] is None for r in idx.rows if r.part != "gds")
    target = drawn[1]
    answer = library_blocks.pick(lib, "ind_demo", 5, {"Lp": target.values["Lp"]}, {"Lp": [2e-10, 5e-9, 1e-12]})
    best = answer["rows"][0]
    assert answer["exact"] and (best["part"], best["obs_id"]) == (target.part, target.obs_id) and best["footprint"] == target.footprint
    assert "footprint_why" not in best and answer["cell"]["rows"] == 1
