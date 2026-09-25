#!/usr/bin/env python3
"""Atomic cross-campaign source SHA lock for the P0/P1 dispatcher."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 1, "updated_at": now(), "locks": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("locks"), list):
        raise ValueError(f"invalid source lock ledger: {path}")
    return payload


def root_path(raw: str) -> Path:
    return Path(raw).resolve()


def campaign_batch(root: Path, campaign: str, batch_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    state_path = root / "skills/_ops/runtime/state/role_d_curation_campaigns" / campaign / "campaign.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    batch = next((value for value in state.get("batches", []) if value.get("batch_id") == batch_id), None)
    if not isinstance(batch, dict):
        raise KeyError(f"batch not found: {campaign}/{batch_id}")
    return state, batch


def claim(args: argparse.Namespace) -> dict[str, Any]:
    root = root_path(args.kb_root)
    state, batch = campaign_batch(root, args.campaign, args.batch)
    source_raw = str(batch.get("source_path") or "").replace("\\", "/")
    if not source_raw:
        raise ValueError("source lock requires a source_path")
    marker = "source-library/"
    source_key = source_raw[source_raw.find(marker) :] if marker in source_raw else source_raw
    source = (root / source_key).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    source_sha = digest(source)
    ledger_path = root / args.ledger
    payload = load(ledger_path)
    active = [value for value in payload["locks"] if isinstance(value, dict) and value.get("released_at") is None]
    conflicts = [value for value in active if value.get("source_sha256") == source_sha and not (
        value.get("campaign_id") == args.campaign and value.get("batch_id") == args.batch and value.get("runner_id") == args.runner
    )]
    if conflicts:
        raise RuntimeError("source_lock_conflict: " + json.dumps(conflicts, ensure_ascii=False, separators=(",", ":")))
    existing = next((value for value in active if value.get("campaign_id") == args.campaign and value.get("batch_id") == args.batch and value.get("runner_id") == args.runner), None)
    if existing:
        return {"ok": True, "idempotent": True, "lock": existing, "ledger": str(ledger_path)}
    ordinals = [int(state["items"][qid].get("queue_ordinal") or 0) for qid in batch.get("question_ids", []) if qid in state.get("items", {})]
    lock = {
        "lock_id": uuid.uuid4().hex,
        "campaign_id": args.campaign,
        "batch_id": args.batch,
        "runner_id": args.runner,
        "source_path": source_key,
        "source_sha256": source_sha,
        "queue_ref_sha256": state.get("ledger_sha256"),
        "queue_ordinal_min": min(ordinals) if ordinals else None,
        "claimed_at": now(),
        "released_at": None,
    }
    payload["updated_at"] = now()
    payload["locks"].append(lock)
    atomic_write(ledger_path, payload)
    return {"ok": True, "idempotent": False, "lock": lock, "ledger": str(ledger_path)}


def release(args: argparse.Namespace) -> dict[str, Any]:
    root = root_path(args.kb_root)
    ledger_path = root / args.ledger
    payload = load(ledger_path)
    changed = 0
    for value in payload["locks"]:
        if not isinstance(value, dict) or value.get("released_at") is not None:
            continue
        if value.get("campaign_id") == args.campaign and value.get("batch_id") == args.batch and value.get("runner_id") == args.runner:
            value["released_at"] = now()
            changed += 1
    if changed:
        payload["updated_at"] = now()
        atomic_write(ledger_path, payload)
    return {"ok": True, "released": changed, "ledger": str(ledger_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb-root", default=".")
    parser.add_argument("--ledger", default="tmp/tasks/p0p1-curation-20260722/locks/source_locks.json")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("claim", "release"):
        p = sub.add_parser(name)
        p.add_argument("--campaign", required=True)
        p.add_argument("--batch", required=True)
        p.add_argument("--runner", required=True)
    status = sub.add_parser("status")
    args = parser.parse_args()
    try:
        result = claim(args) if args.command == "claim" else release(args) if args.command == "release" else {"ok": True, "ledger": str(root_path(args.kb_root) / args.ledger), "locks": load(root_path(args.kb_root) / args.ledger).get("locks", [])}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "type": type(exc).__name__}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
