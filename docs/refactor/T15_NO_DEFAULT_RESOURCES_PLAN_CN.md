# T15：资源由用户指定、去掉开发环境假设

状态：第 7 节已拍板（2026-09-24）：D1 EMX 超时也由用户指定（必填）；D2 是；D3 是；D4 是；D5 是。subagent 思考强度改为 extra。T15.1–T15.6 与 TuRBO (b) 已合入 main（见第 8 节），T15.7 文档清理进行中；T15.8 已完成的验收记在第 8 节。

## 0. 一句话

并发任务数、每任务线程数、EMX 的线程与内存上限、每台机器的总上限，全部由用户明确给出；没有内置初始值，缺任何一项
就拒绝启动。同时把审查里的其它机器假设（身份标识含资源、`.proc` 路径、控制端只支持 Linux、依赖未声明）和两处
工艺假设（底层金属只认 "M1"、"低频"固定 3 GHz）一并去掉。

## 1. 已拍板的原则（用户，2026-09-24）

- 典型拓扑：**仿真服务器是 Linux；远程控制端一般是 Windows 或 macOS**（也可能是 Linux）。
- **并行任务数、每任务线程数、EMX 限制等，由用户指定，不应有初始值；不指定就不能启动。**
- 一切改进面向普遍需求，测试案例只用来验证（`no-dev-environment-assumptions`）。

## 2. 需求与验收标准

功能（F）
- F1 `~/.ic-opt/site.yaml` 改为**按主机**：每台机器一项（`local` = 运行 ic-opt 的这台；其余以 `--ssh-profile` 的名字为键），
  `max_threads`、`max_memory_gb` **必填**；可选 `cshrc`、`scratch_root`、`license_probe`、`transfer_timeout_s`。
  代码里不再有 128 / 128 / 6 / 16 / 半核数这类数字。
- F2 `spec.yaml` 的资源字段**必填**：`simulator.parallel_jobs`、`simulator.threads_per_run`、`em.threads`、`em.memory_gb`
  （`ic-opt migrate` 仍把 0.1 的值显式写出，迁移后的文件自然合规）。
- F3 缺任何一项 → `load_run` / `env.doctor` / `--plan` 都以明确信息拒绝（缺哪台主机、缺哪个字段、示例怎么写）；
  `engine.run` 拒绝任何单阶段线程或内存超出执行机上限的任务（去掉并发下限 1）；`site` 不再可为 `None`。
- F4 库的计算（拟合进程数、每进程 BLAS 线程、预测分块字节数）从 `hosts.local` 推导；显式 `OMP_NUM_THREADS` 只会
  往下压、从不被抬高；`hosts.local` 缺失时需要拟合的库功能同样拒绝。
- F5 身份标识只含问题本身：`Spec.fingerprint` 剔除资源、超时、保留策略、license、budget；EMX 物理键用 `.proc` 内容哈希
  而非路径；`lib_signoff` 可指定 `process_file=`；现有工程与 N28 库经一次性迁移后**代际不变、观测继续复用**。
- F6 控制端可在 Windows / macOS 运行远程模式：不在模块顶层导入 `fcntl`；结果库的锁可移植；并行拟合用 `spawn` 安全的
  工作进程；文档写明"仿真主机 Linux；控制端 Windows / macOS / Linux；本地执行器仅 Linux / macOS"。
- F7 依赖在 `pyproject.toml` 声明（`library` extra：scikit-learn、threadpoolctl；openbox 与 TuRBO 的打包见第 7 节）。
- F8 pcell：地环夹具的 DRC 豁免用 profile 的 `fixture_conductor`，不用字面量 "M1"；"低频"上限成为可配置量并相对于器件
  （默认 `min(3 GHz, SRF/10)`），扫频内没有低频样本时只让 `*_lf` 列为空，不让整个点失败。
- F9 文档与示例：README / SKILL / `docs/em/*` 不再把 128、40 GHz、150–170 pH、N28 写成常态；EM 示例基于 demo_6m；
  耗时说明注明"参考主机"；包内不含个人路径。

正确性与验收（C）
- C1 单元测试：缺 site.yaml、缺主机项、缺字段、超上限四种情况各有拒绝测试；`slots` 无下限；`workers_for` 无 `None` 分支。
- C2 现有 N28 库在迁移后仍是一个代际（`lib.load` 的 generations 与迁移前相同），G1/G2 门通过；一个既有工程提高 budget 续跑
  时旧观测复用（指纹迁移）。
- C3 在一个干净的虚拟环境里 `pip install -e ".[em,library]"` 后，`ic-opt doctor` 与库测试能跑（依赖声明完整）。
- C4 并行拟合在 `spawn` 上下文下与 `fork` 结果一致（测试强制 `spawn`）；`store.lock` 在无 `fcntl` 的平台（用打桩模拟）可用。
- C5 `demo_6m` 把 M1 改名为任意名字后 `em.validate_profile` 仍通过；一个只扫 50–120 GHz 的合成器件不再 `failed:measure`。
- C6 库、pcell、CLI、docs 测试与 ruff 全绿；黄金 GDS 不变（本任务不动几何）。

## 3. 设计

### 3.1 `site.yaml` v2

```yaml
# ~/.ic-opt/site.yaml —— 每台机器一项；数字由你按机器填写，工具没有默认值
hosts:
  local:                    # 运行 ic-opt 的这台机器（不带 --ssh-profile 时它也是仿真机）
    max_threads: 16         # 必填：这台机器允许 ic-opt 同时占用的线程总数
    max_memory_gb: 32       # 必填：允许占用的内存总量
  lab:                      # 与 --ssh-profile lab 同名
    max_threads: 128
    max_memory_gb: 256
    cshrc: /home/me/cadence_env.csh      # 可选：仿真机上的 Cadence 环境
    scratch_root: /scratch/me/ic-opt     # 可选：默认 ~/.ic-opt/scratch
    license_probe: lmstat -a             # 可选：doctor 用的 license 探测命令
    transfer_timeout_s: 1800             # 可选：scp 超时
```

- `site.load()` → `Site(hosts)`；`site.host(name)` 返回 `HostLimits`，缺项抛 `SiteError`，信息含示例。旧的扁平写法
  （顶层 `max_threads`）视为 `hosts.local`，并提示改成新格式。
- `HostLimits` 的字段没有默认值（dataclass 必填）。
- 执行机 = `executor.host`（`local` 或 profile 名）；控制端 = `local`，库的计算用它。

### 3.2 spec 与启动检查

- `Simulator.threads_per_run`、`EmSettings.threads`、`EmSettings.memory_gb` 去掉默认值（pydantic 必填）；`parallel_jobs` 已必填。
- `load_run`：`site.host(executor.host)` 必须存在；`Run.jobs` 用它；`engine.run` 在开跑前对流水线每个阶段检查
  `threads ≤ max_threads and memory_gb ≤ max_memory_gb`，否则 `EnvelopeError`（`--plan` 也报）；`Site.slots` 去掉 `max(1, …)`。
- `blocks.evaluate` / `blocks.optimize` 的 `site` 参数必填；README 与 SKILL 的配方骨架同步。
- `env.doctor` 打印执行机上限与本次占用（"5 jobs × 8 threads / 16 GB → 40 threads / 80 GB of 128 / 256"）。

### 3.3 库的计算量

- `Library.models(workers=None, threads=None)`：`limits = site.host("local")`；`workers = min(缺的模型数, limits.max_threads // 2,
  limits.max_memory_gb // est_gb)`，`est_gb` 按训练行数估算（n²·(dims+2)·8 B 的 3 倍，向上取整到 0.1 GB）；每进程线程
  `max(1, min(limits.max_threads // workers, OMP_NUM_THREADS if set))`；显式 `workers`/`threads` 参数只能小于等于上限。
- `suggest.PREDICT_CHUNK` 改为按字节：`chunk_rows = budget_bytes // (8 × n_train × 7)`，`budget_bytes` 默认
  `limits.max_memory_gb × 10%`（`predict_all(chunk_bytes=…)` 可覆盖）。
- `region.DEFAULT_THREADS` 删除；`region(threads=…)` 缺省走 3.3 的规则。
- `lib_design` 的 `Predict` 首次使用前预拟合（`Library.models`），并加 `rel_sigma_max` 参数（审查行 21）。

### 3.4 身份标识

- `Spec.problem()`：`model_dump` 后剔除 `simulator.{parallel_jobs, threads_per_run, timeout_s, license_check, keep_failed_runs,
  keep_successful_runs}`、`em.{threads, memory_gb, timeout_s, verbose}`、`budget`；`fingerprint()` 基于它。
- 兼容：观测里保存的 `spec_fingerprint` 若等于**当前 spec 按旧公式**算出的指纹，也视为同一问题（一次性接受旧值），并在
  `ic-opt migrate-store PROJECT`（新命令）里改写为新指纹。
- `emx.physics_key` 用 `proc_sha256`（内容）替换 `process_file`（路径）；`Emx.identity` 在首次拿到 proc 哈希后确定；
  库的代际因此只随物理设置与工艺文件**内容**变化。`ic-opt migrate-store` 同时把观测的 `pipeline_fingerprint` 按新公式重写
  （需要当前 spec 与 proc 哈希；N28 库四个部件 + 两个电感部件各跑一次）。`lib_signoff` 加 `process_file=`。

### 3.5 控制端可移植

- `store.lock`：`fcntl` 只在可用时导入；Windows 用 `msvcrt.locking`；抽成 `ic_opt/_lock.py`。
- `Library.models`：`multiprocessing.get_context("spawn")`（Windows/macOS 默认；Linux 也用 spawn 以保证一致）；工作函数
  已是模块级、只传路径与名字，满足 spawn 要求。
- 检查并去掉其它 POSIX-only 调用（`os.fork`、`csh` 只用于执行机）；`LocalExecutor` 明确只支持 Linux/macOS。
- README / ADR-0001 增加"平台"一节。

### 3.6 依赖与打包

- `pyproject.toml`：`dependencies` 加 `scikit-learn`, `threadpoolctl`；`[project.optional-dependencies].turbo` 与 openbox 的处理
  见第 7 节 D3。

### 3.7 pcell 与测量

- `em_chain` 与 `profile_validation` 的豁免改为 `("max_width", profile.fixture_conductor)`，共用一个函数。
- `measure.Topology.low_freq_max_hz` 由 spec 的 `topology.low_freq_max_hz`（可选）与 manifest 的 `low_freq_max_hz`（可选）
  给出；缺省相对规则 `min(3 GHz, SRF/10)`；无低频样本时 `L*_lf`、`k_lf` 为 None，其它列照算。

### 3.8 文档与示例

README、`skills/ic-opt/SKILL.md`、`docs/em/library.md`、`docs/em/devices.md`（由 reference.py 生成）、`src/ic_opt/em/pcell/README.md`：
site.yaml v2 示例（数字标注"按你的机器填"）、EM 示例改 demo_6m、`lib.region` 示例用占位量名、耗时注明参考主机、
删个人路径与 TSMC 文件名、"SRF ≥ 1.25×f0"改为 `srf_margin`、"M1 保留"改为夹具金属。

## 4. 任务分解（每任务一个提交，定向测试，`ruff` 干净；编码派 subagent Opus 5.5 / High，各自 worktree，先核对基点）

| 任务 | 内容 | 主要文件 | 测试 |
|---|---|---|---|
| T15.1 | site.yaml v2 + spec 资源必填 + 启动/引擎拒绝 + doctor 输出 | `site.py`、`recipe.py`、`eval/engine.py`、`blocks/{evaluate,optimize,doctor}.py`、`spec.py`、`migrate.py`、`cli.py` | C1；现有测试里的默认值改为显式 |
| T15.2 | 问题指纹 + EMX 物理键内容哈希 + `migrate-store` + `lib_signoff process_file=` | `spec.py`、`em/emx.py`、`stages/em_chain.py`、`eval/engine.py`、`store.py`、`cli.py`、`recipes/lib_signoff.py` | C2（合成工程与合成库上的迁移测试） |
| T15.3 | 库计算量由 `hosts.local` 推导；分块按字节；`lib_design` 预拟合与 `rel_sigma_max` | `library/{query,suggest,region,stage}.py`、`recipes/lib_design.py` | 拒绝、上限、OMP 不被抬高、分块等价 |
| T15.4 | 可移植：锁、spawn、平台说明 | `store.py`、新 `_lock.py`、`library/query.py`、README、ADR | C4 |
| T15.5 | 依赖与打包 | `pyproject.toml`、`suggesters/base.py` | C3（干净虚拟环境安装脚本 + 测试） |
| T15.6 | 夹具金属名 + 低频定义 | `stages/em_chain.py`、`em/pcell/profile_validation.py`、`em/measure.py`、`spec.py`、`library/manifest.py`、`library/dataset.py` | C5 |
| T15.7 | 文档与示例清理（第 3.8 节；审查 C 类 27–35） | 文档、SKILL、`reference.py` | `test_library_docs.py`、SKILL 测试 |
| T15.8 | 验收（Claude）：C1–C6，N28 库迁移后 `lib.load` 代际与 G 门，40 GHz 区域答案不变 | — | — |

依赖：T15.1 先行（3、7 依赖它的 site API）；T15.2、T15.4、T15.5、T15.6 互不依赖，可与 T15.1 并行；T15.3 在 T15.1 后；
T15.7 最后。

## 5. 约定

同 T14：方案/规格/验收由 Claude；编码派 subagent（Opus 5.5 / High）；一任务一提交；定向测试；英文代码注释；
不动生成器几何、不动 em-opt、不跑 EMX；派发提示必须写明"先 `git merge --ff-only main` 核对基点，测试用主树 venv +
`PYTHONPATH=<worktree>/src`"。

## 6. 风险

- 必填字段是破坏性变更：所有现有 spec.yaml（含测试夹具、`docs/em/library.md` 的示例、N28 库各部件的 `.icopt/spec.json`）
  必须显式写出资源字段；库部件的 spec.json 已包含（建库时写死），但要核对。
- 指纹与代际迁移必须先在合成工程上证明"迁移前后观测复用、代际数不变"，再对 N28 库做（备份 `observations.jsonl`）。
- spawn 使工作进程重新导入 ic_opt（每个约 1–2 s），对首次拟合的总时间影响可忽略。
- TuRBO/openbox 打包涉及许可证与 numpy<2 约束，可能需要单独一轮。

## 7. 待用户拍板

- D1 超时（`simulator.timeout_s` 已必填；`em.timeout_s` 现默认 3600）：EMX 超时是否也改为必填？建议：是（与"限制由用户指定"一致）。
- D2 缺 site.yaml 的行为：拒绝启动（已拍板"不指定就不能启动"）；`doctor` 是否额外通过 Executor 探测 `nproc`/内存，把用户填的
  上限与机器实况对照并**只提示不放行**？建议：是。
- D3 打包：openbox 继续 vendor 但在 pyproject 用 path 依赖声明；TuRBO 拷入 `ic_opt/_vendor/turbo`（MIT）随包分发。建议：是。
- D4 本轮范围：A 类 10 条 + B 类"M1"与 3 GHz 两条（T15.1–T15.7）；审查其余 B/C 类（RV 过孔名、AP=11 校验器、σ/μ 可配、
  缓存目录、license 探测、5 nm 网格、非均匀频率表、四端口极性、冒烟线宽）列入 T16。建议：是。
- D5 变压器格点间不准的普遍解法（按不确定度全域自适应补点的 `lib.densify`；L@f = 低频电感 × 谐振因子建模）列入 T16。建议：是。

## 8. 交付与验收记录（2026-09-24 夜 – 09-25 凌晨）

| 任务 | 提交 | 交付要点 | 复核 |
|---|---|---|---|
| T15.6 | b721144 | 夹具金属 DRC 豁免取 `profile.fixture_conductor`；`low_freq_max_hz`（spec 与 manifest 可设，默认 3 GHz，`"relative"` = min(3 GHz, SRF/10) 可选，因 OCEAN 一致性有 26/72 器件 SRF < 30 GHz）；缺低频样本只置空 | 109 绿；黄金 GDS 不变 |
| T15.5 | cc7aba2 | scikit-learn / threadpoolctl 入 `dependencies`；README 安装两步；`scripts/check_clean_install.sh` 两套干净环境 PASS；`test_packaging.py` | TuRBO 为 Uber 非商业许可，未拷入包 |
| T15.1 | 43e4888 | `site.yaml` v2 按主机、`HostLimits` 无默认值；`threads_per_run`、`em.threads/memory_gb/timeout_s` 必填；`EnvelopeError` 逐阶段拒绝、`slots` 无下限；doctor 打印上限并探测 `nproc`/MemTotal 只告警；CLI 退出码 2；migrate 显式写出 0.1 默认值 | 非 pcell 204 绿 + pcell 277 绿；46 个新测试；我修了一处 T15.6 测试的 `limits` 夹具 |
| T15.4 | 1325348 | `_lock.py`（fcntl / msvcrt）、`spawn` 工作进程、Windows 路径/编码/换行修正、平台说明入 README 与 ADR | spawn 副作用：从 stdin 喂的脚本不能触发拟合，须用带 `__main__` 保护的文件（T15.7 写入文档） |
| T15.2 | 5c2eadd | `Spec.problem()`/新指纹 + 旧指纹兼容；`physics_key(proc_sha256)` 去路径；`Emx.resolve_identity`；`ic-opt migrate-store`（重写两种指纹、搬 EMX 缓存键、备份、幂等）；`lib_signoff process_file=`；数据集缓存键改按行内容 | 234 绿；随后 ba5c40c：`opt.optimize` 也接受旧指纹、`em.binary` 出问题身份 |
| T15.3 | fe87509 | `Library(limits=hosts.local)`、`fit_plan`（并行数 = min(缺模型数, max_threads//2, max_memory_gb//单次拟合内存, Windows 61)）、每进程 BLAS = 预算均分且不超显式 `OMP_NUM_THREADS`、预测分块 = 10% 内存预算、`Predict` 预拟合 + `rel_sigma_max`、`lib_design` 传 limits | 254 绿；随后 9982bab：单次拟合超 `max_memory_gb` 拒绝、校准 JSON 原子写、`lib_signoff` 传 limits |
| TuRBO (b) | 51f74a0 | `-e vendor/TuRBO` 安装、正常导入、去 `sys.path` 插入；`turbo.egg-info` 不再跟踪；干净环境检查 A/B 均 PASS | 用户 09-24 23:57 拍板 b |

**T15.8 验收（在迁移后的 N28 库上）**
- 本机 `~/.ic-opt/site.yaml` 按用户上限写 `hosts.local: {max_threads: 128, max_memory_gb: 256, cshrc: …}`（T15.1 之后没有它什么都不能启动，这是设计）。
- `ic-opt migrate-store` 先 `--dry-run` 后实跑 8 个部件（各留 `observations.jsonl.bak-20260924T1534*`）：每个部件代际一对一（如 xfm_bs_ap `bdc60a7…` → `0b3c6da…` x1576），EMX 缓存条目全部搬到新键；`lib.load`：六张表行数不变（516/532/1576/1576/2440/2370）、`excluded {}`。
- 数据集缓存键公式变了（改按行内容），首次查询重建数据集并重新校准：两张单圈变压器表 40 GHz 六个模型各 6 分钟（6 进程并行）。
- C5：G1/G2 门 6/6；全量非 pcell 套件 235 绿（后续合入后 254 绿）。
- C3 回归：40 GHz 区域答案（`reports/library_query/region_acceptance_40g_t15.json`）AP 稳健 50 / 均值 4312、范围逐项相同；M10 稳健 23 相同、均值 3738 对 3739（一格贴窗边；重拟合的 BLAS 线程数不同带来 ~1e-6 相对差异，T14.1 记录过）、范围相同；热跑 33 / 35 s。
- C3（干净环境安装）：由 `scripts/check_clean_install.sh` 验证（T15.5、TuRBO b 各跑一次，PASS）。
- 未在真机验证：Windows / macOS 控制端（锁与 spawn 的 Windows 分支只以打桩测试）。

**列入 T16（审查 B/C 类其余项与新发现）**：RV 过孔名（行 13）、配置校验器 AP=11（行 14）、第三方生成器的 DRC 门（15）、EMX/Spectre 默认与 license 等待（16–18）、SSH 超时入 site.yaml（19）、库缓存目录可指定（20）、σ/μ 上限按列（21）、`relax` 参数（22）、5 nm 网格入 profile（23）、非均匀频率表（24）、四端口极性告警（25）、冒烟线宽夹取（26）；`OPENBLAS/MKL_NUM_THREADS` 也算显式上限；`process_file` 路径是否出问题身份；超时整组杀进程；`validate_profile` 经 SSH 读 `.proc`；`lib_design` 首次并行校准的跨进程竞争；变压器格点间不准的普遍解法（`lib.densify` 按不确定度全域补点、L@f = L_lf × 谐振因子建模）；TuRBO 长期替代（c/d）。
