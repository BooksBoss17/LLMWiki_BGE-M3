#!/usr/bin/env python3
"""Render a PDF to slide images and a contact sheet for PPT review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_render_deps():
    try:
        import fitz  # type: ignore
        from PIL import Image, ImageDraw  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "Missing render dependencies. Install PyMuPDF and Pillow in the active Python environment."
        ) from exc
    return fitz, Image, ImageDraw


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render PDF pages to PNG slides and contact sheet.")
    parser.add_argument("--pdf", required=True, help="Input PDF path.")
    parser.add_argument("--out-dir", help="Directory for slide PNG files. Defaults to <pdf-dir>/slides.")
    parser.add_argument("--scale", type=float, default=1.5, help="PyMuPDF render scale.")
    parser.add_argument("--thumb-width", type=int, default=360)
    parser.add_argument("--thumb-height", type=int, default=203)
    parser.add_argument("--cols", type=int, default=4)
    parser.add_argument("--contact-sheet", help="Contact sheet output path. Defaults to <pdf-dir>/contact_sheet.png.")
    parser.add_argument("--no-contact-sheet", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fitz, Image, ImageDraw = load_render_deps()

    pdf = Path(args.pdf).resolve()
    if not pdf.exists():
        raise FileNotFoundError(pdf)
    out_dir = Path(args.out_dir).resolve() if args.out_dir else pdf.parent / "slides"
    out_dir.mkdir(parents=True, exist_ok=True)
    contact_sheet = Path(args.contact_sheet).resolve() if args.contact_sheet else pdf.parent / "contact_sheet.png"

    doc = fitz.open(str(pdf))
    slide_paths: list[Path] = []
    for index, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=fitz.Matrix(args.scale, args.scale), alpha=False)
        path = out_dir / f"slide_{index:02d}.png"
        pix.save(str(path))
        slide_paths.append(path)

    sheet_path = None
    if not args.no_contact_sheet:
        thumbs = []
        tile_w = args.thumb_width + 20
        tile_h = args.thumb_height + 42
        for path in slide_paths:
            image = Image.open(path).convert("RGB")
            image.thumbnail((args.thumb_width, args.thumb_height))
            canvas = Image.new("RGB", (tile_w, tile_h), "white")
            canvas.paste(image, ((tile_w - image.width) // 2, 32))
            draw = ImageDraw.Draw(canvas)
            draw.text((10, 8), path.stem.replace("slide_", "Slide "), fill="black")
            thumbs.append(canvas)
        rows = (len(thumbs) + args.cols - 1) // args.cols
        sheet = Image.new("RGB", (args.cols * tile_w, rows * tile_h), (240, 240, 240))
        for index, image in enumerate(thumbs):
            x = (index % args.cols) * tile_w
            y = (index // args.cols) * tile_h
            sheet.paste(image, (x, y))
        contact_sheet.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(contact_sheet, quality=95)
        sheet_path = contact_sheet

    manifest = {
        "pdf": str(pdf),
        "page_count": len(slide_paths),
        "slides": [str(path) for path in slide_paths],
        "contact_sheet": str(sheet_path) if sheet_path else None,
    }
    (out_dir.parent / "render_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
