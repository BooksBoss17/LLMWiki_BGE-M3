# Image-Only / Scanned PDF OCR Workflow

Use this reference when a PDF may be scanned, image-only, mixed text/image, or formula-fragmented. Typical cases include exam papers exported as full-page images and answer PDFs with a text layer but broken formulas.

## Entry Rules

1. Run diagnosis before conversion:

```bash
python skills/import/bemarkdown/scripts/diagnose_pdf.py <file.pdf> --json
```

2. Interpret `classification`:

| classification | Meaning | Default path |
|---|---|---|
| `image_pdf` | Pages have little/no text and full-page image coverage | `combine_image_pdf_ocr.py` |
| `mixed_pdf` | Text layer and image blocks both matter, or formula-risk markers exist | `combine_image_pdf_ocr.py` |
| `text_pdf` | Text is mostly complete and formulas are readable | `pymupdf4llm` path in `SKILL.md` |

If `summary.formula_risk=true`, use combined OCR even when the PDF has a text layer.

## Combined OCR Default

Run the orchestrator for image-only exams, scanned papers, mixed answer PDFs, or formula-risk pages:

```bash
python skills/import/bemarkdown/scripts/combine_image_pdf_ocr.py <file.pdf> <output_dir> --json
```

The orchestrator renders pages, collects marker as a document/layout candidate, collects local OCR workbench candidates when requested, writes `conflict_blocks.jsonl`, and creates `vlm_review_tasks.jsonl` for only the risky blocks. It does not declare the Markdown import-ready until `final_quality_report.json.ok_for_import=true`.

## Marker Candidate / Quick Smoke

Run the wrapper instead of calling `marker` directly:

```bash
python skills/import/bemarkdown/scripts/convert_pdf_with_marker.py <file.pdf> <output_dir> --json
```

The wrapper:

- finds `marker.exe`, preferring the Python310 install used on this machine;
- verifies marker Python import `marker`;
- verifies `torch.cuda.is_available()` is true and refuses slow CPU conversion;
- detects the current marker CLI force-OCR flag, including `--PdfProvider_force_ocr`;
- copies the source PDF to an ASCII staging name under `<output_dir>/marker_input/source.pdf`;
- writes `conversion_summary.json` with diagnosis, environment, command result, output Markdown paths, and `picture intentionally omitted` count.

Use `--page-range 0-1` for a small smoke test. Marker page ranges are zero-based. For image-only exams, marker output is a candidate source; do not send marker Markdown directly to exercise pairing/import.

## Local OCR/VLM Fallback

Use `references/local-vlm-ocr-fallback.md` through `combine_image_pdf_ocr.py` when marker or text-layer output is nonempty but not trustworthy. Typical triggers:

- blank or incomplete pages after marker;
- formula fragmentation, missing subscripts/vectors/fractions/radicals;
- handwriting, student answers, or hand-drawn derivations;
- tables, captions, or figure-adjacent text that marker garbles.

Render only the risky pages or crop regions, then hand those images to `import/chinese-handwriting-formula-transcriber` with ASCII task ids and UTF-8 task cards. BeMarkdown keeps the final Markdown merge and quality gates; the transcriber owns PaddleOCR-VL, PP-OCRv6, PP-FormulaNet, TexTeller, and private OCR outputs. Accept `machine_final` only after visual agreement; review `machine_candidate`; treat `machine_abstain` as unresolved. Local candidates must return to the combined OCR conflict/quality report before downstream import.

## General VLM Fallback

Use `scripts/pdf_text_to_md.py` only when marker plus local OCR/VLM still misses pages, corrupts formulas, or a few pages need exact reconstruction. It renders pages for a VLM pass; it is not the primary converter.

```bash
python skills/import/bemarkdown/scripts/pdf_text_to_md.py <file.pdf> <render_output_dir> 3
```

Ask the VLM to transcribe text and formulas line-by-line, use LaTeX for formulas, and preserve physical diagram image references.

## Quality Gates

- Diagnosis JSON exists and records page count, text characters, image coverage, and classification.
- Combined OCR outputs `candidate_manifest.json`, `merged_draft.md`, `conflict_blocks.jsonl`, `vlm_review_tasks.jsonl`, and `final_quality_report.json`.
- Marker conversion uses CUDA when marker is run; CPU marker conversion is not acceptable for production import.
- Output Markdown is nonempty and page count is plausible for the source.
- `picture intentionally omitted` count is 0, each occurrence resolved by local OCR/VLM, VLM, or manual recovery.
- `final_quality_report.json.ok_for_import=true`, `issue_count=0`, and `unresolved_conflict_count=0` before exercise-bank pairing.
- No empty OCR placeholders, guessed formulas, or missing answer sections remain before exercise-bank pairing.
- For exam papers with separate answer PDFs, convert the paper and answer PDF separately, then pair questions from two Markdown outputs.
