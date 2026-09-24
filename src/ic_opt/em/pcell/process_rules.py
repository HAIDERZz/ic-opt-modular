from __future__ import annotations

import os
from collections.abc import Sequence
from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ic_opt.em.pcell.stack import REFERENCE_GRID_UM

GDS_DBU_UM = 0.001                                   # the database unit the generators write their GDS in


class Units(BaseModel):
    model_config = ConfigDict(extra="forbid")

    length: str


class RuleCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_inventory: str
    layout_rules: str
    metal_width_space: tuple[str, ...]
    via_primitives: tuple[str, ...]
    passive_via_arrays: tuple[str, ...]
    emx_via_models: tuple[str, ...]

    @field_validator("layer_inventory")
    @classmethod
    def _layer_inventory_scope_supported(cls, value: str) -> str:
        if value != "full_known_inventory":
            raise ValueError("unsupported layer inventory coverage scope")
        return value

    @field_validator("layout_rules")
    @classmethod
    def _layout_rule_scope_supported(cls, value: str) -> str:
        if value != "passive_generator_core_rules":
            raise ValueError("unsupported layout rule coverage scope")
        return value


class ConductorRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    drawing: tuple[int, int]
    pin: tuple[int, int] | None = None
    emx_name: str
    layer_class: str = Field(alias="class")


class ViaRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    drawing: tuple[int, int]
    emx_name: str
    connects: tuple[str, str]

    @field_validator("connects")
    @classmethod
    def _connects_exactly_two(cls, value: tuple[str, str]) -> tuple[str, str]:
        if len(value) != 2:
            raise ValueError("via connects must list exactly two conductors")
        if value[0] == value[1]:
            raise ValueError("via connects must reference two different conductors")
        return value


class MarkerRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    drawing: tuple[int, int]
    purpose: str


class LayerCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conductors: dict[str, ConductorRule]
    vias: dict[str, ViaRule]
    markers: dict[str, MarkerRule]
    ground_fixture_conductor: str | None = None  # the bottom metal, which carries the ground ring and stubs; default: the metal named M1


class EmxConductorStackRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thickness_um: float


class EmxViaModelRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    via: str
    emx_effective_size_um: float


class EmxStack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    geometry_scaling: float
    conductors: dict[str, EmxConductorStackRule]
    via_models: dict[str, EmxViaModelRule]


class MetalWidthSpaceRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_width_um: float | None = None
    max_width_um: float | None = None
    min_space_um: float | None = None


class ViaPrimitiveRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cut_size_um: tuple[float, float]
    min_cut_space_um: float
    min_enclosure_um: dict[str, float]


class PassiveViaArrayRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_count: int
    max_space_um: float


class ParallelSpacingRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metals: tuple[str, ...]
    when_width_gt_um: float
    when_parallel_length_gt_um: float
    min_space_um: float


class PassiveViaArrayCoverage(BaseModel):
    """Declared rule-coverage partition over the via catalog.

    ``not_yet_modeled`` means no passive via array rule has been
    transcribed from the DRC deck yet -- a rule coverage gap, NOT a DRC
    prohibition. Via existence itself is declared by
    ``layer_catalog.vias[*].connects`` and the EMX/PDK stack.
    """

    model_config = ConfigDict(extra="forbid")

    modeled: tuple[str, ...]
    not_yet_modeled: tuple[str, ...]

    @model_validator(mode="after")
    def _partition_is_disjoint(self) -> PassiveViaArrayCoverage:
        overlap = set(self.modeled) & set(self.not_yet_modeled)
        if overlap:
            raise ValueError(
                "vias classified twice in passive_via_array_coverage: "
                f"{sorted(overlap)}"
            )
        return self


class ViaRestrictionException(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vias: tuple[str, ...]
    marker: str
    band_um: float
    implemented_by_generator: bool = False

    @field_validator("band_um")
    @classmethod
    def _band_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("exception band_um must be positive")
        return value


class PassiveViaRestriction(BaseModel):
    """Cited passive-region DRC restriction on via usage.

    Unlike the retired uncited ban, every instance names its deck rule id
    (dict key), quotes the deck text and states the exception precisely.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    applies_to: tuple[str, ...]
    scope: str
    exception: ViaRestrictionException | None = None
    source_text: str
    class_mapping_note: str = ""


class PassiveMetalRestriction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    note: str
    source_text: str


class PassiveRegionRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marker: str
    passive_via_array_coverage: PassiveViaArrayCoverage
    via_restrictions: dict[str, PassiveViaRestriction] = Field(default_factory=dict)
    metal_restrictions: dict[str, PassiveMetalRestriction] = Field(
        default_factory=dict
    )
    via_array_rules: dict[str, PassiveViaArrayRule]
    wide_parallel_spacing: tuple[ParallelSpacingRule, ...]


class LayoutRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metal_width_space: dict[str, MetalWidthSpaceRule]
    via_primitives: dict[str, ViaPrimitiveRule]
    passive_region: PassiveRegionRules
    # The vias whose cut enclosure the DRC audit checks (``ProcessRuleProfile.audited_vias``); left out: every via of
    # the metal stack. A list, empty included, is the author's explicit choice.
    audited_vias: tuple[str, ...] | None = None
    # The grid every drawn coordinate the pcell quantizes lands on (``stack.grid_um`` inside a build, T16 R-23).
    manufacturing_grid_um: float = Field(default=REFERENCE_GRID_UM, gt=0)

    @field_validator("manufacturing_grid_um")
    @classmethod
    def _grid_is_whole_nanometres(cls, value: float) -> float:
        steps = value / GDS_DBU_UM
        if abs(steps - round(steps)) > 1e-6:
            raise ValueError(f"manufacturing_grid_um {value} is not a whole number of GDS database units "
                             f"({GDS_DBU_UM} um): the generators write integer-nanometre coordinates")
        return value


class ProcessRuleProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    process_id: str
    units: Units
    coverage: RuleCoverage
    layer_catalog: LayerCatalog
    emx_stack: EmxStack
    layout_rules: LayoutRules

    @field_validator("schema_version")
    @classmethod
    def _schema_version_supported(cls, value: str) -> str:
        if value != "process-rule-profile-v1":
            raise ValueError("unsupported process rule profile schema_version")
        return value

    @property
    def metal_stack(self) -> tuple[str, ...]:
        """The metals the generators draw on, bottom first (T13.11). Metals are the catalog conductors with a width /
        space rule (diffusion, poly and the like are conductors too, but no device is drawn on them); their order is
        the via chain -- every via between two metals joins neighbours -- walked up from the ground-fixture conductor
        (``layer_catalog.ground_fixture_conductor``, default the metal named M1). Key order in the file does not
        matter. Generators address a metal by its position here (1 = the fixture layer), so any number of metals
        with any names works. Raises ValueError when the vias do not chain every metal into one column."""
        return _metal_chain(self)

    @property
    def fixture_conductor(self) -> str:
        return self.metal_stack[0]

    @property
    def audited_vias(self) -> tuple[str, ...]:
        """The vias whose cut enclosure the DRC audit checks (``drc_audit.audit_gds``): ``layout_rules.audited_vias``
        when the profile lists them, else every via of the metal stack -- the via joining each metal to the next,
        bottom first -- so no via a device can draw is left out because of its name (T16 R-13)."""
        declared = self.layout_rules.audited_vias
        if declared is not None:
            return tuple(declared)
        stack = self.metal_stack
        joins = {frozenset(via.connects): name for name, via in self.layer_catalog.vias.items()}
        return tuple(joins[frozenset(pair)] for pair in zip(stack, stack[1:]))

    @model_validator(mode="after")
    def _coverage_matches_declared_rules(self) -> ProcessRuleProfile:
        if set(self.coverage.metal_width_space) != set(
            self.layout_rules.metal_width_space
        ):
            raise ValueError("coverage.metal_width_space must match layout rules")
        if set(self.coverage.via_primitives) != set(self.layout_rules.via_primitives):
            raise ValueError("coverage.via_primitives must match layout rules")
        if set(self.coverage.passive_via_arrays) != set(
            self.layout_rules.passive_region.via_array_rules
        ):
            raise ValueError("coverage.passive_via_arrays must match layout rules")
        if set(self.coverage.emx_via_models) != set(self.emx_stack.via_models):
            raise ValueError("coverage.emx_via_models must match EMX stack rules")
        passive = self.layout_rules.passive_region
        coverage_decl = passive.passive_via_array_coverage
        if set(coverage_decl.modeled) != set(passive.via_array_rules):
            raise ValueError(
                "passive_via_array_coverage.modeled must match "
                "passive_region.via_array_rules"
            )
        classified = set(coverage_decl.modeled) | set(coverage_decl.not_yet_modeled)
        if classified != set(self.layer_catalog.vias):
            raise ValueError(
                "every via in layer_catalog.vias must be classified in "
                "passive_via_array_coverage (modeled or not_yet_modeled)"
            )
        for rule_id, restriction in passive.via_restrictions.items():
            unknown = set(restriction.applies_to) - set(self.layer_catalog.vias)
            if not restriction.applies_to or unknown:
                raise ValueError(
                    f"{rule_id}: restriction applies_to lists unknown via(s) "
                    f"{sorted(unknown)}"
                )
            if restriction.exception is not None:
                stray = set(restriction.exception.vias) - set(restriction.applies_to)
                if stray:
                    raise ValueError(
                        f"{rule_id}: exception vias {sorted(stray)} are not "
                        "in applies_to"
                    )
                if restriction.exception.marker not in self.layer_catalog.markers:
                    raise ValueError(
                        f"{rule_id}: exception marker "
                        f"{restriction.exception.marker} is not in the "
                        "marker catalog"
                    )
        return self

    @model_validator(mode="after")
    def _metal_stack_is_a_via_chain(self) -> ProcessRuleProfile:
        _metal_chain(self)
        return self

    @model_validator(mode="after")
    def _audited_vias_can_be_audited(self) -> ProcessRuleProfile:
        """Every via ``layout_rules.audited_vias`` lists is a catalog via whose primitive rule gives the enclosure on
        both metals it joins: the audit has a rule to apply to each, or the profile is refused here."""
        declared = self.layout_rules.audited_vias
        if declared is None:
            return self
        for i, name in enumerate(declared):
            if name in declared[:i]:
                raise ValueError(f"layout_rules.audited_vias lists {name} twice")
            via = self.layer_catalog.vias.get(name)
            if via is None:
                raise ValueError(f"layout_rules.audited_vias names {name}, which is not in layer_catalog.vias")
            primitive = self.layout_rules.via_primitives.get(name)
            if primitive is None:
                raise ValueError(f"layout_rules.audited_vias names {name}, which has no layout_rules.via_primitives "
                                 "entry: the audit has no enclosure rule to apply")
            missing = [c for c in via.connects if c not in primitive.min_enclosure_um]
            if missing:
                raise ValueError(f"layout_rules.audited_vias names {name}, whose via_primitives.{name}.min_enclosure_um "
                                 f"has no entry for {missing}")
        return self


def _metal_chain(profile: ProcessRuleProfile) -> tuple[str, ...]:
    for name, via in profile.layer_catalog.vias.items():
        unknown = [c for c in via.connects if c not in profile.layer_catalog.conductors]
        if unknown:
            raise ValueError(f"layer_catalog.vias.{name}.connects references unknown conductor(s) {unknown}")
    metals = [name for name in profile.layer_catalog.conductors if name in profile.layout_rules.metal_width_space]
    if not metals:
        raise ValueError("no layer_catalog conductor has a layout_rules.metal_width_space rule: the metal stack is empty")
    declared = profile.layer_catalog.ground_fixture_conductor
    if declared is not None:
        if declared not in metals:
            raise ValueError(f"layer_catalog.ground_fixture_conductor {declared!r} is not a metal (a conductor with a width rule)")
        bottom = declared
    else:
        named = [m for m in metals if m.upper() == "M1"]
        if not named:
            raise ValueError("no metal is named M1: declare layer_catalog.ground_fixture_conductor, the bottom metal the "
                             "ground fixture is drawn on")
        bottom = named[0]
    neighbours: dict[str, list[str]] = {m: [] for m in metals}
    for via in profile.layer_catalog.vias.values():
        a, b = via.connects
        if a in neighbours and b in neighbours:
            neighbours[a].append(b)
            neighbours[b].append(a)
    chain, previous = [bottom], None
    while True:
        onward = [m for m in neighbours[chain[-1]] if m != previous]
        if len(onward) > 1 or (len(chain) == 1 and len(neighbours[bottom]) > 1):
            raise ValueError(f"the vias join {chain[-1]} to {sorted(set(neighbours[chain[-1]]))}: metals must form one column, "
                             f"each via joining a metal to the next (from the fixture metal {bottom})")
        if not onward:
            break
        previous = chain[-1]
        if onward[0] in chain:
            raise ValueError(f"the vias close a loop at {onward[0]}: metals must form one column")
        chain.append(onward[0])
    missing = [m for m in metals if m not in chain]
    if missing:
        raise ValueError(f"metals {missing} are not joined to the via chain from {bottom} ({' - '.join(chain)}): "
                         "every metal needs the via to its neighbour below")
    return tuple(chain)


PROFILE_DIRS_ENV_VAR = "IC_OPT_PROFILE_DIRS"


def _external_profile_dirs() -> list[Path]:
    raw = os.environ.get(PROFILE_DIRS_ENV_VAR, "")
    return [Path(part).expanduser() for part in raw.split(os.pathsep) if part]


def _profile_path(profile_id: str, extra_dirs: Sequence[Path] = ()) -> Path:
    # Generic: resolve any <id>/rule.yaml, searching ``extra_dirs`` (from a
    # caller such as em.validate_profile, which passes its profile directory's
    # parent), then external directories from IC_OPT_PROFILE_DIRS
    # (os.pathsep-separated, in order), then the packaged resources dir.
    # Real-process (NDA-bound) profiles live only under the external dirs and
    # are never packaged; the packaged dir holds the fictitious demo_6m.
    for directory in [*extra_dirs, *_external_profile_dirs()]:
        candidate = directory / profile_id / "rule.yaml"
        if candidate.is_file():
            return candidate

    path = Path(resources.files("ic_opt.em.pcell").joinpath(f"profiles/{profile_id}/rule.yaml"))
    if path.is_file():
        return path

    raise ValueError(
        f"unsupported process rule profile: {profile_id} (searched "
        f"{PROFILE_DIRS_ENV_VAR} directories and packaged resources; set "
        f"{PROFILE_DIRS_ENV_VAR} to a directory containing "
        f"{profile_id}/rule.yaml)"
    )


def _with_names(profile: ProcessRuleProfile) -> ProcessRuleProfile:
    for name, rule in profile.layer_catalog.conductors.items():
        rule.name = name
    for name, rule in profile.layer_catalog.vias.items():
        rule.name = name
    for name, rule in profile.layer_catalog.markers.items():
        rule.name = name
    for name, rule in profile.layout_rules.passive_region.via_restrictions.items():
        rule.name = name
    for name, rule in profile.layout_rules.passive_region.metal_restrictions.items():
        rule.name = name
    return profile


def get_process_rule_profile(
    profile_id: str, *, extra_dirs: Sequence[Path] = ()
) -> ProcessRuleProfile:
    profile_path = _profile_path(profile_id, extra_dirs)
    data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    return _with_names(ProcessRuleProfile.model_validate(data))


def resolve_conductor(profile: ProcessRuleProfile, name: str) -> ConductorRule:
    try:
        return profile.layer_catalog.conductors[name]
    except KeyError as exc:
        raise ValueError(
            f"unknown conductor {name} for process {profile.process_id}"
        ) from exc


def resolve_via(profile: ProcessRuleProfile, name: str) -> ViaRule:
    try:
        return profile.layer_catalog.vias[name]
    except KeyError as exc:
        raise ValueError(f"unknown via {name} for process {profile.process_id}") from exc


def resolve_marker(profile: ProcessRuleProfile, name: str) -> MarkerRule:
    try:
        return profile.layer_catalog.markers[name]
    except KeyError as exc:
        raise ValueError(f"unknown marker {name} for process {profile.process_id}") from exc


def resolve_via_stack(profile: ProcessRuleProfile, lower: str, upper: str) -> ViaRule:
    requested = {lower, upper}
    for via in profile.layer_catalog.vias.values():
        if set(via.connects) == requested:
            return via
    raise ValueError(
        f"no via stack connects {lower} to {upper} for process {profile.process_id}"
    )


def resolve_metal_width_space_rule(
    profile: ProcessRuleProfile, metal: str
) -> MetalWidthSpaceRule:
    try:
        return profile.layout_rules.metal_width_space[metal]
    except KeyError as exc:
        raise ValueError(
            f"no metal width/space rule for {metal} in process {profile.process_id}"
        ) from exc


def resolve_via_primitive_rule(
    profile: ProcessRuleProfile, via: str
) -> ViaPrimitiveRule:
    try:
        return profile.layout_rules.via_primitives[via]
    except KeyError as exc:
        raise ValueError(
            f"no via primitive rule for {via} in process {profile.process_id}"
        ) from exc


def resolve_passive_via_array_rule(
    profile: ProcessRuleProfile, via: str
) -> PassiveViaArrayRule:
    try:
        return profile.layout_rules.passive_region.via_array_rules[via]
    except KeyError as exc:
        raise ValueError(
            f"no passive via array coverage for {via} in process "
            f"{profile.process_id} (rule coverage gap, not a DRC prohibition)"
        ) from exc
