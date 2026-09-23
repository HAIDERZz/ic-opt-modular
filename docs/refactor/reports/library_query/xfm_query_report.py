"""The transformer query-library verification (T13.7) as a review page, from xfm_query_verify.py outputs.

Usage: xfm_query_report.py LIBRARY_ROOT OUT_HTML VERIFY_JSON [VERIFY_JSON ...]   (one or more strata per JSON)
Figures: held-out parity (seed 0, the library's own model settings) for Lp_lf, k_lf, SRF and Qp_peak per stratum,
and the SRF_p jump figure (figs/xfm_srf_jump.png, made by xfm_srf_jump_fig.py).
"""
from __future__ import annotations

import base64
import html
import io
import json
import sys
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from ic_opt.library import gp, query

sys.path.insert(0, str(Path(__file__).parent))
from xfm_query_verify import settings, xy

warnings.filterwarnings("ignore", module="sklearn")
root, out = Path(sys.argv[1]), Path(sys.argv[2])
V: dict = {}
for path in sys.argv[3:]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    V.update({s: doc[s] for s in doc["strata"]})
STRATA = list(V)
LIB = query.Library(root, calibrate=False)
JUMP_FIG = Path(__file__).parent / "figs" / "xfm_srf_jump.png"
PANELS = (("Lp_lf", "Lp_lf (nH)", 1e9), ("k_lf", "k_lf", 1.0), ("SRF", "system SRF (GHz)", 1.0), ("Qp_peak", "Qp_peak (0-150 GHz)", 1.0))


def png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=105)
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def pct(v: float | None, digits: int = 2) -> str:
    return "–" if v is None else f"{v * 100:.{digits}f}%"


def parity() -> str:
    fig, axes = plt.subplots(len(STRATA), len(PANELS), figsize=(13, 3.3 * len(STRATA)), squeeze=False)
    for i, stratum in enumerate(STRATA):
        for j, (q, label, scale) in enumerate(PANELS):
            ax = axes[i][j]
            x, y = xy(LIB, stratum, q)
            test, train = gp.split(len(y), 0)
            model = gp.StratumGP(**settings(LIB, stratum, q, y[train])).fit(x[train], y[train])
            mu, _ = model.predict(x[test])
            ok = np.isfinite(mu)
            ax.scatter(y[test][ok] * scale, mu[ok] * scale, s=8, color="#2e6f95", alpha=0.7, edgecolors="none")
            lo, hi = min(y[test][ok].min(), mu[ok].min()) * scale, max(y[test][ok].max(), mu[ok].max()) * scale
            ax.plot([lo, hi], [lo, hi], color="#888", lw=0.8)
            ax.set_title(f"{stratum}: {label}", fontsize=9)
            ax.set_xlabel("measured", fontsize=8)
            ax.set_ylabel("predicted", fontsize=8)
            ax.tick_params(labelsize=7)
    fig.tight_layout()
    return png(fig)


def forward_rows(stratum: str) -> str:
    rows = []
    for key, r in V[stratum]["forward"].items():
        q, variant = key.split("|")
        if variant != "library":
            continue
        rows.append(f"<tr><td><code>{html.escape(q)}</code></td><td class='num'>{r['n']}</td><td class='num'>{pct(r['median_rel'], 3)}</td>"
                    f"<td class='num'>{pct(r['p90_rel'])}</td><td class='num'>{pct(r['max_rel'], 1)}</td><td class='num'>{pct(r['coverage_2sigma'], 1)}</td>"
                    f"<td class='num'>{r['k_scale_seeds_0_2']:.2f}</td><td class='num'>{pct(r['coverage_calibrated_seeds_3_4'], 1)}</td></tr>")
    return "".join(rows)


def kmap_rows() -> str:
    rows = []
    for stratum in STRATA:
        fw = V[stratum]["forward"]
        for key, r in fw.items():
            q, variant = key.split("|")
            if variant == "library" and r.get("feature_map"):
                for other in ("identity", "identity-joint"):
                    o = fw.get(f"{q}|{other}")
                    if o:
                        rows.append(f"<tr><td>{stratum}</td><td><code>{q}</code></td><td>{other}（{o['nt_mode']}）</td><td class='num'>{pct(o['median_rel'], 3)}</td>"
                                    f"<td class='num'>{pct(o['p90_rel'])}</td><td class='num'>{pct(o['max_rel'], 1)}</td><td>无量纲</td><td class='num'>{pct(r['median_rel'], 3)}</td>"
                                    f"<td class='num'>{pct(r['p90_rel'])}</td><td class='num'>{pct(r['max_rel'], 1)}</td></tr>")
    return "".join(rows)


def srf_rows() -> str:
    rows = []
    for stratum in STRATA:
        fw, knn = V[stratum]["forward"], V[stratum]["srf_knn"]
        for q in ("SRF", "SRF_p", "SRF_s"):
            if f"{q}|library" in fw and q in knn:
                r = fw[f"{q}|library"]
                rows.append(f"<tr><td>{stratum}</td><td><code>{q}</code></td><td class='num'>{r['n']}</td><td class='num'>{pct(r['median_rel'])}</td><td class='num'>{pct(r['p90_rel'])}</td>"
                            f"<td class='num'>{pct(r['max_rel'], 1)}</td><td class='num'>{pct(knn[q]['knn5_median_rel'], 1)}</td><td class='num'>{pct(knn[q]['knn5_p90_rel'], 1)}</td></tr>")
    return "".join(rows)


def integrity_rows() -> str:
    rows = []
    for stratum in STRATA:
        c = V[stratum]["integrity"]
        grids = "; ".join(f"{g['rows']} 行 {g['n_freq']} 点至 {g['stop_ghz']:g} GHz" for g in c["grids"])
        rows.append(f"<tr><td>{stratum}</td><td class='num'>{c['rows']}</td><td class='num'>{c['duplicate_coordinates']}</td><td class='num'>{c['passive_rows']}/{c['rows']}</td>"
                    f"<td class='num'>{c['stored_reproduced']}/{c['rows']}</td><td class='num'>{len(c['generations'])}</td><td>{grids}</td></tr>")
    return "".join(rows)


GUARD_NAMES = {"library points": "库点", "midpoints of nearest measured pairs": "最近实测点对的中点",
               "outside the box (one dim 10 % beyond)": "越界（任一维超出已测范围 10%）", "non-integer turns": "非整数匝数"}


def guard_rows() -> str:
    rows = []
    for stratum in STRATA:
        for name, t in V[stratum]["guard"].items():
            rejects = ", ".join(f"{k.split(':')[1]}×{v}" for k, v in t.items() if k.startswith("reject"))
            want = "接受" if name.startswith(("library", "midpoints")) else "拒绝"
            rows.append(f"<tr><td>{stratum}</td><td>{html.escape(GUARD_NAMES.get(name, name))}</td><td>{want}</td>"
                        f"<td class='num'>{t['accept']}/{t['n']} 接受</td><td>{rejects or '–'}</td></tr>")
    return "".join(rows)


def inverse_rows() -> str:
    rows = []
    for stratum in STRATA:
        for mode, r in V[stratum]["inverse"].items():
            rows.append(f"<tr><td>{stratum}</td><td>{'2σ 保守' if mode == 'conservative' else '只看均值'}</td><td class='num'>{r['answered']}/{r['queries']}</td>"
                        f"<td class='num'>{pct(r['first_hit_rate'], 1)}</td><td class='num'>{pct(r['top3_precision'], 1)}</td><td class='num'>{pct(r['median_k_regret'], 2)}</td></tr>")
    return "".join(rows)


def value(q: str, v: float) -> str:
    if q.startswith("L"):
        return f"{v * 1e9:.4g} nH"
    if q.startswith("SRF"):
        return f"{v / 1e9:.4g} GHz"
    return f"{v:.4g}"


def examples_html() -> str:
    parts = []
    for stratum in STRATA:
        for ex in V[stratum].get("examples", []):
            if ex.get("error"):
                parts.append(f"<p><b>{stratum}：{html.escape(ex['name'])}</b> — {html.escape(ex['error'])}</p>")
                continue
            notes = "；".join(html.escape(n) for n in ex["notes"])
            head = (f"<h3>{stratum}：{html.escape(ex['name'])}</h3><p class='note'>候选池 {ex['pool']}，满足 {ex['satisfying']}（其中实测 {ex['satisfying_measured']}）"
                    + (f"；{notes}" if notes else "") + "</p>")
            rows = []
            for kind, items in (("实测", ex["measured"][:2]), ("预测", ex["candidates"])):
                for c in items:
                    geo = ", ".join(f"{k.replace('_um', '').replace('primary_', 'P.').replace('secondary_', 'S.')}={v:g}" for k, v in c["params"].items())
                    pred = "；".join(f"{q} {value(q, p['value'])} [{value(q, p['lo'])}, {value(q, p['hi'])}]" for q, p in c["predicted"].items())
                    built = "✓ 实造+审计通过" if c.get("build", {}).get("built") else ("实测点" if kind == "实测" else "✗")
                    rows.append(f"<tr><td>{kind}</td><td class='small'>{html.escape(geo)}</td><td class='small'>{html.escape(pred)}</td><td class='small'>{built}</td></tr>")
            parts.append(head + "<div class='table-wrap'><table><thead><tr><th>来源</th><th>几何（µm）</th><th>值 [2σ 区间]</th><th>实造</th></tr></thead><tbody>"
                         + "".join(rows) + "</tbody></table></div>")
    return "".join(parts)


def library_forward(q: str, key: str) -> list[float]:
    return [V[s]["forward"][f"{q}|library"][key] for s in STRATA if f"{q}|library" in V[s]["forward"]]


jump = "data:image/png;base64," + base64.b64encode(JUMP_FIG.read_bytes()).decode() if JUMP_FIG.is_file() else ""
rows_total = sum(V[s]["integrity"]["rows"] for s in STRATA)
cols = [q for s in STRATA for q in [k.split("|")[0] for k in V[s]["forward"] if k.endswith("|library")]]
med_all = [V[s]["forward"][k]["median_rel"] for s in STRATA for k in V[s]["forward"] if k.endswith("|library")]
cov_cal = [V[s]["forward"][k]["coverage_calibrated_seeds_3_4"] for s in STRATA for k in V[s]["forward"] if k.endswith("|library")]
first_hit = [V[s]["inverse"]["conservative"]["first_hit_rate"] for s in STRATA]
page = f"""<title>N28 变压器查询库验证</title>
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
h3 {{ font-size:16px; margin:22px 0 6px; }}
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
.small {{ font-size:12px; }}
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
  <div class="eyebrow">ic-opt-modular · T13.7 · N28 · 变压器 {len(STRATA)} 个分层 · {rows_total} 点</div>
  <h1>N28 变压器查询库验证</h1>
  <p class="note">用嵌入后的查询模块（<code>ic_opt.library</code>）在新建的变压器库上按电感验证的同一流程逐项检验：数据完整性、正向预测（含 k 的无量纲映射重验）、SRF、域守卫、逆向推荐、真实查询示例。分层：{", ".join(STRATA)}。</p>
</header>
<div class="kpis">
  <div class="kpi"><div class="v">{pct(min(med_all), 3)}–{pct(max(med_all), 2)}</div><div class="l">{len(cols)} 个查询列的留出中位误差</div></div>
  <div class="kpi"><div class="v">{pct(min(library_forward('k_lf', 'median_rel')), 3)}</div><div class="l">k_lf 留出中位误差（无量纲映射）</div></div>
  <div class="kpi"><div class="v">{pct(min(cov_cal), 0)}–{pct(max(cov_cal), 0)}</div><div class="l">校准后 2σ 覆盖（跨组，名义 95%）</div></div>
  <div class="kpi"><div class="v">{pct(min(first_hit), 0)}</div><div class="l">逆向推荐首选命中（留出真值）</div></div>
</div>

<h2>结论</h2>
<div class="finding ok"><b>可用。</b>所有查询列的留出中位误差 {pct(min(med_all), 3)}–{pct(max(med_all), 2)}；用前 3 组留出定的校准系数在另外 2 组上的 2σ 覆盖 {pct(min(cov_cal), 1)}–{pct(max(cov_cal), 1)}。逆向推荐在留出真值上首选全部命中，推荐出的几何都能实造、通过产品 DRC 审计。</div>
<div class="finding"><b>已修：变压器的 SRF 改用系统 SRF。</b>SRF_p（初级 Im(Z) 的第一个零点，次级开路）会在次级反射过来的谐振与初级自身谐振之间跳变：反射下陷是否越过零只差约 20 Ω，几何几乎相同的两个点 SRF_p 可差 60 GHz（见第 4 节图）。测量内核新增标量 <code>SRF</code>（各驱动中最低的谐振；电感即 SRF_p，电感库复核不变），耦合对的锚定曲线一律按它截断，推荐隐含的 SRF 下限也用它（5ab7add）。</div>
<div class="finding ok"><b>k 的无量纲映射在新库上重验通过。</b>以平均外径（对数）、外径比、线宽/外径、中心偏移/平均半径为特征，k_lf 与 k@10/28/60 的中位误差和 p90 都约降到恒等映射的一半；最大误差两者相当，都出在参数盒边角那几行（第 3 节）。库清单里 k 列都用它。</div>
<div class="finding"><b>留意：60 GHz 锚定的 Lp / Ls 覆盖略低。</b>这两列只有谐振离 60 GHz 足够远的行（约三分之二）可用，跨组覆盖约 92%，低于名义 95%；中位误差仍在 0.25% 以内。</div>

<h2>1 数据完整性</h2>
<div class="table-wrap"><table><thead><tr><th>分层</th><th class="num">行</th><th class="num">重复坐标</th><th class="num">无源</th><th class="num">存储值复现</th><th class="num">代际</th><th>频率网格</th></tr></thead>
<tbody>{integrity_rows()}</tbody></table></div>

<h2>2 正向预测（留出交叉验证）</h2>
<p>5 个种子 × 20% 留出，用库自己的模型设置（Matern 5/2，对数目标，k 列用无量纲映射）。"校准系数"由种子 0–2 的留出 z 分位数得出，"跨组覆盖"是它放宽后的 2σ 区间在种子 3–4 上的实际覆盖，即样本外的校准效果。</p>
{"".join(f"<h3>{s}</h3><div class='table-wrap'><table><thead><tr><th>查询列</th><th class='num'>行</th><th class='num'>中位</th><th class='num'>p90</th><th class='num'>最大</th><th class='num'>2σ 覆盖</th><th class='num'>校准系数</th><th class='num'>跨组覆盖</th></tr></thead><tbody>{forward_rows(s)}</tbody></table></div>" for s in STRATA)}
<figure><img src="{parity()}" alt="held-out parity"><figcaption>留出预测对实测（种子 0，库的模型设置）；灰线是 y = x。</figcaption></figure>

<h2>3 k 的无量纲映射（新词汇重验）</h2>
<div class="table-wrap"><table><thead><tr><th>分层</th><th>查询列</th><th>对照</th><th class="num">中位</th><th class="num">p90</th><th class="num">最大</th><th>映射</th><th class="num">中位</th><th class="num">p90</th><th class="num">最大</th></tr></thead>
<tbody>{kmap_rows()}</tbody></table></div>
<p class="note">同一数据、同一留出划分。最大误差集中在参数盒边角（外径最小、偏移比最大，k 随偏移陡降）。</p>

<h2>4 SRF</h2>
<div class="table-wrap"><table><thead><tr><th>分层</th><th>量</th><th class="num">行（有谐振）</th><th class="num">GP 中位</th><th class="num">GP p90</th><th class="num">GP 最大</th><th class="num">5 近邻均值 中位</th><th class="num">5 近邻 p90</th></tr></thead>
<tbody>{srf_rows()}</tbody></table></div>
{f'<figure><img src="{jump}" alt="SRF_p jump"><figcaption>两个相邻几何（只差次级线宽 6 / 8 µm）：初级 Im(Z) 在次级谐振处的反射下陷一个未越零（SRF_p = 161.8 GHz）、一个越零（SRF_p = 101.3 GHz）；系统 SRF 两者都约 99 GHz。上：真实 GDS 渲染；下：阻抗曲线与放大。</figcaption></figure>' if jump else ''}

<h2>5 域守卫</h2>
<div class="table-wrap"><table><thead><tr><th>分层</th><th>测试点</th><th>应当</th><th class="num">结果</th><th>拒绝判据</th></tr></thead>
<tbody>{guard_rows()}</tbody></table></div>
<p class="note">c1 = 超出已测范围盒，c2 = 匝数层不可用，c3 = 在凸包外。</p>

<h2>6 逆向推荐（离线真值检验）</h2>
<p>每个种子在 80% 上训练，把留出的 20%（真值已知）当候选池；从留出里任取一个设计的 Lp_lf、Ls_lf 当目标（±5%），最大化 k_lf，每种子 40 个查询。看首选是否真的落在两个窗口内、前 3 的命中比例、和留出里真正最好设计相比 k 的损失。</p>
<div class="table-wrap"><table><thead><tr><th>分层</th><th>约束方式</th><th class="num">有答案/查询</th><th class="num">首选命中</th><th class="num">前 3 命中</th><th class="num">k 损失中位</th></tr></thead>
<tbody>{inverse_rows()}</tbody></table></div>

<h2>7 真实查询示例（全库模型）</h2>
{examples_html()}
<p class="note">预测候选不是库里的点，结论要靠真实 EMX 复核（T13.6，经批准再跑）。</p>

<h2>8 方法与位置</h2>
<p class="note">验证：<code>docs/refactor/reports/library_query/xfm_query_verify.py</code>（BLAS 线程限 4、降优先级，与正式批并行，未占 EMX 的 128 线程额度）；SRF 跳变图：<code>xfm_srf_jump_fig.py</code>；本页：<code>xfm_query_report.py</code>。库：<code>/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28/</code>。</p>
</div>
"""
out.write_text(page, encoding="utf-8")
print(out, f"{out.stat().st_size / 1e6:.2f} MB")
