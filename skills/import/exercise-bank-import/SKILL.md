---
name: exercise-bank-import
description: "Import exercise sheets, homework, exam papers, and lecture questions into the exercise bank. BeMarkdown conversion → question extraction → Question Markdown Standard v1 formatting → quality loop. Use when importing questions from DOCX/PDF exercise materials, including monthly/midterm/mock/college-entrance exam papers that also need a raw exam analysis report."
version: 3.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [exercise-bank, questions, docx, markdown, physics, education]
    related_skills: [BeMarkdown, textbook-import, llm-wiki]
---

# Exercise Bank Import — 题库导入工作流

> **🔒 锁定区** — 本工作流受 SCHEMA.md 权限控制保护。标准权限下必须按步骤执行，不得跳步、不得另写临时批量导入脚本、不得修改流程。用户已明确：宁愿慢，不能出错；原件永久保存；raw → Wiki → RAG 必须同步。

## 0. 核心原则（给弱模型执行）

1. **完整闭环**：原件存档 → 文件类型判断 → BeMarkdown/marker 转换 → LLM 配对 → 生成题目 MD → 三层质检 ≥3 圈 0 issues → 必要时生成试卷分析报告 → Wiki 同步 → RAG 重建 → 清理临时文件。
2. **原件不删**：DOCX/PDF/解析原件永久保存在 `source-library/`；raw 中题目/讲义/标准为可检索副本，不能替代原件。
3. **禁临时批量绕流程**：不得写 `batch_import_exercises.py` 一类脚本绕过逐份导入流程。需要加速时，先讨论并改 skill，不擅自改模式。
4. **标签体系优先**：`raw/exercises/` 只作物理存储；标准分类以 frontmatter `knowledge_points` 为准，必须来自 `skills/taxonomy/exercise-knowledge-tags/SKILL.md` 的标签目录（旧文档可能写作 `skills/题库知识点标签目录.md`）；AI 自主补充写 `ai_extra_tags`。
5. **图片只保留物理信息**：示意图、图像、受力图、电路、实验装置、轨迹、图形选项可保留；选项文字、标题、普通文本、孤立符号、公式截图必须转文本/LaTeX。
6. **无占位符**：不得保留“见解析原件”“见答案表”“题干缺失”等占位。缺内容必须用 DOCX 搜索、PDF 渲染、VLM 或原件补全。

## 1. When to Use

- 从试卷、作业、练习卷、讲义中提取题目入 `raw/exercises/`。
- 源文件为 DOCX/DOC/PDF，目标为每题一个标准 Markdown。
- 导入月考、期中考、高考、模拟考等考试卷，并按用户要求同步生成整卷分析报告。
- 需要同时保留讲义完整 MD，并把例题/变式抽入题库。

Don't use for:
- 教材导入：用 `textbook-import`。
- 课程标准/评价体系/试题分析报告全文导入：用 `standards-import` 或对应维护流程；试题分析报告中的题目是分析对象，默认不抽成练习题。
- B站教学视频：用 `video-transcript-import`。

## 2. Question Markdown Standard v1

每题一个文件，文件名等于 `{ID}.md`；ID 为 `{题型代码}{7位全局序号}`，全局递增，一旦分配不可更改。

```markdown
---
id: MC0000001
question_type: MC
knowledge_points:
  - 力学/圆周运动/向心力
ai_extra_tags:
  - 临界条件
difficulty: 0.50
difficulty_reason: "LLM初判，待人工确认"
source_type: exercise_sheet
source_title: "源文档标题"
source_path: "../source-library/源文件.docx"
source_question_no: "1"
assets:
  - "media/image_0001.png"
review_status: auto
classification_confidence: 0.80
classification_notes: "题型和知识点由LLM初判"
---

# MC0000001

## 题目

题目正文（LaTeX 公式 + 必要图片引用）。

## 答案

A

## 详解

解析正文。
```

题型代码：`MC` 单选，`MA` 多选，`B` 填空，`E` 实验，`C` 计算，`TF` 判断，`DR` 作图，`PR` 证明。

## 3. 主流程（必须逐步完成）

### Step 1 — 原件存档

- 将原卷、解析、答案、PDF/DOCX 全部复制到 `source-library/`。
- 原卷和解析分别命名；同一文件含题目+解析时保留原文件名并记录分割方式。
- 完成标准：原件路径写入每题 `source_path`，原件未删除、未覆盖。

### Step 1.5 — 文件类型判断

| 类型 | 判断标准 | 完整 MD | 题目提取 | 存储 |
|---|---|---|---|---|
| 试卷/作业 | 只有题目、答案、解析 | 不保留完整 MD | 提取到题库 | `raw/exercises/` |
| 考试卷 + 分析报告 | 月考/期中/高考/模拟考等整卷，且用户要求试卷分析 | 不保留完整转换 MD；另生成分析报告 | 提取到题库 + 逐题分析 | `raw/exercises/` + `raw/exam_analysis_reports/` |
| 讲义 | 含知识点讲解、方法点拨、题型分类、典例/变式 | 保留 | 提取题目并留指针 | 完整 MD → `raw/lectures/`；题目 → `raw/exercises/` |
| 试题分析报告 | 含命题思路、考查目标、试题亮点 | 保留 | 默认不提取 | `raw/standards/` |

判断方法：抽查前几页/目录/标题；含系统教学讲解即讲义；含“高考试题分析/评价体系/说明”即标准/报告。

### Step 2 — 转换

**DOCX/DOC**：按 BeMarkdown 完整流程，不手动跳步。DOCX 用 `batch_convert_all.py` 或 `convert_one_docx(docx_path, out_dir, latex_ocr)`；含 WMF/OMML 时必须调用共享 PP-FormulaNet_plus-L 与 TexTeller 双引擎公式链，触发 LibreOffice→高分辨率 PNG→候选共识或视觉复核→LaTeX。

**PDF**：先按 BeMarkdown PDF 分支运行 `diagnose_pdf.py`。若为 `image_pdf`、`mixed_pdf` 或 `summary.formula_risk=true`，必须用 BeMarkdown `combine_image_pdf_ocr.py` 组合模式；marker 只作为候选来源，不得把 marker Markdown 直接送入题目配对。只有 `text_pdf` 且公式可读时才用 pymupdf4llm。组合模式必须读取 `candidate_manifest.json` 和 `final_quality_report.json`：`candidate_manifest.json` 必须含 `model_candidate_matrix` 与 `engine_status`，且 `final_quality_report.json` 必须满足 `ok_for_import=true`、`issue_count=0`、`unresolved_conflict_count=0`、`model_candidate_matrix_present=true`，才能进入题目/答案解析配对；否则按 `../bemarkdown/references/combined-image-pdf-ocr.md` 和 `../bemarkdown/references/local-vlm-ocr-fallback.md` 处理冲突块。试卷和参考答案是两个 PDF 时必须分别转换，再进入题目/答案解析配对。PDF 练习卷常在同一个 MD 中包含题目和答案，用“参考答案/答案和解析”分割。

**DOCX 图片型题干兜底**：若 BeMarkdown 输出几乎全图片、题号很少或为 0，导出 PDF 后按 BeMarkdown PDF 分支用组合 OCR；以组合 OCR 过门禁后的 MD 为主，BeMarkdown 输出补公式/图片。

完成标准：得到可切题 MD；无 base64 data URI；WMF 不直接入题库；转换日志/临时目录可追溯。

### Step 3 — LLM 逐题配对

**上下文纪律**：练习导入需要“全文结构视野”，但不得默认把整份原卷/解析全文塞进同一个长期对话。先用程序或短 worker 建立题号、页码、图片锚点、答案区、解析区的 `manifest`/索引；LLM 只看结构骨架、当前题/当前小节、前后边界窗口、候选答案解析和必要图片说明。只有 OCR 极乱、题号缺失、解析混排等异常情形，才允许在独立短生命周期执行会话中扩大阅读范围；完成后只返回 `paired_questions.json`、质检摘要和交接卡，不能把全文带回总控对话。

把原卷 MD 与解析 MD（或同一 MD 的题目区/答案区）交给 LLM/子 agent 输出 `paired_questions.json`：

```json
[
  {
    "question_no": "1",
    "question_type": "MC",
    "question_text": "题目完整正文",
    "answer": "B",
    "explanation": "详解完整正文"
  }
]
```

配对规则：按内容匹配，不依赖题号；题目边界到下一题前；答案从答案表、`故选X`、`故答案为`、计算题答案块提取；多选题标 `MA`；无解析不得写占位，必须补全或说明原件确无解析。

子 agent 模式：子 agent 只负责配对 JSON；主 agent 负责 ID 分配、MD 生成、图片复制、格式一致性。子 agent 返回 `completed` 不可信，必须检查 JSON 文件存在、非空、`json.load()` 通过；中文引号或 429 静默失败时重试/修复。

完成标准：题目数量与原卷一致；每题有题干、答案、详解；JSON 合法且已保存。

### Step 4 — 图片与公式处理

1. 只扫描 body 中的 `![](media/...)`，不要把 frontmatter `assets:` 当正文图片。
2. 保留图片：物理示意图、坐标/曲线/波形图、受力图、电路图、实验装置图、轨迹图、图形选项、必要实物情境图。
3. 转文本/LaTeX：选项文字、题干/大题标题、普通文本行、孤立标点/数字、公式截图。
4. 错配图门禁：题干含 `如图/图中/图像/曲线/装置/轨迹/电路`，但图片高度很小、很扁或文件极小，必须回原 DOCX/PDF/转换产物找正确图。典型事故：`MA0001332` 需要分子速率分布曲线，却误导入“二、非选择题：”标题图；正确做法是回原件替换真图并复核答案/详解。
5. 透明 PNG/带 alpha 图片门禁：必须检查直接显示和白底合成显示的差异。若图片带 alpha、直接亮度很低但白底合成后可读，视为 `alpha_black_risk`；必须白底合成修复或回原件确认，不得把“黑底不可读”误判为正常物理图。
6. 公式图：alt 或 OCR 可靠时转 `$LaTeX$`；低置信度不得盲替换，回到 `source-library/` 或交给 VLM 复核。
7. 每题 `assets` 必须等于正文实际 `media/...` 引用列表；删除图片引用后同步移除 stale asset。

完成标准：0 断链、0 data URI、0 WMF、0 image open error、0 `alpha_black_risk`、0 未解释 `dark_risk`、0 高优先级文字错图残留、0 assets mismatch。不能只凭“断链为 0”判定图片合格。

### Step 5 — 生成题目 Markdown

- 分配下一批全局 ID，不按题型/目录分段。
- `knowledge_points` 从 `skills/taxonomy/exercise-knowledge-tags/SKILL.md` 的标签目录中选，推荐完整路径，可多标签；实验题同时标实验标签和学科知识标签。
- AI 自主标签写 `ai_extra_tags`，用于方法、情境、易错点、能力维度、教学用途，不参与标准知识点合法性校验。
- `difficulty` 默认 `0.50`，加 `difficulty_reason`。
- `source_*` 溯源字段完整；`review_status: auto`，`classification_confidence` 可写 LLM 判断置信度。
- Markdown 必须兼容当前 RAG 题库 parser：`## 题目`、`## 答案`、`## 详解` 标题后必须各有一个空行；`knowledge_points` 和 `ai_extra_tags` 允许使用合法的 YAML 块列表（缩进或顶格 `- 标签`），但同一字段内应保持一种格式。

完成标准：文件名与 frontmatter `id` 一致；题目/答案/详解三段齐全；图片复制到对应 `media/`；raw 目录不因知识点分类而移动旧题。

### Step 5.1 — RAG parser 兼容门禁

生成题目 MD 后、进入报告和 RAG 重建前，必须对本次新增题目运行当前 RAG parser 兼容检查：

```bash
python skills/import/exercise-bank-import/scripts/validate_exercise_rag_compat.py --kb-root . raw/exercises/<本次目录> --json --out tmp/tasks/<task_id>/exercise_rag_compat_pre_rag.json
```

该门禁只验证“现有 `BGE-M3/scripts/rag_pipeline.py` 能否切出非空题干、答案、详解和 `knowledge_points`”。如果报告出现 `rag_heading_spacing_*`、`empty_rag_*` 或 `rag_knowledge_points_not_parseable`，必须先修题目 Markdown；不得带着空 chunk 风险进入 RAG 重建。不要为了通过此门禁修改 `BGE-M3/scripts/rag_pipeline.py`，除非用户另行授权。

### Step 5.5 — 考试卷报告（按需）

当用户要求对月考、期中考、高考、模拟考等整卷生成分析报告时，必须读取 `references/exam-analysis-report-workflow.md` 并执行该分支。

- 普通 `试卷分析报告` 保存到 `raw/exam_analysis_reports/`，覆盖整张试卷，逐题按“题目 + 解析 + 题目所含知识点 + 题目重点 + 易错点”整理；该报告不导入 Wiki/BGE。
- 普通 `试卷分析报告` 必须复制到 `StudentDataSQL/runtime/exam-reports/<明确考试名>/`，文件名必须写清学校/学年学期/年级学科/考试名称，便于后续学生成绩分析。
- `试卷设计分析报告` 另存到 `raw/lectures/<考试名>试卷设计分析/<考试名>试卷设计分析_设计.md`，按讲义设计文档方式整理命题结构、双向细目表、难度梯度、命题重点、易错设计点和讲评建议；该报告必须同步 Wiki 并进入 BGE。
- 缺解析时必须自行补写；知识点、重点、易错点、设计意图必须结合 Wiki + BGE/RAG 和标签目录判断。
- 报告中的题图优先引用已入题库的 `media/...`，不要重复保存普通文字截图。
- 质检必须覆盖两类报告结构、StudentDataSQL 副本、Wiki 同步和 RAG 中 `试卷设计分析报告` 命中。

### Step 6 — 三层质量检查循环（至少 3 圈，0 issues）

每轮必须检查并修复，直到连续通过；不能只查本次新增导致历史问题被忽略，批量导入时可先按 ID 范围查新增，最后做全量检查。

**层 1：格式**
- frontmatter 完整；ID 唯一且文件名匹配；必备段落存在；图片引用存在；LaTeX `$` 闭合；`$$` display math 需先合并再计数。

**层 2：内容**
- 题干 >50 字符；详解 >20 字符且不是“无”“见解析”“见原件”“见答案”；答案不为空；WMF 引用数为 0；无 base64/data URI；图片语义审查通过。
- 对本次新增题目或全库收尾运行图片审计脚本：

```bash
python skills/import/exercise-bank-import/scripts/audit_exercise_images.py --kb-root . --out tmp/tasks/<task_id>/exercise_image_audit --contact-sheet --json
```

  必须处理 `issues.json` 和 `alpha_black_risk.json`；`alpha_black_risk_count` 与 `semantic_missing_figure_count` 必须为 0。题干明确写“如图所示/图中/图甲/下图/图1”等而正文和 `assets` 都无图片时，`stem_requires_figure_but_assets_empty` 是 hard issue。`dark_risk_count` 必须为 0，或有人工验收记录说明它确实是合法物理图/照片。`suspects.json` 是语义复核队列，不能因为它不是断链错误就跳过。

**层 3：对应关系**
- 题目题号等于 `source_question_no`；详解归属正确；答案与详解中 `故选X` 一致（排序后比较，多选 `AC`=`CA`）；不一致时以详解推导为准修正答案表 OCR 错误。

修复方式：DOCX 搜索关键词、PDF 页面渲染 3x、VLM 补识别、从详解提取答案、计算题答案块移入详解、回原件找缺图。不得留占位。

### Step 7 — 同步 Wiki

每次 raw 更新后必须同步 Wiki，不是可选项。**三库关系门禁**：Wiki 不是 raw 的索引壳；Wiki 知识点枢纽页应自包含地记录知识点概述、教材/讲义/视频 wikilinks、题目 ID 索引和 BGE 检索线索，但不得生成 `[查看](../raw/exercises/...)`、`^[../raw/...]` 或 `## raw 原文` 这类让回答 agent 回 raw 的链接。

- 创建/更新 `LLMWiki/concepts/` 中的知识点枢纽页或标签索引页。
- `题库分类索引.md` 以 frontmatter `knowledge_points` 为准；旧 raw 目录仅作物理存储快照。
- 题目索引写题号、题型、难度、来源、知识点；题目正文由 BGE-M3 从 raw 整理稿 chunks 召回，不从 Wiki 链接回 raw。
- 新增维护/审计/校验报告放 `_meta/`；用户入口/标签目录可留 `concepts/`。
- `concepts/` 页面目标 <15KB，硬上限 20KB；超限按语义拆为总览页+子页面，完整拆分地图放 `_meta/<parent>-split-map.md`。
- 更新 `LLMWiki/index.md` 和 `LLMWiki/log.md`。

完成标准：Wiki 页面自包含且无可见 raw 链接，索引可从 Obsidian 打开，wikilinks 无断链/歧义。

### Step 8 — 重建 RAG

在 `BGE-M3` 下运行，必须清理 Hermes 注入环境：

```bash
cd "<KB_ROOT>/BGE-M3"
env -u PYTHONPATH -u PYTHONHOME runtime/env/Scripts/python.exe scripts/rag_pipeline.py
```

RAG 重建后，必须对本次新增题目再跑一次 chunk 级复核：

```bash
python skills/import/exercise-bank-import/scripts/validate_exercise_rag_compat.py --kb-root . --check-chunks raw/exercises/<本次目录> --json --out tmp/tasks/<task_id>/exercise_rag_compat_post_rag.json
```

完成标准：`chunks.jsonl`、`embeddings.npz`、`sparse_weights.json`、`index.faiss`、`metadata.json` 都生成；`metadata.json` 可能是 dict，读取 chunks 用 `meta.get('chunks', [])`；新增题目对应 `exercise-<ID>` chunk 的 `text` 不是空壳，`knowledge_points` 非空。

### Step 9 — 清理与交付

- 清理 `_tmp_exer_*`、`paired_questions.json`、`gen_*.py`、`runner.py`、子 agent 辅助脚本；原件和 raw 正式文件不能删。
- 汇总新增题数、ID 范围、题型分布、质检轮数、Wiki/RAG 状态、异常修复。
- 若更新了 skill 或整合包，同步 `<PACKAGE_ROOT>\`。

## 4. 分支处理速查

### 4.1 DOCX 公式 / 图片陷阱

- mammoth 可能把图片内联成 `data:image/png;base64,...`。先提取 base64 为图片，再从 alt 提取 LaTeX；不得把 data URI 入库。
- BeMarkdown 只在传入 `latex_ocr` 时处理 WMF→LaTeX。不要用 PIL 直接转 WMF，分辨率极低不可用。
- 讲义解析版常是原卷超集：合并时以解析版为完整讲义，重命名为 `讲义XX-标题.md`，删除旧的原卷/解析 MD 副本但不删原件。

### 4.2 答案格式与配对

- 题号兼容英文/中文句号：`[\.．]`。
- `N.【答案】X` + `【解析】...` 交替格式：按题号配对，不按 block 序号。
- 计算题 `【答案】` 常含完整解题过程而 `【解析】` 写“见答案”；应把答案块放入详解，答案字段写“见详解”。
- 选择题/计算题独立编号时，按 section 边界分段，用 `(section_index, q_num)` 匹配。
- 解析可能混在前题末尾，如 `故答案为：16.`；搜索 `故答案为` / `故选` 反推归属。

### 4.3 讲义导入

- 讲义含“知识点讲解/题型N/典例/变式/链接高考真题”等结构，完整 MD 必须保留到 `raw/lectures/<讲义名>/`。
- 题目挖走后，讲义原位置留指针 `> 题目已入题库：[[ID]]`。
- 讲义设计文档（知识点凝练+选题逻辑+教学策略）可从讲义 MD 提取为 `_设计.md`，`source_type=lesson_design`。
- delegate_task 配对 JSON 常因中文引号或 429 失败，必须 `json.load()` 验证并检查预期题数。

### 4.4 高考真题/试题分析

- 同一 DOCX 常含题目+解析卷尾，先找“答案和解析/参考答案”分割。
- 上海卷等结构特殊，可能只有综合题大 section，每题多小问，题型多为 `C`。
- 教育部考试院《高考试题分析》类材料默认作为标准/报告保留，不抽题入题库，除非用户明确要求。

## 5. 常见事故与硬门禁

1. **短题干/短详解**：题干≤50或详解≤20时，必须回到 `source-library/` 或使用 VLM 补全。
2. **答案=无**：若详解含 `故选X/故答案为`，批量回填；无详解则补详解。
3. **答案与详解不一致**：以详解推导为准修正答案表 OCR。
4. **图片断链为 0 仍可能错**：必须做语义审查；`MA0001332` 是反例。
5. **透明 PNG 可能黑底不可读**：带 alpha 的线图/示意图在部分查看器或 RAG 渲染链路中会显示成黑底黑线。命中 `alpha_black_risk` 时必须白底合成或回原件确认。
6. **frontmatter 正则越界**：抽 `knowledge_points` 时限定字段区间，避免把 `assets` 列表当知识点。
7. **临时脚本污染**：子 agent 生成的 `gen_files.py/runner.py/test_exec.py` 必须清理。
8. **只查新增不够**：阶段性可查新增，收尾必须全量检查，防历史问题或修复副作用。
9. **raw 目录不再表达分类**：不要为分类移动旧题；更新 frontmatter 标签和 Wiki 标签索引。
10. **RAG 环境串包**：重建必须 `env -u PYTHONPATH -u PYTHONHOME`，否则可能 DLL/FlagEmbedding 崩溃。
11. **RAG 空 chunk 隐患**：题目 Markdown 视觉上可读不代表 RAG 能解析；标题后缺空行或 frontmatter 列表不缩进，会导致 `题目：\n答案：\n详解：` 空壳 chunk。必须运行 `validate_exercise_rag_compat.py`。
12. **弱模型执行注意**：每一步必须有真实文件/日志/校验结果，不接受“应该已完成”的口头判断。

## 6. Verification Checklist

- [ ] 原件已存档，路径写入 `source_path`。
- [ ] 文件类型已判断：试卷/讲义/试题分析。
- [ ] BeMarkdown 转换完成；图片型/公式风险 PDF 的 `candidate_manifest.json` 含 `model_candidate_matrix`/`engine_status`，`final_quality_report.json` 为 `ok_for_import=true`、`issue_count=0`、`unresolved_conflict_count=0`、`model_candidate_matrix_present=true`；无 data URI、无 WMF 入库。
- [ ] 配对 JSON 合法、题数一致、无空题干/空答案/空详解。
- [ ] 每题一个 MD；ID 全局唯一递增；文件名=ID。
- [ ] frontmatter 完整：`id/question_type/knowledge_points/difficulty/source_*/review_*`。
- [ ] 题目 MD 已通过 `validate_exercise_rag_compat.py` 预检：题干/答案/详解/knowledge_points 都能被当前 RAG parser 解析。
- [ ] `knowledge_points` 来自标签目录；AI 标签写 `ai_extra_tags`。
- [ ] 新导入题目已通过 Role D 标签流程同步 `knowledge_point_ids`；中文 `knowledge_points` 继续保留。
- [ ] `## 题目`、`## 答案`、`## 详解` 三段齐全。
- [ ] 已运行 `audit_exercise_images.py`；图片 body 引用 0 断链、0 data URI、0 WMF、0 image open error、0 `alpha_black_risk`、0 `stem_requires_figure_but_assets_empty`；`dark_risk=0` 或有人工验收记录；异常小/扁图已回原件核对。
- [ ] `assets` 与正文实际 `media/...` 引用完全一致。
- [ ] LaTeX `$` 闭合；无占位符；无“答案=无”残留。
- [ ] 题号、答案、详解对应关系检查通过。
- [ ] 质量检查至少 3 圈，最终 0 issues。
- [ ] 讲义完整 MD 已保留到 `raw/lectures/`，题目位置留指针（如适用）。
- [ ] Wiki 已同步：索引页/枢纽页/index/log 更新，维护页放 `_meta/`。
- [ ] `concepts/` 无 >20KB 页面；wikilinks 无断链/歧义（如本次影响 Wiki）。
- [ ] RAG 已重建并核对 chunks/vectors。
- [ ] RAG 重建后已通过 `validate_exercise_rag_compat.py --check-chunks`：新增题 `exercise-<ID>` chunk 文本非空、knowledge_points 非空。
- [ ] 临时目录和子 agent 辅助脚本已清理。

## 7. References（按需查看，不替代主流程）

- `references/image-semantic-audit.md` — 图片语义审查、`MA0001332` 事故、0 issues 校验。
- `references/tag-taxonomy-transition.md` — `knowledge_points` / `ai_extra_tags` 标签体系。
- `references/wmf-to-latex-reconvert.md` — WMF→LaTeX 正确修复流程。
- `references/docx-image-only-ocr-fallback.md` — DOCX 图片型题干 OCR 兜底。
- `references/exam-paper-answer-formats.md` — 高考真题答案格式与配对策略。
- `references/exam-analysis-report-workflow.md` — 月考/期中考/高考/模拟考等考试卷导入时同步生成 raw 试卷分析报告。
- `references/lecture-import-workflow.md` — 讲义导入 delegate_task 配对流程。
- `references/json-chinese-quote-fix.md` — 子 agent JSON 中文引号修复。
- `references/batch-lecture-import-pattern.md` — 批量讲义导入模式。
- `references/lecture-design-extraction.md` — 讲义设计文档提取。
- `references/exercise-classification-maintenance.md` — 旧 raw 目录分类维护，仅历史参考。
- `references/recent-exercise-import-fallbacks.md` — 混合 DOC/DOCX/PDF 导入兜底。
- `scripts/validate_exercise_rag_compat.py` — 检查题目 Markdown 与当前 RAG parser 的兼容性，防止空壳 exercise chunk。
- `scripts/audit_exercise_images.py` — 检查题库图片断链、assets/body mismatch、明确指图但零资产、透明 PNG 黑底风险、暗图风险和语义复核候选。
- `../bemarkdown/references/image-pdf-ocr-workflow.md` — 图片型/扫描/混合 PDF 试卷和答案转换入口。
- `../bemarkdown/references/combined-image-pdf-ocr.md` — 图片型 PDF 多引擎候选、块级融合、VLM 冲突复核和入库门禁。
- `../bemarkdown/references/local-vlm-ocr-fallback.md` — marker 转换质量不足时的 PaddleOCR-VL/PP-OCRv6/PP-FormulaNet/TexTeller 本地 OCR/VLM 兜底。
- `scripts/kb_maintenance_check.py` — 日常维护检查脚本。

## 8. Role D 强制后置阶段

题目三轮质量门禁后，按顺序执行：

1. 答案/详解存在风险时先用 `learn/exercise-solution-curation` 生成 proposal；只有教师授权才应用。
2. 用 `taxonomy/exercise-knowledge-tags` 为新增题目写稳定 `knowledge_point_ids`，同时保留中文 `knowledge_points`。
3. 完成 Wiki 同步后，以 `source_skill=exercise-bank-import` 生成 `import_handoff.json`。
4. 运行图谱 dry-run；安全新增自动 apply，破坏性项输出 `approval_required`。
5. 用 handoff verify 校验 completion-ready 结果，然后才执行最终 RAG 重建和 `--check-chunks`。

```bash
python skills/_shared/scripts/import_handoff.py create --spec <handoff-spec.json> --json
python skills/learn/physics-knowledge-graph/scripts/update_graph.py --handoff <import_handoff.json> --dry-run --json
python skills/learn/physics-knowledge-graph/scripts/update_graph.py --handoff <import_handoff.json> --apply --result-out <skills/_ops/runtime/state/.../graph_update_result.json> --json
python skills/_shared/scripts/import_handoff.py verify --handoff <import_handoff.json> --graph-result <graph_update_result.json> --json
```

本节顺序覆盖前文 checklist 中“先重建 RAG”的旧顺序。没有成功图谱结果时不得声明题库导入完成。
