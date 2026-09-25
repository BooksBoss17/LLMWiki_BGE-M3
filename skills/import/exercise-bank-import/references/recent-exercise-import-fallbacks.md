# Recent exercise-bank import fallbacks

Use this reference when importing mixed DOC/DOCX/PDF exercise sheets where OCR, equation conversion, or LLM extraction may be brittle.

## Durable workflow lessons

1. **Cross-convert DOC/DOCX when question stems are image-heavy**
   - Run the normal BeMarkdown DOC/DOCX conversion first to preserve OMML/EQ/WMF-to-LaTeX results and media.
   - If the resulting Markdown shows many bare `![图](...)` lines or few question-number anchors, export the DOC/DOCX to PDF with LibreOffice and run marker/OCR on that PDF too.
   - Use the OCR PDF Markdown as the main question-stem source and the BeMarkdown output as backup for formulas/images. This catches image-only or badly segmented question stems.

2. **Pairing JSON before writing raw questions**
   - For each source file, write a `paired_json/<source_key>.json` array before generating `raw/exercises` files.
   - Required fields: `question_no`, `question_type`, `question_text`, `answer`, `explanation`, `knowledge_points`, `category_path`, `difficulty`, `difficulty_reason`.
   - Validate every JSON file for count, first/last question number, empty answer, empty explanation, and malformed JSON before assigning global IDs.

3. **If LLM/subagent extraction times out, do not block the import indefinitely**
   - Retry once with a smaller, more explicit prompt.
   - If it still times out, directly parse the source Markdown in the main flow for that file, especially when the answer table and question ranges are visible.
   - Keep the same paired JSON schema so the downstream writer remains deterministic.

4. **Guard against partial writes**
   - Generate raw files only after all paired JSONs are present.
   - If the writer fails mid-run, remove any IDs/assets from the new ID range before rerunning to avoid duplicate or orphaned media.
   - Track an `imported_manifest.json` with start/end IDs, written files, and missing assets.

5. **Missing answers are not allowed to remain as final state**
   - If the original answer table truly omits a problem, inspect the problem image/text and solve it or use VLM assistance to determine the answer and explanation.
   - Record in notes that the original answer area was missing and the answer was recovered by analysis.
   - Only use `无详解（原件仅提供答案）` when an answer exists but the source genuinely has no explanation.

6. **Final checks for a batch**
   - Three loops over new IDs only: frontmatter, required sections, question length, answer non-empty, explanation non-empty or accepted `无详解（原件仅提供答案）`, no `.wmf`, balanced `$`, all `media/...` images exist.
   - Then update a Wiki import index page and log entry before rebuilding RAG.
