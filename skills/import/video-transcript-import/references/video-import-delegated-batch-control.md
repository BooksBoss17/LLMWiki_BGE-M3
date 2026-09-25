# Delegated Batch Control for Large B站 Video Imports

Use this when a video-import request contains many candidates and the control conversation must avoid context blow-up while preserving import quality.

## Core pattern

1. **Main agent is总控, not the log sink**
   - Owns queue, scope, safety boundaries, final acceptance, Wiki/RAG rebuild, and 3-loop validation.
   - Does not read full transcripts, full VLM logs, or large raw markdown unless sampling or investigating a failure.

2. **Create a task state directory first**
   - Example: `tmp/logs/legacy-task-status/video_import_p0_<timestamp>/`
   - Store:
     - `videos_full_queue.json`
     - `videos_active_batch.json`
     - `import_status.json`
     - `context_budget_and_delegation_plan.md`
     - `download_status.json`
     - `transcribe_status.json`
     - `per_video/<BV>_<short-title>/...`

3. **Predict context-risk before importing**

| Stage | Context risk | Handling |
|---|---:|---|
| Candidate discovery | Medium | Delegate to subagents; write candidate JSON; main verifies shape/duplicates. |
| Download/merge | Low | Background script + status JSON; do not read curl logs unless a row fails. |
| Whisper transcription | Medium | Background script; write txt/json; main reads status only. |
| Full transcript analysis | High | Generate compact transcript and visual cue lines; do not load full transcript into main context. |
| Keyframe selection | Medium/High | Per-video subagent reads compact/cue files and writes JSON candidates. |
| VLM/contact-sheet review | High | Per-video subagent/VLM writes `keyframe_vlm_notes.md`; main reads final manifest/issues. |
| Three-layer raw generation | Very High | Per-video worker may draft/write files; main validates and samples. |
| Wiki self-contained pages | High | Script/template generation plus main validation; subagent may suggest wikilinks only. |
| RAG rebuild | Medium | Main runs once per batch; read success/issues, not full logs. |
| 3-loop validation | Medium | Main runs validator and reads issue summaries. |

4. **Batch size**
   - Start with 4–6 P0 videos, not the whole queue.
   - Prefer a coherent first batch that covers the biggest gaps without overloading later VLM/markdown generation.
   - Leave P1/P2 as BV-only queue entries until the first batch passes raw/Wiki/RAG.

5. **Pipeline overlap that is safe**
   - Download can continue while already-downloaded videos begin transcription.
   - Transcription can continue while completed transcripts are compacted and sent to per-video analysis subagents.
   - Do **not** let raw/Wiki/RAG generation get ahead of VLM keyframe validation.

## Per-video subagent contract

After transcription succeeds, create in `per_video/<BV>_<short-title>/`:

- `video_meta.json`
- `transcript_compact_2min.md`
- `visual_cue_lines.md`

Then delegate a bounded task:

- Read only compact/cue/meta, not the full transcript.
- Write only:
  - `chapter_plan.md`
  - `keyframe_candidates.json`
  - `asr_risk_notes.md`
- Do not download, transcribe, extract frames, write raw/Wiki, or run RAG.

After frames are extracted, delegate another bounded task if needed:

- Review contact sheets / VLM outputs.
- Write `keyframe_vlm_notes.md` and `final_keyframes.json`.
- Mark incomplete/obscured frames and suggest retry windows.

## Main-agent acceptance gates

Main must personally verify or run scripts for:

- `download_status.json`: every active BV `ok=true`, format/video/audio durations match, audio decode clean.
- `transcribe_status.json`: txt/json exist, JSON parses, segment count nonzero, Whisper info duration close to video duration.
- Raw layer: three markdown files + media, correct frontmatter, no raw temp files.
- Knowledge notes: no images, no timestamp noise, no ads, formulas corrected.
- Lesson plan: teaching flow and keyframe references present.
- Wiki: self-contained parent/children pages, no visible raw links/transclusion.
- RAG: metadata has chunks from new knowledge notes with `source_type=transcript`.
- Three validation loops return zero issues.

## Practical trigger phrases

If the user asks to “逐个导入” many videos or asks whether subagents can help before import, create the delegation plan before starting downloads. If the user later asks “检查进度”, summarize counts from status files and continue the next safe stage immediately.