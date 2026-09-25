#!/usr/bin/env python3
"""Hash-chain, parse, render, and review legacy MathType formulas in DOCX.

The durable state lives under ``tmp/state/bemarkdown/<task-id>``.  Production
Markdown is never written by this module.  A completion record is emitted only
when every MTEF hash and every independent WMF has a hash-bound final decision.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import io
import json
import os
import posixpath
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
import zipfile
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Sequence

from lxml import etree


SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_ROOT = SCRIPT_DIR.parents[2]
MODEL_RUNTIME_PATH = SKILLS_ROOT / "_shared" / "model-tools" / "scripts" / "model_runtime.py"
PROJECT_PATHS_PATH = SKILLS_ROOT / "_shared" / "scripts" / "project_paths.py"
SHARED_SCRIPTS = SKILLS_ROOT / "_shared" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SHARED_SCRIPTS))

from formula_candidate_utils import resolve_formula_consensus  # noqa: E402
from formula_ensemble import run_formula_ensemble  # noqa: E402
from mtef_mathml import MappingResult, map_mtef_xml  # noqa: E402
from libreoffice_runner import render_vectors_full_frame  # noqa: E402


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODEL_RUNTIME = _load_module("llmwiki_model_runtime_mtef", MODEL_RUNTIME_PATH)
PROJECT_PATHS = _load_module("llmwiki_project_paths_mtef", PROJECT_PATHS_PATH)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
V = "urn:schemas-microsoft-com:vml"
O = "urn:schemas-microsoft-com:office:office"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

SCHEMA_VERSION = 3
CONTRACT_VERSION = "mtef-kb-latex-3"
FINAL_STATUSES = {"machine_final", "reviewed_final"}
DEFAULT_STAGES = ("scan", "parse", "render", "models", "export")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_text(path: Path, text: str) -> None:
    atomic_bytes(path, text.encode("utf-8"))


def atomic_json(path: Path, payload: Any) -> None:
    atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def utc_now() -> str:
    import datetime as _datetime

    return _datetime.datetime.now(_datetime.timezone.utc).isoformat()


def clean_task_id(value: str) -> str:
    value = value.strip()
    if not value or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in value):
        raise ValueError("task-id may contain only ASCII letters, digits, '-' and '_'")
    return value


def _process_alive(pid: int) -> bool:
    if pid < 1:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        error_access_denied = 5
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if handle:
            exit_code = ctypes.c_ulong()
            try:
                return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == 259
            finally:
                kernel32.CloseHandle(handle)
        return ctypes.get_last_error() == error_access_denied
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (OSError, SystemError):
        return False


def _process_identity(pid: int) -> str | None:
    """Return a PID-reuse-safe process identity where the host supports it."""
    if pid < 1 or os.name != "nt":
        return None
    import ctypes

    class FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None
    creation = FileTime()
    exit_time = FileTime()
    kernel_time = FileTime()
    user_time = FileTime()
    try:
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        ticks = (int(creation.high) << 32) | int(creation.low)
        return f"windows-filetime:{ticks}"
    finally:
        kernel32.CloseHandle(handle)


def _legacy_lock_can_belong_to_process(started_at: Any, process_identity: str | None) -> bool:
    """Conservatively match a legacy Windows lock that lacks process identity."""
    if os.name != "nt" or not process_identity or not process_identity.startswith("windows-filetime:"):
        return True
    try:
        import datetime as _datetime

        creation_ticks = int(process_identity.split(":", 1)[1])
        started = _datetime.datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=_datetime.timezone.utc)
        unix_seconds = started.timestamp()
        lock_ticks = int((unix_seconds + 11644473600) * 10_000_000)
    except (TypeError, ValueError, OverflowError):
        return True
    # The owning Python process must have started no later than the lock.  A
    # small tolerance avoids rejecting a valid lock because of timestamp
    # serialization precision.
    return creation_ticks <= lock_ticks + 20_000_000


def acquire_task_lock(root: Path) -> tuple[Path, str]:
    lock = root / ".audit.lock"
    token = uuid.uuid4().hex
    payload = {
        "pid": os.getpid(),
        "process_identity": _process_identity(os.getpid()),
        "token": token,
        "started_at": utc_now(),
    }
    for _ in range(2):
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                current = json.loads(lock.read_text(encoding="utf-8"))
            except Exception:
                current = {}
            current_pid = int(current.get("pid") or 0)
            live_identity = _process_identity(current_pid)
            stored_identity = current.get("process_identity")
            same_live_process = _process_alive(current_pid) and (
                (bool(stored_identity) and stored_identity == live_identity)
                or (
                    not stored_identity
                    and _legacy_lock_can_belong_to_process(current.get("started_at"), live_identity)
                )
            )
            if same_live_process:
                raise RuntimeError(f"audit task is already running: {lock} pid={current.get('pid')}")
            lock.unlink(missing_ok=True)
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        return lock, token
    raise RuntimeError(f"cannot acquire audit task lock: {lock}")


def release_task_lock(lock: Path, token: str) -> None:
    try:
        current = json.loads(lock.read_text(encoding="utf-8"))
    except Exception:
        return
    if current.get("token") == token:
        lock.unlink(missing_ok=True)


def _component_fingerprint(component_ids: Sequence[str]) -> dict[str, Any]:
    registry = MODEL_RUNTIME.load_registry()
    inventory_path = MODEL_RUNTIME.runtime_root() / "inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8")) if inventory_path.is_file() else {"components": {}}
    payload: dict[str, Any] = {}
    for component_id in component_ids:
        component = registry.get("components", {}).get(component_id, {})
        payload[component_id] = {
            "version": component.get("version"),
            "artifact_sha256": component.get("artifact_sha256"),
            "inventory": inventory.get("components", {}).get(component_id, {}).get("files", []),
        }
    return payload


def parser_runtime_fingerprint() -> str:
    payload: dict[str, Any] = {
        "components": _component_fingerprint(
            ("temurin-jre-21.0.11+10", "jruby-complete-9.3.8.0", "transpect-mathtype-0.0.7.5")
        )
    }
    for source in (SCRIPT_DIR / "mtef_worker.rb",):
        payload[str(source.name)] = sha256_path(source)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(canonical)


def runtime_fingerprint() -> str:
    payload: dict[str, Any] = {
        "contract": CONTRACT_VERSION,
        "parser_runtime_fingerprint": parser_runtime_fingerprint(),
        "components": _component_fingerprint(
            (
                "apache-batik-1.19", "wmf-native-adapter-1.0.0", "olefile-0.47",
                "pp-formulanet-plus-l", "texteller-1.0.2",
            )
        ),
    }
    for source in (
        Path(__file__),
        SCRIPT_DIR / "mtef_mathml.py",
        SCRIPT_DIR / "formula_candidate_utils.py",
        SCRIPT_DIR / "formula_ensemble.py",
        SHARED_SCRIPTS / "libreoffice_runner.py",
        SHARED_SCRIPTS / "render_wmf_native.ps1",
    ):
        payload[str(source.name)] = sha256_path(source)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(canonical)


class AuditStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=60)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                document_key TEXT PRIMARY KEY,
                source_path TEXT NOT NULL UNIQUE,
                docx_sha256 TEXT NOT NULL,
                source_bytes INTEGER NOT NULL,
                source_mtime_ns INTEGER NOT NULL,
                status TEXT NOT NULL,
                issue_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS formula_occurrences (
                occurrence_id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_key TEXT NOT NULL REFERENCES documents(document_key) ON DELETE CASCADE,
                object_index INTEGER NOT NULL,
                paragraph_index INTEGER,
                source_kind TEXT NOT NULL,
                prog_id TEXT,
                ole_rel_id TEXT,
                ole_target TEXT,
                preview_rel_id TEXT,
                preview_target TEXT,
                ole_sha256 TEXT,
                native_sha256 TEXT,
                mtef_sha256 TEXT,
                wmf_sha256 TEXT,
                mtef_version INTEGER,
                status TEXT NOT NULL,
                issue_json TEXT NOT NULL DEFAULT '[]',
                UNIQUE(document_key, object_index, source_kind)
            );
            CREATE INDEX IF NOT EXISTS idx_occurrence_mtef ON formula_occurrences(mtef_sha256);
            CREATE INDEX IF NOT EXISTS idx_occurrence_wmf ON formula_occurrences(wmf_sha256);
            CREATE TABLE IF NOT EXISTS mtef_results (
                mtef_sha256 TEXT PRIMARY KEY,
                representative_ole_path TEXT NOT NULL,
                mtef_version INTEGER NOT NULL,
                parser_fingerprint TEXT NOT NULL DEFAULT '',
                runtime_fingerprint TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                parser_status TEXT NOT NULL,
                parser_xml TEXT,
                parser_segment_count INTEGER NOT NULL DEFAULT 1,
                mathml TEXT,
                structure_latex TEXT,
                final_latex TEXT,
                final_kind TEXT,
                final_status TEXT NOT NULL,
                risk_json TEXT NOT NULL DEFAULT '[]',
                issue_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS previews (
                wmf_sha256 TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                render_status TEXT NOT NULL,
                png_path TEXT,
                png_sha256 TEXT,
                render_json TEXT,
                final_kind TEXT,
                final_value TEXT,
                final_status TEXT NOT NULL DEFAULT 'needs_vlm',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS model_evidence (
                mtef_sha256 TEXT,
                wmf_sha256 TEXT NOT NULL,
                engine TEXT NOT NULL,
                png_sha256 TEXT NOT NULL DEFAULT '',
                runtime_fingerprint TEXT NOT NULL DEFAULT '',
                candidate TEXT,
                normalized TEXT,
                confidence REAL,
                status TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(mtef_sha256, wmf_sha256, engine)
            );
            CREATE TABLE IF NOT EXISTS reviews (
                review_key TEXT PRIMARY KEY,
                mtef_sha256 TEXT,
                wmf_sha256 TEXT NOT NULL,
                final_kind TEXT NOT NULL,
                final_value TEXT NOT NULL,
                visual_evidence TEXT NOT NULL,
                confidence REAL NOT NULL,
                runtime_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                reviewed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                stage TEXT NOT NULL,
                item_key TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL
            );
            """
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(mtef_results)")}
        if "parser_fingerprint" not in columns:
            self.connection.execute("ALTER TABLE mtef_results ADD COLUMN parser_fingerprint TEXT NOT NULL DEFAULT ''")
        if "parser_segment_count" not in columns:
            self.connection.execute("ALTER TABLE mtef_results ADD COLUMN parser_segment_count INTEGER NOT NULL DEFAULT 1")
        if "final_kind" not in columns:
            self.connection.execute("ALTER TABLE mtef_results ADD COLUMN final_kind TEXT")
        evidence_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(model_evidence)")}
        if "png_sha256" not in evidence_columns:
            self.connection.execute("ALTER TABLE model_evidence ADD COLUMN png_sha256 TEXT NOT NULL DEFAULT ''")
        if "runtime_fingerprint" not in evidence_columns:
            self.connection.execute("ALTER TABLE model_evidence ADD COLUMN runtime_fingerprint TEXT NOT NULL DEFAULT ''")
        self.set_meta("schema_version", str(SCHEMA_VERSION))
        self.connection.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def set_meta(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def attempt(self, stage: str, item_key: str, status: str, error: str | None, started: float) -> None:
        self.connection.execute(
            "INSERT INTO attempts(stage,item_key,status,error,started_at,finished_at) VALUES(?,?,?,?,?,?)",
            (stage, item_key, status, error, str(started), str(time.time())),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()


@dataclass
class AuditPaths:
    root: Path
    db: Path
    ole: Path
    wmf: Path
    render: Path
    batches: Path
    reports: Path

    @classmethod
    def for_task(cls, task_id: str) -> "AuditPaths":
        tmp = PROJECT_PATHS.resolve_path("workspace.tmp", start=SCRIPT_DIR)
        root = tmp / "state" / "bemarkdown" / clean_task_id(task_id)
        return cls(
            root=root,
            db=root / "formula_audit.sqlite3",
            ole=root / "artifacts" / "ole",
            wmf=root / "artifacts" / "wmf",
            render=root / "renders",
            batches=root / "batches",
            reports=root / "reports",
        )


def _olefile_module() -> Any:
    component = MODEL_RUNTIME.resolve_component("olefile-0.47")
    entry = Path(str(component["resolved_path"]))
    package_root = entry.parents[1] if entry.name == "olefile.py" else entry
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))
    return importlib.import_module("olefile")


def extract_equation_native(ole_payload: bytes) -> tuple[bytes, bytes, int]:
    olefile = _olefile_module()
    try:
        container = olefile.OleFileIO(io.BytesIO(ole_payload))
    except Exception as exc:
        raise ValueError(f"damaged OLE container: {exc}") from exc
    try:
        if not container.exists("Equation Native"):
            raise ValueError("OLE container has no Equation Native stream")
        native = container.openstream("Equation Native").read()
    finally:
        container.close()
    if len(native) < 29:
        raise ValueError("Equation Native stream is shorter than the 28-byte header")
    header_size = int.from_bytes(native[:4], "little")
    if header_size != 28:
        raise ValueError(f"unexpected Equation Native header size: {header_size}")
    mtef = native[header_size:]
    version = int(mtef[0])
    if version not in {3, 5}:
        raise ValueError(f"unsupported MTEF version: {version}")
    return native, mtef, version


def _normalize_target(base_part: str, target: str) -> str:
    if target.startswith("/"):
        normalized = target.lstrip("/")
    else:
        normalized = posixpath.normpath(posixpath.join(posixpath.dirname(base_part), target))
    if normalized.startswith("../") or normalized == "..":
        raise ValueError(f"relationship target escapes DOCX package: {target}")
    return normalized


def _relationships(archive: zipfile.ZipFile, source_part: str = "word/document.xml") -> dict[str, dict[str, str]]:
    rel_part = f"{posixpath.dirname(source_part)}/_rels/{posixpath.basename(source_part)}.rels"
    root = etree.fromstring(archive.read(rel_part))
    result: dict[str, dict[str, str]] = {}
    for relation in root:
        rel_id = str(relation.get("Id") or "")
        target = str(relation.get("Target") or "")
        mode = str(relation.get("TargetMode") or "Internal")
        result[rel_id] = {
            "target": _normalize_target(source_part, target) if mode != "External" else target,
            "type": str(relation.get("Type") or ""),
            "mode": mode,
        }
    return result


def _write_artifact(directory: Path, digest: str, suffix: str, payload: bytes) -> Path:
    target = directory / f"{digest}{suffix}"
    if target.is_file():
        if sha256_path(target) != digest:
            raise ValueError(f"artifact hash drift: {target}")
    else:
        atomic_bytes(target, payload)
    return target


def scan_docx(path: Path, store: AuditStore, paths: AuditPaths, *, rebuild: bool = False) -> dict[str, Any]:
    started = time.time()
    source = path.resolve()
    stat = source.stat()
    docx_sha = sha256_path(source)
    document_key = sha256_bytes((str(source).casefold() + "\0" + docx_sha).encode("utf-8"))
    existing = store.connection.execute(
        "SELECT * FROM documents WHERE source_path=?", (str(source),)
    ).fetchone()
    if (
        existing is not None
        and not rebuild
        and existing["docx_sha256"] == docx_sha
        and int(existing["source_mtime_ns"]) == stat.st_mtime_ns
        and existing["status"] == "scanned"
    ):
        return {"status": "cached", "document_key": existing["document_key"], "path": str(source)}

    occurrences: list[dict[str, Any]] = []
    issues: list[str] = []
    paired_preview_targets: set[str] = set()
    with zipfile.ZipFile(source, "r") as archive:
        document_xml = archive.read("word/document.xml")
        document = etree.fromstring(document_xml)
        relationships = _relationships(archive)
        object_index = 0
        for paragraph_index, paragraph in enumerate(document.iter(f"{{{W}}}p"), 1):
            for word_object in paragraph.iter(f"{{{W}}}object"):
                object_index += 1
                ole_node = word_object.find(f".//{{{O}}}OLEObject")
                image_node = word_object.find(f".//{{{V}}}imagedata")
                ole_rel = str(ole_node.get(f"{{{R}}}id") or "") if ole_node is not None else ""
                image_rel = str(image_node.get(f"{{{R}}}id") or "") if image_node is not None else ""
                prog_id = str(ole_node.get("ProgID") or "") if ole_node is not None else ""
                ole_info = relationships.get(ole_rel, {})
                image_info = relationships.get(image_rel, {})
                row: dict[str, Any] = {
                    "document_key": document_key,
                    "object_index": object_index,
                    "paragraph_index": paragraph_index,
                    "source_kind": "equation_ole" if ole_rel else "independent_wmf",
                    "prog_id": prog_id,
                    "ole_rel_id": ole_rel or None,
                    "ole_target": ole_info.get("target"),
                    "preview_rel_id": image_rel or None,
                    "preview_target": image_info.get("target"),
                    "ole_sha256": None,
                    "native_sha256": None,
                    "mtef_sha256": None,
                    "wmf_sha256": None,
                    "mtef_version": None,
                    "status": "blocked",
                    "issue_json": "[]",
                }
                row_issues: list[str] = []
                if image_info.get("mode") == "External" or ole_info.get("mode") == "External":
                    row_issues.append("external_relationship_not_allowed")
                if image_info.get("target"):
                    preview_target = str(image_info["target"])
                    if PurePosixPath(preview_target).suffix.lower() == ".wmf":
                        try:
                            preview = archive.read(preview_target)
                            wmf_sha = sha256_bytes(preview)
                            _write_artifact(paths.wmf, wmf_sha, ".wmf", preview)
                            row["wmf_sha256"] = wmf_sha
                            paired_preview_targets.add(preview_target)
                        except KeyError:
                            row_issues.append("preview_relationship_target_missing")
                    else:
                        row_issues.append("equation_preview_is_not_wmf")
                else:
                    row_issues.append("equation_preview_relationship_missing")
                if ole_info.get("target"):
                    try:
                        ole_payload = archive.read(str(ole_info["target"]))
                        ole_sha = sha256_bytes(ole_payload)
                        ole_path = _write_artifact(paths.ole, ole_sha, ".bin", ole_payload)
                        native, mtef, version = extract_equation_native(ole_payload)
                        row.update(
                            {
                                "ole_sha256": ole_sha,
                                "native_sha256": sha256_bytes(native),
                                "mtef_sha256": sha256_bytes(mtef),
                                "mtef_version": version,
                                "status": "structure_pending" if row.get("wmf_sha256") else "blocked",
                            }
                        )
                        row["representative_ole_path"] = str(ole_path)
                    except KeyError:
                        row_issues.append("ole_relationship_target_missing")
                    except ValueError as exc:
                        row_issues.append(str(exc))
                elif ole_rel:
                    row_issues.append("ole_relationship_missing")
                elif row.get("wmf_sha256"):
                    row["status"] = "needs_vlm"
                row["issue_json"] = json.dumps(sorted(set(row_issues)), ensure_ascii=False)
                occurrences.append(row)

        # Include WMF relationships that do not belong to an OLE object.  They
        # must be reviewed as latex, text, or image and never auto-finalized.
        known_independent = {str(row.get("preview_target")) for row in occurrences if row["source_kind"] == "independent_wmf"}
        for rel_id, relation in relationships.items():
            target = str(relation.get("target") or "")
            if relation.get("mode") == "External" or PurePosixPath(target).suffix.lower() != ".wmf":
                continue
            if target in paired_preview_targets or target in known_independent:
                continue
            object_index += 1
            try:
                preview = archive.read(target)
            except KeyError:
                issues.append(f"independent WMF target missing: {target}")
                continue
            wmf_sha = sha256_bytes(preview)
            _write_artifact(paths.wmf, wmf_sha, ".wmf", preview)
            occurrences.append(
                {
                    "document_key": document_key,
                    "object_index": object_index,
                    "paragraph_index": None,
                    "source_kind": "independent_wmf",
                    "prog_id": None,
                    "ole_rel_id": None,
                    "ole_target": None,
                    "preview_rel_id": rel_id,
                    "preview_target": target,
                    "ole_sha256": None,
                    "native_sha256": None,
                    "mtef_sha256": None,
                    "wmf_sha256": wmf_sha,
                    "mtef_version": None,
                    "status": "needs_vlm",
                    "issue_json": "[]",
                }
            )

    with store.transaction() as connection:
        if existing is not None:
            connection.execute("DELETE FROM documents WHERE source_path=?", (str(source),))
        connection.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)",
            (
                document_key,
                str(source),
                docx_sha,
                stat.st_size,
                stat.st_mtime_ns,
                "scanned" if not issues else "blocked",
                json.dumps(issues, ensure_ascii=False),
                utc_now(),
            ),
        )
        for row in occurrences:
            columns = [
                "document_key", "object_index", "paragraph_index", "source_kind", "prog_id",
                "ole_rel_id", "ole_target", "preview_rel_id", "preview_target", "ole_sha256",
                "native_sha256", "mtef_sha256", "wmf_sha256", "mtef_version", "status", "issue_json",
            ]
            connection.execute(
                f"INSERT INTO formula_occurrences({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                tuple(row.get(column) for column in columns),
            )
            if row.get("mtef_sha256"):
                connection.execute(
                    """
                    INSERT INTO mtef_results(
                        mtef_sha256,representative_ole_path,mtef_version,runtime_fingerprint,contract_version,
                        parser_status,final_status,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(mtef_sha256) DO UPDATE SET
                        representative_ole_path=excluded.representative_ole_path,
                        mtef_version=excluded.mtef_version
                    """,
                    (
                        row["mtef_sha256"], row["representative_ole_path"], row["mtef_version"], "", CONTRACT_VERSION,
                        "pending", "structure_candidate", utc_now(),
                    ),
                )
            if row.get("wmf_sha256"):
                wmf_path = paths.wmf / f"{row['wmf_sha256']}.wmf"
                connection.execute(
                    """
                    INSERT INTO previews(wmf_sha256,source_path,render_status,final_status,updated_at)
                    VALUES(?,?,?,?,?)
                    ON CONFLICT(wmf_sha256) DO UPDATE SET source_path=excluded.source_path
                    """,
                    (row["wmf_sha256"], str(wmf_path), "pending", "needs_vlm", utc_now()),
                )
    store.attempt("scan", document_key, "ok", None, started)
    return {"status": "scanned", "document_key": document_key, "path": str(source), "occurrences": len(occurrences)}


def _jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    atomic_text(path, "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records))


def _transpect_library_args() -> list[str]:
    root = Path(MODEL_RUNTIME.resolve_component("transpect-mathtype-0.0.7.5")["resolved_path"])
    libraries = [
        root / "mathtype-0.0.7.5" / "lib",
        root / "bindata-2.3.5" / "lib",
        root / "nokogiri-1.7.0.1-java" / "lib",
        root / "ruby-ole-1.2.12.2" / "lib",
    ]
    missing = [path for path in libraries if not path.is_dir()]
    if missing:
        raise FileNotFoundError(f"Transpect parser libraries are missing: {missing}")
    return [value for path in libraries for value in ("-I", str(path))]


def _run_jruby_batch(task: Path, output: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    model_runtime = MODEL_RUNTIME_PATH
    command = [
        sys.executable,
        str(model_runtime),
        "exec",
        "--id",
        "jruby-complete-9.3.8.0",
        "--",
        *_transpect_library_args(),
        str(SCRIPT_DIR / "mtef_worker.rb"),
        "--input",
        str(task),
        "--output",
        str(output),
        "--max-batch",
        "512",
    ]
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        command,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def _chunks(values: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _invalidate_reviewed_empty_previews(
    connection: sqlite3.Connection,
    mtef_sha256: str,
    *,
    updated_at: str,
) -> int:
    """Restore blank-render evidence when its MTEF review becomes stale.

    Applying an ``empty`` review promotes the hash-bound blank previews from
    ``blocked`` to ``reviewed_empty``.  If the parser, mapping contract, or
    runtime fingerprint later changes, the MTEF conclusion is invalid again;
    the preview rows must return to their pre-review state as part of the same
    transaction.  The PNG path/hash and render JSON remain valid evidence and
    are deliberately left untouched.
    """
    cursor = connection.execute(
        """
        UPDATE previews
        SET render_status='blocked',final_kind=NULL,final_value=NULL,
            final_status='needs_vlm',updated_at=?
        WHERE render_status='reviewed_empty' AND final_kind='empty'
          AND final_status='reviewed_final'
          AND wmf_sha256 IN (
              SELECT wmf_sha256 FROM formula_occurrences
              WHERE mtef_sha256=? AND wmf_sha256 IS NOT NULL
          )
        """,
        (updated_at, mtef_sha256),
    )
    return int(cursor.rowcount)


def parse_pending_mtef(
    store: AuditStore,
    paths: AuditPaths,
    fingerprint: str,
    parser_fingerprint: str,
    *,
    rebuild: bool = False,
    timeout: int = 1800,
) -> dict[str, int]:
    rows = store.connection.execute("SELECT * FROM mtef_results ORDER BY mtef_sha256").fetchall()
    pending = [
        row
        for row in rows
        if rebuild
        or row["parser_fingerprint"] != parser_fingerprint
        or row["parser_status"] not in {"parsed", "blocked"}
    ]
    counts: Counter[str] = Counter()
    for batch_index, batch in enumerate(_chunks(pending, 512), 1):
        started = time.time()
        task_path = paths.batches / f"mtef-{batch_index:05d}.jsonl"
        output_path = paths.batches / f"mtef-{batch_index:05d}.result.jsonl"
        _jsonl(
            task_path,
            (
                {"id": row["mtef_sha256"], "mtef_sha256": row["mtef_sha256"], "ole_path": row["representative_ole_path"]}
                for row in batch
            ),
        )
        try:
            process = _run_jruby_batch(task_path, output_path, timeout)
        except subprocess.TimeoutExpired as exc:
            store.attempt("parse", f"batch-{batch_index}", "timeout", str(exc), started)
            counts["timeout"] += len(batch)
            continue
        if not output_path.is_file():
            store.attempt("parse", f"batch-{batch_index}", "failed", process.stderr[-2000:], started)
            counts["failed"] += len(batch)
            continue
        result_records: dict[str, dict[str, Any]] = {}
        for line in output_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                result_records[str(record.get("id"))] = record
        with store.transaction() as connection:
            for row in batch:
                digest = row["mtef_sha256"]
                updated_at = utc_now()
                counts["invalidated_empty_previews"] += _invalidate_reviewed_empty_previews(
                    connection,
                    digest,
                    updated_at=updated_at,
                )
                record = result_records.get(digest)
                if not record or not record.get("ok"):
                    issue = [str((record or {}).get("error") or "JRuby result missing")]
                    connection.execute(
                        """
                        UPDATE mtef_results SET parser_fingerprint=?,runtime_fingerprint=?,contract_version=?,parser_status='blocked',
                            parser_xml=NULL,mathml=NULL,structure_latex=NULL,final_latex=NULL,final_kind=NULL,final_status='blocked',
                            risk_json='[]',issue_json=?,updated_at=? WHERE mtef_sha256=?
                        """,
                        (parser_fingerprint, fingerprint, CONTRACT_VERSION, json.dumps(issue, ensure_ascii=False), updated_at, digest),
                    )
                    counts["blocked"] += 1
                    continue
                segment_count = int(record.get("segment_count") or 1)
                parser_xml = str((record.get("segments") or [{}])[0].get("xml") or record.get("xml") or "")
                if segment_count == 1:
                    mapping = map_mtef_xml(parser_xml)
                else:
                    mapping = MappingResult(
                        status="blocked",
                        unsupported=[f"multiple_mtef_segments:{segment_count}"],
                        version=int(record.get("version") or 0),
                    )
                final_status = "structure_candidate" if mapping.status == "structure_candidate" else "blocked"
                connection.execute(
                    """
                    UPDATE mtef_results SET parser_fingerprint=?,runtime_fingerprint=?,contract_version=?,parser_status='parsed',
                        parser_xml=?,parser_segment_count=?,mathml=?,structure_latex=?,final_latex=NULL,final_kind=NULL,final_status=?,risk_json=?,issue_json=?,updated_at=?
                    WHERE mtef_sha256=?
                    """,
                    (
                        parser_fingerprint,
                        fingerprint,
                        CONTRACT_VERSION,
                        parser_xml,
                        segment_count,
                        mapping.mathml,
                        mapping.latex,
                        final_status,
                        json.dumps(mapping.risk_flags, ensure_ascii=False),
                        json.dumps(mapping.unsupported, ensure_ascii=False),
                        updated_at,
                        digest,
                    ),
                )
                counts[final_status] += 1
        store.attempt("parse", f"batch-{batch_index}", "ok" if process.returncode == 0 else "partial", process.stderr[-2000:] or None, started)
    # Mapping-only contract updates reuse the hash-verified parser XML.  This
    # prevents a LaTeX mapping revision from needlessly restarting 16k JRuby
    # parses while still invalidating every final decision and review hash.
    remap = store.connection.execute(
        """
        SELECT * FROM mtef_results
        WHERE parser_status='parsed' AND parser_xml IS NOT NULL
          AND (runtime_fingerprint!=? OR contract_version!=?)
        """,
        (fingerprint, CONTRACT_VERSION),
    ).fetchall()
    if remap:
        with store.transaction() as connection:
            for row in remap:
                updated_at = utc_now()
                counts["invalidated_empty_previews"] += _invalidate_reviewed_empty_previews(
                    connection,
                    str(row["mtef_sha256"]),
                    updated_at=updated_at,
                )
                segment_count = int(row["parser_segment_count"] or 1)
                if segment_count == 1:
                    mapping = map_mtef_xml(str(row["parser_xml"]))
                else:
                    mapping = MappingResult(
                        status="blocked",
                        unsupported=[f"multiple_mtef_segments:{segment_count}"],
                        version=int(row["mtef_version"] or 0),
                    )
                final_status = "structure_candidate" if mapping.status == "structure_candidate" else "blocked"
                connection.execute(
                    """
                    UPDATE mtef_results SET runtime_fingerprint=?,contract_version=?,mathml=?,structure_latex=?,
                        final_latex=NULL,final_kind=NULL,final_status=?,risk_json=?,issue_json=?,updated_at=? WHERE mtef_sha256=?
                    """,
                    (
                        fingerprint, CONTRACT_VERSION, mapping.mathml, mapping.latex, final_status,
                        json.dumps(mapping.risk_flags, ensure_ascii=False),
                        json.dumps(mapping.unsupported, ensure_ascii=False), updated_at, row["mtef_sha256"],
                    ),
                )
                counts[f"remapped_{final_status}"] += 1
    counts["cached_parser"] = len(rows) - len(pending)
    return dict(counts)


def render_pending_previews(
    store: AuditStore,
    paths: AuditPaths,
    *,
    rebuild: bool = False,
    timeout: int = 300,
    chunk_size: int = 64,
) -> dict[str, int]:
    rows = store.connection.execute("SELECT * FROM previews ORDER BY wmf_sha256").fetchall()
    pending = [row for row in rows if rebuild or row["render_status"] != "rendered" or not row["png_path"] or not Path(row["png_path"]).is_file()]
    counts: Counter[str] = Counter()
    for batch in _chunks(pending, chunk_size):
        sources = [Path(row["source_path"]) for row in batch]
        started = time.time()
        results = render_vectors_full_frame(sources, paths.render, timeout=timeout, reuse_existing=not rebuild)
        with store.transaction() as connection:
            for row, source in zip(batch, sources):
                result = results.get(str(source.resolve()))
                if isinstance(result, dict):
                    issues = list(result.get("geometry_issues") or [])
                    status = "rendered" if not issues else "blocked"
                    connection.execute(
                        """
                        UPDATE previews SET render_status=?,png_path=?,png_sha256=?,render_json=?,updated_at=?
                        WHERE wmf_sha256=?
                        """,
                        (
                            status,
                            result.get("output_path"),
                            result.get("visual_sha256"),
                            json.dumps(result, ensure_ascii=False),
                            utc_now(),
                            row["wmf_sha256"],
                        ),
                    )
                    counts[status] += 1
                else:
                    connection.execute(
                        "UPDATE previews SET render_status='blocked',render_json=?,updated_at=? WHERE wmf_sha256=?",
                        (json.dumps({"error": repr(result)}, ensure_ascii=False), utc_now(), row["wmf_sha256"]),
                    )
                    counts["blocked"] += 1
        store.attempt("render", f"{batch[0]['wmf_sha256']}:{len(batch)}", "ok", None, started)
    counts["cached"] = len(rows) - len(pending)
    return dict(counts)


def run_model_evidence(
    store: AuditStore,
    paths: AuditPaths,
    *,
    rebuild: bool = False,
    batch_size: int = 256,
    timeout: int = 7200,
) -> dict[str, int]:
    del batch_size  # One persistent worker per engine; per-image JSONL is the checkpoint boundary.
    fingerprint_row = store.connection.execute("SELECT value FROM meta WHERE key='runtime_fingerprint'").fetchone()
    fingerprint = str(fingerprint_row[0]) if fingerprint_row else ""
    rows = store.connection.execute("SELECT * FROM previews WHERE render_status='rendered' ORDER BY wmf_sha256").fetchall()
    if not rebuild:
        completed = {
            row[0]
            for row in store.connection.execute(
                """
                SELECT e.wmf_sha256 FROM model_evidence e
                JOIN previews p ON p.wmf_sha256=e.wmf_sha256
                WHERE e.mtef_sha256='' AND e.runtime_fingerprint=? AND e.png_sha256=p.png_sha256
                GROUP BY e.wmf_sha256 HAVING COUNT(DISTINCT e.engine)>=2
                """,
                (fingerprint,),
            )
        }
        rows = [row for row in rows if row["wmf_sha256"] not in completed]
    counts: Counter[str] = Counter()
    if rows:
        images = [Path(row["png_path"]) for row in rows]
        geometry = {
            str(Path(row["png_path"]).resolve()): list((json.loads(row["render_json"] or "{}").get("geometry_issues") or []))
            for row in rows
        }
        started = time.time()
        try:
            payload = run_formula_ensemble(
                images,
                paths.batches / f"models-persistent-{fingerprint[:16]}",
                geometry_by_image=geometry,
                timeout=timeout,
            )
        except Exception as exc:
            store.attempt("models", f"persistent-{len(rows)}", "failed", repr(exc), started)
            counts["failed"] += len(rows)
            reconcile_consensus(store)
            return dict(counts)
        by_image = {str(Path(record["source_image"]).resolve()): record for record in payload.get("records", [])}
        with store.transaction() as connection:
            for row in rows:
                image_key = str(Path(row["png_path"]).resolve())
                record = by_image.get(image_key, {})
                for candidate in record.get("candidates") or []:
                    engine = str(candidate.get("engine") or "unknown")
                    connection.execute(
                        """
                        INSERT INTO model_evidence(
                            mtef_sha256,wmf_sha256,engine,png_sha256,runtime_fingerprint,
                            candidate,normalized,confidence,status,evidence_json,updated_at
                        ) VALUES('',?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(mtef_sha256,wmf_sha256,engine) DO UPDATE SET
                            png_sha256=excluded.png_sha256,runtime_fingerprint=excluded.runtime_fingerprint,
                            candidate=excluded.candidate,normalized=excluded.normalized,confidence=excluded.confidence,
                            status=excluded.status,evidence_json=excluded.evidence_json,updated_at=excluded.updated_at
                        """,
                        (
                            row["wmf_sha256"],
                            engine,
                            row["png_sha256"],
                            fingerprint,
                            candidate.get("text"),
                            candidate.get("normalized_latex"),
                            candidate.get("confidence"),
                            candidate.get("status", "candidate"),
                            json.dumps(candidate, ensure_ascii=False),
                            utc_now(),
                        ),
                    )
                counts[str((record.get("resolution") or {}).get("status") or "missing")] += 1
        store.attempt("models", f"persistent-{len(rows)}", "ok", None, started)
    counts["cached"] = int(store.connection.execute("SELECT COUNT(DISTINCT wmf_sha256) FROM model_evidence WHERE mtef_sha256='' AND runtime_fingerprint=?", (fingerprint,)).fetchone()[0])
    reconcile_consensus(store)
    return dict(counts)


def reconcile_consensus(store: AuditStore) -> None:
    fingerprint_row = store.connection.execute("SELECT value FROM meta WHERE key='runtime_fingerprint'").fetchone()
    fingerprint = str(fingerprint_row[0]) if fingerprint_row else ""
    rows = store.connection.execute("SELECT * FROM mtef_results").fetchall()
    with store.transaction() as connection:
        for row in rows:
            if row["parser_status"] != "parsed" or not row["structure_latex"]:
                continue
            preview_hashes = [
                item[0]
                for item in connection.execute(
                    "SELECT DISTINCT wmf_sha256 FROM formula_occurrences WHERE mtef_sha256=? AND wmf_sha256 IS NOT NULL",
                    (row["mtef_sha256"],),
                )
            ]
            if not preview_hashes:
                continue
            final_values: set[str] = set()
            unresolved = False
            for wmf_sha in preview_hashes:
                preview = connection.execute("SELECT * FROM previews WHERE wmf_sha256=?", (wmf_sha,)).fetchone()
                if preview is None or preview["render_status"] != "rendered":
                    unresolved = True
                    continue
                candidates = [
                    {"engine": "mtef", "text": row["structure_latex"], "confidence": 1.0, "risk_flags": json.loads(row["risk_json"] or "[]")}
                ]
                evidence = connection.execute(
                    """
                    SELECT * FROM model_evidence
                    WHERE mtef_sha256='' AND wmf_sha256=? AND runtime_fingerprint=? AND png_sha256=?
                      AND engine IN ('pp_formulanet','texteller')
                    """,
                    (wmf_sha, fingerprint, preview["png_sha256"]),
                ).fetchall()
                for item in evidence:
                    candidates.append(
                        {"engine": item["engine"], "text": item["candidate"], "confidence": item["confidence"]}
                    )
                resolution = resolve_formula_consensus(
                    candidates,
                    geometry_issues=list((json.loads(preview["render_json"] or "{}").get("geometry_issues") or [])),
                    required_engines=("mtef", "pp_formulanet", "texteller"),
                )
                for candidate in resolution.pop("candidates", []):
                    connection.execute(
                        """
                        INSERT INTO model_evidence(
                            mtef_sha256,wmf_sha256,engine,png_sha256,runtime_fingerprint,
                            candidate,normalized,confidence,status,evidence_json,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(mtef_sha256,wmf_sha256,engine) DO UPDATE SET
                            png_sha256=excluded.png_sha256,runtime_fingerprint=excluded.runtime_fingerprint,
                            candidate=excluded.candidate,normalized=excluded.normalized,confidence=excluded.confidence,
                            status=excluded.status,evidence_json=excluded.evidence_json,updated_at=excluded.updated_at
                        """,
                        (
                            row["mtef_sha256"], wmf_sha, candidate["engine"], preview["png_sha256"], fingerprint, candidate["text"],
                            candidate.get("normalized_latex"), candidate.get("confidence"), candidate["status"],
                            json.dumps(candidate, ensure_ascii=False), utc_now(),
                        ),
                    )
                if resolution["status"] == "machine_final" and resolution.get("final_latex"):
                    final_values.add(str(resolution["final_latex"]))
                else:
                    unresolved = True
            if not unresolved and len(final_values) == 1:
                final = next(iter(final_values))
                connection.execute(
                    "UPDATE mtef_results SET final_latex=?,final_kind='latex',final_status='machine_final',issue_json='[]',updated_at=? WHERE mtef_sha256=?",
                    (final, utc_now(), row["mtef_sha256"]),
                )
            elif row["final_status"] != "reviewed_final":
                issue = ["three_source_consensus_not_satisfied"]
                if len(final_values) > 1:
                    issue.append("preview_variants_disagree")
                connection.execute(
                    "UPDATE mtef_results SET final_latex=NULL,final_kind=NULL,final_status='needs_vlm',issue_json=?,updated_at=? WHERE mtef_sha256=?",
                    (json.dumps(issue, ensure_ascii=False), utc_now(), row["mtef_sha256"]),
                )


def export_reports(store: AuditStore, paths: AuditPaths, fingerprint: str) -> dict[str, Any]:
    occurrence_total = int(store.connection.execute("SELECT COUNT(*) FROM formula_occurrences").fetchone()[0])
    equation_total = int(store.connection.execute("SELECT COUNT(*) FROM formula_occurrences WHERE mtef_sha256 IS NOT NULL").fetchone()[0])
    independent_total = int(store.connection.execute("SELECT COUNT(*) FROM formula_occurrences WHERE source_kind='independent_wmf'").fetchone()[0])
    unique_mtef = int(store.connection.execute("SELECT COUNT(*) FROM mtef_results").fetchone()[0])
    unique_previews = int(store.connection.execute("SELECT COUNT(*) FROM previews").fetchone()[0])
    versions = {str(row[0]): int(row[1]) for row in store.connection.execute("SELECT mtef_version,COUNT(*) FROM formula_occurrences WHERE mtef_version IS NOT NULL GROUP BY mtef_version")}
    status_counts = {str(row[0]): int(row[1]) for row in store.connection.execute("SELECT final_status,COUNT(*) FROM mtef_results GROUP BY final_status")}
    preview_status = {str(row[0]): int(row[1]) for row in store.connection.execute("SELECT render_status,COUNT(*) FROM previews GROUP BY render_status")}
    unresolved_mtef = int(
        store.connection.execute(
            "SELECT COUNT(*) FROM mtef_results WHERE final_status NOT IN ('machine_final','reviewed_final')"
        ).fetchone()[0]
    )
    unresolved_independent = int(
        store.connection.execute(
            """
            SELECT COUNT(DISTINCT p.wmf_sha256) FROM previews p
            JOIN formula_occurrences o ON o.wmf_sha256=p.wmf_sha256
            WHERE o.source_kind='independent_wmf' AND p.final_status!='reviewed_final'
            """
        ).fetchone()[0]
    )
    review_tasks: list[dict[str, Any]] = []
    for row in store.connection.execute(
        "SELECT * FROM mtef_results WHERE final_status NOT IN ('machine_final','reviewed_final') ORDER BY mtef_sha256"
    ):
        previews = [
            dict(item)
            for item in store.connection.execute(
                """
                SELECT DISTINCT p.wmf_sha256,p.png_path,p.png_sha256,p.render_status
                FROM previews p JOIN formula_occurrences o ON o.wmf_sha256=p.wmf_sha256
                WHERE o.mtef_sha256=? ORDER BY p.wmf_sha256
                """,
                (row["mtef_sha256"],),
            )
        ]
        review_tasks.append(
            {
                "review_key": row["mtef_sha256"],
                "status": "unresolved",
                "mtef_sha256": row["mtef_sha256"],
                "mtef_version": row["mtef_version"],
                "runtime_fingerprint": fingerprint,
                "structure_latex": row["structure_latex"],
                "risk_flags": json.loads(row["risk_json"] or "[]"),
                "issues": json.loads(row["issue_json"] or "[]"),
                "previews": previews,
                "required_result": {
                    "status": "resolved|unresolved",
                    "final_kind": "latex|empty",
                    "final_latex": "source-evidenced KB-LaTeX; omit only when final_kind=empty",
                    "visual_evidence": "full-resolution evidence",
                    "confidence": "0.0-1.0",
                },
            }
        )
    for row in store.connection.execute(
        """
        SELECT DISTINCT p.* FROM previews p JOIN formula_occurrences o ON o.wmf_sha256=p.wmf_sha256
        WHERE o.source_kind='independent_wmf' AND p.final_status!='reviewed_final' ORDER BY p.wmf_sha256
        """
    ):
        review_tasks.append(
            {
                "review_key": f"wmf:{row['wmf_sha256']}",
                "status": "unresolved",
                "wmf_sha256": row["wmf_sha256"],
                "png_path": row["png_path"],
                "png_sha256": row["png_sha256"],
                "runtime_fingerprint": fingerprint,
                "required_result": {
                    "status": "resolved|unresolved",
                    "final_kind": "latex|text|image",
                    "final_value": "source-evidenced value or retained image reference",
                    "visual_evidence": "full-resolution evidence",
                    "confidence": "0.0-1.0",
                },
            }
        )
    paths.reports.mkdir(parents=True, exist_ok=True)
    _jsonl(paths.reports / "formula_review_tasks.jsonl", review_tasks)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "runtime_fingerprint": fingerprint,
        "generated_at": utc_now(),
        "counts": {
            "formula_occurrences": occurrence_total,
            "equation_occurrences": equation_total,
            "independent_wmf_occurrences": independent_total,
            "unique_mtef": unique_mtef,
            "unique_wmf_previews": unique_previews,
            "mtef_versions": versions,
            "mtef_status": status_counts,
            "preview_status": preview_status,
            "unresolved_mtef": unresolved_mtef,
            "unresolved_independent_wmf": unresolved_independent,
            "review_tasks": len(review_tasks),
        },
        "ok": unresolved_mtef == 0 and unresolved_independent == 0,
        "database": str(paths.db),
        "review_tasks": str(paths.reports / "formula_review_tasks.jsonl"),
    }
    atomic_json(paths.reports / "formula_audit_summary.json", summary)
    completion = paths.root / "completion.json"
    if summary["ok"]:
        atomic_json(
            completion,
            {
                "schema_version": 1,
                "status": "completion_ready",
                "contract_version": CONTRACT_VERSION,
                "runtime_fingerprint": fingerprint,
                "summary_sha256": sha256_path(paths.reports / "formula_audit_summary.json"),
                "database_integrity": store.connection.execute("PRAGMA integrity_check").fetchone()[0],
                "completed_at": utc_now(),
            },
        )
    else:
        completion.unlink(missing_ok=True)
    return summary


def run_audit(
    input_path: Path,
    task_id: str,
    *,
    stages: Sequence[str] = DEFAULT_STAGES,
    resume: bool = True,
    rebuild: bool = False,
    parse_timeout: int = 1800,
    render_timeout: int = 300,
    model_timeout: int = 7200,
) -> dict[str, Any]:
    del resume  # Resume is the default because every stage queries durable state.
    paths = AuditPaths.for_task(task_id)
    paths.root.mkdir(parents=True, exist_ok=True)
    lock, lock_token = acquire_task_lock(paths.root)
    store = AuditStore(paths.db)
    fingerprint = runtime_fingerprint()
    parser_fingerprint = parser_runtime_fingerprint()
    store.set_meta("contract_version", CONTRACT_VERSION)
    store.set_meta("runtime_fingerprint", fingerprint)
    store.set_meta("parser_fingerprint", parser_fingerprint)
    store.set_meta("task_id", task_id)
    store.connection.commit()
    result: dict[str, Any] = {"task_id": task_id, "state_root": str(paths.root), "stages": {}}
    try:
        if "scan" in stages:
            docx_files = [input_path.resolve()] if input_path.is_file() else sorted(input_path.rglob("*.docx"))
            if not docx_files:
                raise FileNotFoundError(f"no DOCX files found: {input_path}")
            scan_results = []
            for docx in docx_files:
                try:
                    scan_results.append(scan_docx(docx, store, paths, rebuild=rebuild))
                except Exception as exc:
                    store.attempt("scan", str(docx), "failed", repr(exc), time.time())
                    scan_results.append({"status": "failed", "path": str(docx), "error": repr(exc)})
            result["stages"]["scan"] = dict(Counter(item["status"] for item in scan_results))
        if "parse" in stages:
            result["stages"]["parse"] = parse_pending_mtef(
                store, paths, fingerprint, parser_fingerprint, rebuild=rebuild, timeout=parse_timeout
            )
        if "render" in stages:
            result["stages"]["render"] = render_pending_previews(store, paths, rebuild=rebuild, timeout=render_timeout)
        if "models" in stages:
            result["stages"]["models"] = run_model_evidence(store, paths, rebuild=rebuild, timeout=model_timeout)
        if "export" in stages:
            result["summary"] = export_reports(store, paths, fingerprint)
            result["ok"] = bool(result["summary"]["ok"])
        else:
            result["ok"] = True
        return result
    finally:
        store.close()
        release_task_lock(lock, lock_token)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="DOCX file or directory")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--stages", default=",".join(DEFAULT_STAGES), help="comma-separated scan,parse,render,models,export")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true", default=True)
    mode.add_argument("--rebuild", action="store_true")
    parser.add_argument("--parse-timeout", type=int, default=1800)
    parser.add_argument("--render-timeout", type=int, default=300)
    parser.add_argument("--model-timeout", type=int, default=7200)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stages = tuple(value.strip() for value in args.stages.split(",") if value.strip())
    unknown = [stage for stage in stages if stage not in DEFAULT_STAGES]
    if unknown:
        raise SystemExit(f"unknown stages: {', '.join(unknown)}")
    payload = run_audit(
        args.input,
        args.task_id,
        stages=stages,
        resume=args.resume,
        rebuild=args.rebuild,
        parse_timeout=args.parse_timeout,
        render_timeout=args.render_timeout,
        model_timeout=args.model_timeout,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
