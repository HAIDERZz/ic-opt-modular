"""M2.5: the committed parameter reference is exactly what the config models generate."""

from __future__ import annotations

from pathlib import Path

from ic_opt.em.pcell.reference import render

DOC = Path(__file__).resolve().parents[3] / "docs" / "em" / "devices.md"


def test_devices_reference_is_generated_from_the_models():
    assert DOC.read_text(encoding="utf-8") == render(), "regenerate: python -m ic_opt.em.pcell.reference > docs/em/devices.md"


def test_reference_names_every_family_and_retired_field():
    text = render()
    for family in ("clean_port_ind_sym", "clean_port_xfm_bs", "clean_port_xfm_ms", "clean_port_xfm_balun", "clean_port_xfm_tw", "clean_port_xfm_il"):
        assert f"## `{family}`" in text
    assert "`top_metal` → `metal`" in text and "`bottom_metal` (removed)" in text and "`multi_turns` → `secondary_turns`" in text
    assert "|" not in text.split("| `stub_width_by_port_um` | ")[1].split(" |")[0]          # no pipe inside a type cell
