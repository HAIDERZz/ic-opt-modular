# T10：查询库（器件特性库 / 代理模型 / 逆向推荐）的嵌入方案

- 状态：**规划稿，待用户批准后执行**（2026-09-22）
- 前提：T9 的 EM 核心已就绪并经真实冒烟（`EXECUTION_PLAN_CN.md` §3 T9.1–T9.7）；本文只讨论 em-opt 第二条产品线"查询库"（`device_db/` 4.7k 行 + `surrogate/` 2.2k 行 + `hermes-db` 9 个动词 + `experiments/device_db_sweep_n28/` 的扫参工具）怎么进来
- 依据：`analysis/em/03_device_db_and_surrogate.md`（schema v4、测量、StratumGP、域守卫、逆向算法、扫参计划、13 条陷阱）、`04_recorded_data_and_fakes.md` §1（数据清单）
- 原则不变：做减法；库不进评估链（`DESIGN_CN.md` 4.1 已定）；新需求 = 新 block / 新 recipe，不是新 CLI

## 0. 一句话

**库就是一张观测表；查询是 block；推荐是"在代理模型流水线上做优化"再用真实 EM 复核；入库不是动作——跑过的点本来就在表里。** em-opt 的 sqlite、`hermes-db`、独立的 ingest / backflow / model_cache / family_schemas 全部退役，只搬三样内核：StratumGP（含无量纲特征映射）、DomainGuard、"库坐标 → 生成器配置"的派生规则。

## 1. 概念映射

| em-opt | ic-opt | 备注 |
| --- | --- | --- |
| sample（sqlite 一行） | `Observation`（`.icopt/observations.jsonl` 一行） | `params` = 库坐标（dims），`children["<device>/nominal"].metrics` = 标量量，sNp 在 `sims/<obs>/em/<device>/` |
| stratum（家族 × 金属栈，12 个） | **库工程** `<library>/<stratum>/spec.yaml`（一个器件，`fixed` 里是金属栈等常量） | 长期累积、追加写；扩库 = 加预算再跑 |
| 六元组身份 family / process_profile / family_schema_revision / emx_settings_hash / params_hash / geom_version | `spec_fingerprint`（family、profile、fixed、变量定义都在 spec 里）+ `pipeline_fingerprint`（`Pcell.identity = 几何代 GEOMETRY_VERSION`，`Emx.identity` = EMX 物理设置 + `.proc` 内容哈希）+ `Observation.key`（吸附后的坐标） | 引擎的复用规则本来就按这三者判"同一次测量"；**不混代**天然成立：代不同 → `pipeline_fingerprint` 不同 → 不复用、不拟合 |
| `status ∈ {ok, build_rejected, emx_failed}` | `ok` / `failed:pcell` / `failed:emx:<device>` | 更细；`build_rejected` 行导入后就是 `failed:pcell` 行 |
| `source ∈ {sweep, optimizer, manual}` + `--pin-geom-version` | `origin`（`points:sobol` / `suggest:…` / `user` / `import:device_db:<stratum>:<id>`）+ `pipeline_fingerprint` | 出处与代际分开记 |
| 曲线表（`Lp/Qp/Ls/Qs/k` × 201 频点） | 不复制：保留 sNp，`measure` 内核按需重算（12k 行 × 40 KB，秒级） | 少一份会漂移的副本；`--anchored Lp@28e9` = 一个 `quantity: Lp, frequency_hz: 28e9` 的指标 |
| `hermes-db query --params` | 在代理流水线上 `sim.evaluate` 一个点（零仿真）；若该点已测过，引擎复用规则直接给出实测 | 见 §3 |
| `hermes-db suggest --spec` | `opt.optimize(pipeline=surrogate)` + `signoff(em_only)` | 见 §4 |
| `hermes-db coverage / family-status` | `analyze.report` 的一节 | achieved box、NT 分层、代际行数、完整行数 |
| `hermes-db ingest-optimizer-run` / backflow | `opt.adopt(spec, foreign)`（已有） | 把另一个工程的观测按本 spec 重新打分并入 |
| `hermes-db reingest`（换公式重算） | recipe `remeasure`（对库观测重跑 `measure`） | 公式版本 = `measure` 代码本身 |
| `sweep_driver` + `family_schemas` + `sampler` | recipe `em_sweep`（`points.sobol` → `em.buildable` 过滤 → `sim.evaluate(em_only)`） | 见 §2 |
| `EmxResourceGuard`（未接线） | 站点包络 + `--plan`（T9 已定） | — |

## 2. 库工程的 spec：库坐标 → 生成器配置

em-opt 的扫参坐标（dims）不等于生成器字段：`opening_um` 由 `(OD, W)` 按 `dop()` 规则派生，`stub_width_um = W`（变压器取 `max(Wp, Ws)`），`center_spacing_um = ratio × (OD_a + OD_b) / 4`，IL/MS 还有次级 OD 等派生量（`03` §5、`family_schemas.py`）。这些规则今天藏在 `experiments/` 的采样器里，库的"坐标系"因此不可见、不可复现。方案：spec 显式声明派生字段。

```yaml
# <library>/xfm_bs_m10m9/spec.yaml —— 一个 stratum 就是一个库工程
project: xfm_bs_m10m9
devices:
  - id: xfm
    generator: clean_port_xfm_bs
    profile: n28_1p10m
    ports: [P1, N1, P2, N2]
    fixed:            # 金属栈与常量：与 em-opt strata 表逐项对应
      primary_metal: "10"
      secondary_metal: "9"
      primary_lead_length_um: 20.0
      secondary_lead_length_um: 20.0
      ground_fixture: {inner_margin_um: 15.0, ring_width_um: 50.0, stub_length_um: 2.0, stub_chamfer_um: 0.0}
    variables:        # 库坐标 = spec 变量（bare 名，单器件）
      primary_outer_diameter_um: od_p
      secondary_outer_diameter_um: od_s
      primary_width_um: w_p
      secondary_width_um: w_s
    derived:          # 生成器字段 = 变量的表达式；`max_opening` 由 pcell 插件注册进表达式命名空间
      primary_opening_um:   "min(8.0, floor(0.9 * max_opening(od_p, w_p) * 100) / 100)"
      secondary_opening_um: "min(8.0, floor(0.9 * max_opening(od_s, w_s) * 100) / 100)"
      center_spacing_um:    "csr * (od_p + od_s) / 4"
      ground_fixture.stub_width_um: "max(w_p, w_s)"
variables:
  - {name: od_p, kind: continuous_step, lower: "40", upper: "160", step: "1"}
  - {name: od_s, kind: continuous_step, lower: "40", upper: "160", step: "1"}
  - {name: w_p,  kind: continuous_step, lower: "3",  upper: "12",  step: "0.1"}
  - {name: w_s,  kind: continuous_step, lower: "3",  upper: "12",  step: "0.1"}
  - {name: csr,  kind: continuous_step, lower: "0",  upper: "1",   step: "0.01"}   # center_spacing_ratio
em: { …与扫参计划的 emx 块一致… }
metrics:            # 库的标量量 = 家族契约的 metric_names（03 §2.6）
  - {name: Lp_res, unit: H, device: xfm, quantity: Lp_res}
  - {name: Qp_peak, unit: ratio, device: xfm, quantity: Qp_peak}
  - {name: SRF_p, unit: Hz, device: xfm, quantity: SRF_p}
  - {name: Ls_res, unit: H, device: xfm, quantity: Ls_res}
  - {name: Qs_peak, unit: ratio, device: xfm, quantity: Qs_peak}
  - {name: SRF_s, unit: Hz, device: xfm, quantity: SRF_s}
  - {name: k_lf, unit: ratio, device: xfm, quantity: k_lf}
budget: {max_simulations: 5000}
```

要加的东西：

1. `devices[].derived`（点分路径 → 表达式）：由 `device_config()` 在 fixed + 变量之后求值；表达式用已有的安全求值器（`min/max/ln` 已支持，加 `floor`），插件可注册函数（`max_opening` 来自 `_pcell_core.max_opening`）。派生规则的版本从此写在 spec 里（`spec_fingerprint` 覆盖），不再是 `opening_rule_version` 这种旁注。
2. `Pcell.identity = GEOMETRY_VERSION`：由插件声明（`generator_plugin.GEOMETRY_VERSION = 6`，即 em-opt `ingest.CURRENT_GEOM_VERSION` 的延续），进 `pipeline_fingerprint`。几何一改就升号；`tests/ic_opt/test_em_pcell.py` 的 V1 字节回放守住"改了几何忘了升号"。
3. `em.buildable(spec, points) -> points`：用真实生成器在临时目录构建（1–5 ms/点，NT=2 约 0.3 s）过滤掉造不出来的点——取代 `family_schemas.feasible()` 的解析近似；`em_sweep` recipe 先 `points.sobol` 过采样再过滤，达到"每 stratum 接受 N 点"的扫参语义。
4. `SRF` 为 None 的行不再是"失败"：库指标里 `SRF_p` 可为空（em-opt 的 NULL 语义）。`Measure` 对 `quantity` 为 None 的**标量**记 `null` 而不是 issue（只有曲线取值越界才失败）；`objective` / `constraints` 引用到 None 时按缺失指标处理（现有 `metric_failed`）。这一条让"扫库"里无谐振的器件仍是 `ok` 行。
5. `simulations()`：`failed:pcell` 的子结果不计预算（没有仿真发生）。

## 3. 代理模型 = 一个阶段

```text
Point ─pcell─▶ Geometry ─predict─▶ ChildResult{ Q, Q_lo, Q_hi }     (device 子链；零仿真)
```

- `em/surrogate/stratum_gp.py`：em-opt `surrogate/model.py` 原样搬（per-nt / joint、rbf / matern52、`xfm_bs` / `xfm_ms` 的 k 无量纲特征映射、log 目标与 delta 近似 σ、`MIN_NT_SAMPLES = 25`）；`em/surrogate/domain.py`：DomainGuard 四判据（achieved box、NT 层样本量、凸包、相对 σ）原样搬。训练输入从"sqlite 行"改为 `Observations`（X = spec 变量，y = 指标）——这是唯一的接口改动。
- `Predict(spec, training: Observations, k_sigma=2.0)`：构造时按 (器件, 指标) 各拟合一个 StratumGP（只用 `pipeline_fingerprint` 与当前流水线一致的 `ok` 行；曲线量按 `frequency_hz` 现算 y）；`run` 输出每个请求指标的 `Q`（μ）、`Q_lo` / `Q_hi`（μ ∓ kσ；log 目标在 log 空间取区间再变换，= em-opt `prediction_bounds`）；域守卫拒绝 → `failed:predict`，issues 里带判据编号与 3 个最近邻实测点（em-opt `OutOfDomainError` 的证据语义）。
- `surrogate_pipeline(spec, training)` = `[Pcell, Predict]`：`pcell` 在前，造不出来的点在这里就 `failed:pcell`——em-opt `suggest(verify_build=True)` 藏在建议器里的那次构建验证，变成流水线里可见的一段。
- 保守约束：用户在目标 spec 里对 `Q_lo` / `Q_hi` 写约束（`min` 用 `Q_lo`，`max` 用 `Q_hi`，`target±tol` 两条），语义与 em-opt `_bounds_satisfy` 相同且显式。
- 模型缓存：一次 `evaluate` 拟合一次（进程内）；em-opt 的 pickle 缓存（`model_cache.py`）不搬——12k 行 GP 拟合是秒级到十秒级，不值一层指纹缓存；需要时再加。

## 4. 动词 → recipe

| recipe | 做什么 | 由哪些 block 组成 |
| --- | --- | --- |
| `em_sweep LIB n=500 seed=…` | 扫库 / 扩库 | `points.sobol`（过采样）→ `em.buildable` → `sim.evaluate(em_only)`；重跑即扩库 |
| `predict LIB points=…` | 查一个/几个点（= `hermes-db query`） | `sim.evaluate(pipeline=surrogate(LIB))`：已测过的点由引擎复用给出实测，没测过的给 μ / σ / 域守卫结论 |
| `inverse TARGET library=LIB k=5` | 逆向推荐（= `hermes-db suggest`） | `opt.optimize(pipeline=surrogate(LIB), strategy=turbo\|openbox)` 在目标 spec 上搜（目标 spec = 库 spec + 目标约束/目标函数；变量空间相同）→ `analyze.best(k)`；候选多样化 = 现有 `points.from` 的去重 + 缩放空间最小间距（搬 `DIVERSIFY_MIN_SPACING`） |
| `signoff TARGET` | 真实复核（= 闭环战役的一批） | 已有的 `signoff` recipe，把搜索流水线换成 surrogate、复核换成 `em_only`；复核结果 `opt.adopt` 进 LIB |
| `fill_gap LIB n=…` | 找库里没测过的可构造点（= `hermes-db fill-gap`） | `points.sobol` → `em.buildable` → 去掉 `LIB` 观测已有的键 → 打印 / 写 points.json，不启动 EMX |
| `remeasure LIB` | 换公式重算（= `reingest`） | 对每条观测在其 sNp 上重跑 `Measure`，写新观测（旧行保留，`pipeline_fingerprint` 因 measure 版本而异） |
| `analyze.report` 新增一节 "Library coverage" | = `coverage` / `family-status` | 每变量 achieved min/max、NT 分层计数、各代行数、完整行数、域守卫可用的凸包点数 |

`opt.adopt` 已存在：把闭环复核、别的优化工程、手工点并入库都是它。**没有"ingest"**——同一张表。

## 5. 数据落地与一次性导入

- 库数据不进代码仓库（9,722 个 sNp ≈ 450 MB）：`IC_OPT_LIBRARY=/path/to/library/`，下面 12 个 stratum 工程目录；recipe 参数 `library=` 接受工程路径。
- `em.import_library(sqlite, sweep_root, out_root)`（一次性 block）：
  1. 每个 stratum 从 `strata` / `family_contracts` 表 + 扫参计划的 `fixed` / `opening_rule` / `emx` 生成 `spec.yaml`（§2 形态；派生表达式按家族模板生成）；
  2. 每个 `ok` 样本：`params_json` → `params`，sNp 复制到 `sims/<obs>/em/<device>/`，标量用 `measure` 现算（与 sqlite 值逐位一致，V2 已证），`origin = import:device_db:<stratum>:<sample_id>`，`pipeline_fingerprint` 按该行的 `geom_version` + 该 `emx_settings_hash` 对应的 EMX 设置计算（四组 `emx_settings_hash` 各取一个样本的 `emx_manifest.json` 重建 `em` 段）；
  3. `build_rejected` 样本 → `failed:pcell` 行（保留"这些坐标造不出来"的知识，它是域守卫的一部分）；
  4. 校验：行数 = 9,722 ok + 3,045 rejected；每行 sNp sha256 = `snp_sha256`；每行标量 = sqlite `metrics`。

## 6. 验证

| # | 证明什么 | 金标准 | 判据 |
| --- | --- | --- | --- |
| V7 | 导入无损 | sqlite 12,767 行 | 行数、sNp sha256、标量逐位一致（复用 V2 内核） |
| V6 | 代理模型无损 | `GP_BENCHMARK.md`（per_nt-matern52 留出 CV 中位相对误差 0.0180）；`03` §4.8 的 geom5 复核（400 点 pin5：209 预测 / 191 凸包外） | 同一留出集复算 CV 在基准 ±10% 内；域守卫在同 400 点上的接受/拒绝集合相同 |
| V8 | 派生规则无损 | 6,300 个 sweep 点的 `geometry_manifest.json` | `params_json` 经 §2 的 `derived` 表达式得到的生成器配置 == manifest 里的 `geometry.config`（逐字段） |
| V9 | 闭环可跑（需批准） | `.scratch/em-closed-loop-validation/CAMPAIGN.md` 的协议（三批 58 点，2σ 覆盖 95.6%） | `inverse` 提 5 点 → `signoff(em_only)` 真实 EMX（每点 2–3 s）→ 实测落在预测 2σ 内的比例与战役同量级；结果 `adopt` 进库 |

## 7. 任务分解（每任务一个提交）

| # | 任务 | 产出 | 完成判据 |
| --- | --- | --- | --- |
| T10.1 | spec 与内核的准备 | `devices[].derived`（表达式 + 插件函数注册）、`Pcell.identity = GEOMETRY_VERSION`、`em.buildable`、`Measure` 对 None 标量的处理、`simulations()` 不计 `failed:pcell` | 现有 96 测试不变；V8 通过（派生规则复现 manifest） |
| T10.2 | 导入 | `em.import_library`、12 个库工程的 spec 生成、`IC_OPT_LIBRARY` | V7 通过 |
| T10.3 | 代理模型 | `em/surrogate/{stratum_gp,domain}.py`（搬运）、`Predict`、`surrogate_pipeline`、recipe `predict` / `inverse` | V6 通过；`ic-opt run inverse` 在导入库上给出候选并打印域守卫证据 |
| T10.4 | 库的日常动词 | `em_sweep` / `fill_gap` / `remeasure` recipe、`signoff` 接 surrogate→em_only、报告 "Library coverage" 一节 | 假 EMX 下 `em_sweep` → `inverse` → `signoff` → `adopt` 端到端 |
| T10.5 | 真实闭环 + 文档 | V9（用户批准后）；README / SKILL 的库段；`DESIGN_CN.md` | 一批真实点通过；文档与代码一致 |

净效果：搬入 ≈ 1.2k 行（StratumGP + DomainGuard）+ 新写 ≈ 600 行（derived / buildable / Predict / import / recipes）；em-opt 的 `device_db/`（4.7k）、`surrogate/inverse.py`（1.2k）、`model_cache.py`、`hermes-db` CLI（1.8k）、`experiments/device_db_sweep_n28/{sweep_driver,sampler,family_schemas}.py` 全部退役（em-opt 工作区本身不动，见 §9）。

## 8. 需要用户拍板

1. **库数据放哪**：建议 `IC_OPT_LIBRARY` 指向仓库外目录（如 `$HOME/ic-opt-library/`），12 个 stratum 工程各一目录；导入源 sqlite 与 sweep 目录原地保留在 em-opt 工作区。
2. **导入范围**：建议全部四代（geom 2–5）都导入并按 `pipeline_fingerprint` 分代——旧代数据仍可作 `initial=` 参考；只用最新代拟合是默认行为。
3. **`derived` 表达式**是否接受为库坐标的正式定义（替代 em-opt 的 `opening_rule` / `family_schemas`）。
4. **真实闭环**（T10.5）执行前再批准。

## 9. em-opt 工作区的处置（用户 2026-09-22 裁定：不动原始工程）

`<em-opt>` **原样保留、不删不改**：它是回放测试的金标准数据源（V1–V4、V7–V8 都从它读）、私有工艺 profile 与 `.proc` 的所在、以及历史记录。从本仓库的角度它已退役——功能对照表：

| em-opt 用法 | ic-opt 等价物 |
| --- | --- |
| `em-ic-opt PROJECT --doctor / --real / --continue N` | `ic-opt doctor PROJECT` / `ic-opt run optimize PROJECT` / 加大 `budget=` 重跑（`ic-opt migrate` 先转 `em_opt_requirement.md`） |
| `hermes-workflow check-requirement / prepare / validate` | `ic-opt run … --plan`（唯一审批点） |
| `hermes-db query / suggest / fill-gap / coverage / reingest / ingest-optimizer-run` | T10 的 `predict` / `inverse` / `fill_gap` / 报告一节 / `remeasure` / `opt.adopt` |
| `sweep_driver.py --plan …` | `ic-opt run em_sweep LIB` |
| `validate-profile`（工艺规则撰写套件） | `ic_opt/em/pcell/profile_validation.py` 已随几何库搬入，T10.4 接成 `ic-opt call em.validate_profile` |
