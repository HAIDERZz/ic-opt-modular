"""XFM_BS_Q_RATIO_ACCEPTANCE_CN.html: the N-19 option-2 acceptance (xfm_bs_q_ratio_acceptance.json, four variants on the
library after the corner round) as a review page.

Usage: xfm_bs_q_ratio_acceptance_page.py OUT_HTML   (figure to figs/)
"""
from __future__ import annotations

import base64
import html
import io
import json
import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
out = Path(sys.argv[1])
rep = json.loads((HERE / "xfm_bs_q_ratio_acceptance.json").read_text(encoding="utf-8"))
TABLES = list(rep["tables"])
COLS = ["Qp@40", "Qs@40", "Qp@60", "Qs@60"]
DIMS = ["OD_P", "OD_S"]
VARIANTS = [("direct", "现状：直接建模"), ("ratio", "Q 峰值 × 比值（采用）"), ("ratio_mapped", "比值 + 无量纲坐标"), ("mapped", "只换坐标（方案 1）")]
SOURCE = {"n17": "N-17 偏移带（8–24 µm）", "b12": "B-12 轻偏移带（0–16 µm）"}
plt.rcParams["font.family"] = ["Noto Sans CJK JP", "DejaVu Sans"]


def pct(x):
    return "–" if x is None else f"{x * 100:.1f}%"


def figure() -> str:
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.4), sharey=True)
    for ax, t in zip(axes, TABLES):
        labels, vals = [], {v: [] for v, _ in VARIANTS}
        for c in COLS:
            for d in DIMS:
                labels.append(f"{c}\n留出 {d} 档")
                for v, _ in VARIANTS:
                    vals[v].append(rep["tables"][t]["protocol_ii"][c][d][v]["pooled"]["p90_rel"] * 100)
        xs = list(range(len(labels)))
        for i, ((v, name), color) in enumerate(zip(VARIANTS, ["#9a9c90", "#2f7d5f", "#5b6fc9", "#c98a3d"])):
            ax.bar([x + (i - 1.5) * 0.2 for x in xs], vals[v], width=0.2, color=color, label=name)
        ax.set_xticks(xs)
        ax.set_xticklabels(labels, fontsize=8)
        ax.set_title(f"{t}：整档留出的 p90 相对误差")
        ax.grid(axis="y", alpha=0.3)
    axes[0].set_ylabel("p90 |预测/实测 − 1| (%)")
    axes[0].legend(fontsize=9)
    fig.tight_layout()
    (HERE / "figs").mkdir(exist_ok=True)
    fig.savefig(HERE / "figs" / f"{out.stem}_p90.png", dpi=130)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def holdout_rows(t: str) -> str:
    rows = []
    for c in COLS:
        for d in DIMS:
            e = rep["tables"][t]["protocol_ii"][c][d]
            cells = "".join(f"<td class='num'>{pct(e[v]['pooled']['median_rel'])} / <b>{pct(e[v]['pooled']['p90_rel'])}</b> / {pct(e[v]['pooled']['max_rel'])}</td>" for v, _ in VARIANTS)
            rows.append(f"<tr><td>{html.escape(c)}</td><td>{d}</td><td class='num'>{e['direct']['pooled']['n']}</td>{cells}</tr>")
    return "".join(rows)


def test_rows(t: str) -> str:
    rows = []
    for c in COLS:
        e = rep["tables"][t]["protocol_iii_tests"][c]
        if not e:
            continue
        sources = sorted({p["source"] for p in e["direct"]["points"]})
        for src in sources:
            cells = []
            for v, _ in VARIANTS:
                sel = [p for p in e[v]["points"] if p["source"] == src]
                rel = [abs(p["rel"]) for p in sel]
                cov = sum(p["inside"] for p in sel) / len(sel)
                cells.append(f"<td class='num'>{statistics.median(rel) * 100:.1f}% / {max(rel) * 100:.1f}% / <b>{cov:.0%}</b></td>")
            rows.append(f"<tr><td>{html.escape(c)}</td><td>{SOURCE.get(src, src)}</td><td class='num'>{len(sel)}</td>{''.join(cells)}</tr>")
    return "".join(rows)


def point_rows(t: str) -> str:
    rows = []
    e = rep["tables"][t]["protocol_iii_tests"]["Qp@40"]
    d = {p["obs_id"]: p for p in e["direct"]["points"] if p["source"] == "n17"}
    m = {p["obs_id"]: p for p in e["ratio"]["points"] if p["source"] == "n17"}
    es = rep["tables"][t]["protocol_iii_tests"]["Qs@40"]
    ds = {p["obs_id"]: p for p in es["direct"]["points"] if p["source"] == "n17"}
    ms = {p["obs_id"]: p for p in es["ratio"]["points"] if p["source"] == "n17"}
    for i, k in enumerate(d, 1):
        rows.append(f"<tr><td class='num'>{i}</td><td class='num'>{d[k]['measured']:.1f}</td>"
                    f"<td class='num'>{d[k]['rel'] * 100:+.1f}% {'✓' if d[k]['inside'] else '✗'} → {m[k]['rel'] * 100:+.1f}% {'✓' if m[k]['inside'] else '✗'}</td>"
                    f"<td class='num'>{ds[k]['measured']:.1f}</td>"
                    f"<td class='num'>{ds[k]['rel'] * 100:+.1f}% {'✓' if ds[k]['inside'] else '✗'} → {ms[k]['rel'] * 100:+.1f}% {'✓' if ms[k]['inside'] else '✗'}</td></tr>")
    return "".join(rows)


def cal_rows(t: str) -> str:
    rows = []
    for v, name in VARIANTS:
        f = rep["tables"][t]["protocol_i_calibration"][v]
        for c in COLS:
            mm = f["calibration"][c]
            rows.append(f"<tr><td>{name}</td><td>{c}</td><td class='num'>{mm['k_scale']:.2f}</td><td class='num'>{pct(mm.get('median_rel'))}</td>"
                        f"<td class='num'>{mm.get('coverage_2sigma_before', float('nan')):.2f}</td><td class='num'>{f['seconds']['curve_models']:.0f} s</td></tr>")
    return "".join(rows)


img = figure()
sections = ""
for t in TABLES:
    sections += f"""
<h2>{t}</h2>
<h3>整档留出（每档 ≥ 25 个可用行，不校准，预测被拿掉的档）</h3>
<div class="table-wrap"><table><thead><tr><th>结果列</th><th>留出的档</th><th class="num">留出行数</th><th class="num">现状：直接建模<br>中位 / <b>p90</b> / 最大</th><th class="num">Q 峰值 × 比值（采用）</th><th class="num">比值 + 无量纲坐标</th><th class="num">只换坐标（方案 1）</th></tr></thead>
<tbody>{holdout_rows(t)}</tbody></table></div>
<h3>测过但从未回流的独立设计（完整库，校准后；中位 / 最大 / <b>2σ 覆盖</b>）</h3>
<div class="table-wrap"><table><thead><tr><th>结果列</th><th>测试集</th><th class="num">点数</th><th class="num">现状：直接建模</th><th class="num">Q 峰值 × 比值（采用）</th><th class="num">比值 + 无量纲坐标</th><th class="num">只换坐标（方案 1）</th></tr></thead>
<tbody>{test_rows(t)}</tbody></table></div>
<h3>N-17 偏移带的每个点：Q@40 误差与是否在区间内，现状 → Q 峰值 × 比值</h3>
<div class="table-wrap"><table><thead><tr><th class="num">点</th><th class="num">实测 Qp@40</th><th class="num">Qp@40</th><th class="num">实测 Qs@40</th><th class="num">Qs@40</th></tr></thead>
<tbody>{point_rows(t)}</tbody></table></div>
<h3>完整库校准</h3>
<div class="table-wrap"><table><thead><tr><th>建法</th><th>结果列</th><th class="num">k_scale</th><th class="num">典型误差</th><th class="num">放大前 2σ 覆盖</th><th class="num">四列拟合用时</th></tr></thead>
<tbody>{cal_rows(t)}</tbody></table></div>
"""
page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>单圈变压器 Q 列比值建模验收</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=JetBrains+Mono:wght@400;600&display=swap">
<style>
:root {{ --ground:#f6f5f0; --surface:#ffffff; --surface-2:#eef0ea; --ink:#1f2320; --muted:#5f665f; --line:#d9dcd3; --accent:#2f7d5f; --warn:#b8611f; --code-bg:#eef0ea; --sans:"Noto Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif; --mono:"JetBrains Mono",ui-monospace,Menlo,monospace; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --ground:#141512; --surface:#1d1e1a; --surface-2:#262822; --ink:#e7e8e0; --muted:#a2a497; --line:#383a32; --accent:#7cc0a0; --warn:#e3a066; --code-bg:#24261f; }} }}
:root[data-theme="dark"] {{ --ground:#141512; --surface:#1d1e1a; --surface-2:#262822; --ink:#e7e8e0; --muted:#a2a497; --line:#383a32; --accent:#7cc0a0; --warn:#e3a066; --code-bg:#24261f; }}
body {{ background:var(--ground); color:var(--ink); font-family:var(--sans); font-size:15px; line-height:1.7; margin:0; padding-block:0 64px; padding-inline:16px; }}
.wrap {{ max-width:1080px; margin:0 auto; }}
header {{ padding-block:36px 18px; border-bottom:2px solid var(--accent); }}
.eyebrow {{ font-family:var(--mono); font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }}
h1 {{ font-size:31px; margin:8px 0 10px; text-wrap:balance; }} h2 {{ font-size:21px; margin:38px 0 10px; }} h3 {{ font-size:16px; margin:22px 0 8px; }}
p, li {{ max-width:84ch; }} p {{ margin:0 0 12px; }}
code {{ font-family:var(--mono); font-size:.86em; background:var(--code-bg); padding:1px 5px; border-radius:3px; }}
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
.note {{ font-size:12.5px; color:var(--muted); }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="eyebrow">ic-opt-modular · 查询库 · xfm_bs_ap + xfm_bs_m10 · N-19 方案 2 · {html.escape(rep['date'])}</div>
  <h1>单圈变压器 Q 列改为 Q 峰值 × 比值的验收：采用</h1>
  <p class="note">问题（BACKLOG N-19，方案 2）：Qp@40 / Qs@40 在偏移带误差 10–30%，方案 1（只换坐标）在那里更自信但不更准，未采用。方案 2 把 Q 曲线列拆成 Q 峰值（Qp_peak / Qs_peak，表里已有的平滑量）× 单独建模的比值（<code>model: ratio</code>，代码提交 56d8f3e 让 Q 列也能用比值），与电感列的做法同构。验收在角落补点（方案 3，两表各回流 25 行）之后的库上做，四种建法并列：每张表 20 个外径档整档留出（160 折），测过但从未回流的独立设计（ap：N-17 与 B-12 各 10 个，m10：N-17 的 8 个），完整库校准（<code>xfm_bs_q_ratio_acceptance.py</code>，{rep['fold_workers']} 个进程 × {rep['omp_num_threads']} 线程，{rep['wall_seconds']:.0f} 秒）。</p>
</header>
<h2>结论</h2>
<div class="finding ok"><b>采用 Q 峰值 × 比值：在偏移带更准，而且区间诚实。</b>N-17 偏移带的点上：xfm_bs_ap 的 Qs@40 中位 4.7% → 3.4%、最大 23.0% → 13.7%、覆盖 80% → 100%，Qp@40 3.4% → 2.9%（覆盖 90% 不变）；xfm_bs_m10 的 Qp@40 5.2% → 4.1%、最大 33.3% → 14.9%、覆盖 88% → 100%，Qs@40 4.7% → 3.6%、最大 28.4% → 15.2%。B-12 轻偏移带的点与现状相同（1–2%）。完整库校准下 k_scale 恰为 1.00、放大前覆盖 0.96：比值模型自己报的 σ 已经够诚实。</div>
<div class="finding"><b>它不解决档间插值，那是坐标的事。</b>整档留出的 p90 误差比值建模只从 30–40% 降到 28–34%；只换坐标能降到 7–10%，但一如方案 1，在偏移带覆盖掉到 60–70%。"比值 + 坐标"介于两者之间（留出 16–27%，ap 偏移带覆盖 70–80%，m10 100%）。按"宁可保守不可错误肯定"取比值建模；坐标留待偏移带的行再多一些时连同再验。</div>
<div class="finding"><b>代价：拟合用时约三倍。</b>四个 Q 列的完整库拟合（含 5 折校准，每折重拟 Q 峰值部件）从约 500 秒增加到约 1750 秒（4 个工作进程 × 2 线程），每次回流后首次使用时发生一次，之后走缓存。</div>
<figure><img src="data:image/png;base64,{img}" alt="整档留出 p90 误差两种建法对比"><figcaption>两张表、四个 Q 列、留出 OD_P 或 OD_S 档：四种建法的 p90 相对误差（库含角落补点行）。只换坐标在这个指标上最好，但见结论。</figcaption></figure>
{sections}
<h2>改动</h2>
<p>真实 manifest（<code>&lt;ic-opt-library&gt;/n28/library.yaml</code>，备份 <code>library.yaml.bak_20260926_0135</code>）：xfm_bs 两表共用的量定义里 <code>Qp</code>、<code>Qs</code> 加 <code>model: ratio</code>（以 Qp_peak / Qs_peak 为底，不加坐标）。代码：提交 56d8f3e（manifest 校验允许 Q 列用比值；顺带修正组合模型留出校准把 ≤ 0 的值放进对数空间的问题）。临时副本在 <code>&lt;ic-opt-library&gt;/n28_scratch_n19r/</code>，数据 <code>xfm_bs_q_ratio_acceptance.json</code>，本页由 <code>xfm_bs_q_ratio_acceptance_page.py</code> 生成。</p>
</div>
</body>
</html>
"""
out.write_text(page, encoding="utf-8")
print(out, f"{out.stat().st_size / 1e3:.0f} kB")
