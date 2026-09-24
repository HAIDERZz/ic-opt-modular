"""Library blocks: ``ic-opt call lib.<name> <library dir> key=value ...`` (the directory holds library.yaml)."""

from __future__ import annotations

import math
from pathlib import Path

from ic_opt.library import query as _query


def _lib(library: _query.Library | str | Path) -> _query.Library:
    return library if isinstance(library, _query.Library) else _query.Library(library)


def _names(value: str | list[str] | None) -> list[str] | None:
    if value is None or isinstance(value, list):
        return value
    return [v.strip() for v in str(value).split(",") if v.strip()]


def _trend(value: str | None) -> tuple[str, str] | None:
    """``"<quantity>:<dim>"`` as ``(quantity, dim)``, split on the first colon."""
    if value is None or value == "":
        return None
    quantity, sep, dim = value.partition(":") if isinstance(value, str) else ("", "", "")
    if not (sep and quantity.strip() and dim.strip()):
        raise ValueError(f"trend {value!r}: expected <quantity>:<dim>, e.g. k@40:center_spacing_um")
    return quantity.strip(), dim.strip()


def _strict_json(value: object) -> object:
    """``value`` with every inf, -inf and NaN replaced by None: JSON has no spelling for them (Python writes Infinity,
    which strict parsers refuse)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _strict_json(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_strict_json(v) for v in value]
    return value


def load(library: _query.Library | str | Path, stratum: str | None = None) -> dict:
    """Build (or read from cache) each stratum's dataset and report its integrity evidence."""
    return _query.load(_lib(library), stratum)


def coverage(library: _query.Library | str | Path, stratum: str) -> dict:
    """Rows per part and turns level, the achieved range of every dim, usable rows and value range per quantity."""
    return _query.coverage(_lib(library), stratum)


def query(library: _query.Library | str | Path, stratum: str, params: dict, quantities: str | list[str] | None = None, k: float = 2.0) -> dict:
    """Measured values at an exact library point; elsewhere mu with calibrated k-sigma bounds, the domain verdict and nearest measured rows.

    ``params`` maps every dim to a value (JSON on the command line); ``quantities`` is a comma list (default: all columns).
    """
    return _query.query(_lib(library), stratum, params, _names(quantities), k=float(k))


def suggest(library: _query.Library | str | Path, stratum: str, targets: dict, objective: str | None = None, n: int = 5,
            pool_size: int = 8192, seed: int = 0, k: float = 2.0, verify_build: bool = True) -> dict:
    """Designs that meet ``targets`` with margin: measured ones first (exact), then predicted candidates built and audited.

    ``targets`` maps quantities to ``{"min": v}``, ``{"max": v}`` or ``{"target": v, "tol": rel}`` (JSON on the command
    line); ``objective`` is ``max:<quantity>`` or ``min:<quantity>``. Anchored targets add SRF >= 1.25 x f0 unless SRF is
    already constrained.
    """
    from ic_opt.library import suggest as _suggest

    return _strict_json(_suggest.suggest(_lib(library), stratum, targets, objective, n=int(n), pool_size=int(pool_size), seed=int(seed),
                                         k=float(k), verify_build=bool(verify_build)))


def region(library: _query.Library | str | Path, stratum: str, targets: dict, objective: str | None = None, steps: dict | None = None,
           levels_per_dim: int = 20, max_points: int = 2_000_000, pool_size: int = 32768, seed: int = 0, k: float = 2.0,
           group_by: str | list[str] | None = None, trend: str | None = None, n: int = 8, verify_build: bool = False,
           sample_size: int = 5000, threads: int | None = None, workers: int | None = None) -> dict:
    """The part of the geometry space whose predictions meet ``targets``: sweep ranges, not ``lib.suggest``'s best few points.

    Two levels: ``robust`` -- the calibrated k-sigma interval lies inside every window (``lib.suggest``'s test; centre a
    sweep there); ``mean`` -- the predicted value does (the optimistic envelope). ``targets`` maps quantities to
    ``{"min": v}``, ``{"max": v}``, a window ``{"min": a, "max": b}`` or ``{"target": v, "tol": rel}`` (JSON on the command
    line); anchored targets add SRF >= 1.25 x f0 unless SRF is already constrained. ``objective`` (``max:<quantity>`` or
    ``min:<quantity>``) ranks the candidates. The grid lies on multiples of the manifest steps, about ``levels_per_dim``
    values per dim and at most ``max_points``, unless ``steps`` (JSON) sets a dim's step. ``group_by`` is a comma list of
    dims to tabulate the region by; ``trend`` is ``<quantity>:<dim>`` (e.g. ``k@40:center_spacing_um``): that quantity
    along that dim over the points meeting every other target. ``threads`` caps BLAS, ``workers`` is the processes fitting
    uncached models. The answer is strict JSON, every non-finite number null: ``targets[].upper`` is null except for windows.
    """
    from ic_opt.library import region as _region

    for name, value in (("targets", targets), ("steps", steps)):
        if value is not None and not isinstance(value, dict):
            raise ValueError(f"{name}: expected a JSON object, got {value!r}")
    return _strict_json(_region.region(
        _lib(library), stratum, targets, objective, steps=steps, levels_per_dim=int(levels_per_dim), max_points=int(max_points),
        pool_size=int(pool_size), seed=int(seed), k=float(k), group_by=_names(group_by), trend=_trend(trend), n=int(n),
        verify_build=bool(verify_build), sample_size=int(sample_size), threads=None if threads is None else int(threads),
        workers=None if workers is None else int(workers)))
