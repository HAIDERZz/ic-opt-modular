# T9：把 em-opt 的 EM 能力作为阶段与模块并入 ic-opt

- 状态：**执行中**（2026-09-22 用户裁定：几何代码许可无问题——docstring 里的 GPL 溯源早已失效，代码基本原创，可并入本仓库；**查询库暂缓**，先做 EM 仿真核心：T9.1 → T9.2 → T9.4 → T9.5 → T9.3；T9.6 的库/代理模型另行规划）
- 依据：`analysis/em/01_em_evaluation_chain.md`（评估链，415 行）、`02_pcell_geometry_layer.md`（几何层）、`03_device_db_and_surrogate.md`（查询库与代理模型，580 行）、`04_recorded_data_and_fakes.md`（记录数据与假件，481 行）——对 em-opt `main@06be907` / 0.2.1 的源码级研读，全部带文件/行号；本文引用处以报告为准
- 上游约束：`DESIGN_CN.md` §4.1（评估 = 通用引擎 + 可插阶段）、`docs/adr/0001`（远端只经 Executor）、用户红线（真实 EMX 须 `--plan` 后批准；总线程 ≤ 128、内存 ≤ 128 GB；工艺数值永不进 src；GDS topcell == 文件名 stem）

## 0. 一句话

em-opt 今天是 ic-opt 0.1 的 fork（62,292 行：几何 11.7k、器件库 4.7k、代理模型 2.2k、EM 内核 2.9k、Spectre 适配 2.0k、**编排 38.7k**）。T9 只搬前四类的**内核**，编排一行不搬：EM 变成三个阶段接到现有的 `spectre → ocean → extract` 前面；器件"查询库"变成一张观测表；逆向推荐变成"用代理模型当流水线"。做完后 em-opt 仓库退役为数据/工艺档案。

## 1. 目标与不做的事

做：

1. 三条随包流水线，全部由同一个引擎跑：
   - `em_circuit`：`pcell → emx → { measure ‖ bind_nport → spectre → ocean → extract }`（= em-opt 的 EM optimize，外加真正生效的器件诊断量）
   - `em_only`：`pcell → emx → measure`（= 器件表征 / 扫库 / 器件级优化，例如"L_res = 0.55 nH 下 Q_peak 最大"）
   - `surrogate`：`pcell → predict`（StratumGP 在库观测上预测，零仿真；`pcell` 在前面只为剔除造不出来的点）→ 逆向设计，再用 `signoff` 把前 k 个点换成 `em_only` 复核 = 闭环
2. `spec.yaml` 增加三个可选段：`devices`、`em`、`bindings`；`metrics` 允许 `device + quantity` 形式的器件指标。
3. 引擎补三件通用能力（都不认识 EMX）：子单元由子级阶段声明（`testbench` / `device`，一条流水线可同时含两种子链）；点级阶段按指纹缓存；并发槽按最重阶段的 `resources` 在站点包络内计算。
4. 现有器件库（12,767 行 sqlite）一次性导入为观测表工程；StratumGP 成为一个 suggester；`hermes-db` 的 query / suggest / coverage 变成对观测表的 block 调用。
5. 验证：几何逐字节回放、测量逐位回放、EMX argv 回放、EM-电路链回放（含 nport 补丁 sha256 相等）；真实 EMX 冒烟（含 bwrap 隔离的远程验收）。

不做：

- 不搬 em-opt 的 requirement_intake / approvals / ledger / real_run / remote_* / optimizer_* / run_retention / optimizer_em_artifacts 编排（38.7k 行；`01` 报告 §9 逐项列了"可丢弃"清单）。
- 不保留 `hermes-workflow` / `em-ic-opt` / `hermes-db` 三个 CLI；不保留 `emx_resource_guard.py`（em-opt 生产代码里本就没接线，`01` §10.1）。
- 不在 EM optimize 里支持 process corners（em-opt 也不支持；引擎天然支持，等有需求再开）。
- 不把 N28/N65 工艺数值放进 src：profile 仍从 `IC_OPT_PROFILE_DIRS` 外部目录加载；随包只带全虚构的 `demo_6m` 做测试。

## 2. 阶段契约（类型、层级、资源、指纹）

```text
                 ┌─ measure ─────────────────────────────────────▶ ChildResult   (unit = device)
Point ─pcell─▶ Geometry ─emx─▶ SParams ─┤
                 └─ bind_nport ─▶ Netlist ─spectre─▶ RawSim ─ocean─▶ Scalars ─extract─▶ ChildResult   (unit = testbench)
Point ─pcell─▶ Geometry ─predict─▶ ChildResult   (unit = device；surrogate 流水线)
```

| 阶段 | level / unit | 输入 → 输出 | resources | fingerprint（缓存键） | 失败 → status |
| --- | --- | --- | --- | --- | --- |
| `pcell` | point | `Point` → `Geometry{devices: {id: DeviceGeometry(gds_path, top_cell, ports: {label: (signal, reference)}, config, gds_sha256)}}` | 1 线程，毫秒级 | 无 | `failed:pcell`（参数越界 / 拓扑不可构造 / 内建 DRC 违规） |
| `emx` | point | `Geometry` → `SParams{devices: {id: Touchstone(path, port_labels_in_snp_order, z0, freqs, s)}}` | `threads=em.threads`, `memory_gb=em.memory_gb`（一个点内多器件串行） | `sha256(gds_sha256 ∥ ports ∥ canonical(em) ∥ proc_sha256)`，每器件一个 | `failed:emx`（非零退出 / 超时 / sNp 缺失、端口数或阻抗不符、数值非有限） |
| `measure` | child / device | `SParams` → `ChildResult{metrics}` | — | 无 | `failed:measure`（量不可算 / 频点超出扫频） |
| `bind_nport` | child / testbench | `SParams` → `Netlist` | — | 无 | `failed:bind_nport`（实例缺失或多于一条 / 端子序与 sNp 列序不符） |
| `predict` | child / device | `Geometry` → `ChildResult{metrics}` | — | 无 | `failed:predict`（域守卫拒绝：achieved box 外 / NT 层样本不足 / 凸包外 / 相对 σ 超限） |
| `spectre / ocean / extract` | child / testbench | 不变 | 不变 | 无 | 不变 |

`Geometry` / `SParams` 是纯数据（dataclass，可 JSON 化，落在 `sims/<obs>/em/<device>/`）。`pcell` 不缓存（构造 1–5 ms，仅 NT=2 紧凑双匝路径约 0.3 s，`02` §8.3）；GDS 文件名固定为 `<device_id>.gds`，顶层 cell 名即 `device_id`（em-opt 的 `top_cell` 字段本就是死字段，`02` §10.1，新 spec 不设此字段）——同一几何在不同点上字节相同，才让 `emx` 的缓存键能跨点命中。GDS 写入是字节确定的（时间戳关闭，`02` §6.6）。端口只保留 `-p name=signal:reference` 三元组（em-opt 也只落盘这些，坐标不持久化，`02` §10.2）。工艺 profile 每次生成都重新读 YAML（`02` §10.4）→ `Pcell` 按 profile id + 内容哈希做进程内缓存。`bind_nport` 把 sNp 复制进 `<child>/netlist/models/<device>.sNp` 并改写 nport 实例的 `file=`，其余交给不变的 Spectre 三段——这就是 em-opt 的 M2/M3/M4 在新架构里的全部形态。

对照 em-opt 的内核（搬运不改）与新行为（`01` §9 复用表 + §10 脆弱点）：

| 内核 | 来源 | 在新阶段里 |
| --- | --- | --- |
| `build_emx_argv` 的 24 项 flag 映射 + `-p name=signal:reference` | `emx_runner.py:23-94` | `em/emx.py::argv(settings, device)`；`--format=touchstone`、`--include-command-line` 固定；`max_parallel_jobs` 不进 argv |
| 缓存键配方（剔除候选专属路径、argv 不进键、`.proc` 内容哈希进键） | `em_cache.py:32-51`、`em_artifacts.py:193-213` | `Emx.fingerprint`；键改为 GDS 字节 + 端口 + 设置 + `.proc` 哈希（几何 config 已由 GDS 字节刻画） |
| Touchstone 头部校验（后缀端口数、`EMX was run`、`# Hz S RI R z0`） | `emx_runner.py:125-168` | `em/touchstone.py::validate_header`，**再加数值解析**（em-opt 全仓从不读 S 矩阵数值，`01` §5） |
| 逻辑语句切分 / `FILE_RE` / `_extract_nport_signal_order` / 恰好一条实例 / 行数不变 | `nport_binding.py:101-241` | `em/nport.py::patch(text, instance, snp_rel_path, terminals) -> text`（文本进文本出；`interp=` 等其它选项原样保留，`04` §3.3） |
| 多器件参数解复用（`<device>.<field>`） | `em_candidate_preparation.py:394-436` | `spec.devices[].variables` 映射；实现在 `Pcell.run` |
| 端口交叉核对（EMX ports 的 signal/reference 必须是 GDS 实际标签） | `em_candidate_preparation.py:152-176` | `Pcell.run` 结束时核对 |

**引擎的三处扩展**（`eval/engine.py`，预计 +80 行）：

1. 子单元：子级阶段声明 `unit: "testbench" | "device"`；引擎按 unit 把子级阶段分成两条子链，分别对 `spec.testbenches × corners` 和 `spec.devices × [nominal]` 展开，子键 `<unit_id>/<corner>`；`ChildResult.testbench` 改名 `unit`。聚合器不变（所有子结果的 metrics 取并集；任一子失败则点失败）。
2. 缓存：点级阶段 `fingerprint(inp)` 非空时，引擎查 `.icopt/cache/<stage>/<fp>/`，命中则 `stage.load(dir)`，否则 `run` 后 `stage.save(out, dir)`（临时目录 + 原子 rename；第二个写者若内容哈希不同**记 issue 而不是静默丢弃**，修 `01` §10.5）。观测里记录 `cache: hit|miss` 每器件。这回答了 `01` §10.10 的架构问题：**引擎拥有缓存，阶段只提供指纹与序列化**。
3. 并发槽：`workers = min(parallel_jobs, site.slots(max(stage.threads), max(stage.memory_gb)))`。EMX 声明 4 线程 / 32 GB 时，128 线程 / 128 GB 的站点给 4 个槽——与 em-opt 2026-07-09 事故后冻结的包络（`--parallel=4 --max-memory=32G`，4 并发）一致；`--plan` 打印槽数与由哪个阶段决定。实时 RSS 监控两边生产代码都没有，不做；文档保留"外层 systemd scope"的运维说明。

顺手修掉的 em-opt 脆弱点（`01` §10）：EMX 三条执行路径都没有超时 → `em.timeout_s` 必填并传给 `executor.run`；EMX 不设 cwd → `cwd = <remote_dir>/em/<device>`，argv 全用相对文件名；`process_file` 不校验绝对路径 → Spec 校验 + doctor `exists` + 内容哈希；"sNp 列序 = EMX 端口名字典序"只写在文档里 → `Emx` 按字典序算出 `port_labels_in_snp_order`，`bind_nport` 用它对照 `bindings.terminals`（物理列序检查，而不只是网表自洽）；Spectre 瞬时 `can't create server socket` 重试在 T3 丢了 → 补回（`Spectre.run` 重试一次）。

## 3. spec.yaml 的三个新段

```yaml
devices:                                   # 每项 = 一个 pcell 生成器实例
  - id: xfmr_in
    generator: clean_port_xfm_bs           # builtin 插件 id；或 "path/to/plugin.py:generator_id"
    profile: n28_1p10m                     # 工艺规则 profile id，经 IC_OPT_PROFILE_DIRS 解析（值不入仓）
    ports: [P1, N1, P2, N2]                # 语义端口；含 CT 时按插件固定顺序追加 CT / CTP / CTS
    fixed:                                 # 非优化的生成器字段（含 ground_fixture、drc_check 等，原样透传）
      primary_outer_diameter_um: 90
      primary_metal: "10"
      secondary_metal: "9"
      ground_fixture: {inner_margin_um: 15, ring_width_um: 50, stub_length_um: 2, stub_chamfer_um: 0}
    variables:                             # 生成器字段 ← spec 变量名（默认 "<id>.<field>"，单器件可省略前缀）
      primary_width_um: xfmr_in.wp
      secondary_width_um: xfmr_in.ws
    topology:                              # measure 用的理想 balun 拓扑；缺省由端口集推出：
      drives: [[P1, N1], [N2, P2]]         #   2 端口 → [[P1,N1]]；4 端口 → [[P1,N1],[N2,P2]]（次级反接，与库 parity 一致）
      grounded: []                         #   多出的 CT/CTP/CTS 端口缺省接地
em:                                        # EMX 设置：一份 spec 一套
  binary: emx
  process_file: /site/path/tsmcN28_1p10m.proc    # executor 主机上的绝对路径；doctor 用 exists 检查并取 sha256
  mode: quasistatic                        # quasistatic | full_wave
  frequencies: {start_hz: 0, stop_hz: 200e9, step_hz: 1e9}    # 或 [4.0e10]
  accuracy: standard                       # standard|high|higher|highest，或 {edge_width_um, max_splits, thickness_um}
  three_d_metals: [M10, AP]
  via_separation_um: 0.5
  s_impedance: 50
  threads: 4                               # --parallel
  memory_gb: 32                            # --max-memory=32G
  simultaneous_frequencies: 0              # 事故后规则：显式 0
  timeout_s: 3600
  extra_args: []
bindings:                                  # 电路 testbench 里吃器件 sNp 的 nport 实例
  - testbench: lo_xfmr_tb
    instance: NPORT0
    device: xfmr_in
    terminals: [P1, N1, P2, N2]            # 电路端子顺序，必须 == sNp 列序（Emx 算出的语义标签序）
metrics:
  - {name: gain_db, unit: dB, testbench: lo_xfmr_tb, expression: 'value(db20(getData("gain" ?result "sp")) 4e10)'}
  - {name: Qp_10g,  unit: ratio, device: xfmr_in, quantity: Qp, frequency_hz: 1.0e10}   # 曲线量在网格频点取值（吸附最近点）
  - {name: SRF_p,   unit: Hz,    device: xfmr_in, quantity: SRF_p}                      # 标量量：Lp_lf Lp_res Qp_peak SRF_p (Ls_* Qs_* SRF_s k_lf)
```

规则：`testbenches` 与 `devices` 至少一个非空；`bindings` 非空要求二者都有；`Metric` 的 `testbench+expression` 与 `device+quantity` 二选一；`em` 段存在时 doctor 追加四项检查（`emx` 工具、`process_file` 存在、每个 `profile` 可加载、EMX 槽数）。量的定义原样搬 `device_db/measure.py`（`03` §1：`Z = z0(I+S)(I−S)⁻¹`、理想 balun 约束方程、`L = Im(Z)/ω`、`Q = Im/Re`、`k = Im(Z01)/√(Im Z00 · Im Z11)`、`SRF` 首次符号翻转线性插值、`L_res` = SRF_cap/5 处取值），包括 2 端口 Touchstone 的列序转置特判和 SRF=NULL 语义。

## 4. 器件库 = 一张观测表

em-opt 查询库每一行（参数 → L/Q/SRF/k…）就是 `em_only` 流水线的一条 `Observation`。六元组身份（`03` §2.3：family + process_profile + emx_settings_hash + params_hash + geom_version + family_schema_revision）逐项落到已有字段：

| 库身份 | 观测表 |
| --- | --- |
| family + process_profile + family_schema_revision + 固定字段 | `spec_fingerprint`（`devices[].generator/profile/fixed` 都在 spec 里） |
| emx_settings_hash + geom_version（+ `.proc` 哈希） | `pipeline_fingerprint`（`Emx` 设置 + `.proc` 内容哈希 + 生成器插件声明的 `GEOMETRY_VERSION` + profile 内容哈希）。`02` §6.5 建议改用插件代码哈希自动化"代"的概念，但代码哈希会让任何无关改动把整个库判成异代；这里保留人工版本号，用 V1 字节回放测试守住"改了几何却没升版本"的漂移 |
| params_hash | `Observation.key`（吸附后的参数字符串） |
| sweep / optimizer / manual 来源 | `origin`（`points:sobol` / `suggest:…` / `import:device_db:<stratum>@geom5`） |

`03` §6 提醒"库是跨 run 的长期语料"——观测表本来就是按**工程**累积、追加写的，一个 `library/<stratum>/` 工程就是长期语料；扫库 = `em_sweep` recipe，扩库 = 加预算再跑。"不混代训练"（`03` §7.11）= 建议器只拟合 `pipeline_fingerprint` 与当前流水线相同的观测；要用旧代数据，recipe 显式传 `initial=`（等价于 `--pin-geom-version`），并在报告里列出被排除的代与行数。

| em-opt 概念 | ic-opt 形态 |
| --- | --- |
| stratum（家族 × 金属栈，12 个） | 库工程目录 `library/<stratum>/spec.yaml` + `.icopt/observations.jsonl`；sNp 放 `cache/emx/<fp>/`（内容寻址） |
| `hermes-db ingest` / backflow | `em.import_library(sqlite, stratum, out)`（一次性迁移，含 `build_rejected` 行 → `failed:pcell`）；日常回流 = `opt.adopt` |
| `hermes-db query` | `analyze.best` / `points.from`（测过的点）或 `predict` 流水线一次评估（没测过的点） |
| `hermes-db suggest` / inverse（Sobol 候选池 → 域守卫 → 保守约束 → 多样化 → 真实构建验证） | `opt.optimize(pipeline=[pcell, predict], strategy=stratum_gp)`：候选池 = `points.sobol` + `pcell` 剔除不可构造点（= `verify_build`，但作为评估而不是藏在建议器里，`03` §6 待决点 ②）；保守约束 = `constraint_penalty` 用 `mu ± k·σ` |
| `hermes-db coverage` / `family-status` | 报告一节（achieved box、NT 分层计数、代际行数） |
| `FamilySchema.draw/feasible`（拒绝重采样） | `points.sobol(spec, n, feasible=b.em.feasible(spec))`——可行性判据就是 `pcell` 的解析式快速检查，同一份实现 |
| StratumGP（per_nt + matern52，无量纲 k 映射，log 目标）+ DomainGuard 四判据 | `suggesters/stratum.py` 与 `stages/em_chain.py::Predict` 共用 `em/surrogate/`（搬运） |
| `reingest`（换 DERIVED_VERSION 重算） | 对库观测重跑 `measure` 阶段的一条 recipe |

## 5. 包布局与依赖

研读报告 `02` §10.6 提到六家族几何代码的 docstring 自述为 GPL SKILL 衍生作品；用户裁定该表述早已失效（至多是 MIT gdsfactory 的二次开发，现已基本原创），因此几何库**并入本仓库** `ic_opt/em/pcell/`，作为 `builtin:clean_port` 插件；外部器件仍可用 `plugin: /path/to/plugin.py` 接入。

```text
src/ic_opt/em/                     EM 库（不认识引擎）
  pcell/                           从 em-opt devices/clean_port + geometry/ 搬入：primitives、core、六家族、guards、pgs、straight_extension、
                                   rule_adapter、process_rules（profile 搜索 IC_OPT_PROFILE_DIRS + 进程内缓存）、drc_audit、gds_compare、
                                   registry（builtin:clean_port | /path/plugin.py）（≈11.5k 行搬运；em-opt 的 8 个几何测试文件随迁）
  emx.py                           argv 构造 + 经 Executor 运行 + 头部校验（≈150 行）
  touchstone.py                    sNp 解析（numpy）、列序、量的计算（= device_db/measure.py 内核，≈300 行）
  nport.py                         nport 实例定位/改写、端子核对（≈120 行）
  surrogate/                       StratumGP + DomainGuard（≈900 行，搬运；许可无疑问，是本项目原创）
src/ic_opt/stages/em_chain.py      Pcell / Emx / Measure / BindNport / Predict + 三条流水线（≈350 行）
src/ic_opt/spec.py                 + Device / EmSettings / Binding / Topology；Metric.device+quantity（≈150 行）
src/ic_opt/eval/engine.py          + 两条子链 / 缓存 / 资源槽（≈80 行）
src/ic_opt/suggesters/stratum.py   StratumGP suggester（≈120 行）
src/ic_opt/blocks/em.py            em.import_library、em.feasible、em.audit（≈150 行）
src/ic_opt/recipes/em_optimize.py  em_sweep.py  em_inverse.py（各 ≤ 25 行）
src/ic_opt/migrate.py              + em_opt_requirement.md 的 EM 段（≈80 行；Passive Diagnostic Constraints → device 指标 + 约束，em-opt 里它什么都不算，`01` §5）
tests/ic_opt/                      fakes.py 加 `emx` 分支（按几何指纹回放录制的 sNp）+ 解析式耦合电感替身（借 em-opt `mock_snp.py`）；
                                   fixture 用 `gdsfactory_xfmr_spike/emx_smoke/*.s4p`（真实 EMX 输出，1–2 KB）
```

依赖：`[em]` extra = `klayout>=0.30`（几何层唯一第三方依赖）+ `scikit-learn`（StratumGP；open-box 已带）。核心包不依赖 klayout。

净效果：ic-opt 新增 ≈ 14k 行（11.5k 是搬运的几何库）；em-opt 62k 行退役。

## 6. 验证策略（每条都有真实记录做金标准）

| # | 证明什么 | 金标准（`04` §1） | 方法 | 通过判据 |
| --- | --- | --- | --- | --- |
| V1 | 几何库搬运无损 | `device_db.sqlite.samples(params_json, gds_sha256)` + `outputs/sweep/<stratum>/<idx>_<hash>/geometry_manifest.json`（6300 个成功点） | 每 stratum 抽 ≥ 50 点，用 manifest 里的完整 config 重新生成 | GDS 字节 sha256 全等（用户已用同法证过 4,472 行库回放） |
| V2 | 测量内核无损 | `samples.snp_path` + `metrics(name, freq_hz, value)`（本机 sqlite） | 对录制 sNp 跑 `measure` | 曲线量与标量量逐位相等（rel ≤ 1e-12，NULL ↔ None） |
| V3 | EMX 命令无损 | `runs/em_optimizer/*/em/emx_manifest.json.argv`（三个工程） | 由 spec 重建 argv | 去掉路径与 `--log-file` 后逐项相等 |
| V4 | EM-电路链无损 | `ind_ct_turbo_smoke`（20 点，单器件单 TB，3 端口 CT 电感）+ `em-library-workflow-acceptance/local-turbo`（10 点，双器件双 TB，4 端口变压器） | 假 EMX 按几何指纹回放录制 sNp、假 Spectre 回放录制 OCEAN 标量；其余走真实引擎 | `nport_patch_manifest.patched_input_sha256` 相等；fom / objective / metrics / 状态逐点一致（`native_turbo_optimizer_evaluations.jsonl` 字段与现有回放测试同构，目录少一层 `corners/`） |
| V5 | 真实 EMX（需批准） | 同上两个工程各 2 点 | 本地 + `--ssh-profile`（bwrap：`/opt/eda`、`.proc`、profile 目录、`~/.ic-opt` 隐藏） | 与录制值一致（同机同版本 EMX 2024.1.0；实测 2.3–2.5 s/次、cgroup 峰值 0.3–1.9 GiB，`04` §5） |
| V6 | 库导入与代理模型 | sqlite 12,767 行；`GP_BENCHMARK.md` 的 per_nt-matern52 = 0.0180 中位相对误差；闭环战役 2σ 覆盖 95.6% | 导入后 `stratum_gp` 在同一留出集复算 CV | 中位相对误差与基准一致（±10% 相对） |

真实 EMX 的资源请求（V5）：`threads=4, memory_gb=32`，并发 ≤ 4 → 声明峰值 16 线程 / 128 GB（实测 < 8 GiB）；执行前 `--plan` 打印后再启动。

## 7. 任务分解（每任务一个提交）

| # | 任务 | 产出 | 完成判据 |
| --- | --- | --- | --- |
| T9.1 | 引擎与 spec 的通用扩展 | `spec.py`（devices / em / bindings / topology / Metric.device+quantity）、`eval/engine.py`（两条子链、缓存、资源槽）、`ChildResult.unit`、`Spectre` socket 重试 | 现有 67 测试不变绿；新增：device 子链的假流水线、缓存命中/未命中/冲突、槽数计算、两条子链并存 |
| T9.2 | 几何库搬入 | `ic_opt/em/pcell/`（六家族 + registry + rules + drc + 其 8 个测试文件）、`[em]` extra、`demo_6m` 随包、`IC_OPT_PROFILE_DIRS`、`Pcell` 阶段、`em.feasible` | V1 通过（需 `IC_OPT_PROFILE_DIRS` 指向本机私有 profile，无则跳过）；迁入的几何用例全绿；demo_6m 上六家族各生成一次 |
| T9.3 | 测量与 EM-only | `em/touchstone.py`、`Measure`、`em_only_pipeline`、`em_sweep` recipe、假 EMX（解析式） | V2 通过；`em_sweep` 端到端；`ind_ct` 3 端口拓扑（CT 接地）有测试 |
| T9.4 | EMX 阶段 | `em/emx.py`、`Emx`（超时、cwd、缓存、资源）、doctor 四项 EM 检查、`--plan` 打印槽数 | V3 通过；缓存命中不再调用 executor；远端路径只经 Executor |
| T9.5 | nport 绑定与 EM-电路链 | `em/nport.py`、`BindNport`（物理列序检查）、`em_circuit_pipeline`、`em_optimize` recipe、migrate 的 EM 段 | V4 通过（30 点、patched sha256 相等）；em-opt 两份 requirement 模板能 migrate |
| T9.6 | 库与代理模型 | `em.import_library`、`suggesters/stratum.py`、`Predict`、`surrogate_pipeline`、`em_inverse` recipe、报告的库覆盖一节 | V6 通过；`ic-opt run em_inverse` 在导入库上给出候选，`signoff` 走 `em_only` |
| T9.7 | 真实冒烟 + 文档 | V5（用户批准后）；README / SKILL 的 EM 段；`DESIGN_CN.md` §4.1 表更新；archify `ic-opt-evaluate-stages` 图按实现重绘 | 两工程本地 + 隔离 SSH 全 PASS；文档与代码一致 |
| T9.8 | 退役 em-opt | em-opt 仓库只保留 `process_data/`、`experiments/`（数据）与一页 README 指向新仓库；`author-process-rule` skill 迁到 ic-opt | 新仓库能覆盖 em-opt 三个 CLI 的全部用例（列表见 T9.5 / T9.6 的 recipe） |

顺序：T9.1 → T9.2 → T9.3 → T9.4 → T9.5 → T9.6 → T9.7 → T9.8；T9.2 与 T9.3 可并行。

## 8. 需要用户拍板的点

1. ~~几何库的许可与落点~~ **已裁定**：并入本仓库（docstring 的 GPL 溯源已失效，代码基本原创）。
2. **器件库迁移**：**暂缓**（用户 2026-09-22：先做 EM 仿真核心，库的嵌入方式之后再定）。
3. **真实 EMX 冒烟**（T9.7）：按 §6 V5 的资源请求执行，需你批准；是否顺带用真实 EMX 重跑 V1 抽样中的少量点做"同机复现"对照（每点 2–3 s，建议 12 点）。
4. **em-opt 退役范围**（T9.8）：仓库归档还是删除；`experiments/` 5.6 GB 数据的去向（建议原地保留，新仓库用绝对路径 / 环境变量引用）。
5. **行为变更确认**：失败状态细分为 `failed:pcell|emx|bind_nport|measure|predict`（em-opt 折叠成一个 `real_check_failed`，数值惩罚相同）；器件诊断量真正参与约束（em-opt 的 Passive Diagnostic Constraints 不计算）；`bind_nport` 加物理列序检查（em-opt 只查网表自洽）。都是更严格，不会让原本通过的工程失败，除非它本来接错了线。

## 9. 与研读报告的对应（执行时逐项核对）

- 阶段内核签名、EMX argv 各项来源、缓存键配方、nport 补丁与端子核对、失败分类、并发、可丢弃清单 → `analysis/em/01_em_evaluation_chain.md`
- 生成器 API（`get_generator(id, plugin_module)` → `PassiveDeviceGenerator.generate(config, outdir, gds_name) -> GeometryGenerationResult(gds_path, top_cell, manifest_path, emx_ports_path)`）、六家族 pydantic 配置模型（校验规则的全部价值都在这里）、profile 结构与 `validate-profile`、DRC 三层（内联 guard / 写盘后结构复核 / 规则级 `audit_gds`）、GDS 字节确定性、NT=2 慢路径、许可提示、8 个可搬运测试文件 → `02_pcell_geometry_layer.md`
- sNp → 量的公式、schema v4 六元组身份、hermes-db 九个动词、StratumGP / DomainGuard / inverse 全流程、扫参计划格式、13 条陷阱 → `03_device_db_and_surrogate.md`
- 回放数据集（`ind_ct_turbo_smoke` 20 点、`em-library-workflow-acceptance` 10 点、`device_db_sweep_n28` 12,767 行 / 6300 个完整点目录）、nport 实例 verbatim、假件模式、EMX 时间/内存实测 → `04_recorded_data_and_fakes.md`
