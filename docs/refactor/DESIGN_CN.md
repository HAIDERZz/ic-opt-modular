# IC-Opt 0.2 详细设计（实现契约）

- 上游：`REFACTOR_PLAN_CN.md`（方案与证据）、`REPORT_INVENTORY_CN.md`（报告决策）
- 决策状态（2026-09-22）：①–⑦ **全部按建议通过**。本文件是按这些决策写的实现契约，代码以此为准；改契约先改这里。
- 图（archify 生成的独立交互 HTML，浏览器直接打开；`.json` 是图源，改图先改源再 `deliver`）：
  - `diagrams/ic-opt-architecture.html` —— 目标架构：三层 + Executor 接缝 + 护栏边界
  - `diagrams/ic-opt-usage.html` —— 使用方式：写 spec/recipe → `--plan` → run → Block 依次执行 → 读报告
  - `diagrams/ic-opt-evaluate-remote.html` —— 一次 `sim.evaluate` 经 SshExecutor 的时序（决策 ③ 的落地）
  - `diagrams/ic-opt-evaluate-stages.html` —— 评估 = 通用引擎 + 可插阶段：ic-opt 与 em-opt 两条流水线共享引擎与后三段（4.1 节）
  - `diagrams/ic-opt-five-workflows.html` —— 接线矩阵：单/多 tb × 单/多 corner 四个工况接线相同（差别在 spec 与 evaluate 的参数），fix-run 只把 `opt.suggest` 换成 `points.fixed`
  - 每张图旁的 `*.visual-check.json` 是浏览器实测回执（1440×900 / 2048×1320 无溢出、字号达标）

## 1. 已定决策

| # | 决策 | 结论 |
| --- | --- | --- |
| ① | 组合形式 | Python Recipe 优先；每个 Block 自动生成单块 CLI；不做 YAML DSL / DAG 引擎 |
| ② | 规格格式 | 单个 `spec.yaml`（pydantic 直接加载）取代 `opt_requirement.md` + 11 个 `config/*.yaml`；提供 `ic-opt migrate` |
| ③ | 远程权威方 | 项目正本在 Controller；Remote 只做网表导出读取与仿真；`store.publish` 可选回传 |
| ④ | 护栏 | 保留为不变量：license、超时、线程上限、互斥锁、资源包络（站点上限）、累计仿真预算、来源记录；删除审批链，换 `--plan` |
| ⑤ | 建议器 | 无状态 `suggest(spec, observations, n)`；续跑 = 加预算再跑；热启动 = `initial=` |
| ⑥ | 包与版本 | 新包 `ic_opt`，发布 0.2.0；`ic-opt PROJECT_DIR --real/--doctor/--continue` 垫片保留一个版本 |
| ⑦ | 报告 | 六节 + 四图（`REFACTOR_PLAN_CN.md` 2.5 节） |

## 2. 包布局（实际，2026-09-22 T8 后；41 个文件 3.8k 行）

```text
src/ic_opt/
  __init__.py        __version__
  spec.py            Spec 及子模型（pydantic）；metrics 可为空（纯波形 fix-run）；fingerprint()
  space.py           吸附 / 去重键 / 网格计数 / Point
  objective.py       表达式安全求值、可行性、惩罚 → Evaluation
  observation.py     ChildResult / Observation / Observations（feasible, best, by_step, keys, points）
  store.py           RunStore：observations.jsonl（按 obs_id 序读出）、steps.jsonl、decks/ sims/ cache/ reports/、flock
  deck.py            Deck(templates[(tb, corner)], source, bundles)：save/load/fingerprint
  site.py            ~/.ic-opt/site.yaml：max_threads / max_memory_gb / cshrc；slots()
  recipe.py          Run（project, spec, store, executor, cshrc, site；jobs）、PLAN_MODE、load_run、load_recipe
  migrate.py         opt_requirement.md / config/ → Spec；recipe_command / recipe_note / write_spec
  cli.py             typer：run / blocks / describe / doctor / migrate / call；main() 含 0.1 垫片
  executor/
    base.py          Executor 协议、CommandResult、错误类型、shell_program（csh -fc）
    local.py         LocalExecutor
    ssh.py           SshExecutor（退出码契约、临时名上传、tar 流、~ 一次解析）
  sim/
    netlist.py       模板化 / 角特化 / 渲染
    ocean.py         WaveformExport、replay 脚本、标量 TSV 解析
    corner.py        aggregate(spec, children)：只看本次评估过的角
  eval/
    stage.py         Stage 协议、StageContext、StageFailure、Resources、pipeline_fingerprint
    engine.py        通用引擎（~170 行）：复用（spec_fp, pipeline_fp, key, tb×corner 集）、预算、锁、并发、聚合、落观测、保留
  stages/
    spectre_chain.py Render → Spectre → Ocean → Extract 四阶段一个文件 + spectre_pipeline()
    em_chain.py      Pcell（点级）→ Emx（点级，每器件一个，引擎按指纹缓存）→ BindNport（testbench 子链）/ Measure（device 子链）
                     + em_circuit_pipeline / em_only_pipeline；blocks.evaluate.default_pipeline 按 spec 形状选择
  em/                EM 库（不认识引擎）：pcell/（六家族几何库，搬自 em-opt）、emx.py（argv/运行/指纹）、touchstone.py（读取/校验）、
                     measure.py（S→Z、理想 balun、L/Q/k/SRF）、nport.py（网表 nport 绑定）
  suggesters/
    base.py          Proposal、Suggester 协议 propose(spec, history, n, *, seed)、penalized_objective
    turbo.py         TuRBO（从观测表按 origin 标签重建信任域）
    openbox.py       OpenBox GP/PRF/auto + EIC（重放全部观测）
    random.py        sobol / latin_hypercube / random
  blocks/
    __init__.py      @block 注册表（13 个）、describe、`ic_opt.blocks as b` 命名空间
    doctor.py  netlist.py  points.py  evaluate.py  optimize.py  analyze.py
  recipes/
    optimize.py  fix_run.py  coarse_to_fine.py  signoff.py
```

T9（2026-09-22）实现与 §4.1 的偏差：子级阶段声明 `unit`（testbench / device），一条流水线可同时含两条子链；`Stage.fingerprint(inp, ctx)` / `save(out, dir)` / `load(dir, inp, ctx)` 由引擎驱动缓存（`.icopt/cache/<stage>/<fp>/`）；`Emx` 每器件一个阶段而不是一个阶段扇出；`Resources` 只用于并发槽（无实时内存监控）；查询库/代理模型（`predict`、`stratum_gp`、`points.from_db`）**暂缓**。

与本文件原契约的偏差（代码为准）：四个 Spectre 阶段合在 `stages/spectre_chain.py`；`sim/spectre_ocean.py` 拆成 `netlist.py` + `ocean.py`；
`env.executor` 不是 Block，而是 `recipe.load_run()`；单块 CLI 是 `ic-opt call NAME`，不是 `ic-opt NAME`；Stage 没有 `input_type/output_type`
（类型靠约定，引擎只校验 point 级在前、child 级在后）；阶段缓存（fingerprint）引擎尚未消费，留给 EM 阶段；
`Observation` 用 `params/origin/fom/objective/feasible/constraint_penalty/pipeline_fingerprint`，状态含 `failed:<stage>`；
`Executor.run(command: str, *, cwd, timeout_s, cshrc)`、`get(remote, local, *, dereference=False)`；
`optimize(spec, executor, store, *, budget, batch, strategy, deck|pipeline, corners, waveforms, initial, seed, step, cshrc, parallel_jobs, failure_penalty)`；
`best(spec, observations, k) -> list[Observation]`；`hermes_workflow` 已在 T8 删除（无薄壳期）。

## 3. 核心类型

```python
class Spec(BaseModel):                       # spec.yaml 的根
    project: ProjectInfo                     # name, description
    testbenches: list[Testbench]             # ≥1；单 testbench 就是长度 1（不再有"单/多"两种形态）
    corners: list[Corner] = []               # 空 = 只跑源点 corner
    corner_policy: CornerPolicy              # objective: worst_case|nominal, constraints: all_corners|nominal
    variables: list[VariableSpec]
    metrics: list[MetricSpec]                # 每个指标声明所属 testbench 与 OCEAN 表达式
    constraints: list[ConstraintSpec]
    objective: ObjectiveSpec | None          # fix-run 项目可以没有
    simulator: SimulatorSettings             # engine, preset, output_format, threads_per_run, parallel_jobs, timeout_s, license_check, keep_failed, keep_successful
    budget: Budget                           # max_simulations: 项目累计硬上限
    def fingerprint(self) -> str             # 内容哈希，写进每条观测

class Deck(BaseModel):                       # netlist.import 的输出
    entries: dict[tuple[tb_id, corner_id | None], DeckEntry]   # 模板化网表 + 哈希 + 允许模板化的变量集
    source: MaestroSource                    # 从哪个 maestro 导出目录来的（Remote 路径也记录）

class Point(BaseModel):                      # 一个候选
    params: dict[str, str]                   # 已吸附到步长网格的字符串值（与今天一致，保证与网表文本一致）
    key: str                                 # 去重键
    origin: str                              # "user" | "recipe:<name>" | "suggest:<strategy>" | "agent"

class Observation(BaseModel):                # 观测表一行
    obs_id: str                              # 序号型 id（取代 real_NNN）
    point: Point
    children: dict[str, ChildResult]         # "<tb>/<corner>" -> 指标、状态、issues、sim 目录、耗时、命令轨迹路径
    metrics: dict[str, float]                # 聚合后的指标
    objective: float | None
    feasible: bool | None
    status: Literal["ok","sim_failed","metric_failed","constraint_failed","skipped"]
    spec_fingerprint: str; deck_fingerprint: str
    step: str                                # 所属 Recipe 步骤名
    started_at, finished_at

Observations = list[Observation]  +  便捷方法：feasible(), best(k), by_step(name), to_frame()
```

### Executor 协议

```python
class Executor(Protocol):
    host: str                                                    # "local" 或 ssh profile 名
    def run(self, argv: list[str], *, cwd: str, timeout_s: int, env: EnvWrapper) -> CommandResult
    def put(self, local: Path, remote: str) -> None
    def get(self, remote: str, local: Path) -> None
    def exists(self, remote: str) -> bool                        # 只有 0/1 两种答案；其他退出码抛 TransportError
    def scratch(self, key: str) -> str                           # 远端仿真工作目录，按 key 稳定
```

契约（ADR-0001 原样迁入，由 `SshExecutor` 一处保证）：Remote 路径只经这五个方法访问；传输失败抛错，永不降级为"文件不存在"；永不回退到同名本地路径。`LocalExecutor` 的 `put/get` 是复制，`scratch` 是 `.icopt/sims/`。

### RunStore

```text
<project>/
  spec.yaml
  recipes/*.py                     可选
  .icopt/
    observations.jsonl             唯一事实表（追加写；一行一个 Observation）
    steps.jsonl                    每次 Block 调用：名称、参数哈希、输入指纹、输出摘要、状态、耗时
    decks/<fingerprint>/           import 出来的模板网表
    sims/<obs_id>/<tb>/<corner>/   原始仿真目录（Remote 时只拉回标量、日志、指定波形；raw PSF 留远端按保留策略清理）
    reports/                       report.md / report.html / 四张图（可随时重建）
    lock                           项目级互斥（本地 flock；远程 scratch 目录 mkdir 锁）
```

幂等规则：`evaluate` 对 `(spec_fingerprint, deck_fingerprint, point.key, corners)` 已有 `ok` 观测的点直接复用，不重跑；Recipe 重跑即续跑。

## 4. Block 接口

| Block | 签名 | 不变量 / 备注 |
| --- | --- | --- |
| `spec.load` | `load_spec(path) -> Spec` | 校验 = 今天 `validate.py` 的契约检查（变量网格、表达式安全、指标路由、资源字段） |
| `env.executor` | `executor(spec, *, ssh_profile=None) -> Executor` | 读 `~/.ic-opt/site.yaml` 取站点上限与 cshrc 发现规则 |
| `env.doctor` | `doctor(spec, ex) -> DoctorReport` | 工具链可见性、license 探测、资源包络（`parallel_jobs×threads_per_run ≤ site.max_threads`）、预算余量；`require_pass()` |
| `netlist.import` | `import_netlists(spec, ex, store) -> Deck` | 经 `ex` 读 Maestro 导出目录，模板化允许的变量，写 `decks/`；不变量：只允许模板化 spec 声明的变量（今天的 `forbidden_setup_changes` 检查） |
| `points.*` | `points_fixed(rows)` `points_grid(spec, per_dim)` `points_sobol(spec, n, seed)` `points_one_at_a_time(spec, center, rel)` `points_from(observations, k)` | 都返回 `list[Point]`，都经 `space.snap()` |
| `sim.evaluate` | `evaluate(spec, points, ex, store, *, pipeline=None, corners="all"\|list, waveforms=(), retries=1, name=None) -> Observations` | 薄封装：`pipeline` 缺省为 `pipelines.spectre(deck)`；实际工作在 `eval.engine`（见 4.1 节）。并发 = `spec.simulator.parallel_jobs`（候选级）再受站点包络与阶段 `resources` 约束；护栏：license、超时、线程上限、预算（超出即抛 `BudgetExceeded`，已完成的观测保留）、锁 |
| `opt.suggest` | `suggest(spec, observations, n, *, strategy, seed, initial=()) -> list[Point]` | 无状态：每次由 `observations + initial` 重建模型；`initial` 经兼容性过滤（变量集/边界/指标名一致，今天 `history_warm_start` 的规则） |
| `opt.optimize` | `optimize(spec, deck, ex, store, *, strategy, budget, batch, corners="all", initial=(), seed=None, name=None) -> Observations` | `while 本步骤观测数 < budget: evaluate(suggest(...))`；去重由 `suggest` 内对 `observations` 的键集合保证 |
| `analyze.best` | `best(spec, observations, k=1) -> Ranking` | 按 corner_policy 聚合后的可行点排序；`.point` / `.points` |
| `analyze.report` | `report(spec, observations, store) -> Path` | 六节四图；SHAP 用 `shap+lightgbm`（可选依赖），Space Compression 用 `openbox.compressor` |

### 4.1 评估引擎与阶段（回应"sim.evaluate 是不是一个大单块"）

不是。`sim.evaluate` 拆成**一个通用引擎 + 一条可插的阶段流水线**。引擎不认识 Spectre，也不认识 EMX；
它对每个点执行流水线，管的是所有应用都一样的事。这样"适配新应用"改的是阶段，不是引擎。

```python
class Stage(Protocol):
    name: str                                  # "render" / "spectre" / "emx" ...
    level: Literal["point", "child"]           # point 级每个点跑一次；child 级每个 tb×corner 跑一次
    input_type: type; output_type: type        # 相邻阶段类型必须匹配（pipeline 构造时检查，像 GNU Radio 的端口类型）
    resources: Resources                       # threads / memory 每次调用的占用；引擎据此在站点包络内算并发槽
    def fingerprint(self, inp) -> str          # 输入指纹；cacheable 的阶段按 (name, fingerprint) 复用产物
    def run(self, inp, ctx: StageContext) -> out   # ctx: executor, spec, store, obs_id, tb_id, corner_id, workdir
    # 失败抛 StageFailure(issues)；引擎记为 status="failed:<name>"，后续阶段不再执行

Pipeline = list[Stage]      # 引擎校验：point 级在前、child 级在后，类型首尾相接
```

引擎（`eval/engine.py`，约 300 行）对每个点做的事，顺序固定：

1. 查重：`(spec_fp, pipeline_fp, point.key, corners)` 已有 `ok` 观测 → 直接复用
2. 护栏：预算余量、站点包络、项目锁、license（一次）
3. point 级阶段依次执行（可缓存阶段先查 `.icopt/cache/<stage>/<fp>/`）
4. 对每个 tb×corner 执行 child 级阶段 → `ChildResult`
5. 按 `corner_policy` 聚合 → 指标、目标、可行性 → 追加 `Observation`
6. 保留策略（本地或远端）

两条随包流水线：

| 应用 | 流水线 | 与今天的对应 |
| --- | --- | --- |
| ic-opt | `render → spectre → ocean → extract` | `evaluate_real_candidate` 那条链，拆成四个有类型的阶段 |
| em-opt | `pcell → emx → bind_nport → spectre → ocean → extract` | M2 = pcell，EM artifact + `EmCacheStore` = emx 的阶段缓存，M3 = bind_nport，M4 = 共享的后三段 |

em-opt 今天是 fork：复制了 ic-opt 74 个模块中的 71 个（36 个逐字相同），编排类文件分叉 25–65%
（`remote_optimizer_flow` 0.32、`validate` 0.39、`requirement_intake` 0.65），两个优化后端各再包一层
（`em_optimizer_evaluator.py` 912 行），同步靠 91 条移植指南。重构后它是：3 个阶段文件、spec 里两个可选段
（`devices`：器件族/生成器、几何变量映射、每个 testbench 的 nport 实例与端口序；`em`：EMX 设置与频率扫描）、
一行流水线定义。查询库/逆向推荐不进评估链，是另一个 Points 源（`points.from_db`）或 Suggester。

Recipe 里的写法（默认不用碰，spec 里有 `devices` 段时 `evaluate` 自动选 em 流水线）：

```python
pipe = b.pipelines.em(spec)            # 或手工：b.pipeline(b.stages.pcell(spec), b.stages.emx(spec), b.stages.bind_nport(spec), *b.stages.spectre_chain(spec))
obs  = b.optimize(spec, ex, store, strategy="openbox_gp_eic", budget=60, pipeline=pipe)
```

资源包络也随之变成阶段声明：`emx` 声明 `threads=4, memory_gb=32`，引擎在站点上限（如 128 线程 / 256 GB）内算出并发槽，
不再需要 em-opt 单独的 `emx_resource_guard.py`。

注册：

```python
@block("sim.evaluate", summary="Run Spectre/OCEAN on points and append observations")
def evaluate(...): ...
```

`ic-opt blocks` 列表；`ic-opt blocks describe sim.evaluate` 打印签名、参数 JSON Schema（由类型注解生成）、一段示例；`ic-opt sim.evaluate --spec spec.yaml --points pts.json ...` 由签名自动生成。

## 5. Recipe 与 CLI

```python
# recipes/optimize.py —— 随包，= 今天的 optimize 模式
from ic_opt import blocks as b
def main(project, *, strategy="openbox_prf_eic", budget=30, batch=10, ssh_profile=None):
    spec  = b.load_spec(project / "spec.yaml")
    store = b.store(project)
    ex    = b.executor(spec, ssh_profile=ssh_profile)
    b.doctor(spec, ex).require_pass()
    deck  = b.import_netlists(spec, ex, store)
    obs   = b.optimize(spec, ex, store, strategy=strategy, budget=budget, batch=batch)   # deck 由默认流水线 pipelines.spectre(spec, store) 取
    b.report(spec, obs, store)
```

CLI 面（全部）：

```text
ic-opt run <recipe.py|内置名> <project> [--plan] [recipe 参数]   运行 Recipe；--plan 只打印将执行的 Block、最大仿真数、峰值并发/线程，不启动仿真
ic-opt blocks [describe <name>]                                  Block 清单 / 单块说明
ic-opt call <block.name> <project> key=value ...                 单块 CLI（spec/executor/store/observations 自动注入）
ic-opt doctor <project> [--ssh-profile P]
ic-opt migrate <old_project_dir> <new_project_dir>               opt_requirement.md + config/ → spec.yaml（+ 把 optimizer/fixed_points/waveform 段落写成 recipe 参数提示）
ic-opt <project> --real|--doctor|--continue N [--ssh-profile P] 0.1 垫片：内部转 migrate + run optimize/fix_run；0.2.x 保留一版
```

`--plan` 是唯一的"审批点"：它输出的内容（Block 序列、`max_simulations` 余量、`parallel_jobs×threads`、涉及的 Executor 主机）就是今天 Approval Checklist 四个布尔想确认的事。

## 6. 旧模块 → 新位置

| 旧（hermes_workflow） | 去向 |
| --- | --- |
| schemas, validate(契约检查部分), requirement_semantics, objective_contract, candidate_contract, measurement_routes, fix_run_models(模型部分) | `spec.py` / `space.py` / `objective.py`（搬） |
| requirement_intake, project_readiness, validate(文件加载部分), package, optimizer_task_package, approvals, health, dry_run | **删**；`migrate` 只保留 md→yaml 的解析器 |
| spectre_ocean（渲染/argv/解析/波形/命令轨迹）, netlists | `sim/spectre_ocean.py` / `blocks/netlist.py`（搬） |
| real_run, real_run_recovery, real_result_record, result_handoff, metric_requests, metric_results, multi_testbench_aggregation, remote_spectre_ocean, fix_run_flow, remote_fix_run_flow | `blocks/evaluate.py` + `sim/corner.py`（一份实现；recovery 缩为 `retries`） |
| native_turbo, native_turbo_history, openbox_backend, history_warm_start, optimizer_loop, optimizer_suggestion, optimizer_strategy, optimizer_runtime, optimizer_resources, optimizer_progress_state, optimizer_artifacts, optimizer_trace_identity, optimizer_continuation_flow, mock_optimizer | `suggesters/*` + `blocks/optimize.py`（线程上限进 `evaluate`；进度/产物/身份检查删） |
| optimizer_insights, optimizer_html_report, optimizer_space_advisory, optimizer_decision | `blocks/analyze.py`（只留六节四图） |
| optimizer_acceptance, optimizer_completion, optimizer_finalize, optimizer_final_summary, optimizer_supervisor_decision, optimizer_effectiveness, optimizer_tradeoffs, optimizer_report_interpretation, optimizer_status, optimizer_trace_science, reports | **删** |
| product_doctor, remote_doctor, doctor_readiness, license_probe, toolchain_env | `blocks/doctor.py`（一个 doctor，经 Executor） |
| remote_ssh, remote_project, remote_attempt_lock | `executor/ssh.py` + `store.py`（锁） |
| remote_prepare, remote_history_manifests, retention_evidence, run_retention, remote_optimizer_flow | `store.py` 的保留策略 + `publish`；其余**删** |
| cli, product_cli, optimizer_flow, diagnostics | `cli.py` + `blocks/__init__.py`（注册表、自动 CLI）；diagnostics 结构化错误码保留在 `cli.py` |

## 7. 迁移阶段（细化 P0）

P0 基线（本阶段不改产品代码）：

1. 新开发区建 venv：`uv venv --python 3.11 .venv && uv pip install -e '.[dev]'`（torch/gpytorch 约 2 GB，一次）
2. 全量 pytest 跑一次记录基线（1757 绿是 0.1.10 发布时的数字，以实跑为准）
3. **回放执行器**：`tests/replay/` —— 用磁盘上真实运行的 `runs/real/*/{result_manifest,metric_result_manifest}.json` 录制（样本：`<recorded-run 802f8b44>/`，80 点单 tb；`<recorded-run second_batch_20260810>/`，100 点 3tb×3corner），做一个按 `(point, tb, corner)` 返回录制标量的 `ReplayExecutor`；旧流程在它上面端到端跑出的观测序列存为金标准
4. 金标准只约束 P2 的评估链（同一点集 → 同样的聚合指标/目标/可行性），不约束 P3 的采样序列

P1–P6 同 `REFACTOR_PLAN_CN.md` 第 4 节。每阶段结束：净行数为负、定向测试绿、金标准回放一致；P2/P3/P4 各一次真实冒烟（需你批准，遵守 `~/.ic-opt/site.yaml` 上限）。

## 8. 测试策略

- Block 契约测试：每个 Block 一个测试文件，用 `FakeExecutor`（内存文件系统 + 录制命令）和 `ReplayExecutor`
- 搬家的内核（渲染、解析、聚合、SSH 语义、锁）沿用现有测试，随代码移动
- flow 级测试（5 个 flow 文件对应的约 9,000 行）随 flow 删除，不迁
- 新增门禁：`tests/test_block_registry.py`（每个 Block 都能 `describe`、CLI 自动生成、参数 schema 有效）
