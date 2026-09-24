"""Library blocks: ``ic-opt call lib.<name> <library dir> key=value ...`` (the directory holds library.yaml).

The library computes on the machine running ic-opt. A library given as a directory (the command line) sizes
its model fits and predictions from site.yaml's ``hosts.local``, read when something has to be fitted or
predicted in bulk -- never the executor host's entry; a recipe passes ``Library(root, limits=...)`` to size
it from its own ``run.site.host("local")``. ``rel_sigma_max`` is the confidence ceiling on sigma / mu
(domain criterion 4): given, it holds for every quantity; left out, each quantity has its own -- its
``rel_sigma_max`` in library.yaml, else 0.15 (``domain.DEFAULT_SIGMA_REL_MAX``).

Every block takes ``cache_dir``: the directory for the library's cache files (datasets, calibrations, models).
Without it they go to the library's own ``.cache``, or, when that cannot be written, to
``~/.cache/ic-opt/<key>/`` (``ic_opt.library.cache``), and the answer's ``notes`` say so; the files already in
the library's own ``.cache`` are read either way."""

from __future__ import annotations

import json
import math
from pathlib import Path

from ic_opt.library import cache as _cache
from ic_opt.library import query as _query


def _lib(library: _query.Library | str | Path, cache_dir: str | Path | None = None) -> _query.Library:
    """The library to answer from: a directory opened with ``cache_dir``, or a Library as it is. A Library whose cache lives
    in another directory than an explicit ``cache_dir`` is refused: it was opened with its own."""
    if not isinstance(library, _query.Library):
        return _query.Library(library, cache_dir=cache_dir)
    if cache_dir is not None and not _cache.same(library.cache.directory, cache_dir):
        raise ValueError(f"cache_dir={cache_dir} is not the cache directory of the library given ({library.cache.directory}); "
                         "open it with Library(root, cache_dir=...)")
    return library


def _ceiling(value: float | str | None) -> float | None:
    """An explicit confidence ceiling as a float; None leaves each quantity its own."""
    return None if value is None else float(value)


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
        raise ValueError(f"trend {value!r}: expected <quantity>:<dim>, e.g. Qp_peak:width_um or <curve>@<f>:<dim>")
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


def load(library: _query.Library | str | Path, stratum: str | None = None, cache_dir: str | None = None) -> dict:
    """Build (or read from cache) each stratum's dataset and report its integrity evidence (and the library's ``notes``).
    ``cache_dir`` holds the library's cache files (default: its own ``.cache``, else ``~/.cache/ic-opt/<key>/``)."""
    return _query.load(_lib(library, cache_dir), stratum)


def coverage(library: _query.Library | str | Path, stratum: str, cache_dir: str | None = None) -> dict:
    """Rows per part and turns level, the achieved range of every dim, usable rows and value range per quantity.
    ``cache_dir`` holds the library's cache files (default: its own ``.cache``, else ``~/.cache/ic-opt/<key>/``)."""
    return _query.coverage(_lib(library, cache_dir), stratum)


def query(library: _query.Library | str | Path, stratum: str, params: dict, quantities: str | list[str] | None = None, k: float = 2.0,
          rel_sigma_max: float | None = None, cache_dir: str | None = None) -> dict:
    """Measured values at an exact library point; elsewhere mu with calibrated k-sigma bounds, the domain verdict and nearest measured rows.

    ``params`` maps every dim to a value (JSON on the command line); ``quantities`` is a comma list (default: all columns). A
    prediction with sigma / mu above its ceiling -- ``rel_sigma_max`` when given, else the quantity's in library.yaml, else
    0.15; each prediction reports the one it was held to -- is ``uncertain``. ``cache_dir`` holds the library's cache
    files (default: its own ``.cache``, else ``~/.cache/ic-opt/<key>/``, which the answer's ``notes`` name).
    """
    return _query.query(_lib(library, cache_dir), stratum, params, _names(quantities), k=float(k), rel_sigma_max=_ceiling(rel_sigma_max))


def suggest(library: _query.Library | str | Path, stratum: str, targets: dict, objective: str | None = None, n: int = 5,
            pool_size: int = 8192, seed: int = 0, k: float = 2.0, verify_build: bool = True,
            rel_sigma_max: float | None = None, cache_dir: str | None = None) -> dict:
    """Designs that meet ``targets`` with margin: measured ones first (exact), then predicted candidates built and audited.

    ``targets`` maps quantities to ``{"min": v}``, ``{"max": v}`` or ``{"target": v, "tol": rel}`` (JSON on the command
    line); ``objective`` is ``max:<quantity>`` or ``min:<quantity>``. Anchored targets add SRF >= srf_margin x f0
    (library.yaml; 1.25 unless set) unless SRF is already constrained. A candidate with sigma / mu above the ceiling of
    any quantity (``rel_sigma_max`` when given, else the quantity's in library.yaml, else 0.15) is dropped.
    ``cache_dir`` holds the library's cache files (default: its own ``.cache``, else ``~/.cache/ic-opt/<key>/``, which
    the answer's ``notes`` name).
    """
    from ic_opt.library import suggest as _suggest

    return _strict_json(_suggest.suggest(_lib(library, cache_dir), stratum, targets, objective, n=int(n), pool_size=int(pool_size),
                                         seed=int(seed), k=float(k), rel_sigma_max=_ceiling(rel_sigma_max), verify_build=bool(verify_build)))


def region(library: _query.Library | str | Path, stratum: str, targets: dict, objective: str | None = None, steps: dict | None = None,
           levels_per_dim: int = 20, max_points: int = 2_000_000, pool_size: int = 32768, seed: int = 0, k: float = 2.0,
           group_by: str | list[str] | None = None, trend: str | None = None, n: int = 8, verify_build: bool = False,
           sample_size: int = 5000, threads: int | None = None, workers: int | None = None,
           rel_sigma_max: float | None = None, cache_dir: str | None = None) -> dict:
    """The part of the geometry space whose predictions meet ``targets``: sweep ranges, not ``lib.suggest``'s best few points.

    Two levels: ``robust`` -- the calibrated k-sigma interval lies inside every window (``lib.suggest``'s test; centre a
    sweep there); ``mean`` -- the predicted value does (the optimistic envelope). ``targets`` maps quantities to
    ``{"min": v}``, ``{"max": v}``, a window ``{"min": a, "max": b}`` or ``{"target": v, "tol": rel}`` (JSON on the command
    line), an anchored quantity ``<curve>@<f>`` naming one of the stratum's anchors; anchored targets add
    SRF >= srf_margin x f0 (library.yaml; 1.25 unless set) unless SRF is already constrained. ``objective``
    (``max:<quantity>`` or ``min:<quantity>``) ranks the candidates. The grid lies on multiples of the manifest steps,
    about ``levels_per_dim`` values per dim and at most ``max_points``, unless ``steps`` (JSON) sets a dim's step.
    ``group_by`` is a comma list of dims to tabulate the region by; ``trend`` is ``<quantity>:<dim>`` (e.g.
    ``Qp_peak:width_um``, or ``k@<f>:center_spacing_um`` for a transformer): that quantity along that dim over the points
    meeting every other target. A point with sigma / mu above the ceiling of any quantity (``rel_sigma_max`` when given,
    else the quantity's in library.yaml, else 0.15) is not confident and joins neither level. ``threads`` caps BLAS and ``workers`` the processes fitting uncached models, both within site.yaml's
    hosts.local (above it they are refused; by default they follow from it). ``cache_dir`` holds the library's cache
    files (default: its own ``.cache``, else ``~/.cache/ic-opt/<key>/``, which the answer's ``notes`` name). The answer
    is strict JSON, every non-finite number null: ``targets[].upper`` is null except for windows.
    """
    from ic_opt.library import region as _region

    for name, value in (("targets", targets), ("steps", steps)):
        if value is not None and not isinstance(value, dict):
            raise ValueError(f"{name}: expected a JSON object, got {value!r}")
    return _strict_json(_region.region(
        _lib(library, cache_dir), stratum, targets, objective, steps=steps, levels_per_dim=int(levels_per_dim), max_points=int(max_points),
        pool_size=int(pool_size), seed=int(seed), k=float(k), rel_sigma_max=_ceiling(rel_sigma_max), group_by=_names(group_by),
        trend=_trend(trend), n=int(n), verify_build=bool(verify_build), sample_size=int(sample_size),
        threads=None if threads is None else int(threads), workers=None if workers is None else int(workers)))


def densify(library: _query.Library | str | Path, stratum: str, n: int, quantities: str | list[str] | None = None,
            bounds: dict | None = None, score: str = "ceiling", pool_size: int = 65536, top: int = 4000, seed: int = 0,
            k: float = 2.0, threads: int | None = None, workers: int | None = None, out: str | None = None,
            rel_sigma_max: float | None = None, cache_dir: str | None = None) -> dict:
    """Where to simulate next: ``n`` new geometries whose simulation lowers the models' uncertainty the most over the
    sampled domain, whatever the targets.

    ``quantities`` is a comma list (default: every column). ``bounds`` (JSON) keeps part of the domain: per dim a window
    ``{"min": a, "max": b}`` (clipped to the achieved range; either end may be left out) or a fixed value, e.g.
    ``{"<dim>": <v>, "<dim>": {"min": <a>, "max": <b>}}``; a dim the stratum lacks or a window missing its achieved range
    is refused. Candidates come from ``pool_size`` Sobol points inside the bounds snapped to the manifest steps, off the
    measured rows and inside every model's domain. Each is scored by the largest over the quantities of the model's own
    posterior sigma (no floor; for a positive quantity the log-space sigma) divided by a norm: ``score=ceiling`` (the
    default) divides by the quantity's confidence ceiling (``rel_sigma_max`` when given, else the quantity's in
    library.yaml, else 0.15) -- how far above what the library calls usable; ``score=typical`` by the quantity's
    held-out median relative error. The ``top`` best get each model's posterior
    covariance among them (fewer when the covariances would not fit 10% of site.yaml's max_memory_gb); then, n times, the
    best is picked and every other candidate's variance updated as if the pick were measured, so the next pick goes where
    uncertainty remains. ``before`` / ``after`` give the pool's relative sigma (median, p90, max, share above the
    quantity's ceiling) without and with the picks measured -- exact for the fitted hyperparameters; a refit after
    sign-off changes them. Each candidate carries its score, sigma before any pick and when picked, the current
    prediction with its calibrated ``k``-sigma interval and the three nearest measured rows; ``bounds`` echoes the
    effective bounds ({} without) and ``pool`` counts what they keep. ``out`` writes the answer to that file, which
    ``ic-opt run lib_signoff PROJECT library=... candidates=<file> top=<n> --plan`` takes as it is. ``threads`` and
    ``workers`` cap BLAS and the fitting processes within site.yaml's hosts.local, as in ``lib.region``. ``cache_dir``
    holds the library's cache files (default: its own ``.cache``, else ``~/.cache/ic-opt/<key>/``, which the answer's
    ``notes`` name). The answer is strict JSON, every non-finite number null.
    """
    from ic_opt.library import densify as _densify

    if bounds is not None and not isinstance(bounds, dict):
        raise ValueError(f"bounds: expected a JSON object, got {bounds!r}")
    answer = _strict_json(_densify.densify(
        _lib(library, cache_dir), stratum, _names(quantities), n=int(n), pool_size=int(pool_size), top=int(top), seed=int(seed), k=float(k),
        rel_sigma_max=_ceiling(rel_sigma_max), score=str(score), bounds=bounds, threads=None if threads is None else int(threads),
        workers=None if workers is None else int(workers)))
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(answer, indent=1, allow_nan=False) + "\n", encoding="utf-8")
    return answer
