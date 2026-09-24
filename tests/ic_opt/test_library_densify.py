"""T16.1: lib.densify -- where to simulate next by model uncertainty. The selection on known functions (picks in the gaps;
the exact update against a refit with the hyperparameters frozen, and qualitatively against a normal refit), the model's
raw sigma and posterior covariance, the pool and the turns levels on the synthetic libraries, the top cap and the memory
budget, and the block the command line calls, whose answer lib_signoff takes as its candidates."""
from __future__ import annotations

import inspect
import json

import numpy as np
import pytest
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel
from typer.testing import CliRunner

from ic_opt.blocks import library as library_blocks
from ic_opt.cli import app
from ic_opt.library import densify, domain, gp, query, suggest
from ic_opt.recipe import PLAN_MODE, load_recipe
from ic_opt.recipes import lib_signoff
from ic_opt.site import EnvelopeError, HostLimits
from tests.ic_opt.library_fixtures import (
    LOCAL,
    XFM_DIMS,
    build_library,
    build_xfm_library,
    use_site,
)
from tests.ic_opt.test_library_signoff import make_run

pytest.importorskip("klayout.db")

XFM_QUANTITIES = ["Lp_lf", "k_lf", "Qs_peak"]
OD_P, OD_S, W_P, W_S, CS = XFM_DIMS
KEYS = ["stratum", "quantities", "n", "bounds", "pool", "before", "after", "candidates", "method", "seconds", "notes"]
CANDIDATE_KEYS = ["params", "score", "rel_sigma", "rel_sigma_at_pick", "predicted", "nearest"]


@pytest.fixture(scope="module")
def xfm(tmp_path_factory):
    return query.Library(build_xfm_library(tmp_path_factory.mktemp("xfmdensify")), limits=LOCAL)


@pytest.fixture(scope="module")
def ind(tmp_path_factory):
    return query.Library(build_library(tmp_path_factory.mktemp("inddensify")), limits=LOCAL)


# -- known functions --------------------------------------------------------------------------------------------------

def gap_1d() -> tuple[gp.StratumGP, np.ndarray, np.ndarray]:
    """A smooth positive function sampled on [0, 0.35] and [0.65, 1]: (model, training rows, pool without them)."""
    t = np.r_[np.linspace(0, 0.35, 8), np.linspace(0.65, 1, 8)][:, None]
    model = gp.StratumGP(dims=["t"], ranges={"t": (0.0, 1.0)}, log_target=True, kernel="matern52").fit(t, np.exp(0.5 * np.sin(12 * t[:, 0])))
    grid = np.linspace(0, 1, 201)[:, None]
    return model, t, grid[~np.isin(np.round(grid[:, 0], 9), np.round(t[:, 0], 9))]


def f_2d(z: np.ndarray) -> np.ndarray:
    return np.exp(0.5 * np.sin(6 * z[:, 0]) * np.cos(5 * z[:, 1]))


def hole_2d() -> tuple[gp.StratumGP, np.ndarray, np.ndarray]:
    """A 9 x 9 grid on the unit square without its central 3 x 3 block (a in (0.3, 0.7) and b in (0.3, 0.7))."""
    g = np.linspace(0, 1, 9)
    x = np.array([(a, b) for a in g for b in g if not (0.3 < a < 0.7 and 0.3 < b < 0.7)])
    model = gp.StratumGP(dims=["a", "b"], ranges={"a": (0.0, 1.0), "b": (0.0, 1.0)}, log_target=True, kernel="matern52").fit(x, f_2d(x))
    fine = np.linspace(0, 1, 33)
    pool = np.array([(a, b) for a in fine for b in fine])
    return model, x, pool[~np.array([np.isclose(x, p).all(axis=1).any() for p in pool])]


def frozen_refit_sigma(model: gp.StratumGP, x_train: np.ndarray, x_new: np.ndarray, x_eval: np.ndarray, *, jitter_new: bool = False) -> np.ndarray:
    """The posterior sigma (fitted space) of the model's GP refitted on ``x_train`` + ``x_new`` with its hyperparameters frozen,
    in the model's own units: the fitted kernel times the normalisation variance (``normalize_y`` would rescale the amplitude
    with the new targets), scikit-learn's jitter ``alpha`` on the rows the model was fitted on and, unless ``jitter_new``, not on
    the new ones -- the update conditions on them with the kernel's own white noise. The targets are zeros: a GP's posterior
    variance never depends on them."""
    reg = model._gp
    scale = float(reg._y_train_std) ** 2
    rows = np.vstack([x_train, x_new])
    alpha = np.r_[np.full(len(x_train), reg.alpha * scale), np.full(len(x_new), reg.alpha * scale if jitter_new else 0.0)]
    ref = GaussianProcessRegressor(kernel=ConstantKernel(scale, "fixed") * reg.kernel_, optimizer=None, alpha=alpha)
    ref.fit(model._scale(rows), np.zeros(len(rows)))
    return ref.predict(model._scale(x_eval), return_std=True)[1]


def test_picks_land_in_the_gaps_between_training_points():
    model, _t, pool = gap_1d()
    sel = densify.select(pool, {"y": model}, 3, top=len(pool))
    picked = pool[sel.picks, 0]
    assert len(set(picked)) == 3 and ((picked > 0.35) & (picked < 0.65)).all(), picked
    assert sel.score == sorted(sel.score, reverse=True) and sel.score[0] == pytest.approx(np.nanmax(sel.before["y"]))
    assert 0.45 <= picked[0] <= 0.55                                       # the middle of the gap first, then either side
    assert np.nanmax(sel.after["y"]) < 0.2 * np.nanmax(sel.before["y"])
    model2, _x, pool2 = hole_2d()
    sel2 = densify.select(pool2, {"y": model2}, 4, top=len(pool2))
    picked2 = pool2[sel2.picks]
    assert len({tuple(p) for p in picked2}) == 4 and ((picked2 > 0.25) & (picked2 < 0.75)).all(), picked2


@pytest.mark.parametrize("case", [gap_1d, hole_2d], ids=["1d", "2d"])
def test_the_after_sigma_is_what_a_refit_with_frozen_hyperparameters_gives(case):
    """(b) GP posterior variances do not depend on the measured values: with the hyperparameters fixed, sigma after measuring
    the picks is exactly the greedy update's. The reference first reproduces the model itself, so the comparison means
    something; it is well conditioned (length scales of a fraction of the box), so both agree to rounding."""
    model, x_train, pool = case()
    sel = densify.select(pool, {"y": model}, 4, top=len(pool))
    mu, sigma = model.predict(pool, floor=False)
    np.testing.assert_allclose(frozen_refit_sigma(model, x_train, pool[:0], pool), sigma / mu, rtol=1e-8)
    np.testing.assert_allclose(sel.before["y"], sigma / mu, rtol=1e-12)
    rest = np.setdiff1d(np.arange(len(pool)), sel.picks)
    ref = frozen_refit_sigma(model, x_train, pool[sel.picks], pool)
    np.testing.assert_allclose(sel.after["y"][rest], ref[rest], rtol=1e-6)
    assert (sel.after["y"][sel.picks] <= 1e-3 * sel.before["y"][sel.picks]).all()              # measured: nothing left
    jittered = frozen_refit_sigma(model, x_train, pool[sel.picks], pool, jitter_new=True)       # scikit-learn's own refit
    np.testing.assert_allclose(sel.after["y"][rest], jittered[rest], rtol=1e-5)                 # its 1e-10 jitter shows next to a pick


def test_a_normal_refit_lowers_sigma_as_the_estimate_says():
    """(b) With the hyperparameters optimised again on the augmented rows (what the library does after sign-off) the
    reduction is not exact but the same in kind: the refit's p90 and max fall to the estimate's order."""
    model, x_train, pool = hole_2d()
    sel = densify.select(pool, {"y": model}, 6, top=len(pool))
    rows = np.vstack([x_train, pool[sel.picks]])
    refit = gp.StratumGP(dims=["a", "b"], ranges={"a": (0.0, 1.0), "b": (0.0, 1.0)}, log_target=True, kernel="matern52").fit(rows, f_2d(rows))
    mu, sigma = refit.predict(pool, floor=False)
    before, estimate, real = sel.before["y"], sel.after["y"], sigma / mu
    for stat in (lambda v: np.quantile(v, 0.9), np.max):
        assert stat(real) < 0.6 * stat(before) and stat(estimate) < 0.6 * stat(before)
        assert 0.5 < stat(real) / stat(estimate) < 2.0


def two_gaps() -> tuple[dict[str, gp.StratumGP], np.ndarray]:
    """Two quantities over one dim: "a" measured everywhere but (0.2, 0.4), "b" everywhere but (0.55, 0.95) -- the wider
    gap, so b's raw sigma there is the larger one."""
    grid = np.linspace(0, 1, 41)[:, None]

    def fit(gap: tuple[float, float], f) -> gp.StratumGP:
        t = grid[(grid[:, 0] <= gap[0]) | (grid[:, 0] >= gap[1])]
        return gp.StratumGP(dims=["t"], ranges={"t": (0.0, 1.0)}, log_target=True, kernel="matern52").fit(t, f(t[:, 0]))

    models = {"a": fit((0.2, 0.4), lambda t: np.exp(0.4 * np.sin(9 * t))), "b": fit((0.55, 0.95), lambda t: np.exp(0.4 * np.cos(7 * t)))}
    fine = np.linspace(0, 1, 401)[:, None]
    return models, fine[~np.isin(np.round(fine[:, 0], 9), np.round(grid[:, 0], 9))]


def test_the_two_scores_rank_a_constructed_case_differently():
    """``typical`` divides by each quantity's held-out median error, ``ceiling`` by the confidence ceiling. Quantity a has a
    tiny typical error: under ``typical`` its gap leads although b is the less certain one; under ``ceiling`` b's gap does."""
    models, pool = two_gaps()
    raw = {q: np.divide(*m.predict(pool, floor=False)[::-1]) for q, m in models.items()}          # sigma / mu
    assert raw["b"].max() > 2 * raw["a"].max()
    median_rel = {"a": raw["a"].max() / 50, "b": raw["b"].max()}
    typical, ceiling = densify.norms("typical", median_rel, 0.15), densify.norms("ceiling", median_rel, 0.15)
    assert typical == median_rel and ceiling == {"a": 0.15, "b": 0.15}
    assert densify.norms("typical", {"a": None, "b": 0.0}, 0.15) == {"a": 1.0, "b": 1.0}              # no calibration: sigma_rel
    first = {name: pool[densify.select(pool, models, 1, norm=norm, top=len(pool)).picks[0], 0]
             for name, norm in (("typical", typical), ("ceiling", ceiling))}
    assert 0.2 < first["typical"] < 0.4 and 0.55 < first["ceiling"] < 0.95, first
    with pytest.raises(ValueError, match="expected one of"):
        densify.norms("median", median_rel, 0.15)


# -- the model's raw sigma and posterior covariance -----------------------------------------------------------------------

def per_nt_model() -> tuple[gp.StratumGP, np.ndarray]:
    """Turns 1 and 2 with 30 rows each (fitted), turns 3 with 10 (below MIN_NT_SAMPLES: no sub-GP)."""
    rng = np.random.default_rng(0)
    rows = [[rng.uniform(80, 220), rng.uniform(4, 10), 2.0 if nt == 1 else rng.uniform(2, 4), nt] for nt, count in ((1, 30), (2, 30), (3, 10))
            for _ in range(count)]
    x = np.array(rows)
    y = 1e-10 * x[:, 3] ** 2 * (x[:, 0] / 100) ** 1.4 * (6 / x[:, 1]) ** 0.2 * (1 - 0.03 * (x[:, 2] - 2))
    ranges = {"od": (60.0, 240.0), "w": (4.0, 10.0), "s": (2.0, 4.0), "nt": (1.0, 5.0)}
    model = gp.StratumGP(dims=["od", "w", "s", "nt"], ranges=ranges, log_target=True, nt_mode="per_nt", kernel="matern52", nt_dim="nt",
                         sigma_floor_rel=0.05).fit(x, y)
    return model, x


def test_predict_without_the_floor_is_the_gps_own_sigma_and_the_default_is_unchanged():
    model, _x = per_nt_model()
    q = np.array([[150, 6, 2, 1], [150, 6, 3, 2], [200, 9, 3.5, 2], [150, 6, 3, 3]])
    floored, default, raw = model.predict(q, floor=True), model.predict(q), model.predict(q, floor=False)
    for a, b in zip(floored, default):
        np.testing.assert_array_equal(a, b)
    np.testing.assert_array_equal(raw[0], default[0])
    assert (raw[1][:3] < 0.05 * raw[0][:3]).all() and np.allclose(default[1][:3], 0.05 * default[0][:3])   # the floor is flat
    assert np.isnan(raw[1][3]) and "floor" in inspect.signature(gp.StratumGP.predict).parameters


def test_posterior_cov_matches_predict_and_never_couples_turns_levels():
    model, _x = per_nt_model()
    q = np.array([[150, 6, 2, 1], [120, 5, 2, 1], [150, 6, 3, 2], [200, 9, 3.5, 2], [150, 6, 3, 3], [150, 6, 3, 1.5]])
    mu, cov = model.posterior_cov(q)
    pmu, psigma = model.predict(q, floor=False)
    ok = model.available(q)
    assert ok.tolist() == [True, True, True, True, False, False]
    np.testing.assert_allclose(np.exp(mu[ok]), pmu[ok], rtol=1e-12)                        # the fitted space: log
    # a posterior variance is a small difference of large terms; a matrix product and predict's einsum round it apart
    np.testing.assert_allclose(np.sqrt(np.diagonal(cov)[ok]), psigma[ok] / pmu[ok], rtol=1e-6)
    assert (cov[:2, 2:4] == 0).all() and (cov[2:4, :2] == 0).all()                           # turns 1 and 2: separate GPs
    assert cov[0, 1] != 0 and cov[2, 3] != 0 and np.allclose(cov[:4, :4], cov[:4, :4].T)
    assert np.isnan(mu[4:]).all() and np.isnan(cov[4:, :]).all() and np.isnan(cov[:, 4:]).all()
    joint, _y = hole_2d()[:2]
    _mu, jcov = joint.posterior_cov(np.array([[0.5, 0.5], [0.52, 0.5]]))
    assert jcov.shape == (2, 2) and np.isfinite(jcov).all() and jcov[0, 1] > 0


def test_per_nt_picks_stay_on_fitted_levels_and_never_update_across_them():
    """(d) Turns 3 has no sub-GP and 1.5 is no level: never picked. One pick updates its own level only."""
    model, _x = per_nt_model()
    rng = np.random.default_rng(1)
    pool = np.array([[rng.uniform(90, 210), rng.uniform(4.5, 9.5), 2.0 if nt == 1 else rng.uniform(2.1, 3.9), nt]
                     for nt in (1, 1.5, 2, 3) for _ in range(40)])
    sel = densify.select(pool, {"y": model}, 1, top=len(pool))
    (pick,) = sel.picks
    level = pool[pick, 3]
    assert level in (1, 2)
    same, other = pool[:, 3] == level, np.isin(pool[:, 3], [1, 2]) & (pool[:, 3] != level)
    # the other level is untouched: before is predict's sigma, after the covariance's diagonal -- the same variance
    # computed two ways, which round apart where it is a tiny difference of large terms (next to a training row)
    np.testing.assert_allclose(sel.after["y"][other], sel.before["y"][other], rtol=1e-3)
    assert (sel.after["y"][other] <= sel.before["y"][other]).all()
    assert (sel.after["y"][same] < 0.9 * sel.before["y"][same]).sum() > 5
    unfitted = np.isin(pool[:, 3], [1.5, 3])
    assert np.isnan(sel.before["y"][unfitted]).all() and np.isnan(sel.after["y"][unfitted]).all()
    many = densify.select(pool, {"y": model}, 12, top=len(pool))
    assert {pool[i, 3] for i in many.picks} <= {1.0, 2.0} and len(set(many.picks)) == 12


def test_the_memory_budget_caps_the_covariances():
    """cov_rows: the most rows M with (models - 1 + COV_COPIES) x M^2 + PREDICT_COPIES x M x n_train float64 in the budget."""
    model, _t, pool = gap_1d()
    rows = densify.cov_rows(200_000, 1, 16)

    def held(m: int) -> int:
        return 8 * (densify.COV_COPIES * m * m + suggest.PREDICT_COPIES * m * 16)

    assert held(rows) <= 200_000 < held(rows + 1)
    assert densify.cov_rows(10**9, 4, 1581) < densify.cov_rows(10**9, 1, 1581) and densify.cov_rows(100, 1, 16) == 0
    sel = densify.select(pool, {"y": model}, 3, top=len(pool), budget=200_000, n_train=16)
    assert sel.top == rows < len(pool) and sel.capped and len(sel.picks) == 3
    assert set(sel.picks) <= set(np.argsort(-sel.before["y"], kind="stable")[:rows].tolist())   # among the best `rows` only
    with pytest.raises(EnvelopeError, match="do not fit the prediction budget"):
        densify.select(pool, {"y": model}, 3, top=len(pool), budget=1000, n_train=16)


# -- on the synthetic libraries ---------------------------------------------------------------------------------------

def test_picks_are_distinct_in_domain_off_the_measured_rows_and_on_the_steps(xfm):
    """(c) on the transformer library: every pick is a new geometry every model answers, on the manifest's steps."""
    r = densify.densify(xfm, "xfm_demo", XFM_QUANTITIES, n=6, pool_size=1024, workers=1)
    assert list(r) == KEYS and r["quantities"] == XFM_QUANTITIES and r["n"] == 6 and len(r["candidates"]) == 6
    ds = xfm.dataset("xfm_demo")
    steps = xfm.manifest.strata["xfm_demo"].steps
    x = np.array([[c["params"][d] for d in XFM_DIMS] for c in r["candidates"]])
    assert len({tuple(p) for p in x}) == 6
    assert all(ds.find(c["params"]) is None for c in r["candidates"])
    for q in XFM_QUANTITIES:
        assert xfm.model("xfm_demo", q).guard.inside(x).all()
    for i, d in enumerate(XFM_DIMS):
        assert np.allclose(x[:, i] / steps[d], np.round(x[:, i] / steps[d]), atol=1e-9)
    pool = r["pool"]
    assert r["bounds"] == {} and pool["size"] == 1024 and pool["in_bounds"] == pool["distinct"] <= 1024
    assert 0 < pool["in_domain"] <= pool["distinct"] - pool["measured"] and pool["levels"] == {}
    method = r["method"]
    assert method["top"] == min(4000, pool["in_domain"]) and (method["score"], method["formula"]) == ("ceiling", densify.SCORES["ceiling"])
    assert method["norm"] == {q: domain.DEFAULT_SIGMA_REL_MAX for q in XFM_QUANTITIES}
    for c in r["candidates"]:
        assert list(c) == CANDIDATE_KEYS and len(c["nearest"]) == 3 and c["nearest"][0]["scaled_distance"] > 0
        assert set(c["rel_sigma"]) == set(c["rel_sigma_at_pick"]) == set(c["predicted"]) == set(XFM_QUANTITIES)
        assert max(c["rel_sigma_at_pick"][q] / method["norm"][q] for q in XFM_QUANTITIES) == pytest.approx(c["score"])
        assert all(c["rel_sigma_at_pick"][q] <= c["rel_sigma"][q] * (1 + 1e-9) for q in XFM_QUANTITIES)
        assert c["predicted"]["Lp_lf"]["lo"] < c["predicted"]["Lp_lf"]["value"] < c["predicted"]["Lp_lf"]["hi"]
    scores = [c["score"] for c in r["candidates"]]
    assert scores == sorted(scores, reverse=True)
    for q in XFM_QUANTITIES:
        b, a = r["before"][q], r["after"][q]
        assert b["points"] == a["points"] == pool["in_domain"] and a["rel_sigma"]["p90"] <= b["rel_sigma"]["p90"]
        assert a["rel_sigma"]["max"] <= b["rel_sigma"]["max"] and 0 <= a["above_ceiling_share"] <= b["above_ceiling_share"] <= 1
    assert set(r["seconds"]) == {"models", "pool", "predict", "select", "after", "report", "total"}


def test_per_nt_stratum_picks_within_its_fitted_levels(ind):
    """(d) on the inductor library (turns 1 and 2): every pick sits on one of them, single turns at their one spacing. Every
    column by default; SRF_p above the sweep (small single turns) neither scores nor counts."""
    r = densify.densify(ind, "ind_demo", n=5, pool_size=2048, workers=1)
    assert r["quantities"] == ind.dataset("ind_demo").columns and len(r["candidates"]) == 5
    assert set(r["pool"]["levels"]) == {1, 2} and sum(r["pool"]["levels"].values()) == r["pool"]["in_domain"]
    for c in r["candidates"]:
        p = c["params"]
        assert p["turns"] in (1.0, 2.0) and (p["turns"] == 2.0 or p["spacing_um"] == 2.0)
    above = r["pool"]["in_domain"] - r["before"]["SRF_p"]["points"]
    assert above > 0 and any(note.startswith(f"SRF_p: {above} pool candidates lie above the sweep") for note in r["notes"]), r["notes"]
    assert all(r["before"][q]["points"] == r["pool"]["in_domain"] for q in ("Lp_lf", "Qp_peak", "Lp@10"))


def test_bounds_fix_a_dim_or_clip_a_window_before_the_domain_check(xfm):
    """A fixed value is every pool row's value; a window (either end optional) is clipped to the achieved range. The
    answer echoes the effective bounds and counts the pool they keep; the score and its norm are echoed in ``method``."""
    r = densify.densify(xfm, "xfm_demo", XFM_QUANTITIES, n=4, pool_size=1024, bounds={CS: 0, OD_P: {"min": 100, "max": 999}},
                        score="typical", workers=1)
    assert r["bounds"] == {CS: 0.0, OD_P: {"min": 100.0, "max": 200.0}} and len(r["candidates"]) == 4
    assert all(c["params"][CS] == 0.0 and 100 <= c["params"][OD_P] <= 200 for c in r["candidates"])
    pool = r["pool"]
    assert 0 < pool["in_domain"] <= pool["in_bounds"] - pool["measured"] and pool["in_bounds"] <= pool["distinct"] <= 1024
    method = r["method"]
    assert (method["score"], method["formula"]) == ("typical", densify.SCORES["typical"]) and method["norm"] == method["median_rel"]
    for c in r["candidates"]:
        assert max(c["rel_sigma_at_pick"][q] / method["norm"][q] for q in XFM_QUANTITIES) == pytest.approx(c["score"])
    one_sided = densify.densify(xfm, "xfm_demo", XFM_QUANTITIES, n=2, pool_size=256, bounds={OD_S: {"max": 100}}, workers=1)
    assert one_sided["bounds"] == {OD_S: {"min": 48.0, "max": 100.0}} and all(c["params"][OD_S] <= 100 for c in one_sided["candidates"])


@pytest.mark.parametrize(("bounds", "message"), [
    ({"turns": 1}, r"xfm_demo has no dim 'turns'"),
    ({CS: {"min": 200, "max": 300}}, r"center_spacing_um window \[200, 300\] misses center_spacing_um's achieved range \[0, "),
    ({CS: 500}, r"center_spacing_um=500 lies outside center_spacing_um's achieved range \[0, "),
    ({CS: {"min": 5, "max": 1}}, r"window \[5, 1\] has its min above its max"),
    ({CS: {"lo": 1}}, r'center_spacing_um takes a window \{"min": a, "max": b\}'),
    ({CS: "zero"}, r"numbers only; got 'zero'"),
    ({CS: 0.3}, r"center_spacing_um=0.3 holds no multiple of its manifest step 0.5"),
    ({OD_P: {"min": 100.2, "max": 100.7}}, (r"primary_outer_diameter_um\[100.2, 100.7\] holds no multiple of its manifest step 1 "
                                            r"\(primary_outer_diameter_um's achieved range \[80, 200\]\)")),
])
def test_bounds_that_name_no_dim_or_miss_the_achieved_range_are_refused(xfm, bounds, message):
    with pytest.raises(ValueError, match=message):
        densify.densify(xfm, "xfm_demo", XFM_QUANTITIES, n=1, pool_size=64, bounds=bounds, workers=1)


def test_bounds_keep_turns_levels_and_drop_rows_a_level_holds_outside(ind):
    """Turns fixed at 2: only that level. A spacing window above 2 um: single turns (spacing 2 at every row) drop out."""
    two = densify.densify(ind, "ind_demo", ["Lp_lf"], n=3, pool_size=1024, bounds={"turns": 2}, workers=1)
    assert two["bounds"] == {"turns": 2.0} and set(two["pool"]["levels"]) == {2} and {c["params"]["turns"] for c in two["candidates"]} == {2.0}
    wide = densify.densify(ind, "ind_demo", ["Lp_lf"], n=3, pool_size=1024, bounds={"spacing_um": {"min": 2.5}}, workers=1)
    assert wide["bounds"] == {"spacing_um": {"min": 2.5, "max": 3.0}} and set(wide["pool"]["levels"]) == {2}
    assert wide["pool"]["in_bounds"] < wide["pool"]["distinct"] and any("fell outside the bounds" in note for note in wide["notes"])
    assert all(c["params"]["spacing_um"] >= 2.5 for c in wide["candidates"])
    for bad, message in (({"turns": 1.5}, r"turns=1.5 holds none of the turns levels with rows \[1, 2\]"),
                         ({"turns": 3}, r"turns=3 lies outside turns's achieved range \[1, 2\]")):
        with pytest.raises(ValueError, match=message):
            densify.densify(ind, "ind_demo", ["Lp_lf"], n=1, pool_size=64, bounds=bad, workers=1)


def spy_cov(monkeypatch) -> list[int]:
    """From now on, the rows of every posterior_cov call on a joint model (the xfm library has no turns dim)."""
    calls: list[int] = []
    original = gp.StratumGP.posterior_cov

    def posterior_cov(self, x):
        calls.append(len(x))
        return original(self, x)

    monkeypatch.setattr(gp.StratumGP, "posterior_cov", posterior_cov)
    return calls


def test_top_and_the_memory_budget_are_honoured(xfm, monkeypatch):
    """(e) The covariances are computed among ``top`` candidates; a machine whose prediction budget cannot hold that many
    lowers it, says so, and still picks; one that cannot hold n refuses."""
    xfm.models("xfm_demo", XFM_QUANTITIES, workers=1)                       # fitted and cached: the tiny machines only load
    calls = spy_cov(monkeypatch)
    r = densify.densify(xfm, "xfm_demo", XFM_QUANTITIES, n=3, pool_size=1024, top=50, workers=1)
    assert r["method"]["top"] == r["method"]["top_requested"] == 50 and calls[:3] == [50, 50, 50]
    assert max(calls) <= densify.AFTER_CHUNK + 3 and not any("top lowered" in note for note in r["notes"])
    calls.clear()
    tiny = HostLimits(max_threads=2, max_memory_gb=0.0005)                   # a 52 KiB prediction budget
    small = densify.densify(query.Library(xfm.root, limits=tiny), "xfm_demo", XFM_QUANTITIES, n=3, pool_size=1024, top=50, workers=1)
    budget = int(0.0005 * 0.1 * 1024**3)
    held = densify.cov_rows(budget, 3, max(len(xfm.model("xfm_demo", q).rows) for q in XFM_QUANTITIES))
    assert 3 <= small["method"]["top"] == held < 50 and calls[:3] == [held] * 3 and len(small["candidates"]) == 3
    assert any(note.startswith(f"top lowered from 50 to {held}") for note in small["notes"]), small["notes"]
    with pytest.raises(EnvelopeError, match="raise max_memory_gb or lower n"):
        densify.densify(query.Library(xfm.root, limits=HostLimits(max_threads=2, max_memory_gb=0.00005)), "xfm_demo", XFM_QUANTITIES,
                        n=3, pool_size=1024, workers=1)
    for bad, message in (({"n": 0}, "n must be a positive integer"), ({"n": 5, "top": 4}, "top=4 is below n=5"),
                         ({"n": 2, "quantities": ["Ls_res"]}, "no quantities")):
        with pytest.raises(ValueError, match=message):
            densify.densify(xfm, "xfm_demo", bad.pop("quantities", XFM_QUANTITIES), pool_size=256, **bad)


def test_the_block_answers_strict_json_and_its_file_is_lib_signoffs_candidates(xfm, tmp_path, monkeypatch):
    """(f) Non-finite numbers are null, the command-line spellings parse, and ``out=`` writes what lib_signoff reads."""
    seen = {}
    real = densify.densify

    def core(library, stratum, quantities=None, **kw):
        seen.update(kw, quantities=quantities)
        return real(library, stratum, quantities, **kw)

    monkeypatch.setattr(densify, "densify", core)
    out = tmp_path / "picks" / "densify.json"
    answer = library_blocks.densify(xfm, "xfm_demo", "4", quantities="Lp_lf, k_lf", pool_size="1024", top="500", seed="2",
                                    workers="1", out=str(out))
    assert (seen["quantities"], seen["n"], seen["pool_size"], seen["top"], seen["seed"], seen["workers"], seen["threads"]) == \
        (["Lp_lf", "k_lf"], 4, 1024, 500, 2, 1, None)
    assert seen["rel_sigma_max"] == domain.DEFAULT_SIGMA_REL_MAX and seen["k"] == 2.0
    assert (seen["score"], seen["bounds"]) == ("ceiling", None) and answer["bounds"] == {}
    text = out.read_text(encoding="utf-8")
    assert json.loads(text) == answer and "NaN" not in text and "Infinity" not in text
    json.dumps(answer, allow_nan=False)
    wanted = lib_signoff._candidates(out, XFM_DIMS, 10)
    assert wanted == [c["params"] for c in answer["candidates"]] and len(wanted) == 4
    assert inspect.signature(library_blocks.densify).parameters["rel_sigma_max"].default == domain.DEFAULT_SIGMA_REL_MAX
    with pytest.raises(ValueError, match="bounds: expected a JSON object"):
        library_blocks.densify(xfm, "xfm_demo", 1, bounds=f"{CS}=0")                       # the shell ate the quotes
    monkeypatch.setattr(densify, "densify", lambda *a, **k: {"before": {"SRF": {"rel_sigma": {"max": float("nan")}}},
                                                             "candidates": [{"predicted": {"SRF": {"hi": float("inf")}}}]})
    assert library_blocks.densify(xfm, "xfm_demo", 1) == {"before": {"SRF": {"rel_sigma": {"max": None}}},
                                                          "candidates": [{"predicted": {"SRF": {"hi": None}}}]}


def test_call_lib_densify_prints_json_and_writes_the_candidates(xfm, tmp_path, monkeypatch):
    use_site(monkeypatch, tmp_path / "site.yaml", local=LOCAL)
    out = tmp_path / "densify.json"
    result = CliRunner().invoke(app, ["call", "lib.densify", str(xfm.root), "stratum=xfm_demo", "n=2", "quantities=Lp_lf,SRF",
                                      f'bounds={{"{CS}": 0, "{OD_P}": {{"max": 150}}}}', "score=typical", "pool_size=512",
                                      "top=200", "workers=1", f"out={out}"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert list(body) == KEYS and body["quantities"] == ["Lp_lf", "SRF"] and len(body["candidates"]) == 2
    assert body["bounds"] == {CS: 0.0, OD_P: {"min": 80.0, "max": 150.0}} and body["method"]["score"] == "typical"
    assert all(c["params"][CS] == 0 and c["params"][OD_P] <= 150 for c in body["candidates"])
    assert json.loads(out.read_text(encoding="utf-8")) == body
    bad = CliRunner().invoke(app, ["call", "lib.densify", str(xfm.root), "stratum=xfm_demo", "n=2", "quantities=Lp_lf,Ls_res"])
    assert bad.exit_code == 2 and "no quantities ['Ls_res']" in bad.output


def test_lib_signoff_plans_the_densify_candidates(ind, tmp_path, capsys):
    """The workflow of docs/em/library.md 5c: lib.densify -> lib_signoff candidates=<its file> --plan (nothing runs)."""
    out = tmp_path / "densify.json"
    library_blocks.densify(ind, "ind_demo", 3, quantities="Lp_lf,Lp@10", pool_size=1024, workers=1, out=str(out))
    run, ex = make_run(tmp_path, "signoff")
    token = PLAN_MODE.set(True)
    try:
        load_recipe("lib_signoff")(run, library=str(ind.root), candidates=str(out), stratum="ind_demo", top=3)
    finally:
        PLAN_MODE.reset(token)
    assert ex.emx_runs == 0 and run.store.observations() == []
    assert "[plan] lib_signoff: 3 candidates of ind_demo through em_only" in capsys.readouterr().out
