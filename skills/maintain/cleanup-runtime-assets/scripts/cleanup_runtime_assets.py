#!/usr/bin/env python
"""Validate and optionally apply a hash-bound LLMWiki runtime cleanup plan."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any


SKILLS_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS_ROOT / "_shared" / "scripts"))
from project_paths import find_project_root, resolve_path  # noqa: E402


ALLOWED_ROOTS = {
    "workspace.tmp",
    "skills.ops.runtime",
    "skills.model-tools.runtime",
    "rag.runtime",
    "library.source",
}
PROTECTED_MARKERS = {
    "checkpoint.json",
    "document_queue.json",
    "issue_log.jsonl",
    "import_handoff.json",
    "approval.json",
    "proposal.json",
}
CATEGORIES = {"temporary", "runtime_cache", "rebuildable", "source_duplicate"}


class CleanupPlanError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_join(root: Path, relative: str) -> Path:
    rel = Path(relative)
    if not relative or rel.is_absolute():
        raise CleanupPlanError("relative_path must be non-empty and relative")
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise CleanupPlanError(f"target escapes path_id root: {relative}") from exc
    if target == root.resolve():
        raise CleanupPlanError("refusing to target a path_id root")
    return target


def measure(path: Path) -> tuple[int, int]:
    if path.is_file():
        return path.stat().st_size, 1
    total = 0
    count = 0
    for item in path.rglob("*"):
        if item.is_file() and not item.is_symlink():
            total += item.stat().st_size
            count += 1
    return total, count


def protected_markers(path: Path) -> list[str]:
    if not path.is_dir():
        return []
    return sorted(
        str(item.relative_to(path))
        for item in path.rglob("*")
        if item.is_file() and item.name in PROTECTED_MARKERS
    )


def validate_target(
    item: dict[str, Any],
    *,
    root: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    path_id = item.get("path_id")
    category = item.get("category")
    if path_id not in ALLOWED_ROOTS:
        raise CleanupPlanError(f"path_id is not cleanup-authorized: {path_id}")
    if category not in CATEGORIES:
        raise CleanupPlanError(f"unsupported category: {category}")
    if not item.get("reason"):
        raise CleanupPlanError("every target requires reason")

    scope_root = resolve_path(path_id, start=root, must_exist=True)
    target = safe_join(scope_root, str(item.get("relative_path", "")))
    if not target.exists():
        raise CleanupPlanError(f"target does not exist: {target}")
    if target.is_symlink():
        raise CleanupPlanError(f"symlink targets are not allowed: {target}")

    if category == "temporary":
        if path_id not in {"workspace.tmp", "skills.ops.runtime"}:
            raise CleanupPlanError("temporary targets are limited to tmp and skills ops runtime")
        markers = protected_markers(target)
        if markers and item.get("completed") is not True:
            raise CleanupPlanError(
                f"protected task markers require completed=true: {target}: {markers[:5]}"
            )
    elif category == "runtime_cache":
        if path_id != "skills.model-tools.runtime":
            raise CleanupPlanError("runtime_cache requires skills.model-tools.runtime")
        if item.get("registry_unreferenced") is not True:
            raise CleanupPlanError("runtime_cache requires registry_unreferenced=true")
        if args.apply and not args.allow_runtime_cache:
            raise CleanupPlanError("runtime_cache requires --allow-runtime-cache")
        rel_parts = Path(str(item["relative_path"])).parts
        if rel_parts and rel_parts[0].lower() in {"envs", "python"}:
            raise CleanupPlanError("environment and bundled Python trees cannot be file-cleaned")
    elif category == "rebuildable":
        if path_id != "rag.runtime":
            raise CleanupPlanError("rebuildable targets are limited to rag.runtime")
        if item.get("rebuildable") is not True:
            raise CleanupPlanError("rebuildable target requires rebuildable=true")
        if args.apply and not args.allow_rebuildable:
            raise CleanupPlanError("applying rebuildable target requires --allow-rebuildable")
    else:
        if path_id != "library.source" or not target.is_file():
            raise CleanupPlanError("source_duplicate must be a source-library file")
        if args.apply and not args.allow_source_dedup:
            raise CleanupPlanError("source_duplicate requires --allow-source-dedup")
        canonical_id = item.get("canonical_path_id")
        if canonical_id != "library.source":
            raise CleanupPlanError("canonical source must use library.source")
        canonical = safe_join(
            resolve_path(canonical_id, start=root, must_exist=True),
            str(item.get("canonical_relative_path", "")),
        )
        if not canonical.is_file() or canonical == target:
            raise CleanupPlanError("canonical source must be a distinct existing file")
        target_hash = sha256_file(target)
        canonical_hash = sha256_file(canonical)
        expected = str(item.get("expected_sha256", "")).lower()
        expected_canonical = str(item.get("canonical_sha256", "")).lower()
        if not expected or not expected_canonical:
            raise CleanupPlanError("source_duplicate requires both bound SHA-256 values")
        if not (target_hash == canonical_hash == expected == expected_canonical):
            raise CleanupPlanError("source duplicate SHA-256 binding failed")

    bytes_total, files_total = measure(target)
    if "expected_bytes" in item and int(item["expected_bytes"]) != bytes_total:
        raise CleanupPlanError(
            f"expected_bytes mismatch for {target}: {item['expected_bytes']} != {bytes_total}"
        )
    return {
        "path_id": path_id,
        "relative_path": item["relative_path"],
        "full_path": str(target),
        "category": category,
        "reason": item["reason"],
        "bytes": bytes_total,
        "files": files_total,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--role-c-authorized", action="store_true")
    parser.add_argument("--allow-runtime-cache", action="store_true")
    parser.add_argument("--allow-rebuildable", action="store_true")
    parser.add_argument("--allow-source-dedup", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = find_project_root(args.plan)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    if plan.get("schema_version") != 1 or not isinstance(plan.get("targets"), list):
        raise CleanupPlanError("plan requires schema_version=1 and targets[]")
    if args.apply and not args.role_c_authorized:
        raise CleanupPlanError("apply requires --role-c-authorized")

    validated = [validate_target(item, root=root, args=args) for item in plan["targets"]]
    paths = [Path(item["full_path"]) for item in validated]
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if left in right.parents or right in left.parents:
                raise CleanupPlanError(f"overlapping targets are forbidden: {left} / {right}")

    deleted: list[str] = []
    if args.apply:
        for item in validated:
            target = Path(item["full_path"])
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            deleted.append(item["full_path"])

    payload = {
        "ok": True,
        "mode": "apply" if args.apply else "dry-run",
        "project_root": str(root),
        "target_count": len(validated),
        "total_bytes": sum(item["bytes"] for item in validated),
        "total_files": sum(item["files"] for item in validated),
        "targets": validated,
        "deleted": deleted,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CleanupPlanError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(2)
