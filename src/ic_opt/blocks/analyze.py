"""analyze.best / analyze.report — the six sections and four figures that survived the report review.

Sections: best observed · top feasible · constraint margins · parameter importance (SHAP) ·
corners (policy, best point per corner, per-corner failures) · space compression advisory.
Figures: feasible_convergence · convergence (all points, failures on a status strip) ·
constraint_margins (normalized by the observed metric range) · bottleneck_weighted_score
(only when the objective parses as bottleneck + weighted sum; no hard-coded fallback).
"""

from __future__ import annotations

import ast
import base64
import html
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ic_opt import objective as objective_contract
from ic_opt import space
from ic_opt.observation import Observation, Observations
from ic_opt.spec import Spec
from ic_opt.store import RunStore

STATUS_COLORS = {"ok": "#2f9e44", "constraint_failed": "#e08b2d", "metric_failed": "#d64545"}


def best(spec: Spec, observations: Sequence[Observation], k: int = 1) -> Observations:
    return Observations(observations).best(k)


def report(spec: Spec, observations: Sequence[Observation], store: RunStore, *, title: str | None = None) -> Path:
    obs = Observations(observations)
    out = store.reports_dir()
    figures = _figures(spec, obs, out)
    sections: list[tuple[str, str]] = [
        ("Best observed", _best_section(spec, obs)),
        ("Top feasible candidates", _top_section(spec, obs)),
        ("Constraint margins", _margins_section(spec, obs)),
        ("Parameter importance (SHAP)", _importance_section(spec, obs)),
    ]
    if spec.corners:
        sections.append(("Corners", _corners_section(spec, obs)))
    sections.append(("Space compression advisory", _advisory_section(spec, obs)))
    sections.append(("Figures", "\n".join(f"![{name}]({path.name})" for name, path in figures.items()) or "_no figures_"))

    heading = title or f"IC-Opt report — {spec.project}"
    counts = _status_counts(obs)
    intro = f"{len(obs)} observations · " + " · ".join(f"{k} {v}" for k, v in counts.items())
    md = f"# {heading}\n\n{intro}\n\n" + "\n\n".join(f"## {h}\n\n{body}" for h, body in sections) + "\n"
    (out / "report.md").write_text(md, encoding="utf-8")
    (out / "report.html").write_text(_html(heading, intro, sections, figures), encoding="utf-8")
    store.log_step("report", "ok", observations=len(obs), figures=list(figures))
    return out / "report.md"


# -- sections ---------------------------------------------------------------------

def _status_counts(obs: Observations) -> dict[str, int]:
    counts: dict[str, int] = {}
    for o in obs:
        counts[o.status] = counts.get(o.status, 0) + 1
    return dict(sorted(counts.items()))


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.6g}"


def _best_section(spec: Spec, obs: Observations) -> str:
    top = obs.best(1)
    if not top:
        return "_no feasible observation yet_"
    o = top[0]
    lines = [f"- observation: `{o.obs_id}` (step `{o.step}`, origin `{o.origin}`)",
             f"- objective ({spec.objective.direction if spec.objective else 'n/a'}): {_fmt(o.fom)}",
             "- parameters: " + ", ".join(f"{k}={v}" for k, v in o.params.items()),
             "- metrics: " + ", ".join(f"{k}={_fmt(v)}" for k, v in o.metrics.items())]
    if spec.corners:
        lines.append(f"- corner policy: objective={spec.corner_policy.objective}, constraints={spec.corner_policy.constraints}")
    return "\n".join(lines)


def _top_section(spec: Spec, obs: Observations, k: int = 5) -> str:
    rows = obs.best(k)
    if not rows:
        return "_no feasible observation yet_"
    header = ["obs", "objective", *[v.name for v in spec.variables], *[m.name for m in spec.metrics]]
    table = [header, ["---"] * len(header)]
    for o in rows:
        table.append([o.obs_id, _fmt(o.fom), *[o.params[v.name] for v in spec.variables], *[_fmt(o.metrics.get(m.name)) for m in spec.metrics]])
    return "\n".join("| " + " | ".join(r) + " |" for r in table)


def _margin(spec: Spec, constraint, value: float) -> float:
    limit = float(space.parse_scalar(constraint.value.replace(" ", ""))[0])
    return (limit - value) if constraint.op in ("lt", "le") else (value - limit)


def _margins_section(spec: Spec, obs: Observations) -> str:
    if not spec.constraints:
        return "_no constraints_"
    lines = []
    for c in spec.constraints:
        rows = [(o, _margin(spec, c, o.metrics[c.metric])) for o in obs if c.metric in o.metrics]
        if not rows:
            lines.append(f"- {c.metric} {c.op} {c.value}: no data")
            continue
        best_o, best_m = max(rows, key=lambda r: r[1])
        worst_o, worst_m = min(rows, key=lambda r: r[1])
        passed = sum(1 for _, m in rows if m >= 0)
        lines.append(f"- {c.metric} {c.op} {c.value}: pass {passed}/{len(rows)}, best margin {_fmt(best_m)} ({best_o.obs_id}), "
                     f"worst {_fmt(worst_m)} ({worst_o.obs_id})")
    return "\n".join(lines)


def _importance_section(spec: Spec, obs: Observations) -> str:
    rows = [o for o in obs if o.metrics]
    if len(rows) < 8:
        return f"_needs at least 8 observations with metrics (have {len(rows)})_"
    try:
        import lightgbm
        import numpy as np
        import shap
    except ImportError:
        return "_not available: install the `advanced` extras (shap, lightgbm)_"
    x = np.array([space.to_raw(spec, o.params) for o in rows])
    names = [v.name for v in spec.variables]
    targets: dict[str, list[float]] = {}
    if spec.objective:
        targets["objective"] = [o.fom if o.fom is not None else math.nan for o in rows]
    for m in spec.metrics:
        targets[m.name] = [o.metrics.get(m.name, math.nan) for o in rows]
    lines = []
    for target, y in targets.items():
        y = np.array(y, dtype=float)
        mask = np.isfinite(y)
        if mask.sum() < 8 or np.allclose(y[mask], y[mask][0]):
            continue
        model = lightgbm.LGBMRegressor(n_estimators=200, learning_rate=0.05, min_child_samples=2, verbose=-1).fit(x[mask], y[mask])
        values = np.abs(shap.TreeExplainer(model).shap_values(x[mask])).mean(axis=0)
        total = values.sum() or 1.0
        ranked = sorted(zip(names, values / total, strict=True), key=lambda kv: -kv[1])
        lines.append(f"- {target}: " + ", ".join(f"{n} {100 * s:.1f}%" for n, s in ranked))
    return "\n".join(lines) or "_no target had enough variation_"


def _corners_section(spec: Spec, obs: Observations) -> str:
    lines = [f"- policy: objective={spec.corner_policy.objective}, constraints={spec.corner_policy.constraints}"]
    top = obs.best(1)
    if top:
        per_corner = _metrics_per_corner(top[0])
        lines.append(f"- best observation {top[0].obs_id} per corner:")
        for corner, metrics in per_corner.items():
            ev = objective_contract.evaluate(spec, metrics)
            lines.append(f"  - {corner}: {ev.status}, objective {_fmt(ev.objective)}, " + ", ".join(f"{k}={_fmt(v)}" for k, v in metrics.items()))
    failures: dict[str, int] = {c.id: 0 for c in spec.corners}
    for o in obs:
        for corner, metrics in _metrics_per_corner(o).items():
            child_failed = any(ch.status != "ok" for ch in o.children.values() if (ch.corner or "nominal") == corner)
            if child_failed or objective_contract.evaluate(spec, metrics).status != "ok":
                failures[corner] = failures.get(corner, 0) + 1
    lines.append("- failures per corner: " + ", ".join(f"{k} {v}/{len(obs)}" for k, v in failures.items()))
    return "\n".join(lines)


def _metrics_per_corner(o: Observation) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for child in o.children.values():
        out.setdefault(child.corner or "nominal", {}).update(child.metrics)
    return out


def _advisory_section(spec: Spec, obs: Observations) -> str:
    rows = [o for o in obs if o.status in ("ok", "constraint_failed") and o.fom is not None]
    if len(rows) < 3:
        return f"_needs at least 3 scored observations (have {len(rows)})_"
    try:
        from openbox import logger as openbox_logger
        from openbox.compressor import Compressor, create_steps_from_strings
        from openbox.utils.config_space import (
            Configuration,
            ConfigurationSpace,
            UniformFloatHyperparameter,
            UniformIntegerHyperparameter,
        )
        from openbox.utils.constants import SUCCESS
        from openbox.utils.history import History
        from openbox.utils.history import Observation as ObHistoryObservation
    except ImportError:
        return "_not available: openbox compressor missing_"
    openbox_logger.init(level="WARNING", logdir=None)
    cs = ConfigurationSpace()
    for v in spec.variables:
        lo, hi = float(space.parse_scalar(v.lower)[0]), float(space.parse_scalar(v.upper)[0])
        cs.add_hyperparameter(UniformIntegerHyperparameter(v.name, int(lo), int(hi)) if v.kind == "integer" else UniformFloatHyperparameter(v.name, lo, hi))
    history = History(task_id="ic_opt_advisory", num_objectives=1, num_constraints=0, config_space=cs)
    for o in rows:
        values = {v.name: (int(space.parse_scalar(o.params[v.name])[0]) if v.kind == "integer" else float(space.parse_scalar(o.params[v.name])[0])) for v in spec.variables}
        history.update_observation(ObHistoryObservation(config=Configuration(cs, values=values), objectives=[o.objective if o.objective is not None else 1e6 + o.constraint_penalty], trial_state=SUCCESS, elapsed_time=0.0))
    steps = create_steps_from_strings(["d_none", "r_boundary", "p_none"], step_params={"r_boundary": {"top_ratio": 0.3, "sigma": 2.0}})
    Compressor(config_space=cs, steps=steps).compress_space(space_history=[history], source_similarities={0: 1.0})
    lines = []
    for step in steps:
        for item in (step.get_step_info().get("compression_info") or {}).get("compressed_params", []) or []:
            orig, comp = item.get("original_range"), item.get("compressed_range")
            if item.get("name") in {v.name for v in spec.variables} and orig and comp:
                lines.append(f"- {item['name']}: {orig[0]:g}..{orig[1]:g} → {comp[0]:g}..{comp[1]:g} (advisory only, not applied)")
    return "\n".join(lines) or "_compressor produced no narrower ranges_"


# -- figures ----------------------------------------------------------------------

def _figures(spec: Spec, obs: Observations, out: Path) -> dict[str, Path]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return {}
    figures: dict[str, Path] = {}
    index = {o.obs_id: i + 1 for i, o in enumerate(obs)}
    feasible = [(index[o.obs_id], o.objective) for o in obs if o.feasible and o.objective is not None]

    if feasible:
        fig, ax = plt.subplots(figsize=(8, 4))
        xs, ys = zip(*feasible, strict=True)
        best_so_far = [min(ys[: i + 1]) for i in range(len(ys))]
        ax.plot(xs, ys, "o-", color="#5a6ff0", alpha=0.8, label="feasible objective")
        ax.plot(xs, best_so_far, color="#2f9e44", linewidth=2, label="best feasible so far")
        ax.set(title="Feasible objective convergence", xlabel="observation", ylabel="objective (min form)")
        ax.legend(fontsize=8)
        figures["feasible_convergence"] = _save(fig, out / "feasible_convergence.png")

    fig, (ax, strip) = plt.subplots(2, 1, figsize=(8, 5), sharex=True, gridspec_kw={"height_ratios": [4, 1]})
    if feasible:
        xs, ys = zip(*feasible, strict=True)
        ax.plot(xs, ys, "o", color=STATUS_COLORS["ok"], alpha=0.8, label="feasible")
        ax.plot(xs, [min(ys[: i + 1]) for i in range(len(ys))], color="#111827", linewidth=1.5, label="best so far")
        ax.legend(fontsize=8)
    ax.set(title="All observations", ylabel="objective (feasible only)")
    for status, color in STATUS_COLORS.items():
        pts = [index[o.obs_id] for o in obs if (o.status if o.status in STATUS_COLORS else "metric_failed") == status]
        if pts:
            strip.scatter(pts, [0] * len(pts), color=color, s=18, label=status)
    strip.set(yticks=[], xlabel="observation")
    strip.legend(fontsize=7, loc="upper left", ncol=3)
    figures["convergence"] = _save(fig, out / "convergence.png")

    if spec.constraints:
        n = len(spec.constraints)
        cols = 2 if n > 1 else 1
        fig, axes = plt.subplots(math.ceil(n / cols), cols, figsize=(5 * cols, 3.2 * math.ceil(n / cols)), squeeze=False)
        for axis, c in zip(axes.flat, spec.constraints, strict=False):
            rows = [(index[o.obs_id], _margin(spec, c, o.metrics[c.metric])) for o in obs if c.metric in o.metrics]
            values = [o.metrics[c.metric] for o in obs if c.metric in o.metrics]
            scale_ = (max(values) - min(values)) if values and max(values) > min(values) else (abs(float(space.parse_scalar(c.value.replace(" ", ""))[0])) or 1.0)
            if rows:
                xs, ms = zip(*rows, strict=True)
                axis.plot(xs, [m / scale_ for m in ms], "-", color="#c4c8d4", linewidth=0.8)
                axis.scatter(xs, [m / scale_ for m in ms], c=["#2f9e44" if m >= 0 else "#d64545" for m in ms], s=18)
            axis.axhline(0, color="#111827", linewidth=0.8)
            axis.set(title=f"{c.metric} {c.op} {c.value}", ylabel="margin / observed range")
        for axis in list(axes.flat)[n:]:
            axis.set_visible(False)
        fig.suptitle("Constraint margins (positive = pass)")
        figures["constraint_margins"] = _save(fig, out / "constraint_margins.png")

    model = score_model(spec.objective.expression) if spec.objective else None
    if model:
        pts = []
        for o in obs:
            scores = _component_scores(model, o.metrics)
            if scores:
                pts.append((o, sum(model["weights"][k] * s for k, s in scores.items()) / sum(model["weights"].values()), min(scores.values())))
        if pts:
            fig, ax = plt.subplots(figsize=(6.5, 5.5))
            a, b = model["bottleneck_weight"], model["sum_weight"]
            for score, color in [(0.2, "#5a6ff0"), (0.4, "#e08b2d"), (0.6, "#2f9e44"), (0.8, "#d64545")]:
                grid_x = [i / 50 for i in range(51)]
                line = [((score - b * x) / a) if a else None for x in grid_x]
                xy = [(x, y) for x, y in zip(grid_x, line, strict=True) if y is not None and 0 <= y <= 1]
                if xy:
                    ax.plot([p[0] for p in xy], [p[1] for p in xy], color=color, linewidth=1, alpha=0.7)
                    ax.text(xy[-1][0], xy[-1][1], f"score={score:.1f}", fontsize=8, color=color)
            for status, color in STATUS_COLORS.items():
                sel = [(w, m) for o, w, m in pts if (o.status if o.status in STATUS_COLORS else "metric_failed") == status]
                if sel:
                    ax.scatter([s[0] for s in sel], [s[1] for s in sel], color=color, s=22, alpha=0.8, label=status)
            top = obs.best(1)
            if top:
                hit = next(((w, m) for o, w, m in pts if o.obs_id == top[0].obs_id), None)
                if hit:
                    ax.scatter([hit[0]], [hit[1]], marker="*", s=110, color="#111827", label=f"best {top[0].obs_id}", zorder=5)
            ax.set(xlim=(-0.02, 1.02), ylim=(-0.02, 1.02), xlabel="weighted-sum score", ylabel="bottleneck score min(z_i)",
                   title="Normalized margin bottleneck vs weighted score")
            ax.legend(fontsize=8)
            figures["bottleneck_weighted_score"] = _save(fig, out / "bottleneck_weighted_score.png")
    return figures


def _save(fig, path: Path) -> Path:
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    import matplotlib.pyplot as plt

    plt.close(fig)
    return path


# -- objective score model (bottleneck + weighted sum) ------------------------------

def score_model(expression: str) -> dict[str, Any] | None:
    """Parse ``±(a*min(z1..zn) + b*(w1*z1 + ... + wn*zn))``; None when the objective has another shape."""
    try:
        root = ast.parse(expression, mode="eval").body
    except SyntaxError:
        return None
    if isinstance(root, ast.UnaryOp) and isinstance(root.op, ast.USub):
        root = root.operand
    terms = _flatten_add(root)
    if len(terms) != 2:
        return None
    min_call = _min_call(root)                      # the widest min(...) is the bottleneck
    if min_call is None:
        return None
    parsed = [_split_product(t) for t in terms]
    bottleneck = next(((c, p) for c, p in parsed if p is not None and _contains(p, min_call)), None)
    weighted = next(((c, p) for c, p in parsed if p is not None and not _contains(p, min_call)), None)
    if bottleneck is None or weighted is None:
        return None
    components = {_label(arg, i): ast.unparse(arg) for i, arg in enumerate(min_call.args, 1)}
    by_dump = {ast.dump(arg): _label(arg, i) for i, arg in enumerate(min_call.args, 1)}
    weights: dict[str, float] = {}
    for term in _flatten_add(weighted[1]):
        coeff, payload = _split_product(term)
        if coeff is None or payload is None or ast.dump(payload) not in by_dump:
            return None
        weights[by_dump[ast.dump(payload)]] = weights.get(by_dump[ast.dump(payload)], 0.0) + coeff
    if set(weights) != set(components):
        return None
    return {"components": components, "weights": weights, "bottleneck_weight": bottleneck[0], "sum_weight": weighted[0]}


def _component_scores(model: dict[str, Any], metrics: dict[str, float]) -> dict[str, float] | None:
    if not metrics:
        return None
    scores = {}
    for label, expr in model["components"].items():
        try:
            value = objective_contract.evaluate_expression(expr, metrics)
        except (KeyError, ValueError):
            return None
        scores[label] = max(0.0, min(1.0, value))
    return scores


def _contains(node: ast.AST, target: ast.AST) -> bool:
    return any(ast.dump(n) == ast.dump(target) for n in ast.walk(node))


def _min_call(node: ast.AST) -> ast.Call | None:
    calls = [n for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "min" and len(n.args) >= 2]
    return max(calls, key=lambda c: len(c.args)) if calls else None


def _flatten_add(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return [*_flatten_add(node.left), *_flatten_add(node.right)]
    return [node]


def _split_product(node: ast.AST) -> tuple[float | None, ast.AST | None]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        for coeff, payload in ((node.left, node.right), (node.right, node.left)):
            if isinstance(coeff, ast.Constant) and isinstance(coeff.value, int | float):
                return float(coeff.value), payload
    return None, None


def _label(node: ast.AST, index: int) -> str:
    names = list(dict.fromkeys(n.id for n in ast.walk(node) if isinstance(n, ast.Name) and n.id not in {"min", "max", "ln"}))
    return names[0] if len(names) == 1 else f"component_{index}"


# -- html --------------------------------------------------------------------------

def _html(title: str, intro: str, sections: list[tuple[str, str]], figures: dict[str, Path]) -> str:
    parts = [f"<h1>{html.escape(title)}</h1><p>{html.escape(intro)}</p>"]
    for heading, body in sections:
        parts.append(f"<h2>{html.escape(heading)}</h2>")
        if heading == "Figures":
            for name, path in figures.items():
                data = base64.b64encode(path.read_bytes()).decode()
                parts.append(f'<figure><img src="data:image/png;base64,{data}" alt="{name}"><figcaption>{name}</figcaption></figure>')
            continue
        parts.append(_md_to_html(body))
    style = "body{font:14px/1.5 system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#1f2937}table{border-collapse:collapse}td,th{border:1px solid #d1d5db;padding:2px 8px;font-variant-numeric:tabular-nums}img{max-width:100%}figure{margin:1rem 0}code{background:#f3f4f6;padding:0 3px}"
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>{style}</style></head><body>{''.join(parts)}</body></html>"


def _md_to_html(body: str) -> str:
    lines = body.splitlines()
    if lines and lines[0].startswith("|"):
        rows = [[c.strip() for c in line.strip("|").split("|")] for line in lines if not set(line.replace("|", "").strip()) <= {"-", " "}]
        head = "".join(f"<th>{html.escape(c)}</th>" for c in rows[0])
        tail = "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
        return f"<table><tr>{head}</tr>{tail}</table>"
    out, in_list = [], False
    for line in lines:
        if line.startswith(("- ", "  - ")):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(line.lstrip(' -'))}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            if line.strip():
                out.append(f"<p>{_inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


def _inline(text: str) -> str:
    escaped = html.escape(text)
    if escaped.startswith("_") and escaped.endswith("_"):
        return f"<em>{escaped[1:-1]}</em>"
    while "`" in escaped:
        start = escaped.index("`")
        end = escaped.find("`", start + 1)
        if end < 0:
            break
        escaped = escaped[:start] + "<code>" + escaped[start + 1 : end] + "</code>" + escaped[end + 1 :]
    return escaped
