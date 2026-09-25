# Local Video Coverage Baseline for Candidate Discovery

Use this reference when the user asks to scan the local `LLMWiki_BGE-M3` video/knowledge coverage, build a deduplication baseline, or identify missing knowledge-point coverage before choosing new B站 videos to import.

## Goal

Produce a handoff-ready report for the main agent that answers:

- Which B站 videos are already imported locally?
- Which BV numbers, normalized titles, UP names, and Wiki/raw paths should be treated as the deduplication baseline?
- Which高中物理 modules are strongly covered vs. weak/missing in video form?
- Which candidate directions should be preferred for UP-乙 / UP-甲 / other UPs?

This is a **read-only discovery step**. Do not download, transcribe, extract frames, edit Wiki pages, or rebuild RAG unless the user explicitly expands scope.

## Canonical output location

Create a timestamped task folder under the KB temp/status area, for example:

```text
tmp/logs/legacy-task-status/video_candidate_discovery_<YYYYMMDD_HHMMSS>/
```

Recommended files:

```text
scan_local_coverage.py          # deterministic local scan script
local_coverage_scan.json        # machine-readable inventory
local_coverage_auto_table.md    # raw/automatic tables; may contain keyword false positives
local_coverage_report.md        # handoff-ready, human-corrected report
```

## Scan scope

Read from:

```text
raw/transcripts/
LLMWiki/concepts/
LLMWiki/_meta/
LLMWiki/index.md
```

Exclude `_meta/` backup copies from active coverage counts, but read `_meta/*.md` audit/candidate reports as historical clues. Treat current `raw/` + active `LLMWiki/concepts/视频-*.md` as authoritative when they conflict with older `_meta` reports.

## Extraction fields

For every video directory / Wiki parent page, extract at least:

- short local title
- raw UP directory
- displayed UP / `up_master`
- BV number (`BV[0-9A-Za-z]{10}`)
- duration when available
- raw directory path
- raw knowledge-note path
- Wiki parent path
- booleans: has transcript, has knowledge notes, has lesson plan, has Wiki parent
- keyframe count from `media/keyframe_*.jpg`
- rough physics module classification

Also scan Wiki parent pages and child pages for UP-name aliases. In this KB, `教物理的UP-甲` and `只教物理的UP-甲` can refer to the same teacher/account lineage; merge them for deduplication unless the user asks to distinguish accounts.

## Suggested module buckets

Use stable, course-level buckets rather than a long list of one-off topics:

- 运动学
- 相互作用/平衡
- 牛顿运动定律
- 曲线/圆周/万有引力
- 功和能
- 动量
- 静电场
- 恒定电流
- 电磁感应
- 磁场/洛伦兹力
- 交变电流/变压器
- 热学
- 光学
- 近代物理
- 实验

Keyword matching is only a first pass. Expect false positives from formulas, examples, and cross-topic综合题. The final `local_coverage_report.md` should be manually corrected into high-signal judgments such as “已有较多”, “已有但可补专题”, “明显缺视频”, or “理论偏少”.

## Report shape

The handoff report should include:

1. **Scan summary** — raw directory count, Wiki parent count, UP distribution, and three-layer completeness.
2. **Imported video inventory** — grouped by UP and topic, with BV numbers.
3. **Module coverage table** — coverage judgment, representative local videos, and missing directions.
4. **Deduplication baseline** — BV list, title-normalization rules, UP alias rules, and paths to search.
5. **Keyword/path table** — module-specific search terms and high-signal local pages.
6. **Candidate guidance** — e.g. UP-乙 for技巧/母题/速通; UP-甲 for系统推导/长课.
7. **One-line conclusion** for the main agent.

## Deduplication rules

1. **BV first**: if a candidate BV is already in local raw/Wiki, treat it as already imported or as a repair target, not a new import.
2. **Normalize titles second**: strip marketing phrases and punctuation such as “保姆级”, “秒解”, “高考物理UP-乙”, “实验班版”, “上集”, repeated exclamation marks, and long descriptive titles. Compare against local short titles.
3. **Merge UP aliases**: search both `教物理的UP-甲` and `只教物理的UP-甲` for UP-甲 candidates.
4. **Search both layers**:
   - `raw/transcripts/<UP>/<title>/`
   - `LLMWiki/concepts/视频-<title>.md`
   - `LLMWiki/concepts/视频-<title>-知识笔记.md`
   - `LLMWiki/concepts/视频-<title>-教学简案.md`
5. **Treat `_meta` as historical**: older candidate reports can be stale after later imports. Verify against current raw/Wiki.

## Common local coverage conclusions observed

As of the session that produced this reference, the local video layer was strong in mechanics foundations, force models, 平抛/天体, and 电磁感应, while video coverage was weaker in:

- 动量
- 交变电流/变压器
- 恒定电流 pure-circuit theory
- systematic 热学
- systematic 磁场/洛伦兹力
- geometrical 光学
- non-electrical lab experiments

Do not hard-code these as permanent facts. Re-scan current raw/Wiki before reporting; use them as examples of how to phrase the final gap list.

## Verification

Before finalizing:

- Confirm the output files exist in the task folder.
- Confirm current raw video count and active Wiki parent count are both reported.
- Confirm all videos have the three expected raw Markdown layers and a Wiki parent, or explicitly list exceptions.
- Confirm `_meta` historical candidates that became imported are not presented as still missing.
- State explicitly that no import/RAG actions were taken when the task was discovery-only.
