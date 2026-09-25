#!/usr/bin/env python
"""运行脱敏的 OCR/VLM/公式/STT 能力基准测试。"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from model_runtime import environment_variables, resolve_component, runtime_root

SCRIPT = Path(__file__).resolve()
SKILLS_ROOT = SCRIPT.parents[3]
REPO_ROOT = SKILLS_ROOT.parent
TRANSCRIBER = SKILLS_ROOT / "import" / "chinese-handwriting-formula-transcriber"
PRIVATE_ROOT = Path.home() / "Desktop" / "ocr_private_runs" / "chinese_handwriting_formula_transcriber"


def clean_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.update(environment_variables())
    return env


def run_command(name: str, command: list[str], output_dir: Path, timeout: int = 1800) -> dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        env=clean_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{name}.stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (output_dir / f"{name}.stderr.txt").write_text(proc.stderr, encoding="utf-8")
    return {"name": name, "ok": proc.returncode == 0, "exit_code": proc.returncode, "elapsed_sec": round(time.perf_counter() - started, 3)}


def summarize_engine_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"ok": False, "error": "report_missing"}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "ok": bool(data.get("ok")),
        "steps": [
            {"name": step.get("name"), "ok": bool(step.get("ok")), "error": step.get("error")}
            for step in data.get("steps", [])
        ],
    }


def step_data(report: dict[str, Any], name: str) -> Any:
    for step in report.get("steps", []):
        if step.get("name") == name and step.get("ok"):
            return step.get("data")
    return None


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def normalize_formula(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\\[", "").replace("\\]", "").replace("$$", "")
    value = re.sub(r"\\(?:mathsf|mathrm)\{([^{}]*)\}", r"\1", value)
    value = value.replace("\\quad", "").replace("\\,", "")
    value = value.replace("{", "").replace("}", "")
    return re.sub(r"\s+", "", value).lower()


def error_rate(prediction: str, expected: str) -> float:
    prediction = normalize_text(prediction)
    expected = normalize_text(expected)
    return round(levenshtein(prediction, expected) / max(1, len(expected)), 4)


def formula_metrics(prediction: str, expected: str) -> dict[str, Any]:
    pred = normalize_formula(prediction)
    truth = normalize_formula(expected)
    distance = levenshtein(pred, truth)
    return {
        "normalized_exact_match": pred == truth,
        "normalized_edit_distance": distance,
        "normalized_error_rate": round(distance / max(1, len(truth)), 4),
    }


def synthetic_metrics(paddle_report: Path, formula_report: Path) -> dict[str, Any]:
    paddle = json.loads(paddle_report.read_text(encoding="utf-8"))
    formula = json.loads(formula_report.read_text(encoding="utf-8"))
    expected_text = "物理答题：由牛顿第二定律得 F=ma"
    expected_formula = "v=v0+at,Ek=1/2mv^2"

    rec = step_data(paddle, "pp_ocrv6_medium_rec")[0]["res"]
    det = step_data(paddle, "pp_ocrv6_medium_det")[0]["res"]
    pp_formula = step_data(paddle, "pp_formulanet_plus_l")[0]["res"]["rec_formula"]
    layout = step_data(paddle, "pp_doclayoutv3")[0]["res"]["boxes"]
    vl = step_data(paddle, "paddleocr_vl_1_6")[0]["res"]["parsing_res_list"]
    texteller = step_data(formula, "texteller_inference")
    expected_labels = ["text", "display_formula", "display_formula", "text"]
    layout_labels = [item.get("label") for item in layout]
    vl_labels = [item.get("block_label") for item in vl]
    vl_orders = [item.get("block_order") for item in vl]
    return {
        "fixture": "synthetic_chinese_text_formula_layout_v1",
        "text": {
            "pp_ocrv6_medium_rec_cer": error_rate(rec.get("rec_text", ""), expected_text),
            "recognition_confidence": round(float(rec.get("rec_score", 0.0)), 4),
            "pp_ocrv6_medium_det_box_recall": round(min(1.0, len(det.get("dt_polys", [])) / 1), 4),
            "expected_line_count": 1,
            "detected_line_count": len(det.get("dt_polys", [])),
        },
        "formula": {
            "pp_formulanet_plus_l": formula_metrics(pp_formula, expected_formula),
            "texteller": {
                **formula_metrics(texteller.get("latex", ""), expected_formula),
                "device": texteller.get("device"),
            },
        },
        "layout": {
            "pp_doclayoutv3_block_type_accuracy": round(sum(a == b for a, b in zip(layout_labels, expected_labels)) / len(expected_labels), 4),
            "pp_doclayoutv3_reading_order_complete": [item.get("order") for item in layout] == [1, 2, 3, 4],
            "paddleocr_vl_block_type_accuracy": round(sum(a == b for a, b in zip(vl_labels, expected_labels)) / len(expected_labels), 4),
            "paddleocr_vl_reading_order_complete": vl_orders == [1, 2, 3, 4],
            "paddleocr_vl_text_block_cer": round(sum(error_rate(item.get("block_content", ""), truth) for item, truth in zip((vl[0], vl[-1]), ("手写中文和物理公式测试", "因此加速度方向向右"))) / 2, 4),
        },
        "runtime": {
            "paddle_run_check": step_data(paddle, "paddle_run_check"),
            "paddle_gpu": step_data(paddle, "paddle_gpu_peak"),
            "formula_onnxruntime_providers": step_data(formula, "imports").get("onnxruntime_providers", []),
        },
        "limitations": [
            "合成指标只使用一个已知样本，属于冒烟证据，不是公开基准测试分数。",
            "公式完全匹配的归一化只移除样式和空白；不将语义修复计为匹配。",
        ],
    }


def update_validation_lock(payload: dict[str, Any]) -> None:
    target = runtime_root() / "validation-lock.json"
    current = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {"schema_version": 1, "components": {}}
    components = current.setdefault("components", {})
    stamp = payload.get("generated_at")
    smoke = payload.get("suites", {}).get("smoke", {})
    if smoke.get("ok"):
        paddle_steps = {row.get("name"): row for row in smoke.get("paddle", {}).get("steps", [])}
        formula_steps = {row.get("name"): row for row in smoke.get("formula", {}).get("steps", [])}
        texteller_device = str(
            smoke.get("metrics", {}).get("formula", {}).get("texteller", {}).get("device") or "unknown"
        )
        mapping = {
            "paddlepaddle-gpu": ("paddle_run_check", paddle_steps, "gpu:0"),
            "paddleocr": ("paddleocr_vl_1_6", paddle_steps, "gpu:0"),
            "paddlex": ("paddleocr_vl_1_6", paddle_steps, "gpu:0"),
            "pp-ocrv6-medium-rec": ("pp_ocrv6_medium_rec", paddle_steps, "gpu:0"),
            "pp-ocrv6-medium-det": ("pp_ocrv6_medium_det", paddle_steps, "gpu:0"),
            "pp-formulanet-plus-l": ("pp_formulanet_plus_l", paddle_steps, "gpu:0"),
            "pp-doclayoutv3": ("pp_doclayoutv3", paddle_steps, "gpu:0"),
            "paddleocr-vl-1.6": ("paddleocr_vl_1_6", paddle_steps, "gpu:0"),
            "texteller-1.0.2": ("texteller_inference", formula_steps, texteller_device),
        }
        paddle_runtime = smoke.get("metrics", {}).get("runtime", {}).get("paddle_run_check", {})
        paddle_warnings = []
        if paddle_runtime and not paddle_runtime.get("cudnn_match", False):
            paddle_warnings.append(
                "Paddle cuDNN build/runtime mismatch: "
                f"build={paddle_runtime.get('cudnn_build')} runtime={paddle_runtime.get('cudnn_runtime')}"
            )
        for component_id, (step_name, steps, device) in mapping.items():
            if steps.get(step_name, {}).get("ok"):
                proof = {
                    "inference_verified": True,
                    "device": device,
                    "validated_at": stamp,
                    "suite": "synthetic_smoke",
                }
                if component_id in {"paddlepaddle-gpu", "paddleocr", "paddlex", "pp-formulanet-plus-l"}:
                    proof["runtime"] = paddle_runtime
                    if paddle_warnings:
                        proof["warnings"] = paddle_warnings
                components[component_id] = proof
    speech = payload.get("suites", {}).get("speech", {})
    if speech.get("ok"):
        for component_id, row in speech.get("models", {}).items():
            if row.get("ok"):
                components[component_id] = {"inference_verified": True, "device": "cuda", "validated_at": stamp, "suite": "speech_90s"}
    current["updated_at"] = stamp
    current["runtime_root"] = str(runtime_root())
    target.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def run_smoke(run_root: Path) -> dict[str, Any]:
    samples = run_root / "synthetic"
    cli = Path(resolve_component("paddleocr")["python"]).parent.parent.parent / "cli-py311" / "Scripts" / "python.exe"
    paddle = Path(resolve_component("paddleocr")["python"])
    formula = Path(resolve_component("texteller-1.0.2")["python"])
    make = run_command("make_synthetic", [str(cli), str(TRANSCRIBER / "scripts" / "_make_smoke_inputs.py"), "--output-dir", str(samples)], run_root)
    paddle_report = run_root / "paddle-smoke.json"
    formula_report = run_root / "formula-smoke.json"
    paddle_run = run_command(
        "paddle_smoke",
        [str(paddle), str(TRANSCRIBER / "scripts" / "_warm_paddle.py"), "--sample-dir", str(samples), "--output", str(paddle_report), "--device", "gpu:0"],
        run_root,
    )
    formula_run = run_command(
        "formula_smoke",
        [str(formula), str(TRANSCRIBER / "scripts" / "_warm_formula.py"), "--sample-dir", str(samples), "--output", str(formula_report)],
        run_root,
    )
    paddle_summary = summarize_engine_report(paddle_report)
    formula_summary = summarize_engine_report(formula_report)
    return {
        "ok": make["ok"] and paddle_run["ok"] and formula_run["ok"] and paddle_summary["ok"] and formula_summary["ok"],
        "runs": [make, paddle_run, formula_run],
        "paddle": paddle_summary,
        "formula": formula_summary,
        "metrics": synthetic_metrics(paddle_report, formula_report) if paddle_summary["ok"] and formula_summary["ok"] else {},
    }


def private_task_summary(task_id: str, output_dir: Path) -> dict[str, Any]:
    task_card = PRIVATE_ROOT / "tasks" / f"{task_id}.json"
    if not task_card.exists():
        return {"fixture_id": task_id, "ok": False, "error": "task_card_missing"}
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(TRANSCRIBER / "scripts" / "transcribe_image.ps1"),
        "-TaskId",
        task_id,
        "-Profile",
        "full",
    ]
    row = run_command(f"task_{task_id}", command, output_dir)
    task = json.loads(task_card.read_text(encoding="utf-8"))
    combined = Path(task.get("output_root", PRIVATE_ROOT / task_id)) / "combined_result.json"
    engines = []
    image_present = False
    if combined.exists():
        data = json.loads(combined.read_text(encoding="utf-8"))
        image_present = bool(data.get("image"))
        for engine in data.get("engines", []):
            parsed = engine.get("parsed") if isinstance(engine.get("parsed"), dict) else {}
            steps = [
                {"name": step.get("name"), "ok": bool(step.get("ok"))}
                for step in parsed.get("steps", [])
            ]
            parsed_ok = bool(parsed.get("ok")) and all(step["ok"] for step in steps)
            engines.append(
                {
                    "engine": engine.get("engine"),
                    "exit_code": engine.get("exit_code"),
                    "parsed_ok": parsed_ok,
                    "steps": steps,
                }
            )
    return {
        "fixture_id": task_id,
        "privacy": "private_local_only" if task_id.startswith("ocr_trial") else "non_private_exam_fixture",
        "ok": row["ok"] and bool(engines) and all(engine["parsed_ok"] for engine in engines),
        "elapsed_sec": row["elapsed_sec"],
        "image_present": image_present,
        "engines": engines,
    }


def levenshtein(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def run_speech(source: Path, run_root: Path) -> dict[str, Any]:
    if not source.exists():
        return {"ok": False, "error": "speech_source_missing"}
    wav = run_root / "speech-90s.wav"
    extract = run_command(
        "speech_extract",
        ["ffmpeg", "-y", "-v", "error", "-ss", "0", "-t", "90", "-i", str(source), "-vn", "-ar", "16000", "-ac", "1", str(wav)],
        run_root,
    )
    if not extract["ok"]:
        return {"ok": False, "extract": extract}
    python = Path(resolve_component("faster-whisper-large-v3")["python"])
    code = (
        "import json,sys,time; from faster_whisper import WhisperModel;"
        "m=WhisperModel(sys.argv[1],device='cuda',compute_type='float16');t=time.perf_counter();"
        "s,_=m.transcribe(sys.argv[2],language='zh',vad_filter=True);"
        "r=[{'start':round(x.start,2),'end':round(x.end,2),'text':x.text.strip()} for x in s if x.text.strip()];"
        "print(json.dumps({'elapsed_sec':round(time.perf_counter()-t,3),'segments':r},ensure_ascii=False))"
    )
    results = {}
    for model_id in ("faster-whisper-large-v3", "faster-whisper-medium"):
        model_path = resolve_component(model_id)["resolved_path"]
        started = time.perf_counter()
        proc = subprocess.run([str(python), "-c", code, model_path, str(wav)], env=clean_environment(), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        (run_root / f"{model_id}.stderr.txt").write_text(proc.stderr, encoding="utf-8")
        parsed = json.loads(proc.stdout) if proc.returncode == 0 and proc.stdout.strip() else {"segments": []}
        text = "".join(item["text"] for item in parsed.get("segments", []))
        (run_root / f"{model_id}.private-transcript.json").write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
        results[model_id] = {"ok": proc.returncode == 0 and bool(text), "elapsed_sec": parsed.get("elapsed_sec", round(time.perf_counter() - started, 3)), "segment_count": len(parsed.get("segments", [])), "character_count": len(text), "_text": text}
    large = results["faster-whisper-large-v3"].pop("_text")
    medium = results["faster-whisper-medium"].pop("_text")
    disagreement = levenshtein(large, medium) / max(1, len(large))
    return {
        "ok": all(item["ok"] for item in results.values()),
        "fixture_id": "public_chinese_technical_video_90s",
        "models": results,
        "pairwise_character_error_rate_vs_large_v3": round(disagreement, 4),
        "metric_limit": "未保存人工转录真值；这是模型差异率，不是真值 CER。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="运行共享 OCR/VLM/公式/STT 脱敏基准测试。")
    parser.add_argument("--suite", choices=["smoke", "representative", "speech", "all"], default="all", help="要运行的测试套件。")
    parser.add_argument("--speech-source", help="语音测试的本地音视频源路径。")
    parser.add_argument("--summary-out", help="可选的脱敏摘要输出路径。")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = runtime_root() / "benchmarks" / stamp
    run_root.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "run_id": stamp, "privacy": "sanitized_summary_only", "suites": {}}
    if args.suite in {"smoke", "all"}:
        payload["suites"]["smoke"] = run_smoke(run_root)
    if args.suite in {"representative", "all"}:
        task_ids = [
            "bemd_combined_paper_source_p001_1783594210",
            "bemd_combined_answer_source_p003_1783594884",
            "ocr_trial_20260707_04",
        ]
        rows = [private_task_summary(task_id, run_root) for task_id in task_ids]
        payload["suites"]["representative"] = {
            "ok": all(row["ok"] for row in rows),
            "fixtures": rows,
            "metric_limit": "这些任务卡没有保存人工真值；结果只证明步骤完成，不声称 CER 或公式准确率。",
        }
    if args.suite in {"speech", "all"}:
        payload["suites"]["speech"] = run_speech(Path(args.speech_source), run_root) if args.speech_source else {"ok": False, "error": "需要 --speech-source"}
    payload["ok"] = all(suite.get("ok") for suite in payload["suites"].values())
    update_validation_lock(payload)
    runtime_report = run_root / "sanitized-summary.json"
    runtime_report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.summary_out:
        target = Path(args.summary_out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
