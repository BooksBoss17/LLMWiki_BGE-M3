#!/usr/bin/env python3
"""Build bounded, hash-backed source manifests for the P0/P1 campaigns.

The manifest is an ignored runtime artifact.  It maps the source question
ordinal used by the imported Markdown to a DOCX paragraph range and extracts
only the embedded media occurrences needed by the selected campaign items.
It never edits raw questions, assets, Wiki pages, or RAG data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from lxml import etree


NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
}
RID = "{%s}" % NS["r"]
QUESTION_HEADING = re.compile(r"^\s*【(?:典例|变式)[^】]+】")
NUMBERED_HEADING = re.compile(r"^\s*(\d{1,3})([．.)])")
SAFE_GROUP = re.compile(r"[^A-Za-z0-9._-]+")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def normalize_source_key(raw: str) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    marker = "source-library/"
    if marker in value:
        return value[value.find(marker) :]
    return value.removeprefix("./").removeprefix("../")


def load_campaign_items(kb_root: Path, campaign_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    by_source: dict[str, list[dict[str, Any]]] = {}
    state_root = kb_root / "skills/_ops/runtime/state/role_d_curation_campaigns"
    for campaign_id in campaign_ids:
        state_path = state_root / campaign_id / "campaign.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        for item in state.get("items", {}).values():
            if not isinstance(item, dict) or item.get("status") in {"applied", "no_change", "blocked"}:
                continue
            source = normalize_source_key(item.get("source_path"))
            if not source:
                continue
            item = dict(item)
            item["campaign_id"] = campaign_id
            item["source_path"] = source
            by_source.setdefault(source, []).append(item)
    return by_source


def relationship_map(root: etree._Element) -> dict[str, dict[str, str]]:
    return {
        str(rel.get("Id")): {
            "target": str(rel.get("Target", "")),
            "type": str(rel.get("Type", "")).rsplit("/", 1)[-1],
        }
        for rel in root
        if rel.get("Id")
    }


def word_extent(node: etree._Element) -> dict[str, Any] | None:
    container = node.xpath("ancestor::wp:inline[1]", namespaces=NS)
    if not container:
        container = node.xpath("ancestor::wp:anchor[1]", namespaces=NS)
    if container:
        extent = container[0].find("wp:extent", NS)
        if extent is not None:
            cx = int(extent.get("cx", "0"))
            cy = int(extent.get("cy", "0"))
            return {"source": "wp:extent", "cx_emu": cx, "cy_emu": cy, "aspect": round(cx / cy, 8) if cy else None}
    shapes = node.xpath("ancestor::v:shape[1]", namespaces=NS)
    if shapes:
        style = shapes[0].get("style", "")
        width = re.search(r"(?:^|;)width:([0-9.]+)pt", style)
        height = re.search(r"(?:^|;)height:([0-9.]+)pt", style)
        if width and height:
            w = float(width.group(1))
            h = float(height.group(1))
            return {"source": "VML shape style", "width_pt": w, "height_pt": h, "aspect": round(w / h, 8) if h else None}
    return None


def paragraph_tokens(paragraph: etree._Element) -> tuple[str, list[dict[str, Any]]]:
    pieces: list[str] = []
    usages: list[dict[str, Any]] = []
    for node in paragraph.iter():
        local = etree.QName(node).localname
        namespace = etree.QName(node).namespace
        if namespace == NS["w"] and local == "t":
            pieces.append(node.text or "")
        elif namespace == NS["w"] and local == "tab":
            pieces.append("\t")
        elif namespace == NS["w"] and local in {"br", "cr"}:
            pieces.append("\n")
        elif namespace == NS["a"] and local == "blip":
            rid = node.get(RID + "embed")
            if rid:
                pieces.append(f"{{{{IMAGE:{rid}}}}}")
                usages.append({"relationship_id": rid, "word_extent": word_extent(node)})
        elif namespace == NS["v"] and local == "imagedata":
            rid = node.get(RID + "id")
            if rid:
                pieces.append(f"{{{{IMAGE:{rid}}}}}")
                ole = node.xpath("ancestor::w:object[1]//o:OLEObject", namespaces=NS)
                usages.append({
                    "relationship_id": rid,
                    "word_extent": word_extent(node),
                    "paired_ole_relationship_id": ole[0].get(RID + "id") if ole else None,
                    "ole_prog_id": ole[0].get("ProgID") if ole else None,
                })
    return "".join(pieces), usages


def is_heading(text: str) -> bool:
    if QUESTION_HEADING.match(text):
        return True
    match = NUMBERED_HEADING.match(text)
    if not match:
        return False
    # Knowledge-point outlines use the Chinese enumeration comma (1、...)
    # and must never become question boundaries.  Numbered source questions
    # use a full stop/period and normally start with a year parenthesis,
    # "如图", or a question-style subject; solution prose starts with
    # "由/根据/设/解得" and is intentionally excluded.
    rest = text[match.end() :].lstrip()
    if not rest or rest.startswith(("答案", "详解", "解析")):
        return False
    if rest.startswith(("（", "(", "如图", "关于", "某", "一", "在", "将", "若", "已知", "空间", "半径", "质量")):
        return True
    return False


def heading_ordinal(text: str) -> int | None:
    match = NUMBERED_HEADING.match(text)
    if match:
        return int(match.group(1))
    return None


def group_name(source_key: str, source_hash: str) -> str:
    stem = SAFE_GROUP.sub("_", Path(source_key).stem).strip("._-") or "source"
    return f"p0p1_{stem[:80]}_{source_hash[:12]}"


def source_entry(target: str) -> str:
    target = target.replace("\\", "/")
    return str(PurePosixPath("word") / PurePosixPath(target).as_posix().lstrip("/"))


def build_manifest(kb_root: Path, source_key: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    source = (kb_root / source_key).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.lower() != ".docx":
        raise ValueError(f"this preparer currently supports DOCX only: {source_key}")
    source_hash = sha256_path(source)
    group_root = kb_root / "skills/_ops/runtime/reports/role_d_curation/source_groups" / group_name(source_key, source_hash)
    image_root = group_root / "images"
    image_root.mkdir(parents=True, exist_ok=True)
    wanted: dict[int, list[dict[str, Any]]] = {}
    for item in items:
        raw = str(item.get("source_question_no") or "")
        numbers = re.findall(r"\d+", raw)
        if not numbers:
            raise ValueError(f"cannot map source_question_no={raw!r} for {item.get('question_id')}")
        wanted.setdefault(int(numbers[-1]), []).append(item)

    with zipfile.ZipFile(source) as archive:
        document_root = etree.fromstring(archive.read("word/document.xml"))
        rels_root = etree.fromstring(archive.read("word/_rels/document.xml.rels"))
        relationships = relationship_map(rels_root)
        paragraphs = document_root.xpath("//w:document/w:body//w:p", namespaces=NS)
        parsed: list[tuple[int, str, list[dict[str, Any]]]] = []
        for index, paragraph in enumerate(paragraphs):
            text, usages = paragraph_tokens(paragraph)
            if is_heading(text):
                parsed.append((index, text, usages))
        if not parsed:
            raise ValueError(f"no question headings found in {source_key}")

        questions: list[dict[str, Any]] = []
        ordinal = 0
        for position, (start, heading, _heading_usages) in enumerate(parsed):
            ordinal += 1
            if ordinal not in wanted:
                continue
            end = parsed[position + 1][0] - 1 if position + 1 < len(parsed) else len(paragraphs) - 1
            for item in wanted[ordinal]:
                qid = str(item["question_id"]).upper()
                relationships_out: list[dict[str, Any]] = []
                occurrences = 0
                for block in range(start, end + 1):
                    text, usages = paragraph_tokens(paragraphs[block])
                    for usage in usages:
                        rid = str(usage.get("relationship_id") or "")
                        rel = relationships.get(rid)
                        if not rel or rel.get("type") != "image":
                            continue
                        entry = source_entry(rel["target"])
                        try:
                            data = archive.read(entry)
                        except KeyError:
                            continue
                        suffix = Path(rel["target"]).suffix.lower() or ".bin"
                        target = image_root / f"{qid}_{occurrences + 1:04d}_{rid}{suffix}"
                        if not target.is_file() or sha256_path(target) != sha256_bytes(data):
                            target.write_bytes(data)
                        occurrences += 1
                        relationships_out.append({
                            "relationship_id": rid,
                            "target": rel["target"],
                            "block": block,
                            "extracted_path": target.relative_to(kb_root).as_posix(),
                            "sha256": sha256_bytes(data),
                            "bytes": len(data),
                            "word_extent": usage.get("word_extent"),
                        })
                context_parts = []
                for block in range(start, end + 1):
                    text, _ = paragraph_tokens(paragraphs[block])
                    if text.strip():
                        context_parts.append(f"[{block}] {text}")
                questions.append({
                    "source_question_no": str(item.get("source_question_no") or ordinal),
                    "heading": heading,
                    "start_block": start,
                    "end_block": end,
                    "question_context": "\n".join(context_parts),
                    "relationships": relationships_out,
                    "question_id": qid,
                    "target_path": item["target_path"],
                    "current_sha256": item["current_sha256"],
                })

    questions.sort(key=lambda value: (int(re.findall(r"\d+", str(value["source_question_no"]))[-1]), value["question_id"]))
    manifest = {
        "schema_version": 2,
        "source_path": source_key,
        "source_sha256": source_hash,
        "source_bytes": source.stat().st_size,
        "question_count": len(questions),
        "mapped_target_count": len(questions),
        "mapping_method": "stable_source_question_ordinal_from_DOCX_question_headings",
        "source_integrity_verified_after_extraction": sha256_path(source) == source_hash,
        "questions": questions,
    }
    atomic_write(group_root / "manifest.json", manifest)
    return {"manifest": (group_root / "manifest.json").relative_to(kb_root).as_posix(), "source_path": source_key, "source_sha256": source_hash, "question_count": len(questions), "group": group_root.name}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-root", default=".")
    parser.add_argument("--campaign", action="append", required=True)
    parser.add_argument("--docx-only", action="store_true", help="skip non-DOCX sources for staged preparation")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = Path(args.kb_root).resolve()
    by_source = load_campaign_items(root, args.campaign)
    results = []
    skipped = []
    for source_key in sorted(by_source):
        if args.docx_only and Path(source_key).suffix.lower() != ".docx":
            skipped.append({"source_path": source_key, "reason": "non_docx_staged_later"})
            continue
        results.append(build_manifest(root, source_key, by_source[source_key]))
    payload = {"ok": True, "campaigns": args.campaign, "sources": results, "source_count": len(results), "skipped": skipped}
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
