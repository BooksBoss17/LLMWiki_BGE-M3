#!/usr/bin/env python
"""Run marker for image-only or formula-risk PDFs with explicit environment checks."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from diagnose_pdf import diagnose_pdf  # noqa: E402


class MarkerError(RuntimeError):
    """Expected marker wrapper failure."""


def run_capture(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def find_marker_exe(explicit: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_marker = os.environ.get("MARKER_EXE")
    if env_marker:
        candidates.append(Path(env_marker))
    which_marker = shutil.which("marker")
    if which_marker:
        candidates.append(Path(which_marker))
    candidates.append(Path.home() / "AppData/Local/Programs/Python/Python310/Scripts/marker.exe")

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.exists():
            return resolved
    raise MarkerError("marker executable not found; install marker-pdf in Python310 or set MARKER_EXE")


def python_for_marker(marker_exe: Path) -> Path | None:
    # Typical Windows layout: Python310/Scripts/marker.exe -> Python310/python.exe
    candidate = marker_exe.parent.parent / "python.exe"
    return candidate if candidate.exists() else None


def marker_help(marker_exe: Path) -> str:
    proc = run_capture([str(marker_exe), "--help"], timeout=90)
    if proc.returncode != 0:
        raise MarkerError(f"marker --help failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout + proc.stderr


def force_ocr_option(help_text: str) -> str:
    if "--PdfProvider_force_ocr" in help_text:
        return "--PdfProvider_force_ocr"
    if "--DocumentProvider_force_ocr" in help_text:
        return "--DocumentProvider_force_ocr"
    raise MarkerError("marker CLI does not expose PdfProvider_force_ocr/DocumentProvider_force_ocr")


def verify_cuda(marker_exe: Path) -> dict[str, Any]:
    py = python_for_marker(marker_exe)
    if py is not None:
        cmd = [
            str(py),
            "-c",
            (
                "import json, importlib.util, torch; "
                "print(json.dumps({"
                "'python': __import__('sys').executable, "
                "'marker': importlib.util.find_spec('marker') is not None, "
                "'torch': torch.__version__, "
                "'cuda': torch.cuda.is_available()"
                "}))"
            ),
        ]
    elif shutil.which("py"):
        cmd = [
            "py",
            "-3.10",
            "-c",
            (
                "import json, importlib.util, torch; "
                "print(json.dumps({"
                "'python': __import__('sys').executable, "
                "'marker': importlib.util.find_spec('marker') is not None, "
                "'torch': torch.__version__, "
                "'cuda': torch.cuda.is_available()"
                "}))"
            ),
        ]
    else:
        raise MarkerError("cannot find Python310 to verify marker and CUDA")

    proc = run_capture(cmd, timeout=60)
    if proc.returncode != 0:
        raise MarkerError(f"Python310 marker/CUDA check failed: {proc.stderr.strip() or proc.stdout.strip()}")
    try:
        info = json.loads(proc.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError as exc:
        raise MarkerError(f"could not parse marker/CUDA check output: {proc.stdout!r}") from exc
    if not info.get("marker"):
        raise MarkerError("Python310 cannot import marker")
    if not info.get("cuda"):
        raise MarkerError("torch.cuda.is_available() is false; refusing slow CPU marker conversion")
    return info


def select_force_ocr(mode: str, diagnosis: dict[str, Any]) -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    classification = diagnosis.get("classification")
    formula_risk = bool(diagnosis.get("summary", {}).get("formula_risk"))
    return classification in {"image_pdf", "mixed_pdf"} or formula_risk


def find_markdown_outputs(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.md") if path.is_file())


def count_picture_omitted(markdown_files: list[Path]) -> int:
    total = 0
    for path in markdown_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total += text.lower().count("picture intentionally omitted")
    return total


def convert(args: argparse.Namespace) -> dict[str, Any]:
    marker_exe = find_marker_exe(args.marker_exe)
    help_text = marker_help(marker_exe)
    force_option = force_ocr_option(help_text)
    cuda_info = verify_cuda(marker_exe)
    env_info = {
        "marker_exe": str(marker_exe),
        "force_ocr_option": force_option,
        "cuda": cuda_info,
    }
    if not args.pdf:
        if args.check_only:
            return {"ok": True, "check_only": True, "environment": env_info, "diagnosis": None}
        raise MarkerError("PDF is required unless --check-only is used")

    pdf = Path(args.pdf).resolve()
    if not pdf.exists():
        raise MarkerError(f"PDF does not exist: {pdf}")
    diagnosis = diagnose_pdf(pdf)
    force_ocr = select_force_ocr(args.force_ocr, diagnosis)
    if args.check_only:
        return {
            "ok": True,
            "check_only": True,
            "environment": env_info,
            "diagnosis": diagnosis,
        }

    if not args.output_dir:
        raise MarkerError("output_dir is required for conversion")
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    marker_input = output_dir / "marker_input"
    marker_output = output_dir / "marker_output"
    marker_input.mkdir(parents=True, exist_ok=True)
    marker_output.mkdir(parents=True, exist_ok=True)
    staged_pdf = marker_input / "source.pdf"
    shutil.copy2(pdf, staged_pdf)

    cmd = [
        str(marker_exe),
        str(marker_input),
        "--output_dir",
        str(marker_output),
        "--output_format",
        "markdown",
    ]
    if args.page_range:
        cmd.extend(["--page_range", args.page_range])
    if force_ocr:
        cmd.append(force_option)
    if args.use_llm:
        cmd.append("--use_llm")

    start = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    elapsed = round(time.time() - start, 2)
    if proc.returncode != 0:
        raise MarkerError(
            "marker conversion failed\n"
            f"command: {' '.join(cmd)}\n"
            f"stdout: {proc.stdout[-4000:]}\n"
            f"stderr: {proc.stderr[-4000:]}"
        )

    markdown_files = find_markdown_outputs(marker_output)
    omitted = count_picture_omitted(markdown_files)
    summary = {
        "ok": True,
        "pdf": str(pdf),
        "output_dir": str(output_dir),
        "diagnosis": diagnosis,
        "environment": env_info,
        "force_ocr": force_ocr,
        "page_range": args.page_range or "",
        "use_llm": bool(args.use_llm),
        "elapsed_seconds": elapsed,
        "markdown_files": [str(path) for path in markdown_files],
        "markdown_file_count": len(markdown_files),
        "picture_intentionally_omitted_count": omitted,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }
    (output_dir / "conversion_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert PDF with marker after BeMarkdown environment checks.")
    parser.add_argument("pdf", nargs="?", help="PDF file to convert or optionally diagnose during --check-only")
    parser.add_argument("output_dir", nargs="?", help="Output directory; required for conversion")
    parser.add_argument("--force-ocr", choices=["auto", "always", "never"], default="auto")
    parser.add_argument("--page-range", help="marker page_range, zero-based, e.g. 0-1")
    parser.add_argument("--marker-exe", help="Explicit marker executable path")
    parser.add_argument("--use-llm", action="store_true", help="Pass marker --use_llm")
    parser.add_argument("--check-only", action="store_true", help="Verify environment; diagnose too when a PDF is supplied")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args(argv)

    try:
        summary = convert(args)
    except MarkerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"ok={summary['ok']} output_dir={summary.get('output_dir', '')}")
        if summary.get("check_only"):
            print("check_only=true")
        else:
            print(f"markdown_files={summary.get('markdown_file_count', 0)} elapsed={summary.get('elapsed_seconds', 0)}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
