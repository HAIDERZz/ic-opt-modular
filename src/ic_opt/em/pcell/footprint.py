"""A drawn device's footprint: the bounding box of what the generator draws for the device -- windings, crossovers,
leads, tap stacks -- without the ground fixture it adds for EMX.

How the fixture is told from the device. Every built-in clean-port family draws the fixture last
(``fixture.add_ground_fixture``), and that function takes the box of everything drawn so far -- the device -- before it
draws the ring and one stub per port; the build records it in the manifest beside the GDS (``geometry.device_bbox_um``,
T19.2). The footprint is that box, read from the manifest without opening the GDS (``recorded_device_bbox``). It is the
only reading when the fixture shares its metal with the device's internal shapes (``metal_rule: shared`` in
``ground_fixture``): the fixture's layer then holds device shapes too.

A row of an older generation has no such record, and its footprint is measured by layer as before: the fixture is the
one thing drawn on its conductor -- the profile's fixture conductor, the bottom metal of its stack
(``ProcessRuleProfile.fixture_conductor``: the metal named M1 unless ``layer_catalog.ground_fixture_conductor`` names
another), or the metal the manifest records (``geometry.fixture_metal``, T19.1). The ring, the stubs and, with ``pgs``,
the shield's strips tied to the ring (``pgs.add_pgs``) are drawn there, and the families refuse that conductor as a
product metal, directly and through a crossunder one or two levels below a winding (``generator_plugin._forbid_m1_metal``
and its two siblings; a moved fixture's metal is one the device draws nothing on, under T19.1's rule). That footprint is
the box around every polygon, box and path on any other layer of the GDS -- the fixture metal's pin layer holds only
the ground pins' labels, and labels have no extent and do not count.

A generator of another plugin gives no such guarantee: its footprint is refused (``FootprintError``), never a box that
might hold the fixture. So is a profile this machine cannot load (``ProfileUnavailable``) when the footprint has to be
measured by layer: the layer is the profile's.

``footprint`` is ``{"width_um", "height_um", "area_um2"}`` rounded to 0.001: the box's width (x) and height (y) and their
product.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

BUILTIN ="builtin:clean_port"                      # the plugin of the families whose fixture is told from the device
DIGITS = 3                                          # rounding: 0.001 um, 0.001 um^2
NM_UM = 0.001                                       # one nanometre: the generators' database unit, the record's resolution


class FootprintError(ValueError):
    """No footprint: the fixture cannot be told from the device, or the GDS holds no device shape."""


class ProfileUnavailable(FootprintError):
    """The process profile that names the fixture conductor's layer cannot be loaded on this machine."""


def _recorded_geometry(gds: str | Path) -> dict:
    """The ``geometry`` block of the manifest beside ``gds``; empty when there is no manifest or it cannot be read."""
    manifest = Path(gds).with_name("geometry_manifest.json")
    if not manifest.is_file():
        return {}
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    geometry = data.get("geometry") if isinstance(data, dict) else None
    return geometry if isinstance(geometry, dict) else {}


def recorded_fixture_metal(gds: str | Path) -> str | None:
    """The conductor the ground fixture was drawn on, as the manifest beside ``gds`` records it (``geometry.fixture_metal``,
    T19.1); None when there is no manifest or it says nothing (a row of an older generation: the profile's fixture
    conductor, the bottom metal)."""
    metal = _recorded_geometry(gds).get("fixture_metal")
    return metal if isinstance(metal, str) and metal else None


def recorded_device_bbox(gds: str | Path) -> tuple[float, float, float, float] | None:
    """The box (x0, y0, x1, y1, um) around everything the device drew, as the manifest beside ``gds`` records it
    (``geometry.device_bbox_um``, T19.2: taken before the ground fixture was drawn); None when there is no manifest, it
    records no box (a row of an older generation) or not four numbers with x0 <= x1 and y0 <= y1."""
    box = _recorded_geometry(gds).get("device_bbox_um")
    if not (isinstance(box, list) and len(box) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box)):
        return None
    x0, y0, x1, y1 = (float(v) for v in box)
    return (x0, y0, x1, y1) if x0 <= x1 and y0 <= y1 else None


def box_footprint(box: tuple[float, float, float, float]) -> dict:
    """The footprint of a recorded box (x0, y0, x1, y1, um): its width, height and area, computed from whole nanometres
    as ``measure`` computes them from the GDS's database units, so the record and the measurement of the same drawing
    agree to the last digit."""
    x0, y0, x1, y1 = (round(v / NM_UM) for v in box)
    width, height = (x1 - x0) * NM_UM, (y1 - y0) * NM_UM
    return {"width_um": round(width, DIGITS), "height_um": round(height, DIGITS), "area_um2": round(width * height, DIGITS)}


def _require_builtin(generator: str, plugin: str) -> None:
    from ic_opt.em.pcell.generator_plugin import PLUGIN_GENERATORS

    if plugin != BUILTIN or generator not in PLUGIN_GENERATORS:
        raise FootprintError(f"generator {generator!r} of {plugin} is not one of the built-in families {sorted(PLUGIN_GENERATORS)}: "
                             "nothing says its fixture conductor holds the ground fixture alone, so its fixture cannot be told "
                             "from its device")


def fixture_layer(profile: str, generator: str, plugin: str = BUILTIN, *, metal: str | None = None) -> tuple[int, int]:
    """The (layer, datatype) that holds a device's ground fixture and nothing of the device, for a built-in family: the
    drawing layer of ``metal`` -- the conductor the build recorded (``recorded_fixture_metal``) -- else of the fixture
    conductor of ``profile``, the bottom metal. FootprintError for any other generator; ProfileUnavailable when the
    profile cannot be loaded here."""
    _require_builtin(generator, plugin)
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
    """The footprint of a GDS that ``generator`` (of ``plugin``) drew on ``profile``: the device's box the manifest beside
    the GDS records (``recorded_device_bbox``, T19.2; the GDS is not opened), else -- a row of an older generation --
    ``measure`` without the fixture's layer (``fixture_layer``: the conductor the manifest records, else the fixture
    conductor). FootprintError for another plugin's generator, record or not; ProfileUnavailable when the footprint has
    to be measured and the profile cannot be loaded here."""
    _require_builtin(generator, plugin)
    box = recorded_device_bbox(gds)
    if box is not None:
        return box_footprint(box)
    return measure(gds, fixture_layer(profile, generator, plugin, metal=recorded_fixture_metal(gds)))
