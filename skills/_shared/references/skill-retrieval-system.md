# Skills 专用检索系统

本系统只用于选择 agent 应加载哪个 canonical skill。它和教学知识库 RAG 完全隔离。

## 入口命令

```bash
python skills/_shared/scripts/skill_retriever.py --json "任务描述"
```

常用选项：

- `--no-semantic`：只使用规则和词法检索，最快，适合兼容层和自测。
- `--rebuild`：重建 skills 专用检索缓存，只写入 `skills/_ops/runtime/state/skill-retrieval/`。
- `--top-k N`：返回候选 skill 数量。
- `--self-test`：运行 `skills/_registry/skill-routing-fixtures.jsonl` 路由回归测试。

旧入口仍可用：

```bash
python skills/_shared/scripts/agent_skill_router.py --json "任务描述"
```

旧入口会复用 `skill_retriever.py` 的规则/词法路由，但默认禁用语义层以保持快速、稳定。

## 输出字段

- `primary`：当前阶段唯一应读取的 `SKILL.md`。
- `load_now`：当前应立即加载的 skill；v1 只包含一个 primary。
- `queued`：后续阶段可能需要的 skill，不要提前读取。
- `alternatives`：其他候选，只用于诊断。
- `confidence`：路由置信度。
- `reason`：命中的阶段和关键词证据。
- `semantic_status`：语义层状态；无缓存或不可用时不影响规则/词法路由。

## 隔离规则

1. 教学 RAG 只服务 `raw/` → `BGE-M3/` 的知识检索，不索引 `skills/`。
2. skill 检索只读取 `registry.yaml`、`card.md` 和必要元数据，不读取全部 `SKILL.md`。
3. skill 检索缓存只允许位于 `skills/_ops/runtime/state/skill-retrieval/`。
4. 不要把 `skills/`、`SKILL.md`、`card.md` 写入 `BGE-M3/runtime/data/chunks.jsonl` 或 `BGE-M3/runtime/index/metadata.json`。

## 多阶段任务

多阶段任务只加载一个 primary，其余进入 `queued`：

- 试卷导入并打标签：先读 `import/exercise-bank-import/SKILL.md`，再进入 `taxonomy/exercise-knowledge-tags/SKILL.md` 和 RAG 同步。
- 讲义生成并导出 Word：先读 `retrieve/lecture-generation/SKILL.md`，内容完成后再读 `retrieve/md-to-docx/SKILL.md`。
- 简单问答/解题：只读 `retrieve/llmwiki-rag-retrieval/SKILL.md`。
- 入口/registry/router/角色测试：读 `maintain/kb-framework-admin/SKILL.md`。
- Git 初始化、`.gitignore`、提交和仓库健康：读 `maintain/git-repository-management/SKILL.md`。

## Card 维护要求

每个 active skill 的 `card.md` 必须包含这些字段：

- `use_when`
- `do_not_use_when`
- `input`
- `output`
- `next_skills`

这些字段是检索文本的主要来源。不要把长流程、事故复盘或运行细节放进 card；需要时放到 `SKILL.md`、`references/` 或脚本中。
