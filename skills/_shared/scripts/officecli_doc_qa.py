#!/usr/bin/env python
"""Controlled OfficeCLI loader and DOCX QA wrapper for LLMWiki outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from project_paths import resolve_path

KB_ROOT = resolve_path("project.root", start=Path(__file__))
OUTPUT_ROOT = resolve_path("workspace.output", start=KB_ROOT)
RUNTIME_ROOT = resolve_path("skills.model-tools.runtime", start=KB_ROOT) / "tools" / "officecli"
DEFAULT_VERSION = "v1.0.135"
ASSET_NAME = "officecli-win-x64.exe"
EXE_NAME = "officecli.exe" if os.name == "nt" else "officecli"
MIRROR_BASE = "https://d.officecli.ai"
GITHUB_BASE = "https://github.com/iOfficeAI/OfficeCLI"


class OfficeCliQAError(RuntimeError):
    pass


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def safe_env() -> dict[str, str]:
    env = os.environ.copy()
    env["OFFICECLI_NO_AUTO_INSTALL"] = "1"
    env["OFFICECLI_SKIP_UPDATE"] = "1"
    return env


def normalize_version(version: str) -> str:
    version = version.strip()
    return version if version.startswith("v") else f"v{version}"


def local_exe(version: str = DEFAULT_VERSION) -> Path:
    return RUNTIME_ROOT / normalize_version(version) / EXE_NAME


def is_windows_x64() -> bool:
    return os.name == "nt" and platform.machine().lower() in {"amd64", "x86_64"}


def run_officecli(exe: Path, args: list[str], *, timeout: int = 90) -> dict[str, Any]:
    if not exe.is_absolute():
        exe = exe.resolve()
    if not exe.exists():
        raise OfficeCliQAError(f"OfficeCLI executable not found: {exe}")
    proc = subprocess.run(
        [str(exe), *args],
        cwd=str(KB_ROOT),
        env=safe_env(),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    return {
        "cmd": [str(exe), *args],
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def parse_json_maybe(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def candidate_paths(version: str = DEFAULT_VERSION) -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    env_exe = os.environ.get("OFFICECLI_EXE")
    if env_exe:
        candidates.append(("env", Path(env_exe).expanduser()))
    default_local = local_exe(version)
    candidates.append(("local", default_local))
    if RUNTIME_ROOT.exists():
        for child in sorted(RUNTIME_ROOT.iterdir(), reverse=True):
            path = child / EXE_NAME
            if path != default_local:
                candidates.append(("local", path))
    path_exe = shutil.which("officecli")
    if path_exe:
        candidates.append(("path", Path(path_exe)))
    seen: set[str] = set()
    unique: list[tuple[str, Path]] = []
    for source, path in candidates:
        key = str(path.expanduser().resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append((source, path))
    return unique


def probe_candidate(source: str, path: Path) -> dict[str, Any]:
    path = path.expanduser()
    payload: dict[str, Any] = {"source": source, "path": str(path)}
    if not path.exists():
        payload["ok"] = False
        payload["reason"] = "missing"
        return payload
    try:
        result = run_officecli(path, ["--version"], timeout=15)
    except Exception as exc:
        payload["ok"] = False
        payload["reason"] = str(exc)
        return payload
    text = (result["stdout"] + " " + result["stderr"]).strip()
    payload.update(
        {
            "ok": result["returncode"] == 0,
            "version_output": text,
            "returncode": result["returncode"],
        }
    )
    if result["returncode"] != 0:
        payload["reason"] = text or "version check failed"
    return payload


def detect_officecli(version: str = DEFAULT_VERSION) -> dict[str, Any]:
    checks = [probe_candidate(source, path) for source, path in candidate_paths(version)]
    selected = next((item for item in checks if item.get("ok")), None)
    return {
        "available": selected is not None,
        "selected": selected,
        "checks": checks,
        "default_version": normalize_version(version),
    }


def request_bytes(url: str, *, timeout: int = 300) -> bytes:
    req = Request(url, headers={"User-Agent": "llmwiki-officecli-loader"})
    with urlopen(req, timeout=timeout) as response:
        return response.read()


def fetch_with_fallback(paths: list[str]) -> tuple[bytes, str]:
    errors: list[str] = []
    for url in paths:
        try:
            return request_bytes(url), url
        except (OSError, URLError) as exc:
            errors.append(f"{url}: {exc}")
    raise OfficeCliQAError("; ".join(errors))


def release_urls(version: str, asset: str) -> list[str]:
    version = normalize_version(version)
    return [
        f"{MIRROR_BASE}/releases/download/{version}/{asset}",
        f"{GITHUB_BASE}/releases/download/{version}/{asset}",
    ]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_checksum(version: str, asset: str) -> tuple[str | None, str | None]:
    try:
        data, source = fetch_with_fallback(release_urls(version, "SHA256SUMS"))
    except OfficeCliQAError:
        return None, None
    for line in data.decode("utf-8", errors="replace").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1].lstrip("*") == asset:
            return parts[0].lower(), source
    return None, source


def install_local(version: str = DEFAULT_VERSION) -> dict[str, Any]:
    version = normalize_version(version)
    if not is_windows_x64():
        raise OfficeCliQAError("install-local currently supports Windows x64 only")
    target_dir = RUNTIME_ROOT / version
    target = target_dir / EXE_NAME
    target_dir.mkdir(parents=True, exist_ok=True)
    asset = ASSET_NAME
    data, download_url = fetch_with_fallback(release_urls(version, asset))
    expected, sums_url = expected_checksum(version, asset)
    with tempfile.NamedTemporaryFile(delete=False, dir=str(target_dir), suffix=".download") as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        actual = sha256(tmp_path)
        if expected and actual.lower() != expected:
            raise OfficeCliQAError(
                f"checksum mismatch for {asset}: expected {expected}, got {actual}"
            )
        tmp_path.replace(target)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    probe = probe_candidate("local", target)
    return {
        "ok": bool(probe.get("ok")),
        "version": version,
        "asset": asset,
        "path": str(target),
        "download_url": download_url,
        "sha256": actual,
        "sha256_expected": expected,
        "sha256_source": sums_url,
        "probe": probe,
    }


def output_child(path: str | None, *, default_name: str) -> Path:
    out = Path(path) if path else OUTPUT_ROOT / default_name
    if not out.is_absolute():
        out = KB_ROOT / out
    resolved = out.resolve()
    output_resolved = OUTPUT_ROOT.resolve()
    if resolved != output_resolved and output_resolved not in resolved.parents:
        raise OfficeCliQAError(f"output path must be under {output_resolved}")
    out.mkdir(parents=True, exist_ok=True)
    return out


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, payload: Any) -> None:
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def collect_issue_messages(payload: Any) -> list[str]:
    """Extract human-readable OfficeCLI issue messages from version-varying JSON."""
    messages: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in {"message", "error", "warning", "description", "detail"} and isinstance(value, str):
                if value.strip():
                    messages.append(value.strip())
            else:
                messages.extend(collect_issue_messages(value))
    elif isinstance(payload, list):
        for value in payload:
            messages.extend(collect_issue_messages(value))
    return list(dict.fromkeys(messages))


def classify_source_audit_issues(*payloads: Any) -> tuple[list[str], list[str]]:
    messages: list[str] = []
    for payload in payloads:
        messages.extend(collect_issue_messages(payload))
    messages = list(dict.fromkeys(messages))
    blocking_pattern = re.compile(
        r"(?:\brelationship\b|missing.{0,40}(?:media|image|drawing)|"
        r"(?:media|image|drawing).{0,40}missing|(?:corrupt|broken).{0,40}(?:media|relationship))",
        re.IGNORECASE,
    )
    blocking = [message for message in messages if blocking_pattern.search(message)]
    advisories = [message for message in messages if message not in blocking]
    return blocking, advisories


def run_qa(
    docx: Path,
    pdf: Path | None,
    out_dir: Path,
    *,
    version: str,
    policy: str = "final",
) -> dict[str, Any]:
    if policy not in {"source-audit", "final"}:
        raise ValueError(f"unsupported OfficeCLI QA policy: {policy}")
    detect = detect_officecli(version)
    if not detect["available"] or not detect["selected"]:
        payload = {
            "ok": False,
            "status": "unavailable",
            "policy": policy,
            "docx": str(docx),
            "pdf": str(pdf) if pdf else None,
            "out_dir": str(out_dir),
            "officecli": detect,
        }
        write_json(out_dir / "officecli_qa.json", payload)
        return payload

    exe = Path(detect["selected"]["path"]).expanduser().resolve()
    docx = docx.resolve()
    if not docx.exists():
        raise OfficeCliQAError(f"DOCX not found: {docx}")
    if pdf is not None and not pdf.exists():
        pdf = None

    validate_result = run_officecli(exe, ["validate", str(docx), "--json"], timeout=90)
    validate_json = parse_json_maybe(validate_result["stdout"])

    issues_result = run_officecli(exe, ["view", str(docx), "issues", "--json"], timeout=90)
    issues_json = parse_json_maybe(issues_result["stdout"])
    write_json(out_dir / "issues.json", issues_json if issues_json is not None else issues_result)

    stats_result = run_officecli(exe, ["view", str(docx), "stats"], timeout=90)
    write_text(out_dir / "stats.txt", stats_result["stdout"] + stats_result["stderr"])

    contact_sheet = out_dir / "contact_sheet.png"
    screenshot_result = run_officecli(
        exe,
        [
            "view",
            str(docx),
            "screenshot",
            "--grid",
            "auto",
            "-o",
            str(contact_sheet),
        ],
        timeout=180,
    )

    preview_html = out_dir / "preview.html"
    html_result: dict[str, Any] | None = None
    if screenshot_result["returncode"] != 0:
        html_result = run_officecli(
            exe,
            ["view", str(docx), "html", "--out", str(preview_html)],
            timeout=120,
        )

    blocking_issues, advisories = classify_source_audit_issues(validate_json, issues_json)
    validate_command_ok = validate_result["returncode"] == 0
    screenshot_ok = screenshot_result["returncode"] == 0 and contact_sheet.exists()
    issues_ok = issues_result["returncode"] == 0
    stats_ok = stats_result["returncode"] == 0
    if policy == "source-audit":
        validate_tool_failed = validate_result["returncode"] != 0 and validate_json is None
        qa_ok = not validate_tool_failed and not blocking_issues and issues_ok and stats_ok and screenshot_ok
        validate_ok = not validate_tool_failed and not blocking_issues
    else:
        validate_ok = validate_command_ok
        qa_ok = validate_ok and issues_ok and stats_ok and screenshot_ok
    payload = {
        "ok": qa_ok,
        "status": "ok" if qa_ok else "failed",
        "policy": policy,
        "docx": str(docx),
        "pdf": str(pdf.resolve()) if pdf else None,
        "out_dir": str(out_dir),
        "officecli": detect,
        "blocking_issues": blocking_issues,
        "advisories": advisories,
        "outputs": {
            "qa_json": str(out_dir / "officecli_qa.json"),
            "issues_json": str(out_dir / "issues.json"),
            "stats_txt": str(out_dir / "stats.txt"),
            "contact_sheet": str(contact_sheet) if contact_sheet.exists() else None,
            "preview_html": str(preview_html) if preview_html.exists() else None,
        },
        "validate": {
            "ok": validate_ok,
            "command_ok": validate_command_ok,
            "returncode": validate_result["returncode"],
            "json": validate_json,
            "stderr": validate_result["stderr"],
        },
        "issues": {
            "ok": issues_result["returncode"] == 0,
            "returncode": issues_result["returncode"],
            "stderr": issues_result["stderr"],
        },
        "stats": {
            "ok": stats_result["returncode"] == 0,
            "returncode": stats_result["returncode"],
            "stderr": stats_result["stderr"],
        },
        "screenshot": {
            "ok": screenshot_ok,
            "returncode": screenshot_result["returncode"],
            "stderr": screenshot_result["stderr"],
            "stdout": screenshot_result["stdout"],
        },
    }
    if html_result is not None:
        payload["html_fallback"] = {
            "ok": html_result["returncode"] == 0,
            "returncode": html_result["returncode"],
            "stderr": html_result["stderr"],
            "stdout": html_result["stdout"],
        }
    write_json(out_dir / "officecli_qa.json", payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Controlled OfficeCLI DOCX QA wrapper.")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check")
    check.add_argument("--version", default=DEFAULT_VERSION)
    check.add_argument("--json", action="store_true")

    install = sub.add_parser("install-local")
    install.add_argument("--version", default=DEFAULT_VERSION)
    install.add_argument("--json", action="store_true")

    qa = sub.add_parser("qa")
    qa.add_argument("--docx", required=True)
    qa.add_argument("--pdf")
    qa.add_argument("--out-dir", required=True)
    qa.add_argument("--version", default=DEFAULT_VERSION)
    qa.add_argument("--policy", choices=("source-audit", "final"), default="final")
    qa.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            payload = detect_officecli(args.version)
            payload["ok"] = payload["available"]
            if args.json:
                print_json(payload)
            else:
                print(payload["selected"]["path"] if payload["selected"] else "OfficeCLI unavailable")
            return 0 if payload["available"] else 1
        if args.command == "install-local":
            payload = install_local(args.version)
            if args.json:
                print_json(payload)
            else:
                print(payload["path"])
            return 0 if payload["ok"] else 1
        if args.command == "qa":
            out_dir = output_child(args.out_dir, default_name="qa")
            payload = run_qa(
                Path(args.docx),
                Path(args.pdf) if args.pdf else None,
                out_dir,
                version=args.version,
                policy=args.policy,
            )
            if args.json:
                print_json(payload)
            else:
                print(f"{payload['status']}: {payload['outputs']['qa_json']}")
            return 0 if payload["ok"] else 1
    except Exception as exc:
        payload = {"ok": False, "status": "error", "error": str(exc)}
        if getattr(args, "json", False):
            print_json(payload)
        else:
            print(str(exc), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
