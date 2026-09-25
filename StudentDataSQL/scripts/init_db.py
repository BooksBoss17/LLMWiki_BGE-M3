#!/usr/bin/env python
from __future__ import annotations

import argparse

from student_data_common import apply_migrations, connect_db, main_guard, print_json, reset_db, resolve_args_db


def run() -> int:
    parser = argparse.ArgumentParser(description="Initialize StudentDataSQL database.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dev", action="store_true", help="initialize dev/synthetic SQLite database")
    group.add_argument("--real-data", action="store_true", help="initialize encrypted real student database")
    parser.add_argument("--db", help="database path under StudentDataSQL/runtime/db")
    parser.add_argument("--reset", action="store_true", help="delete target DB before initializing")
    parser.add_argument("--json", action="store_true", help="print JSON result")
    args = parser.parse_args()

    path, real_data = resolve_args_db(args)
    if args.reset:
        reset_db(path)
    conn = connect_db(path, real_data=real_data)
    applied = apply_migrations(conn)
    conn.close()
    payload = {"ok": True, "db": str(path), "real_data": real_data, "applied": applied}
    if args.json:
        print_json(payload)
    else:
        print(f"Initialized {path}")
        if applied:
            print("Applied migrations:")
            for item in applied:
                print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_guard(run))
