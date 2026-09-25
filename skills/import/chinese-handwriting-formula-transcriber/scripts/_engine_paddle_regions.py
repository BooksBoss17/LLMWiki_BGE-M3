import argparse
import json
import traceback
from pathlib import Path


def to_jsonable(value):
    if hasattr(value, "json"):
        try:
            return value.json
        except Exception:
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    try:
        import numpy as np

        if isinstance(value, np.ndarray):
            return value.tolist()
    except Exception:
        pass
    return repr(value)


def construct_with_optional_device(cls, device, **kwargs):
    try:
        return cls(device=device, **kwargs)
    except TypeError:
        return cls(**kwargs)


def run_region_ocr(pipeline, image_path):
    output = pipeline.predict(
        input=str(image_path),
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    converted = [to_jsonable(item) for item in output]
    texts = []
    scores = []
    for item in converted:
        data = item.get("res", item) if isinstance(item, dict) else {}
        texts.extend(data.get("rec_texts") or [])
        scores.extend(data.get("rec_scores") or [])
    return {"raw": converted, "texts": texts, "scores": scores}


def run_region_formula(model, image_path):
    output = model.predict(input=str(image_path), batch_size=1)
    converted = [to_jsonable(item) for item in output]
    formulas = []
    for item in converted:
        data = item.get("res", item) if isinstance(item, dict) else {}
        formula = data.get("rec_formula")
        if formula:
            formulas.append(formula)
    return {"raw": converted, "formulas": formulas}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--device", default="gpu:0")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    report = {
        "engine": "paddle_regions",
        "manifest": str(manifest_path),
        "regions": [],
        "ok": False,
    }

    try:
        from paddlex import create_pipeline
        from paddleocr import FormulaRecognition

        ocr_pipeline = create_pipeline(pipeline="OCR", device=args.device)
        formula_model = construct_with_optional_device(
            FormulaRecognition,
            args.device,
            model_name="PP-FormulaNet_plus-L",
        )
        for region in manifest["regions"]:
            image_path = Path(region["path"])
            entry = {
                "id": region["id"],
                "label": region["label"],
                "bbox": region["bbox"],
                "path": str(image_path),
                "paddlex_ocr": None,
                "pp_formulanet": None,
                "ok": False,
            }
            try:
                entry["paddlex_ocr"] = run_region_ocr(ocr_pipeline, image_path)
                entry["pp_formulanet"] = run_region_formula(formula_model, image_path)
                entry["ok"] = True
            except Exception as exc:
                entry["error"] = repr(exc)
                entry["traceback"] = traceback.format_exc(limit=8)
            report["regions"].append(entry)
        report["ok"] = any(region.get("ok") for region in report["regions"])
    except Exception as exc:
        report["error"] = repr(exc)
        report["traceback"] = traceback.format_exc(limit=8)

    result_path = out_dir / "paddle_regions_result.json"
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
