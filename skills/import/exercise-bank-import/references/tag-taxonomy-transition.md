# Tag Taxonomy Transition for Exercise Bank

Use this reference when the exercise bank switches from directory-based classification to frontmatter `knowledge_points` tagging, or when existing questions need to be re-tagged under a new taxonomy.

## Core policy

- `raw/exercises/` is physical storage only. Do **not** move question files just to express knowledge classification.
- The canonical source for standard knowledge tags is the knowledge-base root file `skills/题库知识点标签目录.md`.
- Each question's frontmatter `knowledge_points` is the standard knowledge classification source of truth.
- Prefer complete paths (`一级/二级/三级`) when possible; allow multiple standard tags for comprehensive, experimental, or cross-module questions.
- In addition to standard `knowledge_points`, the AI may add free-form auxiliary tags under `ai_extra_tags` based on its own judgment (methods, problem features, common pitfalls, ability dimensions, teaching use). These do not need to appear in the taxonomy and should not be mixed into `knowledge_points` unless the taxonomy is explicitly expanded.
- Keep IDs, question body, answer, explanation, and media untouched during tag migration unless a separate quality issue is explicitly being fixed.

## Safe migration workflow

1. **Parse the taxonomy**
   - Read `skills/题库知识点标签目录.md`.
   - Build the set of valid tags including first-, second-, and third-level paths.
   - Treat second-level tags as valid only when the taxonomy has no useful third-level distinction for the question, or when the question cannot be safely refined.

2. **Generate a migration plan first — do not write files yet**
   - Scan every `raw/exercises/**/*.md` question.
   - Extract frontmatter, source title, question type, existing `knowledge_points`, and concise excerpts from `## 题目`, `## 答案`, `## 详解`.
   - Combine:
     - old physical directory as weak prior only;
     - source title hints;
     - targeted keyword rules;
     - question type (`E` should trigger experiment-tag consideration).
   - Write audit artifacts such as:
     - `_tmp_tag_migration/exercise_tag_migration_plan_v2.jsonl`
     - `_tmp_tag_migration/low_confidence_questions_v2.json`
     - `_tmp_tag_migration/migration_report_v2.md`

3. **Avoid over-broad rules**
   - Do not let generic words like `时间`, `速度`, `加速度`, `重力加速度` alone trigger specific experiment tags.
   - Gate experiment labels behind `question_type: E` or clear experimental context (`实验`, `探究`, `验证`, `测量`, `器材`, `数据`, `读数`, `误差`, etc.).
   - Use directory mapping only as a coarse prior; do not blindly copy raw directory names into `knowledge_points`.

4. **LLM-review only the hard subset**
   - Send only low-confidence or ambiguous questions to LLM review, not the whole bank.
   - Include current tags, rule suggestions, question/answer/explanation excerpts, source title, and the taxonomy file path.
   - Require result JSON keyed by question ID:
     ```json
     {
       "MC0000001": {
         "tags": ["一级/二级/三级"],
         "confidence": 0.88,
         "notes": "short reason"
       }
     }
     ```
   - If large batches time out, split them into smaller retry batches (about 20–25 questions each) rather than changing the task logic.

5. **Validate before applying**
   - Check result JSON parses.
   - Check expected IDs exactly match result IDs (no missing/extra/duplicates).
   - Check every tag is in the parsed taxonomy.
   - Check 1–5 tags per question.
   - Manually inspect any remaining first-level generic tags before writing.

6. **Backup, then apply**
   - Create a backup directory such as `_backup_before_tag_migration_YYYY-MM-DD/` or `_backup_before_tag_refinement_YYYY-MM-DD/`.
   - Copy each target MD before modifying it.
   - Only update frontmatter fields:
     - `knowledge_points`
     - `classification_confidence`
     - `classification_notes`
     - `tag_schema`
   - Use a stable marker, e.g. `tag_schema: 题库知识点标签目录.md@YYYY-MM-DD`.

7. **Full validation after applying**
   - Question count and ID continuity.
   - All `knowledge_points` are non-empty lists.
   - All tags valid under taxonomy.
   - Old raw-only directory labels are cleared (e.g. `力学/运动学`, `热学/热学`, `综合/综合`) unless the string is also an intentional valid taxonomy tag.
   - Image links, WMF references, data URI, and `$` closure remain clean.
   - Experiment questions have a concrete experiment tag where the taxonomy supports one, plus relevant subject-matter tags.

8. **Second-round refinement**
   - After first migration, scan for:
     - first-level generic tags (`力学`, `电磁学`, `热学`, `光学`, `物理实验`, etc.);
     - second-level tags with available third-level children but no child selected;
     - experiment questions missing a concrete experiment tag;
     - low confidence values.
   - Send only this candidate set to LLM review.
   - Accept remaining second-level tags when the taxonomy itself has no third-level child (e.g. a standalone `光学/激光`) or when the item is genuinely a history/method category.

9. **Wiki and RAG sync**
   - Update `LLMWiki/concepts/题库分类索引.md` to describe the tag-index status and top tag distribution.
   - Create/update audit pages such as:
     - `题库知识点标签迁移-YYYY-MM-DD.md`
     - `题库知识点标签二轮精修-YYYY-MM-DD.md`
   - Append `LLMWiki/log.md`.
   - Rebuild RAG with the clean environment command:
     ```bash
     cd "<KB_ROOT>/BGE-M3"
     env -u PYTHONPATH -u PYTHONHOME runtime/env/Scripts/python.exe scripts/rag_pipeline.py
     ```

## Common pitfalls

- **False experiment inflation**: generic kinematics words cause many questions to be mislabeled as measurement experiments. Gate experiment tags strictly.
- **Old-directory false positives**: some strings are both old directory labels and valid new taxonomy tags (e.g. `力学/牛顿运动定律`, `电磁学/电磁感应`). Only flag old labels that are not valid taxonomy entries.
- **LLM self-report is not proof**: after delegation, independently read and validate the result files before applying.
- **Timeout recovery**: retry failed LLM batches in smaller chunks; do not partially apply successful batches until the full target set is validated.
- **Over-perfecting first pass**: first pass should make every question valid and usable; second pass refines coarse tags. This is safer than trying to perfectly classify 1000+ questions in one step.
