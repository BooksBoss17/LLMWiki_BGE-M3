---
name: physics-question-tutoring
description: 识别文字、图片或 PDF 中的高中物理题并进行分层辅导。用于学生拍题、题意确认、逐级提示、解题思路和规范完整解析；识别不确定时必须停止并请求确认。
---

# 物理拍题与分层辅导

默认使用 `student` 模式。该模式只读教学知识库，只能写入
`output/learning_sessions/<session_id>/`。

跨 agent 默认使用 `weak` 模型档。Codex 学习对话显式使用 `strong`；模型档只改变
识图复核、检索和自检能力，不改变任何权限。

## 线性流程

1. 为本次请求建立不含姓名、学号或考号的 ASCII `session_id`。
2. 运行题目解析入口：

```bash
python skills/learn/physics-question-tutoring/scripts/question_pipeline.py \
  --input <image-or-pdf-or-text> --session-id <id> --stage confirm \
  --model-profile weak --json
```

3. 使用 `strong` 处理图片时，先运行本地 OCR，只把会话目录中的
   `source_sanitized.png` 交给强模型复核。读取
   `references/strong-model-profile.md`，生成固定 schema 的视觉复核 JSON，再用
   `--vision-review-json` 重新运行 confirm。不得把原图或 EXIF 交给模型。
4. 读取会话目录中的 `question_parse.json`。若 `status` 不是
   `confirmed`，只向学生列出 `unconfirmed_items` 并等待确认；禁止补猜文字、
   公式、方向、图中数值或缺失条件。
5. 题意确认后，按需加载 `retrieve/llmwiki-rag-retrieval/SKILL.md`，只通过
   Wiki + RAG 获取物理依据，不回读 `raw/` 全文。
6. 严格按一个阶段回答：
   - `hint`：只给一个可执行提示，不给最终公式链或数值答案。
   - `plan`：给建模对象、物理规律和求解顺序，不代入到最终结果。
   - `solution`：给规范完整解析、验算和易错点。
7. 将当前阶段写成结构化响应，并运行：

```bash
python skills/learn/_shared/scripts/validate_role_d_response.py \
  --contract <stage-contract.json> --response <model-response.json> --json
```

   只有 `status=accepted` 才可向学生输出。数值不一致时只修订一次；第二次失败时
   停止并请求确认。
8. 需要受力图、坐标图、矢量图、轨迹或原图批注时，转入
   `learn/physics-diagram-toolkit/SKILL.md`，不要手写不受校验的 SVG。
9. 将本阶段结构化结果和面向学生的 Markdown 留在当前会话目录；不得写入
   `raw/`、`LLMWiki/`、`skills/`、StudentDataSQL 或 Git 跟踪文件。

## 失败关闭

- OCR/公式/图形对象任一关键项低置信时，停在 `confirm`。
- 图片没有可靠本地 OCR 且没有 strong 视觉候选时，返回 `needs_ocr`。strong 视觉候选只能进入 `needs_confirmation`，不得直接解题。
- `strong` 图片缺少视觉复核、复核 hash 不匹配或 OCR/视觉冲突时，保持阻塞；视觉候选不得自动确认。
- 条件不足、图像裁切、方向不清或选项缺失时，返回 `needs_confirmation`。
- 不得在 `hint` 阶段泄露答案；先运行脚本生成阶段约束，再组织回复。

## 按需参考

- 需要理解解析结果字段或给本地 OCR 适配器提供结果时，读取
  `references/question-parse-schema.md`。
- 使用 `strong` 视觉复核或结构化响应时，读取
  `references/strong-model-profile.md`。

## 完成清单

- `question_parse.json` schema 有效且没有未处理关键疑点。
- 当前 model profile 明确；strong 图片已完成脱敏图复核且 hash 匹配。
- 当前输出只包含用户请求的阶段。
- 结构化响应校验结果为 `accepted`。
- 物理依据来自 Wiki + RAG；没有读取学生数据库或 raw 全文。
- 会话产物只位于 `output/learning_sessions/<session_id>/`。
