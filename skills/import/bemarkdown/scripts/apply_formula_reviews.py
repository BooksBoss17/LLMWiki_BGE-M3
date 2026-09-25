#!/usr/bin/env python3
"""Apply hash-bound formula/WMF review results to converted Markdown.

The converter intentionally leaves risky WMF references in place and writes
``formula_review_tasks.jsonl``. This resume seam verifies the original WMF
hash, applies either LaTeX or source-evidenced plain text (for punctuation or
option labels), updates the formula manifest, and can fail closed while any
review remains.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    if text.startswith("["):
        value = json.loads(text)
        if not isinstance(value, list):
            raise ValueError("review file JSON root must be an array")
        return [dict(item) for item in value]
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"review line {line_no} must be an object")
        records.append(dict(value))
    return records


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_text(
        path,
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
    )


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def apply_reviews(
    md_path: Path,
    media_dir: Path,
    review_path: Path,
    *,
    manifest_path: Path | None = None,
    out_md: Path | None = None,
    out_manifest: Path | None = None,
    out_reviews: Path | None = None,
    require_all: bool = False,
) -> dict[str, Any]:
    text = md_path.read_text(encoding="utf-8")
    reviews = _load_records(review_path)
    manifest: dict[str, Any] | None = None
    if manifest_path:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("formula manifest root must be an object")
        manifest = raw

    applied: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for review in reviews:
        candidate_id = str(review.get("candidate_id") or "").strip()
        if not candidate_id:
            raise ValueError("each review requires candidate_id")
        if review.get("status") != "resolved":
            unresolved.append(candidate_id)
            continue
        evidence = str(review.get("visual_evidence") or "").strip()
        if not evidence:
            raise ValueError(f"{candidate_id}: visual_evidence is required")
        confidence = review.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            raise ValueError(f"{candidate_id}: confidence must be between 0 and 1")

        wmf_path = media_dir / candidate_id
        if not wmf_path.is_file():
            raise FileNotFoundError(f"{candidate_id}: source WMF not found: {wmf_path}")
        expected_hash = str(review.get("source_sha256") or "").lower().strip()
        actual_hash = _sha256(wmf_path)
        if expected_hash and expected_hash != actual_hash:
            raise ValueError(f"{candidate_id}: source_sha256 mismatch")

        kind = str(review.get("replacement_kind") or "latex").lower().strip()
        if kind == "text":
            replacement = str(review.get("replacement") or "")
            if not replacement:
                raise ValueError(f"{candidate_id}: text replacement is empty")
        elif kind == "latex":
            latex = str(review.get("final_latex") or "").strip()
            if not latex:
                raise ValueError(f"{candidate_id}: final_latex is empty")
            replacement = f"${latex}$"
        else:
            raise ValueError(f"{candidate_id}: replacement_kind must be latex or text")

        ref = f"media/{candidate_id}"
        pattern = re.compile(r"!\[图\]\(" + re.escape(ref) + r"\)")
        text, count = pattern.subn(lambda _match: replacement, text)
        if count == 0:
            raise ValueError(f"{candidate_id}: no matching WMF reference in Markdown")

        if manifest is not None:
            record = manifest.get(ref)
            if record is None:
                record = next(
                    (
                        value
                        for value in manifest.values()
                        if isinstance(value, dict)
                        and Path(str(value.get("wmf_ref") or "")).name == candidate_id
                    ),
                    None,
                )
            if record is None:
                raise ValueError(f"{candidate_id}: no manifest record found")
            resolution = record.setdefault("resolution", {})
            resolution.update(
                {
                    "status": "reviewed_final",
                    "final_latex": str(review.get("final_latex") or "") if kind == "latex" else None,
                    "risk_flags": [],
                    "geometry_issues": [],
                    "reviewed": True,
                    "replacement_kind": kind,
                    "replacement": replacement,
                }
            )
            record["review"] = {
                "status": "resolved",
                "visual_evidence": evidence,
                "confidence": float(confidence),
                "source_sha256": actual_hash,
            }

        review["applied_replacement"] = replacement
        review["applied_count"] = count
        review["source_sha256"] = actual_hash
        applied.append(review)

    if manifest is not None and require_all:
        unresolved.extend(
            str(key)
            for key, value in manifest.items()
            if isinstance(value, dict)
            and (value.get("resolution") or {}).get("status") not in {"machine_final", "reviewed_final"}
        )
    if require_all and unresolved:
        raise ValueError("unresolved formula reviews: " + ", ".join(sorted(set(unresolved))))

    out_md = out_md or md_path.with_name(md_path.stem + "_reviewed.md")
    _atomic_text(out_md, text)
    if manifest is not None:
        out_manifest = out_manifest or manifest_path.with_name(manifest_path.stem + "_reviewed.json")
        _atomic_text(out_manifest, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    if out_reviews:
        _write_jsonl(out_reviews, reviews)
    return {
        "ok": not unresolved,
        "review_count": len(reviews),
        "applied_count": len(applied),
        "unresolved_count": len(sorted(set(unresolved))),
        "unresolved": sorted(set(unresolved)),
        "out_md": str(out_md),
        "out_manifest": str(out_manifest) if out_manifest else None,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def apply_audit_reviews(database: Path, review_path: Path, *, require_all: bool = False) -> dict[str, Any]:
    """Apply hash-bound MTEF or independent-WMF decisions to the audit WAL."""
    reviews = _load_records(review_path)
    connection = sqlite3.connect(database, timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    fingerprint_row = connection.execute("SELECT value FROM meta WHERE key='runtime_fingerprint'").fetchone()
    if fingerprint_row is None:
        connection.close()
        raise ValueError("audit database has no runtime_fingerprint")
    fingerprint = str(fingerprint_row[0])
    applied = 0
    unresolved: list[str] = []
    try:
        connection.execute("BEGIN IMMEDIATE")
        for review in reviews:
            review_key = str(review.get("review_key") or review.get("candidate_id") or "").strip()
            if not review_key:
                raise ValueError("each audit review requires review_key")
            if review.get("status") != "resolved":
                unresolved.append(review_key)
                continue
            if str(review.get("runtime_fingerprint") or "") != fingerprint:
                raise ValueError(f"{review_key}: runtime_fingerprint mismatch")
            evidence = str(review.get("visual_evidence") or "").strip()
            confidence = review.get("confidence")
            if not evidence:
                raise ValueError(f"{review_key}: visual_evidence is required")
            if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
                raise ValueError(f"{review_key}: confidence must be between 0 and 1")

            mtef_sha = str(review.get("mtef_sha256") or "").lower().strip()
            wmf_sha = str(review.get("wmf_sha256") or "").lower().strip()
            if mtef_sha:
                if review_key != mtef_sha:
                    raise ValueError(f"{review_key}: review_key must equal mtef_sha256")
                current = connection.execute("SELECT * FROM mtef_results WHERE mtef_sha256=?", (mtef_sha,)).fetchone()
                if current is None:
                    raise ValueError(f"{review_key}: MTEF hash is not present in the audit")
                preview_rows = list(
                    connection.execute(
                        """
                        SELECT DISTINCT p.wmf_sha256,p.png_sha256,p.render_status,p.render_json FROM previews p
                        JOIN formula_occurrences o ON o.wmf_sha256=p.wmf_sha256
                        WHERE o.mtef_sha256=? ORDER BY p.wmf_sha256
                        """,
                        (mtef_sha,),
                    )
                )
                expected_previews = {row[0]: row[1] for row in preview_rows}
                supplied_previews = {
                    str(row.get("wmf_sha256") or "").lower(): str(row.get("png_sha256") or "").lower()
                    for row in review.get("previews") or []
                }
                if supplied_previews != expected_previews:
                    raise ValueError(f"{review_key}: preview hash set mismatch")
                final_kind = str(review.get("final_kind") or "latex").lower().strip()
                if final_kind not in {"latex", "empty"}:
                    raise ValueError(f"{review_key}: final_kind must be latex or empty")
                final_latex = str(review.get("final_latex") or "").strip()
                if final_kind == "latex" and not final_latex:
                    raise ValueError(f"{review_key}: final_latex is required")
                if final_kind == "empty":
                    current_issues = set(json.loads(current["issue_json"] or "[]"))
                    if current["structure_latex"] or "MTEF mapping produced empty LaTeX" not in current_issues:
                        raise ValueError(f"{review_key}: empty is allowed only for a structurally empty MTEF")
                    if not preview_rows:
                        raise ValueError(f"{review_key}: empty MTEF requires blank preview evidence")
                    for preview_row in preview_rows:
                        render = json.loads(preview_row["render_json"] or "{}")
                        if preview_row["render_status"] != "blocked" or render.get("geometry_issues") != ["blank_render"]:
                            raise ValueError(f"{review_key}: every empty-MTEF preview must be hash-bound blank evidence")
                    final_latex = ""
                for preview_sha in expected_previews:
                    connection.execute(
                        """
                        INSERT INTO reviews(
                            review_key,mtef_sha256,wmf_sha256,final_kind,final_value,visual_evidence,
                            confidence,runtime_fingerprint,status,reviewed_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(review_key) DO UPDATE SET
                            final_kind=excluded.final_kind,final_value=excluded.final_value,visual_evidence=excluded.visual_evidence,
                            confidence=excluded.confidence,runtime_fingerprint=excluded.runtime_fingerprint,
                            status=excluded.status,reviewed_at=excluded.reviewed_at
                        """,
                        (
                            f"{mtef_sha}:{preview_sha}", mtef_sha, preview_sha, final_kind, final_latex,
                            evidence, float(confidence), fingerprint, "resolved", _utc_now(),
                        ),
                    )
                connection.execute(
                    """
                    UPDATE mtef_results SET final_latex=?,final_kind=?,final_status='reviewed_final',risk_json='[]',
                        issue_json='[]',updated_at=? WHERE mtef_sha256=?
                    """,
                    (final_latex or None, final_kind, _utc_now(), mtef_sha),
                )
                if final_kind == "empty":
                    connection.execute(
                        """
                        UPDATE previews SET render_status='reviewed_empty',final_kind='empty',final_value='',
                            final_status='reviewed_final',updated_at=?
                        WHERE wmf_sha256 IN (
                            SELECT wmf_sha256 FROM formula_occurrences WHERE mtef_sha256=? AND wmf_sha256 IS NOT NULL
                        )
                        """,
                        (_utc_now(), mtef_sha),
                    )
            elif wmf_sha:
                if review_key != f"wmf:{wmf_sha}":
                    raise ValueError(f"{review_key}: review_key must equal wmf:<wmf_sha256>")
                preview = connection.execute("SELECT * FROM previews WHERE wmf_sha256=?", (wmf_sha,)).fetchone()
                if preview is None:
                    raise ValueError(f"{review_key}: WMF hash is not present in the audit")
                supplied_png = str(review.get("png_sha256") or "").lower()
                if supplied_png != str(preview["png_sha256"] or "").lower():
                    raise ValueError(f"{review_key}: PNG evidence hash mismatch")
                final_kind = str(review.get("final_kind") or "").lower().strip()
                if final_kind not in {"latex", "text", "image"}:
                    raise ValueError(f"{review_key}: final_kind must be latex, text, or image")
                final_value = str(review.get("final_value") or review.get("final_latex") or "").strip()
                if not final_value:
                    raise ValueError(f"{review_key}: final_value is required")
                connection.execute(
                    """
                    INSERT INTO reviews(
                        review_key,mtef_sha256,wmf_sha256,final_kind,final_value,visual_evidence,
                        confidence,runtime_fingerprint,status,reviewed_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(review_key) DO UPDATE SET
                        final_kind=excluded.final_kind,final_value=excluded.final_value,
                        visual_evidence=excluded.visual_evidence,confidence=excluded.confidence,
                        runtime_fingerprint=excluded.runtime_fingerprint,status=excluded.status,
                        reviewed_at=excluded.reviewed_at
                    """,
                    (
                        review_key, None, wmf_sha, final_kind, final_value, evidence, float(confidence),
                        fingerprint, "resolved", _utc_now(),
                    ),
                )
                connection.execute(
                    "UPDATE previews SET final_kind=?,final_value=?,final_status='reviewed_final',updated_at=? WHERE wmf_sha256=?",
                    (final_kind, final_value, _utc_now(), wmf_sha),
                )
            else:
                raise ValueError(f"{review_key}: mtef_sha256 or wmf_sha256 is required")
            applied += 1

        if require_all:
            unresolved.extend(
                row[0]
                for row in connection.execute(
                    "SELECT mtef_sha256 FROM mtef_results WHERE final_status NOT IN ('machine_final','reviewed_final')"
                )
            )
            unresolved.extend(
                f"wmf:{row[0]}"
                for row in connection.execute(
                    """
                    SELECT DISTINCT p.wmf_sha256 FROM previews p
                    JOIN formula_occurrences o ON o.wmf_sha256=p.wmf_sha256
                    WHERE o.source_kind='independent_wmf' AND p.final_status!='reviewed_final'
                    """
                )
            )
        if require_all and unresolved:
            raise ValueError("unresolved formula reviews: " + ", ".join(sorted(set(unresolved))))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "ok": not unresolved,
        "review_count": len(reviews),
        "applied_count": applied,
        "unresolved_count": len(set(unresolved)),
        "unresolved": sorted(set(unresolved)),
        "database": str(database),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--md", type=Path)
    parser.add_argument("--media-dir", type=Path)
    parser.add_argument("--reviews", required=True, type=Path)
    parser.add_argument("--audit-db", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--out-md", type=Path)
    parser.add_argument("--out-manifest", type=Path)
    parser.add_argument("--out-reviews", type=Path)
    parser.add_argument("--require-all", action="store_true")
    args = parser.parse_args()
    if args.audit_db:
        if args.md or args.media_dir or args.manifest:
            parser.error("--audit-db cannot be combined with Markdown review inputs")
        result = apply_audit_reviews(args.audit_db, args.reviews, require_all=args.require_all)
    else:
        if not args.md or not args.media_dir:
            parser.error("--md and --media-dir are required unless --audit-db is used")
        result = apply_reviews(
            args.md,
            args.media_dir,
            args.reviews,
            manifest_path=args.manifest,
            out_md=args.out_md,
            out_manifest=args.out_manifest,
            out_reviews=args.out_reviews,
            require_all=args.require_all,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] or not args.require_all else 2


if __name__ == "__main__":
    raise SystemExit(main())
