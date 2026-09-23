"""T13.7: transformer strata -- secondary and coupling columns, the dimensionless k maps, query and suggest (synthetic xfm_bs library)."""
from __future__ import annotations

import numpy as np
import pytest

from ic_opt.em import measure
from ic_opt.library import dataset, gp, manifest, query
from ic_opt.library import suggest as s
from tests.ic_opt.library_fixtures import (
    XFM_DIMS,
    build_xfm_library,
    xfm_physics,
    xfm_points,
    xfm_truth,
)

pytest.importorskip("klayout.db")


@pytest.fixture(scope="module")
def xfm_library(tmp_path_factory):
    return build_xfm_library(tmp_path_factory.mktemp("xfmlib"))


def test_dataset_carries_the_secondary_and_coupling_columns(xfm_library):
    ds = query.Library(xfm_library).dataset("xfm_demo")
    assert set(ds.columns) == {"Lp_lf", "Ls_lf", "k_lf", "Qp_peak", "Qs_peak", "SRF_p", "SRF_s", "SRF", "k@10"} and len(ds.rows) == 224
    geometry = xfm_points()[57]
    row, truth = ds.find(dict(zip(XFM_DIMS, geometry))), xfm_truth(geometry)
    for q in ("Lp_lf", "Ls_lf", "k_lf", "Qs_peak"):
        assert row.values[q] == pytest.approx(truth[q], rel=1e-9)
    assert row.values["k_lf"] == pytest.approx(xfm_physics(*geometry)["k"], rel=2e-3)          # the drives (P1, N1), (N2, P2) couple positively
    resonant = [r for r in ds.rows if r.values["SRF_p"] is not None or r.values["SRF_s"] is not None]
    assert resonant and all(r.values["SRF"] == min(v for v in (r.values["SRF_p"], r.values["SRF_s"]) if v is not None) for r in resonant)
    assert all(r.values["SRF"] is None for r in ds.rows if r.values["SRF_p"] is None and r.values["SRF_s"] is None)


def test_anchored_curves_of_a_coupled_pair_stop_below_the_lower_resonance():
    """The other winding's resonance reflects into this drive's impedance: SRF_p may sit far above it, the curve may not."""
    q = measure.Quantities(np.array([0.0]), {}, {"SRF_p": 150e9, "SRF_s": 70e9, "SRF": 70e9})
    assert all(dataset._drive_srf(q, curve) == 70e9 for curve in ("Lp", "Qp", "Ls", "Qs", "k"))
    single = measure.Quantities(np.array([0.0]), {}, {"SRF_p": 90e9, "SRF": 90e9})
    assert dataset._drive_srf(single, "Lp") == 90e9


def test_feature_maps_see_scale_only_in_their_first_feature():
    bs = np.array([xfm_points()[57]])
    f1, f2 = (gp.FEATURE_MAPS["xfm_bs_dimensionless"][1](XFM_DIMS, v) for v in (bs, 2 * bs))
    assert f2[0, 0] == pytest.approx(f1[0, 0] + np.log(2)) and np.allclose(f2[0, 1:], f1[0, 1:])
    ms_dims = list(manifest.XFM_MS_DIMS)
    ms = np.array([[150.0, 120.0, 6.0, 5.0, 10.0, 2.0, 3.0]])                  # ..., secondary_spacing_um, secondary_turns
    doubled = ms * np.array([2, 2, 2, 2, 2, 2, 1])
    g1, g2 = (gp.FEATURE_MAPS["xfm_ms_dimensionless"][1](ms_dims, v) for v in (ms, doubled))
    assert g2[0, 0] == pytest.approx(g1[0, 0] + np.log(2)) and np.allclose(g2[0, 1:], g1[0, 1:]) and g1[0, -1] == 3.0


def test_a_mapped_model_is_one_joint_gp_scaled_into_the_unit_box():
    dims = list(manifest.XFM_MS_DIMS)
    rng = np.random.default_rng(0)
    lo, hi = np.array([60, 60, 4, 4, 0, 2, 2]), np.array([240, 240, 10, 10, 30, 4, 5])
    x = lo + (hi - lo) * rng.random((40, 7))
    x[:, 6] = np.round(x[:, 6])
    model = gp.StratumGP(dims=dims, ranges={d: (float(a), float(b)) for d, a, b in zip(dims, lo, hi)}, log_target=True, nt_mode="per_nt",
                         nt_dim="secondary_turns", kernel="matern52", feature_map="xfm_ms_dimensionless")
    assert model.nt_mode == "joint"                                              # turns is a feature, not a split
    scaled = model._scale(x)
    assert scaled.shape == (40, 7) and scaled.min() >= 0 and scaled.max() <= 1
    model.fit(x, 0.5 + 0.1 * np.log(x[:, 0] / x[:, 1]) ** 2)
    assert model.available(x).all() and np.isfinite(model.predict(x[:3])[0]).all()


def test_manifest_rejects_unknown_maps_and_foreign_dims():
    stratum = {"generator": "clean_port_xfm_bs", "dims": XFM_DIMS, "parts": [{"store": "xfm"}], "quantities": {"k_lf": {"feature_map": "nope"}}}
    with pytest.raises(ValueError, match="unknown feature_map"):
        manifest.Stratum.model_validate(stratum)
    stratum["quantities"] = {"k_lf": {"feature_map": "xfm_ms_dimensionless"}}
    with pytest.raises(ValueError, match="needs the dims"):
        manifest.Stratum.model_validate(stratum)


def test_the_dimensionless_map_wins_on_scale_invariant_coupling(tmp_path):
    mapped = query.Library(build_xfm_library(tmp_path / "mapped")).model("xfm_demo", "k_lf")
    plain = query.Library(build_xfm_library(tmp_path / "plain", feature_map=None)).model("xfm_demo", "k_lf")
    assert mapped.gp.feature_map == "xfm_bs_dimensionless" and plain.gp.feature_map is None
    assert mapped.calibration["median_rel"] < plain.calibration["median_rel"]


def test_query_predicts_coupling_and_both_inductances(xfm_library):
    lib = query.Library(xfm_library)
    geometry = (150.0, 150.0, 5.5, 5.5, 5.0)
    answer = query.query(lib, "xfm_demo", dict(zip(XFM_DIMS, geometry)), ["k_lf", "Lp_lf", "Ls_lf"])
    physics = xfm_physics(*geometry)
    for q, true in (("k_lf", physics["k"]), ("Lp_lf", physics["Lp"]), ("Ls_lf", physics["Ls"])):
        a = answer["quantities"][q]
        assert a["status"] == "predicted" and a["value"] == pytest.approx(true, rel=3e-2) and a["lo"] < a["value"] < a["hi"]


def test_anchored_coupling_targets_imply_the_system_resonance(xfm_library):
    goals = s.parse_targets({"k@10": {"min": 0.5}})
    assert {(t.quantity, t.value) for t in s.implied_srf(goals, None, 1.25)} == {("SRF_p", 12.5e9), ("SRF_s", 12.5e9)}   # per drive: both
    assert {(t.quantity, t.value) for t in s.implied_srf(goals, None, 1.25, ["SRF", "SRF_p"])} == {("SRF", 12.5e9)}
    answer = s.suggest(query.Library(xfm_library), "xfm_demo", {"k@10": {"min": 0.5}}, "max:k_lf", n=1, verify_build=False)
    assert {t["quantity"] for t in answer["targets"]} == {"k@10", "SRF"}


def test_suggest_returns_built_transformers_that_meet_the_targets(xfm_library):
    lib = query.Library(xfm_library)
    answer = s.suggest(lib, "xfm_demo", {"k_lf": {"min": 0.6}, "Lp_lf": {"target": 0.5e-9, "tol": 0.05}}, "max:k_lf", n=2)
    assert answer["candidates"], answer["notes"]
    for c in answer["candidates"]:
        assert c["build"]["built"] and c["predicted"]["k_lf"]["lo"] >= 0.6
        assert 0.475e-9 <= c["predicted"]["Lp_lf"]["lo"] and c["predicted"]["Lp_lf"]["hi"] <= 0.525e-9
        assert xfm_physics(*(c["params"][d] for d in XFM_DIMS))["k"] >= 0.6 * 0.97


def test_a_q_peak_is_searched_below_the_system_resonance():
    """Above the lowest resonance a coupled pair's Q can climb again to the band edge; that is not its quality factor."""
    f = np.arange(0.0, 151e9, 1e9)
    q = np.where(f < 60e9, 20 * np.sin(np.pi * f / 120e9), -5 + 0.2 * (f - 60e9) / 1e9)        # peak 20 at 60 GHz, then 13 at 150 GHz
    assert dataset._peak(f, q, 150e9, 60e9) == pytest.approx(20 * np.sin(np.pi * 59 / 120))
    assert dataset._peak(f, q, 150e9, None) == pytest.approx(20 * np.sin(np.pi * 59 / 120))  # no resonance known: the band alone
    assert dataset._peak(f, q, 40e9, 60e9) == pytest.approx(20 * np.sin(np.pi * 40 / 120))

