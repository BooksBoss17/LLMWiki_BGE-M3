#!/usr/bin/env python
"""Apply fixed-schema, hash-guarded updates to Role D managed Wiki pages."""
from __future__ import annotations

import argparse
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
DEFAULT_KB_ROOT = SCRIPT.parents[4]
DEFAULT_MANAGED_CONFIG = SCRIPT.parent.parent / "references" / "managed-graph-pages.yaml"
SAFE_ACTIONS = {"append_relation", "append_backlink", "create_managed_page"}
DESTRUCTIVE_ACTIONS = {"delete_page", "rename_page", "move_page", "merge_pages", "replace_page"}
SOURCE_SKILLS = {"textbook-import", "standards-import", "exercise-bank-import", "video-transcript-import"}
BEGIN = "<!-- ROLE_D_MANAGED:BEGIN -->"
END = "<!-- ROLE_D_MANAGED:END -->"
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
HANDOFF_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(root.resolve())
    return resolved


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required; run install_skill_dependencies.py --install --all") from exc
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"YAML must contain an object: {path}")
    return payload


def normalize_page_name(value: Any) -> str:
    name = str(value or "").strip().replace("\\", "/")
    if "/" in name or name in {"", ".", ".."} or ".." in Path(name).parts:
        raise ValueError(f"page must be a filename under LLMWiki/concepts: {name}")
    if not name.lower().endswith(".md"):
        name += ".md"
    return name


def page_is_self_managed(path: Path) -> bool:
    if not path.exists():
        return False
    head = path.read_text(encoding="utf-8", errors="ignore")[:3000]
    return bool(re.search(r"(?m)^role_d_managed:\s*true\s*$", head, flags=re.IGNORECASE))


def validate_handoff(payload: dict[str, Any]) -> None:
    if int(payload.get("schema_version", 0)) != 1:
        raise ValueError("handoff schema_version must be 1")
    handoff_id = str(payload.get("handoff_id") or "")
    if not HANDOFF_ID_RE.fullmatch(handoff_id):
        raise ValueError("handoff_id must be safe ASCII")
    if str(payload.get("source_skill") or "") not in SOURCE_SKILLS:
        raise ValueError("source_skill does not trigger Role D graph maintenance")
    for field in ["changed_raw_files", "changed_wiki_files", "question_ids", "kp_ids"]:
        if not isinstance(payload.get(field), list):
            raise ValueError(f"{field} must be an array")
    gates = payload.get("quality_gates")
    if not isinstance(gates, dict) or gates.get("passed") is not True:
        raise PermissionError("import quality gates have not passed")
    if int(gates.get("unresolved_issues", 0)) != 0:
        raise PermissionError("import handoff contains unresolved issues")
    if not isinstance(payload.get("file_hashes"), dict):
        raise ValueError("file_hashes must be an object")
    if not isinstance(payload.get("graph_changes"), list):
        raise ValueError("graph_changes must be an array")


def expected_hash_for(payload: dict[str, Any], page_name: str, change: dict[str, Any]) -> str:
    direct = str(change.get("expected_sha256") or "")
    hashes = payload.get("file_hashes", {})
    candidates = [page_name, f"LLMWiki/concepts/{page_name}"]
    expected = direct or next((str(hashes.get(key)) for key in candidates if hashes.get(key)), "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError(f"missing valid expected hash for managed page: {page_name}")
    return expected


def validate_bullet(text: Any) -> str:
    value = str(text or "").strip()
    if not value.startswith("- ") or "\n" in value or len(value) > 1000:
        raise ValueError("managed additions must be a single Markdown bullet no longer than 1000 characters")
    if BEGIN in value or END in value or value.startswith("#"):
        raise ValueError("managed addition contains forbidden control text")
    return value


def append_managed_line(text: str, line: str) -> str:
    if line in text:
        return text
    if BEGIN in text or END in text:
        if text.count(BEGIN) != 1 or text.count(END) != 1 or text.index(BEGIN) > text.index(END):
            raise ValueError("managed block markers are malformed")
        return text.replace(END, f"{line}\n{END}", 1)
    block = f"\n\n## Role D 导入增量\n\n{BEGIN}\n{line}\n{END}\n"
    return text.rstrip() + block


def replace_managed_block(text: str, lines: list[str]) -> str:
    if text.count(BEGIN) != 1 or text.count(END) != 1 or text.index(BEGIN) > text.index(END):
        raise ValueError("replace_page requires one existing managed block")
    body = "\n".join(validate_bullet(item) for item in lines)
    pattern = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), flags=re.DOTALL)
    return pattern.sub(f"{BEGIN}\n{body}\n{END}", text, count=1)


def create_managed_page(change: dict[str, Any]) -> str:
    title = str(change.get("title") or "").strip()
    summary = str(change.get("summary") or "").strip()
    relations = change.get("relations", [])
    if not title or not summary or len(summary) > 1500:
        raise ValueError("create_managed_page requires title and a summary no longer than 1500 characters")
    if "#" in summary[:1] or BEGIN in summary or END in summary:
        raise ValueError("summary contains forbidden control text")
    if not isinstance(relations, list):
        raise ValueError("relations must be an array")
    bullets = [validate_bullet(item) for item in relations]
    block = "\n".join(bullets) if bullets else "- 暂无新增关系。"
    return (
        "---\n"
        f"title: {title}\n"
        "type: concept\n"
        "tags: [physics, knowledge-graph, role-d-managed]\n"
        "role_d_managed: true\n"
        f"created: '{dt.date.today().isoformat()}'\n"
        f"updated: '{dt.date.today().isoformat()}'\n"
        "---\n\n"
        f"# {title}\n\n{summary}\n\n## 受控关系\n\n{BEGIN}\n{block}\n{END}\n"
    )


def all_wiki_stems(wiki_root: Path, planned_new: set[str]) -> set[str]:
    stems = {path.stem for path in wiki_root.rglob("*.md")}
    stems.update(Path(name).stem for name in planned_new)
    return stems


def validate_new_links(texts: list[str], wiki_root: Path, planned_new: set[str]) -> list[str]:
    stems = all_wiki_stems(wiki_root, planned_new)
    missing: list[str] = []
    for text in texts:
        for target in WIKILINK_RE.findall(text):
            stem = Path(target.strip()).name
            if stem not in stems and target.strip() not in stems:
                missing.append(target.strip())
    return sorted(set(missing))


def inbound_pages(wiki_root: Path, old_stem: str) -> list[Path]:
    pattern = re.compile(rf"\[\[{re.escape(old_stem)}(?:[#|\]])")
    hits: list[Path] = []
    for page in wiki_root.rglob("*.md"):
        if pattern.search(page.read_text(encoding="utf-8", errors="ignore")):
            hits.append(page)
    return hits


def atomic_write(path: Path, text: str, temp_root: Path) -> None:
    temp_root.mkdir(parents=True, exist_ok=True)
    temporary = temp_root / f"{path.name}.tmp"
    temporary.write_text(text, encoding="utf-8", newline="\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temporary, path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update Role D managed physics knowledge graph pages.")
    parser.add_argument("--handoff", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--teacher-authorized", action="store_true")
    parser.add_argument("--allow-destructive", action="store_true")
    parser.add_argument("--result-out")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--kb-root", help=argparse.SUPPRESS)
    parser.add_argument("--managed-config", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.allow_destructive and not (args.apply and args.teacher_authorized):
            raise PermissionError("--allow-destructive requires --apply --teacher-authorized")
        kb_root = DEFAULT_KB_ROOT
        config_path = DEFAULT_MANAGED_CONFIG
        if args.kb_root or args.managed_config:
            if not args.test_mode:
                raise PermissionError("custom roots are available only with --test-mode")
            if args.kb_root:
                kb_root = Path(args.kb_root).resolve()
            if args.managed_config:
                config_path = Path(args.managed_config).resolve()
        handoff_path = Path(args.handoff).resolve()
        payload = json.loads(handoff_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("handoff must be a JSON object")
        validate_handoff(payload)
        config = load_yaml(config_path)
        managed_names = {normalize_page_name(item) for item in config.get("managed_pages", [])}
        wiki_root = (kb_root / "LLMWiki").resolve()
        concepts_root = (wiki_root / "concepts").resolve()
        changes = payload["graph_changes"]
        safe_changes = [item for item in changes if isinstance(item, dict) and item.get("action") in SAFE_ACTIONS]
        destructive_changes = [item for item in changes if isinstance(item, dict) and item.get("action") in DESTRUCTIVE_ACTIONS]
        unknown = [item for item in changes if not isinstance(item, dict) or item.get("action") not in SAFE_ACTIONS | DESTRUCTIVE_ACTIONS]
        if unknown:
            raise ValueError(f"unknown graph action(s): {unknown}")

        original: dict[Path, str | None] = {}
        planned: dict[Path, str | None] = {}
        new_link_texts: list[str] = []
        planned_new: set[str] = set()

        def load_existing(page_name: str, change: dict[str, Any]) -> tuple[Path, str]:
            path = ensure_inside(concepts_root / page_name, concepts_root)
            if not path.is_file():
                raise FileNotFoundError(path)
            if page_name not in managed_names and not page_is_self_managed(path):
                raise PermissionError(f"page is not Role D managed: {page_name}")
            expected = expected_hash_for(payload, page_name, change)
            actual = sha256_file(path)
            if actual != expected:
                raise RuntimeError(f"hash conflict for {page_name}: expected {expected}, actual {actual}")
            if path not in original:
                original[path] = path.read_text(encoding="utf-8")
                planned[path] = original[path]
            current = planned[path]
            if current is None:
                raise ValueError(f"page already scheduled for deletion: {page_name}")
            return path, current

        for change in safe_changes:
            action = str(change["action"])
            page_name = normalize_page_name(change.get("page"))
            path = ensure_inside(concepts_root / page_name, concepts_root)
            if action == "create_managed_page":
                if path.exists() or path in planned:
                    raise FileExistsError(path)
                if change.get("register_managed") is not True:
                    raise PermissionError("new graph page requires register_managed=true")
                content = create_managed_page(change)
                original[path] = None
                planned[path] = content
                planned_new.add(page_name)
                new_link_texts.append(content)
            else:
                path, current = load_existing(page_name, change)
                line = validate_bullet(change.get("text"))
                planned[path] = append_managed_line(current, line)
                new_link_texts.append(line)

        destructive_to_apply = destructive_changes if args.allow_destructive else []
        for change in destructive_to_apply:
            action = str(change["action"])
            if action in {"delete_page", "replace_page"}:
                page_name = normalize_page_name(change.get("page"))
                path, current = load_existing(page_name, change)
                if action == "delete_page":
                    inbound = [item for item in inbound_pages(wiki_root, path.stem) if item != path]
                    if inbound:
                        raise PermissionError(
                            f"delete_page would break inbound links: {[str(item.relative_to(wiki_root)) for item in inbound]}"
                        )
                    planned[path] = None
                else:
                    lines = change.get("lines")
                    if not isinstance(lines, list):
                        raise ValueError("replace_page requires lines array")
                    planned[path] = replace_managed_block(current, [str(item) for item in lines])
                    new_link_texts.extend(str(item) for item in lines)
            elif action in {"rename_page", "move_page"}:
                old_name = normalize_page_name(change.get("page") or change.get("old_page"))
                new_name = normalize_page_name(change.get("new_page"))
                old_path, current = load_existing(old_name, change)
                new_path = ensure_inside(concepts_root / new_name, concepts_root)
                if new_path.exists() or new_path in planned:
                    raise FileExistsError(new_path)
                inbound = [item for item in inbound_pages(wiki_root, old_path.stem) if item != old_path]
                unmanaged = [item for item in inbound if item.name not in managed_names and not page_is_self_managed(item)]
                if unmanaged:
                    raise PermissionError(
                        f"unmanaged pages link to {old_name}: {[str(item.relative_to(wiki_root)) for item in unmanaged]}"
                    )
                for inbound_path in inbound:
                    _loaded_path, inbound_content = load_existing(inbound_path.name, {})
                    planned[inbound_path] = inbound_content.replace(f"[[{old_path.stem}]]", f"[[{new_path.stem}]]")
                original[new_path] = None
                planned[new_path] = current.replace(f"title: {old_path.stem}", f"title: {new_path.stem}", 1).replace(
                    f"# {old_path.stem}", f"# {new_path.stem}", 1
                )
                planned[old_path] = None
            elif action == "merge_pages":
                source_name = normalize_page_name(change.get("source_page"))
                target_name = normalize_page_name(change.get("target_page"))
                source_path, _source = load_existing(source_name, change)
                target_change = dict(change)
                target_change["expected_sha256"] = change.get("target_expected_sha256")
                target_path, target_content = load_existing(target_name, target_change)
                note = validate_bullet(change.get("merge_note"))
                planned[target_path] = append_managed_line(target_content, note)
                inbound = [item for item in inbound_pages(wiki_root, source_path.stem) if item != source_path]
                unmanaged = [item for item in inbound if item.name not in managed_names and not page_is_self_managed(item)]
                if unmanaged:
                    raise PermissionError(
                        f"unmanaged pages link to {source_name}: {[str(item.relative_to(wiki_root)) for item in unmanaged]}"
                    )
                for inbound_path in inbound:
                    _loaded_path, inbound_content = load_existing(inbound_path.name, {})
                    planned[inbound_path] = inbound_content.replace(f"[[{source_path.stem}]]", f"[[{target_path.stem}]]")
                planned[source_path] = None

        missing_links = validate_new_links(new_link_texts, wiki_root, planned_new)
        if missing_links:
            raise ValueError(f"new graph changes contain unresolved wikilinks: {missing_links}")

        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        report_root = kb_root / "skills" / "_ops" / "runtime" / "reports" / "role_d_graph" / f"{payload['handoff_id']}_{timestamp}"
        backup_root = kb_root / "skills" / "_ops" / "runtime" / "state" / "backups" / "role_d_graph" / f"{payload['handoff_id']}_{timestamp}"
        report_root.mkdir(parents=True, exist_ok=True)
        diff_parts: list[str] = []
        for path in sorted(planned, key=lambda item: str(item)):
            before = original.get(path) or ""
            after = planned[path] or ""
            if before == after:
                continue
            rel = str(path.relative_to(kb_root)).replace("\\", "/")
            diff_parts.extend(
                difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=rel, tofile=rel)
            )
        diff_path = report_root / "graph_update.diff"
        diff_path.write_text("".join(diff_parts), encoding="utf-8", newline="\n")

        changed_paths = [path for path in planned if original.get(path) != planned[path]]
        applied = False
        if args.apply and changed_paths:
            for path in changed_paths:
                before = original.get(path)
                if before is not None:
                    backup_path = backup_root / path.relative_to(kb_root)
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, backup_path)
            for path in changed_paths:
                after = planned[path]
                if after is None:
                    path.unlink()
                else:
                    atomic_write(path, after, backup_root / "tmp")
            applied = True

        pending_destructive = destructive_changes if not args.allow_destructive else []
        if not changes:
            status = "no_change"
        elif args.dry_run:
            status = "dry_run"
        elif pending_destructive:
            status = "approval_required"
        elif destructive_to_apply:
            status = "applied_destructive"
        else:
            status = "applied_safe"
        safe_effective = sum(1 for change in safe_changes if change)
        completion_ready = status in {"no_change", "applied_safe", "applied_destructive", "approval_required"}
        changed_page_hashes: dict[str, str | None] = {}
        if applied:
            for path in changed_paths:
                relative = str(path.relative_to(kb_root)).replace("\\", "/")
                changed_page_hashes[relative] = sha256_file(path) if path.is_file() else None
        result: dict[str, Any] = {
            "ok": True,
            "schema_version": 1,
            "handoff_id": payload["handoff_id"],
            "status": status,
            "completion_ready": completion_ready,
            "validated": True,
            "applied": applied,
            "safe_change_count": safe_effective,
            "destructive_change_count": len(destructive_changes),
            "pending_destructive": pending_destructive,
            "changed_pages": [str(path.relative_to(kb_root)).replace("\\", "/") for path in changed_paths],
            "changed_page_hashes": changed_page_hashes,
            "diff": str(diff_path),
            "backup_root": str(backup_root) if applied else None,
        }
        result_path = Path(args.result_out).resolve() if args.result_out else report_root / "graph_update_result.json"
        if args.result_out and not args.test_mode:
            allowed_result_root = (kb_root / "skills" / "_ops" / "runtime").resolve()
            ensure_inside(result_path, allowed_result_root)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["result_path"] = str(result_path)
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else str(result_path))
        return 0
    except Exception as exc:  # noqa: BLE001
        error = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
