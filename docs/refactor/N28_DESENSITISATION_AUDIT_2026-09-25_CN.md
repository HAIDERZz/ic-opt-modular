# N28 脱敏审计（2026-09-25，0.3.0 发布前，BACKLOG B-4 / RT-2）

本文记录发布 0.3.0 前对公开仓库与构建产物做的一次 N28 工艺事实检查：查了什么、怎么查、查到什么、改了什么、还剩什么要用户拍板。
**本文不含任何工艺数值**：发现只按种类描述（如"AP 层号""VIA8 切孔尺寸"），不写数字、不写真实工艺文件名与路径。

## 1. 范围

**规则（用户定）**：TSMC N28 的工艺事实不得出现在公开仓库与构建出的包里。工艺事实指：

- N28 叠层的 GDS 层号与 datatype；
- 金属、过孔、AP 的厚度；
- 最小线宽 / 线距 / 包围 / 过孔规则，以及任何 DRC 数值（含规则手册条目编号与条文）；
- EMX 工艺文件（`.proc`）中的介电常数、电阻率等数值；
- `.proc` 的文件名与路径；
- N28 私有 profile（`n28_1p10m` 的 `rule.yaml`）的内容。

不算工艺事实的：中文重构文档里作为名称出现的"N28""TSMC"；器件设计几何（外径、线宽、线距、匝数）；仿真结果。

**检查对象**：

- 仓库：`git ls-files` 列出的文件（`vendor/` 除外，未跟踪文件不在内）——`src/`、`tests/`、`docs/`（含 `docs/refactor/reports/**` 下的 JSON / HTML / py 与 `docs/refactor/*.md`）、`skills/`、`scripts/`、`examples/`、`README.md`、`RELEASE_NOTES_*.md`、`CONTRIBUTING.md`、`pyproject.toml`；二进制文件（PNG、HTML 内嵌图片、黄金 GDS）单独检查。
- 构建产物：本工作树离线构建的 wheel 与 sdist（随包数据含 pcell README 与 demo_6m profile）。
- 基点：main@8ecebce（任务开始时为 1128c7f，动手前快进到 8ecebce）。下文行号均指 8ecebce。

**对照来源**：仓库外的私有 N28 profile 与 N28 的 EMX 工艺文件，只读；只在本地临时目录整理词表，词表与原文件内容都不进仓库。

## 2. 方法

1. **整理敏感项清单**（仓库外临时目录）：
   - 名称类：`.proc` 文件名及其目录名、叠层代号、规则手册名与条目编号、标记层名、接触孔名；
   - N28 全部（layer, datatype）对（含 `.proc` 里额外出现的对），按五种写法匹配：`(L, D)`、`[L, D]`、`L/D`、`lLtD`、`layer: L, datatype: D`，另查 datatype 取 N28 特有值的写法；
   - profile 与 `.proc` 中的特征数值（59 个，出现即报）；
   - 常见数值（60 个，如整数线宽），只在带规则语境词（英文与中文：线宽、线距、包围、厚、过孔、层号……）的行里报；
   - 双向短语扫描："金属/过孔名 … 规则词 … 数值"与"规则词 … 数值 … 金属/过孔名"。
2. **逐条判定**：每个命中按上下文归为 敏感 / 无害 / 需决策。数值命中大多是巧合（结果列、CSS、计时），逐行看过。
3. **二进制**：HTML 里内嵌的 base64 图片解出后逐张目视（64 张，去掉与仓库 PNG 相同的 40 张，新图 24 张）；仓库 45 张 PNG 逐张目视，重点看版图渲染图的图例是否带层号；13 个黄金 GDS（及其历史版本）用 KLayout 列出层号对。
4. **修复**后重扫仓库；从工作树离线构建 wheel 与 sdist（setuptools PEP 517 后端，无网络、不做隔离构建），解包后用同一套清单扫描；改动前的 8ecebce 也同样构建扫描，作对照。
5. 私有 profile 只经 `IC_OPT_PROFILE_DIRS` 读取；需要它的测试在缺失时跳过。

## 3. 结论

- 敏感发现 30 项：**已修 25 项**（src 12、tests 5、docs 8），**需决策 5 项**（第 6 节）。无害类别 9 类（第 4.4 节）。
- 扫描器口径：8ecebce 上敏感类命中（名称 / 层号对 / 特征数值）1031 行、50 个文件；修复后（不计本文）846 行、34 个文件，逐行看过全部无害（多为结果 JSON 中恰好同形的数值、CSS、计时、门数"6/6"、demo_6m 的巧合）。
- 构建产物：修复前 wheel 与 sdist 各有 30 处敏感命中（24 行，全部在 `ic_opt/em/pcell` 下的 README 与模块注释）；**修复后为 0**（第 5 节）。唯一仍随包发布、而扫描器按文本查不到的是参考模式内置层号表（以公式写成），列为 O-1。
- 私有 profile 的 YAML 与 `.proc` 文件本身从未入库；`v0.2.0` 标签不含本次查到的内容。但本次修掉的内容在已推送的公开历史里仍可见（O-3）。
- 演示改用 demo_6m 后，`generate_all()`（pcell 演示与报告）不再需要私有 profile，任何人可运行。

## 4. 发现清单

### 4.1 随包源码（src，已修）

| 编号 | 位置（8ecebce） | 工艺事实的种类 | 处理 | 做法 |
| --- | --- | --- | --- | --- |
| S-01 | `src/ic_opt/em/pcell/README.md:21-24,34-42,53-57,74-77,87-89,104-108,141-145,151-152,167,203-205,213-215,224-226,253-254,264,302,307-308,368-370`（随包数据） | N28 层号与 datatype：M8 的 drawing/pin 对、M8–M10 pin 层号、AP 与 AP pin 层号、M10↔AP 过孔层号、参考版图的 M8/M9/via8 层号、参考模式 M1 层号写法；规则手册条目编号 3 处；由 N28 规则推出的结论（"多匝只在某层合法"）；以私有 profile 为演示对象 | 已修 | 演示改为 demo_6m，引用 demo_6m 自己的层号；参考模式只写函数名；删去条目编号与由规则推出的结论；"N28"只作历史名称 |
| S-02 | `src/ic_opt/em/pcell/_pcell_demo.py:591-815`（`DEMOS`） | 12 个演示以私有 N28 profile 为输入 | 已修 | 11 个改为 demo_6m（形状与尺寸不变，金属换成 M6/M5/M4/M3，全部可造；按 demo_6m 审计，只有接地夹具演示的 M1 夹具有 2 处线距告警——沿用原夹具参数，倒角桩之间过近，与工艺无关）；`base_balun_sec` 的工艺模式演示删除（该原语固定在 MET=9，demo_6m 没有这一层）；演示总数 31 → 30 |
| S-03 | `_pcell_demo.py:363-368`（`KNOWN_DEVIATIONS`） | 由 N28 VIA8 切孔与间距推出的阵列节距；参考版图 via7 节距 | 已修 | 改为按常量名描述（参考切孔 + 参考间距） |
| S-04 | `_pcell_demo.py:428-434,438,441-442,938` | 规则手册条目编号与条文内容（受限过孔范围、标记层名、例外带） | 已修 | 改为不带编号与内容的一般描述 |
| S-05 | `_pcell_demo.py:869-871,920-928,976-977` | 报告文字把参考模式层号表写成"参考工艺的层号"并列出 M1–M10 与过孔层号；参考版图 M8/M9/via8 层号；条目编号；渲染说明把 N28 AP 与层号范围关联 | 已修（文字）；代码中的层号表见 O-1 | 报告 JSON 的 `layer_mapping` 改为只说明来源（函数名 / profile）；参考模式过孔规则写常量名 |
| S-06 | `src/ic_opt/em/pcell/_pcell_core.py:728-730,978,1184-1189,1206,1209-1210,1289` | 条目编号及受限过孔范围；N28 已建模的过孔阵列清单；把 N28 AP 及其过孔与参考层号范围关联的说明；一个与 AP 最小线距相同的"间距下限"数值 | 已修 | 一般描述 |
| S-07 | `_pcell_guards.py:255-256` | AP 最小线距数值 | 已修 | 删去数值 |
| S-08 | `_pcell_xfm_tw.py:479-491,850` | AP 与 M9/M10 最小线距数值及由其推出的中间量；"AP 在某线距下"随机抽检描述 | 已修 | 改为符号式与不指明工艺的算例 |
| S-09 | `_pcell_primitives.py:803-807` | 电感区标记层名；条目编号与"VIA5 受限"条文 | 已修 | 一般描述 |
| S-10 | `_pcell_xfm_il.py:1178-1183` | "N28 对低层过孔另有限制"（条文内容） | 已修 | 一般描述 |
| S-11 | `rule_adapter.py:125,152-153,156-157` | 条目编号；N28 已建模过孔阵列清单 | 已修 | 一般描述 |
| S-12 | `profile_validation.py:17-18` | N28 `.proc` 的 define 写法示例，含 M1 真实层号 | 已修 | 示例改用 demo_6m 层号 |

### 4.2 测试（公开仓库，已修）

| 编号 | 位置（8ecebce） | 工艺事实的种类 | 处理 | 做法 |
| --- | --- | --- | --- | --- |
| T-01 | `tests/ic_opt/pcell/test_pcell_inductor_python_port_clean.py:872-1033,1077-1213,1294-1388,1499-1656,1708-2041,2114-2141,2321-2493,2741-2878,3397,4883-4925,5283-5325` | 约 50 处 N28 层号/datatype 元组；混在参考模式测试里的 N28 datatype 集合；VIA7/VIA8 的切孔/间距/包围/节距（µm 与 nm 两种写法）；AP、M9/M10 最小线距；M10↔AP 过孔的切孔 + 包围与由此推出的最小落点宽度；私有 profile 的仓库内路径；条目编号、标记层名、接触孔名 | 已修 | 期望值一律从加载的规则 profile 读取（新增 `_cut_nm`、`_pitch_nm`、`_enclosure_nm`、`_via_layer`、`_pin_layer`、`_rv_landing_floor_um`）；参考模式断言改为调用 `metal_layer()` / `metal_pin_layer()` / `via_layer()`；注释与文档字符串去掉数值与编号；演示名随 S-02 更新；名字里带线距数值的一个测试改名 |
| T-02 | `tests/ic_opt/pcell/test_geometry_process_rules.py:19-375` | 整份 N28 profile 内容被写成期望值：导体/过孔/标记清单（含标记层名）、覆盖清单、层号对、各类规则数值、限制条目编号、适用过孔、范围文字、例外标记与带宽、条文片段、叠层代号；另有两个测试把私有 profile 复制进包目录 | 已修 | 期望值改为与私有 profile 自身的 YAML 逐项比对（测装载是否忠实，不再抄值）；搜索顺序与打包回退测试改用公开的 demo_6m 副本 |
| T-03 | `tests/ic_opt/pcell/test_geometry_rule_adapter.py:15-321` | N28 层号与 pin、线宽线距、过孔切孔/间距/包围/节距、层类别、EMX 名、覆盖清单、条目编号、标记层名；两个测试把改过的私有 profile 写进临时目录 | 已修 | 同 T-02；缺数据时失败关闭的两个测试改用 demo_6m 副本 |
| T-04 | `tests/ic_opt/pcell/test_clean_port_generator_plugin.py:636-689,1857-1871` | 合成版图用 N28 的 M9/M10/VIA9 层号对；由 AP 最小线距推出的门限与报错文字 | 已修 | 合成版图改用 demo_6m 的 M5/M6/VIA5（从 profile 取）；门限由 profile 计算 |
| T-05 | `tests/ic_opt/pcell/test_profile_validation.py:220-227` | `.proc` define 解析样例用了 N28 M1 的 drawing/pin 层号对 | 已修 | 换成虚构层号 |

### 4.3 文档与报告脚本（公开仓库，已修）

| 编号 | 位置（8ecebce） | 工艺事实的种类 | 处理 | 做法 |
| --- | --- | --- | --- | --- |
| D-01 | `docs/refactor/T9_EM_PLAN_CN.md:91` | `.proc` 真实文件名 | 已修 | 占位 `<工艺>.proc` |
| D-02 | `docs/refactor/analysis/em/03_device_db_and_surrogate.md:394` | `.proc` 文件名与相对路径（含私有目录名） | 已修 | 占位 |
| D-03 | `docs/refactor/analysis/em/04_recorded_data_and_fakes.md:14,58` | `.proc` 文件名 | 已修 | 改为描述 |
| D-04 | `docs/refactor/analysis/em/02_pcell_geometry_layer.md:643` | 规则手册条目编号 | 已修 | 删去编号 |
| D-05 | `docs/refactor/AUDIT_ENV_ASSUMPTIONS_2026-09-24_CN.md:59,61` | 作为证据引用的 `.proc` 文件名（两种写法） | 已修 | 改为描述 |
| D-06 | `docs/refactor/EXECUTION_PLAN_CN.md:84` | N28 导体个数（profile 内容） | 已修 | 改为"全部导体" |
| D-07 | `docs/refactor/reports/pcell_plan/IND_LIBRARY_PLAN_CN.html:153` | EMX 命令行里的 `.proc` 文件名 | 已修 | 占位 |
| D-08 | `docs/refactor/reports/pcell_plan/ind_library.py:30`、`ind_pilot.py:24`、`xfm_pilot.py:24` | `.proc` 绝对路径与文件名写死在脚本里 | 已修 | 改读环境变量 `IC_OPT_EMX_PROC`，未设置时报错退出（这三个开发脚本以后运行需先设该变量） |

### 4.4 判为无害的类别（逐条看过，不改）

| 编号 | 位置 | 内容 | 判定理由 |
| --- | --- | --- | --- |
| B-01 | src / tests / docs 各处 | "N28""TSMC""n28"、profile 名 `n28_1p10m`、票据名 `n28-rules-slim`、JSON 键 `n28_drc_proof`、测试名、"1P10M+AP"编号约定 | 名称，不是工艺事实 |
| B-02 | src / tests / docs 各处 | 金属与过孔名（M1–M10、AP、VIA1–VIA9、RV）作参数值与代码常量（如 `_AUDITED_VIAS`，已另列 BACKLOG R-13）；层的上下顺序 | 名称 |
| B-03 | src、README、skills、tests | `.proc` 作为 EMX 工艺文件格式名；测试里的虚构路径（`/site/n28.proc`、`/site/demo.proc`） | 不是真实文件名或路径 |
| B-04 | `docs/refactor/reports/**`（JSON / HTML / py） | 器件设计几何、扫参网格、EMX 网格与仿真设置、仿真结果与模型误差、生成失败原因（含窗口尺寸） | 规则明确不算 |
| B-05 | 45 张仓库 PNG 与 HTML 内嵌 64 张图 | 版图渲染图的图例只显示层名或 demo_6m 层号；其余为结果图与架构图 | 逐张目视 |
| B-06 | `tests/ic_opt/pcell/golden/*.gds`（13 个，两版历史） | 只含 demo_6m 的层 | KLayout 列层确认 |
| B-07 | `docs/refactor/analysis/pcell/01_gdsfactory_v10_methodology.md:143`、`docs/refactor/reports/pcell_plan/gf_*.png` | gdsfactory 通用 PDK 的层号 | 与 N28 无关 |
| B-08 | 各处 | 与特征数值同形的计时、CSS 数值、门数（如"6/6"）、结果列取值 | 语境判断 |
| B-09 | `src/ic_opt/em/pcell/profiles/demo_6m/rule.yaml`、`skills/author-process-rule/SKILL.md` 及引用它的测试 | demo_6m（虚构）的层号与规则数值 | 虚构值；其中少数与 N28 同类数值相同，是否要求零重合见 O-5 |

## 5. 构建产物证据

| 产物 | 修复前（8ecebce） | 修复后（本提交） |
| --- | --- | --- |
| wheel `ic_auto_opt_workflow-0.2.0-py3-none-any.whl`（100 个文件） | 敏感命中 30 处 / 24 行：`ic_opt/em/pcell/README.md`（层号对、条目编号）、`_pcell_demo.py`（条目编号、标记层名）、`_pcell_primitives.py`（标记层名、条目编号）、`_pcell_core.py`、`rule_adapter.py`（条目编号）、`profile_validation.py`（`.proc` 层号写法） | **0** |
| sdist `ic_auto_opt_workflow-0.2.0.tar.gz`（121 个文件：`src/ic_opt`、egg-info、README、pyproject、LICENSE，不含 tests / docs） | 同上，30 处 / 24 行 | **0** |

- 扫描内容：第 2 节的全部清单（名称、层号对五种写法与 datatype、59 个特征数值、规则语境中的 60 个常见数值、双向短语扫描）。
- 修复后产物中仍出现、判为无害的：demo_6m profile 中与 N28 同形的一个层号（O-5）、`library/query.py` 里一个计时数、`RECORD` 中一段 sha256 编码碰巧含"tsmc"四个字母。
- 扫描器按文本查不到的：参考模式内置层号表以公式写在 `_pcell_core.py`（`metal_layer` 等），仍随包发布，见 O-1。
- 修复后同时核对：wheel 与 sdist 中不再有私有 profile 名作为演示输入，不含任何 `.proc` 文件名或路径。

## 6. 需要决策的遗留项（未修）

| 编号 | 位置 | 内容 | 为什么没修 | 建议做法 |
| --- | --- | --- | --- | --- |
| O-1 | `src/ic_opt/em/pcell/_pcell_core.py:141-181`（`metal_layer`、`metal_pin_layer`、`via_layer` 及注释）、`:1199-1222`（`_layer_display_name` 的层号范围回退）；随包发布 | 参考模式（`process=None`）的内置层号表：M1–M10、VIA1–VIA9 与 M1–M10 pin 的层号字段与 N28 相同（datatype 一律为 0）；注释称之为"参考工艺的层号" | 改动会改变参考模式的 GDS 输出（公开函数的行为） | 参考模式改用明显虚构的层号表，或取消参考模式、所有入口都要求 profile；`_layer_display_name` 改为按表查名。参考模式测试已改为调用这三个函数，会随表自动变化；黄金 GDS（demo_6m）与 N28 字节回放（工艺模式）不受影响；参考模式不进库，不需要升 `GEOMETRY_VERSION` |
| O-2 | `_pcell_core.py:24-27`（`VIA_CUT_UM` / `VIA_SPACE_UM` / `VIA_ENC_UM`，"在 ind_ref.gds 中量得"）、`:919`（`REFERENCE_MIN_MET_SPACING_UM`）；测试中对参考切孔的断言（如 `test_reference_mode_still_uses_reference_via_cut_size`） | 参考模式过孔常数量自参考版图 `ind_ref.gds`，该版图来自另一个私有工艺的项目（见 `AUDIT_ENV_ASSUMPTIONS` 第 33 行引用的原路径）；与 N28 的规则值不同。具体对照结果已交用户，不在此写出 | 不属于 N28，是否算需脱敏的工艺事实取决于 O-4；改动也会改变参考模式输出 | 与 O-1 一并换成虚构常数（推荐），或按 O-4 的决定处理 |
| O-3 | 公开仓库历史：`origin/main` = 1128c7f（已推送） | 本次修掉的内容在历史中仍可见：自 d3a80c0 与 7dc340c（2026-09-22）起的测试、文档、报告脚本、pcell README 与演示；另有 1128c7f 的 BACKLOG 一行列出 AP、M10↔AP 过孔与 pin 层号（8ecebce 已改写，但 1128c7f 已公开）。私有 profile YAML 与 `.proc` 本身从未入库；`v0.2.0` 标签（ceef822）早于上述提交，经检索不含这些内容 | 删改 HEAD 不能撤回已公开的历史 | (a) 接受现状，只保证 0.3.0 起干净；(b) 用 `git filter-repo --replace-text`（替换清单放在仓库外）改写 `origin/main` 全部历史并强制推送，通知已有克隆，改写后用本次清单对全部历史 blob 复查；(c) 先把仓库临时设为私有再决定 |
| O-4 | 另一个私有工艺 profile（`n65_1p9m`） | 本次只审 N28。检查中顺带看到，这个工艺的同类事实也出现在仓库里（参考模式常数 O-2，以及个别测试注释），具体位置已交用户，不在此列出 | 超出本次范围 | 若它同样受此规则约束，以它的 profile 为词表重复本次审计（方法同第 2 节） |
| O-5 | `src/ic_opt/em/pcell/profiles/demo_6m/rule.yaml`（随包）与 `skills/author-process-rule/SKILL.md` 副本 | demo_6m 声明全部虚构，但有少数数值（一个层号、若干线宽/线距，都是常见整数）与 N28 的同类数值相同。为免反推，本文不列具体项 | 改 demo_6m 会改变黄金 GDS | 如要求零重合：在 demo_6m 与 skills 副本中同步改成其他虚构值，`IC_OPT_REGENERATE_GOLDEN=1` 重生黄金 GDS，同一提交升 `GEOMETRY_VERSION` |

## 7. 测试与检查

- `ruff check src tests`：通过。
- 有私有 profile（`IC_OPT_PROFILE_DIRS=<私有 profile 目录>`）：`pytest tests/ic_opt/pcell tests/ic_opt/test_em_pcell.py` 全部通过（917 通过、1 跳过；与改动前收集到的 918 个测试一一对应，没有删测试，3 个改名）。
- 无私有 profile：`pytest tests/ic_opt/pcell tests/ic_opt/test_em_pcell.py tests/ic_opt/test_packaging.py` 全部通过（288 通过，其余因缺私有 profile 跳过），黄金 GDS 13 例全部一致；整个 `pytest tests` 542 通过、647 跳过。
- 无私有 profile 运行 `generate_all()`：30 个演示全部生成，其中 11 个为 demo_6m 工艺模式。

## 8. 以后怎么复查

- 词表与扫描脚本不能进仓库（它们本身就是工艺事实）。建议把词表与脚本放在私有 profile 旁边，发布前对工作树与构建产物各跑一次；新增 profile（如 N65）时同样生成词表。
- 写测试时，工艺模式的期望值一律从加载的 profile 读取（本次在 T-01 中新增的辅助函数可直接复用），不写数值；需要合成 profile 时从 demo_6m 复制。
- 文档里提到私有工艺，只写种类，不写数值、条目编号、`.proc` 文件名与路径。
- 给私有工艺的 GDS 出图时，图例只用层名（`ic_opt.em.pcell.render.render(..., names=layer_names(profile))`）。`render.render` 不给 `names` 时、以及 `_pcell_demo._render_png` 的图例，都会写出"层号/datatype"，放进报告页就等于公开层号。本次检查的全部图片都没有这个问题。
