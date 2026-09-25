"""XFM_MS_RATIO_ACCEPTANCE_CN.html: the N-16 acceptance (xfm_ms_ratio_acceptance.json) as a review page.

Usage: xfm_ms_ratio_acceptance_page.py OUT_HTML   (figure to figs/)
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
out = Path(sys.argv[1])
rep = json.loads((HERE / "xfm_ms_ratio_acceptance.json").read_text(encoding="utf-8"))
TABLES = list(rep["tables"])
COLS = ["Lp@60", "Ls@60"]
DIMS = ["OD_P", "OD_S"]
VARIANTS = [("direct", "现状：直接建模"), ("mapped", "只换无量纲坐标"), ("ratio", "比值 + 无量纲坐标（采用）")]
STUDY = {"direct": "A", "mapped": "F", "ratio": "BF"}
plt.rcParams["font.family"] = ["Noto Sans CJK JP", "DejaVu Sans"]


def pct(x):
    return "–" if x is None else f"{x * 100:.1f}%"


def figure() -> str:
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2), sharey=True)
    for ax, t in zip(axes, TABLES):
        labels, vals = [], {v: [] for v, _ in VARIANTS}
        for c in COLS:
            for d in DIMS:
                labels.append(f"{c}\n留出 {d} 档")
                for v, _ in VARIANTS:
                    vals[v].append(rep["tables"][t]["protocol_ii"][c][d][v]["pooled"]["p90_rel"] * 100)
        xs = list(range(len(labels)))
        for i, ((v, name), color) in enumerate(zip(VARIANTS, ["#9a9c90", "#5b6fc9", "#2f7d5f"])):
            ax.bar([x + (i - 1) * 0.27 for x in xs], vals[v], width=0.27, color=color, label=name)
        ax.set_xticks(xs)
        ax.set_xticklabels(labels, fontsize=9)
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
            cells = []
            for v, _ in VARIANTS:
                p, s = e[v]["pooled"], e[v]["study"] or {}
                cells.append(f"<td class='num'>{pct(p['median_rel'])} / <b>{pct(p['p90_rel'])}</b> / {pct(p['max_rel'])}<br><span class='note'>研究 {STUDY[v]}：{pct(s.get('p90_rel'))}</span></td>")
            n = e["direct"]["pooled"]["n"]
            rows.append(f"<tr><td>{html.escape(c)}</td><td>{d}</td><td class='num'>{n}</td>{''.join(cells)}</tr>")
    return "".join(rows)


def cal_rows(t: str) -> str:
    rows = []
    for v in ("direct", "ratio"):
        f = rep["tables"][t]["protocol_i_calibration"].get(v)
        if not f:
            continue
        for c in COLS:
            m = f["calibration"][c]
            rows.append(f"<tr><td>{dict(VARIANTS)[v]}</td><td>{c}</td><td class='num'>{m['k_scale']:.2f}</td><td class='num'>{pct(m.get('median_rel'))}</td>"
                        f"<td class='num'>{m.get('coverage_2sigma_before', float('nan')):.2f}</td><td class='num'>{f['seconds']['curve_models']:.0f} s</td></tr>")
    return "".join(rows)


img = figure()
sections = ""
for t in TABLES:
    sections += f"""
<h2>{t}</h2>
<div class="table-wrap"><table><thead><tr><th>结果列</th><th>留出的档</th><th class="num">留出行数</th><th class="num">现状：直接建模<br>中位 / <b>p90</b> / 最大</th><th class="num">只换无量纲坐标</th><th class="num">比值 + 无量纲坐标（采用）</th></tr></thead>
<tbody>{holdout_rows(t)}</tbody></table></div>
<div class="table-wrap"><table><thead><tr><th>完整库校准</th><th>结果列</th><th class="num">k_scale</th><th class="num">典型误差</th><th class="num">放大前 2σ 覆盖</th><th class="num">两列拟合用时</th></tr></thead>
<tbody>{cal_rows(t)}</tbody></table></div>
"""
page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>多圈变压器比值建模验收</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=JetBrains+Mono:wght@400;600&display=swap">
<style>
:root {{ --ground:#f6f5f0; --surface:#ffffff; --surface-2:#eef0ea; --ink:#1f2320; --muted:#5f665f; --line:#d9dcd3; --accent:#2f7d5f; --warn:#b8611f; --code-bg:#eef0ea; --sans:"Noto Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif; --mono:"JetBrains Mono",ui-monospace,Menlo,monospace; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --ground:#141512; --surface:#1d1e1a; --surface-2:#262822; --ink:#e7e8e0; --muted:#a2a497; --line:#383a32; --accent:#7cc0a0; --warn:#e3a066; --code-bg:#24261f; }} }}
:root[data-theme="dark"] {{ --ground:#141512; --surface:#1d1e1a; --surface-2:#262822; --ink:#e7e8e0; --muted:#a2a497; --line:#383a32; --accent:#7cc0a0; --warn:#e3a066; --code-bg:#24261f; }}
body {{ background:var(--ground); color:var(--ink); font-family:var(--sans); font-size:15px; line-height:1.7; margin:0; padding-block:0 64px; padding-inline:16px; }}
.wrap {{ max-width:1080px; margin:0 auto; }}
header {{ padding-block:36px 18px; border-bottom:2px solid var(--accent); }}
.eyebrow {{ font-family:var(--mono); font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }}
h1 {{ font-size:31px; margin:8px 0 10px; text-wrap:balance; }} h2 {{ font-size:21px; margin:38px 0 10px; }}
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
.note {{ font-size:12px; color:var(--muted); }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="eyebrow">ic-opt-modular · 查询库 · xfm_ms_ap + xfm_ms_m10 · N-16 · {html.escape(rep['date'])}</div>
  <h1>多圈变压器电感列改为比值建模的验收</h1>
  <p class="note">问题（BACKLOG N-16）：T16.2a 研究建议多圈表的 Lp / Ls 用 <code>model: ratio</code> + <code>feature_map: xfm_ms_dimensionless</code>（研究里的 BF 法）。
  改真实 manifest 前，用库自己的实现（<code>library/composed.py</code>）在库的临时副本上复核：把研究留出过的每一个 OD_P、OD_S 档（≥ 25 个可用行）整档拿掉，
  用剩下的行拟合（不校准），预测被拿掉的 Lp@60 / Ls@60 行；三种建法并列。另外对完整库做库的正式校准（5 × 20% 留出，组合模型每折重拟所有部件）。
  脚本 <code>xfm_ms_ratio_acceptance.py</code>，数据 <code>xfm_ms_ratio_acceptance.json</code>，{rep['fold_workers']} 个进程 × {rep['omp_num_threads']} 线程，共 {rep['wall_seconds']:.0f} 秒。</p>
</header>
<h2>结论</h2>
<div class="finding ok"><b>研究结论在库自己的实现上成立，已改真实 manifest。</b>整档留出的 p90 误差：xfm_ms_ap 的 Ls@60 从 41.2%（留出 OD_P 档）/ 20.9%（留出 OD_S 档）降到 8.5% / 6.6%，Lp@60 从 23.2% / 10.0% 降到 8.2% / 5.1%；xfm_ms_m10 的 Ls@60 从 27.4% / 21.2% 降到 13.5% / 7.4%，Lp@60 从 17.0% / 9.3% 降到 8.9% / 6.5%。完整库校准下，比值建模的典型误差更小（0.14–0.36%），放大前的 2σ 覆盖已达 0.94–0.95，k_scale 接近 1。</div>
<div class="finding"><b>收益主要来自换坐标，比值本身只再改善一点。</b>"只换无量纲坐标"已经拿到绝大部分收益（例如 ap 的 Ls@60 留出 OD_P 档 8.5% 对 8.5%）；比值建模在 m10 的 Ls@60 上再降 2.2 个百分点（15.7% → 13.5%、8.2% → 7.4%），在其它列持平或差 0.1–0.2 个百分点。按研究的书面建议采用比值 + 坐标；它还把答案拆成低频值 × 比值两部分可解释，代价是每张表的两列曲线模型拟合用时约翻倍（一次性，进缓存）。</div>
<div class="finding"><b>库的数字与研究的数字不完全相同。</b>库的直接建模按圈数分层拟合（研究的 A 法不分层），所以现状的误差比研究报得更差（ap Ls@60 留出 OD_P 档 41.2% 对 27.9%）；改后的数字比研究高 1–4 个百分点（留出的行集与训练集略有不同）。方向与量级一致，足以支持这项改动。</div>
<figure><img src="data:image/png;base64,{img}" alt="整档留出 p90 误差三种建法对比"><figcaption>两张表、两列、留出 OD_P 或 OD_S 档：三种建法的 p90 相对误差。</figcaption></figure>
{sections}
<h2>改动与验收</h2>
<ul>
  <li>真实 manifest（<code>&lt;ic-opt-library&gt;/n28/library.yaml</code>，备份 <code>library.yaml.bak_20260925_1815</code>）：xfm_ms 两表共用的量定义里 <code>Lp</code>、<code>Ls</code> 加 <code>model: ratio, feature_map: xfm_ms_dimensionless</code>；Qp / Qs / k 不变，单圈表与电感表不变。</li>
  <li>改后重跑 <code>xfm_query_verify.py</code> 两张多圈表，页面 <code>XFM_QUERY_VERIFY_CN.html</code> 相应更新。</li>
</ul>
</div>
</body>
</html>
"""
out.write_text(page, encoding="utf-8")
print(out, f"{out.stat().st_size / 1e3:.0f} kB")
