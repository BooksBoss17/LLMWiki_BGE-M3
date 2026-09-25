import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path

from _runtime_paths import environment_python


def run_step(name, python_path, args, output_dir):
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
        "stdout_tail": proc.stdout[-6000:],
        "stderr_tail": proc.stderr[-6000:],
        "parsed": parsed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-root", required=True)
    parser.add_argument("--task-card", required=True)
    parser.add_argument("--profile", choices=["local"], default="local")
    parser.add_argument("--private-root", required=True)
    args = parser.parse_args()

    skill_root = Path(args.skill_root)
    task_card = Path(args.task_card)
    task = json.loads(task_card.read_text(encoding="utf-8"))
    task_id = task.get("task_id") or task_card.stem
    source = Path(task["source_image"])
    output_root = Path(task.get("output_root") or Path(args.private_root) / task_id)
    auto_root = output_root / "auto_ocr"
    auto_root.mkdir(parents=True, exist_ok=True)

    scripts_dir = skill_root / "scripts"
    cli_python = environment_python(skill_root, "cli")
    paddle_python = environment_python(skill_root, "paddle")
    formula_python = environment_python(skill_root, "formula")

    run_log = {
        "task_id": task_id,
        "source_image": str(source),
        "task_card": str(task_card),
        "output_root": str(auto_root),
        "created_at": datetime.now().astimezone().isoformat(),
        "profile": args.profile,
        "steps": [],
    }

    prepare = run_step(
        "prepare_auto_inputs",
        cli_python,
        [
            scripts_dir / "_auto_prepare_inputs.py",
            "--source-image",
            source,
            "--output-dir",
            auto_root,
        ],
        auto_root,
    )
    run_log["steps"].append(prepare)
    if prepare["exit_code"] != 0 or not isinstance(prepare.get("parsed"), dict):
        (auto_root / "auto_run_log.json").write_text(json.dumps(run_log, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": False, "error": "prepare_auto_inputs_failed", "output_root": str(auto_root)}, ensure_ascii=False, indent=2))
        return 1

    manifest_path = Path(prepare["parsed"]["manifest"])
    paddle_dir = auto_root / "paddle"
    paddle = run_step(
        "paddle_auto",
        paddle_python,
        [
            scripts_dir / "_engine_paddle_auto.py",
            "--manifest",
            manifest_path,
            "--output-dir",
            paddle_dir,
        ],
        paddle_dir,
    )
    run_log["steps"].append(paddle)
    paddle_result = paddle_dir / "paddle_auto_result.json"
    if not paddle_result.exists():
        (auto_root / "auto_run_log.json").write_text(json.dumps(run_log, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": False, "error": "paddle_auto_result_missing", "output_root": str(auto_root)}, ensure_ascii=False, indent=2))
        return 1

    formula_dir = auto_root / "formula"
    formula_args = [
        scripts_dir / "_engine_formula_auto.py",
        "--paddle-result",
        paddle_result,
        "--output-dir",
        formula_dir,
    ]
    formula = run_step("formula_auto", formula_python, formula_args, formula_dir)
    run_log["steps"].append(formula)
    formula_result = formula_dir / "formula_auto_result.json"
    if not formula_result.exists():
        formula_result.write_text(
            json.dumps(
                {
                    "engine": "formula_auto",
                    "ok": False,
                    "error": "formula_auto_result_missing",
                    "stdout_tail": formula["stdout_tail"],
                    "stderr_tail": formula["stderr_tail"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    rank = run_step(
        "auto_ranker",
        cli_python,
        [
            scripts_dir / "_auto_ranker.py",
            "--task-id",
            task_id,
            "--source-image",
            source,
            "--manifest",
            manifest_path,
            "--paddle-result",
            paddle_result,
            "--formula-result",
            formula_result,
            "--output-dir",
            auto_root,
            "--schemas",
            skill_root / "configs" / "question_schemas.yaml",
        ],
        auto_root,
    )
    run_log["steps"].append(rank)
    run_log["ok"] = rank["exit_code"] == 0
    (auto_root / "auto_run_log.json").write_text(json.dumps(run_log, ensure_ascii=False, indent=2), encoding="utf-8")

    transcript = auto_root / "auto_transcript.json"
    markdown = auto_root / "auto_transcript.md"
    print(
        json.dumps(
            {
                "ok": transcript.exists(),
                "auto_transcript": str(transcript),
                "markdown": str(markdown),
                "run_log": str(auto_root / "auto_run_log.json"),
                "output_root": str(auto_root),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if transcript.exists() else 1


if __name__ == "__main__":
    raise SystemExit(main())
