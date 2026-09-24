# T13：器件查询库嵌入 ic-opt 的开发方案

- 状态：**已批准，除 T13.6 真实 EMX 复核外全部完成**（2026-09-24）：T13.1–T13.5、T13.6 代码（94310db…13a1523）、T13.7 变压器四个分层（04b3ab9、5ab7add 系统 SRF、eae6b94 Q 峰只在系统 SRF 以下取、验证报告 `reports/library_query/XFM_QUERY_VERIFY_CN.html`）、T13.8 文档（d60d496）、T13.9（5c4136a）、T13.10（9c74db5、cf71c26）、T13.11（300626f：金属按过孔链的层序编号，现有工艺逐字节不变）。T13.6 的 `--plan` 已给出（10 个电感候选），等用户确认
- 取代：`T10_LIBRARY_PLAN_CN.md`（2026-09-22）。T10 以"从 em-opt 的 sqlite 导入 12,767 行"为前提；按 T12 的决定，各器件族改为用第 7 代几何、细网格全波在 ic-opt 里直接重建，这个前提已不成立
- 依据：电感查询库验证（`reports/library_query/IND_QUERY_VERIFY_CN.html`）、T12 建库记录（`EXECUTION_PLAN_CN.md`）、em-opt 查询栈分析（`analysis/em/03_device_db_and_surrogate.md`）
- 架构图与开发流程图（archify 生成，可交互）：`reports/library_query/arch/t13-library-module.architecture.html`、`reports/library_query/arch/t13-dev-plan.workflow.html`

## 0. 一句话

**库就是 ic-opt 的观测表，已经在那里；查询是在观测上拟合代理模型回答正向和逆向问题；复核是 em_only 真实 EMX；回流是 adopt。** 新增一个 `ic_opt.library` 包、4 个 block、2 个 recipe、1 个 stage。em-opt 查询栈里的 sqlite、`hermes-db`、ingest、backflow、model_cache 都不搬，只移植 StratumGP 与 DomainGuard 两个内核（约 1.2k 行），并按验证结论修正。

## 1. 与 T10 的差异

| 问题 | T10 的做法 | T13 的做法 | 原因 |
| --- | --- | --- | --- |
| 数据从哪来 | 从 em-opt sqlite 一次性导入 | 不导入；直接读 T12 建的库工程 | 新库已是 ic-opt 观测表；旧库用粗网格，高频 Q 最多偏高 27%，不宜混用 |
| 库坐标 | spec 里加 `derived` 表达式层（开口、中心距由坐标推导） | 不需要：库坐标就是生成器字段 | 新库开口固定、中心距按 µm 直接入库 |
| 查询口径 | 用入库标量 | 从 sNp 按统一口径重算 | 单圈工程扫到 250 GHz、其余 150 GHz，Q 峰值口径不一 |
| SRF 约束 | 5 个最近实测点都满足 | 对 log SRF 拟合 GP | 验证：GP 中位误差 0.2–0.3%，5 近邻 8–9% |
| 域守卫 | 原样搬 | 按层剔除退化维度；数据不足的层当判据 2 返回 | 验证：原版拒掉全部单圈查询；高频锚定会让预测抛异常 |
| σ | 原样用 GP 的 σ | 按留出残差校准 k | 验证：2σ 实际覆盖 86–96%，多数低于 95% |

## 2. 模块设计

### 2.1 包结构 `src/ic_opt/library/`

| 模块 | 内容 | 来源 |
| --- | --- | --- |
| `manifest.py` | 解析库根的 `library.yaml`：分层（stratum）、每层的部件（库工程目录与扫频上限）、坐标维度、匝数维、查询量定义、代际（几何代 + EMX 物理设置） | 新写 |
| `dataset.py` | 分层数据集：只取代际一致的 `ok` 观测；用 `em.measure` 从 sNp 统一重算查询量（含锚定 L/Q/k @ f）；按 observations.jsonl 哈希 + measure 代码版本缓存在 `<library>/.cache/` | 新写（原型：`ind_dataset.py`） |
| `gp.py` | StratumGP：per_nt / joint、rbf / matern52、log 目标与 2σ 界、变压器 k 的无量纲特征映射；未拟合层返回"不可用"而不是抛异常；σ 校准系数 | 移植 em-opt `surrogate/model.py` 并修正 |
| `domain.py` | DomainGuard 四判据：已测范围盒、匝数层样本量、凸包、相对 σ；凸包只用该层有变化的维度 | 移植 em-opt `surrogate/domain.py` 并修正 |
| `query.py` | `query()` / `suggest()` / `coverage()`：纯函数，输入分层数据集与模型 | 新写（原型：`ind_query_verify.py`） |
| `stage.py` | `Predict` stage 与 `surrogate_pipeline(spec, library)`：让 `opt.optimize` 直接在代理模型上搜 | 新写 |

对外只暴露这些接口：

| 接口 | 形态 | 做什么 |
| --- | --- | --- |
| `lib.coverage` | block | 每层的已测范围、各匝数层点数、各查询量的取值范围、代际与完整行数 |
| `lib.query` | block | 给几何：命中实测点就返回实测；否则返回 μ 与 2σ 界、域守卫结论、3 个最近实测点 |
| `lib.suggest` | block | 给目标（`min` / `max` / `target±tol`，可锚定频率）：Sobol 候选池 → 域守卫 → 预测 → 2σ 保守约束 → 按目标下界排序 → 去重 → 用真实生成器实造并做设计规则与连通检查 → 返回候选与证据 |
| `lib.load` | block | 打印分层数据集摘要（行数、代际、缓存状态） |
| `lib_design` | recipe | `opt.optimize(pipeline=surrogate)` → 取前 k → 实造审计；零 EMX |
| `lib_signoff` | recipe | 对候选跑 `em_only` 真实 EMX（先 `--plan`，受站点资源上限约束）→ 报告实测与预测的 z 分数和 2σ 覆盖 → `opt.adopt` 回流进对应分层的库工程（`origin = signoff:<run>`） |

CLI 不新增子命令：`ic-opt call lib.query <library> stratum=ind_sym_ap 'params={…}'`，dict 结果直接打印为 JSON（原计划的 `--format json` 选项因此不需要）。唯一的 CLI 改动是 `call` 识别含 `library.yaml` 的目录（此时向 block 提供 `library` 而不是 `spec` / `store`）。

### 2.2 库清单 `library.yaml`（放在库根，仓库外）

```yaml
# <库根>/library.yaml —— 仓库外；仓库里只有测试用的 demo_6m 合成库（实现后的写法，量名即测量内核的量名）
schema_version: ic-opt-library-v1
process_profile: <profile>
strata:
  ind_sym_ap:
    generator: clean_port_ind_sym
    dims: [outer_diameter_um, width_um, spacing_um, turns]
    nt_dim: turns
    parts:                        # 一个分层可由多个库工程组成，扫频上限从各自的 sNp 读
      - {store: ind_sym_ap}       # 0–150 GHz
      - {store: ind_sym_ap_nt1}   # 单圈 0–250 GHz
    steps: {outer_diameter_um: 1, width_um: 0.1, spacing_um: 0.1, turns: 1}   # 逆向推荐的候选分辨率
    quantities:
      Lp_lf: {}
      Lp_res: {}
      Qp_peak: {band_ghz: 150}    # 统一口径：各部件都在 0–150 GHz 内找峰
      SRF_p: {}                   # GP 建模；扫频内无谐振 → above_sweep
      Lp: {anchors_ghz: [10, 28, 60]}   # 列 Lp@10 / Lp@28 / Lp@60；srf_margin 默认 1.25
      Qp: {anchors_ghz: [10, 28, 60]}
  xfm_bs_ap:                      # T13.7 定稿
    generator: clean_port_xfm_bs
    dims: [<变压器库的坐标维>]
    parts: [{store: xfm_bs_ap}]
    quantities: {Lp_lf: {}, Ls_lf: {}, k_lf: {feature_map: <k 的无量纲映射>}, Qp_peak: {band_ghz: 150}, Qs_peak: {band_ghz: 150}, SRF_p: {}, SRF_s: {}}
```

代际：数据集只收 `pipeline_fingerprint` 与清单声明的代际一致的行（几何代 `GEOMETRY_VERSION` + EMX 物理设置 + `.proc` 内容哈希），天然不混代。

### 2.3 数据与安全边界

- 库数据（观测、sNp、缓存）只在 `IC_OPT_LIBRARY` 指向的仓库外目录；仓库测试用 demo_6m 合成库（由假 EMX 生成的解析 S 参数），不含 N28 数值。
- em-opt 工作区只读：移植是复制代码并注明出处，不改、不删原工程。
- GP 拟合的 BLAS 线程受限（默认 4），与 EMX 共机时不挤占 128 线程额度；真实 EMX 只在 `lib_signoff` 里跑，先 `--plan`，受站点资源上限约束。

### 2.4 工艺接入：用户为自己的工艺建库要提供什么

| # | 用户提供 | 内容 | 来源 |
| --- | --- | --- | --- |
| 1 | 工艺规则 profile `<IC_OPT_PROFILE_DIRS>/<profile>/rule.yaml` | `layer_catalog`（金属 / 过孔 / 标记的 GDS 图层号与 pin 层、各层在 .proc 里的名字）、`emx_stack`（各金属厚度须与 .proc 一致、过孔等效尺寸）、`layout_rules`（线宽 / 线距 / 最大线宽、过孔尺寸 / 间距 / 包围、过孔阵列、宽线平行间距）、`coverage` | foundry 设计规则手册与 PDK 图层表；只填生成器用到的核心规则 |
| 2 | EMX 工艺文件 `.proc` | 叠层厚度、介质、电导率、GDS 图层号到各层的映射 | 通常由 PDK 提供 |
| 3 | 运行环境 | EMX 与 license、Cadence 环境 csh、站点资源上限 | 用户站点 |

依据：仿真叠层以 `.proc` 为准（EMX 直接读它）；版图几何按 profile 的 `layout_rules` 画，代码里不写死任何工艺数值；`drc_audit` 是按 profile 的 5 类核心检查（最小线宽、最小线距、最大线宽、过孔包围、宽线平行间距）加端口连通检查，**不是 foundry 签核 DRC**（密度、开槽、天线、线端等不查），流片前仍须用 foundry 的签核规则。

三项新增：

1. **编写指南迁入（T13.9）**：把 em-opt 的 `author-process-rule` 技能搬进本仓库 `skills/author-process-rule/SKILL.md`，以 demo_6m 为完整示例（与 `profiles/demo_6m/rule.yaml` 逐字节同步，由测试守住）；把 profile 文件头里失效的 `hermes-workflow validate-profile` 引用改成本仓库的接口。
2. **`em.validate_profile` 接口（T13.10）**：把已有的 `validate_profile` 暴露为 block，经 `ic-opt call em.validate_profile <profile 目录> proc=<.proc> generate=true` 调用（`call` 也识别含 `rule.yaml` 的目录）。检查分四关：格式 → 内部一致性 → 与 .proc 核对（层名、厚度，**新增 GDS 图层号核对**：profile 的 drawing / pin 图层号须与 .proc 的 `layer` 映射一致，否则 EMX 认不出几何或端口）→ 每个器件族实造一个并做 DRC 审计与连通检查。
3. **金属按 profile 顺序编号（T13.11）**：现在代码按名字编号（M1…M10 为 1–10，AP 写死为 11），超过 10 层金属或顶层不叫 AP 的工艺接不进来。改为按 profile 中导体的上下顺序编号，名字任意（RDL、UTM 等均可）；`M1` 仍专供接地夹具（改为"最底层导体"并在 profile 里显式声明）。要求对现有 profile（demo_6m、N28、N65）**生成结果逐字节不变**，不升几何代。

## 3. 验证结论如何进入方案

| 验证发现 | 方案里的落点 | 验收门 |
| --- | --- | --- |
| 正向预测中位误差 0.03–0.23%，p90 < 1% | T13.2 移植后在同一数据、同样种子下复现 | G1：各查询量中位误差在本次结果 ±10% 内，2σ 覆盖差 ≤2 个百分点 |
| 域守卫拒掉全部单圈查询 | `domain.py` 每层只用有变化的维度建凸包 | G1：库点、中点全接受；越界、造不出来、非整数匝数全拒绝（同本次表） |
| 高频锚定时稀疏层抛异常 | `gp.py` 未拟合层返回"不可用"，域守卫当判据 2 | 单测：28 / 60 GHz 锚定查询不抛异常 |
| SRF：GP 0.2–0.3% vs 5 近邻 8–9% | SRF 走 GP；无谐振区域单独判 | G1 中含 SRF 行 |
| 2σ 覆盖 86–96% | 按留出残差的 z 分位数校准 k，使覆盖达 95% | 校准后留出覆盖 95% ± 2% |
| 逆向留出：首选全部命中，前 3 命中 99.5–100% | `lib.suggest` 按本次算法实现 | G2：同一留出协议复现 |

## 4. 任务分解（每任务一个提交，定向测试）

| # | 任务 | 产出 | 完成判据 |
| --- | --- | --- | --- |
| T13.1 | 清单与数据集 | `manifest.py`、`dataset.py`、缓存；demo_6m 合成库夹具 | 在真实库上复现本次数据检查（1038 行、重算逐位一致、全部无源）；合成库单测 |
| T13.2 | 模型内核 | `gp.py`、`domain.py`（移植 + 三处修正 + σ 校准） | G1 |
| T13.3 | 查询与覆盖 | `lib.query` / `lib.coverage` / `lib.load`；`call` 识别 `library.yaml`；`--format json` | 命中实测点返回实测；域外返回判据与 3 个最近邻；JSON 输出结构测试 |
| T13.4 | 逆向推荐 | `lib.suggest`（含锚定目标自动加 SRF ≥ 1.25 f0 约束、实造与设计规则审计） | G2；本次三个示例查询复现 |
| T13.5 | 代理流水线 | `Predict` stage、`surrogate_pipeline`、recipe `lib_design` | 假 EMX 端到端：设计 → 候选 → 实造审计 |
| T13.6 | EMX 复核与回流 | recipe `lib_signoff`、复核报告（z 分数、2σ 覆盖）、`adopt` 回流 | G3：经批准后对约 10 个候选跑真实 EMX，实测落在 2σ 内的比例 ≥ 90% |
| T13.7 | 变压器接入 | xfm_bs / xfm_ms 四个分层；k、Lp、Ls、SRF_p、SRF_s；k 的无量纲映射在新库上重验 | 变压器库建成后，按本次同样的验证流程出报告 |
| T13.8 | 文档与交付 | `docs/guide` 查询库一章、SKILL 查询段、T10 标注作废、`EXECUTION_PLAN_CN.md` | 文档与代码一致 |
| T13.9 | profile 编写指南迁入 | `skills/author-process-rule/SKILL.md`（demo_6m 示例逐字节同步）；修正 profile 文件头的失效引用 | 同步测试通过；指南里的命令在本仓库可执行 |
| T13.10 | `em.validate_profile` 接口 | block + `call` 识别 `rule.yaml` 目录；新增 GDS 图层号与 .proc 映射核对 | demo_6m 全关通过；故意改错图层号 / 厚度 / 名字各报一条可定位的错误；N28、N65 私有 profile 全关通过 |
| T13.11 | 金属按 profile 顺序编号 | `_metal_index` 等按 profile 导体顺序编号；夹具层显式声明；名字不再限于 M1…M10 / AP | 13 个黄金 GDS 逐字节不变；demo_6m / N28 / N65 各族实造结果不变；新增一个 12 层、顶层叫 RDL 的合成 profile 能生成全部器件族 |

规模：移植约 1.2k 行，新写约 1.3k 行（含工艺接入约 400 行），另加测试。执行顺序：T13.1 → T13.5（查询内核），T13.11 → T13.10 → T13.9（工艺接入；T13.11 改动生成器内核，放在变压器正式批跑完之后），T13.7 等变压器库跑完（预计 2026-09-24 中午前后），T13.6 先 `--plan` 经确认再跑，T13.8 收尾。T13.1–T13.5、T13.9–T13.11 都不需要 EMX。

## 5. 已拍板（2026-09-23）

1. 旧 em-opt 库不导入；各族按 T12 的流程重建。
2. `library.yaml` 放在库根（仓库外），仓库只放 demo_6m 示例。
3. CLI 只让 `call` 识别 `library.yaml`（以及 T13.10 的 `rule.yaml`）目录，不新增子命令。
4. SRF 改用 GP。
5. σ 按留出残差校准到 95% 覆盖。
6. T13.6 的真实 EMX 复核：变压器库跑完后先给 `--plan`，经确认再跑（约 10 点，每点 8 线程）。
7. 追加工艺接入三项：编写指南迁入（T13.9）、`em.validate_profile` 接口含图层号核对（T13.10）、金属按 profile 顺序编号（T13.11）。

## 6. 风险

- 变压器 k 的无量纲映射的证据来自旧库（粗网格、准静态），需在新库上重验（T13.7）。
- 变压器分层每层上千行，GP 拟合是 O(n³)：n≈1500 为秒级到十秒级，可接受；超过时再做稀疏化或分块。
- T13.11 改动生成器内核的编号逻辑，一旦几何有变就会让在跑或已建的库失去复用；必须对现有 profile 逐字节不变、不升几何代，并放在变压器正式批跑完之后做。
- 公开仓库：库数值与 N28 渲染图不进仓库；本方案的报告页与验证数字属于同一类待处理内容，推送前统一处理（已记在推送前清单）。
