"""Fit a library's knot columns ahead (N-126): the ``lib.prefit`` block.

A declared curve answers at any frequency inside the parts' sweeps, but a column whose model the cache does not hold is fitted
at its first request: minutes per table, an hour for the two largest transformer tables at ten frequencies (N-122). A
library whose library.yaml declares a ``frequency_grid`` (``manifest.FrequencyGrid``) names the frequencies worth having
ahead: per curve, its knots (``query.Library.knots``) -- its anchors and the grid's frequencies inside the parts' sweeps,
where the column can have a model. ``prefit`` measures them and fits every knot column the cache does not hold, stratum after
stratum, each stratum with the whole of the library's limits, as ``lib_signoff`` fits (``Library.models``: worker processes
and BLAS threads from ``limits``, ``workers`` and ``threads`` capping them). The models a knot column needs besides its own --
its resonance's, a composed curve's parts -- come with it, each fitted once.

Resumable: a column whose model is cached is not fitted again, so a run that was interrupted continues when started again.
The columns go to ``Library.models`` ``ROUNDS`` per worker at a time, curve after curve and in each by frequency: a call
ends when its slowest fit does; an interruption loses at most one call's fits (a composed curve's calibration is written
once its five folds are done); and the models of a call are dropped from memory after it -- they stay in the cache, tens of
MB each on a large transformer table.
"""

from __future__ import annotations

import sys
import time

from ic_opt.library import dataset, manifest, query

ROUNDS = 2                                            # knot columns per Library.models call, per worker it runs


def prefit(library: query.Library, strata: list[str] | None = None, curves: list[str] | None = None, *, workers: int | None = None,
           threads: int | None = None) -> dict:
    """Measure and fit every knot column (``Library.knots``) whose model the cache does not hold: per stratum of ``strata``
    (default: every stratum), its curves among ``curves`` (default: every curve it declares), one stratum after another.
    Prints per stratum, on stderr, the knot columns cached, to fit and skipped (with why), progress, and at the end the
    columns fitted, cached and skipped and the minutes. The answer: ``frequency_grid`` (library.yaml's entry, or None),
    ``strata`` -- per stratum ``knots`` (per curve its frequencies in GHz), ``fitted`` and ``cached`` (knot columns),
    ``skipped`` (per column that is no knot -- an anchor or grid frequency where the column can have no model -- why) and
    ``seconds`` -- and the library's ``notes``. ValueError for a stratum library.yaml does not declare or a curve none of the
    strata declares; ``workers`` / ``threads`` above the limits are refused as ``Library.models`` refuses them."""
    names = _strata(library, strata)
    wanted = _curves(library, names, curves)
    grid = library.manifest.frequency_grid
    notes = list(library.notes)
    if grid is None:
        notes.append("library.yaml declares no frequency_grid: each curve's knots are its anchors")
    out = {"frequency_grid": None if grid is None else grid.model_dump(), "strata": {}, "notes": notes}
    for name in names:
        declared = dataset.curves(library.manifest.strata[name])
        out["strata"][name] = _stratum(library, name, [c for c in wanted if c in declared], workers, threads)
    return out


def _strata(library: query.Library, strata: list[str] | None) -> list[str]:
    if not strata:
        return library.strata()
    unknown = [s for s in strata if s not in library.manifest.strata]
    if unknown:
        raise ValueError(f"no strata {unknown} in {library.root / manifest.MANIFEST}; have {library.strata()}")
    return list(dict.fromkeys(strata))


def _curves(library: query.Library, names: list[str], curves: list[str] | None) -> list[str]:
    """The curves to fit, in ``manifest.CURVES`` order: ``curves``, each declared by one of the strata ``names`` at least."""
    declared = [c for c in manifest.CURVES if any(c in library.manifest.strata[n].quantities for n in names)]
    if not curves:
        return declared
    unknown = [c for c in dict.fromkeys(curves) if c not in declared]
    if unknown:
        raise ValueError(f"curves {unknown}: no stratum asked for declares them; the curves they declare: {declared or 'none'}")
    return [c for c in declared if c in curves]


def _stratum(lib: query.Library, name: str, curves: list[str], workers: int | None, threads: int | None) -> dict:
    start = time.monotonic()
    knots = {c: lib.knots(name, c) for c in curves}
    skipped = {}
    for c in curves:
        for f in lib._knot_candidates(name, c):
            if f not in knots[c]:
                skipped[f"{c}@{f:g}"] = lib.no_model(name, f"{c}@{f:g}")["reason"]
    columns = [f"{c}@{f:g}" for c in curves for f in knots[c]]
    cached = [c for c in columns if lib._model_file(name, c) is not None]
    have = set(cached)
    missing = [c for c in columns if c not in have]
    _say(f"{name}: {len(columns)} knot columns ({_per_curve(columns)}): {len(cached)} cached, {len(missing)} to fit; "
         f"{len(skipped)} skipped")
    for reason, columns_skipped in _by_reason(skipped).items():
        _say(f"{name}: skipped {', '.join(columns_skipped)} -- {reason}")
    if missing:
        ds = lib.dataset(name)
        n, _threads = query.fit_plan(lib.limits, len(missing), max(len(ds.usable(c)) for c in missing), len(ds.dims), workers=workers,
                                     threads=threads)
        size = ROUNDS * n
        for i in range(0, len(missing), size):
            chunk = missing[i:i + size]
            lib.models(name, chunk, workers=workers, threads=threads)
            for c in chunk:                              # in the cache now: the memory is the next call's
                lib._models.pop((name, c), None)
            _say(f"{name}: {i + len(chunk)} of {len(missing)} fitted, {(time.monotonic() - start) / 60:.1f} min")
    seconds = time.monotonic() - start
    _say(f"{name}: fitted {len(missing)}{_paren(missing)}, cached {len(cached)}{_paren(cached)}, skipped {len(skipped)}; "
         f"{seconds / 60:.1f} min")
    return {"knots": knots, "fitted": missing, "cached": cached, "skipped": skipped, "seconds": round(seconds, 1)}


def _per_curve(columns: list[str]) -> str:
    counts: dict[str, int] = {}
    for c in columns:
        counts[c.partition("@")[0]] = counts.get(c.partition("@")[0], 0) + 1
    return ", ".join(f"{curve} {n}" for curve, n in counts.items()) or "none"


def _paren(columns: list[str]) -> str:
    return f" ({_per_curve(columns)})" if columns else ""


def _by_reason(skipped: dict[str, str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for column, reason in skipped.items():
        out.setdefault(reason, []).append(column)
    return out


def _say(text: str) -> None:
    """A progress line on stderr: the answer is stdout's, and a prefit runs for minutes to hours."""
    print(f"[lib.prefit] {text}", file=sys.stderr, flush=True)
