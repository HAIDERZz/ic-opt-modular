"""The library manifest (``library.yaml`` at the library root): which run stores make up each stratum and how its query quantities are defined.

A library is a directory of ordinary ic-opt run stores (projects built by ``sim.evaluate`` on an em_only
spec) plus this manifest. A stratum is one device family on one metal body; it may be assembled from
several stores ("parts") that were swept with different EMX settings, e.g. single-turn inductors swept
further in frequency. Quantities are named after the measure kernel: scalars (``Lp_lf``, ``Qp_peak``,
``SRF_p``, ``k_lf`` ...) or curves sampled at anchor frequencies (``Lp`` with ``anchors_ghz: [28]`` gives the
column ``Lp@28``).

A quantity may set ``rel_sigma_max``, the confidence ceiling on sigma / mu for its columns (every anchor of a
curve): a prediction less sure than that is ``uncertain``. It takes effect where a call gives none -- the
precedence is a call's explicit ``rel_sigma_max``, then the quantity's, then ``domain.DEFAULT_SIGMA_REL_MAX``
(``Library.rel_sigma_max``) -- and it is no part of any cache key: setting it refits nothing.

A curve's ``model`` says how its columns are predicted: ``direct`` (the default) fits one model per column;
``ratio`` and ``resonance`` build the column from the stratum's low-frequency scalar (and, for ``resonance``, its
system SRF) and fit only what is left (``ic_opt.library.composed``). Unlike the ceiling, a ``model`` other than
``direct`` is part of the dataset's cache key (it changes what is fitted); ``direct`` stays out of it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, PositiveFloat, field_validator, model_serializer, model_validator

from ic_opt.spec import Model

MANIFEST = "library.yaml"
SCHEMA = "ic-opt-library-v1"
SCALARS = ("Lp_lf", "Lp_res", "Qp_peak", "SRF_p", "Ls_lf", "Ls_res", "Qs_peak", "SRF_s", "k_lf", "SRF")   # SRF: the system SRF
CURVES = ("Lp", "Qp", "Ls", "Qs", "k")
PEAKS = ("Qp_peak", "Qs_peak")
LOW_FREQUENCY = {"Lp": "Lp_lf", "Ls": "Ls_lf", "k": "k_lf"}   # the scalar a ratio / resonance curve (Quantity.model) is built on
RESONANCE_CURVES = ("Lp", "Ls")                           # inductances: the ones that rise towards the self-resonance
XFM_BS_DIMS = ("primary_outer_diameter_um", "secondary_outer_diameter_um", "primary_width_um", "secondary_width_um", "center_spacing_um")
XFM_MS_DIMS = (*XFM_BS_DIMS, "secondary_spacing_um", "secondary_turns")
FEATURE_MAP_DIMS = {"xfm_bs_dimensionless": XFM_BS_DIMS, "xfm_ms_dimensionless": XFM_MS_DIMS}   # a map consumes exactly these dims


class Part(Model):
    store: str = Field(min_length=1)                      # project directory relative to the library root (holds .icopt/)
    pipeline_fingerprint: str | None = None               # pin a generation; default: the part's most common one among ok rows


class Quantity(Model):
    band_ghz: float | None = Field(default=None, gt=0)    # peaks only: search 0 < f <= band (and below the system SRF), the same for every part
    anchors_ghz: list[float] = Field(default_factory=list)   # curves only: sample at these frequencies
    srf_margin: float = Field(default=1.25, ge=1.0)       # an anchored row is usable at f0 only if its resonance lies above margin x f0
    feature_map: str | None = None                        # the model's input features (FEATURE_MAP_DIMS; default: the dims themselves)
    rel_sigma_max: float | None = Field(default=None, gt=0, allow_inf_nan=False)   # confidence ceiling on sigma / mu; None: the default
    model: Literal["direct", "ratio", "resonance"] = "direct"   # curves: one model per column, or built on LOW_FREQUENCY (and SRF)

    @field_validator("anchors_ghz")
    @classmethod
    def _positive_anchors(cls, value: list[float]) -> list[float]:
        if any(f <= 0 for f in value) or len(set(value)) != len(value):
            raise ValueError("anchors_ghz must be distinct positive frequencies")
        return sorted(value)

    @model_serializer(mode="wrap")
    def _dump(self, handler):
        """``model: direct``, the default, stays out of the dump: a stratum's dump is part of its dataset's cache key (and so of
        every calibration and model cached on it), so a library that never names ``model`` keeps its caches."""
        data = handler(self)
        if self.model == "direct":
            data.pop("model", None)
        return data


class Stratum(Model):
    generator: str = Field(min_length=1)
    dims: list[str] = Field(min_length=1)                 # observation parameters that locate a point
    nt_dim: str | None = None                             # the integer turns dimension, if the family has one
    parts: list[Part] = Field(min_length=1)
    quantities: dict[str, Quantity] = Field(min_length=1)
    steps: dict[str, float] = Field(default_factory=dict)  # candidate resolution per dim for inverse queries (e.g. outer_diameter_um: 1)
    low_freq_max_hz: PositiveFloat | Literal["relative"] | None = None   # top of the L*_lf / k_lf band for every part; None: each part's spec

    @model_validator(mode="after")
    def _consistent(self) -> Stratum:
        if self.nt_dim is not None and self.nt_dim not in self.dims:
            raise ValueError(f"nt_dim {self.nt_dim!r} is not one of the dims {self.dims}")
        stray = [d for d in self.steps if d not in self.dims]
        if stray or any(v <= 0 for v in self.steps.values()):
            raise ValueError(f"steps must be positive and name dims {self.dims}; got {self.steps}")
        if len({p.store for p in self.parts}) != len(self.parts):
            raise ValueError("a store appears twice in parts")
        for name, q in self.quantities.items():
            if name in SCALARS:
                if q.anchors_ghz:
                    raise ValueError(f"{name} is a scalar; anchors_ghz belongs to the curves {CURVES}")
                if q.band_ghz is not None and name not in PEAKS:
                    raise ValueError(f"band_ghz applies to the peak quantities {PEAKS}, not {name}")
            elif name in CURVES:
                if not q.anchors_ghz:
                    raise ValueError(f"curve {name} needs anchors_ghz")
                if q.band_ghz is not None:
                    raise ValueError(f"band_ghz does not apply to the curve {name}")
            else:
                raise ValueError(f"unknown quantity {name!r}; scalars {SCALARS}, curves {CURVES}")
            if q.feature_map is not None:
                if q.feature_map not in FEATURE_MAP_DIMS:
                    raise ValueError(f"{name}: unknown feature_map {q.feature_map!r}; expected one of {sorted(FEATURE_MAP_DIMS)}")
                if set(FEATURE_MAP_DIMS[q.feature_map]) != set(self.dims):
                    raise ValueError(f"{name}: feature_map {q.feature_map} needs the dims {list(FEATURE_MAP_DIMS[q.feature_map])}, not {self.dims}")
            if q.model != "direct":
                self._check_model(name, q.model)
        return self

    def _check_model(self, name: str, model: str) -> None:
        """A ``ratio`` / ``resonance`` curve names the models it is built on: they must be the stratum's own quantities."""
        if name not in CURVES:
            raise ValueError(f"{name}: model {model!r} applies to the curves {CURVES}; a scalar is always modelled directly")
        if name not in LOW_FREQUENCY:
            raise ValueError(f"{name}: model {model!r} builds on a low-frequency scalar, and {name} has none "
                             f"(the curves that have one: {sorted(LOW_FREQUENCY)})")
        if model == "resonance" and name not in RESONANCE_CURVES:
            raise ValueError(f"{name}: model resonance applies to the inductances {RESONANCE_CURVES}; use ratio for {name}")
        needed = [LOW_FREQUENCY[name]] + (["SRF"] if model == "resonance" else [])
        absent = [q for q in needed if q not in self.quantities]
        if absent:
            raise ValueError(f"{name}: model {model} is built on {needed}; add {absent} to the stratum's quantities")

    def columns(self) -> list[str]:
        """Dataset columns: every scalar, and ``<curve>@<GHz>`` per anchor."""
        out = []
        for name, q in self.quantities.items():
            out += [f"{name}@{f:g}" for f in q.anchors_ghz] if name in CURVES else [name]
        return out


class Library(Model):
    schema_version: Literal["ic-opt-library-v1"] = SCHEMA
    process_profile: str = Field(min_length=1)
    strata: dict[str, Stratum] = Field(min_length=1)


def load(root: str | Path) -> Library:
    """Read and validate ``<root>/library.yaml``."""
    path = Path(root) / MANIFEST
    if not path.is_file():
        raise FileNotFoundError(f"no {MANIFEST} in {root}")
    return Library.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def is_library(directory: str | Path) -> bool:
    return (Path(directory) / MANIFEST).is_file()
