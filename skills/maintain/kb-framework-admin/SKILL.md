---
name: kb-framework-admin
description: "维护 LLMWiki_BGE-M3 agent 框架：角色、AGENTS/SCHEMA 入口契约、registry、skill router、兼容 shim、路由夹具、验证脚本和 Role A/B/D 行为测试。"
version: 1.0.0
author: LLMWiki Agent
license: MIT
platforms: [windows]
metadata:
  role: role-c-kb-framework-admin
  tags: [framework, skills, registry, router, compatibility, validation]
---

# KB Framework Admin — 知识库框架维护工作流

本 skill 属于角色 C。用于维护 agent 兼容框架和 skills 库结构，不直接执行知识内容导入或教学检索。

## 核心原则

1. `AGENTS.md` 是根入口权威源；各工具 shim 只保留简短摘要和跳转。
2. 所有 agent 先读 `skills/registry.yaml`，再用 `skills/_shared/scripts/skill_retriever.py --json "任务描述"` 选择一个 primary skill。
3. 每次改 registry、card、router 或角色边界，必须更新路由夹具并运行验证。
4. 保持教学 RAG 和 skill 检索隔离：不得把 `skills/` 文档写入 `BGE-M3/runtime/data` 或 `BGE-M3/runtime/index`。
5. 角色 A 管知识内容导入维护；角色 B 管教师侧检索和教学输出；角色 C 管框架、路由、兼容和测试；角色 D 管学生学习、题库解析、标签和受控知识图谱。

## 主流程

1. 明确本次维护目标：AGENTS/SCHEMA 入口规范、入口兼容、角色边界、router 规则、card 字段、验证脚本或目录结构。
2. 读取 `skills/registry.yaml`、相关 `roles/*.md`、目标 `card.md` 和必要脚本；不要批量读取全部 `SKILL.md`。多 agent campaign 的实际调度转入 `maintain/agent-campaign-orchestration/SKILL.md`。
3. 修改前备份会影响正式 skill/registry 的文件到 `<SKILL_BACKUP_ROOT>/`。
4. 更新 registry/card/router/fixtures/doc 后，运行：

```bash
python skills/_shared/scripts/validate_skill_library.py
python skills/_shared/scripts/skill_retriever.py --self-test
```

5. 至少抽查这些路由：

```bash
python skills/_shared/scripts/skill_retriever.py --json --no-semantic "导入试卷并打知识点标签"
python skills/_shared/scripts/skill_retriever.py --json --no-semantic "解题并查知识库"
python skills/_shared/scripts/skill_retriever.py --json --no-semantic "更新入口文件并测试角色AB"
```

6. 最终报告列出改动文件、验证命令和是否触及锁定区。

## 完成标准

- `validate_skill_library.py` 通过。
- `skill_retriever.py --self-test` 通过。
- 角色 A/B/C/D 职责无重叠冲突。
- agent shim 均指向 `skill_retriever.py --json`。
- 未污染教学 BGE-M3 RAG。
