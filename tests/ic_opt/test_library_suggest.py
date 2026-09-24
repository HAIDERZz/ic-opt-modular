"""T13.4: lib.suggest on the synthetic library of test_library_query (measured designs first, built predictions next).
T15.3: predictions in chunks sized by bytes, from the library's limits."""
from __future__ import annotations

import json

import numpy as np
import pytest
from typer.testing import CliRunner

from ic_opt.cli import app
from ic_opt.library import query as q
from ic_opt.library import suggest as s
from ic_opt.site import HostLimits
from tests.ic_opt.library_fixtures import (
    LOCAL,
    build_library,
    clear_thread_caps,
    params,
    truth,
    use_site,
)

pytest.importorskip("klayout.db")


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    return build_library(tmp_path_factory.mktemp("slib"))


def test_targets_objective_and_implied_srf():
    goals = s.parse_targets({"Lp_lf": {"target": 1e-9, "tol": 0.05}, "Qp_peak": {"min": 10}, "SRF_p": {"max": 80e9}})
    assert [(t.quantity, t.kind) for t in goals] == [("Lp_lf", "target"), ("Qp_peak", "min"), ("SRF_p", "max")]
    assert goals[0].window() == pytest.approx((0.95e-9, 1.05e-9)) and goals[1].window() == (10, np.inf)
    for bad in ({"Lp_lf": {"target": 1e-9}}, {"Lp_lf": {"target": 1e-9, "tol": 1.5}}):
        with pytest.raises(ValueError):
            s.parse_targets(bad)
    assert [(t.kind, t.window()) for t in s.parse_targets({"Lp_lf": {"min": 1, "max": 2}})] == [("window", (1, 2))]
    assert s.parse_objective("max:Qp_peak") == ("max", "Qp_peak") and s.parse_objective(None) is None
    with pytest.raises(ValueError, match="max:<quantity>"):
        s.parse_objective("best:Qp_peak")
    extra = s.implied_srf(s.parse_targets({"Lp@10": {"target": 1e-9, "tol": 0.05}}), ("max", "Qp@28"), 1.25)
    assert [(t.quantity, t.kind, t.value) for t in extra] == [("SRF_p", "min", pytest.approx(35e9))]
    assert s.implied_srf(s.parse_targets({"Lp@10": {"target": 1e-9, "tol": 0.05}, "SRF_p": {"min": 1e9}}), None, 1.25) == []


def test_score_takes_measured_values_and_srf_floors_as_settled(library):
    lib = q.Library(library, limits=LOCAL)
    models = {name: lib.model("ind_demo", name) for name in ("Lp_lf", "SRF_p")}
    x = np.array([[150.0, 5.0, 3.0, 2], [155.0, 4.5, 2.5, 2]])
    goals = [s.Target("SRF_p", "min", 40e9)]
    exact = {"SRF_p": np.array([50e9, np.nan]), "Lp_lf": np.array([np.nan, np.nan])}
    floors = {"SRF_p": np.array([np.nan, 60e9])}
    out = s.score(x, models, goals, None, exact=exact, srf_floor=floors)
    assert out["pred"]["SRF_p"]["lo"].tolist() == [50e9, 60e9] and out["pred"]["SRF_p"]["hi"][1] == np.inf
    assert out["ok"].tolist() == [True, True] and out["pred"]["SRF_p"]["rel_sigma"].tolist() == [0.0, 0.0]
    strict = s.score(x, models, [s.Target("SRF_p", "max", 55e9)], None, exact=exact, srf_floor=floors)
    assert strict["ok"].tolist() == [True, False]                                     # above the sweep cannot satisfy a maximum


def test_suggest_lists_measured_designs_then_built_predictions(library):
    lib = q.Library(library, limits=LOCAL)
    target = lib.dataset("ind_demo").find(params(150, 5, 3, 2)).values["Lp_lf"]           # a measured design sits inside the window
    r = s.suggest(lib, "ind_demo", {"Lp_lf": {"target": target, "tol": 0.05}}, "max:Qp_peak", n=2, pool_size=512, seed=1)
    assert r["satisfying_measured"] >= 1 and r["satisfying"] > r["satisfying_measured"] and r["notes"] == []
    for m in r["measured"]:
        value = m["predicted"]["Lp_lf"]["value"]
        assert m["measured"]["obs_id"] and m["predicted"]["Lp_lf"]["lo"] == value == m["predicted"]["Lp_lf"]["hi"]
        assert 0.95 * target <= value <= 1.05 * target
    assert len(r["candidates"]) == 2
    qs = [c["predicted"]["Qp_peak"]["lo"] for c in r["candidates"]]
    assert qs == sorted(qs, reverse=True)
    for c in r["candidates"]:
        p = c["predicted"]["Lp_lf"]
        assert c["build"]["built"] and c["build"]["ports"] == ["N1", "P1"] and 0.95 * target <= p["lo"] <= p["hi"] <= 1.05 * target
        g = c["params"]
        assert 0.95 * target <= truth(g["outer_diameter_um"], g["width_um"], g["spacing_um"], int(g["turns"]))["Lp_lf"] <= 1.05 * target
    none = s.suggest(lib, "ind_demo", {"Lp_lf": {"target": 50e-9, "tol": 0.01}}, None, n=2, pool_size=256)
    assert none["satisfying"] == 0 and none["measured"] == [] and none["candidates"] == []
    with pytest.raises(ValueError, match="no quantities"):
        s.suggest(lib, "ind_demo", {"Ls_lf": {"min": 1e-9}}, None)


def test_call_lib_suggest_prints_json(library, tmp_path, monkeypatch):
    use_site(monkeypatch, tmp_path / "site.yaml", local=LOCAL)
    out = CliRunner().invoke(app, ["call", "lib.suggest", str(library), "stratum=ind_demo", 'targets={"Lp_lf":{"target":1.2e-9,"tol":0.05}}',
                                   "objective=max:Qp_peak", "n=1", "pool_size=256"])
    assert out.exit_code == 0, out.output
    assert "Infinity" not in out.output and "NaN" not in out.output      # strict JSON: open bounds and non-window uppers are null
    body = json.loads(out.output)
    assert body["stratum"] == "ind_demo" and len(body["candidates"]) <= 1 and "measured" in body


def test_the_prediction_budget_is_a_share_of_the_machines_memory(library, monkeypatch):
    """PREDICT_MEMORY_SHARE (10 %) of max_memory_gb per GP call; rows per call = budget / (8 bytes x training rows x 7). A
    1300-row model on a 16 GB laptop: 1.6 GB -> 23 598 rows per call; on a 256 GB server 377 579. suggest predicts its
    pool within that budget and with the machine's BLAS threads (max_threads, never above OMP_NUM_THREADS)."""
    laptop, server = HostLimits(max_threads=8, max_memory_gb=16), HostLimits(max_threads=128, max_memory_gb=256)
    assert s.predict_budget(laptop) == int(1.6 * 1024**3) and s.predict_budget(server) == int(25.6 * 1024**3)
    assert s.rows_per_call(s.predict_budget(laptop), 1300) == 23598 and s.rows_per_call(s.predict_budget(server), 1300) == 377579
    assert s.rows_per_call(10, 1300) == 1                             # at least one row, however small the budget
    budgets, blas = [], []
    real_predict, real_limits = s.predict_all, s.threadpool_limits

    def predict_all(*args, chunk_bytes=None, **kwargs):
        budgets.append(chunk_bytes)
        return real_predict(*args, chunk_bytes=chunk_bytes, **kwargs)

    def limits(limits=None, user_api=None):
        blas.append((limits, user_api))
        return real_limits(limits=limits, user_api=user_api)

    monkeypatch.setattr(s, "predict_all", predict_all)
    monkeypatch.setattr(s, "threadpool_limits", limits)
    clear_thread_caps(monkeypatch)
    small = HostLimits(max_threads=3, max_memory_gb=0.5)
    targets = {"Lp_lf": {"target": 1.2e-9, "tol": 0.05}}
    s.suggest(q.Library(library, limits=small), "ind_demo", targets, None, n=1, pool_size=256, verify_build=False)
    monkeypatch.setenv("OMP_NUM_THREADS", "1")
    s.suggest(q.Library(library, limits=small), "ind_demo", targets, None, n=1, pool_size=256, verify_build=False)
    assert budgets == [s.predict_budget(small)] * 2 and blas == [(3, "blas"), (1, "blas")]
