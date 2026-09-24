# 报告页对照表（`docs/refactor/reports/`，2026-09-25）

每个页面：由哪个脚本生成、读什么数据、发布到哪个 artifact。artifact 是发布时的快照，页面文件以仓库为准。
库本身在仓库外（`<ic-opt-library>/n28/`），含 N28 几何的批产物（`smoke/`）不入仓库。

## library_query/（查询库）

| 页面 | 生成脚本 | 数据 | artifact | 备注 |
|---|---|---|---|---|
| `IND_QUERY_VERIFY_CN.html` | `ind_query_report.py` | `ind_query_verify.json`（`ind_dataset.py` + `ind_query_verify.py`；09-23 的旧基线保留为 `ind_query_verify_20260923_1038rows.json`） | [LzQW9GC3berWR45jMzhgtn](https://claude.ai/artifact/LzQW9GC3berWR45jMzhgtn) v2 | 2026-09-25 按回流后的 1048 行重生成（cbbe008） |
| `T13_PLAN_CN.html` | `t13_plan_page.py` | `../T13_LIBRARY_MODULE_PLAN_CN.md` + `arch/t13-library-module.architecture.html` + `arch/t13-dev-plan.workflow.html` | [9Kd6syDGfn56CXnXGo2qqJ](https://claude.ai/artifact/9Kd6syDGfn56CXnXGo2qqJ) | T13 方案页 |
| `XFM_QUERY_VERIFY_CN.html` | `xfm_query_report.py` | `xfm_query_verify_{bs,ms}_{ap,m10}.json`（`xfm_query_verify.py`） | [6MKZJ2QPf25CYocfPTJNZm](https://claude.ai/artifact/6MKZJ2QPf25CYocfPTJNZm) v3 | ms 两表为高频加密后的重验 |
| `IND_SIGNOFF_N28_CN.html` | `ind_signoff_report.py` | `ind_signoff_{ap,m10}.json`（`lib_signoff` 报告；候选由 `ind_signoff_candidates.py` 挑选） | [6hBxsFKhvUo3vFMZfufryg](https://claude.ai/artifact/6hBxsFKhvUo3vFMZfufryg) v3 | 电感 10 点真实复核，已回流 |
| `XFM_SIGNOFF_N28_CN.html` | `xfm_signoff_report.py` | `xfm_signoff_bs_{ap,m10}.json`；候选 `xfm_signoff_candidates_bs_*.json`（`xfm_signoff_candidates.py`）；`figs/XFM_SIGNOFF_N28_CN_error_vs_interval.png` | [1izasWsgeg4BagNz6rrz5n](https://claude.ai/artifact/1izasWsgeg4BagNz6rrz5n) | 变压器格点间 10 点真实复核，未回流（BACKLOG B-2） |
| `XFM_ANCHOR_MODEL_STUDY_CN.html` | `xfm_anchor_model_study.py page` | `xfm_anchor_model_study.json`（同一脚本 `run`：四张变压器表 × 三种评价 × 各建法的 GP 拟合）；`figs/XFM_ANCHOR_MODEL_STUDY_CN_*.png` | [GYPUVJwkAmSBM2aEyjFxmR](https://claude.ai/artifact/GYPUVJwkAmSBM2aEyjFxmR) | T16.2a 预列频率结果列的建模对照（不改产品代码）|
| `XFM_BS_40G_REGION_CN.html` | `xfm_bs_region_report.py` | `region_40g_xfm_bs_{ap,m10}.json`（`lib.region` block 输出）；`figs/XFM_BS_40G_REGION_CN_*.png` | [Gu4d8j12ikgSpeVrXusQG6](https://claude.ai/artifact/Gu4d8j12ikgSpeVrXusQG6) v3 | 40 GHz 单圈变压器扫参范围；v3 = 回流 10 行 + 谐振分解模型（T16.2b）后重生成 |
| `XFM_BS_60G_REGION_CN.html` | `xfm_bs_region_report.py` | `region_60g_xfm_bs_{ap,m10}.json`；`figs/XFM_BS_60G_REGION_CN_*.png` | [4eCa3y9zBQ3LxDSs53A3Hs](https://claude.ai/artifact/4eCa3y9zBQ3LxDSs53A3Hs) v2 | 60 GHz 同上；v2 同 40 GHz 的 v3 |
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

## 其他

- `docs/refactor/diagrams/*.json`：archify 源文件；渲染的 `.html` / `.png` 不入仓库（`.gitignore`）。
- `docs/refactor/REPORT_INVENTORY_CN.md`：优化运行自身产出的报告文件清单（决策 ⑦），与本文件无关。
