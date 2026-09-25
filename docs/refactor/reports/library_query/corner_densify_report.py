"""N-19 option 3 review page: the corner round of the single-turn transformer tables -- 25 lib.densify picks per table in
the corner OD_S/OD_P 1.10-1.25 x W_S 8-10 um x CS 14-22 um x OD_P 140-200 um through real EMX and adopted; the N-17 test
designs (never adopted) before vs after.

Usage: corner_densify_report.py OUT_HTML   (reads corner_*_xfm_bs_{ap,m10}.json next to this script; figures go to figs/)
"""
from __future__ import annotations

import base64
import collections
import html
import io
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
out = Path(sys.argv[1])
STRATA = ["xfm_bs_ap", "xfm_bs_m10"]
DIMS = [("primary_outer_diameter_um", "OD_P"), ("secondary_outer_diameter_um", "OD_S"), ("primary_width_um", "W_P"),
        ("secondary_width_um", "W_S"), ("center_spacing_um", "CS")]
MAIN = ["Lp@40", "Ls@40", "k@40", "SRF", "Qp@40", "Qs@40", "Lp_lf", "Ls_lf", "k_lf"]
SHOW = ["Lp@40", "Ls@40", "k@40", "SRF"]
plt.rcParams["font.family"] = ["Noto Sans CJK JP", "DejaVu Sans"]

ev = {s: json.loads((HERE / f"corner_evaluation_{s}.json").read_text(encoding="utf-8")) for s in STRATA}
picks = {s: json.loads((HERE / f"corner25_candidates_{s}.json").read_text(encoding="utf-8")) for s in STRATA}
# Hand-written reading of the numbers (filled in after the run); the tables and figures below are data.
FINDINGS: dict[str, list[tuple[str, str, str]]] = json.loads((HERE / "corner_findings.json").read_text(encoding="utf-8")) \
    if (HERE / "corner_findings.json").exists() else {}


def pct(x, signed=False):
    if x is None:
        return "–"
    return f"{x * 100:+.1f}%" if signed else f"{abs(x) * 100:.1f}%"


def cov(x) -> str:
    return "–" if x is None else f"{x:.0%}"


def figure(s: str) -> str:
    e = ev[s]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2))
    pts = e["test_points"]
    xs = list(range(1, len(pts) + 1))
    for ax, q in zip(axes, ["Lp@40", "SRF", "k@40"]):
        b = [abs(p["quantities"][q]["before"]["rel"] or 0) * 100 for p in pts]
        a = [abs(p["quantities"][q]["after"]["rel"] or 0) * 100 for p in pts]
        ax.bar([x - 0.2 for x in xs], b, width=0.4, color="#9a9c90", label=f"补点前（{e['rows_before']} 行）")
        ax.bar([x + 0.2 for x in xs], a, width=0.4, color="#2f6b4f", label=f"补点后（{e['rows_after']} 行）")
        ax.set_title(f"{s} · {q}：N-17 的 10 个测试点的相对误差")
        ax.set_xlabel("测试点")
        ax.set_ylabel("|预测/实测 − 1| (%)")
        ax.set_xticks(xs)
        ax.grid(axis="y", alpha=0.3)
    axes[0].legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    figs = HERE / "figs"
    figs.mkdir(exist_ok=True)
    fig.savefig(figs / f"{out.stem}_{s.split('_')[-1]}_test_points.png", dpi=130)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def summary_rows(s: str) -> str:
    rows = []
    for q in MAIN:
        b, a = ev[s]["summary"][q]["before"], ev[s]["summary"][q]["after"]
        rows.append(f"<tr><td>{html.escape(q)}</td><td class='num'>{pct(b['median_rel'])}</td><td class='num'>{pct(b['max_rel'])}</td>"
                    f"<td class='num'>{cov(b['coverage'])}</td><td class='num'>{pct(a['median_rel'])}</td>"
                    f"<td class='num'>{pct(a['max_rel'])}</td><td class='num'>{cov(a['coverage'])}</td></tr>")
    return "".join(rows)


def point_rows(s: str) -> str:
    rows = []
    for i, p in enumerate(ev[s]["test_points"], 1):
        geo = " / ".join(f"{p['params'][d]:g}" for d, _ in DIMS)
        cells = "".join(f"<td class='num'>{pct(p['quantities'][q]['before']['rel'], True)} → {pct(p['quantities'][q]['after']['rel'], True)}</td>"
                        for q in SHOW)
        srf = p["quantities"]["SRF"]["measured"]
        srf_text = "–" if srf is None else f"{srf / 1e9:.0f}"
        rows.append(f"<tr><td class='num'>{i}</td><td class='num'>{geo}</td><td class='num'>{srf_text}</td>{cells}</tr>")
    return "".join(rows)


def pick_rows(s: str) -> str:
    rows = []
    for q in SHOW:
        v = ev[s]["picks_signoff"]["per_quantity"][q]
        rows.append(f"<tr><td>{q}</td><td class='num'>{v['n']}</td><td class='num'>{pct(v['median_rel'])}</td>"
                    f"<td class='num'>{pct(v['max_rel'])}</td><td class='num'>{cov(v['coverage'])}</td></tr>")
    return "".join(rows)


def pick_geometry(s: str) -> str:
    c = picks[s]["candidates"]
    odp = collections.Counter(int(x["params"]["primary_outer_diameter_um"] // 30 * 30) for x in c)
    cs = collections.Counter(int(x["params"]["center_spacing_um"] // 4 * 4) for x in c)
    ratio = collections.Counter(round(x["params"]["secondary_outer_diameter_um"] / x["params"]["primary_outer_diameter_um"], 1) for x in c)
    return (" · ".join(f"OD_P {k}–{k + 30} µm：{v}" for k, v in sorted(odp.items())) + "<br>"
            + " · ".join(f"CS {k}–{k + 4} µm：{v}" for k, v in sorted(cs.items())) + "<br>"
            + " · ".join(f"外径比 ≈{k}：{v}" for k, v in sorted(ratio.items())))


def sigma_rows(s: str) -> str:
    rows = []
    for q in SHOW:
        b, a = picks[s]["before"][q]["rel_sigma"], picks[s]["after"][q]["rel_sigma"]
        rows.append(f"<tr><td>{q}</td><td class='num'>{pct(b['median'])}</td><td class='num'>{pct(b['p90'])}</td>"
                    f"<td class='num'>{pct(a['median'])}</td><td class='num'>{pct(a['p90'])}</td></tr>")
    return "".join(rows)


def findings(s: str) -> str:
    items = FINDINGS.get(s) or []
    if not items:
        e = ev[s]["summary"]
        better = [q for q in MAIN if (e[q]["after"]["median_rel"] or 0) <= (e[q]["before"]["median_rel"] or 0)]
        worse = [q for q in MAIN if q not in better]
        items = [("ok", "中位误差", f"回流后中位误差下降或持平的结果列：{', '.join(better) or '无'}；上升的：{', '.join(worse) or '无'}。")]
    return "".join(f"<div class='finding {cls}'><b>{html.escape(title)}</b> {text}</div>" for cls, title, text in items)


def stratum_section(s: str, n: int) -> str:
    e = ev[s]
    img = figure(s)
    sm = e["summary"]
    return f"""
<h2>{n} {s}：回流前 {e['rows_before']} 行 → 回流后 {e['rows_after']} 行</h2>
<div class="kpis">
  <div class="kpi"><div class="v">{e['picks_signoff']['ok']}/{e['picks_signoff']['n']}</div><div class="l">角落选点真实 EMX 成功</div></div>
  <div class="kpi"><div class="v">{pct(sm['Lp@40']['before']['median_rel'])} → {pct(sm['Lp@40']['after']['median_rel'])}</div><div class="l">Lp@40 测试点中位误差，回流前 → 后</div></div>
  <div class="kpi"><div class="v">{pct(sm['Qp@40']['before']['median_rel'])} → {pct(sm['Qp@40']['after']['median_rel'])}</div><div class="l">Qp@40 测试点中位误差，补点前 → 后</div></div>
  <div class="kpi"><div class="v">{cov(e['picks_signoff']['per_quantity']['k@40']['coverage'])} / {cov(e['picks_signoff']['per_quantity']['SRF']['coverage'])}</div><div class="l">25 个选点上 k@40 / SRF 回流前区间覆盖（本应 95%）</div></div>
</div>
{findings(s)}
<h3>{n}.1 N-17 的十个测试点：角落补点前 → 后</h3>
<div class="table-wrap"><table><thead><tr><th rowspan="2">结果列</th><th colspan="3">回流前（{e['rows_before']} 行）</th><th colspan="3">回流后（{e['rows_after']} 行）</th></tr>
<tr><th class="num">中位</th><th class="num">最大</th><th class="num">2σ 覆盖</th><th class="num">中位</th><th class="num">最大</th><th class="num">2σ 覆盖</th></tr></thead>
<tbody>{summary_rows(s)}</tbody></table></div>
<figure><img src="data:image/png;base64,{img}" alt="{s} 十个测试点回流前后的相对误差"><figcaption>每个测试点回流前（灰）与回流后（绿）的相对误差；SRF 在扫频上限之上的点无值。</figcaption></figure>
<div class="table-wrap"><table><thead><tr><th class="num">点</th><th class="num">OD_P / OD_S / W_P / W_S / CS（µm）</th><th class="num">实测 SRF (GHz)</th><th class="num">Lp@40</th><th class="num">Ls@40</th><th class="num">k@40</th><th class="num">SRF</th></tr></thead>
<tbody>{point_rows(s)}</tbody></table></div>
<h3>{n}.2 二十五个角落选点：回流前的预测 vs 实测</h3>
<div class="table-wrap"><table><thead><tr><th>结果列</th><th class="num">有实测的点</th><th class="num">中位误差</th><th class="num">最大误差</th><th class="num">2σ 覆盖</th></tr></thead>
<tbody>{pick_rows(s)}</tbody></table></div>
<h3>{n}.3 选点时模型自己估计的不确定度（池内相对 σ，选点前 → 假定选点已测后）</h3>
<div class="table-wrap"><table><thead><tr><th>结果列</th><th class="num">中位 前</th><th class="num">p90 前</th><th class="num">中位 后</th><th class="num">p90 后</th></tr></thead>
<tbody>{sigma_rows(s)}</tbody></table></div>
<p class="note">选点的几何分布：{pick_geometry(s)}。池：{picks[s]['pool']['size']} 个 Sobol 点，域内 {picks[s]['pool']['in_domain']}，外径比窗内 {picks[s]['pool'].get('in_ratio', '–')}。</p>
"""


sections = "".join(stratum_section(s, i + 1) for i, s in enumerate(STRATA))
page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>单圈变压器角落补点复核</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=JetBrains+Mono:wght@400;600&display=swap">
<style>
:root {{ --ground:#f6f5f0; --surface:#ffffff; --surface-2:#eef0ea; --ink:#1f2320; --muted:#5f665f; --line:#d9dcd3; --accent:#2f7d5f; --warn:#b8611f; --code-bg:#eef0ea; --sans:"Noto Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif; --mono:"JetBrains Mono",ui-monospace,Menlo,monospace; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --ground:#141512; --surface:#1d1e1a; --surface-2:#262822; --ink:#e7e8e0; --muted:#a2a497; --line:#383a32; --accent:#7cc0a0; --warn:#e3a066; --code-bg:#24261f; }} }}
:root[data-theme="dark"] {{ --ground:#141512; --surface:#1d1e1a; --surface-2:#262822; --ink:#e7e8e0; --muted:#a2a497; --line:#383a32; --accent:#7cc0a0; --warn:#e3a066; --code-bg:#24261f; }}
body {{ background:var(--ground); color:var(--ink); font-family:var(--sans); font-size:15px; line-height:1.7; margin:0; padding-block:0 64px; padding-inline:16px; }}
.wrap {{ max-width:1080px; margin:0 auto; }}
header {{ padding-block:36px 18px; border-bottom:2px solid var(--accent); }}
.eyebrow {{ font-family:var(--mono); font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }}
h1 {{ font-size:31px; margin:8px 0 10px; text-wrap:balance; }}
h2 {{ font-size:21px; margin:38px 0 10px; }} h3 {{ font-size:16px; margin:24px 0 8px; }}
p, li {{ max-width:84ch; }} p {{ margin:0 0 12px; }}
code {{ font-family:var(--mono); font-size:.86em; background:var(--code-bg); padding:1px 5px; border-radius:3px; }}
.kpis {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:16px 0; }}
.kpi {{ background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:11px 14px; }}
.kpi .v {{ font-family:var(--mono); font-size:19px; font-variant-numeric:tabular-nums; }}
.kpi .l {{ font-size:12.5px; color:var(--muted); }}
.table-wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:6px; background:var(--surface); margin:0 0 14px; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; }}
th, td {{ text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ background:var(--surface-2); font-weight:500; }}
td.num, th.num {{ text-align:right; font-family:var(--mono); font-variant-numeric:tabular-nums; white-space:nowrap; }}
figure {{ margin:0 0 14px; background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:8px; }}
figure img {{ width:100%; max-width:100%; height:auto; display:block; }}
figcaption {{ font-size:12.5px; color:var(--muted); margin-top:6px; }}
.finding {{ border:1px solid var(--line); border-left:4px solid var(--warn); background:var(--surface); border-radius:6px; padding:10px 14px; margin:0 0 10px; }}
.finding b {{ color:var(--warn); }} .ok {{ border-left-color:var(--accent); }} .ok b {{ color:var(--accent); }}
.note {{ font-size:13px; color:var(--muted); }}
@media (max-width:760px) {{ .kpis {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="eyebrow">ic-opt-modular · 查询库 · xfm_bs_ap + xfm_bs_m10 · lib.densify → lib_signoff → adopt · 2026-09-25</div>
  <h1>单圈变压器角落补点复核（N-19 方案 3）</h1>
  <p class="note">问题（BACKLOG N-19，方案 3）：B-12 与 N-17 两轮补点后，"次级比初级大 10–25%、次级线宽 8–10 µm、中心偏移 15–20 µm"这个角落仍靠外推，N-17 的第 3、6、7 号测试点（ap）与第 3 号（m10）在那里 Lp@40、k@40、Q@40 都不准。这一轮把 <code>lib.densify</code> 的池限定在该角落（OD_S/OD_P 1.10–1.25、W_S 8–10 µm、CS 14–22 µm、OD_P 140–200 µm；score=ceiling，六个结果列含 Qp@40 / Qs@40），每张表选 25 点，全部过真实 EMX 并回流（ap 7 路、m10 5 路 × 8 线程 × 32 GB）。前后对比用的仍是 N-17 那 10 个从未回流的测试点："补点前"即 N-17 页里的"回流后"。选点脚本 <code>&lt;ic-opt-library&gt;/n28_signoff/make_corner.py</code>。</p>
</header>
{sections}
<h2>3 方法与数据</h2>
<ul>
  <li>误差 = 预测 / 实测 − 1（有符号）；补点前取自 N-17 评估里回流后的 <code>lib.query</code>，补点后取自角落回流并重拟后的 <code>lib.query</code>，k = 2。</li>
  <li>脚本：<code>make_corner.py</code>（选点）、<code>run_corner.sh</code>（真实 EMX 与回流）、<code>corner_evaluate.py</code>（前后对比），均在 <code>&lt;ic-opt-library&gt;/n28_signoff/</code>；本页由 <code>corner_densify_report.py</code> 生成，数据副本 <code>corner_*_xfm_bs_{{ap,m10}}.json</code> 在本目录。</li>
</ul>
</div>
</body>
</html>
"""
out.write_text(page, encoding="utf-8")
print(out, f"{out.stat().st_size / 1e3:.0f} kB")
