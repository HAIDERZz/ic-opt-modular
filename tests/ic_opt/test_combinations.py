"""N-106 sections 1 and 2: a pcell device takes its allowed combinations from a file (``Device.combinations``, which
``lib.region out=`` writes), and ``space.tables`` turns it into a table over the device's variables, exactly as a library
device's rows are (T18.2A): snap, check and grid_size honour it; a fixed field filters the rows; a value off its variable's
grid, a dim nothing takes and a variable the file lacks are refused; a library device takes no file; the file is part of
no identity. The oracle tables are written from the file's rows by hand, not by the code."""

from __future__ import annotations

import copy
import json
import re

import numpy as np
import pytest
import yaml
from pydantic import ValidationError

from ic_opt import space
from ic_opt.library import combinations, link
from ic_opt.library.link import LinkError
from ic_opt.spec import Spec, load_spec
from tests.ic_opt.fakes import minimal_spec
from tests.ic_opt.test_spec_library import EM_CIRCUIT, library_device, library_spec

DIMS = ["outer_diameter_um", "width_um", "spacing_um", "turns"]
WIDTHS = [2.0, 2.4, 2.8, 3.2, 3.6, 4.0]
# A band: for the i-th width the outer diameters 52 + 4 i and 56 + 4 i (spacing 2, one turn); then two rows at spacing 2.1
# and one with two turns, which the device's fixed spacing 2 and turns 1 drop.
BAND = [[52.0 + 4 * i + d, w, 2.0, 1] for i, w in enumerate(WIDTHS) for d in (0, 4)]
OTHERS = [[60.0, 2.0, 2.1, 1], [64.0, 2.4, 2.1, 1], [28.0, 2.0, 2.0, 2]]
LEVELS = {(i, i + d) for i in range(6) for d in (0, 1)}                # (ind.w, ind.od) level indices of BAND


@pytest.fixture(autouse=True)
def forget():
    link.clear()
    yield
    link.clear()


def band_file(tmp_path, rows=BAND + OTHERS, dims=DIMS, name="band.json"):
    return combinations.write(tmp_path / name, dims, rows, {"library": "/libraries/demo", "stratum": "ind_demo"})


def pcell_spec(path, *, fixed=None, variables=None, od=("52", "76", "4"), **device) -> Spec:
    """F and W of the circuit, the device's ind.w (2 to 4 step 0.4: 6 levels) and ind.od (7 levels at the default),
    interleaved so that the spec's order (ind.w before ind.od) is not the file's."""
    d = minimal_spec()
    d["variables"] = [d["variables"][0], {"name": "ind.w", "kind": "continuous_step", "lower": "2", "upper": "4", "step": "0.4"},
                      d["variables"][1], {"name": "ind.od", "kind": "continuous_step", "lower": od[0], "upper": od[1], "step": od[2]}]
    d["devices"] = [{"id": "ind", "generator": "clean_port_ind_sym", "profile": "demo_6m", "ports": ["P1", "N1"],
                     "fixed": {"turns": 1, "spacing_um": 2.0, "opening_um": 8.0} if fixed is None else fixed,
                     "variables": variables or {"outer_diameter_um": "ind.od", "width_um": "ind.w"},
                     "combinations": str(path), **device}]
    return Spec.model_validate(d)


def level_of(spec, name, text) -> int:
    v = next(v for v in spec.variables if v.name == name)
    return int((space.parse_scalar(text)[0] - space.parse_scalar(v.lower)[0]) / space.parse_scalar(v.step)[0])


def device_levels(spec, params) -> tuple[int, int]:
    return level_of(spec, "ind.w", params["ind.w"]), level_of(spec, "ind.od", params["ind.od"])


def test_a_pcell_device_s_file_is_a_table_of_allowed_combinations_the_space_honours(tmp_path):
    path = band_file(tmp_path)
    spec = pcell_spec(path)
    (table,) = space.tables(spec)
    assert table.names == ("ind.w", "ind.od") and table.label == "device ind (combinations band.json)"
    assert set(table.levels) == LEVELS and len(table.levels) == 12
    assert space.grid_size(spec) == 6 * 4 * 12                          # F x W x the 12 combinations, of 6 x 4 x 6 x 7
    plain = spec.model_copy(update={"devices": [spec.devices[0].model_copy(update={"combinations": None})]})
    assert space.tables(plain) == [] and space.grid_size(plain) == 1008
    lows, highs = (np.array(b) for b in space.bounds(spec))
    for raw in (lows + np.random.default_rng(1).random((200, 4)) * (highs - lows)).tolist():
        params, free = space.snap(spec, raw), space.snap(plain, raw)
        assert device_levels(spec, params) in LEVELS
        assert (params["F"], params["W"]) == (free["F"], free["W"])            # the free variables as without the file
        space.check(spec, params)
    valid = {"F": "22", "ind.w": "2.8", "W": "0.8u", "ind.od": "64"}           # width level 2, diameter level 3
    assert space.snap(spec, space.to_raw(spec, valid)) == valid
    with pytest.raises(ValueError, match=re.escape("device ind (combinations band.json): ind.w=2 ind.od=76 is not one of its "
                                                   "combinations; the nearest one is ind.w=2.8 ind.od=64")):
        space.check(spec, {**valid, "ind.w": "2", "ind.od": "76"})
    assert combinations.lines(spec) == [
        f"device ind: 12 allowed combinations of ind.w, ind.od from {path} (3 rows dropped by fixed spacing_um=2, turns=1)",
        "search space: 288 valid points (the variables' grids hold 1008)"]


def test_a_fixed_field_keeps_only_the_rows_at_its_value(tmp_path):
    path = band_file(tmp_path)
    wide = pcell_spec(path, fixed={"turns": 1, "spacing_um": 2.1, "opening_um": 8.0})
    (table,) = space.tables(wide)
    assert set(table.levels) == {(0, 2), (1, 3)}                               # (2.0, 60) and (2.4, 64) at spacing 2.1
    assert combinations.resolve(wide)["ind"].dropped == 13
    assert combinations.lines(wide)[0].endswith("(13 rows dropped by fixed spacing_um=2.1, turns=1)")
    nothing = pcell_spec(path, fixed={"turns": 1, "spacing_um": 2.5})
    with pytest.raises(LinkError, match=re.escape("device ind (combinations band.json): no combination is left: the file holds "
                                                  "15 rows, none at fixed spacing_um=2.5, turns=1 (spacing_um 2 to 2.1; turns 1 to 2)")):
        space.tables(nothing)


def test_rows_on_one_combination_collapse_and_a_file_without_fixed_dims_says_nothing_dropped(tmp_path):
    path = band_file(tmp_path, rows=[[52.0, 2.0], [52.0, 2.0], [56.0, 2.0]], dims=["outer_diameter_um", "width_um"])
    spec = pcell_spec(path)
    (table,) = space.tables(spec)
    assert table.levels == ((0, 0), (0, 1))
    assert combinations.lines(spec)[0] == f"device ind: 2 allowed combinations of ind.w, ind.od from {path}"


def test_a_value_off_its_variable_s_grid_is_refused_naming_the_variable_the_step_and_the_nearest_levels(tmp_path):
    path = band_file(tmp_path)
    with pytest.raises(LinkError, match=re.escape("device ind (combinations band.json): row 2: outer_diameter_um=56 is not on "
                                                  "the grid of ind.od (52 to 76 step 8); the nearest levels are 52 and 60")):
        space.tables(pcell_spec(path, od=("52", "76", "8")))
    with pytest.raises(LinkError, match=re.escape("row 1: outer_diameter_um=52 is not on the grid of ind.od (56 to 76 step 4); "
                                                  "the nearest level is 56")):
        space.tables(pcell_spec(path, od=("56", "76", "4")))


@pytest.mark.parametrize(("change", "message"), [
    ({"fixed": {"turns": 1, "opening_um": 8.0}},
     ("the file's dim spacing_um is neither a variable of the device (outer_diameter_um -> ind.od, width_um -> ind.w) nor "
      "one of its fixed fields")),
    ({"variables": {"outer_diameter_um": "ind.od", "opening_um": "ind.w"}},
     "the file's dim width_um is neither a variable of the device"),
    ({"variables": {"outer_diameter_um": "ind.od", "width_um": "ind.w", "lead_length_um": "W"}},
     ("the device maps lead_length_um to W, and the file has no dim lead_length_um (its dims: outer_diameter_um, "
      "width_um, spacing_um, turns)")),
])
def test_a_dim_nothing_takes_and_a_variable_the_file_lacks_are_refused(tmp_path, change, message):
    spec = pcell_spec(band_file(tmp_path), **{"fixed": None, "variables": None, **change})
    with pytest.raises(LinkError, match=re.escape(message)):
        space.tables(spec)


@pytest.mark.parametrize(("text", "message"), [
    ("{}", "not an ic-opt-combinations-v1 document"),
    ('{"format": "ic-opt-combinations-v1", "dims": ["a", "a"], "rows": []}', "dims must be a list of distinct"),
    ('{"format": "ic-opt-combinations-v1", "dims": ["a", "b"], "rows": [[1, 2], [3]]}', "rows must be lists of 2 numbers"),
    ('{"format": "ic-opt-combinations-v1", "dims": ["a", "b"], "rows": [[1, 2, 3]]}', "rows must be lists of 2 numbers"),
])
def test_a_file_that_is_no_combinations_document_is_refused(tmp_path, text, message):
    (tmp_path / "bad.json").write_text(text, encoding="utf-8")
    with pytest.raises(LinkError, match=re.escape(message)):
        space.tables(pcell_spec(tmp_path / "bad.json"))


def test_a_library_device_takes_no_combinations_file():
    with pytest.raises(ValidationError, match="combinations and library exclude each other -- a library device's "
                                              r"combinations are the rows of its table \(xfm_demo\)"):
        Spec.model_validate(library_spec({**library_device(), "combinations": "/x/band.json"}))


def test_the_file_is_part_of_no_identity_and_a_spec_without_it_keeps_its_dump(tmp_path):
    base = Spec.model_validate(copy.deepcopy(EM_CIRCUIT))
    d = copy.deepcopy(EM_CIRCUIT)
    d["devices"][0]["combinations"] = str(tmp_path / "band.json")
    named = Spec.model_validate(d)
    assert (named.fingerprint(), named._legacy_fingerprint()) == (base.fingerprint(), base._legacy_fingerprint())
    assert named.problem() == base.problem() and "combinations" not in base.model_dump(mode="json")["devices"][0]
    assert named.model_dump(mode="json")["devices"][0]["combinations"] == str(tmp_path / "band.json")


def test_a_relative_path_is_the_spec_s_directory_s(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    band_file(project)
    d = pcell_spec(project / "band.json").model_dump(mode="json")
    d["devices"][0]["combinations"] = "band.json"
    (project / "spec.yaml").write_text(yaml.safe_dump(d), encoding="utf-8")
    monkeypatch.chdir(tmp_path)                                         # not the project: the path is the spec's
    spec = load_spec(project / "spec.yaml")
    assert spec.devices[0].combinations == str(project / "band.json") and len(space.tables(spec)[0].levels) == 12
    d["devices"][0]["combinations"] = str(project / "band.json")         # absolute: as written
    (project / "spec.yaml").write_text(yaml.safe_dump(d), encoding="utf-8")
    assert load_spec(project / "spec.yaml").devices[0].combinations == str(project / "band.json")


def test_a_process_reads_a_file_once_until_it_is_told_to_forget(tmp_path):
    path = band_file(tmp_path)
    spec = pcell_spec(path)
    first = space.tables(spec)
    path.write_text(json.dumps({"format": combinations.FORMAT, "dims": DIMS, "rows": BAND[:2]}), encoding="utf-8")
    assert space.tables(spec) == first                                   # a run sees one table for its whole life
    link.clear()
    assert set(space.tables(spec)[0].levels) == {(0, 0), (0, 1)}
    combinations.write(path, DIMS, BAND[:1], {})                         # what this process writes, it reads again
    assert space.tables(spec)[0].levels == ((0, 0),)
