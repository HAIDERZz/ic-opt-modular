"""T14.2/T14.3: lib.region on the synthetic transformer library -- window targets, the score split, the grid, both levels, the
summaries, and the block the command line calls. T15.3: its BLAS threads and prediction chunks come from the library's limits."""
from __future__ import annotations

import inspect
import json
import shutil

import numpy as np
import pytest
from typer.testing import CliRunner

from ic_opt.blocks import library as library_blocks
from ic_opt.cli import app
from ic_opt.library import domain, gp, query, region
from ic_opt.library import suggest as s
from ic_opt.site import HostLimits
from tests.ic_opt.fakes import FAKE_HOST
from tests.ic_opt.library_fixtures import (
    LOCAL,
    XFM_DIMS,
    build_library,
    build_xfm_library,
    clear_thread_caps,
    use_site,
)

pytest.importorskip("klayout.db")

OD_P, OD_S, W_P, W_S, CS = XFM_DIMS
LATTICE = {OD_P: 20, OD_S: 4, W_P: 1, W_S: 1, CS: 0.5}              # most library rows sit on these multiples
PLAIN = {"Lp_lf": {"min": 0.40e-9, "max": 0.50e-9}, "k_lf": {"min": 0.6}}
ANCHORED = {"Lp_lf": {"min": 0.58e-9, "max": 0.82e-9}, "k@10": {"min": 0.66}}   # implies SRF >= 12.5 GHz
KEYS = ["stratum", "targets", "objective", "grid", "levels", "binding", "edge", "group_by", "trend", "candidates", "candidates_level",
        "measured", "points_sample", "seconds", "notes"]                          # the answer's documented top-level keys, in order


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    return query.Library(build_xfm_library(tmp_path_factory.mktemp("xfmregion")), limits=LOCAL)


def run(lib, targets, objective=None, **kw):
    return region.region(lib, "xfm_demo", targets, objective, **{"pool_size": 2048, "workers": 1, **kw})


def key(params: dict) -> tuple:
    return tuple(round(params[d], 6) for d in XFM_DIMS)


def spy_predict(monkeypatch) -> list[int]:
    """From now on, the rows of every StratumGP.predict call: one per chunk ``suggest._gp_predict`` splits a batch into."""
    calls: list[int] = []
    original = gp.StratumGP.predict

    def predict(self, x):
        calls.append(len(x))
        return original(self, x)

    monkeypatch.setattr(gp.StratumGP, "predict", predict)
    return calls


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
    budget = 8 * s.PREDICT_COPIES * max(len(m.rows) for m in models.values()) * 100     # 100 rows per call for the largest model
    calls = spy_predict(monkeypatch)
    chunked_ok, chunked = s.predict_all(x, models, chunk_bytes=budget)                  # a grid is predicted in chunks: the same answer
    assert calls and max(calls) <= s.rows_per_call(budget, min(len(m.rows) for m in models.values())) and len(calls) > len(models)
    whole_ok, whole = s.predict_all(x, models)
    assert calls[-len(models):] == [len(x)] * len(models)                             # no budget: one call per model
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
    assert list(r) == KEYS
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
    lib = query.Library(build_library(tmp_path_factory.mktemp("indregion")), limits=LOCAL)
    r = region.region(lib, "ind_demo", {"Lp_lf": {"min": 0.8e-9, "max": 1.6e-9}}, pool_size=512, workers=1, sample_size=10**6,
                      steps={"outer_diameter_um": 5, "width_um": 0.5, "spacing_um": 0.5}, group_by=["turns"])
    assert set(r["grid"]["steps"]) == {"outer_diameter_um", "width_um", "spacing_um"} and r["grid"]["bracket"]["turns"] == [1.0, 2.0]
    assert [row["key"] for row in r["group_by"]["rows"]] == [{"turns": 1.0}, {"turns": 2.0}]
    assert {p["params"]["spacing_um"] for p in r["points_sample"] if p["params"]["turns"] == 1} == {2.0}
    assert r["edge"]["turns"] == {"at_min": True, "at_max": True} and r["measured"]
    with pytest.raises(ValueError, match="turns go by integer level"):
        region.region(lib, "ind_demo", {"Lp_lf": {"min": 0.8e-9, "max": 1.6e-9}}, steps={"turns": 1})


def test_the_block_answers_strict_json_and_parses_the_command_line_spellings(lib, monkeypatch):
    """The core keeps inf (a minimum's open upper end, an SRF above the sweep); the block's answer has null there instead."""
    seen = {}

    def core(library, stratum, targets, objective=None, **kw):
        seen.update(kw)
        return {"targets": [{"quantity": "k_lf", "kind": "min", "value": 0.6, "tol": 0.0, "upper": float("inf")}],
                "candidates": [{"predicted": {"SRF": {"value": 5e10, "lo": 5e10, "hi": float("inf")}}}],
                "trend": {"rows": [(float("-inf"), float("nan"), 0.5)]}}

    monkeypatch.setattr(region, "region", core)
    out = library_blocks.region(lib, "xfm_demo", PLAIN, group_by=f"{W_P}, {W_S}", trend=f"k@10:{CS}", max_points=3e4, workers="2")
    assert out == {"targets": [{"quantity": "k_lf", "kind": "min", "value": 0.6, "tol": 0.0, "upper": None}],
                   "candidates": [{"predicted": {"SRF": {"value": 5e10, "lo": 5e10, "hi": None}}}], "trend": {"rows": [[None, None, 0.5]]}}
    json.dumps(out, allow_nan=False)
    assert (seen["group_by"], seen["trend"], seen["max_points"], seen["workers"], seen["threads"]) == ([W_P, W_S], ("k@10", CS), 30000, 2, None)
    assert seen["rel_sigma_max"] == domain.DEFAULT_SIGMA_REL_MAX
    library_blocks.region(lib, "xfm_demo", PLAIN, rel_sigma_max="0.3")
    assert seen["rel_sigma_max"] == 0.3
    for bad in ("k_lf", "k_lf:", f":{CS}", ["k_lf", CS]):
        with pytest.raises(ValueError, match="expected <quantity>:<dim>"):
            library_blocks.region(lib, "xfm_demo", PLAIN, trend=bad)
    with pytest.raises(ValueError, match="targets: expected a JSON object"):
        library_blocks.region(lib, "xfm_demo", "{Lp_lf: {min: 4e-10}}")                  # the shell ate the quotes


def test_call_lib_region_prints_json(lib, tmp_path, monkeypatch):
    use_site(monkeypatch, tmp_path / "site.yaml", local=LOCAL)
    out = CliRunner().invoke(app, ["call", "lib.region", str(lib.root), "stratum=xfm_demo", f"targets={json.dumps(PLAIN)}",
                                   f"steps={json.dumps({OD_P: 5, OD_S: 5, W_P: 1, W_S: 1, CS: 2})}", f"group_by={W_P},{W_S}",
                                   f"trend=k_lf:{CS}", "objective=max:Qp_peak", "n=2", "pool_size=2048", "workers=1"])
    assert out.exit_code == 0, out.output
    assert "Infinity" not in out.stdout and "NaN" not in out.stdout
    body = json.loads(out.stdout)
    assert list(body) == KEYS and body["stratum"] == "xfm_demo" and body["levels"]["mean"]["count"] > 0 and len(body["candidates"]) == 2
    assert {t["quantity"]: t["upper"] for t in body["targets"]} == {"Lp_lf": 0.5e-9, "k_lf": None}          # null except for a window
    assert body["group_by"]["dims"] == [W_P, W_S] and (body["trend"]["quantity"], body["trend"]["dim"]) == ("k_lf", CS)
    assert body["grid"]["steps"] == {OD_P: 5, OD_S: 5, W_P: 1, W_S: 1, CS: 2}
    bad = CliRunner().invoke(app, ["call", "lib.region", str(lib.root), "stratum=xfm_demo", f"targets={json.dumps(PLAIN)}", "trend=k_lf"])
    assert bad.exit_code == 2 and "expected <quantity>:<dim>" in bad.output


# -- T15.3: the work is sized by the library's limits -------------------------------------------------------------------


def spy_sizing(monkeypatch) -> tuple[list, list]:
    """From now on, region's BLAS limits (limit, user_api) and the chunk budget of every prediction it makes."""
    blas, budgets = [], []
    real_limits, real_predict = region.threadpool_limits, s.predict_all

    def limits(limits=None, user_api=None):
        blas.append((limits, user_api))
        return real_limits(limits=limits, user_api=user_api)

    def predict_all(*args, chunk_bytes=None, **kwargs):
        budgets.append(chunk_bytes)
        return real_predict(*args, chunk_bytes=chunk_bytes, **kwargs)

    monkeypatch.setattr(region, "threadpool_limits", limits)
    monkeypatch.setattr(s, "predict_all", predict_all)
    return blas, budgets


COARSE = {OD_P: 10, OD_S: 10, W_P: 1, W_S: 1, CS: 4}


def test_region_takes_blas_threads_and_prediction_chunks_from_the_limits(lib, monkeypatch):
    """BLAS: max_threads, or the explicit ``threads`` within it, never above OMP_NUM_THREADS; every prediction in chunks of
    PREDICT_MEMORY_SHARE of max_memory_gb."""
    blas, budgets = spy_sizing(monkeypatch)
    clear_thread_caps(monkeypatch)
    six = HostLimits(max_threads=6, max_memory_gb=3)
    run(query.Library(lib.root, limits=six), PLAIN, steps=COARSE, n=1)
    assert blas == [(6, "blas")] and budgets and set(budgets) == {s.predict_budget(six)} == {int(0.3 * 1024**3)}
    run(query.Library(lib.root, limits=six), PLAIN, steps=COARSE, n=1, threads=4)
    monkeypatch.setenv("OMP_NUM_THREADS", "2")
    run(query.Library(lib.root, limits=six), PLAIN, steps=COARSE, n=1)
    run(query.Library(lib.root, limits=six), PLAIN, steps=COARSE, n=1, threads=4)
    assert [limit for limit, _ in blas] == [6, 4, 2, 2]
    with pytest.raises(ValueError, match="threads=7 exceeds max_threads 6"):
        run(query.Library(lib.root, limits=six), PLAIN, threads=7)


def test_region_refuses_workers_above_the_limits_before_fitting(lib, tmp_path):
    root = shutil.copytree(lib.root, tmp_path / "lib", ignore=shutil.ignore_patterns(".cache"))
    with pytest.raises(ValueError, match=r"workers=2 exceeds 1: max_threads 2 of this machine \(site.yaml hosts.local\) // 2"):
        region.region(query.Library(root, limits=LOCAL), "xfm_demo", PLAIN, pool_size=256, workers=2)
    assert not list((root / ".cache").glob("model-*")) and not list((root / ".cache").glob("calibration-*"))


def test_the_command_line_sizes_the_library_from_the_site_files_local_entry(lib, tmp_path, monkeypatch):
    """`ic-opt call` on a library root: the computation takes hosts.local of the site file the command line reads -- never
    another host's entry; without a local entry, a region is refused and a measured row still answers."""
    blas, budgets = spy_sizing(monkeypatch)
    clear_thread_caps(monkeypatch)
    laptop = HostLimits(max_threads=3, max_memory_gb=5)
    use_site(monkeypatch, tmp_path / "site.yaml", local=laptop, lab=FAKE_HOST)
    args = ["call", "lib.region", str(lib.root), "stratum=xfm_demo", f"targets={json.dumps(PLAIN)}", f"steps={json.dumps(COARSE)}",
            "n=1", "pool_size=256", "workers=1"]
    out = CliRunner().invoke(app, args)
    assert out.exit_code == 0, out.output
    assert blas == [(3, "blas")] and budgets and set(budgets) == {s.predict_budget(laptop)}
    use_site(monkeypatch, tmp_path / "lab.yaml", lab=FAKE_HOST)
    refused = CliRunner().invoke(app, args)
    assert refused.exit_code == 2 and "has no entry for host 'local' (known: lab)" in refused.output, refused.output
    row = lib.dataset("xfm_demo").rows[0]
    measured = CliRunner().invoke(app, ["call", "lib.query", str(lib.root), "stratum=xfm_demo", f"params={json.dumps(row.coords)}"])
    assert measured.exit_code == 0 and json.loads(measured.output)["measured"]["obs_id"] == row.obs_id, measured.output


@pytest.mark.parametrize("block", [library_blocks.query, library_blocks.suggest, library_blocks.region])
def test_the_confidence_ceiling_is_a_block_parameter(block):
    assert inspect.signature(block).parameters["rel_sigma_max"].default == domain.DEFAULT_SIGMA_REL_MAX
