---
name: textbook-import
description: "Import textbook PDF/DOCX into LLMWiki knowledge base: BeMarkdown conversion → chapter splitting → heading normalization → image classification → OCR cleanup → quality loop. Use when importing textbooks or structured reference books into the wiki."
version: 1.4.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [textbook, import, wiki, markdown, ocr, marker-pdf]
    related_skills: [BeMarkdown, llm-wiki]
---

# Textbook Import — 教材导入工作流

> **🔒 锁定区** — 标准权限下严格按步骤执行，不得跳步、不得另写临时脚本、不得改流程。教材导入低频但要求高：agent 负责全量核对标题、OCR、图片、公式、Wiki/RAG，不留占位符。

## 0. 核心原则

1. **完整闭环**：原件存档 → BeMarkdown 统一诊断/转换 → 按章拆分 → 标题标准化 → 图片分类 → OCR 乱码清理 → 质量循环 0 issues → Wiki 结构化同步 → RAG 重建 → 清理临时。
2. **原件不删**：PDF/DOCX 原件永久保存在 `source-library/`；raw/textbooks 是转换副本。
3. **不留 OCR 占位符**：乱码段落必须用 PDF 页面渲染 + VLM 或 pymupdf 文本层修复，不能写 `<!-- OCR乱码 -->`。
4. **图片只保留内容图**：物理示意图、实验照片、图表保留；装饰 logo、标题横幅、栏目图标、公式/题号截图转文字或删除。
5. **Wiki/RAG 必须同步**：raw 更新后同步 Wiki 索引/章/节页，并重建 BGE-M3 RAG。
6. **上下文预算**：Wiki `concepts/` 页面目标 <15KB，硬上限 20KB；超限按语义拆为总览页+子页面。

## 1. When to Use

- 导入教材、参考书、结构化教学书籍。
- 源文件为 PDF 或 DOCX，转换统一委托 BeMarkdown。
- 目标是生成按章拆分、标题层级规范、图片干净、可同步 Wiki/RAG 的 Markdown。

Don't use for：论文/文章（llm-wiki ingest）、视频（video-transcript-import）、课程标准/评价体系（standards-import）、题目入库（exercise-bank-import）。

## 2. 前置条件

- BeMarkdown 已可用；PDF 先诊断，DOCX 按 OMML/EQ/WMF/PNG-alt 决策树处理。
- marker CLI、CUDA 和 ASCII staging 由 BeMarkdown wrapper 检查；教材 skill 不直接拼 marker 命令。
- 知识库结构：`source-library/` + `raw/textbooks/<书名>/` + `raw/textbooks/<书名>/media/`。
- 临时转换目录统一为 `tmp/tasks/codex/<ascii_task_id>/`，不得写入 `LLMWiki/_tmp_*`。

## 3. 主流程

### Step 1 — 原件存档

复制源文件到 `<KB_ROOT>/source-library/`，核对文件大小一致。

完成标准：原件存在、大小一致、不会被后续清理删除。

### Step 2 — 转换为临时 Markdown

**PDF → BeMarkdown**：先诊断，再按报告选择 clean text 或组合 OCR 路径。

```bash
python skills/import/bemarkdown/scripts/diagnose_pdf.py <book.pdf> --json
python skills/import/bemarkdown/scripts/combine_image_pdf_ocr.py <book.pdf> <task_output_dir> --json
```

`text_pdf` 且公式完整时可按 BeMarkdown 使用 pymupdf4llm；`image_pdf`、`mixed_pdf` 或 `formula_risk=true` 必须用组合 OCR。marker 只通过 `convert_pdf_with_marker.py` 作为候选运行，不直接入库。

**DOCX → BeMarkdown**：按 BeMarkdown skill 完整流程；含 OMML/EQ/WMF 公式时不得跳过 WMF→PNG→结构化公式候选→按引擎清理→视觉复核→LaTeX。`machine_candidate`/`machine_abstain` 不得直接回填。

并行策略：多本教材时，只有当前 agent 确认具备可靠后台任务或 subagent 能力，才可在处理前一本拆分/清理时启动下一本转换；具体调度参数由当前 agent/runtime 决定，不写入共享流程。没有并行能力时，先把书目、阶段、输入输出和状态写入任务计划，再逐本串行执行并落盘更新状态。

完成标准：临时 MD、图片、转换日志存在；记录总行数、图片数、耗时；无 data URI/WMF 直接入库。

### Step 3 — 建立 raw 目录

```text
raw/textbooks/<书名>/
├── 00-封面与目录.md
├── 01-绪论.md
├── 02-第N章-章名.md
└── media/
```

完成标准：章节文件和 `media/` 目录准备好。

### Step 4 — 移动图片并按章拆分

1. 图片全部移入 `raw/textbooks/<书名>/media/`。
2. 扫描临时 MD，定位 `第N章` 起始行，按行号区间拆分。
3. 每个文件添加 frontmatter：`title/source/created/type/tags`。
4. 图片路径统一改为 `media/<图片名>`。

完成标准：每章一个 MD；封面/目录/后记单独文件；frontmatter 完整；图片路径 0 断链。

### Step 5 — 标题层级标准化

目标层级：H1=书名（每文件 1 个），H2=章，H3=节/栏目，H4=节内小标题/例题/解/节练习；无 H5，无跳级。

修正规则：
- `# 第N章`/`#### 第N章` → `## 第N章 章名`；只有章号时补章名。
- `# 第N节`/`## 第N节` → `### 第N节`。
- 非书名 H1 → H3；TOC 页的章条目 H2 → H3。
- H5 → H4；`## 1.小标题` → H4。
- 删除装饰性大字乱码 H1，如 `猫弦/猪论/直缆/道金额/半频速频度槽`。
- 图表标题 `图 N-X`、`表 N-X` 不作标题，改为粗体文本。

完成标准：无 H5、无 H2→H4/H3→H5 跳级、无非书名 H1、重复章标题 H2 每文件≤2。

### Step 6 — 图片分类处理

用尺寸/文件大小做初筛，VLM 抽查确认，不能只靠大小删除内容图。

**删除装饰图**：Physics logo、蓝色地球图标、红灯泡、蓝色量角器、3D柱状图、素养侧栏图标、三棱镜色散装饰、点状虚线、绿色返回箭头、灰色插头、纯排版素材。

**转文字/LaTeX**：章标题横幅→H2，节标题横幅→H3，栏目 logo→H4，题号截图→`**第N题**`，公式截图→`$LaTeX$`。

**保留内容图**：物理示意图、受力图、运动图像、实验照片、数据表、真实教材插图。

完成标准：装饰图引用清除，文字/公式图已转文本，保留图片均为内容图，0 断链。

### Step 7 — OCR 乱码清理

历史乱码词表只能作为检测提示，不能作为跨教材静默替换规则。发现乱码、跨页断词、重复段落或未知章名时，必须回到源页，读取 BeMarkdown structured candidates，并按需让 agent VLM 复核。

只有经过源页核验的 replacement/章名 JSON 才能写回。不得删除无法解释的段落，不得写 `<!-- OCR乱码 -->`、`待人工校对` 等占位符。

完成标准：无乱码标题、无重复乱码段落、无 OCR 占位符、无装饰符号残留。

### Step 8 — 质量检查循环

使用 `scripts/check_and_fix.py` 检查。默认只报告；`--fix-safe` 只做空标题、过深标题、标题加粗和空白等格式修复，不改语义内容：

```bash
python skills/import/textbook-import/scripts/check_and_fix.py <textbook_dir> --repair-tasks <task_dir>/repair_tasks.json --json
```

若有源页已核验的替换和章名，显式传入并授权写回：

```bash
python skills/import/textbook-import/scripts/check_and_fix.py <textbook_dir> --fix-safe --reviewed-replacements <task_dir>/reviewed_replacements.json --chapter-names <task_dir>/chapter_names.json --apply-reviewed --repair-tasks <task_dir>/repair_tasks.json --json
```

检查项：OCR 乱码、未闭合 `$`、残留 Physics、图表标题层级、标题 `---/**/装饰符`、OCR 占位符、空标题、重复乱码、图片断链、标题跳级、重复章标题 H2、非书名 H1、TOC 页 H2、`#### 第N章` 错层级。

完成标准：repair tasks 全部由源页证据解决；最终脚本退出码为 0、`issue_count=0`。

### Step 9 — 收尾 raw

删除临时转换目录；统计文件数、行数、图片数、标题层级分布；更新 `LLMWiki/log.md`。

完成标准：临时目录清理，raw/textbooks 可直接浏览。

### Step 10 — 同步 Wiki

**三库关系门禁**：Wiki 不是 raw 的索引壳。同步 Wiki 时必须把 raw 中整理好的 MD 内容导入 Wiki，或在 Wiki 中二次加工为自包含页面；不得生成 `^[../raw/...]` transclusion、`## raw 原文`、`[查看](../raw/...)` 这类让回答 agent 回 raw 的链接。

每本教材创建/更新：

1. **书索引页** `concepts/<slug>.md`：书总结 + 章节目录 + 关键学习线索。
2. **章索引页** `concepts/<slug>-第N章-章名.md`：章总结 + 节目录 + 本章核心概念表；只用 Wiki 内部 `[[wikilinks]]` 指向节页。
3. **节摘要页** `concepts/<slug>-第N章-第M节-节名.md`：节摘要、关键定义/公式/定律、例题及答案、课本提问、练习概括。内容来自 raw 中整理好的 MD，但直接写入 Wiki 或加工后写入 Wiki。

摘要原则：叙述性段落压缩；关键定义/公式/定律、例题及答案、课本提问保留原句；章末练习/节练习只概括不保留题目；图片若需在 Wiki 使用，复制到 `LLMWiki/assets/` 并改为 Wiki 内部资产路径。frontmatter `sources` 可保留 raw 路径作溯源元数据，但正文不得有可见 raw 链接。

完成标准：书→章→节三级页面齐全，`index.md` 更新，Wiki 正文无 raw transclusion/可见 raw markdown 链接，wikilinks 无断链/歧义；`concepts/` 页面无 >20KB。

### Step 11 — 同步 RAG

```bash
cd "<KB_ROOT>/BGE-M3"
env -u PYTHONPATH -u PYTHONHOME runtime/env/Scripts/python.exe scripts/rag_pipeline.py
```

脚本扫描 raw/textbooks、standards、exercises、lectures 等，按类型切片并构建 BGE-M3 dense+sparse+FAISS。

完成标准：输出“RAG 入库完成”；`chunks.jsonl/embeddings.npz/sparse_weights.json/index.faiss/metadata.json` 存在，chunks 数与 raw/Wiki 更新相符。

## 4. 常见事故与硬门禁

1. **不要直接调用 marker**：统一走 BeMarkdown wrapper，让当前 CLI、CUDA、ASCII staging 和输出门禁只维护一份。
2. **CUDA 必查**：CPU 模式极慢；先确认 `torch.cuda.is_available()`。
3. **VLM 不可全省**：装饰图/内容图必须抽样 VLM，严重 OCR 乱码必须 VLM 或文本层修复。
4. **图片路径必须改 `media/`**：否则 Obsidian 不显示。
5. **标题修复要覆盖新增类型**：非书名 H1、TOC H2、`#### 第N章` 是常见漏修。
6. **章名不能内置猜测**：从目录页或源 PDF 核验后，以 `--chapter-names` JSON 传入。
7. **VLM 页码偏移**：marker `_page_N_` 是物理页码，pymupdf 用 `doc[N]`；印刷页码可能差 4–5 页。
8. **VLM/API 超时**：先跳过，处理其他页，最后重试；反复超时用 `pymupdf.page.get_text()`。
9. **重复乱码不能直接删**：先回原页识别，删除会丢教材内容。
10. **教材导入低频高要求**：必须全量核对，不留待人工占位。

## 5. Verification Checklist

- [ ] 原件已存档，大小一致。
- [ ] marker/BeMarkdown 转换完成，临时 MD + 图片存在。
- [ ] raw/textbooks/<书名>/ 每章 MD + media 目录齐全。
- [ ] frontmatter 完整，图片路径为 `media/...` 且 0 断链。
- [ ] 标题层级：H1=书名，H2=章，H3=节/栏目，H4=小标题，无 H5/跳级/非书名 H1。
- [ ] 装饰图标已删除，文字/公式图片已转文本/LaTeX，内容图保留。
- [ ] 无 OCR 乱码标题、重复乱码段落、装饰符残留、OCR 占位符。
- [ ] LaTeX `$` 闭合。
- [ ] 质量检查循环 0 issues。
- [ ] 临时转换目录已清理，log.md 已更新。
- [ ] Wiki 书/章/节页面已创建或更新，index.md 更新，wikilinks 0 断链。
- [ ] `concepts/` 页面无 >20KB。
- [ ] RAG 已重建并核对输出。

## 6. References（按需查看，不替代主流程）

- `references/wiki-restructuring-format.md` — Wiki 书/章/节重构格式。
- `scripts/check_and_fix.py` — 默认只检测；`--fix-safe` 仅修格式，语义/OCR 内容必须通过 source-reviewed replacement 和 repair task 显式解决。

## 7. Role D 强制图谱后置阶段

前述 Wiki 同步和三轮质量门禁通过后，先生成 schema v1 handoff spec，再运行：

```bash
python skills/_shared/scripts/import_handoff.py create --spec <handoff-spec.json> --json
python skills/learn/physics-knowledge-graph/scripts/update_graph.py --handoff <import_handoff.json> --dry-run --json
python skills/learn/physics-knowledge-graph/scripts/update_graph.py --handoff <import_handoff.json> --apply --result-out <skills/_ops/runtime/state/.../graph_update_result.json> --json
python skills/_shared/scripts/import_handoff.py verify --handoff <import_handoff.json> --graph-result <graph_update_result.json> --json
```

`source_skill` 固定为 `textbook-import`。handoff 必须记录 changed raw/Wiki files、kp ids、质量报告和 hash。若 dry-run 为 `no_change`，可直接把该结果用于 verify；若有安全变更必须 apply。破坏性项保留 `approval_required`，不得自行授权。只有 verify 允许后才执行本 skill 的最终 RAG 重建；缺少 completion-ready 图谱结果时不得声明教材导入完成。
