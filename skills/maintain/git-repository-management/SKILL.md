---
name: git-repository-management
description: "角色 C 子技能：维护 LLMWiki_BGE-M3 本地 Git 仓库，包括初始化、.gitignore 策略、分批提交、提交前检查、仓库健康和回滚风险确认。"
version: 1.0.0
author: LLMWiki Agent
license: MIT
platforms: [windows]
metadata:
  role: role-c-kb-framework-admin
  tags: [git, repository, gitignore, commit, version-control]
---

# Git Repository Management — Git 仓库管理子技能

本 skill 是角色 C 的子技能。它维护本地 Git 仓库和跟踪策略，不负责知识内容导入、教学检索或学生数据分析。

## 核心原则

1. v1 采用“框架 + Markdown”策略：跟踪入口规则、skills 轻量文件、Wiki/raw Markdown、StudentDataSQL schema/scripts/templates/synthetic fixtures 和轻量配置。
2. 不跟踪模型、venv、RAG 输出、缓存、原件大文件、media 图片、临时代码、output、StudentDataSQL runtime 数据。
3. 初始化仓库前先维护 `.gitignore`，再 `git init` 和 `git add`。
4. 如果缺少 Git identity，只设置仓库本地 `user.name` 和 `user.email`，不改全局配置。
5. 提交前必须检查 `git status --short`、`git ls-files`、`git diff --cached --stat` 和关键路径 `git check-ignore`。
6. 不使用 `git reset --hard`、`git checkout --` 或 `git clean` 回滚用户变更，除非用户明确要求。

## 主流程

1. 确认当前目录是知识库根目录。
2. 维护 `.gitignore`，至少覆盖：
   - `BGE-M3/runtime/env/`
   - `BGE-M3/runtime/models/`
   - `BGE-M3/runtime/data/`
   - `BGE-M3/runtime/index/`
   - `skills/**/runtime/`
   - `skills/_shared/model-tools/runtime/`
   - `skills/_ops/runtime/state/skill-retrieval/`
   - `skills/_ops/runtime/reports/`
   - `StudentDataSQL/runtime/db/`
   - `StudentDataSQL/runtime/imports/`
   - `StudentDataSQL/runtime/exports/`
   - `StudentDataSQL/runtime/backups/`
   - `StudentDataSQL/runtime/logs/`
   - `output/`
   - `source-library/`
   - `raw/**/media/`
   - `tmp/tasks/`
3. 如果仓库未初始化，运行 `git init`。
4. 如果本仓库缺少本地 identity，运行：

```bash
git config user.name "LLMWiki Git Admin"
git config user.email "llmwiki-git-admin@example.local"
```

5. 提交前运行框架验证：

```bash
python skills/_shared/scripts/validate_skill_library.py
python skills/_shared/scripts/skill_retriever.py --self-test
```

6. 提交前检查忽略规则：

```bash
git status --short
git ls-files
git check-ignore -v BGE-M3/runtime/models/
git check-ignore -v BGE-M3/runtime/env/
git check-ignore -v skills/_ops/runtime/state/skill-retrieval/
git check-ignore -v StudentDataSQL/runtime/db/student_data.dev.sqlite3
git check-ignore -v output/test.md
git check-ignore -v source-library/
git check-ignore -v raw/exercises/力学/运动学/media/
```

7. 按来源分批 `git add` 和 `git commit`，不要把框架、StudentDataSQL、知识内容、运行产物混在一个提交里。

## 分批提交建议

- 框架/入口/skills/router：`Update framework routing and skill library`
- StudentDataSQL 框架：`Add StudentDataSQL analysis subsystem`
- 知识内容导入：`Import ... knowledge pages`
- 整合包或部署脚本：`Update integration package tooling`
- 临时代码归档：`Organize temporary code artifacts`

提交信息用英文短句即可，便于 Git 工具和跨 agent 读取；正文说明可用中文。

## 安全边界

- 不提交 `BGE-M3/runtime/models/`、`BGE-M3/runtime/env/`、`BGE-M3/runtime/data/`、`BGE-M3/runtime/index/`。
- 不提交 `skills/_shared/model-tools/runtime/`、skill runtime env/model、`skills/_ops/runtime/` 缓存/报告。
- 不提交 `StudentDataSQL/runtime/db/`、`imports/`、`exports/`、`backups/`、`logs/`。
- 不提交 `source-library/`、`raw/**/media/`、视频下载中间件、`.m4s`、`.mp4`、`.wav`。
- 不提交真实学生数据、答题卡、成绩单、数据库或导出报告。

## 完成标准

- Git 仓库已初始化或已确认健康。
- `.gitignore` 与当前跟踪策略一致。
- `git ls-files` 不包含模型、venv、RAG 输出、缓存、原件大文件、media 图片或学生数据运行产物。
- 验证脚本通过。
- 提交后工作树干净，或只剩用户明确保留的未提交/未跟踪文件。
