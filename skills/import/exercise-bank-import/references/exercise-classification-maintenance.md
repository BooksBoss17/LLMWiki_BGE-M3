# raw/exercises 分类整理维护流程

Use this when the exercise bank has drifted from the canonical `SCHEMA.md` taxonomy, especially after mixed batches or broad imports that left items in `综合/综合` or created ad-hoc subdirectories.

## Canonical target

`raw/exercises/` should contain only the SCHEMA 5大类17子分类:

- 力学/运动学、力与平衡、牛顿运动定律、曲线运动、万有引力与航天、功和能、动量
- 电磁学/电场、磁场、电磁感应、交变电流、电路
- 光学/光学、机械振动与波
- 热学/热学
- 综合/综合、2025高考真题

Do not create source-name categories such as `期末复习`, `变压器`, `气体实验`, or `电磁振荡`; map them to the canonical subcategory.

## Safe reclassification pattern

1. **Inventory first**
   - Count Markdown question files per directory.
   - Verify total count and max ID before moving.
   - Identify non-canonical directories and overfull `综合/综合`.

2. **Classify by knowledge, not source**
   - Use frontmatter `knowledge_points`, `source_title`, and the first 1000–2000 chars of the question body.
   - Preserve `综合/2025高考真题` as-is unless the user explicitly asks to split provincial exam papers.
   - Move only when the target is clear; leave genuinely cross-module items in `综合/综合`.

3. **Recommended keyword priority**
   - Put electromagnetic rules before generic wave rules so `LC振荡/振荡电路/电磁波/自感/变压器` goes to `电磁学/交变电流`, not `光学/机械振动与波`.
   - `热学/气体实验` should merge into `热学/热学`.
   - `电磁学/变压器`, `电磁学/交流电与变压器`, `电磁学/电磁振荡` should merge into `电磁学/交变电流`.

4. **Move MD and media together**
   - For each moved question, move body image refs `![](media/...)` and frontmatter `assets:` entries to the destination `media/` directory.
   - Skip invalid refs like `media/` directory-only references.
   - After a partial failure, resume by re-inventorying; do not assume the batch was atomic.

5. **Recover missing assets after moves**
   - Run a full body-image broken-link scan after moving.
   - If `media/image_xxxx.png` is missing, search all of `raw/exercises/`, raw temp conversion dirs, and `source-library/` for the same basename, then copy the best match into the new destination.
   - Re-run until missing image count is 0.

6. **Convert inline data images**
   - Convert `![](data:image/...;base64,...)` into physical files under the question's `media/` directory and replace the markdown with `![](media/<id>_inlineNN_hash.ext)`.
   - Some old files use nested alt text such as `![[1]](data:image/png;base64,...)`; use a permissive regex for the alt text, not only `[^\]]*`.

7. **Fix formula hygiene revealed by full scans**
   - Moving/classification maintenance is a good time to fix historical odd `$` counts caused by truncated answer sections or `$$`/inline OCR artifacts.
   - Do not mask formula problems by excluding old IDs unless the user asked only for a narrow new-import check.

8. **Verification loop**
   - Confirm exactly 17 canonical directories.
   - Confirm total question count unchanged.
   - Confirm ID range is continuous.
   - Confirm: invalid dirs = 0, missing images = 0, `data:image` = 0, odd `$` = 0.
   - Separately report remaining `.wmf` references; those require BeMarkdown 共享 PP-FormulaNet/TexTeller 公式链从原始 DOCX 重新转换，不是简单目录清理。

9. **Sync downstream**
   - Update/create `LLMWiki/concepts/题库分类索引.md`.
   - Append a maintenance entry to `LLMWiki/log.md` and link the index from `LLMWiki/index.md`.
   - Rebuild RAG with the clean environment command:
     `env -u PYTHONPATH -u PYTHONHOME runtime/env/Scripts/python.exe scripts/rag_pipeline.py`.
   - Verify RAG metadata exercise source paths only use the 17 canonical directories.
