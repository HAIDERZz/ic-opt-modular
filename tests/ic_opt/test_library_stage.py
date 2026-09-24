"""T13.5: the Predict stage, surrogate_pipeline and the lib_design recipe on the synthetic library (fake end to end).
T15.3: the models are fitted once, within the library's limits (hosts.local), before any evaluation thread queries them."""
from __future__ import annotations

import inspect
import json
import shutil
import threading
from pathlib import Path

import pytest
import yaml

from ic_opt.blocks.evaluate import evaluate
from ic_opt.eval.stage import pipeline_fingerprint
from ic_opt.library import domain, gp, query, stage
from ic_opt.recipe import PLAN_MODE, Run, load_recipe
from ic_opt.recipes import lib_design
from ic_opt.site import Site
from ic_opt.space import Point
from ic_opt.spec import Spec, load_spec
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor
from tests.ic_opt.library_fixtures import LOCAL, build_library, params, truth
from tests.ic_opt.test_library import part_spec

pytest.importorskip("klayout.db")

FITS_PER_MODEL = 6                                    # the five hold-out fits of the calibration, then the model


def fits_by_thread(monkeypatch) -> list[str]:
    """From now on, the thread of every model fit in this process (a per_nt model's sub-GPs fit inside its fit, uncounted)."""
    names: list[str] = []
    local = threading.local()
    original = gp.StratumGP.fit

    def fit(self, x, y):
        depth = getattr(local, "depth", 0)
        if depth == 0:
            names.append(threading.current_thread().name)
        local.depth = depth + 1
        try:
            return original(self, x, y)
        finally:
            local.depth = depth

    monkeypatch.setattr(gp.StratumGP, "fit", fit)
    return names


def uncached(library: Path, dest: Path) -> Path:
    """The synthetic library without its .cache: nothing calibrated or fitted yet."""
    return shutil.copytree(library, dest, ignore=shutil.ignore_patterns(".cache"))


@pytest.fixture(scope="module")
def library(tmp_path_factory) -> Path:
    return build_library(tmp_path_factory.mktemp("stlib"))


def design_spec(project: str, *, metal: str = "6", window=(2.0e-9, 3.0e-9)) -> Spec:
    d = part_spec(project, 60).model_dump(mode="json")
    d["devices"][0]["fixed"]["metal"] = metal
    d["variables"] = [{"name": "outer_diameter_um", "kind": "continuous_step", "lower": "100", "upper": "200", "step": "1"},
                      {"name": "width_um", "kind": "continuous_step", "lower": "4", "upper": "6", "step": "0.1"},
                      {"name": "spacing_um", "kind": "continuous_step", "lower": "2", "upper": "3", "step": "0.1"},
                      {"name": "turns", "kind": "integer", "lower": "2", "upper": "2", "step": "1"}]
    d["metrics"] = [{"name": "L", "unit": "H", "device": "ind", "quantity": "Lp_lf"}, {"name": "Q", "unit": "1", "device": "ind", "quantity": "Qp_peak"},
                    {"name": "L10", "unit": "H", "device": "ind", "quantity": "Lp", "frequency_hz": 10e9}]
    d["constraints"] = [{"metric": "L", "op": "gt", "value": f"{window[0]:g}"}, {"metric": "L", "op": "lt", "value": f"{window[1]:g}"}]
    d["objective"] = {"direction": "maximize", "expression": "Q"}
    return Spec.model_validate(d)


def test_predict_fills_device_metrics_from_the_library(library, tmp_path):
    lib = query.Library(library, limits=LOCAL)
    spec = design_spec("predict")
    assert stage.match_stratum(lib, spec.devices[0]) == "ind_demo"
    pipeline = stage.surrogate_pipeline(spec, lib)
    assert [s.name for s in pipeline] == ["pcell", "predict"]
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims")
    obs = evaluate(spec, [Point({"outer_diameter_um": "150", "width_um": "5", "spacing_um": "3", "turns": "2"}, "user"),
                          Point({"outer_diameter_um": "155", "width_um": "4.5", "spacing_um": "2.5", "turns": "2"}, "user")], ex, store, pipeline=pipeline,
                   limits=FAKE_HOST)
    measured, predicted = obs
    assert ex.emx_runs == 0 and {o.status for o in obs} == {"ok"}
    assert measured.metrics["L"] == lib.dataset("ind_demo").find(params(150, 5, 3, 2)).values["Lp_lf"]
    assert predicted.metrics["L"] == pytest.approx(truth(155, 4.5, 2.5, 2)["Lp_lf"], rel=2e-2) and predicted.metrics["L10"] > 0
    answer = json.loads((store.project_dir / predicted.children["ind/nominal"].sim_dir / "predictions.json").read_text())
    assert answer["quantities"]["Lp_lf"]["status"] == "predicted" and answer["quantities"]["Lp_lf"]["lo"] < predicted.metrics["L"]
    assert predicted.pipeline_fingerprint == pipeline_fingerprint(pipeline) != measured.spec_fingerprint


def test_predict_refuses_what_the_library_cannot_vouch_for(library, tmp_path):
    lib = query.Library(library, limits=LOCAL)
    spec = design_spec("refuse")
    d = spec.model_dump(mode="json")
    d["variables"][0]["upper"] = "260"
    spec = Spec.model_validate(d)
    store = RunStore(tmp_path)
    obs = evaluate(spec, [Point({"outer_diameter_um": "250", "width_um": "5", "spacing_um": "3", "turns": "2"}, "user")],
                   FakeSpectreExecutor(store.root / "sims"), store, pipeline=stage.surrogate_pipeline(spec, lib), limits=FAKE_HOST)
    assert obs[0].status == "failed:predict" and any("out_of_domain" in i and "outside the measured range" in i for i in obs[0].issues)
    other_metal = design_spec("m5", metal="5")
    with pytest.raises(ValueError, match="0 strata match"):
        stage.match_stratum(lib, other_metal.devices[0])
    wrong = RunStore(tmp_path / "wrong")
    obs = evaluate(other_metal, [Point({"outer_diameter_um": "150", "width_um": "5", "spacing_um": "3", "turns": "2"}, "user")],
                   FakeSpectreExecutor(wrong.root / "sims"), wrong, pipeline=stage.surrogate_pipeline(other_metal, lib, {"ind": "ind_demo"}),
                   limits=FAKE_HOST)
    assert obs[0].status == "failed:predict" and "is not stratum ind_demo's device" in " ".join(obs[0].issues)


def test_predict_fits_every_model_once_for_all_evaluation_threads(library, tmp_path, monkeypatch):
    """Four points in four evaluation threads, the caller did not prefit: the first thread to need the library fits every
    model the stage asks for (three columns x FITS_PER_MODEL fits) while the others wait; a second evaluation fits none."""
    lib = query.Library(uncached(library, tmp_path / "lib"), limits=LOCAL)                # one fit worker: the fits run here
    spec = design_spec("threads")
    pipeline = stage.surrogate_pipeline(spec, lib)
    fits = fits_by_thread(monkeypatch)
    store = RunStore(tmp_path / "p")
    ex = FakeSpectreExecutor(store.root / "sims")
    points = [Point({"outer_diameter_um": od, "width_um": w, "spacing_um": "2.5", "turns": "2"}, "user")
              for od, w in (("145", "5"), ("155", "4.5"), ("165", "5.5"), ("175", "5"))]
    obs = evaluate(spec, points, ex, store, pipeline=pipeline, limits=FAKE_HOST, parallel_jobs=4)
    assert all({"L", "Q", "L10"} <= set(o.metrics) for o in obs), [o.issues for o in obs]
    assert len(fits) == 3 * FITS_PER_MODEL and len(set(fits)) == 1 and fits[0] != threading.current_thread().name
    assert pipeline[-1].prefit(spec) == {"ind_demo": ["Lp_lf", "Qp_peak", "Lp@10"]}
    more = [Point({"outer_diameter_um": od, "width_um": "5", "spacing_um": "2.5", "turns": "2"}, "user") for od in ("150", "160", "170")]
    evaluate(spec, more, ex, store, pipeline=pipeline, limits=FAKE_HOST, parallel_jobs=4)
    assert len(fits) == 3 * FITS_PER_MODEL


def test_predict_honours_rel_sigma_max(library, tmp_path):
    """The confidence ceiling is the stage's (and lib_design's) parameter, domain.DEFAULT_SIGMA_REL_MAX by default: at 1e-12
    every prediction is uncertain and fails the point, a measured library point still answers."""
    lib = query.Library(library, limits=LOCAL)
    spec = design_spec("strict")
    strict = stage.surrogate_pipeline(spec, lib, rel_sigma_max=1e-12)
    store = RunStore(tmp_path)
    measured, predicted = evaluate(spec, [Point({"outer_diameter_um": "150", "width_um": "5", "spacing_um": "3", "turns": "2"}, "user"),
                                          Point({"outer_diameter_um": "155", "width_um": "4.5", "spacing_um": "2.5", "turns": "2"}, "user")],
                                   FakeSpectreExecutor(store.root / "sims"), store, pipeline=strict, limits=FAKE_HOST)
    assert measured.status == "ok" and predicted.status == "failed:predict"
    assert predicted.issues == [f"ind/nominal: metric {m}: uncertain" for m in ("L", "Q", "L10")]
    assert stage.Predict(lib, {"ind": "ind_demo"}).rel_sigma_max == domain.DEFAULT_SIGMA_REL_MAX
    assert pipeline_fingerprint(strict) != pipeline_fingerprint(stage.surrogate_pipeline(spec, lib))
    assert inspect.signature(lib_design.main).parameters["rel_sigma_max"].default == domain.DEFAULT_SIGMA_REL_MAX


def test_lib_design_recipe_optimizes_on_predictions_and_reports_leaders(library, tmp_path, monkeypatch):
    """The library computes within hosts.local (two threads here: one fit worker, in this process), the evaluation within the
    executor host's entry (lab); every model is fitted once, before the search, in the recipe's own thread -- none in an
    evaluation thread, none under --plan."""
    root = uncached(library, tmp_path / "lib")
    project = tmp_path / "design"
    project.mkdir()
    (project / "spec.yaml").write_text(yaml.safe_dump(design_spec("design").model_dump(mode="json")), encoding="utf-8")
    site = Site({"local": LOCAL, "lab": FAKE_HOST})
    store = RunStore(project)
    run = Run(project, load_spec(project / "spec.yaml"), store, FakeSpectreExecutor(store.root / "sims"), None, site, site.host("lab"))
    fits = fits_by_thread(monkeypatch)
    passed = {}
    real = stage.surrogate_pipeline

    def surrogate_pipeline(*args, **kwargs):
        passed.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(stage, "surrogate_pipeline", surrogate_pipeline)
    main = load_recipe("lib_design")
    token = PLAN_MODE.set(True)
    try:
        main(run, library=str(root), budget=12, batch=6, strategy="random", top=3, rel_sigma_max=0.3)
    finally:
        PLAN_MODE.reset(token)
    assert run.store.observations() == [] and fits == []                              # plan mode starts nothing and fits nothing
    assert passed == {"k": 2.0, "rel_sigma_max": 0.3}
    main(run, library=str(root), budget=24, batch=12, strategy="random", top=3, seed=3)
    assert passed["rel_sigma_max"] == domain.DEFAULT_SIGMA_REL_MAX
    assert len(fits) == 3 * FITS_PER_MODEL and set(fits) == {threading.current_thread().name}
    obs = run.store.observations()
    assert len(obs) == 24 and all(o.step == "lib_design" for o in obs)
    assert all(o.status in ("ok", "constraint_failed", "failed:predict", "failed:pcell") for o in obs)
    assert all(o.simulations == 0 for o in obs) and run.spec.budget.max_simulations == 10          # predictions spend no simulation budget
    report = json.loads((run.store.root / "reports" / "lib_design.json").read_text())
    assert report["strata"] == {"ind": "ind_demo"} and report["evaluated"] == 24 and 1 <= len(report["leaders"]) <= 3
    for leader in report["leaders"]:
        if leader["feasible"]:
            assert 2.0e-9 < leader["metrics"]["L"] < 3.0e-9 and leader["library"]["Lp_lf"]["status"] in ("measured", "predicted")
    feasible = [row["metrics"]["Q"] for row in report["leaders"] if row["feasible"]]
    assert feasible == sorted(feasible, reverse=True)
