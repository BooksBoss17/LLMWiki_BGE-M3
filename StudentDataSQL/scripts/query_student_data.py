#!/usr/bin/env python
from __future__ import annotations

import argparse

from student_data_common import (
    SAFE_VIEWS,
    add_mode_args,
    apply_migrations,
    audit,
    connect_db,
    main_guard,
    print_json,
    resolve_args_db,
    sanitize_rows,
)


def run() -> int:
    parser = argparse.ArgumentParser(description="Run a safe whitelisted StudentDataSQL query.")
    add_mode_args(parser, default_dev=True)
    parser.add_argument("--view", required=True, choices=sorted(SAFE_VIEWS), help="safe view alias")
    parser.add_argument("--class-id", help="filter by class_id")
    parser.add_argument("--student-id", help="filter by pseudonymized student_id")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--actor", default="agent", help="audit actor")
    parser.add_argument("--purpose", default="safe student data query", help="audit purpose")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path, real_data = resolve_args_db(args)
    conn = connect_db(path, real_data=real_data)
    apply_migrations(conn)
    view = SAFE_VIEWS[args.view]
    clauses: list[str] = []
    params: list[object] = []
    if args.class_id:
        clauses.append("class_id = ?")
        params.append(args.class_id)
    if args.student_id:
        clauses.append("student_id = ?")
        params.append(args.student_id)
    sql = f"SELECT * FROM {view}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " LIMIT ?"
    params.append(max(1, min(args.limit, 500)))
    rows = conn.execute(sql, params).fetchall()
    result_rows = sanitize_rows(rows)
    audit(
        conn,
        actor=args.actor,
        action="query",
        target=view,
        purpose=args.purpose,
        row_count=len(result_rows),
        anonymized=True,
        real_data=real_data,
    )
    conn.close()
    payload = {"ok": True, "db": str(path), "view": args.view, "rows": result_rows, "row_count": len(result_rows)}
    if args.json:
        print_json(payload)
    else:
        for row in result_rows:
            print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main_guard(run))
