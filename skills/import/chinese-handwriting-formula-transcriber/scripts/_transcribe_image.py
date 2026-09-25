import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

from _runtime_paths import environment_python


def run_engine(name, python_path, script_path, source_image, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            str(python_path),
            str(script_path),
            "--source-image",
            str(source_image),
            "--output-dir",
            str(output_dir),
        ],
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
    if parsed is None:
        result_path = output_dir / f"{name}_result.json"
        if result_path.exists():
            try:
                parsed = json.loads(result_path.read_text(encoding="utf-8"))
            except Exception:
                parsed = None
    return {
        "engine": name,
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout[-8000:],
        "stderr_tail": proc.stderr[-8000:],
        "parsed": parsed,
    }


def write_markdown(path, result):
    lines = [
        f"# OCR 冒烟测试摘要：{result['task_id']}",
        "",
        f"- 源图像：`{result['source_image']}`",
        f"- 输出根目录：`{result['output_root']}`",
        f"- 创建时间：`{result['created_at']}`",
        "",
        "## 图像",
        "",
        f"- 尺寸：{result['image'].get('width')} x {result['image'].get('height')}",
        f"- 模式：{result['image'].get('mode')}",
        "",
        "## 引擎",
        "",
    ]
    for engine in result["engines"]:
        parsed_ok = None
        if isinstance(engine.get("parsed"), dict):
            parsed_ok = engine["parsed"].get("ok")
        lines.extend(
            [
                f"### {engine['engine']}",
                "",
                f"- 退出码：`{engine['exit_code']}`",
                f"- 解析成功：`{parsed_ok}`",
                "",
            ]
        )
        if engine.get("stderr_tail"):
            lines.extend(["```text", engine["stderr_tail"][-2000:], "```", ""])
    if result["warnings"]:
        lines.extend(["## 警告", ""])
        lines.extend([f"- {warning}" for warning in result["warnings"]])
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
    if not source.exists():
        raise FileNotFoundError(source)

    output_root = Path(task.get("output_root") or Path(args.private_root) / task_id)
    output_root.mkdir(parents=True, exist_ok=True)

    with Image.open(source) as img:
        image_info = {
            "width": img.width,
            "height": img.height,
            "mode": img.mode,
            "format": img.format,
        }

    result = {
        "task_id": task_id,
        "source_image": str(source),
        "task_card": str(task_card),
        "output_root": str(output_root),
        "created_at": datetime.now().astimezone().isoformat(),
        "image": image_info,
        "profile": args.profile,
        "engines": [],
        "warnings": [],
    }

    scripts_dir = skill_root / "scripts"
    if args.profile in ("full", "paddle"):
        paddle_python = environment_python(skill_root, "paddle")
        if paddle_python.exists():
            result["engines"].append(
                run_engine(
                    "paddle",
                    paddle_python,
                    scripts_dir / "_engine_paddle_transcribe.py",
                    source,
                    output_root / "paddle",
                )
            )
        else:
            result["warnings"].append("缺少 paddle venv")

    if args.profile in ("full", "formula"):
        formula_python = environment_python(skill_root, "formula")
        if formula_python.exists():
            result["engines"].append(
                run_engine(
                    "formula",
                    formula_python,
                    scripts_dir / "_engine_formula_transcribe.py",
                    source,
                    output_root / "formula",
                )
            )
        else:
            result["warnings"].append("缺少 formula venv")

    any_ok = False
    for engine in result["engines"]:
        parsed = engine.get("parsed")
        if isinstance(parsed, dict) and parsed.get("ok"):
            any_ok = True
    if not any_ok:
        result["warnings"].append("没有引擎报告 ok=true")

    combined_path = output_root / "combined_result.json"
    combined_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(output_root / "summary.md", result)
    print(json.dumps({"ok": any_ok, "combined_result": str(combined_path), "output_root": str(output_root)}, ensure_ascii=False, indent=2))
    return 0 if any_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
