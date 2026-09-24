"""em.validate_profile generate=true: one canonical device per family through the production generator + its DRC audit.

Ported from em-opt's validate-profile --generate tests. A healthy full-height profile builds all six families;
a profile too shallow for a family SKIPs it honestly instead of failing. The private n28_1p10m / n65_1p9m
profiles run when IC_OPT_PROFILE_DIRS points at them.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ic_opt.cli import app
from ic_opt.em.pcell.profile_validation import validate_profile
from tests.ic_opt.pcell.conftest import requires_profile
from tests.ic_opt.pcell.test_profile_validation import DEMO_RULE, MINIMAL_PROFILE, write_profile

pytest.importorskip("klayout.db")

runner = CliRunner()
DEMO_DIR = DEMO_RULE.parent
FAMILIES = ["clean_port_ind_sym", "clean_port_xfm_balun", "clean_port_xfm_bs", "clean_port_xfm_il", "clean_port_xfm_ms", "clean_port_xfm_tw"]


def demo_variant(tmp_path: Path, name: str, mutate) -> Path:
    data = yaml.safe_load(DEMO_RULE.read_text(encoding="utf-8"))
    mutate(data)
    (tmp_path / name).mkdir()
    (tmp_path / name / "rule.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return tmp_path / name


def test_demo_6m_generation_all_six_families_pass(tmp_path: Path) -> None:
    result = runner.invoke(app, ["call", "em.validate_profile", str(DEMO_DIR), "generate=true", f"out={tmp_path}"])
    assert result.exit_code == 0, result.output
    assert "[generation] PASS" in result.output and "6 PASS, 0 FAIL, 0 SKIP" in result.output
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_dir()) == FAMILIES              # out= keeps one artifact dir per family
    assert (tmp_path / "clean_port_ind_sym" / "smoke_clean_port_ind_sym.gds").is_file()


@requires_profile("n28_1p10m")
def test_n28_generation_all_six_families_pass() -> None:
    report = validate_profile("n28_1p10m", generate=True)
    assert report.passed, report.format()
    assert "6 PASS, 0 FAIL, 0 SKIP" in report.format()


@requires_profile("n65_1p9m")
def test_n65_generation_all_six_families_pass() -> None:
    report = validate_profile("n65_1p9m", generate=True)
    assert report.passed, report.format()
    assert "6 PASS, 0 FAIL, 0 SKIP" in report.format()


def test_tw_canonical_point_passes_on_coarse_top_metal(tmp_path: Path) -> None:
    # On an N65-class top metal (min_space 2.0) the tw crossing's 45-degree segment against the dive pad's corner
    # used to audit-fail; the pcell's own pad-corner slot bound guarantees the clearance, no validator-side bump.
    profile = demo_variant(tmp_path, "demo_6m_coarse", lambda d: d["layout_rules"]["metal_width_space"]["M6"].__setitem__("min_space_um", 2.0))
    result = runner.invoke(app, ["call", "em.validate_profile", str(profile), "generate=true", "families=clean_port_xfm_tw"])
    assert result.exit_code == 0, result.output
    assert "1 PASS, 0 FAIL, 0 SKIP" in result.output


def test_family_filter_generates_only_that_family(tmp_path: Path) -> None:
    result = runner.invoke(app, ["call", "em.validate_profile", str(DEMO_DIR), "generate=true", "families=clean_port_xfm_tw", f"out={tmp_path}"])
    assert result.exit_code == 0, result.output
    assert "1 PASS, 0 FAIL, 0 SKIP" in result.output
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_dir()) == ["clean_port_xfm_tw"]


def test_generation_not_requested_is_skipped() -> None:
    result = runner.invoke(app, ["call", "em.validate_profile", str(DEMO_DIR)])
    assert result.exit_code == 0, result.output
    assert "[generation] SKIPPED" in result.output


def test_too_shallow_profile_skips_families(tmp_path: Path) -> None:
    # a 2-metal stack can host none of the six canonical devices: an honest SKIP, not a failure
    report = validate_profile("min2m", extra_dirs=(write_profile(tmp_path, MINIMAL_PROFILE),), generate=True)
    assert report.passed, report.format()
    assert "0 PASS, 0 FAIL, 6 SKIP" in report.format()


def test_rule_violation_is_reported_per_family(tmp_path: Path) -> None:
    # a via enclosure no winding-width landing can hold (a transcription slip the smoke cannot size its way around):
    # generation fails and names the family
    profile = demo_variant(tmp_path, "demo_6m_broken",
                           lambda d: d["layout_rules"]["via_primitives"]["VIA5"].__setitem__("min_enclosure_um", {"M5": 3.0, "M6": 3.0}))
    result = runner.invoke(app, ["call", "em.validate_profile", str(profile), "generate=true", "families=clean_port_ind_sym"])
    assert result.exit_code == 1
    assert "[generation] FAIL" in result.output and "clean_port_ind_sym" in result.output


def set_rule(key: str, value: float, metals: tuple[str, ...]):
    def mutate(d: dict) -> None:
        for metal in metals:
            d["layout_rules"]["metal_width_space"][metal][key] = value
    return mutate


@pytest.mark.parametrize(("name", "mutate", "inside"), [
    ("wide_windings", set_rule("min_width_um", 8.0, ("M4", "M5", "M6")), lambda w: w > 8.0),        # min_width > 6
    ("wide_everything", set_rule("min_width_um", 7.0, ("M1", "M2", "M3", "M4", "M5", "M6")), lambda w: w > 7.0),
    ("narrow_windings", set_rule("max_width_um", 4.0, ("M4", "M5", "M6")), lambda w: w < 4.0),        # max_width < 6
])
def test_a_profile_whose_width_rules_exclude_6_um_still_validates(tmp_path: Path, name, mutate, inside) -> None:
    """T16 R-26: the smoke's winding width (6 um in the reference campaigns) is clamped into the width rules of the
    metals the device draws, the device grows with a wider winding, the fixture follows its conductor's rules."""
    from ic_opt.em.pcell._pcell_core import max_opening
    from ic_opt.em.pcell.process_rules import get_process_rule_profile
    from ic_opt.em.pcell.profile_validation import _canonical_config

    profile_dir = demo_variant(tmp_path, name, mutate)
    report = validate_profile(name, extra_dirs=(profile_dir.parent,), generate=True)
    assert report.passed and "6 PASS, 0 FAIL, 0 SKIP" in report.format(), report.format()
    profile = get_process_rule_profile(name, extra_dirs=(profile_dir.parent,))
    config = _canonical_config("clean_port_ind_sym", profile, name, 6, max_opening)
    assert inside(config["width_um"]) and config["ground_fixture"]["stub_width_um"] >= config["width_um"]


def test_the_demo_smoke_devices_are_the_reference_campaigns_mid_range() -> None:
    """demo_6m's canonical devices do not move: 6 um windings, the reference outer diameters and fixture."""
    from ic_opt.em.pcell._pcell_core import max_opening
    from ic_opt.em.pcell.process_rules import get_process_rule_profile
    from ic_opt.em.pcell.profile_validation import GENERATION_FAMILIES, _canonical_config

    profile = get_process_rule_profile("demo_6m")
    fixture = {"inner_margin_um": 15.0, "ring_width_um": 50.0, "stub_length_um": 2.0, "stub_chamfer_um": 0.0, "stub_width_um": 6.0}
    diameters = {"clean_port_ind_sym": (120.0,), "clean_port_xfm_bs": (120.0, 120.0), "clean_port_xfm_ms": (160.0, 120.0),
                 "clean_port_xfm_balun": (120.0, 102.0), "clean_port_xfm_tw": (160.0,), "clean_port_xfm_il": (150.0,)}
    for family in GENERATION_FAMILIES:
        config = _canonical_config(family, profile, "demo_6m", 6, max_opening)
        assert config["ground_fixture"] == fixture, family
        assert {v for k, v in config.items() if k.endswith("width_um")} == {6.0}, family
        assert tuple(v for k, v in config.items() if k.endswith("outer_diameter_um")) == diameters[family], family


def test_unknown_family_fails(tmp_path: Path) -> None:
    result = runner.invoke(app, ["call", "em.validate_profile", str(DEMO_DIR), "generate=true", "families=clean_port_xfm_zz"])
    assert result.exit_code == 1
    assert "unknown family: clean_port_xfm_zz" in result.output
