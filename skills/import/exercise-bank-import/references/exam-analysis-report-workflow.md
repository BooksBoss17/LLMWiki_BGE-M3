# Exam Paper Report Workflow

Use this reference when importing a complete monthly exam, midterm, final exam,
mock exam, college-entrance exam paper, or similar whole paper where the user
asks for question-bank import plus exam-paper reports.

## Output Boundary

Create three durable outputs after per-question Markdown generation:

- Question bank files: `raw/exercises/<source-or-storage-dir>/{ID}.md`
- Whole-paper question analysis report:
  `raw/exam_analysis_reports/<exam-name>试卷分析报告.md`
- Exam design analysis report:
  `raw/lectures/<exam-name>试卷设计分析/<exam-name>试卷设计分析_设计.md`

The two report types have different library boundaries:

- `试卷分析报告` is the whole-paper per-question analysis report. It contains
  every question, solution, knowledge points, key focus, and pitfalls. Keep it
  in `raw/exam_analysis_reports/` and copy it to
  `StudentDataSQL/runtime/exam-reports/<exact-exam-name>/`. Do not import this report
  into Wiki or BGE.
- `试卷设计分析报告` is the paper-design and teaching-design report, similar to
  lecture design documents. It summarizes paper structure, design intent,
  blueprint, difficulty gradient, skill coverage, and lecture/review strategy.
  It must be imported into Wiki and BGE.

## Naming Rules

All exam reports copied to StudentDataSQL must identify the exact exam in both
folder and filename. Prefer:

```text
StudentDataSQL/runtime/exam-reports/<school>_<school-year-term>_<grade-subject-exam>/<school>_<school-year-term>_<grade-subject-exam>_试卷分析报告.md
```

Do not store full student names, school numbers, exam numbers, student-status
numbers, or guardian/contact data in the canonical `_试卷分析报告.md` copied to
`StudentDataSQL/runtime/exam-reports/`. If later score analysis needs rankings,
original answer-card filename mappings, or individual answer records, keep them
as separate local StudentDataSQL analysis attachments under the exact exam
folder; never copy them into `raw/`, `LLMWiki/`, `BGE-M3`, `skills/`, Git, or
public/classroom-facing exports.

## Per-Question Analysis Report Format

The ordinary `试卷分析报告` must contain one section per imported question, in
original paper order. Use the generated stable question ID in each heading.

```markdown
## 第 N 题（ID）

### 题目

完整题目。需要图像时引用题库 media，避免重复保存同一图片。

### 解析

完整解析。原卷无解析时，agent 必须自行补写，不得写“见原卷/无解析/略”。

### 题目所含知识点

- 标准 knowledge_points 标签

### 题目重点

本题主要考查的模型、方法或能力。

### 易错点

学生最容易错的判断、公式适用条件、符号方向或图像含义。
```

## Exam Design Analysis Report Format

The `试卷设计分析报告` should not duplicate all question text. It should be a
teacher-facing design document with sections such as:

- 设计定位
- 试卷结构
- 双向细目表
- 难度梯度设计
- 命题重点
- 易错设计点
- 讲评设计建议
- 后续教学反馈点

Save it under `raw/lectures/<exam-name>试卷设计分析/<exam-name>试卷设计分析_设计.md`.
The `_设计.md` suffix is intentional: the current RAG pipeline already indexes
these files as `source_type=lesson_design`, so this path avoids changing the
locked RAG pipeline while keeping exam design reports retrievable.

## Workflow

1. Finish normal exercise import through `paired_questions.json` and
   per-question Markdown generation.
2. Allocate global IDs before writing reports so report sections can reference
   stable IDs and media paths.
3. Assign `knowledge_points` from
   `skills/taxonomy/exercise-knowledge-tags/SKILL.md`. Older notes may mention
   `skills/题库知识点标签目录.md`; treat that as historical wording unless the file
   exists in the current repo.
4. Run the RAG parser compatibility precheck on generated question Markdown:
   `python skills/import/exercise-bank-import/scripts/validate_exercise_rag_compat.py --kb-root . raw/exercises/<exam-dir> --json`.
   Fix heading spacing or frontmatter list formatting before writing reports or
   rebuilding RAG.
5. Run targeted Wiki + BGE/RAG retrieval for the paper's major themes. Keep
   only concise evidence in task notes; do not paste long RAG logs into the
   reports or main agent context.
6. Write the ordinary per-question report to `raw/exam_analysis_reports/`.
7. Copy the ordinary per-question report to `StudentDataSQL/runtime/exam-reports/`
   with an exact-exam-name folder and filename.
8. Write the exam design report to the `raw/lectures/.../_设计.md` path.
9. Sync Wiki entry points for the design report and import summary. Wiki pages
   must be self-contained and must not expose raw/source paths as user-facing
   navigation.
10. Rebuild RAG and verify:
   - all new question IDs are present in metadata/chunks
   - new question chunks have non-empty `text` and non-empty `knowledge_points`
     by running `validate_exercise_rag_compat.py --check-chunks`
   - the exam design report is present in metadata/chunks, normally as
     `source_type=lesson_design`
   - the ordinary `raw/exam_analysis_reports/` report is not required to appear
     in RAG

## Validation Gates

Run at least three validation loops and finish with zero unresolved issues.

- Question-bank gates: unique IDs, required frontmatter, `## 题目/答案/详解`,
  valid image links, no WMF/data URI, balanced math delimiters, and passing
  `validate_exercise_rag_compat.py` before and after RAG rebuild.
- Per-question report gates: exactly one report file, exactly one section per
  imported question, each section has `题目/解析/题目所含知识点/题目重点/易错点`, no
  placeholders, and all image links resolve.
- Exam design report gates: design report exists, has blueprint/structure and
  teaching-design sections, references stable question IDs, and is indexed by
  RAG after rebuild.
- StudentDataSQL gates: report copy exists under `StudentDataSQL/runtime/exam-reports/`,
  filename identifies the exact exam, and the canonical `_试卷分析报告.md` remains a
  paper-level report. Any rankings, original answer-card filename mappings, or
  individual answer records needed for later score analysis must be separate
  local StudentDataSQL attachments and must not enter teaching RAG or public
  outputs.
- Cross-check gates: report IDs match generated question IDs, report question
  count equals `paired_questions.json`, answers match answer table or official
  solution, and source paths resolve.

## Notes

- For exam papers with multiple modules, prefer source-oriented storage under
  `raw/exercises/综合/<paper-title>/`; let `knowledge_points` express
  classification.
- If the PDF text layer is mojibake or formula-fragmented, render pages and use
  visual/OCR extraction as the source of truth.
- If report generation happens after question import, do not recover state from
  a long chat transcript. Read `paired_questions.json`, generated question
  files, and the import manifest/status files.
