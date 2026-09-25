#!/usr/bin/env python3
"""Validate exercise Markdown against the current RAG exercise parser.

This is a gate script for exercise imports. It intentionally mirrors the
regular-expression parser used by BGE-M3/scripts/rag_pipeline.py so that an
exercise file that passes this check will not become an empty RAG chunk because
of heading spacing or frontmatter list formatting.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


QUESTION_RE = re.compile(r"## 题目\n\n(.*?)(?=\n## 答案)", re.DOTALL)
ANSWER_RE = re.compile(r"## 答案\n\n(.*?)(?=\n## 详解)", re.DOTALL)
EXPLANATION_RE = re.compile(r"## 详解\n\n(.*)", re.DOTALL)
KP_SECTION_RE = re.compile(
    r"^knowledge_points:[^\S\r\n]*\r?\n((?:^[ \t]*-[ \t]+.+(?:\r?\n|$))+)",
    re.MULTILINE,
)
IMAGE_RE = re.compile(r"!\[[^\]]*]\([^)]+\)")


def strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return text


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    fm: dict[str, str] = {}
    for line in parts[1].splitlines():
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if match:
            fm[match.group(1)] = match.group(2).strip().strip("'\"")
    return fm


def clean_for_rag(text: str) -> str:
    return IMAGE_RE.sub("", text).strip()


def collect_markdown(paths: list[str], kb_root: Path) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_absolute():
            path = kb_root / path
        path = path.resolve()
        if path.is_dir():
            files.extend(sorted(path.rglob("*.md")))
        elif path.is_file() and path.suffix.lower() == ".md":
            files.append(path)
        else:
            raise FileNotFoundError(f"Exercise path not found or not markdown: {path}")
    return sorted(dict.fromkeys(files))


def load_chunks(chunks_path: Path) -> dict[str, dict[str, Any]]:
    chunks: dict[str, dict[str, Any]] = {}
    if not chunks_path.exists():
        raise FileNotFoundError(f"chunks.jsonl not found: {chunks_path}")
    for line in chunks_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        chunk_id = str(item.get("id", ""))
        if chunk_id:
            chunks[chunk_id] = item
    return chunks


def validate_file(path: Path, kb_root: Path, chunks: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    body = strip_frontmatter(text)
    fm = parse_frontmatter(text)
    question_id = fm.get("id") or path.stem

    q_match = QUESTION_RE.search(body)
    a_match = ANSWER_RE.search(body)
    e_match = EXPLANATION_RE.search(body)
    kp_match = KP_SECTION_RE.search(text)

    q_text = clean_for_rag(q_match.group(1)) if q_match else ""
    a_text = a_match.group(1).strip() if a_match else ""
    e_text = clean_for_rag(e_match.group(1)) if e_match else ""
    kps = re.findall(r"^[ \t]*-[ \t]+(.+)$", kp_match.group(1), re.MULTILINE) if kp_match else []
    kps = [kp.strip().strip('"') for kp in kps]

    issues: list[dict[str, Any]] = []
    if not text.startswith("---\n"):
        issues.append({"code": "missing_frontmatter"})
    if not fm.get("id"):
        issues.append({"code": "missing_id"})
    if not fm.get("question_type"):
        issues.append({"code": "missing_question_type"})
    if not q_match:
        issues.append({"code": "rag_heading_spacing_question", "message": "`## 题目` must be followed by one blank line"})
    if not a_match:
        issues.append({"code": "rag_heading_spacing_answer", "message": "`## 答案` must be followed by one blank line"})
    if not e_match:
        issues.append({"code": "rag_heading_spacing_explanation", "message": "`## 详解` must be followed by one blank line"})
    if not q_text:
        issues.append({"code": "empty_rag_question_text"})
    if not a_text:
        issues.append({"code": "empty_rag_answer_text"})
    if not e_text:
        issues.append({"code": "empty_rag_explanation_text"})
    if not kps:
        issues.append(
            {
                "code": "rag_knowledge_points_not_parseable",
                "message": "knowledge_points must contain at least one YAML list item",
            }
        )

    chunk_check: dict[str, Any] | None = None
    if chunks is not None:
        chunk_id = f"exercise-{question_id}"
        chunk = chunks.get(chunk_id)
        if not chunk:
            issues.append({"code": "rag_chunk_missing", "chunk_id": chunk_id})
            chunk_check = {"chunk_id": chunk_id, "exists": False}
        else:
            chunk_text = str(chunk.get("text", ""))
            chunk_kps = chunk.get("knowledge_points") or []
            chunk_check = {
                "chunk_id": chunk_id,
                "exists": True,
                "text_chars": len(chunk_text),
                "knowledge_points": chunk_kps,
            }
            if len(chunk_text.strip()) <= len("题目：\n答案：\n详解："):
                issues.append({"code": "rag_chunk_text_empty", "chunk_id": chunk_id})
            if not chunk_kps:
                issues.append({"code": "rag_chunk_knowledge_points_empty", "chunk_id": chunk_id})

    return {
        "file": path.relative_to(kb_root).as_posix() if path.is_relative_to(kb_root) else str(path),
        "id": question_id,
        "question_chars": len(q_text),
        "answer_chars": len(a_text),
        "explanation_chars": len(e_text),
        "knowledge_points": kps,
        "chunk": chunk_check,
        "issue_count": len(issues),
        "issues": issues,
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    kb_root = Path(args.kb_root).resolve()
    files = collect_markdown(args.paths, kb_root)
    chunks = None
    if args.check_chunks:
        chunks_path = Path(args.chunks_path)
        if not chunks_path.is_absolute():
            chunks_path = kb_root / chunks_path
        chunks = load_chunks(chunks_path.resolve())
    results = [validate_file(path, kb_root, chunks) for path in files]
    issue_count = sum(item["issue_count"] for item in results)
    return {
        "ok": issue_count == 0,
        "issue_count": issue_count,
        "file_count": len(files),
        "checked_chunks": bool(args.check_chunks),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate exercise Markdown RAG parser compatibility.")
    parser.add_argument("paths", nargs="+", help="Exercise Markdown files or directories")
    parser.add_argument("--kb-root", default=".", help="LLMWiki_BGE-M3 root")
    parser.add_argument("--check-chunks", action="store_true", help="Also validate BGE-M3 chunks.jsonl after RAG rebuild")
    parser.add_argument("--chunks-path", default="BGE-M3/runtime/data/chunks.jsonl")
    parser.add_argument("--out", help="Write JSON report to this path")
    parser.add_argument("--json", action="store_true", help="Print full JSON report")
    parser.add_argument("--no-fail", action="store_true", help="Return 0 even if issues are found")
    args = parser.parse_args(argv)

    report = build_report(args)
    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = Path(args.kb_root).resolve() / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({k: report[k] for k in ("ok", "issue_count", "file_count", "checked_chunks")}, ensure_ascii=False))

    if report["issue_count"] and not args.no_fail:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
