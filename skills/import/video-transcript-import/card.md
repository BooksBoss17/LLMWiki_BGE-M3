# video-transcript-import card

- status: active
- category: import
- role: role-a-import-maintenance
- canonical_skill: `import/video-transcript-import/SKILL.md`
- manifest: `import/video-transcript-import/manifest.yaml`
- description: Import Bilibili/teaching videos through search/download, transcription, LLM-driven frame extraction, VLM validation, structured Markdown, Wiki/RAG sync.
- triggers: B站视频导入, 视频转写, 抽帧, faster-whisper, VLM, 关键帧
- use_when: Bilibili video import, video transcription, subtitle cleanup, keyframe extraction, VLM frame validation
- do_not_use_when: PPT analysis, textbook import, exercise-bank import, simple knowledge retrieval
- input: Bilibili URL, local video, audio/video source
- output: transcript Markdown, keyframe assets, video overview/notes/brief pages, import_handoff.json, graph_update_result.json, RAG sync handoff
- required_mcp: bilibili-search (`mcp_bilibili_search_*`; see manifest.yaml and `_registry/mcp_servers.catalog.yaml`)
- next_skills: maintain/llmwiki-maintenance/SKILL.md, learn/physics-knowledge-graph/SKILL.md, maintain/rag-management/SKILL.md

## Load rule

Read this card or `registry.yaml` for routing. When this is the primary match, read only `import/video-transcript-import/SKILL.md` first. Read manifest/scripts/references/runtime only when the loaded skill asks for them.
