#!/usr/bin/env python
"""下载官方模型快照，并将 PaddleX 模型预热到共享缓存。"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from model_runtime import environment_variables, runtime_root


def apply_environment() -> None:
    for name, value in environment_variables().items():
        os.environ[name] = value
        Path(value).mkdir(parents=True, exist_ok=True) if name.endswith(("HOME", "CACHE")) else None


def run(name: str, func) -> dict[str, Any]:
    try:
        data = func()
        return {"name": name, "ok": True, "data": data}
    except Exception as exc:
        return {"name": name, "ok": False, "error": repr(exc), "traceback": traceback.format_exc(limit=8)}


def jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if hasattr(value, "json"):
        try:
            return value.json
        except Exception:
            pass
    return repr(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="下载官方模型快照并预热 PaddleX 官方模型。")
    parser.add_argument("--scope", choices=["paddle", "all"], default="paddle", help="下载和预热范围。")
    parser.add_argument("--device", default="gpu:0", help="Paddle 推理设备。")
    parser.add_argument("--write-lock", action="store_true", help="将下载结果写入运行库锁定文件。")
    args = parser.parse_args()
    apply_environment()

    root = runtime_root()
    models = root / "models"
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.executable,
        "python_version": platform.python_version(),
        "device": args.device,
        "steps": [],
        "revisions": {},
    }

    def download_vl() -> dict[str, Any]:
        from huggingface_hub import HfApi, snapshot_download

        repo_id = "PaddlePaddle/PaddleOCR-VL-1.6"
        info = HfApi().model_info(repo_id)
        target = models / "paddlex" / "official_models" / "PaddleOCR-VL-1.6"
        if not (target / "model.safetensors").exists():
            snapshot_download(repo_id=repo_id, revision=info.sha, local_dir=target)
        report["revisions"][repo_id] = info.sha
        return {"repo_id": repo_id, "revision": info.sha, "path": str(target)}

    report["steps"].append(run("paddleocr_vl_1_6_snapshot", download_vl))

    def paddle_check() -> dict[str, Any]:
        import paddle

        paddle.utils.run_check()
        return {
            "version": paddle.__version__,
            "compiled_with_cuda": bool(paddle.device.is_compiled_with_cuda()),
            "cuda_device_count": int(paddle.device.cuda.device_count()) if paddle.device.is_compiled_with_cuda() else 0,
        }

    report["steps"].append(run("paddle_gpu", paddle_check))

    def warm_model(class_name: str, model_name: str) -> dict[str, Any]:
        import paddleocr

        cls = getattr(paddleocr, class_name)
        try:
            instance = cls(model_name=model_name, device=args.device)
        except TypeError:
            instance = cls(model_name=model_name)
        return {"class": class_name, "model_name": model_name, "instance": repr(instance)}

    for step_name, class_name, model_name in [
        ("pp_ocrv6_medium_det", "TextDetection", "PP-OCRv6_medium_det"),
        ("pp_ocrv6_medium_rec", "TextRecognition", "PP-OCRv6_medium_rec"),
        ("pp_formulanet_plus_l", "FormulaRecognition", "PP-FormulaNet_plus-L"),
        ("pp_doclayoutv3", "LayoutDetection", "PP-DocLayoutV3"),
    ]:
        report["steps"].append(run(step_name, lambda c=class_name, m=model_name: warm_model(c, m)))

    def construct_full_vl() -> dict[str, Any]:
        from paddleocr import PaddleOCRVL

        layout_dir = models / "paddlex" / "official_models" / "PP-DocLayoutV3"
        vl_dir = models / "paddlex" / "official_models" / "PaddleOCR-VL-1.6"
        pipeline = PaddleOCRVL(
            pipeline_version="v1.6",
            layout_detection_model_name="PP-DocLayoutV3",
            layout_detection_model_dir=str(layout_dir),
            vl_rec_model_name="PaddleOCR-VL-1.6-0.9B",
            vl_rec_model_dir=str(vl_dir),
            device=args.device,
        )
        return {"pipeline": repr(pipeline), "layout_dir": str(layout_dir), "vl_dir": str(vl_dir)}

    report["steps"].append(run("paddleocr_vl_1_6_pipeline", construct_full_vl))
    report["ok"] = all(step["ok"] for step in report["steps"])
    report = jsonable(report)
    target = root / "download-lock.json"
    if args.write_lock:
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
