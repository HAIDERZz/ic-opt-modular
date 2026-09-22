"""M1.3: the ground fixture follows each port's orientation, and its ring is a pure function of the port and body coordinates."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("klayout.db")

import klayout.db as kdb
from pydantic import ValidationError

from ic_opt.em.pcell import get_generator
from ic_opt.em.pcell._pcell_core import (
    Cell,
    finalize_emx_ports,
    metal_drawing_pin,
    metal_layer,
)
from ic_opt.em.pcell.fixture import (
    GroundFixtureConfig,
    add_ground_fixture,
)
from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter
from tests.ic_opt.pcell.test_golden import FIXTURE, XFM_PORTS


def test_stub_side_is_the_port_orientation_not_the_nearest_edge():
    """A lead that leaves to +x near the top of a tall body still gets a horizontal stub on the right."""
    body = Cell("body", "test", {})
    body.add_rect(metal_layer(6), -20.0, -100.0, 20.0, 100.0)
    lead = Cell("lead", "test", {})
    lead.add_rect(metal_layer(6), 0.0, 0.0, 10.0, 4.0)
    lead.add_emx_port(name="P1", logical_name="P1", metal=6, label_layer=(66, 1), x_um=10.0, y_um=2.0, lead_zone_um=(0.0, 0.0, 10.0, 4.0))
    body.inst(lead, (20.0, 95.0), "R0")                      # port at (30, 97): 3 um from the top edge, 0 from the right edge
    body.emx_ports = finalize_emx_ports(body)
    add_ground_fixture(body, GroundFixtureConfig(inner_margin_um=15.0, ring_width_um=10.0, stub_width_um=4.0, stub_length_um=2.0, stub_chamfer_um=0.0))
    m1_draw, _ = metal_drawing_pin(1, None)
    stubs = [pts for layer, pts in body.flat_shapes() if layer == m1_draw and {y for _, y in pts} == {95_000, 99_000}]
    assert len(stubs) == 1 and sorted({x for x, _ in stubs[0]}) == [30_000, 35_000]   # from the port tip 2 um outward along +x, stub_width tall
    assert body.emx_ports[0]["reference"] == "G01"


@pytest.mark.parametrize("cs", [0.0, 0.01, 0.03, 1.23, 3.01, 6.0, 12.0, 24.0])
def test_ring_edge_is_exactly_the_farther_of_stub_reach_and_body_margin(cs, tmp_path):
    g = get_generator("clean_port_xfm_bs", plugin_module="builtin:clean_port")
    cfg = {"port_order": XFM_PORTS, "primary_outer_diameter_um": 100.0, "secondary_outer_diameter_um": 120.0, "primary_width_um": 5.0,
           "secondary_width_um": 5.0, "primary_opening_um": 8.0, "secondary_opening_um": 8.0, "primary_lead_length_um": 20.0,
           "secondary_lead_length_um": 20.0, "center_spacing_um": cs, "primary_metal": "6", "secondary_metal": "5",
           "process_profile": "demo_6m", "ground_fixture": FIXTURE}
    r = g.generate(g.config_model.model_validate(cfg), outdir=tmp_path, gds_name="bs.gds")
    ports = {p["logical_name"]: p for p in json.loads(r.manifest_path.read_text())["ports"]}
    adapter = get_geometry_rule_adapter("demo_6m")
    layout = kdb.Layout()
    layout.read(str(r.gds_path))
    top = layout.top_cells()[0]
    ring = kdb.Region(top.begin_shapes_rec(layout.layer(*adapter.layer("M1").drawing))).merged()
    inner = (kdb.Region(ring.bbox()) - ring).bbox()
    body_left, body_right = cs / 2 - 60.0, cs / 2 + 60.0              # the wider secondary ring (OD 120 at +cs/2) is the protruding body
    expect_left = min(ports["P1"]["x_um"] - FIXTURE["stub_length_um"], body_left - FIXTURE["inner_margin_um"])
    expect_right = max(ports["P2"]["x_um"] + FIXTURE["stub_length_um"], body_right + FIXTURE["inner_margin_um"])
    assert inner.left * 1e-3 == pytest.approx(expect_left, abs=1e-9) and inner.right * 1e-3 == pytest.approx(expect_right, abs=1e-9)


def test_center_spacing_must_sit_on_the_coordinate_grid():
    g = get_generator("clean_port_xfm_bs", plugin_module="builtin:clean_port")
    cfg = {"port_order": XFM_PORTS, "primary_outer_diameter_um": 100.0, "secondary_outer_diameter_um": 100.0, "primary_width_um": 5.0,
           "secondary_width_um": 5.0, "primary_opening_um": 8.0, "secondary_opening_um": 8.0, "primary_lead_length_um": 20.0,
           "secondary_lead_length_um": 20.0, "center_spacing_um": 1.2345, "primary_metal": "6", "secondary_metal": "5",
           "process_profile": "demo_6m", "ground_fixture": FIXTURE}
    with pytest.raises(ValidationError, match="multiple_of"):
        g.config_model.model_validate(cfg)
