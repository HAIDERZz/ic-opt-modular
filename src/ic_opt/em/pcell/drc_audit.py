"""Rule-profile-driven DRC audit core (migrated from M12's
``experiments/device_db_sweep_n28/ap_drc_audit.py`` so the real optimizer
path can use it too -- ``src`` must never import from ``experiments``).

Every layer number and rule value comes from the process rule profile
(``ic_opt.em.pcell.rule_adapter``); nothing here hardcodes a
layer number or a rule value, so the same checks apply to any conductor/via
the loaded profile happens to carry, not just AP.

Two things this module does NOT do (kept in the experiments-side CLI/sweep
wrapper, `experiments/device_db_sweep_n28/ap_drc_audit.py`, since neither is
needed by the live optimizer path): building a GDS from scratch for a
representative sweep point (``audit_stratum_point``), and CLI argument
parsing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import klayout.db as kdb

from ic_opt.em.pcell.rule_adapter import (
    GeometryRuleAdapter,
    get_geometry_rule_adapter,
)

Box = tuple[float, float, float, float]        # left, bottom, right, top in um


@dataclass(frozen=True)
class Violation:
    kind: str
    layer: str
    count: int
    detail: str
    boxes_um: tuple[Box, ...] = ()              # where: one bounding box per violating edge pair or region


@dataclass
class Report:
    violations: list[Violation] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        # "zero violations" alone cannot mean "clean" -- a run that checked
        # nothing (unknown profile scope, empty GDS) must not read as a pass.
        return not self.violations and bool(self.checked)

    def summary(self) -> str:
        lines = [
            f"checked ({len(self.checked)}): "
            + (", ".join(self.checked) if self.checked else "(none)")
        ]
        if not self.violations:
            lines.append("violations: none")
        else:
            lines.append(f"violations ({len(self.violations)}):")
            for v in self.violations:
                lines.append(f"  [{v.kind}] {v.layer}: {v.count} -- {v.detail}")
        lines.append(f"PASS: {self.passed}")
        return "\n".join(lines)


def _region_for(layout: kdb.Layout, top: kdb.Cell, drawing: tuple[int, int]) -> kdb.Region:
    layer_index = layout.find_layer(kdb.LayerInfo(drawing[0], drawing[1]))
    if layer_index is None:
        return kdb.Region()
    region = kdb.Region()
    for shape in top.shapes(layer_index).each():
        if not shape.is_text():
            region.insert(shape.polygon)
    return region.merged()


def _um_to_dbu(value_um: float, dbu: float) -> int:
    return int(round(value_um / dbu))


def _box_um(box: kdb.Box, dbu: float) -> Box:
    return (box.left * dbu, box.bottom * dbu, box.right * dbu, box.top * dbu)


def _pair_box(pair: kdb.EdgePair) -> kdb.Box:
    return kdb.Box(pair.first.p1, pair.first.p2) + kdb.Box(pair.second.p1, pair.second.p2)


def _pair_boxes(edge_pairs: kdb.EdgePairs, dbu: float) -> tuple[Box, ...]:
    return tuple(_box_um(_pair_box(pair), dbu) for pair in edge_pairs.each())


def _region_boxes(region: kdb.Region, dbu: float) -> tuple[Box, ...]:
    return tuple(_box_um(polygon.bbox(), dbu) for polygon in region.each())


def _edge_key(edge: kdb.Edge) -> tuple[tuple[int, int], tuple[int, int]]:
    """Orientation-independent identity for one merged-region boundary edge."""
    return tuple(sorted(((edge.p1.x, edge.p1.y), (edge.p2.x, edge.p2.y))))


def _edge_pair_key(
    pair: kdb.EdgePair,
) -> tuple[
    tuple[tuple[int, int], tuple[int, int]],
    tuple[tuple[int, int], tuple[int, int]],
]:
    return tuple(sorted((_edge_key(pair.first), _edge_key(pair.second))))


@dataclass(frozen=True)
class WideParallelFinding:
    count: int                       # unique violating edge pairs
    details: list[str]               # one line per rule that fired
    boxes_um: tuple[Box, ...]        # one bounding box per violating edge pair


def wide_parallel_spacing_violations(
    region: kdb.Region,
    *,
    metal_name: str,
    adapter: GeometryRuleAdapter,
    dbu: float,
    other: kdb.Region | None = None,
) -> WideParallelFinding:
    """Unique profile-rule findings within/between metal regions."""
    rules = adapter.profile.layout_rules.passive_region.wide_parallel_spacing
    violation_keys = set()
    boxes: list[Box] = []
    rule_details = []
    for rule in rules:
        if metal_name not in rule.metals:
            continue

        # Both profile predicates are strict ``>``.  One extra database
        # unit keeps an exactly-threshold width or projection out of the
        # conditional rule without embedding a process-specific epsilon.
        width_floor = _um_to_dbu(rule.when_width_gt_um, dbu) + 1
        projection_floor = (
            _um_to_dbu(rule.when_parallel_length_gt_um, dbu) + 1
        )

        def narrow_edges(value: kdb.Region) -> set:
            return {
                _edge_key(edge)
                for pair in value.width_check(
                    width_floor, True, kdb.Metrics.Euclidian
                ).each()
                for edge in (pair.first, pair.second)
            }

        first_narrow = narrow_edges(region)
        second_narrow = first_narrow if other is None else narrow_edges(other)
        if other is None:
            candidates = region.space_check(
                _um_to_dbu(rule.min_space_um, dbu),
                True,
                kdb.Metrics.Euclidian,
                1,
                projection_floor,
            )
        else:
            candidates = region.separation_check(
                other,
                _um_to_dbu(rule.min_space_um, dbu),
                True,
                kdb.Metrics.Euclidian,
                1,
                projection_floor,
            )
        rule_hit = False
        for pair in candidates.each():
            if (
                _edge_key(pair.first) not in first_narrow
                and _edge_key(pair.second) not in second_narrow
            ):
                if _edge_pair_key(pair) not in violation_keys:
                    boxes.append(_box_um(_pair_box(pair), dbu))
                violation_keys.add(_edge_pair_key(pair))
                rule_hit = True
        if rule_hit:
            rule_details.append(
                f"W>{rule.when_width_gt_um} um, "
                f"L>{rule.when_parallel_length_gt_um} um requires "
                f"space>={rule.min_space_um} um"
            )
    return WideParallelFinding(len(violation_keys), rule_details, tuple(boxes))


def _audit_wide_parallel_spacing(
    layout: kdb.Layout,
    top: kdb.Cell,
    adapter: GeometryRuleAdapter,
    report: Report,
) -> None:
    """Audit conditional width/parallel-length spacing rules from profile."""
    rules = adapter.profile.layout_rules.passive_region.wide_parallel_spacing
    metals = sorted({metal for rule in rules for metal in rule.metals})
    for metal_name in metals:
        layer_spec = adapter.layer(metal_name)
        region = _region_for(layout, top, layer_spec.drawing)
        if region.is_empty():
            continue

        report.checked.append(f"wide_parallel_spacing:{metal_name}")
        finding = wide_parallel_spacing_violations(
            region,
            metal_name=metal_name,
            adapter=adapter,
            dbu=layout.dbu,
        )
        if finding.count:
            report.violations.append(Violation(
                kind="wide_parallel_spacing",
                layer=metal_name,
                count=finding.count,
                detail=(
                    "wide parallel edge pair(s) violate "
                    + "; ".join(finding.details)
                ),
                boxes_um=finding.boxes_um,
            ))


def _audit_metal_rules(
    layout: kdb.Layout, top: kdb.Cell, adapter: GeometryRuleAdapter, report: Report
) -> None:
    metal_rules = adapter.profile.layout_rules.metal_width_space
    for metal_name in sorted(metal_rules):
        # A metal_width_space entry whose conductor is missing from
        # layer_catalog.conductors is a MALFORMED profile: the lookup
        # ValueError propagates (fail closed). The only legitimate skip is
        # the empty-region case below: the conductor exists, this device
        # just didn't draw it.
        layer_spec = adapter.layer(metal_name)
        region = _region_for(layout, top, layer_spec.drawing)
        if region.is_empty():
            continue  # this device didn't draw this metal; nothing to check

        rule = metal_rules[metal_name]
        if rule.min_width_um is not None:
            report.checked.append(f"min_width:{metal_name}")
            edge_pairs = region.width_check(
                _um_to_dbu(rule.min_width_um, layout.dbu), False, kdb.Metrics.Euclidian)
            n = edge_pairs.count()
            if n:
                report.violations.append(Violation(
                    kind="min_width", layer=metal_name, count=n,
                    detail=f"edge(s) narrower than min_width={rule.min_width_um} um",
                    boxes_um=_pair_boxes(edge_pairs, layout.dbu)))

        if rule.min_space_um is not None:
            report.checked.append(f"min_space:{metal_name}")
            edge_pairs = region.space_check(
                _um_to_dbu(rule.min_space_um, layout.dbu), False, kdb.Metrics.Euclidian)
            n = edge_pairs.count()
            if n:
                report.violations.append(Violation(
                    kind="min_space", layer=metal_name, count=n,
                    detail=f"edge pair(s) closer than min_space={rule.min_space_um} um",
                    boxes_um=_pair_boxes(edge_pairs, layout.dbu)))

        if rule.max_width_um is not None:
            report.checked.append(f"max_width:{metal_name}")
            eroded = region.sized(-_um_to_dbu(rule.max_width_um / 2.0, layout.dbu))
            if not eroded.is_empty():
                report.violations.append(Violation(
                    kind="max_width", layer=metal_name, count=eroded.count(),
                    detail=f"region wider than max_width={rule.max_width_um} um",
                    boxes_um=_region_boxes(eroded, layout.dbu)))


def _audit_via_enclosure(
    layout: kdb.Layout, top: kdb.Cell, adapter: GeometryRuleAdapter,
    via_name: str, report: Report,
) -> None:
    via = adapter.via(via_name)
    cuts = _region_for(layout, top, via.drawing)
    if cuts.is_empty():
        return  # this device has no cuts on this via layer

    lower_metal, upper_metal = via.lower_metal, via.upper_metal
    lower_enc = via.min_enclosure_um.get(lower_metal)
    upper_enc = via.min_enclosure_um.get(upper_metal)
    if lower_enc is None or upper_enc is None:
        raise ValueError(
            f"{via_name} enclosure rule does not cover both connected "
            f"metals ({lower_metal}, {upper_metal}); rule profile is "
            "incomplete for this via")

    report.checked.append(f"via_enclosure:{via_name}")
    total_violations = 0
    detail_bits: list[str] = []
    boxes: list[Box] = []
    for metal_name, enclosure_um in ((lower_metal, lower_enc), (upper_metal, upper_enc)):
        metal_layer = adapter.layer(metal_name)
        metal_region = _region_for(layout, top, metal_layer.drawing)
        sized_cuts = cuts.sized(_um_to_dbu(enclosure_um, layout.dbu))
        under_enclosed = (sized_cuts - metal_region).merged()
        if not under_enclosed.is_empty():
            n = under_enclosed.count()
            total_violations += n
            boxes += _region_boxes(under_enclosed, layout.dbu)
            detail_bits.append(
                f"{n} cut(s) under-enclosed by {metal_name} "
                f"(< {enclosure_um} um min_enclosure)")
    if total_violations:
        report.violations.append(Violation(
            kind="via_enclosure", layer=via_name, count=total_violations,
            detail="; ".join(detail_bits), boxes_um=tuple(boxes)))


# Via classes whose cut-array legality (count/spacing/enclosure-in-window) is
# enforced by the generator itself at generation time via
# GeometryRuleAdapter.plan_passive_via_array (fail-closed there); RV is the
# one via class this audit checks directly because it lands a top-metal body
# through a single large redistribution via, the geometry class this audit
# exists to police.
_AUDITED_VIAS: tuple[str, ...] = ("RV",)


def audit_gds(gds_path, profile_id: str) -> Report:
    """Audit a built GDS against ``profile_id``'s rule profile."""
    adapter = get_geometry_rule_adapter(profile_id)

    layout = kdb.Layout()
    layout.read(str(gds_path))
    tops = layout.top_cells()
    if len(tops) != 1:
        raise ValueError(f"DRC audit requires exactly one top cell, got {len(tops)}")
    top = tops[0]
    top.flatten(True)

    report = Report()
    _audit_metal_rules(layout, top, adapter, report)
    _audit_wide_parallel_spacing(layout, top, adapter, report)
    for via_name in _AUDITED_VIAS:
        if via_name not in adapter.profile.layer_catalog.vias:
            # The ONE legitimate skip: this profile has no such via at all.
            # Any other problem (missing primitive rule, missing enclosure
            # side, unknown connected metal) is a MALFORMED profile and must
            # fail closed -- _audit_via_enclosure raises and we don't
            # swallow it.
            continue
        _audit_via_enclosure(layout, top, adapter, via_name, report)
    return report


def _metal_index(metal: str) -> int:
    """Conductor token -> stack index, same semantics as the pcell's own
    ``_metal_index`` ("AP" is the top of the stack at index 11, case-
    insensitive; "10"/"M10"/"m10" -> 10). Raises ValueError for
    non-conductor tokens (e.g. via names like "RV")."""
    token = metal.strip()
    if token.upper() == "AP":
        return 11
    digits = token[1:] if token[:1] in ("m", "M") else token
    return int(digits)


def _metal_name(index: int) -> str:
    """Stack index -> profile conductor name, same semantics as the
    pcell's ``_metal_name`` (index 11 is "AP", not "M11")."""
    return "AP" if index == 11 else f"M{index}"


def canonical_conductor(name: str) -> str:
    """THE single canonicalization point for conductor names: "ap" -> "AP",
    "10"/"m10" -> "M10", via the same ``_metal_index``/``_metal_name``
    semantics the pcell uses to accept these spellings at generation time.
    Non-conductor tokens (e.g. the via name "RV" appearing as a Violation
    layer) pass through stripped."""
    try:
        return _metal_name(_metal_index(name))
    except ValueError:
        return name.strip()


def _ind_recipe(config: dict) -> list[str]:
    # NT=1 is one direct body-metal ring and has no turn-to-turn crossunder.
    # NT>=2 draws that bridge on the next lower conductor (bottom_metal remains the
    # chain's documented dead parameter — never echo it here; round-2
    # finding, fixed M13 ticket 04). With ct_metal the tap vias stack lands
    # on every level from top-1 down to the CT lead metal, so those
    # conductors join the expected set even for NT=1.
    top = _metal_index(config["top_metal"])
    turns = int(config.get("turns", 2))
    layers = [_metal_name(top)]
    if turns >= 2:
        layers.append(_conductor_below(config["top_metal"], config.get("process_profile")))
    ct = config.get("ct_metal")
    if ct is not None:
        layers += _ct_chain(config["top_metal"], ct, config.get("process_profile"))
    return _dedupe(layers)


def _conductors_below(metal: str, process_profile: str | None,
                      levels: int) -> list[str]:
    """The ``levels`` real conductors directly below ``metal``, nearest
    first. With a profile this follows the actual stack (n65_1p9m has no
    M10, so below AP comes M9 then M8); without one it is the contiguous
    numeric stack. Fails closed when the stack is not deep enough."""
    candidates = [_metal_name(i) for i in range(1, _metal_index(metal))]
    if process_profile is not None:
        adapter = get_geometry_rule_adapter(process_profile)
        adapter.layer(canonical_conductor(metal))
        candidates = [m for m in candidates
                      if m in adapter.profile.layer_catalog.conductors]
    if len(candidates) < levels:
        raise ValueError(
            f"{metal} has only {len(candidates)} conductor(s) below it in "
            f"profile {process_profile!r}; {levels} needed")
    return candidates[::-1][:levels]


def _conductor_below(metal: str, process_profile: str | None) -> str:
    return _conductors_below(metal, process_profile, 1)[0]


def _ct_chain(winding_metal: str, ct_metal: str | None,
              process_profile: str | None = None) -> list[str]:
    """Conductors the tap vias stack lands on: every level from one below
    the winding plane down to the CT lead metal (M13 ticket 05)."""
    if ct_metal is None:
        return []
    top = _metal_index(winding_metal)
    layers = [_metal_name(i)
              for i in range(top - 1, _metal_index(ct_metal) - 1, -1)]
    if process_profile is not None:
        adapter = get_geometry_rule_adapter(process_profile)
        adapter.layer(canonical_conductor(winding_metal))
        adapter.layer(canonical_conductor(ct_metal))
        layers = [name for name in layers
                  if name in adapter.profile.layer_catalog.conductors]
    return layers


def _dedupe(layers: list[str]) -> list[str]:
    seen: set[str] = set()
    return [x for x in layers if not (x in seen or seen.add(x))]


def _xfm_bs_recipe(config: dict) -> list[str]:
    layers = [config["primary_metal"], config["secondary_metal"]]
    layers += _ct_chain(config["primary_metal"],
                        config.get("ct_primary_metal"), config.get("process_profile"))
    layers += _ct_chain(config["secondary_metal"],
                        config.get("ct_secondary_metal"), config.get("process_profile"))
    return _dedupe(_metal_name(_metal_index(m)) for m in layers)


def _xfm_ms_recipe(config: dict) -> list[str]:
    multi = config["multi_metal"]
    # Reference bridge scheme (design-region issue 03): the multi coil is
    # an ind on multi_metal -- crossovers use multi_metal-1 only.
    layers = [
        config["single_metal"],
        multi,
        _conductor_below(multi, config.get("process_profile")),
    ]
    layers += _ct_chain(config["single_metal"],
                        config.get("ct_primary_metal"), config.get("process_profile"))
    layers += _ct_chain(multi, config.get("ct_secondary_metal"), config.get("process_profile"))
    return _dedupe(_metal_name(_metal_index(m)) for m in layers)


def _xfm_balun_recipe(config: dict) -> list[str]:
    """Balun conductors: the balun plane plus the real conductor below it
    (the nested secondary's escape crossunder always draws there; nesting
    is mandatory since 2026-09-22) plus any CT tap chain."""
    balun = config["balun_metal"]
    layers = [_metal_name(_metal_index(balun)),
              _conductor_below(balun, config.get("process_profile"))]
    layers += _ct_chain(balun, config.get("ct_primary_metal"), config.get("process_profile"))
    layers += _ct_chain(balun, config.get("ct_secondary_metal"), config.get("process_profile"))
    return _dedupe(layers)


def _xfm_tw_recipe(config: dict) -> list[str]:
    # Both windings share every ring on top_metal; every ring-boundary
    # crossing dives exactly one level (the real conductor below top_metal,
    # xfm-tw tickets 01/02b). No CT exists on this device.
    top = config["top_metal"]
    return [_metal_name(_metal_index(top)),
            _conductor_below(top, config.get("process_profile"))]


def _ct_chain_up(winding_metal: str, ct_metal: str | None,
                 process_profile: str | None = None) -> list[str]:
    """Conductors an UPWARD tap's via stack lands on: every level from one
    above the winding plane up to the CT lead metal (xfm_il ticket 03c --
    the only device whose CT tap direction is chosen per-call, rather than
    always sitting strictly below the winding plane like every other
    CT-bearing device's ``_ct_chain`` above)."""
    if ct_metal is None:
        return []
    bottom = _metal_index(winding_metal)
    top = _metal_index(ct_metal)
    layers = [_metal_name(i) for i in range(bottom + 1, top + 1)]
    if process_profile is not None:
        adapter = get_geometry_rule_adapter(process_profile)
        adapter.layer(canonical_conductor(winding_metal))
        adapter.layer(canonical_conductor(ct_metal))
        layers = [name for name in layers
                  if name in adapter.profile.layer_catalog.conductors]
    return layers


def _il_ct_chain(sl_metal: str, ct_metal: str | None,
                 process_profile: str | None = None) -> list[str]:
    """Direction-dispatched CT chain for xfm_il (ticket 03c, mirroring the
    pcell's own ``_il_ct_metal_guard``): a CT metal ABOVE sl_metal uses the
    upward chain (sl_metal+1..ct_metal); BELOW uses the same downward chain
    every other CT-bearing device already uses (sl_metal-1..ct_metal, which
    overlaps -- and dedupes against -- the two crossunder leg layers
    ``_xfm_il_recipe`` already lists)."""
    if ct_metal is None:
        return []
    if _metal_index(ct_metal) > _metal_index(sl_metal):
        return _ct_chain_up(sl_metal, ct_metal, process_profile)
    return _ct_chain(sl_metal, ct_metal, process_profile)


def _xfm_il_recipe(config: dict) -> list[str]:
    # Both windings share one metal plane (top_metal); every turn's
    # crossunder is split across TWO independent layers below it -- the
    # real conductors one and two levels down (leg1, leg2), ticket 02d's
    # dual-layer-legs fix -- unconditionally, since the pcell requires
    # NT_P>=2 (always a crossunder to draw). CT taps (either winding,
    # either direction) add their own via-stack chain on top of that.
    top = config["top_metal"]
    profile = config.get("process_profile")
    layers = [_metal_name(_metal_index(top))] + _conductors_below(top, profile, 2)
    layers += _il_ct_chain(top, config.get("ct_primary_metal"), profile)
    layers += _il_ct_chain(top, config.get("ct_secondary_metal"), profile)
    return _dedupe(_metal_name(_metal_index(m)) for m in layers)


# Generator id -> full expected-conductor recipe. The pcell draws
# crossunder/crossover metal on the REAL conductor(s) below the winding
# metal (``_metal_below`` in the pcell; ``_conductors_below`` here reads the
# same profile stack, so under AP on n65_1p9m comes M9, not a nonexistent
# M10): xfm_ms under the multi winding, xfm_balun under the balun body, the
# inductor's crossunder (its config bottom_metal is a dead parameter) plus
# the optional ct_metal tap chain; xfm_bs's two windings are both fully
# named (primary/secondary); xfm_tw's dive legs are one level down (CT-less
# by design); xfm_il's crossunder is split across the two conductors below
# (ticket 02d) and its optional CT chains are direction-dispatched (above
# top_metal -> upward chain; below -> the same downward chain as every
# other CT-bearing device, ticket 03c). Without a profile every recipe is
# the contiguous numeric stack (reference mode).
_EXPECTED_RECIPES = {
    "clean_port_ind_sym": _ind_recipe,
    "clean_port_xfm_bs": _xfm_bs_recipe,
    "clean_port_xfm_ms": _xfm_ms_recipe,
    "clean_port_xfm_balun": _xfm_balun_recipe,
    "clean_port_xfm_tw": _xfm_tw_recipe,
    "clean_port_xfm_il": _xfm_il_recipe,
}


def require_layers_from_config(generator_id: str, config: dict) -> list[str]:
    """The FULL expected conductor recipe for this generator id, from the
    ``_EXPECTED_RECIPES`` table above -- winding metals from the config's
    metal fields PLUS the implicit crossunder conductors the pcell draws on
    the real stack levels below (xfm_ms: under multi_metal; xfm_balun:
    under balun_metal).
    Names are canonicalized ("ap" -> AP, "10" -> M10). Fails closed
    (ValueError) for a generator id without a recipe: a default coverage
    requirement nobody modeled must not silently degrade to *_metal-only --
    the caller must supply the expected layers explicitly instead."""
    recipe = _EXPECTED_RECIPES.get(generator_id)
    if recipe is None:
        raise ValueError(
            f"no expected-conductor recipe for generator {generator_id!r} "
            f"(known: {sorted(_EXPECTED_RECIPES)}); supply the expected "
            "conductors explicitly")
    out: list[str] = []
    for metal in recipe(config):
        name = canonical_conductor(metal)
        if name not in out:
            out.append(name)
    return out


def product_scope_record(
    report: Report, expected_conductors: list[str],
    *,
    ignore_layers: tuple[str, ...] = (),
    ignore_findings: frozenset[tuple[str, str]] = frozenset(),
) -> dict:
    """The product-scope pass/fail predicate. ``ignore_layers`` (default
    EMPTY here -- unlike the sweep-side wrapper, a real optimizer candidate
    has no ground-fixture ring to exempt by default) names layers whose
    findings are excluded from the product verdict entirely.
    ``ignore_findings`` is the finer-grained sibling: a set of
    ``(kind, layer)`` pairs to exclude, for a caller that needs to keep
    auditing a layer in general but exempt one SPECIFIC, known-and-
    understood violation kind on it (e.g. a fixed-size fixture that shares a
    GDS layer with product geometry the caller still wants fully audited).
    Both are caller-supplied, domain-specific knowledge -- this function
    stays generic over any layer/kind combination.

    Returns a dict carrying BOTH views of a ``Report``:

    * raw -- ``all_checked`` (count), ``all_violations``, ``all_pass``
      (``report.passed`` verbatim; always present so consumers can
      distinguish raw-clean from product-scope-clean);
    * product scope -- ``violations`` (``ignore_layers``/``ignore_findings``
      excluded) and ``outcome`` ("pass"/"fail"/"missing_layer").

    Coverage requirement: a pass requires EVERY conductor in
    ``expected_conductors`` (the device's metal recipe) to appear in at
    least one ran check. Any expected layer with zero checks means that
    metal's geometry silently vanished from the GDS -- the record comes
    back ``outcome="missing_layer"`` with the missing layers listed, never
    "pass". An empty ``expected_conductors`` fails closed (``ValueError``).
    """
    if not expected_conductors:
        raise ValueError(
            "expected_conductors must be non-empty (fail closed: a "
            "coverage requirement of nothing would pass a GDS with no "
            "product metal at all)")
    expected = []
    for m in expected_conductors:
        name = canonical_conductor(m)
        if name not in expected:
            expected.append(name)
    ignored = {canonical_conductor(m) for m in ignore_layers}
    ignored_findings = {(kind, canonical_conductor(layer))
                         for kind, layer in ignore_findings}
    checked_layers = {canonical_conductor(c.split(":", 1)[1])
                      for c in report.checked if ":" in c}
    missing = [m for m in expected if m not in checked_layers]
    product_violations = [
        v for v in report.violations
        if canonical_conductor(v.layer) not in ignored
        and (v.kind, canonical_conductor(v.layer)) not in ignored_findings]
    record = {
        "all_checked": len(report.checked),
        "all_violations": [
            {"kind": v.kind, "layer": v.layer, "count": v.count}
            for v in report.violations],
        "all_pass": report.passed,
        "violations": [
            {"kind": v.kind, "layer": v.layer, "count": v.count}
            for v in product_violations],
    }
    if missing:
        record["outcome"] = "missing_layer"
        record["missing"] = missing
    else:
        record["outcome"] = "pass" if not product_violations else "fail"
    return record
