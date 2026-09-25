#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path
import re

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
)

FORBIDDEN_ROSTER_COLUMNS = {
    "real_name",
    "name",
    "姓名",
    "school_number",
    "school_no",
    "学号",
    "exam_number",
    "exam_no",
    "考号",
    "student_status_number",
    "student_registry_number",
    "学籍号",
    "guardian_contact",
    "student_code",
}


def normalize_gender(value: str) -> str:
    text = (value or "").strip().upper()
    if text in {"M", "MALE", "男", "男生"}:
        return "M"
    if text in {"F", "FEMALE", "女", "女生"}:
        return "F"
    if text in {"U", "UNKNOWN", "未知", ""}:
        return "U"
    raise ValueError(f"unsupported gender value: {value}")


def seat_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("blank seat_no")
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return str(int(text)) if text.isdigit() else text


def generated_student_id(term_id: str, class_id: str, gender: str, seat_no: str) -> str:
    return f"{term_id}_{class_id}_{gender}_seat{seat_text(seat_no)}"


def run() -> int:
    parser = argparse.ArgumentParser(description="Import class roster CSV into StudentDataSQL.")
    add_mode_args(parser, default_dev=False)
    parser.add_argument("--csv", required=True, help="roster CSV path")
    parser.add_argument("--actor", default="teacher", help="audit actor")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path, real_data = resolve_args_db(args)
    rows = read_csv(Path(args.csv))
    require_columns(rows, ["class_id", "class_name", "term_id", "school_year", "term_name", "seat_no", "family_name", "gender"])
    forbidden_present = sorted(FORBIDDEN_ROSTER_COLUMNS.intersection(rows[0].keys()))
    if forbidden_present:
        raise ValueError(f"roster CSV contains forbidden identity columns: {', '.join(forbidden_present)}")

    conn = connect_db(path, real_data=real_data)
    apply_migrations(conn)
    success = 0
    for row in rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO terms(term_id, school_year, term_name, starts_on, ends_on)
            VALUES (?, ?, ?, ?, ?)
            """,
            (row["term_id"], row["school_year"], row["term_name"], row.get("starts_on"), row.get("ends_on")),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO classes(class_id, class_name, grade_level, term_id)
            VALUES (?, ?, ?, ?)
            """,
            (row["class_id"], row["class_name"], row.get("grade_level"), row["term_id"]),
        )
        gender = normalize_gender(row["gender"])
        seat_no = seat_text(row["seat_no"])
        family_name = row["family_name"].strip()
        if not family_name:
            raise ValueError("family_name is required")
        student_id = (row.get("student_id") or "").strip() or generated_student_id(row["term_id"], row["class_id"], gender, seat_no)
        expected_student_id = generated_student_id(row["term_id"], row["class_id"], gender, seat_no)
        if student_id != expected_student_id:
            raise ValueError(
                "student_id must be derived from term_id + class_id + gender + seat_no: "
                f"expected {expected_student_id}, got {student_id}"
            )
        student_code = student_id
        pseudonym = f"{row['class_name']}-{family_name}-{seat_no}号"
        conn.execute(
            """
            INSERT OR REPLACE INTO students(student_id, student_code, pseudonym, family_name, gender, status)
            VALUES (?, ?, ?, ?, ?, COALESCE(NULLIF(?, ''), 'active'))
            """,
            (student_id, student_code, pseudonym, family_name, gender, row.get("status", "")),
        )
        membership_id = row.get("membership_id") or f"membership_{row['class_id']}_{student_id}"
        conn.execute(
            """
            INSERT OR REPLACE INTO class_memberships(
                membership_id, student_id, class_id, term_id, seat_no, joined_on, left_on
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                membership_id,
                student_id,
                row["class_id"],
                row["term_id"],
                seat_no,
                row.get("joined_on"),
                row.get("left_on"),
            ),
        )
        success += 1
    conn.commit()
    batch_id = record_import_batch(
        conn,
        import_type="roster",
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
        target="roster",
        purpose="import class roster",
        row_count=success,
        anonymized=not real_data,
        real_data=real_data,
    )
    conn.close()
    payload = {"ok": True, "db": str(path), "batch_id": batch_id, "rows": success, "real_data": real_data}
    if args.json:
        print_json(payload)
    else:
        print(f"Imported {success} roster rows into {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_guard(run))
