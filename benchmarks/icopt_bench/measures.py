"""Pure functions over ``loop.run_one`` result dicts: no plotting, no file I/O.

A "worst quartile" throughout is the 75th percentile in the direction that is worse: for ``first_feasible`` and
``best_at`` (both minimization-form, lower is better) that is ``np.percentile(values, 75)``; runs that never found a
feasible point count as ``budget + 1`` for ``first_feasible`` -- worse than any run that did, on any budget in the
benchmark -- and as worse than any value for ``best_at``: a statistic that falls on such a run is ``None`` ("none").
Leaving them out instead would flatter a method that succeeds rarely: two lucky runs of ten would set its median.

"The first n points" are the points with ``index <= n`` (``index`` is 1-based).
"""

from __future__ import annotations

import numpy as np


def first_feasible(result: dict) -> int | None:
    """1-based index of the first feasible point, or ``None`` if the run never found one."""
    hits = [p["index"] for p in result["points"] if p["feasible"]]
    return min(hits) if hits else None


def best_at(result: dict, n: int) -> float | None:
    """Smallest (minimization-form) objective among the feasible points of the first ``n``, or ``None``."""
    values = [p["objective"] for p in result["points"] if p["feasible"] and p["index"] <= n and p["objective"] is not None]
    return min(values) if values else None


def violation_at(result: dict, n: int) -> float | None:
    """Smallest ``constraint_penalty`` among the first ``n`` points whose status is ``ok`` or ``constraint_failed``
    (how close the run came, on the runs where nothing was feasible)."""
    values = [p["constraint_penalty"] for p in result["points"] if p["index"] <= n and p["status"] in ("ok", "constraint_failed")]
    return min(values) if values else None


_NONE = 1e300            # stands for "no value" while percentiles are taken (inf - inf in an interpolation is nan)


def _stats(values: list[float | None]) -> dict:
    """median / 25th / 75th percentile of ``values``, a missing value (None) counting as worse than any number; a
    statistic that falls on a missing value is None. ``n`` counts the values that are there. ``p25`` is not part of the
    printed report table but feeds ``compare``'s interquartile spread."""
    arr = np.asarray([_NONE if v is None else v for v in values], dtype=float)
    out = {k: float(np.percentile(arr, q)) for k, q in (("median", 50), ("p25", 25), ("p75", 75))}
    return {**{k: (None if v >= _NONE / 1e3 else v) for k, v in out.items()}, "n": sum(v is not None for v in values)}


def summarize(results: list[dict], budgets: tuple[int, ...] = (50, 100, 200)) -> list[dict]:
    """One row per (problem, method) in ``results``: seed count, success rate and best-objective stats at each
    budget, ``first_feasible`` stats, mean suggest time per batch, and how many runs ended with an error."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in results:
        groups.setdefault((r["problem"], r["method"]), []).append(r)

    rows = []
    for (problem, method), group in sorted(groups.items()):
        ff_values = [ff if (ff := first_feasible(r)) is not None else r["budget"] + 1 for r in group]
        row = {
            "problem": problem, "method": method, "n_seeds": len(group),
            "first_feasible": _stats(ff_values),
            "success_rate": {}, "best_at": {},
            "mean_suggest_seconds": float(np.mean([s for r in group for s in r["suggest_seconds"]]))
            if any(r["suggest_seconds"] for r in group) else None,
            "n_errors": sum(1 for r in group if r["error"]),
        }
        for b in budgets:
            row["success_rate"][b] = sum(1 for r in group if first_feasible(r) is not None and first_feasible(r) <= b) / len(group)
            stats = _stats([best_at(r, b) for r in group])
            row["best_at"][b] = {**stats, "n_missing": len(group) - stats["n"]}
        rows.append(row)
    return rows


def _verdict(baseline: float | None, candidate: float | None, spread: float, *, higher_is_better: bool) -> str:
    if baseline is None or candidate is None:
        return "within noise"
    diff = candidate - baseline
    if diff == 0 or abs(diff) < 0.5 * spread:      # identical medians is "within noise" even with a zero spread (5x0 seeds)
        return "within noise"
    improved = diff > 0 if higher_is_better else diff < 0
    return "better" if improved else "worse"


def compare(rows: list[dict], baseline: str, candidate: str) -> list[dict]:
    """One row per problem in ``rows`` (the output of :func:`summarize`): the difference between ``candidate`` and
    ``baseline`` on each measure, the larger of the two methods' interquartile spreads, and a verdict -- ``better``,
    ``worse`` or ``within noise`` (difference smaller than half that spread)."""
    by_problem: dict[str, dict[str, dict]] = {}
    for row in rows:
        by_problem.setdefault(row["problem"], {})[row["method"]] = row

    out = []
    for problem, methods in sorted(by_problem.items()):
        if baseline not in methods or candidate not in methods:
            continue
        base, cand = methods[baseline], methods[candidate]
        measures = {
            "first_feasible": _verdict(base["first_feasible"]["median"], cand["first_feasible"]["median"],
                                        max(base["first_feasible"]["p75"] - base["first_feasible"]["p25"],
                                            cand["first_feasible"]["p75"] - cand["first_feasible"]["p25"]),
                                        higher_is_better=False),
        }
        for b in base["success_rate"]:
            if b not in cand["success_rate"]:
                continue
            measures[f"success_rate@{b}"] = "better" if cand["success_rate"][b] > base["success_rate"][b] else (
                "worse" if cand["success_rate"][b] < base["success_rate"][b] else "within noise")
            bb, cb = base["best_at"][b], cand["best_at"][b]
            if bb["median"] is None or cb["median"] is None:      # at least half of a method's runs have no feasible point
                measures[f"best_at@{b}"] = "within noise" if bb["median"] is cb["median"] else (
                    "better" if bb["median"] is None else "worse")
            elif None in (bb["p25"], bb["p75"], cb["p25"], cb["p75"]):   # a quartile falls on a run without a value
                measures[f"best_at@{b}"] = "within noise"
            else:
                spread = max(bb["p75"] - bb["p25"], cb["p75"] - cb["p25"])
                measures[f"best_at@{b}"] = _verdict(bb["median"], cb["median"], spread, higher_is_better=False)
        tallies = {"better": 0, "worse": 0, "within noise": 0}
        for v in measures.values():
            tallies[v] += 1
        out.append({"problem": problem, "baseline": baseline, "candidate": candidate, "measures": measures, "tally": tallies})
    return out
