"""Persistent, evidence-backed batching for long Role D curation campaigns."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from curation_evidence import (
    next_pending_segment,
    prepare_question_evidence,
    record_question_evidence,
    summarize_batch_budget,
)


SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1
BUDGET_PROFILE = "quality_stable_60"
MAX_UNIQUE_IMAGES = 16
CAMPAIGN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
QUESTION_ID_RE = re.compile(r"(?:MC|MA|B|C|E)\d{7}", re.IGNORECASE)
TERMINAL_STATUSES = {"applied", "blocked", "no_change"}
ITEM_STATUSES = {"pending", "dry_run_passed", "applied", "blocked", "no_change", "stale"}
REPORT_ROOT_REL = Path("skills/_ops/runtime/reports/role_d_curation")
STATE_ROOT_REL = Path("skills/_ops/runtime/state/role_d_curation_campaigns")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_kb_root(raw: str | None) -> Path:
    if raw:
        return Path(raw).resolve()
    return Path(__file__).resolve().parents[4]


def relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def resolve_path(root: Path, raw: str | Path | None) -> Path | None:
    if raw is None or str(raw).strip() == "":
        return None
    path = Path(str(raw))
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def validate_campaign_id(value: str) -> str:
    if not CAMPAIGN_ID_RE.fullmatch(value):
        raise ValueError("campaign must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
    return value


def campaign_root(kb_root: Path, campaign_id: str) -> Path:
    return kb_root / STATE_ROOT_REL / validate_campaign_id(campaign_id)


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


@contextmanager
def campaign_lock(root: Path, timeout_seconds: float = 10.0) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".lock"
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"{os.getpid()} {utc_now()}\n".encode("ascii"))
            os.close(descriptor)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 60:
                    lock.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"campaign state is locked: {lock}")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def migrate_state_v1(state: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(state)
    migrated["schema_version"] = SCHEMA_VERSION
    migrated["budget_profile"] = BUDGET_PROFILE
    migrated["max_unique_images"] = MAX_UNIQUE_IMAGES
    migrated["context_budget_ratio"] = 0.60
    migrated.setdefault("context_window_tokens", 272_000)
    migrated.setdefault("runner_completion_barriers", {})
    migrated["_migration_required"] = True
    migrated["batches"] = [dict(batch) for batch in state.get("batches") or []]
    for batch in migrated["batches"]:
        batch.setdefault("claimed_by_runner", None)
        batch.setdefault("completed_by_runner", None)
        batch.setdefault("evidence_stage", "pending")
        batch.setdefault("segments_total", 0)
        batch.setdefault("segments_completed", 0)
        batch.setdefault("unique_image_count", 0)
        batch.setdefault("total_pixels", 0)
        batch.setdefault("text_bytes", 0)
        batch.setdefault("active_work_unit", None)
    return migrated


def load_state(root: Path) -> dict[str, Any]:
    path = root / "campaign.json"
    if not path.is_file():
        raise FileNotFoundError(f"campaign not initialized: {path}")
    state = load_json_object(path, "campaign state")
    if state.get("schema_version") == LEGACY_SCHEMA_VERSION:
        return migrate_state_v1(state)
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported campaign schema_version")
    return state


def save_state(root: Path, state: dict[str, Any]) -> None:
    if state.pop("_migration_required", False):
        source = root / "campaign.json"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = root / f"campaign.v1.{timestamp}.json"
        if source.is_file() and not backup.exists():
            shutil.copy2(source, backup)
    state["updated_at"] = utc_now()
    atomic_write_json(root / "campaign.json", state)


def resolve_runner_id(args: argparse.Namespace) -> tuple[str | None, list[str]]:
    runner_id = str(getattr(args, "runner_id", None) or os.environ.get("CODEX_THREAD_ID") or "").strip()
    if runner_id:
        return runner_id, []
    return None, ["runner_id_unavailable_thread_gate_disabled"]


def normalize_source_key(raw: str | None) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    if not value:
        return ""
    marker = "source-library/"
    index = value.find(marker)
    if index >= 0:
        return value[index:]
    while value.startswith("../"):
        value = value[3:]
    return value.removeprefix("./")


def resolve_source_file(kb_root: Path, raw: str | None) -> Path | None:
    key = normalize_source_key(raw)
    if not key:
        return None
    candidate = (kb_root / key).resolve()
    return candidate if candidate.is_file() else None


def natural_question_key(item: dict[str, Any]) -> tuple[Any, ...]:
    raw_number = str(item.get("source_question_no") or "")
    numbers = tuple(int(value) for value in re.findall(r"\d+", raw_number))
    return (numbers or (10**9,), raw_number, str(item.get("question_id") or ""))


def discover_source_manifests(kb_root: Path) -> dict[str, str]:
    base = kb_root / REPORT_ROOT_REL / "source_groups"
    candidates: dict[str, tuple[tuple[int, int, float], Path]] = {}
    if not base.is_dir():
        return {}
    for path in base.rglob("manifest.json"):
        try:
            payload = load_json_object(path, "source manifest")
        except Exception:
            continue
        key = normalize_source_key(str(payload.get("source_path") or ""))
        if not key:
            continue
        questions = payload.get("questions") if isinstance(payload.get("questions"), list) else []
        score = (
            int(payload.get("mapped_target_count") or 0),
            len(questions),
            path.stat().st_mtime,
        )
        if key not in candidates or score > candidates[key][0]:
            candidates[key] = (score, path)
    return {key: relative_or_absolute(value[1], kb_root) for key, value in candidates.items()}


def validation_command_names(report: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for validation in report.get("validations") or []:
        if not isinstance(validation, dict):
            continue
        command = validation.get("command") or []
        if isinstance(command, str):
            parts = command.split()
        elif isinstance(command, list):
            parts = [str(value) for value in command]
        else:
            parts = []
        names.update(Path(value).name.lower() for value in parts)
    return names


def validate_apply_report(
    report_path: Path,
    kb_root: Path,
    question_id: str,
    target_path: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    try:
        report = load_json_object(report_path, "curation report")
    except Exception as exc:
        return False, f"invalid report: {exc}", None
    checks = [
        report.get("ok") is True,
        report.get("mode") == "apply",
        report.get("applied") is True,
        report.get("rolled_back") is not True,
        str(report.get("question_id") or "").upper() == question_id.upper(),
        report.get("schema_version") == 2,
        report.get("model_profile") == "strong",
    ]
    if not all(checks):
        return False, "report is not a successful strong v2 apply", report
    validations = report.get("validations")
    if not isinstance(validations, list) or len(validations) < 2:
        return False, "successful apply requires RAG and image validations", report
    for value in validations:
        if not isinstance(value, dict) or value.get("ok") is not True:
            return False, "one or more post-apply validations failed", report
        try:
            returncode = int(value.get("returncode", 1))
        except (TypeError, ValueError):
            return False, "one or more post-apply validations have an invalid return code", report
        if returncode != 0:
            return False, "one or more post-apply validations failed", report
    command_names = validation_command_names(report)
    required = {"validate_exercise_rag_compat.py", "audit_exercise_images.py"}
    if not required.issubset(command_names):
        return False, "RAG compatibility and image audit evidence are both required", report
    target = resolve_path(kb_root, target_path)
    if target is None or not target.is_file():
        return False, "target file is missing", report
    current_hash = sha256(target)
    if current_hash != str(report.get("new_sha256") or ""):
        return False, "current target hash does not match successful apply report", report
    return True, "ok", report


def validate_dry_run_report(
    report_path: Path,
    kb_root: Path,
    question_id: str,
    target_path: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    try:
        report = load_json_object(report_path, "curation report")
    except Exception as exc:
        return False, f"invalid report: {exc}", None
    checks = [
        report.get("ok") is True,
        report.get("mode") == "dry-run",
        report.get("applied") is not True,
        report.get("rolled_back") is not True,
        str(report.get("question_id") or "").upper() == question_id.upper(),
    ]
    if not all(checks):
        return False, "report is not a successful dry-run", report
    target = resolve_path(kb_root, target_path)
    if target is None or not target.is_file():
        return False, "target file is missing", report
    if sha256(target) != str(report.get("expected_sha256") or ""):
        return False, "dry-run target hash is stale", report
    return True, "ok", report


def report_candidates(kb_root: Path) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    root = kb_root / REPORT_ROOT_REL
    if not root.is_dir():
        return result
    for path in root.rglob("curation_report.json"):
        try:
            payload = load_json_object(path, "curation report")
            question_id = str(payload.get("question_id") or "").upper()
        except Exception:
            continue
        if QUESTION_ID_RE.fullmatch(question_id):
            result.setdefault(question_id, []).append(path)
    for paths in result.values():
        paths.sort(key=lambda value: value.stat().st_mtime, reverse=True)
    return result


def recalculate_batches(state: dict[str, Any]) -> None:
    items = state["items"]
    for batch in state["batches"]:
        statuses = [items[question_id]["status"] for question_id in batch["question_ids"]]
        if not statuses or all(status in TERMINAL_STATUSES for status in statuses):
            batch["status"] = "completed"
            batch.setdefault("completed_at", utc_now())
        elif batch.get("claimed_at"):
            batch["status"] = "in_progress"
            batch.pop("completed_at", None)
        elif any(status in {"dry_run_passed", "stale"} for status in statuses):
            batch["status"] = "ready"
            batch.pop("completed_at", None)
        else:
            batch["status"] = "pending"
            batch.pop("completed_at", None)
    active = state.get("active_batch_id")
    if active:
        active_batch = next((value for value in state["batches"] if value["batch_id"] == active), None)
        if active_batch is None or active_batch["status"] == "completed":
            state["active_batch_id"] = None


def refresh_source_manifests(state: dict[str, Any], kb_root: Path) -> int:
    manifests = discover_source_manifests(kb_root)
    changed = 0
    for batch in state["batches"]:
        if batch["kind"] != "curation":
            continue
        manifest = manifests.get(normalize_source_key(batch.get("source_path")))
        if manifest and batch.get("source_manifest") != manifest:
            batch["source_manifest"] = manifest
            changed += 1
    state["source_manifest_count"] = len(manifests)
    return changed


def reconcile_state(state: dict[str, Any], kb_root: Path) -> dict[str, int]:
    candidates = report_candidates(kb_root)
    changed = 0
    applied = 0
    dry_run = 0
    stale = 0
    for question_id, item in state["items"].items():
        if item["status"] in {"blocked", "no_change"}:
            continue
        previous = item["status"]
        matched_apply: tuple[Path, dict[str, Any]] | None = None
        matched_dry: tuple[Path, dict[str, Any]] | None = None
        for path in candidates.get(question_id, []):
            ok, _, report = validate_apply_report(path, kb_root, question_id, item["target_path"])
            if ok and report is not None:
                matched_apply = (path, report)
                break
            if matched_dry is None:
                dry_ok, _, dry_report = validate_dry_run_report(path, kb_root, question_id, item["target_path"])
                if dry_ok and dry_report is not None:
                    matched_dry = (path, dry_report)
        if matched_apply:
            path, report = matched_apply
            item["status"] = "applied"
            item["report"] = relative_or_absolute(path, kb_root)
            item["current_sha256"] = report["new_sha256"]
            item["completed_at"] = item.get("completed_at") or utc_now()
            applied += 1
        elif matched_dry:
            path, _ = matched_dry
            item["status"] = "dry_run_passed"
            item["report"] = relative_or_absolute(path, kb_root)
            item.pop("completed_at", None)
            dry_run += 1
        else:
            target = resolve_path(kb_root, item["target_path"])
            expected = str(item.get("current_sha256") or "")
            if previous == "applied" or (target and target.is_file() and expected and sha256(target) != expected):
                item["status"] = "stale"
                item.pop("completed_at", None)
                stale += 1
            elif previous not in {"pending", "stale"}:
                item["status"] = "pending"
                item.pop("completed_at", None)
        if item["status"] != previous:
            changed += 1
    recalculate_batches(state)
    return {"changed": changed, "applied": applied, "dry_run_passed": dry_run, "stale": stale}


def batch_lookup(state: dict[str, Any], batch_id: str) -> dict[str, Any]:
    for batch in state["batches"]:
        if batch["batch_id"] == batch_id:
            return batch
    raise KeyError(f"unknown batch: {batch_id}")


def move_source_mapped_item(
    state: dict[str, Any],
    current_batch: dict[str, Any],
    item: dict[str, Any],
    source_manifest: str | None,
) -> dict[str, Any]:
    question_id = item["question_id"]
    current_batch["question_ids"] = [value for value in current_batch["question_ids"] if value != question_id]
    target = next(
        (
            batch for batch in reversed(state["batches"])
            if batch["kind"] == "curation"
            and batch.get("source_path") == item["source_key"]
            and batch["status"] in {"pending", "ready"}
            and not batch.get("claimed_at")
            and len(batch["question_ids"]) < int(state["batch_size"])
        ),
        None,
    )
    if target is None:
        next_number = max(
            (int(match.group(1)) for batch in state["batches"] if (match := re.fullmatch(r"batch-(\d+)", batch["batch_id"]))),
            default=0,
        ) + 1
        target = {
            "batch_id": f"batch-{next_number:04d}",
            "kind": "curation",
            "status": "pending",
            "source_path": item["source_key"],
            "source_manifest": source_manifest,
            "question_ids": [],
            "claim_count": 0,
            "claimed_by_runner": None,
            "completed_by_runner": None,
            "evidence_stage": "pending",
            "segments_total": 0,
            "segments_completed": 0,
            "unique_image_count": 0,
            "total_pixels": 0,
            "text_bytes": 0,
            "active_work_unit": None,
        }
        insertion = next(
            (index for index, batch in enumerate(state["batches"]) if batch["kind"] == "source_resolution"),
            len(state["batches"]),
        )
        state["batches"].insert(insertion, target)
    target["question_ids"].append(question_id)
    target["question_ids"].sort(key=lambda value: natural_question_key(state["items"][value]))
    target["evidence_stage"] = "pending"
    return target


def status_counts(state: dict[str, Any]) -> dict[str, int]:
    counts = {status: 0 for status in sorted(ITEM_STATUSES)}
    for item in state["items"].values():
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    counts["total"] = len(state["items"])
    counts["terminal"] = sum(counts.get(value, 0) for value in TERMINAL_STATUSES)
    counts["remaining"] = counts["total"] - counts["terminal"]
    counts["batches_total"] = len(state["batches"])
    counts["batches_completed"] = sum(value["status"] == "completed" for value in state["batches"])
    return counts


def write_batch_handoff(root: Path, state: dict[str, Any], batch: dict[str, Any]) -> str:
    items = state["items"]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": state["campaign_id"],
        "batch_id": batch["batch_id"],
        "batch_status": batch["status"],
        "claimed_by_runner": batch.get("claimed_by_runner"),
        "completed_by_runner": batch.get("completed_by_runner"),
        "evidence_stage": batch.get("evidence_stage", "pending"),
        "segments_total": int(batch.get("segments_total") or 0),
        "segments_completed": int(batch.get("segments_completed") or 0),
        "updated_at": utc_now(),
        "items": [
            {
                "question_id": question_id,
                "status": items[question_id]["status"],
                "proposal": items[question_id].get("proposal"),
                "report": items[question_id].get("report"),
                "evidence": items[question_id].get("evidence"),
                "reason": items[question_id].get("resolution_reason"),
            }
            for question_id in batch["question_ids"]
        ],
    }
    path = root / "handoff" / f"{batch['batch_id']}.json"
    atomic_write_json(path, payload)
    return str(path)


def create_batch_manifest(
    kb_root: Path,
    root: Path,
    state: dict[str, Any],
    batch: dict[str, Any],
) -> str | None:
    manifest_raw = batch.get("source_manifest")
    if not manifest_raw:
        return None
    manifest_path = resolve_path(kb_root, manifest_raw)
    if manifest_path is None or not manifest_path.is_file():
        return None
    try:
        manifest = load_json_object(manifest_path, "source manifest")
    except Exception:
        return None
    wanted = set(batch["question_ids"])
    source_questions = [
        value for value in manifest.get("questions") or []
        if isinstance(value, dict) and str(value.get("question_id") or "").upper() in wanted
    ]
    questions = []
    for question in source_questions:
        relationships = [value for value in question.get("relationships") or [] if isinstance(value, dict)]
        questions.append({
            "question_id": str(question.get("question_id") or "").upper(),
            "source_question_no": question.get("source_question_no"),
            "target_path": question.get("target_path"),
            "current_sha256": question.get("current_sha256"),
            "relationship_count": len(relationships),
            "relationship_bytes": sum(int(value.get("bytes") or 0) for value in relationships),
            "unique_relationship_hashes": len({
                str(value.get("sha256")) for value in relationships if value.get("sha256")
            }),
        })
    payload = {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": state["campaign_id"],
        "batch_id": batch["batch_id"],
        "source_path": manifest.get("source_path") or batch.get("source_path"),
        "source_manifest": relative_or_absolute(manifest_path, kb_root),
        "question_count": len(questions),
        "questions": questions,
    }
    output = root / "batch_manifests" / f"{batch['batch_id']}.json"
    atomic_write_json(output, payload)
    return str(output)


def build_task_card(kb_root: Path, root: Path, state: dict[str, Any], batch: dict[str, Any]) -> dict[str, Any]:
    source_file = resolve_source_file(kb_root, batch.get("source_path"))
    batch_manifest = create_batch_manifest(kb_root, root, state, batch)
    items = state["items"]
    output_dir = root / "work" / batch["batch_id"]
    output_dir.mkdir(parents=True, exist_ok=True)
    handoff = root / "handoff" / f"{batch['batch_id']}.json"
    evidence_ledgers = [
        str(root / "work" / batch["batch_id"] / "evidence" / question_id / "ledger.json")
        for question_id in batch["question_ids"]
        if (root / "work" / batch["batch_id"] / "evidence" / question_id / "ledger.json").is_file()
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": state["campaign_id"],
        "batch_id": batch["batch_id"],
        "work_unit_id": f"{batch['batch_id']}:curation",
        "kind": batch["kind"],
        "resumed": int(batch.get("claim_count") or 0) > 1,
        "work_unit_kind": "curation",
        "claimed_by_runner": batch.get("claimed_by_runner"),
        "completed_by_runner": batch.get("completed_by_runner"),
        "new_task_required": False,
        "evidence_stage": batch.get("evidence_stage", "pending"),
        "segments_total": int(batch.get("segments_total") or 0),
        "segments_completed": int(batch.get("segments_completed") or 0),
        "unique_image_count": int(batch.get("unique_image_count") or 0),
        "total_pixels": int(batch.get("total_pixels") or 0),
        "text_bytes": int(batch.get("text_bytes") or 0),
        "budget_profile": state.get("budget_profile", BUDGET_PROFILE),
        "max_unique_images": int(state.get("max_unique_images") or MAX_UNIQUE_IMAGES),
        "teacher_authorized": state["teacher_authorized"],
        "allow_destructive": False,
        "source": {
            "path": batch.get("source_path") or None,
            "sha256": sha256(source_file) if source_file else None,
            "exists": bool(source_file),
            "source_manifest": batch.get("source_manifest"),
            "batch_manifest": batch_manifest,
        },
        "evidence": {
            "ledger_paths": evidence_ledgers,
            "reopen_only_statuses": ["unresolved", "high_risk"],
            "do_not_reopen_all_images": True,
        },
        "items": [
            {
                "question_id": question_id,
                "status": items[question_id]["status"],
                "target_path": items[question_id]["target_path"],
                "current_sha256": items[question_id].get("current_sha256"),
                "source_question_no": items[question_id].get("source_question_no"),
                "reasons": items[question_id].get("reasons") or [],
                "proposal": items[question_id].get("proposal"),
                "report": items[question_id].get("report"),
            }
            for question_id in batch["question_ids"]
            if items[question_id]["status"] not in TERMINAL_STATUSES
        ],
        "required_steps": (
            ["locate_and_hash_authoritative_source", "record_source_mapping_or_evidence_block"]
            if batch["kind"] == "source_resolution"
            else (["prepare_source_manifest_once"] if not batch.get("source_manifest") else []) + [
                "read_confirmed_evidence_ledgers_only",
                "reopen_only_unresolved_or_high_risk_originals",
                "proposal",
                "dry_run",
                "apply_with_teacher_authorized_when_gates_pass",
                "record_evidence",
                "write_handoff_and_stop",
            ]
        ),
        "output_dir": str(output_dir),
        "handoff": str(handoff),
        "stop_after_batch": True,
    }


def compact_claim_response(card: dict[str, Any], card_path: Path, warnings: list[str]) -> dict[str, Any]:
    return {
        "ok": True,
        "complete": False,
        "campaign_id": card["campaign_id"],
        "batch_id": card["batch_id"],
        "work_unit_id": card["work_unit_id"],
        "work_unit_kind": card["work_unit_kind"],
        "resumed": bool(card.get("resumed", False)),
        "claimed_by_runner": card.get("claimed_by_runner"),
        "new_task_required": False,
        "evidence_stage": card.get("evidence_stage"),
        "segments_total": int(card.get("segments_total") or 0),
        "segments_completed": int(card.get("segments_completed") or 0),
        "unique_image_count": int(card.get("unique_image_count") or 0),
        "total_pixels": int(card.get("total_pixels") or 0),
        "text_bytes": int(card.get("text_bytes") or 0),
        "budget_profile": card.get("budget_profile", BUDGET_PROFILE),
        "max_unique_images": int(card.get("max_unique_images") or MAX_UNIQUE_IMAGES),
        "estimated_context_ratio": float(card.get("estimated_context_ratio") or 0),
        "task_card": str(card_path),
        "warnings": warnings,
    }


def build_evidence_task_card(
    root: Path,
    state: dict[str, Any],
    batch: dict[str, Any],
    ledger: dict[str, Any],
    segment: dict[str, Any],
    runner_id: str | None,
) -> tuple[dict[str, Any], Path]:
    question_id = str(ledger["question_id"])
    segment_id = str(segment["segment_id"])
    output_dir = root / "work" / batch["batch_id"] / "evidence" / question_id / "reviews"
    output_dir.mkdir(parents=True, exist_ok=True)
    handoff = root / "handoff" / f"{batch['batch_id']}.json"
    card = {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": state["campaign_id"],
        "batch_id": batch["batch_id"],
        "work_unit_id": f"{batch['batch_id']}:{question_id}:{segment_id}",
        "work_unit_kind": "evidence_segment",
        "question_id": question_id,
        "segment_id": segment_id,
        "claimed_by_runner": runner_id,
        "completed_by_runner": None,
        "new_task_required": False,
        "teacher_authorized": state["teacher_authorized"],
        "allow_destructive": False,
        "evidence_stage": ledger.get("evidence_stage", "pending"),
        "segments_total": int(ledger.get("segments_total") or 0),
        "segments_completed": int(ledger.get("segments_completed") or 0),
        "budget_profile": ledger.get("budget_profile", BUDGET_PROFILE),
        "max_unique_images": int(ledger.get("max_unique_images") or MAX_UNIQUE_IMAGES),
        "unique_image_count": int(segment.get("unique_image_count") or 0),
        "total_pixels": int(segment.get("total_pixels") or 0),
        "text_bytes": int(segment.get("text_bytes") or 0),
        "estimated_context_ratio": float(segment.get("estimated_context_ratio") or 0),
        "segment_manifest": segment["manifest"],
        "segment_manifest_sha256": segment["segment_manifest_sha256"],
        "required_steps": [
            "open_only_segment_manifest",
            "review_each_original_full_resolution_image",
            "treat_ocr_as_candidate_only",
            "record_hash_bound_segment_evidence",
            "write_handoff_and_stop",
        ],
        "quality_rules": {
            "allow_crop": False,
            "allow_resize": False,
            "allow_low_resolution_preview": False,
        },
        "output_dir": str(output_dir),
        "handoff": str(handoff),
        "stop_after_work_unit": True,
    }
    card_path = root / "task_cards" / f"{batch['batch_id']}--{question_id}--{segment_id}.json"
    return card, card_path


def ensure_batch_evidence(
    kb_root: Path,
    root: Path,
    state: dict[str, Any],
    batch: dict[str, Any],
) -> dict[str, Any]:
    if batch["kind"] != "curation":
        return summarize_batch_budget(root, batch)
    evidence_root = root / "work" / batch["batch_id"] / "evidence"
    for question_id in batch["question_ids"]:
        ledger = evidence_root / question_id / "ledger.json"
        if not ledger.is_file():
            prepare_question_evidence(kb_root, root, state, batch, question_id)
    budget = summarize_batch_budget(root, batch)
    ledgers_complete = budget["questions_prepared"] == len(batch["question_ids"])
    all_segments_complete = budget["segments_total"] == budget["segments_completed"]
    evidence_stage = (
        "blocked" if budget.get("segments_review_blocked")
        else ("complete" if ledgers_complete and all_segments_complete else "in_progress")
    )
    batch.update({
        "evidence_stage": evidence_stage,
        "segments_total": budget["segments_total"],
        "segments_completed": budget["segments_completed"],
        "unique_image_count": budget["unique_image_count"],
        "total_pixels": budget["total_pixels"],
        "text_bytes": budget["text_bytes"],
    })
    return budget


def render_status_markdown(state: dict[str, Any]) -> str:
    counts = status_counts(state)
    active = state.get("active_batch_id") or "无"
    lines = [
        f"# Role D 校对 Campaign：{state['campaign_id']}",
        "",
        f"- 总题数：{counts['total']}",
        f"- 已完成：{counts['terminal']}（apply {counts.get('applied', 0)} / 阻断 {counts.get('blocked', 0)} / 无需修改 {counts.get('no_change', 0)}）",
        f"- 待继续：{counts['remaining']}（dry-run {counts.get('dry_run_passed', 0)} / stale {counts.get('stale', 0)}）",
        f"- 批次：{counts['batches_completed']}/{counts['batches_total']}",
        f"- 当前批次：{active}",
        f"- 每批上限：{state['batch_size']}",
        f"- 教师整项授权：{'是' if state['teacher_authorized'] else '否'}",
        "",
    ]
    if state.get("active_batch_id"):
        batch = batch_lookup(state, state["active_batch_id"])
        lines.extend(["## 当前批次", "", f"- `{batch['batch_id']}`：{', '.join(batch['question_ids'])}", ""])
    return "\n".join(lines)


def initialize_campaign(args: argparse.Namespace, kb_root: Path) -> dict[str, Any]:
    ledger_path = resolve_path(kb_root, args.ledger)
    if ledger_path is None or not ledger_path.is_file():
        raise FileNotFoundError(f"ledger not found: {args.ledger}")
    ledger = load_json_object(ledger_path, "issue ledger")
    raw_items = ledger.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("issue ledger must contain an items array")
    root = campaign_root(kb_root, args.campaign)
    state_path = root / "campaign.json"
    if state_path.exists():
        raise FileExistsError(f"campaign already exists: {state_path}")
    source_manifests = discover_source_manifests(kb_root)
    items: dict[str, dict[str, Any]] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("ledger items must be objects")
        question_id = str(raw.get("question_id") or "").upper()
        if not QUESTION_ID_RE.fullmatch(question_id):
            raise ValueError(f"invalid question_id in ledger: {question_id}")
        if question_id in items:
            raise ValueError(f"duplicate question_id in ledger: {question_id}")
        target_path = str(raw.get("target_path") or "")
        target = resolve_path(kb_root, target_path)
        current_hash = sha256(target) if target and target.is_file() else str(raw.get("current_sha256") or "")
        source_path = str(raw.get("source_path") or "")
        item = {
            "question_id": question_id,
            "status": "pending",
            "target_path": target_path,
            "current_sha256": current_hash,
            "source_title": raw.get("source_title") or "",
            "source_path": source_path,
            "source_key": normalize_source_key(source_path),
            "source_question_no": str(raw.get("source_question_no") or ""),
            "reasons": list(raw.get("reasons") or []),
            "report_references": list(raw.get("report_references") or []),
        }
        evidence = raw.get("apply_evidence")
        if isinstance(evidence, dict) and evidence.get("report"):
            report_path = resolve_path(kb_root, evidence["report"])
            if report_path and report_path.is_file():
                valid, _, report = validate_apply_report(report_path, kb_root, question_id, target_path)
                if valid and report is not None:
                    item["status"] = "applied"
                    item["report"] = relative_or_absolute(report_path, kb_root)
                    item["current_sha256"] = report["new_sha256"]
                    item["completed_at"] = utc_now()
        items[question_id] = item

    grouped: dict[str, list[dict[str, Any]]] = {}
    unresolved: list[dict[str, Any]] = []
    for item in items.values():
        if item["status"] in TERMINAL_STATUSES:
            continue
        if item["source_key"]:
            grouped.setdefault(item["source_key"], []).append(item)
        else:
            unresolved.append(item)
    batches: list[dict[str, Any]] = []
    sequence = 1
    for source_key in sorted(grouped, key=str.casefold):
        group = sorted(grouped[source_key], key=natural_question_key)
        for offset in range(0, len(group), args.batch_size):
            chunk = group[offset: offset + args.batch_size]
            batches.append({
                "batch_id": f"batch-{sequence:04d}",
                "kind": "curation",
                "status": "pending",
                "source_path": source_key,
                "source_manifest": source_manifests.get(source_key),
                "question_ids": [value["question_id"] for value in chunk],
                "claim_count": 0,
                "claimed_by_runner": None,
                "completed_by_runner": None,
                "evidence_stage": "pending",
                "segments_total": 0,
                "segments_completed": 0,
                "unique_image_count": 0,
                "total_pixels": 0,
                "text_bytes": 0,
                "active_work_unit": None,
            })
            sequence += 1
    unresolved.sort(key=natural_question_key)
    for offset in range(0, len(unresolved), args.batch_size):
        chunk = unresolved[offset: offset + args.batch_size]
        batches.append({
            "batch_id": f"batch-{sequence:04d}",
            "kind": "source_resolution",
            "status": "pending",
            "source_path": "",
            "source_manifest": None,
            "question_ids": [value["question_id"] for value in chunk],
            "claim_count": 0,
            "claimed_by_runner": None,
            "completed_by_runner": None,
            "evidence_stage": "not_required",
            "segments_total": 0,
            "segments_completed": 0,
            "unique_image_count": 0,
            "total_pixels": 0,
            "text_bytes": 0,
            "active_work_unit": None,
        })
        sequence += 1
    now = utc_now()
    state = {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": args.campaign,
        "created_at": now,
        "updated_at": now,
        "ledger_path": relative_or_absolute(ledger_path, kb_root),
        "ledger_sha256": sha256(ledger_path),
        "batch_size": args.batch_size,
        "teacher_authorized": bool(args.teacher_authorized),
        "allow_destructive": False,
        "budget_profile": BUDGET_PROFILE,
        "max_unique_images": MAX_UNIQUE_IMAGES,
        "context_budget_ratio": 0.60,
        "context_window_tokens": int(args.context_window_tokens),
        "runner_completion_barriers": {},
        "active_batch_id": None,
        "source_manifest_count": len(source_manifests),
        "items": items,
        "batches": batches,
    }
    recalculate_batches(state)
    with campaign_lock(root):
        save_state(root, state)
        atomic_write_text(root / "status.md", render_status_markdown(state))
    counts = status_counts(state)
    return {
        "ok": True,
        "campaign_id": args.campaign,
        "state": str(root / "campaign.json"),
        "ledger_count": len(raw_items),
        "applied": counts.get("applied", 0),
        "remaining": counts["remaining"],
        "curation_batches": sum(value["kind"] == "curation" for value in batches),
        "source_resolution_batches": sum(value["kind"] == "source_resolution" for value in batches),
        "source_manifest_count": len(source_manifests),
    }


def claim_campaign(args: argparse.Namespace, kb_root: Path) -> dict[str, Any]:
    root = campaign_root(kb_root, args.campaign)
    runner_id, warnings = resolve_runner_id(args)
    with campaign_lock(root):
        state = load_state(root)
        refresh_source_manifests(state, kb_root)
        reconcile_state(state, kb_root)
        barriers = state.setdefault("runner_completion_barriers", {})
        if runner_id and (
            runner_id in barriers
            or any(batch.get("completed_by_runner") == runner_id for batch in state["batches"])
        ):
            next_available = any(batch["status"] != "completed" for batch in state["batches"])
            save_state(root, state)
            return {
                "ok": True,
                "complete": not next_available,
                "campaign_id": args.campaign,
                "new_task_required": next_available,
                "next_batch_available": next_available,
                "completed_by_runner": runner_id,
                "completed_work_unit": barriers.get(runner_id),
                "warnings": warnings,
            }
        batch: dict[str, Any] | None = None
        if state.get("active_batch_id"):
            candidate = batch_lookup(state, state["active_batch_id"])
            if candidate["status"] != "completed":
                batch = candidate
        if batch is None:
            batch = next((value for value in state["batches"] if value["status"] != "completed"), None)
        if batch is None:
            state["active_batch_id"] = None
            save_state(root, state)
            return {"ok": True, "campaign_id": args.campaign, "complete": True, "counts": status_counts(state)}
        state["active_batch_id"] = batch["batch_id"]
        previous_runner = batch.get("claimed_by_runner")
        if runner_id and previous_runner and previous_runner != runner_id:
            batch["takeover_from_runner"] = previous_runner
        batch["claimed_at"] = utc_now()
        batch["claimed_by_runner"] = runner_id
        batch["claim_count"] = int(batch.get("claim_count") or 0) + 1
        batch["status"] = "in_progress"
        if batch["kind"] == "curation":
            budget = ensure_batch_evidence(kb_root, root, state, batch)
            if budget["over_budget"]:
                write_batch_handoff(root, state, batch)
                save_state(root, state)
                raise RuntimeError(
                    "evidence_budget_blocked: at least one original image exceeds the 60% context budget; "
                    "use a larger-context model or manual review, never crop or downscale"
                )
            pending = next_pending_segment(root, batch)
        else:
            pending = None
        if pending is not None:
            ledger, segment = pending
            work_unit_id = f"{batch['batch_id']}:{ledger['question_id']}:{segment['segment_id']}"
            previous_work_unit = batch.get("active_work_unit") or {}
            takeover = previous_work_unit.get("claimed_by_runner")
            batch["active_work_unit"] = {
                "work_unit_id": work_unit_id,
                "work_unit_kind": "evidence_segment",
                "question_id": ledger["question_id"],
                "segment_id": segment["segment_id"],
                "claimed_by_runner": runner_id,
                "takeover_from_runner": takeover if takeover and takeover != runner_id else None,
                "claimed_at": utc_now(),
            }
            card, card_path = build_evidence_task_card(root, state, batch, ledger, segment, runner_id)
        else:
            batch["active_work_unit"] = {
                "work_unit_id": f"{batch['batch_id']}:curation",
                "work_unit_kind": "curation",
                "claimed_by_runner": runner_id,
                "claimed_at": utc_now(),
            }
            card = build_task_card(kb_root, root, state, batch)
            card_path = root / "task_cards" / f"{batch['batch_id']}.json"
        atomic_write_json(card_path, card)
        save_state(root, state)
        atomic_write_text(root / "status.md", render_status_markdown(state))
    return compact_claim_response(card, card_path, warnings)


def record_campaign(args: argparse.Namespace, kb_root: Path) -> dict[str, Any]:
    root = campaign_root(kb_root, args.campaign)
    runner_id, warnings = resolve_runner_id(args)
    evidence_path = resolve_path(kb_root, args.evidence)
    if evidence_path is None or not evidence_path.is_file():
        raise FileNotFoundError(f"evidence not found: {args.evidence}")
    with campaign_lock(root):
        state = load_state(root)
        question_id = args.question.upper()
        if question_id not in state["items"]:
            raise KeyError(f"question is not in campaign: {question_id}")
        item = state["items"][question_id]
        batch = next(value for value in state["batches"] if question_id in value["question_ids"])
        active_work_unit = batch.get("active_work_unit") or {}
        claimed_by_runner = active_work_unit.get("claimed_by_runner") or batch.get("claimed_by_runner")
        if runner_id and claimed_by_runner and claimed_by_runner != runner_id:
            raise PermissionError(
                f"runner_mismatch: batch is claimed by {claimed_by_runner}, not {runner_id}"
            )
        if batch["kind"] == "curation" and active_work_unit.get("work_unit_kind") != "curation":
            raise PermissionError("curation_record_requires_completed_evidence_and_active_curation_work_unit")
        mapped_batch: dict[str, Any] | None = None
        if args.status == "applied":
            valid, reason, report = validate_apply_report(evidence_path, kb_root, question_id, item["target_path"])
            if not valid or report is None:
                raise ValueError(reason)
            item["status"] = "applied"
            item["report"] = relative_or_absolute(evidence_path, kb_root)
            item["current_sha256"] = report["new_sha256"]
            item["completed_at"] = utc_now()
        elif args.status == "dry-run":
            valid, reason, _ = validate_dry_run_report(evidence_path, kb_root, question_id, item["target_path"])
            if not valid:
                raise ValueError(reason)
            item["status"] = "dry_run_passed"
            item["report"] = relative_or_absolute(evidence_path, kb_root)
            item.pop("completed_at", None)
        elif args.status == "source-mapped":
            if batch["kind"] != "source_resolution":
                raise ValueError("source-mapped is only valid for a source_resolution item")
            if not args.source_path:
                raise ValueError("--source-path is required for status source-mapped")
            source_file = resolve_source_file(kb_root, args.source_path)
            if source_file is None:
                raise FileNotFoundError(f"source file not found under kb root: {args.source_path}")
            item["source_path"] = relative_or_absolute(source_file, kb_root)
            item["source_key"] = normalize_source_key(item["source_path"])
            item["source_evidence"] = relative_or_absolute(evidence_path, kb_root)
            item["source_sha256"] = sha256(source_file)
            item["status"] = "pending"
            item.pop("completed_at", None)
            manifests = discover_source_manifests(kb_root)
            mapped_batch = move_source_mapped_item(state, batch, item, manifests.get(item["source_key"]))
        else:
            if not args.reason:
                raise ValueError(f"--reason is required for status {args.status}")
            item["status"] = "no_change" if args.status == "no-change" else "blocked"
            item["evidence"] = relative_or_absolute(evidence_path, kb_root)
            item["resolution_reason"] = args.reason
            item["completed_at"] = utc_now()
        if args.proposal:
            proposal_path = resolve_path(kb_root, args.proposal)
            if proposal_path is None or not proposal_path.is_file():
                raise FileNotFoundError(f"proposal not found: {args.proposal}")
            item["proposal"] = relative_or_absolute(proposal_path, kb_root)
        recalculate_batches(state)
        if batch["status"] == "completed":
            batch["completed_by_runner"] = runner_id
            if runner_id:
                state.setdefault("runner_completion_barriers", {})[runner_id] = active_work_unit.get(
                    "work_unit_id", f"{batch['batch_id']}:curation"
                )
            batch["active_work_unit"] = None
        handoff = write_batch_handoff(root, state, batch)
        save_state(root, state)
        atomic_write_text(root / "status.md", render_status_markdown(state))
    return {
        "ok": True,
        "campaign_id": args.campaign,
        "batch_id": batch["batch_id"],
        "question_id": question_id,
        "status": item["status"],
        "batch_status": batch["status"],
        "mapped_batch_id": mapped_batch["batch_id"] if mapped_batch else None,
        "handoff": handoff,
        "counts": status_counts(state),
        "warnings": warnings,
    }


def prepare_evidence_campaign(args: argparse.Namespace, kb_root: Path) -> dict[str, Any]:
    root = campaign_root(kb_root, args.campaign)
    runner_id, warnings = resolve_runner_id(args)
    with campaign_lock(root):
        state = load_state(root)
        refresh_source_manifests(state, kb_root)
        question_id = args.question.upper()
        if question_id not in state["items"]:
            raise KeyError(f"question is not in campaign: {question_id}")
        batch = next(value for value in state["batches"] if question_id in value["question_ids"])
        if batch["kind"] != "curation":
            raise ValueError("evidence preparation is only valid for curation batches")
        claimed_by_runner = batch.get("claimed_by_runner")
        if runner_id and claimed_by_runner and claimed_by_runner != runner_id:
            raise PermissionError(
                f"runner_mismatch: batch is claimed by {claimed_by_runner}, not {runner_id}"
            )
        result = prepare_question_evidence(kb_root, root, state, batch, question_id)
        budget = summarize_batch_budget(root, batch)
        evidence_stage = (
            "blocked" if budget.get("segments_review_blocked")
            else (
                "complete"
                if budget["questions_prepared"] == len(batch["question_ids"])
                and budget["segments_total"] == budget["segments_completed"]
                else "in_progress"
            )
        )
        batch.update({
            "evidence_stage": evidence_stage,
            "segments_total": budget["segments_total"],
            "segments_completed": budget["segments_completed"],
            "unique_image_count": budget["unique_image_count"],
            "total_pixels": budget["total_pixels"],
            "text_bytes": budget["text_bytes"],
        })
        save_state(root, state)
    return {
        "ok": True,
        "campaign_id": args.campaign,
        "batch_id": batch["batch_id"],
        "question_id": question_id,
        "budget_profile": state.get("budget_profile", BUDGET_PROFILE),
        "max_unique_images": int(state.get("max_unique_images") or MAX_UNIQUE_IMAGES),
        "warnings": warnings,
        **result,
    }


def record_evidence_campaign(args: argparse.Namespace, kb_root: Path) -> dict[str, Any]:
    root = campaign_root(kb_root, args.campaign)
    runner_id, warnings = resolve_runner_id(args)
    evidence_path = resolve_path(kb_root, args.evidence)
    if evidence_path is None or not evidence_path.is_file():
        raise FileNotFoundError(f"evidence not found: {args.evidence}")
    with campaign_lock(root):
        state = load_state(root)
        question_id = args.question.upper()
        if question_id not in state["items"]:
            raise KeyError(f"question is not in campaign: {question_id}")
        batch = next(value for value in state["batches"] if question_id in value["question_ids"])
        active = batch.get("active_work_unit") or {}
        if active.get("work_unit_kind") != "evidence_segment" or active.get("question_id") != question_id:
            raise PermissionError("record_evidence_requires_an_active_claimed_segment")
        claimed_by_runner = active.get("claimed_by_runner")
        if runner_id and claimed_by_runner and claimed_by_runner != runner_id:
            raise PermissionError(
                f"runner_mismatch: work unit is claimed by {claimed_by_runner}, not {runner_id}"
            )
        evidence_payload = load_json_object(evidence_path, "segment evidence")
        if str(evidence_payload.get("segment_id") or "") != str(active.get("segment_id") or ""):
            raise ValueError("segment evidence does not match the active work unit")
        ledger_path = root / "work" / batch["batch_id"] / "evidence" / question_id / "ledger.json"
        result = record_question_evidence(kb_root, ledger_path, evidence_path, runner_id)
        batch["active_work_unit"] = None
        batch["completed_by_runner"] = runner_id
        if runner_id:
            state.setdefault("runner_completion_barriers", {})[runner_id] = active["work_unit_id"]
        budget = summarize_batch_budget(root, batch)
        evidence_stage = (
            "blocked" if budget.get("segments_review_blocked")
            else (
                "complete"
                if budget["questions_prepared"] == len(batch["question_ids"])
                and budget["segments_total"] == budget["segments_completed"]
                else "in_progress"
            )
        )
        batch.update({
            "evidence_stage": evidence_stage,
            "segments_total": budget["segments_total"],
            "segments_completed": budget["segments_completed"],
            "unique_image_count": budget["unique_image_count"],
            "total_pixels": budget["total_pixels"],
            "text_bytes": budget["text_bytes"],
        })
        handoff = write_batch_handoff(root, state, batch)
        save_state(root, state)
        atomic_write_text(root / "status.md", render_status_markdown(state))
    return {
        "ok": True,
        "campaign_id": args.campaign,
        "batch_id": batch["batch_id"],
        "new_task_required": bool(runner_id),
        "handoff": handoff,
        "warnings": warnings,
        **result,
    }


def budget_status_campaign(args: argparse.Namespace, kb_root: Path) -> dict[str, Any]:
    root = campaign_root(kb_root, args.campaign)
    with campaign_lock(root):
        state = load_state(root)
        batches = []
        for batch in state["batches"]:
            if batch["kind"] != "curation":
                continue
            summary = summarize_batch_budget(root, batch)
            if summary["questions_prepared"] or batch["batch_id"] == state.get("active_batch_id"):
                batches.append({"batch_id": batch["batch_id"], **summary})
    return {
        "ok": True,
        "campaign_id": args.campaign,
        "budget_profile": state.get("budget_profile", BUDGET_PROFILE),
        "context_budget_ratio": float(state.get("context_budget_ratio") or 0.60),
        "context_window_tokens": int(state.get("context_window_tokens") or 272_000),
        "max_unique_images": int(state.get("max_unique_images") or MAX_UNIQUE_IMAGES),
        "batches": batches,
    }


def reconcile_campaign(args: argparse.Namespace, kb_root: Path) -> dict[str, Any]:
    root = campaign_root(kb_root, args.campaign)
    with campaign_lock(root):
        state = load_state(root)
        manifest_changes = refresh_source_manifests(state, kb_root)
        result = reconcile_state(state, kb_root)
        result["source_manifests_refreshed"] = manifest_changes
        if state.get("active_batch_id"):
            write_batch_handoff(root, state, batch_lookup(state, state["active_batch_id"]))
        save_state(root, state)
        markdown = render_status_markdown(state)
        atomic_write_text(root / "status.md", markdown)
    return {"ok": True, "campaign_id": args.campaign, "reconcile": result, "counts": status_counts(state)}


def campaign_status(args: argparse.Namespace, kb_root: Path) -> tuple[dict[str, Any], str]:
    root = campaign_root(kb_root, args.campaign)
    with campaign_lock(root):
        state = load_state(root)
        refresh_source_manifests(state, kb_root)
        recalculate_batches(state)
        markdown = render_status_markdown(state)
        atomic_write_text(root / "status.md", markdown)
    payload = {
        "ok": True,
        "campaign_id": args.campaign,
        "active_batch_id": state.get("active_batch_id"),
        "counts": status_counts(state),
        "status_markdown": str(root / "status.md"),
    }
    return payload, markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage resumable Role D curation campaigns.")
    parser.add_argument("--kb-root", help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Initialize a campaign from an issue ledger.")
    init.add_argument("--ledger", required=True)
    init.add_argument("--campaign", required=True, type=validate_campaign_id)
    init.add_argument("--batch-size", type=int, default=5)
    init.add_argument(
        "--context-window-tokens",
        type=int,
        default=int(os.environ.get("CURATION_CONTEXT_WINDOW_TOKENS", "272000")),
    )
    init.add_argument("--teacher-authorized", action="store_true")
    init.add_argument("--json", action="store_true")

    claim = subparsers.add_parser("claim", help="Claim or resume the next bounded batch.")
    claim.add_argument("--campaign", required=True, type=validate_campaign_id)
    claim.add_argument("--runner-id")
    claim.add_argument("--json", action="store_true")

    record = subparsers.add_parser("record", help="Record evidence for one campaign item.")
    record.add_argument("--campaign", required=True, type=validate_campaign_id)
    record.add_argument("--question", required=True)
    record.add_argument("--status", required=True, choices=["applied", "dry-run", "blocked", "no-change", "source-mapped"])
    record.add_argument("--evidence", required=True)
    record.add_argument("--reason")
    record.add_argument("--proposal")
    record.add_argument("--source-path")
    record.add_argument("--runner-id")
    record.add_argument("--json", action="store_true")

    prepare = subparsers.add_parser("prepare-evidence", help="Prepare full-resolution evidence segments for one question.")
    prepare.add_argument("--campaign", required=True, type=validate_campaign_id)
    prepare.add_argument("--question", required=True)
    prepare.add_argument("--runner-id")
    prepare.add_argument("--json", action="store_true")

    record_evidence = subparsers.add_parser("record-evidence", help="Record one hash-bound evidence segment.")
    record_evidence.add_argument("--campaign", required=True, type=validate_campaign_id)
    record_evidence.add_argument("--question", required=True)
    record_evidence.add_argument("--evidence", required=True)
    record_evidence.add_argument("--runner-id")
    record_evidence.add_argument("--json", action="store_true")

    budget = subparsers.add_parser("budget-status", help="Show compact evidence budget status.")
    budget.add_argument("--campaign", required=True, type=validate_campaign_id)
    budget.add_argument("--json", action="store_true")

    reconcile = subparsers.add_parser("reconcile", help="Reconcile state from authoritative reports.")
    reconcile.add_argument("--campaign", required=True, type=validate_campaign_id)
    reconcile.add_argument("--json", action="store_true")

    status = subparsers.add_parser("status", help="Show compact campaign progress.")
    status.add_argument("--campaign", required=True, type=validate_campaign_id)
    output = status.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true")
    output.add_argument("--markdown", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if getattr(args, "batch_size", 1) < 1 or getattr(args, "batch_size", 1) > 5:
            raise ValueError("batch-size must be between 1 and 5")
        if getattr(args, "context_window_tokens", 272_000) < 32_000:
            raise ValueError("context-window-tokens must be at least 32000")
        kb_root = resolve_kb_root(args.kb_root)
        if args.command == "init":
            payload = initialize_campaign(args, kb_root)
            markdown = None
        elif args.command == "claim":
            payload = claim_campaign(args, kb_root)
            markdown = None
        elif args.command == "record":
            payload = record_campaign(args, kb_root)
            markdown = None
        elif args.command == "prepare-evidence":
            payload = prepare_evidence_campaign(args, kb_root)
            markdown = None
        elif args.command == "record-evidence":
            payload = record_evidence_campaign(args, kb_root)
            markdown = None
        elif args.command == "budget-status":
            payload = budget_status_campaign(args, kb_root)
            markdown = None
        elif args.command == "reconcile":
            payload = reconcile_campaign(args, kb_root)
            markdown = None
        else:
            payload, markdown = campaign_status(args, kb_root)
        if args.command == "status" and args.markdown and markdown is not None:
            print(markdown)
        elif getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(payload.get("task_card") or payload.get("state") or json.dumps(payload, ensure_ascii=False))
        return 0
    except Exception as exc:
        error = {"ok": False, "error": str(exc), "type": type(exc).__name__}
        if getattr(args, "json", False):
            print(json.dumps(error, ensure_ascii=False, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
