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

EXPECTED_N28_CONDUCTORS = {
    "OD",
    "PO",
    "M1",
    "M2",
    "M3",
    "M4",
    "M5",
    "M6",
    "M7",
    "M8",
    "M9",
    "M10",
    "AP",
}

EXPECTED_N28_METAL_WIDTH_SPACE_RULES = {
    "M1",
    "M2",
    "M3",
    "M4",
    "M5",
    "M6",
    "M7",
    "M8",
    "M9",
    "M10",
    "AP",
}

EXPECTED_N28_VIAS = {
    "ODCONT",
    "POLYCONT",
    "VIA1",
    "VIA2",
    "VIA3",
    "VIA4",
    "VIA5",
    "VIA6",
    "VIA7",
    "VIA8",
    "VIA9",
    "RV",
}


from tests.ic_opt.pcell.conftest import profile_path, requires_profile

pytestmark = requires_profile("n28_1p10m")
_N28_PROFILE_PATH = profile_path("n28_1p10m") or Path("n28_1p10m/rule.yaml")


def _load_n28_rule_data() -> dict:
    return yaml.safe_load(_N28_PROFILE_PATH.read_text(encoding="utf-8"))


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
    data = _load_n28_rule_data()
    data["process_id"] = process_id
    (profile_dir / "rule.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_profile_dir_env_var_hit_wins_over_packaged(tmp_path, monkeypatch) -> None:
    # An IC_OPT_PROFILE_DIRS hit must be preferred over anything with the
    # same id under packaged resources (n28_1p10m itself is repo-only now --
    # see test_process_rule_loader_is_generic for the packaged-fallback
    # side of this search order, using a synthetic packaged id).
    _write_profile(tmp_path, "n28_1p10m", process_id="env_dir_wins")
    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(tmp_path))

    profile = get_process_rule_profile("n28_1p10m")

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
    # to the packaged resources dir, not short-circuit the search. n28 is
    # repo-only (process_data/), never packaged, so this uses a synthetic
    # profile planted under the packaged resources dir instead.
    import shutil
    from importlib import resources

    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(tmp_path))
    base = Path(str(resources.files("ic_opt.em.pcell").joinpath("profiles")))
    new_id = "_m7v_fallthrough_probe"
    dst_dir = base / new_id
    dst_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(_N28_PROFILE_PATH, dst_dir / "rule.yaml")
        assert get_process_rule_profile(new_id).process_id == "n28_1p10m"
    finally:
        shutil.rmtree(dst_dir, ignore_errors=True)


def test_process_rule_loader_is_generic(monkeypatch) -> None:
    # The loader must resolve ANY <id>/rule.yaml, not special-case n28. Copy
    # the real n28 profile (now repo-only under process_data/, never
    # packaged) into a fresh id under the PACKAGED profiles dir (editable
    # install -> resources.files is a real writable path) with no
    # IC_OPT_PROFILE_DIRS hit for that id, so this doubles as the
    # "packaged fallback still works for a synthetic packaged profile" case.
    import shutil
    from importlib import resources

    monkeypatch.delenv(PROFILE_DIRS_ENV_VAR, raising=False)
    base = Path(str(resources.files("ic_opt.em.pcell").joinpath("profiles")))
    new_id = "_m7v_generic_probe"
    dst_dir = base / new_id
    dst_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(_N28_PROFILE_PATH, dst_dir / "rule.yaml")
        # process_id comes from the yaml body, proving the FILE was loaded via
        # the generic id path rather than a hardcoded branch.
        assert get_process_rule_profile(new_id).process_id == "n28_1p10m"
    finally:
        shutil.rmtree(dst_dir, ignore_errors=True)


def test_process_rule_profile_defines_full_known_n28_inventory() -> None:
    profile = get_process_rule_profile("n28_1p10m")

    assert set(profile.layer_catalog.conductors) == EXPECTED_N28_CONDUCTORS
    assert set(profile.layer_catalog.vias) == EXPECTED_N28_VIAS
    assert set(profile.layer_catalog.markers) == {"INDDMY", "LOWMEDN"}


def test_process_rule_profile_declares_core_layout_rule_coverage() -> None:
    profile = get_process_rule_profile("n28_1p10m")

    assert profile.coverage.layer_inventory == "full_known_inventory"
    assert profile.coverage.layout_rules == "passive_generator_core_rules"
    assert set(profile.coverage.metal_width_space) == EXPECTED_N28_METAL_WIDTH_SPACE_RULES
    assert set(profile.coverage.via_primitives) == EXPECTED_N28_VIAS
    assert set(profile.coverage.passive_via_arrays) == {"VIA8", "VIA9", "RV"}


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

    assert resolve_conductor(profile, "M10").drawing == (40, 80)
    assert resolve_conductor(profile, "AP").pin == (126, 0)
    assert resolve_via(profile, "VIA9").connects == ("M9", "M10")
    assert resolve_marker(profile, "INDDMY").purpose == "passive_region"


def test_process_rule_profile_resolves_via_stack_by_connected_metals() -> None:
    profile = get_process_rule_profile("n28_1p10m")

    assert resolve_via_stack(profile, "M9", "M10").name == "VIA9"
    assert resolve_via_stack(profile, "M10", "M9").name == "VIA9"
    assert resolve_via_stack(profile, "M10", "AP").name == "RV"


def test_process_rule_profile_exposes_passive_region_rules() -> None:
    profile = get_process_rule_profile("n28_1p10m")
    passive_region = profile.layout_rules.passive_region

    assert passive_region.marker == "INDDMY"
    assert set(profile.layout_rules.metal_width_space) == (
        EXPECTED_N28_METAL_WIDTH_SPACE_RULES
    )
    assert set(profile.layout_rules.via_primitives) == EXPECTED_N28_VIAS
    assert set(passive_region.via_array_rules) == {"VIA8", "VIA9", "RV"}
    coverage = passive_region.passive_via_array_coverage
    assert set(coverage.modeled) == {"VIA8", "VIA9", "RV"}
    assert set(coverage.modeled) == set(passive_region.via_array_rules)
    assert set(coverage.modeled) | set(coverage.not_yet_modeled) == set(
        profile.layer_catalog.vias
    )
    assert not set(coverage.modeled) & set(coverage.not_yet_modeled)
    assert "VIA7" in coverage.not_yet_modeled


def test_profile_rejects_unclassified_via() -> None:
    data = _load_n28_rule_data()
    coverage = data["layout_rules"]["passive_region"]["passive_via_array_coverage"]
    coverage["not_yet_modeled"] = [
        via for via in coverage["not_yet_modeled"] if via != "VIA7"
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

    assert resolve_metal_width_space_rule(profile, "M1").min_width_um == 0.28
    assert resolve_metal_width_space_rule(profile, "M7").min_width_um == 0.4
    assert resolve_metal_width_space_rule(profile, "M10").min_width_um == 1.0
    assert resolve_metal_width_space_rule(profile, "AP").min_space_um == 2.0
    assert resolve_via_primitive_rule(profile, "VIA1").cut_size_um == (0.05, 0.05)
    assert resolve_via_primitive_rule(profile, "VIA8").cut_size_um == (0.46, 0.46)
    assert resolve_via_primitive_rule(profile, "VIA9").cut_size_um == (0.46, 0.46)
    assert resolve_via_primitive_rule(profile, "RV").cut_size_um == (3.0, 3.0)
    assert resolve_passive_via_array_rule(profile, "VIA8").min_count == 4
    # RV (M10<->AP) is modeled as a single large via, geometry from its
    # primitive rule (min_count 1, not the >=4 VIA8/VIA9 redundancy array).
    assert resolve_passive_via_array_rule(profile, "RV").min_count == 1
    assert resolve_passive_via_array_rule(profile, "RV").max_space_um == 2.0

    with pytest.raises(ValueError, match="no metal width/space rule for PO"):
        resolve_metal_width_space_rule(profile, "PO")
    # the coverage-gap path still applies to a still-unmodeled via (VIA7).
    with pytest.raises(ValueError, match="no passive via array coverage for VIA7"):
        resolve_passive_via_array_rule(profile, "VIA7")


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
    restrictions = profile.layout_rules.passive_region.via_restrictions
    assert set(restrictions) == {"IND.R.1"}
    r = restrictions["IND.R.1"]
    assert set(r.applies_to) == {
        "VIA1", "VIA2", "VIA3", "VIA4", "VIA5", "VIA6", "VIA7"
    }
    assert r.scope == "INDDMY SIZING 16 um"
    assert set(r.exception.vias) == {"VIA1", "VIA2", "VIA3", "VIA4", "VIA5"}
    assert r.exception.marker == "LOWMEDN"
    assert r.exception.band_um == 4.0
    assert r.exception.implemented_by_generator is False
    assert "VIAx, and VIAy are not allowed" in r.source_text
    assert "5X2Y2R" in r.class_mapping_note

    metals = profile.layout_rules.passive_region.metal_restrictions
    assert set(metals) == {"IND.R.5"}
    assert "one layer only" in metals["IND.R.5"].source_text
    assert "M7K" in metals["IND.R.5"].note

    lowmedn = profile.layer_catalog.markers["LOWMEDN"]
    assert lowmedn.drawing == (4495, 0)
    assert lowmedn.purpose == "low_metal_density_region"

def test_profile_rejects_restriction_on_unknown_via() -> None:
    data = _load_n28_rule_data()
    data["layout_rules"]["passive_region"]["via_restrictions"]["IND.R.1"][
        "applies_to"
    ].append("VIA99")
    with pytest.raises(ValueError, match="unknown via"):
        ProcessRuleProfile.model_validate(data)

def test_profile_rejects_exception_via_outside_applies_to() -> None:
    data = _load_n28_rule_data()
    data["layout_rules"]["passive_region"]["via_restrictions"]["IND.R.1"][
        "exception"
    ]["vias"].append("VIA8")
    with pytest.raises(ValueError, match="exception"):
        ProcessRuleProfile.model_validate(data)

def test_profile_rejects_exception_marker_missing_from_catalog() -> None:
    data = _load_n28_rule_data()
    data["layout_rules"]["passive_region"]["via_restrictions"]["IND.R.1"][
        "exception"
    ]["marker"] = "NOSUCHMARK"
    with pytest.raises(ValueError, match="marker"):
        ProcessRuleProfile.model_validate(data)


def test_n28_profile_lookup_fails_without_env_var(monkeypatch) -> None:
    # n28_1p10m is NDA-derived, repo-only data under process_data/profiles/
    # (never packaged); without IC_OPT_PROFILE_DIRS pointing there, the
    # lookup must fail closed with a message naming the env var. This is the
    # regression test proving the profile really did move out of the wheel's
    # package-data.
    monkeypatch.delenv(PROFILE_DIRS_ENV_VAR, raising=False)

    with pytest.raises(ValueError, match="n28_1p10m") as exc_info:
        get_process_rule_profile("n28_1p10m")

    assert PROFILE_DIRS_ENV_VAR in str(exc_info.value)
