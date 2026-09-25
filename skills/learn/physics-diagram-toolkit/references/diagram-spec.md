# Diagram specification v3

## 坐标空间

| renderer | coordinate_space | 用途 |
|---|---|---|
| `scene` | `scene_units` | 原创力学图。逻辑 x/y 通过同一比例缩放并居中，角度不受画布纵横比影响。 |
| `scene` | `source_pixels` | EXIF 归一化原图上的精确批注。 |
| `plot` | `plot_data` | 函数、实验数据和坐标图。 |

`scene_units` 还必须声明 `scene.width`、`scene.height` 和可选 `padding_px`。

## Scene v3 示例

```json
{
  "schema_version": 3,
  "renderer": "scene",
  "coordinate_space": "scene_units",
  "purpose": "question",
  "style_profile": "exam-monochrome",
  "canvas": {"width": 1000, "height": 650, "background": "#ffffff"},
  "scene": {"width": 100, "height": 65, "padding_px": 24},
  "objects": [
    {"id": "ball", "type": "sphere", "position": [20, 18], "size": [4, 4]},
    {"id": "impact", "type": "impact_marker", "position": [82, 52], "size": [2.5, 2.5]}
  ],
  "vectors": [
    {"id": "v0", "kind": "velocity", "semantic_role": "given", "fact_id": "v0", "start": "ball.center", "direction": [1, 0], "length": 14, "label": "v₀"}
  ],
  "trajectories": [
    {"id": "path", "type": "projectile", "start": "ball.center", "end": "impact.center", "initial_direction": [1, 0]}
  ],
  "dimensions": [
    {"id": "h", "type": "linear", "semantic_role": "given", "fact_id": "height", "from": [20, 18], "to": [20, 52], "offset": [-8, 0], "label": "h"}
  ],
  "relations": [
    {"id": "launch", "type": "connected", "a": "path.start", "b": "ball.center"},
    {"id": "tangent", "type": "tangent", "a": "path.start_direction", "b": "v0.direction"}
  ]
}
```

## 对象、表面与锚点

- 对象：`block`、`point_mass`、`cart`、`sphere`、`pulley`、`collar`、`ring`、`fixed_support`、`impact_marker`。
- 表面：`ground`、`wall`、`line`、`incline`、`rod`。
- 连接：`rope`、`spring`。
- landmarks：`marker=dot|cross|none`，用于 P、Q、接触点和只参与关系验证的语义点。

对象提供 `center/top/bottom/left/right`；固定支座另有 `connection`。表面提供 `start/end/direction`；连接提供 `from/to/direction`；轨迹提供 `start/end/start_direction`。

物块和小车优先使用 `on_surface:{surface,fraction,clearance}`。编译器自动对齐、计算接触点，并检查底边与表面的残差不超过 0.5 px。

## 关系

支持：

- 点关系：`coincident`、`connected`、`contact`、`fixed_to`、`passes_through`，默认残差 ≤ 0.5 px。
- 方向关系：`parallel`、`perpendicular`、`tangent`，默认角度残差 ≤ 0.5°。

关系必须引用已登记的语义点或方向；未知类型和未知引用直接失败。

## 轨迹、尺寸和批注

- v3 平抛只使用 `projectile`：终点必须位于起点右下方；起点解析切线水平，采样点连续向右下方且曲率连续。轨迹默认实线，只有显式 `dash` 才画虚线。
- `dimensions[type=linear]` 生成投影线和双箭头尺寸线；`offset` 决定尺寸线与原测量段的距离，`extension_gaps:[起点留白,终点留白]` 可避免投影线穿过球或圆环。
- `dimensions[type=angular]` 用 `vertex/ray_a/ray_b` 绑定实际两条射线，可用 `value_degrees` 校验标注值；需要补画水平短线等缺失参考边时设置 `draw_rays:["a"]` 或 `["a","b"]`。
- annotations：`text`、`math_label`、`line`、`projection`、`leader`、`point_marker`、`highlight`。v3 自由角标禁用，改用 angular dimension。
- 标签默认进行边界和关键几何碰撞检查；确需放在物体内部时只能由编译器生成 `allow_overlap`。

## 图层与风格

默认图层顺序为 surface → structure → connector → trajectory → dimension → vector → annotation → label。旋转物块使用白色填充 polygon，表面位于其后，避免斜面穿入物块。

- `exam-monochrome`：结构、已知量、运动和尺寸全部黑白，禁止分析层。
- `solution-color`：结构黑、运动蓝、受力红、尺寸绿、辅助关系灰。

## Plot v3

plot 数据结构沿用 v2，仅将 `schema_version` 升为 3，并增加 `purpose/style_profile`。表达式仍只允许数字、所选变量、`pi/E`、算术和白名单单参数函数；禁止属性访问、导入、索引、推导式和任意 Python。

## Legacy

- `render_diagram.py` 继续接受低层 schema v1 primitives。
- scene spec v2 和 plot spec v2 继续用于读取及草稿渲染。
- 新正式交付不得手写 v1 或使用 v2 spec，必须经 request v2 + spec v3 流水线。
