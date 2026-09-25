#!/usr/bin/env python
"""Shared deterministic helpers for the physics diagram pipeline."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any


SCRIPT = Path(__file__).resolve()
KB_ROOT = SCRIPT.parents[4]
SHARED_SCRIPTS = KB_ROOT / "skills" / "_shared" / "scripts"
if str(SHARED_SCRIPTS) not in os.sys.path:
    os.sys.path.insert(0, str(SHARED_SCRIPTS))

from project_paths import resolve_path  # noqa: E402


TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def ensure_inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(root.resolve())
    return resolved


def ensure_task_id(task_id: str) -> str:
    if not TASK_ID_RE.fullmatch(task_id):
        raise ValueError("task-id must be 1-64 ASCII letters, digits, dot, underscore, or hyphen")
    return task_id


def finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def task_base(task_id: str, override: Path | None = None) -> Path:
    ensure_task_id(task_id)
    if override is not None:
        root = override.resolve()
    else:
        root = resolve_path("workspace.tmp", start=KB_ROOT) / "tasks"
    return root / task_id / "physics-diagram"


def output_base(override: Path | None = None) -> Path:
    return override.resolve() if override is not None else resolve_path("workspace.output", start=KB_ROOT)


def resolve_project_input(value: str, *, must_exist: bool = True) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = KB_ROOT / candidate
    candidate = candidate.resolve()
    ensure_inside(candidate, KB_ROOT)
    if must_exist and not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def fingerprint_files(paths: list[Path]) -> dict[str, str]:
    return {str(path): sha256_file(path) for path in paths}
