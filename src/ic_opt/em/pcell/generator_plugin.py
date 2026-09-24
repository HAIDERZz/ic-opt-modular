"""Product-generator plugin wrapping the clean-port inductors.

Loaded by the product registry via geometry.yaml's `plugin_module` (contract-
explicit plugin mechanism, Stage 2 spec 2026-07-09). This file and the ported
module it wraps ship inside the installed package under
``ic_opt.em.pcell/devices/clean_port/`` (M11 R2: the device library ships
in the release; see this directory's README.md for the provenance/licensing
note). Contracts may still reference this file by absolute path, or via the
``plugin_module: builtin:clean_port`` shorthand resolved by
``geometry/registry.py``.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import threading
from pathlib import Path
from typing import ClassVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_serializer,
    model_validator,
)

from ic_opt.em.pcell import GEOMETRY_VERSION
from ic_opt.em.pcell.base import (
    GeometryGenerationResult,
    PassiveDeviceGenerator,
)
from ic_opt.em.pcell.path_safety import validate_output_file_name
from ic_opt.em.pcell.pgs import CleanPortPgsConfig, add_pgs
from ic_opt.em.pcell.rule_adapter import get_geometry_rule_adapter

_PORT_NAME_RE = re.compile(r"[A-Za-z0-9_]+")


def _metal_stack_index_or_none(value: str) -> int | None:
    """Best-effort metal-stack index, ONLY for the M1-adjacency checks below
    (mirrors pcell_inductor_port_clean.py's own _metal_index digit-parsing
    convention: "1"/"M1"/"m1" -> 1, "AP"/"ap" -> 11, so M1 and AP spellings
    are recognized without needing the full pcell/rule-profile machinery at
    validation time -- that module is loaded lazily via _clean_port() only
    when actually generating geometry, and this file must not force that
    load, and its klayout dependency, just to validate a config). Returns
    None for anything this convention doesn't parse as a plain digit string;
    such a value is left to the pcell's own generate()-time validation.
    """
    token = value.strip()
    if token.upper() == "AP":
        return 11
    digits = token[1:] if token[:1] in ("m", "M") else token
    try:
        return int(digits)
    except ValueError:
        return None


def _forbid_m1_metal(value: str) -> str:
    """Reject M1 as a product-metal choice. M1 is exclusively occupied by
    every clean-port generator's mandatory ground-fixture ring/stub
    (add_ground_fixture(), independent of any device parameter) -- a
    product winding/crossunder placed on the same layer would share one
    merged GDS region with that fixture, which the per-layer DRC audit
    (em_candidate_preparation.py) cannot then reliably separate from
    genuine product geometry. Forbidding M1 here removes the ambiguity at
    its source instead of trying to filter it out after the fact."""
    if _metal_stack_index_or_none(value) == 1:
        raise ValueError(
            f"metal {value!r} resolves to M1, which is reserved for the "
            "ground fixture and is not a valid product-metal choice"
        )
    return value


def _forbid_m1_and_m2_metal(value: str) -> str:
    """Like ``_forbid_m1_metal``, but ALSO rejects M2: this generator draws
    its crossunder/crossover one stack level below this field's own metal
    (see _EXPECTED_RECIPES's _xfm_ms_recipe/_xfm_balun_recipe in
    geometry/drc_audit.py), so an M2 winding would put that implicit,
    unconfigurable crossunder on M1 just the same."""
    index = _metal_stack_index_or_none(value)
    if index in (1, 2):
        raise ValueError(
            f"metal {value!r} resolves to M{index}, which would place this "
            "device's implicit crossunder on M1 (one stack level below); M1 "
            "is reserved for the ground fixture and is not a valid "
            "product-metal choice (directly or via the derived crossunder)"
        )
    return value


def _forbid_m1_through_m3_metal(value: str) -> str:
    """Like ``_forbid_m1_and_m2_metal``, but ALSO rejects M3: xfm_il draws
    its crossunder leg2 TWO stack levels below this field's own metal
    (ticket 02d's dual-layer legs -- see ``_xfm_il_recipe`` in
    geometry/drc_audit.py), so an M3 metal would put that implicit
    leg2 on M1 just the same as M1/M2 would directly."""
    index = _metal_stack_index_or_none(value)
    if index in (1, 2, 3):
        raise ValueError(
            f"metal {value!r} resolves to M{index}, which would place this "
            "device's implicit crossunder leg2 on M1 (two stack levels "
            "below); M1 is reserved for the ground fixture and is not a "
            "valid product-metal choice (directly or via the derived "
            "two-layer crossunder)"
        )
    return value

_CLEAN_PORT_PATH = Path(__file__).resolve().parent / "pcell_inductor_port_clean.py"

_CLEAN_PORT_LOCK = threading.Lock()


def _clean_port():
    with _CLEAN_PORT_LOCK:
        if "clean_port_mod" in sys.modules:
            return sys.modules["clean_port_mod"]
        spec = importlib.util.spec_from_file_location("clean_port_mod", _CLEAN_PORT_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["clean_port_mod"] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop("clean_port_mod", None)
            raise
        return mod


class CleanPortGroundFixtureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inner_margin_um: float = Field(gt=0)
    ring_width_um: float = Field(gt=0)
    # None = derive per port from the device's own lead widths (M13 ticket
    # 09): the product's default is "each stub is as wide as the lead it
    # lands on"; an explicit value reproduces the old fixed-width behavior
    # byte-identically. stub_width_by_port_um overrides either, per port.
    stub_width_um: float | None = Field(default=None, gt=0)
    stub_length_um: float = Field(gt=0)
    stub_chamfer_um: float = Field(ge=0)
    stub_width_by_port_um: dict[str, float] | None = None

    @field_validator("stub_width_by_port_um")
    @classmethod
    def _stub_widths_positive(
        cls, value: dict[str, float] | None
    ) -> dict[str, float] | None:
        if value is not None:
            for port, width in value.items():
                if width <= 0:
                    raise ValueError(
                        f"stub_width_by_port_um[{port!r}] must be > 0, got {width}"
                    )
        return value


METAL_FIELDS = ("metal", "ct_metal", "primary_metal", "secondary_metal", "ct_primary_metal", "ct_secondary_metal")


class _CleanPortDeviceConfigBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Field names this family retired (M2.2 vocabulary: one winding -> ``metal`` / ``turns``; two windings ->
    #: ``primary_*`` / ``secondary_*``; xfm_tw's stub gaps -> ``port_gap_*``). Old name -> new name, or None
    #: when the field was dead and is simply gone. ``translate_config`` applies it to old records; a spec
    #: that still uses an old name is refused with the new one.
    renamed: ClassVar[dict[str, str | None]] = {}

    process_profile: str
    port_order: list[str]
    ground_fixture: CleanPortGroundFixtureConfig
    drc_check: StrictBool = True

    @model_validator(mode="before")
    @classmethod
    def _refuse_retired_names(cls, data):
        if isinstance(data, dict):
            stale = [k for k in data if k in cls.renamed]
            if stale:
                raise ValueError("retired field name(s): " + "; ".join(
                    f"{k} is now {cls.renamed[k]}" if cls.renamed[k] else f"{k} was removed (it never affected the geometry)" for k in stale))
        return data

    @model_validator(mode="after")
    def _metals_are_profile_conductors(self):
        """Every metal the config names is a metal of its profile's stack, by name: "AP", "M9", or "9" for M9. The
        pcell reads digits as stack positions internally (T13.11), so a "10" on a stack without M10 has to be
        refused here rather than land on whatever the tenth metal is."""
        if not _profile_known(self.process_profile):
            return self
        from ic_opt.em.pcell.process_rules import get_process_rule_profile

        stack = get_process_rule_profile(self.process_profile).metal_stack
        names = {name.upper() for name in stack}
        for field_name in METAL_FIELDS:
            value = getattr(self, field_name, None)
            if value is None:
                continue
            token = str(value).strip().upper()
            digits = token[1:] if token[:1] == "M" else token
            if token not in names and not (digits.isdigit() and f"M{int(digits)}" in names):
                raise ValueError(f"{field_name} {value!r} is not a metal of profile {self.process_profile} "
                                 f"(its metals, bottom first: {', '.join(stack)})")
        return self

    @model_serializer(mode="wrap")
    def _serialize_config(self, handler):
        data = handler(self)
        # An absent shield must retain the historical config/cache identity.
        if data.get("pgs") is None:
            data.pop("pgs", None)
        if data.get("straight_extension_um") == 0:
            data.pop("straight_extension_um")
        return data

    @field_validator("port_order")
    @classmethod
    def _ports_unique(cls, value: list[str]) -> list[str]:
        for name in value:
            if not name.strip():
                raise ValueError("port_order names must be non-empty")
            if not _PORT_NAME_RE.fullmatch(name):
                raise ValueError(
                    f"port_order name {name!r} must match [A-Za-z0-9_]+ "
                    "(':', '=' or whitespace would corrupt the EMX "
                    "-p name=signal:ref port line format)"
                )
        if len(set(value)) != len(value):
            raise ValueError("port_order names must be unique")
        return value


class _CleanPortInductorConfigBase(_CleanPortDeviceConfigBase):
    outer_diameter_um: float = Field(gt=0)
    width_um: float = Field(gt=0)
    spacing_um: float = Field(gt=0)
    opening_um: float = Field(gt=0)
    lead_length_um: float = Field(gt=0)
    turns: int = Field(ge=1)
    metal: str = Field(min_length=1)
    port_spacing_um: float | None = Field(default=None, gt=0, multiple_of=0.01)      # tip-to-tip centre spacing; default 2*opening + width (M3.1)

    @field_validator("metal")
    @classmethod
    def _metals_not_m1(cls, value: str) -> str:
        return _forbid_m1_metal(value)


#: The inductor's fixed semantic base, the same shape ``_XFM_PORTS`` gives
#: the transformers. The PCell's own ``ind_sym`` default has always been
#: this; only the CT-less config branch used to let a caller override it
#: with anything, which is how the production plan came to draw p01/p02.
_IND_PORTS = ["P1", "N1"]

#: Migration text for the retired CT-less "any two unique names" contract,
#: in the RETIRED_GENERATOR_IDS style: say what to write, and say what is
#: NOT changing so nobody "fixes" the EMX port names too.
_IND_PORT_MIGRATION = (
    "M13 semantic port naming now applies to the CT-less inductor as well: "
    "replace `port_order: [p01, p02]` with `port_order: ['P1', 'N1']`. The "
    "EMX port NAMES are unaffected -- a sweep plan keeps driving EMX with "
    "p01/p02 through its `emx_ports_override` (name p01 -> signal P1, name "
    "p02 -> signal N1), so the sNp column order does not move."
)


class CleanPortIndSymConfig(_CleanPortInductorConfigBase):
    renamed: ClassVar[dict[str, str | None]] = {"top_metal": "metal", "bottom_metal": None}
    pgs: CleanPortPgsConfig | None = None
    straight_extension_um: float = Field(
        default=0, ge=0, multiple_of=0.01, allow_inf_nan=False)
    # The port set is the fixed semantic base [P1, N1], plus the appended
    # tap when ct_metal is set — exactly [P1, N1, CT] (M13 ticket 03). The
    # 2-or-3 length window here is refined by _ct_contract below.
    port_order: list[str] = Field(min_length=2, max_length=3)
    ct_metal: str | None = None

    @field_validator("ct_metal")
    @classmethod
    def _ct_metal_not_m1(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _forbid_m1_metal(value)

    @model_validator(mode="after")
    def _ct_contract(self) -> CleanPortIndSymConfig:
        if self.ct_metal is None:
            if self.port_order != _IND_PORTS:
                raise ValueError(
                    f"port_order must be exactly {_IND_PORTS} without "
                    f"ct_metal; got {self.port_order!r}. "
                    + _IND_PORT_MIGRATION
                )
            return self
        if self.port_order != ["P1", "N1", "CT"]:
            raise ValueError(
                "ct_metal adds the tap port, so port_order must be exactly "
                f"['P1', 'N1', 'CT']; got {self.port_order!r} "
                "(M13: taps append to the fixed semantic base order)"
            )
        # For turns>=2 the winding crossunder is drawn at metal-1 and
        # crosses y=0 exactly where the CT lead (drawn on ct_metal) runs.
        # Keep the same conservative two-level tap contract for turns==1;
        # relaxing the public CT contract is outside the direct-ring fix.
        # Unparseable metal spellings fall through to the pcell's own
        # generate()-time guard, per _metal_stack_index_or_none's contract.
        top = _metal_stack_index_or_none(self.metal)
        ct = _metal_stack_index_or_none(self.ct_metal)
        if top is not None and ct is not None and ct >= top - 1:
            raise ValueError(
                f"ct_metal {self.ct_metal!r} must sit at least two "
                f"levels below metal {self.metal!r}: multi-turn "
                "windings occupy metal-1 at the CT lead path, and the "
                "same conservative tap contract applies to turns=1 "
                "(bug review 2026-07-17 N1)"
            )
        return self


_XFM_PORTS = ["P1", "N1", "P2", "N2"]


class _FixedXfmPortOrderMixin:
    """Shared port-set validator for xfm devices: the fixed base
    [P1, N1, P2, N2] plus the enabled taps — CTP (primary) before CTS
    (secondary) — appended in that order (M13 ticket 05). The device
    defines the winding<->port mapping. EMX sorts sNp ports lexicographically
    by EMX port name, independently of this field or the -p argument order."""

    @model_validator(mode="after")
    def _ports_fixed_by_device(self):
        expected = list(_XFM_PORTS)
        if getattr(self, "ct_primary_metal", None) is not None:
            expected.append("CTP")
        if getattr(self, "ct_secondary_metal", None) is not None:
            expected.append("CTS")
        if self.port_order != expected:
            raise ValueError(
                "xfm devices have fixed port names; port_order must be "
                f"exactly {expected} (the fixed base {_XFM_PORTS} plus the "
                "enabled taps CTP/CTS in that order; EMX sorts sNp ports "
                "lexicographically by EMX port name, independently of "
                "this field or the ports list order)")
        return self


class CleanPortXfmBsConfig(_FixedXfmPortOrderMixin, _CleanPortDeviceConfigBase):
    pgs: CleanPortPgsConfig | None = None
    straight_extension_um: float = Field(
        default=0, ge=0, multiple_of=0.01, allow_inf_nan=False)
    primary_outer_diameter_um: float = Field(gt=0)
    secondary_outer_diameter_um: float = Field(gt=0)
    primary_width_um: float = Field(gt=0)
    secondary_width_um: float = Field(gt=0)
    primary_opening_um: float = Field(gt=0)
    secondary_opening_um: float = Field(gt=0)
    primary_lead_length_um: float = Field(gt=0)
    secondary_lead_length_um: float = Field(gt=0)
    center_spacing_um: float = Field(ge=0, multiple_of=0.01, allow_inf_nan=False)      # half of it is a coordinate: keep it on the grid (D9)
    primary_metal: str = Field(min_length=1)
    secondary_metal: str = Field(min_length=1)
    ct_primary_metal: str | None = None
    ct_secondary_metal: str | None = None
    primary_port_spacing_um: float | None = Field(default=None, gt=0, multiple_of=0.01)     # M3.1: per winding, default 2*opening + width
    secondary_port_spacing_um: float | None = Field(default=None, gt=0, multiple_of=0.01)

    @field_validator("ct_primary_metal", "ct_secondary_metal")
    @classmethod
    def _ct_metals_not_m1(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _forbid_m1_metal(value)

    @model_validator(mode="after")
    def _center_spacing_keeps_overlap(self) -> CleanPortXfmBsConfig:
        # Broadside coupling lives in the vertical overlap of the two
        # windings: at spacing (OD_P+OD_S)/2 they are merely tangent (zero
        # overlap), so the product caps spacing at half that — at least
        # ~half the coils still overlap. Anything wider is two independent
        # inductors, not a transformer (user decision 2026-07-17, M13
        # ticket 10; xfm_balun has its own, stricter rule: the secondary
        # must nest inside the primary).
        bound = (self.primary_outer_diameter_um
                 + self.secondary_outer_diameter_um) / 4.0
        if self.center_spacing_um > bound:
            raise ValueError(
                f"center_spacing_um {self.center_spacing_um} exceeds "
                f"(primary_od + secondary_od)/4 = {bound}: beyond that the "
                "windings lose the broadside overlap that makes this a "
                "transformer")
        return self

    @model_validator(mode="after")
    def _ct_below_windings(self) -> CleanPortXfmBsConfig:
        # The tap vias stack drops from the winding plane, so the CT metal
        # must sit strictly below its winding (M13 ticket 05; the pcell
        # repeats this guard fail-closed at generate time).
        for ct, host, label in (
                (self.ct_primary_metal, self.primary_metal, "primary"),
                (self.ct_secondary_metal, self.secondary_metal,
                 "secondary")):
            if ct is None:
                continue
            c = _metal_stack_index_or_none(ct)
            h = _metal_stack_index_or_none(host)
            if c is not None and h is not None and c >= h:
                raise ValueError(
                    f"ct_{label}_metal {ct!r} must sit below "
                    f"{label}_metal {host!r} (the tap stack drops from "
                    "the winding plane)")
        return self

    @field_validator("primary_metal", "secondary_metal")
    @classmethod
    def _metals_not_m1(cls, value: str) -> str:
        return _forbid_m1_metal(value)


class CleanPortXfmMsConfig(_FixedXfmPortOrderMixin, _CleanPortDeviceConfigBase):
    """1:N stacked transformer. The primary is the single-turn winding (P1/N1,
    ``primary_width_um``); the secondary is the multi-turn winding (P2/N2,
    ``secondary_width_um``, ``secondary_turns`` >= 2)."""

    renamed: ClassVar[dict[str, str | None]] = {
        "single_outer_diameter_um": "primary_outer_diameter_um", "multi_outer_diameter_um": "secondary_outer_diameter_um",
        "single_width_um": "primary_width_um", "multi_width_um": "secondary_width_um",
        "single_opening_um": "primary_opening_um", "multi_opening_um": "secondary_opening_um",
        "single_lead_length_um": "primary_lead_length_um", "multi_lead_length_um": "secondary_lead_length_um",
        "multi_turns": "secondary_turns", "multi_spacing_um": "secondary_spacing_um",
        "single_metal": "primary_metal", "multi_metal": "secondary_metal",
    }
    pgs: CleanPortPgsConfig | None = None
    straight_extension_um: float = Field(
        default=0, ge=0, multiple_of=0.01, allow_inf_nan=False)
    primary_outer_diameter_um: float = Field(gt=0)
    secondary_outer_diameter_um: float = Field(gt=0)
    primary_width_um: float = Field(gt=0)
    secondary_width_um: float = Field(gt=0)
    primary_opening_um: float = Field(gt=0)
    secondary_opening_um: float = Field(gt=0)
    primary_lead_length_um: float = Field(gt=0)
    secondary_lead_length_um: float = Field(gt=0)
    secondary_turns: int = Field(ge=2)
    secondary_spacing_um: float = Field(gt=0)
    center_spacing_um: float = Field(ge=0, multiple_of=0.01, allow_inf_nan=False)      # half of it is a coordinate: keep it on the grid (D9)
    primary_metal: str = Field(min_length=1)
    secondary_metal: str = Field(min_length=1)
    ct_primary_metal: str | None = None
    ct_secondary_metal: str | None = None
    primary_port_spacing_um: float | None = Field(default=None, gt=0, multiple_of=0.01)     # M3.1: per winding, default 2*opening + width
    secondary_port_spacing_um: float | None = Field(default=None, gt=0, multiple_of=0.01)

    @field_validator("primary_metal")
    @classmethod
    def _primary_metal_not_m1(cls, value: str) -> str:
        return _forbid_m1_metal(value)

    @field_validator("secondary_metal")
    @classmethod
    def _secondary_metal_not_m1_or_m2(cls, value: str) -> str:
        return _forbid_m1_and_m2_metal(value)

    @field_validator("ct_primary_metal", "ct_secondary_metal")
    @classmethod
    def _ct_metals_not_m1(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _forbid_m1_metal(value)

    @model_validator(mode="after")
    def _center_spacing_keeps_overlap(self) -> CleanPortXfmMsConfig:
        # Same overlap rule as xfm_bs (M13 ticket 10): the windings couple
        # through their vertical overlap, so spacing is capped at
        # (primary_od + secondary_od)/4.
        bound = (self.primary_outer_diameter_um
                 + self.secondary_outer_diameter_um) / 4.0
        if self.center_spacing_um > bound:
            raise ValueError(
                f"center_spacing_um {self.center_spacing_um} exceeds "
                f"(primary_od + secondary_od)/4 = {bound}: beyond that the "
                "windings lose the overlap that makes this a transformer")
        return self

    @model_validator(mode="after")
    def _ct_metal_rules(self) -> CleanPortXfmMsConfig:
        # P side taps the single-turn primary: strictly below its plane.
        # S side taps the multi-turn secondary through ind_sym's CT path,
        # so it obeys the same N1 adjacency rule as the inductor: at least
        # two levels below secondary_metal (the crossunder occupies
        # secondary_metal-1 and crosses the CT lead path at y=0).
        ct_p = _metal_stack_index_or_none(self.ct_primary_metal) \
            if self.ct_primary_metal is not None else None
        primary = _metal_stack_index_or_none(self.primary_metal)
        if ct_p is not None and primary is not None and ct_p >= primary:
            raise ValueError(
                f"ct_primary_metal {self.ct_primary_metal!r} must sit "
                f"below primary_metal {self.primary_metal!r} (the tap stack "
                "drops from the winding plane)")
        ct_s = _metal_stack_index_or_none(self.ct_secondary_metal) \
            if self.ct_secondary_metal is not None else None
        secondary = _metal_stack_index_or_none(self.secondary_metal)
        if ct_s is not None and secondary is not None and ct_s >= secondary - 1:
            raise ValueError(
                f"ct_secondary_metal {self.ct_secondary_metal!r} must sit "
                f"at least two levels below secondary_metal "
                f"{self.secondary_metal!r}: the secondary's crossunder "
                "occupies secondary_metal-1 and crosses the CT lead path, "
                "shorting the tap net (bug review 2026-07-17 N1)")
        return self


class CleanPortXfmBalunConfig(_FixedXfmPortOrderMixin, _CleanPortDeviceConfigBase):
    renamed: ClassVar[dict[str, str | None]] = {"balun_metal": "metal"}
    primary_outer_diameter_um: float = Field(gt=0)
    secondary_outer_diameter_um: float = Field(gt=0)
    primary_width_um: float = Field(gt=0)
    secondary_width_um: float = Field(gt=0)
    spacing_um: float = Field(gt=0)
    primary_opening_um: float = Field(gt=0)
    secondary_opening_um: float = Field(gt=0)
    primary_lead_length_um: float = Field(gt=0)
    secondary_lead_length_um: float = Field(gt=0)
    primary_turns: int = Field(ge=1)
    secondary_turns: int = Field(ge=1)
    center_spacing_um: float = Field(ge=0, multiple_of=0.01, allow_inf_nan=False)      # half of it is a coordinate: keep it on the grid (D9)
    metal: str = Field(min_length=1)
    ct_primary_metal: str | None = None
    ct_secondary_metal: str | None = None
    primary_port_spacing_um: float | None = Field(default=None, gt=0, multiple_of=0.01)     # M3.1 (the nested secondary's escape leads are fixed)

    @field_validator("metal")
    @classmethod
    def _metal_not_m1_or_m2(cls, value: str) -> str:
        return _forbid_m1_and_m2_metal(value)

    @field_validator("ct_primary_metal", "ct_secondary_metal")
    @classmethod
    def _ct_metals_not_m1(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _forbid_m1_metal(value)

    @model_validator(mode="after")
    def _ct_below_balun_plane(self) -> CleanPortXfmBalunConfig:
        # Both balun windings live on ``metal``; each tap stack drops
        # from that plane (M13 ticket 05; the pcell repeats this guard
        # fail-closed at generate time).
        for ct, label in ((self.ct_primary_metal, "primary"),
                          (self.ct_secondary_metal, "secondary")):
            if ct is None:
                continue
            c = _metal_stack_index_or_none(ct)
            h = _metal_stack_index_or_none(self.metal)
            if c is not None and h is not None and c >= h:
                raise ValueError(
                    f"ct_{label}_metal {ct!r} must sit below metal "
                    f"{self.metal!r} (the tap stack drops from the "
                    "winding plane)")
        return self

    @model_validator(mode="after")
    def _secondary_nests_inside_primary(self) -> CleanPortXfmBalunConfig:
        # Coplanar rings couple only when one sits inside the other; two
        # rings that do not overlap are two inductors, not a balun (user
        # directive 2026-09-22 retired the side-by-side mode; the pcell
        # repeats this guard fail-closed at generate time).
        x_p, x_s = -self.center_spacing_um / 2.0, self.center_spacing_um / 2.0
        od_p, od_s = self.primary_outer_diameter_um, self.secondary_outer_diameter_um
        nested = (x_s - od_s / 2.0 > x_p - od_p / 2.0 + 1e-9
                  and x_s + od_s / 2.0 < x_p + od_p / 2.0 - 1e-9)
        if not nested:
            raise ValueError(
                f"the secondary (od {od_s}) must nest inside the primary (od "
                f"{od_p}) at center_spacing_um {self.center_spacing_um}: "
                "coplanar rings that do not overlap are two inductors, not a balun")
        return self


class CleanPortXfmTwConfig(_FixedXfmPortOrderMixin, _CleanPortDeviceConfigBase):
    """Type 3 same-layer overlapping-inductor ("twisted") transformer: NR
    concentric octagon rings shared half-and-half by P (CCW) and S (P's
    x-mirror, CW), each NR/2 turns, connected across the NR-1 ring
    boundaries by explicit dive/same-layer legs. Both windings share ONE
    metal plane (``metal``) and ONE width (width_um) -- there is no
    separate primary / secondary width the way xfm_bs/xfm_ms/xfm_balun
    have, since P and S occupy the SAME rings and so share their width.

    No CT: xfm_tw has no tap winding at all (a non-goal of the family), so
    unlike every other xfm config in this module there is no ct_primary_metal/
    ct_secondary_metal field to opt into -- any attempt to pass one (or any
    other CT-shaped field) is just an unknown field, rejected by the base
    class's extra="forbid" the same as any other typo.
    """

    renamed: ClassVar[dict[str, str | None]] = {"top_metal": "metal", "opening_p_um": "port_gap_p_um", "opening_n_um": "port_gap_n_um"}
    outer_diameter_um: float = Field(gt=0)
    width_um: float = Field(gt=0)
    spacing_um: float = Field(gt=0)
    # NR (ring count): deliberately not called "turns" like the other xfm
    # configs' turn-count fields -- each winding's own turn count is the
    # fraction ring_count/2 (spec.md), so reusing "turns" here would
    # misrepresent it.
    ring_count: int
    # ticket 02c semantics (mirrors xfm_tw's own pcell docstring): the two
    # P-side (P1/P2) port stubs' INNER edges -- the edges facing the coil
    # centre -- sit exactly port_gap_p_um apart in TOTAL (each stub's own
    # centreline is offset +-(port_gap_p_um/2 + width_um/2) from the
    # x-axis). port_gap_n_um is the same rule for the two N-side (N1/N2)
    # stubs. This is NOT "each stub is port_gap_p_um/port_gap_n_um from the
    # coil centreline" -- that earlier (ticket 01/02) reading put the inner
    # edges 2x too far apart and was corrected in ticket 02c.
    port_gap_p_um: float = Field(gt=0)
    port_gap_n_um: float = Field(gt=0)
    lead_length_um: float = Field(gt=0)
    metal: str = Field(min_length=1)

    @field_validator("metal")
    @classmethod
    def _metal_not_m1_or_m2(cls, value: str) -> str:
        # The dive legs at every ring boundary land on metal-1
        # implicitly -- there is no separate config field for the dive
        # layer (same situation as xfm_ms's secondary_metal / xfm_balun's
        # metal): M2 would put that implicit dive layer on M1 even
        # though M1 was never named directly.
        return _forbid_m1_and_m2_metal(value)

    @field_validator("ring_count")
    @classmethod
    def _ring_count_odd_and_at_least_three(cls, value: int) -> int:
        # Mirrors xfm_tw's own _tw_plan precondition (`if NR < 3 or
        # NR % 2 == 0`) so the config-level and pcell-level rejections read
        # as the same rule caught twice, not two different rules.
        if value < 3 or value % 2 == 0:
            raise ValueError(
                f"ring_count={value} must be an odd integer >= 3 (NR "
                "concentric rings shared by P/S, NR/2 turns per winding -- "
                "matches xfm_tw's own _tw_plan precondition, spec.md)"
            )
        return value


class CleanPortXfmIlConfig(_FixedXfmPortOrderMixin, _CleanPortDeviceConfigBase):
    """Type 3 same-layer interleaved ("Rabjohn/Frlan") transformer: P and S
    alternate radial bands on ONE metal plane (``metal``), each turn's
    crossunder split across metal-1 (leg1) and metal-2 (leg2). Both windings
    share ONE width (width_um) and ONE spacing (spacing_um) -- unequal
    winding widths are not supported, the same rationale as xfm_tw's
    shared width_um; the CT tap leads also reuse
    that same shared W (the pcell's ``_il_ct_tap_exact`` draws every lead,
    tap included, at W).

    ``turns`` is a SINGLE field mapping NT_P=NT_S=turns (user directive
    2026-07-19, ticket 03c): primary/secondary turn counts must be EQUAL,
    and this field makes the unequal case structurally unrepresentable
    instead of accepting two turn counts and validating they match. The
    floor >=2 mirrors the pcell's own NT_P>=2 guard (a single interleaved
    turn has no crossover to interleave against -- use xfm_bs/xfm_balun
    for 1+1 same-layer windings instead).

    ``ct_primary_metal``/``ct_secondary_metal`` (both optional, default
    None -> no tap) are DIRECTION-DISPATCHED by comparing the CT metal to
    ``metal`` (ticket 03c, mirroring the pcell's own
    ``_il_ct_metal_guard``): ABOVE it taps UPWARD (no adjacency
    floor -- nothing else this device ever draws is above the coil plane);
    BELOW it taps DOWNWARD (must sit at least THREE levels below --
    metal-1 is leg1, metal-2 is leg2, so the floor is
    metal-3); EQUAL to it is always illegal (the coil's own
    layer, not a separate tap plane). **CTP cannot tap downward in ANY
    legal configuration** -- the interleaved lattice always sandwiches a P
    ring inside a same-reach S bridge gap, proven by sweep in the pcell's
    own docstring/tests -- so in practice ct_primary_metal needs an
    UPWARD value (e.g. "10" or "AP" on a top_metal="9" body); on a
    metal="AP" body specifically there is no metal above it, so CTP
    cannot be tapped in EITHER direction, a genuine architectural limit of
    that body, not a bug. CTS's downward tap is the structural mirror
    opposite -- always clear (S is always this device's innermost
    winding) -- so ct_secondary_metal works in EITHER direction on every
    body. This config only checks the direction/floor rule above; the
    exact-midpoint blocked-window failure (a downward CTP colliding with
    the other winding's crossunder) is geometry-dependent and is left to
    the pcell's own fail-closed check at generate() time.
    """

    renamed: ClassVar[dict[str, str | None]] = {
        "top_metal": "metal", "opening_p_um": "primary_opening_um", "opening_s_um": "secondary_opening_um",
        "lead_p_um": "primary_lead_length_um", "lead_s_um": "secondary_lead_length_um",
    }
    outer_diameter_um: float = Field(gt=0)
    width_um: float = Field(gt=0)
    spacing_um: float = Field(gt=0)
    turns: int = Field(ge=2)
    primary_opening_um: float = Field(gt=0)
    secondary_opening_um: float = Field(gt=0)
    primary_lead_length_um: float = Field(gt=0)
    secondary_lead_length_um: float = Field(gt=0)
    metal: str = Field(min_length=1)
    ct_primary_metal: str | None = None
    ct_secondary_metal: str | None = None

    @field_validator("metal")
    @classmethod
    def _metal_not_m1_through_m3(cls, value: str) -> str:
        # leg2 lands TWO stack levels below metal (ticket 02d) -- no
        # separate config field for it, same situation as xfm_tw's dive
        # layer / xfm_ms's multi_metal crossunder.
        return _forbid_m1_through_m3_metal(value)

    @field_validator("ct_primary_metal", "ct_secondary_metal")
    @classmethod
    def _ct_metals_not_m1(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _forbid_m1_metal(value)

    @model_validator(mode="after")
    def _ct_metal_direction_and_floor(self) -> CleanPortXfmIlConfig:
        top = _metal_stack_index_or_none(self.metal)
        # The floor is the real leg2 conductor (pcell _il_ct_adjacency_guard):
        # on a stack with gaps (n65_1p9m has no M10) that is NOT top-2, so
        # read the profile when one is named (port contract follow-up
        # 2026-09-22); the pure numeric floor stays the reference-mode rule.
        leg2 = None if top is None else top - 2
        if top is not None and _profile_known(self.process_profile):
            leg2 = _real_leg2_index(self.metal, self.process_profile)
        for ct, label in ((self.ct_primary_metal, "primary"),
                          (self.ct_secondary_metal, "secondary")):
            if ct is None:
                continue
            c = _metal_stack_index_or_none(ct)
            if top is None or c is None:
                continue
            if c == top:
                raise ValueError(
                    f"ct_{label}_metal {ct!r} cannot equal metal "
                    f"{self.metal!r} -- that is the coil ring's own "
                    "layer, not a separate tap plane (xfm_il ticket 03c)")
            if c < top and (leg2 is None or c >= leg2):
                raise ValueError(
                    f"ct_{label}_metal {ct!r} must sit at least three "
                    f"levels below metal {self.metal!r}: xfm_il's "
                    "dual-layer crossunder occupies both metal-1 and "
                    "metal-2 (ticket 02d), so a CT on either layer "
                    "galvanically shorts the tap net to the mid-winding "
                    "bridges (ticket 03c); use a downward metal at or "
                    f"below metal-3, or an upward metal above "
                    f"{self.metal!r} instead")
        return self


def _profile_known(profile_id: str) -> bool:
    """Whether a rule profile of this id can be loaded (config validation
    must not fail on a profile the pcell will reject with its own message)."""
    from ic_opt.em.pcell.process_rules import get_process_rule_profile
    try:
        get_process_rule_profile(profile_id)
    except Exception:
        return False
    return True


def _real_leg2_index(top_metal: str, profile_id: str) -> int | None:
    """Stack index of the conductor two real levels below ``top_metal`` in
    the profile, or None when the stack is not deep enough (the pcell then
    fails closed by itself)."""
    from ic_opt.em.pcell.process_rules import get_process_rule_profile
    conductors = get_process_rule_profile(profile_id).layer_catalog.conductors
    below = [i for i in range(1, _metal_stack_index_or_none(top_metal))
             if (("AP" if i == 11 else f"M{i}") in conductors)]
    return below[-2] if len(below) >= 2 else None


def _auto_stub_widths(config) -> dict[str, float]:
    """Each port's own lead width — the auto default for stub widths.

    The inductor's every lead (P/N and the CT tap) is width_um wide; on the
    two-winding devices the P1/N1/CTP leads carry the primary/single width
    and P2/N2/CTS the secondary/multi width. xfm_tw and xfm_il both draw
    every port (taps included) at the SAME width_um (P and S share the
    winding's own W, spec.md for each), same rule as ind_sym's single
    winding."""
    if isinstance(config, (CleanPortIndSymConfig, CleanPortXfmTwConfig,
                           CleanPortXfmIlConfig)):
        return {name: config.width_um for name in config.port_order}
    if isinstance(config, (CleanPortXfmBsConfig, CleanPortXfmMsConfig, CleanPortXfmBalunConfig)):
        p_w, s_w = config.primary_width_um, config.secondary_width_um
    else:  # fail closed: an unmapped device must not silently guess
        raise TypeError(
            f"no auto stub-width rule for config type {type(config).__name__}")
    side = {"P1": p_w, "N1": p_w, "CTP": p_w,
            "P2": s_w, "N2": s_w, "CTS": s_w}
    return {name: side[name] for name in config.port_order}


def _build_fixture(p, fixture: CleanPortGroundFixtureConfig,
                   auto_widths: dict[str, float] | None = None):
    stub_width = fixture.stub_width_um
    by_port = fixture.stub_width_by_port_um
    if stub_width is None:
        if not auto_widths:
            raise ValueError(
                "ground_fixture.stub_width_um is unset and the generator "
                "supplied no per-port lead widths (fail closed)")
        merged = dict(auto_widths)
        if by_port:
            merged.update(by_port)
        # Every port is covered by the map, so the global value never draws
        # anything — it only has to be a valid width.
        stub_width = next(iter(merged.values()))
        by_port = merged
    return p.GroundFixtureConfig(
        inner_margin_um=fixture.inner_margin_um,
        ring_width_um=fixture.ring_width_um,
        stub_width_um=stub_width,
        stub_length_um=fixture.stub_length_um,
        stub_chamfer_um=fixture.stub_chamfer_um,
        stub_width_by_port_um=by_port,
    )


def audit_via_landing(gds_path: Path, process_profile: str) -> dict:
    """Fail closed if any via cut lacks landing metal on BOTH connected layers.

    This is the defect class that made EMX's mesher diverge and froze the
    server on 2026-07-09 (72/144 floating VIA9 cuts from the old src CT
    generator). Layer pairs come from the process rule profile — nothing is
    hardcoded.
    """
    import klayout.db as kdb

    adapter = get_geometry_rule_adapter(process_profile)
    ly = kdb.Layout()
    ly.read(str(gds_path))
    tops = ly.top_cells()
    if len(tops) != 1:
        raise ValueError(
            f"via landing audit requires exactly one top cell, got {len(tops)}")
    top = tops[0]
    top.flatten(True)

    def region(ld: tuple[int, int]) -> kdb.Region:
        li = ly.find_layer(kdb.LayerInfo(ld[0], ld[1]))
        if li is None:
            return kdb.Region()
        reg = kdb.Region()
        for shape in top.shapes(li).each():
            if not shape.is_text():
                reg.insert(shape.polygon)
        return reg.merged()

    checked = 0
    violations: list[str] = []
    for via_name, via in adapter.profile.layer_catalog.vias.items():
        cuts = region(tuple(via.drawing))
        if cuts.is_empty():
            continue
        checked += 1
        lower, upper = via.connects
        landing = (region(tuple(adapter.layer(lower).drawing))
                   & region(tuple(adapter.layer(upper).drawing))).merged()
        floating = (cuts - landing).merged()
        if not floating.is_empty():
            violations.append(
                f"{via_name}: {floating.count()} cut region(s), "
                f"{floating.area()} dbu^2 without {lower}&{upper} landing")
    if violations:
        raise ValueError(
            "via landing audit failed (cuts without metal landing): "
            + "; ".join(violations))
    return {"status": "pass", "vias_checked": checked}


def audit_port_lattice(gds_path: Path, ports: list[dict], process_profile: str) -> dict:
    """Independent post-write port audit (port contract 2026-09-21,
    spec.md's "加一层独立复核"). Re-parses the just-written GDS -- an
    independent source from the in-memory ``Cell`` -- and, for every port
    the manifest names, asserts closed:

    * its exact point sits on an edge of its own ``lead_zone_nm`` (not
      merely inside it);
    * that exact point carries its own text label, verbatim, on
      ``label_layer``, in the file klayout actually wrote;
    * that exact point lands on real drawn metal on its own conductor
      layer, in the same file.

    Zero tolerance, no nearest-point search: every check looks at the
    EXACT point the manifest names, never the nearest label or metal -- a
    search-based check would risk crediting a neighbouring port's own
    glyph (a dense CT tap, stacked windings) instead of catching the
    mis-registration it exists to catch. This is deliberately independent
    of ``_check_port_lattice_invariant`` (run inside
    ``finalize_emx_ports``, at generation time): that check compares two
    pieces of in-memory data the SAME code path produced and cannot catch
    a bug in ``write_gds`` itself; this one only trusts what klayout
    parses back out of the file.

    The "exact point" is ``port["point_nm"]`` -- the authoritative
    integer coordinate every family's ``finalize_emx_ports()`` call
    composes through the same integer transform chain shapes/labels use,
    read directly (no derived/robust indirection needed: every family
    finalizes its own ports the same way, so ``point_nm`` and
    ``label_xy_um`` can never diverge).
    """
    import klayout.db as kdb

    adapter = get_geometry_rule_adapter(process_profile)
    ly = kdb.Layout()
    ly.read(str(gds_path))
    tops = ly.top_cells()
    if len(tops) != 1:
        raise ValueError(
            f"port lattice audit requires exactly one top cell, got {len(tops)}")
    top = tops[0]
    top.flatten(True)

    def polygons(ld: tuple[int, int]) -> list[kdb.Polygon]:
        li = ly.find_layer(kdb.LayerInfo(ld[0], ld[1]))
        if li is None:
            return []
        return [s.polygon for s in top.shapes(li).each() if not s.is_text()]

    def texts(ld: tuple[int, int]) -> set[tuple[str, int, int]]:
        li = ly.find_layer(kdb.LayerInfo(ld[0], ld[1]))
        if li is None:
            return set()
        return {
            (s.text_string, s.text.x, s.text.y)
            for s in top.shapes(li).each() if s.is_text()
        }

    for port in ports:
        name = port["name"]
        px, py = port["point_nm"]
        x0, y0, x1, y1 = port["lead_zone_nm"]
        # The port faces out of the one zone edge it sits on (port contract
        # 2026-09-21; the orientation is the edge, recorded at
        # registration). Bounded on both axes, equality on the edge the
        # orientation names -- a zone shifted along the orthogonal axis
        # would still share the other bound and must not pass.
        xlo, xhi = min(x0, x1), max(x0, x1)
        ylo, yhi = min(y0, y1), max(y0, y1)
        edge = {0: px == xhi, 180: px == xlo, 90: py == yhi, 270: py == ylo}[port["orientation_deg"]]
        if not (edge and xlo <= px <= xhi and ylo <= py <= yhi):
            raise ValueError(
                f"port lattice audit failed: {name} point=({px}, {py}) "
                f"is not on the {port['orientation_deg']}-degree edge of "
                f"its own lead_zone_nm={port['lead_zone_nm']}"
            )
        label_layer = tuple(port["label_layer"])
        if (name, px, py) not in texts(label_layer):
            raise ValueError(
                f"port lattice audit failed: {name} has no {label_layer} "
                f"text '{name}' at exactly ({px}, {py}) in "
                f"{gds_path.name}"
            )
        metal_ld = tuple(adapter.layer(port["metal"]).drawing)
        point = kdb.Point(px, py)
        if not any(poly.inside(point) for poly in polygons(metal_ld)):
            raise ValueError(
                f"port lattice audit failed: {name} point ({px}, {py}) "
                f"does not land on drawn metal on layer {metal_ld} "
                f"({port['metal']}) in {gds_path.name}"
            )
    return {"status": "pass", "ports_checked": len(ports)}


def _manifest_port(port: dict) -> dict:
    """The port as the file describes it (M2.1): where it is, which way it faces, how wide its lead is, what it pairs with."""
    return {
        "name": port["name"], "logical_name": port["logical_name"], "reference": port["reference"], "metal": port["metal"],
        "x_um": port["label_xy_um"][0], "y_um": port["label_xy_um"][1], "width_um": port["width_um"],
        "orientation_deg": port["orientation_deg"], "pair": port["pair"],
        "lead_zone_um": [round(v * 0.001, 3) for v in port["lead_zone_nm"]],
    }


def _write_geometry_outputs(
    p, cell, config: _CleanPortDeviceConfigBase, *, generator_id: str,
    outdir: Path, gds_name: str, requires_vias: bool,
) -> GeometryGenerationResult:
    """Shared output seam for all clean-port generators; `p` is the loaded clean-port module handle. Runs inside the
    profile's metal stack, like the build: anything here that turns a stack position back into a conductor (the
    shield, the audits) must read the same stack the positions came from."""
    from ic_opt.em.pcell.stack import use_stack

    with use_stack(config.process_profile):
        return _write_geometry_outputs_in_stack(p, cell, config, generator_id=generator_id, outdir=outdir,
                                                gds_name=gds_name, requires_vias=requires_vias)


def _write_geometry_outputs_in_stack(
    p, cell, config: _CleanPortDeviceConfigBase, *, generator_id: str,
    outdir: Path, gds_name: str, requires_vias: bool,
) -> GeometryGenerationResult:
    gds_name = validate_output_file_name(gds_name, "gds_name")
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    # write_gds names the GDS top cell after the sanitized filename stem, and
    # that is the name EMX gets (the top cell IS the file name; there is no override).
    resolved_top_cell = re.sub(r"[^A-Za-z0-9_$?]", "_", Path(gds_name).stem)
    cell.name = resolved_top_cell
    gds_path = outdir / gds_name
    pgs = getattr(config, "pgs", None)
    pgs_geometry = (add_pgs(cell, pgs, config.process_profile,
                            config.ground_fixture.ring_width_um)
                    if pgs is not None else None)
    p.write_gds(cell, gds_path)

    emx_ports_path = outdir / "emx_ports.txt"
    port_lines = p.emx_port_lines(cell.emx_ports)
    emx_ports_path.write_text("\n".join(port_lines) + "\n", encoding="utf-8")

    audit = audit_via_landing(gds_path, config.process_profile)
    vias_checked = audit.get("vias_checked", 0)
    if requires_vias and vias_checked < 1:
        raise ValueError(
            "via landing audit checked no via layers; this generator "
            "declares requires_vias=True, so an empty audit is a bug")
    if not requires_vias and vias_checked != 0:
        raise ValueError(
            "via landing audit found via layers but this generator "
            "declares requires_vias=False; update the generator's "
            "declaration if the device legitimately gained vias")
    port_audit = audit_port_lattice(gds_path, cell.emx_ports, config.process_profile)

    manifest_path = outdir / "geometry_manifest.json"
    manifest_path.write_text(json.dumps({
        "schema_version": "1.1",
        "generator_id": generator_id,
        "geometry_version": GEOMETRY_VERSION,
        "geometry": {"config": config.model_dump(mode="json")},
        "stack": get_geometry_rule_adapter(config.process_profile).stack_summary(),      # the 3D data this geometry assumes (M2.3)
        "ports": [_manifest_port(port) for port in cell.emx_ports],
        "suggested_emx_ports": port_lines,
        "suggested_emx_ports_note": (
            "EMX orders sNp ports lexicographically by EMX port name, "
            "independently of the -p argument or ports list order. These "
            "suggested names are sorted; if project emx.yaml overrides them, "
            "sort the actual EMX names and use their signal/reference "
            "mapping for Nport bindings. Zero-padded p01, p02, ... names "
            "preserve the intended numeric order."),
        "via_landing_audit": audit,
        "port_lattice_audit": port_audit,
        **({"pgs_geometry": pgs_geometry} if pgs_geometry is not None else {}),
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return GeometryGenerationResult(
        generator_id=generator_id, gds_path=gds_path,
        top_cell=resolved_top_cell, manifest_path=manifest_path,
        emx_ports_path=emx_ports_path)


def translate_config(generator_id: str, config: dict) -> dict:
    """An old record's config in today's vocabulary (M2.2): renamed fields renamed, removed fields dropped."""
    renamed = PLUGIN_GENERATORS[generator_id].config_model.renamed
    return {renamed.get(k, k): v for k, v in config.items() if renamed.get(k, k) is not None}


class _CleanPortGenerator(PassiveDeviceGenerator):
    """The six built-in families share the package's geometry generation."""

    geometry_version = GEOMETRY_VERSION


class CleanPortIndSymGenerator(_CleanPortGenerator):
    generator_id = "clean_port_ind_sym"
    config_model = CleanPortIndSymConfig

    def generate(self, config, *, outdir, gds_name):
        p = _clean_port()
        cell = p.ind_sym(
            OD=config.outer_diameter_um, W=config.width_um,
            OPENING=config.opening_um, LEAD=config.lead_length_um,
            S=config.spacing_um, NT=config.turns,
            TOP_ME=config.metal, BTM_ME=config.metal,      # BTM_ME is the reference chain's documented dead parameter
            CT_ME=config.ct_metal,
            STRAIGHT_EXTENSION=config.straight_extension_um,
            port_order=list(config.port_order),
            ground_fixture=_build_fixture(p, config.ground_fixture,
                                          _auto_stub_widths(config)),
            process=p.process_rule_context(config.process_profile),
            PORT_SPACING=config.port_spacing_um,
        )
        return _write_geometry_outputs(
            p, cell, config, generator_id=self.generator_id,
            outdir=outdir, gds_name=gds_name,
            requires_vias=(config.turns >= 2 or config.ct_metal is not None))


class CleanPortXfmBsGenerator(_CleanPortGenerator):
    generator_id = "clean_port_xfm_bs"
    config_model = CleanPortXfmBsConfig

    def generate(self, config, *, outdir, gds_name):
        p = _clean_port()
        cell = p.xfm_bs(
            OD_P=config.primary_outer_diameter_um,
            OD_S=config.secondary_outer_diameter_um,
            W_P=config.primary_width_um, W_S=config.secondary_width_um,
            OPENING_P=config.primary_opening_um,
            OPENING_S=config.secondary_opening_um,
            LEAD_P=config.primary_lead_length_um,
            LEAD_S=config.secondary_lead_length_um,
            CENTER_SPACING=config.center_spacing_um,
            PRI_ME=config.primary_metal, SEC_ME=config.secondary_metal,
            CT_P_ME=config.ct_primary_metal,
            CT_S_ME=config.ct_secondary_metal,
            STRAIGHT_EXTENSION=config.straight_extension_um,
            ground_fixture=_build_fixture(p, config.ground_fixture,
                                          _auto_stub_widths(config)),
            process=p.process_rule_context(config.process_profile),
            PORT_SPACING_P=config.primary_port_spacing_um,
            PORT_SPACING_S=config.secondary_port_spacing_um,
        )
        # the broadside windings are via-less; the tap stacks are the only
        # via source, so via expectation follows the CT fields (M13)
        has_ct = (config.ct_primary_metal is not None
                  or config.ct_secondary_metal is not None)
        return _write_geometry_outputs(
            p, cell, config, generator_id=self.generator_id,
            outdir=outdir, gds_name=gds_name,
            requires_vias=has_ct)


class CleanPortXfmMsGenerator(_CleanPortGenerator):
    generator_id = "clean_port_xfm_ms"
    config_model = CleanPortXfmMsConfig

    def generate(self, config, *, outdir, gds_name):
        p = _clean_port()
        cell = p.xfm_ms(
            OD_S=config.primary_outer_diameter_um,
            OD_M=config.secondary_outer_diameter_um,
            W_S=config.primary_width_um, W_M=config.secondary_width_um,
            OPENING_S=config.primary_opening_um,
            OPENING_M=config.secondary_opening_um,
            LEAD_S=config.primary_lead_length_um,
            LEAD_M=config.secondary_lead_length_um,
            NT_M=config.secondary_turns, S_M=config.secondary_spacing_um,
            CENTER_SPACING=config.center_spacing_um,
            SINGLE_ME=config.primary_metal, MULTI_ME=config.secondary_metal,
            CT_P_ME=config.ct_primary_metal,
            CT_S_ME=config.ct_secondary_metal,
            ground_fixture=_build_fixture(p, config.ground_fixture,
                                          _auto_stub_widths(config)),
            process=p.process_rule_context(config.process_profile),
            STRAIGHT_EXTENSION=config.straight_extension_um,
            PORT_SPACING_S=config.primary_port_spacing_um,
            PORT_SPACING_M=config.secondary_port_spacing_um,
        )
        return _write_geometry_outputs(
            p, cell, config, generator_id=self.generator_id,
            outdir=outdir, gds_name=gds_name,
            requires_vias=True)


class CleanPortXfmBalunGenerator(_CleanPortGenerator):
    generator_id = "clean_port_xfm_balun"
    config_model = CleanPortXfmBalunConfig

    def generate(self, config, *, outdir, gds_name):
        p = _clean_port()
        cell = p.xfm_balun(
            OD_P=config.primary_outer_diameter_um,
            OD_S=config.secondary_outer_diameter_um,
            W_P=config.primary_width_um, W_S=config.secondary_width_um,
            S=config.spacing_um,
            OPENING_P=config.primary_opening_um,
            OPENING_S=config.secondary_opening_um,
            LEAD_P=config.primary_lead_length_um,
            LEAD_S=config.secondary_lead_length_um,
            NT_P=config.primary_turns, NT_S=config.secondary_turns,
            CENTER_SPACING=config.center_spacing_um,
            BALUN_ME=config.metal,
            CT_P_ME=config.ct_primary_metal,
            CT_S_ME=config.ct_secondary_metal,
            ground_fixture=_build_fixture(p, config.ground_fixture,
                                          _auto_stub_widths(config)),
            process=p.process_rule_context(config.process_profile),
            PORT_SPACING_P=config.primary_port_spacing_um,
        )
        # vias exist only when something actually drops below the balun
        # plane: the nested-mode escape crossunder, a multi-turn winding's
        # ind_sym crossover, or a CT tap stack. A plain side-by-side NT=1
        # balun is via-less (round-1 false-reject fix, M13 ticket 05).
        return _write_geometry_outputs(
            p, cell, config, generator_id=self.generator_id,
            outdir=outdir, gds_name=gds_name,
            requires_vias=True)


class CleanPortXfmTwGenerator(_CleanPortGenerator):
    generator_id = "clean_port_xfm_tw"
    config_model = CleanPortXfmTwConfig

    def generate(self, config, *, outdir, gds_name):
        p = _clean_port()
        cell = p.xfm_tw(
            OD=config.outer_diameter_um,
            W=config.width_um,
            S=config.spacing_um,
            NR=config.ring_count,
            OPENING_P=config.port_gap_p_um,
            OPENING_N=config.port_gap_n_um,
            LEAD=config.lead_length_um,
            SL_ME=config.metal,
            port_order=list(config.port_order),
            ground_fixture=_build_fixture(p, config.ground_fixture,
                                          _auto_stub_widths(config)),
            process=p.process_rule_context(config.process_profile),
        )
        # every ring boundary crossing draws a dive leg with vias() at both
        # endpoints (spec.md: NR-1 dive legs per winding, NR>=3 so always
        # >=2 total) -- xfm_tw is unconditionally via-ful, unlike
        # xfm_bs/xfm_balun where vias depend on an optional CT/nested mode.
        return _write_geometry_outputs(
            p, cell, config, generator_id=self.generator_id,
            outdir=outdir, gds_name=gds_name,
            requires_vias=True)


class CleanPortXfmIlGenerator(_CleanPortGenerator):
    generator_id = "clean_port_xfm_il"
    config_model = CleanPortXfmIlConfig

    def generate(self, config, *, outdir, gds_name):
        p = _clean_port()
        cell = p.xfm_il(
            OD=config.outer_diameter_um,
            W=config.width_um,
            S=config.spacing_um,
            NT_P=config.turns, NT_S=config.turns,
            OPENING_P=config.primary_opening_um, OPENING_S=config.secondary_opening_um,
            LEAD_P=config.primary_lead_length_um, LEAD_S=config.secondary_lead_length_um,
            SL_ME=config.metal,
            CT_P_ME=config.ct_primary_metal,
            CT_S_ME=config.ct_secondary_metal,
            port_order=list(config.port_order),
            ground_fixture=_build_fixture(p, config.ground_fixture,
                                          _auto_stub_widths(config)),
            process=p.process_rule_context(config.process_profile),
        )
        # turns>=2 (config floor) always draws the interleaved crossunder's
        # two independent legs (ticket 02d), so xfm_il is unconditionally
        # via-ful -- same rationale as xfm_tw's own dive legs.
        return _write_geometry_outputs(
            p, cell, config, generator_id=self.generator_id,
            outdir=outdir, gds_name=gds_name,
            requires_vias=True)


PLUGIN_GENERATORS: dict[str, PassiveDeviceGenerator] = {
    CleanPortIndSymGenerator.generator_id: CleanPortIndSymGenerator(),
    CleanPortXfmBsGenerator.generator_id: CleanPortXfmBsGenerator(),
    CleanPortXfmMsGenerator.generator_id: CleanPortXfmMsGenerator(),
    CleanPortXfmBalunGenerator.generator_id: CleanPortXfmBalunGenerator(),
    CleanPortXfmTwGenerator.generator_id: CleanPortXfmTwGenerator(),
    CleanPortXfmIlGenerator.generator_id: CleanPortXfmIlGenerator(),
}

# Retired generator ids -> migration recipe. The registry surfaces these
# fail-closed when an old requirement file still names a retired id (the
# M12 precedent: legacy built-ins were removed with a documented mapping).
RETIRED_GENERATOR_IDS: dict[str, str] = {
    "clean_port_ind_sym_ct": (
        "the standalone CT inductor was unified into clean_port_ind_sym in "
        "M13: set id: clean_port_ind_sym, add ct_metal: \"<CT metal, e.g. "
        "8>\" to fixed_parameters (at least two levels below metal), "
        "and set port_order: [P1, N1, CT]; all other fields are unchanged "
        "(bottom_metal stays the winding-chain field)."
    ),
}
