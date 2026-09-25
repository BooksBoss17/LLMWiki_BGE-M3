"""Batch-review WMF PNGs with PP-FormulaNet and TexTeller.

Prerequisites:
  - shared PP-FormulaNet and TexTeller runtimes resolved by formula_ensemble.py
  - WMF files already rendered to PNG via wmf_to_png.py (LibreOffice)

Usage:
    python batch_ocr_wmf.py <md_path> <wmf_png_dir> <output_md_path>

The script:
1. Finds all ![图](media/xxx.wmf) references in the MD
2. OCRs each corresponding PNG with the shared two-engine formula ensemble
3. Replaces only consensus-accepted WMF references with $LaTeX$
4. Saves OCR results as JSON for review
"""
import sys
import os
import re
import time
import json
from pathlib import Path

import hashlib

from formula_candidate_utils import make_formula_review_task
from formula_ensemble import run_formula_ensemble


def batch_ocr_wmf(md_path, wmf_png_dir, output_md_path):
    """Batch OCR WMF images and replace references in MD with LaTeX."""
    t0 = time.time()

    # Read MD
    with open(md_path, 'r', encoding='utf-8') as f:
        md_content = f.read()

    # Find all WMF image references
    wmf_refs = re.findall(r'!\[图\]\((media/[^)]+\.wmf)\)', md_content)
    print(f"\nFound {len(wmf_refs)} WMF references in MD")

    # OCR each unique WMF file
    wmf_to_latex = {}
    review_tasks = []
    unique_wmf_files = sorted(set(wmf_refs))

    png_paths = []
    ref_by_png = {}
    for i, wmf_ref in enumerate(unique_wmf_files):
        wmf_name = os.path.basename(wmf_ref)
        png_name = wmf_name.replace('.wmf', '.png')
        png_path = os.path.join(wmf_png_dir, png_name)

        if not os.path.exists(png_path):
            print(f"  [{i+1}/{len(unique_wmf_files)}] {wmf_name}: PNG not found")
            wmf_to_latex[wmf_ref] = None
            continue

        resolved = str(Path(png_path).resolve())
        png_paths.append(resolved)
        ref_by_png[resolved] = wmf_ref

    ensemble = run_formula_ensemble(png_paths, Path(output_md_path).parent / "formula_ensemble") if png_paths else {"records": []}
    records = {record["source_image"]: record for record in ensemble.get("records", [])}
    for png_path, wmf_ref in ref_by_png.items():
        record = records.get(png_path)
        wmf_to_latex[wmf_ref] = record
        if not record:
            continue
        resolution = record["resolution"]
        if resolution["status"] != "machine_final":
            source = Path(md_path).parent / wmf_ref
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None
            review_tasks.append(
                make_formula_review_task(Path(wmf_ref).name, png_path, record, source_sha256=source_hash)
            )
        display = str(resolution.get("final_latex") or "needs VLM")
        print(f"  {Path(wmf_ref).name}: {display}")

    # Replace WMF references in MD
    def replace_wmf_ref(match):
        wmf_ref = match.group(1)
        info = wmf_to_latex.get(wmf_ref)
        resolution = info.get("resolution", {}) if info else {}
        if resolution.get('status') == 'machine_final' and resolution.get('final_latex'):
            return f'${resolution["final_latex"]}$'
        else:
            return match.group(0)  # Keep as image ref if OCR failed

    new_md = re.sub(r'!\[图\]\((media/[^)]+\.wmf)\)', replace_wmf_ref, md_content)

    # Save
    with open(output_md_path, 'w', encoding='utf-8') as f:
        f.write(new_md)

    # Stats
    success = sum(1 for v in wmf_to_latex.values() if v and v.get('candidates'))
    total = len(unique_wmf_files)
    scores = [
        float(candidate["confidence"])
        for value in wmf_to_latex.values() if value
        for candidate in value.get("candidates", [])
        if isinstance(candidate.get("confidence"), (int, float))
    ]
    avg_score = sum(scores) / max(len(scores), 1)

    print(f"\n{'='*60}")
    print(f"OCR Complete: {success}/{total} WMF files recognized")
    print(f"Average score: {avg_score:.3f}")
    print(f"Output: {output_md_path}")
    print(f"MD size: {os.path.getsize(md_path)/1024:.1f}KB -> {os.path.getsize(output_md_path)/1024:.1f}KB")

    # Save OCR results as JSON
    ocr_json_path = os.path.join(os.path.dirname(output_md_path), 'wmf_ocr_results.json')
    ocr_data = {ref: info or {'candidates': [], 'resolution': {'status': 'machine_abstain', 'risk_flags': ['missing_png_or_ocr_failure']}} for ref, info in wmf_to_latex.items()}
    with open(ocr_json_path, 'w', encoding='utf-8') as f:
        json.dump(ocr_data, f, ensure_ascii=False, indent=2)
    review_path = os.path.join(os.path.dirname(output_md_path), 'formula_review_tasks.jsonl')
    with open(review_path, 'w', encoding='utf-8') as f:
        for task in review_tasks:
            f.write(json.dumps(task, ensure_ascii=False) + '\n')
    print(f"OCR results saved: {ocr_json_path}")
    print(f"Formula review tasks saved: {review_path} ({len(review_tasks)} unresolved)")

    return success, total, avg_score


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print("Usage: python batch_ocr_wmf.py <md_path> <wmf_png_dir> <output_md_path>")
        sys.exit(1)
    batch_ocr_wmf(sys.argv[1], sys.argv[2], sys.argv[3])
