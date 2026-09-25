"""B-12 review page: sixty lib.densify picks through real EMX and adopted, ten independent random designs as the test set.

Usage: b12_densify_report.py OUT_HTML   (reads b12_*.json next to this script; the figure goes to figs/)
"""
from __future__ import annotations

import base64
import html
import io
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
ev = json.loads((HERE / "b12_evaluation_xfm_bs_ap.json").read_text(encoding="utf-8"))
picks = json.loads((HERE / "b12_densify60_candidates_xfm_bs_ap.json").read_text(encoding="utf-8"))
out = Path(sys.argv[1])
DIMS = [("primary_outer_diameter_um", "OD_P"), ("secondary_outer_diameter_um", "OD_S"), ("primary_width_um", "W_P"),
        ("secondary_width_um", "W_S"), ("center_spacing_um", "CS")]
MAIN = ["Lp@40", "Ls@40", "k@40", "SRF", "Qp@40", "Qs@40", "Lp_lf", "Ls_lf", "k_lf"]
plt.rcParams["font.family"] = ["Noto Sans CJK JP", "DejaVu Sans"]


def pct(x, signed=False):
    if x is None:
        return "–"
    return f"{x * 100:+.1f}%" if signed else f"{abs(x) * 100:.1f}%"


def figure() -> str:
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2))
    pts = ev["test_points"]
    xs = list(range(1, len(pts) + 1))
    for ax, q in zip(axes, ["Lp@40", "SRF", "k@40"]):
        b = [abs(p["quantities"][q]["before"]["rel"] or 0) * 100 for p in pts]
        a = [abs(p["quantities"][q]["after"]["rel"] or 0) * 100 for p in pts]
        ax.bar([x - 0.2 for x in xs], b, width=0.4, color="#9a9c90", label="回流前（1581 行）")
        ax.bar([x + 0.2 for x in xs], a, width=0.4, color="#2f6b4f", label="回流后（1641 行）")
        ax.set_title(f"{q}：10 个独立测试点的相对误差")
        ax.set_xlabel("测试点"); ax.set_ylabel("|预测/实测 − 1| (%)"); ax.set_xticks(xs)
        ax.grid(axis="y", alpha=0.3)
    axes[0].legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    figs = HERE / "figs"; figs.mkdir(exist_ok=True)
    fig.savefig(figs / f"{out.stem}_test_points.png", dpi=130)
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=110); plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def cov(x) -> str:
    return "–" if x is None else f"{x:.0%}"


def summary_rows() -> str:
    rows = []
    for q in MAIN:
        b, a = ev["summary"][q]["before"], ev["summary"][q]["after"]
        rows.append(f"<tr><td>{html.escape(q)}</td><td class='num'>{pct(b['median_rel'])}</td><td class='num'>{pct(b['max_rel'])}</td>"
                    f"<td class='num'>{cov(b['coverage'])}</td>"
                    f"<td class='num'>{pct(a['median_rel'])}</td><td class='num'>{pct(a['max_rel'])}</td>"
                    f"<td class='num'>{cov(a['coverage'])}</td></tr>")
    return "".join(rows)


def point_rows() -> str:
    rows = []
    for i, p in enumerate(ev["test_points"], 1):
        geo = " / ".join(f"{p['params'][d]:g}" for d, _ in DIMS)
        cells = "".join(f"<td class='num'>{pct(p['quantities'][q]['before']['rel'], True)} → {pct(p['quantities'][q]['after']['rel'], True)}</td>"
                        for q in ["Lp@40", "Ls@40", "k@40", "SRF"])
        srf = p["quantities"]["SRF"]["measured"]
        srf_text = "–" if srf is None else f"{srf / 1e9:.0f}"
        rows.append(f"<tr><td class='num'>{i}</td><td class='num'>{geo}</td><td class='num'>{srf_text}</td>{cells}</tr>")
    return "".join(rows)


def pick_rows() -> str:
    rows = []
    for q in ["Lp@40", "Ls@40", "k@40", "SRF"]:
        v = ev["picks_signoff"]["per_quantity"][q]
        rows.append(f"<tr><td>{q}</td><td class='num'>{v['n']}</td><td class='num'>{pct(v['median_rel'])}</td><td class='num'>{pct(v['max_rel'])}</td>"
                    f"<td class='num'>{cov(v['coverage'])}</td></tr>")
    return "".join(rows)


def pick_geometry() -> str:
    import collections
    odp = collections.Counter(int(c["params"]["primary_outer_diameter_um"] // 30 * 30) for c in picks["candidates"])
    cs = collections.Counter(int(c["params"]["center_spacing_um"] // 4 * 4) for c in picks["candidates"])
    return (" · ".join(f"OD_P {k}–{k + 30} µm：{v}" for k, v in sorted(odp.items())) + "<br>"
            + " · ".join(f"CS {k}–{k + 4} µm：{v}" for k, v in sorted(cs.items())))


img = figure()
s = ev["summary"]
page = f"""<title>N28 单圈变压器补点回流复核</title>
<style>
:root {{ --ground:#f3f2ee; --surface:#fff; --surface-2:#e7e5de; --ink:#1b1c18; --muted:#5d5f55; --line:#d2d0c6; --accent:#2f6b4f; --warn:#9a4a12; --code-bg:#ebe9e2;
  --sans:"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif; --mono:"IBM Plex Mono",Menlo,Consolas,monospace; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --ground:#141512; --surface:#1d1e1a; --surface-2:#262822; --ink:#e7e8e0; --muted:#a2a497; --line:#383a32; --accent:#7cc0a0; --warn:#e3a066; --code-bg:#24261f; }} }}
:root[data-theme="dark"] {{ --ground:#141512; --surface:#1d1e1a; --surface-2:#262822; --ink:#e7e8e0; --muted:#a2a497; --line:#383a32; --accent:#7cc0a0; --warn:#e3a066; --code-bg:#24261f; }}
body {{ background:var(--ground); color:var(--ink); font-family:var(--sans); font-size:15px; line-height:1.7; margin:0; padding-block:0 64px; padding-inline:16px; }}
.wrap {{ max-width:1080px; margin:0 auto; }}
header {{ padding-block:36px 18px; border-bottom:2px solid var(--accent); }}
.eyebrow {{ font-family:var(--mono); font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }}
h1 {{ font-size:31px; margin:8px 0 10px; text-wrap:balance; }}
h2 {{ font-size:21px; margin:38px 0 10px; }}
p, li {{ max-width:84ch; }} p {{ margin:0 0 12px; }}
code {{ font-family:var(--mono); font-size:.86em; background:var(--code-bg); padding:1px 5px; border-radius:3px; }}
.kpis {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:16px 0; }}
.kpi {{ background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:11px 14px; }}
.kpi .v {{ font-family:var(--mono); font-size:21px; font-variant-numeric:tabular-nums; }}
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
<div class="wrap">
<header>
  <div class="eyebrow">ic-opt-modular · 查询库 · xfm_bs_ap · lib.densify → lib_signoff → adopt · 2026-09-25</div>
  <h1>N28 单圈变压器补点回流复核</h1>
  <p class="note">问题（BACKLOG B-12）：按模型不确定度补点，真的能把格点之间的预测变准吗？做法：<code>lib.densify</code> 在同心到轻偏心子域
  （中心偏移 0–16 µm，score=ceiling）选 60 个点；另外在同一子域随机取 10 个与不确定度无关的点作为独立测试集；70 个点都过真实 EMX
  （7 路 × 8 线程 × 32 GB，共 21 分钟）。只回流 60 个选点（xfm_bs_ap 1581→1641 行），用 10 个测试点比较回流前后的预测。回流前的模型已含
  T16.2b 的谐振分解与无量纲坐标。</p>
</header>

<div class="kpis">
  <div class="kpi"><div class="v">70/70</div><div class="l">真实 EMX 全部成功（60 选点 + 10 测试点）</div></div>
  <div class="kpi"><div class="v">{pct(s['Lp@40']['before']['median_rel'])} → {pct(s['Lp@40']['after']['median_rel'])}</div><div class="l">Lp@40 测试点中位误差，回流前 → 后</div></div>
  <div class="kpi"><div class="v">{pct(s['k@40']['before']['max_rel'])} → {pct(s['k@40']['after']['max_rel'])}</div><div class="l">k@40 测试点最大误差，回流前 → 后</div></div>
  <div class="kpi"><div class="v">{cov(ev['picks_signoff']['per_quantity']['SRF']['coverage'])}</div><div class="l">60 个选点上 SRF 回流前区间覆盖（本应 95%）</div></div>
</div>

<h2>结论</h2>
<div class="finding ok"><b>补点有效，中位误差全线下降。</b>10 个独立测试点上，9 个结果列的中位误差回流后都下降或持平（Lp@40 {pct(s['Lp@40']['before']['median_rel'])}→{pct(s['Lp@40']['after']['median_rel'])}，k@40 {pct(s['k@40']['before']['median_rel'])}→{pct(s['k@40']['after']['median_rel'])}，Qp@40 {pct(s['Qp@40']['before']['median_rel'])}→{pct(s['Qp@40']['after']['median_rel'])}）；k@40、Qp@40、Qs@40、Lp_lf、Ls_lf 的最大误差也明显下降（k@40 {pct(s['k@40']['before']['max_rel'])}→{pct(s['k@40']['after']['max_rel'])}，Qp@40 {pct(s['Qp@40']['before']['max_rel'])}→{pct(s['Qp@40']['after']['max_rel'])}）。</div>
<div class="finding"><b>选点处的模型不只是"没把握"，而是有偏：区间覆盖只有 {cov(ev['picks_signoff']['per_quantity']['k@40']['coverage'])}（k@40）和 {cov(ev['picks_signoff']['per_quantity']['SRF']['coverage'])}（SRF）。</b>60 个选点的实测与回流前预测比较：SRF 中位偏 {pct(ev['picks_signoff']['per_quantity']['SRF']['median_rel'])}、最大 {pct(ev['picks_signoff']['per_quantity']['SRF']['max_rel'])}，校准区间大多没盖住。这些点集中在"偏心 + 两外径不等"这片此前从未采样的区域（T16.2a 研究指出的空白），模型在那里的误差是系统性的，留出校准（在已采样区域上做）估不出来。这正是补点该去的地方，也是为什么补点前的区间在那里不可信。</div>
<div class="finding"><b>一个测试点变差：第 6 点（OD_P 141、OD_S 128、CS 15.5 µm，偏移最大）。</b>Lp@40 从 +0.5% 变为 +11.7%，SRF 从 +2.6% 变为 −10.9%，区间随之变宽（仍覆盖实测）。它位于子域边缘、外径比 0.9、偏移接近上限，回流的 60 点中靠近它的只有几个，SRF 模型在那里重新拟合后更陡。第 9 点的 SRF 也从 −0.8% 变为 +5.4%，但其 k@40 从 −10.4% 变为 −2.7%。因此最大误差在 Lp@40 / Ls@40 / SRF 三列上没有下降，需要在偏移更大的一带再补一轮。</div>

<h2>1 十个独立测试点：回流前 → 后</h2>
<div class="table-wrap"><table><thead><tr><th rowspan="2">结果列</th><th colspan="3">回流前（1581 行）</th><th colspan="3">回流后（1641 行）</th></tr>
<tr><th class="num">中位</th><th class="num">最大</th><th class="num">2σ 覆盖</th><th class="num">中位</th><th class="num">最大</th><th class="num">2σ 覆盖</th></tr></thead>
<tbody>{summary_rows()}</tbody></table></div>
<figure><img src="data:image/png;base64,{img}" alt="十个测试点回流前后的相对误差"><figcaption>每个测试点回流前（灰）与回流后（绿）的相对误差；第 10 点的 SRF 在扫频上限之上，无值。</figcaption></figure>
<div class="table-wrap"><table><thead><tr><th class="num">点</th><th class="num">OD_P / OD_S / W_P / W_S / CS（µm）</th><th class="num">实测 SRF (GHz)</th><th class="num">Lp@40</th><th class="num">Ls@40</th><th class="num">k@40</th><th class="num">SRF</th></tr></thead>
<tbody>{point_rows()}</tbody></table></div>
<p class="note">误差 = 预测 / 实测 − 1（有符号）；回流前取自 lib_signoff 对测试点运行时的预测，回流后取自回流并重拟后的 <code>lib.query</code>，k = 2。</p>

<h2>2 六十个选点：回流前的预测 vs 实测</h2>
<div class="table-wrap"><table><thead><tr><th>结果列</th><th class="num">有实测的点</th><th class="num">中位误差</th><th class="num">最大误差</th><th class="num">2σ 覆盖</th></tr></thead>
<tbody>{pick_rows()}</tbody></table></div>
<p class="note">选点的几何分布：{pick_geometry()}。全部 60 点 EMX 成功，全部回流（obs_1582–obs_1641，<code>origin=signoff:…</code>）。</p>

<h2>3 下一步</h2>
<ul>
  <li>再补一轮：把 <code>bounds</code> 放到中心偏移 8–24 µm、外径比 0.8–1.25 一带（第 6、9 点所在），同样 60 点左右；回流后重跑本页脚本。</li>
  <li>校准的盲区：留出校准只覆盖已采样区域；在未采样区域，域守卫应比现在更保守——候选是把"离最近实测行的缩放距离"作为不确定度的附加项（T16 后续）。</li>
  <li>xfm_bs_m10 表同样处理；多圈表先按研究建议改 <code>ratio</code> 模型再补点。</li>
</ul>
</div>
"""
out.write_text(page, encoding="utf-8")
print(out, f"{out.stat().st_size / 1e3:.0f} kB")
