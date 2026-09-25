#!/usr/bin/env python3
"""Retire P0/P1 mission v2 and build simple batch v3 campaigns."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any


CAMPAIGNS = {
    "电磁学": "p0p1-electromagnetics",
    "光学": "p0p1-optics",
    "力学": "p0p1-mechanics",
    "热学": "p0p1-thermal",
    "综合": "p0p1-comprehensive",
}
REVALIDATE_IDS = {
    "C0000888",
    "C0000891",
    "C0000913",
    "MA0000907",
    "MA0000908",
    "MA0000281",
    "B0000175",
}
EXPECTED_TOTAL = 833
EXPECTED_SOURCE_BLOCKED = 395
SCRIPT = Path(__file__).resolve()
SKILLS_ROOT = SCRIPT.parents[3]
ORCHESTRATOR_PATH = SKILLS_ROOT / "_shared/scripts/agent_mission_orchestrator.py"
SPEC = importlib.util.spec_from_file_location("batch_v3_orchestrator", ORCHESTRATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
orchestrator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(orchestrator)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_ref(path: Path, root: Path) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        display = resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        display = str(resolved)
    return {"path": display, "sha256": sha256_file(resolved), "size_bytes": resolved.stat().st_size}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def normalized_relative(value: str) -> str:
    path = Path(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ":" in value:
        raise ValueError(f"unsafe repository-relative path: {value}")
    return path.as_posix()


def capability(item: dict[str, Any]) -> str:
    severities = {str(value).upper() for value in item.get("reported_severities", [])}
    if "P0" in severities:
        return "complex"
    categories = {
        str(reason.get("category", "")).lower()
        for reason in item.get("reasons", [])
        if isinstance(reason, dict)
    }
    risky = {"stem", "formula", "figure", "image", "image_semantics", "physics", "unanswerable"}
    return "ambiguous" if categories & risky else "bounded"


def transform_ledger(kb_root: Path, source: dict[str, Any], campaign_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    transformed: list[dict[str, Any]] = []
    drift: list[dict[str, Any]] = []
    for item in source.get("items", []):
        qid = str(item["question_id"])
        target_rel = normalized_relative(str(item["target_path"]))
        target = (kb_root / target_rel).resolve()
        target.relative_to(kb_root.resolve())
        if not target.is_file():
            raise FileNotFoundError(target)
        current_target_sha = sha256_file(target)
        legacy_target_sha = str(item.get("current_sha256") or "")
        if legacy_target_sha != current_target_sha:
            drift.append({
                "question_id": qid,
                "kind": "target_baseline_updated",
                "legacy_sha256": legacy_target_sha,
                "v3_sha256": current_target_sha,
            })

        source_ready = item.get("source_state") == "source_ready"
        source_ref: dict[str, Any] | None = None
        if source_ready:
            source_rel = normalized_relative(str(item.get("source_key") or item.get("source_path") or ""))
            source_path = (kb_root / source_rel).resolve()
            expected_source_sha = str(item.get("source_sha256") or "")
            if source_path.is_file() and sha256_file(source_path) == expected_source_sha:
                source_ref = {
                    "path_id": "project.root",
                    "relative_path": source_rel,
                    "sha256": expected_source_sha,
                }
            else:
                source_ready = False
                drift.append({
                    "question_id": qid,
                    "kind": "source_hash_or_path_drift",
                    "relative_path": source_rel,
                    "expected_sha256": expected_source_sha,
                })

        initial_status = "source_blocked" if not source_ready else (
            "revalidation_queued" if qid in REVALIDATE_IDS else "queued"
        )
        transformed.append({
            "question_id": qid,
            "queue_ordinal": int(item["queue_ordinal"]),
            "severity": "P0" if "P0" in item.get("reported_severities", []) else "P1",
            "capability": capability(item),
            "source_ready": source_ready,
            "target_ref": {
                "path_id": "project.root",
                "relative_path": target_rel,
                "sha256": current_target_sha,
            },
            "source_ref": source_ref,
            "initial_status": initial_status,
            "issue_refs": item.get("reasons", []),
            "report_refs": item.get("report_references", []),
            "asset_refs": item.get("asset_refs", []),
        })
    transformed.sort(key=lambda row: row["queue_ordinal"])
    if [row["queue_ordinal"] for row in transformed] != list(range(len(transformed))):
        raise ValueError(f"non-contiguous queue ordinals: {campaign_id}")
    return {
        "schema_version": 3,
        "kind": "campaign_ledger",
        "campaign_id": campaign_id,
        "scope_key": source.get("volume"),
        "source_ledger": source.get("campaign_id"),
        "items": transformed,
    }, drift


def build_grant(prior: dict[str, Any], campaign_id: str, ledger_sha: str) -> dict[str, Any]:
    required = (
        "authorized_by",
        "authorization_basis",
        "authorization_record_path",
        "authorization_record_sha256",
        "allowed_fields",
        "writer",
        "issued_at",
        "expires_at",
    )
    missing = [field for field in required if not prior.get(field)]
    if missing or prior.get("destructive") is not False:
        raise ValueError(f"prior teacher authorization is incomplete: {campaign_id}: {missing}")
    record_path = Path(str(prior["authorization_record_path"])).resolve()
    if not record_path.is_file() or sha256_file(record_path) != prior["authorization_record_sha256"]:
        raise ValueError(f"teacher authorization record hash mismatch: {campaign_id}")
    return {
        "schema_version": 3,
        "kind": "authorization_grant",
        "grant_id": f"grant-{campaign_id}-v3",
        "campaign_id": campaign_id,
        "ledger_sha256": ledger_sha,
        **{field: prior[field] for field in required},
        "destructive": False,
    }


def inventory(paths: list[Path], root: Path) -> list[dict[str, Any]]:
    files: dict[str, Path] = {}
    for entry in paths:
        if entry.is_file():
            files[str(entry.resolve()).casefold()] = entry
        elif entry.is_dir():
            for child in entry.rglob("*"):
                if child.is_file():
                    files[str(child.resolve()).casefold()] = child
    return [file_ref(path, root) for path in sorted(files.values(), key=lambda value: str(value).casefold())]


def migrate(args: argparse.Namespace) -> dict[str, Any]:
    kb_root = Path(args.kb_root).resolve()
    manifest_path = Path(args.manifest).resolve()
    profile_path = Path(args.profile).resolve()
    prior_grants_root = Path(args.prior_grants_root).resolve()
    output_root = Path(args.output_root).resolve()
    manifest = load_json(manifest_path)
    profile = load_json(profile_path)
    if profile.get("schema_version") != 3:
        raise ValueError("campaign profile must be v3")

    volumes = {str(row["volume"]): row for row in manifest.get("volumes", [])}
    if set(volumes) != set(CAMPAIGNS):
        raise ValueError("P0/P1 manifest volume set changed")
    campaigns: list[dict[str, Any]] = []
    total = 0
    blocked = 0
    all_ordinals: dict[str, list[int]] = {}
    all_drift: list[dict[str, Any]] = []
    for volume, campaign_id in CAMPAIGNS.items():
        legacy_ledger_path = Path(str(volumes[volume]["ledger"])).resolve()
        legacy_ledger = load_json(legacy_ledger_path)
        ledger, drift = transform_ledger(kb_root, legacy_ledger, campaign_id)
        ledger_path = output_root / campaign_id / "ledger.v3.json"
        atomic_json(ledger_path, ledger)
        ledger_sha = sha256_file(ledger_path)
        prior_grant = load_json(prior_grants_root / campaign_id / "authorization-grant.v2.json")
        grant = build_grant(prior_grant, campaign_id, ledger_sha)
        grant_path = output_root / campaign_id / "authorization-grant.v3.json"
        atomic_json(grant_path, grant)

        state_file = kb_root / "skills/_ops/runtime/state/agent_batches_v3" / campaign_id / "campaign.json"
        if state_file.exists():
            with orchestrator.state_mutex(state_file.parent):
                state = load_json(state_file)
                if (state.get("ledger_ref") or {}).get("sha256") != ledger_sha:
                    raise RuntimeError(f"existing v3 campaign binds another ledger: {campaign_id}")
                if state.get("active_run") is not None:
                    raise RuntimeError(f"refusing to refresh an active v3 campaign: {campaign_id}")
                initial_by_id = {row["question_id"]: row["initial_status"] for row in ledger["items"]}
                if set(state.get("items", {})) != set(initial_by_id) or any(
                    state["items"][qid].get("status") != status
                    for qid, status in initial_by_id.items()
                ):
                    raise RuntimeError(f"refusing to refresh a progressed v3 campaign: {campaign_id}")
                state["authorization_ref"] = orchestrator.file_ref(grant_path)
                state["profile_ref"] = orchestrator.file_ref(profile_path)
                state["profile"] = profile
                orchestrator.save_state(state_file.parent, state, "migration_inputs_refreshed")
                state = load_json(state_file)
            action = "refreshed"
        else:
            orchestrator.initialize(
                SimpleNamespace(
                    campaign=campaign_id,
                    ledger=str(ledger_path),
                    authorization=str(grant_path),
                    profile=str(profile_path),
                    batch_size=5,
                ),
                kb_root,
            )
            state = load_json(state_file)
            action = "initialized"

        rows = list(ledger["items"])
        total += len(rows)
        blocked += sum(row["initial_status"] == "source_blocked" for row in rows)
        all_ordinals[campaign_id] = [row["queue_ordinal"] for row in rows]
        all_drift.extend({"campaign_id": campaign_id, **row} for row in drift)
        campaigns.append({
            "campaign_id": campaign_id,
            "volume": volume,
            "action": action,
            "item_count": len(rows),
            "source_blocked": sum(row["initial_status"] == "source_blocked" for row in rows),
            "revalidation_queued": sum(row["initial_status"] == "revalidation_queued" for row in rows),
            "ledger": file_ref(ledger_path, kb_root),
            "authorization": file_ref(grant_path, kb_root),
            "state": file_ref(state_file, kb_root),
        })

    if total != EXPECTED_TOTAL or blocked != EXPECTED_SOURCE_BLOCKED:
        raise RuntimeError(f"migration count mismatch: total={total}, source_blocked={blocked}")

    retirement_paths = [
        kb_root / "skills/_ops/runtime/state/agent_missions",
        prior_grants_root,
        kb_root / "skills/_ops/runtime/backups/20260723T-framework-v3",
        kb_root / "skills/_shared/scripts/import_v1_mission_adapter.py",
        kb_root / "skills/learn/exercise-solution-curation/scripts/migrate_p0p1_mission_v2.py",
    ]
    retired_inventory = inventory(retirement_paths, kb_root)
    retirement = {
        "schema_version": 3,
        "kind": "mission_v2_retirement",
        "retired_at": utc_now(),
        "execution_disabled": True,
        "policy": "retain historical source, state and artifacts as read-only evidence; reject them as v3 cards or receipts",
        "inventory": retired_inventory,
        "campaigns": campaigns,
        "migration_counts": {
            "total": total,
            "source_ready": total - blocked,
            "source_blocked": blocked,
            "target_baseline_updates": sum(row["kind"] == "target_baseline_updated" for row in all_drift),
            "source_drift": sum(row["kind"] == "source_hash_or_path_drift" for row in all_drift),
        },
        "drift": all_drift,
        "queue_ordinals_sha256": hashlib.sha256(
            json.dumps(all_ordinals, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "canary_order": ["p0p1-optics", "p0p1-thermal"],
    }
    retirement_path = Path(args.retirement_report).resolve()
    atomic_json(retirement_path, retirement)
    marker_path = kb_root / "skills/_ops/runtime/state/agent_missions/.retired-v2.json"
    atomic_json(marker_path, {
        "schema_version": 3,
        "kind": "retired_control_plane_marker",
        "execution_disabled": True,
        "retirement_report": file_ref(retirement_path, kb_root),
    })
    return {
        "ok": True,
        "campaigns": campaigns,
        "counts": retirement["migration_counts"],
        "retirement_report": file_ref(retirement_path, kb_root),
        "retirement_marker": file_ref(marker_path, kb_root),
        "retired_file_count": len(retired_inventory),
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Migrate the stable 833-item P0/P1 ledger to batch v3")
    value.add_argument("--kb-root", required=True)
    value.add_argument("--manifest", required=True)
    value.add_argument("--profile", required=True)
    value.add_argument("--prior-grants-root", required=True)
    value.add_argument("--output-root", required=True)
    value.add_argument("--retirement-report", required=True)
    value.add_argument("--json", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    try:
        result = migrate(parser().parse_args(argv))
        code = 0
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        code = 1
    print(json.dumps(result, ensure_ascii=False, indent=None if "--json" in (argv or []) else 2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
