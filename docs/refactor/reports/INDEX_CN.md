# 报告页对照表（`docs/refactor/reports/`，2026-09-25）

每个页面：由哪个脚本生成、读什么数据、发布到哪个 artifact。artifact 是发布时的快照，页面文件以仓库为准。
库本身在仓库外（`<ic-opt-library>/n28/`），含 N28 几何的批产物（`smoke/`）不入仓库。

## library_query/（查询库）

| 页面 | 生成脚本 | 数据 | artifact | 备注 |
|---|---|---|---|---|
| `IND_QUERY_VERIFY_CN.html` | `ind_query_report.py` | `ind_query_verify.json`（`ind_dataset.py` + `ind_query_verify.py`；09-23 的旧基线保留为 `ind_query_verify_20260923_1038rows.json`） | [LzQW9GC3berWR45jMzhgtn](https://claude.ai/artifact/LzQW9GC3berWR45jMzhgtn) v2 | 2026-09-25 按回流后的 1048 行重生成（cbbe008） |
| `T13_PLAN_CN.html` | `t13_plan_page.py` | `../T13_LIBRARY_MODULE_PLAN_CN.md` + `arch/t13-library-module.architecture.html` + `arch/t13-dev-plan.workflow.html` | [9Kd6syDGfn56CXnXGo2qqJ](https://claude.ai/artifact/9Kd6syDGfn56CXnXGo2qqJ) | T13 方案页 |
| `XFM_QUERY_VERIFY_CN.html` | `xfm_query_report.py` | `xfm_query_verify_{bs,ms}_{ap,m10}.json`（`xfm_query_verify.py`） | [6MKZJ2QPf25CYocfPTJNZm](https://claude.ai/artifact/6MKZJ2QPf25CYocfPTJNZm) v3 | ms 两表为高频加密后的重验；2026-09-25 N-16（Lp/Ls 改 ratio + 无量纲坐标）后再次重跑 ms 两表 |
| `IND_SIGNOFF_N28_CN.html` | `ind_signoff_report.py` | `ind_signoff_{ap,m10}.json`（`lib_signoff` 报告；候选由 `ind_signoff_candidates.py` 挑选） | [6hBxsFKhvUo3vFMZfufryg](https://claude.ai/artifact/6hBxsFKhvUo3vFMZfufryg) v3 | 电感 10 点真实复核，已回流 |
| `XFM_SIGNOFF_N28_CN.html` | `xfm_signoff_report.py` | `xfm_signoff_bs_{ap,m10}.json`；候选 `xfm_signoff_candidates_bs_*.json`（`xfm_signoff_candidates.py`）；`figs/XFM_SIGNOFF_N28_CN_error_vs_interval.png` | [1izasWsgeg4BagNz6rrz5n](https://claude.ai/artifact/1izasWsgeg4BagNz6rrz5n) | 变压器格点间 10 点真实复核，未回流（BACKLOG B-2） |
| `XFM_ANCHOR_MODEL_STUDY_CN.html` | `xfm_anchor_model_study.py page` | `xfm_anchor_model_study.json`（同一脚本 `run`：四张变压器表 × 三种评价 × 各建法的 GP 拟合）；`figs/XFM_ANCHOR_MODEL_STUDY_CN_*.png` | [GYPUVJwkAmSBM2aEyjFxmR](https://claude.ai/artifact/GYPUVJwkAmSBM2aEyjFxmR) | T16.2a 锚定结果列（Lp@f0、k@f0 等按频率列出的量）的建模对照（不改产品代码）|
| `XFM_BS_40G_REGION_CN.html` | `xfm_bs_region_report.py` | `region_40g_xfm_bs_{ap,m10}.json`（`lib.region` block 输出）；`figs/XFM_BS_40G_REGION_CN_*.png` | [Gu4d8j12ikgSpeVrXusQG6](https://claude.ai/artifact/Gu4d8j12ikgSpeVrXusQG6) v3 | 40 GHz 单圈变压器扫参范围；v3 = 回流 10 行 + 谐振分解模型（T16.2b）后重生成 |
| `XFM_BS_60G_REGION_CN.html` | `xfm_bs_region_report.py` | `region_60g_xfm_bs_{ap,m10}.json`；`figs/XFM_BS_60G_REGION_CN_*.png` | [4eCa3y9zBQ3LxDSs53A3Hs](https://claude.ai/artifact/4eCa3y9zBQ3LxDSs53A3Hs) v2 | 60 GHz 同上；v2 同 40 GHz 的 v3 |
| `XFM_DENSIFY_B12_CN.html` | `b12_densify_report.py` | `b12_evaluation_xfm_bs_ap.json`、`b12_test10_signoff_xfm_bs_ap.json`、`b12_densify60_signoff_xfm_bs_ap.json`、`b12_{densify60,test10}_candidates_xfm_bs_ap.json`（由 `<ic-opt-library>/n28_signoff/` 的 `make_test10.py` / `run_b12.sh` / `b12_evaluate.py` 产出）；`figs/XFM_DENSIFY_B12_CN_test_points.png` | [Pfa1MDmG4FoLDrsZqKKcep](https://claude.ai/artifact/Pfa1MDmG4FoLDrsZqKKcep) | B-12 真实加密批：60 选点回流 xfm_bs_ap（1581→1641 行）+ 10 个独立测试点回流前后对比 |
| `LIBRARY_PRINCIPLES_CN.html` | `library_principles_page.py` | 示意图 `library_principles_figs.py` → `figs/LIBRARY_PRINCIPLES_CN_{gp,calibration,resonance}.png`（示意数据，非库内数值） | [EZ9fBMhw9Vao98bdv59ft2](https://claude.ai/artifact/EZ9fBMhw9Vao98bdv59ft2) | 查询库原理讲解（面向非专业读者：高斯过程、补点依据与盲点、校准、域守卫、无量纲坐标、组合模型、四种问法；N-16 前的背景） |
| `XFM_MS_RATIO_ACCEPTANCE_CN.html` | `xfm_ms_ratio_acceptance_page.py` | `xfm_ms_ratio_acceptance.json`（`xfm_ms_ratio_acceptance.py LIB SCRATCH OUT`：两张多圈表 57 个整档留出 × 三种建法 + 完整库校准）；`figs/XFM_MS_RATIO_ACCEPTANCE_CN_p90.png` | [X7UEKWWVCxwKFvu4xG7XHw](https://claude.ai/artifact/X7UEKWWVCxwKFvu4xG7XHw) | N-16：多圈表 Lp/Ls 改比值 + 无量纲坐标前的验收 |
| `XFM_DENSIFY_N17_CN.html` | `n17_densify_report.py` | `n17_evaluation_xfm_bs_{ap,m10}.json`、`n17_{test10,densify60}_signoff_xfm_bs_{ap,m10}.json`、`n17_{densify60,test10}_candidates_xfm_bs_{ap,m10}.json`（由 `<ic-opt-library>/n28_signoff/` 的 `make_n17.py` / `make_n17_test10.py` / `run_n17.sh` / `n17_evaluate.py` 产出）；`n17_findings.json`（结论文字）；`figs/XFM_DENSIFY_N17_CN_{ap,m10}_test_points.png` | [229i2uKvcfDBk3pFLjk13X](https://claude.ai/artifact/229i2uKvcfDBk3pFLjk13X) | N-17 第二轮补点：中心偏移 8–24 µm × 外径比 0.8–1.25，ap 与 m10 各 60 选点回流（1641→1701、1581→1641 行）+ 10 独立测试点前后对比 |
| `XFM_BS_Q_MAP_ACCEPTANCE_CN.html` | `xfm_bs_q_map_acceptance_page.py` | `xfm_bs_q_map_acceptance.json`（`xfm_bs_q_map_acceptance.py LIB SCRATCH OUT`：两张单圈表 Q 列直接建模 vs 加无量纲坐标，80 折整档留出 + 30 个独立设计 + 完整库校准）；`figs/XFM_BS_Q_MAP_ACCEPTANCE_CN_p90.png` | [1CrNf1XnBGspsqU68Me4fh](https://claude.ai/artifact/1CrNf1XnBGspsqU68Me4fh) | N-19 方案 1 验收：未采用（偏移带更自信但不更准） |
| `arch/region-60g.workflow.html` | archify `deliver`（源 `arch/region-60g.workflow.json`） | — | [HisZZFoUYYiMv3rjQKDbcz](https://claude.ai/artifact/HisZZFoUYYiMv3rjQKDbcz) | 可行域查找流程图 |
| `arch/t13-library-module.architecture.html`、`arch/t13-dev-plan.workflow.html` | archify `deliver`（同名 `.json`） | — | 嵌入 T13 方案页 | `*.visual-check.*` 是浏览器检查证据 |

非页面产物：`region_acceptance.py` + `region_acceptance_40g.json` / `region_acceptance_40g_t15.json`（T14.4 与 T15.8 的验收记录）；
`xfm_bs_region_40g.json`（原型输出，C3 对照基准）；`xfm_srf_jump_fig.py` → `figs/xfm_srf_jump.png`（变压器 SRF_p 跳变配图）。

## pcell_plan/（建库与 pcell）

| 页面 | 生成脚本 | 数据 | artifact | 备注 |
|---|---|---|---|---|
| `PCELL_IMPROVEMENT_PLAN_CN.html` | 直接编写（无脚本） | — | [U4vL284CytQFfe6DAncnk7](https://claude.ai/artifact/U4vL284CytQFfe6DAncnk7) | PCell 打磨计划（T11） |
| `IND_LIBRARY_PLAN_CN.html` | 直接编写（无脚本） | 先导批 `ind_pilot.py` / `ind_pilot_report.py`（产物在 `smoke/`） | [3PwCq5MSpd9YpjbRZUEKQG](https://claude.ai/artifact/3PwCq5MSpd9YpjbRZUEKQG) | 电感建库提案 |
| `IND_LIBRARY_N28_CN.html` | `ind_library_report.py` | 库根的批产物（`ind_library.py` 批跑；网格 `ind_grid.py` → `ind_grid_n28.json`） | [UxuKzJTt2WKitZu1W6NibU](https://claude.ai/artifact/UxuKzJTt2WKitZu1W6NibU) | 电感库审阅页 |
| `XFM_LIBRARY_PLAN_CN.html` | `xfm_proposal.py` | 先导批 `xfm_pilot.py` / `xfm_pilot_report.py` | [NtoZwFEiSBrB3JKZA5KrJA](https://claude.ai/artifact/NtoZwFEiSBrB3JKZA5KrJA) | 变压器建库提案 |
| `XFM_LIBRARY_N28_CN.html` | `xfm_library_report.py` | 库根的批产物（`xfm_library.py` 批跑；网格 `xfm_grid.py` → `xfm_grid_n28.json`，加密 `xfm_densify.py` / `xfm_densify_select.py` → `xfm_grid_n28_densify_sixty_cells.json`） | [98KEBu2RDgmDuVr2nm8CRx](https://claude.ai/artifact/98KEBu2RDgmDuVr2nm8CRx) v2 | 变压器库审阅页（含加密批） |
| `smoke/pcell_random50/REPORT.html`（不入仓库） | `random50_report.py` | 随机器件自检产物 | [SbcEAt2h18v8eJyYQNSemh](https://claude.ai/artifact/SbcEAt2h18v8eJyYQNSemh) | PCell 随机自检 |

其他工具与图：`d4_grid.py` / `d4_probe.py` + `d4/`（demo_6m 的 D4 落点探针网格）；`library_drift_scan.py` → `library_drift.json`
（库行几何漂移扫描）；`pcell_byte_replay.py`（T13.11 逐字节回放，基准在 `<ic-opt-library>/t13_11_baseline/`）；`xfm_memory.py`
（EMX 内存模型）；`ind_library_check.py` / `xfm_library_check.py`（批完成检查）；`*_progress.sh`（批进度）；`demo_families.py` →
`clean_port_*.png`（六族端口渲染）；`gf_*.png`（gdsfactory 上游器件渲染，对照用）；`ind_*.png`（先导批渲染）。

## project_structure/（项目结构）

| 页面 | 生成脚本 | 数据 | artifact | 备注 |
|---|---|---|---|---|
| `PROJECT_STRUCTURE_2026-09-25_CN.html` | `build_structure_report.py page` | `structure_facts.json`（同一脚本 `collect`：仓库 / git / `ic-opt blocks` / INDEX / `diagrams/*.json` / `graphify-out/` 实测）、`library_rows.json`（`ic-opt call lib.load` 的行数）；`figs/PROJECT_STRUCTURE_*.png`；内嵌两张 archify 图的浏览器检查截图 | [GyLKpWNBzn8b4HmHU3z9f2](https://claude.ai/artifact/GyLKpWNBzn8b4HmHU3z9f2) | 阶段性收尾的项目结构总览：archify 两图 + graphify 知识图谱 + 包 / 块 / 配方 / 命令行 / 文档 / 测试 / 待办 |

## 其他

- `docs/refactor/diagrams/*.json`：archify 源文件；渲染的 `.html` / `.png` 不入仓库（`.gitignore`），`*.visual-check.json` 是浏览器检查证据。2026-09-25 的两张：`ic-opt-system-v030.architecture.json`（IC-Opt 0.3 系统结构，[Q2GCRD2WtGwpZHH8LvTFfG](https://claude.ai/artifact/Q2GCRD2WtGwpZHH8LvTFfG)）、`device-library-pipeline.dataflow.json`（器件查询库数据流，[T1NBTvFvkTtoEstp7ZvMqA](https://claude.ai/artifact/T1NBTvFvkTtoEstp7ZvMqA)）。重构初期（09-22）的五张图没有随 0.3.0 更新，已于 2026-09-25 删除（用户决定），历史可在 git 里找。
- `docs/refactor/REPORT_INVENTORY_CN.md`：优化运行自身产出的报告文件清单（决策 ⑦），与本文件无关。
