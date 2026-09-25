#!/usr/bin/env python
"""Summarize long video-import task state into a short JSON report."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    data = path.read_bytes()
    errors = []
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            return json.loads(data.decode(encoding))
        except Exception as exc:
            errors.append(f"{encoding}: {exc}")
    raise ValueError(f"cannot parse JSON {path}: {'; '.join(errors[-2:])}")


def read_json_or_none(path: Path | None) -> Any:
    if path is None or not path.exists():
        return None
    try:
        return read_json(path)
    except Exception as exc:
        return {"_parse_error": repr(exc)}


def load_videos(path: Path | None) -> list[dict[str, Any]]:
    data = read_json_or_none(path)
    if data is None:
        return []
    if isinstance(data, dict):
        for key in ("videos", "queue", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        return []
    return [dict(item) for item in data if isinstance(item, dict)]


def status_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict) and "_parse_error" in data:
        return [{"ok": False, "error": data["_parse_error"]}]
    if isinstance(data, dict):
        if isinstance(data.get("status"), list):
            data = data["status"]
        elif isinstance(data.get("videos"), dict):
            data = data["videos"]
        else:
            rows = []
            for key, value in data.items():
                if isinstance(value, dict):
                    row = dict(value)
                    row.setdefault("bvid", key)
                    rows.append(row)
            return rows
    if isinstance(data, list):
        return [dict(row) for row in data if isinstance(row, dict)]
    return []


def summarize_status(path: Path | None, label: str, max_failures: int) -> dict[str, Any]:
    data = read_json_or_none(path)
    if data is None:
        return {"label": label, "path": str(path) if path else None, "exists": False}
    rows = status_rows(data)
    failures = []
    for row in rows:
        if not row.get("ok"):
            failures.append(
                {
                    "bvid": row.get("bvid"),
                    "title": row.get("storage_title") or row.get("import_title") or row.get("title") or row.get("original_title"),
                    "error": row.get("error"),
                }
            )
    return {
        "label": label,
        "path": str(path),
        "exists": True,
        "count": len(rows),
        "ok": sum(1 for row in rows if row.get("ok")),
        "failed": len(failures),
        "failures": failures[:max_failures],
        "truncated_failures": max(0, len(failures) - max_failures),
    }


def validator_summary(path: Path) -> dict[str, Any]:
    data = read_json_or_none(path)
    if data is None:
        return {"path": str(path), "exists": False}
    if isinstance(data, dict) and "_parse_error" in data:
        return {"path": str(path), "exists": True, "parse_error": data["_parse_error"], "issue_count": None}
    issues = data.get("issues", []) if isinstance(data, dict) else []
    issue_count = data.get("issue_count", len(issues)) if isinstance(data, dict) else None
    return {
        "path": str(path),
        "exists": True,
        "issue_count": issue_count,
        "sample_issues": issues[:10] if isinstance(issues, list) else [],
    }


def unique_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def discover_validators(task_dir: Path, explicit: list[str]) -> list[Path]:
    paths = [Path(p) for p in explicit]
    if task_dir.exists():
        for pattern in ("*validation*.json", "validation_loop_*.json", "raw_*_validation*.json"):
            paths.extend(task_dir.glob(pattern))
    return [p for p in unique_paths(paths) if p.exists()]


def read_tail(path: Path, max_chars: int) -> str:
    if max_chars <= 0 or not path.exists():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - max_chars * 4))
        data = handle.read()
    return data.decode("utf-8", errors="ignore")[-max_chars:]


def stringify_for_match(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except TypeError:
        return str(obj)


def import_title(video: dict[str, Any]) -> str:
    return str(video.get("import_title") or video.get("short_title") or video.get("wiki_title") or video.get("title") or video.get("bvid") or "")


def summarize_rag(metadata_path: Path | None, videos: list[dict[str, Any]]) -> dict[str, Any]:
    data = read_json_or_none(metadata_path)
    if data is None:
        return {"metadata_path": str(metadata_path) if metadata_path else None, "exists": False}
    if isinstance(data, dict) and "_parse_error" in data:
        return {"metadata_path": str(metadata_path), "exists": True, "parse_error": data["_parse_error"]}

    if isinstance(data, dict):
        chunks = data.get("chunks", [])
        total_chunks = data.get("total_chunks", len(chunks) if isinstance(chunks, list) else None)
        total_vectors = data.get("total_vectors")
    elif isinstance(data, list):
        chunks = data
        total_chunks = len(chunks)
        total_vectors = None
    else:
        chunks = []
        total_chunks = None
        total_vectors = None

    matched = []
    missing = []
    for video in videos:
        keys = [str(video.get("bvid") or ""), import_title(video), str(video.get("title") or "")]
        keys = [key for key in keys if key]
        found = False
        for chunk in chunks if isinstance(chunks, list) else []:
            if isinstance(chunk, dict) and chunk.get("source_type") != "transcript":
                continue
            blob = stringify_for_match(chunk)
            if any(key in blob for key in keys):
                found = True
                break
        (matched if found else missing).append(video.get("bvid") or import_title(video))

    return {
        "metadata_path": str(metadata_path),
        "exists": True,
        "total_chunks": total_chunks,
        "total_vectors": total_vectors,
        "matched_videos": matched,
        "missing_videos": missing,
    }


def default_path(task_dir: Path, name: str, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    candidate = task_dir / name
    return candidate if candidate.exists() else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", required=True)
    ap.add_argument("--videos", help="queue JSON; defaults to active then full queue in task dir")
    ap.add_argument("--download-status")
    ap.add_argument("--transcribe-status")
    ap.add_argument("--validator", action="append", default=[])
    ap.add_argument("--rag-metadata")
    ap.add_argument("--rag-log")
    ap.add_argument("--out", help="write summary JSON to this path")
    ap.add_argument("--max-failures", type=int, default=20)
    ap.add_argument("--log-tail-chars", type=int, default=3000)
    args = ap.parse_args()

    task_dir = Path(args.task_dir)
    videos_path = Path(args.videos) if args.videos else None
    if videos_path is None:
        for name in ("videos_active_batch.json", "videos_full_queue.json", "videos.json"):
            candidate = task_dir / name
            if candidate.exists():
                videos_path = candidate
                break
    videos = load_videos(videos_path)
    validators = [validator_summary(path) for path in discover_validators(task_dir, args.validator)]
    rag_metadata = Path(args.rag_metadata) if args.rag_metadata else None
    rag_log = Path(args.rag_log) if args.rag_log else None

    summary: dict[str, Any] = {
        "generated_at": now_iso(),
        "task_dir": str(task_dir),
        "videos_path": str(videos_path) if videos_path else None,
        "queue_count": len(videos),
        "download": summarize_status(default_path(task_dir, "download_status.json", args.download_status), "download", args.max_failures),
        "transcribe": summarize_status(default_path(task_dir, "transcribe_status.json", args.transcribe_status), "transcribe", args.max_failures),
        "validators": validators,
        "rag": summarize_rag(rag_metadata, videos) if rag_metadata else {"exists": False, "metadata_path": None},
    }
    if rag_log:
        summary["rag"]["log_tail"] = read_tail(rag_log, args.log_tail_chars)

    flags = []
    for key in ("download", "transcribe"):
        section = summary[key]
        if section.get("exists") and section.get("failed"):
            flags.append(f"{key}_has_failures")
    for validator in validators:
        if validator.get("issue_count") not in (0, None):
            flags.append("validator_has_issues")
            break
    if summary["rag"].get("missing_videos"):
        flags.append("rag_missing_video_chunks")
    summary["next_flags"] = sorted(set(flags))

    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
