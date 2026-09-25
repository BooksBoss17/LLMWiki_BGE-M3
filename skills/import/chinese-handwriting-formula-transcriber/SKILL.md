---
name: chinese-handwriting-formula-transcriber
description: 面向中文手写、手写数学公式、手写物理推导和学生答题卡图像的本地 OCR 转写工作流。当 Codex 需要安装、验证、下载或调用 PaddleOCR-VL、PP-OCRv6、PP-FormulaNet、TexTeller 等本地模型，将中文手写或公式转写为带复核标记的 JSON/Markdown 时使用。
---

# 中文手写与公式转写

对含中文手写和手写公式的学生答题卡图像执行本地优先转写。该工作流依赖多个大型模型，因此始终将环境安装、模型预热和真实图像转写分开执行。

## 必需流程

1. 首先运行 `scripts/doctor.ps1`。
2. 如果缺少环境，运行 `scripts/setup_env.ps1`。
3. 首次真实转写前，使用 `-Scope full` 运行 `scripts/download_models.ps1`。
4. 处理真实学生图像时，创建带 ASCII 任务 ID 的 UTF-8 任务卡，然后使用 `-TaskId <task-id>` 运行 `scripts/transcribe_image.ps1`。
5. 处理题目边界清晰的答题卡时，在整页冒烟测试后，优先使用 `-TaskId <task-id> -Profile local` 运行 `scripts/transcribe_auto.ps1`。该脚本会裁剪题目区域、去除红色批注、将长答题区域拆分为文本行、在本地路由 OCR/公式引擎、拒绝明显幻觉，并写入机器选择的候选结果。
6. 仅在需要更底层的逐题原始 OCR 诊断时，使用 `-TaskId <task-id>` 运行 `scripts/transcribe_regions.ps1`。

不要通过内联 PowerShell 直接传递中文源路径。将真实路径写入 UTF-8 任务卡，只传递 ASCII 任务 ID。

## 隐私规则

不得将真实学生原图、裁剪图、OCR 输出或复核资产复制到本 Skill 目录。真实运行必须写入：

```text
%USERPROFILE%\Desktop\ocr_private_runs\chinese_handwriting_formula_transcriber\<task-id>\
```

合成预热图像和报告保存在 `skills/_shared/model-tools/runtime/benchmarks/` 下；可复用模型和虚拟环境通过模型 ID 从同一共享运行库解析。不得重建 Skill 本地 `.runtime/`、`models/` 或 `.venvs/` 目录。

## 引擎

- `paddle` venv：PaddlePaddle、PaddleOCR、PaddleX、PP-OCRv6、PP-FormulaNet_plus-L、PaddleOCR-VL-1.6。
- `formula` venv：TexTeller。
- `cli` venv：流程编排、任务卡读取、图像元数据和 JSON/Markdown 输出。

每个引擎必须独立失败并写入错误对象，不得中止整个转写流程。

## 输出

转写器写入：

- `combined_result.json`：源文件元数据、引擎状态、原始摘要和警告。
- `summary.md`：人类可读的运行摘要。
- 每个引擎的独立目录：原始 JSON、文本和日志。
- `region_ocr/region_summary.md`：使用 `transcribe_regions.ps1` 时生成的逐题裁剪 OCR 摘要。
- `auto_ocr/auto_transcript.json` 和 `auto_ocr/auto_transcript.md`：完全自动的本地转写，包含 `final_candidate`、`confidence`、`quality_score`、`status`、`risk_flags`、`parsed_fields` 和 `alternatives`。

第一阶段输出用于自动转写，不用于自动评分。不得静默纠正物理内容。低置信度机器选择必须保持 `machine_candidate` 或 `machine_abstain` 标记；只有严格模板证据或文本行级证据才能标记为 `machine_final`。

## 参考文档

仅在修改工作流、环境布局或引擎路由时读取 `references/architecture.md`。
