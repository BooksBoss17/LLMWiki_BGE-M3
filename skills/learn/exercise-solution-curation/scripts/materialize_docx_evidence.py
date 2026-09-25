"""Materialize a DOCX as Markdown using only confirmed campaign evidence.

This is the final-task seam: the source DOCX is converted locally, image
relationships are matched by SHA-256, confirmed formulas become LaTeX, and
confirmed physical diagrams become native-bound PNGs.  No OCR/VLM image is
opened again here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any


BEMARKDOWN_SCRIPTS = Path(__file__).resolve().parents[3] / "import" / "bemarkdown" / "scripts"
if str(BEMARKDOWN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(BEMARKDOWN_SCRIPTS))

from omml_docx_to_md import convert_docx_to_md  # noqa: E402


IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<target>media/[^)]+)\)")


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def collect_confirmed_evidence(
    campaign_root: Path,
    batch_id: str,
    kb_root: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, Path]]:
    resolutions: dict[str, dict[str, Any]] = {}
    visuals: dict[str, Path] = {}
    evidence_root = campaign_root / "work" / batch_id / "evidence"
    for ledger_path in evidence_root.glob("*/ledger.json"):
        ledger = load_json(ledger_path)
        if ledger.get("evidence_stage") != "complete":
            raise ValueError(f"evidence ledger is not complete: {ledger_path}")
        for asset in ledger.get("assets") or []:
            source_hash = str(asset.get("source_sha256") or "")
            visual_raw = str(asset.get("visual_path") or "")
            visual = Path(visual_raw)
            if not visual.is_absolute():
                visual = kb_root / visual
            if source_hash and visual.is_file():
                visuals[source_hash] = visual.resolve()
            inherited = (asset.get("duplicate_of") or {}).get("donor_resolution")
            if source_hash and isinstance(inherited, dict):
                resolutions.setdefault(source_hash, inherited)
        for segment in ledger.get("segments") or []:
            review = segment.get("review") or {}
            for source_hash, resolution in (review.get("resolutions") or {}).items():
                if resolution.get("status") in {"unresolved", "high_risk"}:
                    raise ValueError(f"unresolved evidence cannot be materialized: {source_hash}")
                resolutions[str(source_hash)] = resolution
    return resolutions, visuals


def replace_evidence_images(
    markdown: str,
    media_dir: Path,
    resolutions: dict[str, dict[str, Any]],
    visuals: dict[str, Path],
    output_media: Path,
) -> tuple[str, dict[str, int]]:
    output_media.mkdir(parents=True, exist_ok=True)
    stats = {"formula": 0, "diagram": 0, "unmatched": 0}

    def replacement(match: re.Match[str], *, display: bool) -> str:
        if match.group("alt") == "原题图":
            return match.group(0)
        relative = match.group("target")
        source = media_dir / Path(relative).name
        if not source.is_file():
            stats["unmatched"] += 1
            return match.group(0)
        source_hash = sha256_path(source)
        resolution = resolutions.get(source_hash)
        if not resolution:
            stats["unmatched"] += 1
            return match.group(0)
        status = str(resolution.get("status") or "")
        final_latex = str(resolution.get("final_latex") or "").strip()
        if status in {"resolved", "confirmed"} and final_latex:
            stats["formula"] += 1
            return f"$${final_latex}$$" if display else f"${final_latex}$"
        if status == "not_applicable":
            visual = visuals.get(source_hash)
            if visual is None or not visual.is_file():
                raise FileNotFoundError(f"confirmed diagram visual is missing: {source_hash}")
            target_name = f"{source_hash[:16]}.png"
            target = output_media / target_name
            if not target.is_file() or sha256_path(target) != sha256_path(visual):
                shutil.copy2(visual, target)
            stats["diagram"] += 1
            return f"![原题图](media/{target_name})"
        raise ValueError(f"unsupported confirmed evidence status for {source_hash}: {status}")

    standalone = re.compile(r"(?m)^\s*(!\[[^\]]*\]\((media/[^)]+)\))\s*$")

    def replace_standalone(match: re.Match[str]) -> str:
        image_match = IMAGE_RE.search(match.group(1))
        if image_match is None:
            return match.group(0)
        return replacement(image_match, display=True)

    materialized = standalone.sub(replace_standalone, markdown)
    materialized = IMAGE_RE.sub(lambda match: replacement(match, display=False), materialized)
    return materialized, stats


def materialize(
    kb_root: Path,
    campaign_root: Path,
    batch_id: str,
    source_docx: Path,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path, _, _ = convert_docx_to_md(str(source_docx), str(output_dir))
    converted = Path(markdown_path)
    resolutions, visuals = collect_confirmed_evidence(campaign_root, batch_id, kb_root)
    materialized, stats = replace_evidence_images(
        converted.read_text(encoding="utf-8"),
        output_dir / "media",
        resolutions,
        visuals,
        output_dir / "media",
    )
    destination = output_dir / "materialized.md"
    destination.write_text(materialized, encoding="utf-8", newline="\n")
    report = {
        "schema_version": 2,
        # The source DOCX can contain questions outside the active batch; their
        # images intentionally remain unmatched.  Selected-question extraction
        # performs the later zero-WMF gate, while any unresolved active evidence
        # has already raised above.
        "ok": True,
        "source_docx": str(source_docx.resolve()),
        "source_sha256": sha256_path(source_docx),
        "batch_id": batch_id,
        "output_markdown": str(destination.resolve()),
        "stats": stats,
    }
    (output_dir / "materialization_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb-root", default=".")
    parser.add_argument("--campaign-root", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--source-docx", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    report = materialize(
        Path(args.kb_root).resolve(),
        Path(args.campaign_root).resolve(),
        args.batch_id,
        Path(args.source_docx).resolve(),
        Path(args.output_dir).resolve(),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
