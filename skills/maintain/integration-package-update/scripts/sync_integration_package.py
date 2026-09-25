#!/usr/bin/env python
"""Dry-run or authorized sync from the development KB to the integration package."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

KB_ROOT = Path(__file__).resolve().parents[4]
SHARED_SCRIPTS = KB_ROOT / "skills" / "_shared" / "scripts"
if str(SHARED_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_SCRIPTS))

from project_paths import resolve_path


SKILLS_ROOT = resolve_path("skills.root", start=KB_ROOT)
REPORT_ROOT = resolve_path("skills.ops.runtime", start=KB_ROOT) / "reports" / "integration-package-update"
DEFAULT_PACKAGE_ROOT = resolve_path("integration.skills-mirror", start=KB_ROOT).parent

BAD_SUFFIXES = {
    ".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".db", ".faiss", ".exe",
    ".m4s", ".mp4", ".mov", ".mkv", ".wav", ".mp3",
    ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".pdf",
}
SKILL_RUNTIME_DIR_NAMES = {".runtime", ".venvs", "models"}
SKILL_RUNTIME_CHILD_NAMES = {"env", "envs", "model", "models", ".venv", "venv"}
STALE_PACKAGE_DIRS = [
    "Skills/maintain/integration-package-update",
    "Skills/_shared/runtime",
    "Skills/_shared/model-tools/runtime",
    "Skills/_ops/runtime",
    "Skills/_runtime",
    "BGE-M3/runtime",
    "BGE-M3/models",
    "BGE-M3/data",
    "BGE-M3/output",
    "BGE-M3/.venv",
    "StudentDataSQL/runtime/db",
    "StudentDataSQL/runtime/imports",
    "StudentDataSQL/runtime/exports",
    "StudentDataSQL/runtime/backups",
    "StudentDataSQL/runtime/logs",
    "StudentDataSQL/exam_reports",
    "codex_helpers",
    "Project-template/codex_helpers",
    "Project-template/原件",
    "Project-template/临时代码",
    "output",
    "tmp",
    "原件",
    "临时代码",
]
STALE_PACKAGE_FILES = [
    "Skills/_shared/schemas/agent_compact_result_v2.schema.json",
    "Skills/_shared/schemas/agent_mission_card_v2.schema.json",
    "Skills/_shared/schemas/audit_report_v2.schema.json",
    "Skills/_shared/schemas/authorization_grant_v2.schema.json",
    "Skills/_shared/schemas/execution_receipt_v2.schema.json",
    "Skills/_shared/schemas/legacy_execution_adoption_v2.schema.json",
    "Skills/_shared/schemas/platform_capabilities_v2.schema.json",
    "Skills/_shared/scripts/import_v1_mission_adapter.py",
    "Skills/_shared/tests/test_agent_campaign_e2e.py",
    "Skills/_shared/tests/test_agent_v2_schemas.py",
    "Skills/learn/exercise-solution-curation/scripts/migrate_p0p1_mission_v2.py",
    "Skills/learn/tests/test_p0p1_mission_migration.py",
]
LOCAL_RETIREMENT_FILES = {
    "_ops/audits/agent-mission-v2-retirement-20260723.json",
    "_shared/scripts/import_v1_mission_adapter.py",
    "learn/exercise-solution-curation/scripts/migrate_p0p1_mission_v2.py",
}


def posix(path: Path) -> str:
    return path.as_posix()


def rel(path: Path, root: Path) -> str:
    return posix(path.resolve().relative_to(root.resolve()))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def write_text(path: Path, text: str) -> None:
    target = accessible_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def accessible_path(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    if os.name == "nt" and not str(absolute).startswith("\\\\?\\"):
        return Path("\\\\?\\" + str(absolute))
    return absolute


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def portable_runtime_entries(registry: dict[str, Any], skills_runtime: Path, bge_runtime: Path) -> list[dict[str, Any]]:
    """Return the file-level allowlist for an authorized portable package."""
    package = registry.get("portable_package", {})
    excluded_names = set(package.get("excluded_names", [])) | {"__pycache__", ".staging", "benchmarks"}
    roots: list[tuple[Path, Path]] = []
    components = registry.get("components", {})
    for component_id in package.get("component_ids", []):
        component = components.get(component_id, {})
        relative = component.get("package_path") or component.get("path")
        if relative:
            roots.append((skills_runtime / relative, Path("Skills/_shared/model-tools/runtime") / relative))
    environments = registry.get("environments", {})
    for environment_id in package.get("environment_ids", []):
        environment = environments.get(environment_id, {})
        relative = environment.get("path")
        if relative:
            roots.append((skills_runtime / relative, Path("Skills/_shared/model-tools/runtime") / relative))
    roots.extend(
        [
            (bge_runtime / "env", Path("BGE-M3/runtime/env")),
            (bge_runtime / "models" / "BAAI" / "bge-m3", Path("BGE-M3/runtime/models/BAAI/bge-m3")),
        ]
    )
    entries: dict[str, dict[str, Any]] = {}
    for source_root, destination_root in roots:
        if not source_root.exists():
            continue
        candidates = [source_root] if source_root.is_file() else source_root.rglob("*")
        for source in candidates:
            if not source.is_file():
                continue
            relative = Path(source.name) if source_root.is_file() else source.relative_to(source_root)
            if any(part in excluded_names for part in relative.parts):
                continue
            if source.name.endswith((".pyc", ".pyo", ".pre-portable")):
                continue
            destination = (destination_root.parent / relative) if source_root.is_file() else (destination_root / relative)
            key = destination.as_posix()
            entries[key] = {"src": str(source.resolve()), "dest": key, "bytes": source.stat().st_size}
    return [entries[key] for key in sorted(entries)]


RUNTIME_MANIFEST_NAME = "PORTABLE_RUNTIME_MANIFEST.json"
RUNTIME_DEST_ROOTS = ("Skills/_shared/model-tools/runtime", "BGE-M3/runtime")


def runtime_path(path: str) -> bool:
    normalized = path.replace("\\", "/").rstrip("/")
    return any(normalized == root or normalized.startswith(root + "/") for root in RUNTIME_DEST_ROOTS)


def runtime_license_gate(registry: dict[str, Any]) -> dict[str, Any]:
    components = registry.get("components", {})
    environments = registry.get("environments", {})
    rows: dict[str, dict[str, Any]] = {}
    environment_rows: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, str]] = []
    for component_id in registry.get("portable_package", {}).get("component_ids", []):
        component = components.get(component_id, {})
        row = {
            "license": component.get("license"),
            "redistribution": component.get("redistribution"),
            "official_url": component.get("official_url"),
        }
        rows[component_id] = row
        if not row["license"]:
            issues.append({"component": component_id, "reason": "license missing"})
        if str(row["redistribution"] or "").startswith("blocked"):
            issues.append({"component": component_id, "reason": str(row["redistribution"])})
    for environment_id in registry.get("portable_package", {}).get("environment_ids", []):
        environment = environments.get(environment_id, {})
        row = {
            "license": environment.get("license"),
            "redistribution": environment.get("redistribution"),
            "requirements": environment.get("requirements"),
        }
        environment_rows[environment_id] = row
        issue_id = f"environment:{environment_id}"
        if not row["license"] or str(row["license"]).lower() == "unknown":
            issues.append({"component": issue_id, "reason": "license missing or unknown"})
        if str(row["redistribution"] or "").startswith("blocked"):
            issues.append({"component": issue_id, "reason": str(row["redistribution"])})
    return {"ok": not issues, "components": rows, "environments": environment_rows, "issues": issues}


def build_runtime_source_manifest(
    entries: list[dict[str, Any]],
    licenses: dict[str, Any],
    *,
    cache_path: Path | None = None,
    validation_lock: dict[str, Any] | None = None,
    selection_sha256: str | None = None,
) -> dict[str, Any]:
    cached = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path and cache_path.is_file() else {"files": []}
    cached_by_path = {item.get("path"): item for item in cached.get("files", [])}
    files: list[dict[str, Any]] = []
    total = 0
    reused = 0
    for index, entry in enumerate(entries, start=1):
        source = Path(entry["src"])
        stat = source.stat()
        size = stat.st_size
        total += size
        previous = cached_by_path.get(entry["dest"])
        if previous and previous.get("source_bytes") == size and previous.get("source_mtime_ns") == stat.st_mtime_ns:
            source_hash = previous["source_sha256"]
            reused += 1
        else:
            source_hash = hash_file(source)
        files.append(
            {
                "path": entry["dest"],
                "source_bytes": size,
                "source_mtime_ns": stat.st_mtime_ns,
                "source_sha256": source_hash,
            }
        )
        if index % 5000 == 0:
            print(f"runtime source hash: {index}/{len(entries)} reused={reused}", file=sys.stderr, flush=True)
    payload = {
        "schema_version": 1,
        "file_count": len(files),
        "source_bytes": total,
        "licenses": licenses,
        "validation_lock": validation_lock or {"components": {}},
        "selection_sha256": selection_sha256,
        "files": files,
    }
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def runtime_selection_sha256(registry: dict[str, Any]) -> str:
    components = registry.get("components", {})
    environments = registry.get("environments", {})
    package = registry.get("portable_package", {})
    selected = {
        "portable_package": package,
        "components": {item: components.get(item) for item in package.get("component_ids", [])},
        "environments": {item: environments.get(item) for item in package.get("environment_ids", [])},
    }
    return hash_text(json.dumps(selected, ensure_ascii=False, sort_keys=True))


def load_cached_source_manifest(
    cache_path: Path,
    registry: dict[str, Any],
    licenses: dict[str, Any],
    validation_lock: dict[str, Any],
) -> dict[str, Any] | None:
    if not cache_path.is_file():
        return None
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    current_selection = runtime_selection_sha256(registry)
    cached_selection = cached.get("selection_sha256")
    if cached_selection not in {None, current_selection} or not cached.get("files"):
        return None
    cached["selection_sha256"] = current_selection
    cached["licenses"] = licenses
    cached["validation_lock"] = validation_lock
    cache_path.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
    return cached


def runtime_sync_summary(source_manifest: dict[str, Any], package_root: Path) -> dict[str, Any]:
    manifest_path = package_root / RUNTIME_MANIFEST_NAME
    existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {"files": []}
    existing_by_path = {item.get("path"): item for item in existing.get("files", [])}
    counts = {"create": 0, "update": 0, "unchanged": 0, "delete": 0}
    runtime_present = all((package_root / root).exists() for root in RUNTIME_DEST_ROOTS)
    for item in source_manifest.get("files", []):
        previous = existing_by_path.get(item["path"])
        unchanged = bool(
            previous
            and previous.get("source_sha256") == item.get("source_sha256")
            and previous.get("source_bytes") == item.get("source_bytes")
            and runtime_present
        )
        counts["unchanged" if unchanged else ("update" if previous else "create")] += 1
    source_paths = {item["path"] for item in source_manifest.get("files", [])}
    counts["delete"] = sum(1 for path in existing_by_path if path not in source_paths)
    return counts


def verify_staged_runtime(
    stage_root: Path,
    source_manifest: dict[str, Any],
) -> dict[str, Any]:
    source_by_path = {item["path"]: item for item in source_manifest["files"]}
    files: list[dict[str, Any]] = []
    total = 0
    for index, path in enumerate(sorted(source_by_path), start=1):
        staged = accessible_path(stage_root / path)
        if not staged.is_file():
            raise RuntimeError(f"staged runtime file missing: {path}")
        size = staged.stat().st_size
        total += size
        source_item = source_by_path[path]
        files.append(
            {
                "path": path,
                "bytes": size,
                "sha256": hash_file(staged),
                "source_bytes": source_item["source_bytes"],
                "source_sha256": source_item["source_sha256"],
            }
        )
        if index % 5000 == 0:
            print(f"runtime stage verify: {index}/{len(source_by_path)}", file=sys.stderr, flush=True)
    return {
        "schema_version": 1,
        "file_count": len(files),
        "bytes": total,
        "source_bytes": source_manifest["source_bytes"],
        "licenses": source_manifest["licenses"],
        "validation_lock": source_manifest.get("validation_lock", {"components": {}}),
        "files": files,
    }


def refresh_runtime_manifest(source_manifest: dict[str, Any], package_root: Path) -> dict[str, Any]:
    manifest_path = package_root / RUNTIME_MANIFEST_NAME
    existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    existing["licenses"] = source_manifest["licenses"]
    existing["validation_lock"] = source_manifest.get("validation_lock", {"components": {}})
    existing["source_bytes"] = source_manifest["source_bytes"]
    manifest_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return {"manifest": str(manifest_path), "file_count": existing["file_count"], "bytes": existing["bytes"], "metadata_refreshed": True}


def clean_runtime_noise(package_root: Path) -> dict[str, int]:
    removed_dirs = 0
    removed_files = 0
    for relative in RUNTIME_DEST_ROOTS:
        root = accessible_path(package_root / relative)
        if not root.exists():
            continue
        for current, dirs, files in os.walk(str(root), topdown=True):
            current_path = Path(current)
            kept: list[str] = []
            for name in dirs:
                if name == "__pycache__":
                    shutil.rmtree(current_path / name)
                    removed_dirs += 1
                else:
                    kept.append(name)
            dirs[:] = kept
            for name in files:
                if name.endswith((".pyc", ".pyo", ".pre-portable")):
                    (current_path / name).unlink()
                    removed_files += 1
    return {"directories": removed_dirs, "files": removed_files}


def apply_runtime_entries(
    entries: list[dict[str, Any]],
    source_manifest: dict[str, Any],
    package_root: Path,
) -> dict[str, Any]:
    volume_root = Path(package_root.anchor).resolve()
    stage_root = volume_root / f"llmwiki-runtime-stage-{uuid.uuid4().hex[:10]}"
    backup_root = volume_root / f"llmwiki-runtime-backup-{uuid.uuid4().hex[:10]}"

    def ensure_managed_temp(path: Path, prefix: str) -> None:
        resolved = path.resolve()
        if resolved.parent != volume_root or not resolved.name.startswith(prefix):
            raise RuntimeError(f"unsafe runtime temp path: {resolved}")
    promoted: list[Path] = []
    promoted_files: list[Path] = []
    backed_up: list[tuple[Path, Path]] = []
    try:
        for index, entry in enumerate(entries, start=1):
            destination = stage_root / entry["dest"]
            ensure_inside(destination, stage_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(entry["src"]), destination)
            if index % 5000 == 0:
                print(f"runtime stage copy: {index}/{len(entries)}", file=sys.stderr, flush=True)
        verify_staged_runtime(stage_root, source_manifest)
        for relative in RUNTIME_DEST_ROOTS:
            target = package_root / relative
            staged = stage_root / relative
            if target.exists():
                backup = backup_root / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(backup))
                backed_up.append((target, backup))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged), str(target))
            promoted.append(target)
        manifest_path = package_root / RUNTIME_MANIFEST_NAME
        if manifest_path.exists():
            backup_manifest = backup_root / RUNTIME_MANIFEST_NAME
            backup_manifest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(manifest_path), str(backup_manifest))
            backed_up.append((manifest_path, backup_manifest))
        relocate = KB_ROOT / "skills" / "_shared" / "model-tools" / "scripts" / "relocate_runtime_envs.py"
        package_python = package_root / "Skills" / "_shared" / "model-tools" / "runtime" / "python" / "cpython-3.11.15-windows-x86_64-none" / "python.exe"
        result = subprocess.run(
            [str(package_python), str(relocate), "--kb-root", str(package_root), "--apply", "--user-authorized", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
        if result.returncode != 0:
            raise RuntimeError(f"packaged runtime relocation failed: {result.stdout[-4000:]} {result.stderr[-4000:]}")
        cleaned = clean_runtime_noise(package_root)
        final_manifest = verify_staged_runtime(package_root, source_manifest)
        manifest_path.write_text(json.dumps(final_manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        promoted_files.append(manifest_path)
        if backup_root.exists():
            ensure_managed_temp(backup_root, "llmwiki-runtime-backup-")
            shutil.rmtree(backup_root)
        return {
            "manifest": str(manifest_path),
            "file_count": final_manifest["file_count"],
            "bytes": final_manifest["bytes"],
            "relocation": json.loads(result.stdout),
            "runtime_noise_removed": cleaned,
        }
    except Exception:
        for target in reversed(promoted_files):
            if target.exists():
                ensure_inside(target, package_root)
                target.unlink()
        for target in reversed(promoted):
            if target.exists():
                ensure_inside(target, package_root)
                shutil.rmtree(target)
        for target, backup in reversed(backed_up):
            if backup.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(backup), str(target))
        raise
    finally:
        if stage_root.exists():
            ensure_managed_temp(stage_root, "llmwiki-runtime-stage-")
            shutil.rmtree(stage_root)
        if backup_root.exists() and not backed_up:
            ensure_managed_temp(backup_root, "llmwiki-runtime-backup-")
            shutil.rmtree(backup_root)


def ensure_inside(path: Path, root: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"unsafe package path: {path}") from exc


def package_root_from_args(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    if os.environ.get("PACKAGE_ROOT"):
        return Path(os.environ["PACKAGE_ROOT"]).expanduser().resolve()
    return DEFAULT_PACKAGE_ROOT.resolve()


def yaml_value(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, flags=re.MULTILINE)
    return match.group(1).strip().strip('"').strip("'") if match else None


def discover_local_only() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for manifest in SKILLS_ROOT.glob("*/*/manifest.yaml"):
        text = read_text(manifest)
        lower = text.lower()
        if "package_export: false" not in lower and "distribution: local_only" not in lower:
            continue
        name = yaml_value(text, "name") or manifest.parent.name
        result[name] = {
            "dir": rel(manifest.parent, SKILLS_ROOT),
            "skill": rel(manifest.parent / "SKILL.md", SKILLS_ROOT),
            "manifest": rel(manifest, SKILLS_ROOT),
        }
    return result


def filter_named_blocks(text: str, local_names: set[str]) -> tuple[str, list[str]]:
    lines = text.splitlines()
    output: list[str] = []
    filtered: list[str] = []
    i = 0
    while i < len(lines):
        match = re.match(r"^  - name:\s*(.+?)\s*$", lines[i])
        if not match:
            output.append(lines[i])
            i += 1
            continue
        name = match.group(1).strip()
        block = [lines[i]]
        i += 1
        while i < len(lines) and not re.match(r"^  - name:\s*", lines[i]):
            block.append(lines[i])
            i += 1
        if name in local_names:
            filtered.append(name)
        else:
            output.extend(block)
    return "\n".join(output).rstrip() + "\n", filtered


def filter_script_blocks(text: str, local_dirs: set[str]) -> tuple[str, list[str]]:
    lines = text.splitlines()
    output: list[str] = []
    filtered: list[str] = []
    i = 0
    while i < len(lines):
        match = re.match(r"^  - path:\s*(.+?)\s*$", lines[i])
        if not match:
            output.append(lines[i])
            i += 1
            continue
        path_value = match.group(1).strip()
        block = [lines[i]]
        i += 1
        while i < len(lines) and not re.match(r"^  - path:\s*", lines[i]):
            block.append(lines[i])
            i += 1
        if any(path_value == d or path_value.startswith(d + "/") for d in local_dirs):
            filtered.append(path_value)
        else:
            output.extend(block)
    return "\n".join(output).rstrip() + "\n", filtered


def filter_fixture_lines(text: str, local_skill_paths: set[str]) -> tuple[str, list[str]]:
    output: list[str] = []
    filtered: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            output.append(line)
            continue
        if item.get("expected_primary") in local_skill_paths:
            filtered.append(str(item.get("query", item.get("expected_primary"))))
        else:
            output.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(output).rstrip() + "\n", filtered


def raw_readme() -> str:
    return """# raw 目录占位说明

`raw/` 是目标知识库保存预处理 Markdown 的库目录。整合包只创建目录和说明，不携带开发机生产库中的真实 raw 内容。

新设备部署后，由导入类 skill 在本地写入教材、题库、讲义、视频转写等预处理 Markdown；写入后再同步 Wiki 和 BGE-M3 RAG。
"""


def source_library_readme() -> str:
    return """# Source Library 目录占位说明

`source-library/` 是目标知识库保存导入原始文件备份的库目录。整合包只创建目录和说明，不携带开发机生产库中的 PDF、DOCX、PPT、图片、音视频或转写原件。

导入任务应在目标机器本地保存原件，并避免把这些大文件加入 Git 或教学向量索引。
"""


def exam_reports_readme() -> str:
    return """# Exam Reports 目录占位说明

`StudentDataSQL/runtime/exam-reports/` 只在目标机器本地保存试卷分析报告。整合包不携带开发机报告正文、学生数据或私有附件。
"""


def add_file(plan: list[dict[str, Any]], src: Path | None, dest: Path, package_root: Path, *, text: str | None = None, transform: str | None = None) -> None:
    ensure_inside(dest, package_root)
    if text is None:
        if src is None or not src.exists():
            return
        size = src.stat().st_size
        status = "unchanged" if dest.exists() and hash_file(src) == hash_file(dest) else ("update" if dest.exists() else "create")
        item: dict[str, Any] = {"src": rel(src, KB_ROOT), "dest": rel(dest, package_root), "status": status, "bytes": size}
    else:
        size = len(text.encode("utf-8"))
        status = "unchanged" if dest.exists() and hash_text(text) == hash_file(dest) else ("update" if dest.exists() else "create")
        item = {"dest": rel(dest, package_root), "status": status, "bytes": size, "_text": text}
    if transform:
        item["transform"] = transform
    plan.append(item)


def skill_dir_skip(rel_dir: Path, local_dirs: set[str]) -> str | None:
    parts = set(rel_dir.parts)
    lower_parts = {part.lower() for part in rel_dir.parts}
    rel_dir_posix = posix(rel_dir)
    if any(rel_dir_posix == d or rel_dir_posix.startswith(d + "/") for d in local_dirs):
        return "local-only skill is not exported"
    if "__pycache__" in parts:
        return "python cache"
    if "_archive" in parts or "_incoming" in parts:
        return "archived or incoming skill material"
    if "_runtime" in parts:
        return "skills runtime/cache/report state"
    if rel_dir_posix.startswith("_ops/runtime"):
        return "skills operations runtime/log/state/report data"
    if rel_dir_posix.startswith("_shared/runtime"):
        return "shared runtime env is installed locally"
    if rel_dir_posix.startswith("_shared/model-tools/runtime"):
        return "shared OCR/VLM/STT runtime assets are installed locally"
    if lower_parts & SKILL_RUNTIME_DIR_NAMES:
        return "skill runtime env/model/cache is installed locally"
    if "runtime" in lower_parts and any(part in SKILL_RUNTIME_CHILD_NAMES for part in lower_parts):
        return "skill runtime env/model is installed locally"
    return None


def package_skill_runtime_dir(rel_dir: Path) -> bool:
    lower_parts = {part.lower() for part in rel_dir.parts}
    if lower_parts & SKILL_RUNTIME_DIR_NAMES:
        return True
    return "runtime" in lower_parts and any(part in SKILL_RUNTIME_CHILD_NAMES for part in lower_parts)


def file_skip(rel_file: Path) -> str | None:
    if posix(rel_file) in LOCAL_RETIREMENT_FILES:
        return "local mission v2 retirement evidence"
    if rel_file.name in {"Thumbs.db", "Desktop.ini", ".DS_Store"}:
        return "os/editor noise"
    if rel_file.suffix.lower() in BAD_SUFFIXES or rel_file.name.endswith((".pyc", ".pyo")):
        return "runtime or binary artifact"
    return None


def add_tree(
    plan: list[dict[str, Any]],
    skipped: list[dict[str, str]],
    filtered: list[dict[str, str]],
    src_root: Path,
    dest_root: Path,
    package_root: Path,
    local_only: dict[str, dict[str, str]] | None = None,
) -> None:
    if not src_root.exists():
        skipped.append({"path": rel(src_root, KB_ROOT), "reason": "source path missing"})
        return
    local_names = set(local_only or {})
    local_dirs = {v["dir"] for v in (local_only or {}).values()}
    local_skills = {v["skill"] for v in (local_only or {}).values()}
    for root, dirs, files in os.walk(src_root):
        root_path = Path(root)
        rel_dir = root_path.relative_to(src_root)
        kept_dirs: list[str] = []
        for d in dirs:
            child = rel_dir / d
            reason = skill_dir_skip(child, local_dirs) if src_root == SKILLS_ROOT else ("python cache" if d == "__pycache__" else None)
            if reason:
                skipped.append({"path": posix(child), "reason": reason})
            else:
                kept_dirs.append(d)
        dirs[:] = kept_dirs
        for name in files:
            rel_file = rel_dir / name
            reason = file_skip(rel_file)
            if reason:
                skipped.append({"path": posix(rel_file), "reason": reason})
                continue
            src = root_path / name
            dest = dest_root / rel_file
            rel_posix = posix(rel_file)
            if src_root == SKILLS_ROOT and rel_posix == "registry.yaml":
                text, names = filter_named_blocks(read_text(src), local_names)
                filtered.extend({"kind": "registry", "item": n} for n in names)
                add_file(plan, src, dest, package_root, text=text, transform="filter-local-only-registry")
            elif src_root == SKILLS_ROOT and rel_posix == "_registry/skills.catalog.yaml":
                text, names = filter_named_blocks(read_text(src), local_names)
                filtered.extend({"kind": "skills.catalog", "item": n} for n in names)
                add_file(plan, src, dest, package_root, text=text, transform="filter-local-only-catalog")
            elif src_root == SKILLS_ROOT and rel_posix == "_registry/scripts.catalog.yaml":
                text, paths = filter_script_blocks(read_text(src), local_dirs | LOCAL_RETIREMENT_FILES)
                filtered.extend({"kind": "scripts.catalog", "item": p} for p in paths)
                add_file(plan, src, dest, package_root, text=text, transform="filter-local-only-script-catalog")
            elif src_root == SKILLS_ROOT and rel_posix == "_registry/skill-routing-fixtures.jsonl":
                text, queries = filter_fixture_lines(read_text(src), local_skills)
                filtered.extend({"kind": "routing.fixture", "item": q} for q in queries)
                add_file(plan, src, dest, package_root, text=text, transform="filter-local-only-fixtures")
            else:
                add_file(plan, src, dest, package_root)


def build_plan(
    package_root: Path,
    local_only: dict[str, dict[str, str]],
    *,
    include_runtime: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    plan: list[dict[str, Any]] = []
    deletes: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    filtered: list[dict[str, str]] = []

    add_file(plan, KB_ROOT / "PROJECT_LAYOUT.yaml", package_root / "PROJECT_LAYOUT.yaml", package_root)
    for item in ["README.md", "CHANGELOG.md", "PROJECT_LAYOUT.yaml", "AGENTS.md", "CLAUDE.md", "GEMINI.md", ".clinerules", ".cursorrules", ".gitignore"]:
        add_file(plan, KB_ROOT / item, package_root / "Project-template" / item, package_root)
    for item in [".cursor", ".roo", ".github"]:
        add_tree(plan, skipped, filtered, KB_ROOT / item, package_root / "Project-template" / item, package_root)
    add_tree(
        plan,
        skipped,
        filtered,
        resolve_path("helpers.codex", start=KB_ROOT),
        package_root / "Project-template" / ".codex" / "helpers",
        package_root,
    )
    add_file(
        plan,
        KB_ROOT / ".codex" / "config.toml",
        package_root / "Project-template" / ".codex" / "config.toml",
        package_root,
    )
    skipped.append({"path": "tmp/downloads/", "reason": "下载和重下 staging 是本机运行数据"})
    skipped.append({"path": "tmp/", "reason": "临时任务、日志、状态、渲染和 staging 不进入整合包"})

    add_file(plan, KB_ROOT / "SCHEMA.md", package_root / "Wiki-template" / "SCHEMA.md", package_root)
    add_file(plan, KB_ROOT / "LLMWiki" / "index.md", package_root / "Wiki-template" / "index.md", package_root)
    add_file(plan, KB_ROOT / "LLMWiki" / "log.md", package_root / "Wiki-template" / "log.md", package_root)
    add_tree(plan, skipped, filtered, KB_ROOT / "LLMWiki" / ".obsidian", package_root / "Wiki-template" / ".obsidian", package_root)

    add_tree(plan, skipped, filtered, SKILLS_ROOT, package_root / "Skills", package_root, local_only)

    for item in ["README.md", "CHANGELOG.md", "SCHEMA_STUDENT_DATA.md"]:
        add_file(plan, KB_ROOT / "StudentDataSQL" / item, package_root / "StudentDataSQL" / item, package_root)
    for item in ["migrations", "scripts", "templates", "tests/fixtures_synthetic"]:
        add_tree(plan, skipped, filtered, KB_ROOT / "StudentDataSQL" / item, package_root / "StudentDataSQL" / item, package_root)
    add_file(
        plan,
        None,
        package_root / "StudentDataSQL" / "runtime" / "exam-reports" / "README.md",
        package_root,
        text=exam_reports_readme(),
        transform="generated-placeholder",
    )

    add_file(plan, KB_ROOT / "raw" / "README.md", package_root / "raw" / "README.md", package_root)
    add_file(
        plan,
        None,
        package_root / "source-library" / "README.md",
        package_root,
        text=source_library_readme(),
        transform="generated-placeholder",
    )

    for item in ["README.md", "CHANGELOG.md"]:
        add_file(plan, KB_ROOT / "BGE-M3" / item, package_root / "BGE-M3" / item, package_root)
    for item in ["config", "scripts", "tests"]:
        add_tree(plan, skipped, filtered, KB_ROOT / "BGE-M3" / item, package_root / "BGE-M3" / item, package_root)
    for item in ["rag_pipeline.py", "run_rag_safe.bat", "test_bge_m3.py", "test_retrieval.py"]:
        add_file(plan, KB_ROOT / "BGE-M3" / "scripts" / item, package_root / "RAG" / item, package_root)
    if (KB_ROOT / "requirements").exists():
        add_tree(plan, skipped, filtered, KB_ROOT / "requirements", package_root / "requirements", package_root)
    else:
        skipped.append({"path": "requirements/", "reason": "source requirements directory missing; keeping existing package files"})

    stale_dirs = list(STALE_PACKAGE_DIRS)
    stale_dirs.extend(f"Skills/{item['dir']}" for item in local_only.values())
    for item in stale_dirs:
        target = package_root / item
        if target.exists():
            ensure_inside(target, package_root)
            deletes.append({"target": str(target), "rel_target": rel(target, package_root), "reason": "remove forbidden or stale package runtime directory"})
    for item in STALE_PACKAGE_FILES:
        target = package_root / item
        if target.is_file():
            ensure_inside(target, package_root)
            deletes.append({"target": str(target), "rel_target": rel(target, package_root), "reason": "remove retired mission v2 portable file"})

    if package_root.exists():
        for root, dirs, files in os.walk(package_root):
            root_path = Path(root)
            rel_root = root_path.relative_to(package_root)
            kept_dirs: list[str] = []
            for d in dirs:
                child = root_path / d
                child_rel = posix(rel_root / d)
                if include_runtime and child_rel in RUNTIME_DEST_ROOTS:
                    continue
                if d == "__pycache__":
                    ensure_inside(child, package_root)
                    deletes.append({"target": str(child), "rel_target": rel(child, package_root), "reason": "remove python cache directory"})
                else:
                    kept_dirs.append(d)
            dirs[:] = kept_dirs
            for name in files:
                child = root_path / name
                if child.suffix.lower() in BAD_SUFFIXES or name.endswith((".pyc", ".pyo")):
                    ensure_inside(child, package_root)
                    deletes.append(
                        {
                            "target": str(child),
                            "rel_target": rel(child, package_root),
                            "reason": "remove forbidden runtime or binary artifact",
                        }
                    )

    skills_target = package_root / "Skills"
    if skills_target.exists():
        for root, dirs, _files in os.walk(skills_target):
            root_path = Path(root)
            rel_root = root_path.relative_to(skills_target)
            kept_dirs: list[str] = []
            for d in dirs:
                runtime_child = root_path / d
                rel_child = rel_root / d
                if include_runtime and runtime_path("Skills/" + posix(rel_child)):
                    continue
                if package_skill_runtime_dir(rel_child):
                    ensure_inside(runtime_child, package_root)
                    deletes.append({"target": str(runtime_child), "rel_target": rel(runtime_child, package_root), "reason": "remove skill runtime env/model/cache from package"})
                else:
                    kept_dirs.append(d)
            dirs[:] = kept_dirs

    for parent_rel, keep in [
        ("raw", {"README.md"}),
        ("source-library", {"README.md"}),
        ("StudentDataSQL/runtime/exam-reports", {"README.md"}),
    ]:
        parent = package_root / parent_rel
        if parent.exists():
            for child in parent.iterdir():
                if child.name in keep:
                    continue
                ensure_inside(child, package_root)
                deletes.append({"target": str(child), "rel_target": rel(child, package_root), "reason": "remove production data from package placeholder directory"})

    concepts = package_root / "Wiki-template" / "concepts"
    if concepts.exists():
        ensure_inside(concepts, package_root)
        deletes.append({"target": str(concepts), "rel_target": rel(concepts, package_root), "reason": "remove production Wiki concepts from template"})

    if include_runtime:
        deletes = [item for item in deletes if not runtime_path(item["rel_target"])]
    return plan, deletes, skipped, filtered


def apply_plan(plan: list[dict[str, Any]], deletes: list[dict[str, str]], package_root: Path) -> None:
    for item in deletes:
        target = Path(item["target"])
        ensure_inside(target, package_root)
        accessible_target = accessible_path(target)
        if accessible_target.exists():
            if accessible_target.is_dir():
                shutil.rmtree(accessible_target)
            else:
                accessible_target.unlink()
    for item in plan:
        if item["status"] == "unchanged":
            continue
        dest = package_root / item["dest"]
        ensure_inside(dest, package_root)
        if "_text" in item:
            write_text(dest, item["_text"])
        else:
            src = KB_ROOT / item["src"]
            accessible_dest = accessible_path(dest)
            accessible_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(accessible_path(src), accessible_dest)


def boundary_scan(package_root: Path, *, include_runtime: bool = False) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    if not package_root.exists():
        return {"ok": False, "issues": [{"path": str(package_root), "reason": "package root missing"}]}

    for item in STALE_PACKAGE_DIRS:
        if include_runtime and runtime_path(item):
            continue
        if (package_root / item).exists():
            issues.append({"path": item, "reason": "forbidden directory exists"})
    for item in STALE_PACKAGE_FILES:
        if (package_root / item).exists():
            issues.append({"path": item, "reason": "retired mission v2 file exists"})

    for root, dirs, files in os.walk(package_root):
        root_path = Path(root)
        rel_dir = root_path.relative_to(package_root)
        if "__pycache__" in dirs:
            issues.append({"path": posix(rel_dir / "__pycache__"), "reason": "python cache directory"})
        for d in dirs:
            child = rel_dir / d
            if include_runtime and posix(child) in RUNTIME_DEST_ROOTS:
                continue
            if posix(child).startswith("Skills/") and package_skill_runtime_dir(child.relative_to(Path("Skills"))) and not (include_runtime and runtime_path(posix(child))):
                issues.append({"path": posix(child), "reason": "skill runtime env/model/cache directory"})
        if include_runtime:
            dirs[:] = [d for d in dirs if posix(rel_dir / d) not in RUNTIME_DEST_ROOTS]
        for name in files:
            child = rel_dir / name
            child_posix = posix(child)
            suffix = Path(name).suffix.lower()
            if child_posix.startswith("raw/") and child_posix != "raw/README.md":
                issues.append({"path": child_posix, "reason": "production raw content"})
            if child_posix.startswith("source-library/") and child_posix != "source-library/README.md":
                issues.append({"path": child_posix, "reason": "production original content"})
            if child_posix.startswith("StudentDataSQL/runtime/exam-reports/") and child_posix != "StudentDataSQL/runtime/exam-reports/README.md":
                issues.append({"path": child_posix, "reason": "exam report body"})
            if child_posix.startswith("Wiki-template/concepts/"):
                issues.append({"path": child_posix, "reason": "production Wiki concepts"})
            if (suffix in BAD_SUFFIXES or name.endswith((".pyc", ".pyo"))) and not (include_runtime and runtime_path(child_posix)):
                issues.append({"path": child_posix, "reason": "runtime or binary artifact"})

    if include_runtime:
        for forbidden in ("BGE-M3/runtime/data", "BGE-M3/runtime/index", "BGE-M3/runtime/logs"):
            if (package_root / forbidden).exists():
                issues.append({"path": forbidden, "reason": "production RAG runtime excluded from portable package"})
        manifest_path = package_root / RUNTIME_MANIFEST_NAME
        runtime_roots_exist = any((package_root / item).exists() for item in RUNTIME_DEST_ROOTS)
        if runtime_roots_exist and not manifest_path.is_file():
            issues.append({"path": RUNTIME_MANIFEST_NAME, "reason": "portable runtime manifest missing"})
        elif manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                expected = list(manifest.get("files", []))
                step = max(1, len(expected) // 256)
                sample = expected[::step]
                if expected and expected[-1] not in sample:
                    sample.append(expected[-1])
                missing = 0
                size_drift = 0
                for item in sample:
                    target = accessible_path(package_root / item["path"])
                    if not target.is_file():
                        missing += 1
                    elif target.stat().st_size != item.get("bytes"):
                        size_drift += 1
                if missing:
                    issues.append({"path": RUNTIME_MANIFEST_NAME, "reason": f"runtime sentinel files missing: {missing}/{len(sample)}"})
                if size_drift:
                    issues.append({"path": RUNTIME_MANIFEST_NAME, "reason": f"runtime sentinel size drift: {size_drift}/{len(sample)}"})
            except Exception as exc:
                issues.append({"path": RUNTIME_MANIFEST_NAME, "reason": f"invalid runtime manifest: {exc}"})

    for item in [
        ("Skills/registry.yaml", "local-only skill leaked into registry"),
        ("Skills/_registry/skills.catalog.yaml", "local-only skill leaked into skill catalog"),
        ("Skills/_registry/scripts.catalog.yaml", "local-only skill leaked into script catalog"),
        ("Skills/_registry/skill-routing-fixtures.jsonl", "local-only skill leaked into routing fixtures"),
    ]:
        path = package_root / item[0]
        if path.exists() and "integration-package-update" in read_text(path):
            issues.append({"path": item[0], "reason": item[1]})
    return {"ok": not issues, "issues": issues}


def summarize(plan: list[dict[str, Any]], deletes: list[dict[str, str]], skipped: list[dict[str, str]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    bytes_by_status: dict[str, int] = {}
    for item in plan:
        status = item["status"]
        counts[status] = counts.get(status, 0) + 1
        bytes_by_status[status] = bytes_by_status.get(status, 0) + int(item["bytes"])
    return {"copy_counts": counts, "bytes_by_status": bytes_by_status, "delete_count": len(deletes), "skipped_count": len(skipped)}


def public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k != "_text"}


MODEL_RUNTIME_SCOPE_PREFIXES = (
    "Skills/_shared/model-tools/",
)

MODEL_RUNTIME_SCOPE_PATHS = {
    "Project-template/AGENTS.md",
    "Project-template/.gitignore",
    "Wiki-template/SCHEMA.md",
    "Skills/README.md",
    "Skills/registry.yaml",
    "Skills/_registry/scripts.catalog.yaml",
    "Skills/_registry/skills.catalog.yaml",
    "Skills/_registry/skill-routing-fixtures.jsonl",
    "Skills/_shared/scripts/libreoffice_runner.py",
    "Skills/_shared/scripts/skill_retriever.py",
    "Skills/_shared/scripts/install_skill_dependencies.py",
    "Skills/_shared/scripts/validate_skill_library.py",
    "Skills/import/bemarkdown/SKILL.md",
    "Skills/import/bemarkdown/manifest.yaml",
    "Skills/import/bemarkdown/scripts/batch_convert_all.py",
    "Skills/import/bemarkdown/scripts/formula_ensemble.py",
    "Skills/import/bemarkdown/scripts/run_bemarkdown_with_shared_runtime.bat",
    "Skills/import/bemarkdown/tests/test_formula_ensemble.py",
    "Skills/import/video-transcript-import/SKILL.md",
    "Skills/import/video-transcript-import/manifest.yaml",
    "Skills/import/video-transcript-import/scripts/run_transcribe_with_shared_runtime.bat",
    "Skills/import/video-transcript-import/scripts/transcribe_video_batch.py",
    "Skills/import/chinese-handwriting-formula-transcriber/SKILL.md",
    "Skills/import/chinese-handwriting-formula-transcriber/card.md",
    "Skills/import/chinese-handwriting-formula-transcriber/manifest.yaml",
    "Skills/import/chinese-handwriting-formula-transcriber/agents/openai.yaml",
    "Skills/import/chinese-handwriting-formula-transcriber/references/architecture.md",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/_common.ps1",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/_auto_prepare_inputs.py",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/_auto_ranker.py",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/_engine_formula_auto.py",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/_engine_formula_regions.py",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/_engine_formula_transcribe.py",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/_engine_paddle_auto.py",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/_make_smoke_inputs.py",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/_runtime_paths.py",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/_texteller_runtime.py",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/_transcribe_auto.py",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/_transcribe_image.py",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/_transcribe_regions.py",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/_warm_formula.py",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/_warm_paddle.py",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/doctor.ps1",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/download_models.ps1",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/setup_env.ps1",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/transcribe_auto.ps1",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/transcribe_image.ps1",
    "Skills/import/chinese-handwriting-formula-transcriber/scripts/transcribe_regions.ps1",
    "Skills/learn/exercise-solution-curation/manifest.yaml",
    "Skills/learn/exercise-solution-curation/scripts/curation_evidence.py",
    "Skills/learn/exercise-solution-curation/scripts/manage_curation_campaign.py",
    "Skills/learn/exercise-solution-curation/scripts/materialize_docx_evidence.py",
    "Skills/learn/physics-diagram-toolkit/manifest.yaml",
    "Skills/learn/physics-knowledge-graph/manifest.yaml",
    "Skills/learn/physics-question-tutoring/manifest.yaml",
    "Skills/taxonomy/exercise-knowledge-tags/manifest.yaml",
    "Skills/learn/_shared/references/model-profiles.json",
    "Skills/learn/_shared/scripts/validate_role_d_response.py",
    "Skills/learn/_shared/tests/fixtures/role_d_strong_eval_cases.json",
}


def filter_scope(
    plan: list[dict[str, Any]],
    deletes: list[dict[str, str]],
    scope: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if scope == "all":
        return plan, deletes
    if scope != "model-tools":
        raise ValueError(f"unsupported sync scope: {scope}")

    def included(path: str) -> bool:
        return path in MODEL_RUNTIME_SCOPE_PATHS or any(
            path == prefix.rstrip("/") or path.startswith(prefix)
            for prefix in MODEL_RUNTIME_SCOPE_PREFIXES
        )

    scoped_plan = [item for item in plan if included(str(item.get("dest", "")))]
    scoped_deletes = [item for item in deletes if included(str(item.get("rel_target", "")))]
    return scoped_plan, scoped_deletes


def write_report(payload: dict[str, Any]) -> Path:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = REPORT_ROOT / f"sync_report_{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--user-authorized", action="store_true")
    parser.add_argument("--scope", choices=["all", "model-tools"], default="all")
    parser.add_argument("--include-runtime", action="store_true", help="include allowlisted portable Skills and BGE-M3 runtimes")
    parser.add_argument("--refresh-runtime-inventory", action="store_true", help="rehash and rebuild the portable runtime source cache")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    package_root = package_root_from_args(args.package_root)
    if args.include_runtime and args.scope != "all":
        payload = {"ok": False, "mode": "blocked", "package_root": str(package_root), "error": "--include-runtime requires --scope all"}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload["error"])
        return 2
    if args.apply and not args.user_authorized:
        payload = {"ok": False, "mode": "blocked", "package_root": str(package_root), "error": "--apply requires --user-authorized"}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload["error"])
        return 2

    try:
        local_only = discover_local_only()
        runtime_payload: dict[str, Any] | None = None
        runtime_entries: list[dict[str, Any]] = []
        runtime_source_manifest: dict[str, Any] | None = None
        if args.include_runtime:
            registry = json.loads((SKILLS_ROOT / "_shared" / "model-tools" / "registry.yaml").read_text(encoding="utf-8"))
            gate = runtime_license_gate(registry)
            if not gate["ok"]:
                raise RuntimeError(f"portable runtime license gate failed: {gate['issues']}")
            runtime_root = resolve_path("skills.model-tools.runtime", start=KB_ROOT)
            cache_path = resolve_path("skills.ops.runtime", start=KB_ROOT) / "state" / "portable-runtime-source-manifest.json"
            validation_lock = json.loads((runtime_root / "validation-lock.json").read_text(encoding="utf-8"))
            license_inventory = {"components": gate["components"], "environments": gate["environments"]}
            runtime_source_manifest = None if args.refresh_runtime_inventory else load_cached_source_manifest(
                cache_path, registry, license_inventory, validation_lock
            )
            cache_used = runtime_source_manifest is not None
            if runtime_source_manifest is None:
                runtime_entries = portable_runtime_entries(registry, runtime_root, resolve_path("rag.runtime", start=KB_ROOT))
                runtime_source_manifest = build_runtime_source_manifest(
                    runtime_entries,
                    license_inventory,
                    cache_path=cache_path,
                    validation_lock=validation_lock,
                    selection_sha256=runtime_selection_sha256(registry),
                )
            runtime_payload = {
                "license_gate": gate,
                "summary": runtime_sync_summary(runtime_source_manifest, package_root),
                "source_file_count": runtime_source_manifest["file_count"],
                "source_bytes": runtime_source_manifest["source_bytes"],
                "source_cache_used": cache_used,
            }
        plan, deletes, skipped, filtered = build_plan(package_root, local_only, include_runtime=args.include_runtime)
        plan, deletes = filter_scope(plan, deletes, args.scope)
        if args.apply:
            package_root.mkdir(parents=True, exist_ok=True)
            apply_plan(plan, deletes, package_root)
            if args.include_runtime and runtime_source_manifest is not None:
                summary = runtime_payload["summary"]
                if summary["create"] == 0 and summary["update"] == 0 and summary["delete"] == 0:
                    runtime_payload["apply"] = refresh_runtime_manifest(runtime_source_manifest, package_root)
                else:
                    if not runtime_entries:
                        runtime_entries = portable_runtime_entries(
                            registry,
                            resolve_path("skills.model-tools.runtime", start=KB_ROOT),
                            resolve_path("rag.runtime", start=KB_ROOT),
                        )
                    runtime_payload["apply"] = apply_runtime_entries(runtime_entries, runtime_source_manifest, package_root)
        scan = boundary_scan(package_root, include_runtime=args.include_runtime)
        payload = {
            "ok": bool(scan["ok"]),
            "mode": "applied" if args.apply else "dry-run",
            "kb_root": str(KB_ROOT),
            "package_root": str(package_root),
            "scope": args.scope,
            "include_runtime": args.include_runtime,
            "local_only_filtered": local_only,
            "filtered_outputs": filtered,
            "summary": summarize(plan, deletes, skipped),
            "copy_plan": [public_item(item) for item in plan if item["status"] != "unchanged"],
            "delete_plan": [{"target": item["rel_target"], "reason": item["reason"]} for item in deletes],
            "skipped": skipped,
            "boundary_scan": scan,
        }
        if runtime_payload is not None:
            payload["portable_runtime"] = runtime_payload
        report = write_report(payload)
        payload["report_path"] = str(report)
    except Exception as exc:
        payload = {"ok": False, "mode": "error", "package_root": str(package_root), "error": str(exc)}
        try:
            payload["report_path"] = str(write_report(payload))
        except Exception:
            pass
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else str(exc))
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else f"{payload['mode']} ok={payload['ok']} report={payload['report_path']}")
    return 0 if (not args.apply or payload["ok"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
