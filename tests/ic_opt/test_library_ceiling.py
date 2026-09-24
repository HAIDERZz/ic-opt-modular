"""T16.5 R-21: a confidence ceiling per quantity (library.yaml ``rel_sigma_max``). Precedence: a call's explicit value, then the
quantity's, then domain.DEFAULT_SIGMA_REL_MAX -- the same in lib.query, lib.suggest, lib.region, lib.densify and the Predict
stage. The ceiling is no part of any cache key: setting one keeps every dataset, calibration and model."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pydantic
import pytest
import yaml

from ic_opt.library import dataset, densify, domain, manifest, region, stage
from ic_opt.library import query as q
from ic_opt.library import suggest as s
from tests.ic_opt.library_fixtures import LOCAL, build_library, params
from tests.ic_opt.test_library_query import count_fits

STRICT = 1e-6                    # below the sigma of any prediction (its floor is the held-out median error): never confident
LOOSE = 0.4
COLUMNS = ["Lp_lf", "Qp_peak", "Lp@10"]
BETWEEN = params(155, 4.5, 2.5, 2)                                      # between the library's rows
TARGETS = {"Lp_lf": {"min": 1.5e-9, "max": 2.0e-9}}                     # met by two-turn rows of 100-120 um


@pytest.fixture(scope="module")
def plain(tmp_path_factory) -> Path:
    """The synthetic library, no ceilings in its library.yaml, its three models fitted and cached."""
    root = build_library(tmp_path_factory.mktemp("plain"))
    q.Library(root, limits=LOCAL).models("ind_demo", COLUMNS)
    return root


@pytest.fixture(scope="module")
def ceilings(plain, tmp_path_factory) -> Path:
    """A copy of ``plain``, .cache included, whose library.yaml gives Lp_lf the ceiling STRICT and Qp_peak LOOSE (the curve
    Lp keeps the default)."""
    root = shutil.copytree(plain, tmp_path_factory.mktemp("ceilings") / "lib")
    doc = yaml.safe_load((root / "library.yaml").read_text(encoding="utf-8"))
    rules = doc["strata"]["ind_demo"]["quantities"]
    rules["Lp_lf"]["rel_sigma_max"], rules["Qp_peak"]["rel_sigma_max"] = STRICT, LOOSE
    (root / "library.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return root


def test_a_ceiling_in_the_manifest_keeps_every_cache(plain, ceilings, monkeypatch):
    """The ceiling decides what an answer trusts, not what a row holds or how a model is fitted: the dataset key is the
    same, and every model loads from the cache the copy brought along."""
    assert dataset.build(ceilings, "ind_demo").key == dataset.build(plain, "ind_demo").key
    fits = count_fits(monkeypatch)
    lib = q.Library(ceilings, limits=LOCAL)
    models = lib.models("ind_demo", COLUMNS)
    assert fits == [] and lib.dataset("ind_demo").cache == "hit"
    assert {c: m.rel_sigma_max for c, m in models.items()} == {"Lp_lf": STRICT, "Qp_peak": LOOSE, "Lp@10": domain.DEFAULT_SIGMA_REL_MAX}
    assert [lib.rel_sigma_max("ind_demo", c, 0.3) for c in COLUMNS] == [0.3] * 3            # an explicit value wins


@pytest.mark.parametrize("bad", [0, -0.1, float("inf"), float("nan"), "high"])
def test_a_ceiling_is_a_positive_finite_number(bad):
    with pytest.raises(pydantic.ValidationError, match="rel_sigma_max"):
        manifest.Quantity.model_validate({"rel_sigma_max": bad})


def test_query_takes_the_calls_ceiling_then_the_quantitys_then_the_default(ceilings):
    lib = q.Library(ceilings, limits=LOCAL)
    got = q.query(lib, "ind_demo", BETWEEN, COLUMNS)["quantities"]
    assert {c: (a["status"], a["rel_sigma_max"]) for c, a in got.items()} == {
        "Lp_lf": ("uncertain", STRICT), "Qp_peak": ("predicted", LOOSE), "Lp@10": ("predicted", domain.DEFAULT_SIGMA_REL_MAX)}
    explicit = q.query(lib, "ind_demo", BETWEEN, COLUMNS, rel_sigma_max=0.5)["quantities"]
    assert {c: (a["status"], a["rel_sigma_max"]) for c, a in explicit.items()} == dict.fromkeys(COLUMNS, ("predicted", 0.5))
    measured = q.query(lib, "ind_demo", params(150, 5, 3, 2), ["Lp_lf"])["quantities"]["Lp_lf"]
    assert measured["status"] == "measured"                                               # a measurement needs no ceiling


def test_suggest_and_region_gate_each_quantity_on_its_own_ceiling(ceilings):
    """Lp_lf's ceiling is STRICT: no prediction of it is confident, so lib.suggest keeps only measured designs and
    lib.region finds no confident grid point; an explicit ceiling overrides the manifest's for both."""
    lib = q.Library(ceilings, limits=LOCAL)
    strict = s.suggest(lib, "ind_demo", TARGETS, None, n=2, pool_size=256, verify_build=False)
    assert strict["candidates"] == [] and strict["satisfying"] == strict["satisfying_measured"] > 0
    loose = s.suggest(lib, "ind_demo", TARGETS, None, n=2, pool_size=256, verify_build=False, rel_sigma_max=0.5)
    assert len(loose["candidates"]) == 2
    shut = region.region(lib, "ind_demo", TARGETS, pool_size=256, n=1, workers=1)
    assert shut["grid"]["confident"] == 0 and shut["levels"]["mean"]["count"] == 0 and shut["measured"]
    assert any("where a model has no confident answer" in note for note in shut["notes"]), shut["notes"]
    open_ = region.region(lib, "ind_demo", TARGETS, pool_size=256, n=1, workers=1, rel_sigma_max=0.5)
    assert open_["levels"]["mean"]["count"] > 0


def test_densify_divides_by_each_quantitys_ceiling(ceilings):
    """score=ceiling: each quantity's sigma over its own ceiling, which the statistics count against as well. Lp_lf's
    STRICT ceiling puts its whole pool above it; an explicit value is every quantity's."""
    lib = q.Library(ceilings, limits=LOCAL)
    r = densify.densify(lib, "ind_demo", COLUMNS, n=2, pool_size=512, top=64, workers=1)
    own = {"Lp_lf": STRICT, "Qp_peak": LOOSE, "Lp@10": domain.DEFAULT_SIGMA_REL_MAX}
    assert r["method"]["norm"] == r["method"]["rel_sigma_max"] == own
    assert r["before"]["Lp_lf"]["above_ceiling_share"] == 1.0
    for c in r["candidates"]:
        assert max(c["rel_sigma_at_pick"][col] / own[col] for col in COLUMNS) == pytest.approx(c["score"])
    given = densify.densify(lib, "ind_demo", COLUMNS, n=2, pool_size=512, top=64, workers=1, rel_sigma_max=0.3)
    assert given["method"]["norm"] == given["method"]["rel_sigma_max"] == dict.fromkeys(COLUMNS, 0.3)
    with pytest.raises(ValueError, match="rel_sigma_max must be a positive number"):
        densify.densify(lib, "ind_demo", COLUMNS, n=1, pool_size=64, workers=1, rel_sigma_max=0.0)


def test_predict_fails_only_the_metric_whose_ceiling_it_misses(plain, ceilings, tmp_path):
    """The Predict stage asks each column with its own ceiling: L (Lp_lf, STRICT) is uncertain, Q and L10 are not. A manifest
    ceiling is part of the stage's identity -- surrogate observations of another ceiling are another generation -- while a
    library that sets none keeps the identity it had, and an explicit rel_sigma_max overrides every column."""
    pytest.importorskip("klayout.db")
    from ic_opt.blocks.evaluate import evaluate
    from ic_opt.eval.stage import pipeline_fingerprint
    from ic_opt.space import Point
    from ic_opt.store import RunStore
    from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor
    from tests.ic_opt.test_library_stage import design_spec

    spec = design_spec("ceilings")
    lib, before = q.Library(ceilings, limits=LOCAL), q.Library(plain, limits=LOCAL)
    store = RunStore(tmp_path)
    point = Point({"outer_diameter_um": "155", "width_um": "4.5", "spacing_um": "2.5", "turns": "2"}, "user")
    (strict,) = evaluate(spec, [point], FakeSpectreExecutor(store.root / "sims"), store, pipeline=stage.surrogate_pipeline(spec, lib),
                         limits=FAKE_HOST)
    assert strict.status == "failed:predict" and strict.issues == ["ind/nominal: metric L: uncertain"]
    identity = json.loads(stage.Predict(before, {"ind": "ind_demo"}).identity)
    assert identity == {"k": 2.0, "rel_sigma_max": domain.DEFAULT_SIGMA_REL_MAX, "calibrate": True,
                        "strata": {"ind": ["ind_demo", before.dataset("ind_demo").key]}}
    assert json.loads(stage.Predict(lib, {"ind": "ind_demo"}).identity)["rel_sigma_max_by_quantity"] == {
        "ind_demo": {"Lp_lf": STRICT, "Qp_peak": LOOSE}}
    fingerprint = {name: pipeline_fingerprint(stage.surrogate_pipeline(spec, library, **kw))
                   for name, library, kw in (("plain", before, {}), ("ceilings", lib, {}),
                                             ("plain 0.5", before, {"rel_sigma_max": 0.5}), ("ceilings 0.5", lib, {"rel_sigma_max": 0.5}))}
    assert fingerprint["plain"] != fingerprint["ceilings"] and fingerprint["plain 0.5"] == fingerprint["ceilings 0.5"]
    other = RunStore(tmp_path / "given")
    (given,) = evaluate(spec, [point], FakeSpectreExecutor(other.root / "sims"), other,
                        pipeline=stage.surrogate_pipeline(spec, lib, rel_sigma_max=0.5), limits=FAKE_HOST)
    assert given.status in ("ok", "constraint_failed") and not given.issues
