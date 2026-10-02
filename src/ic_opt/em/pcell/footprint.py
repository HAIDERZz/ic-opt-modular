"""A drawn device's footprint: the bounding box of what the generator draws for the device -- windings, crossovers,
leads, tap stacks -- without the ground fixture it adds for EMX.

How the fixture is told from the device. Every built-in clean-port family draws the fixture last, and only there, on
the profile's fixture conductor, the bottom metal of its stack (``ProcessRuleProfile.fixture_conductor``: the metal
named M1 unless ``layer_catalog.ground_fixture_conductor`` names another): the ring and one stub per port
(``fixture.add_ground_fixture``) and, with ``pgs``, the shield's strips tied to the ring (``pgs.add_pgs``). The same
families refuse that conductor as a product metal, directly and through a crossunder one or two levels below a winding
(``generator_plugin._forbid_m1_metal`` and its two siblings), so no shape of the device is ever drawn on it. The
footprint is therefore the box around every polygon, box and path on any other layer of the GDS -- the fixture
conductor's pin layer holds only the ground pins' labels, and labels have no extent and do not count.

A generator of another plugin gives no such guarantee: its footprint is refused (``FootprintError``), never a box that
might hold the fixture. So is a profile this machine cannot load (``ProfileUnavailable``): the layer is the profile's.

``footprint`` is ``{"width_um", "height_um", "area_um2"}`` rounded to 0.001: the box's width (x) and height (y) and their
product.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

BUILTIN ="builtin:clean_port"                      # the plugin of the families whose fixture is told apart by its layer
DIGITS = 3                                          # rounding: 0.001 um, 0.001 um^2


class FootprintError(ValueError):
    """No footprint: the fixture cannot be told from the device, or the GDS holds no device shape."""


class ProfileUnavailable(FootprintError):
    """The process profile that names the fixture conductor's layer cannot be loaded on this machine."""


def recorded_fixture_metal(gds: str | Path) -> str | None:
    """The conductor the ground fixture was drawn on, as the manifest beside ``gds`` records it (``geometry.fixture_metal``,
    T19.1); None when there is no manifest or it says nothing (a row of an older generation: the profile's fixture
    conductor, the bottom metal)."""
    manifest = Path(gds).with_name("geometry_manifest.json")
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    metal = (data.get("geometry") or {}).get("fixture_metal") if isinstance(data, dict) else None
    return metal if isinstance(metal, str) and metal else None


def fixture_layer(profile: str, generator: str, plugin: str = BUILTIN, *, metal: str | None = None) -> tuple[int, int]:
    """The (layer, datatype) that holds a device's ground fixture and nothing of the device, for a built-in family: the
    drawing layer of ``metal`` -- the conductor the build recorded (``recorded_fixture_metal``) -- else of the fixture
    conductor of ``profile``, the bottom metal. FootprintError for any other generator; ProfileUnavailable when the
    profile cannot be loaded here."""
    from ic_opt.em.pcell.generator_plugin import PLUGIN_GENERATORS

    if plugin != BUILTIN or generator not in PLUGIN_GENERATORS:
        raise FootprintError(f"generator {generator!r} of {plugin} is not one of the built-in families {sorted(PLUGIN_GENERATORS)}: "
                             "nothing says its fixture conductor holds the ground fixture alone, so its fixture cannot be told "
                             "from its device")
    try:
        from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter

        adapter = get_geometry_rule_adapter(profile)
        return tuple(adapter.layer(metal or adapter.profile.fixture_conductor).drawing)
    except (ValueError, OSError, yaml.YAMLError) as exc:          # an unknown profile, an unreadable or invalid rule.yaml
        raise ProfileUnavailable(f"process profile {profile!r} cannot be loaded here: {exc}") from exc


def measure(gds: str | Path, fixture: tuple[int, int]) -> dict:
    """The footprint of the GDS ``gds``: the box around every shape that is not a label and not on the ``fixture`` layer,
    through every cell of every top cell. FootprintError when nothing is left."""
    import klayout.db as kdb

    layout = kdb.Layout()
    layout.read(str(gds))
    box = kdb.Box()
    skip = tuple(fixture)
    for top in layout.top_cells():
        for index in layout.layer_indexes():
            info = layout.get_info(index)
            if (info.layer, info.datatype) == skip:
                continue
            shapes = top.begin_shapes_rec(index)
            while not shapes.at_end():
                shape = shapes.shape()
                if not shape.is_text():
                    box += shape.bbox().transformed(shapes.trans())
                shapes.next()
    if box.empty():
        raise FootprintError(f"{Path(gds).name} has no shape outside the fixture conductor's layer {skip[0]}/{skip[1]}")
    width, height = box.width() * layout.dbu, box.height() * layout.dbu
    return {"width_um": round(width, DIGITS), "height_um": round(height, DIGITS), "area_um2": round(width * height, DIGITS)}


def footprint(gds: str | Path, *, profile: str, generator: str, plugin: str = BUILTIN) -> dict:
    """The footprint of a GDS that ``generator`` (of ``plugin``) drew on ``profile``: ``measure`` without the fixture's
    layer (``fixture_layer``: the conductor the manifest beside the GDS records, else the fixture conductor).
    FootprintError (ProfileUnavailable) when it cannot be told apart."""
    return measure(gds, fixture_layer(profile, generator, plugin, metal=recorded_fixture_metal(gds)))
