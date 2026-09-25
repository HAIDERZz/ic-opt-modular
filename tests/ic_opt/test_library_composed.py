"""T16.2b: curve columns composed from the stratum's own models -- ``model: ratio`` (base value x ratio: the low-frequency
value, or for Qp / Qs the peak, N-19) and ``model: resonance`` (low-frequency value x the ideal rise at the predicted SRF x
a residual) -- on a synthetic transformer table whose Lp@20 / Ls@20 rise towards a resonance that is steep across
OD_S / OD_P (library_fixtures.res_physics)."""
from __future__ import annotations

import contextlib
import json
import multiprocessing
import shutil
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pytest
import yaml
from pydantic import ValidationError

from ic_opt import _lock
from ic_opt.library import composed, dataset, densify, domain, gp, manifest, region
from ic_opt.library import query as q
from ic_opt.site import HostLimits
from tests.ic_opt.fakes import FAKE_HOST
from tests.ic_opt.library_fixtures import (
    LOCAL,
    MAPPED,
    RES_OPS,
    RES_STRATUM,
    XFM_DIMS,
    build_resonance_library,
    build_xfm_library,
    clear_thread_caps,
    res_manifest,
)

S, LP, LS, K, QP = RES_STRATUM, "Lp@20", "Ls@20", "k@20", "Qp@20"
RESONANCE = {"model": "resonance", "feature_map": MAPPED}
RATIO = {"model": "ratio", "feature_map": MAPPED}
ONE_THREAD = HostLimits(max_threads=1, max_memory_gb=8)


@pytest.fixture(autouse=True)
def _no_thread_caps(monkeypatch):
    clear_thread_caps(monkeypatch)


@pytest.fixture(scope="module")
def table(tmp_path_factory) -> Path:
    """The whole table, Lp and Ls composed with resonance on the mapped inputs, SRF on the mapped inputs; Lp@20 and Ls@20
    fitted (with their parts and calibrations) into its cache, which the tests copy."""
    root = build_resonance_library(tmp_path_factory.mktemp("res"), lp=RESONANCE, ls=RESONANCE, srf={"feature_map": MAPPED})
    q.Library(root, limits=LOCAL).models(S, [LP, LS])
    return root


def held_out(root: Path, level: float) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """The rows of the whole table at OD_P = ``level`` (x, and the measured Lp@20 / Ls@20 there)."""
    ds = dataset.build(root, S)
    rows = [r for r in ds.usable(LP) if r.coords["primary_outer_diameter_um"] == level]
    return ds.matrix(rows), {c: ds.values(c, rows) for c in (LP, LS)}


def errors(model: q.Model, x: np.ndarray, truth: np.ndarray) -> np.ndarray:
    return np.abs(model.gp.predict(x)[0] - truth) / truth


# -- the manifest ------------------------------------------------------------------------------------------------------

def stratum(**quantities) -> dict:
    return {"generator": "clean_port_xfm_bs", "dims": XFM_DIMS, "parts": [{"store": "xfm"}], "quantities": quantities}


@pytest.mark.parametrize(("quantities", "message"), [
    ({"Lp_lf": {"model": "ratio"}}, "a scalar is always modelled directly"),
    ({"Lp_lf": {}, "SRF": {}, "Qp": {"anchors_ghz": [20], "model": "ratio"}}, r"add \['Qp_peak'\]"),
    ({"Qp_peak": {}, "Qs": {"anchors_ghz": [20], "model": "ratio"}}, r"add \['Qs_peak'\]"),
    ({"k_lf": {}, "SRF": {}, "k": {"anchors_ghz": [20], "model": "resonance"}}, "applies to the inductances"),
    ({"Qp_peak": {}, "SRF": {}, "Qp": {"anchors_ghz": [20], "model": "resonance"}}, "applies to the inductances"),
    ({"SRF": {}, "Lp": {"anchors_ghz": [20], "model": "ratio"}}, r"add \['Lp_lf'\]"),
    ({"Ls_lf": {}, "Ls": {"anchors_ghz": [20], "model": "resonance"}}, r"add \['SRF'\]"),
    ({"Lp_lf": {}, "Lp": {"anchors_ghz": [20], "model": "exponential"}}, "Input should be 'direct', 'ratio' or 'resonance'"),
])
def test_the_manifest_refuses_a_model_without_what_it_is_built_on(quantities, message):
    with pytest.raises(ValidationError, match=message):
        manifest.Stratum.model_validate(stratum(**quantities))


def test_ratio_needs_only_the_low_frequency_scalar_and_resonance_the_srf_too():
    ok = manifest.Stratum.model_validate(stratum(Lp_lf={}, k_lf={}, SRF={}, Lp={"anchors_ghz": [20], "model": "resonance"},
                                                 k={"anchors_ghz": [20], "model": "ratio"}))
    assert ok.quantities["Lp"].model == "resonance" and ok.quantities["k"].model == "ratio"
    assert manifest.Stratum.model_validate(stratum(Ls_lf={}, Ls={"anchors_ghz": [20], "model": "ratio"})).quantities["Ls"].model == "ratio"


def test_a_q_curve_takes_ratio_on_its_peak():
    """N-19: the base of a Q curve's ratio is its peak -- Qp_peak for Qp, Qs_peak for Qs -- the one scalar it needs."""
    ok = manifest.Stratum.model_validate(stratum(Qp_peak={}, Qs_peak={}, Qp={"anchors_ghz": [20], "model": "ratio"},
                                                 Qs={"anchors_ghz": [20], "model": "ratio"}))
    assert ok.quantities["Qp"].model == ok.quantities["Qs"].model == "ratio"


def test_direct_is_the_default_and_keeps_every_cache_key(tmp_path):
    """``model: direct`` is left out of the stratum's dump -- the dataset key, and with it every calibration and model file,
    stays what it was before the option existed -- while another option makes a new key."""
    assert "model" not in manifest.Quantity().model_dump() and "model" not in manifest.Quantity(model="direct").model_dump()
    root = build_xfm_library(tmp_path / "xfm")
    before = dataset.build(root, "xfm_demo").key
    doc = yaml.safe_load((root / "library.yaml").read_text(encoding="utf-8"))
    doc["strata"]["xfm_demo"]["quantities"]["k"]["model"] = "direct"
    (root / "library.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    again = dataset.build(root, "xfm_demo")
    assert again.key == before and again.cache == "hit"
    doc["strata"]["xfm_demo"]["quantities"]["k"]["model"] = "ratio"
    (root / "library.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert dataset.build(root, "xfm_demo").key != before


# -- accuracy between the levels -----------------------------------------------------------------------------------------

def test_resonance_beats_direct_on_a_level_left_out(table, tmp_path):
    """OD_P = 120 left out of the fit, its rows predicted: the direct model interpolates the steep rise across 40 um of OD_P;
    the resonance model gets it from the SRF model on the mapped inputs and fits a smooth rest. Both intervals cover."""
    x, truth = held_out(table, 120)
    gap = [o for o in RES_OPS if o != 120]
    direct = q.Library(build_resonance_library(tmp_path / "direct", ops=gap), limits=LOCAL)
    built = q.Library(build_resonance_library(tmp_path / "built", ops=gap, lp=RESONANCE, ls=RESONANCE, srf={"feature_map": MAPPED}),
                      limits=LOCAL)
    for column in (LP, LS):
        worse, better = errors(direct.model(S, column), x, truth[column]), errors(built.model(S, column), x, truth[column])
        assert isinstance(built.model(S, column).gp, composed.ComposedGP) and isinstance(direct.model(S, column).gp, gp.StratumGP)
        assert np.quantile(better, 0.9) < 0.2 * np.quantile(worse, 0.9) and better.max() < 0.02 < worse.max(), column
        lo, hi = built.model(S, column).gp.predict_bounds(x)
        assert ((truth[column] >= lo) & (truth[column] <= hi)).all(), column


def test_ratio_is_the_low_frequency_model_times_a_ratio_model(tmp_path):
    """k@20 = k_lf x (k@20 / k_lf): the k_lf part is the library's own k_lf model object, shared, and the ratio GP carries the
    curve's feature map."""
    lib = q.Library(build_resonance_library(tmp_path / "lib", k={"model": "ratio"}), limits=LOCAL)
    k20, klf = lib.model(S, K), lib.model(S, "k_lf")
    assert isinstance(k20.gp, composed.ComposedGP) and k20.gp.kind == "ratio" and k20.gp.lf is klf.gp
    assert k20.gp.part.feature_map == MAPPED and k20.calibration["model"] == "ratio"
    x = np.array([[110.0, 104.0, 6.0, 7.0, 0.0], [150.0, 170.0, 5.5, 5.0, 0.0]])
    mu, _ = k20.gp.predict(x)
    np.testing.assert_allclose(mu, klf.gp.predict(x)[0] * k20.gp.part.predict(x)[0], rtol=1e-12)
    ds = lib.dataset(S)
    rows = ds.usable(K)[::7]
    np.testing.assert_allclose(k20.gp.predict(ds.matrix(rows))[0], ds.values(K, rows), rtol=5e-3)     # its own rows, near-exactly


def test_a_q_ratio_is_the_peak_model_times_a_ratio_model(tmp_path):
    """Qp@20 = Qp_peak x (Qp@20 / Qp_peak) (N-19): a Q curve's base is its peak -- the library's own Qp_peak model object,
    shared -- and the prediction adds the two models in log space; lib.query's composition names the peak."""
    lib = q.Library(build_resonance_library(tmp_path / "lib", qp=RATIO, scalars=("Qp_peak",)), limits=LOCAL)
    qp20, peak = lib.model(S, QP), lib.model(S, "Qp_peak")
    assert isinstance(qp20.gp, composed.ComposedGP) and qp20.gp.kind == "ratio" and qp20.gp.lf is peak.gp
    assert qp20.gp.part.feature_map == MAPPED and qp20.calibration["model"] == "ratio"
    x = np.array([[110.0, 104.0, 6.0, 7.0, 0.0], [150.0, 170.0, 5.5, 5.0, 0.0]])
    mu, _ = qp20.gp.predict(x)
    np.testing.assert_allclose(np.log(mu), np.log(peak.gp.predict(x)[0]) + np.log(qp20.gp.part.predict(x)[0]), rtol=1e-12)
    ds = lib.dataset(S)
    rows = ds.usable(QP)[::7]
    np.testing.assert_allclose(qp20.gp.predict(ds.matrix(rows))[0], ds.values(QP, rows), rtol=5e-3)    # its own rows, near-exactly
    answer = q.query(lib, S, dict(zip(XFM_DIMS, x[0])), [QP, "Qp_peak"])["quantities"]
    c = answer[QP]["composition"]
    assert answer[QP]["status"] == "predicted" and set(c) == {"model", "formula", "Qp_peak", "ratio"}
    assert c["model"] == "ratio" and c["formula"] == "Qp_peak x ratio"
    assert c["Qp_peak"] == pytest.approx(answer["Qp_peak"]["value"], rel=1e-12)
    assert c["Qp_peak"] * c["ratio"] == pytest.approx(answer[QP]["value"], rel=1e-9)


def test_q_values_at_or_below_zero_are_left_out_of_the_ratio_and_counted(tmp_path):
    """A log-space model takes no value <= 0: rows of a Q column holding one are left out of the ratio's fit and of its
    calibration, as rows without the curve are, and the calibration counts them. Held out, such a row would have no finite
    |z| and the calibration would come out wrong without a word; left out, the hold-out draws from the other 97 rows and
    the model answers the three rows' geometries from their neighbours."""
    lib = q.Library(build_resonance_library(tmp_path / "lib", qp=RATIO, scalars=("Qp_peak",)), limits=LOCAL)
    rows = lib.dataset(S).rows
    picked = [26, 49, 73]                                             # inside the table: OD_P 100, 120 and 140
    truth = [rows[i].values[QP] for i in picked]
    for i, value in zip(picked, (-2.0, 0.0, -0.5)):
        rows[i].values[QP] = value                                    # this library's dataset in memory: what its fits read
    m = lib.model(S, QP)
    assert m.calibration["dropped_nonpositive"] == 3 and m.calibration["n_scored"] == len(gp.SEEDS) * round(gp.HOLDOUT * 97)
    assert np.isfinite([m.calibration[key] for key in ("k_scale", "median_rel", "coverage_2sigma_before")]).all()
    np.testing.assert_allclose(m.gp.predict(lib.dataset(S).matrix([rows[i] for i in picked]))[0], truth, rtol=0.01)


# -- the library's handling: order, sharing, caches ----------------------------------------------------------------------

def test_models_fits_each_part_once_and_before_the_curves(table, tmp_path, monkeypatch):
    """Lp@20 and Ls@20 share the SRF model: it is fitted once, with the two low-frequency models, before either curve."""
    fitted: list[str] = []
    original = q.Library._fit

    def fit(self, stratum, quantity):
        fitted.append(quantity)
        return original(self, stratum, quantity)

    monkeypatch.setattr(q.Library, "_fit", fit)
    lib = q.Library(shutil.copytree(table, tmp_path / "lib", ignore=shutil.ignore_patterns(".cache")), limits=LOCAL)
    got = lib.models(S, [LP, LS])
    assert list(got) == [LP, LS] and fitted == ["Lp_lf", "SRF", "Ls_lf", LP, LS]
    assert got[LP].gp.srf is got[LS].gp.srf is lib.model(S, "SRF").gp and got[LP].gp.lf is lib.model(S, "Lp_lf").gp
    again = q.Library(lib.root, limits=LOCAL)
    fitted.clear()
    loaded = again.models(S, [LS])
    assert fitted == [] and loaded[LS].gp.srf is again.model(S, "SRF").gp                       # every file loads; parts attached


def test_a_composed_model_file_holds_only_its_own_part(table):
    lib = q.Library(table, limits=LOCAL)
    path = lib._model_file(S, LP)
    assert path is not None and path.name.startswith("model-xfm_res-Lp_at_20-")
    with path.open("rb") as f:
        stored = __import__("pickle").load(f)
    assert isinstance(stored, composed.ComposedGP) and stored.lf is None and stored.srf is None and stored.part is not None
    calibrations = sorted(p.name.rsplit("-", 1)[0] for p in (table / ".cache").glob("calibration-xfm_res-*_at_20-*.json"))
    assert calibrations == ["calibration-xfm_res-Lp_at_20-resonance", "calibration-xfm_res-Ls_at_20-resonance"]


def test_cache_keys_follow_the_option_and_the_parts_keys(table, tmp_path):
    """The composed model's file is keyed by the option (and the settings it implies) and by its parts' own keys: a part
    whose key changes -- here its calibration -- makes the composed file a new one, so the curve is refitted."""
    root = shutil.copytree(table, tmp_path / "lib")
    lib = q.Library(root, limits=LOCAL)
    before = lib._model_file(S, LP)
    calibration = lib.model(S, LP).calibration
    assert before is not None and lib._composed_model_name(S, LP, calibration) == before.name
    part = next((root / ".cache").glob("calibration-xfm_res-Lp_lf-*.json"))
    data = json.loads(part.read_text(encoding="utf-8"))
    part.write_text(json.dumps({**data, "k_scale": data["k_scale"] + 0.5}), encoding="utf-8")
    moved = q.Library(root, limits=LOCAL)
    assert moved._composed_model_name(S, LP, calibration) != before.name and moved._model_file(S, LP) is None
    res_manifest(root, lp={"model": "ratio", "feature_map": MAPPED}, ls=RESONANCE, srf={"feature_map": MAPPED})
    ratio = q.Library(root, limits=LOCAL)
    assert ratio._model_file(S, LP) is None and ratio._composed_calibration(S, LP, compute=False) is None
    assert ratio.model(S, LP).gp.kind == "ratio" and ratio._model_file(S, LP) not in (None, before)


def test_the_parallel_path_matches_one_worker_and_writes_only_to_the_cache(table, tmp_path):
    """Spawned workers: the parts, then the five calibration folds of each curve as jobs of their own, then each curve's
    own part -- the same calibration and the same answers as one worker fitting everything here (one BLAS thread both).
    With cache_dir= every file goes there, the fold jobs' and the final jobs' too, and each calibration file -- composed or
    not -- has the lock file its fit held; the library's own .cache is never made."""
    reference = q.Library(shutil.copytree(table, tmp_path / "one", ignore=shutil.ignore_patterns(".cache")), limits=ONE_THREAD)
    expected = reference.models(S, [LP, LS])
    root, elsewhere = shutil.copytree(table, tmp_path / "many", ignore=shutil.ignore_patterns(".cache")), tmp_path / "elsewhere"
    got = q.Library(root, limits=FAKE_HOST, cache_dir=elsewhere).models(S, [LP, LS], workers=4, threads=4)
    x = reference.dataset(S).matrix()[::9]
    for column in (LP, LS):
        mine, theirs = got[column].calibration, expected[column].calibration
        assert mine.keys() == theirs.keys() and mine["source"] == theirs["source"] == "holdout 5x20%, every part refitted on each fold"
        for key in ("k_scale", "median_rel", "coverage_2sigma_before", "n_scored"):
            assert mine[key] == pytest.approx(theirs[key], rel=1e-9), key
        for a, b in zip(got[column].gp.predict(x), expected[column].gp.predict(x)):
            np.testing.assert_allclose(a, b, rtol=1e-9)
    files = [p.name for p in elsewhere.glob(f"*-{S}-*")]
    stems = sorted({name.rsplit("-", 1)[0] for name in files if not name.endswith(".lock")})
    assert stems == [f"calibration-{S}-{c}" for c in ("Lp_at_20-resonance", "Lp_lf", "Ls_at_20-resonance", "Ls_lf", "SRF")] + \
        [f"dataset-{S}"] + [f"model-{S}-{c}" for c in ("Lp_at_20", "Lp_lf", "Ls_at_20", "Ls_lf", "SRF")]
    calibrations = [n for n in files if n.startswith("calibration-") and n.endswith(".json")]
    assert sorted(n for n in files if n.endswith(".lock")) == sorted(f"{n}.lock" for n in calibrations)
    assert not (root / ".cache").exists()


def test_a_reader_loads_the_owners_composed_models_and_parts_from_its_cache(table, tmp_path, monkeypatch):
    """Opened with another cache directory, the library still reads its own .cache: the parts and the composed curves the
    owner fitted load from there -- nothing is fitted, nothing is written."""
    fits: list[str] = []
    monkeypatch.setattr(gp.StratumGP, "fit", lambda self, x, y: fits.append("gp") or pytest.fail("a part was fitted"))
    monkeypatch.setattr(composed.ComposedGP, "fit", lambda self, *a, **k: fits.append("composed") or pytest.fail("a curve was fitted"))
    elsewhere = tmp_path / "elsewhere"
    lib = q.Library(table, limits=LOCAL, cache_dir=elsewhere)
    got = lib.models(S, [LP, LS])
    assert fits == [] and got[LP].gp.srf is got[LS].gp.srf is lib.model(S, "SRF").gp
    assert not elsewhere.exists() or not any(elsewhere.iterdir())


def test_a_composed_fit_waits_for_the_lock_and_loads_what_the_holder_wrote(table, tmp_path, monkeypatch):
    """N-4's discipline for a composed curve: while another holder has the lock next to the curve's calibration file, a fit
    of that curve waits; when the lock comes free it finds the model the holder wrote and fits nothing."""
    root = shutil.copytree(table, tmp_path / "lib")
    owner = q.Library(root, limits=LOCAL)
    model_file = owner._model_file(S, LP)
    saved = model_file.read_bytes()
    model_file.unlink()
    lock = owner.cache.target(f"{owner._composed_calibration_name(S, LP)}.lock")
    original = _lock.waiting_lock
    entered, waits = threading.Event(), []

    @contextlib.contextmanager
    def spy(path):
        entered.set()
        with original(path) as waited:
            waits.append((Path(path).name, waited))
            yield waited

    monkeypatch.setattr(_lock, "waiting_lock", spy)
    monkeypatch.setattr(composed.ComposedGP, "fit", lambda self, *a, **k: pytest.fail("the curve was fitted again"))
    answer: dict = {}
    with original(lock):                                      # the holder
        waiter = threading.Thread(target=lambda: answer.update(model=q.Library(root, limits=LOCAL).model(S, LP)))
        waiter.start()
        assert entered.wait(timeout=60)
        time.sleep(0.5)                                           # the waiter is polling the lock now
        model_file.write_bytes(saved)                             # what the holder wrote
    waiter.join(timeout=120)
    assert waits == [(lock.name, True)] and isinstance(answer["model"].gp, composed.ComposedGP)


# -- the StratumGP interface -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("column", [LP, K])
def test_posterior_cov_diagonal_is_the_unfloored_variance(table, tmp_path, column):
    root = table if column == LP else build_resonance_library(tmp_path / "ratio", k={"model": "ratio"})
    model = q.Library(root, limits=LOCAL).model(S, column).gp
    x = np.array([[90.0, 95.0, 5.0, 6.0, 0.0], [130.0, 118.0, 7.0, 5.5, 0.0], [150.0, 165.0, 8.0, 8.0, 0.0]])
    m, c = model.posterior_cov(x)
    mu, sigma = model.predict(x, floor=False)
    # a posterior variance is a small difference of large terms; a matrix product and predict's einsum round it apart
    np.testing.assert_allclose(np.sqrt(np.diagonal(c)), sigma / mu, rtol=1e-6)
    np.testing.assert_allclose(np.exp(m), mu, rtol=1e-12)
    assert np.allclose(c, c.T) and model.available(x).all() and model.log_target
    floored = model.predict(x)[1]
    assert (floored >= sigma).all() and (floored >= model.sigma_floor_rel * mu * (1 - 1e-12)).all()


def test_the_query_answer_shows_the_composition(table):
    """A composed curve's answer carries where its value came from: the low-frequency value, the SRF (in Hz, as the SRF
    column answers it), the ideal rise at that SRF and the residual, whose product is the value. A measured point has none."""
    lib = q.Library(table, limits=LOCAL)
    params = dict(zip(XFM_DIMS, (110.0, 104.0, 6.0, 7.0, 0.0)))
    answer = q.query(lib, S, params, [LP, "SRF", "Lp_lf"])["quantities"]
    c = answer[LP]["composition"]
    assert c["model"] == "resonance" and c["formula"] == "Lp_lf x resonance_factor x residual"
    assert c["Lp_lf"] == pytest.approx(answer["Lp_lf"]["value"], rel=1e-12) and c["SRF"] == pytest.approx(answer["SRF"]["value"], rel=1e-12)
    assert c["resonance_factor"] == pytest.approx(1 / (1 - (20e9 / c["SRF"]) ** 2), rel=1e-12)
    assert c["Lp_lf"] * c["resonance_factor"] * c["residual"] == pytest.approx(answer[LP]["value"], rel=1e-9)
    assert "composition" not in answer["SRF"] and "composition" not in answer["Lp_lf"]
    assert answer[LP]["status"] == "predicted" and answer[LP]["rel_sigma_max"] == domain.DEFAULT_SIGMA_REL_MAX
    row = lib.dataset(S).rows[0]
    assert "composition" not in q.query(lib, S, row.coords, [LP])["quantities"][LP]


def test_a_composed_curve_keeps_its_confidence_ceiling_and_the_ceiling_refits_nothing(table, tmp_path):
    """The curve's rel_sigma_max in library.yaml is its ceiling like any quantity's: the answer reports it next to the
    composition, and setting it changes no cache key -- the same model file answers."""
    root = shutil.copytree(table, tmp_path / "lib")
    before = q.Library(root, limits=LOCAL)._model_file(S, LP)
    res_manifest(root, lp={**RESONANCE, "rel_sigma_max": 1e-4}, ls=RESONANCE, srf={"feature_map": MAPPED})
    lib = q.Library(root, limits=LOCAL)
    assert lib._model_file(S, LP) == before and lib.model(S, LP).rel_sigma_max == 1e-4
    a = q.query(lib, S, dict(zip(XFM_DIMS, (110.0, 104.0, 6.0, 7.0, 0.0))), [LP])["quantities"][LP]
    assert a["status"] == "uncertain" and a["rel_sigma_max"] == 1e-4 and a["composition"]["model"] == "resonance"


def test_region_and_densify_take_composed_models(table, tmp_path, monkeypatch):
    """The callers of the library's models need nothing new: a window on Lp@20 and new points for Lp@20 / Ls@20."""
    lib = q.Library(table, limits=LOCAL)
    mid = float(np.median(lib.dataset(S).values(LP)))
    got = region.region(lib, S, {LP: {"min": 0.95 * mid, "max": 1.05 * mid}}, steps={d: 2.0 for d in XFM_DIMS[:2]} | {d: 1.0 for d in XFM_DIMS[2:4]},
                        levels_per_dim=6, pool_size=2048, sample_size=10)
    assert got["levels"]["mean"]["count"] > 0 and got["grid"]["confident"] > 0
    picks = densify.densify(lib, S, [LP, LS], n=3, pool_size=1024, top=200)
    assert len(picks["candidates"]) == 3 and all(np.isfinite(c["predicted"][LP]["value"]) for c in picks["candidates"])
    assert picks["after"][LP]["rel_sigma"]["p90"] <= picks["before"][LP]["rel_sigma"]["p90"]


def fit_part_in_worker(root: Path) -> list[str]:
    """In a spawned process: the composed curve's fit alone, recording what it fits (the parts must come from their files)."""
    seen: list[str] = []
    original = q.Library._fit

    def fit(self, stratum, quantity):
        seen.append(quantity)
        return original(self, stratum, quantity)

    q.Library._fit = fit
    q._fit_in_worker(root, True, S, LP, 1)
    return seen


def test_a_worker_fits_the_curve_on_the_parts_cached_before(table, tmp_path):
    """What a final job does in its own interpreter: the parts load from their cache files and only the curve is fitted."""
    root = shutil.copytree(table, tmp_path / "lib")
    for stale in (root / ".cache").glob("model-xfm_res-Lp_at_20-*.pkl"):
        stale.unlink()
    with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
        seen = pool.submit(fit_part_in_worker, root).result()
    assert seen == [LP] and q.Library(root, limits=LOCAL)._model_file(S, LP) is not None
