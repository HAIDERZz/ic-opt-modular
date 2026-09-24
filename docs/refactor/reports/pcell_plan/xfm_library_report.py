"""The finished transformer library as a review page: coverage of L / k / Q / SRF, agreement with the grid estimates, cost.

Usage: xfm_library_report.py LIBRARY_ROOT OUT_HTML
Values are the query library's definitions (library.yaml strata xfm_*): Q peaks below the system SRF, SRF = the lowest
resonance over both windings. Per-point cost and memory come from the EMX logs (xfm_library_check.rows).
"""
from __future__ import annotations

import base64
import collections
import html
import io
import json
import statistics
import sys
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from ic_opt.em.pcell.render import layer_names, render
from ic_opt.library import query

sys.path.insert(0, str(Path(__file__).parent))
from xfm_library_check import PROJECTS, grid_index, rows

root, out = Path(sys.argv[1]), Path(sys.argv[2])
checks = rows(root)
grid = grid_index(root)
lib = query.Library(root, calibrate=False)
DS = {p: lib.dataset(p) for p in PROJECTS}
LOG = (root / "xfm_run.log").read_text().splitlines()
MEM = [line.split() for line in (root / "xfm_mem_samples.log").read_text().splitlines() if len(line.split()) == 3]
TITLE = {"xfm_bs_ap": "bs AP/M10", "xfm_bs_m10": "bs M10/M9", "xfm_ms_ap": "ms AP/M10", "xfm_ms_m10": "ms M10/M9"}
COLORS = {"xfm_bs_ap": "#2e6f95", "xfm_bs_m10": "#4f8f55", "xfm_ms_ap": "#b5651d", "xfm_ms_m10": "#8a3d7f"}


def png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=105)
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def col(project: str, name: str, scale: float = 1.0) -> list[float]:
    return [r.values[name] * scale for r in DS[project].rows if r.values.get(name) is not None]


def coverage_plot() -> str:
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    for p in PROJECTS:
        rs = [r for r in DS[p].rows if r.values.get("Lp_lf") and r.values.get("Ls_lf")]
        axes[0].scatter([r.values["Lp_lf"] * 1e9 for r in rs], [r.values["Ls_lf"] * 1e9 for r in rs], s=4, alpha=0.5, color=COLORS[p], label=TITLE[p])
        axes[1].hist(col(p, "k_lf"), bins=40, histtype="step", color=COLORS[p], label=TITLE[p])
        axes[2].hist(col(p, "SRF", 1e-9), bins=40, histtype="step", color=COLORS[p], label=TITLE[p])
    axes[0].set(xscale="log", yscale="log", xlabel="Lp_lf (nH)", ylabel="Ls_lf (nH)", title="inductances")
    axes[1].set(xlabel="k_lf", ylabel="points", title="coupling")
    axes[2].set(xlabel="system SRF (GHz, in the sweep)", ylabel="points", title="lowest resonance")
    axes[0].legend(fontsize=7, markerscale=3)
    fig.tight_layout()
    return png(fig)


def q_plot() -> str:
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
    for p in PROJECTS:
        rs = [r for r in DS[p].rows if r.values.get("Qp_peak") and r.values.get("Lp_lf")]
        axes[0].scatter([r.values["Lp_lf"] * 1e9 for r in rs], [r.values["Qp_peak"] for r in rs], s=4, alpha=0.5, color=COLORS[p], label=TITLE[p])
        rs = [r for r in DS[p].rows if r.values.get("Qs_peak") and r.values.get("Ls_lf")]
        axes[1].scatter([r.values["Ls_lf"] * 1e9 for r in rs], [r.values["Qs_peak"] for r in rs], s=4, alpha=0.5, color=COLORS[p])
    axes[0].set(xscale="log", xlabel="Lp_lf (nH)", ylabel="Qp peak (below the system SRF)", title="primary")
    axes[1].set(xscale="log", xlabel="Ls_lf (nH)", ylabel="Qs peak (below the system SRF)", title="secondary")
    axes[0].legend(fontsize=7, markerscale=3)
    fig.tight_layout()
    return png(fig)


def estimate_plot() -> str:
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.9))
    for p in PROJECTS:
        v = [(grid[r["grid_key"]], r) for r in checks if r["project"] == p and r["status"] == "ok" and r["grid_key"] in grid]
        v = [(g, r) for g, r in v if all(g.get(k) is not None for k in ("Lp_est_nH", "Ls_est_nH", "k_est"))]   # densification points carry no estimate
        axes[0].scatter([g["Lp_est_nH"] for g, _ in v], [r["Lp_nH"] for _, r in v], s=4, alpha=0.5, color=COLORS[p], label=TITLE[p])
        axes[1].scatter([g["Ls_est_nH"] for g, _ in v], [r["Ls_nH"] for _, r in v], s=4, alpha=0.5, color=COLORS[p])
        axes[2].scatter([g["k_est"] for g, _ in v], [r["k"] for _, r in v], s=4, alpha=0.5, color=COLORS[p])
    for ax, name in zip(axes, ("Lp (nH)", "Ls (nH)", "k")):
        lo, hi = ax.get_xlim()
        ax.plot([lo, hi], [lo, hi], color="#888", lw=0.8)
        ax.set(xlabel=f"grid estimate {name}", ylabel=f"EMX {name}")
    axes[0].set(xscale="log", yscale="log")
    axes[1].set(xscale="log", yscale="log")
    axes[0].legend(fontsize=7, markerscale=3)
    fig.tight_layout()
    return png(fig)


def memory_plot() -> str:
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
    for p in PROJECTS:
        v = [r for r in checks if r["project"] == p and r["peak_gb"] and r["mem_pred_gb"]]
        axes[0].scatter([r["mem_pred_gb"] for r in v], [r["peak_gb"] for r in v], s=4, alpha=0.5, color=COLORS[p], label=TITLE[p])
    classes = json.loads((root / "xfm_grid.json").read_text())["memory_model"]["classes"]
    for c, spec in classes.items():
        axes[0].axhline(spec["cap_gb"], color="#c0392b", lw=0.6, ls=":")
        axes[0].annotate(f"{c} cap {spec['cap_gb']:g}", (1, spec["cap_gb"]), fontsize=7, color="#c0392b", va="bottom")
    lo, hi = axes[0].get_xlim()
    axes[0].plot([lo, hi], [lo, hi], color="#888", lw=0.8)
    axes[0].set(xlabel="predicted peak (GB)", ylabel="EMX peak (GB)", title="per point: EMX peak vs prediction (dotted: class caps)")
    axes[0].legend(fontsize=7, markerscale=3)
    t = [m[0] for m in MEM]
    axes[1].plot(range(len(t)), [float(m[2]) for m in MEM], lw=0.6, color="#2e6f95")
    axes[1].axhline(256, color="#c0392b", lw=0.8, ls="--")
    axes[1].set(xlabel=f"20 s samples ({t[0]} .. {t[-1]})", ylabel="sum of EMX RSS (GB)", title="all EMX processes together (ceiling 256 GB)")
    fig.tight_layout()
    return png(fig)


def samples() -> str:
    """One mid-size device per project, rendered from its GDS."""
    names = layer_names(json.loads((root / PROJECTS[0] / ".icopt" / "spec.json").read_text())["devices"][0]["profile"])
    fig, axes = plt.subplots(1, 4, figsize=(13, 4.1))
    with tempfile.TemporaryDirectory() as tmp:
        for ax, p in zip(axes, PROJECTS):
            rs = sorted(DS[p].rows, key=lambda r: abs(r.coords["primary_outer_diameter_um"] - 150) + abs(r.coords["center_spacing_um"]))
            r = rs[0]
            gds = root / Path(r.snp).parent / "xfm.gds"
            img = render(gds, Path(tmp) / f"{p}.png", names=names, size_in=4.2, bare=True)
            ax.imshow(plt.imread(img))
            ax.set_axis_off()
            geo = ", ".join(f"{v:g}" for v in r.coords.values())
            ax.set_title(f"{TITLE[p]} {r.obs_id}\n({geo})", fontsize=8)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    return png(fig)


def project_rows() -> str:
    out_rows = []
    for p in PROJECTS:
        v = [r for r in checks if r["project"] == p]
        ok = [r for r in v if r["status"] == "ok"]
        walls = sorted(r["wall_s"] for r in ok if r["wall_s"])
        spec = json.loads((root / p / ".icopt" / "spec.json").read_text())
        fx = spec["devices"][0]["fixed"]
        ds = DS[p]
        srf_in = len(col(p, "SRF"))
        out_rows.append(
            f"<tr><td>{p}</td><td>{fx['primary_metal']} / {fx['secondary_metal']}</td><td>{', '.join(spec['em']['three_d_metals'])}</td>"
            f"<td class='num'>0–{spec['em']['frequencies']['stop_hz'] / 1e9:g} GHz</td><td class='num'>{len(ok)}/{len(v)}</td>"
            f"<td class='num'>{statistics.median(walls):.0f} / {walls[-1]:.0f} s</td><td class='num'>{srf_in}/{len(ds.rows)}</td>"
            f"<td class='num'>{min(col(p, 'Lp_lf', 1e9)):.3f}–{max(col(p, 'Lp_lf', 1e9)):.2f}</td><td class='num'>{min(col(p, 'Ls_lf', 1e9)):.3f}–{max(col(p, 'Ls_lf', 1e9)):.2f}</td>"
            f"<td class='num'>{min(col(p, 'k_lf')):.3f}–{max(col(p, 'k_lf')):.3f}</td></tr>")
    return "".join(out_rows)


def memory_rows() -> str:
    by = collections.defaultdict(list)
    for r in checks:
        if r["peak_gb"] and r["mem_pred_gb"]:
            by[r["mem_class"]].append(r)
    classes = json.loads((root / "xfm_grid.json").read_text())["memory_model"]["classes"]
    out_rows = []
    for c in sorted(by):
        v = by[c]
        ratio = sorted(r["peak_gb"] / r["mem_pred_gb"] for r in v)
        top = max(v, key=lambda r: r["peak_gb"])
        out_rows.append(f"<tr><td>{c}</td><td class='num'>{len(v)}</td><td class='num'>{classes[c]['cap_gb']:g} GB × {classes[c]['jobs']}</td>"
                        f"<td class='num'>{ratio[len(ratio) // 2]:.2f}</td><td class='num'>{ratio[-1]:.2f}</td><td class='num'>{top['peak_gb']:.1f} GB</td></tr>")
    return "".join(out_rows)


ok_all = [r for r in checks if r["status"] == "ok"]
dk = sorted(abs(r["k"] - r["k_est"]) for r in ok_all if r["k"] is not None and r["k_est"] is not None)
peak_total = max(float(m[2]) for m in MEM)
done_lines = [line for line in LOG if "done in" in line]
started, finished = "09-23 12:35", done_lines[-1][1:15] if done_lines else "?"
page = f"""<title>N28 变压器库</title>
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
h2 {{ font-size:21px; margin:38px 0 10px; text-wrap:balance; }}
p, li {{ max-width:84ch; }}
p {{ margin:0 0 12px; }}
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
.finding b {{ color:var(--warn); }}
.ok {{ border-left-color:var(--accent); }}
.ok b {{ color:var(--accent); }}
.note {{ font-size:13px; color:var(--muted); }}
@media (max-width:760px) {{ .kpis {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">ic-opt-modular · T12 · N28 · xfm_bs + xfm_ms · 细网格全波</div>
  <h1>N28 变压器库</h1>
  <p class="note">正式批 {started} 启动，{finished} 跑完；四个运行存储，按内存分档逐档运行（总线程 ≤ 128、EMX 内存上限合计 ≤ 256 GB）。量的口径与查询库一致：Q 峰只在系统 SRF 以下取，SRF 取两绕组中较低的谐振。</p>
</header>
<div class="kpis">
  <div class="kpi"><div class="v">{len(ok_all)}/{len(checks)}</div><div class="l">成功点 / 总点数</div></div>
  <div class="kpi"><div class="v">{sum(1 for r in checks if r['status'] != 'ok')}</div><div class="l">失败点</div></div>
  <div class="kpi"><div class="v">{peak_total:.1f} GB</div><div class="l">全体 EMX 进程内存峰值（上限 256 GB）</div></div>
  <div class="kpi"><div class="v">{dk[len(dk) // 2]:.3f}</div><div class="l">k 与网格估算的偏差中位 |Δk|</div></div>
</div>

<h2>各工程</h2>
<div class="table-wrap"><table><thead><tr><th>工程</th><th>初级 / 次级金属</th><th>--3d</th><th class="num">扫频</th><th class="num">成功</th><th class="num">单点墙钟 中位/最大</th><th class="num">扫频内有谐振</th><th class="num">Lp (nH)</th><th class="num">Ls (nH)</th><th class="num">k</th></tr></thead>
<tbody>{project_rows()}</tbody></table></div>
<figure><img src="{samples()}" alt="sample devices"><figcaption>每个工程一个外径约 150 µm 的样例（真实 GDS 渲染）。</figcaption></figure>

<h2>感值、耦合与谐振</h2>
<figure><img src="{coverage_plot()}" alt="coverage"><figcaption>左：Lp–Ls 覆盖（对数轴）；中：k 分布；右：扫频内的系统 SRF 分布（扫频内无谐振的点不在图中）。</figcaption></figure>

<h2>品质因数</h2>
<figure><img src="{q_plot()}" alt="Q"><figcaption>Q 峰取在系统 SRF 以下（多匝次级在扫频内谐振，谐振后初级 Q 会在带边回升，那不是器件的 Q）。</figcaption></figure>

<h2>与网格估算的一致性</h2>
<figure><img src="{estimate_plot()}" alt="estimate"><figcaption>建网格时的电流片估算与 EMX 结果；k 的估算来自旧库拟合。</figcaption></figure>
<p class="note">k 偏差 |Δk| 中位 {dk[len(dk) // 2]:.3f}、90% 分位 {dk[int(0.9 * len(dk))]:.3f}、最大 {dk[-1]:.3f}：估算只用于分档与排程，库里存的是 EMX 结果。</p>

<h2>内存与代价</h2>
<div class="table-wrap"><table><thead><tr><th>内存档</th><th class="num">点数</th><th class="num">每任务上限 × 并发</th><th class="num">实测/预测 中位</th><th class="num">最大</th><th class="num">单点最高峰值</th></tr></thead>
<tbody>{memory_rows()}</tbody></table></div>
<figure><img src="{memory_plot()}" alt="memory"><figcaption>左：每点 EMX 峰值对预测（虚线为各档每任务上限）；右：全体 EMX 进程内存之和（20 秒采样）。</figcaption></figure>
<div class="finding"><b>留意：内存预测在大器件上偏低。</b>实测/预测的中位从 A 档的 0.66 升到 D、E 档的 0.85，最大 1.18（D 档单点 32.1 GB，等于该档每任务上限）。EMX 的上限是软的，这些点都正常完成；各任务峰值错开，全体合计最高 {peak_total:.1f} GB，远低于 256 GB。以后大器件分档按预测的约 1.2 倍留余量。</div>

<h2>位置</h2>
<p class="note">库：<code>{html.escape(str(root))}</code>（四个运行存储 + <code>xfm_grid.json</code> + <code>xfm_run.log</code> + <code>xfm_mem_samples.log</code> + <code>library.yaml</code>）。脚本：<code>docs/refactor/reports/pcell_plan/xfm_{{grid,memory,pilot,library,library_check,library_report}}.py</code>。查询验证见 <code>docs/refactor/reports/library_query/XFM_QUERY_VERIFY_CN.html</code>。</p>
</div>
"""
out.write_text(page, encoding="utf-8")
print(out, f"{out.stat().st_size / 1e6:.2f} MB")
