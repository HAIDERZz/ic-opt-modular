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
from tests.ic_opt.pcell.conftest import PACKAGE_DIR, profile_path, requires_profile

# Expected values are read from the private profile's own rule.yaml
# (IC_OPT_PROFILE_DIRS), never written here: private process rules stay out
# of the repository. What is tested is that the adapter reports the rule
# file faithfully and plans with it.
pytestmark = requires_profile("n28_1p10m")
_N28_RULE_PATH = profile_path("n28_1p10m") or Path("n28_1p10m/rule.yaml")
_DEMO_RULE_PATH = PACKAGE_DIR / "profiles" / "demo_6m" / "rule.yaml"


def _raw() -> dict:
    return yaml.safe_load(_N28_RULE_PATH.read_text(encoding="utf-8"))


def _primitive(via: str) -> dict:
    return _raw()["layout_rules"]["via_primitives"][via]


def test_geometry_rule_adapter_loads_n28_profile() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")

    assert isinstance(adapter, GeometryRuleAdapter)
    assert adapter.process_id == "n28_1p10m"


def test_geometry_rule_adapter_exposes_layer_specs() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")
    conductors = _raw()["layer_catalog"]["conductors"]

    m10 = adapter.layer("M10")
    ap = adapter.layer("AP")

    assert isinstance(m10, LayerSpec)
    assert m10.name == "M10"
    assert m10.drawing == tuple(conductors["M10"]["drawing"])
    assert m10.pin == tuple(conductors["M10"]["pin"])
    assert m10.emx_name == conductors["M10"]["emx_name"]
    assert m10.layer_class == conductors["M10"]["class"]
    assert ap.drawing == tuple(conductors["AP"]["drawing"])
    assert ap.pin == tuple(conductors["AP"]["pin"])


def test_geometry_rule_adapter_exposes_metal_width_space_rules() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")
    rules = _raw()["layout_rules"]["metal_width_space"]

    assert isinstance(adapter.metal_rule("M10"), MetalRuleSpec)
    for metal in ("M1", "M7", "M10", "AP"):
        rule = adapter.metal_rule(metal)
        assert rule.min_width_um == rules[metal]["min_width_um"]
        assert rule.max_width_um == rules[metal]["max_width_um"]
        assert rule.min_space_um == rules[metal]["min_space_um"]


def test_geometry_rule_adapter_fails_closed_for_unknown_layer_or_rule() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")
    raw = _raw()
    no_width_rule = next(c for c in raw["layer_catalog"]["conductors"]
                         if c not in raw["layout_rules"]["metal_width_space"])

    with pytest.raises(ValueError, match="unknown conductor BAD"):
        adapter.layer("BAD")
    with pytest.raises(ValueError, match=f"no metal width/space rule for {no_width_rule}"):
        adapter.metal_rule(no_width_rule)


def test_geometry_rule_adapter_exposes_via_rules_by_name() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")
    raw = _raw()

    via9 = adapter.via("VIA9")
    top = adapter.via_between("M10", "AP")

    assert isinstance(via9, ViaRuleSpec)
    assert via9.name == "VIA9"
    assert via9.drawing == tuple(raw["layer_catalog"]["vias"]["VIA9"]["drawing"])
    assert via9.emx_name == raw["layer_catalog"]["vias"]["VIA9"]["emx_name"]
    assert via9.lower_metal == "M9"
    assert via9.upper_metal == "M10"
    assert via9.cut_size_um == tuple(_primitive("VIA9")["cut_size_um"])
    assert via9.min_cut_space_um == _primitive("VIA9")["min_cut_space_um"]
    assert via9.min_enclosure_um == _primitive("VIA9")["min_enclosure_um"]
    assert top.lower_metal == "M10"
    assert top.upper_metal == "AP"
    assert top.cut_size_um == tuple(_primitive(top.name)["cut_size_um"])


def test_geometry_rule_adapter_exposes_via_rules_by_connected_metals() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")

    assert adapter.via_between("M9", "M10").name == "VIA9"
    assert adapter.via_between("M10", "M9").name == "VIA9"
    assert adapter.via_between("M10", "AP").name == adapter.via_between("AP", "M10").name


def test_geometry_rule_adapter_fails_closed_for_unknown_vias() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")

    with pytest.raises(ValueError, match="unknown via BAD"):
        adapter.via("BAD")
    with pytest.raises(ValueError, match="no via stack connects M1 to AP"):
        adapter.via_between("M1", "AP")


def _assert_plan_follows_primitive(plan, via: str) -> None:
    primitive = _primitive(via)
    cut = tuple(primitive["cut_size_um"])
    space = primitive["min_cut_space_um"]
    assert plan.cut_size_um == cut
    assert plan.cut_spacing_um == (space, space)
    assert plan.center_pitch_um == pytest.approx((cut[0] + space, cut[1] + space))
    assert plan.enclosure_um == primitive["min_enclosure_um"]


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
    _assert_plan_follows_primitive(plan, "VIA9")
    assert plan.rows >= 2
    assert plan.columns >= 2
    assert plan.rows * plan.columns >= _raw()["layout_rules"]["passive_region"]["via_array_rules"]["VIA9"]["min_count"]


def test_geometry_rule_adapter_plans_rv_single_cut() -> None:
    # M7V: the M10<->AP via is a single large redistribution via. In a W=5
    # landing exactly one cut fits; geometry comes from its primitive rule.
    adapter = get_geometry_rule_adapter("n28_1p10m")

    plan = adapter.plan_passive_via_array(
        lower_metal="M10",
        upper_metal="AP",
        available_width_um=5.0,
        available_height_um=5.0,
    )

    assert plan.via == adapter.via_between("M10", "AP").name
    assert plan.lower_metal == "M10"
    assert plan.upper_metal == "AP"
    assert plan.rows == 1 and plan.columns == 1
    assert plan.cut_size_um == tuple(_primitive(plan.via)["cut_size_um"])
    assert plan.enclosure_um == _primitive(plan.via)["min_enclosure_um"]


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
    assert manifest["coverage"]["passive_via_arrays"] == sorted(_raw()["coverage"]["passive_via_arrays"])

    rendered = repr(manifest)
    assert "/home/zzchen/" not in rendered
    assert "gdsgen_ref/pcell" not in rendered
    assert "center_tapped_symmetric_inductor" not in rendered

def test_geometry_rule_adapter_exposes_passive_via_array_coverage() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")
    raw_coverage = _raw()["layout_rules"]["passive_region"]["passive_via_array_coverage"]

    coverage = adapter.passive_via_array_coverage()
    assert coverage["modeled"] == tuple(raw_coverage["modeled"])
    assert set(coverage["not_yet_modeled"]) == set(raw_coverage["not_yet_modeled"])

    manifest = adapter.manifest()
    assert manifest["coverage"]["passive_via_array_coverage"] == {
        "modeled": list(raw_coverage["modeled"]),
        "not_yet_modeled": list(coverage["not_yet_modeled"]),
    }

def test_adapter_plans_via7_geometry_after_ind_r1_gate_removal() -> None:
    """n28-rules-slim (user directive 2026-07-19): via_restrictions no longer
    gate plan_passive_via_array. VIA7 now plans purely from its own
    via_primitives geometry -- cut size, cut spacing and enclosure as the
    profile states them (this used to raise citing a via restriction)."""
    adapter = get_geometry_rule_adapter("n28_1p10m")
    plan = adapter.plan_passive_via_array(
        lower_metal="M7",
        upper_metal="M8",
        available_width_um=10.0,
        available_height_um=10.0,
    )
    assert plan.via == "VIA7"
    _assert_plan_follows_primitive(plan, "VIA7")
    assert plan.rows >= 2 and plan.columns >= 2


def test_adapter_plans_via3_geometry_after_ind_r1_gate_removal() -> None:
    """VIA3 also now plans purely from its via_primitives geometry (this
    used to raise citing a restriction exception "not implemented by this
    generator")."""
    adapter = get_geometry_rule_adapter("n28_1p10m")
    plan = adapter.plan_passive_via_array(
        lower_metal="M3",
        upper_metal="M4",
        available_width_um=10.0,
        available_height_um=10.0,
    )
    assert plan.via == "VIA3"
    _assert_plan_follows_primitive(plan, "VIA3")
    assert plan.rows >= 2 and plan.columns >= 2


def test_adapter_passive_via_restriction_still_queryable_as_citation() -> None:
    """passive_via_restriction() itself is unchanged (data/citation lookup);
    only plan_passive_via_array stopped consulting it as a gate."""
    adapter = get_geometry_rule_adapter("n28_1p10m")
    raw = _raw()
    restrictions = raw["layout_rules"]["passive_region"]["via_restrictions"]
    seen = set()
    for via in raw["layer_catalog"]["vias"]:
        cited = [name for name, r in restrictions.items() if via in r["applies_to"]]
        restriction = adapter.passive_via_restriction(via)
        if cited:
            assert restriction is not None and restriction.name == cited[0]
        else:
            assert restriction is None
        seen.add(bool(cited))
    assert seen == {True, False}      # both a cited and an uncited via exist


def test_adapter_manifest_lists_via_restrictions() -> None:
    adapter = get_geometry_rule_adapter("n28_1p10m")
    restrictions = _raw()["layout_rules"]["passive_region"]["via_restrictions"]
    assert adapter.manifest()["coverage"]["passive_via_restrictions"] == sorted(restrictions)


def test_adapter_plan_still_fails_closed_on_missing_via_enclosure_data(
    tmp_path, monkeypatch
) -> None:
    """Geometric-only enforcement (n28-rules-slim) strips the
    via_restrictions/coverage POLICY gates, but missing via GEOMETRY is a
    data-availability gap, not a policy restriction, and must still fail
    closed. Here VIA3's M4 enclosure entry is deleted from a synthetic copy
    of the public demo_6m profile; plan_passive_via_array must still refuse
    and name the via."""
    data = yaml.safe_load(_DEMO_RULE_PATH.read_text(encoding="utf-8"))
    del data["layout_rules"]["via_primitives"]["VIA3"]["min_enclosure_um"]["M4"]

    new_id = "_demo_missing_via3_enclosure_probe"
    dst_dir = tmp_path / new_id
    dst_dir.mkdir(parents=True)
    (dst_dir / "rule.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(tmp_path))

    adapter = get_geometry_rule_adapter(new_id)
    with pytest.raises(
        ValueError, match="VIA3 enclosure rules do not cover both metals"
    ):
        adapter.plan_passive_via_array(
            lower_metal="M3",
            upper_metal="M4",
            available_width_um=10.0,
            available_height_um=10.0,
        )


def test_adapter_plan_still_fails_closed_on_missing_via_primitive_entry(
    tmp_path, monkeypatch
) -> None:
    """A via with no via_primitives entry at all (cut size/spacing/
    enclosure entirely undefined) must still fail closed -- the geometric
    data itself, not any policy gate, is what plan_passive_via_array
    actually requires (synthetic copy of the public demo_6m profile)."""
    data = yaml.safe_load(_DEMO_RULE_PATH.read_text(encoding="utf-8"))
    del data["layout_rules"]["via_primitives"]["VIA3"]
    data["coverage"]["via_primitives"] = [
        v for v in data["coverage"]["via_primitives"] if v != "VIA3"
    ]

    new_id = "_demo_missing_via3_primitive_probe"
    dst_dir = tmp_path / new_id
    dst_dir.mkdir(parents=True)
    (dst_dir / "rule.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(tmp_path))

    adapter = get_geometry_rule_adapter(new_id)
    with pytest.raises(ValueError, match="no via primitive rule for VIA3"):
        adapter.plan_passive_via_array(
            lower_metal="M3",
            upper_metal="M4",
            available_width_um=10.0,
            available_height_um=10.0,
        )
