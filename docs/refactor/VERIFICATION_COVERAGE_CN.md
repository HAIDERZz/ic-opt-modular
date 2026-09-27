# 功能验证覆盖盘点（2026-09-27）

口径：**真机** = 在真实 Spectre / EMX / 远程控制端上跑过并有证据（验收目录或库）；**冒烟** = 仓库 `smoke/` 里的一次性真机跑，无正式验收；
**测试** = 只有单元 / 集成测试（伪执行器）；**未验证** = 两者皆无。证据目录：`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/{n15,n23,n27,n35,n42}/`、
库 `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28/`。统计自这些 store 的 `steps.jsonl` / `observations.jsonl`（2026-09-27）。

## 命令行

| 命令 | 状态 | 证据 |
|---|---|---|
| `run optimize` | 真机 | N-23 B、N-27、N-35、N-42（OpenBox）；Windows 控制端 |
| `run fix_run` | 真机 | N-15、N-23 A/B 复核、N-27 任务 2、N-42 温度 A/B |
| `run coarse_to_fine` | 真机 | N-23 A（粗搜 OpenBox 12 + 细化 TuRBO 12 + 续跑 4） |
| `run signoff` | **测试** | 从未在真机上跑过（"先一角优化、再全 corner 复核前 k"） |
| `run lib_design` | **测试** | 库上的预测式优化从未真机跑过 |
| `run lib_signoff` | 真机 | 库 N-17 / corner 加密：`signoff:xfm_bs_ap` 150 行、`xfm_bs_m10` 90 行被采纳 |
| `run <自定义 recipe.py>` | 真机 | 库扫参 `sweep.py`（9000 行 `grid:` 来源） |
| `doctor` | 真机 | 每轮验收；含一次真实 `[FAIL]`（N-27 MSYS 路径改写） |
| `blocks` / `describe` | 真机 | N-23 / N-35 / N-42 安装检查 |
| `call <block>` | 真机 | `lib.*` 五个块（N-27）、`analyze.report`（本次报告复核）、`em.validate_profile`（N65 工艺 profile 验收） |
| `migrate` | 冒烟 | `smoke/t8_*` 三个 0.1 工程转换（MIGRATION.md 在案）；无正式验收 |
| `migrate-store` | 冒烟 / 库 | 库 store 重盖章（T15）；无正式验收 |
| `--plan` | 真机 | 每轮；含网表预检 FAIL（测试）与 WARNING（N-42 前置检查） |
| `--ssh-profile` | 真机 | Windows 控制端全部轮次 |
| `--cshrc` | 真机 | 服务器本机复核；`cshrc:` 写在 site.yaml（Windows） |

## 策略与点来源

| 项 | 状态 | 证据 |
|---|---|---|
| `openbox_gp_eic`（初始设计 + 代理） | 真机 | N-42：10 init + 70 acq，代理点向可行区聚集 |
| `turbo` | 真机 | N-23 A 细化 12 点（Windows 控制端，torch 2.8） |
| `random` | 冒烟 | 某 store 5 行 `suggest:random`；无验收 |
| `sobol` / `latin_hypercube` 策略 | **测试** | 无真机来源标记 |
| `points.fixed` | 真机 | fix_run 各轮 |
| `points.grid` | 真机 | 库扫参 9000 行 |
| `points.sobol` / `points.one_at_a_time` / `points.from` | **测试** | 无真机来源标记 |

## spec 特性

| 项 | 状态 | 证据 |
|---|---|---|
| 多平台（3 个 Spectre 平台） | 真机 | N-23 A、N-35、N-42 |
| corner：`model_section` | 真机 | N-35 / N-42（tt/ss/ff 段） |
| corner：`options`（`temp`） | 真机 | N-42 前置 A/B + 80 点（tt 逐位同 N-35，ss/ff 温漂方向正确） |
| corner：`variables` | **未验证有效** | 每次都设了 `temperature`，但没有导出真正引用它；plan 现会 WARNING |
| corner：`model_file` | **测试** | 无导出用过 |
| `corner_policy` worst_case / all_corners | 真机 | N-42 报告与 observations 一致 |
| 器件 `clean_port_xfm_bs`（电路内 EM） | 真机 | N-23 B、N-27 |
| 器件 `clean_port_ind_sym` / `xfm_ms`（纯器件） | 真机 | 库各层 |
| 器件带抽头（`CT*`） | **未验证** | 库无 CT 行；EM 链从未绑过带抽头器件 |
| 显式 `topology` | 真机 | N-23 / N-27 spec 写了 drives |
| 自定义 `plugin:` 路径 | **测试** | 只用过 builtin:clean_port |
| `em.accuracy` 网格式 / `full_wave` | 真机 | N-27 eval5（网格）、N-42 前 spec（full_wave） |
| `bindings`（两个 nport） | 真机 | N-23 B、N-27 |
| 器件量测指标（Qp / k @ f） | 真机 | N-23 B、N-27 |
| 波形导出（fix_run `waveforms=`） | **测试** | 无真机 waveforms.json |
| `license_queue_timeout_s` | **测试** | 无真机 spec 用过 |
| `keep_failed_runs: false`（保留策略） | **测试** | 真机全是默认值 |
| `budget` 超限（BudgetExceeded） | 真机 | N-27（改 spec 后旧观测计数） |

## 执行路径与失败路径

| 项 | 状态 | 证据 |
|---|---|---|
| 观测复用（同点不重仿） | 真机 | N-23 续跑；N-27 重跑复用 EMX 缓存 |
| EMX 缓存命中（跨系统键一致） | 真机 | N-23 复核 24/24 命中 |
| 同批同几何并发重复 EMX（N-24） | 真机观察 | N-23 冒烟与 Windows 各出现一次；未修 |
| `metric_failed`（表达式 nil / 非标量） | 真机 | N-35 首轮 |
| `failed:extract`（旧语义） | 真机 | N-23 A 12 点（BW nil） |
| `failed:spectre` / `failed:ocean`（许可证、崩溃） | **未验证** | 真机从未出现 |
| 超时杀进程组（`timeout_s`） | **未验证** | 真机从未触发 |
| Ctrl-C 中断与续跑（`interrupted`） | **未验证** | N-35 中止首轮但 steps 里没有 interrupted 行（agent 用别的方式停的） |
| 工程锁（第二个运行被拒） | **测试** | 真机未试 |
| 尾点文件名（Windows 长路径） | 真机 | N-15 / N-23 / N-35（plan 临时目录） |
| 报告全部段落 + 视觉审查 | 真机 | N-42 报告 + 本次渲染复核（桌面 / 手机） |
| 库 `cached` 盘点 | **测试** | 新功能，真机未用 |

## 平台

| 项 | 状态 |
|---|---|
| Linux 本机执行（LocalExecutor） | 真机（服务器复核） |
| Windows 控制端 + SSH | 真机（N-15 起五轮） |
| macOS 控制端 | **未验证**（torch 需 macOS 14+） |
| Windows 本机执行 | 设计上不支持（文档已写） |

## 建议的下一批验证（按性价比）

1. `signoff` recipe：用 N-42 的混频器工程（`corner=tt budget=20 top=5`），一次跑覆盖"单角搜索 + 全 corner 复核"，约 30 min。
2. 失败路径三合一：把 `simulator.timeout_s` 设得极短跑 2 点（超时杀进程组）；运行中 Ctrl-C（`interrupted` 与续跑）；同时起第二个运行（锁）。约 20 min，无需新导出。
3. `fix_run` 波形导出（`waveforms=`）与 `points.one_at_a_time` 灵敏度设计：混频器工程 1 点 × 9 变量步，约 20 min。
4. `random` 基线 20 点与 `sobol` 策略：与 N-42 同 spec 对照，约 40 min。
5. 带抽头器件进 EM 链：需先在库 / pcell 上做 CT 真实验证（T13 遗留，需批准 EMX）。
6. corner `variables` 真正生效：需要一份引用 `temperature` 参数的 Maestro 导出（用户在 ADE 里把 temp 设为变量）。
7. macOS 控制端：需要一台 macOS 14+ 笔记本。
