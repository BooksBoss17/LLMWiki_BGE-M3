# Local OCR/VLM Fallback For Image Documents

Use this reference when BeMarkdown needs local OCR candidates for risky PDF pages or crop regions. For image-only, scanned, mixed, or formula-risk PDFs, `combine_image_pdf_ocr.py` is the orchestrator; this file describes the low-level branch that calls `import/chinese-handwriting-formula-transcriber`.

Do not copy the transcriber skill's models, venvs, or private outputs into BeMarkdown.

## Boundary

- BeMarkdown owns document diagnosis, PDF rendering, marker candidate collection, Markdown merge, conflict files, and final import-quality gates.
- `chinese-handwriting-formula-transcriber` owns PaddleOCR-VL-1.6, PP-OCRv6, PP-FormulaNet_plus-L、TexTeller 的编排、保守候选排序和私有 OCR 输出。
- Local OCR fallback produces evidence and correction candidates. It does not automatically bless content for raw/Wiki/RAG import.
- Real student answer-card images and OCR logs must stay under the transcriber private root. Do not copy real crops, OCR logs, or transcripts into `skills/`.

## Output Reading Contract

BeMarkdown must read structured JSON first and only scan Markdown/TXT as a compatibility fallback.

Formal interfaces:

- Whole-page smoke: `<output_root>/combined_result.json`, plus engine details in `paddle/paddle_result.json` and `formula/formula_result.json`.
- Region diagnostics: `<output_root>/region_ocr/region_combined_result.json`, `paddle/paddle_regions_result.json`, and `formula/formula_regions_result.json`.
- Auto transcript: `<output_root>/auto_ocr/auto_transcript.json`, only for answer-card or explicitly region-structured runs.

Compatibility-only files:

- `paddle/paddleocr_vl/*.md`
- `formula/texteller_result.txt`
- `summary.md`, `region_summary.md`, `auto_transcript.md`

Map outputs into the BeMarkdown candidate contract:

- `PaddleOCR-VL` -> whole-page layout/text candidate.
- `PP-OCRv6/PaddleX` -> text-line evidence, not final Markdown structure.
- `PP-FormulaNet_plus-L` -> formula LaTeX evidence.
- `TexTeller` -> handwritten/mixed formula comparison evidence.
- `auto_transcript.json` -> `machine_final`, `machine_candidate`, or `machine_abstain` candidates according to its own status fields.

Never promote `machine_candidate` or `machine_abstain` to import-ready text inside BeMarkdown. They must be represented in `conflict_blocks.jsonl` and resolved by visual review or agent VLM.

The transcriber location and private output root are deployment settings, not hard-coded machine contracts. Use `--transcriber-dir` / `BEMARKDOWN_TRANSCRIBER_DIR`, `--private-ocr-root` / `BEMARKDOWN_PRIVATE_OCR_ROOT`, or a platform-neutral `--transcriber-command` template containing `{task_id}`. Wrapper discovery may use `pwsh`, Windows PowerShell, or `POWERSHELL_EXE`; agents on other platforms should supply an explicit command template.

## Trigger Conditions

Enter this branch from combined OCR when any condition is true:

- marker or text-layer output has empty pages, missing question numbers, missing answer sections, or unresolved `picture intentionally omitted`;
- typed formulas are visibly fragmented, especially vectors, subscripts, radicals, integrals, fractions, or unit exponents;
- the page contains handwritten notes, student answer-card regions, or hand-drawn derivations;
- question images, tables, captions, or figure-adjacent text are garbled;
- quality review needs a second local model opinion before manual or agent VLM correction.

Do not use local OCR first for ordinary text PDFs. For clean text PDFs, use pymupdf4llm. For image-only exams, run combined OCR and keep local OCR limited to pages or regions that need another candidate.

## Preflight

When entering this branch, read:

- `../chinese-handwriting-formula-transcriber/SKILL.md`
- `../chinese-handwriting-formula-transcriber/references/architecture.md`

Then run:

```powershell
.\skills\import\chinese-handwriting-formula-transcriber\scripts\doctor.ps1
```

If environment models are missing, use the transcriber setup flow:

```powershell
.\skills\import\chinese-handwriting-formula-transcriber\scripts\setup_env.ps1
.\skills\import\chinese-handwriting-formula-transcriber\scripts\download_models.ps1 -Scope full
```

In Codex Windows sessions, avoid Chinese path literals in inline shell. Put real paths inside UTF-8 JSON task cards and pass only ASCII task ids.

## Page Or Region Handoff

1. Render risky PDF pages or crop regions from BeMarkdown combined OCR output to images.
2. Use ASCII task ids, for example `exam_final_p03_formula_20260709`.
3. Create a UTF-8 task card under the transcriber task-card root:

```json
{
  "task_id": "exam_final_p03_formula_20260709",
  "source_image": "C:/absolute/path/to/page_or_region.png",
  "output_root": "%USERPROFILE%/Desktop/ocr_private_runs/chinese_handwriting_formula_transcriber/<task-id>",
  "notes": "BeMarkdown local OCR/VLM fallback for page 3 formula block"
}
```

4. Run a whole-image smoke pass:

```powershell
.\skills\import\chinese-handwriting-formula-transcriber\scripts\transcribe_image.ps1 -TaskId exam_final_p03_formula_20260709
```

5. For answer-card layouts or clear question boundaries, run:

```powershell
.\skills\import\chinese-handwriting-formula-transcriber\scripts\transcribe_auto.ps1 -TaskId exam_final_p03_formula_20260709 -Profile local
```

6. For low-level diagnostics, run:

```powershell
.\skills\import\chinese-handwriting-formula-transcriber\scripts\transcribe_regions.ps1 -TaskId exam_final_p03_formula_20260709
```

## Merge Rules

- Use `machine_final` only when the source image and model output agree line-by-line.
- Treat `machine_candidate` as a correction candidate requiring visual review against the rendered page.
- Treat `machine_abstain`, engine errors, and conflicting formula outputs as unresolved.
- Preserve physics meaning. Do not silently improve a formula or derivation beyond what appears in the source.
- Record page/region ids, task ids, and status in `candidate_manifest.json`, `conflict_blocks.jsonl`, or `final_quality_report.json`.

## Quality Gates Before Downstream Import

- All risky pages are either resolved with explicit correction evidence or reported as unresolved blockers.
- No OCR placeholders, guessed formulas, empty page stubs, or `picture intentionally omitted` remain.
- Formulas, units, subscripts, vectors, radicals, fractions, and signs are checked visually.
- Tables, circuit/graph/experiment diagrams, and image anchors still match the source.
- Real student OCR artifacts remain in the private run root, not in `raw/`, `LLMWiki/`, `BGE-M3`, or `skills/`.
