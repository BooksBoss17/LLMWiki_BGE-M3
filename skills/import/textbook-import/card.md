# textbook-import card

- status: active
- category: import
- role: role-a-import-maintenance
- canonical_skill: `import/textbook-import/SKILL.md`
- manifest: `import/textbook-import/manifest.yaml`
- description: Import textbook PDF/DOCX through the shared BeMarkdown conversion entry, then split chapters, apply formatting-only safe fixes, resolve source-evidenced OCR repair tasks, and sync Wiki/RAG.
- triggers: 教材导入, 课本入库, 章节拆分, OCR清理
- use_when: textbook import, reference book import, chapter splitting, structured book cleanup, raw/textbooks creation
- do_not_use_when: exercise bank import, standards import, video transcription, simple question answering
- input: textbook PDF/DOCX/Markdown, structured reference book
- output: chapter Markdown files, cleaned media, Wiki pages, import_handoff.json, graph_update_result.json, RAG sync handoff
- next_skills: maintain/llmwiki-maintenance/SKILL.md, learn/physics-knowledge-graph/SKILL.md, maintain/rag-management/SKILL.md

## Load rule

Read this card or `registry.yaml` for routing. When this is the primary match, read only `import/textbook-import/SKILL.md` first. Read manifest/scripts/references/runtime only when the loaded skill asks for them.
