"""T16 R-14: the generator configs judge metal levels on the profile's own stack.

The adjacency rules (the fixture metal is reserved; a crossunder one or two levels down must not land on it; a tap sits
far enough below its winding) used to read the fixed 1P10M+AP numbering: AP was 11 on any profile, and a metal not
called M<n> was not read at all. They now read positions the way the pcell does (``ic_opt.em.pcell.stack``): on a
6-metal + AP profile ``metal: AP, ct_metal: M6`` is refused when the config is validated, as the pcell refuses it at
build; on a profile whose metals are called ME1..ME6 every rule applies; without a loadable profile the reference
convention (AP = 11) stays.
"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from ic_opt.em.pcell._pcell_core import max_opening
from ic_opt.em.pcell.generator_plugin import PLUGIN_GENERATORS
from ic_opt.em.pcell.process_rules import PROFILE_DIRS_ENV_VAR, get_process_rule_profile
from ic_opt.em.pcell.profile_validation import _canonical_config
from tests.ic_opt.pcell.test_metal_stack import demo
from tests.ic_opt.pcell.test_profile_validation import write_profile


def with_ap(base: dict) -> dict:
    """demo_6m with an aluminium-pad metal AP above M6, joined by the via VAP (every number invented)."""
    d = copy.deepcopy(base)
    d["process_id"] = "demo_ap"
    cat, emx, rules = d["layer_catalog"], d["emx_stack"], d["layout_rules"]
    cat["conductors"]["AP"] = {"drawing": [95, 0], "pin": [95, 2], "emx_name": "AP", "class": "aluminum_pad"}
    cat["vias"]["VAP"] = {"drawing": [96, 0], "emx_name": "VAP", "connects": ["M6", "AP"]}
    emx["conductors"]["AP"] = {"thickness_um": 2.8}
    emx["via_models"]["VAP"] = {"via": "VAP", "emx_effective_size_um": 1.02}
    rules["metal_width_space"]["AP"] = {"min_width_um": 1.5, "max_width_um": 30.0, "min_space_um": 1.5}
    rules["via_primitives"]["VAP"] = {"cut_size_um": [1.0, 1.0], "min_cut_space_um": 1.0, "min_enclosure_um": {"M6": 0.3, "AP": 0.3}}
    rules["passive_region"]["via_array_rules"]["VAP"] = {"min_count": 2, "max_space_um": 4.0}
    rules["passive_region"]["passive_via_array_coverage"]["modeled"].append("VAP")
    d["coverage"].update(metal_width_space=list(rules["metal_width_space"]), via_primitives=list(rules["via_primitives"]),
                         passive_via_arrays=list(rules["passive_region"]["via_array_rules"]), emx_via_models=list(emx["via_models"]))
    return d


def renamed_metals(base: dict, prefix: str = "ME") -> dict:
    """demo_6m with every metal M<n> called <prefix><n> (the bottom one declared as the fixture conductor)."""
    names = {f"M{i}": f"{prefix}{i}" for i in range(1, 7)}
    d = copy.deepcopy(base)
    d["process_id"] = "demo_me"
    cat, emx, rules = d["layer_catalog"], d["emx_stack"], d["layout_rules"]
    cat["conductors"] = {names[k]: {**v, "emx_name": names[k]} for k, v in cat["conductors"].items()}
    cat["ground_fixture_conductor"] = names["M1"]
    for via in cat["vias"].values():
        via["connects"] = [names[m] for m in via["connects"]]
    emx["conductors"] = {names[k]: v for k, v in emx["conductors"].items()}
    rules["metal_width_space"] = {names[k]: v for k, v in rules["metal_width_space"].items()}
    for primitive in rules["via_primitives"].values():
        primitive["min_enclosure_um"] = {names[k]: v for k, v in primitive["min_enclosure_um"].items()}
    for rule in rules["passive_region"]["wide_parallel_spacing"]:
        rule["metals"] = [names[m] for m in rule["metals"]]
    d["coverage"]["metal_width_space"] = list(rules["metal_width_space"])
    return d


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    root = tmp_path / "profiles"
    write_profile(root, with_ap(demo()), "demo_ap")
    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(write_profile(root, renamed_metals(demo()), "demo_me")))
    return root


def config(family: str, profile_id: str, top: int, **overrides) -> dict:
    """The profile's canonical smoke config of ``family`` on its ``top``-th metal, with ``overrides``."""
    return _canonical_config(family, get_process_rule_profile(profile_id), profile_id, top, max_opening) | overrides


def validate(family: str, data: dict):
    return PLUGIN_GENERATORS[family].config_model.model_validate(data)


def test_six_metals_and_ap_judge_the_tap_at_config_time_as_the_pcell_does(profiles, tmp_path):
    ind = config("clean_port_ind_sym", "demo_ap", 7, port_order=["P1", "N1", "CT"])
    assert ind["metal"] == "AP"
    with pytest.raises(ValidationError, match="ct_metal 'M6' must sit at least two levels below metal 'AP'"):
        validate("clean_port_ind_sym", ind | {"ct_metal": "M6"})                          # AP read as 11 let this through
    accepted = validate("clean_port_ind_sym", ind | {"ct_metal": "M5"})
    gen = PLUGIN_GENERATORS["clean_port_ind_sym"]
    gen.generate(accepted, outdir=tmp_path / "ok", gds_name="ok.gds")                        # the pcell builds what the config accepts
    refused = accepted.model_copy(update={"ct_metal": "M6"})                                  # past the config: the pcell's own guard
    with pytest.raises(ValueError, match="must sit at least two levels below the coil top metal AP"):
        gen.generate(refused, outdir=tmp_path / "no", gds_name="no.gds")


def test_metals_not_called_m_n_get_every_rule(profiles):
    reserved = "which is reserved for the ground fixture"
    with pytest.raises(ValidationError, match=f"'ME1' resolves to ME1, {reserved}"):
        validate("clean_port_ind_sym", config("clean_port_ind_sym", "demo_me", 6, metal="ME1"))
    with pytest.raises(ValidationError, match="'ME2' resolves to ME2, which would place this device's implicit crossunder on ME1"):
        validate("clean_port_xfm_ms", config("clean_port_xfm_ms", "demo_me", 6, primary_metal="ME3", secondary_metal="ME2"))
    for family in ("clean_port_xfm_balun", "clean_port_xfm_tw"):
        with pytest.raises(ValidationError, match="implicit crossunder on ME1"):
            validate(family, config(family, "demo_me", 6, metal="ME2"))
    with pytest.raises(ValidationError, match="implicit crossunder leg2 on ME1"):
        validate("clean_port_xfm_il", config("clean_port_xfm_il", "demo_me", 6, metal="ME3"))
    with pytest.raises(ValidationError, match="ct_metal 'ME5' must sit at least two levels below metal 'ME6'"):
        validate("clean_port_ind_sym", config("clean_port_ind_sym", "demo_me", 6, ct_metal="ME5", port_order=["P1", "N1", "CT"]))
    validate("clean_port_ind_sym", config("clean_port_ind_sym", "demo_me", 6, ct_metal="ME4", port_order=["P1", "N1", "CT"]))
    taps = ["P1", "N1", "P2", "N2", "CTP"]
    with pytest.raises(ValidationError, match="ct_primary_metal 'ME6' must sit below primary_metal 'ME6'"):
        validate("clean_port_xfm_bs", config("clean_port_xfm_bs", "demo_me", 6, ct_primary_metal="ME6", port_order=taps))
    with pytest.raises(ValidationError, match="ct_primary_metal 'ME6' must sit below metal 'ME6'"):
        validate("clean_port_xfm_balun", config("clean_port_xfm_balun", "demo_me", 6, ct_primary_metal="ME6", port_order=taps))
    il = config("clean_port_xfm_il", "demo_me", 6, port_order=taps)
    with pytest.raises(ValidationError, match="cannot equal metal 'ME6'"):
        validate("clean_port_xfm_il", il | {"ct_primary_metal": "ME6"})
    for leg in ("ME5", "ME4"):
        with pytest.raises(ValidationError, match="must sit at least three levels below metal 'ME6'"):
            validate("clean_port_xfm_il", il | {"ct_primary_metal": leg})
    validate("clean_port_xfm_il", il | {"ct_primary_metal": "ME3"})


def test_without_a_loadable_profile_the_reference_convention_stays():
    ind = config("clean_port_ind_sym", "demo_6m", 6) | {"process_profile": "no_such_profile", "port_order": ["P1", "N1", "CT"]}
    with pytest.raises(ValidationError, match="ct_metal '10' must sit at least two levels below metal 'AP'"):
        validate("clean_port_ind_sym", ind | {"metal": "AP", "ct_metal": "10"})                # AP = 11
    validate("clean_port_ind_sym", ind | {"metal": "AP", "ct_metal": "9"})
    with pytest.raises(ValidationError, match="'1' resolves to M1, which is reserved for the ground fixture"):
        validate("clean_port_ind_sym", ind | {"metal": "1", "port_order": ["P1", "N1"], "ct_metal": None})


def test_an_edited_profile_is_read_again(profiles):
    ind = config("clean_port_ind_sym", "demo_ap", 7, port_order=["P1", "N1", "CT"], ct_metal="M6")
    with pytest.raises(ValidationError, match="must sit at least two levels below"):
        validate("clean_port_ind_sym", ind)
    write_profile(profiles, demo() | {"process_id": "demo_ap"}, "demo_ap")                     # the same id, now without AP
    with pytest.raises(ValidationError, match="metal 'AP' is not a metal of profile demo_ap"):
        validate("clean_port_ind_sym", ind)
