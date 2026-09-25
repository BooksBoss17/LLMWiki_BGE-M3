#!/usr/bin/env python
"""Create and verify the mandatory Role A -> Role D import handoff."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve()
SKILLS_ROOT = SCRIPT.parents[2]
DEFAULT_KB_ROOT = SKILLS_ROOT.parent
HANDOFF_ROOT = SKILLS_ROOT / "_ops" / "runtime" / "state" / "import_handoffs"
GRAPH_SOURCES = {"textbook-import", "standards-import", "exercise-bank-import", "video-transcript-import"}
NON_GRAPH_SOURCES = {"bemarkdown", "student-data-import"}
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


def safe_repo_path(kb_root: Path, value: Any) -> tuple[str, Path]:
    rel = str(value or "").replace("\\", "/").strip()
    candidate = Path(rel)
    if not rel or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"unsafe repository path: {rel}")
    if not (rel.startswith("raw/") or rel.startswith("LLMWiki/")):
        raise PermissionError(f"handoff may hash only raw/ or LLMWiki/ files: {rel}")
    path = ensure_inside(kb_root / candidate, kb_root)
    return rel, path


def validate_handoff(payload: dict[str, Any]) -> None:
    if int(payload.get("schema_version", 0)) != 1:
        raise ValueError("schema_version must be 1")
    if not HANDOFF_ID_RE.fullmatch(str(payload.get("handoff_id") or "")):
        raise ValueError("handoff_id must be safe ASCII")
    source = str(payload.get("source_skill") or "")
    if source not in GRAPH_SOURCES | NON_GRAPH_SOURCES:
        raise ValueError(f"unsupported source_skill: {source}")
    if bool(payload.get("requires_graph")) != (source in GRAPH_SOURCES):
        raise ValueError("requires_graph does not match source_skill policy")
    for field in ["changed_raw_files", "changed_wiki_files", "question_ids", "kp_ids", "graph_changes"]:
        if not isinstance(payload.get(field), list):
            raise ValueError(f"{field} must be an array")
    gates = payload.get("quality_gates")
    if not isinstance(gates, dict) or gates.get("passed") is not True or int(gates.get("unresolved_issues", -1)) != 0:
        raise PermissionError("quality gates must be passed with zero unresolved issues")
    hashes = payload.get("file_hashes")
    if not isinstance(hashes, dict):
        raise ValueError("file_hashes must be an object")
    for rel, digest in hashes.items():
        if not isinstance(rel, str) or not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
            raise ValueError(f"invalid file hash entry: {rel}")


def normalize_spec(spec: dict[str, Any], kb_root: Path) -> dict[str, Any]:
    source = str(spec.get("source_skill") or "")
    handoff_id = str(spec.get("handoff_id") or "")
    if source not in GRAPH_SOURCES | NON_GRAPH_SOURCES:
        raise ValueError(f"unsupported source_skill: {source}")
    if not HANDOFF_ID_RE.fullmatch(handoff_id):
        raise ValueError("handoff_id must be safe ASCII")
    gates = spec.get("quality_gates")
    if not isinstance(gates, dict) or gates.get("passed") is not True or int(gates.get("unresolved_issues", -1)) != 0:
        raise PermissionError("quality gates must be passed with zero unresolved issues")
    arrays: dict[str, list[Any]] = {}
    for field in ["changed_raw_files", "changed_wiki_files", "question_ids", "kp_ids", "graph_changes"]:
        value = spec.get(field, [])
        if not isinstance(value, list):
            raise ValueError(f"{field} must be an array")
        arrays[field] = value
    if source in GRAPH_SOURCES and not arrays["changed_raw_files"] and not arrays["changed_wiki_files"]:
        raise ValueError("teaching import handoff must list at least one changed raw/Wiki file")

    hash_values: list[Any] = []
    hash_values.extend(arrays["changed_raw_files"])
    hash_values.extend(arrays["changed_wiki_files"])
    explicit = spec.get("hash_paths", [])
    if not isinstance(explicit, list):
        raise ValueError("hash_paths must be an array")
    hash_values.extend(explicit)
    for change in arrays["graph_changes"]:
        if not isinstance(change, dict):
            raise ValueError("every graph change must be an object")
        page = change.get("page") or change.get("old_page") or change.get("source_page")
        if page:
            name = str(page)
            if not name.lower().endswith(".md"):
                name += ".md"
            hash_values.append(f"LLMWiki/concepts/{name}")
        target_page = change.get("target_page")
        if target_page:
            name = str(target_page)
            if not name.lower().endswith(".md"):
                name += ".md"
            hash_values.append(f"LLMWiki/concepts/{name}")

    file_hashes: dict[str, str] = {}
    for value in hash_values:
        rel, path = safe_repo_path(kb_root, value)
        if path.exists():
            if not path.is_file():
                raise ValueError(f"hash path is not a file: {rel}")
            file_hashes[rel] = sha256_file(path)
        elif rel in [str(item).replace("\\", "/") for item in arrays["changed_raw_files"] + arrays["changed_wiki_files"]]:
            raise FileNotFoundError(path)

    payload = {
        "schema_version": 1,
        "handoff_id": handoff_id,
        "source_skill": source,
        "requires_graph": source in GRAPH_SOURCES,
        "changed_raw_files": [str(item).replace("\\", "/") for item in arrays["changed_raw_files"]],
        "changed_wiki_files": [str(item).replace("\\", "/") for item in arrays["changed_wiki_files"]],
        "question_ids": [str(item) for item in arrays["question_ids"]],
        "kp_ids": [str(item) for item in arrays["kp_ids"]],
        "quality_gates": {"passed": True, "unresolved_issues": 0, "reports": list(gates.get("reports", []))},
        "file_hashes": file_hashes,
        "graph_changes": arrays["graph_changes"],
    }
    validate_handoff(payload)
    return payload


def create_handoff(args: argparse.Namespace) -> int:
    kb_root = DEFAULT_KB_ROOT
    if args.kb_root:
        if not args.test_mode:
            raise PermissionError("--kb-root is available only with --test-mode")
        kb_root = Path(args.kb_root).resolve()
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ValueError("handoff spec must be a JSON object")
    payload = normalize_spec(spec, kb_root)
    out = Path(args.out).resolve() if args.out else (kb_root / "skills" / "_ops" / "runtime" / "state" / "import_handoffs" / payload["handoff_id"] / "import_handoff.json")
    if not args.test_mode:
        ensure_inside(out, HANDOFF_ROOT)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {"ok": True, "handoff": str(out), "handoff_id": payload["handoff_id"], "requires_graph": payload["requires_graph"], "file_hash_count": len(payload["file_hashes"])}
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else str(out))
    return 0


def verify_handoff(args: argparse.Namespace) -> int:
    handoff_path = Path(args.handoff).resolve()
    out = handoff_path.parent / "handoff_completion.json"
    out.unlink(missing_ok=True)
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    if not isinstance(handoff, dict):
        raise ValueError("handoff must be a JSON object")
    validate_handoff(handoff)
    graph_result: dict[str, Any] | None = None
    graph_mutated_paths: set[str] = set()
    if handoff["requires_graph"]:
        if not args.graph_result:
            raise PermissionError("teaching imports require --graph-result before completion")
        graph_result = json.loads(Path(args.graph_result).read_text(encoding="utf-8"))
        if not isinstance(graph_result, dict):
            raise ValueError("graph result must be a JSON object")
        if graph_result.get("handoff_id") != handoff["handoff_id"]:
            raise ValueError("graph result belongs to a different handoff")
        if graph_result.get("ok") is not True or graph_result.get("validated") is not True or graph_result.get("completion_ready") is not True:
            raise PermissionError("graph update result is not completion-ready")
        if graph_result.get("status") not in {"no_change", "applied_safe", "applied_destructive", "approval_required"}:
            raise PermissionError(f"graph update status cannot complete import: {graph_result.get('status')}")
        if graph_result.get("applied") is True:
            changed_pages = graph_result.get("changed_pages")
            post_hashes = graph_result.get("changed_page_hashes")
            if not isinstance(changed_pages, list) or not isinstance(post_hashes, dict):
                raise ValueError("applied graph result must bind changed pages to post-apply hashes")
            for value in changed_pages:
                normalized, path = safe_repo_path(DEFAULT_KB_ROOT, value)
                if normalized not in post_hashes:
                    raise ValueError(f"graph changed page has no post-apply hash: {normalized}")
                expected = post_hashes[normalized]
                if expected is None:
                    if path.exists():
                        raise ValueError(f"graph result expected deleted page: {normalized}")
                else:
                    if not re.fullmatch(r"[0-9a-f]{64}", str(expected)):
                        raise ValueError(f"invalid graph post-apply hash: {normalized}")
                    if not path.is_file() or sha256_file(path) != expected:
                        raise ValueError(f"graph post-apply hash mismatch: {normalized}")
                graph_mutated_paths.add(normalized)
    for value in handoff["changed_raw_files"] + handoff["changed_wiki_files"]:
        normalized, _ = safe_repo_path(DEFAULT_KB_ROOT, value)
        if normalized not in handoff["file_hashes"]:
            raise ValueError(f"changed file has no hash: {normalized}")
    for rel, expected_digest in handoff["file_hashes"].items():
        normalized, path = safe_repo_path(DEFAULT_KB_ROOT, rel)
        if normalized in graph_mutated_paths:
            continue
        if not path.exists():
            raise FileNotFoundError(path)
        if not path.is_file():
            raise ValueError(f"hash path is not a file: {normalized}")
        actual_digest = sha256_file(path)
        if actual_digest != expected_digest:
            raise ValueError(f"file hash mismatch: {normalized}")
    completion = {
        "ok": True,
        "schema_version": 1,
        "handoff_id": handoff["handoff_id"],
        "source_skill": handoff["source_skill"],
        "graph_status": graph_result.get("status") if graph_result else "not_required",
        "rag_rebuild_allowed": True,
        "import_may_be_declared_complete_after_rag_rebuild": True,
    }
    temporary_out = out.with_suffix(out.suffix + ".tmp")
    temporary_out.write_text(json.dumps(completion, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_out.replace(out)
    completion["completion"] = str(out)
    print(json.dumps(completion, ensure_ascii=False, indent=2) if args.json else str(out))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or verify Role A import handoffs.")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--spec", required=True)
    create.add_argument("--out")
    create.add_argument("--json", action="store_true")
    create.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)
    create.add_argument("--kb-root", help=argparse.SUPPRESS)
    verify = sub.add_parser("verify")
    verify.add_argument("--handoff", required=True)
    verify.add_argument("--graph-result")
    verify.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return create_handoff(args) if args.command == "create" else verify_handoff(args)
    except Exception as exc:  # noqa: BLE001
        error = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
