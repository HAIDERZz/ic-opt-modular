"""T13.3: lib.query / lib.coverage / lib.load on a synthetic library, and `ic-opt call` on a library root."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ic_opt.cli import app
from ic_opt.library import gp
from ic_opt.library import query as q
from tests.ic_opt.library_fixtures import STOP_GHZ, build_library, params, truth


@pytest.fixture(scope="module")
def library(tmp_path_factory) -> Path:
    return build_library(tmp_path_factory.mktemp("qlib"))


def test_measured_at_library_points_and_calibrated_predictions_between(library):
    lib = q.Library(library)
    hit = q.query(lib, "ind_demo", params(150, 5, 3, 2))
    assert hit["measured"]["part"] == "nt2" and {v["status"] for v in hit["quantities"].values()} == {"measured"}
    assert hit["quantities"]["Lp_lf"]["value"] == lib.dataset("ind_demo").find(params(150, 5, 3, 2)).values["Lp_lf"]
    between = q.query(lib, "ind_demo", params(155, 4.5, 2.5, 2), ["Lp_lf", "Qp_peak"])
    lp = between["quantities"]["Lp_lf"]
    assert between["measured"] is None and lp["status"] == "predicted" and lp["unit"] == "H" and lp["k_scale"] >= 1.0
    assert lp["lo"] < lp["value"] < lp["hi"] and len(lp["nearest"]) == 3 and lp["nearest"][0]["scaled_distance"] > 0
    assert lp["value"] == pytest.approx(truth(155, 4.5, 2.5, 2)["Lp_lf"], rel=2e-2)
    single = q.query(lib, "ind_demo", params(135, 5.5, 2, 1), ["Lp_lf"])["quantities"]["Lp_lf"]
    assert single["status"] == "predicted" and single["value"] == pytest.approx(truth(135, 5.5, 2, 1)["Lp_lf"], rel=2e-2)


def test_domain_answers_carry_the_reason_and_evidence(library):
    lib = q.Library(library)
    answers = {
        "range": q.query(lib, "ind_demo", params(250, 5, 2, 2), ["Lp_lf"]),
        "fixed spacing": q.query(lib, "ind_demo", params(135, 5, 3, 1), ["Lp_lf"]),
        "half turn": q.query(lib, "ind_demo", params(135, 5, 2, 1.5), ["Lp_lf"]),
    }
    got = {k: (a["quantities"]["Lp_lf"]["status"], a["quantities"]["Lp_lf"]["criterion"]) for k, a in answers.items()}
    assert got == {"range": ("out_of_domain", 1), "fixed spacing": ("out_of_domain", 1), "half turn": ("out_of_domain", 2)}
    assert "spacing_um is 2 for every row at turns=1" in answers["fixed spacing"]["quantities"]["Lp_lf"]["reason"]
    assert answers["range"]["quantities"]["Lp_lf"]["fill_points"][1]["outer_diameter_um"] == 200.0
    small = q.query(lib, "ind_demo", params(85, 5.5, 2, 1), ["SRF_p"])["quantities"]["SRF_p"]
    assert truth(85, 5.5, 2, 1)["SRF_p"] is None
    assert small["status"] == "above_sweep" and small["lower_bound"] == STOP_GHZ * 1e9 and small["value"] is None
    big = q.query(lib, "ind_demo", params(185, 5.5, 2.5, 2), ["SRF_p"])["quantities"]["SRF_p"]
    assert big["status"] == "predicted" and big["value"] == pytest.approx(truth(185, 5.5, 2.5, 2)["SRF_p"], rel=3e-2)
    shaky = q.query(lib, "ind_demo", params(155, 4.5, 2.5, 2), ["Lp_lf"], rel_sigma_max=1e-12)["quantities"]["Lp_lf"]
    assert shaky["status"] == "uncertain" and shaky["value"] > 0 and shaky["rel_sigma"] > 1e-12
    with pytest.raises(ValueError, match="missing"):
        q.query(lib, "ind_demo", {"outer_diameter_um": 100}, ["Lp_lf"])
    with pytest.raises(ValueError, match="no quantities"):
        q.query(lib, "ind_demo", params(150, 5, 3, 2), ["Ls_lf"])


def test_calibration_is_cached_and_never_narrows(library, monkeypatch):
    first = q.Library(library).model("ind_demo", "Qp_peak")
    files = list((library / ".cache").glob("calibration-ind_demo-Qp_peak-*.json"))
    assert len(files) == 1 and first.calibration["k_scale"] >= 1.0 and first.calibration["source"] == "holdout 5x20%"
    monkeypatch.setattr(gp, "holdout", lambda *a, **k: pytest.fail("calibration must come from the cache"))
    again = q.Library(library).model("ind_demo", "Qp_peak")
    assert again.calibration == first.calibration and again.gp.k_scale == first.calibration["k_scale"]
    assert q.Library(library, calibrate=False).model("ind_demo", "Qp_peak").calibration == {"k_scale": 1.0, "source": "off"}


def test_coverage_and_load(library):
    lib = q.Library(library)
    c = q.coverage(lib, "ind_demo")
    assert c["rows"] == 39 + 66 and c["parts"] == {"nt1": 39, "nt2": 66} and c["levels"] == {1: 39, 2: 66}
    assert c["dims"]["outer_diameter_um"] == {"min": 80.0, "max": 200.0, "distinct": 13} and c["quantities"]["Lp_lf"]["rows"] == 105
    assert c["quantities"]["SRF_p"]["unit"] == "Hz" and 0 < c["quantities"]["SRF_p"]["rows"] < 105
    loaded = q.load(lib)
    assert loaded["ind_demo"]["rows"] == 105 and loaded["ind_demo"]["passive_rows"] == 105


def test_call_on_a_library_root_prints_json(library):
    runner = CliRunner()
    out = runner.invoke(app, ["call", "lib.query", str(library), "stratum=ind_demo", 'params={"outer_diameter_um":150,"width_um":5,"spacing_um":3,"turns":2}',
                              "quantities=Lp_lf,SRF_p"])
    assert out.exit_code == 0, out.output
    body = json.loads(out.output)
    assert body["measured"]["obs_id"] and set(body["quantities"]) == {"Lp_lf", "SRF_p"}
    cov = runner.invoke(app, ["call", "lib.coverage", str(library), "stratum=ind_demo"])
    assert cov.exit_code == 0 and json.loads(cov.output)["rows"] == 105
    bad = runner.invoke(app, ["call", "lib.coverage", str(library), "stratum=nope"])
    assert bad.exit_code == 2 and "no stratum 'nope'" in bad.output
