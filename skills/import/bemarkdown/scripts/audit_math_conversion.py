#!/usr/bin/env python3
"""Audit DOCX OMML/EQ coverage without rewriting source or knowledge content."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from lxml import etree


SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SKILLS_ROOT / "_shared" / "scripts"))

from eq_field_to_latex import extract_field_results  # noqa: E402
from omml_to_latex import M, convert_omml  # noqa: E402
from project_paths import resolve_path  # noqa: E402


LEGACY_PATTERNS = (
    (re.compile(r"\\x\s*\\to\s*\("), "eq-x-top-as-vector"),
    (re.compile(r"\\o\s*\\al\s*\("), "eq-overstrike-as-nuclear-scripts"),
)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def audit_docx_tree(source_root: Path) -> dict[str, Any]:
    source_root = source_root.resolve()
    status_counts: Counter[str] = Counter()
    issue_counts: Counter[str] = Counter()
    compatibility_counts: Counter[str] = Counter()
    impacted_files: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    docx_files = sorted(source_root.rglob("*.docx"), key=lambda path: path.as_posix().lower())

    for path in docx_files:
        relative = path.relative_to(source_root).as_posix()
        source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        impacts: list[dict[str, Any]] = []
        try:
            with zipfile.ZipFile(path, "r") as archive:
                tree = etree.fromstring(archive.read("word/document.xml"))
        except Exception as exc:  # noqa: BLE001 - audit must retain every unreadable source
            errors.append({"source_file": relative, "error": str(exc)})
            continue

        for formula_index, elem in enumerate(tree.findall(f".//{{{M}}}oMath"), start=1):
            result = convert_omml(elem)
            status_counts[f"omml.{result.status}"] += 1
            for item in result.unsupported_constructs:
                issue_counts[item["code"]] += 1
            if result.status != "converted":
                source = etree.tostring(elem, encoding="utf-8")
                impacts.append(
                    {
                        "source_kind": "omml",
                        "formula_index": formula_index,
                        "formula_source_sha256": hashlib.sha256(source).hexdigest(),
                        **result.to_dict(),
                    }
                )

        for formula_index, (instruction, result) in enumerate(extract_field_results(path), start=1):
            status_counts[f"eq.{result.status}"] += 1
            for item in result.unsupported_constructs:
                issue_counts[item["code"]] += 1
            compatibility_rules = [rule for pattern, rule in LEGACY_PATTERNS if pattern.search(instruction)]
            for rule in compatibility_rules:
                compatibility_counts[rule] += 1
            if result.status != "converted" or compatibility_rules:
                impacts.append(
                    {
                        "source_kind": "eq",
                        "formula_index": formula_index,
                        "formula_source_sha256": hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
                        "instruction": instruction,
                        "historical_compatibility_candidates": compatibility_rules,
                        **result.to_dict(),
                    }
                )

        if impacts:
            impacted_files.append(
                {
                    "source_file": relative,
                    "source_sha256": source_sha256,
                    "impacts": impacts,
                }
            )

    omml_total = sum(value for key, value in status_counts.items() if key.startswith("omml."))
    eq_total = sum(value for key, value in status_counts.items() if key.startswith("eq."))
    blocked_total = sum(
        value
        for key, value in status_counts.items()
        if key.endswith(".needs_review") or key.endswith(".unsupported")
    )
    return {
        "schema_version": 1,
        "source_root_id": "library.source",
        "summary": {
            "docx_scanned": len(docx_files),
            "docx_errors": len(errors),
            "omml_total": omml_total,
            "eq_total": eq_total,
            "formula_total": omml_total + eq_total,
            "blocked_total": blocked_total,
            "impacted_file_count": len(impacted_files),
            "status_counts": dict(sorted(status_counts.items())),
            "issue_counts": dict(sorted(issue_counts.items())),
            "historical_compatibility_candidates": dict(sorted(compatibility_counts.items())),
        },
        "impacted_files": impacted_files,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-on-blocked", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    source_root = args.source_root or resolve_path("library.source", start=SCRIPT_DIR, must_exist=True)
    report = audit_docx_tree(source_root)
    if args.output:
        _atomic_write_json(args.output, report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        summary = report["summary"]
        print(
            f"DOCX={summary['docx_scanned']} OMML={summary['omml_total']} EQ={summary['eq_total']} "
            f"blocked={summary['blocked_total']} impacted_files={summary['impacted_file_count']}"
        )
        if args.output:
            print(f"Report: {args.output}")
    if report["summary"]["docx_errors"]:
        return 2
    if args.fail_on_blocked and report["summary"]["blocked_total"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
