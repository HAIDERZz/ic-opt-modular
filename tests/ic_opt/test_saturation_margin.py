"""T17.11 (``docs/refactor/T17_11_SATURATION_MARGIN_SPEC.md``, section 4): a metric read from the operating points -- the
worst saturation margin of named transistors, the smallest ``|vds| - |vdsat|`` over them.

Every netlist and operating-point table here is made up: made-up models, instance names and values."""

from __future__ import annotations

import copy
import json

import pytest

from ic_opt.spec import Spec
from tests.ic_opt.fakes import make_spec, minimal_spec
from tests.ic_opt.test_schedule import bench

SAT = {"name": "SAT_MARGIN", "unit": "V", "testbench": "dc", "saturation_margin": {"instances": ["M1", "M2"]}}


def sat_spec(**overrides) -> dict:
    """Two testbenches: ``tb`` gives NF by an OCEAN expression, ``dc`` the saturation margin of M1 and M2 from its
    operating points. NF < 9 dB and SAT_MARGIN >= 50 mV; minimize NF."""
    d = minimal_spec(testbenches=[bench("tb"), bench("dc")],
                     metrics=[{"name": "NF", "unit": "dB", "expression": "nf()", "testbench": "tb"}, copy.deepcopy(SAT)],
                     constraints=[{"metric": "NF", "op": "lt", "value": "9 dB"},
                                  {"metric": "SAT_MARGIN", "op": "ge", "value": "50m V"}],
                     budget={"max_simulations": 1000})
    d.update(overrides)
    return d


def with_sat(**changes) -> dict:
    """``sat_spec()`` with its saturation metric changed: a value None removes that key."""
    d = sat_spec()
    for key, value in changes.items():
        if value is None:
            d["metrics"][1].pop(key, None)
        else:
            d["metrics"][1][key] = value
    return d


# -- 1. the spec --------------------------------------------------------------------------------------------------------


def test_the_good_form_loads():
    spec = Spec.model_validate(sat_spec())
    metric = spec.metrics[1]
    assert metric.saturation_margin.instances == ["M1", "M2"] and metric.testbench == "dc" and metric.expression is None
    assert spec.metrics_for("dc") == [] and [m.name for m in spec.metrics_for("tb")] == ["NF"]    # OCEAN's metrics only
    assert spec.problem()["metrics"][1] == {"name": "SAT_MARGIN", "unit": "V", "testbench": "dc", "required_signals": [],
                                            "saturation_margin": {"instances": ["M1", "M2"]}}
    names = Spec.model_validate(with_sat(saturation_margin={"instances": [" /I0/M3 ", "M\\<0\\>"]})).metrics[1]
    assert names.saturation_margin.instances == ["/I0/M3", "M\\<0\\>"]      # any name the table uses, stripped


@pytest.mark.parametrize(("changes", "message"), [
    ({"expression": "x()"}, "metric SAT_MARGIN: a saturation_margin metric takes no expression"),
    ({"device": "xfmr"}, "metric SAT_MARGIN: a saturation_margin metric takes no device"),
    ({"quantity": "Lp", "device": "xfmr"}, "metric SAT_MARGIN: a saturation_margin metric takes no device, quantity"),
    ({"testbench": None}, "metric SAT_MARGIN: a saturation_margin metric must name its testbench"),
    ({"saturation_margin": {"instances": []}}, "metric SAT_MARGIN: saturation_margin needs at least one instance"),
    ({"saturation_margin": {"instances": ["M1", "M2", "M1"]}},
     r"metric SAT_MARGIN: saturation_margin instances must be distinct \(M1 twice\)"),
    ({"saturation_margin": {"instances": ["M1", " "]}}, "metric SAT_MARGIN: saturation_margin instances must be non-empty"),
    ({"saturation_margin": {}}, r"saturation_margin\.instances\s+Field required"),
    ({"saturation_margin": {"instances": ["M1"], "vds": "0.1"}}, "Extra inputs are not permitted"),
    ({"testbench": "ac"}, "metric SAT_MARGIN references unknown testbench ac"),
])
def test_what_is_refused(changes, message):
    with pytest.raises(ValueError, match=message):
        Spec.model_validate(with_sat(**changes))


def test_it_names_its_testbench_in_a_spec_of_one_testbench_too():
    """An expression metric of a one-testbench spec is given that testbench; a saturation metric states it (section 2:
    ``testbench`` present), and then loads."""
    alone = {k: v for k, v in SAT.items() if k != "testbench"}
    with pytest.raises(ValueError, match="must name its testbench"):
        make_spec(metrics=[minimal_spec()["metrics"][0], alone])
    spec = make_spec(metrics=[minimal_spec()["metrics"][0], {**alone, "testbench": "tb"}])
    assert spec.metrics[1].testbench == "tb" and spec.metrics[0].testbench == "tb"


def test_operating_points_off_is_refused_at_load():
    d = sat_spec()
    d["simulator"]["operating_points"] = False
    with pytest.raises(ValueError, match="SAT_MARGIN reads the operating points; set simulator.operating_points true"):
        Spec.model_validate(d)
    d["metrics"] = d["metrics"][:1]
    d["constraints"] = d["constraints"][:1]
    assert Spec.model_validate(d).simulator.operating_points is False           # without the metric: as before


def test_the_metric_is_part_of_the_problem_and_a_spec_without_it_keeps_its_identity():
    """The fingerprint changes as for any metric; a spec without the field keeps its dump and both its fingerprints
    (pinned in ``test_operating_points``: the new field stays out of the legacy dump while unset)."""
    spec = make_spec()
    assert (spec.fingerprint(), spec._legacy_fingerprint()) == ("ba0f5751e8b31248", "1d1cf8fb28678108")
    assert "saturation_margin" not in json.dumps(spec.model_dump(mode="json"))
    with_it = Spec.model_validate(sat_spec())
    without = Spec.model_validate(sat_spec(metrics=sat_spec()["metrics"][:1], constraints=sat_spec()["constraints"][:1]))
    other = Spec.model_validate(with_sat(saturation_margin={"instances": ["M1", "M2", "M7"]}))
    assert len({without.fingerprint(), with_it.fingerprint(), other.fingerprint()}) == 3
    assert without._legacy_fingerprint() != with_it._legacy_fingerprint()
    assert Spec.model_validate(json.loads(json.dumps(with_it.model_dump(mode="json")))) == with_it     # round trip
