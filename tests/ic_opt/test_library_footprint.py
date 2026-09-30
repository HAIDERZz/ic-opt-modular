"""T18.1 section 2: the footprint of a drawn device -- the bounding box of what the generator draws for it without the
ground fixture it gets for EMX -- measured from a GDS the generator wrote (ic_opt.em.pcell.footprint)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("klayout.db")

import klayout.db as kdb

from ic_opt.em.pcell import footprint as fp
from ic_opt.em.pcell import get_generator
from ic_opt.em.pcell.generator_plugin import _clean_port
from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter
from tests.ic_opt.test_library import FIXTURE

PROFILE = "demo_6m"
IND = {"port_order": ["P1", "N1"], "outer_diameter_um": 120.0, "width_um": 5.0, "spacing_um": 2.0, "turns": 2, "opening_um": 8.0,
       "lead_length_um": 20.0, "metal": "6", "process_profile": PROFILE, "ground_fixture": FIXTURE}
XFM = {"port_order": ["P1", "N1", "P2", "N2"], "primary_outer_diameter_um": 100.0, "secondary_outer_diameter_um": 120.0,
       "primary_width_um": 5.0, "secondary_width_um": 5.0, "primary_opening_um": 8.0, "secondary_opening_um": 8.0,
       "primary_lead_length_um": 20.0, "secondary_lead_length_um": 20.0, "center_spacing_um": 0.0, "primary_metal": "6",
       "secondary_metal": "5", "process_profile": PROFILE, "ground_fixture": FIXTURE}
PGS = {"strip_width_um": 1.0, "strip_spacing_um": 2.0, "margin_um": 5.0}


def generate(generator: str, config: dict, outdir: Path):
    g = get_generator(generator, plugin_module="builtin:clean_port")
    model = g.config_model.model_validate(config)
    return model, g.generate(model, outdir=outdir, gds_name="device.gds")


def without_fixture(generator: str, c) -> tuple[float, float, float, float]:
    """The generator's own drawing of the device with no ground fixture (the family function called as ``generate`` calls
    it, ``ground_fixture=None``): the box around every shape, in um."""
    p = _clean_port()
    process = p.process_rule_context(c.process_profile)
    if generator == "clean_port_ind_sym":
        cell = p.ind_sym(OD=c.outer_diameter_um, W=c.width_um, OPENING=c.opening_um, LEAD=c.lead_length_um, S=c.spacing_um, NT=c.turns,
                         TOP_ME=c.metal, BTM_ME=c.metal, CT_ME=c.ct_metal, STRAIGHT_EXTENSION=c.straight_extension_um,
                         port_order=list(c.port_order), ground_fixture=None, process=process, PORT_SPACING=c.port_spacing_um)
    else:
        cell = p.xfm_bs(OD_P=c.primary_outer_diameter_um, OD_S=c.secondary_outer_diameter_um, W_P=c.primary_width_um,
                        W_S=c.secondary_width_um, OPENING_P=c.primary_opening_um, OPENING_S=c.secondary_opening_um,
                        LEAD_P=c.primary_lead_length_um, LEAD_S=c.secondary_lead_length_um, CENTER_SPACING=c.center_spacing_um,
                        PRI_ME=c.primary_metal, SEC_ME=c.secondary_metal, CT_P_ME=c.ct_primary_metal, CT_S_ME=c.ct_secondary_metal,
                        STRAIGHT_EXTENSION=c.straight_extension_um, ground_fixture=None, process=process,
                        PORT_SPACING_P=c.primary_port_spacing_um, PORT_SPACING_S=c.secondary_port_spacing_um)
    xs = [x for _layer, pts in cell.flat_shapes() for x, _y in pts]
    ys = [y for _layer, pts in cell.flat_shapes() for _x, y in pts]
    return min(xs) / 1e3, min(ys) / 1e3, max(xs) / 1e3, max(ys) / 1e3


def gds_box(path: Path) -> kdb.Box:
    layout = kdb.Layout()
    layout.read(str(path))
    return layout.top_cells()[0].dbbox()


@pytest.mark.parametrize(("generator", "config"), [("clean_port_ind_sym", IND), ("clean_port_xfm_bs", XFM)], ids=["ind_sym", "xfm_bs"])
def test_the_footprint_is_the_drawn_device_without_its_ground_fixture(tmp_path, generator, config):
    """Against the generator's own numbers: the same device drawn with no fixture has exactly this box; its ports (the lead
    tips, from the manifest) and its diameters are its edges; the GDS with the fixture is wider by the ring on each side."""
    model, result = generate(generator, config, tmp_path)
    box = fp.footprint(result.gds_path, profile=PROFILE, generator=generator)
    x0, y0, x1, y1 = without_fixture(generator, model)
    assert box == {"width_um": round(x1 - x0, 3), "height_um": round(y1 - y0, 3), "area_um2": round((x1 - x0) * (y1 - y0), 3)}
    ports = json.loads(result.manifest_path.read_text())["ports"]
    diameter = max(config.get("outer_diameter_um", 0), config.get("primary_outer_diameter_um", 0), config.get("secondary_outer_diameter_um", 0))
    if generator == "clean_port_ind_sym":                          # the winding from -OD/2, the leads out to the ports on the right
        assert box["width_um"] == pytest.approx(max(p["x_um"] for p in ports) + diameter / 2, abs=1e-9)
    else:                                                          # the primary's ports on the left, the secondary's on the right
        assert box["width_um"] == pytest.approx(max(p["x_um"] for p in ports) - min(p["x_um"] for p in ports), abs=1e-9)
    assert box["height_um"] == pytest.approx(diameter, abs=0.011)   # the octagon's flats, on the 0.005 um grid
    whole = gds_box(result.gds_path)
    ring = FIXTURE["ring_width_um"]
    assert whole.width() >= box["width_um"] + 2 * ring and whole.height() >= box["height_um"] + 2 * ring


def test_the_shield_strips_belong_to_the_fixture(tmp_path):
    """A patterned ground shield is drawn on the fixture conductor, tied to the ring: the footprint does not change with it
    while the fixture conductor's layer does."""
    _, plain = generate("clean_port_ind_sym", IND, tmp_path / "plain")
    _, shielded = generate("clean_port_ind_sym", {**IND, "pgs": PGS}, tmp_path / "pgs")
    layer = tuple(get_geometry_rule_adapter(PROFILE).layer("M1").drawing)
    area = []
    for result in (plain, shielded):
        layout = kdb.Layout()
        layout.read(str(result.gds_path))
        area.append(kdb.Region(layout.top_cells()[0].begin_shapes_rec(layout.layer(*layer))).area())
    assert area[1] > area[0]
    assert fp.footprint(shielded.gds_path, profile=PROFILE, generator="clean_port_ind_sym") == fp.footprint(
        plain.gds_path, profile=PROFILE, generator="clean_port_ind_sym")


def test_a_fixture_that_cannot_be_told_apart_gives_no_box(tmp_path):
    """Another plugin's generator gives no guarantee that the fixture conductor holds the fixture alone: refused, never a
    box that might include the fixture. So is a profile this machine cannot load, and a GDS with nothing but the fixture."""
    _, result = generate("clean_port_ind_sym", IND, tmp_path)
    with pytest.raises(fp.FootprintError, match="not one of the built-in families"):
        fp.footprint(result.gds_path, profile=PROFILE, generator="clean_port_ind_sym", plugin="/opt/plugins/mine.py")
    with pytest.raises(fp.FootprintError, match="not one of the built-in families"):
        fp.fixture_layer(PROFILE, "my_spiral")
    with pytest.raises(fp.ProfileUnavailable, match="process profile 'no_such_profile' cannot be loaded here"):
        fp.fixture_layer("no_such_profile", "clean_port_ind_sym")
    layer = fp.fixture_layer(PROFILE, "clean_port_ind_sym")
    layout = kdb.Layout()
    layout.dbu = 0.001
    layout.create_cell("fixture").shapes(layout.layer(*layer)).insert(kdb.Box(0, 0, 1000, 1000))
    only = tmp_path / "fixture.gds"
    layout.write(str(only))
    with pytest.raises(fp.FootprintError, match="no shape outside the fixture conductor's layer"):
        fp.measure(only, layer)
