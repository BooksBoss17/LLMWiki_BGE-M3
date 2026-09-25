#!/usr/bin/env python
"""Hash-guarded full-question curation for teacher-authorized Role D strong mode."""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import difflib
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve()
DEFAULT_KB_ROOT = SCRIPT.parents[4]
PLATFORM_ADAPTER_ROOT = SCRIPT.parents[3] / "_shared" / "scripts"
if str(PLATFORM_ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ADAPTER_ROOT))
from project_paths import ProjectLayoutError, resolve_path  # noqa: E402
QUESTION_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{2,32}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
PLACEHOLDERS = ["见原件", "待补", "待完善", "TODO", "TBD", "暂无解析", "解析略"]
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)")
EXPLICIT_FIGURE_REFERENCE_RE = re.compile(
    r"如(?:下)?图(?:所示)?|图示(?:中|所示)?|图中|下图(?:中|所示)?|"
    r"图[甲乙丙丁一二三四五六七八九十0-9]+(?:中|所示)?|见图[甲乙丙丁一二三四五六七八九十0-9]*"
)
MEDIA_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
MISSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TRANSACTION_STATES = {
    "prepared",
    "applying",
    "commit_decided",
    "committed",
    "compensating",
    "compensated",
    "recovery_required",
}
TRANSACTION_TERMINAL_STATES = {"committed", "compensated"}
COMMIT_DECISION_NAME = "commit-decision.json"
COMMIT_DECISION_KIND = "curation_writer_commit_decision"
ATOMIC_REPLACE_RETRY_DELAYS_SECONDS = (0.01, 0.02, 0.04, 0.08, 0.16)


class WriterTransactionRecoveryError(RuntimeError):
    """The writer could not prove that every transaction file was restored."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(root.resolve())
    return resolved


def replace_h2_section(text: str, heading: str, body: str) -> str:
    normalized = body.strip()
    pattern = re.compile(rf"(?ms)^##\s+{re.escape(heading)}\s*\n.*?(?=^##\s+|\Z)")
    replacement = f"## {heading}\n\n{normalized}\n\n"
    if pattern.search(text):
        # A function replacement preserves LaTeX backslashes such as \mu and
        # \max instead of treating them as regular-expression escapes.
        updated = pattern.sub(lambda _match: replacement, text, count=1)
    else:
        updated = text.rstrip() + "\n\n" + replacement
    return updated.rstrip() + "\n"


def split_frontmatter(text: str) -> tuple[str, str]:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, flags=re.DOTALL)
    if not match:
        raise ValueError("question file must contain YAML frontmatter")
    return match.group(1), text[match.end() :]


def replace_frontmatter_list(text: str, field: str, values: list[str]) -> str:
    frontmatter, body = split_frontmatter(text)
    lines = frontmatter.splitlines()
    start: int | None = None
    end: int | None = None
    for index, line in enumerate(lines):
        if re.match(rf"^{re.escape(field)}:\s*", line):
            start = index
            end = index + 1
            while end < len(lines) and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*:\s*", lines[end]):
                end += 1
            break
    rendered = [f"{field}: []"] if not values else [f"{field}:", *(f"- {item}" for item in values)]
    if start is None:
        lines.extend(rendered)
    else:
        assert end is not None
        lines[start:end] = rendered
    return "---\n" + "\n".join(lines) + "\n---\n" + body.lstrip("\n")


def body_media_refs(text: str) -> list[str]:
    _frontmatter, body = split_frontmatter(text)
    refs = [match.group(1).replace("\\", "/") for match in IMAGE_RE.finditer(body)]
    return sorted(dict.fromkeys(ref for ref in refs if ref.startswith("media/")))


def extract_h2_section(text: str, heading: str) -> str:
    _frontmatter, body = split_frontmatter(text)
    match = re.search(rf"(?ms)^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)", body)
    return match.group(1).strip() if match else ""


def frontmatter_list(text: str, field: str) -> list[str]:
    frontmatter, _body = split_frontmatter(text)
    lines = frontmatter.splitlines()
    values: list[str] = []
    in_field = False
    for line in lines:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*:\s*", line):
            key, inline = line.split(":", 1)
            in_field = key == field
            if in_field:
                values.extend(re.findall(r"['\"]([^'\"]+)['\"]", inline))
            continue
        if in_field:
            item = re.match(r"^\s*-\s*(.+?)\s*$", line)
            if item:
                values.append(item.group(1).strip().strip("'\""))
    return sorted(dict.fromkeys(value.replace("\\", "/") for value in values if value))


def validate_explicit_figure_dependency(text: str) -> None:
    stem = extract_h2_section(text, "题目")
    match = EXPLICIT_FIGURE_REFERENCE_RE.search(stem)
    if match and not body_media_refs(text) and not frontmatter_list(text, "assets"):
        raise ValueError(
            "stem_requires_figure_but_assets_empty: "
            f"explicit figure signal {match.group(0)!r} has no body image or assets entry"
        )


def normalize_choice_options(answer: str) -> list[str]:
    compact = re.sub(r"[\s、,，;；/和及]+", "", answer.upper())
    if not re.fullmatch(r"[A-H]+", compact):
        raise ValueError("choice answer must contain only option letters A-H")
    return sorted(set(compact))


def validate_assets(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("updates.assets must be an array of media/... paths")
    assets: list[str] = []
    for raw in value:
        item = raw.strip().replace("\\", "/")
        parts = Path(item).parts
        if not item.startswith("media/") or Path(item).is_absolute() or ".." in parts or len(parts) != 2:
            raise ValueError(f"invalid asset path: {raw}")
        if Path(item).suffix.lower() not in MEDIA_EXTENSIONS:
            raise ValueError(f"unsupported asset extension: {raw}")
        assets.append(item)
    if len(assets) != len(set(assets)):
        raise ValueError("updates.assets contains duplicates")
    return assets


def validate_ai_extra_tags(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("updates.ai_extra_tags must be an array of strings")
    if len(value) > 16:
        raise ValueError("updates.ai_extra_tags allows at most 16 tags")
    tags: list[str] = []
    for raw in value:
        tag = raw.strip()
        if not 2 <= len(tag) <= 32:
            raise ValueError(f"ai_extra_tags item must be 2-32 characters: {raw}")
        if "/" in tag or "\\" in tag or re.fullmatch(r"kp_[0-9]+", tag, flags=re.IGNORECASE):
            raise ValueError(f"ai_extra_tags must not imitate standard paths or kp_id: {raw}")
        tags.append(tag)
    if len(tags) != len(set(tags)):
        raise ValueError("updates.ai_extra_tags contains duplicates")
    return tags


def validate_media_files(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("media_files must be an array")
    media_files: list[dict[str, str]] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError("each media_files entry must be an object")
        unknown = set(entry) - {"source_path", "source_sha256", "target", "expected_target_sha256"}
        if unknown:
            raise ValueError(f"unknown media_files fields: {sorted(unknown)}")
        source_path = str(entry.get("source_path") or "")
        source_hash = str(entry.get("source_sha256") or "").lower()
        target = str(entry.get("target") or "").replace("\\", "/")
        expected_target_hash = str(entry.get("expected_target_sha256") or "").lower()
        if not source_path or Path(source_path).is_absolute() or ".." in Path(source_path).parts:
            raise ValueError("media source_path must be a safe repository-relative path")
        if not HASH_RE.fullmatch(source_hash):
            raise ValueError("media source_sha256 must be 64 lowercase hex characters")
        if expected_target_hash and not HASH_RE.fullmatch(expected_target_hash):
            raise ValueError("media expected_target_sha256 must be 64 lowercase hex characters")
        validate_assets([target])
        normalized = {"source_path": source_path, "source_sha256": source_hash, "target": target}
        if expected_target_hash:
            normalized["expected_target_sha256"] = expected_target_hash
        media_files.append(normalized)
    targets = [item["target"] for item in media_files]
    if len(targets) != len(set(targets)):
        raise ValueError("media_files contains duplicate targets")
    return media_files


def validate_verification(value: Any, question_id: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("strong v2 substantive updates require verification")
    allowed = {
        "source_evidence", "stem_complete", "figure_dependencies_resolved",
        "review_passes", "derived_correct_options", "non_choice_check",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown verification fields: {sorted(unknown)}")
    if value.get("stem_complete") is not True:
        raise ValueError("verification.stem_complete must be true")
    if value.get("figure_dependencies_resolved") is not True:
        raise ValueError("verification.figure_dependencies_resolved must be true")
    review_passes = int(value.get("review_passes", 0))
    if review_passes < 2:
        raise ValueError("verification.review_passes must be at least 2")
    raw_evidence = value.get("source_evidence")
    if not isinstance(raw_evidence, list) or not raw_evidence:
        raise ValueError("verification.source_evidence must be a non-empty array")
    evidence: list[dict[str, Any]] = []
    for item in raw_evidence:
        if not isinstance(item, dict):
            raise ValueError("each source_evidence item must be an object")
        item_unknown = set(item) - {"path", "sha256", "page", "relationship_id"}
        if item_unknown:
            raise ValueError(f"unknown source_evidence fields: {sorted(item_unknown)}")
        path = str(item.get("path") or "")
        digest = str(item.get("sha256") or "").lower()
        if not path or Path(path).is_absolute() or ".." in Path(path).parts:
            raise ValueError("source_evidence.path must be repository-relative")
        if not HASH_RE.fullmatch(digest):
            raise ValueError("source_evidence.sha256 must be 64 lowercase hex characters")
        normalized: dict[str, Any] = {"path": path, "sha256": digest}
        if "page" in item:
            page = int(item["page"])
            if page < 1:
                raise ValueError("source_evidence.page must be positive")
            normalized["page"] = page
        if "relationship_id" in item:
            relationship_id = str(item["relationship_id"]).strip()
            if not relationship_id:
                raise ValueError("source_evidence.relationship_id must not be empty")
            normalized["relationship_id"] = relationship_id
        evidence.append(normalized)
    normalized_verification: dict[str, Any] = {
        "source_evidence": evidence,
        "stem_complete": True,
        "figure_dependencies_resolved": True,
        "review_passes": review_passes,
    }
    if question_id.upper().startswith(("MC", "MA")):
        options = value.get("derived_correct_options")
        if not isinstance(options, list) or not options or not all(isinstance(item, str) for item in options):
            raise ValueError("MC/MA verification requires derived_correct_options")
        normalized_options = [item.strip().upper() for item in options]
        if any(not re.fullmatch(r"[A-H]", item) for item in normalized_options):
            raise ValueError("derived_correct_options items must be single letters A-H")
        if len(normalized_options) != len(set(normalized_options)):
            raise ValueError("derived_correct_options contains duplicates")
        normalized_verification["derived_correct_options"] = sorted(normalized_options)
    else:
        check = value.get("non_choice_check")
        if not isinstance(check, dict) or set(check) != {"status", "summary"}:
            raise ValueError("non-choice verification requires status and summary")
        summary = str(check.get("summary") or "").strip()
        if check.get("status") != "confirmed" or len(summary) < 20:
            raise ValueError("non_choice_check must be confirmed with a substantive summary")
        normalized_verification["non_choice_check"] = {"status": "confirmed", "summary": summary}
    return normalized_verification


def validate_proposal(payload: dict[str, Any]) -> tuple[int, str, str, str, str, dict[str, Any], list[dict[str, str]], dict[str, Any] | None]:
    schema_version = int(payload.get("schema_version", 0))
    if schema_version not in {1, 2}:
        raise ValueError("schema_version must be 1 or 2")
    question_id = str(payload.get("question_id") or "")
    if not QUESTION_ID_RE.fullmatch(question_id):
        raise ValueError("invalid question_id")
    target_path = str(payload.get("target_path") or "")
    if not target_path or Path(target_path).is_absolute() or ".." in Path(target_path).parts:
        raise ValueError("target_path must be a safe repository-relative path")
    expected_hash = str(payload.get("expected_sha256") or "").lower()
    if not HASH_RE.fullmatch(expected_hash):
        raise ValueError("expected_sha256 must be 64 lowercase hex characters")
    updates = payload.get("updates")
    if not isinstance(updates, dict):
        raise ValueError("updates must be an object")
    allowed = {"answer", "solution"} if schema_version == 1 else {"stem", "answer", "solution", "assets", "ai_extra_tags"}
    unknown = set(updates) - allowed
    if unknown:
        raise ValueError(f"v{schema_version} does not allow update fields: {sorted(unknown)}")
    model_profile = str(payload.get("model_profile") or ("weak" if schema_version == 1 else ""))
    if model_profile not in {"weak", "strong"}:
        raise ValueError("model_profile must be weak or strong")
    if schema_version == 1 and set(updates) != {"answer", "solution"}:
        raise ValueError("v1 requires updates.answer and updates.solution")
    if schema_version == 2 and not updates:
        raise ValueError("updates must contain at least one writable field")
    if schema_version == 2 and model_profile != "strong" and set(updates) - {"answer", "solution"}:
        raise PermissionError("stem and assets updates require model_profile strong")

    normalized: dict[str, Any] = {}
    for field in ("stem", "answer", "solution"):
        if field in updates:
            value = str(updates.get(field) or "").strip()
            if not value:
                raise ValueError(f"updates.{field} must not be empty")
            normalized[field] = value
    if "solution" in normalized and len(normalized["solution"]) < 20:
        raise ValueError("solution is too short to be a complete derivation")
    if "stem" in normalized and len(normalized["stem"]) < 5:
        raise ValueError("stem is too short to be a complete question")
    if "assets" in updates:
        normalized["assets"] = validate_assets(updates["assets"])
    if "ai_extra_tags" in updates:
        normalized["ai_extra_tags"] = validate_ai_extra_tags(updates["ai_extra_tags"])
    media_files = validate_media_files(payload.get("media_files"))
    if media_files and model_profile != "strong":
        raise PermissionError("media_files require model_profile strong")
    combined = "\n".join(str(normalized.get(field, "")) for field in ("stem", "answer", "solution"))
    found = [item for item in PLACEHOLDERS if item.lower() in combined.lower()]
    if found:
        raise ValueError(f"placeholder content is forbidden: {found}")
    substantive = bool(set(normalized) & {"stem", "answer", "solution", "assets"} or media_files)
    verification = None
    if schema_version == 2 and substantive:
        if model_profile != "strong":
            raise PermissionError("strong v2 substantive updates require model_profile strong")
        verification = validate_verification(payload.get("verification"), question_id)
    return schema_version, model_profile, question_id, target_path, expected_hash, normalized, media_files, verification


def run_validation(command: list[str], cwd: Path) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.run(
        command,
        cwd=str(cwd),
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=300,
    )
    return {
        "command": command,
        "returncode": process.returncode,
        "ok": process.returncode == 0,
        "stdout": process.stdout[-4000:],
        "stderr": process.stderr[-4000:],
    }


def emit_json(payload: dict[str, Any], *, error: bool = False) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    stream = sys.stderr if error else sys.stdout
    try:
        print(rendered, file=stream)
    except UnicodeEncodeError:
        binary = getattr(stream, "buffer", None)
        if binary is None:
            raise
        binary.write((rendered + "\n").encode("utf-8"))


def replace_atomically_with_retry(source: Path, destination: Path) -> None:
    """Retry only transient access denials around an otherwise atomic replace."""
    for delay in ATOMIC_REPLACE_RETRY_DELAYS_SECONDS:
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            time.sleep(delay)
    os.replace(source, destination)


def write_atomic(path: Path, text: str, temp_root: Path) -> None:
    temp_root.mkdir(parents=True, exist_ok=True)
    temporary = temp_root / f"{path.name}.tmp"
    temporary.write_text(text, encoding="utf-8", newline="\n")
    replace_atomically_with_retry(temporary, path)


def write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    replace_atomically_with_retry(temporary, path)


def write_durable_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp"
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(rendered)
        handle.flush()
        os.fsync(handle.fileno())
    replace_atomically_with_retry(temporary, path)
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())
    directory_descriptor: int | None = None
    try:
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        os.fsync(directory_descriptor)
    except OSError:
        # Windows does not consistently permit opening directories for fsync.
        # The marker file itself has already been flushed after atomic publish.
        pass
    finally:
        if directory_descriptor is not None:
            os.close(directory_descriptor)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def file_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "sha256": None}
    if not path.is_file():
        raise RuntimeError(f"transaction path is not a regular file: {path}")
    return {"exists": True, "sha256": sha256_file(path)}


def state_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    return (
        actual.get("exists") is expected.get("exists")
        and actual.get("sha256") == expected.get("sha256")
    )


def require_file_state(path: Path, expected: dict[str, Any], label: str) -> None:
    actual = file_state(path)
    if not state_matches(actual, expected):
        raise RuntimeError(
            f"{label} drift: expected exists={expected.get('exists')} "
            f"sha256={expected.get('sha256')}, actual exists={actual.get('exists')} "
            f"sha256={actual.get('sha256')}"
        )


def transaction_fault_hook(event: str, *, destination: Path, hold_path: Path | None) -> None:
    """No-op fault boundary patched by transaction tests."""


def atomic_move_noreplace(source: Path, destination: Path) -> None:
    """Atomically rename *source* to an absent *destination* on Windows/Unix.

    ``os.rename`` is no-replace on Windows but replaces on POSIX.  Linux and
    Darwin therefore need their exclusive rename APIs.  Unsupported Unix
    variants fail closed instead of falling back to a check-then-rename gap.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.rename(source, destination)
        return

    libc = ctypes.CDLL(None, use_errno=True)
    source_raw = os.fsencode(source)
    destination_raw = os.fsencode(destination)
    result: int
    if sys.platform.startswith("linux"):
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise RuntimeError("atomic no-replace rename is unavailable on this Linux runtime")
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameat2.restype = ctypes.c_int
        result = renameat2(-100, source_raw, -100, destination_raw, 1)  # AT_FDCWD, RENAME_NOREPLACE
    elif sys.platform == "darwin":
        renamex_np = getattr(libc, "renamex_np", None)
        if renamex_np is None:
            raise RuntimeError("atomic no-replace rename is unavailable on this Darwin runtime")
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(source_raw, destination_raw, 0x00000004)  # RENAME_EXCL
    else:
        renameatx_np = getattr(libc, "renameatx_np", None)
        if renameatx_np is None:
            raise RuntimeError("atomic no-replace rename is unavailable on this Unix runtime")
        renameatx_np.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameatx_np.restype = ctypes.c_int
        result = renameatx_np(-100, source_raw, -100, destination_raw, 0x00000004)
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(error_number, os.strerror(error_number), str(destination))
        raise OSError(error_number, os.strerror(error_number), str(source), str(destination))


def transaction_hold_path(transaction_root: Path, record: dict[str, Any]) -> Path:
    hold = record.get("hold")
    relative = hold.get("relative_path") if isinstance(hold, dict) else None
    if not isinstance(relative, str) or not relative:
        relative = f"holds/{int(record['index']):03d}.pre"
    path = (transaction_root / relative).resolve()
    return ensure_inside(path, transaction_root)


def restore_moved_file_noreplace(*, hold_path: Path, destination: Path, expected: dict[str, Any], label: str) -> None:
    """Restore a held file only into an absent path; never replace a winner."""

    require_file_state(hold_path, expected, f"{label} hold")
    try:
        atomic_move_noreplace(hold_path, destination)
    except FileExistsError as exc:
        raise RuntimeError(f"{label} refused to replace an external winner: {destination}") from exc
    require_file_state(destination, expected, f"{label} restored")


def snapshot_file(
    *,
    source: Path,
    expected_sha256: str,
    copy_path: Path,
    label: str,
) -> dict[str, Any]:
    require_file_state(source, {"exists": True, "sha256": expected_sha256}, label)
    copy_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, copy_path)
    if sha256_file(copy_path) != expected_sha256:
        raise RuntimeError(f"{label} snapshot hash mismatch")
    require_file_state(source, {"exists": True, "sha256": expected_sha256}, label)
    return {
        "exists": True,
        "sha256": expected_sha256,
        "copy_path": copy_path.name,
        "copy_sha256": expected_sha256,
    }


def snapshot_text(*, text: str, copy_path: Path) -> dict[str, Any]:
    copy_path.parent.mkdir(parents=True, exist_ok=True)
    copy_path.write_text(text, encoding="utf-8", newline="\n")
    digest = sha256_file(copy_path)
    return {
        "exists": True,
        "sha256": digest,
        "copy_path": copy_path.name,
        "copy_sha256": digest,
    }


def transaction_snapshot_path(transaction_root: Path, snapshot: dict[str, Any]) -> Path:
    copy_name = snapshot.get("copy_path")
    if not snapshot.get("exists") or not isinstance(copy_name, str) or not copy_name:
        raise RuntimeError("transaction snapshot copy is missing")
    return ensure_inside(transaction_root / "copies" / copy_name, transaction_root / "copies")


def write_transaction_journal(journal_path: Path, journal: dict[str, Any]) -> None:
    journal["updated_at"] = utc_now()
    write_atomic_json(journal_path, journal)


def transition_transaction(
    journal_path: Path,
    journal: dict[str, Any],
    status: str,
    *,
    detail: str | None = None,
) -> None:
    if status not in TRANSACTION_STATES:
        raise ValueError(f"unsupported writer transaction status: {status}")
    journal["status"] = status
    event: dict[str, Any] = {"status": status, "at": utc_now()}
    if detail:
        event["detail"] = detail
    journal.setdefault("history", []).append(event)
    write_transaction_journal(journal_path, journal)


def transaction_file_path(kb_root: Path, record: dict[str, Any]) -> Path:
    relative_path = str(record.get("relative_path") or "")
    if not relative_path or Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
        raise RuntimeError("transaction file has an unsafe relative_path")
    return ensure_inside(kb_root / relative_path, kb_root)


def prepare_writer_transaction(
    *,
    kb_root: Path,
    question_id: str,
    transaction_id: str,
    target_rel: str,
    target: Path,
    expected_target_sha256: str,
    updated: str,
    prepared_media: list[dict[str, Any]],
    state_root: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    canonical_transactions_root = (
        kb_root / "skills/_ops/runtime/state/role_d_curation/writer_transactions"
    ).resolve()
    state_root = (
        state_root.resolve()
        if state_root is not None
        else (canonical_transactions_root / question_id / transaction_id).resolve()
    )
    ensure_inside(state_root, canonical_transactions_root)
    copies_root = state_root / "copies"
    copies_root.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []

    target_pre_copy = copies_root / "000.pre"
    target_post_copy = copies_root / "000.post"
    target_pre = snapshot_file(
        source=target,
        expected_sha256=expected_target_sha256,
        copy_path=target_pre_copy,
        label="main Markdown precondition",
    )
    target_post = snapshot_text(text=updated, copy_path=target_post_copy)
    records.append(
        {
            "index": 0,
            "kind": "main_markdown",
            "relative_path": target_rel,
            "action": "update" if target_pre["sha256"] != target_post["sha256"] else "reuse",
            "pre": target_pre,
            "post": target_post,
            "hold": {
                "relative_path": "holds/000.pre",
                "sha256": target_pre["sha256"],
            },
            "source_path": None,
            "source_sha256": None,
            "apply_status": "pending",
            "compensation_status": "pending",
            "last_error": None,
        }
    )

    for index, item in enumerate(prepared_media, start=1):
        destination = item["destination"]
        action = item["action"]
        source = item["source"]
        source_sha256 = item["source_sha256"]
        pre_copy = copies_root / f"{index:03d}.pre"
        post_copy = copies_root / f"{index:03d}.post"
        if action == "create":
            require_file_state(destination, {"exists": False, "sha256": None}, f"media create {item['target']}")
            pre = {"exists": False, "sha256": None, "copy_path": None, "copy_sha256": None}
        else:
            expected_pre_sha256 = source_sha256 if action == "reuse" else item["expected_target_sha256"]
            pre = snapshot_file(
                source=destination,
                expected_sha256=expected_pre_sha256,
                copy_path=pre_copy,
                label=f"media {action} {item['target']}",
            )
        post = snapshot_file(
            source=source,
            expected_sha256=source_sha256,
            copy_path=post_copy,
            label=f"media source {item['source_path']}",
        )
        require_file_state(destination, pre, f"media {action} {item['target']}")
        relative = destination.resolve().relative_to(kb_root.resolve()).as_posix()
        records.append(
            {
                "index": index,
                "kind": "media",
                "relative_path": relative,
                "action": action,
                "pre": pre,
                "post": post,
                "hold": (
                    {
                        "relative_path": f"holds/{index:03d}.pre",
                        "sha256": pre["sha256"],
                    }
                    if pre["exists"] and action != "reuse"
                    else None
                ),
                "source_path": item["source_path"],
                "source_sha256": source_sha256,
                "apply_status": "pending",
                "compensation_status": "pending",
                "last_error": None,
            }
        )

    created_at = utc_now()
    journal = {
        "schema_version": 1,
        "kind": "curation_writer_transaction",
        "transaction_id": transaction_id,
        "question_id": question_id,
        "status": "prepared",
        "created_at": created_at,
        "updated_at": created_at,
        "files": records,
        "history": [{"status": "prepared", "at": created_at}],
        "last_error": None,
    }
    journal_path = state_root / "journal.json"
    write_atomic_json(journal_path, journal)
    return journal_path, journal


def install_transaction_snapshot(
    *,
    transaction_root: Path,
    destination: Path,
    snapshot: dict[str, Any],
    expected_current: dict[str, Any],
    operation_label: str,
    hold_path: Path | None = None,
) -> None:
    source = transaction_snapshot_path(transaction_root, snapshot)
    if sha256_file(source) != snapshot.get("copy_sha256"):
        raise RuntimeError(f"{operation_label} snapshot drift")
    temporary = transaction_root / "tmp" / f"{destination.name}.{operation_label}.tmp"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, temporary)
    if sha256_file(temporary) != snapshot.get("sha256"):
        raise RuntimeError(f"{operation_label} staged copy hash mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if expected_current.get("exists") is False:
        # A second existence check followed by os.replace still permits an
        # external creator to win the gap and be overwritten.  Linking the
        # complete staged copy is an atomic create-if-absent operation on the
        # same project filesystem; FileExistsError therefore fails closed.
        try:
            os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    else:
        require_file_state(destination, expected_current, f"{operation_label} precondition")
        if hold_path is None:
            raise RuntimeError(f"{operation_label} requires a transaction hold")
        hold_path.parent.mkdir(parents=True, exist_ok=True)
        if hold_path.exists():
            require_file_state(hold_path, expected_current, f"{operation_label} existing hold")
            require_file_state(destination, {"exists": False, "sha256": None}, f"{operation_label} occupied target")
        else:
            transaction_fault_hook(
                "after_precondition_before_hold",
                destination=destination,
                hold_path=hold_path,
            )
            atomic_move_noreplace(destination, hold_path)
            try:
                require_file_state(hold_path, expected_current, f"{operation_label} acquired hold")
            except Exception:
                if not destination.exists():
                    restore_moved_file_noreplace(
                        hold_path=hold_path,
                        destination=destination,
                        expected=file_state(hold_path),
                        label=f"{operation_label} raced precondition",
                    )
                raise
        transaction_fault_hook(
            "after_hold_before_install",
            destination=destination,
            hold_path=hold_path,
        )
        try:
            os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    require_file_state(destination, snapshot, f"{operation_label} postcondition")


def apply_transaction_file(
    *,
    kb_root: Path,
    journal_path: Path,
    journal: dict[str, Any],
    record: dict[str, Any],
) -> None:
    destination = transaction_file_path(kb_root, record)
    if record.get("kind") == "media":
        source = ensure_inside(kb_root / str(record.get("source_path") or ""), kb_root)
        require_file_state(
            source,
            {"exists": True, "sha256": record.get("source_sha256")},
            f"media source {record.get('source_path')}",
        )
    require_file_state(destination, record["pre"], f"{record.get('action')} {record['relative_path']}")
    if record.get("action") == "reuse":
        require_file_state(destination, record["post"], f"reuse {record['relative_path']}")
        record["apply_status"] = "reused"
    else:
        install_transaction_snapshot(
            transaction_root=journal_path.parent,
            destination=destination,
            snapshot=record["post"],
            expected_current=record["pre"],
            operation_label=f"apply-{record['index']}",
            hold_path=(
                transaction_hold_path(journal_path.parent, record)
                if record["pre"].get("exists")
                else None
            ),
        )
        record["apply_status"] = "applied"
    record["last_error"] = None
    write_transaction_journal(journal_path, journal)


def apply_writer_transaction(
    *,
    kb_root: Path,
    journal_path: Path,
    journal: dict[str, Any],
) -> None:
    transition_transaction(journal_path, journal, "applying")
    for record in journal["files"]:
        if record.get("apply_status") in {"applied", "reused"}:
            continue
        apply_transaction_file(
            kb_root=kb_root,
            journal_path=journal_path,
            journal=journal,
            record=record,
        )


def commit_decision_path(journal_path: Path) -> Path:
    return journal_path.parent / COMMIT_DECISION_NAME


def commit_record_binding(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": record.get("index"),
        "kind": record.get("kind"),
        "relative_path": record.get("relative_path"),
        "action": record.get("action"),
        "pre": {
            "exists": record.get("pre", {}).get("exists"),
            "sha256": record.get("pre", {}).get("sha256"),
        },
        "post": {
            "exists": record.get("post", {}).get("exists"),
            "sha256": record.get("post", {}).get("sha256"),
        },
    }


def publish_commit_decision(
    *,
    kb_root: Path,
    journal_path: Path,
    journal: dict[str, Any],
    receipt_out: Path | None,
    receipt_payload: dict[str, Any] | None,
) -> tuple[Path, dict[str, Any]]:
    if (receipt_out is None) is not (receipt_payload is None):
        raise ValueError("execution receipt path and payload must be provided together")
    if any(record.get("kind") == "execution_receipt" for record in journal["files"]):
        raise RuntimeError("execution receipt cannot precede the commit decision")
    for record in journal["files"]:
        destination = transaction_file_path(kb_root, record)
        require_file_state(destination, record["post"], f"commit decision {record['relative_path']}")

    transaction_root = journal_path.parent
    receipt_plan: dict[str, Any] | None = None
    if receipt_out is not None and receipt_payload is not None:
        index = len(journal["files"])
        pre_state = file_state(receipt_out)
        if pre_state["exists"]:
            pre = snapshot_file(
                source=receipt_out,
                expected_sha256=pre_state["sha256"],
                copy_path=transaction_root / "copies" / f"{index:03d}.receipt-pre",
                label="execution receipt precondition",
            )
        else:
            pre = {"exists": False, "sha256": None, "copy_path": None, "copy_sha256": None}
        require_file_state(receipt_out, pre, "execution receipt precondition")
        receipt_plan = {
            "index": index,
            "relative_path": receipt_out.resolve().relative_to(kb_root.resolve()).as_posix(),
            "pre": pre,
            "payload": receipt_payload,
        }

    decision_path = commit_decision_path(journal_path)
    if decision_path.exists():
        raise FileExistsError(f"commit decision already exists: {decision_path}")
    created_at = utc_now()
    decision = {
        "schema_version": 1,
        "kind": COMMIT_DECISION_KIND,
        "transaction_id": journal["transaction_id"],
        "question_id": journal["question_id"],
        "created_at": created_at,
        "journal": {
            "path": journal_path.name,
            "sha256_before_decision": sha256_file(journal_path),
        },
        "files": [commit_record_binding(record) for record in journal["files"]],
        "execution_receipt": receipt_plan,
    }
    write_durable_atomic_json(decision_path, decision)
    return decision_path, decision


def load_commit_decision(
    *,
    journal_path: Path,
    journal: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    decision_path = commit_decision_path(journal_path)
    payload = json.loads(decision_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("kind") != COMMIT_DECISION_KIND:
        raise WriterTransactionRecoveryError(f"invalid writer commit decision: {decision_path}")
    if payload.get("schema_version") != 1:
        raise WriterTransactionRecoveryError(f"unsupported writer commit decision: {decision_path}")
    if (
        payload.get("transaction_id") != journal.get("transaction_id")
        or payload.get("question_id") != journal.get("question_id")
    ):
        raise WriterTransactionRecoveryError(f"writer commit-decision binding mismatch: {decision_path}")
    expected_files = [
        commit_record_binding(record)
        for record in journal["files"]
        if record.get("kind") != "execution_receipt"
    ]
    if payload.get("files") != expected_files:
        raise WriterTransactionRecoveryError(f"writer commit-decision file binding mismatch: {decision_path}")
    journal_binding = payload.get("journal")
    if not isinstance(journal_binding, dict) or journal_binding.get("path") != journal_path.name:
        raise WriterTransactionRecoveryError(f"writer commit-decision journal binding mismatch: {decision_path}")
    if journal.get("status") in {"prepared", "applying"} and journal.get("commit_decision") is None:
        if journal_binding.get("sha256_before_decision") != sha256_file(journal_path):
            raise WriterTransactionRecoveryError(
                f"writer journal changed after commit-decision publish without recording the decision: {journal_path}"
            )
    receipt_plan = payload.get("execution_receipt")
    if receipt_plan is not None:
        if not isinstance(receipt_plan, dict) or not isinstance(receipt_plan.get("payload"), dict):
            raise WriterTransactionRecoveryError(f"invalid execution receipt plan: {decision_path}")
        relative_path = str(receipt_plan.get("relative_path") or "")
        if not relative_path or Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
            raise WriterTransactionRecoveryError(f"unsafe execution receipt plan: {decision_path}")
        pre = receipt_plan.get("pre")
        if not isinstance(pre, dict) or not isinstance(pre.get("exists"), bool):
            raise WriterTransactionRecoveryError(f"invalid execution receipt precondition: {decision_path}")
        if pre.get("exists"):
            pre_copy = transaction_snapshot_path(journal_path.parent, pre)
            if sha256_file(pre_copy) != pre.get("sha256"):
                raise WriterTransactionRecoveryError(f"execution receipt precondition snapshot drift: {pre_copy}")
    return decision_path, payload


def transaction_has_commit_decision(journal_path: Path, journal: dict[str, Any]) -> bool:
    return journal.get("status") in {"commit_decided", "committed"} or commit_decision_path(journal_path).is_file()


def attach_commit_decision_to_journal(
    *,
    journal_path: Path,
    journal: dict[str, Any],
    decision_path: Path,
) -> dict[str, str]:
    decision_ref = receipt_file_ref(decision_path, kind="writer_transaction_commit_decision")
    existing = journal.get("commit_decision")
    if existing is not None and existing != decision_ref:
        raise WriterTransactionRecoveryError(f"writer journal commit-decision reference drift: {journal_path}")
    journal["commit_decision"] = decision_ref
    if journal.get("status") not in {"commit_decided", "committed"}:
        transition_transaction(
            journal_path,
            journal,
            "commit_decided",
            detail="durable commit decision published",
        )
    elif existing is None:
        write_transaction_journal(journal_path, journal)
    return decision_ref


def ensure_transaction_file_committed(
    *,
    kb_root: Path,
    journal_path: Path,
    journal: dict[str, Any],
    record: dict[str, Any],
) -> None:
    destination = transaction_file_path(kb_root, record)
    current = file_state(destination)
    if state_matches(current, record["post"]):
        pass
    elif state_matches(current, record["pre"]):
        if record.get("action") == "reuse":
            raise WriterTransactionRecoveryError(f"reuse record lost its committed state: {record['relative_path']}")
        install_transaction_snapshot(
            transaction_root=journal_path.parent,
            destination=destination,
            snapshot=record["post"],
            expected_current=record["pre"],
            operation_label=f"commit-{record['index']}",
            hold_path=(
                transaction_hold_path(journal_path.parent, record)
                if record["pre"].get("exists")
                else None
            ),
        )
    else:
        raise WriterTransactionRecoveryError(
            f"commit recovery drift for {record['relative_path']}: current state matches neither pre nor post"
        )
    desired_status = "reused" if record.get("action") == "reuse" else "applied"
    if record.get("apply_status") != desired_status or record.get("last_error") is not None:
        record["apply_status"] = desired_status
        record["last_error"] = None
        write_transaction_journal(journal_path, journal)


def prepare_commit_receipt_record(
    *,
    journal_path: Path,
    journal: dict[str, Any],
    decision_path: Path,
    decision: dict[str, Any],
) -> dict[str, Any] | None:
    plan = decision.get("execution_receipt")
    existing_records = [record for record in journal["files"] if record.get("kind") == "execution_receipt"]
    if plan is None:
        if existing_records:
            raise WriterTransactionRecoveryError("journal has an execution receipt without a commit-decision plan")
        return None
    if len(existing_records) > 1:
        raise WriterTransactionRecoveryError("journal has multiple execution receipt records")

    decision_ref = receipt_file_ref(decision_path, kind="writer_transaction_commit_decision")
    receipt_payload = dict(plan["payload"])
    validations = receipt_payload.get("validations")
    if not isinstance(validations, dict) or validations.get("ok") is not True:
        raise WriterTransactionRecoveryError("execution receipt validations must be an ok=true object")
    if "writer_transaction" in validations:
        raise WriterTransactionRecoveryError("execution receipt writer transaction evidence is writer-managed")
    receipt_payload["validations"] = {
        **validations,
        "writer_transaction": {
            **decision_ref,
            "transaction_id": decision["transaction_id"],
            "journal": {
                "path": str(journal_path),
                "sha256_before_decision": decision["journal"]["sha256_before_decision"],
            },
        },
    }
    rendered = json.dumps(receipt_payload, ensure_ascii=False, indent=2) + "\n"
    expected_post_sha256 = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    index = plan.get("index")
    if not isinstance(index, int) or index != len(decision["files"]):
        raise WriterTransactionRecoveryError("execution receipt transaction index mismatch")

    if existing_records:
        record = existing_records[0]
        if (
            record.get("index") != index
            or record.get("relative_path") != plan.get("relative_path")
            or record.get("pre") != plan.get("pre")
            or record.get("post", {}).get("sha256") != expected_post_sha256
        ):
            raise WriterTransactionRecoveryError("execution receipt transaction record drift")
        post_copy = transaction_snapshot_path(journal_path.parent, record["post"])
        if sha256_file(post_copy) != expected_post_sha256:
            raise WriterTransactionRecoveryError(f"execution receipt snapshot drift: {post_copy}")
        return record

    post = snapshot_text(
        text=rendered,
        copy_path=journal_path.parent / "copies" / f"{index:03d}.receipt-post",
    )
    if post["sha256"] != expected_post_sha256:
        raise WriterTransactionRecoveryError("execution receipt snapshot hash mismatch")
    record = {
        "index": index,
        "kind": "execution_receipt",
        "relative_path": plan["relative_path"],
        "action": "replace" if plan["pre"]["exists"] else "create",
        "pre": plan["pre"],
        "post": post,
        "source_path": None,
        "source_sha256": None,
        "apply_status": "pending",
        "compensation_status": "pending",
        "last_error": None,
    }
    journal["files"].append(record)
    write_transaction_journal(journal_path, journal)
    return record


def complete_committed_writer_transaction(
    *,
    kb_root: Path,
    journal_path: Path,
    journal: dict[str, Any],
) -> dict[str, str]:
    decision_path, decision = load_commit_decision(journal_path=journal_path, journal=journal)
    decision_ref = attach_commit_decision_to_journal(
        journal_path=journal_path,
        journal=journal,
        decision_path=decision_path,
    )
    for record in journal["files"]:
        if record.get("kind") == "execution_receipt":
            continue
        ensure_transaction_file_committed(
            kb_root=kb_root,
            journal_path=journal_path,
            journal=journal,
            record=record,
        )
    receipt_record = prepare_commit_receipt_record(
        journal_path=journal_path,
        journal=journal,
        decision_path=decision_path,
        decision=decision,
    )
    if receipt_record is not None:
        ensure_transaction_file_committed(
            kb_root=kb_root,
            journal_path=journal_path,
            journal=journal,
            record=receipt_record,
        )
    if journal.get("status") != "committed":
        transition_transaction(journal_path, journal, "committed")
    return decision_ref


def commit_writer_transaction(
    *,
    kb_root: Path,
    journal_path: Path,
    journal: dict[str, Any],
    receipt_out: Path | None = None,
    receipt_payload: dict[str, Any] | None = None,
) -> dict[str, str]:
    publish_commit_decision(
        kb_root=kb_root,
        journal_path=journal_path,
        journal=journal,
        receipt_out=receipt_out,
        receipt_payload=receipt_payload,
    )
    return complete_committed_writer_transaction(
        kb_root=kb_root,
        journal_path=journal_path,
        journal=journal,
    )


def compensate_transaction_file(
    *,
    kb_root: Path,
    journal_path: Path,
    record: dict[str, Any],
) -> None:
    destination = transaction_file_path(kb_root, record)
    pre = record["pre"]
    post = record["post"]
    current = file_state(destination)
    if state_matches(current, pre):
        record["compensation_status"] = "already_pre"
        record["last_error"] = None
        return
    if not state_matches(current, post):
        raise RuntimeError(
            f"compensation drift for {record['relative_path']}: current state matches neither pre nor post"
        )
    if pre.get("exists"):
        hold_path = transaction_hold_path(journal_path.parent, record)
        require_file_state(hold_path, pre, f"compensate-{record['index']} pre hold")
        displaced_post = journal_path.parent / "compensation-holds" / f"{int(record['index']):03d}.post"
        if displaced_post.exists():
            require_file_state(displaced_post, post, f"compensate-{record['index']} displaced post")
            require_file_state(destination, {"exists": False, "sha256": None}, f"compensate-{record['index']} target")
        else:
            atomic_move_noreplace(destination, displaced_post)
            try:
                require_file_state(displaced_post, post, f"compensate-{record['index']} displaced post")
            except Exception:
                if not destination.exists():
                    restore_moved_file_noreplace(
                        hold_path=displaced_post,
                        destination=destination,
                        expected=file_state(displaced_post),
                        label=f"compensate-{record['index']} raced post",
                    )
                raise
        try:
            restore_moved_file_noreplace(
                hold_path=hold_path,
                destination=destination,
                expected=pre,
                label=f"compensate-{record['index']}",
            )
        except Exception:
            if not destination.exists() and displaced_post.exists():
                restore_moved_file_noreplace(
                    hold_path=displaced_post,
                    destination=destination,
                    expected=post,
                    label=f"compensate-{record['index']} rollback",
                )
            raise
    else:
        removed_path = journal_path.parent / "compensation-holds" / f"{int(record['index']):03d}.created"
        if removed_path.exists():
            require_file_state(removed_path, post, f"compensate-delete held {record['relative_path']}")
            require_file_state(destination, pre, f"compensate-delete target {record['relative_path']}")
        else:
            atomic_move_noreplace(destination, removed_path)
            try:
                require_file_state(removed_path, post, f"compensate-delete held {record['relative_path']}")
            except Exception:
                if not destination.exists():
                    restore_moved_file_noreplace(
                        hold_path=removed_path,
                        destination=destination,
                        expected=file_state(removed_path),
                        label=f"compensate-delete raced {record['relative_path']}",
                    )
                raise
        require_file_state(destination, pre, f"compensate-delete {record['relative_path']}")
    record["compensation_status"] = "compensated"
    record["last_error"] = None


def compensate_writer_transaction(
    *,
    kb_root: Path,
    journal_path: Path,
    journal: dict[str, Any],
    reason: str,
) -> None:
    if transaction_has_commit_decision(journal_path, journal):
        raise WriterTransactionRecoveryError(
            f"refusing to compensate transaction with a durable commit decision: {journal_path}"
        )
    transition_transaction(journal_path, journal, "compensating", detail=reason)
    failures: list[str] = []
    for record in reversed(journal["files"]):
        try:
            compensate_transaction_file(
                kb_root=kb_root,
                journal_path=journal_path,
                record=record,
            )
        except Exception as exc:  # noqa: BLE001
            record["compensation_status"] = "recovery_required"
            record["last_error"] = f"{type(exc).__name__}: {exc}"
            failures.append(f"{record['relative_path']}: {type(exc).__name__}: {exc}")
        try:
            write_transaction_journal(journal_path, journal)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"journal update: {type(exc).__name__}: {exc}")
    if failures:
        detail = "; ".join(failures)
        journal["last_error"] = detail
        try:
            transition_transaction(journal_path, journal, "recovery_required", detail=detail)
        except Exception as exc:  # noqa: BLE001
            raise WriterTransactionRecoveryError(
                f"writer transaction recovery_required and journal update failed: {journal_path}: {exc}"
            ) from exc
        raise WriterTransactionRecoveryError(
            f"writer transaction recovery_required: {journal_path}: {detail}"
        )
    journal["last_error"] = None
    transition_transaction(journal_path, journal, "compensated", detail=reason)


def load_transaction_journal(journal_path: Path) -> dict[str, Any]:
    payload = json.loads(journal_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("kind") != "curation_writer_transaction":
        raise RuntimeError(f"invalid writer transaction journal: {journal_path}")
    if payload.get("status") not in TRANSACTION_STATES or not isinstance(payload.get("files"), list):
        raise RuntimeError(f"invalid writer transaction state: {journal_path}")
    return payload


def recover_incomplete_writer_transactions(
    *,
    kb_root: Path,
    question_id: str,
    allow_recovery: bool,
) -> list[dict[str, str]]:
    question_root = (
        kb_root
        / "skills/_ops/runtime/state/role_d_curation/writer_transactions"
        / question_id
    ).resolve()
    if not question_root.exists():
        return []
    recovered: list[dict[str, str]] = []
    for journal_path in sorted(question_root.glob("*/journal.json")):
        journal = load_transaction_journal(journal_path)
        if journal.get("question_id") != question_id:
            raise RuntimeError(f"writer transaction question binding mismatch: {journal_path}")
        if journal["status"] == "committed":
            continue
        if journal["status"] == "compensated":
            if commit_decision_path(journal_path).is_file():
                raise WriterTransactionRecoveryError(
                    f"compensated writer transaction has a commit decision: {journal_path}"
                )
            continue
        if not allow_recovery:
            recovery_mode = "commit completion" if transaction_has_commit_decision(journal_path, journal) else "compensation"
            raise WriterTransactionRecoveryError(
                f"unfinished writer transaction status={journal['status']} requires {recovery_mode}: {journal_path}"
            )
        if transaction_has_commit_decision(journal_path, journal):
            complete_committed_writer_transaction(
                kb_root=kb_root,
                journal_path=journal_path,
                journal=journal,
            )
        else:
            compensate_writer_transaction(
                kb_root=kb_root,
                journal_path=journal_path,
                journal=journal,
                reason="automatic recovery on writer startup",
            )
        recovered.append(receipt_file_ref(journal_path, kind="writer_transaction_journal"))
    return recovered


def transaction_committed_files(
    *,
    journal_path: Path,
    journal: dict[str, Any],
) -> list[dict[str, Any]]:
    committed: list[dict[str, Any]] = []
    for record in journal["files"]:
        if record.get("kind") == "execution_receipt":
            continue
        backup = None
        if record.get("action") in {"update", "replace"}:
            pre_copy = transaction_snapshot_path(journal_path.parent, record["pre"])
            backup = receipt_file_ref(pre_copy, kind="backup")
        committed.append(
            {
                "relative_path": record["relative_path"],
                "action": record["action"],
                "before_sha256": record["pre"].get("sha256"),
                "after_sha256": record["post"].get("sha256"),
                "backup": backup,
            }
        )
    return committed


def load_hash_bound_json(path: Path, expected_sha256: str, label: str) -> dict[str, Any]:
    if not HASH_RE.fullmatch(expected_sha256):
        raise ValueError(f"{label} sha256 must be 64 lowercase hex characters")
    if not path.is_file() or sha256_file(path) != expected_sha256:
        raise RuntimeError(f"{label} hash conflict")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def resolve_scope_path(kb_root: Path, scope: dict[str, Any]) -> Path:
    path_id = str(scope.get("path_id") or "")
    relative = str(scope.get("relative_path") or "")
    try:
        base = kb_root.resolve() if path_id == "project.root" else resolve_path(path_id, start=kb_root)
    except ProjectLayoutError as exc:
        raise PermissionError(f"unknown mission path_id: {path_id}") from exc
    resolved = (base / relative).resolve()
    resolved.relative_to(base.resolve())
    return resolved


def validate_batch_binding(
    *,
    kb_root: Path,
    batch_card_path: Path,
    batch_card_sha256: str,
    question_id: str,
    target_rel: str,
    expected_hash: str,
    updates: dict[str, Any],
    media_files: list[dict[str, str]],
    authorization_path: Path,
    authorization_sha256: str,
    audit_result_path: Path | None,
    audit_result_sha256: str | None,
    receipt_out: Path,
    apply_mode: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    card = load_hash_bound_json(batch_card_path, batch_card_sha256, "batch card")
    if card.get("schema_version") != 3 or card.get("kind") != "batch_card":
        raise ValueError("batch card must use schema_version=3")
    campaign_id = str(card.get("campaign_id") or "")
    state_path = kb_root / "skills/_ops/runtime/state/agent_batches_v3" / campaign_id / "campaign.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    active = state.get("active_run")
    if not isinstance(active, dict):
        raise PermissionError("campaign has no active v3 run")
    if active.get("run_id") != card.get("run_id") or active.get("batch_id") != card.get("batch_id"):
        raise PermissionError("batch card is not the active run")
    if (active.get("card_ref") or {}).get("sha256") != batch_card_sha256:
        raise PermissionError("active batch-card hash mismatch")
    card_item = next((item for item in card.get("items", []) if item.get("question_id") == question_id), None)
    if not isinstance(card_item, dict) or (card_item.get("target_ref") or {}).get("relative_path") != target_rel:
        raise PermissionError("question target is outside the batch card")
    if (card_item.get("target_ref") or {}).get("sha256") != expected_hash:
        raise PermissionError("proposal baseline differs from the batch card")
    if apply_mode:
        if card.get("phase") != "auditor" or active.get("phase") != "applying":
            raise PermissionError("only an independently audited v3 run may apply")
    elif card.get("phase") not in {"executor", "repair"} or active.get("phase") not in {"executor", "repair"}:
        raise PermissionError("dry-run requires an active executor or repair run")
    grant = load_hash_bound_json(authorization_path, authorization_sha256, "authorization")
    if state.get("authorization_ref") != {"path": str(authorization_path), "sha256": authorization_sha256}:
        raise PermissionError("authorization does not match the active campaign")
    if grant.get("campaign_id") != campaign_id:
        raise PermissionError("authorization campaign mismatch")
    if grant.get("ledger_sha256") != (state.get("ledger_ref") or {}).get("sha256"):
        raise PermissionError("authorization ledger binding mismatch")
    authorization_record_path = ensure_inside(Path(str(grant.get("authorization_record_path") or "")), kb_root)
    authorization_record_sha = str(grant.get("authorization_record_sha256") or "")
    if not authorization_record_path.is_file() or sha256_file(authorization_record_path) != authorization_record_sha:
        raise PermissionError("teacher authorization record hash mismatch")
    if grant.get("writer") != "exercise-solution-curation/curate_exercise.py":
        raise PermissionError("authorization does not allow this canonical writer")
    if grant.get("destructive") is not False:
        raise PermissionError("curation authorization must use destructive=false")
    allowed_fields = set(grant.get("allowed_fields") or [])
    requested_fields = set(updates)
    if media_files:
        requested_fields.add("media")
    if not requested_fields.issubset(allowed_fields):
        raise PermissionError(f"authorization grant does not allow fields: {sorted(requested_fields - allowed_fields)}")
    expires_at = str(grant.get("expires_at") or "")
    try:
        expiration = dt.datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("authorization grant expires_at must be ISO-8601") from exc
    if expiration <= dt.datetime.now(dt.timezone.utc):
        raise PermissionError("authorization has expired")
    campaign_root = state_path.parent.resolve()
    if apply_mode:
        expected_receipt = campaign_root / "receipts" / card["batch_id"] / f"{question_id}.apply.json"
        if receipt_out.resolve() != expected_receipt:
            raise PermissionError("apply receipt path is not derived from the active batch")
        if audit_result_path is None or audit_result_sha256 is None:
            raise PermissionError("apply requires the independent audit result")
        audit_result = load_hash_bound_json(audit_result_path, audit_result_sha256, "audit result")
        bindings = {
            "schema_version": 3,
            "kind": "batch_result",
            "campaign_id": campaign_id,
            "batch_id": card["batch_id"],
            "run_id": card["run_id"],
            "phase": "auditor",
            "batch_card_sha256": batch_card_sha256,
        }
        if any(audit_result.get(key) != value for key, value in bindings.items()):
            raise PermissionError("audit result does not bind the active batch")
        audit_row = next((item for item in audit_result.get("items", []) if item.get("question_id") == question_id), None)
        if not isinstance(audit_row, dict) or audit_row.get("verdict") != "passed":
            raise PermissionError("question did not pass the independent audit")
    else:
        artifact_root = ensure_inside(Path(card["artifact_root"]), campaign_root)
        ensure_inside(receipt_out, artifact_root)
        audit_result = {}
    return card, grant, state, audit_result


def receipt_file_ref(path: Path, *, kind: str) -> dict[str, str]:
    return {"kind": kind, "path": str(path), "sha256": sha256_file(path)}


def validate_prior_dry_run_receipt(
    *,
    path: Path,
    expected_sha256: str,
    campaign_id: str,
    proposal_path: Path,
    question_id: str,
    expected_before_sha256: str,
    expected_after_sha256: str,
) -> dict[str, Any]:
    receipt = load_hash_bound_json(path, expected_sha256, "dry-run receipt")
    bindings = {
        "schema_version": 3,
        "kind": "dry_run_receipt",
        "status": "verified",
        "campaign_id": campaign_id,
        "question_id": question_id,
    }
    mismatched = [field for field, expected in bindings.items() if receipt.get(field) != expected]
    if mismatched:
        raise RuntimeError(f"dry-run receipt binding mismatch: {mismatched}")
    proposal_ref = receipt.get("proposal")
    if not isinstance(proposal_ref, dict) or proposal_ref.get("sha256") != sha256_file(proposal_path):
        raise RuntimeError("dry-run receipt proposal hash mismatch")
    files = receipt.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError("dry-run receipt lacks predicted files")
    target_file = files[0]
    if (
        target_file.get("before_sha256") != expected_before_sha256
        or target_file.get("after_sha256") != expected_after_sha256
    ):
        raise RuntimeError("dry-run receipt target hashes no longer match")
    return receipt


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely curate a complete exercise record.")
    parser.add_argument("--proposal", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--teacher-authorized", action="store_true")
    parser.add_argument("--batch-card")
    parser.add_argument("--batch-card-sha256")
    parser.add_argument("--audit-result")
    parser.add_argument("--audit-result-sha256")
    parser.add_argument("--authorization")
    parser.add_argument("--authorization-sha256")
    parser.add_argument("--dry-run-receipt")
    parser.add_argument("--dry-run-receipt-sha256")
    parser.add_argument("--receipt-out")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--kb-root", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    transaction_journal_path: Path | None = None
    transaction_journal: dict[str, Any] | None = None
    try:
        if args.apply and not args.teacher_authorized:
            raise PermissionError("--apply requires --teacher-authorized")
        kb_root = DEFAULT_KB_ROOT
        if args.kb_root:
            if not args.test_mode:
                raise PermissionError("--kb-root is available only with --test-mode")
            kb_root = Path(args.kb_root).resolve()
        proposal_path = Path(args.proposal).resolve()
        payload = json.loads(proposal_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("proposal must be a JSON object")
        schema_version, model_profile, question_id, target_rel, expected_hash, updates, media_files, verification = validate_proposal(payload)
        batch_fields = (
            args.batch_card,
            args.batch_card_sha256,
            args.authorization,
            args.authorization_sha256,
            args.receipt_out,
        )
        batch_mode = any(value is not None for value in batch_fields)
        if batch_mode and not all(value is not None for value in batch_fields):
            raise ValueError("v3 writes require --batch-card, --authorization and --receipt-out with hashes")
        if not batch_mode and any(value is not None for value in (args.dry_run_receipt, args.dry_run_receipt_sha256, args.audit_result, args.audit_result_sha256)):
            raise ValueError("batch receipts and audits require v3 batch mode")
        if args.apply and batch_mode and not all(
            value is not None
            for value in (args.dry_run_receipt, args.dry_run_receipt_sha256, args.audit_result, args.audit_result_sha256)
        ):
            raise ValueError("v3 apply requires hash-bound dry-run and independent audit results")
        card: dict[str, Any] | None = None
        grant: dict[str, Any] | None = None
        grant_sha256: str | None = None
        campaign_state: dict[str, Any] | None = None
        audit_result: dict[str, Any] | None = None
        batch_card_path: Path | None = None
        receipt_out: Path | None = None
        if batch_mode:
            batch_card_path = ensure_inside(Path(args.batch_card), kb_root)
            receipt_out = ensure_inside(Path(args.receipt_out), kb_root)
            authorization_path = ensure_inside(Path(args.authorization), kb_root)
            audit_result_path = ensure_inside(Path(args.audit_result), kb_root) if args.audit_result else None
            card, grant, campaign_state, audit_result = validate_batch_binding(
                kb_root=kb_root,
                batch_card_path=batch_card_path,
                batch_card_sha256=args.batch_card_sha256,
                question_id=question_id,
                target_rel=target_rel,
                expected_hash=expected_hash,
                updates=updates,
                media_files=media_files,
                authorization_path=authorization_path,
                authorization_sha256=args.authorization_sha256,
                audit_result_path=audit_result_path,
                audit_result_sha256=args.audit_result_sha256,
                receipt_out=receipt_out,
                apply_mode=args.apply,
            )
            grant_sha256 = args.authorization_sha256
        recovered_transactions = recover_incomplete_writer_transactions(
            kb_root=kb_root,
            question_id=question_id,
            allow_recovery=args.apply,
        )
        exercise_root = (kb_root / "raw" / "exercises").resolve()
        target = ensure_inside(kb_root / target_rel, exercise_root)
        if not target.is_file():
            raise FileNotFoundError(target)
        if target.stem.lower() != question_id.lower():
            raise ValueError("question_id does not match target filename")
        actual_hash = sha256_file(target)
        if actual_hash != expected_hash:
            raise RuntimeError(f"hash conflict: expected {expected_hash}, actual {actual_hash}")
        original = target.read_text(encoding="utf-8")
        if verification:
            for evidence in verification["source_evidence"]:
                evidence_path = ensure_inside(kb_root / evidence["path"], kb_root)
                if not evidence_path.is_file():
                    raise FileNotFoundError(evidence_path)
                if sha256_file(evidence_path) != evidence["sha256"]:
                    raise RuntimeError(f"source evidence hash conflict: {evidence['path']}")
        updated = original
        section_map = {"stem": "题目", "answer": "答案", "solution": "详解"}
        for field, heading in section_map.items():
            if field in updates:
                updated = replace_h2_section(updated, heading, str(updates[field]))
        if "assets" in updates:
            updated = replace_frontmatter_list(updated, "assets", list(updates["assets"]))
        if "ai_extra_tags" in updates:
            updated = replace_frontmatter_list(updated, "ai_extra_tags", list(updates["ai_extra_tags"]))

        original_refs = body_media_refs(original)
        updated_refs = body_media_refs(updated)
        if updated_refs != original_refs and "assets" not in updates:
            raise ValueError("changing body image references requires updates.assets")
        if "assets" in updates and sorted(updates["assets"]) != updated_refs:
            raise ValueError("updates.assets must exactly match final body media references")
        media_targets = {item["target"] for item in media_files}
        if not media_targets.issubset(set(updated_refs)):
            raise ValueError("every media_files target must be referenced by the final question body")
        validate_explicit_figure_dependency(updated)
        if verification and question_id.upper().startswith(("MC", "MA")):
            final_options = normalize_choice_options(extract_h2_section(updated, "答案"))
            if final_options != verification["derived_correct_options"]:
                raise ValueError(
                    "derived_correct_options do not match the final answer: "
                    f"derived={verification['derived_correct_options']} final={final_options}"
                )

        prepared_media: list[dict[str, Any]] = []
        media_root = (target.parent / "media").resolve()
        for item in media_files:
            source = ensure_inside(kb_root / item["source_path"], kb_root)
            if not source.is_file():
                raise FileNotFoundError(source)
            if sha256_file(source) != item["source_sha256"]:
                raise RuntimeError(f"media hash conflict: {item['source_path']}")
            destination = ensure_inside(target.parent / item["target"], media_root)
            if destination.suffix.lower() not in MEDIA_EXTENSIONS:
                raise ValueError(f"unsupported media target: {item['target']}")
            action = "create"
            if destination.exists():
                current_target_hash = sha256_file(destination)
                if current_target_hash == item["source_sha256"]:
                    action = "reuse"
                else:
                    expected_target_hash = item.get("expected_target_sha256")
                    if not expected_target_hash:
                        raise FileExistsError(
                            f"media replacement requires expected_target_sha256: {item['target']}"
                        )
                    if current_target_hash != expected_target_hash:
                        raise RuntimeError(f"media target hash conflict: {item['target']}")
                    action = "replace"
            elif item.get("expected_target_sha256"):
                raise FileNotFoundError(f"media target to replace does not exist: {item['target']}")
            prepared_media.append({**item, "source": source, "destination": destination, "action": action})
        if original == updated:
            if not any(item["action"] in {"create", "replace"} for item in prepared_media):
                raise ValueError("proposal produces no change")

        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        report_root = kb_root / "skills" / "_ops" / "runtime" / "reports" / "role_d_curation" / f"{question_id}_{timestamp}"
        report_root.mkdir(parents=True, exist_ok=True)
        diff_text = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=target_rel,
                tofile=target_rel,
            )
        )
        diff_path = report_root / "curation.diff"
        diff_path.write_text(diff_text, encoding="utf-8", newline="\n")
        warnings: list[str] = []
        if (
            "answer" in updates
            and "solution" in updates
            and re.fullmatch(r"[A-H]", updates["answer"], flags=re.IGNORECASE)
            and not re.search(
                rf"(?<![A-Za-z]){re.escape(updates['answer'])}(?![A-Za-z])",
                updates["solution"],
                flags=re.IGNORECASE,
            )
        ):
            warnings.append("choice answer letter is not explicitly repeated in solution")

        result: dict[str, Any] = {
            "ok": True,
            "mode": "apply" if args.apply else "dry-run",
            "schema_version": schema_version,
            "model_profile": model_profile,
            "question_id": question_id,
            "target_path": target_rel,
            "expected_sha256": expected_hash,
            "new_sha256": hashlib.sha256(updated.encode("utf-8")).hexdigest(),
            "diff": str(diff_path),
            "warnings": warnings,
            "applied": False,
            "rolled_back": False,
            "validations": [],
            "media_files": [
                {"source_path": item["source_path"], "target": item["target"], "action": item["action"]}
                for item in prepared_media
            ],
            "verification": verification,
            "recovered_transactions": recovered_transactions,
        }

        predicted_files: list[dict[str, Any]] = [
            {
                "relative_path": target_rel,
                "action": "update" if original != updated else "reuse",
                "before_sha256": expected_hash,
                "after_sha256": result["new_sha256"],
                "backup": None,
            }
        ]
        for item in prepared_media:
            relative = (target.parent / item["target"]).resolve().relative_to(kb_root.resolve()).as_posix()
            before_media_sha = sha256_file(item["destination"]) if item["destination"].is_file() else None
            predicted_files.append(
                {
                    "relative_path": relative,
                    "action": item["action"],
                    "before_sha256": before_media_sha,
                    "after_sha256": item["source_sha256"],
                    "backup": None,
                }
            )

        if batch_mode and args.dry_run:
            assert card is not None and grant_sha256 is not None and receipt_out is not None
            dry_receipt = {
                "schema_version": 3,
                "kind": "dry_run_receipt",
                "status": "verified",
                "campaign_id": card["campaign_id"],
                "batch_id": card["batch_id"],
                "run_id": card["run_id"],
                "question_id": question_id,
                "revision_no": card["revision_no"],
                "batch_card_sha256": args.batch_card_sha256,
                "authorization_sha256": grant_sha256,
                "proposal": {"path": str(proposal_path), "sha256": sha256_file(proposal_path)},
                "files": predicted_files,
                "validations": {"ok": True, "checks": ["proposal_and_hash_guards"]},
                "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            }
            write_atomic_json(receipt_out, dry_receipt)
            result["dry_run_receipt"] = receipt_file_ref(receipt_out, kind="dry_run_receipt")

        if args.apply:
            dry_run_receipt_path: Path | None = None
            if batch_mode:
                assert card is not None
                dry_run_receipt_path = ensure_inside(Path(args.dry_run_receipt), kb_root)
                validate_prior_dry_run_receipt(
                    path=dry_run_receipt_path,
                    expected_sha256=args.dry_run_receipt_sha256,
                    campaign_id=card["campaign_id"],
                    proposal_path=proposal_path,
                    question_id=question_id,
                    expected_before_sha256=expected_hash,
                    expected_after_sha256=result["new_sha256"],
                )
            if sha256_file(target) != expected_hash:
                raise RuntimeError("hash changed between dry-run and apply")
            transaction_id = card["run_id"] if batch_mode and card is not None else f"{question_id}_{timestamp}_{os.getpid()}"
            transaction_journal_path, transaction_journal = prepare_writer_transaction(
                kb_root=kb_root,
                question_id=question_id,
                transaction_id=transaction_id,
                target_rel=target_rel,
                target=target,
                expected_target_sha256=expected_hash,
                updated=updated,
                prepared_media=prepared_media,
            )
            main_record = transaction_journal["files"][0]
            main_backup = transaction_snapshot_path(transaction_journal_path.parent, main_record["pre"])
            media_backups = [
                transaction_snapshot_path(transaction_journal_path.parent, record["pre"])
                for record in transaction_journal["files"]
                if record.get("kind") == "media" and record.get("action") == "replace"
            ]
            try:
                apply_writer_transaction(
                    kb_root=kb_root,
                    journal_path=transaction_journal_path,
                    journal=transaction_journal,
                )
                result["applied"] = True
                result["backup"] = str(main_backup)
                result["media_backups"] = [str(path) for path in media_backups]

                if args.test_mode:
                    result["validations"] = [{"ok": True, "status": "skipped:test-mode"}]
                else:
                    rag_validator = kb_root / "skills" / "import" / "exercise-bank-import" / "scripts" / "validate_exercise_rag_compat.py"
                    image_validator = kb_root / "skills" / "import" / "exercise-bank-import" / "scripts" / "audit_exercise_images.py"
                    rag_report = report_root / "rag_compat.json"
                    image_report = report_root / "image_audit"
                    result["validations"] = [
                        run_validation(
                            [sys.executable, str(rag_validator), "--kb-root", str(kb_root), str(target), "--out", str(rag_report), "--json"],
                            kb_root,
                        ),
                        run_validation(
                            [sys.executable, str(image_validator), "--kb-root", str(kb_root), "--out", str(image_report), "--json", str(target)],
                            kb_root,
                        ),
                    ]
                if not all(item["ok"] for item in result["validations"]):
                    compensate_writer_transaction(
                        kb_root=kb_root,
                        journal_path=transaction_journal_path,
                        journal=transaction_journal,
                        reason="post-apply validation failed",
                    )
                    result["ok"] = False
                    result["rolled_back"] = True
                    result["applied"] = False
                    result["error"] = "post-apply validation failed; original restored"
                if result["ok"] and result["applied"]:
                    committed_files = transaction_committed_files(
                        journal_path=transaction_journal_path,
                        journal=transaction_journal,
                    )
                    apply_receipt: dict[str, Any] | None = None
                    if batch_mode:
                        assert card is not None and grant_sha256 is not None and receipt_out is not None
                        receipt_files = []
                        for record in committed_files:
                            absolute = (kb_root / record["relative_path"]).resolve()
                            receipt_files.append({
                                "path": str(absolute),
                                "before_sha256": record.get("before_sha256"),
                                "after_sha256": record.get("after_sha256"),
                                "backup_path": (record.get("backup") or {}).get("path"),
                                "backup_sha256": (record.get("backup") or {}).get("sha256"),
                            })
                        apply_receipt = {
                            "schema_version": 3,
                            "kind": "apply_receipt",
                            "status": "applied",
                            "campaign_id": card["campaign_id"],
                            "batch_id": card["batch_id"],
                            "run_id": card["run_id"],
                            "question_id": question_id,
                            "revision_no": card["revision_no"],
                            "batch_card_sha256": args.batch_card_sha256,
                            "authorization_sha256": grant_sha256,
                            "proposal": {"path": str(proposal_path), "sha256": sha256_file(proposal_path)},
                            "dry_run": {
                                "path": str(dry_run_receipt_path),
                                "sha256": args.dry_run_receipt_sha256,
                            },
                            "audit_result": {"path": str(args.audit_result), "sha256": args.audit_result_sha256},
                            "files": receipt_files,
                            "validations": {"ok": True, "checks": result["validations"]},
                            "created_at": utc_now(),
                        }
                    commit_writer_transaction(
                        kb_root=kb_root,
                        journal_path=transaction_journal_path,
                        journal=transaction_journal,
                        receipt_out=receipt_out if batch_mode else None,
                        receipt_payload=apply_receipt,
                    )
                    result["transaction_journal"] = receipt_file_ref(
                        transaction_journal_path,
                        kind="writer_transaction_journal",
                    )
                    if batch_mode:
                        assert receipt_out is not None
                        result["apply_receipt"] = receipt_file_ref(receipt_out, kind="apply_receipt")
                else:
                    result["transaction_journal"] = receipt_file_ref(
                        transaction_journal_path,
                        kind="writer_transaction_journal",
                    )
            except Exception as exc:
                if (
                    transaction_journal is not None
                    and transaction_journal_path is not None
                    and transaction_journal.get("status") not in TRANSACTION_TERMINAL_STATES
                    and not isinstance(exc, WriterTransactionRecoveryError)
                ):
                    if transaction_has_commit_decision(transaction_journal_path, transaction_journal):
                        raise WriterTransactionRecoveryError(
                            "writer failed after the durable commit decision; startup recovery must finish the commit: "
                            f"{transaction_journal_path}"
                        ) from exc
                    try:
                        compensate_writer_transaction(
                            kb_root=kb_root,
                            journal_path=transaction_journal_path,
                            journal=transaction_journal,
                            reason=f"apply failed: {type(exc).__name__}: {exc}",
                        )
                    except WriterTransactionRecoveryError as recovery_exc:
                        raise recovery_exc from exc
                raise

        report_path = report_root / "curation_report.json"
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["report"] = str(report_path)
        if args.json:
            emit_json(result)
        else:
            print(str(report_path))
        return 0 if result["ok"] else 1
    except Exception as exc:  # noqa: BLE001
        error = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        if transaction_journal_path is not None and transaction_journal_path.is_file():
            try:
                failed_journal = load_transaction_journal(transaction_journal_path)
                error["transaction_journal"] = {
                    **receipt_file_ref(transaction_journal_path, kind="writer_transaction_journal"),
                    "status": failed_journal["status"],
                }
            except Exception:  # noqa: BLE001
                error["transaction_journal"] = {
                    "kind": "writer_transaction_journal",
                    "path": str(transaction_journal_path),
                    "status": "unreadable",
                }
        emit_json(error, error=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
