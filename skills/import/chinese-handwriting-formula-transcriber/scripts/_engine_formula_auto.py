import argparse
import json
import traceback
from pathlib import Path


def run_texteller(image_path):
    from _texteller_runtime import infer_formula

    result = infer_formula(image_path)
    return {"status": "ok", **result}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paddle-result", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--skip-texteller", action="store_true")
    parser.add_argument("--max-texteller-lines", type=int, default=6)
    args = parser.parse_args()

    paddle_result_path = Path(args.paddle_result)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paddle = json.loads(paddle_result_path.read_text(encoding="utf-8"))

    report = {
        "engine": "formula_auto",
        "paddle_result": str(paddle_result_path),
        "regions": [],
        "ok": False,
        "texteller_enabled": not args.skip_texteller,
    }

    texteller_count = 0
    for region in paddle.get("regions", []):
        region_entry = {"id": region["id"], "label": region["label"], "lines": [], "ok": False}
        for line in region.get("lines", []):
            if not line.get("run_formula_engines"):
                continue
            image_path = Path(line["path"])
            entry = {
                "line_id": line["line_id"],
                "path": str(image_path),
                "texteller": {"status": "not_run", "reason": "max_texteller_lines"},
                "ok": False,
            }
            if args.skip_texteller:
                entry["texteller"] = {"status": "not_run", "reason": "explicitly_skipped"}
            elif texteller_count < args.max_texteller_lines:
                try:
                    entry["texteller"] = run_texteller(image_path)
                    texteller_count += 1
                    entry["ok"] = True
                except Exception as exc:
                    entry["error"] = repr(exc)
                    entry["traceback"] = traceback.format_exc(limit=8)
            region_entry["lines"].append(entry)
        region_entry["ok"] = any(line.get("ok") for line in region_entry["lines"])
        report["regions"].append(region_entry)
    report["ok"] = any(region.get("ok") for region in report["regions"])

    result_path = out_dir / "formula_auto_result.json"
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
