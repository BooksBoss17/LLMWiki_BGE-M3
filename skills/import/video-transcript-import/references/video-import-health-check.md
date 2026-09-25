# Video Import Health Check / 视频导入后健康检查

Use after a video-import session was resumed in another conversation, after context loss, or whenever the user asks whether the knowledge base is still healthy.

## Scope

Check the current effective knowledge base, not historical backups:

- raw/transcripts/<UP主>/<视频>/三层文件、media、VLM notes、keyframe manifest
- source-library/transcripts/ 下的 MP4/txt/json；当前主约定是扁平命名 `<UP主>_<BV号>.*`，也兼容旧批次 `source-library/transcripts/<UP主>/...` 子目录
- LLMWiki/concepts/视频-* Wiki pages
- BGE-M3/runtime/index/metadata.json and transcript chunks
- Current Wiki graph and assets

Ignore all `LLMWiki/_meta/` audit/history/backup folders when judging current Wiki health. They are not active graph content; do not validate their wikilinks, image assets, page sizes, or raw-pointer style as current issues.

## Current-Wiki rules

- `frontmatter.sources: ../raw/...` is allowed as provenance metadata.
- Visible body raw pointers are not allowed: `^[../raw/...]`, `## raw 原文`, `[查看](../raw/...)`, or body links to raw `.md`.
- Do not count any page under `LLMWiki/_meta/` as current broken links, oversize pages, missing assets, or raw-pointer violations; `_meta` is audit/history storage, not active Wiki graph content.
- Concepts pages should remain below 20KB; split video pages into overview + `-知识笔记` + `-教学简案` pages when needed.

## Video-level checks

For each expected BV/title:

1. MP4 exists（扁平 `<UP主>_<BV号>.mp4` 或旧式 UP 子目录均可）and ffprobe format/video/audio durations match expected duration within ~3–5 seconds.
2. txt/json transcript files exist; JSON is a non-empty list; last segment should be plausible. Do not reject solely because the last segment ends slightly before video end if WAV/info duration was complete.
3. raw directory contains:
   - `<标题>.md`
   - `<标题>_知识笔记.md`
   - `<标题>_教学简案.md`
   - `media/keyframe_*.jpg`
   - `keyframe_vlm_notes.md`
   - `keyframe_candidates.json`
4. Knowledge notes contain no image markdown and no timestamp noise.
5. Wiki pages exist: `视频-<标题>.md`, `视频-<标题>-知识笔记.md`, `视频-<标题>-教学简案.md`.
6. RAG metadata has `source_type='transcript'` chunks matching the title or BV.

## Backfill rule for keyframe_candidates.json

If `keyframe_candidates.json` is missing but final `media/keyframe_*.jpg` and `keyframe_vlm_notes.md` exist and VLM notes state final frames are complete, backfill `keyframe_candidates.json` from the actual media filenames:

```json
{
  "bvid": "BV...",
  "title": "...",
  "generated_by": "kb health check backfill",
  "note": "Backfilled from final media/keyframe_*.jpg files because VLM notes and final frames existed but keyframe_candidates.json was missing.",
  "vlm_notes": "keyframe_vlm_notes.md",
  "vlm_all_complete": true,
  "final_keyframes": [
    {"index": 1, "time_s": 70, "file": "media/keyframe_01_70s.jpg", "reason": "final keyframe; see keyframe_vlm_notes.md", "vlm_complete": true}
  ],
  "retries": []
}
```

This is an audit-manifest repair only. It does not modify knowledge notes or RAG content, so RAG rebuild is not required.

## RAG checks

- `metadata.json.total_chunks == len(metadata.json.chunks)`
- `metadata.json.total_vectors == len(metadata.json.chunks)`
- transcript chunks exist for every newly imported video
- source_type counts are plausible and no chunk count mismatch appears

## Report format

Report separately:

- content issues requiring repair (missing raw/Wiki/RAG content, corrupt MP4, broken links)
- audit-only gaps (e.g. missing keyframe manifest when VLM notes and frames are present)
- whether RAG rebuild is required
