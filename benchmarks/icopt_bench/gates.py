"""The first three conditions for changing the default strategy (plan D11), computed from a sweep's result files.

    python -m icopt_bench.gates DIR [DIR ...] --baseline openbox_gp_eic --candidate metric_gp --out gates.md \\
        [--heldout --reason "..."]

Per problem and per measure -- the index of the first feasible point, and the best feasible objective within 50, 100
and 200 points -- the two methods' runs are compared:

1. no step back: the candidate's median is not worse than the baseline's;
2. a step forward: the candidate is better by more than the seeds differ among themselves;
3. steady: the candidate's worst quartile is not worse than the baseline's.

"More than the seeds differ" is a one-sided Mann-Whitney test at 5% on the runs' values (a rank test: a run that
found no feasible point simply ranks last, it needs no number). The fourth condition, the user's own circuits, is not
a benchmark matter. Several DIRs are read as one set of results (the baseline and the candidate may come from
different sweeps of the same problems and seeds).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from scipy.stats import mannwhitneyu

from icopt_bench.measures import best_at, first_feasible
from icopt_bench.report import BUDGETS, load_all

LEVEL = 0.05
_WORST = 1e300                       # a run without a value, while ranks and percentiles are taken


def _values(runs: list[dict], measure: str) -> list[float]:
    if measure == "first_feasible":
        return [float(ff) if (ff := first_feasible(r)) is not None else _WORST for r in runs]
    budget = int(measure.split("@")[1])
    return [v if (v := best_at(r, budget)) is not None else _WORST for r in runs]


def _shown(x: float) -> float | None:
    return None if x >= _WORST / 1e3 else x


def judge(baseline: list[float], candidate: list[float]) -> dict:
    """Both lists in a form where smaller is better. ``p_better`` is the chance of ranks this favourable to the
    candidate if the two methods were the same; ``p_worse`` the same for the baseline."""
    b, c = np.asarray(baseline), np.asarray(candidate)
    if np.all(b == b[0]) and np.all(c == b[0]):                  # every run of both gives one value: nothing to test
        p_better = p_worse = 1.0
    else:
        p_better = float(mannwhitneyu(c, b, alternative="less").pvalue)
        p_worse = float(mannwhitneyu(c, b, alternative="greater").pvalue)
    stats = {k: (float(np.percentile(b, q, method="higher")), float(np.percentile(c, q, method="higher")))
             for k, q in (("median", 50), ("p75", 75))}
    return {
        "baseline_median": _shown(stats["median"][0]), "candidate_median": _shown(stats["median"][1]),
        "baseline_p75": _shown(stats["p75"][0]), "candidate_p75": _shown(stats["p75"][1]),
        "no_step_back": stats["median"][1] <= stats["median"][0],
        "step_forward": p_better < LEVEL,
        "step_back_beyond_seeds": p_worse < LEVEL,
        "steady": stats["p75"][1] <= stats["p75"][0],
        "p_better": p_better, "p_worse": p_worse,
    }


def gates(results: list[dict], baseline: str, candidate: str, budgets: tuple[int, ...] = BUDGETS) -> list[dict]:
    """One row per problem that has runs of both methods. Percentiles are taken as the value of an actual run
    (``method="higher"``), so a median never interpolates between a run with a value and one without."""
    by_problem: dict[str, dict[str, list[dict]]] = {}
    for r in results:
        if r["method"] in (baseline, candidate):
            by_problem.setdefault(r["problem"], {}).setdefault(r["method"], []).append(r)
    measures = ["first_feasible", *[f"best_at@{b}" for b in budgets]]
    rows = []
    for problem, runs in sorted(by_problem.items()):
        if baseline not in runs or candidate not in runs:
            continue
        judged = {m: judge(_values(runs[baseline], m), _values(runs[candidate], m)) for m in measures}
        rows.append({
            "problem": problem, "seeds": (len(runs[baseline]), len(runs[candidate])), "measures": judged,
            "no_step_back": all(j["no_step_back"] for j in judged.values()),
            "step_forward": any(j["step_forward"] for j in judged.values()),
            "step_back_beyond_seeds": any(j["step_back_beyond_seeds"] for j in judged.values()),
            "steady": all(j["steady"] for j in judged.values()),
        })
    return rows


def _fmt(x: float | None) -> str:
    return "none" if x is None else f"{x:.4g}"


def write_md(rows: list[dict], baseline: str, candidate: str, path: Path) -> None:
    n = len(rows)
    lines = [f"# {candidate} against {baseline}: conditions 1-3 of D11\n",
             f"Problems: {n}. Objectives in minimization form; `none` = no feasible point within the budget.\n",
             f"- 1, no step back (median not worse, every measure): {sum(r['no_step_back'] for r in rows)} of {n}",
             f"- 1', no step back beyond what the seeds differ: {sum(not r['step_back_beyond_seeds'] for r in rows)} of {n}",
             f"- 2, a step forward on at least one measure: {sum(r['step_forward'] for r in rows)} of {n}",
             f"- 3, steady (worst quartile not worse, every measure): {sum(r['steady'] for r in rows)} of {n}\n",
             "| problem | measure | median (baseline / candidate) | worst quartile (baseline / candidate) | 1 | 2 | 3 | beyond seeds |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        for measure, j in row["measures"].items():
            beyond = "better" if j["step_forward"] else ("worse" if j["step_back_beyond_seeds"] else "-")
            lines.append(
                f"| {row['problem']} | {measure} | {_fmt(j['baseline_median'])} / {_fmt(j['candidate_median'])} | "
                f"{_fmt(j['baseline_p75'])} / {_fmt(j['candidate_p75'])} | {'yes' if j['no_step_back'] else 'NO'} | "
                f"{'yes' if j['step_forward'] else '-'} | {'yes' if j['steady'] else 'NO'} | {beyond} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Conditions 1-3 for changing the default strategy.")
    parser.add_argument("run_dirs", nargs="+")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--heldout", action="store_true")
    parser.add_argument("--reason", default=None)
    args = parser.parse_args(argv)
    results = [r for d in args.run_dirs for r in load_all(Path(d), allow_heldout=args.heldout, reason=args.reason)]
    rows = gates(results, args.baseline, args.candidate)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_md(rows, args.baseline, args.candidate, out)
    print(out.read_text(encoding="utf-8").split("| problem")[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
