#!/usr/bin/env python3
"""Audit the LLMWiki layer without modifying repository content."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable


LINK_RE = re.compile(r"\[\[([^\]|#\n]+)(?:#[^\]|\n]+)?(?:\|[^\]]+)?\]\]")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
RAW_BODY_RE = re.compile(
    r"\^\[\.\./raw/|##\s*raw 原文|\[查看\]\(\.\./raw|\.\./raw/[^\s)]+\.md"
)


def strip_frontmatter(text: str) -> str:
    if text.startswith("---") and text.count("---") >= 2:
        return text.split("---", 2)[2]
    return text


def frontmatter_fields(text: str) -> dict[str, str]:
    if not text.startswith("---") or text.count("---") < 2:
        return {}
    fields: dict[str, str] = {}
    for line in text.split("---", 2)[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields


def rel_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def iter_markdown(folder: Path) -> Iterable[Path]:
    if not folder.exists():
        return []
    return sorted(folder.glob("*.md"))


def load_active_pages(vault: Path, active_dirs: list[str]) -> tuple[dict[str, Path], list[Path]]:
    pages: dict[str, Path] = {}
    for name in active_dirs:
        for path in iter_markdown(vault / name):
            pages[path.stem] = path
    controls = [p for p in (vault / "index.md", vault / "log.md") if p.exists()]
    return pages, controls


def resolve_image(page: Path, target: str) -> Path:
    clean = target.split("#", 1)[0].split("?", 1)[0]
    return (page.parent / clean).resolve()


def video_kind(slug: str) -> tuple[str, str] | None:
    if not slug.startswith("视频-"):
        return None
    name = slug[3:]
    if name.endswith("-知识笔记"):
        return name[: -len("-知识笔记")], "knowledge"
    if name.endswith("-教学简案"):
        return name[: -len("-教学简案")], "lesson"
    return name, "parent"


def build_report(kb_root: Path, active_dirs: list[str], max_page_bytes: int) -> dict:
    vault = kb_root / "LLMWiki"
    pages, controls = load_active_pages(vault, active_dirs)
    sources = list(pages.values()) + controls

    inlinks: dict[str, set[str]] = defaultdict(set)
    outlinks: dict[str, set[str]] = defaultdict(set)
    dead: dict[str, list[str]] = defaultdict(list)
    image_issues: list[dict] = []
    raw_pointer: list[str] = []
    oversized: list[dict] = []
    malformed: list[str] = []

    for path in sources:
        source_slug = path.stem
        rel = rel_path(path, vault)
        text = path.read_text(encoding="utf-8", errors="ignore")
        body = strip_frontmatter(text)

        if path in pages.values() and not text.lstrip().startswith("---"):
            malformed.append(f"{rel}: missing frontmatter")
        byte_size = len(text.encode("utf-8"))
        if byte_size > max_page_bytes:
            oversized.append({"file": rel, "bytes": byte_size})
        if RAW_BODY_RE.search(body):
            raw_pointer.append(rel)

        for match in LINK_RE.finditer(text):
            target_slug = Path(match.group(1).strip().replace("\\", "/")).stem
            if not target_slug:
                continue
            outlinks[source_slug].add(target_slug)
            if target_slug in pages:
                inlinks[target_slug].add(source_slug)
            else:
                dead[rel].append(target_slug)

        for match in IMAGE_RE.finditer(text):
            target = match.group(1).strip()
            if target.startswith(("http://", "https://", "data:", "#")):
                continue
            if not resolve_image(path, target).exists():
                image_issues.append({"file": rel, "target": target})

    orphans = [
        {"slug": slug, "file": rel_path(path, vault)}
        for slug, path in sorted(pages.items())
        if not inlinks.get(slug)
    ]

    video_groups: dict[str, set[str]] = defaultdict(set)
    for slug in pages:
        parsed = video_kind(slug)
        if parsed:
            base, kind = parsed
            video_groups[base].add(kind)
    video_missing = [
        {"base": base, "missing": [k for k in ("parent", "knowledge", "lesson") if k not in kinds]}
        for base, kinds in sorted(video_groups.items())
        if {"parent", "knowledge", "lesson"} - kinds
    ]

    wiki_video_by_bvid: dict[str, Path] = {}
    for slug, path in pages.items():
        parsed = video_kind(slug)
        if not parsed or parsed[1] != "parent":
            continue
        bvid = frontmatter_fields(path.read_text(encoding="utf-8", errors="ignore")).get("bvid")
        if bvid:
            wiki_video_by_bvid[bvid] = path

    raw_video_parents: list[dict[str, str]] = []
    video_raw_triplet_incomplete: list[dict] = []
    transcripts = kb_root / "raw" / "transcripts"
    if transcripts.exists():
        for path in sorted(transcripts.glob("*/*/*.md")):
            if path.stem != path.parent.name:
                continue
            fields = frontmatter_fields(path.read_text(encoding="utf-8", errors="ignore"))
            bvid = fields.get("bvid")
            if bvid:
                item = {"bvid": bvid, "title": fields.get("title", path.stem), "file": rel_path(path, kb_root)}
                raw_video_parents.append(item)
                expected = {
                    "knowledge": path.with_name(f"{path.stem}_知识笔记.md"),
                    "lesson": path.with_name(f"{path.stem}_教学简案.md"),
                }
                missing = [kind for kind, expected_path in expected.items() if not expected_path.exists()]
                if missing:
                    video_raw_triplet_incomplete.append({**item, "missing": missing})
    raw_video_bvids = {item["bvid"] for item in raw_video_parents}
    video_raw_without_wiki = [item for item in raw_video_parents if item["bvid"] not in wiki_video_by_bvid]
    video_wiki_without_raw = [
        {"bvid": bvid, "file": rel_path(path, kb_root)}
        for bvid, path in sorted(wiki_video_by_bvid.items())
        if bvid not in raw_video_bvids
    ]

    index_links: set[str] = set()
    index = vault / "index.md"
    if index.exists():
        for match in LINK_RE.finditer(index.read_text(encoding="utf-8", errors="ignore")):
            index_links.add(Path(match.group(1).strip()).stem)
    missing_from_index = sorted(slug for slug in pages if slug not in index_links)

    return {
        "kb_root": str(kb_root),
        "vault": str(vault),
        "active_dirs": active_dirs,
        "active_page_count": len(pages),
        "control_docs": [rel_path(p, vault) for p in controls],
        "dead_link_count": sum(len(v) for v in dead.values()),
        "dead_links": {k: sorted(set(v)) for k, v in sorted(dead.items())},
        "orphan_count": len(orphans),
        "orphans": orphans,
        "image_issue_count": len(image_issues),
        "image_issues": image_issues,
        "visible_raw_pointer_count": len(raw_pointer),
        "visible_raw_pointer_pages": sorted(raw_pointer),
        "oversized_count": len(oversized),
        "oversized": sorted(oversized, key=lambda item: item["bytes"], reverse=True),
        "malformed_count": len(malformed),
        "malformed": malformed,
        "video_group_count": len(video_groups),
        "video_missing_count": len(video_missing),
        "video_missing": video_missing,
        "video_raw_without_wiki_count": len(video_raw_without_wiki),
        "video_raw_without_wiki": video_raw_without_wiki,
        "video_raw_triplet_incomplete_count": len(video_raw_triplet_incomplete),
        "video_raw_triplet_incomplete": video_raw_triplet_incomplete,
        "video_wiki_without_raw_count": len(video_wiki_without_raw),
        "video_wiki_without_raw": video_wiki_without_raw,
        "missing_from_index_count": len(missing_from_index),
        "missing_from_index": missing_from_index,
        "top_inlinked": Counter({k: len(v) for k, v in inlinks.items()}).most_common(20),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit LLMWiki graph health without editing files.")
    parser.add_argument("--kb-root", default=".", help="Path to LLMWiki_BGE-M3 root.")
    parser.add_argument(
        "--active-dirs",
        default="concepts,comparisons,entities,queries",
        help="Comma-separated LLMWiki subdirectories to treat as active graph nodes.",
    )
    parser.add_argument("--max-page-bytes", type=int, default=20000)
    parser.add_argument(
        "--out",
        default="skills/_ops/runtime/reports/wiki-maintenance-audit.json",
        help="Report path, relative to kb root unless absolute.",
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    parser.add_argument("--fail-on-issues", action="store_true", help="Exit 1 if actionable issues exist.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    kb_root = Path(args.kb_root).resolve()
    active_dirs = [item.strip() for item in args.active_dirs.split(",") if item.strip()]
    report = build_report(kb_root, active_dirs, args.max_page_bytes)

    out = Path(args.out)
    if not out.is_absolute():
        out = kb_root / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        summary = {
            "active_page_count": report["active_page_count"],
            "dead_link_count": report["dead_link_count"],
            "orphan_count": report["orphan_count"],
            "image_issue_count": report["image_issue_count"],
            "visible_raw_pointer_count": report["visible_raw_pointer_count"],
            "malformed_count": report["malformed_count"],
            "video_missing_count": report["video_missing_count"],
            "video_raw_triplet_incomplete_count": report["video_raw_triplet_incomplete_count"],
            "video_raw_without_wiki_count": report["video_raw_without_wiki_count"],
            "video_wiki_without_raw_count": report["video_wiki_without_raw_count"],
            "report": str(out),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.fail_on_issues and (
        report["dead_link_count"]
        or report["image_issue_count"]
        or report["visible_raw_pointer_count"]
        or report["malformed_count"]
        or report["video_missing_count"]
        or report["video_raw_triplet_incomplete_count"]
        or report["video_raw_without_wiki_count"]
        or report["video_wiki_without_raw_count"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
