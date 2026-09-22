from pathlib import Path

import pytest
import yaml

from ic_opt import objective, space
from ic_opt.spec import Spec, load_spec

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "spec.yaml"


def minimal(**overrides) -> dict:
    base = {
        "project": "demo",
        "testbenches": [{"id": "tb", "maestro_point_root": "/x", "virtuoso_library": "lib", "cell": "c", "test_name": "t"}],
        "variables": [
            {"name": "F", "kind": "integer", "lower": "20", "upper": "30", "step": "2"},
            {"name": "W", "kind": "continuous_step", "lower": "0.6u", "upper": "1.2u", "step": "0.2u"},
        ],
        "metrics": [{"name": "NF", "unit": "dB", "expression": 'value(getData("NF"))'}],
        "constraints": [{"metric": "NF", "op": "lt", "value": "9 dB"}],
        "objective": {"direction": "minimize", "expression": "NF"},
        "simulator": {"parallel_jobs": 2, "timeout_s": 60},
        "budget": {"max_simulations": 10},
    }
    base.update(overrides)
    return base


# -- spec ---------------------------------------------------------------------

def test_example_spec_loads_and_fingerprints_stably():
    spec = load_spec(EXAMPLE)
    assert spec.testbench_ids == ["cg_nf", "iip3", "p1db"]
    assert spec.corner_ids == ["tt", "ss", "ff"]
    assert [m.name for m in spec.metrics_for("cg_nf")] == ["BW", "MAX_GAIN", "NF_3G"]
    assert spec.fingerprint() == Spec.model_validate(yaml.safe_load(EXAMPLE.read_text())).fingerprint()


def test_single_testbench_metric_gets_the_default_testbench():
    spec = Spec.model_validate(minimal())
    assert spec.metrics[0].testbench == "tb"
    assert spec.corner_ids == [None]


def test_spec_rejects_bad_cross_references():
    with pytest.raises(ValueError, match="unknown metric"):
        Spec.model_validate(minimal(objective={"direction": "minimize", "expression": "GAIN"}))
    with pytest.raises(ValueError, match="unknown metric"):
        Spec.model_validate(minimal(constraints=[{"metric": "GAIN", "op": "lt", "value": "1"}]))
    with pytest.raises(ValueError, match="divisible"):
        Spec.model_validate(minimal(variables=[{"name": "F", "kind": "integer", "lower": "20", "upper": "31", "step": "2"}]))
    with pytest.raises(ValueError, match="must name its testbench"):
        two = minimal()
        two["testbenches"].append({**two["testbenches"][0], "id": "tb2"})
        Spec.model_validate(two)


# -- space --------------------------------------------------------------------

def test_snap_and_check_round_trip():
    spec = Spec.model_validate(minimal())
    assert space.snap(spec, [23.4, 0.71]) == {"F": "24", "W": "0.8u"}
    assert space.snap(spec, [-5, 9.9]) == {"F": "20", "W": "1.2u"}          # clamped
    space.check(spec, {"F": "24", "W": "0.8u"})
    assert space.to_raw(spec, {"F": "24", "W": "0.8u"}) == [24.0, 0.8]
    assert space.grid_size(spec) == 6 * 4
    assert space.bounds(spec) == ([20.0, 0.6], [30.0, 1.2])
    with pytest.raises(ValueError, match="aligned"):
        space.check(spec, {"F": "21", "W": "0.8u"})
    with pytest.raises(ValueError, match="unit suffix"):
        space.check(spec, {"F": "20", "W": "800n"})


def test_point_key_is_order_independent():
    a = space.Point({"F": "20", "W": "0.6u"})
    b = space.Point({"W": "0.6u", "F": "20"}, origin="suggest:turbo")
    assert a.key == b.key and a != b


# -- objective ----------------------------------------------------------------

def test_expression_contract():
    assert objective.expression_issues("min(NF, 3) + ln(BW)", {"NF", "BW"}) == []
    assert objective.expression_issues("NF_3g", {"NF_3G"}) == ["unknown metric NF_3g; did you mean NF_3G?"]
    assert objective.expression_issues("__import__('os')", {"NF"})[0].startswith("unsupported")
    assert objective.evaluate_expression("max(1, NF) * 2", {"NF": 3.0}) == 6.0


def test_evaluate_reports_feasibility_and_penalty():
    spec = Spec.model_validate(minimal())
    ok = objective.evaluate(spec, {"NF": 8.0})
    assert (ok.status, ok.feasible, ok.fom, ok.objective) == ("ok", True, 8.0, 8.0)
    bad = objective.evaluate(spec, {"NF": 10.0})
    assert bad.status == "constraint_failed" and not bad.feasible and bad.fom == 10.0 and bad.objective is None
    assert bad.constraint_penalty == pytest.approx((1 / 9) ** 2)
    assert objective.evaluate(spec, {}).status == "metric_failed"
    maximize = Spec.model_validate(minimal(objective={"direction": "maximize", "expression": "NF"}))
    assert objective.evaluate(maximize, {"NF": 8.0}).objective == -8.0
