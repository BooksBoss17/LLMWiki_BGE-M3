---
name: physics-diagram-toolkit
description: 本地确定性生成和批注高中物理示意图，并以自动几何断言和 hash 复核阻断物理错误。用于题目黑白情境图、答案彩色受力/运动分析图、原图批注、原创力学场景、轨迹、函数图、坐标图和实验数据图；原创图先检索并复核 Qwen3-VL 题库参考图。
---

# 物理示意图生成

保留原题原图。只有原图批注，或原创/改编后原图不再适配时才进入本流程；缺失、错配或语义错误的题库原图回退 Role A 修复。

## 选择用途和路径

- 题目图：`mode=original`、`purpose=question`、`style_profile=exam-monochrome`。只画题干明确给出的对象、尺寸、速度或力；禁止推导得到的完整受力分析。
- 解析图：`mode=original`、`purpose=solution`、`style_profile=solution-color`。可画受力、运动学和辅助关系。
- 原图批注：`mode=annotate`、`purpose=annotation`、`renderer=scene`。先校准坐标和语义锚点。
- 函数或实验数据图：`renderer=plot`。用数据或受限表达式绘制，不让 scene renderer 猜数值。

正式任务使用 request v2 和 spec v3。读取：

- `references/pipeline-contract.md`：request、review、状态、题目/解析隔离和 legacy 发布规则。
- `references/diagram-spec.md`：scene units、对象、关系、尺寸、轨迹和 plot schema。

## 运行

准备 ASCII task ID 和 UTF-8 request 后，始终经共享 runtime 调用：

```powershell
python skills/_shared/model-tools/scripts/model_runtime.py run --id physics-diagram-runtime --script skills/learn/physics-diagram-toolkit/scripts/diagram_pipeline.py -- prepare --request <diagram_request.json> --task-id <ascii-id> --json
```

完成 `calibration_review.json` 或 `reference_review.json`，再渲染 spec v3：

```powershell
python skills/_shared/model-tools/scripts/model_runtime.py run --id physics-diagram-runtime --script skills/learn/physics-diagram-toolkit/scripts/diagram_pipeline.py -- render --task-id <ascii-id> --spec <diagram_spec.json> --json
```

同时查看 `diagram.png`、`geometry_report.json` 和 `review_sheet.png`。完成 template 中全部逐项语义断言后发布：

```powershell
python skills/_shared/model-tools/scripts/model_runtime.py run --id physics-diagram-runtime --script skills/learn/physics-diagram-toolkit/scripts/diagram_pipeline.py -- verify --task-id <ascii-id> --review <semantic_review.json> --json
```

中断恢复：

```powershell
python skills/_shared/model-tools/scripts/model_runtime.py run --id physics-diagram-runtime --script skills/learn/physics-diagram-toolkit/scripts/diagram_pipeline.py -- resume --task-id <ascii-id> --json
```

## 强制门禁

1. request v2 的 `diagram_intent.facts` 是题目图允许显示量的权威来源。question 中 `given|motion` 必须绑定 `fact_id`，`analysis` 一律拒绝。
2. 原创 scene 使用各向同性 `scene_units`；角度、共点、接触、相切、平行、垂直、尺寸端点、平抛切线和单调性全部进入 `geometry_report.json`。
3. 任何自动断言、标签边界/碰撞或 render validation 失败都进入 `blocked`；strong 或教师 review 不能覆盖。
4. 原创图的 reference review 必须绑定图片 SHA，并填写从参考图确认的 topology、symbols、layout；只有图片列表没有制图规则时不得渲染正式图。
5. Qwen3-VL-Embedding-2B 只在 CUDA 环境检索；中文路径通过 Pillow 对象进入模型，禁止 CPU 静默降级或 `file://` 拼接。
6. weak 只能生成草稿；正式交付要求 strong 或教师批准全部 hash 绑定语义断言。
7. request v1/spec v2 只能读取或生成 legacy 草稿；新发布必须 request v2 + spec v3。已完成的旧 completion 保持可读。
8. 原图只读，临时网格不进入正式图片；源文件变化、未知 primitive/关系、非有限数据、路径逃逸和发布冲突均失败关闭。

## Role B 与题库写回

- Role B 为每张新图显式登记 `question_image` 或 `solution_image`。练习文件只引用题目图；答案文件可引用解析图或复用无分析层题目图。
- 图片发布到 `output/assets_<name>/<task-id>/`；原题继续复用原图字节。
- student 模式只写 `output/learning_sessions/<session-id>/`。
- 题库写回仍走 curator proposal、dry-run、`--teacher-authorized`、源/目标 hash 和 canonical writer；不得借绘图修改正式知识点 ID。

## 完成证据

- task 状态为 `complete`，目录含 hash 绑定 `completion.json`。
- scene 含 PNG、SVG、render report、geometry report 和 review sheet；plot 另含 PDF。
- `geometry_report.ok=true` 且无 failed assertions；semantic review 的全部固定断言为 approved。
- reference review 含可靠图片和制图规则；`source_complete=false` 警告得到保留。
