# pcell 建模质量与架构批判(非 DRC 复审)

范围与方法:只读代码 study,目标是 `<repo>/src/ic_opt/em/pcell`
(下称 `pcell/`)。任务不是重新审 DRC(2026-09-21 的 gdsfactory 对比审查已经做过,见下),而是评估
**建模逻辑与架构**本身是否"简陋"——形状表达力、构造方法、端口模型、参数接口、工艺抽象、鲁棒性、测试/文档质量。
所有路径为绝对路径,行号对应 2026-09-22 研究时的工作树状态(`git status` 干净)。N28/N65 私有 profile
只引用规则 ID/层名,不引用任何数值;示例数值一律来自仓库自带的公开虚构 profile
`pcell/profiles/demo_6m/rule.yaml`。计时/调用计数均为本次研究现场用
`ic-opt-modular/.venv/bin/python` 实测,非推测。

背景文档(已读,不重复其已确认的结论,只在需要时引用):
- `<em-opt>/docs/reports/2026-09-21-gdsfactory-comparison-and-pcell-improvements.md`
  (下称"gdsfactory 审查",D1-D11 缺陷编号即出自此文,§4)
- `<repo>/docs/refactor/analysis/em/02_pcell_geometry_layer.md`
  (下称"契约文档",已系统记录 API/schema/耗时/fingerprint 设计;本文件不复述其内容,仅在结论有出入处指出)

`pcell/` 目录的真实文件清单(比任务描述给出的列表多几个文件,一并纳入研究范围):
`_pcell_core.py`(1479行) `_pcell_primitives.py`(1103) `_pcell_ind_sym.py`(1098)
`_pcell_xfm_bs.py`(232) `_pcell_xfm_ms.py`(207) `_pcell_xfm_balun.py`(485)
`_pcell_xfm_tw.py`(960) `_pcell_xfm_il.py`(1448) `_pcell_guards.py`(333)
`_pcell_straight_extension.py`(135) `pgs.py`(122) `pcell_inductor_port_clean.py`(254,facade)
`generator_plugin.py`(1184) `process_rules.py`(414) `rule_adapter.py`(245) `drc_audit.py`(618)
`gds_compare.py`(108) `README.md`(397) 以及任务列表未提及的
`base.py`(58) `__init__.py`(34) `path_safety.py`(13) `profile_validation.py`(564) `registry.py`(143)
`_pcell_demo.py`(1050,离线 demo/报告工具,不在生产 `generate()` 路径上)。.py 总计约 12,684 行。
测试:`tests/ic_opt/pcell/` 10 个文件共 10,003 行,本次实测
`IC_OPT_PROFILE_DIRS=<N28/N65 私有目录> ./.venv/bin/python -m pytest tests/ic_opt/pcell/ -q`
→ **634 passed, 0 failed, 45.61s**(含真实 N28/N65 profile,不只是 demo_6m)。

---

## 1. 几何词汇表:能表达什么、不能表达什么

### 1.1 唯一的环拓扑原语:固定 8 边、45° 倒角的八边形

六个家族的每一个"环"最终都经由同一个函数 `base_oct_quad`
(`pcell/_pcell_primitives.py:229-307`)画出(直接调用,或经 `base_oct_half`/`base_oct` 包装,
`_pcell_primitives.py:315-379`)。该函数硬编码倒角比例:

```python
C = ceiltogrid(W * math.tan(PI / 8) + 0.005)      # _pcell_primitives.py:258
DIV = 2 + math.sqrt(2)                             # _pcell_primitives.py:259
A = roundtogrid(OD / DIV)                          # _pcell_primitives.py:260
```

`tan(pi/8)`(45° 半角)与 `DIV=2+sqrt(2)`(八边形外径→倒角边长比例)是**几何常数**,不是参数——
六个家族都无法把边数改成 4/6/12/16,也无法把倒角角度从 45° 改成任意值。同一对常数在文件内**独立重复
出现了至少 5 处**而非收敛成一个共享 helper:`base_oct_quad`(258-262)、
`base_oct_quad_vias`(429-431)、`base_xfm_half`(517-519)、`base_ind_hud_cross` 的走廊裁剪数学两处
(996-997、1024-1025)。`_pcell_core.py:1290-1301` 的注释自己承认了这个限制的后果:
`base_oct_quad`/`base_oct_half` "hard-coded for exactly ONE opening per quadrant pair",
xfm_tw 需要每环 4 个独立槽位,"does not fit that algebra without forking it per-edge"——于是
xfm_tw 只能整套重新实现八边形顶点计算(`_tw_oct_chamfer`/`_tw_oct_vertices`/`_tw_oct_walk_pts`,
`_pcell_xfm_tw.py:208-279`),而不是扩展共享原语。gdsfactory 审查自己也把"支持 4/12/16 边"列为
"不立项",理由正是"改动集中在跨线结(四处把 45°/√2 写死)而非环本体"(审查 §3 表格倒数第二行)——
本次代码阅读确认了这四处硬编码正是上面列出的这五个位置(审查数的"四处"与本次读到的"五处"略有出入,
可能是审查只数了跨线结、未计入 `base_xfm_half`/`base_oct_quad_vias` 这两个更少用的变体)。

**结论**:整个库的几何词汇表是"八边形环(固定8边、固定45°倒角)+ 每象限最多一个开口" ——
圆形环、矩形环、任意多边形环、可变倒角角度均不可表达,且这不是某个家族的局部限制,而是最底层原语
`base_oct_quad` 的属性,六个家族(除 xfm_tw 自己重新实现之外)全部继承这一限制。

### 1.2 绕组拓扑:对称、等匝距、等线宽,无渐变

`ind_sym` 的多匝绕组由 `_ind_ring_turns`(`_pcell_ind_sym.py:152-345`)驱动,`PITCH = W + S`
对整个绕组只有一个标量取值(默认值;`PITCH` 参数可覆盖,但仍是**单一**标量,不能按匝渐变),`W` 同样
是绕组全程唯一线宽。函数名 `ind_sym`("symmetric")本身就是设计前提:绕组必须关于原点点对称
(每匝在四个方向上对称展开)。**渐变匝距、渐变线宽、非对称(不关于中心点对称的)螺旋**在当前架构下
不可表达——不是某个校验拒绝了它们,而是构造函数根本没有对应的自由度。

多层叠绕(> 2 层同时耦合,而非 xfm_bs/xfm_ms 的恰好 2 层)同样不存在:`xfm_bs`/`xfm_ms` 的签名
只接受 `PRI_ME`/`SEC_ME` 或 `SINGLE_ME`/`MULTI_ME` 两个金属参数,没有"N 层耦合"的家族或参数扩展点。
这与 gdsfactory 审查 §7"明确不做"列表("M:N 叠层与连续 spiral")一致——是记录在案的范围决策,
不是本文新发现,但作为"能表达什么"的完整性清单仍需列出。

### 1.3 屏蔽:只有全器件底部鱼骨 PGS,没有绕组间屏蔽

`pgs.py`(122行)实现的是**唯一**一种屏蔽形状:M1 上的水平鱼骨(横条+单脊柱+单接地点,
`add_pgs`,`pgs.py:21-122`)。且只有 `CleanPortIndSymConfig`/`CleanPortXfmBsConfig`/
`CleanPortXfmMsConfig` 三个 family 的 pydantic 配置声明了 `pgs` 字段(`generator_plugin.py:231,
311, 384`);`CleanPortXfmBalunConfig`/`CleanPortXfmTwConfig`/`CleanPortXfmIlConfig`
三个类完全没有该字段,且基类 `extra="forbid"` 会直接拒绝任何尝试。**"绕组间屏蔽"**(例如
xfm_bs 广边耦合的两层之间插一层局部屏蔽以降低容性耦合)这一概念在词汇表里完全不存在——现有 PGS
是器件底部的接地屏蔽,不是层间屏蔽。

### 1.4 引线/桥/夹具:形状恒定,只有位置可选

- **引线(lead)**:`base_lead`/`base_lead_pair`(`_pcell_primitives.py:548-735`)永远画一个
  轴对齐矩形 via 块(`vias(Length=W, Width=L)`),两条引线永远关于 y=0 对称、永远水平。没有斜向
  引线、没有渐变宽度引线。
- **跨线(bridge/crossunder)**:整个库只有一种跨线拓扑——`base_xfm_cross`
  (`_pcell_primitives.py:133-221`,一条 `base_ind_diag` 对角线 + 两端 via 块),六个家族的所有
  "跨线"最终都实例化这一个原语(有的实例化两次,如 `base_ind_hud_cross` 的 cross1/cross2)。
- **地夹具 stub**:`add_ground_fixture`(`_pcell_core.py:869-972`)对每个端口按"离哪条包围盒边最近"
  的欧氏距离启发式(`_pcell_core.py:902-911`)分配左右上下四个方向之一,每个方向对应**恰好一种**
  手写倒角梯形(四段独立的 `add_polygon` 调用,`_pcell_core.py:951-970`),环本体永远是 4 个矩形拼成
  的空心矩形框(941-944)——**不会**跟随器件本体的八边形轮廓收紧,这正是 README(`README.md:130-136`)
  自己描述的"不等 OD 的 xfm_bs/xfm_ms 会把环往外推"行为的几何根源,也是第 6 节 D5 的间接成因之一。

### 1.5 一个反例:xfm_tw 证明"path/cross-section 挤出"技术已经存在

`_tw_add_wide_path`(`_pcell_core.py:1332-1344`)用 `kdb.Path(pts_nm, width).polygon()`
沿中心线挤出一条等宽导体——这是全库**唯一**的"中心线+宽度→多边形"式构造,只被两处使用:
xfm_tw 自己的环/跨线渲染(`_pcell_xfm_tw.py` 内部)和 `ind_sym` NT=2 紧凑桥的两条腿
(`_pcell_ind_sym.py:812-813`)。这证明"path extrusion"不是技术能力缺口,而是**架构选择**——
其余所有原语(占全库绝大多数)仍然是逐点手算顶点列表,详见第 2 节。

---

## 2. 构造方法:手算整数-nm 顶点列表 vs path/cross-section 挤出

### 2.1 现状:除 1.5 节的两处例外,一切都是手写顶点公式

`base_oct_quad` 的主体(`_pcell_primitives.py:264-306`)是两段各 8 顶点的
`cell.add_polygon(layer, [(x1,y1), (x2,y2), ...])` 字面量列表,每个坐标是 `OD`/`W`/`OP` 的
闭式代数表达式。`base_ind_diag`(34-92)、`base_xfm_cross`(133-221)同理。这些浮点表达式各自独立
调用 `ceiltogrid`/`roundtogrid`/`floortogrid`(`_pcell_core.py:39-48`,0.005 um 网格)量化,
再经 `Cell.add_polygon` → `_nm()`(`_pcell_core.py:66-68`,`int(round(x_um/0.001))`)逐点取整到
纳米。**没有一个共享的"路径中心线 + 宽度 → 多边形"函数覆盖这类形状**;每个原语都是独立的手解代数。

### 2.2 代价 1:同一公式在文件内独立复制

八边形倒角公式(`A`/`BA`/`C`,见 1.1)在 `_pcell_primitives.py` 内至少复制 5 次(见 1.1 引用的
行号),而不是集中到一个函数里传参复用。这直接导致后来必须再写一个"楼梯偏置"补丁函数
`chamfer_staircase_delta`(`_pcell_core.py:1045-1091`)去补偿"每个环独立量化,相邻环倒角边可能
比 floor 少 6-9nm"的问题——补丁本身还要**再抄一遍**同一公式(`_pcell_core.py:1074-1077`)才能算出
需要偏置多少格。这是典型的"公式没有唯一权威来源"导致的连锁维护成本。

### 2.3 代价 2:独立取整导致本应相等的量出现 1nm/亚纳米级偏差(D5/D8/D9 的根因,见第 6 节)

因为很多"物理上应该相等"的量是通过**文本不同但数值等价**的浮点表达式分别计算、分别取整的
(例如某个端口坐标由一条表达式算出并 `_nm()`,而它理应落在的引线多边形顶点由另一条表达式算出、
经由环开口/桥参数间接推导、再各自 `_nm()`),当真值恰好落在取整边界附近时,两条独立路径可能取整到
相差 1 个网格(5nm)甚至 1nm 的不同整数。这正是第 6 节 D5(地环位置跳变)、D8(45°倒角边出现
44.9959°)、D9(center_spacing 未做网格校验)三个缺陷共享的**同一种架构性病因**——如果坐标是通过
"一条路径中心线,一次取整"的方式产生(2.1 节 `_tw_add_wide_path` 的模式),这整类缺陷会在架构层面
被消除,而不是像现在这样逐个打补丁(`_junction_clearance_const`、`chamfer_staircase_delta`、
`_check_winding_fit` 各自处理一个症状)。

### 2.4 每个家族的实际代码成本(直接测量)与"复用 vs 重新发明"的强相关

| 家族 | 行数(`_pcell_xfm_*.py`) | 复用策略 |
|---|---:|---|
| `xfm_bs` | 232 | 直接调用 `base_oct`/`base_lead_pair`/`vias`,不复用 ind_sym |
| `xfm_ms` | **207**(全库最小) | 单圈直接复用 `xfm_bs._bs_winding`;多圈**整体复用** `ind_sym`/`_compact_two_turn_winding` |
| `xfm_balun` | 485 | 复用 `base_oct_half`(经 `base_xfm_half`)与 `ind_sym`(NT≥2 分支),但同层嵌套+逃逸跨线是全新代码 |
| `xfm_tw` | 960 | **不能**复用 `base_oct_quad`(1.1 节"每象限一个开口"限制),自建八边形顶点/走线/leg 规划器 |
| `xfm_il` | **1448**(全库最大) | 同样不能复用 `base_oct_quad`;自建 CT 方向判定、双层跨线、桥车道规划 |

`ind_sym` 本身(`_pcell_ind_sym.py`,1098 行)里位于 `_compact_two_turn_*` 系列
(`_pcell_ind_sym.py:624-1097`,约 470 行)的 NT=2 紧凑绕组分支单独就接近 xfm_bs 全家族的两倍
——这是第 6 节要展开的性能热点,但在这里先指出它的**规模**已经和"发明一个新家族"是同一数量级。

净结论:两个能够复用 `ind_sym`/`xfm_bs` 内核的家族(ms、在较小程度上是 balun)代码量落在
200-500 行;两个因为共享原语"每象限一个开口"的限制而被迫从零实现八边形几何的家族(tw、il)
代码量落在 960-1448 行——**4-6 倍的成本差直接可归因于原语层的不灵活,而不是拓扑本身固有的复杂度**。
这意味着:**第 7 个家族一旦需要每环超过 1-2 个开口(目前六个家族里 tw/il 已经踩过这条线),几乎可以
确定不是"调用现有原语传新参数"的量级,而是比照 tw/il 的 1000+ 行级别的全新实现**。

### 2.5 "core"文件的边界名不副实

`_pcell_core.py`(1479 行)按拆分说明(文件头注释,`_pcell_core.py:1-4`,"Auto-split from
pcell_inductor_port_clean.py")理应是所有家族共享的通用核心,但其中至少两大块并非通用:

- **xfm_tw 专属渲染原语**(`TwLeg`/`TwArc`/`_TW_MIRROR_ANGLE`/`_TW_CARDINAL_UNIT`/
  `_TW_TANGENT_CCW`/`_TW_GRID_DBU`/`_tw_snap_dbu_to_grid`/`_tw_add_wide_path`/
  `_TW_FIXED_PORT_ORDER`,`_pcell_core.py:1222-1347`,约 125 行)——逻辑上属于
  `_pcell_xfm_tw.py`,却物理上留在 core 里,是"auto-split verbatim move"(拆分时逐字搬迁、
  未重新归类)遗留的边界模糊。
- **地夹具功能**(`add_ground_fixture`/`_body_bbox_um`/`_drawing_bbox_um`,
  `_pcell_core.py:792-972`,约 180 行)是一个"产品特性"(每个 family 都强制调用一次),
  而非几何原语——它更适合和 `pgs.py` 并列成为独立模块,而不是掺入 core。

这种边界模糊会让"新增第 7 个家族该改哪个文件"这个问题的答案变得不直观:core 文件的体量与内容
分布,并不能一致地反映"这是所有家族都用得到的东西"。

---

## 3. 端口模型:文本标签 + 隐式几何,而非显式端口对象

### 3.1 `Port` dataclass 没有 width/orientation 字段

`Port`(`_pcell_core.py:330-349`)的字段是
`name, logical_name, metal(int), label_layer(tuple), point_nm(tuple), lead_zone_nm(4-tuple)`。
**没有 `width` 字段,没有 `orientation` 字段**:

- 宽度只能靠调用方自己从 `lead_zone_nm` 的窄边反推(`abs(x1-x0)` 或 `abs(y1-y0)`,取决于哪个轴更窄),
  且必须先猜对哪个轴是宽度轴。
- 朝向(端口朝 +x/-x/+y/-y 出线)完全没有存储字段;`generator_plugin.py:894-898` 的
  `audit_port_lattice` 里 `on_x_edge`/`on_y_edge` 判定逻辑,本质上就是**事后**重新推断朝向以便
  做审计——如果朝向是端口对象的一等字段,这段推断代码根本不需要存在。
- 差分对语义(P1/N1 是一对差分端口 vs 独立单端端口)**完全没有结构化表达**,只靠端口名字符串里的
  P/N 字母约定;`Port`/`_port_dict` 里没有任何"这两个端口互为一对"的字段。
- 参考端子(`reference`)的语义极窄:`_port_dict`(`_pcell_core.py:287-309`)把 `reference`
  初始化为 `None`(代表"参照无穷远地平面"的 EMX edge port),**唯一**改写它的地方是
  `add_ground_fixture`(`_pcell_core.py:972`,`p["reference"] = ref_name`)——即"本地 M1 环 stub"。
  没有第三种可能(例如让一个端口显式参照*另一个信号端口*,构成真正的差分 EMX port)。

### 3.2 落盘时进一步降维:结构化端口信息从未离开内存

即使内存里的 `Port`/`_port_dict` 已经比"端口对象"窄,落盘的产物还要更窄:
`_write_geometry_outputs`(`generator_plugin.py:922-980`)只把 `cell.emx_ports` 转成
`-p name=signal[:reference]` 纯文本行(`emx_port_lines`,`_pcell_core.py:1442-1455`)写入
`emx_ports.txt`,`geometry_manifest.json` 里也只有这份文本行列表(`generator_plugin.py:964`,
`suggested_emx_ports`)。`GeometryGenerationResult`(`base.py:10-16`)不携带任何端口坐标/层信息。
也就是说:**`point_nm`/`lead_zone_nm`/`metal_index` 这份结构化数据,从产生到被丢弃只存在于一次
`generate()` 调用的内存里**,契约文档 §1.4/§10.2 已经指出这一缺口,本次独立复读代码确认属实。

### 3.3 EMX 端口放置规则:靠"事后断言",不靠"构造时保证"

端口必须精确落在自己引线的边缘/必须有对应文本标签/必须落在真实导体上——这三条规则不是通过一个
"端口对象"的构造过程结构性保证的,而是靠两层独立的运行期断言事后检查:
`_check_port_lattice_invariant`(`_pcell_core.py:750-767`,内存态自洽)与
`audit_port_lattice`(`generator_plugin.py:823-919`,重新解析刚写出的 GDS)。这两层检查写得很
严谨(zero-tolerance,不用最近点搜索),但**架构上**它们存在的原因,正是因为端口坐标/引线区域是由
调用方各自"手算+调用 `add_emx_port`"拼出来的,而不是从一个类型系统就能保证一致性的端口对象派生
出来的——检查得越严谨,恰恰说明底层数据模型本身不足以让这类错误变得不可能。

### 3.4 地夹具"stub"如何工作(任务明确要求说明)

`add_ground_fixture`(`_pcell_core.py:869-972`)的算法:
1. 对每个端口,按其 `point_nm` 到器件整体包围盒 4 条边的欧氏距离,分到 left/right/bottom/top
   四类之一(`distances_by_port`,902-911)——**这是唯一可用的"朝向"判定方式**,因为 3.1 节已经
   指出 `Port` 没有显式朝向字段。
2. 每条边的内环边界取两个独立下界的"较远者"(917-940):
   `(该边上所有端口的 point ∓ stub_length_um)` 与 `(非端口本体包围盒 ∓ inner_margin_um)`
   ——`_body_bbox_um`(`_pcell_core.py:809-866`)用"多边形减去所有已登记引线区域"的布尔差专门算出
   "非端口本体"部分。
3. 环永远是 4 个矩形(941-944,一个空心矩形框,不跟随器件八边形轮廓);每个端口在其对应边上画
   **一种**手写倒角梯形 stub(4 个方向 4 段独立 `add_polygon`,951-970),再放一个
   `G{index:02d}` pin 标签(971),并把该端口的 `reference` 设为这个标签名(972)。

---

## 4. 六个家族的参数接口(`generator_plugin.py`)

### 4.1 单位:一致性做得好

所有长度字段无一例外以 `_um` 结尾(`outer_diameter_um`…`stub_chamfer_um`…
`strip_width_um`/`strip_spacing_um`/`margin_um`),计数字段无后缀(`turns`/`multi_turns`/
`ring_count`)。这一点在六个家族间**完全一致**,是值得肯定的设计纪律,不应被下面的批评掩盖。

### 4.2 概念命名跨家族不一致(任务明确要求核查的三组)

**"匝数"有 4 种拼法、至少 2 种语义**:

| 家族 | 字段名 | 语义 |
|---|---|---|
| `ind_sym` | `turns` | 唯一绕组的 NT |
| `xfm_il` | `turns` | **同一字段名**,但语义是"P 和 S 共用的匝数"(单绕组值 vs 双绕组共享值,语义已不同) |
| `xfm_ms` | `multi_turns` | 只描述多匝那一侧;单匝侧的"1"从不出现在任何字段里 |
| `xfm_balun` | `primary_turns`/`secondary_turns` | 两侧独立 |
| `xfm_tw` | `ring_count` | **不是匝数**,是环数 NR(=2×每绕组匝数),`generator_plugin.py:547-550` 的
  docstring 明确警告"deliberately not called turns... reusing turns here would misrepresent it" |

**金属层命名 4 套体系**:`top_metal`+`bottom_metal`(ind_sym,但 `bottom_metal` 是死参数,见 4.3)、
`primary_metal`+`secondary_metal`(bs)、`single_metal`+`multi_metal`(ms)、`balun_metal`
(balun,单字段,因为两绕组共层)、`top_metal`(tw、il,单字段,隐含跨线层由内核推导)。
六个家族里没有两个用同一套"金属选择"词汇。

**"开口(OPENING)"字段名相同但物理定义不同**:`opening_p_um` 同时出现在 `xfm_tw`
(`generator_plugin.py:560-561`)与 `xfm_il`(637-638)。前者的 docstring
(552-559)明确定义为"P 侧两个 stub **内边缘的总间隔**"(一个跨两个端口的量);
后者的 `opening_p_um`/`opening_s_um`(与 bs/balun 的 `primary_opening_um` 同族)是**单侧自己的**
半开口量,与 `ind_sym.opening_um` 同一定义体系。也就是说同一个字段名 `opening_p_um` 在库内两个
文件里对应两种不同的物理量——这正是任务要求核查的"同名不同义"陷阱的一个实例。

### 4.3 死字段:验证生效但行为不生效(比"未声明字段"更隐蔽)

- **`bottom_metal`**(`CleanPortIndSymConfig`,`generator_plugin.py:204`):config 层校验它
  非 M1(`_metals_not_m1`,206-209),构造层把它原样写入 `Cell.params`
  (`_pcell_ind_sym.py:523`),但**从未**被读取来选择实际跨线层——真正的跨线层来自
  `_metal_below(top_met, process)`(profile 驱动的真实相邻导体)。`drc_audit.py:345-346`
  的注释直接承认:"bottom_metal remains the chain's documented dead parameter — never echo
  it here"。
- **`top_cell`**(`PassiveDeviceGenerator.generate(..., top_cell: str | None = None)`,
  六个 `CleanPortXxxGenerator.generate()` 均接受此参数):`_write_geometry_outputs`
  (`generator_plugin.py:930-934`)**恒定**用 `gds_name` 的文件名 stem 覆盖 `cell.name`,
  函数体内从未读取 `top_cell` 形参本身。

两者都"看起来会生效"(有类型、有校验、出现在签名里),实际上被静默忽略——这比 pydantic
`extra="forbid"` 会直接拒绝的"未知字段"更危险,因为使用者拿不到任何报错信号。

### 4.4 耦合参数:约束是双重强制的,但没有"派生值"帮手

`center_spacing_um` 相对 OD 的上界(`(od_a+od_b)/4`)在**两层**独立强制:config 层
`model_validator`(如 `CleanPortXfmBsConfig._center_spacing_keeps_overlap`,
`generator_plugin.py:335-352`)与构造层 `check_stacked_overlap`
(`_pcell_xfm_bs.py:122-135`)。这保证了"两处表达同一条规则"至少数值上不会漂移太久
(pydantic 层测的是 config 字段,构造层测的是实际调用参数,理论上应恒等)。但**没有任何字段是从其他
字段派生计算出来的**——比如没有"给定期望的端口间距,反推 OPENING"这类便利函数;`OPENING` 上界
`max_opening(OD, W)`(`_pcell_core.py:51-63`)只负责拒绝非法值,不负责建议合法值。这正是
gdsfactory 审查已经指出的"端口中心距由 OPENING 与 W 联动决定,缺一个独立自由度"的具体体现
(审查 §2 第 2 行)。

### 4.5 校验质量:pydantic 层扎实,但两层校验存在同规则重复表达的维护风险

Config 层(`generator_plugin.py`)校验质量高:`extra="forbid"` 严格模式、`Field(gt=0)` 类范围
约束、`model_validator(mode="after")` 跨字段约束且报错信息带具体数值(如"center_spacing_um
{X} exceeds (primary_od+secondary_od)/4 = {bound}")。但**同一条规则**常常在构造层
(`_pcell_guards.py`/`_pcell_xfm_*.py` 内联 `if/raise`)**重新手写一遍**,而不是共享同一个校验器:
"CT 必须低于所在绕组层"这条规则在 `_pcell_xfm_bs.py:179-186`、`_pcell_xfm_ms.py:97-100`、
`_pcell_xfm_balun.py:245-264`(`_add_balun_ct` 内)各自手写一次(措辞、变量名都不同)。
gdsfactory 审查 §5.3 记录的一次真实回归——`_check_trace_rules` 只接到了 `ind_sym` 顶层,
`xfm_ms`(NT_M=2)/`xfm_balun`(NT=2)绕过它直接调用 compact 规划器,导致同一组"引线臂间隙不合规"
的参数在 ind_sym 上按名拒绝、在另外两个家族上却静默出图——正是这种"两层校验、规则各自表达"架构下
必然会发生的一类缺陷,而不是偶然疏忽。

---

## 5. 工艺抽象:`process_rules.py` / `rule_adapter.py`

### 5.1 profile 提供什么

`ProcessRuleProfile`(`process_rules.py:227-243`)= `layer_catalog`(conductors: drawing+pin
GDS 层/datatype + emx_name + class 字符串;vias: drawing + emx_name + connects 二元组;
markers)+ `layout_rules`(`metal_width_space`: min/max width、min space,三者皆可选;
`via_primitives`: cut_size、min_cut_space、按导体的 min_enclosure 映射;`passive_region`:
marker 引用、对**已建模子集**的 via 阵列 min_count/max_space、仅作文档引用不参与生成期门禁的
`via_restrictions`/`metal_restrictions`、条件加严的 `wide_parallel_spacing`)+ **单独一段**
`emx_stack`(每导体 `thickness_um`、每 via `emx_effective_size_um`、一个全局
`geometry_scaling`,`process_rules.py:87-105`)。

pcell 构造层唯一的入口是 `GeometryRuleAdapter`(`rule_adapter.py:66-241`,已完整通读全文件
245 行),其方法面正好是
`layer()/metal_rule()/via()/via_between()/plan_passive_via_array()/passive_via_restriction()/
passive_via_array_coverage()/manifest()`——**没有任何方法读取 `profile.emx_stack`**
(全文件 grep "emx_stack" 结果为空)。

### 5.2 它缺什么:厚度/z 数据"就在 profile 里"但 pcell 层够不着

`emx_stack.conductors.<M>.thickness_um` 这个字段**确实存在**于 schema 里(证据:
`pcell/profiles/demo_6m/rule.yaml:48-54` 公开示例,M1..M4=0.2、M5=0.9、M6=3.0),但由于
`GeometryRuleAdapter` 完全不暴露它,**pcell 构造代码对厚度/z 一无所知**——不是"厚度信息缺失",
而是"厚度信息存在但这一层的适配器没有读取接口"。与 EMX 真实 `.proc` 文件的 3D 一致性,目前只靠
`profile_validation.py` 的可选 `--proc` 阶段(`_proc_stage`,`profile_validation.py:187-221`)
做**字符串 token 匹配**(`emx_name` 是否作为 token 出现在 .proc 文本里),这是名字层面的一致性检查,
不是任何数值/几何层面的检查,且是 CLI 的可选步骤,不在 `generate()` 的调用链里。

**电阻率/电导率在这份 schema 里完全没有建模**——`EmxConductorStackRule`
(`process_rules.py:87-91`)只有一个字段 `thickness_um`,没有 sheet resistance、体电阻率或任何
与趋肤深度相关的材料参数。也就是说,即便把 `emx_stack` 接进 adapter,也只能补上厚度,补不上
"3D 一致性"里通常还需要的材料属性。

### 5.3 `_metal_below` 如何选层

`_metal_below(met, process, levels=1)`(`_pcell_core.py:182-192`)是纯**目录成员判定**:
`candidates = [m for m in range(1, top) if _metal_name(m) in profile.layer_catalog.conductors]`,
取 `candidates[-levels]`。它只回答"这两个导体名字是否都在 catalog 里"这一拓扑问题,不检查两者之间
是否存在 `via_between()` 可解析的真实 via(那是后续独立调用时才会失败的问题),也完全不涉及厚度/
电学特性——是纯粹的"层目录相邻性",不是"工艺可连接性"或"电学相邻性"。

### 5.4 EMX 侧命名如何派生

`emx_name` 是 profile 里每个导体/via 各自的自由字符串字段,与自己的 `drawing`(GDS layer/datatype)
之间**没有任何结构性约束**,只靠人工作者纪律对齐;唯一的自动交叉检查是上面提到的、CLI 可选的
`_proc_stage` token 匹配。日常 `generate()` 调用链完全不做这项检查。

**小结**:工艺抽象对 pcell 实际需要的 2D-DRC 维度(线宽/间距/via 阵列)覆盖得相当完整、且严格
fail-closed(第 6 节会展开);但对 3D/材料维度(厚度、电阻率、via 有效尺寸)在 schema 里"看似
建模了"、实际在几何构造这一层完全不可达——容易让读者误以为"profile 里有 emx_stack"就等于
"几何层是 Z-aware 的"。

---

## 6. 鲁棒性:D4/D5/D8/D9/D11 现状确认 + NT=2 性能实测 + 静默行为

### 6.1 D4(跨线落点未齐平落环)—— 部分修复,ind_sym 仍开放

`_check_winding_fit`(`_pcell_guards.py:70-99`)现在**确实**在 `ind_sym` 里被无条件调用
(`_pcell_ind_sym.py:491`,发生在 NT==2 紧凑分支判断之前——对所有 NT 都会执行),也在 `xfm_il`
里调用(`_pcell_xfm_il.py:1200-1201`,P/S 各一次)。所以字面意义上"only wired into IL, not
ind_sym"这句话对当前代码已经**过时**——函数调用确实存在。

但 `_check_winding_fit` 只检查**最内层**承接跨线的匝的 OPENING 是否超过 `max_opening` 界限
(docstring:"innermost facing-bearing turn... OPENING that ``_ind_ring_turns`` derives"),
这是一条与 D4 实际缺陷**不同**的规则。D4 的真正修复机制是 `base_ind_hud_cross` 的
`corridor=`/`outer_corridor=` 走廊裁剪参数(`_pcell_primitives.py:830-831, 975-1044`,design-region
issue 04):`corridor` 应传入桥"远端 pad"实际落地所在的**相邻内环**的 (OD, chamfer_bias),
用于裁剪 pad 长度使其不越过该环的倒角。全仓 grep 确认:显式传入真实 `corridor=` 的调用点只有
`_pcell_xfm_balun.py:406` 与 `_pcell_xfm_il.py:1334`;`ind_sym` 自己的多匝构造核心
`_ind_ring_turns`(`_pcell_ind_sym.py:152-345`)只在中间匝调用里传了 `outer_corridor=`
(283-284 行,裁剪**近端** pad 相对**外一环**),从未传 `corridor=`——因此远端 pad 的走廊裁剪
永远落到默认值"用本环自己的 OD"(`_pcell_primitives.py:995`:
`cor_od, cor_bias = (OD, chamfer_bias) if corridor is None else corridor`)。这在几何上不对:
第 i 匝桥的远端 pad 落点半径是 `OD_i/2 − pitch`,恰好等于**下一个更内环**的半径,而不是本环自己
的半径——用错误的(过大的)corridor 半径去算裁剪阈值,不会正确地保护远端 pad 免于越出真正相邻的
内环。**结论:D4 在 `ind_sym` 的通用多匝路径(NT≥3)上仍然实质性开放**,即使一个同名的 `_check_
winding_fit` 现在会跑;balun 与 il 已经拿到了真正的走廊裁剪修复,ind_sym(以及直接复用它的
`xfm_ms` 多匝侧)没有。

### 6.2 D5 / D9(地环位置跳变 / center_spacing 缺网格校验)

`center_spacing_um`(`generator_plugin.py:322, 397, 474`,三处 `Field(ge=0)`)**没有**
`multiple_of` 网格约束——对照同一批 class 里 `straight_extension_um` 明确写了
`multiple_of=0.01`(如 `generator_plugin.py:232-233`),这是一处不对称。`CENTER_SPACING/2`
在 `_pcell_xfm_bs.py:178`(`xP, xS = -CENTER_SPACING/2.0, CENTER_SPACING/2.0`)与
`_pcell_xfm_ms.py:104` 直接作为后续所有环/引线/端口坐标的中心偏移量参与运算——按第 2.3 节描述的
"手算+各自独立取整"架构,当这个中心值落在半纳米边界附近时,端口坐标与地夹具用来分类"这个端口最近哪
条边"的判定(`_pcell_core.py:902-911`)、以及"非本体包围盒"的计算(`_body_bbox_um`,
`_pcell_core.py:809-866`,靠减去已登记 lead_zone 矩形实现),都可能因为两条独立取整路径的 1nm
误差而对同一物理位置给出不一致的分类结果,进而让 `add_ground_fixture` 的"两个下界取较远者"逻辑
(917-940)在两种判据之间跳变——这与 D5 描述的"地环内沿从'端口+stub_length' 跳到 '引线尖+
inner_margin'"症状完全吻合。库内受影响行数(151 行)是历史生产库的数据事实,本次代码研究不重新
核实(不接触/不需要接触器件库),仅确认代码机制在当前 `ic-opt-modular` 树中原样存在。

### 6.3 D8(对角线 0.5nm 余数 → 44.9959°)

`base_ind_diag`(`_pcell_primitives.py:34-92`)在 58/60 行独立计算 `C`/`C2 = ceiltogrid(C/√2)`;
`base_xfm_cross`(133-221)在 188/189 行**为同一个 45° 结合部**再独立算一遍
`C`/`C2`——这是 2.3 节"同一物理关系,两条独立取整链"模式的又一实例,是该缺陷的确切代码位置。
本次未重新推导 44.9959° 这个具体残余角(该数值推导已由 gdsfactory 审查完成),只确认修复前的
代码结构在当前树中未变。

### 6.4 D11(TW/IL 缺少同网自短路守卫)—— 确认开放

`_check_ind_winding_segments`(段数自短路守卫,`_pcell_guards.py:142-169`)与紧凑候选的
`holes()` 环路检查(`_pcell_ind_sym.py:871`,在 `_compact_two_turn_candidate_is_qualified` 内)
**只**被 `_pcell_ind_sym.py`(600-606 行调用前者)使用,`_pcell_xfm_tw.py`/`_pcell_xfm_il.py`
从未导入或调用两者(全仓 grep 确认)。TW(`_pcell_xfm_tw.py:950`)与
IL(`_pcell_xfm_il.py:1413`,包在 try/except 里以给出更友好的错误信息)**确实**调用
`_xfm_net_short`——但那只检查 P 网与 S 网**之间**是否短路,不检查"自己这一个绕组的环是否因为
挤压/取整而意外并成比匝数更少的连通分量"(同网自短路)。这是两类不同的短路检查,ind_sym 两者都有,
TW/IL 只有跨网的那一类。**D11 在当前代码中确认开放**。

### 6.5 NT=2 紧凑绕组路径:现场实测复现"数量级更慢"

用 `IC_OPT_PROFILE_DIRS` 指向公开 `demo_6m` profile、`ic-opt-modular/.venv/bin/python`
直接调用 `ind_sym(...)`(M6/M5,不经过 pydantic/写盘/审计,只量纯构造耗时),对
`_compact_two_turn_candidate`/`_compact_two_turn_candidate_is_qualified` 插桩计数,得到:

| OD/W/S(um) | NT=1 | **NT=2** | NT=3 | NT=4 | NT=5 | NT=2 候选构造次数 | NT=2/其它 NT 倍数 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 120/3/2 | 0.45ms | **316.9ms** | 2.65ms | 4.27ms | 5.90ms | 2,445 | 53.7×(对比最慢的NT=5)–703.8×(对比最快的NT=1) |
| 200/4/2 | 0.36ms | **488.2ms** | 2.78ms | 4.58ms | 6.12ms | 4,047 | 79.8×–1358.7× |
| 120/3/**3**(更松的S) | 0.37ms | **2.86ms** | 2.55ms | 4.21ms | 5.87ms | 15 | 仅 0.5×–7.8× |

根因(代码路径确认,与 gdsfactory 审查的 cProfile 结论一致):`_compact_two_turn_winding`
(`_pcell_ind_sym.py:999-1097`)对 pad 长度做二分搜索,每个候选 pad 长度又调用
`_compact_two_turn_lane_offsets`(897-996)在 250nm 步长网格上线性搜索车道偏移对,每个偏移对
都要真实构造一份 klayout `Cell`(`_compact_two_turn_candidate`,672-850)并跑
`Region.merged()`/`width_check()`/`space_check()`(`_compact_two_turn_candidate_is_qualified`,
853-894)——这是一个**组合搜索**,不是固定开销的解析式构造。第三行的实测证明放宽 `S`(绕组间距)
就能把候选数从 2,445 降到 15、耗时降到 2.86ms——**倍数高度依赖参数是否贴近 DRC 边界**,不是一个
固定的"NT=2 恒慢 N 倍"常数;`xfm_ms(multi_turns=2)` 与 `xfm_balun` 的 `secondary_turns>=2`
路径(经 `ind_sym`)继承同一开销。

### 6.6 静默行为与错误信息质量

- 4.3 节的 `bottom_metal`/`top_cell` 是"验证生效、行为不生效"的静默字段。
- 多个原语的次要参数是**已文档化**的死参数(不是隐藏缺陷,而是明确记录在 `KNOWN_DEVIATIONS`
  或函数 docstring 里):`vias()` 的 `bPP`(`_pcell_core.py:610`)、`base_xfm_cross` 的
  `viat`/`viad`(`_pcell_primitives.py:139-140`,"declared but never read in the reference
  source")、`base_lead` 的 `WD`/`PINP`/`DUMMYL`/`DUMMYP`(`_pcell_primitives.py:551-557`)、
  `base_xfm_half` 的 `WI`/`BB`/`PA`/`PB`(`_pcell_primitives.py:498-508`)。这些字段仍会出现在
  调用签名里,传错值不会有任何反馈,但**至少文档说清楚了**——这是与 `bottom_metal`/`top_cell`
  的关键区别(后两者看起来会生效,前面这些明确写了不生效)。
- `PortError` 消息质量普遍高:几乎每条都带上了具体数值、越界的界限、以及可执行的修复建议
  (例如"increase OD, reduce NT, or reduce W/S")——这是需要在批判之外明确肯定的架构优点。

---

## 7. 测试与文档

### 7.1 `test_pcell_inductor_python_port_clean.py`(6,070 行,294 个 `test_` 函数)

实测按前缀分组(部分):`xfm_il_*` ≥19、`tw_*` ≥10、`xfm_ms_*` ≥12(含 `xfm_ms_dual_ct_*`、
`xfm_ms_ground_ring_regime_stable_across_nm` 等)、`ind_sym*` ≥7、`xfm_balun_*` ≥2 等——测试
颗粒度对齐到每个家族的每个具体行为分支,而不是笼统的"生成不报错"式冒烟测试。这批测试通过
README 描述的特殊加载方式(`sys.modules` 注册后 `spec.loader.exec_module`,绕开常规 import)
**直接调用**六个 family 函数本身,跳过 pydantic 校验、`generate()` 的两次写盘后审计——它钉住的是
"构造函数在给定内部参数(OD/W/S/TOP_ME 而非 `*_um` 公开名)下产生的精确坐标",不是公开契约层。

### 7.2 `test_clean_port_generator_plugin.py`(2,617 行)

与上一份互补,钉住 pydantic config 校验规则、`generate()` 端到端契约(manifest 内容、两次端口/
via 审计、确定性写盘)——即公开契约层。两份测试合起来覆盖了"内部构造"与"外部契约"两层,分工清楚。

### 7.3 黄金几何回归:模块存在,但**未接入测试**

`gds_compare.py`(108 行,`load_physical`/`compare_physical`/`compare_gds`,已在 `src/` 下,
带 CLI)正确实现了仓库自己对"几何相等"的权威定义(逐层 merged-Region XOR 为空 + 标签元组相等,
明确排除字节/hash 比较,`gds_compare.py:1-7` 模块 docstring)。但**全仓 grep 确认
`tests/ic_opt/pcell/` 下没有任何文件 import 它**,且测试目录下**没有任何 `.gds` 黄金基准文件**
被提交。当前对"默认几何是否漂移"的防护完全依赖:(a) 每条测试内联的"现算一个理想 Region、跟实际输出
做 XOR"断言(如 `test_pcell_inductor_python_port_clean.py:5020-5025`),只覆盖测试作者当时想到的
具体情形;(b) 针对(不在本仓库内的)生产器件库,在专项审查时手写、跑完即弃的 `.scratch/` 一次性
回放脚本(gdsfactory 审查 §5 引用的 `replay_library_geometry.py`)——**没有一条常驻、CI 强制执行的
"N 个代表性配置 vs 已提交黄金 GDS"回归门禁**。这是一个具体、可执行的改进点(见第 8 节 P2-ARCH-4)。

### 7.4 README 准确性:内容详实,但存在两处具体问题

- **失效的文档指针**:`README.md:4-5` 把
  `../../../../docs/guide/06-device-variables.md` 与 `07-device-inventory.md` 列为
  "参数参考"和"当前清单/限制"的**首选**入口,但 `ic-opt-modular` 仓库里**完全不存在**
  `docs/guide` 目录(repo 级 `find` 确认)。这是从 `em-opt` 搬迁到 `ic-opt-modular`("moved
  verbatim")时未被携带、也未重新指向的死链接——README 的第一句实质性内容就指向一个不存在的文件。
- **许可证/成熟度自相矛盾,原样带入新仓库**:`pcell_inductor_port_clean.py:28-30`
  写"This experimental port must NOT be merged into the formal `src/` product code without
  an explicit licensing decision",而 `README.md:332-333` 写"These modules are already the
  in-package product implementation; the old 'prototype only / not merged into src' milestone
  description is superseded"。两处对同一份代码的定位描述互相矛盾,且这份代码确实已经在
  `src/ic_opt/em/pcell/` 下被 `generator_plugin.py` 作为生产入口调用——这个矛盾是 gdsfactory
  审查已经发现的问题,本次独立复读两处原文确认它在 `ic-opt-modular` 里逐字未变,说明搬迁过程没有
  顺带解决这个悬而未决的许可证审查问题。

### 7.5 参数文档分三层,中间层需要读者自己做映射

(1) `generator_plugin.py` 里逐字段 `Field(...)` + docstring(精确、机器可查);
(2) `_pcell_*.py` 里 `ind_sym`/`xfm_*` 函数自身的 docstring(精确但用内部 SKILL 风格命名——
OD/W/S/TOP_ME,不是公开的 `*_um` 名字,例如 `generator_plugin.py:989-1000` 里
`p.ind_sym(OD=config.outer_diameter_um, ...)` 才是两套命名之间唯一的映射代码);
(3) 本应是"公开命名版"参考的两份 guide 文档——已确认缺失(见 7.4)。第 (3) 层缺失后,新读者只能
从 (2) 层的映射代码自己反推 `outer_diameter_um ↔ OD` 这类对应关系。

---

## 8. 改进建议(P0/P1/P2)

标注:**改默认几何**=会让至少一条现有(或未来同参数)配置产生字节/物理不同的 GDS,需要新一代
几何版本标记(仓库历史上这类版本记账机制叫 `CURRENT_GEOM_VERSION`,目前活在 `em-opt` 的
`device_db/ingest.py` 里,**尚未移植进 `ic-opt-modular`**——若采纳下述改动,需要新建等价机制或
在重建计划里显式记录"这一代库对应哪次代码改动")并对受影响的库行重新跑 EMX;**架构项**=引入可复用
的基础设施,本身可以做成"纯新增、不改变现有输出"从而不强制版本跳代;**缺陷修复**=对应 D 编号;
**新能力**=当前几何词汇表里不存在、需要全新原语的能力。

### P0(建议在下一次重建前完成)

| 编号 | 内容 | 文件 | 规模 | 改默认几何? | 验证方式 |
|---|---|---|---|---|---|
| P0-D4 | 把真实相邻内环的 `(OD, chamfer_bias)` 通过 `corridor=` 传给 `_ind_ring_turns` 里的
  `base_ind_hud_cross` 调用(仿照 balun/il 已有的用法) | `_pcell_ind_sym.py:257-345`
  (`_ind_ring_turns`);原语 `base_ind_hud_cross` 已支持该参数,无需改 `_pcell_primitives.py` | ~20-40 行 |
  **是**,对当前落点悬空的 NT≥3 行(gdsfactory 审查测算 17-28 行)——需与用户已挂起的
  §6.1 决策("接入并淘汰这些行" vs "先只审计不拦截" vs "改构造后重新EMX")一起定案 | 全库
  `gds_compare.py` 回放(预期只有被标记的行变化)+ `audit_gds` + 新增单测:构造一个当前会
  悬空的小 OD/NT=5 配置,断言远端 pad 落在真实相邻环的倒角线内 |
| P0-D11 | 给 TW/IL 补同网自短路守卫(段数/连通分量计数 + `holes()` 环路检测),对齐
  `ind_sym` 已有的 `_check_ind_winding_segments`/`holes()` | `_pcell_guards.py`
  (泛化 `_check_ind_winding_segments` 接受显式预期分量数与任意金属层);
  `_pcell_xfm_tw.py`/`_pcell_xfm_il.py`(各自绕组构造完成后调用) | ~60-120 行 |
  **可能否**——前提是先对库内现有 tw/il 行做一次回放,确认零新增拒判(gdsfactory 审查
  §4 D11 行自己也这样建议);如回放发现历史行确实自短路,则退化为"是" | 库回放(期望 0 新增拒判)+
  新增合成回归测试:构造一个刻意收紧 S/OD 到会自短路的配置,断言新守卫 fail closed |
| P0-ARCH-1 | 设计一个支持"每环任意数量、任意角度命名开口"的八边形环原语,取代
  `base_oct_quad` 的"每象限最多一个开口"限制——这是让 xfm_tw/xfm_il 未来能收敛回共享原语、
  也是任何"第七个家族"能以合理成本实现的前提 | 新增于 `_pcell_primitives.py` 或新模块;
  第一阶段只新增、不改现有调用点 | 500+ 行(含至少 xfm_tw 的迁移验证,数天量级) |
  **首次引入时否**(纯新增,不接线);**每个家族真正切换时是**,需逐家族单独回放+单独定版 |
  新原语自身的单元测试(任意开口数/角度组合的顶点正确性)+ 逐家族迁移时的全量库回放 |

### P1

| 编号 | 内容 | 文件 | 规模 | 改默认几何? | 验证方式 |
|---|---|---|---|---|---|
| P1-D9/D5 | 给 `center_spacing_um` 加 `multiple_of` 网格校验;审计其余未加网格约束的
  几何浮点字段;让端口坐标与地夹具判据来自同一次取整而非两条独立表达式 | `generator_plugin.py`
  (3 处 `center_spacing_um` 字段定义)、`_pcell_xfm_bs.py:178`、`_pcell_xfm_ms.py:104`、
  `_pcell_core.py`(`add_ground_fixture`/`_body_bbox_um`) | 校验器 ~10 行;"单一取整源"
  重构 ~100-150 行(牵涉多个调用点) | **是**,对当前 center 未落网格的行(gdsfactory 审查
  测算 151 行,与用户已挂起的 §6.2 决策一起定案) | 库回放 + 新增测试:同一物理中心分别用
  "落网格的表达式"和"刻意不落网格再修正"两条路径构造,断言地环内沿完全一致 |
| P1-D8 | 把对角线倒角公式(`A`/`BA`/`C`/`C2`)收敛成一个共享 helper,`base_ind_diag`/
  `base_xfm_cross`/`base_oct_quad`/`base_oct_quad_vias`/`base_xfm_half`/
  `base_ind_hud_cross` 走廊数学统一调用,只取整一次 | `_pcell_primitives.py`(5-6 个调用点)、
  `_pcell_core.py`(新 helper) | ~80-150 行净变化(减少的重复行多于新增) | **理论上否**
  (对已合法的历史行应字节不变,只修正当前会产生 44.9959° 的边缘输入);但因为触达六个家族共用的
  底层原语,建议与下一次本来就要做的重建**一并**验证 | 全库回放(预期近 100% 不变)+
  gdsfactory 审查已定位的 0.5nm 余数输入的专项单测 |
| P1-ARCH-2 | 把 `Port` 升级为携带显式 `width_um`/`orientation` 字段的对象(数据本就在
  `lead_zone_um` 里,只是没有转成命名字段),并把结构化端口信息(而非降维文本行)一并写入
  `geometry_manifest.json`(新增 `"ports"` 键) | `_pcell_core.py`(`Port`、`_port_dict`、
  `add_emx_port`)、`generator_plugin.py`(`_write_geometry_outputs`) | ~150-250 行,纯新增字段
  /新增 manifest 键 | **否**(不改变任何已画多边形,只是把已知量暴露成命名字段) |
  按家族各构造一个用例,手工核对新字段;断言旧 manifest 消费方仍可用(新键是超集) |
| P1-ARCH-3 | 给 `GeometryRuleAdapter` 增加读取 `emx_stack`(厚度、via 有效尺寸)的方法,
  并评估是否需要给 profile schema 补充电阻率/电导率字段以真正支持 3D 一致性 | `rule_adapter.py`
  (新方法)、`process_rules.py`(如需新增材料字段)、`profile_validation.py` | accessor
  ~30 行;材料建模视需求可能是开放量级的新工作(需要工艺 owner 提供真实数值,不是纯重构) |
  **否**(pcell 构造逻辑不必立即消费它) | 新增 adapter 方法的单元测试;如新增材料字段,
  走 `profile_validation.py` 的 schema 校验路径 |

### P2

| 编号 | 内容 | 文件 | 规模 | 改默认几何? | 验证方式 |
|---|---|---|---|---|---|
| P2-ARCH-4 | 把 `gds_compare.py` 接入测试套件:提交一小批代表性 GDS(每家族 × 若干
  NT/金属组合,用可安全公开的 `demo_6m` 生成)作为黄金基准,新增参数化测试重新生成并断言
  `compare_gds(golden, fresh)["physical_equal"]` | 新增 `tests/ic_opt/pcell/golden/` 目录 +
  一个新测试模块 | ~150-300 行(多数是基准生成脚本 + 测试) | 否(纯测试基础设施) |
  该测试本身即验证手段;新增家族/新增基准点时的一次性生成脚本需人工复核初始基准 |
| P2-CAP-1(新能力) | 独立于 OPENING/W 的端口间距自由度(类比 gdsfactory 的
  `port_spacing`,但需吸取其"未做自相交校验"的教训,显式加边界测试) | `base_lead_pair`
  (`_pcell_primitives.py:627-735`)新增可选偏移参数;各家族端口注册调用点 | ~100-200 行,
  纯新增可选参数,默认值=当前行为 | 否 | 新增自相交边界测试(极端偏移值下引线是否自相交) |
| P2-CAP-2(新能力) | 绕组间屏蔽(区别于现有的器件级 PGS 鱼骨),用于 xfm_bs/xfm_ms 广边
  耦合场景降低容性耦合 | 新原语,暂无可依托的现有代码 | 300+ 行,含新的短路守卫 | 否
  (新增可选特性) | 新增 DRC/短路测试(屏蔽不得碰任一绕组) |
| P2-CAP-3(记录不建议做) | 圆形/矩形环、非对称渐变螺旋、>2 层叠绕、任意边数环——均为
  从零实现的新原语家族,不是参数扩展;按 gdsfactory 审查 §7 已有共识,建议只在有真实电路/工艺
  需求时才立项 | — | — | — | — |

---

## 附:与 gdsfactory 审查结论的关系

本次批判聚焦"建模逻辑与架构本身",与 2026-09-21 gdsfactory 对比审查(聚焦"我方 vs 上游的
DRC 严谨性/执行验证")互补而非重复——后者的核心结论(工艺规则驱动、fail-closed、生成期强制
连通性检查、CT 构造正确、端口与真实金属同源等)在本次通读中被独立确认为真实优点,不因本报告的
批判性结论而失效;本报告新增的是:1) 六处新读到的文件(`base.py`/`__init__.py`/`path_safety.py`/
`profile_validation.py`/`registry.py`/`_pcell_demo.py`)与二级子目录的盘点;2) 用当前
`ic-opt-modular` 树重新实测的 NT=2 性能数据(包含插桩得到的候选构造计数,以及"S 稍微放松即可从
700× 降到 8×"这一此前未见记录的敏感性发现);3) D4/D11 在"字面表述"与"实质代码状态"之间的
精确区分(D4 的 `_check_winding_fit` 调用已存在但解决的是另一个问题;D11 的
`_xfm_net_short` 已存在但解决的是另一类短路);4) `docs/guide` 死链接、测试与 `gds_compare.py`
互不相连这两处此前未被记录的具体问题;5) 端口模型、参数命名、工艺抽象三节的架构级批判,
这些不是"缺陷"而是"设计选择的代价",是用户要求的"建模质量"评估的核心。
