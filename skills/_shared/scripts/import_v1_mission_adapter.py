#!/usr/bin/env python3
"""Lossless compatibility adapter from Role A import contract v1 to v2 envelopes."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any


CONTRACT_PATH = Path(__file__).with_name("import_batch_contract.py")
SPEC = importlib.util.spec_from_file_location("import_batch_contract", CONTRACT_PATH)
assert SPEC and SPEC.loader
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


def file_ref(path: str | Path) -> dict[str, Any]:
    source = Path(path).resolve()
    return {
        "path": str(source),
        "sha256": contract.sha256_file(source),
        "size_bytes": source.stat().st_size,
    }


def load_object_with_ref(path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    source = Path(path).resolve()
    raw = source.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value, {
        "path": str(source),
        "sha256": contract.sha256_bytes(raw),
        "size_bytes": len(raw),
    }


def adapt_task_card(
    card: dict[str, Any],
    *,
    campaign_id: str,
    scope_key: str,
    task_card_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract.validate_task_card(card)
    legacy_sha = (
        str(task_card_ref["sha256"])
        if task_card_ref is not None
        else contract.sha256_bytes(contract.canonical_json_bytes(card))
    )
    return {
        "schema_version": 2,
        "adapter": "role-a-import-v1",
        "adapter_version": 1,
        "campaign_id": campaign_id,
        "scope_key": scope_key,
        "task_id": card["task_id"],
        "batch_id": card["batch_id"],
        "revision_no": 0,
        "dispatch_attempt_no": card["attempt_no"],
        "worker_instance_id": card["worker_instance_id"],
        "fork_context": False,
        "phase": f"legacy-import-{card['agent_role']}",
        "role_plan": ["role-a"],
        "required_capability": "bounded",
        "queue_sha256": card["queue_ref"]["sha256"],
        "queue_ordinal": card["queue_ordinal"],
        "owner_skill": card["owner_skill"],
        "payload_class": card["payload_class"],
        "input_refs": card["input_refs"],
        "write_scope": card.get("write_targets", []),
        "result_path": card["result_path"],
        "legacy_task_card": deepcopy(card),
        "legacy_task_card_ref": deepcopy(task_card_ref),
        "task_card_sha256": legacy_sha,
    }


def adapt_compact_result(
    result: dict[str, Any],
    adapted_card: dict[str, Any],
    *,
    compact_result_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if adapted_card.get("schema_version") != 2 or adapted_card.get("adapter") != "role-a-import-v1":
        raise ValueError("adapted_card must be a Role A import v1 adapter envelope")
    legacy_card = adapted_card.get("legacy_task_card")
    if not isinstance(legacy_card, dict):
        raise ValueError("adapted_card lacks legacy_task_card")
    legacy_card_ref = adapted_card.get("legacy_task_card_ref")
    if legacy_card_ref is not None:
        if not isinstance(legacy_card_ref, dict) or legacy_card_ref.get("sha256") != adapted_card.get("task_card_sha256"):
            raise ValueError("adapted_card legacy task-card ref/hash binding is invalid")
        legacy_card_path = Path(str(legacy_card_ref.get("path", "")))
        if (
            not legacy_card_path.is_file()
            or contract.sha256_file(legacy_card_path) != legacy_card_ref.get("sha256")
            or legacy_card_path.stat().st_size != legacy_card_ref.get("size_bytes")
        ):
            raise ValueError("legacy task-card file has drifted from its adapter binding")
    contract.validate_compact_result(result)
    contract.validate_result_against_task_card(
        result,
        legacy_card,
        task_card_sha256=str(adapted_card["task_card_sha256"]),
    )
    artifacts = result.get("artifacts", [])
    adapted = {
        "schema_version": 2,
        "adapter": "role-a-import-v1",
        "task_id": result["task_id"],
        "batch_id": result["batch_id"],
        "revision_no": 0,
        "dispatch_attempt_no": result["attempt_no"],
        "worker_instance_id": result["worker_instance_id"],
        "task_card_sha256": adapted_card["task_card_sha256"],
        "legacy_status": result["status"],
        "status": result["status"],
        "legacy_compact_result": deepcopy(result),
        "legacy_compact_result_ref": deepcopy(compact_result_ref),
        "defect_codes": [issue.get("code", "legacy_issue") for issue in result.get("issues", []) if isinstance(issue, dict)],
        "issues": deepcopy(result.get("issues", [])),
        "metrics": deepcopy(result.get("metrics", {})),
        "artifacts": artifacts,
        "receipts": [],
        "summary": result["summary"],
        "owner_skill": result["owner_skill"],
        "agent_role": result["agent_role"],
        "queue_sha256": result["queue_sha256"],
        "queue_ordinal": result["queue_ordinal"],
    }
    if "next_action" in result:
        adapted["next_action"] = result["next_action"]
    return adapted


def load_object(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    task = subparsers.add_parser("task-card")
    task.add_argument("--input", required=True)
    task.add_argument("--campaign", required=True)
    task.add_argument("--scope-key", required=True)
    task.add_argument("--out", required=True)
    task.add_argument("--json", action="store_true")
    result = subparsers.add_parser("compact-result")
    result.add_argument("--input", required=True)
    result.add_argument("--adapted-task-card", required=True)
    result.add_argument("--out", required=True)
    result.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "task-card":
            source, source_ref = load_object_with_ref(args.input)
            payload = adapt_task_card(
                source,
                campaign_id=args.campaign,
                scope_key=args.scope_key,
                task_card_ref=source_ref,
            )
        else:
            source, source_ref = load_object_with_ref(args.input)
            payload = adapt_compact_result(
                source,
                load_object(args.adapted_task_card),
                compact_result_ref=source_ref,
            )
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        response = {"ok": True, "output": str(output.resolve()), "schema_version": 2}
        print(json.dumps(response if args.json else payload, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, contract.ContractError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
