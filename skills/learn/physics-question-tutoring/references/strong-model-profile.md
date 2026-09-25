# Role D Strong Model Profile

Use this reference only when `--model-profile strong` is explicitly selected.
The machine-readable capability contract is
`../../_shared/references/model-profiles.json`.

## Vision review

Run local OCR first. Show the strong model only the EXIF-free
`source_sanitized.png`, then write this exact object:

```json
{
  "schema_version": 1,
  "source_sha256": "original source hash",
  "sanitized_sha256": "source_sanitized.png hash",
  "review_status": "agree|partial|conflict|insufficient",
  "confidence": 0.0,
  "stem_candidate": "candidate text or null",
  "formula_candidates": ["F=ma"],
  "diagram_objects": [{"type": "arrow", "label": "F", "confidence": 0.9}],
  "conflicts": [{"field": "stem.value", "reason": "OCR 与图像复核不一致"}],
  "uncertain_items": [{"field": "diagram.direction", "reason": "箭头端点不清"}]
}
```

Do not silently replace OCR fields. An OCR/vision conflict remains blocked until
the user confirms the intended question. If OCR has no text, the vision result
may become a candidate, but it cannot become confirmed automatically.

## Staged response

Write one JSON object with `schema_version`, `stage`, `action`, `answer_kind`,
and `sections`. `action` must equal the current stage. The required section
names come from the stage contract.

For a numeric solution, also include:

```json
{
  "calculation_check": {
    "expression": "6/2",
    "claimed_value": 3,
    "unit": "m/s^2",
    "retry_count": 0
  }
}
```

The expression may contain only numeric literals, parentheses, `+`, `-`, `*`,
`/`, and bounded `**`. Run `validate_role_d_response.py` before showing the
answer. On `retry_required`, correct the response once and set `retry_count=1`.
On a second mismatch, stop and request confirmation.

## Strong reasoning

- Retrieve standard Wiki/RAG evidence first; use deep retrieval only when the
  initial evidence is insufficient or the task is a curator cross-check.
- Compare a second method only when it materially checks the first solution.
- Treat the second model pass as review, not deterministic proof.
- Preserve every student/curator permission boundary from the weak profile.
