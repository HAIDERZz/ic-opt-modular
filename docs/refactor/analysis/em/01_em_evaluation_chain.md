# EM 评估链路分析：kernel 与 contract（供阶段化复用）

## 0. 范围、基线与方法

- 研究对象：`<em-opt>`，包 `src/em_ic_opt_workflow`。
- 源码基线：commit `06be9076`（2026-09-22，分支 `main`，`src/`、`docs/guide/` 工作区干净，无未提交改动）。
- 方法：对下列文件做逐行通读（`Read` 工具，行号即文件真实行号），辅以 `grep -n` 做跨文件调用链与"是否被引用"的排他性核查（尤其用于第10节"完全未接线"类结论）。所有行号引用均已用 `grep -n`/`Read` 交叉验证；文中一次报告过程中发现并纠正了一处因未加行号的 `sed` 误读导致的 1000 行偏移错误（`native_turbo.py` 的 `_objective_evaluation_for_observation`），提醒本文档的后续维护者：涉及此文件 1189 行之后的引用已用 `Read(offset=...)` 复核。
- 另外读取了目标仓库 `<repo>` 的 `docs/refactor/DESIGN_CN.md`、`src/ic_opt/eval/stage.py`、`src/ic_opt/eval/engine.py`、`src/ic_opt/stages/spectre_chain.py`，以确定目标 `Stage`/`Engine` 契约的确切形状——第9、10节的"复用为哪个 Stage""目前引擎里还缺什么"等结论直接引用这些文件。

### 文件路径索引（下文用裸文件名 + 行号引用，均指此表中的绝对路径）

| 裸文件名 | 绝对路径 |
| --- | --- |
| `em_optimizer_evaluator.py` | `<em-opt>/src/em_ic_opt_workflow/em_optimizer_evaluator.py` |
| `em_candidate_preparation.py` | `<em-opt>/src/em_ic_opt_workflow/em_candidate_preparation.py` |
| `em_artifacts.py` | `<em-opt>/src/em_ic_opt_workflow/em_artifacts.py` |
| `em_cache.py` | `<em-opt>/src/em_ic_opt_workflow/em_cache.py` |
| `optimizer_em_artifacts.py` | `<em-opt>/src/em_ic_opt_workflow/optimizer_em_artifacts.py` |
| `emx_config.py` | `<em-opt>/src/em_ic_opt_workflow/emx_config.py` |
| `emx_execution.py` | `<em-opt>/src/em_ic_opt_workflow/emx_execution.py` |
| `emx_runner.py` | `<em-opt>/src/em_ic_opt_workflow/emx_runner.py` |
| `emx_resource_guard.py` | `<em-opt>/src/em_ic_opt_workflow/emx_resource_guard.py` |
| `m3_circuit_preparation.py` | `<em-opt>/src/em_ic_opt_workflow/m3_circuit_preparation.py` |
| `nport_binding.py` | `<em-opt>/src/em_ic_opt_workflow/nport_binding.py` |
| `em_circuit_evaluator.py` | `<em-opt>/src/em_ic_opt_workflow/em_circuit_evaluator.py` |
| `remote_em_circuit_evaluator.py` | `<em-opt>/src/em_ic_opt_workflow/remote_em_circuit_evaluator.py` |
| `circuit_bundle.py` | `<em-opt>/src/em_ic_opt_workflow/circuit_bundle.py` |
| `schemas.py` | `<em-opt>/src/em_ic_opt_workflow/schemas.py` |
| `requirement_intake.py` | `<em-opt>/src/em_ic_opt_workflow/requirement_intake.py` |
| `remote_emx_runner.py` | `<em-opt>/src/em_ic_opt_workflow/remote_emx_runner.py` |
| `remote_optimizer_flow.py` | `<em-opt>/src/em_ic_opt_workflow/remote_optimizer_flow.py` |
| `validate.py` | `<em-opt>/src/em_ic_opt_workflow/validate.py` |
| `native_turbo.py` | `<em-opt>/src/em_ic_opt_workflow/native_turbo.py` |
| `openbox_backend.py` | `<em-opt>/src/em_ic_opt_workflow/openbox_backend.py` |
| `run_retention.py` | `<em-opt>/src/em_ic_opt_workflow/run_retention.py` |
| `execution_adapters/spectre_ocean.py` | `<em-opt>/src/em_ic_opt_workflow/execution_adapters/spectre_ocean.py` |
| `geometry/registry.py` | `<em-opt>/src/em_ic_opt_workflow/geometry/registry.py` |
| `geometry/base.py` | `<em-opt>/src/em_ic_opt_workflow/geometry/base.py` |
| `geometry/drc_audit.py` | `<em-opt>/src/em_ic_opt_workflow/geometry/drc_audit.py` |
| `csh_subprocess.py` | `<em-opt>/src/em_ic_opt_workflow/csh_subprocess.py` |
| `docs/guide/02-requirement-reference.md` | `<em-opt>/docs/guide/02-requirement-reference.md` |
| `docs/guide/04-resources-and-safety.md` | `<em-opt>/docs/guide/04-resources-and-safety.md` |
| `ic-opt-modular/.../DESIGN_CN.md` | `<repo>/docs/refactor/DESIGN_CN.md` |
| `ic-opt-modular/.../eval/stage.py` | `<repo>/src/ic_opt/eval/stage.py` |
| `ic-opt-modular/.../eval/engine.py` | `<repo>/src/ic_opt/eval/engine.py` |
| `ic-opt-modular/.../stages/spectre_chain.py` | `<repo>/src/ic_opt/stages/spectre_chain.py` |

目标映射（`DESIGN_CN.md:191`）：`pcell → emx → bind_nport → spectre → ocean → extract`；其中 **M2 = pcell**，**EM artifact 产出 + `EmCacheStore` = emx 阶段的缓存层**，**M3 = bind_nport**，**M4 = 已随 ic-opt 一起实现的共享后三段**（`spectre_chain.py` 已经落地 `Render/Spectre/Ocean/Extract` 四个 `child` 级 Stage）。本报告的第1–8节严格聚焦"今天的 EM-opt-workflow 怎么做"，第9、10节再把结论对齐到已存在的 `Stage`/`Engine` 契约。

---

## 1. 单候选点端到端数据流（EM optimize 模式）

入口：`evaluate_one_em_optimizer_candidate`（`em_optimizer_evaluator.py:476-605`），由批量评估器 `make_em_candidate_batch_evaluator`（615-695）在线程池里对批内每个候选并发调用（详见第8节）。

候选目录：`_candidate_root(project_dir, candidate_id) = project_dir/"runs"/"em_optimizer"/candidate_id`（121-122）；`em_dir = candidate_root/"em"`，`circuit_dir = candidate_root/"circuit"`（493-495）。

### 步骤 1 — M2 pcell（几何生成 + DRC）

单器件路径（`bundle.em_devices is None`，523-533）调用 `prepare_em_candidate_from_contract`（`em_candidate_preparation.py:439-469`）：

1. `_merged_geometry_parameters(bundle, parameters)`（289-310）把设计变量值与 `generator.fixed_parameters`、`process_profile`、`port_order`、`drc_check`（经 `_inject_drc_check`，335-348）拼成生成器的完整配置字典。
2. `gds_name` **写死为 `"device.gds"`**（460 行字面量），`top_cell = bundle.geometry.generator.top_cell`（461）。
3. 调用 `prepare_em_candidate(request, outdir=em_dir)`（196-286，见第9节"kernel"）：
   - `generator = get_generator(request.generator_id, plugin_module=...)`（202；`geometry/registry.py:118-143` 解析 `"builtin:clean_port"` 等）。
   - `geometry = generator.generate(geometry_config, outdir=em_dir, gds_name="device.gds", top_cell=...)`（204-209）——**这是 pcell 渲染 kernel 本身**（`PassiveDeviceGenerator.generate`，`geometry/base.py:23-32`），写出 `em/device.gds` + 生成器自己的几何 manifest（`geometry.manifest_path`）+ 建议端口文件（`geometry.emx_ports_path`）。
   - DRC 门禁（除非 `drc_check=False` 显式关闭，由 `_drc_check_enabled` 判定，179-193）：`audit_gds(gds_path, process_profile)` → `product_scope_record(report, expected_conductors, ignore_findings=...)`（210-246；`geometry/drc_audit.py:286`,`526`,`550`）。不过关抛 `DrcViolationError(ValueError)`（26-33），文件 M1 层的 `max_width` 违规被硬编码豁免（214-229 注释解释：清洁端口族的地环工装必然触发一次 M1 max_width，与产品绕组无关）。
   - 端口交叉核对：`_load_emx_ports` + `_validate_configured_ports_against_geometry`（247-248,152-176）——确认 `EMX Settings.ports[].signal/reference` 在生成器实际画出的标签集合里。
   - 组装 `EmxRunConfig`（249-276）→ `emx_argv = build_emx_argv(emx_config)`（277；见第2节）→ `write_emx_manifest`（279）写 `em/emx_manifest.json`（**仪式性**：审计留痕，下游不再读取此文件的内容做判断）。

多器件路径（`bundle.em_devices is not None`，545-554）调用 `prepare_em_devices_from_contract`（472-516），逐器件重复上面同一套 kernel，`gds_name=f"{device.id}.gds"`（495），`outdir=candidate_root/"devices"/device.id/"em"`（506）——详见第6节。

任一分支若抛出 `EM_CANDIDATE_EXPECTED_FAILURES`（`ValueError`/`OSError`/`RuntimeError`/`subprocess.TimeoutExpired`，45-51）都被 `_stage_failure_observation("candidate_preparation_failed", exc)` 捕获（460-465,532,554）。

### 步骤 2 — EM artifact 产出 + 缓存门（emx 阶段）

`produce_em_candidate_artifact`/`produce_em_candidate_artifacts`（`em_artifacts.py:317-333`/`361-428`，内部都走 `_produce_em_candidate_artifact`，233-314）：

- 指纹 `fingerprint = _fingerprint_prepared(prepared)`（193-213，见第3节）。
- **命中**：`EmCacheStore.restore(...)`（`em_cache.py:82-106`）成功 → `cache_status="hit"`，**不跑 EMX**，`execution=None`。
- **未命中**：`execution = run_prepared_em_candidate(prepared, runner=em_runner)`（`emx_execution.py:55-95`，见第2节，即真正的 EMX 调用）；`execution.status=="pass"` 时 `store.store(...)`（`em_cache.py:61-80`）写入缓存，`cache_status="miss"`；否则 `"miss_failed"`。
- **禁用**：直接跑 EMX，`cache_status="disabled"`。
- 无论哪个分支，**都会**（重新）写 `em/em_artifact_manifest.json`（301-307；`write_em_artifact_manifest`，126-145；payload 结构见 73-123：几何 provenance、EMX argv/manifest 路径、touchstone 路径+sha256+校验结果、`cache.{fingerprint,status}`）。

回到评估器：`artifact.emx_execution is not None and artifact.emx_execution.status != "pass"` → 短路成 `emx_failed`（567-568）。随后 `_em_artifact_validation_issues_from_artifact`（569-572；体见 262-318）**重新打开刚写的 `em_artifact_manifest.json` 再校验一遍**touchstone 路径/sha256/`expected_ports`/`validation.status`——这是对上一步已产出信息的防御性复检（**仪式性**，见第9节）。

### 步骤 3 — M3 bind_nport

`prepare_candidate_circuit_bundles(bundle, candidate_id, em_artifact_manifest=artifact.manifest_path, outdir=circuit_dir)`（`m3_circuit_preparation.py:33-164`，见第4节）。写出：
- `circuit/<testbench_id>/circuit/*`（`materialize_testbench_bundle` 物化的 Maestro 导出网表副本，`circuit_bundle.py:148-169`）
- `circuit/<testbench_id>/nport_patch_manifest.json`（105-125）
- `circuit/m3_circuit_manifest.json`（135-157，聚合所有 testbench 的路径与 patch 结果）

`m3_result.status != "pass"`（无论是异常还是返回值，583-589 注释明确指出两者被折叠成同一个 `circuit_preparation_failed` 状态）→ `_circuit_preparation_failure_observation`（450-457,588-589）。

### 步骤 4 — M4 spectre → ocean → extract（与 ic-opt 共享的链路）

`evaluate_fixed_em_candidate(bundle, candidate_id, candidate_parameters, run_id=candidate.run_id, m3_circuit_manifest=m3_result.manifest_path, runner=circuit_runner, cadence_cshrc=..., allow_overwrite=False)`（`em_circuit_evaluator.py:322-409`）：
1. `prepare_em_circuit_adapter_packages`（82-227）：先用 `_validate_m3_circuit_manifest`（571-749，约180行）**把 M3 manifest 整个重新校验一遍**（schema_version、candidate_id、testbench 归属、`circuit_dir` 必须在 manifest 目录之下且不含符号链接、`input_scs` 哈希、`nport_patch_manifest` 的 `patched_input_sha256` 与当前 `input.scs` 一致……）；写出 `runs/real/<run_id>/{candidate.json, testbenches/<tb>/netlist/*, testbenches/<tb>/metric_extraction_request.json, testbenches/<tb>/real_run_manifest.json, metric_extraction_request.json(根), real_run_manifest.json(根), em_circuit_evaluation_manifest.json}`。
2. `run_em_circuit_adapter_packages`（230-264）：**串行** `for testbench_id in testbench_ids` 逐个调用 `run_spectre_ocean_adapter`（`execution_adapters/spectre_ocean.py:214-317`，见下）。
3. `build_em_circuit_observation`（267-319）：调用 `aggregate_multi_testbench_run`（`multi_testbench_aggregation.py:78+`）按 corner 策略聚合出 `metrics`/`objective`/`constraints_passed`/`status`，写 `runs/real/<run_id>/em_circuit_observation.json`。

`run_spectre_ocean_adapter` 内部（同一文件）：渲染 OCEAN 回放脚本 `render_ocean_replay_script`（619-684）→ `_run_spectre_with_retries`（339-360，`SPECTRE_MAX_ATTEMPTS=2`，仅当 `spectre.out` 里出现 `"can't create server socket"` 才重试，`_is_transient_spectre_socket_failure`，367-374）→ `_run_ocean_with_retries`（320-336，`OCEAN_MAX_ATTEMPTS=3`）→ `parse_ocean_scalars`（687-759，解析 OCEAN 写出的 TSV 标量表，**不是** Touchstone）→ 写 `runs/real/<run_id>/testbenches/<tb>/{netlist/*,psf/*,metrics/*,result_manifest.json}`。

### 步骤 5 — 收尾（`finish` 闭包，497-521，包住每一条返回路径）

- 若最终 `observation.status != "recorded"` 且 `runs/real/<run_id>/result_manifest.json` 尚不存在（即失败发生在 M4 写出任何东西之前）：补写一个最小 stub（506-512）——**纯粹是为了满足下游报告工具（`optimizer_em_artifacts.py`）"result_manifest 总是存在"的假设**（仪式性）。
- `svc.apply_run_retention(bundle, candidate, observation)` → `_apply_em_run_retention`（125-177）→ `apply_local_run_retention`（`run_retention.py:227+`）**对两棵目录各调一次**：`runs/real/<candidate.run_id>` 与 `runs/em_optimizer/<candidate_id>`（152-163）——EM 候选天生有两棵运行目录（M2/M3 写 `em_optimizer/`，M4 写 `real/`），保留策略必须分别应用，顺序还讲究（候选根目录放最后，好让合并后的 decision 以候选目录为准）。

`native_observation_from_em_result`（54-91）把 `EmCircuitEvaluationResult` 折成 `NativeTurboObservation`，交给 `finish()` 返回（604-605）。

### 本节：essential vs. ceremony 速览（详表见第9节）

**Essential（每候选点必经、缺了会改变数值结果）**：`generator.generate()` 渲染、DRC 审计（除非显式 `drc_check=False`）、端口交叉核对、`build_emx_argv` + EMX 子进程（或缓存命中替代）、Touchstone 头部校验、`patch_nport_file_path` 及其前置的 nport 绑定交叉核对、Spectre + OCEAN 执行、`aggregate_multi_testbench_run` 的指标/目标/约束计算。

**Ceremony（审计留痕/生命周期管理，整体可被"引擎+typed对象"替代而不改变评估结果）**：`emx_manifest.json`/`em_artifact_manifest.json`/`m3_circuit_manifest.json`/`nport_patch_manifest.json`/`em_circuit_evaluation_manifest.json`/`real_run_manifest.json`/`metric_extraction_request.json` 的写出与逐级重新读回校验（`_validate_m3_circuit_manifest` 180行、`_em_artifact_validation_issues_from_artifact` 等）；`finish()` 里的失败证据 stub；`apply_local_run_retention` 的双目录处理与 decision report；Approval Checklist（在 requirement 解析期一次性把关，见 `requirement_intake.py:99-107`，不在候选点热路径里）。

---

## 2. EMX 调用

### argv 构造

纯函数 `build_emx_argv(config: EmxRunConfig) -> list[str]`（`emx_runner.py:23-94`）。逐 flag 对照表：

| 位置/flag | 触发条件 | `EmxRunConfig` 字段（`emx_config.py:48-94`） | 对应 `EmxSettings` 契约字段（`schemas.py:506-578`） |
| --- | --- | --- | --- |
| `argv[0]` | 总是（24-30） | `config.binary` | `binary` |
| `--quasistatic` \| `--full-wave` | 总是（26） | `config.mode` | `mode` |
| `--format=touchstone` | 总是（27） | 硬编码常量 | 无（不受契约控制） |
| `--s-impedance=<num>` | 总是（28） | `config.s_impedance` | `s_impedance` |
| `--s-file=<path>` | 总是（29） | `config.s_file`（`outdir/s_file_name`） | `s_file_name`（拼接候选目录后变绝对/相对路径） |
| `--sweep` | `sweep.enabled`（31-32） | `config.sweep.enabled` | `sweep.enabled` |
| `--sweep-stepsize=<num>` | sweep 且 `step_hz is not None`（33-34） | `sweep.step_hz` | `sweep.step_hz` |
| `--sweep-num-steps=<n>` | sweep 且 `num_steps is not None`（35-36） | `sweep.num_steps` | `sweep.num_steps` |
| `--thickness=<num>` | `thickness_um is not None`（37-38） | `thickness_um` | `thickness_um` |
| `--max-splits=<n>` | `max_splits is not None`（39-40） | `max_splits` | `max_splits` |
| `--via-separation=<num>` | `via_separation_um is not None`（41-42） | `via_separation_um` | `via_separation_um` |
| `--include-command-line` | `config.include_command_line`（43-44） | 恒为 `True`（见下） | **无对应用户字段**——`_emx_preparation_options_from_contract`（`em_candidate_preparation.py:389`）写死 `True`，专为让 Touchstone 头出现 `"EMX was run"`（供 `validate_touchstone_output` 检测，`emx_runner.py:156`） |
| `--verbose=<n>` | `verbose is not None`（45-46） | `verbose` | `verbose` |
| `--log-file=<path>` | `log_file is not None`（47-48） | `config.log_file` | **无对应字段**——`log_file_name` 被 `_emx_preparation_options_from_contract`（368）硬编码为 `"emx.log"` |
| `--parallel=<n>` | `parallel is not None`（49-50） | `config.parallel` | `max_cpu_per_job`（映射见 `em_candidate_preparation.py:385` `parallel=emx.max_cpu_per_job`） |
| `--simultaneous-frequencies=<n>` | `is not None`（含 `0`，51-52） | `simultaneous_frequencies` | `simultaneous_frequencies`（0 会被显式传出，不当作"未配置"） |
| `--max-memory=<num>G` | `max_memory_gb is not None`（53-54） | `max_memory_gb` | `max_memory_gb` |
| `--edge-width=<num>` | `edge_width_um is not None`（55-56） | `edge_width_um` | `edge_width_um` |
| `--accuracy=<name>` | `accuracy is not None`（57-58） | `accuracy` | `accuracy` |
| `--3d=<a,b,...>` | `three_d_metals` 非空（59-60） | `three_d_metals` | `three_d_metals` |
| `--via-inductance=<a,b,...>` | 非空（61-62） | `via_inductance` | `via_inductance` |
| `--via-sidewalls=<a,b,...>` | 非空（63-64） | `via_sidewalls` | `via_sidewalls` |
| `--mode=<m>`（逐个重复） | `for mode in config.modes`（65-66） | `modes` | `modes` |
| 原样 extend | 总是（67） | `extra_args` | `extra_args`（契约层禁止在此夹带 `max_parallel_jobs`/`max_memory`/`simultaneous_frequencies`，见 `schemas.py:540-570`） |
| `-p <name>=<signal>[:<reference>]`（逐端口重复） | `for port in config.ports`（68-69） | `ports` | `ports`（见下"端口传递"） |
| 位置参数：sweep 时的 `--discrete-frequency=<num>`（逐个）+ 频率区间/正频率列表（70-93） | 视 `sweep.enabled` 分支 | `frequencies_hz`/`sweep.start_hz`/`sweep.stop_hz` | `frequency_hz` + `sweep.*` |
| 位置参数：`<gds_file> <top_cell> <process_file> <freq...>`（86-93，追加在最后） | 总是 | `gds_file`/`top_cell`/`process_file` | `process_file`；`gds_file`/`top_cell` 来自 pcell 输出，不是 EMX Settings 字段 |

`max_parallel_jobs` **从不出现在 argv 里**——它只是工作流层的线程池并发上限（第8节），并被 `schemas.py:552-557` 的校验器明确禁止塞进 `extra_args`。

### 端口传递（signal/reference）

`_port_argument(port: EmxPort)`（`emx_runner.py:17-20`）：`reference is None` 时输出 `f"{name}={signal}"`，否则 `f"{name}={signal}:{reference}"`。三个字段一比一来自 `EmxSettings.ports[i]`（`EmxPortConfig{name,signal,reference}`，`schemas.py:396-416`），经 `_emx_preparation_options_from_contract`（`em_candidate_preparation.py:364-367`）原样传下去；`name` 是 EMX 自己的端口标签（如 `p01`），`signal`/`reference` 必须是 pcell 实际画出的 GDS 引脚文本标签（由 `_validate_configured_ports_against_geometry`，160-176，对着 `geometry.emx_ports_path` 建议端口做交叉核对）。

### 工作目录

**本地两条 runner 都不设置 `cwd`**：`_default_runner`（`emx_execution.py:41-52`）与 `_cshrc_emx_runner`（`em_optimizer_evaluator.py:203-218`）调用 `subprocess.run` 均无 `cwd=` 参数，EMX 继承调用方 Python 进程自身的 cwd。这之所以"能跑"，是因为 `gds_file`/`s_file`/`log_file` 都由 `outdir` 拼出（`outdir` 通常是绝对路径），但 `process_file`（`EmxSettings.process_file`，`schemas.py:509`，纯 `NonEmptyStr`）**没有任何强制绝对路径的校验器**——对照同文件里 `ProcessCorner.model_file`（227-244）明确要求 `PurePosixPath(value).is_absolute()`。远端路径则不同：`RemoteEmxRunner.__call__`（`remote_emx_runner.py:27-130`）把 `cd {remote_em_dir};` 显式拼进 csh 命令体（76-83），本地/远端在"是否显式定 cwd"上并不对称。

### cshrc / csh 包装

`build_cshrc_command(cadence_cshrc, command) = f"source {shlex.quote(cadence_cshrc)}; {command}"`（`csh_subprocess.py:16-18`），`build_argv_command(argv) = " ".join(shlex.quote(a) for a in argv)`（21-23）；`_cshrc_emx_runner`（`em_optimizer_evaluator.py:204-205`）组合成 `subprocess.run(["csh","-fc",wrapper], capture_output=True, text=True, check=False)`（206-211）。

### 超时

`_default_runner`（`emx_execution.py:41-52`）与 `_cshrc_emx_runner`（`em_optimizer_evaluator.py:203-218`）**都不传 `timeout=`**——本地 EMX 调用理论上可以永久挂起。远端 `RemoteEmxRunner`（`remote_emx_runner.py:19-26`）本可携带 `timeout_s: int | None`（在 `ssh.run(command, timeout_s=self.timeout_s)` 处使用，84行），但其唯一生产构造点 `remote_optimizer_flow.py:_remote_emx_runner`（433-446）把它**硬编码为 `timeout_s=None`**（445）。也就是说三条生产路径（本地直连、本地 cshrc、远端）**没有一条真正强制了 EMX 超时**；唯一支持并强制超时的是下面这段从未被接线的 `EmxResourceGuard.run()`。

### 成功判定

`run_prepared_em_candidate`（`emx_execution.py:55-95`）：`status = "pass" if process_result.returncode == 0 and not issues else "fail"`（74）。`issues` 汇总自：(a) `returncode != 0` 时追加 `f"emx returned nonzero exit code {rc}"`（64-65）；(b) `validate_touchstone_output(s_file, expected_ports=len(config.ports), expected_impedance=config.s_impedance)`（67-72；`emx_runner.py:125-168`）——**只做头部/元数据校验**：文件后缀 `.sNp` 与 `expected_ports` 一致（118-122,132-137）、文件存在（140-146）、文件"以 `!` 开头的头部块"中出现子串 `"EMX was run"`（148-157，正因此 `include_command_line` 才被强制 `True`）、至少一行 `#` 选项行严格等于 `# Hz S RI R <s_impedance>`（159-162）。**全程不解析任何 S 参数数值**（见第5节）。

### 资源守卫 `emx_resource_guard.py`

`EmxResourceGuard(EmxResourcePolicy(warn_gib, pause_gib, limit_gib, poll_interval_s=2.0, terminate_grace_s=5.0))`（19-41,54-66，构造时校验 `warn<pause<limit`）：
- `.wait_for_dispatch()`（82-103）：聚合 RSS ≥ `pause_gib` 时阻塞新调度，直至降回阈值以下或触发硬限。
- `.run(argv, *, timeout_s, candidate_id)`（105-185）：`Popen(..., start_new_session=True)`（114-120，独立进程组）；用 `communicate(timeout=poll_interval_s)` 轮询并用 `deadline = monotonic()+timeout_s` 强制超时（137-158）；每次轮询触发 `_sample_and_enforce_once`（220-266），后者遍历 `/proc/<pid>/status` 汇总**每个活跃 EMX 根进程的整棵进程树** RSS（`_process_tree_rss_bytes`，292-334），越过 `warn_gib` 记一次日志（239-241,257-263），越过 `limit_gib` 即"跳闸"——对所有活跃作业的进程组先 `SIGTERM` 再在 `terminate_grace_s` 后 `SIGKILL`（242-255,264-266,268-282）。
- **该模块功能完整、有专门单测（`tests/test_emx_resource_guard.py`），但在生产代码里完全没有被接线**：repo 级 `grep -rln "EmxResourceGuard("` 只命中它自身文件与 `.scratch/*/campaign.py`、`experiments/device_db_sweep_n28/sweep_driver.py` 这类一次性脚本；`optimizer_flow.py`、`optimizer_continuation_flow.py`、`remote_optimizer_flow.py`、`em_optimizer_evaluator.py` 均不构造它、也不用它包装 `em_runner`。生产环境真正起作用的内存纪律是 (a) EMX 自身的 `--max-memory=<max_memory_gb>G` 自限，和 (b) 运维手工用外部 `systemd-run --scope -p MemoryMax=...` 包住整个 Controller 进程树（`docs/guide/04-resources-and-safety.md:18-33` 明文写着"它只约束单个 EMX 的求解设置，不能据此保证整机或整个进程树的峰值"）。详见第10节 #1。

---

## 3. EM 缓存

`EmCacheKey`（`em_cache.py:14-20`）四个字段：`generator_id: str`、`geometry_config: dict`、`emx_config: dict`、`process_file: Path`、`gds_sha256: str`。

由 `_fingerprint_prepared(prepared, device_id=None)`（`em_artifacts.py:193-213`）构造：

- `geometry_config = _geometry_config_for_cache(prepared.geometry.manifest_path)`（45-61）——读取**pcell 生成器自己写的几何 manifest JSON**里的 `payload["geometry"]["config"]`（没有就退回整个 payload）。**这不是设计变量字典本身，也不是 GDS 几何字节**，而是生成器记录下来的、它认为足以刻画自己配置的字典（可能包含生成器派生/固定字段，不只是传入的 `parameters`）。
- 多器件时 `geometry_config = {"device_id": device_id, "geometry": geometry_config}`（201-205）——按 `device_id` 给指纹加命名空间，哪怕两个器件的几何参数字面相同也不会互相命中。
- `emx_config = prepared.emx_config.model_dump(mode="json")`——**整个 `EmxRunConfig`**（第2节 argv 表里的全部字段）序列化成 dict，此时还带着候选专属的绝对路径字段。
- `process_file = prepared.emx_config.process_file`——原样 `Path`（未 `resolve()`）。
- `gds_sha256 = sha256_file(prepared.geometry.gds_path) or ""`——**唯一真正的"几何字节"输入**。

`fingerprint_em_cache_key(key)`（`em_cache.py:32-51`）：
- `_CANDIDATE_PATH_FIELDS = ("gds_file","s_file","log_file")`（23）在哈希前从 `emx_config` 中剔除（39-41）——这三个字段是 `runs/em_optimizer/<candidate_id>/em/...` 下的候选专属绝对路径，若不剔除会让每个候选的指纹都不同，缓存永远不会跨候选命中（33-38 注释标注这是 "bug review 2026-07-16" 修过的坑）。
- **`emx_argv`（已经拼好的完整 CLI 参数列表）被有意排除在指纹之外**——`_fingerprint_prepared` 根本不读 `prepared.emx_argv`；注释理由是 argv 完全派生自 `EmxRunConfig` 字段加那三个路径，不带来指纹以外的新信息。
- 最终哈希 payload：`{generator_id, geometry_config, emx_config: <净化后>, process_file: str(path), process_file_sha256: _sha256_file(path)（文件不存在则为 `None`，26-29）, gds_sha256}`，`json.dumps(sort_keys=True, separators=(",",":"))` 后取 sha256 十六进制（42-51）。**`process_file` 的路径字符串和其内容哈希都进指纹**——同内容换路径、或同路径换内容，指纹都会变。

缓存根目录：`bundle.project_dir/"cache"/"em"`（`em_optimizer_evaluator.py:538,560`），条目落在 `<cache_root>/<fingerprint>/`（`em_cache.py:58-59`），内含拷贝的 `*.s*p` 文件与该条目自己的 `em_artifact_manifest.json` 快照（61-80）。

**`strict_provenance` 的含义**：`_cache_enabled(em_cache)`（`em_artifacts.py:216-221`）要求 `enabled=True` **且** `mode=="strict_provenance"`；`EmCacheSettings.mode: Literal["strict_provenance"]`（`schemas.py:613-615`）目前是唯一合法字面量——即当前实现里根本没有"非严格"模式，取这个名字是为未来的更宽松模式预留位置。"provenance" 的实质是：`EmCacheStore.restore()`（`em_cache.py:82-106`）在归还缓存前，**用当前候选要求的 `expected_ports`/`expected_impedance` 对缓存里的 Touchstone 重新跑一遍 `validate_touchstone_output`**（97-101）——不是"指纹对上了就无条件信"，而是"指纹对上了，还要重新过一遍与新跑一次同等的头部校验"。

命中/未命中行为（`em_artifacts.py:269-299`）：
- **命中**：`store.restore(...)` 返回 `True` → `cache_status="hit"`，**不跑 EMX**（`execution=None`，`ran_emx=False`），命中的文件被拷贝到 `prepared.emx_config.s_file`（`em_cache.py:104-105`）。
- **未命中**：`restore()` 返回 `False`（条目目录不存在/manifest 缺失/`*.s*p` 文件不是恰好一个/重新校验不过）→ 跑 `run_prepared_em_candidate`（280）；若随后 `status=="pass"` 才 `store.store(...)`（283-292），`cache_status="miss"`；EMX 失败则不存，`cache_status="miss_failed"`（294-295）。
- **禁用**：直接跑 EMX，`cache_status="disabled"`（296-299）。
- 无论哪一分支，**都会重新写** `em_artifact_manifest.json`（301-307）——manifest 是"每候选一份"，缓存条目是"每指纹一份"，两者生命周期不同：命中也会为**这个** `candidate_id` 生成一份新鲜的 manifest。
- **并发写入安全性**：`EmCacheStore.store()` 先在 `tempfile.mkdtemp(dir=root, prefix=f".{fingerprint}.staging-")` 暂存目录写好整个条目，再原子 `staging.rename(entry)`（67-76）；若 `entry` 已存在（另一个并发候选抢先写完），当前写者直接放弃并清理暂存目录（63-65,77-80，注释断言"First writer won; by construction the content is identical"——**这一断言从未被代码验证**，见第10节 #5）。

---

## 4. Nport 绑定（`m3_circuit_preparation.py` + `nport_binding.py`）

### 契约字段（`NportBindingConfig`，`schemas.py:588-607`）

`testbench`（须是已声明 testbench id）、`instance`（要патch的 Spectre 子电路实例名）、`device_id`（多器件时必填，语义上必填但 schema 上是 `Optional`，实际由 `validate.py:_validate_nport_bindings` 512-549 与 `m3_circuit_preparation.py:_validate_device_artifact` 294-347 两处补上强制）、`expected_ports`（`ge=1`）、`original_file: {path, sha256?}`（导出网表里当前写着的占位文件路径，以及一个**可选**的、仅当占位文件恰好还可读时才会核对的期望 sha256）、`replacement_path`（相对路径，禁止绝对/`..`，`schemas.py:602-607`）、`terminal_order`（信号侧端子名，按"应当出现在 nport 实例端子列表里的顺序"声明）。

### `prepare_candidate_circuit_bundles`（`m3_circuit_preparation.py:33-164`）—— M3 每候选 kernel

1. 读 `em_artifact_manifest.json`（42），先做结构校验（`_validate_artifact`/`_validate_device_artifact`，256-347：单/多器件分支，交叉核对每条绑定的 `expected_ports` 与 manifest 里 `touchstone.expected_ports`/`validation.ports`，见 363-400）；同时校验 `nport_bindings` 自身（`_validate_nport_bindings`，167-185：`len(terminal_order)==expected_ports`，以及若 `original_file.sha256` 存在且占位文件当下可读，核对 sha256 是否一致）。
2. 按 `testbench` 分组绑定（70-72）；每个 testbench：`materialize_testbench_bundle(project_dir, testbench_id, run_dir)`（`circuit_bundle.py:148-169`）把 `circuit/imported/<testbench_id>/*`（Maestro 导出网表树，过滤掉 `.spectre_port` 之类运行期文件，`SPECTRE_RUNTIME_ARTIFACTS`，`circuit_bundle.py:10,205-206`）复制进 `<run_dir>/circuit/`。
3. 每条绑定：`_touchstone_for_binding`（213-219，单器件用 `None` 键，多器件用 `device_id` 键）挑出对应 touchstone，`shutil.copy2` 到 `<circuit_dir>/<replacement_path>`（85-87），再调用 **`patch_nport_file_path`**（见下）。
4. 写 `<run_dir>/<testbench_id>/nport_patch_manifest.json`（105-125）与顶层 `circuit/m3_circuit_manifest.json`（135-157）。

### `patch_nport_file_path`（`nport_binding.py:101-160`）—— 真正的正则/解析 kernel（对文本，非纯函数，做文件 I/O）

- 前置校验：`_validate_replacement_path`（197-204，禁止绝对/`..`）、`_validate_snp_suffix`（207-212，后缀必须是 `.s<expected_ports>p`）、`_validate_terminal_order_length`（215-222）。
- 读入 `input_scs` 全文，`original_sha256 = sha256(text)`（114-115）。
- **语句切分** `_logical_statements`/`_statement_text`（176-187,172-173）：按 Spectre 的反斜杠续行约定，把物理行拼成"逻辑语句"（一行去掉行尾空白后以 `\` 结尾就并入下一行）。
- **实例匹配** `_find_nport_statement_matches`（163-169）：语句首 token 等于 `instance` **且**语句文本里出现子串 `" nport"` 才算匹配；**必须恰好一条**，否则 `NportPatchError`（117-121）——这是纯文本启发式，不是真正的 Spectre 网表解析器（例如同一逻辑语句里出现同大小写不敏感的 `" NPORT"` 会不匹配；反过来，注释里若恰好出现 `" nport"` 字样会被算作候选行本身不受影响，但极端情况下可能误判）。
- `FILE_RE = re.compile(r'file\s*=\s*"(?P<path>[^"]+)"')`（36）抠出带引号的占位路径，**必须与 `expected_original` 字符串完全相等**（不做路径归一化）才继续，否则 `NportPatchError`（128-131）。
- `_extract_nport_signal_order(statement_text, expected_ports)`（229-241）：用非贪婪 DOTALL 正则 `\((?P<nodes>.*?)\)` 抓语句里**第一个**括号分组当端子列表，按空白切分（先把续行反斜杠替换成空格），断言 token 数恰为 `expected_ports*2`（每个端口贡献一个信号节点+一个参考/地节点），**取偶数下标（每隔一个）的 token 作为信号侧顺序**——即代码假定网表按 `sig1 ref1 sig2 ref2 ...` 交替排列。此结果**必须与 `terminal_order` 完全相等**，否则 `NportPatchError`（133-137）。这是"sNp 列序 = 网表端子序"这条规则在代码里**唯一**被强制的地方，但它检查的是"用户在 `terminal_order` 里声明的顺序"与"网表里当下写着的顺序"是否自洽，**不是**去核对物理 `.sNp` 文件本身的端口列到底对应哪个信号（见下"sNp 索引序"）。
- 只替换被引号包住的路径子串（`statement_text[:start]+replacement_path+statement_text[end:]`，139-143），拼回整份文本前断言**patch 前后物理行数不变**（144-146），整份文件写回（150-151）。
- 返回 `NportPatchResult(instance, input_scs, original_file, replacement_path, expected_ports, original_sha256, patched_sha256)`。

### 留存证据

`_patch_manifest_entry`（`m3_circuit_preparation.py:221-242`）为每条 patch 记录：`testbench_id, instance, device_id, input_scs, original_file, replacement_path, expected_ports, terminal_order, original_file_sha256_status`（`_original_file_sha256_status`：`"not_requested"`/`"not_checked_unreadable"`/`"checked_match"`——注意**没有 `"checked_mismatch"` 这一档并继续走**；不匹配时 `_validate_nport_bindings`，167-185，会在整个 M3 阶段开始前就把 issue 加进去，让整段 M3 直接 `status="fail"`）、`original_input_sha256`、`patched_input_sha256`、`snp_path`、`snp_sha256`。汇总进 `nport_patch_manifest.json`，再被 `m3_circuit_manifest.json.testbenches[].nport_patch_manifest` 引用。

### sNp 索引序规则的真实约束力

docs 明文写着（`docs/guide/02-requirement-reference.md:118`）："**sNp 的索引按 EMX port name 的字典序排列**，不是 YAML ports 列表顺序……使用 p01…p04 可明确保持 P1/N1/P2/N2 的预期顺序；直接用 P1/N1/P2/N2 作 name 时，字典序是 N1/N2/P1/P2。" 但 `nport_binding.py`、`m3_circuit_preparation.py`、`emx_runner.py:validate_touchstone_output` 里**没有任何代码打开 `.sNp` 文件、按 `EmxSettings.ports[].name` 排序去反向核对物理列序是否真的等于 `terminal_order`**。唯一的自动化检查是上面描述的"网表自洽性"检查（网表里的端子顺序 = 用户敲进 `terminal_order` 的顺序）。EMX 到网表这一段"接线对不对"完全靠人——这正是approval checklist 里 `nport_bindings_user_approved` 字段存在的原因（`requirement_intake.py:99-107`），文档也明确提醒"不要仅因为端口数量相同就认为接线正确"（`docs/guide/02-requirement-reference.md:120`）。

---

## 5. sNp 处理与 Passive Diagnostic Constraints

### Touchstone 解析在哪里、用什么库

**本包里唯一的 Touchstone 相关解析是纯头部/元数据校验**：`validate_touchstone_output(path, *, expected_ports, expected_impedance)`（`emx_runner.py:125-168`）用标准库 `Path.read_text()` + `str.splitlines()` 手写解析（对 `src/em_ic_opt_workflow/` 全量 `grep -rn` 查找 `skrf`/`scikit-rf`/`Touchstone(` **零命中**，未使用任何第三方 RF/Touchstone 库）。只收集"以 `!` 开头的头部行直到第一条非 `!` 行为止"（148-153）这一段文本，检查：(a) 后缀 `.sNp` 数字与 `expected_ports` 一致（118-122,132-137）；(b) 文件存在（140-146）；(c) 头部文本里出现子串 `"EMX was run"`（156-157）；(d) 至少一行 `#` 选项行严格等于 `# Hz S RI R <impedance>`（159-162）。**全文件范围内不存在任何读取/解析 S 参数数值矩阵的代码**——没有频率列、没有任何 `S(i,j)` 复数值、没有无源性/互易性/NaN 检查。`EmCacheStore.restore()`（`em_cache.py:97-101`）在归还缓存前重跑的也是这同一个头部校验。

### "Passive Diagnostic Constraints" 到底算了什么：**在当前版本里什么都没算**

`PassiveDiagnosticConstraintsConfig`（`schemas.py:696-698`）的形状与普通 `Constraints` **完全相同**（`list[ConstraintSpec]`，即 `{metric, op, value}`），不带任何 S 参数专属字段。对 `bundle.passive_diagnostic_constraints` 的全部消费者做穷举：

- `requirement_intake.py`：只是把 YAML 段原样渲染进 `config/passive_diagnostic_constraints.yaml`（354-358），无计算。
- `validate.py:_validate_passive_diagnostic_constraints`（586-605）：**唯一的运行期逻辑**——只检查每条 `constraint.metric` 是否在 `Metrics` 里声明过（592-604）；**甚至没有像它旁边的 `_validate_metrics`（759-780，尤其 776-779）那样调用 `parse_constraint_threshold(constraint.value, unit)`** ——即它比普通 Constraints 校验得还要松。
- `package.py`：只是把 `passive_diagnostic_constraints.yaml` 列进"打包进不可变项目"的可选文件清单（24行），无计算。
- **`src/em_ic_opt_workflow/` 里没有任何其它文件引用它**：对 `passive_diagnostic` 做全包递归 `grep`，命中仅限上述三个文件加 `schemas.py` 自身的定义。真正计算 `metrics`/`objective`/`constraints_passed`/约束惩罚的模块——`em_circuit_evaluator.py`、`multi_testbench_aggregation.py`、`native_turbo.py`、`openbox_backend.py`——**都不读取** `bundle.passive_diagnostic_constraints`。两个优化后端唯一使用的约束惩罚实现（`native_turbo.py:_constraint_penalty`，1289-1312；`openbox_backend.py:_constraint_residuals_for_metrics`，1995-2015）都只遍历 `metrics_config.constraints`，即普通 `bundle.metrics.constraints`。

结论：**"Passive Diagnostic Constraints" 目前是一个 schema 校验通过、但功能上完全隔离、不产生任何实际效果的可选段**——它能从 requirement 走到 config 文件，能被检查"metric 名字是否存在"，但从不被任何决定通过/失败、目标、惩罚的代码读取。**因此这里没有"公式"可以移植**：如果 `ic-opt-modular` 打算让它真正生效（比如直接从原始 sNp 独立算出 SRF/Q/无源性一类诊断量，绕开 OCEAN/Spectre 指标管线），那是全新工作，不是搬运——`EM-opt-workflow` 里不存在这样的计算代码。

---

## 6. 多器件（EM Devices）处理

### 契约

`EmDeviceConfig`（`schemas.py:438-452`）：`id`/`parameter_prefix`（均须是裸标识符，`validate_name`）、`geometry: EmDeviceGeometryConfig`（418-427，内嵌完整 `GeometryGeneratorConfig`，且强制 YAML 里必须显式写出 `fixed_parameters`，哪怕是 `{}`，见 `_generator_fixed_parameters_are_explicit`）、`emx: EmDeviceEmxConfig`（429-436：自己的 `s_file_name` + 自己的 `ports` 列表——**每个器件有独立端口表和独立 sNp 文件名**；`EmxSettings` 里非端口/非文件名的字段——`binary`/`mode`/`frequency_hz`/`accuracy`/... ——则对所有器件一视同仁地套用，见 `_emx_preparation_options_from_contract(bundle.emx.emx, s_file_name=device.emx.s_file_name, ports=device.emx.ports)`，`em_candidate_preparation.py:498-502`）。`EmDevicesConfig`（454-466）强制 `id` 与 `parameter_prefix` 均全局唯一。

### 参数解复用 `_split_multi_device_parameters`（`em_candidate_preparation.py:394-436`）

- 多器件项目下**所有**设计变量名必须带 `.`（`<parameter_prefix>.<generator_parameter>` 形式）；出现裸名直接报错（400-405）。
- `allowed_parameters_by_prefix = {device.parameter_prefix: set(device.geometry.generator.parameters)}`（407-410），任何 `prefix.parameter` 若 `parameter` 不在该器件自己的 `generator.parameters` 里即报错（411-419）。
- 逐器件切出 `{parameter: parameters[f"{prefix}.{parameter}"] for parameter in device.geometry.generator.parameters}`（423-432），缺哪个报哪个（434-435）。
- 返回 `dict[device.id, dict[本地参数名, 值]]`。

### 逐器件 pcell + EMX 配置构建（`prepare_em_devices_from_contract`，472-516）

循环 `bundle.em_devices.devices`：`_merged_device_geometry_parameters(device, local_parameters)`（313-332，与单器件版 `_merged_geometry_parameters` 289-310 同形，只是读 `device.geometry.generator`）→ 组装 `EmCandidatePreparationConfig`，`gds_name=f"{device.id}.gds"`（495，**不是**单器件固定的 `"device.gds"`）→ `prepare_em_candidate(request, outdir=outdir/"devices"/device.id/"em")`（504-507）。**每个器件都是一次完全独立的 pcell + DRC + EMX-argv 构建**（第1节 kernel 的 N 次独立调用），不是一次合并的 EMX 调用。

### 逐器件 EMX 运行 + 逐器件缓存（`produce_em_candidate_artifacts`，`em_artifacts.py:361-428`）

循环 `prepared.devices`，每个都走同一个单器件 kernel `_produce_em_candidate_artifact(..., cache_device_id=device.device_id)`（381-389）——**缓存真正按器件独立**（指纹按 `device_id` 加命名空间，见第3节），同一候选里两个器件可以一个命中一个未命中。聚合规则：`ran_emx = any(设备 ran_emx)`（390）；`cache_statuses` → `_aggregate_cache_status`（347-358：有失败则 `"failed"`；全空则 `"disabled"`；全相同则该值；否则 `"mixed"`）；`device_fingerprints` → `_aggregate_cache_fingerprint`（336-344：对 `[{device_id,fingerprint},...]` 排序后 JSON 化再 sha256）；聚合对象上的 `emx_execution` 字段**只保留一个代表性执行结果**——有失败设备则是第一个失败的，否则是第一个设备的（398-405,427），即便实际独立跑了 N 次。写一份聚合 `em_artifact_manifest.json`（schema_version `"1.1"`，`{schema_version, candidate_id, candidate_parameters, devices:[...]}`，`_write_aggregate_em_artifact_manifest`，172-190）。

### 下游（M3/M4）的多器件感知

`m3_circuit_preparation.py:_device_touchstones`（201-211）按 manifest 是否带 `"devices"` 列表分单/多器件分支；`NportBindingConfig.device_id` 在存在 `em_devices.yaml` 时被语义要求必填（`validate.py:512-549`、`m3_circuit_preparation.py:294-347` 两处强制），每条绑定经 `_touchstone_for_binding`（213-219）挑自己器件的 touchstone。`em_optimizer_evaluator.py` 的失败归因辅助函数（`_touchstone_failed_device_ids_from_em_artifact_manifest`，366-388；`_device_touchstone_has_failure`，391-413）专门用来在"聚合 `emx_failed`"状态下反查究竟是哪个/哪些 `device_id` 的 EMX 失败了，好把具体设备名写进 issues 而不是只报一句"EMX failed"。

---

## 7. 失败分类法

### `evaluate_one_em_optimizer_candidate` 内部的阶段状态

`_STAGE_FAILURE_LABELS`（`em_optimizer_evaluator.py:468-473`）：

| 状态字符串 | 触发条件 | 构造函数 |
| --- | --- | --- |
| `candidate_preparation_failed` | `prepare_em_candidate_from_contract`/`prepare_em_devices_from_contract` 抛出 `EM_CANDIDATE_EXPECTED_FAILURES`（45-51：`ValueError`含`DrcViolationError`、`OSError`、`RuntimeError`、`subprocess.TimeoutExpired`）——即 pcell/DRC/端口不匹配类失败 | `_stage_failure_observation("candidate_preparation_failed", exc)`（460-465），调用点 532,554 |
| `em_artifact_failed` | `produce_em_candidate_artifact(s)` 自身抛异常（少见，多为缓存/IO 错误），或 EMX 已跑完但 manifest/touchstone provenance 复检不过（`_em_artifact_validation_issues_from_artifact`，262-318） | 异常路径走 `_stage_failure_observation`（544,566）；校验不过路径走 `_em_artifact_failure_observation`（229-234,574） |
| `emx_failed` | `artifact.emx_execution is not None and artifact.emx_execution.status != "pass"`（567） | `_emx_failure_observation(_emx_failure_issues_from_artifact(artifact))`（221-226,237-259；多器件时尝试点名具体 `device_id`） |
| `circuit_preparation_failed` | `prepare_candidate_circuit_bundles` 抛异常，**或**返回 `status != "pass"`——**两者被折成同一个状态字符串**（583-589 注释明写："the optimizer observes one status whether M3 raised or returned non-pass; the issues list carries the distinction"） | `_stage_failure_observation`/`_circuit_preparation_failure_observation`（450-457） |
| `em_circuit_evaluation_failed` | `evaluate_fixed_em_candidate`（M4）抛出预期失败 | `_stage_failure_observation`（603） |
| 透传 `result.status`（如 `"succeeded"`、`"constraint_failed"` 等） | M4 正常返回未抛异常 | `native_observation_from_em_result` 处理，见下 |

### `native_observation_from_em_result`（54-91）—— M4 结果 → `NativeTurboObservation`

- `status in ("succeeded","constraint_failed") and observation.metrics`（63-66）→ `status="recorded"`，`issues=[]`（67-75）。**58-62 行注释点明关键设计**："constraint_failed" 是*设计*结果（指标提取干净，只是用户 Constraints 没过），刻意归一成 "recorded"，让**优化器**（而非 EM 评估器）用 `native_turbo.py:evaluate_candidate_objective`/`_constraint_penalty` 重新算一遍梯度化约束惩罚——这是 EM 路径与非 EM Spectre-only 路径在约束处理上严格对齐的机制。
- `status=="succeeded"` 但无指标 → `issues=["succeeded but no metrics extracted"]`，`status` 原样透传（77-81）。
- 其余一律 → `status="real_check_failed"`，`issues=_collect_failure_issues(result)`（82-91；94-118：按 `package_result`/`execution_result`/`observation` 三处 issues 去重合并，三处皆空则合成 `f"em circuit evaluation failed: {result.status}"`）。

### 批级归一化（`make_em_candidate_batch_evaluator`，665-693）

`observation.status in {*_STAGE_FAILURE_LABELS, "emx_failed"}`（即上表全部五个状态）**统一重写成 `"real_check_failed"`**（687-690）后才交还给 TuRBO/OpenBox 后端——优化器视角里"真实检查失败"只有一种通用状态；细分的阶段名只作为字符串活在 `observation.issues` 与磁盘 manifest 里，不是可编程区分的独立状态。

### `failure_penalty` 路径（`native_turbo.py`）

`_objective_evaluation_for_observation`（**1190-1208**，注意不是 189 附近——本文档写作过程中曾误读为此处，已用 `Read(offset=1180)` 复核纠正）：`observation.metrics is None` 时直接返回 `ObjectiveEvaluation(status=observation.status, objective=optimizer_config.optimizer.failure_penalty, fom=None, constraints_passed=False, constraint_penalty=0.0, issues=observation.issues or [observation.status])`——candidate 的目标值就是 `OptimizerSettings.failure_penalty`（`schemas.py:800`，单个用户常量），**"pcell 失败"与"EMX 超时"与"Spectre 崩溃"对搜索算法而言数值上完全等价**。当 `status=="recorded"`（有指标）时，`evaluate_candidate_objective`（**1211-1272**）自己还能再产生三种结果：
- `status="metric_failed"`（指标缺失/非有限，或目标表达式求值失败/非有限，`objective=failure_penalty`，1216-1247）
- `status="constraint_failed"`（`objective=failure_penalty+constraint_penalty`，1249-1258，`constraint_penalty` 由 `_constraint_penalty`/`_normalized_violation`，1289-1325，对每条越界约束累加"归一化违反量的平方"）
- `status="feasible"`（`objective=-fom` 或 `fom`，视 `ObjectiveDirection`，1260-1272）

`openbox_backend.py` 直接 `import` 并复用 `native_turbo.py` 的 `evaluate_candidate_objective`（其 `_trace_from_observation`，1939-1992；`_constraint_residuals_for_metrics`，1995-2015，为 OpenBox 自己的 EIC 型约束采集函数重新实现了同形状但独立的约束残差计算，同样只读 `metrics_config.constraints`）——**两个优化后端在 EM 路径上共享同一套失败/约束语义实现**。

---

## 8. 并发

三个命名并发旋钮（docs/guide/04-resources-and-safety.md 表格已总结，源头在 schema）：

- `OptimizerSettings.batch_size`（`schemas.py:797`，`ge=1`）——优化器一次提议批次里的候选数。
- `EmxSettings.max_parallel_jobs`（524-526，`1..MAX_ALLOWED_EMX_PARALLEL_JOBS=8`，常量定义于 34 行）——EM 候选流水线的并发上限。
- `SpectreSettings.parallel_jobs`（738-740，同 `1..8`）——**在 EM optimize 模式下被复用为候选级 M4 并发上限**（而非它在 fix-run 模式下"同一固定点内 TB/corner 子任务并发"的第二重含义，见 `docs/guide/02-requirement-reference.md:169` 与第10节 #8）。

三者在 `make_em_candidate_batch_evaluator`（`em_optimizer_evaluator.py:615-695`）里合成一处：

```python
candidate_parallel_jobs = _resolve_candidate_parallel_jobs(bundle, override)   # 859-873：CLI --parallel-jobs 覆盖优先，否则取 bundle.spectre.spectre.parallel_jobs
max_workers = min(
    bundle.optimizer.optimizer.batch_size,
    bundle.emx.emx.max_parallel_jobs,
    candidate_parallel_jobs,
)
```
（657-661；结果 `<1` 时 `raise ValueError`，662-663）。`evaluate(candidates)`（665-693）内 `workers = min(max_workers, len(candidates))`，用**一个** `ThreadPoolExecutor(max_workers=workers)` 并发派发 `evaluate_one_em_optimizer_candidate`（672-684），`as_completed` 收集后**按原始候选顺序重排**再返回（685-692，完成顺序与调用方无关）。`em_optimizer_resource_summary`（829-856）用同一公式算出 `em_effective_candidate_workers` 并在跑完后写入报告/JSONL（`_append_em_resource_summary`，876-912），确保产物里始终留痕当次实际用的并发数。

**谁并行、谁串行**：
- **批内跨候选并行**：整条单候选流水线（pcell 渲染 → DRC → EMX 子进程 → 缓存判定 → M3 nport patch → M4 对每个 testbench 跑 Spectre+OCEAN）在一个线程里同步执行；最多 `max_workers` 个候选的流水线同时跑。
- **单候选内部串行**：pcell 必须先于 EMX；EMX（或缓存命中）必须先于 M3；M3 先于 M4。M4 内部 `run_em_circuit_adapter_packages`（`em_circuit_evaluator.py:230-264`）是一个**普通 `for testbench_id in testbench_ids` 循环**，同步调用 `run_spectre_ocean_adapter`——**同一候选的多个 testbench 是逐个跑的，不并发**，尽管 `SpectreSettings.parallel_jobs` 这个名字听起来像是要并行化点什么；该字段在 EM-optimize 模式下的唯一实际含义就是上面的候选级上限（对照 fix-run 模式，`docs/guide/02-requirement-reference.md:169`："固定点串行处理，同一点中的 TB/corner 子任务按 `parallel_jobs` 并发"——fix-run 真的会并行 TB/corner 子任务，EM-optimize 不会）。
- **EMX 自身内部并行**是另一层，与 Python 调度完全无关：`EmxSettings.max_cpu_per_job`（527-529，`1..16`）变成 EMX 的 `--parallel=<n>` CLI flag（第2节），是 EMX 进程内部自己的多线程。
- `native_turbo.py`（`batch_worker_count = min(self.optimizer.optimizer.batch_size, self.parallel_jobs)`，**430 行**）与 `openbox_backend.py`（`min(candidate.batch_size, parallel_jobs)`，1988 行）各自还算了一个同名的展示/记账值挂在每条 trace 上，**不是**另一个独立线程池；真正的并发全部由 `make_em_candidate_batch_evaluator` 里那一个 `ThreadPoolExecutor` 提供，优化器把它当作外部注入的 `batch_evaluator` 调用。

---

## 9. Kernel 复用表

### 值得原样（或仅做"文件 I/O → 内存对象"边界改造后）复用的纯/近纯函数

| 函数 | 签名 / 位置 | 纯度 | 目标 Stage（`pcell → emx → bind_nport → spectre/ocean/extract`） | 备注 |
| --- | --- | --- | --- | --- |
| `build_emx_argv` | `build_emx_argv(config: EmxRunConfig) -> list[str]`，`emx_runner.py:23-94` | 纯 | `emx` | 直接对标 `stages/spectre_chain.py:Spectre.argv()`（71-76）的形状；把 `EmxRunConfig` 换成新的 EM spec 类型即可 |
| `_port_argument` | `_port_argument(port: EmxPort) -> str`，`emx_runner.py:17-20` | 纯 | `emx` | `build_emx_argv` 的子函数 |
| `validate_touchstone_output` | `validate_touchstone_output(path, *, expected_ports, expected_impedance) -> TouchstoneValidationResult`，`emx_runner.py:125-168` | 读一个文件，其余纯 | `emx`（跑完后的校验） | 直接复用；其数值盲区见第5节，若新管线要更强校验需另加 |
| `EmCacheKey` + `fingerprint_em_cache_key` | `em_cache.py:14-20,32-51` | 读 `process_file` 字节，其余纯 | `emx` 的 `Stage.fingerprint(inp) -> str \| None` | **`eval/stage.py:58-59` 的 `fingerprint` 方法就是为 EM 阶段预留的**（`eval/engine.py` 模块 docstring 11-12 行原话："Stage caching (`Stage.fingerprint`) is reserved for the first cacheable stage (EM)"）；复用的是"哪些字段进哈希、哪些要剔除（候选专属路径）、argv 不进哈希"这套配方本身，不是 `EmCacheStore` 这个存取类 |
| `_extract_nport_signal_order` | `_extract_nport_signal_order(statement_text: str, expected_ports: int) -> list[str]`，`nport_binding.py:229-241` | 纯（`str -> list[str]`） | `bind_nport` | 端子顺序抽取正则 |
| `patch_nport_file_path` 的核心逻辑（`_find_nport_statement_matches`/`_logical_statements`/`_statement_text`/`FILE_RE`/拼接替换，139-146） | `nport_binding.py:101-241` | 读写一个文件；核心是纯文本变换 | `bind_nport` | 复用正则/校验逻辑本身，但签名应改成"输入网表文本 `str`、输出 patch 后文本 `str`"，以贴合 `spectre_chain.py:Netlist(text: str)` 这种值类型约定，而不是像今天这样直接原地读写 `input_scs: Path` |
| `_split_multi_device_parameters` / `_merged_geometry_parameters` / `_merged_device_geometry_parameters` | `em_candidate_preparation.py:394-436,289-332` | 纯 dict 变换 | `pcell` | 逐器件参数路由逻辑，原样复用 |
| `_parse_emx_port_line` / `_load_emx_ports` / `_validate_configured_ports_against_geometry` | `em_candidate_preparation.py:138-176` | 读一个文件，其余纯 | `pcell` → `emx` 边界 | 端口交叉核对 |
| `_extract_emx_version` / `_extract_warning_lines` | `emx_execution.py:136-149` | 纯（正则） | `emx`（可选） | 日志解析，锦上添花 |
| `_process_tree_rss_bytes` | `emx_resource_guard.py:292-334` | 读 `/proc`，其余纯计算 | 不在 EM 链路本身，但若 `Resources(threads,memory_gb)` 要真正落地成"实时内存监控"（`DESIGN_CN.md:206-207` 提到的并发槽计算），这是当前两个仓库里**唯一**做进程树 RSS 聚合的实现，值得挖出来复用 |

### 应当丢弃的编排/仪式性代码（typed 内存对象流水线不需要）

| 代码 | 位置 | 为什么可丢 |
| --- | --- | --- |
| `write_em_artifact_manifest`/`_em_artifact_manifest_payload`/`_write_aggregate_em_artifact_manifest` | `em_artifacts.py` | 换成 `emx` Stage 直接返回一个 typed 输出对象；调试留痕可以仍写到 `ctx.workdir`，但不需要被下一阶段重新解析 |
| `_em_artifact_validation_issues_from_artifact`/`_device_touchstone_validation_issues`/`_touchstone_failed_device_ids_from_em_artifact_manifest`/`_device_touchstone_has_failure`/`_resolve_touchstone_manifest_path`/`_project_root_from_em_artifact_manifest`/`_looks_like_aggregate_em_artifact_manifest_path` | `em_optimizer_evaluator.py:237-447`（约210行） | 整段代码存在的唯一目的是"重新打开两步之前刚写的 JSON，反推哪个设备的 EMX 失败了"；手里已经有 typed 的每设备执行结果时，这退化成读一个字段 |
| `_validate_m3_circuit_manifest` | `em_circuit_evaluator.py:571-749`（约180行） | 只有当 M3/M4 是两个通过磁盘 JSON 交接的独立可调用阶段时才需要这种"收到手之后从头再核一遍"的防御；typed `BindNportResult` 直接传给下一阶段的 `run()` 不需要 |
| `_STAGE_FAILURE_LABELS`/`_stage_failure_observation`/`_emx_failure_observation`/`_em_artifact_failure_observation`/`_circuit_preparation_failure_observation` | `em_optimizer_evaluator.py:229-234,450-473` | 被 `ic-opt-modular` 已经实现的通用机制整体取代：`StageFailure(*issues)` → 引擎记为 `status=f"failed:{stage.name}"`（`eval/stage.py:21-25`；`eval/engine.py:120-160`，尤其 138-142 行"某个 point 级阶段失败，则该点下所有 child 都拿到相同的 `failed:<stage>` 状态"） |
| `EmCacheStore`（暂存目录+原子 rename 的文件存取类） | `em_cache.py:54-106` | 哈希配方（上表）值得留，但存取机制应统一进通用的 `.icopt/cache/<stage>/<fingerprint>/`（尚未实现，见第10节 #10），不必再单独维护一个 EM 专属的 cache store 类 |
| `_apply_em_run_retention`（双目录处理）+ 全部 `run_retention.py`（557行） | `em_optimizer_evaluator.py:125-177`；`run_retention.py` | 被 `eval/engine.py:_retain`（163-172，约10行）整体取代——一旦 pcell/emx/bind_nport/spectre/ocean/extract 都写进同一个点的 `ctx.workdir`（而不是像今天这样分裂成 `runs/em_optimizer/<candidate_id>` 与 `runs/real/<run_id>` 两棵树），双目录保留问题本身就消失了 |
| `finish()` 里失败证据 stub manifest | `em_optimizer_evaluator.py:497-521` | 只是为了满足 `optimizer_em_artifacts.py` 的"result_manifest 总存在"假设；新引擎的 `Observations`/`observations.jsonl` 不需要这层兼容垫 |
| `optimizer_em_artifacts.py`（全 482 行） | 同名文件 | 整个模块的工作是沿着 `result_manifest → em_circuit_evaluation_manifest → {m2_em_artifact_manifest, m3_circuit_manifest} → nport_patch_manifest → waveform csv` 这条链把人类可读摘要重新拼出来——这条链之所以拆成这么多跳，正是因为旧设计靠磁盘 JSON 交接各阶段；新引擎里 `Observation.children[testbench]`（含 `sim_dir`/`trace`，参见 `DESIGN_CN.md:100-108` 与 `eval/stage.py:StageContext.record`，41-44）已经是结构化的，这层重建工作基本消失 |

---

## 10. 意外与脆弱点

1. **`emx_resource_guard.py` 功能完整、单测齐全，但在生产代码里完全没有被接线。** 详见第2节末段。Repo 级 `grep` 确认唯一非测试调用方是 `.scratch/*/campaign.py`、`experiments/device_db_sweep_n28/sweep_driver.py` 一类一次性脚本；`optimizer_flow.py`/`optimizer_continuation_flow.py`/`remote_optimizer_flow.py`/`em_optimizer_evaluator.py` 一个都没有构造它。真正生效的内存保护是 EMX 自身的 `--max-memory` 与运维手工的 `systemd-run --scope -p MemoryMax=...`（`docs/guide/04-resources-and-safety.md:24-33`）。**对再实现者的直接影响**：`ic-opt-modular/docs/refactor/DESIGN_CN.md:206-207` 已经决定"资源包络变成阶段声明……不再需要 em-opt 单独的 `emx_resource_guard.py`"——但 `eval/engine.py` 目前唯一的资源门是模拟次数预算 `spec.budget.max_simulations`（`engine.py:79-83`），**并不追踪实时内存**；也就是说"用 Python 主动看住 EMX 进程树内存"这件事，旧代码写好了但没接，新代码目前也还没有等价物。如果确实需要，`_process_tree_rss_bytes`（`emx_resource_guard.py:292-334`）的 `/proc` 遍历逻辑是两个仓库里唯一现成的实现，值得作为参考移植，而不是假装这件事已经被"阶段声明 `resources`"解决了。

2. **本地与远端三条 EMX 执行路径都没有真正的超时。** `_default_runner`（`emx_execution.py:41-52`）、`_cshrc_emx_runner`（`em_optimizer_evaluator.py:203-218`）都不传 `timeout=`；`RemoteEmxRunner` 支持 `timeout_s` 但唯一的生产构造点 `remote_optimizer_flow.py:_remote_emx_runner`（433-446）硬编码 `timeout_s=None`（445）。对照 Spectre/OCEAN：`execution_adapters/spectre_ocean.py` 每次子进程调用都从 `context.request.spectre.get("timeout_s", 3600)` 取真实超时并传下去（331,350,780,806）。一次卡死的 EMX（license 等待、内部死循环）会永久占住一个 worker 线程，没有任何代码路径能让它超时退出。

3. **EMX 从不设置 `cwd`，`process_file` 是路径类字段里唯一没有强制绝对路径的一个。** `_default_runner`/`_cshrc_emx_runner` 均不传 `cwd`，EMX 继承调用进程自身的工作目录。这能work 全靠 `gds_file`/`s_file`/`log_file` 由（通常绝对的）`outdir` 拼出；但 `EmxSettings.process_file`（`schemas.py:509`）是裸 `NonEmptyStr`，没有像同文件 `ProcessCorner.model_file`（227-244）那样强制 `PurePosixPath(value).is_absolute()`。移植到新引擎时要注意：`Executor.run(argv, *, cwd, ...)`（`DESIGN_CN.md:118`）把 `cwd` 变成了显式必填参数——写 `emx` Stage 时必须显式决定填什么，顺手把 `process_file` 缺失的绝对路径校验也补上会更稳。

4. **"sNp 按 EMX port name 字典序排列"这条整个 nport 绑定契约赖以成立的规则，只存在于文档，不是代码不变量。** 第4节已详述：`nport_binding.py` 只核对"用户在 `terminal_order` 里声明的顺序"与"网表里当下写的顺序"是否自洽，从不打开物理 `.sNp` 反查 EMX 自己的端口排序约定。接错线会产生一个语法上完全"校验通过"、物理上端子对错的候选，当前唯一的安全网是人（`nport_bindings_user_approved` 审批字段 + 文档警告，`docs/guide/02-requirement-reference.md:120`）。

5. **`EmCacheStore.store()` 的"先到先得，内容必然相同"只是断言，从未验证。** `em_cache.py:63-65` 的注释直接承认这一点。若某个生成器存在隐藏的非确定性（同指纹却产出不同 Touchstone），第二个候选会被悄悄丢弃自己的结果，转而拿到第一个候选的文件，没有任何交叉哈希检查、没有警告、没有 issue 记录。

6. **"Passive Diagnostic Constraints" 贯穿 schema/requirement/打包全链路，却什么都不算（第5节）。** 容易被误读成"S 参数诊断公式一定在某处，只是没找到"——事实是除了一个比普通 Constraints 还弱的"metric 名字是否存在"检查（`validate.py:586-605`）之外，没有任何代码计算它。

7. **EM 失败状态的信息在两处被有意丢弃。** 第一次，`evaluate_one_em_optimizer_candidate` 记录了五个不同的阶段名；第二次，`make_em_candidate_batch_evaluator`（687-690）在优化器看到之前就把它们（连同 `emx_failed`）全部折叠成一个 `"real_check_failed"`；第三次，`failure_penalty` 把它们全部映射成同一个数值常量。这是刻意的"与非 EM 路径保持一致"设计（`native_observation_from_em_result` 58-62 行注释明确说明理由），不是缺陷——但如果新引擎打算给 `status=f"failed:{stage}"` 更细的粒度（比如让代理模型区分"参数导致的 DRC 失败"与"工具挂了"两类失败），那是相对今天的**行为变更**，不是直接搬运。

8. **`SpectreSettings.parallel_jobs` 在不同工作流模式下语义不同，字段/校验代码却是同一份。** EM-optimize 模式下是候选级并发上限（参与 `make_em_candidate_batch_evaluator` 的三方 `min`）；fix-run 模式下是"同一固定点内 TB/corner 子任务并发"（`docs/guide/02-requirement-reference.md:169`）。schema 定义完全相同（`schemas.py:738-740`），`optimizer_contract_issues`（`validate.py:852-902`，尤其 880-887 行 `optimizer.batch_size > spectre.parallel_jobs`）只按第一种含义做跨字段校验——未来若 EM 出现类似 fix-run 的模式，这条校验不能直接照搬。

9. **`ic-opt-modular` 里已经落地的 `Spectre` Stage 丢了旧代码的"瞬时 socket 失败重试"。** `stages/spectre_chain.py:Spectre.run()`（78-94）不重试；而 `execution_adapters/spectre_ocean.py:_run_spectre_with_retries`/`_is_transient_spectre_socket_failure`（339-374）会在 `spectre.out` 出现 `"can't create server socket"` 时重试一次（`SPECTRE_MAX_ATTEMPTS=2`）。这与 EM 链路本身无关，但因为 EM 流水线被规定原样复用这三段已实现的 `spectre/ocean/extract` Stage（`DESIGN_CN.md:191`），EM 侧会连带继承这个回归——顺手提醒 `spectre_chain.py` 的维护者是否是有意简化。

10. **`Stage.fingerprint()` 就是为 EMX 阶段预留的接口，但通用引擎目前完全不调用它。** `eval/engine.py` 模块级 docstring（11-12 行）原话："Stage caching (`Stage.fingerprint`) is reserved for the first cacheable stage (EM); this engine does not consult it yet." 而 `_run_point`/`_run_stages`（`eval/engine.py:120-160`）目前对每个 Stage 都无条件调用 `stage.run()`。这意味着把 `fingerprint_em_cache_key` 的哈希配方搬进新 `EmxStage.fingerprint()` 只完成了一半：`_produce_em_candidate_artifact` 今天实现的"先查缓存、命中就跳过整个 EMX 调用"这条分支（`em_artifacts.py:269-299`），在通用引擎里还没有位置——要么往 `eval/engine.py` 里加一段通用的"缓存命中即跳过 `run()`"逻辑，要么让 `emx` Stage 自己在 `run()` 内部先查自己的缓存再决定要不要真的跑 EMX（两种设计在"引擎 vs 阶段"到底谁拥有"这份工作是否已经算过"这件事上是不同的架构选择，需要在写 `emx` Stage 之前先定下来，而不是想当然认为 `fingerprint()` 方法一实现缓存就自动生效）。

---
