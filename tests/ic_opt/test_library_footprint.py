"""T18.1 section 2: answers carry the footprint -- the bounding box of the drawn device without the ground fixture it gets
for EMX -- measured from a GDS the generator wrote (ic_opt.em.pcell.footprint): a library row's from the GDS kept beside
its sNp, a drawn candidate's from its drawing, a lib_design leader's from its pcell's."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

pytest.importorskip("klayout.db")

import klayout.db as kdb

from ic_opt.blocks import library as library_blocks
from ic_opt.em.pcell import footprint as fp
from ic_opt.em.pcell import get_generator
from ic_opt.em.pcell.generator_plugin import _clean_port
from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter
from ic_opt.library import dataset, suggest
from ic_opt.library import query as q
from ic_opt.recipe import Run, load_recipe
from ic_opt.site import Site
from ic_opt.spec import Spec, load_spec
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor
from tests.ic_opt.library_fixtures import LOCAL, build_library, params
from tests.ic_opt.test_library import FIXTURE, NT2, run_part, write_manifest

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


@pytest.fixture(scope="module")
def mixed(tmp_path_factory) -> Path:
    """build_library's 105 rows (analytic sNp, no GDS) and a third part ``gds`` of three rows built by the real pcell (a fake
    EMX), which keep their GDS beside their sNp."""
    root = build_library(tmp_path_factory.mktemp("mixed"))
    run_part(root, "gds", 60, [{"outer_diameter_um": od, "width_um": 4.5, "spacing_um": 2, "turns": 2} for od in (105, 135, 165)])
    doc = yaml.safe_load((root / "library.yaml").read_text(encoding="utf-8"))
    doc["strata"]["ind_demo"]["parts"].append({"store": "gds"})
    (root / "library.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return root


def test_library_rows_carry_the_footprint_of_their_own_gds_and_none_without(mixed, monkeypatch):
    lib = q.Library(mixed, limits=LOCAL)
    ds = lib.dataset("ind_demo")
    known = lib.footprints("ind_demo")
    gds_rows = [r for r in ds.rows if r.part == "gds"]
    assert len(gds_rows) == 3 and len(known) == len(ds.rows)
    for r in gds_rows:
        gds = mixed / Path(r.snp).parent / "ind.gds"
        assert known[(r.part, r.obs_id)] == (fp.footprint(gds, profile=PROFILE, generator="clean_port_ind_sym"), None)
        assert known[(r.part, r.obs_id)][0]["width_um"] > r.coords["outer_diameter_um"]
    assert {known[(r.part, r.obs_id)] for r in ds.rows if r.part != "gds"} == {(None, "no GDS beside its sNp")}
    (cached,) = (mixed / ".cache").glob("footprint-ind_demo-*.json")
    assert cached.name == f"footprint-ind_demo-{ds.key}.json"
    monkeypatch.setattr(fp, "measure", lambda *a, **k: pytest.fail("a new library reads the footprints from the cache file"))
    assert q.Library(mixed, limits=LOCAL).footprints("ind_demo") == known


def test_library_rows_with_the_fixture_off_the_bottom_metal_keep_the_devices_own_footprint(tmp_path):
    """T19.2 (found while testing it): the dataset measured every row against the bottom metal's layer, so a row whose
    fixture was drawn on another metal (T19.1 ``metal: auto``) got the ring's outer box. Now each row goes through
    ``footprint()``: the same geometry built with the fixture on the bottom metal and on the auto metal has the same
    footprint, the device's own, smaller than the ring by the margin and the ring width on each side."""
    root = build_library(tmp_path / "lib")
    points = [{"outer_diameter_um": od, "width_um": 4.5, "spacing_um": 2, "turns": 2} for od in (105, 135)]
    run_part(root, "gds", 60, points)
    run_part(root, "gds_auto", 60, points, fixture={**FIXTURE, "metal": "auto"})
    doc = yaml.safe_load((root / "library.yaml").read_text(encoding="utf-8"))
    doc["strata"]["ind_demo"]["parts"] += [{"store": "gds"}, {"store": "gds_auto"}]
    (root / "library.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    lib = q.Library(root, limits=LOCAL)
    ds = lib.dataset("ind_demo")
    known = lib.footprints("ind_demo")
    by_part = {part: {r.coords["outer_diameter_um"]: known[(r.part, r.obs_id)] for r in ds.rows if r.part == part} for part in ("gds", "gds_auto")}
    assert set(by_part["gds"]) == set(by_part["gds_auto"]) == {105.0, 135.0}
    for od in (105.0, 135.0):
        box, why = by_part["gds_auto"][od]
        assert why is None and box == by_part["gds"][od][0]
        assert fp.recorded_fixture_metal(next(root.glob("gds_auto/.icopt/sims/*/em/ind/ind.gds"))) != "M1"
        assert box["width_um"] < od + 2 * (FIXTURE["inner_margin_um"] + FIXTURE["ring_width_um"])
    cached = json.loads((root / ".cache" / dataset.footprints_file(ds)).read_text(encoding="utf-8"))
    assert cached["version"] == dataset.FOOTPRINT_VERSION == 2


def test_lib_query_gives_a_rows_footprint_and_draws_one_only_when_asked(mixed, tmp_path):
    lib = q.Library(mixed, limits=LOCAL)
    row = next(r for r in lib.dataset("ind_demo").rows if r.part == "gds")
    at_row = library_blocks.query(lib, "ind_demo", row.coords, "Lp_lf")
    assert at_row["measured"]["part"] == "gds" and at_row["footprint"] == lib.footprints("ind_demo")[(row.part, row.obs_id)][0]
    geometry = params(115, 4.5, 2, 2)                                   # no row there
    plain = library_blocks.query(lib, "ind_demo", geometry, "Lp_lf")
    assert plain["footprint"] is None and plain["footprint_why"].startswith("not a library row: footprint=true draws")
    drawn = library_blocks.query(lib, "ind_demo", geometry, "Lp_lf", footprint=True)
    run_part(tmp_path, "check", 60, [geometry])                         # the same geometry through the real pcell, independently
    (gds,) = (tmp_path / "check" / ".icopt" / "sims").glob("*/em/ind/ind.gds")
    assert drawn["footprint"] == fp.footprint(gds, profile=PROFILE, generator="clean_port_ind_sym") and "footprint_why" not in drawn


def test_lib_suggest_shows_measured_footprints_and_the_drawn_candidates_own(mixed):
    lib = q.Library(mixed, limits=LOCAL)
    row = next(r for r in lib.dataset("ind_demo").rows if r.part == "gds")
    tight = suggest.suggest(lib, "ind_demo", {"Lp_lf": {"target": row.values["Lp_lf"], "tol": 0.002}}, None, n=3, pool_size=256,
                            verify_build=False)
    (shown,) = [m for m in tight["measured"] if m["measured"]["part"] == "gds"]
    assert shown["footprint"] == lib.footprints("ind_demo")[(row.part, row.obs_id)][0] is not None
    wide = suggest.suggest(lib, "ind_demo", {"Lp_lf": {"target": row.values["Lp_lf"], "tol": 0.05}}, "max:Qp_peak", n=1, pool_size=256)
    (candidate,) = wide["candidates"]
    assert candidate["build"]["built"] and set(candidate["footprint"]) == {"width_um", "height_um", "area_um2"}
    assert "footprint" not in candidate["build"] and candidate["footprint"]["width_um"] > candidate["params"]["outer_diameter_um"]


def test_a_part_whose_generator_gives_no_guarantee_has_no_footprint_and_says_why(tmp_path):
    """A part drawn by another plugin: its rows keep a GDS, but its fixture cannot be told from its device."""
    run_part(tmp_path, "ind_nt2", 30, NT2[:2])
    spec = yaml.safe_load((tmp_path / "ind_nt2" / "spec.yaml").read_text(encoding="utf-8"))
    spec["devices"][0]["plugin"] = "/opt/plugins/mine.py"
    (tmp_path / "ind_nt2" / "spec.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    write_manifest(tmp_path, parts=("ind_nt2",))
    lib = q.Library(tmp_path, limits=LOCAL)
    reasons = {why for _box, why in lib.footprints("ind_demo").values()}
    assert len(reasons) == 1 and "is not one of the built-in families" in reasons.pop()
    assert not list((tmp_path / ".cache").glob("footprint-*.json"))            # nothing was measured: nothing to keep


def test_lib_design_leaders_carry_the_footprint_of_their_drawn_gds(tmp_path):
    from tests.ic_opt.test_library_stage import design_spec

    root = build_library(tmp_path / "lib")
    project = tmp_path / "design"
    project.mkdir()
    d = design_spec("design").model_dump(mode="json")
    d["metrics"], d["constraints"] = [d["metrics"][0]], []
    d["objective"] = {"direction": "maximize", "expression": "L"}
    (project / "spec.yaml").write_text(yaml.safe_dump(Spec.model_validate(d).model_dump(mode="json")), encoding="utf-8")
    site = Site({"local": LOCAL, "lab": FAKE_HOST})
    store = RunStore(project)
    run = Run(project, load_spec(project / "spec.yaml"), store, FakeSpectreExecutor(store.root / "sims"), None, site, site.host("lab"))
    load_recipe("lib_design")(run, library=str(root), budget=6, batch=6, strategy="random", top=2, seed=1)
    report = json.loads((store.root / "reports" / "lib_design.json").read_text())
    assert report["leaders"]
    for leader in report["leaders"]:
        gds = store.root / "sims" / leader["obs_id"] / "em" / "ind" / "ind.gds"
        assert leader["footprint"] == fp.footprint(gds, profile=PROFILE, generator="clean_port_ind_sym")
        assert leader["footprint"]["width_um"] > float(leader["params"]["outer_diameter_um"])


def test_footprints_are_cached_only_when_some_row_had_a_gds(tmp_path):
    """A library whose rows keep no GDS reads none and keeps no file: its cache holds the dataset's file and nothing else."""
    root = build_library(tmp_path / "lib")
    lib = q.Library(root, limits=LOCAL)
    assert set(lib.footprints("ind_demo").values()) == {(None, "no GDS beside its sNp")}
    ds = lib.dataset("ind_demo")
    assert sorted(p.name for p in (root / ".cache").iterdir()) == [f"dataset-ind_demo-{ds.key}.json"]
    assert not (root / ".cache" / dataset.footprints_file(ds)).exists()
