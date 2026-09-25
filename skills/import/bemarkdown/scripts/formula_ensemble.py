"""Hash-bound local formula ensemble for full-frame WMF evidence images.

The controller runs PP-FormulaNet in the transcriber's Paddle environment and
TexTeller in its formula environment. Each model is initialized once per
resumable task and appends one fsync'd JSONL record per image. The public result is conservative: only two clean matching engines
can produce ``machine_final``; every other result is routed to VLM review.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

from formula_candidate_utils import resolve_formula_consensus


SKILLS_ROOT = Path(__file__).resolve().parents[3]
MODEL_RUNTIME_SCRIPT = SKILLS_ROOT / "_shared" / "model-tools" / "scripts" / "model_runtime.py"
MODEL_RUNTIME_SPEC = importlib.util.spec_from_file_location("llmwiki_shared_model_runtime", MODEL_RUNTIME_SCRIPT)
if MODEL_RUNTIME_SPEC is None or MODEL_RUNTIME_SPEC.loader is None:
    raise RuntimeError(f"cannot load shared model resolver: {MODEL_RUNTIME_SCRIPT}")
MODEL_RUNTIME = importlib.util.module_from_spec(MODEL_RUNTIME_SPEC)
MODEL_RUNTIME_SPEC.loader.exec_module(MODEL_RUNTIME)
PP_FORMULANET_COMPONENT = MODEL_RUNTIME.resolve_component("pp-formulanet-plus-l")
TEXTELLER_COMPONENT = MODEL_RUNTIME.resolve_component("texteller-1.0.2")
PADDLE_PYTHON = Path(PP_FORMULANET_COMPONENT["python"])
TEXTELLER_PYTHON = Path(TEXTELLER_COMPONENT["python"])
PP_FORMULANET_MODEL_DIR = Path(PP_FORMULANET_COMPONENT["resolved_path"])
TEXTELLER_MODEL_DIR = Path(TEXTELLER_COMPONENT["resolved_path"])
ENGINE_NAMES = ("pp_formulanet", "texteller")
PADDLE_BATCH_SIZE = 16
TEXTELLER_BATCH_SIZE = 32


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_stream_records(path: Path) -> dict[str, dict[str, Any]]:
    """Return the latest complete hash-bound stream record for every image."""
    records: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return records
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        source = str(record.get("source_image") or "")
        if source and record.get("status") in {"ok", "abstain"}:
            records[str(Path(source).resolve())] = record
    return records


def _stream_record_signature(record: dict[str, Any]) -> str:
    """Return the inference fields that must agree for byte-identical inputs."""
    payload = {
        key: record.get(key)
        for key in ("status", "text", "confidence", "candidates", "errors")
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _records_by_source_hash(
    records: dict[str, dict[str, Any]],
    allowed_hashes: set[str],
) -> dict[str, dict[str, Any]]:
    """Index complete records by image bytes and reject divergent evidence."""
    by_hash: dict[str, dict[str, Any]] = {}
    signatures: dict[str, str] = {}
    for record in records.values():
        digest = str(record.get("source_sha256") or "")
        if digest not in allowed_hashes:
            continue
        signature = _stream_record_signature(record)
        if digest in signatures and signatures[digest] != signature:
            raise RuntimeError(f"conflicting completed model evidence for identical PNG sha256: {digest}")
        signatures[digest] = signature
        by_hash[digest] = record
    return by_hash


def _record_for_source(
    record: dict[str, Any],
    source: Path,
    digest: str,
) -> dict[str, Any]:
    """Bind one byte-identical inference record to a requested source path."""
    rebound = dict(record)
    original = str(Path(str(record.get("source_image") or source)).resolve())
    requested = str(source.resolve())
    rebound["source_image"] = requested
    rebound["source_sha256"] = digest
    if original != requested:
        rebound["deduplicated_from"] = original
        rebound["deduplication_key"] = "source_sha256"
    return rebound


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "json"):
        try:
            return value.json
        except Exception:
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return repr(value)


def extract_candidate(value: Any) -> tuple[str, float | None]:
    """Extract one formula from common Paddle return shapes."""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[", "(")):
            try:
                return extract_candidate(ast.literal_eval(text))
            except Exception:
                pass
        return text, None
    if isinstance(value, dict):
        for key in ("rec_formula", "latex", "text", "formula"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                raw_score = value.get("score", value.get("confidence", value.get("rec_score")))
                score = float(raw_score) if isinstance(raw_score, (int, float)) else None
                return text.strip(), score
        for nested in value.values():
            text, score = extract_candidate(nested)
            if text:
                return text, score
    if isinstance(value, (list, tuple)):
        for nested in value:
            text, score = extract_candidate(nested)
            if text:
                return text, score
    return "", None


def extract_texteller(stdout: str) -> str:
    match = re.search(r"Predicted LaTeX:\s*```(?:latex)?\s*(.*?)```", stdout, re.S | re.I)
    if match:
        return match.group(1).strip()
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _construct_formula_model() -> Any:
    from paddleocr import FormulaRecognition

    if not PP_FORMULANET_MODEL_DIR.is_dir():
        raise FileNotFoundError(f"bundled PP-FormulaNet model missing: {PP_FORMULANET_MODEL_DIR}")
    model_args = {
        "model_name": str(PP_FORMULANET_COMPONENT["model_name"]),
        "model_dir": str(PP_FORMULANET_MODEL_DIR),
    }
    try:
        return FormulaRecognition(device="gpu:0", **model_args)
    except TypeError:
        return FormulaRecognition(**model_args)


def worker_paddle(task_path: Path, output_path: Path) -> int:
    task = json.loads(task_path.read_text(encoding="utf-8"))
    report: dict[str, Any] = {"engine": "pp_formulanet", "records": [], "ok": False}
    try:
        model = _construct_formula_model()
        for image in task["images"]:
            record = {"source_image": image, "text": "", "confidence": None, "ok": False}
            try:
                output = model.predict(input=image, batch_size=1)
                converted = [to_jsonable(item) for item in output]
                text, score = extract_candidate(converted)
                record.update({"text": text, "confidence": score, "raw": converted, "ok": bool(text)})
            except Exception as exc:
                record.update({"error": repr(exc), "traceback": traceback.format_exc(limit=8)})
            report["records"].append(record)
        report["ok"] = any(record["ok"] for record in report["records"])
    except Exception as exc:
        report.update({"error": repr(exc), "traceback": traceback.format_exc(limit=8)})
    atomic_json(output_path, report)
    return 0 if report["ok"] else 1


def _texteller_model() -> tuple[Any, Any, Any]:
    from texteller.api import img2latex, load_model, load_tokenizer
    from texteller.utils import image as texteller_image

    if not TEXTELLER_MODEL_DIR.is_dir():
        raise FileNotFoundError(f"bundled TexTeller model missing: {TEXTELLER_MODEL_DIR}")
    if not getattr(texteller_image.trim_white_border, "_llmwiki_safe", False):
        original_trim = texteller_image.trim_white_border

        def safe_trim(image: Any) -> Any:
            import numpy as np

            trimmed = original_trim(image)
            if getattr(trimmed, "ndim", 0) != 3 or trimmed.shape[0] < 1 or trimmed.shape[1] < 1:
                return trimmed
            height, width = int(trimmed.shape[0]), int(trimmed.shape[1])
            long_side = max(width, height)
            required_short = max(1, math.ceil(long_side * 2 / 448))
            pad_height = max(0, required_short - height)
            pad_width = max(0, required_short - width)
            if not pad_height and not pad_width:
                return trimmed
            top = pad_height // 2
            bottom = pad_height - top
            left = pad_width // 2
            right = pad_width - left
            return np.pad(trimmed, ((top, bottom), (left, right), (0, 0)), constant_values=255)

        safe_trim._llmwiki_safe = True  # type: ignore[attr-defined]
        texteller_image.trim_white_border = safe_trim
    model_dir = str(TEXTELLER_MODEL_DIR)
    return load_model(model_dir), load_tokenizer(model_dir), img2latex


def _run_texteller_batch(runtime: tuple[Any, Any, Any], images: list[str]) -> list[str]:
    model, tokenizer, img2latex = runtime
    from PIL import Image

    with contextlib.ExitStack() as stack:
        prepared: list[str] = []
        for index, image in enumerate(images):
            with Image.open(image) as opened:
                has_alpha = "A" in opened.getbands() or (opened.mode == "P" and "transparency" in opened.info)
                width, height = opened.size
            long_side = max(width, height)
            short_side = min(width, height)
            required_short = max(1, math.ceil(long_side * 2 / 448))
            needs_padding = short_side < required_short
            if has_alpha or needs_padding or not image.isascii():
                temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix="texteller-input-"))
                cli_path = Path(temporary) / f"input-{index}.png"
                if has_alpha or needs_padding:
                    with Image.open(image) as opened:
                        if has_alpha:
                            rgba = opened.convert("RGBA")
                            white_rgba = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                            white_rgba.alpha_composite(rgba)
                            content = white_rgba.convert("RGB")
                        else:
                            content = opened.convert("RGB")
                        target_width = max(content.width, required_short if content.width <= content.height else content.width)
                        target_height = max(content.height, required_short if content.height < content.width else content.height)
                        canvas = Image.new("RGB", (target_width, target_height), "white")
                        canvas.paste(
                            content,
                            ((target_width - content.width) // 2, (target_height - content.height) // 2),
                        )
                        canvas.save(cli_path, format="PNG")
                else:
                    shutil.copy2(image, cli_path)
                prepared.append(str(cli_path))
            else:
                prepared.append(image)
        predictions = img2latex(
            model=model,
            tokenizer=tokenizer,
            images=prepared,
            out_format="latex",
            keep_style=False,
        )
        if len(predictions or []) != len(images):
            raise RuntimeError(f"TexTeller batch result count mismatch: {len(predictions or [])} != {len(images)}")
        return [str(value).strip() for value in predictions]


def _run_texteller(runtime: tuple[Any, Any, Any], image: str) -> str:
    return _run_texteller_batch(runtime, [image])[0]


def worker_formula(task_path: Path, output_path: Path) -> int:
    task = json.loads(task_path.read_text(encoding="utf-8"))
    report: dict[str, Any] = {"engines": ["texteller"], "records": [], "ok": False}
    texteller = None
    texteller_error = None
    try:
        texteller = _texteller_model()
    except Exception as exc:
        texteller_error = repr(exc)

    for image in task["images"]:
        entry: dict[str, Any] = {"source_image": image, "candidates": [], "errors": []}
        if texteller is not None:
            try:
                entry["candidates"].append({
                    "engine": "texteller",
                    "text": _run_texteller(texteller, image),
                    "confidence": None,
                })
            except Exception as exc:
                entry["errors"].append({"engine": "texteller", "error": repr(exc)})
        else:
            entry["errors"].append({"engine": "texteller", "error": texteller_error})
        report["records"].append(entry)
    report["ok"] = any(entry["candidates"] for entry in report["records"])
    atomic_json(output_path, report)
    return 0 if report["ok"] else 1


def worker_paddle_stream(task_path: Path, output_path: Path) -> int:
    task = json.loads(task_path.read_text(encoding="utf-8"))
    try:
        model = _construct_formula_model()
    except Exception as exc:
        for image in task["images"]:
            append_jsonl(output_path, {
                "source_image": image,
                "source_sha256": sha256_path(Path(image)),
                "status": "error",
                "error": repr(exc),
            })
        return 1
    success = False
    images = list(task["images"])
    for start in range(0, len(images), PADDLE_BATCH_SIZE):
        batch = images[start:start + PADDLE_BATCH_SIZE]
        try:
            output = model.predict(input=batch, batch_size=len(batch))
            converted = [to_jsonable(item) for item in output]
            if len(converted) != len(batch):
                raise RuntimeError(f"Paddle batch result count mismatch: {len(converted)} != {len(batch)}")
            for image, raw in zip(batch, converted):
                text, score = extract_candidate(raw)
                record = {
                    "source_image": image,
                    "source_sha256": sha256_path(Path(image)),
                    "text": text,
                    "confidence": score,
                    "raw": raw,
                    "status": "ok" if text else "abstain",
                }
                success = success or bool(text)
                append_jsonl(output_path, record)
        except Exception as exc:
            batch_error = repr(exc)
            for image in batch:
                record: dict[str, Any] = {
                    "source_image": image,
                    "source_sha256": sha256_path(Path(image)),
                    "text": "",
                    "confidence": None,
                }
                try:
                    output = model.predict(input=image, batch_size=1)
                    converted = [to_jsonable(item) for item in output]
                    text, score = extract_candidate(converted)
                    record.update({"text": text, "confidence": score, "raw": converted, "status": "ok" if text else "abstain"})
                    success = success or bool(text)
                except Exception as item_exc:
                    record.update({
                        "status": "error",
                        "error": repr(item_exc),
                        "batch_error": batch_error,
                        "traceback": traceback.format_exc(limit=8),
                    })
                append_jsonl(output_path, record)
    return 0 if success else 1


def worker_formula_stream(task_path: Path, output_path: Path) -> int:
    task = json.loads(task_path.read_text(encoding="utf-8"))
    try:
        runtime = _texteller_model()
    except Exception as exc:
        for image in task["images"]:
            append_jsonl(output_path, {
                "source_image": image,
                "source_sha256": sha256_path(Path(image)),
                "status": "error",
                "error": repr(exc),
            })
        return 1
    success = False
    images = list(task["images"])
    for start in range(0, len(images), TEXTELLER_BATCH_SIZE):
        batch = images[start:start + TEXTELLER_BATCH_SIZE]
        try:
            texts = _run_texteller_batch(runtime, batch)
            for image, text in zip(batch, texts):
                record: dict[str, Any] = {
                    "source_image": image,
                    "source_sha256": sha256_path(Path(image)),
                    "candidates": [{"engine": "texteller", "text": text, "confidence": None}],
                    "errors": [],
                    "status": "ok" if text else "abstain",
                }
                success = success or bool(text)
                append_jsonl(output_path, record)
        except Exception as exc:
            batch_error = repr(exc)
            for image in batch:
                record = {
                    "source_image": image,
                    "source_sha256": sha256_path(Path(image)),
                    "candidates": [],
                    "errors": [],
                }
                try:
                    text = _run_texteller(runtime, image)
                    record["candidates"].append({"engine": "texteller", "text": text, "confidence": None})
                    record["status"] = "ok" if text else "abstain"
                    success = success or bool(text)
                except Exception as item_exc:
                    record["status"] = "error"
                    record["errors"].append({"engine": "texteller", "error": repr(item_exc)})
                    record["batch_error"] = batch_error
                    record["traceback"] = traceback.format_exc(limit=8)
                append_jsonl(output_path, record)
    return 0 if success else 1


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        process.kill()


def _run_subprocess(command: list[str], *, timeout: int, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        creationflags=creationflags,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(process)
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=str(exc.output or "") + str(stdout or ""),
            stderr=str(exc.stderr or "") + str(stderr or ""),
        ) from exc
    return subprocess.CompletedProcess(command, process.returncode, stdout=stdout, stderr=stderr)


def _run_worker(python: Path, mode: str, task: Path, output: Path, timeout: int) -> dict[str, Any]:
    if not python.is_file():
        return {"ok": False, "error": f"runtime python missing: {python}", "records": []}
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    process = _run_subprocess(
        [str(python), str(Path(__file__).resolve()), mode, "--task", str(task), "--output", str(output)],
        timeout=timeout,
        env=env,
    )
    if output.is_file():
        payload = json.loads(output.read_text(encoding="utf-8"))
    else:
        payload = {"ok": False, "records": []}
    payload["worker_returncode"] = process.returncode
    payload["worker_stdout"] = process.stdout[-4000:]
    payload["worker_stderr"] = process.stderr[-4000:]
    return payload


def _run_stream_worker(
    python: Path,
    mode: str,
    images: list[Path],
    task: Path,
    output: Path,
    timeout: int,
) -> dict[str, Any]:
    current_hashes = {str(path.resolve()): sha256_path(path) for path in images}
    groups: dict[str, list[Path]] = {}
    for image in images:
        groups.setdefault(current_hashes[str(image.resolve())], []).append(image)
    allowed_hashes = set(groups)
    cached = load_stream_records(output)
    cached_by_hash = _records_by_source_hash(cached, allowed_hashes)
    pending = [group[0] for digest, group in groups.items() if digest not in cached_by_hash]
    cached_count = sum(len(group) for digest, group in groups.items() if digest in cached_by_hash)
    worker_returncode = 0
    worker_stdout = ""
    worker_stderr = ""
    if pending:
        atomic_json(task, {"schema_version": 1, "images": [str(path) for path in pending]})
        env = os.environ.copy()
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            process = _run_subprocess(
                [str(python), str(Path(__file__).resolve()), mode, "--task", str(task), "--output", str(output)],
                timeout=timeout,
                env=env,
            )
            worker_returncode = process.returncode
            worker_stdout = process.stdout[-4000:]
            worker_stderr = process.stderr[-4000:]
        except subprocess.TimeoutExpired as exc:
            worker_returncode = 124
            worker_stdout = str(exc.stdout or "")[-4000:]
            worker_stderr = (str(exc.stderr or "") + f"\nworker timed out after {timeout}s")[-4000:]
    cached = load_stream_records(output)
    cached_by_hash = _records_by_source_hash(cached, allowed_hashes)
    records = [
        _record_for_source(cached_by_hash[digest], image, digest)
        for image in images
        if (digest := current_hashes[str(image.resolve())]) in cached_by_hash
    ]
    pending_count = sum(len(group) for digest, group in groups.items() if digest not in cached_by_hash)
    return {
        "ok": len(records) == len(images),
        "records": records,
        "worker_returncode": worker_returncode,
        "worker_stdout": worker_stdout,
        "worker_stderr": worker_stderr,
        "input_count": len(images),
        "unique_hash_count": len(groups),
        "deduplicated_count": len(images) - len(groups),
        "submitted_unique_count": len(pending),
        "cached_count": cached_count,
        "pending_count": pending_count,
    }


def merge_engine_reports(
    images: list[Path],
    paddle: dict[str, Any],
    formula: dict[str, Any],
    *,
    geometry_by_image: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    candidates_by_image: dict[str, list[dict[str, Any]]] = {str(path.resolve()): [] for path in images}
    errors_by_image: dict[str, list[dict[str, Any]]] = {str(path.resolve()): [] for path in images}
    for record in paddle.get("records") or []:
        key = str(Path(record.get("source_image", "")).resolve())
        if key in candidates_by_image and (
            record.get("status") in {"ok", "abstain"}
            or ("status" not in record and "text" in record)
        ):
            candidates_by_image[key].append({
                "engine": "pp_formulanet",
                "text": record.get("text", ""),
                "confidence": record.get("confidence"),
            })
        if key in errors_by_image and record.get("error"):
            errors_by_image[key].append({"engine": "pp_formulanet", "error": record["error"]})
    for record in formula.get("records") or []:
        key = str(Path(record.get("source_image", "")).resolve())
        if key in candidates_by_image:
            candidates_by_image[key].extend(record.get("candidates") or [])
            errors_by_image[key].extend(record.get("errors") or [])

    results: list[dict[str, Any]] = []
    for image in images:
        key = str(image.resolve())
        geometry = (geometry_by_image or {}).get(key, [])
        resolution = resolve_formula_consensus(candidates_by_image[key], geometry_issues=geometry)
        results.append({
            "schema_version": 2,
            "source_image": key,
            "source_sha256": sha256_path(image),
            "required_engines": list(ENGINE_NAMES),
            "candidates": resolution.pop("candidates"),
            "engine_errors": errors_by_image[key],
            "resolution": resolution,
        })
    return results


def run_formula_ensemble(
    images: list[str | Path],
    output_dir: str | Path,
    *,
    geometry_by_image: dict[str, list[str]] | None = None,
    timeout: int = 3600,
) -> dict[str, Any]:
    paths = [Path(value).resolve() for value in images]
    if not paths or any(not path.is_file() for path in paths):
        raise FileNotFoundError("every formula ensemble input must be an existing image")
    output_root = Path(output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    paddle_path = output_root / "pp_formulanet.jsonl"
    formula_path = output_root / "texteller.jsonl"
    started = time.monotonic()
    paddle = _run_stream_worker(
        PADDLE_PYTHON,
        "worker-paddle-stream",
        paths,
        output_root / "pp_formulanet.task.json",
        paddle_path,
        timeout,
    )
    remaining = max(0, int(timeout - (time.monotonic() - started)))
    if remaining >= 5:
        formula = _run_stream_worker(
            TEXTELLER_PYTHON,
            "worker-formula-stream",
            paths,
            output_root / "texteller.task.json",
            formula_path,
            remaining,
        )
    else:
        unique_hash_count = len({sha256_path(path) for path in paths})
        formula = {
            "ok": False,
            "records": [],
            "worker_returncode": 124,
            "worker_stdout": "",
            "worker_stderr": "not started: total model timeout budget exhausted by PP-FormulaNet",
            "input_count": len(paths),
            "unique_hash_count": unique_hash_count,
            "deduplicated_count": len(paths) - unique_hash_count,
            "submitted_unique_count": 0,
            "cached_count": 0,
            "pending_count": len(paths),
        }
    records = merge_engine_reports(paths, paddle, formula, geometry_by_image=geometry_by_image)
    payload = {
        "schema_version": 2,
        "ok": all(record["resolution"]["status"] != "machine_abstain" for record in records),
        "records": records,
        "engine_runs": {
            "pp_formulanet": {
                key: paddle.get(key)
                for key in (
                    "ok", "worker_returncode", "worker_stderr", "input_count", "unique_hash_count",
                    "deduplicated_count", "submitted_unique_count", "cached_count", "pending_count",
                )
            },
            "texteller": {
                key: formula.get(key)
                for key in (
                    "ok", "worker_returncode", "worker_stderr", "input_count", "unique_hash_count",
                    "deduplicated_count", "submitted_unique_count", "cached_count", "pending_count",
                )
            },
        },
    }
    atomic_json(output_root / "formula_ensemble.json", payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("worker-paddle", "worker-formula", "worker-paddle-stream", "worker-formula-stream"):
        worker = sub.add_parser(name)
        worker.add_argument("--task", required=True)
        worker.add_argument("--output", required=True)
    run = sub.add_parser("run")
    run.add_argument("--image", action="append", required=True)
    run.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    if args.command == "worker-paddle":
        return worker_paddle(Path(args.task), Path(args.output))
    if args.command == "worker-formula":
        return worker_formula(Path(args.task), Path(args.output))
    if args.command == "worker-paddle-stream":
        return worker_paddle_stream(Path(args.task), Path(args.output))
    if args.command == "worker-formula-stream":
        return worker_formula_stream(Path(args.task), Path(args.output))
    payload = run_formula_ensemble(args.image, args.output_dir)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
