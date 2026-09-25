# knowledge-checklist 检索卡

- status: active
- category: retrieve
- role: role-b-retrieval-calling
- canonical_skill: `retrieve/knowledge-checklist/SKILL.md`
- manifest: `retrieve/knowledge-checklist/manifest.yaml`
- description: 基于 LLMWiki_BGE-M3 证据生成教学知识清单、考点梳理、方法总结、易错点清单和复习自查材料；Word/PDF 导出交给 md-to-docx QA。
- triggers: 知识清单, 考点梳理, 方法总结, 复习清单, 教材总结, 重点知识, 易错点
- use_when: 总结教材全册或章节知识点, 准备考点清单, 制作方法总结, 生成可导出 Word/PDF 的复习资料
- do_not_use_when: 只回答单个问题, 导入源材料, 重建 RAG, 只把已有 Markdown 转 Word/PDF
- input: 教材册次, 章节, 单元, 考试主题, 复习目标, 输出约束
- output: `output/` 下的 Markdown 知识清单, 考点优先级表, 方法流程, 导出/OfficeCLI QA 交接
- next_skills: retrieve/llmwiki-rag-retrieval/SKILL.md, retrieve/md-to-docx/SKILL.md, retrieve/teaching-output-format/SKILL.md

## 加载规则

路由时只读本 card 或 `registry.yaml`。当本 skill 是 primary 时，先只读 `retrieve/knowledge-checklist/SKILL.md`；通常先由 `llmwiki-rag-retrieval` 提供证据，再进入 Word/PDF 和格式 skill。
