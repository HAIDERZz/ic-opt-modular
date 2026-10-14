"""N-114: one recommendation request across the tables of a device kind -- lib.suggest and lib.region with family= (and
taps=) or strata= -- and N-50, lib.suggest's second pool on lib.region's grid. The synthetic family of
``library_fixtures.build_family_library``: ind_o8 and ind_o2 differ only in the port opening (and in the diameters their
rows cover), ind_ct has a centre tap and no Qp_peak column."""
from __future__ import annotations

import json

import numpy as np
import pytest
from typer.testing import CliRunner

from ic_opt.blocks import library as library_blocks
from ic_opt.cli import app
from ic_opt.library import family, query, region, suggest
from tests.ic_opt.library_fixtures import LOCAL, build_family_library, use_site

pytest.importorskip("klayout.db")

NEAR_O2 = {"Lp_lf": {"target": 1.5e-9, "tol": 0.05}}            # 1.425-1.575 nH: inside ind_o2's (and ind_ct's) rows only
BOTH = {"Lp_lf": {"min": 2.25e-9, "max": 2.45e-9}}              # ind_o2 reaches 2.34 nH at the most, ind_o8 2.33 nH at the least
STEPS = {"outer_diameter_um": 2, "width_um": 0.5, "spacing_um": 0.5}
FAMILY_KEYS = ["family", "taps", "strata", "facts", "leaders", "unreachable", "notes"]
O2_FIXED = {"opening_um": 2.0, "metal": "6", "taps": []}
O8_FIXED = {"opening_um": 8.0, "metal": "6", "taps": []}


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    return query.Library(build_family_library(tmp_path_factory.mktemp("family")), limits=LOCAL)


def ask(lib, **kw):
    return library_blocks.suggest(lib, **{"n": 2, "pool_size": 256, "verify_build": False, **kw})


def measured_range(lib, stratum: str, column: str) -> tuple[float, float]:
    ds = lib.dataset(stratum)
    values = ds.values(column, ds.usable(column))
    return float(values.min()), float(values.max())


def key(params: dict) -> tuple:
    return tuple(round(float(v), 6) for v in params.values())


def unlabelled(leader: dict) -> dict:
    return {k: v for k, v in leader.items() if k not in ("stratum", "fixed")}


def test_a_target_one_table_reaches_comes_from_it_and_the_other_says_its_measured_range(lib):
    answer = ask(lib, family="ind_sym", targets=NEAR_O2, objective="max:Qp_peak")
    assert list(answer) == FAMILY_KEYS
    assert (answer["family"], answer["taps"], list(answer["strata"])) == ("ind_sym", "none", ["ind_o2", "ind_o8"])
    assert answer["facts"] == {"ind_o2": {"generator": "clean_port_ind_sym", "fixed": O2_FIXED, "rows": 36},
                               "ind_o8": {"generator": "clean_port_ind_sym", "fixed": O8_FIXED, "rows": 42}}
    alone = ask(lib, stratum="ind_o2", targets=NEAR_O2, objective="max:Qp_peak")
    assert answer["strata"]["ind_o2"] == alone                    # a table of the family answers as stratum= does, field for field
    assert alone["measured"] and alone["candidates"] and "region_grid" not in alone
    leaders = answer["leaders"]
    assert [unlabelled(e) for e in leaders] == alone["measured"] + alone["candidates"]       # one table: its own order
    assert all((e["stratum"], e["fixed"]) == ("ind_o2", O2_FIXED) for e in leaders)
    low, high = measured_range(lib, "ind_o8", "Lp_lf")
    window = list(suggest.Target("Lp_lf", "target", 1.5e-9, 0.05).window())
    assert answer["unreachable"] == {"ind_o8": {
        "kind": "outside_range",
        "reason": f"Lp_lf window 1.425-1.575 nH lies below this table's measured {low * 1e9:.4g}-{high * 1e9:.4g} nH",
        "ranges": {"Lp_lf": [low, high]}, "outside": [{"quantity": "Lp_lf", "window": window, "range": [low, high], "side": "below"}],
        "closest": None}}
    assert answer["notes"] == [f"Lp_lf window 1.425-1.575 nH: designs in ind_o2; ind_o8 reaches Lp_lf {low * 1e9:.4g} nH at the least"]


def test_targets_each_inside_its_range_but_not_together_name_the_closest_measured_row(lib):
    """ind_o8: the largest inductance comes with the lowest resonance, so no row has both near their maxima; ind_o2's rows
    stay below the inductance asked for."""
    ds = lib.dataset("ind_o8")
    lp, srf = ds.values("Lp_lf"), ds.values("SRF_p")
    lp_min, srf_min = 0.95 * lp.max(), 0.95 * srf.max()
    assert (lp >= lp_min).any() and (srf >= srf_min).any() and not ((lp >= lp_min) & (srf >= srf_min)).any()
    answer = ask(lib, family="ind_sym", targets={"Lp_lf": {"min": lp_min}, "SRF_p": {"min": srf_min}})
    assert answer["leaders"] == [] and list(answer["unreachable"]) == ["ind_o2", "ind_o8"]
    why = answer["unreachable"]["ind_o8"]
    distance = np.hypot(np.maximum(lp_min - lp, 0) / lp_min, np.maximum(srf_min - srf, 0) / srf_min)   # each shortfall over its bound
    best = ds.rows[int(np.argmin(distance))]
    assert (why["kind"], why["outside"]) == ("not_met_together", [])
    assert why["ranges"] == {"Lp_lf": list(measured_range(lib, "ind_o8", "Lp_lf")), "SRF_p": list(measured_range(lib, "ind_o8", "SRF_p"))}
    assert why["closest"] == {"part": best.part, "obs_id": best.obs_id, "params": best.coords,
                              "values": {"Lp_lf": best.values["Lp_lf"], "SRF_p": best.values["SRF_p"]},
                              "distance": pytest.approx(float(distance.min()), abs=1e-6)}
    assert why["reason"].startswith("every target lies inside this table's measured range, but no measured row meets them together")
    assert f"the closest measured row is {best.part}/{best.obs_id}: Lp_lf " in why["reason"]
    o2 = answer["unreachable"]["ind_o2"]
    assert o2["kind"] == "outside_range" and [(o["quantity"], o["side"]) for o in o2["outside"]] == [("Lp_lf", "above")]
    (note,) = answer["notes"]
    assert ": no table has a design; ind_o2 reaches Lp_lf " in note and note.endswith(
        "nH at the most; ind_o8 meets each target alone, not all together")


def test_taps_choose_the_tables_and_strata_lists_them_as_given(lib):
    assert family.members(lib, "ind_sym") == ["ind_o2", "ind_o8"] == family.members(lib, "clean_port_ind_sym", "none")
    assert family.members(lib, "ind_sym", "any") == ["ind_ct", "ind_o2", "ind_o8"]
    assert family.members(lib, "ind_sym", "only") == ["ind_ct"]
    with pytest.raises(ValueError, match=r"no table of family 'xfm_bs' .*the library's families: ind_sym \(3 tables\)"):
        family.members(lib, "xfm_bs")
    with pytest.raises(ValueError, match="taps is none"):
        family.members(lib, "ind_sym", "some")
    tapped = ask(lib, family="ind_sym", taps="only", targets=NEAR_O2)
    assert (tapped["taps"], list(tapped["strata"]), tapped["unreachable"], tapped["notes"]) == ("only", ["ind_ct"], {}, [])
    assert tapped["facts"]["ind_ct"]["fixed"] == {"opening_um": 8.0, "metal": "6", "taps": ["CT"], "ct_metal": "5"}
    assert tapped["leaders"] and {e["stratum"] for e in tapped["leaders"]} == {"ind_ct"}
    assert list(ask(lib, family="ind_sym", taps="any", targets=NEAR_O2)["strata"]) == ["ind_ct", "ind_o2", "ind_o8"]
    listed = ask(lib, strata="ind_o8,ind_ct", targets=NEAR_O2, objective="max:Qp_peak")
    assert (listed["family"], listed["taps"], list(listed["strata"]), listed["leaders"]) == (None, None, ["ind_o8", "ind_ct"], [])
    error = listed["strata"]["ind_ct"]["error"]
    assert "ind_ct has no quantities ['Qp_peak']" in error and listed["unreachable"]["ind_ct"] == {
        "kind": "cannot_take", "reason": f"ind_ct cannot take the request: {error}", "ranges": {}, "outside": [], "closest": None}
    assert listed["notes"][0].endswith("no table has a design; ind_o8 reaches Lp_lf 2.327 nH at the least; ind_ct cannot take the request")


def test_a_column_no_row_of_a_table_has_is_that_tables_refusal(lib):
    """Every ind_o8 row resonates below 1.25 x 50 GHz, so none has Lp@50: that table is refused before any model is fitted."""
    answer = ask(lib, family="ind_sym", targets={"Lp@50": {"min": 2e-9}})
    assert answer["strata"]["ind_o8"] == {"error": "ind_o8: no row has a value of Lp@50; a row has a curve's value only inside its sweep "
                                                   "and while its system SRF lies above the curve's srf_margin x f"}
    assert answer["unreachable"]["ind_o8"]["kind"] == "cannot_take" and "error" not in answer["strata"]["ind_o2"]


@pytest.mark.parametrize("kw, message", [
    ({}, "give exactly one of stratum= .* got none"),
    ({"stratum": "ind_o8", "family": "ind_sym"}, "give exactly one of stratum= .* got stratum, family"),
    ({"strata": "ind_o8", "taps": "any"}, "taps= filters the tables of a family="),
    ({"stratum": "ind_o8", "taps": "none"}, "taps= filters the tables of a family="),
    ({"strata": "ind_o8,ind_x"}, r"no table \['ind_x'\]"),
])
def test_the_tables_are_named_one_way(lib, kw, message):
    for block in (library_blocks.suggest, library_blocks.region):
        with pytest.raises(ValueError, match=message):
            block(lib, targets=NEAR_O2, **kw)


def test_leaders_merge_the_tables_by_the_objective_keeping_each_tables_order(lib):
    answer = ask(lib, family="ind_sym", targets=BOTH, objective="max:Qp_peak", n=3)
    measured = [e for e in answer["leaders"] if "measured" in e]
    predicted = [e for e in answer["leaders"] if "measured" not in e]
    assert answer["leaders"] == measured + predicted and len(measured) == len(predicted) == 3 and answer["unreachable"] == {}
    for group, field in ((measured, "measured"), (predicted, "candidates")):
        bounds = [e["predicted"]["Qp_peak"]["lo"] for e in group]
        assert bounds == sorted(bounds, reverse=True)                  # the objective's conservative bound, across the tables
        for name in ("ind_o2", "ind_o8"):
            own = [unlabelled(e) for e in group if e["stratum"] == name]
            assert own == answer["strata"][name][field][:len(own)]    # each table's own order
    closest = ask(lib, family="ind_sym", targets=BOTH, n=4)           # no objective: closeness to the window's centre
    off = [abs(e["predicted"]["Lp_lf"]["value"] / 2.35e-9 - 1) for e in closest["leaders"] if "measured" in e]
    assert off == sorted(off) and {e["stratum"] for e in closest["leaders"]} == {"ind_o2", "ind_o8"}


def test_region_asks_every_table_and_out_writes_the_one_table_that_holds_points(lib, tmp_path):
    kw = {"steps": STEPS, "pool_size": 512, "workers": 1}
    with pytest.raises(ValueError, match=r"out= writes the combinations of one table, and 2 tables hold robust grid points: "
                                         r"ind_o2 \(\d+\), ind_o8 \(\d+\); rerun with stratum=<one of them> and out="):
        library_blocks.region(lib, family="ind_sym", targets=BOTH, out=str(tmp_path / "both.json"), **kw)
    assert not (tmp_path / "both.json").exists()
    answer = library_blocks.region(lib, family="ind_sym", targets=NEAR_O2, out=str(tmp_path / "o2.json"), device="L1", **kw)
    assert list(answer) == [*FAMILY_KEYS, "combinations", "device_block", "variables_block"]
    o2 = answer["strata"]["ind_o2"]
    assert answer["combinations"] == {"path": str(tmp_path / "o2.json"), "level": "robust", "rows": o2["levels"]["robust"]["count"]}
    assert json.loads((tmp_path / "o2.json").read_text(encoding="utf-8"))["source"]["stratum"] == "ind_o2"
    assert "id: L1" in answer["device_block"] and "opening_um: 2.0" in answer["device_block"]
    assert answer["notes"][-1] == "out= wrote the robust grid points of ind_o2, the only table that holds any"
    alone = library_blocks.region(lib, "ind_o2", NEAR_O2, **kw)
    assert {k: v for k, v in o2.items() if k != "seconds"} == {k: v for k, v in alone.items() if k != "seconds"}
    assert [(e["stratum"], e["fixed"]) for e in answer["leaders"]] == [("ind_o2", O2_FIXED), ("ind_o8", O8_FIXED)]
    first, last = answer["leaders"]
    assert (first["robust"], first["mean"], first["measured"]) == (o2["levels"]["robust"], o2["levels"]["mean"], len(o2["measured"]))
    assert first["robust"]["count"] > 0 and last["robust"]["count"] == last["measured"] == 0
    assert answer["unreachable"]["ind_o8"]["kind"] == "outside_range" and list(answer["unreachable"]) == ["ind_o8"]
    with pytest.raises(ValueError, match="no table of the request holds a robust grid point, so there is no combination to write"):
        library_blocks.region(lib, family="ind_sym", targets={"Lp_lf": {"min": 9e-9, "max": 1e-8}}, out=str(tmp_path / "x.json"), **kw)


def test_n50_a_region_the_sobol_pool_misses_gives_candidates_from_regions_grid(lib):
    """16 Sobol points over ind_o8 miss the designs within 2 % of 3.2 nH with Q >= 100; lib.region's grid for the same
    targets holds them, and suggest's second pool is that grid: its candidates are robust points of lib.region's own."""
    targets, objective = {"Lp_lf": {"target": 3.2e-9, "tol": 0.02}, "Qp_peak": {"min": 100}}, "max:Qp_peak"
    answer = suggest.suggest(lib, "ind_o8", targets, objective, n=3, pool_size=16, verify_build=False)
    assert answer["satisfying"] == answer["satisfying_measured"]          # no predicted point of the Sobol pool meets them
    grid = region.region(lib, "ind_o8", targets, objective, workers=1, sample_size=10**6)          # lib.region at its defaults
    robust = {key(p["params"]) for p in grid["points_sample"] if p["level"] == "robust"}
    rows = {key(r.coords) for r in lib.dataset("ind_o8").rows}
    second = answer["region_grid"]
    assert {k: second[k] for k in ("steps", "bracket", "points", "relax")} == {k: grid["grid"][k] for k in ("steps", "bracket", "points", "relax")}
    assert second["satisfying"] == len(robust - rows) > 0                  # the library's rows are measured designs, not candidates
    assert len(answer["candidates"]) == 3 and all(c["pool"] == "region_grid" and key(c["params"]) in robust for c in answer["candidates"])
    assert any("a second pool was drawn: the grid lib.region searches" in note for note in answer["notes"])
    wide = suggest.suggest(lib, "ind_o8", targets, objective, n=3, pool_size=4096, verify_build=False)
    assert "region_grid" not in wide and wide["candidates"] and {c["pool"] for c in wide["candidates"]} == {"sobol"}


def test_call_lib_suggest_with_a_family_prints_json(lib, tmp_path, monkeypatch):
    use_site(monkeypatch, tmp_path / "site.yaml", local=LOCAL)
    args = ["call", "lib.suggest", str(lib.root), f"targets={json.dumps(NEAR_O2)}", "objective=max:Qp_peak", "n=1", "pool_size=256",
            "verify_build=false"]
    out = CliRunner().invoke(app, [*args, "family=ind_sym"])
    assert out.exit_code == 0, out.output
    assert "Infinity" not in out.stdout and "NaN" not in out.stdout
    body = json.loads(out.stdout)
    assert list(body) == FAMILY_KEYS and list(body["strata"]) == ["ind_o2", "ind_o8"] and body["leaders"][0]["stratum"] == "ind_o2"
    assert json.loads(CliRunner().invoke(app, [*args, "strata=ind_o8,ind_o2"]).stdout)["family"] is None
    refused = CliRunner().invoke(app, [*args, "family=ind_sym", "stratum=ind_o2"])
    assert refused.exit_code == 2 and "give exactly one of stratum=" in refused.output
