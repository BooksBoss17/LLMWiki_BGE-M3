import argparse
import json
import os
import traceback
from pathlib import Path

from _portable_image import load_rgb_array


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
    parser.add_argument("--sample-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--device", default="gpu:0")
    args = parser.parse_args()

    sample_dir = Path(args.sample_dir)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    text_img = sample_dir / "synthetic_chinese_text.png"
    formula_img = sample_dir / "synthetic_formula.png"
    page_img = sample_dir / "synthetic_page.png"

    report = {"engine": "paddle", "steps": []}

    def paddle_run_check():
        import paddle

        paddle.utils.run_check()
        cudnn_build = str(paddle.version.cudnn() or "")
        cudnn_runtime = int(paddle.device.get_cudnn_version() or 0)
        build_parts = [int(part) for part in cudnn_build.split(".") if part.isdigit()]
        while len(build_parts) < 3:
            build_parts.append(0)
        cudnn_build_code = build_parts[0] * 10000 + build_parts[1] * 100 + build_parts[2]
        return {
            "version": paddle.__version__,
            "compiled_with_cuda": bool(paddle.device.is_compiled_with_cuda()),
            "device_count": int(paddle.device.cuda.device_count())
            if paddle.device.is_compiled_with_cuda()
            else 0,
            "cuda_build": str(paddle.version.cuda() or ""),
            "cudnn_build": cudnn_build,
            "cudnn_runtime": cudnn_runtime,
            "cudnn_match": cudnn_build_code == cudnn_runtime,
        }

    report["steps"].append(run_step("paddle_run_check", paddle_run_check))

    def pp_ocrv6_text_recognition():
        from paddleocr import TextRecognition

        model = construct_with_optional_device(
            TextRecognition,
            args.device,
            model_name="PP-OCRv6_medium_rec",
        )
        output = model.predict(input=load_rgb_array(text_img), batch_size=1)
        return [to_jsonable(item) for item in output]

    report["steps"].append(run_step("pp_ocrv6_medium_rec", pp_ocrv6_text_recognition))

    def pp_ocrv6_text_detection():
        from paddleocr import TextDetection

        model = construct_with_optional_device(
            TextDetection,
            args.device,
            model_name="PP-OCRv6_medium_det",
        )
        output = model.predict(input=load_rgb_array(text_img), batch_size=1)
        return [to_jsonable(item) for item in output]

    report["steps"].append(run_step("pp_ocrv6_medium_det", pp_ocrv6_text_detection))

    def pp_formulanet():
        from paddleocr import FormulaRecognition

        model = construct_with_optional_device(
            FormulaRecognition,
            args.device,
            model_name="PP-FormulaNet_plus-L",
        )
        output = model.predict(input=load_rgb_array(formula_img), batch_size=1)
        return [to_jsonable(item) for item in output]

    report["steps"].append(run_step("pp_formulanet_plus_l", pp_formulanet))

    def pp_doclayoutv3():
        from paddleocr import LayoutDetection

        model = construct_with_optional_device(
            LayoutDetection,
            args.device,
            model_name="PP-DocLayoutV3",
        )
        output = model.predict(input=load_rgb_array(page_img), batch_size=1)
        return [to_jsonable(item) for item in output]

    report["steps"].append(run_step("pp_doclayoutv3", pp_doclayoutv3))

    def paddleocr_vl():
        from paddleocr import PaddleOCRVL

        cache_root = Path(os.environ["PADDLE_PDX_CACHE_HOME"]) / "official_models"
        pipeline = PaddleOCRVL(
            pipeline_version="v1.6",
            layout_detection_model_name="PP-DocLayoutV3",
            layout_detection_model_dir=str(cache_root / "PP-DocLayoutV3"),
            # 公开仓库名称为 PaddleOCR-VL-1.6，而导出的
            # PaddleX 推理元数据会标识具体的 0.9B 模型。
            vl_rec_model_name="PaddleOCR-VL-1.6-0.9B",
            vl_rec_model_dir=str(cache_root / "PaddleOCR-VL-1.6"),
            device=args.device,
        )
        output = pipeline.predict(load_rgb_array(page_img))
        results = []
        for res in output:
            results.append(to_jsonable(res))
        return results

    report["steps"].append(run_step("paddleocr_vl_1_6", paddleocr_vl))

    def paddle_gpu_peak():
        import paddle

        props = paddle.device.cuda.get_device_properties(0)
        return {
            "device": args.device,
            "name": props.name,
            "total_memory_bytes": int(props.total_memory),
            "peak_allocated_bytes": int(paddle.device.cuda.max_memory_allocated()),
            "peak_reserved_bytes": int(paddle.device.cuda.max_memory_reserved()),
        }

    report["steps"].append(run_step("paddle_gpu_peak", paddle_gpu_peak))
    report["ok"] = all(step["ok"] for step in report["steps"])
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
