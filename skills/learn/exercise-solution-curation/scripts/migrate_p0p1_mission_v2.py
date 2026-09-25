#!/usr/bin/env python3
"""Migrate stable P0/P1 ledgers and abandoned legacy locks into mission v2."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 2
LEGACY_NO_CHANGE_IDS = {"MA0000281", "B0000175"}
PLATFORM_ADAPTER_PATH = Path(__file__).resolve().parents[3] / "_shared/scripts/agent_platform_adapter.py"
PLATFORM_SPEC = importlib.util.spec_from_file_location("p0p1_platform_adapter", PLATFORM_ADAPTER_PATH)
assert PLATFORM_SPEC and PLATFORM_SPEC.loader
platform_adapter = importlib.util.module_from_spec(PLATFORM_SPEC)
PLATFORM_SPEC.loader.exec_module(platform_adapter)
ORCHESTRATOR_PATH = Path(__file__).resolve().parents[3] / "_shared/scripts/agent_mission_orchestrator.py"
ORCHESTRATOR_SPEC = importlib.util.spec_from_file_location("p0p1_agent_mission_orchestrator", ORCHESTRATOR_PATH)
assert ORCHESTRATOR_SPEC and ORCHESTRATOR_SPEC.loader
orchestrator = importlib.util.module_from_spec(ORCHESTRATOR_SPEC)
ORCHESTRATOR_SPEC.loader.exec_module(orchestrator)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_ref(path: Path) -> dict[str, Any]:
    source = path.resolve()
    return {"path": str(source), "sha256": sha256_file(source), "size_bytes": source.stat().st_size}


def parse_utc(value: Any, field: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def load_object(path: str | Path, label: str) -> dict[str, Any]:
    source = Path(path)
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {source}")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write_bytes(path, json_bytes(value))


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def canonical_sha256(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def ensure_inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(root.resolve())
    return resolved


def normalize_relative(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts or ":" in normalized:
        raise ValueError(f"unsafe repository-relative path: {value}")
    return normalized


def capability_for_item(item: dict[str, Any]) -> str:
    severities = {str(value).upper() for value in item.get("reported_severities", [])}
    if "P0" in severities:
        return "complex"
    risky = {"stem", "formula", "figure", "image", "asset", "unanswerable", "题图", "公式", "题干"}
    categories = {str(reason.get("category", "")).lower() for reason in item.get("reasons", []) if isinstance(reason, dict)}
    return "ambiguous" if categories & risky else "bounded"


def transform_item(kb_root: Path, legacy_item: dict[str, Any], drift: list[dict[str, Any]]) -> dict[str, Any]:
    question_id = str(legacy_item["question_id"])
    target_rel = normalize_relative(str(legacy_item["target_path"]))
    target = ensure_inside(kb_root / target_rel, kb_root)
    if not target.is_file():
        raise FileNotFoundError(target)
    target_sha = sha256_file(target)
    expected_target_sha = str(legacy_item.get("current_sha256") or "")
    if target_sha != expected_target_sha:
        raise RuntimeError(f"target hash drift for {question_id}: {expected_target_sha} -> {target_sha}")

    source_ready = legacy_item.get("source_state") == "source_ready"
    source_ref: dict[str, Any] | None = None
    source_raw = legacy_item.get("source_key") or legacy_item.get("source_path")
    if source_ready and source_raw:
        source_rel = normalize_relative(str(source_raw))
        source = ensure_inside(kb_root / source_rel, kb_root)
        expected_source_sha = str(legacy_item.get("source_sha256") or "")
        if source.is_file() and sha256_file(source) == expected_source_sha:
            source_ref = {
                "path_id": "project.root",
                "relative_path": source_rel,
                "sha256": expected_source_sha,
                "size_bytes": source.stat().st_size,
            }
        else:
            source_ready = False
            drift.append(
                {
                    "question_id": question_id,
                    "defect_code": "source_hash_or_path_drift",
                    "legacy_source_path": source_rel,
                    "legacy_source_sha256": expected_source_sha,
                }
            )
    result = {
        "question_id": question_id,
        "queue_ordinal": int(legacy_item["queue_ordinal"]),
        "severity": "P0" if "P0" in legacy_item.get("reported_severities", []) else "P1",
        "source_ready": source_ready,
        "target_ref": {
            "path_id": "project.root",
            "relative_path": target_rel,
            "sha256": target_sha,
            "size_bytes": target.stat().st_size,
        },
        "source_ref": source_ref,
        "capability": capability_for_item(legacy_item),
        "legacy_issue_refs": legacy_item.get("reasons", []),
        "legacy_report_references": legacy_item.get("report_references", []),
        "legacy_asset_refs": legacy_item.get("asset_refs", []),
        "initial_status": "legacy_no_change_pending_audit" if question_id in LEGACY_NO_CHANGE_IDS else "queued",
    }
    return result


def prepare_migration(
    *,
    kb_root: Path,
    manifest_path: Path,
    platform_capabilities_path: Path,
    authorization_record_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    kb_root = kb_root.resolve()
    manifest_path = ensure_inside(manifest_path, kb_root)
    platform_capabilities_path = ensure_inside(platform_capabilities_path, kb_root)
    authorization_record_path = ensure_inside(authorization_record_path, kb_root)
    output_root = ensure_inside(output_root, kb_root)
    manifest = load_object(manifest_path, "legacy manifest")
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("volumes"), list):
        raise ValueError("legacy P0/P1 manifest must use schema_version=1")
    authorization = load_object(authorization_record_path, "authorization record")
    if authorization.get("schema_version") != 1:
        raise ValueError("authorization record must use schema_version=1")
    for field in ("authorized_by", "authorization_basis", "issued_at", "expires_at"):
        if not isinstance(authorization.get(field), str) or not authorization[field]:
            raise ValueError(f"authorization record lacks {field}")
    platform = load_object(platform_capabilities_path, "platform capabilities")
    if platform.get("schema_version") != 2:
        raise ValueError("platform capabilities must use schema_version=2")

    campaigns: list[dict[str, Any]] = []
    total_items = 0
    all_ordinals: dict[str, list[int]] = {}
    for volume_entry in manifest["volumes"]:
        legacy_path = Path(str(volume_entry["ledger"]))
        if not legacy_path.is_absolute():
            legacy_path = kb_root / legacy_path
        legacy_path = ensure_inside(legacy_path, kb_root)
        legacy = load_object(legacy_path, "legacy volume ledger")
        campaign_id = str(legacy["campaign_id"])
        drift: list[dict[str, Any]] = []
        transformed = [transform_item(kb_root, item, drift) for item in legacy["items"]]
        ordinals = [item["queue_ordinal"] for item in transformed]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError(f"duplicate queue ordinal in {campaign_id}")
        all_ordinals[campaign_id] = ordinals
        ledger = {
            "schema_version": 2,
            "campaign_id": campaign_id,
            "profile": "p0p1-exercise-curation-v2",
            "scope_key": str(legacy["volume"]),
            "legacy_ledger": {"path": str(legacy_path), "sha256": sha256_file(legacy_path)},
            "legacy_queue_sha256": volume_entry.get("queue_sha256"),
            "items": transformed,
        }
        campaign_root = output_root / campaign_id
        ledger_path = campaign_root / "ledger.v2.json"
        atomic_write_json(ledger_path, ledger)
        grant = {
            "schema_version": 2,
            "grant_id": f"grant-{campaign_id}-v2",
            "campaign_id": campaign_id,
            "ledger_sha256": sha256_file(ledger_path),
            "authorized_by": authorization["authorized_by"],
            "authorization_basis": authorization["authorization_basis"],
            "authorization_record_path": str(authorization_record_path),
            "authorization_record_sha256": sha256_file(authorization_record_path),
            "allowed_fields": ["stem", "answer", "solution", "assets", "ai_extra_tags", "media"],
            "allowed_path_ids": ["project.root", "skills.ops.runtime"],
            "writer": "exercise-solution-curation/curate_exercise.py",
            "issued_at": authorization["issued_at"],
            "expires_at": authorization["expires_at"],
            "destructive": False,
        }
        grant_path = campaign_root / "authorization-grant.v2.json"
        atomic_write_json(grant_path, grant)
        campaigns.append(
            {
                "campaign_id": campaign_id,
                "volume": legacy["volume"],
                "item_count": len(transformed),
                "source_ready": sum(1 for item in transformed if item["source_ready"]),
                "source_blocked": sum(1 for item in transformed if not item["source_ready"]),
                "legacy_no_change_pending_audit": sum(
                    1 for item in transformed if item["initial_status"] == "legacy_no_change_pending_audit"
                ),
                "ledger": str(ledger_path),
                "ledger_sha256": sha256_file(ledger_path),
                "authorization_grant": str(grant_path),
                "authorization_grant_sha256": sha256_file(grant_path),
                "source_drift": drift,
            }
        )
        total_items += len(transformed)
    if total_items != int(manifest.get("p0p1_total", -1)):
        raise RuntimeError(f"P0/P1 count changed during migration: {total_items} != {manifest.get('p0p1_total')}")
    report = {
        "ok": True,
        "schema_version": 2,
        "migration": "p0p1-mission-v2-prepare",
        "legacy_manifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
        "platform_capabilities": {
            "path": str(platform_capabilities_path),
            "sha256": sha256_file(platform_capabilities_path),
        },
        "authorization_record": {
            "path": str(authorization_record_path),
            "sha256": sha256_file(authorization_record_path),
        },
        "total_items": total_items,
        "campaigns": campaigns,
        "queue_ordinals": all_ordinals,
        "created_at": utc_now(),
    }
    report_path = output_root / "migration-prepare-report.json"
    atomic_write_json(report_path, report)
    report["report"] = str(report_path)
    report["report_sha256"] = sha256_file(report_path)
    return report


def _same_path(left: str | Path, right: str | Path) -> bool:
    return Path(left).resolve() == Path(right).resolve()


def _verify_bound_file(kb_root: Path, path: str | Path, expected_sha256: str, label: str) -> Path:
    source = Path(path)
    if not source.is_absolute():
        source = kb_root / source
    source = ensure_inside(source, kb_root)
    if not source.is_file() or sha256_file(source) != expected_sha256:
        raise RuntimeError(f"{label} path/hash binding is invalid")
    return source


def _phase_for_status(status: str) -> str | None:
    if status == "queued":
        return "executor"
    if status in {
        "applied_pending_audit",
        "no_change_pending_audit",
        "reapplied_pending_audit",
        "legacy_no_change_pending_audit",
        "source_mapped_pending_audit",
    }:
        return "auditor"
    if status == "repair_queued":
        return "repair"
    return None


def _validate_v2_cutover_ready(
    *,
    kb_root: Path,
    campaign_id: str,
    prepare_report: dict[str, Any],
    campaign_report: dict[str, Any],
    legacy_manifest: dict[str, Any],
) -> dict[str, Any]:
    ledger_path = _verify_bound_file(
        kb_root,
        campaign_report["ledger"],
        str(campaign_report["ledger_sha256"]),
        f"{campaign_id} ledger",
    )
    ledger = load_object(ledger_path, f"{campaign_id} v2 ledger")
    if ledger.get("schema_version") != 2 or ledger.get("campaign_id") != campaign_id:
        raise RuntimeError(f"{campaign_id} v2 ledger identity is invalid")
    legacy_ref = ledger.get("legacy_ledger")
    if not isinstance(legacy_ref, dict):
        raise RuntimeError(f"{campaign_id} v2 ledger lacks legacy binding")
    legacy_path = _verify_bound_file(
        kb_root,
        legacy_ref.get("path", ""),
        str(legacy_ref.get("sha256", "")),
        f"{campaign_id} legacy ledger",
    )
    manifest_entries = [
        entry
        for entry in legacy_manifest.get("volumes", [])
        if isinstance(entry, dict)
        and entry.get("volume") == campaign_report.get("volume")
        and _same_path(
            Path(str(entry.get("ledger", "")))
            if Path(str(entry.get("ledger", ""))).is_absolute()
            else kb_root / Path(str(entry.get("ledger", ""))),
            legacy_path,
        )
    ]
    if (
        len(manifest_entries) != 1
        or manifest_entries[0].get("queue_sha256") != legacy_ref.get("sha256")
        or ledger.get("legacy_queue_sha256") != legacy_ref.get("sha256")
    ):
        raise RuntimeError(f"{campaign_id} prepare report/manifest/legacy ledger binding is invalid")

    grant_path = _verify_bound_file(
        kb_root,
        campaign_report["authorization_grant"],
        str(campaign_report["authorization_grant_sha256"]),
        f"{campaign_id} authorization grant",
    )
    grant = load_object(grant_path, f"{campaign_id} authorization grant")
    if (
        grant.get("schema_version") != 2
        or grant.get("campaign_id") != campaign_id
        or grant.get("ledger_sha256") != campaign_report["ledger_sha256"]
    ):
        raise RuntimeError(f"{campaign_id} grant/ledger binding is invalid")
    authorization_ref = prepare_report.get("authorization_record")
    if not isinstance(authorization_ref, dict):
        raise RuntimeError("prepare report lacks authorization record binding")
    authorization_path = _verify_bound_file(
        kb_root,
        authorization_ref.get("path", ""),
        str(authorization_ref.get("sha256", "")),
        "authorization record",
    )
    authorization = load_object(authorization_path, "authorization record")
    if (
        authorization.get("schema_version") != 1
        or not _same_path(grant.get("authorization_record_path", ""), authorization_path)
        or grant.get("authorization_record_sha256") != authorization_ref.get("sha256")
        or any(
            grant.get(field) != authorization.get(field)
            for field in ("authorized_by", "authorization_basis", "issued_at", "expires_at")
        )
        or grant.get("destructive") is not False
    ):
        raise RuntimeError(f"{campaign_id} authorization record binding is invalid")
    if parse_utc(grant.get("expires_at"), "grant.expires_at") <= dt.datetime.now(dt.timezone.utc):
        raise RuntimeError(f"{campaign_id} authorization grant is expired")

    platform_ref = prepare_report.get("platform_capabilities")
    if not isinstance(platform_ref, dict):
        raise RuntimeError("prepare report lacks platform binding")
    platform_path = _verify_bound_file(
        kb_root,
        platform_ref.get("path", ""),
        str(platform_ref.get("sha256", "")),
        "platform capabilities",
    )
    platform = load_object(platform_path, "platform capabilities")
    try:
        platform_adapter.validate_platform_capabilities(platform)
    except platform_adapter.PlatformGateError as exc:
        raise RuntimeError(f"platform capabilities are not currently valid: {exc.code}") from exc

    state_path = kb_root / f"skills/_ops/runtime/state/agent_missions/{campaign_id}/campaign.json"
    state_path = ensure_inside(state_path, kb_root)
    state = load_object(state_path, f"v2 campaign state {campaign_id}")
    if state.get("schema_version") != 2 or state.get("campaign_id") != campaign_id:
        raise RuntimeError(f"v2 campaign is not initialized: {campaign_id}")
    if not any(isinstance(event, dict) and event.get("event") == "initialized" for event in state.get("events", [])):
        raise RuntimeError(f"v2 campaign lacks initialized event: {campaign_id}")
    if state.get("active_mission") is not None:
        raise RuntimeError(f"v2 campaign has an active mission: {campaign_id}")
    bindings = (
        ("ledger_path", ledger_path, "ledger_sha256", campaign_report["ledger_sha256"]),
        (
            "authorization_grant_path",
            grant_path,
            "authorization_grant_sha256",
            campaign_report["authorization_grant_sha256"],
        ),
        (
            "platform_capabilities_path",
            platform_path,
            "platform_capabilities_sha256",
            platform_ref["sha256"],
        ),
    )
    for path_field, expected_path, sha_field, expected_sha in bindings:
        if not _same_path(state.get(path_field, ""), expected_path) or state.get(sha_field) != expected_sha:
            raise RuntimeError(f"{campaign_id} v2 state binding mismatch: {path_field}")
    if state.get("profile") != ledger.get("profile") or state.get("scope_key") != ledger.get("scope_key"):
        raise RuntimeError(f"{campaign_id} v2 state ledger metadata mismatch")

    ledger_items = {str(item.get("question_id")): item for item in ledger.get("items", []) if isinstance(item, dict)}
    state_items = state.get("items")
    if not isinstance(state_items, dict) or set(state_items) != set(ledger_items):
        raise RuntimeError(f"{campaign_id} v2 state item set does not match ledger")
    for question_id, item in state_items.items():
        source = ledger_items[question_id]
        for field in ("queue_ordinal", "target_ref", "source_ref", "source_ready", "capability"):
            if item.get(field) != source.get(field):
                raise RuntimeError(f"{campaign_id} state/ledger item binding mismatch: {question_id}.{field}")
        target_ref = item.get("target_ref")
        if not isinstance(target_ref, dict):
            raise RuntimeError(f"{campaign_id} target ref is invalid: {question_id}")
        target = ensure_inside(kb_root / normalize_relative(str(target_ref.get("relative_path", ""))), kb_root)
        if (
            not target.is_file()
            or target.stat().st_size != target_ref.get("size_bytes")
            or sha256_file(target) != target_ref.get("sha256")
        ):
            raise RuntimeError(f"{campaign_id} target hash drift: {question_id}")
        source_ref = item.get("source_ref")
        if item.get("source_ready") and isinstance(source_ref, dict):
            source_path = ensure_inside(kb_root / normalize_relative(str(source_ref.get("relative_path", ""))), kb_root)
            if (
                not source_path.is_file()
                or source_path.stat().st_size != source_ref.get("size_bytes")
                or sha256_file(source_path) != source_ref.get("sha256")
            ):
                raise RuntimeError(f"{campaign_id} source hash drift: {question_id}")
    selected = orchestrator.select_batch(state)
    if not selected:
        raise RuntimeError(f"v2 campaign has no next/claimable mission: {campaign_id}")
    selected_items, phase = selected
    if state.get("active_mission") is not None:
        raise RuntimeError(f"v2 campaign has an active mission: {campaign_id}")
    attempts = selected_items[0].get("dispatch_attempts")
    if (
        not isinstance(attempts, dict)
        or not isinstance(attempts.get(phase), int)
        or attempts[phase] >= 2
    ):
        raise RuntimeError(f"v2 campaign dispatch attempts are exhausted: {campaign_id}")
    next_claimable = {
        "question_id": selected_items[0]["question_id"],
        "phase": phase,
        "queue_ordinal": selected_items[0]["queue_ordinal"],
    }
    return {
        "state_path": state_path,
        "state": state,
        "ledger_path": ledger_path,
        "grant_path": grant_path,
        "platform_path": platform_path,
        "next_claimable": next_claimable,
        "claim_batch": {
            "phase": phase,
            "question_ids": [item["question_id"] for item in selected_items],
            "queue_ordinals": [item["queue_ordinal"] for item in selected_items],
            "dispatch_attempt_no": attempts[phase] + 1,
        },
    }


def cutover_failpoint(_name: str) -> None:
    """Test hook for simulating a process-level interruption at a durable boundary."""


def _snapshot_to_backup(path: Path, backup_path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "sha256": None, "size_bytes": 0, "backup": None}
    if not path.is_file():
        raise RuntimeError(f"transaction path is not a file: {path}")
    atomic_write_bytes(backup_path, path.read_bytes())
    return {
        "exists": True,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "backup": file_ref(backup_path),
    }


def _stage_json_file(
    *,
    path: Path,
    role: str,
    backup_root: Path,
    slug: str,
    post_value: dict[str, Any] | None,
    campaign_id: str | None = None,
) -> dict[str, Any]:
    pre = _snapshot_to_backup(path, backup_root / "pre" / f"{slug}.json")
    if post_value is None:
        post = {"exists": False, "sha256": None, "size_bytes": 0, "backup": None}
    else:
        post_backup = backup_root / "post" / f"{slug}.json"
        atomic_write_json(post_backup, post_value)
        post = {
            "exists": True,
            "sha256": sha256_file(post_backup),
            "size_bytes": post_backup.stat().st_size,
            "backup": file_ref(post_backup),
        }
    result: dict[str, Any] = {
        "role": role,
        "path": str(path),
        "pre": pre,
        "post": post,
    }
    if campaign_id:
        result["campaign_id"] = campaign_id
    return result


def _snapshot_matches(path: Path, snapshot: dict[str, Any]) -> bool:
    exists = snapshot.get("exists") is True
    if not exists:
        return not path.exists()
    return (
        path.is_file()
        and path.stat().st_size == snapshot.get("size_bytes")
        and sha256_file(path) == snapshot.get("sha256")
    )


def _install_snapshot(kb_root: Path, entry: dict[str, Any], side: str) -> None:
    path = ensure_inside(Path(str(entry["path"])), kb_root)
    snapshot = entry[side]
    if _snapshot_matches(path, snapshot):
        return
    if snapshot.get("exists") is not True:
        path.unlink(missing_ok=True)
    else:
        backup = snapshot.get("backup")
        if not isinstance(backup, dict):
            raise RuntimeError(f"transaction snapshot lacks backup: {path}")
        backup_path = _verify_bound_file(
            kb_root,
            backup.get("path", ""),
            str(backup.get("sha256", "")),
            f"transaction {side} backup",
        )
        if backup_path.stat().st_size != snapshot.get("size_bytes"):
            raise RuntimeError(f"transaction backup size mismatch: {path}")
        atomic_write_bytes(path, backup_path.read_bytes())
    if not _snapshot_matches(path, snapshot):
        raise RuntimeError(f"transaction {side} restore verification failed: {path}")


def _restore_transaction_side(kb_root: Path, journal: dict[str, Any], side: str) -> None:
    files = journal.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError("cutover journal has no managed files")
    for entry in files:
        if not isinstance(entry, dict) or side not in entry or "pre" not in entry or "post" not in entry:
            raise RuntimeError("cutover journal file entry is invalid")
        path = ensure_inside(Path(str(entry.get("path", ""))), kb_root)
        if not (_snapshot_matches(path, entry["pre"]) or _snapshot_matches(path, entry["post"])):
            raise RuntimeError(f"cutover recovery found external file drift: {path}")
    for entry in files:
        _install_snapshot(kb_root, entry, side)


def _journal_marker_path(kb_root: Path, journal: dict[str, Any], field: str) -> Path:
    value = journal.get(field)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"cutover journal lacks {field}")
    return ensure_inside(Path(value), kb_root)


def _verify_journal_marker(
    *,
    kb_root: Path,
    journal_path: Path,
    journal: dict[str, Any],
    marker_path: Path,
    expected_kind: str,
) -> dict[str, Any]:
    marker = load_object(marker_path, expected_kind)
    journal_ref = marker.get("journal")
    if (
        marker.get("schema_version") != 2
        or marker.get("kind") != expected_kind
        or marker.get("transaction_id") != journal.get("transaction_id")
        or not isinstance(journal_ref, dict)
        or not _same_path(journal_ref.get("path", ""), journal_path)
        or journal_ref.get("sha256") != sha256_file(journal_path)
    ):
        raise RuntimeError(f"invalid {expected_kind}")
    return marker


def _journal_paths(receipt_root: Path) -> list[Path]:
    journal_root = receipt_root / "journals"
    if not journal_root.is_dir():
        return []
    return sorted(journal_root.glob("*.cutover-journal.json"))


def _recover_cutover_transactions(kb_root: Path, receipt_root: Path) -> list[dict[str, Any]]:
    recovered: list[dict[str, Any]] = []
    for journal_path in _journal_paths(receipt_root):
        journal = load_object(journal_path, "legacy cutover journal")
        if journal.get("schema_version") != 2 or journal.get("kind") != "legacy_cutover_transaction_journal":
            raise RuntimeError(f"invalid cutover journal: {journal_path}")
        commit_marker = _journal_marker_path(kb_root, journal, "commit_marker_path")
        completion_marker = _journal_marker_path(kb_root, journal, "completion_marker_path")
        rollback_marker = _journal_marker_path(kb_root, journal, "rollback_marker_path")
        if rollback_marker.is_file():
            _verify_journal_marker(
                kb_root=kb_root,
                journal_path=journal_path,
                journal=journal,
                marker_path=rollback_marker,
                expected_kind="legacy_cutover_rollback_marker",
            )
            continue
        if completion_marker.is_file():
            _verify_journal_marker(
                kb_root=kb_root,
                journal_path=journal_path,
                journal=journal,
                marker_path=completion_marker,
                expected_kind="legacy_cutover_completion_marker",
            )
            if not commit_marker.is_file():
                raise RuntimeError("cutover completion marker lacks commit decision")
            _verify_journal_marker(
                kb_root=kb_root,
                journal_path=journal_path,
                journal=journal,
                marker_path=commit_marker,
                expected_kind="legacy_cutover_commit_marker",
            )
            continue
        if commit_marker.is_file():
            _verify_journal_marker(
                kb_root=kb_root,
                journal_path=journal_path,
                journal=journal,
                marker_path=commit_marker,
                expected_kind="legacy_cutover_commit_marker",
            )
            _restore_transaction_side(kb_root, journal, "post")
            atomic_write_json(
                completion_marker,
                {
                    "schema_version": 2,
                    "kind": "legacy_cutover_completion_marker",
                    "transaction_id": journal["transaction_id"],
                    "journal": file_ref(journal_path),
                    "completed_at": utc_now(),
                },
            )
            recovered.append({"transaction_id": journal["transaction_id"], "decision": "commit", "journal": journal})
            continue
        _restore_transaction_side(kb_root, journal, "pre")
        rollback = {
            "schema_version": 2,
            "kind": "legacy_cutover_rollback_marker",
            "transaction_id": journal["transaction_id"],
            "journal": file_ref(journal_path),
            "rolled_back_at": utc_now(),
        }
        atomic_write_json(rollback_marker, rollback)
        recovered.append({"transaction_id": journal["transaction_id"], "decision": "rollback", "journal": journal})
    return recovered


def _clear_proven_dead_mutex(lock_path: Path) -> None:
    record = orchestrator._read_lock_record(lock_path)
    if record is None or orchestrator._pid_liveness(record[3]) is not False:
        return
    try:
        if lock_path.read_bytes() == record[0]:
            lock_path.unlink()
    except FileNotFoundError:
        pass


def _pending_journal_campaigns(receipt_root: Path) -> set[str]:
    campaign_ids: set[str] = set()
    for journal_path in _journal_paths(receipt_root):
        journal = load_object(journal_path, "legacy cutover journal")
        commit_marker = Path(str(journal.get("commit_marker_path", "")))
        completion_marker = Path(str(journal.get("completion_marker_path", "")))
        rollback_marker = Path(str(journal.get("rollback_marker_path", "")))
        if rollback_marker.is_file() or completion_marker.is_file():
            continue
        values = journal.get("campaign_ids")
        if isinstance(values, list):
            campaign_ids.update(str(value) for value in values)
        if commit_marker.is_file():
            continue
    return campaign_ids


@contextmanager
def _cutover_lock_guard(
    *,
    kb_root: Path,
    receipt_root: Path,
    campaign_ids: list[str],
) -> Iterator[Path]:
    guarded_campaigns = sorted(set(campaign_ids) | _pending_journal_campaigns(receipt_root))
    mission_root = orchestrator.missions_root(kb_root)
    for campaign_id in guarded_campaigns:
        _clear_proven_dead_mutex(orchestrator.campaign_dir(kb_root, campaign_id) / ".campaign.lock")
    _clear_proven_dead_mutex(mission_root / ".source-locks.lock")
    with ExitStack() as stack:
        for campaign_id in guarded_campaigns:
            stack.enter_context(orchestrator.campaign_lock(orchestrator.campaign_dir(kb_root, campaign_id)))
        shared_lock_path = stack.enter_context(orchestrator.source_lock_guard(kb_root))
        yield shared_lock_path


def _claim_equivalent_lock_preflight(
    *,
    kb_root: Path,
    transaction_id: str,
    prepared: list[dict[str, Any]],
    shared_lock_ledger: dict[str, Any],
) -> list[dict[str, Any]]:
    if shared_lock_ledger.get("schema_version") != 2 or not isinstance(shared_lock_ledger.get("locks"), dict):
        raise RuntimeError("shared v2 mission-lock ledger is invalid")
    virtual_locks = copy.deepcopy(shared_lock_ledger["locks"])
    worker_registry = copy.deepcopy(shared_lock_ledger.get("worker_registry", {}))
    if not isinstance(worker_registry, dict):
        raise RuntimeError("shared v2 mission worker registry is invalid")
    reservations: list[dict[str, Any]] = []

    def reserve(key: str, value: dict[str, Any], conflict_code: str) -> None:
        existing = virtual_locks.get(key)
        if existing and not orchestrator.lock_owned_by(existing, orchestrator.lock_owner_identity(value)):
            raise RuntimeError(
                f"{conflict_code}: lock {key} is owned by "
                f"{existing.get('campaign_id')}/{existing.get('task_id')}/{existing.get('worker_instance_id')}"
            )
        virtual_locks[key] = value

    for index, entry in enumerate(prepared):
        campaign_id = entry["campaign_id"]
        state = entry["v2_state"]
        claim_batch = entry["claim_batch"]
        selected = [state["items"][question_id] for question_id in claim_batch["question_ids"]]
        token = hashlib.sha256(f"{transaction_id}:{campaign_id}:{index}".encode("utf-8")).hexdigest()[:24]
        owner = {
            "campaign_id": campaign_id,
            "task_id": f"cutover-{token}",
            "lease_id": f"lease-cutover-{token}",
            "worker_instance_id": f"legacy-cutover-worker-{token}",
        }
        prior_workers = {
            history.get("worker_instance_id")
            for item in state["items"].values()
            for history in item.get("worker_history", [])
            if isinstance(history, dict)
        }
        if owner["worker_instance_id"] in prior_workers:
            raise RuntimeError(f"worker_reuse_forbidden: {owner['worker_instance_id']}")
        existing_worker = worker_registry.get(owner["worker_instance_id"])
        if existing_worker is not None and not orchestrator.lock_owned_by(existing_worker, owner):
            raise RuntimeError(
                f"worker_reuse_forbidden: {owner['worker_instance_id']} is registered to "
                f"{existing_worker.get('campaign_id')}/{existing_worker.get('task_id')}"
            )
        lock_value = {
            **owner,
            "scope_key": state["scope_key"],
            "claimed_at": utc_now(),
        }
        scope_key = f"scope:{state['scope_key']}"
        reserve(scope_key, lock_value, "scope_lock_conflict")
        source_sha256 = (selected[0].get("source_ref") or {}).get("sha256")
        if source_sha256:
            orchestrator.ensure_sha(source_sha256, "source_sha256")
            reserve(source_sha256, lock_value, "source_lock_conflict")
        target_locks: list[dict[str, Any]] = []
        for item in selected:
            orchestrator.verify_bound_path_ref(kb_root, item["target_ref"], kind="target")
            if item.get("source_ready"):
                orchestrator.verify_bound_path_ref(kb_root, item["source_ref"], kind="source")
            target_ref = item["target_ref"]
            target_key = orchestrator.target_lock_key(kb_root, target_ref)
            existing_target = virtual_locks.get(target_key)
            lifecycle_takeover = (
                isinstance(existing_target, dict)
                and existing_target.get("lifecycle_retained") is True
                and existing_target.get("campaign_id") == campaign_id
                and existing_target.get("question_id") == item["question_id"]
            )
            target_value = {
                **lock_value,
                "question_id": item["question_id"],
                "path_id": target_ref["path_id"],
                "relative_path": target_ref["relative_path"],
                "canonical_path": orchestrator.canonical_physical_path(
                    orchestrator.resolve_bound_path(kb_root, target_ref, kind="target")
                ),
            }
            if lifecycle_takeover:
                virtual_locks[target_key] = target_value
            else:
                reserve(target_key, target_value, "target_lock_conflict")
            target_locks.append(
                {
                    "key": target_key,
                    "path_id": target_ref["path_id"],
                    "relative_path": target_ref["relative_path"],
                    "canonical_path": target_value["canonical_path"],
                }
            )
        worker_registry.setdefault(
            owner["worker_instance_id"],
            {**owner, "claimed_at": lock_value["claimed_at"]},
        )
        reservations.append(
            {
                "campaign_id": campaign_id,
                "phase": claim_batch["phase"],
                "question_ids": claim_batch["question_ids"],
                "dispatch_attempt_no": claim_batch["dispatch_attempt_no"],
                "owner": owner,
                "worker_fresh": True,
                "scope_lock_key": scope_key,
                "source_lock_key": source_sha256,
                "target_locks": target_locks,
            }
        )
    return reservations


def _transaction_result(journal: dict[str, Any], *, recovered: bool = False) -> dict[str, Any]:
    journal_path = Path(str(journal["journal_path"]))
    return {
        "ok": True,
        "released_campaigns": journal["campaign_ids"],
        "receipts": journal["result_receipts"],
        "transaction": {
            "transaction_id": journal["transaction_id"],
            "journal": file_ref(journal_path),
            "commit_marker": journal["commit_marker_path"],
            "completion_marker": journal["completion_marker_path"],
            "recovered": recovered,
        },
    }


def _matching_committed_transaction(
    *,
    receipt_root: Path,
    campaign_ids: list[str],
    prepare_report_path: Path,
    source_locks_path: Path,
    worker_close_evidence_path: Path,
) -> dict[str, Any] | None:
    for journal_path in reversed(_journal_paths(receipt_root)):
        journal = load_object(journal_path, "legacy cutover journal")
        marker_path = Path(str(journal.get("commit_marker_path", "")))
        completion_path = Path(str(journal.get("completion_marker_path", "")))
        inputs = journal.get("inputs")
        if (
            marker_path.is_file()
            and completion_path.is_file()
            and journal.get("campaign_ids") == campaign_ids
            and isinstance(inputs, dict)
            and _same_path(inputs.get("prepare_report", {}).get("path", ""), prepare_report_path)
            and inputs.get("prepare_report", {}).get("sha256") == sha256_file(prepare_report_path)
            and _same_path(inputs.get("source_locks_path", ""), source_locks_path)
            and _same_path(inputs.get("worker_close_evidence", {}).get("path", ""), worker_close_evidence_path)
            and inputs.get("worker_close_evidence", {}).get("sha256") == sha256_file(worker_close_evidence_path)
        ):
            return journal
    return None


def _cleanup_rolled_back_transaction(kb_root: Path, journal_path: Path, journal: dict[str, Any]) -> None:
    rollback_marker = _journal_marker_path(kb_root, journal, "rollback_marker_path")
    completion_marker = _journal_marker_path(kb_root, journal, "completion_marker_path")
    backup_root = ensure_inside(Path(str(journal["backup_root"])), kb_root)
    rollback_marker.unlink(missing_ok=True)
    completion_marker.unlink(missing_ok=True)
    journal_path.unlink(missing_ok=True)
    shutil.rmtree(backup_root, ignore_errors=True)


def release_legacy_campaigns(
    *,
    kb_root: Path,
    campaign_ids: list[str],
    prepare_report_path: Path,
    source_locks_path: Path,
    worker_close_evidence_path: Path,
    receipt_root: Path,
) -> dict[str, Any]:
    kb_root = kb_root.resolve()
    prepare_report_path = ensure_inside(prepare_report_path, kb_root)
    source_locks_path = ensure_inside(source_locks_path, kb_root)
    worker_close_evidence_path = ensure_inside(worker_close_evidence_path, kb_root)
    receipt_root = ensure_inside(receipt_root, kb_root)
    if not campaign_ids or len(campaign_ids) != len(set(campaign_ids)):
        raise ValueError("campaign_ids must be non-empty and unique")
    campaign_ids = sorted(campaign_ids)
    receipt_root.mkdir(parents=True, exist_ok=True)
    with _cutover_lock_guard(kb_root=kb_root, receipt_root=receipt_root, campaign_ids=campaign_ids) as shared_lock_path:
        _recover_cutover_transactions(kb_root, receipt_root)
        committed = _matching_committed_transaction(
            receipt_root=receipt_root,
            campaign_ids=campaign_ids,
            prepare_report_path=prepare_report_path,
            source_locks_path=source_locks_path,
            worker_close_evidence_path=worker_close_evidence_path,
        )
        if committed is not None:
            _restore_transaction_side(kb_root, committed, "post")
            return _transaction_result(committed, recovered=True)

        prepare_report = load_object(prepare_report_path, "migration prepare report")
        if (
            prepare_report.get("ok") is not True
            or prepare_report.get("schema_version") != 2
            or prepare_report.get("migration") != "p0p1-mission-v2-prepare"
        ):
            raise ValueError("migration prepare report is invalid")
        legacy_manifest_ref = prepare_report.get("legacy_manifest")
        if not isinstance(legacy_manifest_ref, dict):
            raise ValueError("migration prepare report lacks legacy manifest binding")
        legacy_manifest_path = _verify_bound_file(
            kb_root,
            legacy_manifest_ref.get("path", ""),
            str(legacy_manifest_ref.get("sha256", "")),
            "legacy manifest",
        )
        legacy_manifest = load_object(legacy_manifest_path, "legacy manifest")
        if legacy_manifest.get("schema_version") != 1 or not isinstance(legacy_manifest.get("volumes"), list):
            raise ValueError("legacy manifest bound by prepare report is invalid")
        campaign_entries = prepare_report.get("campaigns")
        if not isinstance(campaign_entries, list):
            raise ValueError("migration prepare report campaigns are invalid")
        report_campaigns = {
            str(entry.get("campaign_id")): entry
            for entry in campaign_entries
            if isinstance(entry, dict) and entry.get("campaign_id")
        }
        if len(report_campaigns) != len(campaign_entries):
            raise ValueError("migration prepare report campaign identities are invalid or duplicated")
        if prepare_report.get("total_items") != sum(int(entry.get("item_count", -1)) for entry in campaign_entries):
            raise ValueError("migration prepare report total_items is inconsistent")
        if any(campaign_id not in report_campaigns for campaign_id in campaign_ids):
            raise RuntimeError("prepare report does not cover every requested campaign")
        close_evidence = load_object(worker_close_evidence_path, "worker close evidence")
        closed_runners = set(close_evidence.get("closed_runners") or [])
        legacy_locks = load_object(source_locks_path, "legacy source locks")
        if legacy_locks.get("schema_version") != 1 or not isinstance(legacy_locks.get("locks"), list):
            raise ValueError("legacy source lock ledger is invalid")
        for lock in legacy_locks["locks"]:
            if lock.get("campaign_id") in campaign_ids and lock.get("released_at") is None:
                if lock.get("runner_id") not in closed_runners:
                    raise RuntimeError(f"legacy lock runner close is not verified: {lock.get('runner_id')}")

        prepared: list[dict[str, Any]] = []
        for campaign_id in campaign_ids:
            v2 = _validate_v2_cutover_ready(
                kb_root=kb_root,
                campaign_id=campaign_id,
                prepare_report=prepare_report,
                campaign_report=report_campaigns[campaign_id],
                legacy_manifest=legacy_manifest,
            )
            legacy_state_path = ensure_inside(
                kb_root / f"skills/_ops/runtime/state/role_d_curation_campaigns/{campaign_id}/campaign.json",
                kb_root,
            )
            legacy_state = load_object(legacy_state_path, f"legacy campaign {campaign_id}")
            active_batch_id = legacy_state.get("active_batch_id")
            batch = next(
                (value for value in legacy_state.get("batches", []) if value.get("batch_id") == active_batch_id),
                None,
            )
            runner_id = batch.get("claimed_by_runner") if isinstance(batch, dict) else None
            if runner_id and runner_id not in closed_runners:
                raise RuntimeError(f"worker close is not verified for {campaign_id}: {runner_id}")
            prepared.append(
                {
                    "campaign_id": campaign_id,
                    "legacy_state_path": legacy_state_path,
                    "legacy_state": legacy_state,
                    "legacy_state_before_sha256": sha256_file(legacy_state_path),
                    "v2_state_path": v2["state_path"],
                    "v2_state": v2["state"],
                    "v2_state_before_sha256": sha256_file(v2["state_path"]),
                    "next_claimable": v2["next_claimable"],
                    "claim_batch": v2["claim_batch"],
                    "active_batch_id": active_batch_id,
                    "runner_id": runner_id,
                    "active_work_unit": batch.get("active_work_unit") if isinstance(batch, dict) else None,
                }
            )

        shared_lock_ledger = (
            load_object(shared_lock_path, "shared v2 mission locks")
            if shared_lock_path.is_file()
            else {"schema_version": 2, "locks": {}, "worker_registry": {}}
        )
        transaction_id = f"cutover-{uuid.uuid4().hex}"
        reservations = _claim_equivalent_lock_preflight(
            kb_root=kb_root,
            transaction_id=transaction_id,
            prepared=prepared,
            shared_lock_ledger=shared_lock_ledger,
        )
        for entry, reservation in zip(prepared, reservations, strict=True):
            entry["lock_reservation"] = reservation

        released_at = utc_now()
        legacy_locks_post = copy.deepcopy(legacy_locks)
        for lock in legacy_locks_post["locks"]:
            if lock.get("campaign_id") in campaign_ids and lock.get("released_at") is None:
                lock["released_at"] = released_at
                lock["release_reason"] = "abandoned_legacy_migrated_to_mission_v2"
        legacy_locks_post["updated_at"] = released_at
        for entry in prepared:
            legacy_post = copy.deepcopy(entry["legacy_state"])
            batch = next(
                (value for value in legacy_post.get("batches", []) if value.get("batch_id") == entry["active_batch_id"]),
                None,
            )
            if isinstance(batch, dict):
                batch["status"] = "abandoned_legacy"
                batch["legacy_abandonment"] = {
                    "runner_id": entry["runner_id"],
                    "active_work_unit": entry["active_work_unit"],
                    "abandoned_at": released_at,
                    "reason": "migrated_to_agent_mission_v2",
                }
                batch["active_work_unit"] = None
                batch["claimed_by_runner"] = None
            legacy_post["active_batch_id"] = None
            legacy_post["active_work_unit"] = None
            legacy_post.setdefault("legacy_migration_events", []).append(
                {
                    "status": "abandoned_legacy",
                    "batch_id": entry["active_batch_id"],
                    "runner_id": entry["runner_id"],
                    "worker_close_evidence_sha256": sha256_file(worker_close_evidence_path),
                    "claim_lock_preflight": entry["lock_reservation"],
                    "old_proposals_and_evidence_valid": False,
                    "recorded_at": released_at,
                }
            )
            v2_post = copy.deepcopy(entry["v2_state"])
            v2_post.setdefault("events", []).append(
                {
                    "at": released_at,
                    "event": "legacy_cutover_committed",
                    "transaction_id": transaction_id,
                    "prepare_report_sha256": sha256_file(prepare_report_path),
                    "legacy_state_before_sha256": entry["legacy_state_before_sha256"],
                    "next_claimable": entry["next_claimable"],
                    "claim_lock_preflight": entry["lock_reservation"],
                }
            )
            v2_post["updated_at"] = released_at
            v2_post["state_revision"] = int(v2_post.get("state_revision", 0)) + 1
            entry["legacy_state_post"] = legacy_post
            entry["v2_state_post"] = v2_post

        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_root = receipt_root / "backups" / transaction_id
        journal_path = receipt_root / "journals" / f"{transaction_id}.cutover-journal.json"
        commit_marker_path = receipt_root / "journals" / f"{transaction_id}.commit.json"
        completion_marker_path = receipt_root / "journals" / f"{transaction_id}.complete.json"
        rollback_marker_path = receipt_root / "journals" / f"{transaction_id}.rollback.json"
        if (
            backup_root.exists()
            or journal_path.exists()
            or commit_marker_path.exists()
            or completion_marker_path.exists()
            or rollback_marker_path.exists()
        ):
            raise FileExistsError(f"cutover transaction already exists: {transaction_id}")
        backup_root.mkdir(parents=True, exist_ok=False)
        files: list[dict[str, Any]] = []
        campaign_files: dict[str, dict[str, dict[str, Any]]] = {}
        try:
            for entry in prepared:
                campaign_id = entry["campaign_id"]
                legacy_file = _stage_json_file(
                    path=entry["legacy_state_path"],
                    role="legacy_campaign_state",
                    campaign_id=campaign_id,
                    backup_root=backup_root,
                    slug=f"{campaign_id}.legacy-campaign",
                    post_value=entry["legacy_state_post"],
                )
                v2_file = _stage_json_file(
                    path=entry["v2_state_path"],
                    role="v2_campaign_state",
                    campaign_id=campaign_id,
                    backup_root=backup_root,
                    slug=f"{campaign_id}.v2-campaign",
                    post_value=entry["v2_state_post"],
                )
                files.extend((legacy_file, v2_file))
                campaign_files[campaign_id] = {"legacy": legacy_file, "v2": v2_file}
            legacy_lock_file = _stage_json_file(
                path=source_locks_path,
                role="legacy_source_lock_ledger",
                backup_root=backup_root,
                slug="legacy-source-locks",
                post_value=legacy_locks_post,
            )
            shared_lock_file = _stage_json_file(
                path=shared_lock_path,
                role="v2_shared_mission_lock_ledger",
                backup_root=backup_root,
                slug="v2-shared-mission-locks",
                post_value=shared_lock_ledger if shared_lock_path.is_file() else None,
            )
            files.extend((legacy_lock_file, shared_lock_file))
            receipt_entries: list[dict[str, Any]] = []
            result_receipts: list[dict[str, Any]] = []
            for entry in prepared:
                campaign_id = entry["campaign_id"]
                state_files = campaign_files[campaign_id]
                legacy_pre = state_files["legacy"]["pre"]
                legacy_post = state_files["legacy"]["post"]
                v2_pre = state_files["v2"]["pre"]
                v2_post = state_files["v2"]["post"]
                receipt_path = receipt_root / f"{campaign_id}.{timestamp}.legacy-migration-receipt.json"
                if receipt_path.exists():
                    raise FileExistsError(receipt_path)
                receipt = {
                    "schema_version": 2,
                    "kind": "legacy_migration_receipt",
                    "campaign_id": campaign_id,
                    "legacy_status": "abandoned_legacy",
                    "legacy_batch_id": entry["active_batch_id"],
                    "legacy_runner_id": entry["runner_id"],
                    "legacy_active_work_unit": entry["active_work_unit"],
                    "prepare_report": file_ref(prepare_report_path),
                    "legacy_state_before": {"path": str(entry["legacy_state_path"]), "sha256": legacy_pre["sha256"]},
                    "legacy_state_after": {"path": str(entry["legacy_state_path"]), "sha256": legacy_post["sha256"]},
                    "legacy_state_backup": legacy_pre["backup"],
                    "v2_state_before": {"path": str(entry["v2_state_path"]), "sha256": v2_pre["sha256"]},
                    "v2_state_after": {"path": str(entry["v2_state_path"]), "sha256": v2_post["sha256"]},
                    "v2_state_backup": v2_pre["backup"],
                    "state_before_sha256": legacy_pre["sha256"],
                    "state_after_sha256": legacy_post["sha256"],
                    "state_backup": legacy_pre["backup"]["path"],
                    "state_backup_sha256": legacy_pre["backup"]["sha256"],
                    "source_locks_before": {"path": str(source_locks_path), "sha256": legacy_lock_file["pre"]["sha256"]},
                    "source_locks_after_sha256": legacy_lock_file["post"]["sha256"],
                    "source_locks_backup": legacy_lock_file["pre"]["backup"],
                    "v2_shared_locks_before": shared_lock_file["pre"],
                    "v2_shared_locks_after": shared_lock_file["post"],
                    "claim_equivalent_lock_preflight": entry["lock_reservation"],
                    "worker_close_evidence": file_ref(worker_close_evidence_path),
                    "cutover_transaction": {
                        "transaction_id": transaction_id,
                        "journal_path": str(journal_path),
                        "commit_marker_path": str(commit_marker_path),
                        "completion_marker_path": str(completion_marker_path),
                    },
                    "old_proposals_and_evidence_valid": False,
                    "execution_receipt_fabricated": False,
                    "created_at": released_at,
                }
                receipt_file = _stage_json_file(
                    path=receipt_path,
                    role="legacy_migration_receipt",
                    campaign_id=campaign_id,
                    backup_root=backup_root,
                    slug=f"{campaign_id}.legacy-migration-receipt",
                    post_value=receipt,
                )
                files.append(receipt_file)
                receipt_entries.append(receipt_file)
                result_receipts.append(
                    {
                        "path": str(receipt_path),
                        "sha256": receipt_file["post"]["sha256"],
                        "state_backup": legacy_pre["backup"]["path"],
                        "v2_state_backup": v2_pre["backup"]["path"],
                    }
                )
            journal = {
                "schema_version": 2,
                "kind": "legacy_cutover_transaction_journal",
                "transaction_id": transaction_id,
                "campaign_ids": campaign_ids,
                "journal_path": str(journal_path),
                "backup_root": str(backup_root),
                "commit_marker_path": str(commit_marker_path),
                "completion_marker_path": str(completion_marker_path),
                "rollback_marker_path": str(rollback_marker_path),
                "inputs": {
                    "prepare_report": file_ref(prepare_report_path),
                    "source_locks_path": str(source_locks_path),
                    "source_locks_pre_sha256": legacy_lock_file["pre"]["sha256"],
                    "worker_close_evidence": file_ref(worker_close_evidence_path),
                },
                "claim_equivalent_reservations": reservations,
                "files": files,
                "result_receipts": result_receipts,
                "prepared_at": released_at,
            }
            atomic_write_json(journal_path, journal)
        except Exception:
            shutil.rmtree(backup_root, ignore_errors=True)
            journal_path.unlink(missing_ok=True)
            raise

        try:
            for index, entry in enumerate(prepared):
                state_files = campaign_files[entry["campaign_id"]]
                _install_snapshot(kb_root, state_files["legacy"], "post")
                _install_snapshot(kb_root, state_files["v2"], "post")
                if index == 0:
                    cutover_failpoint("after_first_campaign_write")
            _install_snapshot(kb_root, legacy_lock_file, "post")
            _install_snapshot(kb_root, shared_lock_file, "post")
            for receipt_entry in receipt_entries:
                _install_snapshot(kb_root, receipt_entry, "post")
            cutover_failpoint("before_commit_decision")
            commit_marker = {
                "schema_version": 2,
                "kind": "legacy_cutover_commit_marker",
                "transaction_id": transaction_id,
                "journal": file_ref(journal_path),
                "committed_at": utc_now(),
            }
            atomic_write_json(commit_marker_path, commit_marker)
            cutover_failpoint("after_commit_decision")
            atomic_write_json(
                completion_marker_path,
                {
                    "schema_version": 2,
                    "kind": "legacy_cutover_completion_marker",
                    "transaction_id": transaction_id,
                    "journal": file_ref(journal_path),
                    "completed_at": utc_now(),
                },
            )
        except Exception:
            if commit_marker_path.is_file():
                _verify_journal_marker(
                    kb_root=kb_root,
                    journal_path=journal_path,
                    journal=journal,
                    marker_path=commit_marker_path,
                    expected_kind="legacy_cutover_commit_marker",
                )
                _restore_transaction_side(kb_root, journal, "post")
                atomic_write_json(
                    completion_marker_path,
                    {
                        "schema_version": 2,
                        "kind": "legacy_cutover_completion_marker",
                        "transaction_id": transaction_id,
                        "journal": file_ref(journal_path),
                        "completed_at": utc_now(),
                    },
                )
                return _transaction_result(journal, recovered=True)
            _restore_transaction_side(kb_root, journal, "pre")
            rollback = {
                "schema_version": 2,
                "kind": "legacy_cutover_rollback_marker",
                "transaction_id": transaction_id,
                "journal": file_ref(journal_path),
                "rolled_back_at": utc_now(),
            }
            atomic_write_json(rollback_marker_path, rollback)
            _cleanup_rolled_back_transaction(kb_root, journal_path, journal)
            raise
        return _transaction_result(journal)


def prepare_expired_active_canary_cutover(
    *,
    kb_root: Path,
    campaign_id: str,
    canary_mode: str,
    output_path: Path,
    apply_receipt_paths: list[Path] | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Validate an expired canary and emit migration-only evidence without mutating state."""
    if canary_mode not in {"optics", "thermal"}:
        raise ValueError("canary_mode must be optics or thermal")
    kb_root = kb_root.resolve()
    output_path = ensure_inside(output_path, kb_root)
    state_path = ensure_inside(
        kb_root / f"skills/_ops/runtime/state/agent_missions/{campaign_id}/campaign.json",
        kb_root,
    )
    state_before = state_path.read_bytes()
    state = load_object(state_path, f"v2 canary state {campaign_id}")
    active = state.get("active_mission")
    if state.get("schema_version") != 2 or state.get("campaign_id") != campaign_id or not isinstance(active, dict):
        raise RuntimeError("canary requires an initialized v2 campaign with an active mission")
    if active.get("phase") != "executor" or active.get("dispatch_attempt_no") != 1:
        raise RuntimeError("canary cutover only accepts expired executor attempt1")
    current_time = now or dt.datetime.now(dt.timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    lease_seconds = active.get("lease_seconds")
    if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or lease_seconds < 0:
        raise RuntimeError("active canary lease is invalid")
    lease_expires_at = parse_utc(active.get("claimed_at"), "active_mission.claimed_at") + dt.timedelta(
        seconds=lease_seconds
    )
    if current_time.astimezone(dt.timezone.utc) < lease_expires_at:
        raise RuntimeError("active canary lease has not expired")
    mission_card_path = _verify_bound_file(
        kb_root,
        active.get("mission_card_path", ""),
        str(active.get("task_card_sha256", "")),
        "active mission card",
    )
    question_ids = active.get("question_ids")
    if not isinstance(question_ids, list) or not question_ids:
        raise RuntimeError("active canary has no question_ids")
    items = state.get("items")
    if not isinstance(items, dict) or any(question_id not in items for question_id in question_ids):
        raise RuntimeError("active canary question set is invalid")

    current_targets: dict[str, dict[str, Any]] = {}
    for question_id in question_ids:
        target_ref = items[question_id].get("target_ref")
        if not isinstance(target_ref, dict) or target_ref.get("path_id") != "project.root":
            raise RuntimeError(f"invalid target ref for {question_id}")
        target_path = ensure_inside(
            kb_root / normalize_relative(str(target_ref.get("relative_path", ""))),
            kb_root,
        )
        if not target_path.is_file():
            raise RuntimeError(f"target is missing for {question_id}")
        current_targets[question_id] = {
            "path_id": "project.root",
            "relative_path": target_ref["relative_path"],
            "baseline_sha256": target_ref["sha256"],
            "current_sha256": sha256_file(target_path),
            "size_bytes": target_path.stat().st_size,
            "target_lock_key": orchestrator.target_lock_key(kb_root, target_ref),
            "canonical_path": orchestrator.canonical_physical_path(target_path),
        }

    receipt_bindings: list[dict[str, Any]] = []
    supplied_receipts = apply_receipt_paths or []
    if canary_mode == "optics":
        if len(supplied_receipts) != len(question_ids):
            raise RuntimeError("optics canary requires one old apply receipt per active question")
        by_question: dict[str, tuple[Path, dict[str, Any]]] = {}
        for raw_path in supplied_receipts:
            receipt_path = ensure_inside(raw_path, kb_root)
            receipt = load_object(receipt_path, "old apply receipt")
            question_id = str(receipt.get("question_id") or "")
            if question_id in by_question:
                raise RuntimeError(f"duplicate old apply receipt for {question_id}")
            if (
                receipt.get("schema_version") != 2
                or receipt.get("kind") != "execution_receipt"
                or receipt.get("operation") != "apply"
                or receipt.get("status") != "committed"
                or receipt.get("campaign_id") != campaign_id
                or receipt.get("task_id") != active.get("task_id")
                or receipt.get("batch_id") != active.get("batch_id")
                or receipt.get("revision_no") != active.get("revision_no")
                or receipt.get("worker_instance_id") != active.get("worker_instance_id")
                or receipt.get("lease_id") != active.get("lease_id")
                or receipt.get("mission_card_sha256") != active.get("task_card_sha256")
                or receipt.get("authorization_grant_sha256") != state.get("authorization_grant_sha256")
                or receipt.get("dispatch_attempt_no") != 1
            ):
                raise RuntimeError(f"old apply receipt identity is invalid: {receipt_path}")
            by_question[question_id] = (receipt_path, receipt)
        if set(by_question) != set(question_ids):
            raise RuntimeError("old apply receipts do not cover the active optics question set")
        for question_id in question_ids:
            receipt_path, receipt = by_question[question_id]
            target = current_targets[question_id]
            matching_files = [
                entry
                for entry in receipt.get("files", [])
                if isinstance(entry, dict) and entry.get("relative_path") == target["relative_path"]
            ]
            if len(matching_files) != 1:
                raise RuntimeError(f"old apply receipt target binding is invalid: {question_id}")
            file_entry = matching_files[0]
            if (
                file_entry.get("before_sha256") != target["baseline_sha256"]
                or file_entry.get("after_sha256") != target["current_sha256"]
            ):
                raise RuntimeError(f"old apply receipt/current target hash mismatch: {question_id}")
            backup = file_entry.get("backup")
            if not isinstance(backup, dict) or backup.get("kind") != "backup":
                raise RuntimeError(f"old apply receipt lacks a bound backup: {question_id}")
            backup_path = _verify_bound_file(
                kb_root,
                backup.get("path", ""),
                str(backup.get("sha256", "")),
                f"{question_id} old apply backup",
            )
            if sha256_file(backup_path) != target["baseline_sha256"]:
                raise RuntimeError(f"old apply receipt backup/baseline mismatch: {question_id}")
            receipt_bindings.append(
                {
                    "question_id": question_id,
                    "apply_receipt": file_ref(receipt_path),
                    "backup": file_ref(backup_path),
                    "target": target,
                }
            )
        attempt1 = {
            "dispatch_attempt_no": 1,
            "status": "committed_apply_pending_orchestrator_adoption",
            "apply_receipt_count": len(receipt_bindings),
        }
    else:
        if supplied_receipts:
            raise RuntimeError("thermal canary must not bind apply receipts")
        drifted = [
            question_id
            for question_id, target in current_targets.items()
            if target["current_sha256"] != target["baseline_sha256"]
        ]
        if drifted:
            raise RuntimeError(f"thermal canary baseline changed: {', '.join(sorted(drifted))}")
        attempt1 = {
            "dispatch_attempt_no": 1,
            "status": "failed",
            "reason": "worker_lease_expired_without_committed_apply",
        }

    expired_identity = {
        "campaign_id": campaign_id,
        "task_id": active["task_id"],
        "batch_id": active["batch_id"],
        "lease_id": active["lease_id"],
        "worker_instance_id": active["worker_instance_id"],
        "scope_key": active["scope_key"],
        "phase": active["phase"],
        "revision_no": active["revision_no"],
        "dispatch_attempt_no": active["dispatch_attempt_no"],
        "question_ids": question_ids,
        "task_card_sha256": active["task_card_sha256"],
        "mission_card_path": active["mission_card_path"],
        "claimed_at": active["claimed_at"],
        "lease_seconds": lease_seconds,
        "lease_expires_at": lease_expires_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    source_hashes = {
        (items[question_id].get("source_ref") or {}).get("sha256")
        for question_id in question_ids
        if (items[question_id].get("source_ref") or {}).get("sha256")
    }
    if len(source_hashes) > 1:
        raise RuntimeError("active canary spans more than one source lock")
    claim_lock_binding = {
        "owner": orchestrator.lock_owner_identity(active),
        "scope_lock_key": f"scope:{active['scope_key']}",
        "source_lock_key": next(iter(source_hashes), None),
        "target_locks": [
            {
                "question_id": question_id,
                "key": current_targets[question_id]["target_lock_key"],
                "canonical_path": current_targets[question_id]["canonical_path"],
            }
            for question_id in question_ids
        ],
    }
    proposal = {
        "schema_version": 2,
        "kind": "expired_active_v2_canary_cutover_proposal",
        "campaign_id": campaign_id,
        "canary_mode": canary_mode,
        "v2_state": file_ref(state_path),
        "mission_card": file_ref(mission_card_path),
        "authorization_grant_sha256": state.get("authorization_grant_sha256"),
        "platform_capabilities_sha256": state.get("platform_capabilities_sha256"),
        "expired_active_mission": expired_identity,
        "expired_active_mission_sha256": canonical_sha256(expired_identity),
        "claim_lock_binding": claim_lock_binding,
        "claim_lock_binding_sha256": canonical_sha256(claim_lock_binding),
        "attempt1": attempt1,
        "current_targets": [current_targets[question_id] for question_id in question_ids],
        "old_apply_receipt_bindings": receipt_bindings,
        "requested_orchestrator_action": "consume_expired_canary_cutover_proposal",
        "state_mutated": False,
        "execution_receipt_fabricated": False,
        "created_at": utc_now(),
    }
    validation_receipt_path = output_path.with_name(f"{output_path.stem}.validation-receipt.json")
    if output_path.exists() or validation_receipt_path.exists():
        raise FileExistsError("canary migration proposal outputs must not already exist")
    validation_receipt = {
        "schema_version": 2,
        "kind": "expired_active_v2_canary_cutover_validation_receipt",
        "campaign_id": campaign_id,
        "canary_mode": canary_mode,
        "proposal": None,
        "v2_state_before": file_ref(state_path),
        "mission_card": file_ref(mission_card_path),
        "expired_active_mission_sha256": canonical_sha256(expired_identity),
        "claim_lock_binding_sha256": canonical_sha256(claim_lock_binding),
        "authorization_grant_sha256": state.get("authorization_grant_sha256"),
        "platform_capabilities_sha256": state.get("platform_capabilities_sha256"),
        "state_mutated": False,
        "execution_receipt_created": False,
        "execution_receipt_fabricated": False,
        "created_at": utc_now(),
    }
    try:
        atomic_write_json(output_path, proposal)
        validation_receipt["proposal"] = file_ref(output_path)
        atomic_write_json(validation_receipt_path, validation_receipt)
    except Exception:
        output_path.unlink(missing_ok=True)
        validation_receipt_path.unlink(missing_ok=True)
        raise
    if state_path.read_bytes() != state_before:
        output_path.unlink(missing_ok=True)
        validation_receipt_path.unlink(missing_ok=True)
        raise RuntimeError("canary proposal generation unexpectedly mutated v2 state")
    return {
        "ok": True,
        "proposal": file_ref(output_path),
        "validation_receipt": file_ref(validation_receipt_path),
        "state_mutated": False,
        "execution_receipt_created": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-root", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--manifest", required=True)
    prepare.add_argument("--platform-capabilities", required=True)
    prepare.add_argument("--authorization-record", required=True)
    prepare.add_argument("--output-root", required=True)
    prepare.add_argument("--json", action="store_true")
    release = subparsers.add_parser("release-legacy")
    release.add_argument("--campaign", action="append", required=True)
    release.add_argument("--prepare-report", required=True)
    release.add_argument("--source-locks", required=True)
    release.add_argument("--worker-close-evidence", required=True)
    release.add_argument("--receipt-root", required=True)
    release.add_argument("--user-authorized", action="store_true", required=True)
    release.add_argument("--json", action="store_true")
    canary = subparsers.add_parser("prepare-canary-cutover")
    canary.add_argument("--campaign", required=True)
    canary.add_argument("--mode", choices=("optics", "thermal"), required=True)
    canary.add_argument("--apply-receipt", action="append", default=[])
    canary.add_argument("--out", required=True)
    canary.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kb_root = Path(args.kb_root).resolve()
    try:
        if args.command == "prepare":
            payload = prepare_migration(
                kb_root=kb_root,
                manifest_path=Path(args.manifest),
                platform_capabilities_path=Path(args.platform_capabilities),
                authorization_record_path=Path(args.authorization_record),
                output_root=Path(args.output_root),
            )
        elif args.command == "release-legacy":
            payload = release_legacy_campaigns(
                kb_root=kb_root,
                campaign_ids=args.campaign,
                prepare_report_path=Path(args.prepare_report),
                source_locks_path=Path(args.source_locks),
                worker_close_evidence_path=Path(args.worker_close_evidence),
                receipt_root=Path(args.receipt_root),
            )
        else:
            payload = prepare_expired_active_canary_cutover(
                kb_root=kb_root,
                campaign_id=args.campaign,
                canary_mode=args.mode,
                output_path=Path(args.out),
                apply_receipt_paths=[Path(value) for value in args.apply_receipt],
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
