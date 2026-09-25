---
name: knowledge-checklist
description: 基于 LLMWiki_BGE-M3 检索证据生成教学知识清单、考点梳理、方法总结、易错点清单和复习自查材料。用于全册、章节、单元或考试复习主题的 Role B 输出；Word/PDF 交付交给 md-to-docx 和格式 skill。
---

# Knowledge Checklist — 知识清单生成

本 Role B skill 用于把已检索的知识库证据整理成紧凑、可教学使用的知识清单。它不替代检索；先用 `llmwiki-rag-retrieval` 拿到 Wiki/RAG 证据，再用本 skill 组织输出。

## 路由要求

1. 先读 `skills/registry.yaml` 并路由任务。
2. 先用 `retrieve/llmwiki-rag-retrieval/SKILL.md` 获取 Wiki/RAG 证据。
3. 本 skill 只负责把证据整理成知识清单结构。
4. 如果用户要求 Word/PDF，将完成的 Markdown 交给 `retrieve/md-to-docx/SKILL.md` 和 `retrieve/teaching-output-format/SKILL.md`。

回答时不要直接读 raw 源文件。以 Wiki 页面和 BGE-M3 chunks 作为证据面。

## 检索深度

- 单个概念或一课内容：标准检索即可；只有定义类问题可轻度检索。
- 全章、全册、考试复习或跨专题清单：使用深度检索。
- 未获管理员明确授权时，不使用 colbert/extreme 等高成本检索模式。

## 输出结构

输出面向教师的可用清单，不写成长篇论文。必须包含：

1. **使用说明**：适用对象、范围和使用方法。
2. **全册/全章主线**：一小段概括，加一条紧凑依赖链。
3. **知识清单**：按章节或主题分块，每块包括：
   - 必会概念。
   - 必会公式/规律。
   - 典型考法。
   - 方法总结。
   - 易错点。
4. **重点方法专栏**：列出高优先级解题流程或学生自查步骤。

## 内容规则

- 力学、电磁学等计算专题要把物理图像、方向/符号、单位、受力/电路/能量/动量之间的关系说清楚。
- 只考应用辨析的专题，如电磁波、传感器等，要聚焦概念区分和生活情境映射。
- 公式用 Markdown LaTeX，便于 DOCX 导出器转成 OMML。
- 证据标记保持简短，例如 `[教材:选必2-第2章]`、`[课标:选择性必修2]`、`[讲义:电磁感应]`。

## 风格

- 使用适合高中物理教师的简洁中文。
- 只有重复可比较的信息才用表格；方法流程和自查项优先用项目符号。
- 避免脱离知识库证据的泛泛总结。

## 完成标准

- 实质性清单保存到 `output/`。
- 如果要求 Word/PDF，将最终 Markdown 交给 `retrieve/md-to-docx/SKILL.md`；本 skill 不实现单独的导出器。
- 草稿/参考输出可用 `--officecli-qa auto`，正式交付用 `--officecli-qa required`。
- OfficeCLI QA 产物留在 `output/qa_<name>/`。
- 交付前确认页数、文本可读、没有空白页。
- 如果生成了 `contact_sheet.png`，交付前要目视检查。
- 不交付 OpenXML 校验失败、缺图、占位符、异常空白或明显溢出的最终文件。
