# 现有报告与图表清单（决策 ⑦）

**决定（2026-09-22）**：保留 A 类 insight 里的 Best observed、Top feasible candidates（前 5）、Constraint margins、
参数重要度（SHAP）、多 corner 三节（策略 / 推荐点各 corner 指标 / 各 corner 失败数）、Space Compression Advisory；
B 类图保留前四张（feasible_convergence、constraint_margins、bottleneck_weighted_score、convergence）。其余全部删除。
落地规格与三处必修见 `REFACTOR_PLAN_CN.md` 2.5 节。以下为决策时的盘点原文。

样本来源（真实远程运行，磁盘上现存）：

- 主样本：`<recorded-run 802f8b44>/reports/` —— OpenBox `openbox_auto`（gp+eic），80 点，13 可行 / 51 约束失败 / 16 指标失败，推荐 real_066
- v0.1.10 验收批次：`<recorded-run second_batch_20260810>/reports/` —— 100 点，3 testbench × 3 corner，全部不可行（48 约束失败 + 52 指标失败），推荐 none
- 多 testbench 有可行解：`<recorded-run muti_tb_fix>/reports/` —— 150 点，3 可行，推荐 real_126（旧版，图为 SVG）

一次 `ic-opt --real` 在 `reports/` 下留下 **26 个文件 + 2 个目录**（主样本），分四类。

## A. 结论类（面向人）

| 文件 | 内容 | 备注 |
| --- | --- | --- |
| `optimizer_decision_report.md` / `.json` | 推荐 run、动作（accept_best_observed_or_continue / adjust_constraints_or_continue）、依据、置信度、推荐点的参数与指标、多 corner 时的 worst/selected corner、瓶颈分、证据计数、下一步、边界声明（不声称全局最优）、警告 | 1.3–1.5 KB，一屏读完；skill 让 agent 读它给建议 |
| `optimizer_insight_report.html` | 8 个区块：Best observed / Objective and feasibility / Report-layer Pareto / Space Compression Advisory / History Warm-start / OpenBox Advanced Visualization / Plot artifacts / Artifact index；只嵌 1 张图 | 13 KB；skill 说「先看它定向」，但内容是 md 的子集 |
| `optimizer_insight_report.md` | 20 个小节（见下）+ 7 张图链接 | 7–11 KB |
| `optimizer_insight_report.json` | 同上的机器版，另含 `observed_relationships` 全量、`plots` 路径、`html_generation` | 139–236 KB |
| `openbox_advanced_visualization/.../*.html` + `visualization_data_*.json` + `openbox_advanced_visualization_manifest.json` | OpenBox 自带可视化：objective/constraint history、surrogate fit verification、parameter importance（SHAP） | 仅 OpenBox + advanced 依赖；TuRBO 无 |

`optimizer_insight_report.md` 的 20 个小节：

1. 头部：status、评估数、状态计数
2. **Best observed**：run、objective、参数
3. IC-native Summary：最佳可行 run、可行数、最佳可行 objective（与 2 重复）
4. **All evaluable FoM**：按配置目标对**所有可算的点**（含不可行）排序的最佳 + 图
5. Configured Objective Ranking：同 4 的前 5 名列表（含 status、参数、指标）
6. Normalized Margin Bottleneck Plot：瓶颈分最佳 run + 图
7. Process Corner Summary：约束/目标策略、selected/worst corner
8. Best Candidate Corner Metrics：推荐点各 corner 指标
9. Corner Failure Distribution：各 corner 失败计数
10. Optimizer effectiveness audit：请求/解析到的策略、代理模型、采集函数、初始点数、replay 数、最近一批的 history 增长与可行数
11. **Top feasible candidates**：前 5 名可行点（objective、参数、指标）
12. **Constraint margins**：每条约束的 limit、最好/最差裕量（及 run）、通过率 + 图
13. OpenBox parameter importance：SHAP，目标 + 每个指标的变量重要度百分比
14. Trustworthy Trade-Off Summary（样本里为 not_available）
15. History Reuse Summary（热启动时才有内容）
16. Report-layer Pareto Trade-Off：用原始指标算的 Pareto 前沿（样本：64 点里 63 个在前沿——五维指标下几乎人人都在前沿，信息量为零）
17. Space Compression Advisory：OpenBox compressor 干跑建议的变量范围（advisory only，不回写）
18. Plots：7 张图索引
19. Observed Relationships：每个变量 × 每个指标 + 目标的 Pearson 相关（n=64）
20. Advanced surrogate visualization：指向 OpenBox HTML

## B. 七张图（`reports/optimizer_visuals/*.png`，旧版为 `.svg`）

| 图 | 内容 | 看图后的观察（主样本） |
| --- | --- | --- |
| `convergence.png` | 全部点的 objective 折线 + best-so-far 阶梯，按状态着色 | 失败点带 1e6 惩罚值，全部被裁掉（角标「67 outlier/penalty values clipped」），实际只剩 13 个可行点，与下一张信息完全相同；best-so-far 线从图顶垂直掉下来，误导 |
| `feasible_convergence.png` | 只画可行点 + best feasible so far | 干净、有用 |
| `constraint_margins.png` | 每条约束一个子图，归一化裕量随评估序号，正=通过 | 有用；但 IIP3 子图 y 轴到 1e31——归一化除以 `max(abs(limit), 1e-30)`（`optimizer_insights.py:1001`），limit=0 dBm 时失效 |
| `bottleneck_weighted_score.png` | x=加权和分数，y=最小归一化裕量（瓶颈分），等分线，标出 best | 概念可用；分数模型来自解析目标表达式，解析失败时回退到**写死的 Mixer 阈值**（19 GHz / 4 dB / 12 dB / 0 dBm / −2 dBm，`optimizer_insights.py:1308-1333`）——工具在这里不是通用的 |
| `all_evaluable_fom.png` | 所有可算点的 FoM 折线，标出 best | 标出的 best（real_057）是约束失败点，决策报告随后不得不警告「已忽略」；对使用者是噪音 |
| `parameter_objective_scatter.png` | 每个变量一个子图：参数 vs objective | 同样只剩可行点（其余被裁），80 点 4 变量下每格十几个点，信息很少 |
| `status_distribution.png` | 三种状态计数柱状图 | 三个数字，md 头部一行已有 |

## C. 过程 / 证据类（agent 被要求读，人基本不看）

| 文件 | 内容 |
| --- | --- |
| `optimizer_flow_run_report.json` | 17 步各自 pass/fail + detail、backend、预算参数、推荐 run/动作、`user_decision_required`；skill 称之为「最终成功标记」 |
| `optimizer_run_acceptance_report.json` | 验收：评估数、result/metric manifest 计数、状态计数、设置、best、issues |
| `optimizer_completion_report.json` | 完成度：decision、confidence、`global_optimum_claim`、搜索空间覆盖、improvement、continuation 摘要 |
| `optimizer_finalize_report.json` | 把 acceptance / completion / insight 三个状态汇总成一个 |
| `optimizer_run_report.json`（OpenBox）/ `native_turbo_optimizer_report.json`（TuRBO） | 后端运行报告：settings、batch summary、best、线程限制审计、evaluations 内嵌 |
| `optimizer_effectiveness_audit.json` | 每批：策略/代理/采集函数解析结果、初始化、replay 计数、线程限制 |
| `supervisor_instruction.json`（项目根） | 审批门记录：decision、reason、allowed/forbidden actions、config 哈希 |
| `optimizer_final_summary.md/.json`、`optimizer_supervisor_decision.md/.json` | 仅内部 CLI 生成；三个样本目录里都没有——产品流程不产出 |

## D. 闸门 / 每候选点检查（每个候选点覆盖一次，只剩最后一个点的）

`ic_opt_doctor_report.json`（22 KB：checks、license 探测、资源摘要、评估矩阵、dirty state、进度摘要）、`license_probe_report.json`、`project_readiness_report.json`、`requirement_intake_report.json`、`netlist_preparation_report.json`、`dry_run_report.json`、`state/health_check.json`；
每候选点：`real_run_check_report.json`、`metric_result_check_report.json`、`real_result_record_report.json`、`real_run_recovery_report.json`、`multi_testbench_aggregation_report.json`——都是单文件，被后一个候选覆盖，报告目录里只剩最后一个候选的；
远程：`remote_preparation_snapshot.json`（52 KB）、`remote_run_artifacts.sha256`（278 KB）、`remote_run_artifact_paths.txt`（834 KB）；
模式专属：`history_warm_start_audit.json/.md`、`fix_run_report.json`。

## E. 原始数据（重构后合并为一张观测表）

`optimizer_evaluations.jsonl` / `native_turbo_optimizer_evaluations.jsonl`（每行：run_id、参数、raw_x、指标、objective、fom、约束惩罚、状态、issues、batch 信息、并发信息、两个 manifest 路径）、`ledger/experiment_ledger.jsonl`、`state/optimizer_state.json` + `best_candidate.json`、`runs/**/result_manifest.json` / `metric_result_manifest.json` / `waveform_export_manifest.json`。
