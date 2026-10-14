"""N-125 (``docs/refactor/N125_SHARED_PART_FOLDS_SPEC.md``): a composed curve's calibration on stable rows -- fold s holds
out the same rows S_s of the stratum for every column, and each part's model of a fold is fitted once and shared by every
curve built on it, at any frequency -- while a curve calibrated before keeps its calibration, its model file and its answers.

The resonance table of test_library_composed (``library_fixtures.res_physics``, 100 rows): Lp and Ls with ``model:
resonance``, Qp with ``model: ratio``, on the mapped inputs; every row has the curves at 20 GHz, 68 rows at 40 GHz (their
resonance above 1.25 x 40 GHz). The fake stratum ``sparse`` holds rows written straight into the library's dataset (no sNp):
40 transformers with Lp_lf and four Lp anchors (``model: ratio``, joint on the features) whose few rows are placed against
the folds' held-out rows."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
import yaml

from ic_opt.library import composed, dataset, folds, gp, manifest
from ic_opt.library import query as q
from ic_opt.site import HostLimits
from tests.ic_opt.fakes import FAKE_HOST
from tests.ic_opt.library_fixtures import (
    LOCAL,
    MAPPED,
    RES_STRATUM,
    build_resonance_library,
    clear_thread_caps,
)

S, LP = RES_STRATUM, "Lp@20"
RESONANCE = {"model": "resonance", "feature_map": MAPPED}
RATIO = {"model": "ratio", "feature_map": MAPPED}
SCALARS = ("Lp_lf", "Ls_lf", "SRF", "Qp_peak")
FIELDS = {"k_scale", "median_rel", "coverage_2sigma_before", "n_scored", "dropped_nonpositive", "source", "sigma_floor", "model"}
TODAY = "holdout 5x20%, every part refitted on each fold"          # the source of a calibration from before N-125
ONE_THREAD = HostLimits(max_threads=1, max_memory_gb=8)


@pytest.fixture(autouse=True)
def _no_thread_caps(monkeypatch):
    clear_thread_caps(monkeypatch)


def table(root: Path) -> Path:
    return build_resonance_library(root, lp=RESONANCE, ls=RESONANCE, qp=RATIO, srf={"feature_map": MAPPED}, scalars=SCALARS)


def positive(ds: dataset.Dataset, column: str) -> set[int]:
    with np.errstate(invalid="ignore"):
        return set(np.flatnonzero(np.isfinite(ds.values(column)) & (ds.values(column) > 0)).tolist())


def held(n: int, seed: int) -> set[int]:
    return set(folds.held(n, seed).tolist())


def todays_calibration(lib: q.Library, column: str, stratum: str = S) -> tuple[str, dict]:
    """What main's code writes for a composed curve, computed as it computes it: ``composed.holdout`` over the curve's own rows,
    every part refitted on each fold, under the name keyed by the dataset and the settings alone."""
    settings = lib._composed_settings(stratum, column)
    report = composed.holdout(*lib._composed_data(stratum, column), **settings)
    h = hashlib.sha256(b"composed")
    h.update(lib.dataset(stratum).key.encode())
    h.update(json.dumps(settings, sort_keys=True).encode())
    name = f"calibration-{stratum}-{column.replace('@', '_at_')}-{settings['kind']}-{h.hexdigest()[:20]}.json"
    return name, {"k_scale": gp.calibration_scale(report), "median_rel": report["median_rel"],
                  "coverage_2sigma_before": report["coverage_2sigma"], "n_scored": report["n_scored"],
                  "dropped_nonpositive": report["dropped_nonpositive"], "source": TODAY,
                  "sigma_floor": "median held-out relative error", "model": settings["kind"]}


# -- one fold ---------------------------------------------------------------------------------------------------------------

def test_on_a_curve_every_row_has_a_stable_fold_is_todays_fold(tmp_path):
    """Where every row of the stratum has the curve (Lp@20), S_s is what ``composed.holdout_fold`` holds out -- gp.split's
    share of the curve's rows is gp.split's share of all the rows --, so the fold on stable rows, with the parts' fold models
    fitted apart (``folds.fit_part``), scores the same rows with the same numbers, bit for bit, and the curve's calibration
    equals today's."""
    lib = q.Library(table(tmp_path / "lib"), limits=LOCAL)
    x, y, base, srf = lib._composed_data(S, LP)
    s = lib._composed_settings(S, LP)
    assert len(positive(lib.dataset(S), LP)) == len(x) == 100
    for seed in gp.SEEDS:
        today = composed.holdout_fold(x, y, base, srf, seed, **s)
        stable = folds.fold(x, y, base, seed, lf=folds.fit_part(x, base, seed, s["lf"]), srf=folds.fit_part(x, srf, seed, s["srf"]),
                            **{k: s[k] for k in ("kind", "f0", "dims", "ranges", "nt_dim", "part", "names")})
        assert stable == today, seed
    _name, today = todays_calibration(lib, LP)
    mine = lib._composed_calibration(S, LP)
    assert {k: mine[k] for k in FIELDS - {"source"}} == {k: today[k] for k in FIELDS - {"source"}}
    assert mine["folds"] == "stable-rows" and mine["source"] == folds.SOURCE


# -- the library: what each fold sees, what is fitted, what is kept ------------------------------------------------------------

def spy(monkeypatch, ds: dataset.Dataset) -> dict:
    """From now on, in this process: each part fold model fitted (``folds.fit_part``, keyed by part and seed) with the rows
    it was fitted on, and each calibration fold (``folds.fold``) with the parts' fold models it used, the rows its curve's
    own model was fitted on and the rows it scored -- as row indices of ``ds``."""
    index = {tuple(row): i for i, row in enumerate(ds.matrix())}
    seen: dict = {"part_rows": {}, "folds": [], "owner": {}, "kept": []}
    stack: list[tuple[str, int]] = []                # the part fold or the curve's fold being made
    sink: list[list] = []                            # what StratumGP.fit and ComposedGP.predict get meanwhile

    def rows(x) -> set[int]:
        return {index[tuple(r)] for r in np.asarray(x, dtype=float)}

    fit, predict = gp.StratumGP.fit, composed.ComposedGP.predict
    part_fold, fit_part, composed_fold, fold = q.Library._part_fold, folds.fit_part, q.Library._composed_fold, folds.fold

    def spy_fit(self, x, y):
        if sink:
            sink[-1].append(("fit", rows(x)))
        return fit(self, x, y)

    def spy_predict(self, x, *, floor=True):
        if sink:
            sink[-1].append(("predict", rows(x)))
        return predict(self, x, floor=floor)

    def spy_part_fold(self, stratum, part, seed):
        stack.append((part, seed))
        try:
            model = part_fold(self, stratum, part, seed)
        finally:
            stack.pop()
        seen["owner"][id(model)] = (part, seed)
        seen["kept"].append(model)                   # its id stays its own
        return model

    def spy_fit_part(x, y, seed, settings):
        sink.append([])
        try:
            return fit_part(x, y, seed, settings)
        finally:
            seen["part_rows"].setdefault(stack[-1], []).extend(r for kind, r in sink.pop() if kind == "fit")

    def spy_composed_fold(self, stratum, quantity, seed):
        stack.append((quantity, seed))
        try:
            return composed_fold(self, stratum, quantity, seed)
        finally:
            stack.pop()

    def spy_fold(x, y, lf_y, seed, *, lf, srf, **settings):
        sink.append([])
        try:
            return fold(x, y, lf_y, seed, lf=lf, srf=srf, **settings)
        finally:
            events = sink.pop()
            seen["folds"].append({"column": stack[-1][0], "seed": seed,
                                  "parts": [seen["owner"][id(m)] for m in (lf, srf) if m is not None],
                                  "own": [r for kind, r in events if kind == "fit"], "scored": [r for kind, r in events if kind == "predict"]})

    monkeypatch.setattr(gp.StratumGP, "fit", spy_fit)
    monkeypatch.setattr(composed.ComposedGP, "predict", spy_predict)
    monkeypatch.setattr(q.Library, "_part_fold", spy_part_fold)
    monkeypatch.setattr(folds, "fit_part", spy_fit_part)
    monkeypatch.setattr(q.Library, "_composed_fold", spy_composed_fold)
    monkeypatch.setattr(folds, "fold", spy_fold)
    return seen


def test_every_held_out_row_is_unseen_and_each_part_fold_model_is_fitted_once(tmp_path, monkeypatch):
    """Lp@20 (100 rows), Lp@40, Ls@40 and Qp@40 (68 rows) on stable rows, one worker: each part's model of each fold --
    Lp_lf, Ls_lf, SRF and Qp_peak, five each -- is fitted exactly once, on the part's rows less S_s, and used by every curve
    built on it (SRF by three, at two frequencies); each curve's fold scores S_s's rows with a positive value of the curve,
    none of which any model it predicts with has seen. The calibrations carry today's fields and ``folds``, and a second
    library on the same cache fits nothing and answers the same."""
    root = table(tmp_path / "lib")
    lib = q.Library(root, limits=LOCAL)
    columns = lib.columns(S, ["Lp@20", "Lp@40", "Ls@40", "Qp@40"])
    ds = lib.dataset(S)
    n = len(ds.rows)
    assert positive(ds, "Lp@20") != positive(ds, "Lp@40") and len(positive(ds, "Lp@40")) == 68
    seen = spy(monkeypatch, ds)
    got = lib.models(S, columns)
    parts = {"Lp@20": ["Lp_lf", "SRF"], "Lp@40": ["Lp_lf", "SRF"], "Ls@40": ["Ls_lf", "SRF"], "Qp@40": ["Qp_peak"]}
    assert sorted(seen["part_rows"]) == sorted((p, seed) for p in ("Lp_lf", "Ls_lf", "Qp_peak", "SRF") for seed in gp.SEEDS)
    for (p, seed), fits in seen["part_rows"].items():                  # once each, on the part's rows less S_s
        usable = set(np.flatnonzero(np.isfinite(ds.values(p))).tolist())
        assert fits == [usable - held(n, seed)], (p, seed)
    assert sorted((f["column"], f["seed"]) for f in seen["folds"]) == sorted((c, seed) for c in columns for seed in gp.SEEDS)
    for f in seen["folds"]:
        out, c = held(n, f["seed"]), f["column"]
        (own,), (scored,) = f["own"], f["scored"]
        assert f["parts"] == [(p, f["seed"]) for p in parts[c]] and scored == out & positive(ds, c)
        assert not scored & own and own <= positive(ds, c) - out
        for part in f["parts"]:
            assert not scored & seen["part_rows"][part][0]
    assert {f["seed"]: f["scored"][0] for f in seen["folds"] if f["column"] == "Lp@20"} == {s: held(n, s) for s in gp.SEEDS}
    for c in columns:
        cal = got[c].calibration
        assert set(cal) == FIELDS | {"folds"} and cal["folds"] == "stable-rows" and cal["source"] == folds.SOURCE
        assert cal["n_scored"] == sum(len(held(n, s) & positive(ds, c)) for s in gp.SEEDS) and cal["model"] == got[c].gp.kind
    files = sorted(p.name.rsplit("-", 1)[0] for p in lib.cache.directory.glob(f"fold-{S}-*.pkl"))
    assert files == sorted(f"fold-{S}-{p}-s{seed}" for p in ("Lp_lf", "Ls_lf", "Qp_peak", "SRF") for seed in gp.SEEDS)

    monkeypatch.setattr(gp.StratumGP, "fit", lambda self, x, y: pytest.fail("a model was fitted again"))
    again = q.Library(root, limits=LOCAL).models(S, columns)
    x = ds.matrix()[::7]
    for c in columns:
        assert again[c].calibration == got[c].calibration
        for a, b in zip(again[c].gp.predict(x), got[c].gp.predict(x)):
            assert np.array_equal(a, b), c


def test_a_curve_calibrated_before_keeps_its_calibration_its_model_file_and_its_answers(tmp_path, monkeypatch):
    """Lp@20's calibration as main's code writes it, under main's name: ``models`` takes it -- no fold runs, no part fold
    model is fitted, nothing on stable rows is written -- and fits the curve on it, the model file named through it as
    before; the answers are those of a model fitted on it as main fits one. Its model file there too, nothing is fitted
    at all and the answers are that file's, bit for bit. A sibling curve without a calibration gets one on stable rows."""
    root = table(tmp_path / "lib")
    owner = q.Library(root, limits=LOCAL)
    owner.models(S, ["Lp_lf", "Ls_lf", "SRF"])
    name, before = todays_calibration(owner, LP)
    (owner.cache.directory / name).write_text(json.dumps(before), encoding="utf-8")
    assert owner._composed_calibration_names(S, LP)[0] == name and owner._composed_calibration_name(S, LP) == name

    for what in ("fold", "fit_part"):
        monkeypatch.setattr(folds, what, lambda *a, **k: pytest.fail("a fold ran for a curve calibrated before"))
    monkeypatch.setattr(composed, "holdout_fold", lambda *a, **k: pytest.fail("a fold ran for a curve calibrated before"))
    lib = q.Library(root, limits=LOCAL)
    assert lib.cached(S)[LP] == "calibration" and lib.no_model(S, LP) is None
    m = lib.model(S, LP)
    assert m.calibration == before and not list(lib.cache.directory.glob("fold-*"))
    assert not (lib.cache.directory / lib._composed_calibration_names(S, LP)[1]).exists()
    path = lib._model_file(S, LP)
    assert path is not None and path.name == lib._composed_model_name(S, LP, before)

    s = owner._composed_settings(S, LP)                                 # the curve as main fits it on that calibration
    ds = owner.dataset(S)
    rows = ds.usable(LP)
    reference = composed.ComposedGP(kind=s["kind"], f0=s["f0"], dims=s["dims"], ranges=s["ranges"], nt_dim=s["nt_dim"], part=s["part"],
                                    names=s["names"], k_scale=before["k_scale"], sigma_floor_rel=before["median_rel"])
    reference.attach(owner.model(S, "Lp_lf").gp, owner.model(S, "SRF").gp, n_train=max(len(ds.usable(c)) for c in ("Lp_lf", "SRF")))
    reference.fit(ds.matrix(rows), ds.values(LP, rows), lf_y=ds.values("Lp_lf", rows))
    x = np.array([[110.0, 104.0, 6.0, 7.0, 0.0], [150.0, 165.0, 8.0, 8.0, 0.0], [90.0, 95.0, 5.0, 6.0, 0.0]])
    for a, b in zip(m.gp.predict(x) + m.gp.predict_bounds(x), reference.predict(x) + reference.predict_bounds(x)):
        assert np.array_equal(a, b)

    q._save_model(path, reference)                                       # main's model file, written by main
    monkeypatch.setattr(gp.StratumGP, "fit", lambda self, x, y: pytest.fail("a model was fitted"))
    monkeypatch.setattr(composed.ComposedGP, "fit", lambda self, *a, **k: pytest.fail("a curve was fitted"))
    loaded = q.Library(root, limits=LOCAL)
    answer = q.query(loaded, S, dict(zip(ds.dims, x[0])), [LP])["quantities"][LP]
    mu, sigma = reference.predict(x[:1])
    lo, hi = reference.predict_bounds(x[:1])
    assert (answer["value"], answer["lo"], answer["hi"], answer["k_scale"]) == (mu[0], lo[0], hi[0], before["k_scale"])
    assert answer["rel_sigma"] == sigma[0] / mu[0] and answer["status"] == "predicted"

    monkeypatch.undo()
    clear_thread_caps(monkeypatch)
    sibling = q.Library(root, limits=LOCAL).model(S, "Ls@20").calibration
    assert sibling["folds"] == "stable-rows" and len(list(lib.cache.directory.glob(f"fold-{S}-*.pkl"))) == 10


def test_the_parallel_path_fits_only_the_missing_part_fold_models(tmp_path):
    """Spawned workers. Lp@20 first: its parts' ten fold models, its five folds, its fit. Ls@20 then: only Ls_lf's five fold
    models are fitted -- SRF's, on file, are read and left as they are -- and its calibration is the one a single worker
    computes on a cache of its own (one BLAS thread both)."""
    root = table(tmp_path / "lib")
    lib = q.Library(root, limits=FAKE_HOST)
    lib.models(S, [LP], workers=4, threads=4)
    first = {p.name: p.stat().st_mtime_ns for p in lib.cache.directory.glob(f"fold-{S}-*.pkl")}
    assert sorted(name.rsplit("-", 1)[0] for name in first) == sorted(f"fold-{S}-{p}-s{s}" for p in ("Lp_lf", "SRF") for s in gp.SEEDS)
    got = q.Library(root, limits=FAKE_HOST).models(S, ["Ls@20"], workers=4, threads=4)["Ls@20"]
    after = {p.name: p.stat().st_mtime_ns for p in lib.cache.directory.glob(f"fold-{S}-*.pkl")}
    assert {k: after[k] for k in first} == first and len(after) == 15
    alone = q.Library(shutil.copytree(root, tmp_path / "alone", ignore=shutil.ignore_patterns(".cache")), limits=ONE_THREAD)
    expected = alone.model(S, "Ls@20").calibration
    assert got.calibration.keys() == expected.keys()
    for key in ("k_scale", "median_rel", "coverage_2sigma_before", "n_scored", "dropped_nonpositive"):
        assert got.calibration[key] == pytest.approx(expected[key], rel=1e-9), key


# -- a curve too sparse for the shared folds: the folds of before, N-120's verdicts unchanged ------------------------------------

SPARSE = "sparse"
STEPS = {"primary_outer_diameter_um": 1, "secondary_outer_diameter_um": 1, "primary_width_um": 0.1, "secondary_width_um": 0.1,
         "center_spacing_um": 0.5}
GEOMETRY = [(op, round(op * r), wp, 6.0, 0.0) for op in (80.0, 100.0, 120.0, 140.0, 160.0) for r in (0.8, 1.0, 1.2, 1.4) for wp in (5.0, 8.0)]
N = len(GEOMETRY)                                                        # 40
NEVER = sorted(set(range(N)) - set().union(*(held(N, s) for s in gp.SEEDS)))    # rows no fold holds out
ONCE = sorted(i for i in range(N) if sum(i in held(N, s) for s in gp.SEEDS) == 1)
CURVES = {"Lp@100": NEVER[:3],                                          # no stable fold holds out a row: nothing to score
          "Lp@110": [ONCE[0], NEVER[0]],                                # 2 rows: the stable folds would complete, today's not
          "Lp@120": [ONCE[1]],                                          # 1 row: neither
          "Lp@130": [ONCE[2], ONCE[3]] + NEVER[3:7]}                    # some stable folds hold out none of its rows, others do
MANIFEST = {"schema_version": "ic-opt-library-v1", "process_profile": "demo_6m", "strata": {SPARSE: {
    "generator": "clean_port_xfm_bs", "dims": list(manifest.XFM_BS_DIMS), "parts": [{"store": SPARSE}], "steps": STEPS,
    "quantities": {"Lp_lf": {}, "Lp": {"anchors_ghz": [100, 110, 120, 130], "model": "ratio", "feature_map": MAPPED}}}}}


def lp_lf(op, os_, wp, ws, cs) -> float:
    return 1e-10 * (op / 100) ** 1.3 * (os_ / 100) ** 0.2 * (5 / wp) ** 0.1


def sparse_library(tmp_path: Path, cache: Path) -> q.Library:
    """A library declaring ``sparse`` on an empty cache directory, its dataset the rows of GEOMETRY: Lp_lf on every row, each
    Lp anchor of CURVES on its rows only."""
    root = tmp_path / "sparse_root"
    root.mkdir(exist_ok=True)
    (root / "library.yaml").write_text(yaml.safe_dump(MANIFEST, sort_keys=False), encoding="utf-8")
    lib = q.Library(root, limits=LOCAL, cache_dir=cache)
    columns = lib.manifest.strata[SPARSE].columns()
    rows = []
    for i, g in enumerate(GEOMETRY):
        values = {c: (1.2 + 0.01 * i) * lp_lf(*g) if i in CURVES.get(c, ()) else None for c in columns}
        values["Lp_lf"] = lp_lf(*g)
        rows.append(dataset.Row(SPARSE, f"obs_{i:04d}", dict(zip(manifest.XFM_BS_DIMS, g)), values, 250e9, 251, 4, 0.5, True,
                                f"{SPARSE}/obs_{i:04d}.s4p"))
    lib._datasets[SPARSE] = dataset.Dataset(SPARSE, list(manifest.XFM_BS_DIMS), None, columns, rows, {SPARSE: "gen1"}, {}, "off", "sparsekey")
    return lib


def test_a_curve_too_sparse_for_the_shared_folds_is_calibrated_as_before_and_no_verdict_changes(tmp_path):
    """Which composed curves can have a model is N-120's rule, unchanged (``composed_fits``): Lp@100 (3 rows) and Lp@130
    (6 rows) yes, Lp@110 (2) and Lp@120 (1) no -- and a fit raises exactly for those two, with the errors of before. The
    stable folds calibrate a curve only where they can too (``Library._stable``): Lp@130, whose folds partly hold out none
    of its rows (they score nothing, the others calibrate it), but not Lp@100, none of whose rows any stable fold holds
    out: it is calibrated on the folds of before, under its earlier name, as main calibrates it -- and so are the two
    curves without a model, whose stable folds alone would have completed for Lp@110."""
    assert NEVER and len(ONCE) >= 4
    lib = sparse_library(tmp_path, tmp_path / "cache")
    reason = "{} usable rows: too few for a model of this column"
    expected = {"Lp@100": None, "Lp@110": {"rows": 2, "reason": reason.format(2)}, "Lp@120": {"rows": 1, "reason": reason.format(1)},
                "Lp@130": None}
    assert {c: lib.no_model(SPARSE, c) for c in CURVES} == expected
    rows = {c: lib._composed_rows(SPARSE, c) for c in CURVES}
    assert {c: (q.composed_fits(*r), q.stable_composed_fits(*r)) for c, r in rows.items()} == {
        "Lp@100": (True, False), "Lp@110": (False, True), "Lp@120": (False, False), "Lp@130": (True, True)}
    assert {c: lib._stable(SPARSE, c) for c in CURVES} == {"Lp@100": False, "Lp@110": False, "Lp@120": False, "Lp@130": True}
    raised = {}
    for column in CURVES:
        try:
            lib._fit(SPARSE, column)
        except ValueError as exc:
            raised[column] = str(exc)
    assert set(raised) == {"Lp@110", "Lp@120"} and all("Found array with 0 sample(s)" in why for why in raised.values())

    before, stable = lib._models[(SPARSE, "Lp@100")].calibration, lib._models[(SPARSE, "Lp@130")].calibration
    name, today = todays_calibration(lib, "Lp@100", SPARSE)
    assert before == today and (lib.cache.directory / name).is_file() and "folds" not in before
    assert stable["folds"] == "stable-rows" and stable["n_scored"] == sum(len(held(N, s) & set(CURVES["Lp@130"])) for s in gp.SEEDS)
    assert any(not held(N, s) & set(CURVES["Lp@130"]) for s in gp.SEEDS)              # some fold scored none of its rows
    assert sorted(p.name.rsplit("-", 1)[0] for p in lib.cache.directory.glob("fold-*.pkl")) == [f"fold-{SPARSE}-Lp_lf-s{s}" for s in gp.SEEDS]


def test_the_stable_condition_in_numbers():
    levels, nan = np.array([2.0] * 30 + [3.0] * 10), np.full(40, np.nan)
    base = np.full(40, 1e-10)
    out = [held(40, s) for s in gp.SEEDS]
    never = sorted(set(range(40)) - set().union(*out))

    def curve(rows):
        return np.array([2e-10 if i in rows else np.nan for i in range(40)])

    joint, per_level = {"part": False, "lf": False}, {"part": True, "lf": True}
    assert not q.stable_composed_fits(levels, curve(never[:3]), base, None, joint)          # no fold holds out a curve row
    assert q.stable_composed_fits(levels, curve(never[:3]), base, None, per_level)          # per level: nothing to refuse
    assert q.stable_composed_fits(levels, curve(never[:3]), base, None, joint, calibrate=False)
    many = sorted(set(range(40)) - set(never))[:12]
    assert q.stable_composed_fits(levels, curve(many), base, None, joint)
    assert not q.stable_composed_fits(levels, curve(many), nan, None, per_level)             # no base value: no row for the part
    assert not q.stable_composed_fits(levels, curve(many), base, nan, {**joint, "srf": False})   # a joint SRF fold model on no row
    three = [i for i in range(30, 40) if any(i in o for o in out)]                           # at level 3, held out somewhere
    lf_by_level = {"part": False, "lf": True}                                                # Lp_lf per level: none at level 3 (10 rows)
    assert three and not q.stable_composed_fits(levels, curve(three), base, None, lf_by_level)
    assert q.stable_composed_fits(levels, curve(three), base, None, per_level)
