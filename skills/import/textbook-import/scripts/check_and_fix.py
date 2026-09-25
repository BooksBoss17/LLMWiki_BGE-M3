#!/usr/bin/env python3
"""Validate textbook Markdown and apply only review-safe fixes.

Semantic OCR repair is deliberately out of scope for automatic mutation. The
script reports unresolved content as repair tasks so an agent can inspect the
source page with BeMarkdown/transcriber/VLM before writing corrected text.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


IMAGE_RE = re.compile(r"!\[[^\]]*]\(([^)]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
EMPTY_HEADING_RE = re.compile(r"^#{1,6}\s*$")
CAPTION_HEADING_RE = re.compile(r"^#{1,6}\s*(图|表)\s*\d+")
CHAPTER_ONLY_RE = re.compile(r"^##\s+第(\d+)章\s*$")
REPEATED_TEXT_RE = re.compile(r"(.{5,20})\1{3,}")
PLACEHOLDER_RE = re.compile(
    r"OCR乱码|picture intentionally omitted|machine_abstain|TODO|待补|占位|无法识别|OCR失败",
    re.IGNORECASE,
)

# Historical tokens remain detection evidence only. They are never replaced
# unless the caller supplies an explicitly reviewed replacement map.
DEFAULT_SUSPICIOUS_TOKENS = (
    "釜略",
    "元警",
    "节练る",
    "瓮略",
    "総略",
    "猪论",
    "猫弦",
    "直缆",
    "道金额",
    "半频速频度槽",
    "相互作開",
    "示警全安",
    "万有引力電電",
    "科学选步",
)


def load_string_map(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise ValueError(f"expected a JSON object of string:string entries: {path}")
    return data


def markdown_files(base: Path) -> list[Path]:
    return sorted(path for path in base.glob("*.md") if path.is_file())


def issue(path: Path, line: int | None, kind: str, text: str = "", **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"path": path.as_posix(), "kind": kind}
    if line is not None:
        row["line"] = line
    if text:
        row["text"] = text[:240]
    row.update(extra)
    return row


def unescaped_dollar_count(text: str) -> int:
    return len(re.findall(r"(?<!\\)\$", text))


def scan_file(
    path: Path,
    *,
    book_title: str | None = None,
    chapter_names: dict[str, str] | None = None,
    suspicious_tokens: tuple[str, ...] = DEFAULT_SUSPICIOUS_TOKENS,
) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    issues: list[dict[str, Any]] = []
    headings: list[tuple[int, int, str]] = []

    if unescaped_dollar_count(text) % 2:
        issues.append(issue(path, None, "unbalanced_latex_delimiter"))

    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        heading = HEADING_RE.match(line)
        if heading:
            headings.append((number, len(heading.group(1)), heading.group(2).strip()))
            if "**" in heading.group(2):
                issues.append(issue(path, number, "bold_in_heading", stripped))
            if len(heading.group(1)) > 4:
                issues.append(issue(path, number, "heading_too_deep", stripped))
            if CAPTION_HEADING_RE.match(stripped):
                issues.append(issue(path, number, "caption_as_heading", stripped))
        elif EMPTY_HEADING_RE.match(stripped):
            issues.append(issue(path, number, "empty_heading", stripped))

        if PLACEHOLDER_RE.search(stripped):
            issues.append(issue(path, number, "placeholder", stripped))
        for token in suspicious_tokens:
            if token in stripped:
                issues.append(issue(path, number, "suspicious_ocr_token", stripped, token=token))

        if "$" not in stripped and not stripped.startswith(("|", "<!--")):
            repeats = [value for value in REPEATED_TEXT_RE.findall(stripped) if not re.fullmatch(r"[_\-\s]+", value)]
            if repeats:
                issues.append(issue(path, number, "repeated_ocr_noise", stripped, repeated=repeats[0][:40]))

        if "data:image/" in stripped:
            issues.append(issue(path, number, "embedded_data_uri", stripped))
        for raw_ref in IMAGE_RE.findall(line):
            if raw_ref.startswith(("http://", "https://")):
                continue
            if raw_ref.lower().endswith(".wmf"):
                issues.append(issue(path, number, "wmf_reference", raw_ref))
            target = (path.parent / raw_ref).resolve()
            if not target.exists():
                issues.append(issue(path, number, "broken_image", raw_ref, target=target.as_posix()))

        chapter = CHAPTER_ONLY_RE.match(stripped)
        if chapter:
            number_text = chapter.group(1)
            issues.append(
                issue(
                    path,
                    number,
                    "chapter_name_missing",
                    stripped,
                    reviewed_replacement=(chapter_names or {}).get(number_text),
                )
            )

    h1 = [(line, title) for line, level, title in headings if level == 1]
    if len(h1) != 1:
        issues.append(issue(path, None, "h1_count", count=len(h1), lines=[line for line, _ in h1]))
    elif book_title and h1[0][1] != book_title:
        issues.append(issue(path, h1[0][0], "unexpected_h1", h1[0][1], expected=book_title))

    for previous, current in zip(headings, headings[1:]):
        if current[1] > previous[1] + 1:
            issues.append(
                issue(
                    path,
                    current[0],
                    "heading_level_jump",
                    current[2],
                    previous_level=previous[1],
                    current_level=current[1],
                )
            )

    h2_counts = Counter(title for _, level, title in headings if level == 2)
    for title, count in h2_counts.items():
        if count > 1:
            issues.append(issue(path, None, "duplicate_h2", title, count=count))
    return issues


def check_all(
    base: Path,
    *,
    book_title: str | None = None,
    chapter_names: dict[str, str] | None = None,
    suspicious_tokens: tuple[str, ...] = DEFAULT_SUSPICIOUS_TOKENS,
) -> list[dict[str, Any]]:
    return [
        item
        for path in markdown_files(base)
        for item in scan_file(
            path,
            book_title=book_title,
            chapter_names=chapter_names,
            suspicious_tokens=suspicious_tokens,
        )
    ]


def apply_safe_fixes(path: Path) -> int:
    """Apply formatting-only fixes; never delete or replace semantic content."""
    original = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = original.splitlines()
    fixed: list[str] = []
    changes = 0
    for line in lines:
        stripped = line.rstrip()
        if stripped != line:
            changes += 1
        if EMPTY_HEADING_RE.match(stripped.strip()):
            changes += 1
            continue
        heading = HEADING_RE.match(stripped)
        if heading:
            level = min(len(heading.group(1)), 4)
            title = heading.group(2).replace("**", "").strip()
            normalized = f"{'#' * level} {title}"
            if normalized != stripped:
                changes += 1
            if CAPTION_HEADING_RE.match(normalized):
                normalized = f"**{title}**"
                changes += 1
            fixed.append(normalized)
        else:
            fixed.append(stripped)
    rendered = re.sub(r"\n{3,}", "\n\n", "\n".join(fixed)).rstrip() + "\n"
    if rendered != original:
        path.write_text(rendered, encoding="utf-8")
    return changes


def apply_reviewed_replacements(
    path: Path,
    replacements: dict[str, str],
    chapter_names: dict[str, str],
) -> int:
    """Apply only caller-supplied, source-verified replacements."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    changes = 0
    for old, new in replacements.items():
        count = text.count(old)
        if count:
            text = text.replace(old, new)
            changes += count

    def replace_chapter(match: re.Match[str]) -> str:
        nonlocal changes
        number = match.group(1)
        title = chapter_names.get(number)
        if not title:
            return match.group(0)
        changes += 1
        return f"## 第{number}章 {title}"

    text = re.sub(r"^##\s+第(\d+)章\s*$", replace_chapter, text, flags=re.MULTILINE)
    if changes:
        path.write_text(text, encoding="utf-8")
    return changes


def repair_tasks(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions = {
        "suspicious_ocr_token": "Inspect the source page and structured OCR candidates before correcting text.",
        "repeated_ocr_noise": "Render the source page and resolve with transcriber/agent VLM; do not delete the paragraph.",
        "placeholder": "Recover the missing source content; placeholders cannot pass import quality gates.",
        "chapter_name_missing": "Supply a source-verified chapter-names JSON map.",
        "unexpected_h1": "Confirm the intended book title before changing heading level or text.",
        "duplicate_h2": "Compare the source structure; never delete duplicate headings automatically.",
        "wmf_reference": "Run BeMarkdown WMF formula candidate workflow and resolve review tasks.",
    }
    rows: list[dict[str, Any]] = []
    for item in issues:
        row = dict(item)
        row["status"] = "unresolved"
        row["recommended_action"] = actions.get(item["kind"], "Review against the source before editing.")
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate textbook Markdown without unsafe OCR mutation.")
    parser.add_argument("textbook_md_dir", type=Path)
    parser.add_argument("--book-title")
    parser.add_argument("--fix-safe", action="store_true", help="Apply formatting-only fixes")
    parser.add_argument("--reviewed-replacements", type=Path, help="Source-verified JSON old:new replacements")
    parser.add_argument("--chapter-names", type=Path, help="Source-verified JSON chapter-number:title map")
    parser.add_argument("--apply-reviewed", action="store_true", help="Apply the supplied reviewed maps")
    parser.add_argument("--repair-tasks", type=Path, help="Write unresolved issues as JSON")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    base = args.textbook_md_dir.resolve()
    if not base.is_dir():
        raise SystemExit(f"textbook Markdown directory not found: {base}")
    replacements = load_string_map(args.reviewed_replacements)
    chapter_names = load_string_map(args.chapter_names)

    changes = 0
    if args.fix_safe:
        changes += sum(apply_safe_fixes(path) for path in markdown_files(base))
    if args.apply_reviewed:
        if not replacements and not chapter_names:
            raise SystemExit("--apply-reviewed requires --reviewed-replacements and/or --chapter-names")
        changes += sum(apply_reviewed_replacements(path, replacements, chapter_names) for path in markdown_files(base))

    issues = check_all(base, book_title=args.book_title, chapter_names=chapter_names)
    tasks = repair_tasks(issues)
    if args.repair_tasks:
        args.repair_tasks.parent.mkdir(parents=True, exist_ok=True)
        args.repair_tasks.write_text(json.dumps(tasks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = {
        "ok": not issues,
        "file_count": len(markdown_files(base)),
        "change_count": changes,
        "issue_count": len(issues),
        "issue_kinds": dict(Counter(item["kind"] for item in issues)),
        "issues": issues,
        "repair_tasks": str(args.repair_tasks.resolve()) if args.repair_tasks else None,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"ok={summary['ok']} files={summary['file_count']} changes={changes} issues={len(issues)}")
        for kind, count in summary["issue_kinds"].items():
            print(f"  {kind}: {count}")
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
