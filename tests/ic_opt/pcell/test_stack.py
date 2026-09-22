"""M2.3: the profile's 3D stack is reachable from the geometry layer, recorded in the manifest, and checked against the .proc."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("klayout.db")

from ic_opt.em.pcell.proc_file import conductor_thicknesses, stack_mismatches
from ic_opt.em.pcell.process_rules import get_process_rule_profile
from ic_opt.em.pcell.profile_validation import _proc_stage
from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter
from tests.ic_opt.fakes import DEMO_PROC
from tests.ic_opt.pcell.test_golden import build


def test_adapter_exposes_the_stack():
    adapter = get_geometry_rule_adapter("demo_6m")
    assert (adapter.stack("M6").thickness_um, adapter.stack("M6").emx_name) == (3.0, "M6")
    summary = adapter.stack_summary()
    assert summary["geometry_scaling"] == 1.0 and summary["conductors"]["M5"] == 0.9 and summary["via_models"]["VIA5"] == 0.52
    with pytest.raises(ValueError, match="unknown conductor M7"):
        get_geometry_rule_adapter("demo_6m").stack("M7")


def test_manifest_records_the_stack(tmp_path):
    manifest = json.loads((build("xfm_bs", tmp_path).parent / "geometry_manifest.json").read_text())
    assert manifest["stack"]["conductors"] == {"M1": 0.2, "M2": 0.2, "M3": 0.2, "M4": 0.2, "M5": 0.9, "M6": 3.0}


def test_proc_thicknesses_are_compared_numerically(tmp_path):
    proc = conductor_thicknesses(DEMO_PROC)
    assert proc["M6"] == 3.0 and proc["M1"] == 0.2 and "VIA1" not in proc
    stack = get_geometry_rule_adapter("demo_6m").stack_summary()["conductors"]
    assert stack_mismatches(stack, proc) == []
    assert stack_mismatches(stack, {**proc, "M6": 2.8}) == ["M6: profile thickness 3 um, .proc 2.8 um"]
    assert stack_mismatches(stack, {k: v for k, v in proc.items() if k != "M5"})[0].startswith("M5: not a conductor")
    good = tmp_path / "good.proc"
    good.write_text(DEMO_PROC + "".join(f"via M{i} M{i + 1} {{ 0.1 => 0.12, 1e6 S/m }} VIA{i}\n" for i in range(2, 6)))
    assert _proc_stage(get_process_rule_profile("demo_6m"), good).status == "PASS"
    bad = tmp_path / "bad.proc"
    bad.write_text(good.read_text().replace("conductor 3.0 m6_rsh M6", "conductor 2.8 m6_rsh M6"))
    result = _proc_stage(get_process_rule_profile("demo_6m"), bad)
    assert result.status == "FAIL" and "M6: profile thickness 3 um, .proc 2.8 um" in result.details[0]
