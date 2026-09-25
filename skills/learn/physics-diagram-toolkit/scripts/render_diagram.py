#!/usr/bin/env python
"""Render validated physics diagram JSON to deterministic SVG and PNG."""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve()
KB_ROOT = SCRIPT.parents[4]
SESSION_ROOT = KB_ROOT / "output" / "learning_sessions"
KINDS = {"free_body", "coordinate", "vector", "trajectory", "annotation"}
PRIMITIVES = {"line", "arrow", "double_arrow", "text", "circle", "rect", "polygon", "polyline", "axis"}
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(root.resolve())
    return resolved


def number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    if not math.isfinite(float(value)):
        raise ValueError(f"{field} must be finite")
    return float(value)


def point(value: Any, field: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{field} must be [x, y]")
    return number(value[0], f"{field}[0]"), number(value[1], f"{field}[1]")


def color(value: Any, default: str = "#111111") -> str:
    candidate = str(value or default)
    if not COLOR_RE.fullmatch(candidate):
        raise ValueError(f"unsupported color: {candidate}; use #RRGGBB")
    return candidate.lower()


def find_chinese_font() -> Path:
    runtime = Path(os.environ.get("LLMWIKI_MODEL_RUNTIME_ROOT", ""))
    candidates = [
        runtime / "fonts" / "noto-sans-cjk-sc" / "2.004" / "NotoSansCJKsc-Regular.otf",
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError("no Chinese-capable font found")


def arrow_head(x1: float, y1: float, x2: float, y2: float, size: float = 14.0) -> list[tuple[float, float]]:
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < 1.0:
        raise ValueError("arrow length must be at least 1 pixel")
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    base_x, base_y = x2 - ux * size, y2 - uy * size
    return [(x2, y2), (base_x + px * size * 0.45, base_y + py * size * 0.45), (base_x - px * size * 0.45, base_y - py * size * 0.45)]


def sanitize_background(
    source: Path,
    destination: Path,
    width: int,
    height: int,
    background_color: str,
):
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Pillow is required; run install_skill_dependencies.py --install --all") from exc
    with Image.open(source) as image:
        foreground = image.convert("RGBA").resize((width, height))
        background = Image.new("RGBA", foreground.size, background_color)
        clean = Image.alpha_composite(background, foreground).convert("RGB")
        clean.save(destination, format="PNG", optimize=True)
        return clean.copy()


def load_font(font_path: Path, size: int):
    from PIL import ImageFont

    return ImageFont.truetype(str(font_path), size=size)


def validate_coord(x: float, y: float, width: int, height: int, field: str, issues: list[str]) -> None:
    if not (0 <= x <= width and 0 <= y <= height):
        issues.append(f"{field} outside canvas: ({x}, {y})")


def svg_image_tag(path: Path, width: int, height: int) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f'<image href="data:image/png;base64,{encoded}" x="0" y="0" width="{width}" height="{height}" />'


def render(spec: dict[str, Any], spec_path: Path, out_dir: Path) -> dict[str, Any]:
    try:
        from PIL import Image, ImageChops, ImageDraw
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Pillow is required; run install_skill_dependencies.py --install --all") from exc

    if int(spec.get("schema_version", 0)) != 1:
        raise ValueError("schema_version must be 1")
    kind = str(spec.get("kind", ""))
    if kind not in KINDS:
        raise ValueError(f"unsupported kind: {kind}")
    revision = spec.get("revision", 0)
    if not isinstance(revision, int) or isinstance(revision, bool) or not 0 <= revision <= 2:
        raise ValueError("revision must be an integer between 0 and 2")
    canvas = spec.get("canvas")
    if not isinstance(canvas, dict):
        raise ValueError("canvas must be an object")
    width, height = int(number(canvas.get("width"), "canvas.width")), int(number(canvas.get("height"), "canvas.height"))
    if not (200 <= width <= 4000 and 200 <= height <= 4000):
        raise ValueError("canvas dimensions must be between 200 and 4000 pixels")
    background_color = color(canvas.get("background"), "#ffffff")
    primitives = spec.get("primitives")
    if not isinstance(primitives, list) or not primitives:
        raise ValueError("primitives must be a non-empty array")

    out_dir.mkdir(parents=True, exist_ok=True)
    font_path = find_chinese_font()
    source_path: Path | None = None
    source_hash_before: str | None = None
    sanitized_background: Path | None = None
    if spec.get("background_image"):
        source_path = Path(str(spec["background_image"]))
        if not source_path.is_absolute():
            source_path = (spec_path.parent / source_path).resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        source_hash_before = sha256_file(source_path)
        sanitized_background = out_dir / "background_sanitized.png"
        image = sanitize_background(
            source_path,
            sanitized_background,
            width,
            height,
            background_color,
        )
    else:
        if kind == "annotation":
            raise ValueError("annotation kind requires background_image")
        image = Image.new("RGB", (width, height), background_color)
    base_image = image.copy()
    draw = ImageDraw.Draw(image)
    issues: list[str] = []
    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<defs><marker id=\"arrowhead\" markerWidth=\"10\" markerHeight=\"7\" refX=\"9\" refY=\"3.5\" orient=\"auto-start-reverse\"><polygon points=\"0 0, 10 3.5, 0 7\" fill=\"context-stroke\" /></marker></defs>",
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="{background_color}" />',
    ]
    if sanitized_background:
        svg_parts.append(svg_image_tag(sanitized_background, width, height))

    arrow_count = 0
    text_boxes: list[list[float]] = []
    text_records: list[dict[str, Any]] = []
    geometry_records: list[dict[str, Any]] = []

    def draw_text(
        x: float,
        y: float,
        text_value: str,
        size: int,
        fill: str,
        *,
        semantic_id: str = "",
        allow_overlap: bool = False,
    ) -> None:
        font = load_font(font_path, size)
        subscript_map = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
        math_match = re.fullmatch(r"([A-Za-z]+)(?:_([A-Za-z0-9]+)|([₀₁₂₃₄₅₆₇₈₉]+))", text_value)
        if math_match:
            base_text = math_match.group(1)
            subscript_text = (math_match.group(2) or math_match.group(3)).translate(subscript_map)
            subscript_size = max(10, int(size * 0.68))
            subscript_font = load_font(font_path, subscript_size)
            base_bbox = draw.textbbox((x, y), base_text, font=font)
            base_width = base_bbox[2] - base_bbox[0]
            subscript_x, subscript_y = x + base_width, y + size * 0.48
            subscript_bbox = draw.textbbox((subscript_x, subscript_y), subscript_text, font=subscript_font)
            bbox = (
                min(base_bbox[0], subscript_bbox[0]),
                min(base_bbox[1], subscript_bbox[1]),
                max(base_bbox[2], subscript_bbox[2]),
                max(base_bbox[3], subscript_bbox[3]),
            )
        else:
            base_text, subscript_text = text_value, ""
            subscript_font = None
            subscript_x = subscript_y = 0.0
            bbox = draw.textbbox((x, y), text_value, font=font)
        text_boxes.append([float(v) for v in bbox])
        text_records.append(
            {
                "text": text_value,
                "bbox": [float(v) for v in bbox],
                "semantic_id": semantic_id,
                "allow_overlap": allow_overlap,
            }
        )
        if bbox[0] < 0 or bbox[1] < 0 or bbox[2] > width or bbox[3] > height:
            issues.append(f"text outside canvas: {text_value}")
        draw.text((x, y), base_text, font=font, fill=fill)
        if subscript_text and subscript_font is not None:
            draw.text((subscript_x, subscript_y), subscript_text, font=subscript_font, fill=fill)
            svg_parts.append(
                f'<text x="{x:g}" y="{y + size:g}" font-family="Microsoft YaHei, Noto Sans CJK SC, sans-serif" font-size="{size}" font-style="italic" fill="{fill}">{html.escape(base_text)}<tspan baseline-shift="sub" font-size="68%">{html.escape(subscript_text)}</tspan></text>'
            )
        else:
            svg_parts.append(
                f'<text x="{x:g}" y="{y + size:g}" font-family="Microsoft YaHei, Noto Sans CJK SC, sans-serif" font-size="{size}" fill="{fill}">{html.escape(text_value)}</text>'
            )

    def draw_arrow(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        stroke: str,
        line_width: int,
        label: str = "",
        *,
        double: bool = False,
        semantic_id: str = "",
    ) -> None:
        nonlocal arrow_count
        arrow_count += 1
        head = arrow_head(x1, y1, x2, y2, max(10.0, line_width * 4.0))
        draw.line((x1, y1, x2, y2), fill=stroke, width=line_width)
        draw.polygon(head, fill=stroke)
        if double:
            draw.polygon(arrow_head(x2, y2, x1, y1, max(10.0, line_width * 4.0)), fill=stroke)
        marker_start = ' marker-start="url(#arrowhead)"' if double else ""
        svg_parts.append(
            f'<line x1="{x1:g}" y1="{y1:g}" x2="{x2:g}" y2="{y2:g}" stroke="{stroke}" stroke-width="{line_width}"{marker_start} marker-end="url(#arrowhead)" />'
        )
        if label:
            label_x = min(width - 1, max(0.0, x2 + 8.0))
            label_y = min(height - 1, max(0.0, y2 - 28.0))
            draw_text(label_x, label_y, label, 22, stroke, semantic_id=f"{semantic_id}.label", allow_overlap=True)

    def record_geometry(
        primitive_type: str,
        semantic_id: str,
        bounds: tuple[float, float, float, float],
        *,
        points: list[tuple[float, float]] | None = None,
    ) -> None:
        record = {
            "type": primitive_type,
            "semantic_id": semantic_id,
            "bbox": [float(value) for value in bounds],
        }
        if points:
            record["points"] = [[float(x), float(y)] for x, y in points]
        geometry_records.append(record)

    def dash_pattern(value: Any, field: str) -> list[float] | None:
        if value is None:
            return None
        if not isinstance(value, list) or len(value) < 2:
            raise ValueError(f"{field} must contain at least dash and gap lengths")
        result = [number(item, f"{field}[]") for item in value]
        if any(item <= 0 for item in result):
            raise ValueError(f"{field} values must be positive")
        return result

    def draw_dashed_segment(
        start: tuple[float, float],
        end: tuple[float, float],
        stroke: str,
        line_width: int,
        pattern: list[float],
    ) -> None:
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return
        ux, uy = dx / length, dy / length
        position = 0.0
        pattern_index = 0
        draw_on = True
        while position < length:
            step = pattern[pattern_index % len(pattern)]
            next_position = min(length, position + step)
            if draw_on:
                draw.line(
                    (
                        start[0] + ux * position,
                        start[1] + uy * position,
                        start[0] + ux * next_position,
                        start[1] + uy * next_position,
                    ),
                    fill=stroke,
                    width=line_width,
                )
            position = next_position
            pattern_index += 1
            draw_on = not draw_on

    for index, primitive in enumerate(primitives):
        if not isinstance(primitive, dict):
            raise ValueError(f"primitives[{index}] must be an object")
        primitive_type = str(primitive.get("type", ""))
        if primitive_type not in PRIMITIVES:
            raise ValueError(f"unsupported primitive type: {primitive_type}")
        stroke = color(primitive.get("color") or primitive.get("stroke"), "#111111")
        legacy_width = primitive.get("width", 4) if primitive_type not in {"rect"} else 4
        line_width = int(number(primitive.get("stroke_width", legacy_width), f"primitives[{index}].stroke_width"))
        if not 1 <= line_width <= 30:
            raise ValueError("line width must be 1-30")

        semantic_id = str(primitive.get("semantic_id", ""))
        if primitive_type in {"line", "arrow", "double_arrow"}:
            x1 = number(primitive.get("x1"), "x1")
            y1 = number(primitive.get("y1"), "y1")
            x2 = number(primitive.get("x2"), "x2")
            y2 = number(primitive.get("y2"), "y2")
            for x, y, name in [(x1, y1, "start"), (x2, y2, "end")]:
                validate_coord(x, y, width, height, f"primitive[{index}].{name}", issues)
            record_geometry(
                primitive_type,
                semantic_id,
                (min(x1, x2) - line_width, min(y1, y2) - line_width, max(x1, x2) + line_width, max(y1, y2) + line_width),
                points=[(x1, y1), (x2, y2)],
            )
            if primitive_type in {"arrow", "double_arrow"}:
                draw_arrow(
                    x1,
                    y1,
                    x2,
                    y2,
                    stroke,
                    line_width,
                    str(primitive.get("label") or ""),
                    double=primitive_type == "double_arrow",
                    semantic_id=semantic_id,
                )
            else:
                dash = dash_pattern(primitive.get("dash"), f"primitives[{index}].dash")
                if dash:
                    draw_dashed_segment((x1, y1), (x2, y2), stroke, line_width, dash)
                else:
                    draw.line((x1, y1, x2, y2), fill=stroke, width=line_width)
                dash_attr = f' stroke-dasharray="{" ".join(f"{item:g}" for item in dash)}"' if dash else ""
                svg_parts.append(f'<line x1="{x1:g}" y1="{y1:g}" x2="{x2:g}" y2="{y2:g}" stroke="{stroke}" stroke-width="{line_width}"{dash_attr} />')
        elif primitive_type == "text":
            x = number(primitive.get("x"), "x")
            y = number(primitive.get("y"), "y")
            validate_coord(x, y, width, height, f"primitive[{index}].text", issues)
            size = int(number(primitive.get("size", 24), "size"))
            if not 10 <= size <= 120:
                raise ValueError("text size must be 10-120")
            draw_text(
                x,
                y,
                str(primitive.get("text") or ""),
                size,
                stroke,
                semantic_id=semantic_id,
                allow_overlap=bool(primitive.get("allow_overlap", False)),
            )
        elif primitive_type == "circle":
            cx, cy, radius = number(primitive.get("cx"), "cx"), number(primitive.get("cy"), "cy"), number(primitive.get("r"), "r")
            if radius <= 0:
                raise ValueError("circle radius must be positive")
            validate_coord(cx - radius, cy - radius, width, height, f"primitive[{index}].circle-min", issues)
            validate_coord(cx + radius, cy + radius, width, height, f"primitive[{index}].circle-max", issues)
            box = (cx - radius, cy - radius, cx + radius, cy + radius)
            fill = color(primitive.get("fill"), background_color) if primitive.get("fill") else None
            draw.ellipse(box, outline=stroke, fill=fill, width=line_width)
            record_geometry("circle", semantic_id, box)
            svg_parts.append(f'<circle cx="{cx:g}" cy="{cy:g}" r="{radius:g}" fill="{fill or "none"}" stroke="{stroke}" stroke-width="{line_width}" />')
        elif primitive_type == "rect":
            x, y = number(primitive.get("x"), "x"), number(primitive.get("y"), "y")
            rect_width, rect_height = number(primitive.get("width"), "width"), number(primitive.get("height"), "height")
            validate_coord(x, y, width, height, f"primitive[{index}].rect-min", issues)
            validate_coord(x + rect_width, y + rect_height, width, height, f"primitive[{index}].rect-max", issues)
            fill = color(primitive.get("fill"), background_color) if primitive.get("fill") else None
            draw.rectangle((x, y, x + rect_width, y + rect_height), outline=stroke, fill=fill, width=line_width)
            record_geometry("rect", semantic_id, (x, y, x + rect_width, y + rect_height))
            svg_fill = fill or "none"
            svg_parts.append(f'<rect x="{x:g}" y="{y:g}" width="{rect_width:g}" height="{rect_height:g}" fill="{svg_fill}" stroke="{stroke}" stroke-width="{line_width}" />')
        elif primitive_type == "polygon":
            raw_points = primitive.get("points")
            if not isinstance(raw_points, list) or len(raw_points) < 3:
                raise ValueError("polygon requires at least three points")
            points = [point(item, f"points[{item_index}]") for item_index, item in enumerate(raw_points)]
            for item_index, (x, y) in enumerate(points):
                validate_coord(x, y, width, height, f"primitive[{index}].points[{item_index}]", issues)
            fill = color(primitive.get("fill"), background_color) if primitive.get("fill") else None
            draw.polygon(points, outline=stroke, fill=fill)
            if line_width > 1:
                draw.line([*points, points[0]], fill=stroke, width=line_width, joint="curve")
            xs, ys = [value[0] for value in points], [value[1] for value in points]
            record_geometry("polygon", semantic_id, (min(xs), min(ys), max(xs), max(ys)))
            point_text = " ".join(f"{x:g},{y:g}" for x, y in points)
            svg_parts.append(f'<polygon points="{point_text}" fill="{fill or "none"}" stroke="{stroke}" stroke-width="{line_width}" />')
        elif primitive_type == "polyline":
            raw_points = primitive.get("points")
            if not isinstance(raw_points, list) or len(raw_points) < 2:
                raise ValueError("polyline requires at least two points")
            points = [point(item, f"points[{item_index}]") for item_index, item in enumerate(raw_points)]
            for item_index, (x, y) in enumerate(points):
                validate_coord(x, y, width, height, f"primitive[{index}].points[{item_index}]", issues)
            dash = dash_pattern(primitive.get("dash"), f"primitives[{index}].dash")
            if dash:
                for start, end in zip(points, points[1:]):
                    draw_dashed_segment(start, end, stroke, line_width, dash)
            else:
                draw.line(points, fill=stroke, width=line_width, joint="curve")
            xs, ys = [value[0] for value in points], [value[1] for value in points]
            record_geometry("polyline", semantic_id, (min(xs) - line_width, min(ys) - line_width, max(xs) + line_width, max(ys) + line_width), points=points)
            point_text = " ".join(f"{x:g},{y:g}" for x, y in points)
            dash_attr = f' stroke-dasharray="{" ".join(f"{item:g}" for item in dash)}"' if dash else ""
            svg_parts.append(f'<polyline points="{point_text}" fill="none" stroke="{stroke}" stroke-width="{line_width}"{dash_attr} />')
        elif primitive_type == "axis":
            origin = point(primitive.get("origin"), "origin")
            x_end = point(primitive.get("x_end"), "x_end")
            y_end = point(primitive.get("y_end"), "y_end")
            for value, name in [(origin, "origin"), (x_end, "x_end"), (y_end, "y_end")]:
                validate_coord(value[0], value[1], width, height, f"primitive[{index}].{name}", issues)
            draw_arrow(origin[0], origin[1], x_end[0], x_end[1], stroke, line_width, str(primitive.get("x_label") or "x"))
            draw_arrow(origin[0], origin[1], y_end[0], y_end[1], stroke, line_width, str(primitive.get("y_label") or "y"))

    if bool(spec.get("collision_check")):
        def intersects(a: list[float], b: list[float]) -> bool:
            return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]

        def segment_intersects_rect(start: list[float], end: list[float], rect: list[float]) -> bool:
            if rect[0] <= start[0] <= rect[2] and rect[1] <= start[1] <= rect[3]:
                return True
            if rect[0] <= end[0] <= rect[2] and rect[1] <= end[1] <= rect[3]:
                return True

            def orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
                return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

            def crosses(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> bool:
                return orientation(a, b, c) * orientation(a, b, d) <= 0 and orientation(c, d, a) * orientation(c, d, b) <= 0

            a, b = (start[0], start[1]), (end[0], end[1])
            corners = [(rect[0], rect[1]), (rect[2], rect[1]), (rect[2], rect[3]), (rect[0], rect[3])]
            return any(crosses(a, b, corners[index], corners[(index + 1) % 4]) for index in range(4))

        def geometry_hits_text(record: dict[str, Any], text_box: list[float]) -> bool:
            if not intersects(text_box, record["bbox"]):
                return False
            points = record.get("points")
            if isinstance(points, list) and len(points) >= 2:
                return any(segment_intersects_rect(start, end, text_box) for start, end in zip(points, points[1:]))
            return True

        for text_record in text_records:
            if text_record["allow_overlap"]:
                continue
            collisions = [
                record["semantic_id"] or record["type"]
                for record in geometry_records
                if geometry_hits_text(record, text_record["bbox"])
            ]
            if collisions:
                issues.append(f"text collision: {text_record['text']} overlaps {', '.join(sorted(set(collisions)))}")

    if kind in {"free_body", "vector"} and arrow_count == 0:
        issues.append(f"{kind} diagram requires at least one arrow")
    overlay_diff = ImageChops.difference(image, base_image)
    nonblank_overlay = overlay_diff.getbbox() is not None
    if not nonblank_overlay:
        issues.append("rendered overlay is blank")

    source_unchanged = True
    if source_path and source_hash_before:
        source_unchanged = sha256_file(source_path) == source_hash_before
        if not source_unchanged:
            issues.append("source image hash changed")

    svg_parts.append("</svg>")
    svg_path = out_dir / "diagram.svg"
    png_path = out_dir / "diagram.png"
    if not issues:
        svg_path.write_text("\n".join(svg_parts) + "\n", encoding="utf-8")
        image.save(png_path, format="PNG", optimize=True)

    report = {
        "ok": not issues,
        "kind": kind,
        "revision": revision,
        "canvas": {"width": width, "height": height},
        "font": str(font_path),
        "svg": str(svg_path) if not issues else None,
        "png": str(png_path) if not issues else None,
        "sanitized_background": str(sanitized_background) if sanitized_background else None,
        "source_unchanged": source_unchanged,
        "validation": {
            "primitive_count": len(primitives),
            "arrow_count": arrow_count,
            "text_boxes": text_boxes,
            "text_records": text_records,
            "geometry_records": geometry_records,
            "nonblank_overlay": nonblank_overlay,
            "issues": issues,
        },
    }
    report_path = out_dir / "render_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report"] = str(report_path)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a validated physics diagram spec.")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        spec_path = Path(args.spec).resolve()
        if not spec_path.is_file():
            raise FileNotFoundError(spec_path)
        out_dir = Path(args.out_dir).resolve()
        if not args.test_mode:
            ensure_inside(out_dir, SESSION_ROOT)
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        if not isinstance(spec, dict):
            raise ValueError("diagram spec must be a JSON object")
        report = render(spec, spec_path, out_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else str(report["report"]))
        return 0 if report["ok"] else 1
    except Exception as exc:  # noqa: BLE001
        error = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
