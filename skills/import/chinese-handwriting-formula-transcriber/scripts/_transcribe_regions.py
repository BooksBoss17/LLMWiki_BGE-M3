import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path

from _runtime_paths import environment_python


def run_python(name, python_path, args, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [str(python_path), *[str(arg) for arg in args]],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    (output_dir / f"{name}_stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (output_dir / f"{name}_stderr.txt").write_text(proc.stderr, encoding="utf-8")
    parsed = None
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except Exception:
            parsed = None
    return {
        "name": name,
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout[-8000:],
        "stderr_tail": proc.stderr[-8000:],
        "parsed": parsed,
    }


def clean_texteller(value):
    if not isinstance(value, dict) or value.get("status") != "ok":
        return ""
    return str(value.get("latex") or "").strip()


def region_by_id(report):
    if not isinstance(report, dict):
        return {}
    return {region["id"]: region for region in report.get("regions", [])}


def write_markdown(path, result):
    paddle_regions = region_by_id(result.get("paddle_regions"))
    formula_regions = region_by_id(result.get("formula_regions"))
    lines = [
        f"# 区域 OCR 摘要：{result['task_id']}",
        "",
        f"- 源图像：`{result['source_image']}`",
        f"- 输出根目录：`{result['output_root']}`",
        f"- 创建时间：`{result['created_at']}`",
        "",
    ]
    for region in result["regions"]:
        rid = region["id"]
        paddle = paddle_regions.get(rid, {})
        formula = formula_regions.get(rid, {})
        ocr = paddle.get("paddlex_ocr") or {}
        texts = [str(text) for text in (ocr.get("texts") or []) if str(text).strip()]
        formulanet = paddle.get("pp_formulanet") or {}
        formulas = [str(text) for text in (formulanet.get("formulas") or []) if str(text).strip()]
        texteller = clean_texteller(formula.get("texteller"))
        lines.extend(
            [
                f"## Q{region['label']}",
                "",
                f"- 裁剪图：`{region['path']}`",
                f"- 边界框：`{region['bbox']}`",
                f"- Paddle 成功：`{paddle.get('ok')}`；TexTeller 成功：`{formula.get('ok')}`",
                "",
                "### PaddleX 文本",
                "",
            ]
        )
        if texts:
            lines.extend([f"- {text}" for text in texts[:80]])
        else:
            lines.append("- 无文本")
        lines.extend(["", "### PP-FormulaNet", ""])
        if formulas:
            lines.extend(["```latex", "\n\n".join(formulas[:4])[:3000], "```"])
        else:
            lines.append("- 无公式")
        lines.extend(["", "### TexTeller", "", "```latex", texteller[:3000], "```", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-root", required=True)
    parser.add_argument("--task-card", required=True)
    parser.add_argument("--profile", choices=["full", "paddle", "formula"], default="full")
    parser.add_argument("--private-root", required=True)
    args = parser.parse_args()

    skill_root = Path(args.skill_root)
    task_card = Path(args.task_card)
    task = json.loads(task_card.read_text(encoding="utf-8"))
    task_id = task.get("task_id") or task_card.stem
    source = Path(task["source_image"])
    output_root = Path(task.get("output_root") or Path(args.private_root) / task_id)
    regions_root = output_root / "region_ocr"
    regions_root.mkdir(parents=True, exist_ok=True)

    scripts_dir = skill_root / "scripts"
    cli_python = environment_python(skill_root, "cli")
    paddle_python = environment_python(skill_root, "paddle")
    formula_python = environment_python(skill_root, "formula")

    crop_run = run_python(
        "crop",
        cli_python,
        [
            scripts_dir / "_crop_answer_regions.py",
            "--source-image",
            source,
            "--output-dir",
            regions_root,
        ],
        regions_root,
    )
    if crop_run["exit_code"] != 0 or not isinstance(crop_run.get("parsed"), dict):
        raise RuntimeError(f"裁剪失败：{crop_run['stderr_tail'] or crop_run['stdout_tail']}")
    manifest_path = Path(crop_run["parsed"]["manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    engine_runs = []
    paddle_report = None
    formula_report = None

    if args.profile in ("full", "paddle"):
        paddle_run = run_python(
            "paddle_regions",
            paddle_python,
            [
                scripts_dir / "_engine_paddle_regions.py",
                "--manifest",
                manifest_path,
                "--output-dir",
                regions_root / "paddle",
            ],
            regions_root / "paddle",
        )
        engine_runs.append(paddle_run)
        result_path = regions_root / "paddle" / "paddle_regions_result.json"
        if result_path.exists():
            paddle_report = json.loads(result_path.read_text(encoding="utf-8"))

    if args.profile in ("full", "formula"):
        formula_run = run_python(
            "formula_regions",
            formula_python,
            [
                scripts_dir / "_engine_formula_regions.py",
                "--manifest",
                manifest_path,
                "--output-dir",
                regions_root / "formula",
            ],
            regions_root / "formula",
        )
        engine_runs.append(formula_run)
        result_path = regions_root / "formula" / "formula_regions_result.json"
        if result_path.exists():
            formula_report = json.loads(result_path.read_text(encoding="utf-8"))

    result = {
        "task_id": task_id,
        "source_image": str(source),
        "task_card": str(task_card),
        "output_root": str(output_root),
        "created_at": datetime.now().astimezone().isoformat(),
        "regions_manifest": str(manifest_path),
        "regions": manifest["regions"],
        "crop_run": crop_run,
        "engine_runs": engine_runs,
        "paddle_regions": paddle_report,
        "formula_regions": formula_report,
        "ok": any(
            isinstance(report, dict) and report.get("ok")
            for report in (paddle_report, formula_report)
        ),
    }

    combined_path = regions_root / "region_combined_result.json"
    combined_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(regions_root / "region_summary.md", result)
    print(
        json.dumps(
            {"ok": result["ok"], "combined_result": str(combined_path), "output_root": str(regions_root)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
