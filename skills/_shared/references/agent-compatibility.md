# 通用 Agent 兼容层

本文说明 Hermes 之外的 agent 如何使用 LLMWiki_BGE-M3 知识库。

## 为什么需要这一层

Hermes 原生支持 `skill_view`。其他 agent 通常没有这个工具。为了让 Codex、Claude Code、Gemini CLI、Cursor、Cline/Roo、Copilot Chat、Aider、Continue 等工具使用同一套工作流，本库把 skills 暴露为普通文件：

- `skills/registry.yaml`：机器可读的 skill 注册表。
- 每个工作流一个 canonical `SKILL.md`。
- `manifest.yaml`：脚本、runtime、MCP/plugin 依赖声明。
- `card.md`：检索用轻量文档卡。

任何有文件系统访问权限的 agent 都按同一入口工作：

```text
读取 skills/registry.yaml
-> 可用时运行 skills/_shared/scripts/skill_retriever.py --json "任务描述"
-> 从 load_now[0] 选择一个 primary skill
-> 只读取该 primary skill 的 SKILL.md
-> 需要脚本/runtime/MCP-plugin 依赖时再读 manifest.yaml
-> 必要时调用 scripts/wrappers
```

## 支持的 Agent 入口

| Agent / 工具 | 项目入口文件 |
|---|---|
| Hermes | `AGENTS.md` 或 `.hermes.md`；也可使用 `llmwiki-skill-router` skill |
| OpenAI Codex CLI | `AGENTS.md` |
| OpenCode | `AGENTS.md` 与仓库文件 |
| Claude Code | `CLAUDE.md` |
| Gemini CLI | `GEMINI.md` |
| Cursor | `.cursorrules`、`.cursor/rules/llmwiki-agent-compat.mdc` |
| Cline | `.clinerules` |
| Roo Code | `.roo/rules/llmwiki-agent-compat.md` |
| GitHub Copilot Chat | `.github/copilot-instructions.md` |
| Aider / Continue / 通用 agent | 手动读取 `AGENTS.md` 和本文 |

## 最小启动提示词

```text
你正在 <KB_ROOT> 工作，这是已部署的 LLMWiki_BGE-M3 仓库根目录。
先读 AGENTS.md。
再读 skills/registry.yaml。
不要一次性加载全部 skills。
如果可用，运行 skills/_shared/scripts/skill_retriever.py --json "<任务描述>"。
只把 load_now[0] 当作当前 primary skill 并遵循它。
queued skills 是后续阶段，不属于当前上下文。
只有在需要脚本/runtime 路径或 MCP/plugin 依赖时才读取 manifest.yaml。
没有明确授权时不要修改锁定区。
绝不要把 skills/ 文件加入教学 BGE-M3 RAG 索引。
用中文回复用户。
```

## 兼容层文件

- `AGENTS.md`：根入口权威文件，Codex/OpenCode/Hermes 和通用 agent 优先读取。
- `CLAUDE.md`：Claude Code shim。
- `GEMINI.md`：Gemini CLI shim。
- `.cursorrules` 和 `.cursor/rules/llmwiki-agent-compat.mdc`：Cursor shim。
- `.clinerules`：Cline shim。
- `.roo/rules/llmwiki-agent-compat.md`：Roo Code shim。
- `.github/copilot-instructions.md`：GitHub Copilot Chat shim。
- `skills/_shared/scripts/agent_skill_router.py`：仅依赖标准库的兼容旧路由器。
- `skills/_shared/scripts/skill_retriever.py`：隔离的 skills 专用混合检索器；缓存只写入 `skills/_ops/runtime/state/skill-retrieval/`。
- `skills/_shared/scripts/agent_context_pack.py`：给不能稳定发现规则的 agent 使用的压缩上下文包。
- `skills/_shared/scripts/install_skill_dependencies.py`：新设备部署/依赖修复集中入口。

## MCP / Plugin 依赖

部分 skills 需要外部 MCP 工具。依赖不得只写在 prose 里，必须在对应 `manifest.yaml` 的 `mcp_servers` 中声明，并登记到 `skills/_registry/mcp_servers.catalog.yaml`。

Hermes profile 可用性检查：

```bash
python skills/_shared/scripts/check_mcp_requirements.py --json
```

如果缺少必需 server，使用统一配置生成器接入 bundled launcher；不要手写 `npx` 或依赖用户 npm cache：

```bash
python skills/_shared/scripts/configure_agent_mcp.py --agent <agent> --dry-run --json
python skills/_shared/scripts/configure_agent_mcp.py --agent <agent> --apply --user-authorized --json
```

无稳定 native MCP 的 Agent 使用 `skills/_shared/model-tools/scripts/mcp_call.py` CLI fallback。

新设备首次部署或依赖修复时，使用集中 bootstrap，不要手动逐个安装：

```bash
python skills/_shared/scripts/install_skill_dependencies.py --json
python skills/_shared/scripts/install_skill_dependencies.py --install --all --json
```

该脚本负责检查 bundled MCP、RAG runtime、BGE-M3 模型，以及可选的 OCR/STT 重型 skill runtime。调用项目 venv 时会清理 `PYTHONPATH` 和 `PYTHONHOME`，避免 agent 环境污染。

## 角色

- Role A：导入和维护知识内容，以及导入类 skills；教学导入完成前必须 handoff 给 Role D 更新图谱。
- Role B：面向教师检索知识并生成教学输出。
- Role C：维护知识库框架，包括 roles、registry、router、兼容 shim、验证脚本和 Git 管理子技能。
- Role D：默认以 student 只读模式提供拍题辅导和物理绘图；跨 agent 默认 `weak`，Codex 默认提示显式选择 `strong`。模型档不改变教师授权、hash、dry-run 或破坏性门禁。

## Skill 检索与教学 RAG 隔离

BGE-M3 教学 RAG 只处理来自 `raw/` 的物理知识 chunks。它不得索引 `skills/`、`SKILL.md`、`card.md`、`output/learning_sessions/` 或 agent 工作流文本。

Skill 选择由 `skills/_shared/scripts/skill_retriever.py` 完成。它只读取 registry/card 元数据，并把可选缓存写入 `skills/_ops/runtime/state/skill-retrieval/`。

## Canonical runtime 与整合包镜像

canonical runtime 始终位于 `skills/_shared/model-tools/runtime/`，RAG runtime 始终位于 `BGE-M3/runtime/`，两者都不进入 Git。经用户显式授权，整合包可以携带 registry allowlist 中的当前有效 runtime；它仍不得携带 raw/Wiki 生产内容、RAG data/index/logs、StudentDataSQL runtime、缓存或 disabled/legacy 组件。

目标机器先执行依赖审计和 doctor：

```bash
python skills/_shared/scripts/validate_skill_dependencies.py --json
python skills/_shared/model-tools/scripts/model_runtime.py doctor --all --json
```

整合包更新属于 Role C 框架管理工作。只有用户明确授意“更新/同步/执行整合包”时，才可使用本机 local-only 的 `maintain/integration-package-update`；普通完整性检查或边界讨论仍使用 `maintain/kb-framework-admin`。
