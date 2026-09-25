# Combined Image PDF OCR Workflow

Use this reference for image-only, scanned, or formula-risk PDFs when a single
OCR engine is not reliable enough for import-grade Markdown.

## Core Flow

1. Diagnose the PDF with `diagnose_pdf.py`.
2. Render selected pages to images.
3. Collect marker OCR as a document-level/layout candidate.
4. Collect local OCR candidates through the transcriber structured adapter:
   - Whole-page: PaddleOCR-VL, PP-OCRv6/PaddleX, PP-FormulaNet, TexTeller。
   - Region/line: `region_combined_result.json`, `paddle_regions_result.json`, `formula_regions_result.json`.
   - Auto transcript: `auto_transcript.json` only when the task is explicitly an answer-card or clear-region workflow.
5. Run `combine_image_pdf_ocr.py` to write candidates, conflict files, VLM tasks, and the per-wave quality report.
6. Use agent VLM only on `vlm_review_tasks.jsonl`.
7. Re-run combination with VLM result JSONL when conflicts are reviewed.
8. For multi-wave jobs, rebuild the wave summary from each wave's final `final_quality_report.json`.
9. Enter exercise pairing/import only when final reports and final wave summary are import-ready.

For deployment-only marker/CUDA preflight, run `python skills/import/bemarkdown/scripts/convert_pdf_with_marker.py --check-only --json`; no PDF or output directory is required.

## Commands

Run one wave:

```bash
python skills/import/bemarkdown/scripts/combine_image_pdf_ocr.py <file.pdf> <output_dir> --page-range 0-1 --run-local-transcriber --local-page-limit 1 --json
```

Useful options:

- `--marker-output-dir <dir>`: reuse an existing marker conversion directory.
- `--local-output-root PAGE=PATH`: reuse an existing private OCR run, where PAGE is 1-based.
- `--run-local-transcriber`: create task cards and call the local OCR workbench for rendered pages.
- `--local-page-limit N`: cap expensive local OCR runs during smoke tests.
- `--transcriber-command TEMPLATE`: use a platform-neutral command template containing `{task_id}` instead of assuming PowerShell.
- `--transcriber-dir DIR`: override the transcriber skill directory; the `BEMARKDOWN_TRANSCRIBER_DIR` environment variable is the equivalent deployment setting.
- `--private-ocr-root DIR`: override private model output storage; the `BEMARKDOWN_PRIVATE_OCR_ROOT` environment variable is the equivalent deployment setting.
- `--vlm-results <jsonl>`: apply agent VLM review results keyed by `block_id`.
- `--no-vlm-tasks`: skip task generation when only collecting candidates.

After VLM review and rerun, rebuild multi-wave summaries from final reports:

```bash
python skills/import/bemarkdown/scripts/summarize_combined_ocr_waves.py <combined_wave_root> --out ../ocr_combined_wave_summary_after_vlm.json --json
```

## Outputs

- `candidate_manifest.json`: diagnosis, rendered page paths, marker summary, local OCR run roots, `engine_status`, `model_candidate_matrix`, and candidate metadata.
- `merged_draft.md`: best-effort draft; not import-ready unless the quality report says so.
- `conflict_blocks.jsonl`: unresolved blocks requiring visual review.
- `vlm_review_tasks.jsonl`: page/block images and candidate paths for agent VLM review.
- `final_quality_report.json`: per-wave import gate.
- `ocr_combined_wave_summary_after_vlm.json`: multi-wave gate rebuilt from final per-wave reports.

## Candidate Contract

Every non-marker local candidate should be represented with:

- `engine`: `paddleocr_vl`, `pp_ocr`, `pp_formulanet`, `texteller` 或 `auto_transcript`。
- `page`: 1-based page number.
- `region_id`: optional region/line id.
- `source_kind`: whole-page, region, line, formula, or auto-transcript source.
- `text`: candidate Markdown/text/LaTeX.
- `status`: `machine_final`, `machine_candidate`, or `machine_abstain`.
- `confidence` and `quality_score`: optional numeric model/ranker scores.
- `risk_flags`: formula, low-confidence, template, hallucination, or parser warnings.
- `path` and `metadata`: evidence file and source-specific context.

`model_candidate_matrix` groups these by page and engine. It is the main quick-read surface for the controller agent; do not rely on chat summaries of model runs.

## Status Semantics

- `machine_final`: strong machine evidence, still allowed to be checked by VLM when formulas/diagrams are involved.
- `machine_candidate`: usable candidate only after visual or agent VLM review.
- `machine_abstain`: unresolved blocker; keep the page/block unresolved.

## Merge Rules

- Do not trust marker final text for image-only exams; use it as layout and image-anchor evidence.
- Treat structured `combined_result.json` and region JSON outputs as the formal interface. Fixed-directory `.md` and `.txt` scans are compatibility fallbacks only when the corresponding structured engine candidate is absent.
- 优先采用结构化的 PaddleOCR-VL 文本；公式密集块在视觉复核前始终保持 unresolved。
- Treat PP-OCR line text as evidence for Chinese text boundaries, not final Markdown structure.
- Treat PP-FormulaNet and TexTeller outputs as formula evidence; do not insert them into final Markdown without visual agreement.
- Agent VLM reviews only conflict blocks, especially nuclear notation, vectors, fractions, roots, graph options, tables, and experiment diagrams.
- If VLM cannot verify the source image, keep the block unresolved; do not guess.

## Import Gate

Before handing output to `exercise-bank-import`:

- Every wave `final_quality_report.json.ok_for_import` is true.
- Every wave `final_quality_report.json.issue_count` is 0.
- Every wave `unresolved_conflict_count` is 0.
- Every wave `final_quality_report.json.model_candidate_matrix_present` is true.
- Every image/formula-risk page has either resolved model disagreement or an explicit visual review note.
- The multi-wave summary has been rebuilt from final `final_quality_report.json` files; no stale first-pass wave status remains.
- Image links are not broken.
- No `picture intentionally omitted` remains.
- Formula or diagram conflicts have VLM evidence or manual visual confirmation.

Run three quality passes before downstream import:

1. Structure pass: question numbers continuous, page order correct, and choice/fill/experiment/calculation section boundaries sensible.
2. Content pass: no empty stems, obvious OCR garbage, formula fragments, or guessed text remains.
3. Visual pass: image links valid; stems containing `如图`、`图像`、`装置`、`电路` keep the correct figure; nuclear notation, vectors, fractions, roots, unit exponents, plus/minus signs, tables, and graph options have visual evidence.

If any pass finds an issue, keep the related conflict unresolved and do not proceed to exercise pairing.
