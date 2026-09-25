#!/usr/bin/env python
"""读取 PROJECT_LAYOUT.yaml 并统一解析 LLMWiki_BGE-M3 路径。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT_ENV = "LLMWIKI_KB_ROOT"
LAYOUT_ENV = "LLMWIKI_PROJECT_LAYOUT"
PATH_OVERRIDES = {
    "skills.model-tools.runtime": "LLMWIKI_MODEL_RUNTIME_ROOT",
    "integration.skills-mirror": "LLMWIKI_INTEGRATION_SKILLS_ROOT",
}


class ProjectLayoutError(RuntimeError):
    """路径契约缺失或内容无效。"""


def _candidate_roots(start: Path) -> list[Path]:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    return [current, *current.parents]


def find_project_root(start: str | os.PathLike[str] | None = None) -> Path:
    """从环境变量或父目录中的 PROJECT_LAYOUT.yaml 查找项目根。"""
    configured = os.environ.get(ROOT_ENV)
    if configured:
        root = Path(configured).expanduser().resolve()
        if not (root / "PROJECT_LAYOUT.yaml").is_file():
            raise ProjectLayoutError(f"{ROOT_ENV} 未指向有效项目根：{root}")
        return root

    origin = Path(start) if start is not None else Path.cwd()
    for candidate in _candidate_roots(origin):
        if (candidate / "PROJECT_LAYOUT.yaml").is_file():
            return candidate
    raise ProjectLayoutError("未找到 PROJECT_LAYOUT.yaml；请从项目内运行或设置 LLMWIKI_KB_ROOT")


def layout_path(start: str | os.PathLike[str] | None = None) -> Path:
    configured = os.environ.get(LAYOUT_ENV)
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise ProjectLayoutError(f"{LAYOUT_ENV} 指向的文件不存在：{path}")
        return path
    return find_project_root(start) / "PROJECT_LAYOUT.yaml"


def load_layout(start: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """加载 JSON 兼容 YAML；避免给基础路径解析器增加第三方依赖。"""
    path = layout_path(start)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectLayoutError(f"无法读取路径契约 {path}：{exc}") from exc
    if data.get("schema_version") != 1 or not isinstance(data.get("paths"), dict):
        raise ProjectLayoutError("PROJECT_LAYOUT.yaml 必须使用 schema_version=1 并包含 paths 对象")
    return data


def resolve_path(
    path_id: str,
    *,
    start: str | os.PathLike[str] | None = None,
    must_exist: bool = False,
) -> Path:
    """按路径 ID 返回绝对路径；少数本机路径允许环境变量覆盖。"""
    root = find_project_root(start)
    if path_id == "project.root":
        result = root
    else:
        override_name = PATH_OVERRIDES.get(path_id)
        override = os.environ.get(override_name, "") if override_name else ""
        if override:
            result = Path(override).expanduser().resolve()
        else:
            entry = load_layout(root)["paths"].get(path_id)
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise ProjectLayoutError(f"未知路径 ID：{path_id}")
            result = (root / entry["path"]).resolve()
    if must_exist and not result.exists():
        raise ProjectLayoutError(f"路径尚不存在：{path_id} -> {result}")
    return result


def describe_paths(start: str | os.PathLike[str] | None = None) -> dict[str, dict[str, Any]]:
    root = find_project_root(start)
    layout = load_layout(root)
    described: dict[str, dict[str, Any]] = {}
    for path_id, entry in layout["paths"].items():
        resolved = resolve_path(path_id, start=root)
        described[path_id] = {
            **entry,
            "resolved": str(resolved),
            "exists": resolved.exists(),
            "override": PATH_OVERRIDES.get(path_id),
        }
    return described


def validate_layout(
    start: str | os.PathLike[str] | None = None,
    *,
    allow_missing: bool = False,
) -> dict[str, Any]:
    root = find_project_root(start)
    paths = describe_paths(root)
    missing = [path_id for path_id, item in paths.items() if not item["exists"]]
    return {
        "ok": allow_missing or not missing,
        "schema_version": load_layout(root)["schema_version"],
        "project_root": str(root),
        "path_count": len(paths),
        "missing": missing,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="从指定目录查找项目根")
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_parser = subparsers.add_parser("resolve", help="解析一个路径 ID")
    resolve_parser.add_argument("id")
    resolve_parser.add_argument("--must-exist", action="store_true")
    resolve_parser.add_argument("--json", action="store_true")

    list_parser = subparsers.add_parser("list", help="列出全部路径")
    list_parser.add_argument("--json", action="store_true")

    validate_parser = subparsers.add_parser("validate", help="验证路径契约")
    validate_parser.add_argument("--allow-missing", action="store_true")
    validate_parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "resolve":
            resolved = resolve_path(args.id, start=args.root, must_exist=args.must_exist)
            payload: Any = {"id": args.id, "path": str(resolved), "exists": resolved.exists()}
        elif args.command == "list":
            payload = describe_paths(args.root)
        else:
            payload = validate_layout(args.root, allow_missing=args.allow_missing)
    except ProjectLayoutError as exc:
        payload = {"ok": False, "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False) if getattr(args, "json", False) else payload["error"])
        return 2

    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command == "resolve":
        print(payload["path"])
    elif args.command == "list":
        for path_id, item in payload.items():
            print(f"{path_id}\t{item['resolved']}")
    else:
        print("路径契约有效" if payload["ok"] else f"缺少路径：{', '.join(payload['missing'])}")
    return 0 if payload.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
