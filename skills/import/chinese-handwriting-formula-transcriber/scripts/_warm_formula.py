import argparse
import json
import os
import traceback
from pathlib import Path

from _portable_image import load_rgb_array


def to_jsonable(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return str(value)


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    sample_dir = Path(args.sample_dir)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    formula_img = sample_dir / "synthetic_formula.png"
    report = {"engine": "formula", "steps": []}

    def imports():
        import onnxruntime as ort
        import texteller

        return {
            "texteller": to_jsonable(getattr(texteller, "__version__", None)),
            "onnxruntime_providers": ort.get_available_providers(),
        }

    report["steps"].append(run_step("imports", imports))

    def texteller_api():
        import torch
        from texteller import img2latex, load_model, load_tokenizer

        runtime_root = Path(os.environ["LLMWIKI_MODEL_RUNTIME_ROOT"])
        model_dir = runtime_root / "models" / "huggingface" / "OleehyO" / "TexTeller"
        model = load_model(model_dir=str(model_dir))
        tokenizer = load_tokenizer(tokenizer_dir=str(model_dir))
        prediction = img2latex(model, tokenizer, [load_rgb_array(formula_img)], out_format="latex")[0]
        if not prediction.strip():
            raise RuntimeError("TexTeller 返回了空公式")
        return {
            "device": str(model.device),
            "torch_cuda_available": bool(torch.cuda.is_available()),
            "latex": prediction,
        }

    report["steps"].append(run_step("texteller_inference", texteller_api))

    report["ok"] = all(step["ok"] for step in report["steps"])
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
