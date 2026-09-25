# standards-import card

- status: active
- category: import
- role: role-a-import-maintenance
- canonical_skill: `import/standards-import/SKILL.md`
- manifest: `import/standards-import/manifest.yaml`
- description: Import curriculum standards, evaluation systems, exam analysis reports, and policy-like teaching documents into raw/standards plus Wiki/RAG; all PDFs first use BeMarkdown diagnosis and image/mixed/formula-risk PDFs use its combined OCR gate.
- triggers: 课程标准, 评价体系, 标准导入, 试题分析报告
- use_when: curriculum standard import, evaluation framework import, exam analysis report import, clause/heading normalization
- do_not_use_when: ordinary exercise import, simple retrieval, lecture generation, Word export
- input: PDF/DOCX/Markdown standards or evaluation documents
- output: raw/standards Markdown, Wiki pages, import_handoff.json, graph_update_result.json, RAG sync handoff
- next_skills: maintain/llmwiki-maintenance/SKILL.md, learn/physics-knowledge-graph/SKILL.md, maintain/rag-management/SKILL.md

## Load rule

Read this card or `registry.yaml` for routing. When this is the primary match, read only `import/standards-import/SKILL.md` first. Read manifest/scripts/references/runtime only when the loaded skill asks for them.
