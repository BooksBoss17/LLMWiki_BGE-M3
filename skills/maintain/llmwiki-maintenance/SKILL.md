---
name: llmwiki-maintenance
description: "用于维护 LLMWiki_BGE-M3 的 Wiki 层：检查死链、孤立页、index/log 新鲜度、视频三页结构、raw 指针泄漏、图片资源，并判断是否需要重建 RAG。"
version: 1.0.0
author: LLMWiki Agent
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [llmwiki, wiki, maintenance, graph-health, rag, video]
    related_skills: [video-transcript-import, rag-management, llmwiki-rag-retrieval]
---

# LLMWiki-BGE-M3 Wiki 维护

本 skill 属于 Role A 的知识内容维护范围，负责 Wiki 层健康检查、导航更新、视频三页规范化，以及 raw/Wiki/RAG 边界判断。它不用于回答物理问题；回答和检索使用 `llmwiki-rag-retrieval`，RAG 重建使用 `rag-management`，新内容导入使用对应导入 skill。

## Canonical 路径

```text
KB root:      <KB_ROOT>
Wiki vault:   <KB_ROOT>\LLMWiki
Raw source:   <KB_ROOT>\raw
RAG root:     <KB_ROOT>\BGE-M3
Temp code:    <KB_ROOT>\临时代码
Package:      <PACKAGE_ROOT>
```

## 使用场景

用户提出下列任务时使用：

- 检查、维护、更新 Wiki 库或图谱健康。
- 修复死链、孤立页、缺失 frontmatter、坏图片、过期 index/log。
- 把视频 Wiki 规范化为父页面 + 知识笔记 + 教学简案。
- 检查 Wiki 是否自包含，正文是否泄漏 raw 路径。
- 判断 Wiki-only 修改是否需要重建 RAG。

不要用于：

- 从零导入新教材、题库、视频或讲义。
- 未经管理员授权修改 `SCHEMA.md` 或 `BGE-M3/scripts/rag_pipeline.py`。
- 把 `_meta/` 备份当作活跃 Wiki 页面读取。

## 权限边界

普通维护可编辑：

- `LLMWiki/index.md`
- `LLMWiki/log.md`
- `LLMWiki/concepts/`、`comparisons/`、`entities/`、`queries/` 下的活跃 Wiki 页面
- `LLMWiki/_meta/` 下的审计记录和备份
- `tmp/tasks/` 下的临时审计脚本或报告

需要明确管理员授权才可编辑：

- `SCHEMA.md`
- `BGE-M3/scripts/rag_pipeline.py`
- 标记 locked 的 skill 工作流步骤
- 检索模式、上下文预算、权限控制规则

## 三库边界

1. `raw/`：永久源库和清洗后的 Markdown；教学 RAG 从 raw 清洗源切片。
2. `LLMWiki/`：自包含 Wiki 页面，通过 `[[wikilinks]]` 导航；正文不得只是 raw 转引壳。
3. `BGE-M3/`：从 raw 清洗 Markdown 生成语义 chunks，不从 Wiki 或 skills 重复建索引。

Wiki frontmatter 可保存来源：

```yaml
sources:
  - ../raw/transcripts/<UP>/<title>/<title>_知识笔记.md
```

Wiki 正文禁止出现：

```markdown
^[../raw/...]
## raw 原文
[查看](../raw/...)
![图](../raw/...)
../raw/...md
```

只有 Wiki index、frontmatter 或导航改变时，不重建 RAG。只有 raw 清洗源内容改变，或 RAG metadata/index 明显过期时，才重建 RAG。

## 维护流程

### 1. 定义活跃图谱

活跃图谱目录：

```text
LLMWiki/concepts/
LLMWiki/comparisons/
LLMWiki/entities/
LLMWiki/queries/
```

控制文档：

```text
LLMWiki/index.md
LLMWiki/log.md
```

排除目录：

```text
LLMWiki/_meta/
LLMWiki/_archive/
LLMWiki/assets/
LLMWiki/.obsidian/
raw/
source-library/
BGE-M3/
tmp/tasks/
```

### 2. 先运行审计

优先使用复用脚本：

```bash
python skills/maintain/llmwiki-maintenance/scripts/audit_wiki_health.py --kb-root . --out skills/_ops/runtime/reports/wiki-maintenance-audit.json
```

审计至少覆盖：

- 活跃页面 `[[wikilinks]]` 死链。
- 活跃孤立页。
- Markdown 图片资源缺失。
- Wiki 正文可见 raw 指针。
- 缺失或损坏 frontmatter。
- 非控制页面超过 20KB。
- 每个 `视频-*` 是否具备父页面、`-知识笔记`、`-教学简案`。
- `index.md` 是否包含明显过期的视频父页面统计或死链。

### 3. 分类再修复

| 类别 | 含义 | 处理 |
|---|---|---|
| 可安全自动修复 | 目标明确的格式、导航、引用问题 | 直接修复 |
| 结构性例外 | 有意拆分或历史审计结构 | 单独记录，不强行归零 |
| 需要语义判断 | 缺页、重命名、可能删除知识 | 先询问用户或提出计划 |

安全自动修复示例：

- `index.md` 里把 `_meta/` 审计文档当成 `[[wikilinks]]`：改成普通 Markdown 链接。
- `log.md` 代码示例中的 ``[[wikilinks]]``：转义或改成普通文本。
- 明确类型的审计/导入报告缺 frontmatter。
- 视频 index section 过期：按视频父页面重生成。
- raw 已有三层视频内容但 Wiki 缺子页：补齐子页。

结构性例外示例：

- 课程标准拆分子页由 split map 管理。
- 大页拆分 map、`_meta/` 审计文档。

最终报告要区分 `raw_orphan_count`、`structural_orphan_count`、`actionable_orphan_count`。

### 4. 修复图谱和导航

修复原则：

1. 大页面或风险页面先备份到 `LLMWiki/_meta/<topic>-backup-<YYYY-MM-DD-HHMMSS>/`。
2. 只改最小受影响文件。
3. 保留知识内容，不为了通过 lint 删除知识。
4. 维护完成后在 `LLMWiki/log.md` 追加记录。

死链修复优先级：

1. 拼写或重命名错误：改到已有页面。
2. 指向 `_meta/` 或非活跃页面：改为普通文本或 Markdown 链接。
3. 确实缺少概念页：创建页面，但不得凭空编造内容。

孤立页修复优先级：

1. 从最相关 hub 页面链接。
2. 拆分子页由 split map 链接，避免让父页重新膨胀。
3. 结构性孤立页记录为例外。

### 5. 维护 `index.md`

`index.md` 是导航目录，不要求罗列每个拆分子页。

必须更新：

- `Last updated` 和活跃页计数。
- 主要 hub 页面。
- 视频父页面列表，并说明每个父页面应有 `知识笔记` / `教学简案` 子页。
- `_meta/` 审计文档用普通 Markdown 链接，不用 `[[wikilinks]]`。
- 不把大量课程/教材拆分子页塞进 index；通过父页或 split map 可达即可。

### 6. 视频 Wiki 三页结构

标准结构：

```text
LLMWiki/concepts/视频-<标题>.md
LLMWiki/concepts/视频-<标题>-知识笔记.md
LLMWiki/concepts/视频-<标题>-教学简案.md
```

父页面：

- Obsidian/index/graph 入口卡片。
- 链接知识笔记和教学简案。
- 保存 UP 主、BV、相关概念 metadata。

知识笔记：

- 纯文本知识内容。
- 去除时间戳噪声和平台广告。
- 可直接被答疑 agent 阅读。

教学简案：

- 教学流程和策略。
- 可引用复制到 `LLMWiki/assets/` 的关键帧图片。

如果 raw 已有三层内容而 Wiki 只有单页，则从 raw 清洗文件补齐子页，并把父页改成摘要入口。raw 缺失时不得臆造，报告需要重新生成 raw。

### 7. RAG 决策门

需要重建 RAG：

- `raw/` 知识源文件改变。
- raw 视频知识笔记被清洗或规范化。
- raw 题库、教材、课标内容改变。

不需要重建 RAG：

- 只改 `LLMWiki/index.md`。
- 只改 `LLMWiki/log.md`。
- 只改 Wiki frontmatter 或导航。
- 只写 `_meta/` 审计记录。
- 只把 `_meta/` wikilink 改成 Markdown link。

Codex/Windows 推荐通过 UTF-8 guard 执行 RAG：

```powershell
python .codex/helpers/windows_utf8_guard.py rag --kb-root .
```

直接命令仍可用于确认：

```bash
cd '<KB_ROOT>/BGE-M3' && env -u PYTHONPATH -u PYTHONHOME runtime/env/Scripts/python.exe scripts/rag_pipeline.py
```

### 8. 验证

大批量修复至少跑 3 轮审计：

- 死链 = 0。
- 坏图片 = 0。
- Wiki 正文可见 raw 指针 = 0。
- frontmatter 错误 = 0。
- 视频缺页 = 0。
- actionable orphan = 0。
- 非控制 oversized 页面 = 0，或已有拆分计划。

小型 index/log-only 修复可跑 1 次全量审计，但最终计数仍必须清楚。

最终摘要写入：

```text
skills/_ops/runtime/reports/wiki-maintenance-summary.json
```

## 整合包同步

如果本 skill 或 framework 工作流发生变化，不要手动复制到整合包。普通检查和边界讨论仍由 Role C 的 `kb-framework-admin` 处理；用户明确授权“更新/同步/执行整合包”时，才使用本机 local-only 的：

```bash
python skills/maintain/integration-package-update/scripts/sync_integration_package.py --dry-run --json
python skills/maintain/integration-package-update/scripts/sync_integration_package.py --apply --user-authorized --json
```

该 local-only skill 不会进入整合包。

## 常见陷阱

1. 把 `_meta/` 备份算作活跃页面，导致假死链和假重复页。
2. 为了让结构性孤立页归零而膨胀父页。
3. 把 `_meta/` 审计链接写成活跃 wikilink。
4. Wiki-only 修改后误重建 RAG。
5. 在 Wiki 正文暴露 raw 路径。
6. 为了通过 lint 删除知识内容。
7. 视频页面只规范化一半。
8. 把整合包放进知识库根目录。

## 验证清单

- [ ] 活跃图谱排除 `_meta`、`_archive`、assets、raw、RAG、原件、临时代码。
- [ ] 审计报告写入 `tmp/tasks/`。
- [ ] 死链 = 0。
- [ ] 坏图片 = 0。
- [ ] 可见 raw 正文指针 = 0。
- [ ] malformed frontmatter = 0。
- [ ] 视频组数量与缺页数量已报告，缺页 = 0。
- [ ] raw orphan 已区分 structural/actionable，actionable = 0。
- [ ] index 日期、计数和视频父页面列表已更新。
- [ ] log 追加维护摘要。
- [ ] RAG 重建决策已说明；需要重建时必须有真实输出。
- [ ] 若触及 skill/package 流程，已按 Role C 授权流程处理。
