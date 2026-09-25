#!/usr/bin/env python
"""Fail-closed question parsing and tutoring-stage orchestration for Role D."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve()
KB_ROOT = SCRIPT.parents[4]
DEFAULT_OUTPUT_ROOT = KB_ROOT / "output" / "learning_sessions"
MODEL_PROFILES_PATH = SCRIPT.parents[2] / "_shared" / "references" / "model-profiles.json"
SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
TEXT_SUFFIXES = {".txt", ".md"}
STAGES = {"confirm", "hint", "plan", "solution"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_model_profiles() -> dict[str, Any]:
    payload = json.loads(MODEL_PROFILES_PATH.read_text(encoding="utf-8"))
    profiles = payload.get("profiles")
    if payload.get("schema_version") != 1 or not isinstance(profiles, dict):
        raise ValueError("invalid Role D model profile contract")
    if set(profiles) != {"weak", "strong"}:
        raise ValueError("Role D model profiles must be exactly weak and strong")
    return payload


def ensure_inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(root.resolve())
    return resolved


def clean_session_id(value: str) -> str:
    if not SESSION_RE.fullmatch(value) or ".." in value:
        raise ValueError("session-id must be 1-64 ASCII letters, digits, dot, underscore, or hyphen")
    return value


def redact_identity_lines(text: str) -> tuple[str, list[str]]:
    patterns = [r"姓名\s*[:：]?\s*\S+", r"(?:学号|考号|准考证号)\s*[:：]?\s*[A-Za-z0-9-]+"]
    redactions: list[str] = []
    clean = text
    for pattern in patterns:
        if re.search(pattern, clean, flags=re.IGNORECASE):
            redactions.append(pattern.split("\\s", 1)[0])
            clean = re.sub(pattern, "[REDACTED]", clean, flags=re.IGNORECASE)
    return clean, redactions


def sanitize_image(source: Path, destination: Path) -> tuple[int, int]:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - deployment error path
        raise RuntimeError("Pillow is required; run install_skill_dependencies.py --install --all") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        clean = image.convert("RGB")
        clean.save(destination, format="PNG", optimize=True)
        return clean.size


def extract_pdf_text(source: Path) -> tuple[str, float, list[dict[str, str]]]:
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - deployment error path
        raise RuntimeError("PyMuPDF is required; run install_skill_dependencies.py --install --all") from exc
    pages: list[str] = []
    with fitz.open(source) as document:
        for page in document:
            pages.append(page.get_text("text").strip())
    text = "\n".join(part for part in pages if part).strip()
    if len(text) < 20:
        return text, 0.0, [{"field": "source.text", "reason": "PDF 没有可靠文本层，需要本地 OCR"}]
    return text, 0.97, []


def load_ocr_adapter(source: Path, explicit: str | None) -> tuple[dict[str, Any] | None, Path | None]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend([Path(str(source) + ".ocr.json"), source.with_suffix(".ocr.json")])
    for candidate in candidates:
        if not candidate.exists():
            continue
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"OCR adapter must be a JSON object: {candidate}")
        return payload, candidate.resolve()
    return None, None


def normalize_uncertainties(value: Any) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if not isinstance(value, list):
        return items
    for index, item in enumerate(value):
        if isinstance(item, str):
            items.append({"field": f"ocr.item_{index + 1}", "reason": item})
        elif isinstance(item, dict):
            items.append(
                {
                    "field": str(item.get("field") or f"ocr.item_{index + 1}"),
                    "reason": str(item.get("reason") or "未确认"),
                }
            )
    return items


def load_vision_review(path_value: str, source: Path, sanitized_copy: str | None) -> dict[str, Any]:
    if not sanitized_copy:
        raise ValueError("vision review requires a sanitized image source")
    path = Path(path_value).resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "source_sha256",
        "sanitized_sha256",
        "review_status",
        "confidence",
        "stem_candidate",
        "formula_candidates",
        "diagram_objects",
        "conflicts",
        "uncertain_items",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError(f"vision review fields must be exactly: {sorted(required)}")
    if payload["schema_version"] != 1:
        raise ValueError("vision review schema_version must be 1")
    if payload["source_sha256"] != sha256_file(source):
        raise ValueError("vision review source_sha256 mismatch")
    if payload["sanitized_sha256"] != sha256_file(Path(sanitized_copy)):
        raise ValueError("vision review sanitized_sha256 mismatch")
    if payload["review_status"] not in {"agree", "partial", "conflict", "insufficient"}:
        raise ValueError("invalid vision review status")
    confidence = payload["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        raise ValueError("vision review confidence must be between 0 and 1")
    if payload["stem_candidate"] is not None and not isinstance(payload["stem_candidate"], str):
        raise ValueError("vision review stem_candidate must be string or null")
    if not isinstance(payload["formula_candidates"], list) or not all(isinstance(item, str) for item in payload["formula_candidates"]):
        raise ValueError("vision review formula_candidates must be a string array")
    if not isinstance(payload["diagram_objects"], list) or not all(isinstance(item, dict) for item in payload["diagram_objects"]):
        raise ValueError("vision review diagram_objects must be an object array")
    conflicts = normalize_uncertainties(payload["conflicts"])
    uncertain_items = normalize_uncertainties(payload["uncertain_items"])
    if len(conflicts) != len(payload["conflicts"]) or len(uncertain_items) != len(payload["uncertain_items"]):
        raise ValueError("vision review conflicts and uncertain_items must contain strings or objects")
    candidate, redactions = redact_identity_lines(payload["stem_candidate"] or "")
    for label in redactions:
        uncertain_items.append({"field": "privacy", "reason": f"已移除视觉候选身份字段: {label}"})
    return {
        "review_status": payload["review_status"],
        "confidence": round(float(confidence), 4),
        "stem_candidate": candidate or None,
        "formula_candidates": payload["formula_candidates"],
        "diagram_objects": payload["diagram_objects"],
        "conflicts": conflicts,
        "uncertain_items": uncertain_items,
        "review_sha256": sha256_file(path),
    }


def parse_options(text: str) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    pattern = re.compile(r"(?m)^\s*([A-H])\s*[\.．、:：)]\s*(.+?)\s*$")
    for match in pattern.finditer(text):
        options.append({"label": match.group(1), "text": match.group(2).strip()})
    return options


def extract_formulas(text: str, adapter: dict[str, Any] | None) -> list[str]:
    formulas: list[str] = []
    if adapter and isinstance(adapter.get("formulas"), list):
        formulas.extend(str(item).strip() for item in adapter["formulas"] if str(item).strip())
    patterns = [r"\$([^$\n]+)\$", r"\\\[([^\]]+)\\\]", r"\\\(([^\)]+)\\\)"]
    for pattern in patterns:
        formulas.extend(match.strip() for match in re.findall(pattern, text) if match.strip())
    return list(dict.fromkeys(formulas))


def load_source(source: Path, session_dir: Path, ocr_json: str | None) -> dict[str, Any]:
    suffix = source.suffix.lower()
    adapter: dict[str, Any] | None = None
    adapter_path: Path | None = None
    uncertainties: list[dict[str, str]] = []
    sanitized_copy: str | None = None
    dimensions: list[int] | None = None

    if suffix in TEXT_SUFFIXES:
        text = source.read_text(encoding="utf-8")
        confidence = 1.0
        kind = "text"
    elif suffix == ".pdf":
        text, confidence, uncertainties = extract_pdf_text(source)
        kind = "pdf"
        if confidence == 0.0:
            adapter, adapter_path = load_ocr_adapter(source, ocr_json)
    elif suffix in IMAGE_SUFFIXES:
        kind = "image"
        sanitized = session_dir / "source_sanitized.png"
        size = sanitize_image(source, sanitized)
        dimensions = [int(size[0]), int(size[1])]
        sanitized_copy = str(sanitized)
        adapter, adapter_path = load_ocr_adapter(source, ocr_json)
        text = ""
        confidence = 0.0
    elif suffix == ".json":
        kind = "structured"
        adapter = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(adapter, dict):
            raise ValueError("structured question input must be a JSON object")
        adapter_path = source
        text = ""
        confidence = 0.0
    else:
        raise ValueError(f"unsupported input type: {suffix or '<none>'}")

    if adapter is not None:
        text = str(adapter.get("text") or text).strip()
        confidence = float(adapter.get("confidence", confidence or 0.0))
        uncertainties = normalize_uncertainties(adapter.get("uncertain_items"))

    text, redactions = redact_identity_lines(text)
    for label in redactions:
        uncertainties.append({"field": "privacy", "reason": f"已移除身份字段: {label}"})

    return {
        "kind": kind,
        "text": text.strip(),
        "confidence": max(0.0, min(1.0, confidence)),
        "uncertainties": uncertainties,
        "adapter": adapter,
        "adapter_sha256": sha256_file(adapter_path) if adapter_path else None,
        "sanitized_copy": sanitized_copy,
        "sanitized_sha256": sha256_file(Path(sanitized_copy)) if sanitized_copy else None,
        "dimensions": dimensions,
    }


def build_parse(args: argparse.Namespace, source: Path, session_dir: Path) -> dict[str, Any]:
    loaded = load_source(source, session_dir, args.ocr_json)
    text = loaded["text"]
    confidence = loaded["confidence"]
    uncertainties = list(loaded["uncertainties"])
    adapter = loaded.get("adapter") or {}
    vision_review: dict[str, Any] | None = None
    if args.vision_review_json:
        if args.model_profile != "strong":
            raise ValueError("vision review is available only with --model-profile strong")
        if loaded["kind"] != "image":
            raise ValueError("vision review is supported only for image inputs")
        vision_review = load_vision_review(args.vision_review_json, source, loaded["sanitized_copy"])
        uncertainties.extend(vision_review["conflicts"])
        uncertainties.extend(vision_review["uncertain_items"])
        if vision_review["review_status"] != "agree" and not vision_review["conflicts"] and not vision_review["uncertain_items"]:
            uncertainties.append({"field": "vision_review", "reason": f"视觉复核状态为 {vision_review['review_status']}，需要用户确认"})
        if not text and vision_review["stem_candidate"]:
            text = vision_review["stem_candidate"]
            confidence = min(float(vision_review["confidence"]), 0.84)
            uncertainties.append({"field": "question", "reason": "本地 OCR 无可靠文本；强模型视觉候选必须由用户确认"})

    vision_review_required = args.model_profile == "strong" and loaded["kind"] == "image"
    missing_required_review = vision_review_required and vision_review is None
    if missing_required_review:
        uncertainties.append({"field": "vision_review", "reason": "strong 档必须在本地 OCR 后复核脱敏图像"})

    if not text:
        status = "needs_ocr"
        if not uncertainties:
            uncertainties.append({"field": "source.text", "reason": "没有可靠 OCR 文本"})
    elif confidence < 0.85 or uncertainties:
        status = "needs_confirmation"
    elif loaded["kind"] in {"image", "structured"} and not args.confirmed:
        status = "needs_confirmation"
        uncertainties.append({"field": "question", "reason": "图片/结构化识别结果需要用户确认"})
    else:
        status = "confirmed"

    diagram_objects = adapter.get("diagram_objects", []) if isinstance(adapter, dict) else []
    if not isinstance(diagram_objects, list):
        diagram_objects = []
    formulas = extract_formulas(text, adapter)
    confirmation_basis = json_sha256(
        {
            "source_sha256": sha256_file(source),
            "sanitized_sha256": loaded["sanitized_sha256"],
            "ocr_adapter_sha256": loaded["adapter_sha256"],
            "model_profile": args.model_profile,
            "stem": text,
            "options": parse_options(text),
            "latex_formulas": formulas,
            "diagram_objects": diagram_objects,
            "vision_review_sha256": vision_review.get("review_sha256") if vision_review else None,
        }
    )

    previous_path = session_dir / "question_parse.json"
    previous_confirmed = False
    if previous_path.exists():
        try:
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
            previous_confirmed = bool(previous.get("confirmed_by_user")) and previous.get("confirmation_basis_sha256") == confirmation_basis
        except Exception:
            previous_confirmed = False
    confirmation_applied = False
    if args.confirmed or previous_confirmed:
        if text and confidence >= 0.5 and not missing_required_review:
            status = "confirmed"
            uncertainties = []
            confirmation_applied = True

    return {
        "schema_version": 1,
        "session_id": args.session_id,
        "source": {
            "kind": loaded["kind"],
            "sha256": sha256_file(source),
            "sanitized_copy": loaded["sanitized_copy"],
            "sanitized_sha256": loaded["sanitized_sha256"],
            "dimensions": loaded["dimensions"],
            "ocr_adapter_sha256": loaded["adapter_sha256"],
        },
        "status": status,
        "model_profile": args.model_profile,
        "confidence": round(confidence, 4),
        "stem": text,
        "options": parse_options(text),
        "latex_formulas": formulas,
        "diagram_objects": diagram_objects,
        "vision_review": vision_review,
        "unconfirmed_items": uncertainties,
        "confirmed_by_user": confirmation_applied,
        "confirmation_basis_sha256": confirmation_basis,
    }


def stage_contract(stage: str, parse_path: Path, payload: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    blocked = stage != "confirm" and payload["status"] != "confirmed"
    sections = {
        "confirm": ["parsed_question", "unconfirmed_items"],
        "hint": ["single_hint", "check_question"],
        "plan": ["model", "laws", "ordered_steps"],
        "solution": ["given", "model", "derivation", "answer", "verification", "common_errors"],
    }[stage]
    reveal = stage == "solution"
    return {
        "schema_version": 1,
        "stage": stage,
        "model_profile": payload["model_profile"],
        "profile_capabilities": profile,
        "status": "blocked" if blocked else "ready_for_model",
        "question_parse": str(parse_path),
        "retrieval_required": stage != "confirm",
        "retrieval_skill": "retrieve/llmwiki-rag-retrieval/SKILL.md" if stage != "confirm" else None,
        "required_sections": sections,
        "response_constraints": {
            "reveal_final_answer": reveal,
            "read_raw_fulltext": False,
            "read_student_database": False,
            "write_outside_session": False,
            "must_cite_physics_law": stage in {"plan", "solution"},
            "structured_response_validation": bool(profile["response_validation_required"]),
            "numeric_recheck_required": bool(profile["numeric_recheck_required"]),
        },
        "unconfirmed_items": payload["unconfirmed_items"] if blocked or stage == "confirm" else [],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse a physics question and enforce tutoring stages.")
    parser.add_argument("--input", required=True, help="image, PDF, text, Markdown, or structured JSON input")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--stage", choices=sorted(STAGES), required=True)
    parser.add_argument("--ocr-json", help="structured local OCR adapter result")
    parser.add_argument("--vision-review-json", help="strong-model review of the sanitized image")
    parser.add_argument("--model-profile", choices=["weak", "strong"], default="weak")
    parser.add_argument("--confirmed", action="store_true", help="record explicit user confirmation")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output-root", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        model_profiles = load_model_profiles()
        args.session_id = clean_session_id(args.session_id)
        source = Path(args.input).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        output_root = DEFAULT_OUTPUT_ROOT
        if args.output_root:
            if not args.test_mode:
                raise PermissionError("--output-root is available only with --test-mode")
            output_root = Path(args.output_root).resolve()
        session_dir = ensure_inside(output_root / args.session_id, output_root)
        session_dir.mkdir(parents=True, exist_ok=True)
        payload = build_parse(args, source, session_dir)
        parse_path = session_dir / "question_parse.json"
        parse_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        contract = stage_contract(args.stage, parse_path, payload, model_profiles["profiles"][args.model_profile])
        stage_path = session_dir / f"stage_{args.stage}.json"
        stage_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
        result = {
            "ok": contract["status"] != "blocked",
            "session_dir": str(session_dir),
            "question_parse": str(parse_path),
            "stage_contract": str(stage_path),
            "parse_status": payload["status"],
            "stage_status": contract["status"],
            "confidence": payload["confidence"],
            "unconfirmed_items": payload["unconfirmed_items"],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else str(stage_path))
        return 0 if result["ok"] else 3
    except Exception as exc:  # noqa: BLE001 - CLI returns fail-closed JSON
        error = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
