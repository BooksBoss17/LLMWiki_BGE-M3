---
name: rag-management
description: "管理 BGE-M3 教学 RAG：从 raw/ Markdown 构建 chunks、embedding、FAISS 索引和 metadata；在 raw 内容更新后安全重建检索库；检查 RAG 输出是否污染或过期。用于 RAG重建、BGE-M3、FAISS、向量索引和检索库维护。"
version: 1.1.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [rag, bge-m3, faiss, vector-search, embedding, knowledge-base]
    related_skills: [textbook-import, standards-import, exercise-bank-import, video-transcript-import]
---

# RAG Management — BGE-M3 向量检索库管理

本 Role A 维护 skill 用于让教学 RAG 与 `raw/` 保持同步。它不导入新材料，也不回答物理问题；导入完成后由它重建或检查 `BGE-M3/` 输出。

## 使用场景

- `raw/` 中教材、课标、题库、讲义或视频知识笔记发生变化。
- 需要重建 `BGE-M3/runtime/data/chunks.jsonl`、`BGE-M3/runtime/index/index.faiss` 和 `metadata.json`。
- 需要检查教学 RAG 是否误收录 `skills/`、`StudentDataSQL/`、`output/` 或其他非教学来源。
- 需要对 RAG 检索库做健康检查，而不是直接回答用户问题。

不要用于：

- 导入 PDF/DOCX/PPT/视频或题库原件。
- 生成讲义、组卷、导出 Word/PDF。
- 查询学生成绩或作业数据。
- 修改 `BGE-M3/scripts/rag_pipeline.py` 的核心逻辑；该文件属于锁定区，除非用户明确授权。

## 输入来源

教学 RAG 只从清洗后的 `raw/**/*.md` 构建。当前约定：

| 数据类型 | 切片方式 | chunk 粒度 | 说明 |
|---|---|---|---|
| 教材 | 按 H3/H4 标题边界 | 每节一个 chunk | 保留标题路径作为上下文 |
| 标准/评价体系 | 按 H2/H3 条款边界 | 每条款一个 chunk | 保留文档名和条款标题 |
| 题库 | 不切片 | 每题一个 chunk | 题目天然是最小单元 |
| 讲义 | 按教材方式 | 每节一个 chunk | `source_type='lecture'` |
| 视频转写 | 按 H2/H3 标题边界 | 每节一个 chunk | 只切 `*_知识笔记.md`；逐字稿不进入 RAG |

## 输出文件

| 文件 | 位置 | 格式 | 说明 |
|---|---|---|---|
| `chunks.jsonl` | `BGE-M3/runtime/data/` | JSON Lines | 每行一个 chunk，含 `id/source/source_type/title/text/chunk_index` |
| `embeddings.npz` | `BGE-M3/runtime/index/` | NumPy | dense 向量矩阵 |
| `sparse_weights.json` | `BGE-M3/runtime/index/` | JSON | 每条 chunk 的 sparse 权重 |
| `index.faiss` | `BGE-M3/runtime/index/` | FAISS | 归一化向量的 `IndexFlatIP` 索引 |
| `metadata.json` | `BGE-M3/runtime/index/` | JSON | chunk id、source 映射和统计信息 |

## 安全重建命令

从知识库根目录运行。Codex on Windows 优先使用 UTF-8 guard：

```powershell
python .codex/helpers/windows_utf8_guard.py rag --kb-root .
```

通用命令在 `BGE-M3/` 下运行，注意清除 agent 注入的 Python 环境变量：

```bash
env -u PYTHONPATH -u PYTHONHOME runtime/env/Scripts/python.exe scripts/rag_pipeline.py
```

Windows PowerShell 可等价使用：

```powershell
Set-Location BGE-M3
$env:PYTHONPATH=$null
$env:PYTHONHOME=$null
.\runtime\env\Scripts\python.exe scripts\rag_pipeline.py
```

## 检查规则

重建前：

- 确认本次 raw 修改属于教学内容，而不是学生个人数据、skill 文档或临时输出。
- 如果只修改了 `skills/`、`StudentDataSQL/` schema/scripts、`output/`、`tmp/tasks/`，不要重建教学 RAG。
- 如果导入任务尚未完成质量检查，不要把半成品 raw 推入 RAG。

重建后：

- 检查命令退出码为 0。
- 检查 `BGE-M3/runtime/data/chunks.jsonl`、`BGE-M3/runtime/index/index.faiss`、`BGE-M3/runtime/index/metadata.json` 存在且更新时间更新。
- 抽查 `metadata.json` 中的 `source`，不得包含 `../skills`、`StudentDataSQL`、`output`、`临时代码`、`原件` 或学生姓名/成绩/作业记录。
- 运行一次代表性检索，确认新增内容可召回。

## 与其他 skill 的关系

- 教材、课标、题库、讲义、视频导入完成后，通常由相应导入 skill queued 到本 skill。
- 用户只是提问、解题或备课检索时，使用 `retrieve/llmwiki-rag-retrieval/SKILL.md`。
- 用户要求修改 RAG pipeline 代码、路由规则或框架策略时，切换到 `maintain/kb-framework-admin/SKILL.md`。

## 完成标准

- RAG 重建命令成功。
- 输出文件完整且未收录非教学来源。
- 关键新增内容能被检索到。
- 最终报告说明是否重建、使用命令、输出文件、污染检查和抽查结果。
