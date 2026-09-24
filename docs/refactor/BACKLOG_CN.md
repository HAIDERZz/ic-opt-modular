# 待办总表（阶段性收尾，2026-09-25）

一张表管全部未完成事项：来源（审查行号 / 方案节 / 记忆）、现状、下一步、谁来定。
状态含义：**待拍板** = 需要用户决定；**可做** = 已批准或无需决定，等排期；**记录** = 只需知道，不打算单独做。
编号稳定，后续引用 W-x / B-x / R-x / N-x。新任务从这里领取，完成后在 `EXECUTION_PLAN_CN.md` 追加一条并在此划掉。

## 0. 收尾动作

| # | 事项 | 状态 |
|---|---|---|
| W-1 | `git push origin main`（main 领先 origin 33 提交；只推 `origin`，不推 `github` 远程） | **用户执行**（auto 模式分类器不允许我推） |
| W-2 | 版本号：T15 是破坏性变更（site.yaml v2、spec 资源字段必填、指纹与代际迁移），`pyproject` 仍是 0.2.0。建议升 **0.3.0**，写 `RELEASE_NOTES_v0.3.0.md`（升级步骤：写 site.yaml、补 spec 资源字段、`ic-opt migrate-store`），与 B-4 一起处理 | **待拍板**（是否发版、版本号） |
| W-3 | `.gitignore` 加 `.claude/worktrees/`；删除未跟踪的全量加密网格 `reports/pcell_plan/xfm_grid_n28_densify.json`（4318 点"all"档，从未运行；实际跑的 sixty_cells 档已提交） | 已做（本次提交） |
| W-4 | `EXECUTION_PLAN_CN.md` 补 T15 条目与收尾条目；审查文档状态头修正（行 16、19 已在 T15.1 处理）；新建 `reports/INDEX_CN.md`（每个报告页 → 生成脚本 → 数据 → artifact 链接） | 已做（本次提交） |
| W-5 | N28 库迁移备份 `<库根>/<部件>/.icopt/observations.jsonl.bak-20260924T1534*`（8 个部件）：验收已过，保留一周后删除 | 可做（2026-10-02 后） |
| W-6 | 记忆整理：T13–T15 过程记忆压缩为"现状 + 教训 + 本文件指针" | 已做 |
| W-7 | `reports/library_query/IND_QUERY_VERIFY_CN.html` 仍是 09-23 版（1038 行），其数据 `ind_query_verify.json` 已在 09-24 按回流后 1048 行重做基线：用 `ind_query_report.py DATASET_JSON VERIFY_JSON OUT_HTML` 重生成并重发 artifact（脚本会重拟合四个结果列的模型画对照图，约几分钟） | 可做 |

## 1. 待用户拍板

| # | 事项 | 背景 | 选项 / 建议 |
|---|---|---|---|
| B-1 | 变压器格点间模型不准的**普遍解法** | 真实复核（`XFM_SIGNOFF_N28_CN.html`）：格点间 Lp@40 偏 +1.5…+22%、k 到 +9%、SRF 到 −18%，区间诚实（覆盖 96–98%）但宽；根因是 bs 表次级外径 20 µm 一档太稀，锚定量在谐振附近变化快 | (a) `lib.densify`：按模型不确定度在全域自适应补点（与具体目标无关）；(b) 建模改为 L@f = L_lf × 谐振因子，两者各自平滑；(c) 建库策略把次级外径改为连续采样。建议 a + b 立项 T16 |
| B-2 | 10 个格点间变压器实测点是否回流入库 | 真实数据，正落在稀疏处；`adopt=true` 几秒，复用观测不跑 EMX | 建议回流（与 B-1 一起看）。注意：adopt 再跑会重复入库 |
| B-3 | TuRBO 长期方案 | 现按 b（`-e vendor/TuRBO`），仍是 Uber 非商业许可；是 `lib_design` / `lib_signoff` / `coarse_to_fine` 细阶段的默认策略 | 面向商业用户则 (c) 换 MIT 实现或自写 TuRBO-1（需基准对比）或 (d) 默认策略改 OpenBox；否则维持 b |
| B-4 | 公开发布前的 N28 脱敏 | v0.2.0 已流出含 N28 数值需重发（记忆）；T15.7 之后 pcell README 与 `_pcell_demo` 仍有 N28 层号（74/126/85/138–140）、演示输入 `n28_1p10m`；库与页面在仓库外 | 建议：发 0.3.0 前对随包 / 随仓库内容做一次脱敏审计 |
| B-5 | v0.2.0 时代工程 store 的指纹兼容 | T9 之前 schema 不同，新旧指纹公式都对不上：旧观测不复用、只计 budget（README 已说明） | (a) `migrate-store` 增加"按 0.2.0 公式"匹配；(b) 接受现状。建议 a（小改） |
| B-6 | T16 范围与顺序 | 第 2 节 | 按第 2 节建议顺序，或指定子集 |

## 2. T16 候选（可做，按建议顺序）

| # | 事项 | 来源 | 大小 |
|---|---|---|---|
| R-13 | 过孔包围审计只认 "RV"：profile 标记要审计的过孔 | 审查 13 | 小 |
| R-14 | 配置校验器用 AP=11 编号：在 `use_stack(profile)` 内按金属栈解析 | 审查 14 | 中 |
| R-15 | 第三方生成器过产品级 DRC 门：生成器声明期望导体 | 审查 15 | 中 |
| R-17 | Spectre license 排队等待 900 s 写死 → `simulator.license_queue_timeout_s` | 审查 17 | 小 |
| R-18 | doctor 对纯 EM spec 仍要求 `spectre` / `ocean`；环境钩子只支持 csh 文件（`license_probe` 已入 site.yaml） | 审查 18 | 中 |
| R-20 | 库缓存目录可指定，库根只读时回退 `~/.cache/ic-opt/<key>` | 审查 20 | 小 |
| R-21 | σ/μ 置信上限按结果列可配（manifest） | 审查 21 | 小 |
| R-22 | `lib.region` 的 `relax=` 参数 | 审查 22 | 小 |
| R-23 | 5 nm 制造网格入 profile 字段 | 审查 23 | 小 |
| R-24 | 非均匀频率表的频率列取值（局部间距或插值） | 审查 24 | 中 |
| R-25 | 四端口默认极性说明 + k_lf < 0 告警 | 审查 25 | 小 |
| R-26 | 校验冒烟线宽夹到 profile 范围 | 审查 26 | 小 |
| N-1 | `OPENBLAS_NUM_THREADS` / `MKL_NUM_THREADS` 也视为显式上限 | T15.3 问题 3 | 小 |
| N-2 | 超时不释放资源：任务成进程组 / 会话，整组杀 | 审查开放问题 4 | 中 |
| N-3 | `em.validate_profile proc=` 经 `--ssh-profile` 读远端 `.proc` | 审查开放问题 6 | 中 |
| N-4 | 跨进程同时校准同一模型的竞争（同一台机器两个进程） | T15.3 问题 5 | 小 |
| N-5 | ADR-0001 隔离冒烟脚本把 `~/.ic-opt/site.yaml` 绑进沙箱 | T15.7 问题 2 | 小（需能跑 bwrap） |
| N-6 | pcell 注释里 em-opt 时代的指针（`geometry/…`、`.scratch/…`）清理 | T15.7 问题 5 | 小 |
| N-7 | `lib.region` 输出回显 `k`（页面按默认 2σ 措辞） | T14.3 问题 2 | 小 |
| N-8 | 报告脚本 `docs/refactor/reports/**` 的 11 条 ruff 提示（不在 `ruff check src tests` 范围） | T15.7 | 小 |
| N-9 | Windows / macOS 控制端真机验证（锁、spawn、scp 盘符路径；msvcrt 分支只有打桩测试） | T15.4 | 需机器 |

## 3. 记录（不打算单独立项）

- 内存模型对大器件低估（最大 1.18×）：建库脚本已按 1.2× 预留。
- ms 表 Q 容量下限的负结果、xfm 限带 parity 开放——随 B-1 一并考虑。
- 电感表 60 GHz 列的 2σ 覆盖最低 0.85（AP `L@60`、M10 `Q@60`，`ind_query_verify.json`）：在 0.85–0.96 带内，只观察。
- `em-opt` 老仓库（EM-opt-workflow）的遗留决策（D4 落点悬空 / D5 地环翻转、真实 CT 验证、分代 CV 门）：该工作区只读、不再开发；如需迁移到本仓库另行立项。

## 4. 已收官（本文件不再跟踪）

T13 查询库嵌入（含 T13.6 真实复核与回流、T13.7 变压器四表验证、T13.9–11 工艺接入）、T14 `lib.region`、ms 高频加密批（2010 点，60 GHz 可用行 82→755/658）、T15 去环境假设（含 N28 库迁移）。
记录分别在 `T13_LIBRARY_MODULE_PLAN_CN.md`、`T14_LIBRARY_REGION_PLAN_CN.md`、`T15_NO_DEFAULT_RESOURCES_PLAN_CN.md` §8、`EXECUTION_PLAN_CN.md`；报告页对照表在 `reports/INDEX_CN.md`。
