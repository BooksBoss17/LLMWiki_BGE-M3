# Large Batch Video Import: Controller + Subagent Orchestration

Use this reference when a video-import request contains many candidates or the user asks to continue importing from a large queue.

## Problem

B站视频导入容易撑爆总控上下文，主要不是下载/转写本身，而是：完整逐字稿、VLM逐帧审阅、三层Markdown生成、Wiki自包含拼装、RAG/质检日志。用户希望总控对话保持清醒：拆批、分派、验收，不把所有执行日志灌进主上下文。

## Default orchestration pattern

1. **Main controller creates a task directory** under `tmp/logs/legacy-task-status/<task_id>/` and writes:
   - `videos_full_queue.json` — all approved candidates;
   - `videos_active_batch.json` — first small active batch;
   - `import_status.json` — state and boundaries;
   - `context_budget_and_delegation_plan.md` — expected context risks and delegation plan.
2. **Batch size**: start with 4–6 high-priority videos, not the full queue. Prefer a coherent mix of missing knowledge points; defer questionable copyright/source videos for separate review.
3. **Background scripts, not subagents**, handle long IO/GPU work:
   - download/merge/integrity gates via `bilibili_playurl_download.py` + `download_status.json`;
   - faster-whisper via `transcribe_video_batch.py` + `transcribe_status.json`.
   The main agent reads status summaries, not full logs or full transcripts.
4. **Per-video subagents** are used only after files exist and inputs can be bounded:
   - compact transcript → chapter structure, ASR-risk terms, candidate keyframe times;
   - contact sheet/keyframes → VLM completeness notes and final keyframe manifest;
   - VLM-approved frames + compact transcript → three-layer raw drafts/files.
5. **Main controller always verifies** subagent outputs before claiming success:
   - files exist at expected paths;
   - frontmatter is valid;
   - knowledge notes have no images, timestamps, or ads;
   - lesson plan references final keyframes only;
   - Wiki pages are self-contained and contain no visible raw links;
   - RAG metadata contains transcript chunks;
   - three validation loops end with zero issues.

## Context budget guardrails

- Do not load full transcripts into the main conversation unless debugging a narrow segment. Generate a compact `[MM:SS]`/2-minute/section summary first.
- Do not paste full VLM OCR logs into the main conversation. Store `keyframe_vlm_notes.md`; main agent reads only a result table and issues.
- Do not let subagent self-reports substitute for verification. Read/parse the written JSON/MD files or run validation scripts.
- Limit concurrent API-heavy subagents to 1–2 to avoid rate-limit cascades.
- When context approaches the handoff threshold, write/update a controller continuation card with active batch, completed videos, failed videos, pending validations, and process IDs.

## What to delegate vs keep in main

| Work | Delegate? | Reason |
|---|---:|---|
| Candidate discovery / BV recording / local de-dup | Yes | Search result volume is high; output is bounded JSON. |
| Download/merge | No | Mechanical script task; logs should stay out of context. |
| faster-whisper transcription | No | GPU/script task; output files are large. |
| Compact transcript + keyframe proposal | Yes | Reasoning-heavy but bounded by compact input and JSON output. |
| Contact sheet/VLM completeness review | Yes | Visual reasoning; write notes/manifest, not chat logs. |
| Three-layer raw draft generation | Yes, per video | Large but isolated; main must verify. |
| Wiki/RAG final sync | Main controller | Cross-library side effects require final centralized verification. |
| Three-loop quality gate | Main controller | Final acceptance criterion cannot be outsourced. |

## User preference encoded

For this user, large video-import tasks should proceed autonomously in this pattern after an approved candidate list: record candidates first, return to main controller, then import in small verified batches. Do not ask for per-video confirmation unless a source/copyright/duplication ambiguity changes the decision.