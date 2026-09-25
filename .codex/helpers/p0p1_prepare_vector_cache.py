#!/usr/bin/env python3
"""Prepare a sequential, cacheable vector render set for one active batch."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SHARED_SCRIPTS = Path(__file__).resolve().parents[2] / "skills/_shared/scripts"
sys.path.insert(0, str(SHARED_SCRIPTS))
from libreoffice_runner import render_vector_full_frame


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_key(raw: str) -> str:
    value = str(raw or "").replace("\\", "/")
    marker = "source-library/"
    return value[value.find(marker) :] if marker in value else value.removeprefix("../")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb-root", default=".")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--batch", required=True)
    args = parser.parse_args()
    root = Path(args.kb_root).resolve()
    campaign_root = root / "skills/_ops/runtime/state/role_d_curation_campaigns" / args.campaign
    state = json.loads((campaign_root / "campaign.json").read_text(encoding="utf-8"))
    batch = next(value for value in state["batches"] if value["batch_id"] == args.batch)
    wanted = set(batch["question_ids"])
    manifest = None
    for path in (root / "skills/_ops/runtime/reports/role_d_curation/source_groups").rglob("manifest.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if source_key(payload.get("source_path")) == source_key(batch.get("source_path")):
            manifest = payload
            break
    if manifest is None:
        raise FileNotFoundError(f"source manifest unavailable for {args.campaign}/{args.batch}")
    questions = {str(value.get("question_id")): value for value in manifest.get("questions") or []}
    cache_dir = campaign_root / "work" / args.batch / "formula_png_native"
    cache_path = cache_dir / "wmf_render_manifest.json"
    cache_dir.mkdir(parents=True, exist_ok=True)
    prior = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else []
    by_hash = {str(value.get("source_sha256")): value for value in prior if isinstance(value, dict)}
    rendered = []
    for qid in batch["question_ids"]:
        question = questions.get(qid)
        if question is None:
            raise KeyError(f"question missing from source manifest: {qid}")
        output_dir = campaign_root / "work" / args.batch / "evidence" / qid / "visual"
        for occurrence, relationship in enumerate(question.get("relationships") or [], start=1):
            extracted = root / str(relationship.get("extracted_path") or "")
            if extracted.suffix.lower() not in {".wmf", ".emf"} or not extracted.is_file():
                continue
            source_hash = str(relationship.get("sha256") or sha256(extracted))
            if source_hash in by_hash and Path(str(by_hash[source_hash].get("output_path") or "")).is_file():
                continue
            safe_name = f"{qid}_{occurrence:04d}_{relationship.get('relationship_id','r')}.png"
            result = render_vector_full_frame(extracted, output_dir, output_name=safe_name)
            result["status"] = "rendered"
            result["source_sha256"] = source_hash
            result["output_path"] = Path(str(result["output_path"])).resolve().relative_to(root).as_posix()
            by_hash[source_hash] = result
            rendered.append({"question_id": qid, "source_sha256": source_hash, "output_path": result["output_path"]})
            # Persist after every render so a single slow/malformed vector
            # cannot discard the cache already prepared for earlier assets.
            cache_path.write_text(json.dumps(list(by_hash.values()), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cache_path.write_text(json.dumps(list(by_hash.values()), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "campaign": args.campaign, "batch": args.batch, "cache": cache_path.relative_to(root).as_posix(), "rendered": rendered, "entries": len(by_hash)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
