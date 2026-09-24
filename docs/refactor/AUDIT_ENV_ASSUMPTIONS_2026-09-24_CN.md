# 审查：把开发机约束或测试案例当成普遍情况的地方（2026-09-24）

用户提出的问题："开发时我限制在 128 线程 / 256 GB，因为我有这样的服务器；如果写死，对其他用户就是 bug。测试用的
160 pH / 40 GHz 变压器只是随手的例子，不是我关心的区域；改进要面向普遍需求。审查整个项目源码有没有这种情况。"

审查范围：`src/ic_opt/` 全部、`skills/`、`docs/em/`、README、`pyproject.toml`（只读；main@5f014d0）。执行：subagent
（Opus 5.5 / High），Claude 复核了关键证据行（site.py 的默认值与 `slots` 下限、`workers_for` 的 `site is None`、
`Spec.fingerprint` 含资源字段、`physics_key` 含 .proc 路径、`"M1"` 字面量、`_AUDITED_VIAS = ("RV",)`、`TURBO_PATH`、
`LOW_FREQ_MAX_HZ`）。

分类：A = 机器相关的假设（bug）；B = 从本库 / 本例 / 本工艺调出来的值，应可配置或注明；C = 文档 / 测试把例子写成常态；
D = 物理或统计上的合理选择。

## 直接回答两个担心

- **128 线程 / 256 GB**：产品里没有 256；128 是 `site.py` 在没有 `~/.ic-opt/site.yaml` 时的内置默认（128 线程、**128 GB**），
  本机没有 site.yaml，所以一直在用这个默认，而不是 256 GB。问题在于任何机器没有 site.yaml 都会得到这个"大机器"默认（行 1）。
- **160 pH / 40 GHz**：没有任何产品代码写死它；只出现在文档、agent 技能说明和一处 docstring（行 27）。库代码的维度、
  结果列、频率列、步长全部来自 `library.yaml`。产品代码里唯一的频率常数是 `LOW_FREQ_MAX_HZ = 3e9`（行 11）。

## 发现清单（A 在前）

| # | 位置 | 假设 | 类 | 证据 | 修法 |
|---|---|---|---|---|---|
| 1 | `src/ic_opt/site.py:20-21,32-33` | 无 site.yaml 时上限默认为本服务器量级：16 核 / 32 GB 的机器也会放行 4 × 32 GB EMX 或 12 × 10 线程 Spectre | A | `max_threads: int = 128` / `max_memory_gb: float = 128.0`；`if not path.exists(): return Site()` | 通过 Executor 探测执行机（`nproc`、MemTotal），取探测值与 site.yaml 的较小者；缺 site.yaml 时 doctor 提示 |
| 2 | `site.py:28`；`eval/engine.py:86-93` | 单个任务比整个上限还大也照跑（并发数下限为 1），只有 `env.doctor` 会拒；`lib_signoff`、文档里的 `sweep.py`、自定义配方都不调 doctor | A | `return max(1, min(by_threads, by_memory))` | `engine.run` 里任一阶段线程或内存超上限即拒绝 |
| 3 | `blocks/evaluate.py:31`；`blocks/optimize.py:83`；`eval/engine.py:89-90` | `site=None` 表示完全没有上限；README:67-68 与 SKILL.md:45-46 的配方骨架都没传 `site=`；EM spec 下 EMX 内存上限被跳过（`run.jobs` 只数 Spectre 线程） | A | `if site is None: return wanted` | 默认 `site.load()`（或 `site` 必填），改两处骨架 |
| 4 | `site.py:15`；`recipe.py:55-59` | 只有一份 site.yaml（上限与 cshrc），不按 `--ssh-profile` 区分；远程 scratch 固定在远端 `~/.ic-opt/scratch/<工程>`，实验室 home 常有配额 | A | `SITE_FILE = Path("~/.ic-opt/site.yaml")`；`SshExecutor(ssh_profile, f"~/.ic-opt/scratch/{spec.project}")` | site.yaml 加 `hosts: {local: …, <profile>: {max_threads, max_memory_gb, cshrc, scratch_root, …}}`，按 `executor.host` 选 |
| 5 | `library/query.py:44,110-114`；`library/region.py:43,151-156` | 库的并行拟合按本机 256 核定（`os.cpu_count()` 数的是整机，不是 cgroup / 亲和 / 站点份额）；不看内存（4000 行联合模型一次拟合超 1 GB）；显式 `OMP_NUM_THREADS=4` 会被抬到 2×workers（28 列 → 56 线程）；`DEFAULT_THREADS=8` 在 4 核笔记本上超订 | A | `FIT_WORKERS = max(1, (os.cpu_count() or 2) // 2)`；`threads = max(int(os.environ.get("OMP_NUM_THREADS", "8")), 2 * workers)` | workers = min(缺的模型数, 可用核数/2, 站点份额, 内存预算/单次拟合估算)；绝不超过显式 OMP_NUM_THREADS；默认线程 = min(8, 可用核) |
| 6 | `library/suggest.py:49` | 预测分块按固定行数，内存随训练行数增长：n=400 约 0.8 GB，n=3000-5000（联合变压器表）6-10 GB；`lib.region`（200 万格）可能把笔记本控制端打爆 | A | `PREDICT_CHUNK = 40_000` | 按字节定分块：预算 / (8·n_train·7)，预算来自 site.yaml 或参数 |
| 7 | `spec.py:465-467`（用于 `eval/engine.py:117,125-127`、`blocks/optimize.py:91-100`） | spec 指纹（问题身份）包含机器资源：parallel_jobs、threads_per_run、超时、EMX 线程/内存，还有 budget。为小机器调低 `parallel_jobs` 就换指纹（已验证 10→4 不同）：旧观测不复用，`opt.optimize` 从 0 数，而 budget 仍按旧仿真数扣 | A | `json.dumps(self.model_dump(mode="json"), …)` | 指纹只含问题字段；剔除资源、超时、保留策略、license、budget |
| 8 | `em/emx.py:72-74`；`stages/em_chain.py:164`；`library/dataset.py:116-122` 消费 | EMX 物理键（既是 EMX 缓存键也是库的"代际"）包含本机 `.proc` **路径**（二进制路径已剔除）。同一 `.proc` 换路径或换机器就算新代际，协作者回流的行被当"其他代际"丢弃；原地改过的 `.proc` 反而沿用旧代际；`lib_signoff` 无法覆盖路径 | A | `exclude={"threads","memory_gb","timeout_s","verbose","binary"}` | 键里去掉路径，放入 proc 内容 sha256（`Emx.fingerprint` 已算）；`lib_signoff` 加 `process_file=` |
| 9 | `pyproject.toml:17-24`；`suggesters/base.py:22` | 只在本开发 checkout + venv 能跑：scikit-learn、threadpoolctl（库用）和 openbox（默认策略）未声明，靠 `-e vendor/open-box` 带入；`turbo` 从源码树加载 TuRBO，wheel 里没有；turbo 是 lib_design、signoff、coarse_to_fine 细阶段的默认策略 | A | `TURBO_PATH = Path(__file__).resolve().parents[3] / "vendor" / "TuRBO"` | 声明依赖（作为 extra），正规打包 TuRBO |
| 10 | `store.py:11`；`library/query.py:113` | 控制端必须是 Linux：`fcntl`（Windows 上 CLI 都导入不了）、`fork` 进程上下文；ADR-0001 的目标场景是"个人 PC → 实验室服务器" | A（若 Windows 控制端在范围内） | `import fcntl`；`multiprocessing.get_context("fork")` | 可移植文件锁 + spawn 安全的工作进程，或在 README/ADR 明说控制端须 Linux/macOS |
| 11 | `em/measure.py:28,39,108-111,158,174` | "低频"固定 ≤ 3 GHz，是毫米波的选择。L_lf、k_lf 总是计算：扫频没有 ≤ 3 GHz 的点（如 50-120 GHz）时每个点都 `failed:measure` 被丢弃，哪怕只要 `Lp@80`；SRF 接近 10 GHz 的射频器件 L_lf 高约 3%。spec/manifest 都改不了 | B | `LOW_FREQ_MAX_HZ = 3e9`；`raise MeasureError("no finite low-frequency samples …")` | 做成 spec/manifest 字段，默认相对（如 ≤ min(3 GHz, SRF/10)），标量按需计算 |
| 12 | `stages/em_chain.py:142`；`em/pcell/profile_validation.py:532` | 地环夹具的 DRC 豁免按名字 `"M1"`，而 profile 可把这层金属叫任何名字（`ground_fixture_conductor`）。**已验证**：demo_6m 把 M1 改名 ME1（schema 合法）后 ind_sym 与 xfm_bs 校验失败 `[max_width] ME1 x1`；这种 profile 上每次构建都会 `failed:pcell` | B | `ignore_findings=frozenset({("max_width", "M1")})` | 两处都用 `profile.fixture_conductor`（一个共享函数） |
| 13 | `em/pcell/drc_audit.py:319` | 过孔包围审计只查名叫 "RV" 的过孔（TSMC 命名）；其他厂的 RDL 过孔被静默跳过，SKILL.md:141-143 让作者改名当变通 | B | `_AUDITED_VIAS = ("RV",)` | profile 标记要审计的过孔（类别或 `audited_vias`） |
| 14 | `em/pcell/generator_plugin.py:44-60,314-316,411-412,498-508,726-738,770-778` | 配置校验器用 N28 的编号（AP = 11、金属名 `M<n>`），不用 profile 的金属栈：6 层 + AP 时 `metal: AP, ct_metal: M6` 能过配置校验、到 pcell 才失败；不叫 `M<n>` 的金属名整个跳过检查 | B | `if token.upper() == "AP": return 11`；`("AP" if i == 11 else f"M{i}")` | 在 `use_stack(profile)` 内用 `stack.index` 解析位置 |
| 15 | `stages/em_chain.py:141`；`em/pcell/drc_audit.py:545-552,567` | 产品级 DRC 门只认六个内置生成器；第三方插件（spec.py:231 宣传的 `Device.plugin`）除非 `drc_check=False` 否则必 `failed:pcell` | B | `_EXPECTED_RECIPES.get(generator_id)` → `ValueError` | 让生成器声明期望导体；表作为回退 |
| 16 | `spec.py:299-302` | EMX 默认（4 线程、32 GB、3600 s）是本服务器的典型器件；配合 128 GB 站点默认，doctor 在任何机器都接受每路 32 GB，而 EMX 视上限为软的 | B | `memory_gb: float = Field(default=32.0, …)` | `memory_gb` 必填，或用站点校准的模型估算 |
| 17 | `spec.py:201`；`stages/spectre_chain.py:83` | 实验室值：Spectre threads_per_run 默认 10；license 排队等待固定 900 s 无设置 | B | `default=10`；`"+lqtimeout", "900"` | threads_per_run 注明为实验室调优；加 `simulator.license_queue_timeout_s` |
| 18 | `blocks/doctor.py:61-67`；`executor/base.py:53-56` | doctor 即使纯 EM spec 也要求 `spectre`、`ocean`，还要 source cshrc 后 PATH 里有 `lmstat -a`（本实验室配置）；环境钩子只支持 csh 文件 | B | `which spectre ocean`；`lmstat -a`；`["csh","-fc", …]` | 只探测流水线用到的工具；按主机加 `license_probe`、`env: {script, shell}` |
| 19 | `executor/ssh.py:45` | SSH 传输/探测超时固定，CLI 与 site.yaml 都改不了 | B | `transfer_timeout_s: int = 1800` | 按主机的 site.yaml 键 |
| 20 | `library/dataset.py:97-137`；`library/query.py:159-169,184-190` | 数据集、校准、模型缓存都写进库根目录；库只读共享给他人时第一次查询就 PermissionError | B | `root / ".cache" / f"dataset-{name}-{key}.json"` | 允许指定缓存目录，库根不可写时回退 `~/.cache/ic-opt/<key>` |
| 21 | `library/domain.py:31`；`library/stage.py:57`；`blocks/library.py:53,61,75` | 置信上限 σ/μ ≤ 0.15 对 L、Q、SRF、k 一视同仁；`lib.query/suggest/region`、`lib_design` 都不暴露，`Predict` 里又重复了一遍字面量 | B | `DEFAULT_SIGMA_REL_MAX = 0.15`；`rel_sigma_max: float = 0.15` | block/配方参数，可选 library.yaml 按结果列设置 |
| 22 | `library/region.py:42` | 粗筛放宽固定 10%；括号盒需要的余量取决于模型 σ 与候选池密度 | B | `RELAX = 0.10` | `relax=` 参数 |
| 23 | `em/pcell/_pcell_core.py:20` | 5 nm 制造网格是 N28 的，不在 profile schema 里 | B | `GRID_UM = 0.005` | profile 字段（默认 0.005），或注明限制 |
| 24 | `em/measure.py:53-58` | 频率列必须落在最小全局步长的一半内，适合本库的 1 GHz 线性扫频；spec.py:291 允许的非均匀频率表在高频列会失败 | B | `> 0.5 * _grid_step(self.freqs)` | 用局部间距，或插值 |
| 25 | `spec.py:251-259` | 默认四端口拓扑把次级反相，为的是与内置器件族的极性一致；别的生成器端口几何会静默得到负 k | B | `(base[3], base[2])` | 文档说明；k_lf < 0 时告警 |
| 26 | `em/pcell/profile_validation.py:66-71,376-470` | 校验冒烟用 N28 建库时的尺寸（地环 50、边距 15、线宽 6）；间距随 profile 变，线宽不变 | B | `"ring_width_um": 50.0`，`"width_um": 6.0` | 线宽夹到 profile 的 [min_width, max_width] |
| 27 | `skills/ic-opt/SKILL.md:65`；`docs/em/library.md:147-154`；`library/region.py:4-6`；`blocks/library.py:27,87` | `lib.region` 的示例就是这次的测试案例（150-170 pH、Q > 10、k 0.5-0.7、40 GHz）。技能说明面向 agent，agent 会锚定这些数 | C | `"Lp@40": {"min": 150e-12, "max": 170e-12}` | 用占位符，或两个对比示例并标明仅示意 |
| 28 | `README.md:136-137`；`skills/ic-opt/SKILL.md:24` | 把"默认 128 线程 / 128 GB"当普遍默认写出 | C | 原文 | 与行 1 一起改 |
| 29 | `docs/em/library.md:176-178,232-234` | 过时："workers … 最多 6"（代码已是核数一半）；推荐的 OMP 上限实际不起作用（行 5） | C | 原文 | 与行 5 一起改 |
| 30 | `skills/ic-opt/SKILL.md:57` | 说提高 budget 后"旧观测按点复用"，在当前指纹下不成立（行 7） | C | 原文 | 与行 7 一起改 |
| 31 | `README.md:94-107` | 唯一的 EM 示例是 N28（`n28_1p10m`、`/site/tsmcN28.proc`、`[M10, AP]`、`4e10`），而随包只有 demo_6m | C | 原文 | 示例改基于 demo_6m |
| 32 | `blocks/library.py:66,84`；`docs/em/library.md:142`；`docs/em/devices.md:5`（由 `reference.py:180` 生成） | "SRF >= 1.25 x f0"（其实是 manifest 的 `srf_margin`）；"M1 保留"（其实是 profile 的夹具金属） | C | 原文 | 改措辞 |
| 33 | `em/pcell/README.md:368,394`；`pcell_inductor_port_clean.py:16`；`_pcell_demo.py:917-920`；`_pcell_core.py:144` | 个人路径与 TSMC 文件名随包发布；pcell README 是包数据，demo 还把路径写进 JSON 报告 | C | `"/home/zzchen/Prj/Prj_For_N65/ind_ref.gds"`、`tsmcN28_1p10m.proc` | 公开发布前删除 |
| 34 | `tests/ic_opt/pcell/test_compact_planner.py:76`；`tests/ic_opt/test_em_engine.py:173` | 按本机校准的墙钟断言（笔记本/CI 会抖）；一个测试钉死了行 2 的行为 | C | `< 0.15`；`== 1  # never below one worker` | 标 slow 或用相对界；随行 2 更新 |
| 35 | `recipes/lib_signoff.py:85`；`library/query.py:11-12` | 本机测得的耗时被当作普遍说法展示给用户（"~2-5 min per round"） | C | 原文 | 写"在参考主机上" |
| 36 | `library/gp.py:42,81,232-237`；`library/query.py:42`；`library/domain.py:30,61` | 统计选择：5 折 × 20% 留出、校准到 95%、SRF"高于扫频"= 5 近邻中 3 个、每匝数层 ≥ 25 行、凸包分块约 160 MB。25 行可做成 manifest 字段 | D | — | 无 |
| 37 | `library/manifest.py:39`；`library/region.py:47-52`；`library/suggest.py:295-297` | srf_margin 1.25 是 manifest 字段；levels_per_dim 20、max_points 2e6、pool 32768/8192、min_spacing 0.05、sample_size 5000 是参数；除行 6 外笔记本内存无忧 | D | — | 无 |
| 38 | `em/measure.py:115-121`；`spec.py:301` | L_res 取 ≤ SRF/5 是相对器件的；`simultaneous_frequencies: 0` 是安全默认 | D | — | 无 |
| 39 | `em/pcell/stack.py:12,29,84-91`；`_pcell_core.py:147-181` | "AP = 11"/GDS 层 30+m 只在参考模式生效；产品路径总有 profile（`Device.profile` 必填，生成时开 `use_stack`）；唯一泄漏是行 14 | D | — | 无 |
| 40 | `library/manifest.py:26-28`；`library/gp.py:55-67`；`migrate.py:75,104-118,202-210` | 无量纲特征映射只对 xfm_bs / xfm_ms 存在，但按 manifest 可选；迁移默认（CT "7"、4 线程 / 32G、10 线程）是有意复现 em-opt / 0.1 的行为 | D | — | 无 |

## 最重要的三项修复

1. **让站点上限真实且按主机区分**（行 1-4、28、34）：不再默认成本服务器的大小——通过 Executor 探测 `nproc` / MemTotal，取与 site.yaml 的较小者；site.yaml 按 `executor.host` 分主机（上限、cshrc、scratch、license 探测）；`site` 总是生效（默认 `site.load()`），`engine.run` 拒绝任何单阶段超上限。今天只有配方恰好传了 `site=` 又调了 doctor 时上限才起作用。
2. **机器信息不进身份标识**（行 7、8、30）：spec 指纹只含问题本身；EMX 身份与缓存键用 proc 内容哈希而不是路径。今天把工程搬到小机器会重置优化器续跑，把库搬到别的机器会把它劈成两个代际。
3. **库的计算按实际分配定，而不是按 256 核的一半**（行 5、6、9、29）：workers、BLAS 线程、预测分块由亲和核数、site.yaml 与内存预算决定；绝不覆盖显式 OMP_NUM_THREADS；在 pyproject 声明 scikit-learn、threadpoolctl、openbox 与 TuRBO。

紧随其后：行 12（已验证：底层金属不叫 "M1" 的 profile 每次构建都失败）与行 11（3 GHz 的"低频"定义）。

## 已查且干净的部分

- 没有代码按工艺 id 分支；产品代码不导入 `docs/refactor/reports`。EMX 内存模型、内存档 A-E、`MS_THREADS`、加密偏移与格点步长只存在于 `docs/refactor/reports/pcell_plan/*.py`，没有东西导入它们。
- `query`、`suggest`、`region`、`stage`、`dataset` 没有写死 10/28/40/60 GHz、pH 窗口、Q 或 k 阈值、外径/线宽范围、匝数或 N28 金属名，全部来自 `library.yaml`；配置模型没有 N28 默认值，每个几何字段都必填。
- ADR-0001：doctor 的导出与 proc 检查、`.proc` 哈希、sNp/日志取回、网表导入、Spectre/OCEAN 全经 Executor，cshrc 从不猜。库、profile、pcell 按设计在控制端跑。执行路径上没有 `/home/zzchen` 或 `/opt`；行 33 的路径只在包内文本与 demo 的 JSON 报告里。
- 测试：没有按核数或内存定工作量的；私有数据测试在环境变量缺失时跳过。本仓库没有 `docs/guide/`。

## 待决定的问题

1. 缺 site.yaml 时：探测主机，还是拒绝运行？
2. Windows 控制端是否在范围内（行 10）？
3. 产品要不要带站点校准的 EMX 内存估算？报告脚本里有（绕组周长的幂律），产品现在信用户给的软上限 `memory_gb`。
4. 超时可能不释放资源：`LocalExecutor.run` 大概只杀 csh/sh 外壳，EMX/Spectre 继续跑；SSH 只杀本地客户端。下一个任务会在旧任务仍占核与内存时启动。是否让任务各自成进程组/会话，整组杀？
5. `lib_design` 在并行线程里跑 Predict，没有预拟合也没有 BLAS 上限：首次使用时每个线程同时拟同样的模型；`Library.model` 无锁，校准 JSON 非原子写。是否像 `lib_signoff` 那样先预拟合？
6. `em.validate_profile proc=` 从控制端磁盘读 `.proc`，远程用户的 `.proc` 在仿真机（可能受 NDA 限制）。是否经 `--ssh-profile` 读？
