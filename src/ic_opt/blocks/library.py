"""Library blocks: ``ic-opt call lib.<name> <library dir> key=value ...`` (the directory holds library.yaml)."""

from __future__ import annotations

from pathlib import Path

from ic_opt.library import query as _query


def _lib(library: _query.Library | str | Path) -> _query.Library:
    return library if isinstance(library, _query.Library) else _query.Library(library)


def _names(value: str | list[str] | None) -> list[str] | None:
    if value is None or isinstance(value, list):
        return value
    return [v.strip() for v in str(value).split(",") if v.strip()]


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

    return _suggest.suggest(_lib(library), stratum, targets, objective, n=int(n), pool_size=int(pool_size), seed=int(seed),
                            k=float(k), verify_build=bool(verify_build))
