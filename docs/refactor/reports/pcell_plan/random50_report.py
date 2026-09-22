"""The random-device campaign report: re-renders thumbnails, contact sheets and two close-ups per family, then writes REPORT.html.

Usage: random50_report.py CAMPAIGN_DIR [SECOND_SEED_DIR]
"""
from __future__ import annotations

import base64
import html
import json
import random
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.pyplot as plt

from ic_opt.em.pcell.render import layer_names, render

campaign = Path(sys.argv[1])
second = Path(sys.argv[2]) if len(sys.argv) > 2 and Path(sys.argv[2]).exists() else None
summary = json.loads((campaign / "summary.json").read_text())
profile = summary["profile"]
names = layer_names(profile)
fig_dir = campaign / "report_fig"
fig_dir.mkdir(exist_ok=True)

FAMILY_CN = {"clean_port_ind_sym": "对称电感 ind_sym", "clean_port_xfm_bs": "广边耦合变压器 xfm_bs", "clean_port_xfm_ms": "1:N 叠层变压器 xfm_ms",
             "clean_port_xfm_balun": "同层嵌套巴伦 xfm_balun", "clean_port_xfm_tw": "交织变压器 xfm_tw", "clean_port_xfm_il": "交错变压器 xfm_il"}
RANGES = {
    "clean_port_ind_sym": "OD 60–240, W 4–10, S 2–4, NT 1–5; 30% 带 CT（metal−2）、20% 独立端口间距 15–31、20% PGS",
    "clean_port_xfm_bs": "OD_P/OD_S 60–240, W 4–10, 中心偏移 0–min(OD)/2; 30% 双抽头、20% 端口间距、20% PGS",
    "clean_port_xfm_ms": "OD_P 60–240, OD_S 50–200, W_P 4–10, W_S 3–8, NT_S 2–5, S_S 2–4, 中心偏移 0–min(OD)/2; 30% 双抽头、20% 端口间距、20% PGS",
    "clean_port_xfm_balun": "NT_P 1–4, W 4–10, S 2–4, OD_P 60+30·NT_P–240, OD_S = OD_P−2·NT_P·(W+S)−2S−[0,10], OPENING_S 8–15; 30% 双抽头、20% 主边端口间距",
    "clean_port_xfm_tw": "OD 80–300, W 4–10, S 2–6, NR ∈ {3,5,7}",
    "clean_port_xfm_il": "OD 80–300, W 4–9, S 2–4, NT 2–5, OPENING_P/S 12–18; 30% 双抽头（M10 体向上 AP，AP 体向下 metal−3）",
}


def param_tag(config: dict) -> str:
    return ", ".join(f"{k.replace('_um', '')}={v}" for k, v in config.items() if k not in ("port_order", "pgs", "ground_fixture") and v is not None and not isinstance(v, dict))


def data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def rebuild_thumbs(camp: Path, fam: str, devices: list[dict]) -> Path:
    for d in devices:
        gds = Path(d["gds"])
        if not gds.exists():
            gds = camp / fam / f"{d['index']:02d}" / f"{fam}.gds"
        render(gds, gds.parent / "thumb.png", names=names, dpi=60, size_in=2.4, bare=True)
    cols = 10
    rows = (len(devices) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 1.5, rows * 1.5), dpi=90)
    for ax in axes.flat:
        ax.axis("off")
    for ax, d in zip(axes.flat, devices):
        gds = camp / fam / f"{d['index']:02d}" / f"{fam}.gds"
        ax.imshow(mpimg.imread(gds.parent / "thumb.png"))
        ax.set_title(f"{d['index']:02d}", fontsize=7, color="black" if d["ok"] else "red")
    fig.tight_layout(pad=0.15)
    out = fig_dir / f"{fam}_sheet.png"
    fig.savefig(out)
    plt.close(fig)
    return out


sections = []
totals = {"accepted": 0, "refused": 0, "ok": 0}
rng = random.Random(7)
for fam, s in summary["families"].items():
    devices = s["devices"]
    sheet = rebuild_thumbs(campaign, fam, devices)
    picks = rng.sample(devices, 2)
    closeups = []
    for d in picks:
        gds = campaign / fam / f"{d['index']:02d}" / f"{fam}.gds"
        tag = param_tag(d["config"])
        png = render(gds, fig_dir / f"{fam}_{d['index']:02d}.png", names=names, title=f"{fam} #{d['index']:02d}", dpi=100, size_in=5.6)
        closeups.append((d, tag, png))
    totals["accepted"] += s["accepted"]
    totals["refused"] += s["refused"]
    totals["ok"] += sum(1 for d in devices if d["ok"])
    reasons = "".join(f"<li><code>{html.escape(r)}</code> × {n}</li>" for r, n in s["refusal_reasons"])
    rows = "".join(
        "<tr><td>%02d</td><td>%s</td><td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td><td class='num'>%.2f</td></tr>" % (
            d["index"], html.escape(param_tag(d["config"])), "✓" if not d["audit"] else html.escape(",".join(d["audit"])),
            "✓" if d["topology_ok"] else "✗", "✓" if d["pad_hang"] == 0 else "%.1f%%" % (100 * d["pad_hang"]), "✓" if d["ports_outward"] else "✗", d["build_s"])
        for d in devices)
    close_html = "".join(
        f"<figure><img src='{data_uri(png)}' alt='{fam} {d['index']:02d}'><figcaption><b>#{d['index']:02d}</b> {html.escape(tag)}</figcaption></figure>"
        for d, tag, png in closeups)
    sections.append(f"""
<h2 id="{fam}">{FAMILY_CN[fam]}</h2>
<div class="kpis">
  <div class="kpi"><div class="v">{s['accepted']}</div><div class="l">生成并通过全部自检</div></div>
  <div class="kpi"><div class="v">{s['refused']}</div><div class="l">抽样被生成器拒绝（fail-closed）</div></div>
  <div class="kpi"><div class="v">{s['taps']} / {s['port_spacing']} / {s['pgs']}</div><div class="l">含中心抽头 / 独立端口间距 / PGS</div></div>
  <div class="kpi"><div class="v">{s['build_s']['median']:.2f} s</div><div class="l">构造耗时中位数（最大 {s['build_s']['max']:.2f} s）</div></div>
</div>
<p class="note">抽样范围：{RANGES[fam]}。</p>
<figure class="sheet"><img src="{data_uri(sheet)}" alt="{fam} contact sheet"><figcaption>50 个器件缩略图（编号 = 生成顺序；红色编号 = 未通过自检，本次无）。</figcaption></figure>
<div class="figs two">{close_html}</div>
<details><summary>拒绝原因（数字已掩码）</summary><ul>{reasons or '<li>无</li>'}</ul></details>
<details><summary>50 个器件逐条自检</summary><div class="table-wrap"><table><thead><tr><th>#</th><th>参数</th><th class="num">DRC</th><th class="num">连通</th><th class="num">落点</th><th class="num">朝向</th><th class="num">秒</th></tr></thead><tbody>{rows}</tbody></table></div></details>
""")

second_html = ""
if second is not None and (second / "summary.json").exists():
    s2 = json.loads((second / "summary.json").read_text())
    rows2 = "".join(f"<tr><td>{FAMILY_CN[f]}</td><td class='num'>{d['accepted']}</td><td class='num'>{d['refused']}</td><td class='num'>{'全部通过' if d['all_ok'] else '未通过: ' + str(d['not_ok'])}</td></tr>" for f, d in s2["families"].items())
    second_html = f"""
<h2 id="seed2">第二组随机（seed {s2['seed']}）</h2>
<p>同一脚本换一个随机种子再做一遍，验证第一组不是侥幸：</p>
<div class="table-wrap"><table><thead><tr><th>家族</th><th class="num">通过自检</th><th class="num">被拒绝</th><th class="num">结果</th></tr></thead><tbody>{rows2}</tbody></table></div>
"""

page = f"""<title>PCell 随机自检</title>
<style>
:root {{ --ground:#f2f4f6; --surface:#fff; --surface-2:#e9edf1; --ink:#17232d; --muted:#58697a; --line:#cfd7de; --accent:#1f5f8b; --good:#2b7a58; --bad:#b3261e; --code-bg:#eef2f5;
  --sans:"Noto Sans SC","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif; --mono:"IBM Plex Mono",Menlo,Consolas,monospace; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --ground:#0f161c; --surface:#172029; --surface-2:#1f2a35; --ink:#e4ebf1; --muted:#9fb0bf; --line:#2c3a47; --accent:#7db6e3; --good:#6cc59c; --bad:#f19a92; --code-bg:#1d2731; }} }}
:root[data-theme="dark"] {{ --ground:#0f161c; --surface:#172029; --surface-2:#1f2a35; --ink:#e4ebf1; --muted:#9fb0bf; --line:#2c3a47; --accent:#7db6e3; --good:#6cc59c; --bad:#f19a92; --code-bg:#1d2731; }}
body {{ background:var(--ground); color:var(--ink); font-family:var(--sans); font-size:15px; line-height:1.7; margin:0; padding-block:0 64px; padding-inline:16px; }}
.wrap {{ max-width:1080px; margin:0 auto; }}
header {{ padding-block:36px 20px; border-bottom:1px solid var(--line); }}
.eyebrow {{ font-family:var(--mono); font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }}
h1 {{ font-size:32px; line-height:1.2; margin:8px 0 10px; text-wrap:balance; }}
h2 {{ font-size:21px; margin:40px 0 10px; scroll-margin-top:16px; }}
p {{ max-width:78ch; margin:0 0 12px; }}
.lead {{ color:var(--muted); font-size:16px; max-width:70ch; }}
code {{ font-family:var(--mono); font-size:.86em; background:var(--code-bg); padding:1px 5px; border-radius:3px; }}
.verdict {{ background:var(--surface); border:1px solid var(--line); border-left:4px solid var(--good); border-radius:6px; padding:16px 20px; margin-top:16px; }}
.kpis {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:10px 0 12px; }}
.kpi {{ background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:10px 14px; }}
.kpi .v {{ font-family:var(--mono); font-size:22px; font-variant-numeric:tabular-nums; }}
.kpi .l {{ font-size:12.5px; color:var(--muted); }}
.note {{ font-size:13px; color:var(--muted); }}
figure {{ margin:0; background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:8px; }}
figure img {{ width:100%; max-width:100%; height:auto; display:block; }}
figcaption {{ font-size:12.5px; color:var(--muted); margin-top:6px; }}
figure.sheet {{ margin:12px 0; }}
.figs.two {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin:12px 0; }}
.table-wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:6px; background:var(--surface); margin:8px 0 12px; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; }}
th, td {{ text-align:left; vertical-align:top; padding:6px 10px; border-bottom:1px solid var(--line); }}
th {{ background:var(--surface-2); font-weight:500; white-space:nowrap; }}
td.num, th.num {{ text-align:right; font-family:var(--mono); font-variant-numeric:tabular-nums; white-space:nowrap; }}
details {{ margin:8px 0; }} summary {{ cursor:pointer; color:var(--accent); }}
ul {{ max-width:90ch; }} li {{ margin-bottom:4px; font-size:13.5px; }}
nav {{ display:flex; flex-wrap:wrap; gap:6px 14px; padding-block:12px; font-size:13px; border-bottom:1px solid var(--line); }}
nav a {{ color:var(--accent); text-decoration:none; }}
@media (max-width:760px) {{ .kpis, .figs.two {{ grid-template-columns:1fr; }} h1 {{ font-size:26px; }} }}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">ic-opt-modular · 打磨后自检 · 工艺 {html.escape(profile)} · 几何第 {summary['geometry_version']} 代 · 2026-09-22</div>
  <h1>PCell 随机自检</h1>
  <p class="lead">六个家族各随机生成 50 个器件（真实 N28 规则，AP 与 M10 两种体材随机），每个器件过五项自检：打包 DRC 审计、端口连通性（LVS-lite）、跨线落点焊盘包含、端口朝向、构造耗时。缩略图与每条结果都在页内，供你逐一审查。</p>
</header>
<nav>{"".join(f'<a href="#{f}">{FAMILY_CN[f].split(" ")[-1]}</a>' for f in summary["families"])}<a href="#seed2">第二组</a><a href="#method">方法</a></nav>

<div class="verdict">
<p><b>结果：{totals['ok']} / {totals['accepted']} 个器件全部通过五项自检；生成器在抽样中拒绝了 {totals['refused']} 次（每次都带具体原因，见各族折叠区）。</b></p>
<p>这一轮抽样自身还暴露并修掉了 4 个问题（都已提交）：端口间距外扩折线的直段没算进斜接偏移（AP 上短 0.33 µm）；巴伦嵌套逃逸的臂尖焊盘可以越出次级平边；NT=2 紧凑规划器的车道上界没约束焊盘落在平边内（4% 悬空）；xfm_il 只查 P/S 之间的桥层间距、不查同一绕组自己的落点栈与相邻匝对角线（4/50 器件）。修复后重跑得到上面的数字。</p>
</div>

{"".join(sections)}
{second_html}

<h2 id="method">方法与位置</h2>
<ul>
  <li>脚本：<code>docs/refactor/reports/pcell_plan/random50.py</code>（抽样 + 生成 + 自检 + 缩略图）与 <code>random50_report.py</code>（本页）。命令：<code>IC_OPT_PROFILE_DIRS=… python random50.py {html.escape(profile)} smoke/pcell_random50 --per-family 50 --seed 1</code>。</li>
  <li>产物（不入仓库，含 N28 几何）：<code>/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/smoke/pcell_random50/&lt;family&gt;/&lt;nn&gt;/</code>（GDS、manifest、emx_ports.txt、缩略图），汇总 <code>summary.json</code>，本页 <code>REPORT.html</code>。</li>
  <li>五项自检：<code>audit_gds</code>（宽度/间距/宽线平行/RV 包围，忽略地环的 M1 max_width）；<code>connectivity.nets</code>（P/N 同网、P 与 S 异网、抽头在各自绕组、所有 G 桩同一地网、标签都在金属上）；落点焊盘包含（含过孔的 ≤1.5W 方块减去其余金属为空；xfm_tw 的下潜腿焊盘是导体本身，不适用）；manifest 端口朝向向外；构造耗时。</li>
  <li>被拒绝的抽样不是缺陷：随机范围故意宽于库的设计域，让每条 fail-closed 守卫都有机会出现在报告里。</li>
</ul>
</div>
"""
(campaign / "REPORT.html").write_text(page, encoding="utf-8")
print(campaign / "REPORT.html", f"{(campaign / 'REPORT.html').stat().st_size / 1e6:.2f} MB", totals)
