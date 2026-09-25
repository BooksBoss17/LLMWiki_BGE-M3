#!/usr/bin/env python
"""Deterministic storage-title normalization for video import queues."""

from __future__ import annotations

import re
from typing import Any


INVALID_STORAGE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
REPEATED_DASHES = re.compile(r'-{2,}')
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def normalize_storage_title(title: Any, fallback: str = "video", max_length: int = 120) -> str:
    text = str(title or "").strip()
    text = INVALID_STORAGE_CHARS.sub("-", text)
    text = REPEATED_DASHES.sub("-", text).strip(" .-")
    if not text:
        text = str(fallback or "video").strip() or "video"
    if text.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        text = "_" + text
    if max_length > 0 and len(text) > max_length:
        text = text[:max_length].rstrip(" .-") or str(fallback or "video")
    return text


def normalize_video_item(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item)
    bvid = str(row.get("bvid") or "video")
    original = str(row.get("original_title") or row.get("title") or row.get("import_title") or bvid)
    requested_storage = row.get("storage_title") or row.get("import_title") or original
    storage = normalize_storage_title(requested_storage, fallback=bvid)
    row["original_title"] = original
    row["storage_title"] = storage
    row["import_title"] = storage
    row["wiki_title"] = normalize_storage_title(row.get("wiki_title") or storage, fallback=bvid)
    return row


def normalize_queue_data(data: Any) -> list[dict[str, Any]]:
    rows = data if isinstance(data, list) else [data]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError("video queue must be a JSON object or a non-empty array of objects")
    return [normalize_video_item(row) for row in rows]
