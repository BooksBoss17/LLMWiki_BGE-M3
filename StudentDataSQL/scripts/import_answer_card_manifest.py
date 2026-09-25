#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path

from student_data_common import (
    StudentDataError,
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


def run() -> int:
    parser = argparse.ArgumentParser(description="Import de-identified answer-card artifact manifest.")
    add_mode_args(parser, default_dev=False)
    parser.add_argument("--csv", required=True, help="answer card manifest CSV path")
    parser.add_argument("--actor", default="teacher", help="audit actor")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path, real_data = resolve_args_db(args)
    rows = read_csv(Path(args.csv))
    require_columns(rows, ["assessment_id", "class_id", "student_id", "seat_no", "artifact_status"])

    conn = connect_db(path, real_data=real_data)
    apply_migrations(conn)
    success = 0
    for row in rows:
        status = (row.get("artifact_status") or "").strip()
        if status not in {"present", "missing_artifact"}:
            raise StudentDataError(f"invalid artifact_status: {status}")
        student_id = require_existing_student_id(conn, row.get("student_id") or "")
        artifact_id = row.get("artifact_id") or f"{row['assessment_id']}_{student_id}_answer_card"
        conn.execute(
            """
            INSERT OR REPLACE INTO answer_card_artifacts(
                artifact_id, assessment_id, class_id, student_id, seat_no,
                artifact_status, redacted_path, sha256, evidence_ref
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                row["assessment_id"],
                row["class_id"],
                student_id,
                row["seat_no"],
                status,
                row.get("redacted_path") or None,
                row.get("sha256") or None,
                row.get("evidence_ref") or None,
            ),
        )
        success += 1
    conn.commit()
    batch_id = record_import_batch(
        conn,
        import_type="answer_card_manifest",
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
        target="answer_card_manifest",
        purpose="import de-identified answer-card artifact manifest",
        row_count=success,
        anonymized=not real_data,
        real_data=real_data,
    )
    conn.close()
    payload = {"ok": True, "db": str(path), "batch_id": batch_id, "rows": success, "real_data": real_data}
    if args.json:
        print_json(payload)
    else:
        print(f"Imported {success} answer-card manifest rows into {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_guard(run))
