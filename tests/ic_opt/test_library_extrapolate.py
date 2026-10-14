"""N-116: answers a little outside the measured range along the outer diameters, labelled. The guard's allowance and its
hull clamp, lib.query's label and widened interval, the switch extrapolate=0, predict_all / lib.suggest / lib.region /
lib.densify proposing designs beyond the range, the N-113 rule on an extrapolated point, the family request's wording and
the command line -- on the fake tables of ``library_fixtures``: ind_ext (outer diameters 60-120 um, widths 4-8 um: the
allowance 9 um on both sides), xfm_demo (primary 80-200 um, secondary 48-250 um), xfm_res (the N-113 resonance table) and
the ind_sym family."""
from __future__ import annotations

import json

import numpy as np
import pytest
from typer.testing import CliRunner

from ic_opt.blocks import library as library_blocks
from ic_opt.cli import app
from ic_opt.library import densify, domain, gp, region, suggest
from ic_opt.library import query as q
from tests.ic_opt.library_fixtures import (
    EXT_STRATUM,
    LOCAL,
    MAPPED,
    RES_STRATUM,
    XFM_DIMS,
    build_extrapolate_library,
    build_family_library,
    build_resonance_library,
    build_xfm_library,
    params,
    use_site,
)
from tests.ic_opt.test_library import DIMS

pytest.importorskip("klayout.db")

S, OD, W = EXT_STRATUM, "outer_diameter_um", "width_um"
OD_P, OD_S = XFM_DIMS[0], XFM_DIMS[1]
F = domain.EXTRAPOLATE_FRACTION
REASON_40 = ("outer_diameter_um=40 outside the measured range [60, 120]: 20 below it, beyond the allowance of 9 there "
             "(extrapolate=0.15 x min(range, boundary))")


def label(side: str, beyond: float, allowance: float, lo: float = 60.0, hi: float = 120.0) -> dict:
    return {"side": side, "beyond": beyond, "allowance": allowance, "measured_range": [lo, hi]}


def row(point: dict, dims=DIMS) -> np.ndarray:
    return np.array([[point[d] for d in dims]], dtype=float)


def labels_in(value) -> bool:
    """Whether an answer holds an ``extrapolated`` key or a ``widening`` anywhere."""
    if isinstance(value, dict):
        return "extrapolated" in value or "widening" in value or any(labels_in(v) for v in value.values())
    if isinstance(value, list):
        return any(labels_in(v) for v in value)
    return False


@pytest.fixture(scope="module")
def ext(tmp_path_factory) -> q.Library:
    lib = q.Library(build_extrapolate_library(tmp_path_factory.mktemp("n116ext")), limits=LOCAL)
    lib.models(S, ["Lp_lf", "Qp_peak", "SRF_p", "Lp@10"])
    return lib


@pytest.fixture(scope="module")
def xfm(tmp_path_factory) -> q.Library:
    return q.Library(build_xfm_library(tmp_path_factory.mktemp("n116xfm")), limits=LOCAL)


@pytest.fixture(scope="module")
def res(tmp_path_factory) -> q.Library:
    """test_library_resonance's table: Lp@20 composed (model: resonance), k@20 direct, both with srf_margin 1.9 (38 GHz)."""
    rule = {"model": "resonance", "feature_map": MAPPED, "srf_margin": 1.9}
    root = build_resonance_library(tmp_path_factory.mktemp("n116res"), lp=rule, ls=rule, k={"srf_margin": 1.9}, srf={"feature_map": MAPPED})
    return q.Library(root, limits=LOCAL)


# -- the guard ---------------------------------------------------------------------------------------------------------------

def test_the_guard_extrapolates_only_the_outer_diameters_by_their_allowance():
    """min(F x range, F x boundary) per side; inside it the verdict carries the label and the depth (in allowances at
    0.15), beyond it or along another dim the refusal says how far; check, inside and depth agree; 0 is the guard as it was."""
    x = np.array([[od, w, s, 2] for od in range(60, 121, 10) for w in (4.0, 6.0, 8.0) for s in (2.0, 3.0)], dtype=float)
    guard = domain.DomainGuard(x, DIMS, {d: (float(x[:, i].min()) - 1, float(x[:, i].max()) + 1) for i, d in enumerate(DIMS)},
                               nt_dim="turns")
    assert guard.allowance() == {OD: (9.0, 9.0)} and guard.allowance(0.3) == {OD: (18.0, 18.0)} and guard.allowance(0) == {OD: (0.0, 0.0)}
    v = guard.check(params(55, 6, 2, 2), extrapolate=F)
    assert v.extrapolated == {OD: label("below", 5.0, 9.0)} and v.depth == pytest.approx(5 / 9)
    assert guard.check(params(129, 6, 2, 2), extrapolate=F).extrapolated == {OD: label("above", 9.0, 9.0)}
    inside = guard.check(params(90, 6, 2, 2), extrapolate=F)
    assert inside.extrapolated == {} and inside.depth == 0
    with pytest.raises(domain.OutOfDomainError) as far:
        guard.check(params(40, 6, 2, 2), extrapolate=F)
    assert far.value.criterion == 1 and far.value.reason == REASON_40 and far.value.fill_points[1][OD] == 51.0
    with pytest.raises(domain.OutOfDomainError) as thin:
        guard.check(params(90, 3, 2, 2), extrapolate=F)
    assert thin.value.reason == "width_um=3 outside the measured range [4, 8]: 1 below it; only outer_diameter_um is extrapolated"
    for point, reason, fill in ((params(55, 6, 2, 2), "outer_diameter_um=55 outside the measured range [60, 120]", 60.0),
                                (params(90, 3, 2, 2), "width_um=3 outside the measured range [4, 8]", 90.0)):
        with pytest.raises(domain.OutOfDomainError) as strict:                  # the default of the guard itself: 0, as before
            guard.check(point)
        assert (strict.value.criterion, strict.value.reason, strict.value.fill_points[1][OD]) == (1, reason, fill)
    assert guard.check(params(129.5, 6, 2, 2), extrapolate=0.3).extrapolated == {OD: label("above", 9.5, 18.0)}
    rng = np.random.default_rng(1)
    pts = np.column_stack([rng.uniform(35, 145, 400), rng.uniform(3, 9, 400), rng.uniform(1.5, 3.5, 400), np.full(400, 2.0)])
    for fraction in (0.0, F, 0.3):
        one = []
        for p in pts:
            try:
                guard.check(dict(zip(DIMS, p)), extrapolate=fraction)
                one.append(True)
            except domain.OutOfDomainError:
                one.append(False)
        assert one == guard.inside(pts, extrapolate=fraction).tolist(), fraction
    depth = guard.depth(pts)
    assert np.allclose(depth, np.maximum(np.maximum(60 - pts[:, 0], 0), np.maximum(pts[:, 0] - 120, 0)) / 9.0)
    assert guard.inside(pts).tolist() == guard.inside(pts, extrapolate=0).tolist()


def test_a_point_within_the_allowance_is_answered_with_its_label_and_the_widened_interval(ext):
    """55 um (5 below the 60 um rows, the allowance 9): every model answers, status as before, labelled; the interval is the
    calibrated one widened by 1 + a x depth -- Q_peak's a > 0, L_lf's 0 (its interval is the model's own, bit for bit)."""
    point = params(55, 6, 2, 2)
    got = q.query(ext, S, point, ["Lp_lf", "Qp_peak"])["quantities"]
    for column in ("Lp_lf", "Qp_peak"):
        a = got[column]
        assert a["status"] in ("predicted", "uncertain") and a["extrapolated"] == {OD: label("below", 5.0, 9.0)}
        m = ext.model(S, column)
        mu, sigma = m.gp.predict(row(point))
        wide = 1 + q.WIDENING[q.COLUMN_CLASS[column]] * 5 / 9
        lo, hi = gp.prediction_bounds(mu, sigma, log_target=m.gp.log_target, k=2.0 * m.gp.k_scale * wide)
        assert a["widening"] == pytest.approx(wide) and (a["lo"], a["hi"]) == (float(lo[0]), float(hi[0]))
        assert a["value"] == float(mu[0]) and a["rel_sigma"] == float(sigma[0] / mu[0])
    assert got["Qp_peak"]["widening"] == pytest.approx(1 + 3.09 * 5 / 9) and got["Lp_lf"]["widening"] == 1.0
    m = ext.model(S, "Lp_lf")
    lo0, hi0 = m.gp.predict_bounds(row(point), 2.0)
    assert (got["Lp_lf"]["lo"], got["Lp_lf"]["hi"]) == (float(lo0[0]), float(hi0[0]))
    edge = q.query(ext, S, params(129, 6, 2, 2), ["Qp_peak"])["quantities"]["Qp_peak"]
    assert edge["extrapolated"] == {OD: label("above", 9.0, 9.0)} and edge["widening"] == pytest.approx(1 + 3.09)


def test_beyond_the_allowance_or_along_another_dim_the_point_is_refused_saying_how_far(ext):
    far = q.query(ext, S, params(40, 6, 2, 2), ["Lp_lf"])["quantities"]["Lp_lf"]
    assert far["status"] == "out_of_domain" and far["criterion"] == 1 and far["reason"] == REASON_40
    assert far["fill_points"] == [params(40.0, 6.0, 2.0, 2.0), params(51.0, 6.0, 2.0, 2.0)]   # onto the box the guard accepts
    thin = q.query(ext, S, params(90, 3, 2, 2), ["Lp_lf"])["quantities"]["Lp_lf"]
    assert thin["status"] == "out_of_domain" and thin["criterion"] == 1
    assert thin["reason"] == "width_um=3 outside the measured range [4, 8]: 1 below it; only outer_diameter_um is extrapolated"
    both = q.query(ext, S, params(55, 3, 2, 2), ["Lp_lf"])["quantities"]["Lp_lf"]          # the width refuses it, whatever the diameter
    assert both["status"] == "out_of_domain" and both["reason"].startswith("width_um=3 outside the measured range [4, 8]")
    wider = q.query(ext, S, params(45, 6, 2, 2), ["Lp_lf"], extrapolate=0.3)["quantities"]["Lp_lf"]   # another fraction for the call
    assert wider["extrapolated"] == {OD: label("below", 15.0, 18.0)} and wider["widening"] == 1.0
    with pytest.raises(ValueError, match="extrapolate is the fraction"):
        q.query(ext, S, params(55, 6, 2, 2), ["Lp_lf"], extrapolate=-0.1)


def test_a_transformer_below_both_outer_diameters_is_answered_with_both_labelled(xfm):
    """xfm_demo: primary 80-200 (allowance below 12), secondary 48-250 (below: 0.15 x 48 = 7.2); 4 and 2 um below them:
    both dims labelled, the depth the farther of the two (4 / 12), clamped (80, 48) a row of the table."""
    point = dict(zip(XFM_DIMS, (76.0, 46.0, 4.0, 4.0, 0.0)))
    got = q.query(xfm, "xfm_demo", point, ["Lp_lf", "k_lf", "Qp_peak"])["quantities"]
    both = {OD_P: label("below", 4.0, 12.0, 80.0, 200.0), OD_S: label("below", 2.0, 7.2, 48.0, 250.0)}
    for column, a in got.items():
        assert a["status"] in ("predicted", "uncertain") and a["extrapolated"] == both, column
        assert a["widening"] == pytest.approx(1 + q.WIDENING[q.COLUMN_CLASS[column]] * max(4 / 12, 2 / 7.2)), column
    assert got["k_lf"]["widening"] == pytest.approx(1.25)


def test_a_point_whose_clamped_twin_lies_outside_the_hull_is_refused(xfm):
    """(76, 110): the primary within its allowance, the secondary inside its range -- but clamped onto the primary's 80 um
    the device would be a secondary of 1.375 x the primary, outside every row's hull: refused by criterion 3."""
    point = dict(zip(XFM_DIMS, (76.0, 110.0, 4.0, 4.0, 0.0)))
    a = q.query(xfm, "xfm_demo", point, ["Lp_lf"])["quantities"]["Lp_lf"]
    assert a["status"] == "out_of_domain" and a["criterion"] == 3
    assert a["reason"] == "outside the convex hull at all rows (judged with primary_outer_diameter_um=80: clamped onto the measured range)"
    guard = xfm.model("xfm_demo", "Lp_lf").guard
    assert guard.inside(row(point, XFM_DIMS), extrapolate=F).tolist() == [False]
    assert guard.inside(row(dict(zip(XFM_DIMS, (76.0, 46.0, 4.0, 4.0, 0.0))), XFM_DIMS), extrapolate=F).tolist() == [True]


# -- extrapolate=0 ------------------------------------------------------------------------------------------------------------

def test_extrapolate_0_answers_as_before_and_answers_inside_the_range_do_not_change(ext):
    """Inside the range the default and extrapolate=0 answer bit for bit alike (lib.query and predict_all); beyond it
    extrapolate=0 is the refusal of before N-116, field for field: today's reason, the fill point on the achieved box."""
    inside = params(85, 5, 2.5, 2)
    assert q.query(ext, S, inside) == q.query(ext, S, inside, extrapolate=0)
    a = q.query(ext, S, params(55, 6, 2, 2), ["Lp_lf"], extrapolate=0)["quantities"]["Lp_lf"]
    m = ext.model(S, "Lp_lf")
    nearest = [q._evidence(m.rows[i], "Lp_lf", DIMS, d) for i, d in m.guard.nearest(params(55, 6, 2, 2))]
    assert a == {"status": "out_of_domain", "criterion": 1, "reason": "outer_diameter_um=55 outside the measured range [60, 120]",
                 "unit": "H", "nearest": nearest, "fill_points": [params(55.0, 6.0, 2.0, 2.0), params(60.0, 6.0, 2.0, 2.0)]}
    x = np.array([[od, w, 2.5, 2] for od in (55, 70, 95, 118, 125) for w in (4.5, 7.5)], dtype=float)
    models = ext.models(S, ["Lp_lf", "Qp_peak"])
    ok, pred = suggest.predict_all(x, models)
    ok0, pred0 = suggest.predict_all(x, models, extrapolate=0)
    within = (x[:, 0] >= 60) & (x[:, 0] <= 120)
    assert ok.all() and ok0.tolist() == within.tolist()
    for column in models:
        for key in ("value", "lo", "hi", "rel_sigma", "sigma"):
            assert pred[column][key][within].tobytes() == pred0[column][key][within].tobytes(), (column, key)
        assert (pred[column]["depth"] > 0).tolist() == (~within).tolist() and not pred0[column]["depth"].any()
    assert (pred["Qp_peak"]["hi"][~within] > pred0["Qp_peak"]["hi"][~within]).all()                 # widened beyond the range


def test_suggest_and_region_with_extrapolate_0_stay_inside_the_measured_range(ext):
    targets = {"Lp_lf": {"min": 0.9e-9, "max": 1.1e-9}}
    sug = suggest.suggest(ext, S, targets, "max:Qp_peak", n=3, pool_size=256, verify_build=False, extrapolate=0)
    reg = region.region(ext, S, targets, "max:Qp_peak", pool_size=256, levels_per_dim=8, extrapolate=0)
    assert not labels_in(sug) and not labels_in(reg)
    assert all(60 <= c["params"][OD] <= 120 for c in sug["candidates"])
    assert 60 <= reg["levels"]["mean"]["ranges"][OD][0] and reg["levels"]["mean"]["ranges"][OD][1] <= 120
    assert all(60 <= p["params"][OD] <= 120 for p in reg["points_sample"])


# -- recommendations beyond the range --------------------------------------------------------------------------------------

LOW = {"Lp_lf": {"min": 0.66e-9, "max": 0.72e-9}}        # below every row's Lp_lf (0.737 nH at the least): 51-59 um diameters


def test_predict_all_suggest_and_region_propose_designs_beyond_the_range_marked(ext):
    """A window below every measured Lp_lf: only designs beyond the 60 um rows meet it. predict_all lets them in (depth,
    widening), lib.suggest proposes them marked (and nothing with extrapolate=0), lib.region's levels say which part of their
    range lies beyond the measured one, its candidates and sample points are marked."""
    measured = ext.dataset(S).values("Lp_lf")
    assert measured.min() > LOW["Lp_lf"]["max"]
    sug = suggest.suggest(ext, S, LOW, "max:Qp_peak", n=3, pool_size=1024, verify_build=False)
    assert sug["measured"] == [] and sug["candidates"]
    for c in sug["candidates"]:
        assert c["params"][OD] < 60 and set(c["extrapolated"]) == {OD} and c["extrapolated"][OD]["side"] == "below"
        assert c["extrapolated"][OD]["beyond"] == pytest.approx(60 - c["params"][OD]) and c["extrapolated"][OD]["allowance"] == 9.0
        assert c["predicted"]["Qp_peak"]["widening"] == pytest.approx(1 + 3.09 * (60 - c["params"][OD]) / 9)
        assert c["predicted"]["Lp_lf"]["widening"] == 1.0
        assert LOW["Lp_lf"]["min"] <= c["predicted"]["Lp_lf"]["lo"] and c["predicted"]["Lp_lf"]["hi"] <= LOW["Lp_lf"]["max"]
    assert any("beyond the measured range along the outer diameters" in note for note in sug["notes"])
    none = suggest.suggest(ext, S, LOW, "max:Qp_peak", n=3, pool_size=1024, verify_build=False, extrapolate=0)
    assert none["candidates"] == [] and none["satisfying"] == 0
    reg = region.region(ext, S, LOW, "max:Qp_peak", pool_size=1024, levels_per_dim=8)
    robust = reg["levels"]["robust"]
    assert robust["count"] and robust["ranges"][OD][1] < 60 and robust["ranges"][OD][0] >= 51
    assert robust["extrapolated"] == {"count": robust["count"],
                                      "beyond_measured": {OD: {"measured": [60.0, 120.0], "below": [robust["ranges"][OD][0], 60.0]}}}
    assert reg["candidates"] and all(set(c["extrapolated"]) == {OD} for c in reg["candidates"])
    assert reg["points_sample"] and all(p["extrapolated"] is True for p in reg["points_sample"])
    assert reg["grid"]["bracket"][OD][0] >= 51 - 1e-9 and any("extrapolated" in note for note in reg["notes"])
    mean = reg["levels"]["mean"]
    assert reg["edge"][OD]["at_min"] == (mean["ranges"][OD][0] - reg["grid"]["steps"][OD] < 51 - 1e-9)        # against the reach
    x = np.array([[c["params"][d] for d in DIMS] for c in sug["candidates"]] + [[90.0, 6.0, 2.0, 2.0]])
    ok, pred = suggest.predict_all(x, ext.models(S, ["Lp_lf", "Qp_peak"]))
    assert ok.all() and (pred["Lp_lf"]["depth"][:-1] > 0).all() and pred["Lp_lf"]["depth"][-1] == 0


def test_densify_keeps_its_pool_in_the_tables_box(ext):
    """lib.densify's pool stays inside the achieved box; where every model's rows span it (ind_ext's Lp_lf and Qp_peak) the
    default and extrapolate=0 pick alike, unlabelled."""
    kw = {"n": 3, "pool_size": 512, "top": 200, "workers": 1}
    default = densify.densify(ext, S, ["Lp_lf", "Qp_peak"], **kw)
    strict = densify.densify(ext, S, ["Lp_lf", "Qp_peak"], extrapolate=0, **kw)
    assert [c["params"] for c in default["candidates"]] == [c["params"] for c in strict["candidates"]]
    assert all(60 <= c["params"][OD] <= 120 for c in default["candidates"]) and not labels_in(default["candidates"])


# -- the N-113 rule on an extrapolated point -------------------------------------------------------------------------------

def test_an_extrapolated_resonance_decides_the_curve_as_any_other(res):
    """xfm_res (primary 80-160 um, the allowance 12): at 170 / 170 um the system SRF, extrapolated, lies at 27 GHz, below
    srf_margin x f (38 GHz): the curves are above_resonance and their srf carries its label; at 170 / 150 um it lies above the
    threshold: the curves answer, labelled, near_resonance where the SRF's widened lower bound reaches the threshold."""
    beyond = q.query(res, RES_STRATUM, dict(zip(XFM_DIMS, (170.0, 170.0, 6.0, 6.0, 0.0))), ["Lp@20", "k@20", "SRF"])["quantities"]
    srf = beyond["SRF"]
    assert srf["status"] == "predicted" and srf["value"] < 38e9 and srf["extrapolated"] == {OD_P: label("above", 10.0, 12.0, 80.0, 160.0)}
    assert srf["widening"] == pytest.approx(1 + q.WIDENING["SRF"] * 10 / 12)
    for column in ("Lp@20", "k@20"):
        a = beyond[column]
        assert a["status"] == "above_resonance" and "value" not in a
        assert a["srf"] == {"column": "SRF", "value": srf["value"], "lo": srf["lo"], "hi": srf["hi"], "extrapolated": srf["extrapolated"]}
    near = q.query(res, RES_STRATUM, dict(zip(XFM_DIMS, (170.0, 150.0, 6.0, 6.0, 0.0))), ["Lp@20", "k@20", "SRF"])["quantities"]
    assert near["SRF"]["value"] > 38e9 and near["SRF"]["extrapolated"]
    for column in ("Lp@20", "k@20"):
        a = near[column]
        assert a["status"] in ("predicted", "uncertain") and set(a["extrapolated"]) == {OD_P}
        assert (a.get("near_resonance") is not None) == (near["SRF"]["lo"] <= 38e9)
    strict = q.query(res, RES_STRATUM, dict(zip(XFM_DIMS, (170.0, 150.0, 6.0, 6.0, 0.0))), ["Lp@20"], extrapolate=0)["quantities"]["Lp@20"]
    assert strict["status"] == "out_of_domain" and strict["reason"].startswith("resonance model: primary_outer_diameter_um=170 outside")


# -- the family request ---------------------------------------------------------------------------------------------------

def test_a_table_reached_only_by_extrapolation_has_designs_marked_and_without_it_says_its_range(tmp_path_factory):
    """ind_o8 (140-200 um) measures 2.33 nH at the least; 2.2 nH +-3 % lies just below: within its allowance (131 um) it has
    designs, marked, beside ind_o2's measured ones; with extrapolate=0 it has none, and reason (a) without the extrapolation
    clause."""
    lib = q.Library(build_family_library(tmp_path_factory.mktemp("n116family")), limits=LOCAL)
    targets = {"Lp_lf": {"target": 2.2e-9, "tol": 0.03}}
    kw = {"family": "ind_sym", "targets": targets, "objective": "max:Qp_peak", "n": 3, "pool_size": 1024, "verify_build": False}
    answer = library_blocks.suggest(lib, **kw)
    assert "ind_o8" not in answer["unreachable"] and answer["strata"]["ind_o8"]["candidates"]
    for c in answer["strata"]["ind_o8"]["candidates"]:
        assert c["params"][OD] < 140 and c["extrapolated"][OD]["measured_range"] == [140.0, 200.0]
    leaders = answer["leaders"]                                  # measured designs first; ind_o8's, if any made it, marked
    assert [("measured" in e) for e in leaders] == sorted(("measured" in e for e in leaders), reverse=True)
    assert all("extrapolated" in e for e in leaders if e["stratum"] == "ind_o8")
    strict = library_blocks.suggest(lib, **kw, extrapolate=0)
    reason = strict["unreachable"]["ind_o8"]
    assert reason["kind"] == "outside_range" and reason["reason"].endswith(" nH") and "extrapolation" not in reason["reason"]
    assert not labels_in(strict)


# -- the command line ------------------------------------------------------------------------------------------------------

def test_the_command_line_takes_extrapolate(ext, tmp_path, monkeypatch):
    use_site(monkeypatch, tmp_path / "site.yaml", local=LOCAL)
    call = ["call", "lib.query", str(ext.root), f"stratum={S}", f"params={json.dumps(params(55, 6, 2, 2))}", "quantities=Lp_lf"]
    result = CliRunner().invoke(app, call)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["quantities"]["Lp_lf"]["extrapolated"] == {OD: label("below", 5.0, 9.0)}
    off = CliRunner().invoke(app, [*call, "extrapolate=0"])
    assert off.exit_code == 0, off.output
    assert json.loads(off.stdout)["quantities"]["Lp_lf"]["reason"] == "outer_diameter_um=55 outside the measured range [60, 120]"
    bad = CliRunner().invoke(app, [*call, "extrapolate=-1"])
    assert bad.exit_code != 0 and "extrapolate is the fraction" in bad.output
