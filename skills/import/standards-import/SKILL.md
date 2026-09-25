---
name: standards-import
description: "Import curriculum standards and exam evaluation framework documents into the wiki. Route every PDF through BeMarkdown diagnosis, use pymupdf only for verified clean text PDFs, and use combined OCR for scanned, mixed, or formula-risk PDFs. Use when importing policy, standard, exam-evaluation, or regulatory documents."
version: 1.1.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [standards, curriculum, exam, pdf, markdown, wiki]
    related_skills: [textbook-import, BeMarkdown, llm-wiki]
---

# Standards Import — 标准文档导入工作流

> **🔒 锁定区** — 本工作流步骤受权限控制保护。标准权限下严格按步骤执行，不得跳步、不得另写临时脚本、不得修改步骤内容。如需批量处理或修改流程，需用户单次允许或管理员权限。详见 SCHEMA.md 权限控制规则。

将课程标准、高考评价体系等政策/标准类文档导入 LLMWiki 知识库。

## When to Use

- 用户要求导入课程标准、考试评价体系、教学大纲等政策文件
- 用户要求导入高考试题分析报告（如教育部考试院《高考试题分析》）——这类文件含命题思路、考查目标、试题亮点，本质是教学评研究文档
- 源文件是文本型 PDF（有文本层）或扫描型 PDF（无文本层）
- 目标是生成整体一个 MD 文件，条款编号转为标题层级

Don't use for:
- 教材导入（用 `textbook-import` skill）
- 题目提取入库（用 `exercise-bank-import` skill）

## 前置条件

- `pymupdf` 已安装（文本型 PDF 提取）
- BeMarkdown 可用；marker、本地 OCR 模型和 CUDA 由 BeMarkdown wrapper 检查
- 知识库目录：`source-library/` + `raw/standards/` + `raw/standards/media/`
- 临时转换目录：`tmp/tasks/codex/<ascii_task_id>/`，不得写入 `LLMWiki/_tmp_*`

## 文件类型判断

所有 PDF 必须先调用 BeMarkdown 全页诊断，不得只抽前 5 页按“有字/无字”二分：

```bash
python skills/import/bemarkdown/scripts/diagnose_pdf.py <file.pdf> --json
```

- `text_pdf` 且 `formula_risk=false`：允许用 pymupdf 纯文本提取并手动构建标题。
- `image_pdf`、`mixed_pdf` 或 `formula_risk=true`：调用 `combine_image_pdf_ocr.py`，marker 仅作候选。
- 组合 OCR 必须读取 `final_quality_report.json`；只有 0 issues、0 unresolved conflicts 才能进入 raw。

## 工作流

### 文本型 PDF（pymupdf 提取）

```python
import pymupdf
doc = pymupdf.open(path)
all_lines = []
for page in doc:
    for line in page.get_text().split('\n'):
        all_lines.append(line.rstrip())
doc.close()
```

### 扫描、混合或公式风险 PDF（BeMarkdown 组合 OCR）

```bash
python skills/import/bemarkdown/scripts/combine_image_pdf_ocr.py <file.pdf> <task_output_dir> --json
```

长文档按页码 wave 运行；疑难块由 agent VLM 复核并回灌。不要直接调用 marker CLI，不要把 marker 单模型输出当最终稿。当前 marker 参数、CUDA 和 ASCII staging 统一由 `convert_pdf_with_marker.py` 处理。

### 标题层级标准

| 层级 | 用途 | 匹配模式 |
|------|------|---------|
| H1 `#` | 文档名（每文件 1 个） | frontmatter 后手动添加 |
| H2 `##` | 大章节 | `^[一二三四五六七八九十]+、` 或 `^\d+\s+` (短标题) |
| H3 `###` | 条目 | `^（[一二三四五六七八九十]+）` 或 `^\d+\.\d+\s+` |
| H4 `####` | 子条目 | `^\d+\.\s+` (短内容) 或 `^【.+】` |

### 清理规则

1. 删除页眉重复行（如"普通高中物理课程标准（2017年版2020年修订）"每页出现）
2. 删除纯页码行（`^\d{1,3}$`）
3. 删除目录点线行（`...` 或 `···`）
4. 期刊页眉行删除（如"2019年第12期"）
5. `pymupdf4llm` 对中文文档误加粗体 → 后处理 `re.sub(r'\*\*(.+?)\*\*', r'\1', md)`
6. 学术论文编号和标题可能分行 → 合并跨行编号标题

### 关键陷阱：pymupdf4llm vs pymupdf

**不要用 pymupdf4llm 处理中文标准文档** — 它会把短行误判为标题，生成数百个 H2。用 `pymupdf.page.get_text()` 纯文本提取，然后手动用正则构建标题层级。

### 学术论文特殊处理

学术论文的章节编号（如"1"、"2.1"）和标题文字可能分在两行：
```
2
物理科考试的功能定位
```
需要合并：检测纯数字行后跟短标题行，合并为 `"2 物理科考试的功能定位"` 再转 H2。

### frontmatter 模板

```yaml
---
title: <文档标题>
source: ../source-library/<文件名>
created: YYYY-MM-DD
type: summary
tags: [model, standard, <领域>]
---

# <文档标题>
```

## 质量检查

同 textbook-import 的质量循环，但检查项更少：
1. 非文档名 H1 → 降为 H3
2. 标题层级跳级（H2→H4）
3. 未闭合的 `$`
4. 断链图片引用
5. 重复乱码文本

## Step 6: 将 raw 更新同步到 Wiki 库

**每次 raw 库完成导入（新增/调整标准文档）后，必须执行此步骤，将 raw 中的变更同步到 wiki 库。** 不是可选步骤——raw 库和 wiki 库必须保持一致。

### 6.1 创建 wiki 页

**三库关系门禁**：Wiki 不是 raw 的索引壳。标准/评价体系 Wiki 页必须把 raw 中整理好的正文和结构化总结写入 Wiki；不得生成 `^[../raw/...]` transclusion、`## raw 原文` 或可见 `[查看](../raw/...)` 链接。frontmatter `sources` 可保留 raw 路径作溯源元数据，但回答 agent 不回 raw。

每份标准文档创建一个 wiki 页 `concepts/<slug>.md`：

1. 添加 wiki frontmatter（title, created, updated, type, tags, sources）
2. 将 raw 中已整理的正文导入 Wiki 正文，并添加文档总结/章总结
3. 若正文图片需在 Wiki 使用，复制到 `LLMWiki/assets/standards/...`，并改为 Wiki 内部资产路径
4. 添加 `[[wikilinks]]` 互链到相关标准文档
5. 页面超过 20KB 时按结构/语义拆分为总览页 + Wiki 子页面，不用 raw transclusion 规避体积限制

### 6.2 添加结构化总结

**不拆分文件**（标准文档是条款式，不需要拆成独立节页），在原文件内添加：

1. 文件顶部 frontmatter 后、H1 标题后添加 `## 文档总结`（2-3 句话概括全文定位和核心内容）
2. 每个 H2 章节标题下方添加 `**章总结：**`（1-2 句话概括该章内容）
3. 保留原始正文内容不删除

格式：
```markdown
# <文档标题>

## 文档总结
（2-3句话概括全文定位）

## 一、章节标题

**章总结：**（1-2句话概括本章）

[保留原始正文...]
```

### 6.2 更新 index.md

在 `index.md` 的 `## 课程标准与评价体系 (Standards)` 下添加 wiki 页链接。

### 6.3 raw 库同步规则（每次 raw 变更后必须执行）

**当 raw 库发生变化时（新增/删除/移动标准文件，重命名分类目录），必须同步更新 wiki 库中对应的 wiki 页。这不是可选步骤。**

### 6.4 同步 RAG 库

raw 库变更后，还必须重新运行 RAG 入库脚本同步向量索引：

```bash
cd "<KB_ROOT>/BGE-M3"
env -u PYTHONPATH -u PYTHONHOME runtime/env/Scripts/python.exe scripts/rag_pipeline.py
```

详见 `rag-management` skill。
- **重命名 raw 文件**：更新 wiki 页 frontmatter `sources` 溯源路径；正文仍保持自包含
- **移动 raw 分类目录**：更新 `sources` 溯源路径；Wiki 中使用的图片应在 `LLMWiki/assets/` 中有副本

**完成标准：** 每份标准有自包含 wiki 页，正文无 raw transclusion/可见 raw 链接，Wiki 图片使用 assets 内部路径，index.md 已更新。

## Step 7: 将 raw 更新同步到 RAG 库

**每次 raw 库完成导入后，必须执行此步骤，将 raw 中的变更同步到 BGE-M3 RAG 库。** 不是可选步骤——raw 库、Wiki 库、RAG 库三者必须保持一致。

### 执行方式

运行 RAG 入库脚手架脚本，它会重新扫描整个 raw/ 目录，重新切片 + 向量化 + 建索引：

```bash
cd "<KB_ROOT>/BGE-M3"
env -u PYTHONPATH -u PYTHONHOME runtime/env/Scripts/python.exe scripts/rag_pipeline.py
```

脚本会自动完成：
1. 扫描 raw/textbooks/、raw/standards/、raw/exercises/、raw/lectures/ 全部 MD
2. 按类型切片（教材按H3/H4、标准按H2/H3、题库不切片、讲义按H3/H4）
3. BGE-M3 FP16 GPU 向量化（dense + sparse）
4. 构建 FAISS 索引
5. 持久化到 BGE-M3/runtime/data/ 和 BGE-M3/runtime/index/

**完成标准：** 脚本输出 "RAG 入库完成"，chunks 数量与 raw 文件数量匹配。

## Common Pitfalls

1. **pymupdf4llm 误判标题** — 对中文标准文档，pymupdf4llm 会把页眉、短段落都标为 H2，产生 264 个 H2。改用 `pymupdf.get_text()` + 手动正则。

2. **学术论文编号跨行** — 章节号和标题分在两行，不合并会导致编号行被误判为正文、标题行丢失 H2 标记。

3. **扫描/混合 PDF 或公式风险** — 必须走 BeMarkdown 组合 OCR；marker 只是候选来源，不能单模型入库。

4. **TOC 目录页干扰** — 目录中的章标题和正文重复，导致 duplicate_h2。删除目录点线行即可解决大部分。

5. **试题分析报告属于标准类** — 教育部考试院编的《高考试题分析》这类文件含命题思路、考查目标、试题亮点，本质是教学评研究文档而非练习题。应归入 `raw/standards/`，不提取题目到题库（题目是分析对象而非练习素材）。

6. **章节编号重复** — `## 文档总结` 本身也是一个 H2，章总结数量可能比正文 H2 多 1。检查时忽略此差异。

## Verification Checklist

- [ ] 原件已在 `source-library/` 目录
- [ ] 每份文档一个 MD 文件
- [ ] Frontmatter 完整
- [ ] 标题层级正确（H1=文档名, H2=大章, H3=条目, H4=子条目）
- [ ] 无标题层级跳级
- [ ] 质量检查循环通过
- [ ] **Wiki 库已创建/更新自包含 wiki 页（正文无 raw transclusion/可见 raw 链接）**
- [ ] **Wiki 图片已复制到 LLMWiki/assets/ 并使用内部路径**
- [ ] **index.md 已更新**

## Role D 强制图谱后置阶段

Wiki 同步和质量门禁通过后，用 `source_skill=standards-import` 生成 schema v1 handoff spec，并依次运行：

```bash
python skills/_shared/scripts/import_handoff.py create --spec <handoff-spec.json> --json
python skills/learn/physics-knowledge-graph/scripts/update_graph.py --handoff <import_handoff.json> --dry-run --json
python skills/learn/physics-knowledge-graph/scripts/update_graph.py --handoff <import_handoff.json> --apply --result-out <skills/_ops/runtime/state/.../graph_update_result.json> --json
python skills/_shared/scripts/import_handoff.py verify --handoff <import_handoff.json> --graph-result <graph_update_result.json> --json
```

安全变更必须在最终 RAG 重建前应用；删页、改名、合并和大迁移只输出 `approval_required`。若确实没有图谱变化，dry-run 的 `no_change` 结果可通过 verify。没有 completion-ready 图谱结果时不得声明标准导入完成。
