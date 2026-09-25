#!/usr/bin/env python
"""Diagnose PDF text/image structure before choosing a conversion path."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

try:
    import fitz  # PyMuPDF
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit("PyMuPDF is required: install/import fitz before diagnosing PDFs") from exc


TEXT_PAGE_MIN_CHARS = 80
FULL_PAGE_IMAGE_THRESHOLD = 0.65


def image_coverage(block: dict[str, Any], page_area: float) -> float:
    bbox = block.get("bbox") or [0, 0, 0, 0]
    try:
        x0, y0, x1, y1 = [float(v) for v in bbox]
    except (TypeError, ValueError):
        return 0.0
    if page_area <= 0:
        return 0.0
    return max(0.0, (x1 - x0) * (y1 - y0) / page_area)


def has_private_use_chars(text: str) -> bool:
    return any("\ue000" <= ch <= "\uf8ff" for ch in text)


def line_fragment_ratio(text: str) -> float:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return 0.0
    short_lines = sum(1 for line in lines if len(line) <= 3)
    return short_lines / len(lines)


def diagnose_pdf(pdf_path: Path, max_pages: int = 0) -> dict[str, Any]:
    pdf_path = pdf_path.resolve()
    doc = fitz.open(pdf_path)
    page_count = len(doc)
    limit = page_count if max_pages <= 0 else min(page_count, max_pages)
    page_reports: list[dict[str, Any]] = []

    for index in range(limit):
        page = doc[index]
        text = page.get_text("text") or ""
        stripped = text.strip()
        blocks = page.get_text("dict").get("blocks", [])
        text_blocks = [block for block in blocks if block.get("type") == 0]
        image_blocks = [block for block in blocks if block.get("type") == 1]
        page_area = float(page.rect.width * page.rect.height)
        max_image_coverage = max(
            (image_coverage(block, page_area) for block in image_blocks),
            default=0.0,
        )
        report = {
            "page": index + 1,
            "text_chars": len(stripped),
            "text_blocks": len(text_blocks),
            "image_blocks": len(image_blocks),
            "xref_images": len(page.get_images(full=True)),
            "max_image_coverage": round(max_image_coverage, 4),
            "has_full_page_image": max_image_coverage >= FULL_PAGE_IMAGE_THRESHOLD,
            "has_private_use_chars": has_private_use_chars(stripped),
            "line_fragment_ratio": round(line_fragment_ratio(stripped), 4),
        }
        page_reports.append(report)

    doc.close()

    analyzed = len(page_reports)
    text_pages = sum(1 for p in page_reports if p["text_chars"] >= TEXT_PAGE_MIN_CHARS)
    zero_text_pages = sum(1 for p in page_reports if p["text_chars"] == 0)
    image_pages = sum(1 for p in page_reports if p["image_blocks"] or p["xref_images"])
    full_image_pages = sum(1 for p in page_reports if p["has_full_page_image"])
    total_chars = sum(int(p["text_chars"]) for p in page_reports)
    avg_chars = total_chars / analyzed if analyzed else 0.0
    text_page_ratio = text_pages / analyzed if analyzed else 0.0
    image_page_ratio = image_pages / analyzed if analyzed else 0.0
    full_image_ratio = full_image_pages / analyzed if analyzed else 0.0
    fragment_values = [float(p["line_fragment_ratio"]) for p in page_reports if p["text_chars"]]
    avg_fragment_ratio = statistics.mean(fragment_values) if fragment_values else 0.0
    private_use_pages = sum(1 for p in page_reports if p["has_private_use_chars"])

    if analyzed == 0:
        classification = "unknown_pdf"
    elif text_page_ratio <= 0.2 and full_image_ratio >= 0.5:
        classification = "image_pdf"
    elif image_page_ratio >= 0.3 and text_page_ratio >= 0.3:
        classification = "mixed_pdf"
    elif full_image_ratio >= 0.3:
        classification = "mixed_pdf"
    else:
        classification = "text_pdf"

    formula_risk = (
        classification == "mixed_pdf"
        or private_use_pages > 0
        or (avg_fragment_ratio >= 0.25 and avg_chars >= TEXT_PAGE_MIN_CHARS)
    )
    recommendation = "combined_ocr" if classification in {"image_pdf", "mixed_pdf"} or formula_risk else "pymupdf4llm"

    return {
        "pdf": str(pdf_path),
        "page_count": page_count,
        "pages_analyzed": analyzed,
        "classification": classification,
        "recommendation": recommendation,
        "summary": {
            "text_char_total": total_chars,
            "avg_text_chars_per_page": round(avg_chars, 2),
            "zero_text_pages": zero_text_pages,
            "text_page_ratio": round(text_page_ratio, 4),
            "image_page_ratio": round(image_page_ratio, 4),
            "full_page_image_ratio": round(full_image_ratio, 4),
            "private_use_pages": private_use_pages,
            "avg_line_fragment_ratio": round(avg_fragment_ratio, 4),
            "formula_risk": formula_risk,
        },
        "page_reports": page_reports,
    }


def print_human(result: dict[str, Any]) -> None:
    summary = result["summary"]
    print(f"PDF: {result['pdf']}")
    print(f"Pages: {result['page_count']} analyzed={result['pages_analyzed']}")
    print(f"Classification: {result['classification']}")
    print(f"Recommendation: {result['recommendation']}")
    print(
        "Summary: "
        f"chars={summary['text_char_total']} "
        f"zero_text_pages={summary['zero_text_pages']} "
        f"text_page_ratio={summary['text_page_ratio']} "
        f"image_page_ratio={summary['image_page_ratio']} "
        f"full_page_image_ratio={summary['full_page_image_ratio']} "
        f"formula_risk={summary['formula_risk']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose PDF text/image structure for BeMarkdown.")
    parser.add_argument("pdf", help="PDF file to inspect")
    parser.add_argument("--max-pages", type=int, default=0, help="Analyze only the first N pages; 0 means all pages")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args(argv)

    result = diagnose_pdf(Path(args.pdf), max_pages=args.max_pages)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
