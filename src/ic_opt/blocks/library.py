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
the library's own ``.cache`` are read either way.

A column name may be a declared curve at any frequency, ``<curve>@<GHz>`` (T18.1): ``lib.query`` (``quantities``),
``lib.suggest`` and ``lib.region`` (targets, objective, trend) and ``lib.densify`` (``quantities``) take it. A design's
``footprint`` is the box around the drawn device without its ground fixture (``ic_opt.em.pcell.footprint``).
``lib.index`` and ``lib.pick`` show a stratum's rows by their electrical values at one frequency
(``ic_opt.library.index``)."""

from __future__ import annotations

import json
import math
from pathlib import Path

from ic_opt.library import cache as _cache
from ic_opt.library import index as _index
from ic_opt.library import query as _query
from ic_opt.library import region as _region


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
          rel_sigma_max: float | None = None, footprint: bool = False, cache_dir: str | None = None) -> dict:
    """Measured values at an exact library point; elsewhere mu with calibrated k-sigma bounds, the domain verdict and nearest measured rows.

    ``params`` maps every dim to a value (JSON on the command line); ``quantities`` is a comma list (default: all columns),
    where a declared curve may be asked at any frequency inside the parts' sweeps, ``<curve>@<GHz>`` (``Lp@33``). A
    prediction with sigma / mu above its ceiling -- ``rel_sigma_max`` when given, else the quantity's in library.yaml, else
    0.15; each prediction reports the one it was held to -- is ``uncertain``. ``footprint`` is the box around the drawn
    device without its ground fixture: a library point's row's; elsewhere null unless ``footprint=true``, which draws the
    geometry with the stratum's generator (no EMX); ``footprint_why`` says why a footprint is null. ``cache_dir`` holds the
    library's cache files (default: its own ``.cache``, else ``~/.cache/ic-opt/<key>/``, which the answer's ``notes`` name).
    """
    from ic_opt.library import suggest as _suggest

    lib = _lib(library, cache_dir)
    answer = _query.query(lib, stratum, params, _names(quantities), k=float(k), rel_sigma_max=_ceiling(rel_sigma_max))
    answer.update(_suggest.footprint_fields(*_query.footprint_at(lib, stratum, answer["params"], build=_flag(footprint))))
    return _strict_json(answer)


def _flag(value: bool | str | None) -> bool:
    """A yes/no parameter: a bool, or its command-line spelling (``true`` / ``false``, ``1`` / ``0``)."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no", "none", ""):
        return False
    raise ValueError(f"expected true or false, got {value!r}")


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
           rel_sigma_max: float | None = None, relax: float = _region.RELAX, cache_dir: str | None = None) -> dict:
    """The part of the geometry space whose predictions meet ``targets``: sweep ranges, not ``lib.suggest``'s best few points.

    Two levels: ``robust`` -- the calibrated k-sigma interval lies inside every window (``lib.suggest``'s test; centre a
    sweep there); ``mean`` -- the predicted value does (the optimistic envelope). ``targets`` maps quantities to
    ``{"min": v}``, ``{"max": v}``, a window ``{"min": a, "max": b}`` or ``{"target": v, "tol": rel}`` (JSON on the command
    line), an anchored quantity ``<curve>@<f>`` naming one of the stratum's anchors; anchored targets add
    SRF >= srf_margin x f0 (library.yaml; 1.25 unless set) unless SRF is already constrained. ``objective``
    (``max:<quantity>`` or ``min:<quantity>``) ranks the candidates. The grid lies on multiples of the manifest steps,
    about ``levels_per_dim`` values per dim and at most ``max_points``, unless ``steps`` (JSON) sets a dim's step; it
    covers the candidates of a coarse pass whose stated windows are ``relax`` wider (a fraction; ``grid`` echoes it):
    raise it when the models are unsure or the pool is sparse, and the region comes out cut short.
    ``group_by`` is a comma list of dims to tabulate the region by; ``trend`` is ``<quantity>:<dim>`` (e.g.
    ``Qp_peak:width_um``, or ``k@<f>:center_spacing_um`` for a transformer): that quantity along that dim over the points
    meeting every other target. A point with sigma / mu above the ceiling of any quantity (``rel_sigma_max`` when given,
    else the quantity's in library.yaml, else 0.15) is not confident and joins neither level; the answer echoes ``k``
    and the ceiling each quantity was held to (``rel_sigma_max``, per quantity). ``threads`` caps BLAS and ``workers`` the processes fitting uncached models, both within site.yaml's
    hosts.local (above it they are refused; by default they follow from it). ``cache_dir`` holds the library's cache
    files (default: its own ``.cache``, else ``~/.cache/ic-opt/<key>/``, which the answer's ``notes`` name). The answer
    is strict JSON, every non-finite number null: ``targets[].upper`` is null except for windows.
    """
    for name, value in (("targets", targets), ("steps", steps)):
        if value is not None and not isinstance(value, dict):
            raise ValueError(f"{name}: expected a JSON object, got {value!r}")
    return _strict_json(_region.region(
        _lib(library, cache_dir), stratum, targets, objective, steps=steps, levels_per_dim=int(levels_per_dim), max_points=int(max_points),
        pool_size=int(pool_size), seed=int(seed), k=float(k), rel_sigma_max=_ceiling(rel_sigma_max), relax=float(relax),
        group_by=_names(group_by), trend=_trend(trend), n=int(n), verify_build=bool(verify_build), sample_size=int(sample_size),
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


def _grid(value: dict | None) -> dict | None:
    """``grid`` as the index takes it: ``{coordinate: [lower, upper, step]}`` (JSON on the command line)."""
    if value is not None and not isinstance(value, dict):          # the shell ate the quotes, or a list was given
        raise ValueError(f'grid: expected a JSON object {{"<column>": [lower, upper, step], ...}}, got {value!r}')
    return value


def index(library: _query.Library | str | Path, stratum: str, frequency_ghz: float, srf_margin: float | None = None,
          grid: dict | None = None, prefer: str | None = None, out: str | None = None, cache_dir: str | None = None) -> dict:
    """A stratum's rows by their electrical values at ``frequency_ghz``: the index of ``ic_opt.library.index``.

    The index's columns are every declared curve at that frequency under its bare name (``Lp``, ``Qp``; ``Ls``, ``Qs``,
    ``k`` for a coupled pair), every declared scalar, ``Qmin`` = min(Qp, Qs) when both exist and ``area`` (um2, the
    footprint's). Its rows are the rows with every curve there; ``srf_margin`` above the manifest's also drops the rows
    whose system SRF is at or below it x f. Without ``grid`` the answer summarizes the index: rows kept and dropped (and
    why), each column's range, the footprints, the parts' sweeps. ``grid`` (JSON) puts the rows on a grid of electrical
    values, ``{"<column>": [lower, upper, step], ...}`` in the column's unit (H, 1, Hz): levels lower + i x step, searched
    logarithmically where lower > 0 and upper / lower >= 10; each row goes to the cell of its nearest levels, ranked in
    the cell by ``prefer`` (``max:<column>`` or ``min:<column>``; default ``max:Qmin``, else ``max:Qp``). The answer then
    also has ``table``: cells occupied of how many, rows per occupied cell (median, largest) and per coordinate the span of
    occupied levels and how many hold no row. ``out`` writes the table (``grid`` needed): per occupied cell its levels,
    the level values and its best row (values, params, footprint, part, obs id, the sNp path relative to the library
    root). ``cache_dir`` holds the library's cache files. The answer is strict JSON, every non-finite number null.
    """
    lib = _lib(library, cache_dir)
    grid = _grid(grid)
    if out and grid is None:
        raise ValueError("out= writes the table of a grid: give grid= too")
    built = _index.build(lib, stratum, float(frequency_ghz) * 1e9, srf_margin=None if srf_margin is None else float(srf_margin))
    answer = _index.summary(lib, built)
    if grid is not None:
        table = _index.table(built, grid, prefer)
        answer["table"] = _index.table_summary(table)
        if out:
            answer["out"] = str(_index.write(out, _strict_json(_index.table_json(lib, built, table))))
    answer["notes"] = lib.notes + (_index.notes(built, table) if grid is not None else [])
    return _strict_json(answer)


def pick(library: _query.Library | str | Path, stratum: str, frequency_ghz: float, targets: dict | None = None, grid: dict | None = None,
         prefer: str | None = None, n: int = 1, srf_margin: float | None = None, cache_dir: str | None = None) -> dict:
    """The library rows nearest to target electrical values at ``frequency_ghz``: ``ic_opt.library.index.pick``.

    ``targets`` (JSON) gives a value per coordinate of ``grid``, e.g. ``{"Lp": 3e-10, "Ls": 2.5e-10, "k": 0.6}``; ``grid``
    (JSON, required: it says what counts as the same value) is ``{"<column>": [lower, upper, step], ...}`` as in
    ``lib.index``. The answer is the nearest occupied cell -- by the Euclidean distance over the coordinates in the grid's
    unit coordinates, a tie to the smallest cell -- its level values and its best ``n`` rows by ``prefer`` (default
    ``max:Qmin``, else ``max:Qp``): electrical values, geometry, footprint, part and obs id, the sNp path relative to the
    library root. ``exact`` says whether the targets' own cell holds a row; ``distance`` is per coordinate the cell's level
    minus the target's, in levels. ``srf_margin`` and ``cache_dir`` as in ``lib.index``. Strict JSON.
    """
    if targets is None or not isinstance(targets, dict):
        raise ValueError(f'targets is required, a JSON object {{"<column>": value, ...}}; got {targets!r}')
    grid = _grid(grid)
    if grid is None:
        raise ValueError('grid is required, {"<column>": [lower, upper, step], ...}: it says what counts as the same value')
    lib = _lib(library, cache_dir)
    if isinstance(n, bool) or int(n) < 1:
        raise ValueError(f"n must be a positive integer, got {n!r}")
    built = _index.build(lib, stratum, float(frequency_ghz) * 1e9, srf_margin=None if srf_margin is None else float(srf_margin))
    table = _index.table(built, grid, prefer)
    answer = _index.pick_json(lib, built, table, _index.pick(table, targets), int(n))
    answer["targets"] = targets
    answer["notes"] = lib.notes + _index.notes(built, table)
    return _strict_json(answer)
