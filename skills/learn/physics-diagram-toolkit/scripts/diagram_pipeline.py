#!/usr/bin/env python
"""Resumable, hash-bound pipeline for annotated and original physics diagrams."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from calibrate_image import calibrate
from diagram_common import (
    KB_ROOT,
    append_jsonl,
    atomic_write_json,
    ensure_inside,
    ensure_task_id,
    load_json,
    output_base,
    resolve_project_input,
    sha256_file,
    sha256_json,
    task_base,
)
from render_diagram import render as render_low_level
from render_plot import render_plot
from scene_compiler import compile_scene
from geometry_validator import assertion, finalize_report


ITEM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
ALLOWED_STATUSES = {
    "needs_calibration_review",
    "needs_reference_review",
    "needs_semantic_review",
    "complete",
    "blocked",
}
SCRIPT = Path(__file__).resolve()
MODEL_RUNTIME = KB_ROOT / "skills" / "_shared" / "model-tools" / "scripts" / "model_runtime.py"
VISUAL_INDEX = KB_ROOT / "skills" / "maintain" / "rag-management" / "scripts" / "visual_image_index.py"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def event(task_dir: Path, stage: str, status: str, **details: Any) -> None:
    append_jsonl(task_dir / "events.jsonl", {"at": utc_now(), "stage": stage, "status": status, **details})


def resolve_output_dir(request: dict[str, Any], override: Path | None) -> Path:
    root = output_base(override)
    raw = request.get("output_dir") or "physics_diagrams"
    candidate = Path(str(raw)).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
    elif str(candidate).replace("\\", "/").startswith("output/") and override is None:
        resolved = (KB_ROOT / candidate).resolve()
    else:
        resolved = (root / candidate).resolve()
    ensure_inside(resolved, root)
    return resolved


def normalize_request(request: dict[str, Any]) -> dict[str, Any]:
    schema_version = int(request.get("schema_version", 0))
    if schema_version not in {1, 2}:
        raise ValueError("diagram_request schema_version must be 1 or 2")
    defaults = {
        "mode": request.get("mode"),
        "renderer": request.get("renderer"),
        "model_profile": request.get("model_profile", "weak"),
        "question_text": request.get("question_text"),
        "query_text": request.get("query_text"),
        "source_image": request.get("source_image"),
        "source_sha256": request.get("source_sha256"),
        "reference_results_path": request.get("reference_results_path"),
        "purpose": request.get("purpose"),
        "style_profile": request.get("style_profile"),
        "diagram_intent": request.get("diagram_intent"),
    }
    raw_items = request.get("items")
    if raw_items is None:
        raw_items = [{"id": "main"}]
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("request.items must be a non-empty array when present")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"items[{index}] must be an object")
        item = {**defaults, **raw}
        item_id = str(item.get("id", ""))
        if not ITEM_ID_RE.fullmatch(item_id) or item_id in seen:
            raise ValueError(f"invalid or duplicate item id: {item_id}")
        seen.add(item_id)
        mode = str(item.get("mode", ""))
        renderer = str(item.get("renderer", ""))
        profile = str(item.get("model_profile", ""))
        if mode not in {"annotate", "original"}:
            raise ValueError(f"items[{index}].mode must be annotate or original")
        if renderer not in {"scene", "plot"}:
            raise ValueError(f"items[{index}].renderer must be scene or plot")
        if profile not in {"weak", "strong"}:
            raise ValueError(f"items[{index}].model_profile must be weak or strong")
        if mode == "annotate" and renderer != "scene":
            raise ValueError("annotate mode supports only the scene renderer")
        if mode == "annotate" and not item.get("source_image"):
            raise ValueError("annotate mode requires source_image")
        if mode == "original" and not str(item.get("question_text", "")).strip():
            raise ValueError("original mode requires question_text")
        if schema_version == 2:
            purpose = str(item.get("purpose", ""))
            if purpose not in {"question", "solution", "annotation"}:
                raise ValueError(f"items[{index}].purpose must be question|solution|annotation")
            if mode == "annotate" and purpose != "annotation":
                raise ValueError("annotate mode requires purpose=annotation")
            if mode == "original" and purpose == "annotation":
                raise ValueError("original mode requires purpose=question|solution")
            expected_style = "exam-monochrome" if purpose == "question" else "solution-color"
            style_profile = str(item.get("style_profile") or expected_style)
            if purpose != "annotation" and style_profile != expected_style:
                raise ValueError(f"purpose={purpose} requires style_profile={expected_style}")
            if style_profile not in {"exam-monochrome", "solution-color"}:
                raise ValueError("style_profile must be exam-monochrome|solution-color")
            intent = item.get("diagram_intent")
            if not isinstance(intent, dict):
                raise ValueError(f"items[{index}].diagram_intent must be an object")
            facts = intent.get("facts")
            if not isinstance(facts, list):
                raise ValueError(f"items[{index}].diagram_intent.facts must be an array")
            fact_ids: set[str] = set()
            for fact_index, fact in enumerate(facts):
                if not isinstance(fact, dict):
                    raise ValueError(f"items[{index}].diagram_intent.facts[{fact_index}] must be an object")
                fact_id = str(fact.get("id", ""))
                if not fact_id or fact_id in fact_ids or not str(fact.get("quote", "")).strip():
                    raise ValueError(f"items[{index}].diagram_intent facts require unique id and non-empty quote")
                fact_ids.add(fact_id)
            item["purpose"] = purpose
            item["style_profile"] = style_profile
            item["diagram_intent"] = {**intent, "facts": facts}
            item["legacy_contract"] = False
        else:
            item["purpose"] = "annotation" if mode == "annotate" else "legacy"
            item["style_profile"] = "legacy-color"
            item["diagram_intent"] = {"facts": []}
            item["legacy_contract"] = True
        items.append(item)
    return {
        "schema_version": schema_version,
        "output_dir": request.get("output_dir") or "physics_diagrams",
        "items": items,
    }


def task_paths(task_id: str, task_root_override: Path | None = None) -> tuple[Path, Path, Path]:
    task_dir = task_base(task_id, task_root_override)
    return task_dir, task_dir / "request.json", task_dir / "state.json"


def write_state(task_dir: Path, state: dict[str, Any]) -> None:
    if state.get("status") not in ALLOWED_STATUSES:
        raise ValueError(f"unsupported task status: {state.get('status')}")
    state["updated_at"] = utc_now()
    atomic_write_json(task_dir / "state.json", state)


def read_task(task_id: str, task_root_override: Path | None = None) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    task_dir, request_path, state_path = task_paths(task_id, task_root_override)
    if not request_path.is_file() or not state_path.is_file():
        raise FileNotFoundError(f"task is not initialized: {task_id}")
    request = load_json(request_path)
    state = load_json(state_path)
    if state.get("request_sha256") != sha256_file(request_path):
        raise RuntimeError("task request fingerprint mismatch")
    return task_dir, request, state


def run_visual_batch(requests: list[dict[str, str]], task_dir: Path, top_k: int) -> dict[str, Any]:
    request_path = task_dir / "visual_queries.json"
    atomic_write_json(request_path, {"schema_version": 1, "queries": requests})
    command = [
        sys.executable,
        str(MODEL_RUNTIME),
        "run",
        "--id",
        "qwen3-vl-embedding-2b",
        "--script",
        str(VISUAL_INDEX),
        "--",
        "query-batch",
        "--requests",
        str(request_path),
        "--top-k",
        str(top_k),
    ]
    process = subprocess.run(
        command,
        cwd=str(KB_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=900,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(f"visual retrieval failed: {(process.stderr or process.stdout).strip()}")
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"visual retrieval returned invalid JSON: {process.stdout[-2000:]}") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError(f"visual retrieval failed: {payload}")
    return payload


def normalized_reference_report(raw: dict[str, Any], item_id: str, query_text: str) -> dict[str, Any]:
    if not raw.get("index_artifacts_verified"):
        raise RuntimeError(f"visual index artifacts were not verified for {item_id}")
    results = raw.get("results")
    if not isinstance(results, list):
        raise ValueError(f"visual result for {item_id} lacks results")
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in results:
        if not isinstance(result, dict):
            continue
        digest = str(result.get("sha256", ""))
        if len(digest) != 64 or digest in seen:
            continue
        seen.add(digest)
        deduped.append(
            {
                "rank": len(deduped) + 1,
                "score": float(result["score"]),
                "relative_path": str(result["relative_path"]),
                "sha256": digest,
                "width": int(result["width"]),
                "height": int(result["height"]),
            }
        )
    if not deduped:
        raise RuntimeError(f"visual retrieval returned no valid references for {item_id}")
    source_complete = bool(raw.get("source_complete"))
    report = {
        "schema_version": 1,
        "status": "needs_reference_review",
        "item_id": item_id,
        "query": query_text,
        "query_sha256": sha256_json(query_text),
        "index": str(raw.get("index", "")),
        "index_manifest_sha256": str(raw.get("index_manifest_sha256", "")),
        "index_artifacts_verified": True,
        "dimension": int(raw.get("dimension", 0)),
        "source_complete": source_complete,
        "invalid_candidate_count": int(raw.get("invalid_candidate_count", 0)),
        "coverage_warning": None if source_complete else "visual index is incomplete; results may be used positively but cannot prove absence",
        "results": deduped,
    }
    report["report_sha256"] = sha256_json({key: value for key, value in report.items() if key != "report_sha256"})
    return report


def cached_reference_report(path: Path, query_text: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        report = load_json(path)
        if report.get("query_sha256") != sha256_json(query_text) or not report.get("index_artifacts_verified"):
            return None
        index_dir = Path(str(report.get("index", ""))).resolve()
        ensure_inside(index_dir, KB_ROOT / "BGE-M3" / "runtime" / "index")
        manifest_path = index_dir / "manifest.json"
        if not manifest_path.is_file() or sha256_file(manifest_path) != report.get("index_manifest_sha256"):
            return None
        manifest = load_json(manifest_path)
        for artifact in manifest.get("artifacts", []):
            if not isinstance(artifact, dict):
                return None
            artifact_path = (index_dir / str(artifact.get("path", ""))).resolve()
            ensure_inside(artifact_path, index_dir)
            if (
                not artifact_path.is_file()
                or artifact_path.stat().st_size != int(artifact.get("bytes", -1))
                or sha256_file(artifact_path) != artifact.get("sha256")
            ):
                return None
        return report
    except Exception:
        return None


def prepare(
    request_path: Path,
    task_id: str,
    *,
    task_root_override: Path | None = None,
    output_root_override: Path | None = None,
    test_mode: bool = False,
) -> dict[str, Any]:
    ensure_task_id(task_id)
    original_request = load_json(request_path.resolve())
    request = normalize_request(original_request)
    resolve_output_dir(request, output_root_override)
    task_dir, stored_request, state_path = task_paths(task_id, task_root_override)
    task_dir.mkdir(parents=True, exist_ok=True)
    if stored_request.exists():
        existing = load_json(stored_request)
        if sha256_json(existing) != sha256_json(request):
            raise RuntimeError("task-id already exists with a different request")
    else:
        atomic_write_json(stored_request, request)
    request_sha = sha256_file(stored_request)
    state = load_json(state_path) if state_path.is_file() else {
        "schema_version": 1,
        "task_id": task_id,
        "created_at": utc_now(),
        "request_sha256": request_sha,
        "request_schema_version": request["schema_version"],
        "items": {},
    }
    event(task_dir, "prepare", "started", item_count=len(request["items"]))

    pending_queries: list[dict[str, str]] = []
    test_results: dict[str, dict[str, Any]] = {}
    cached_reports: dict[str, dict[str, Any]] = {}
    for item in request["items"]:
        item_id = item["id"]
        item_dir = task_dir / "items" / item_id
        item_dir.mkdir(parents=True, exist_ok=True)
        if item["mode"] == "annotate":
            source = resolve_project_input(str(item["source_image"]))
            expected = str(item.get("source_sha256") or "")
            actual = sha256_file(source)
            if expected and actual != expected:
                raise RuntimeError(f"source image hash mismatch for {item_id}")
            proposal_path = item_dir / "prepare" / "coordinate_map.json"
            if proposal_path.is_file():
                proposal = load_json(proposal_path)
                if proposal.get("source", {}).get("sha256") != actual:
                    raise RuntimeError(f"source image changed since calibration for {item_id}")
            else:
                proposal = calibrate(source, item_dir / "prepare")
            state["items"][item_id] = {
                "mode": "annotate",
                "renderer": "scene",
                "model_profile": item["model_profile"],
                "purpose": item["purpose"],
                "style_profile": item["style_profile"],
                "diagram_intent": item["diagram_intent"],
                "legacy_contract": item["legacy_contract"],
                "status": "needs_calibration_review",
                "source_path": str(source),
                "source_sha256": actual,
                "coordinate_map": str(proposal_path),
                "coordinate_map_sha256": sha256_file(proposal_path),
                "next_action": f"write {item_dir / 'calibration_review.json'}",
            }
        else:
            query_text = str(item.get("query_text") or item["question_text"]).strip()
            report_path = item_dir / "reference_report.json"
            cached = cached_reference_report(report_path, query_text)
            test_path = item.get("reference_results_path")
            if cached is not None:
                cached_reports[item_id] = cached
                event(task_dir, "prepare", "reference_cache_hit", item_id=item_id)
            elif test_path:
                if not test_mode:
                    raise ValueError("reference_results_path is allowed only in test mode")
                test_results[item_id] = load_json(resolve_project_input(str(test_path)))
            else:
                pending_queries.append({"id": item_id, "text": query_text})
            state["items"][item_id] = {
                "mode": "original",
                "renderer": item["renderer"],
                "model_profile": item["model_profile"],
                "purpose": item["purpose"],
                "style_profile": item["style_profile"],
                "diagram_intent": item["diagram_intent"],
                "legacy_contract": item["legacy_contract"],
                "status": "preparing_references",
                "question_sha256": sha256_json(str(item["question_text"])),
                "query_text": query_text,
            }

    checkpoint_statuses = {value["status"] for value in state["items"].values()}
    state["status"] = "needs_calibration_review" if "needs_calibration_review" in checkpoint_statuses else "needs_reference_review"
    state["next_action"] = "prepare is incomplete; rerun prepare if the process is interrupted"
    write_state(task_dir, state)

    batch_by_id: dict[str, dict[str, Any]] = {}
    if pending_queries:
        batch = run_visual_batch(pending_queries, task_dir, top_k=12)
        for row in batch.get("queries", []):
            if isinstance(row, dict) and row.get("id"):
                batch_by_id[str(row["id"])] = {**batch, **row}
    for item in request["items"]:
        if item["mode"] != "original":
            continue
        item_id = item["id"]
        item_dir = task_dir / "items" / item_id
        report = cached_reports.get(item_id)
        if report is None:
            raw = test_results.get(item_id) or batch_by_id.get(item_id)
            if raw is None:
                raise RuntimeError(f"missing visual query result for {item_id}")
            report = normalized_reference_report(raw, item_id, state["items"][item_id]["query_text"])
        report_path = item_dir / "reference_report.json"
        atomic_write_json(report_path, report)
        template = {
            "schema_version": 1 if item["legacy_contract"] else 2,
            "status": "needs_review",
            "reviewer_type": None,
            "report_sha256": report["report_sha256"],
            "reliable_candidate_count": 0,
            "accepted_sha256": [],
            "rejected": [],
            "extracted_rules": {
                "topology": [],
                "symbols": [],
                "layout": [],
            },
            "uncertainties": [],
        }
        atomic_write_json(item_dir / "reference_review.template.json", template)
        state["items"][item_id].update(
            {
                "status": "needs_reference_review",
                "reference_report": str(report_path),
                "reference_report_sha256": sha256_file(report_path),
                "source_complete": report["source_complete"],
                "next_action": f"write {item_dir / 'reference_review.json'}",
            }
        )
    statuses = {value["status"] for value in state["items"].values()}
    state["status"] = "needs_calibration_review" if "needs_calibration_review" in statuses else "needs_reference_review"
    state["next_action"] = "complete the per-item calibration/reference reviews, then run render"
    write_state(task_dir, state)
    event(task_dir, "prepare", state["status"], item_count=len(request["items"]))
    return {"ok": True, "task_dir": str(task_dir), "state": state}


def parse_spec_bundle(payload: dict[str, Any], item_ids: list[str]) -> dict[str, dict[str, Any]]:
    if "items" not in payload:
        if len(item_ids) != 1:
            raise ValueError("multi-item task requires a spec bundle with items")
        return {item_ids[0]: payload}
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("spec bundle items must be an array")
    result: dict[str, dict[str, Any]] = {}
    for row in raw_items:
        if not isinstance(row, dict) or not isinstance(row.get("spec"), dict):
            raise ValueError("each spec bundle item requires id and spec")
        item_id = str(row.get("id", ""))
        if item_id in result:
            raise ValueError(f"duplicate spec item: {item_id}")
        result[item_id] = row["spec"]
    if set(result) != set(item_ids):
        raise ValueError("spec bundle item ids do not match task item ids")
    return result


def approved_reviewer(review: dict[str, Any], field: str) -> None:
    if review.get("status") != "approved":
        raise RuntimeError(f"{field} is not approved")
    if review.get("reviewer_type") not in {"strong", "teacher"}:
        raise RuntimeError(f"{field} requires reviewer_type strong or teacher")
    if review.get("uncertainties"):
        raise RuntimeError(f"{field} still contains uncertainties")


def calibration_anchors(item_dir: Path, item_state: dict[str, Any]) -> tuple[dict[str, list[float]], str]:
    proposal = load_json(Path(item_state["coordinate_map"]))
    review_path = item_dir / "calibration_review.json"
    review = load_json(review_path)
    approved_reviewer(review, "calibration review")
    if review.get("proposal_sha256") != proposal.get("proposal_sha256") or review.get("source_sha256") != item_state["source_sha256"]:
        raise RuntimeError("calibration review hash mismatch")
    width, height = proposal["sanitized"]["size"]
    anchors: dict[str, list[float]] = {}
    raw_anchors = review.get("anchors")
    if not isinstance(raw_anchors, list) or not raw_anchors:
        raise RuntimeError("calibration review requires at least one semantic anchor")
    for index, anchor in enumerate(raw_anchors):
        if not isinstance(anchor, dict) or not str(anchor.get("id", "")):
            raise ValueError(f"invalid calibration anchor at index {index}")
        anchor_id = str(anchor["id"])
        if anchor_id in anchors:
            raise ValueError(f"duplicate calibration anchor: {anchor_id}")
        pixel = anchor.get("pixel")
        if not isinstance(pixel, list) or len(pixel) != 2:
            raise ValueError(f"anchor {anchor_id} requires pixel [x, y]")
        x, y = float(pixel[0]), float(pixel[1])
        if not (0 <= x <= width - 1 and 0 <= y <= height - 1):
            raise ValueError(f"anchor outside sanitized image: {anchor_id}")
        anchors[anchor_id] = [x, y]
    return anchors, proposal["sanitized"]["path"]


def validate_reference_review(item_dir: Path, item_state: dict[str, Any]) -> dict[str, Any]:
    report = load_json(Path(item_state["reference_report"]))
    review = load_json(item_dir / "reference_review.json")
    approved_reviewer(review, "reference review")
    if review.get("report_sha256") != report.get("report_sha256"):
        raise RuntimeError("reference review hash mismatch")
    accepted = review.get("accepted_sha256")
    if not isinstance(accepted, list) or not accepted:
        raise RuntimeError("reference review must accept at least one reliable image")
    available = {str(row["sha256"]) for row in report["results"]}
    accepted_set = {str(value) for value in accepted}
    if len(accepted_set) != len(accepted) or not accepted_set <= available:
        raise RuntimeError("reference review accepts unknown or duplicate hashes")
    reliable_count = int(review.get("reliable_candidate_count", len(accepted)))
    if reliable_count >= 2 and len(accepted_set) < 2:
        raise RuntimeError("at least two reliable references must be accepted when available")
    if not item_state.get("legacy_contract"):
        rules = review.get("extracted_rules")
        if not isinstance(rules, dict):
            raise RuntimeError("reference review requires extracted_rules")
        for field in ("topology", "symbols", "layout"):
            values = rules.get(field)
            if not isinstance(values, list) or not values or any(not str(value).strip() for value in values):
                raise RuntimeError(f"reference review extracted_rules.{field} must contain confirmed rules")
    by_hash = {str(row["sha256"]): row for row in report["results"]}
    for digest in accepted_set:
        relative_path = Path(str(by_hash[digest]["relative_path"]))
        candidate = (KB_ROOT / relative_path).resolve()
        ensure_inside(candidate, KB_ROOT)
        if not candidate.is_file() or sha256_file(candidate) != digest:
            raise RuntimeError(f"accepted reference changed or is missing: {relative_path}")
    return {**review, "accepted_sha256": sorted(accepted_set)}


def create_review_sheet(
    *,
    output_image: Path,
    reference_images: list[tuple[str, Path]],
    destination: Path,
) -> Path:
    try:
        from PIL import Image, ImageDraw, ImageOps
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Pillow is required for review sheets") from exc
    panels = [*reference_images, ("output", output_image)]
    if not panels:
        raise ValueError("review sheet requires at least one panel")
    panel_width, panel_height, header_height, gap = 360, 260, 30, 16
    columns = min(3, len(panels))
    rows = (len(panels) + columns - 1) // columns
    sheet = Image.new("RGB", (gap + columns * (panel_width + gap), gap + rows * (panel_height + header_height + gap)), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (label, path) in enumerate(panels):
        if not path.is_file():
            raise FileNotFoundError(path)
        column, row = index % columns, index // columns
        x = gap + column * (panel_width + gap)
        y = gap + row * (panel_height + header_height + gap)
        draw.text((x, y), label, fill="black")
        with Image.open(path) as opened:
            clean = ImageOps.exif_transpose(opened).convert("RGB")
            clean.thumbnail((panel_width, panel_height))
            panel = Image.new("RGB", (panel_width, panel_height), "white")
            panel.paste(clean, ((panel_width - clean.width) // 2, (panel_height - clean.height) // 2))
        sheet.paste(panel, (x, y + header_height))
        draw.rectangle((x, y + header_height, x + panel_width, y + header_height + panel_height), outline="#777777", width=1)
    sheet.save(destination, format="PNG", optimize=True)
    return destination


def render_task(
    task_id: str,
    spec_path: Path,
    *,
    task_root_override: Path | None = None,
) -> dict[str, Any]:
    task_dir, request, state = read_task(task_id, task_root_override)
    item_ids = [item["id"] for item in request["items"]]
    specs = parse_spec_bundle(load_json(spec_path.resolve()), item_ids)
    spec_bundle_sha = sha256_file(spec_path.resolve())
    event(task_dir, "render", "started", spec_sha256=spec_bundle_sha)
    for item in request["items"]:
        item_id = item["id"]
        item_state = state["items"][item_id]
        item_dir = task_dir / "items" / item_id
        spec = specs[item_id]
        spec_schema_version = int(spec.get("schema_version", 0))
        if spec_schema_version not in {2, 3} or spec.get("renderer") != item["renderer"]:
            raise ValueError(f"spec mismatch for item {item_id}")
        if item["legacy_contract"] and spec_schema_version != 2:
            raise ValueError("legacy request tasks require legacy schema v2 specs")
        if not item["legacy_contract"] and spec_schema_version != 3:
            raise ValueError("formal request v2 tasks require diagram_spec schema v3")
        if spec_schema_version == 3:
            if spec.get("purpose") != item["purpose"] or spec.get("style_profile") != item["style_profile"]:
                raise ValueError(f"spec purpose/style mismatch for item {item_id}")
        anchors: dict[str, list[float]] | None = None
        background: str | None = None
        accepted_reference_paths: list[tuple[str, Path]] = []
        if item["mode"] == "annotate":
            if sha256_file(Path(item_state["source_path"])) != item_state["source_sha256"]:
                raise RuntimeError(f"source image changed for item {item_id}")
            anchors, background = calibration_anchors(item_dir, item_state)
            proposal = load_json(Path(item_state["coordinate_map"]))
            expected_size = proposal["sanitized"]["size"]
            canvas = spec.get("canvas", {})
            if [canvas.get("width"), canvas.get("height")] != expected_size:
                raise ValueError(f"annotation canvas must match sanitized image for item {item_id}")
        else:
            reference_review = validate_reference_review(item_dir, item_state)
            atomic_write_json(item_dir / "accepted_references.json", reference_review)
            report_by_hash = {
                str(row["sha256"]): row
                for row in load_json(Path(item_state["reference_report"]))["results"]
                if isinstance(row, dict)
            }
            for index, digest in enumerate(reference_review["accepted_sha256"], start=1):
                candidate = (KB_ROOT / Path(str(report_by_hash[digest]["relative_path"]))).resolve()
                ensure_inside(candidate, KB_ROOT)
                accepted_reference_paths.append((f"reference {index}", candidate))
        staging = item_dir / "staging"
        if staging.exists():
            ensure_inside(staging, task_dir)
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        item_spec_path = item_dir / "diagram_spec.json"
        atomic_write_json(item_spec_path, spec)
        if item["renderer"] == "scene":
            request_context = {
                "purpose": item["purpose"],
                "style_profile": item["style_profile"],
                "diagram_intent": item["diagram_intent"],
            }
            compiled = compile_scene(spec, anchors=anchors, background_image=background, request_context=request_context)
            low_level_path = item_dir / "compiled_diagram.json"
            atomic_write_json(low_level_path, compiled["low_level_spec"])
            report = render_low_level(compiled["low_level_spec"], low_level_path, staging)
            if not report.get("ok"):
                raise RuntimeError(f"scene render validation failed for {item_id}: {report}")
            compiler_summary = compiled["summary"]
            geometry_report = compiled["geometry_report"]
            geometry_report["assertions"].append(
                assertion(
                    "render.no-collisions-or-bounds-errors",
                    "render",
                    not report["validation"]["issues"],
                    measured={"issues": report["validation"]["issues"]},
                    detail="rendered labels and primitives must remain inside the canvas without collisions",
                )
            )
            geometry_report["assertion_count"] = len(geometry_report["assertions"])
            geometry_report["failed_assertions"] = [row["id"] for row in geometry_report["assertions"] if not row.get("ok")]
            geometry_report["ok"] = not geometry_report["failed_assertions"]
        else:
            report = render_plot(spec, staging)
            compiler_summary = None
            geometry_report = finalize_report(
                schema_version=spec_schema_version,
                coordinate_space="plot_data",
                assertions=[
                    assertion(
                        "plot.numeric-and-layout-validation",
                        "plot",
                        bool(report.get("ok")),
                        measured={"series_count": len(report.get("series", []))},
                        detail="plot renderer accepted finite data, axes, units and output bounds",
                    )
                ],
                metadata={"purpose": item["purpose"], "style_profile": item["style_profile"]},
            )
        if not geometry_report.get("ok"):
            raise RuntimeError(f"geometry validation failed for {item_id}: {geometry_report.get('failed_assertions')}")
        geometry_report_path = staging / "geometry_report.json"
        atomic_write_json(geometry_report_path, geometry_report)
        review_references = accepted_reference_paths
        if item["mode"] == "annotate" and background:
            review_references = [("source", Path(background))]
        create_review_sheet(
            output_image=staging / "diagram.png",
            reference_images=review_references,
            destination=staging / "review_sheet.png",
        )
        artifacts: dict[str, dict[str, Any]] = {}
        for path in sorted(staging.iterdir()):
            if path.is_file():
                artifacts[path.name] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
        manifest = {
            "schema_version": 1,
            "item_id": item_id,
            "request_sha256": state["request_sha256"],
            "spec_sha256": sha256_file(item_spec_path),
            "renderer": item["renderer"],
            "model_profile": item["model_profile"],
            "purpose": item["purpose"],
            "style_profile": item["style_profile"],
            "spec_schema_version": spec_schema_version,
            "compiler_summary": compiler_summary,
            "geometry_report_sha256": sha256_file(geometry_report_path),
            "artifacts": artifacts,
        }
        manifest["manifest_sha256"] = sha256_json({key: value for key, value in manifest.items() if key != "manifest_sha256"})
        atomic_write_json(item_dir / "render_manifest.json", manifest)
        item_state.update(
            {
                "status": "needs_semantic_review",
                "spec_sha256": manifest["spec_sha256"],
                "spec_schema_version": spec_schema_version,
                "render_manifest": str(item_dir / "render_manifest.json"),
                "render_manifest_sha256": sha256_file(item_dir / "render_manifest.json"),
                "geometry_report": str(geometry_report_path),
                "geometry_report_sha256": manifest["geometry_report_sha256"],
                "next_action": "include this item in semantic_review.json",
            }
        )
    review_items: list[dict[str, Any]] = []
    for item_id in item_ids:
        item_state = state["items"][item_id]
        semantic_assertion_ids = [
            "matches-request-purpose",
            "objects-and-relations-are-physically-correct",
            "labels-and-symbols-are-unambiguous",
        ]
        if item_state["mode"] == "original":
            semantic_assertion_ids.append("follows-reviewed-reference-rules")
        review_items.append(
            {
                "id": item_id,
                "status": "needs_review",
                "render_manifest_sha256": item_state["render_manifest_sha256"],
                "geometry_report_sha256": item_state["geometry_report_sha256"],
                "artifacts": load_json(Path(item_state["render_manifest"]))["artifacts"],
                "semantic_assertions": [
                    {"id": assertion_id, "status": "needs_review"}
                    for assertion_id in semantic_assertion_ids
                ],
                "uncertainties": [],
            }
        )
    review_template = {
        "schema_version": 2,
        "status": "needs_review",
        "reviewer_type": None,
        "request_sha256": state["request_sha256"],
        "spec_bundle_sha256": spec_bundle_sha,
        "items": review_items,
        "uncertainties": [],
    }
    atomic_write_json(task_dir / "semantic_review.template.json", review_template)
    state["status"] = "needs_semantic_review"
    state["spec_bundle_sha256"] = spec_bundle_sha
    state["next_action"] = "complete semantic_review.json and run verify"
    write_state(task_dir, state)
    event(task_dir, "render", "needs_semantic_review", item_count=len(item_ids))
    return {"ok": True, "task_dir": str(task_dir), "review_template": str(task_dir / "semantic_review.template.json"), "state": state}


def verify_review(review: dict[str, Any], state: dict[str, Any]) -> None:
    approved_reviewer(review, "semantic review")
    if review.get("request_sha256") != state["request_sha256"]:
        raise RuntimeError("semantic review request hash mismatch")
    if review.get("spec_bundle_sha256") != state.get("spec_bundle_sha256"):
        raise RuntimeError("semantic review spec hash mismatch")
    raw_items = review.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("semantic review items must be an array")
    reviews = {str(item.get("id")): item for item in raw_items if isinstance(item, dict)}
    if set(reviews) != set(state["items"]):
        raise RuntimeError("semantic review item ids do not match task")
    for item_id, item_state in state["items"].items():
        item_review = reviews[item_id]
        if item_review.get("status") != "approved" or item_review.get("uncertainties"):
            raise RuntimeError(f"semantic review is not clean for item {item_id}")
        if item_review.get("render_manifest_sha256") != item_state["render_manifest_sha256"]:
            raise RuntimeError(f"semantic review manifest hash mismatch for item {item_id}")
        manifest_path = Path(item_state["render_manifest"])
        if not manifest_path.is_file() or sha256_file(manifest_path) != item_state["render_manifest_sha256"]:
            raise RuntimeError(f"render manifest changed or is missing for item {item_id}")
        manifest = load_json(manifest_path)
        geometry_path = Path(item_state["geometry_report"])
        if not geometry_path.is_file() or sha256_file(geometry_path) != item_state.get("geometry_report_sha256"):
            raise RuntimeError(f"geometry report changed or is missing for item {item_id}")
        geometry_report = load_json(geometry_path)
        if not geometry_report.get("ok") or geometry_report.get("failed_assertions"):
            raise RuntimeError(f"automatic geometry assertions failed for item {item_id}")
        if item_review.get("geometry_report_sha256") != item_state.get("geometry_report_sha256"):
            raise RuntimeError(f"semantic review geometry hash mismatch for item {item_id}")
        if manifest.get("geometry_report_sha256") != item_state.get("geometry_report_sha256"):
            raise RuntimeError(f"render manifest geometry hash mismatch for item {item_id}")
        if not item_state.get("legacy_contract") and int(manifest.get("spec_schema_version", 0)) != 3:
            raise RuntimeError(f"formal item lacks spec schema v3 in render manifest: {item_id}")
        expected_semantic_ids = {
            "matches-request-purpose",
            "objects-and-relations-are-physically-correct",
            "labels-and-symbols-are-unambiguous",
        }
        if item_state["mode"] == "original":
            expected_semantic_ids.add("follows-reviewed-reference-rules")
        raw_assertions = item_review.get("semantic_assertions")
        if not isinstance(raw_assertions, list):
            raise RuntimeError(f"semantic assertions are missing for item {item_id}")
        semantic_statuses = {
            str(row.get("id")): row.get("status")
            for row in raw_assertions
            if isinstance(row, dict)
        }
        if set(semantic_statuses) != expected_semantic_ids or any(status != "approved" for status in semantic_statuses.values()):
            raise RuntimeError(f"semantic assertions are incomplete for item {item_id}")
        if item_review.get("artifacts") != manifest.get("artifacts"):
            raise RuntimeError(f"semantic review artifact hashes mismatch for item {item_id}")
        staging = Path(item_state["render_manifest"]).parent / "staging"
        for name, record in manifest["artifacts"].items():
            path = staging / name
            if not path.is_file() or sha256_file(path) != record["sha256"]:
                raise RuntimeError(f"rendered artifact changed after review: {item_id}/{name}")


def verify_task(
    task_id: str,
    review_path: Path,
    *,
    task_root_override: Path | None = None,
    output_root_override: Path | None = None,
) -> dict[str, Any]:
    task_dir, request, state = read_task(task_id, task_root_override)
    output_dir = resolve_output_dir(request, output_root_override)
    final_dir = output_dir / task_id
    if final_dir.exists():
        completion_path = final_dir / "completion.json"
        if completion_path.is_file() and load_json(completion_path).get("request_sha256") == state["request_sha256"]:
            state["status"] = "complete"
            state["published"] = str(final_dir)
            write_state(task_dir, state)
            return {"ok": True, "idempotent": True, "published": str(final_dir), "state": state}
        raise FileExistsError(f"publish target already exists with different content: {final_dir}")
    if request.get("schema_version") != 2 or any(
        item.get("legacy_contract") or int(state["items"][item["id"]].get("spec_schema_version", 0)) != 3
        for item in request["items"]
    ):
        raise RuntimeError("legacy request/spec may render drafts but cannot publish; migrate to request v2 and spec v3")
    review = load_json(review_path.resolve())
    verify_review(review, state)
    publish = task_dir / "publish_pending"
    if publish.exists():
        ensure_inside(publish, task_dir)
        shutil.rmtree(publish)
    publish.mkdir(parents=True)
    published_items: dict[str, Any] = {}
    for item_id, item_state in state["items"].items():
        source = Path(item_state["render_manifest"]).parent / "staging"
        target = publish / item_id
        shutil.copytree(source, target)
        manifest = load_json(Path(item_state["render_manifest"]))
        atomic_write_json(target / "render_manifest.json", manifest)
        provenance_dir = target / "provenance"
        provenance_dir.mkdir()
        provenance_sources = [
            Path(item_state["render_manifest"]).parent / "diagram_spec.json",
            Path(item_state["geometry_report"]),
        ]
        if item_state["mode"] == "annotate":
            provenance_sources.extend(
                [
                    Path(item_state["coordinate_map"]),
                    Path(item_state["render_manifest"]).parent / "calibration_review.json",
                ]
            )
        else:
            provenance_sources.extend(
                [
                    Path(item_state["reference_report"]),
                    Path(item_state["render_manifest"]).parent / "reference_review.json",
                    Path(item_state["render_manifest"]).parent / "accepted_references.json",
                ]
            )
        provenance: dict[str, str] = {}
        for provenance_source in provenance_sources:
            if not provenance_source.is_file():
                raise FileNotFoundError(f"missing provenance file: {provenance_source}")
            destination = provenance_dir / provenance_source.name
            shutil.copy2(provenance_source, destination)
            provenance[destination.name] = sha256_file(destination)
        published_items[item_id] = {"manifest": manifest, "provenance": provenance}
    completion = {
        "schema_version": 1,
        "ok": True,
        "task_id": task_id,
        "completed_at": utc_now(),
        "request_sha256": state["request_sha256"],
        "spec_bundle_sha256": state["spec_bundle_sha256"],
        "semantic_review_sha256": sha256_file(review_path.resolve()),
        "items": {
            item_id: {
                "render_manifest_sha256": value["manifest"]["manifest_sha256"],
                "geometry_report_sha256": value["manifest"]["geometry_report_sha256"],
                "provenance": value["provenance"],
            }
            for item_id, value in published_items.items()
        },
    }
    atomic_write_json(publish / "completion.json", completion)
    output_dir.mkdir(parents=True, exist_ok=True)
    publish_error: PermissionError | None = None
    for _attempt in range(5):
        try:
            os.replace(publish, final_dir)
            publish_error = None
            break
        except PermissionError as exc:
            publish_error = exc
            time.sleep(0.1)
    if publish_error is not None:
        raise publish_error
    state["status"] = "complete"
    state["published"] = str(final_dir)
    state["completion_sha256"] = sha256_file(final_dir / "completion.json")
    state["next_action"] = None
    write_state(task_dir, state)
    event(task_dir, "verify", "complete", published=str(final_dir))
    return {"ok": True, "idempotent": False, "published": str(final_dir), "completion": completion, "state": state}


def resume(task_id: str, *, task_root_override: Path | None = None) -> dict[str, Any]:
    task_dir, _request, state = read_task(task_id, task_root_override)
    for item_id, item in state["items"].items():
        source_path = item.get("source_path")
        if source_path and sha256_file(Path(source_path)) != item.get("source_sha256"):
            state["status"] = "blocked"
            state["next_action"] = f"source image changed for {item_id}; create a new task"
            write_state(task_dir, state)
            event(task_dir, "resume", "blocked", item_id=item_id, reason="source_changed")
            break
    return {"ok": state["status"] != "blocked", "task_dir": str(task_dir), "state": state}


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task-root", help=argparse.SUPPRESS)
    parser.add_argument("--output-root", help=argparse.SUPPRESS)
    parser.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--request", required=True)
    prepare_parser.add_argument("--task-id", required=True)
    add_common_options(prepare_parser)
    render_parser = sub.add_parser("render")
    render_parser.add_argument("--task-id", required=True)
    render_parser.add_argument("--spec", required=True)
    add_common_options(render_parser)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--task-id", required=True)
    verify_parser.add_argument("--review", required=True)
    add_common_options(verify_parser)
    resume_parser = sub.add_parser("resume")
    resume_parser.add_argument("--task-id", required=True)
    add_common_options(resume_parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    task_root_override = Path(args.task_root).resolve() if args.task_root else None
    output_root_override = Path(args.output_root).resolve() if args.output_root else None
    try:
        if args.command == "prepare":
            result = prepare(
                Path(args.request),
                args.task_id,
                task_root_override=task_root_override,
                output_root_override=output_root_override,
                test_mode=bool(args.test_mode),
            )
        elif args.command == "render":
            result = render_task(args.task_id, Path(args.spec), task_root_override=task_root_override)
        elif args.command == "verify":
            result = verify_task(
                args.task_id,
                Path(args.review),
                task_root_override=task_root_override,
                output_root_override=output_root_override,
            )
        else:
            result = resume(args.task_id, task_root_override=task_root_override)
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else str(result.get("published") or result.get("task_dir")))
        return 0 if result.get("ok") else 1
    except Exception as exc:  # noqa: BLE001
        task_id = getattr(args, "task_id", None)
        if task_id:
            try:
                task_dir = task_base(task_id, task_root_override)
                if task_dir.exists():
                    event(task_dir, args.command, "blocked", error=f"{type(exc).__name__}: {exc}")
                    state_path = task_dir / "state.json"
                    if state_path.is_file():
                        failed_state = load_json(state_path)
                    else:
                        request_path = task_dir / "request.json"
                        failed_state = {
                            "schema_version": 1,
                            "task_id": task_id,
                            "created_at": utc_now(),
                            "request_sha256": sha256_file(request_path) if request_path.is_file() else None,
                            "items": {},
                        }
                    failed_state["status"] = "blocked"
                    failed_state["error"] = f"{type(exc).__name__}: {exc}"
                    failed_state["next_action"] = "resolve the reported error, then rerun the same stage"
                    write_state(task_dir, failed_state)
            except Exception:
                pass
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
