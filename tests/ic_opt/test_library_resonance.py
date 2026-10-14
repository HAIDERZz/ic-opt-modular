"""N-113 (``docs/refactor/N113_ABOVE_RESONANCE_SPEC.md``): no curve answer above the device's own resonance.

A measured row has a value for ``<curve>@<f>`` only where its resonance lies above the curve's ``srf_margin`` x f
(``dataset._anchor_value``). ``lib.query`` answers a curve column ``above_resonance``, without a value, where the resonance
predicted there is at or below that threshold, ``out_of_domain`` where the resonance's model refuses the point, and flags
``near_resonance`` where only the resonance's lower bound reaches the threshold; ``suggest.predict_all`` -- the gate of
lib.suggest, lib.region and lib.densify -- leaves such rows out for every anchored column it predicts.

Most tests use the transformer table of the composed-model tests (``library_fixtures.res_physics``) with the curves'
``srf_margin`` at 1.9: at 20 GHz the threshold is 38 GHz, which the large equal windings (140-160 um, 30-37 GHz) do not
reach -- their rows have no Lp@20 or k@20 -- while both curves' own domain guards still take a point between them. The
inductor table (one drive, no system SRF) serves the per-drive resonance SRF_p and a resonance above the sweep."""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
import yaml

from ic_opt.library import composed, densify, gp, manifest, region
from ic_opt.library import query as q
from ic_opt.library import suggest as s
from tests.ic_opt.library_fixtures import (
    LOCAL,
    MAPPED,
    RES_F0_GHZ,
    RES_STRATUM,
    XFM_DIMS,
    build_library,
    build_resonance_library,
    build_xfm_library,
    params,
)
from tests.ic_opt.test_library import DIMS

S, LP, K = RES_STRATUM, "Lp@20", "k@20"
MARGIN = 1.9
THRESHOLD = MARGIN * RES_F0_GHZ * 1e9                         # 38 GHz
LOW = dict(zip(XFM_DIMS, (150.0, 150.0, 6.5, 6.5, 0.0)))       # between the 140 and 160 um windings: about 33 GHz
HIGH = dict(zip(XFM_DIMS, (110.0, 104.0, 6.0, 7.0, 0.0)))      # about 65 GHz
BEYOND = dict(zip(XFM_DIMS, (190.0, 190.0, 6.0, 6.0, 0.0)))    # past the diameters' allowance (172 um): the SRF model refuses it
STEPS = {XFM_DIMS[0]: 2, XFM_DIMS[1]: 2, XFM_DIMS[2]: 0.5, XFM_DIMS[3]: 0.5}
ANSWER = q._answer                                             # a column's answer from its own model, as the library has it


@pytest.fixture(scope="module")
def res(tmp_path_factory) -> q.Library:
    """The resonance table, Lp and Ls composed with ``model: resonance``, k direct, both curves' srf_margin at 1.9; Lp@20,
    k@20 and Lp_lf fitted (with what they need: Lp@20's parts, both curves' resonance SRF)."""
    rule = {"model": "resonance", "feature_map": MAPPED, "srf_margin": MARGIN}
    root = build_resonance_library(tmp_path_factory.mktemp("n113res"), lp=rule, ls=rule, k={"srf_margin": MARGIN},
                                   srf={"feature_map": MAPPED})
    lib = q.Library(root, limits=LOCAL)
    lib.models(S, [LP, K, "Lp_lf"])
    return lib


@pytest.fixture(scope="module")
def ind(tmp_path_factory) -> q.Library:
    return q.Library(build_library(tmp_path_factory.mktemp("n113ind")), limits=LOCAL)


def row(point: dict, dims=XFM_DIMS) -> np.ndarray:
    return np.array([[point[d] for d in dims]], dtype=float)


def given(monkeypatch, answers: dict[str, dict]) -> None:
    """From now on, the resonance columns ``answers`` names answer as given there (a fake answer, so that the threshold can be
    placed exactly); every other column answers from its model."""

    def answer(library, stratum, column, *args):
        return dict(answers[column]) if column in answers else ANSWER(library, stratum, column, *args)

    monkeypatch.setattr(q, "_answer", answer)


def predicted(value: float, lo: float, status: str = "predicted") -> dict:
    """A resonance column's answer (Hz)."""
    return {"status": status, "value": value, "lo": lo, "hi": 2 * value - lo, "unit": "Hz", "nearest": []}


# -- lib.query -----------------------------------------------------------------------------------------------------------


def test_a_curve_at_or_past_the_resonance_predicted_there_is_above_resonance(res):
    """(a) the ``model: resonance`` curve Lp@20 and (d) the direct k@20 between the large windings: the SRF model puts the
    resonance at about 33 GHz, at or below 1.9 x 20 GHz, so neither has a value there -- though the point lies inside both
    curves' own domains, where each model alone answers. The answer names the resonance, the threshold, the frequency and
    the margin, and gives the nearest rows of the curve's own model; the SRF itself answers as before, and a point whose
    resonance lies above the threshold answers as before too, unflagged."""
    x = row(LOW)
    assert isinstance(res.model(S, LP).gp, composed.ComposedGP) and isinstance(res.model(S, K).gp, gp.StratumGP)
    got = q.query(res, S, LOW, [LP, K, "SRF"])["quantities"]
    srf = got["SRF"]
    assert srf["status"] == "predicted" and srf["value"] < THRESHOLD and srf == q._answer(res, S, "SRF", LOW, x, 2.0, None)
    for column, unit in ((LP, "H"), (K, "1")):
        m = res.model(S, column)
        alone = q._answer(res, S, column, LOW, x, 2.0, None)
        assert m.guard.inside(x)[0] and alone["status"] in ("predicted", "uncertain") and alone["value"] > 0, column
        a = got[column]
        assert a["status"] == "above_resonance" and not {"value", "lo", "hi"} & set(a), column
        assert a["srf"] == {"column": "SRF", "value": srf["value"], "lo": srf["lo"], "hi": srf["hi"]}
        assert (a["threshold_hz"], a["frequency_hz"], a["srf_margin"], a["unit"]) == (THRESHOLD, 20e9, MARGIN, unit)
        assert a["reason"] == (f"the resonance predicted here ({srf['value'] / 1e9:.4g} GHz) is at or below srf_margin x f "
                               "(38 GHz): a measured row there has no value for this column")
        assert [(e["part"], e["obs_id"], e["scaled_distance"]) for e in a["nearest"]] == [
            (m.rows[i].part, m.rows[i].obs_id, round(dist, 4)) for i, dist in m.guard.nearest(LOW)]
        assert all(e["value"] == m.rows[i].values[column] for e, (i, _) in zip(a["nearest"], m.guard.nearest(LOW)))
    away = q.query(res, S, HIGH, [LP, K])["quantities"]
    for column, a in away.items():
        assert a == q._answer(res, S, column, HIGH, row(HIGH), 2.0, None) and a["status"] == "predicted", column


def test_a_resonance_its_model_refuses_leaves_the_curve_out_of_domain(res):
    """(f) Past the measured diameters the SRF model refuses the point: the rule cannot be checked, so the curve is out of
    domain with that model's criterion, nearest rows and fill points, its reason prefixed, in the curve's unit."""
    got = q.query(res, S, BEYOND, [LP, K, "SRF"])["quantities"]
    srf = got["SRF"]
    assert srf["status"] == "out_of_domain" and srf["criterion"] == 1
    for column, unit in ((LP, "H"), (K, "1")):
        assert got[column] == {**srf, "reason": f"resonance model: {srf['reason']}", "unit": unit}, column


def test_near_resonance_flags_a_lower_bound_at_or_below_the_threshold(ind, monkeypatch):
    """(b) On the inductor table (Lp@10, its resonance SRF_p, threshold 12.5 GHz), with the resonance's answer given: a value
    above the threshold whose calibrated lower bound reaches it answers as without the rule, flagged ``near_resonance`` --
    predicted or uncertain alike, a lower bound at the threshold included; a lower bound above it flags nothing; a value at
    the threshold is ``above_resonance``."""
    point, column = params(155, 4.5, 2.5, 2), "Lp@10"
    own = q._answer(ind, "ind_demo", column, point, row(point, DIMS), 2.0, None)
    assert own["status"] == "predicted"
    for answer, flag in ((predicted(13e9, 12e9), 12e9), (predicted(13e9, 12.5e9), 12.5e9), (predicted(13e9, 10e9, "uncertain"), 10e9),
                         (predicted(14e9, 13e9), None)):
        given(monkeypatch, {"SRF_p": answer})
        got = q.query(ind, "ind_demo", point, [column])["quantities"][column]
        assert {k: v for k, v in got.items() if k != "near_resonance"} == own, answer
        assert got.get("near_resonance") == (None if flag is None else {"column": "SRF_p", "srf_lo_hz": flag, "threshold_hz": 12.5e9})
    for answer in (predicted(12.5e9, 12e9), predicted(12e9, 9e9, "uncertain")):
        given(monkeypatch, {"SRF_p": answer})
        got = q.query(ind, "ind_demo", point, [column])["quantities"][column]
        assert got["status"] == "above_resonance" and got["srf"] == {"column": "SRF_p", "value": answer["value"], "lo": answer["lo"],
                                                                     "hi": answer["hi"]}
        assert got["reason"].startswith(f"the resonance predicted here (SRF_p {answer['value'] / 1e9:.4g} GHz) is at or below")


def test_a_resonance_above_the_sweep_lets_the_curve_answer(ind):
    """(c) Single turns resonate above the 60 GHz sweep: SRF_p is ``above_sweep`` there, the sweep's end its lower bound. At
    or above srf_margin x f (Lp@10: 12.5 GHz) the curve answers as without the rule; below it (Lp@50: 62.5 GHz) too, as a
    measured row with no resonance in its sweep has the value, flagged ``near_resonance`` with the sweep's end."""
    point = params(135, 5.5, 2, 1)
    x = row(point, DIMS)
    got = q.query(ind, "ind_demo", point, ["SRF_p", "Lp@10", "Lp@50"])["quantities"]
    assert got["SRF_p"]["status"] == "above_sweep" and got["SRF_p"]["lower_bound"] == 60e9
    assert got["Lp@10"] == q._answer(ind, "ind_demo", "Lp@10", point, x, 2.0, None) and got["Lp@10"]["status"] == "predicted"
    near = got["Lp@50"]
    assert near["near_resonance"] == {"column": "SRF_p", "srf_lo_hz": 60e9, "threshold_hz": 62.5e9}
    assert {k: v for k, v in near.items() if k != "near_resonance"} == q._answer(ind, "ind_demo", "Lp@50", point, x, 2.0, None)


def with_quantities(root: Path, drop: tuple[str, ...]) -> q.Library:
    """The transformer table declaring Lp, Ls, Qp, Qs and k at 10 GHz, without the scalars ``drop``."""
    doc = yaml.safe_load((root / "library.yaml").read_text(encoding="utf-8"))
    quantities = doc["strata"]["xfm_demo"]["quantities"]
    for name in drop:
        quantities.pop(name)
    quantities.update({c: {"anchors_ghz": [10]} for c in ("Lp", "Ls", "Qp", "Qs")})
    (root / "library.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return q.Library(root, limits=LOCAL)


def test_the_resonance_is_the_system_srf_else_each_drives_own(ind, tmp_path):
    """(e) The system SRF where the stratum declares it; else per drive -- SRF_p for Lp and Qp, SRF_s for Ls and Qs, both for
    k -- the columns suggest.implied_srf adds; a stratum that declares none of them has no rule for its curves. Only a curve
    column has a resonance, at its own frequency (an extension column too) and with its curve's srf_margin."""
    limit = ind.resonance("ind_demo", "Lp@10")
    assert (limit.columns, limit.frequency_hz, limit.srf_margin, limit.threshold_hz, limit.models) == (("SRF_p",), 10e9, 1.25, 12.5e9, {})
    assert ind.resonance("ind_demo", "Lp_lf") is None and ind.resonance("ind_demo", "SRF_p") is None
    assert ind.resonance("ind_demo", "Lp@33").threshold_hz == 1.25 * 33e9
    root = build_xfm_library(tmp_path / "xfm")
    system = with_quantities(root, ())
    assert {c: system.resonance("xfm_demo", f"{c}@10").columns for c in manifest.CURVES} == dict.fromkeys(manifest.CURVES, ("SRF",))
    drives = with_quantities(root, ("SRF",))
    per_drive = {c: drives.resonance("xfm_demo", f"{c}@10").columns for c in manifest.CURVES}
    assert per_drive == {"Lp": ("SRF_p",), "Qp": ("SRF_p",), "Ls": ("SRF_s",), "Qs": ("SRF_s",), "k": ("SRF_p", "SRF_s")}
    columns = drives.dataset("xfm_demo").columns
    for c, wanted in per_drive.items():
        implied = s.implied_srf(s.parse_targets({f"{c}@10": {"min": 0.0}}), None, 1.25, columns)
        assert tuple(t.quantity for t in implied) == wanted, c
    bare = with_quantities(root, ("SRF_p", "SRF_s"))                     # SRF went with the call before
    assert all(bare.resonance("xfm_demo", f"{c}@10") is None for c in manifest.CURVES)


def test_with_both_drives_resonances_either_one_decides(tmp_path, monkeypatch):
    """(e) k of a stratum without the system SRF, through lib.query: both drives' resonances are asked; one at or below the
    threshold makes k ``above_resonance`` and is named (the lower when both are); one its model refuses makes k
    ``out_of_domain``; the lowest lower bound is the one ``near_resonance`` names; a resonance above the sweep counts as
    above the threshold its sweep's end reaches."""
    lib = with_quantities(build_xfm_library(tmp_path / "xfm"), ("SRF",))
    point = dict(zip(XFM_DIMS, (130.0, 120.0, 5.5, 5.5, 2.0)))
    x = row(point)
    assert set(lib.models("xfm_demo", ["k@10"])["k@10"].resonance.models) == {"SRF_p", "SRF_s"}
    own = q._answer(lib, "xfm_demo", "k@10", point, x, 2.0, None)
    refused = {"status": "out_of_domain", "criterion": 3, "reason": "outside the convex hull at all rows", "unit": "Hz", "nearest": []}
    cases = (({"SRF_p": predicted(50e9, 49e9), "SRF_s": predicted(10e9, 9e9)}, ("above_resonance", "SRF_s")),
             ({"SRF_p": predicted(11e9, 10e9), "SRF_s": predicted(12e9, 11e9)}, ("above_resonance", "SRF_p")),
             ({"SRF_p": predicted(11e9, 10e9), "SRF_s": refused}, ("above_resonance", "SRF_p")),
             ({"SRF_p": predicted(50e9, 49e9), "SRF_s": refused}, ("out_of_domain", None)),
             ({"SRF_p": predicted(13e9, 12e9), "SRF_s": predicted(14e9, 12.2e9)}, ("near", "SRF_p")),
             ({"SRF_p": {"status": "above_sweep", "value": None, "lower_bound": 60e9, "unit": "Hz"}, "SRF_s": predicted(50e9, 49e9)},
              ("answered", None)))
    for answers, (status, named) in cases:
        given(monkeypatch, answers)
        got = q.query(lib, "xfm_demo", point, ["k@10"])["quantities"]["k@10"]
        if status == "above_resonance":
            assert got["status"] == status and got["srf"]["column"] == named, answers
        elif status == "out_of_domain":
            assert got == {**refused, "reason": "resonance model: outside the convex hull at all rows", "unit": "1"}
        elif status == "near":
            assert got["near_resonance"] == {"column": named, "srf_lo_hz": 12e9, "threshold_hz": 12.5e9}
        else:
            assert got == own


# -- Library.models and suggest.predict_all ------------------------------------------------------------------------------------


def test_models_loads_a_curves_resonance_with_it_and_attaches_it(res, tmp_path):
    """A curve's model comes with its resonance's, fitted first when it is not cached, and carries it (``Model.resonance``):
    what ``predict_all`` checks the curve against. Other columns carry none."""
    got = res.models(S, [K, "Lp_lf"])
    limit = got[K].resonance
    assert (limit.columns, limit.threshold_hz) == (("SRF",), THRESHOLD) and limit.models["SRF"] is res.model(S, "SRF")
    assert got["Lp_lf"].resonance is None and res.model(S, "SRF").resonance is None
    fresh = q.Library(shutil.copytree(res.root, tmp_path / "lib", ignore=shutil.ignore_patterns(".cache")), limits=LOCAL)
    assert fresh._model_file(S, "SRF") is None
    fresh.models(S, [K])
    assert fresh._model_file(S, "SRF") is not None and fresh.model(S, K).resonance.models["SRF"] is fresh.model(S, "SRF")


class Fixed:
    """A stand-in model: the given mean per row (in its own unit: GHz for an SRF column), sigma 1 % of it."""

    log_target, k_scale, n_train = True, 1.0, 0

    def __init__(self, mu) -> None:
        self.mu = np.asarray(mu, dtype=float)

    def predict(self, x, *, floor: bool = True) -> tuple[np.ndarray, np.ndarray]:
        return self.mu[: len(x)].copy(), 0.01 * np.abs(self.mu[: len(x)])


def fixed(column: str, mu, resonance: q.Resonance | None = None) -> q.Model:
    return q.Model("s", column, [], Fixed(mu), None, {}, resonance=resonance)


def test_predict_all_leaves_out_the_rows_past_an_anchored_columns_resonance():
    """(g) Whether or not SRF is among the models predicted: a row whose resonance (mean) lies at or below srf_margin x f is
    not ok; k without the system SRF needs both drives' resonances above it; a row settled above the sweep, or where the
    column itself is measured, passes; a resonance with no mean (no model there) leaves the row out."""
    x = np.zeros((5, 1))
    srf_p, srf_s = fixed("SRF_p", [20, 20, 10, 20, np.nan]), fixed("SRF_s", [20, 11, 20, 12.5, 20])        # GHz
    k = fixed("k@10", [0.7] * 5, q.Resonance(("SRF_p", "SRF_s"), 10e9, 1.25, {"SRF_p": srf_p, "SRF_s": srf_s}))
    ok, pred = s.predict_all(x, {"k@10": k}, check_domain=False)
    assert ok.tolist() == [True, False, False, False, False] and list(pred) == ["k@10"]
    assert s.below_resonance(x, {"k@10": k})["k@10"].tolist() == ok.tolist()
    floors = {"SRF_s": np.array([np.nan, 70e9, np.nan, np.nan, np.nan])}                  # row 1: SRF_s settled above the sweep
    exact = {"k@10": np.array([np.nan, np.nan, 0.7, np.nan, np.nan])}                    # row 2: k measured
    ok, _ = s.predict_all(x, {"k@10": k}, check_domain=False, srf_floor=floors, exact=exact)
    assert ok.tolist() == [True, True, True, False, False]
    measured = {"SRF_s": np.array([np.nan, np.nan, np.nan, 13e9, np.nan])}               # row 3: SRF_s measured above it
    assert s.below_resonance(x, {"k@10": k}, exact=measured)["k@10"].tolist() == [True, False, False, True, False]
    plain = fixed("Lp_lf", [1e-9] * 5)
    assert s.predict_all(x, {"Lp_lf": plain}, check_domain=False)[0].all() and s.below_resonance(x, {"Lp_lf": plain}) == {}


def test_a_region_trend_on_an_anchored_column_counts_only_the_points_below_its_resonance(res, monkeypatch):
    """(g) through lib.region: a window on Lp_lf implies no SRF target; the trend k@20 is predicted at every point, and a
    point past its resonance (the large equal windings) is no longer counted -- as a point out of k@20's domain is not. The
    control, the gate switched off, counts them."""
    window = {"Lp_lf": {"min": 0.45e-9, "max": 0.75e-9}}
    options = {"trend": (K, XFM_DIMS[0]), "pool_size": 2048, "workers": 1, "sample_size": 10**6, "steps": STEPS}
    srf = res.model(S, "SRF").gp

    def resonance(answer: dict) -> np.ndarray:
        return srf.predict(np.array([[p["params"][d] for d in XFM_DIMS] for p in answer["points_sample"]]))[0] * 1e9

    gated = region.region(res, S, window, **options)
    assert [t["quantity"] for t in gated["targets"]] == ["Lp_lf"] and gated["levels"]["mean"]["count"] > 0
    assert (resonance(gated) > THRESHOLD).all()
    monkeypatch.setattr(s, "below_resonance", lambda *args, **kwargs: {})
    ungated = region.region(res, S, window, **options)
    assert (resonance(ungated) <= THRESHOLD).any() and ungated["levels"]["mean"]["count"] > gated["levels"]["mean"]["count"]


def test_densify_does_not_score_an_anchored_column_past_its_resonance(res):
    """lib.densify: k@20 neither scores nor counts where the resonance predicted there lies at or below 38 GHz, as an SRF
    does not above the sweep, and a pick there carries no prediction of it; a note says how many such candidates there are."""
    d = densify.densify(res, S, ["Lp_lf", K], n=3, pool_size=2048, top=200, workers=1)
    pool = d["pool"]["in_domain"]
    assert d["before"]["Lp_lf"]["points"] == pool > d["before"][K]["points"] > 0
    beyond = pool - d["before"][K]["points"]
    assert any(note.startswith(f"{K}: {beyond} pool candidates lie where the resonance predicted there is at or below srf_margin "
                               "x f (38 GHz)") for note in d["notes"]), d["notes"]
    srf = res.model(S, "SRF").gp
    for c in d["candidates"]:
        below = srf.predict(row(c["params"]))[0][0] * 1e9 > THRESHOLD
        assert (c["predicted"][K]["value"] is not None) == below and (c["rel_sigma"][K] is not None) == below, c["params"]


def test_answers_the_implied_srf_already_constrained_do_not_change(ind, monkeypatch):
    """(h) An anchored target implies SRF_p >= srf_margin x f on the calibrated interval, so every design it lets through lies
    below the resonance already: lib.suggest's and lib.region's answers -- measured designs, candidates, levels, samples --
    are the same with the gate and without it."""
    ind.column("ind_demo", "Lp@15")
    values = sorted(r.values["Lp@15"] for r in ind.dataset("ind_demo").usable("Lp@15") if r.coords["turns"] == 2)
    targets = {"Lp@15": {"min": values[len(values) // 4], "max": values[-1]}}
    steps = {"outer_diameter_um": 5, "width_um": 0.5, "spacing_um": 0.5}
    keys = ("targets", "levels", "edge", "group_by", "trend", "candidates", "candidates_level", "measured", "points_sample")

    def answers() -> tuple[dict, dict]:
        found = s.suggest(ind, "ind_demo", targets, "max:Qp_peak", n=3, pool_size=512, verify_build=False)
        grid = region.region(ind, "ind_demo", targets, "max:Qp_peak", pool_size=512, workers=1, n=3, sample_size=10**6, steps=steps,
                             trend=("Lp@15", "outer_diameter_um"))
        return found, {key: grid[key] for key in keys}

    gated = answers()
    assert gated[0]["targets"][-1]["quantity"] == "SRF_p" and gated[0]["candidates"] and gated[1]["levels"]["mean"]["count"] > 0
    monkeypatch.setattr(s, "below_resonance", lambda *args, **kwargs: {})
    assert answers() == gated
