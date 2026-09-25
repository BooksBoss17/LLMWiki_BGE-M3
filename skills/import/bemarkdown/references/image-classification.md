# Image Classification in DOCX → Markdown Pipeline

## Problem

After mammoth extracts images from DOCX, each image needs to be classified:
- **Formula/symbol** → convert to LaTeX text ($...$)
- **Blank placeholder** → remove
- **Diagram/illustration** → keep as `![](media/xxx.png)`

## Classification Methods (in order of reliability)

### 1. Alt text presence (most reliable)

Word documents created with equation editors store the original LaTeX/OMML
in the image's alt text. mammoth preserves this as `![latex](media/xxx.png)`.

- **Has alt text** → formula (97% confidence). Replace with `$alt$`.
- **No alt text** → could be a diagram OR a formula that lost its metadata.
  Needs further classification (see below).

### 2. File size heuristic (fast, imperfect)

- `< 1KB` and no alt → blank placeholder, remove
- `> 5KB` and no alt → likely a diagram, keep
- `1-5KB` and no alt → ambiguous, needs VLM check

### 3. ⚠️ Transparency check (UNRELIABLE — do NOT use alone)

**Transparency (alpha channel) is NOT a reliable indicator of formula vs diagram.**

Real-world test on 19 images without alt text:
- 2 transparent (RGBA, >10% transparent pixels) → both were physical apparatus diagrams
- 17 opaque → all were diagrams/coordinate graphs
- 0 were formulas

Transparent diagrams occur because Word crops illustrations from their
background when exporting, producing RGBA PNGs with transparency.

### 4. VLM spot-check (gold standard, use sparingly)

For images without alt text, use vision_analyze on 3-5 sample images to
determine the content type before batch processing.

Prompt: "这是物理试卷中提取的图片。请描述内容：是公式、符号、文字、还是示意图？"

- If samples are all diagrams → keep all remaining as images
- If samples include formulas → generate structured PP-FormulaNet/TexTeller candidates and review risky blocks with agent VLM
- If mixed → classify each individually (costly but accurate)

Do not promote one formula engine's text solely because it is available. Use the shared candidate status contract: only simple high-confidence `machine_final` may be filled automatically; `machine_candidate` and `machine_abstain` must retain the source image and create a review task.

## Real-world benchmark

Test: Physics exam DOCX (578KB + 736KB), 598 total images.

| Category | Count | Method used | Cost |
|----------|-------|-------------|------|
| Formula (has alt) | 579 | Alt text → LaTeX | Zero (text replacement) |
| Blank placeholder | 2 | Size < 1KB, no alt | Zero |
| Diagram (no alt) | 17 | VLM spot-check (5 samples) | 5 VLM calls |
| Diagram (no alt) | 2 | VLM spot-check | (included above) |
| **Misclassified** | **0** | — | — |

Key takeaway: 95% of images had alt text and were converted with zero
model calls. The remaining 5% were all diagrams, verified with 5 VLM calls
total. No OCR was needed for this document type.
