#!/usr/bin/env python
"""Shared helpers for the StudentDataSQL local subsystem."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SHARED_SCRIPTS = PROJECT_ROOT / "skills" / "_shared" / "scripts"
if str(SHARED_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_SCRIPTS))

from project_paths import resolve_path

ROOT = resolve_path("library.sql", start=PROJECT_ROOT)
RUNTIME_ROOT = resolve_path("sql.runtime", start=PROJECT_ROOT)
DB_DIR = resolve_path("sql.db", start=PROJECT_ROOT)
IMPORTS_DIR = resolve_path("sql.imports", start=PROJECT_ROOT)
EXPORTS_DIR = resolve_path("sql.exports", start=PROJECT_ROOT)
BACKUPS_DIR = resolve_path("sql.backups", start=PROJECT_ROOT)
LOGS_DIR = resolve_path("sql.logs", start=PROJECT_ROOT)
EXAM_REPORTS_DIR = resolve_path("sql.exam-reports", start=PROJECT_ROOT)
MIGRATIONS_DIR = ROOT / "migrations"
DEV_DB = DB_DIR / "student_data.dev.sqlite3"
REAL_DB = DB_DIR / "student_data.sqlite3"
RUNTIME_DIRS = [DB_DIR, IMPORTS_DIR, EXPORTS_DIR, BACKUPS_DIR, LOGS_DIR, EXAM_REPORTS_DIR]

SAFE_VIEWS = {
    "class_knowledge_summary": "v_class_knowledge_summary_safe",
    "homework_recent": "v_homework_recent_safe",
    "student_profile": "v_student_profile_pseudonymized",
    "exam_item_analysis": "v_exam_item_analysis_safe",
}

FORBIDDEN_OUTPUT_KEYS = {
    "real_name",
    "school_number",
    "guardian_contact",
    "notes",
    "teacher_note",
    "teacher_comment",
    "class_rank",
    "grade_rank",
    "score_band",
}


class StudentDataError(RuntimeError):
    """Expected operational failure for this subsystem."""


def sqlite_module_for(real_data: bool):
    if not real_data:
        return sqlite3
    for module_name in ("sqlcipher3", "pysqlcipher3.dbapi2"):
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue
    raise StudentDataError(
        "real-data mode requires a SQLCipher Python module such as sqlcipher3; "
        "install sqlcipher3-wheels or provide an equivalent runtime"
    )


def ensure_runtime_dirs() -> None:
    for path in RUNTIME_DIRS:
        path.mkdir(parents=True, exist_ok=True)


def require_real_data_key() -> str:
    key = os.environ.get("STUDENT_DATA_DB_KEY", "").strip()
    if not key:
        raise StudentDataError(
            "real-data mode requires STUDENT_DATA_DB_KEY and SQLCipher; refusing to open real student DB"
        )
    return key


def assert_child_path(path: Path, parent: Path) -> None:
    resolved = path.resolve()
    parent_resolved = parent.resolve()
    if resolved != parent_resolved and parent_resolved not in resolved.parents:
        raise StudentDataError(f"refusing path outside {parent_resolved}: {resolved}")


def db_path_for_mode(dev: bool, real_data: bool, db_path: str | None = None) -> Path:
    if dev == real_data:
        raise StudentDataError("choose exactly one database mode: --dev or --real-data")
    path = Path(db_path) if db_path else (DEV_DB if dev else REAL_DB)
    if not path.is_absolute():
        path = ROOT / path
    assert_child_path(path, DB_DIR)
    return path


def connect_db(path: Path, *, real_data: bool) -> sqlite3.Connection:
    ensure_runtime_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    created_here = real_data and not path.exists()
    if real_data:
        key = require_real_data_key()
    sqlite_module = sqlite_module_for(real_data)
    conn = sqlite_module.connect(path)
    conn.row_factory = sqlite_module.Row
    if real_data:
        try:
            key_literal = "'" + key.replace("'", "''") + "'"
            conn.execute(f"PRAGMA key = {key_literal}")
        except sqlite_module.Error as exc:
            conn.close()
            if created_here:
                path.unlink(missing_ok=True)
            raise StudentDataError(f"SQLCipher key setup failed: {exc}") from exc
        version = conn.execute("PRAGMA cipher_version").fetchone()
        if not version:
            conn.close()
            if created_here:
                path.unlink(missing_ok=True)
            raise StudentDataError("SQLCipher is not available in this Python SQLite build; refusing real-data DB")
    else:
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def apply_migrations(conn: sqlite3.Connection) -> list[str]:
    migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    applied: list[str] = []
    existing = {
        row["version"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchall()
        and conn.execute("SELECT version FROM schema_migrations").fetchall()
    }
    for migration in migrations:
        version = migration.name
        if version in existing:
            continue
        sql = migration.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
            (version,),
        )
        applied.append(version)
    conn.commit()
    return applied


def reset_db(path: Path) -> None:
    assert_child_path(path, DB_DIR)
    for suffix in ["", "-wal", "-shm"]:
        target = Path(str(path) + suffix)
        if target.exists():
            target.unlink()


def audit(
    conn: sqlite3.Connection,
    *,
    actor: str,
    action: str,
    target: str,
    purpose: str = "",
    row_count: int | None = None,
    anonymized: bool = True,
    real_data: bool = False,
) -> str:
    audit_id = f"audit_{uuid.uuid4().hex}"
    conn.execute(
        """
        INSERT INTO access_audit_log(
            audit_id, actor, action, target, purpose, row_count, anonymized, real_data
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (audit_id, actor, action, target, purpose, row_count, int(anonymized), int(real_data)),
    )
    conn.commit()
    return audit_id


def record_import_batch(
    conn: sqlite3.Connection,
    *,
    import_type: str,
    source_file: str,
    row_count: int,
    success_count: int,
    error_count: int,
    imported_by: str,
    real_data: bool,
) -> str:
    checksum = file_sha256(Path(source_file)) if source_file else ""
    batch_id = f"batch_{uuid.uuid4().hex}"
    conn.execute(
        """
        INSERT INTO import_batches(
            batch_id, import_type, source_file, row_count, success_count,
            error_count, imported_by, checksum, real_data
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            import_type,
            source_file,
            row_count,
            success_count,
            error_count,
            imported_by,
            checksum,
            int(real_data),
        ),
    )
    conn.commit()
    return batch_id


def file_sha256(path: Path) -> str:
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def require_columns(rows: list[dict[str, str]], columns: list[str]) -> None:
    if not rows:
        raise StudentDataError("CSV contains no rows")
    missing = [col for col in columns if col not in rows[0]]
    if missing:
        raise StudentDataError(f"CSV missing required columns: {', '.join(missing)}")


def require_existing_student_id(conn: sqlite3.Connection, student_id: str) -> str:
    text = (student_id or "").strip()
    if not text:
        raise StudentDataError("student_id is required")
    row = conn.execute("SELECT student_id FROM students WHERE student_id = ?", (text,)).fetchone()
    if not row:
        raise StudentDataError(f"unknown student_id: {text}")
    return str(row["student_id"])


def sanitize_rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for row in rows:
        item = {key: row[key] for key in row.keys() if key not in FORBIDDEN_OUTPUT_KEYS}
        sanitized.append(item)
    return sanitized


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def add_mode_args(parser: argparse.ArgumentParser, *, default_dev: bool = True) -> None:
    group = parser.add_mutually_exclusive_group(required=not default_dev)
    group.add_argument("--dev", action="store_true", default=default_dev, help="use synthetic/dev SQLite database")
    group.add_argument("--real-data", action="store_true", help="use encrypted real student database")
    parser.add_argument("--db", help="database path under StudentDataSQL/runtime/db")


def resolve_args_db(args: argparse.Namespace) -> tuple[Path, bool]:
    real_data = bool(getattr(args, "real_data", False))
    dev = bool(getattr(args, "dev", False)) and not real_data
    return db_path_for_mode(dev, real_data, getattr(args, "db", None)), real_data


def main_guard(fn) -> int:
    try:
        return int(fn())
    except StudentDataError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
