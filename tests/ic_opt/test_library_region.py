"""T14.2: lib.region on the synthetic transformer library -- window targets, the score split, the grid, both levels and the summaries."""
from __future__ import annotations

import json

import numpy as np
import pytest

from ic_opt.library import domain, query, region
from ic_opt.library import suggest as s
from tests.ic_opt.library_fixtures import XFM_DIMS, build_library, build_xfm_library

pytest.importorskip("klayout.db")

OD_P, OD_S, W_P, W_S, CS = XFM_DIMS
LATTICE = {OD_P: 20, OD_S: 4, W_P: 1, W_S: 1, CS: 0.5}              # most library rows sit on these multiples
PLAIN = {"Lp_lf": {"min": 0.40e-9, "max": 0.50e-9}, "k_lf": {"min": 0.6}}
ANCHORED = {"Lp_lf": {"min": 0.58e-9, "max": 0.82e-9}, "k@10": {"min": 0.66}}   # implies SRF >= 12.5 GHz


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    return query.Library(build_xfm_library(tmp_path_factory.mktemp("xfmregion")))


def run(lib, targets, objective=None, **kw):
    return region.region(lib, "xfm_demo", targets, objective, **{"pool_size": 2048, "workers": 1, **kw})


def key(params: dict) -> tuple:
    return tuple(round(params[d], 6) for d in XFM_DIMS)


def plain(value) -> bool:
    """JSON data made of Python types only."""
    if isinstance(value, dict):
        return all(isinstance(k, str) and plain(v) for k, v in value.items())
    if isinstance(value, list):
        return all(plain(v) for v in value)
    return value is None or type(value) in (bool, int, float, str)


def score_before(x, models, targets, objective, *, k=2.0, rel_sigma_max=domain.DEFAULT_SIGMA_REL_MAX):
    """suggest.score as it was before T14.2 (two GP predictions per quantity; its settled rows are not exercised here):
    the reference the split must reproduce."""
    n, ok, pred = len(x), np.ones(len(x), dtype=bool), {}
    for q in sorted({t.quantity for t in targets} | ({objective[1]} if objective else set())):
        m, scale = models[q], 1e9 if q.startswith("SRF") else 1.0
        mu, sigma = m.gp.predict(x)
        lo, hi = m.gp.predict_bounds(x, k)
        mu, sigma, lo, hi = mu * scale, sigma * scale, lo * scale, hi * scale
        ok &= np.isfinite(mu) & domain.sigma_ok(mu, sigma, rel_sigma_max) & m.guard.inside(x)
        pred[q] = {"value": mu, "lo": lo, "hi": hi, "rel_sigma": sigma / np.maximum(np.abs(mu), 1e-300)}
    for t in targets:
        a, b = t.window()
        ok &= (pred[t.quantity]["lo"] >= a) & (pred[t.quantity]["hi"] <= b)
    if objective:
        order = np.lexsort((pred[objective[1]]["rel_sigma"], -pred[objective[1]]["lo"] if objective[0] == "max" else pred[objective[1]]["hi"]))
    else:
        dist = sum((np.abs(pred[t.quantity]["value"] / t.value - 1) for t in targets if t.kind == "target"), np.zeros(n))
        order = np.lexsort((sum((pred[t.quantity]["rel_sigma"] for t in targets), np.zeros(n)), dist))
    return ok, [int(i) for i in order if ok[i]], pred


def test_a_min_max_pair_is_a_window_and_satisfy_has_two_levels():
    (t,) = s.parse_targets({"Lp_lf": {"min": 1e-9, "max": 2e-9}})
    assert (t.kind, t.window(), t.upper) == ("window", (1e-9, 2e-9), 2e-9)
    with pytest.raises(ValueError, match="min < max"):
        s.parse_targets({"Lp_lf": {"min": 2e-9, "max": 1e-9}})
    pred = {"Lp_lf": {"value": np.array([1.5, 1.95, 2.5]), "lo": np.array([0.9, 1.9, 2.4]), "hi": np.array([1.6, 2.05, 2.6])}}
    goals = [s.Target("Lp_lf", "window", 1.0, upper=2.0)]
    assert s.satisfy(pred, goals, "robust").tolist() == [False, False, False]
    assert s.satisfy(pred, goals, "mean").tolist() == [True, True, False]
    assert s.satisfy(pred, [s.Target("Lp_lf", "window", 0.8, upper=2.1)], "robust").tolist() == [True, True, False]
    with pytest.raises(ValueError, match="level"):
        s.satisfy(pred, goals, "median")


def test_the_split_reproduces_score_and_the_two_prediction_original(lib, monkeypatch):
    x = s.pool(lib, "xfm_demo", 512, 3)
    for spec, objective in (({"Lp_lf": {"target": 0.45e-9, "tol": 0.1}, "k_lf": {"min": 0.6}}, "max:Qp_peak"),
                            ({"k_lf": {"target": 0.65, "tol": 0.05}}, None)):
        goals, obj = s.parse_targets(spec), s.parse_objective(objective)
        models = {q: lib.model("xfm_demo", q) for q in {t.quantity for t in goals} | ({obj[1]} if obj else set())}
        ref = s.score(x, models, goals, obj)
        ok, pred = s.predict_all(x, models)
        ok &= s.satisfy(pred, goals, "robust")
        assert ref["ranked"] and ok.tolist() == ref["ok"].tolist() and s.rank(pred, goals, obj, ok) == ref["ranked"]
        before_ok, before_ranked, before_pred = score_before(x, models, goals, obj)
        assert before_ok.tolist() == ref["ok"].tolist() and before_ranked == ref["ranked"]
        for q, p in before_pred.items():
            for field in ("value", "lo", "hi", "rel_sigma"):
                np.testing.assert_allclose(ref["pred"][q][field], p[field], rtol=1e-12)
    monkeypatch.setattr(s, "PREDICT_CHUNK", 100)                                   # a grid is predicted in chunks: the same answer
    chunked_ok, chunked = s.predict_all(x, models)
    monkeypatch.undo()
    whole_ok, whole = s.predict_all(x, models)
    assert chunked_ok.tolist() == whole_ok.tolist()
    for q in whole:                  # up to the GP's own rounding: its mean is a large cancelling sum, ordered by the batch size
        for field in ("value", "lo", "hi", "rel_sigma", "sigma"):
            np.testing.assert_allclose(chunked[q][field], whole[q][field], rtol=1e-8)
    window = s.suggest(lib, "xfm_demo", {"Lp_lf": {"min": 0.44e-9, "max": 0.46e-9}}, None, n=2, pool_size=512, verify_build=False)
    assert window["candidates"] and all(0.44e-9 <= c["predicted"]["Lp_lf"]["lo"] <= c["predicted"]["Lp_lf"]["hi"] <= 0.46e-9
                                        for c in window["candidates"])


@pytest.mark.parametrize("targets", [PLAIN, ANCHORED], ids=["plain", "anchored"])
def test_every_measured_design_on_the_grid_lies_in_the_mean_set(lib, targets):
    """C1: with the grid on the library's lattice, a row whose measured values meet the targets is a mean-feasible point."""
    r = run(lib, targets, steps=LATTICE, sample_size=10**7, group_by=[W_P, W_S])
    mean = {key(p["params"]) for p in r["points_sample"]}
    robust = {key(p["params"]) for p in r["points_sample"] if p["level"] == "robust"}
    assert len(mean) == r["levels"]["mean"]["count"] and 0 < len(robust) == r["levels"]["robust"]["count"] and robust <= mean
    on_grid = [m for m in r["measured"] if all(abs(m["params"][d] / v - round(m["params"][d] / v)) < 1e-9 for d, v in LATTICE.items())]
    assert len(on_grid) >= 8 and all(key(m["params"]) in mean for m in on_grid)
    assert r["group_by"]["rows"] and all(row["count_robust"] <= row["count_mean"] for row in r["group_by"]["rows"])
    assert r["grid"]["steps"] == LATTICE and r["grid"]["coarsened"] == 1 and not r["grid"]["auto_steps"]
    if targets is ANCHORED:                                     # the implied resonance, in Hz on both sides of the comparison
        assert {"quantity": "SRF", "kind": "min", "value": 12.5e9, "tol": 0.0, "upper": float("inf")} in r["targets"]
        assert all(1e10 < p["predicted"]["SRF"] < 1e11 for p in r["points_sample"]) and any("SRF >= 12.5 GHz" in note for note in r["notes"])
        assert all(m["values"]["SRF"] > 12.5e9 for m in r["measured"])


def test_an_impossible_window_returns_an_empty_region_and_says_why(lib):
    r = run(lib, {"Lp_lf": {"min": 5e-9, "max": 6e-9}, "k_lf": {"min": 0.6}}, group_by=[W_P], trend=("k_lf", CS))
    assert r["grid"]["points"] == r["grid"]["in_domain"] == r["grid"]["confident"] == 0
    assert r["levels"] == {"robust": {"count": 0, "ranges": {}}, "mean": {"count": 0, "ranges": {}}}
    assert r["binding"] == {"Lp_lf": 0, "k_lf": 0} and r["candidates"] == r["measured"] == r["points_sample"] == []
    assert r["group_by"] == {"dims": [W_P], "rows": []} and r["trend"] == {"quantity": "k_lf", "dim": CS, "rows": []}
    assert r["edge"] == {d: {"at_min": False, "at_max": False} for d in XFM_DIMS}
    assert any("no coarse candidate meets Lp_lf even with the stated windows 10% wider" in note for note in r["notes"]), r["notes"]


def test_max_points_coarsens_every_step_on_the_manifest_lattice(lib):
    lattice = lib.manifest.strata["xfm_demo"].steps
    r = run(lib, PLAIN, max_points=3000)
    grid = r["grid"]
    assert grid["auto_steps"] and grid["coarsened"] > 1 and 0 < grid["points"] <= 3000 and set(grid["steps"]) == set(XFM_DIMS)
    for d, step in grid["steps"].items():
        ratio = step / lattice[d]
        assert ratio >= grid["coarsened"] and abs(ratio - round(ratio)) < 1e-9 and round(ratio) % grid["coarsened"] == 0
    assert any("every step multiplied by" in note for note in r["notes"]) and r["levels"]["mean"]["count"] > 0
    for bad in ({CS: 0.3}, {CS: -0.5}, {"turns": 1}):
        with pytest.raises(ValueError, match="steps"):
            run(lib, PLAIN, steps=bad)


def test_group_by_trend_edge_and_candidates_have_the_documented_shapes(lib):
    r = run(lib, PLAIN, "max:Qp_peak", steps={OD_P: 5, OD_S: 5, W_P: 1, W_S: 1, CS: 2}, group_by=[W_P, W_S], trend=("k_lf", CS), n=3)
    assert plain(r) and json.loads(json.dumps(r))["grid"] == r["grid"]
    assert list(r) == ["stratum", "targets", "objective", "grid", "levels", "binding", "edge", "group_by", "trend", "candidates",
                       "candidates_level", "measured", "points_sample", "seconds", "notes"]
    assert set(r["seconds"]) == {"models", "coarse", "grid", "predict", "summarize", "total"}
    assert r["levels"]["robust"]["count"] <= r["levels"]["mean"]["count"] <= r["grid"]["confident"] <= r["grid"]["in_domain"] <= r["grid"]["points"]
    assert set(r["binding"]) == {"Lp_lf", "k_lf"} and min(r["binding"].values()) >= r["levels"]["mean"]["count"]
    assert set(r["edge"]) == set(XFM_DIMS) and all(type(v) is bool for e in r["edge"].values() for v in (e["at_min"], e["at_max"]))
    assert r["edge"][W_P] == {"at_min": True, "at_max": True}                      # widths 4-7 um all qualify: the library's whole range
    rows = r["group_by"]["rows"]
    assert r["group_by"]["dims"] == [W_P, W_S] and sum(row["count_mean"] for row in rows) == r["levels"]["mean"]["count"]
    for row in rows:
        assert set(row["key"]) == {W_P, W_S} and set(row["ranges"]) == {OD_P, OD_S, CS} and row["objective"][0] <= row["objective"][1]
    trend = r["trend"]
    assert (trend["quantity"], trend["dim"]) == ("k_lf", CS) and [t["value"] for t in trend["rows"]] == sorted(t["value"] for t in trend["rows"])
    assert all(t["count"] > 0 and t["min"] <= t["median"] <= t["max"] for t in trend["rows"])
    assert trend["rows"][0]["median"] > trend["rows"][-1]["median"]               # coupling falls as the windings move apart
    assert r["candidates_level"] == "robust" and len(r["candidates"]) == 3
    for c in r["candidates"]:
        assert set(c) == {"params", "predicted", "nearest"} and set(c["params"]) == set(XFM_DIMS) and len(c["nearest"]) == 3
        assert set(c["predicted"]) == {"Lp_lf", "k_lf", "Qp_peak"} and 0.40e-9 <= c["predicted"]["Lp_lf"]["lo"] <= c["predicted"]["Lp_lf"]["hi"] <= 0.50e-9
    bounds = [c["predicted"]["Qp_peak"]["lo"] for c in r["candidates"]]
    assert bounds == sorted(bounds, reverse=True)                                  # ranked on the objective's conservative bound
    assert all(len(p["predicted"]) == 3 and p["level"] in ("robust", "mean") for p in r["points_sample"])


def test_candidates_come_from_the_mean_set_when_no_interval_fits_the_window(lib):
    """Every calibrated interval at k = 1e4 is wider than a +-1.1 % window (sigma is floored at the held-out error), so the
    robust set is empty while predicted values still fall inside."""
    r = run(lib, {"Lp_lf": {"min": 0.445e-9, "max": 0.455e-9}}, k=1e4, steps={OD_P: 2, OD_S: 8, W_P: 1, W_S: 1, CS: 3}, n=3)
    assert r["levels"]["robust"]["count"] == 0 < r["levels"]["mean"]["count"] and r["candidates_level"] == "mean"
    assert len(r["candidates"]) == 3 and {p["level"] for p in r["points_sample"]} == {"mean"}
    off = [abs(c["predicted"]["Lp_lf"]["value"] / 0.45e-9 - 1) for c in r["candidates"]]
    assert off[0] == min(off) and all(0.445e-9 <= c["predicted"]["Lp_lf"]["value"] <= 0.455e-9 for c in r["candidates"])


def test_verify_build_attaches_the_real_generators_verdict(lib):
    r = run(lib, PLAIN, steps={OD_P: 10, OD_S: 10, W_P: 1, W_S: 1, CS: 4}, n=1, verify_build=True)
    (c,) = r["candidates"]
    assert list(c) == ["params", "build", "predicted", "nearest"] and c["build"]["built"] and c["build"]["ports"] == ["N1", "N2", "P1", "P2"]


def test_turns_go_by_level_and_a_level_keeps_the_dims_it_fixes(tmp_path_factory):
    """The inductor library: turns 1 and 2, every single-turn row at spacing 2 um (spacing does not shape one turn)."""
    lib = query.Library(build_library(tmp_path_factory.mktemp("indregion")))
    r = region.region(lib, "ind_demo", {"Lp_lf": {"min": 0.8e-9, "max": 1.6e-9}}, pool_size=512, workers=1, sample_size=10**6,
                      steps={"outer_diameter_um": 5, "width_um": 0.5, "spacing_um": 0.5}, group_by=["turns"])
    assert set(r["grid"]["steps"]) == {"outer_diameter_um", "width_um", "spacing_um"} and r["grid"]["bracket"]["turns"] == [1.0, 2.0]
    assert [row["key"] for row in r["group_by"]["rows"]] == [{"turns": 1.0}, {"turns": 2.0}]
    assert {p["params"]["spacing_um"] for p in r["points_sample"] if p["params"]["turns"] == 1} == {2.0}
    assert r["edge"]["turns"] == {"at_min": True, "at_max": True} and r["measured"]
    with pytest.raises(ValueError, match="turns go by integer level"):
        region.region(lib, "ind_demo", {"Lp_lf": {"min": 0.8e-9, "max": 1.6e-9}}, steps={"turns": 1})
