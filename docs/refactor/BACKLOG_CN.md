# 待办总表（阶段性收尾，2026-09-25）

一张表管全部未完成事项：来源（审查行号 / 方案节 / 记忆）、现状、下一步、谁来定。
状态含义：**待拍板** = 需要用户决定；**可做** = 已批准或无需决定，等排期；**进行中** = 已派发；**记录** = 只需知道，不打算单独做。
编号稳定，后续引用 W-x / RT-x / B-x / R-x / N-x。新任务从这里领取，完成后在 `EXECUTION_PLAN_CN.md` 追加一条并在此划掉。

**2026-09-25 用户拍板**：推送已完成；发 0.3.0；B-1 取 a + b；B-2 回流；B-3 暂维持 b；B-4 发布前脱敏；B-5 取 a；B-6 按第 2 节顺序。

## 0. 收尾动作

| # | 事项 | 状态 |
|---|---|---|
| W-1 | `git push origin main`（只推 `origin`，不推 `github` 远程） | 已做；2026-09-25 起由 Claude 推送（`git push origin main` 1128c7f → d512977；`--tags` 合并形式曾被自动模式的命令分类器拦下，分开推 main 与单个标签即可） |
| W-2 | 版本号升 **0.3.0**（T15 是破坏性变更：site.yaml v2、spec 资源字段必填、指纹与代际迁移） | 已拍板 → 见"发布 0.3.0"表 |
| W-3 | `.gitignore` 加 `.claude/worktrees/`；删除未跟踪的全量加密网格 | 已做（1128c7f） |
| W-4 | `EXECUTION_PLAN_CN.md` 补 T15 条目与收尾条目；审查文档状态头修正；新建 `reports/INDEX_CN.md` | 已做（1128c7f） |
| W-5 | N28 库迁移备份 `<库根>/<部件>/.icopt/observations.jsonl.bak-20260924T1534*`（8 个部件）与两个变压器复核工程的 `.bak-20260924T1829*`：保留一周后删除 | 可做（2026-10-02 后） |
| W-6 | 记忆整理 | 已做 |
| W-7 | `reports/library_query/IND_QUERY_VERIFY_CN.html` 仍是 09-23 版（1038 行），数据已按回流后 1048 行重做基线：用 `ind_query_report.py DATASET_JSON VERIFY_JSON OUT_HTML` 重生成并重发 artifact | 已做（cbbe008，artifact v2；页眉行数改为取自数据集） |

## 0.5 发布 0.3.0（用户 2026-09-25 拍板；顺序：RT-1/2/3 并行 → RT-4 → RT-5）

| # | 事项 | 状态 |
|---|---|---|
| RT-1 | B-5：`migrate-store` 按 0.2.0 发布版的指纹公式匹配并重写 0.2.0 时代的行，用 v0.2.0 标签代码真实生成的 store 做夹具验证 | 已做（7b0f1d5：`v020_fingerprint` 白名单公式 + 0.2.0 的"只按阶段名"流水线指纹精确匹配并重写；发现 0.2.0 store 因 T9.1 把 `testbench` 改名 `unit` 根本读不进来，加了读取别名；夹具 `tests/ic_opt/fixtures/store_v020/` 由 v0.2.0 代码真实写出；7 个新测试，271 绿） |
| RT-2 | B-4：N28 脱敏审计（随包 + 随仓库；层号、厚度、规则数值、`.proc` 名与路径、profile 内容），修复可修项，审计文档 `N28_DESENSITISATION_AUDIT_2026-09-25_CN.md` 本身不含数值 | 已做（079e917：30 项敏感发现修 25、5 项待拍板 B-7…B-11；wheel/sdist 命中 30→0；11 个演示改 demo_6m；pcell 套件 917 绿、黄金 GDS 13/13） |
| RT-3 | 按 0.2.0 发布说明的承诺删除 0.1 命令行 shim（`ic-opt PROJECT --real…`），保留 `ic-opt migrate` | 已做（90f6a0b：旧命令行退出码 2 并提示 migrate + run；CLI 测试 77 绿） |
| RT-4 | Claude：合入并验收 RT-1..3；`RELEASE_NOTES_v0.3.0.md`（升级步骤：写 site.yaml、补 spec 资源字段、`migrate-store`）；版本号 `pyproject` / `__init__` / README；干净环境安装检查；定向测试 | 已做（发布提交 + 本地标签 v0.3.0；非 pcell 271 绿、pcell 917/288 绿、CLI 77 绿、干净安装 PASS） |
| RT-5 | `git push origin v0.3.0`（Claude 已于 2026-09-25 推送）与 GitHub Release 页（发布说明用 `RELEASE_NOTES_v0.3.0.md`） | 已做：标签由 Claude 推送，Release 页由用户于 2026-09-25 发布（wheel + sdist 从 v0.3.0 标签构建） |

## 1. 已拍板事项（2026-09-25）

| # | 事项 | 决定 | 落点 |
|---|---|---|---|
| B-1 | 变压器格点间模型不准的普遍解法（真实复核：格点间 Lp@40 偏 +1.5…+22%、k 到 +9%、SRF 到 −18%，区间诚实但宽；根因 bs 表次级外径 20 µm 一档太稀、锚定量在谐振附近变化快） | a + b：`lib.densify` 按模型不确定度全域补点；建模改为 L@f = L_lf × 谐振因子 | T16.1 `lib.densify` 已做（0b3e5be：精确后验方差贪心选点、`bounds=` 子域、`score=ceiling|typical`；真实库六份输出 `reports/library_query/densify_40g_xfm_bs_*.json`）；T16.2a 研究已做（cab49bd，页 `XFM_ANCHOR_MODEL_STUDY_CN.html`，artifact GYPUVJwkAmSBM2aEyjFxmR）：关键是输入坐标——换成平均外径 + 外径比后，SRF 整档留出 p90 84–95%→1.4–1.6%；"低频电感 × 谐振因子 × 无量纲残差"（DF）把 Lp@40 整档留出 p90 36–40%→2.9%，区间最窄且诚实；k 保持现状；另发现采样空白"偏心 + 两外径不等"从未采过（10 个格点间点正落在那里）。T16.2b 已做（442b0c3 + 验收 34f04a5：`Quantity.model: direct|ratio|resonance`、`library/composed.py`，库自身复现研究 DF 数字：留一档 OD_P p90 2.91/2.94%，10 个格点间最大 4.75/9.24% 覆盖 100%）；2026-09-25 10:00 已按建议改真实库 `library.yaml`（备份 `.bak_20260925_1000`：xfm_bs 块 SRF feature_map、Lp/Ls `model: resonance` + feature_map），40/60 GHz 区域页已按新模型 + 回流行重生成并重发（82701d8；40 GHz 稳健 50/23→927/452、均值 4312/3738→5818/4947；60 GHz 稳健 263/205、均值 2068/2693）；T16.1c 待批准（B-12）；ms 表 `ratio` 另做对照（N-16） |
| B-2 | 10 个格点间变压器实测点回流入库 | 回流 | 已做（2026-09-25 02:33：两复核工程先 `migrate-store` 重写代际，再经 `lib_signoff._adopt` 复制，xfm_bs_ap / xfm_bs_m10 各 1576→1581 行，数据集重建 excluded {}；记录 `<ic-opt-library>/n28_signoff/adopt_xfm_bs.{py,log}`、`migrate_store_xfm_bs.log`） |
| B-3 | TuRBO 长期方案 | 暂维持 b（`-e vendor/TuRBO`，Uber 非商业许可） | 记录；面向商业用户时再议 c / d |
| B-4 | 公开发布前的 N28 脱敏 | 发布前审计 | RT-2 |
| B-5 | v0.2.0 时代工程 store 的指纹兼容 | a：`migrate-store` 加 0.2.0 公式匹配 | RT-1 |
| B-6 | T16 范围与顺序 | 按第 2 节顺序 | `T16_PLAN_CN.md` |

### 1.1 脱敏审计新出的待拍板项（RT-2，2026-09-25；详见 `N28_DESENSITISATION_AUDIT_2026-09-25_CN.md` 第 7 节）

| # | 事项 | 选项 / 建议 | 状态 |
|---|---|---|---|
| B-7 | O-1 参考模式内置层号表（`_pcell_core.py` 的 `metal_layer` / `metal_pin_layer` / `via_layer` 与显示名回退）与私有工艺的金属 / 过孔 / pin 层号相同，随包发布 | 建议：参考模式的层号表也从 profile 读取（N65 profile 在仓库外，`ind_ref.gds` 逐字节比对只在有它时跑）；否则改成明显虚构的层号表。黄金 GDS 与库回放不受影响 | **待拍板** |
| B-8 | O-2 参考模式过孔常数（`VIA_CUT_UM` / `VIA_SPACE_UM` / `VIA_ENC_UM`、`REFERENCE_MIN_MET_SPACING_UM`）等于私有 N65 profile 的值 | 与 B-7 同一方案：随 profile 读取 | **待拍板** |
| B-9 | O-3 公开历史：origin/main 自 2026-09-22 起的提交仍含本次修掉的内容（v0.2.0 标签本身干净；私有 profile 与 `.proc` 从未入库） | (a) 接受现状，只保证 0.3.0 起干净；(b) `git filter-repo --replace-text` 改写历史并强制推送（词表放仓库外，改写后复查全部历史 blob）；(c) 先转私有再定。建议 a，除非层号级别的信息也必须从历史清除 | **待拍板** |
| B-10 | O-4 N65 不在本次范围：一处测试注释含 N65 顶层金属最小线距；B-8 的常数 | 若 N65 同受此规则约束，以 N65 profile 为词表再审一遍（小） | **待拍板** |
| B-11 | O-5 demo_6m 与 N28 的巧合（一个绘图层号、两组线宽 / 线距数值相同） | 建议不动（通用量级的巧合）；若要求零重合则改 demo_6m 与 skills 里的副本、重生黄金 GDS、升 `GEOMETRY_VERSION` | **待拍板** |
| B-12 | T16.1c 真实加密批（需 `--plan` 批准）：建议先做 xfm_bs_ap 一张表——`lib.densify` 在同心到轻偏心子域（`bounds={"center_spacing_um": {"min": 0, "max": 16}}`，score=ceiling）的 60 个选点 + 10 个同一子域内随机（与 σ 无关）的独立测试点，共 70 次 EMX（约 1 h，8 线程 × 4 路）；回流 60 点后用 10 个测试点比较回流前后 Lp@40 / k@40 / SRF 误差；旧的 10 个格点间点作为次要对照。发现：全域与同心区的选点都主要落在 OD_P > 120 µm 的格点之间（那里格距 30 µm、谐振更近 40 GHz），并不特别靠近旧的 10 个测试点，所以不用它们做主要测试集 | T16.2a 已出结论：先落地 T16.2b（不花 EMX），再跑此批；研究还指出采样空白正是"偏心 + 两外径不等"，全域 / 轻偏心的选点会自然补到那里。建议：等 T16.2b 合入后按上述方案批准 | 已做（2026-09-25 14:03–14:24）：70/70 EMX 成功；60 选点回流 xfm_bs_ap 1581→1641 行；10 个独立测试点回流前→后：中位误差 9 列全降或持平（Lp@40 0.9%→0.4%、k@40 1.5%→1.0%），k@40/Qp@40/Qs@40/L_lf 最大误差明显下降，但偏移最大的第 6 点 Lp@40/SRF 变差（+0.5%→+11.7%、+2.6%→−10.9%，区间仍覆盖）；60 选点处回流前 k@40/SRF 区间覆盖仅 15%/17%——未采样区域的误差是系统性的，留出校准估不出来。页 `reports/library_query/XFM_DENSIFY_B12_CN.html`。下一步（N-17）：偏移 8–24 µm 一带再补一轮；域守卫加距离项 |

## 2. T16 候选（已批准，按顺序；方案见 `T16_PLAN_CN.md`）

| # | 事项 | 来源 | 大小 |
|---|---|---|---|
| R-13 | 过孔包围审计只认 "RV"：profile 标记要审计的过孔 | 审查 13 | 小，已做（511a937） |
| R-14 | 配置校验器用 AP=11 编号：在 `use_stack(profile)` 内按金属栈解析 | 审查 14 | 中，已做（e98724f） |
| R-15 | 第三方生成器过产品级 DRC 门：生成器声明期望导体 | 审查 15 | 中，已做（6227c6d） |
| R-17 | Spectre license 排队等待 900 s 写死 → `simulator.license_queue_timeout_s` | 审查 17 | 小，已做（e5dc8a7） |
| R-18 | doctor 对纯 EM spec 仍要求 `spectre` / `ocean`；环境钩子只支持 csh 文件（`license_probe` 已入 site.yaml） | 审查 18 | 中，已做（c66778b） |
| R-20 | 库缓存目录可指定，库根只读时回退 `~/.cache/ic-opt/<key>` | 审查 20 | 小，已做（8800ca5） |
| R-21 | σ/μ 置信上限按结果列可配（manifest） | 审查 21 | 小，已做（3eeedbc） |
| R-22 | `lib.region` 的 `relax=` 参数 | 审查 22 | 小，已做（b1ee958） |
| R-23 | 5 nm 制造网格入 profile 字段 | 审查 23 | 小，已做（f0b0c05） |
| R-24 | 非均匀频率表的频率列取值（局部间距或插值） | 审查 24 | 中，已做（dfa8db5） |
| R-25 | 四端口默认极性说明 + k_lf < 0 告警 | 审查 25 | 小，已做（1510fa4） |
| R-26 | 校验冒烟线宽夹到 profile 范围 | 审查 26 | 小，已做（7c6e4bd） |
| N-1 | `OPENBLAS_NUM_THREADS` / `MKL_NUM_THREADS` 也视为显式上限 | T15.3 问题 3 | 小，已做（f300796） |
| N-2 | 超时不释放资源：任务成进程组 / 会话，整组杀 | 审查开放问题 4 | 中，已做（85fcb4b） |
| N-3 | `em.validate_profile proc=` 经 `--ssh-profile` 读远端 `.proc` | 审查开放问题 6 | 中，已做（ca1814f） |
| N-4 | 跨进程同时校准同一模型的竞争（同一台机器两个进程） | T15.3 问题 5 | 小，已做（7210790） |
| N-5 | ADR-0001 隔离冒烟脚本把 `~/.ic-opt/site.yaml` 绑进沙箱 | T15.7 问题 2 | 小（需能跑 bwrap），已做（964dcd0） |
| N-6 | pcell 注释里 em-opt 时代的指针（`geometry/…`、`.scratch/…`）清理 | T15.7 问题 5 | 小，已做（a7b9cdd） |
| N-7 | `lib.region` 输出回显 `k`（页面按默认 2σ 措辞） | T14.3 问题 2 | 小，已做（a9c16dd） |
| N-8 | 报告脚本 `docs/refactor/reports/**` 的 11 条 ruff 提示（不在 `ruff check src tests` 范围） | T15.7 | 小，已做（9c8795a） |
| N-9 | Windows / macOS 控制端真机验证（锁、spawn、scp 盘符路径；msvcrt 分支只有打桩测试） | T15.4 | 需机器 |
| N-10 | stage 里抛出的 `CommandTimeout` 不会变成该点的 `failed:<stage>`，一个任务超时会中止整个 `sim.evaluate` | T16.4 发现 | 小，已做（0beb0f8） |
| N-11 | Ctrl-C 之后 engine 的线程池仍把排队中的点跑完 | T16.4 发现 | 小，已做（9a009af） |
| N-12 | `ic-opt migrate` 未为 0.1 工程写回 `license_queue_timeout_s: 900`（0.1 总是传 `+lqtimeout 900`）：按 T15 的做法显式写出旧行为 | T16.4 发现 | 小，已做（176162e） |
| N-13 | T13.11 遗留的不一致：配置名字检查把 "5" 读成名叫 M5 的金属，pcell 的 `stack.index("5")` 读成第 5 层金属；现有 profile 两者一致，若某 profile 的 M<n> 名字不在第 n 位（如底层叫 LI）就会分歧。修法：`stack.position_in` 优先按名字 M<n> | T16.3 发现 | 小，已做（2776ab8：生成器改传整数位置，黄金 GDS 与三套基线回放 0 差异） |
| N-14 | 报告脚本里 `d4_grid.py`、`d4_probe.py`、`wps_locate.py` 与 `demo_families.py` 的 5/6 个配置仍用 M2.2 退役的字段名，今天的生成器会拒绝（N-8 只修 ruff，未更新） | T16.7 发现 | 已做（2026-09-25，用户批准）：四个脚本改为 M2.2 字段名（`metal`、primary_* / secondary_*、`port_gap_*`），`d4_grid.py` 用法变为 `PROFILE METAL OUT.json`；四个脚本在 demo_6m 上各跑一遍全部生成，ruff 干净 |
| N-15 | N-5 的隔离冒烟脚本已绑入 site.yaml，但完整远程运行（实验室主机 + Spectre）未验证；ADR 已注明 | T16.7 | **Windows 已通过**（2026-09-27 第 2 次重跑，Windows 10.0.26200 / Python 3.11.9 / OpenSSH 9.5p2，修复版 main@35ff42b）：远程 doctor 全 ok，2 点 × 3 testbench 在服务器上跑完（97 s），比对 PASS 且 40 个记录值与服务器基线逐位相同（Claude 在服务器侧独立复核），尾点文件在 deck 与每个子任务 netlist 中原名保留，控制端无 psf。过程：首跑暴露 N-21 → 修复 → 第 1 次重跑因方案用 `Get-FileHash` 停在第 0 步 → 改用 `n15_tools.py` → 通过。证据：服务器 `ic-opt-accept/n15/reports/`（三个 zip）与 NAS `reports/windows/rerun_report/`。**macOS 未测** |
| N-16 | 多圈变压器表（xfm_ms）按研究建议用 `model: ratio`（Ls@60 整档留出 p90 23.8/23.5%→7.0/8.8%），需先用库自身模型做一次对照再改真实 manifest | T16.2b | 已做（2026-09-25，用户理解原理后批准）：`xfm_ms_ratio_acceptance.py` 用库自身实现在临时副本上做 57 个整档留出（OD_P / OD_S 各档 × 直接 / 只换坐标 / 比值+坐标）+ 4 套完整库校准：Ls@60 整档留出 p90 ap 41.2%/20.9%→8.5%/6.6%、m10 27.4%/21.2%→13.5%/7.4%，收益主要来自无量纲坐标，比值再改善一点；真实 manifest 的 xfm_ms Lp/Ls 已改 `model: ratio, feature_map: xfm_ms_dimensionless`（备份 `library.yaml.bak_20260925_1815`）；页 `XFM_MS_RATIO_ACCEPTANCE_CN.html`；两表 `xfm_query_verify.py` 重跑并更新验证页 |
| N-17 | B-12 后续：在中心偏移 8–24 µm、外径比 0.8–1.25 一带再补一轮（约 60 点，需 `--plan` 批准）；xfm_bs_m10 同样处理；未采样区域的校准盲区——候选把"离最近实测行的缩放距离"计入不确定度 | B-12 | 已做（2026-09-25 17:00–18:17）：ap 与 m10 各 60 选点 + 10 测试点，140/140 EMX 成功，两表回流（1641→1701、1581→1641）。10 个独立测试点回流前→后：Lp@40 中位 ap 6.4%→4.2%（最大 34.9%→9.0%）、m10 7.5%→3.6%（最大 59.2%→15.4%）；SRF 中位 9.7%→1.9% / 8.9%→2.4%；k_lf 3.7%→0.6% / 7.4%→1.0%。选点处回流前覆盖 91% / 94%（B-12 那批 15%/17%）。未吃透的角落：外径比 1.1–1.25 × 线宽 8–10 µm × 偏移 15–20 µm（两表各只落 3 / 2 个选点，第 3 点 Lp@40 变差到 +9% / +15%）；Q@40 两列整带 10–30% 误差不受补点影响（新 N-19）；SRF 在选点处覆盖 17% / 30%，校准盲区候选（距离项）仍开放。页 `reports/library_query/XFM_DENSIFY_N17_CN.html` |
| N-18 | 2026-09-25 一次 `uv run` 在仓库里当场生成不含 vendor/open-box 的 uv.lock 并把 .venv 同步成 numpy 2.x，`import ic_opt` 失败 | 按 CONTRIBUTING 的 `uv pip install -e ".[dev,em,turbo,report]" -e vendor/open-box -e vendor/TuRBO` 恢复（numpy 1.26.4 / scipy 1.12.0 / scikit-learn 1.3.2），定向测试 69 绿，uv.lock 已删 | 已关闭：操作失误（`uv run` / `uv sync` 不是本仓库的安装法），安装法不改；规则记入 Claude 记忆 |
| N-19 | 单圈变压器表 Q@40 / Qs@40 两列在偏移带的误差 10–30%，B-12 / N-17 两轮补点都没改善（谐振附近 Q 陡，Q 列仍按原始尺寸直接建模） | 候选：给 Qp / Qs 曲线列加 `feature_map: xfm_bs_dimensionless`，或按 Qp_peak × 比值建模（`model: ratio` 需先允许 Q 列）；先用 T16.2a 的对照脚本评估 | 方案 1 已验收、**未采用**（2026-09-25 21:50，`xfm_bs_q_map_acceptance.py`，80 折整档留出 + 30 个从未回流的独立设计）：换坐标让整档留出 p90 从 30–45% 降到 6–19%，但对 N-19 要解决的偏移带没有帮助——N-17 的 10 个点上 Qp@40 中位 11.9%→13.3%、区间覆盖 90%→40%，Qs@40 8.6%→11.5%、90%→60%（更自信但不更准）。页 `XFM_BS_Q_MAP_ACCEPTANCE_CN.html`。余下两条路：方案 2（Q@f = Qp_peak × 比值，改 manifest 校验与组合模型，约半天）；方案 3（在角落 外径比 1.1–1.25 × 线宽 8–10 µm × 偏移 15–20 µm 定向补 20–30 点，约 15 分钟 EMX，需批准）。**已做**（2026-09-26，用户批准方案 2、3 并行）：方案 3 两表各 25 个角落选点（OD_S/OD_P 1.10–1.25 × W_S 8–10 µm × CS 14–22 µm）50/50 EMX 回流（1701→1726、1641→1666），ap 的 N-17 测试点 Qp@40 中位 17.6%→3.6%、Lp@40 4.2%→1.7%，m10 电感 / SRF 改善、Q 持平；补点前 Q 模型在角落覆盖 0%（错得很自信）。方案 2 代码 56d8f3e（Qp/Qs 可 `model: ratio` 以 Q 峰值为底 + 留出校准 ≤0 修正），验收 `xfm_bs_q_ratio_acceptance.py`（160 折 + 30 独立设计 + 8 套校准，81 分钟）：偏移带 Qs@40 最大 23%→14%、覆盖 80%→100%，m10 Qp@40 最大 33%→15%、覆盖 88%→100%，k_scale 1.00；档间插值只小幅改善（坐标之事，留待）。真实 manifest 的 xfm_bs Qp/Qs 已改 `model: ratio`（备份 `library.yaml.bak_20260926_0135`）。页 `XFM_DENSIFY_CORNER_CN.html`、`XFM_BS_Q_RATIO_ACCEPTANCE_CN.html` |
| N-20 | `xfm_query_verify.py` 整表体检曾串行单进程：多圈表两张 6.6 h（23 列 × 5 折 + k 列两种坐标对照 + SRF / 守卫 / 反向 / 示例；k_lf 全表联合拟合一组 15 min；只给了 4 线程） | 用户规则（2026-09-26）：库计算类任务一律并行，预算 32 线程（128 / 256 GB 是仿真器的上限，与此无关）；不讨论"机器空闲与否" | 已做：脚本改为进程池（默认 16 进程 × 2 线程），步骤 2–5 按表并行，加 `--columns`（只验指定列）与 `--steps`；结果与串行一致（seeded）；报告脚本的对照图仍重拟四列，未改 |
| N-21 | **Windows 控制端无法导入网表**：Cadence Maestro 导出目录固有文件 `amap/__dspf_information__.`（文件名以点结尾，本机每个导出都有），Windows 普通路径会去掉尾点，`shutil.copytree` / `rmtree` / `open` 全部报"文件不存在"；受影响：`Deck.save`（decks 落盘）、`netlist.import` 的 `.staging` 清理、`render_netlist` 复制 bundle 到工作目录、`SshExecutor._put_tree` 本地打包（Windows tar 未验证） | N-15 Windows 验收发现（2026-09-26） | **已修并经 Windows 真机确认**（11b3482；2026-09-27 N-15 Windows 重跑通过） |
| N-22 | 只有器件、没有 testbench 的 spec（em_only 流水线）经 optimize / signoff / coarse_to_fine / fix_run 运行时在 `netlist.import` 崩溃：`Deck.save` 没有模板时不建目录，写 `source.txt` 报 FileNotFoundError | 审核 N-21 修复时发现（2026-09-26） | 已修（`Deck.save` 先建目录；`import_netlists` 先建 `.staging`；两个测试） |
| N-23 | 完整远程控制端验收（用户 2026-09-27：N-15 验证太少，不算完整的电路优化，也没有验证 EM）：A 混频器 `coarse_to_fine`（OpenBox 12 + TuRBO 12，续跑 +4）；B EM 链（LO_XFMR_TB 两个 xfm_bs，笔记本上 pcell → 服务器 EMX → bind_nport → Spectre，OpenBox 12 点，含器件量测）；全套源码安装（OpenBox / TuRBO / klayout / torch） | 用户拍板：N28 profile 可拷到笔记本、验收后删除；EMX 上限 80 次（4 线程、8 GB、≤4 并行、simfreq 0）；真正的"电路 + 器件"联合优化等用户在 Virtuoso 做带 nport 的混频器测试平台（现有平台都不满足：LO_XFMR_TB 无电路变量，混频器用 ideal_balun，112G 基带 / 28G IF 的 nport 为整块电路 EM 且 sNp 路径不在本机） | 准备完毕：服务器参考（A 28 点完整优化 + 续跑；B 2 点冒烟，4 次 EMX）；包 `/4027_NAS/reports/windows_n23/`（源码 zip main@5e63716、A spec、`n23_tools.py`、方案、prompt；B spec 与私有 profile 只经 scp）；服务器复核 `ic-opt-accept/n23/n23_verify.py`（A 28 点重仿逐位；B 分两遍：先用控制端 EMX 缓存重算全部点，要求缓存全命中、GDS 逐字节相同，sNp 与控制端相同的点直接逐位比对；因同几何并发而用了另一份 sNp 的点（见 N-24），再单独用它自己那份 sNp 重算并逐位比对；A、B 并行，合计 76 线程；已用假控制端包端到端自检，含 Windows 式缓存目录名）。发现：EMX 同输入重复运行 sNp 有约 1e-5 差异（不可逐位复现），故 B 以缓存方式复核；混频器 43% 的点因 BW 指标在 pac 扫描内无 −3 dB 点返回空值而 failed:extract（指标定义特性，优化器按 1e6 惩罚正确处理）。等 Windows 报告 |
| N-24 | 同一批里两个点共用某个器件的几何时，两个点都会跑一次 EMX：点级缓存没有"进行中"去重，`_run_cached` 只在写入时保留先写入的一份。后果一：多花 EMX 次数并计入预算；后果二：后写入的点用的是自己那份 sNp，缓存里留的却是另一份，之后重放这个点会命中缓存、拿到另一份 sNp，指标差约 1e-5（EMX 本身不可逐位复现） | N-23 服务器冒烟与复核脚本自检发现（2026-09-27）：B 冒烟的 2 个点共用 xfmr_in，xfmr_in 跑了两次 EMX，复核时这个点的指标差约 1e-5 | 候选，待拍板：同一项目内按指纹加"进行中"锁，后到的点等先到的点写完缓存后直接读缓存，每个指纹只跑一次，所有点都用缓存里那一份 sNp；N-23 的复核脚本已绕开这个问题（按点用各自的 sNp） |
| N-25 | **Windows 控制端 `turbo` 策略无法导入 torch**：`import torch` 报 `WinError 1114`（c10.dll 初始化失败）。原因：scikit-learn < 1.4（OpenBox 的约束）的 Windows 安装包自带 msvcp140.dll 14.32，导入时按绝对路径强制加载；同一进程里之后按名字依赖 msvcp140.dll 的 DLL 都拿到这一份。torch 2.9 起用 MSVC 14.42 编译，std::mutex 改为编译期构造（c10.dll / torch_cpu.dll 不再导入 `_Mtx_init_in_situ`，只调 `_Mtx_lock`），在 14.32 上初始化即崩溃；torch ≤ 2.8 用 ≤ 14.38 编译，仍导入初始化函数。ic-opt 启动就导入查询库（scikit-learn），所以 Windows 上 TuRBO 与 `latin_hypercube` 必然失败，更新系统 VC++ 运行库也无效 | N-23 Windows 第 1 次（2026-09-27）第 1 步环境检查发现；服务器上逐个解析 Windows 安装包核实：笔记本装的 66 个包里只有 torch 2.14 同时满足"≥ 14.40 编译 + 经共享 msvcp140 调用互斥锁"；scikit-learn 各版都预加载自带 msvcp140（≤ 1.5.0 为 14.32，1.6.1 为 14.42，1.7.2 为 14.44） | 已修：`turbo` 附加依赖在 Windows 上限定 torch < 2.9（其他平台不变），README 平台一节加说明，打包测试加防回退检查；uv 按 Windows 解析得 torch 2.8.0、按 Linux 仍为 2.14.0。等 Windows 真机确认。以后放开 scikit-learn 约束时一并移动上限（其自带 msvcp140 须不低于 torch 的编译器版本） |

## 3. 记录（不打算单独立项）

- 内存模型对大器件低估（最大 1.18×）：建库脚本已按 1.2× 预留。
- ms 表 Q 容量下限的负结果、xfm 限带 parity 开放——随 B-1 一并考虑。
- 电感表 60 GHz 列的 2σ 覆盖最低 0.85（AP `L@60`、M10 `Q@60`，`ind_query_verify.json`）：在 0.85–0.96 带内，只观察。
- `em-opt` 老仓库（EM-opt-workflow）的遗留决策（D4 落点悬空 / D5 地环翻转、真实 CT 验证、分代 CV 门）：该工作区只读、不再开发；如需迁移到本仓库另行立项。
- 变压器两表回流后（B-2）40 GHz / 60 GHz 区域答案会随数据变化，`region_acceptance_40g*.json` 是回流前的验收记录，不是门。

## 4. 已收官（本文件不再跟踪）

T13 查询库嵌入（含 T13.6 真实复核与回流、T13.7 变压器四表验证、T13.9–11 工艺接入）、T14 `lib.region`、ms 高频加密批（2010 点，60 GHz 可用行 82→755/658）、T15 去环境假设（含 N28 库迁移）。
记录分别在 `T13_LIBRARY_MODULE_PLAN_CN.md`、`T14_LIBRARY_REGION_PLAN_CN.md`、`T15_NO_DEFAULT_RESOURCES_PLAN_CN.md` §8、`EXECUTION_PLAN_CN.md`；报告页对照表在 `reports/INDEX_CN.md`。
