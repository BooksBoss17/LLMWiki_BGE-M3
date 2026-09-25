# rag-management 检索卡

- status: active
- category: maintain
- role: role-a-import-maintenance
- canonical_skill: `maintain/rag-management/SKILL.md`
- manifest: `maintain/rag-management/manifest.yaml`
- description: 从 `raw/` Markdown 构建和维护教学 BGE-M3 RAG 向量数据库；不得用于 `skills/` 检索。
- triggers: RAG重建, BGE-M3, FAISS, 向量索引
- use_when: 重建教学 RAG, 重建 FAISS 索引, 刷新向量, 检查来自 raw 的 chunks/metadata
- do_not_use_when: 选择 agent skill, 简单知识检索, raw 内容尚未变化前导入源材料
- input: raw/ Markdown 源库, BGE-M3 pipeline 状态, 重建请求
- output: chunks.jsonl, embeddings, sparse weights, index.faiss, metadata 报告
- next_skills: maintain/llmwiki-maintenance/SKILL.md

## 加载规则

路由时只读 card 或 `registry.yaml`。当本 skill 是 primary 时，先读取 `maintain/rag-management/SKILL.md`；只有 skill 要求时再读取 manifest、scripts、references 或 runtime。
