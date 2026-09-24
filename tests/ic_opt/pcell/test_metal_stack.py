"""T13.11: metals are addressed by stack position, the stack is the profile's via chain -- any names, any count.

The existing profiles keep their numbers (the byte-identity check against the recorded N28 library and the frozen
demo_6m / N28 / N65 baselines lives in docs/refactor/reports/pcell_plan/pcell_byte_replay.py); these tests pin the
mechanism: the chain is read from the vias whatever the key order, the fixture metal is the chain's bottom (and the
DRC gate excuses the fixture ring on it by that name, T15.6), the reference convention survives outside a profile,
user configs must name real metals, stacks do not leak between threads, and a fictitious 12-metal profile whose top is
called RDL builds every family on RDL.
"""
from __future__ import annotations

import copy
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from ic_opt.em.pcell import stack
from ic_opt.em.pcell.process_rules import (
    PROFILE_DIRS_ENV_VAR,
    ProcessRuleProfile,
    get_process_rule_profile,
)
from ic_opt.em.pcell.profile_validation import (
    GENERATION_FAMILIES,
    _canonical_config,
    validate_profile,
)
from tests.ic_opt.pcell.test_profile_validation import DEMO_RULE, write_profile

pytest.importorskip("klayout.db")


def demo() -> dict:
    return yaml.safe_load(DEMO_RULE.read_text(encoding="utf-8"))


def rdl12(base: dict) -> dict:
    """demo_6m grown to 12 metals: M7..M11 above M6 and a top conductor called RDL (every number invented)."""
    d = copy.deepcopy(base)
    d["process_id"] = "rdl12"
    cat, emx, rules = d["layer_catalog"], d["emx_stack"], d["layout_rules"]
    cat["conductors"]["M6"]["class"] = "intermediate_top_metal"
    added = {f"M{i}": (80 + i, "intermediate_top_metal", 0.9, (0.4, 12.0, 0.4)) for i in range(7, 11)}
    added["M11"] = (91, "thick_top_metal", 3.0, (1.0, 30.0, 1.5))
    added["RDL"] = (95, "aluminum_pad", 2.8, (1.5, 30.0, 1.5))
    for name, (layer, cls, thickness, (w_min, w_max, space)) in added.items():
        cat["conductors"][name] = {"drawing": [layer, 0], "pin": [layer, 2], "emx_name": name, "class": cls}
        emx["conductors"][name] = {"thickness_um": thickness}
        rules["metal_width_space"][name] = {"min_width_um": w_min, "max_width_um": w_max, "min_space_um": space}
    chain = ["M6", "M7", "M8", "M9", "M10", "M11", "RDL"]
    names = {"VIA6": 76, "VIA7": 77, "VIA8": 78, "VIA9": 79, "VIA10": 80, "RV": 96}
    for (lower, upper), (name, layer) in zip(zip(chain, chain[1:]), names.items()):
        cut, space, enclosure = {"RV": (1.0, 1.0, 0.3), "VIA10": (0.5, 0.55, 0.1)}.get(name, (0.3, 0.35, 0.05))
        cat["vias"][name] = {"drawing": [layer, 0], "emx_name": name, "connects": [lower, upper]}
        emx["via_models"][name] = {"via": name, "emx_effective_size_um": cut + 0.02}
        rules["via_primitives"][name] = {"cut_size_um": [cut, cut], "min_cut_space_um": space, "min_enclosure_um": {lower: enclosure, upper: enclosure}}
        rules["passive_region"]["via_array_rules"][name] = {"min_count": 2, "max_space_um": 4.0}
        rules["passive_region"]["passive_via_array_coverage"]["modeled"].append(name)
    rules["passive_region"]["wide_parallel_spacing"] = [
        {"metals": ["M11", "RDL"], "when_width_gt_um": 5.0, "when_parallel_length_gt_um": 20.0, "min_space_um": 2.0}]
    d["coverage"].update(metal_width_space=list(rules["metal_width_space"]), via_primitives=list(rules["via_primitives"]),
                         passive_via_arrays=list(rules["passive_region"]["via_array_rules"]), emx_via_models=list(emx["via_models"]))
    return d


def test_the_stack_is_the_via_chain_whatever_the_key_order():
    shuffled = yaml.safe_load(yaml.safe_dump(demo(), sort_keys=True))
    shuffled["layer_catalog"]["conductors"] = dict(reversed(list(shuffled["layer_catalog"]["conductors"].items())))   # listed top first
    profile = ProcessRuleProfile.model_validate(shuffled)
    assert profile.metal_stack == ("M1", "M2", "M3", "M4", "M5", "M6") and profile.fixture_conductor == "M1"
    grown = ProcessRuleProfile.model_validate(yaml.safe_load(yaml.safe_dump(rdl12(demo()), sort_keys=True)))
    assert grown.metal_stack[-3:] == ("M10", "M11", "RDL")                                 # sorted keys put M10 before M2; the vias do not


def bottom_renamed(base: dict, name: str) -> dict:
    """``base`` (demo_6m) with its bottom metal M1 called ``name`` everywhere it appears; a bottom metal not named M1 must
    then be declared as layer_catalog.ground_fixture_conductor."""
    d = copy.deepcopy(base)
    cat = d["layer_catalog"]
    cat["conductors"] = {(name if k == "M1" else k): v for k, v in cat["conductors"].items()}
    cat["conductors"][name]["emx_name"] = name
    cat["vias"]["VIA1"]["connects"] = [name, "M2"]
    for section in (d["emx_stack"]["conductors"], d["layout_rules"]["metal_width_space"],
                    d["layout_rules"]["via_primitives"]["VIA1"]["min_enclosure_um"]):
        section[name] = section.pop("M1")
    d["coverage"]["metal_width_space"] = list(d["layout_rules"]["metal_width_space"])
    return d


def test_the_fixture_metal_is_the_bottom_named_m1_or_declared():
    renamed = bottom_renamed(demo(), "ME1")
    cat = renamed["layer_catalog"]
    with pytest.raises(ValueError, match="declare layer_catalog.ground_fixture_conductor"):
        ProcessRuleProfile.model_validate(renamed)
    cat["ground_fixture_conductor"] = "ME1"
    assert ProcessRuleProfile.model_validate(renamed).metal_stack[:2] == ("ME1", "M2")
    cat["ground_fixture_conductor"] = "M3"                                                  # a middle metal has two neighbours
    with pytest.raises(ValueError, match="one column"):
        ProcessRuleProfile.model_validate(renamed)


def test_a_via_skipping_a_level_or_a_missing_via_breaks_the_chain():
    skipping = demo()
    skipping["layer_catalog"]["vias"]["VIA3"]["connects"] = ["M2", "M4"]
    with pytest.raises(ValueError, match="one column"):
        ProcessRuleProfile.model_validate(skipping)
    missing = demo()
    missing["layer_catalog"]["vias"].pop("VIA5")
    for key in ("via_primitives",):
        missing["layout_rules"][key].pop("VIA5")
    missing["layout_rules"]["passive_region"]["via_array_rules"].pop("VIA5")
    missing["layout_rules"]["passive_region"]["passive_via_array_coverage"]["modeled"].remove("VIA5")
    missing["emx_stack"]["via_models"].pop("VIA5")
    for key in ("via_primitives", "passive_via_arrays", "emx_via_models"):
        missing["coverage"][key].remove("VIA5")
    with pytest.raises(ValueError, match=r"metals \['M6'\] are not joined"):
        ProcessRuleProfile.model_validate(missing)


def test_reference_mode_and_profile_mode_positions():
    assert stack.active() is None and stack.size() == 11
    assert stack.index("AP") == 11 and stack.index("m10") == 10 and stack.name(11) == "AP" and stack.name(10) == "M10"
    with stack.use_stack(ProcessRuleProfile.model_validate(rdl12(demo()))):
        assert stack.size() == 12 and stack.index("rdl") == 12 and stack.index("M11") == 11 and stack.index("11") == 11
        assert stack.name(12) == "RDL" and stack.name(13) == "M13"                          # above the top: a name no profile defines
        with pytest.raises(ValueError, match="not a conductor of the stack"):
            stack.index("AP")
    assert stack.active() is None


def test_stacks_do_not_leak_between_threads():
    profile = ProcessRuleProfile.model_validate(demo())

    def inside(_):
        with stack.use_stack(profile):
            return stack.size(), stack.index("6")

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert set(pool.map(inside, range(8))) == {(6, 6)}
        assert set(pool.map(lambda _: stack.size(), range(8))) == {11}


def test_a_config_metal_must_name_a_metal_of_its_profile():
    from ic_opt.em.pcell._pcell_core import max_opening
    from ic_opt.em.pcell.generator_plugin import PLUGIN_GENERATORS

    gen = PLUGIN_GENERATORS["clean_port_ind_sym"]
    base = _canonical_config("clean_port_ind_sym", get_process_rule_profile("demo_6m"), "demo_6m", 6, max_opening)
    for spelling in ("M6", "6", "m6"):
        assert gen.config_model.model_validate(base | {"metal": spelling}).metal == spelling
    with pytest.raises(ValueError, match="is not a metal of profile demo_6m"):
        gen.config_model.model_validate(base | {"metal": "7"})                                # no M7: never "the 7th metal"


def test_a_renamed_fixture_metal_passes_the_smoke_and_the_pcell_audit(tmp_path, monkeypatch):
    """T15.6 (audit row 12): the product-scope DRC gate excuses max_width on the profile's fixture conductor, whatever
    the profile calls it. demo_6m with M1 renamed ME1 used to fail every build ([max_width] ME1 x1, the fixture ring)."""
    from ic_opt.em.pcell.drc_audit import audit_gds, fixture_exemptions
    from ic_opt.space import Point
    from ic_opt.spec import Spec
    from ic_opt.stages.em_chain import Pcell
    from tests.ic_opt.test_em_pcell import demo_spec, point_context

    data = bottom_renamed(demo(), "ME1") | {"process_id": "me1_6m"}
    data["layer_catalog"]["ground_fixture_conductor"] = "ME1"
    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(write_profile(tmp_path / "profiles", data, "me1_6m")))
    assert fixture_exemptions("me1_6m") == {("max_width", "ME1")} and fixture_exemptions("demo_6m") == {("max_width", "M1")}
    report = validate_profile("me1_6m", generate=True)
    assert report.passed and "6 PASS, 0 FAIL, 0 SKIP" in report.format(), report.format()
    d = demo_spec(two_devices=True).model_dump(mode="json")
    for device in d["devices"]:
        device["profile"] = "me1_6m"
    spec = Spec.model_validate(d)
    point = Point({"ind.outer_diameter_um": "90", "ind.width_um": "4", "xfm.primary_width_um": "6", "xfm.secondary_width_um": "5"}, "user")
    geometry = Pcell(spec).run(point, point_context(spec, tmp_path / "run"))           # the product-scope audit is part of the build
    for g in geometry.devices.values():                                                 # it passed on the exemption alone
        assert {(v.kind, v.layer) for v in audit_gds(g.gds_path, "me1_6m").violations} == {("max_width", "ME1")}


def test_twelve_metals_with_rdl_on_top_build_every_family(tmp_path, monkeypatch):
    from ic_opt.em.pcell._pcell_core import max_opening
    from ic_opt.em.pcell.drc_audit import (
        audit_gds,
        fixture_exemptions,
        product_scope_record,
        require_layers_from_config,
    )
    from ic_opt.em.pcell.generator_plugin import PLUGIN_GENERATORS

    root = write_profile(tmp_path, rdl12(demo()), "rdl12")
    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(root))
    report = validate_profile("rdl12", generate=True)
    assert report.passed, report.format()
    assert "6 PASS, 0 FAIL, 0 SKIP" in report.format()                                     # the smoke's plain top: M11
    profile = get_process_rule_profile("rdl12")
    for family in GENERATION_FAMILIES:
        gen = PLUGIN_GENERATORS[family]
        config = gen.config_model.model_validate(_canonical_config(family, profile, "rdl12", 12, max_opening))
        assert "RDL" in {v for k, v in config.model_dump().items() if k.endswith("metal")}
        with tempfile.TemporaryDirectory() as out:
            geometry = gen.generate(config, outdir=Path(out), gds_name="rdl.gds")
            record = product_scope_record(audit_gds(geometry.gds_path, "rdl12"), require_layers_from_config(family, config.model_dump()),
                                          ignore_findings=fixture_exemptions(profile))
        assert record["outcome"] == "pass", (family, record)


def test_a_gds_layer_claimed_twice_is_reported_unless_vias_share_a_landing(tmp_path):
    clash = demo()
    clash["layer_catalog"]["vias"]["VIA5"]["drawing"] = [71, 0]                             # VIA1's layer; VIA1 and VIA5 share no metal
    report = validate_profile("clash", extra_dirs=(write_profile(tmp_path, clash | {"process_id": "clash"}, "clash"),))
    assert "GDS layer 71/0 is claimed by layer_catalog.vias.VIA1, layer_catalog.vias.VIA5" in report.format() and not report.passed
    contact = demo()
    contact["layer_catalog"]["vias"]["VIA2"]["drawing"] = [71, 0]                           # VIA1 (M1-M2) and VIA2 (M2-M3) both land on M2
    labels_on_drawing = contact["layer_catalog"]["conductors"]["M3"]
    labels_on_drawing["pin"] = labels_on_drawing["drawing"]
    report = validate_profile("contact", extra_dirs=(write_profile(tmp_path, contact | {"process_id": "contact"}, "contact"),))
    assert next(s for s in report.stages if s.name == "consistency").status == "PASS", report.format()
