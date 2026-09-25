#!/usr/bin/env python3
"""Rebuild a combined OCR wave summary from final quality reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_REPORT = "final_quality_report.json"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def wave_name(path: Path) -> str:
    if path.name == DEFAULT_REPORT:
        return path.parent.name
    return path.stem


def find_reports(root: Path, explicit_waves: list[str]) -> list[Path]:
    if explicit_waves:
        reports: list[Path] = []
        for wave in explicit_waves:
            path = Path(wave)
            if not path.is_absolute():
                path = root / path
            if path.is_dir():
                path = path / DEFAULT_REPORT
            if not path.exists():
                raise FileNotFoundError(f"final quality report not found for wave: {wave}")
            reports.append(path.resolve())
        return reports
    return sorted(root.glob(f"*/{DEFAULT_REPORT}"))


def build_summary(root: Path, reports: list[Path]) -> dict[str, Any]:
    waves: list[dict[str, Any]] = []
    for report_path in reports:
        report = load_json(report_path)
        waves.append(
            {
                "name": wave_name(report_path),
                "report": str(report_path),
                "ok_for_import": bool(report.get("ok_for_import")),
                "issue_count": int(report.get("issue_count", 0)),
                "unresolved_conflict_count": int(report.get("unresolved_conflict_count", 0)),
                "conflict_count": int(report.get("conflict_count", 0)),
                "candidate_count": int(report.get("candidate_count", 0)),
                "issues": report.get("issues", []),
            }
        )
    bad = [
        wave
        for wave in waves
        if not wave["ok_for_import"] or wave["issue_count"] or wave["unresolved_conflict_count"]
    ]
    return {
        "ok_for_import": not bad,
        "wave_count": len(waves),
        "bad_wave_count": len(bad),
        "issue_count": sum(wave["issue_count"] for wave in waves),
        "unresolved_conflict_count": sum(wave["unresolved_conflict_count"] for wave in waves),
        "root": str(root),
        "waves": waves,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize final_quality_report.json files from combined OCR waves.")
    parser.add_argument("root", help="Directory containing wave subdirectories, or a parent task OCR/combined directory")
    parser.add_argument("--wave", action="append", default=[], help="Specific wave directory or report path; may repeat")
    parser.add_argument("--out", help="Write summary JSON to this path")
    parser.add_argument("--json", action="store_true", help="Print full JSON summary")
    parser.add_argument("--no-fail", action="store_true", help="Return 0 even when a wave is not import-ready")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    reports = find_reports(root, args.wave)
    if not reports:
        raise SystemExit(f"No {DEFAULT_REPORT} files found under {root}")
    summary = build_summary(root, reports)

    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = root / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            json.dumps(
                {
                    "ok_for_import": summary["ok_for_import"],
                    "wave_count": summary["wave_count"],
                    "bad_wave_count": summary["bad_wave_count"],
                    "issue_count": summary["issue_count"],
                    "unresolved_conflict_count": summary["unresolved_conflict_count"],
                },
                ensure_ascii=False,
            )
        )

    if not summary["ok_for_import"] and not args.no_fail:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
