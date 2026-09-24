# 04 · EM 侧真实录制数据与测试 Fake 盘点

调研对象：`<em-opt>`（只读，未修改该仓库任何文件）。
目的：为 `ic-opt-modular` 的 EM 阶段链 `pcell → emx → bind_nport → spectre → ocean → extract`
（以及纯 EM 路径 `pcell → emx → measure`）建立"回放一致性"（replay parity）证明所需的输入清单——
即 Spectre 一侧已经用 `tests/ic_opt/test_replay_parity.py` + `tests/ic_opt/fakes.py::FakeSpectreExecutor`
做过的事情，在 EM 一侧需要哪些真实录制数据、以及可以照抄哪些 em-opt 的测试 fake 模式。

方法：`rtk find` / `rtk grep` / `rtk ls` 做目录级聚合普查，再用 `Read`/`python3 -c` 对命中的关键
manifest、jsonl、sqlite 做精读；未逐文件列出（按目录聚合），大目录用 `du -sh` 只取体量。

**红线**：本报告不包含任何 N28 工艺的数值型 DRC/物理规则（间距、宽度下限、金属层电学参数等）；
出现的都是路径、文件名、器件自身的设计几何参数（outer_diameter_um 等，是优化器的设计变量，不是
工艺规则）、以及协议/字段名。工艺文件只以 profile 名 `n28_1p10m` 出现（EMX 工艺文件的真实文件名与路径已脱敏，不入仓库），
从未读取其数值内容。

---

## 0. 一句话结论

EM-opt-workflow 磁盘上有三类可直接复用的真实数据，覆盖度足以做 EM 侧的回放一致性验证；
`native_turbo_optimizer_evaluations.jsonl` 的逐点行字段（`parameters/run_id/status/fom/objective/
constraint_penalty/metrics`）与 `ic-opt-modular` 的 `optimizer_evaluations.jsonl`/`test_replay_parity.py`
期望的 schema **几乎逐字段相同**，只有目录嵌套深度不同（em-opt 是
`testbenches/<tb>/metrics/`，ic-opt-modular 期望 `testbenches/<tb>/corners/<corner>/metrics/`）。
测试对 EMX/Spectre/OCEAN 的伪造方式，是清一色的"依赖注入 + 按 `argv[0]` 分派的可调用对象/类"，
与 `ic-opt-modular` 现有的 `LocalExecutor.run(command, *, cwd, timeout_s, cshrc)` 形状高度同构，
可以在同一个 `FakeSpectreExecutor` 上加一个 `argv[0] == "emx"` 分支来统一收口，而不需要另起一套。

---

## 1. 真实录制 EM 数据清单

搜索范围：`EM-opt-workflow/{experiments,outputs,.scratch,logs}`、`$HOME/remote_opt`、
`~/.ic-opt`（不存在）、`$HOME/em-ic-opt`、`$HOME/*em*`。
`$HOME/remote_opt` 和 `$HOME/simulation` 是另一个项目（Mixer_CS 系列 Spectre/Maestro
调优，属于本机同一用户的 Cadence 工程，非 EM-opt-workflow 产物），只在任务 3（nport 语法）里用到。

### 1.1 总览表

| 路径（绝对） | 类型 | 点数 | GDS/sNp/metrics/manifest 是否齐全 | device family | 磁盘大小 |
|---|---|---|---|---|---|
| `experiments/m9_optimizer_inductor_smoke/ind_ct_turbo_smoke` | optimizer run（native TuRBO 闭环） | 20（`candidate_000001..20` + `real_001..020`） | 全部齐全（GDS+.s3p+全套 manifest+Spectre/OCEAN 派生 metrics） | `clean_port_ind_sym_ct`（N28 三端中心抽头对称电感） | 30M |
| `experiments/device_db_sweep_n28/outputs/sweep/<stratum>/<id>_<hash>/`（索引：`outputs/device_db.sqlite`） | sweep campaign（器件库扫参） | DB 中 12767 行（9722 ok / 3045 build_rejected）；磁盘保留原始 GDS+sNp+manifest 的子集 1.2G | 齐全（每点目录内 GDS+.s4p/.s3p+geometry_manifest.json+emx_manifest.json+emx_run_report.json+emx.log） | 6 个家族：`xfm_ms`(3145) `xfm_il`(2933) `ind_sym`(2029) `xfm_tw`(1946) `xfm_bs`(1717) `xfm_balun`(997) | sweep/ 1.2G；device_db.sqlite 451M；releases/ 2.4G（历史快照）；目录总计 5.6G |
| `.scratch/em-library-workflow-acceptance/{local-turbo,remote-only/"turbo real"}/runs/em_optimizer` 与同名 `workflow-2345-acceptance` 副本 | closed-loop（多器件+多 testbench 验收） | 10（`candidate_000001..10`，每个含 2 个 EM 设备 `xfmr_in`/`xfmr_out` + 2 个电路 testbench） | 齐全 | `clean_port_xfm_bs`（LO 变压器 `lo_xfmr`，4 端口） | 136M（em-library-workflow-acceptance）/ 46M（workflow-2345-acceptance，同一验收脚本 2026-09-09 跑了两份） |
| `experiments/m5_5_real_optimizer_smoke/{xfmr_turbo_smoke,xfmr_openbox_smoke}` | optimizer run（早期里程碑 smoke） | turbo=5，openbox=9~11 | 齐全但点数少 | `clean_port_xfm_bs` | 30M |
| `experiments/m6_3_real_multi_nport_smoke/{xfmr_two_nport_openbox_smoke,lo_xfmr_real_two_nport_openbox_smoke}` | closed-loop（单 testbench 内两个 nport 实例，验证 bind_nport 的多器件绑定） | 各 1 个 candidate + 1 个 real | 齐全，但只有 1 点，主要价值是"结构"而非"统计量" | `clean_port_xfm_bs` | 6.9M |
| `experiments/m6_6_real_multi_tb_smoke/lo_xfmr_two_tb_openbox_smoke*`（含 10 余个探针变体目录） | closed-loop（同一器件绑定进 2 个 testbench，验证 multi_testbench_aggregation） | 4（多个探针变体各自 1~4 点，探针目的是调试子进程/环境隔离问题而非造数据） | 齐全 | `clean_port_xfm_bs` | 84M（含十余个几乎重复的探针变体） |
| `experiments/pcell_inductor_python_port_clean/outputs/emx_real_validation` | fix-run 风格的纯验证（非闭环） | 5 个 `.s2p`，flat 目录 | 只有 sNp，无逐点 manifest/GDS（GDS 由同项目其他子目录另存） | `ind_sym`（电感 Python port 校验） | ~几百 K |
| `experiments/gdsfactory_xfmr_spike/{emx_smoke,random_10,m4_5_preview}` | 早期原型/测试夹具 | emx_smoke 3 个 `.s4p`；random_10 十点几何抽样（仅 GDS，无 EMX） | 部分（emx_smoke 有真实 EMX 输出但器件是 gdsfactory 手搓的 spike，不是生产 pcell） | 变压器原型 spike | 数十 K |
| `experiments/device_db_sweep_n28/outputs/closed_loop/{real_ind,real_xfm,batch2_ind,batch2_xfm,batch3_ind,batch3_xfm}` | closed-loop 提案批次（3 批 × {ind,xfm}） | 58 个点（`points.jsonl` 逐行，仅 `params`+`stratum`，与项目记忆"3批58/300真实EMX全成功"吻合） | **不齐全**：只有生成器参数提案和 `report.json` 摘要，没有保留每点的原始 GDS/sNp/manifest（结果已并入 `device_db.sqlite`） | ind_sym / xfm_* | 216K |
| `$HOME/remote_opt/Mixer_CS_*`、`$HOME/simulation/*` | 另一项目（Cadence Mixer 调优），非 EM-opt-workflow 产物 | 不适用 | 不适用（无 EMX，只有真实 Spectre/Maestro 记录） | Mixer_CS（含 xfmr/balun 的 s-parameter 模型引用） | 不适用 |

### 1.2 EMX 设置摘要（仅取自 manifest 的 `frequency`/`accuracy`/`mode`/process 文件名字段）

| 数据集 | mode | accuracy / 网格控制 | 频率 sweep | process 文件名 |
|---|---|---|---|---|
| `ind_ct_turbo_smoke` | `quasistatic` | `accuracy=standard`，`--3d=M9,M8`，`--via-separation=0.5` | 0–200 GHz，步长 1 GHz（自适应插值，只在约 20 个频点上真正求解） | 站点 EMX 工艺文件（文件名与路径不入仓库） |
| `em-library-workflow-acceptance`（xfmr_in/out） | `quasistatic` | `accuracy=standard`，`--3d=M10,AP` | 同上，0–200 GHz / 1 GHz | 同上 |
| `device_db_sweep_n28/outputs/sweep/xfm_bs_m10m9` | `quasistatic` | `accuracy` 字段为 `null`，改用显式网格控制：`--edge-width=0.5 --max-splits=3 --thickness=0.5`（比"standard"更细的网格，是该库的生产级 sweep 配方） | 同上 | 同上 |
| `test_emx_execution.py` 单测里出现的 `accuracy="high"`/`"highest"`、`mode="full_wave"` | 仅存在于**单元测试的构造参数**（`build_emx_argv` 契约测试），磁盘上没有找到对应的真实 full_wave 录制运行 | — | — | — |

### 1.3 用于回放一致性的首选 2–3 个数据集

1. **`<em-opt>/experiments/m9_optimizer_inductor_smoke/ind_ct_turbo_smoke`**
   —— 最佳首选。单器件单 testbench，20 点原生 TuRBO 闭环，`config/*.yaml` 是一份完整的项目契约
   （geometry/emx/nport_bindings/spectre/metrics/optimizer/testbenches/variables 八个文件，一一对应
   `pcell/emx/bind_nport/spectre/ocean+extract/optimizer/testbench 绑定/设计变量`八个概念），
   `reports/native_turbo_optimizer_evaluations.jsonl` 的逐行字段与 ic-opt-modular 的
   `optimizer_evaluations.jsonl` 契约近乎一致（见 §2.3）。体量小（30M），适合直接拷进
   `tests/replay/` 之类的目录或者用环境变量指向它。

2. **`<em-opt>/experiments/device_db_sweep_n28/outputs/sweep/`**
   （索引：`outputs/device_db.sqlite` 的 `samples`/`metrics` 两张表）
   —— 最佳"广度"数据源。12767 行里绝大多数（9722 ok）在磁盘上仍能找到对应的
   `<stratum>/<sample_id>_<params_hash>/{*.gds, *.s3p|*.s4p, geometry_manifest.json,
   emx_manifest.json, emx_run_report.json, emx.log}`，且 sqlite 里的 `params_json`+`generator_id`+
   `geom_version` 足以在没有原始 GDS 时"用生成器重新画出等价 GDS"。覆盖 6 个器件家族、两种金属
   栈（`_m10m9` / `_apm10` 等 stratum 后缀），是唯一能覆盖多器件家族、且有 `snp_sha256`/
   `gds_sha256` 可做字节级校验的数据源。缺点是体量大（sweep/ 1.2G，完整 outputs/ 目录 5.6G，
   含 2.4G 历史 releases 快照不建议拷贝）。

3. **`<em-opt>/.scratch/em-library-workflow-acceptance/local-turbo`**
   （或其 `remote-only/"turbo real"` 副本；`workflow-2345-acceptance` 下同结构的 46M 副本可作为
   第二来源核对）
   —— 最佳"链路完整度"数据源。每个 candidate 绑定 **两个** EM 设备（`xfmr_in.s4p`、
   `xfmr_out.s4p`）到**两个** Spectre testbench（`lo_xfmr_tb`、`lo_xfmr_tb_s21`），每个 testbench
   内各有一条 `NPORT` 语句，`multi_testbench_aggregation_report.json` 里能看到 corner 选择/汇总逻辑
   —— 这是 `ind_ct_turbo_smoke`（单设备单 TB）测试不到的 `bind_nport` 多实例场景。10 点、136M，
   体量适中。

以上三者互补：①=最小可跑通的金标准；②=多器件家族 + "从参数重建 GDS"的证据；
③=`bind_nport`/多 testbench 聚合的结构性覆盖。`m5_5_real_optimizer_smoke` /
`m6_3_real_multi_nport_smoke` / `m6_6_real_multi_tb_smoke` 点数太少（1–11 点）且多为同一调试目的的
重复探针，仅作为①③的历史印证，不建议作为主数据源。

---

## 2. 目录树与 manifest 字段样本

### 2.1 EM optimizer run 示例：`ind_ct_turbo_smoke`

```
experiments/m9_optimizer_inductor_smoke/ind_ct_turbo_smoke/
├── config/                     # 项目契约（geometry/emx/nport_bindings/spectre/metrics/optimizer/...）
├── reports/
│   ├── native_turbo_optimizer_evaluations.jsonl   # 逐点录制行（见 §2.3）
│   ├── native_turbo_optimizer_report.json
│   └── optimizer_final_summary.json
├── runs/
│   ├── em_optimizer/
│   │   └── candidate_000001/
│   │       ├── em/
│   │       │   ├── candidate_000001.gds
│   │       │   ├── candidate.s3p                  # EMX 真实输出（3 端口）
│   │       │   ├── emx_manifest.json               # argv + config（见下）
│   │       │   ├── emx_run_report.json             # returncode/stderr(含CPU/Wall/Peak mem)/snp_validation
│   │       │   ├── em_artifact_manifest.json
│   │       │   ├── geometry_manifest.json          # generator_id + geometry.config
│   │       │   └── emx_ports.txt
│   │       └── circuit/
│   │           ├── m3_circuit_manifest.json        # candidate_parameters + cache.fingerprint + emx/geometry/touchstone 三个子块的校验
│   │           └── ind_ct_tb/
│   │               ├── nport_patch_manifest.json   # 把 candidate.s3p 补丁进 input.scs 的 NPORT0 实例
│   │               └── circuit/input.scs           # 见 §3 verbatim
│   └── real/
│       └── real_001/
│           ├── candidate.json
│           ├── em_circuit_evaluation_manifest.json # 串联 m2(em)+m3(circuit) 两份 manifest 的指针
│           ├── em_circuit_observation.json         # {"metrics": {"Lp_10g":..,"Qp_10g":..}, "objective":.., "constraints_passed":true}
│           ├── metric_extraction_request.json      # OCEAN 表达式 + expression_sha256 + waveform_exports
│           ├── multi_testbench_aggregation_report.json
│           ├── result_manifest.json                # command_trace 里有 spectre/ocean 完整 argv
│           └── testbenches/ind_ct_tb/
│               ├── metrics/{metric_probe.ocn, metric_result_manifest.json, ocean_scalars.tsv, ocean.log}
│               ├── netlist/{input.scs, models/candidate.s3p, ...Cadence 中间文件}
│               └── psf/spectre.out
└── state/optimizer_state.json
```

`geometry_manifest.json`（键 + 截断示例值）：
```json
{
  "generator_id": "clean_port_ind_sym_ct",
  "geometry": {"config": {
    "outer_diameter_um": 102.0, "width_um": 5.2, "turns": 2, "spacing_um": 2.0,
    "opening_um": 8.0, "lead_length_um": 20.0, "top_metal": "9", "bottom_metal": "8",
    "port_order": ["p01","p02","p03"], "process_profile": "n28_1p10m",
    "ground_fixture": {"ring_width_um": 50.0, "stub_width_um": 5.0, "...": "..."}
  }},
  "suggested_emx_ports": ["-p p01=p01:G01", "..."],
  "via_landing_audit": {"status": "pass", "vias_checked": 1}
}
```

`emx_manifest.json`（`config` 子对象的键集合）：
`accuracy, binary, edge_width_um, extra_args[], frequencies_hz[], gds_file, include_command_line,
log_file, max_splits, mode, modes[], parallel, ports[{name,reference,signal}], process_file,
s_file, s_impedance, simultaneous_frequencies, sweep{enabled,start_hz,step_hz,stop_hz,num_steps},
thickness_um, three_d_metals[], top_cell, verbose, via_inductance[], via_separation_um,
via_sidewalls[]`；顶层还有 `argv[]`（拼好的 emx 命令行）、`mode`、`schema_version`。

`emx_run_report.json` 顶层键：
`argv[], candidate_id, emx_version, issues[], log_file, resource_usage(可为空), returncode, s_file,
schema_version, snp_validation{status,ports,issues}, status, stderr(文本日志,含 CPU/Wall/Peak mem 行),
stdout, warnings[]`。

`em_circuit_observation.json`（真实值，非工艺数据，是电感 Q/L 的仿真结果）：
```json
{"candidate_id": "candidate_000001", "constraints_passed": true, "issues": [],
 "metrics": {"Lp_10g": 5.493876e-10, "Qp_10g": 6.561484}, "objective": 6.561484, "status": "succeeded"}
```

### 2.2 Campaign point 示例：`device_db_sweep_n28` 一个 sweep 点

```
experiments/device_db_sweep_n28/outputs/sweep/xfm_bs_m10m9/00001_23c7e32c/
├── xfm_bs_m10m9_00001.gds
├── xfm_bs_m10m9_00001.s4p
├── emx_manifest.json
├── emx_run_report.json          # 多了一个 "resource_usage" 字段（见 §5.3），标准 run 报告没有
├── emx.log
├── emx_ports.txt
├── geometry_manifest.json       # generator_id = clean_port_xfm_bs
└── resume_gate/                 # 断点续跑用的快照子集（同名的 emx_ports.txt/geometry_manifest.json + 一份 gds）
```

`geometry_manifest.json` 的 `geometry.config` 键集合与 §2.1 同构，但值属于变压器家族：
`center_spacing_um, ct_primary_metal, ct_secondary_metal, drc_check, ground_fixture{...},
port_order:[P1,N1,P2,N2], primary_lead_length_um, primary_metal, primary_opening_um,
primary_outer_diameter_um, primary_width_um, process_profile, secondary_*`。

`outputs/device_db.sqlite` 的表结构（列名，无数据）——这是"回放不需要磁盘原始文件、只要参数就能重建"的索引：

```
samples(id, family, stratum, params_json, params_hash, snp_path, snp_sha256, gds_sha256,
        process_profile, fixture_json, emx_settings_hash, emx_port_names, status,
        failure_reason, source, created_at, geom_version, family_schema_revision)
metrics(sample_id, topology_id, name, freq_hz, value, derived_version)
strata(name, family_id, process_profile, family_schema_revision, generator_id,
       signal_ports_json, topology_name, active)
family_contracts(family_id, generator_id, device_kind, schema_revision, dims_json,
                  int_dims_json, nt_dim, signal_ports_json, topology_name,
                  metric_names_json, ct_policy, active)
```

### 2.3 与 `ic-opt-modular` 现有回放测试的字段对照（关键发现）

`ic-opt-modular/tests/ic_opt/test_replay_parity.py` 已经证明了 Spectre 一侧的回放方法：读
`reports/optimizer_evaluations.jsonl` 逐行 + `runs/real/<run_id>/testbenches/<tb>/corners/<corner>/
metrics/metric_result_manifest.json`，用 `FakeSpectreExecutor` 把录制的 OCEAN 标量原样喂回去，
其余渲染/抽取/角落聚合/目标函数全部走真实引擎，和录制的 `fom/objective/constraint_penalty/metrics`
做数值比对（相对误差 1e-9 量级）。

`ind_ct_turbo_smoke/reports/native_turbo_optimizer_evaluations.jsonl` 的第一行（真实值）：

```json
{"batch_id": "batch_001", "constraint_penalty": 0.0, "evaluation_index": 1, "fom": 6.561484,
 "metric_result_manifest": "runs/real/real_001/metrics/metric_result_manifest.json",
 "metrics": {"Lp_10g": 5.493876e-10, "Qp_10g": 6.561484}, "objective": -6.561484,
 "parameters": {"outer_diameter_um": "102", "width_um": "5.2"},
 "raw_x": [101.585.., 5.192..], "result_manifest": "runs/real/real_001/result_manifest.json",
 "run_id": "real_001", "selection_phase": "initialization", "status": "feasible", "...": "..."}
```

这与 `test_replay_parity.py` 期望的行字段（`parameters, run_id, status, fom, objective,
constraint_penalty, metrics`）**逐字段吻合**，文件名不同（`native_turbo_optimizer_evaluations.jsonl`
而非 `optimizer_evaluations.jsonl`——em-opt 侧按算法起名，取数逻辑要兼容两种文件名）。

唯一的结构性差异：`load_children()` 期望
`testbenches/<tb>/corners/<corner>/metrics/metric_result_manifest.json`；
em-opt 这个项目（单 corner，无 corner 概念）是更浅的
`testbenches/<tb>/metrics/metric_result_manifest.json`（已用 `Read` 核实该文件真实存在，
`metrics` 键是 `[{name,status,value,expression_sha256,...}]` 列表，与 `load_children` 里
`m["name"]`/`m["status"]`/`m["value"]` 的取法完全兼容）。给 EM 侧写等价回放器时，加载路径要么
判断 `corners/` 是否存在再回退到扁平路径，要么统一先探测 `nominal` 是否是隐式默认 corner。

---

## 3. 含 nport 实例的 Spectre testbench（verbatim）

### 3.1 单 nport（`ind_ct_turbo_smoke`，3 端口电感）

路径：`experiments/m9_optimizer_inductor_smoke/ind_ct_turbo_smoke/runs/em_optimizer/candidate_000001/circuit/ind_ct_tb/circuit/input.scs`

```
NPORT0 ( net4 0 net3 0 0 0) nport \
        file="models/candidate.s3p"
```

### 3.2 单 testbench 内两个 nport 实例（`m6_3_real_multi_nport_smoke`，验证 `bind_nport` 多设备绑定）

路径：`experiments/m6_3_real_multi_nport_smoke/xfmr_two_nport_openbox_smoke/runs/em_optimizer/candidate_000001/circuit/xfmr_tb/circuit/input.scs`

```
NPORT0 ( P1 0 P2 0 S1 0 S2 0) nport \
        file="models/xfmr_in.s4p"
NPORT1 ( P1 0 P2 0 S1 0 S2 0) nport \
        file="models/xfmr_out.s4p"
```

（同一组端口节点，两个并联的 4 端口 nport，分别代表同一个物理变压器的两次不同 EM 抽取/两个视角——
这是唯一在磁盘上找到的"一个 testbench 里塞两个 NPORT"的真实例子，`bind_nport` 阶段的多实例路径
必须能处理这种情况。）

### 3.3 真实 Cadence 工程里的 nport（`$HOME/simulation`，另一项目，18 端子/10 端口，含 `interp=` 选项）

路径：`$HOME/simulation/Virtuoso_Bridge_test/MixerCS_PSS_IIP3/maestro/results/maestro/Interactive.27/psf/Mixer_CS_IIP3/netlist/input.scs`

```
subckt CS_Mixer_XFMR GND P1 P2 P_Com S1 S2 S_Com
NPORT0 ( P1 GND P2 GND P_Com GND P_Com GND S1 GND S2 GND S_Com GND S_Com \
        GND GND GND GND GND) nport \
        file="/home/host/project/EMX_work/112G_Double_Balanced_Mixer_XFMR_CS_Switch_5.work/XFMR_CS_Switch_5.s10p" \
        interp=bbspice
```

这一条是 EM-opt-workflow 自己生成的 netlist里从未出现的变体：显式 `interp=bbspice` 插值选项、
文件路径来自另一台主机的 EMX 工作目录（`/home/host/project/...`，纯路径，非工艺数据）、以及
18 端子（9 差分/单端信号对）远超 em-opt 目前 3–4 端口的规模。`bind_nport` 若要对齐真实 Cadence
用法，应把 `interp=` 作为可选透传字段而不是硬编码省略。

---

## 4. 测试如何伪造 EMX / pcell(generator) / Spectre / OCEAN

### 4.1 EMX 阶段的伪造点：`EmxRunner`

真实类型定义（`src/em_ic_opt_workflow/emx_execution.py:38`）：
```python
EmxRunner = Callable[[list[str]], EmxProcessResult]
# EmxProcessResult = @dataclass(frozen=True): returncode:int, stdout:str, stderr:str,
#                    resource_usage: dict[str,object] | None = None
```
`run_prepared_em_candidate(prepared, runner=fake_runner)`（`src/em_ic_opt_workflow/emx_execution.py`）
接受这个可调用对象；测试里最小的 fake 就是一个闭包，负责断言 argv、手写一个最小合法 Touchstone
文件、返回一个 `EmxProcessResult`——不启动任何子进程：

```python
# tests/test_emx_execution.py
def fake_runner(argv: list[str]) -> EmxProcessResult:
    assert argv == prepared.emx_argv
    _write_valid_s4p(prepared.emx_config.s_file)   # 手写 "# Hz S RI R 50" + 一行数据
    return EmxProcessResult(returncode=0, stdout="fake ok", stderr="")
```
同一文件里还有失败路径的变体：`returncode=2, stderr="license failed"`、
`returncode=0` 但不写 snp 文件（触发"touchstone file is missing"）。

上一层（`em_ic_opt_workflow.em_optimizer_evaluator`）把它继续网上传递：
`run_em_native_turbo_optimization(..., em_runner: EmxRunner | None = None,
circuit_runner: CommandRunner | None = None)`；默认会用 `_cshrc_emx_runner(cadence_cshrc)`
包一层真实 `subprocess.run`。`tests/test_em_optimizer_evaluator.py` 里有直接
`monkeypatch.setattr(subprocess, "run", fake_subprocess_run)` 的用法，用来测"默认 runner 确实把
cshrc 包进去了"这件事本身，而不是测业务逻辑。

### 4.2 Spectre + OCEAN 阶段的伪造点：`CommandRunner` Protocol

真实定义（`src/em_ic_opt_workflow/execution_adapters/spectre_ocean.py:116`）：
```python
class CommandRunner(Protocol):
    def run(self, argv: list[str], *, cwd: Path, stdout_path: Path,
            stderr_path: Path, timeout_s: int) -> CommandResult: ...
class SubprocessCommandRunner:            # 真实实现，subprocess.run 落盘 stdout/stderr
class CshrcCommandRunner:                 # 包一层 csh -fc 'source <cshrc>; ...'
```
测试里到处复用同一模式，按 `argv[0]` 分派：`FakeSuccessRunner`（写 `psf/spectre.out` 和
`ocean_scalars.tsv`，标量值从 `metric_extraction_request.json` 里声明的 metric 名字动态生成
`"{index}.25"`，并把 `expression_sha256` 原样抄回去以满足下游的表达式溯源校验）以及一组故障注入
子类：`FakeSpectreFailureRunner`（returncode=2）、`FakeOceanFailureRunner`（returncode=3）、
`FakeTransientOceanFailureRunner`（第一次 license 失败=35，第二次成功，测重试）、
`FakeTransientSpectreSocketFailureRunner`、`FakeOceanFailureWithoutArtifactsRunner`、
`FakeOceanMalformedScalarRunner`。这些类在
`tests/test_spectre_ocean_adapter.py`（主定义，约 1900 行测试文件里的一段）、
`tests/test_em_circuit_evaluator.py`（EM+电路联合评估器用的独立副本，字段一致）、
`tests/test_remote_spectre_ocean.py`（远程执行版本的 `FakeRunner`）里各自维护一份，**没有抽到
共享模块**——这是 ic-opt-modular 设计新 fake 时可以顺手改善的一点（合并成一个
`tests/fakes/spectre_ocean.py`）。

### 4.3 pcell / generator：基本不伪造，直接用真实插件

`geometry.yaml` 里 `plugin_module` 指向的生成器（如
`src/em_ic_opt_workflow/devices/clean_port/generator_plugin.py`）是纯 Python、无外部依赖、
运行时间是毫秒级，测试**直接调用真实生成器**：
```python
REAL_PLUGIN = ROOT / "src/em_ic_opt_workflow/devices/clean_port/generator_plugin.py"
gen = get_generator("clean_port_ind_sym", plugin_module=REAL_PLUGIN)
```
唯一出现"伪造"的地方是 `tests/test_geometry_registry_plugin.py` 里为了测**插件加载机制本身**
（而非物理生成）而现写的、故意有缺陷的临时插件模块，比如：
```python
bad.write_text("PLUGIN_GENERATORS = {'x': object()}\n")   # 触发 "PassiveDeviceGenerator" 报错
```
以及 `tests/test_em_candidate_preparation.py` 里为了测"旧插件不声明 `drc_check` 字段时的兼容
分支"而现写的 `legacy_plugin.py`（`generate()` 里直接 `raise NotImplementedError`，因为该测试根本
不会走到几何生成那一步）。**结论：pcell 阶段不需要专门的"物理 fake"，因为真实实现已经够便宜；
需要的只是"插件契约违反"式的负例 fake。**

### 4.4 pytest 之外的解析式 EMX 替身：`mock_snp.py`

`experiments/device_db_sweep_n28/mock_snp.py`（`sweep_driver.py --mock-emx` 使用）用耦合电感的
电路代数模型（`_s_from_branches`：给定分支阻抗矩阵解出 n 端口 S 参数）直接合成 Touchstone 文件，
不调用 EMX、不需要 license，注释里明确说明"不是对真实 EMX 结果的标定，只用于 driver 排练和
CV-gate 冒烟"，且与 `tests/device_db/test_measure.py::_s_from_branches` 共享同一段网络代数
（避免两边算法漂移）。这是**除 pytest fake 之外**、专门为"离线跑通调度/落库逻辑"设计的解析替身，
如果 ic-opt-modular 想要一个比"写死 canned 值"更真实、又不需要真实 EMX 的 EM-only 测量路径，
这段代码值得直接借用或移植。

### 4.5 可直接复用的最小真实 sNp/GDS fixture

`experiments/gdsfactory_xfmr_spike/emx_smoke/` 下的文件不是伪造的，是**真实 EMX 2024.1.0**
在这台机器上跑出来的输出，被 `tests/test_emx_execution.py` 用仓库相对路径直接引用（不复制进
`tests/fixtures/`），用于校验 `validate_touchstone_output`：

| 文件 | 大小 | 用途 |
|---|---|---|
| `experiments/gdsfactory_xfmr_spike/emx_smoke/local_gnd/xfmr_spike_local_gnd.s4p` | 1346 B | `test_validate_touchstone_output_accepts_verified_emx_s4p` 的正例；同一文件复制改后缀后还用作 `test_validate_touchstone_output_rejects_extension_port_count_mismatch` 的反例 |
| `experiments/gdsfactory_xfmr_spike/emx_smoke/xfmr_spike.s4p` | 1026 B | 单频点验证 |
| `experiments/gdsfactory_xfmr_spike/emx_smoke/xfmr_spike_1g_10g_3pt.s4p` | 1973 B | 3 频点（1/5.5/10 GHz）sweep 验证 |
| 同目录下的 `*.gds`（如 `out_local_gnd/xfmr_spike_local_gnd.gds`） | 752–1074 B | 对应的最小 2 端口变压器 spike 版图 |

这批文件体量最小、内容真实（文件头里能看到 `EMX version 2024.1.0` 和运行主机名），是"canned sNp
fixture"的最佳来源——比 `mock_snp.py` 现造的更有说服力，比 `device_db_sweep_n28` 里几十 KB 的
生产级 `.s4p`（201 频点）更轻。

`tests/` 目录本身不存放任何 `.gds`/`.s*p` 二进制 fixture（已用 `find` 确认为空）：所有单测都是
`_write_valid_s4p()` 这种内联现写的最小 Touchstone 文本，或者像上面这样引用 `experiments/` 下的
真实文件。

### 4.6 共享测试辅助模块（非按文件重复定义的部分）

`tests/project_factory.py`（`create_generic_project` / `create_approved_generic_project`：搭一个
带完整 `config/*.yaml` 的假项目骨架）、`tests/report_helpers.py`（`write_pass_reports`：一次性把
一堆"全绿"报告文件铺到项目目录，跳过真正跑 dry-run 的中间步骤）、
`tests/real_run_smoke_helpers.py` / `tests/real_run_cluster_helpers.py`（`write_fake_metric_result_manifest`
等：手写 `metric_result_manifest.json` 使下游校验通过）、`tests/netlist_dry_run_helpers.py`。
这些是"造项目"和"造报告"级别的 fixture 工厂，和 §4.1/4.2 的"造 subprocess 结果"是两个正交的
伪造维度，ic-opt-modular 若要复刻，也需要两个维度都覆盖（造 `Spec`/项目骨架 + 造 executor 结果）。

---

## 5. 时间与内存证据

### 5.1 单次 EMX 运行的直接样本（均为 `quasistatic` + `accuracy=standard`，N28）

| 数据点 | 端口数 | `--3d` 金属层 | `--parallel` | `--max-memory` 声明 | CPU time | Wall-clock | EMX 自报 Peak memory |
|---|---|---|---|---|---|---|---|
| `ind_ct_turbo_smoke` candidate_000001（电感） | 3 | M9,M8 | 4 | 64G | 8.35 sec | **2.52 sec** | 193.65 MB |
| `em-library-workflow-acceptance` candidate_000001/xfmr_in（变压器） | 4 | M10,AP | 4 | 32G | 7.68 sec | **2.30 sec** | 253.05 MB |

两者都是"0–200GHz、1GHz 步长"的宽带 sweep，但 EMX 的 quasistatic 自适应插值只在约 20 个频点上
真正求解（日志里能看到 `Simulating at frequency ...` 只出现在离散的十几个频率上），所以宽带 sweep
并不显著拉长单次运行时间——**这解释了为什么"标准精度小尺寸无源器件"的单次 EMX 调用只要 2–3 秒**。

### 5.2 批量聚合证据

`experiments/device_db_sweep_n28/outputs/SWEEP_REPORT.md`：
> "...jobs（`--parallel=4 --max-memory=64G --simultaneous-frequencies=0`，默认网格），
> cgroup MemoryMax=280G per sub-batch scope. Wall time ≈ **20 min total**（~2 s/point，
> 远低于 3–5 h 的预估）..."

与 §5.1 的单点秒级耗时量级一致，可作为"每点约 2 秒"这个经验值的独立佐证。

### 5.3 `device_db_sweep_n28` 的 `resource_usage` 字段——另一种口径，注意不要混用

`EmxProcessResult` 有一个可选字段 `resource_usage: dict | None`；普通项目（如 `ind_ct_turbo_smoke`）
跑出来的 `emx_run_report.json` **没有**这个键，而 `device_db_sweep_n28` 的 sweep driver（带资源守护，
见 `tests/device_db/test_sweep_experiment.py` 里的 `emx_memory_warn_gib` / `emx_memory_pause_gib` /
`emx_memory_limit_gib` / `emx_terminate_grace_s` 等旋钮和 `driver._current_cgroup_memory_limit_gib`）
会把 cgroup 轮询到的峰值内存写进去：

| stratum（家族） | `job_peak_gib`（cgroup 口径） |
|---|---|
| `ind_sym_m10` | 0.42 |
| `xfm_bs_m10m9` | 0.31 |
| `xfm_bs_apm10` | 0.30 |
| `xfm_balun_m10` | 0.33 |
| `xfm_ms_m10m9` | 0.85 |
| `xfm_il_m10` | 1.23 |
| `xfm_tw_m10` | 1.93 |
| `ind_sym_ap` | 1.89 |

这组数字比 §5.1 EMX 自报的 ~200MB 高一个数量级——两者**测量层不同**（EMX 自己在 stderr 里报的是
它自己感知到的峰值；`resource_usage` 是外部 cgroup 轮询到的进程组峰值，包含 Python 驱动进程、
文件 I/O 缓存等开销，且这批 sweep 用的是更细的网格配方 `--edge-width/--max-splits/--thickness`
而非 `accuracy=standard`），**给新 stage 定 `resources` 声明时应该以 `resource_usage`
（cgroup/进程组口径）为准，而不是 EMX 自报的 stderr 数字**，因为前者才是操作系统真正会记账、
真正可能触发 OOM 的口径。

### 5.4 站点包络配置的真实痕迹

- `ind_ct_turbo_smoke/config/emx.yaml`：`max_parallel_jobs: 2`，`max_cpu_per_job: 4`；
  `config/spectre.yaml`：`parallel_jobs: 2`，`threads_per_run: 8`，`timeout_s: 7200`。
- `.scratch/bs-emx-validation-40ghz-2026-09-10/resource-{before,after}.json`：真实 cgroup 快照，
  `memory.max = 137438953472`（128 GiB）、`pids.max = 64`。
- 项目记忆（2026-09-21）记录的现行硬上限：**总线程 ≤128、总内存 ≤256G**，与
  `ic-opt-modular/docs/refactor/DESIGN_CN.md` 第 206 行"引擎在站点上限（如 128 线程 / 256 GB）
  内算出并发槽"的设计前提完全对得上——本报告的数据可以直接作为该设计前提的实测依据。
  同一份设计文档里 `emx 声明 threads=4, memory_gb=32` 的占位符，也与 §5.1 实测的
  `parallel=4` / `--max-memory=32G~64G` 量级吻合。

---

## 6. 对 ic-opt-modular 的落地建议（简要）

1. EM 回放测试直接仿照 `tests/ic_opt/test_replay_parity.py` 的结构，新增一个读取
   `native_turbo_optimizer_evaluations.jsonl`（文件名需要兼容，不只认 `optimizer_evaluations.jsonl`）
   + `runs/em_optimizer/<candidate>/em/{geometry_manifest.json,emx_manifest.json,candidate.sNp}`
   的加载器，先用 `ind_ct_turbo_smoke`（20 点，单设备单 TB）跑通,再上
   `em-library-workflow-acceptance`（10 点，双设备双 TB，压 `bind_nport`/聚合路径）。
2. `tests/ic_opt/fakes.py::FakeSpectreExecutor` 可以直接加一个 `argv[0] == "emx"` 分支
   （仿照 em-opt `FakeSuccessRunner` 对 `ocean_scalars.tsv` 的手法，从录制的
   `emx_manifest.json`/`candidate.sNp` 里取出 `--s-file=` 目标路径，把录制的 sNp 字节原样写过去），
   不必新起一个 Executor 类。
3. 最小 canned sNp/GDS fixture 直接从
   `experiments/gdsfactory_xfmr_spike/emx_smoke/{xfmr_spike.s4p,xfmr_spike_1g_10g_3pt.s4p,
   local_gnd/xfmr_spike_local_gnd.s4p}`（1–2KB，真实 EMX 输出）复制，不必现编。
4. `resources` 声明的内存基线用 §5.3 的 `job_peak_gib`（cgroup 口径，0.3–1.9 GiB/单任务）而非
   EMX 自报的 ~200MB；并发槽计算对齐 §5.4 的 128 线程 / 256GB 站点上限。
5. `device_db_sweep_n28/outputs/device_db.sqlite` 的 `samples` 表（`params_json`+`generator_id`+
   `geom_version`+`snp_sha256`+`gds_sha256`）是一个现成的、可编程查询的回放候选池，比在
   1.2G 的 `outputs/sweep/` 目录里手工挑点更适合做批量抽样验证。
