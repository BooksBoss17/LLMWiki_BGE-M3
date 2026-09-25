---
name: llmwiki-rag-retrieval
description: "LLMWiki_BGE-M3 知识库检索规则：Wiki+RAG 双重检索，五种模式（智能/轻度/标准/深度/极限深度），根据问题类型自动匹配检索深度和召回数量。极限深度检索需管理员权限。面向回答问题的 agent，定义如何调用知识库。"
version: 2.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [rag, retrieval, wiki, knowledge-base, physics, education]
    related_skills: [llm-wiki, kb-arch-wiki]
---

# LLMWiki_BGE-M3 知识库检索规则

面向回答问题的 agent。本规则定义如何调用 LLMWiki（Wiki 库）和 BGE-M3（RAG 库）进行双重检索。

## 知识库结构

```
LLMWiki_BGE-M3/
├── raw/                    ← 原始 MD 文件（教材+标准+题库+讲义+视频转写，3970 chunks）
│   ├── textbooks/          (6本教材, 42 MD, 2387图)
│   ├── standards/          (5份标准/分析文档, 5 MD, 17图)
│   ├── exercises/          (1304题, 5大类17子分类: 力学/电磁学/光学/热学/综合)
│   ├── lectures/           (16份讲义×2: 讲义本体+讲义设计, WMF已全转LaTeX)
│   └── transcripts/        (28+视频×3层=84+MD+287+关键帧, 知识笔记进RAG)
├── LLMWiki/                ← Wiki 库（Obsidian，230 页，[[wikilinks]] 图谱）
│   ├── concepts/           (教材书→章→节三级+标准+知识点枢纽+讲义+讲义设计+视频页)
│   ├── index.md            (内容目录)
│   └── SCHEMA.md           (规范，含Agent角色区分+检索入口规则+权限控制规则)
├── BGE-M3/                 ← RAG 库（BGE-M3 + FAISS，3970 向量）
│   ├── data/chunks.jsonl   (切片文本)
│   ├── output/index.faiss  (向量索引)
│   ├── output/metadata.json
│   └── scripts/rag_pipeline.py
├── output/                 ← 生成的输出文件（讲义、组卷等，非raw资料）
└── source-library/                   ← 二进制原件（PDF/DOCX/MP4, 永久保存）
```

**RAG source_type 分布：** textbook(1703) + standard(103) + exercise(1304) + lecture(16) + lesson_design(111) + transcript(733) = 3970 chunks

**讲义结构（v2.1+）：** 每份讲义目录下有2个MD：
- `讲义XX-标题.md` — 合并版（题目+答案+详解，WMF公式已全转LaTeX），source_type=lecture
- `讲义XX-标题_设计.md` — 讲义设计（知识点体系+选题逻辑+教学策略），source_type=lesson_design

## 三库关系与回答阶段边界

本知识库采用 **raw / Wiki / BGE-M3 三库相对独立** 的架构：

1. **raw 库**：保存原件和整理好的 Markdown 源稿（教材、标准、题库、讲义、视频三层文件）。raw 是导入和 RAG 入库的来源，不是回答阶段的检索入口。
2. **Wiki 库**：保存已经整理或二次加工后的自包含 Markdown 页面，使用 `[[wikilinks]]` 建立结构化图谱。回答问题时，agent 根据 index、枢纽页和 wikilinks 在 Wiki 内找到完成任务所需信息；**不得因为 Wiki 页有 raw 路径溯源就返回 raw 重新读取全文**。
3. **BGE-M3 库**：预先从 raw 中整理好的 MD 切片建索引，回答时通过语义检索召回 chunks。BGE 不返回 Wiki 页面本身，而返回 raw 源稿切片；agent 将 RAG chunks 与 Wiki 页面互补合并。

**硬门禁：Wiki 不是 raw 的索引壳。** Wiki 正文不得只有 `^[../raw/...]`、`## raw 原文`、`[查看](../raw/...)` 这类 raw 指针。frontmatter `sources` 可保留 raw 路径作溯源元数据，但检索/回答时不把它当成读取入口。若发现 Wiki 页是 raw 指针壳，应先通知维护 agent 修复为自包含 Wiki 页，而不是回 raw 临时补读。

## 五种检索模式

### 1. 轻度检索 (Light)

**只检索 Wiki 库。** Agent 直接读取 Wiki 页面内容，不调用 RAG。

| 项目 | 配置 |
|------|------|
| Wiki 检索 | ✅ 读取 concepts/ 中的页面 |
| RAG dense | ❌ |
| RAG sparse | ❌ |
| RAG colbert | ❌ |
| 上下文上限 | ~4K tokens |

**适用场景：**
- 简单概念查询（"什么是机械能守恒？""电容器的作用是什么？"）
- 知识点目录浏览（"教材第几章讲电磁感应？"）
- 班级管理/德育讨论（不依赖物理知识库）
- 文档格式/排版请求
- 日常对话、闲聊、非物理问题

### 2. 标准检索 (Standard)

**Wiki + RAG dense + sparse。** Agent 同时从两个库获取信息。

| 项目 | 配置 |
|------|------|
| Wiki 检索 | ✅ 读取相关页面 |
| RAG dense | ✅ top-K=5 |
| RAG sparse | ✅ top-K=5 |
| RAG colbert | ❌ |
| 上下文上限 | ~8K tokens |

**适用场景：**
- 单题解析（"这道题怎么做？"——需要教材知识点 + 相关题目）
- 备课（"帮我设计交变电流这节课"——需要教材章节 + 课标要求）
- 答案核验（"这个选项为什么错？"——需要教材定义 + 题库类似题）
- 成绩分析后的教学建议（需要课标 + 教材定位薄弱知识点）
- 教研写作（需要课标核心素养 + 教材内容支撑）

### 3. 深度检索 (Deep)

**Wiki + RAG dense + sparse，加大召回量。** 不调用 colbert，但比标准检索更深。

| 项目 | 配置 |
|------|------|
| Wiki 检索 | ✅ 读取多个相关页面 |
| RAG dense | ✅ top-K=10 |
| RAG sparse | ✅ top-K=10 |
| RAG colbert | ❌ |
| 上下文上限 | ~12K tokens |

**适用场景：**
- 跨章节综合题（电磁感应+动量+能量+电路——需要多个知识点的教材+题库）
- 命题/组卷（需要系统检索某知识点的所有题目+教材定义+课标要求）
- 系统性备课（整章设计——需要教材全章+课标+相关题目+评价体系）
- 考试评价体系分析（需要课标+评价体系+教材综合）
- 竞赛/拔高教学（需要超出高中范围的深度内容）
- 研究性学习设计（需要跨学科+跨章节的综合材料）

### 4. 极限深度检索 (Extreme Deep)

> **🔒 需管理员权限** — colbert 模式需要修改 rag_pipeline.py 开启 colbert 向量生成和检索逻辑（锁定区）。标准权限下不可用，调用会返回错误。

**Wiki + RAG 三模式全开（dense + sparse + colbert）。**

| 项目 | 配置 |
|------|------|
| Wiki 检索 | ✅ 读取多个相关页面 |
| RAG dense | ✅ top-K=10 |
| RAG sparse | ✅ top-K=10 |
| RAG colbert | ✅ top-K=5 |
| 上下文上限 | ~16K tokens |

**适用场景：**
- 极端复杂的跨学科综合题（需要最精细的 token 级匹配）
- 命题质量深度分析（需要 colbert 的细粒度交互打分）
- 学术研究级别的知识库检索

**启用方式：** 用户需明确授予管理员权限，然后修改 rag_pipeline.py：
1. 入库时 `return_colbert_vecs=True`，保存 colbert 向量
2. 检索时在 `search()` 方法中添加 `colbert` 分支

### 5. 智能模式 (Smart)

**LLM 自主判断使用哪种检索模式。** 根据问题复杂度、涉及知识点数量、是否需要跨章节综合来决定。

**判断逻辑：**

```
用户提问
  ↓
LLM 分析问题特征：
  - 涉及几个知识点？(1个 vs 多个)
  - 需要解题推导吗？(概念查询 vs 计算推导)
  - 需要跨章节吗？(单章 vs 综合)
  - 需要题库支撑吗？(纯理论 vs 理论+题目)
  - 上下文预算充足吗？
  ↓
匹配检索模式：
  - 1个知识点 + 概念查询 → 轻度
  - 1-2个知识点 + 需要题目/教材 → 标准
  - 多个知识点 + 跨章节 + 综合 → 深度
  - 极端复杂 + 用户明确要求 → 极限深度（需管理员权限）
  ↓
执行检索 → 返回结果
```

**自适应学习（自适应区，标准权限可更新）**

**此区域 agent 可在标准权限下自主更新，无需管理员授权。**

agent 记录每次检索的命中质量和用户反馈，逐步优化同类问题的检索模式：

- 用户反馈"不够详细" → 升级同类问题的检索模式（轻度→标准→深度）
- 某类问题反复用同一模式效果好 → 固化为默认映射

**可更新内容：**
- 检索经验日志（问题特征 → 效果好的检索模式）
- 问题分类→检索模式映射表
- 用户检索偏好记录

**不可更新内容（锁定区）：**
- 四种检索模式的定义（轻度/标准/深度/智能的参数配置）
- 上下文预算上限（4K/8K/16K）
- 检索执行流程（Wiki导航 → RAG调用 → 合并去重）

## 问题分类参考（基于两位教师需求分析）

### 轻度检索场景

| 问题类型 | 典型示例 | 来源教师 |
|---------|---------|---------|
| 概念定义 | "洛伦兹力的方向怎么判断？" | 教师A/教师B |
| 教材目录 | "必修二第3章讲什么？" | 教师B |
| 班级管理 | "学生迟到怎么处理？" | 教师A/教师B |
| 德育讨论 | "如何看待这个家长的行为？" | 教师A |
| 文档请求 | "帮我写个班会PPT大纲" | 教师A |
| 资料整理 | "把这段口述转成正式文本" | 教师B |

### 标准检索场景

| 问题类型 | 典型示例 | 来源教师 |
|---------|---------|---------|
| 单题解析 | "这道电磁感应题怎么做？" | 教师A/教师B |
| 答案核验 | "第6题D选项为什么对？" | 教师B |
| 备课设计 | "帮我设计交变电流第1节课" | 教师A/教师B |
| 命题意图 | "这道题考查了什么核心素养？" | 教师A |
| 错因分析 | "学生这道题为什么错？" | 教师B |
| 教研写作 | "写一篇AI赋能物理教学的文章" | 教师A |
| 成绩分析 | "这次考试物理薄弱点在哪？" | 教师B |

### 深度检索场景

| 问题类型 | 典型示例 | 来源教师 |
|---------|---------|---------|
| 综合大题 | "带电粒子在电磁复合场中的运动" | 教师A |
| 组卷 | "出一份电磁感应综合卷" | 教师A |
| 系统备课 | "备整个第3章恒定电流" | 教师B |
| 评价体系 | "物理科考试如何对接高考评价体系？" | 教师A |
| 竞赛教学 | "竞赛需要补充哪些大学物理内容？" | 教师B |
| 研究性学习 | "设计一个电磁感应的研究性学习项目" | 教师B |

## 检索执行方式

### Wiki 检索（结构化导航）

Wiki 检索不是自动搜索，而是 agent 主动导航。流程：

```
1. read index.md → 找到相关页面名和分类
2. read_file 读取该页面 → 获取内容 + [[wikilinks]]
3. 跟随 [[wikilinks]] 读取相关页面 → 扩展上下文
4. 截取相关段落 → 注入上下文
```

**示例：用户问"机械能守恒的条件是什么"**

```
Step 1: read index.md
  → 发现 [[机械能守恒定律]] 枢纽页

Step 2: read concepts/机械能守恒定律.md
  → 概述：机械能守恒定律及其应用...
  → 教材关联：[[physics-textbook-vol2-第1章-功和机械能]]
  → 题目索引：17题列表

Step 3: read concepts/physics-textbook-vol2-第1章-功和机械能.md
  → 跟随 wikilink 到教材章节
  → 找到"科学验证: 机械能守恒定律"段落
  → 内容：在只有重力或弹力做功的情况下...

Step 4: 截取该段落 → 注入上下文
  → "在只有重力或弹力做功的情况下,物体系统的机械能是守恒的..."
```

**Agent 导航规则：**
- 从 index.md 开始，不要盲目翻 concepts/ 目录
- 枢纽页是起点——它包含概述 + 教材关联 + 题目索引 + 相关知识点
- 跟随 `[[wikilinks]]` 扩展到具体章节或相邻知识点
- 只截取相关段落注入上下文，不读整页（控制 token 预算）
- 如果 index.md 中找不到直接匹配，用 `search_files` 在 concepts/ 中搜索关键词

### RAG 检索（语义匹配）

RAG 检索是自动化的语义匹配，通过 BGE-M3 向量化 + FAISS 索引返回 top-K chunks。

```python
import sys
sys.path.insert(0, r"<KB_ROOT>\BGE-M3\scripts")
from rag_pipeline import RAGRetriever

retriever = RAGRetriever()
retriever.load()

# Dense 检索（语义相似）
results = retriever.search("查询文本", k=5, mode="dense")

# Sparse 检索（关键词匹配）
results = retriever.search("查询文本", k=5, mode="sparse")

# 合并去重，按分数排序
```

**与 Wiki 检索的分工：**
- Wiki 检索是**结构化导航**（index → 枢纽页 → 章节页），精确但需要 agent 判断路径，适合概念查询、目录导航、教材/讲义/视频页阅读。
- RAG 检索是**语义匹配**（query → FAISS → top-K chunks），自动但可能召回碎片，适合题目检索、相似题召回和跨章节综合。
- raw 路径只作溯源元数据；回答阶段不要沿 raw 路径重新 read_file。若需要题目全文，优先用题号/知识点对 BGE 做语义检索召回。

### 混合检索流程（标准/深度模式）

```
1. Agent 分析问题 → 确定检索模式
2. Wiki 检索：read index.md → 枢纽页 → 跟随 wikilinks 到具体章节
3. RAG 检索：rag_pipeline.py 调用 dense/sparse/colbert
4. 合并结果：Wiki 内容 + RAG chunks → 去重 → 注入上下文
5. Agent 基于上下文生成回答
6. 回答中标注来源：[教材:必修二-第1章] [题目:MC0000001] [课标:2017版]
```

**去重规则：** Wiki 页面内容来自 raw MD，RAG chunks 也来自 raw MD，同一内容可能被两次召回。合并时按 source 路径去重，保留信息更完整的版本。

## 上下文管理

| 模式 | Wiki 内容 | RAG chunks | 总预算 |
|------|----------|-----------|--------|
| 轻度 | ~2K tokens | 0 | ~4K |
| 标准 | ~2K tokens | ~4K (5+5 chunks) | ~8K |
| 深度 | ~3K tokens | ~6K (10+10 chunks) | ~12K |
| 极限深度 🔒 | ~4K tokens | ~8K (10+10+5 chunks) | ~16K |

**原则：确保上下文不过量，同时召回信息充分且不召回无效信息。**

- RAG chunks 预截断：每个 chunk 的 text 字段只取前 500 字符注入上下文
- Wiki 页面预截断：只取相关章节，不读整页
- 去重：dense 和 sparse 结果合并后按 source 去重

## 生成输出文件规范

所有基于知识库生成的输出文件（讲义、组卷、教案、复习资料等）统一保存到：

```
LLMWiki_BGE-M3/output/<文件名>.md
```

**不要保存到 raw/ 子目录** — raw/ 是原始资料区（只读），output/ 是生成物区。

讲义/复习讲义生成的工作流详见 `references/lecture-generation.md`（7步：确定模式→Wiki导航→RAG多查询→读题目→读考情→生成讲义→存output）。

## 输出文件管理

所有生成物（讲义、组卷、复习资料等）统一输出到 `LLMWiki_BGE-M3/output/` 目录。

**生成讲义/复习讲义/复习练习的工作流：**

1. 根据课题确定检索模式（系统备课→深度检索，单题解析→标准检索）
2. 如果用户提供近期复习卷、质检卷或本校样卷，先按 `source_title / knowledge_points / 题型 / 题干` 统计题目分布，确定真实重难点；不要按教材目录平均铺开
3. Wiki 导航：read index.md → 相关章节页 → 跟随 wikilinks
4. RAG 检索：dense + sparse（k=10 for 深度, k=5 for 标准），合并去重
5. 题目素材获取：用题号、知识点、题型对 BGE 做 dense+sparse 检索，召回题目 chunk；优先选择本校近期卷、市质检卷和已在 Wiki 枢纽页列出的典型题 ID。不要从 Wiki 的题目索引回 raw 读取全文。
6. 根据统计结果决定内容取舍：高频模块展开，低频模块压缩为易错判断；内容过多时拆成多份练习而非硬塞一份
7. 编写讲义/练习 MD 或直接生成 DOCX，标注来源：`[教材:选三-第X章]` `[题库:MC0001xxx]` `[讲义:冲刺16]`
8. 保存到 `output/` 目录

**MD→Word 转换（A4双栏紧凑排版）：** 见 `references/md-to-docx-compact-a4.md`（含 OMML 公式转换器、Word COM 双栏方案、页数验证循环）

## 常见陷阱

1. **不要每次都开深度检索** — 简单概念题开深度检索会注入大量无关 chunk，浪费上下文窗口还干扰回答。

2. **Wiki 和 RAG 结果可能重叠** — Wiki 页面内容来自 raw MD，RAG chunks 也来自 raw MD，同一内容可能被两次召回。合并时按 source 路径去重。

3. **RAG 检索需要加载模型** — `RAGRetriever.load()` 会加载 BGE-M3 模型（~2秒），首次调用有延迟。后续调用复用已加载的模型。

4. **题库 chunk 含图片引用文本** — RAG 向量化时已去除图片 Markdown 语法，但文本中可能残留图片 alt 文字。注入上下文时清理 `![](media/...)` 引用。

5. **教材切片按 H3/H4 边界** — 一个 chunk 可能只含一个小节，跨小节的问题需要多个 chunk。标准检索 k=5 通常能覆盖，深度检索 k=10 更保险。

6. **智能模式不是一成不变** — agent 应记录"某类问题用某模式效果好"的经验，逐步优化。用户反馈"不够详细"时升级模式。

7. **colbert 不可用是正常的** — 当前 rag_pipeline.py 入库时 `return_colbert_vecs=False`，colbert 向量未生成，检索代码中也无 colbert 分支。极限深度检索需要管理员权限修改代码（开启 `return_colbert_vecs=True` + 添加 colbert 检索逻辑 + 保存 colbert 向量到磁盘）。标准/深度检索不依赖 colbert，dense+sparse 已足够。Codex 等外部 agent 以只读方式接入知识库时，调用 `mode="colbert"` 会返回 None 并报错，这是预期行为。

8. **execute_code 无法调用 RAG** — `execute_code` 运行在默认 Python 环境，缺少 faiss 等依赖。RAG 检索必须通过 `terminal` 调用 BGE-M3/runtime/env 的 Python：`cd <KB_ROOT>/BGE-M3 && env -u PYTHONPATH -u PYTHONHOME runtime/env/Scripts/python.exe -c "..."`。metadata.json 是 dict 结构（keys: total_chunks/total_vectors/dim/chunks），读取 chunks 用 `m.get('chunks',[])`。

8. **极限深度检索需管理员权限** — colbert 模式涉及修改 rag_pipeline.py（锁定区），标准权限下不可用。智能模式下也不得自动切换到极限深度，除非用户明确授予管理员权限。当前 `rag_pipeline.py` 入库时 `return_colbert_vecs=False`，colbert 向量未生成，调用 `mode="colbert"` 会返回 None 并报错。标准/深度检索不依赖 colbert，dense+sparse 已足够。colbert 向量存储开销大（每个 chunk 上百个 token 向量×1024 维），2339 chunks 可能产生数十 GB 数据，需评估磁盘和内存容量后再启用。

9. **RAG 检索须用 BGE-M3/runtime/env 的 Python** — `execute_code` 运行在默认 Python 环境中，缺少 faiss 等依赖。RAG 检索脚本必须通过 `BGE-M3/runtime/env/Scripts/python.exe` 运行（terminal 工具调用），否则会报 `ModuleNotFoundError: No module named 'faiss'`。

11. **用户要求 Word 格式输出时优先 A4 双栏** — 内容多时用双栏排版，7pt 字号 + 9pt 行间距 + 0.8cm 边距可将 ~8000字/13表格的讲义压缩到 A4 两页。**⚠️ python-docx 的 `w:cols` 设置双栏会导致尾随空白页（无法通过 XML 修复），必须用 Word COM 的 `TextColumns.SetCount(2)` 设置双栏。** 详见 `references/md-to-docx-compact-a4.md`。

12. **公式必须用 OMML 格式** — 用户明确要求"转成word之后公式使用OMML格式看上去才更美观"。不要用 Unicode 近似（₀₁₂³ 等）。自写 LaTeX→OMML 转换器，通过 `lxml.etree` 构建 `m:oMath` 元素插入段落。详见 `references/md-to-docx-compact-a4.md`。

13. **知识框架不要用 ASCII 树状图** — 用户反馈"画得很乱"。ASCII 树状图（`├──` `│` `└──`）在 Word 中因字体不等宽会错位。必须用表格（3列：章/节/核心知识点），合并章节单元格。

14. **讲义内容根据类型适配** — 期末复习 ≠ 高考复习。期末复习去掉考情分析、适用教材、复习范围、课型、来源标注等元信息，只留标题+知识点+题目。高考冲刺复习保留考情分析。详见 `references/lecture-generation.md`。

15. **Word COM PDF 导出文件锁** — 如果旧 PDF 被阅读器打开，`ExportAsFixedFormat` 会失败（`com_error -2147352567`）。先 `taskkill //IM WINWORD.EXE //F`，或用不同文件名输出。

## 验证清单

- [ ] Agent 能根据问题类型选择合适的检索模式
- [ ] 轻度检索只读 Wiki，不加载 RAG 模型
- [ ] 标准检索同时返回 Wiki + dense + sparse 结果
- [ ] 深度检索返回 Wiki + dense + sparse（k=10），不调用 colbert
- [ ] 极限深度检索需管理员权限，调用 colbert（当前未启用）
- [ ] 上下文总量不超过预算（轻度 4K / 标准 8K / 深度 12K / 极限深度 16K）
- [ ] 回答中标注信息来源（教材/题目/课标）
- [ ] 智能模式能根据用户反馈调整同类问题的检索深度
- [ ] 智能模式不得自动切换到极限深度，除非用户明确授予管理员权限
