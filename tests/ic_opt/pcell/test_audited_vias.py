"""T16 R-13: the via-enclosure audit follows the profile, not a via name.

It used to check only a via called ``RV``; a process that calls its redistribution via anything else had that via
skipped without a word. Now the audited set is ``layout_rules.audited_vias`` or, left out, every via of the metal
stack; a synthetic profile whose vias carry RDL-style names is audited like any other, and a listed via must carry the
rule the audit applies.
"""
from __future__ import annotations

import copy
import tempfile
from pathlib import Path

import pytest
import yaml

from ic_opt.em.pcell.process_rules import (
    PROFILE_DIRS_ENV_VAR,
    ProcessRuleProfile,
    get_process_rule_profile,
)
from tests.ic_opt.pcell.test_metal_stack import demo, rdl12
from tests.ic_opt.pcell.test_profile_validation import write_profile

klayout = pytest.importorskip("klayout.db")

#: rdl12's vias above M6 under RDL-style names (every number stays rdl12's invented one)
RDL_NAMES = {"VIA6": "V6", "VIA7": "V7", "VIA8": "V8", "VIA9": "V9", "VIA10": "V10", "RV": "VRDL"}


def rdl_named(base: dict, names: dict[str, str] = RDL_NAMES) -> dict:
    """``base`` with its vias renamed everywhere a via name appears."""
    d = copy.deepcopy(base)
    d["process_id"] = "rdl_named"
    cat, emx, rules = d["layer_catalog"], d["emx_stack"], d["layout_rules"]
    passive = rules["passive_region"]

    def rename(section: dict) -> dict:
        return {names.get(k, k): v for k, v in section.items()}

    cat["vias"], rules["via_primitives"], passive["via_array_rules"] = (
        rename(cat["vias"]), rename(rules["via_primitives"]), rename(passive["via_array_rules"]))
    emx["via_models"] = {names.get(k, k): {**v, "via": names.get(v["via"], v["via"])} for k, v in emx["via_models"].items()}
    passive["passive_via_array_coverage"]["modeled"] = [names.get(v, v) for v in passive["passive_via_array_coverage"]["modeled"]]
    for key in ("via_primitives", "passive_via_arrays", "emx_via_models"):
        d["coverage"][key] = [names.get(v, v) for v in d["coverage"][key]]
    return d


def profile(data: dict) -> ProcessRuleProfile:
    return ProcessRuleProfile.model_validate(data)


def test_left_out_it_is_every_via_of_the_stack_bottom_first_whatever_the_names_and_key_order():
    assert profile(demo()).audited_vias == ("VIA1", "VIA2", "VIA3", "VIA4", "VIA5")
    named = rdl_named(rdl12(demo()))
    expected = ("VIA1", "VIA2", "VIA3", "VIA4", "VIA5", "V6", "V7", "V8", "V9", "V10", "VRDL")
    assert profile(named).audited_vias == expected
    shuffled = yaml.safe_load(yaml.safe_dump(named, sort_keys=True))
    shuffled["layer_catalog"]["vias"] = dict(reversed(list(shuffled["layer_catalog"]["vias"].items())))
    assert profile(shuffled).audited_vias == expected


def test_a_list_replaces_the_set_and_every_via_it_names_must_carry_its_rule():
    named = rdl_named(rdl12(demo()))
    named["layout_rules"]["audited_vias"] = ["VRDL"]
    assert profile(named).audited_vias == ("VRDL",)
    named["layout_rules"]["audited_vias"] = []                                            # explicit: no enclosure check
    assert profile(named).audited_vias == ()
    for listed, message in ((["RV"], "RV, which is not in layer_catalog.vias"), (["V9", "V9"], "lists V9 twice")):
        named["layout_rules"]["audited_vias"] = listed
        with pytest.raises(ValueError, match=message):
            profile(named)
    one_sided = copy.deepcopy(named)
    one_sided["layout_rules"]["audited_vias"] = ["VRDL"]
    one_sided["layout_rules"]["via_primitives"]["VRDL"]["min_enclosure_um"].pop("RDL")
    with pytest.raises(ValueError, match=r"via_primitives.VRDL.min_enclosure_um has no entry for \['RDL'\]"):
        profile(one_sided)
    ruleless = without_primitive(named, "VRDL")
    ruleless["layout_rules"]["audited_vias"] = ["VRDL"]
    with pytest.raises(ValueError, match="VRDL, which has no layout_rules.via_primitives entry"):
        profile(ruleless)


def test_validate_profile_names_the_field_of_a_list_it_refuses(tmp_path):
    from ic_opt.em.pcell.profile_validation import validate_profile

    bad = demo() | {"process_id": "bad_list"}
    bad["layout_rules"]["audited_vias"] = ["VIA5", "VIA9"]
    report = validate_profile("bad_list", extra_dirs=(write_profile(tmp_path, bad, "bad_list"),))
    assert not report.passed and "layout_rules.audited_vias names VIA9, which is not in layer_catalog.vias" in report.format()


def without_primitive(data: dict, via: str) -> dict:
    d = copy.deepcopy(data)
    d["layout_rules"]["via_primitives"].pop(via)
    d["coverage"]["via_primitives"].remove(via)
    return d


def pad_with_cuts(path: Path, profile_id: str, cuts: list[tuple[float, float]]) -> Path:
    """A 10 x 10 um M11 / RDL pad joined by 1 um VRDL cuts (lower-left corners ``cuts``); layers read from the profile."""
    p = get_process_rule_profile(profile_id)
    layout = klayout.Layout()
    layout.dbu = 0.001
    top = layout.create_cell("pad")
    for metal in ("M11", "RDL"):
        top.shapes(layout.layer(*p.layer_catalog.conductors[metal].drawing)).insert(klayout.DBox(0.0, 0.0, 10.0, 10.0))
    via = layout.layer(*p.layer_catalog.vias["VRDL"].drawing)
    for x, y in cuts:
        top.shapes(via).insert(klayout.DBox(x, y, x + 1.0, y + 1.0))
    layout.write(str(path))
    return path


def test_an_rdl_named_via_is_audited_and_an_under_enclosed_cut_is_found(tmp_path, monkeypatch):
    from ic_opt.em.pcell.drc_audit import audit_gds

    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(write_profile(tmp_path / "profiles", rdl_named(rdl12(demo())), "rdl_named")))
    clean = audit_gds(pad_with_cuts(tmp_path / "clean.gds", "rdl_named", [(4.5, 4.5)]), "rdl_named")
    assert "via_enclosure:VRDL" in clean.checked and clean.passed, clean.summary()     # before R-13: never checked
    short = audit_gds(pad_with_cuts(tmp_path / "short.gds", "rdl_named", [(4.5, 4.5), (0.1, 4.5)]), "rdl_named")
    (finding,) = [v for v in short.violations if v.kind == "via_enclosure"]
    assert finding.layer == "VRDL" and finding.count == 2                               # the edge cut, short on M11 and on RDL
    for left, bottom, right, top in finding.boxes_um:                                   # located just outside the pad edge, at that cut
        assert left < 0.0 <= right + 1e-9 < 0.1 and bottom < 4.5 and top > 5.5


def test_an_empty_list_turns_the_check_off_and_drawn_cuts_without_a_rule_fail_closed(tmp_path, monkeypatch):
    from ic_opt.em.pcell.drc_audit import audit_gds

    off = rdl_named(rdl12(demo()))
    off["layout_rules"]["audited_vias"] = []
    ruleless = without_primitive(rdl_named(rdl12(demo())) | {"process_id": "ruleless"}, "VRDL")   # left out: VRDL is audited
    root = tmp_path / "profiles"
    write_profile(root, off | {"process_id": "off"}, "off")
    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(write_profile(root, ruleless, "ruleless")))
    short = pad_with_cuts(tmp_path / "short.gds", "off", [(0.1, 4.5)])
    assert not [c for c in audit_gds(short, "off").checked if c.startswith("via_enclosure")]
    assert "via_enclosure:VRDL" not in audit_gds(pad_with_cuts(tmp_path / "none.gds", "ruleless", []), "ruleless").checked
    with pytest.raises(ValueError, match="no via primitive rule for VRDL"):
        audit_gds(short, "ruleless")


@pytest.mark.parametrize("family", ["clean_port_ind_sym", "clean_port_xfm_il"])
def test_devices_on_the_rdl_named_stack_pass_the_product_audit_with_their_vias_checked(family, tmp_path, monkeypatch):
    from ic_opt.em.pcell._pcell_core import max_opening
    from ic_opt.em.pcell.drc_audit import (
        audit_gds,
        fixture_exemptions,
        product_scope_record,
        require_layers_from_config,
    )
    from ic_opt.em.pcell.generator_plugin import PLUGIN_GENERATORS
    from ic_opt.em.pcell.profile_validation import _canonical_config

    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(write_profile(tmp_path, rdl_named(rdl12(demo())), "rdl_named")))
    p = get_process_rule_profile("rdl_named")
    gen = PLUGIN_GENERATORS[family]
    config = gen.config_model.model_validate(_canonical_config(family, p, "rdl_named", 12, max_opening))
    with tempfile.TemporaryDirectory() as out:
        report = audit_gds(gen.generate(config, outdir=Path(out), gds_name="rdl.gds").gds_path, "rdl_named")
    assert "via_enclosure:VRDL" in report.checked                                        # the body on RDL lands through VRDL
    record = product_scope_record(report, require_layers_from_config(family, config.model_dump()), ignore_findings=fixture_exemptions(p))
    assert record["outcome"] == "pass", record
