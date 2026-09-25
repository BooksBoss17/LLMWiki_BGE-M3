#!/usr/bin/env python
"""列出或清理超过保留期的 tmp 文件；默认只执行 dry-run。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from project_paths import resolve_path


def is_within(path: Path, parent: Path) -> bool:
    resolved = path.resolve()
    root = parent.resolve()
    return resolved == root or root in resolved.parents


def collect_candidates(tmp_root: Path, *, days: int, now: float | None = None) -> list[dict[str, Any]]:
    if days < 0:
        raise ValueError("days 不能为负数")
    current = time.time() if now is None else now
    cutoff = current - days * 24 * 60 * 60
    candidates: list[dict[str, Any]] = []
    if not tmp_root.exists():
        return candidates
    for path in sorted(tmp_root.rglob("*")):
        if not path.is_file() and not path.is_symlink():
            continue
        stat = path.lstat()
        if stat.st_mtime > cutoff:
            continue
        candidates.append(
            {
                "path": path.relative_to(tmp_root).as_posix(),
                "bytes": stat.st_size,
                "modified_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(stat.st_mtime)),
            }
        )
    return candidates


def execute(tmp_root: Path, *, days: int, apply: bool, now: float | None = None) -> dict[str, Any]:
    candidates = collect_candidates(tmp_root, days=days, now=now)
    deleted: list[str] = []
    if apply:
        for item in candidates:
            target = (tmp_root / item["path"]).resolve()
            if not is_within(target, tmp_root):
                raise RuntimeError(f"拒绝删除 tmp 之外的路径：{target}")
            target.unlink(missing_ok=True)
            deleted.append(item["path"])
        for directory in sorted((path for path in tmp_root.rglob("*") if path.is_dir()), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
    return {
        "ok": True,
        "mode": "apply" if apply else "dry-run",
        "tmp_root": str(tmp_root.resolve()),
        "retention_days": days,
        "candidate_count": len(candidates),
        "candidate_bytes": sum(int(item["bytes"]) for item in candidates),
        "deleted_count": len(deleted),
        "deleted": deleted,
        "candidates": candidates,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="保留天数，默认 30")
    parser.add_argument("--tmp-root", type=Path, help="仅允许指定 PROJECT_LAYOUT.yaml 中 tmp 的子目录")
    parser.add_argument("--apply", action="store_true", help="删除候选文件；默认只列出")
    parser.add_argument("--user-authorized", action="store_true", help="确认用户已明确授权删除")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    configured_tmp = resolve_path("workspace.tmp", start=Path(__file__))
    tmp_root = (args.tmp_root or configured_tmp).resolve()
    if not is_within(tmp_root, configured_tmp):
        payload = {"ok": False, "error": f"tmp-root 必须位于 {configured_tmp}"}
        print(json.dumps(payload, ensure_ascii=False) if args.json else payload["error"])
        return 2
    if args.apply and not args.user_authorized:
        payload = {"ok": False, "error": "--apply 需要 --user-authorized"}
        print(json.dumps(payload, ensure_ascii=False) if args.json else payload["error"])
        return 2

    try:
        payload = execute(tmp_root, days=args.days, apply=args.apply)
    except (OSError, RuntimeError, ValueError) as exc:
        payload = {"ok": False, "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False) if args.json else payload["error"])
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
