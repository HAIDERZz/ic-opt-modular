"""em.validate_profile: schema, authoring-level consistency, the site .proc (names, thicknesses, GDS layer map) and the CLI face.

Ported from em-opt's validate-profile tests (the minimal 2-metal profile breaks exactly one thing per test so
the rendered error must point at it), plus T13.10: the drawing and pin layers against the proc's ``define``
statements, and one deliberate mistake -- a layer number, a datatype, a pin pair, a thickness, a name -- reporting
exactly one locatable error.
"""
from __future__ import annotations

import copy
from pathlib import Path

import yaml
from typer.testing import CliRunner

from ic_opt.cli import app
from ic_opt.em.pcell.profile_validation import proc_layer_map, validate_profile

runner = CliRunner()
DEMO_RULE = Path(__file__).resolve().parents[3] / "src" / "ic_opt" / "em" / "pcell" / "profiles" / "demo_6m" / "rule.yaml"

MINIMAL_PROFILE: dict = {
    "schema_version": "process-rule-profile-v1",
    "process_id": "min2m",
    "units": {"length": "um"},
    "coverage": {
        "layer_inventory": "full_known_inventory",
        "layout_rules": "passive_generator_core_rules",
        "metal_width_space": ["M1", "M2"],
        "via_primitives": ["VIA1"],
        "passive_via_arrays": ["VIA1"],
        "emx_via_models": ["VIA1"],
    },
    "layer_catalog": {
        "conductors": {
            "M1": {"drawing": [1, 0], "emx_name": "M1", "class": "thin"},
            "M2": {"drawing": [2, 0], "emx_name": "M2", "class": "thin"},
        },
        "vias": {"VIA1": {"drawing": [21, 0], "emx_name": "VIA1", "connects": ["M1", "M2"]}},
        "markers": {"PASSIVE": {"drawing": [100, 0], "purpose": "passive region marker"}},
    },
    "emx_stack": {
        "geometry_scaling": 1.0,
        "conductors": {"M1": {"thickness_um": 0.1}, "M2": {"thickness_um": 0.2}},
        "via_models": {"VIA1": {"via": "VIA1", "emx_effective_size_um": 0.25}},
    },
    "layout_rules": {
        "metal_width_space": {
            "M1": {"min_width_um": 0.1, "max_width_um": 20.0, "min_space_um": 0.1},
            "M2": {"min_width_um": 0.1, "max_width_um": 20.0, "min_space_um": 0.1},
        },
        "via_primitives": {"VIA1": {"cut_size_um": [0.2, 0.2], "min_cut_space_um": 0.2, "min_enclosure_um": {"M1": 0.05, "M2": 0.05}}},
        "passive_region": {
            "marker": "PASSIVE",
            "passive_via_array_coverage": {"modeled": ["VIA1"], "not_yet_modeled": []},
            "via_array_rules": {"VIA1": {"min_count": 2, "max_space_um": 5.0}},
            "wide_parallel_spacing": [],
        },
    },
}

PROC_TEXT = """\
# fictitious EMX proc for the name / thickness / layer checks
assume microns
define fillsize = 0
define M1 = fill(l1t0, fillsize)
define M2 = fill(l2t0+l102t0, fillsize)
define VIA1 = merge(l21t0, 0.2)
conductor 0.10 0.050 M1
conductor 0.20 0.070 M2 bias m2_bias
via 21 0.9 VIA1 M1 M2
"""


def write_profile(root: Path, data: dict, profile_id: str = "min2m") -> Path:
    profile_dir = root / profile_id
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "rule.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return root


def mutated(mutate, base: dict = MINIMAL_PROFILE) -> dict:
    data = copy.deepcopy(base)
    mutate(data)
    return data


def errors(report) -> list[str]:
    """Every finding of a failed stage that is not a warning."""
    return [d for s in report.stages if s.status == "FAIL" for d in s.details if not d.startswith("warn:")]


def test_minimal_profile_passes(tmp_path: Path) -> None:
    report = validate_profile("min2m", extra_dirs=(write_profile(tmp_path, MINIMAL_PROFILE),))
    assert report.passed and report.ok
    text = report.format()
    assert "[schema] PASS" in text and "2 conductors" in text and "[consistency] PASS" in text and "result: PASS" in text


def test_missing_profile_fails_with_hint(tmp_path: Path) -> None:
    report = validate_profile("nope", extra_dirs=(tmp_path,))
    assert not report.passed
    assert "IC_OPT_PROFILE_DIRS" in report.format()


def test_yaml_parse_error_is_rendered(tmp_path: Path) -> None:
    (tmp_path / "min2m").mkdir()
    (tmp_path / "min2m" / "rule.yaml").write_text("{ not yaml: [", encoding="utf-8")
    report = validate_profile("min2m", extra_dirs=(tmp_path,))
    assert not report.passed and "YAML" in report.format()


def test_unknown_key_error_names_the_yaml_path(tmp_path: Path) -> None:
    root = write_profile(tmp_path, mutated(lambda d: d["layer_catalog"]["conductors"]["M1"].__setitem__("bogus", 1)))
    report = validate_profile("min2m", extra_dirs=(root,))
    assert not report.passed and "layer_catalog.conductors.M1.bogus" in report.format()


def test_coverage_mismatch_is_rendered(tmp_path: Path) -> None:
    root = write_profile(tmp_path, mutated(lambda d: d["coverage"].__setitem__("metal_width_space", ["M1"])))
    report = validate_profile("min2m", extra_dirs=(root,))
    assert not report.passed and "coverage.metal_width_space must match layout rules" in report.format()


def test_bad_schema_version_is_rendered(tmp_path: Path) -> None:
    root = write_profile(tmp_path, mutated(lambda d: d.__setitem__("schema_version", "v0")))
    report = validate_profile("min2m", extra_dirs=(root,))
    assert not report.passed and "schema_version" in report.format()


# ---- authoring-level consistency checks the runtime schema does not make


def test_emx_stack_conductor_not_in_catalog_fails(tmp_path: Path) -> None:
    root = write_profile(tmp_path, mutated(lambda d: d["emx_stack"]["conductors"].__setitem__("M3", {"thickness_um": 0.3})))
    report = validate_profile("min2m", extra_dirs=(root,))
    assert not report.passed and "M3" in report.format()


def test_via_connects_unknown_conductor_fails(tmp_path: Path) -> None:
    root = write_profile(tmp_path, mutated(lambda d: d["layer_catalog"]["vias"]["VIA1"].__setitem__("connects", ["M1", "M9"])))
    report = validate_profile("min2m", extra_dirs=(root,))
    assert not report.passed and "M9" in report.format()


def test_via_model_referencing_unknown_via_fails(tmp_path: Path) -> None:
    root = write_profile(tmp_path, mutated(lambda d: d["emx_stack"]["via_models"]["VIA1"].__setitem__("via", "VIA9")))
    report = validate_profile("min2m", extra_dirs=(root,))
    assert not report.passed and "VIA9" in report.format()


def test_passive_marker_missing_from_catalog_fails(tmp_path: Path) -> None:
    root = write_profile(tmp_path, mutated(lambda d: d["layout_rules"]["passive_region"].__setitem__("marker", "NOPE")))
    report = validate_profile("min2m", extra_dirs=(root,))
    assert not report.passed and "NOPE" in report.format()


def test_enclosure_key_unknown_conductor_fails(tmp_path: Path) -> None:
    root = write_profile(tmp_path, mutated(
        lambda d: d["layout_rules"]["via_primitives"]["VIA1"].__setitem__("min_enclosure_um", {"M1": 0.05, "M9": 0.05})))
    report = validate_profile("min2m", extra_dirs=(root,))
    assert not report.passed and "M9" in report.format()


def test_width_rule_for_unknown_conductor_fails(tmp_path: Path) -> None:
    def add_m3_rule(d: dict) -> None:
        d["layout_rules"]["metal_width_space"]["M3"] = {"min_width_um": 0.1, "max_width_um": 20.0, "min_space_um": 0.1}
        d["coverage"]["metal_width_space"] = ["M1", "M2", "M3"]

    report = validate_profile("min2m", extra_dirs=(write_profile(tmp_path, mutated(add_m3_rule)),))
    assert not report.passed and "M3" in report.format()


def test_process_id_folder_mismatch_warns_but_passes(tmp_path: Path) -> None:
    report = validate_profile("min2m", extra_dirs=(write_profile(tmp_path, mutated(lambda d: d.__setitem__("process_id", "other_name"))),))
    assert report.passed and "warn" in report.format() and "other_name" in report.format()


def test_non_um_units_warn_but_pass(tmp_path: Path) -> None:
    report = validate_profile("min2m", extra_dirs=(write_profile(tmp_path, mutated(lambda d: d["units"].__setitem__("length", "nm"))),))
    assert report.passed and "warn" in report.format()


# ---- the site .proc: names, thicknesses, GDS layer map


def test_proc_checks_pass_when_names_thicknesses_and_layers_agree(tmp_path: Path) -> None:
    proc = tmp_path / "demo.proc"
    proc.write_text(PROC_TEXT, encoding="utf-8")
    report = validate_profile("min2m", extra_dirs=(write_profile(tmp_path, MINIMAL_PROFILE),), proc_path=proc)
    assert report.passed, report.format()
    assert "[emx-names-vs-proc] PASS" in report.format() and "[gds-layers-vs-proc] PASS" in report.format()
    assert "3 drawing and 0 pin layers mapped" in report.format()


def test_proc_check_lists_missing_names(tmp_path: Path) -> None:
    proc = tmp_path / "demo.proc"
    proc.write_text("assume microns\nconductor 0.10 0.050 M1\n# M2 only in a comment\n", encoding="utf-8")
    report = validate_profile("min2m", extra_dirs=(write_profile(tmp_path, MINIMAL_PROFILE),), proc_path=proc)
    assert not report.passed
    assert "M2" in report.format() and "VIA1" in report.format()


def test_proc_checks_skipped_without_proc(tmp_path: Path) -> None:
    report = validate_profile("min2m", extra_dirs=(write_profile(tmp_path, MINIMAL_PROFILE),))
    assert "[emx-names-vs-proc] SKIPPED" in report.format() and "[gds-layers-vs-proc] SKIPPED" in report.format()


def test_proc_without_defines_skips_the_layer_check(tmp_path: Path) -> None:
    proc = tmp_path / "demo.proc"
    proc.write_text("\n".join(line for line in PROC_TEXT.splitlines() if not line.startswith("define")), encoding="utf-8")
    report = validate_profile("min2m", extra_dirs=(write_profile(tmp_path, MINIMAL_PROFILE),), proc_path=proc)
    assert report.passed
    stage = next(s for s in report.stages if s.name == "gds-layers-vs-proc")
    assert stage.status == "SKIPPED" and "no define statements" in stage.details[0]


def test_proc_layer_map_resolves_defines_that_name_other_defines() -> None:
    text = """define fillsize = 0
define ctm = l77t0
define via7raw = merge(count(l57t0), 0.68)   # a comment l99t0
define via7 = via7raw-ctm
define metal1 = l31t0+l131t0
"""
    layers = proc_layer_map(text)
    assert layers["metal1"] == {(31, 0), (131, 0)} and layers["via7raw"] == {(57, 0)}
    assert layers["via7"] == {(57, 0), (77, 0)} and layers["fillsize"] == set()


def test_define_without_a_layer_term_warns_but_passes(tmp_path: Path) -> None:
    proc = tmp_path / "demo.proc"
    proc.write_text(PROC_TEXT.replace("define M1 = fill(l1t0, fillsize)", "define M1 = fill(metal_one, fillsize)"), encoding="utf-8")
    report = validate_profile("min2m", extra_dirs=(write_profile(tmp_path, MINIMAL_PROFILE),), proc_path=proc)
    assert report.passed
    assert "warn: M1: the define of M1 names no l<layer>t<datatype> term" in report.format()


def test_name_used_in_the_proc_but_never_defined_fails(tmp_path: Path) -> None:
    proc = tmp_path / "demo.proc"
    proc.write_text(PROC_TEXT.replace("define VIA1 = merge(l21t0, 0.2)\n", ""), encoding="utf-8")
    report = validate_profile("min2m", extra_dirs=(write_profile(tmp_path, MINIMAL_PROFILE),), proc_path=proc)
    assert errors(report) == ["VIA1: VIA1 has no 'define VIA1 = ...' in demo.proc"]


# ---- T13.10 acceptance: demo_6m against its own proc; one deliberate mistake, one locatable error


def demo_proc(profile: dict) -> str:
    """The proc demo_6m would have: one define per conductor (drawing + pin layer) / via, conductor thicknesses from its stack."""
    catalog, stack = profile["layer_catalog"], profile["emx_stack"]["conductors"]
    lines = ["assume microns", "define fillsize = 0"]
    lines += [f"define {r['emx_name']} = fill(l{r['drawing'][0]}t{r['drawing'][1]}+l{r['pin'][0]}t{r['pin'][1]}, fillsize)"
              for r in catalog["conductors"].values()]
    lines += [f"define {r['emx_name']} = merge(l{r['drawing'][0]}t{r['drawing'][1]}, 0.2)" for r in catalog["vias"].values()]
    lines += [f"conductor {stack[name]['thickness_um']} 0.05 {r['emx_name']}" for name, r in catalog["conductors"].items()]
    lines += [f"via 0.5 0.9 {r['emx_name']} {r['connects'][0]} {r['connects'][1]}" for r in catalog["vias"].values()]
    return "\n".join(lines) + "\n"


def check_demo(tmp_path: Path, mutate=None):
    base = yaml.safe_load(DEMO_RULE.read_text(encoding="utf-8"))
    proc = tmp_path / "demo_6m.proc"
    proc.write_text(demo_proc(base), encoding="utf-8")
    data = mutated(mutate, base) if mutate else base
    root = tmp_path / "profiles"
    write_profile(root, data, "demo_6m")
    return validate_profile("demo_6m", extra_dirs=(root,), proc_path=proc)


def test_demo_6m_passes_every_stage_against_its_proc(tmp_path: Path) -> None:
    report = check_demo(tmp_path)
    assert report.passed, report.format()
    assert [s.status for s in report.stages] == ["PASS", "PASS", "PASS", "PASS", "SKIPPED"]
    assert "11 drawing and 6 pin layers mapped by the defines of demo_6m.proc" in report.format()


def test_wrong_layer_number_reports_one_error(tmp_path: Path) -> None:
    report = check_demo(tmp_path, lambda d: d["layer_catalog"]["conductors"]["M3"].__setitem__("drawing", [163, 0]))
    assert errors(report) == ["M3: drawing layer 163/0 is not in the define of M3 (63/0, 63/2) -- EMX would not see this geometry"]


def test_wrong_via_datatype_reports_one_error(tmp_path: Path) -> None:
    report = check_demo(tmp_path, lambda d: d["layer_catalog"]["vias"]["VIA4"].__setitem__("drawing", [74, 20]))
    assert errors(report) == ["VIA4: drawing layer 74/20 is not in the define of VIA4 (74/0) -- EMX would not see this geometry"]


def test_wrong_thickness_reports_one_error(tmp_path: Path) -> None:
    report = check_demo(tmp_path, lambda d: d["emx_stack"]["conductors"]["M5"].__setitem__("thickness_um", 1.2))
    assert errors(report) == ["emx_stack vs demo_6m.proc: M5: profile thickness 1.2 um, .proc 0.9 um"]


def test_wrong_name_reports_one_error(tmp_path: Path) -> None:
    report = check_demo(tmp_path, lambda d: d["layer_catalog"]["conductors"]["M4"].__setitem__("emx_name", "MET4"))
    assert errors(report) == ["emx_name not found in demo_6m.proc: MET4"]


def test_wrong_pin_datatype_reports_one_error(tmp_path: Path) -> None:
    report = check_demo(tmp_path, lambda d: d["layer_catalog"]["conductors"]["M6"].__setitem__("pin", [66, 20]))
    assert errors(report) == ["M6: pin layer 66/20 is not in the define of M6 (66/0, 66/2) -- EMX would not find the port labels"]


# ---- the CLI face: ic-opt call em.validate_profile <profile dir>


def test_call_validates_a_profile_directory(tmp_path: Path) -> None:
    proc = tmp_path / "demo.proc"
    proc.write_text(PROC_TEXT, encoding="utf-8")
    write_profile(tmp_path, MINIMAL_PROFILE)
    result = runner.invoke(app, ["call", "em.validate_profile", str(tmp_path / "min2m"), f"proc={proc}"])
    assert result.exit_code == 0, result.output
    assert "[gds-layers-vs-proc] PASS" in result.output and "result: PASS" in result.output


def test_call_exits_one_with_the_yaml_path_of_a_broken_profile(tmp_path: Path) -> None:
    write_profile(tmp_path, mutated(lambda d: d["layer_catalog"]["conductors"]["M1"].__setitem__("bogus", 1)))
    result = runner.invoke(app, ["call", "em.validate_profile", str(tmp_path / "min2m")])
    assert result.exit_code == 1
    assert "layer_catalog.conductors.M1.bogus" in result.output and "result: FAIL" in result.output


def test_call_exits_one_on_a_layer_the_proc_does_not_map(tmp_path: Path) -> None:
    proc = tmp_path / "demo.proc"
    proc.write_text(PROC_TEXT.replace("l2t0+l102t0", "l102t0"), encoding="utf-8")
    write_profile(tmp_path, MINIMAL_PROFILE)
    result = runner.invoke(app, ["call", "em.validate_profile", str(tmp_path / "min2m"), f"proc={proc}"])
    assert result.exit_code == 1
    assert "M2: drawing layer 2/0 is not in the define of M2 (102/0)" in result.output
