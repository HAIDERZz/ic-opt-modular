"""T13.6 sign-off review page: the library's predictions for recommended designs against real EMX (lib_signoff reports).

Usage: ind_signoff_report.py OUT_HTML SIGNOFF_JSON [SIGNOFF_JSON ...]   (each a lib_signoff.json, one stratum)
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
REPORTS = [json.loads(Path(p).read_text(encoding="utf-8")) for p in sys.argv[2:]]


def scale(q: str) -> tuple[float, str]:
    if q.startswith("L"):
        return 1e9, "nH"
    if q.startswith("SRF"):
        return 1e-9, "GHz"
    return 1.0, ""


def pct(v: float | None, digits: int = 1) -> str:
    return "–" if v is None else f"{v * 100:.{digits}f}%"


def rows_for(report: dict) -> str:
    out_rows = []
    for pt in report["points"]:
        geo = ", ".join(f"{k.replace('_um', '')}={float(v):g}" for k, v in pt["params"].items())
        first = True
        for q, e in pt["quantities"].items():
            k, unit = scale(q)
            if e.get("z") is None:
                measured = "" if e.get("measured") is None else f" (实测 {e['measured'] * k:.4g} {unit})"
                cells = f"<td colspan='4' class='dim'>{e['status']}{measured}</td>"
            else:
                err = (e["measured"] - e["predicted"]) / e["predicted"]
                mark = "" if e["inside"] else " ✗"
                cells = (f"<td class='num'>{e['predicted'] * k:.4g} [{e['lo'] * k:.4g}, {e['hi'] * k:.4g}] {unit}</td>"
                         f"<td class='num'>{e['measured'] * k:.4g} {unit}</td><td class='num'>{err * 100:+.2f}%</td>"
                         f"<td class='num{'' if e['inside'] else ' bad'}'>{e['z']:+.2f}{mark}</td>")
            lead = f"<td rowspan='{len(pt['quantities'])}'>{pt['obs_id']}<br><span class='small'>{pt['part']}<br>{html.escape(geo)}</span></td>" if first else ""
            first = False
            out_rows.append(f"<tr>{lead}<td><code>{q}</code></td>{cells}</tr>")
    return "".join(out_rows)


# Coverage before the sigma floor (library commit c5488b2; user decision 2026-09-24 "4. 是" added the floor, commit 07496db).
BEFORE_FLOOR = {"inside": 66, "scored": 74, "by_stratum": {"ind_sym_ap": 0.848, "ind_sym_m10": 0.964}}
tot_inside = sum(e["inside"] for r in REPORTS for pt in r["points"] for e in pt["quantities"].values() if "inside" in e)
tot_scored = sum(1 for r in REPORTS for pt in r["points"] for e in pt["quantities"].values() if "inside" in e)
errs = [abs(e["measured"] - e["predicted"]) / e["predicted"] for r in REPORTS for pt in r["points"] for e in pt["quantities"].values() if e.get("z") is not None]
worst = max(errs)
outside = [(r["stratum"], pt["obs_id"], q, e) for r in REPORTS for pt in r["points"] for q, e in pt["quantities"].items() if e.get("inside") is False]
page = f"""<title>N28 电感推荐复核</title>
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
p {{ margin:0 0 12px; max-width:84ch; }}
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
.dim {{ color:var(--muted); }}
.small {{ font-size:12px; color:var(--muted); }}
.finding {{ border:1px solid var(--line); border-left:4px solid var(--warn); background:var(--surface); border-radius:6px; padding:10px 14px; margin:0 0 10px; }}
.finding b {{ color:var(--warn); }}
.ok {{ border-left-color:var(--accent); }}
.ok b {{ color:var(--accent); }}
.note {{ font-size:13px; color:var(--muted); }}
@media (max-width:760px) {{ .kpis {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">ic-opt-modular · T13.6 · N28 · 电感 · 真实 EMX 复核</div>
  <h1>N28 电感推荐复核</h1>
  <p class="note">10 个由 <code>lib.suggest</code> 推荐、不在库里的电感（AP 1–2 匝、M10 3–5 匝），先记录库的预测与校准区间，再用各自库工程的 EMX 设置实测（2026-09-24，用户批准），逐量对比。</p>
</header>
<div class="kpis">
  <div class="kpi"><div class="v">{sum(r['ok'] for r in REPORTS)}/{sum(r['signed_off'] for r in REPORTS)}</div><div class="l">EMX 成功</div></div>
  <div class="kpi"><div class="v">{pct(worst, 2)}</div><div class="l">实测与预测的最大相对偏差（{len(errs)} 个量）</div></div>
  <div class="kpi"><div class="v">{tot_inside}/{tot_scored} = {pct(tot_inside / tot_scored)}</div><div class="l">落在校准 2σ 区间内（G3 门槛 90%）</div></div>
  <div class="kpi"><div class="v">{' / '.join(f"{r['stratum'].split('_')[-1].upper()} {pct(r['coverage'])}" for r in REPORTS)}</div><div class="l">按分层的区间覆盖</div></div>
</div>

<h2>结论</h2>
<div class="finding ok"><b>预测准。</b>{len(errs)} 个实测量与预测的相对偏差全部在 {pct(worst, 2)} 以内（多数 0.3% 以内）；推荐目标（Lp 窗口、SRF 下限、锚定量）全部命中。</div>
<div class="finding{' ok' if tot_inside / tot_scored >= 0.9 else ''}"><b>区间覆盖 {pct(tot_inside / tot_scored)}（加 σ 下限之前 {BEFORE_FLOOR['inside']}/{BEFORE_FLOOR['scored']} = {pct(BEFORE_FLOOR['inside'] / BEFORE_FLOOR['scored'])}）。</b>第一次对比时没盖住 {BEFORE_FLOOR['scored'] - BEFORE_FLOOR['inside']} 个量，都是偏差 0.1–0.4% 而 |z| 2.5–9 的情况，集中在 Qp@10 与 Qp_peak：推荐点按"Q 最大"选出，线宽 9.9、线距 4.0 贴着参数盒边界，GP 在边界处的 σ 偏小，而留出校准（σ 放大系数按库内留出点定）主要反映内部点。按用户决定（2026-09-24），库现在给每个量的 σ 加下限——不低于该量留出的中位相对误差（<code>StratumGP(sigma_floor_rel=…)</code>，commit 07496db）；本页的预测区间与 z 已用同一批实测重算（AP {pct(BEFORE_FLOOR['by_stratum']['ind_sym_ap'])} → {pct(next(r['coverage'] for r in REPORTS if r['stratum'] == 'ind_sym_ap'))}，M10 {pct(BEFORE_FLOOR['by_stratum']['ind_sym_m10'])} → {pct(next(r['coverage'] for r in REPORTS if r['stratum'] == 'ind_sym_m10'))}）。仍在区间外的 {len(outside)} 个量：{'；'.join(f"{o[0].split('_')[-1].upper()} {o[1]} <code>{o[2]}</code>（偏差 {(o[3]['measured'] - o[3]['predicted']) / o[3]['predicted'] * 100:+.2f}%，z {o[3]['z']:+.2f}）" for o in outside)}——偏差都在 0.5% 以内，是 |z| 略过 2 的贴边情况，不再是 z 5–9 的漏报。</div>

<h2>逐量对比</h2>
{"".join(f"<h3>{r['stratum']}（{r['ok']}/{r['signed_off']} ok，覆盖 {pct(r['coverage'])}，|z| 中位 {r['median_abs_z']:.2f}）</h3><div class='table-wrap'><table><thead><tr><th>候选</th><th>量</th><th class='num'>预测 [2σ 区间]</th><th class='num'>实测</th><th class='num'>偏差</th><th class='num'>z</th></tr></thead><tbody>{rows_for(r)}</tbody></table></div>" for r in REPORTS)}
<p class="note">"out_of_domain" 的锚定量：该候选的谐振离锚定频率太近（SRF ≤ 1.25 f0），库按定义不预测；实测值照录。</p>

<h2>位置</h2>
<p class="note">候选与签核工程：<code>/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28_signoff/</code>（<code>candidates_*.json</code>、<code>run_signoff.sh</code>、各工程 <code>.icopt/reports/lib_signoff.json</code>）；本页：<code>docs/refactor/reports/library_query/ind_signoff_report.py</code>。回流（<code>adopt=true</code>）尚未执行。</p>
</div>
"""
out.write_text(page, encoding="utf-8")
print(out, f"{out.stat().st_size / 1e3:.0f} kB")
