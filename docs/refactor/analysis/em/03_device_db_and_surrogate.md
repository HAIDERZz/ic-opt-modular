# EM 查询库(device_db + surrogate) → ic-opt-modular 概念映射分析

- 研究对象仓库：`<em-opt>`（package `em_ic_opt_workflow`），HEAD `06be90768557923e7940de45b3ad2ceebeca79df`（2026-09-22，工作区干净）。
- 目标仓库/落点：`<repo>`，本文对照 `docs/refactor/DESIGN_CN.md`（下称"设计契约"）第 4.1 节已定的"通用引擎 + 可插阶段"架构与 `suggesters/` / `points.*` 词汇。
- 方法：直接 `Read`/`grep` 源码与文档，未运行任何测试或 EMX；未修改除本文件外的任何文件。
- 版本号提醒（贯穿全文，先说明以免混淆）：这个子系统里同时存在 **6 套独立的版本计数器**——`schema.py` 的 `SCHEMA_VERSION`/`ACTIVE_SCHEMA_VERSION`（DB 表结构）、`ingest.py` 的 `CURRENT_GEOM_VERSION`/`geom_version`（PCell 几何生成代）、`measure.py` 的 `DERIVED_VERSION`（指标公式版本）、`model_cache.py` 的 `MODEL_CODE_VERSION`（GP 拟合语义版本）、`family_catalog.py` 的 `schema_revision`/`family_schema_revision`（家族生成器契约版本）、`cli.py` 的 `OUTPUT_SCHEMA_VERSION`（JSON 输出契约版本）。第 7 节还会展开。

---

## 1. sNp → 器件指标

### 1.1 Touchstone 解析：自写解析器，不依赖第三方库

`src/em_ic_opt_workflow/device_db/measure.py` 顶部 import 只有 `math`、`pathlib.Path`、`numpy`、`pydantic`（第 10-16 行）——**没有 `scikit-rf` 或任何第三方 Touchstone 库**，解析是手写的：

```python
def read_touchstone(
    path: Path | str, *, return_z0: bool = False,
) -> tuple[np.ndarray, np.ndarray] | tuple[np.ndarray, np.ndarray, float]:
```
（`src/em_ic_opt_workflow/device_db/measure.py:34-36`）

- 只支持 **Touchstone v1**（`#`注释行 + 逐频率数据行格式；docstring 明确写"Read a touchstone v1 sNp file"，第 37 行）。若遇到 Touchstone v2 的 `[Version]` 关键字块，代码没有专门分支识别，会把 `[Version]`/`2.0` 当成数据 token 传给 `float()`，抛出未包装的 `ValueError`，而不是 fail-closed 的 `MeasureError`（第 69 行 `tokens.extend(float(t) for t in line.split())`）。
- 端口数从文件后缀推断：`_ports_from_suffix`（第 27-31 行）要求形如 `.sNp`（如 `.s2p`/`.s4p`），否则抛 `MeasureError`。
- 支持 `RI`/`MA`/`DB` 三种数值格式与 `HZ/KHZ/MHZ/GHZ` 频率单位（`_FREQ_MULT`，第 20 行；解析逻辑第 54-68 行、80-84 行）。
- **参考阻抗 R 的读取**：选项行 `# ... R <value>` 决定 `z0`（第 61-67 行），缺省 50.0 欧姆（第 48 行）；非有限或非正会 fail-closed（第 66-67 行）。`return_z0=True` 时把它一并返回（第 88 行）；不传则维持"两值返回"的历史 API（第 44 行注释）。
- **端口顺序假设（重要且容易踩坑）**：Touchstone v1 对 **2-port 有特殊列序** `S11 S21 S12 S22`（不是行主序），代码用 `if n == 2: s = s.transpose(0, 2, 1)` 专门修正（第 86-87 行）；对 n>2（本库里 4-port 变压器）不需要转置，因为文件本身就是行主序。这是一个只对 2-port 生效的特判，若未来新增 3-port 或其它偶数端口家族，必须重新核对 Touchstone 规范，不能想当然套用同一 transpose。

### 1.2 S → Z：不是 Y 参数，是 Z 参数

`s_to_z`（`src/em_ic_opt_workflow/device_db/measure.py:91-106`）：

```python
def s_to_z(s: np.ndarray, z0: float = 50.0) -> np.ndarray:
    ...
    z[i] = z0 * np.linalg.solve((eye - s[i]).T, (eye + s[i]).T).T
```

即标准 `Z = z0 (I+S)(I-S)^-1`，**逐频率求解**（第 100-106 行的 for 循环）：某一频点若 `(I-S)` 奇异（如 0 Hz 处理想开路 `S=I`）只让该频点变 `NaN`，不会污染整批（docstring 第 95-96 行 / 代码注释同）。

### 1.3 差分/平衡-不平衡混合模式阻抗：理想 balun 约束方程组

`TopologyConfig`（`src/em_ic_opt_workflow/device_db/measure.py:109-135`，pydantic `BaseModel`）描述一种"理想 balun 测量拓扑"：

```python
class TopologyConfig(BaseModel):
    name: str
    drives: list[tuple[int, int]] = Field(min_length=1, max_length=2)
    drive_names: list[str]
    grounded_ports: list[int] = []
    low_freq_max_hz: float = 3e9
    expected_ports: int | None = None
```

- `drives`：1-based sNp 端口号对 `(plus, minus)`，每对代表一次差分驱动；`drive_names` 给这对驱动起名（如 `"p"`、`"s"`），决定输出里 `L<name>`/`Q<name>` 的列名。
- 约束个数必须等于端口数：`2*len(drives) + len(grounded_ports) == expected_ports`（`_check` 校验器，第 125-135 行）。

`_mixed_mode_z`（第 138-168 行）按频点求解线性方程组，把全端口 Z 矩阵坍缩成 `[F, D, D]` 的"混合模式"阻抗矩阵：每对驱动贡献一条"共模钳位" `V_plus + V_minus = 0`（第 150-152 行）、每个接地端口贡献 `V_g = 0`（第 153-155 行）、再加一条"差分激励" `I_plus - I_minus = 2*Ia`（第 156-159 行），解出各端口电流后用 `v = z[fi] @ currents` 求端口电压，差分阻抗即 `zm[fi,i] = v[p-1] - v[m-1]`（第 165-167 行）。这套方程组就是 measure.py 顶部 docstring 里写的"2026-07-10 OCEAN parity"（第 4-8 行）：`Z = z0(I+S)(I-S)^-1`；每个理想 balun 贡献共模钳位与差分激励；接地端口贡献 `V=0`。

**两个家族的实际拓扑常量**（不在 measure.py 里，是各调用方冻结的具体实例，两处定义完全一致）：

```python
IND_DIFF = TopologyConfig(name="ind_diff", drives=[(1, 2)], drive_names=["p"], expected_ports=2)
XFM_DUAL_BALUN = TopologyConfig(name="xfm_dual_balun", drives=[(1, 2), (4, 3)],
                                 drive_names=["p", "s"], expected_ports=4)
```
（`experiments/device_db_sweep_n28/sweep_driver.py:146-150`，另一份等价定义在 `experiments/device_db_sweep_n28/run_parity_gate.py:106-114`）

即：2-port 电感只有一路差分驱动 `(1,2)`；4-port 变压器（sNp 端口序 `[P1,N1,P2,N2]`）用两路驱动 `(1,2)` 和 `(4,3)`——**次级刻意反接**（`sweep_driver.py:141-144` 注释："xfm_dual_balun's secondary drive is (4, 3): sNp order is [P1, N1, P2, N2] ... the proven parity orientation is V(N2) - V(P2)"）。这是"不能只凭端口字母猜测 k 符号"的直接代码依据（另见 `docs/guide/03-device-db-and-hermes-db.md:62`）。

### 1.4 L/Q/SRF/k 的具体公式

全部在 `metrics_from_s`（`src/em_ic_opt_workflow/device_db/measure.py:214-275`）：

```python
def metrics_from_s(freqs: np.ndarray, s: np.ndarray,
                   topo: TopologyConfig, *, z0: float = 50.0) -> dict:
```

对每路驱动 `nm`（如 `"p"`、`"s"`），取混合模式阻抗对角元 `zii = zm[:, i, i]`：

- `L<nm>(f) = Im(zii) / w`，`w = 2*pi*f`（第 244-245 行）——标准单端口等效电感公式，直接对 `w=0` 的频点填 `NaN`（`np.where(w > 0, ...)`）。
- `Q<nm>(f) = Im(zii) / Re(zii)`（第 246 行）。
- 双驱动时的耦合系数：`k(f) = Im(z01) / sqrt(Im(z00) * Im(z11))`（第 266-271 行），即经典 `k = M / sqrt(Lp*Ls)`，因为分子分母的 `w` 因子相互抵消，代码直接对 `Im(Z)` 做比值，不必先各自除以 `w` 再相乘开方。

标量指标（每个 sample 一行，`freq_hz IS NULL`）：

- `L<nm>_lf`：`_finite_lf_mean`（第 171-175 行）——`0 < f <= low_freq_max_hz`（默认 3 GHz）区间内有限 L 值的算术平均。
- `L<nm>_res`："SRF/5 处取值"，由 `_l_res`（第 178-192 行）实现：在 `0 < f <= SRF_cap/5` 的最大频点上直接取 `L(f)`，比"低频均值"更贴近器件真实工作点（docstring 第 178-184 行）。`SRF_cap` 是**系统级** SRF——同一 topology 下所有驱动的 `SRF_<nm>` 中的最小值（第 257-261 行）：任何一路先谐振都会拖累其它路的"资源前"读数（变压器次级谐振早会反射进初级）。找不到任何有限 SRF 时，`L<nm>_res` 退化为 `L<nm>_lf`（第 263-265 行），对单驱动拓扑这与 pre-M14 的"仅自身 SRF"语义逐位相同（docstring 第 229-231 行）。
- `Q<nm>_peak`：正频率范围内有限 Q 的最大值（第 251-254 行）；没有任何有限值直接 `MeasureError`。
- `SRF_<nm>`：`_srf_first_sign_flip`（第 195-211 行）——`Im(Z)` 从正到非正的**第一次**符号翻转，用线性插值给出精确频率；找不到翻转返回 `None`（不是"无穷大"，`None` 同时覆盖"频段内没谐振"和"起点已经过谐振"两种物理上完全不同的情况，消费方必须结合 `L_lf` 的正负判断，见第 195-202 行注释）。
- `k_lf`：双驱动时，`k(f)` 曲线在 `low_freq_max_hz` 内的有限值均值（第 272-274 行）。

`metrics_from_snp`（第 278-280 行）把"读文件 + 算指标"串成一步：

```python
def metrics_from_snp(snp_path: Path | str, topo: TopologyConfig) -> dict:
    freqs, s, z0 = read_touchstone(Path(snp_path), return_z0=True)
    return metrics_from_s(freqs, s, topo, z0=z0)
```

它是真正被生产入库路径调用的入口（见 `src/em_ic_opt_workflow/device_db/ingest.py:310`），**始终把文件里实际的 R 值传给 S→Z**，不再写死 50 欧姆（`docs/guide/03-device-db-and-hermes-db.md:66`）；直接调用 `read_touchstone()` 时默认仍是旧的两值返回。

`metrics_from_s`/`metrics_from_snp` 返回 `{"curves": {...}, "scalars": {...}}`，`curves` 的每个值是 `[(freq_hz, value), ...]` 全频点列表——这正是"整条 sweep 曲线"的物化前形态。频率处理是"整条网格 + 事后按需查询某频点"，不是"单频率"：EMX 侧按 `sweep_stepsize` 做等步长扫频（生产库用 1 GHz 步长，见第 5 节），`measure.py` 对每个网格频点都算一遍 L/Q/k；没有做跨频点插值——`suggest`/`query` 的 `--anchored <base>@<freq_hz>` 语义是"吸附到已有网格里最近的频点"（`_snap_to_grid`，`src/em_ic_opt_workflow/surrogate/inverse.py:391-394`），而不是插值出请求频率的精确值。

### 1.5 "measurement routes"：与 sNp 指标无关的另一个模块

`measurement_routes.py` **不在 `device_db/` 里**，而是 `src/em_ic_opt_workflow/measurement_routes.py`（包根目录）。读完全文（125 行）确认：它是**电路级多 testbench 场景下"哪个指标该由哪个 Spectre testbench 计算"的路由校验器**，与 sNp/EM 指标完全无关：

```python
def measurement_route_issues(
    *,
    metrics: MetricsConfig | None,
    waveform_exports: WaveformExportsConfig | None,
    testbenches: TestbenchesConfig | None,
) -> list[MeasurementRouteIssue]:
```
（`src/em_ic_opt_workflow/measurement_routes.py:18-23`）

它检查三件事（第 33-123 行）：单 testbench 项目里 `metrics.yaml`/`waveform_exports.yaml` 的条目不得声明 `testbench` 字段；多 testbench 项目里每条 metric/波形导出必须声明一个**已在 `testbenches.yaml` 里注册**的 `testbench`；反过来，每个声明的 testbench 至少要有一条 metric 或波形导出路由到它，否则报 `MeasurementRouteIssue`（`file`/`path`/`message` 三元组，第 11-15 行）。

**结论**：任务描述里把"measurement routes"和"sNp→metrics"放在同一条里问，但代码上这是两个不相干的概念——`measure.py` 是 EM-only 的物理量提取，`measurement_routes.py` 是通用 ic-opt-workflow 电路级多 testbench 的"指标归属哪个 TB"路由检查。DESIGN_CN.md 的旧模块迁移表已经把 `measurement_routes` 归入 `spec.py`/`space.py`/`objective.py`（`<repo>/docs/refactor/DESIGN_CN.md:250`），即并入新 `Spec` 的静态校验，与本报告讨论的 EM 查询库无关，不需要跟着 device_db 一起迁移。

---

## 2. 库 schema v4

`src/em_ic_opt_workflow/device_db/schema.py` 头部注释（第 1-8 行）："sNp 文件本身是唯一的物理真值；这个 DB 存 provenance（samples）、测量拓扑及其 parity 门状态（topologies）、可重建的物化指标缓存（metrics）"。

### 2.1 版本历史与当前活跃 schema

- `SCHEMA_VERSION = 3`（第 39 行）：legacy 库版本，`samples` 表用**表级 UNIQUE** `(family, process_profile, emx_settings_hash, params_hash, geom_version)`。
- `ACTIVE_SCHEMA_VERSION = 4`（第 40 行）：当前生产 schema，CT-less，新增 `family_contracts`/`strata` 两张目录表，把 `family_schema_revision` 也并入身份。
- `require_active_db(conn, *, operation: str)`（第 221-272 行）是**唯一的 fail-closed 守门**：`PRAGMA user_version != ACTIVE_SCHEMA_VERSION` 直接 `RuntimeError`；即使版本号对，还要逐条比对 `family_contracts` 表跟代码里 `family_catalog.ACTIVE_FAMILY_IDS`/`get_family(...).schema_revision`/`active` 是否**完全一致**（缺失家族、多余家族、revision 漂移、家族被停用，四类都分别报出来，第 251-272 行）——目的是防止"库文件版本号对了，但里面登记的家族契约是旧代码写的"这种静默漂移。`ingest_sample`/`has_sample`/`materialize_metrics`/`reingest_metrics`/`upsert_topology` 等所有写路径以及 `resolve_stratum_dataset` 都会先调用它（`src/em_ic_opt_workflow/device_db/ingest.py:150, 225, 249, 270-271, 344`；`schema.py:649`）。

### 2.2 表结构

`_ACTIVE_DDL`（`schema.py:149-196`）：

- `family_contracts`（第 150-163 行）：`family_id`(PK) / `generator_id` / `device_kind ∈ {inductor,transformer}` / `schema_revision` / `dims_json` / `int_dims_json` / `nt_dim` / `signal_ports_json` / `topology_name` / `metric_names_json` / `ct_policy CHECK(='forbidden')` / `active`。这是把 `family_catalog.py` 里 Python `dataclass` 的内容**镜像进 DB**（`_contract_row`，第 375-392 行；`_seed_active_family_contracts`，第 395-427 行，写入后立刻读回比对，不一致就 `RuntimeError`）——`require_active_db` 靠比对这张表实现"代码/DB 家族契约一致性"检查。
- `strata`（第 164-174 行）：`name`(PK) / `family_id` FK / `process_profile` / `family_schema_revision` / `generator_id` / `signal_ports_json` / `topology_name` / `active`，`UNIQUE(name, family_id, process_profile, family_schema_revision)`。一个 stratum = "一个家族在一种金属栈组合上的具体扫描面"（如 `ind_sym_m10`、`xfm_bs_apm10`）。
- `samples`（`_active_samples_table_ddl`，第 119-146 行）列：

  ```text
  id, family, stratum, params_json, params_hash, snp_path, snp_sha256,
  gds_sha256, process_profile, fixture_json, emx_settings_hash,
  emx_port_names, status, failure_reason, source, created_at,
  geom_version, family_schema_revision
  ```
  外键 `FOREIGN KEY (stratum, family, process_profile, family_schema_revision) REFERENCES strata(...)`（第 141-144 行）；`status CHECK IN ('ok','build_rejected','emx_failed')`（第 134 行）；`source CHECK IN ('sweep','optimizer','manual')`（第 136-137 行）。
- `topologies`（第 176-184 行）：`name`(UNIQUE) / `config_json`（即序列化的 `TopologyConfig`）/ `parity_status CHECK IN ('pending','passed','failed')` / `parity_max_err` / `parity_ref`。
- `metrics`（第 185-192 行）：`sample_id` FK(`ON DELETE CASCADE`) / `topology_id` FK / `name` / `freq_hz`(nullable) / `value`(nullable) / `derived_version`。`freq_hz IS NULL` 的行是标量（`Lp_res`/`Qp_peak`/`SRF_p`/`k_lf`…），`freq_hz IS NOT NULL` 的行是曲线采样点（`Lp`/`Qp`/`Ls`/`Qs`/`k`，见 `family_catalog.py:28` 的 `CURVE_METRIC_NAMES`）。`value IS NULL` 是**故意的**：非有限值（如高于扫频上限的 SRF）存 NULL，不是缺行（`ingest.materialize_metrics`，`ingest.py:317-321`）。

### 2.3 唯一身份索引："what identifies a sample"

```python
_ACTIVE_IDENTITY_INDEX_DDL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_samples_identity ON samples "
    "(family, process_profile, emx_settings_hash, params_hash, geom_version, "
    "family_schema_revision)")
```
（`schema.py:87-90`，文字版见 `docs/guide/03-device-db-and-hermes-db.md:11-14`）

即：**family + process_profile + emx_settings_hash + params_hash + geom_version + family_schema_revision** 六元组唯一确定一个 sample。逐项含义：

- `process_profile`：工艺画像字符串（如 `"n28_1p10m"`），来自 sweep plan 的 `process_profile` 字段。
- `params_hash = canonical_hash(params)`（`ingest.py:105-108`，`_canon` 把 int/float 统一转成 `"f:{float!r}"` 字符串形式后 `json.dumps(sort_keys=True)` 再 sha256，第 90-108 行）——**规范化哈希**，顺带拒绝非有限值（NaN/Inf 直接 `ValueError`，第 95-96 行）。
- `emx_settings_hash = canonical_hash(emx_settings)`：EMX 运行配置（mode/step_hz/s_impedance/parallel/three_d_metals/…)的哈希，不含每候选点独有的输出路径。
- `geom_version`：PCell 生成器代码版本（`ingest.CURRENT_GEOM_VERSION = 6`，`ingest.py:67`；`KNOWN_GEOM_VERSIONS = (1,2,3,4,5,6)`，第 72 行）。第 67-64 行的大段注释记录了 1→6 每次几何变更的边界事件（PCell 接缝修复、桥臂拐角修正、紧凑两圈闭环修复、端口-地夹具坐标统一……)。**同一组 params 在不同 geom_version 下允许并存**——这是 v3 相对 v1/v2 的关键设计变化（`schema.py:31-38` 注释）：不是覆盖旧数据，而是"同一参数点在两代几何上各测一次"都合法。
- `family_schema_revision`：家族生成器契约版本（当前六个家族全部是 `schema_revision=2`，`family_catalog.py` 逐条定义，如第 81 行 `ind_sym` 的 `schema_revision=2`）。

`resolve_stratum_dataset`（`schema.py:604-748`）是查询/建议路径**必经**的绑定校验：先核对 plan 声明的 `family`/`process_profile`/`family_schema_revision` 是否与 DB 里 `strata` 表记录的一致（第 660-681 行），再看这个 stratum 的已接受样本是否落在**单一** `(emx_settings_hash, geom_version)` 组合里（`require_single_dataset=True` 时，混了多套直接 `ValueError` 列出全部组合，第 710-722 行——docstring 明确说这是"不同的数值语义，不是更多数据"，第 566-567 行）。`pin_geom_version` 支持传具体整数，或 `"latest"`（自动选**该 stratum 里完整样本数 ≥ min_samples 的最新一代**，第 683-701 行；`min_samples` 默认 `DEFAULT_GEOM_MIN_SAMPLES = 25`，第 501 行）。

### 2.4 "accepted"/"ok" 的确切含义 —— 两层

第一层，`samples.status = 'ok'`：EMX 真正跑完且 sNp 校验通过（对照 `status IN ('ok','build_rejected','emx_failed')`）。

第二层，**"complete"（可训练）**——`complete_ok_sample_rows`（`schema.py:515-550`）：一个 `ok` 行还必须满足"这个家族契约的**全部标量**（`FamilyContract.scalar_metric_names`，`family_catalog.py:65-73`，即 `metric_names` 里排除曲线基名 `Lp/Qp/Ls/Qs/k` 之后剩下的名字）在 `metrics` 表里都有对应的 `freq_hz IS NULL` 行"（SQL 见第 541-550 行，用 `COUNT(DISTINCT m.name) = ?` 卡数量）。这是因为存在"几何已生成、EMX 已成功、但指标物化还没跑"的中间态（docstring 第 523-531 行称之为"`_materialize_guarded` 留给 resume 重试的故意中间态"）——**训练/覆盖统计只能用 complete，不能用 ok**，`hermes-db family-status`/`coverage`/`suggest`/`query` 全部走这条线（`cli.py` 里对 `complete_ok_sample_rows` 或 `resolve_stratum_dataset` 的调用见第 5.、7. 节）。

### 2.5 sNp 文件存储：并非全库统一"内容 SHA 命名"

- **sweep 主入库路径**（`source='sweep'`）：`snp_path` 存的是相对 `db_root` 的路径字符串（`ingest.py:186`，`str(snp_path.resolve().relative_to(Path(db_root).resolve()))`），**文件名沿用 sweep 产出时的原名**（`<stratum>_<sobol_idx:05d>.s<N>p`，见第 5 节），并不改名为 sha256；`snp_sha256`（`ingest.py:187`，`_sha256_file`）只是**另存一列**用于完整性校验（被 `experiments/device_db_sweep_n28/artifact_audit.py` 用来核对文件没有被篡改）。
- **optimizer 回灌路径**（`source='optimizer'`，`backflow.py`）：这里才是"用内容 SHA256 命名"——

  ```python
  dest = dest_dir / f"{snp_sha256}{snp_src.suffix}"
  ```
  （`src/em_ic_opt_workflow/device_db/backflow.py:431`，`dest_dir = db_root / "backflow" / run_root.name`，第 429 行）。注释明确解释原因（第 425-428 行）："candidate_id 在不同器件间可能重复，run 目录名在不同项目间也可能重复；只有内容能确定这份 EM 结果该被留存哪一份"，并在写入后立刻重新哈希校验（第 434-435 行）。

第 7 节会指出一处磁盘上的历史遗留数据（`outputs/backflow/ind_ct_turbo_smoke/`）文件名**不符合**这条当前规则，属于陷阱。

### 2.6 Family catalog：每家族的参数列

`src/em_ic_opt_workflow/device_db/family_catalog.py` 用一个 `frozen` dataclass 描述契约：

```python
@dataclass(frozen=True, slots=True)
class FamilyContract:
    family_id: str
    generator_id: str
    device_kind: Literal["inductor", "transformer"]
    schema_revision: int
    dims: tuple[str, ...]
    int_dims: tuple[str, ...]
    nt_dim: str | None
    signal_ports: tuple[str, ...]
    emx_port_names: tuple[str, ...]
    topology: str
    metric_names: tuple[str, ...]
    ct_policy: Literal["forbidden"] = "forbidden"
```
（`family_catalog.py:39-63`）

`_FAMILIES` 字典（第 76-173 行）当前登记 **6 个活跃家族**，`dims`（=库里的自变量坐标列，即 params_json 的 key 集合）分别是：

| family_id | dims | int_dims/nt_dim | device_kind | topology |
|---|---|---|---|---|
| `ind_sym` | `outer_diameter_um, width_um, spacing_um, turns` | `turns` | inductor | `ind_diff` |
| `xfm_bs` | `primary_outer_diameter_um, secondary_outer_diameter_um, primary_width_um, secondary_width_um, center_spacing_ratio` | 无（`nt_dim=None`） | transformer | `xfm_dual_balun` |
| `xfm_ms` | `single_outer_diameter_um, multi_outer_diameter_um, single_width_um, multi_width_um, multi_turns, center_spacing_ratio` | `multi_turns` | transformer | `xfm_dual_balun` |
| `xfm_balun` | `primary_outer_diameter_um, width_um, spacing_um, primary_turns` | `primary_turns` | transformer | `xfm_dual_balun` |
| `xfm_tw` | `outer_diameter_um, width_um, spacing_um, ring_count` | `ring_count` | transformer | `xfm_dual_balun` |
| `xfm_il` | `outer_diameter_um, width_um, spacing_um, turns` | `turns` | transformer | `xfm_dual_balun` |

（逐条定义见 `family_catalog.py:77-172`；`ACTIVE_FAMILY_IDS = tuple(_FAMILIES)`，第 175 行）

其中 `xfm_bs` 是**唯一没有 NT 维度**的家族（`nt_dim=None`）——第 4 节会说明这如何改变 `StratumGP`/`DomainGuard` 的行为。

`metric_names`：单驱动家族是 `_INDUCTOR_METRICS = ("Lp","Qp","Lp_lf","Lp_res","Qp_peak","SRF_p")`（第 16-18 行），双驱动家族是 `_TRANSFORMER_METRICS`（多出 `Ls,Qs,k,Ls_lf,Ls_res,Qs_peak,SRF_s,k_lf`，第 19-23 行）。`scalar_metric_names` 属性（第 65-73 行）过滤掉 `CURVE_METRIC_NAMES = {"Lp","Qp","Ls","Qs","k"}`（第 28 行）——即"只留标量"，供 2.4 节的 completeness 判断使用。

**"CT-less"是这份目录的顶层约束**（也是第 7 节展开的重点）：`ct_policy: Literal["forbidden"]`（第 63 行）是类型层面写死的；`is_ct_shaped_name()`（第 203-209 行，正则 `_CT_NAME_RE` 第 180 行）识别任何形如 `ct`/`ct1`/`center_tap`/`centertap` 的名字片段，`validate_no_ct_keys()`（第 212-222 行）递归检查一份 payload（params/fixture/emx_settings/manifest…）里**任何 key** 都不能是 CT 形状；`validate_sample_payload`（第 269-298 行）在入库前统一跑这些检查。

`emx_port_names` vs `signal_ports` 是**两套独立词表**（docstring 第 48-60 行明说）：`signal_ports` 是语义信号名（如 `P1/N1/P2/N2`），`emx_port_names` 是实际驱动 EMX 用的名字——五个变压器家族通过 `emx_ports_override` 把 `p01→P1` 之类映射过去，但 `ind_sym` 的 CT-less 契约就是直接用 `p01/p02` 当信号名，二者**不相等**。这也是第 7 节"端口名字典序陷阱"的前置知识：

```python
#: Every active stratum drives EMX with zero-padded positional port names
#: (``-p p01=<signal>:<ref>``); EMX sorts ports lexicographically, so the
#: padding is what pins the sNp column order. ...
_EMX_2PORT = ("p01", "p02")
_EMX_4PORT = ("p01", "p02", "p03", "p04")
```
（`family_catalog.py:31-36`）

---

## 3. hermes-db CLI 动词

入口注册：`pyproject.toml:50` `hermes-db = "em_ic_opt_workflow.device_db.cli:app"`（Typer app，`src/em_ic_opt_workflow/device_db/cli.py:84-91`）。模块头部把动词分组说明（第 2-40 行）：核心四个只读动词 `query`/`coverage`/`reingest`(写)/`parity-status`，加目录感知的 `family-status`，加反向代理两个 `suggest`/`fill-gap`，加回灌 `ingest-optimizer-run`(写)。**除 `reingest` 和 `ingest-optimizer-run` 外全部 `read_only=True` 打开 DB**（第 19-20 行）。退出码约定（第 87-91 行）：1=缺文件/无数据，2=输入或绑定错误，3=域外/不确定度拒绝，4=推荐池空。

逐个动词（都定义在 `cli.py`）：

| 动词 | 定义行 | 输入 | 输出/行为 |
|---|---|---|---|
| `query` | `cli.py:861-999`（装饰器 860） | `--db --plan --stratum` 必填；`--params JSON` 或 `--sample-id N` 二选一；`--anchored "Lp@28e9,..."`；`--max-rel-sigma`；`--pin-geom-version`；`--min-samples`；`--model-cache DIR`；`--format text|json` | 参数命中已有样本→打印 `measured`；否则对该 stratum 拟合 `StratumGP` 做 `predicted`（先过 `DomainGuard`）。`--sample-id` 走历史审计路径，仍会用 `_fetch_sample_measured` 做 family/stratum/process_profile 一致性校验（第 227-316 行），不一致直接 exit 2（M14 issue 14 修复，见第 33-40 行模块注释）。 |
| `suggest` | `cli.py:1397-1538`（装饰器 1396） | `--db --plan --stratum --spec JSON`；`-n`；`--pool`；`--seed`；`--nt-mode`/`--kernel` 覆盖；`--max-rel-sigma`；`--verify-build/--no-verify-build`；`--emit-requirement`；`--pin-geom-version`/`--min-samples`；`--format`；`--model-cache` | 薄封装，真正逻辑在 `surrogate.inverse.suggest`（第 1465-1471 行调用），打印排名候选或吐出 `--emit-requirement` 的可粘贴 yaml 片段。 |
| `fill-gap` | `cli.py:1577-1750`（装饰器 1576） | `--db --plan --stratum`；`-n`；`--region JSON`；`--seed`；`--emit-points FILE` | **只读、绝不跑 EMX**（docstring 第 1593-1594 行）：在"已达到范围盒"内用 scrambled Sobol 采样、经 family schema 的 `feasible()` 过滤、按完整身份（stratum+family+process[+emx_settings_hash][+geom_version]）判重（第 1655-1673 行），把找到的"从未测过"的新点打印/写 JSONL，并打印一段 EMX 成本报价（第 1697-1748 行，纯估算，不执行）。 |
| `coverage` | `cli.py:1149-1181`（装饰器 1148） | `--db`；可选 `--stratum` | 每个 stratum 打印 `ok/build_rejected/emx_failed` 计数、各 dim 的 achieved min/max、按 NT 分层计数、`Lp_res`/`Qp_peak`/`SRF_p` 的物化覆盖数（`_print_stratum_coverage`，第 1079-1145 行）。**不区分 geom_version**（docstring 第 79 行 in guide: "默认统计不等于指定几何代训练集")。 |
| `parity-status` | `cli.py:1187-1203`（装饰器 1186） | `--db` | 打印 `topologies` 表：`name`/`parity_status`/`parity_max_err`/`parity_ref`。 |
| `family-status` | `cli.py:1003-1045`（装饰器 1002） | `--db --family` | 区分"目录里有没有这个家族"(`catalog_status`)和"这个库里有没有已表征数据"(`characterization_status`)，并给出 `ok_samples`/`complete_samples`/`incomplete_samples`（用 2.4 节的 completeness 定义）。 |
| `legacy-audit` | `cli.py:1049-1074`（装饰器 1048） | `--db`（指向 v3 归档） | 纯只读盘点：`schema_version` + 各 family 计数；**不提供训练/推荐/写入**（guide 第 81 行）。 |
| `reingest` | `cli.py:1209-1234`（装饰器 1208） | `--db --topology --family`（唯一非 `_open_ro` 的读写动词之一） | 用已存的 sNp 对该 family 的全部 `ok` 样本**重新物化指标**（`ingest.reingest_metrics`），parity 门未 `passed` 直接拒绝（`ingest._topology_id_gated`，`ingest.py:269-285`）；**不重跑 EMX、不补历史几何证据**（guide 第 150 行）。 |
| `ingest-optimizer-run` | `cli.py:1756-1829`（装饰器 1755） | `--db --run-root --binding-json --geom-version`（无默认值，必须显式给）| 唯一另一个写动词，薄封装 `backflow.ingest_optimizer_run`，见第 4 项。 |

`--format json` 只影响 `query`/`suggest` 的**成功**输出（`OUTPUT_SCHEMA_VERSION = "1"`，`cli.py:112`），错误路径始终保持文本 + 既有退出码（多处注释强调，如 `cli.py:243-246`）；`suggest --format json` 与 `--emit-requirement` 互斥（`cli.py:1455-1459`）。

`--pin-geom-version`（`_geom_pin_option` 回调 `cli.py:371-375`，底层 `schema.parse_geom_version_pin`，`schema.py:504-512`）接受正整数或字符串 `"latest"`；`--anchored` 语法是逗号分隔的 `<base>@<freq_hz>`（`base ∈ {Lp,Ls,Qp,Qs,k}`），由 `surrogate.inverse._parse_anchored_spec` 统一解析（`inverse.py:332-373`），`query`/`suggest` 共用同一套"网格吸附 + 近 SRF 训练侧排除"逻辑（详见第 4 节）。

---

## 4. Surrogate（StratumGP / DomainGuard / inverse.suggest）

### 4.1 什么是一个 "stratum"

`src/em_ic_opt_workflow/surrogate/model.py` 顶部原话（第 3-4 行）：

> "One StratumGP per (stratum x scalar metric); a stratum is family x metal body — metal stacks are never mixed into one response surface."

即：一个 stratum = 一个家族在**一种具体金属栈组合**上的扫描面（如 `ind_sym_m10` = ind_sym 家族在 M10/M9 金属栈上；`ind_sym_ap` = 同家族在 AP/M10 上）；不同金属栈永远不共享同一个响应面/同一个 GP。

### 4.2 特征（feature map）

`StratumGP.__init__` 签名：

```python
def __init__(self, *, dims: list[str], ranges: dict[str, tuple],
             log_target: bool = False, nt_mode: str = "joint",
             kernel: str = "rbf", nt_dim: str | None = _NT_DIM,
             feature_map: str = IDENTITY_FEATURE_MAP):
```
（`src/em_ic_opt_workflow/surrogate/model.py:133-136`，类定义第 129 行）

默认 `feature_map="identity"`——直接对 `dims` 做 min-max 归一化（`_scale`，第 170-193 行，按 `ranges` 缩放到 `[0,1]`）。两个例外由 `feature_map_for_target(family, target)`（第 80-97 行）选出：

- `xfm_bs` 的 `k`/`k_lf`：映射到 `XFM_BS_DIMENSIONLESS_FEATURE_MAP`（`_xfm_bs_dimensionless_features`，第 195-259 行）——把 5 个原始几何量转成 `log_outer_diameter_mean, log_outer_diameter_ratio, primary_width_over_diameter, secondary_width_over_diameter, center_spacing_ratio` 五个"无量纲"特征。理由是耦合系数在尺度上近似不变。
- `xfm_ms` 的 `k`/`k_lf`：同理映射到 `XFM_MS_DIMENSIONLESS_FEATURE_MAP`（第 261-329 行），多保留一维 `multi_turns`。docstring 引用了一次真实 CV 实验（第 92-96 行）："per_nt identity 2.9-3.1% vs 这个映射 0.7-0.9% 中位相对误差"（这是模型选型证据，不是 N28 工艺数值，可以引用）。
- 非恒等映射与 `nt_mode="per_nt"` 冲突时**自动降级为 joint**（第 148-156 行），而不是报错——因为无量纲映射已经把 NT 折进特征空间，再按层拆分既浪费样本又重复信息。

### 4.3 核函数

`_make_kernel(n_dims, kernel)`（`model.py:117-126`）：`kernel ∈ {"rbf", "matern52"}`，都是 `ConstantKernel(1.0,(1e-3,1e3)) * base + WhiteKernel(1e-6,(1e-10,1e-1))`；`base` 是各向异性 `RBF(length_scale=[0.3]*n_dims, (1e-2,1e2))` 或 `Matern(nu=2.5, ...)`。底层是 `sklearn.gaussian_process.GaussianProcessRegressor`（`normalize_y=True, n_restarts_optimizer=4, random_state=0`，`_fit_joint`，第 347-350 行）。

### 4.4 训练数据选择与 NT 分层

`nt_mode="per_nt"`（`_fit_per_nt`，第 353-380 行）：按 `nt_dim`（如 `turns`）四舍五入到整数分组，每层单独 fit 一个"去掉 NT 维"的子 `StratumGP`；**样本数 < `MIN_NT_SAMPLES = 25`（第 131 行）的层不拟合**，记录进 `unavailable_nt: dict[level, n]`（第 371-373 行），预测该层直接抛 `ValueError`（`_predict_per_nt`，第 436-441 行，报文带上 `unavailable_nt` 全貌）。`nt_dim=None` 的家族（`xfm_bs`）在 `suggest`/`query` 里被强制 `effective_nt_mode="joint"`（`inverse.py:744-748`、`cli.py:440-443`），因为压根没有"层"的概念。

`log_target`：对 `Lp_res/Qp_peak/Ls_res/Qs_peak` 为 True（`inverse.py:145-146` 的 `_LOG_TARGET` 字典），对 `k_lf` 为 False（测得的耦合可能接近零甚至为负，取对数无意义）。log 目标下 `predict()` 返回的 `sigma` 是**一阶 delta 近似**（`model.py:386-390` docstring 明确警告："not exact... treat sigma as indicative uncertainty only"）；精确 k-sigma 区间由 `prediction_bounds`/`predict_bounds`（第 100-114 行、397-410 行）在 log 空间构造后再转回原始空间，供 `suggest` 的保守约束判定使用（QL-11，`inverse.py:1035-1041`）。

### 4.5 缓存（model_cache.py）

`src/em_ic_opt_workflow/surrogate/model_cache.py` 是**选配**的指纹式模型缓存：`fit_cached(cache_dir, payload, fit_fn)`（第 122-132 行）——`cache_dir=None` 直接 `(fit_fn(), "off")`；否则按 `fingerprint(payload)` 查 `.pkl`，命中就返回**同一个**拟合对象（bit-identical 预测），未命中就 fit 后落盘。`payload` 由 `target_payload(...)`（第 63-87 行）拼出，覆盖到会影响拟合结果的**一切**：`provenance`（stratum/family/process_profile/family_schema_revision/emx_settings_hash/geom_version/n_ok/n_train，来自 `StratumDataset.provenance()`）、逐样本 `data_digest`（`data_digest`，第 49-60 行，对每个 `(sample_id, canonical(params), y值)` 排序后整体 sha256——一个样本的一次指标变化 0.1% 都会失效缓存）、模型语义（`dims/ranges/nt_dim/target/kind/log_target/feature_map/nt_mode/kernel/snapped_freq_hz`）、代码环境（`measure.DERIVED_VERSION`、`sklearn.__version__`、本模块自己的 `MODEL_CODE_VERSION = 1`，第 46 行）。命中/未命中/关闭三态由 `cli.py`/`inverse.py` 聚合成一行 `model_cache: hit|miss|off`（`aggregate_status`，第 135-141 行）输出。**Pickle 安全边界**明确写在 docstring 里（第 28-31 行）：只从调用方显式传入的目录加载，从不隐式/共享路径读取。

### 4.6 DomainGuard：拒绝什么、为什么

`src/em_ic_opt_workflow/surrogate/domain.py`，`class DomainGuard`（第 151 行起）。`check(params)`（第 299-349 行）**按顺序**验四条，第一条不过直接 `raise OutOfDomainError`（不会继续查后面几条）：

1. **Criterion 1 — achieved box**：每个 dim 的值必须落在**已接受样本的实际 min/max**内（不是 plan 里名义 `ranges`！`self._achieved_min/_achieved_max` 来自样本矩阵本身，第 198-199 行）——achieved box 几乎总比名义 range 更紧。
2. **Criterion 2 — NT 层样本量**：`nt_dim` 非 None 时，查询点的整数 NT 层必须有 `>= min_per_level`（默认 `DEFAULT_MIN_PER_LEVEL = 25`，第 71 行）个样本，镜像 `StratumGP.MIN_NT_SAMPLES`；非整数 NT 值直接拒绝（第 320-324 行：NT 层是离散整数，不允许四舍五入吸附）；`nt_dim=None` 时**整条跳过**（第 317-318 行）。
3. **Criterion 3 — 凸包**：连续维（排除 NT 维）在 min-max 缩放后的 **Delaunay 凸包**内（`_hull_for_level`，第 244-259 行，按 NT 层懒建并缓存；`nt_dim=None` 时是**全局单一凸包**，用 `_GLOBAL_LEVEL` 哨兵键，第 118 行）。凸包退化（共面/共线，`QhullError`）时 **fail-closed**：判定为"未覆盖"而不是跳过检查（docstring 第 32-34 行）。这一条存在的原因是"achieved box 内部可能有凹陷/空洞，只看逐轴范围会漏判"。
4. **Criterion 4 — GP 相对不确定度**（不由 `DomainGuard` 状态持有，是静态方法）：

   ```python
   @staticmethod
   def check_sigma(mu: float, sigma: float, rel_max: float = 0.15) -> bool:
   ```
   （`domain.py:351-361`，`DEFAULT_SIGMA_REL_MAX = 0.15`，第 77 行）：`|sigma|/max(|mu|,1e-30) <= rel_max`；`mu==0` 且 `sigma!=0` 视为失败（fail-closed）。docstring 明确说这一条问的是"模型在这一点自信吗"，与前三条"这里有数据吗"是**不同的问题**，两者都要过，互不替代（第 41-50 行）。批量形式 `sigma_mask`（第 104-113 行）供 `suggest` 一次性过滤整个候选池。

`OutOfDomainError`（第 130-148 行）携带 `criterion`(1-4)、`reason`、**3 个最近邻样本**（`_nearest`，缩放欧氏距离，第 263-267 行）、`suggested_fill_points = [query, clamped_query]`（`clamped_query` 是逐维裁剪到 achieved box 的"最近可信点"，第 277-285 行）——这是"域外时给出可操作证据"的设计。`DomainGuard.nearest(params, k=3)`（第 289-297 行）把这套最近邻查找单独暴露给 `suggest` 的 SRF 5-NN 检查复用。

### 4.7 反向推荐（`inverse.suggest`）

`src/em_ic_opt_workflow/surrogate/inverse.py`，签名：

```python
def suggest(conn, plan: dict, stratum_name: str, spec: dict, *, n: int = 5,
           pool: int = 8192, k_sigma: float = 2.0, seed: int = 0,
           nt_mode: str | None = None, kernel: str | None = None,
           max_rel_sigma: float = DEFAULT_SIGMA_REL_MAX,
           verify_build: bool = True,
           model_cache: str | None = None,
           pin_geom_version: int | str | None = None,
           min_samples: int = DEFAULT_GEOM_MIN_SAMPLES) -> "SuggestResult":
```
（`inverse.py:651-658`；`DEFAULT_NT_MODE="per_nt"`/`DEFAULT_KERNEL="matern52"`，第 163-164 行，来自一次真实库 CV 基准，见 4.8 节）

- **输入（`spec`）**：`{metric_name: {"min"|"max"|"target"+"tol": ...}}`；key 可以是普通标量名（`Lp_res`/`Qp_peak`/…)，也可以是 **f0-锚定目标** `"<base>@<freq_hz>"`（`base ∈ {Lp,Ls,Qp,Qs,k}`，双绕组家族才有全部 5 个，单绕组只有 `Lp,Qp`——`_ANCHORED_BASES_1DRIVE`/`_ANCHORED_BASES_2DRIVE`，`inverse.py:288-289`），由 `_parse_anchored_spec`（第 332-373 行）在**任何 DB 访问之前**校验合法性。
- **算法流水线**（docstring 第 9-35 行 + 代码 第 651-1191 行逐段对应）：
  1. 只读加载该 stratum 的 `complete` 样本（`_load_ok_samples`→`complete_ok_sample_rows`，第 236-249 行）；**先解析 `resolve_stratum_dataset` 绑定**（含 `PlanConfigurationBinding` 一致性校验，第 752-759 行）。
  2. 对每个 f0-锚定 key：网格吸附（`_snap_to_grid`）+ 训练侧近-SRF 排除（`SRF_ANCHOR_MARGIN = 1.25`，第 301 行：训练样本 `min(finite SRF_p,SRF_s) <= 1.25*f0` 就被剔除，`_build_anchored_target_arrays`，第 414-478 行）后单独 fit 一个 `StratumGP`（第 786-844 行）。
  3. 对每个标量 GP 目标（`Lp_res/Qp_peak`[/`Ls_res/Qs_peak/k_lf`]）fit 一个 `StratumGP`（第 909-944 行），配 `model_cache_mod.fit_cached`。
  4. **候选池**：scrambled Sobol（`scipy.stats.qmc.Sobol`）经该 stratum 的 `FamilySchema.draw()`/`feasible()`（第 968-985 行）——**候选生成用的是家族自己的 Sobol/schema 采样器，与 sweep 阶段共用同一套 `family_schemas.py`**，保证"推荐出的候选一定是这个库当初采样时也会采到的可构造点"。
  5. `DomainGuard.check()` 过滤域外候选（第 990-999 行）。
  6. 按 NT 层**批量** `predict()`（不是逐候选调用，第 1012-1048 行），`sigma_mask` 应用 criterion 4。
  7. **保守约束检查**：`min` 需要 `mu - k_sigma*sigma >= v`；`max` 需要 `mu + k_sigma*sigma <= v`；`target±tol` 需要整个 `[mu-k*sigma, mu+k*sigma]` 落在目标窗口内（`_bounds_satisfy`，第 556-574 行）；`SRF_p`/`SRF_s` 没有 GP，直接查该候选**5 个最近邻真实样本**的 `SRF_p`/`SRF_s`，全部满足才算通过（`_srf_satisfies`，第 577-598 行；`NULL` 满足 `min` 但不满足 `max`/`target`，第 587-596 行）——锚定目标若没显式约束对应 SRF，`suggest` 会**自动**加一条 `min = 1.25*max(锚定频率)` 的 SRF 约束并在 `notes` 里说明（第 878-891 行）。
  8. 按 `(target 距离, 不确定度)` 排序（`_primary`/`_secondary`，第 1099-1115 行），再贪心多样化（缩放空间最小间距 `DIVERSIFY_MIN_SPACING = 0.05`，第 171 行）。
  9. **可选真实构建验证**（`verify_build=True` 默认开）：对选中候选跑**真实生成器 + 5 类核心 DRC**（`_verify_candidate_build`，第 604-646 行）——用的是与 sweep 相同的 `schema.build_config()` → `generator.generate()` → `drc_audit.audit_gds()` 链路，M1 层被排除（EM 地夹具环必然触发 M1 最大线宽，第 616 行注释）。失败的候选被丢弃并计入 `notes`，预算 `verify_budget = max(4*n, 8)`（第 1131 行）。
- **输出**：`SuggestResult`（`list` 子类，`class SuggestResult(list):`，第 312-330 行）——每个候选 `{"params", "predicted": {metric: {"mu","sigma"[,"freq_hz"]}}, "evidence": [{"sample_id","params","measured"}], "verification": "build+core-drc"|"surrogate-feasible"}`；外挂属性 `.notes`（advisories）、`.dataset`（`StratumDataset`，供 CLI 打印 provenance）、`.model_cache_status`、`.build_geom_version`。**空列表**严格代表"spec 在已表征域内无可行解"（docstring 第 37-43 行），其它任何数据缺失/未知指标都是 `ValueError`，不会伪装成空列表。

### 4.8 闭环 harness 与基准测试（来自 docs/.scratch，未重跑）

**GP nt_mode/kernel 基准**（`experiments/device_db_sweep_n28/outputs/GP_BENCHMARK.md`，25 行，5-seed 均值 `cv_report` 中位相对误差）：

| combo | 总体均值 |
|---|---:|
| joint-rbf | 0.0239 |
| joint-matern52 | 0.0197 |
| per_nt-rbf | 0.0218 |
| **per_nt-matern52** | **0.0180**（胜者） |

这就是 `inverse.py:163-164` `DEFAULT_NT_MODE="per_nt"`/`DEFAULT_KERNEL="matern52"` 的来源。

**真实 EMX 闭环校准战役**（`<em-opt>/.scratch/em-closed-loop-validation/CAMPAIGN.md`，spec 见同目录 `spec.md`）：用户 2026-07-27 批准总预算 300 组真实 EMX，迭代协议是 `propose(surrogate.inverse.suggest) → 真实 EMX(sweep_driver --points-file) → ingest → report(z/2σ覆盖率) → 诊断 → 代码优化 → 下一批`。已跑 3 批（累计 58/300）：

| 批次 | 点数 | 2σ 覆盖率 | \|z\| 中位 | 批间代码变更 |
|---|---|---|---|---|
| 1 | 18/18 | **99.0%**(98/99) | 0.57 | 无（基线） |
| 2 | 20/20 | 96.6%(56/58) | 0.60 | report 原生 by_class 聚合 |
| 3 | 20/20 | 87.8%(43/49) | 0.44 | `xfm_ms` 无量纲 k 映射(commit `12bd1fb`) |

阶段小结（`CAMPAIGN.md:100-106`）：累计 206 读数，2σ 覆盖 95.6%——"正合名义(95%)，不做全局 sigma 校正"；一项数据驱动改进（`xfm_ms` 的 `k_lf`：per_nt-identity 3.09%/2.91% CV 中位误差 → joint-无量纲映射 0.90%/0.74%，真实 EMX 验证后从"6.0% 越线"降到"0.09%-1.29%，中位 ~0.4%"）；一项负结果如实记录：`xfm_ms` 的 `Qp_peak/Qs_peak` 中位误差 ~2.8%-3.0% 判定为 **GP 容量/损耗物理下限**，"不做无据变更"（`CAMPAIGN.md:93-98`）。剩余 242 组预算未用。**留出 CV 的名义基线是 91.9% 2σ 覆盖**（`CAMPAIGN.md:27`），闭环实测在推荐点附近反而更好（99.0%/96.6%），说明"suggest 推荐的良支撑域内点"比"留出 CV 的边缘难点"更容易预测准。

**geom5 三家族复核**（`docs/reports/2026-09-12-ind-ms-model-and-library-review.md`）报告了一次独立评估：400 个宽域随机点里 `--pin-geom-version 5` + 1GHz 条件下 209 个给出预测、191 个凸包外拒绝，**覆盖率 52.25%**（旧 pin3 是 32%，同一分母）；预测误差（第 57-61 行表格）：`Lp` 209 点平均相对误差 0.2210%(最大 4.5804%)，`Ls` 81 点 0.1675%(最大 1.89265%)，`k` 81 点平均**绝对**误差 0.003128(最大 0.021807)。同一份报告明确记录了一个**未解决的局部低估**：`xfm_ms_apm10_014` 的 `Lp`/`Ls` 误差达到模型 `sigma` 的 **10.6/6.5 倍**，原始 sNp 独立复算确认测量无误——"不能声称预测置信区间已经校准通过"（该文件第 65 行；也被 `docs/guide/03-device-db-and-hermes-db.md:158` 列为"当前边界"之一）。

---

## 5. 扫描计划格式与 campaign 目录

### 5.1 Sweep plan yaml 格式

以 `experiments/device_db_sweep_n28/sweep_plan_expansion_ind.yaml`（全文 70 行）为例，顶层字段：

```yaml
db_path: outputs/device_db.sqlite
process_profile: n28_1p10m
proc_file: ../../gdsgen_ref/n28_proc/tsmcN28_1p10m.proc
plugin_module: builtin:clean_port
sampler_module: sampler.py
ranges: {outer_diameter_um: [...], width_um: [...], spacing_um: [...], turns: [...]}
rounding: {outer_diameter_um: 1.0, width_um: 0.1, spacing_um: 0.1}
fixed: {lead_length_um: 20.0}
opening_rule: {max_um: ..., safety: ..., version: scaled_v1}
ground_fixture: {inner_margin_um: ..., ring_width_um: ..., stub_length_um: ..., stub_chamfer_um: ...}
emx: {mode: quasistatic, sweep: {start_hz, stop_hz, step_hz}, s_impedance, parallel,
      simultaneous_frequencies, max_memory_gb, edge_width_um, thickness_um, max_splits,
      via_separation_um, extra_args, timeout_s}
pool: {jobs, mem_floor_gib, emx_memory_warn_gib, emx_memory_pause_gib, emx_memory_limit_gib,
       emx_memory_poll_interval_s, emx_terminate_grace_s, max_emx_failure_frac}
drc_gate: true
accepted_per_stratum: <int>
sub_batch_size: <int>
seed: <int>
strata:
  - {name: ind_sym_m10, generator: clean_port_ind_sym, family: ind_sym,
     top_metal: "10", bottom_metal: "9", three_d_metals: [M10, M9],
     port_order: [P1, N1], topology: ind_diff,
     emx_ports_override: [{name: p01, signal: P1, reference: G01}, ...]}
  - {name: ind_sym_ap, ...}
```

（完整原文见 `experiments/device_db_sweep_n28/sweep_plan_expansion_ind.yaml:1-70`；路径字段 `proc_file`/`sampler_module`/`plugin_module` 支持相对路径，由 `src/em_ic_opt_workflow/device_db/plan_paths.py:31-48` 的 `resolve_plan_paths()` 统一解析为绝对路径，`db_path` 例外——由 sweep driver/cv_gate 自己相对 plan 文件目录解析，`plan_paths.py:17-21` 注释说明。）

其它 plan 文件（`sweep_plan_xfm.yaml`/`sweep_plan_expansion_xfm.yaml`/`sweep_plan_v4_il.yaml`）结构相同，区别在于变压器家族的 `ranges` 常常放在**每个 stratum 自己**的 `ranges` 字段而不是顶层（`_stratum_ranges()` 两处实现，`cli.py:191-196` 与 `inverse.py:224-233`，逻辑一致："stratum 有自己的 `ranges` 就用它，否则退回 plan 顶层 `ranges`，再叠加可选的 `ranges_override`"）。

### 5.2 采样方法：Sobol + 拒绝重采样，不是网格

`experiments/device_db_sweep_n28/sampler.py` 与 `experiments/device_db_sweep_n28/family_schemas.py` 共同实现"schema-driven sampler"：

```python
def family_accepted_stream(stratum: str, schema, ranges: dict, *,
                           opening_rule: dict, seed: int, count: int,
                           clean_port_mod=None, skip_indices: set[int] | None = None):
    mod = clean_port_mod if clean_port_mod is not None else _load_clean_port()
    sob = qmc.Sobol(d=len(schema.dims), scramble=True, seed=_stratum_seed(stratum, seed))
    yielded, idx = 0, 0
    while yielded < count:
        u = sob.random(1)[0]
        idx += 1
        p = schema.draw(u, ranges)
        if not schema.feasible(p, opening_rule, mod):
            continue
        ...
        yielded += 1
        yield idx, p
```
（`experiments/device_db_sweep_n28/sampler.py:122-146`；`_stratum_seed` 用 `sha256(f"{stratum}:{seed}")` 派生每个 stratum 独立但确定的种子，第 97-99 行）

用的是 **`scipy.stats.qmc.Sobol`（scrambled）加拒绝重采样**，每个家族一个 `FamilySchema`（`experiments/device_db_sweep_n28/family_schemas.py:133-190` 定义基类协议）：

```python
@dataclass(frozen=True)
class FamilySchema:
    family: str
    dims: tuple[str, ...]
    rounding: dict[str, float]
    int_dims: tuple[str, ...] = ()
    nt_dim: str | None = None

    def draw(self, u, ranges: dict) -> dict: ...      # 单位立方体 Sobol 行 -> 取整/规整参数
    def feasible(self, params, opening_rule, clean_port_mod) -> bool: ...  # 快速解析可行性
    def build_config(self, params, plan, stratum, opening_rule, clean_port_mod) -> tuple[dict, dict]: ...  # -> (生成器配置, fixture provenance)
    def emit_requirement(self, params, plan, stratum, opening_rule, clean_port_mod, box) -> dict: ...
```

`draw()`（第 141-157 行）对整数维用 `min(int(lo)+int(u*(hi-lo+1)), hi)` 离散化，连续维按 `rounding` 网格步长四舍五入（`round_grid`，第 78-79 行）——**没有网格(grid)采样模式**；`get_family_schema(family_id)`（第 910 行起）按目录分发到六个具体子类（`InductorSchema`/`XfmBsSchema`/`XfmTwSchema`/`XfmIlSchema`/`XfmMsSchema`/`XfmBalunSchema`）。`sampler_module`/`family_schemas_module` 都是**按绝对路径动态加载**（`_load_sampler`，`inverse.py:173-211`；`_load_family_schemas`，`sampler.py:46-58`），并缓存进 `sys.modules` 的固定 key，保证同一进程内所有调用方共享同一份"clean_port"生成器模块对象（否则会出现"两份几何生成器判定标准不一致"的问题，`sampler.py:64-75` 注释明说）。

### 5.3 一个 campaign run 目录里每个点的确切文件

驱动器 `experiments/device_db_sweep_n28/sweep_driver.py`。目录命名：

```python
workdir = (db_root / "sweep" / stratum_name
          / f"{sobol_idx:05d}_{params_hash[:8]}")
```
（`sweep_driver.py:1087-1088`；`params_hash = ingest.canonical_hash(params)`）

即 `outputs/sweep/<stratum_name>/<五位十进制 Sobol 序号>_<params_hash 前8位hex>/`。`_run_point`（第 299-398 行）在这个目录里按阶段写：

1. **几何生成成功**即写：
   - `<stratum_name>_<sobol_idx:05d>.gds`（第 308 行，`generator.generate(cfg, outdir=workdir, gds_name=...)`）
   - `emx_ports.txt`（生成器建议的 `-p` 参数行，按端口名字典序排列，例：`-p N1=N1:G02` / `-p N2=N2:G04` / `-p P1=P1:G01` / `-p P2=P2:G03`）
   - `geometry_manifest.json`（`generator_id` + 完整 `geometry.config`（真实使用的每个生成器字段）+ `suggested_emx_ports` + `via_landing_audit`）
2. **通过 `drc_gate`（若启用）后真正调用 EMX** 才额外写：
   - `emx_manifest.json`（`write_emx_manifest`，第 367-368 行；含完整 argv 与结构化 `EmxRunConfig`：mode/ports/sweep/three_d_metals/process_file/…)
   - `emx.log`（EMX 原始 stdout/stderr）
   - `<stratum_name>_<sobol_idx:05d>.s<N>p`（Touchstone，N=2 对 `ind_sym`，N=4 对五个变压器家族；路径见第 336 行）
   - `emx_run_report.json`（`returncode`/`status`/`resource_usage`(峰值内存)/`snp_validation`（端口数校验）)
3. **可选 `resume_gate/` 子目录**：仅当**重跑/续跑**并且该参数点在 DB 里已有 `status='ok'` 记录、且 `drc_gate` 打开时才出现（`sweep_driver.py:1021-1052`）——它是"用当前 PCell 代码重新生成一份几何 + 重新过一遍 DRC 门"的**审计快照**，不是原始测量证据；内容是 `clean_port_<generator后缀>.gds` + 它自己的 `emx_ports.txt`/`geometry_manifest.json`。若重审计失败(`resume_gate_refused`)，库里的原始行**保持不动**，仅继续找新点补足配额。
4. **没有单独的 per-point `metrics.json`**：派生标量/曲线只在成功入库后写进（gitignored 的）SQLite `metrics` 表，由 `ingest.materialize_metrics`（`src/em_ic_opt_workflow/device_db/ingest.py:288-326`）在拿到 `sample_id` 后调用 `measure.metrics_from_snp(Path(db_root)/row["snp_path"], topo)` 计算——**按 DB 的整数 `sample_id` 索引，不按 `<idx>_<hash>` 目录名索引**。要为某个 campaign 目录重建"回放测试"用的指标，需要：(a) 直接读该库的 `metrics` 表（本机可用，但 gitignored、不进版本库），或 (b) 自己对目录里的 `.sNp` 调 `measure.metrics_from_snp()`，配上该家族登记的 `TopologyConfig`（`ind_diff`/`xfm_dual_balun`，定义见 `sweep_driver.py:146-150`）。

失败点的三种落地形态（`_run_point` 返回 `outcome`，映射到 `samples.status`；映射逻辑在 `sweep_driver.py:1137-1224`）：
- 生成器在写出任何文件前就抛异常 → `outcome="build_rejected"`，目录**完全为空**（`workdir.mkdir()` 已执行但没有后续写入，第 304、309-311 行）。
- 生成器成功但 `drc_gate` 审计不过 → `outcome="drc_rejected"`，落库时仍记 `status="build_rejected"`（`failure_reason` 前缀 `"drc_gate:"`，第 1153-1187 行注释解释"samples 表没有专门的 drc_rejected 状态，用 build_rejected 复用同一"重采样而非重试"语义，但在驱动器自己的计数器里单独记一栏方便区分"）；目录里有 `geometry_manifest.json`/`emx_ports.txt`/GDS，但没有 sNp/EMX 相关文件。
- EMX 本身失败 → `outcome="emx_failed"`（磁盘上本次核对的两棵历史 sweep 树里没有样本落到这一支，全部 EMX 调用都是 `status: pass`）。

### 5.4 磁盘上实际存在的记录目录（本机核实，均 gitignored）

`experiments/device_db_sweep_n28/.gitignore` 只有一行 `outputs/`（`experiments/device_db_sweep_n28/.gitignore:1`），`git check-ignore` 核实**整棵 `outputs/` 树**（GDS、sNp、所有 JSON、sqlite 本身）都不进版本库——克隆一份新 checkout 不会带着这些数据。以下计数/大小是本机 `find`/`du` 现场核实（2026-09-22）：

**`experiments/device_db_sweep_n28/outputs/sweep/`**（1.2G，12 个 stratum 目录，共 **9345** 个 `<idx>_<hash>` 点目录）：

| stratum | 点目录数 | 大小 |
|---|---:|---:|
| `ind_sym_ap` | 498 | 30M |
| `ind_sym_m10` | 482 | 39M |
| `xfm_balun_ap` | 511 | 59M |
| `xfm_balun_m10` | 486 | 71M |
| `xfm_bs_apm10` | 425 | 54M |
| `xfm_bs_m10m9` | 434 | 55M |
| `xfm_il_ap` | 1512 | 108M |
| `xfm_il_m10` | 1421 | 155M |
| `xfm_ms_apm10` | 812 | 122M |
| `xfm_ms_m10m9` | 818 | 335M |
| `xfm_tw_ap` | 1185 | 77M |
| `xfm_tw_m10` | 761 | 114M |

9345 个点目录里：**7028** 个有 `geometry_manifest.json`/`emx_ports.txt`/GDS（几何生成成功）；其中 **6300** 个进一步有 `emx_manifest.json`/`emx.log`/`emx_run_report.json`/`.sNp`（现场核对全部 6300 份 `emx_run_report.json` 的 `status` 字段**都是 `"pass"`**）；其余 **728** 个只到几何这一步（`drc_gate` 拒绝）；剩下 **2317** 个点目录是**完全空的**（生成器在写任何文件前就失败，见 5.3 节的第一种失败形态）。`728 + 2317 = 3045`，与 `docs/guide/07-device-inventory.md:37` "另保留3045条历史`build_rejected`" **精确吻合**；`6300` 与该文档同一行"9722条ok"里"原6300条ok"的措辞（该指南文件第 64 行"发布保留原6300条ok与3045条历史拒绝，旧9345行没有重写"）也精确吻合——即：**`outputs/sweep/` 就是当前生产库最早一批 9345 行（geom_version 2-4 为主）的原始物理凭证**，2026-09-12 之后新发布的 geom5 增量（+3422 行）**不在这棵树里**，其证据分散在 `.scratch/three-family-models-2026-09-12/`（如 `campaign-C/`、40 个 `C-rebuild-manifest.json` 里的真实重跑）与 `experiments/device_db_sweep_n28/outputs/releases/three-family-current-20260912(-C)/samples/rebuild_<旧sample_id>/`（40 个目录，本机核实），目录命名规则与 5.3 节完全不同（按旧 sample_id 命名，不是 `<sobol_idx>_<hash>`），**不适合直接套用同一份回放测试逻辑**。另有 `resume_gate/` 子目录 **2798** 个（審計快照，非测量证据，见 5.3 节第 3 点）。

其它值得记录的磁盘对象（同样 gitignored）：

- `experiments/device_db_sweep_n28/outputs/device_db.sqlite`（451M）：当前生产库，`PRAGMA user_version=4`；按 `docs/guide/07-device-inventory.md:37` 载"9722条ok + 3045条build_rejected = 12767行"，`ARTIFACT_AUDIT.json`（见下）确认 `audited_ok_samples=9722`、`conclusion="pass"`、`error_count=0`，生成时间 `2026-09-11T19:44:11Z`——**截至今天(2026-09-22)生产库仍是 geom5**，还没有切到 geom6。
- `experiments/device_db_sweep_n28/outputs/device_db.geom6-port-contract-20260921.candidate.sqlite`（603M）：geom6 边界（`ingest.py:51-64` 注释所述"port-lattice ground-fixture fix", commit `fdddb01`, 2026-09-21）的**候选库**，尚未切换为生产库（与 MEMORY 记录"候选库3358行第六代自检通过；只差发布到生产库"一致）。
- `experiments/device_db_sweep_n28/outputs/ARTIFACT_AUDIT.json`：`experiments/device_db_sweep_n28/artifact_audit.py`（docstring 第 1-33 行）产出的**全库产物审计**——逐样本重新核对 sNp/GDS sha256、GDS 单一 topcell 命名、EMX manifest 的固定网格+资源包络参数、双词表端口契约、`emx_run_report` 的 returncode/状态、`geometry_manifest` 的 CT-less 与 params 往返一致性；`query`/`suggest` 把它的时间戳 + db sha 展示为 `last_full_audit:`（`cli.py:122-149`）。
- `experiments/device_db_sweep_n28/outputs/backflow/ind_ct_turbo_smoke/`（1.1M，20 个 `candidate_000001.s3p` … `candidate_000020.s3p`）：M10bcd 设计文档里"验收：把真实 M9 optimizer run（`experiments/m9_optimizer_inductor_smoke/ind_ct_turbo_smoke`）灌进库"的验收产物（见 `docs/superpowers/specs/2026-07-10-m10bcd-surrogate-cli-backflow-design.md:129-133`）；**文件名不符合当前 `backflow.py:431` 的内容 SHA256 命名规则**，是early-version遗留，第 7 节详述。
- `experiments/device_db_sweep_n28/outputs/closed_loop/{batch2_ind,batch2_xfm,batch3_ind,batch3_xfm,real_ind,real_xfm}/`（共 216K）：`closed_loop.py` 的 `propose`/`report` 产物（`points.jsonl`/`proposals.json`/`report.json`），对应第 4.8 节战役台账里的 3 个已跑批次。
- `experiments/device_db_sweep_n28/outputs/releases/{geom6-port-contract-20260921(1.1G), three-family-current-20260912(501M), three-family-current-20260912-C(819M)}/`：几何代迁移/发布的**一次性**留证目录（`candidate-origin.json`/`*-registration.json`/`samples/`/`reuse*/`/`holdout*/`，外加一份切换前备份 `device_db.before-geom5.sqlite`），不是可复用的 sweep campaign 格式。

---

## 6. 映射提案：每项能力落在新架构的哪个概念上

对照设计契约 `<repo>/docs/refactor/DESIGN_CN.md` 第 4.1 节已经写下的结论（原文，第 197 行）：

> "查询库/逆向推荐不进评估链，是另一个 Points 源（`points.from_db`）或 Suggester。"

以及该节给出的 em-opt 流水线：`pcell → emx → bind_nport → spectre → ocean → extract`（第 191 行），本报告逐项对照：

| 能力(旧) | 落在(a)EM extract 阶段 / (b)points 源 / (c)suggester / (d)分析报告块 / (e)独立CLI | 理由与新包最小接口 |
|---|---|---|
| **measure**（`measure.py::metrics_from_snp/metrics_from_s`） | **(a) extract 阶段**，且是 em-opt 流水线里 `extract` 的**EM-only 变体**：`pcell → emx → extract`（跳过 `bind_nport/spectre/ocean`，因为纯器件表征没有宿主电路） | 纯函数、无 DB 依赖，天然契合 `Stage.run(inp, ctx)` 契约（设计契约 `DESIGN_CN.md:165-172`）。最小输入：一个 sNp 文件路径（或字节）+ 参考阻抗（已内嵌在 Touchstone 里）+ 一份 `TopologyConfig`（drives/drive_names/grounded_ports，per-family 静态配置，不随点变化）。**待决问题**：新引擎的 `Stage.level` 只有 `"point"`/`"child"` 两级（`DESIGN_CN.md:167`）；EM-only 表征没有 tb×corner 的"child"概念（一个点只有一次 EMX、一份 sNp），应该建模成 `level="point"` 的一次性 stage，而不是套用电路评估的 `level="child"` 语义——需要在实现前明确决定，本报告不替设计文档做这个决定。 |
| **ingest / backflow**（`ingest.py::ingest_sample/materialize_metrics`，`backflow.py::ingest_optimizer_run`） | **不进评估链**，是库自己的**持久化/回灌 ETL**，最接近 **(e) 独立 CLI**（`ingest-optimizer-run`）而非 (b)(c) | 关键架构判断：device_db 是**跨 run、长期累积**的语料库（当前 12767 行，跨越 2026-07 到 2026-09 多轮 campaign），而设计契约里的 `Observations`/`observations.jsonl`（`DESIGN_CN.md:98-110, 134`）是**单个 run 自己的**、append-only 观测表。二者生命周期不同，不能把 device_db 当成某次 `sim.evaluate` 的 `observations.jsonl` 本身。建议：库继续保持独立 sqlite，`ingest-optimizer-run` 换个输入源——从旧格式的 `em_artifact_manifest.json` 缓存（`backflow.py:2-21` 注释所述格式）改读**新引擎自己的** `observations.jsonl` + `sims/<obs_id>/...`（`DESIGN_CN.md:129-140`），其余身份哈希/geom_version 标注/CT-less 校验/parity 门逻辑原样保留（这些是库自己的完整性规则，与"观测从哪来"正交）。 |
| **query**（`hermes-db query`） | **(d) 分析/报告块**，读专用（不建议做成 suggester 或 points 源） | 它既不产生新候选点（不是 points 源），也不是"对 spec 提议若干候选"的 suggester 形态——是"对单个具体点，回答测过没有/预测多少"的诊断工具。最小接口：只读 sqlite 连接 + `(family, process_profile, stratum, params 或 sample_id)` + 可选 `pin_geom_version`；不需要引擎的 `Executor`/`Store`。 |
| **suggest / recommend**（`surrogate.inverse.suggest`） | **(c) suggester**，与设计契约的 `Suggester` 协议**形状高度吻合** | 契约：`propose(spec, history, n, *, seed) -> list[Proposal]`（`DESIGN_CN.md:54`），Block 层 `suggest(spec, observations, n, *, strategy, seed, initial=()) -> list[Point]`（`DESIGN_CN.md:154`），且同一行明确"无状态：每次由 `observations + initial` 重建模型"（`DESIGN_CN.md:154`；决策表里 `DESIGN_CN.md:21` 同样写"无状态 `suggest(spec, observations, n)`"）——`inverse.suggest` 本来就是无状态、每次从 DB 重新 fit（4.5 节的 `model_cache` 只是加速，不改变"无状态"语义）。**但有一处不对齐，需要显式决定**：`inverse.suggest` 不是从"当次 run 的 `observations`"取训练数据，而是从**外部、跨 run 的 device_db** 取——这意味着一个 `suggesters/device_db.py` 需要比标准 `Suggester` 协议更多的构造信息（哪个 sqlite 文件、哪个 stratum/process_profile/topology）。设计契约已经预留了口子：`em` 流水线的 `spec` 有可选段 `devices`（器件族/生成器/几何变量映射/端口序）和 `em`（EMX 设置/频率扫描），`DESIGN_CN.md:196`——建议把"库文件路径 + stratum 绑定"也放进 `spec.devices`（或紧邻它的一个新可选字段），suggester 从 `spec` 里读，而不是扩展 `Suggester.propose()` 的位置参数。另一处需要显式决定的偏差：`verify_build=True` 时 `inverse.suggest` 会在**返回候选之前**自己跑一次真实 PCell 生成 + DRC（`inverse.py:604-646`），这是"建议器内嵌了一次微型评估"，与"suggester 只提议、engine 才评估"的分层不一致（QL-12 历史原因：纯代理可行性筛选出来的候选经常造不出来）。建议决策二选一：①把 build+DRC 验证做成 `points.from_db`/`opt.suggest` 之后、`sim.evaluate` 之前的一个轻量 point 级 stage（`pcell` 阶段的"只建几何不跑 EMX"子集），或者②保留在 suggester 内部但在文档里明确这是一次刻意的例外（因为它不消耗仿真预算、不产出新 Observation，只是"筛掉造不出来的候选"）。 |
| **coverage**（`hermes-db coverage`） | **(d) 分析/报告块** | 纯聚合查询（per-stratum ok/build_rejected/emx_failed 计数、achieved range、NT 分布、标量覆盖数），无状态、只读，直接对应 `analyze.*` 类 Block（如 `analyze.report`，`DESIGN_CN.md:157`）的同类工作，但**读的是外部 device_db 而不是当次 `observations`**——应该做成一个专门指向 device_db 的报告脚本/子命令，不需要挂进 `analyze.report` 的六节四图体系。 |
| **backflow 的身份/校验逻辑**（`family_catalog.py` 的 CT-less/端口契约校验、`configuration_binding.py` 的"计划配置 vs 实测配置"一致性检查） | 不属于上述任一桶，是**库自身的数据完整性层**，应作为 device_db 包（若独立拆分）内部的写入前置校验，不进入通用引擎 | 这些检查（身份哈希、geom_version 合法性、CT 命名黑名单、EMX 端口双词表一致性、`PlanConfigurationBinding.validate`）都是"device_db 这个长期语料库自己的完整性约束"，与"这次评估是怎么跑的"无关；通用引擎/`Stage` 协议不需要知道它们的存在。 |
| **reingest**（`hermes-db reingest`） | **(e) 独立 CLI / 维护操作**——"对已存 sNp 重放 extract 阶段" | 本质是"用新的 `DERIVED_VERSION` 公式重算旧数据"的批量维护动作，不发生在任何一次评估循环里。若 extract 变成 `Stage`，`reingest` 就是"对库里所有历史 sNp 重新调用同一个 `Stage.run()`"的批处理脚本，可以直接复用新 `extract` stage 的实现，不需要单独维护一份公式代码（今天已经是这样：`reingest_metrics` 调的还是 `measure.metrics_from_snp`）。 |
| **migration_v4.py**（v3→v4 CT-less 迁移） | **(e) 独立 CLI**，一次性工具，与引擎/suggester 完全无关 | 不需要映射，保持独立脚本形态即可。 |
| **family-status / parity-status / legacy-audit** | **(d) 分析/报告块**（`legacy-audit` 更接近纯历史盘点，可视为 (e)） | 与 `coverage` 同理，都是对 device_db 的只读报告，不需要动引擎。 |
| **候选生成用的 `FamilySchema.draw/feasible`**（`family_schemas.py`） | **(b) points 源**的一种具体实现 | 已经完全符合"给定 dims/ranges 产出 `list[Point]`"的形状（`points_sobol(spec, n, seed)` 的家族特化版本，`DESIGN_CN.md:152`），可以原样改造成 `points.sobol_family(spec, n, seed)`，把"可行性"判据（`feasible()`）留在同一个函数里，供 suggester 的候选池复用；同时也是 sweep campaign 自己产生候选点的来源——**同一份代码对应两个 Block**（`points.*` 生成新候选 + sweep driver 用它产生要发去 EMX 的点），这与今天 `sampler.py:149-167` 的"pinned wrapper 保证两条路径永不分叉"的设计意图是一致的，应该保留"单一实现、多个调用方"的结构，不要因为拆包而复制一份。 |
| **query 库整体（历史测量数据）作为热启动种子** | **(b) points 源**：`points.from_db` | 设计契约已经点名这个函数（`DESIGN_CN.md:197`），且 Block 表里 `points_from(observations, k)`（`DESIGN_CN.md:152`）已有同构的"从既有观测取种子点"先例。最小接口：给定 `(db_path, stratum 绑定, 可选 geom_version pin, 可选 k)`，返回 `list[Point]`（`params` 需要从 device_db 的原生数值类型转成新 `Point.params: dict[str,str]` 的"已吸附到步长网格的字符串"形式，`DESIGN_CN.md:94`）——这一步类型转换目前没有代码，需要新写。 |

**一个需要提前拍板的接口缺口**：新 `Observation`/`Point` 模型（`DESIGN_CN.md:93-110`）里没有"这条记录来自哪个几何生成代/哪套 EMX 设置"这类字段——`origin: str` 目前的取值域是 `"user"|"recipe:<name>"|"suggest:<strategy>"|"agent"`（`DESIGN_CN.md:96`）。如果 `points.from_db`/`suggesters/device_db.py` 要对外暴露"这个点来自 geom_version=5 的真实测量"这类 provenance（今天 `query`/`suggest` 的每条输出都带 `provenance`/`selection` 块，`cli.py:302-313, 737-738`），要么扩展 `origin` 的取值空间（如 `"db:<stratum>@geom5"`），要么在 `Point`/`Observation` 上补一个可选 `provenance: dict` 字段。这是本报告发现的一个具体、待决的小接口缺口，不是可以绕过的细节——今天库的用户已经依赖这些 provenance 字段做"pin 一个几何代、不要混训"的正确性判断（2.3/2.4 节），新架构如果丢了这个字段，`--pin-geom-version`/`--anchored` 这类语义会无处安放。

---

## 7. 脆弱点与容易踩的坑

1. **6 套独立版本号**（已在文首列出，此处补充后果）：`geom_version`(1-6，几何) 与 `DERIVED_VERSION`(=3，公式) 与 `family_schema_revision`(=2，生成器契约) 与 `MODEL_CODE_VERSION`(=1，GP拟合语义) 与 `SCHEMA_VERSION`/`ACTIVE_SCHEMA_VERSION`(=3/4，表结构) 与 `OUTPUT_SCHEMA_VERSION`(="1"，CLI JSON) 六者互相独立递增，任何"升级"讨论必须先问清楚升的是哪一个——例如"geom5"（几何生成代）与"schema v4"（表结构）经常在文档口语里被混着说，但二者互不隶属（`ingest.py:24-38, 67`；`schema.py:24-40`）。

2. **2-port Touchstone 的列序特判**（`measure.py:86-87`）：`if n == 2: s = s.transpose(0, 2, 1)`。这是 Touchstone v1 规范对 2-port 的历史遗留特例（列序 `S11 S21 S12 S22`，不同于 n≥3 时的行主序），代码只对 `n==2` 做了修正。任何新增奇数/偶数端口家族都必须重新核对，不能假设"transpose 一下就对了"具有普适性。

3. **端口名字典序陷阱**（`family_catalog.py:31-36`，`_EMX_2PORT/_EMX_4PORT`）：EMX 按端口名**字典序**排列 sNp 列，所以全库统一用零填充的 `p01,p02,p03,p04`——如果哪里改成不补零的 `p1,p2,...,p10`，`"p10"` 会字典序排到 `"p2"` 前面，S 矩阵列序静默错位，算出的 L/Q/k 数值上仍然"看起来合理"但物理上是错的。`family_catalog.validate_sample_payload`（第 269-298 行）和 `ingest.materialize_metrics`（`ingest.py:298-309`）专门做了"存储的端口名必须与家族契约逐一相等且顺序一致"的二次校验（QL-04），但这只能防"入库时数据被污染"，防不了"生成/驱动 EMX 时端口名就没补零"这类上游错误。

4. **`signal_ports` 与 `emx_port_names` 是两套词表，`ind_sym` 是特例**（`family_catalog.py:48-60`）：五个变压器家族的 EMX 名与语义名通过 `emx_ports_override` 显式映射；`ind_sym` 的 CT-less 契约**直接**用 `p01/p02` 当语义名（历史遗留），二者恰好相等只是巧合，不是设计原则——迁移代码时若假设"这两个词表总是一一对应"会在 `ind_sym` 上踩坑（实际上是"总是可能不同，`ind_sym` 只是当前唯一相同的个例"）。

5. **CT-less 是数值库的边界，不是几何生成器的边界**：`ct_policy: Literal["forbidden"]`（`family_catalog.py:63`）+ `is_ct_shaped_name`/`validate_no_ct_keys`（第 203-222 行）把**任何** CT 形状的 key（params/fixture/emx_settings/manifest/binding 的任意层级）都当成入库时的硬拒绝。但底层 PCell 生成器（`devices/clean_port/...`）本身是支持可选中心抽头几何的——"生成器能画 CT"和"查询库有 CT 的测量模型"是两件事（项目 `CLAUDE.md` 与本仓库 MEMORY 均反复强调这一点）。迁移/扩展查询库时如果只看生成器能力就假设"库也支持"，会直接被 `validate_no_ct_keys` fail-closed 拒绝——这是有意为之，不是 bug。

6. **`ingest-optimizer-run` 的历史遗留命名不代表当前规则**：`experiments/device_db_sweep_n28/outputs/backflow/ind_ct_turbo_smoke/` 下的 20 个文件叫 `candidate_000001.s3p`…`candidate_000020.s3p`，是**候选 id 命名**，与 `backflow.py:431` 当前的 `f"{snp_sha256}{suffix}"` 内容哈希命名规则不符——大概率是该命名规则引入前的验收遗留数据（`docs/guide/03-device-db-and-hermes-db.md:127` 明确写"当前以sNp内容SHA命名库内文件，同candidate_id的不同器件不会再覆盖彼此文件"，暗示过去不是这样）。写回放测试/迁移脚本时必须以**代码里的当前规则**为准，不能拿这个磁盘目录的文件名模式反推规则。

7. **原始 sweep 入库 ≠ backflow 入库的文件存放方式**：`source='sweep'` 的行，`snp_path` 就是 sweep 树里原名的文件（相对路径记账，文件不挪不改名，`ingest.py:186`）；只有 `source='optimizer'`（backflow）才会把 sNp 复制并改名为内容哈希（`backflow.py:429-431`）。"sNp 用内容 SHA256 命名"这句话**只对 backflow 树成立**，对主 sweep 树不成立——照抄这条规则去处理 `outputs/sweep/` 会找错文件名。

8. **单位约定：全部 SI 基本单位，没有工程前缀**：电感是 **亨利(H)**，频率是 **赫兹(Hz)**，Q 和 k 无量纲（`docs/guide/03-device-db-and-hermes-db.md:52`）。一个 330pH 的电感在库里读出来是 `3.3e-10`；`--anchored "Lp@28e9"` 里的 `28e9` 是 28 GHz 而不是 28（这一点在命令行里打错一个数量级不会报错，只会安静地查到一个不存在的频段或触发"频率超出网格范围"的 `ValueError`）。EMX 侧的 `--sweep-stepsize`/`start_hz`/`stop_hz`（`backflow.py:99-100, 125-126` 解析）同理都是 Hz。

9. **`SRF=NULL` 不等于"无穷大"，且方向依赖 `L_lf` 符号**：`_srf_first_sign_flip`（`measure.py:195-211`）在"频段内没有谐振"和"起点就已经过了谐振点（第一个有限频点 `Im(Z)` 已经 ≤ 0）"两种情况下都返回 `None`——消费方必须结合 `L*_lf` 的正负才能分辨到底是"这个器件在整个扫频范围内都表现为电感"还是"从一开始就已经是电容性的"（`measure.py:199-202` 注释 + `docs/guide/03-device-db-and-hermes-db.md:59` "NULL不是无限大SRF"）。`suggest`/`query` 的 `_srf_satisfies`（`inverse.py:577-598`）对此的处理是"NULL 满足 `min` 但不满足 `max`/`target`"，这是一个 fail-closed 的保守选择，不是物理上的精确刻画；`docs/guide/03-device-db-and-hermes-db.md:157` 也把"SRF=NULL 的近邻下限判断尚未携带已测频带上限"列为已知未解决问题。

10. **`suggest`/`query` 的 `sigma` 在 log 目标下只是一阶近似**（`model.py:384-390`）：`Lp_res/Qp_peak/Ls_res/Qs_peak` 都是 `log_target=True`，`predict()` 返回的 `sigma = exp(mu_log)*sigma_log` 只是 delta 方法近似，不是精确的对数正态标准差；`--max-rel-sigma`/`k_sigma` 不构成"已校准的置信区间"承诺——4.8 节引用的 `xfm_ms_apm10_014` 案例（预测误差达到 sigma 的 10.6/6.5 倍）就是这条警告在真实数据上的体现，`docs/guide/03-device-db-and-hermes-db.md:158` 与该案例来源文档都明确写"不能声称预测置信区间已经校准通过"。

11. **`geom_version` 允许同一参数点在多代几何上并存，但训练/查询必须钉死单一代**：`resolve_stratum_dataset`（`schema.py:604-748`）在**不显式 `pin_geom_version`** 时要求某 stratum 的已接受样本只能属于一个 `(emx_settings_hash, geom_version)` 组合，否则直接 `ValueError` 列出全部组合拒绝——这不是"看起来可以但没做"的遗漏，是**故意的 fail-closed**（"不同的数值语义，不是更多数据"，`schema.py:566-567`）。迁移/合并多个 campaign 产出的库时，如果简单地把两个 sqlite 的 `samples` 表 UNION 起来而不管 `geom_version`，会立刻让所有下游查询/建议报错。

12. **`suggest(verify_build=True)` 默认会在返回结果前跑真实生成器 + DRC**（`inverse.py:604-646`，第 6 节已展开）：这是当前代码里"suggester 内嵌一次同步评估"的唯一例外，迁移到新架构时如果不显式处理，容易被误当成"纯代理、零副作用"的标准 suggester 对待，从而漏掉它对 klayout/DRC 规则文件等环境依赖的隐性要求（`inverse.py:723-742` 的"pre-flight 检查"就是在给这个隐性依赖打补丁：环境缺失时提前用清晰错误代替"每个候选都失败、空列表被误判为 spec 不可行"）。

13. **outputs/ 整体 gitignore，含义是"这份研究报告引用的所有具体数字都只在本机成立"**：`experiments/device_db_sweep_n28/.gitignore:1` 只有 `outputs/` 一行，新 clone 不会带任何 GDS/sNp/sqlite/JSON 产物。第 5 节给出的目录树/计数/大小是本机现场核实的快照，任何回放测试如果假设"CI/新 checkout 上也有这些文件"会直接失败——回放测试要么把一个裁剪过的样例子集显式提交进新仓库（作为测试 fixture），要么改造 `ingest-optimizer-run`/`sweep_driver` 风格的脚本让它们能在测试里生成同构的最小样例。
