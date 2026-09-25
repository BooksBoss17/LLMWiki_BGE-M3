# bemarkdown card

- status: active
- category: import
- role: role-a-import-maintenance
- canonical_skill: `import/bemarkdown/SKILL.md`
- manifest: `import/bemarkdown/manifest.yaml`
- description: Convert DOCX/PDF source files to clean Markdown, including standards-based OMML/EQ, strict Equation OLE/MTEF v3/v5 to MathML/KB-LaTeX, WMF hash-chain and three-source review, plus combined image/scanned PDF OCR. Unknown structures and unresolved visual evidence block final Markdown.
- triggers: DOCX, PDF, 图片型PDF, 扫描PDF, PDF图片转写, 图片型PDF组合OCR, 组合OCR, 组合转写, VLM复核任务, OMML, EQ, WMF, MTEF, MathType, Equation OLE, OCR, 本地OCR兜底, Markdown转换
- use_when: source file conversion, DOCX Markdown, PDF Markdown, scanned/image-only PDF combined OCR, formula/image OCR cleanup, marker candidate correction, VLM review task generation, BeMarkdown pipeline
- do_not_use_when: importing exercises into exercise bank, generating lectures, exporting existing Markdown Word, answering teaching questions
- input: DOCX, DOC, PDF, source document needing Markdown conversion
- output: draft/final Markdown, media, OMML/EQ report, MTEF SQLite/hash audit and completion, hash-bound formula review tasks, reviewed finals, combined PDF candidate/conflict/VLM reports
- next_skills: import/exercise-bank-import/SKILL.md, import/chinese-handwriting-formula-transcriber/SKILL.md, import/textbook-import/SKILL.md, import/standards-import/SKILL.md

## Load rule

Read card via `registry.yaml` routing. On match, read only `import/bemarkdown/SKILL.md` first. Read manifest/scripts/references/runtime only when loaded skill asks for them.
