#!/usr/bin/env python
from __future__ import annotations

import argparse
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
    require_existing_student_id,
)


def item_columns(row: dict[str, str]) -> list[str]:
    return sorted([key for key in row if key.startswith("Q") and key.endswith("_correct")])


def run() -> int:
    parser = argparse.ArgumentParser(description="Import homework submission CSV into StudentDataSQL.")
    add_mode_args(parser, default_dev=False)
    parser.add_argument("--csv", required=True, help="homework CSV path")
    parser.add_argument("--actor", default="teacher", help="audit actor")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path, real_data = resolve_args_db(args)
    rows = read_csv(Path(args.csv))
    require_columns(rows, ["assignment_id", "class_id", "title", "assigned_at", "student_id", "status"])
    item_cols = item_columns(rows[0])

    conn = connect_db(path, real_data=real_data)
    apply_migrations(conn)
    first = rows[0]
    assignment_item_ids: dict[str, str] = {}
    conn.execute(
        """
        INSERT OR IGNORE INTO assignments(
            assignment_id, class_id, term_id, title, assignment_type, assigned_at, due_at, source_ref
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            first["assignment_id"],
            first["class_id"],
            first.get("term_id"),
            first["title"],
            first.get("assignment_type") or "daily",
            first["assigned_at"],
            first.get("due_at"),
            first.get("source_ref"),
        ),
    )
    for idx, col in enumerate(item_cols, 1):
        item_no = col.removesuffix("_correct")
        question_id = first.get(f"{item_no}_question_id")
        conn.execute(
            """
            INSERT OR IGNORE INTO assignment_items(
                assignment_item_id, assignment_id, question_id, item_no, max_score, required_flag
            ) VALUES (?, ?, ?, ?, ?, 1)
            """,
            (f"{first['assignment_id']}_{item_no}", first["assignment_id"], question_id, item_no, 1),
        )
        existing_item = conn.execute(
            """
            SELECT assignment_item_id FROM assignment_items
            WHERE assignment_id = ? AND item_no = ?
            """,
            (first["assignment_id"], item_no),
        ).fetchone()
        if not existing_item:
            raise RuntimeError(f"failed to create or find assignment item: {first['assignment_id']} {item_no}")
        assignment_item_ids[item_no] = str(existing_item["assignment_item_id"])

    success = 0
    for row in rows:
        student_id = require_existing_student_id(conn, row["student_id"])
        submission_id = f"{row['assignment_id']}_{student_id}"
        conn.execute(
            """
            INSERT OR REPLACE INTO homework_submissions(
                submission_id, assignment_id, student_id, submitted_at, status,
                completion_rate, teacher_score, time_spent_min
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                submission_id,
                row["assignment_id"],
                student_id,
                row.get("submitted_at"),
                row["status"],
                row.get("completion_rate"),
                row.get("teacher_score"),
                row.get("time_spent_min"),
            ),
        )
        for col in item_cols:
            item_no = col.removesuffix("_correct")
            is_correct = row.get(col)
            conn.execute(
                """
                INSERT OR REPLACE INTO homework_item_results(
                    homework_item_result_id, submission_id, assignment_item_id,
                    score, is_correct, attempt_count, error_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{submission_id}_{item_no}",
                    submission_id,
                    assignment_item_ids[item_no],
                    1 if is_correct == "1" else 0,
                    1 if is_correct == "1" else 0,
                    int(row.get(f"{item_no}_attempt_count") or 1),
                    row.get(f"{item_no}_error_type"),
                ),
            )
        conn.execute(
            """
            INSERT OR REPLACE INTO homework_corrections(
                correction_id, submission_id, corrected_at, correction_status, correction_quality
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                f"correction_{submission_id}",
                submission_id,
                row.get("corrected_at"),
                row.get("correction_status") or "not_required",
                row.get("correction_quality"),
            ),
        )
        success += 1
    conn.commit()
    batch_id = record_import_batch(
        conn,
        import_type="homework",
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
        target="homework",
        purpose="import homework submissions",
        row_count=success,
        anonymized=not real_data,
        real_data=real_data,
    )
    conn.close()
    payload = {"ok": True, "db": str(path), "batch_id": batch_id, "rows": success, "real_data": real_data}
    if args.json:
        print_json(payload)
    else:
        print(f"Imported {success} homework rows into {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_guard(run))
