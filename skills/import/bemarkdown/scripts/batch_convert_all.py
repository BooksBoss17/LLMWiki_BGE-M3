#!/usr/bin/env python3
"""Batch convert all DOCX files in a directory to Markdown with full formula extraction.

Handles all four Word formula formats:
1. OMML (m:oMath) → omml_to_latex.py
2. EQ field codes (fldChar/instrText) → eq_field_to_latex.py  
3. WMF/OLE images → full-frame native render → PP-FormulaNet/TexTeller consensus/VLM
4. PNG images with alt LaTeX → mammoth alt→LaTeX replacement

Usage:
    python batch_convert_all.py <input_dir> <output_dir>

The input_dir should contain subdirectories with .docx files.
"""
import argparse
import sys
import os
import re
import time
import json
import hashlib
import sqlite3
import uuid

# Setup paths (canonical KB skills library)
from pathlib import Path
SCRIPT_DIR = str(Path(__file__).resolve().parent)
SOFFICE = os.environ.get('LIBREOFFICE_SOFFICE')

sys.path.insert(0, SCRIPT_DIR)

from omml_docx_to_md import convert_docx_to_md
from wmf_to_png import batch_convert_wmf
from formula_candidate_utils import make_formula_review_task
from formula_ensemble import run_formula_ensemble
from mtef_formula_audit import FINAL_STATUSES, run_audit


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_json(path: Path, payload: object) -> None:
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def reviewed_wmf_replacements(database: Path, media_dir: Path, refs: list[str]) -> dict[str, dict[str, str]]:
    """Resolve only machine/reviewed finals whose WMF hash matches the audit."""
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    replacements: dict[str, dict[str, str]] = {}
    try:
        for ref in sorted(set(refs)):
            source = media_dir / Path(ref).name
            if not source.is_file():
                continue
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            mtef = connection.execute(
                """
                SELECT DISTINCT r.final_status,r.final_kind,r.final_latex FROM mtef_results r
                JOIN formula_occurrences o ON o.mtef_sha256=r.mtef_sha256
                WHERE o.wmf_sha256=?
                """,
                (digest,),
            ).fetchall()
            if len(mtef) == 1 and mtef[0]["final_status"] in FINAL_STATUSES:
                if mtef[0]["final_kind"] == "empty":
                    replacements[ref] = {"kind": "empty", "value": "", "status": str(mtef[0]["final_status"])}
                    continue
                if mtef[0]["final_kind"] == "latex" and mtef[0]["final_latex"]:
                    replacements[ref] = {"kind": "latex", "value": str(mtef[0]["final_latex"]), "status": str(mtef[0]["final_status"])}
                    continue
            preview = connection.execute(
                "SELECT final_status,final_kind,final_value FROM previews WHERE wmf_sha256=?",
                (digest,),
            ).fetchone()
            if preview and preview["final_status"] == "reviewed_final" and preview["final_kind"] in {"latex", "text", "image"}:
                replacements[ref] = {
                    "kind": str(preview["final_kind"]),
                    "value": str(preview["final_value"]),
                    "status": "reviewed_final",
                }
    finally:
        connection.close()
    return replacements


def convert_one_docx(
    docx_path,
    out_dir,
    latex_ocr=None,
    *,
    mtef_mode: str = "shadow",
    audit_db: str | os.PathLike[str] | None = None,
    audit_ready: bool = False,
):
    """Convert a single DOCX to Markdown with all formula formats handled.
    
    Args:
        docx_path: Path to .docx file
        out_dir: Output directory
        latex_ocr: Deprecated compatibility argument; ignored.  The local
            two-engine shared formula ensemble owns model initialization.
    
    Returns:
        dict with conversion stats
    """
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)
    
    # Step 1: DOCX → MD (handles OMML + EQ fields + VML/OLE + DrawingML)
    md_path, formula_count, img_count = convert_docx_to_md(docx_path, out_dir)
    
    with open(md_path, 'r', encoding='utf-8') as f:
        md_content = f.read()
    
    # Check for WMF references
    wmf_refs = re.findall(r'!\[图\]\((media/[^)]+\.wmf)\)', md_content)
    wmf_ocr_count = 0
    wmf_ocr_score = 0
    wmf_pending_review = 0
    audit_replacements: dict[str, dict[str, str]] = {}
    if audit_db and Path(audit_db).is_file() and wmf_refs:
        audit_replacements = reviewed_wmf_replacements(Path(audit_db), Path(out_dir) / "media", wmf_refs)
    
    if wmf_refs:
        # Step 2: WMF → PNG
        png_dir = os.path.join(out_dir, "wmf_png")
        rendered = batch_convert_wmf(os.path.join(out_dir, "media"), png_dir, SOFFICE)
        
        # Step 3: PNG → local two-engine candidates and conservative consensus
        ocr_results = {}
        review_tasks = []
        ref_by_png = {}
        png_paths = []
        for wmf_ref in sorted(set(wmf_refs)):
            if wmf_ref in audit_replacements:
                continue
            wmf_name = os.path.basename(wmf_ref)
            png_path = rendered.get(wmf_name)
            if png_path and os.path.exists(png_path):
                resolved = str(Path(png_path).resolve())
                ref_by_png[resolved] = wmf_ref
                png_paths.append(resolved)

        ensemble = run_formula_ensemble(png_paths, os.path.join(out_dir, "formula_ensemble")) if png_paths else {"records": []}
        records = {record["source_image"]: record for record in ensemble.get("records", [])}
        for png_path, wmf_ref in ref_by_png.items():
            wmf_name = os.path.basename(wmf_ref)
            record = records.get(png_path) or {
                "source_image": png_path,
                "candidates": [],
                "resolution": {"status": "machine_abstain", "final_latex": None, "risk_flags": ["engine_result_missing"]},
            }
            record["wmf_ref"] = wmf_ref
            ocr_results[wmf_ref] = record
            resolution = record["resolution"]
            confidences = [
                float(value["confidence"])
                for value in record.get("candidates", [])
                if isinstance(value.get("confidence"), (int, float))
            ]
            # Legacy two-engine agreement is candidate evidence only whenever
            # MTEF governance is active.  A final requires the structure source
            # or a hash-bound review from the audit database.
            can_use_legacy_final = mtef_mode == "off" and resolution["status"] == "machine_final"
            if not can_use_legacy_final:
                source_wmf = Path(out_dir) / wmf_ref
                source_hash = hashlib.sha256(source_wmf.read_bytes()).hexdigest() if source_wmf.is_file() else None
                review_tasks.append(
                    make_formula_review_task(wmf_name, png_path, record, source_sha256=source_hash)
                )
                wmf_pending_review += 1
            wmf_ocr_count += 1
            wmf_ocr_score += max(confidences, default=0.0)
        
        # Step 4: Backfill MD
        def replace_wmf(match):
            wmf_ref = match.group(1)
            audited = audit_replacements.get(wmf_ref)
            if audited:
                if audited["kind"] == "latex":
                    return f"${audited['value']}$"
                if audited["kind"] == "text":
                    return audited["value"]
                if audited["kind"] == "empty":
                    return ""
                return match.group(0)
            info = ocr_results.get(wmf_ref)
            resolution = info.get("resolution", {}) if info else {}
            if mtef_mode == "off" and resolution.get('status') == 'machine_final' and resolution.get('final_latex'):
                return f"${resolution['final_latex']}$"
            return match.group(0)
        
        md_content = re.sub(r'!\[图\]\((media/[^)]+\.wmf)\)', replace_wmf, md_content)
        md_content = md_content.replace('\xa0', ' ')
        
        # Save OCR results
        _atomic_json(Path(out_dir) / "formula_candidate_manifest.json", ocr_results)
        _atomic_text(
            Path(out_dir) / "formula_review_tasks.jsonl",
            "".join(json.dumps(task, ensure_ascii=False) + "\n" for task in review_tasks),
        )
    
    unresolved_refs = re.findall(r'!\[图\]\((media/[^)]+\.wmf)\)', md_content)
    wmf_pending_review = len(set(unresolved_refs))
    final_allowed = not unresolved_refs and (mtef_mode == "off" or (mtef_mode == "strict" and audit_ready))
    suffix = "_final.md" if final_allowed else "_draft.md"
    out_md = Path(out_dir) / os.path.basename(md_path).replace('.md', suffix)
    _atomic_text(out_md, md_content)
    completion = Path(out_dir) / "completion.json"
    if final_allowed:
        _atomic_json(
            completion,
            {
                "schema_version": 1,
                "status": "completion_ready",
                "source_sha256": hashlib.sha256(Path(docx_path).read_bytes()).hexdigest(),
                "markdown": str(out_md),
                "markdown_sha256": hashlib.sha256(out_md.read_bytes()).hexdigest(),
                "mtef_mode": mtef_mode,
            },
        )
    else:
        completion.unlink(missing_ok=True)
    
    t1 = time.time()
    
    return {
        'omml_eq': formula_count,
        'wmf_ocr': wmf_ocr_count,
        'wmf_pending_review': wmf_pending_review,
        'wmf_score': round(wmf_ocr_score / max(wmf_ocr_count, 1), 3) if wmf_ocr_count > 0 else None,
        'time': round(t1 - t0, 1),
        'status': 'final' if final_allowed else 'draft',
        'markdown': str(out_md),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", default=".")
    parser.add_argument("output", nargs="?", default="./output")
    parser.add_argument("--mtef-mode", choices=("shadow", "strict", "off"), default="shadow")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--task-id")
    resume = parser.add_mutually_exclusive_group()
    resume.add_argument("--resume", action="store_true", default=True)
    resume.add_argument("--rebuild", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_dir = args.input
    output_dir = args.output
    os.makedirs(output_dir, exist_ok=True)

    audit_payload = None
    audit_db = None
    audit_ready = args.mtef_mode == "off"
    if args.mtef_mode != "off":
        task_id = args.task_id or f"bemarkdown-{hashlib.sha256(str(Path(input_dir).resolve()).encode('utf-8')).hexdigest()[:12]}"
        audit_payload = run_audit(
            Path(input_dir),
            task_id,
            resume=args.resume,
            rebuild=args.rebuild,
        )
        audit_db = Path(audit_payload["state_root"]) / "formula_audit.sqlite3"
        audit_ready = bool((audit_payload.get("summary") or {}).get("ok"))
        if args.audit_only:
            print(json.dumps(audit_payload, ensure_ascii=False, indent=2))
            return 0 if audit_ready else 2
        if args.mtef_mode == "strict" and not audit_ready:
            payload = {"ok": False, "status": "blocked", "reason": "MTEF audit unresolved", "audit": audit_payload}
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 2
    
    # Find all DOCX files recursively
    docx_files = []
    for root, dirs, files in os.walk(input_dir):
        for f in sorted(files):
            if f.endswith('.docx'):
                docx_files.append(os.path.join(root, f))
    
    print(f"Found {len(docx_files)} DOCX files")
    
    # Convert each file
    stats = []
    for i, fpath in enumerate(docx_files):
        fname = os.path.basename(fpath)
        ftype = "解析" if "解析" in fname else "原卷"
        
        out_dir = os.path.join(output_dir, f"{i+1:02d}_{os.path.splitext(fname)[0][:30]}")
        
        try:
            result = convert_one_docx(
                fpath,
                out_dir,
                mtef_mode=args.mtef_mode,
                audit_db=audit_db,
                audit_ready=audit_ready,
            )
            result['idx'] = i + 1
            result['name'] = fname
            result['type'] = ftype
            stats.append(result)
            
            avg = f"{result['wmf_score']:.3f}" if result.get('wmf_score') else "-"
            print(f"  [{i+1:2d}/{len(docx_files)}] {fname[:40]:<40} OMML+EQ={result['omml_eq']:>3} WMF={result['wmf_ocr']:>3} pending={result['wmf_pending_review']:>3} ({avg}) {result['time']:.1f}s")
        except Exception as e:
            print(f"  [{i+1:2d}/{len(docx_files)}] {fname[:40]:<40} ❌ {e}")
            stats.append({'idx': i + 1, 'name': fname, 'type': ftype, 'error': str(e)})
    
    # Save summary
    _atomic_json(Path(output_dir) / "conversion_summary.json", stats)
    
    # Print totals
    total_omml = sum(s.get('omml_eq', 0) for s in stats if 'error' not in s)
    total_wmf = sum(s.get('wmf_ocr', 0) for s in stats if 'error' not in s)
    total_pending = sum(s.get('wmf_pending_review', 0) for s in stats if 'error' not in s)
    total_time = sum(s.get('time', 0) for s in stats if 'error' not in s)
    
    print(f"\n{'='*60}")
    print(f"Done: {len(docx_files)} files | OMML+EQ={total_omml} WMF_OCR={total_wmf} PendingReview={total_pending} Time={total_time:.1f}s")
    print(f"Summary: {os.path.join(output_dir, 'conversion_summary.json')}")
    if args.json:
        print(json.dumps({"ok": not any("error" in row for row in stats), "audit": audit_payload, "documents": stats}, ensure_ascii=False, indent=2))
    return 0 if not any("error" in row for row in stats) else 1


if __name__ == '__main__':
    raise SystemExit(main())
