"""Review page: real EMX at designs between the transformer library's lattice levels, against the library's intervals.

Usage: xfm_signoff_report.py OUT_HTML SIGNOFF_JSON [SIGNOFF_JSON ...]   (each a lib_signoff.json, one stratum)
The figure (saved next to the page under figs/) plots every quantity's measured error against its predicted interval
half-width: a point below the diagonal is inside its interval.
"""
from __future__ import annotations

import base64
import html
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

out = Path(sys.argv[1])
REPORTS = [json.loads(Path(p).read_text(encoding="utf-8")) for p in sys.argv[2:]]
FOCUS = ["Lp@40", "Ls@40", "Qp@40", "Qs@40", "k@40", "SRF", "Lp_lf", "Ls_lf", "k_lf"]
plt.rcParams["font.family"] = ["Noto Sans CJK JP", "Droid Sans Fallback", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def scale(q: str) -> tuple[float, str]:
    if q.startswith("L"):
        return 1e12, "pH"
    if q.startswith("SRF"):
        return 1e-9, "GHz"
    return 1.0, ""


def geo(params: dict) -> str:
    names = {"primary_outer_diameter_um": "OD_P", "secondary_outer_diameter_um": "OD_S", "primary_width_um": "W_P",
             "secondary_width_um": "W_S", "center_spacing_um": "CS"}
    return ", ".join(f"{names.get(k, k)}={float(v):g}" for k, v in params.items())


def scored(report: dict):
    for pt in report["points"]:
        for q, e in pt["quantities"].items():
            if e.get("z") is not None:
                yield pt, q, e


def figure() -> str:
    fig, axes = plt.subplots(1, len(REPORTS), figsize=(7.2 * len(REPORTS), 5.2), squeeze=False)
    for ax, r in zip(axes[0], REPORTS):
        for q in FOCUS:
            xs, ys = [], []
            for _pt, name, e in scored(r):
                if name == q:
                    xs.append((e["hi"] - e["lo"]) / 2 / abs(e["predicted"]) * 100)
                    ys.append(abs(e["measured"] - e["predicted"]) / abs(e["predicted"]) * 100)
            if xs:
                ax.scatter(xs, ys, s=28, label=q)
        lim = 35
        ax.plot([0, lim], [0, lim], "k--", lw=0.8, label="误差 = 区间半宽（区间边界）")
        ax.set_xlim(0, lim)
        ax.set_ylim(0, lim)
        ax.set_xlabel("库给出的区间半宽（% 预测值）")
        ax.set_ylabel("实测与预测的偏差（%）")
        ax.set_title(f"{r['stratum']}：格点间 5 个器件，{r['ok']}/{r['signed_off']} 仿真成功")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    figs = out.parent / "figs"
    figs.mkdir(parents=True, exist_ok=True)
    path = figs / f"{out.stem}_error_vs_interval.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def rows(report: dict) -> str:
    cells = []
    for pt in report["points"]:
        first = True
        for q in FOCUS:
            e = pt["quantities"].get(q)
            if not e or e.get("z") is None:
                continue
            k, unit = scale(q)
            err = (e["measured"] - e["predicted"]) / e["predicted"]
            hw = (e["hi"] - e["lo"]) / 2 / abs(e["predicted"])
            lead = f"<td rowspan='{sum(1 for x in FOCUS if pt['quantities'].get(x, {}).get('z') is not None)}'>{pt['obs_id']}<br><span class='small'>{html.escape(geo(pt['params']))}</span></td>" if first else ""
            first = False
            cells.append(f"<tr>{lead}<td><code>{q}</code></td><td class='num'>{e['predicted'] * k:.4g} {unit}</td><td class='num'>±{hw * 100:.1f}%</td>"
                         f"<td class='num'>{e['measured'] * k:.4g} {unit}</td><td class='num'>{err * 100:+.1f}%</td>"
                         f"<td class='num{'' if e['inside'] else ' bad'}'>{e['z']:+.2f}{'' if e['inside'] else ' ✗'}</td></tr>")
    return "".join(cells)


def summary(report: dict) -> dict:
    errs = np.array([abs(e["measured"] - e["predicted"]) / abs(e["predicted"]) for _p, _q, e in scored(report)])
    zs = np.array([abs(e["z"]) for _p, _q, e in scored(report)])
    per_q = {}
    for q in FOCUS:
        es = [abs(e["measured"] - e["predicted"]) / abs(e["predicted"]) for _p, name, e in scored(report) if name == q]
        hw = [(e["hi"] - e["lo"]) / 2 / abs(e["predicted"]) for _p, name, e in scored(report) if name == q]
        if es:
            per_q[q] = (float(np.median(es)), float(max(es)), float(np.median(hw)))
    return {"n": len(errs), "err_median": float(np.median(errs)), "err_p90": float(np.percentile(errs, 90)), "err_max": float(errs.max()),
            "z_median": float(np.median(zs)), "z_max": float(zs.max()), "coverage": report["coverage"], "per_q": per_q}


S = {r["stratum"]: summary(r) for r in REPORTS}
img = figure()
per_q_rows = "".join(
    f"<tr><td><code>{q}</code></td>" + "".join(f"<td class='num'>{S[r['stratum']]['per_q'][q][0] * 100:.1f}% / {S[r['stratum']]['per_q'][q][1] * 100:.1f}%</td>"
                                                f"<td class='num'>±{S[r['stratum']]['per_q'][q][2] * 100:.1f}%</td>" for r in REPORTS) + "</tr>"
    for q in FOCUS if all(q in S[r["stratum"]]["per_q"] for r in REPORTS))
page = f"""<title>N28 单圈变压器格点间实测复核</title>
<style>
:root {{ --ground:#f3f2ee; --surface:#fff; --surface-2:#e7e5de; --ink:#1b1c18; --muted:#5d5f55; --line:#d2d0c6; --accent:#2f6b4f; --warn:#9a4a12; --code-bg:#ebe9e2;
  --sans:"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif; --mono:"IBM Plex Mono",Menlo,Consolas,monospace; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --ground:#141512; --surface:#1d1e1a; --surface-2:#262822; --ink:#e7e8e0; --muted:#a2a497; --line:#383a32; --accent:#7cc0a0; --warn:#e3a066; --code-bg:#24261f; }} }}
:root[data-theme="dark"] {{ --ground:#141512; --surface:#1d1e1a; --surface-2:#262822; --ink:#e7e8e0; --muted:#a2a497; --line:#383a32; --accent:#7cc0a0; --warn:#e3a066; --code-bg:#24261f; }}
body {{ background:var(--ground); color:var(--ink); font-family:var(--sans); font-size:15px; line-height:1.7; margin:0; padding-block:0 64px; padding-inline:16px; }}
.wrap {{ max-width:1100px; margin:0 auto; }}
header {{ padding-block:36px 18px; border-bottom:2px solid var(--accent); }}
.eyebrow {{ font-family:var(--mono); font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }}
h1 {{ font-size:31px; margin:8px 0 10px; text-wrap:balance; }}
h2 {{ font-size:21px; margin:38px 0 10px; }}
p {{ margin:0 0 12px; max-width:86ch; }}
code {{ font-family:var(--mono); font-size:.86em; background:var(--code-bg); padding:1px 5px; border-radius:3px; }}
.kpis {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:16px 0; }}
.kpi {{ background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:11px 14px; }}
.kpi .v {{ font-family:var(--mono); font-size:21px; font-variant-numeric:tabular-nums; }}
.kpi .l {{ font-size:12.5px; color:var(--muted); }}
.table-wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:6px; background:var(--surface); margin:0 0 14px; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; }}
th, td {{ text-align:left; padding:5px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ background:var(--surface-2); font-weight:500; }}
td.num, th.num {{ text-align:right; font-family:var(--mono); font-variant-numeric:tabular-nums; white-space:nowrap; }}
td.bad {{ color:var(--warn); font-weight:600; }}
.small {{ font-size:12px; color:var(--muted); }}
.finding {{ border:1px solid var(--line); border-left:4px solid var(--warn); background:var(--surface); border-radius:6px; padding:10px 14px; margin:0 0 10px; }}
.finding b {{ color:var(--warn); }}
.ok {{ border-left-color:var(--accent); }}
.ok b {{ color:var(--accent); }}
.note {{ font-size:13px; color:var(--muted); }}
img {{ width:100%; max-width:100%; border:1px solid var(--line); border-radius:6px; }}
@media (max-width:760px) {{ .kpis {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">ic-opt-modular · N28 · 单圈变压器 · 真实 EMX 复核</div>
  <h1>N28 单圈变压器格点间实测复核</h1>
  <p class="note">问题：库的模型在外径格点之间给出 5–15% 的不确定度，是模型过于保守，还是那里真的不准？做法：从 40 GHz 可行区域里各表挑 5 个离库内实测行最远的器件（到最近行的缩放距离 0.25–0.28），先记录库的预测与区间，再用库自己的 EMX 设置真实仿真（2026-09-24 20:27–20:30，用户批准），逐量对比。</p>
</header>
<div class="kpis">
  <div class="kpi"><div class="v">{sum(r['ok'] for r in REPORTS)}/{sum(r['signed_off'] for r in REPORTS)}</div><div class="l">EMX 成功</div></div>
  <div class="kpi"><div class="v">{' / '.join(f"{S[r['stratum']]['coverage'] * 100:.0f}%" for r in REPORTS)}</div><div class="l">落在区间内的量（AP/M10 表 · M10/M9 表）</div></div>
  <div class="kpi"><div class="v">{' / '.join(f"{S[r['stratum']]['z_median']:.2f}" for r in REPORTS)}</div><div class="l">|z| 中位（区间刚好时约 0.67）</div></div>
  <div class="kpi"><div class="v">{' / '.join(f"{S[r['stratum']]['err_median'] * 100:.1f}%" for r in REPORTS)}</div><div class="l">实测与预测偏差的中位（全部 28 个量）</div></div>
</div>

<h2>结论</h2>
<div class="finding ok"><b>区间是诚实的。</b>{sum(S[r['stratum']]['n'] for r in REPORTS)} 个实测量里 {' / '.join(f"{S[r['stratum']]['coverage'] * 100:.0f}%" for r in REPORTS)} 落在库给出的区间内，|z| 中位 {' / '.join(f"{S[r['stratum']]['z_median']:.2f}" for r in REPORTS)}——与"区间刚好"的 0.67 相当，不是过宽。</div>
<div class="finding"><b>但格点之间模型确实不准。</b>Lp@40 偏差 +1.5% 到 +22%、k@40 到 +9%、SRF 到 −18%，连低频的 k_lf 也到 +16%（Lp_lf 仍在 2% 以内）；且方向一致：实测电感、耦合都高于预测，谐振低于预测——模型在格点之间低估了两个线圈的耦合。原因是库对单圈变压器的采样太稀：外径按 20 µm 一档、次级外径只取几档，而 k、SRF 和靠近谐振的 L@f 随外径组合变化很快。</div>
<div class="finding"><b>对扫参范围的含义。</b>今天给出的 40 GHz 范围来自"均值集"，在格点之间可能偏 5–20%；要让范围可信，需要在目标区域（OD_P 80–120、OD_S 80–120、线宽 5–9、偏移 0–16 µm）做一次定向加密（约 50–100 个真实 EMX，1 小时内），并把这 10 个实测点回流入库。加密后模型的区间会收窄，"2σ 稳健集"才有意义。</div>

<h2>偏差与区间半宽（每个点一个量）</h2>
<img src="{img}" alt="实测偏差对区间半宽">
<div class="table-wrap"><table><thead><tr><th>量</th>{"".join(f"<th class='num'>{r['stratum']}<br><span class='small'>偏差中位 / 最大</span></th><th class='num'>区间半宽中位</th>" for r in REPORTS)}</tr></thead><tbody>{per_q_rows}</tbody></table></div>

<h2>逐量对比（40 GHz 各量、系统 SRF、低频量）</h2>
{"".join(f"<h3>{r['stratum']}（{r['ok']}/{r['signed_off']} ok，覆盖 {r['coverage'] * 100:.0f}%）</h3><div class='table-wrap'><table><thead><tr><th>器件</th><th>量</th><th class='num'>预测</th><th class='num'>区间半宽</th><th class='num'>实测</th><th class='num'>偏差</th><th class='num'>z</th></tr></thead><tbody>{rows(r)}</tbody></table></div>" for r in REPORTS)}

<h2>位置</h2>
<p class="note">候选挑选 <code>docs/refactor/reports/library_query/xfm_signoff_candidates.py</code>；工程与报告 <code>/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28_signoff/xfm_bs_ap/</code>、<code>…/xfm_bs_m10/</code>（各 <code>.icopt/reports/lib_signoff.json</code>）；本页 <code>xfm_signoff_report.py</code>。回流（<code>adopt=true</code>）未执行。</p>
</div>
"""
out.write_text(page, encoding="utf-8")
print(out, f"{out.stat().st_size / 1e3:.0f} kB")
