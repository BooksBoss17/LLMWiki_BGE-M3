#!/usr/bin/env python
"""为目录迁移生成可恢复的文件清单、大小与 SHA-256。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

from project_paths import find_project_root, resolve_path


LEGACY_ROOTS = {
    "legacy.source-library": "\u539f\u4ef6",
    "legacy.temp-code": "\u4e34\u65f6\u4ee3\u7801",
    "legacy.tmp": "tmp",
    "legacy.skills-runtime": "skills/_runtime",
    "legacy.model-runtime": "skills/_shared/model-runtime",
    "legacy.shared-runtime": "skills/_shared/runtime",
    "legacy.wiki-meta": "LLMWiki/_meta",
    "legacy.codex-helpers": "codex_helpers",
    "legacy.rag-env": "BGE-M3/.venv",
    "legacy.rag-models": "BGE-M3/models",
    "legacy.rag-data": "BGE-M3/data",
    "legacy.rag-index": "BGE-M3/output",
    "legacy.sql-db": "StudentDataSQL/db",
    "legacy.sql-imports": "StudentDataSQL/imports",
    "legacy.sql-exports": "StudentDataSQL/exports",
    "legacy.sql-backups": "StudentDataSQL/backups",
    "legacy.sql-logs": "StudentDataSQL/logs",
    "legacy.sql-exam-reports": "StudentDataSQL/exam_reports",
}


def sha256_file(path: Path, block_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(block_size):
            digest.update(chunk)
    return digest.hexdigest()


def iter_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for current, directories, files in os.walk(root):
        directories.sort(key=str.casefold)
        files.sort(key=str.casefold)
        base = Path(current)
        for name in files:
            yield base / name


def resolve_inventory_root(project_root: Path, root_id: str) -> Path:
    if root_id in LEGACY_ROOTS:
        return (project_root / LEGACY_ROOTS[root_id]).resolve()
    return resolve_path(root_id, start=project_root)


def inventory(
    root_ids: list[str],
    *,
    output: Path,
    summary_path: Path,
    include_hash: bool,
) -> dict[str, Any]:
    project_root = find_project_root()
    output.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    totals: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []

    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for root_id in root_ids:
            root = resolve_inventory_root(project_root, root_id)
            stats: dict[str, Any] = {
                "root": str(root),
                "exists": root.exists(),
                "file_count": 0,
                "bytes": 0,
                "hashed": include_hash,
            }
            totals[root_id] = stats
            if not root.exists():
                continue
            for path in iter_files(root):
                try:
                    stat = path.stat()
                    record = {
                        "root_id": root_id,
                        "relative_path": path.relative_to(root).as_posix() if path != root else path.name,
                        "bytes": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    }
                    if include_hash:
                        record["sha256"] = sha256_file(path)
                    stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                    stats["file_count"] += 1
                    stats["bytes"] += stat.st_size
                except (OSError, ValueError) as exc:
                    errors.append({"root_id": root_id, "path": str(path), "error": str(exc)})

    summary = {
        "schema_version": 1,
        "project_root": str(project_root),
        "manifest": str(output.resolve()),
        "roots": totals,
        "errors": errors,
        "ok": not errors,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-id", action="append", required=True, dest="root_ids")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--hash", action="store_true", dest="include_hash")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = inventory(
        args.root_ids,
        output=args.output,
        summary_path=args.summary,
        include_hash=args.include_hash,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for root_id, stats in result["roots"].items():
            print(f"{root_id}: {stats['file_count']} files, {stats['bytes']} bytes")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
