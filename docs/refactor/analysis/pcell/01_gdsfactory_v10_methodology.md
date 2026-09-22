# gdsfactory v10.0.0rc0 参数化器件建模方法论研究

- 研究对象：`<gdsfactory-clone>`，`git describe` = `v10.0.0rc0`（commit `5dcaad4`），`.venv` 内 `gdsfactory.__version__ == "10.0.0rc0"`，Python 3.13。
- 对照对象（仅在必要处提及，不重复几何正确性结论）：`<repo>/src/ic_opt/em/pcell`。
- 范围声明：2026-09-21 已完成一次针对 v9.51.0 的**执行级**比较（`inductors.py`/`transformers.py`/`_geometry.py`/`spiral_inductor.py`/vias/microstrip 的几何是否正确、是否有工艺规则），本报告**不重复**该结论；本报告只回答"gdsfactory 作为参数化器件建模框架，提供了哪些我们手搓 klayout.db 脚本里没有的方法论/框架能力，值得抄哪些"。
- 验证方式：全部结论均给出 `文件路径:行号`；涉及运行时行为的一律用 `.venv/bin/python -c "..."` 实跑并在文中原样引用输出；涉及版本差异的以 `git log`/`git show --stat`/`git diff` 为准。

---

## 1. Component 模型：Component / 引用层级 / Port

### 1.1 Component 不是"包了一层"的自研类，而是 kfactory KCell 本身

`gdsfactory/component.py:188` `class ComponentBase(ProtoKCell[float, BaseKCell], ABC)`，`gdsfactory/component.py:680` `class Component(ComponentBase, kf.DKCell)`。`kf.DKCell` 是 kfactory 里以微米（double）为坐标单位的 KCell 类型。也就是说 `Component` 通过继承直接就是一个 kfactory 意义上的 cell，而不是"内部持有一个 kdb.Cell 再转发方法"的包装类。这是 v10 相对于旧版 gdsfactory（自己维护一套 Component/Polygon/CellArray 体系，klayout 只是后端）的根本性架构变化——**gdsfactory 现在是 kfactory 之上的一层"命名 + PDK + RF/光子学组件库"，几何内核完全交给 kfactory/klayout.db**。

佐证：
- `ComponentReference: TypeAlias = DInstance`（`gdsfactory/component.py:185`），即引用类型就是 kfactory 的 `kfactory.instance.DInstance`（实测 `gf.ComponentReference.__mro__` = `(DInstance, ProtoTInstance, ProtoInstance, UMGeometricObject, GeometricObject, ABC, Generic, object)`）。
- `Port`：`gdsfactory/port.py:37` `from kfactory import DPort as Port  # runtime re-export of a class` ——Port 干脆就是 kfactory 的 `DPort`，gdsfactory 不再自定义 Port 类。实测：`type(port) == <class 'kfactory.port.DPort'>`。
- `<<` 运算符：`gdsfactory/component.py:826-832` `__lshift__` 直接转发到 `add_ref`；`add_ref`（`component.py:834-878`）内部调用 kfactory 的 `create_inst`/`create_vinst`。

### 1.2 引用与层级：支持原生阵列引用（AREF），而不是逐个放置

`add_ref(component, name=None, columns=1, rows=1, column_pitch=0.0, row_pitch=0.0)`（`component.py:834-878`）在 `rows>1 or columns>1` 时调用 `self.create_inst(component, na=columns, nb=rows, a=DVector(column_pitch,0), b=DVector(0,row_pitch))`（`component.py:870-873`），生成的是**一个** GDS **AREF**（数组实例），不是 N×M 个独立实例。这一点在器件库里被大量复用：`gdsfactory/components/vias/via_stack.py:237-243` 和 `gdsfactory/components/analog/_geometry.py:82-115`（`_add_via_array`）都是"算出行列数 → 一次 `add_ref(..., columns=, rows=, column_pitch=, row_pitch=)`"，而不是双重 `for` 循环。

对比：`ic-opt-modular` 侧目前是手写双重循环 `for column in range(plan.columns): for row in range(plan.rows):`（`src/ic_opt/em/pcell/_pcell_core.py:584-585`）逐个放置过孔，via 数量大时会产生大量独立实例/多边形而不是一个 AREF。

### 1.3 Port 字段与 dbu 对齐（实测）

```
port: DPort(self.name='e1', self.width=2.0, trans=r0 *1 0.12345678,0, layer=WG (1/0), port_type=electrical)
center: (0.12345678, 0.0) width: 2.0 orientation: 0.0 layer: WG port_type: electrical
dbu: 0.001
```
（`.venv/bin/python` 实跑 `gf.gpdk.PDK.activate(); c.add_port(center=(0.12345678,0), width=2.0, layer=(1,0), port_type="electrical")`）

关键发现——**dbu 对齐是隐式、静默的，不是强校验**：
- `center`（`DPort.center`，浮点）保留用户传入的任意精度值，不报错也不四舍五入；只有转换到整数网格视图 `icenter` 时才吸附：`center=(0.1234567891, 0)` → `icenter=(123, 0)`（即吸附到最近的 1 nm）。
- `gdsfactory/port.py:63` 定义了 `class PortNotOnGridError(ValueError)`，但全仓库 `grep -rn "raise PortNotOnGridError"` **零命中**——这个异常类从未被抛出，是历史遗留的"看起来有校验、实际没有"的陷阱。
- 官方口径（`docs/cross_section_migration.md:26-28`，随 #4827 一起写的说明文档）："Each bound is snapped independently with `kcl.to_dbu()`. KLayout rounds halfway values away from zero: at 1 nm DBU, ±0.2505 µm becomes ±251 DBU."——即离网格恰好一半的坐标按"远离零"取整，且**每个 CrossSection 里的 strip 边界是独立吸附的**，不是先算好浮点差值再统一取整,因此对称 strip 在取整后可能变成非对称（同一文档也说明了这点，并把这种"取整后不再对称"的场景单独处理为 `AsymmetricCrossSection`）。
- `orientation` 不要求是 Manhattan 角：实测 `orientation=37.123` 可以正常创建端口（`angle` 属性——离散的 0/1/2/3 象限枚举——在非 Manhattan 角时返回 0，只是一个"最接近象限"的辅助字段，不做校验/报错）。

### 1.4 `add_port` / `add_ports_from_markers*`：两种建端口的范式

- 显式坐标：`Component.add_port(name, center, width, orientation, layer, port_type, cross_section=...)`（`component.py:230-363`），我们自己的 PCell 目前就是这种写法。
- 从几何标记推断：`gdsfactory/add_ports.py:249-303`（`add_ports_from_markers_square`，读取 `pin_layer` 上的方形 marker，中心即端口中心）与 `add_ports.py:306-…`（`add_ports_from_markers_center`，从 marker 相对包围盒的位置反推朝向/宽度，`port_name_prefix` 按 `port_type` 默认给 `"o"`/`"e"` 前缀）。这套机制把"在某层画一个小方块 = 声明一个端口"和"手写坐标声明端口"并列为一等公民,对"先出图、再从图反推端口"的画法（比如从一个已有 GDS 版图逆向抽端口）更友好。

### 1.5 电性端口与光学端口：不是两个类，是一个字符串标签 + 一套独立的"网表分组"机制

`port_type` 只是 `Port`/`DPort` 上的一个普通字符串字段（`add_port` 默认值在 `component.py:304-305` 落到 `"optical"`）。`select_ports_optical = partial(select_ports, port_type="optical")` / `select_ports_electrical = partial(select_ports, port_type="electrical")`（`gdsfactory/port.py:384-385`）说明"电性/光学"只是过滤谓词的差异，Port 类本身完全相同（宽度、层、朝向、cross_section 字段一模一样）。

真正体现"这组端口属于同一个电气 net/pin"的是另一条独立的机制：`kf.DKCell.create_pin(name, ports: Iterable[ProtoPort], pin_type='DC', info=None) -> DPin`（实测签名，kfactory 提供，gdsfactory 未重新包装）。器件库里大量出现 `c.create_pin(ports=[port], name=port.name)`（如 `gdsfactory/components/waveguides/wire.py:74-76`、`gdsfactory/components/pads/pad_gsg.py:89-91`），用来把物理端口注册为一个具名 pin，供 schematic/LVS 使用；`Component.dup()`（`component.py:696-721`）里专门处理了"pin 的端口引用在复制后要重新映射"（因为 pin 存的是 `BasePort` 对象引用而不是名字）。

### 1.6 get_netlist / pprint_ports / show / plot（实测输出）

`get_netlist(recursive=False, on_multi_connect="error", on_dangling_port="warn", ...)`（`component.py:590-655`）返回**place-aware**网表：`instances`（含 `settings`+`info`）、`placements`（x/y/rotation/mirror）、`ports`（父层暴露的端口）、`nets`（`{p1,p2}` 端口对列表，纯几何重叠推断,不依赖用户显式声明连接）。实测两段 `metal_routing` 直线首尾相接：

```
"nets": [ { "p1": "straight,e2", "p2": "straight2,e1" } ]
```
并在未连接的两端打印 `UserWarning: Unconnected ports: ['straight,e1', 'straight2,e2']`（`on_dangling_port` 默认 `"warn"`）。

`pprint_ports(**kwargs)`（`component.py:535-556`，实现在 `gdsfactory/port.py:89-112`）用 `rich.table.Table` 打印 `name/width/orientation/layer/center/port_type` 六列；注意它只打印**当前这一层**显式 `add_port` 过的端口——顶层容器如果没有自己 `add_port`，即使子引用有端口，`pprint_ports()` 也打印空表（实测验证）。端口不会自动从子实例"冒泡"到父组件。

`show(lyrdb=None, l2n=None, markers=None, ...)` 与 `plot(lyrdb=None, display_type=None, ...)`（`component.py:1366-1395`，签名来自 kfactory）都能接收一个 KLayout `ReportDatabase`（`lyrdb`，即 DRC 报告）或 `markers`（形状+颜色标注列表）直接叠加显示——即"把 DRC/自定义标注结果和版图一起可视化"是框架自带能力,不需要另外拼图。`plot_netlist`/`plot_netlist_graphviz`（`component.py:1485-1586`）用 `graphviz.Digraph` 画连接关系图。

---

## 2. CrossSection 与 Path

### 2.1 #4827 之后，CrossSection/Section 的真身就是 kfactory 类型

`gdsfactory/cross_section/base.py:9-30`：
```python
from kfactory import DAsymmetricCrossSection as AsymmetricCrossSection
from kfactory import DCrossSection as SymmetricCrossSection
from kfactory import DCrossSectionLayer
CrossSection: TypeAlias = SymmetricCrossSection | AsymmetricCrossSection
type SectionSpec = (
    DCrossSectionLayer | tuple[typings.LayerSpec | kf.kdb.LayerInfo, float, float]
)
```
包文档字符串直接写明（`gdsfactory/cross_section/__init__.py:1-8`）："Factories return kfactory DCrossSection or DAsymmetricCrossSection objects." 旧版 gdsfactory 自己的 `CrossSection`/`Section` pydantic 模型已经**整体删除**，`Section` 现在对应 kfactory 的 `CrossSectionLayer`/`DCrossSectionLayer`。官方迁移说明（`docs/cross_section_migration.md:3-8`）强调："Their profiles travel with ports through GDS and OAS metadata; no separate gdsfactory profile registry is needed to recover them."——即 cross-section 信息现在**内嵌进端口的 GDS/OASIS 元数据**里,读回 GDS 不再需要 gdsfactory 侧的注册表来恢复。

这一点对我们有直接参考价值：如果我们的端口元数据（宽度/层/net 名）只活在 Python 对象里、GDS 里只有裸多边形，那么任何脱离当前 Python 环境读取 GDS 的下游工具（比如另写的 S 参数后处理脚本）都拿不到这些信息,只能靠约定对齐——这正是记忆里"三套端口词汇""sNp 序陷阱"这类问题的根源之一。kfactory 把这类信息做成**随文件走的自描述元数据**是一个值得借鉴的方向（见第 8 节表格）。

### 2.2 `cross_section()` 工厂与 `xsection` 装饰器

`gdsfactory/cross_section/utils.py:62-181`：`cross_section(width, offset, layer, sections, bbox_layers, bbox_offsets, cladding_layers, cladding_offsets, cladding_centers, radius, radius_min, name, kcl)` 把"主 strip + 若干辅助 strip（比如慢变掺杂/包层/GSG 地线）"编译成一个 kfactory `AsymmetricalCrossSection`，再检测是否轴对称、能整理成更省内存的 `SymmetricalCrossSection`（`utils.py:155-181`）。`xsection` 装饰器（`utils.py:28-59`）让"用默认参数调用时,结果按函数名注册进全局 `cross_sections` 表"——即 cross-section 工厂函数天然具备命名规范化,不需要手工维护名字字符串。

### 2.3 预置 cross-section：`metal1/2/3`、`gs`、`gsg`

`gdsfactory/cross_section/presets.py:420-468`：`metal1/2/3(width=10, layer="M1/2/3", radius=None)`，`radius = radius or width`（用线宽当默认弯曲半径的经验规则）。`gs`/`gsg`（`presets.py:471-533`）用**多 strip 复合 cross-section** 描述 Ground-Signal(-Ground) 探针结构：`gsg(trace_width=140, layer="M3", gap=100, radius=None)` 内部构造三条 `(layer, lo, hi)` strip（中心信号线 + 两侧地线），`radius` 默认 `3*width+2*gap`。`metal_routing = metal3`（`presets.py:536`）是一个别名。

### 2.4 Path：`straight`/`arc`/`euler`/`smooth`/`spiral_archimedean`

`class Path(UMGeometricObject)`（`gdsfactory/path.py:94-…`）内部只是一个 `points: ndarray[N,2]` + `start_angle`/`end_angle`，支持 `+`/`+=` 拼接（`path.py:167-174`）。生成器：
- `straight(length, npoints)`（`path.py:2023-2041`）
- `arc(radius, angle, npoints=None, start_angle=-90, angular_step=None)`（`path.py:1530-1588`），`npoints` 默认按 `PDK.bend_points_distance` 换算,也可以用 `angular_step` 直接指定角步长。
- `euler(...)`（`path.py:1635-1812`）：Euler/clothoid 弯曲，用 Fresnel 积分（`path.py:1595-1634`，可选 scipy 或纯 numpy 近似）。
- `smooth(points, radius=4.0, bend=euler, **kwargs)`（`path.py:2102-…`）：给一串折线路径点自动在拐角处插入 `bend`（默认 euler）过渡。
- **`spiral_archimedean(min_bend_radius, separation, number_of_loops, npoints)`**（`path.py:2044-2069`）：一个**通用阿基米德螺旋 Path 生成器**，`theta=linspace(0, N*2π, npoints)`，`r = separation/π*theta + min_bend_radius`。这不是"电感"组件,只是几何原语,但数学上和我们八边形电感的"半径随圈数单调收缩"骨架是一回事，只是它是圆形而不是八边形（八边形需要在此基础上按 `sides` 分段替换为直线段,gdsfactory 没有现成的"多边形螺旋 Path"生成器）。

### 2.5 `extrude`/`transition`：分段宽度/偏移函数、按需抑制端口

`gdsfactory/path.py:1087-1225`（模块级 `extrude(p, cross_section, layer, width, simplify, all_angle, add_bbox, ports, width_function, offset_function, insets, hidden)`）逐个 section 计算：`w = widths[i](t) if i in widths else section.width`（`path.py:1145`），`offset = offsets[i](t) if i in offsets else ...`（`path.py:1146-1150`），`t` 是沿路径弧长归一化坐标。也就是说**每个 strip 的宽度/偏移都可以是一个 `f(t)` 函数**，一次 `extrude()` 调用就能生成"渐变线宽的螺旋走线"（比如 Q 值优化常用的变宽电感）而不需要手工分段拼多边形。`ports={0: ("e1","e2","electrical")}` 按 section 序号选择要不要在该 section 端点出端口（`path.py:1130`, `_section_ports`）。

`transition(cross_section1, cross_section2, width_type="sine", offset_type="sine")`（`path.py:839-881`）在两个 profile 有公共层时生成一个 `Transition` 对象，交给 `extrude_transition()` 处理不同宽度/偏移之间的渐变（sine/linear/parabolic 或自定义 Callable）。

### 2.6 radius/bbox 处理

`radius`/`radius_min` 是 profile（CrossSection）上的一次性元数据："a profile registered without them cannot acquire them later"（`docs/cross_section_migration.md:141`）。`validate_radius(xs, radius, error_type=None)`（`cross_section/utils.py:251-262`）按 `CONF.bend_radius_error_type` 决定半径小于 `radius_min` 时是报错还是警告。`add_bbox`：`xs.add_bbox(component, ref=...)`（kfactory 提供，`cross_section_migration.md:101-131` 有详细行为说明）能对"主层/某个引用/某个 dbox"分别加不同 padding，且区分"真实整数网格 cell"与"待实例化的虚拟 cell（`insert_vinsts()` 前）"两种场景,避免 bbox 因为浮点/整数坐标转换而漂移。

### 2.7 RF/金属路由：`route_single_electrical`/`route_bundle_electrical`/`wire_corner`/`via_corner`

`gdsfactory/routing/route_single.py:351-389`（`route_single_electrical`）与 `route_bundle.py:705`（`route_bundle_electrical = partial(route_bundle, router="electrical")`）**都不自己实现布线算法**，而是转发到 `kf.routing.electrical.route_bundle(...)`（`route_single.py:378`，`route_bundle.py:668`）——即金属布线的曼哈顿寻径本身也已经下沉进 kfactory。

`gdsfactory/components/waveguides/wire.py:22-77`（`wire_corner`）是"同层 45°/90° 金属拐角"：只画一个正方形 + 两个正交端口,`x.add_bbox(c)` 补一圈 keepout,`create_pin` 注册电性 pin。`gdsfactory/components/vias/via_corner.py`（全文件）是"跨层拐角"：按 `cross_section` 序列取每层的 `(layer, orientation-pair)`,用 `gf.c.compass(...)` 画每层的矩形,再用被路由方的 via 组件的 `info["xsize"/"ysize"/"enclosure"/"column_pitch"/"row_pitch"]` 反推能塞下几列几行 via,一次 `c.add_ref(via, columns=, rows=, column_pitch=, row_pitch=)` 摆完（`via_corner.py:96-113`）。

---

## 3. Layers & Process / Pdk

### 3.1 LayerMap：从 klayout `.lyp` 直接生成

`gdsfactory/technology/layer_map.py:7-10`：`class LayerMap(gf.LayerEnum): layout = gf.constant(gf.kcl.layout)`——一个 PDK 只需继承这个基类,用类属性声明 `NAME: Layer = (gds_layer, datatype)`。`lyp_to_dataclass(lyp_filepath, ...)`（`layer_map.py:13-57`）能直接读 KLayout 的层属性文件（`.lyp`）自动生成这样一个 `LayerMap` 的 Python 源码文件——即层表可以由 KLayout 工艺文件反向生成,而不是手工誊抄维护。

### 3.2 LayerLevel 字段（3D/工艺层栈的最小可用元数据集）

`gdsfactory/technology/layer_stack.py:325-396`（`class LayerLevel(BaseModel)`）字段：`layer`（`LogicalLayer`/`DerivedLayer`，支持 `&`/`|`/`^`/`-` 布尔层运算，见 `layer_stack.py:38-104`）、`thickness`/`thickness_variation`、`zmin`、`sidewall_angle`/`sidewall_angle_variation`、`width_to_z`、`z_to_bias`（"z 高度 → 收缩/扩张量"的分段表）、`mesh_order`（重叠区域谁优先渲染）、`material`（字符串,给 3D/网格工具用）、`background`/`background_exclude_layers`（"在整个 bbox 范围铺一层背景材质,再挖掉某些源层"，用来表达衬底/包层）、`info: dict[str, Any]`（自由扩展字段）。已弃用字段（`thickness_tolerance`/`zmin_tolerance`/`sidewall_angle_tolerance`/`width_tolerance`，`layer_stack.py:366-384`）会在赋值时触发 `DeprecationWarning`，明确要求"公差改用 `*_variation`（一个 `Variation` 分布对象）表达,不要用一个孤立的容差数字"——即"工艺容差"是一等公民但被单独建模,不和标称值混在一个字段里。

### 3.3 LayerStack 到 3D/KLayout 脚本

`LayerStack.get_klayout_3d_script(layer_views=None, dbu=0.001)`（`layer_stack.py:566-…`）遍历所有 `LayerLevel`,把 `DerivedLayer` 展开成 KLayout DRC 脚本语法的 `input(l,d)` 与布尔表达式字符串,再拼出每层的 `z(name, zstart, zstop, ...)` 语句——可以直接贴进 `tech.lyt` 得到 KLayout 自带的 2.5D 剖面预览,不需要额外工具。`gdsfactory/export/to_3d.py:14-133`（`to_3d(component, layer_views, layer_stack, exclude_layers)`）用 `layer_stack.get_component_with_derived_layers()` 展开派生层,再用 `trimesh.creation.extrude_polygon` 按每层 `thickness`/`zmin` 挤出多边形,拼成一个 `trimesh.Scene`（纯可视化,不是 EM/FEM 网格）。

### 3.4 Pdk 类字段

`gdsfactory/pdk.py:114-192`（`class Pdk(BaseModel)`）核心字段：`layers`（`LayerEnum` 类）、`layer_stack`、`layer_views`、`cross_sections`/`cells`/`containers`/`models`/`symbols`（全是 `name -> factory` 字典）、`port_cross_sections`（按物理层号索引的端口 profile 工厂,`dict[tuple[int,int], CrossSectionFactory]`）、`layer_port_types`/`auxiliary_port_types`（"这个物理层默认的端口类型"）、`base_pdks: list[Pdk]`（**PDK 可以继承/组合**,`model_post_init`——`pdk.py:222-250`——按 `base_pdks` 顺序合并 `cross_sections`/`cells`/`containers`/三个端口约定字典,自身定义优先级最高）、`dbu`（每个 PDK 可以有自己的数据库单位,默认 `1nm`，`activate()` 时写入 `kf.kcl`）。`activate(force=False)`（`pdk.py:270-278`）只是把自己设成模块级全局 `_ACTIVE_PDK`。`register_cells`/`register_cross_sections`/`register_cells_yaml`（`pdk.py:280-…`）允许运行时增量注册,重名会 `warnings.warn`（不是静默覆盖也不是报错）。

### 3.5 generic PDK（`gf.gpdk`）：本质是光子学工艺栈，不是"RF PDK"

`gdsfactory/gpdk/layer_stack.py` 全文件：`LayerStackParameters` 给出的是硅光工艺典型值（`thickness_wg=220nm`、`thickness_clad=3.0um`、`thickness_nitride=350nm`……），金属栈只有三层 `M1/M2/M3`（`zmin_metal1=1.1um, thickness_metal1=700nm`；`zmin_metal2=2.3um, thickness_metal2=700nm`；`zmin_metal3=3.2um, thickness_metal3=2000nm`，均 `material="Aluminum"`），加 `VIAC/VIA1/VIA2`。这套 M1-M3 是给"加热器走线/引出焊盘"用的,**没有射频专用要素**（没有厚顶层金属、没有 MIM 电容层、没有衬底电阻率/介损参数,`material` 只是给 3D 渲染选色和给 `gplugins` 的 FDTD/模式求解器用的材料名字符串,不是 S 参数级别的电磁参数）。`get_process()`（`gpdk/layer_stack.py:275-408`）描述的是刻蚀/注入/退火等硅光工艺步骤,同样与 RF 无关。

结论：**gdsfactory 仓库内没有随包提供一个"真正的 RF PDK"**（真实厚金属+衬底损耗+MIM 电容的工艺栈）；RF 无源器件（`inductors.py`/`transformers.py`等）都是直接画在通用光子学 PDK 的 M1-M3 之上,层栈本身对 RF 建模没有任何针对性增强。`gdsfactory/components/analog/transformers.py:596-712`（`stacked_transformer`/`get_extended_layer_stack`）里甚至专门写了一段警告注释,说明默认用的第 4 层金属 `M4_LAYER=(53,0)`/`VIA3_LAYER=(48,0)` 是**在 `gpdk` 层栈之外临时注册的"合成层"**,如果要接 EM 仿真必须调用 `get_extended_layer_stack()` 而不能用 PDK 默认的 `get_layer_stack()`,否则网格工具根本不知道 M4 的 z 坐标（`transformers.py:598-611` 原文引用："*** SIMULATION PIPELINE WARNING *** ... sim.set_stack(...) will NOT know M4 has any zmin/thickness/material ... You MUST instead call: sim.set_stack(stack=get_extended_layer_stack(), ...)"）。经检索,`Palace`/`gsim`/`set_stack` 仅出现在这一处注释里（`grep -rln "Palace|gsim|set_stack" gdsfactory --include=*.py` 只命中 `transformers.py`），即这是**面向一个不在本仓库内的下游仿真流水线的接口约定**，gdsfactory 自己并不提供从 `LayerStack` 到电磁网格/S 参数的转换（这部分能力属于可选依赖 `gplugins[devsim,femwell,gmsh,meow,sax,schematic,tidy3d]`，`pyproject.toml:87`，本身不在此仓库,故不展开）。

---

## 4. `@gf.cell` 装饰器与配套设施

### 4.1 `gf.cell`/`gf.vcell`/`gf.cell_with_module_name` 是 kfactory 装饰器的薄封装

`gdsfactory/_cell.py:94-175`：`cell(...)` 把 `set_settings/set_name/check_ports/check_instances/snap_ports/add_port_layers/cache/basename/drop_params/register_factory/overwrite_existing/layout_cache/info/post_process/debug_names/tags/schematic_function` 等参数原样转发给 `kfactory.cell`（`_cell.py:9` `from kfactory import cell as _cell`；`_cell.py:154` `c: Any = _cell(_func, **cell_kwargs)`），只是把 `output_type` 锁定成 `gdsfactory.Component`,再打一个 `c.is_gf_cell = True` 标记（供 `get_factories.is_cell` 识别，见 4.5）。`vcell`（`_cell.py:209-269`）是 `ComponentAllAngle`（非网格对齐、延迟到 `insert_vinsts()` 才落到整数网格的"虚拟实例"变体,`component.py:1656`）的对应版本。`cell_with_module_name`（`_cell.py:326-335`）等价于 `cell(with_module_name=True)`，用模块路径参与命名以避免不同模块下同名函数冲突。**命名/缓存/序列化逻辑本身已经不在 gdsfactory 里维护**，全部委托给 kfactory。

### 4.2 命名/缓存/settings/info（实测）

```python
c1 = gf.components.straight(length=10)   # name: straight_gdsfactorypcomponentspwaveguidespstraight_L10__f2286423
c2 = gf.components.straight(length=10)   # c1 is c2 == True   (参数相同 → 命中缓存,同一对象)
c3 = gf.components.straight(length=20)   # name: ..._L20__31a8c163
c1.settings.model_dump() == {'length': 10, 'npoints': 2, 'cross_section': 'strip', 'width': None}
c1.info == {'length': 10, 'width': 0.5, 'route_info_type': 'strip', 'route_info_length': 10, ...}
```
`straight` 用 `@gf.cell_with_module_name(...)`（`gdsfactory/components/waveguides/straight.py:16`），因此自动命名里嵌入了模块路径（`gdsfactorypcomponentspwaveguidespstraight`，`.`被替换为`p`）+ 参数摘要（`_L10_`）+ 内容哈希后缀（`_f2286423`）。这个名字**明显超过常见的 32 字符 GDS cell-name 限制惯例**，靠 `CONF.max_cellname_length = 64`（`gdsfactory/config.py:118`）和 `write_gds()` 里 `name[: CONF.max_cellname_length]`（`component.py:507`）截断兜底,`deduplicate_cell_names=True`（`write_gds` 默认参数,`component.py:481,492-493`）在截断后仍然撞名时追加 `$1`/`$2` 后缀去重。这是"自动命名换来零手工维护成本"必须付出的代价——名字很长很丑,只能靠截断+哈希去重,不能反解出参数。

`settings`（pydantic 模型,仅函数签名参数）与 `info`（普通 dict,函数体内自由写入,比如上面 `route_info_*`/`resistance`/`inductance`/`model` 等）是两条独立通道：前者用于缓存 key / 网表序列化,后者用于"计算结果、供下游查询的衍生量"。我们自己的 `c.info["resistance"]`/`c.info["inductance"]`（`gdsfactory/components/analog/inductors.py:138-144` 等处）用法与此完全一致,是同一约定的复用。

### 4.3 `copy`/`flatten`

`Component.copy()`（`component.py:365-367`）就是 `self.dup()`；`Component.dup()`（`component.py:696-721`）重写了 kfactory 的 `dup()`，专门修复"pin 的端口列表在复制后引用还指向旧对象"的问题（保存/清空/重建 `self._base.pins`，用 `id(old_port) -> index` 建立映射后按新端口重新组装 `BasePin`）——这是一个**在真实使用中发现并修的 bug**，提示"深拷贝一个带 pin/net 元数据的 cell"这件事本身有陷阱,值得我们在自己的 `copy`/克隆逻辑里留意（如果我们的 PCell 有类似"命名 net 分组"的元数据）。`flatten(merge=True)` 直接继承自 `kf.DKCell`（未在 gdsfactory 侧重写），调用点如 `component.py:1323`（`offset()`内部）、`gdsfactory/components/analog/transformers.py:1243,1367`（`_secondary_inductor`/`transformer_concentric` 组装完成后拍平)。

### 4.4 YAML 声明式电路组装（`from_yaml`）

`gdsfactory/read/from_yaml.py:1-48`（模块 docstring 即完整语法示例）：顶层 `instances`（每个实例的 `component`+`settings`，支持 `${settings.xxx}` 模板变量引用父层参数）、`placements`（`x: mzi,cc` 这种"相对某实例某锚点"的位置表达式）、`ports`（把子实例端口提升为本组件端口）、`routes`（如 `routes.electrical.links` 声明一组端口对,配合 `settings: {layer, width, radius}` 自动布线）。配合 `Pdk.register_cells_yaml(dirpath)`（`pdk.py:305-…`）可以把一个目录下的 `*.pic.yml` 文件整体注册成可调用 cell,和 Python 定义的 cell 同等对待。`gdsfactory/watch.py:1-8` 还提供一个基于 `watchdog` 的文件监视器,检测到 `.pic.yml` 变化后用 `exec()` 动态重跑并推送到运行中的 KLayout（文档里专门给了一条安全警告："Only watch directories that contain trusted code"）。这套"YAML 描述拓扑 + Python 只写参数化叶子器件"的分层,是我们目前用纯 Python 脚本拼测试结构（pad+DUT+地环）时可以参考的一种更声明式、更容易 diff 审查的替代方案。

### 4.5 黄金回归测试：`difftest` + 全库自动发现测试

`gdsfactory/difftest.py`：
- `xor(old, new, ...)`（`difftest.py:49-…`）对两份布局按层做 `kdb.Region` 异或,返回逐层 `LayerDiff(layer, xor_area, ref_area, run_area, iou, bbox, polygon_count_ref, polygon_count_run, present_in)`——**用交并比(IoU)和面积而不是逐字节比较**来判断"变没变"，专门有 `ignore_sliver_differences`/`sliver_tolerance` 参数过滤掉浮点/dbu 取整造成的针尖状伪差异（噪声）。
- `difftest(component, test_name, dirpath, xor=True, ...)`（`difftest.py:538-614`）：先做整份文件的 `filecmp.cmp`（字节相同直接通过）,不同则跑分层 XOR diff,`show=True` 时把差异直接在 KLayout GUI 打开供人工判断,交互确认后可以 `overwrite()` 覆盖参考 GDS（`difftest.py:617-628`）。CLI 等价命令 `gf gds-diff --xor <ref> <run>`（`difftest.py:605`）。
- **规模化落地**：`tests/components/test_components.py:15` `cells = get_cells([gf.components])` 用 `gdsfactory/get_factories.py:15-129`（`get_cells`/`is_cell`）**反射式扫描** `gf.components` 模块下所有满足"被 `@gf.cell`/`@gf.vcell` 装饰,或返回值标注为 `Component`"条件的函数,自动组成一个 pytest 参数化夹具（`skip_test` 是唯一的人工排除名单，`test_components.py:35-49`）；每个 cell 自动获得两个测试：`test_gds`（默认参数实例化 + `difftest`，首次运行自动落地参考 GDS）与 `test_settings`（`pytest_regressions.data_regression` 对 `component.to_dict()` 做快照回归）。**这意味着只要一个新组件被 `@gf.cell` 装饰并出现在 `gf.components` 命名空间里，它就自动获得黄金 GDS 回归 + 参数快照回归测试，不需要再写一行测试代码**。

这套机制在这次 v10 迁移（见第 7 节）里被真实用来做验证："No GDS geometry goldens were regenerated ... Instance placements and netlist connectivity were compared before accepting those changes"（`docs/cross_section_migration.md:79-89`），并给出了一个具体量化案例：`ring_single(cross_section="rib")` 的 SLAB90 层 XOR 面积从"应为 0"变成 `3.43365 µm²`（对比基准面积 `1066.994205 µm² → 1067.644168 µm²`），团队判定这是"移除了旧版一个 0.05µm 的简化容差"导致的**可接受的行为变化**而非回归,并对另一个真正依赖旧行为的组件 (`ring_single_pn`) 显式传入等效参数使其重新逐字节匹配黄金件。

### 4.6 `write_gds` 确定性

`write_gds(gdspath, gdsdir, save_options, with_metadata=True, exclude_layers, no_empty_cells=False, deduplicate_cell_names=True)`（`component.py:473-533`）：`exclude_layers` 通过 `save_options.deselect_all_layers()` + 逐层 `add_layer()` 实现"白名单导出"；`with_metadata` 控制是否写 `write_context_info`（即 settings/ports 等 gdsfactory 专有元数据段）。`Component.write()`（`component.py:723-755`，重写自 kfactory）在写文件前统一调用 `insert_vinsts()`、`set_meta_data()`、`_fix_pin_metadata()`（`component.py:56-71`：把 pin 的端口索引从 int 转成 str，绕过 "kfactory 2.5.1 存的是 int、读的时候按 str 找键导致 KeyError" 的兼容性问题）。"确定性"主要体现在:相同参数 → 相同缓存对象 → 相同名字 → `difftest` 能可靠地逐字节短路；真正的多次导出稳定性（cell 顺序、坐标取整）依赖的是 kfactory/klayout.db 底层写文件的实现,gdsfactory 侧没有另外的"排序/哈希"逻辑。

---

## 5. 模拟 / RF 器件的接口设计

### 5.1 两个"同名不同物"的 `spiral_inductor` ——一个真实存在的框架级坑

这是本次研究中最值得记录的发现,直接关系到"框架如何组织一个上百器件的库"这一方法论问题。

- `gdsfactory/components/spirals/spiral_inductor.py:14-57`：面向**超导谐振器/量子比特读出**的方形螺旋电感,参数 `width/pitch/turns/outer_diameter/tail/layer`，实现只有 17 行——`gf.path.arc()` 拼接 `turns*2` 段半径递减的弧 + `gf.path.extrude()` 一次成型（第 2.4/2.5 节的 Path/extrude 范式的典型示例）。
- `gdsfactory/components/analog/inductors.py:148-337`：**另一个**同名函数 `spiral_inductor`，是真正的射频八边形螺旋电感（`d_out/N/sides/width/spacing/aspect_ratio/port_side/add_pgs/via/...`），带下线桥(underpass)、过孔阵列、图案化地屏蔽(PGS)。

两者 `__all__` 都声明了 `spiral_inductor`（`inductors.py:22`；`spirals/spiral_inductor.py` 隐式），但 `gdsfactory/components/analog/__init__.py:6-14` 的顶层 `__all__` **只列出了** `get_extended_layer_stack/inductor/interdigital_capacitor/interdigitated_electrodes/stacked_transformer/symmetric_transformer/via3`,**遗漏了 `spiral_inductor` 和 `symmetric_inductor`**（`transformers.py` 里的 `transformer_concentric` 同样未被 `transformers.py` 自己的 `__all__`——`transformers.py:23-28`——之外的任何地方重新导出）。`gdsfactory/components/__init__.py:23` `from .analog import *` 是**按源模块的 `__all__` 决定实际绑定哪些名字**,与 `components/__init__.py` 自己的 `__all__` 无关；随后 `components/__init__.py:41` `from .spirals import *` 才把 `spirals/spiral_inductor.py` 里的同名函数绑定上去。

实测（`.venv/bin/python`，已 `gf.gpdk.PDK.activate()`）：
```
gf.components.spiral_inductor.__module__      == 'gdsfactory.components.spirals.spiral_inductor'   # 量子比特版，不是 RF 版
hasattr(gf.components, 'symmetric_inductor')   == False
hasattr(gf.components, 'transformer_concentric') == False
gf.get_component('symmetric_inductor')          # ValueError: 'symmetric_inductor' not in PDK 'generic'. Did you mean ['symmetric_transformer']?
'symmetric_inductor' in get_cells([gf.components])   == False
```
后果：
1. `gf.components.spiral_inductor()` 这个"看起来最符合直觉"的调用,实际拿到的是与八边形 RF 电感完全无关的量子比特方形螺旋。
2. **真正的差分八边形电感 `symmetric_inductor`（带中心抽头、PGS，是 gdsfactory 自带组件里与我们 PCell 库定位最接近的一个）在公共 API 里完全不可达**，只能通过内部模块路径 `gdsfactory.components.analog.inductors.symmetric_inductor` 直接导入。
3. 因为第 4.5 节的自动化测试是对 `gf.components` 做反射扫描,`symmetric_inductor`/`transformer_concentric`/analog 版 `spiral_inductor` **三个组件在 gdsfactory 自己的 CI 里没有任何黄金 GDS 回归测试、没有 settings 快照测试**——一个 `__init__.py` 里少写三个字符串就足以让"自动化测试全覆盖"这个方法论优势对这三个组件完全失效。

对我们的启示：这恰恰印证了"靠人工维护的扁平 `__all__`/命名空间 star-import 惯例本身就是脆弱的"——如果我们也打算做一个"自动发现全部注册器件 → 自动生成回归测试"的机制（第 4.5 节值得抄的模式）,必须让"注册"和"能否被发现"用同一个单一事实来源（比如统一走一个显式 `registry.register(name, factory)` 调用,而不是靠模块 `__all__` 手工同步）,否则会重演这个问题。经检索,我们自己的 `src/ic_opt/em/pcell/registry.py` 已经是显式注册表模式,不是 star-import 模式,这一点结构上优于 gdsfactory 这里的写法。

### 5.2 参数接口全景（仅接口层面，不评述计算是否正确）

| 组件 | 文件 | 关键参数 | 端口 |
|---|---|---|---|
| `inductor`（2 匝差分单环） | `gdsfactory/components/analog/inductors.py:42-145` | `width, space, diameter, resistance, inductance, turns, layer_metal, layer_inductor, layer_metal_pin, layers_no_fill` | `P1,P2` |
| `spiral_inductor`（RF 八边形，模块内可达但公共 API 不可达，见 5.1） | `inductors.py:148-337` | `d_out, N, sides, width, spacing, aspect_ratio, port_side, add_pgs, pgs_diameter/width/spacing, via, resistance, inductance, layer_winding, layer_underpass, layers_pgs` | `P1(layer_winding), P2(layer_underpass)` |
| `symmetric_inductor`（差分八边形，公共 API 不可达） | `inductors.py:343-676` | 同上 + `center_tap, via_extent, port_spacing` | `P1,P2,[CT]` |
| `symmetric_transformer`（交叉缠绕变压器） | `gdsfactory/components/analog/transformers.py:35-593` | `d_out, N1, N2, sides, width, spacing, center_tap_primary/secondary, via_extent, port_spacing, via, add_pgs, ...` | `P1±,P2±,[CT1,CT2]` |
| `stacked_transformer`（叠层变压器） | `transformers.py:971-1163` | 同上 + `via_primary/secondary, layer_winding_primary/secondary, layer_crossing_primary/secondary` | `P±,S±,[CT_P,CT_S]` |
| `transformer_concentric`（同心 1:1，公共 API 不可达） | `transformers.py:1247-1369` | `width_primary/secondary, space, coupling_gap, diameter_outer, layer_primary/secondary/secondary_jumper, via_secondary, via_size` | `P1,P2,S1,S2` |
| `interdigital_capacitor` | `gdsfactory/components/analog/interdigital_capacitor.py:16-127` | `fingers, finger_length, finger_gap, thickness, layer` | `e1,e2` |
| `pad_gsg`（探针垫，声明式） | `gdsfactory/components/pads/pad_gsg.py:81-92` | `length, cross_section="gsg"` | 由 `gsg` cross-section 派生的多端口 |
| `pad_gsg_short/open` | `pad_gsg.py:19-77` | `size, layer_metal, metal_spacing, short, pad, pad_pitch, route_xsize` | 由 `route_quad` 拼出 |

所有 RF/analog 组件的单位都是微米、电阻欧姆、电感亨利，与我们一致；`resistance`/`inductance` 全部**只是存进 `c.info`的标称元数据，不参与任何几何计算**（`inductors.py:138-139` 等——`# Metadata` 注释后直接赋值），即这些是"外部先算好、塞进组件供下游查询"的占位符,不是гdsfactory 自己求解出来的。

文档字符串风格：全部是纯文字 `Args:` 列表,**没有一个 RF/analog 组件的 docstring 里带示例代码块或图片**（`grep '```python'` 在 `analog/*.py`、`spirals/spiral_inductor.py`、`pads/pad_gsg.py` 均无命中）,而这恰恰是 `gf.path.arc()`（`path.py:1547-1553`）、`gf.path.spiral_archimedean()`（`path.py:2057-2063`）等框架层 API 普遍采用的写法（"```python ... .plot() ```" 可运行示例）。也没有发现专门的 RF 教程 notebook（`docs/notebooks` 下与 RF/inductor/transformer 相关的检索为空,只有通用的 `08_pdk.py`/`09_pdk_import.py`）。

### 5.3 `_geometry.py`：一个和我们几乎同构的"手搓多边形数学"共享库

`gdsfactory/components/analog/_geometry.py`（全文件 155 行）提供 `_zip/_map_y/_mirror_x/_sign/_make_aspect_shift_y/_routing_geometric_45/_via_component_info/_add_via_array/_via_array_at/_pgs`，全部基于 `list[tuple[float,float]]` 原始多边形坐标 + `math`/三角函数手算,**没有使用 kfactory 的 `kdb.Region`/`Path.extrude()`**。换句话说，gdsfactory 自己的 RF 器件作者，在真正画八边形电感/变压器这类"没有解析闭式解的复杂平面结构"时，也放弃了框架提供的高层几何抽象，退回到和我们完全一样的手写多边形顶点计算。**这说明 Path/CrossSection/extrude 这套框架能力目前对"复杂环形 RF 结构"的覆盖是不足的，不是我们没用好，而是连 gdsfactory 自己都没用**；框架真正值得抄的是外围设施（第 6 节的过孔阵列/Region DRC 检查、第 4 节的自动回归测试、第 1 节的端口元数据模型），而不是指望 Path/extrude 能替代八边形环的核心画法。

可直接复用的小契约：`_via_component_info(via_component)`（`_geometry.py:63-79`）强制要求任何 via 组件的 `.info` 携带 `xsize/ysize/enclosure/column_pitch/row_pitch` 五个键,缺失就 `raise ValueError`；`_add_via_array`（`_geometry.py:82-115`）基于这五个数拿到 `nb_vias_x/y = floor((avail-w)/pitch+1)` 再一次性 `add_ref(..., columns=, rows=, ...)`。`gdsfactory/components/vias/via_stack.py:100-118,195-199,237-243` 与 `via_corner.py:70-107` 是同一契约的另外两处独立实现（有少量重复,未进一步抽象共享）。

### 5.4 `microstrip.py`：不是几何生成器，是解析 EM 公式库

`gdsfactory/components/analog/microstrip.py`（全文件 214 行）**不含任何 `Component`/`add_polygon`**，是纯函数集合：`_microstrip_Z`（Hammerstad-Jensen 微带线特征阻抗/有效介电常数解析公式，`microstrip.py:58-90`）、`_microstrip_LC_per_meter`、`_microstrip_Z_with_Lk`/`_microstrip_v_with_Lk`（含动态电感 `Lk`，面向超导电路）、`_find_microstrip_wire_width(Z_target, ...)`（`microstrip.py:175-213`，用 `scipy.optimize.fmin` **反解**给定目标阻抗所需的线宽）。这是"先解析算出目标线宽 → 再喂给 `Path.extrude()` 画图"这条路线的公式端实现，价值在于**微带/CPW 这类有闭式解的传输线结构**，对我们真正需要数值电磁仿真（EMX）才能求解的耦合八边形螺旋结构参考价值有限，但对我们如果要新增"焊盘到 DUT 的引线/CPW 馈线"这类简单传输线单元时是一个现成可抄的公式模板。

### 5.5 测试覆盖現状

`tests/components/test_analog_ports.py`（全文件）只对 `interdigital_capacitor`/`spiral_inductor`（量子比特版）做了极薄的行为测试：端口数量、`port_type`、默认层、自定义层、`cross_section.radius` 是否等于线宽。**没有找到任何针对 `inductors.py`/`transformers.py` 里八边形结构（`symmetric_inductor`/`symmetric_transformer`/`stacked_transformer`）的专门测试**；它们唯一的测试覆盖来自第 4.5 节的全局 `get_cells([gf.components])` 自动回归夹具——而 5.1 节已指出 `symmetric_inductor`/`transformer_concentric` 恰好因为命名空间问题**连这层兜底测试都没有**。

---

## 6. 布尔 / 几何工具

### 6.1 phidl 时代的"扁平函数式" API 已经不存在

实测：`hasattr(gf,'outline')==False`，`hasattr(gf,'union')==False`，`hasattr(gf,'xor_diff')==False`，顶层 `gf.offset`/`gf.fill` 也不存在（`hasattr==False`）。仅存的顶层函数是 `gf.boolean(A,B,operation,layer,layer1,layer2)`（`gdsfactory/boolean.py:14-83`，`operation` 支持 `{'not','and','or','xor','-','&','|','^'}`，内部把两侧转成 `kdb.Region` 再调 `boolean_operations[operation](ar, br)`）。原来 phidl 风格的 `outline`/`union`/`xor_diff`/独立 `offset`/`fill` 函数已经被以下两条路径取代：
- `Component` 的方法：`offset(layer, distance, flatten=False, corner_mode=2)`（`component.py:1302-1333`，本质就是 `region.size(distance_dbu, distance_dbu, corner_mode)` 再整层替换）、`fill(fill_cell, fill_layers, fill_regions, exclude_layers, exclude_regions, n_threads, tile_size, ...)`（`component.py:1586-1653`，转发到 `kfactory.utils.fill.fill_tiled`，支持多线程分块）、`get_region(layer, merge, smooth)`（`component.py:990-1012`，直接拿到 `kdb.Region`）、`get_polygons`/`get_polygons_points`（`component.py:968-1034`）。
- 直接用 kfactory/klayout 的 `kdb.Region`（216 个方法，实测枚举）。

### 6.2 `kdb.Region` 是一整套工业级 2D 计算几何 + DRC 引擎（来自 klayout.db，非 gdsfactory 自研）

实测确认存在且已读取官方 docstring 的相关方法：`width_check(d, whole_edges, metrics, ignore_angle, min_projection, max_projection, shielded, ...)`、`space_check(...)`、`enclosing_check(d, other, ...)`、`separation_check`、`notch_check`、`round_corners(r_inner, r_outer, n)`、`minkowski_sum`、`interacting`/`covering`/`overlapping`/`inside`、`sized`、`merge`/`merged`。这些是 KLayout 独立 DRC 引擎在 Python 里的原生暴露,不是 gdsfactory 编写的——`Component.offset()`（6.1 节）用的 `region.size(..., corner_mode)` 就是这层能力的一个包装。

**重要澄清（避免过度归功于 gdsfactory）**：由于我们的 PCell 库本身就是"直接写在 klayout.db 之上"，这套能力我们本来就能直接调用，而且经检索**已经在用**：`src/ic_opt/em/pcell/drc_audit.py:118,127,135,212,222`、`pgs.py:89,106-107`、`_pcell_ind_sym.py:886,890`、`_pcell_guards.py:225,238,302`、`_pcell_xfm_il.py:529,964` 都已经调用了 `width_check`/`space_check`/`separation_check`。所以这**不是**"gdsfactory 有、我们没有"的能力差,而是同一个 klayout.db 底座上"两边都已经用了同一套 DRC 谓词"的印证；差异只在于 gdsfactory 把 `get_region()`/`offset()` 包装成了 `Component` 的一等方法,调用更顺手，而 `round_corners`/`minkowski_sum` 这两个我们当前未见使用的方法（八边形圆角化 / 非矩形 keepout 膨胀）在 gdsfactory 器件库里同样**没有人用**（`_geometry.py` 全是手算,见 5.3）——即这两个方法目前双方都处于"存在但未使用"的状态，是否值得对我们的八边形拐角做圆角化处理，需要单独的 EM 收益评估,不是白拿的框架红利。

### 6.3 过孔阵列：单个 AREF vs 手写双重循环（真实差异，已在 1.2 节给出行号）

这是本节唯一一处双方**存在实质工程差异**、且对我们直接可操作的点：gdsfactory 全线用 `add_ref(via, columns=, rows=, column_pitch=, row_pitch=)` 生成单个数组实例（`_geometry.py:82-115`、`via_stack.py:237-243`、`via_corner.py:96-113`），我们目前是 `_pcell_core.py:584-585` 手写双重 `for` 循环逐个放置。数组实例在大规模过孔（环形电感的下线桥、变压器交叉过孔阵列）场景下能显著减少 GDS 文件里的独立形状/实例数,但代价是数组内部必须整行整列均匀,任何"局部去掉几个过孔"（比如八边形角部避让）的定制都要退化回逐个放置或者拆分成多个子数组——需要按我们实际过孔阵列是否规则来判断是否值得替换。

### 6.4 内联 enclosure 校验（"生成即校验，失败即拒绝"）

`gdsfactory/components/vias/via_stack.py:250-258`：数组摆放完之后立刻用解析公式反算实际外扩边距 `cw/ch`,小于要求的 `enclosure` 就直接 `raise ValueError`（给出具体数字）,而不是生成一个违规版图再事后跑 DRC 才发现。这是一种"在生成函数内部就把最基本的一条工艺规则当作前置条件校验掉"的写法。经检索,我们自己的过孔/中心抽头相关规则也有类似的"fail closed"设计（如内存记录中的 "N28 rule-backed mode ... fails closed on VIA7 CT"），本方向上双方方法论一致,不构成新增点。

---

## 7. v9.51.0 → v10.0.0rc0：与 RF 建模相关的实际变化

`git log --oneline v9.51.0..v10.0.0rc0`（9 个 commit）：
```
5dcaad4 Bump to 10.0.0rc0
bf7204f Remove towncrier from release tooling
c6da59f make gdsfactory pre-release again (MGPA)
5e09039 Replace gdsfactory cross sections with kfactory types (#4827)
4d31c84 docs: fix port sides and cut-off comments in Component basics (#4857)
d23df42 docs: replace remaining d* alias mentions in references, routing and ...
c7e917e update changelog (#4855)
82750f7 Improve the logging situation related to PDK layers (#4853)
b3edcf4 Bump to 9.51.0 (#4854)
```
除版本号/变更日志/发布流程调整外，**唯一一次实质性代码改动就是 `5e09039`（#4827）**：`git show --stat 5e09039` 显示 **142 个文件、+4002/−3766 行**，日期 2026-09-16。这正是本报告第 2.1/2.6/6.1 节反复引用的"CrossSection/Section 迁移到 kfactory 类型"改动，官方随附了 `docs/cross_section_migration.md`（本次一并读取并大量引用，见第 2 节）。`gdsfactory/components/analog/`、`gdsfactory/components/vias/`、`gdsfactory/components/pads/pad_gsg.py` 均**不在**这次改动的文件列表内（`git diff --stat v9.51.0..v10.0.0rc0 -- gdsfactory/components/analog/` 为空），即 5.1-5.5 节描述的 RF/analog 组件接口和命名空间问题**在 v9.51.0 就已经是这个状态**，并非 v10 引入,也不在这次 rc 里被修复。唯一被这次迁移触及的 RF 相关文件是 `gdsfactory/components/spirals/spiral_inductor.py`（量子比特螺旋，+4/−6 行）：

```diff
+from gdsfactory.cross_section.utils import get_port_cross_section
 from gdsfactory.typings import LayerSpec
...
-    cross_section = gf.cross_section.cross_section(
-        width=width, layer=layer,
-        port_names=("e1", "e2"), port_types=("electrical", "electrical"),
+    cross_section = get_port_cross_section(width, layer, gf.kcl)
+    c = gf.path.extrude(
+        P, cross_section=cross_section, ports={0: ("e1", "e2", "electrical")}
     )
-    c = gf.path.extrude(P, cross_section=cross_section)
```
这个 diff 是 #4827 对 RF 建模影响的最小可复现样本：旧 API 里"端口命名/端口类型"是 `CrossSection` 对象自带的字段（`port_names=`/`port_types=`），迁移后 `CrossSection`（=kfactory `DCrossSection`）**不再携带端口命名信息**，端口命名下沉成 `extrude(..., ports={0: (start_name, end_name, port_type)})` 的调用时参数——与第 2.1 节"cross-section 只管截面形状，端口/宽度函数/insets 等属于挤出（extrusion）时刻的关注点"这一设计原则完全一致。`feat: add RF inductors, transformers, and geometry helpers [#4789]`（`CHANGELOG.md`，v9.49.0，2026-08-25）说明本报告第 5 节讨论的整套 RF 组件是在 v9.51.0 之前两个小版本就已落地的既有代码，与本次 rc 无关。

结论：**v10.0.0rc0 相对 v9.51.0，在"RF 建模方法论"层面唯一的实质变化是 cross-section/Section 的内核从 gdsfactory 自研 pydantic 模型切换为 kfactory 原生类型**（连带端口元数据的存储方式、端口命名的归属、`bbox`/`radius` 的语义都随之调整，细节见第 2 节），RF/analog 组件本身的实现、接口、命名空间状态未变。

---

## 8. 模式 → 收益 → 是否适用于我们的 octagon-ring PCell 对照表

| 模式（gdsfactory/kfactory） | 提供的收益 | 适用于我们的 octagon-ring PCell？ | 如何采用 / 成本 |
|---|---|---|---|
| 全局反射注册表 + 自动黄金 GDS/settings 回归（`get_cells`+`difftest`，§4.5） | 新增器件零测试代码即获得 GDS 回归 + 参数快照回归 | **适用，价值高**：我们的 `registry.py` 已是显式注册，比 gdsfactory 的 star-import 更不容易漏（§5.1 教训） | 在 `registry.py` 基础上加一个"遍历已注册器件 → 用默认参数实例化 → 逐层 XOR 面积回归（借鉴 §4.5 的 IoU/sliver-tolerance 思路）+ settings 快照"的 pytest 夹具；成本中等（`drc_audit.py`已有 `kdb.Region`基础设施可直接复用做 XOR） |
| `kdb.Region.width_check/space_check/separation_check/enclosing_check`（§6.2） | 与独立 KLayout DRC 引擎结果一致、C++ 实现更快 | **已在用**，非新增点 | 无需改动；仅需在新规则（notch/enclosing）上补充覆盖 |
| 端口元数据随 GDS/OASIS 自描述（cross-section 信息内嵌端口 metadata，§2.1） | 下游脱离当前 Python 环境也能从 GDS 读回端口宽度/层/类型，减少"多套端口词汇"错配 | **适用，价值高**：直接对应记忆中"三套端口词汇/sNp 序陷阱"的根因之一 | 评估我们导出的 GDS/相关 sidecar 是否已经把端口宽度/层/net 名固化进文件本身（而不仅是 Python 侧字典）；如果没有，参考 kfactory 的"端口元数据随文件走"约定补一层，成本中到高（涉及现有下游解析代码同步） |
| `add_ref(component, columns=, rows=, column_pitch=, row_pitch=)` 单个 AREF（§1.2、§6.3） | 减少大规模规则过孔阵列的实例/多边形数量 | **部分适用**：仅对"整行整列无局部去孔"的规则过孔阵列区域适用 | 对下线桥/交叉过孔这类规则子阵列可直接替换 `_pcell_core.py:584-585` 的双重循环；八边形角部避让等不规则区域仍需保留逐个放置；成本低（局部替换） |
| `Component.show(lyrdb=...)`/`plot(lyrdb=..., markers=...)` 把 DRC 报告/标注叠加到版图可视化（§1.6） | 交互式/静态图直接看到"哪里违规"，而不是纯文本报告 | **适用，价值中高**：直接对应记忆中"报告问题必须配图"的既有偏好 | 若我们的 DRC 审计已经能产出 `kdb.Region`/坐标列表，包一层"渲染到 PNG 并标红违规区域"的工具（不需要依赖 gdsfactory，直接用 klayout.db/klayout.lay 或现有截图流程即可）；成本低——本质是抄"用 Region+markers 驱动可视化"这个思路,不依赖 gdsfactory 本身 |
| `Path`（`straight/arc/euler/smooth`）+ `extrude(width_function=, offset_function=)` 声明式扫描成形（§2.4-2.5） | 变宽/渐变偏移的走线一次 `extrude` 成型,不用手工分段拼多边形 | **有限适用**：仅圆形螺旋/简单渐变线宽场景直接受益；核心的八边形环+下线桥+过孔结构，gdsfactory 自己也放弃了这条路线（§5.3 实证），改手算多边形 | 只在"渐变线宽电感/简单 CPW 馈线"这类新增需求上考虑引入等价的参数化扫描小工具；对现有八边形环主体代码不建议改造，成本/收益比不划算 |
| 微带/CPW 解析公式库（Hammerstad-Jensen，§5.4） | 简单传输线结构可以"目标阻抗→线宽"反解，不需要跑 EM | **低到中适用**：我们的核心器件需要 EMX 数值解，闭式公式帮不上；但焊盘引线/CPW 馈线这类辅助结构可用 | 如果未来要加显式的馈线/CPW 段，可直接抄这套公式（连同 `_find_microstrip_wire_width` 的 `scipy.optimize.fmin` 反解模式）；成本低（纯函数,无框架依赖） |
| `LayerLevel`（thickness/zmin/sidewall_angle/material/z_to_bias/mesh_order + 弃用字段强制走 `*_variation`，§3.2） | 用统一 schema 描述工艺栈,且强制"标称值"和"容差分布"分离建模 | **可参考**：如果我们的工艺规则档案（process rule profiles）目前把公差和标称值混在同一个数字里,这是一个可以对齐的 schema 设计思路 | 仅供 schema 设计参考,不建议直接依赖 gdsfactory 的 `LayerStack` 类型（我们没有采用 gdsfactory 的层栈体系，引入整套类型收益有限）；成本为"设计参考"，非代码依赖 |
| `LayerMap.lyp_to_dataclass`：从 KLayout `.lyp` 反向生成层表源码（§3.1） | 层号表与 KLayout 工艺文件保持单一事实来源 | **适用，价值中**：如果我们现在是手工维护层号字典 | 若我们有对应 `.lyp`/tech 文件,可写一个等价的小脚本生成/校验层号表；成本低 |
| `create_pin`/`DPin`：多个物理端口归组为一个具名 net/pin（§1.5） | 区分"物理端口形状"与"电气 net/pin"两个层级,供网表/LVS 使用 | **视需求而定**：若我们的多指过孔/多焊盘阵列存在"多个物理端口属于同一电气节点"的场景，这是一个现成的建模区分 | 需求驱动，不算通用收益；若无此类多对一场景可不采用 |
| YAML 声明式电路组装 `from_yaml`（§4.4） | 拓扑用 YAML 描述、参数化叶子器件用 Python，更易 diff 审查 | **有限适用**：适合"pad+DUT+地环"这类相对固定拓扑的测试结构组装 | 若要采用需要自建等价的 YAML→Python 解析层（不依赖 gdsfactory 本身）；成本中等,收益取决于测试结构 YAML 化的组织收益是否大于新增一层解析的维护成本 |

---

## 9. 不建议采用的部分（及理由）

1. **不要因为 gdsfactory "有" Path/CrossSection/extrude 就迁移八边形环主体几何过去**：§5.3 已实证 gdsfactory 自己的 RF 器件作者在同类问题（八边形环+下线桥+过孔+中心抽头）上也放弃了这条路线，回到手写多边形。迁移收益不确定,成本（改写全部 PCell 几何+重新验证 DRC/EM 一致性）极高,不划算。
2. **不要引入 gdsfactory 整套 `Pdk`/`LayerStack`/`Component`/`kfactory` 依赖**：我们已经是"直接写在 klayout.db 之上"的轻量路线，gdsfactory 是一个几十万行、以光子学为核心场景的通用框架，其 RF 能力（第 5 节）本身覆盖薄弱、命名空间有已知缺陷（§5.1），引入整套依赖换来的框架红利（主要是 §4.5 的自动回归和 §1 的端口模型）都可以用远小的代价在我们现有 klayout.db 代码上单独复刻,不需要连带引入光子学专用的路由/PDK/YAML 体系。
3. **不要照抄 `gf.components` 的"扁平命名空间 + 多层 `from .x import *` + 手工 `__all__`"组织方式**：这正是 §5.1 命名空间 bug 的根源。我们现有的显式 `registry.py` 注册模式已经优于这个设计,应保持,不要为了"看起来和 gdsfactory 一样"而改成 star-import 风格。
4. **不要采用自动生成的长哈希后缀命名（`_cell_with_module_name` 风格，§4.2）**：那是 gdsfactory 为"数千个通用组件在同一全局命名空间里互不冲突"这个规模问题设计的方案,代价是名字丑、不可读、依赖截断+去重兜底。我们器件数量级和场景都不需要这种复杂度,现有命名方式（如能人工保证唯一性）更可读、更利于人工审查 GDS/日志。
5. **不要指望 `to_3d()`/`get_klayout_3d_script()` 能替代真正的电磁网格生成**：§3.3/§3.5 已确认这两个都只是"按 LayerLevel 的 thickness/zmin 做多边形挤出"的可视化/KLayout 剖面预览工具（`trimesh`/KLayout DRC 脚本文本），不含材料电磁参数、不生成 EM 求解器网格,和我们现有的 EMX 前处理流程不是同一层次的东西，没有替代或对接价值。
6. **`kdb.Region.round_corners`/`minkowski_sum` 暂不建议主动引入**：虽然接口存在（§6.2），但 gdsfactory 自己的 RF 器件也未使用，是否该给八边形拐角做圆角化需要独立的 EM 收益评估（电流拥挤/尖峰场强 vs. 版图面积/加工复杂度），不是"框架提供了就该用"的免费红利，属于需要单独立项验证的方向,不在本次方法论采纳范围内。

---

## 结论摘要

gdsfactory v10.0.0rc0 相对我们自己"直接写 klayout.db 多边形"的 PCell 库,真正的方法论增量集中在**外围设施**而非几何算法本身：(1) `Component`/`Port` 直接就是 kfactory 类型,端口元数据随文件自描述；(2) 一个反射式自动发现 + 黄金 GDS/settings 回归的测试方法论,新组件零成本获得回归覆盖；(3) `@gf.cell` 把命名/缓存/序列化下沉给 kfactory,换来的是极长的自动生成名字；(4) `Component.offset/fill/get_region` 把 `kdb.Region`（含 width/space/enclosing/separation_check、round_corners、minkowski_sum 等 216 个方法）包装成一等方法——但这套 Region 能力本身来自 klayout.db,我们已经在 `drc_audit.py` 等处直接使用,不是差距。核心的射频八边形环/变压器/过孔阵列/中心抽头/PGS 几何,gdsfactory 自己（`_geometry.py`）也是手写多边形数学,并未提供比我们更优越的抽象；其 RF 组件库还存在一个已验证的真实缺陷——`symmetric_inductor`/`transformer_concentric`/analog 版 `spiral_inductor` 因为 `gdsfactory/components/analog/__init__.py:6-14` 的 `__all__` 遗漏而在公共 API 和自动化测试里完全不可见。v9.51.0→v10.0.0rc0 这次 rc 唯一的实质代码变化是 #4827 cross-section 内核替换（142 文件,+4002/−3766 行）,不影响本报告第 5 节对 RF 组件本身的观察。第 8 节表格给出的可采纳项里,价值最高、成本最低的是"自动发现+黄金回归测试方法论"和"过孔用单个 AREF 数组实例"两项；"端口元数据自描述"价值高但改动面广,需要专项评估。
