#!/usr/bin/env python3
"""T17.0a-B2 step 4: finds out what actually runs. For every AnalogGym circuit, evaluates (a) the authors' design
where there is one, (b) the log-scale midpoint of every range, (c) 32 log-scale Sobol points (seed 0), snapped to
the grid, at most 8 ngspice processes at once. Writes ``analoggym_check.json`` (kept in the repository) and
generates ``benchmarks/ANALOGGYM_CIRCUITS.md`` from it.

    PYTHONPATH=<worktree>/src:<worktree>/benchmarks ICOPT_BENCH_DATA=... ICOPT_BENCH_NGSPICE=... \\
        .venv/bin/python benchmarks/tools/analoggym_check.py
"""

from __future__ import annotations

import json
import shutil
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # benchmarks/, in case PYTHONPATH left it out

from icopt_bench import analoggym as ag

from ic_opt.space import parse_scalar

HERE = Path(__file__).resolve().parent
JSON_PATH = HERE / "analoggym_check.json"
MD_PATH = HERE.parent / "ANALOGGYM_CIRCUITS.md"
# Working directories for the ngspice runs: under $TMPDIR (or the platform default) like any other temp file --
# set TMPDIR before running this script to control exactly where they land.
SCRATCH = Path(tempfile.mkdtemp(prefix="analoggym_check_"))
MAX_WORKERS = 8
N_SOBOL = 32


def _sobol_unit_points(n_dims: int, n_points: int, seed: int = 0):
    from scipy.stats import qmc
    if n_dims == 0:
        return [[] for _ in range(n_points)]
    sampler = qmc.Sobol(d=n_dims, seed=seed)
    return sampler.random(n_points).tolist()


def _log_snap_continuous(u: float, lower: Decimal, upper: Decimal, step: Decimal) -> str:
    lo, hi = float(lower), float(upper)
    raw = lo * (hi / lo) ** u if lo > 0 else lo + u * (hi - lo)
    offset = round((Decimal(str(raw)) - lower) / step)
    offset = max(0, min(int((upper - lower) / step), offset))
    return f"{(lower + offset * step).normalize():f}"


def _log_snap_integer(u: float, lower: int, upper: int, step: int) -> str:
    raw = lower * (upper / lower) ** u if lower > 0 else lower + u * (upper - lower)
    offset = round((raw - lower) / step)
    offset = max(0, min((upper - lower) // step, offset))
    return str(lower + offset * step)


def _point_from_units(circuit: ag.Circuit, units: list[float]) -> dict[str, str]:
    params = {}
    for v, u in zip(circuit.variables, units, strict=True):
        if v["kind"] == "integer":
            params[v["name"]] = _log_snap_integer(u, int(v["lower"]), int(v["upper"]), int(v["step"]))
        else:
            lower, unit = parse_scalar(v["lower"])
            upper, _ = parse_scalar(v["upper"])
            step, _ = parse_scalar(v["step"])
            params[v["name"]] = _log_snap_continuous(u, lower, upper, step) + unit
    return params


def _midpoint(circuit: ag.Circuit) -> dict[str, str]:
    return _point_from_units(circuit, [0.5] * len(circuit.variables))


def _sobol_points(circuit: ag.Circuit) -> list[dict[str, str]]:
    units = _sobol_unit_points(len(circuit.variables), N_SOBOL, seed=0)
    return [_point_from_units(circuit, row) for row in units]


def _log_tail(workdir: Path, unit: str, n: int = 6) -> list[str]:
    """The decisive lines from ``<unit>.log``: every ``... failed`` / ``Error: ...`` line if there is one (that is
    the reason a metric is missing or the run failed), else the log's last ``n`` lines (a clean exit's own tail,
    e.g. when ngspice simply never wrote the file `simulate` expected)."""
    log = workdir / f"{unit}.log"
    if not log.exists():
        return []
    lines = [ln.rstrip() for ln in log.read_text(errors="ignore").splitlines() if ln.strip()]
    decisive = [ln for ln in lines if "failed" in ln.lower() or ln.strip().lower().startswith("error")]
    return decisive if decisive else lines[-n:]


def _eval_point(circuit: ag.Circuit, params: dict[str, str], workdir: Path) -> tuple[dict, float]:
    start = time.perf_counter()
    children = ag.simulate(circuit, params, workdir=workdir, timeout_s=120)
    elapsed = time.perf_counter() - start
    metrics: dict[str, float] = {}
    missing: list[str] = []
    failed: list[str] = []
    log_tail: dict[str, list[str]] = {}
    for unit_key, child in children.items():
        unit = unit_key.split("/")[0]
        metrics.update(child.metrics)
        if child.status.startswith("failed"):
            failed.append(unit_key)
            log_tail[unit] = _log_tail(workdir, unit)
        elif child.status == "metric_failed":
            missing.extend(m.split(" ")[1] for m in child.issues if m.startswith("metric "))
            log_tail[unit] = _log_tail(workdir, unit)
    return {"metrics": metrics, "missing": missing, "failed": failed, "seconds": elapsed,
            "log_tail": log_tail}, elapsed


def _target_met(target: dict, metrics: dict[str, float]) -> bool | None:
    if target["metric"] not in metrics:
        return None
    v = metrics[target["metric"]]
    return v >= target["value"] if target["direction"] == "ge" else v <= target["value"]


def check_circuit(name: str, circuit: ag.Circuit) -> dict:
    points: list[tuple[str, dict[str, str]]] = []
    if circuit.authors_design:
        points.append(("authors_design", dict(circuit.authors_design)))
    points.append(("midpoint", _midpoint(circuit)))
    for i, p in enumerate(_sobol_points(circuit)):
        points.append((f"sobol_{i:02d}", p))

    root = SCRATCH / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}
        for label, params in points:
            wd = root / label
            wd.mkdir(parents=True, exist_ok=True)
            futures[pool.submit(_eval_point, circuit, params, wd)] = label
        for fut in as_completed(futures):
            label = futures[fut]
            result, _ = fut.result()
            results[label] = result
    shutil.rmtree(root, ignore_errors=True)

    n_ok = sum(1 for r in results.values() if not r["missing"] and not r["failed"])
    n_partial = sum(1 for r in results.values() if r["missing"] and not r["failed"])
    n_failed = sum(1 for r in results.values() if r["failed"])
    seconds = [r["seconds"] for r in results.values()]
    metric_stats: dict[str, dict] = {}
    for r in results.values():
        for m, v in r["metrics"].items():
            metric_stats.setdefault(m, []).append(v)
    metric_summary = {m: {"min": min(vs), "median": statistics.median(vs), "max": max(vs)}
                       for m, vs in metric_stats.items()}

    authors_ok = None
    authors_misses = []
    if circuit.authors_design:
        ad = results["authors_design"]
        for t in circuit.targets:
            met = _target_met(t, ad["metrics"])
            if met is False:
                authors_misses.append({"metric": t["metric"], "direction": t["direction"], "target": t["value"],
                                       "value": ad["metrics"].get(t["metric"])})
            elif met is None:
                authors_misses.append({"metric": t["metric"], "direction": t["direction"], "target": t["value"],
                                       "value": None})
        authors_ok = len(authors_misses) == 0

    return {
        "kind": circuit.kind, "case": circuit.case, "n_variables": len(circuit.variables), "n_points": len(points),
        "n_ok": n_ok, "n_partial": n_partial, "n_failed": n_failed, "median_seconds": statistics.median(seconds),
        "max_seconds": max(seconds), "metrics": metric_summary, "authors_design_meets_targets": authors_ok,
        "authors_design_misses": authors_misses, "points": results,
    }


def check_determinism(circuits: dict[str, ag.Circuit]) -> dict:
    name = "leung_nmcf_pin_3" if "leung_nmcf_pin_3" in circuits else next(iter(circuits))
    circuit = circuits[name]
    params = dict(circuit.authors_design) if circuit.authors_design else _midpoint(circuit)
    root = SCRATCH / "_determinism"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    runs = []
    for i in range(3):
        wd = root / str(i)
        wd.mkdir()
        result, _ = _eval_point(circuit, params, wd)
        runs.append(result["metrics"])
    shutil.rmtree(root, ignore_errors=True)
    diffs = []
    for m in runs[0]:
        values = [r.get(m) for r in runs]
        if len(set(values)) > 1:
            diffs.append({"metric": m, "values": values})
    return {"circuit": name, "identical": not diffs, "differences": diffs}


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def write_markdown(report: dict) -> None:
    circuits = report["circuits"]
    det = report["determinism"]
    lines = [
        "# AnalogGym circuits: what runs (T17.0a-B2)",
        "",
        ("Generated by `benchmarks/tools/analoggym_check.py` from `benchmarks/tools/analoggym_check.json`. Each "
         "circuit is evaluated at 34 points: the authors' design (where AnalogGym gives one), the log-scale "
         "midpoint of every range, and 32 log-scale Sobol points (seed 0), all snapped to the grid."),
        "",
        f"Determinism: re-evaluating one point of `{det['circuit']}` three times gave "
        + ("identical metrics every time." if det["identical"] else
           "metrics that were NOT identical -- " +
           "; ".join(f"{d['metric']}: {d['values']}" for d in det["differences"])),
        "",
        "## Summary",
        "",
        "| circuit | kind | case | vars | ok | partial | failed | median s | max s | authors' design meets targets |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, c in circuits.items():
        meets = "yes" if c["authors_design_meets_targets"] else ("no" if c["authors_design_meets_targets"]
                                                                  is not None else "n/a")
        lines.append(f"| {name} | {c.get('kind', '')} | {c.get('case', '')} | {c['n_variables']} | {c['n_ok']} | "
                     f"{c['n_partial']} | {c['n_failed']} | {_fmt(c['median_seconds'])} | {_fmt(c['max_seconds'])} "
                     f"| {meets} |")
    lines += ["", "## Per-circuit metric ranges", ""]
    for name, c in circuits.items():
        lines.append(f"### {name}")
        lines.append("")
        lines.append("| metric | min | median | max |")
        lines.append("|---|---|---|---|")
        for m, s in sorted(c["metrics"].items()):
            lines.append(f"| {m} | {_fmt(s['min'])} | {_fmt(s['median'])} | {_fmt(s['max'])} |")
        if c["authors_design_misses"]:
            lines.append("")
            lines.append("Authors' design misses: " + "; ".join(
                f"{m['metric']} {m['direction']} {_fmt(m['target'])} (got {_fmt(m['value'])})"
                for m in c["authors_design_misses"]))
        lines.append("")
    lines += ["## Circuits that do not run, or run badly", ""]
    for name, c in circuits.items():
        if c["n_ok"] == c["n_points"] and c["n_failed"] == 0:
            continue
        reason = ""
        fallback_label = None
        for label, point in c["points"].items():
            if not (point["failed"] or point["missing"]):
                continue
            tails = [ln for lns in point["log_tail"].values() for ln in lns
                     if "failed" in ln.lower() or ln.strip().lower().startswith("error")]
            if tails:
                reason = f"e.g. `{label}` -- `{tails[0]}`"
                break
            fallback_label = fallback_label or label
        if not reason:
            # ngspice printed no error and no "... failed" line for any bad point of this circuit: the missing
            # metric is a value ngspice computed cleanly but that is not finite, or that our own SR/TS step-response
            # analysis (a Python port, not ngspice) could not resolve -- there is no ngspice-side line to quote.
            reason = (f"e.g. `{fallback_label}` -- no decisive ngspice log line (the run exited cleanly; the "
                      f"missing metric came from a non-finite value or an unresolved step response)")
        lines.append(f"- **{name}**: {c['n_ok']}/{c['n_points']} points gave every metric, {c['n_partial']} gave "
                     f"some, {c['n_failed']} failed outright. {reason}")
    MD_PATH.write_text("\n".join(lines) + "\n")


def main() -> int:
    circuits = ag.circuits()
    report = {"circuits": {}, "determinism": None}
    print("determinism check ...", file=sys.stderr)
    report["determinism"] = check_determinism(circuits)
    for name, circuit in circuits.items():
        print(f"checking {name} ({len(circuit.variables)} variables) ...", file=sys.stderr)
        report["circuits"][name] = check_circuit(name, circuit)
    JSON_PATH.write_text(json.dumps(report, indent=2, sort_keys=True))
    write_markdown(report)
    print(f"wrote {JSON_PATH} and {MD_PATH}", file=sys.stderr)
    shutil.rmtree(SCRATCH, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
