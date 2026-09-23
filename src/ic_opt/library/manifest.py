"""The library manifest (``library.yaml`` at the library root): which run stores make up each stratum and how its query quantities are defined.

A library is a directory of ordinary ic-opt run stores (projects built by ``sim.evaluate`` on an em_only
spec) plus this manifest. A stratum is one device family on one metal body; it may be assembled from
several stores ("parts") that were swept with different EMX settings, e.g. single-turn inductors swept
further in frequency. Quantities are named after the measure kernel: scalars (``Lp_lf``, ``Qp_peak``,
``SRF_p``, ``k_lf`` ...) or curves sampled at anchor frequencies (``Lp`` with ``anchors_ghz: [28]`` gives the
column ``Lp@28``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, field_validator, model_validator

from ic_opt.spec import Model

MANIFEST = "library.yaml"
SCHEMA = "ic-opt-library-v1"
SCALARS = ("Lp_lf", "Lp_res", "Qp_peak", "SRF_p", "Ls_lf", "Ls_res", "Qs_peak", "SRF_s", "k_lf")
CURVES = ("Lp", "Qp", "Ls", "Qs", "k")
PEAKS = ("Qp_peak", "Qs_peak")


class Part(Model):
    store: str = Field(min_length=1)                      # project directory relative to the library root (holds .icopt/)
    pipeline_fingerprint: str | None = None               # pin a generation; default: the part's most common one among ok rows


class Quantity(Model):
    band_ghz: float | None = Field(default=None, gt=0)    # peaks only: search 0 < f <= band, the same for every part
    anchors_ghz: list[float] = Field(default_factory=list)   # curves only: sample at these frequencies
    srf_margin: float = Field(default=1.25, ge=1.0)       # an anchored row is usable at f0 only if its resonance lies above margin x f0
    feature_map: str | None = None                        # model hint (e.g. the transformer k maps); read by the model layer

    @field_validator("anchors_ghz")
    @classmethod
    def _positive_anchors(cls, value: list[float]) -> list[float]:
        if any(f <= 0 for f in value) or len(set(value)) != len(value):
            raise ValueError("anchors_ghz must be distinct positive frequencies")
        return sorted(value)


class Stratum(Model):
    generator: str = Field(min_length=1)
    dims: list[str] = Field(min_length=1)                 # observation parameters that locate a point
    nt_dim: str | None = None                             # the integer turns dimension, if the family has one
    parts: list[Part] = Field(min_length=1)
    quantities: dict[str, Quantity] = Field(min_length=1)
    steps: dict[str, float] = Field(default_factory=dict)  # candidate resolution per dim for inverse queries (e.g. outer_diameter_um: 1)

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
        return self

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
