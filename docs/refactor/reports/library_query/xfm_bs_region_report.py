"""Review page for lib.region answers: the sweep ranges the library recommends for a set of target windows.

Usage: xfm_bs_region_report.py OUT_HTML LIBRARY_ROOT REGION_JSON [REGION_JSON ...]   (one lib.region answer per stratum)

A REGION_JSON is what ``ic-opt call lib.region LIBRARY_ROOT stratum=S 'targets={...}' group_by=A,B trend=Q:D`` prints
(docs/em/library.md, section 5b). The page adds what an answer leaves to the library: each dim's achieved range and
manifest step, the part's fixed fields, how many rows measured every target quantity, and the calibration cached for
each model (read, never computed). Its figures -- the first two dims' projection, the count per pair of group_by dims,
the trend -- are saved under figs/ next to the page and embedded as data URIs.
"""
from __future__ import annotations

import base64
import html
import json
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import yaml

from ic_opt.library import query

out, ROOT = Path(sys.argv[1]), Path(sys.argv[2])
ANSWERS = [json.loads(Path(p).read_text(encoding="utf-8")) for p in sys.argv[3:]]
LIB = query.Library(ROOT)
FIGS = out.parent / "figs"
DIM = {"primary_outer_diameter_um": ("初级外径", "OD_P"), "secondary_outer_diameter_um": ("次级外径", "OD_S"),
       "primary_width_um": ("初级线宽", "W_P"), "secondary_width_um": ("次级线宽", "W_S"), "center_spacing_um": ("中心偏移", "CS"),
       "outer_diameter_um": ("外径", "OD"), "width_um": ("线宽", "W"), "spacing_um": ("间距", "S"), "turns": ("匝数", "NT")}
DEVICE = {"clean_port_xfm_bs": "单圈变压器"}
FIXED = {"primary_metal": "初级金属", "secondary_metal": "次级金属", "primary_opening_um": "初级开口", "secondary_opening_um": "次级开口",
         "primary_lead_length_um": "初级引线", "secondary_lead_length_um": "次级引线", "ground_fixture": "地环"}
plt.rcParams["font.family"] = ["Noto Sans CJK JP", "Droid Sans Fallback", "DejaVu Sans"]   # the SC face hides in a .ttc matplotlib lists under JP
plt.rcParams["axes.unicode_minus"] = False


def esc(v: object) -> str:
    return html.escape(str(v))


def abbr(d: str) -> str:
    """A dim's short name (``OD_P``), plain text for the figures."""
    return DIM.get(d, (d, d))[1]


def short(d: str) -> str:
    return esc(abbr(d))


def label(d: str) -> str:
    return f"{DIM[d][0]} {DIM[d][1]}" if d in DIM else esc(d)


def um(d: str) -> str:
    return " µm" if d.endswith("_um") else ""


def axis(d: str) -> str:
    return abbr(d) + (" (µm)" if d.endswith("_um") else "")


def span(r: list[float] | None, d: str, unit: bool = True) -> str:
    if not r:
        return "–"
    text = f"{r[0]:g}" if r[0] == r[1] else f"{r[0]:g} – {r[1]:g}"
    return text + (um(d) if unit else "")


def scale(q: str) -> tuple[float, str]:
    """Display units: L in pH, SRF in GHz, k and Q plain."""
    if q.startswith("SRF"):
        return 1e-9, "GHz"
    if q.startswith("L"):
        return 1e12, "pH"
    return 1.0, ""


def fmt_q(q: str, v: float | None, none: str = "–") -> str:
    if v is None:
        return none
    k, unit = scale(q)
    digits = 3 if q.startswith("k") else 1 if q.startswith("Q") else 0
    return f"{v * k:.{digits}f}" + (f" {unit}" if unit else "")


def pct(v: float | None, digits: int = 2) -> str:
    return "–" if v is None else f"{v * 100:.{digits}f}%"


def bounds(t: dict) -> tuple[float | None, float | None]:
    """A target's window in SI units, None at an open end."""
    if t["kind"] == "min":
        return t["value"], None
    if t["kind"] == "max":
        return None, t["value"]
    if t["kind"] == "window":
        return t["value"], t["upper"]
    return t["value"] * (1 - t["tol"]), t["value"] * (1 + t["tol"])


def target_text(t: dict) -> str:
    """Plain text: ``Lp@40 150–170 pH``, ``Qp@40 ≥ 10``, ``Lp_lf = 1200 pH ± 3%``."""
    q = t["quantity"]
    k, unit = scale(q)
    u = f" {unit}" if unit else ""
    lo, hi = bounds(t)
    if t["kind"] == "target":
        return f"{q} = {t['value'] * k:.4g}{u} ± {t['tol'] * 100:g}%"
    if hi is None:
        return f"{q} ≥ {lo * k:.4g}{u}"
    if lo is None:
        return f"{q} ≤ {hi * k:.4g}{u}"
    return f"{q} {lo * k:.4g}–{hi * k:.4g}{u}"


def targets_html(a: dict) -> str:
    """The answer's targets; one that lib.region added (an anchored quantity implies SRF >= margin x f0) is marked."""
    implied = {t["quantity"] for t in a["targets"] if any(n.startswith(f"added {t['quantity']} >= ") for n in a["notes"])}   # region's note
    return "，".join(esc(target_text(t)) + ("（锚定量隐含）" if t["quantity"] in implied else "") for t in a["targets"])


def quantities(a: dict) -> list[str]:
    """The answer's quantities: the targets' in their order, then the objective's and the trend's."""
    extra = [a["objective"].partition(":")[2] if a["objective"] else None, a["trend"]["quantity"]]
    return list(dict.fromkeys([t["quantity"] for t in a["targets"]] + [q for q in extra if q]))


def empty(columns: int) -> str:
    return f"<tr><td colspan='{columns}' class='dim'>无</td></tr>"


# -- figures -------------------------------------------------------------------------------------------------------------

def save(fig, name: str) -> str:
    """``fig`` as figs/<name>.png next to the page; the data URI the page embeds."""
    FIGS.mkdir(parents=True, exist_ok=True)
    path = FIGS / f"{name}.png"
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def coords(rows: list[dict], d: str) -> list[float]:
    return [r["params"][d] for r in rows]


def fig_projection(stratum: str, a: dict, dims: list[str]) -> str | None:
    """The first two dims: the (sampled) mean set, its robust points on top, the measured rows meeting every target as stars."""
    pts, hits = a["points_sample"], a["measured"]
    if not pts and not hits:
        return None
    dx, dy = dims[:2]
    n_mean, robust = a["levels"]["mean"]["count"], [p for p in pts if p["level"] == "robust"]
    fig, ax = plt.subplots(figsize=(7.0, 5.2))
    ax.scatter(coords(pts, dx), coords(pts, dy), s=10, c="#9fc7b3",
               label=f"预测均值满足（{n_mean} 点" + (f"，图中抽样 {len(pts)} 点）" if len(pts) < n_mean else "）"))
    if robust:
        ax.scatter(coords(robust, dx), coords(robust, dy), s=12, c="#2f6b4f", label=f"2σ 区间整体满足（{a['levels']['robust']['count']} 点）")
    if hits:
        ax.scatter(coords(hits, dx), coords(hits, dy), marker="*", s=170, c="#c8102e", edgecolors="k", linewidths=0.5, zorder=5,
                   label=f"库内实测满足（{len(hits)} 行）")
    ax.set_xlabel(axis(dx))
    ax.set_ylabel(axis(dy))
    ax.set_title(f"{stratum}：可行 {abbr(dx)} × {abbr(dy)}（其余维投影）")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    return save(fig, f"{out.stem}_{stratum}_projection")


def fig_groups(stratum: str, a: dict) -> str | None:
    """Mean-set points per combination of exactly two group_by dims, the robust ones in brackets."""
    g = a["group_by"]
    if len(g["dims"]) != 2 or not g["rows"]:
        return None
    dx, dy = g["dims"]
    xs, ys = sorted({r["key"][dx] for r in g["rows"]}), sorted({r["key"][dy] for r in g["rows"]})
    mean, robust = np.zeros((len(ys), len(xs))), np.zeros((len(ys), len(xs)))
    for r in g["rows"]:
        i, j = ys.index(r["key"][dy]), xs.index(r["key"][dx])
        mean[i, j], robust[i, j] = r["count_mean"], r["count_robust"]
    fig, ax = plt.subplots(figsize=(7.0, 5.2))
    im = ax.imshow(mean, origin="lower", cmap="Greens", aspect="auto")
    ax.set_xticks(range(len(xs)), [f"{v:g}" for v in xs])
    ax.set_yticks(range(len(ys)), [f"{v:g}" for v in ys])
    written = mean.size <= 100                                    # numbers in the cells while they stay legible
    if written:
        for i, j in zip(*np.nonzero(mean)):
            text = f"{mean[i, j]:.0f}" + (f"\n({robust[i, j]:.0f})" if robust[i, j] else "")
            ax.text(j, i, text, ha="center", va="center", fontsize=7, color="white" if im.norm(mean[i, j]) > 0.6 else "black")
    ax.set_xlabel(axis(dx))
    ax.set_ylabel(axis(dy))
    ax.set_title(f"{stratum}：均值可行点数按 {abbr(dx)} × {abbr(dy)}"
                 + ("（括号内为 2σ 稳健）" if written else ""))
    fig.colorbar(im, ax=ax, fraction=0.046, label="均值可行点数")
    return save(fig, f"{out.stem}_{stratum}_groups")


def fig_trend(stratum: str, a: dict) -> str | None:
    """The trend rows (min, median, max of the quantity per value of the dim) against the quantity's own target."""
    t = a["trend"]
    if not t["rows"]:
        return None
    q, d = t["quantity"], t["dim"]
    k, unit = scale(q)
    x = np.array([r["value"] for r in t["rows"]])
    lo, med, hi = (np.array([r[key] for r in t["rows"]]) * k for key in ("min", "median", "max"))
    fig, ax = plt.subplots(figsize=(7.0, 5.2))
    ax.errorbar(x, med, yerr=[med - lo, hi - med], fmt="o-", color="#2f6b4f", ecolor="#9fc7b3", elinewidth=4, markersize=4,
                label="中位；竖条为 min – max")
    for goal in (g for g in a["targets"] if g["quantity"] == q):
        a_lo, a_hi = bounds(goal)
        if a_lo is not None and a_hi is not None:
            ax.axhspan(a_lo * k, a_hi * k, color="#c8102e", alpha=0.08, label=f"目标 {target_text(goal)}")
        else:
            ax.axhline((a_hi if a_lo is None else a_lo) * k, color="#c8102e", linestyle="--", linewidth=1, label=f"目标 {target_text(goal)}")
    ax.set_xlabel(axis(d))
    ax.set_ylabel(f"预测 {q}" + (f" ({unit})" if unit else ""))
    ax.set_title(f"{stratum}：{q} 随 {abbr(d)}（满足其余全部目标的均值级格点）")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    return save(fig, f"{out.stem}_{stratum}_trend")


# -- tables --------------------------------------------------------------------------------------------------------------

def fixed_note(stratum: str) -> str:
    """The first part's device ``fixed`` fields and sweep, from the spec it ran with (.icopt/spec.json), else its spec.yaml."""
    part = LIB.manifest.strata[stratum].parts[0].store
    path = next((p for p in (ROOT / part / ".icopt" / "spec.json", ROOT / part / "spec.yaml") if p.is_file()), None)
    if path is None:
        return f"固定参数：部件 <code>{esc(part)}</code> 没有 spec。"
    text = path.read_text(encoding="utf-8")
    spec = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)

    def value(key: str, v: object) -> str:
        if isinstance(v, dict):
            return "（" + "，".join(f"{esc(k)}{value(k, w)}" for k, w in v.items()) + "）"
        if isinstance(v, int | float) and not isinstance(v, bool):
            return f" {v:g}{um(key)}"
        return f" <code>{esc(v)}</code>"

    order = list(FIXED)
    fixed = sorted(spec["devices"][0].get("fixed", {}).items(), key=lambda kv: order.index(kv[0]) if kv[0] in FIXED else len(order))
    fields = [f"{FIXED.get(key, esc(key))}{value(key, v)}" for key, v in fixed]
    em = spec.get("em") or {}
    if em:
        fields.append(f"EMX 扫到 {em['frequencies']['stop_hz'] / 1e9:g} GHz，3D 金属 {esc(', '.join(em.get('three_d_metals') or [])) or '–'}")
    return f"固定参数（与库一致，取自部件 <code>{esc(part)}</code>）：" + "；".join(fields) + "。"


def edge_note(a: dict) -> str:
    if not a["levels"]["mean"]["count"]:
        return ""
    edge = [f"{short(d)} 触及{'下' if e['at_min'] else ''}{'上' if e['at_max'] else ''}界" for d, e in a["edge"].items() if e["at_min"] or e["at_max"]]
    return ("可行区域 " + "、".join(edge) + "：该方向超出库覆盖范围后模型不再作答。") if edge else "可行区域未触及库覆盖范围的边界。"


def range_table(stratum: str, a: dict, dims: list[str]) -> str:
    ds = LIB.dataset(stratum)
    x, steps = ds.matrix(), LIB.manifest.strata[stratum].steps
    rows = []
    for i, d in enumerate(dims):
        grid = a["grid"]["steps"].get(d)
        rows.append(f"<tr><td>{label(d)}<br><span class='small'><code>{esc(d)}</code></span></td>"
                    f"<td class='num'>{span(a['levels']['robust']['ranges'].get(d), d)}</td><td class='num'>{span(a['levels']['mean']['ranges'].get(d), d)}</td>"
                    f"<td class='num'>{span([float(x[:, i].min()), float(x[:, i].max())], d)}</td>"
                    f"<td class='num'>{f'{steps[d]:g}{um(d)}' if d in steps else '–'}</td>"
                    f"<td class='num'>{f'{grid:g}{um(d)}' if grid else '按层' if d == ds.nt_dim else '–'}</td></tr>")
    return ("<div class='table-wrap'><table><thead><tr><th>参数</th><th class='num'>建议扫描范围（2σ 稳健）</th><th class='num'>均值可行范围（外包络）</th>"
            "<th class='num'>库覆盖范围</th><th class='num'>库步长</th><th class='num'>网格步长</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div>")


def group_table(a: dict, dims: list[str]) -> str:
    g = a["group_by"]
    if not g["dims"]:
        return ""
    rest = [d for d in dims if d not in g["dims"]]
    obj = a["objective"].partition(":")[2] if a["objective"] else None
    head = ("".join(f"<th class='num'>{short(d)}</th>" for d in g["dims"]) + "".join(f"<th class='num'>{short(d)} 范围</th>" for d in rest)
            + (f"<th class='num'>{esc(obj)} 预测范围</th>" if obj else "") + "<th class='num'>2σ 稳健点数</th><th class='num'>均值点数</th>")
    body = "".join(
        "<tr>" + "".join(f"<td class='num'>{r['key'][d]:g}</td>" for d in g["dims"])
        + "".join(f"<td class='num'>{span(r['ranges'][d], d, unit=False)}</td>" for d in rest)
        + (f"<td class='num'>{fmt_q(obj, r['objective'][0])} – {fmt_q(obj, r['objective'][1])}</td>" if obj else "")
        + (f"<td class='num'><b>{r['count_robust']}</b></td>" if r["count_robust"] else "<td class='num dim'>0</td>")
        + f"<td class='num'>{r['count_mean']}</td></tr>" for r in g["rows"])
    names = "、".join(short(d) for d in g["dims"])
    unit = "，µm" if any(d.endswith("_um") for d in rest) else ""
    return (f"<h3>按 {names} 分组看其余维（均值可行点的范围{unit}；2σ 稳健点数加粗）</h3>"
            f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{body or empty(len(dims) + 2 + bool(obj))}</tbody></table></div>")


def trend_table(a: dict) -> str:
    t = a["trend"]
    if not t["quantity"]:
        return ""
    q, d = t["quantity"], t["dim"]
    body = "".join(f"<tr><td class='num'>{r['value']:g}</td><td class='num'>{fmt_q(q, r['min'])}</td><td class='num'>{fmt_q(q, r['median'])}</td>"
                   f"<td class='num'>{fmt_q(q, r['max'])}</td><td class='num'>{r['count']}</td></tr>" for r in t["rows"])
    return (f"<h3>{esc(q)} 随 {short(d)}（满足其余全部目标的均值级格点）</h3><div class='table-wrap'><table><thead><tr>"
            f"<th class='num'>{esc(axis(d))}</th><th class='num'>{esc(q)} 最小</th><th class='num'>中位</th><th class='num'>最大</th><th class='num'>点数</th>"
            f"</tr></thead><tbody>{body or empty(5)}</tbody></table></div>")


def candidate_table(a: dict, dims: list[str], qn: list[str]) -> str:
    level = "2σ 稳健" if a["candidates_level"] == "robust" else "均值"
    sense, _, obj = (a["objective"] or "").partition(":")
    rule = (f"按 {esc(obj)} 的 2σ {'下界从高到低' if sense == 'max' else '上界从低到高'}" if obj else "按离目标窗中点从近到远")
    rows = "".join(
        "<tr><td>" + ", ".join(f"{short(d)}={c['params'][d]:g}" for d in dims) + "</td>"
        + "".join(f"<td class='num'>{fmt_q(q, c['predicted'][q]['value'])}<br><span class='small'>[{fmt_q(q, c['predicted'][q]['lo'])}, "
                  f"{fmt_q(q, c['predicted'][q]['hi'], none='∞')}]</span></td>" for q in qn)
        + "</tr>" for c in a["candidates"])
    head = f"代表性候选（{level}集里{rule}排序的前 {len(a['candidates'])} 个，彼此拉开）" if a["candidates"] else "代表性候选"
    return (f"<h3>{head}</h3>"
            "<div class='table-wrap'><table><thead><tr><th>参数</th>" + "".join(f"<th class='num'>{esc(q)}<br><span class='small'>预测 [2σ]</span></th>" for q in qn)
            + f"</tr></thead><tbody>{rows or empty(len(qn) + 1)}</tbody></table></div>")


def measured_table(a: dict, dims: list[str], qn: list[str]) -> str:
    rows = "".join(
        f"<tr><td>{esc(m['part'])} / {esc(m['obs_id'])}<br><span class='small'>" + ", ".join(f"{short(d)}={m['params'][d]:g}" for d in dims) + "</span></td>"
        + "".join(f"<td class='num'>{fmt_q(q, m['values'].get(q))}</td>" for q in qn) + "</tr>" for m in a["measured"])
    return ("<h3>库内实测已满足全部条件的行</h3><div class='table-wrap'><table><thead><tr><th>观测</th>"
            + "".join(f"<th class='num'>{esc(q)}</th>" for q in qn) + f"</tr></thead><tbody>{rows or empty(len(qn) + 1)}</tbody></table></div>")


def calibration_table(stratum: str, qn: list[str]) -> str:
    """The calibration each model was fitted with, where the library's cache holds it (lib.region's fits write it)."""
    rows = []
    for q in qn:
        ds, used, x, y, settings = LIB._fit_inputs(stratum, q)
        cal = LIB._calibration(ds, q, x, y, settings, compute=False)
        if cal:
            rows.append(f"<tr><td><code>{esc(q)}</code></td><td class='num'>{len(used)}</td><td class='num'>{pct(cal.get('median_rel'))}</td>"
                        f"<td class='num'>{pct(cal.get('coverage_2sigma_before'), 1)}</td><td class='num'>{cal['k_scale']:.2f}</td></tr>")
    if not rows:
        return ""
    return ("<details><summary class='note'>模型校准（留出 5 折）</summary><div class='table-wrap'><table><thead><tr><th>量</th><th class='num'>行</th>"
            "<th class='num'>留出中位误差</th><th class='num'>2σ 覆盖（校准前）</th><th class='num'>k_scale</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div></details>")


# -- the page ------------------------------------------------------------------------------------------------------------

def tightest(a: dict) -> str | None:
    """The target the fewest confident grid points meet alone; None without a grid (the notes say why)."""
    return min(a["binding"], key=a["binding"].get) if a["binding"] and a["grid"]["confident"] else None


def stratum_section(a: dict) -> str:
    stratum = a["stratum"]
    ds, declared = LIB.dataset(stratum), LIB.manifest.strata[stratum]
    dims, qn = ds.dims, quantities(a)
    rb, mn, grid, sec = a["levels"]["robust"], a["levels"]["mean"], a["grid"], a["seconds"]
    full = sum(all(r.values.get(t["quantity"]) is not None for t in a["targets"]) for r in ds.rows)
    tight = tightest(a)
    figures = [(uri, caption) for uri, caption in (
        (fig_projection(stratum, a, dims), f"前两维投影：{short(dims[0])} × {short(dims[1])}"),
        (fig_groups(stratum, a), "按分组两维的均值可行点数"),
        (fig_trend(stratum, a), f"{esc(a['trend']['quantity'])} 随 {short(a['trend']['dim'])}" if a["trend"]["quantity"] else "")) if uri]
    figs = "".join(f"<figure><img src='{uri}' alt='{caption}'><figcaption>{caption}</figcaption></figure>" for uri, caption in figures)
    same = a["targets"] == ANSWERS[0]["targets"]
    notes = "".join(f"<li>{esc(n)}</li>" for n in a["notes"])
    return f"""
<h2>{esc(stratum)}</h2>
<p class="note">{esc(DEVICE.get(declared.generator, declared.generator))}（<code>{esc(declared.generator)}</code>），部件 {'、'.join(f'<code>{esc(p.store)}</code>' for p in declared.parts)}，库 {len(ds.rows)} 行。{'' if same else '目标：' + targets_html(a) + '。'}
网格 {grid['points']} 格（域内 {grid['in_domain']}、可信 {grid['confident']}）{'，自动步长' if grid['auto_steps'] else ''}{f"，步长已放粗 ×{grid['coarsened']}" if grid['coarsened'] > 1 else ''}；
耗时 {sec['total']:.0f} s（模型 {sec['models']:.0f} s、粗筛 {sec['coarse']:.0f} s、网格 {sec['grid']:.0f} s、预测 {sec['predict']:.0f} s、汇总 {sec['summarize']:.0f} s）。</p>
<div class="kpis">
  <div class="kpi"><div class="v">{rb['count']}</div><div class="l">2σ 区间整体满足的网格点</div></div>
  <div class="kpi"><div class="v">{mn['count']}</div><div class="l">预测均值满足的网格点（共 {grid['confident']} 个域内可信点）</div></div>
  <div class="kpi"><div class="v">{len(a['measured'])}</div><div class="l">库内实测已满足全部目标的行（{full} 行有全部目标量的实测值）</div></div>
  <div class="kpi"><div class="v">{esc(tight or '–')}</div><div class="l">最紧约束（单项满足的均值级格点最少）：{' / '.join(f'{esc(q)} {c}' for q, c in a['binding'].items())}</div></div>
</div>
<h3>扫参范围</h3>
{range_table(stratum, a, dims)}
<p class="note">{fixed_note(stratum)}{edge_note(a)}</p>
{f"<div class='figs'>{figs}</div>" if figs else ''}
{group_table(a, dims)}
{trend_table(a)}
{candidate_table(a, dims, qn)}
{measured_table(a, dims, qn)}
{calibration_table(stratum, qn)}
{f"<details><summary class='note'>lib.region 的说明（{len(a['notes'])} 条）</summary><ul class='note'>{notes}</ul></details>" if notes else ''}
"""


def finding(a: dict) -> str:
    rb, mn, dims = a["levels"]["robust"], a["levels"]["mean"], LIB.dataset(a["stratum"]).dims
    hits = f"库内已有 {len(a['measured'])} 行实测满足全部条件。"
    if rb["count"]:
        body = (f"2σ 稳健可行 {rb['count']} 点，均值可行 {mn['count']} 点；{hits}建议扫描："
                + "；".join(f"{label(d)} {span(rb['ranges'][d], d)}" for d in dims) + "。")
    elif mn["count"]:
        body = f"2σ 稳健集为空；均值可行 {mn['count']} 点：" + "；".join(f"{label(d)} {span(mn['ranges'][d], d)}" for d in dims) + f"。{hits}"
    else:
        body = "网格上没有满足全部目标的点。" + (esc(a["notes"][-1]) if a["notes"] else "")
    return f"<div class='finding{' ok' if rb['count'] else ''}'><b>{esc(a['stratum'])}：</b>{body}</div>"


profile = LIB.manifest.process_profile
generators = sorted({LIB.manifest.strata[a["stratum"]].generator for a in ANSWERS})
device = DEVICE.get(generators[0], generators[0]) if len(generators) == 1 else "器件"
anchors = sorted({float(m.group(1)) for a in ANSWERS for t in a["targets"] if (m := re.search(r"@([0-9.]+)$", t["quantity"]))})
f0 = "/".join(f"{v:g}" for v in anchors)
title = " ".join(filter(None, [profile.split("_")[0].upper(), device, f"{f0} GHz" if f0 else "", "扫参范围"]))
group_dims = sorted({short(d) for a in ANSWERS for d in a["group_by"]["dims"]})
trends = sorted({f"{esc(a['trend']['quantity'])} 随 {short(a['trend']['dim'])}" for a in ANSWERS if a["trend"]["quantity"]})
reading = ("读法：范围是可行点在各维上的投影（外包络），维之间相关"
           + (f"——按分组表（{'、'.join(group_dims)}）取对应的其余维范围更准" if group_dims else "——各维范围不能任意组合")
           + (f"；{'、'.join(trends)} 的走势见各层走势表" if trends else "") + "。单项满足数最少的目标约束最紧："
           + "；".join(f"{esc(a['stratum'])} 为 {esc(tightest(a) or '–（网格为空）')}" for a in ANSWERS) + "。")
sections = "".join(stratum_section(a) for a in ANSWERS)
page = f"""<title>{esc(title)}</title>
<style>
:root {{ --ground:#f3f2ee; --surface:#fff; --surface-2:#e7e5de; --ink:#1b1c18; --muted:#5d5f55; --line:#d2d0c6; --accent:#2f6b4f; --warn:#9a4a12; --code-bg:#ebe9e2;
  --sans:"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif; --mono:"IBM Plex Mono",Menlo,Consolas,monospace; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --ground:#141512; --surface:#1d1e1a; --surface-2:#262822; --ink:#e7e8e0; --muted:#a2a497; --line:#383a32; --accent:#7cc0a0; --warn:#e3a066; --code-bg:#24261f; }} }}
:root[data-theme="dark"] {{ --ground:#141512; --surface:#1d1e1a; --surface-2:#262822; --ink:#e7e8e0; --muted:#a2a497; --line:#383a32; --accent:#7cc0a0; --warn:#e3a066; --code-bg:#24261f; }}
body {{ background:var(--ground); color:var(--ink); font-family:var(--sans); font-size:15px; line-height:1.7; margin:0; padding-block:0 64px; padding-inline:16px; }}
.wrap {{ max-width:1120px; margin:0 auto; }}
header {{ padding-block:36px 18px; border-bottom:2px solid var(--accent); }}
.eyebrow {{ font-family:var(--mono); font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }}
h1 {{ font-size:31px; margin:8px 0 10px; text-wrap:balance; }}
h2 {{ font-size:22px; margin:40px 0 10px; border-bottom:1px solid var(--line); padding-bottom:4px; }}
h3 {{ font-size:17px; margin:26px 0 8px; }}
p {{ margin:0 0 12px; max-width:88ch; }}
code {{ font-family:var(--mono); font-size:.86em; background:var(--code-bg); padding:1px 5px; border-radius:3px; overflow-wrap:anywhere; }}
.kpis {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:16px 0; }}
.kpi {{ background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:11px 14px; }}
.kpi .v {{ font-family:var(--mono); font-size:21px; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }}
.kpi .l {{ font-size:12.5px; color:var(--muted); }}
.table-wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:6px; background:var(--surface); margin:0 0 14px; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; }}
th, td {{ text-align:left; padding:5px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ background:var(--surface-2); font-weight:500; }}
td.num, th.num {{ text-align:right; font-family:var(--mono); font-variant-numeric:tabular-nums; white-space:nowrap; }}
.dim {{ color:var(--muted); }}
.small {{ font-size:12px; color:var(--muted); }}
.finding {{ border:1px solid var(--line); border-left:4px solid var(--warn); background:var(--surface); border-radius:6px; padding:10px 14px; margin:0 0 10px; }}
.finding b {{ color:var(--warn); }}
.ok {{ border-left-color:var(--accent); }}
.ok b {{ color:var(--accent); }}
.note {{ font-size:13px; color:var(--muted); }}
.figs {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr)); gap:12px; margin:10px 0 14px; }}
figure {{ margin:0; background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:8px; }}
figure img {{ display:block; width:100%; height:auto; }}
figcaption {{ font-size:12.5px; color:var(--muted); margin-top:4px; }}
details {{ margin:10px 0; }}
@media (max-width:760px) {{ .kpis {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">ic-opt-modular · 查询库 · {esc(profile)} · {esc(' / '.join(generators))}{f' · {f0} GHz' if f0 else ''} · lib.region</div>
  <h1>{esc(title)}</h1>
  <p class="note">问题：{targets_html(ANSWERS[0])}{'' if all(a['targets'] == ANSWERS[0]['targets'] for a in ANSWERS) else '（各层目标不同，见各节）'}——参数化建模的扫参范围。
  分层：{'、'.join(f"<code>{esc(a['stratum'])}</code>" for a in ANSWERS)}。
  方法：<code>lib.region</code> 先用放宽 10% 的目标窗粗筛库内行与 Sobol 点定括号盒，盒内按清单步长的整数倍铺网格（自动时每维约 20 档，匝数按层；各维步长见扫参范围表），
  只保留库采样域内的格点，用库的 GP 模型对每个量预测一次；"2σ 稳健"= 校准后的 2σ 区间整体落在目标窗内，"均值可行"= 预测均值落在窗内。</p>
</header>

<h2>结论</h2>
{''.join(finding(a) for a in ANSWERS)}
<p class="note">{reading}</p>
{sections}
<h2>位置</h2>
<p class="note">本页 <code>docs/refactor/reports/library_query/xfm_bs_region_report.py</code>；输入（每层一个 <code>ic-opt call lib.region</code> 的输出，命令见 <code>docs/em/library.md</code> 第 5b 节）：{'、'.join(f'<code>{esc(p)}</code>' for p in sys.argv[3:])}；图 <code>{esc(FIGS)}</code>；库 <code>{esc(ROOT)}</code>。</p>
</div>
"""
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(page, encoding="utf-8")
print(out, f"{out.stat().st_size / 1e3:.0f} kB")
