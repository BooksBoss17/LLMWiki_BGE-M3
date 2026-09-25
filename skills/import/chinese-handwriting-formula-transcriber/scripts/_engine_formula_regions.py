import argparse
import json
import traceback
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    report = {
        "engine": "formula_regions",
        "manifest": str(manifest_path),
        "regions": [],
        "ok": False,
    }

    try:
        from _texteller_runtime import infer_formula
    except Exception as exc:
        report["error"] = repr(exc)
        report["traceback"] = traceback.format_exc(limit=8)
        result_path = out_dir / "formula_regions_result.json"
        result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    for region in manifest["regions"]:
        image_path = Path(region["path"])
        entry = {
            "id": region["id"],
            "label": region["label"],
            "bbox": region["bbox"],
            "path": str(image_path),
            "texteller": {"status": "not_run"},
            "ok": False,
        }
        try:
            entry["texteller"] = {"status": "ok", **infer_formula(image_path)}
            entry["ok"] = True
        except Exception as exc:
            entry["error"] = repr(exc)
            entry["traceback"] = traceback.format_exc(limit=8)
        report["regions"].append(entry)
    report["ok"] = any(region.get("ok") for region in report["regions"])

    result_path = out_dir / "formula_regions_result.json"
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
