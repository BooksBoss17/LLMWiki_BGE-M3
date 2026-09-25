import argparse
import json
import re
import traceback
from pathlib import Path


SHORT_ANSWER_IDS = {"q09", "q10", "q11", "q12", "q13"}
FORMULA_CHARS = set("=+-*/^_()[]{}<>≤≥≈∴∵√∑∫πθαβγΔΩμ")
TEMPLATE_WORDS = ("满分", "答题", "班级", "姓名", "座号", "缺考", "考场")


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


def text_features(text):
    text = text or ""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    digits = len(re.findall(r"\d", text))
    formula = sum(1 for char in text if char in FORMULA_CHARS)
    template = any(word in text for word in TEMPLATE_WORDS)
    return {"cjk": cjk, "latin": latin, "digits": digits, "formula": formula, "template": template}


def classify_line(region_id, text, stats):
    ink_ratio = float((stats or {}).get("ink_ratio") or 0.0)
    if ink_ratio < 0.002:
        return "empty", False
    if region_id in SHORT_ANSWER_IDS:
        # 短答题优先按文本/选择/填空处理；公式模型在此类区域噪声过大。
        return "text", False
    features = text_features(text)
    if features["template"] and features["formula"] < 2:
        return "text", False
    formula_score = features["formula"] + min(features["latin"], 8) * 0.35 + min(features["digits"], 8) * 0.18
    if formula_score >= 2.2 and features["cjk"] >= 2:
        return "mixed", True
    if formula_score >= 1.5:
        return "formula", True
    return "text", False


def run_ocr(pipeline, image_path):
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
    return {"raw": converted, "texts": texts, "scores": scores, "joined_text": " ".join(map(str, texts)).strip()}


def run_formula(model, image_path):
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
        "engine": "paddle_auto",
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
            region_entry = {
                "id": region["id"],
                "label": region["label"],
                "region_type": region["region_type"],
                "lines": [],
                "ok": False,
            }
            for line in region["lines"]:
                line_entry = {
                    "line_id": line["line_id"],
                    "bbox": line["bbox"],
                    "path": line["path"],
                    "stats": line["stats"],
                    "paddle_text": None,
                    "line_type": "unknown",
                    "run_formula_engines": False,
                    "pp_formulanet": None,
                    "ok": False,
                }
                try:
                    text_result = run_ocr(ocr_pipeline, Path(line["path"]))
                    line_entry["paddle_text"] = text_result
                    line_type, should_run_formula = classify_line(region["id"], text_result["joined_text"], line["stats"])
                    line_entry["line_type"] = line_type
                    line_entry["run_formula_engines"] = should_run_formula
                    if should_run_formula:
                        line_entry["pp_formulanet"] = run_formula(formula_model, Path(line["path"]))
                    line_entry["ok"] = True
                except Exception as exc:
                    line_entry["error"] = repr(exc)
                    line_entry["traceback"] = traceback.format_exc(limit=8)
                region_entry["lines"].append(line_entry)
            region_entry["ok"] = any(line.get("ok") for line in region_entry["lines"])
            report["regions"].append(region_entry)
        report["ok"] = any(region.get("ok") for region in report["regions"])
    except Exception as exc:
        report["error"] = repr(exc)
        report["traceback"] = traceback.format_exc(limit=8)

    result_path = out_dir / "paddle_auto_result.json"
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
