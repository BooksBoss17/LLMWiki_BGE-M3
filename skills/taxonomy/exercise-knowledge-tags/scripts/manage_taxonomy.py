#!/usr/bin/env python
"""Manage stable physics knowledge-point IDs and gradual question assignments."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve()
SKILL_DIR = SCRIPT.parent.parent
DEFAULT_KB_ROOT = SCRIPT.parents[4]
DEFAULT_REGISTRY = SKILL_DIR / "references" / "knowledge-points.yaml"
DEFAULT_SKILL_DOC = SKILL_DIR / "SKILL.md"
NODE_FIELDS = [
    "kp_id",
    "title",
    "parent_id",
    "level",
    "sort_order",
    "path",
    "aliases",
    "status",
    "replacement_ids",
    "wiki_path",
]
KP_ID_RE = re.compile(r"^kp_[0-9]{6}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
DESTRUCTIVE_ACTIONS = {"rename", "move", "deprecate", "merge"}


def yaml_module():
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required; run install_skill_dependencies.py --install --all") from exc
    return yaml


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_ai_extra_tags(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("ai_extra_tags must be an array of strings")
    if len(value) > 16:
        raise ValueError("ai_extra_tags allows at most 16 tags")
    tags: list[str] = []
    for raw in value:
        tag = raw.strip()
        if not 2 <= len(tag) <= 32:
            raise ValueError(f"ai_extra_tags item must be 2-32 characters: {raw}")
        if "/" in tag or "\\" in tag or re.fullmatch(r"kp_[0-9]+", tag, flags=re.IGNORECASE):
            raise ValueError(f"ai_extra_tags must not imitate standard paths or kp_id: {raw}")
        tags.append(tag)
    if len(tags) != len(set(tags)):
        raise ValueError("ai_extra_tags contains duplicates")
    return tags


def ensure_inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(root.resolve())
    return resolved


def load_registry(path: Path) -> dict[str, Any]:
    payload = yaml_module().safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("knowledge-point registry must be a YAML object")
    validate_registry(payload)
    return payload


def dump_registry(payload: dict[str, Any]) -> str:
    normalized = {
        "version": 1,
        "next_id": int(payload["next_id"]),
        "nodes": [{field: node[field] for field in NODE_FIELDS} for node in payload["nodes"]],
    }
    return yaml_module().safe_dump(normalized, allow_unicode=True, sort_keys=False, width=120)


def node_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(node["kp_id"]): node for node in payload["nodes"]}


def children_map(payload: dict[str, Any]) -> dict[str | None, list[dict[str, Any]]]:
    children: dict[str | None, list[dict[str, Any]]] = {}
    for node in payload["nodes"]:
        children.setdefault(node["parent_id"], []).append(node)
    for values in children.values():
        values.sort(key=lambda item: (int(item["sort_order"]), str(item["kp_id"])))
    return children


def validate_registry(payload: dict[str, Any]) -> None:
    if int(payload.get("version", 0)) != 1:
        raise ValueError("registry version must be 1")
    nodes = payload.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("registry nodes must be a non-empty array")
    ids: set[str] = set()
    active_paths: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or set(node) != set(NODE_FIELDS):
            raise ValueError(f"node {index} must contain exactly: {NODE_FIELDS}")
        kp_id = str(node["kp_id"])
        if not KP_ID_RE.fullmatch(kp_id) or kp_id in ids:
            raise ValueError(f"invalid or duplicate kp_id: {kp_id}")
        ids.add(kp_id)
        if not str(node["title"]).strip() or "/" in str(node["title"]):
            raise ValueError(f"invalid title for {kp_id}")
        if int(node["level"]) not in {1, 2, 3}:
            raise ValueError(f"invalid level for {kp_id}")
        if int(node["sort_order"]) < 1:
            raise ValueError(f"sort_order must be positive for {kp_id}")
        if not isinstance(node["aliases"], list) or not isinstance(node["replacement_ids"], list):
            raise ValueError(f"aliases and replacement_ids must be arrays for {kp_id}")
        if node["status"] not in {"active", "deprecated"}:
            raise ValueError(f"invalid status for {kp_id}")
        if node["status"] == "deprecated" and not node["replacement_ids"]:
            raise ValueError(f"deprecated node requires replacement_ids: {kp_id}")
        if node["status"] == "active":
            path = str(node["path"])
            if path in active_paths:
                raise ValueError(f"duplicate active path: {path}")
            active_paths.add(path)

    by_id = node_map(payload)
    for kp_id, node in by_id.items():
        parent_id = node["parent_id"]
        if parent_id is None:
            if int(node["level"]) != 1 or str(node["path"]) != str(node["title"]):
                raise ValueError(f"root node hierarchy mismatch: {kp_id}")
        else:
            if parent_id not in by_id:
                raise ValueError(f"missing parent for {kp_id}: {parent_id}")
            parent = by_id[parent_id]
            if int(node["level"]) != int(parent["level"]) + 1:
                raise ValueError(f"level mismatch for {kp_id}")
            expected_path = f"{parent['path']}/{node['title']}"
            if str(node["path"]) != expected_path:
                raise ValueError(f"path mismatch for {kp_id}: expected {expected_path}")
        seen: set[str] = set()
        cursor = kp_id
        while cursor is not None:
            if cursor in seen:
                raise ValueError(f"parent cycle detected at {kp_id}")
            seen.add(cursor)
            cursor = by_id[cursor]["parent_id"] if cursor in by_id else None
        for replacement in node["replacement_ids"]:
            if replacement not in by_id or replacement == kp_id:
                raise ValueError(f"invalid replacement for {kp_id}: {replacement}")
    highest = max(int(kp_id.split("_")[1]) for kp_id in ids)
    if int(payload.get("next_id", 0)) <= highest:
        raise ValueError("next_id must be greater than every allocated ID")


def parse_skill_catalog(skill_doc: Path, kb_root: Path) -> dict[str, Any]:
    lines = skill_doc.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index("## 标签目录") + 1
    except ValueError as exc:
        raise ValueError("skill document has no 标签目录 section") from exc
    parsed: list[tuple[int, str]] = []
    for raw in lines[start:]:
        if raw.startswith("## "):
            break
        match = re.match(r"^(\s*)-\s+(.+?)\s*$", raw)
        if not match:
            continue
        spaces = len(match.group(1))
        if spaces not in {0, 2, 4}:
            continue
        parsed.append((spaces // 2 + 1, match.group(2)))
    if not parsed:
        raise ValueError("no taxonomy bullets found")
    nodes: list[dict[str, Any]] = []
    stack: dict[int, dict[str, Any]] = {}
    sibling_counts: dict[str | None, int] = {}
    wiki_stems = {path.stem: str(path.relative_to(kb_root)).replace("\\", "/") for path in (kb_root / "LLMWiki").rglob("*.md")}
    for sequence, (level, title) in enumerate(parsed, start=1):
        parent = stack.get(level - 1) if level > 1 else None
        if level > 1 and parent is None:
            raise ValueError(f"taxonomy hierarchy gap before: {title}")
        parent_id = parent["kp_id"] if parent else None
        sibling_counts[parent_id] = sibling_counts.get(parent_id, 0) + 1
        kp_id = f"kp_{sequence:06d}"
        path_value = f"{parent['path']}/{title}" if parent else title
        node = {
            "kp_id": kp_id,
            "title": title,
            "parent_id": parent_id,
            "level": level,
            "sort_order": sibling_counts[parent_id],
            "path": path_value,
            "aliases": [],
            "status": "active",
            "replacement_ids": [],
            "wiki_path": wiki_stems.get(title, ""),
        }
        nodes.append(node)
        stack[level] = node
        for deeper in [key for key in stack if key > level]:
            del stack[deeper]
    payload = {"version": 1, "next_id": len(nodes) + 1, "nodes": nodes}
    validate_registry(payload)
    return payload


def allocate_id(payload: dict[str, Any]) -> str:
    value = int(payload["next_id"])
    payload["next_id"] = value + 1
    return f"kp_{value:06d}"


def descendants(payload: dict[str, Any], root_id: str) -> list[dict[str, Any]]:
    children = children_map(payload)
    result: list[dict[str, Any]] = []

    def visit(parent_id: str) -> None:
        for child in children.get(parent_id, []):
            result.append(child)
            visit(child["kp_id"])

    visit(root_id)
    return result


def refresh_subtree(payload: dict[str, Any], root_id: str) -> None:
    by_id = node_map(payload)

    def refresh(kp_id: str) -> None:
        node = by_id[kp_id]
        old_path = str(node["path"])
        parent = by_id.get(node["parent_id"])
        node["level"] = int(parent["level"]) + 1 if parent else 1
        node["path"] = f"{parent['path']}/{node['title']}" if parent else node["title"]
        if old_path != node["path"] and old_path not in node["aliases"]:
            node["aliases"].append(old_path)
        for child in [item for item in payload["nodes"] if item["parent_id"] == kp_id]:
            refresh(child["kp_id"])

    refresh(root_id)


def update_frontmatter_list(text: str, key: str, values: list[str]) -> str:
    if not text.startswith("---\n"):
        raise ValueError("question Markdown has no YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("question Markdown frontmatter is not closed")
    front = text[4:end]
    block_pattern = re.compile(
        rf"(?m)^{re.escape(key)}:[^\S\r\n]*(?:\[[^\r\n]*\])?\r?\n"
        rf"(?:^[ \t]*-[^\r\n]*(?:\r?\n|$))*"
    )
    front_with_newline = front + "\n"
    match = block_pattern.search(front_with_newline)
    if match:
        item = re.search(r"(?m)^([ \t]*)-[ \t]+", match.group(0))
        item_prefix = item.group(1) if item else "  "
        replacement = key + ":\n" + "".join(f"{item_prefix}- {value}\n" for value in values)
        updated_front = block_pattern.sub(lambda _match: replacement, front_with_newline, count=1).rstrip("\n")
    else:
        replacement = key + ":\n" + "".join(f"  - {value}\n" for value in values)
        updated_front = front.rstrip() + "\n" + replacement.rstrip("\n")
    return "---\n" + updated_front + "\n---\n" + text[end + 5 :]


def render_skill_doc(text: str, payload: dict[str, Any]) -> str:
    children = children_map(payload)
    catalog_lines: list[str] = []

    def visit(parent_id: str | None, depth: int) -> None:
        for node in children.get(parent_id, []):
            if node["status"] != "active":
                continue
            catalog_lines.append("  " * depth + f"- {node['title']}")
            visit(node["kp_id"], depth + 1)

    visit(None, 0)
    deprecated = [node for node in payload["nodes"] if node["status"] == "deprecated"]
    if deprecated:
        catalog_lines.extend(["", "### 已停用标签（ID 保留）", ""])
        for node in deprecated:
            replacements = ", ".join(node["replacement_ids"])
            catalog_lines.append(f"- {node['path']} (`{node['kp_id']}` → {replacements})")
    catalog = "\n".join(catalog_lines).rstrip()
    section_pattern = re.compile(r"(?ms)^## 标签目录\s*\n.*?(?=^## 建议的题目 frontmatter 写法)")
    if not section_pattern.search(text):
        raise ValueError("cannot locate taxonomy catalog section")
    text = section_pattern.sub(f"## 标签目录\n\n{catalog}\n\n", text, count=1)
    counts = {level: sum(1 for node in payload["nodes"] if int(node["level"]) == level and node["status"] == "active") for level in [1, 2, 3]}
    stats_pattern = re.compile(r"(?ms)^## 层级统计\s*\n.*?(?=^## 标签目录)")
    stats = (
        "## 层级统计\n\n"
        f"- 一级模块：{counts[1]} 个\n"
        f"- 二级主题：{counts[2]} 个\n"
        f"- 三级标签：{counts[3]} 个\n\n"
    )
    if stats_pattern.search(text):
        text = stats_pattern.sub(stats, text, count=1)
    return text


def atomic_write(path: Path, text: str, temp_root: Path) -> None:
    temp_root.mkdir(parents=True, exist_ok=True)
    temporary = temp_root / f"{path.name}.tmp"
    temporary.write_text(text, encoding="utf-8", newline="\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temporary, path)


def apply_change(payload: dict[str, Any], change: dict[str, Any], allocated: list[str]) -> None:
    action = str(change.get("action") or "")
    by_id = node_map(payload)
    if action == "add":
        title = str(change.get("title") or "").strip()
        parent_id = change.get("parent_id")
        if not title or "/" in title:
            raise ValueError("add requires a title without slash")
        parent = by_id.get(parent_id) if parent_id else None
        if parent_id and parent is None:
            raise ValueError(f"unknown parent_id: {parent_id}")
        level = int(parent["level"]) + 1 if parent else 1
        if level > 3:
            raise ValueError("taxonomy v1 supports at most three levels")
        if any(node["title"] == title and node["parent_id"] == parent_id and node["status"] == "active" for node in payload["nodes"]):
            raise ValueError("an active sibling already has this title")
        kp_id = allocate_id(payload)
        allocated.append(kp_id)
        sort_order = int(change.get("sort_order") or (max([int(node["sort_order"]) for node in payload["nodes"] if node["parent_id"] == parent_id] or [0]) + 1))
        path_value = f"{parent['path']}/{title}" if parent else title
        payload["nodes"].append(
            {
                "kp_id": kp_id,
                "title": title,
                "parent_id": parent_id,
                "level": level,
                "sort_order": sort_order,
                "path": path_value,
                "aliases": [],
                "status": "active",
                "replacement_ids": [],
                "wiki_path": str(change.get("wiki_path") or ""),
            }
        )
    elif action == "rename":
        kp_id = str(change.get("kp_id") or "")
        node = by_id.get(kp_id)
        title = str(change.get("new_title") or "").strip()
        if not node or not title or "/" in title:
            raise ValueError("rename requires valid kp_id and new_title")
        node["title"] = title
        refresh_subtree(payload, kp_id)
    elif action == "move":
        kp_id = str(change.get("kp_id") or "")
        new_parent_id = change.get("new_parent_id")
        node = by_id.get(kp_id)
        parent = by_id.get(new_parent_id) if new_parent_id else None
        if not node or (new_parent_id and parent is None):
            raise ValueError("move requires valid kp_id and new_parent_id")
        if new_parent_id == kp_id or new_parent_id in {item["kp_id"] for item in descendants(payload, kp_id)}:
            raise ValueError("move would create a parent cycle")
        node["parent_id"] = new_parent_id
        node["sort_order"] = int(change.get("sort_order") or (max([int(item["sort_order"]) for item in payload["nodes"] if item["parent_id"] == new_parent_id] or [0]) + 1))
        refresh_subtree(payload, kp_id)
        if any(int(item["level"]) > 3 for item in [node] + descendants(payload, kp_id)):
            raise ValueError("move would exceed three taxonomy levels")
    elif action in {"deprecate", "merge"}:
        kp_id = str(change.get("kp_id") or change.get("source_id") or "")
        node = by_id.get(kp_id)
        replacements = change.get("replacement_ids")
        if action == "merge" and replacements is None:
            replacements = [change.get("target_id")]
        if not node or not isinstance(replacements, list) or not replacements:
            raise ValueError(f"{action} requires source kp_id and replacement_ids")
        if any(item["status"] == "active" for item in descendants(payload, kp_id)):
            raise ValueError("cannot deprecate a node with active descendants")
        node["status"] = "deprecated"
        node["replacement_ids"] = [str(item) for item in replacements]
    elif action == "reorder":
        kp_id = str(change.get("kp_id") or "")
        node = by_id.get(kp_id)
        if not node:
            raise ValueError("reorder requires valid kp_id")
        node["sort_order"] = int(change.get("sort_order", 0))
    elif action == "assign_question":
        return
    elif action == "delete":
        raise ValueError("knowledge-point IDs are never deleted; use deprecate with replacement_ids")
    else:
        raise ValueError(f"unsupported taxonomy action: {action}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage the stable physics knowledge-point registry.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--proposal")
    source.add_argument("--bootstrap-from-skill", action="store_true")
    source.add_argument("--validate", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--teacher-authorized", action="store_true")
    parser.add_argument("--allow-destructive", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--kb-root", help=argparse.SUPPRESS)
    parser.add_argument("--registry", help=argparse.SUPPRESS)
    parser.add_argument("--skill-doc", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        kb_root = DEFAULT_KB_ROOT
        registry_path = DEFAULT_REGISTRY
        skill_doc = DEFAULT_SKILL_DOC
        if args.kb_root or args.registry or args.skill_doc:
            if not args.test_mode:
                raise PermissionError("custom paths are available only with --test-mode")
            if args.kb_root:
                kb_root = Path(args.kb_root).resolve()
            if args.registry:
                registry_path = Path(args.registry).resolve()
            if args.skill_doc:
                skill_doc = Path(args.skill_doc).resolve()

        if args.bootstrap_from_skill:
            if not args.teacher_authorized:
                raise PermissionError("bootstrap requires --teacher-authorized")
            if registry_path.exists():
                raise FileExistsError("registry already exists; bootstrap never overwrites stable IDs")
            payload = parse_skill_catalog(skill_doc, kb_root)
            registry_path.parent.mkdir(parents=True, exist_ok=True)
            registry_path.write_text(dump_registry(payload), encoding="utf-8", newline="\n")
            counts = {level: sum(1 for node in payload["nodes"] if node["level"] == level) for level in [1, 2, 3]}
            result = {"ok": True, "mode": "bootstrap", "registry": str(registry_path), "node_count": len(payload["nodes"]), "counts": counts, "next_id": payload["next_id"]}
            print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else str(registry_path))
            return 0

        payload = load_registry(registry_path)
        if args.validate:
            result = {"ok": True, "mode": "validate", "registry": str(registry_path), "node_count": len(payload["nodes"]), "next_id": payload["next_id"]}
            print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else "ok")
            return 0

        if not (args.dry_run or args.apply):
            raise ValueError("proposal mode requires --dry-run or --apply")
        if args.apply and not args.teacher_authorized:
            raise PermissionError("--apply requires --teacher-authorized")
        proposal_path = Path(args.proposal).resolve()
        proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
        if not isinstance(proposal, dict) or int(proposal.get("schema_version", 0)) != 1:
            raise ValueError("proposal must be a schema_version 1 JSON object")
        expected_hash = str(proposal.get("expected_registry_sha256") or "").lower()
        if not HASH_RE.fullmatch(expected_hash) or sha256_file(registry_path) != expected_hash:
            raise RuntimeError("registry hash conflict")
        changes = proposal.get("changes")
        if not isinstance(changes, list) or not changes:
            raise ValueError("proposal changes must be a non-empty array")
        actions = {str(change.get("action") or "") for change in changes if isinstance(change, dict)}
        destructive = sorted(actions & DESTRUCTIVE_ACTIONS)
        if args.apply and destructive and not args.allow_destructive:
            raise PermissionError(f"destructive taxonomy actions require --allow-destructive: {destructive}")

        updated = copy.deepcopy(payload)
        allocated: list[str] = []
        for change in changes:
            if not isinstance(change, dict):
                raise ValueError("each taxonomy change must be an object")
            apply_change(updated, change, allocated)
        validate_registry(updated)

        question_updates: dict[Path, tuple[str, str]] = {}
        by_id = node_map(updated)
        exercise_root = (kb_root / "raw" / "exercises").resolve()
        for change in changes:
            if change.get("action") != "assign_question":
                continue
            target_rel = str(change.get("target_path") or "")
            if Path(target_rel).is_absolute() or ".." in Path(target_rel).parts:
                raise ValueError("assign_question target_path must be repository-relative")
            target = ensure_inside(kb_root / target_rel, exercise_root)
            expected_question_hash = str(change.get("expected_sha256") or "").lower()
            if not target.is_file() or not HASH_RE.fullmatch(expected_question_hash) or sha256_file(target) != expected_question_hash:
                raise RuntimeError(f"question hash conflict: {target_rel}")
            ids = [str(item) for item in change.get("knowledge_point_ids", [])]
            has_ai_tags = "ai_extra_tags" in change
            if not ids and not has_ai_tags:
                raise ValueError("assign_question requires knowledge_point_ids and/or ai_extra_tags")
            if ids and any(item not in by_id or by_id[item]["status"] != "active" for item in ids):
                raise ValueError(f"assign_question contains unknown/deprecated IDs: {ids}")
            before = target.read_text(encoding="utf-8")
            after = before
            if ids:
                after = update_frontmatter_list(after, "knowledge_points", [str(by_id[item]["path"]) for item in ids])
                after = update_frontmatter_list(after, "knowledge_point_ids", ids)
            if has_ai_tags:
                after = update_frontmatter_list(after, "ai_extra_tags", validate_ai_extra_tags(change["ai_extra_tags"]))
            question_updates[target] = (before, after)

        registry_before = registry_path.read_text(encoding="utf-8")
        registry_after = dump_registry(updated)
        skill_before = skill_doc.read_text(encoding="utf-8")
        skill_after = render_skill_doc(skill_before, updated)
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        report_root = kb_root / "skills" / "_ops" / "runtime" / "reports" / "role_d_taxonomy" / timestamp
        backup_root = kb_root / "skills" / "_ops" / "runtime" / "state" / "backups" / "role_d_taxonomy" / timestamp
        report_root.mkdir(parents=True, exist_ok=True)
        diff_parts: list[str] = []
        for label, before, after in [(str(registry_path), registry_before, registry_after), (str(skill_doc), skill_before, skill_after)]:
            if before != after:
                diff_parts.extend(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=label, tofile=label))
        for target, (before, after) in question_updates.items():
            diff_parts.extend(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=str(target), tofile=str(target)))
        diff_path = report_root / "taxonomy.diff"
        diff_path.write_text("".join(diff_parts), encoding="utf-8", newline="\n")

        applied = False
        if args.apply:
            backup_root.mkdir(parents=True, exist_ok=True)
            shutil.copy2(registry_path, backup_root / "knowledge-points.yaml")
            shutil.copy2(skill_doc, backup_root / "SKILL.md")
            for target in question_updates:
                backup = backup_root / target.relative_to(kb_root)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            atomic_write(registry_path, registry_after, backup_root / "tmp")
            atomic_write(skill_doc, skill_after, backup_root / "tmp")
            for target, (_before, after) in question_updates.items():
                atomic_write(target, after, backup_root / "tmp")
            validate_registry(load_registry(registry_path))
            applied = True

        result = {
            "ok": True,
            "mode": "apply" if args.apply else "dry-run",
            "applied": applied,
            "allocated_ids": allocated,
            "destructive_actions": destructive,
            "question_update_count": len(question_updates),
            "node_count": len(updated["nodes"]),
            "next_id": updated["next_id"],
            "diff": str(diff_path),
            "backup_root": str(backup_root) if applied else None,
        }
        report_path = report_root / "taxonomy_report.json"
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["report"] = str(report_path)
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else str(report_path))
        return 0
    except Exception as exc:  # noqa: BLE001
        error = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
