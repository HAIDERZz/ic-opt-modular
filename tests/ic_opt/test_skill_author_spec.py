"""The spec-authoring skill stays true to the schema: every complete example in it validates, every Spec section has its
reference heading, and the rules it states name real fields (2026-09-27, after the N-27 real-scenario acceptance wrote
its specs from the docs alone)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from ic_opt.spec import Spec

SKILL = Path(__file__).resolve().parents[2] / "skills" / "author-spec" / "SKILL.md"


def examples() -> dict[str, dict]:
    text = SKILL.read_text(encoding="utf-8")
    out = {}
    for block in re.findall(r"```yaml\n(# complete: (\w+)\n.*?)```", text, flags=re.DOTALL):
        out[block[1]] = yaml.safe_load(block[0])
    return out


@pytest.mark.parametrize("shape", ["circuit", "em_circuit", "joint", "em_only"])
def test_every_complete_example_is_a_valid_spec(shape):
    spec = Spec.model_validate(examples()[shape])
    if shape == "circuit":
        assert spec.corner_ids == ["tt", "ss"] and spec.circuit_variables == ["W", "VB", "F"] and not spec.devices
    if shape == "em_circuit":
        assert spec.circuit_variables == [] and spec.bindings[0].instance == "NPORT0"
        assert {m.name for m in spec.metrics_for_device("xfmr")} == {"Qp_60g", "k_60g", "Lp_lf"}
    if shape == "joint":                     # N-51 (2026-09-28): the main use had no complete example
        assert spec.circuit_variables == ["WCS", "VB_RF", "FCS"] and spec.corner_ids == ["tt", "ss", "ff"]
        assert spec.device_fields(spec.device("xfmr")) == {"primary_width_um": "xfmr.primary_width_um",
                                                           "secondary_width_um": "xfmr.secondary_width_um"}
        assert [b.kept for b in spec.bindings] == [["P1", "N1", "CTP", "P2", "N2", "CTS"]] * 2
        assert {m.name for m in spec.metrics_for_device("xfmr")} == {"k_lf", "SRF_p"}
    if shape == "em_only":
        assert not spec.testbenches and spec.device_fields(spec.devices[0]) == {v.name: v.name for v in spec.variables}


def test_every_spec_section_has_a_reference_heading():
    text = SKILL.read_text(encoding="utf-8")
    headings = set(re.findall(r"^### (.+)$", text, flags=re.MULTILINE))
    for field in Spec.model_fields:
        assert any(f"`{field}`" in h for h in headings), field


def test_the_skill_states_the_rules_the_acceptances_tripped_over():
    text = SKILL.read_text(encoding="utf-8")
    for rule in ("top-level\n`parameters` entry of **every** testbench", "non_scalar", "sNp column order",
                 "`simultaneous_frequencies: 0`", "the same\nnumbers at every corner", "approval point"):
        assert rule in text, rule
