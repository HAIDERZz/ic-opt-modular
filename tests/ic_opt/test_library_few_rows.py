"""N-120 (``docs/refactor/N120_FEW_ROWS_COLUMNS_SPEC.md``): a column whose usable rows are too few for a model is reported,
not fitted into a crash.

``Library.no_model`` decides it from the rows, before anything is fitted: a column can have a model exactly when its fit and
its 5 x 20 % hold-out complete on its usable rows (``query.fits``, ``query.composed_fits``). ``Library.models`` fits no
other, ``lib.query`` answers it ``no_model`` with its row count, ``Library.cached`` lists it as ``too few rows (<n>)``, and
lib.suggest / lib.region / lib.densify refuse a request naming it (a family request: that table's ``cannot_take``).

The fake strata hold rows written straight into the library's datasets (no sNp, no EMX) on the multi-turn transformer's
dims. ``few``: ``Lp_lf`` (40 rows at two secondary turns, 4 at three; per turns level, no feature map), ``k_lf`` (2 rows,
one joint model on the dimensionless features), ``Lp@100`` (``model: ratio`` on ``Lp_lf``, a joint part on the features;
its 4 rows at three turns, where ``Lp_lf`` has no sub-model) and ``Lp@120`` (no row). ``kept``: ``Lp_lf``, ``Qp_peak`` (4
rows at three turns: per turns level, no sub-model) and ``Qs_peak`` (no row)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from ic_opt.library import dataset, densify, family, manifest, region
from ic_opt.library import query as q
from ic_opt.library import suggest as s
from tests.ic_opt.library_fixtures import LOCAL

DIMS = list(manifest.XFM_MS_DIMS)
MAPPED = "xfm_ms_dimensionless"
STEPS = {"primary_outer_diameter_um": 1, "secondary_outer_diameter_um": 1, "primary_width_um": 0.1, "secondary_width_um": 0.1,
         "center_spacing_um": 0.5, "secondary_spacing_um": 0.1}
TWO = [(op, os_, wp, ws, 0.0, 2.0, 2.0) for op in (60.0, 70.0, 80.0, 90.0, 100.0) for os_ in (50.0, 60.0) for wp in (5.0, 8.0)
       for ws in (5.0, 8.0)]                                                     # 40 rows, two secondary turns
THREE = [(op, 55.0, wp, 6.0, 0.0, 2.0, 3.0) for op in (70.0, 90.0) for wp in (5.0, 8.0)]   # 4 rows, three secondary turns
BETWEEN = dict(zip(DIMS, (75.0, 55.0, 6.5, 6.5, 0.0, 2.0, 2.0)))                # no row; inside the two-turn rows' hull
AT_THREE = dict(zip(DIMS, (80.0, 55.0, 6.5, 6.0, 0.0, 2.0, 3.0)))               # no row; between the three-turn rows
REASON = "{} usable rows: too few for a model of this column"
FEW = {"Lp_lf": None, "k_lf": 2, "Lp@100": 4, "Lp@120": 0}                      # column -> its usable rows when too few
KEPT = {"Lp_lf": None, "Qp_peak": None, "Qs_peak": 0}


def lp_lf(op, os_, wp, ws, cs, ss, nt) -> float:
    return 1e-10 * (op / 100) ** 1.3 * (os_ / 100) ** 0.2 * (5 / wp) ** 0.1 * nt


def values(i: int, g: tuple) -> dict:
    three = g[-1] == 3.0
    return {"Lp_lf": lp_lf(*g), "k_lf": 0.5 + 0.01 * i if i < 2 else None, "Lp@100": 1.2 * lp_lf(*g) if three else None,
            "Lp@120": None, "Qp_peak": 10 + g[0] / 100 if three else None, "Qs_peak": None, "SRF_p": None}


def stratum_rows(name: str, columns: list[str]) -> dataset.Dataset:
    rows = [dataset.Row(name, f"obs_{i:04d}", dict(zip(DIMS, g)), {c: values(i, g)[c] for c in columns}, 250e9, 251, 4, 0.5, True,
                        f"{name}/obs_{i:04d}.s4p") for i, g in enumerate(TWO + THREE)]
    return dataset.Dataset(name, DIMS, "secondary_turns", columns, rows, {name: "gen1"}, {}, "off", f"{name}key")


@pytest.fixture(scope="module")
def root(tmp_path_factory) -> Path:
    """A library.yaml declaring the two fake strata; their datasets come from ``stratum_rows`` (``library``)."""
    root = tmp_path_factory.mktemp("few_rows")
    common = {"generator": "clean_port_xfm_ms", "dims": DIMS, "nt_dim": "secondary_turns", "steps": STEPS}
    doc = {"schema_version": "ic-opt-library-v1", "process_profile": "demo_6m", "strata": {
        "few": {**common, "parts": [{"store": "few"}], "quantities": {
            "Lp_lf": {}, "k_lf": {"feature_map": MAPPED}, "Lp": {"anchors_ghz": [100, 120], "model": "ratio", "feature_map": MAPPED}}},
        "kept": {**common, "parts": [{"store": "kept"}], "quantities": {
            "Lp_lf": {}, "Qp_peak": {"band_ghz": 250}, "Qs_peak": {"band_ghz": 250}}}}}
    (root / "library.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return root


def library(root: Path, cache: Path) -> q.Library:
    """A fresh library on ``root`` with an empty cache directory ``cache`` and the fake strata's rows."""
    lib = q.Library(root, limits=LOCAL, cache_dir=cache)
    for name in lib.strata():
        lib._datasets[name] = stratum_rows(name, lib.manifest.strata[name].columns())
    return lib


@pytest.fixture
def lib(root, tmp_path) -> q.Library:
    return library(root, tmp_path / "cache")


def fitted(monkeypatch) -> list[str]:
    """From now on, every column ``Library._fit`` fits (calibration and model) in this process."""
    out: list[str] = []
    original = q.Library._fit

    def fit(self, stratum, quantity):
        out.append(quantity)
        return original(self, stratum, quantity)

    monkeypatch.setattr(q.Library, "_fit", fit)
    return out


def test_the_rows_decide_which_columns_can_have_a_model(lib):
    assert list(lib.dataset("few").columns) == list(FEW) and list(lib.dataset("kept").columns) == list(KEPT)
    for name, expected in (("few", FEW), ("kept", KEPT)):
        for column, rows in expected.items():
            short = lib.no_model(name, column)
            assert short == (None if rows is None else {"rows": rows, "reason": REASON.format(rows)}), (name, column)
    assert len(lib.dataset("kept").usable("Qp_peak")) == 4          # as few as Lp@100's, per turns level: it fits (below)


def test_the_condition_is_where_the_librarys_own_fit_raises(root, tmp_path):
    """The verdict mirrors what the library's own calibration and fit do with these rows (``Library._fit``, unchanged since
    before N-120): it raises exactly for the columns ``no_model`` names -- ``StratumGP.fit`` on no row, scikit-learn on a
    joint fold model's empty held-out set (2 rows: round(0.4) = 0; Lp@100: its one held-out row's level has no Lp_lf
    sub-model in the fold, so the joint part is asked for no row), ``ComposedGP.fit`` on no curve row -- and completes for
    the others, ``kept``'s Qp_peak included: a per-level model on 4 rows has no sub-model and scores no held-out row, but
    raises nothing."""
    lib = library(root, tmp_path / "cache")
    raised = {}
    for name, columns in (("few", FEW), ("kept", KEPT)):
        for column in columns:
            try:
                lib._fit(name, column)
            except ValueError as exc:
                raised[(name, column)] = str(exc)
    assert set(raised) == {(name, c) for name, columns in (("few", FEW), ("kept", KEPT)) for c, rows in columns.items()
                           if rows is not None}
    assert "Found array with 0 sample(s)" in raised[("few", "k_lf")] and "Found array with 0 sample(s)" in raised[("few", "Lp@100")]
    assert raised[("few", "Lp@120")].startswith("no row has a positive curve value") and raised[("kept", "Qs_peak")] == "expected [N, 7] inputs"
    calibration = lib._models[("kept", "Qp_peak")].calibration
    assert calibration["n_scored"] == 0 and np.isnan(calibration["median_rel"])        # fitted, as before: nothing scored
    assert not lib._models[("kept", "Qp_peak")].gp.available(np.array([list(AT_THREE.values())]))[0]


def test_the_condition_in_numbers():
    assert [q.fits(n, per_nt=False) for n in range(5)] == [False, False, False, True, True]     # round(0.2 n) >= 1
    assert [q.fits(n, per_nt=True) for n in range(3)] == [False, True, True]
    assert q.fits(1, per_nt=False, calibrate=False) and not q.fits(0, per_nt=True, calibrate=False)
    levels, nan = np.array([2.0] * 30 + [3.0] * 4), np.full(34, np.nan)
    base = np.full(34, 1e-10)
    three = np.where(levels == 3.0, 2e-10, nan)
    assert not q.composed_fits(levels, three, base, None, {"part": False, "lf": True})       # the held-out row's level: no lf sub-model
    assert q.composed_fits(levels, three, base, None, {"part": True, "lf": True})            # all per level: nothing reaches scikit-learn
    assert q.composed_fits(levels, three, base, None, {"part": False, "lf": False})          # all joint: every held-out row answered
    assert not q.composed_fits(levels, three, nan, None, {"part": True, "lf": True})          # no base value: no row for the part
    two = np.where(np.arange(34) < 2, 2e-10, nan)
    assert not q.composed_fits(levels, two, base, None, {"part": False, "lf": False})        # 2 curve rows: no held-out row
    assert q.composed_fits(levels, two, base, None, {"part": False, "lf": False}, calibrate=False)
    srf = np.full(34, np.nan)
    assert not q.composed_fits(levels, three, base, srf, {"part": False, "lf": False, "srf": False})   # a joint SRF fold model on no row


def test_models_of_every_column_fit_only_the_ones_that_can_have_one(lib, monkeypatch):
    fits = fitted(monkeypatch)
    assert lib.cached("few") == {"Lp_lf": "none", "k_lf": "too few rows (2)", "Lp@100": "too few rows (4)", "Lp@120": "too few rows (0)"}
    models = lib.models("few", list(FEW))
    assert fits == ["Lp_lf"] and list(models) == ["Lp_lf"]
    assert lib.cached("few")["Lp_lf"] == "model" and {c: v for c, v in lib.cached("few").items() if c != "Lp_lf"} == {
        "k_lf": "too few rows (2)", "Lp@100": "too few rows (4)", "Lp@120": "too few rows (0)"}
    assert lib.models("few", ["k_lf", "Lp@120"]) == {} and fits == ["Lp_lf"]               # asked alone: nothing fitted
    with pytest.raises(ValueError, match=r"few has no model of k_lf \(2 usable rows: too few for a model of this column\)"):
        lib.model("few", "k_lf")
    assert fits == ["Lp_lf"]


def test_a_query_of_every_column_answers_no_model_for_those_and_the_others_as_before(lib):
    answer = q.query(lib, "few", BETWEEN)
    got = answer["quantities"]
    assert list(got) == list(FEW) and got["Lp_lf"]["status"] in ("predicted", "uncertain")
    assert got["Lp_lf"] == q.query(lib, "few", BETWEEN, ["Lp_lf"])["quantities"]["Lp_lf"]   # as when asked alone
    for column in ("k_lf", "Lp@100", "Lp@120"):
        rows = FEW[column]
        assert got[column] == {"status": "no_model", "rows": rows, "reason": REASON.format(rows), "unit": q.unit(column)}
    row = lib.dataset("few").rows[40]                                                    # a library row: measured, as before
    measured = q.query(lib, "few", row.coords)["quantities"]
    assert measured["Lp@100"] == {"status": "measured", "value": row.values["Lp@100"], "unit": "H"}
    assert measured["Lp@120"] == {"status": "measured", "value": None, "unit": "H"}


def test_a_per_level_column_whose_rows_fit_keeps_its_model_and_answers(lib, monkeypatch):
    """``kept``'s Qp_peak: 4 rows at three turns, a per-level model with no sub-model -- fitted as before N-120 (it raises
    nothing) and answering out of domain (criterion 2) as before; Qs_peak, without a row, has none."""
    fits = fitted(monkeypatch)
    assert lib.cached("kept") == {"Lp_lf": "none", "Qp_peak": "none", "Qs_peak": "too few rows (0)"}
    assert list(lib.models("kept", list(KEPT))) == ["Lp_lf", "Qp_peak"] and fits == ["Lp_lf", "Qp_peak"]
    got = q.query(lib, "kept", AT_THREE)["quantities"]
    assert got["Qp_peak"]["status"] == "out_of_domain" and got["Qp_peak"]["criterion"] == 2
    assert got["Qp_peak"]["reason"] == "secondary_turns=3 has 4 rows (< 25)"
    assert got["Qs_peak"] == {"status": "no_model", "rows": 0, "reason": REASON.format(0), "unit": "1"}


def test_a_target_objective_or_trend_on_such_a_column_is_refused_before_anything_is_fitted(lib, monkeypatch):
    fits = fitted(monkeypatch)
    two = r"few has no model of k_lf \(2 usable rows: too few for a model of this column\)"
    with pytest.raises(ValueError, match=two):
        s.suggest(lib, "few", {"k_lf": {"min": 0.3}}, verify_build=False)
    with pytest.raises(ValueError, match=two):
        s.suggest(lib, "few", {"Lp_lf": {"min": 1e-10}}, "max:k_lf", verify_build=False)
    with pytest.raises(ValueError, match=r"few has no model of Lp@120 \(0 usable rows"):
        region.region(lib, "few", {"Lp_lf": {"min": 1e-10}}, trend=("Lp@120", "primary_outer_diameter_um"))
    with pytest.raises(ValueError, match=two):
        region.region(lib, "few", {"k_lf": {"min": 0.3}})
    with pytest.raises(ValueError, match=r"few has no model of k_lf .*; Lp@100 \(4 usable rows"):
        densify.densify(lib, "few", ["Lp_lf", "k_lf", "Lp@100"], n=1)
    assert fits == [] and not any(lib.cache.directory.glob("model-*"))


def test_a_family_request_names_the_reason_as_that_tables_cannot_take(lib, monkeypatch):
    fits = fitted(monkeypatch)
    answer = family.suggest(lib, ["few"], {"k_lf": {"min": 0.3}}, n=1, pool_size=64, verify_build=False)
    error = "few has no model of k_lf (2 usable rows: too few for a model of this column)"
    assert answer["strata"]["few"] == {"error": error} and fits == []
    assert answer["unreachable"]["few"] == {"kind": "cannot_take", "reason": f"few cannot take the request: {error}", "ranges": {},
                                            "outside": [], "closest": None}


def test_densify_of_every_column_leaves_them_out_with_a_note(lib, monkeypatch):
    fits = fitted(monkeypatch)
    answer = densify.densify(lib, "few", n=2, pool_size=256, top=64)
    assert answer["quantities"] == ["Lp_lf"] and fits == ["Lp_lf"] and len(answer["candidates"]) == 2
    assert ("3 column(s) left out, their usable rows too few for a model (lib.query answers them no_model): k_lf (2 rows), "
            "Lp@100 (4 rows), Lp@120 (0 rows)") in answer["notes"]


def test_the_query_block_answers_no_model_in_strict_json(lib):
    from ic_opt.blocks import library as blocks

    answer = blocks.query(lib, "few", BETWEEN, "k_lf,Lp@120")
    assert answer["quantities"] == {"k_lf": {"status": "no_model", "rows": 2, "reason": REASON.format(2), "unit": "1"},
                                    "Lp@120": {"status": "no_model", "rows": 0, "reason": REASON.format(0), "unit": "H"}}
    json.dumps(answer, allow_nan=False)


def test_a_resonance_column_without_a_model_limits_no_curve(root, tmp_path, monkeypatch):
    """``Library.resonance`` takes the resonance columns that can have a model: one with too few rows is left out, as one
    without rows always was -- here SRF_p with 2 rows, joint on the dimensionless features, so the curve has no rule and
    asking for it fits no SRF_p (whose hold-out would raise)."""
    doc = yaml.safe_load((root / "library.yaml").read_text(encoding="utf-8"))
    doc["strata"]["few"]["quantities"]["SRF_p"] = {"feature_map": MAPPED}
    other = tmp_path / "srf"
    other.mkdir()
    (other / "library.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    lib = q.Library(other, limits=LOCAL, cache_dir=tmp_path / "cache")
    ds = stratum_rows("few", lib.manifest.strata["few"].columns())
    for i, row in enumerate(ds.rows):
        row.values["SRF_p"] = 150e9 + i if i < 2 else None
    lib._datasets["few"] = ds
    assert lib.no_model("few", "SRF_p") == {"rows": 2, "reason": REASON.format(2)}
    assert lib.resonance("few", "Lp@100") is None and lib.resonance("few", "Lp@120") is None
    fits = fitted(monkeypatch)
    assert list(lib.models("few", ["Lp_lf", "SRF_p"])) == ["Lp_lf"] and fits == ["Lp_lf"]


def test_lib_signoff_leaves_such_a_column_out_of_its_report(tmp_path, monkeypatch, capsys):
    """The synthetic inductor table with Lp anchored at 70 GHz too, past every row's 60 GHz sweep: lib_signoff fits the
    other columns, gets through its plan and its run, says which column it leaves out, and reports the others."""
    pytest.importorskip("klayout.db")
    from ic_opt.recipe import PLAN_MODE, Run, load_recipe
    from ic_opt.site import Site
    from ic_opt.store import RunStore
    from tests.ic_opt.fakes import FakeSpectreExecutor
    from tests.ic_opt.library_fixtures import build_library, use_site
    from tests.ic_opt.test_library import part_spec
    from tests.ic_opt.test_library_signoff import library_physics

    use_site(monkeypatch, tmp_path / "site.yaml", local=LOCAL)
    lib_root = build_library(tmp_path / "lib")
    doc = yaml.safe_load((lib_root / "library.yaml").read_text(encoding="utf-8"))
    doc["strata"]["ind_demo"]["quantities"]["Lp"]["anchors_ghz"] = [10, 70]
    (lib_root / "library.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    (tmp_path / "cand.json").write_text(json.dumps([{"outer_diameter_um": 155, "width_um": 4.5, "spacing_um": 2.5, "turns": 2}]),
                                        encoding="utf-8")
    project = tmp_path / "signoff"                       # test_library_signoff.make_run's, on LOCAL: one fit at a time, here
    project.mkdir()
    spec = part_spec("signoff", 60)
    (project / "spec.yaml").write_text(yaml.safe_dump(spec.model_dump(mode="json")), encoding="utf-8")
    store = RunStore(project)
    ex = FakeSpectreExecutor(store.root / "sims", snp_fn=library_physics)
    site = Site({"local": LOCAL})
    run = Run(project, spec, store, ex, None, site, site.host("local"))
    main = load_recipe("lib_signoff")
    token = PLAN_MODE.set(True)
    try:
        main(run, library=str(lib_root), candidates=str(tmp_path / "cand.json"), stratum="ind_demo")
    finally:
        PLAN_MODE.reset(token)
    note = ("lib_signoff: 1 of ind_demo's columns have too few usable rows for a model and are left out of the report: "
            "Lp@70 (0)")
    assert note in capsys.readouterr().out and ex.emx_runs == 0
    main(run, library=str(lib_root), candidates=str(tmp_path / "cand.json"), stratum="ind_demo")
    report = json.loads((run.store.root / "reports" / "lib_signoff.json").read_text())
    (point,) = report["points"]
    assert ex.emx_runs == 1 and point["status"] == "ok" and set(point["quantities"]) == {"Lp_lf", "Qp_peak", "SRF_p", "Lp@10"}
    assert point["quantities"]["Lp_lf"]["status"] == "predicted" and report["coverage"] is not None


def test_lib_refine_drops_a_neighbour_whose_column_has_no_model_as_uncertain_and_says_so():
    """lib_refine asks query.query for its columns: a ``no_model`` one has no value, so the neighbour fails ``uncertain``,
    named as such -- for ``Qmin`` too, whichever Q has no model."""
    from ic_opt.library import index
    from ic_opt.recipes import lib_refine

    axes = {"Qmin": index.Axis.of("Qmin", 5, 20, 0.1)}
    none = {"status": "no_model", "rows": 2, "reason": REASON.format(2), "unit": "1"}
    for other in ({"status": "predicted", "value": 12.0, "lo": 11.0, "hi": 13.0}, {"status": "uncertain", "value": 12.0}):
        for answers in ({"Qp@112": other, "Qs@112": none}, {"Qp@112": none, "Qs@112": other}):
            qmin = lib_refine._answer("Qmin", ["Qp@112", "Qs@112"], answers)
            assert qmin == {"status": "no_model", "value": None}
            assert lib_refine.verdict({"Qmin": qmin}, None, axes, 4e10) == ("uncertain", "Qmin: no_model")
