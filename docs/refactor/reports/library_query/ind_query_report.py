"""The inductor query library verification as a review page (reads ind_dataset.py + ind_query_verify.py outputs).

Usage: ind_query_report.py DATASET_JSON VERIFY_JSON OUT_HTML
Figures: held-out parity (seed 0, the default per_nt-matern52 model) for L_lf, Q_peak150, SRF and L@28 on both bodies.
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

sys.path.insert(0, str(Path(__file__).parent))
from ind_query_verify import DIMS, RANGES, StratumGP, fitted_levels, matrix, split, usable

warnings.filterwarnings("ignore", module="sklearn")
dataset_path, verify_path, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
data = json.loads(dataset_path.read_text())
rows, checks = data["rows"], data["checks"]
V = json.loads(verify_path.read_text())
BODIES = ("AP", "M10")
COLORS = {1: "#6f4e37", 2: "#b5651d", 3: "#2e6f95", 4: "#4f8f55", 5: "#8a3d7f"}
PANELS = (("L_lf", "L_lf (nH)", 1e9), ("Q_peak150", "Q_peak (0-150 GHz)", 1.0), ("SRF", "SRF (GHz)", 1e-9), ("L@28", "L @ 28 GHz (nH)", 1e9))


def png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=105)
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def parity() -> str:
    fig, axes = plt.subplots(2, 4, figsize=(13, 6.4))
    for i, body in enumerate(BODIES):
        rs = [r for r in rows if r["body"] == body]
        for j, (target, label, scale) in enumerate(PANELS):
            ax = axes[i][j]
            pool = [r for r in rs if r["SRF"] is not None] if target == "SRF" else usable(rs, target)
            test, train = split(len(pool), 0)
            tr, te = [pool[k] for k in train], [pool[k] for k in test]
            gp = StratumGP(dims=DIMS, ranges=RANGES, log_target=True, nt_mode="per_nt", kernel="matern52", nt_dim="turns")
            gp.fit(matrix(tr), np.array([r[target] for r in tr]))
            te = [r for r, a in zip(te, fitted_levels(gp, matrix(te))) if a]
            mu, _ = gp.predict(matrix(te))
            y = np.array([r[target] for r in te])
            for nt in sorted({r["nt"] for r in te}):
                m = np.array([r["nt"] == nt for r in te])
                ax.scatter(y[m] * scale, mu[m] * scale, s=9, color=COLORS[nt], alpha=0.75, label=f"NT={nt}", edgecolors="none")
            lo, hi = min(y.min(), mu.min()) * scale, max(y.max(), mu.max()) * scale
            ax.plot([lo, hi], [lo, hi], color="#888", lw=0.8)
            ax.set_xscale("log")
            ax.set_yscale("log")
            ax.grid(alpha=0.3)
            rel = np.abs(mu / y - 1)
            ax.set_title(f"{body}  {label}\nheld-out n={len(y)}, median {100 * np.median(rel):.2f}%", fontsize=8.5)
            if j == 0:
                ax.set_ylabel("predicted")
            if i == 1:
                ax.set_xlabel("measured (EMX)")
    axes[0][0].legend(fontsize=7, loc="upper left")
    fig.tight_layout()
    return png(fig)


def pct(v: float | None, digits: int = 2) -> str:
    return "–" if v is None else f"{100 * v:.{digits}f}%"


def forward_table() -> str:
    names = {"L_lf": "L_lf（≤3 GHz 均值）", "L_res": "L_res（≤SRF/5）", "Q_peak150": "Q 峰值（0–150 GHz）", "L@28": "L @ 28 GHz",
             "Q@28": "Q @ 28 GHz", "L@60": "L @ 60 GHz", "Q@60": "Q @ 60 GHz"}
    out = []
    for q, label in names.items():
        cells = []
        for body in BODIES:
            d = V[f"forward_{body}"][f"{q}|per_nt-matern52"]
            j = V[f"forward_{body}"][f"{q}|joint-matern52"]
            cells.append(f"<td class='num'>{d['n']}</td><td class='num'>{pct(d['median_rel'], 3)}</td><td class='num'>{pct(d['p90_rel'])}</td>"
                         f"<td class='num'>{pct(d['max_rel'], 1)}</td><td class='num'>{pct(d['coverage_2sigma'], 1)}</td><td class='num dim'>{pct(j['max_rel'], 1)}</td>")
        out.append(f"<tr><td>{label}</td>{''.join(cells)}</tr>")
    return "".join(out)


def guard_table() -> str:
    cases = ["library points", "cell midpoints (4 measured corners)", "outside the box", "unbuildable (inner < 25 um)", "non-integer turns"]
    names = {"library points": "库里的点本身", "cell midpoints (4 measured corners)": "网格单元中点（四角都有实测）", "outside the box": "超出覆盖范围",
             "unbuildable (inner < 25 um)": "造不出来（内径 < 25 µm）", "non-integer turns": "非整数匝数"}
    expect = {"library points": "全接受", "cell midpoints (4 measured corners)": "全接受", "outside the box": "全拒绝",
              "unbuildable (inner < 25 um)": "全拒绝", "non-integer turns": "拒绝"}

    def fmt(v: dict) -> str:
        acc = v.get("accept", 0)
        rej = ", ".join(f"{k.split(':')[1]}×{n}" for k, n in v.items() if k.startswith("reject"))
        return f"{acc}/{v['n']} 接受" + (f"（拒绝 {rej}）" if rej else "")
    out = []
    for c in cases:
        cells = "".join(f"<td>{fmt(V[f'guard_{b}'][f'{c}|shipped'])}</td><td>{fmt(V[f'guard_{b}'][f'{c}|per-level dims'])}</td>" for b in BODIES)
        out.append(f"<tr><td>{names[c]}</td><td>{expect[c]}</td>{cells}</tr>")
    return "".join(out)


def inverse_table() -> str:
    out = []
    for body in BODIES:
        for mode, label in (("conservative", "2σ 保守"), ("mean only", "只看均值")):
            v = V[f"inverse_{body}"][mode]
            out.append(f"<tr><td>{body}</td><td>{label}</td><td class='num'>{v['answered']}/{v['queries']}</td><td class='num'>{pct(v['first_hit_rate'], 1)}</td>"
                       f"<td class='num'>{pct(v['top3_precision'], 1)}</td><td class='num'>{pct(v['median_regret'], 1)}</td><td class='num'>{pct(v['max_first_L_err'], 2)}</td></tr>")
    return "".join(out)


def examples_html() -> str:
    blocks = []
    for ex in V["examples"]:
        target = ex["window"][0]
        obj = ex["objective"]
        unit = (lambda v: f"{v * 1e9:.3f} nH") if target.startswith("L") else (lambda v: f"{v:.2f}")
        rows_html = []
        for k, p in enumerate(ex["picks"], 1):
            g = p["params"]
            lmu, llo, lhi = p["pred"][target]
            qmu, qlo = p["pred"][obj]
            b = p["build"]
            build = "实造通过 · 设计规则 0 违例 · 端口连通正常" if b.get("built") and not b.get("drc_violations") else html.escape(str(b))
            near = "<br>".join(f"OD {n['od']:g} W {n['w']:g} S {n['s']:g} NT {n['nt']}：{target} {unit(n[target]) if n.get(target) else '–'}，"
                               f"{obj} {n[obj]:.2f}，SRF {n['SRF_GHz']:.1f} GHz" if n.get("SRF_GHz") else "" for n in p["nearest_measured"])
            rows_html.append(f"<tr><td class='num'>{k}</td><td class='num'>OD {g['outer_diameter_um']:g} · W {g['width_um']:g} · S {g['spacing_um']:g} · NT {g['turns']}</td>"
                             f"<td class='num'>{unit(lmu)}<br><span class='dim'>2σ {unit(llo)}–{unit(lhi)}</span></td><td class='num'>{qmu:.2f}<br><span class='dim'>2σ 下界 {qlo:.2f}</span></td>"
                             f"<td>{build}</td><td class='small'>{near}</td></tr>")
        blocks.append(f"<h3>{html.escape(ex['body'])}：{html.escape(ex['name'])}</h3><p class='note'>候选池 {ex['pool']} 个 Sobol 点 → 域守卫内 {ex['in_domain']} → 满足 2σ 约束 {ex['satisfy']} → 按目标下界排序并去重，取前 3。</p>"
                      f"<div class='table-wrap'><table><thead><tr><th>#</th><th>几何</th><th>{html.escape(target)} 预测</th><th>{html.escape(obj)} 预测</th><th>实造检查</th><th>最近的 3 个实测点</th></tr></thead><tbody>{''.join(rows_html)}</tbody></table></div>")
    return "".join(blocks)


def srf_row(body: str) -> str:
    s = V[f"srf_{body}"]
    return (f"<tr><td>{body}</td><td class='num'>{s['n']}</td><td class='num'>{pct(s['gp_median_rel'])}</td><td class='num'>{pct(s['gp_p90_rel'])}</td><td class='num'>{pct(s['gp_coverage_2sigma'], 1)}</td>"
            f"<td class='num'>{pct(s['knn5_mean_median_rel'], 1)}</td><td class='num'>{pct(s['knn5_mean_p90_rel'], 1)}</td><td class='num'>{pct(s['knn5_brackets_truth'], 1)}</td></tr>")


med = {q: [V[f"forward_{b}"][f"{q}|per_nt-matern52"]["median_rel"] for b in BODIES] for q in ("L_lf", "Q_peak150")}
cov = [V[f"forward_{b}"][f"{q}|per_nt-matern52"]["coverage_2sigma"] for b in BODIES for q in ("L_lf", "L_res", "Q_peak150", "L@28", "Q@28", "L@60", "Q@60")]
page = f"""<title>N28 电感查询库验证</title>
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
.dim {{ color:var(--muted); }}
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
  <div class="eyebrow">ic-opt-modular · T13.0 · N28 · 电感库 1038 点</div>
  <h1>N28 电感查询库验证</h1>
  <p class="note">用现有查询内核（em-opt 的 StratumGP 与 DomainGuard，只读导入，不改原工程）在新建的电感库上逐项验证：数据完整性、正向预测、SRF、域守卫、逆向推荐、真实查询示例。真实 EMX 复核留待变压器库跑完、经批准后进行。</p>
</header>
<div class="kpis">
  <div class="kpi"><div class="v">{pct(min(med['L_lf']), 3)}–{pct(max(med['L_lf']), 3)}</div><div class="l">L_lf 留出预测中位误差</div></div>
  <div class="kpi"><div class="v">{pct(min(med['Q_peak150']), 2)}–{pct(max(med['Q_peak150']), 2)}</div><div class="l">Q 峰值留出预测中位误差</div></div>
  <div class="kpi"><div class="v">{pct(min(cov), 0)}–{pct(max(cov), 0)}</div><div class="l">2σ 区间实际覆盖（名义 95%）</div></div>
  <div class="kpi"><div class="v">{pct(V['inverse_AP']['conservative']['first_hit_rate'], 0)}</div><div class="l">逆向推荐首选命中（留出真值）</div></div>
</div>

<h2>结论</h2>
<div class="finding ok"><b>可用。</b>正向预测误差中位 0.03–0.23%、90% 分位 &lt;1%，比旧库基准（1.8%）好一个量级：网格建库加细网格全波，响应面平滑。逆向推荐在留出真值上首选全部命中目标窗口，推荐出的几何都能实造、0 违例。</div>
<div class="finding"><b>必须修：域守卫把所有单圈查询都拒掉了。</b>单圈层线距只有一个值（单圈时线距不改变几何），em-opt 的凸包判据在该层退化，按"退化即拒绝"的设计拒掉了全部 56 个单圈库点和 42 个单圈中点。改为"每层只用在该层有变化的维度建凸包"后，库点与中点全接受，越界、造不出来、非整数匝数仍全部拒绝。</div>
<div class="finding"><b>必须修：锚定频率下数据稀疏的层会让预测直接崩溃。</b>锚定到 f0 时要剔除 SRF ≤ 1.25 f0 的训练点，高频锚定下多匝层可能不足 25 点而不拟合，em-opt 的预测对这种层抛异常（本次验证在 28 GHz 锚定时触发）。嵌入模块应把它当域守卫判据 2（该层数据不足）返回，而不是异常。</div>
<div class="finding"><b>建议换：SRF 用 GP 预测。</b>em-opt 用"5 个最近实测点"判 SRF：均值误差中位 8–9%、90% 分位 23–27%。对 log SRF 拟合 GP：中位 0.2–0.3%、90% 分位约 2%，2σ 覆盖 92–94%。</div>
<div class="finding"><b>建议加：σ 校准。</b>2σ 区间实际覆盖 86–96%，多数低于名义 95%（旧库也是 91.9%），GP 的不确定度略偏乐观。可用留出残差的 z 分位数把 k 放大到覆盖 95%。</div>

<h2>1 数据完整性</h2>
<div class="table-wrap"><table><tbody>
<tr><td>行数 / 重复坐标</td><td class="num">{checks['rows']} / {checks['duplicate_coordinates']}</td></tr>
<tr><td>从 sNp 重算 L_lf、SRF 与入库值逐位一致</td><td class="num">{checks['stored_quantities_reproduced']}/{checks['rows']}</td></tr>
<tr><td>S 参数无源（最大奇异值 ≤ 1 + 1e-6）</td><td class="num">{checks['passive_rows']}/{checks['rows']}（最大 {checks['max_singular_value']:.8f}）</td></tr>
<tr><td>频率网格 0–150 GHz / 0–250 GHz（单圈）</td><td class="num">{checks['grid_150']} / {checks['grid_250']}</td></tr>
<tr><td>SRF 落在扫频范围内</td><td class="num">{checks['srf_in_band']}/{checks['rows']}</td></tr>
<tr><td>Q 峰值（0–150 GHz）落在带边、仍在上升</td><td class="num">{checks['qpeak150_at_band_edge']}</td></tr>
</tbody></table></div>
<p class="note">口径统一：单圈工程扫到 250 GHz、其余 150 GHz，入库的 Q_peak 是各自全频段最大值，口径不一；查询库按 0–150 GHz 统一重算 Q 峰值（Q_peak150）。锚定量 L/Q @ 10/28/60 GHz 由 sNp 现算。</p>

<h2>2 正向预测（留出交叉验证）</h2>
<p>5 个随机种子 × 20% 留出，每种子在其余 80% 上拟合、在留出点上打分；默认模型 per_nt-matern52（em-opt 旧库基准胜者）。锚定量只用 SRF &gt; 1.25 f0 的行（em-opt 语义），所以行数少。</p>
<div class="table-wrap"><table><thead><tr><th rowspan="2">查询量</th><th colspan="6">AP 体</th><th colspan="6">M10 体</th></tr>
<tr><th class="num">行</th><th class="num">中位</th><th class="num">p90</th><th class="num">最大</th><th class="num">2σ 覆盖</th><th class="num">joint 最大</th><th class="num">行</th><th class="num">中位</th><th class="num">p90</th><th class="num">最大</th><th class="num">2σ 覆盖</th><th class="num">joint 最大</th></tr></thead>
<tbody>{forward_table()}</tbody></table></div>
<p class="note">per_nt 与 joint 的中位误差相当；joint 在锚定量上的最大误差最高到 45%（跨匝数外推），per_nt 为 1.7–30%。保持 per_nt 为默认。最大误差集中在 SRF 刚过 1.25 f0 的点：Q 在谐振附近变化剧烈。</p>
<figure><img src="{parity()}" alt="held-out parity"><figcaption>留出预测对实测（种子 0，per_nt-matern52），按匝数着色；灰线是 y = x。</figcaption></figure>

<h2>3 SRF</h2>
<div class="table-wrap"><table><thead><tr><th>体材</th><th class="num">行（有谐振）</th><th class="num">GP 中位</th><th class="num">GP p90</th><th class="num">GP 2σ 覆盖</th><th class="num">5 近邻均值 中位</th><th class="num">5 近邻 p90</th><th class="num">5 近邻包住真值</th></tr></thead>
<tbody>{srf_row('AP')}{srf_row('M10')}</tbody></table></div>
<p class="note">em-opt 的 suggest 用"5 个最近实测点都满足"来判 SRF 约束（保守但粗）；GP 在同一留出集上精确一个量级以上。扫频内无谐振的行（{checks['rows'] - checks['srf_in_band']} 行）不进 GP，嵌入时对这类区域返回"SRF 高于扫频上限"。</p>

<h2>4 域守卫</h2>
<div class="table-wrap"><table><thead><tr><th rowspan="2">测试点</th><th rowspan="2">应当</th><th colspan="2">AP 体</th><th colspan="2">M10 体</th></tr><tr><th>em-opt 原版</th><th>按层修正</th><th>em-opt 原版</th><th>按层修正</th></tr></thead>
<tbody>{guard_table()}</tbody></table></div>
<p class="note">c1 = 超出已测范围盒，c2 = 匝数层不可用，c3 = 在凸包外（或凸包退化）。原版拒掉的库点、中点全部是单圈层（凸包退化）。</p>

<h2>5 逆向推荐（离线真值检验）</h2>
<p>每个种子在 80% 上训练，把留出的 20%（真值已知）当候选池；目标 L_lf = L0 ± 5%（L0 在 0.15–8 nH 间取 25 档），最大化 Q 峰值。看推荐的第一个设计是否真的落在窗口内、前 3 个的命中比例、以及和"留出里真正最好的设计"相比 Q 损失多少。</p>
<div class="table-wrap"><table><thead><tr><th>体材</th><th>约束方式</th><th class="num">有答案/查询</th><th class="num">首选命中</th><th class="num">前 3 命中</th><th class="num">Q 损失中位</th><th class="num">首选 L 最大偏差</th></tr></thead>
<tbody>{inverse_table()}</tbody></table></div>
<p class="note">2σ 保守约束让前 3 命中从 98.9–99.2% 升到 99.5–100%，代价是少数查询（2–3 个）如实回答"没有满足 2σ 窗口的候选"。</p>

<h2>6 真实查询示例（全库模型）</h2>
{examples_html()}
<p class="note">这些候选都不是库里的点，预测需要真实 EMX 复核才能下结论。复核放在方案的 T13.6（变压器库跑完后，经批准再跑，约 10 点）。</p>

<h2>7 方法与位置</h2>
<p class="note">数据集：<code>docs/refactor/reports/library_query/ind_dataset.py</code>；验证：<code>ind_query_verify.py</code>（em-opt 内核只读导入；GP 的 BLAS 线程限 4、降优先级，与变压器正式批并行，未占用 EMX 的 128 线程额度）；本页：<code>ind_query_report.py</code>。库：<code>/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28/</code>。</p>
</div>
"""
out.write_text(page, encoding="utf-8")
print(out, f"{out.stat().st_size / 1e6:.2f} MB")
