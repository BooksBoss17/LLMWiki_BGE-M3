# Batch Video Import: Context-Budgeted Subagent Orchestration

Use this pattern when importing many Bilibili teaching videos into LLMWiki_BGE-M3 without overloading the main conversation.

## When to use

- User asks to import multiple videos or a full priority batch.
- The task includes long transcripts, many VLM frame notes, raw/Wiki/RAG synchronization, and repeated validation.
- The main agent must remain a controller rather than carrying all transcript and VLM text in context.

## Controller/subagent split

Main agent owns:

1. Task directory and queue files under `tmp/logs/legacy-task-status/<task_id>/`.
2. Download/merge/audio-decode status JSON.
3. faster-whisper process management and transcript status JSON.
4. Final raw/Wiki/RAG acceptance, RAG rebuild, three validation loops, cleanup, and user report.

Subagents can own only bounded file-in/file-out work:

1. Per-video compact analysis from `transcript_compact_2min.md` + `visual_cue_lines.md`.
   - Output: `chapter_plan.md`, `keyframe_candidates.json`, `asr_risk_notes.md`.
2. Per-video raw generation after keyframes pass VLM.
   - Output only: `<title>_知识笔记.md`, `<title>_教学简案.md` in the assigned raw directory.
3. Optional candidate discovery and local coverage scans before import.

Subagents must not download, transcribe, write Wiki/RAG, modify schema/skills/pipeline, or make final acceptance decisions.

## Context-saving workflow

1. Build queue JSON with `bvid`, `up`, `title`, `duration_sec`, `import_title`, `keyframes`.
2. Download/merge batch in background; read only `download_status.json` summaries.
3. Transcribe completed downloads in batches; read only `transcribe_status.json` summaries.
4. For each completed transcript, script-generate:
   - `transcript_compact_2min.md` (bucketed transcript summary)
   - `visual_cue_lines.md` (lines containing visual/board cues)
   - `video_meta.json`
5. Dispatch per-video subagents for chapter/keyframe/ASR analysis.
6. Main agent extracts frames with ffmpeg, creates contact sheets, runs VLM review, and writes:
   - `keyframes_manifest.json`
   - `contact_sheets.json`
   - `keyframe_vlm_notes.md`
   - `final_keyframes.json`
7. Copy accepted frames into raw `media/`, generate raw transcript layer, then dispatch raw-generation subagents for knowledge notes and lesson plans.
8. Main agent validates raw before writing Wiki.
9. Generate Wiki three-page structure and copy lesson-plan images to `LLMWiki/assets/videos/<UP>/<title>/`.
10. Run RAG once after the batch, then run at least three validation loops.

## Raw validator compatibility pitfall

`validate_video_import.py` expects these audit files inside each raw video directory:

- `keyframe_vlm_notes.md`
- `keyframe_candidates.json`

If the working copies live under `tmp/logs/legacy-task-status/.../per_video/<BV>`, copy them into the raw directory before validation. A good `keyframe_candidates.json` can wrap final frame metadata:

```json
{
  "bvid": "BV...",
  "title": "导入标题",
  "vlm_notes": "keyframe_vlm_notes.md",
  "vlm_all_complete": true,
  "final_keyframes": [],
  "candidate_source": "tmp/tasks/.../keyframe_candidates.json",
  "retries": []
}
```

The raw directory should then contain:

```text
<title>.md
<title>_知识笔记.md
<title>_教学简案.md
media/keyframe_*.jpg
keyframe_manifest.json
keyframe_candidates.json
keyframe_vlm_notes.md
```

## Validation gates

Run these in order:

1. Raw only:

```bash
python validate_video_import.py --kb-root <KB_ROOT> --up <fallback_up> --videos <queue.json> --backfill-manifest
```

2. Raw + Wiki:

```bash
python validate_video_import.py --kb-root <KB_ROOT> --up <fallback_up> --videos <queue.json> --wiki --backfill-manifest
```

3. RAG rebuild once for the full batch:

```bash
cd <KB_ROOT>/BGE-M3
 -u PYTHONPATH -u PYTHONHOME runtime/env/Scripts/python.exe scripts/rag_pipeline.py
```

4. Three final loops:

```bash
for i in 1 2 3; do
  python validate_video_import.py --kb-root <KB_ROOT> --up <fallback_up> --videos <queue.json> --wiki --rag --backfill-manifest || exit 1
done
```

## Cleanup

After final validation, remove temporary `.m4s` files from the task download work directory. Keep task JSON, compact files, contact sheets, and audit outputs under `tmp/logs/legacy-task-status/<task_id>/` for traceability.
