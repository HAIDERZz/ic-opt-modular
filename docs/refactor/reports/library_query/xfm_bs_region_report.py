"""Review page for xfm_bs_region.py: the sweep ranges the library recommends for a set of targets at one frequency.

Usage: xfm_bs_region_report.py OUT_HTML REGION_JSON LIBRARY_ROOT
"""
from __future__ import annotations

import base64
import html
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
R = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
ROOT = Path(sys.argv[3])
F0 = f"{R['f0_ghz']:g}"
DIM_LABEL = {"OD_P": "初级外径 OD_P", "OD_S": "次级外径 OD_S", "W_P": "初级线宽 W_P", "W_S": "次级线宽 W_S", "CS": "中心偏移 CS"}
DIM_FIELD = {"OD_P": "primary_outer_diameter_um", "OD_S": "secondary_outer_diameter_um", "W_P": "primary_width_um",
             "W_S": "secondary_width_um", "CS": "center_spacing_um"}
STEP = {"OD_P": 1, "OD_S": 1, "W_P": 0.1, "W_S": 0.1, "CS": 0.5}


def img(path: str) -> str:
    data = base64.b64encode(Path(path).read_bytes()).decode()
    return f"<img src='data:image/png;base64,{data}' alt='可行区域图' style='width:100%;max-width:100%;border:1px solid var(--line);border-radius:6px'>"


def fmt_q(q: str, v: float | None) -> str:
    if v is None:
        return "–"
    if q.startswith("L"):
        return f"{v * 1e12:.0f} pH"
    if q.startswith("SRF"):
        return f"{v / 1e9:.0f} GHz"
    if q.startswith("k"):
        return f"{v:.3f}"
    return f"{v:.1f}"


def pct_or_dash(v: float | None) -> str:
    return "–" if v is None else f"{v * 100:.0f}%"


def rng(r: list[float], d: str) -> str:
    return f"{r[0]:g} – {r[1]:g} µm" if d != "CS" or r[1] > 0 else "0 µm"


def fixed_fields(part: str) -> dict:
    spec = json.loads((ROOT / part / ".icopt" / "spec.json").read_text(encoding="utf-8"))
    d = spec["devices"][0]
    return {"fixed": d["fixed"], "em": {"stop_ghz": spec["em"]["frequencies"]["stop_hz"] / 1e9, "three_d": spec["em"]["three_d_metals"]}}


def stratum_section(name: str, e: dict) -> str:
    if "error" in e:
        return f"<h2>{name}</h2><div class='finding'><b>无结果。</b>{html.escape(e['error'])}</div>"
    part = name
    ff = fixed_fields(part)
    fm, fr = e["feasible_mean"], e["feasible_robust"]
    rows_rng = "".join(
        f"<tr><td>{DIM_LABEL[d]}<br><span class='small'><code>{DIM_FIELD[d]}</code></span></td>"
        f"<td class='num'>{rng(fr['ranges'][d], d) if fr['count'] else '–'}</td>"
        f"<td class='num'>{rng(fm['ranges'][d], d) if fm['count'] else '–'}</td>"
        f"<td class='num'>{e['domain_ranges'][d][0]:g} – {e['domain_ranges'][d][1]:g} µm</td><td class='num'>{STEP[d]:g} µm</td></tr>"
        for d in ("OD_P", "OD_S", "W_P", "W_S", "CS"))
    level = "by_width_robust" if fr["count"] else "by_width_mean"
    width_rows = "".join(
        f"<tr><td class='num'>{w['W_P']:g}</td><td class='num'>{w['W_S']:g}</td><td class='num'>{w['OD_P'][0]:g} – {w['OD_P'][1]:g}</td>"
        f"<td class='num'>{w['OD_S'][0]:g} – {w['OD_S'][1]:g}</td><td class='num'>{w['CS'][0]:g} – {w['CS'][1]:g}</td>"
        f"<td class='num'>{w['Qmin'][0]:.1f} – {w['Qmin'][1]:.1f}</td><td class='num'>{w['count']}</td></tr>" for w in e[level])
    cs_rows = "".join(f"<tr><td class='num'>{c['CS']:g}</td><td class='num'>{c['k'][0]:.3f} – {c['k'][1]:.3f}</td><td class='num'>{c['k_median']:.3f}</td><td class='num'>{c['count']}</td></tr>"
                      for c in e["k_by_cs"])
    qn = [f"Lp@{F0}", f"Ls@{F0}", f"Qp@{F0}", f"Qs@{F0}", f"k@{F0}", "SRF"]
    cand_rows = "".join(
        "<tr><td>" + ", ".join(f"{d}={c['params'][DIM_FIELD[d]]:g}" for d in ("OD_P", "OD_S", "W_P", "W_S", "CS")) + "</td>"
        + "".join(f"<td class='num'>{fmt_q(q, c['quantities'][q]['mu'])}<br><span class='small'>[{fmt_q(q, c['quantities'][q]['lo'])}, {fmt_q(q, c['quantities'][q]['hi'])}]</span></td>" for q in qn)
        + "</tr>" for c in e["candidates"])
    hit_rows = "".join(
        f"<tr><td>{h['obs_id']}<br><span class='small'>" + ", ".join(f"{d}={h['params'][DIM_FIELD[d]]:g}" for d in ("OD_P", "OD_S", "W_P", "W_S", "CS")) + "</span></td>"
        + "".join(f"<td class='num'>{fmt_q(q, h['quantities'][q])}</td>" for q in qn) + "</tr>" for h in e["measured_hits"])
    model_rows = "".join(f"<tr><td><code>{q}</code></td><td class='num'>{m['rows']}</td><td class='num'>{m['median_rel'] * 100:.2f}%</td>"
                         f"<td class='num'>{pct_or_dash(m.get('coverage'))}</td><td class='num'>{m['k_scale']:.2f}</td></tr>"
                         for q, m in e["models"].items())
    b = e["binding"]
    edge_note = ", ".join(f"{d} 触及{'下' if v['at_min'] else ''}{'上' if v['at_max'] else ''}界" for d, v in e.get("edge", {}).items() if v["at_min"] or v["at_max"])
    fx = ff["fixed"]
    fixed_txt = (f"初级金属 <code>{fx['primary_metal']}</code>、次级金属 <code>{fx['secondary_metal']}</code>；开口 {fx['primary_opening_um']:g}/{fx['secondary_opening_um']:g} µm；"
                 f"引线 {fx['primary_lead_length_um']:g}/{fx['secondary_lead_length_um']:g} µm；地环 {fx['ground_fixture']}；EMX 扫到 {ff['em']['stop_ghz']:g} GHz，3D 金属 {ff['em']['three_d']}")
    return f"""
<h2>{name}</h2>
<div class="kpis">
  <div class="kpi"><div class="v">{fr['count']}</div><div class="l">2σ 区间整体满足的网格点</div></div>
  <div class="kpi"><div class="v">{fm['count']}</div><div class="l">预测均值满足的网格点（共 {e['grid_in_domain_and_confident']} 个域内可信点）</div></div>
  <div class="kpi"><div class="v">{len(e['measured_hits'])}</div><div class="l">库内实测已满足的行（{e['usable_at_f0']} 行在 {F0} GHz 可用）</div></div>
  <div class="kpi"><div class="v">{b['L']} / {b['Qp']} / {b['Qs']} / {b['k']}</div><div class="l">单项满足数：电感窗 / Qp / Qs / k（谁最小谁最紧）</div></div>
</div>
<h3>扫参范围</h3>
<div class="table-wrap"><table><thead><tr><th>参数</th><th class="num">建议扫描范围（2σ 稳健）</th><th class="num">均值可行范围（外包络）</th><th class="num">库覆盖范围</th><th class="num">库步长</th></tr></thead><tbody>{rows_rng}</tbody></table></div>
<p class="note">固定参数（与库一致）：{fixed_txt}。{('可行区域 ' + edge_note + '，该方向超出库覆盖范围后模型不再作答。') if edge_note else '可行区域未触及库覆盖范围的边界。'}</p>
{img(e['figure']) if e.get('figure') else ''}
<h3>按线宽对看外径与偏移（{'2σ 稳健' if fr['count'] else '均值'}）</h3>
<div class="table-wrap"><table><thead><tr><th class="num">W_P</th><th class="num">W_S</th><th class="num">OD_P 范围</th><th class="num">OD_S 范围</th><th class="num">CS 范围</th><th class="num">min(Qp,Qs) 预测</th><th class="num">点数</th></tr></thead><tbody>{width_rows}</tbody></table></div>
<h3>k 随中心偏移（电感落窗的点）</h3>
<div class="table-wrap"><table><thead><tr><th class="num">CS (µm)</th><th class="num">k@{F0} 范围</th><th class="num">k 中位</th><th class="num">点数</th></tr></thead><tbody>{cs_rows}</tbody></table></div>
<h3>代表性候选（{'2σ 稳健' if e['candidates_level'] == 'robust' else '均值'}集里 min(Qp,Qs) 最高的 {len(e['candidates'])} 个，彼此拉开）</h3>
<div class="table-wrap"><table><thead><tr><th>参数</th>{"".join(f"<th class='num'>{q}<br><span class='small'>预测 [2σ]</span></th>" for q in qn)}</tr></thead><tbody>{cand_rows}</tbody></table></div>
<h3>库内实测已满足全部条件的行</h3>
<div class="table-wrap"><table><thead><tr><th>观测</th>{"".join(f"<th class='num'>{q}</th>" for q in qn)}</tr></thead><tbody>{hit_rows or "<tr><td colspan='7' class='dim'>无</td></tr>"}</tbody></table></div>
<details><summary class="note">模型校准（留出 5 折）</summary><div class="table-wrap"><table><thead><tr><th>量</th><th class="num">行</th><th class="num">留出中位误差</th><th class="num">2σ 覆盖</th><th class="num">k_scale</th></tr></thead><tbody>{model_rows}</tbody></table></div></details>
"""


C = R["constraints"]
strata = list(R["strata"])
page = f"""<title>N28 单圈变压器 {F0} GHz 扫参范围</title>
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
.dim {{ color:var(--muted); }}
.small {{ font-size:12px; color:var(--muted); }}
.finding {{ border:1px solid var(--line); border-left:4px solid var(--warn); background:var(--surface); border-radius:6px; padding:10px 14px; margin:0 0 10px; }}
.finding b {{ color:var(--warn); }}
.ok {{ border-left-color:var(--accent); }}
.ok b {{ color:var(--accent); }}
.note {{ font-size:13px; color:var(--muted); }}
details {{ margin:10px 0; }}
@media (max-width:760px) {{ .kpis {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">ic-opt-modular · 查询库 · N28 · clean_port_xfm_bs · {F0} GHz</div>
  <h1>N28 单圈变压器 {F0} GHz 扫参范围</h1>
  <p class="note">问题：{F0} GHz 处初级、次级电感均在 {C['L_pH'][0]:g}–{C['L_pH'][1]:g} pH，两绕组 Q &gt; {C['Q_min']:g}，k = {C['k'][0]:g}–{C['k'][1]:g}，初级次级均为单圈——参数化建模的扫参范围。
  器件：<code>clean_port_xfm_bs</code>（单圈初级 + 单圈次级，上下层宽边耦合，中心可偏移）；两种金属组合各一层：AP/M10 与 M10/M9。
  方法：在库的采样域内按步长 OD {R['grid_steps']['OD']:g} µm、线宽 {R['grid_steps']['W']:g} µm、偏移 {R['grid_steps']['CS']:g} µm 铺网格，用库的 GP 模型预测 {F0} GHz 的 Lp、Ls、Qp、Qs、k 与系统 SRF（SRF ≥ 1.25 × {F0} GHz 才算可用），
  "2σ 稳健"= 校准后的 2σ 区间整体落在目标窗内，"均值可行"= 预测均值落在窗内。</p>
</header>

<h2>结论</h2>
{"".join(f"<div class='finding{' ok' if R['strata'][s].get('feasible_robust', {}).get('count') else ''}'><b>{s}：</b>" + (("2σ 稳健可行 " + str(R['strata'][s]['feasible_robust']['count']) + " 点，均值可行 " + str(R['strata'][s]['feasible_mean']['count']) + " 点；库内已有 " + str(len(R['strata'][s]['measured_hits'])) + " 行实测满足全部条件。建议扫描：" + "；".join(f"{DIM_LABEL[d]} {rng(R['strata'][s]['feasible_robust']['ranges'][d], d)}" for d in ('OD_P', 'OD_S', 'W_P', 'W_S', 'CS')) + "。") if R['strata'][s].get('feasible_robust', {}).get('count') else ("2σ 稳健集为空；均值可行 " + str(R['strata'][s].get('feasible_mean', {}).get('count', 0)) + " 点" + ("：" + "；".join(f"{DIM_LABEL[d]} {rng(R['strata'][s]['feasible_mean']['ranges'][d], d)}" for d in ('OD_P', 'OD_S', 'W_P', 'W_S', 'CS')) if R['strata'][s].get('feasible_mean', {}).get('count') else "") + "。")) + "</div>" for s in strata)}
<p class="note">读法：范围是可行点在各维上的投影（外包络），维之间相关——按"线宽对"表取对应的外径与偏移更准；k 主要由中心偏移决定，见各层"k 随中心偏移"表。Q 是最紧的约束：这一尺寸的单圈环在 {F0} GHz 的 Q 大多在 8–14 之间，Q &gt; {C['Q_min']:g} 只在较宽线（8–10 µm）、外径 80–100 µm 一带成立。</p>

{"".join(stratum_section(s, R['strata'][s]) for s in strata)}

<h2>位置</h2>
<p class="note">扫描脚本 <code>docs/refactor/reports/library_query/xfm_bs_region.py</code>，结果 <code>{html.escape(sys.argv[2])}</code>，本页 <code>docs/refactor/reports/library_query/xfm_bs_region_report.py</code>；库 <code>{ROOT}</code>（<code>library.yaml</code> 的 bs 层锚点已加入 {F0} GHz，备份 <code>library.yaml.bak_20260924_1420</code>）。</p>
</div>
"""
out.write_text(page, encoding="utf-8")
print(out, f"{out.stat().st_size / 1e3:.0f} kB")
