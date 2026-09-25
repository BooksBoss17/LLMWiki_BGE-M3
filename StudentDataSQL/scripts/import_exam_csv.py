#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from student_data_common import (
    add_mode_args,
    apply_migrations,
    audit,
    connect_db,
    main_guard,
    print_json,
    read_csv,
    record_import_batch,
    require_columns,
    resolve_args_db,
    StudentDataError,
    require_existing_student_id,
)


METHOD_ERROR_TYPES = {
    "",
    "concept_confusion",
    "model_selection_error",
    "condition_misread",
    "direction_sign_error",
    "formula_condition_error",
    "calculation_error",
    "process_incomplete",
    "expression_unit_error",
    "careless_or_omission",
    "unknown",
}


def score_columns(row: dict[str, str]) -> list[str]:
    return [
        key
        for key in row
        if re.fullmatch(r"Q[0-9A-Za-z_]+_score", key) and not key.endswith("_max_score")
    ]


def parse_float(value: str | None) -> float | None:
    text = (value or "").strip()
    if not text or text in {"-", "None", "null", "缺", "缺考"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_int(value: str | None) -> int | None:
    text = (value or "").strip()
    if not text or text in {"-", "None", "null", "缺", "缺考"}:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def normalized_answer(value: str | None) -> str:
    text = (value or "").strip().upper().replace("，", ",")
    if not text:
        return ""
    parts = [part.strip() for part in text.split(",") if part.strip()]
    return ",".join(sorted(parts)) if len(parts) > 1 else text


def infer_is_correct(score: float | None, item_max: float, response: str, correct_answer: str) -> int | None:
    if score is None:
        return None
    if correct_answer and response:
        return int(normalized_answer(response) == normalized_answer(correct_answer))
    return int(score >= item_max)


def validate_method_error(value: str | None, *, item_no: str) -> str | None:
    text = (value or "").strip()
    if text not in METHOD_ERROR_TYPES:
        raise StudentDataError(f"invalid method_error_type for {item_no}: {text}")
    return text or None


def json_or_empty(value: str | None, *, field: str, item_no: str) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise StudentDataError(f"{field} for {item_no} must be valid JSON") from exc
    return text


def run() -> int:
    parser = argparse.ArgumentParser(description="Import exam item scores CSV into StudentDataSQL.")
    add_mode_args(parser, default_dev=False)
    parser.add_argument("--csv", required=True, help="exam item result CSV path")
    parser.add_argument("--actor", default="teacher", help="audit actor")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path, real_data = resolve_args_db(args)
    rows = read_csv(Path(args.csv))
    require_columns(rows, ["assessment_id", "class_id", "title", "assessed_on", "student_id", "total_score"])
    item_cols = score_columns(rows[0])
    if not item_cols:
        raise StudentDataError("CSV must include item score columns such as Q1_score")

    conn = connect_db(path, real_data=real_data)
    apply_migrations(conn)
    first = rows[0]
    max_score = float(first.get("max_score") or 0) or None
    assessment_item_ids: dict[str, str] = {}
    conn.execute(
        """
        INSERT OR IGNORE INTO assessments(
            assessment_id, class_id, term_id, title, assessment_type, assessed_on, max_score, source_ref
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            first["assessment_id"],
            first["class_id"],
            first.get("term_id"),
            first["title"],
            first.get("assessment_type") or "exam",
            first["assessed_on"],
            max_score,
            first.get("source_ref"),
        ),
    )
    for idx, col in enumerate(item_cols, 1):
        item_key = col.removesuffix("_score")
        item_no = first.get(f"{item_key}_item_no") or item_key
        question_id = first.get(f"{item_no}_question_id") or f"question_{first['assessment_id']}_{item_no}"
        question_id = first.get(f"{item_key}_question_id") or first.get(f"{item_no}_question_id") or question_id
        kp_id = first.get(f"{item_no}_kp_id")
        kp_id = first.get(f"{item_key}_kp_id") or kp_id
        item_max = float(first.get(f"{item_key}_max_score") or first.get(f"{item_no}_max_score") or 1)
        conn.execute(
            """
            INSERT OR IGNORE INTO questions(question_id, title, question_type, max_score)
            VALUES (?, ?, ?, ?)
            """,
            (
                question_id,
                first.get(f"{item_key}_title") or first.get(f"{item_no}_title") or item_no,
                first.get(f"{item_key}_question_type") or "exam_item",
                item_max,
            ),
        )
        if kp_id:
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_points(kp_id, title, wiki_path) VALUES (?, ?, ?)",
                (
                    kp_id,
                    first.get(f"{item_key}_kp_title") or first.get(f"{item_no}_kp_title") or kp_id,
                    first.get(f"{item_key}_wiki_path") or first.get(f"{item_no}_wiki_path"),
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO question_knowledge_points(question_id, kp_id, weight, role, confidence)
                VALUES (?, ?, 1.0, 'primary', 1.0)
                """,
                (question_id, kp_id),
            )
        conn.execute(
            """
            INSERT OR IGNORE INTO assessment_items(
                assessment_item_id, assessment_id, question_id, item_no, max_score,
                display_order, correct_answer, scoring_points_json, method_tags_json,
                key_point, difficulty_point, exam_report_ref
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{first['assessment_id']}_{item_key}",
                first["assessment_id"],
                question_id,
                item_no,
                item_max,
                idx,
                first.get(f"{item_key}_correct_answer") or None,
                json_or_empty(first.get(f"{item_key}_scoring_points_json"), field="scoring_points_json", item_no=item_no),
                json_or_empty(first.get(f"{item_key}_method_tags_json"), field="method_tags_json", item_no=item_no),
                first.get(f"{item_key}_key_point") or None,
                first.get(f"{item_key}_difficulty_point") or None,
                first.get(f"{item_key}_exam_report_ref") or first.get("exam_report_ref") or None,
            ),
        )
        conn.execute(
            """
            UPDATE assessment_items
            SET key_point = COALESCE(NULLIF(?, ''), key_point),
                difficulty_point = COALESCE(NULLIF(?, ''), difficulty_point),
                exam_report_ref = COALESCE(NULLIF(?, ''), exam_report_ref)
            WHERE assessment_id = ? AND item_no = ?
            """,
            (
                first.get(f"{item_key}_key_point") or "",
                first.get(f"{item_key}_difficulty_point") or "",
                first.get(f"{item_key}_exam_report_ref") or first.get("exam_report_ref") or "",
                first["assessment_id"],
                item_no,
            ),
        )
        existing_item = conn.execute(
            """
            SELECT assessment_item_id FROM assessment_items
            WHERE assessment_id = ? AND item_no = ?
            """,
            (first["assessment_id"], item_no),
        ).fetchone()
        if not existing_item:
            raise StudentDataError(f"failed to create or find assessment item: {first['assessment_id']} {item_no}")
        assessment_item_ids[item_key] = str(existing_item["assessment_item_id"])

    success = 0
    for row in rows:
        student_id = require_existing_student_id(conn, row["student_id"])
        total = parse_float(row.get("total_score"))
        score_rate = total / max_score if total is not None and max_score else None
        conn.execute(
            """
            INSERT OR REPLACE INTO student_assessment_results(
                result_id, assessment_id, student_id, total_score, score_rate,
                class_rank, grade_rank, percentile, score_band
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{row['assessment_id']}_{student_id}",
                row["assessment_id"],
                student_id,
                total,
                score_rate,
                parse_int(row.get("class_rank")),
                parse_int(row.get("grade_rank")),
                row.get("percentile"),
                (row.get("score_band") or "").strip() or None,
            ),
        )
        for col in item_cols:
            item_key = col.removesuffix("_score")
            item_no = first.get(f"{item_key}_item_no") or item_key
            score = parse_float(row.get(col))
            item_max = float(first.get(f"{item_key}_max_score") or first.get(f"{item_no}_max_score") or 1)
            response_text = (row.get(f"{item_key}_response") or "").strip() or None
            correct_answer = first.get(f"{item_key}_correct_answer") or ""
            method_error_type = validate_method_error(row.get(f"{item_key}_method_error_type"), item_no=item_no)
            conn.execute(
                """
                INSERT OR REPLACE INTO student_item_results(
                    item_result_id, assessment_item_id, student_id, score, is_correct,
                    error_type, response_text, method_error_type, error_detail, evidence_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{row['assessment_id']}_{student_id}_{item_key}",
                    assessment_item_ids[item_key],
                    student_id,
                    score,
                    infer_is_correct(score, item_max, response_text or "", correct_answer),
                    row.get(f"{item_key}_error_type") or method_error_type,
                    response_text,
                    method_error_type,
                    row.get(f"{item_key}_error_detail") or None,
                    row.get(f"{item_key}_evidence_ref") or None,
                ),
            )
        success += 1
    conn.commit()
    batch_id = record_import_batch(
        conn,
        import_type="exam",
        source_file=args.csv,
        row_count=len(rows),
        success_count=success,
        error_count=0,
        imported_by=args.actor,
        real_data=real_data,
    )
    audit(
        conn,
        actor=args.actor,
        action="import",
        target="exam",
        purpose="import exam item scores",
        row_count=success,
        anonymized=not real_data,
        real_data=real_data,
    )
    conn.close()
    payload = {"ok": True, "db": str(path), "batch_id": batch_id, "rows": success, "real_data": real_data}
    if args.json:
        print_json(payload)
    else:
        print(f"Imported {success} exam rows into {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_guard(run))
