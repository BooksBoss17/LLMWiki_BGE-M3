import argparse
import json
import traceback
from pathlib import Path


def run_step(name, func):
    try:
        data = func()
        return {"name": name, "ok": True, "data": data}
    except Exception as exc:
        return {
            "name": name,
            "ok": False,
            "error": repr(exc),
            "traceback": traceback.format_exc(limit=8),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-image", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    source = Path(args.source_image)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"engine": "formula", "source_image": str(source), "steps": []}

    def texteller_whole_page():
        from _texteller_runtime import infer_formula

        result = infer_formula(source)
        (out_dir / "texteller_result.txt").write_text(result["latex"], encoding="utf-8")
        return result

    report["steps"].append(run_step("texteller_whole_page", texteller_whole_page))

    report["ok"] = all(step["ok"] for step in report["steps"])
    (out_dir / "formula_result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
