#!/usr/bin/env python
"""Create a hash-bound coordinate proposal and temporary grid for image annotation."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any

from diagram_common import atomic_write_json, ensure_inside, sha256_file, sha256_json


def orientation_matrix(orientation: int, width: int, height: int) -> list[list[float]]:
    matrices = {
        1: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        2: [[-1, 0, width - 1], [0, 1, 0], [0, 0, 1]],
        3: [[-1, 0, width - 1], [0, -1, height - 1], [0, 0, 1]],
        4: [[1, 0, 0], [0, -1, height - 1], [0, 0, 1]],
        5: [[0, 1, 0], [1, 0, 0], [0, 0, 1]],
        6: [[0, -1, height - 1], [1, 0, 0], [0, 0, 1]],
        7: [[0, -1, height - 1], [-1, 0, width - 1], [0, 0, 1]],
        8: [[0, 1, 0], [-1, 0, width - 1], [0, 0, 1]],
    }
    return [[float(value) for value in row] for row in matrices.get(orientation, matrices[1])]


def invert_matrix(matrix: list[list[float]]) -> list[list[float]]:
    import numpy as np

    return np.linalg.inv(np.asarray(matrix, dtype=np.float64)).tolist()


def find_font(size: int):
    from PIL import ImageFont

    runtime = Path(os.environ.get("LLMWIKI_MODEL_RUNTIME_ROOT", ""))
    candidates = [
        runtime / "fonts" / "noto-sans-cjk-sc" / "2.004" / "NotoSansCJKsc-Regular.otf",
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size), str(candidate)
    raise RuntimeError("no local Chinese-capable font found")


def dedupe_points(points: list[tuple[float, float, str]], width: int, height: int) -> list[dict[str, Any]]:
    kept: list[tuple[float, float, str]] = []
    minimum = max(3.0, min(width, height) * 0.01)
    for x, y, kind in points:
        if not (0 <= x < width and 0 <= y < height):
            continue
        if any(math.hypot(x - px, y - py) < minimum for px, py, _ in kept):
            continue
        kept.append((x, y, kind))
        if len(kept) >= 80:
            break
    return [
        {
            "id": f"auto_{index:03d}",
            "kind": kind,
            "pixel": [round(x, 3), round(y, 3)],
            "normalized": [round(x / max(1, width - 1), 8), round(y / max(1, height - 1), 8)],
            "semantic": None,
        }
        for index, (x, y, kind) in enumerate(kept, start=1)
    ]


def line_intersection(a: list[int], b: list[int]) -> tuple[float, float] | None:
    x1, y1, x2, y2 = (float(value) for value in a)
    x3, y3, x4, y4 = (float(value) for value in b)
    denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denominator) < 1e-6:
        return None
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / denominator
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / denominator
    margin = 0.08 * max(math.hypot(x2 - x1, y2 - y1), math.hypot(x4 - x3, y4 - y3))
    if (
        min(x1, x2) - margin <= px <= max(x1, x2) + margin
        and min(y1, y2) - margin <= py <= max(y1, y2) + margin
        and min(x3, x4) - margin <= px <= max(x3, x4) + margin
        and min(y3, y4) - margin <= py <= max(y3, y4) + margin
    ):
        return px, py
    return None


def detect_geometry(image_path: Path) -> dict[str, Any]:
    import cv2
    import numpy as np

    data = np.fromfile(str(image_path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise RuntimeError(f"OpenCV cannot decode sanitized image: {image_path}")
    height, width = image.shape
    blur = cv2.GaussianBlur(image, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)
    nonzero = cv2.findNonZero(edges)
    if nonzero is None:
        content_box = [0, 0, width, height]
    else:
        x, y, w, h = cv2.boundingRect(nonzero)
        content_box = [int(x), int(y), int(w), int(h)]

    minimum_length = max(20, int(min(width, height) * 0.08))
    raw_lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180,
        threshold=max(20, minimum_length // 2),
        minLineLength=minimum_length,
        maxLineGap=max(5, minimum_length // 5),
    )
    lines: list[list[int]] = []
    if raw_lines is not None:
        candidates = [list(map(int, row[0])) for row in raw_lines]
        candidates.sort(key=lambda row: math.hypot(row[2] - row[0], row[3] - row[1]), reverse=True)
        lines = candidates[:24]

    scale = min(1.0, 1000.0 / max(width, height))
    circle_image = cv2.resize(blur, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else blur
    min_radius = max(5, int(min(circle_image.shape) * 0.02))
    raw_circles = cv2.HoughCircles(
        circle_image,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=max(12, min_radius * 2),
        param1=100,
        param2=30,
        minRadius=min_radius,
        maxRadius=max(min_radius + 1, int(min(circle_image.shape) * 0.35)),
    )
    circles: list[list[float]] = []
    if raw_circles is not None:
        for cx, cy, radius in raw_circles[0][:12]:
            circles.append([round(float(cx / scale), 3), round(float(cy / scale), 3), round(float(radius / scale), 3)])

    x, y, w, h = content_box
    points: list[tuple[float, float, str]] = [
        (x + w / 2, y + h / 2, "content_center"),
        (x, y, "content_corner"),
        (x + w - 1, y, "content_corner"),
        (x, y + h - 1, "content_corner"),
        (x + w - 1, y + h - 1, "content_corner"),
    ]
    for x1, y1, x2, y2 in lines:
        points.extend([(x1, y1, "line_endpoint"), (x2, y2, "line_endpoint")])
    for index, first in enumerate(lines):
        for second in lines[index + 1 :]:
            intersection = line_intersection(first, second)
            if intersection:
                points.append((intersection[0], intersection[1], "line_intersection"))
    points.extend((circle[0], circle[1], "circle_center") for circle in circles)
    return {
        "content_box": content_box,
        "lines": lines,
        "circles": circles,
        "candidate_anchors": dedupe_points(points, width, height),
    }


def draw_grid(source: Path, destination: Path, max_side: int = 1600) -> dict[str, Any]:
    from PIL import Image, ImageDraw

    with Image.open(source) as opened:
        image = opened.convert("RGB")
    source_width, source_height = image.size
    scale = min(1.0, max_side / max(source_width, source_height))
    preview_width = max(1, round(source_width * scale))
    preview_height = max(1, round(source_height * scale))
    if scale != 1.0:
        image = image.resize((preview_width, preview_height), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(image, "RGBA")
    minor_color = (20, 110, 190, 55)
    major_color = (210, 35, 35, 120)
    for percent in range(0, 101, 2):
        x = round((preview_width - 1) * percent / 100)
        y = round((preview_height - 1) * percent / 100)
        is_major = percent % 10 == 0
        draw.line((x, 0, x, preview_height - 1), fill=major_color if is_major else minor_color, width=2 if is_major else 1)
        draw.line((0, y, preview_width - 1, y), fill=major_color if is_major else minor_color, width=2 if is_major else 1)
    font, font_path = find_font(max(12, round(min(preview_width, preview_height) * 0.018)))
    for percent in range(0, 101, 10):
        x = round((preview_width - 1) * percent / 100)
        y = round((preview_height - 1) * percent / 100)
        source_x = round((source_width - 1) * percent / 100)
        source_y = round((source_height - 1) * percent / 100)
        draw.text((min(x + 3, preview_width - 90), 3), f"x={source_x}", font=font, fill=(180, 0, 0, 230))
        draw.text((3, min(y + 3, preview_height - 24)), f"y={source_y}", font=font, fill=(180, 0, 0, 230))
    image.save(destination, format="PNG", optimize=True)
    matrix = [[scale, 0.0, 0.0], [0.0, scale, 0.0], [0.0, 0.0, 1.0]]
    return {
        "size": [preview_width, preview_height],
        "source_to_preview": matrix,
        "preview_to_source": invert_matrix(matrix),
        "font": font_path,
    }


def calibrate(source: Path, out_dir: Path) -> dict[str, Any]:
    from PIL import Image, ImageOps

    out_dir.mkdir(parents=True, exist_ok=True)
    source_hash = sha256_file(source)
    with Image.open(source) as opened:
        opened.load()
        original_size = list(opened.size)
        orientation = int(opened.getexif().get(274, 1) or 1)
        normalized = ImageOps.exif_transpose(opened).convert("RGBA")
        background = Image.new("RGBA", normalized.size, "white")
        sanitized = Image.alpha_composite(background, normalized).convert("RGB")
    sanitized_path = out_dir / "background_sanitized.png"
    sanitized.save(sanitized_path, format="PNG", optimize=True)
    if sha256_file(source) != source_hash:
        raise RuntimeError("source image changed while calibrating")
    grid_path = out_dir / "coordinate_grid.png"
    preview = draw_grid(sanitized_path, grid_path)
    geometry = detect_geometry(sanitized_path)
    width, height = sanitized.size
    source_to_sanitized = orientation_matrix(orientation, original_size[0], original_size[1])
    sanitized_to_normalized = [
        [1.0 / max(1, width - 1), 0.0, 0.0],
        [0.0, 1.0 / max(1, height - 1), 0.0],
        [0.0, 0.0, 1.0],
    ]
    proposal = {
        "schema_version": 1,
        "status": "needs_calibration_review",
        "source": {
            "path": str(source),
            "sha256": source_hash,
            "original_size": original_size,
            "exif_orientation": orientation,
        },
        "sanitized": {"path": str(sanitized_path), "sha256": sha256_file(sanitized_path), "size": [width, height]},
        "coordinate_system": {
            "pixel_origin": "top_left",
            "pixel_bounds": [0, 0, width - 1, height - 1],
            "normalized_bounds": [0.0, 0.0, 1.0, 1.0],
            "source_to_sanitized": source_to_sanitized,
            "sanitized_to_source": invert_matrix(source_to_sanitized),
            "sanitized_to_normalized": sanitized_to_normalized,
            "normalized_to_sanitized": invert_matrix(sanitized_to_normalized),
        },
        "preview": {"path": str(grid_path), "sha256": sha256_file(grid_path), **preview},
        "geometry_proposals": geometry,
        "semantic_assignment": "unassigned",
    }
    proposal["proposal_sha256"] = sha256_json({key: value for key, value in proposal.items() if key != "proposal_sha256"})
    proposal_path = out_dir / "coordinate_map.json"
    atomic_write_json(proposal_path, proposal)
    review_template = {
        "schema_version": 1,
        "status": "needs_review",
        "proposal_sha256": proposal["proposal_sha256"],
        "source_sha256": source_hash,
        "reviewer_type": None,
        "anchors": [],
        "uncertainties": [],
    }
    atomic_write_json(out_dir / "calibration_review.template.json", review_template)
    return {"ok": True, "proposal": str(proposal_path), "proposal_sha256": proposal["proposal_sha256"], **proposal}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        source = Path(args.source).expanduser().resolve()
        out_dir = Path(args.out_dir).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        ensure_inside(out_dir, out_dir.parent)
        result = calibrate(source, out_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result["proposal"])
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
