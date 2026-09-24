"""Authoring-time validation for process rule profiles (validate-profile).

The runtime loader (``geometry/process_rules.py``) is the fail-closed gate
the optimizer trips over when a profile is wrong; this module is its
authoring-side counterpart:

* schema errors are rendered one per line as ``<yaml.path>: <message>`` so
  an authoring agent can jump straight to the offending key;
* referential checks the runtime schema does not make (it only cross-checks
  what generation consumes) run as a separate "consistency" stage --
  emx_stack/catalog key agreement, via ``connects`` membership, the passive
  marker, enclosure keys and wide-parallel metals;
* optionally, every catalog ``emx_name`` is checked against a site EMX proc
  file, token-wise -- the proc is the naming authority EMX itself reads, so
  a name absent there would only fail much later, at simulation time -- and
  every conductor's and via's drawing layer, and every conductor's pin layer,
  against the proc's ``define`` statements (``define M1 = fill(l61t0+...,
  ...)`` on demo_6m): a GDS layer the proc does not map is geometry EMX
  silently ignores,
  and EMX looks for a conductor's port labels only on the layers its define
  names (EMX User Manual, Ports), which is where the pcell writes them.

Generation smoke (build one canonical device per family and DRC-audit it)
is layered on top by the CLI's ``--generate`` flag; see
``validate_profile``'s callers.
"""
from __future__ import annotations

import contextlib
import os
import re
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from ic_opt.em.pcell.proc_file import conductor_thicknesses, stack_mismatches
from ic_opt.em.pcell.process_rules import (
    PROFILE_DIRS_ENV_VAR,
    ProcessRuleProfile,
    _profile_path,
    _with_names,
)

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: Generation-smoke families, in report order. Values: the minimum metal
#: stack index the family's canonical device needs for its top metal --
#: ms/balun/bs draw an implicit crossunder one level below (M1/M2
#: forbidden), il's crossunder leg2 sits TWO levels below (M1..M3
#: forbidden), so a too-shallow stack is an honest SKIP, not a failure.
GENERATION_FAMILIES: dict[str, int] = {
    "clean_port_ind_sym": 3,
    "clean_port_xfm_bs": 3,
    "clean_port_xfm_ms": 3,
    "clean_port_xfm_balun": 3,
    "clean_port_xfm_tw": 3,
    "clean_port_xfm_il": 4,
}

#: Ground fixture used by every canonical smoke device: the constants the
#: reference geometry sweep campaigns ran with (stub width follows the winding
#: width per device below).
_SMOKE_FIXTURE = {
    "inner_margin_um": 15.0,
    "ring_width_um": 50.0,
    "stub_length_um": 2.0,
    "stub_chamfer_um": 0.0,
}


@dataclass
class StageResult:
    name: str
    status: str  # "PASS" | "FAIL" | "SKIPPED"
    details: list[str] = field(default_factory=list)


@dataclass
class ProfileValidationReport:
    profile_id: str
    profile_path: Path | None
    stages: list[StageResult]

    @property
    def passed(self) -> bool:
        return all(stage.status != "FAIL" for stage in self.stages)

    @property
    def ok(self) -> bool:
        """``passed`` under the report convention the CLI's ``call`` exits on (as ``env.doctor``)."""
        return self.passed

    def __str__(self) -> str:
        return self.format()

    def format(self) -> str:
        header = f"profile: {self.profile_id}"
        if self.profile_path is not None:
            header += f" ({self.profile_path})"
        lines = [header]
        for stage in self.stages:
            lines.append(f"[{stage.name}] {stage.status}")
            for detail in stage.details:
                lines.append(f"  - {detail}")
        lines.append(f"result: {'PASS' if self.passed else 'FAIL'}")
        return "\n".join(lines)


def _render_validation_error(exc: ValidationError) -> list[str]:
    details = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "(profile)"
        details.append(f"{loc}: {err['msg']}")
    return details


def _shared_layer_errors(catalog) -> list[str]:
    """A GDS layer claimed twice is a transcription slip, except a conductor's pin on its own drawing layer and vias
    that share a cut layer while landing on a common conductor (one contact layer from diffusion and poly to M1)."""
    claims: dict[tuple[int, int], list[tuple[str, str, object]]] = {}
    for name, rule in catalog.conductors.items():
        claims.setdefault(tuple(rule.drawing), []).append(("conductors", name, rule))
        if rule.pin is not None and tuple(rule.pin) != tuple(rule.drawing):
            claims.setdefault(tuple(rule.pin), []).append(("conductors", f"{name} (pin)", rule))
    for name, rule in catalog.vias.items():
        claims.setdefault(tuple(rule.drawing), []).append(("vias", name, rule))
    for name, rule in catalog.markers.items():
        claims.setdefault(tuple(rule.drawing), []).append(("markers", name, rule))
    out = []
    for layer, users in sorted(claims.items()):
        if len(users) < 2:
            continue
        vias = [rule for kind, _, rule in users if kind == "vias"]
        if len(vias) == len(users) and all(set(a.connects) & set(b.connects) for a in vias for b in vias):
            continue
        out.append(f"GDS layer {layer[0]}/{layer[1]} is claimed by " + ", ".join(f"layer_catalog.{k}.{n}" for k, n, _ in users))
    return out


def _consistency_stage(
    profile: ProcessRuleProfile, profile_id: str
) -> StageResult:
    errors: list[str] = []
    warns: list[str] = []
    catalog = profile.layer_catalog
    conductors = set(catalog.conductors)
    vias = set(catalog.vias)

    stray = set(profile.emx_stack.conductors) - conductors
    if stray:
        errors.append(
            "emx_stack.conductors not in layer_catalog.conductors: "
            f"{sorted(stray)}"
        )
    errors += _shared_layer_errors(catalog)
    for name, via in catalog.vias.items():
        unknown = set(via.connects) - conductors
        if unknown:
            errors.append(
                f"layer_catalog.vias.{name}.connects references unknown "
                f"conductor(s) {sorted(unknown)}"
            )
    for name, model in profile.emx_stack.via_models.items():
        if model.via not in vias:
            errors.append(
                f"emx_stack.via_models.{name}.via references unknown via "
                f"{model.via!r}"
            )
    passive = profile.layout_rules.passive_region
    if passive.marker not in catalog.markers:
        errors.append(
            f"layout_rules.passive_region.marker {passive.marker!r} is not "
            "in layer_catalog.markers"
        )
    for metal in profile.layout_rules.metal_width_space:
        if metal not in conductors:
            errors.append(
                f"layout_rules.metal_width_space.{metal}: unknown conductor"
            )
    for via_name, primitive in profile.layout_rules.via_primitives.items():
        if via_name not in vias:
            errors.append(
                f"layout_rules.via_primitives.{via_name}: unknown via"
            )
        unknown = set(primitive.min_enclosure_um) - conductors
        if unknown:
            errors.append(
                f"layout_rules.via_primitives.{via_name}.min_enclosure_um "
                f"references unknown conductor(s) {sorted(unknown)}"
            )
        via_rule = catalog.vias.get(via_name)
        if via_rule is not None:
            missing = set(via_rule.connects) - set(primitive.min_enclosure_um)
            if missing:
                warns.append(
                    f"warn: via_primitives.{via_name}.min_enclosure_um has "
                    f"no entry for connected conductor(s) {sorted(missing)}"
                )
    for rule in passive.wide_parallel_spacing:
        unknown = set(rule.metals) - conductors
        if unknown:
            errors.append(
                "layout_rules.passive_region.wide_parallel_spacing "
                f"references unknown conductor(s) {sorted(unknown)}"
            )
    if profile.process_id != profile_id:
        warns.append(
            f"warn: process_id {profile.process_id!r} != profile folder id "
            f"{profile_id!r} (requirement.md must use the folder id)"
        )
    if profile.units.length != "um":
        warns.append(
            f"warn: units.length {profile.units.length!r} -- the geometry "
            "code assumes micrometres ('um')"
        )
    return StageResult(
        "consistency", "FAIL" if errors else "PASS", errors + warns
    )


def _proc_stage(
    profile: ProcessRuleProfile, proc_path: Path | None
) -> StageResult:
    if proc_path is None:
        return StageResult("emx-names-vs-proc", "SKIPPED", ["no --proc given"])
    try:
        text = proc_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return StageResult(
            "emx-names-vs-proc", "FAIL", [f"cannot read proc file: {exc}"]
        )
    tokens = _proc_tokens(text)
    catalog = profile.layer_catalog
    wanted = {rule.emx_name for rule in catalog.conductors.values()}
    wanted |= {rule.emx_name for rule in catalog.vias.values()}
    missing = sorted(wanted - tokens)
    if missing:
        return StageResult(
            "emx-names-vs-proc",
            "FAIL",
            [
                f"emx_name not found in {proc_path.name}: {name}"
                for name in missing
            ],
        )
    # M2.3: names agreeing is not the stack agreeing -- compare the conductor thicknesses numerically
    from ic_opt.em.pcell.rule_adapter import GeometryRuleAdapter

    stack = GeometryRuleAdapter(profile).stack_summary()["conductors"]
    mismatches = stack_mismatches(stack, conductor_thicknesses(text))
    if mismatches:
        return StageResult("emx-names-vs-proc", "FAIL", [f"emx_stack vs {proc_path.name}: {m}" for m in mismatches])
    return StageResult(
        "emx-names-vs-proc",
        "PASS",
        [f"{len(wanted)} emx_names found in {proc_path.name}; {len(stack)} conductor thicknesses agree"],
    )


def _proc_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("*"):
            continue
        tokens.update(_TOKEN_RE.findall(line.split("#", 1)[0]))
    return tokens


_DEFINE_RE = re.compile(r"^\s*define\s+(\w+)\s*=\s*(.+)$")
_GDS_REF_RE = re.compile(r"\bl(\d+)t(\d+)\b")


def proc_layer_map(text: str) -> dict[str, set[tuple[int, int]]]:
    """``{define name: GDS (layer, datatype) set}`` of a proc -- ``l<L>t<D>`` terms, through defines that name other defines
    (``define via7 = via7raw-cbm-ctm``; a subtracted operand's layers count too, which only ever widens the set)."""
    raw: dict[str, str] = {}
    for line in text.splitlines():
        m = _DEFINE_RE.match(line.split("#", 1)[0])
        if m:
            raw[m.group(1)] = m.group(2)
    resolved: dict[str, set[tuple[int, int]]] = {}

    def layers(name: str, trail: frozenset[str]) -> set[tuple[int, int]]:
        if name not in resolved:
            out = {(int(a), int(b)) for a, b in _GDS_REF_RE.findall(raw[name])}
            for token in _TOKEN_RE.findall(raw[name]):
                if token in raw and token not in trail:
                    out |= layers(token, trail | {token})
            resolved[name] = out
        return resolved[name]

    return {name: layers(name, frozenset({name})) for name in raw}


def _gds_layers_stage(profile: ProcessRuleProfile, proc_path: Path | None) -> StageResult:
    """Every conductor / via drawing layer and every conductor pin layer is in the proc's define of its emx_name
    (the GDS -> EMX layer map; the pin layer carries the port labels). ``nolabels(...)`` in a define is not modeled."""
    name = "gds-layers-vs-proc"
    if proc_path is None:
        return StageResult(name, "SKIPPED", ["no --proc given"])
    try:
        text = Path(proc_path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return StageResult(name, "FAIL", [f"cannot read proc file: {exc}"])
    defines = proc_layer_map(text)
    if not defines:
        return StageResult(name, "SKIPPED", [f"{Path(proc_path).name} has no define statements; its GDS layer map is not checked"])
    tokens = _proc_tokens(text)
    catalog = profile.layer_catalog
    rules = [(n, r.emx_name, r.drawing, r.pin) for n, r in catalog.conductors.items()]
    rules += [(n, r.emx_name, r.drawing, None) for n, r in catalog.vias.items()]
    problems, notes = [], []
    for layer, emx_name, drawing, pin in rules:
        mapped = defines.get(emx_name)
        if mapped is None:
            if emx_name in tokens:                      # a name absent from the proc altogether is the emx-names stage's finding
                problems.append(f"{layer}: {emx_name} has no 'define {emx_name} = ...' in {Path(proc_path).name}")
        elif not mapped:
            notes.append(f"warn: {layer}: the define of {emx_name} names no l<layer>t<datatype> term; its drawing layer is not checked")
        elif drawing not in mapped:
            problems.append(f"{layer}: drawing layer {drawing[0]}/{drawing[1]} is not in the define of {emx_name} "
                            f"({', '.join(f'{a}/{b}' for a, b in sorted(mapped))}) -- EMX would not see this geometry")
        elif pin is not None and pin not in mapped:
            problems.append(f"{layer}: pin layer {pin[0]}/{pin[1]} is not in the define of {emx_name} "
                            f"({', '.join(f'{a}/{b}' for a, b in sorted(mapped))}) -- EMX would not find the port labels")
    if problems:
        return StageResult(name, "FAIL", problems + notes)
    pins = sum(pin is not None for *_, pin in rules)
    return StageResult(name, "PASS", [f"{len(rules)} drawing and {pins} pin layers mapped by the defines of {Path(proc_path).name}", *notes])


def _smoke_top_metal_index(profile: ProcessRuleProfile) -> int:
    """Stack position of the highest plain metal: the top of the metal stack below any aluminium-pad /
    redistribution layer (class ``aluminum_pad``) -- the smoke exercises the main metal stack, the proven
    sweep territory. 0 when the stack has no plain metal."""
    catalog = profile.layer_catalog.conductors
    plain = [i for i, name in enumerate(profile.metal_stack, 1) if catalog[name].layer_class != "aluminum_pad"]
    return plain[-1] if plain else 0


def _derived_opening(od: float, w: float, max_um: float, max_opening) -> float:
    """The sweep campaigns' derived-opening formula (family_schemas.dop):
    ``max_um`` if the generator's own max_opening(OD, W) bound clears it,
    else 90% of that bound."""
    bound = max_opening(od, w)
    if max_um <= 0.9 * bound:
        return max_um
    return max(round(0.9 * bound, 2), 0.01)


def _smoke_winding_spacing(
    profile: ProcessRuleProfile, metal_index: int, base_um: float,
    factor: float,
) -> float:
    """Canonical winding spacing on the metal at stack position ``metal_index``: the reference-sweep
    mid-range ``base_um``, grown to ``factor`` x the metal's own min_space
    when the process is coarser than that territory (plain parallel-run
    clearance; 1.5 leaves margin over the rule itself). Values are
    unchanged for fine-pitch top metals (min_space around 1 um). The tw
    family briefly carried a special 2.1 factor here as a workaround for
    its diagonal-vs-pad-corner approach; that geometry is now guaranteed
    by the pcell itself (_tw_slot_half_width bound (b) pad-corner
    correction, tw-bridge-corner-clearance 2026-07-28), so every family
    uses the plain factor again."""
    stack = profile.metal_stack
    name = stack[metal_index - 1] if 1 <= metal_index <= len(stack) else None
    rule = profile.layout_rules.metal_width_space.get(name)
    min_space = getattr(rule, "min_space_um", None) or 0.0
    return max(base_um, round(factor * min_space, 2))


def _canonical_config(
    family: str, profile: ProcessRuleProfile, profile_id: str, top: int,
    max_opening,
) -> dict:
    """Canonical smoke device for one family: mid-range of the reference
    sweep campaigns (od 60-240, w 4-10, s 2-4, lead 20), expressed with
    the profile's conductor names and no center taps. Winding spacings adapt to
    the profile's own min_space (see _smoke_winding_spacing)."""

    def dop(od: float, w: float, max_um: float = 8.0) -> float:
        return _derived_opening(od, w, max_um, max_opening)

    def spacing(metal_index: int, base_um: float, factor: float = 1.5) -> float:
        return _smoke_winding_spacing(profile, metal_index, base_um, factor)

    stack = profile.metal_stack
    top_name, below_name = stack[top - 1], stack[top - 2]
    xfm_ports = ["P1", "N1", "P2", "N2"]
    fixture = dict(_SMOKE_FIXTURE, stub_width_um=6.0)
    base = {
        "process_profile": profile_id,
        "ground_fixture": fixture,
        "drc_check": True,
    }
    if family == "clean_port_ind_sym":
        return base | {
            "port_order": ["P1", "N1"],
            "outer_diameter_um": 120.0,
            "width_um": 6.0,
            "spacing_um": spacing(top, 3.0),
            "opening_um": dop(120.0, 6.0),
            "lead_length_um": 20.0,
            "turns": 2,
            "metal": top_name,
        }
    if family == "clean_port_xfm_bs":
        return base | {
            "port_order": xfm_ports,
            "primary_outer_diameter_um": 120.0,
            "secondary_outer_diameter_um": 120.0,
            "primary_width_um": 6.0,
            "secondary_width_um": 6.0,
            "primary_opening_um": dop(120.0, 6.0),
            "secondary_opening_um": dop(120.0, 6.0),
            "primary_lead_length_um": 20.0,
            "secondary_lead_length_um": 20.0,
            "center_spacing_um": 0.0,
            "primary_metal": top_name,
            "secondary_metal": below_name,
        }
    if family == "clean_port_xfm_ms":
        return base | {
            "port_order": xfm_ports,
            "primary_outer_diameter_um": 160.0,
            "secondary_outer_diameter_um": 120.0,
            "primary_width_um": 6.0,
            "secondary_width_um": 6.0,
            "primary_opening_um": dop(160.0, 6.0),
            "secondary_opening_um": dop(120.0, 6.0),
            "primary_lead_length_um": 20.0,
            "secondary_lead_length_um": 20.0,
            "secondary_turns": 2,
            "secondary_spacing_um": spacing(top - 1, 3.0),
            "center_spacing_um": 0.0,
            "primary_metal": top_name,
            "secondary_metal": below_name,
        }
    if family == "clean_port_xfm_balun":
        od_p, w, s = 120.0, 6.0, spacing(top, 3.0)
        od_s = od_p - 2 * (w + s)
        return base | {
            "port_order": xfm_ports,
            "primary_outer_diameter_um": od_p,
            "secondary_outer_diameter_um": od_s,
            "primary_width_um": w,
            "secondary_width_um": w,
            "spacing_um": s,
            "primary_opening_um": dop(od_p, w),
            "secondary_opening_um": dop(od_s, w),
            "primary_lead_length_um": 20.0,
            "secondary_lead_length_um": 20.0,
            "primary_turns": 1,
            "secondary_turns": 1,
            "center_spacing_um": 0.0,
            "metal": top_name,
        }
    if family == "clean_port_xfm_tw":
        return base | {
            "port_order": xfm_ports,
            "outer_diameter_um": 160.0,
            "width_um": 6.0,
            "spacing_um": spacing(top, 4.0),
            "ring_count": 3,
            "port_gap_p_um": dop(160.0, 6.0),
            "port_gap_n_um": dop(160.0, 6.0),
            "lead_length_um": 20.0,
            "metal": top_name,
        }
    if family == "clean_port_xfm_il":
        od, w, s = 150.0, 6.0, spacing(top, 3.0)
        return base | {
            "port_order": xfm_ports,
            "outer_diameter_um": od,
            "width_um": w,
            "spacing_um": s,
            "turns": 2,
            "primary_opening_um": dop(od, w, 18.0),
            "secondary_opening_um": dop(od - 2 * (w + s), w, 18.0),
            "primary_lead_length_um": 20.0,
            "secondary_lead_length_um": 20.0,
            "metal": top_name,
        }
    raise ValueError(f"unknown generation family: {family}")


def _generate_one_family(
    family: str,
    profile: ProcessRuleProfile,
    profile_id: str,
    top: int,
    out_dir: Path,
) -> tuple[str, list[str]]:
    """(status, details) for one family: build the canonical device
    through the production plugin path, then apply the production DRC
    scope (audit_gds + product_scope_record with the ground-fixture
    exemption, fixture_exemptions: max_width on the profile's fixture
    conductor -- exactly the pcell stage's interpretation)."""
    from ic_opt.em.pcell._pcell_core import max_opening
    from ic_opt.em.pcell.drc_audit import (
        audit_gds,
        expected_conductors,
        fixture_exemptions,
        product_scope_record,
    )
    from ic_opt.em.pcell.generator_plugin import (
        PLUGIN_GENERATORS,
    )

    if top < GENERATION_FAMILIES[family]:
        return "SKIP", [
            f"{family}: needs top metal >= M{GENERATION_FAMILIES[family]}, "
            f"profile stack tops out at M{top}"
        ]
    generator = PLUGIN_GENERATORS[family]
    try:
        config = generator.config_model.model_validate(
            _canonical_config(family, profile, profile_id, top, max_opening)
        )
        family_dir = out_dir / family
        family_dir.mkdir(parents=True, exist_ok=True)
        geometry = generator.generate(
            config, outdir=family_dir, gds_name=f"smoke_{family}.gds"
        )
        report = audit_gds(geometry.gds_path, profile_id)
        record = product_scope_record(
            report,
            expected_conductors(generator, config),
            ignore_findings=fixture_exemptions(profile),
        )
    except Exception as exc:
        return "FAIL", [f"{family}: {type(exc).__name__}: {exc}"]
    if record["outcome"] == "pass":
        return "PASS", []
    if record["outcome"] == "missing_layer":
        return "FAIL", [
            f"{family}: audit found missing product layer(s) "
            f"{record['missing']}"
        ]
    violations = "; ".join(
        f"[{v['kind']}] {v['layer']} x{v['count']}"
        for v in record["violations"]
    )
    return "FAIL", [f"{family}: DRC violation(s): {violations}"]


@contextlib.contextmanager
def _profile_dirs_env(extra_dirs: Sequence[Path]) -> Iterator[None]:
    """Temporarily prepend ``extra_dirs`` to IC_OPT_PROFILE_DIRS: the
    generation smoke runs the production plugin path, which resolves the
    profile by id through the environment, not through this module's
    ``extra_dirs`` parameter."""
    if not extra_dirs:
        yield
        return
    previous = os.environ.get(PROFILE_DIRS_ENV_VAR)
    parts = [str(directory) for directory in extra_dirs]
    if previous:
        parts.append(previous)
    os.environ[PROFILE_DIRS_ENV_VAR] = os.pathsep.join(parts)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(PROFILE_DIRS_ENV_VAR, None)
        else:
            os.environ[PROFILE_DIRS_ENV_VAR] = previous


def _generation_stage(
    profile: ProcessRuleProfile,
    profile_id: str,
    families: Sequence[str],
    out_dir: Path | None,
    extra_dirs: Sequence[Path] = (),
) -> StageResult:
    unknown = [f for f in families if f not in GENERATION_FAMILIES]
    if unknown:
        return StageResult(
            "generation",
            "FAIL",
            [f"unknown family: {name}" for name in unknown],
        )
    top = _smoke_top_metal_index(profile)
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    details: list[str] = []
    with (
        tempfile.TemporaryDirectory(prefix="validate_profile_") as tmp,
        _profile_dirs_env(extra_dirs),
    ):
        target = out_dir if out_dir is not None else Path(tmp)
        for family in families:
            status, family_details = _generate_one_family(
                family, profile, profile_id, top, target
            )
            counts[status] += 1
            details.extend(family_details)
    summary = (
        f"{counts['PASS']} PASS, {counts['FAIL']} FAIL, "
        f"{counts['SKIP']} SKIP"
    )
    return StageResult(
        "generation",
        "FAIL" if counts["FAIL"] else "PASS",
        [summary, *details],
    )


def validate_profile(
    profile_id: str,
    *,
    extra_dirs: Sequence[Path] = (),
    proc_path: Path | None = None,
    generate: bool = False,
    families: Sequence[str] | None = None,
    out_dir: Path | None = None,
) -> ProfileValidationReport:
    stages: list[StageResult] = []
    profile: ProcessRuleProfile | None = None
    path: Path | None = None
    try:
        path = _profile_path(profile_id, tuple(extra_dirs))
    except ValueError as exc:
        stages.append(StageResult("schema", "FAIL", [str(exc)]))
    if path is not None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            stages.append(
                StageResult("schema", "FAIL", [f"YAML parse error: {exc}"])
            )
        else:
            try:
                profile = _with_names(ProcessRuleProfile.model_validate(data))
            except ValidationError as exc:
                stages.append(
                    StageResult(
                        "schema", "FAIL", _render_validation_error(exc)
                    )
                )
            else:
                catalog = profile.layer_catalog
                stages.append(
                    StageResult(
                        "schema",
                        "PASS",
                        [
                            f"{len(catalog.conductors)} conductors, "
                            f"{len(catalog.vias)} vias, "
                            f"{len(catalog.markers)} markers"
                        ],
                    )
                )
    if profile is not None:
        stages.append(_consistency_stage(profile, profile_id))
        stages.append(_proc_stage(profile, proc_path))
        stages.append(_gds_layers_stage(profile, proc_path))
        if generate:
            stages.append(
                _generation_stage(
                    profile,
                    profile_id,
                    tuple(families) if families else tuple(GENERATION_FAMILIES),
                    out_dir,
                    extra_dirs=tuple(extra_dirs),
                )
            )
        else:
            stages.append(
                StageResult(
                    "generation", "SKIPPED", ["not requested (--generate)"]
                )
            )
    return ProfileValidationReport(profile_id, path, stages)
