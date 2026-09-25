#!/usr/bin/env python3
"""Validate the shared one-shot import task/result/write contracts.

The validator intentionally has no third-party dependencies.  JSON Schema
files remain the interchange authority; these checks add profile, byte-size,
privacy, and path rules that JSON Schema cannot express by itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


MAX_COMPACT_RESULT_BYTES = 16_384
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ISSUE_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
PROFILE_PATH = Path(__file__).resolve().parents[1] / "config" / "import_batch_profiles.json"
OWNER_SKILLS = frozenset(
    {
        "bemarkdown",
        "chinese-handwriting-formula-transcriber",
        "textbook-import",
        "standards-import",
        "exercise-bank-import",
        "student-data-import",
        "video-transcript-import",
    }
)
AGENT_ROLES = frozenset({"reviewer", "executor", "verifier"})
RESULT_STATUSES = frozenset({"completed", "blocked", "failed", "abstained"})
FORBIDDEN_CONTENT_KEYS = frozenset(
    {
        "body",
        "content",
        "document",
        "document_body",
        "full_text",
        "full_transcript",
        "image",
        "image_bytes",
        "image_base64",
        "log",
        "logs",
        "long_log",
        "ocr_text",
        "student_row",
        "student_rows",
        "database_key",
        "secret",
    }
)


class ContractError(ValueError):
    """A deterministic contract rejection with a stable machine code."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details

    def payload(self) -> dict[str, Any]:
        return {"ok": False, "error": self.code, "message": str(self), **self.details}


def load_json(path: str | Path) -> Any:
    source = Path(path)
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("json_invalid", f"Cannot read valid UTF-8 JSON: {source}") from exc


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ContractError("file_unreadable", f"Cannot hash file: {path}") from exc
    return digest.hexdigest()


def load_profiles(path: str | Path | None = None) -> dict[str, Any]:
    source = Path(path) if path is not None else PROFILE_PATH
    payload = load_json(source)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ContractError("profiles_invalid", "Import batch profiles must use schema_version=1.")
    if payload.get("max_compact_result_bytes") != MAX_COMPACT_RESULT_BYTES:
        raise ContractError(
            "profiles_invalid",
            f"max_compact_result_bytes must be {MAX_COMPACT_RESULT_BYTES}.",
        )
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != OWNER_SKILLS:
        raise ContractError(
            "profiles_incomplete",
            "Profiles must cover exactly the seven canonical import skills.",
            missing=sorted(OWNER_SKILLS - set(profiles or {})),
            unknown=sorted(set(profiles or {}) - OWNER_SKILLS),
        )
    for owner_skill, profile in profiles.items():
        _validate_profile(owner_skill, profile)
    return payload


def get_profile(owner_skill: str, profiles: dict[str, Any] | None = None) -> dict[str, Any]:
    catalog = profiles or load_profiles()
    profile = catalog["profiles"].get(owner_skill)
    if profile is None:
        raise ContractError("owner_skill_unknown", f"Unknown import skill: {owner_skill}")
    return profile


def _validate_profile(owner_skill: str, profile: Any) -> None:
    if not isinstance(profile, dict) or profile.get("owner_skill") != owner_skill:
        raise ContractError("profile_invalid", f"Profile owner mismatch: {owner_skill}")
    roles = profile.get("allowed_agent_roles")
    if not isinstance(roles, list) or not roles or not set(roles).issubset(AGENT_ROLES):
        raise ContractError("profile_invalid", f"Invalid allowed_agent_roles: {owner_skill}")
    if profile.get("agent_lifetime") != "one_shot":
        raise ContractError("profile_invalid", f"Agent lifetime must be one_shot: {owner_skill}")
    for section in ("batch", "concurrency", "privacy", "writer"):
        if not isinstance(profile.get(section), dict):
            raise ContractError("profile_invalid", f"Missing profile section {section}: {owner_skill}")
    batch = profile["batch"]
    writer = profile["writer"]
    for key in ("max_items", "max_pages", "max_unresolved_review_blocks"):
        if not isinstance(batch.get(key), int) or batch[key] < 1:
            raise ContractError("profile_invalid", f"Invalid {key}: {owner_skill}")
    if not isinstance(writer.get("max_artifacts"), int) or writer["max_artifacts"] < 0:
        raise ContractError("profile_invalid", f"Invalid writer max_artifacts: {owner_skill}")
    privacy = profile["privacy"]
    if not isinstance(privacy.get("generic_agents_allowed"), bool):
        raise ContractError("profile_invalid", f"Invalid generic agent policy: {owner_skill}")
    if not isinstance(privacy.get("requires_deidentification"), bool):
        raise ContractError("profile_invalid", f"Invalid deidentification policy: {owner_skill}")
    if not isinstance(privacy.get("allowed_payload_classes"), list) or not privacy["allowed_payload_classes"]:
        raise ContractError("profile_invalid", f"Missing allowed payload classes: {owner_skill}")
    if not isinstance(privacy.get("forbidden_payload_classes"), list):
        raise ContractError("profile_invalid", f"Missing forbidden payload classes: {owner_skill}")
    if set(privacy["allowed_payload_classes"]) & set(privacy["forbidden_payload_classes"]):
        raise ContractError("profile_invalid", f"Payload classes overlap: {owner_skill}")
    mode = writer.get("mode")
    if mode == "canonical_writer_only":
        canonical_writer = writer.get("canonical_writer")
        if not isinstance(canonical_writer, str) or not canonical_writer.startswith(f"skills/import/{owner_skill}/scripts/"):
            raise ContractError("profile_invalid", f"Invalid canonical writer: {owner_skill}")
    elif "canonical_writer" in writer:
        raise ContractError("profile_invalid", f"Unexpected canonical writer: {owner_skill}")


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("object_required", f"{label} must be a JSON object.")
    return value


def _check_keys(value: dict[str, Any], required: Iterable[str], optional: Iterable[str], label: str) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        raise ContractError("required_field_missing", f"{label} is missing required fields.", fields=missing)
    if unknown:
        raise ContractError("unknown_field", f"{label} contains unknown fields.", fields=unknown)


def _check_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ContractError("identifier_invalid", f"Invalid {field}.", field=field)
    return value


def _check_owner_and_role(value: dict[str, Any], profiles: dict[str, Any]) -> dict[str, Any]:
    owner_skill = value.get("owner_skill")
    if not isinstance(owner_skill, str):
        raise ContractError("owner_skill_unknown", "owner_skill must be a canonical import skill.")
    profile = get_profile(owner_skill, profiles)
    role = value.get("agent_role")
    if role not in profile["allowed_agent_roles"]:
        raise ContractError("agent_role_forbidden", f"{role!r} is not allowed for {owner_skill}.")
    return profile


def _check_path_ref(value: Any, label: str, *, require_hash: bool = False) -> dict[str, Any]:
    ref = _require_object(value, label)
    required = {"path_id", "relative_path"}
    optional: set[str] = set()
    if require_hash:
        required.update({"sha256", "size_bytes"})
    _check_keys(ref, required, optional, label)
    path_id = ref.get("path_id")
    relative = ref.get("relative_path")
    if not isinstance(path_id, str) or not path_id or len(path_id) > 64:
        raise ContractError("path_id_invalid", f"Invalid path_id in {label}.")
    if not isinstance(relative, str) or not relative or len(relative) > 1024:
        raise ContractError("relative_path_invalid", f"Invalid relative_path in {label}.")
    normalized = relative.replace("\\", "/")
    pure = PurePosixPath(normalized)
    parts = normalized.split("/")
    if (
        normalized != relative
        or pure.is_absolute()
        or any(part in {"", ".", ".."} or ":" in part for part in parts)
        or normalized.endswith("/")
    ):
        raise ContractError("relative_path_unsafe", f"Unsafe relative_path in {label}.", relative_path=relative)
    if require_hash:
        if not isinstance(ref.get("sha256"), str) or not SHA256_RE.fullmatch(ref["sha256"]):
            raise ContractError("sha256_invalid", f"Invalid sha256 in {label}.")
        if not isinstance(ref.get("size_bytes"), int) or isinstance(ref["size_bytes"], bool) or ref["size_bytes"] < 0:
            raise ContractError("size_invalid", f"Invalid size_bytes in {label}.")
    return ref


def validate_task_card(value: Any, *, profiles: dict[str, Any] | None = None) -> dict[str, Any]:
    card = _require_object(value, "task card")
    _scan_forbidden_content(card)
    _check_keys(
        card,
        {
            "schema_version",
            "task_id",
            "batch_id",
            "attempt_id",
            "attempt_no",
            "worker_instance_id",
            "fork_context",
            "queue_ref",
            "queue_ordinal",
            "owner_skill",
            "agent_role",
            "payload_class",
            "batch_metrics",
            "input_refs",
            "result_path",
        },
        {"retry_of", "privacy", "write_targets", "instructions", "created_at", "expires_at"},
        "task card",
    )
    if card.get("schema_version") != 1:
        raise ContractError("schema_version_invalid", "Task card must use schema_version=1.")
    _check_identifier(card.get("task_id"), "task_id")
    _check_identifier(card.get("batch_id"), "batch_id")
    attempt_id = _check_identifier(card.get("attempt_id"), "attempt_id")
    attempt_no = card.get("attempt_no")
    if attempt_no not in {1, 2} or isinstance(attempt_no, bool):
        raise ContractError("attempt_no_invalid", "attempt_no must be 1 or 2.")
    retry_of = card.get("retry_of")
    if attempt_no == 1 and retry_of is not None:
        raise ContractError("retry_of_forbidden", "attempt 1 must not declare retry_of.")
    if attempt_no == 2:
        retry_id = _check_identifier(retry_of, "retry_of")
        if retry_id == attempt_id:
            raise ContractError("retry_of_invalid", "retry_of must identify the prior attempt.")
    _check_identifier(card.get("worker_instance_id"), "worker_instance_id")
    if card.get("fork_context") is not False:
        raise ContractError("fork_context_forbidden", "One-shot workers require fork_context=false.")
    queue_ref = _check_path_ref(card.get("queue_ref"), "queue_ref", require_hash=True)
    if queue_ref["path_id"] != "workspace.tmp":
        raise ContractError("queue_ref_forbidden", "queue_ref must stay under workspace.tmp.")
    queue_ordinal = card.get("queue_ordinal")
    if not isinstance(queue_ordinal, int) or isinstance(queue_ordinal, bool) or queue_ordinal < 0:
        raise ContractError("queue_ordinal_invalid", "queue_ordinal must be a non-negative integer.")
    catalog = profiles or load_profiles()
    profile = _check_owner_and_role(card, catalog)
    payload_class = card.get("payload_class")
    allowed_payloads = profile["privacy"]["allowed_payload_classes"]
    if payload_class not in allowed_payloads:
        raise ContractError(
            "payload_class_forbidden",
            f"Payload class {payload_class!r} is forbidden for {card['owner_skill']}.",
            allowed=allowed_payloads,
        )
    privacy = card.get("privacy")
    if privacy is not None:
        privacy = _require_object(privacy, "privacy")
        _check_keys(privacy, {"deidentified", "contains_raw_private_payload"}, set(), "privacy")
        if not isinstance(privacy.get("deidentified"), bool) or not isinstance(
            privacy.get("contains_raw_private_payload"), bool
        ):
            raise ContractError("privacy_invalid", "privacy flags must be booleans.")
        if privacy["contains_raw_private_payload"]:
            raise ContractError("raw_private_payload_forbidden", "Raw or private payloads are forbidden.")
    if profile["privacy"]["requires_deidentification"]:
        if privacy is None or privacy["deidentified"] is not True:
            raise ContractError("deidentification_required", "This profile requires explicit deidentification.")
    metrics = _require_object(card.get("batch_metrics"), "batch_metrics")
    metric_limits = {
        "item_count": "max_items",
        "page_count": "max_pages",
        "unresolved_review_blocks": "max_unresolved_review_blocks",
    }
    _check_keys(metrics, metric_limits, set(), "batch_metrics")
    for metric, limit_name in metric_limits.items():
        count = metrics.get(metric)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ContractError("batch_metrics_invalid", f"{metric} must be a non-negative integer.")
        if count > profile["batch"][limit_name]:
            raise ContractError(
                "batch_limit_exceeded",
                f"Task card exceeds {limit_name}.",
                metric=metric,
                value=count,
                limit=profile["batch"][limit_name],
            )
    inputs = card.get("input_refs")
    if not isinstance(inputs, list) or not inputs:
        raise ContractError("input_refs_invalid", "input_refs must be a non-empty array.")
    if len(inputs) > 20:
        raise ContractError("input_refs_invalid", "input_refs must contain at most 20 references.")
    checked_inputs = [_check_path_ref(item, f"input_refs[{index}]", require_hash=True) for index, item in enumerate(inputs)]
    _check_unique_refs(checked_inputs, "input_refs")
    write_targets = card.get("write_targets", [])
    if not isinstance(write_targets, list):
        raise ContractError("write_targets_invalid", "write_targets must be an array.")
    checked_targets = [_check_path_ref(item, f"write_targets[{index}]") for index, item in enumerate(write_targets)]
    _check_unique_refs(checked_targets, "write_targets")
    if card["agent_role"] != "executor" and write_targets:
        raise ContractError("write_targets_forbidden", "Only executor task cards may declare write_targets.")
    if card["agent_role"] == "executor" and profile["writer"]["mode"] == "disabled_for_generic_agents":
        raise ContractError("agent_role_forbidden", "Generic executors are forbidden for this profile.")
    allowed_targets = set(profile["writer"]["allowed_target_path_ids"])
    if any(item["path_id"] not in allowed_targets for item in checked_targets):
        raise ContractError("write_target_forbidden", "A write target path_id is outside the profile boundary.")
    result_path = _check_path_ref(card.get("result_path"), "result_path")
    if result_path["path_id"] != "workspace.tmp":
        raise ContractError("result_path_forbidden", "Compact results must stay under workspace.tmp.")
    instructions = card.get("instructions", [])
    if not isinstance(instructions, list) or len(instructions) > 20 or any(
        not isinstance(item, str) or not item or len(item) > 512 for item in instructions
    ):
        raise ContractError("instructions_invalid", "instructions must contain at most 20 short strings.")
    return card


def _check_unique_refs(refs: list[dict[str, Any]], label: str) -> None:
    keys = [(item["path_id"], item["relative_path"].replace("\\", "/")) for item in refs]
    if len(keys) != len(set(keys)):
        raise ContractError("duplicate_reference", f"{label} contains duplicate path references.")


def _scan_forbidden_content(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in FORBIDDEN_CONTENT_KEYS:
                raise ContractError("embedded_content_forbidden", f"Forbidden embedded-content field at {path}.{key}.")
            _scan_forbidden_content(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_forbidden_content(item, f"{path}[{index}]")
    elif isinstance(value, str):
        lowered = value.lstrip().lower()
        if lowered.startswith("data:image/") or (len(value) > 256 and lowered.startswith("base64:")):
            raise ContractError("embedded_content_forbidden", f"Embedded binary content at {path}.")


def validate_compact_result(
    value: Any,
    *,
    encoded_size: int | None = None,
    profiles: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = _require_object(value, "compact result")
    actual_size = encoded_size if encoded_size is not None else len(canonical_json_bytes(result))
    if actual_size > MAX_COMPACT_RESULT_BYTES:
        raise ContractError(
            "compact_result_too_large",
            f"Compact result is {actual_size} bytes; limit is {MAX_COMPACT_RESULT_BYTES}.",
            size_bytes=actual_size,
            max_bytes=MAX_COMPACT_RESULT_BYTES,
        )
    _scan_forbidden_content(result)
    _check_keys(
        result,
        {
            "schema_version",
            "task_id",
            "batch_id",
            "attempt_id",
            "attempt_no",
            "worker_instance_id",
            "queue_sha256",
            "queue_ordinal",
            "task_card_sha256",
            "owner_skill",
            "agent_role",
            "status",
            "summary",
            "issues",
            "artifacts",
        },
        {"metrics", "next_action"},
        "compact result",
    )
    if result.get("schema_version") != 1:
        raise ContractError("schema_version_invalid", "Compact result must use schema_version=1.")
    _check_identifier(result.get("task_id"), "task_id")
    _check_identifier(result.get("batch_id"), "batch_id")
    _check_identifier(result.get("attempt_id"), "attempt_id")
    if result.get("attempt_no") not in {1, 2} or isinstance(result.get("attempt_no"), bool):
        raise ContractError("attempt_no_invalid", "Result attempt_no must be 1 or 2.")
    _check_identifier(result.get("worker_instance_id"), "worker_instance_id")
    for field in ("queue_sha256", "task_card_sha256"):
        if not isinstance(result.get(field), str) or not SHA256_RE.fullmatch(result[field]):
            raise ContractError("sha256_invalid", f"Invalid {field}.")
    queue_ordinal = result.get("queue_ordinal")
    if not isinstance(queue_ordinal, int) or isinstance(queue_ordinal, bool) or queue_ordinal < 0:
        raise ContractError("queue_ordinal_invalid", "Result queue_ordinal must be non-negative.")
    catalog = profiles or load_profiles()
    profile = _check_owner_and_role(result, catalog)
    if result.get("status") not in RESULT_STATUSES:
        raise ContractError("result_status_invalid", "Invalid compact result status.")
    summary = result.get("summary")
    if not isinstance(summary, str) or not summary or len(summary) > 2000:
        raise ContractError("summary_invalid", "summary must be 1..2000 characters.")
    issues = result.get("issues")
    if not isinstance(issues, list) or len(issues) > profile["max_result_issues"]:
        raise ContractError("result_issue_limit_exceeded", "Compact result exceeds the profile issue limit.")
    for index, issue in enumerate(issues):
        _validate_issue(issue, index)
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) > 20:
        raise ContractError("artifact_refs_invalid", "artifacts must contain at most 20 references.")
    checked = [_validate_artifact_ref(item, f"artifacts[{index}]") for index, item in enumerate(artifacts)]
    _check_unique_refs(checked, "artifacts")
    metrics = result.get("metrics", {})
    if not isinstance(metrics, dict) or len(metrics) > 20 or any(
        not isinstance(key, str)
        or not key
        or not isinstance(number, int)
        or isinstance(number, bool)
        or number < 0
        for key, number in metrics.items()
    ):
        raise ContractError("metrics_invalid", "metrics must contain at most 20 non-negative integer counters.")
    next_action = result.get("next_action")
    if next_action is not None and (not isinstance(next_action, str) or len(next_action) > 512):
        raise ContractError("next_action_invalid", "next_action must be a short string.")
    return result


def validate_result_against_task_card(
    result: Any,
    task_card: Any,
    *,
    task_card_sha256: str | None = None,
    profiles: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a result and bind every one-shot identity field to its task card."""
    catalog = profiles or load_profiles()
    checked_card = validate_task_card(task_card, profiles=catalog)
    checked_result = validate_compact_result(result, profiles=catalog)
    expected = {
        "task_id": checked_card["task_id"],
        "batch_id": checked_card["batch_id"],
        "attempt_id": checked_card["attempt_id"],
        "attempt_no": checked_card["attempt_no"],
        "worker_instance_id": checked_card["worker_instance_id"],
        "queue_sha256": checked_card["queue_ref"]["sha256"],
        "queue_ordinal": checked_card["queue_ordinal"],
        "owner_skill": checked_card["owner_skill"],
        "agent_role": checked_card["agent_role"],
        "task_card_sha256": task_card_sha256 or sha256_bytes(canonical_json_bytes(checked_card)),
    }
    mismatches = {
        field: {"expected": wanted, "actual": checked_result.get(field)}
        for field, wanted in expected.items()
        if checked_result.get(field) != wanted
    }
    if mismatches:
        raise ContractError("result_task_identity_mismatch", "Result identity does not match the task card.", fields=mismatches)
    return checked_result


def _validate_attempt_ledger(value: Any) -> dict[str, Any]:
    ledger = _require_object(value, "attempt ledger")
    _check_keys(ledger, {"schema_version", "task_id", "batch_id", "attempts"}, set(), "attempt ledger")
    if ledger.get("schema_version") != 1:
        raise ContractError("schema_version_invalid", "Attempt ledger must use schema_version=1.")
    _check_identifier(ledger.get("task_id"), "task_id")
    _check_identifier(ledger.get("batch_id"), "batch_id")
    attempts = ledger.get("attempts")
    if not isinstance(attempts, list) or len(attempts) > 2:
        raise ContractError("attempt_ledger_invalid", "attempts must be an array with at most two entries.")
    attempt_ids: set[str] = set()
    attempt_numbers: set[int] = set()
    worker_ids: set[str] = set()
    for index, entry_value in enumerate(attempts):
        entry = _require_object(entry_value, f"attempts[{index}]")
        _check_keys(
            entry,
            {"attempt_id", "attempt_no", "worker_instance_id", "queue_sha256", "queue_ordinal"},
            {"status", "result_sha256"},
            f"attempts[{index}]",
        )
        attempt_id = _check_identifier(entry.get("attempt_id"), "attempt_id")
        worker_id = _check_identifier(entry.get("worker_instance_id"), "worker_instance_id")
        attempt_no = entry.get("attempt_no")
        if attempt_no not in {1, 2} or isinstance(attempt_no, bool):
            raise ContractError("attempt_no_invalid", f"Invalid attempt_no at attempts[{index}].")
        if not isinstance(entry.get("queue_sha256"), str) or not SHA256_RE.fullmatch(entry["queue_sha256"]):
            raise ContractError("sha256_invalid", f"Invalid queue_sha256 at attempts[{index}].")
        ordinal = entry.get("queue_ordinal")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
            raise ContractError("queue_ordinal_invalid", f"Invalid queue_ordinal at attempts[{index}].")
        if attempt_id in attempt_ids or attempt_no in attempt_numbers:
            raise ContractError("ledger_attempt_duplicate", "Attempt ledger repeats an attempt identity.")
        if worker_id in worker_ids:
            raise ContractError("ledger_worker_reused", "Attempt ledger reuses a worker_instance_id.")
        attempt_ids.add(attempt_id)
        attempt_numbers.add(attempt_no)
        worker_ids.add(worker_id)
        status = entry.get("status")
        if status is not None and status not in RESULT_STATUSES:
            raise ContractError("result_status_invalid", f"Invalid status at attempts[{index}].")
        result_sha256 = entry.get("result_sha256")
        if result_sha256 is not None and (not isinstance(result_sha256, str) or not SHA256_RE.fullmatch(result_sha256)):
            raise ContractError("sha256_invalid", f"Invalid result_sha256 at attempts[{index}].")
    if attempt_numbers and attempt_numbers != set(range(1, max(attempt_numbers) + 1)):
        raise ContractError("attempt_ledger_gap", "Attempt ledger must contain a contiguous sequence starting at 1.")
    return ledger


def validate_task_card_against_attempt_ledger(task_card: Any, attempt_ledger: Any) -> dict[str, Any]:
    """Reject repeated attempts/workers and require attempt 2 to retry ledger attempt 1."""
    card = validate_task_card(task_card)
    ledger = _validate_attempt_ledger(attempt_ledger)
    if ledger["task_id"] != card["task_id"] or ledger["batch_id"] != card["batch_id"]:
        raise ContractError("ledger_task_identity_mismatch", "Attempt ledger does not belong to the task card.")
    attempts = ledger["attempts"]
    if any(entry["attempt_id"] == card["attempt_id"] or entry["attempt_no"] == card["attempt_no"] for entry in attempts):
        raise ContractError("attempt_reused", "This attempt has already been recorded.")
    if any(entry["worker_instance_id"] == card["worker_instance_id"] for entry in attempts):
        raise ContractError("worker_instance_reused", "worker_instance_id must be fresh for every attempt.")
    expected_queue = (card["queue_ref"]["sha256"], card["queue_ordinal"])
    if any((entry["queue_sha256"], entry["queue_ordinal"]) != expected_queue for entry in attempts):
        raise ContractError("ledger_queue_identity_mismatch", "Attempt ledger queue identity does not match the task card.")
    if card["attempt_no"] == 1:
        if attempts:
            raise ContractError("attempt_sequence_invalid", "Attempt 1 requires an empty ledger.")
    else:
        if len(attempts) != 1 or attempts[0]["attempt_no"] != 1 or attempts[0]["attempt_id"] != card["retry_of"]:
            raise ContractError("retry_of_ledger_mismatch", "Attempt 2 must retry the sole recorded attempt 1.")
    return card


def validate_result_lifecycle(
    result: Any,
    task_card: Any,
    attempt_ledger: Any,
    *,
    task_card_sha256: str | None = None,
) -> dict[str, Any]:
    validate_task_card_against_attempt_ledger(task_card, attempt_ledger)
    return validate_result_against_task_card(result, task_card, task_card_sha256=task_card_sha256)


def _validate_issue(value: Any, index: int) -> None:
    issue = _require_object(value, f"issues[{index}]")
    _check_keys(issue, {"code", "severity", "message"}, {"evidence_refs"}, f"issues[{index}]")
    if not isinstance(issue.get("code"), str) or not ISSUE_CODE_RE.fullmatch(issue["code"]):
        raise ContractError("issue_invalid", f"Invalid issue code at index {index}.")
    if issue.get("severity") not in {"info", "warning", "error", "blocker"}:
        raise ContractError("issue_invalid", f"Invalid issue severity at index {index}.")
    if not isinstance(issue.get("message"), str) or not issue["message"] or len(issue["message"]) > 1000:
        raise ContractError("issue_invalid", f"Invalid issue message at index {index}.")
    evidence = issue.get("evidence_refs", [])
    if not isinstance(evidence, list) or len(evidence) > 10:
        raise ContractError("issue_invalid", f"Invalid evidence_refs at index {index}.")
    for ref_index, ref in enumerate(evidence):
        _validate_artifact_ref(ref, f"issues[{index}].evidence_refs[{ref_index}]")


def _validate_artifact_ref(value: Any, label: str) -> dict[str, Any]:
    ref = _require_object(value, label)
    _check_keys(ref, {"path_id", "relative_path", "sha256", "size_bytes"}, {"media_type"}, label)
    base = {key: ref[key] for key in ("path_id", "relative_path", "sha256", "size_bytes")}
    _check_path_ref(base, label, require_hash=True)
    media_type = ref.get("media_type")
    if media_type is not None and (not isinstance(media_type, str) or len(media_type) > 128):
        raise ContractError("media_type_invalid", f"Invalid media_type in {label}.")
    return ref


def validate_artifact_bundle(value: Any, *, profiles: dict[str, Any] | None = None) -> dict[str, Any]:
    bundle = _require_object(value, "artifact bundle")
    _check_keys(
        bundle,
        {"schema_version", "task_id", "batch_id", "attempt_id", "owner_skill", "artifacts"},
        set(),
        "artifact bundle",
    )
    if bundle.get("schema_version") != 1:
        raise ContractError("schema_version_invalid", "Artifact bundle must use schema_version=1.")
    _check_identifier(bundle.get("task_id"), "task_id")
    _check_identifier(bundle.get("batch_id"), "batch_id")
    _check_identifier(bundle.get("attempt_id"), "attempt_id")
    catalog = profiles or load_profiles()
    owner_skill = bundle.get("owner_skill")
    profile = get_profile(owner_skill, catalog) if isinstance(owner_skill, str) else None
    if profile is None:
        raise ContractError("owner_skill_unknown", "owner_skill must be a canonical import skill.")
    writer = profile["writer"]
    if writer["mode"] == "canonical_writer_only":
        raise ContractError(
            "canonical_writer_required",
            f"{owner_skill} must use {writer['canonical_writer']} instead of the generic artifact writer.",
            canonical_writer=writer["canonical_writer"],
        )
    if writer["mode"] == "disabled_for_generic_agents" or writer["max_artifacts"] == 0:
        raise ContractError("writer_disabled", f"Shared artifact writer is disabled for {owner_skill}.")
    artifacts = bundle.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ContractError("artifacts_invalid", "Artifact bundle must contain at least one artifact.")
    if len(artifacts) > writer["max_artifacts"]:
        raise ContractError("artifact_limit_exceeded", "Artifact bundle exceeds the profile writer limit.")
    ids: set[str] = set()
    sources: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    for index, value_item in enumerate(artifacts):
        item = _require_object(value_item, f"artifacts[{index}]")
        _check_keys(
            item,
            {"artifact_id", "source", "target", "sha256", "size_bytes"},
            {"media_type"},
            f"artifacts[{index}]",
        )
        artifact_id = _check_identifier(item.get("artifact_id"), "artifact_id")
        if artifact_id in ids:
            raise ContractError("artifact_id_duplicate", f"Duplicate artifact_id: {artifact_id}")
        ids.add(artifact_id)
        source = _check_path_ref(item.get("source"), f"artifacts[{index}].source")
        target = _check_path_ref(item.get("target"), f"artifacts[{index}].target")
        if source["path_id"] not in writer["allowed_source_path_ids"]:
            raise ContractError("artifact_source_forbidden", "Artifact source path_id is outside the profile boundary.")
        if target["path_id"] not in writer["allowed_target_path_ids"]:
            raise ContractError("artifact_target_forbidden", "Artifact target path_id is outside the profile boundary.")
        if not isinstance(item.get("sha256"), str) or not SHA256_RE.fullmatch(item["sha256"]):
            raise ContractError("sha256_invalid", f"Invalid sha256 for artifact {artifact_id}.")
        if not isinstance(item.get("size_bytes"), int) or isinstance(item["size_bytes"], bool) or item["size_bytes"] < 0:
            raise ContractError("size_invalid", f"Invalid size_bytes for artifact {artifact_id}.")
        sources.append(source)
        targets.append(target)
    _check_unique_refs(sources, "artifact sources")
    _check_unique_refs(targets, "artifact targets")
    return bundle


def validate_artifact_approval(value: Any) -> dict[str, Any]:
    approval = _require_object(value, "artifact approval")
    _check_keys(
        approval,
        {
            "schema_version",
            "task_id",
            "batch_id",
            "attempt_id",
            "owner_skill",
            "bundle_sha256",
            "plan_sha256",
            "approved",
            "approved_by",
            "approved_at",
        },
        {"note"},
        "artifact approval",
    )
    if approval.get("schema_version") != 1 or approval.get("approved") is not True:
        raise ContractError("approval_invalid", "Approval must use schema_version=1 and approved=true.")
    _check_identifier(approval.get("task_id"), "task_id")
    _check_identifier(approval.get("batch_id"), "batch_id")
    _check_identifier(approval.get("attempt_id"), "attempt_id")
    if approval.get("owner_skill") not in OWNER_SKILLS:
        raise ContractError("owner_skill_unknown", "Approval owner_skill is unknown.")
    for field in ("bundle_sha256", "plan_sha256"):
        if not isinstance(approval.get(field), str) or not SHA256_RE.fullmatch(approval[field]):
            raise ContractError("sha256_invalid", f"Invalid {field}.")
    if not isinstance(approval.get("approved_by"), str) or not approval["approved_by"] or len(approval["approved_by"]) > 128:
        raise ContractError("approval_invalid", "approved_by must be a short non-empty string.")
    if not isinstance(approval.get("approved_at"), str) or not approval["approved_at"]:
        raise ContractError("approval_invalid", "approved_at is required.")
    note = approval.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > 512):
        raise ContractError("approval_invalid", "Approval note must be at most 512 characters.")
    return approval


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    profile = subparsers.add_parser("profile", help="Print one import batch profile")
    profile.add_argument("--skill", required=True)
    for command in ("validate-task-card", "validate-result", "validate-bundle", "validate-approval"):
        child = subparsers.add_parser(command)
        child.add_argument("--input", required=True)
        if command == "validate-task-card":
            child.add_argument("--attempt-ledger")
        elif command == "validate-result":
            child.add_argument("--task-card", required=True)
            child.add_argument("--attempt-ledger", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "profile":
            result: Any = get_profile(args.skill)
        else:
            source = Path(args.input)
            value = load_json(source)
            if args.command == "validate-task-card":
                validate_task_card(value)
                if args.attempt_ledger:
                    validate_task_card_against_attempt_ledger(value, load_json(args.attempt_ledger))
            elif args.command == "validate-result":
                validate_compact_result(value, encoded_size=source.stat().st_size)
                card_path = Path(args.task_card)
                validate_result_lifecycle(
                    value,
                    load_json(card_path),
                    load_json(args.attempt_ledger),
                    task_card_sha256=sha256_file(card_path),
                )
            elif args.command == "validate-bundle":
                validate_artifact_bundle(value)
            else:
                validate_artifact_approval(value)
            result = {"ok": True, "kind": args.command.removeprefix("validate-")}
        _print(result)
        return 0
    except ContractError as exc:
        _print(exc.payload())
        return 2


if __name__ == "__main__":
    sys.exit(main())
