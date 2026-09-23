"""The xfm_bs / xfm_ms library proposal page: parameter ranges, grid coverage, EMX settings, pilot and decisions.

Usage: xfm_proposal.py GRID_JSON IND_ROWS_JSON OUT_HTML
GRID_JSON comes from xfm_grid.py; IND_ROWS_JSON is the N28 inductor library's library_rows.json (cost model).
"""
from __future__ import annotations

import base64
import collections
import html
import io
import json
import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).parent
grid_path, ind_path, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
grid = json.loads(grid_path.read_text())["families"]
ind = [r for r in json.loads(ind_path.read_text()) if r["wall_s"]]
OK = {f: [p for p in grid[f]["points"] if p["status"] == "ok"] for f in ("bs", "ms")}
BS_STOP, MS_STOP, JOBS = 200, 150, 16


def png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def wall_model() -> dict[tuple[int, float], float]:
    """Median wall per (turns, OD) from the inductor library, rescaled to one frequency point (NT=1 ran 251 points, the rest 151)."""
    by = collections.defaultdict(list)
    for r in ind:
        by[(r["turns"], r["od"])].append(r["wall_s"] / (251 if r["turns"] == 1 else 151))
    return {k: statistics.median(v) for k, v in by.items()}


WALL = wall_model()


def wall_at(nt: int, od: float) -> float:
    keys = [k for k in WALL if k[0] == nt] or [k for k in WALL if k[0] == 5]
    k = min(keys, key=lambda k: abs(k[1] - od))
    return WALL[k] * od / k[1] if od > k[1] else WALL[k]


def est_wall(f: str, p: dict) -> float:
    """Rough per-point wall (s) at 8 threads: the two windings as separate inductor runs, summed; the pilot replaces this."""
    if f == "bs":
        return (wall_at(1, p["od_p"]) + wall_at(1, p["od_s"])) * (BS_STOP + 1)
    return (wall_at(1, p["od_p"]) + wall_at(p["nt_s"], p["od_s"])) * (MS_STOP + 1)


cost = {f: sum(est_wall(f, p) for p in OK[f]) for f in ("bs", "ms")}


def od_map(f: str) -> str:
    fig, axes = plt.subplots(1, 1, figsize=(5.4, 4.6))
    c = collections.Counter((p["od_p"], p["od_s"]) for p in OK[f] if p["pair"] == "ap" and p["offset"] == 0)
    xs, ys, ss = zip(*[(a, b, n) for (a, b), n in c.items()])
    axes.scatter(xs, ys, s=[6 + 1.4 * n for n in ss], color="#2e6f95", alpha=0.6, edgecolors="none")
    for r, ls in ((0.5, ":"), (1.0, "-"), (2.0, ":")):
        axes.plot([60, 240], [60 * r, 240 * r], color="#999", lw=0.9, ls=ls)
    axes.set_xlim(50, 250)
    axes.set_ylim(40, 260)
    axes.set_xlabel("OD primary (um)")
    axes.set_ylabel("OD secondary (um)")
    axes.set_title(f"xfm_{f}, one metal pair, concentric; marker ~ points per cell", fontsize=9)
    axes.grid(alpha=0.3)
    fig.tight_layout()
    return png(fig)


def k_hist() -> str:
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.4), sharey=False)
    for ax, f in zip(axes, ("bs", "ms")):
        conc = [p["k_est"] for p in OK[f] if p["k_est"] is not None and p["offset"] == 0]
        off = [p["k_est"] for p in OK[f] if p["k_est"] is not None and p["offset"] > 0]
        ax.hist([conc, off], bins=[i / 40 for i in range(4, 32)], stacked=True, color=["#2e6f95", "#b5651d"], label=["concentric", "lateral offset"])
        ax.set_title(f"xfm_{f}: estimated k_lf (old library, nearest neighbours)", fontsize=9)
        ax.set_xlabel("k_lf")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    axes[0].set_ylabel("points")
    fig.tight_layout()
    return png(fig)


def l_scatter() -> str:
    fig, ax = plt.subplots(figsize=(5.6, 4.4))
    for f, color in (("bs", "#2e6f95"), ("ms", "#8a3d7f")):
        v = [p for p in OK[f] if p["pair"] == "ap"]
        ax.scatter([p["Lp_est_nH"] for p in v], [p["Ls_est_nH"] for p in v], s=6, alpha=0.35, color=color, label=f"xfm_{f}", edgecolors="none")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("estimated Lp (nH)")
    ax.set_ylabel("estimated Ls (nH)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    return png(fig)


def img(name: str) -> str:
    return "data:image/png;base64," + base64.b64encode((HERE / "xfm" / f"{name}.png").read_bytes()).decode()


def rng(f: str, key: str) -> str:
    v = sorted(p[key] for p in OK[f] if p.get(key) is not None)
    return f"{v[0]:.3f}–{v[-1]:.2f}（中位 {v[len(v) // 2]:.2f}）"


def tally(f: str) -> tuple[int, int, int, int]:
    pts = grid[f]["points"]
    return (len(pts), sum(p["status"] == "skipped:inner" for p in pts), sum(p["status"] == "refused" for p in pts), len(OK[f]))


def reasons(f: str) -> str:
    return "".join(f"<li><span class='num'>{n}</span> {html.escape(r)}</li>" for r, n in grid[f]["refusal_reasons"][:4])


def by_nt() -> str:
    c = collections.Counter(p["nt_s"] for p in OK["ms"])
    return "、".join(f"NT={k}: {c[k]}" for k in sorted(c))


def kbins(f: str) -> str:
    edges = ((0, 0.2), (0.2, 0.3), (0.3, 0.5), (0.5, 0.6), (0.6, 1))
    v = [p["k_est"] for p in OK[f] if p["k_est"] is not None]
    return "".join(f"<td class='num'>{sum(lo <= k < hi for k in v)}</td>" for lo, hi in edges)


bs_t, ms_t = tally("bs"), tally("ms")
n_bs, n_ms = bs_t[3], ms_t[3]
off_bs = sum(p["offset"] > 0 for p in OK["bs"])
off_ms = sum(p["offset"] > 0 for p in OK["ms"])
hours = lambda s, jobs: s / jobs / 3600

page = f"""<title>N28 变压器建库提案</title>
<style>
:root {{ --ground:#f2f3f1; --surface:#fff; --surface-2:#e6e9e6; --ink:#17201c; --muted:#56615b; --line:#cfd6d1; --accent:#2e6f95; --warn:#9a5b12; --code-bg:#e9ece9;
  --sans:"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif; --mono:"IBM Plex Mono",Menlo,Consolas,monospace; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --ground:#121614; --surface:#1a201d; --surface-2:#232a26; --ink:#e3e9e5; --muted:#9aa69f; --line:#333c37; --accent:#7fb3d3; --warn:#e0a45a; --code-bg:#222925; }} }}
:root[data-theme="dark"] {{ --ground:#121614; --surface:#1a201d; --surface-2:#232a26; --ink:#e3e9e5; --muted:#9aa69f; --line:#333c37; --accent:#7fb3d3; --warn:#e0a45a; --code-bg:#222925; }}
body {{ background:var(--ground); color:var(--ink); font-family:var(--sans); font-size:15px; line-height:1.7; margin:0; padding-block:0 64px; padding-inline:16px; }}
.wrap {{ max-width:1040px; margin:0 auto; }}
header {{ padding-block:36px 18px; border-bottom:2px solid var(--accent); }}
.eyebrow {{ font-family:var(--mono); font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }}
h1 {{ font-size:31px; margin:8px 0 10px; text-wrap:balance; }}
h2 {{ font-size:21px; margin:38px 0 10px; text-wrap:balance; }}
h3 {{ font-size:16.5px; margin:22px 0 8px; }}
p, li {{ max-width:82ch; }}
p {{ margin:0 0 12px; }}
code {{ font-family:var(--mono); font-size:.86em; background:var(--code-bg); padding:1px 5px; border-radius:3px; }}
.table-wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:6px; background:var(--surface); margin:0 0 14px; }}
table {{ border-collapse:collapse; width:100%; font-size:13.5px; }}
th, td {{ text-align:left; padding:7px 12px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ background:var(--surface-2); font-weight:500; }}
td.num, th.num, .num {{ font-family:var(--mono); font-variant-numeric:tabular-nums; }}
td.num, th.num {{ text-align:right; white-space:nowrap; }}
figure {{ margin:0 0 14px; background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:8px; }}
figure img {{ width:100%; max-width:100%; height:auto; display:block; }}
figcaption {{ font-size:12.5px; color:var(--muted); margin-top:6px; }}
.grid2 {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:12px; }}
.grid3 {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:12px; }}
.decision {{ border:1px solid var(--line); border-left:4px solid var(--warn); background:var(--surface); border-radius:6px; padding:10px 14px; margin:0 0 10px; }}
.decision b {{ color:var(--warn); }}
.note {{ font-size:13px; color:var(--muted); }}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">ic-opt-modular · T12 建库 · 第二族 · N28 · 待批准</div>
  <h1>N28 变压器建库提案：xfm_bs 与 xfm_ms</h1>
  <p class="note">沿用电感库的原则与流程：不带中心抽头；线宽覆盖 4–10 µm；两级外径比限制在 0.5×–2×；查询目标 L、k、SRF、Q；full-wave。先报参数范围与 EMX 设置 → 批准 → 先导批 → 批准 → 正式跑。本页所有点数都由当前生成器逐点实造得出（不跑 EMX）。</p>
</header>

<h2>一句话</h2>
<p>两族共 <b class="num">{n_bs + n_ms}</b> 点（xfm_bs {n_bs}、xfm_ms {n_ms}），两种金属对（AP/M10、M10/M9）各一半。粗估 EMX 墙钟约 <b class="num">{hours(cost['bs'] + cost['ms'], 8):.0f} h</b>（8 并发）或 <b class="num">{hours(cost['bs'] + cost['ms'], JOBS):.0f} h</b>（{JOBS} 并发 × 8 线程 = 128 线程上限）；先导批会给出实测值后再定。</p>

<h2>1 参数化建模：各参数范围</h2>
<h3>1.1 两族共同</h3>
<div class="table-wrap"><table><thead><tr><th>项</th><th>取值</th><th>说明</th></tr></thead><tbody>
<tr><td>金属对（初级/次级）</td><td><code>AP / M10</code>、<code>M10 / M9</code></td><td>与电感库两种体材对应；初级在上层。xfm_ms 的次级跨线再往下一层（M9 / M8）。</td></tr>
<tr><td>初级外径 OD_P</td><td class="num">60, 80, 100, 120, 150, 180, 210, 240 µm</td><td>与电感库同一组外径。</td></tr>
<tr><td>次级外径 OD_S</td><td>同一组外径中落在 <b>0.5×–2× OD_P</b> 内的值</td><td>用户原则。xfm_bs 另加 OD_P ±4/±8/±12 µm（见 1.2）。</td></tr>
<tr><td>开口 / 引线</td><td class="num">8 µm / 20 µm（两绕组相同）</td><td>与电感库一致；生成器在小外径处会拒绝放不下的开口。</td></tr>
<tr><td>地夹具</td><td>内边距 15、环宽 50、桩长 2、无倒角（µm）</td><td>与电感库一致；每个端口一个地 pin（G01–G04）。</td></tr>
<tr><td>中心抽头</td><td>不带</td><td>用户原则。</td></tr>
<tr><td>合理性规则</td><td>每个绕组内径 ≥ 30 µm</td><td>与电感库一致。</td></tr>
<tr><td>横向错位（中心距）</td><td>0；另在“外径与线宽都相同”的对角点上加 0.25 / 0.5 / 0.75 × 上限</td><td>上限 = (OD_P+OD_S)/4（生成器强制，超过就不再重叠）。错位是降 k 的旋钮，只放在对角上，见决策 X3。</td></tr>
</tbody></table></div>

<h3>1.2 xfm_bs（单匝对单匝，上下层宽边耦合）</h3>
<div class="table-wrap"><table><thead><tr><th>参数</th><th>取值</th><th>理由</th></tr></thead><tbody>
<tr><td>W_P、W_S</td><td class="num">4, 6, 8, 10 µm，全交叉（16 组）</td><td>k 取决于上下两条线的径向重叠（|OD_S−OD_P|/2 与线宽之比），两个线宽都要独立扫。</td></tr>
<tr><td>近对角外径</td><td class="num">OD_S = OD_P ± 4, ± 8, ± 12 µm</td><td>旧库实测：外径比 1.0 时 k≈0.65，0.8 时就掉到 ≈0.36。外径台阶 20–30 µm 太粗，接不住这个峰；±4/8/12 让径向错开 2/4/6 µm，与线宽同量级。</td></tr>
</tbody></table></div>
<p>候选 {bs_t[0]} → 内径不足滤掉 {bs_t[1]} → 生成器拒绝 {bs_t[2]} → <b>{n_bs} 点</b>（其中横向错位 {off_bs}）。拒绝原因：</p><ul class="note">{reasons('bs')}</ul>

<h3>1.3 xfm_ms（单匝初级对多匝次级）</h3>
<div class="table-wrap"><table><thead><tr><th>参数</th><th>取值</th><th>理由</th></tr></thead><tbody>
<tr><td>次级匝数 NT_S</td><td class="num">2, 3, 4, 5</td><td>与电感库一致。</td></tr>
<tr><td>W_P、W_S</td><td class="num">4, 7, 10 µm</td><td>覆盖 4–10。</td></tr>
<tr><td>次级线距 S_S</td><td class="num">2, 3, 4 µm</td><td>旧库固定 2 µm。刚建好的电感库实测 S 从 2 加到 4：L 降 6–17%、SRF 升 16–31%，不是弱参数，必须扫。</td></tr>
<tr><td>(W_P, W_S, S_S) 组合方式</td><td>L9 正交表（每个外径/匝数单元 9 组，不是 27 组）</td><td>任意两个参数之间仍然全交叉；第三个参数的对应关系随单元轮换，全库合起来 27 种组合都出现（实测 {len(collections.Counter((p['w_p'], p['w_s'], p['s_s']) for p in OK['ms']))} 种）。全交叉会有 8376 点，L9 压到三分之一。</td></tr>
</tbody></table></div>
<p>候选 {ms_t[0]} → 内径不足滤掉 {ms_t[1]} → 生成器拒绝 {ms_t[2]} → <b>{n_ms} 点</b>（{by_nt()}；其中横向错位 {off_ms}）。拒绝原因（多为小外径放不下跨线焊盘或桥）：</p><ul class="note">{reasons('ms')}</ul>

<h2>2 覆盖预估</h2>
<div class="table-wrap"><table><thead><tr><th>族</th><th>估算 Lp nH</th><th>估算 Ls nH</th><th>估算 k_lf</th></tr></thead><tbody>
<tr><td>xfm_bs</td><td class="num">{rng('bs', 'Lp_est_nH')}</td><td class="num">{rng('bs', 'Ls_est_nH')}</td><td class="num">{rng('bs', 'k_est')}</td></tr>
<tr><td>xfm_ms</td><td class="num">{rng('ms', 'Lp_est_nH')}</td><td class="num">{rng('ms', 'Ls_est_nH')}</td><td class="num">{rng('ms', 'k_est')}</td></tr>
</tbody></table></div>
<div class="table-wrap"><table><thead><tr><th>估算 k 分布（点数）</th><th class="num">&lt;0.2</th><th class="num">0.2–0.3</th><th class="num">0.3–0.5</th><th class="num">0.5–0.6</th><th class="num">≥0.6</th></tr></thead><tbody>
<tr><td>xfm_bs</td>{kbins('bs')}</tr><tr><td>xfm_ms</td>{kbins('ms')}</tr></tbody></table></div>
<p class="note">估算来源：各绕组 L 用 Mohan 电流片公式，按刚建成的 N28 电感库逐（体材、匝数）标定；k 用旧 N28 变压器库（第 2–5 代几何、粗网格准静态，低频 k 可信）按（外径比、错位、匝数、外径、线宽）取最近 5 点平均。只用来判断覆盖，入库的是 EMX 实测值。</p>
<div class="grid2">
<figure><img src="{od_map('bs')}" alt="xfm_bs OD map"><figcaption>xfm_bs 外径覆盖：对角附近加密（近对角 ±4/8/12），虚线是 0.5× 与 2× 边界。</figcaption></figure>
<figure><img src="{od_map('ms')}" alt="xfm_ms OD map"><figcaption>xfm_ms 外径覆盖：小外径的多匝次级大多因内径或跨线放不下而被滤掉。</figcaption></figure>
</div>
<div class="grid2">
<figure><img src="{k_hist()}" alt="k histogram"><figcaption>估算 k 分布；橙色是横向错位点。</figcaption></figure>
<figure><img src="{l_scatter()}" alt="Lp vs Ls"><figcaption>估算 Lp 对 Ls（AP/M10）：xfm_ms 的次级感值覆盖到约 9 nH，匝比可达约 1:4。</figcaption></figure>
</div>

<h2>3 代表几何（AP/M10，生成器实造）</h2>
<p class="note">六个例子都过了设计规则审计（0 违例）与端口连通检查。</p>
<div class="grid3">
<figure><img src="{img('bs_matched')}" alt="bs matched"><figcaption>xfm_bs 同外径同线宽：k 最高（估算约 0.68）。</figcaption></figure>
<figure><img src="{img('bs_ratio2')}" alt="bs ratio 2"><figcaption>xfm_bs 外径比 2：边界点，k 估算约 0.17。</figcaption></figure>
<figure><img src="{img('bs_offset05')}" alt="bs offset"><figcaption>xfm_bs 横向错位 0.5 × 上限：k 估算约 0.32。</figcaption></figure>
<figure><img src="{img('ms_nt3')}" alt="ms NT3"><figcaption>xfm_ms NT=3，外径比 1.2：初级压在次级中间匝上方，k 估算约 0.68。</figcaption></figure>
<figure><img src="{img('ms_ratio05')}" alt="ms ratio 0.5"><figcaption>xfm_ms 外径比 0.5：边界点，k 估算约 0.15。</figcaption></figure>
<figure><img src="{img('ms_nt5')}" alt="ms NT5"><figcaption>xfm_ms NT=5 同外径：Ls 估算约 6.8 nH。</figcaption></figure>
</div>

<h2>4 EMX 设置</h2>
<div class="table-wrap"><table><thead><tr><th>项</th><th>设置</th><th>说明</th></tr></thead><tbody>
<tr><td>求解</td><td><code>--full-wave</code></td><td>同电感库：每个端口有独立地 pin，回流路径明确。</td></tr>
<tr><td>扫频</td><td>xfm_bs 0→{BS_STOP} GHz、xfm_ms 0→{MS_STOP} GHz，步进 1 GHz</td><td>旧库实测 SRF 在 150 GHz 内的比例：bs 初级 71%、次级 76%（200 GHz 内 93% / 96%）；ms 初级约 90%、次级 ≥98%。所以 bs 扩到 200 GHz、ms 保持 150 GHz，见决策 X2。</td></tr>
<tr><td>网格</td><td>thickness 0.25 / edge-width 0.2 / max-splits 5</td><td>电感先导批选定的细档。变压器上下层间距小、k 对层间场敏感，先导批复核一次（见第 5 节）。</td></tr>
<tr><td><code>--3d</code></td><td>bs：<code>AP,M10</code> / <code>M10,M9</code>；ms：<code>AP,M10,M9</code> / <code>M10,M9,M8</code></td><td>绕组与跨线金属；M1 地环不做三维（电感先导批实测影响 &lt;0.1%）。</td></tr>
<tr><td>端口</td><td><code>p01=P1:G01 p02=N1:G02 p03=P2:G03 p04=N2:G04</code></td><td>四端口 sNp，顺序固定。</td></tr>
<tr><td>其余</td><td><code>--via-separation=0.5</code>、<code>--simultaneous-frequencies=0</code>、每任务 8 线程、超时 7200 s</td><td>同电感库；同时频点数 0 是 2026-07-09 事故后的硬规则。</td></tr>
<tr><td>测量</td><td>两个理想巴伦差分驱动：Lp、Ls、k_lf、Qp/Qs 峰值、SRF_p/SRF_s</td><td>与 OCEAN 对齐过的内核（最大相对误差 4.7e-6）；低频量取 ≤3 GHz 均值。</td></tr>
<tr><td>并发</td><td>正式批 {JOBS} 并发 × 8 线程 = 128 线程；每任务内存上限按先导批峰值定，总和 ≤ 256 GB</td><td>用户上限 128 线程 / 256 GB。电感库用 8 并发（64 线程），这次点数是电感库的 5.7 倍，建议用满上限，见决策 X4。</td></tr>
</tbody></table></div>

<h2>5 先导批</h2>
<p>目的：(1) 复核细网格对 k 与 Q 是否已收敛；(2) 实测变压器单点墙钟与峰值内存，定并发与每任务内存上限；(3) 首次走通 ic-opt 四端口变压器 em_only 流程（端口、测量量、入库字段）。</p>
<div class="table-wrap"><table><thead><tr><th>几何（AP/M10）</th><th>网格</th><th>扫频</th></tr></thead><tbody>
<tr><td>bs 小：OD 60/60，W 4/4</td><td>细、极细（0.15/0.1/6）</td><td>0–200 GHz</td></tr>
<tr><td>bs 大：OD 240/240，W 10/10</td><td>细、极细</td><td>0–200 GHz</td></tr>
<tr><td>ms 中：OD 150/180，W 7/7，NT 3，S 2</td><td>细、极细</td><td>0–150 GHz</td></tr>
<tr><td>ms 大：OD 240/240，W 10/10，NT 5，S 4</td><td>细、极细</td><td>0–150 GHz</td></tr>
</tbody></table></div>
<p>共 8 次 EMX，串行，每次 16 线程、内存上限 64 GB。判据：细档对极细档 L &lt;0.5%、k &lt;1%、Q &lt;2%、SRF &lt;1%，满足就用细档；不满足则报告后再定。</p>

<h2>6 代价粗估</h2>
<div class="table-wrap"><table><thead><tr><th>族</th><th class="num">点数</th><th class="num">粗估单点中位 s</th><th class="num">8 并发 h</th><th class="num">{JOBS} 并发 h</th></tr></thead><tbody>
<tr><td>xfm_bs</td><td class="num">{n_bs}</td><td class="num">{statistics.median(est_wall('bs', p) for p in OK['bs']):.0f}</td><td class="num">{hours(cost['bs'], 8):.1f}</td><td class="num">{hours(cost['bs'], JOBS):.1f}</td></tr>
<tr><td>xfm_ms</td><td class="num">{n_ms}</td><td class="num">{statistics.median(est_wall('ms', p) for p in OK['ms']):.0f}</td><td class="num">{hours(cost['ms'], 8):.1f}</td><td class="num">{hours(cost['ms'], JOBS):.1f}</td></tr>
</tbody></table></div>
<p class="note">模型：把两个绕组当成两个同尺寸电感，按电感库实测单频点耗时相加，再乘频点数。四端口与层间耦合会让实际更慢，所以这是下限；先导批后给出实测值。</p>

<h2>7 待拍板</h2>
<div class="decision"><b>X1 参数网格</b>：按第 1 节，共 {n_bs + n_ms} 点（bs {n_bs}，ms {n_ms}）。可选缩减：bs 线宽改 4/7/10（约 −44%），或 ms 去掉 S_S=3（约 −33%）。建议按原方案。</div>
<div class="decision"><b>X2 扫频</b>：bs 0–{BS_STOP} GHz（比 150 GHz 贵约 33%，SRF 覆盖率从约 71% 升到约 93%），ms 0–{MS_STOP} GHz。建议如此。</div>
<div class="decision"><b>X3 横向错位点</b>：bs {off_bs} 点、ms {off_ms} 点，只放在同外径同线宽的对角上，给 k 提供 0.15–0.5 的另一条路径。真实设计里错位用得少，如觉得不必要可整体删掉。建议保留。</div>
<div class="decision"><b>X4 并发</b>：正式批 {JOBS} × 8 线程 = 128 线程，每任务内存上限按先导批峰值定、总和 ≤ 256 GB。建议如此。</div>
<div class="decision"><b>X5 先导批</b>：按第 5 节跑 8 次 EMX（约 1 小时内）。</div>

<h2>位置</h2>
<p class="note">网格：<code>{html.escape(str(grid_path.resolve()))}</code>（每点含状态、拒绝原因、估算 Lp/Ls/k）；脚本 <code>xfm_grid.py</code>、<code>xfm_proposal.py</code>；代表几何 <code>xfm/*.png</code>。库将建在 <code>/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28/</code> 下，与电感库并列。</p>
</div>
"""
out.write_text(page, encoding="utf-8")
print(out, f"{out.stat().st_size / 1e6:.2f} MB", {f: round(hours(cost[f], 8), 1) for f in cost})
