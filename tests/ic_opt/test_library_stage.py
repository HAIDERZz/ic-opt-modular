"""T13.5: the Predict stage, surrogate_pipeline and the lib_design recipe on the synthetic library (fake end to end)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ic_opt.blocks.evaluate import evaluate
from ic_opt.eval.stage import pipeline_fingerprint
from ic_opt.library import query, stage
from ic_opt.recipe import PLAN_MODE, load_recipe, load_run
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FakeSpectreExecutor
from tests.ic_opt.library_fixtures import build_library, params, truth
from tests.ic_opt.test_library import part_spec

pytest.importorskip("klayout.db")


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
    lib = query.Library(library)
    spec = design_spec("predict")
    assert stage.match_stratum(lib, spec.devices[0]) == "ind_demo"
    pipeline = stage.surrogate_pipeline(spec, lib)
    assert [s.name for s in pipeline] == ["pcell", "predict"]
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims")
    obs = evaluate(spec, [Point({"outer_diameter_um": "150", "width_um": "5", "spacing_um": "3", "turns": "2"}, "user"),
                          Point({"outer_diameter_um": "155", "width_um": "4.5", "spacing_um": "2.5", "turns": "2"}, "user")], ex, store, pipeline=pipeline)
    measured, predicted = obs
    assert ex.emx_runs == 0 and {o.status for o in obs} == {"ok"}
    assert measured.metrics["L"] == lib.dataset("ind_demo").find(params(150, 5, 3, 2)).values["Lp_lf"]
    assert predicted.metrics["L"] == pytest.approx(truth(155, 4.5, 2.5, 2)["Lp_lf"], rel=2e-2) and predicted.metrics["L10"] > 0
    answer = json.loads((store.project_dir / predicted.children["ind/nominal"].sim_dir / "predictions.json").read_text())
    assert answer["quantities"]["Lp_lf"]["status"] == "predicted" and answer["quantities"]["Lp_lf"]["lo"] < predicted.metrics["L"]
    assert predicted.pipeline_fingerprint == pipeline_fingerprint(pipeline) != measured.spec_fingerprint


def test_predict_refuses_what_the_library_cannot_vouch_for(library, tmp_path):
    lib = query.Library(library)
    spec = design_spec("refuse")
    d = spec.model_dump(mode="json")
    d["variables"][0]["upper"] = "260"
    spec = Spec.model_validate(d)
    store = RunStore(tmp_path)
    obs = evaluate(spec, [Point({"outer_diameter_um": "250", "width_um": "5", "spacing_um": "3", "turns": "2"}, "user")],
                   FakeSpectreExecutor(store.root / "sims"), store, pipeline=stage.surrogate_pipeline(spec, lib))
    assert obs[0].status == "failed:predict" and any("out_of_domain" in i and "outside the measured range" in i for i in obs[0].issues)
    other_metal = design_spec("m5", metal="5")
    with pytest.raises(ValueError, match="0 strata match"):
        stage.match_stratum(lib, other_metal.devices[0])
    wrong = RunStore(tmp_path / "wrong")
    obs = evaluate(other_metal, [Point({"outer_diameter_um": "150", "width_um": "5", "spacing_um": "3", "turns": "2"}, "user")],
                   FakeSpectreExecutor(wrong.root / "sims"), wrong, pipeline=stage.surrogate_pipeline(other_metal, lib, {"ind": "ind_demo"}))
    assert obs[0].status == "failed:predict" and "is not stratum ind_demo's device" in " ".join(obs[0].issues)


def test_lib_design_recipe_optimizes_on_predictions_and_reports_leaders(library, tmp_path):
    project = tmp_path / "design"
    project.mkdir()
    (project / "spec.yaml").write_text(yaml.safe_dump(design_spec("design").model_dump(mode="json")), encoding="utf-8")
    run = load_run(project)
    main = load_recipe("lib_design")
    token = PLAN_MODE.set(True)
    try:
        main(run, library=str(library), budget=12, batch=6, strategy="random", top=3)
    finally:
        PLAN_MODE.reset(token)
    assert run.store.observations() == []                                         # plan mode starts nothing
    main(run, library=str(library), budget=24, batch=12, strategy="random", top=3, seed=3)
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
