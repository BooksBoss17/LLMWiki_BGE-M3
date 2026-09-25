#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import hashlib
import mimetypes
from pathlib import Path

from student_data_common import (
    IMPORTS_DIR,
    StudentDataError,
    add_mode_args,
    apply_migrations,
    assert_child_path,
    audit,
    connect_db,
    main_guard,
    print_json,
    resolve_args_db,
)

DEFAULT_IMPORT_ROOT = IMPORTS_DIR


def normalize_rel(path: str | Path) -> str:
    return str(path).replace("\\", "/").strip()


def stable_artifact_id(relpath: str, digest: str) -> str:
    key = f"{normalize_rel(relpath)}\0{digest}".encode("utf-8")
    return "fileart_" + hashlib.sha256(key).hexdigest()[:32]


def iter_import_artifacts(root: Path) -> list[Path]:
    normalized = root / "normalized"
    answer_cards = root / "redacted_answer_cards"
    files: list[Path] = []
    if normalized.exists():
        files.extend(sorted(normalized.glob("*.csv")))
        files.extend(sorted(normalized.glob("*.json")))
    if answer_cards.exists():
        files.extend(sorted(answer_cards.rglob("*.png")))
    return [path for path in files if path.is_file()]


def csv_first_row(path: Path) -> dict[str, str]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            return next(reader, {}) or {}
    except (UnicodeDecodeError, csv.Error, OSError):
        return {}


def infer_artifact_type(path: Path) -> str:
    name = path.name
    if path.suffix.lower() == ".json":
        return "preprocess_summary_json"
    if path.suffix.lower() == ".png":
        return "answer_card_png"
    if name.endswith("_roster.csv"):
        return "roster_csv"
    if "answer_card_manifest" in name:
        return "answer_card_manifest_csv"
    if "scores_ready" in name:
        return "score_ready_csv"
    if name.endswith("_scores.csv"):
        return "score_csv"
    if "item_map_review" in name:
        return "item_map_review_csv"
    return "import_csv"


def answer_card_lookup(conn) -> dict[str, dict[str, str]]:
    rows = conn.execute(
        """
        SELECT artifact_id, assessment_id, class_id, student_id, seat_no, redacted_path
        FROM answer_card_artifacts
        WHERE redacted_path IS NOT NULL AND redacted_path <> ''
        """
    ).fetchall()
    lookup: dict[str, dict[str, str]] = {}
    for row in rows:
        item = {key: str(row[key]) if row[key] is not None else "" for key in row.keys()}
        raw_path = item.get("redacted_path", "")
        lookup[normalize_rel(raw_path)] = item
        try:
            lookup[normalize_rel(Path(raw_path))] = item
        except OSError:
            pass
    return lookup


def remove_empty_dirs(root: Path) -> None:
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            continue


def run() -> int:
    parser = argparse.ArgumentParser(description="Archive real import files into encrypted StudentDataSQL DB.")
    add_mode_args(parser, default_dev=False)
    parser.add_argument("--root", default=str(DEFAULT_IMPORT_ROOT), help="StudentDataSQL imports root")
    parser.add_argument("--delete-after", action="store_true", help="delete archived files after DB commit")
    parser.add_argument("--actor", default="teacher", help="audit actor")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    db_path, real_data = resolve_args_db(args)
    if not real_data:
        raise StudentDataError("file artifact archival is allowed only with --real-data")

    import_root = Path(args.root)
    if not import_root.is_absolute():
        import_root = (Path.cwd() / import_root).resolve()
    assert_child_path(import_root, ROOT)
    if import_root.resolve() != DEFAULT_IMPORT_ROOT.resolve():
        assert_child_path(import_root, DEFAULT_IMPORT_ROOT)

    files = iter_import_artifacts(import_root)
    conn = connect_db(db_path, real_data=True)
    apply_migrations(conn)
    card_lookup = answer_card_lookup(conn)

    archived = 0
    updated_answer_cards = 0
    by_type: dict[str, int] = {}
    archived_paths: list[Path] = []
    for path in files:
        relpath = normalize_rel(path.relative_to(ROOT))
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        artifact_id = stable_artifact_id(relpath, digest)
        artifact_type = infer_artifact_type(path)
        first_row = csv_first_row(path) if path.suffix.lower() == ".csv" else {}
        class_id = first_row.get("class_id") or None
        assessment_id = first_row.get("assessment_id") or None
        student_id = None
        seat_no = None
        linked_record_id = None

        if artifact_type == "answer_card_png":
            matches = [
                normalize_rel(path.relative_to(Path.cwd())),
                relpath,
                normalize_rel(path),
                normalize_rel(path.resolve()),
            ]
            card = next((card_lookup[key] for key in matches if key in card_lookup), None)
            if card:
                class_id = card.get("class_id") or class_id
                assessment_id = card.get("assessment_id") or assessment_id
                student_id = card.get("student_id") or None
                seat_no = card.get("seat_no") or None
                linked_record_id = card.get("artifact_id") or None

        conn.execute(
            """
            INSERT OR REPLACE INTO encrypted_file_artifacts(
                artifact_id, artifact_type, source_path, source_relpath, source_name,
                class_id, assessment_id, student_id, seat_no, linked_record_id,
                content_sha256, content_size, mime_type, content, real_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                artifact_id,
                artifact_type,
                str(path.resolve()),
                relpath,
                path.name,
                class_id,
                assessment_id,
                student_id,
                seat_no,
                linked_record_id,
                digest,
                len(data),
                mimetypes.guess_type(path.name)[0],
                data,
            ),
        )
        if linked_record_id:
            conn.execute(
                """
                UPDATE answer_card_artifacts
                SET redacted_path = ?
                WHERE artifact_id = ?
                """,
                (f"encrypted-artifact:{artifact_id}", linked_record_id),
            )
            updated_answer_cards += 1
        archived += 1
        by_type[artifact_type] = by_type.get(artifact_type, 0) + 1
        archived_paths.append(path)

    conn.commit()
    audit(
        conn,
        actor=args.actor,
        action="import",
        target="encrypted_file_artifacts",
        purpose="archive real import source files into encrypted DB",
        row_count=archived,
        anonymized=False,
        real_data=True,
    )
    conn.close()

    deleted = 0
    if args.delete_after:
        for path in archived_paths:
            if path.exists():
                path.unlink()
                deleted += 1
        remove_empty_dirs(import_root)

    payload = {
        "ok": True,
        "db": str(db_path),
        "root": str(import_root),
        "archived": archived,
        "deleted": deleted,
        "updated_answer_cards": updated_answer_cards,
        "by_type": dict(sorted(by_type.items())),
        "real_data": True,
    }
    if args.json:
        print_json(payload)
    else:
        print(f"Archived {archived} files into {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_guard(run))
