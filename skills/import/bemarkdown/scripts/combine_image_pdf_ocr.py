#!/usr/bin/env python
"""Combine OCR candidates for image-only or formula-risk PDFs.

This script is an orchestrator, not a final authority. It collects marker and
local OCR/VLM candidates, flags risky blocks, and writes review tasks for an
agent VLM to resolve. A document is import-ready only when all conflicts are
resolved and quality gates pass.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import fitz  # PyMuPDF
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit("PyMuPDF is required: install/import fitz before combining PDF OCR") from exc

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
KB_ROOT = SKILL_DIR.parents[2]
DEFAULT_TRANSCRIBER_DIR = SKILL_DIR.parent / "chinese-handwriting-formula-transcriber"
DEFAULT_PRIVATE_OCR_ROOT = Path.home() / "Desktop" / "ocr_private_runs" / "chinese_handwriting_formula_transcriber"


@dataclass
class Candidate:
    engine: str
    page: int | None
    path: str
    text: str
    metadata: dict[str, Any]
    region_id: str | None = None
    source_kind: str = "text"
    status: str = "machine_candidate"
    confidence: float | None = None
    quality_score: float | None = None
    risk_flags: list[str] | None = None

    def to_manifest(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "page": self.page,
            "region_id": self.region_id,
            "source_kind": self.source_kind,
            "path": self.path,
            "chars": len(self.text),
            "nonblank_lines": len(nonblank_lines(self.text)),
            "status": self.status,
            "confidence": self.confidence,
            "quality_score": self.quality_score,
            "risk_flags": self.risk_flags or [],
            "metadata": self.metadata,
        }


def nonblank_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def parse_page_range(value: str | None, page_count: int) -> list[int]:
    if not value:
        return list(range(page_count))
    pages: set[int] = set()
    for raw_part in re.split(r"[,，]", value):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s), int(end_s)
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    invalid = [page for page in pages if page < 0 or page >= page_count]
    if invalid:
        raise ValueError(f"page range contains invalid zero-based pages: {invalid}; page_count={page_count}")
    return sorted(pages)


def diagnose_pdf(pdf: Path) -> dict[str, Any]:
    sys.path.insert(0, str(SCRIPT_DIR))
    from diagnose_pdf import diagnose_pdf as _diagnose_pdf  # noqa: PLC0415

    return _diagnose_pdf(pdf)


def render_pages(pdf: Path, pages: list[int], output_dir: Path, zoom: float) -> dict[int, Path]:
    render_dir = output_dir / "rendered_pages"
    render_dir.mkdir(parents=True, exist_ok=True)
    rendered: dict[int, Path] = {}
    doc = fitz.open(pdf)
    try:
        matrix = fitz.Matrix(zoom, zoom)
        for page_index in pages:
            page = doc[page_index]
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            path = render_dir / f"page_{page_index + 1:03d}.png"
            pix.save(path)
            rendered[page_index + 1] = path
    finally:
        doc.close()
    return rendered


def copy_pdf_to_ascii_staging(pdf: Path, output_dir: Path) -> Path:
    staged = output_dir / "source.pdf"
    staged.parent.mkdir(parents=True, exist_ok=True)
    if staged.resolve() != pdf.resolve():
        shutil.copy2(pdf, staged)
    return staged


def run_marker(pdf: Path, output_dir: Path, page_range: str | None, force_ocr: str, use_llm: bool) -> dict[str, Any]:
    marker_out = output_dir / "marker_candidate"
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "convert_pdf_with_marker.py"),
        str(pdf),
        str(marker_out),
        "--force-ocr",
        force_ocr,
        "--json",
    ]
    if page_range:
        cmd.extend(["--page-range", page_range])
    if use_llm:
        cmd.append("--use-llm")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    start = time.time()
    proc = subprocess.run(cmd, cwd=str(KB_ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    run_log = {
        "command": cmd,
        "returncode": proc.returncode,
        "elapsed_seconds": round(time.time() - start, 2),
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }
    write_json(output_dir / "marker_run_log.json", run_log)
    if proc.returncode != 0:
        return {"ok": False, "output_dir": str(marker_out), "run_log": run_log}
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        summary_path = marker_out / "conversion_summary.json"
        parsed = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    return {"ok": True, "output_dir": str(marker_out), "summary": parsed, "run_log": run_log}


def find_markdown_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.md") if path.is_file())


def collect_marker_candidates(
    marker_output_dir: Path,
    covered_pages: list[int] | None = None,
) -> tuple[list[Candidate], dict[str, Any]]:
    candidates: list[Candidate] = []
    summary_path = marker_output_dir / "conversion_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    for md in find_markdown_files(marker_output_dir):
        text = md.read_text(encoding="utf-8", errors="replace")
        pages = sorted(set(covered_pages or []))
        candidates.append(
            Candidate(
                engine="marker",
                page=pages[0] if len(pages) == 1 else None,
                path=str(md),
                text=text,
                metadata={
                    "source": "marker_markdown",
                    "picture_intentionally_omitted_count": text.lower().count("picture intentionally omitted"),
                    "covered_pages": pages,
                },
                source_kind="page_layout" if len(pages) == 1 else "document_layout",
            )
        )
    return candidates, summary


def parse_local_output_arg(values: list[str]) -> dict[int, Path]:
    result: dict[int, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--local-output-root must use PAGE=PATH, e.g. 1=C:/path/to/root")
        page_s, path_s = value.split("=", 1)
        result[int(page_s)] = Path(path_s).resolve()
    return result


def create_transcriber_task(
    task_id: str,
    source_image: Path,
    output_root: Path,
    private_ocr_root: Path,
) -> Path:
    tasks = private_ocr_root / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    card = {
        "task_id": task_id,
        "source_image": str(source_image),
        "output_root": str(output_root),
        "notes": "BeMarkdown combined image PDF OCR candidate",
    }
    path = tasks / f"{task_id}.json"
    write_json(path, card)
    return path


def build_transcriber_command(
    task_id: str,
    transcriber_dir: Path,
    command_template: str | None = None,
) -> list[str]:
    template = command_template or os.environ.get("BEMARKDOWN_TRANSCRIBER_COMMAND")
    if template:
        rendered = template.format(task_id=task_id, transcriber_dir=str(transcriber_dir))
        return shlex.split(rendered, posix=os.name != "nt")

    script = transcriber_dir / "scripts" / "transcribe_image.ps1"
    powershell = os.environ.get("POWERSHELL_EXE") or shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        raise RuntimeError(
            "no PowerShell runtime found for transcriber; set BEMARKDOWN_TRANSCRIBER_COMMAND "
            "to a cross-platform wrapper command containing {task_id}"
        )
    return [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-TaskId", task_id]


def run_local_transcriber(
    task_id: str,
    transcriber_dir: Path,
    command_template: str | None = None,
) -> dict[str, Any]:
    try:
        cmd = build_transcriber_command(task_id, transcriber_dir, command_template)
    except RuntimeError as exc:
        return {"command": [], "returncode": -1, "stdout_tail": "", "stderr_tail": str(exc)}
    proc = subprocess.run(cmd, cwd=str(KB_ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {
        "command": cmd,
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }


def load_json_if_exists(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def clean_candidate_text(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] in "'\"" and text[-1] == text[0]:
        text = text[1:-1]
    return text.replace("\\n", "\n").strip()


def to_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def average_numeric(values: Any) -> float | None:
    if not isinstance(values, list):
        return None
    nums: list[float] = []
    for value in values:
        parsed = to_optional_float(value)
        if parsed is not None:
            nums.append(parsed)
    if not nums:
        return None
    return round(sum(nums) / len(nums), 4)


def extract_values_by_keys(data: Any, keys: set[str]) -> list[str]:
    found: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key in keys:
                if isinstance(value, str) and value.strip():
                    found.append(clean_candidate_text(value))
                elif isinstance(value, list):
                    found.extend(clean_candidate_text(item) for item in value if clean_candidate_text(item))
            else:
                found.extend(extract_values_by_keys(value, keys))
    elif isinstance(data, list):
        for item in data:
            found.extend(extract_values_by_keys(item, keys))
    return found


def extract_strings_from_json(data: Any) -> list[str]:
    return extract_values_by_keys(data, {"rec_text", "rec_texts", "text", "texts", "joined_text"})


def extract_formulas_from_json(data: Any) -> list[str]:
    values = extract_values_by_keys(data, {"rec_formula", "rec_formulas", "formula", "formulas", "latex"})
    return [value for value in values if value]


def extract_texteller_candidates(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return []
    values = extract_values_by_keys(data, {"latex"})
    for stdout in extract_values_by_keys(data, {"stdout_tail", "stdout"}):
        match = re.search(r"Predicted LaTeX:\s*```(.*?)```", stdout, re.S)
        if match:
            values.append(clean_candidate_text(match.group(1)))
        elif stdout.strip():
            values.append(clean_candidate_text(stdout))
    return list(dict.fromkeys(value for value in values if value))


def add_candidate(
    candidates: list[Candidate],
    *,
    engine: str,
    page: int,
    path: Path,
    text: Any,
    source_kind: str,
    metadata: dict[str, Any] | None = None,
    region_id: str | None = None,
    status: str = "machine_candidate",
    confidence: float | None = None,
    quality_score: float | None = None,
    risk_flags: list[str] | None = None,
) -> None:
    cleaned = clean_candidate_text(text)
    if not cleaned:
        return
    candidates.append(
        Candidate(
            engine=engine,
            page=page,
            path=str(path),
            text=cleaned,
            metadata=metadata or {},
            region_id=region_id,
            source_kind=source_kind,
            status=status,
            confidence=confidence,
            quality_score=quality_score,
            risk_flags=risk_flags or [],
        )
    )


def result_records(data: Any) -> list[dict[str, Any]]:
    values = data if isinstance(data, list) else [data]
    records: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        result = value.get("res") if isinstance(value.get("res"), dict) else value
        if isinstance(result, dict):
            records.append(result)
    return records


def collect_structured_result_candidates(root: Path, page: int) -> list[Candidate]:
    """Read the transcriber canonical JSON before compatibility file scans."""
    path = root / "combined_result.json"
    combined = load_json_if_exists(path)
    if not isinstance(combined, dict):
        return []
    candidates: list[Candidate] = []
    for engine_row in combined.get("engines") or []:
        if not isinstance(engine_row, dict):
            continue
        engine_group = str(engine_row.get("engine") or "unknown")
        parsed = engine_row.get("parsed") if isinstance(engine_row.get("parsed"), dict) else {}
        for step_index, step in enumerate(parsed.get("steps") or []):
            if not isinstance(step, dict) or not step.get("ok"):
                continue
            name = str(step.get("name") or "")
            data = step.get("data")
            pointer = f"engines[{engine_group}].steps[{step_index}]"
            metadata = {"source": "combined_result_json", "step": name, "json_pointer": pointer}

            if "paddleocr_vl" in name:
                blocks: list[dict[str, Any]] = []
                for record in result_records(data):
                    blocks.extend(item for item in record.get("parsing_res_list") or [] if isinstance(item, dict))
                blocks.sort(key=lambda item: int(item.get("block_order") or 0))
                text = "\n\n".join(clean_candidate_text(item.get("block_content")) for item in blocks if clean_candidate_text(item.get("block_content")))
                if text:
                    add_candidate(
                        candidates,
                        engine="paddleocr_vl",
                        page=page,
                        path=path,
                        text=text,
                        source_kind="whole_page_layout",
                        metadata={**metadata, "block_count": len(blocks), "block_labels": [item.get("block_label") for item in blocks]},
                    )
            elif "paddlex_ocr" in name or "pp_ocr" in name:
                for record in result_records(data):
                    texts = [clean_candidate_text(value) for value in record.get("rec_texts") or [] if clean_candidate_text(value)]
                    if texts:
                        add_candidate(
                            candidates,
                            engine="pp_ocr",
                            page=page,
                            path=path,
                            text="\n".join(texts),
                            source_kind="whole_page_text_lines",
                            metadata={**metadata, "string_count": len(texts)},
                            confidence=average_numeric(record.get("rec_scores")),
                        )
            elif "formulanet" in name:
                for record in result_records(data):
                    formula = clean_candidate_text(record.get("rec_formula"))
                    if formula:
                        add_candidate(
                            candidates,
                            engine="pp_formulanet",
                            page=page,
                            path=path,
                            text=formula,
                            source_kind="whole_page_formula",
                            metadata=metadata,
                            risk_flags=["formula_candidate"],
                        )
            elif "texteller" in name:
                for value in extract_texteller_candidates(data):
                    add_candidate(
                        candidates,
                        engine="texteller",
                        page=page,
                        path=path,
                        text=value,
                        source_kind="whole_page_formula",
                        metadata=metadata,
                        risk_flags=["formula_candidate"],
                    )
    return candidates


def collect_whole_page_candidates(root: Path, page: int) -> list[Candidate]:
    candidates: list[Candidate] = []
    paddle_result = load_json_if_exists(root / "paddle" / "paddle_result.json")
    formula_result = load_json_if_exists(root / "formula" / "formula_result.json")

    vl_md = sorted((root / "paddle" / "paddleocr_vl").glob("*.md"))
    for md in vl_md:
        add_candidate(
            candidates,
            engine="paddleocr_vl",
            page=page,
            path=md,
            text=md.read_text(encoding="utf-8", errors="replace"),
            source_kind="whole_page_layout",
            metadata={"source": "paddleocr_vl_markdown"},
        )

    ocr_jsons = sorted((root / "paddle" / "paddlex_ocr").glob("*_res.json"))
    for path in ocr_jsons:
        data = load_json_if_exists(path)
        texts = extract_strings_from_json(data)
        if texts:
            add_candidate(
                candidates,
                engine="pp_ocr",
                page=page,
                path=path,
                text="\n".join(texts),
                source_kind="whole_page_text_lines",
                metadata={"source": "paddlex_ocr_json", "string_count": len(texts)},
            )

    for formula in extract_formulas_from_json(paddle_result):
        add_candidate(
            candidates,
            engine="pp_formulanet",
            page=page,
            path=root / "paddle" / "paddle_result.json",
            text=formula,
            source_kind="whole_page_formula",
            metadata={"source": "paddle_result_json"},
            risk_flags=["formula_candidate"],
        )

    texteller = root / "formula" / "texteller_result.txt"
    if texteller.exists():
        add_candidate(
            candidates,
            engine="texteller",
            page=page,
            path=texteller,
            text=texteller.read_text(encoding="utf-8", errors="replace"),
            source_kind="whole_page_formula",
            metadata={"source": "texteller_whole_page"},
            risk_flags=["formula_candidate"],
        )
    for value in extract_texteller_candidates(formula_result):
        add_candidate(
            candidates,
            engine="texteller",
            page=page,
            path=root / "formula" / "formula_result.json",
            text=value,
            source_kind="whole_page_formula",
            metadata={"source": "formula_result_json"},
            risk_flags=["formula_candidate"],
        )
    return candidates


def collect_region_candidates(root: Path, page: int) -> list[Candidate]:
    region_root = root / "region_ocr"
    combined = load_json_if_exists(region_root / "region_combined_result.json")
    if not isinstance(combined, dict):
        return []
    candidates: list[Candidate] = []
    paddle_regions = {
        str(region.get("id")): region
        for region in ((combined.get("paddle_regions") or {}).get("regions") or [])
        if isinstance(region, dict)
    }
    formula_regions = {
        str(region.get("id")): region
        for region in ((combined.get("formula_regions") or {}).get("regions") or [])
        if isinstance(region, dict)
    }
    manifest_regions = [region for region in combined.get("regions") or [] if isinstance(region, dict)]
    for region in manifest_regions:
        region_id = str(region.get("id") or "")
        label = str(region.get("label") or region_id)
        paddle = paddle_regions.get(region_id) or {}
        ocr = paddle.get("paddlex_ocr") or {}
        texts = [clean_candidate_text(text) for text in ocr.get("texts") or [] if clean_candidate_text(text)]
        if texts:
            add_candidate(
                candidates,
                engine="pp_ocr",
                page=page,
                path=region_root / "paddle" / "paddle_regions_result.json",
                text="\n".join(texts),
                source_kind="region_text_lines",
                metadata={"region_label": label, "bbox": region.get("bbox"), "scores": ocr.get("scores")},
                region_id=region_id,
                confidence=average_numeric(ocr.get("scores")),
            )
        for formula in (paddle.get("pp_formulanet") or {}).get("formulas") or []:
            add_candidate(
                candidates,
                engine="pp_formulanet",
                page=page,
                path=region_root / "paddle" / "paddle_regions_result.json",
                text=formula,
                source_kind="region_formula",
                metadata={"region_label": label, "bbox": region.get("bbox")},
                region_id=region_id,
                risk_flags=["formula_candidate"],
            )
        formula = formula_regions.get(region_id) or {}
        texteller = formula.get("texteller") or {}
        if texteller.get("status") == "ok" and texteller.get("latex"):
            add_candidate(
                candidates,
                engine="texteller",
                page=page,
                path=region_root / "formula" / "formula_regions_result.json",
                text=texteller.get("latex"),
                source_kind="region_formula",
                metadata={"region_label": label, "bbox": region.get("bbox")},
                region_id=region_id,
                risk_flags=["formula_candidate"],
            )
    return candidates


def collect_auto_transcript_candidates(root: Path, page: int) -> list[Candidate]:
    transcript = load_json_if_exists(root / "auto_ocr" / "auto_transcript.json")
    if not isinstance(transcript, dict):
        return []
    candidates: list[Candidate] = []
    for region in transcript.get("regions") or []:
        if not isinstance(region, dict):
            continue
        status = str(region.get("status") or "machine_abstain")
        text = region.get("final_candidate")
        region_id = str(region.get("region_id") or "")
        add_candidate(
            candidates,
            engine="auto_transcript",
            page=page,
            path=root / "auto_ocr" / "auto_transcript.json",
            text=text,
            source_kind="auto_region_final_candidate",
            metadata={
                "question_id": region.get("question_id"),
                "region_type": region.get("region_type"),
                "parsed_fields": region.get("parsed_fields") or {},
            },
            region_id=region_id,
            status=status,
            confidence=to_optional_float(region.get("confidence")),
            quality_score=to_optional_float(region.get("quality_score")),
            risk_flags=[str(flag) for flag in region.get("risk_flags") or []],
        )
        if status == "machine_abstain":
            candidates.append(
                Candidate(
                    engine="auto_transcript",
                    page=page,
                    path=str(root / "auto_ocr" / "auto_transcript.json"),
                    text="",
                    metadata={"question_id": region.get("question_id"), "region_type": region.get("region_type")},
                    region_id=region_id,
                    source_kind="auto_region_abstain",
                    status="machine_abstain",
                    confidence=to_optional_float(region.get("confidence")),
                    quality_score=to_optional_float(region.get("quality_score")),
                    risk_flags=[str(flag) for flag in region.get("risk_flags") or ["machine_abstain"]],
                )
            )
    return candidates


def collect_local_candidates(root: Path, page: int, include_auto_transcript: bool = False) -> list[Candidate]:
    structured = collect_structured_result_candidates(root, page)
    structured_engines = {item.engine for item in structured}
    compatibility = [
        item for item in collect_whole_page_candidates(root, page) if item.engine not in structured_engines
    ]
    candidates = structured + compatibility
    candidates.extend(collect_region_candidates(root, page))
    if include_auto_transcript:
        candidates.extend(collect_auto_transcript_candidates(root, page))
    return candidates


def candidate_score(candidate: Candidate) -> int:
    text = candidate.text
    score = len(nonblank_lines(text)) * 2 + min(len(text) // 20, 200)
    # Prefer structured whole-page/region OCR candidates; raw line text and
    # standalone formula engines are evidence, not usually final Markdown shape.
    score += {
        "paddleocr_vl": 90,
        "auto_transcript": 55,
        "pp_formulanet": 45,
        "texteller": 42,
        "marker": 25,
        "pp_ocr": -45,
    }.get(candidate.engine, 0)
    score += {
        "machine_final": 80,
        "machine_candidate": 15,
        "machine_abstain": -160,
    }.get(candidate.status, 0)
    if candidate.confidence is not None:
        score += int(max(0.0, min(1.0, candidate.confidence)) * 30)
    if candidate.quality_score is not None:
        score += int(max(0.0, min(1.0, candidate.quality_score)) * 30)
    if candidate.region_id:
        score -= 15
    if "formula_candidate" in (candidate.risk_flags or []):
        score -= 10
    score += len(re.findall(r"(?<!\d)([1-9]|1[0-9]|2[0-9])[\.．、]", text)) * 8
    score += len(re.findall(r"[ABCD][\.．、]", text)) * 4
    score -= text.lower().count("picture intentionally omitted") * 50
    score -= text.count("�") * 10
    score -= len(re.findall(r"(style=\"text-align:\s*src=|牧師|粥|I_6|\\\\bar\{u\})", text)) * 20
    return score


def choose_candidate(page: int, candidates: list[Candidate]) -> Candidate | None:
    page_candidates = [item for item in candidates if item.page == page]
    if page_candidates:
        return max(page_candidates, key=candidate_score)
    marker_candidates = [item for item in candidates if item.engine == "marker" and item.page == page]
    if marker_candidates:
        return max(marker_candidates, key=candidate_score)
    return None


def marker_candidates_for_page(candidates: list[Candidate], page: int) -> list[Candidate]:
    rows: list[Candidate] = []
    for item in candidates:
        if item.engine != "marker":
            continue
        covered = item.metadata.get("covered_pages") or []
        if item.page == page or page in covered:
            rows.append(item)
    return rows


def risk_flags(text: str) -> list[str]:
    flags: list[str] = []
    if not text.strip():
        flags.append("empty_text")
    if text.lower().count("picture intentionally omitted"):
        flags.append("picture_omitted")
    if re.search(r"(style=\"text-align:\s*src=|牧師|粥|I_6|\\\\bar\{u\}|�)", text):
        flags.append("ocr_garbled")
    if re.search(r"(\\frac|\\sqrt|\\vec|\\rightharpoonup|\$|[_^]|核|衰变|Ra|Rn|β|alpha|α)", text):
        flags.append("formula_or_nuclear_symbol")
    if re.search(r"(如图|图像|装置|电路|曲线|实验|表格)", text):
        flags.append("diagram_or_table_context")
    if len(nonblank_lines(text)) < 8:
        flags.append("short_page_candidate")
    return sorted(set(flags))


def build_conflicts(pages: list[int], candidates: list[Candidate], rendered: dict[int, Path], vlm_enabled: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conflicts: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    by_page: dict[int, list[Candidate]] = {}
    for candidate in candidates:
        if candidate.page is not None:
            by_page.setdefault(candidate.page, []).append(candidate)

    for page_index in pages:
        page = page_index + 1
        page_candidates = by_page.get(page, [])
        marker = marker_candidates_for_page(candidates, page)
        selected = choose_candidate(page, candidates)
        candidate_flags = risk_flags(selected.text if selected else "")
        page_conflict_types = set(candidate_flags)
        if marker:
            marker_flags = set(risk_flags(marker[0].text))
            page_conflict_types.update(marker_flags & {"picture_omitted", "ocr_garbled", "empty_text"})
        if len(page_candidates) >= 2:
            scores = sorted(candidate_score(item) for item in page_candidates)
            if scores[-1] - scores[0] > 60:
                page_conflict_types.add("candidate_disagreement")
        if any(item.status == "machine_abstain" for item in page_candidates):
            page_conflict_types.add("machine_abstain")
        if any(item.status == "machine_candidate" for item in page_candidates):
            page_conflict_types.add("machine_candidate_review")
        if any((item.confidence is not None and item.confidence < 0.65) or (item.quality_score is not None and item.quality_score < 0.65) for item in page_candidates):
            page_conflict_types.add("low_confidence_candidate")
        if any(item.region_id for item in page_candidates):
            page_conflict_types.add("region_candidate")
        if any(item.source_kind.endswith("formula") or "formula_candidate" in (item.risk_flags or []) for item in page_candidates):
            page_conflict_types.add("formula_candidate")
        risk_flag_values = {flag for item in page_candidates for flag in (item.risk_flags or [])}
        if risk_flag_values:
            page_conflict_types.add("candidate_risk_flags")
        if not page_candidates:
            page_conflict_types.add("missing_local_candidate")
        if page_conflict_types:
            block_id = f"p{page:03d}_combined_review"
            conflict = {
                "block_id": block_id,
                "page": page,
                "status": "unresolved",
                "conflict_types": sorted(page_conflict_types),
                "source_image": str(rendered.get(page, "")),
                "selected_candidate": selected.to_manifest() if selected else None,
                "candidate_refs": [item.to_manifest() for item in page_candidates],
                "marker_ref": marker[0].to_manifest() if marker else None,
                "marker_refs": [item.to_manifest() for item in marker],
            }
            conflicts.append(conflict)
            if vlm_enabled:
                candidate_paths = [item.path for item in page_candidates]
                for marker_item in marker:
                    if marker_item.path not in candidate_paths:
                        candidate_paths.append(marker_item.path)
                tasks.append(
                    {
                        "block_id": block_id,
                        "page": page,
                        "source_image": str(rendered.get(page, "")),
                        "conflict_types": sorted(page_conflict_types),
                        "candidate_paths": candidate_paths,
                        "instruction": (
                            "Inspect the source image and OCR candidates. Return corrected Markdown for this page/block, "
                            "preserving physics meaning, formulas as LaTeX, and necessary diagram references. "
                            "Do not guess unreadable content; mark unresolved if visual evidence is insufficient."
                        ),
                        "expected_result_schema": {
                            "block_id": block_id,
                            "status": "resolved|unresolved",
                            "corrected_markdown": "string",
                            "evidence": "short visual evidence notes",
                            "confidence": "0.0-1.0",
                        },
                    }
                )
    return conflicts, tasks


def load_vlm_results(path: Path | None) -> dict[str, dict[str, Any]]:
    if not path or not path.exists():
        return {}
    results: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        block_id = str(item.get("block_id", ""))
        if block_id:
            results[block_id] = item
    return results


def build_model_candidate_matrix(pages: list[int], candidates: list[Candidate]) -> dict[str, Any]:
    matrix: dict[str, Any] = {}
    for page_index in pages:
        page = page_index + 1
        page_candidates = [item for item in candidates if item.page == page]
        engines: dict[str, Any] = {}
        for item in page_candidates:
            entry = engines.setdefault(
                item.engine,
                {
                    "candidate_count": 0,
                    "statuses": {},
                    "source_kinds": {},
                    "risk_flags": {},
                    "region_ids": [],
                    "max_confidence": None,
                    "max_quality_score": None,
                },
            )
            entry["candidate_count"] += 1
            entry["statuses"][item.status] = entry["statuses"].get(item.status, 0) + 1
            entry["source_kinds"][item.source_kind] = entry["source_kinds"].get(item.source_kind, 0) + 1
            for flag in item.risk_flags or []:
                entry["risk_flags"][flag] = entry["risk_flags"].get(flag, 0) + 1
            if item.region_id and item.region_id not in entry["region_ids"]:
                entry["region_ids"].append(item.region_id)
            if item.confidence is not None:
                entry["max_confidence"] = max(entry["max_confidence"] or 0.0, item.confidence)
            if item.quality_score is not None:
                entry["max_quality_score"] = max(entry["max_quality_score"] or 0.0, item.quality_score)
        matrix[str(page)] = {
            "candidate_count": len(page_candidates),
            "engine_count": len(engines),
            "engines": engines,
            "needs_vlm_review": any(
                item.status != "machine_final"
                or item.region_id
                or item.risk_flags
                or (item.confidence is not None and item.confidence < 0.65)
                or (item.quality_score is not None and item.quality_score < 0.65)
                for item in page_candidates
            ),
        }
    return matrix


def build_engine_status(local_roots: dict[int, Path]) -> dict[str, Any]:
    status: dict[str, Any] = {}
    for page, root in sorted(local_roots.items()):
        combined = load_json_if_exists(root / "combined_result.json")
        region_combined = load_json_if_exists(root / "region_ocr" / "region_combined_result.json")
        auto_transcript = load_json_if_exists(root / "auto_ocr" / "auto_transcript.json")
        page_status: dict[str, Any] = {
            "output_root": str(root),
            "whole_page": {"exists": isinstance(combined, dict), "ok": None, "engines": {}},
            "regions": {"exists": isinstance(region_combined, dict), "ok": None},
            "auto_transcript": {"exists": isinstance(auto_transcript, dict), "status": None},
        }
        if isinstance(combined, dict):
            page_status["whole_page"]["ok"] = any(
                isinstance(engine, dict) and isinstance(engine.get("parsed"), dict) and engine["parsed"].get("ok")
                for engine in combined.get("engines") or []
            )
            for engine in combined.get("engines") or []:
                if isinstance(engine, dict):
                    name = str(engine.get("engine") or engine.get("name") or "unknown")
                    parsed = engine.get("parsed") if isinstance(engine.get("parsed"), dict) else {}
                    page_status["whole_page"]["engines"][name] = {
                        "exit_code": engine.get("exit_code"),
                        "ok": parsed.get("ok"),
                        "step_names": [
                            step.get("name")
                            for step in parsed.get("steps") or []
                            if isinstance(step, dict) and step.get("name")
                        ],
                    }
        if isinstance(region_combined, dict):
            page_status["regions"]["ok"] = region_combined.get("ok")
        if isinstance(auto_transcript, dict):
            page_status["auto_transcript"]["status"] = auto_transcript.get("status")
        status[str(page)] = page_status
    return status


def write_merged_draft(path: Path, pages: list[int], candidates: list[Candidate], conflicts: list[dict[str, Any]], vlm_results: dict[str, dict[str, Any]]) -> None:
    conflict_by_page = {int(item["page"]): item for item in conflicts}
    chunks = [
        "<!-- COMBINED_OCR_DRAFT: not import-ready until final_quality_report.ok_for_import=true -->",
        "",
    ]
    for page_index in pages:
        page = page_index + 1
        chunks.append(f"\n\n<!-- page {page} -->\n")
        conflict = conflict_by_page.get(page)
        result = vlm_results.get(conflict["block_id"]) if conflict else None
        if result and result.get("status") == "resolved" and str(result.get("corrected_markdown", "")).strip():
            chunks.append(str(result["corrected_markdown"]).strip())
            continue
        selected = choose_candidate(page, candidates)
        if selected:
            chunks.append(f"<!-- selected_candidate={selected.engine} score={candidate_score(selected)} -->\n")
            chunks.append(selected.text.strip())
        else:
            chunks.append("<!-- unresolved: no candidate text for this page -->")
    path.write_text("\n".join(chunks).strip() + "\n", encoding="utf-8")


def broken_image_links(markdown_path: Path) -> list[str]:
    text = markdown_path.read_text(encoding="utf-8", errors="replace")
    links = re.findall(r"!\[[^\]]*]\(([^)]+)\)", text)
    broken: list[str] = []
    for link in links:
        if re.match(r"https?://", link):
            continue
        if not (markdown_path.parent / link).exists():
            broken.append(link)
    return broken


def build_quality_report(
    diagnosis: dict[str, Any],
    selected_page_count: int,
    merged_draft: Path,
    conflicts: list[dict[str, Any]],
    vlm_results: dict[str, dict[str, Any]],
    marker_summary: dict[str, Any],
    candidates: list[Candidate],
    model_candidate_matrix: dict[str, Any],
) -> dict[str, Any]:
    text = merged_draft.read_text(encoding="utf-8", errors="replace") if merged_draft.exists() else ""
    unresolved = [
        item
        for item in conflicts
        if vlm_results.get(item["block_id"], {}).get("status") != "resolved"
    ]
    issues: list[dict[str, Any]] = []
    if unresolved:
        issues.append({"code": "unresolved_conflicts", "count": len(unresolved)})
    if text.lower().count("picture intentionally omitted"):
        issues.append({"code": "picture_intentionally_omitted", "count": text.lower().count("picture intentionally omitted")})
    if text.count("�"):
        issues.append({"code": "replacement_characters", "count": text.count("�")})
    min_expected_lines = max(10, selected_page_count * 5)
    if len(nonblank_lines(text)) < min_expected_lines:
        issues.append({"code": "too_few_nonblank_lines", "count": len(nonblank_lines(text))})
    broken = broken_image_links(merged_draft)
    if broken:
        issues.append({"code": "broken_image_links", "items": broken})
    if text.count("$") % 2:
        issues.append({"code": "unbalanced_inline_latex_dollars", "count": text.count("$")})
    marker_omitted = marker_summary.get("picture_intentionally_omitted_count")
    if marker_omitted:
        issues.append({"code": "marker_picture_omitted", "count": marker_omitted})
    if not model_candidate_matrix:
        issues.append({"code": "model_candidate_matrix_missing"})

    return {
        "ok_for_import": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "unresolved_conflict_count": len(unresolved),
        "conflict_count": len(conflicts),
        "candidate_count": len(candidates),
        "model_candidate_matrix_present": bool(model_candidate_matrix),
        "model_candidate_matrix_summary": {
            "page_count": len(model_candidate_matrix),
            "pages_without_local_candidates": [
                page for page, item in model_candidate_matrix.items() if not item.get("candidate_count")
            ],
            "pages_needing_vlm_review": [
                page for page, item in model_candidate_matrix.items() if item.get("needs_vlm_review")
            ],
        },
        "diagnosis": {
            "classification": diagnosis.get("classification"),
            "recommendation": diagnosis.get("recommendation"),
            "page_count": diagnosis.get("page_count"),
            "summary": diagnosis.get("summary"),
        },
        "merged_draft": str(merged_draft),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Combine marker, local OCR, and VLM-review candidates for image PDFs.")
    parser.add_argument("pdf", help="PDF to process")
    parser.add_argument("output_dir", help="Output directory")
    parser.add_argument("--page-range", help="Zero-based pages, e.g. 0-1,3. Default: all pages")
    parser.add_argument("--render-zoom", type=float, default=3.0)
    parser.add_argument("--force-ocr", choices=["auto", "always", "never"], default="auto")
    parser.add_argument("--use-marker-llm", action="store_true")
    parser.add_argument("--skip-marker", action="store_true", help="Do not run marker; use --marker-output-dir if available")
    parser.add_argument("--marker-output-dir", help="Existing marker output directory containing conversion_summary.json or marker_output/")
    parser.add_argument("--run-local-transcriber", action="store_true", help="Run chinese-handwriting-formula-transcriber on rendered pages")
    parser.add_argument("--local-page-limit", type=int, default=0, help="Limit local transcriber pages; 0 means no limit")
    parser.add_argument("--local-output-root", action="append", default=[], help="Reuse local OCR output as PAGE=PATH, where PAGE is 1-based")
    parser.add_argument("--private-ocr-root", help="Private transcriber root; defaults to BEMARKDOWN_PRIVATE_OCR_ROOT or the local Desktop root")
    parser.add_argument("--transcriber-dir", help="Transcriber skill directory; defaults to BEMARKDOWN_TRANSCRIBER_DIR or the sibling canonical skill")
    parser.add_argument("--transcriber-command", help="Command template containing {task_id}; overrides PowerShell wrapper discovery")
    parser.add_argument("--include-auto-transcript", action="store_true", help="Also read auto_ocr/auto_transcript.json from explicit answer-card/region OCR runs")
    parser.add_argument("--no-vlm-tasks", action="store_true", help="Do not write vlm_review_tasks.jsonl")
    parser.add_argument("--vlm-results", help="Optional JSONL with resolved VLM review results")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    pdf = Path(args.pdf).resolve()
    if not pdf.exists():
        raise SystemExit(f"PDF not found: {pdf}")
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    staged_pdf = copy_pdf_to_ascii_staging(pdf, output_dir / "input")
    diagnosis = diagnose_pdf(staged_pdf)
    page_count = int(diagnosis.get("page_count") or 0)
    pages = parse_page_range(args.page_range, page_count)
    covered_pages = [page + 1 for page in pages]
    rendered = render_pages(staged_pdf, pages, output_dir, args.render_zoom)

    marker_info: dict[str, Any] = {"ok": False}
    marker_candidates: list[Candidate] = []
    marker_summary: dict[str, Any] = {}
    marker_output_dir = Path(args.marker_output_dir).resolve() if args.marker_output_dir else None
    if marker_output_dir:
        marker_candidates, marker_summary = collect_marker_candidates(marker_output_dir, covered_pages)
        marker_info = {"ok": True, "output_dir": str(marker_output_dir), "reused": True}
    elif not args.skip_marker:
        marker_info = run_marker(staged_pdf, output_dir, args.page_range, args.force_ocr, args.use_marker_llm)
        marker_output_dir = Path(marker_info["output_dir"])
        if marker_info.get("ok"):
            marker_candidates, marker_summary = collect_marker_candidates(marker_output_dir, covered_pages)
            marker_summary = marker_info.get("summary") or marker_summary

    local_roots = parse_local_output_arg(args.local_output_root)
    if args.run_local_transcriber:
        private_ocr_root = Path(
            args.private_ocr_root
            or os.environ.get("BEMARKDOWN_PRIVATE_OCR_ROOT")
            or DEFAULT_PRIVATE_OCR_ROOT
        ).expanduser().resolve()
        transcriber_dir = Path(
            args.transcriber_dir
            or os.environ.get("BEMARKDOWN_TRANSCRIBER_DIR")
            or DEFAULT_TRANSCRIBER_DIR
        ).expanduser().resolve()
        local_pages = [page + 1 for page in pages]
        if args.local_page_limit > 0:
            local_pages = local_pages[: args.local_page_limit]
        for page in local_pages:
            if page in local_roots:
                continue
            image = rendered.get(page)
            if not image:
                continue
            task_id = f"bemd_combined_{pdf.stem[:20].lower()}_p{page:03d}_{int(time.time())}"
            task_id = re.sub(r"[^a-z0-9_]+", "_", task_id)
            root = private_ocr_root / task_id
            task_card = create_transcriber_task(task_id, image, root, private_ocr_root)
            run_log = run_local_transcriber(task_id, transcriber_dir, args.transcriber_command)
            write_json(output_dir / "local_transcriber_runs" / f"page_{page:03d}.json", {"task_id": task_id, "task_card": str(task_card), "output_root": str(root), "run": run_log})
            if run_log["returncode"] == 0:
                local_roots[page] = root

    local_candidates: list[Candidate] = []
    for page, root in sorted(local_roots.items()):
        local_candidates.extend(collect_local_candidates(root, page, include_auto_transcript=args.include_auto_transcript))

    candidates = marker_candidates + local_candidates
    conflicts, vlm_tasks = build_conflicts(pages, candidates, rendered, not args.no_vlm_tasks)
    vlm_results = load_vlm_results(Path(args.vlm_results).resolve() if args.vlm_results else None)
    model_candidate_matrix = build_model_candidate_matrix(pages, local_candidates)
    engine_status = build_engine_status(local_roots)

    manifest = {
        "pdf": str(pdf),
        "staged_pdf": str(staged_pdf),
        "output_dir": str(output_dir),
        "diagnosis": diagnosis,
        "pages_zero_based": pages,
        "rendered_pages": {str(page): str(path) for page, path in rendered.items()},
        "marker": marker_info,
        "local_output_roots": {str(page): str(root) for page, root in sorted(local_roots.items())},
        "engine_status": engine_status,
        "model_candidate_matrix": model_candidate_matrix,
        "candidates": [candidate.to_manifest() for candidate in candidates],
    }
    write_json(output_dir / "candidate_manifest.json", manifest)
    write_jsonl(output_dir / "conflict_blocks.jsonl", conflicts)
    write_jsonl(output_dir / "vlm_review_tasks.jsonl", vlm_tasks)

    merged_draft = output_dir / "merged_draft.md"
    write_merged_draft(merged_draft, pages, candidates, conflicts, vlm_results)
    quality = build_quality_report(diagnosis, len(pages), merged_draft, conflicts, vlm_results, marker_summary, candidates, model_candidate_matrix)
    write_json(output_dir / "final_quality_report.json", quality)

    summary = {
        "ok": True,
        "output_dir": str(output_dir),
        "candidate_manifest": str(output_dir / "candidate_manifest.json"),
        "merged_draft": str(merged_draft),
        "conflict_blocks": str(output_dir / "conflict_blocks.jsonl"),
        "vlm_review_tasks": str(output_dir / "vlm_review_tasks.jsonl"),
        "final_quality_report": str(output_dir / "final_quality_report.json"),
        "ok_for_import": quality["ok_for_import"],
        "issue_count": quality["issue_count"],
        "unresolved_conflict_count": quality["unresolved_conflict_count"],
        "candidate_count": len(candidates),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"ok=true output_dir={output_dir}")
        print(f"ok_for_import={summary['ok_for_import']} issues={summary['issue_count']} unresolved={summary['unresolved_conflict_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
