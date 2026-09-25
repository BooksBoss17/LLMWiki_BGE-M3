# Exam Score Import Workflow

Use this reference only after `student-data-import` is selected for an exam score or answer-card workflow.

## Command Patterns

Roster normalization:

```bash
python StudentDataSQL/scripts/preprocess_roster_xlsx.py --xlsx <source.xlsx> --term-id <term_id> --class-id <class_id> --class-name <class_name> --json
```

Roster import:

```bash
python StudentDataSQL/scripts/import_roster.py --real-data --csv StudentDataSQL/runtime/imports/normalized/<roster>.csv --json
```

School score sheet and answer-card normalization:

```bash
python StudentDataSQL/scripts/preprocess_school_exam_xls.py --source-dir <source_dir> --class-code <class_id> --exam <exam_key> --copy-answer-cards --json
```

The score preprocessor requires the canonical roster CSV, defaulting to `StudentDataSQL/runtime/imports/normalized/<class_id>_<term_id>_roster.csv`. Use `--roster-csv <roster.csv>` only when intentionally overriding that default. The score sheet seat number is only a lookup key; normalized score CSVs must copy `student_id` from the roster instead of generating a new ID.

Exam item-score import:

```bash
python StudentDataSQL/scripts/import_exam_csv.py --real-data --csv StudentDataSQL/runtime/imports/normalized/<scores_ready>.csv --json
```

Answer-card manifest import:

```bash
python StudentDataSQL/scripts/import_answer_card_manifest.py --real-data --csv StudentDataSQL/runtime/imports/normalized/<manifest_present>.csv --json
```

Encrypted source-file archival for real data:

```bash
python StudentDataSQL/scripts/import_file_artifacts.py --real-data --root StudentDataSQL/runtime/imports --delete-after --json
```

Use `--dev` only for synthetic fixtures containing no real students. Real imports and real-data preprocessing require `--real-data` and `STUDENT_DATA_DB_KEY`; without the key, refuse before creating persistent real CSV/JSON/PNG staging files.

## Required Checks

- Roster fields include `student_id`, `family_name`, `class_id`, `seat_no`, `term_id`, and `gender`; `gender=U` is allowed only when explicitly unknown.
- Score rows include `assessment_id`, `class_id`, roster-derived `student_id`, `total_score`, `class_rank`, `grade_rank`, `score_band`, and per-question score/max/question fields.
- School score sheets require `class_rank`, `grade_rank`, and `score_band`; stop if the source lacks those fields.
- Every score and answer-card manifest `student_id` must exist in the canonical roster import.
- Full names, school numbers, exam numbers, student-status numbers, and guardian/contact data remain forbidden in normalized CSVs, logs, safe views, Git, `raw/`, `LLMWiki/`, and `BGE-M3`.
- Per-question metadata from `StudentDataSQL/runtime/exam-reports/` should be copied when available: `Q*_kp_id`, `Q*_kp_title`, `Q*_key_point`, and `Q*_difficulty_point`; `Q*_wiki_path` remains optional.
- Present-only answer-card imports must use manifest rows with `artifact_status=present`.
- After encrypted source-file archival succeeds, `StudentDataSQL/runtime/imports/normalized/` and `StudentDataSQL/runtime/imports/redacted_answer_cards/` must not retain real CSV/JSON/PNG staging files.

## Recovery

If interrupted before encrypted archival, resume from the files under `StudentDataSQL/runtime/imports/normalized/` and `StudentDataSQL/runtime/imports/redacted_answer_cards/`, then run validation before continuing. Do not rely on chat history as source truth.
