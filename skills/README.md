# LLMWiki_BGE-M3 Skills Library

本目录是 LLMWiki_BGE-M3 的权威 agent 能力库。目标是让 Hermes、Codex、Claude Code、OpenCode、Cursor/Cline/Roo 等 agent 都通过同一套 registry-router 机制按需加载 skill，而不是一次性读取所有长文档。

## 当前结构

```text
skills/
├── README.md
├── registry.yaml                 # 在线路由入口；agent 先读它
├── _registry/                    # 台账：migration_map / skills.catalog / scripts.catalog / mcp_servers.catalog
├── _incoming/                    # 未整理旧文件/孤儿脚本暂存；不是调用入口
├── _shared/model-tools/          # 多 skill 共用模型、工具、环境与 resolver
├── _ops/audits/                  # 长期可复核的脱敏运维摘要
├── _ops/runtime/                 # 日志、状态、报告、备份与 staging；Git 忽略
├── _archive/                     # 旧 flat skills、redirect stubs、迁移来源、一次性脚本
├── roles/                        # 角色 A/B/C/D 说明
├── import/                       # 导入维护类 skills
├── retrieve/                     # 检索调用/讲义/Word 输出类 skills
├── maintain/                     # Wiki/RAG 维护类 skills
├── analyze/                      # 教学材料与学生数据分析类 skills
├── learn/                        # 学生学习、物理绘图、题库解析和知识图谱类 skills
└── taxonomy/                     # 标签/分类体系类 skills
```

## 正式 skill 包规范

本项目采用实际可执行的 Hermes/Codex 兼容包形态：

```text
<category>/<skill-name>/
├── SKILL.md          # 完整工作流，弱模型也能按它执行
├── manifest.yaml     # 机器可读信息：脚本、runtime、依赖、加载策略
├── card.md           # 极简检索卡片，供 agent 快速判断是否加载
├── scripts/          # 该 skill 专属长期脚本
├── references/       # 分支细节/事故经验/可选说明
├── templates/        # 输出模板
└── runtime/          # 仅允许真正 skill 专属的本机资产；OCR/VLM/STT 禁止放这里
```

说明：GPT 建议里的 `skill.yaml` 在本项目中对应 `manifest.yaml`；`skill.md` 对应 `SKILL.md`；`card.md` 已补齐为轻量检索入口。

OCR、公式识别、文档解析和语音转录统一使用 `_shared/model-tools/`。模型/环境/工具源码位于其 ignored `runtime/`，其他 skill 只通过共享模型 ID、resolver 或 `LLMWIKI_MODEL_RUNTIME_ROOT` 调用。

## 角色分工

- 角色 A：知识内容导入维护，负责 raw/Wiki/RAG 内容同步和导入类 skill，并把教学导入 handoff 给 Role D 更新图谱。
- 角色 B：教师侧检索调用，负责回答、备课、讲义、组卷、Word/PDF 输出和安全学生数据分析。
- 角色 C：知识库框架管理员，负责 skills 架构、registry/router、入口兼容层、角色 A/B/D 测试和 Git 子技能。
- 角色 D：物理学习 Agent，默认只读服务学生；教师授权后维护题库解析、稳定标签和受控知识图谱。

## 加载原则

1. 先读 `registry.yaml`，不要全量读所有 `SKILL.md`。
2. 只读当前任务匹配的一个 primary `SKILL.md`。
3. 需要脚本、模型、环境或 MCP/plugin 时再读对应 `manifest.yaml`。
4. `references/`、`scripts/`、`runtime/` 默认不内联，只有具体分支需要时才读取。
5. 省 token 靠按需加载，不靠削空主流程；`SKILL.md` 必须保留完整闭环。

## Skills 专用检索

跨 agent 推荐使用独立 skill 检索入口：

```bash
python skills/_shared/scripts/skill_retriever.py --json "任务描述"
```

- 输出 `primary/load_now/queued/alternatives/confidence/reason`。
- 多阶段任务只立即读取 `load_now[0]`；`queued` 等进入对应阶段再读。
- 混合检索缓存只写入 `skills/_ops/runtime/state/skill-retrieval/`。
- 脚本或 MCP/plugin 依赖缺失时，先查 `manifest.yaml` 与 `_registry/mcp_servers.catalog.yaml`；Hermes profile 可运行 `python skills/_shared/scripts/check_mcp_requirements.py --json` 检查。
- 不要把 `skills/` 文档加入教学 BGE-M3 RAG；教学 RAG 只从 `raw/` 入库。
- 详细说明见 `_shared/references/skill-retrieval-system.md`。

## 临时脚本规则

- 能反复提升准确率/效率/token 成本的脚本，纳入对应 skill 的 `scripts/` 并登记到 `_registry/scripts.catalog.yaml`。
- 多个 skill 共用的脚本放 `_shared/scripts/`。
- 尚不能判断用途的脚本放 `_incoming/orphans/`。
- 一次性迁移/审计/修复脚本放 `_archive/temp-scripts-*`。
- 运行日志、状态、报告、备份与 staging 放 `_ops/runtime/`；长期可复核的脱敏结论提升到 `_ops/audits/`。

详见 `_registry/temp-script-policy.md`。

## 当前台账

- 正式 canonical skills：25 个
- 活跃/归档脚本登记：58 个
- MCP/plugin 依赖台账：1 个必需 server（bilibili-search）
- 迁移映射记录：20 条

主要台账文件：

```text
_registry/migration_map.csv
_registry/skills.catalog.yaml
_registry/scripts.catalog.yaml
_registry/mcp_servers.catalog.yaml
_registry/temp-script-policy.md
```

## 权限

- `SCHEMA.md`、核心 skill 步骤、`BGE-M3/scripts/rag_pipeline.py` 属锁定区。
- 修改正式 skill 前先备份到维护者指定的 `skill备份/` 目录。
- `_archive/` 默认不参与正式调用；需要恢复时先登记再迁回正式目录。

## 验证

```bash
cd <KB_ROOT>/skills
python _shared/scripts/validate_skill_library.py
python _shared/scripts/skill_retriever.py --self-test
```

## 初次部署依赖

集中检查/安装 skill 依赖：

```bash
cd <KB_ROOT>
python skills/_shared/scripts/install_skill_dependencies.py --json
python skills/_shared/scripts/install_skill_dependencies.py --install --all --json
```

该入口集中处理 MCP/npm 依赖、RAG venv、BGE-M3 模型下载，以及视频转写/OCR skill 的 Python runtime。默认不安装；必须显式传 `--install`。
