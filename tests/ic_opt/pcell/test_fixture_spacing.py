"""T19.5 section 1.7 (``docs/refactor/T19_5_TAP_TWIN_RECIPE_SPEC.md``): two rules of the ground fixture.

1. Spacing among the fixture's own shapes is exempt where its metal holds nothing else: on a metal chosen under
   ``metal_rule: free`` the DRC gate exempts ``min_space`` / ``wide_parallel_spacing`` there as it exempts ``max_width``;
   under ``shared`` and with ``metal`` absent (the bottom metal, a shield's strips there) nothing changes.
2. Stubs must not touch: two stubs that touch or overlap are refused when the fixture is laid out, naming the two ports
   and the gap (one edge would hold two G pins, which EMX refuses).

The case is xfm_bs with a same-metal primary tap and a small secondary opening (demo_6m, the windings on M6 / M5, the
``auto`` fixture metal M4 with a min space of 0.1 um): the CTP stub runs between the P2 and N2 stubs."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

pytest.importorskip("klayout.db")

import klayout.db as kdb

from ic_opt.em.pcell._pcell_core import PortError
from ic_opt.em.pcell.drc_audit import audit_gds
from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter
from ic_opt.spec import Device
from ic_opt.stages.em_chain import DrcRefused, _audit
from tests.ic_opt.pcell.test_xfm_bs_same_metal_tap import AUTO_STUBS, PROFILE, XFM_PORTS, generate

#: a 1.55 um secondary opening puts the P2 / N2 stubs (5 um, the secondary's width) at +-(1.55 .. 6.55) um, and a 3 um
#: same-metal primary tap's CTP stub at +-1.5 um between them: 0.05 um from each
NARROW = {"secondary_opening_um": 1.55, "ct_primary_metal": "6", "ct_primary_width_um": 3.0, "port_order": [*XFM_PORTS, "CTP"]}
DEVICE = Device(id="bs", generator="clean_port_xfm_bs", profile=PROFILE, ports=[*XFM_PORTS, "CTP"])
PGS = {"strip_width_um": 1.0, "strip_spacing_um": 2.0, "margin_um": 5.0}


def gate(gen, model, result) -> dict | None:
    """``stages.em_chain._audit`` on a build, as the pcell stage calls it: None when it passes, the record when it refuses."""
    try:
        _audit(DEVICE, gen, model, result.gds_path, fixture_metal=result.fixture_metal, fixture_metal_rule=result.fixture_metal_rule)
    except DrcRefused as refused:
        return refused.record
    return None


def with_shapes(gds: Path, metal: str, boxes, out: Path) -> Path:
    layout = kdb.Layout()
    layout.read(str(gds))
    li = layout.layer(*get_geometry_rule_adapter(PROFILE).layer(metal).drawing)
    for box in boxes:
        layout.top_cell().shapes(li).insert(box)
    layout.write(str(out))
    return out


def test_under_free_the_fixtures_own_spacing_passes_the_gate(tmp_path):
    """``auto`` chooses M4 under ``free``: the raw audit finds the CTP stub 0.05 um from the P2 and N2 stubs (two min_space
    findings on M4), and the gate -- the pcell stage's own ``_audit`` -- passes the build: on that metal every shape is
    the ring or a stub. ``M4`` named explicitly is the same choice."""
    for name, fixture in (("auto", {**AUTO_STUBS, "metal": "auto"}), ("named", {**AUTO_STUBS, "metal": "M4"})):
        gen, model, result = generate(tmp_path / name, "bs", fixture, **NARROW)
        assert (result.fixture_metal, result.fixture_metal_rule) == ("M4", "free")
        raw = audit_gds(result.gds_path, PROFILE)
        assert [(v.kind, v.layer, v.count) for v in raw.violations if v.layer == "M4" and v.kind != "max_width"] == [("min_space", "M4", 2)]
        assert gate(gen, model, result) is None, name


def test_under_shared_spacing_on_the_fixture_metal_still_counts(tmp_path):
    """The same drawing with M4 chosen under ``shared`` (the device may draw its internal shapes there): the gate refuses
    it on those two min_space findings, a ``DrcRefused`` whose issues are the ones the pcell stage always gave."""
    gen, model, result = generate(tmp_path, "bs", {**AUTO_STUBS, "metal": "auto", "metal_rule": "shared"}, **NARROW)
    assert (result.fixture_metal, result.fixture_metal_rule) == ("M4", "shared")
    with pytest.raises(DrcRefused) as refused:
        _audit(DEVICE, gen, model, result.gds_path, fixture_metal=result.fixture_metal, fixture_metal_rule=result.fixture_metal_rule)
    assert refused.value.record["violations"] == [{"kind": "min_space", "layer": "M4", "count": 2}]
    assert refused.value.issues == ["device bs: DRC audit found 1 violation(s)", "[min_space] M4 x2"]


def test_with_the_metal_absent_nothing_changes(tmp_path):
    """``metal`` absent: the fixture on the bottom metal, M1, no rule reported. The narrow build's stubs there fail the gate
    on M1's min space as before T19.5; so does a shielded build (``pgs``, metal absent or the bottom metal named) with two
    strips drawn closer than M1's min space -- a shield is a fabricated structure, its spacing counts."""
    gen, model, result = generate(tmp_path / "default", "bs", AUTO_STUBS, **NARROW)
    assert (result.fixture_metal, result.fixture_metal_rule) == ("M1", None)
    assert gate(gen, model, result)["violations"] == [{"kind": "min_space", "layer": "M1", "count": 2}]
    for name, fixture in (("pgs", AUTO_STUBS), ("pgs_m1", {**AUTO_STUBS, "metal": "M1"})):
        gen, model, shielded = generate(tmp_path / name, "bs", fixture, pgs=PGS)
        assert (shielded.fixture_metal, shielded.fixture_metal_rule) == ("M1", None)
        assert gate(gen, model, shielded) is None
        close = with_shapes(shielded.gds_path, "M1", [kdb.Box(-300_000, 400_000, -200_000, 405_000),       # 0.05 um apart (M1: 0.1 um)
                                                       kdb.Box(-300_000, 405_050, -200_000, 410_050)], tmp_path / f"{name}_close.gds")
        record = gate(gen, model, dataclasses.replace(shielded, gds_path=close))
        assert record is not None and {"kind": "min_space", "layer": "M1", "count": 1} in record["violations"], name


@pytest.mark.parametrize(("width", "word", "gap"), [(3.1, "touch", "0"), (3.2, "overlap", "-0.05")])
def test_stubs_that_touch_or_overlap_are_refused(tmp_path, width, word, gap):
    """A 3.1 um tap's stub (+-1.55 um) touches the P2 stub's edge at 1.55 um; a 3.2 um one overlaps it by 0.05 um: refused
    before anything is drawn, naming the two ports and the gap, under ``free``, under ``shared`` and on the bottom metal."""
    for name, fixture in (("free", {**AUTO_STUBS, "metal": "auto"}), ("shared", {**AUTO_STUBS, "metal": "auto", "metal_rule": "shared"}),
                          ("default", AUTO_STUBS)):
        with pytest.raises(PortError, match=rf"the stubs of ports P2 and CTP on M\d {word} \(gap {gap} um between their outlines, "
                                            r"chamfers included\): one edge would hold both G pins, which EMX refuses"):
            generate(tmp_path / name, "bs", fixture, **{**NARROW, "ct_primary_width_um": width})
        assert not list((tmp_path / name).rglob("*.gds"))


def test_chamfered_stubs_are_judged_by_their_outlines(tmp_path):
    """With ``stub_chamfer_um`` 1 each stub widens by 1 um a side towards the ring. A 3 um secondary opening leaves the tips
    of the 5 um CTP stub (the tap at the winding's width) and the P2 stub 0.5 um apart, and their roots 1.5 um into each
    other. In reference mode (no profile) nothing shortens a chamfer: refused, the gap measured across the roots. With
    the profile N-65 shortens the facing chamfers first (``test_fixture_chamfer.py``), to 0.2 um, leaving M4's minimum
    spacing of 0.1 um at the ring: built. A 5 um opening leaves the roots 0.5 um apart: built, every chamfer 1 um."""
    from ic_opt.em.pcell._pcell_xfm_bs import xfm_bs
    from ic_opt.em.pcell.fixture import GroundFixtureConfig
    from tests.ic_opt.pcell.test_xfm_bs_same_metal_tap import GEOMETRY

    reference = GroundFixtureConfig(**{**AUTO_STUBS, "stub_width_um": 5.0, "stub_chamfer_um": 1.0})
    with pytest.raises(PortError, match=r"the stubs of ports P2 and CTP on M1 overlap \(gap -1.5 um"):
        xfm_bs(**{**GEOMETRY, "OPENING_S": 3.0}, CT_P_ME="6", ground_fixture=reference)
    chamfered = {**AUTO_STUBS, "stub_chamfer_um": 1.0, "metal": "auto"}
    tap = {"ct_primary_metal": "6", "port_order": [*XFM_PORTS, "CTP"]}
    _, _, close = generate(tmp_path / "close", "bs", chamfered, secondary_opening_um=3.0, **tap)
    assert close.fixture_metal == "M4"
    assert close.stub_chamfers_um == {"P1": {"bottom": 1.0, "top": 1.0}, "N1": {"bottom": 1.0, "top": 1.0},
                                      "P2": {"bottom": 0.2, "top": 1.0}, "N2": {"bottom": 1.0, "top": 0.2},
                                      "CTP": {"bottom": 0.2, "top": 0.2}}
    _, _, built = generate(tmp_path / "room", "bs", chamfered, secondary_opening_um=5.0, **tap)
    assert built.fixture_metal == "M4" and built.stub_chamfers_um is None
