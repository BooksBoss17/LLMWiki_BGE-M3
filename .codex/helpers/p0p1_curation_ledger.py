#!/usr/bin/env python3
"""Build deterministic P0/P1 curation ledgers from external review reports.

This helper only reads review reports and question/source metadata.  It writes
campaign ledgers under the requested ignored task directory; it never edits
raw/ questions, media, Wiki pages, or RAG assets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


ID_RE = re.compile(r"^[A-Z]{1,2}\d{7}$")
QUESTION_HEADING_RE = re.compile(r"^#{3,4} ([A-Z]{1,2}\d{7})\s*$")
SEVERITY_RE = re.compile(r"^\s*severity:\s*[\"']?(P[0-3])", re.I)
FIELD_RE = re.compile(r"^\s*([a-z_]+):\s*[\"']?(.*?)[\"']?\s*$")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
CAMPAIGN_SLUGS = {
    "电磁学": "electromagnetics",
    "光学": "optics",
    "力学": "mechanics",
    "热学": "thermal",
    "综合": "comprehensive",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_source_key(raw: str) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    if "source-library/" in value:
        return value[value.find("source-library/"):]
    while value.startswith("../"):
        value = value[3:]
    return value.removeprefix("./")


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    result: dict[str, str] = {}
    for line in parts[1].splitlines():
        match = FIELD_RE.match(line)
        if match:
            result[match.group(1)] = match.group(2).strip().strip("\"'")
    return result


def parse_report_questions(report_path: Path) -> list[dict[str, Any]]:
    lines = report_path.read_text(encoding="utf-8").splitlines()
    headings = [
        (index, match.group(1))
        for index, line in enumerate(lines)
        if (match := QUESTION_HEADING_RE.match(line))
    ]
    questions: list[dict[str, Any]] = []
    for position, (start, question_id) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        block = lines[start:end]
        issues: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for line_offset, line in enumerate(block, start=start + 1):
            stripped = line.strip()
            if stripped.startswith("- issue_id:"):
                current = {"report_line": line_offset}
                issues.append(current)
                value = stripped.split(":", 1)[1].strip().strip("\"'")
                current["issue_id"] = value
                continue
            if current is None:
                continue
            match = FIELD_RE.match(line)
            if not match:
                continue
            key, value = match.groups()
            value = value.strip().strip("\"'")
            if key in {"severity", "category", "location", "problem", "suggested_action", "needs_source_confirmation"}:
                current[key] = value
        severities = {str(issue.get("severity")) for issue in issues}
        if not severities.intersection({"P0", "P1"}):
            continue
        questions.append(
            {
                "question_id": question_id.upper(),
                "report_path": str(report_path),
                "report_line": start + 1,
                "report_status": next(
                    (
                        line.split(":", 1)[1].strip().strip("\"'")
                        for line in block
                        if line.strip().startswith("status:")
                    ),
                    "",
                ),
                "issues": issues,
                "severities": sorted(severities),
            }
        )
    return questions


def question_file_map(raw_root: Path, volume: str) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    volume_root = raw_root / "exercises" / volume
    for path in volume_root.rglob("*.md"):
        if ID_RE.fullmatch(path.stem):
            if path.stem in mapping:
                raise ValueError(f"duplicate question file for {path.stem}: {mapping[path.stem]} / {path}")
            mapping[path.stem] = path
    return mapping


def natural_key(item: dict[str, Any]) -> tuple[Any, ...]:
    source_no = str(item.get("source_question_no") or "")
    numbers = tuple(int(value) for value in re.findall(r"\d+", source_no))
    severity_rank = 0 if "P0" in item["reported_severities"] else 1
    source_key = str(item.get("source_key") or "~")
    return (severity_rank, source_key.casefold(), numbers or (10**9,), source_no, item["question_id"])


def build_item(root: Path, question_path: Path, report_item: dict[str, Any]) -> dict[str, Any]:
    text = question_path.read_text(encoding="utf-8")
    frontmatter = parse_frontmatter(text)
    raw_source = frontmatter.get("source_path", "")
    source_key = normalize_source_key(raw_source)
    source_path = root / source_key if source_key else None
    source_ready = bool(source_path and source_path.is_file() and source_path.is_relative_to(root / "source-library"))
    body_images = sorted(set(IMAGE_RE.findall(text.split("---", 2)[-1] if "---" in text else text)))
    asset_hashes: list[dict[str, str]] = []
    for image_ref in body_images:
        image_path = (question_path.parent / image_ref).resolve()
        asset_hashes.append(
            {
                "path": image_ref,
                "sha256": sha256(image_path) if image_path.is_file() else "",
            }
        )
    issue_summaries = []
    for issue in report_item["issues"]:
        issue_summaries.append(
            {
                "issue_id": issue.get("issue_id", ""),
                "severity": issue.get("severity", ""),
                "category": issue.get("category", ""),
                "location": issue.get("location", ""),
                "problem": issue.get("problem", ""),
                "suggested_action": issue.get("suggested_action", ""),
                "report_line": issue.get("report_line"),
            }
        )
    return {
        "question_id": report_item["question_id"],
        "target_path": question_path.relative_to(root).as_posix(),
        "current_sha256": sha256(question_path),
        "source_title": frontmatter.get("source_title", ""),
        "source_path": source_key if source_ready else "",
        "reported_source_path": raw_source,
        "source_key": source_key,
        "source_sha256": sha256(source_path) if source_ready and source_path else "",
        "source_state": "source_ready" if source_ready else "source_blocked",
        "source_question_no": frontmatter.get("source_question_no", ""),
        "report_status": report_item["report_status"],
        "reported_severities": report_item["severities"],
        "report_references": [
            f"{report_item['report_path']}:{report_item['report_line']}",
        ],
        "reasons": issue_summaries,
        "asset_refs": asset_hashes,
        "queue_ordinal": 0,
    }


def build_ledgers(root: Path, reports_dir: Path, out_dir: Path) -> dict[str, Any]:
    raw_root = root / "raw"
    reports = sorted(
        path
        for path in reports_dir.glob("*.md")
        if path.name != "exercise_review_standard_external_ai.md"
    )
    if not reports:
        raise FileNotFoundError(f"no review reports found under {reports_dir}")

    all_ids: set[str] = set()
    summaries: list[dict[str, Any]] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for report in reports:
        volume = report.stem
        file_map = question_file_map(raw_root, volume)
        report_items = parse_report_questions(report)
        items: list[dict[str, Any]] = []
        for report_item in report_items:
            question_id = report_item["question_id"]
            if question_id in all_ids:
                raise ValueError(f"question appears in multiple volume reports: {question_id}")
            all_ids.add(question_id)
            question_path = file_map.get(question_id)
            if question_path is None:
                raise FileNotFoundError(f"report question {question_id} not found in raw/exercises/{volume}")
            items.append(build_item(root, question_path, report_item))
        items.sort(key=natural_key)
        for ordinal, item in enumerate(items):
            item["queue_ordinal"] = ordinal
        ledger = {
            "schema_version": 1,
            "campaign_id": f"p0p1-{CAMPAIGN_SLUGS.get(volume, volume)}",
            "volume": volume,
            "source_reports": [str(report)],
            "selection": "P0/P1 questions; co-located issues retained in reasons",
            "items": items,
        }
        ledger_path = out_dir / f"{volume}.json"
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summaries.append(
            {
                "volume": volume,
                "ledger": str(ledger_path),
                "p0p1_count": len(items),
                "source_ready": sum(item["source_state"] == "source_ready" for item in items),
                "source_blocked": sum(item["source_state"] == "source_blocked" for item in items),
                "queue_sha256": sha256(ledger_path),
            }
        )
    manifest = {
        "schema_version": 1,
        "task_id": "p0p1-curation-20260722",
        "root": str(root),
        "reports_dir": str(reports_dir),
        "volumes": summaries,
        "p0p1_total": len(all_ids),
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["manifest"] = str(manifest_path)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-dir", required=True)
    parser.add_argument("--kb-root", default=None)
    parser.add_argument("--out", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        helper_root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(helper_root))
        from skills._shared.scripts.project_paths import find_project_root, resolve_path

        root = find_project_root(args.kb_root)
        raw_root = resolve_path("library.raw", start=root, must_exist=True)
        source_root = resolve_path("library.source", start=root, must_exist=True)
        out_dir = Path(args.out)
        if not out_dir.is_absolute():
            out_dir = (root / out_dir).resolve()
        if not out_dir.is_relative_to(root / "tmp") and not out_dir.is_relative_to(root / "skills" / "_ops" / "runtime"):
            raise ValueError("output must remain under tmp/ or skills/_ops/runtime/")
        reports_dir = Path(args.reports_dir).expanduser().resolve()
        if not reports_dir.is_dir():
            raise FileNotFoundError(f"reports directory not found: {reports_dir}")
        if not raw_root.is_dir() or not source_root.is_dir():
            raise FileNotFoundError("library.raw or library.source is unavailable")
        result = build_ledgers(root, reports_dir, out_dir)
        result["ok"] = True
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "type": type(exc).__name__}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result["manifest"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
