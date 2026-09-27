"""CLI: turn a sweep's result files into a report -- ``summary.csv``, ``summary.md``, optionally
``comparison.md``, and two figures per problem.

    python -m icopt_bench.report DIR --out REPORT_DIR [--baseline random --candidate openbox_gp_eic] \\
        [--heldout --reason "..."]

Reads every ``DIR/*.json`` result file (and, only with ``--heldout``, ``DIR/heldout/*.json`` too -- the same guard
as ``sweep.py``, logged the same way). Figures are matplotlib, ``Agg`` backend, no interactive display needed.
"""

from __future__ import annotations

import argparse
import csv
import json
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from icopt_bench import registry
from icopt_bench.measures import compare, summarize
from icopt_bench.sweep import log_heldout

BUDGETS = (50, 100, 200)
# Fixed per method name, assigned by sorted method name (module docstring's colour list).
_PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#7a5cd6", "#5b6b7a", "#d9a126", "#c0392b", "#0f9bb3"]


def _load_results(directory: Path) -> list[dict]:
    """Every ``<problem>__<method>__<seed>.json`` in ``directory`` -- not ``progress.json``, which ``sweep.py`` writes
    next to them and which is not a run result."""
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*__*__*.json"))]


def load_all(run_dir: Path, *, allow_heldout: bool, reason: str | None) -> list[dict]:
    results = _load_results(run_dir)
    heldout_dir = run_dir / "heldout"
    heldout_results = _load_results(heldout_dir) if heldout_dir.is_dir() else []
    present = {r["problem"] for r in heldout_results}
    held = [n for n in present if registry.is_heldout(n)]
    if held and not allow_heldout:
        raise SystemExit(f"refusing to report held-out problems without --heldout --reason: {sorted(held)}")
    if held:
        if not reason:
            raise SystemExit("--heldout needs --reason")
        log_heldout("report", sorted(held), sorted({r["method"] for r in heldout_results}), reason)
        results += heldout_results
    return results


def method_color(method: str, methods: list[str]) -> str:
    return _PALETTE[sorted(methods).index(method) % len(_PALETTE)]


def write_csv(rows: list[dict], path: Path) -> None:
    fieldnames = ["problem", "method", "n_seeds", "first_feasible_median", "first_feasible_p75", "mean_suggest_seconds",
                  "n_errors"]
    for b in BUDGETS:
        fieldnames += [f"success_rate@{b}", f"best_at@{b}_median", f"best_at@{b}_p75", f"best_at@{b}_n_missing"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            flat = {"problem": row["problem"], "method": row["method"], "n_seeds": row["n_seeds"],
                    "first_feasible_median": row["first_feasible"]["median"], "first_feasible_p75": row["first_feasible"]["p75"],
                    "mean_suggest_seconds": row["mean_suggest_seconds"], "n_errors": row["n_errors"]}
            for b in BUDGETS:
                flat[f"success_rate@{b}"] = row["success_rate"][b]
                flat[f"best_at@{b}_median"] = row["best_at"][b]["median"]
                flat[f"best_at@{b}_p75"] = row["best_at"][b]["p75"]
                flat[f"best_at@{b}_n_missing"] = row["best_at"][b]["n_missing"]
            writer.writerow(flat)


def _fmt(x: float | None, digits: int = 4) -> str:
    return "-" if x is None else f"{x:.{digits}g}"


def write_summary_md(rows: list[dict], path: Path) -> None:
    by_problem: dict[str, list[dict]] = {}
    for row in rows:
        by_problem.setdefault(row["problem"], []).append(row)

    lines = ["# Benchmark summary\n"]
    for problem, group in sorted(by_problem.items()):
        lines.append(f"## {problem}\n")
        header = ["method", "seeds", "first_feasible (median/p75)", "errors"]
        for b in BUDGETS:
            header += [f"success@{b}", f"best@{b} (median/p75, missing)"]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("| " + " | ".join("---" for _ in header) + " |")
        for row in sorted(group, key=lambda r: r["method"]):
            cells = [row["method"], str(row["n_seeds"]),
                     f"{_fmt(row['first_feasible']['median'])}/{_fmt(row['first_feasible']['p75'])}", str(row["n_errors"])]
            for b in BUDGETS:
                cells.append(f"{row['success_rate'][b]:.0%}")
                ba = row["best_at"][b]
                cells.append(f"{_fmt(ba['median'])}/{_fmt(ba['p75'])} ({ba['n_missing']} missing)")
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_comparison_md(rows: list[dict], baseline: str, candidate: str, path: Path) -> None:
    verdicts = compare(rows, baseline, candidate)
    lines = [f"# {candidate} vs {baseline}\n"]
    if not verdicts:
        lines.append("(no problem has both methods)\n")
    else:
        measure_names = sorted(verdicts[0]["measures"])
        header = ["problem", *measure_names, "better", "within noise", "worse"]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("| " + " | ".join("---" for _ in header) + " |")
        for v in verdicts:
            cells = [v["problem"], *[v["measures"][m] for m in measure_names],
                     str(v["tally"]["better"]), str(v["tally"]["within noise"]), str(v["tally"]["worse"])]
            lines.append("| " + " | ".join(cells) + " |")
        totals = {"better": 0, "worse": 0, "within noise": 0}
        for v in verdicts:
            for k in totals:
                totals[k] += v["tally"][k]
        lines.append(f"\nTotals: better {totals['better']}, within noise {totals['within noise']}, worse {totals['worse']}\n")
    path.write_text("\n".join(lines), encoding="utf-8")


def _running_best(result: dict, budget: int) -> np.ndarray:
    """Best feasible objective by point k (1..budget), NaN before the first feasible point."""
    out = np.full(budget, np.nan)
    best = np.inf
    by_index = {p["index"]: p for p in result["points"]}
    for k in range(1, budget + 1):
        p = by_index.get(k)
        if p is not None and p["feasible"] and p["objective"] is not None:
            best = min(best, p["objective"])
        if best < np.inf:
            out[k - 1] = best
    return out


def _feasible_share(result: dict, budget: int) -> np.ndarray:
    out = np.zeros(budget)
    found = False
    by_index = {p["index"]: p for p in result["points"]}
    for k in range(1, budget + 1):
        p = by_index.get(k)
        if p is not None and p["feasible"]:
            found = True
        out[k - 1] = 1.0 if found else 0.0
    return out


def _annotate_ends(ax, entries: list[tuple[str, str, float, float]]) -> None:
    """Direct labels at each line's last point, nudged apart (in 9-point steps) when two would otherwise sit within
    4% of the axes' y-range of each other -- e.g. two methods tied at 0% feasible share the whole run."""
    if not entries:
        return
    span = (ax.get_ylim()[1] - ax.get_ylim()[0]) or 1.0
    placed: list[float] = []
    for method, color, x, y in sorted(entries, key=lambda e: e[3]):
        offset = 0
        while any(abs(y - py) < 0.04 * span for py in placed) and offset < 100:
            offset += 12
            placed = [py for py in placed if abs(y - py) >= 0.04 * span]     # this label cleared that neighbour
        placed.append(y)
        ax.annotate(method, (x, y), xytext=(4, offset), textcoords="offset points", color=color, fontsize=8, va="center")


def plot_problem(problem: str, group: list[dict], out_dir: Path) -> None:
    methods = sorted({r["method"] for r in group})
    budget = max(r["budget"] for r in group)
    x = np.arange(1, budget + 1)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ends = []
    for method in methods:
        runs = [r for r in group if r["method"] == method]
        curves = np.stack([_running_best(r, budget) for r in runs])
        share = np.mean(~np.isnan(curves), axis=0)
        with warnings.catch_warnings():                     # a column before any seed found a feasible point is all-NaN by design
            warnings.simplefilter("ignore", category=RuntimeWarning)
            median = np.nanmedian(curves, axis=0)
            p25 = np.nanpercentile(curves, 25, axis=0)
            p75 = np.nanpercentile(curves, 75, axis=0)
        visible = share >= 0.5
        if not visible.any():
            continue
        color = method_color(method, methods)
        xs = x[visible]
        ax.plot(xs, median[visible], color=color, label=method, linewidth=1.8)
        ax.fill_between(xs, p25[visible], p75[visible], color=color, alpha=0.15, linewidth=0)
        ends.append((method, color, xs[-1], median[visible][-1]))
    ax.set_xlabel("points evaluated")
    ax.set_ylabel("best feasible objective so far (minimization form)")
    ax.set_title(f"{problem}: best so far")
    if ends:
        _annotate_ends(ax, ends)
        ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
    else:
        ax.set_xlim(1, budget)
        ax.text(0.5, 0.5, "no method found a feasible point in at least half the seeds", ha="center", va="center",
                transform=ax.transAxes, fontsize=9, color="#5b6b7a")
    fig.tight_layout()
    fig.savefig(out_dir / f"best_so_far_{problem}.png", dpi=140)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ends = []
    for method in methods:
        runs = [r for r in group if r["method"] == method]
        share = np.mean(np.stack([_feasible_share(r, budget) for r in runs]), axis=0)
        color = method_color(method, methods)
        ax.plot(x, share, color=color, label=method, linewidth=1.8)
        ends.append((method, color, x[-1], share[-1]))
    ax.set_xlabel("points evaluated")
    ax.set_ylabel("share of runs with a feasible point")
    ax.set_ylim(-0.02, 1.02)
    ax.set_title(f"{problem}: feasible share")
    _annotate_ends(ax, ends)
    ax.legend(loc="lower right", fontsize=8, framealpha=0.9)
    fig.tight_layout()
    fig.savefig(out_dir / f"feasible_share_{problem}.png", dpi=140)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Turn a sweep's results into a report.")
    parser.add_argument("run_dir")
    parser.add_argument("--out", required=True)
    parser.add_argument("--baseline", default=None)
    parser.add_argument("--candidate", default=None)
    parser.add_argument("--heldout", action="store_true")
    parser.add_argument("--reason", default=None)
    args = parser.parse_args(argv)

    results = load_all(Path(args.run_dir), allow_heldout=args.heldout, reason=args.reason)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = summarize(results, budgets=BUDGETS)
    write_csv(rows, out_dir / "summary.csv")
    write_summary_md(rows, out_dir / "summary.md")
    if args.baseline and args.candidate:
        write_comparison_md(rows, args.baseline, args.candidate, out_dir / "comparison.md")

    by_problem: dict[str, list[dict]] = {}
    for r in results:
        by_problem.setdefault(r["problem"], []).append(r)
    for problem, group in by_problem.items():
        plot_problem(problem, group, out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
