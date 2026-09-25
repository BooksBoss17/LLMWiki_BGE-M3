# Context Compaction Resume Pattern for Large Video Import Batches

Use this reference when a B站视频批量导入会话 reaches the 65% context threshold or the user reports context overflow.

## Trigger

Pause immediately when either is true:

- Estimated context exceeds the configured hard threshold (about 65%).
- The user reports that the context has overflowed or asks to continue from a new/compacted context.

Do not start new downloads, transcriptions, subagents, Wiki generation, or RAG rebuild after the trigger until the resumable state has been written.

## Write a compact resume card

In the current task directory under `tmp/logs/legacy-task-status/<task_id>/`, write or update:

- `context_compaction_card.md` — human-readable state and next actions.
- `context_overflow_pause_status_latest.json` — machine-readable current matrix.

The JSON should include at least:

```json
{
  "generated_at": "ISO timestamp",
  "reason": "context threshold / user reported overflow",
  "status_files": {
    "download_status.json": {"exists": true, "count": 6, "ok": 6},
    "transcribe_status.json": {"exists": true, "count": 6, "ok": 6},
    "raw_static_validation_after_*.json": {"exists": true, "issues": 6}
  },
  "per_video_raw_matrix": [
    {
      "bvid": "BV...",
      "up": "UP主",
      "title": "导入短标题",
      "per_video": ".../per_video/BV..._标题",
      "raw_dir": ".../raw/transcripts/UP/标题",
      "transcript_compact_2min.md": true,
      "chapter_plan.md": true,
      "keyframe_candidates.json": true,
      "keyframes_manifest.json": true,
      "keyframe_vlm_notes.md": true,
      "final_keyframes.json": true,
      "raw_transcript": true,
      "raw_knowledge": false,
      "raw_lesson": false,
      "media_count": 12
    }
  ],
  "blocking_issues": [
    {"bvid": "BV...", "title": "...", "missing_raw_layers": ["raw_knowledge", "raw_lesson"]}
  ],
  "next_after_compaction": [
    "检查 process list 与状态文件",
    "只补缺失 raw 层，不重复已完成步骤",
    "验收 raw 三层 issue_count=0",
    "生成 Wiki 三页、RAG 重建、三圈校验"
  ]
}
```

## Resume sequence in the new/compacted context

1. Read the knowledge-base total-control card if present: `tmp/logs/legacy-task-status/LLMWiki_BGE-M3总控接续卡.md`.
2. Read the task `context_compaction_card.md` and `context_overflow_pause_status_latest.json`.
3. Check tracked background processes before acting; confirm no download/transcribe job is still running.
4. Trust durable files over conversation memory. Re-scan only small matrices; do not read full transcripts unless a specific file is missing or suspicious.
5. Continue from the exact blocking issues in the JSON. For example, if only `raw_knowledge` and `raw_lesson` are missing for three videos, dispatch workers only for those files.
6. After workers finish, run raw validation before any Wiki/RAG work.
7. Generate Wiki/RAG only after raw validation passes.
8. Finish with the standard 3-loop validation and final report.

## Common pitfalls

- Do not repeat download/transcription for videos already marked ok in status files.
- Do not regenerate completed raw layers unless validation identifies a concrete issue.
- Do not start Wiki/RAG while raw layers are still missing.
- Do not paste large transcripts into the main conversation just to resume; rely on per-video compact files and status matrices.
- If subagents were pending before the pause, the new context cannot rely on their in-memory results; verify by reading the files they should have written.
