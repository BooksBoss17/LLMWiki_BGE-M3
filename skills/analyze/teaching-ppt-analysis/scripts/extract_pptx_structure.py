#!/usr/bin/env python3
"""Extract slide text, notes, and media inventory from a PPTX file."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def read_xml(zip_file: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        return ET.fromstring(zip_file.read(name))
    except KeyError:
        return None


def get_texts(root: ET.Element | None) -> list[str]:
    if root is None:
        return []
    blocks: list[str] = []
    for txbody in root.findall(".//p:txBody", NS):
        paragraphs: list[str] = []
        for para in txbody.findall(".//a:p", NS):
            parts = [t.text for t in para.findall(".//a:t", NS) if t.text]
            text = re.sub(r"\s+", " ", "".join(parts)).strip()
            if text:
                paragraphs.append(text)
        if paragraphs:
            blocks.append("\n".join(paragraphs))
    if not blocks:
        blocks = [t.text.strip() for t in root.findall(".//a:t", NS) if t.text and t.text.strip()]
    deduped: list[str] = []
    for block in blocks:
        if not deduped or deduped[-1] != block:
            deduped.append(block)
    return deduped


def rels_map(zip_file: zipfile.ZipFile, rels_path: str) -> dict[str, dict[str, str]]:
    root = read_xml(zip_file, rels_path)
    mapping: dict[str, dict[str, str]] = {}
    if root is None:
        return mapping
    for rel in root.findall("rel:Relationship", NS):
        rel_id = rel.attrib.get("Id")
        if rel_id:
            mapping[rel_id] = {key.split("}")[-1]: value for key, value in rel.attrib.items()}
    return mapping


def slide_sort_key(name: str) -> int:
    match = re.search(r"slide(\d+)\.xml$", name)
    return int(match.group(1)) if match else 10**9


def normalize_package_path(base: str, target: str) -> str:
    parts: list[str] = []
    for part in (str(Path(base).parent / target).replace("\\", "/")).split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part and part != ".":
            parts.append(part)
    return "/".join(parts)


def extract(pptx: Path, max_notes_chars: int) -> tuple[dict, str, str]:
    with zipfile.ZipFile(pptx) as zip_file:
        names = zip_file.namelist()
        slide_names = sorted(
            [name for name in names if re.match(r"ppt/slides/slide\d+\.xml$", name)],
            key=slide_sort_key,
        )
        media = []
        for name in names:
            if name.startswith("ppt/media/"):
                info = zip_file.getinfo(name)
                media.append(
                    {
                        "path": name,
                        "size_bytes": info.file_size,
                        "size_mb": round(info.file_size / 1024 / 1024, 3),
                        "ext": Path(name).suffix.lower(),
                    }
                )
        ext_counts: dict[str, int] = {}
        for item in media:
            ext_counts[item["ext"]] = ext_counts.get(item["ext"], 0) + 1

        slides = []
        extracted_text = []
        for index, slide_name in enumerate(slide_names, start=1):
            root = read_xml(zip_file, slide_name)
            texts = get_texts(root)
            joined_text = "\n".join(texts)
            title = ""
            for block in texts:
                first = block.split("\n", 1)[0].strip()
                if first:
                    title = first[:80]
                    break

            blips = root.findall(".//a:blip", NS) if root is not None else []
            shapes = root.findall(".//p:sp", NS) if root is not None else []
            pics = root.findall(".//p:pic", NS) if root is not None else []
            graphic_frames = root.findall(".//p:graphicFrame", NS) if root is not None else []
            rels = rels_map(zip_file, f"ppt/slides/_rels/{Path(slide_name).name}.rels")

            slide_media = []
            notes_text = ""
            for blip in blips:
                rel_id = blip.attrib.get(f"{{{NS['r']}}}embed") or blip.attrib.get(f"{{{NS['r']}}}link")
                if rel_id and rel_id in rels:
                    slide_media.append(rels[rel_id].get("Target", ""))
            for rel in rels.values():
                if rel.get("Type", "").endswith("/notesSlide"):
                    notes_path = normalize_package_path(slide_name, rel.get("Target", ""))
                    notes_text = "\n".join(get_texts(read_xml(zip_file, notes_path)))

            slides.append(
                {
                    "slide": index,
                    "xml": slide_name,
                    "title": title,
                    "text_blocks": texts,
                    "text_chars": len(joined_text),
                    "shape_count": len(shapes),
                    "picture_count": len(pics),
                    "graphic_frame_count": len(graphic_frames),
                    "embedded_media_count": len(slide_media),
                    "embedded_media": slide_media[:20],
                    "notes_chars": len(notes_text),
                    "notes_text": notes_text[:max_notes_chars],
                }
            )
            extracted_text.append(
                f"## 幻灯片 {index}: {title or '[无标题]'}\n"
                + (joined_text or "[无可提取文本]")
                + (f"\n\n备注:\n{notes_text}" if notes_text else "")
            )

    summary = {
        "file": str(pptx),
        "size_bytes": pptx.stat().st_size,
        "size_mb": round(pptx.stat().st_size / 1024 / 1024, 2),
        "slide_count": len(slides),
        "media_count": len(media),
        "media_ext_counts": ext_counts,
        "largest_media": sorted(media, key=lambda item: item["size_bytes"], reverse=True)[:20],
        "slides": slides,
    }

    lines = [
        "# PPT结构提取报告",
        "",
        f"文件：{pptx.name}",
        f"大小：{summary['size_mb']} MB",
        f"幻灯片数：{len(slides)}",
        f"媒体文件数：{len(media)}，类型统计：{ext_counts}",
        "",
        "## 最大媒体文件 Top 10",
    ]
    for item in summary["largest_media"][:10]:
        lines.append(f"- {item['path']} | {item['size_mb']} MB")
    lines.extend(["", "## 幻灯片逐页摘要"])
    for slide in slides:
        snippet = " / ".join(block.replace("\n", " / ") for block in slide["text_blocks"][:3])
        if len(snippet) > 180:
            snippet = snippet[:180] + "..."
        lines.append(f"### {slide['slide']:02d}. {slide['title'] or '[无标题]'}")
        lines.append(
            f"- 文本字符：{slide['text_chars']}；图片：{slide['picture_count']}；"
            f"图形框：{slide['graphic_frame_count']}；嵌入媒体：{slide['embedded_media_count']}；"
            f"备注字符：{slide['notes_chars']}"
        )
        lines.append(f"- 文本摘录：{snippet or '[无可提取文本]'}")

    return summary, "\n\n---\n\n".join(extracted_text), "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract PPTX structure for teaching review.")
    parser.add_argument("--pptx", required=True, help="Input .pptx file.")
    parser.add_argument("--out-dir", required=True, help="Directory for JSON/Markdown reports.")
    parser.add_argument("--max-notes-chars", type=int, default=1000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pptx = Path(args.pptx).resolve()
    if not pptx.exists():
        raise FileNotFoundError(pptx)
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    summary, extracted_text, report = extract(pptx, args.max_notes_chars)
    (out_dir / "ppt_structure.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "ppt_extracted_text.md").write_text(extracted_text, encoding="utf-8")
    (out_dir / "ppt_structure_report.md").write_text(report, encoding="utf-8")
    print(
        json.dumps(
            {
                "slides": summary["slide_count"],
                "media": summary["media_count"],
                "out_dir": str(out_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
