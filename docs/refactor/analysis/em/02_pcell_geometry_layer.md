# pcell 几何生成层：公开 API 与合同（供 ic-opt-modular `pcell` Stage 复用）

范围与方法：只读代码study，仓库为 `EM-opt-workflow`（下称 `EMOW` =
`<em-opt>`）。所有路径均为绝对路径；行号对应
2026-09-22 研究时的工作树状态（`git status` 干净，`HEAD` 未变）。凡引用 `n28_1p10m`/`n65_1p9m`
两个真实 profile，只给规则 ID（如 `VIA8`、`M9`）与结构描述，不给任何数值；示例数值一律取自
`process_data/profiles/demo_6m/rule.yaml`（仓库自带的公开虚构 profile，README 明示"ALL VALUES
ARE INVENTED...safe to publish"）。文中多处结论由本次研究现场用 `EMOW/.venv/bin/python` 实测验证
（计时、pytest 运行、cProfile），不是纯读码推测，均已标注。

另：`ic-opt-modular` 自身的 `src/ic_opt/eval/stage.py` 已经定义了 `Stage` 协议（`fingerprint(inp)
-> str | None` + `run(inp, ctx) -> out`），`docs/refactor/DESIGN_CN.md` 191 行明确
"em-opt | `pcell → emx → bind_nport → spectre → ocean → extract`"，且 `eval/engine.py` 第 11
行注释承认"Stage caching (`Stage.fingerprint`) is reserved for the first cacheable [stage]"——
目前没有任何 Stage 真正实现它。也就是说本报告第 6 节不是纯背景调研，而是这条 `pcell` Stage
的 `fingerprint()` 该怎么写的直接设计输入；已在正文标出。

---

## 1. 最小调用序列：(generator id, parameters, fixed_parameters, process_profile) → GDS

### 1.1 两层 API：orchestration 层 vs library 层

EMOW 里实际存在两条路径，容易混淆：

- **library 层（新 pcell Stage 应该复用的）**：`em_ic_opt_workflow.geometry.registry.get_generator`
  + `PassiveDeviceGenerator.generate()`。这两者构成"配置 dict → GDS + manifest"的最小契约，
  和 EMX、Spectre 完全无关。
- **orchestration 层（EMOW 特有的胶水代码，不建议整体照搬）**：
  `em_ic_opt_workflow.em_candidate_preparation.prepare_em_candidate_from_contract` /
  `prepare_em_devices_from_contract`。它在调用 library 层之前，先把优化器吐出的扁平
  `parameters: dict[str, object]`（一个 Point）按 `parameter_prefix` 拆到每个器件、和
  `fixed_parameters`/`process_profile`/`port_order`/`drc_check` 合并成一份完整配置 dict；
  调用 library 层之后，又顺手把 DRC 门禁和 EMX 参数准备也做了——这部分属于"下一个 Stage
  的活"，见第 9 节。

### 1.2 library 层最小调用序列（应被复用）

```python
from em_ic_opt_workflow.geometry.registry import get_generator

generator = get_generator(generator_id, plugin_module="builtin:clean_port")
#   -> PassiveDeviceGenerator 子类单例，例如 CleanPortIndSymGenerator()

config = generator.config_model.model_validate(merged_config_dict)
#   -> pydantic 校验；merged_config_dict = fixed_parameters
#      | {"process_profile": ..., "port_order": [...]}
#      | {name: point[name] for name in parameters}
#      （这一步合并逻辑本身在 EMOW 里位于 orchestration 层，见 1.3）

result = generator.generate(
    config, outdir=outdir, gds_name="device.gds", top_cell=None,
)
#   -> GeometryGenerationResult(generator_id, gds_path, top_cell,
#                                manifest_path, emx_ports_path)
```

- `get_generator` 签名：
  `EMOW/src/em_ic_opt_workflow/geometry/registry.py:118-143`
  ```python
  def get_generator(
      generator_id: str,
      *,
      plugin_module: str | Path | None = None,
  ) -> PassiveDeviceGenerator:
  ```
  必须显式给 `plugin_module`（本仓没有任何硬编码内置生成器；`_BUILTIN_IDS = ()`，见同文件
  第 18 行的注释——2026-07-09 事故后 M12 把旧内置生成器整体移除）。`plugin_module` 接受绝对
  路径或 `"builtin:<name>"` 简写；`builtin:clean_port` 解析规则见
  `EMOW/src/em_ic_opt_workflow/geometry/registry.py:26-49`
  （`_BUILTIN_PLUGIN_MODULES = {"clean_port": ("em_ic_opt_workflow.devices.clean_port",
  "generator_plugin.py")}`，用 `importlib.resources.files` 解析成包内绝对路径）。
  插件模块用 `importlib.util.spec_from_file_location` 按文件路径动态加载，并以
  `sha256(路径)` 为 key 缓存进 `sys.modules`（`registry.py:52-77`）——**进程级单例缓存**，
  改插件文件不重启进程不会生效（同段注释原话："the module is cached per-process (restart
  to pick up plugin edits)"）。
- `PassiveDeviceGenerator` 抽象基类：
  `EMOW/src/em_ic_opt_workflow/geometry/base.py:19-32`
  ```python
  class PassiveDeviceGenerator(ABC):
      generator_id: str
      config_model: type[BaseModel]

      @abstractmethod
      def generate(
          self, config: BaseModel, *, outdir: Path, gds_name: str,
          top_cell: str | None = None,
      ) -> GeometryGenerationResult: ...
  ```
  只有一个抽象方法。`generator_id`/`config_model` 是类属性。**`PLUGIN_GENERATORS` 里的六个
  实例是无状态单例、跨调用共享**（`registry.py:91-98` 注释："Exported generator instances
  are shared across calls and must stay stateless per generate()"）。
- 返回类型 `GeometryGenerationResult`：
  `EMOW/src/em_ic_opt_workflow/geometry/base.py:10-16`
  ```python
  @dataclass(frozen=True)
  class GeometryGenerationResult:
      generator_id: str
      gds_path: Path
      top_cell: str
      manifest_path: Path
      emx_ports_path: Path
  ```
  **只有路径和字符串，不含几何/端口的结构化数据本身**（见 1.4 的重要缺口）。

`generator.generate()` 的具体实现（以 `clean_port_ind_sym` 为例）在
`EMOW/src/em_ic_opt_workflow/devices/clean_port/generator_plugin.py:983-1004`：

```python
class CleanPortIndSymGenerator(PassiveDeviceGenerator):
    generator_id = "clean_port_ind_sym"
    config_model = CleanPortIndSymConfig

    def generate(self, config, *, outdir, gds_name, top_cell=None):
        p = _clean_port()
        cell = p.ind_sym(
            OD=config.outer_diameter_um, W=config.width_um,
            OPENING=config.opening_um, LEAD=config.lead_length_um,
            S=config.spacing_um, NT=config.turns,
            TOP_ME=config.top_metal, BTM_ME=config.bottom_metal,
            CT_ME=config.ct_metal,
            STRAIGHT_EXTENSION=config.straight_extension_um,
            port_order=list(config.port_order),
            ground_fixture=_build_fixture(p, config.ground_fixture,
                                          _auto_stub_widths(config)),
            process=p.process_rule_context(config.process_profile),
        )
        return _write_geometry_outputs(
            p, cell, config, generator_id=self.generator_id,
            outdir=outdir, gds_name=gds_name, top_cell=top_cell,
            requires_vias=(config.turns >= 2 or config.ct_metal is not None))
```

`_clean_port()`（`generator_plugin.py:118-130`）用同样的"按文件路径 + `sys.modules` 缓存"
手法（`threading.Lock` 保护，见 115 行 `_CLEAN_PORT_LOCK`）动态加载
`pcell_inductor_port_clean.py` 这个 facade 模块，句柄记作 `p`。六个家族分别调用
`p.ind_sym` / `p.xfm_bs` / `p.xfm_ms` / `p.xfm_balun` / `p.xfm_tw` / `p.xfm_il`
（生成类定义于同文件 983-1161 行），签名见第 3 节。**这六个函数都返回内部 `Cell` 数据类，
不是 GDS，也不是 pydantic 模型**（`Cell` 定义见 1.4）。

统一的落盘/收尾逻辑集中在
`_write_geometry_outputs`（`EMOW/src/em_ic_opt_workflow/devices/clean_port/generator_plugin.py:922-980`），
签名：
```python
def _write_geometry_outputs(
    p, cell, config: _CleanPortDeviceConfigBase, *, generator_id: str,
    outdir: Path, gds_name: str, top_cell: str | None, requires_vias: bool,
) -> GeometryGenerationResult:
```
六个 family 各自的 `generate()` 都只是"造 Cell + 调它"，这是唯一真正做 I/O 的地方。

### 1.3 orchestration 层的 merge 逻辑（仅供参考，不建议整体复用）

`_merged_device_geometry_parameters`
（`EMOW/src/em_ic_opt_workflow/em_candidate_preparation.py:313-332`）展示了
"Point + GeneratorConfig → 完整 config dict"的合并规则：

```python
def _merged_device_geometry_parameters(device, parameters):
    generator = device.geometry.generator
    missing = [name for name in generator.parameters if name not in parameters]
    if missing: raise ValueError(...)
    merged = dict(generator.fixed_parameters)
    for contract_field in ("process_profile", "port_order"):
        if contract_field in merged: raise ValueError(...)   # 防止用户在 fixed_parameters 里重复声明
    merged["process_profile"] = generator.process_profile
    merged["port_order"] = generator.port_order
    _inject_drc_check(merged, generator)
    for name in generator.parameters:
        if name in merged: raise ValueError(...)
        merged[name] = parameters[name]
    return merged
```
多器件扇出（本任务提到的"possibly fanning out over several devices"）由
`_split_multi_device_parameters`（`em_candidate_preparation.py:394-436`）实现：按
`"<parameter_prefix>.<field>"` 的点号前缀把一个扁平 `parameters` dict 拆给每个
`EmDeviceConfig`（`bundle.em_devices.devices`），再逐个调用
`prepare_em_candidate`（`em_candidate_preparation.py:196-286`，内部即 1.2 的最小序列 +
可选 DRC 门禁，见第 5 节）。完整实测用例：
`EMOW/tests/test_geometry_registry_plugin.py:502-591`
（`test_prepare_em_devices_from_contract_via_plugin`，用
`parameters={"ind.outer_diameter_um": 100.0, "ind.width_um": 5.0}` 生成一个器件；
该用例目前有一处失败，见第 10 节）。

**这套 merge/split 逻辑和 `ContractBundle`/`schemas.py` 的整体 YAML 合同强耦合**（`bundle.geometry`
/`bundle.em_devices`/`bundle.emx` 来自 `em_ic_opt_workflow.validate`），新包的 `Point`
（`ic_opt/space.py:141-149`，`params: dict[str, str]` 已吸附到步长网格的字符串）形状不同，
这部分需要按新契约重写，而不是照抄——但"按前缀拆分 + fixed_parameters 兜底 + 冲突字段报错"
这个**模式**值得保留。

### 1.4 "geometry manifest / port manifest"实际长什么样（关键：比想象的窄）

`_write_geometry_outputs` 落盘 3 个文件（`generator_plugin.py:940-980`）：

1. **GDS**（`gds_path = outdir / gds_name`）——`p.write_gds(cell, gds_path)`。
2. **`emx_ports.txt`**——`port_lines = p.emx_port_lines(cell.emx_ports)`，逐行写
   `"-p {name}={signal}[:{reference}]\n"`。
3. **`geometry_manifest.json`**：
   ```python
   {
     "schema_version": "1.0",
     "generator_id": generator_id,
     "geometry": {"config": config.model_dump(mode="json")},
     "suggested_emx_ports": port_lines,          # 字符串列表，不是结构化端口
     "suggested_emx_ports_note": "...",
     "via_landing_audit": audit,                  # {"status": "pass", "vias_checked": N}
     "port_lattice_audit": port_audit,            # {"status": "pass", "ports_checked": N}
     **({"pgs_geometry": pgs_geometry} if ... )   # 仅当配置了 pgs
   }
   ```
   （`json.dumps(..., indent=2, sort_keys=True)`——`sort_keys=True` 对 json 模块是**递归**
   排序，所以整份 manifest 已经是"canonical JSON"，这对第 6 节的 fingerprint 讨论很关键。）

**重要缺口**：端口的结构化几何信息——每个端口的坐标 `point_nm`、引线区域 `lead_zone_nm`、
金属层 `metal`/`metal_index`、标签层 `label_layer`——只以 `cell.emx_ports: list[dict]`
的形式**短暂存在于内存里**（由 `finalize_emx_ports(cell)` 一次性生成，见 1.5），
`_write_geometry_outputs` 只把它降维成 `-p name=signal:reference` 文本行落盘，**原始
dict 列表从未被序列化到任何文件**，`GeometryGenerationResult` 也不携带它、更不携带
`Cell` 对象本身。也就是说：**今天的公开 API 拿不到"P1/N1/P2/N2/CT 端口的坐标+层"这份结构化
数据**，只能拿到降维后的信号名/参考名映射。如果新 `pcell` Stage 的输出类型 `Geometry`
想要一份真正的"port manifest"（本任务描述里"port names P1/N1/P2/N2/CT，其坐标/层"），
有两个选择：
  (a) fork/包一层 `_write_geometry_outputs`，把 `cell.emx_ports` 也落盘（比如
      `geometry_manifest.json` 里新增一个 `"ports"` 字段，内容就是
      `_port_dict` 产出的原始 dict，见 1.6）；
  (b) 放弃走 `PassiveDeviceGenerator.generate()`，改走"直接模块调用"路径（第 8 节），
      自己拿到 `Cell.emx_ports`——但这条路径是给测试用的内部逃生舱，不是稳定契约
      （`_clean_port()` 前缀下划线、按文件路径动态加载、无版本保证）。
  推荐 (a)：改动量小（`_write_geometry_outputs` 本来就持有 `cell.emx_ports`），且不破坏
  现有 `GeometryGenerationResult` 的向后兼容（加字段而不是改类型）。

### 1.5 顶层 cell / port / GDS 的构造细节

- **端口注册的唯一入口**：`Cell.add_emx_port()`——
  `EMOW/src/em_ic_opt_workflow/devices/clean_port/_pcell_core.py:389-434`。docstring
  明确"This is the ONLY place a port coordinate is ever computed"，`x_um`/`y_um`
  在这里被 snap 成整数纳米**恰好一次**，同一对整数同时写入 `self.labels`（GDS 标签）和
  `self.ports`（结构化 `Port`），保证两者永不漂移（"port contract 2026-09-21"）。
- **端口列表的唯一生成入口**：`finalize_emx_ports(cell) -> list[dict]`——
  `_pcell_core.py:770-789`。遍历 `cell._flat()`（同一条整数变换链，`_pcell_core.py:461-476`，
  贯穿 `cell.inst()` 的多层实例化——CT 抽头、被 xfm_bs/xfm_ms 内部复用的 ind_sym 子绕组都
  经过它），收集所有 `__port__` 节点，逐个调用 `_port_dict()` 组装，并用
  `_check_port_lattice_invariant()`（`_pcell_core.py:750-767`）在构建期断言"每个端口坐标
  必须落在它自己登记的 lead_zone 内"，不满足直接 `PortError`。**六个 family 函数各自在
  自己函数体末尾恰好调用一次** `cell.emx_ports = finalize_emx_ports(cell)`（docstring
  原话："Call this exactly once, at the end of a family function"）。
- **单个端口 dict 的准确形状**（`_port_dict`，`_pcell_core.py:287-309`）：
  ```python
  {
    "name": name, "signal": name, "reference": None,
    "logical_name": logical_name,
    "metal": _metal_name(metal), "metal_index": metal,
    "label_layer": [gds_layer, datatype],
    "point_nm": [x_nm, y_nm],
    "lead_zone_nm": [x0, y0, x1, y1],
    "label_xy_um": [round(x_nm*0.001, 3), round(y_nm*0.001, 3)],
  }
  ```
  `reference` 字段初始为 `None`；只有配置了 `ground_fixture` 时，
  `add_ground_fixture()`（`_pcell_core.py:869-972`）才会把它改写成 `"G{index:02d}"`
  （M13 之前叫"local-ref pin"）。ground_fixture 是**必填字段**（`_CleanPortDeviceConfigBase`
  里没有默认值，见 2.1），六个生成器都不接受省略地夹具。
- **端口名字集合是固定的、由设备族决定，不接受任意命名**：电感 `[P1, N1]` 或
  `[P1, N1, CT]`（`_IND_PORTS = ["P1", "N1"]`，`generator_plugin.py:216`；
  `CleanPortIndSymConfig._ct_contract`，`generator_plugin.py:247-280`）；六个变压器族
  固定基座 `[P1, N1, P2, N2]`（`_XFM_PORTS`，`generator_plugin.py:283`），启用 CT 后按
  "先 CTP 后 CTS"追加（`_FixedXfmPortOrderMixin._ports_fixed_by_device`，
  `generator_plugin.py:286-307`）；xfm_tw 完全没有 CT 选项（没有 `ct_*` 字段，基类
  `extra="forbid"` 直接拒绝任何尝试）。**`port_order` 配置字段实际上只起"校验用户是否
  按规定顺序声明"的作用，不是自由字段**——传错顺序/缺项/加项直接 `ValidationError`。
- **`Cell.name` / GDS 顶层 cell 名 == 输出文件名的 stem，且不可通过 `top_cell` 参数改变**：
  `_write_geometry_outputs`（`generator_plugin.py:930-934`）：
  ```python
  # write_gds names the GDS top cell after the sanitized filename stem.
  # Return that actual name to EMX, including when legacy projects pass
  # a different top_cell override.
  resolved_top_cell = re.sub(r"[^A-Za-z0-9_$?]", "_", Path(gds_name).stem)
  cell.name = resolved_top_cell
  ```
  函数签名里的 `top_cell: str | None` 参数**在函数体内完全没被引用**——纯签名占位。
  `write_gds()` 自己也独立做了同一件事（`_pcell_core.py:498-521`，"stem = Path(path).stem"），
  双保险确保"文件名 stem == GDS 顶层 cell 名"这条硬规则在任何写入路径（生产插件、demo、
  直接调用）下都成立。`GeometryGenerationResult.top_cell` 返回的就是这个
  `resolved_top_cell`，**不是** `GeometryGeneratorConfig.top_cell` 配置字段的值（后者在
  `schemas.py` 里确实存在，但对着"实际生成的 GDS"是死字段——细节和一个现场实测到的失败
  用例见第 10 节）。

### 1.6 外部库依赖

- **GDS I/O**：只用 `klayout.db`（`import klayout.db as kdb`，`_pcell_core.py:13`；
  `pyproject.toml` 依赖清单 `EMOW/pyproject.toml:26` 只有 `"klayout>=0.30"`，没有
  `gdstk`、没有 `gdsfactory`）。README 原话（`EMOW/src/em_ic_opt_workflow/devices/clean_port/README.md:340-341`）：
  "the port (KLayout `klayout.db` for GDS I/O; no gdstk)"。
- **gdsfactory 是被替代的历史遗留**：`xfm_bs` 的 docstring
  （`EMOW/src/em_ic_opt_workflow/devices/clean_port/_pcell_xfm_bs.py:177`）写"replaces the
  gdsfactory placeholder"；README 158-167 行同样确认"It replaces the gdsfactory product
  placeholder `src/.../single_turn_transformer.py`"。所以**当前生产路径完全不依赖
  gdsfactory**，新包如果打算用 gdsfactory 做别的 Stage，不要假设它和 pcell 层共享底层
  表示——pcell 层的 `Cell`/`Shape`/`Label`/`Port` 是自研的最小 dataclass 集合
  （`_pcell_core.py:317-367`），只在 `write_gds()` 落盘那一刻才转成 `kdb.Layout`。
- **规则解析**：`pydantic>=2.7`（`schemas.py`/`process_rules.py`/`rule_adapter.py`/
  `generator_plugin.py` 的所有 Config 类）+ `PyYAML>=6.0`（`process_rules.get_process_rule_profile`
  用 `yaml.safe_load`，见 4.2 节）。

---

## 2. Generator 配置 schema：`GeometryGeneratorConfig` + 按族校验

### 2.1 通用契约层（`schemas.py`，与具体家族无关）

`GeometryGeneratorConfig`——`EMOW/src/em_ic_opt_workflow/schemas.py:344-390`：

```python
class GeometryGeneratorConfig(StrictModel):
    id: str
    top_cell: str
    port_order: list[NonEmptyStr] = Field(min_length=1)
    process_profile: NonEmptyStr
    parameters: list[str] = Field(min_length=1)
    fixed_parameters: dict[str, Any] = Field(default_factory=dict)
    plugin_module: NonEmptyStr | None = None
    drc_check: StrictBool = True
```
（`StrictModel` = `ConfigDict(extra="forbid", str_strip_whitespace=True)`，
`schemas.py:45-46`——任何多余字段直接拒绝。）字段语义：

- `id`：即 `generator_id`，必须匹配 `[A-Za-z_][A-Za-z0-9_]*`（`_id_is_identifier`，
  `schemas.py:375-378`），实际会传给 `get_generator(id, plugin_module=...)`。
- `top_cell`：见 1.5——**只是契约里的一个字段，落盘时被忽略**，真正的顶层 cell 名永远
  等于 `gds_name` 的 stem。
- `port_order`：见 1.5，按家族固定，仅用于"声明式确认"，不是自由配置。
- `process_profile`：profile id 字符串，见第 4 节。
- `parameters`：**这次求值/这个 Point 要覆盖的字段名列表**（对应优化器的设计变量），
  必须是标识符（`_parameters_are_identifiers`，`schemas.py:385-390`）；具体数值不在这里，
  由外部 Point 提供（见 1.3）。
- `fixed_parameters`：本次不作为设计变量、写死的字段值 dict（宽松 `Any`，实际类型由第 2.2
  节的家族 pydantic 模型校验）。
- `plugin_module`：`None` / `"builtin:<name>"` / 绝对路径三选一（校验见
  `_plugin_module_is_absolute`，`schemas.py:354-373`，`builtin:` 前缀会立即调用
  `resolve_plugin_module_path` 验证已知——不存在的 builtin 名字在**config 校验阶段**就报错，
  不用等到真正生成才发现）。
- `drc_check`：见第 5 节，默认 `True`。

`GeometryConfig`（单器件工程用，对应 `config/geometry.yaml`，`## Geometry Generator`
标题）：`schemas.py:392-394`，只是 `{schema_version, generator: GeometryGeneratorConfig}`。

多器件扇出用 `EmDevicesConfig`（对应 `config/em_devices.yaml`，`## EM Devices` 标题，**可选
文件**）：
```python
class EmDeviceGeometryConfig(StrictModel):        # schemas.py:418-427
    generator: GeometryGeneratorConfig
    # 强制要求调用方显式写出 fixed_parameters（哪怕是空 dict），不能省略该 key

class EmDeviceConfig(StrictModel):                # schemas.py:438-452
    id: str
    parameter_prefix: str                          # Point 里用 "<prefix>.<field>" 寻址
    geometry: EmDeviceGeometryConfig
    emx: EmDeviceEmxConfig                          # s_file_name + ports（下一个 Stage 用）

class EmDevicesConfig(StrictModel):                # schemas.py:454-466
    schema_version: Literal["1.0"]
    devices: list[EmDeviceConfig] = Field(min_length=1)
    # id 与 parameter_prefix 各自要求全局唯一
```
文件名 → schema 的映射见
`EMOW/src/em_ic_opt_workflow/requirement_intake.py:114-133`：`geometry.yaml` 是必需文件
（`CONFIG_FILE_MODELS`），`em_devices.yaml` 是可选文件（`OPTIONAL_CONFIG_FILE_MODELS`）。

### 2.2 按家族校验：两层 pydantic model

真正的数值/单位/取值范围校验**不在** `GeometryGeneratorConfig` 里，而在
`generator_plugin.py` 里每个家族各自的 pydantic 模型（`config_model` 类属性指向它）：
`CleanPortIndSymConfig` / `CleanPortXfmBsConfig` / `CleanPortXfmMsConfig` /
`CleanPortXfmBalunConfig` / `CleanPortXfmTwConfig` / `CleanPortXfmIlConfig`
（`generator_plugin.py:230, 310, 380, 462, 527, 590`）。也就是说一次求值要过两道校验：

1. **契约层**（`GeometryGeneratorConfig`）：字段名合法、`parameters`/`fixed_parameters`
   不重叠（`em_candidate_preparation._merged_device_geometry_parameters` 里做，
   `em_candidate_preparation.py:296-309`）。
2. **家族层**（`config_model.model_validate(merged_dict)`）：数值范围（`Field(gt=0)` 等）、
   金属层不能是 M1（`_forbid_m1_metal`/`_forbid_m1_and_m2_metal`/`_forbid_m1_through_m3_metal`，
   `generator_plugin.py:62-111`）、CT 层级关系、port_order 与 CT 是否匹配、
   center_spacing 上界等**跨字段**约束（`model_validator(mode="after")`）。

家族层模型继承链：`_CleanPortDeviceConfigBase`（`generator_plugin.py:161-193`，
公共字段 `process_profile`/`port_order`/`ground_fixture`/`drc_check`）→
`_CleanPortInductorConfigBase`（197-209，仅 ind_sym 用，加 `top_metal`/`bottom_metal`
不能是 M1）。所有家族都用 `ConfigDict(extra="forbid")`（继承自
`_CleanPortDeviceConfigBase.model_config`），多字段/拼写错误一律 `ValidationError`。

### 2.3 单位约定：`_um` 后缀

**所有长度字段一律以 `_um`（微米）结尾**，无例外：`outer_diameter_um`、`width_um`、
`spacing_um`、`opening_um`、`lead_length_um`、`center_spacing_um`、
`straight_extension_um`、`inner_margin_um`、`ring_width_um`、`stub_width_um`、
`stub_length_um`、`stub_chamfer_um`、`strip_width_um`/`strip_spacing_um`/`margin_um`
（pgs）等。内部一律先转整数纳米再计算/落盘（`_nm()`，`_pcell_core.py:66-68`，
`DBU_UM = 0.001`；mask grid `GRID_UM = 0.005`，`_pcell_core.py:18-19`）。计数类字段没有
`_um`：`turns`/`multi_turns`/`ring_count`（xfm_tw 的 NR）/`turns`（xfm_il，P/S 共用一个值）。
金属层字段（`top_metal`/`bottom_metal`/`primary_metal`/…/`ct_*_metal`）是字符串，接受
`"9"`/`"M9"`/`"m9"`/`"AP"` 四种拼法，统一由 `_metal_stack_index_or_none()`
（`generator_plugin.py:41-59`，配置期）与 `_metal_index()`
（`_pcell_core.py:76-102`，构造期）两处**各自独立实现但语义一致**的解析。

### 2.4 `geom_version` / `CURRENT_GEOM_VERSION`：不是 geometry 层的字段，是 device_db 层的记账概念

**容易误解的一点**：`geom_version` 并不出现在 `GeometryGeneratorConfig`、
`_CleanPortDeviceConfigBase` 或 `GeometryGenerationResult` 里的任何一个——`geometry/`
和 `devices/clean_port/` 这两个包本身对"版本"一无所知，一次 `generate()` 调用不会产出
`geom_version`。这个概念活在更上层的 `em_ic_opt_workflow.device_db.ingest` 模块里
（这不在本次任务列出的研究范围内，但直接回答了任务问题，附带说明）：

`EMOW/src/em_ic_opt_workflow/device_db/ingest.py:67`：`CURRENT_GEOM_VERSION = 6`；
`ingest.py:72`：`KNOWN_GEOM_VERSIONS = (1, 2, 3, 4, 5, 6)`。第 25-66 行的大段注释逐一
记录了 v1→v6 各自"改了 pcell 构造代码的哪个角落，导致哪些参数点在相同输入下产出不同
GDS 字节"（不是数值改了，是**代码**改了）：M12 Phase 0.5 的 pcell 接缝重构（v1→v2）；
xfm_tw 槽宽下界的 pad 角修正 + 八边形倒角阶梯化 + NT=2 紧凑绕组改用"参考桥接方案"
（→v3）；xfm_il 外层碰撞桥对的对称错位修正（→v4）；紧凑两匝候选不再允许通过重叠开口
闭合内环（→v5）；端口坐标与引线多边形合并为同一次计算，修正 xfm_bs/xfm_ms 的 M1 地环
位置（→v6）。这是 `device_db` 自己的采样库用来防止"同一组参数坐标因为代码升级而在旧库
里被静默认为同一行"的人工维护计数器（`_validate_geom_version()`，`ingest.py:75-87`，
`bool` 也会被拒绝，防止 `True` 被当成整数 1）。**它不是 geometry 层的公开契约，而是
device_db 这个调用方自己加的一层"构造算法版本"标签**——第 6 节讨论 fingerprint 设计时
会重新用到这个概念，但建议方式不同（新包不必照抄"开发者手动 bump 一个全局常量"这种做法）。

同时注意 `docs/guide/07-device-inventory.md`（`EMOW/docs/guide/07-device-inventory.md:37`）
写的是"`CURRENT_GEOM_VERSION=5`"，而当前源码常量已经是 6（`ingest.py:67`，含 v6 变更记录）
——**文档相对源码有滞后**，新包不要以文档里的版本号为准。

---

## 3. 六个设备族与参数表

来源：`generator_plugin.py` 的六个 pydantic Config 类（家族层校验，2.2 节）+
`EMOW/docs/guide/06-device-variables.md` + `EMOW/docs/guide/07-device-inventory.md`
（面向人的参数/拓扑文档，两者字段名逐一核对一致）。典型构造范围取自
`EMOW/src/em_ic_opt_workflow/profile_validation.py:269-271` 注释（"mid-range of the
reference sweep campaigns (od 60-240, w 4-10, s 2-4, lead 20)"）——这是 `validate-profile
--generate` 冒烟自检用的通用参考范围，**不是任何工艺的 DRC 数值**，可以安全引用。

### 3.0 全家族公共字段（`_CleanPortDeviceConfigBase`，generator_plugin.py:161-193）

| 字段 | 类型/约束 | 含义 |
|---|---|---|
| `process_profile` | 必填 `str` | 见第 4 节 |
| `port_order` | 必填 `list[str]`，唯一、匹配 `[A-Za-z0-9_]+` | 见 1.5，按家族固定 |
| `ground_fixture` | 必填 `CleanPortGroundFixtureConfig` | M1 地环 + 每端口一个倒角 stub，见下 |
| `drc_check` | `StrictBool`，默认 `True` | 见第 5 节 |

`CleanPortGroundFixtureConfig`（`generator_plugin.py:133-158`）：`inner_margin_um`(>0)、
`ring_width_um`(>0)、`stub_width_um`(`float | None`，默认 `None` = 跟随各端口自身引线宽，
2026-07-M13 起支持)、`stub_length_um`(>0)、`stub_chamfer_um`(≥0)、
`stub_width_by_port_um`(按语义端口名覆写，如 `{"CTP": 3.0}`)。

`CleanPortPgsConfig`（`EMOW/src/em_ic_opt_workflow/devices/clean_port/pgs.py:13-18`，
可选鱼骨地屏蔽，仅 `ind_sym`/`xfm_bs`/`xfm_ms` 支持）：`strip_width_um`(>0)、
`strip_spacing_um`(>0)、`margin_um`(>0)，三者均必填正数；生成的实际条数/面积/连接方式
记在 `geometry_manifest.json` 的 `pgs_geometry` 键下（见 1.4）。

### 3.1 `clean_port_ind_sym`（对称电感）—— `CleanPortIndSymConfig`

拓扑：单圈直接环；多圈才有跨线（下一层导体，由内核按 profile 实际邻层推导，不是
`bottom_metal - 1` 这种裸算术）。端口集：`[P1, N1]`，或加 CT 后 `[P1, N1, CT]`。

| 字段 | 类型/约束 | 含义 |
|---|---|---|
| `outer_diameter_um` | float, >0 | 外径（典型参考范围 60–240 um） |
| `width_um` | float, >0 | 线宽（典型 4–10 um） |
| `spacing_um` | float, >0 | 匝间距（典型 2–4 um） |
| `opening_um` | float, >0 | 半开口；引线内边缘各偏 ±opening，内边缘总间隔 2×opening |
| `lead_length_um` | float, >0 | 引线长度（典型 20 um） |
| `turns` | int, ≥1 | 匝数 `NT` |
| `top_metal` | str，非 M1 | 绕组主层 |
| `bottom_metal` | str，非 M1 | **兼容字段，不控制实际跨线层**（实际跨线层由 profile 实际邻层推导） |
| `ct_metal` | `str \| None` | 可选中心抽头层；必须比 `top_metal` 至少低两个**实际**导体层级 |
| `pgs` | `CleanPortPgsConfig \| None` | 可选鱼骨屏蔽 |
| `straight_extension_um` | float, ≥0，0.01 的整数倍 | 可选横向直段拉伸，默认 0（=不变） |

CT 合同（`CleanPortIndSymConfig._ct_contract`，`generator_plugin.py:247-280`）：
`ct_metal is None` 时 `port_order` 必须严格等于 `["P1","N1"]`；否则必须严格等于
`["P1","N1","CT"]`，且 `ct_metal` 必须比 `top_metal` 至少低两级**真实存在的**导体
（N65 AP 下面是 M9 不是 M10，所以"两级"按真实层序算，不是数字减法——README 117 行、
`_metal_stack_index_or_none`/`_real_leg2_index` 等处反复强调这一点）。

### 3.2 `clean_port_xfm_bs`（broadside 单圈变压器）—— `CleanPortXfmBsConfig`

拓扑：初/次级各一圈、不同金属层，无 CT 时本体无跨线 via。端口：`P1/N1`=初级，
`P2/N2`=次级，基座固定 `[P1,N1,P2,N2]`，CT 追加为 `CTP`/`CTS`。

| 字段 | 约束/用途 |
|---|---|
| `primary_outer_diameter_um` / `secondary_outer_diameter_um` | 各 >0，独立外径 |
| `primary_width_um` / `secondary_width_um` | 各 >0 |
| `primary_opening_um` / `secondary_opening_um` | 各 >0 |
| `primary_lead_length_um` / `secondary_lead_length_um` | 各 >0 |
| `center_spacing_um` | ≥0，且 ≤ `(OD_P+OD_S)/4`（配置层 `_center_spacing_keeps_overlap` 与 pcell 层双重强制；超过则两环失去纵向重叠，不再算变压器） |
| `primary_metal` / `secondary_metal` | 必须不同，均非 M1；不要求谁更高 |
| `ct_primary_metal` / `ct_secondary_metal` | 可选，各自须低于对应绕组层；抽头引线朝对侧绕组方向出线到器件外缘 |

### 3.3 `clean_port_xfm_ms`（单圈+多圈阻抗变换）—— `CleanPortXfmMsConfig`

拓扑：较高层单圈（P1/N1）+ 较低层多圈（P2/N2，跨线用其下一实际层）。

| 字段 | 约束/用途 |
|---|---|
| `single_outer_diameter_um` / `multi_outer_diameter_um` | 各 >0 |
| `single_width_um` / `multi_width_um` | 各 >0 |
| `single_opening_um` / `multi_opening_um` | 各 >0 |
| `single_lead_length_um` / `multi_lead_length_um` | 各 >0 |
| `multi_turns` | int，≥2 |
| `multi_spacing_um` | >0，多圈自身匝距 |
| `center_spacing_um` | ≥0，≤ `(single_od+multi_od)/4` |
| `single_metal` | 非 M1，且必须高于 `multi_metal` |
| `multi_metal` | 非 M1/M2（M3 起可用） |
| `ct_primary_metal` | 可选，taps 单圈，须低于 `single_metal` |
| `ct_secondary_metal` | 可选，taps 多圈（走 ind_sym 的 CT 路径），须比 `multi_metal` 至少低两个实际层级 |

`multi_turns == 2` 会命中一条特殊的"紧凑两匝"构造分支，性能特征见第 8 节（比其他匝数慢
两个数量级）。

### 3.4 `clean_port_xfm_balun`（同层巴伦）—— `CleanPortXfmBalunConfig`

拓扑：两个绕组共用同一金属层 `balun_metal`，次级必须完整嵌套在初级内部（2026-09-22 起
并排模式已下线——不重叠的两个同层环只是两个独立电感，不算 balun）；次级引线经
`ESCAPE_ME`（默认 `balun_metal` 下方最近实际导体）逃逸。**没有 `pgs`/`straight_extension_um`
字段**（这两个可选项当前只支持 ind_sym/xfm_bs/xfm_ms，见 3.0/`06-device-variables.md`
第 60-99 行）。

| 字段 | 约束/用途 |
|---|---|
| `primary_outer_diameter_um` / `secondary_outer_diameter_um` | 各 >0 |
| `primary_width_um` / `secondary_width_um` | 各 >0 |
| `spacing_um` | >0，共用匝距 |
| `primary_opening_um` / `secondary_opening_um` | 各 >0 |
| `primary_lead_length_um` / `secondary_lead_length_um` | 各 >0 |
| `primary_turns` / `secondary_turns` | 各 int ≥1，可不同 |
| `center_spacing_um` | ≥0，且次级须完整套在初级内部（`_secondary_nests_inside_primary`） |
| `balun_metal` | 非 M1/M2 |
| `ct_primary_metal` / `ct_secondary_metal` | 可选，均须低于 `balun_metal` |

### 3.5 `clean_port_xfm_tw`（"twisted"同层交叠变压器）—— `CleanPortXfmTwConfig`

拓扑：`ring_count`（`NR`，奇数 ≥3）个同心八边形环，P（逆时针）/S（P 的镜像，顺时针）
各占一半环、各 `NR/2` 匝，相邻环边界用显式 dive/同层跨线连接。**唯一没有 CT 选项的家族**
（没有 `ct_*_metal` 字段，`extra="forbid"` 直接拒绝任何尝试）；**没有独立初/次级线宽**
（P/S 共用同一 `width_um`）。

| 字段 | 约束/用途 |
|---|---|
| `outer_diameter_um` / `width_um` / `spacing_um` | 各 >0 |
| `ring_count` | int，奇数且 ≥3（`NR`，不能拿别的家族的 `turns` 替代） |
| `opening_p_um` | >0，P 侧两 stub **内边缘总间隔** |
| `opening_n_um` | >0，N 侧两 stub 内边缘总间隔 |
| `lead_length_um` | >0 |
| `top_metal` | 非 M1/M2（dive 腿在其下一实际层） |

已知问题（第 10 节详述）：TW 的开口上界校验语义与其他家族不一致，可能误拒合法大开口
（`07-device-inventory.md` 当前几何限制第 2 条）。

### 3.6 `clean_port_xfm_il`（交叉指变压器）—— `CleanPortXfmIlConfig`

拓扑：P/S 在同一金属层交替占据径向环带，**等匝数、等线宽、共用间距**；每匝跨线拆成两层
（`top_metal-1`/`top_metal-2` 的真实邻层）。

| 字段 | 约束/用途 |
|---|---|
| `outer_diameter_um` / `width_um` / `spacing_um` | 各 >0 |
| `turns` | int ≥2，**同一个值同时用于 P 和 S**（没有独立 `primary_turns`/`secondary_turns`） |
| `opening_p_um` / `opening_s_um` | 各 >0 |
| `lead_p_um` / `lead_s_um` | 各 >0（注意字段名不是 `lead_length_um`） |
| `top_metal` | 非 M1/M2/M3（跨线占两层） |
| `ct_primary_metal` / `ct_secondary_metal` | 可选，**方向由与 `top_metal` 的相对位置决定**：向下必须至少低 3 级（跨越两层跨线），向上没有下限；`ct_primary_metal` 向下这一方向在几何上**永远不可行**（会被跨线窗口挡住），实践中只能向上——若 `top_metal="AP"`（栈顶）则 `ct_primary_metal` 在任一方向都不可行，这是结构性限制不是 bug |

---

## 4. Process profile 机制

### 4.1 `rule.yaml` 的结构（以公开虚构 profile `demo_6m` 为例，可以放心引用具体数值）

Pydantic schema 定义在
`EMOW/src/em_ic_opt_workflow/geometry/process_rules.py:227-292`（`ProcessRuleProfile`），
顶层字段：

```yaml
schema_version: process-rule-profile-v1     # 目前唯一支持值，process_rules.py:238-243
process_id: demo_6m
units: {length: um}
coverage: {...}          # 声明本 profile 覆盖了哪些规则类别（下面详述）
layer_catalog: {conductors: {...}, vias: {...}, markers: {...}}
emx_stack: {geometry_scaling, conductors: {...thickness_um}, via_models: {...}}
layout_rules: {metal_width_space: {...}, via_primitives: {...}, passive_region: {...}}
```

（完整样例：`EMOW/process_data/profiles/demo_6m/rule.yaml`，126 行，README 式注释自称
"ALL VALUES ARE INVENTED...safe to publish...the ONLY complete example of
process-rule-profile-v1"。）

- **`layer_catalog.conductors`**：每个导体（`M1`..`M6`）声明 `drawing: [layer,datatype]`、
  `pin: [layer,datatype]`（EMX 端口标签层）、`emx_name`、`class`（如
  `thin_metal`/`thick_top_metal`）。对应 pydantic 类 `ConductorRule`
  （`process_rules.py:43-51`）。
- **`layer_catalog.vias`**：每个 via（`VIA1`..`VIA5`）声明 `drawing`、`emx_name`、
  `connects: [下层, 上层]`（`ViaRule`，`process_rules.py:53-68`，校验 `connects` 恰好
  两个不同导体）。
- **`layer_catalog.markers`**：辅助层（如 `PASSIVE` 无源器件区域标记），`MarkerRule`
  （`process_rules.py:71-77`）。
- **`emx_stack`**：EMX 仿真用的物理栈——每层厚度 `thickness_um`、每个 via 的
  `emx_effective_size_um`（`EmxStack`/`EmxConductorStackRule`/`EmxViaModelRule`，
  `process_rules.py:87-105`）。
- **`layout_rules.metal_width_space`**：每个导体的 `min_width_um`/`max_width_um`/
  `min_space_um`（`MetalWidthSpaceRule`，`process_rules.py:108-113`——三者均可选，
  `None` 表示该规则未建模）。
- **`layout_rules.via_primitives`**：每个 via 的 `cut_size_um: [w,h]`、
  `min_cut_space_um`、`min_enclosure_um: {导体名: 值}`（`ViaPrimitiveRule`，
  `process_rules.py:116-121`）。
- **`layout_rules.passive_region`**：无源器件区域专属规则集合
  （`PassiveRegionRules`，`process_rules.py:206-216`）：
  - `marker`：引用哪个 marker 层。
  - `passive_via_array_coverage`：`{modeled: [...], not_yet_modeled: [...]}`，
    声明"哪些 via 的阵列规则已经建模、哪些还没"（**规则覆盖缺口声明，不是 DRC 禁令**，
    `PassiveViaArrayCoverage.docstring` 原话，`process_rules.py:140-147`）。
  - `via_array_rules`：已建模 via 的阵列级规则 `{min_count, max_space_um}`
    （`PassiveViaArrayRule`，124-129 行）。
  - `via_restrictions` / `metal_restrictions`：**引用型规则**——每条都带
    `source_text`（原始 deck 文字引用）+ `name`（规则 ID，如 demo 里的 `DEMO.V.1`/
    `DEMO.M.1`），可选 `exception`（豁免条件）。**这些从 2026-07-19 起（"n28-rules-slim"
    用户指令）只作为文档引用保留，不再参与生成期门禁**（`PassiveViaRestriction`
    docstring，`process_rules.py:181-196`；`plan_passive_via_array` docstring，
    `rule_adapter.py:141-153`："Neither `passive_region.via_restrictions`...nor
    `passive_via_array_coverage`'s `not_yet_modeled` classification fails generation
    closed any more")。真正门禁生成的只剩五类**几何**规则：线宽、via 切割尺寸、线间距、
    via 到金属边缘包围、via 到 via 间距——外加已建模 via 自己的 `min_count`/
    `max_space_um`。
  - `wide_parallel_spacing`：条件加严规则——`{metals: [...], when_width_gt_um,
    when_parallel_length_gt_um, min_space_um}`，宽线/长平行线额外加严间距
    （`ParallelSpacingRule`，130-137 行）。
- **`coverage`** 顶层字段：**必须和 `layout_rules`/`layer_catalog` 实际内容一一对应**，
  `ProcessRuleProfile._coverage_matches_declared_rules`（`process_rules.py:245-292`）
  做交叉校验，任何不一致直接 `ValidationError`（例如 `coverage.metal_width_space`
  声明的导体集合必须和 `layout_rules.metal_width_space` 的 key 集合完全相等）。

真实 profile（`n28_1p10m`/`n65_1p9m`）结构完全相同，只是数值和规则条目更多；本报告
只引用规则 ID（如 `IND.R.1`、`VIA7`/`VIA8`/`VIA9`、导体名 `M1`-`M10`/`AP`），不引用
任何门槛数值。

### 4.2 加载/解析：搜索路径与 `validate-profile`

`get_process_rule_profile(profile_id, *, extra_dirs=())`——
`EMOW/src/em_ic_opt_workflow/geometry/process_rules.py:346-351`：读 YAML
（`yaml.safe_load`）后 `ProcessRuleProfile.model_validate(data)`，再用
`_with_names()`（332-343 行）把每条规则的 dict key 回填成自己的 `name` 字段（方便报错
消息里带上规则名）。**不带任何缓存**（没有 `lru_cache`；本报告全仓 grep 确认
`geometry/`、`devices/clean_port/`、`schemas.py`、`profile_validation.py` 里都没有
`lru_cache`/`functools.cache`）——每次调用都会重新读文件、重新跑一遍 pydantic 校验，
现场实测（demo_6m，小文件）单次约 7ms（见第 8 节）。

搜索顺序（`_profile_path()`，`process_rules.py:303-329`）：
1. 调用方显式传入的 `extra_dirs`（如 `validate-profile --profile-dir`）；
2. 环境变量 `EM_IC_OPT_PROFILE_DIRS`（`PROFILE_DIRS_ENV_VAR`，295 行；
   `os.pathsep` 分隔多个目录，按声明顺序第一个命中的赢，
   `_external_profile_dirs()`，298-300 行）；
3. 包内资源 `em_ic_opt_workflow/geometry/process_rule_profiles/<id>/rule.yaml`
   （`importlib.resources`，316-319 行）——**当前这个目录在包里是空的**（本报告现场
   `find EMOW/src/em_ic_opt_workflow/geometry/process_rule_profiles -type f` 返回
   0 个文件），注释说明这是"留给未来打包的通用 profile"的位置，NDA 衍生的 profile
   （`n28_1p10m`）"are never packaged"。
4. 三处都找不到则报错，错误信息里点名 `EM_IC_OPT_PROFILE_DIRS`（324-329 行）。

三个真实存在的 profile 目录都在仓库自己的 `process_data/profiles/`
下（`n28_1p10m`、`n65_1p9m`、`demo_6m`——`EMOW/process_data/profiles/`），**不随 wheel
打包**（`EMOW/pyproject.toml:56-60` 注释明确："the old geometry/process_rule_profiles/
**/*.yaml glob is gone: the directory never ships"）。测试环境靠
`EMOW/tests/conftest.py:24-32` 的 session 级 autouse fixture 把
`EM_IC_OPT_PROFILE_DIRS` 指向 `process_data/profiles`；**新包如果要复用这套 profile
机制，必须自己决定 profile 数据怎么分发**（打包进新 wheel？继续用同一个环境变量指向
旧仓库路径？各有取舍，本报告只指出这是个需要新决策的点，不替新包做决定）。

CLI：`hermes-workflow validate-profile <profile_id> [--profile-dir DIR]... [--proc FILE]
[--generate] [--family NAME]... [--out DIR]`
（命令注册：`EMOW/src/em_ic_opt_workflow/cli.py:199-269`，`pyproject.toml:49` 里
`hermes-workflow = "em_ic_opt_workflow.cli:app"`）。四个校验阶段
（`validate_profile()`，`EMOW/src/em_ic_opt_workflow/profile_validation.py:500-564`）：
1. **schema**——`ProcessRuleProfile.model_validate`，失败则逐条 `<yaml路径>: <消息>`
   （`_render_validation_error`，99-104 行）；
2. **consistency**——schema 校验管不到的引用完整性：`emx_stack.conductors` 是否都在
   `layer_catalog.conductors` 里、`vias.*.connects` 引用的导体是否存在、
   `passive_region.marker` 是否在 marker 目录里等（`_consistency_stage`，107-184 行）；
3. **emx-names-vs-proc**（可选，给 `--proc`）——每个 `emx_name` 必须作为 token 出现在
   站点 EMX proc 文件里（`_proc_stage`，187-221 行，"the proc is EMX's naming
   authority"）；
4. **generation**（可选，`--generate`）——**跑一遍真正的生产插件路径**，为六个家族
   各构造一个"canonical smoke device"（`_canonical_config`，265-376 行，就是本报告
   3.x 节参数表里那些典型范围值的来源）并做生产同款 DRC 审计（见第 5 节），
   `GENERATION_FAMILIES` 声明了每个家族至少需要的导体栈深度（`ind_sym`/`xfm_bs`/
   `xfm_ms`/`xfm_balun`/`xfm_tw` = 3，`xfm_il` = 4，`profile_validation.py:49-56`）,
   栈太浅直接 `SKIP` 而不是 `FAIL`。

### 4.3 生成器如何消费 profile：`rule_adapter.py`

`process_rule_context(profile_id) -> ProcessRuleContext`
（`_pcell_core.py:173-179`）是 pcell 层唯一的 profile 入口：
```python
@dataclass(frozen=True)
class ProcessRuleContext:
    profile_id: str
    adapter: "GeometryRuleAdapter"
    passive_region: bool = True
```
`GeometryRuleAdapter`（`EMOW/src/em_ic_opt_workflow/geometry/rule_adapter.py:66-241`，
`frozen dataclass`，包一层 `ProcessRuleProfile`）暴露的方法：
- `layer(metal) -> LayerSpec`（74-82 行）：拿 `drawing`/`pin`/`emx_name`/`layer_class`。
- `metal_rule(metal) -> MetalRuleSpec`（84-96 行）：`layer()` 加上
  `min_width_um`/`max_width_um`/`min_space_um`。
- `via(via_name)` / `via_between(lower, upper) -> ViaRuleSpec`（98-115 行）：拿
  `cut_size_um`/`min_cut_space_um`/`min_enclosure_um`。
- `plan_passive_via_array(*, lower_metal, upper_metal, available_width_um,
  available_height_um) -> ViaArrayPlan`（131-207 行）：给定一块矩形窗口，算出能放下的
  via 阵列的行列数、间距、总尺寸——这是 pcell 层唯一"跟 profile 要具体阵列布局"的地方
  （`_add_process_via_cuts`，`_pcell_core.py:545-588`，直接消费它的返回值）。
- `manifest() -> dict`（216-241 行）：给人看的覆盖范围摘要（不含具体规则数值，只有
  分类统计）。

`ProcessRuleContext` 作为一个不透明的 `process=` 参数，贯穿六个 family 函数和几乎所有
guard 函数（第 5 节）；`process=None` 是"reference 模式"（复现历史 PCell 行为，**不是
任何工艺的 DRC-proof**，README 20-23 行明确声明），实际生产路径**恒定传入非 None 的
`ProcessRuleContext`**（`generator_plugin.py` 每个 `generate()` 都写死
`process=p.process_rule_context(config.process_profile)`）。

---

## 5. DRC 审计：三层不同的检查，只有一层在生成路径里默认跑

任务问"DRC audit 是生成路径的一部分还是独立工具"——答案是**都是，取决于说哪一层**：

### 5.1 第一层：构造期内联 guard（永远跑，无法关闭）

`_pcell_guards.py`（`EMOW/src/em_ic_opt_workflow/devices/clean_port/_pcell_guards.py`）
里的函数在**几何还在构造过程中**就用 klayout `Region` 布尔运算做 fail-closed 检查，
不是生成完之后再审——例如 `_check_opening`（35-43 行，超过 `max_opening` 直接
`PortError`）、`_check_trace_rules`（46-71 行，线宽/引线间隙）、`_check_winding_fit`
（74-103 行）、`_check_ind_winding_segments`（146-173 行，绕组分段数是否符合预期匝数，
防止同层意外短接）、`_heal_seam_notches`（176-251 行，自动缝合交叉口的锐角空隙，仅
process 模式）、`_xfm_net_short`（322-337 行，两个绕组网络在任意层上短路则 fail）。
**这些和"是否 `drc_check=True`"完全无关**——它们是构造函数本体的一部分，永远执行；
`drc_check` 配置项管的是下面 5.3 节那层。

### 5.2 第二层：写盘后结构性复核（生成路径内，`_write_geometry_outputs` 里，永远跑）

两个函数都定义在 `generator_plugin.py`（不是 `geometry/drc_audit.py`），都用 klayout
**重新读一遍刚写的 GDS**（和内存里的 `Cell` 是独立数据源）：

- `audit_via_landing(gds_path, process_profile) -> dict`
  （`generator_plugin.py:769-820`）：逐个 via 层检查每个 cut 是否同时落在上下两层金属上
  （"fail closed if any via cut lacks landing metal on BOTH connected layers"），
  docstring 点名这正是 2026-07-09 事故的缺陷类别（"72/144 floating VIA9 cuts"导致 EMX
  网格发散、压垮服务器）。返回 `{"status": "pass", "vias_checked": N}`。
- `audit_port_lattice(gds_path, ports, process_profile) -> dict`
  （`generator_plugin.py:823-919`）：对 manifest 里每个端口的**精确坐标**（不是最近点
  搜索——docstring 特意强调"Zero tolerance, no nearest-point search"，防止误判到相邻
  端口的字形上）验证三件事：坐标落在 `lead_zone_nm` 的边上、坐标处有该端口名字的文本
  标签、坐标落在该端口金属层的实际多边形内部。这是**独立于** `_check_port_lattice_invariant`
  （内存态自洽性检查）的复核——只信"klayout 从文件里解析出来的东西"。返回
  `{"status": "pass", "ports_checked": N}`。

`_write_geometry_outputs`（922-980 行）在 `p.write_gds(...)` 之后**无条件**依次调用
这两个函数（940-957 行），并且用 `requires_vias` 参数交叉验证"该不该有 via"
（948-956 行，`requires_vias=True` 但 `vias_checked==0` 直接报错，反之亦然——防止
family 声明和实际几何脱节）。**这一层不受 `drc_check` 配置控制，永远跑**。

### 5.3 第三层：独立的规则级 DRC 审计（`geometry/drc_audit.py`，受 `drc_check` 开关控制）

`audit_gds(gds_path, profile_id: str) -> Report`——
`EMOW/src/em_ic_opt_workflow/geometry/drc_audit.py:286-310`。这是**唯一**读 profile 里
`layout_rules`（线宽/间距/via 包围规则）逐条跑的地方：`_audit_metal_rules`（194-236 行，
`min_width`/`min_space`/`max_width`）、`_audit_wide_parallel_spacing`（160-191 行，
条件加严规则）、`_audit_via_enclosure`（239-274 行，目前只对 `RV` 这一类 via 跑，
`_AUDITED_VIAS = ("RV",)`，283 行——其余 via 的阵列合法性已经在生成期由
`plan_passive_via_array` 保证，这里不重复审）。

**这个模块本身不在 `generator.generate()` 调用链里**——`generator_plugin.py` 从不
`import` 它。它被**上一层 orchestration**（`em_candidate_preparation.prepare_em_candidate`，
`EMOW/src/em_ic_opt_workflow/em_candidate_preparation.py:196-247`）显式调用，且**只有
`drc_check` 为真时才跑**（`_drc_check_enabled()`，179-193 行；配置模型没声明
`drc_check` 字段的第三方插件视为"未显式关闭"，默认仍然跑，见该函数 docstring）：
```python
if _drc_check_enabled(geometry_config):
    expected_conductors = require_layers_from_config(request.generator_id,
                                                      geometry_config.model_dump())
    report = audit_gds(geometry.gds_path, geometry_config.process_profile)
    record = product_scope_record(report, expected_conductors,
                                  ignore_findings=frozenset({("max_width", "M1")}))
    if record["outcome"] != "pass":
        raise DrcViolationError(...)
```
`require_layers_from_config(generator_id, config: dict) -> list[str]`
（`drc_audit.py:526-547`）：按家族推导"这次配置理应画到哪些导体层"（含隐式跨线层，
六个家族各自的推导函数在 `_EXPECTED_RECIPES` 表里，`drc_audit.py:516-523`），生成器
id 不在表里直接 `ValueError`（不允许静默降级成"只查绕组主层"）。
`product_scope_record(report, expected_conductors, *, ignore_layers=(), ignore_findings=())
-> dict`（`drc_audit.py:550-618`）：把原始 `Report` 收窄成"产品视角"结论
——`outcome` 三选一：`"pass"`/`"fail"`/`"missing_layer"`（**某个理应出现的导体层
一个检查都没跑到**，例如几何意外没画出这层，也算不通过，不能靠"零违规"蒙混）。
`em_candidate_preparation.py:230-232` 里唯一的豁免是 `("max_width", "M1")`——因为
`add_ground_fixture` 在 M1 上画的地环/stub 是每个生成器都强制附带的，其宽度天然可能
超过 M1 的 `max_width_um`，这与产品绕组本身无关。

**结论**：`audit_gds` 这个"规则级 DRC 审计"是一个可以独立调用的工具（`Report`/
`Violation`/`canonical_conductor` 都通过 `geometry/__init__.py` 懒加载导出，
`EMOW/src/em_ic_opt_workflow/geometry/__init__.py:19-39`，专门绕开对 klayout 的强制
导入——`import em_ic_opt_workflow.geometry` 本身不会拉 klayout），但在**当前生产流程
里**它被接到了"候选准备"这一步（`prepare_em_candidate`），逻辑上属于"pcell 生成之后、
EMX 之前"的**门禁**，而不是 pcell Stage 内部的一部分。新包设计 `pcell` Stage 时，
这一层可以选择：(a) 塞进 `pcell` Stage 的 `run()` 尾部（更贴近"生成即校验"）；
(b) 做成 `pcell` 和 `emx` 之间单独的一个校验 Stage/gate（更贴近现状的"下一步"定位，
也更符合 `Stage` 协议里各阶段职责单一的风格）。两种都合理，本报告不替新包决定，只
指出**现状是 (b)**。

### 5.4 成本（现场实测，见第 8 节数据来源）

- 5.1 内联 guard 的成本已经计入"构造耗时"，大部分家族/匝数下可忽略（<5ms），但
  `ind_sym`/`xfm_ms` 的"两匝紧凑绕组"分支例外——它本身就是靠反复跑 klayout
  `Region.merged()`/`width_check()`/`space_check()` 做候选搜索，构造耗时因此暴涨到
  ~300ms（第 8/10 节详述，这不是"审计"的锅，是构造算法本身的搜索开销）。
- 5.2 的两个复核函数各自约 7-9ms（demo_6m profile，含重新读一遍 GDS 文件）。
- 5.3 的 `audit_gds` 单独调用约 8-9ms（同一 GDS 文件的第三次独立读取）。
- 也就是说一条完整的"生成 + 结构复核 + 规则级 DRC"链路，会把同一个刚写出来的 GDS
  文件**用 klayout 重新解析三次**（5.2 两次 + 5.3 一次），单次生成总耗时里，这部分
  I/O/解析开销是个位数到十位数毫秒级别，相对 5.1 的构造搜索开销（可能到 300ms）是
  次要项，但确有"读三遍"的重复劳动空间——如果新包决定 pcell Stage 自己整合了 DRC，
  值得考虑一次性把 klayout `Layout` 解析结果传给三个检查函数复用，而不是各自
  `kdb.Layout(); ly.read(...)`。

---

## 6. Geometry fingerprint 该 hash 什么

### 6.1 这不是纯理论问题：`ic-opt-modular` 自己的 `Stage` 协议已经要求了它

`ic-opt-modular/src/ic_opt/eval/stage.py:53-62`：
```python
class Stage(Protocol):
    name: str
    level: Literal["point", "child"]
    resources: Resources

    def fingerprint(self, inp: Any) -> str | None:
        """Cache key for this input, or None when the stage must always run."""
        ...
    def run(self, inp: Any, ctx: StageContext) -> Any: ...
```
`ic_opt/eval/engine.py` 第 11 行注释："Stage caching (`Stage.fingerprint`) is reserved
for the first cacheable [stage]"；目前引擎只用 `spec.fingerprint()` +
`pipeline_fingerprint()` 做整点级去重（`engine.py:62,70,101`），**从未调用任何单个
`Stage.fingerprint()`**。也就是说 `pcell`（或 `emx`）大概率会是第一个真正实现这个方法
的 Stage——`DESIGN_CN.md:191` 已经把 `pcell` 的产物称为"EM artifact"，并说
"`EM artifact` + `EmCacheStore` = emx 的阶段缓存"。以下先讲 EMOW 里已有的两套相关
先例，再给出对新 `pcell` Stage 的具体建议。

### 6.2 EMOW 已有先例 1：`em_cache.py` 的 `fingerprint_em_cache_key`（离任务问题最近的代码）

`EMOW/src/em_ic_opt_workflow/em_cache.py:14-51`：
```python
@dataclass(frozen=True)
class EmCacheKey:
    generator_id: str
    geometry_config: dict[str, Any]
    emx_config: dict[str, Any]
    process_file: Path
    gds_sha256: str

def fingerprint_em_cache_key(key: EmCacheKey) -> str:
    sanitized_emx_config = {k: v for k, v in key.emx_config.items()
                            if k not in _CANDIDATE_PATH_FIELDS}   # gds_file/s_file/log_file
    payload = {
        "generator_id": key.generator_id,
        "geometry_config": key.geometry_config,
        "emx_config": sanitized_emx_config,
        "process_file": str(key.process_file),
        "process_file_sha256": _sha256_file(key.process_file),
        "gds_sha256": key.gds_sha256,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
```
关键实现细节（`em_artifacts.py:193-213`，调用方 `_fingerprint_prepared`）：

- `geometry_config` **不是**重新拼一份，而是直接读**已经落盘的**
  `geometry_manifest.json` 的 `geometry.config` 字段（`_geometry_config_for_cache`，
  `EMOW/src/em_ic_opt_workflow/em_artifacts.py:45-61`）——即 1.4 节说的
  `config.model_dump(mode="json")` 那份 canonical JSON。
- `gds_sha256` = `sha256_file(prepared.geometry.gds_path)`，**对已经生成好的 GDS 文件
  字节直接取内容哈希**。
- 排除 `gds_file`/`s_file`/`log_file` 这三个"候选专属路径"字段是有历史教训的
  （代码注释指向 2026-07-16 的 bug review："they made every fingerprint unique per
  candidate, so the cache could never hit across candidates"）——**任何包含
  candidate/run 专属路径的字段混进 fingerprint payload，都会让缓存永远不命中**，
  这是新包设计 `pcell.fingerprint(inp)` 时最值得直接抄的一条教训。

**这套机制的真实用途和局限**：`EmCacheStore`（`em_cache.py:54-107`）用这个 fingerprint
做的是 `strict_provenance` 模式下的 **EMX 结果缓存**（`_produce_em_candidate_artifact`，
`em_artifacts.py:233-`；只有 `em_cache.enabled and mode=="strict_provenance"` 才生效，
`EmCacheSettings.mode: Literal["strict_provenance"]` 是当前唯一取值，
`EMOW/src/em_ic_opt_workflow/schemas.py:613-615`）。**它在 pcell 生成之后才计算**
（依赖 `gds_sha256`，GDS 必须已经写出来），所以它只能让"跳过 EMX 重跑"成立，**不能
让"跳过 pcell 生成本身"成立**——这正是新 `pcell` Stage 需要而 EMOW 没有现成答案的部分：
`Stage.fingerprint(inp)` 必须能在 `run()` 之前、只凭输入就算出来。

### 6.3 EMOW 已有先例 2：`device_db/ingest.py` 的 `canonical_hash` + `geom_version` 轴

`EMOW/src/em_ic_opt_workflow/device_db/ingest.py:90-108`：
```python
def _canon(value):                    # 递归规范化：bool 保留、int/float 统一转 "f:{float!r}"
    ...                                # （拒绝 NaN/Inf），dict 按 key 排序，list/tuple 递归
def canonical_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(_canon(payload), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
```
`has_sample()`（216-242 行）的样本身份判定条件是
`(family, process_profile, emx_settings_hash, params_hash, geom_version,
family_schema_revision)` 六元组——**把"构造代码版本"（`geom_version`，人工维护常量，
见 2.4 节）显式作为身份的一部分**，而不是指望"代码变了、GDS 字节自然会变、gds_sha256
自然会不一样"。这里的 `params` 是 device_db 采样库自己的一小撮"库坐标"（如 ind_sym
只有 `outer_diameter_um`/`width_um`/`spacing_um`/`turns` 四个），不是完整 pcell 生成器
配置——所以这套机制的复用范围比 6.2 窄，但"canonicalize→sha256"这个手法和"把构造算法
版本单列一个轴"这个设计意图，都值得借鉴。

`ic_opt/spec.py:262-264` 的 `Spec.fingerprint()` 用的是同一个配方（`sort_keys=True`
的 JSON 再 sha256），说明新包自己已经把这个惯例定下来了：
```python
def fingerprint(self) -> str:
    payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
```

### 6.4 `gds_compare.py`：这个仓库自己对"相同几何"的权威定义——明确排除了字节哈希

`EMOW/src/em_ic_opt_workflow/geometry/gds_compare.py:1-11`（模块 docstring，原文）：

> "Top-cell names, polygon fragmentation, hierarchy and GDS byte hashes are **not
> geometry**: two files are physically equal when every layer's merged drawing
> XORs to empty and their text labels (string, position, transformation) match.
> GDS properties are not compared. This is the project's criterion for
> 'default geometry did not drift'."

`compare_gds(left, right) -> dict`（93-94 行）→ `compare_physical()`（62-90 行）：
对每一层取 `kdb.Region` 的 merged 表示做 XOR，为空才算这一层相同；标签比较
`(layer, text, x, y, rotation, mirror, size, font)` 元组集合是否相等
（`load_physical()`，31-59 行）。**这个仓库明确认为"GDS 字节相同"既不是"几何相同"的
必要条件，也不是充分条件的唯一来源**——同一份几何可以因为 top cell 改名、多边形切分
方式不同、层级结构不同而字节不同但物理等价；反过来 hierarchy 相同字节也不保证物理
一致（这也是为什么这个仓库在做"代码改动后几何有没有漂移"的回归验证时，用的是
`compare_gds`，不是简单 diff 或者 sha256 比较）。

### 6.5 建议：新 `pcell` Stage 的 `fingerprint(inp)` 该怎么写

结合 6.1-6.4，以下是给新包的具体建议（不是照抄 EMOW 现有代码，是综合其经验教训后的
设计，请新包自行决定是否采纳）：

**`fingerprint(inp)` 必须在 `run()` 之前、只用输入就能算出来**，不能依赖生成结果
（这点排除了直接照抄 6.2 的 `gds_sha256` 思路）。建议 payload：

```python
payload = {
    "generator_id": generator_id,
    "plugin_module_digest": sha256_file(resolved_plugin_path),  # 见下
    "config": merged_config_dict,          # 与 config_model.model_validate() 收到的完全一致
    "process_profile": process_profile_id,
    "process_profile_digest": sha256_file(resolved_rule_yaml_path),  # 见下
}
fingerprint = sha256(json.dumps(_canon(payload), sort_keys=True, separators=(",",":")))
```

三个不能省的原因，均有 EMOW 里的真实反例支撑：
1. **`config` 必须是"合并后送进 pydantic 校验的那份完整 dict"，不能只是 Point 里
   varying 的那几个字段**——`fixed_parameters` 变了几何也会变，6.3 节 `device_db` 那种
   "只 hash 库坐标"的窄范围方案不适合通用 pcell Stage。这份 dict 的规范形态就是
   `config.model_dump(mode="json")`（1.4 节已确认它和 `geometry_manifest.json` 里的
   `geometry.config` 完全一致）。
2. **`process_profile_digest`（rule.yaml 内容哈希）不能省，只留 profile id 字符串
   不够**——4.2 节确认 `EM_IC_OPT_PROFILE_DIRS` 是运行时可变的搜索路径，同一个
   `profile_id`（如 `"n28_1p10m"`）在不同机器/不同时间可能解析到内容不同的文件（没有
   任何版本锁定机制），只 hash id 字符串会把"规则变了但没人告诉你"的情况误判成缓存命中。
3. **`plugin_module_digest`（生成器代码文件哈希）不能省，等价于给"构造算法版本"一个
   自动、精确、不需要人记得手动 bump 的替代品**——2.4 节展示了 EMOW 自己在
   `device_db/ingest.py` 里维护 `CURRENT_GEOM_VERSION` 这种手动计数器付出的复杂度
   （67 行大段变更记录），根因是它们没有把"这次是用哪份构造代码生成的"纳入 hash 输入。
   对 `builtin:clean_port` 这种走文件路径动态加载的插件，`plugin_module` 实际解析到的
   `.py` 文件本身取 sha256 即可（`registry.resolve_plugin_module_path` 已经能拿到这个
   绝对路径，见 1.2 节）；如果插件被拆成多个 `_pcell_*.py` 文件（本仓库现状就是这样，
   见文件清单），要么只 hash facade 文件（`pcell_inductor_port_clean.py`，因为它转手
   re-export 了全部实现，若被 re-export 的子模块变了，facade 文件的**内容**不会跟着变，
   所以这个近似不完全精确），要么把插件目录下所有 `_pcell_*.py`/`generator_plugin.py`
   一起 hash（更精确，代价是插件目录结构变化会牵动 hash 计算逻辑）。这是需要新包
   明确决策的一点，本报告只指出取舍，不代为决定。

**明确不建议放进 `fingerprint(inp)`**：任何 candidate/run 专属路径（`outdir`、
`gds_name` 本身——注意 `gds_name` 的 stem 会变成 GDS 顶层 cell 名，但这是**输出**
的命名规则，不是几何身份的一部分，两个 `gds_name` 不同但其余配置完全相同的调用
理应命中同一个 fingerprint）、`top_cell` 参数（1.5 节已确认它对实际输出没有任何影响，
放进 fingerprint 只会制造假性 miss）。

**验证/防御性哈希（可选，post-hoc，不影响 lookup）**：如果新包想要 6.2 那种"生成完
之后再校验一次实际字节是否符合预期"的双保险，可以照抄 `_sha256_file(gds_path)` 的
思路，但用 `gds_compare.py` 定义的"物理等价"标准（6.4 节）而不是原始字节哈希——因为
`write_gds()` 已确认字节级确定（6.6 节），原始字节哈希在**同一份代码**下是可靠的，
只是不如物理比较宽容（不能容忍无害的实现变化，例如未来想改多边形绘制顺序但物理不变）。

### 6.6 GDS 写入是否确定性：是，已用测试和现场实测验证

- `write_gds()`（`_pcell_core.py:498-521`）显式关闭时间戳：
  `opts.gds2_write_timestamps = False`（520 行），注释："identical geometry must
  produce identical bytes regardless of when the two writes happen"。
- 现有回归测试直接锁定这一点：
  `EMOW/tests/test_clean_port_generator_plugin.py:1508-1529`
  （`test_write_gds_bytes_deterministic_across_seconds`）：同一个 `Cell` 写两次，中间
  `time.sleep(1.05)` 跨过一个整秒边界，断言两份文件字节相等；docstring 提到这条测试
  是补在"2026-07-18 一次满测试套件跑动时，同一棵树的某个用例在跨秒边界时真的 flake 过"
  之后的。
- 本报告现场 grep 确认 `devices/clean_port/*.py` 与 `geometry/*.py` 全部文件里没有
  `random`/`numpy.random`/`uuid`/`time.time()`/`datetime.now` 的使用（结果为空）。
- **前提条件**：确定性是"同一份构造代码 + 同一个 `gds_name`"下的确定性——`gds_name`
  的 stem 决定顶层 cell 名（1.5 节），所以两次调用如果 `gds_name` 不同，字节必然不同
  （哪怕物理内容完全一样），这不是 bug，是设计如此；6.5 节据此建议 fingerprint 不纳入
  `gds_name`。

---

## 7. 确定性与副作用

### 7.1 磁盘写入：`generate()` 恰好写 3 个文件，没有隐藏的第 4 个

见 1.4 节：`{gds_name}`（GDS）、`emx_ports.txt`、`geometry_manifest.json`，三者都在
`outdir` 下，`outdir.mkdir(parents=True, exist_ok=True)`
（`generator_plugin.py:929`）。**不产出 PNG，不产出坐标 JSON**——那些是
`_pcell_demo.py` 这个独立的 demo/参考工具的产物（`_render_png`、
`_write_coordinates_json`，见 7.2），生产 `generate()` 路径完全不碰它们
（`generator_plugin.py` 顶部 import 列表里没有 `_pcell_demo`）。

`gds_name` 在写盘前经过 `validate_output_file_name()`
（`EMOW/src/em_ic_opt_workflow/path_safety.py:6-12`）校验——禁止空串/`.`/`..`、禁止
路径分隔符、禁止 Windows 盘符前缀——纯粹是防路径穿越，逻辑很小，值得直接复用而不是
重写。

### 7.2 `_pcell_demo.py`：独立的参考/演示工具，不在生产路径上，会画 PNG

`EMOW/src/em_ic_opt_workflow/devices/clean_port/_pcell_demo.py`（1055 行）：
`generate_all(out_dir)`（1002-1050 行）批量跑一组内置 `DEMOS` 字典，对每个 demo 额外
产出 `_write_coordinates_json`（823-864 行，把 GDS 里的多边形坐标、标签、`emx_ports`
块、递归实例化 log 一起转成 JSON——**这份 JSON 才是真正带坐标的"port 结构化信息"文件，
但只有 demo 工具产出，生产路径不产出**，呼应 1.4 节的缺口）、`_render_png`
（865-910 行，用 `matplotlib` 把 klayout 解析出的多边形画成 PNG 供人工检视）、
`_write_report`（912-999 行，聚合成 Markdown/JSON 报告）。这条链路现场跑测试确认要拉
`matplotlib`（`test_generate_all_produces_required_files` 触发了
`PyparsingDeprecationWarning`，来自 `matplotlib/_fontconfig_pattern.py`），**新包不需要
这条链路**——它是给人看的调试工具，不是任何契约的一部分。

### 7.3 全局状态：两处进程级模块缓存（重启才能感知插件文件变化）

- `geometry/registry.py:52-77`（`_load_plugin_module`）：按解析后绝对路径的
  `sha256` 前 16 位做 `sys.modules` key，`_PLUGIN_LOAD_LOCK`（20 行，
  `threading.Lock`）保护并发加载。
- `devices/clean_port/generator_plugin.py:113-130`（`_clean_port`）：固定 key
  `"clean_port_mod"`，`_CLEAN_PORT_LOCK`（115 行）保护。

两处都是"首次加载后缓存到进程生命周期结束"，**修改插件/facade 文件后不重启 Python
进程不会生效**（registry.py 91-98 行注释原话）。`PLUGIN_GENERATORS` 里的六个生成器
实例在模块导入时一次性构造（`generator_plugin.py:1164-1171`），是无状态单例
（1.2 节已述），线程安全性由现有测试覆盖
（`EMOW/tests/test_geometry_registry_plugin.py:655` 的
`test_load_plugin_generators_is_thread_safe`，
`EMOW/tests/test_clean_port_generator_plugin.py:1263` 的
`test_clean_port_loader_is_thread_safe`）。

除此之外**没有发现其它进程级可变全局状态**——`get_process_rule_profile`/
`get_geometry_rule_adapter` 每次都是全新对象（无缓存，4.2 节已述），`Cell`/`Shape`/
`Label`/`Port` 都是每次调用新建的普通 dataclass 实例，构造函数之间不共享可变状态。

### 7.4 并发考虑

新包如果打算并发跑多个点的 `pcell` Stage（`Resources` 声明 threads/memory，
`DESIGN_CN.md` 4.1 节），需要注意：
- 两处 `sys.modules` 缓存的加锁范围只覆盖"加载"这一刻，加载完成后多线程共享同一个
  模块对象——由于生成器/family 函数不写任何模块级可变状态（纯函数式：输入配置 →
  新建 `Cell` → 返回），并发调用 `generate()` 本身应该是安全的（现有测试也是这么假设的），
  但**没有看到专门针对"多线程同时首次触发 `_clean_port()`/`_load_plugin_module()`
  加载"之外场景的压力测试**，如果新包要在真正高并发场景下依赖这套代码，建议自行补一轮
  并发 smoke test。
- `write_gds`/`audit_via_landing`/`audit_port_lattice`/`audit_gds` 都是各自独立打开/
  关闭 `kdb.Layout()`，不共享 klayout 层面的全局状态，不同 `outdir` 下的并发写互不
  干扰（各自的 `outdir.mkdir(parents=True, exist_ok=True)` 用的是不同路径）。

---

## 8. 不依赖 EDA 工具的测试 + 单次生成耗时

"不依赖 EDA 工具"在这个仓库里的准确含义是"不依赖真实 EMX/Spectre 二进制"——**这些测试
仍然依赖 `klayout`（一个 Python 库，不是外部 EDA 工具进程）**，因为 GDS 写入/解析
本身就靠它。

### 8.1 可以直接对照/搬运的测试文件

| 文件（绝对路径） | 行数 | 现场实测结果 | 用途 |
|---|---:|---|---|
| `EMOW/tests/test_pcell_inductor_python_port_clean.py` | 6074 | 377 passed in 14.94s | 对每个内部 primitive（`base_ind_diag`/`base_xfm_cross`/`base_oct_quad`/…）和六个 family 函数的**直接模块调用**单元测试；几乎全部走"raw 函数 → `write_gds` → klayout 解析回来断言坐标"路径，不经过 pydantic config/生成器包装 |
| `EMOW/tests/test_clean_port_generator_plugin.py` | 2612 | 164 passed in 21.55s | 对六个 `CleanPortXxxConfig`/`CleanPortXxxGenerator` 的公开契约测试：校验规则、`generator.generate()` 端到端、manifest 内容、port 审计、DRC scope、确定性 |
| `EMOW/tests/test_geometry_registry_plugin.py` | 684 | 35 passed / **1 failed** in 1.83s（单独跑）；完整跑仍是 35/1（无级联失败） | `get_generator`/插件加载契约 + `prepare_em_candidate(_from_contract)`/`prepare_em_devices_from_contract` 端到端（含多器件扇出） |
| `EMOW/tests/test_geometry_rule_adapter.py` | 324 | 全部通过（合并跑，见下） | `GeometryRuleAdapter` 单元测试 |
| `EMOW/tests/test_geometry_process_rules.py` | 437 | 全部通过 | `ProcessRuleProfile`/`get_process_rule_profile` 单元测试 |
| `EMOW/tests/test_clean_port_pgs.py` | 80 | 全部通过 | PGS 屏蔽 |
| `EMOW/tests/test_clean_port_straight_extension.py` | 248 | 全部通过 | 直段拉伸 |
| `EMOW/tests/test_ms_m3_geometry.py` | 44 | 全部通过 | xfm_ms 的 M3 多圈层支持 |

（上面 5 个较小文件合并跑：`80 passed in 7.08s`，命令
`EMOW/.venv/bin/python -m pytest tests/test_geometry_rule_adapter.py
tests/test_geometry_process_rules.py tests/test_clean_port_pgs.py
tests/test_clean_port_straight_extension.py tests/test_ms_m3_geometry.py -q`。）

**没有任何一个上述测试文件依赖真实 EMX/Spectre 二进制**——全部是纯 Python + klayout。
`EMOW/tests/conftest.py:24-32` 的 autouse fixture 保证 `EM_IC_OPT_PROFILE_DIRS` 指向
仓库自带的 `process_data/profiles`，所以这些测试可以直接 `pytest tests/xxx.py` 跑，
不需要额外配置。

### 8.2 最快的单元测试内生成方式：绕开 pydantic config + 生成器包装，直接调用 family 函数

`test_pcell_inductor_python_port_clean.py:38-41` 的加载方式（README 389-393 行也这样
要求，"a bare `spec.loader.exec_module` fails inside the dataclass decorators"）：
```python
_spec = importlib.util.spec_from_file_location("pcell_inductor_port_clean", MOD_PATH)
port = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = port          # 必须先注册到 sys.modules，dataclass 装饰器才能找到类型引用
_spec.loader.exec_module(port)
```
之后直接调用（`test_ind_sym_generator_parity_with_direct_module_call`，
`test_clean_port_generator_plugin.py:244-266`）：
```python
ctx = p.process_rule_context("n28_1p10m")
fx = p.GroundFixtureConfig(inner_margin_um=15.0, ring_width_um=50.0,
                           stub_width_um=5.0, stub_length_um=2.0, stub_chamfer_um=0.0)
cell = p.ind_sym(OD=100.0, W=5.0, OPENING=8.0, LEAD=20.0, S=2.0, NT=2,
                 TOP_ME="9", BTM_ME="8", port_order=["P1", "N1"],
                 ground_fixture=fx, process=ctx)
cell.name = "cand"
p.write_gds(cell, tmp_path / "cand.gds")
```
这条路径跳过了：pydantic 校验、`_write_geometry_outputs` 的两次审计
（`audit_via_landing`/`audit_port_lattice`）、manifest 落盘——**只做纯几何构造 +
写盘**，是仓库里事实上最快的"生成一个器件"方式，代价是完全绕开公开契约（`p` 是按
文件路径动态 import 出来的模块，`generator_plugin.py` 顶部注释称它为"实验/原型口"，
不建议新包对外暴露同款接口，但**测试内部**直接复用它完全没问题，`test_pcell_inductor_
python_port_clean.py` 全文都是这么做的）。

### 8.3 现场实测耗时（`EMOW/.venv/bin/python`，`demo_6m`/`n28_1p10m` profile；探测脚本临时
写在会话 scratch 目录，未改动仓库任何文件）

单次 `generator.generate()`（走完整公开契约：pydantic 校验 + `ind_sym` 构造 + 写盘 +
两次审计 + manifest）：

| 阶段 | 耗时（demo_6m，NT=2 场景） |
|---|---:|
| `import PLUGIN_GENERATORS`（尚未触发 `_clean_port()` 的惰性加载） | ~0.08 s |
| `config_model.model_validate(...)` | ~0 s（可忽略） |
| `generator.generate(...)`，冷启动（首次触发插件文件加载 + profile 解析） | ~0.39 s |
| `generator.generate(...)`，热启动（第 2、3 次调用，`sys.modules` 已缓存插件） | ~0.31 s |

`generate()` 内部耗时分解（`ind_sym`，`OD=120, W=3, S=2, NT=2, TOP_ME="6"`，
`demo_6m`）：

| 子步骤 | 耗时 |
|---|---:|
| `get_process_rule_profile("demo_6m")`（冷/热一样，无缓存） | ~0.007 s |
| `get_geometry_rule_adapter("demo_6m")` | ~0.007 s |
| `p.ind_sym(...)` 纯构造 | **~0.277 s** |
| `p.write_gds(...)` | ~0.001 s |
| `audit_via_landing(...)`（重新读一遍 GDS） | ~0.008 s |
| `audit_port_lattice(...)`（重新读一遍 GDS） | ~0.007 s |
| `drc_audit.audit_gds(...)`（第三次独立读取，模拟 `drc_check=True` 场景） | ~0.008-0.009 s |

**耗时的大头不是 I/O，也不是 DRC 审计，是构造本身**——而且构造耗时**强烈依赖匝数**，
不是均匀的（这是第 10 节要单独展开的一个"意外"发现）：同样的 `OD/W/S`，
`NT=1`：~0.001s，`NT=2`：**~0.28-0.31s**，`NT=3`：~0.003s，`NT=4`：~0.004s，
`NT=5`：~0.005s（`n28_1p10m` 下复测同样是 `NT=1`≈0.001s/`NT=2`≈0.31s/`NT=3`≈0.004s，
排除了 profile 特异性）。原因见第 10 节。

**给新包的实操建议**：如果要写一个"验证 pcell Stage 接线是否正常工作"的最小单元测试/
CI smoke test，**避免用 `turns=2`/`multi_turns=2` 做默认样例**——它是六个家族里目前
已知最慢的单点构造路径（比同族其他匝数慢 60-300 倍），拿它当"随手写一个最小例子"的
默认选择会让 CI 时间产生误导性的方差。`turns=1` 或 `turns=3` 及以上都是毫秒级。

---

## 9. 公开 API 复用矩阵

| 分类 | 具体符号（绝对路径:行号） | 备注 |
|---|---|---|
| **按原样复用** | `em_ic_opt_workflow.geometry.registry.get_generator`（`EMOW/src/em_ic_opt_workflow/geometry/registry.py:118-143`） | 唯一稳定入口；必须显式传 `plugin_module` |
| | `em_ic_opt_workflow.geometry.registry.resolve_plugin_module_path`（同文件 33-49） | `"builtin:xxx"` 解析规则 |
| | `em_ic_opt_workflow.geometry.base.PassiveDeviceGenerator` / `GeometryGenerationResult`（`EMOW/src/em_ic_opt_workflow/geometry/base.py:10-32`） | 抽象契约 + 返回类型；新包若保留同名概念可直接照抄这两个类型定义 |
| | `em_ic_opt_workflow.devices.clean_port.generator_plugin.PLUGIN_GENERATORS` 与六个 `CleanPortXxxConfig`（`EMOW/src/em_ic_opt_workflow/devices/clean_port/generator_plugin.py:230-692, 1164-1171`） | 六个家族的完整 pydantic 校验规则（数值范围/金属层禁区/CT 层级/port_order 合同），价值都在这些 validator 里，不建议重写 |
| | `em_ic_opt_workflow.geometry.process_rules.get_process_rule_profile` / `ProcessRuleProfile`（`EMOW/src/em_ic_opt_workflow/geometry/process_rules.py:227-351`） | rule.yaml schema 定义 + 加载 + 交叉一致性校验 |
| | `em_ic_opt_workflow.geometry.rule_adapter.GeometryRuleAdapter` / `get_geometry_rule_adapter`（`EMOW/src/em_ic_opt_workflow/geometry/rule_adapter.py:66-245`） | pcell 消费 profile 的唯一适配层 |
| | `em_ic_opt_workflow.geometry.drc_audit.audit_gds` / `require_layers_from_config` / `product_scope_record`（`EMOW/src/em_ic_opt_workflow/geometry/drc_audit.py:286-618`） | 独立、无副作用的规则级 DRC，`geometry/__init__.py` 已做懒加载避免强制拉 klayout |
| | `em_ic_opt_workflow.geometry.gds_compare.compare_gds`（`EMOW/src/em_ic_opt_workflow/geometry/gds_compare.py:93-94`） | "物理等价"判定的权威实现，做几何回归测试/未来重构验证时直接用 |
| | `em_ic_opt_workflow.path_safety.validate_output_file_name`（`EMOW/src/em_ic_opt_workflow/path_safety.py:6-12`） | 5 行工具函数，输出文件名防路径穿越 |
| **内部实现，不要绕过公开层直接依赖** | `devices/clean_port/_pcell_core.py`、`_pcell_primitives.py`、`_pcell_ind_sym.py`、`_pcell_xfm_*.py`、`_pcell_guards.py` 的全部符号（下划线前缀模块） | 六个 family 函数（`ind_sym`/`xfm_bs`/`xfm_ms`/`xfm_balun`/`xfm_tw`/`xfm_il`）签名稳定、文档完整，**测试内部**直接调用没问题（第 8 节），但作为长期对外契约应通过 `PassiveDeviceGenerator.generate()`，不要让新包的生产代码 import 这些下划线模块 |
| | `pcell_inductor_port_clean.py` 这个 facade 本身 | 按文件路径动态加载、进程级单例缓存，模块本身的 docstring 明确它是"reference/prototype port"（尽管 README 又说"already the in-package product implementation"——两处表述不完全一致，见第 10 节），当作黑盒插件文件对待，不要 `import` 成 Python 包 |
| | `generator_plugin.py` 里的下划线函数（`_clean_port`、`_build_fixture`、`_auto_stub_widths`、`_forbid_m1*`、`_metal_stack_index_or_none`） | 生成器包装内部细节 |
| | `audit_via_landing`/`audit_port_lattice`（`generator_plugin.py:769-919`，无下划线但只在 `_write_geometry_outputs` 内部调用） | 概念上可复用（结构性复核很有价值），但当前是私有实现细节，没有独立的稳定签名保证；如需要，建议新包重新包一层而不是直接 import |
| **该丢弃/重写的 orchestration** | `em_candidate_preparation._merged_geometry_parameters` / `_merged_device_geometry_parameters` / `_split_multi_device_parameters` / `_inject_drc_check`（`EMOW/src/em_ic_opt_workflow/em_candidate_preparation.py:289-348, 394-436`） | 和 `ContractBundle`/`schemas.py` 整体 YAML 合同强耦合，`Point.params: dict[str,str]` 的新形状不兼容；"按前缀拆分 + fixed_parameters 合并 + 冲突报错"的**模式**保留，代码重写 |
| | `prepare_em_candidate` / `prepare_em_candidate_from_contract` / `prepare_em_devices_from_contract`（同文件 196-286, 439-517） | 把 pcell 生成、DRC 门禁、EMX 参数准备（`EmxPreparationOptions`/`EmxRunConfig`/`build_emx_argv`）糅在一个函数里；新架构下这些职责应该拆成 `pcell` Stage + 一个校验 Stage/gate + `emx` Stage 三段（`DESIGN_CN.md` 191 行的流水线定义） |
| | `em_cache.py` / `em_artifacts.py` 整套 `EmCacheKey`/`EmCacheStore`/`_produce_em_candidate_artifact`（`EMOW/src/em_ic_opt_workflow/em_cache.py`、`em_artifacts.py:193-`） | fingerprint **payload 设计思路**值得借鉴（第 6 节），但这是"生成之后才能算"的 post-hoc 缓存键，不满足新 `Stage.fingerprint(inp)` 必须在 `run()` 之前算出来的要求；`EmCacheStore` 的目录/staging/rename 落盘手法（`em_cache.py:61-80`，先写临时目录再原子 rename，防止并发写入撞车）这个具体技巧可以复用，但整体结构要按新 `Stage` 协议重写 |
| | `device_db/ingest.py` 的 `CURRENT_GEOM_VERSION` 人工计数器机制 | 意图（把"构造代码版本"纳入身份）值得保留，实现（手动维护一个全局 int + 67 行变更日志注释）不建议照抄——第 6.5 节建议用插件文件内容哈希自动化替代 |

---

## 10. 脆弱点与意外发现

以下每一条都经过本报告现场验证（读码 + 实测），不是单纯推测。

1. **`GeometryGeneratorConfig.top_cell` 字段是死字段，且有一个当前失败的测试正好在
   断言"它不是死字段"**——1.5/2.1 节已确认顶层 GDS cell 名恒等于 `gds_name` 的 stem，
   与契约里的 `top_cell` 无关。本报告现场跑
   `EMOW/.venv/bin/python -m pytest tests/test_geometry_registry_plugin.py -q` 得到
   `1 failed, 35 passed in 1.83s`，失败用例是
   `test_prepare_em_devices_from_contract_via_plugin`
   （`EMOW/tests/test_geometry_registry_plugin.py:502-591`），断言在第 588 行：
   ```
   assert prepared.geometry.top_cell == "ind_candidate"
   AssertionError: assert 'ind' == 'ind_candidate'
   ```
   （配置里 `"top_cell": "ind_candidate"`，第 525 行；实际 `gds_name=f"{device.id}.gds"`
   = `"ind.gds"`，stem 是 `"ind"`。）这是这条分支唯一的失败点（其余 35 个用例都过），
   说明这是一个**孤立的、大概率是"顶层 cell 强制等于文件名"规则后来加严之后没有回头
   更新的过时断言**，不是环境问题。**新包的教训**：不要在契约里保留一个"表面上像是
   会生效、实际会被静默忽略"的字段——如果新 `pcell` Stage 的输出契约里也有类似
   "顶层 cell 名"的配置项，要么让它真正生效，要么干脆不暴露这个字段，避免同样的
   死字段陷阱。
2. **"port manifest"today 只有降维版本**——第 1.4 节已详述：`cell.emx_ports`（含
   `point_nm`/`lead_zone_nm`/`metal_index`/`label_layer` 的结构化端口列表）只在内存
   里存在一次，`_write_geometry_outputs` 只把它压成 `-p name=signal:ref` 文本行落盘，
   `GeometryGenerationResult` 也不携带它。如果不特意去改 `_write_geometry_outputs`
   （或者去读只有 demo 工具才产出的 `.coordinates.json`），新 Stage 拿不到端口坐标。
3. **两匝（`NT=2`/`multi_turns=2`）构造路径比其它匝数慢 60-300 倍，根因是内部做了一次
   隐藏的组合搜索**——第 8.3 节实测数据；根因定位（本报告用 `cProfile` 现场跑出来，
   命中 `_pcell_ind_sym.py:902` 的 `_compact_two_turn_lane_offsets` 双重循环，逐个候选
   调 `_compact_two_turn_candidate`（`_pcell_ind_sym.py:677-`，真的构造一份 klayout
   `Cell`）+ `_compact_two_turn_candidate_is_qualified`（`_pcell_ind_sym.py:858-900`，
   调 `_ms_layer_regions`（`_pcell_core.py:1203-1219`）做 `Region.merged()`/
   `width_check()`/`space_check()`），5 次调用总共触发了 4070 次候选评估、9135 次
   `Region.merged()`）。这是"两匝紧凑绕组"这一特殊分支自带的、注释里自己承认的设计
   （`_compact_two_turn_lane_offsets` 第 922-924 行注释："cheap enough for query-library
   sweeps"——对批量库构建而言"够便宜"，但绝对值是同族其它匝数的两个数量级）。
   `xfm_ms(multi_turns=2)` 和 `xfm_balun`（`secondary_turns>=2` 分支，README 299 行
   "NT≥2 uses ind_sym"）会通过复用 `ind_sym` 走到同一条路径。**这不是 bug，是已知
   的性能特征**，但如果新包按点数/家族做批处理调度或成本估算，需要把这条路径当特例
   对待，不能假设"pcell 生成对所有家族/匝数都是均匀的几毫秒"。
4. **`process_rule_context`/`get_geometry_rule_adapter`/`get_process_rule_profile`
   完全没有缓存**——第 4.2 节已述，每次 `generate()` 调用都会重新读一遍 YAML、重新跑
   一遍 pydantic 校验（demo_6m 约 7ms/次；真实 profile 因规则条目更多，预计更久，
   但本报告未测量真实数值以避免引用任何工艺相关的性能画像）。如果新包要在一次批量
   优化里对同一个 `process_profile` 反复生成成百上千个候选点，这是一个显而易见、
   而且 EMOW 自己至今没有做的优化点（加一层进程内缓存，keyed by `profile_id` 或者
   `(profile_id, resolved_path_mtime)`）。
5. **同一份刚写出的 GDS 在一次"生成+DRC"链路里被 klayout 独立重新解析三次**
   （5.2 节两次 + 5.3 节一次）——各自 `kdb.Layout(); ly.read(...)`，互不共享解析结果。
   单次几毫秒到十毫秒级，量级上不是当前性能瓶颈（第 3 点的两匝搜索开销比这个大一个
   数量级以上），但如果新包决定把结构性复核和规则级 DRC 合并进同一个 Stage，值得
   一次解析、多处复用。
6. **`generator_plugin.py` 顶部文档和 `README.md` 对这份代码的"成熟度"表述互相矛盾**：
   `pcell_inductor_port_clean.py` 模块 docstring
   （`EMOW/src/em_ic_opt_workflow/devices/clean_port/pcell_inductor_port_clean.py:28-30`）
   写"This experimental port must NOT be merged into the formal `src/` product code
   without an explicit licensing decision: the geometry construction is a derivative
   work of the GPL SKILL sources."；而同目录 `README.md`
   （`EMOW/src/em_ic_opt_workflow/devices/clean_port/README.md:330-333`）明确写
   "These modules are already the in-package product implementation; the old
   'prototype only / not merged into src' milestone description is superseded."
   ——**两处对同一份代码的定位描述不一致**（一处说"不能合并进正式产品代码"，另一处说
   "已经就是产品代码"），且这份代码确确实实已经在 `src/em_ic_opt_workflow/devices/
   clean_port/` 下、被 `generator_plugin.py` 作为生产入口调用。这不是本报告能替
   EMOW 解决的许可问题，但新包如果打算把这份代码（或其行为等价重写）纳入自己的
   `pcell` Stage，**许可证/来源审查应该独立确认一遍**，不要仅凭"README 说已经是产品
   代码"就认为问题已解决——过时的 docstring 本身就是一个信号，说明这个问题在 EMOW
   内部可能还没有被最终定论过。
7. **文档相对源码有版本滞后**：`docs/guide/07-device-inventory.md:37` 写
   "`CURRENT_GEOM_VERSION=5`"，源码 `device_db/ingest.py:67` 已经是
   `CURRENT_GEOM_VERSION = 6`（含第 51-64 行描述的 v6 变更：端口坐标与引线多边形统一
   计算，修正 xfm_bs/xfm_ms 的 M1 地环位置）。研究这类文档时不能只信文档字面数字。
8. **`ground_fixture` 是强制字段，没有"裸器件"模式**——六个家族的 config 都要求
   `ground_fixture: CleanPortGroundFixtureConfig`（非 `Optional`，见 3.0 节），也就是
   说**当前生产契约不支持"只要绕组本体、不要 M1 地环"的生成**。如果新包的某些用途
   （比如某些 EMX 建模场景想用无穷远地平面而非局部地环）需要这个选项，today 的公开
   API 不提供，需要额外设计（EMX 端口本身支持"无 reference = 边缘端口"，见
   `emx_port_lines`/`_pcell_core.py:1442-1455` 的 docstring，但 pcell 层的六个家族
   config 目前把 `ground_fixture` 焊死为必填，两者是不同层面的事）。
9. **`bottom_metal`（ind_sym）是"活的死参数"**：配置校验会检查它非 M1
   （`_CleanPortInductorConfigBase._metals_not_m1`，`generator_plugin.py:206-209`），
   看起来像一个正常生效的字段，但文档和代码注释反复强调"链路不使用它选择实际跨线层"
   （`06-device-variables.md:114`；`drc_audit.py:344-349` 注释"bottom_metal remains
   the chain's documented dead parameter — never echo it here"）。真正的跨线层由
   `_metal_below()`（`_pcell_core.py:182-192`）按 profile 实际邻层动态推导。这是第
   1 条"死字段"问题的另一个变体，但这次是**校验生效、行为不生效**，比 `top_cell`
   （校验也不做）更隐蔽——容易让人以为改 `bottom_metal` 真的能选层。

