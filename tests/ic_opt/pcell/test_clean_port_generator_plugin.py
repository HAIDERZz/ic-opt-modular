"""Tests for the clean-port generator plugin (in-package device library)."""
import importlib.util
import json
import pathlib
import sys

import pytest
from pydantic import ValidationError

from tests.ic_opt.pcell.conftest import PACKAGE_DIR, requires_profile

pytestmark = requires_profile("n28_1p10m")
ROOT = pathlib.Path(__file__).resolve().parents[3]
PLUGIN_PATH = PACKAGE_DIR / "generator_plugin.py"


def _load_plugin():
    if "clean_port_generator_plugin" in sys.modules:
        return sys.modules["clean_port_generator_plugin"]
    spec = importlib.util.spec_from_file_location(
        "clean_port_generator_plugin", PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["clean_port_generator_plugin"] = mod
    spec.loader.exec_module(mod)
    return mod


def _fixture_dict():
    return {"inner_margin_um": 15.0, "ring_width_um": 50.0, "stub_width_um": 5.0,
            "stub_length_um": 2.0, "stub_chamfer_um": 0.0}


def _ind_sym_config_dict():
    return {
        "process_profile": "n28_1p10m",
        "port_order": ["P1", "N1"],
        "outer_diameter_um": 100.0,
        "width_um": 5.0,
        "spacing_um": 2.0,
        "opening_um": 8.0,
        "lead_length_um": 20.0,
        "turns": 2,
        "metal": "9",
        "ground_fixture": _fixture_dict(),
    }


def test_ind_sym_config_happy_path():
    gp = _load_plugin()
    cfg = gp.CleanPortIndSymConfig.model_validate(_ind_sym_config_dict())
    assert cfg.outer_diameter_um == 100.0
    assert cfg.ground_fixture.ring_width_um == 50.0


def test_config_rejects_extra_fields():
    gp = _load_plugin()
    payload = _ind_sym_config_dict()
    payload["bogus"] = 1
    with pytest.raises(ValidationError):
        gp.CleanPortIndSymConfig.model_validate(payload)


@pytest.mark.parametrize("field", ["metal"])
@pytest.mark.parametrize("m1_spelling", ["1", "M1", "m1"])
def test_ind_sym_config_rejects_m1_metal(field, m1_spelling):
    # Codex review (rounds on 8c5ef3d/fcbbc96/867b65f): every per-layer DRC
    # audit heuristic for M1 (blanket-ignore, recipe-conditional-ignore,
    # max_width-only-ignore) left a real counterexample, because the
    # mandatory ground-fixture ring/stub always shares M1 with any product
    # metal placed there. The actual fix is upstream of the audit: M1 is
    # not a valid product-metal choice at all, for any clean-port
    # generator, so this ambiguity can no longer arise.
    gp = _load_plugin()
    payload = {**_ind_sym_config_dict(), field: m1_spelling}
    with pytest.raises(ValidationError, match="reserved for the ground fixture"):
        gp.CleanPortIndSymConfig.model_validate(payload)


def test_config_requires_ground_fixture():
    gp = _load_plugin()
    payload = _ind_sym_config_dict()
    del payload["ground_fixture"]
    with pytest.raises(ValidationError):
        gp.CleanPortIndSymConfig.model_validate(payload)


def test_ind_sym_port_order_must_be_two_unique():
    gp = _load_plugin()
    payload = _ind_sym_config_dict()
    payload["port_order"] = ["P1", "N1", "P2"]
    with pytest.raises(ValidationError):
        gp.CleanPortIndSymConfig.model_validate(payload)
    payload["port_order"] = ["P1", "P1"]
    with pytest.raises(ValidationError):
        gp.CleanPortIndSymConfig.model_validate(payload)


def test_ind_sym_ctless_port_order_is_the_semantic_base(tmp_path):
    """The CT-less branch used to accept "any two unique names" (a legacy
    escape hatch), and the production plan used it with p01/p02 -- so the
    one inductor family drew different pin labels from the five
    transformers, while the docs, the catalog and the PCell's own default
    all said [P1, N1]. It is now the same fixed semantic base the CT branch
    and the transformers already enforce.
    """
    gp = _load_plugin()
    base = _ind_sym_config_dict()

    ok = gp.CleanPortIndSymConfig.model_validate(
        {**base, "port_order": ["P1", "N1"]})
    assert list(ok.port_order) == ["P1", "N1"]

    for bad in (["p01", "p02"], ["N1", "P1"], ["P1", "n1"]):
        with pytest.raises(ValidationError, match="port_order"):
            gp.CleanPortIndSymConfig.model_validate(
                {**base, "port_order": bad})


def test_ind_sym_legacy_port_order_error_tells_you_how_to_migrate():
    """A hard break with no migration text just moves the confusion into
    the user's terminal."""
    gp = _load_plugin()
    with pytest.raises(ValidationError) as excinfo:
        gp.CleanPortIndSymConfig.model_validate(
            {**_ind_sym_config_dict(), "port_order": ["p01", "p02"]})
    text = str(excinfo.value)
    assert "['P1', 'N1']" in text
    assert "emx_ports_override" in text   # EMX port names are unaffected


def test_ind_sym_ct_metal_requires_semantic_port_order():
    """ct_metal set -> the port set must be exactly [P1, N1, CT] (M13
    ticket 03: taps append to the fixed semantic base order; anything
    else fails closed)."""
    gp = _load_plugin()
    base = {**_ind_sym_config_dict(), "metal": "10", "ct_metal": "8"}
    for bad in (["p01", "p02"], ["P1", "N1"], ["CT", "P1", "N1"],
                ["p01", "p02", "p03"], ["P1", "N1", "CT", "X"]):
        with pytest.raises(ValidationError, match="port_order"):
            gp.CleanPortIndSymConfig.model_validate(
                {**base, "port_order": bad})
    cfg = gp.CleanPortIndSymConfig.model_validate(
        {**base, "port_order": ["P1", "N1", "CT"]})
    assert cfg.ct_metal == "8"
    assert cfg.port_order == ["P1", "N1", "CT"]


def test_ind_sym_config_default_has_no_ct():
    gp = _load_plugin()
    cfg = gp.CleanPortIndSymConfig.model_validate(_ind_sym_config_dict())
    assert cfg.ct_metal is None


def test_ind_sym_ct_metal_rejects_one_level_below_top():
    """ct_metal == top_metal-1 shorts the CT lead to the winding
    crossunder (drawn at top_metal-1, crossing the lead path at y=0) --
    fail closed at config validation, before any geometry or EMX spend
    (bug review 2026-07-17 N1)."""
    gp = _load_plugin()
    payload = {**_ind_sym_config_dict(), "ct_metal": "8",
               "port_order": ["P1", "N1", "CT"]}
    assert payload["metal"] == "9"
    with pytest.raises(ValidationError, match="two levels below"):
        gp.CleanPortIndSymConfig.model_validate(payload)


def test_ind_sym_ct_metal_accepts_two_levels_below_top():
    """The N1 guard binds ct_metal to the winding metal: two levels below is fine."""
    gp = _load_plugin()
    payload = {**_ind_sym_config_dict(), "metal": "10", "ct_metal": "8",
               "port_order": ["P1", "N1", "CT"]}
    cfg = gp.CleanPortIndSymConfig.model_validate(payload)
    assert cfg.ct_metal == "8"


def test_retired_field_names_are_refused_with_the_new_name():
    """M2.2: an old spelling never passes silently; the error names the replacement (or says the field is gone)."""
    gp = _load_plugin()
    with pytest.raises(ValidationError, match="top_metal is now metal"):
        gp.CleanPortIndSymConfig.model_validate({**_ind_sym_config_dict(), "top_metal": "9"})
    with pytest.raises(ValidationError, match="bottom_metal was removed"):
        gp.CleanPortIndSymConfig.model_validate({**_ind_sym_config_dict(), "bottom_metal": "8"})
    with pytest.raises(ValidationError, match="multi_turns is now secondary_turns"):
        gp.CleanPortXfmMsConfig.model_validate({**_xfm_ms_config_dict(), "multi_turns": 2})
    with pytest.raises(ValidationError, match="opening_p_um is now port_gap_p_um"):
        gp.CleanPortXfmTwConfig.model_validate({**_xfm_tw_config_dict(), "opening_p_um": 10.0})
    with pytest.raises(ValidationError, match="lead_p_um is now primary_lead_length_um"):
        gp.CleanPortXfmIlConfig.model_validate({**_xfm_il_config_dict(), "lead_p_um": 20.0})
    old = {"top_metal": "9", "bottom_metal": "8", "turns": 2, "outer_diameter_um": 100.0}
    assert gp.translate_config("clean_port_ind_sym", old) == {"metal": "9", "turns": 2, "outer_diameter_um": 100.0}


def test_ind_sym_ct_metal_rejects_m1():
    gp = _load_plugin()
    payload = {**_ind_sym_config_dict(), "metal": "10", "ct_metal": "M1",
               "port_order": ["P1", "N1", "CT"]}
    with pytest.raises(ValidationError, match="reserved for the ground fixture"):
        gp.CleanPortIndSymConfig.model_validate(payload)


def test_retired_ind_sym_ct_id_absent_with_migration_mapping():
    """clean_port_ind_sym_ct was retired in M13 ticket 03: the plugin no
    longer exports it, and the retirement map carries the replacement
    (clean_port_ind_sym + ct_metal) for old requirement files."""
    gp = _load_plugin()
    assert "clean_port_ind_sym_ct" not in gp.PLUGIN_GENERATORS
    msg = gp.RETIRED_GENERATOR_IDS["clean_port_ind_sym_ct"]
    assert "clean_port_ind_sym" in msg
    assert "ct_metal" in msg
    assert "port_order" in msg


def test_plain_ind_sym_config_keeps_accepting_top_minus_one():
    """The adjacency guard is CT-specific: a plain ind_sym only names its winding metal."""
    gp = _load_plugin()
    cfg = gp.CleanPortIndSymConfig.model_validate(_ind_sym_config_dict())
    assert cfg.metal == "9"


def test_ground_fixture_stub_width_by_port_must_be_positive():
    gp = _load_plugin()
    payload = _fixture_dict()
    payload["stub_width_by_port_um"] = {"p01": -1.0}
    with pytest.raises(ValidationError):
        gp.CleanPortGroundFixtureConfig.model_validate(payload)


def test_port_order_rejects_empty_or_whitespace_names():
    gp = _load_plugin()
    payload = _ind_sym_config_dict()
    payload["port_order"] = ["", "p02"]
    with pytest.raises(ValidationError):
        gp.CleanPortIndSymConfig.model_validate(payload)
    payload["port_order"] = ["   ", "p02"]
    with pytest.raises(ValidationError):
        gp.CleanPortIndSymConfig.model_validate(payload)


def _drawing_layer_regions(gds_path):
    import klayout.db as kdb
    ly = kdb.Layout()
    ly.read(str(gds_path))
    top = ly.top_cells()[0]
    top.flatten(True)
    out = {}
    for li in ly.layer_indexes():
        info = ly.get_info(li)
        reg = kdb.Region()
        for s in top.shapes(li).each():
            if not s.is_text():
                reg.insert(s.polygon)
        reg.merge()
        if not reg.is_empty():
            out[(info.layer, info.datatype)] = reg
    return ly, out


def test_ind_sym_generator_parity_with_direct_module_call(tmp_path):
    gp = _load_plugin()
    p = gp._clean_port()

    cfg = gp.CleanPortIndSymConfig.model_validate(_ind_sym_config_dict())
    gen = gp.CleanPortIndSymGenerator()
    result = gen.generate(cfg, outdir=tmp_path / "plugin", gds_name="cand.gds")
    assert result.generator_id == "clean_port_ind_sym"
    assert result.top_cell == "cand"                      # filename==topcell
    assert result.gds_path.name == "cand.gds"

    # Direct call with identical parameters.
    ctx = p.process_rule_context("n28_1p10m")
    fx = p.GroundFixtureConfig(inner_margin_um=15.0, ring_width_um=50.0,
                               stub_width_um=5.0, stub_length_um=2.0,
                               stub_chamfer_um=0.0)
    cell = p.ind_sym(OD=100.0, W=5.0, OPENING=8.0, LEAD=20.0, S=2.0, NT=2,
                     TOP_ME="9", BTM_ME="8", port_order=["P1", "N1"],
                     ground_fixture=fx, process=ctx)
    cell.name = "cand"
    direct_gds = tmp_path / "direct" / "cand.gds"
    direct_gds.parent.mkdir(parents=True)
    p.write_gds(cell, direct_gds)

    # Drawing-layer XOR must be empty for every layer.
    _, a = _drawing_layer_regions(result.gds_path)
    _, b = _drawing_layer_regions(direct_gds)
    assert set(a) == set(b)
    for ld in a:
        assert (a[ld] ^ b[ld]).is_empty(), f"layer {ld} differs"

    # emx_ports.txt identical to the module's own rendering (local-ref G0n).
    lines = result.emx_ports_path.read_text().strip().splitlines()
    assert lines == p.emx_port_lines(cell.emx_ports)
    # emx_ports.txt is sorted by label, so the semantic names put N1 first
    # where p01/p02 put p01 first -- assert the MAPPING, not that order.
    # The sNp column order comes from the plan's emx_ports_override, not
    # from this file (see its own note in the geometry manifest).
    assert sorted(lines) == ["-p N1=N1:G02", "-p P1=P1:G01"]

    # Manifest carries the dumped config.
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["generator_id"] == "clean_port_ind_sym"
    assert manifest["geometry"]["config"]["outer_diameter_um"] == 100.0


def test_ind_sym_nt1_is_one_direct_n28_ring_without_bridge_or_vias(tmp_path):
    """A one-turn inductor has no adjacent turn to bridge to.

    Exercise the real registered plugin/output seam in N28 process mode: the
    complete P1-to-N1 conductor must be one connected body-metal region, with
    neither a lower-metal bridge nor any via cuts.  The product DRC recipe
    must describe that same physical topology and pass the core-rule audit.
    """
    from ic_opt.em.pcell.drc_audit import (
        audit_gds,
        product_scope_record,
        require_layers_from_config,
    )

    gp = _load_plugin()
    payload = {
        **_ind_sym_config_dict(),
        "turns": 1,
        "metal": "AP",
    }
    cfg = gp.CleanPortIndSymConfig.model_validate(payload)
    result = gp.CleanPortIndSymGenerator().generate(
        cfg, outdir=tmp_path, gds_name="ind_sym_nt1_direct.gds")

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["via_landing_audit"] == {
        "status": "pass",
        "vias_checked": 0,
    }

    p = gp._clean_port()
    ctx = p.process_rule_context("n28_1p10m")
    _, regions = _drawing_layer_regions(result.gds_path)
    body_ld = tuple(ctx.adapter.layer("AP").drawing)
    lower_ld = tuple(ctx.adapter.layer("M10").drawing)
    via_lds = {
        tuple(via.drawing)
        for via in ctx.adapter.profile.layer_catalog.vias.values()
    }
    assert body_ld in regions
    assert regions[body_ld].count() == 1
    assert lower_ld not in regions
    assert regions.keys().isdisjoint(via_lds)

    expected = require_layers_from_config(
        "clean_port_ind_sym", cfg.model_dump(mode="json"))
    assert expected == ["AP"]
    report = audit_gds(result.gds_path, profile_id="n28_1p10m")
    product = product_scope_record(
        report, expected_conductors=expected, ignore_layers=("M1",))
    assert product["outcome"] == "pass"


def test_ind_sym_small_od_wide_single_turn_has_full_width_pin_landing(tmp_path):
    """A 50 um / 10 um single-turn coil must keep a full-width pin joint.

    The registered N28 generator is the product seam: the final GDS must keep
    the requested OD/W and expose a full-width pin landing without inventing a
    bridge for a one-turn winding or relaxing the product DRC recipe.
    """
    import klayout.db as kdb

    from ic_opt.em.pcell.drc_audit import (
        audit_gds,
        product_scope_record,
        require_layers_from_config,
    )

    gp = _load_plugin()
    payload = {
        **_ind_sym_config_dict(),
        "outer_diameter_um": 50.0,
        "width_um": 10.0,
        "spacing_um": 2.1,
        "turns": 1,
        "opening_um": 5.6,
        "metal": "10",
    }
    cfg = gp.CleanPortIndSymConfig.model_validate(payload)
    result = gp.CleanPortIndSymGenerator().generate(
        cfg, outdir=tmp_path, gds_name="ind_sym_small_wide.gds"
    )

    expected = require_layers_from_config(
        "clean_port_ind_sym", cfg.model_dump(mode="json")
    )
    assert expected == ["M10"]
    product = product_scope_record(
        audit_gds(result.gds_path, "n28_1p10m"),
        expected_conductors=expected,
        ignore_layers=("M1",),
    )
    assert product["outcome"] == "pass"

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["via_landing_audit"]["status"] == "pass"
    assert manifest["via_landing_audit"]["vias_checked"] == 0

    p = gp._clean_port()
    ctx = p.process_rule_context("n28_1p10m")
    _, regions = _drawing_layer_regions(result.gds_path)
    body = regions[p._metal(10, ctx)]
    assert body.count() == 1

    landing = kdb.Region(kdb.Box(
        p._nm(25.0 - 10.0), p._nm(5.6),
        p._nm(25.0), p._nm(5.6 + 10.0),
    ))
    assert (landing - body).is_empty()


def test_ind_sym_compact_two_turn_uses_reference_bridge_scheme(tmp_path):
    """The first feasible 10 um-wide NT=2 boundary under the REFERENCE
    scheme (design-region issue 03): leg2 crosses OVER on the coil's own
    layer, leg1 dives one level; M8 (top-2) is never drawn."""
    import klayout.db as kdb

    from ic_opt.em.pcell.drc_audit import (
        audit_gds,
        product_scope_record,
        require_layers_from_config,
    )

    gp = _load_plugin()
    payload = {
        **_ind_sym_config_dict(),
        "outer_diameter_um": 81.0,
        "width_um": 10.0,
        "spacing_um": 2.1,
        "turns": 2,
        "opening_um": 5.6,
        "metal": "10",
    }
    cfg = gp.CleanPortIndSymConfig.model_validate(payload)
    result = gp.CleanPortIndSymGenerator().generate(
        cfg, outdir=tmp_path, gds_name="ind_sym_compact_nt2.gds"
    )

    expected = require_layers_from_config(
        "clean_port_ind_sym", cfg.model_dump(mode="json")
    )
    assert expected == ["M10", "M9"]
    product = product_scope_record(
        audit_gds(result.gds_path, "n28_1p10m"),
        expected_conductors=expected,
        ignore_layers=("M1",),
    )
    assert product["outcome"] == "pass"

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["via_landing_audit"]["status"] == "pass"
    # reference scheme: only leg1 carries a via stack (leg2 is a direct
    # top-layer crossing), so ONE via class is audited (was 2).
    assert manifest["via_landing_audit"]["vias_checked"] == 1

    p = gp._clean_port()
    ctx = p.process_rule_context("n28_1p10m")
    _, regions = _drawing_layer_regions(result.gds_path)
    assert p._metal(8, ctx) not in regions
    stack = [regions[p._metal(met, ctx)] for met in (10, 9)]
    assert [region.count() for region in stack] == [2, 1]
    connected = kdb.Region()
    for region in stack:
        connected += region
    assert connected.merged().count() == 1


@pytest.mark.parametrize(
    ("outer_diameter_um", "width_um", "spacing_um", "expected_pad_um"),
    [
        (143.4, 6.33, 3.2, 6.33),
        # (81, 10, 2.1): the full W x W landing merges the inner turn into
        # a closed (shorted) ring; since 772ce04 rejects such candidates the
        # largest qualified landing was 5.75, and since the pads must also sit
        # entirely on their ring's flat (D4, 2026-09-22) it is 4.0.
        (81.0, 10.0, 2.1, 4.0),
    ],
)
def test_ind_sym_compact_two_turn_uses_largest_drc_clean_via_landing(
    outer_diameter_um,
    width_um,
    spacing_um,
    expected_pad_um,
):
    """Use all legal straight landing before compact geometry forces shrinkage."""
    gp = _load_plugin()
    p = gp._clean_port()
    cell = p.ind_sym(
        OD=outer_diameter_um,
        W=width_um,
        OPENING=8.0 if outer_diameter_um > 100.0 else 5.6,
        LEAD=20.0,
        S=spacing_um,
        NT=2,
        TOP_ME="10",
        BTM_ME="9",
        process=p.process_rule_context("n28_1p10m"),
    )

    assert cell.params["compact_bridge_pad_length_um"] == pytest.approx(
        expected_pad_um
    )


def test_ind_sym_infeasible_compact_two_turn_fails_by_family_name(tmp_path):
    """A 50/10 NT=2 coil stays rejected without leaking xfm_ms wording."""
    gp = _load_plugin()
    payload = {
        **_ind_sym_config_dict(),
        "outer_diameter_um": 50.0,
        "width_um": 10.0,
        "spacing_um": 2.1,
        "turns": 2,
        "opening_um": 5.6,
        "metal": "10",
    }
    cfg = gp.CleanPortIndSymConfig.model_validate(payload)
    with pytest.raises(
        ValueError,
        match=r"ind_sym: no DRC-clean compact two-turn bridge fits OD=50\.000",
    ):
        gp.CleanPortIndSymGenerator().generate(
            cfg, outdir=tmp_path, gds_name="ind_sym_infeasible.gds"
        )


def test_compact_multiturn_inductor_rejects_body_self_short(tmp_path):
    """A radially impossible compact corner must fail on topology before its
    degenerate polygons can reach fixture construction or same-net DRC."""
    gp = _load_plugin()
    payload = {
        **_ind_sym_config_dict(),
        "outer_diameter_um": 50.0,
        "width_um": 8.0,
        "spacing_um": 2.1,
        "turns": 3,
        "opening_um": 6.3,
        "metal": "10",
    }
    cfg = gp.CleanPortIndSymConfig.model_validate(payload)
    # The inner-ring fit guard (_check_winding_fit, wired into ind_sym in
    # the gdsfactory review 2026-09-21) refuses this radially impossible
    # corner FIRST, before the corridor trim or the topology count would
    # -- still fail-closed, message actionable.
    with pytest.raises(ValueError,
                       match="cannot host the crossunder facing"):
        gp.CleanPortIndSymGenerator().generate(
            cfg, outdir=tmp_path, gds_name="ind_sym_compact_3t.gds"
        )


def test_ind_sym_generator_rejects_bogus_process_profile(tmp_path):
    gp = _load_plugin()
    cfg = gp.CleanPortIndSymConfig.model_validate(
        {**_ind_sym_config_dict(), "process_profile": "no_such_profile"})
    with pytest.raises(ValueError, match="unsupported process rule profile"):
        gp.CleanPortIndSymGenerator().generate(
            cfg, outdir=tmp_path, gds_name="x.gds")


def test_ind_sym_generator_rejects_path_traversal_gds_name(tmp_path):
    gp = _load_plugin()
    cfg = gp.CleanPortIndSymConfig.model_validate(_ind_sym_config_dict())
    with pytest.raises(ValueError, match="gds_name"):
        gp.CleanPortIndSymGenerator().generate(
            cfg, outdir=tmp_path, gds_name="../evil.gds")


def test_port_order_rejects_names_outside_safe_charset():
    gp = _load_plugin()
    payload = _ind_sym_config_dict()
    payload["port_order"] = ["p:01", "p02"]
    with pytest.raises(ValidationError):
        gp.CleanPortIndSymConfig.model_validate(payload)


def test_ind_sym_ct_metal_generator_parity_with_direct_module_call(tmp_path):
    """The unified generator (ct_metal set) matches a direct
    ind_sym(CT_ME=...) build layer for layer (M13 ticket 03)."""
    gp = _load_plugin()
    p = gp._clean_port()

    cfg = gp.CleanPortIndSymConfig.model_validate(
        {**_ind_sym_config_dict(), "port_order": ["P1", "N1", "CT"],
         "metal": "10", "ct_metal": "8"})
    gen = gp.CleanPortIndSymGenerator()
    result = gen.generate(cfg, outdir=tmp_path / "plugin", gds_name="ct_cand.gds")
    assert result.generator_id == "clean_port_ind_sym"
    assert result.top_cell == "ct_cand"
    assert result.gds_path.name == "ct_cand.gds"

    ctx = p.process_rule_context("n28_1p10m")
    fx = p.GroundFixtureConfig(inner_margin_um=15.0, ring_width_um=50.0,
                               stub_width_um=5.0, stub_length_um=2.0,
                               stub_chamfer_um=0.0)
    cell = p.ind_sym(OD=100.0, W=5.0, OPENING=8.0, LEAD=20.0, S=2.0, NT=2,
                     TOP_ME="10", BTM_ME="8", CT_ME="8",
                     port_order=["P1", "N1", "CT"],
                     ground_fixture=fx, process=ctx)
    cell.name = "ct_cand"
    direct_gds = tmp_path / "direct" / "ct_cand.gds"
    direct_gds.parent.mkdir(parents=True)
    p.write_gds(cell, direct_gds)

    _, a = _drawing_layer_regions(result.gds_path)
    _, b = _drawing_layer_regions(direct_gds)
    assert set(a) == set(b)
    for ld in a:
        assert (a[ld] ^ b[ld]).is_empty(), f"layer {ld} differs"

    lines = result.emx_ports_path.read_text().strip().splitlines()
    assert lines == p.emx_port_lines(cell.emx_ports)
    assert len(lines) == 3 and lines[0] == "-p CT=CT:G03"

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["generator_id"] == "clean_port_ind_sym"
    assert manifest["geometry"]["config"]["port_order"] == ["P1", "N1", "CT"]
    assert manifest["geometry"]["config"]["ct_metal"] == "8"


def _make_layout_with(shapes):
    """shapes: dict[(layer,datatype)] -> list of (l,b,r,t) boxes in nm."""
    import klayout.db as kdb
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("SYNTH")
    for ld, boxes in shapes.items():
        li = ly.layer(*ld)
        for left, bottom, right, t in boxes:
            top.shapes(li).insert(kdb.Box(left, bottom, right, t))
    return ly


def test_via_audit_rejects_floating_cut(tmp_path):
    gp = _load_plugin()
    # VIA9 (59,80) must land on M9 (39,80) AND M10 (40,80). One cut lacks M9 under it.
    ly = _make_layout_with({
        (40, 80): [(0, 0, 10000, 10000)],          # M10 plate
        (39, 80): [(0, 0, 5000, 10000)],           # M9 covers left half only
        (59, 80): [(1000, 1000, 2000, 2000),        # good cut (inside both)
                   (7000, 1000, 8000, 2000)],       # floating cut (no M9 under it)
    })
    gds = tmp_path / "bad.gds"
    ly.write(str(gds))
    with pytest.raises(ValueError) as err:
        gp.audit_via_landing(gds, "n28_1p10m")
    msg = str(err.value)
    assert "VIA9" in msg and "landing" in msg


def test_via_audit_passes_contained_cuts(tmp_path):
    gp = _load_plugin()
    ly = _make_layout_with({
        (40, 80): [(0, 0, 10000, 10000)],
        (39, 80): [(0, 0, 10000, 10000)],
        (59, 80): [(1000, 1000, 2000, 2000)],
    })
    gds = tmp_path / "good.gds"
    ly.write(str(gds))
    audit = gp.audit_via_landing(gds, "n28_1p10m")
    assert audit["status"] == "pass"
    assert audit["vias_checked"] >= 1


def test_via_audit_rejects_multiple_top_cells(tmp_path):
    import klayout.db as kdb
    gp = _load_plugin()
    ly = kdb.Layout()
    ly.dbu = 0.001
    # CLEAN top first in stream order: a stream-order-dependent audit would
    # pick it and fail open, ignoring the BAD top's floating cut.
    clean = ly.create_cell("CLEAN")
    bad = ly.create_cell("BAD")
    for cell, boxes in (
        (clean, {(40, 80): (0, 0, 10000, 10000),
                 (39, 80): (0, 0, 10000, 10000),
                 (59, 80): (1000, 1000, 2000, 2000)}),
        (bad, {(40, 80): (0, 0, 10000, 10000),
               (59, 80): (7000, 1000, 8000, 2000)}),  # no M9 anywhere
    ):
        for ld, (left, bottom, right, t) in boxes.items():
            cell.shapes(ly.layer(*ld)).insert(kdb.Box(left, bottom, right, t))
    gds = tmp_path / "two_tops.gds"
    ly.write(str(gds))
    with pytest.raises(ValueError, match="exactly one top cell"):
        gp.audit_via_landing(gds, "n28_1p10m")


def test_via_audit_rejects_empty_gds(tmp_path):
    import klayout.db as kdb
    gp = _load_plugin()
    ly = kdb.Layout()
    ly.dbu = 0.001
    gds = tmp_path / "empty.gds"
    ly.write(str(gds))
    with pytest.raises(ValueError, match="exactly one top cell"):
        gp.audit_via_landing(gds, "n28_1p10m")


def test_generated_devices_pass_via_audit(tmp_path):
    gp = _load_plugin()
    cfg = gp.CleanPortIndSymConfig.model_validate(
        {**_ind_sym_config_dict(), "port_order": ["P1", "N1", "CT"],
         "metal": "10", "ct_metal": "8"})
    result = gp.CleanPortIndSymGenerator().generate(
        cfg, outdir=tmp_path, gds_name="ct.gds")
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["via_landing_audit"]["status"] == "pass"
    assert manifest["via_landing_audit"]["vias_checked"] >= 1


def _xfm_bs_config_dict():
    return {
        "process_profile": "n28_1p10m",
        "port_order": ["P1", "N1", "P2", "N2"],
        "primary_outer_diameter_um": 100.0,
        "secondary_outer_diameter_um": 100.0,
        "primary_width_um": 5.0,
        "secondary_width_um": 5.0,
        "primary_opening_um": 8.0,
        "secondary_opening_um": 8.0,
        "primary_lead_length_um": 20.0,
        "secondary_lead_length_um": 20.0,
        "center_spacing_um": 0.0,
        "primary_metal": "10",
        "secondary_metal": "9",
        "ground_fixture": _fixture_dict(),
    }


@pytest.mark.parametrize("field", ["primary_metal", "secondary_metal"])
def test_xfm_bs_config_rejects_m1_metal(field):
    gp = _load_plugin()
    payload = {**_xfm_bs_config_dict(), field: "1"}
    with pytest.raises(ValidationError, match="reserved for the ground fixture"):
        gp.CleanPortXfmBsConfig.model_validate(payload)


def test_xfm_bs_config_port_order_must_be_canonical():
    gp = _load_plugin()
    payload = _xfm_bs_config_dict()
    payload["port_order"] = ["p01", "p02", "p03", "p04"]
    with pytest.raises(ValidationError, match="P1"):
        gp.CleanPortXfmBsConfig.model_validate(payload)
    payload["port_order"] = ["N1", "N2", "P1", "P2"]  # right names, wrong order
    with pytest.raises(ValidationError):
        gp.CleanPortXfmBsConfig.model_validate(payload)


def test_xfm_bs_generator_parity_with_direct_module_call(tmp_path):
    import klayout.db as kdb

    gp = _load_plugin()
    p = gp._clean_port()

    cfg = gp.CleanPortXfmBsConfig.model_validate(_xfm_bs_config_dict())
    result = gp.CleanPortXfmBsGenerator().generate(
        cfg, outdir=tmp_path / "plugin", gds_name="bs.gds")
    assert result.generator_id == "clean_port_xfm_bs"
    assert result.top_cell == "bs"
    layout = kdb.Layout()
    layout.read(str(result.gds_path))
    assert [cell.name for cell in layout.top_cells()] == [result.top_cell]

    ctx = p.process_rule_context("n28_1p10m")
    fx = p.GroundFixtureConfig(inner_margin_um=15.0, ring_width_um=50.0,
                               stub_width_um=5.0, stub_length_um=2.0,
                               stub_chamfer_um=0.0)
    cell = p.xfm_bs(OD_P=100.0, OD_S=100.0, W_P=5.0, W_S=5.0,
                    OPENING_P=8.0, OPENING_S=8.0, LEAD_P=20.0, LEAD_S=20.0,
                    CENTER_SPACING=0.0, PRI_ME="10", SEC_ME="9",
                    ground_fixture=fx, process=ctx)
    cell.name = "bs"
    direct_gds = tmp_path / "direct" / "bs.gds"
    direct_gds.parent.mkdir(parents=True)
    p.write_gds(cell, direct_gds)

    _, a = _drawing_layer_regions(result.gds_path)
    _, b = _drawing_layer_regions(direct_gds)
    assert set(a) == set(b)
    for ld in a:
        assert (a[ld] ^ b[ld]).is_empty(), f"layer {ld} differs"

    lines = result.emx_ports_path.read_text().strip().splitlines()
    assert lines == p.emx_port_lines(cell.emx_ports)
    assert len(lines) == 4
    assert lines == ["-p N1=N1:G02", "-p N2=N2:G04", "-p P1=P1:G01", "-p P2=P2:G03"]

    # xfm_bs has NO via layers by design: audit checked 0 vias and that is OK
    # for this generator (requires_vias=False).
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["via_landing_audit"] == {"status": "pass", "vias_checked": 0}


def test_xfm_bs_small_od_wide_trace_has_full_width_pin_landing(tmp_path):
    """A 50 um / 10 um winding is feasible: each pin lead must overlap one
    full trace width into the coil instead of only touching its OD boundary."""
    import klayout.db as kdb

    from ic_opt.em.pcell.drc_audit import (
        audit_gds,
        product_scope_record,
        require_layers_from_config,
    )

    gp = _load_plugin()
    payload = {
        **_xfm_bs_config_dict(),
        "primary_outer_diameter_um": 80.0,
        "secondary_outer_diameter_um": 50.0,
        "primary_width_um": 8.0,
        "secondary_width_um": 10.0,
        "primary_opening_um": 8.0,
        "secondary_opening_um": 5.6,
        "center_spacing_um": 24.38,
        "primary_metal": "AP",
        "secondary_metal": "10",
    }
    cfg = gp.CleanPortXfmBsConfig.model_validate(payload)
    result = gp.CleanPortXfmBsGenerator().generate(
        cfg, outdir=tmp_path, gds_name="bs_small_wide.gds"
    )

    report = audit_gds(result.gds_path, "n28_1p10m")
    expected = require_layers_from_config(
        "clean_port_xfm_bs", cfg.model_dump(mode="json")
    )
    product = product_scope_record(
        report, expected_conductors=expected, ignore_layers=("M1",)
    )
    assert product["outcome"] == "pass"

    p = gp._clean_port()
    ctx = p.process_rule_context("n28_1p10m")
    _, regions = _drawing_layer_regions(result.gds_path)
    secondary = regions[p._metal(10, ctx)]
    center_x = payload["center_spacing_um"] / 2.0
    edge_x = center_x + payload["secondary_outer_diameter_um"] / 2.0
    width = payload["secondary_width_um"]
    opening = payload["secondary_opening_um"]
    for y0 in (opening, -opening - width):
        landing = kdb.Region(kdb.Box(
            p._nm(edge_x - width), p._nm(y0),
            p._nm(edge_x), p._nm(y0 + width),
        ))
        assert (landing - secondary).is_empty()


def test_xfm_bs_rejects_ct_pad_touching_secondary_leads(tmp_path):
    """Zero-area edge contact is a short even when merged-layer DRC passes."""
    gp = _load_plugin()
    cfg = gp.CleanPortXfmBsConfig.model_validate({
        **_xfm_bs_config_dict(),
        "primary_outer_diameter_um": 120.0,
        "secondary_outer_diameter_um": 100.0,
        "primary_width_um": 6.0,
        "secondary_width_um": 6.0,
        "secondary_opening_um": 3.0,
        "ct_primary_metal": "8",
        "port_order": ["P1", "N1", "P2", "N2", "CTP"],
    })
    # The M10->M8 tap stack includes an M9 pad at y=[-3,3].
    # Secondary leads start at y=+/-3, touching that pad along an edge.
    with pytest.raises(ValueError, match="primary/secondary nets short"):
        gp.CleanPortXfmBsGenerator().generate(
            cfg, outdir=tmp_path, gds_name="bs_ct_touch.gds"
        )


def test_xfm_bs_n65_ap_center_tap_uses_m9_rv_stack(tmp_path):
    from ic_opt.em.pcell.drc_audit import (
        audit_gds,
        product_scope_record,
        require_layers_from_config,
    )

    gp = _load_plugin()
    cfg = gp.CleanPortXfmBsConfig.model_validate({
        **_xfm_bs_config_dict(),
        "process_profile": "n65_1p9m",
        "primary_metal": "AP",
        "secondary_metal": "9",
        "primary_outer_diameter_um": 120.0,
        "primary_width_um": 6.0,
        "secondary_width_um": 6.0,
        "ct_primary_metal": "9",
        "port_order": ["P1", "N1", "P2", "N2", "CTP"],
    })
    result = gp.CleanPortXfmBsGenerator().generate(
        cfg, outdir=tmp_path, gds_name="bs_n65_ap_ct.gds"
    )
    expected = require_layers_from_config("clean_port_xfm_bs", cfg.model_dump())
    assert expected == ["AP", "M9"]
    ctx = gp._clean_port().process_rule_context(cfg.process_profile)
    _, regions = _drawing_layer_regions(result.gds_path)
    assert regions[tuple(ctx.adapter.profile.layer_catalog.vias["RV"].drawing)].count() > 0
    assert regions[tuple(ctx.adapter.layer("M9").drawing)].count() == 2
    record = product_scope_record(
        audit_gds(result.gds_path, cfg.process_profile), expected,
        ignore_findings=frozenset({("max_width", "M1")}),
    )
    assert record["outcome"] == "pass", record


def _xfm_ms_config_dict():
    return {
        "process_profile": "n28_1p10m",
        "port_order": ["P1", "N1", "P2", "N2"],
        "primary_outer_diameter_um": 100.0,
        "secondary_outer_diameter_um": 76.0,
        "primary_width_um": 6.0,
        "secondary_width_um": 3.0,
        "primary_opening_um": 8.0,
        "secondary_opening_um": 6.0,
        "primary_lead_length_um": 20.0,
        "secondary_lead_length_um": 15.0,
        "secondary_turns": 3,
        "secondary_spacing_um": 2.0,
        "center_spacing_um": 0.0,
        "primary_metal": "10",
        "secondary_metal": "9",
        "ground_fixture": {**_fixture_dict(), "stub_width_um": 6.0,
                            "stub_width_by_port_um": {"P2": 3.0, "N2": 3.0}},
    }


def test_xfm_ms_config_rejects_m1_metal():
    gp = _load_plugin()
    with pytest.raises(ValidationError, match="reserved for the ground fixture"):
        gp.CleanPortXfmMsConfig.model_validate(
            {**_xfm_ms_config_dict(), "primary_metal": "1"})
    with pytest.raises(ValidationError, match="reserved for the ground fixture"):
        gp.CleanPortXfmMsConfig.model_validate(
            {**_xfm_ms_config_dict(), "secondary_metal": "1"})


@pytest.mark.parametrize("multi_metal", ["2", "M2"])
def test_xfm_ms_config_rejects_low_multi_metal_implicit_bridge_on_m1(
    multi_metal,
):
    # The one crossunder occupies multi-1: M2 would place it on the
    # fixture-reserved M1 plane. M3 instead uses M2 and is supported.
    gp = _load_plugin()
    with pytest.raises(ValidationError, match="reserved for the ground fixture"):
        gp.CleanPortXfmMsConfig.model_validate(
            {**_xfm_ms_config_dict(), "secondary_metal": multi_metal})


@pytest.mark.parametrize(
    ("width", "multi_od", "center_spacing", "single_metal", "multi_metal", "reject"),
    [
        (8.0, 65.0, 12.69, "10", "9", True),
        (9.0, 75.0, 19.38, "AP", "10", False),
        (10.0, 80.0, 30.0, "10", "9", True),
    ],
)
def test_xfm_ms_compact_multi_turn_uses_reference_bridge_scheme(
    tmp_path, width, multi_od, center_spacing, single_metal, multi_metal, reject
):
    """A compact two-turn winding must remain a series path under the
    REFERENCE bridge scheme (design-region issue 03, 2026-07-31): leg2
    crosses OVER on the coil's own layer, leg1 dives exactly one level --
    multi_metal-2 is never drawn."""
    import klayout.db as kdb

    from ic_opt.em.pcell._pcell_core import PortError
    from ic_opt.em.pcell.drc_audit import (
        audit_gds,
        product_scope_record,
        require_layers_from_config,
    )

    gp = _load_plugin()
    payload = {
        **_xfm_ms_config_dict(),
        "primary_outer_diameter_um": 80.0,
        "secondary_outer_diameter_um": multi_od,
        "primary_width_um": width,
        "secondary_width_um": width,
        "primary_opening_um": 8.0,
        "secondary_opening_um": 8.0,
        "secondary_turns": 2,
        "secondary_spacing_um": 2.1,
        "center_spacing_um": center_spacing,
        "primary_metal": single_metal,
        "secondary_metal": multi_metal,
    }
    cfg = gp.CleanPortXfmMsConfig.model_validate(payload)
    if reject:
        # These two tight M9 cases previously passed only because the
        # inner ring was shorted within one polygon (component count stayed 2).
        with pytest.raises(PortError, match="no DRC-clean compact two-turn bridge"):
            gp.CleanPortXfmMsGenerator().generate(
                cfg, outdir=tmp_path, gds_name="xfm_ms_compact.gds")
        return
    result = gp.CleanPortXfmMsGenerator().generate(
        cfg, outdir=tmp_path, gds_name="xfm_ms_compact.gds"
    )

    expected = require_layers_from_config(
        "clean_port_xfm_ms", cfg.model_dump(mode="json")
    )
    assert expected[-2:] == [
        f"M{int(multi_metal) - offset}" for offset in (0, 1)
    ]
    product = product_scope_record(
        audit_gds(result.gds_path, "n28_1p10m"),
        expected_conductors=expected,
        ignore_layers=("M1",),
    )
    assert product["outcome"] == "pass"

    p = gp._clean_port()
    ctx = p.process_rule_context("n28_1p10m")
    _, regions = _drawing_layer_regions(result.gds_path)
    multi_index = int(multi_metal)
    assert p._metal(multi_index - 2, ctx) not in regions
    bridge_regions = [regions[p._metal(multi_index - i, ctx)] for i in range(2)]
    # top: leg2 joins one outer arc to the inner ring + the other arc = 2;
    # multi-1: leg1 with merged endpoint pads = 1.
    assert [region.count() for region in bridge_regions] == [2, 1]
    assert all(polygon.holes() == 0 for region in bridge_regions for polygon in region.each())
    connected = kdb.Region()
    for region in bridge_regions:
        connected += region
    assert connected.merged().count() == 1


def test_xfm_ms_generator_parity_with_direct_module_call(tmp_path):
    gp = _load_plugin()
    p = gp._clean_port()

    cfg = gp.CleanPortXfmMsConfig.model_validate(_xfm_ms_config_dict())
    result = gp.CleanPortXfmMsGenerator().generate(
        cfg, outdir=tmp_path / "plugin", gds_name="ms.gds")
    assert result.generator_id == "clean_port_xfm_ms"
    assert result.top_cell == "ms"

    ctx = p.process_rule_context("n28_1p10m")
    fx = p.GroundFixtureConfig(inner_margin_um=15.0, ring_width_um=50.0,
                               stub_width_um=6.0, stub_length_um=2.0,
                               stub_chamfer_um=0.0,
                               stub_width_by_port_um={"P2": 3.0, "N2": 3.0})
    cell = p.xfm_ms(OD_S=100.0, OD_M=76.0, W_S=6.0, W_M=3.0,
                    OPENING_S=8.0, OPENING_M=6.0, LEAD_S=20.0, LEAD_M=15.0,
                    NT_M=3, S_M=2.0, CENTER_SPACING=0.0,
                    SINGLE_ME="10", MULTI_ME="9",
                    ground_fixture=fx, process=ctx)
    cell.name = "ms"
    direct_gds = tmp_path / "direct" / "ms.gds"
    direct_gds.parent.mkdir(parents=True)
    p.write_gds(cell, direct_gds)

    _, a = _drawing_layer_regions(result.gds_path)
    _, b = _drawing_layer_regions(direct_gds)
    assert set(a) == set(b)
    for ld in a:
        assert (a[ld] ^ b[ld]).is_empty(), f"layer {ld} differs"

    lines = result.emx_ports_path.read_text().strip().splitlines()
    assert lines == p.emx_port_lines(cell.emx_ports)
    assert len(lines) == 4

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["via_landing_audit"]["status"] == "pass"
    assert manifest["via_landing_audit"]["vias_checked"] >= 1   # ms HAS vias
    assert manifest["geometry"]["config"]["ground_fixture"][
        "stub_width_by_port_um"] == {"P2": 3.0, "N2": 3.0}


def test_unequal_od_xfm_ground_ring_clears_full_winding_body(tmp_path):
    """A smaller right-side winding must not pull the M1 inner edge across
    the larger left-side winding's closed column (BS/MS gallery regression)."""
    gp = _load_plugin()
    p = gp._clean_port()
    ctx = p.process_rule_context("n28_1p10m")
    m1 = p.process_metal_layer(ctx, 1)
    margin = 15.0
    ring_width = 50.0

    cases = [
        (
            gp.CleanPortXfmBsGenerator(),
            gp.CleanPortXfmBsConfig,
            {
                **_xfm_bs_config_dict(),
                "primary_outer_diameter_um": 172.6,
                "secondary_outer_diameter_um": 83.9,
                "primary_width_um": 4.43,
                "secondary_width_um": 4.87,
                "center_spacing_um": 3.9,
            },
            -3.9 / 2.0 + 172.6 / 2.0,
        ),
        (
            gp.CleanPortXfmMsGenerator(),
            gp.CleanPortXfmMsConfig,
            {
                **_xfm_ms_config_dict(),
                "primary_outer_diameter_um": 202.6,
                "secondary_outer_diameter_um": 96.0,
                "primary_width_um": 5.0,
                "secondary_width_um": 3.32,
                "secondary_turns": 4,
                "center_spacing_um": 5.32,
            },
            -5.32 / 2.0 + 202.6 / 2.0,
        ),
    ]

    for index, (generator, model, payload, body_right_um) in enumerate(cases):
        result = generator.generate(
            model.model_validate(payload),
            outdir=tmp_path / str(index),
            gds_name=f"unequal_{index}.gds",
        )
        layout, regions = _drawing_layer_regions(result.gds_path)
        inner_right_um = (
            regions[m1].bbox().right * layout.dbu - ring_width
        )
        assert inner_right_um >= body_right_um + margin - 0.01


def _xfm_balun_config_dict():
    return {
        "process_profile": "n28_1p10m",
        "port_order": ["P1", "N1", "P2", "N2"],
        "primary_outer_diameter_um": 200.0,
        "secondary_outer_diameter_um": 186.0,
        "primary_width_um": 5.0,
        "secondary_width_um": 5.0,
        "spacing_um": 2.0,
        "primary_opening_um": 8.0,
        "secondary_opening_um": 8.0,
        "primary_lead_length_um": 20.0,
        "secondary_lead_length_um": 20.0,
        "primary_turns": 1,
        "secondary_turns": 1,
        "center_spacing_um": 0.0,
        "metal": "9",
        "ground_fixture": _fixture_dict(),
    }


def test_xfm_balun_config_rejects_m1_and_m2_balun_metal():
    # Same implicit-crossunder-one-level-below concern as xfm_ms's
    # multi_metal (_xfm_balun_recipe: balun_metal_index - 1): M2 would put
    # the crossunder on M1 even though M1 was never named directly.
    gp = _load_plugin()
    for bad_metal in ("1", "2"):
        with pytest.raises(ValidationError, match="reserved for the ground fixture"):
            gp.CleanPortXfmBalunConfig.model_validate(
                {**_xfm_balun_config_dict(), "metal": bad_metal})


def test_xfm_balun_generator_parity_with_direct_module_call(tmp_path):
    gp = _load_plugin()
    p = gp._clean_port()

    cfg = gp.CleanPortXfmBalunConfig.model_validate(_xfm_balun_config_dict())
    result = gp.CleanPortXfmBalunGenerator().generate(
        cfg, outdir=tmp_path / "plugin", gds_name="balun.gds")
    assert result.generator_id == "clean_port_xfm_balun"
    assert result.top_cell == "balun"

    ctx = p.process_rule_context("n28_1p10m")
    fx = p.GroundFixtureConfig(inner_margin_um=15.0, ring_width_um=50.0,
                               stub_width_um=5.0, stub_length_um=2.0,
                               stub_chamfer_um=0.0)
    cell = p.xfm_balun(OD_P=200.0, OD_S=186.0, W_P=5.0, W_S=5.0, S=2.0,
                       OPENING_P=8.0, OPENING_S=8.0, LEAD_P=20.0, LEAD_S=20.0,
                       NT_P=1, NT_S=1, CENTER_SPACING=0.0, BALUN_ME="9",
                       ground_fixture=fx, process=ctx)
    cell.name = "balun"
    direct_gds = tmp_path / "direct" / "balun.gds"
    direct_gds.parent.mkdir(parents=True)
    p.write_gds(cell, direct_gds)

    _, a = _drawing_layer_regions(result.gds_path)
    _, b = _drawing_layer_regions(direct_gds)
    assert set(a) == set(b)
    for ld in a:
        assert (a[ld] ^ b[ld]).is_empty(), f"layer {ld} differs"

    lines = result.emx_ports_path.read_text().strip().splitlines()
    assert lines == p.emx_port_lines(cell.emx_ports)
    assert len(lines) == 4

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["via_landing_audit"]["status"] == "pass"
    assert manifest["via_landing_audit"]["vias_checked"] >= 1


def test_xfm_balun_wide_nt2_primary_uses_reference_bridge_scheme(tmp_path):
    """A feasible nested W=10 balun inherits the compact three-layer coil.

    OD_S=100 (was 70): since M1.6 the secondary escape's arm-tip pad must sit
    on the secondary's flat (OPENING_S + W_S <= BA), which a 70 um ring
    cannot offer a 10 um pad at a 10 um opening."""
    from ic_opt.em.pcell.drc_audit import (
        audit_gds,
        product_scope_record,
        require_layers_from_config,
    )

    gp = _load_plugin()
    payload = {
        **_xfm_balun_config_dict(),
        "primary_outer_diameter_um": 150.0,
        "secondary_outer_diameter_um": 100.0,
        "primary_width_um": 10.0,
        "secondary_width_um": 10.0,
        "spacing_um": 2.1,
        "primary_opening_um": 5.6,
        "secondary_opening_um": 10.0,
        "primary_turns": 2,
        "secondary_turns": 1,
        "center_spacing_um": 0.0,
        "metal": "10",
    }
    cfg = gp.CleanPortXfmBalunConfig.model_validate(payload)
    result = gp.CleanPortXfmBalunGenerator().generate(
        cfg, outdir=tmp_path, gds_name="xfm_balun_wide_nt2.gds"
    )

    expected = require_layers_from_config(
        "clean_port_xfm_balun", cfg.model_dump(mode="json")
    )
    # reference bridge scheme (design-region issue 03): the compact NT=2
    # primary uses balun_metal + balun_metal-1 only; M8 is never drawn.
    assert expected == ["M10", "M9"]
    product = product_scope_record(
        audit_gds(result.gds_path, "n28_1p10m"),
        expected_conductors=expected,
        ignore_layers=("M1",),
    )
    assert product["outcome"] == "pass", product
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["via_landing_audit"]["status"] == "pass"
    assert manifest["via_landing_audit"]["vias_checked"] >= 1


def test_requires_vias_true_generator_rejects_empty_audit(tmp_path, monkeypatch):
    gp = _load_plugin()
    monkeypatch.setattr(
        gp, "audit_via_landing",
        lambda *a, **k: {"status": "pass", "vias_checked": 0})
    cfg = gp.CleanPortIndSymConfig.model_validate(_ind_sym_config_dict())
    with pytest.raises(ValueError, match="requires_vias=True"):
        gp.CleanPortIndSymGenerator().generate(
            cfg, outdir=tmp_path, gds_name="ind.gds")


def test_requires_vias_false_generator_rejects_unexpected_vias(tmp_path, monkeypatch):
    gp = _load_plugin()
    monkeypatch.setattr(
        gp, "audit_via_landing",
        lambda *a, **k: {"status": "pass", "vias_checked": 2})
    cfg = gp.CleanPortXfmBsConfig.model_validate(_xfm_bs_config_dict())
    with pytest.raises(ValueError, match="requires_vias=False"):
        gp.CleanPortXfmBsGenerator().generate(
            cfg, outdir=tmp_path, gds_name="bs.gds")


def test_manifest_documents_snp_port_index_order_provenance(tmp_path):
    gp = _load_plugin()
    cfg = gp.CleanPortXfmMsConfig.model_validate(_xfm_ms_config_dict())
    result = gp.CleanPortXfmMsGenerator().generate(
        cfg, outdir=tmp_path, gds_name="ms.gds")
    manifest = json.loads(result.manifest_path.read_text())
    assert "emx.yaml" in manifest["suggested_emx_ports_note"]
    assert "lexicographically by EMX port name" in manifest["suggested_emx_ports_note"]
    assert "independently of the -p argument or ports list order" in manifest["suggested_emx_ports_note"]


def test_clean_port_loader_is_thread_safe():
    import concurrent.futures
    import sys as _sys

    gp = _load_plugin()
    _sys.modules.pop("clean_port_mod", None)  # force a fresh load race

    def probe():
        # generate_all sits at the very bottom of the module, so its
        # presence proves a fully-initialized (not half-executed) load.
        mod = gp._clean_port()
        return hasattr(mod, "generate_all")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: probe(), range(8)))
    assert all(results), f"half-initialized module observed: {results}"


# ---------------------------------------------------------------------------
# M13 ticket 05: xfm_bs / xfm_balun CT exposure at the plugin contract
# ---------------------------------------------------------------------------


def test_xfm_bs_ct_config_port_sets():
    """ct fields drive the exact port set: base [P1,N1,P2,N2] plus the
    enabled taps, CTP before CTS."""
    gp = _load_plugin()
    base = _xfm_bs_config_dict()
    with pytest.raises(ValidationError, match="port_order"):
        gp.CleanPortXfmBsConfig.model_validate(
            {**base, "port_order": ["P1", "N1", "P2", "N2", "CTP"]})
    with pytest.raises(ValidationError, match="port_order"):
        gp.CleanPortXfmBsConfig.model_validate(
            {**base, "ct_primary_metal": "8"})
    cfg = gp.CleanPortXfmBsConfig.model_validate(
        {**base, "ct_primary_metal": "8",
         "port_order": ["P1", "N1", "P2", "N2", "CTP"]})
    assert cfg.ct_primary_metal == "8"
    cfg2 = gp.CleanPortXfmBsConfig.model_validate(
        {**base, "ct_secondary_metal": "7",
         "port_order": ["P1", "N1", "P2", "N2", "CTS"]})
    assert cfg2.ct_secondary_metal == "7"
    both = gp.CleanPortXfmBsConfig.model_validate(
        {**base, "ct_primary_metal": "8", "ct_secondary_metal": "7",
         "port_order": ["P1", "N1", "P2", "N2", "CTP", "CTS"]})
    assert both.ct_primary_metal == "8" and both.ct_secondary_metal == "7"
    with pytest.raises(ValidationError, match="port_order"):
        gp.CleanPortXfmBsConfig.model_validate(
            {**base, "ct_primary_metal": "8", "ct_secondary_metal": "7",
             "port_order": ["P1", "N1", "P2", "N2", "CTS", "CTP"]})


def test_xfm_bs_ct_metal_validators():
    gp = _load_plugin()
    base = _xfm_bs_config_dict()
    with pytest.raises(ValidationError, match="below"):
        gp.CleanPortXfmBsConfig.model_validate(
            {**base, "ct_primary_metal": "10",
             "port_order": ["P1", "N1", "P2", "N2", "CTP"]})
    with pytest.raises(ValidationError, match="reserved for the ground fixture"):
        gp.CleanPortXfmBsConfig.model_validate(
            {**base, "ct_primary_metal": "M1",
             "port_order": ["P1", "N1", "P2", "N2", "CTP"]})


def test_xfm_balun_ct_config_port_sets_and_validators():
    gp = _load_plugin()
    base = _xfm_balun_config_dict()
    cfg = gp.CleanPortXfmBalunConfig.model_validate(
        {**base, "ct_primary_metal": "7", "ct_secondary_metal": "7",
         "port_order": ["P1", "N1", "P2", "N2", "CTP", "CTS"]})
    assert cfg.ct_primary_metal == "7"
    with pytest.raises(ValidationError, match="port_order"):
        gp.CleanPortXfmBalunConfig.model_validate(
            {**base, "ct_primary_metal": "7"})
    with pytest.raises(ValidationError, match="below"):
        gp.CleanPortXfmBalunConfig.model_validate(
            {**base, "ct_secondary_metal": "9",
             "port_order": ["P1", "N1", "P2", "N2", "CTS"]})


def test_xfm_bs_ct_generator_emits_tap_ports(tmp_path):
    """The unified bs generator draws the taps and accepts the tap vias
    (requires_vias is config-derived: a via-less device gains a via stack
    the moment a CT is enabled)."""
    gp = _load_plugin()
    cfg = gp.CleanPortXfmBsConfig.model_validate(
        {**_xfm_bs_config_dict(), "ct_primary_metal": "8",
         "ct_secondary_metal": "8",
         "port_order": ["P1", "N1", "P2", "N2", "CTP", "CTS"]})
    result = gp.CleanPortXfmBsGenerator().generate(
        cfg, outdir=tmp_path, gds_name="bs_ct.gds")
    lines = result.emx_ports_path.read_text().strip().splitlines()
    assert len(lines) == 6
    assert lines[0] == "-p CTP=CTP:G05"
    assert lines[1] == "-p CTS=CTS:G06"
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["geometry"]["config"]["ct_primary_metal"] == "8"
    assert manifest["via_landing_audit"]["vias_checked"] >= 1


# ---------------------------------------------------------------------------
# M13 ticket 06: xfm_ms dual-side CT at the plugin contract
# ---------------------------------------------------------------------------


def test_xfm_ms_ct_config_port_sets_and_validators():
    """P side (CTP) = the single-turn winding; S side (CTS) = the
    multi-turn winding, whose tap obeys the ind N1 adjacency rule (two
    levels below multi_metal)."""
    gp = _load_plugin()
    base = _xfm_ms_config_dict()
    cfg = gp.CleanPortXfmMsConfig.model_validate(
        {**base, "secondary_metal": "10", "ct_primary_metal": "9",
         "ct_secondary_metal": "8",
         "port_order": ["P1", "N1", "P2", "N2", "CTP", "CTS"]})
    assert cfg.ct_primary_metal == "9"
    with pytest.raises(ValidationError, match="port_order"):
        gp.CleanPortXfmMsConfig.model_validate(
            {**base, "secondary_metal": "10", "ct_secondary_metal": "8"})
    with pytest.raises(ValidationError, match="two levels below"):
        gp.CleanPortXfmMsConfig.model_validate(
            {**base, "ct_secondary_metal": "8",
             "port_order": ["P1", "N1", "P2", "N2", "CTS"]})
    with pytest.raises(ValidationError, match="below"):
        gp.CleanPortXfmMsConfig.model_validate(
            {**base, "ct_primary_metal": "AP",
             "port_order": ["P1", "N1", "P2", "N2", "CTP"]})
    with pytest.raises(ValidationError, match="reserved for the ground fixture"):
        gp.CleanPortXfmMsConfig.model_validate(
            {**base, "ct_primary_metal": "1",
             "port_order": ["P1", "N1", "P2", "N2", "CTP"]})


def test_xfm_ms_ct_generator_emits_tap_ports(tmp_path):
    gp = _load_plugin()
    # N28-modeled combo: single AP (tap AP->M10 rides the modeled RV
    # class), multi M10 (tap M10->M8 rides VIA9+VIA8)
    cfg = gp.CleanPortXfmMsConfig.model_validate(
        {**_xfm_ms_config_dict(), "primary_metal": "AP", "secondary_metal": "10",
         "ct_primary_metal": "10", "ct_secondary_metal": "8",
         "port_order": ["P1", "N1", "P2", "N2", "CTP", "CTS"]})
    result = gp.CleanPortXfmMsGenerator().generate(
        cfg, outdir=tmp_path, gds_name="ms_ct.gds")
    lines = result.emx_ports_path.read_text().strip().splitlines()
    assert len(lines) == 6
    assert lines[0] == "-p CTP=CTP:G05"
    assert lines[1] == "-p CTS=CTS:G06"
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["geometry"]["config"]["ct_secondary_metal"] == "8"
    # both tap stacks are genuinely drawn and audited: RV (AP->M10 CTP)
    # + VIA9/VIA8 (M10->M8 CTS + the multi crossover)
    assert manifest["via_landing_audit"]["vias_checked"] == 3


# ---------------------------------------------------------------------------
# M13 ticket 09: stub width defaults to each port's own lead width
# ---------------------------------------------------------------------------


def _without_stub_width(config: dict) -> dict:
    fixture = {k: v for k, v in config["ground_fixture"].items()
               if k != "stub_width_um"}
    return {**config, "ground_fixture": fixture}


def test_ground_fixture_stub_width_optional():
    """Omitting stub_width_um validates: the generator derives it per port."""
    gp = _load_plugin()
    cfg = gp.CleanPortIndSymConfig.model_validate(
        _without_stub_width(_ind_sym_config_dict()))
    assert cfg.ground_fixture.stub_width_um is None


def _auto_vs_explicit_cases():
    gp = _load_plugin()
    ind = {**_ind_sym_config_dict(), "width_um": 7.0,
           "metal": "10", "ct_metal": "8",
           "port_order": ["P1", "N1", "CT"]}
    ind_explicit = {"stub_width_um": 7.0}
    bs = {**_xfm_bs_config_dict(), "primary_width_um": 6.0,
          "secondary_width_um": 3.5, "ct_primary_metal": "8",
          "ct_secondary_metal": "8",
          "port_order": ["P1", "N1", "P2", "N2", "CTP", "CTS"]}
    bs_explicit = {"stub_width_um": 6.0,
                   "stub_width_by_port_um": {"P2": 3.5, "N2": 3.5, "CTS": 3.5}}
    ms = {**_xfm_ms_config_dict(), "primary_width_um": 6.0, "secondary_width_um": 3.0,
          "primary_metal": "AP", "secondary_metal": "10",
          "ct_primary_metal": "10", "ct_secondary_metal": "8",
          "port_order": ["P1", "N1", "P2", "N2", "CTP", "CTS"]}
    ms_explicit = {"stub_width_um": 6.0,
                   "stub_width_by_port_um": {"P2": 3.0, "N2": 3.0, "CTS": 3.0}}
    balun = {**_xfm_balun_config_dict(), "primary_width_um": 5.0,
             "secondary_width_um": 5.0, "ct_primary_metal": "8",
             "ct_secondary_metal": "8",
             "port_order": ["P1", "N1", "P2", "N2", "CTP", "CTS"]}
    balun_explicit = {"stub_width_um": 5.0}
    # xfm_tw has one shared width for every port (P and S occupy the same
    # rings, spec.md) -- even simpler than balun's case above, which only
    # needs a single stub_width_um because its primary/secondary widths
    # happen to be equal, not because the device enforces it.
    tw = {**_xfm_tw_config_dict(), "width_um": 5.0}
    tw_explicit = {"stub_width_um": 5.0}
    # xfm_il: like xfm_tw, P and S share ONE width_um -- and the CT tap
    # leads reuse that same shared W too (xfm_il's own docstring / ticket
    # 04 handoff: "_il_ct_tap_exact" draws every lead, tap included, at W).
    il = {**_xfm_il_config_dict(), "width_um": 6.0,
          "ct_primary_metal": "10",
          "port_order": ["P1", "N1", "P2", "N2", "CTP"]}
    il_explicit = {"stub_width_um": 6.0}
    return [
        ("ind", gp.CleanPortIndSymGenerator, gp.CleanPortIndSymConfig,
         ind, ind_explicit),
        ("bs", gp.CleanPortXfmBsGenerator, gp.CleanPortXfmBsConfig,
         bs, bs_explicit),
        ("ms", gp.CleanPortXfmMsGenerator, gp.CleanPortXfmMsConfig,
         ms, ms_explicit),
        ("balun", gp.CleanPortXfmBalunGenerator, gp.CleanPortXfmBalunConfig,
         balun, balun_explicit),
        ("tw", gp.CleanPortXfmTwGenerator, gp.CleanPortXfmTwConfig,
         tw, tw_explicit),
        ("il", gp.CleanPortXfmIlGenerator, gp.CleanPortXfmIlConfig,
         il, il_explicit),
    ]


@pytest.mark.parametrize("name", ["ind", "bs", "ms", "balun", "tw", "il"])
def test_auto_stub_width_equals_explicit_lead_widths(tmp_path, name):
    """Default (no stub_width_um) draws byte-identical GDS to a config that
    spells out each port's own lead width explicitly — for all four devices,
    taps included."""
    case = {c[0]: c for c in _auto_vs_explicit_cases()}[name]
    _, gen_cls, cfg_cls, base, explicit_fixture = case
    auto_cfg = cfg_cls.model_validate(_without_stub_width(base))
    explicit = {**base, "ground_fixture":
                {**{k: v for k, v in base["ground_fixture"].items()},
                 **explicit_fixture}}
    explicit_cfg = cfg_cls.model_validate(explicit)
    r_auto = gen_cls().generate(auto_cfg, outdir=tmp_path / "auto",
                                gds_name="dev.gds")
    r_exp = gen_cls().generate(explicit_cfg, outdir=tmp_path / "exp",
                               gds_name="dev.gds")
    assert r_auto.gds_path.read_bytes() == r_exp.gds_path.read_bytes()


def test_write_gds_bytes_deterministic_across_seconds(tmp_path):
    """write_gds must not embed wall-clock timestamps: the byte-equality
    assertions in this file otherwise flake whenever their two generate()
    calls straddle a second boundary (caught live in a loaded full-suite
    run 2026-07-18: same tree, [ms] case failed under load, passed alone;
    controlled repro showed byte diffs only at the GDS BGNLIB/BGNSTR
    date fields)."""
    import time

    gp = _load_plugin()
    p = gp._clean_port()
    cell = p.ind_sym(OD=90.0, W=3.0, S=2.0, NT=2, OPENING=10.0, LEAD=20.0,
                     TOP_ME="9")
    # same stem in two directories: write_gds names the top cell after the
    # file stem, so the byte comparison must hold the stem constant
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    p.write_gds(cell, tmp_path / "first" / "dev.gds")
    time.sleep(1.05)
    p.write_gds(cell, tmp_path / "second" / "dev.gds")
    assert ((tmp_path / "first" / "dev.gds").read_bytes()
            == (tmp_path / "second" / "dev.gds").read_bytes())


def test_auto_stub_width_differs_from_wrong_explicit(tmp_path):
    """The auto default genuinely tracks the lead: with W=7 windings it does
    NOT reproduce the old hard-coded 5.0 stub geometry."""
    gp = _load_plugin()
    base = {**_ind_sym_config_dict(), "width_um": 7.0}
    auto = gp.CleanPortIndSymConfig.model_validate(_without_stub_width(base))
    fixed = gp.CleanPortIndSymConfig.model_validate(
        {**base, "ground_fixture": {**base["ground_fixture"],
                                    "stub_width_um": 5.0}})
    r_auto = gp.CleanPortIndSymGenerator().generate(
        auto, outdir=tmp_path / "auto", gds_name="dev.gds")
    r_fixed = gp.CleanPortIndSymGenerator().generate(
        fixed, outdir=tmp_path / "fixed", gds_name="dev.gds")
    assert r_auto.gds_path.read_bytes() != r_fixed.gds_path.read_bytes()


def test_stub_width_by_port_overrides_auto(tmp_path):
    """A per-port override stacks on top of the auto default."""
    gp = _load_plugin()
    base = {**_ind_sym_config_dict(), "width_um": 7.0,
            "metal": "10", "ct_metal": "8",
            "port_order": ["P1", "N1", "CT"]}
    auto_over = gp.CleanPortIndSymConfig.model_validate(
        {**_without_stub_width(base),
         "ground_fixture": {**_without_stub_width(base)["ground_fixture"],
                            "stub_width_by_port_um": {"CT": 3.0}}})
    explicit = gp.CleanPortIndSymConfig.model_validate(
        {**base, "ground_fixture": {**base["ground_fixture"],
                                    "stub_width_um": 7.0,
                                    "stub_width_by_port_um": {"CT": 3.0}}})
    r_auto = gp.CleanPortIndSymGenerator().generate(
        auto_over, outdir=tmp_path / "auto", gds_name="dev.gds")
    r_exp = gp.CleanPortIndSymGenerator().generate(
        explicit, outdir=tmp_path / "exp", gds_name="dev.gds")
    assert r_auto.gds_path.read_bytes() == r_exp.gds_path.read_bytes()


# ---------------------------------------------------------------------------
# M13 ticket 10: center-spacing upper bound on the overlap-coupled xfms
# ---------------------------------------------------------------------------


def test_xfm_bs_center_spacing_bound():
    """bs: spacing above (OD_P+OD_S)/4 fails closed; at the bound it passes."""
    gp = _load_plugin()
    base = _xfm_bs_config_dict()  # OD_P=OD_S=100 -> bound 50.0
    ok = gp.CleanPortXfmBsConfig.model_validate(
        {**base, "center_spacing_um": 50.0})
    assert ok.center_spacing_um == 50.0
    with pytest.raises(ValidationError, match="center_spacing_um"):
        gp.CleanPortXfmBsConfig.model_validate(
            {**base, "center_spacing_um": 50.1})


def test_xfm_ms_center_spacing_bound():
    """ms: same rule against (single_od + multi_od)/4."""
    gp = _load_plugin()
    base = _xfm_ms_config_dict()  # OD_S=100, OD_M=76 -> bound 44.0
    ok = gp.CleanPortXfmMsConfig.model_validate(
        {**base, "center_spacing_um": 44.0})
    assert ok.center_spacing_um == 44.0
    with pytest.raises(ValidationError, match="center_spacing_um"):
        gp.CleanPortXfmMsConfig.model_validate(
            {**base, "center_spacing_um": 44.1})


def test_xfm_balun_center_spacing_must_keep_the_secondary_nested():
    """Coplanar rings that do not overlap are two inductors, not a balun:
    the former side-by-side mode is refused at the config layer too (user
    directive 2026-09-22)."""
    gp = _load_plugin()
    base = _xfm_balun_config_dict()  # OD_P=200, OD_S=186: nested up to |cs| < 7
    with pytest.raises(ValidationError, match="nest"):
        gp.CleanPortXfmBalunConfig.model_validate({**base, "center_spacing_um": 200.0})
    with pytest.raises(ValidationError, match="nest"):
        gp.CleanPortXfmBalunConfig.model_validate({**base, "center_spacing_um": 8.0})
    assert gp.CleanPortXfmBalunConfig.model_validate(
        {**base, "center_spacing_um": 6.0}).center_spacing_um == 6.0


# ---------------------------------------------------------------------------
# xfm_tw plugin contract (ticket 03 -- .scratch/xfm-tw-twisted/issues/
# 03-plugin-contract-recipe-argv.md). No CT: xfm_tw has no tap winding at
# all (spec.md "非目标"), so unlike xfm_bs/xfm_ms/xfm_balun there is no
# ct_primary_metal/ct_secondary_metal field to test here -- any such field
# name is just an unknown field, caught by extra="forbid" like any typo.
# ---------------------------------------------------------------------------


def _xfm_tw_config_dict():
    # AP body, NR=3: the same geometry independently pinned in
    # test_pcell_inductor_python_port_clean.py's _TW_N28_BODY_MATRIX
    # ("ap", "AP", 3, 260.0, 6.0, 6.0, 42 shapes) and in
    # test_tw_n28_ap_body_tight_dims_rejected_by_ring_pitch_guard's own
    # good-build baseline -- reused rather than picking fresh numbers.
    return {
        "process_profile": "n28_1p10m",
        "port_order": ["P1", "N1", "P2", "N2"],
        "outer_diameter_um": 260.0,
        "width_um": 6.0,
        "spacing_um": 6.0,
        "ring_count": 3,
        "port_gap_p_um": 10.0,
        "port_gap_n_um": 10.0,
        "lead_length_um": 20.0,
        "metal": "AP",
        "ground_fixture": _fixture_dict(),
    }


def test_plugin_generators_has_six_devices():
    gp = _load_plugin()
    assert set(gp.PLUGIN_GENERATORS) == {
        "clean_port_ind_sym", "clean_port_xfm_bs", "clean_port_xfm_ms",
        "clean_port_xfm_balun", "clean_port_xfm_tw", "clean_port_xfm_il",
    }
    assert (gp.PLUGIN_GENERATORS["clean_port_xfm_tw"].generator_id
            == "clean_port_xfm_tw")
    assert (gp.PLUGIN_GENERATORS["clean_port_xfm_tw"].config_model
            is gp.CleanPortXfmTwConfig)
    assert (gp.PLUGIN_GENERATORS["clean_port_xfm_il"].generator_id
            == "clean_port_xfm_il")
    assert (gp.PLUGIN_GENERATORS["clean_port_xfm_il"].config_model
            is gp.CleanPortXfmIlConfig)


def test_xfm_tw_config_happy_path():
    gp = _load_plugin()
    cfg = gp.CleanPortXfmTwConfig.model_validate(_xfm_tw_config_dict())
    assert cfg.outer_diameter_um == 260.0
    assert cfg.ring_count == 3
    assert cfg.port_order == ["P1", "N1", "P2", "N2"]


def test_xfm_tw_config_rejects_extra_fields():
    gp = _load_plugin()
    payload = _xfm_tw_config_dict()
    payload["bogus"] = 1
    with pytest.raises(ValidationError):
        gp.CleanPortXfmTwConfig.model_validate(payload)


@pytest.mark.parametrize(
    "bad_ct_field", ["ct_metal", "ct_primary_metal", "ct_secondary_metal"])
def test_xfm_tw_config_has_no_ct_field(bad_ct_field):
    """xfm_tw has no tap winding at all (spec.md "非目标"): every CT-shaped
    field name from the OTHER xfm configs is just an unknown field here,
    rejected by extra="forbid" exactly like any typo."""
    gp = _load_plugin()
    payload = {**_xfm_tw_config_dict(), bad_ct_field: "8"}
    with pytest.raises(ValidationError):
        gp.CleanPortXfmTwConfig.model_validate(payload)


@pytest.mark.parametrize("m1_spelling", ["1", "M1", "m1"])
def test_xfm_tw_config_rejects_m1_top_metal(m1_spelling):
    gp = _load_plugin()
    payload = {**_xfm_tw_config_dict(), "metal": m1_spelling}
    with pytest.raises(ValidationError, match="reserved for the ground fixture"):
        gp.CleanPortXfmTwConfig.model_validate(payload)


def test_xfm_tw_config_rejects_m2_top_metal_implicit_dive_on_m1():
    # The dive legs at every ring boundary land on top_metal-1 implicitly
    # (no separate config field for it -- same situation as xfm_ms's
    # multi_metal / xfm_balun's balun_metal): M2 would put that implicit
    # dive layer on M1 even though M1 was never named directly.
    gp = _load_plugin()
    with pytest.raises(ValidationError, match="reserved for the ground fixture"):
        gp.CleanPortXfmTwConfig.model_validate(
            {**_xfm_tw_config_dict(), "metal": "2"})


def test_xfm_tw_config_port_order_must_be_canonical():
    gp = _load_plugin()
    payload = _xfm_tw_config_dict()
    payload["port_order"] = ["p01", "p02", "p03", "p04"]
    with pytest.raises(ValidationError, match="P1"):
        gp.CleanPortXfmTwConfig.model_validate(payload)
    payload["port_order"] = ["N1", "N2", "P1", "P2"]  # right names, wrong order
    with pytest.raises(ValidationError):
        gp.CleanPortXfmTwConfig.model_validate(payload)


@pytest.mark.parametrize("bad_ring_count", [2, 4, 6, 0, -1])
def test_xfm_tw_config_rejects_non_odd_or_below_3_ring_count(bad_ring_count):
    gp = _load_plugin()
    with pytest.raises(ValidationError, match="odd integer >= 3"):
        gp.CleanPortXfmTwConfig.model_validate(
            {**_xfm_tw_config_dict(), "ring_count": bad_ring_count})


@pytest.mark.parametrize("good_ring_count", [3, 5, 7])
def test_xfm_tw_config_accepts_odd_ring_counts(good_ring_count):
    gp = _load_plugin()
    cfg = gp.CleanPortXfmTwConfig.model_validate(
        {**_xfm_tw_config_dict(), "ring_count": good_ring_count})
    assert cfg.ring_count == good_ring_count


def test_xfm_tw_generator_parity_with_direct_module_call(tmp_path):
    gp = _load_plugin()
    p = gp._clean_port()

    cfg = gp.CleanPortXfmTwConfig.model_validate(_xfm_tw_config_dict())
    result = gp.CleanPortXfmTwGenerator().generate(
        cfg, outdir=tmp_path / "plugin", gds_name="tw.gds")
    assert result.generator_id == "clean_port_xfm_tw"
    assert result.top_cell == "tw"

    ctx = p.process_rule_context("n28_1p10m")
    fx = p.GroundFixtureConfig(inner_margin_um=15.0, ring_width_um=50.0,
                               stub_width_um=5.0, stub_length_um=2.0,
                               stub_chamfer_um=0.0)
    cell = p.xfm_tw(OD=260.0, W=6.0, S=6.0, NR=3, OPENING_P=10.0,
                    OPENING_N=10.0, LEAD=20.0, SL_ME="AP",
                    ground_fixture=fx, process=ctx)
    cell.name = "tw"
    direct_gds = tmp_path / "direct" / "tw.gds"
    direct_gds.parent.mkdir(parents=True)
    p.write_gds(cell, direct_gds)

    _, a = _drawing_layer_regions(result.gds_path)
    _, b = _drawing_layer_regions(direct_gds)
    assert set(a) == set(b)
    for ld in a:
        assert (a[ld] ^ b[ld]).is_empty(), f"layer {ld} differs"

    lines = result.emx_ports_path.read_text().strip().splitlines()
    assert lines == p.emx_port_lines(cell.emx_ports)
    assert len(lines) == 4
    assert lines == ["-p N1=N1:G02", "-p N2=N2:G04", "-p P1=P1:G01", "-p P2=P2:G03"]

    # every ring boundary crossing (NR-1 per winding; NR=3 -> 2 each) draws
    # a dive leg with vias() at both endpoints, so xfm_tw is unconditionally
    # via-ful, unlike xfm_bs/xfm_balun where vias depend on optional CT/
    # nesting.
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["via_landing_audit"]["status"] == "pass"
    assert manifest["via_landing_audit"]["vias_checked"] >= 1
    assert manifest["geometry"]["config"]["ring_count"] == 3


def test_xfm_tw_wide_trace_rule_boundary_passes_product_gates(tmp_path):
    """The rule-derived mitred path supports its first M10 W=10 boundary."""
    from ic_opt.em.pcell.drc_audit import (
        audit_gds,
        product_scope_record,
        require_layers_from_config,
    )

    gp = _load_plugin()
    payload = {
        **_xfm_tw_config_dict(),
        "outer_diameter_um": 124.0,
        "width_um": 10.0,
        "spacing_um": 4.0,
        "ring_count": 3,
        "port_gap_p_um": 8.0,
        "port_gap_n_um": 8.0,
        "metal": "10",
    }
    cfg = gp.CleanPortXfmTwConfig.model_validate(payload)
    result = gp.CleanPortXfmTwGenerator().generate(
        cfg, outdir=tmp_path, gds_name="xfm_tw_wide_boundary.gds"
    )
    expected = require_layers_from_config(
        "clean_port_xfm_tw", cfg.model_dump(mode="json")
    )
    assert expected == ["M10", "M9"]
    product = product_scope_record(
        audit_gds(result.gds_path, "n28_1p10m"),
        expected_conductors=expected,
        ignore_layers=("M1",),
    )
    assert product["outcome"] == "pass", product
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["via_landing_audit"]["status"] == "pass"
    assert manifest["via_landing_audit"]["vias_checked"] >= 1


def test_xfm_tw_port_positions_bottom_bottom_top_top(tmp_path):
    """P1/P2 at the bottom (-y), N1/N2 at the top (+y); P1/N1 on the +x
    side, P2/N2 on the -x side (spec.md's fixed connectivity model, not a
    display convention any caller may rename -- see xfm_tw's own docstring
    and _FixedXfmPortOrderMixin)."""
    gp = _load_plugin()
    cfg = gp.CleanPortXfmTwConfig.model_validate(_xfm_tw_config_dict())
    result = gp.CleanPortXfmTwGenerator().generate(
        cfg, outdir=tmp_path, gds_name="tw.gds")
    manifest = json.loads(result.manifest_path.read_text())
    p = gp._clean_port()
    ctx = p.process_rule_context("n28_1p10m")
    fx = p.GroundFixtureConfig(inner_margin_um=15.0, ring_width_um=50.0,
                               stub_width_um=5.0, stub_length_um=2.0,
                               stub_chamfer_um=0.0)
    cell = p.xfm_tw(OD=260.0, W=6.0, S=6.0, NR=3, OPENING_P=10.0,
                    OPENING_N=10.0, LEAD=20.0, SL_ME="AP",
                    ground_fixture=fx, process=ctx)
    xy = {q["logical_name"]: q["label_xy_um"] for q in cell.emx_ports}
    assert xy["P1"][1] < 0 and xy["P1"][0] > 0
    assert xy["P2"][1] < 0 and xy["P2"][0] < 0
    assert xy["N1"][1] > 0 and xy["N1"][0] > 0
    assert xy["N2"][1] > 0 and xy["N2"][0] < 0
    assert manifest["geometry"]["config"]["port_order"] == [
        "P1", "N1", "P2", "N2"]


def test_xfm_tw_generator_rejects_ap_strict_pitch_shortfall(tmp_path):
    """AP body strictly below the ring-pitch clearance (W=4/S=1.5:
    pitch=5.5 < K=W+AP min_space=6.0): _tw_slot_half_width fails closed,
    and the rejection propagates through the plugin config/generator path
    unmodified -- the SAME guard pinned directly against the pcell in
    test_tw_n28_ap_body_ring_pitch_guard_equality_and_shortfall. (The
    W=4/S=2 EQUALITY case this test used to pin as a rejection is a
    passing DRC equality and builds since the six-family
    tight-spacing-clearance fix.)"""
    gp = _load_plugin()
    cfg = gp.CleanPortXfmTwConfig.model_validate(
        {**_xfm_tw_config_dict(), "width_um": 4.0, "spacing_um": 1.5})
    with pytest.raises(ValueError, match="ring pitch 5.500") as excinfo:
        gp.CleanPortXfmTwGenerator().generate(
            cfg, outdir=tmp_path, gds_name="tw_tight.gds")
    assert "min_space=6.000" in str(excinfo.value)


def test_xfm_tw_generator_requires_vias_true(tmp_path, monkeypatch):
    gp = _load_plugin()
    monkeypatch.setattr(
        gp, "audit_via_landing",
        lambda *a, **k: {"status": "pass", "vias_checked": 0})
    cfg = gp.CleanPortXfmTwConfig.model_validate(_xfm_tw_config_dict())
    with pytest.raises(ValueError, match="requires_vias=True"):
        gp.CleanPortXfmTwGenerator().generate(
            cfg, outdir=tmp_path, gds_name="tw.gds")


def test_xfm_tw_stub_width_by_port_overrides_auto(tmp_path):
    gp = _load_plugin()
    base = {**_xfm_tw_config_dict(), "width_um": 5.0}
    auto_over = gp.CleanPortXfmTwConfig.model_validate(
        {**_without_stub_width(base),
         "ground_fixture": {**_without_stub_width(base)["ground_fixture"],
                            "stub_width_by_port_um": {"N2": 3.0}}})
    explicit = gp.CleanPortXfmTwConfig.model_validate(
        {**base, "ground_fixture": {**base["ground_fixture"],
                                    "stub_width_um": 5.0,
                                    "stub_width_by_port_um": {"N2": 3.0}}})
    r_auto = gp.CleanPortXfmTwGenerator().generate(
        auto_over, outdir=tmp_path / "auto", gds_name="dev.gds")
    r_exp = gp.CleanPortXfmTwGenerator().generate(
        explicit, outdir=tmp_path / "exp", gds_name="dev.gds")
    assert r_auto.gds_path.read_bytes() == r_exp.gds_path.read_bytes()


# ---------------------------------------------------------------------------
# xfm_il plugin contract (ticket 04 -- .scratch/xfm-il-interleaved/issues/
# 04-plugin-contract-recipe.md), the 6th registered device. Single `turns`
# field maps NT_P=NT_S=turns (ticket 03c's equal-turns constraint made
# structurally impossible to violate); ct_primary_metal/ct_secondary_metal
# are direction-dispatched (above top_metal -> upward, no floor; below ->
# downward, floor top_metal-3) mirroring the pcell's own
# ``_il_ct_metal_guard``.
# ---------------------------------------------------------------------------


def _xfm_il_config_dict():
    # N28 M9 body (no CT) -- the SAME geometry
    # test_pcell_inductor_python_port_clean.py's `_il_n28()` helper uses,
    # already proven to build via
    # test_xfm_il_n28_sl_m9_body_without_ct_still_builds.
    return {
        "process_profile": "n28_1p10m",
        "port_order": ["P1", "N1", "P2", "N2"],
        "outer_diameter_um": 200.0,
        "width_um": 5.0,
        "spacing_um": 2.5,
        "turns": 3,
        "primary_opening_um": 18.0,
        "secondary_opening_um": 18.0,
        "primary_lead_length_um": 20.0,
        "secondary_lead_length_um": 20.0,
        "metal": "9",
        "ground_fixture": _fixture_dict(),
    }


def test_xfm_il_config_happy_path():
    gp = _load_plugin()
    cfg = gp.CleanPortXfmIlConfig.model_validate(_xfm_il_config_dict())
    assert cfg.outer_diameter_um == 200.0
    assert cfg.turns == 3
    assert cfg.port_order == ["P1", "N1", "P2", "N2"]
    assert cfg.ct_primary_metal is None and cfg.ct_secondary_metal is None


def test_xfm_il_config_rejects_extra_fields():
    gp = _load_plugin()
    payload = _xfm_il_config_dict()
    payload["bogus"] = 1
    with pytest.raises(ValidationError):
        gp.CleanPortXfmIlConfig.model_validate(payload)


@pytest.mark.parametrize("bad_turns", [1, 0, -1])
def test_xfm_il_config_rejects_turns_below_two(bad_turns):
    """turns floor mirrors the pcell's own NT_P>=2 guard (a single
    interleaved turn has no crossover to interleave against)."""
    gp = _load_plugin()
    with pytest.raises(ValidationError):
        gp.CleanPortXfmIlConfig.model_validate(
            {**_xfm_il_config_dict(), "turns": bad_turns})


@pytest.mark.parametrize(
    "bad_metal", ["1", "M1", "m1", "2", "M2", "3", "M3"])
def test_xfm_il_config_rejects_m1_through_m3_top_metal(bad_metal):
    """leg2 lands TWO stack levels below top_metal (ticket 02d's
    dual-layer-legs fix) -- an M3 top_metal would put leg2 on M1, the
    ground-fixture-reserved layer, just like M1/M2 directly."""
    gp = _load_plugin()
    payload = {**_xfm_il_config_dict(), "metal": bad_metal}
    with pytest.raises(ValidationError, match="reserved for the ground fixture"):
        gp.CleanPortXfmIlConfig.model_validate(payload)


def test_xfm_il_config_accepts_m4_top_metal():
    gp = _load_plugin()
    cfg = gp.CleanPortXfmIlConfig.model_validate(
        {**_xfm_il_config_dict(), "metal": "4"})
    assert cfg.metal == "4"


def test_xfm_il_config_port_order_must_be_canonical():
    gp = _load_plugin()
    payload = _xfm_il_config_dict()
    payload["port_order"] = ["p01", "p02", "p03", "p04"]
    with pytest.raises(ValidationError, match="P1"):
        gp.CleanPortXfmIlConfig.model_validate(payload)
    payload["port_order"] = ["N1", "N2", "P1", "P2"]  # right names, wrong order
    with pytest.raises(ValidationError):
        gp.CleanPortXfmIlConfig.model_validate(payload)


def test_xfm_il_ct_config_port_sets():
    """ct fields drive the exact port set: base [P1,N1,P2,N2] plus the
    enabled taps, CTP before CTS (family precedent)."""
    gp = _load_plugin()
    base = _xfm_il_config_dict()  # top_metal="9"
    with pytest.raises(ValidationError, match="port_order"):
        gp.CleanPortXfmIlConfig.model_validate(
            {**base, "port_order": ["P1", "N1", "P2", "N2", "CTP"]})
    with pytest.raises(ValidationError, match="port_order"):
        gp.CleanPortXfmIlConfig.model_validate(
            {**base, "ct_primary_metal": "10"})
    cfg = gp.CleanPortXfmIlConfig.model_validate(
        {**base, "ct_primary_metal": "10",
         "port_order": ["P1", "N1", "P2", "N2", "CTP"]})
    assert cfg.ct_primary_metal == "10"
    cfg2 = gp.CleanPortXfmIlConfig.model_validate(
        {**base, "ct_secondary_metal": "6",
         "port_order": ["P1", "N1", "P2", "N2", "CTS"]})
    assert cfg2.ct_secondary_metal == "6"
    both = gp.CleanPortXfmIlConfig.model_validate(
        {**base, "ct_primary_metal": "10", "ct_secondary_metal": "6",
         "port_order": ["P1", "N1", "P2", "N2", "CTP", "CTS"]})
    assert both.ct_primary_metal == "10" and both.ct_secondary_metal == "6"
    with pytest.raises(ValidationError, match="port_order"):
        gp.CleanPortXfmIlConfig.model_validate(
            {**base, "ct_primary_metal": "10", "ct_secondary_metal": "6",
             "port_order": ["P1", "N1", "P2", "N2", "CTS", "CTP"]})


def test_xfm_il_ct_metal_rejects_m1():
    gp = _load_plugin()
    base = _xfm_il_config_dict()
    with pytest.raises(ValidationError, match="reserved for the ground fixture"):
        gp.CleanPortXfmIlConfig.model_validate(
            {**base, "ct_primary_metal": "M1",
             "port_order": ["P1", "N1", "P2", "N2", "CTP"]})


def test_xfm_il_ct_metal_equal_to_top_metal_rejected():
    gp = _load_plugin()
    base = _xfm_il_config_dict()  # top_metal="9"
    with pytest.raises(ValidationError, match="cannot equal"):
        gp.CleanPortXfmIlConfig.model_validate(
            {**base, "ct_primary_metal": "9",
             "port_order": ["P1", "N1", "P2", "N2", "CTP"]})


@pytest.mark.parametrize("too_close", ["8", "7"])  # top_metal-1, top_metal-2
def test_xfm_il_ct_metal_downward_within_floor_rejected(too_close):
    """A downward CT within top_metal-1/top_metal-2 galvanically shorts the
    tap net to a crossunder leg (ticket 03c: the floor is top_metal-3)."""
    gp = _load_plugin()
    base = _xfm_il_config_dict()  # top_metal="9"
    with pytest.raises(ValidationError, match="three levels below"):
        gp.CleanPortXfmIlConfig.model_validate(
            {**base, "ct_secondary_metal": too_close,
             "port_order": ["P1", "N1", "P2", "N2", "CTS"]})


def test_xfm_il_ct_metal_downward_at_floor_accepted():
    gp = _load_plugin()
    base = _xfm_il_config_dict()  # top_metal="9"
    cfg = gp.CleanPortXfmIlConfig.model_validate(
        {**base, "ct_secondary_metal": "6",  # top_metal-3
         "port_order": ["P1", "N1", "P2", "N2", "CTS"]})
    assert cfg.ct_secondary_metal == "6"


def test_xfm_il_ct_metal_upward_has_no_floor():
    """Upward CT metals (above top_metal) never touch a crossunder leg, so
    even the very next level up (top_metal+1) is accepted -- unlike the
    downward direction's top_metal-3 floor."""
    gp = _load_plugin()
    base = _xfm_il_config_dict()  # top_metal="9"
    cfg = gp.CleanPortXfmIlConfig.model_validate(
        {**base, "ct_primary_metal": "10",  # top_metal+1
         "port_order": ["P1", "N1", "P2", "N2", "CTP"]})
    assert cfg.ct_primary_metal == "10"


def test_xfm_il_generator_parity_with_direct_module_call(tmp_path):
    gp = _load_plugin()
    p = gp._clean_port()

    cfg = gp.CleanPortXfmIlConfig.model_validate(_xfm_il_config_dict())
    result = gp.CleanPortXfmIlGenerator().generate(
        cfg, outdir=tmp_path / "plugin", gds_name="il.gds")
    assert result.generator_id == "clean_port_xfm_il"
    assert result.top_cell == "il"

    ctx = p.process_rule_context("n28_1p10m")
    fx = p.GroundFixtureConfig(inner_margin_um=15.0, ring_width_um=50.0,
                               stub_width_um=5.0, stub_length_um=2.0,
                               stub_chamfer_um=0.0)
    cell = p.xfm_il(OD=200.0, W=5.0, S=2.5, NT_P=3, NT_S=3,
                    OPENING_P=18.0, OPENING_S=18.0, LEAD_P=20.0, LEAD_S=20.0,
                    SL_ME="9", ground_fixture=fx, process=ctx)
    cell.name = "il"
    direct_gds = tmp_path / "direct" / "il.gds"
    direct_gds.parent.mkdir(parents=True)
    p.write_gds(cell, direct_gds)

    _, a = _drawing_layer_regions(result.gds_path)
    _, b = _drawing_layer_regions(direct_gds)
    assert set(a) == set(b)
    for ld in a:
        assert (a[ld] ^ b[ld]).is_empty(), f"layer {ld} differs"

    lines = result.emx_ports_path.read_text().strip().splitlines()
    assert lines == p.emx_port_lines(cell.emx_ports)
    assert len(lines) == 4
    assert lines == ["-p N1=N1:G02", "-p N2=N2:G04", "-p P1=P1:G01", "-p P2=P2:G03"]

    # NT_P=NT_S=turns>=2 (config floor) always draws the interleaved
    # crossunder's two independent legs, so xfm_il is unconditionally
    # via-ful.
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["via_landing_audit"]["status"] == "pass"
    assert manifest["via_landing_audit"]["vias_checked"] >= 1
    assert manifest["geometry"]["config"]["turns"] == 3


def test_xfm_il_wide_boundary_has_full_width_primary_pin_landing(tmp_path):
    """The first feasible W=8 NT=2 IL keeps a full-width P-side joint.

    secondary_opening 19.5 (was 21): since 2026-09-22 the secondary escape's
    arm-tip pad must sit on the secondary's flat (OPENING_S + tip <= BA)."""
    import klayout.db as kdb

    from ic_opt.em.pcell.drc_audit import (
        audit_gds,
        product_scope_record,
        require_layers_from_config,
    )

    gp = _load_plugin()
    payload = {
        **_xfm_il_config_dict(),
        "outer_diameter_um": 153.0,
        "width_um": 8.0,
        "spacing_um": 2.1,
        "turns": 2,
        "primary_opening_um": 21.0,
        "secondary_opening_um": 19.5,
        "metal": "10",
    }
    cfg = gp.CleanPortXfmIlConfig.model_validate(payload)
    result = gp.CleanPortXfmIlGenerator().generate(
        cfg, outdir=tmp_path, gds_name="xfm_il_wide_boundary.gds"
    )

    expected = require_layers_from_config(
        "clean_port_xfm_il", cfg.model_dump(mode="json")
    )
    assert expected == ["M10", "M9", "M8"]
    product = product_scope_record(
        audit_gds(result.gds_path, "n28_1p10m"),
        expected_conductors=expected,
        ignore_layers=("M1",),
    )
    assert product["outcome"] == "pass", product

    p = gp._clean_port()
    ctx = p.process_rule_context("n28_1p10m")
    _, regions = _drawing_layer_regions(result.gds_path)
    body = regions[p._metal(10, ctx)]
    landing = kdb.Region(kdb.Box(
        p._nm(76.5 - 8.0), p._nm(21.0),
        p._nm(76.5), p._nm(21.0 + 8.0),
    ))
    assert (landing - body).is_empty()


def test_xfm_il_wide_escape_and_bridge_obey_parallel_spacing(tmp_path):
    """The user-reported xfm_il_29 geometry must clear N28 wide M9 space."""
    from ic_opt.em.pcell.drc_audit import (
        audit_gds,
        product_scope_record,
        require_layers_from_config,
    )

    gp = _load_plugin()
    payload = {
        **_xfm_il_config_dict(),
        "outer_diameter_um": 332.0,
        "width_um": 9.71,
        "spacing_um": 2.43,
        "turns": 4,
        "primary_opening_um": 24.9,
        "secondary_opening_um": 24.9,
        "metal": "10",
    }
    cfg = gp.CleanPortXfmIlConfig.model_validate(payload)
    result = gp.CleanPortXfmIlGenerator().generate(
        cfg, outdir=tmp_path, gds_name="xfm_il_29.gds"
    )

    report = audit_gds(result.gds_path, "n28_1p10m")
    product = product_scope_record(
        report,
        expected_conductors=require_layers_from_config(
            "clean_port_xfm_il", cfg.model_dump(mode="json")
        ),
        ignore_layers=("M1",),
    )

    assert product["outcome"] == "pass", product


def test_xfm_il_generator_requires_vias_true(tmp_path, monkeypatch):
    gp = _load_plugin()
    monkeypatch.setattr(
        gp, "audit_via_landing",
        lambda *a, **k: {"status": "pass", "vias_checked": 0})
    cfg = gp.CleanPortXfmIlConfig.model_validate(_xfm_il_config_dict())
    with pytest.raises(ValueError, match="requires_vias=True"):
        gp.CleanPortXfmIlGenerator().generate(
            cfg, outdir=tmp_path, gds_name="il.gds")


# ---------------------------------------------------------------------------
# N28 smoke (ticket 04 item 6): M9 body CTP upward to M10 builds the full
# gds/emx_ports/manifest triplet; an AP body CTP (structurally blocked in
# EITHER direction, ticket 03c) rejects with the pcell's own message
# propagated unmodified through the plugin.
# ---------------------------------------------------------------------------


def test_xfm_il_generator_n28_m9_ctp_upward_to_m10_builds_full_triplet(tmp_path):
    gp = _load_plugin()
    ctx = gp._clean_port().process_rule_context("n28_1p10m")
    cfg = gp.CleanPortXfmIlConfig.model_validate(
        {**_xfm_il_config_dict(), "ct_primary_metal": "10",
         "port_order": ["P1", "N1", "P2", "N2", "CTP"]})
    result = gp.CleanPortXfmIlGenerator().generate(
        cfg, outdir=tmp_path, gds_name="il_n28_ctp.gds")

    assert result.gds_path.is_file() and result.gds_path.stat().st_size > 0

    lines = result.emx_ports_path.read_text().strip().splitlines()
    assert len(lines) == 5
    assert lines[0] == "-p CTP=CTP:G05"

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["generator_id"] == "clean_port_xfm_il"
    assert manifest["geometry"]["config"]["ct_primary_metal"] == "10"
    assert manifest["via_landing_audit"]["status"] == "pass"
    assert manifest["via_landing_audit"]["vias_checked"] >= 1

    p = gp._clean_port()
    assert p.process_metal_layer(ctx, 10) is not None  # sanity: M10 exists


def test_xfm_il_generator_n28_ap_body_ctp_rejected_message_propagates(tmp_path):
    """SL_ME="AP" has no metal above it, so an upward CTP is impossible, and
    a downward one is structurally always blocked (ticket 03c) -- the
    pcell's own AP-specific "real limit" message (not the generic upward
    hint) must reach the caller unmodified through the plugin."""
    gp = _load_plugin()
    cfg = gp.CleanPortXfmIlConfig.model_validate(
        {**_xfm_il_config_dict(), "metal": "AP", "ct_primary_metal": "8",
         "port_order": ["P1", "N1", "P2", "N2", "CTP"]})
    with pytest.raises(ValueError, match="SL_ME='AP' has no metal above it"):
        gp.CleanPortXfmIlGenerator().generate(
            cfg, outdir=tmp_path, gds_name="il_ap_ctp.gds")


# ---------------------------------------------------------------------------
# Port lattice contract (2026-09-21): the independent post-write port audit
# (spec.md's "加一层独立复核" -- re-parse the just-written GDS and cross-check
# it against the in-memory manifest; zero tolerance, no nearest-point
# search), recorded in the manifest next to via_landing_audit.
# ---------------------------------------------------------------------------


def test_generator_manifest_records_a_passing_port_lattice_audit(tmp_path):
    gp = _load_plugin()
    cfg = gp.CleanPortIndSymConfig.model_validate(_ind_sym_config_dict())
    result = gp.CleanPortIndSymGenerator().generate(
        cfg, outdir=tmp_path, gds_name="cand.gds")
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["port_lattice_audit"] == {
        "status": "pass", "ports_checked": 2}


def _ind_sym_cell_and_gds(tmp_path, gp, name="audit"):
    p = gp._clean_port()
    ctx = p.process_rule_context("n28_1p10m")
    cell = p.ind_sym(OD=60.0, W=2.0, OPENING=5.0, LEAD=10.0, S=2.0, NT=2,
                     TOP_ME="9", BTM_ME="8", process=ctx)
    gds_path = tmp_path / f"{name}.gds"
    p.write_gds(cell, gds_path)
    return cell, gds_path


def test_port_lattice_audit_fails_closed_on_tampered_point(tmp_path, monkeypatch):
    """A port dict whose point_nm has been nudged 1 nm off what is
    actually in the written GDS -- D5's own scale -- is rejected, not
    silently accepted as "close enough". (point_nm, not label_xy_um: the
    audit reads its working point straight from point_nm -- port contract
    2026-09-21 -- so that is the field a mis-registration would
    actually show up in; every family's finalize_emx_ports() keeps
    label_xy_um and point_nm in lockstep, so tampering only label_xy_um
    would no longer reach the audit at all.)"""
    gp = _load_plugin()
    cell, gds_path = _ind_sym_cell_and_gds(tmp_path, gp, "tampered_point")
    from copy import deepcopy
    ports = deepcopy(cell.emx_ports)
    monkeypatch.setitem(
        ports[0], "point_nm",
        [ports[0]["point_nm"][0] + 1, ports[0]["point_nm"][1]])
    with pytest.raises(ValueError, match="port lattice audit failed"):
        gp.audit_port_lattice(gds_path, ports, "n28_1p10m")


def test_port_lattice_audit_fails_closed_on_point_off_its_own_zone(tmp_path, monkeypatch):
    """A port dict whose lead_zone_nm has been mis-registered so the point
    no longer sits on its own edge -- the in-memory half of the check,
    exercised the same way finalize_emx_ports's own invariant is, but
    here through the post-write entry point."""
    gp = _load_plugin()
    cell, gds_path = _ind_sym_cell_and_gds(tmp_path, gp, "off_zone")
    from copy import deepcopy
    ports = deepcopy(cell.emx_ports)
    x0, y0, x1, y1 = ports[0]["lead_zone_nm"]
    monkeypatch.setitem(ports[0], "lead_zone_nm", [x0 + 5, y0 + 5, x1 + 5, y1 + 5])
    with pytest.raises(ValueError, match="port lattice audit failed"):
        gp.audit_port_lattice(gds_path, ports, "n28_1p10m")


def test_port_lattice_audit_fails_closed_on_zone_matching_only_one_axis(
    tmp_path, monkeypatch
):
    """A forged lead_zone_nm that shares the port's real point on ONE axis
    (untouched) but is shifted 50 um away on the OTHER, orthogonal axis --
    so the point is nowhere near this box -- must still be rejected. The
    edge check (`px not in (x0, x1) and py not in (y0, y1)`) is an OR of
    two per-axis membership tests with no bound on the orthogonal axis, so
    it short-circuits to "on an edge" whenever EITHER axis happens to
    match, even when the point is far outside the zone's range on the
    other axis (break-the-contract review, port contract 2026-09-21) --
    unlike `test_port_lattice_audit_fails_closed_on_point_off_its_own_zone`
    above, which shifts BOTH axes and so cannot tell the two shapes of
    check apart. `_zone_edge_and_audit` (this file, above) already asserts
    the stronger, correctly-bounded condition as a TEST HELPER; this pins
    that same strength into `audit_port_lattice` itself, the actual
    independent-of-memory audit the manifest records."""
    gp = _load_plugin()
    cell, gds_path = _ind_sym_cell_and_gds(tmp_path, gp, "off_zone_one_axis")
    from copy import deepcopy
    ports = deepcopy(cell.emx_ports)
    x0, y0, x1, y1 = ports[0]["lead_zone_nm"]
    assert y0 != y1, "need a non-degenerate zone on the axis being shifted"
    monkeypatch.setitem(ports[0], "lead_zone_nm", [x0, y0 + 50_000, x1, y1 + 50_000])
    with pytest.raises(ValueError, match="port lattice audit failed"):
        gp.audit_port_lattice(gds_path, ports, "n28_1p10m")


def test_port_lattice_audit_fails_closed_when_label_missing(tmp_path):
    """The written GDS is missing a port's own text glyph entirely (e.g. a
    future write_gds bug that drops a label) -- caught independently of
    whatever the in-memory Cell.labels said, because the audit re-parses
    the file klayout actually wrote. A minimal hand-built cell (real metal
    under the point, but its label cleared before writing) isolates this
    one failure mode from the metal-missing one above."""
    gp = _load_plugin()
    p = gp._clean_port()
    ctx = p.process_rule_context("n28_1p10m")
    pin_layer = p.process_pin_layer(ctx, 9)
    metal_layer = p.process_metal_layer(ctx, 9)
    cell = p.Cell("bare_port", "test", {})
    cell.add_emx_port(name="P1", logical_name="P1", metal=9,
                      label_layer=pin_layer, x_um=2.0, y_um=1.0,
                      lead_zone_um=(0.0, 0.0, 2.0, 2.0))
    cell.add_rect(metal_layer, 0.0, 0.0, 2.0, 2.0)
    # (port contract 2026-09-21) add_emx_port only registers the
    # port structurally (cell.ports); cell.emx_ports is populated
    # exclusively by finalize_emx_ports -- see
    # test_add_emx_port_does_not_populate_emx_ports_directly.
    cell.emx_ports = p.finalize_emx_ports(cell)
    cell.labels = []
    gds_path = tmp_path / "missing_label.gds"
    p.write_gds(cell, gds_path)
    with pytest.raises(ValueError, match="port lattice audit failed"):
        gp.audit_port_lattice(gds_path, cell.emx_ports, "n28_1p10m")


def test_port_lattice_audit_fails_closed_when_metal_missing(tmp_path):
    """The port's own declared conductor layer has no drawn metal at all
    under the point (e.g. a future construction bug that skips the lead) --
    caught the same way, independent of the in-memory shapes. A minimal
    hand-built cell (a label with no polygon at all on its metal layer)
    isolates this one failure mode from ind_sym's own nested geometry."""
    gp = _load_plugin()
    p = gp._clean_port()
    ctx = p.process_rule_context("n28_1p10m")
    metal_layer = p.process_metal_layer(ctx, 9)
    pin_layer = p.process_pin_layer(ctx, 9)
    cell = p.Cell("bare_port", "test", {})
    cell.add_emx_port(name="P1", logical_name="P1", metal=9,
                      label_layer=pin_layer, x_um=1.0, y_um=1.0,
                      lead_zone_um=(0.0, 0.0, 2.0, 1.0))
    # (port contract 2026-09-21) see the sibling test above.
    cell.emx_ports = p.finalize_emx_ports(cell)
    gds_path = tmp_path / "no_metal.gds"
    p.write_gds(cell, gds_path)
    assert metal_layer not in dict(
        (layer, True) for layer, _pts in cell.flat_shapes())
    with pytest.raises(ValueError, match="port lattice audit failed"):
        gp.audit_port_lattice(gds_path, cell.emx_ports, "n28_1p10m")


# ---------------------------------------------------------------------------
# Port lattice contract (2026-09-21): the structural acceptance
# obligation spec.md sets for THIS stage -- every port of every family (all
# CT-capable variants included) has its point exactly on the tip edge of
# its own registered zone, AND passes the independent post-write audit
# (audit_port_lattice, re-parsing the just-written GDS) -- on BOTH process
# profiles this repo ships (n28_1p10m: M1..M10+AP; n65_1p9m: M1..M9+AP, no
# M10). Six families x representative CT variant x two profiles.
# ---------------------------------------------------------------------------

_N28 = "n28_1p10m"
_N65 = "n65_1p9m"


def _zone_edge_and_audit(p, gp, cell, profile_id, tmp_path, label):
    """Every port's point_nm sits ON an edge of its own lead_zone_nm, and
    the independent post-write audit (re-parsing the just-written GDS)
    passes -- the two acceptance obligations spec.md names together for
    this stage's structural test."""
    assert cell.emx_ports
    for pd in cell.emx_ports:
        x, y = pd["point_nm"]
        x0, y0, x1, y1 = pd["lead_zone_nm"]
        assert min(x0, x1) <= x <= max(x0, x1), (label, pd)
        assert min(y0, y1) <= y <= max(y0, y1), (label, pd)
        assert x in (x0, x1) or y in (y0, y1), (label, pd)
    gds_path = tmp_path / f"{label}.gds"
    p.write_gds(cell, gds_path)
    result = gp.audit_port_lattice(gds_path, cell.emx_ports, profile_id)
    assert result == {"status": "pass", "ports_checked": len(cell.emx_ports)}


# Per-profile metal choices (port contract 2026-09-21 test data): n65_1p9m
# has no M10, so every n28 combination below is mirrored one real stack
# level down for n65 (e.g. bs primary/secondary "10"/"9" -> "9"/"8") --
# the SAME relative depth from top metal, not re-derived per profile,
# since both profiles' M1..M9 ranges are contiguous.
_IND_SYM_CASES = [
    (_N28, "9", "8", "7"), (_N65, "8", "7", "6"),
]
_BS_CASES = [
    (_N28, "10", "9", "8", "7"), (_N65, "9", "8", "7", "6"),
]
# ms's own single/multi metal convention (mirrors the family's own
# established default, SINGLE_ME="AP" -- an arbitrary SINGLE_ME="10" choice
# put the primary tap's via stack close enough to the multi winding's own
# crossunder to short the two nets, an unrelated geometric collision, not a
# port-lattice finding).
_MS_CASES = [
    (_N28, "AP", "10", "8", "7"), (_N65, "AP", "9", "7", "6"),
]
_BALUN_CASES = [
    # N28 CT_S on M7 (not M6): a tap stack carrying the winding W must
    # respect each level's own max-width rule (gdsfactory review 2026-09-21).
    (_N28, "9", "7", "7"), (_N65, "8", "6", "5"),
]
_IL_CASES = [
    (_N28, "9", "10", "6"), (_N65, "8", "9", "5"),
]
_TW_CASES = [(_N28, "9"), (_N65, "8")]


@pytest.mark.parametrize("profile_id,top,bottom,ct", _IND_SYM_CASES)
@pytest.mark.parametrize("with_ct", [False, True])
def test_port_lattice_ind_sym_zone_edge_and_audit(
    with_ct, profile_id, top, bottom, ct, tmp_path
):
    gp = _load_plugin()
    p = gp._clean_port()
    process = p.process_rule_context(profile_id)
    cell = p.ind_sym(
        OD=60.045, W=2.015, OPENING=5.005, LEAD=10.045, S=2.005, NT=3,
        TOP_ME=top, BTM_ME=bottom, CT_ME=(ct if with_ct else None),
        process=process)
    _zone_edge_and_audit(
        p, gp, cell, profile_id, tmp_path,
        f"ind_sym-{profile_id}-ct{with_ct}")


@pytest.mark.parametrize("profile_id,pri_me,sec_me,ct_p,ct_s", _BS_CASES)
@pytest.mark.parametrize("with_ct", [False, True])
def test_port_lattice_xfm_bs_zone_edge_and_audit(
    with_ct, profile_id, pri_me, sec_me, ct_p, ct_s, tmp_path
):
    gp = _load_plugin()
    p = gp._clean_port()
    process = p.process_rule_context(profile_id)
    cell = p.xfm_bs(
        OD_P=90.045, OD_S=90.045, W_P=4.015, W_S=4.015,
        OPENING_P=8.005, OPENING_S=8.005, LEAD_P=20.045, LEAD_S=20.045,
        CENTER_SPACING=27.485, PRI_ME=pri_me, SEC_ME=sec_me,
        CT_P_ME=(ct_p if with_ct else None),
        CT_S_ME=(ct_s if with_ct else None),
        process=process)
    _zone_edge_and_audit(
        p, gp, cell, profile_id, tmp_path, f"xfm_bs-{profile_id}-ct{with_ct}")


@pytest.mark.parametrize("profile_id,single_me,multi_me,ct_p,ct_s", _MS_CASES)
@pytest.mark.parametrize("with_ct", [False, True])
def test_port_lattice_xfm_ms_zone_edge_and_audit(
    with_ct, profile_id, single_me, multi_me, ct_p, ct_s, tmp_path
):
    gp = _load_plugin()
    p = gp._clean_port()
    process = p.process_rule_context(profile_id)
    cell = p.xfm_ms(
        # W_S wider than the bs/il default (6.015, not 4.015): on n65_1p9m
        # a CT_P_ME="AP" tap's M9->AP RV via array needs more than a
        # 4.015um window (an unrelated process via-sizing floor, not a
        # port-lattice concern).
        OD_S=90.045, OD_M=90.045, W_S=6.015, W_M=4.015,
        OPENING_S=8.005, OPENING_M=6.005, LEAD_S=20.045, LEAD_M=15.045,
        NT_M=3, S_M=2.005, CENTER_SPACING=27.485,
        SINGLE_ME=single_me, MULTI_ME=multi_me,
        CT_P_ME=(ct_p if with_ct else None),
        CT_S_ME=(ct_s if with_ct else None),
        process=process)
    _zone_edge_and_audit(
        p, gp, cell, profile_id, tmp_path, f"xfm_ms-{profile_id}-ct{with_ct}")


@pytest.mark.parametrize("profile_id,balun_me,ct_p,ct_s", _BALUN_CASES)
@pytest.mark.parametrize("with_ct", [False, True])
def test_port_lattice_xfm_balun_zone_edge_and_audit(
    with_ct, profile_id, balun_me, ct_p, ct_s, tmp_path
):
    gp = _load_plugin()
    p = gp._clean_port()
    process = p.process_rule_context(profile_id)
    # OPENING_S wider than the family default (14.005, not 8.005): the
    # secondary's own crossunder escape bars need more clearance from
    # the primary's crossover legs on some (profile, metal) pairs (an
    # inter-net spacing floor unrelated to port registration -- the
    # error itself names this remedy). Nested only: the side-by-side
    # mode is gone (2026-09-22).
    kwargs = dict(OD_P=210.045, OD_S=186.045, W_P=5.015, W_S=5.015,
                  OPENING_P=8.005, OPENING_S=14.005, CENTER_SPACING=0.0)
    cell = p.xfm_balun(
        # S wider than the bs/il default (3.005, not 2.005): n65_1p9m's
        # M8 body needs more chamfer-staircase margin between the P/S
        # rings at this OD than 2.005 leaves (an unrelated DRC floor, not
        # a port-lattice concern).
        S=3.005, LEAD_P=20.045, LEAD_S=20.045, NT_P=1, NT_S=1,
        BALUN_ME=balun_me,
        CT_P_ME=(ct_p if with_ct else None),
        CT_S_ME=(ct_s if with_ct else None),
        process=process, **kwargs)
    _zone_edge_and_audit(
        p, gp, cell, profile_id, tmp_path,
        f"xfm_balun-{profile_id}-ct{with_ct}")


@pytest.mark.parametrize("profile_id,sl_me,ct_p,ct_s", _IL_CASES)
@pytest.mark.parametrize("with_ct", [False, True])
def test_port_lattice_xfm_il_zone_edge_and_audit(
    with_ct, profile_id, sl_me, ct_p, ct_s, tmp_path
):
    gp = _load_plugin()
    p = gp._clean_port()
    process = p.process_rule_context(profile_id)
    cell = p.xfm_il(
        OD=200.045, W=4.015, S=2.005, NT_P=3, NT_S=3,
        OPENING_P=14.005, OPENING_S=14.005, LEAD_P=20.045, LEAD_S=20.045,
        SL_ME=sl_me,
        CT_P_ME=(ct_p if with_ct else None),
        CT_S_ME=(ct_s if with_ct else None),
        process=process)
    _zone_edge_and_audit(
        p, gp, cell, profile_id, tmp_path, f"xfm_il-{profile_id}-ct{with_ct}")


@pytest.mark.parametrize("profile_id,sl_me", _TW_CASES)
def test_port_lattice_xfm_tw_zone_edge_and_audit(profile_id, sl_me, tmp_path):
    """xfm_tw has no CT (spec.md "非目标") -- one variant per profile."""
    gp = _load_plugin()
    p = gp._clean_port()
    process = p.process_rule_context(profile_id)
    cell = p.xfm_tw(
        OD=204.045, W=4.015, S=2.005, NR=3,
        OPENING_P=14.005, OPENING_N=14.005, LEAD=20.045, SL_ME=sl_me,
        process=process)
    _zone_edge_and_audit(
        p, gp, cell, profile_id, tmp_path, f"xfm_tw-{profile_id}")


@pytest.mark.parametrize("profile_id,sl_me", _TW_CASES)
@pytest.mark.parametrize("lead_residue_nm", [1, 2])
def test_port_lattice_xfm_tw_zone_edge_and_audit_lead_off_5nm_mask_grid(
    profile_id, sl_me, lead_residue_nm, tmp_path
):
    """xfm_tw's own wide-path stub goes through a SECOND, independent
    grid-snap no other family's lead polygon needs: `add_wide_path`
    re-snaps every drawn hull vertex to the 0.005 um mask grid
    (`snap_nm_to_grid`) after mitring, to fix irrational-trig corner
    noise from diagonal/45-degree segments -- but that snap also touches
    the stub's own flush end cap, whose along-stub coordinate is
    `LEAD`-derived. `LEAD=20.045` (every other xfm_tw fixture in this
    file, `_TW_CASES` included) is itself already a 5nm-grid multiple
    (20045 nm / 5 == 4009), so it can never exercise that second snap
    (break-the-contract review, port contract 2026-09-21). A LEAD whose
    nm value has residue 1 or 2 mod 5nm -- the two residues that round
    the drawn cap edge INWARD, toward the ring -- must still register a
    port that lands on the real drawn conductor; this is exactly what
    the independent, re-parsed-from-GDS `audit_port_lattice` exists to
    catch, and pre-fix does not."""
    gp = _load_plugin()
    p = gp._clean_port()
    process = p.process_rule_context(profile_id)
    lead = 20.0 + lead_residue_nm * p.DBU_UM
    cell = p.xfm_tw(OD=204.0, W=4.0, S=2.0, NR=3,
                    OPENING_P=14.0, OPENING_N=14.0, LEAD=lead,
                    SL_ME=sl_me, process=process)
    _zone_edge_and_audit(
        p, gp, cell, profile_id, tmp_path,
        f"xfm_tw-lead-off-grid-{profile_id}-{lead_residue_nm}")


def test_xfm_il_config_ct_floor_follows_the_real_stack_on_n65():
    """n65_1p9m has no M10, so leg2 under an AP body is M8: a CT on M8 must
    be rejected at config time with the same verdict the pcell gives, not
    let through by top-3 arithmetic and rejected only at generate()."""
    gp = _load_plugin()
    base = {**_xfm_il_config_dict(), "process_profile": "n65_1p9m",
            "metal": "AP", "port_order": ["P1", "N1", "P2", "N2", "CTP"]}
    with pytest.raises(ValidationError, match="three levels below"):
        gp.CleanPortXfmIlConfig.model_validate({**base, "ct_primary_metal": "8"})
    gp.CleanPortXfmIlConfig.model_validate({**base, "ct_primary_metal": "7"})
