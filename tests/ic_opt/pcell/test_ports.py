"""M2.1: ports are objects with a width, an orientation and a differential pair, and the manifest carries them."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("klayout.db")

from ic_opt.em.pcell._pcell_core import (
    Cell,
    PortError,
    finalize_emx_ports,
    port_direction,
    port_pair,
)
from tests.ic_opt.pcell.test_golden import CASES, build


def test_direction_is_the_zone_edge_the_point_sits_on():
    assert port_direction((2000, 500), (0, 0, 2000, 1000)) == ((1, 0), 1000)
    assert port_direction((0, 500), (0, 0, 2000, 1000)) == ((-1, 0), 1000)
    assert port_direction((700, 1000), (0, 0, 2000, 1000)) == ((0, 1), 2000)
    assert port_direction((700, 0), (2000, 1000, 0, 0)) == ((0, -1), 2000)          # an unsorted zone is normalized
    for point in ((1000, 500), (2000, 1000), (3000, 500)):                          # inside, corner, outside
        with pytest.raises(PortError, match="exactly one edge"):
            port_direction(point, (0, 0, 2000, 1000))
    assert port_pair("P1") == "N1" and port_pair("N2") == "P2" and port_pair("CT") is None and port_pair("CTP") is None


def test_orientation_follows_instance_transforms_and_pairs_need_a_partner():
    leaf = Cell("leaf", "test", {})
    leaf.add_emx_port(name="P1", logical_name="P1", metal=6, label_layer=(66, 1), x_um=5.0, y_um=1.0, lead_zone_um=(0.0, 0.0, 5.0, 2.0))
    top = Cell("top", "test", {})
    top.inst(leaf, (10.0, 0.0), "R0")
    top.inst(leaf, (-10.0, 0.0), "MY")                       # mirrored across y: faces -x
    rotated = Cell("rot", "test", {})
    rotated.inst(leaf, (0.0, 0.0), "R90")                    # faces +y
    ports = finalize_emx_ports(top)
    assert [(p["point_nm"][0], p["orientation_deg"], p["width_um"], p["pair"]) for p in ports] == [(15000, 0, 2.0, None), (-15000, 180, 2.0, None)]
    assert finalize_emx_ports(rotated)[0]["orientation_deg"] == 90
    both = Cell("both", "test", {})
    both.inst(leaf, (0.0, 0.0), "R0")
    n1 = Cell("n1", "test", {})
    n1.add_emx_port(name="N1", logical_name="N1", metal=6, label_layer=(66, 1), x_um=5.0, y_um=1.0, lead_zone_um=(0.0, 0.0, 5.0, 2.0))
    both.inst(n1, (0.0, -10.0), "R0")
    assert [p["pair"] for p in finalize_emx_ports(both)] == ["N1", "P1"]


@pytest.mark.parametrize("name", ["ind_sym_nt2_ct", "xfm_bs_ct", "xfm_tw_nr3", "xfm_il_nt2_ct"])
def test_manifest_describes_every_port(name, tmp_path):
    gds = build(name, tmp_path)
    manifest = json.loads((gds.parent / "geometry_manifest.json").read_text())
    ports = {p["logical_name"]: p for p in manifest["ports"]}
    assert set(ports) == set(CASES[name][1]["port_order"]) and manifest["schema_version"] == "1.1"
    for p in ports.values():
        assert p["width_um"] > 0 and p["orientation_deg"] in (0, 90, 180, 270) and p["reference"].startswith("G")
        x0, y0, x1, y1 = p["lead_zone_um"]
        assert x0 <= p["x_um"] <= x1 and y0 <= p["y_um"] <= y1
    assert ports["P1"]["pair"] == "N1" and ports["N1"]["pair"] == "P1"
    assert all(ports[tap]["pair"] is None for tap in ports if tap.startswith("CT"))
    for p in ports.values():                                                                    # every lead leaves the device outward
        outward = {0: p["x_um"] > 0, 180: p["x_um"] < 0, 90: p["y_um"] > 0, 270: p["y_um"] < 0}[p["orientation_deg"]]
        assert outward, p
