# Question Parse Contract

`question_pipeline.py` writes `question_parse.json` with these stable fields:

```json
{
  "schema_version": 1,
  "session_id": "demo-001",
  "source": {"kind": "image", "sha256": "...", "sanitized_copy": "...", "sanitized_sha256": "..."},
  "status": "confirmed|needs_confirmation|needs_ocr",
  "model_profile": "weak|strong",
  "confidence": 0.0,
  "stem": "题干",
  "options": [{"label": "A", "text": "..."}],
  "latex_formulas": ["F=ma"],
  "diagram_objects": [{"type": "arrow", "label": "F", "confidence": 0.9}],
  "vision_review": null,
  "unconfirmed_items": [{"field": "diagram.direction", "reason": "箭头模糊"}],
  "confirmed_by_user": false,
  "confirmation_basis_sha256": "..."
}
```

## OCR adapter input

For image-only inputs, pass `--ocr-json <file>`. The adapter JSON may contain
`text`, `confidence`, `formulas`, `diagram_objects`, and `uncertain_items`.
The script also accepts a sibling `<source>.ocr.json`. No adapter result means
`needs_ocr`; this is intentional fail-closed behavior.

With `--model-profile strong`, image input also requires a hash-bound
`--vision-review-json`. The strong model may review only `sanitized_copy`.
Changing the source, OCR adapter, model profile, or vision review changes
`confirmation_basis_sha256` and invalidates a previous confirmation.

## Stage contract

- `confirm`: may expose parsed text and uncertainties only.
- `hint`: `response_constraints.reveal_final_answer` is always `false`.
- `plan`: may expose laws and steps, but not a computed final answer.
- `solution`: allowed only after `--confirmed` or a previously confirmed parse.
