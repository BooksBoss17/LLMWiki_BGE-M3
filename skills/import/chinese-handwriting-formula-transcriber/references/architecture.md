# 手写与公式转写架构

本 Skill 是面向学生答题卡图像的本地优先 OCR 工作台。它将环境安装、模型预热和真实图像转写分开，使 Agent 能够在不重新运行所有模型的情况下诊断失败。

## 运行时布局

所有可复用环境和权重均从 `skills/_shared/model-tools/` 解析：

- `runtime/envs/cli-py311`：流程编排、任务卡读取、图像元数据和 JSON/Markdown 导出。
- `runtime/envs/paddle-py311`: PaddlePaddle, PaddleOCR, PaddleX, PP-OCRv6, PP-FormulaNet_plus-L, PaddleOCR-VL-1.6.
- `runtime/envs/formula-py311`：TexTeller、ONNX Runtime 和公式专用依赖。
- `runtime/models/`：Hugging Face、Paddle/PaddleX、CnOCR、CnSTD 和 faster-whisper 的共享缓存根目录。
- 共享 `skills/_shared/model-tools/runtime/benchmarks/`：合成冒烟测试输入、安装日志和不含私有内容的摘要。

`LLMWIKI_MODEL_RUNTIME_ROOT` 可覆盖默认共享运行库根目录。模型 ID 和质量优先默认值由共享模型运行库中的 `registry.yaml` 和 `profiles.yaml` 定义。

不要在此处保存真实学生图像和输出。使用桌面上的私有运行根目录。

## 模型职责

- PaddleOCR-VL-1.6：对文本、公式、表格和页面结构执行整页解析冒烟测试。
- PP-OCRv6：执行文本检测和文本识别预热；用于中文手写/文本行 OCR 检查。
- PP-FormulaNet_plus-L：执行 Paddle 公式识别并输出 LaTeX。
- TexTeller：执行手写和混合公式识别冒烟测试。

## 任务卡

在以下位置创建 UTF-8 JSON 任务卡：

```text
%USERPROFILE%\Desktop\ocr_private_runs\chinese_handwriting_formula_transcriber\tasks\<task-id>.json
```

最小结构：

```json
{
  "task_id": "midterm_lzq_20260707",
  "source_image": "C:/absolute/path/to/image.png"
}
```

可选字段：

```json
{
  "output_root": "%USERPROFILE%/Desktop/ocr_private_runs/chinese_handwriting_formula_transcriber/midterm_lzq_20260707",
  "notes": "私有本地冒烟测试"
}
```

只向 PowerShell 传递 ASCII `task_id`。
