# LLMWiki_BGE-M3 约定

> `<KB_ROOT>\` 下的 LLMWiki_BGE-M3 是高中物理教学知识库的生产实例，创建于 2026-06-26。
> 当前范围：鲁教版高中物理教学知识库，包括教材、课程标准、高考评价体系、题库、讲义、B站教学视频、RAG 检索和学生数据分析子系统。
> 本库治理规则自成一体；与其他知识库（如有）互不共享目录结构或生产数据。

## 核心架构

教学知识库仍以三库为核心：

1. **`raw/` 源材料库**：保存预处理 Markdown 和必要媒体，包括 `textbooks`、`standards`、`exercises`、`lectures`、`transcripts`。导入流程和 BGE-M3 切片从这里读取。
2. **`LLMWiki/` Wiki 库**：Obsidian 可读的自包含知识页，面向 agent 导航和教师阅读。回答时优先通过 `index.md`、hub 页和 `[[wikilinks]]` 导航。
3. **`BGE-M3/` RAG 库**：由清洗后的 `raw/**/*.md` 构建 chunks、向量和 FAISS 索引。BGE 返回 raw-source chunks，agent 将 Wiki 导航上下文和 RAG chunks 合并使用。

另外有两个独立子系统：

- **`skills/` 能力库**：保存 agent 角色、registry、router、skill 文档和可复用脚本。它不进入教学 RAG。
- **`StudentDataSQL/` 学生数据子系统**：保存 SQLite schema、迁移、脚本、模板和合成 fixtures。真实学生数据只进入本地加密/运行目录，不进入 `raw/`、`LLMWiki/`、`BGE-M3/`、`skills/`、Git 或向量索引。

硬性规则：Wiki 正文不能只是 raw 指针。不要创建只有 `^[../raw/...]`、`## raw 原文` 或可见 `[查看](../raw/...)` 链接的页面。frontmatter `sources` 可以保留 raw 路径作为来源，但回答时 agent 不应把这些路径当成阅读目标。需要进入 Wiki 的 raw 内容必须导入或概括进 Wiki；超过约 20KB 时拆成语义子页面。

## 目录结构

```text
LLMWiki_BGE-M3/
├── AGENTS.md                    # agent 入口权威契约
├── SCHEMA.md                    # 锁定的知识库结构和治理规则
├── raw/                         # 源材料库：预处理 Markdown + media
├── source-library/                         # 原始文件备份库：PDF/DOCX/PPT/图片/音视频等
├── LLMWiki/                     # 自包含 Wiki / Obsidian vault
│   ├── concepts/                # 教学知识页和 hub
│   ├── assets/                  # Wiki 本地图片
│   ├── _meta/                   # 审计、拆分页、校验报告
│   ├── index.md
│   └── log.md
├── BGE-M3/                      # RAG 子系统
│   ├── data/chunks.jsonl
│   ├── output/index.faiss
│   └── scripts/rag_pipeline.py
├── skills/                      # canonical skills、router、registry、脚本
├── StudentDataSQL/              # 学生成绩/作业/掌握度分析子系统
└── output/                      # 生成的教学输出和 QA 产物
```

## 硬性规则

1. **原始材料永久保留**：导入原件保存在 `source-library/`，预处理 Markdown 保存在 `raw/`，不得因生成 Wiki 或 RAG 而删除。
2. **`raw/` 目录可演进，文件不可丢失**：可以新增、改名或重组子目录；移动文件时要同步 Wiki/RAG 来源记录。
3. **三库同步闭环**：导入或修改 raw 内容后，必须同步 Wiki，并按对应 skill 要求重建或校验 RAG。
4. **skills 与教学 RAG 隔离**：`skills/` 文档只供 router/agent 调用，不加入 BGE-M3 教学向量索引。
5. **学生数据隔离**：学生成绩、作业、日常观察、答题卡个人信息不得进入教学知识库或 Git。

## Obsidian 配置

Obsidian vault 通过 `.obsidian/` 文件直接配置，不依赖 GUI：

- `app.json`：附件目录、wikilinks、短路径格式、缩进等。
- `appearance.json`：主题、字体和强调色。
- 常用核心插件：graph、backlink、outgoing-link、tag-pane、properties、outline、bookmarks、file-explorer、global-search。

## 语言约定

- 中文为主，必要英文技术名词保留，例如 `RAG`、`BGE-M3`、`OpenXML`、`OfficeCLI`、`LaTeX`、`OMML`。
- 对教学内容，英文术语首次出现时可写成“中文（English Term）”。
- 路径、命令、字段名、YAML key、JSON key、数据库表名和脚本参数不要强行翻译。

## 页面大小与上下文预算

- `LLMWiki/concepts/` 页面应便于 agent 扫描：目标 <15KB，硬上限约 20KB。
- 页面超过 20KB 或约 200 行时，按结构和语义拆分，不按任意字节数切。
- 父页面保留概要、导航和子链接；细节移入语义子页面，并回链父页面。
- 子页面太多时，把完整子链接放入 `_meta/<parent>-split-map.md`，父页面只保留分组导航。
- 拆分审计和验证报告放 `_meta/`；`concepts/` 保持教学内容和用户可读索引。
- 拆分后校验：没有超大页面、无断链/歧义 wikilink、index/log 已更新，然后按 RAG skill 的安全命令重建索引。

## 与其他知识库的关系

本库与其他知识库（如有）相互独立：路径不同、领域不同、治理规则不同。
可以互相参考方法论，但不共享目录结构或生产数据。

## 记录

- 2026-06-26：初始化 Wiki、配置 Obsidian vault、确立 raw/原件保留规则。
- 2026-07-10：更新当前架构约定，补充 skills 隔离和 StudentDataSQL 边界。
