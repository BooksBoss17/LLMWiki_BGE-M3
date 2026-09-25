#!/usr/bin/env python3
"""Materialize hash-bound question source slices from a prepared DOCX manifest.

This helper is deliberately limited to Role A source evidence.  It verifies the
source DOCX hash recorded by p0p1_source_manifest.py, copies the exact question
context and relationship metadata into a small runtime artifact, and creates
the v3 source-slice binding consumed by agent_mission_orchestrator.py.
It never edits raw questions, campaign state, receipts, Wiki, or RAG assets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any


SCHEMA_VERSION = 3


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def materialize_slice(root: Path, manifest_path: Path, question_id: str, output_root: Path | None) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    source_path = str(manifest.get("source_path") or "")
    source_sha = str(manifest.get("source_sha256") or "").lower()
    if not source_path or len(source_sha) != 64:
        raise ValueError("manifest must bind source_path and source_sha256")
    source_file = (root / source_path).resolve()
    if not source_file.is_file() or sha256_file(source_file) != source_sha:
        raise ValueError(f"source hash drift: {source_file}")

    question = next((row for row in manifest.get("questions", []) if row.get("question_id") == question_id), None)
    if not isinstance(question, dict):
        raise ValueError(f"question is not present in manifest: {question_id}")
    context = str(question.get("question_context") or "")
    if not context.strip():
        raise ValueError(f"empty source context: {question_id}")

    cache_root = (output_root or root / "skills/_ops/runtime/state/agent_batches_v3/p0p1-optics/source-cache" / source_sha).resolve()
    markdown_path = cache_root / f"{question_id}.source-slice.md"
    json_path = cache_root / f"{question_id}.source-slice.json"
    manifest_hash = sha256_file(manifest_path)
    context_hash = sha256_bytes(context.encode("utf-8"))
    relationships = question.get("relationships") if isinstance(question.get("relationships"), list) else []

    lines = [
        f"# Source slice {question_id}",
        "",
        f"- source question no: {question.get('source_question_no')}",
        f"- source document: {source_path}",
        f"- source SHA-256: {source_sha}",
        f"- DOCX paragraph range: {question.get('start_block')}..{question.get('end_block')}",
        f"- source manifest: {manifest_path.resolve()}",
        f"- source manifest SHA-256: {manifest_hash}",
        f"- exact context SHA-256: {context_hash}",
        "",
        "## Exact DOCX paragraph context",
        "",
        context,
        "",
        "## Embedded media relationships",
        "",
    ]
    if relationships:
        lines.extend([
            "| relationship | DOCX target | paragraph | extracted artifact | SHA-256 |",
            "|---|---|---:|---|---|",
        ])
        for relation in relationships:
            lines.append(
                "| {relationship_id} | {target} | {block} | {extracted_path} | {sha256} |".format(**relation)
            )
    else:
        lines.append("No embedded media relationship was recorded for this source slice.")
    markdown = ("\n".join(lines) + "\n").encode("utf-8")
    atomic_bytes(markdown_path, markdown)
    markdown_sha = sha256_file(markdown_path)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "source_slice",
        "question_id": question_id,
        "source_sha256": source_sha,
        "locator": {
            "type": "docx_manifest_question_context",
            "source_question_no": str(question.get("source_question_no") or ""),
            "source_path": source_path,
            "manifest_path": str(manifest_path.resolve()),
            "manifest_sha256": manifest_hash,
            "start_block": question.get("start_block"),
            "end_block": question.get("end_block"),
            "context_sha256": context_hash,
            "relationship_count": len(relationships),
        },
        "artifact_ref": {"path": str(markdown_path.resolve()), "sha256": markdown_sha},
    }
    atomic_bytes(json_path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return {
        "question_id": question_id,
        "source_sha256": source_sha,
        "source_question_no": str(question.get("source_question_no") or ""),
        "locator": payload["locator"],
        "slice": {"path": str(markdown_path.resolve()), "sha256": markdown_sha},
        "manifest": {"path": str(manifest_path.resolve()), "sha256": manifest_hash},
        "binding": {"path": str(json_path.resolve()), "sha256": sha256_file(json_path)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-root", default=".")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--question-id", action="append", required=True)
    parser.add_argument("--output-root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = Path(args.kb_root).resolve()
    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    output_root = Path(args.output_root).resolve() if args.output_root else None
    results = [materialize_slice(root, manifest_path.resolve(), question_id, output_root) for question_id in args.question_id]
    payload = {"ok": True, "manifest": str(manifest_path.resolve()), "slices": results}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if not args.json else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
