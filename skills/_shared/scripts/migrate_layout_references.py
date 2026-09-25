#!/usr/bin/env python
"""在受控范围内 dry-run 或更新旧目录引用。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from project_paths import find_project_root


REPLACEMENTS = [
    ("临时代码/临时代码/", "tmp/staging/legacy-temp-materials/"),
    ("临时代码\\临时代码\\", "tmp\\staging\\legacy-temp-materials\\"),
    ("临时代码\\\\临时代码\\\\", "tmp\\\\staging\\\\legacy-temp-materials\\\\"),
    ("临时代码/_backup", "tmp/staging/legacy-temp-materials/_backup"),
    ("临时代码\\_backup", "tmp\\staging\\legacy-temp-materials\\_backup"),
    ("临时代码\\\\_backup", "tmp\\\\staging\\\\legacy-temp-materials\\\\_backup"),
    (
        "新建根目录 `临时代码/`，归档 `_tmp_*` 与 `_backup_*` 工作产物，并写入 `临时代码/临时代码归档清单-2026-07-01.json`。",
        "历史临时材料已归入 `tmp/staging/legacy-temp-materials/`，其中包含当时的 `_tmp_*`、`_backup_*` 和归档清单。",
    ),
    ("回原件/VLM", "回到 `source-library/` 或交给 VLM 复核"),
    ("详解 >20 字符且不是“无/见解析原件/见答案”", "详解 >20 字符且不是“无”“见解析”“见原件”“见答案”"),
    ("见原件/题干缺失", "“见原件”或“题干缺失”"),
    ("存于原件/", "存于 source-library/"),
    ("临时代码\\\\任务状态", "tmp\\\\logs\\\\legacy-task-status"),
    ("临时代码\\\\codex_tasks", "tmp\\\\tasks\\\\codex"),
    ("临时代码\\\\_archive", "tmp\\\\staging\\\\legacy-temp-materials"),
    ("临时代码/codex_tasks", "tmp/tasks/codex"),
    ("临时代码\\codex_tasks", "tmp\\tasks\\codex"),
    ("临时代码/任务状态", "tmp/logs/legacy-task-status"),
    ("临时代码\\任务状态", "tmp\\logs\\legacy-task-status"),
    ("临时代码/_archive", "tmp/staging/legacy-temp-materials"),
    ("临时代码\\_archive", "tmp\\staging\\legacy-temp-materials"),
    ("临时代码/ppt分析", "tmp/renders/ppt-analysis"),
    ("临时代码\\ppt分析", "tmp\\renders\\ppt-analysis"),
    ("临时代码/wiki_maintenance_audit_report.json", "skills/_ops/runtime/reports/wiki-maintenance-audit.json"),
    ("临时代码/wiki_maintenance_final_summary.json", "skills/_ops/runtime/reports/wiki-maintenance-summary.json"),
    ("临时代码/", "tmp/tasks/"),
    ("临时代码\\", "tmp\\tasks\\"),
    ("codex_helpers/video_download_ascii_staging", "tmp/downloads/video-download-ascii-staging"),
    ("codex_helpers\\video_download_ascii_staging", "tmp\\downloads\\video-download-ascii-staging"),
    ("codex_helpers/redownload_low_quality_20260704_ascii", "tmp/downloads/redownload-low-quality-20260704"),
    ("codex_helpers\\redownload_low_quality_20260704_ascii", "tmp\\downloads\\redownload-low-quality-20260704"),
    ("codex_helpers/bilibili_cdn_probe", "tmp/downloads/bilibili-cdn-probe"),
    ("codex_helpers\\bilibili_cdn_probe", "tmp\\downloads\\bilibili-cdn-probe"),
    ("codex_helpers/pdf_ascii_staging", "tmp/staging/pdf-ascii"),
    ("codex_helpers\\pdf_ascii_staging", "tmp\\staging\\pdf-ascii"),
    ("codex_helpers/video_transcribe_ascii_staging", "tmp/staging/video-transcribe-ascii"),
    ("codex_helpers\\video_transcribe_ascii_staging", "tmp\\staging\\video-transcribe-ascii"),
    ("codex_helpers/windows_utf8_guard.py", ".codex/helpers/windows_utf8_guard.py"),
    ("codex_helpers\\windows_utf8_guard.py", ".codex\\helpers\\windows_utf8_guard.py"),
    ("codex_helpers/codex_utf8_run.ps1", ".codex/helpers/codex_utf8_run.ps1"),
    ("codex_helpers\\codex_utf8_run.ps1", ".codex\\helpers\\codex_utf8_run.ps1"),
    ("原件/", "source-library/"),
    ("原件\\", "source-library\\"),
    ("StudentDataSQL/exam_reports", "StudentDataSQL/runtime/exam-reports"),
    ("StudentDataSQL/imports", "StudentDataSQL/runtime/imports"),
    ("StudentDataSQL/exports", "StudentDataSQL/runtime/exports"),
    ("StudentDataSQL/backups", "StudentDataSQL/runtime/backups"),
    ("StudentDataSQL/logs", "StudentDataSQL/runtime/logs"),
    ("StudentDataSQL/db", "StudentDataSQL/runtime/db"),
    ("BGE-M3/.venv", "BGE-M3/runtime/env"),
    ("BGE-M3/models", "BGE-M3/runtime/models"),
    ("BGE-M3/data", "BGE-M3/runtime/data"),
    ("BGE-M3/output", "BGE-M3/runtime/index"),
    (".venv/Scripts/python.exe scripts/rag_pipeline.py", "runtime/env/Scripts/python.exe scripts/rag_pipeline.py"),
    (".venv\\Scripts\\python.exe scripts\\rag_pipeline.py", "runtime\\env\\Scripts\\python.exe scripts\\rag_pipeline.py"),
    (".venv/Scripts/python.exe -c", "runtime/env/Scripts/python.exe -c"),
    (".venv\\Scripts\\python.exe -c", "runtime\\env\\Scripts\\python.exe -c"),
    ("skills/_shared/runtime/tools/", "skills/_shared/model-tools/runtime/tools/"),
    ("skills/_shared/runtime/envs/", "skills/_shared/model-tools/runtime/envs/"),
    ("_shared/runtime/tools/", "_shared/model-tools/runtime/tools/"),
    ("_shared/runtime/envs/", "_shared/model-tools/runtime/envs/"),
    ("skills/_shared/model-runtime", "skills/_shared/model-tools"),
    ("_shared/model-runtime", "_shared/model-tools"),
    ("skills/_runtime/cache/skill_retrieval", "skills/_ops/runtime/state/skill-retrieval"),
    ("skills/_runtime/proposals", "skills/_ops/runtime/state/proposals"),
    ("skills/_runtime/backups", "skills/_ops/runtime/backups"),
    ("skills/_runtime/reports", "skills/_ops/runtime/reports"),
    ("skills/_runtime/staging", "skills/_ops/runtime/staging"),
    ("skills/_runtime/logs", "skills/_ops/runtime/logs"),
    ("skills/_runtime/state", "skills/_ops/runtime/state"),
    ("model-registry.yaml", "registry.yaml"),
]

TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".jsonl", ".py", ".ps1", ".bat", ".txt", ".toml"}
EXCLUDED_PARTS = {
    "output",
    "tmp",
    "source-library",
    "audits",
    "原件",
    "临时代码",
    "codex_helpers",
    "runtime",
    "_runtime",
    "model-runtime",
    "_archive",
    "_incoming",
    "__pycache__",
    ".git",
    ".venvs",
    "models",
}
SELF_EXCLUDED = {
    "migrate_layout_references.py",
    "migration_inventory.py",
    "verify_migration.py",
    "test_layout_migration_tools.py",
    "sync_integration_package.py",
}


def iter_files(scope: Path):
    for current, directories, files in os.walk(scope):
        directories[:] = sorted(d for d in directories if d not in EXCLUDED_PARTS)
        base = Path(current)
        for name in sorted(files):
            path = base / name
            if name == "CHANGELOG.md" or name in SELF_EXCLUDED or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            yield path


def replace_text(text: str, project_root: Path) -> tuple[str, dict[str, int]]:
    output_sentinel = "__LLMWIKI_PROJECT_DELIVERABLE_OUTPUT__"
    updated = text.replace("LLMWiki_BGE-M3/output", output_sentinel)
    counts: dict[str, int] = {}
    root = project_root.resolve()
    absolute_prefixes = [
        root.as_posix().rstrip("/") + "/",
        str(root).rstrip("\\") + "\\",
        str(root).replace("\\", "\\\\").rstrip("\\") + "\\\\",
    ]
    for prefix in absolute_prefixes:
        count = updated.count(prefix)
        if count:
            updated = updated.replace(prefix, "")
            counts["<project-root>/"] = counts.get("<project-root>/", 0) + count
    for bare_root in [root.as_posix(), str(root), str(root).replace("\\", "\\\\")]:
        count = updated.count(bare_root)
        if count:
            updated = updated.replace(bare_root, ".")
            counts["<project-root>"] = counts.get("<project-root>", 0) + count
    for old, new in REPLACEMENTS:
        count = updated.count(old)
        if count:
            updated = updated.replace(old, new)
            counts[old] = count
    return updated.replace(output_sentinel, "LLMWiki_BGE-M3/output"), counts


def migrate(scope: Path, *, apply: bool) -> dict[str, Any]:
    changed: list[dict[str, Any]] = []
    project_root = find_project_root()
    for path in iter_files(scope):
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        updated, counts = replace_text(text, project_root)
        if updated == text:
            continue
        changed.append({"path": str(path), "replacements": counts})
        if apply:
            path.write_bytes(updated.encode("utf-8"))
    return {"ok": True, "apply": apply, "scope": str(scope), "changed_count": len(changed), "changed": changed}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", default="skills", help="项目根内的受控相对目录")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--user-authorized", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.apply and not args.user_authorized:
        payload = {"ok": False, "error": "--apply 需要 --user-authorized"}
        print(json.dumps(payload, ensure_ascii=False) if args.json else payload["error"])
        return 2
    root = find_project_root()
    scope = (root / args.scope).resolve()
    try:
        scope.relative_to(root)
    except ValueError:
        payload = {"ok": False, "error": "scope 必须位于项目根内"}
        print(json.dumps(payload, ensure_ascii=False) if args.json else payload["error"])
        return 2
    payload = migrate(scope, apply=args.apply)
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
