# T16 方案：格点间精度的普遍解法 + 审查余项（2026-09-25）

## 0. 一句话

两条主线：(1) 让查询库自己找出"模型最没把握的地方"并给出补点清单（`lib.densify`），再用真实 EMX 补点回流；(2) 把锚定频率上的
电感 / 耦合改为"低频值 × 谐振因子"的分解建模，先做对照研究再落地。外加审查文档（`AUDIT_ENV_ASSUMPTIONS_2026-09-24_CN.md`）
B / C 类余项与 T15 遗留的小项，按 `BACKLOG_CN.md` 第 2 节顺序分组派发。

## 1. 已拍板（用户，2026-09-25）

- B-1：a + b（`lib.densify` 按模型不确定度全域补点；L@f = L_lf × 谐振因子建模）。
- B-6：T16 范围 = BACKLOG 第 2 节全部（R-13…R-26、N-1…N-9），按其顺序。
- 开发约定沿用 T14/T15：方案、规格、验收由 Claude；编码派 subagent（Opus 5.5 / extra，各自 worktree，先核对基点，
  每个条目一个提交，定向测试 + ruff）。N-9 需要 Windows / macOS 机器，由用户安排。
- 与 0.3.0 发布的关系：T16 在 0.3.0 打标之后合入 main；manifest / profile 新字段都带默认值，不再破坏兼容。

## 2. 事实依据（为什么是这两条主线）

- 真实复核（`reports/library_query/XFM_SIGNOFF_N28_CN.html`，10 个格点间器件）：区间诚实（覆盖 96–98%，|z| 中位 0.6–0.7）
  但 Lp@40 实测偏 +1.5…+22%、k@40 到 +9%、SRF 到 −18%；Lp_lf 在 2% 内。
- 根因（T14.4）：bs 表锚定量的 Matern 长度尺度短（OD_S 方向 0.08–0.09），格点上 σ 0.2–0.5%、格点之间 5–9%；
  Lp_lf 的尺度长（0.36–10），格点间 σ 0.2%。锚定量 L@40 = L_lf × 谐振因子，因子在 SRF/f0 ≈ 1.25–2 时为 1.6–2.5，
  对 SRF 的误差极其敏感（SRF 偏 −18% → 因子偏几十个百分点）。系统 SRF 是两个绕组谐振的较小者，随 OD_S 变化时会换主导，
  各向同性 GP 只能用短尺度去拟合这个"拐点"。
- 因此：(a) 补点要按"模型 σ 大的地方"而不是按具体目标窗（普遍需求，不是 160 pH 这个例子）；(b) 把光滑的部分（L_lf）
  与不光滑的部分（谐振因子）分开建模，让误差集中到一个可解释的量上。
- 回流后（B-2）10 个格点间实测行已在库内（obs_1577–1581）。研究与验收把它们**从训练集剔除**（按 obs_id / `adopted.yaml`），
  作为真实的格点间测试集，不需要新的 EMX。

## 3. 设计

### 3.1 T16.1 `lib.densify`（B-1a）

**问题**：给定一张表和若干结果列，找出 n 个新几何点，使这些列的模型在整个采样域内的不确定度下降最多；输出可直接喂给
`lib_signoff candidates=` 的清单。与目标窗无关。

**接口**（block `lib.densify`，CLI `ic-opt call lib.densify <库根> stratum=… n=… [quantities=a,b] [pool_size=65536] [top=4000]
[seed=0] [k=2] [threads=] [workers=] [out=文件]`）：

```
{
  "stratum": "...", "quantities": ["Lp@40", "..."], "n": 60,
  "pool": {"size": 65536, "in_domain": 41230, "levels": {"1": 41230}},
  "before": {"Lp@40": {"rel_sigma": {"median": .., "p90": .., "max": ..}, "above_ceiling_share": ..}, ...},
  "after":  {同上：把 n 个选点当作已观测后的 σ（固定超参数下精确）},
  "candidates": [{"params": {...}, "score": .., "rel_sigma": {"Lp@40": .., ...}, "rel_sigma_after": {...},
                  "nearest": [3 个最近实测行]}],
  "method": {"score": "max_q sigma_rel_q / median_rel_q", "selection": "greedy exact posterior update", "top": 4000},
  "seconds": {...}, "notes": [...]
}
```

**算法**：

1. 模型：`library.models(stratum, quantities)`（默认列 = 该表全部列）。
2. 候选池：`suggest.pool`（Sobol，域内、按匝数层、层内固定维不变）→ 吸附到 manifest `steps` 的倍数 → `DomainGuard.inside`
   → 去掉与实测行重合的点（`ds.find`）。
3. 预测：每个模型的**原始**后验 σ（不加 `sigma_floor_rel`，因为下限是平的、对"哪里没把握"没有信息；`StratumGP.predict`
   增加 `floor=True` 参数，或新增 `posterior`）。对数目标的 σ 即相对 σ。
4. 评分：`score(x) = max_q sigma_rel_q(x) / median_rel_q`（`median_rel` 来自该列的校准记录）——"相当于几个典型误差"，
   让典型误差小的列在它最差的地方也能得到补点；缺 `median_rel` 时用 sigma_rel 本身。
5. 选点：取初始评分最高的 `top` 个候选（按内存预算 `suggest.predict_budget` 封顶），对每个模型算这些点之间的后验协方差
   （新增 `StratumGP.posterior_cov(x)`，per_nt 只在同一匝数层内有协方差；对数目标在对数空间）；然后贪心 n 轮：选评分最高的点
   j，对每个模型把其余点的方差按 `var_i -= cov_ij² / var_j` 精确更新（GP 的后验方差不依赖观测值，固定超参数下这是精确的），
   重新评分。这就是"选它之后别处的 σ 会降多少"，比距离惩罚更实在。
6. 输出 before / after 统计（同一池）、候选（附最近实测行）、耗时。`out=` 时写文件。

**测试**（合成库夹具 `tests/ic_opt/test_library*.py` 已有的 synthetic 库）：一维 / 二维已知函数上，(a) 选点落在训练点之间的空隙；
(b) 把选点真的加入训练后重拟合，σ 与 `after` 估计一致（固定超参数时相对误差 < 1e-6；允许超参数重新优化时定性一致）；
(c) 不重复、不与实测行重合、在域内；(d) per_nt 表两层各自选点；(e) `top` 封顶与内存预算生效；(f) block 严格 JSON、CLI 参数解析。

**文档**：`docs/em/library.md` 新增 5c "加密：哪里补点"（工作流：`lib.densify` → `lib_signoff … candidates=… adopt=true --plan`
→ 批准 → 数据集与模型缓存自动重建），SKILL.md 的块清单加 `lib.densify`。

**验收（Claude）**：C1 上述测试；C2 真实 xfm_bs_ap / xfm_bs_m10（训练集剔除 obs_1577–1581）：`n=60, quantities=Lp@40,Ls@40,
k@40,SRF`，(i) 选点的 OD_S 分布落在 20 µm 档之间（直方图）；(ii) 10 个格点间实测点处的 before-σ 明显高于格点上（它们当时按
"离实测行最远"挑出）；(iii) after 的 p90 相对 σ 比 before 下降（记录数字，不设阈值）；(iv) 耗时 < 3 min（热）。
C3（需用户批准，真实 EMX，约 60 点 / 1 h 以内）：跑 C2 的选点并回流，再以 10 个格点间实测点为测试集比较回流前后
Lp@40 / k@40 / SRF 的最大与中位误差；目标：Lp@40 最大误差从 22% 降到 8% 以内（做不到就如实记录，作为 T16.2 的输入）。

### 3.2 T16.2 锚定量的分解建模（B-1b）

**T16.2a 对照研究**（先做，不改产品代码）：脚本 `docs/refactor/reports/library_query/xfm_anchor_model_study.py` + 结果 JSON +
页面 `XFM_ANCHOR_MODEL_STUDY_CN.html`。表：xfm_bs_ap、xfm_bs_m10（40 GHz、60 GHz 列）、xfm_ms_ap、xfm_ms_m10（60 GHz 列，
按可用行）。训练集剔除回流行；评价三种：(i) 5×20% 留出；(ii) **留一档外径**（依次把某一 OD_S 档 / OD_P 档的全部行留出，用其余
档预测——这是"格点之间"的代理）；(iii) 10 个格点间实测点。指标：中位 / p90 / 最大相对误差、2σ 覆盖、|z| 中位。方法：

- A 直接（现状）：GP 拟合 log L@f0。
- B 比值：GP 拟合 log(L@f0 / L_lf) 与 log L_lf，相乘；σ 按独立假设合成（相对 σ 平方和开方）。
- C 等效谐振：r = L@f0 / L_lf；r > 1 时 f_r = f0 / sqrt(1 − 1/r)（并联 C 模型 L_eff = L/(1 − (f/f_r)²) 的反解），GP 拟合
  log f_r；预测时 r = 1/(1 − (f0/f_r)²)、L@f0 = L_lf × r，σ 按 δ 法传播。报告 r ≤ 1 的行占比（导体损耗 / 邻近效应让 L 随
  频率下降时无法反解；这些行退回 B）。
- D 物理先验 + 残差：r_phys = 1/(1 − (f0/SRF)²) 用**预测的**系统 SRF，GP 拟合 log(r / r_phys) 残差。
- E（可选）：直接 GP 的输入增加预测的 log SRF 作为特征（堆叠）。
- k@f0 同样试 A / B（比值 k@f0 / k_lf）。

给出每（表，列，方法）一行的结果表与推荐；写明每种方法的失败模式。

**T16.2b 落地**（待 T16.2a 结论，Claude 复核后再派）：manifest `Quantity.model: direct | ratio | resonance`（默认 `direct`，
库不变），`_fit_inputs` 生成派生目标列，`predict_all` / `query` 组合预测并给出组成（"由 L_lf × 因子 得到"），校准在组合预测上做，
模型缓存键包含该选项，`lib.region` / `suggest` / `Predict` 自动受益。验收：留一档外径误差与格点间测试集误差按研究的数字复现；
电感表（`model` 未设）逐位不变（G 门）。

### 3.3 T16.3 pcell / profile 组（R-13、R-14、R-15、R-23、R-26）

| 条目 | 位置（审查行） | 做法 | 验收 |
|---|---|---|---|
| R-13 | `em/pcell/drc_audit.py:319` `_AUDITED_VIAS = ("RV",)` | profile 新字段 `audited_vias`（要做包围审计的过孔名列表），默认 = profile 金属栈里声明的全部过孔（不再按 TSMC 名字静默跳过）；demo_6m 与作者指南（`skills/author-process-rule`）同步 | 合成 RDL 过孔命名的 profile 也被审计；demo_6m / 黄金 GDS 不变 |
| R-14 | `em/pcell/generator_plugin.py:44-60,314-316,411-412,498-508,726-738,770-778` | 配置校验按 `use_stack(profile)` 的金属栈解析（顶层金属、AP、相邻关系），不用 AP=11 与 `M<n>` 字面量 | 6 层 + AP 的 profile 上 `metal: AP, ct_metal: M6` 在配置校验就被判定；非 `M<n>` 命名的 profile 全部检查项生效 |
| R-15 | `stages/em_chain.py:141`、`drc_audit.py:545-552,567` | 生成器插件接口声明"期望导体 / 审计配方"（`expected_recipe` 之类），产品级 DRC 门按声明工作；内置六个生成器改用同一声明 | 玩具第三方生成器（测试内）不设 `drc_check=False` 也能通过 DRC 门；内置行为不变 |
| R-23 | `em/pcell/_pcell_core.py:20` `GRID_UM = 0.005` | profile 字段 `manufacturing_grid_um`（默认 0.005） | demo_6m / N28 逐字节不变；改成 0.01 的 profile 取整变化可测 |
| R-26 | `em/pcell/profile_validation.py:66-71,376-470` | 冒烟几何的线宽 / 间距夹到 profile 的 [min, max]（地环 50 / 边距 15 也按 profile 的上限夹） | min_width > 6 或 max_width < 6 的 profile 仍能通过校验；demo_6m 结果不变 |

每条一个提交；pcell 套件（约 375）与黄金 GDS 全绿。

### 3.4 T16.4 doctor / Spectre / 执行器组（R-17、R-18、N-1、N-2、N-3）

| 条目 | 位置 | 做法 | 验收 |
|---|---|---|---|
| R-17 | `stages/spectre_chain.py:83` `+lqtimeout 900` | `simulator.license_queue_timeout_s: int \| None = None`：None 不加该参数（Spectre 自己的行为），设置了才加；不写死实验室值 | 命令行拼装测试；README/spec 文档 |
| R-18 | `blocks/doctor.py:61-67`、`executor/base.py:53-56` | doctor 只探测流水线用到的工具（纯 EM spec：emx；有 testbench：spectre / ocean / license）；环境钩子文件按扩展名选 csh 或 sh（`.csh`/`.cshrc` → csh，其余 → sh），site.yaml 键名不变 | 纯 EM spec 无 spectre 也能 doctor 通过；sh 钩子测试 |
| N-1 | `library/query.py` `omp_cap` | `OPENBLAS_NUM_THREADS` / `MKL_NUM_THREADS` 也算显式上限（取设置了的最小值） | 单测 |
| N-2 | `executor/local.py`、`executor/ssh.py` | 本地任务起新进程组 / 会话，超时整组杀；SSH 任务远端用 `setsid` 记录进程组，超时后再发一次 `kill -- -PGID` | 假任务（子进程再起孙进程）超时后无残留 |
| N-3 | `blocks/…validate_profile`、`cli.py` | `proc=` 经 `--ssh-profile` 的执行器读取远端 `.proc`（下载到临时目录再校验） | 假 SSH 执行器测试 |

### 3.5 T16.5 查询库组（R-20、R-21、R-22、N-4、N-7）

| 条目 | 做法 | 验收 |
|---|---|---|
| R-20 | `Library(root, cache_dir=None)` + 各块 / CLI `cache_dir=`；库根不可写时回退 `~/.cache/ic-opt/<库根路径哈希>/` 并在输出 `notes` 说明 | 只读库根（chmod）下查询成功 |
| R-21 | manifest `Quantity.rel_sigma_max`（默认全局 0.15）；优先级：调用显式参数 > manifest 按列 > 默认 | query / suggest / region / Predict 一致 |
| R-22 | `lib.region relax=`（默认 0.10） | 参数透传测试 |
| N-4 | 校准 JSON 计算前后加文件锁（`_lock.py`），第二个进程等待后直接读 | 两进程并发只算一次 |
| N-7 | `lib.region` 输出回显 `k` 与 `rel_sigma_max`；页面脚本按其措辞 | JSON 字段 |

### 3.6 T16.6 测量组（R-24、R-25）

- R-24 `em/measure.py:53-58`：锚定频率取样按**局部**间距判断（最近样本在半个局部间隔内），否则线性插值相邻样本；落在样本上的行为逐位不变（G 门）。
- R-25 `spec.py:251-259`：默认四端口拓扑（次级反相）写进 spec 文档与 `docs/em/devices.md`；测量到 k_lf < 0 时在观测 `issues` 里告警，数据集 `check` 统计负 k 行数。

### 3.7 T16.7 杂项（N-5、N-6、N-8）

- N-5 ADR-0001 隔离冒烟脚本把 `~/.ic-opt/site.yaml` 绑进沙箱（能跑 bwrap 时验证，否则只改脚本并说明未验证）。
- N-6 pcell 注释里 em-opt 时代的指针（`geometry/…`、`.scratch/…`）清理。
- N-8 `docs/refactor/reports/**` 的 ruff 提示修掉，并把该目录加进 ruff 检查范围（或在 pyproject 里单列）。

## 4. 波次与派发

- 波次 1（0.3.0 打标后）：T16.1、T16.2a、T16.3、T16.4 并行（四个 worktree）。
- 波次 2：T16.5、T16.6、T16.7 并行；随后 T16.2b（按研究结论）；T16.1c 真实加密批（需用户批准 `--plan`）。
- 每波结束 Claude 验收（第 3 节各表的"验收"列 + 定向测试 + ruff + 黄金 GDS / G 门），记入本文件第 7 节与 `EXECUTION_PLAN_CN.md`。
- 版本：T16 全部合入后升 0.4.0（manifest / profile / spec 新增可选字段，模型行为可选变化）。

## 5. 约定

- 派发提示写明：核对基点（`git merge --ff-only main`）、`PYTHONPATH=worktree/src` + 主树 venv、唯一临时文件前缀、不跑真实 EMX /
  Spectre、不写 N28 工艺数值、每条目一提交、报告提交号与证据。
- 真实 EMX（T16.1c）：总线程 ≤ 128、内存 ≤ 256 GB、`--simultaneous-frequencies=0`、`--plan` 后经用户批准。

## 6. 风险

- T16.1 的贪心精确更新在超参数重新优化后只是近似；验收 (b) 用固定超参数验证公式、用重拟合验证定性。
- T16.2 的 C 法在 r ≤ 1（L 随频率下降）的行上无法反解，研究要报告占比；D 法依赖 SRF 模型，可能把 SRF 的拐点问题原样带回。
- T16.3 R-15 改插件接口：内置六个生成器必须逐字节不变（黄金 GDS + 库回放）。
- N-2 的 SSH 整组杀依赖远端 `setsid`，Windows 控制端只做本地进程组分支。

## 7. 交付与验收记录

### 波次 1（2026-09-25 05:00–07:30；subagent Opus 5.5 / extra，各自 worktree，基点 ba1895b = v0.3.0）

| 任务 | 提交（main） | 交付要点 | 验收 |
|---|---|---|---|
| T16.1 `lib.densify` | 0b3e5be | `library/densify.py`：原始后验 σ（`predict(floor=False)`）、`posterior_cov`、按内存预算取 top 候选、贪心 + 精确秩一方差更新（选点协方差的转轴 Cholesky）、`after` 覆盖全池；`bounds=`（每维窗口或固定值，先裁盒再域检查）；`score=ceiling|typical`（默认 ceiling = σ_rel / rel_sigma_max）；扫频上限之上的 SRF 按 `above_sweep` 处理；块 + CLI + `docs/em/library.md` 5c + SKILL | 25 个新测试；定向套件 158 绿；ruff 干净；G 门 6/6；**真实库**（两张单圈表，n=60，Lp@40/Ls@40/k@40/SRF）：热跑 9–21 s；全域选点 59/60 在偏心区（1581 行里 CS>0 只有 101 行），同心子域（`bounds={"center_spacing_um": 0}`）选点主要落在 OD_P>120 µm 的格点之间（格距 30 µm、谐振更近 40 GHz）；before→after 超上限占比 Lp@40 44%→10%（ap, cs0）；剔除回流行后，10 个旧格点间点的原始 σ 6–12%（格点上 0.4%），|z| ≤ 2.2 说明 σ 如实；六份输出 `reports/library_query/densify_40g_xfm_bs_*.json`。C3 真实加密批改为"选点 + 独立随机测试点"，待批准（BACKLOG B-12） |
| T16.3 pcell 组 | 511a937 R-13、e98724f R-14、6227c6d R-15、f0b0c05 R-23、7c6e4bd R-26 | `audited_vias`（缺省审计金属栈全部过孔，711 个基线构建 581 项检查 0 发现）；配置金属检查按 profile 金属栈；`PassiveDeviceGenerator.expected_conductors` 让插件过 DRC 门（768 个基线配置导体清单不变）；`manufacturing_grid_um`；冒烟线宽按 profile 规则取值并等比放大 | pcell 带私有 profile 952 绿、不带 318/364 绿；黄金 GDS 13/13；库回放 9020/9020 相同；t13_11 基线回放与基点一致（仅已知的一条拒绝措辞差异）；ruff 干净。遗留 N-13（M<n> 名字与位置不一致的潜在分歧）→ T16.7 |
| T16.4 doctor / 执行器组 | e5dc8a7 R-17、c66778b R-18、f300796 N-1、85fcb4b N-2、ca1814f N-3；追加 176162e N-12、0beb0f8 N-10、9a009af N-11 | `license_queue_timeout_s`（None 不传参）；doctor 只探测流水线用到的工具、钩子按扩展名选 csh/sh；三种 BLAS 环境变量都算上限；任务起独立进程组、超时整组杀、SIGINT/SIGHUP 转发、SSH 远端 setsid + 清理；`validate_profile proc=` 经 SSH 读；migrate 显式写回 0.1 的 900；超时只失败该点；Ctrl-C 后不再启动任何东西并记录 `interrupted` | 非 pcell 347 绿 / 12 跳过（基点 271）；ruff 干净；假主机与本机充当远端的端到端测试 |
| T16.2a 研究 | cab49bd（页 `reports/library_query/XFM_ANCHOR_MODEL_STUDY_CN.html`，artifact GYPUVJwkAmSBM2aEyjFxmR） | 2045 次 GP 拟合、55 min；方法 A–E 外加 F（直接 + 无量纲输入）、BF（比值 + 无量纲）、DF（低频电感 × 理想谐振因子 × 无量纲残差）；评价：5×20% 留出、整档留出 OD_P / OD_S、10 个格点间实测 | **结论**：陡的方向是"两线圈错开多少"，原始坐标下 OD_P 档间只能硬插值；换坐标后 SRF 整档留出 p90 83.8/95.3%→1.4/1.6%，Lp@40 DF 36.3/39.9%→2.9/2.9%（最大 216.9/106.2%→6.8/8.2%），区间中位 σ 2.7/3.3% 覆盖 100%；格点间实测上 BF 最好（2.0/4.9%），DF 4.7/9.2%，因为"偏心 + 两外径不等"从未采样、SRF 在那里偏 18–22%；k 现状最好；多圈表 BF 把 Ls@60 整档留出 p90 23.8/23.5%→7.0/8.8%。T16.2b 按 DF 落地（`model: resonance` + SRF `feature_map`），ms 表用 `ratio` |

### 波次 2（2026-09-25 07:35 派发）：T16.5 库组、T16.6 测量组、T16.7 杂项 + N-13 —— 待记录。
