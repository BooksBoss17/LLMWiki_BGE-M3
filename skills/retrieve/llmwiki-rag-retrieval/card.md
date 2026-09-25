# llmwiki-rag-retrieval 检索卡

- status: active
- category: retrieve
- role: role-b-retrieval-calling
- canonical_skill: `retrieve/llmwiki-rag-retrieval/SKILL.md`
- manifest: `retrieve/llmwiki-rag-retrieval/manifest.yaml`
- description: 使用 LLMWiki_BGE-M3 教学知识库做 Wiki+RAG 检索，用于答疑、解题、备课事实核对和知识导航。
- triggers: 知识库检索, RAG, Wiki导航, 备课, 解题, 组卷
- use_when: 回答物理问题, 解题, 查找知识库内容, 定位 Wiki 页面, 备课检索
- do_not_use_when: 导入源材料, 重建索引, 生成完整讲义或试卷, 导出 Word/PDF
- input: 问题, 主题, 题干, 检索需求
- output: 有证据支撑的回答, 引用的 Wiki/RAG 上下文, 建议的后续 skill
- next_skills: retrieve/lecture-generation/SKILL.md

## 加载规则

路由时只读本 card 或 `registry.yaml`。当本 skill 是 primary 时，先只读 `retrieve/llmwiki-rag-retrieval/SKILL.md`；只有该 skill 要求时才读取 manifest/scripts/references/runtime。
