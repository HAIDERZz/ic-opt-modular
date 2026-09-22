"""The finished inductor library as a review page: coverage of L / Q / SRF over the grid, agreement with the estimate, cost.

Usage: ind_library_report.py LIBRARY_ROOT OUT_HTML
"""
from __future__ import annotations

import base64
import html
import io
import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).parent))
from ind_library_check import rows

root, out = Path(sys.argv[1]), Path(sys.argv[2])
rs = rows(root)
ok = [r for r in rs if r["status"] == "ok"]
BODY = {"AP": "AP body (crossunder M10)", "10": "M10 body (crossunder M9)"}
COLORS = {1: "#6f4e37", 2: "#b5651d", 3: "#2e6f95", 4: "#4f8f55", 5: "#8a3d7f"}


def png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def scatter(ykey: str, ylabel: str, logy: bool) -> str:
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.9), sharey=True)
    for ax, metal in zip(axes, ("AP", "10")):
        for nt in range(1, 6):
            v = [r for r in ok if r["metal"] == metal and r["turns"] == nt and r[ykey]]
            ax.scatter([r["od"] for r in v], [r[ykey] for r in v], s=[6 + 3 * r["w"] for r in v], color=COLORS[nt], alpha=0.65, label=f"NT={nt}", edgecolors="none")
        ax.set_title(BODY[metal], fontsize=10)
        ax.set_xlabel("OD (um)   marker size ~ width")
        ax.grid(alpha=0.3)
        if logy:
            ax.set_yscale("log")
    axes[0].set_ylabel(ylabel)
    axes[1].legend(fontsize=8, loc="best")
    fig.tight_layout()
    return png(fig)


def est_plot() -> str:
    fig, ax = plt.subplots(figsize=(5.2, 4.2))
    v = [r for r in ok if r["L_nH"] and r["L_est_nH"]]
    ax.scatter([r["L_est_nH"] for r in v], [r["L_nH"] for r in v], s=8, alpha=0.5, color="#2e6f95", edgecolors="none")
    lim = (0.05, 12)
    ax.plot(lim, lim, color="#999", lw=1)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(lim)
    ax.set_ylim(lim)
    ax.set_xlabel("estimated L (nH)")
    ax.set_ylabel("EMX L_lf (nH)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    return png(fig)


def nt1_ratio_plot() -> str:
    fig, ax = plt.subplots(figsize=(5.6, 3.6))
    for metal, marker in (("AP", "o"), ("10", "s")):
        for od, color in ((60.0, "#b5651d"), (80.0, "#8a7a3d"), (120.0, "#2e6f95"), (240.0, "#4f8f55")):
            v = sorted((r["w"], r["L_nH"] / r["L_est_nH"]) for r in ok if r["metal"] == metal and r["turns"] == 1 and r["od"] == od and r["L_nH"] and r["L_est_nH"])
            if v:
                ax.plot([w for w, _ in v], [x for _, x in v], marker=marker, ms=4, color=color, lw=1, alpha=0.85, label=f"{'AP' if metal == 'AP' else 'M10'} OD={od:g}")
    ax.axhline(1.15, color="#b33", lw=0.8, ls="--")
    ax.axhline(0.85, color="#b33", lw=0.8, ls="--")
    ax.set_xlabel("width W (um), NT=1")
    ax.set_ylabel("EMX L / estimated L")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    return png(fig)


def cost_plot() -> str:
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.4))
    for nt in range(1, 6):
        v = [r for r in ok if r["turns"] == nt and r["wall_s"]]
        a1.scatter([r["od"] for r in v], [r["wall_s"] for r in v], s=8, color=COLORS[nt], alpha=0.6, label=f"NT={nt}", edgecolors="none")
        a2.scatter([r["od"] for r in v], [r["peak_gb"] for r in v], s=8, color=COLORS[nt], alpha=0.6, edgecolors="none")
    a1.set_ylabel("wall clock per point (s), 8 concurrent")
    a2.set_ylabel("peak memory (GB)")
    for a in (a1, a2):
        a.set_xlabel("OD (um)")
        a.grid(alpha=0.3)
    a1.legend(fontsize=8)
    fig.tight_layout()
    return png(fig)



errs = [abs(r["L_nH"] / r["L_est_nH"] - 1) * 100 for r in ok if r["L_nH"] and r["L_est_nH"]]
errs.sort()
walls = [r["wall_s"] for r in ok if r["wall_s"]]
proj_rows = []
for project in ("ind_sym_ap", "ind_sym_ap_nt1", "ind_sym_m10", "ind_sym_m10_nt1"):
    v = [r for r in rs if r["project"] == project]
    if not v:
        continue
    g = [r for r in v if r["status"] == "ok"]
    ls = [r["L_nH"] for r in g if r["L_nH"]]
    qs = [r["Q_peak"] for r in g if r["Q_peak"]]
    srf = [r["SRF_GHz"] for r in g if r["SRF_GHz"]]
    band = "0–250 GHz" if project.endswith("nt1") else "0–150 GHz"
    proj_rows.append(f"<tr><td><code>{project}</code></td><td>{band}</td><td class='num'>{len(v)}</td><td class='num'>{len(g)}</td>"
                     f"<td class='num'>{min(ls):.3f}–{max(ls):.2f}</td><td class='num'>{min(qs):.1f}–{max(qs):.1f}</td>"
                     f"<td class='num'>{len(srf)}/{len(g)}</td><td class='num'>{(min(srf) if srf else 0):.1f}–{(max(srf) if srf else 0):.1f}</td></tr>")
off = [r for r in ok if r["L_nH"] and r["L_est_nH"] and abs(r["L_nH"] / r["L_est_nH"] - 1) > 0.15]
off_html = "".join(f"<tr><td><code>{r['project']} {r['obs']}</code></td><td class='num'>{r['turns']}</td><td class='num'>{r['od']:g}</td><td class='num'>{r['w']:g}</td><td class='num'>{r['s']:g}</td>"
                   f"<td class='num'>{r['L_nH']:.3f}</td><td class='num'>{r['L_est_nH']:.3f}</td><td class='num'>{100 * (r['L_nH'] / r['L_est_nH'] - 1):+.1f}%</td></tr>" for r in off)
renders = [("data:image/png;base64," + base64.b64encode((Path(__file__).parent / f).read_bytes()).decode(), c)
           for f, c in (("ind_nt1_od60_w10.png", "AP NT=1 OD=60 W=10 S=2"), ("ind_m10_nt1_od60_w10.png", "M10 NT=1 OD=60 W=10 S=2"))
           if (Path(__file__).parent / f).exists()]
bad = [r for r in rs if r["status"] != "ok"]
bad_html = "".join(f"<li><code>{html.escape(r['project'])} {r['obs']}</code> NT={r['turns']} OD={r['od']:g} W={r['w']:g} S={r['s']:g}: {html.escape('; '.join(r['issues'][:2]))}</li>" for r in bad) or "<li>无</li>"
log = (root / "run.log").read_text() if (root / "run.log").exists() else ""

page = f"""<title>N28 电感库</title>
<style>
:root {{ --ground:#f3f1ec; --surface:#fff; --surface-2:#e8e4dc; --ink:#1d1b16; --muted:#5f5a50; --line:#d3cec3; --accent:#8a5a2b; --good:#3c6e47; --code-bg:#ece8e0;
  --sans:"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif; --mono:"IBM Plex Mono",Menlo,Consolas,monospace; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --ground:#16140f; --surface:#1f1d17; --surface-2:#282520; --ink:#eae5db; --muted:#a59d8e; --line:#39352c; --accent:#d9a066; --good:#7fb489; --code-bg:#252119; }} }}
:root[data-theme="dark"] {{ --ground:#16140f; --surface:#1f1d17; --surface-2:#282520; --ink:#eae5db; --muted:#a59d8e; --line:#39352c; --accent:#d9a066; --good:#7fb489; --code-bg:#252119; }}
body {{ background:var(--ground); color:var(--ink); font-family:var(--sans); font-size:15px; line-height:1.7; margin:0; padding-block:0 64px; padding-inline:16px; }}
.wrap {{ max-width:1040px; margin:0 auto; }}
header {{ padding-block:36px 18px; border-bottom:2px solid var(--accent); }}
.eyebrow {{ font-family:var(--mono); font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }}
h1 {{ font-size:32px; margin:8px 0 10px; }}
h2 {{ font-size:21px; margin:38px 0 10px; }}
p {{ max-width:80ch; margin:0 0 12px; }}
code {{ font-family:var(--mono); font-size:.87em; background:var(--code-bg); padding:1px 5px; border-radius:3px; }}
.kpis {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:16px 0; }}
.kpi {{ background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:11px 14px; }}
.kpi .v {{ font-family:var(--mono); font-size:22px; font-variant-numeric:tabular-nums; }}
.kpi .l {{ font-size:12.5px; color:var(--muted); }}
.table-wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:6px; background:var(--surface); margin:0 0 14px; }}
table {{ border-collapse:collapse; width:100%; font-size:13.5px; }}
th, td {{ text-align:left; padding:7px 12px; border-bottom:1px solid var(--line); }}
th {{ background:var(--surface-2); font-weight:500; }}
td.num, th.num {{ text-align:right; font-family:var(--mono); font-variant-numeric:tabular-nums; white-space:nowrap; }}
figure {{ margin:0 0 14px; background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:8px; }}
figure img {{ width:100%; max-width:100%; height:auto; display:block; }}
figcaption {{ font-size:12.5px; color:var(--muted); margin-top:6px; }}
.note {{ font-size:13px; color:var(--muted); }}
@media (max-width:760px) {{ .kpis {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">ic-opt-modular · 第 7 代几何 · N28 · EMX full-wave 细网格</div>
  <h1>N28 电感库</h1>
  <p class="note">ind_sym，两种体材（AP / M10），不带中心抽头；网格、EMX 设置与先导批见《电感建库提案》。本页是建库完成后的交付审阅。</p>
</header>
<div class="kpis">
  <div class="kpi"><div class="v">{len(ok)}/{len(rs)}</div><div class="l">成功点 / 总点数</div></div>
  <div class="kpi"><div class="v">{min(r['L_nH'] for r in ok if r['L_nH']):.3f}–{max(r['L_nH'] for r in ok if r['L_nH']):.2f}</div><div class="l">L_lf 覆盖（nH）</div></div>
  <div class="kpi"><div class="v">{errs[len(errs) // 2]:.1f}% / {errs[-1]:.1f}%</div><div class="l">L 对估算误差 中位 / 最大</div></div>
  <div class="kpi"><div class="v">{statistics.median(walls):.0f} s</div><div class="l">单点墙钟中位（8 并发）</div></div>
</div>

<h2>各工程</h2>
<div class="table-wrap"><table><thead><tr><th>工程</th><th>扫频</th><th class="num">点数</th><th class="num">成功</th><th class="num">L_lf nH</th><th class="num">Q_peak</th><th class="num">SRF 在带内</th><th class="num">SRF GHz</th></tr></thead>
<tbody>{"".join(proj_rows)}</tbody></table></div>

<h2>感值覆盖</h2>
<figure><img src="{scatter('L_nH', 'L_lf (nH)', True)}" alt="L vs OD"><figcaption>低频感值 L_lf（≤3 GHz 均值）对外径，按匝数着色。</figcaption></figure>
<h2>品质因数</h2>
<figure><img src="{scatter('Q_peak', 'Q_peak', False)}" alt="Q peak vs OD"><figcaption>Q_peak：扫频范围内的最大 Q（小电感的峰常在带边）。</figcaption></figure>
<h2>自谐振频率</h2>
<figure><img src="{scatter('SRF_GHz', 'SRF (GHz)', True)}" alt="SRF vs OD"><figcaption>只画带内有谐振的点；NT=1 扫到 250 GHz，其余到 150 GHz。</figcaption></figure>
<h2>与估算的一致性</h2>
<figure style="max-width:560px"><img src="{est_plot()}" alt="EMX L vs estimate"><figcaption>EMX L_lf 对 Mohan 电流片估算（旧库标定）：|误差| 中位 {errs[len(errs) // 2]:.1f}%，p90 {errs[int(0.9 * len(errs))]:.1f}%，最大 {errs[-1]:.1f}%。偏离大的点会首先暴露几何或端口问题。</figcaption></figure>
<h2>偏离估算超过 15% 的点</h2>
<div class="table-wrap"><table><thead><tr><th>点</th><th class="num">NT</th><th class="num">OD</th><th class="num">W</th><th class="num">S</th><th class="num">EMX L nH</th><th class="num">估算 nH</th><th class="num">偏差</th></tr></thead>
<tbody>{off_html or "<tr><td colspan='8'>无</td></tr>"}</tbody></table></div>
<p>结论：全部是单圈、外径 60 µm 的点，是估算公式的局限，不是几何问题。依据：（1）比值随线宽平滑单调变化：AP 从 W=4 的 1.03 升到 W=10 的 1.19，M10 从 1.13 升到 1.27；（2）外径越大比值越回落，外径 120 µm 时 AP 为 0.94–0.99、M10 为 1.02–1.05；（3）M10 在每个外径上都比 AP 高约 8%，与 M10 更薄、内感更大一致，而电流片公式不计金属厚度（标定来自旧库）。小外径宽线单圈的内径只剩约 40 µm，开口段与引线在总感值中占比变大，公式也不计这部分。抽查 AP W=10、M10 W=5 与 W=10 三个点的 GDS：设计规则审计 0 违例，端口连通正常（P1、N1 各两处，两个地 pin 各一处），manifest 为第 7 代几何。库里存的是 EMX 实测值，查询不受影响。</p>
<figure style="max-width:620px"><img src="{nt1_ratio_plot()}" alt="NT=1 EMX/estimate vs width"><figcaption>单圈电感 EMX/估算 比值对线宽，按外径分线；红色虚线为 ±15%。</figcaption></figure>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px">{"".join(f'<figure><img src="{u}" alt="{c}"><figcaption>{c} 的实际 GDS（含地环夹具）。</figcaption></figure>' for u, c in renders)}</div>
<h2>代价</h2>
<figure><img src="{cost_plot()}" alt="cost"><figcaption>8 个 EMX 并发（每个 8 线程、上限 32 GB）下的单点墙钟与峰值内存。</figcaption></figure>
<h2>失败点</h2>
<ul>{bad_html}</ul>
<h2>位置</h2>
<p>库：<code>{root}</code>（每个工程一个 ic-opt 运行存储：<code>.icopt/observations.jsonl</code>、<code>.icopt/sims/obs_*/em/ind/</code> 下的 GDS / sNp / emx.log / manifest，<code>.icopt/sims/obs_*/ind/nominal/quantities.json</code>）；网格 <code>grid.json</code>；运行日志 <code>run.log</code>。</p>
<pre style="font-family:var(--mono);font-size:12px;background:var(--code-bg);padding:10px;border-radius:6px;overflow-x:auto">{html.escape(log[-3000:])}</pre>
</div>
"""
out.write_text(page, encoding="utf-8")
print(out, f"{out.stat().st_size / 1e6:.2f} MB")
