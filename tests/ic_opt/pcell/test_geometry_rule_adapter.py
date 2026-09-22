from pathlib import Path

import pytest
import yaml

from ic_opt.em.pcell.process_rules import PROFILE_DIRS_ENV_VAR
from ic_opt.em.pcell.rule_adapter import (
    GeometryRuleAdapter,
    LayerSpec,
    MetalRuleSpec,
    ViaArrayPlan,
    ViaRuleSpec,
    get_geometry_rule_adapter,
)
from tests.ic_opt.pcell.conftest import profile_path, requires_profile

pytestmark = requires_profile("n28_1p10m")
_N28_RULE_PATH = profile_path("n28_1p10m") or Path("n28_1p10m/rule.yaml")


def test_geometry_rule_adapter_loads_n28_profile() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")

    assert isinstance(adapter, GeometryRuleAdapter)
    assert adapter.process_id == "n28_1p10m"


def test_geometry_rule_adapter_exposes_layer_specs() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")

    m10 = adapter.layer("M10")
    ap = adapter.layer("AP")

    assert isinstance(m10, LayerSpec)
    assert m10.name == "M10"
    assert m10.drawing == (40, 80)
    assert m10.pin == (140, 0)
    assert m10.emx_name == "M10"
    assert m10.layer_class == "thick_top_metal"
    assert ap.drawing == (74, 0)
    assert ap.pin == (126, 0)


def test_geometry_rule_adapter_exposes_metal_width_space_rules() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")

    m1 = adapter.metal_rule("M1")
    m7 = adapter.metal_rule("M7")
    m10 = adapter.metal_rule("M10")
    ap = adapter.metal_rule("AP")

    assert isinstance(m10, MetalRuleSpec)
    assert m1.min_width_um == 0.28
    assert m1.min_space_um == 0.28
    assert m7.min_width_um == 0.4
    assert m7.max_width_um == 12.0
    assert m10.min_width_um == 1.0
    assert m10.max_width_um == 30.0
    assert ap.min_width_um == 2.0
    assert ap.min_space_um == 2.0


def test_geometry_rule_adapter_fails_closed_for_unknown_layer_or_rule() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")

    with pytest.raises(ValueError, match="unknown conductor BAD"):
        adapter.layer("BAD")
    with pytest.raises(ValueError, match="no metal width/space rule for PO"):
        adapter.metal_rule("PO")


def test_geometry_rule_adapter_exposes_via_rules_by_name() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")

    via9 = adapter.via("VIA9")
    rv = adapter.via("RV")

    assert isinstance(via9, ViaRuleSpec)
    assert via9.name == "VIA9"
    assert via9.drawing == (59, 80)
    assert via9.emx_name == "via9"
    assert via9.lower_metal == "M9"
    assert via9.upper_metal == "M10"
    assert via9.cut_size_um == (0.46, 0.46)
    assert via9.min_cut_space_um == 0.44
    assert via9.min_enclosure_um == {"M9": 0.08, "M10": 0.08}
    assert rv.lower_metal == "M10"
    assert rv.upper_metal == "AP"
    assert rv.cut_size_um == (3.0, 3.0)


def test_geometry_rule_adapter_exposes_via_rules_by_connected_metals() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")

    assert adapter.via_between("M9", "M10").name == "VIA9"
    assert adapter.via_between("M10", "M9").name == "VIA9"
    assert adapter.via_between("M10", "AP").name == "RV"


def test_geometry_rule_adapter_fails_closed_for_unknown_vias() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")

    with pytest.raises(ValueError, match="unknown via BAD"):
        adapter.via("BAD")
    with pytest.raises(ValueError, match="no via stack connects M1 to AP"):
        adapter.via_between("M1", "AP")


def test_geometry_rule_adapter_plans_passive_via_array() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")

    plan = adapter.plan_passive_via_array(
        lower_metal="M9",
        upper_metal="M10",
        available_width_um=3.0,
        available_height_um=3.0,
    )

    assert isinstance(plan, ViaArrayPlan)
    assert plan.via == "VIA9"
    assert plan.lower_metal == "M9"
    assert plan.upper_metal == "M10"
    assert plan.cut_size_um == (0.46, 0.46)
    assert plan.cut_spacing_um == (0.44, 0.44)
    assert plan.center_pitch_um == (0.9, 0.9)
    assert plan.rows >= 2
    assert plan.columns >= 2
    assert plan.rows * plan.columns >= 4
    assert plan.enclosure_um == {"M9": 0.08, "M10": 0.08}


def test_geometry_rule_adapter_plans_rv_single_cut() -> None:
    # M7V: RV (M10<->AP) is a single large redistribution via. In a W=5 landing
    # exactly one 3.0 um RV cut fits; geometry comes from its primitive rule.
    adapter = get_geometry_rule_adapter("n28_1p10m")

    plan = adapter.plan_passive_via_array(
        lower_metal="M10",
        upper_metal="AP",
        available_width_um=5.0,
        available_height_um=5.0,
    )

    assert plan.via == "RV"
    assert plan.lower_metal == "M10"
    assert plan.upper_metal == "AP"
    assert plan.rows == 1 and plan.columns == 1
    assert plan.cut_size_um == (3.0, 3.0)
    assert plan.enclosure_um == {"M10": 0.5, "AP": 0.5}


def test_geometry_rule_adapter_rejects_too_small_via_array_window() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")

    with pytest.raises(ValueError, match="cannot fit VIA9 passive via array"):
        adapter.plan_passive_via_array(
            lower_metal="M9",
            upper_metal="M10",
            available_width_um=0.5,
            available_height_um=0.5,
        )


def test_geometry_rule_adapter_enclosure_maps_are_read_only() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")
    via = adapter.via("VIA9")
    plan = adapter.plan_passive_via_array(
        lower_metal="M9",
        upper_metal="M10",
        available_width_um=3.0,
        available_height_um=3.0,
    )

    with pytest.raises(TypeError):
        via.min_enclosure_um["M9"] = 9.0
    with pytest.raises(TypeError):
        plan.enclosure_um["M9"] = 9.0


def test_geometry_rule_adapter_manifest_is_stable_and_path_free() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")

    manifest = adapter.manifest()

    assert manifest["schema_version"] == "process-rule-profile-v1"
    assert manifest["process_id"] == "n28_1p10m"
    assert manifest["units"] == {"length": "um"}
    assert manifest["coverage"]["layer_inventory"] == "full_known_inventory"
    assert manifest["coverage"]["layout_rules"] == "passive_generator_core_rules"
    assert "M10" in manifest["coverage"]["metal_width_space"]
    assert "VIA9" in manifest["coverage"]["via_primitives"]
    assert manifest["coverage"]["passive_via_arrays"] == ["RV", "VIA8", "VIA9"]

    rendered = repr(manifest)
    assert "/home/zzchen/" not in rendered
    assert "gdsgen_ref/pcell" not in rendered
    assert "center_tapped_symmetric_inductor" not in rendered

def test_geometry_rule_adapter_exposes_passive_via_array_coverage() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")

    coverage = adapter.passive_via_array_coverage()
    assert coverage["modeled"] == ("VIA8", "VIA9", "RV")
    assert "VIA7" in coverage["not_yet_modeled"]

    manifest = adapter.manifest()
    assert manifest["coverage"]["passive_via_array_coverage"] == {
        "modeled": ["VIA8", "VIA9", "RV"],
        "not_yet_modeled": list(coverage["not_yet_modeled"]),
    }

def test_adapter_plans_via7_geometry_after_ind_r1_gate_removal() -> None:
    """n28-rules-slim (user directive 2026-07-19): via_restrictions
    (IND.R.1) no longer gates plan_passive_via_array. VIA7 (one of the
    "VIAy" vias IND.R.1 named, with no LOWMEDN exception) now plans purely
    from its own via_primitives geometry: cut 0.1um, cut spacing 0.1um,
    enclosure 0.04um on both M7 and M8 -- there is no via_array_rules entry
    for VIA7, so no separate min-count/max-space legality applies (this
    used to raise "VIA7 is restricted by IND.R.1...")."""
    adapter = get_geometry_rule_adapter("n28_1p10m")
    plan = adapter.plan_passive_via_array(
        lower_metal="M7",
        upper_metal="M8",
        available_width_um=10.0,
        available_height_um=10.0,
    )
    assert plan.via == "VIA7"
    assert plan.cut_size_um == (0.1, 0.1)
    assert plan.cut_spacing_um == (0.1, 0.1)
    assert plan.enclosure_um == {"M7": 0.04, "M8": 0.04}
    assert plan.rows >= 2 and plan.columns >= 2


def test_adapter_plans_via3_geometry_after_ind_r1_gate_removal() -> None:
    """VIA3 (a "VIAx" via IND.R.1 named with an unimplemented LOWMEDN
    exception) also now plans purely from its via_primitives geometry
    (this used to raise citing "not implemented by this generator")."""
    adapter = get_geometry_rule_adapter("n28_1p10m")
    plan = adapter.plan_passive_via_array(
        lower_metal="M3",
        upper_metal="M4",
        available_width_um=10.0,
        available_height_um=10.0,
    )
    assert plan.via == "VIA3"
    assert plan.cut_size_um == (0.05, 0.05)
    assert plan.cut_spacing_um == (0.07, 0.07)
    assert plan.enclosure_um == {"M3": 0.03, "M4": 0.03}
    assert plan.rows >= 2 and plan.columns >= 2


def test_adapter_passive_via_restriction_still_queryable_as_citation() -> None:
    """passive_via_restriction() itself is unchanged (data/citation lookup);
    only plan_passive_via_array stopped consulting it as a gate."""
    adapter = get_geometry_rule_adapter("n28_1p10m")
    restriction = adapter.passive_via_restriction("VIA7")
    assert restriction is not None
    assert restriction.name == "IND.R.1"
    assert adapter.passive_via_restriction("VIA9") is None


def test_adapter_manifest_lists_via_restrictions() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")
    assert adapter.manifest()["coverage"]["passive_via_restrictions"] == [
        "IND.R.1"
    ]


def test_adapter_plan_still_fails_closed_on_missing_via_enclosure_data(
    tmp_path, monkeypatch
) -> None:
    """Geometric-only enforcement (n28-rules-slim) strips the
    via_restrictions/coverage POLICY gates, but missing via GEOMETRY is a
    data-availability gap, not a policy restriction, and must still fail
    closed. Here VIA7's M8 enclosure entry is deleted from a synthetic
    profile; plan_passive_via_array must still refuse and name the via."""
    data = yaml.safe_load(_N28_RULE_PATH.read_text(encoding="utf-8"))
    del data["layout_rules"]["via_primitives"]["VIA7"]["min_enclosure_um"]["M8"]

    new_id = "_n28_missing_via7_enclosure_probe"
    dst_dir = tmp_path / new_id
    dst_dir.mkdir(parents=True)
    (dst_dir / "rule.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(tmp_path))

    adapter = get_geometry_rule_adapter(new_id)
    with pytest.raises(
        ValueError, match="VIA7 enclosure rules do not cover both metals"
    ):
        adapter.plan_passive_via_array(
            lower_metal="M7",
            upper_metal="M8",
            available_width_um=10.0,
            available_height_um=10.0,
        )


def test_adapter_plan_still_fails_closed_on_missing_via_primitive_entry(
    tmp_path, monkeypatch
) -> None:
    """A via with no via_primitives entry at all (cut size/spacing/
    enclosure entirely undefined) must still fail closed -- the geometric
    data itself, not any policy gate, is what plan_passive_via_array
    actually requires."""
    data = yaml.safe_load(_N28_RULE_PATH.read_text(encoding="utf-8"))
    del data["layout_rules"]["via_primitives"]["VIA7"]
    data["coverage"]["via_primitives"] = [
        v for v in data["coverage"]["via_primitives"] if v != "VIA7"
    ]

    new_id = "_n28_missing_via7_primitive_probe"
    dst_dir = tmp_path / new_id
    dst_dir.mkdir(parents=True)
    (dst_dir / "rule.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(tmp_path))

    adapter = get_geometry_rule_adapter(new_id)
    with pytest.raises(ValueError, match="no via primitive rule for VIA7"):
        adapter.plan_passive_via_array(
            lower_metal="M7",
            upper_metal="M8",
            available_width_um=10.0,
            available_height_um=10.0,
        )
