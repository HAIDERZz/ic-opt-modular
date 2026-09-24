import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from ic_opt.em.pcell.process_rules import (
    PROFILE_DIRS_ENV_VAR,
    ProcessRuleProfile,
    get_process_rule_profile,
    resolve_conductor,
    resolve_marker,
    resolve_metal_width_space_rule,
    resolve_passive_via_array_rule,
    resolve_via,
    resolve_via_primitive_rule,
    resolve_via_stack,
)
from tests.ic_opt.pcell.conftest import PACKAGE_DIR, profile_path, requires_profile

# The private n28_1p10m profile is read from IC_OPT_PROFILE_DIRS; its
# inventory and rule values are compared against its own rule.yaml, never
# written here (private process rules stay out of the repository). The
# search-order tests copy the packaged public demo_6m profile instead.
pytestmark = requires_profile("n28_1p10m")
_N28_PROFILE_PATH = profile_path("n28_1p10m") or Path("n28_1p10m/rule.yaml")
_DEMO_PROFILE_PATH = PACKAGE_DIR / "profiles" / "demo_6m" / "rule.yaml"


def _load_n28_rule_data() -> dict:
    return yaml.safe_load(_N28_PROFILE_PATH.read_text(encoding="utf-8"))


def _load_demo_rule_data() -> dict:
    return yaml.safe_load(_DEMO_PROFILE_PATH.read_text(encoding="utf-8"))


def test_get_process_rule_profile_loads_n28_profile() -> None:
    profile = get_process_rule_profile("n28_1p10m")

    assert profile.schema_version == "process-rule-profile-v1"
    assert profile.process_id == "n28_1p10m"
    assert profile.units.length == "um"


def test_unknown_process_rule_profile_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported process rule profile"):
        get_process_rule_profile("unknown_process")


def _write_profile(directory: Path, profile_id: str, *, process_id: str) -> None:
    profile_dir = directory / profile_id
    profile_dir.mkdir(parents=True, exist_ok=True)
    data = _load_demo_rule_data()
    data["process_id"] = process_id
    (profile_dir / "rule.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_profile_dir_env_var_hit_wins_over_packaged(tmp_path, monkeypatch) -> None:
    # An IC_OPT_PROFILE_DIRS hit must be preferred over the packaged profile
    # with the same id (demo_6m ships in the package; see
    # test_process_rule_loader_is_generic for the packaged-fallback side of
    # this search order, using a synthetic packaged id).
    _write_profile(tmp_path, "demo_6m", process_id="env_dir_wins")
    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(tmp_path))

    profile = get_process_rule_profile("demo_6m")

    assert profile.process_id == "env_dir_wins"


def test_profile_dirs_searched_in_order_first_match_wins(tmp_path, monkeypatch) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write_profile(second, "_m7v_order_probe", process_id="second_dir")
    monkeypatch.setenv(
        PROFILE_DIRS_ENV_VAR, os.pathsep.join([str(first), str(second)])
    )

    # Only the second dir has the profile: search continues past the miss.
    assert get_process_rule_profile("_m7v_order_probe").process_id == "second_dir"

    # Once the first dir also has it, the first dir wins (search order).
    _write_profile(first, "_m7v_order_probe", process_id="first_dir")
    assert get_process_rule_profile("_m7v_order_probe").process_id == "first_dir"


def test_profile_missing_everywhere_error_names_env_var(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(tmp_path / "nowhere"))

    with pytest.raises(ValueError, match="does_not_exist_anywhere") as exc_info:
        get_process_rule_profile("does_not_exist_anywhere")

    assert PROFILE_DIRS_ENV_VAR in str(exc_info.value)


def test_profile_packaged_fallback_used_when_env_dirs_miss(
    tmp_path, monkeypatch
) -> None:
    # An env dir that exists but doesn't have the profile must fall through
    # to the packaged resources dir, not short-circuit the search. This
    # plants a synthetic profile (a copy of the public demo_6m) under the
    # packaged resources dir.
    import shutil
    from importlib import resources

    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(tmp_path))
    base = Path(str(resources.files("ic_opt.em.pcell").joinpath("profiles")))
    new_id = "_m7v_fallthrough_probe"
    dst_dir = base / new_id
    dst_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(_DEMO_PROFILE_PATH, dst_dir / "rule.yaml")
        assert get_process_rule_profile(new_id).process_id == "demo_6m"
    finally:
        shutil.rmtree(dst_dir, ignore_errors=True)


def test_process_rule_loader_is_generic(monkeypatch) -> None:
    # The loader must resolve ANY <id>/rule.yaml, not special-case a profile
    # id. Copy the public demo_6m profile into a fresh id under the PACKAGED
    # profiles dir (editable install -> resources.files is a real writable
    # path) with no IC_OPT_PROFILE_DIRS hit for that id, so this doubles as
    # the "packaged fallback still works for a synthetic packaged profile"
    # case.
    import shutil
    from importlib import resources

    monkeypatch.delenv(PROFILE_DIRS_ENV_VAR, raising=False)
    base = Path(str(resources.files("ic_opt.em.pcell").joinpath("profiles")))
    new_id = "_m7v_generic_probe"
    dst_dir = base / new_id
    dst_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(_DEMO_PROFILE_PATH, dst_dir / "rule.yaml")
        # process_id comes from the yaml body, proving the FILE was loaded via
        # the generic id path rather than a hardcoded branch.
        assert get_process_rule_profile(new_id).process_id == "demo_6m"
    finally:
        shutil.rmtree(dst_dir, ignore_errors=True)


def test_process_rule_profile_defines_full_known_n28_inventory() -> None:
    profile = get_process_rule_profile("n28_1p10m")
    catalog = _load_n28_rule_data()["layer_catalog"]

    assert set(profile.layer_catalog.conductors) == set(catalog["conductors"])
    assert set(profile.layer_catalog.vias) == set(catalog["vias"])
    assert set(profile.layer_catalog.markers) == set(catalog["markers"])
    assert {"M1", "M9", "M10", "AP"} <= set(profile.layer_catalog.conductors)


def test_process_rule_profile_declares_core_layout_rule_coverage() -> None:
    profile = get_process_rule_profile("n28_1p10m")
    coverage = _load_n28_rule_data()["coverage"]

    assert profile.coverage.layer_inventory == "full_known_inventory"
    assert profile.coverage.layout_rules == "passive_generator_core_rules"
    assert set(profile.coverage.metal_width_space) == set(coverage["metal_width_space"])
    assert set(profile.coverage.via_primitives) == set(coverage["via_primitives"])
    assert set(profile.coverage.passive_via_arrays) == set(coverage["passive_via_arrays"])
    assert set(profile.coverage.via_primitives) == set(profile.layer_catalog.vias)


def test_process_rule_profile_requires_core_sections() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ProcessRuleProfile.model_validate(
            {
                "schema_version": "process-rule-profile-v1",
                "process_id": "broken",
                "units": {"length": "um"},
            }
        )

    missing = {error["loc"][0] for error in exc_info.value.errors()}
    assert {"coverage", "layer_catalog", "emx_stack", "layout_rules"}.issubset(
        missing
    )


def test_process_rule_profile_resolves_conductors_vias_and_markers() -> None:
    profile = get_process_rule_profile("n28_1p10m")
    catalog = _load_n28_rule_data()["layer_catalog"]

    assert resolve_conductor(profile, "M10").drawing == tuple(catalog["conductors"]["M10"]["drawing"])
    assert resolve_conductor(profile, "AP").pin == tuple(catalog["conductors"]["AP"]["pin"])
    assert resolve_via(profile, "VIA9").connects == ("M9", "M10")
    for name, marker in catalog["markers"].items():
        assert resolve_marker(profile, name).purpose == marker["purpose"]


def test_process_rule_profile_resolves_via_stack_by_connected_metals() -> None:
    profile = get_process_rule_profile("n28_1p10m")

    assert resolve_via_stack(profile, "M9", "M10").name == "VIA9"
    assert resolve_via_stack(profile, "M10", "M9").name == "VIA9"
    assert resolve_via_stack(profile, "M10", "AP").connects == ("M10", "AP")


def test_process_rule_profile_exposes_passive_region_rules() -> None:
    profile = get_process_rule_profile("n28_1p10m")
    passive_region = profile.layout_rules.passive_region
    raw = _load_n28_rule_data()
    raw_passive = raw["layout_rules"]["passive_region"]

    assert passive_region.marker == raw_passive["marker"]
    assert passive_region.marker in profile.layer_catalog.markers
    assert set(profile.layout_rules.metal_width_space) == set(raw["coverage"]["metal_width_space"])
    assert set(profile.layout_rules.via_primitives) == set(raw["coverage"]["via_primitives"])
    assert set(passive_region.via_array_rules) == set(raw_passive["via_array_rules"])
    coverage = passive_region.passive_via_array_coverage
    raw_coverage = raw_passive["passive_via_array_coverage"]
    assert set(coverage.modeled) == set(raw_coverage["modeled"])
    assert set(coverage.not_yet_modeled) == set(raw_coverage["not_yet_modeled"])
    assert set(coverage.modeled) == set(passive_region.via_array_rules)
    assert set(coverage.modeled) | set(coverage.not_yet_modeled) == set(
        profile.layer_catalog.vias
    )
    assert not set(coverage.modeled) & set(coverage.not_yet_modeled)
    assert coverage.not_yet_modeled


def test_profile_rejects_unclassified_via() -> None:
    data = _load_n28_rule_data()
    coverage = data["layout_rules"]["passive_region"]["passive_via_array_coverage"]
    dropped = coverage["not_yet_modeled"][-1]
    coverage["not_yet_modeled"] = [
        via for via in coverage["not_yet_modeled"] if via != dropped
    ]
    with pytest.raises(ValueError, match="classified"):
        ProcessRuleProfile.model_validate(data)


def test_profile_rejects_stale_lower_via_restrictions_block() -> None:
    data = _load_n28_rule_data()
    data["layout_rules"]["passive_region"]["lower_via_restrictions"] = {
        "disallow": ["VIA7"]
    }
    with pytest.raises(ValueError):
        ProcessRuleProfile.model_validate(data)


def test_layout_rule_lookups_fail_closed_for_uncovered_rules() -> None:
    profile = get_process_rule_profile("n28_1p10m")
    raw = _load_n28_rule_data()
    rules = raw["layout_rules"]

    for metal in ("M1", "M7", "M10"):
        assert (resolve_metal_width_space_rule(profile, metal).min_width_um
                == rules["metal_width_space"][metal]["min_width_um"])
    assert (resolve_metal_width_space_rule(profile, "AP").min_space_um
            == rules["metal_width_space"]["AP"]["min_space_um"])
    for via in ("VIA1", "VIA8", "VIA9"):
        assert (resolve_via_primitive_rule(profile, via).cut_size_um
                == tuple(rules["via_primitives"][via]["cut_size_um"]))
    arrays = rules["passive_region"]["via_array_rules"]
    assert resolve_passive_via_array_rule(profile, "VIA8").min_count == arrays["VIA8"]["min_count"]
    # The single-cut M10<->AP via is modeled with its own (single-cut) array
    # entry; its geometry comes from its primitive rule.
    top_via = resolve_via_stack(profile, "M10", "AP").name
    assert (resolve_via_primitive_rule(profile, top_via).cut_size_um
            == tuple(rules["via_primitives"][top_via]["cut_size_um"]))
    assert resolve_passive_via_array_rule(profile, top_via).min_count == arrays[top_via]["min_count"]
    assert resolve_passive_via_array_rule(profile, top_via).max_space_um == arrays[top_via]["max_space_um"]

    no_width_rule = next(c for c in raw["layer_catalog"]["conductors"] if c not in rules["metal_width_space"])
    with pytest.raises(ValueError, match=f"no metal width/space rule for {no_width_rule}"):
        resolve_metal_width_space_rule(profile, no_width_rule)
    # the coverage-gap path still applies to a still-unmodeled via.
    unmodeled = rules["passive_region"]["passive_via_array_coverage"]["not_yet_modeled"][-1]
    with pytest.raises(ValueError, match=f"no passive via array coverage for {unmodeled}"):
        resolve_passive_via_array_rule(profile, unmodeled)


def test_process_rule_lookup_failures_are_clear() -> None:
    profile = get_process_rule_profile("n28_1p10m")

    with pytest.raises(ValueError, match="unknown conductor BAD"):
        resolve_conductor(profile, "BAD")
    with pytest.raises(ValueError, match="unknown via BAD"):
        resolve_via(profile, "BAD")
    with pytest.raises(ValueError, match="unknown marker BAD"):
        resolve_marker(profile, "BAD")
    with pytest.raises(ValueError, match="no via stack connects M1 to AP"):
        resolve_via_stack(profile, "M1", "AP")


def test_process_rule_profile_has_no_runtime_source_paths_or_pcell_refs() -> None:
    text = _N28_PROFILE_PATH.read_text(encoding="utf-8")

    assert "/home/zzchen/" not in text
    assert "gdsgen_ref/pcell" not in text
    assert "center_tapped_symmetric_inductor" not in text
    assert "transformer" not in text
    assert "tcoil" not in text


def test_profile_exposes_cited_passive_via_restrictions() -> None:
    profile = get_process_rule_profile("n28_1p10m")
    raw = _load_n28_rule_data()
    raw_passive = raw["layout_rules"]["passive_region"]
    restrictions = profile.layout_rules.passive_region.via_restrictions
    assert set(restrictions) == set(raw_passive["via_restrictions"])
    assert restrictions
    for name, data in raw_passive["via_restrictions"].items():
        r = restrictions[name]
        assert r.name == name
        assert set(r.applies_to) == set(data["applies_to"])
        assert r.scope == data["scope"]
        assert r.source_text == data["source_text"]
        assert r.class_mapping_note == data.get("class_mapping_note", "")
        if "exception" in data:
            assert set(r.exception.vias) == set(data["exception"]["vias"])
            assert r.exception.marker == data["exception"]["marker"]
            assert r.exception.band_um == data["exception"]["band_um"]
            assert r.exception.implemented_by_generator is data["exception"]["implemented_by_generator"]

    metals = profile.layout_rules.passive_region.metal_restrictions
    assert set(metals) == set(raw_passive["metal_restrictions"])
    for name, data in raw_passive["metal_restrictions"].items():
        assert metals[name].source_text == data["source_text"]
        assert metals[name].note == data["note"]

    for name, data in raw["layer_catalog"]["markers"].items():
        marker = profile.layer_catalog.markers[name]
        assert marker.drawing == tuple(data["drawing"])
        assert marker.purpose == data["purpose"]


def _restriction_with_exception(data: dict) -> str:
    restrictions = data["layout_rules"]["passive_region"]["via_restrictions"]
    return next(name for name, r in restrictions.items() if "exception" in r)


def test_profile_rejects_restriction_on_unknown_via() -> None:
    data = _load_n28_rule_data()
    restrictions = data["layout_rules"]["passive_region"]["via_restrictions"]
    restrictions[next(iter(restrictions))]["applies_to"].append("VIA99")
    with pytest.raises(ValueError, match="unknown via"):
        ProcessRuleProfile.model_validate(data)

def test_profile_rejects_exception_via_outside_applies_to() -> None:
    data = _load_n28_rule_data()
    restriction = data["layout_rules"]["passive_region"]["via_restrictions"][
        _restriction_with_exception(data)
    ]
    outside = next(v for v in data["layer_catalog"]["vias"] if v not in restriction["applies_to"])
    restriction["exception"]["vias"].append(outside)
    with pytest.raises(ValueError, match="exception"):
        ProcessRuleProfile.model_validate(data)

def test_profile_rejects_exception_marker_missing_from_catalog() -> None:
    data = _load_n28_rule_data()
    data["layout_rules"]["passive_region"]["via_restrictions"][
        _restriction_with_exception(data)
    ]["exception"]["marker"] = "NOSUCHMARK"
    with pytest.raises(ValueError, match="marker"):
        ProcessRuleProfile.model_validate(data)


def test_n28_profile_lookup_fails_without_env_var(monkeypatch) -> None:
    # n28_1p10m is NDA-derived private data (never in this repository or the
    # package); without IC_OPT_PROFILE_DIRS pointing at it, the lookup must
    # fail closed with a message naming the env var. This is the regression
    # test proving the profile really did stay out of the wheel's
    # package-data.
    monkeypatch.delenv(PROFILE_DIRS_ENV_VAR, raising=False)

    with pytest.raises(ValueError, match="n28_1p10m") as exc_info:
        get_process_rule_profile("n28_1p10m")

    assert PROFILE_DIRS_ENV_VAR in str(exc_info.value)
