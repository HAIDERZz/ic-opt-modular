"""T16 R-23: the pcell snaps to the profile's manufacturing grid (``layout_rules.manufacturing_grid_um``).

The 5 nm grid used to be a constant of the construction code. It is now a profile field, 0.005 by default, read by
every grid helper and grid-step search of a build on that profile: the packaged demo and the private profiles draw
exactly as before, and a 10 nm profile draws every family on 10 nm and still passes the audit.
"""
from __future__ import annotations

import copy
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from ic_opt.em.pcell import stack
from ic_opt.em.pcell.process_rules import (
    PROFILE_DIRS_ENV_VAR,
    ProcessRuleProfile,
    get_process_rule_profile,
)
from ic_opt.em.pcell.profile_validation import GENERATION_FAMILIES
from tests.ic_opt.pcell.test_metal_stack import demo
from tests.ic_opt.pcell.test_profile_validation import write_profile

klayout = pytest.importorskip("klayout.db")


def gridded(grid: float | None, profile_id: str) -> dict:
    d = copy.deepcopy(demo()) | {"process_id": profile_id}
    if grid is None:
        d["layout_rules"].pop("manufacturing_grid_um", None)
    else:
        d["layout_rules"]["manufacturing_grid_um"] = grid
    return d


@pytest.fixture
def grid10(tmp_path, monkeypatch):
    root = tmp_path / "profiles"
    write_profile(root, gridded(None, "demo_nogrid"), "demo_nogrid")
    monkeypatch.setenv(PROFILE_DIRS_ENV_VAR, str(write_profile(root, gridded(0.01, "demo_grid10"), "demo_grid10")))
    return "demo_grid10"


def test_the_grid_is_5nm_unless_stated_and_a_whole_number_of_nanometres():
    assert get_process_rule_profile("demo_6m").layout_rules.manufacturing_grid_um == 0.005
    assert ProcessRuleProfile.model_validate(gridded(None, "x")).layout_rules.manufacturing_grid_um == 0.005
    assert ProcessRuleProfile.model_validate(gridded(0.01, "x")).layout_rules.manufacturing_grid_um == 0.01
    for bad in (0.0, -0.01, 0.0025):
        with pytest.raises(ValidationError, match="manufacturing_grid_um"):
            ProcessRuleProfile.model_validate(gridded(bad, "x"))


def test_the_grid_helpers_follow_the_profile_of_the_build():
    from ic_opt.em.pcell._pcell_core import (
        ceiltogrid,
        floortogrid,
        octagon,
        roundtogrid,
        snap_nm_to_grid,
    )

    def helpers():
        return (stack.grid_um(), ceiltogrid(0.123), roundtogrid(0.124), floortogrid(0.129), snap_nm_to_grid(123))

    assert helpers() == pytest.approx((0.005, 0.125, 0.125, 0.125, 125))
    with stack.use_stack(ProcessRuleProfile.model_validate(gridded(0.01, "x"))):
        assert helpers() == pytest.approx((0.01, 0.13, 0.12, 0.12, 120))
        ring = octagon(100.0, 6.0)
        assert all(abs(v / 0.01 - round(v / 0.01)) < 1e-9 for v in (ring.A, ring.BA, ring.C))
        with stack.use_stack("demo_6m"):
            assert helpers() == pytest.approx((0.005, 0.125, 0.125, 0.125, 125))
        assert stack.grid_um() == 0.01
    assert stack.grid_um() == 0.005


def build(family: str, profile_id: str, out: Path):
    from ic_opt.em.pcell._pcell_core import max_opening
    from ic_opt.em.pcell.drc_audit import (
        audit_gds,
        expected_conductors,
        fixture_exemptions,
        product_scope_record,
    )
    from ic_opt.em.pcell.generator_plugin import PLUGIN_GENERATORS
    from ic_opt.em.pcell.profile_validation import _canonical_config

    profile = get_process_rule_profile(profile_id)
    gen = PLUGIN_GENERATORS[family]
    config = gen.config_model.model_validate(_canonical_config(family, profile, profile_id, 6, max_opening))
    gds = gen.generate(config, outdir=out, gds_name="grid.gds").gds_path
    record = product_scope_record(audit_gds(gds, profile_id), expected_conductors(gen, config), ignore_findings=fixture_exemptions(profile))
    return gds, record


def vertices(gds: Path) -> list[tuple[int, int]]:
    layout = klayout.Layout()
    layout.read(str(gds))
    top = layout.top_cells()[0]
    top.flatten(True)
    return [(p.x, p.y) for li in layout.layer_indexes() for shape in top.shapes(li).each() if not shape.is_text()
            for p in shape.polygon.each_point_hull()]


@pytest.mark.parametrize("family", list(GENERATION_FAMILIES))
def test_a_10nm_profile_draws_every_family_on_its_grid_and_passes_the_audit(family, grid10):
    with tempfile.TemporaryDirectory() as out:
        coarse, record = build(family, grid10, Path(out) / "10nm")
        fine, _ = build(family, "demo_6m", Path(out) / "5nm")
        assert record["outcome"] == "pass", record
        assert all(x % 10 == 0 and y % 10 == 0 for x, y in vertices(coarse))
        assert any(x % 10 or y % 10 for x, y in vertices(fine))                      # the 5 nm build uses the finer grid
        assert coarse.read_bytes() != fine.read_bytes()


def test_left_out_the_grid_draws_exactly_as_the_packaged_demo(grid10):
    with tempfile.TemporaryDirectory() as out:
        packaged, _ = build("clean_port_xfm_il", "demo_6m", Path(out) / "demo")
        stated, _ = build("clean_port_xfm_il", "demo_nogrid", Path(out) / "nogrid")
        assert packaged.read_bytes() == stated.read_bytes()


def test_the_halves_of_spacings_stay_on_the_profile_grid(grid10):
    from ic_opt.em.pcell._pcell_core import PortError, max_opening, process_rule_context
    from ic_opt.em.pcell._pcell_ind_sym import ind_sym
    from ic_opt.em.pcell.generator_plugin import PLUGIN_GENERATORS
    from ic_opt.em.pcell.profile_validation import _canonical_config

    bs = PLUGIN_GENERATORS["clean_port_xfm_bs"].config_model
    base = _canonical_config("clean_port_xfm_bs", get_process_rule_profile(grid10), grid10, 6, max_opening)
    with pytest.raises(ValidationError, match="center_spacing_um 0.01 must be a multiple of 0.02 um"):
        bs.model_validate(base | {"center_spacing_um": 0.01})
    bs.model_validate(base | {"center_spacing_um": 0.02})
    bs.model_validate(base | {"process_profile": "demo_6m", "center_spacing_um": 0.01})    # 0.005 um grid: 0.01 is fine
    with pytest.raises(PortError, match="STRAIGHT_EXTENSION must use 0.02 um steps"):
        ind_sym(OD=100.0, W=5.0, OPENING=8.0, LEAD=20.0, S=3.0, NT=2, TOP_ME="6", BTM_ME="5",
                STRAIGHT_EXTENSION=0.01, process=process_rule_context(grid10))
