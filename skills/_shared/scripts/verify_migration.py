#!/usr/bin/env python
"""用迁移前 JSONL 清单校验目标目录的大小和 SHA-256。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


def native_path(path: Path) -> Path:
    """在 Windows 上为超长绝对路径添加 Win32 扩展前缀。"""
    resolved = path.resolve()
    if os.name != "nt":
        return resolved
    raw = str(resolved)
    if raw.startswith("\\\\?\\"):
        return resolved
    if raw.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + raw[2:])
    return Path("\\\\?\\" + raw)


def sha256_file(path: Path, block_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with native_path(path).open("rb") as handle:
        while chunk := handle.read(block_size):
            digest.update(chunk)
    return digest.hexdigest()


def parse_renames(items: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        source, separator, target = item.partition("=")
        if not separator or not source or not target:
            raise ValueError(f"无效 rename，必须为 OLD=NEW：{item}")
        result[source.replace("\\", "/")] = target.replace("\\", "/")
    return result


def expected_records(
    manifest: Path,
    root_id: str,
    renames: dict[str, str],
    prefix_renames: dict[str, str] | None = None,
    default_prefix: str = "",
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    with manifest.open("r", encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            if item.get("root_id") != root_id:
                continue
            relative = str(item["relative_path"]).replace("\\", "/")
            relative = renames.get(relative, relative)
            matched_prefix = False
            for old_prefix, new_prefix in (prefix_renames or {}).items():
                if relative.startswith(old_prefix):
                    relative = new_prefix + relative[len(old_prefix) :]
                    matched_prefix = True
                    break
            if default_prefix and not matched_prefix:
                relative = default_prefix + relative
            records[relative] = item
    return records


def actual_files(root: Path) -> set[str]:
    if not root.exists():
        return set()
    if root.is_file():
        return {root.name}
    walk_root = native_path(root)
    result: set[str] = set()
    for current, _, files in os.walk(walk_root):
        base = Path(current)
        for name in files:
            result.add((base / name).relative_to(walk_root).as_posix())
    return result


def repair_missing_files(
    manifest: Path,
    root_id: str,
    source: Path,
    target: Path,
    *,
    renames: dict[str, str],
) -> list[str]:
    """只补拷清单中目标缺失的文件，适配 Windows 超长路径。"""
    expected = expected_records(manifest, root_id, renames)
    missing = sorted(set(expected) - actual_files(target))
    repaired: list[str] = []
    for target_relative in missing:
        source_relative = str(expected[target_relative]["relative_path"]).replace("/", os.sep)
        source_path = native_path(source / source_relative)
        target_path = native_path(target / target_relative)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        repaired.append(target_relative)
    return repaired


def verify(
    manifest: Path,
    root_id: str,
    target: Path,
    *,
    renames: dict[str, str],
    prefix_renames: dict[str, str] | None = None,
    default_prefix: str = "",
    allow_extra: bool,
) -> dict[str, Any]:
    expected = expected_records(manifest, root_id, renames, prefix_renames, default_prefix)
    actual = actual_files(target)
    missing = sorted(set(expected) - actual)
    extra_paths = actual - set(expected)
    extra = [] if allow_extra else sorted(extra_paths)
    size_mismatch: list[dict[str, Any]] = []
    hash_mismatch: list[dict[str, str]] = []
    verified = 0
    verified_bytes = 0

    for relative in sorted(set(expected) & actual):
        path = target / relative
        item = expected[relative]
        size = native_path(path).stat().st_size
        if size != item["bytes"]:
            size_mismatch.append({"path": relative, "expected": item["bytes"], "actual": size})
            continue
        expected_hash = item.get("sha256")
        if expected_hash:
            actual_hash = sha256_file(path)
            if actual_hash != expected_hash:
                hash_mismatch.append({"path": relative, "expected": expected_hash, "actual": actual_hash})
                continue
        verified += 1
        verified_bytes += size

    ok = not missing and not size_mismatch and not hash_mismatch and (allow_extra or not extra)
    return {
        "ok": ok,
        "root_id": root_id,
        "target": str(target.resolve()),
        "expected_files": len(expected),
        "verified_files": verified,
        "verified_bytes": verified_bytes,
        "missing": missing,
        "extra": extra,
        "extra_count": len(extra_paths),
        "size_mismatch": size_mismatch,
        "hash_mismatch": hash_mismatch,
        "allow_extra": allow_extra,
        "renames": renames,
        "prefix_renames": prefix_renames or {},
        "default_prefix": default_prefix,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--root-id", required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--repair-from", type=Path, help="从源目录补拷清单中缺失的文件")
    parser.add_argument("--rename", action="append", default=[])
    parser.add_argument("--prefix-rename", action="append", default=[])
    parser.add_argument("--default-prefix", default="")
    parser.add_argument("--allow-extra", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        renames = parse_renames(args.rename)
        prefix_renames = parse_renames(args.prefix_rename)
        repaired = (
            repair_missing_files(
                args.manifest,
                args.root_id,
                args.repair_from,
                args.target,
                renames=renames,
            )
            if args.repair_from
            else []
        )
        result = verify(
            args.manifest,
            args.root_id,
            args.target,
            renames=renames,
            prefix_renames=prefix_renames,
            default_prefix=args.default_prefix.replace("\\", "/"),
            allow_extra=args.allow_extra,
        )
        result["repaired"] = repaired
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
