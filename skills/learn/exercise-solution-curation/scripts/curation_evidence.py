"""Full-resolution evidence preparation for Role D curation campaigns.

The public seam is intentionally small: prepare one question, record one
segment, find the next pending segment, and summarize the budget.  DOCX
relationship details, vector rendering, hashing, deduplication, segmentation,
and ledger persistence stay inside this module.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
BUDGET_PROFILE = "quality_stable_60"
MAX_UNIQUE_IMAGES = 16
MAX_VLM_IMAGES = 12
CONTEXT_BUDGET_RATIO = 0.60
RUNTIME_RESERVE_RATIO = 0.20
DEFAULT_CONTEXT_WINDOW_TOKENS = 272_000
RASTER_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
VECTOR_SUFFIXES = {".wmf", ".emf"}
REVIEW_STATUSES = {"confirmed", "resolved", "unresolved", "high_risk", "not_applicable"}

SHARED_SCRIPTS = Path(__file__).resolve().parents[3] / "_shared" / "scripts"
if str(SHARED_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_SCRIPTS))
BEMARKDOWN_SCRIPTS = Path(__file__).resolve().parents[3] / "import" / "bemarkdown" / "scripts"
if str(BEMARKDOWN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(BEMARKDOWN_SCRIPTS))

from libreoffice_runner import render_vector_full_frame  # noqa: E402
from formula_ensemble import run_formula_ensemble  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def resolve_path(kb_root: Path, raw: str | Path | None) -> Path | None:
    if raw is None or not str(raw).strip():
        return None
    path = Path(str(raw))
    if not path.is_absolute():
        path = kb_root / path
    path = path.resolve()
    try:
        path.relative_to(kb_root.resolve())
    except ValueError as exc:
        raise ValueError(f"evidence path escapes kb root: {raw}") from exc
    return path


def relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def image_dimensions(path: Path) -> tuple[int, int]:
    suffix = path.suffix.lower()
    if suffix == ".png":
        header = path.read_bytes()[:24]
        if len(header) >= 24 and header[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", header[16:24])
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception as exc:
        raise ValueError(f"cannot determine image dimensions: {path}") from exc


def estimate_image_tokens(width: int, height: int) -> int:
    """Conservative high-detail tile proxy used for planning, not billing."""
    tiles = max(1, math.ceil(width / 512)) * max(1, math.ceil(height / 512))
    return 85 + 170 * tiles


def estimate_context_ratio(
    assets: list[dict[str, Any]],
    text_bytes: int,
    context_window_tokens: int,
) -> float:
    text_tokens = math.ceil(max(0, text_bytes) / 3)
    image_tokens = sum(estimate_image_tokens(int(asset["width"]), int(asset["height"])) for asset in assets)
    runtime_reserve = math.ceil(context_window_tokens * RUNTIME_RESERVE_RATIO)
    return round((text_tokens + image_tokens + runtime_reserve) / context_window_tokens, 6)


def _question_from_manifest(kb_root: Path, batch: dict[str, Any], question_id: str) -> dict[str, Any]:
    manifest_path = resolve_path(kb_root, batch.get("source_manifest"))
    if manifest_path is None or not manifest_path.is_file():
        raise FileNotFoundError(f"source manifest unavailable for {question_id}")
    manifest = load_json_object(manifest_path, "source manifest")
    for question in manifest.get("questions") or []:
        if isinstance(question, dict) and str(question.get("question_id") or "").upper() == question_id:
            return question
    raise KeyError(f"question missing from source manifest: {question_id}")


def _relationship_candidates(
    kb_root: Path,
    campaign_root: Path,
    batch: dict[str, Any],
    question: dict[str, Any],
    question_id: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    work_root = campaign_root / "work" / batch["batch_id"]
    formula_manifest = work_root / "formula_assets.json"
    if formula_manifest.is_file():
        payload = json.loads(formula_manifest.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            for entry in payload:
                if not isinstance(entry, dict) or str(entry.get("q") or "").upper() != question_id:
                    continue
                source = work_root / "formula_assets" / str(entry.get("file") or "")
                if source.is_file():
                    candidates.append({
                        "source": source,
                        "relationship_id": str(entry.get("relationship_id") or ""),
                        "target": source.name,
                        "block": entry.get("paragraph"),
                        "role": "formula_or_diagram",
                        "expected_sha256": None,
                        "word_extent": entry.get("word_extent"),
                    })
    for relationship in question.get("relationships") or []:
        if not isinstance(relationship, dict):
            continue
        source = resolve_path(kb_root, relationship.get("extracted_path"))
        if source is None or not source.is_file():
            continue
        candidates.append({
            "source": source,
            "relationship_id": str(relationship.get("relationship_id") or ""),
            "target": str(relationship.get("target") or source.name),
            "block": relationship.get("block"),
            "role": "formula_or_diagram" if source.suffix.lower() in VECTOR_SUFFIXES else "main_image",
            "expected_sha256": relationship.get("sha256"),
            "ocr_candidate": relationship.get("ocr_candidate"),
            "word_extent": relationship.get("word_extent"),
        })
    return candidates


def _build_assets(
    kb_root: Path,
    evidence_root: Path,
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    render_cache_path = evidence_root.parents[1] / "formula_png_native" / "wmf_render_manifest.json"
    render_cache: dict[str, dict[str, Any]] = {}
    if render_cache_path.is_file():
        cached_payload = json.loads(render_cache_path.read_text(encoding="utf-8"))
        if isinstance(cached_payload, list):
            render_cache = {
                str(item.get("source_sha256") or ""): item
                for item in cached_payload
                if isinstance(item, dict) and item.get("status") == "rendered"
            }
    by_hash: dict[str, dict[str, Any]] = {}
    occurrence_count = 0
    for candidate in candidates:
        source = Path(candidate["source"]).resolve()
        source_hash = sha256_path(source)
        expected = str(candidate.get("expected_sha256") or "")
        if expected and expected != source_hash:
            raise ValueError(f"source hash mismatch: {source}")
        occurrence = {
            "relationship_id": candidate.get("relationship_id"),
            "target": candidate.get("target"),
            "block": candidate.get("block"),
        }
        occurrence_count += 1
        if source_hash in by_hash:
            by_hash[source_hash]["occurrences"].append(occurrence)
            continue
        suffix = source.suffix.lower()
        render_metadata: dict[str, Any] | None = None
        if suffix in VECTOR_SUFFIXES:
            cached_render = render_cache.get(source_hash)
            cached_output = Path(str((cached_render or {}).get("output_path") or ""))
            if cached_render and cached_output.is_file():
                render_metadata = cached_render
            else:
                render_metadata = render_vector_full_frame(
                    source,
                    evidence_root / "visual",
                    output_name=f"{source_hash}.png",
                )
            visual = Path(render_metadata["output_path"])
        elif suffix in RASTER_SUFFIXES:
            visual = source
        else:
            raise ValueError(f"unsupported evidence image type: {source}")
        width, height = image_dimensions(visual)
        by_hash[source_hash] = {
            "asset_id": f"asset-{len(by_hash) + 1:04d}",
            "role": candidate.get("role") or "main_image",
            "source_path": relative_or_absolute(source, kb_root),
            "source_sha256": source_hash,
            "visual_path": relative_or_absolute(visual, kb_root),
            "visual_sha256": sha256_path(visual),
            "source_bounds": render_metadata.get("source_bounds") if render_metadata else None,
            "word_extent": candidate.get("word_extent"),
            "render_geometry": render_metadata.get("render_geometry") if render_metadata else {
                "width": width,
                "height": height,
                "pixels": width * height,
                "aspect": round(width / height, 8) if height else None,
                "aspect_delta": None,
                "content_bbox": None,
                "content_ratio": None,
            },
            "geometry_issues": render_metadata.get("geometry_issues", []) if render_metadata else [],
            "width": width,
            "height": height,
            "pixels": width * height,
            "bytes": source.stat().st_size,
            "cropped": False,
            "resized": False,
            "full_resolution": True,
            "ocr_candidate": candidate.get("ocr_candidate"),
            "ocr_status": "candidate_only" if candidate.get("ocr_candidate") else "not_generated",
            "occurrences": [occurrence],
        }
    return list(by_hash.values()), occurrence_count


def _write_segments(
    evidence_root: Path,
    campaign_id: str,
    batch_id: str,
    question_id: str,
    assets: list[dict[str, Any]],
    *,
    question_context: dict[str, Any],
    text_bytes: int,
    context_window_tokens: int,
    max_unique_images: int,
    context_budget_ratio: float,
    previous: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    effective_max_images = (
        min(max_unique_images, MAX_VLM_IMAGES)
        if any(asset.get("role") in {"formula", "formula_or_diagram"} for asset in assets)
        else max_unique_images
    )
    current_by_hash = {str(asset["source_sha256"]): asset for asset in assets}
    current_context_hash = hashlib.sha256(
        json.dumps(question_context, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    preserved: list[dict[str, Any]] = []
    preserved_hashes: set[str] = set()
    if previous and previous.get("question_context_sha256") == current_context_hash:
        for prior in previous.get("segments") or []:
            if not isinstance(prior, dict) or prior.get("status") != "completed":
                continue
            prior_manifest = Path(str(prior.get("manifest") or ""))
            if not prior_manifest.is_file() or sha256_path(prior_manifest) != prior.get("segment_manifest_sha256"):
                continue
            manifest = load_json_object(prior_manifest, "prior segment manifest")
            manifest_assets = [value for value in manifest.get("assets") or [] if isinstance(value, dict)]
            if not manifest_assets:
                continue
            unchanged = all(
                str(value.get("source_sha256") or "") in current_by_hash
                and current_by_hash[str(value["source_sha256"])].get("visual_sha256") == value.get("visual_sha256")
                for value in manifest_assets
            )
            if not unchanged:
                continue
            preserved.append(dict(prior))
            preserved_hashes.update(str(value["source_sha256"]) for value in manifest_assets)

    remaining_assets = [asset for asset in assets if str(asset["source_sha256"]) not in preserved_hashes]
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for asset in remaining_assets:
        candidate = current + [asset]
        ratio = estimate_context_ratio(candidate, text_bytes, context_window_tokens)
        if current and (len(candidate) > effective_max_images or ratio > context_budget_ratio):
            groups.append(current)
            current = [asset]
        else:
            current = candidate
    if current:
        groups.append(current)

    previous_segments = {
        str(segment.get("segment_manifest_sha256") or ""): segment
        for segment in (previous or {}).get("segments") or []
        if isinstance(segment, dict)
    }
    results: list[dict[str, Any]] = preserved
    for index, group in enumerate(groups, start=len(preserved) + 1):
        segment_id = f"segment-{index:04d}"
        ratio = estimate_context_ratio(group, text_bytes, context_window_tokens)
        blocked = ratio > context_budget_ratio
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "campaign_id": campaign_id,
            "batch_id": batch_id,
            "question_id": question_id,
            "question_context": question_context,
            "segment_id": segment_id,
            "budget_profile": BUDGET_PROFILE,
            "context_window_tokens": context_window_tokens,
            "max_unique_images": effective_max_images,
            "unique_image_count": len(group),
            "total_pixels": sum(int(asset["pixels"]) for asset in group),
            "text_bytes": text_bytes,
            "estimated_context_ratio": ratio,
            "blocked": blocked,
            "blocked_reason": "single_asset_over_budget" if blocked and len(group) == 1 else None,
            "assets": group,
        }
        path = evidence_root / "segments" / f"{segment_id}.json"
        atomic_write_json(path, manifest)
        manifest_hash = sha256_path(path)
        prior = previous_segments.get(manifest_hash) or {}
        status = str(prior.get("status") or ("blocked" if blocked else "pending"))
        results.append({
            "segment_id": segment_id,
            "manifest": str(path),
            "segment_manifest_sha256": manifest_hash,
            "status": status,
            "blocked_reason": prior.get("blocked_reason") or ("budget" if blocked else None),
            "unique_image_count": len(group),
            "total_pixels": manifest["total_pixels"],
            "text_bytes": text_bytes,
            "estimated_context_ratio": ratio,
            "review": prior.get("review"),
        })
    return results


def _mark_cross_question_duplicates(
    campaign_root: Path,
    batch: dict[str, Any],
    question_id: str,
    assets: list[dict[str, Any]],
) -> None:
    owners: dict[str, dict[str, str]] = {}
    evidence_root = campaign_root / "work" / batch["batch_id"] / "evidence"
    for other_question_id in batch["question_ids"]:
        if other_question_id == question_id:
            continue
        ledger_path = evidence_root / other_question_id / "ledger.json"
        if not ledger_path.is_file():
            continue
        ledger = load_json_object(ledger_path, "evidence ledger")
        for asset in ledger.get("assets") or []:
            if not isinstance(asset, dict) or asset.get("duplicate_of"):
                continue
            source_hash = str(asset.get("source_sha256") or "")
            if source_hash:
                owners.setdefault(source_hash, {
                    "question_id": str(ledger.get("question_id") or other_question_id),
                    "asset_id": str(asset.get("asset_id") or ""),
                    "source_sha256": source_hash,
                })
    for asset in assets:
        owner = owners.get(str(asset.get("source_sha256") or ""))
        if owner:
            asset["duplicate_of"] = owner


def refresh_duplicate_bindings(evidence_batch_root: Path) -> int:
    """Freeze donor review identity and resolution into every duplicate asset."""
    ledgers: dict[str, tuple[Path, dict[str, Any]]] = {}
    skills_root = next((parent for parent in evidence_batch_root.parents if parent.name == "skills"), None)
    kb_root = skills_root.parent if skills_root is not None else evidence_batch_root
    for path in evidence_batch_root.glob("*/ledger.json"):
        ledger = load_json_object(path, "evidence ledger")
        for segment in ledger.get("segments") or []:
            review = segment.get("review") or {}
            evidence_raw = str(review.get("evidence") or "")
            evidence_path = Path(evidence_raw)
            if not evidence_path.is_absolute():
                evidence_path = kb_root / evidence_path
            if not evidence_path.is_file() or sha256_path(evidence_path) != review.get("evidence_sha256"):
                continue
            evidence = load_json_object(evidence_path, "segment evidence")
            review["resolutions"] = {
                str(item.get("source_sha256") or ""): {
                    key: item.get(key)
                    for key in (
                        "status", "final_latex", "resolution_source", "agreed_engines",
                        "visual_evidence", "confidence", "asset_kind",
                    )
                    if item.get(key) is not None
                }
                for item in evidence.get("assets") or []
                if isinstance(item, dict) and item.get("source_sha256")
            }
        ledgers[str(ledger.get("question_id") or path.parent.name)] = (path, ledger)
    changed = 0
    for path, ledger in ledgers.values():
        ledger_changed = False
        inherited_count = 0
        assets = [asset for asset in ledger.get("assets") or [] if isinstance(asset, dict)]
        local_review_count = sum(not asset.get("duplicate_of") for asset in assets)
        for key, value in (
            ("local_review_asset_count", local_review_count),
            ("total_asset_count", len(assets)),
        ):
            if ledger.get(key) != value:
                ledger[key] = value
                ledger_changed = True
        for asset in assets:
            duplicate = asset.get("duplicate_of")
            if not isinstance(duplicate, dict):
                continue
            donor_entry = ledgers.get(str(duplicate.get("question_id") or ""))
            if donor_entry is None:
                continue
            _, donor = donor_entry
            source_hash = str(asset.get("source_sha256") or "")
            binding = None
            for segment in donor.get("segments") or []:
                review = segment.get("review") or {}
                resolution = (review.get("resolutions") or {}).get(source_hash)
                if resolution:
                    binding = {
                        "donor_segment_id": segment.get("segment_id"),
                        "donor_evidence_sha256": review.get("evidence_sha256"),
                        "donor_resolution": resolution,
                    }
                    break
            if binding and any(duplicate.get(key) != value for key, value in binding.items()):
                duplicate.update(binding)
                ledger_changed = True
            if binding:
                inherited_count += 1
        if ledger.get("inherited_evidence_count") != inherited_count:
            ledger["inherited_evidence_count"] = inherited_count
            ledger_changed = True
        if ledger_changed:
            ledger["updated_at"] = utc_now()
            atomic_write_json(path, ledger)
            changed += 1
    return changed


def _enrich_formula_candidates(
    kb_root: Path,
    evidence_root: Path,
    assets: list[dict[str, Any]],
    previous: dict[str, Any] | None,
) -> None:
    batch_cache_path = evidence_root.parents[1] / "formula_ensemble" / "formula_ensemble.json"
    batch_cache: dict[str, dict[str, Any]] = {}
    if batch_cache_path.is_file():
        cached_payload = load_json_object(batch_cache_path, "batch formula ensemble")
        batch_cache = {
            str(record.get("source_sha256") or ""): record
            for record in cached_payload.get("records") or []
            if isinstance(record, dict)
        }

    def apply_record(asset: dict[str, Any], record: dict[str, Any]) -> None:
        asset["formula_candidates"] = record.get("candidates") or []
        asset["local_formula_resolution"] = record.get("resolution") or {}
        status = str(asset["local_formula_resolution"].get("status") or "machine_abstain")
        asset["ocr_status"] = "local_consensus" if status == "machine_final" else "needs_vlm"
        asset["ocr_candidate"] = asset["local_formula_resolution"].get("final_latex")

    previous_by_hash = {
        str(asset.get("source_sha256") or ""): asset
        for asset in (previous or {}).get("assets") or []
        if isinstance(asset, dict)
    }
    pending: list[dict[str, Any]] = []
    for asset in assets:
        if asset.get("role") not in {"formula", "formula_or_diagram"} or asset.get("duplicate_of"):
            continue
        prior = previous_by_hash.get(str(asset["source_sha256"]))
        if (
            prior
            and prior.get("visual_sha256") == asset.get("visual_sha256")
            and isinstance(prior.get("formula_candidates"), list)
            and isinstance(prior.get("local_formula_resolution"), dict)
        ):
            asset["formula_candidates"] = prior["formula_candidates"]
            asset["local_formula_resolution"] = prior["local_formula_resolution"]
            asset["ocr_status"] = prior.get("ocr_status", "candidate_only")
            asset["ocr_candidate"] = prior.get("ocr_candidate")
        elif str(asset.get("visual_sha256") or "") in batch_cache:
            apply_record(asset, batch_cache[str(asset["visual_sha256"])])
        else:
            pending.append(asset)
    if not pending:
        return
    paths = [resolve_path(kb_root, asset["visual_path"]) for asset in pending]
    if any(path is None for path in paths):
        raise ValueError("formula visual path could not be resolved")
    concrete_paths = [path for path in paths if path is not None]
    geometry = {
        str(path.resolve()): list(asset.get("geometry_issues") or [])
        for path, asset in zip(concrete_paths, pending)
    }
    ensemble = run_formula_ensemble(
        concrete_paths,
        evidence_root / "local_formula_ensemble",
        geometry_by_image=geometry,
    )
    by_visual_hash = {
        str(record.get("source_sha256") or ""): record
        for record in ensemble.get("records") or []
    }
    for asset in pending:
        record = by_visual_hash.get(str(asset.get("visual_sha256") or ""))
        if not record:
            asset["formula_candidates"] = []
            asset["local_formula_resolution"] = {
                "status": "machine_abstain",
                "final_latex": None,
                "risk_flags": ["local_engine_result_missing"],
            }
            asset["ocr_status"] = "engine_failure"
            continue
        apply_record(asset, record)


def prepare_question_evidence(
    kb_root: Path,
    campaign_root: Path,
    state: dict[str, Any],
    batch: dict[str, Any],
    question_id: str,
) -> dict[str, Any]:
    question_id = question_id.upper()
    question = _question_from_manifest(kb_root, batch, question_id)
    evidence_root = campaign_root / "work" / batch["batch_id"] / "evidence" / question_id
    ledger_path = evidence_root / "ledger.json"
    previous = load_json_object(ledger_path, "evidence ledger") if ledger_path.is_file() else None
    candidates = _relationship_candidates(kb_root, campaign_root, batch, question, question_id)
    assets, occurrence_count = _build_assets(kb_root, evidence_root, candidates)
    _mark_cross_question_duplicates(campaign_root, batch, question_id, assets)
    _enrich_formula_candidates(kb_root, evidence_root, assets, previous)
    review_assets = [asset for asset in assets if not asset.get("duplicate_of")]
    question_context = {
        key: question.get(key)
        for key in (
            "question_id", "source_question_no", "heading", "target_path",
            "current_sha256", "start_block", "end_block",
        )
        if question.get(key) is not None
    }
    question_context_bytes = json.dumps(question_context, ensure_ascii=False, sort_keys=True).encode("utf-8")
    question_context_sha256 = hashlib.sha256(question_context_bytes).hexdigest()
    text_bytes = len(question_context_bytes)
    context_window_tokens = int(state.get("context_window_tokens") or DEFAULT_CONTEXT_WINDOW_TOKENS)
    max_unique_images = int(state.get("max_unique_images") or MAX_UNIQUE_IMAGES)
    context_budget_ratio = float(state.get("context_budget_ratio") or CONTEXT_BUDGET_RATIO)
    segments = _write_segments(
        evidence_root,
        state["campaign_id"],
        batch["batch_id"],
        question_id,
        review_assets,
        question_context=question_context,
        text_bytes=text_bytes,
        context_window_tokens=context_window_tokens,
        max_unique_images=max_unique_images,
        context_budget_ratio=context_budget_ratio,
        previous=previous,
    )
    old_hashes = {asset.get("source_sha256") for asset in (previous or {}).get("assets") or []}
    new_hashes = {asset["source_sha256"] for asset in assets}
    previous_context_hash = (previous or {}).get("question_context_sha256")
    stale = bool(
        previous
        and (
            not old_hashes.issubset(new_hashes)
            or (previous_context_hash is not None and previous_context_hash != question_context_sha256)
        )
    )
    if stale:
        for segment in segments:
            if segment["status"] == "completed":
                segment["status"] = "stale"
                segment["review"] = None
    completed = sum(segment["status"] == "completed" for segment in segments)
    blocked = any(segment["status"] == "blocked" for segment in segments)
    evidence_stage = "blocked" if blocked else ("complete" if completed == len(segments) else "pending")
    ledger = {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": state["campaign_id"],
        "batch_id": batch["batch_id"],
        "question_id": question_id,
        "prepared_at": utc_now(),
        "budget_profile": state.get("budget_profile", BUDGET_PROFILE),
        "max_unique_images": max_unique_images,
        "context_window_tokens": context_window_tokens,
        "context_budget_ratio": context_budget_ratio,
        "evidence_stage": evidence_stage,
        "segments_total": len(segments),
        "segments_completed": completed,
        "unique_image_count": len(review_assets),
        "local_review_asset_count": len(review_assets),
        "total_asset_count": len(assets),
        "duplicate_occurrence_count": max(0, occurrence_count - len(review_assets)),
        "total_pixels": sum(int(asset["pixels"]) for asset in review_assets),
        "text_bytes": text_bytes,
        "question_context_sha256": question_context_sha256,
        "stale": stale,
        "assets": assets,
        "segments": segments,
    }
    atomic_write_json(ledger_path, ledger)
    return {
        "ledger": str(ledger_path),
        "evidence_stage": evidence_stage,
        "segments_total": len(segments),
        "segments_completed": completed,
        "segment_image_counts": [int(segment["unique_image_count"]) for segment in segments],
        "unique_image_count": len(review_assets),
        "duplicate_occurrence_count": ledger["duplicate_occurrence_count"],
        "total_pixels": ledger["total_pixels"],
        "text_bytes": text_bytes,
        "stale": stale,
    }


def record_question_evidence(
    kb_root: Path,
    ledger_path: Path,
    evidence_path: Path,
    runner_id: str | None,
) -> dict[str, Any]:
    ledger = load_json_object(ledger_path, "evidence ledger")
    evidence = load_json_object(evidence_path, "segment evidence")
    if evidence.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("segment evidence must use schema_version 2")
    if str(evidence.get("question_id") or "").upper() != ledger["question_id"]:
        raise ValueError("segment evidence question_id mismatch")
    segment_id = str(evidence.get("segment_id") or "")
    segment = next((value for value in ledger["segments"] if value["segment_id"] == segment_id), None)
    if segment is None:
        raise KeyError(f"unknown evidence segment: {segment_id}")
    manifest_path = Path(segment["manifest"])
    if not manifest_path.is_absolute():
        manifest_path = kb_root / manifest_path
    current_manifest_hash = sha256_path(manifest_path)
    if current_manifest_hash != segment["segment_manifest_sha256"]:
        raise ValueError("segment manifest changed after preparation")
    if evidence.get("segment_manifest_sha256") != current_manifest_hash:
        raise ValueError("segment evidence is not bound to the current manifest hash")
    manifest = load_json_object(manifest_path, "segment manifest")
    expected_hashes = {asset["source_sha256"] for asset in manifest["assets"]}
    reviews = evidence.get("assets")
    if not isinstance(reviews, list):
        raise ValueError("segment evidence assets must be an array")
    review_by_hash: dict[str, dict[str, Any]] = {}
    for review in reviews:
        if not isinstance(review, dict):
            raise ValueError("segment evidence asset reviews must be objects")
        source_hash = str(review.get("source_sha256") or "")
        status = str(review.get("status") or "")
        if status not in REVIEW_STATUSES:
            raise ValueError(f"invalid evidence review status: {status}")
        if source_hash in review_by_hash:
            raise ValueError(f"duplicate evidence review for source hash: {source_hash}")
        if source_hash not in expected_hashes:
            raise ValueError(f"evidence review source hash is not in current manifest: {source_hash}")
        asset = next(value for value in manifest["assets"] if value["source_sha256"] == source_hash)
        if status in {"confirmed", "resolved"} and asset.get("role") in {"formula", "formula_or_diagram"}:
            final_latex = str(review.get("final_latex") or "").strip()
            resolution_source = str(review.get("resolution_source") or "")
            if not final_latex:
                raise ValueError("confirmed formula evidence requires final_latex")
            if resolution_source not in {"local_consensus", "vlm"}:
                raise ValueError("confirmed formula evidence requires resolution_source local_consensus|vlm")
            if resolution_source == "local_consensus":
                engines = {str(value) for value in review.get("agreed_engines") or [] if value}
                if len(engines) < 2:
                    raise ValueError("local_consensus formula evidence requires two agreed_engines")
            else:
                visual_evidence = str(review.get("visual_evidence") or "").strip()
                confidence = review.get("confidence")
                if not visual_evidence or not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
                    raise ValueError("VLM formula evidence requires visual_evidence and confidence in [0,1]")
        review_by_hash[source_hash] = review
    if set(review_by_hash) != expected_hashes:
        raise ValueError("segment evidence must review every current asset exactly once")
    counts = {status: 0 for status in sorted(REVIEW_STATUSES)}
    for review in review_by_hash.values():
        counts[str(review["status"])] += 1
    unresolved = counts.get("unresolved", 0) + counts.get("high_risk", 0)
    segment["status"] = "blocked" if unresolved else "completed"
    segment["blocked_reason"] = "review_unresolved" if unresolved else None
    segment["review"] = {
        "evidence": relative_or_absolute(evidence_path, kb_root),
        "evidence_sha256": sha256_path(evidence_path),
        "reviewed_by_runner": runner_id,
        "reviewed_at": utc_now(),
        "counts": counts,
        "resolutions": {
            source_hash: {
                key: review.get(key)
                for key in (
                    "status", "final_latex", "resolution_source", "agreed_engines",
                    "visual_evidence", "confidence", "asset_kind",
                )
                if review.get(key) is not None
            }
            for source_hash, review in review_by_hash.items()
        },
    }
    completed = sum(value["status"] == "completed" for value in ledger["segments"])
    ledger["segments_completed"] = completed
    review_blocked = any(
        value.get("status") == "blocked" and value.get("blocked_reason") == "review_unresolved"
        for value in ledger["segments"]
    )
    ledger["evidence_stage"] = (
        "blocked" if review_blocked else ("complete" if completed == len(ledger["segments"]) else "in_progress")
    )
    ledger["updated_at"] = utc_now()
    atomic_write_json(ledger_path, ledger)
    refresh_duplicate_bindings(ledger_path.parent.parent)
    return {
        "question_id": ledger["question_id"],
        "segment_id": segment_id,
        "segment_status": segment["status"],
        "evidence_stage": ledger["evidence_stage"],
        "segments_total": len(ledger["segments"]),
        "segments_completed": completed,
        "review_counts": counts,
    }


def next_pending_segment(
    campaign_root: Path,
    batch: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    evidence_root = campaign_root / "work" / batch["batch_id"] / "evidence"
    for question_id in batch["question_ids"]:
        ledger_path = evidence_root / question_id / "ledger.json"
        if not ledger_path.is_file():
            continue
        ledger = load_json_object(ledger_path, "evidence ledger")
        for segment in ledger.get("segments") or []:
            if segment.get("status") in {"pending", "in_progress", "stale"} or (
                segment.get("status") == "blocked" and segment.get("blocked_reason") == "review_unresolved"
            ):
                return ledger, segment
    return None


def summarize_batch_budget(campaign_root: Path, batch: dict[str, Any]) -> dict[str, Any]:
    evidence_root = campaign_root / "work" / batch["batch_id"] / "evidence"
    ledgers: list[dict[str, Any]] = []
    for question_id in batch["question_ids"]:
        path = evidence_root / question_id / "ledger.json"
        if path.is_file():
            ledgers.append(load_json_object(path, "evidence ledger"))
    segments = [segment for ledger in ledgers for segment in ledger.get("segments") or []]
    return {
        "budget_profile": BUDGET_PROFILE,
        "max_unique_images": int(max((ledger.get("max_unique_images", MAX_UNIQUE_IMAGES) for ledger in ledgers), default=MAX_UNIQUE_IMAGES)),
        "questions_prepared": len(ledgers),
        "segments_total": len(segments),
        "segments_completed": sum(segment.get("status") == "completed" for segment in segments),
        "segments_review_blocked": sum(
            segment.get("status") == "blocked" and segment.get("blocked_reason") == "review_unresolved"
            for segment in segments
        ),
        "unique_image_count": sum(int(ledger.get("unique_image_count") or 0) for ledger in ledgers),
        "total_pixels": sum(int(ledger.get("total_pixels") or 0) for ledger in ledgers),
        "text_bytes": sum(int(ledger.get("text_bytes") or 0) for ledger in ledgers),
        "max_estimated_context_ratio": max((float(segment.get("estimated_context_ratio") or 0) for segment in segments), default=0.0),
        "over_budget": any(float(segment.get("estimated_context_ratio") or 0) > CONTEXT_BUDGET_RATIO for segment in segments),
    }
