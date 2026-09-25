import argparse
import contextlib
import importlib
import io
import json
import platform
import sys


def json_scalar(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def check_import(name: str):
    try:
        module = importlib.import_module(name)
        return {
            "ok": True,
            "version": json_scalar(getattr(module, "__version__", None)),
        }
    except Exception as exc:
        return {"ok": False, "error": repr(exc)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True, choices=["cli", "paddle", "formula"])
    args = parser.parse_args()

    result = {
        "ok": True,
        "python": sys.executable,
        "python_version": platform.python_version(),
        "env": args.env,
        "imports": {},
    }

    if args.env == "cli":
        modules = ["PIL", "cv2", "numpy", "yaml", "rich", "huggingface_hub"]
    elif args.env == "paddle":
        modules = ["paddle", "paddleocr", "paddlex", "PIL", "numpy"]
    else:
        modules = ["texteller", "onnxruntime", "PIL", "numpy"]

    for module in modules:
        result["imports"][module] = check_import(module)

    if args.env == "paddle" and result["imports"]["paddle"]["ok"]:
        try:
            import paddle

            result["paddle"] = {
                "version": paddle.__version__,
                "compiled_with_cuda": bool(paddle.device.is_compiled_with_cuda()),
                "device_count": int(paddle.device.cuda.device_count())
                if paddle.device.is_compiled_with_cuda()
                else 0,
            }
            try:
                stream = io.StringIO()
                with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                    paddle.utils.run_check()
                result["paddle"]["run_check"] = True
                result["paddle"]["run_check_log_tail"] = stream.getvalue()[-2000:]
            except Exception as exc:
                result["paddle"]["run_check"] = False
                result["paddle"]["run_check_error"] = repr(exc)
        except Exception as exc:
            result["paddle"] = {"error": repr(exc)}

    if args.env == "formula" and result["imports"].get("onnxruntime", {}).get("ok"):
        try:
            import onnxruntime as ort

            result["onnxruntime"] = {
                "providers": ort.get_available_providers(),
                "gpu_provider_available": "CUDAExecutionProvider" in ort.get_available_providers(),
            }
        except Exception as exc:
            result["onnxruntime"] = {"error": repr(exc)}

    failed = [k for k, v in result["imports"].items() if not v["ok"]]
    if failed:
        result["ok"] = False
        result["failed_imports"] = failed

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
