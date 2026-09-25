"""LIBRARY_PRINCIPLES_CN.html: how the device query library works, for a reader without the mathematics (2026-09-25).

Usage: library_principles_page.py OUT_HTML   (inlines figs/LIBRARY_PRINCIPLES_CN_*.png from library_principles_figs.py)
"""
import base64
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
out = Path(sys.argv[1])


def img(name: str) -> str:
    return "data:image/png;base64," + base64.b64encode((HERE / "figs" / f"LIBRARY_PRINCIPLES_CN_{name}.png").read_bytes()).decode()


page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>器件查询库工作原理</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=JetBrains+Mono:wght@400;600&display=swap">
<style>
:root {{ --ground:#f6f5f0; --surface:#ffffff; --surface-2:#eef0ea; --ink:#1f2320; --muted:#5f665f; --line:#d9dcd3; --accent:#2f7d5f; --accent-2:#4b5fb8; --warn:#b8611f; --code-bg:#eef0ea; --sans:"Noto Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif; --mono:"JetBrains Mono",ui-monospace,Menlo,monospace; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --ground:#141512; --surface:#1d1e1a; --surface-2:#262822; --ink:#e7e8e0; --muted:#a2a497; --line:#383a32; --accent:#7cc0a0; --accent-2:#9aa8ef; --warn:#e3a066; --code-bg:#24261f; }} }}
:root[data-theme="dark"] {{ --ground:#141512; --surface:#1d1e1a; --surface-2:#262822; --ink:#e7e8e0; --muted:#a2a497; --line:#383a32; --accent:#7cc0a0; --accent-2:#9aa8ef; --warn:#e3a066; --code-bg:#24261f; }}
body {{ background:var(--ground); color:var(--ink); font-family:var(--sans); font-size:15.5px; line-height:1.8; margin:0; padding-block:0 64px; padding-inline:16px; }}
.wrap {{ max-width:900px; margin:0 auto; }}
header {{ padding-block:36px 18px; border-bottom:2px solid var(--accent); margin-bottom:8px; }}
.eyebrow {{ font-family:var(--mono); font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }}
h1 {{ font-size:30px; margin:8px 0 10px; text-wrap:balance; }}
h2 {{ font-size:21px; margin:40px 0 10px; padding-top:6px; border-top:1px solid var(--line); }}
h3 {{ font-size:16.5px; margin:22px 0 6px; }}
p, li {{ max-width:76ch; }} p {{ margin:0 0 12px; }}
code {{ font-family:var(--mono); font-size:.86em; background:var(--code-bg); padding:1px 5px; border-radius:3px; }}
.lead {{ font-size:16.5px; }}
.box {{ border:1px solid var(--line); border-left:4px solid var(--accent); background:var(--surface); border-radius:6px; padding:10px 14px; margin:12px 0 16px; }}
.box.warn {{ border-left-color:var(--warn); }}
.box b:first-child {{ color:var(--accent); }} .box.warn b:first-child {{ color:var(--warn); }}
figure {{ margin:12px 0 18px; background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:8px; }}
figure img {{ width:100%; max-width:100%; height:auto; display:block; }}
figcaption {{ font-size:12.5px; color:var(--muted); margin-top:6px; }}
.tw {{ overflow-x:auto; border:1px solid var(--line); border-radius:6px; background:var(--surface); margin:0 0 14px; }}
table {{ border-collapse:collapse; width:100%; font-size:13.5px; }}
th, td {{ text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ background:var(--surface-2); font-weight:500; white-space:nowrap; }}
.formula {{ font-family:var(--mono); font-size:.92em; background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:8px 12px; margin:8px 0 12px; overflow-x:auto; }}
.note {{ font-size:13px; color:var(--muted); }}
nav.toc {{ font-size:13.5px; display:flex; flex-wrap:wrap; gap:4px 14px; padding:8px 0 14px; }}
nav.toc a {{ color:var(--accent-2); text-decoration:none; }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="eyebrow">ic-opt-modular · 查询库 · 原理讲解 · 2026-09-25</div>
  <h1>器件查询库是怎么算出答案的</h1>
  <p class="lead">库里存的全是真实 EMX 的结果。模型要做的只有两件事：在没有仿真过的几何上给一个估计值，并且诚实地说出这个估计有多大把握。全文只用四个词：表、行、结果列、模型；用到的数学只有"加权平均"和"取对数"。</p>
</header>
<nav class="toc">
<a href="#s1">1 数据</a><a href="#s2">2 核心算法</a><a href="#s3">3 不确定度与补点</a><a href="#s4">4 校准</a><a href="#s5">5 域守卫</a><a href="#s6">6 输入坐标</a><a href="#s7">7 组合模型</a><a href="#s8">8 四种问法</a><a href="#s9">9 对 N-16 的意义</a><a href="#s10">术语表</a>
</nav>

<h2 id="s1">1 数据长什么样</h2>
<p>一张<b>表</b>对应一个器件族加一种金属体，例如 <code>xfm_bs_ap</code>（单圈变压器，AP 金属）。表里的每一<b>行</b>是一个几何：单圈变压器有五个尺寸（初级外径 OD_P、次级外径 OD_S、两个线宽 W_P / W_S、中心偏移 CS），加上这一行从 sNp 重算出来的<b>结果列</b>：低频电感 Lp_lf / Ls_lf、低频耦合 k_lf、Q 峰值、系统自谐振频率 SRF，以及按频率锚定的列（Lp@40、k@40 等，40 指 40 GHz）。</p>
<p>库的规矩是<b>一列一模型</b>：每张表的每个结果列各自一个模型，永远不跨金属体去借数据。八张表现在共 9,080 行。</p>

<h2 id="s2">2 核心算法：高斯过程回归</h2>
<h3>直觉</h3>
<p>把每一行已仿真的几何想成钉在墙上的一颗钉子，模型是一张绷在钉子上的弹性布。钉子处布的位置是确定的；钉子之间布的位置由两边的钉子拉出来；离钉子越远，布晃得越厉害。<b>晃的幅度就是模型报的不确定度 σ</b>。</p>
<figure><img src="{img('gp')}" alt="一维高斯过程示意"><figcaption>图 1（示意数据）：左图，点之间的带子越宽，模型越没把握。右图，在最宽处加一个"假定已测"的点，带子立刻收窄，而且不需要知道那个点会测出什么值，这是第 3 节补点方法的依据。</figcaption></figure>
<h3>它实际怎么算</h3>
<p>对一个新几何 x 的预测值，本质上是<b>已仿真各行结果的加权平均</b>：和 x 相似的行权重大，不相似的行权重小。"相似"由一个叫核函数的公式打分，我们用的是 Matern 5/2（比最常见的高斯核多容忍一点局部拐折）。核函数里每个尺寸各有一个<b>长度尺度</b> ℓ：ℓ 大表示沿这个尺寸走很远结果才变，一颗钉子能影响很远；ℓ 小表示结果变得快，钉子只能影响身边。ℓ 不用人定，由数据自己学出来（拟合时最大化似然，四次随机重启取最好的）。另有一个白噪声项，允许 EMX 结果带一点数值噪声，不强迫布严丝合缝地穿过每颗钉子。</p>
<p>两步预处理：每个尺寸先按它的取值范围归一到 0–1，让 ℓ 在各尺寸之间可比；<b>正的结果列（电感、Q、SRF）在对数上建模</b>，原因有三：对数后曲线更平、误差自然变成相对误差（"差 5%"而不是"差 30 pH"）、区间不会算出负电感。耦合 k 可能取负值，所以按原值建模。</p>
<div class="formula">预测值 = exp(μ_log)　　区间 = [ 预测值 × e^(−2σ_log) , 预测值 × e^(+2σ_log) ]　　（2 是默认的 k，第 4 节会再乘一个校准系数）</div>

<h2 id="s3">3 不确定度只看"钉子在哪"：补点方法的依据与盲点</h2>
<p>高斯过程有一个很特别的性质：<b>σ 只取决于已仿真几何的位置，不取决于它们测出来的值</b>。所以可以"假定"某个几何已经测过，立刻算出加上它之后别处的 σ 会降多少，完全不用真跑 EMX（图 1 右）。</p>
<p><code>lib.densify</code> 就靠这一点工作：在允许的范围里撒六万多个候选几何（Sobol 点，吸附到清单里的步长，去掉已测的和域外的），给每个候选打分：各结果列的 σ/μ 除以该列的把握上限（<code>rel_sigma_max</code>），取最大的那一个；挑出得分最高的候选，"假定已测"更新所有候选的 σ，再挑下一个；重复 60 次。整个过程几秒钟，报告里的"选点前 / 后"就是候选池 σ 的中位数和 p90 在这 60 个假定之下的变化。</p>
<div class="box warn"><b>盲点。</b>模型只知道"这里离钉子远所以我没把握"，不知道"这里的物理和已测区域不一样"。B-12 的 60 个选点上，回流前的区间只盖住 15%（k@40）和 17%（SRF）的实测值：在从未采样过的"偏心 + 两外径不等"区域，误差是系统性的，不是随机晃动。这就是为什么选点必须经真实 EMX 复核（<code>lib_signoff</code>）再回流，而不是直接相信模型。</div>

<h2 id="s4">4 校准：让"±2σ 盖住 95%"名副其实</h2>
<p>高斯过程自己报的 σ 往往偏小，尤其在采样盒边缘。库对每个模型做一次<b>留出检验</b>：把行随机分成五份，每次拿掉 20% 的行，用剩下 80% 拟合，预测被拿掉的行，这样每一行都被"没见过它的模型"预测过一次。把实际误差除以模型 σ，看 ±2σ 盖住了多少行；如果不够 95%，就把区间乘一个系数 <code>k_scale</code> 放大到刚好 95%（只放大、不缩小）。</p>
<figure><img src="{img('calibration')}" alt="校准示意"><figcaption>图 2（示意数据）：左，模型自报的 ±2σ 只盖住 80% 的留出行；右，乘 k_scale = 1.6 后盖住 97%。真实库里每个模型各有自己的 k_scale，报告页上"2σ 覆盖"一列就是这么算的。</figcaption></figure>
<p>留出检验还给出这个模型的<b>典型误差</b>（留出行相对误差的中位数）。库把它设成 σ 的下限：一个答案永远不会声称比这个模型的典型误差更准。</p>
<div class="box"><b>校准的边界。</b>留出只能检验已采样区域里的误差，所以校准后的区间在"钉子之间"是可信的，在"从未打过钉子的区域"（第 3 节的盲点）不可信。这就是 N-17 要去补点的原因，也是待办里"把离最近实测行的距离计入不确定度"这个候选想解决的问题。</div>

<h2 id="s5">5 域守卫：什么时候拒答，什么时候标"不确定"</h2>
<p>问一个几何之前，先过三道<b>几何门</b>：每个尺寸都在实测范围之内（实际达到的，不是名义的）；它所在的圈数层至少有 25 行；它落在同层实测点的凸包之内（把实测点当成钉子，用一根橡皮筋箍住，几何必须在橡皮筋里面）。任一门不过就拒答，并附上最近的三行和"裁剪到实测范围上"的最近可答点。</p>
<p>过了几何门再过一道<b>把握门</b>：σ/μ 不能超过这一列的上限 <code>rel_sigma_max</code>（默认 15%，可按列在 library.yaml 里设）。超过就把答案标为"不确定"，而不是假装有把握。</p>

<h2 id="s6">6 输入坐标：换个坐标，布更容易绷平</h2>
<p>变压器的耦合几乎与整体大小无关，所以对变压器表，模型看到的不是五个原始尺寸，而是五个<b>无量纲坐标</b>：log(平均外径)、log(外径比 OD_P/OD_S)、W_P/OD_P、W_S/OD_S、4·CS/(OD_P+OD_S)；多圈表再加次级圈数作为一个坐标，一张布不分层。在这些坐标下结果列变化慢，长度尺度 ℓ 长，档与档之间的插值就准。T16.2a 研究的第一个发现就是：陡的方向是"两线圈错开多少"，原始坐标下 OD_P 档间只能硬插值。</p>
<p>电感表没有这套坐标：圈数是整数，层与层差异大，所以按圈数分层各拟一张布（每层至少 25 行）。</p>

<h2 id="s7">7 组合模型：把"已知的、平滑的部分"拆出来单独建</h2>
<p>一个锚定列，例如 Lp@40，同时装着两样东西：<b>低频电感</b>（几何的平滑函数）和<b>靠近自谐振时的抬升</b>（谐振越靠近 40 GHz 越陡）。用一张布直接拟 Lp@40（<code>model: direct</code>），布必须同时表现平滑和陡峭，只能把 ℓ 学得很短，档与档之间就可能错几十个百分点。</p>
<figure><img src="{img('resonance')}" alt="谐振抬升因子"><figcaption>图 3：理想并联谐振的抬升因子 1 / (1 − (f/SRF)²)。低频电感相同、SRF 从 90 GHz 移到 55 GHz，40 GHz 处的电感从 1.25 倍变到 2.1 倍。SRF 本身随几何变化，所以 Lp@40 对几何很陡。</figcaption></figure>
<p>T16.2a 研究的解法是拆开建，在对数上相加（对数相加等于原值相乘）：</p>
<div class="formula">resonance（单圈表 xfm_bs 现在用的）：log Lp@40 = log Lp_lf + log[ 1 / (1 − (40/SRF)²) ] + log 残差<br>ratio（N-16 要给多圈表 xfm_ms 用的）：　log Ls@60 = log Ls_lf + log R，　R = Ls@60 / Ls_lf</div>
<p>其中 Lp_lf 和 SRF 各自已经有模型（就是回答这两列的那两个模型，共用，不重复拟合）；残差 R/Res 是很平的量，单独一张布就能拟好。不确定度按各部分独立相加：SRF 的不确定度通过抬升因子的斜率传给 Lp@40，谐振离锚定频率越近传得越多，把握门自然会把那些点标为不确定。</p>
<p>ratio 比 resonance 少一项：它不依赖 SRF 模型，只要低频值和比值。多圈表在 60 GHz 附近的抬升更复杂，研究里比值法对它最好：Ls@60 整档留出（整个外径档拿掉再预测）的 p90 误差从 23.8% / 23.5%（ap / m10）降到 7.0% / 8.8%。</p>
<p>组合模型的校准和第 4 节一样做留出检验，但每一折都把所有部件（低频模型、SRF 模型、残差模型）重新拟一遍，保证被预测的行没有被任何部件见过。</p>

<h2 id="s8">8 四种问法，一条预测路径</h2>
<div class="tw"><table><thead><tr><th>问法</th><th>做什么</th><th>怎么用模型</th></tr></thead><tbody>
<tr><td><code>lib.query</code></td><td>一个几何的每个结果列</td><td>域守卫 → 预测值 + 校准区间 + 把握判定；有实测就直接给实测</td></tr>
<tr><td><code>lib.suggest</code></td><td>满足目标窗、留有余量、造得出来的几何</td><td>候选池（实测行 + Sobol 点）→ 域守卫 → 预测 → 保守判定（整个区间在窗内）→ 排序 → 真生成器 + DRC 复核</td></tr>
<tr><td><code>lib.region</code></td><td>满足目标窗的整片区域及各尺寸范围</td><td>粗筛（窗放宽 relax）→ 网格 → robust（区间全在窗内）与 mean（预测值在窗内）两级可行域</td></tr>
<tr><td><code>lib.densify</code></td><td>下一批该仿真哪里</td><td>第 3 节：按 σ/上限 贪心选点，"假定已测"更新</td></tr>
<tr><td><code>lib_signoff</code></td><td>真实 EMX 复核并回流</td><td>逐量比较预测与实测（z、区间内否），<code>adopt=true</code> 把 ok 行写回部件存储，下次建数据集自动包含，模型重拟</td></tr>
</tbody></table></div>
<p>四种问法共用同一条预测路径：每个结果列一次模型调用，区间由它推出，SRF 的单位只在一处换算。</p>

<h2 id="s9">9 这对 N-16 意味着什么</h2>
<p>N-16 改的只是多圈表两列电感"怎么拆"（library.yaml 里给 Lp / Ls 加 <code>model: ratio</code>），不改任何数据，也不改单圈表。验收和单圈表改 resonance 时一样：先在库的临时副本上用库自己的实现做留出检验，看 Ls@60 的整档留出误差是否像研究里那样下降；过了再改真实 manifest、重拟、过 G 门（交叉验证覆盖率）和现有的变压器查询验证页。不花 EMX，约一小时机器时间；改坏了把 manifest 改回去即可。</p>

<h2 id="s10">术语表</h2>
<div class="tw"><table><thead><tr><th>词</th><th>意思</th></tr></thead><tbody>
<tr><td>表 / 行 / 结果列</td><td>器件族 × 金属体 / 一个已仿真几何 / 从 sNp 重算的量</td></tr>
<tr><td>模型</td><td>一张表的一个结果列的高斯过程（或第 7 节的组合模型）</td></tr>
<tr><td>σ（sigma）</td><td>模型对自己估计值的不确定度；对数列上它是相对量（σ/μ）</td></tr>
<tr><td>长度尺度 ℓ</td><td>沿某个尺寸"走多远算远"；从数据学出</td></tr>
<tr><td>k_scale</td><td>校准系数，把 ±2σ 区间放大到留出行 95% 覆盖</td></tr>
<tr><td>典型误差（median_rel）</td><td>留出行相对误差的中位数；σ 的下限</td></tr>
<tr><td>rel_sigma_max</td><td>把握上限：σ/μ 超过它就标"不确定"；也是补点评分的分母</td></tr>
<tr><td>域守卫</td><td>三道几何门 + 一道把握门</td></tr>
<tr><td>feature_map</td><td>模型看的输入坐标（变压器用无量纲坐标）</td></tr>
<tr><td>direct / ratio / resonance</td><td>一个锚定列的三种建法：整体一张布 / 低频值 × 比值 / 低频值 × 谐振抬升 × 残差</td></tr>
<tr><td>留出检验</td><td>拿掉一部分行、用其余行拟合、预测拿掉的行，用来校准和报告典型误差</td></tr>
</tbody></table></div>
<p class="note">依据：<code>src/ic_opt/library/</code> 的 gp.py、domain.py、composed.py、suggest.py、region.py、densify.py 的模块说明与代码；T16.2a 研究页 <code>XFM_ANCHOR_MODEL_STUDY_CN.html</code>；B-12 页 <code>XFM_DENSIFY_B12_CN.html</code>。图 1–3 是示意数据（<code>library_principles_figs.py</code>），不是库里的数值。</p>
</div>
</body>
</html>
"""
out.write_text(page, encoding="utf-8")
print(out, f"{out.stat().st_size / 1e3:.0f} kB")
