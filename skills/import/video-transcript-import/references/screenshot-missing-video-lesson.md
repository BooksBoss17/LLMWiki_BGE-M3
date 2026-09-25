# HE screenshot missing-video lesson (2026-07-02)

Use this as a candidate-discovery pitfall for Bilibili teaching-video imports.

## What happened

A UP-丁 screenshot showed a visible row:

- `[高中物理]力的合成与分解（标题已脱敏）...`
- `2024年11月10日`
- `33.1万播放`

Keyword searches for `UP-丁 力的合成与分解` and related terms failed to surface the exact video, so it was left as an unresolved screenshot candidate. Later the user supplied `BV<已脱敏>`, and `video_detail` confirmed it exactly matched the screenshot:

- `BV<已脱敏>`
- title: `[高中物理]力的合成与分解（标题已脱敏）`
- owner: `UP-丁`
- publish date: `2024-11-10`
- duration: `19:49`
- cid: `<已脱敏>`

## Durable rule

When working from screenshots, **do not treat Bilibili keyword-search misses as proof that a visible video does not exist**. Search can miss exact HE videos even when title/date/play-count are clear.

If a screenshot row is visually plausible but not resolved:

1. Record it explicitly under `unresolved_candidates` in the candidate audit.
2. Keep enough visual evidence: title fragment, date, play count, duration if visible, and screen position/context.
3. Tell the user it is unresolved rather than absent.
4. If the user later supplies a BV, run `video_detail` and local BV/title dedupe before import.
5. If confirmed new and teaching-relevant, import as a normal video and update the audit.

## Import notes from this case

- Use `scripts/bilibili_playurl_download.py` for the B站 412/VPN/CDN fallback path.
- Use `tmp/tasks/<batch>/` for videos.json/status/download work; do not create root `_tmp`.
- After import, run `validate_video_import.py --wiki --rag --global-wiki` for 3 loops.
- This video added 11 transcript chunks after RAG rebuild (`4401` total chunks in that session).

## VLM target-setting lesson

For video keyframes, a frame can be valid for a **local teaching target** even if it does not contain the entire derivation. Avoid overly broad VLM prompts such as “judge whether the whole knowledge point is complete” when the frame only carries:

- a title/transition (usually reject/re-sample),
- a local definition or example setup,
- a mathematical prerequisite,
- a problem statement before the full solution.

Write frame-specific targets, e.g. “三角函数基础铺垫” or “题目与引入完整，解题过程后续展开”. Reject title-only frames and re-sample to concrete content frames.
