# IC-Opt 模块化重构方案（减法版）

- 基线：`main@48e287b`（v0.1.10；dev / 发行 checkout / GitHub 三处一致），tag `baseline-v0.1.10-48e287b`
- 开发区：`<repo>`，分支 `refactor/modular-blocks`
- 状态：**方案已批准（决策 ①–⑦ 全部通过，2026-09-22），源码未动**。实现契约见 `DESIGN_CN.md`，架构/用法图见 `diagrams/`。
- 证据与复现脚本：`docs/refactor/analysis/`；知识图谱：`graphify-out/`

---

## 0. 一页结论

现在的 ic-opt 是「一条 17 步固定管线 + 用 `opt_requirement.md` 的模式组合去驱动它」。
每来一个新需求（多 corner、fix-run、热启动、远程、续跑……）就长出一个新**模式**，
而模式是**横切**的：每个模式维度要改 11–27 个模块（共 74 个）、加一份模板、加一章文档。
这就是「不可能做出适配所有要求的 workflow」的结构性原因，不是努力不够。

重构方向不是「再加一个工作流引擎」，而是把项目**压缩成少数几个深模块（Block）**，
让组合这件事交给已经足够强的模型用 Python 去写：

| | 现在 | 目标 |
| --- | --- | --- |
| 使用方式 | 1 条命令 + 11 种 requirement 模板 | ~8 个 Block + 几个十几行的 Recipe |
| 新需求 | 新模式（横切 11–27 个模块） | 新 Block（1–2 个文件），或**零代码**只写 Recipe |
| 源码 | 74 模块 / 38k 行 | ~20 模块 / 12–15k 行（估算） |
| 「评估一个点」的实现 | ≥4 份 | 1 份 |
| 优化循环 | 每个后端各一整套（1.8k + 2.0k 行） | 1 个 ~50 行循环 + 无状态 `suggest` |
| 本地 / 远程 | 两套编排（5 个 flow 文件） | 一个 `Executor` 接缝，两个适配器 |
| 文件契约产物名 | 111 个，散落在 ≥55 个模块 | 1 张观测表 + 少量派生视图 |
| Agent 必读文档 | SKILL 419 行 + 模板说明 547 行 + … | `ic-opt blocks describe <name>` 自描述 + ≤150 行 skill |

最关键的五个设计决定（第 2.2 节共七条，另两条是「一张观测表」和「Block 自描述」）：

1. **WHAT / HOW 分离**：`spec.yaml` 只描述设计问题（testbench、corner、变量、指标、约束、目标、仿真资源）；
   「怎么跑」写在 Recipe 里。`mode` 这个概念消失，11 个模板 → 1 个 spec。
2. **唯一的评估原语** `evaluate(spec, deck, points, executor) -> observations`。
   fix-run 就是对用户给的点调用它；优化就是 `suggest → evaluate` 循环；扫参 / corner 签核 / 灵敏度都只是换一个「点从哪来」。
3. **无状态 `suggest(spec, observations, n, strategy)`**：两个后端现在都已经支持「从 trace 重建状态」，
   把它变成常态后，**续跑**和**热启动**不再是模式——它们只是「传入已有观测」。
4. **传输是 Executor 的属性，不是一条独立流程**：`LocalExecutor` / `SshExecutor`；ADR-0001 的 fail-closed 规则原样成为这个接缝的契约。
5. **护栏从「仪式」变成「不变量」**：资源包络、license、仿真预算、互斥锁、来源记录内建在 `evaluate/executor` 里；
   package → approve → supervisor_instruction → task package → acceptance → finalize 这条为弱模型设计的证据链删除，换成一个 `--plan` 预览。

---

## 1. 现状诊断（带证据）

### 1.1 规模与结构

扁平包 `hermes_workflow`，74 个模块 / 38,075 行；测试 78 个文件 / 52,213 行 / 1,513 个测试函数；
面向用户与 agent 的文档约 6,200 行；requirement 模板 11 份。按职能归类（脚本：`docs/refactor/analysis/import_graph.py`）：

| 职能族 | 模块数 | 行数 | 代表模块 |
| --- | ---: | ---: | --- |
| A 规格/契约 | 10 | 4,197 | `requirement_intake`(1652) `validate`(893) `schemas`(587) |
| B 评估（仿真） | 11 | 7,204 | `spectre_ocean`(1470) `real_run`(1277) `real_run_recovery`(908) `metric_results`(761) |
| C 优化（搜索） | 13 | 7,171 | `openbox_backend`(1968) `native_turbo`(1786) `history_warm_start`(982) |
| D 分析/报告 | 15 | 6,733 | `optimizer_insights`(2524) `optimizer_html_report`(637) `optimizer_acceptance`(450) |
| E 环境/闸门 | 10 | 4,196 | `remote_doctor`(1062) `doctor_readiness`(647) `product_doctor`(577) |
| F 远程传输 | 7 | 3,230 | `remote_prepare`(717) `remote_ssh`(705) `retention_evidence`(647) |
| G 编排/CLI | 7 | 5,416 | `remote_optimizer_flow`(1712) `cli`(1374) `remote_fix_run_flow`(616) |

包内 import 图是 17 层 DAG（2 个小环：`real_run↔real_run_recovery`、`native_turbo↔multi_testbench_aggregation`）。
扇入最高：`validate`(33)、`schemas`(21)、`package`(15)、`real_run`(13)。
知识图谱（本次在新开发区重建：4,862 节点 / 17,696 边 / 160 社区）的 god node 前四是
`ContractBundle`(129 条边)、`VariablesConfig`(126)、`RemoteProjectRef`(125)、`MetricsConfig`(125)。
跨社区桥接度最高的两个源码节点更说明问题：**`assert_valid_project()` 连接 33 个社区**——几乎每一步都从磁盘重新加载并校验整个项目，
这是黑板模式的直接证据；**`RemoteProjectRef` 连接 17 个社区**——「远程」渗透全仓库，而不是待在一个接缝后面。
`ContractBundle` 事实上就是 Spec，只是没有被当作参数在步骤之间传递。

### 1.2 七个结构性问题

**P1 模式是横切的（散弹式修改）。** 每个模式维度触及的源码模块数（`grep -l`，共 74 个模块）：

| 维度 | 触及模块 | 命中行 | 触及测试文件 |
| --- | ---: | ---: | ---: |
| 后端名（native_turbo / openbox） | 27 | 510 | 36 |
| 多 testbench | 22 | 268 | 22 |
| 续跑（continuation） | 19 | 295 | 12 |
| 工艺角（process corner） | 18 | 228 | 21 |
| 远程（transport / RemoteProjectRef） | 14 | 99 | 15 |
| fix_run | 13 | 103 | 29 |
| 波形导出 | 13 | 185 | 15 |
| 历史热启动 | 11 | 128 | 12 |

版本史与之吻合（各模式核心文件的首次提交）：v0.1.4 远程 → v0.1.7 多 corner（6 个 feat 提交横穿 intake/netlist/run/aggregate/report）
→ v0.1.8 fix-run → v0.1.9 历史热启动 → v0.1.10 隔离加固。两周五个版本，每个版本一个横切模式。
intake 里甚至按模式维护两套章节表（`requirement_intake.py:51-80`）。

**P2 「评估一个点」没有唯一实现。** 至少四份：

- 本地优化：`native_turbo.py:1657 _run_default_adapter` / `:1710 _run_multi_testbench_default_adapter`
- 远程优化：`execution_adapters/remote_spectre_ocean.py:62`（单函数 361 行）/ `:504`
- 本地 fix-run：`fix_run_flow.py:299`（自带 child-run 循环）
- 远程 fix-run：`remote_fix_run_flow.py:315`（同上）

逐行相似度实测：两个后端的批量评估器工厂 **93%**（`native_turbo.py:844` vs `openbox_backend.py:1671`）；
两份 `_collect_child_runs` **91%**；fix-run 本地/远程主函数 **70%**。
副作用见附录 B：同一段 tb×corner 循环的两个分支里，一个分支的返回码检查缩进错位——复制粘贴式重复的典型代价。

**P3 每个优化后端自带整条循环。** 去重、分批、续跑、报告、热启动接入各写一遍；
`run_openbox_real_optimization` 17 个参数（16 个关键字），`_run_openbox_batches` 单函数 324 行。
热启动只有 OpenBox 能用、续跑两边各实现一次——这些「能力差异」不是算法决定的，是代码组织决定的。
一个细节很说明问题：OpenBox 后端的返回类型叫 `NativeTurboRunResult`，观测叫 `NativeTurboObservation`（各 60+ 条边）——
通用的「观测 / 轨迹 / 结果」概念早已存在，只是以其中一个后端命名、住在那个后端的文件里。

**P4 本地/远程是两套编排。** 5 个 flow 文件 + 两个 doctor（`product_doctor` 577 / `remote_doctor` 1062）。
好消息是接缝雏形已经存在：`CommandRunner` Protocol（`spectre_ocean.py:111`）、`adapter=` 注入、
`OptimizerFlowServices`（`optimizer_flow.py:105`，远程优化正是靠它复用本地管线）。重构是把这些接缝**做实**，不是从零发明。

**P5 隐式黑板。** 步骤之间靠项目目录通信：111 个产物名以字符串字面量散落在 ≥55 个模块里（`docs/refactor/analysis/file_contract_artifacts.txt`），
其中 `reports/` 下 41 个。没有任何地方声明「这一步读什么、写什么」——这正是现在**无法组合**的直接原因。
同一事实还存了三份（`ledger/experiment_ledger.jsonl`、`reports/optimizer_evaluations.jsonl`、`state/optimizer_state.json + best_candidate.json`），
于是又需要大量代码检查它们彼此一致（`optimizer_acceptance` 450 行、`_assert_optimizer_state_matches_bundle`、history manifests）。

黑板模式的另一面是**每一步都从磁盘重建 Spec**：`assert_valid_project()` 有 38 个源码调用方（图上 69 条被调用边、跨 32 个社区），
每个调用方的输入都只是 `project_dir: Path`，`ContractBundle` 从不作为参数传递。实测（`analysis/count_config_reloads.py`，
真实评估链、只把 Spectre 子适配器换成写同样产物的假件）：**评估一个候选点，全套 11 个配置 YAML 被完整重载 18 次**
（9 次 `assert_valid_project` × 每次两遍：先 validate 再 load），真实运行里 `load_adapter_context` 还会给每个 tb×corner 再加 2 次。
100 个候选 × 3 corner 的一次优化约 2,400 次重载。性能不是重点（Spectre 占主导），重点是它证明了没有任何函数拿到过「上一步的结果」。

**P6 为弱模型设计的仪式。** 本地管线 17 步（`optimizer_flow.py:165-347`）里，10 步是闸门或证据：
doctor、validate、check-project-ready、package、dry-run、preflight-health、approve、package-optimizer-task、check-optimizer-run、finalize；
真正「干活」的只有四件事，共 7 步：解析需求、导入/模板化网表、跑优化、出报告（summarize / visualize / decide）。
`EXECUTION_TASK.md`（`package.py:116` "Execution Agent Task"）是 Hermes 监督者/执行者双代理时代的遗留，现在每次运行仍在生成。
文档侧同理：2026-08-12 的文档审计在 25 份文档里查出 **204 处漂移（8 BLOCKER / 78 MAJOR）**——手写文档去描述一个单体，必然漂移。

**P7 WHAT 与 HOW 混在 `opt_requirement.md` 里。** `mode`、Optimizer Settings、Fixed Points、Waveform Exports、History Warm Start 都是「怎么跑」，
它们的组合造成 11 份模板 + 547 行模板说明。而且是两级配置：md → 渲染出 11 个 `config/*.yaml` → 再加载成 `ContractBundle`
（`requirement_intake` 1652 行 + `validate` 893 行），中间层只为「打包后不可变」的哈希仪式服务。

### 1.3 必须保住的资产（搬家，不重写）

这些代码里沉淀了真实流片环境踩出来的修复（见 `docs/audits/2026-08-10-…-ledger-cn.md` 21 节），重构只移动、不重写：

- Spectre/OCEAN 适配器内核（`spectre_ocean.py`）：网表模板化、OCEAN replay 脚本、标量解析、nil/non-finite 策略、波形 CSV、命令轨迹
- `remote_ssh.py` 的 fail-closed 探测语义（ADR-0001）、远程互斥锁
- `schemas.py` 的 pydantic 模型（直接成为 Spec）、`candidate_contract`（步长吸附/去重键）、`objective_contract`（安全表达式）、`measurement_routes`
- 多 corner 聚合策略（worst_case / all_corners）、license 探测、线程/CPU 资源上限、热启动的兼容性过滤
- 上述模块对应的测试与夹具

---

## 2. 目标架构：Block + Recipe

### 2.1 三层

```text
L3  Recipe   组合层   十几行 Python（或一条 CLI）。随包附带的 recipe = 今天的各个「模式」
─────────────────────────────────────────────────────────────────────────────────────
L2  Block    能力层   ~8 个深模块，输入/输出是声明过的类型；每个 = Python 函数 + 自动生成的 CLI + JSON Schema

      spec.load ─► Spec ─┬─► env.doctor ─► DoctorReport
                         └─► netlist.import ─► Deck
                                                 │
      points.fixed / grid / sobol /              │
      from_history ─► Points ──────────┐         │
            ▲                          ▼         ▼
            │                    sim.evaluate(Spec, Deck, Points, Executor)
       opt.suggest                          │
            ▲                               ▼
            └───────────────────────── Observations ─► analyze.best / analyze.report

      opt.optimize = suggest ⇄ evaluate 的 ~50 行循环；fix-run = points.fixed → evaluate
─────────────────────────────────────────────────────────────────────────────────────
L1  Kernel   底座     Spec 模型 · RunStore（观测表/仿真目录/步骤日志/保留策略/锁）· Executor（Local | SSH）· Diagnostics
```

### 2.2 七个设计决定

**D1 WHAT / HOW 分离。** `spec.yaml` = 原 requirement 里描述电路问题的部分（Project、Maestro Source、Process Corners、Design Variables、
Metrics、Constraints、Objective、Simulator Resources），单级 YAML，pydantic 直接加载，没有 `mode`，没有中间 `config/*.yaml`。
Optimizer Settings / Fixed Points / Waveform Exports / History Warm Start 变成 Block 的参数。提供 `ic-opt migrate opt_requirement.md` 一次性转换。

**D2 唯一的评估原语。**

```python
evaluate(spec, deck, points, executor, *, corners="all", waveforms=(), retries=1) -> Observations
```

内部负责：候选吸附/去重 → tb×corner 展开 → 渲染网表 → Spectre → OCEAN 取标量/波形 → 按策略聚合 → 目标/约束求值 → 写观测表 → 保留策略。
并发（`parallel_jobs × threads_per_run`）也只在这里。它就是今天 `evaluate_real_candidate`（`native_turbo.py:691`）那条链，从后端里搬出来、去掉四份拷贝。

补充（2026-09-22，见 `DESIGN_CN.md` 4.1）：`evaluate` 内部不是一个单块，而是**通用引擎 + 可插阶段**——引擎只管查重/缓存/并发/预算/锁/tb×corner/聚合/落观测，
阶段（render → spectre → ocean → extract）才认识 Spectre。em-opt 只是在前面多接三个阶段（pcell → emx → bind_nport），不再需要 fork。

**D3 无状态 suggest。**

```python
suggest(spec, observations, n, *, strategy, seed) -> Points
```

TuRBO（`_restore_trace_history`，`native_turbo.py:1533`）和 OpenBox（`model_replay_traces`，`openbox_backend.py:957`）今天都已能从历史重建模型，
文档也早已声明续跑是 `trace_reconstructed`。把「重建」从特例变成常态后：

- `optimize` = `while 预算未满: evaluate(suggest(...))`，一个实现，所有策略共用；
- **续跑** = 调大预算再跑一次（观测表里就是历史）；
- **热启动** = `initial=` 传入另一个项目的观测（过兼容性过滤），**对所有策略生效**，不再是 OpenBox 专属；
- **Agent 在环** = 分开调用 `suggest` 和 `evaluate`，中间可以看、可以插入自己想试的点（来源会被记录）。

代价：每批重新拟合代理模型（几百个点的 GP 是秒级，相对分钟级的 Spectre 可忽略）；候选序列与旧版不逐位一致（旧版续跑本来也不保证）。

**D4 Executor 接缝。** `Executor` 协议只有 `run / put / get / exists` 四个动作 + 环境包装（cshrc）。
两个真实适配器（本地 csh 子进程、OpenSSH）→ 这是真接缝。ADR-0001 的三条规则（只经传输访问、失败即失败、永不回退同名本地路径）写进协议契约，
由 `SshExecutor` 一处保证。`RemoteProjectRef` 不再穿透全仓库。适配器入口改为 `python -m`，去掉对源码树 `tools/` 目录的路径依赖（`native_turbo.py:1689`）。

**D5 一张观测表。** `observations.jsonl`：每行 = 一个被评估的点（参数、各 tb×corner 指标、聚合指标、目标、可行性、状态、来源、spec/网表哈希、耗时、所属步骤）。
best、进度、完成度、收敛曲线都是它的**派生视图**，随用随算、不落盘为权威状态——一致性检查代码因此整类消失。

```text
<project>/
  spec.yaml                 # WHAT，用户所有
  recipes/*.py              # HOW，用户/agent 所有（可选）
  .icopt/
    observations.jsonl      # 唯一事实表
    steps.jsonl             # Block 调用日志（参数哈希、输入输出、状态、耗时）→ 审计 + 断点续跑
    decks/                  # 导入并模板化的网表（哈希固定）
    sims/<obs>/<tb>/<corner>/   # 原始仿真目录，受保留策略管理
    reports/                # 生成物，可随时重建
```

Recipe 因此是**幂等**的：重跑同一个脚本不会重复已完成的仿真，崩溃后重跑即续跑。

**D6 护栏 = 不变量。** 内建在 `evaluate / executor` 里，agent 写什么 recipe 都绕不过去：

- 沿用现有：license 探测、单次仿真超时、优化器自身的 CPU 线程上限（`optimizer_resources.py`）、互斥锁（从远程专属扩到本地也用）
- 新增两条小不变量，用来替换审批仪式（现在并发/线程只靠 requirement 里「用户已批准」的布尔，没有机器侧上限）：
  - 资源包络：`parallel_jobs × threads_per_run` 不得超过站点上限（`~/.ic-opt/site.yaml`，由你设置，不由 agent 设置）
  - 仿真预算：spec 里的 `max_simulations` 是项目累计硬上限
- 来源记录：每个观测记下点是谁提的（策略名 / user / agent）、spec 哈希、网表哈希
- `ic-opt run recipe.py --plan`：不启动任何仿真，只打印将要执行的 Block、最大仿真数、峰值并发/线程——这是唯一的「审批点」

删除：execution package、immutable-config 哈希仪式、`supervisor_instruction.json`、两份 TASK.md、acceptance/finalize 作为强制闸门、Approval Checklist 四个布尔。
「agent 不得自行提出候选点」从**禁止**改为**记录**。

**D7 Block 自描述。** `@block` 装饰器登记名称、一句话说明、输入输出类型；`ic-opt blocks` 列清单，
`ic-opt blocks describe sim.evaluate` 打印参数 JSON Schema + 示例；单块 CLI 由签名自动生成（替代手写的 1,374 行 `cli.py` 和它的 36 个命令）。
文档从代码生成 → 204 处文档漂移这一类问题从机制上消失。

### 2.3 Block 目录

| Block | 输入 → 输出 | 吸收的现有模块 |
| --- | --- | --- |
| `spec.load` | 文件 → `Spec` | requirement_intake、validate、requirement_semantics、project_readiness、schemas |
| `env.doctor` | Spec, Executor → `DoctorReport` | product_doctor、remote_doctor、doctor_readiness、license_probe、toolchain_env、health |
| `netlist.import` | Spec, Executor → `Deck` | netlists、intake 里的 Maestro 导入、dry_run、remote_prepare（物化部分） |
| `points.*` | 参数 → `Points` | fixed_points、各初始化方法、热启动源读取 |
| `sim.evaluate` | Spec, Deck, Points, Executor → `Observations` | real_run、real_run_recovery、两个 adapter、metric_requests/results、multi_testbench_aggregation、real_result_record、result_handoff、两个 fix_run flow、两个批量评估器 |
| `opt.suggest` / `opt.optimize` | Spec, Observations → Points / Observations | native_turbo、native_turbo_history、openbox_backend、optimizer_loop/suggestion/strategy/runtime/resources、history_warm_start、continuation_flow、mock_optimizer |
| `analyze.best` / `analyze.report` | Spec, Observations → 视图 / HTML+MD | D 族 15 个模块（保留你真正在看的那几张图和结论，其余删除） |
| Kernel：`store` / `executor` | — | optimizer_artifacts、progress_state、run_retention、retention_evidence、remote_history_manifests、remote_ssh、remote_project、remote_attempt_lock、reports、diagnostics |

包结构约 20 个文件：`spec.py space.py objective.py store.py executor/{base,local,ssh}.py blocks/{doctor,netlist,points,evaluate,optimize,analyze}.py
suggesters/{turbo,openbox,random}.py sim/spectre_ocean.py recipes/*.py cli.py`。

### 2.4 Recipe 长什么样

今天的「单 testbench 优化」模式：

```python
from ic_opt import blocks as b

spec = b.load_spec("spec.yaml")
ex   = b.executor(spec)                         # 远程只改这一行：b.executor(spec, ssh_profile="lab")
b.doctor(spec, ex).require_pass()
deck = b.import_netlists(spec, ex)
obs  = b.optimize(spec, deck, ex, strategy="openbox_prf_eic", budget=30, batch=10)
b.report(spec, obs)
```

今天的 fix-run（含波形）：

```python
pts = b.points_fixed([{"F": "24", "W": "0.8u", "L": "30n", "VB_LO": "340m"}, ...])
obs = b.evaluate(spec, deck, pts, ex, waveforms=[{"name": "nf_pnoise", "testbench": "cg_nf", "expression": '...'}])
```

**今天做不到、重构后零新代码的组合：**

```python
# 1) 两段式：全局粗搜 → TuRBO 局部精修（今天：热启动是 OpenBox 专属模式）
coarse = b.optimize(spec, deck, ex, strategy="openbox_prf_eic", budget=60, name="coarse")
fine   = b.optimize(spec, deck, ex, strategy="turbo", budget=60, initial=coarse, name="fine")

# 2) 只在 TT 下优化，再对前 5 名做全 corner 签核（今天：要么全程 3 倍仿真，要么手写 fix-run requirement）
obs  = b.optimize(spec, deck, ex, strategy="turbo", budget=100, corners=["tt"])
sign = b.evaluate(spec, deck, b.best(spec, obs, k=5).points, ex, corners="all", name="signoff")

# 3) 最优点附近做单因素灵敏度
sens = b.evaluate(spec, deck, b.points_one_at_a_time(spec, center=b.best(spec, obs).point, rel=0.1), ex)
```

**需要新 Block 的需求**（例：Monte Carlo、Pareto 前沿）：各写一个文件，复用 Executor 与适配器内核，不碰 optimize / evaluate / spec。
这就是「开发新模块而不是新 workflow」的落点。

### 2.5 报告块的范围（决策 ⑦，已定）

`analyze.report` 只产出**一份**报告（`report.md` + 内容完全相同的 `report.html`，图内嵌），章节固定为：

| 章节 | 数据来源（新设计） | 备注 |
| --- | --- | --- |
| Best observed | 观测表：最佳可行点（多 corner 按策略聚合后） | 吸收现在 decision.md 的推荐点/动作/警告，不再单独出一份 decision 报告 |
| Top feasible candidates | 观测表前 5 可行点：objective、参数、指标 | |
| Constraint margins | 每条约束：limit、最好/最差裕量（及点）、通过率 | 归一化改为按约束**量纲**（用 limit 的单位尺度或指标的观测极差），不再除以 `abs(limit)`——limit=0 时现算法失效（1e31） |
| 参数重要度（SHAP） | 对观测表拟合 LightGBM → SHAP，目标 + 每个指标 | 现在依赖 OpenBox 的 `visualize_html(show_importance=True)` 产出的 JSON，只有 OpenBox 后端有；改为独立块后对所有策略生效，`shap`/`lightgbm` 仍为可选依赖，缺失时该节标 `not_available` |
| 多 corner 三节 | 策略 / 推荐点各 corner 指标 / 各 corner 失败数 | 数据全在观测表的 tb×corner 明细里 |
| Space Compression Advisory | `openbox.compressor`（r_boundary）对观测表干跑 | 仍是 advisory，不回写 spec；openbox 本就是 suggester 依赖 |

四张图：`feasible_convergence`、`constraint_margins`、`bottleneck_weighted_score`、`convergence`。三处必须顺手修（现状见 `REPORT_INVENTORY_CN.md` B 表）：

1. `convergence` 现在把带惩罚值的失败点整体裁掉，剩下的与 `feasible_convergence` 相同——改为失败点按状态着色画在底部标带（不画惩罚值），让它真正成为"全部点"视图。
2. `constraint_margins` 的归一化与上表同一处修复。
3. `bottleneck_weighted_score` 的分数模型只来自目标表达式解析；解析失败时**不再回退**到写死的 Mixer 阈值（`optimizer_insights.py:1308-1333`），而是标 `not_available`。

删除：insight 里其余 14 节（IC-native Summary、All evaluable FoM、Configured Objective Ranking、Pareto、Observed Relationships、effectiveness audit、Trustworthy Trade-Off、History Reuse 等）、
另外 3 张图、独立的 decision/final_summary/supervisor_decision/completion/acceptance/finalize/flow_run 报告、OpenBox 自带可视化 HTML、每候选点的五份单文件检查报告（信息进观测表与步骤日志）。

### 2.6 Agent 怎么用

强模型：读 `ic-opt blocks`（一屏）→ 对需要的块 `describe` → 写/改一个 recipe → `--plan` 给用户看 → 运行 → 读观测表与报告。
弱模型或不想折腾的用户：`ic-opt run optimize spec.yaml`（随包 recipe），体验与今天的单命令一致。
两者走同一套 Block，没有第二条代码路径。

---

## 3. 减法清单

| 项 | 现在 | 目标（估算） | 怎么减 |
| --- | ---: | ---: | --- |
| A 规格 | 4.2k | ~1.5k | 单级 spec；删 md→yaml 渲染层与按模式分支 |
| B 评估 | 7.2k | ~3.0k | 四份评估合一；recovery 简化为 `retries` + 失败观测 |
| C 优化 | 7.2k | ~2.5k | 一个循环 + 三个 suggester；续跑/热启动不再是代码路径 |
| D 分析 | 6.7k | ~2.0k | 一个报告生成器；派生视图不落盘 |
| E 闸门 | 4.2k | ~1.2k | 一个 doctor（经 Executor）；删 package/approvals/task_package |
| F 远程 | 3.2k | ~1.5k | SshExecutor + 物化 + 锁 + 保留；删 sha256 清单同步仪式 |
| G 编排 | 5.4k | ~0.8k | 删 5 个 flow 与手写 cli；注册表 + 自动 CLI + 兼容垫片 |
| **源码合计** | **38k / 74 模块** | **12–15k / ~20 模块** | |
| 测试 | 52k | 20–25k | flow 级测试（单文件 4,765 行、206 处 monkeypatch）换成 Block 契约测试 + FakeExecutor；适配器/规格/聚合类测试随代码搬家 |
| 模板 | 11 份 + 547 行说明 | 1 份 spec + ≤6 个 recipe | |
| 产物名 | 111 | ~10 | |
| Agent 必读 | ~1,000+ 行 | ≤150 行 + `describe` | |

纪律：**除 Kernel 起步阶段外，每个阶段结束时净行数必须为负**；估算值在每阶段结束时用实测值替换。

---

## 4. 迁移路线（绞杀者模式，旧入口全程可用）

新代码进新包 `ic_opt`；旧包 `hermes_workflow` 的模块逐步变成调用新包的薄壳，最后删除。每阶段独立可合并、可回退。

| 阶段 | 做什么 | 删什么 | 验收 |
| --- | --- | --- | --- |
| **P0 基线** | 新开发区建 venv；全量测试跑一次确认基线；做「回放执行器」（按参数回放录制的 Spectre/OCEAN 标量）→ 旧流程端到端产出的观测序列存为金标准 | — | 基线全绿；金标准可复现 |
| **P1 底座** | `Spec`（由 schemas 直接构成）、`RunStore`、`Executor`（Local + SSH 包住现有 remote_ssh）、Block 注册表与 `describe` | — | 新增 ≤1.5k 行；旧流程零改动 |
| **P2 evaluate** | 从 native_turbo / fix_run 抽出唯一评估链；两个后端与两个 fix-run 都改调它 | 四份评估拷贝、两个批量评估器 | 全测试绿；回放金标准逐行一致；**真实冒烟**（需你批准） |
| **P3 suggest/optimize** | Suggester 三实现 + 单循环；续跑/热启动改为 `initial=` | 两个后端各自的循环、continuation_flow、warm-start 的 OpenBox 专属接入 | 同种子下统计等价（非逐位）；真实冒烟 |
| **P4 Recipe + CLI** | 随包 recipes、自动 CLI、`--plan`、`migrate`、旧 `ic-opt PROJECT_DIR --real` 垫片（内部转 spec + recipe） | 5 个 flow 文件、手写 `cli.py` | 旧命令行为不变；远程隔离验收按 ADR-0001 的基准重做一次 |
| **P5 分析与仪式** | 单一报告块；派生视图 | D 族冗余、package/approvals/task_package/supervisor_decision/finalize | 你确认保留的图表与结论都在 |
| **P6 文档与发布** | 新 skill（≤150 行）、README、recipes 即示例；删旧文档与 10 份模板 | 旧文档 | 发布 0.2.0；垫片保留一个版本后删除 |

真实 Spectre/远程运行一律先征得你批准（占 license 与服务器）。

---

## 5. 风险与对策

| 风险 | 对策 |
| --- | --- |
| 搬家过程丢掉真实环境修复 | 适配器内核只移动不重写；对应测试同步搬；P2/P3/P4 各有真实冒烟闸门 |
| agent 自由度变大后打爆服务器/license | 包络与预算是 Executor/evaluate 的内建不变量，上限来自站点配置而非 recipe；`--plan` 预览 |
| 候选序列与旧版不逐位一致 | 明确为统计等价；P0 金标准只约束评估链（P2），不约束采样序列（P3） |
| 失败恢复语义简化（现 `real_run_recovery` 908 行的 assess/retry/abandon） | `retries` + 失败观测 + 惩罚值；中断的点重跑时自动补；若你依赖人工裁决重试，保留为可选块 |
| 远程权威方向改变（见 7-3） | 提供 `store.publish` 把结果同步回服务器项目目录 |
| 测试迁移成本 | flow 测试随 flow 一起删，不迁；新增的是少量 Block 契约测试 |
| 做成一个新引擎（加法） | 见第 6 节，写进验收 |

## 6. 明确不做

不做 GUI；不做 DAG 引擎、YAML DSL、表达式语言；不做插件发现框架（新 Block = 新函数 + 装饰器）；
不新增优化算法；仿真器抽象只做到两个适配器能证明的程度。

> 顺带：em-ic-opt 目前是 fork，v0.1.9→0.1.10 同步靠一份 91 条的移植指南。evaluate 接缝成形后，它有机会变成「另一个仿真适配器 + spec 扩展段」而不是 fork。
> 本方案不包含这件事，只保证不堵死这条路（仿真器相关代码全部收在 `sim/` 之后）。

## 7. 需要你拍板

| # | 决策 | 我的建议 |
| --- | --- | --- |
| 1 | 组合形式 | **Python 优先** + 自动生成的单块 CLI；暂不做 YAML recipe（真有需要再加，届时也只做顺序执行） |
| 2 | 规格格式 | 单个 `spec.yaml` 取代 md + 11 个 config yaml，提供 `migrate`；若你偏好 md 承载说明文字，可保留 md 外壳但内部仍是同一个 Spec |
| 3 | 远程权威方 | 项目（spec/recipe/观测表）放 **Controller**，Remote 只当仿真算力；需要时 `publish` 回服务器。这与 ADR-0001 的隔离规则兼容，但改变了「Remote 项目目录是权威」的现状 |
| 4 | 护栏取舍 | 按 D6：留不变量，删仪式；Approval Checklist 四个布尔删除，改为 `--plan` |
| 5 | 无状态 suggest | 采用（D3）；备选是回调式 Strategy 接缝，改动小但续跑/热启动仍是特例 |
| 6 | 包名与版本 | 新包 `ic_opt`，发布 0.2.0；旧命令垫片保留一个版本 |
| 7 | 报告范围 | **已决定（2026-09-22）**：保留 Best observed、Top feasible candidates（前 5）、Constraint margins、参数重要度（SHAP）、多 corner 三节、Space Compression Advisory；图保留 feasible_convergence、constraint_margins、bottleneck_weighted_score、convergence。其余删除。详见 2.5 节与 `REPORT_INVENTORY_CN.md` |

---

## 附录 A：证据文件

- `docs/refactor/analysis/import_layers.txt`、`import_graph.json`：包内 import 分层、扇入扇出、环
- `docs/refactor/analysis/file_contract_artifacts.txt`：111 个文件契约产物名 → 引用模块
- `docs/refactor/analysis/*.py`：复现脚本（`python3.11 <script> <repo_root> [out]`）
- `graphify-out/GRAPH_REPORT.md`、`graph.html`、`graph.json`：知识图谱，227 个文件 / 约 27 万词 → 4,862 节点 / 17,696 边 / 160 社区
  （范围：排除第三方 `vendor/` 的 597 个文件，见 `.graphifyignore`；`graphify-out/` 已在 `.gitignore` 中，不入库）。
  后续重构中用 `/graphify . --update` 增量刷新，用 `graphify query "<问题>"` 查询

## 附录 B：缺陷 —— 多 corner 的 csh 分支只检查最后一个 corner 的返回码（已复现，未修）

位置：`native_turbo.py:1765`，`_run_multi_testbench_default_adapter` 的 cshrc 分支。`if completed.returncode != 0:` 缩进在**外层** testbench 循环下，
而 `subprocess.run` 在内层 corner 循环里；上方进程内分支（`:1732`）的同一检查位置是对的。

**复现**（`analysis/test_repro_corner_rc_check.py`，一个 testbench × corners [tt, ss, ff]，让 ss 失败、ff 成功）：

| 分支 | 失败 corner | 期望 | 实测 |
| --- | --- | --- | --- |
| 进程内（`cadence_cshrc=None`） | ss（中间） | 抛 RuntimeError 含 "ss" | 通过 |
| csh（`cadence_cshrc=Path`） | ff（最后） | 抛 RuntimeError 含 "ff" | 通过 |
| csh | ss（中间） | 抛 RuntimeError 含 "ss" | **DID NOT RAISE** |

三个 corner 都仍被执行、聚合仍被调用，只是中间 corner 的非零返回码被丢弃。

**下游影响（代码核对，非猜测）**：适配器工具 `tools/run_spectre_ocean_adapter.py:41` 在仿真失败时返回 1 但**已写出**子 result manifest；
前置条件错误返回 2 且不写 manifest。两种情况聚合都会判失败（`multi_testbench_aggregation.py:287-297`，已有测试
`test_aggregate_multi_corner_real_failure_propagates`），父 manifest 状态为 failed；随后 `execute_and_check_real_candidate`
（`native_turbo.py:787-802`）按 `result_status != SUCCEEDED` 把候选判为 `real_check_failed`。所以**分类结果不受影响**。
丢失的是 `issues = real_report.issues or adapter_failure_issues`（`:790`）里的 `adapter_failure_issues`——即失败 corner 的 csh stdout/stderr
（工具打印的 `issue:` 行、Python 回溯）不会进入观测/trace/报告；观测里只剩一句 `result_status is failed`。
前置条件错误（无子 manifest）时根因文本**完全丢失**；仿真失败时根因还留在子 manifest 里，前提是 `keep_failed_runs: true`。

处置建议：重构 P2 让这段代码消失；若要在 0.1.x 线上先修，把 `:1765` 的 `if` 块缩进到内层循环即可，复现测试可直接作为回归测试。
