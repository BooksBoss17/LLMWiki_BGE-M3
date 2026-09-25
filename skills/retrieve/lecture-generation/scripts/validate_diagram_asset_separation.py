#!/usr/bin/env python
"""Validate question/solution physics diagram separation before Role B publication."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


SKILLS_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS_ROOT / "_shared" / "scripts"))
from project_paths import resolve_path  # noqa: E402


IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(root.resolve())
    return resolved


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def markdown_image_hashes(path: Path, output_root: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    hashes: set[str] = set()
    for raw in IMAGE_RE.findall(text):
        target = raw.strip().split(maxsplit=1)[0].strip("<>")
        if "://" in target:
            raise ValueError(f"external image is not allowed in final teaching output: {target}")
        image = inside((path.parent / target).resolve(), output_root)
        if not image.is_file():
            raise FileNotFoundError(image)
        hashes.add(sha256_file(image))
    return hashes


def validate_manifest(manifest_path: Path, *, output_root: Path | None = None) -> dict[str, Any]:
    root = (output_root or resolve_path("workspace.output", start=manifest_path)).resolve()
    manifest_path = inside(manifest_path, root if output_root else resolve_path("project.root", start=manifest_path))
    payload = load_json(manifest_path)
    if payload.get("schema_version") != 1:
        raise ValueError("diagram asset manifest must use schema_version=1")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("diagram asset manifest requires entries")
    question_hashes: set[str] = set()
    solution_hashes: set[str] = set()
    reusable_question_hashes: set[str] = set()
    records: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"entries[{index}] must be an object")
        usage = str(entry.get("usage", ""))
        if usage not in {"question_image", "solution_image"}:
            raise ValueError(f"entries[{index}].usage must be question_image|solution_image")
        asset = inside((root / str(entry.get("asset_path", ""))).resolve(), root)
        completion = inside((root / str(entry.get("completion_path", ""))).resolve(), root)
        if not asset.is_file() or not completion.is_file():
            raise FileNotFoundError(f"missing asset or completion for entries[{index}]")
        digest = sha256_file(asset)
        if entry.get("asset_sha256") != digest:
            raise RuntimeError(f"asset hash mismatch for entries[{index}]")
        completion_payload = load_json(completion)
        if not completion_payload.get("ok"):
            raise RuntimeError(f"completion is not successful for entries[{index}]")
        item_id = str(entry.get("item_id", "main"))
        spec_path = completion.parent / item_id / "provenance" / "diagram_spec.json"
        geometry_path = completion.parent / item_id / "provenance" / "geometry_report.json"
        if not spec_path.is_file() or not geometry_path.is_file():
            raise FileNotFoundError(f"missing spec/geometry provenance for entries[{index}]")
        spec, geometry = load_json(spec_path), load_json(geometry_path)
        if spec.get("schema_version") != 3 or not geometry.get("ok") or geometry.get("failed_assertions"):
            raise RuntimeError(f"entries[{index}] lacks a passing formal v3 geometry contract")
        purpose, style = spec.get("purpose"), spec.get("style_profile")
        analysis_items = [
            row
            for field in ("vectors", "dimensions", "annotations")
            for row in spec.get(field, [])
            if isinstance(row, dict) and row.get("semantic_role") == "analysis"
        ]
        if usage == "question_image":
            if purpose != "question" or style != "exam-monochrome" or analysis_items:
                raise RuntimeError(f"question_image entries[{index}] is not a clean monochrome question diagram")
            question_hashes.add(digest)
        else:
            reuse_question = bool(entry.get("reuse_question_image"))
            if reuse_question:
                if purpose != "question" or analysis_items:
                    raise RuntimeError(f"reused question image is not clean for entries[{index}]")
                reusable_question_hashes.add(digest)
            elif purpose != "solution" or style != "solution-color":
                raise RuntimeError(f"solution_image entries[{index}] must be a solution-color diagram")
            solution_hashes.add(digest)
        records.append({"usage": usage, "sha256": digest, "purpose": purpose, "style_profile": style})
    practice_path = payload.get("practice_markdown")
    answer_path = payload.get("answer_markdown")
    if practice_path:
        practice = inside((root / str(practice_path)).resolve(), root)
        unexpected = markdown_image_hashes(practice, root) - question_hashes
        if unexpected:
            raise RuntimeError("practice Markdown references a non-question diagram")
    if answer_path:
        answer = inside((root / str(answer_path)).resolve(), root)
        allowed = solution_hashes | reusable_question_hashes
        unexpected = markdown_image_hashes(answer, root) - allowed
        if unexpected:
            raise RuntimeError("answer Markdown references an unapproved diagram")
    return {
        "ok": True,
        "entry_count": len(records),
        "question_hashes": sorted(question_hashes),
        "solution_hashes": sorted(solution_hashes),
        "records": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-root", help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = validate_manifest(Path(args.manifest), output_root=Path(args.output_root).resolve() if args.output_root else None)
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else "ok")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
