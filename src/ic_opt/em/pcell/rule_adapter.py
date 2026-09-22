from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ic_opt.em.pcell.process_rules import (
    ProcessRuleProfile,
    get_process_rule_profile,
    resolve_conductor,
    resolve_metal_width_space_rule,
    resolve_via,
    resolve_via_primitive_rule,
    resolve_via_stack,
)


@dataclass(frozen=True)
class LayerSpec:
    name: str
    drawing: tuple[int, int]
    pin: tuple[int, int] | None
    emx_name: str
    layer_class: str


@dataclass(frozen=True)
class MetalRuleSpec:
    name: str
    drawing: tuple[int, int]
    pin: tuple[int, int] | None
    emx_name: str
    layer_class: str
    min_width_um: float | None
    max_width_um: float | None
    min_space_um: float | None


@dataclass(frozen=True)
class ViaRuleSpec:
    name: str
    drawing: tuple[int, int]
    emx_name: str
    lower_metal: str
    upper_metal: str
    cut_size_um: tuple[float, float]
    min_cut_space_um: float
    min_enclosure_um: Mapping[str, float]


@dataclass(frozen=True)
class ViaArrayPlan:
    via: str
    lower_metal: str
    upper_metal: str
    cut_size_um: tuple[float, float]
    cut_spacing_um: tuple[float, float]
    center_pitch_um: tuple[float, float]
    rows: int
    columns: int
    array_size_um: tuple[float, float]
    enclosure_um: Mapping[str, float]


@dataclass(frozen=True)
class GeometryRuleAdapter:
    profile: ProcessRuleProfile

    @property
    def process_id(self) -> str:
        return self.profile.process_id

    def layer(self, metal: str) -> LayerSpec:
        conductor = resolve_conductor(self.profile, metal)
        return LayerSpec(
            name=conductor.name,
            drawing=conductor.drawing,
            pin=conductor.pin,
            emx_name=conductor.emx_name,
            layer_class=conductor.layer_class,
        )

    def metal_rule(self, metal: str) -> MetalRuleSpec:
        layer = self.layer(metal)
        rule = resolve_metal_width_space_rule(self.profile, metal)
        return MetalRuleSpec(
            name=layer.name,
            drawing=layer.drawing,
            pin=layer.pin,
            emx_name=layer.emx_name,
            layer_class=layer.layer_class,
            min_width_um=rule.min_width_um,
            max_width_um=rule.max_width_um,
            min_space_um=rule.min_space_um,
        )

    def via(self, via_name: str) -> ViaRuleSpec:
        via = resolve_via(self.profile, via_name)
        primitive = resolve_via_primitive_rule(self.profile, via.name)
        lower_metal, upper_metal = via.connects
        return ViaRuleSpec(
            name=via.name,
            drawing=via.drawing,
            emx_name=via.emx_name,
            lower_metal=lower_metal,
            upper_metal=upper_metal,
            cut_size_um=primitive.cut_size_um,
            min_cut_space_um=primitive.min_cut_space_um,
            min_enclosure_um=MappingProxyType(dict(primitive.min_enclosure_um)),
        )

    def via_between(self, lower_metal: str, upper_metal: str) -> ViaRuleSpec:
        via = resolve_via_stack(self.profile, lower_metal, upper_metal)
        return self.via(via.name)

    def passive_via_restriction(self, via_name: str):
        """Cited passive-region restriction (e.g. IND.R.1) for a via, if any.

        Documentation/citation lookup only -- ``plan_passive_via_array`` no
        longer consults this (n28-rules-slim, user directive 2026-07-19:
        generator enforcement is geometric-only). Kept so the deck citation
        stays queryable (manifest, tooling) without gating generation."""
        for restriction in (
            self.profile.layout_rules.passive_region.via_restrictions.values()
        ):
            if via_name in restriction.applies_to:
                return restriction
        return None

    def plan_passive_via_array(
        self,
        *,
        lower_metal: str,
        upper_metal: str,
        available_width_um: float,
        available_height_um: float,
    ) -> ViaArrayPlan:
        """Plan a centred via-cut array from geometric via rules only.

        Geometric-only enforcement (n28-rules-slim, user directive
        2026-07-19): the five categories that gate generation are line
        width, via cut size, line spacing, via-to-metal-edge enclosure and
        via-to-via spacing -- all sourced from ``via_primitives`` below.
        Neither ``passive_region.via_restrictions`` (e.g. the cited IND.R.1
        deck text) nor ``passive_via_array_coverage``'s ``not_yet_modeled``
        classification fails generation closed any more; both remain in the
        rule profile purely as cited/declared data. When a via *does* carry
        a modeled ``via_array_rules`` entry (currently VIA8/VIA9/RV), its
        min-count/max-spacing legality still applies unchanged. Missing
        geometric data -- an incomplete ``min_enclosure_um`` map, or a via
        with no ``via_primitives`` entry at all -- still fails closed: that
        is a data-availability gap, not a policy restriction.
        """
        if available_width_um <= 0 or available_height_um <= 0:
            raise ValueError("available via array window must be positive")

        via = self.via_between(lower_metal, upper_metal)
        cut_width, cut_height = via.cut_size_um
        spacing = via.min_cut_space_um
        array_rule = self.profile.layout_rules.passive_region.via_array_rules.get(
            via.name
        )
        if array_rule is not None and spacing > array_rule.max_space_um:
            raise ValueError(
                f"{via.name} minimum cut spacing {spacing}um exceeds passive "
                f"maximum spacing {array_rule.max_space_um}um"
            )

        lower_enclosure = via.min_enclosure_um.get(via.lower_metal)
        upper_enclosure = via.min_enclosure_um.get(via.upper_metal)
        if lower_enclosure is None or upper_enclosure is None:
            raise ValueError(f"{via.name} enclosure rules do not cover both metals")
        enclosure = max(lower_enclosure, upper_enclosure)

        usable_width = available_width_um - 2.0 * enclosure
        usable_height = available_height_um - 2.0 * enclosure
        max_columns = math.floor((usable_width + spacing) / (cut_width + spacing))
        max_rows = math.floor((usable_height + spacing) / (cut_height + spacing))
        if max_columns <= 0 or max_rows <= 0:
            raise ValueError(
                f"cannot fit {via.name} passive via array in "
                f"{available_width_um}um x {available_height_um}um window"
            )
        min_count = array_rule.min_count if array_rule is not None else 1
        if max_columns * max_rows < min_count:
            raise ValueError(
                f"cannot fit {via.name} passive via array with at least "
                f"{min_count} cuts in {available_width_um}um x "
                f"{available_height_um}um window"
            )

        array_width = max_columns * cut_width + (max_columns - 1) * spacing
        array_height = max_rows * cut_height + (max_rows - 1) * spacing
        pitch = (cut_width + spacing, cut_height + spacing)
        return ViaArrayPlan(
            via=via.name,
            lower_metal=via.lower_metal,
            upper_metal=via.upper_metal,
            cut_size_um=via.cut_size_um,
            cut_spacing_um=(spacing, spacing),
            center_pitch_um=pitch,
            rows=max_rows,
            columns=max_columns,
            array_size_um=(round(array_width, 6), round(array_height, 6)),
            enclosure_um=MappingProxyType(dict(via.min_enclosure_um)),
        )

    def passive_via_array_coverage(self) -> dict[str, tuple[str, ...]]:
        coverage = self.profile.layout_rules.passive_region.passive_via_array_coverage
        return {
            "modeled": tuple(coverage.modeled),
            "not_yet_modeled": tuple(coverage.not_yet_modeled),
        }

    def manifest(self) -> dict:
        coverage = self.profile.coverage
        return {
            "schema_version": self.profile.schema_version,
            "process_id": self.profile.process_id,
            "units": {"length": self.profile.units.length},
            "coverage": {
                "layer_inventory": coverage.layer_inventory,
                "layout_rules": coverage.layout_rules,
                "metal_width_space": sorted(coverage.metal_width_space),
                "via_primitives": sorted(coverage.via_primitives),
                "passive_via_arrays": sorted(coverage.passive_via_arrays),
                "passive_via_array_coverage": {
                    "modeled": list(
                        self.passive_via_array_coverage()["modeled"]
                    ),
                    "not_yet_modeled": list(
                        self.passive_via_array_coverage()["not_yet_modeled"]
                    ),
                },
                "passive_via_restrictions": sorted(
                    self.profile.layout_rules.passive_region.via_restrictions
                ),
                "emx_via_models": sorted(coverage.emx_via_models),
            },
        }


def get_geometry_rule_adapter(profile_id: str) -> GeometryRuleAdapter:
    return GeometryRuleAdapter(profile=get_process_rule_profile(profile_id))
