# Image Semantic Audit for Exercise Bank / 题库图片语义审查

Use this reference when importing or maintaining `raw/exercises/**/media`.

Canonical incident: `MA0001332` required a Maxwell molecular
speed-distribution graph, but the stored image was only the section heading
`二、非选择题：`. The correct repair was to return to the source conversion
output, restore the graph, re-check answer/explanation, and then validate to
`0 issues`.

## Goal

Image-link validation is not enough. The retained image must be the
physics-bearing diagram, graph, photo, option figure, or apparatus. Text,
formula, heading, and option screenshots should be converted to Markdown or
LaTeX whenever recoverable.

## Keep As Image

Retain image references only when the image carries visual physics information:

- physical schematic / apparatus diagram
- coordinate graph / curve / p-V or p-T graph / waveform
- force diagram / vector diagram
- circuit diagram / transformer schematic
- particle trajectory / field diagram
- graphical multiple-choice option
- necessary real-world context photo

## Convert To Text Or LaTeX

Do not keep images for:

- multiple-choice option text
- question stem fragments or section headings
- ordinary text lines
- isolated punctuation, digits, brackets, or row fragments
- formula screenshots recoverable as LaTeX

Preferred replacements:

- text screenshots -> editable Markdown text
- formula screenshots -> `$LaTeX$`
- low-confidence formula OCR -> inspect original DOCX/PDF or use VLM before replacing

## High-Priority Suspect Heuristics

Flag image references for manual review when any hold:

- `height <= 45px` and `width >= 180px`, often text-line screenshots
- small file size combined with tiny dimensions
- extreme aspect ratio, especially wide/short crops
- nearby context contains `如图|图中|图像|曲线|装置|轨迹|电路`, but the referenced image is tiny or flat
- alt text or OCR output indicates the image is only formula/text

Treat these as triage signals, not automatic deletion rules. Many small
waveforms, force diagrams, and graphical options are legitimate.

## Transparent PNG / Alpha Black Risk

Some diagrams are transparent PNGs with black lines and a transparent
background. They look correct when composited on white, but can render as
black-on-black in viewers or downstream pipelines that do not handle alpha as
expected. This is a blocking readability issue even when the link is valid.

Run `scripts/audit_exercise_images.py` after image edits. It flags
`alpha_black_risk` when all hold:

- image has alpha (`RGBA`, `LA`, or palette transparency)
- direct grayscale mean `< 45`
- white-background composite mean `> 160`
- transparent-pixel fraction `> 0.15`

Repair options:

- Prefer `--fix-alpha-black` for low-risk transparent PNGs. It composites the
  image on white and overwrites only the image file; Markdown is unchanged.
- If the image content remains ambiguous, return to the original DOCX/PDF or
  BeMarkdown output and confirm the real diagram.
- Do not classify the black direct render as a valid dark physics image without
  checking the white composite or original source.

`dark_risk` is separate: it means a non-alpha image is globally very dark. It
must be 0, or the audit handoff must include a manual review note explaining why
the image is legitimate.

`suspects.json` is not a deletion list. Build a contact sheet and manually
review small/flat images.

## Safe Workflow

1. Build inventory from body-only image references `![](...)`; do not scan YAML
   frontmatter as body content.
2. Assert baseline: missing links, data URI, WMF refs/files, image-open errors.
3. Apply low-risk text/formula replacements first.
4. Run `audit_exercise_images.py`; review `issues.json`,
   `alpha_black_risk.json`, `dark_risk.json`, and `suspects.json`.
5. For high-priority suspects, create a contact sheet and inspect manually or
   with VLM.
6. If an image is removed from a `如图` context, verify whether the true diagram
   is now missing. If yes, return to the original DOCX/PDF/BeMarkdown output and
   restore the correct diagram.
7. Update frontmatter `assets` exactly to match actual body `media/...`
   references.
8. Run final full validation:
   - 0 missing images
   - 0 data URI
   - 0 WMF refs/files
   - 0 image-open errors
   - 0 alpha-black-risk images
   - 0 unexplained dark-risk images
   - 0 high-priority text-image residuals
   - 0 `assets` mismatch
9. Update Wiki audit/log and rebuild RAG when the raw exercise bank changed.

## Typical Repair Patterns

### Text Option Screenshot

Before:

```markdown
![](media/_page_1_Picture_27.jpeg)
- B. 电压表的示数为 400 V
```

After:

```markdown
- A. 电压表的示数为 200 V
- B. 电压表的示数为 400 V
```

Remove the stale image from `assets`.

### Wrong Heading Image When Diagram Is Required

If a question says it uses a graph/diagram but the stored image is a heading or
text strip, do not merely delete the image. Locate the correct source image in
the original DOCX/PDF or conversion output, replace the bad image, and re-check
answer/explanation if labels or line styles affect correctness.

### `如图` After Removing Text Image

If deleting a text screenshot leaves the question still saying `如图所示` with
no image, treat the task as incomplete. Restore the true diagram from the source
document, or rewrite the stem only when the original genuinely had no diagram.

## Notes

- Never scan frontmatter as if it were body content; regex over YAML `assets:`
  causes false positives.
- `assets` is metadata, not proof. Recompute it from actual body image
  references after media edits.
- Contact-sheet review is useful because many false positives are legitimate
  small waveforms, force diagrams, or graphical options.
