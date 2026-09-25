import argparse
import json
import traceback
from pathlib import Path

from PIL import Image


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


def run_step(name, func):
    try:
        data = func()
        return {"name": name, "ok": True, "data": to_jsonable(data)}
    except Exception as exc:
        return {
            "name": name,
            "ok": False,
            "error": repr(exc),
            "traceback": traceback.format_exc(limit=8),
        }


def construct_with_optional_device(cls, device, **kwargs):
    try:
        return cls(device=device, **kwargs)
    except TypeError:
        return cls(**kwargs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-image", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--device", default="gpu:0")
    args = parser.parse_args()

    source = Path(args.source_image)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"engine": "paddle", "source_image": str(source), "steps": []}

    with Image.open(source) as img:
        report["image"] = {
            "width": img.width,
            "height": img.height,
            "mode": img.mode,
            "format": img.format,
        }

    def paddleocr_vl():
        from paddleocr import PaddleOCRVL

        save_dir = out_dir / "paddleocr_vl"
        save_dir.mkdir(parents=True, exist_ok=True)
        pipeline = PaddleOCRVL(pipeline_version="v1.6")
        output = pipeline.predict(str(source))
        results = []
        for res in output:
            try:
                res.save_to_json(save_path=str(save_dir))
            except Exception:
                pass
            try:
                res.save_to_markdown(save_path=str(save_dir))
            except Exception:
                pass
            results.append(to_jsonable(res))
        return results

    report["steps"].append(run_step("paddleocr_vl_1_6_page", paddleocr_vl))

    def paddlex_ocr():
        from paddlex import create_pipeline

        save_dir = out_dir / "paddlex_ocr"
        save_dir.mkdir(parents=True, exist_ok=True)
        pipeline = create_pipeline(pipeline="OCR", device=args.device)
        output = pipeline.predict(
            input=str(source),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        results = []
        for res in output:
            try:
                res.save_to_json(save_path=str(save_dir))
            except Exception:
                pass
            results.append(to_jsonable(res))
        return results

    report["steps"].append(run_step("paddlex_ocr_page", paddlex_ocr))

    def pp_formulanet_whole_page():
        from paddleocr import FormulaRecognition

        model = construct_with_optional_device(
            FormulaRecognition,
            args.device,
            model_name="PP-FormulaNet_plus-L",
        )
        output = model.predict(input=str(source), batch_size=1)
        return [to_jsonable(item) for item in output]

    report["steps"].append(run_step("pp_formulanet_plus_l_whole_page", pp_formulanet_whole_page))
    report["ok"] = any(step["ok"] for step in report["steps"])
    (out_dir / "paddle_result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
