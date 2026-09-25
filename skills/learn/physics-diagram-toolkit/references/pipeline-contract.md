# Pipeline contract v2

## Request

正式任务使用 `diagram_request.json` 的 `schema_version: 2`。单图字段可位于根；批量任务用 `items`，根字段作为默认值。

每项必须包含：

- `id`：1–32 个 ASCII 字母、数字、点、下划线或连字符；单图默认 `main`。
- `mode=annotate|original`，`renderer=scene|plot`，`model_profile=weak|strong`。
- `purpose=question|solution|annotation`；`annotate` 只能配 `annotation`。
- `style_profile`：题目图固定 `exam-monochrome`，解析图固定 `solution-color`；批注图显式选择其一。
- `diagram_intent.facts`：题干事实数组，每项含唯一 `id` 和原文 `quote`。题目图中的 given/motion 向量、尺寸或批注必须引用 `fact_id`。
- 批注项提供 `source_image` 和可选 `source_sha256`；原创项提供 `question_text` 和可选结构化 `query_text`。

`output_dir` 必须位于 `output/`。发布目标固定为 `<output_dir>/<task-id>/`，不同内容不得覆盖。

## 题目与解析隔离

- `purpose=question` 禁止 `semantic_role=analysis`，颜色强制归一为黑白。`given|motion` 必须绑定 `diagram_intent` 事实。
- `purpose=solution` 允许分析向量；结构、运动、受力、尺寸分别采用固定色系。
- Role B 将正式结果登记为 `question_image` 或 `solution_image`。练习文件只能消费前者，答案文件可消费后者或复用无分析层的题目图。

## Review 文件

### Calibration review

`calibration_review.json` 必须绑定 proposal/source SHA，由 strong 或教师确认语义锚点；OpenCV 候选不得自动赋予物理含义。

### Reference review

`reference_review.json` 必须：

- 绑定 `report_sha256`，从报告中选择可靠图片 SHA；有两张以上可靠图时至少选择两张。
- 填写非空 `extracted_rules.topology`、`symbols`、`layout`，记录从参考图确认的关系、符号和布局规则。
- 无可靠参考时停止并请求确认；embedding 分数只能排序，不能代替语义批准。

### Semantic review

渲染后复制 `semantic_review.template.json`，由 strong 或教师逐项批准：

- request/spec/render manifest/artifact/geometry report 的 hash 不变；
- `matches-request-purpose`；
- `objects-and-relations-are-physically-correct`；
- `labels-and-symbols-are-unambiguous`；
- 原创图另需 `follows-reviewed-reference-rules`。

自动 `geometry_report.json` 中任何断言失败时，人工 review 不得覆盖。

## 状态、兼容与失败

公开状态保持：`needs_calibration_review`、`needs_reference_review`、`needs_semantic_review`、`complete`、`blocked`。

- request v1、scene spec v2 和低层 v1 继续可读、可渲染草稿；未完成 legacy 任务不能正式发布。
- 已存在且 hash 匹配的 legacy `completion.json` 保持有效并可幂等读取。
- 新正式发布必须是 request v2 + spec v3。
- 源文件变化、CUDA 失败、未知关系、几何断言失败、标签碰撞、review hash 冲突或占用发布目标均失败关闭，不生成 completion 标记。
- `source_complete=false` 时保留覆盖警告，不得据此断言题库没有相似图。

任务状态保存在 `tmp/tasks/<task-id>/physics-diagram/`；阶段输入指纹相同才允许幂等重试和 resume。
