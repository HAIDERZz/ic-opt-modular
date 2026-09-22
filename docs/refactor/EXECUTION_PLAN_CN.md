# 执行计划（ic-opt 0.2 重构）

- 上游：`REFACTOR_PLAN_CN.md`（为什么）、`DESIGN_CN.md`（做成什么样，实现契约）
- 总路线（2026-09-22 用户定）：**① 先完成 ic-opt 重构 → ② 验证功能 → ③ 把 em 部分做成阶段模块并入**
- 开发方针（用户定）：结构优雅、高效；**核心完成前不做防御性/保护性设计**

## 0. 方针的具体含义

- 新包 `src/ic_opt/` 旁路新建，旧包 `hermes_workflow` **原封不动**直到切换（T8 整体删除）。不做"旧模块改薄壳"这种中间态。
- 能搬的内核（网表渲染、OCEAN 脚本与解析、多 corner 聚合、步长吸附、目标表达式、SSH 语义、TuRBO/OpenBox 接入）**只搬不改**；搬的时候去掉它们对项目目录/manifest 的依赖，改成收参数、返回值。
- 不写：多层校验、manifest 互检、验收/完成度报告、审批链、结构化错误码体系、重试状态机。错误直接抛，`Observation.status` 记 `failed:<stage>` 即可。
- 只写行为测试：每个 Block 一个测试文件，用 `FakeExecutor`；搬来的内核带着它原来的测试一起搬。不为覆盖率写测试。
- 每个任务结束：新代码 `ruff` 干净、该任务的测试绿、能被下一任务直接调用。不跑旧包全量测试（只在 T0 跑一次作基线）。
- 真实 Spectre / 远程运行只在 T8 做，先征得批准。

## 1. 任务序列

| # | 任务 | 产出（`src/ic_opt/` 下） | 完成判据 |
| --- | --- | --- | --- |
| T0 | 环境与基线 | `.venv`（uv，torch CPU，`-e .`，`-e vendor/open-box`）；旧包全量 pytest 跑一次记录数字 | `import ic_opt` 可用；基线数字写进本文件 §3 |
| T1 | 核心类型 | `spec.py`（Spec 及子模型，从 `schemas.py` 搬，去掉 config 文件划分；`load_spec`）、`space.py`（吸附/去重键/网格计数）、`objective.py`（表达式求值、可行性、惩罚）、`store.py`（RunStore：observations.jsonl / steps.jsonl / 目录 / flock） | `tests/ic_opt/test_spec.py` 等四个测试绿；`examples/spec.yaml` 能加载 |
| T2 | Executor | `executor/base.py`（协议、CommandResult、EnvWrapper）、`local.py`（csh -fc）、`ssh.py`（搬 `remote_ssh.py` 的 run/put/get/exists 语义） | 本地：真子进程测试；SSH：搬 `test_remote_ssh.py` 里退出码语义用例 |
| T3 | Spectre 阶段 | `eval/stage.py`（Stage 协议、StageContext、StageFailure）、`stages/render.py`、`spectre.py`、`ocean.py`、`extract.py`（从 `spectre_ocean.py` + `netlists.py` 搬） | 渲染/argv/脚本/解析的原测试搬过来绿 |
| T4 | 评估引擎 | `eval/engine.py`（~300 行：查重、阶段缓存、并发/包络、预算、锁、tb×corner、聚合、落观测、保留）、`sim/corner.py`、`blocks/evaluate.py`、`pipelines.py`（`spectre(spec, store)`） | `FakeExecutor` 跑通 1 tb 与 3 tb×3 corner；**回放测试**：对 80 点真实运行逐点复算，聚合指标/目标/可行性与旧结果一致 |
| T5 | 建议器与循环 | `suggesters/base.py`、`turbo.py`（BatchTurbo1 + 从观测重建）、`openbox.py`（advisor + replay）、`random.py`；`blocks/optimize.py`（suggest / optimize） | 假评估函数下：去重、预算停止、`initial=` 热启动、重跑即续跑 |
| T6 | 其余 Block | `blocks/doctor.py`、`netlist.py`（Maestro 导出 → Deck）、`points.py`、`analyze.py`（best + 六节四图，含三处修复） | 各一个行为测试；用真实运行的观测表回放出报告 |
| T7 | 注册表 / CLI / Recipe | `blocks/__init__.py`（`@block`、describe、自动 CLI）、`cli.py`（run / --plan / blocks / migrate）、`recipes/`（optimize / fix_run / coarse_to_fine / signoff）、`migrate`（opt_requirement.md → spec.yaml） | `ic-opt blocks describe sim.evaluate` 有输出；11 个旧模板都能 migrate；`ic-opt run optimize --plan` 打印正确 |
| T8 | 验证与切换 | 真实冒烟（本地 + SSH，需批准）；删 `hermes_workflow`、旧 tests、旧 docs；0.1 垫片；版本 0.2.0；skill ≤150 行 | 冒烟 PASS；仓库只剩新包；净行数为负 |
| T9 | em 阶段（第二阶段，另行规划） | `stages/pcell.py`、`emx.py`、`bind_nport.py`，spec 的 `devices` / `em` 段，`pipelines.em` | 用 em-opt 的真实运行做回放 |

T1–T7 每个任务一个提交（在 `refactor/modular-blocks` 分支）。

## 2. 关键搬运清单（只搬不改）

| 去处 | 来源（`hermes_workflow/`） | 要剥掉的东西 |
| --- | --- | --- |
| `spec.py` | `schemas.py` 全部模型；`validate.py` 的 `variable_contract_issues` / `optimizer_contract_issues` / 表达式检查 | config 文件名、`ContractBundle`、从目录加载 |
| `space.py` | `candidate_contract.py`；`mock_optimizer.py` 的 `generate_integer_grid` / `generate_continuous_grid` | — |
| `objective.py` | `objective_contract.py`；`native_turbo.py::evaluate_candidate_objective` | `NativeTurbo*` 命名 |
| `executor/ssh.py` | `remote_ssh.py`（`RemoteSshRunner`、`quote_remote_path`、`require_boolean_probe`） | `RemoteProjectRef`、缓存目录概念 |
| `stages/render.py` | `netlists.py` 模板化；`real_run.py::_write_single_testbench_package` 里的渲染部分 | manifest、supervisor 哈希、run 目录布局 |
| `stages/spectre.py` / `ocean.py` / `extract.py` | `spectre_ocean.py` 的 `build_spectre_argv`、`build_ocean_argv`、`render_ocean_replay_script`、`_render_waveform_export_lines`、`parse_ocean_scalars`、nil/non-finite 策略 | `SpectreOceanContext`、三种 manifest 写入、命令轨迹落盘（改为返回值） |
| `sim/corner.py` | `multi_testbench_aggregation.py` 的策略部分（worst_case / all_corners / nominal） | 子 manifest 读取、聚合报告写入 |
| `suggesters/turbo.py` | `native_turbo.py::_default_batch_turbo_factory`、`_initial_unit_design`、`_restore_trace_history` | 评估器、报告、run 目录 |
| `suggesters/openbox.py` | `openbox_backend.py::_create_advisor`、`_build_openbox_space`、`_prepare_unique_batch`、replay 逻辑；`history_warm_start.py` 的兼容性过滤 | 批循环、报告、可视化 |
| `blocks/doctor.py` | `license_probe.py`、`toolchain_env.py` 的探测 | 两个 doctor 的报告体系 |
| `blocks/analyze.py` | `optimizer_insights.py` 里六节对应的计算与四张图的绘制 | 其余 14 节、HTML 拼装、Mixer 写死阈值 |

## 3. 基线记录

- T0（2026-09-22）：`.venv` = uv + Python 3.11.15；torch 2.12.0+cpu、gpytorch 1.15.2、openbox 0.9.0（`-e vendor/open-box`）、numpy 1.26.4、scipy 1.12.0、pydantic 2.13.5、shap 0.49.1、lightgbm 4.6.0；**pyrfr 未装**（本机无 swig，构建失败）→ `openbox_prf_eic` 策略在新 venv 不可用，GP 可用。
- 旧包全量 pytest：**1762 passed, 13 warnings, 74.7 s**（`.venv/bin/python -m pytest -q`）。
- T1（2026-09-22）：`spec.py` / `space.py` / `objective.py` / `observation.py` / `store.py` 共 766 行；`tests/ic_opt/` 9 个测试绿；`examples/spec.yaml` 由 multi_tb_corner 模板改写。
- T2（2026-09-22）：`executor/{base,local,ssh}.py`；退出码契约（255 传输 / 126,127 不可用 / exists 只认 0,1）、临时名 + mv 上传、tar 流目录传输；6 个测试。
- T3（2026-09-22）：`sim/netlist.py`（模板化/角/渲染内核搬入）、`sim/ocean.py`（replay 脚本 + 标量解析，TSV 表头简化为 metric/value/unit/status/message）、`deck.py`、`eval/stage.py`（Stage 协议）、`stages/spectre_chain.py`（render/spectre/ocean/extract 四阶段，一个文件）；9 个测试，含假执行器跑通整条链。
- T4（2026-09-22）：`sim/corner.py`（聚合策略）、`eval/engine.py`（约 170 行：查重复用、预算、锁、并发、tb×corner、聚合、落观测、保留）、`blocks/evaluate.py`、`migrate.py`（config 目录 / opt_requirement.md → Spec，回放需要）；**回放金标准通过**：802f8b444b991da2（3 tb × nominal，80 点）与 second_batch_20260810（3 tb × 3 corner，100 点）逐点状态 / fom / 约束惩罚 / 目标 / 指标全部一致。新包 2,142 行 / 测试 673 行 / 32 个测试绿。
- 偏离设计文档的小决定：四个 Spectre 阶段放在 `stages/spectre_chain.py` 一个文件（内聚、共 ~180 行）；`Observation` 类型放 `observation.py`；阶段缓存（fingerprint）先不在引擎里消费，等 EM 阶段再加。
- T5（2026-09-22）：`suggesters/{base,random,turbo,openbox}.py` + `blocks/optimize.py`（`suggest` / `optimize` / `adopt`），共 453 行。无状态：TuRBO 每次从观测表重建信任域（origin 里的 `init:<r>:<k>` / `tr:<r>:<k>` 标签让下次调用能按批回放 `_adjust_length`；同批的替补点共用标签）；OpenBox 每次重放全部观测进 advisor（GP/PRF + EIC，约束残差 ≤ 0 可行）。`optimize` 只看"本步骤已有多少观测"，重跑即续跑，加预算即继续；`initial=` 的外部观测按本 spec 重新打分后并入。TuRBO/GP 的随机性用 seed 固定（numpy + torch），三种策略 12 点内都找到合成碗底。7 个测试，累计 39 绿。pyrfr 未装 → `openbox_prf_eic` 在本机不可测。
- T6（2026-09-22）：`site.py`（站点包络，默认 128 线程 / 128 GB，`~/.ic-opt/site.yaml` 可覆盖）、`blocks/netlist.py`（经 Executor 拉取导出目录、解引用符号链接、模板化 + 逐角特化、支持文件随 Deck 保存并在 render 阶段带进每个仿真目录）、`blocks/doctor.py`（executor / 工具 / lmstat license / 导出目录存在 / 线程包络 / 预算六项检查 + `plan_line`）、`blocks/points.py`（fixed / sobol / grid / one_at_a_time / from_observations）、`blocks/analyze.py`（六节 + 四图，SHAP 直接用 lightgbm+shap 对观测表算，瓶颈图只在目标表达式解析成"瓶颈 + 加权和"时生成、无 Mixer 回退，约束裕量按观测极差归一化，convergence 失败点画在状态带不再裁剪）。Executor.get 新增 `dereference`。7 个测试（含用真实 80 点记录生成报告），累计 46 绿。
- T7（2026-09-22）：`blocks/__init__.py`（`@block` 注册表，13 个 block，`describe` 打印签名 + 文档）、`recipe.py`（`Run` 上下文：project / spec / store / executor / cshrc / site，`jobs` = spec 并发被站点包络截断；`PLAN_MODE` contextvar；`load_run` / `load_recipe` 内置名或 `.py` 文件）、`recipes/{optimize,fix_run,coarse_to_fine,signoff}.py`（每个 ≤ 20 行，只是 block 的组合）、`cli.py`（typer：`run` / `--plan` / `blocks` / `describe` / `doctor` / `migrate` / `call`）、`migrate.recipe_note`（fix_run 模板写出 points.json / waveforms.json）。`--plan` 是唯一审批点：doctor 打印检查但不拦截预览，netlist.import / sim.evaluate / opt.optimize 打印形状与仿真数（含 spec 预算）后返回空。**11 个旧模板全部 migrate 通过**；四个内置 recipe 在假 Cadence 主机上端到端跑通。顺手修的三处：`Spec.metrics` 允许为空（纯波形 fix_run 合法）；波形导出返回 nil 记为 `failed:extract` issue；**引擎复用观测必须 tb×corner 集合一致，聚合器只看本次评估过的角**（否则 signoff 的"单角搜索 → 全角复核"会直接复用单角观测）。OpenBox 日志降到 WARNING。22 个新测试，累计 68 绿，ruff 干净。
- T8（2026-09-22）：**真实冒烟 PASS（本地 + SSH）**，随后切换。冒烟工程由记录运行 802f8b444b991da2（`insight_remote_mt80_multi_tb`，3 tb × nominal）`ic-opt migrate` 得到，取两个记录点（real_023 可行 / real_002 违约）跑 `fix_run`，3 并发 × 10 线程 = 30 线程峰值（用户上限 128 线程 / 128 GB）：
  - 本地：`<repo>/smoke/t8_local/`（日志 `smoke/t8_local_run.log`），6 次仿真 83 s；两点 BW / MAX_GAIN / NF_3G / IIP3 / P1DB、fom、约束惩罚与 0.1.10 记录**逐位一致**（rel 0.00e+00），状态 ok / constraint_failed 一致。
  - SSH：`--ssh-profile <user>@<lab-host>`（本机自 SSH，走真实 ssh/scp/tar 路径；新包对远端路径只经 Executor，ADR-0001 的"自 SSH 掩盖"风险不存在），`smoke/t8_ssh/`（日志 `smoke/t8_ssh_run.log`），89 s，指标同样逐位一致；远端 scratch `~/.ic-opt/scratch/t8_ssh_smoke/`（psf 按 keep_successful_runs 保留，32 MB）。
  - 冒烟暴露并修掉的两处：doctor 的 license 规则按旧探针对齐（`spectre -V` 成功 + lmstat 列出特性即通过；本站没有 `Spectre_*` 特性名）；`SshExecutor.scratch` 的 `~` 解析不是线程安全的（3 个并行 job 拼出 `$HOME + "home/..."`）→ 属性 + 锁，一次解析，加并发测试。另：观测表按完成顺序追加（崩溃安全）、读出按 obs_id 排序（并行下顺序确定）。
  - 切换：删除 `src/hermes_workflow`（104 文件）、旧 `tests/test_*.py`（78 文件）+ 旧夹具、10 份旧文档、`docs/audits`、`tools/`、`.mcp.json`、`CONTEXT.md`、旧发行说明、`requirements-*.txt`、`VERSION`；11 份旧模板移到 `tests/ic_opt/fixtures/legacy/` 作迁移夹具；`examples/` 只剩 `spec.yaml` + `sweep.py`；保留 `docs/adr/0001`。0.1 垫片：`ic-opt PROJECT --real|--doctor|--continue N [--dry-orchestration] [--ssh-profile P] [--cadence-cshrc F]` 在 `cli.main()` 里翻译成 0.2 命令并就地迁移（打印翻译后的命令），在记录工程上验证通过。版本 0.2.0（`pyproject` + `__version__`），入口 `ic-opt = ic_opt.cli:main`，删 `hermes-workflow` 入口。README 101 行、SKILL 65 行、CONTRIBUTING、`RELEASE_NOTES_v0.2.0.md`。`DESIGN_CN.md` §2 改为实际布局并列出偏差。
  - 计数：新包 41 文件 / 3,806 行，测试 1,148 行 / 70 绿，ruff 干净；仓库非 vendor 文件 98 个。基线 0.1.10 是 38,075 + 52k 行 → 净行数为负。
- T8 补充（2026-09-22，用户追问"自 SSH 验证是否掩盖了假 remote"）：
  - 静态审计：新包所有本地文件操作只作用于 Controller 自己的目录（`.icopt/`、staging、deck、site.yaml、vendor）；远端路径只经 `Executor` 五个方法。唯一"同路径两侧"假设是 `recipe._find_cshrc`（本地探测 `project/cadence_env.csh` / `~/.ic-opt/cadence_env.csh` 存在后拿去远端 source）→ **删除**，cshrc 只能显式给出（`--cshrc` / `IC_OPT_CADENCE_CSHRC` / `site.yaml`），错了由 doctor tools 检查 fail-closed。
  - **隔离验收 PASS**：`docs/refactor/analysis/isolated_ssh_smoke.sh` 把 Controller 放进 bwrap 沙箱（`$HOME/simulation`、`/opt/eda`、`~/.ic-opt` 为空 tmpfs，cshrc 绑成空文件，`/etc/ssh/ssh_config.d` 隐藏以绕过用户命名空间下 ssh 的属主检查），沙箱内本地 doctor 对 tools / license / 三个 export 全 FAIL，同一工程 `--ssh-profile <user>@<lab-host>` 全 ok 并跑完 6 次仿真（92 s），两点指标 / fom / 惩罚与 0.1.10 记录逐位一致；Controller 侧 `sims/` 只有 netlist / metrics / 日志，psf 只在远端 scratch。工程 `<repo>/smoke/t8_ssh_isolated/`，日志 `smoke/t8_ssh_isolated_run.log`。bwrap 两个坑：`--ro-bind /dev/null FILE` 在 nodev 下不可读（用空普通文件）；ssh 在 userns 里拒绝 root 属主的 include。
  - `docs/adr/0001` 按 0.2 的接缝与验收改写（决策不变）。
- T9 规划（2026-09-22）：`docs/refactor/T9_EM_PLAN_CN.md`（规划稿，待批准）+ `analysis/em/01–04`（四份子代理源码研读，共 2,780 行，全部带行号；站点路径已用占位符替换）。规划要点：EM = 三个阶段（pcell / emx / bind_nport）+ 两个器件级子阶段（measure / predict）接到现有 Spectre 三段；引擎只加两条子链、点级缓存、资源槽三件通用能力；器件库 = 观测表工程；逆向推荐 = `pcell → predict` 流水线上的优化 + `signoff`。研读发现的两个硬约束：六家族几何代码自述为 GPL SKILL 衍生作品（`02` §10.6）→ 建议单独成插件包而非并入 MIT 公开仓库；em-opt 的 Passive Diagnostic Constraints 在运行期什么都不算（`01` §5）→ 新设计里器件量真正参与约束。待用户拍板 5 项（§8）。
- T9.1（2026-09-22）：引擎与 spec 的通用扩展。`spec.py`：`devices`（生成器 id / plugin / profile / ports / fixed / variables 映射 / topology 缺省推导）、`em`（EMX 设置，`process_file` 必须绝对路径，`extra_args` 禁止夹带资源字段）、`bindings`、`Metric.device+quantity(+frequency_hz)` 与 `expression` 二选一；变量名允许 `<device>.<field>`；`device_fields`（显式映射 > 前缀 > 单器件裸名）、`circuit_variables`（无器件消费的变量才进网表）。`eval/engine.py`：子级阶段按 `unit`（testbench / device）分成两条子链、`children_of` / `workers_for`（最重阶段的 threads/memory 在站点包络内算槽）、点级阶段按 `fingerprint` 经 `.icopt/cache/<stage>/<fp>/` 缓存（引擎拥有缓存，阶段提供 `save/load`；临时目录 + 原子 rename，先写者胜）、`Observation.cache`；`ChildResult.testbench` → `unit`；聚合器把无角子结果（器件量）并进每个角。`Spectre` 补回瞬时 socket 失败重试一次。`sim.evaluate` / `opt.optimize` 的 `--plan` 行改为按流水线打印"每点子单元数 + 工作者数 × 最重阶段资源"，recipe 传 `site=run.site`。12 个新测试，累计 79 绿。
- T9.2（2026-09-22）：几何库搬入 `src/ic_opt/em/pcell/`（em-opt `devices/clean_port` 20 文件 + `geometry` 6 文件 + `path_safety` + `profile_validation`，12,288 行，只改包路径与 profile 位置：`builtin:clean_port` 指向本包，随包 profile 目录 `pcell/profiles/`（公开虚构 `demo_6m`），私有 profile 经 `IC_OPT_PROFILE_DIRS`）；`[em]` extra = klayout；ruff 对搬运代码只做安全自动修复，其余风格项按目录豁免。`base.py` 加 `EmxPort` / `parse_emx_port_line` / `read_emx_ports`（原在 em-opt 编排层）。`stages/em_chain.py`：`Geometry` / `DeviceGeometry` 类型、`device_config`（fixed + 变量映射 + profile + port_order；整数/浮点按变量 kind，禁止单位后缀）、`snp_order`（有绑定时以绑定端子序为 sNp 列序，否则器件端口序）、`Pcell` 阶段（`<device>.gds` 固定名 = 顶层 cell 名，端口交叉核对，em-opt 的产品域 DRC 门原样：M1 max_width 豁免）。em-opt 的 8 个几何测试文件迁到 `tests/ic_opt/pcell/`（去掉依赖其编排层/文档树的 5 个用例），需私有 profile 的按文件 skip：**有 `IC_OPT_PROFILE_DIRS` 时 634 绿**，无则跳过。**V1 通过**：12 个 stratum 各抽 8 个记录配置，用 em-opt 自己的包（其 .venv）与搬入的库各生成一次——能构建的字节全等、拒绝的类名与消息全等（记录的 GDS 本身出自旧几何代，不能作参照）。核心套件 83 绿。
- T9.4（2026-09-22）：EMX 阶段。`em/touchstone.py`（Touchstone v1 读取 + 头部校验，= em-opt `measure.read_touchstone` + `validate_touchstone_output` 内核；读取时额外检查数值有限）、`em/emx.py`（`argv` 按 em-opt flag 顺序逐项复刻，`--parallel/--max-memory/--simultaneous-frequencies` 只来自专用字段；`numbered_ports` 用 `p01..` 名字把 sNp 列序钉在 `snp_order`；`fingerprint` = GDS sha256 + 端口 + 物理设置（不含线程/内存/超时/verbose）+ `.proc` 内容哈希（经 executor `sha256sum`）；`run` 在 `<remote>/em/<device>/` 用相对文件名执行、带超时、取回 sNp 与 emx.log、头部校验）。`stages/em_chain.py`：`Emx(spec, device)` **每器件一个点级阶段**（引擎按各自指纹缓存；`identity` = 物理设置进 `pipeline_fingerprint`），`Geometry` 状态累加 `sparams`；`Stage.fingerprint(inp, ctx)` 与 `load(dir, inp, ctx)` 签名扩展。doctor 新增 `device:<id>`（生成器可解析、profile 可加载）、`emx`、`em:process_file`、`em:envelope`（每 EMX 线程/内存 → 站点内并发数）。假主机加 `emx`/`sha256sum` 分支（合成耦合电感 Touchstone）。**V3 通过**：三个记录工程（ind_ct 单器件、acceptance 双器件、sweep 细网格）的 `emx_manifest.json` argv 与重建 argv 逐项相等（含位置参数尾部）。核心 90 绿。
- T9.5（2026-09-22）：nport 绑定与 EM-电路链。`em/nport.py`（em-opt `patch_nport_file_path` 内核，文本进文本出：逻辑语句切分、恰好一条实例、只替换引号内路径（`interp=` 等选项保留）、端子数 = 2×端口、行数不变）；`BindNport` 子阶段（渲染电路网表 + 把每个绑定器件的 sNp 放进 `netlist/models/<device>.sNp` 并改写实例；`terminals` 对照 `SParams.port_labels`）；`render_netlist` 从 `Render` 抽出复用；`StageContext.point` 由引擎注入。`em_circuit_pipeline` = pcell → emx×器件 → bind_nport → spectre → ocean → extract；`blocks.evaluate.default_pipeline` 按 spec 形状选流水线（有器件且有 testbench → em_circuit；只有器件 → em_only（T9.3）；否则 Spectre），**因此 `optimize` / `fix_run` / `signoff` recipe 对 EM 工程原样可用，不需要 em 专用 recipe**。预算把 EMX 运行计入（`Emx.runs=1`，缓存命中不计；`--plan` 打印 "N EMX runs + M testbench sims"）。migrate：`Geometry Generator` / `EM Devices` / `EMX Settings` / `Nport Bindings` → `devices` / `em` / `bindings`（退役的 `clean_port_ind_sym_ct` → `clean_port_ind_sym` + `ct_metal` + `[P1,N1,CT]`；`extra_args` 里的资源 flag 归位到专用字段；`max_parallel_jobs` 收紧 `parallel_jobs`；Passive Diagnostic Constraints 丢弃并提示）。**V4 通过**：`ind_ct_turbo_smoke` 20 点 + `em-library-workflow-acceptance/local-turbo` 10 点（双器件双 TB、单 TB 两个 nport），nport 内核对记录的 patched sha256 逐实例相等；端到端（真实 pcell + 回放 sNp + 回放 OCEAN 标量）状态 / fom / 惩罚 / 指标全部一致。两个记录工程的 requirement 都能 migrate，`ic-opt run optimize --plan` 在本机全项 ok（emx、.proc、profile 均就绪）。核心 93 绿。
- T9.3（2026-09-22）：测量与 EM-only。`em/measure.py`（em-opt `device_db/measure.py` 内核原样：`S→Z`、理想 balun 约束方程组求混合模式阻抗、`L=Im/ω`、`Q=Im/Re`、`k=Im(Z01)/√(Im Z00·Im Z11)`、`L*_lf`、`L*_res`（系统 SRF/5 处取值）、`Q*_peak`、`SRF_*`（首次符号翻转线性插值，None = 扫频内无谐振）、`k_lf`；`Topology.from_labels` 把语义端口映射到 sNp 列；`Quantities.at` 按最近网格频点取曲线值、超出网格 fail-closed）。`Measure` 器件子阶段按 spec 的 `quantity`/`frequency_hz` 取值，非有限/缺失记 issue → `failed:measure`，`quantities.json` 落盘；`em_only_pipeline` = pcell → emx → measure；`em_circuit_pipeline` 在有器件指标时并入 measure 子链（两条子链同点）。**V2 通过**：对 em-opt 生产库 `device_db.sqlite` 12 个 stratum 各抽 6 个样本，9 个标量（含 NULL 语义）+ 5 条 201 点曲线全部 rel ≤ 1e-9 一致。核心 96 绿。
