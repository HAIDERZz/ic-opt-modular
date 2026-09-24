# T14：查询库"目标窗 → 可行参数区域"（`lib.region`）开发方案

状态：规划稿（2026-09-24），待用户拍板第 7 节后开工。

## 0. 一句话

把今天验证过的原型脚本 `docs/refactor/reports/library_query/xfm_bs_region.py` 规范成查询库的正式功能
`lib.region`：输入一个分层和一组目标窗，输出"能落进窗内的几何参数区域"（按维的范围、按分组的条件范围、
一个量随一个维的走势、代表性候选、库内实测证据），供用户设扫参边界；两层单圈变压器一次 3 分钟以内（模型已缓存时）。

## 1. 背景与今日实证

- 需求（2026-09-24）："40 GHz 处 Lp、Ls 150–170 pH，两绕组 Q > 10，k 0.5–0.7，单圈/单圈变压器的扫参范围"。
  前仿真用理想元件得到的是一个窗口，真实器件是窗口附近的一片区域；现有 `lib.suggest` 回答的是"最好的几个点"，
  不是"区域的边界"。
- 原型：`xfm_bs_region.py`（257 行）+ 页面脚本 `xfm_bs_region_report.py`（165 行）；结果页
  `XFM_BS_40G_REGION_CN.html`（AP/M10 稳健 50 / 均值 4312 格、实测命中 2；M10/M9 稳健 23 / 均值 3739、命中 5；
  Q 是最紧约束，k 由中心偏移决定）。
- 原型暴露并已修的根因（提交 `0ac6507`）：`DomainGuard` 用 Delaunay 逐点找单纯形判断凸包归属，5 维每点 3.5 ms，
  150 万格要 90 分钟；改为凸包半空间判定（同一凸包、逐点结果一致、快 2500 倍，6 维建凸包 44 s → 0.1 s）。
- 实测耗时（两层合计 6.5 min）：每层 6 个量并行拟合 + 粗筛 109 s、细网格 160 万格 + 域守卫 2 s、65 万域内格 × 6 量
  预测 80 s（30 线程）、汇总出图几秒。串行拟合是 2 min/量（GP 超参数优化不随 BLAS 线程扩展）。
- 原型里的两个坑，规范化时必须消掉：(1) 预测两次（均值一次、区间一次）；(2) 库的 SRF 模型按 GHz 拟合，
  `query._predict` 与 `suggest.score` 各自乘 1e9 还原，脚本漏乘导致一轮全空——正式实现只走 `suggest` 的预测路径。

## 2. 需求与验收标准

功能（F）
- F1 目标语法与 `lib.suggest` 一致（`{min}`、`{max}`、`{target, tol}`），并新增窗口写法 `{min: a, max: b}`
  （两处共用 `parse_targets`）。锚定量自动隐含 `SRF ≥ srf_margin × f0`（复用 `implied_srf`）。
- F2 两级可行：**稳健**（校准 2σ 区间整体在窗内，与 `suggest` 的判定完全相同）与**均值**（预测均值在窗内）。
- F3 输出：两级各自的格数与按维范围；`group_by` 维上的条件范围；`trend`（一个量随一个维的 min/中位/max）；
  `binding`（每个目标单独满足的格数，指出最紧约束）；`edge`（可行集是否触及库覆盖边界）；代表性候选；
  库内实测已满足全部目标的行；抽样点列表供画图；分段耗时。
- F4 网格在库采样域内（域守卫 1–3 条 + σ/μ ≤ 15%），有匝数维的分层按整数层枚举、层内固定维保持固定
  （与 `suggest.pool` 同规则）。
- F5 CLI：`ic-opt call lib.region <库根> stratum=… 'targets={…}' [objective=max:Qp@40] [steps={…}] [group_by=a,b]
  [trend=k@40:center_spacing_um]`，输出 JSON；文档与 SKILL 块清单同步。

性能（P，N28 单圈变压器一层，65 万域内格 × 6 量，`threads=8`）
- P1 模型已缓存：≤ 3 min；P2 模型未缓存但校准已缓存：≤ 6 min；P3 全新频率（含校准）：≤ 15 min。
- P4 单点 `lib.query` / `lib.suggest` 行为与耗时不变或更好（`score` 只预测一次）。

正确性（C）
- C1 合成变压器库上：库内每一行若其实测值满足目标，则该行所在格（网格含库点时）必在均值集内；稳健集 ⊆ 均值集。
- C2 `score` 拆分后，`lib.suggest` 的全部现有测试不改一字通过；`{min,max}` 窗口在 suggest 与 region 中含义相同。
- C3 真实 N28 库 40 GHz 案例：与今日原型同网格（OD 2 / W 1 / CS 2 µm）下稳健/均值格数与按维范围逐一相同
  （原型的 JSON 已入库：`xfm_bs_region_40g.json`）。
- C4 60 GHz 案例（锚点已在清单里）跑通，作为第二个验收样本。
- C5 G1/G2 门、库测试、`ruff` 全绿；`pcell` 黄金 GDS 不受影响（本任务不碰生成器）。

## 3. 设计

### 3.1 接口

```python
# src/ic_opt/library/region.py
def region(library: query.Library, stratum: str, targets: dict, objective: str | None = None, *,
           steps: dict[str, float] | None = None,      # 每维网格步长（须为清单 steps 的整数倍）；None = 自动
           levels_per_dim: int = 20,                    # 自动步长：括号盒内每个连续维约 20 档，向上取到清单步长的整数倍
           max_points: int = 2_000_000,                 # 细网格上限；超过则各维等比放粗并在输出里报告有效步长
           pool_size: int = 32768, seed: int = 0,       # 粗筛的 Sobol 池（复用 suggest.pool）
           k: float = 2.0, rel_sigma_max: float = domain.DEFAULT_SIGMA_REL_MAX,
           group_by: list[str] | None = None, trend: tuple[str, str] | None = None,
           n: int = 8, min_spacing: float = 0.05, verify_build: bool = False,
           sample_size: int = 5000, threads: int | None = None, workers: int | None = None) -> dict
```

Block（`src/ic_opt/blocks/library.py`，注册名 `lib.region`）：同名参数，`group_by` 接受逗号列表，`trend` 接受
`"<quantity>:<dim>"`，`steps`/`targets` 为 JSON。

返回结构（键固定，便于页面脚本与测试）：

```
stratum, targets（含隐含 SRF）, objective,
grid:      {steps, bracket: {dim: [lo, hi]}, points, in_domain, confident, coarsened: int, auto_steps: bool}
levels:    {robust: {count, ranges: {dim: [min, max]}}, mean: {…}}
binding:   {quantity: 单独满足该目标（均值级）的格数}
edge:      {dim: {at_min, at_max}}                       # 均值集触及库覆盖边界
group_by:  {dims, rows: [{key: {dim: v}, count_robust, count_mean, ranges: {dim: [lo, hi]}, objective: [lo, hi] | null}]}
trend:     {quantity, dim, rows: [{value, count, min, median, max}]}   # 在"满足其余全部目标"的均值级格上统计
candidates: [suggest 同款条目 {params, predicted{q: value, lo, hi, rel_sigma}, nearest[, build]}], candidates_level
measured:  [{part, obs_id, params, values}]               # 库内实测值满足全部目标的行
points_sample: [{params, level, predicted: {q: value}}]   # ≤ sample_size，seed 固定
seconds:   {models, coarse, grid, predict, summarize, total}
notes:     [str]
```

### 3.2 算法

1. 解析目标（`parse_targets` + `implied_srf`），取模型 `Library.models(stratum, names, workers=…)`（3.4）。
2. 粗筛：`suggest.pool(pool_size)` 的 Sobol 候选 → 一次预测 → 用**放宽 10% 的窗**（min×0.9、max×1.1、
   窗两端外扩 10%）按均值级筛 → 括号盒 = 通过点的按维 min/max，各维再外扩一个细步长；无通过点 → 返回空结果
   并在 `notes` 说明哪个目标先把池筛空（用 `binding`）。
3. 细网格：括号盒内按步长 `arange`；匝数维按整数层枚举、层内固定维固定；超过 `max_points` 等比放粗
   （`coarsened` 记倍数）。
4. 域守卫（每个模型的 `guard.inside`，与 `score` 一致）→ **一次预测**得到 (μ, σ)，由 `prediction_bounds` 出
   k·k_scale 的 (lo, hi)（分块 4 万点，SRF 乘 1e9 还原，`srf_floors` 处理扫频内无谐振的行）。
5. 两级判定：稳健 = `score` 的原判定（lo ≥ min、hi ≤ max、窗内整段）；均值 = 把 (lo, hi) 换成 (μ, μ)。
6. 汇总：范围投影、`group_by`、`trend`、`binding`、`edge`、候选（稳健集非空取稳健集，否则均值集；按
   objective 保守界或按贴近度排序，`diversify` 拉开；`verify_build=True` 时走 `build_check`）、实测行、抽样点。

### 3.3 改动清单（复用优先）

- `suggest.py`
  - `parse_targets`：新增 `{min, max}` → `Target(kind="window", value=a, tol=b)`（`window()` 返回 `(a, b)`），
    原有三种写法不变；现有测试里"`{min,max}` 报错"的断言改为"解析为窗口"。
  - `score` 拆成 `predict_all(x, models, *, k, rel_sigma_max, srf_floor, exact, check_domain) -> (ok, pred)` 与
    `satisfy(pred, targets, level="robust"|"mean") -> mask`；`score` 保持签名与返回值不变（组合两者），
    并把两次 GP 预测合并为一次。`k_scale` 的处理、SRF 单位、settled 行的处理都留在 `predict_all` 里。
- `region.py`（新）：3.1/3.2；不重复实现任何预测/判定逻辑。
- `query.py`：`Library.models(stratum, quantities, *, workers=None, threads=None) -> dict[str, Model]`
  （并行拟合未缓存的量，见 3.4）；`Library.model` 走落盘缓存。
- `blocks/library.py` + `blocks/__init__.py`：`lib.region`。
- `docs/em/library.md`：第 5 节后新增"5b. Region questions: `lib.region`"（bash 示例须能被 `test_library_docs.py`
  解析）；`skills/ic-opt/SKILL.md` 块清单加 `lib.region`；`T13_LIBRARY_MODULE_PLAN_CN.md` 状态加一行；
  `EXECUTION_PLAN_CN.md` 记 T14。
- `docs/refactor/reports/library_query/xfm_bs_region_report.py` 改为读 block 输出（通用：group_by 表、trend 表、
  `points_sample` 出图）；原型脚本 `xfm_bs_region.py` 删除（其 JSON 保留作 C3 对照）。

### 3.4 模型落盘缓存与并行拟合

- 文件：`<库根>/.cache/model-<stratum>-<q_at>-<key>.pkl`，`key` = sha256(数据集键 + settings + calibration +
  `MODEL_CACHE_VERSION` + `gp.py` 源码哈希 + sklearn 版本)[:20]；写入用临时文件 + `os.replace`；读取失败或
  版本不符一律重拟合并覆盖（fail closed）。`Model.guard` 不缓存（ConvexHull 0.1 s 内重建）。
- `Library.models(...)`：未命中的量用 `ProcessPoolExecutor`（`fork` 上下文）并行，每个工作进程
  `threadpool_limits(threads // workers)`，拟合后自己写缓存，父进程随后按缓存加载（避免大对象经管道来回）；
  `workers=None` → `min(6, 未命中数)`；`threads=None` → `OMP_NUM_THREADS` 或 8。校准缓存不变。
- 单点 `lib.query`/`lib.suggest` 自动受益：第二次起不再拟合。

### 3.5 页面与图

Block 只返回数据。页面脚本负责：按维范围表、`group_by` 表、`trend` 表、候选表、实测行表、模型校准折叠表，
以及 `points_sample` 的三张图（前两维投影 + 实测行标星、`group_by` 两维的计数热图、`trend` 散点）。

## 4. 任务分解（每任务一个提交，定向测试，`ruff` 干净）

| 任务 | 内容 | 交付与测试 | 估计 |
|---|---|---|---|
| T14.1 | `Library.models` 并行拟合 + 模型落盘缓存（3.4） | `query.py`；`tests/ic_opt/test_library_query.py` 加：缓存命中不重拟合（monkeypatch `StratumGP.fit` 计数）、损坏缓存回退重拟合、`models()` 与逐个 `model()` 预测逐点相同、版本键变化即失效 | 120–160 行 + 80 行测试 |
| T14.2 | `parse_targets` 窗口；`score` 拆分为 `predict_all`/`satisfy`；`region.py` | `suggest.py`、`region.py`；`tests/ic_opt/test_library_region.py`（合成 xfm 库：C1、稳健 ⊆ 均值、空目标结构化返回、`max_points` 放粗有效步长、`group_by`/`trend` 形状、`edge`、候选级别回退）；`test_library_suggest.py` 现有用例不改（除 `{min,max}` 那一句） | 300–350 行 + 150 行测试 |
| T14.3 | block `lib.region` + CLI + 文档 + SKILL + 页面脚本改造 + 删原型 | `blocks/`、`docs/em/library.md`、`skills/ic-opt/SKILL.md`、`xfm_bs_region_report.py`；`test_library_docs.py` 通过；CLI 测试 `test_call_lib_region_prints_json` | 120–150 行 + 40 行测试 |
| T14.4 | 验收（Claude 执行） | 真实 N28：40 GHz 同网格与原型 JSON 逐项相同（C3）；冷/热耗时（P1–P3）；60 GHz 跑通（C4）；G1/G2 门与库测试；页面重生成并重发 artifact | — |

依赖：T14.1 与 T14.2 互不依赖（不同文件；`region.py` 通过 `Library.models` 接口取模型，接口以本文 3.1/3.4 为准，
T14.2 的测试用合成小库、逐个 `model()` 即可），可并行派发；T14.3 在两者合入后。

## 5. 本次开发约定（用户 2026-09-24）

- 方案与规格（本文）、每个任务的验收由 Claude 执行；编码任务派发 subagent，模型 **Opus 5.5**、思考强度 **High**。
- 每个任务一个提交，提交信息说明"改了什么、为什么"，尾行 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`；
  只跑定向测试（相关 `tests/ic_opt/test_library_*.py` + `ruff check`），不跑全量。
- 不改生成器/pcell、不动 em-opt 工作区、不跑 EMX；库根在仓库外，测试只用合成库。
- 代码与注释英文，风格随 `suggest.py`/`query.py`（模块 docstring 讲"为什么"，函数短、命名与现有一致）。
- subagent 完成后由 Claude 复核 diff、跑测试、比对接口，再合入或退回。

## 6. 风险与边界

- 网格随维数指数增长：5 维靠括号盒 + 步长可控；7 维（`xfm_ms`）默认自动放粗到 `max_points`，精度下降由输出的
  `steps` 明示；更细需用户缩小目标窗或给 `steps`。
- 可行集是投影：各维相关（线宽定了外径只剩一小段），所以 `group_by` 表是正式输出的一部分，不是附赠。
- `fork` + BLAS：工作进程用 `threadpoolctl` 限线程；今日原型已在 EMX 满载机器上验证可行。
- 缓存的模型是 pickle：只读库根目录下自己写的文件；键含源码哈希与 sklearn 版本，任何不符都重拟合。
- 库覆盖边界：可行集触边（如线宽 10 µm 上界）时只报告不外推；是否补采样由用户决定。

## 7. 待用户拍板

- D1 `targets` 增加 `{min, max}` 窗口写法（suggest 同步支持）——建议：是。
- D2 默认网格：自动步长（每连续维约 20 档，取清单步长整数倍，上限 200 万格），用户可用 `steps` 覆盖——建议：是。
- D3 候选默认不做真实 build 校验（`verify_build=False`，需要时再开）——建议：是（suggest 已负责建得出）。
- D4 模型落盘缓存用 pickle 放 `<库根>/.cache/`，键含源码哈希与 sklearn 版本——建议：是。
- D5 默认线程/进程预算：`threads=8`、`workers=min(6, 量数)`，命令行可覆盖（今天授权的 32 线程仅限本次扫描，不写成默认）——建议：是。
