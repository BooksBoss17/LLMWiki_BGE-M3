#!/usr/bin/env python3
"""Small, durable batch orchestrator for one-shot executor/auditor workers.

The public interface is intentionally limited to init, dispatch, submit,
recover and status.  Business content stays in staged artifacts until an
independent auditor passes it; canonical writers own file transactions.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid
from typing import Any, Iterator


SCHEMA_VERSION = 3
STATE_KIND = "campaign_state"
CARD_KIND = "batch_card"
RESULT_KIND = "batch_result"
RECEIPT_KIND = "apply_receipt"
SOURCE_SLICE_KIND = "source_slice"
SOURCE_SLICE_SCHEMA_PATH = "skills/_shared/schemas/source_slice_v3.schema.json"
EXECUTOR_INTERRUPTION_DEFECTS = [
    "executor_stopped_before_complete_review",
    "media_formula_evidence_not_fully_reverified",
]
DEFAULT_ROOT = Path(__file__).resolve().parents[3]
STATE_REL = Path("skills/_ops/runtime/state/agent_batches_v3")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
RUNNING = {"executor_running", "repair_running", "auditor_running", "source_running"}
DISPATCHABLE = {
    "audit_queued",
    "reaudit_queued",
    "source_audit_queued",
    "repair_queued",
    "queued",
    "revalidation_queued",
    "source_blocked",
}
TERMINAL = {"passed", "no_change_passed", "blocked"}


class CampaignError(RuntimeError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise CampaignError("invalid_id", f"{label} is invalid")
    return value


def ensure_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise CampaignError("invalid_sha256", f"{label} must be a lowercase SHA-256")
    return value


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignError("json_read_failed", f"Cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise CampaignError("json_object_required", f"{label} must be a JSON object")
    return value


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    # Keep the temporary basename short: campaign/source-cache paths are already
    # deep on Windows, and repeating the full destination name can exceed the
    # legacy MAX_PATH boundary even when the final path itself is valid.
    fd, raw = tempfile.mkstemp(prefix=".atomic-", suffix=".tmp", dir=path.parent)
    temp = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextlib.contextmanager
def state_mutex(root: Path) -> Iterator[None]:
    """Use an OS-released, short-lived mutex only while updating state."""

    root.mkdir(parents=True, exist_ok=True)
    path = root / ".state.mutex"
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def kb_root(raw: str | None) -> Path:
    return Path(raw).resolve() if raw else DEFAULT_ROOT


def campaign_root(root: Path, campaign_id: str) -> Path:
    ensure_id(campaign_id, "campaign_id")
    return root / STATE_REL / campaign_id


def state_path(root: Path) -> Path:
    return root / "campaign.json"


def load_state(root: Path) -> dict[str, Any]:
    path = state_path(root)
    if not path.is_file():
        raise CampaignError("campaign_not_found", f"Campaign state is missing: {path}")
    value = load_json(path, "campaign state")
    if value.get("schema_version") != SCHEMA_VERSION or value.get("kind") != STATE_KIND:
        raise CampaignError("campaign_schema_invalid", "Campaign state is not v3")
    return value


def save_state(root: Path, state: dict[str, Any], event: str, **details: Any) -> None:
    state["state_revision"] = int(state.get("state_revision", 0)) + 1
    state["updated_at"] = now()
    state["last_event"] = {"event": event, "at": state["updated_at"], **details}
    atomic_json(state_path(root), state)


def inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise CampaignError("path_outside_root", f"Path is outside allowed root: {path}") from exc
    return resolved


def resolve_target(root: Path, ref: dict[str, Any]) -> Path:
    if ref.get("path_id") != "project.root":
        raise CampaignError("path_id_not_supported", "v3 active campaigns require path_id=project.root")
    rel = ref.get("relative_path")
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
        raise CampaignError("relative_path_invalid", "relative_path must be repository-relative")
    return inside(root / rel, root)


def verified_ref(root: Path, ref: Any, label: str) -> tuple[dict[str, Any], Path]:
    if not isinstance(ref, dict):
        raise CampaignError("artifact_ref_invalid", f"{label} reference is required")
    raw = ref.get("path")
    if not isinstance(raw, str) or not raw:
        raise CampaignError("artifact_path_invalid", f"{label}.path is required")
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    path = inside(path, root)
    expected = ensure_sha(ref.get("sha256"), f"{label}.sha256")
    if not path.is_file() or sha256_file(path) != expected:
        raise CampaignError("artifact_hash_mismatch", f"{label} is missing or changed", path=str(path))
    return {"path": str(path), "sha256": expected}, path


def file_ref(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": sha256_file(path)}


def validate_target_ref(root: Path, ref: Any, label: str) -> dict[str, Any]:
    if not isinstance(ref, dict):
        raise CampaignError("target_ref_invalid", f"{label} must be an object")
    normalized = dict(ref)
    expected = ensure_sha(normalized.get("sha256"), f"{label}.sha256")
    path = resolve_target(root, normalized)
    if not path.is_file() or sha256_file(path) != expected:
        raise CampaignError("target_hash_drift", f"{label} does not match the current file", path=str(path))
    return normalized


def validate_source_ref(root: Path, ref: Any, label: str) -> dict[str, Any] | None:
    if ref is None:
        return None
    return validate_target_ref(root, ref, label)


def validate_profile(root: Path, value: dict[str, Any]) -> dict[str, Any]:
    if value.get("schema_version") != SCHEMA_VERSION or value.get("kind") != "campaign_profile":
        raise CampaignError("profile_schema_invalid", "Profile must be campaign_profile v3")
    ensure_id(value.get("profile_id"), "profile_id")
    for key in ("executor_skills", "auditor_skills"):
        if not isinstance(value.get(key), list) or not all(isinstance(x, str) and x for x in value[key]):
            raise CampaignError("profile_skills_invalid", f"{key} must be a non-empty string list")
    allowed_paths = value.get("allowed_paths")
    if not isinstance(allowed_paths, list) or not allowed_paths or not all(
        isinstance(path, str) and path and not Path(path).is_absolute() and ".." not in Path(path).parts
        for path in allowed_paths
    ):
        raise CampaignError("profile_paths_invalid", "allowed_paths must be safe repository-relative paths")
    model_policy = value.get("model_policy")
    required_tiers = {"bounded", "ambiguous", "complex", "critical"}
    if not isinstance(model_policy, dict) or set(model_policy) != required_tiers or not all(
        isinstance(model_policy[tier], str) and model_policy[tier] for tier in required_tiers
    ):
        raise CampaignError("profile_model_policy_invalid", "model_policy must define all four static capability tiers")
    writer = value.get("writer")
    if not isinstance(writer, dict) or writer.get("mode") != "python":
        raise CampaignError("profile_writer_invalid", "Profile writer must use mode=python")
    script = writer.get("script")
    if not isinstance(script, str) or not script:
        raise CampaignError("profile_writer_invalid", "Writer script is required")
    writer_path = inside(root / script, root)
    if not writer_path.is_file():
        raise CampaignError("profile_writer_missing", f"Writer script is missing: {writer_path}")
    if not isinstance(writer.get("arguments"), list) or not all(isinstance(x, str) for x in writer["arguments"]):
        raise CampaignError("profile_writer_invalid", "Writer arguments must be a string list")
    return value


def validate_authorization(
    value: dict[str, Any],
    campaign_id: str,
    ledger_sha: str,
    root: Path,
    profile: dict[str, Any],
) -> None:
    if value.get("campaign_id") != campaign_id or value.get("ledger_sha256") != ledger_sha:
        raise CampaignError("authorization_binding_mismatch", "Authorization does not bind this campaign and ledger")
    if value.get("destructive") is not False:
        raise CampaignError("authorization_destructive", "Campaign authorization must use destructive=false")
    record_path = value.get("authorization_record_path")
    record_sha = value.get("authorization_record_sha256")
    if not isinstance(record_path, str) or not isinstance(record_sha, str):
        raise CampaignError("authorization_record_missing", "Authorization must bind the user's authorization record")
    record = inside(Path(record_path), root)
    if not record.is_file() or sha256_file(record) != ensure_sha(record_sha, "authorization_record_sha256"):
        raise CampaignError("authorization_record_drift", "User authorization record is missing or changed")
    allowed_fields = value.get("allowed_fields")
    if not isinstance(allowed_fields, list) or not allowed_fields or not all(isinstance(x, str) and x for x in allowed_fields):
        raise CampaignError("authorization_fields_invalid", "Authorization must list allowed fields")
    profile_script = str(profile["writer"]["script"]).replace("\\", "/")
    script_parts = Path(profile_script).parts
    writer_names = {profile_script, Path(profile_script).name}
    if len(script_parts) >= 3 and script_parts[-2] == "scripts":
        writer_names.add(f"{script_parts[-3]}/{script_parts[-1]}")
    if value.get("writer") not in writer_names:
        raise CampaignError("authorization_writer_mismatch", "Authorization does not bind the profile writer")
    expires = value.get("expires_at")
    if not isinstance(expires, str):
        raise CampaignError("authorization_expiry_invalid", "Authorization expires_at is required")
    try:
        expiry = dt.datetime.fromisoformat(expires.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CampaignError("authorization_expiry_invalid", "Authorization expires_at is invalid") from exc
    if expiry <= dt.datetime.now(dt.timezone.utc):
        raise CampaignError("authorization_expired", "Campaign authorization has expired")


def initialize(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    cid = ensure_id(args.campaign, "campaign_id")
    out = campaign_root(root, cid)
    with state_mutex(out):
        if state_path(out).exists():
            raise CampaignError("campaign_exists", f"Campaign already exists: {cid}")
        ledger_path = inside(Path(args.ledger), root)
        auth_path = inside(Path(args.authorization), root)
        profile_path = inside(Path(args.profile), root)
        ledger = load_json(ledger_path, "ledger")
        auth = load_json(auth_path, "authorization")
        profile = validate_profile(root, load_json(profile_path, "profile"))
        ledger_sha = sha256_file(ledger_path)
        validate_authorization(auth, cid, ledger_sha, root, profile)
        raw_items = ledger.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise CampaignError("ledger_items_invalid", "Ledger items must be a non-empty array")
        items: dict[str, Any] = {}
        ordinals: list[int] = []
        targets: set[str] = set()
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise CampaignError("ledger_item_invalid", "Ledger item must be an object")
            qid = ensure_id(raw.get("question_id"), "question_id")
            if qid in items:
                raise CampaignError("ledger_duplicate_question", f"Duplicate question_id: {qid}")
            ordinal = raw.get("queue_ordinal")
            if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
                raise CampaignError("ledger_ordinal_invalid", f"Invalid queue ordinal for {qid}")
            target = validate_target_ref(root, raw.get("target_ref"), f"{qid}.target_ref")
            target_path = resolve_target(root, target)
            if not any(
                target_path == (root / allowed).resolve() or (root / allowed).resolve() in target_path.parents
                for allowed in profile["allowed_paths"]
            ):
                raise CampaignError("target_outside_profile", f"Target is outside profile allowed_paths: {qid}")
            canonical = str(resolve_target(root, target)).casefold()
            if canonical in targets:
                raise CampaignError("ledger_duplicate_target", f"Duplicate target path: {qid}")
            targets.add(canonical)
            source_ready = bool(raw.get("source_ready"))
            source = validate_source_ref(root, raw.get("source_ref"), f"{qid}.source_ref") if source_ready else raw.get("source_ref")
            initial = raw.get("initial_status")
            if initial not in {"queued", "revalidation_queued", "source_blocked"}:
                initial = "queued" if source_ready else "source_blocked"
            if not source_ready:
                initial = "source_blocked"
            items[qid] = {
                "question_id": qid,
                "queue_ordinal": ordinal,
                "severity": raw.get("severity"),
                "capability": raw.get("capability", "bounded"),
                "source_ready": source_ready,
                "target_ref": target,
                "source_ref": source,
                "source_slice_ref": None,
                "initial_status": initial,
                "status": initial,
                "revision_no": 0,
                "dispatch_attempts": {},
                "candidate": None,
                "audit": None,
                "apply_receipt": None,
                "defects": [],
            }
            ordinals.append(ordinal)
        if sorted(ordinals) != list(range(len(ordinals))):
            raise CampaignError("ledger_ordinals_not_contiguous", "Queue ordinals must be contiguous from zero")
        state = {
            "schema_version": SCHEMA_VERSION,
            "kind": STATE_KIND,
            "campaign_id": cid,
            "scope_key": ledger.get("scope_key", cid),
            "ledger_ref": file_ref(ledger_path),
            "authorization_ref": file_ref(auth_path),
            "profile_ref": file_ref(profile_path),
            "profile": profile,
            "batch_size": min(max(int(args.batch_size), 1), 5),
            "items": items,
            "active_run": None,
            "state_revision": 0,
            "created_at": now(),
            "updated_at": now(),
        }
        save_state(out, state, "initialized", item_count=len(items))
    return compact_status(state)


def phase_for_status(status: str) -> str:
    if status in {"audit_queued", "reaudit_queued", "source_audit_queued"}:
        return "auditor"
    if status == "repair_queued":
        return "repair"
    if status == "source_blocked":
        return "source"
    return "executor"


def phase_for_item(item: dict[str, Any]) -> str:
    status = item["status"]
    if status in {"queued", "revalidation_queued"} and item.get("source_slice_ref") is None:
        return "source"
    return phase_for_status(status)


def running_status(phase: str) -> str:
    return {"executor": "executor_running", "repair": "repair_running", "auditor": "auditor_running", "source": "source_running"}[phase]


def dispatch_priority(status: str) -> int:
    return {
        "audit_queued": 0,
        "reaudit_queued": 0,
        "source_audit_queued": 0,
        "repair_queued": 1,
        # Source-blocked items must reach the dedicated A-only source phase
        # before ordinary content executors consume queued work.  Keeping
        # this behind repair but ahead of revalidation/queued preserves the
        # audit and repair gates while making source resolution actionable.
        "source_blocked": 2,
        "revalidation_queued": 3,
        "queued": 4,
    }[status]


def dispatch_priority_for_item(item: dict[str, Any]) -> int:
    if phase_for_item(item) == "source" and item["status"] in {"queued", "revalidation_queued"}:
        return 2
    return dispatch_priority(item["status"])


def source_group_key(item: dict[str, Any]) -> str:
    """Return the stable source identity used to keep a batch coherent."""
    source_ref = item.get("source_ref")
    if item.get("status") == "source_audit_queued":
        candidate = item.get("candidate") or {}
        source_ref = candidate.get("source_ref") or source_ref
    if isinstance(source_ref, dict) and source_ref.get("sha256"):
        return ensure_sha(source_ref["sha256"], "source_group_sha256")
    return "NO_SOURCE"


def dispatch_batch_limit(state: dict[str, Any], phase: str, candidates: list[dict[str, Any]]) -> int:
    """Keep complex canaries deliberately small without expanding the CLI."""
    limit = int(state["batch_size"])
    rollout = state["profile"].get("rollout_policy") or {}
    canaries = rollout.get("canary_campaigns") or []
    first_capability = candidates[0].get("capability", "bounded") if candidates else "bounded"
    if (
        state["campaign_id"] in canaries
        and phase in {"executor", "repair"}
        and first_capability in {"complex", "critical"}
    ):
        configured = rollout.get("complex_canary_batch_size", 1)
        return min(limit, max(1, int(configured)))
    return limit


def card_item(
    item: dict[str, Any],
    phase: str,
    source_cache_root: Path,
    run_id: str,
) -> dict[str, Any]:
    value = {
        "question_id": item["question_id"],
        "queue_ordinal": item["queue_ordinal"],
        "target_ref": item["target_ref"],
        "source_ref": item.get("source_ref"),
        "capability": item.get("capability", "bounded"),
    }
    source_ref = item.get("source_ref")
    if isinstance(source_ref, dict) and source_ref.get("sha256"):
        item_cache_root = source_cache_root / source_ref["sha256"]
        value["source_cache_root"] = str(item_cache_root)
        if phase == "source":
            value["source_slice_path"] = str(
                item_cache_root / f"{item['question_id']}.source-slice.{run_id}.json"
            )
            value["source_artifact_root"] = str(
                item_cache_root / f"{item['question_id']}.{run_id}"
            )
    if item.get("source_slice_ref") is not None:
        value["source_slice_ref"] = item["source_slice_ref"]
    if phase in {"auditor", "repair"}:
        value["candidate"] = item.get("candidate")
        value["prior_audit"] = item.get("audit")
    return value


def stage_plan(
    profile: dict[str, Any],
    phase: str,
    selected_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if phase == "auditor":
        skills = profile["auditor_skills"]
        names = ["content_audit", "governance_audit"]
        first_completion = "content_audit_artifact"
    elif phase == "source":
        skills = profile["executor_skills"][:1]
        names = ["source_resolution"]
        first_completion = "batch_result_submitted"
    else:
        reuse_source_slices = bool(selected_items) and all(
            item.get("source_slice_ref") is not None for item in selected_items
        )
        if reuse_source_slices:
            skills = profile["executor_skills"][-1:]
            names = ["curate_or_repair"]
            first_completion = "batch_result_submitted"
        else:
            skills = profile["executor_skills"]
            names = ["prepare_source", "curate_or_repair"]
            first_completion = "source_slice_or_blocked"
    plan: list[dict[str, Any]] = []
    for index, skill in enumerate(skills):
        final = index == len(skills) - 1
        plan.append({
            "order": index + 1,
            "stage": names[index] if index < len(names) else f"stage_{index + 1}",
            "primary_skill": skill,
            "completion": "batch_result_submitted" if final else first_completion,
            "formal_write": False,
        })
    return plan


def result_skeleton(card: dict[str, Any], card_sha256: str) -> dict[str, Any]:
    rows = []
    for item in card["items"]:
        row = {"question_id": item["question_id"], "defects": []}
        if card["phase"] == "auditor":
            row["verdict"] = "pending"
            row["audit"] = None
        elif card["phase"] == "source":
            row.update({
                "status": "pending",
                "source_ref": None,
                "source_slice_ref": None,
                "evidence": None,
            })
        else:
            row.update({
                "status": "pending",
                "intent": None,
                "source_slice_ref": item.get("source_slice_ref"),
                "evidence": None,
                "proposal": None,
                "dry_run": None,
            })
        rows.append(row)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": RESULT_KIND,
        "campaign_id": card["campaign_id"],
        "batch_id": card["batch_id"],
        "run_id": card["run_id"],
        "phase": card["phase"],
        "batch_card_sha256": card_sha256,
        "status": "pending",
        "producer": None,
        "items": rows,
        "created_at": now(),
    }


def precreate_source_slice_skeletons(card: dict[str, Any]) -> None:
    if card.get("phase") != "source":
        return
    for item in card["items"]:
        source_ref = item.get("source_ref") or {}
        source_sha = ensure_sha(source_ref.get("sha256"), "source_sha256")
        manifest_path = Path(item["source_slice_path"])
        artifact_root = Path(item["source_artifact_root"])
        inside(manifest_path, Path(item["source_cache_root"]))
        inside(artifact_root, Path(item["source_cache_root"]))
        artifact_root.mkdir(parents=True, exist_ok=True)
        atomic_json(manifest_path, {
            "schema_version": SCHEMA_VERSION,
            "kind": SOURCE_SLICE_KIND,
            "question_id": item["question_id"],
            "source_sha256": source_sha,
            "locator": None,
            "artifact_ref": None,
        })


def clear_stale_source_retry_state(item: dict[str, Any]) -> bool:
    if item.get("status") != "source_audit_queued":
        return False
    candidate = item.get("candidate") or {}
    audit = item.get("audit") or {}
    if candidate.get("kind") != "source_mapping" or not audit:
        return False
    candidate_run = ((candidate.get("producer") or {}).get("run_id"))
    audit_run = ((audit.get("producer") or {}).get("run_id"))
    if not candidate_run or not audit_run or candidate_run == audit_run:
        return False
    item["audit"] = None
    item["defects"] = []
    return True


def dispatch(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    out = campaign_root(root, args.campaign)
    with state_mutex(out):
        state = load_state(out)
        if state.get("active_run") is not None:
            raise CampaignError("active_run_exists", "Campaign already has an active run", active_run=state["active_run"])
        for item in state["items"].values():
            clear_stale_source_retry_state(item)
        candidates = [x for x in state["items"].values() if x["status"] in DISPATCHABLE]
        exhausted = False
        for item in candidates:
            phase = phase_for_item(item)
            attempt_key = f"{phase}:r{item['revision_no']}"
            if int(item["dispatch_attempts"].get(attempt_key, 0)) >= 2:
                item["status"] = "blocked"
                item["defects"] = ["dispatch_attempts_exhausted"]
                exhausted = True
        if exhausted:
            save_state(out, state, "dispatch_exhausted")
            candidates = [x for x in state["items"].values() if x["status"] in DISPATCHABLE]
        if not candidates:
            return {"ok": True, "campaign_id": args.campaign, "complete": all(x["status"] in TERMINAL for x in state["items"].values()), "active_run": None, "counts": status_counts(state)}
        candidates.sort(key=lambda x: (dispatch_priority_for_item(x), x["queue_ordinal"]))
        anchor = candidates[0]
        selected_status = anchor["status"]
        phase = phase_for_item(anchor)
        selected_capability = anchor.get("capability", "bounded")
        selected_source = source_group_key(anchor)
        same_group = [
            item for item in candidates
            if item["status"] == selected_status
            and phase_for_item(item) == phase
            and item.get("capability", "bounded") == selected_capability
            and source_group_key(item) == selected_source
        ]
        selected = same_group[: dispatch_batch_limit(state, phase, same_group)]
        for item in selected:
            source_slice_ref = item.get("source_slice_ref")
            if source_slice_ref is None:
                continue
            try:
                _slice_ref, slice_path = verified_ref(root, source_slice_ref, "source slice")
                item["source_slice_ref"] = validate_source_slice(root, out, item, str(slice_path))
            except CampaignError:
                # A stale cached slice is never trusted. The batch falls back to
                # its Role A preparation stage and creates a fresh hash-bound slice.
                item["source_slice_ref"] = None
        phase = phase_for_item(selected[0])
        selected = [item for item in selected if phase_for_item(item) == phase]
        usable: list[dict[str, Any]] = []
        attempt_key = f"{phase}:r{selected[0]['revision_no']}"
        for item in selected:
            attempts = int(item["dispatch_attempts"].get(attempt_key, 0))
            item["dispatch_attempts"][attempt_key] = attempts + 1
            usable.append(item)
        run_id = f"run-{uuid.uuid4().hex}"
        first = min(x["queue_ordinal"] for x in usable)
        revision = usable[0]["revision_no"]
        batch_id = f"{args.campaign}-q{first:06d}-r{revision}-{phase}"
        result_path = out / "results" / f"{run_id}.json"
        artifact_root = out / "artifacts" / run_id
        profile = state["profile"]
        capability = max((x.get("capability", "bounded") for x in usable), key=lambda x: ("bounded", "ambiguous", "complex", "critical").index(x) if x in ("bounded", "ambiguous", "complex", "critical") else 0)
        card = {
            "schema_version": SCHEMA_VERSION,
            "kind": CARD_KIND,
            "campaign_id": args.campaign,
            "batch_id": batch_id,
            "run_id": run_id,
            "phase": phase,
            "revision_no": revision,
            "capability": capability,
            "recommended_runtime": (profile.get("model_policy") or {}).get(capability),
            "stage_plan": stage_plan(profile, phase, usable),
            "authorization_ref": state["authorization_ref"],
            "items": [card_item(x, phase, out / "source-cache", run_id) for x in usable],
            "artifact_root": str(artifact_root),
            "result_path": str(result_path),
            "result_contract": {
                "skeleton_precreated": True,
                "must_submit_before_exit": True,
                "schema_path": "skills/_shared/schemas/batch_result_v3.schema.json",
                "producer_required": True,
                "partial_failure_statuses": ["source_blocked", "unresolved", "runtime_failed"],
            },
            "worker_contract": {
                "execution_mode": "isolated_worker",
                "fork_context": False,
                "worker_instance_id_source": "spawn_response",
                "producer_role": phase,
                "main_agent_may_produce_result": False,
            },
            "created_at": now(),
        }
        if phase == "source":
            card["source_slice_contract"] = {
                "schema_path": SOURCE_SLICE_SCHEMA_PATH,
                "manifest_skeleton_precreated": True,
                "worker_must_overwrite_skeleton": True,
                "artifact_must_be_under_item_root": True,
                "locator_coordinate_space_required": True,
            }
            precreate_source_slice_skeletons(card)
        card_path = out / "cards" / f"{run_id}.json"
        atomic_json(card_path, card)
        card_ref = file_ref(card_path)
        atomic_json(result_path, result_skeleton(card, card_ref["sha256"]))
        result_skeleton_ref = file_ref(result_path)
        pre_statuses = {x["question_id"]: x["status"] for x in usable}
        for item in usable:
            item["status"] = running_status(phase)
        state["active_run"] = {
            "run_id": run_id,
            "batch_id": batch_id,
            "phase": phase,
            "revision_no": revision,
            "question_ids": [x["question_id"] for x in usable],
            "card_ref": card_ref,
            "result_path": str(result_path),
            "result_skeleton_ref": result_skeleton_ref,
            "pre_statuses": pre_statuses,
            "started_at": now(),
        }
        save_state(out, state, "dispatched", run_id=run_id, phase=phase)
    return {"ok": True, "campaign_id": args.campaign, "active_run": state["active_run"], "batch_card": card_ref}


def validate_producer(value: Any, phase: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise CampaignError("producer_required", "Submitted batch result requires producer metadata")
    worker_id = value.get("worker_instance_id")
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise CampaignError("producer_id_invalid", "producer.worker_instance_id is required")
    if value.get("execution_mode") != "isolated_worker":
        raise CampaignError("producer_execution_mode_invalid", "Only isolated_worker results may be submitted")
    if value.get("role") != phase:
        raise CampaignError("producer_role_mismatch", "Producer role must match the active phase")
    return {
        "worker_instance_id": worker_id.strip(),
        "execution_mode": "isolated_worker",
        "role": phase,
    }


def record_producer(item: dict[str, Any], producer: dict[str, str], run_id: str) -> dict[str, str]:
    history = item.setdefault("producer_history", [])
    worker_id = producer["worker_instance_id"]
    if any(isinstance(row, dict) and row.get("worker_instance_id") == worker_id for row in history):
        raise CampaignError("producer_reused", "Each retry and phase requires a fresh worker", worker_instance_id=worker_id)
    entry = {**producer, "run_id": run_id}
    history.append(entry)
    return entry


def validate_result(
    root: Path,
    out: Path,
    state: dict[str, Any],
    result_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], Path, dict[str, str]]:
    active = state.get("active_run")
    if not isinstance(active, dict) or active.get("phase") == "applying":
        raise CampaignError("no_active_worker", "Campaign has no active worker result to submit")
    expected_path = Path(active["result_path"]).resolve()
    if result_path.resolve() != expected_path:
        raise CampaignError("result_path_mismatch", "Result must be written to the pre-registered path")
    result = load_json(result_path, "batch result")
    card_ref_value, card_path = verified_ref(root, active["card_ref"], "batch card")
    bindings = {
        "schema_version": SCHEMA_VERSION,
        "kind": RESULT_KIND,
        "campaign_id": state["campaign_id"],
        "batch_id": active["batch_id"],
        "run_id": active["run_id"],
        "phase": active["phase"],
        "batch_card_sha256": card_ref_value["sha256"],
    }
    mismatch = [key for key, value in bindings.items() if result.get(key) != value]
    if mismatch:
        raise CampaignError("result_binding_mismatch", "Batch result does not bind the active run", fields=mismatch)
    if result.get("status") != "submitted":
        raise CampaignError("result_not_submitted", "Batch result must set status=submitted before submit")
    producer = validate_producer(result.get("producer"), active["phase"])
    rows = result.get("items")
    if not isinstance(rows, list):
        raise CampaignError("result_items_invalid", "Batch result items must be an array")
    by_id = {row.get("question_id"): row for row in rows if isinstance(row, dict)}
    if set(by_id) != set(active["question_ids"]):
        raise CampaignError("result_question_set_mismatch", "Batch result question set differs from the active run")
    return result, load_json(card_path, "batch card"), card_path, producer


def artifact(root: Path, value: Any, label: str, allowed: Path) -> dict[str, str]:
    ref, path = verified_ref(root, value, label)
    inside(path, allowed)
    return ref


def accept_executor_item(
    root: Path,
    out: Path,
    item: dict[str, Any],
    row: dict[str, Any],
    phase: str,
    allowed: Path,
    pre_status: str,
    producer: dict[str, str],
    run_id: str,
    card_item_value: dict[str, Any] | None = None,
) -> None:
    producer_entry = record_producer(item, producer, run_id)
    status = row.get("status")
    if status == "runtime_failed":
        attempt_key = f"{phase}:r{item['revision_no']}"
        if int(item["dispatch_attempts"].get(attempt_key, 0)) >= 2:
            item["status"] = "blocked"
            item["defects"] = ["runtime_attempts_exhausted"]
        else:
            item["status"] = pre_status
            item["defects"] = list(row.get("defects") or ["runtime_failed"])
        item["candidate"] = None
        item["audit"] = None
        return
    if phase == "source":
        if status == "source_mapped":
            source = validate_source_ref(root, row.get("source_ref"), f"{item['question_id']}.source_ref")
            if source is None:
                raise CampaignError("source_mapping_missing", f"Source mapping is missing for {item['question_id']}")
            _submitted_ref, source_slice_path = verified_ref(
                root,
                row.get("source_slice_ref"),
                f"{item['question_id']}.source_slice_ref",
            )
            expected_slice_path = (card_item_value or {}).get("source_slice_path")
            if not expected_slice_path or source_slice_path.resolve() != Path(expected_slice_path).resolve():
                raise CampaignError(
                    "source_slice_path_mismatch",
                    f"Source slice must use the pre-registered manifest path for {item['question_id']}",
                )
            source_item = {**item, "source_ref": source}
            source_slice_ref = validate_source_slice(
                root,
                out,
                source_item,
                str(source_slice_path),
                require_coordinate_space=True,
                artifact_allowed=Path((card_item_value or {})["source_artifact_root"]),
            )
            evidence = artifact(root, row.get("evidence"), "source evidence", allowed)
            item["candidate"] = {
                "kind": "source_mapping",
                "source_ref": source,
                "source_slice_ref": source_slice_ref,
                "evidence": evidence,
                "producer": producer_entry,
            }
            item["audit"] = None
            item["defects"] = []
            item["status"] = "source_audit_queued"
        elif status in {"source_blocked", "unresolved"}:
            item["status"] = "source_blocked"
            item["defects"] = list(row.get("defects") or [status])
        else:
            raise CampaignError("source_result_invalid", f"Invalid source result for {item['question_id']}")
        return
    if status in {"source_blocked", "unresolved"}:
        item["status"] = "source_blocked" if status == "source_blocked" else "blocked"
        item["defects"] = list(row.get("defects") or [status])
        return
    if status != "proposed" or row.get("intent") not in {"apply", "no_change"}:
        raise CampaignError("executor_result_invalid", f"Invalid executor result for {item['question_id']}")
    source_slice_ref = row.get("source_slice_ref")
    if source_slice_ref is None and item.get("source_ref") is not None:
        raise CampaignError(
            "source_slice_required_for_proposal",
            f"Proposed result requires a verified source slice for {item['question_id']}",
        )
    if source_slice_ref is not None:
        _submitted_ref, source_slice_path = verified_ref(
            root,
            source_slice_ref,
            f"{item['question_id']}.source_slice_ref",
        )
        item["source_slice_ref"] = validate_source_slice(
            root,
            out,
            item,
            str(source_slice_path),
        )
    evidence = artifact(root, row.get("evidence"), "evidence", allowed)
    candidate: dict[str, Any] = {
        "kind": "curation",
        "intent": row["intent"],
        "evidence": evidence,
        "producer": producer_entry,
    }
    if row["intent"] == "apply":
        candidate["proposal"] = artifact(root, row.get("proposal"), "proposal", allowed)
        candidate["dry_run"] = artifact(root, row.get("dry_run"), "dry-run receipt", allowed)
    item["candidate"] = candidate
    item["audit"] = None
    item["status"] = "reaudit_queued" if phase == "repair" else "audit_queued"


def accept_audit_item(
    root: Path,
    item: dict[str, Any],
    row: dict[str, Any],
    allowed: Path,
    producer: dict[str, str],
    run_id: str,
) -> str:
    candidate = item.get("candidate") or {}
    executor_producer = candidate.get("producer") or {}
    if executor_producer.get("worker_instance_id") == producer["worker_instance_id"]:
        raise CampaignError(
            "producer_identity_collision",
            "Auditor must be a different isolated worker from the candidate producer",
            worker_instance_id=producer["worker_instance_id"],
        )
    producer_entry = record_producer(item, producer, run_id)
    verdict = row.get("verdict")
    if verdict not in {"passed", "failed"}:
        raise CampaignError("audit_verdict_invalid", f"Invalid audit verdict for {item['question_id']}")
    audit_ref = artifact(root, row.get("audit"), "audit", allowed)
    item["audit"] = {
        "verdict": verdict,
        "artifact": audit_ref,
        "defects": list(row.get("defects") or []),
        "producer": producer_entry,
    }
    if verdict == "failed":
        if candidate.get("kind") == "source_mapping":
            item["status"] = "source_blocked"
            item["candidate"] = None
        elif item["revision_no"] == 0:
            item["revision_no"] = 1
            item["status"] = "repair_queued"
        else:
            item["status"] = "blocked"
        item["defects"] = list(row.get("defects") or ["audit_failed"])
        return "failed"
    if candidate.get("kind") == "source_mapping":
        item["last_source_producer_chain"] = {
            "executor": candidate.get("producer"),
            "auditor": item["audit"].get("producer"),
        }
        item["source_ref"] = candidate["source_ref"]
        item["source_slice_ref"] = candidate["source_slice_ref"]
        item["source_ready"] = True
        item["candidate"] = None
        item["audit"] = None
        item["status"] = "queued"
        item["defects"] = []
        return "source_passed"
    if candidate.get("intent") == "no_change":
        item["status"] = "no_change_passed"
        item["defects"] = []
        return "no_change"
    item["status"] = "apply_pending"
    item["defects"] = []
    return "apply"


def substitution_values(root: Path, out: Path, state: dict[str, Any], active: dict[str, Any], item: dict[str, Any], result_path: Path, result_sha: str, receipt_path: Path) -> dict[str, str]:
    candidate = item["candidate"]
    return {
        "kb_root": str(root),
        "campaign_id": state["campaign_id"],
        "batch_id": active["batch_id"],
        "run_id": active["run_id"],
        "question_id": item["question_id"],
        "proposal_path": candidate["proposal"]["path"],
        "proposal_sha256": candidate["proposal"]["sha256"],
        "dry_run_path": candidate["dry_run"]["path"],
        "dry_run_sha256": candidate["dry_run"]["sha256"],
        "audit_result_path": str(result_path),
        "audit_result_sha256": result_sha,
        "batch_card_path": active["card_ref"]["path"],
        "batch_card_sha256": active["card_ref"]["sha256"],
        "authorization_path": state["authorization_ref"]["path"],
        "authorization_sha256": state["authorization_ref"]["sha256"],
        "receipt_path": str(receipt_path),
    }


def validate_apply_receipt(root: Path, state: dict[str, Any], active: dict[str, Any], item: dict[str, Any], receipt_path: Path) -> dict[str, Any]:
    receipt = load_json(receipt_path, "apply receipt")
    expected = {
        "schema_version": SCHEMA_VERSION,
        "kind": RECEIPT_KIND,
        "status": "applied",
        "campaign_id": state["campaign_id"],
        "batch_id": active["batch_id"],
        "run_id": active["run_id"],
        "question_id": item["question_id"],
        "authorization_sha256": state["authorization_ref"]["sha256"],
    }
    mismatch = [key for key, value in expected.items() if receipt.get(key) != value]
    if mismatch:
        raise CampaignError("apply_receipt_binding_mismatch", "Writer receipt does not bind the approved item", fields=mismatch)
    files = receipt.get("files")
    if not isinstance(files, list) or not files:
        raise CampaignError("apply_receipt_files_invalid", "Writer receipt has no files")
    target_path = resolve_target(root, item["target_ref"])
    target = next((x for x in files if Path(str(x.get("path", ""))).resolve() == target_path), None)
    if not isinstance(target, dict):
        raise CampaignError("apply_receipt_target_missing", "Writer receipt does not include the canonical target")
    before = ensure_sha(target.get("before_sha256"), "receipt.before_sha256")
    after = ensure_sha(target.get("after_sha256"), "receipt.after_sha256")
    if before != item["target_ref"]["sha256"]:
        raise CampaignError("apply_receipt_before_mismatch", "Writer receipt baseline differs from the approved target")
    if not target_path.is_file() or sha256_file(target_path) != after:
        raise CampaignError("apply_receipt_post_mismatch", "Canonical target does not match the writer receipt")
    validations = receipt.get("validations")
    if not isinstance(validations, dict) or validations.get("ok") is not True:
        raise CampaignError("apply_validation_failed", "Writer deterministic validation did not pass")
    return receipt


def invoke_writer(root: Path, out: Path, state: dict[str, Any], active: dict[str, Any], item: dict[str, Any], result_path: Path, result_sha: str) -> dict[str, Any]:
    candidate_producer = (item.get("candidate") or {}).get("producer") or {}
    auditor_producer = (item.get("audit") or {}).get("producer") or {}
    executor_id = candidate_producer.get("worker_instance_id")
    auditor_id = auditor_producer.get("worker_instance_id")
    if (
        candidate_producer.get("execution_mode") != "isolated_worker"
        or auditor_producer.get("execution_mode") != "isolated_worker"
        or not executor_id
        or not auditor_id
    ):
        raise CampaignError("producer_chain_missing", "Writer requires isolated executor and auditor producer metadata")
    if executor_id == auditor_id:
        raise CampaignError("producer_identity_collision", "Writer refuses same-worker executor/auditor chains")
    receipt_path = out / "receipts" / active["batch_id"] / f"{item['question_id']}.apply.json"
    if receipt_path.is_file():
        return validate_apply_receipt(root, state, active, item, receipt_path)
    writer = state["profile"]["writer"]
    script = inside(root / writer["script"], root)
    values = substitution_values(root, out, state, active, item, result_path, result_sha, receipt_path)
    arguments = [value.format_map(values) for value in writer["arguments"]]
    completed = subprocess.run(
        [sys.executable, str(script), *arguments],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=int(writer.get("timeout_seconds", 300)),
        check=False,
    )
    if completed.returncode != 0:
        raise CampaignError("writer_failed", "Canonical writer failed", stderr=completed.stderr[-2000:], stdout=completed.stdout[-2000:])
    if not receipt_path.is_file():
        raise CampaignError("writer_receipt_missing", "Canonical writer returned without an apply receipt")
    return validate_apply_receipt(root, state, active, item, receipt_path)


def finish_apply(root: Path, out: Path, state: dict[str, Any]) -> list[dict[str, Any]]:
    active = state.get("active_run")
    if not isinstance(active, dict) or active.get("phase") != "applying":
        return []
    result_path = Path(active["audit_result_ref"]["path"])
    if sha256_file(result_path) != active["audit_result_ref"]["sha256"]:
        raise CampaignError("audit_result_drift", "Approved audit result changed before apply")
    results: list[dict[str, Any]] = []
    for qid in active["question_ids"]:
        item = state["items"][qid]
        try:
            receipt = invoke_writer(root, out, state, active, item, result_path, active["audit_result_ref"]["sha256"])
            target_path = resolve_target(root, item["target_ref"])
            after = sha256_file(target_path)
            item["target_ref"]["sha256"] = after
            item["apply_receipt"] = file_ref(out / "receipts" / active["batch_id"] / f"{qid}.apply.json")
            item["status"] = "passed"
            results.append({"question_id": qid, "status": "passed", "after_sha256": after})
        except CampaignError as exc:
            current = resolve_target(root, item["target_ref"])
            if current.is_file() and sha256_file(current) == item["target_ref"]["sha256"]:
                if int(item.get("revision_no", 0)) == 0:
                    item["revision_no"] = 1
                    item["status"] = "repair_queued"
                    item["candidate"] = None
                    item["audit"] = None
                    item["defects"] = [exc.code]
                else:
                    item["status"] = "blocked"
                    item["defects"] = [exc.code]
            else:
                item["status"] = "revalidation_queued"
                item["candidate"] = None
                item["audit"] = None
                item["revision_no"] = 0
                item["defects"] = ["target_hash_drift"]
            results.append({"question_id": qid, "status": item["status"], "error": exc.code})
    state["active_run"] = None
    return results


def submit(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    out = campaign_root(root, args.campaign)
    result_path = inside(Path(args.result), root)
    apply_needed = False
    with state_mutex(out):
        state = load_state(out)
        result, card, _card_path, producer = validate_result(root, out, state, result_path)
        active = dict(state["active_run"])
        result_ref = file_ref(result_path)
        allowed = Path(card["artifact_root"]).resolve()
        rows = {row["question_id"]: row for row in result["items"]}
        card_items = {row["question_id"]: row for row in card["items"]}
        if active["phase"] in {"executor", "repair", "source"}:
            for qid in active["question_ids"]:
                accept_executor_item(
                    root,
                    out,
                    state["items"][qid],
                    rows[qid],
                    active["phase"],
                    allowed,
                    active["pre_statuses"][qid],
                    producer,
                    active["run_id"],
                    card_items[qid],
                )
            state["active_run"] = None
            save_state(out, state, "result_submitted", run_id=active["run_id"], phase=active["phase"])
        else:
            apply_ids: list[str] = []
            for qid in active["question_ids"]:
                outcome = accept_audit_item(
                    root,
                    state["items"][qid],
                    rows[qid],
                    allowed,
                    producer,
                    active["run_id"],
                )
                if outcome == "apply":
                    apply_ids.append(qid)
            if apply_ids:
                state["active_run"] = {
                    **active,
                    "phase": "applying",
                    "question_ids": apply_ids,
                    "audit_result_ref": result_ref,
                }
                apply_needed = True
            else:
                state["active_run"] = None
            save_state(out, state, "audit_submitted", run_id=active["run_id"], apply_count=len(apply_ids))
    applied: list[dict[str, Any]] = []
    if apply_needed:
        with state_mutex(out):
            state = load_state(out)
            baseline_revision = state["state_revision"]
            baseline_active = json.loads(json.dumps(state["active_run"]))
            # Apply intent is durable; release the state mutex while writers run.
        state_copy = json.loads(json.dumps(state))
        applied = finish_apply(root, out, state_copy)
        with state_mutex(out):
            current = load_state(out)
            if current.get("active_run") != baseline_active or current.get("state_revision") != baseline_revision:
                raise CampaignError("state_changed_during_apply", "Campaign state changed while canonical writers ran")
            state_copy["state_revision"] = current["state_revision"]
            save_state(out, state_copy, "apply_completed", results=applied)
            state = state_copy
    submitted_items: list[dict[str, Any]] = []
    for qid in active["question_ids"]:
        item = state["items"][qid]
        compact = {"question_id": qid, "status": item["status"]}
        if item.get("source_slice_ref") is not None:
            compact["source_slice_ref"] = item["source_slice_ref"]
        if item.get("apply_receipt") is not None:
            compact["apply_receipt"] = item["apply_receipt"]
        if item.get("defects"):
            compact["defects"] = item["defects"]
        submitted_items.append(compact)
    return {
        "ok": True,
        "campaign_id": args.campaign,
        "counts": status_counts(state),
        "items": submitted_items,
        "applied": applied,
    }


def initial_status_for_item(root: Path, state: dict[str, Any], item: dict[str, Any]) -> str:
    initial = item.get("initial_status")
    if initial in {"queued", "revalidation_queued", "source_blocked"}:
        return initial
    _ledger_ref, ledger_path = verified_ref(root, state["ledger_ref"], "campaign ledger")
    ledger = load_json(ledger_path, "campaign ledger")
    for row in ledger.get("items") or []:
        if isinstance(row, dict) and row.get("question_id") == item["question_id"]:
            value = row.get("initial_status")
            if value in {"queued", "revalidation_queued", "source_blocked"}:
                return value
            break
    return "queued" if item.get("source_ready") else "source_blocked"


def validate_source_slice(
    root: Path,
    out: Path,
    item: dict[str, Any],
    raw_path: str | None,
    *,
    require_coordinate_space: bool = False,
    artifact_allowed: Path | None = None,
) -> dict[str, str]:
    if not raw_path:
        raise CampaignError("source_slice_required", "A hash-bound source slice is required")
    source_ref = item.get("source_ref")
    if not isinstance(source_ref, dict) or not source_ref.get("sha256"):
        raise CampaignError("source_slice_source_missing", "The blocked item has no hash-bound source")
    source_sha = ensure_sha(source_ref["sha256"], "source_sha256")
    cache_root = (out / "source-cache" / source_sha).resolve()
    path = inside(Path(raw_path), cache_root)
    payload = load_json(path, "source slice")
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("kind") != SOURCE_SLICE_KIND
        or payload.get("question_id") != item["question_id"]
        or payload.get("source_sha256") != source_sha
    ):
        raise CampaignError("source_slice_binding_mismatch", "Source slice does not bind the blocked item and source hash")
    if not isinstance(payload.get("locator"), dict) or not payload["locator"]:
        raise CampaignError("source_slice_locator_missing", "Source slice must include a non-empty locator")
    locator = payload["locator"]
    if not isinstance(locator.get("type"), str) or not locator["type"].strip():
        raise CampaignError("source_slice_locator_type_missing", "Source slice locator.type is required")
    if require_coordinate_space and (
        not isinstance(locator.get("coordinate_space"), str)
        or not locator["coordinate_space"].strip()
    ):
        raise CampaignError(
            "source_slice_coordinate_space_missing",
            "New source slices must declare locator.coordinate_space",
        )
    _artifact_ref, artifact_path = verified_ref(root, payload.get("artifact_ref"), "source slice artifact")
    inside(artifact_path, artifact_allowed.resolve() if artifact_allowed is not None else cache_root)
    return file_ref(path)


def requeue_runtime_blocked(args: argparse.Namespace, root: Path, out: Path, state: dict[str, Any]) -> dict[str, Any]:
    qid = ensure_id(args.requeue_blocked, "requeue_blocked")
    if not args.reason:
        raise CampaignError("requeue_reason_required", "Requeueing a blocked item requires --reason")
    item = state["items"].get(qid)
    if not isinstance(item, dict):
        raise CampaignError("question_not_found", f"Question is not in campaign: {qid}")
    if item.get("status") == "blocked" and item.get("defects") == ["writer_failed"] and int(item.get("revision_no", 0)) == 0:
        item["revision_no"] = 1
        item["status"] = "repair_queued"
        item["candidate"] = None
        item["audit"] = None
        item["defects"] = []
        save_state(out, state, "writer_failed_requeued", question_id=qid, reason=args.reason)
        return {
            "ok": True,
            "campaign_id": args.campaign,
            "recovered": True,
            "requeued": qid,
            "phase": "repair",
            "counts": status_counts(state),
        }
    defects = item.get("defects")
    interruption_retry = isinstance(defects, list) and sorted(defects) == sorted(EXECUTOR_INTERRUPTION_DEFECTS)
    runtime_retry = defects in (["runtime_attempts_exhausted"], ["dispatch_attempts_exhausted"])
    if item.get("status") != "blocked" or not (runtime_retry or interruption_retry):
        raise CampaignError("blocked_item_not_recoverable", "Only runtime-attempt exhaustion or an explicitly interrupted executor can be requeued")
    revision = int(item.get("revision_no", 0))
    attempt_key = f"executor:r{revision}"
    minimum_attempts = 1 if interruption_retry else 2
    if int(item.get("dispatch_attempts", {}).get(attempt_key, 0)) < minimum_attempts:
        raise CampaignError("blocked_attempt_history_invalid", "Blocked item does not have exhausted executor attempts")
    source_slice_ref = validate_source_slice(root, out, item, args.source_slice)
    item["source_slice_ref"] = source_slice_ref
    item["status"] = initial_status_for_item(root, state, item)
    item["dispatch_attempts"][attempt_key] = 0
    item["candidate"] = None
    item["audit"] = None
    item["defects"] = []
    save_state(
        out,
        state,
        "executor_interrupted_requeued" if interruption_retry else "runtime_blocked_requeued",
        question_id=qid,
        reason=args.reason,
        source_slice_ref=source_slice_ref,
    )
    return {
        "ok": True,
        "campaign_id": args.campaign,
        "recovered": True,
        "requeued": qid,
        "counts": status_counts(state),
    }


def revalidate_passed(args: argparse.Namespace, out: Path, state: dict[str, Any]) -> dict[str, Any]:
    qid = ensure_id(args.revalidate_passed, "revalidate_passed")
    if not args.reason:
        raise CampaignError("revalidation_reason_required", "Revalidating a passed item requires --reason")
    item = state["items"].get(qid)
    if not isinstance(item, dict):
        raise CampaignError("question_not_found", f"Question is not in campaign: {qid}")
    previous_status = item.get("status")
    history = item.setdefault("revalidation_history", [])
    interrupted_transition = (
        previous_status == "blocked"
        and item.get("defects") == ["dispatch_attempts_exhausted"]
        and bool(history)
        and history[-1].get("previous_status") in {"passed", "no_change_passed"}
        and item.get("candidate") is None
        and item.get("audit") is None
    )
    if previous_status not in {"passed", "no_change_passed"} and not interrupted_transition:
        raise CampaignError("passed_item_required", "Only passed or no_change_passed items can enter controlled revalidation")
    if interrupted_transition:
        previous_status = history[-1]["previous_status"]
    else:
        history.append({
            "requested_at": now(),
            "reason": args.reason,
            "previous_status": previous_status,
            "candidate": item.get("candidate"),
            "audit": item.get("audit"),
            "apply_receipt": item.get("apply_receipt"),
            "dispatch_attempts": item.get("dispatch_attempts", {}),
        })
    item["status"] = "revalidation_queued"
    item["revision_no"] = 0
    item["dispatch_attempts"] = {}
    item["candidate"] = None
    item["audit"] = None
    item["apply_receipt"] = None
    item["defects"] = []
    save_state(
        out,
        state,
        "passed_item_revalidation_transition_recovered" if interrupted_transition else "passed_item_revalidation_queued",
        question_id=qid,
        reason=args.reason,
        previous_status=previous_status,
    )
    return {
        "ok": True,
        "campaign_id": args.campaign,
        "recovered": True,
        "revalidation_queued": qid,
        "previous_status": previous_status,
        "counts": status_counts(state),
    }


def recover(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    out = campaign_root(root, args.campaign)
    with state_mutex(out):
        state = load_state(out)
        active = state.get("active_run")
        if not isinstance(active, dict):
            if args.requeue_blocked:
                return requeue_runtime_blocked(args, root, out, state)
            if args.revalidate_passed:
                if args.source_slice:
                    raise CampaignError("source_slice_not_allowed", "Passed-item revalidation does not accept --source-slice")
                return revalidate_passed(args, out, state)
            return {"ok": True, "campaign_id": args.campaign, "recovered": False, "counts": status_counts(state)}
        if args.requeue_blocked or args.revalidate_passed or args.source_slice:
            raise CampaignError("active_run_exists", "Item requeue or revalidation requires no active run")
        if args.run_id and active.get("run_id") != args.run_id:
            raise CampaignError("run_id_mismatch", "recover run_id does not match the active run")
        if active.get("phase") == "applying":
            baseline_revision = state["state_revision"]
            baseline_active = json.loads(json.dumps(active))
            snapshot = json.loads(json.dumps(state))
        else:
            for qid in active["question_ids"]:
                item = state["items"][qid]
                pre = active["pre_statuses"][qid]
                attempt_key = f"{active['phase']}:r{active['revision_no']}"
                if int(item["dispatch_attempts"].get(attempt_key, 0)) >= 2:
                    item["status"] = "blocked"
                    item["defects"] = ["runtime_attempts_exhausted"]
                else:
                    item["status"] = pre
                    item["defects"] = [args.reason or "worker_lost"]
            state["active_run"] = None
            save_state(out, state, "worker_recovered", run_id=active["run_id"], reason=args.reason or "worker_lost")
            return {"ok": True, "campaign_id": args.campaign, "recovered": True, "counts": status_counts(state)}
    # Applying recovery uses the same idempotent writer path as submit.
    recovered = finish_apply(root, out, snapshot)
    with state_mutex(out):
        current = load_state(out)
        if current.get("active_run") != baseline_active or current.get("state_revision") != baseline_revision:
            raise CampaignError("state_changed_during_recovery", "Campaign state changed during apply recovery")
        snapshot["state_revision"] = current["state_revision"]
        save_state(out, snapshot, "apply_recovered", results=recovered)
    return {"ok": True, "campaign_id": args.campaign, "recovered": True, "results": recovered, "counts": status_counts(snapshot)}


def status_counts(state: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in state["items"].values():
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return dict(sorted(counts.items()))


def compact_status(state: dict[str, Any]) -> dict[str, Any]:
    counts = status_counts(state)
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "campaign_id": state["campaign_id"],
        "state_revision": state["state_revision"],
        "counts": counts,
        "active_run": state.get("active_run"),
        "complete": sum(counts.get(x, 0) for x in TERMINAL) == len(state["items"]) and counts.get("blocked", 0) == 0,
        "blocked": counts.get("blocked", 0) + counts.get("source_blocked", 0),
    }


def campaign_status(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    out = campaign_root(root, args.campaign)
    with state_mutex(out):
        return compact_status(load_state(out))


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Simple v3 one-shot agent batch orchestrator")
    value.add_argument("--kb-root", help=argparse.SUPPRESS)
    commands = value.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--campaign", required=True)
    init.add_argument("--ledger", required=True)
    init.add_argument("--authorization", required=True)
    init.add_argument("--profile", required=True)
    init.add_argument("--batch-size", type=int, default=5)
    dispatch_cmd = commands.add_parser("dispatch")
    dispatch_cmd.add_argument("--campaign", required=True)
    submit_cmd = commands.add_parser("submit")
    submit_cmd.add_argument("--campaign", required=True)
    submit_cmd.add_argument("--result", required=True)
    recover_cmd = commands.add_parser("recover")
    recover_cmd.add_argument("--campaign", required=True)
    recover_cmd.add_argument("--run-id")
    recover_cmd.add_argument("--reason")
    recover_target = recover_cmd.add_mutually_exclusive_group()
    recover_target.add_argument("--requeue-blocked")
    recover_target.add_argument("--revalidate-passed")
    recover_cmd.add_argument("--source-slice")
    status_cmd = commands.add_parser("status")
    status_cmd.add_argument("--campaign", required=True)
    for command in (init, dispatch_cmd, submit_cmd, recover_cmd, status_cmd):
        command.add_argument("--json", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = kb_root(args.kb_root)
    try:
        function = {
            "init": initialize,
            "dispatch": dispatch,
            "submit": submit,
            "recover": recover,
            "status": campaign_status,
        }[args.command]
        payload = function(args, root)
        code = 0
    except CampaignError as exc:
        payload = {"ok": False, "error": exc.code, "message": str(exc), **exc.details}
        code = 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if not args.json else None))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
