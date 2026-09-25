import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from _crop_answer_regions import REGIONS, scale_bbox


LONG_ANSWER_IDS = {"q14", "q15", "q16"}


def long_answer_subareas(width, height):
    header_height = min(52, max(34, int(height * 0.065)))
    score_width = min(230, max(150, int(width * 0.34)))
    score_height = min(60, max(36, int(height * 0.08)))
    return {
        "question_header": [0, 0, width, header_height],
        "score_area": [max(0, width - score_width), 0, width, score_height],
        "student_answer_area": [0, header_height, width, height],
    }


def red_mask_rgb(rgb):
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    lower1 = np.array([0, 45, 80], dtype=np.uint8)
    upper1 = np.array([15, 255, 255], dtype=np.uint8)
    lower2 = np.array([165, 45, 80], dtype=np.uint8)
    upper2 = np.array([180, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower1, upper1) | cv2.inRange(hsv, lower2, upper2)
    kernel = np.ones((3, 3), np.uint8)
    return cv2.dilate(mask, kernel, iterations=1)


def remove_red(rgb, mask):
    clean = rgb.copy()
    clean[mask > 0] = [255, 255, 255]
    return clean


def red_only(rgb, mask):
    red = np.full_like(rgb, 255)
    red[mask > 0] = rgb[mask > 0]
    return red


def ink_mask(rgb):
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    _, mask = cv2.threshold(gray, 205, 255, cv2.THRESH_BINARY_INV)
    # 保持手写笔画连续，同时避免连接距离较远的文本行。
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)


def find_line_boxes(rgb):
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    _, mask = cv2.threshold(gray, 210, 255, cv2.THRESH_BINARY_INV)
    height, width = mask.shape
    # 忽略裁剪边框附近的页面边界和印刷方框边缘。
    margin_x = max(3, round(width * 0.015))
    margin_y = max(3, round(height * 0.015))
    work = mask.copy()
    work[:margin_y, :] = 0
    work[-margin_y:, :] = 0
    work[:, :margin_x] = 0
    work[:, -margin_x:] = 0

    row_counts = (work > 0).sum(axis=1)
    row_threshold = max(3, int(width * 0.006))
    active = row_counts > row_threshold
    boxes = []
    start = None
    gap = 0
    max_gap = max(5, int(height * 0.008))
    for idx, value in enumerate(active):
        if value:
            if start is None:
                start = idx
            gap = 0
        elif start is not None:
            gap += 1
            if gap > max_gap:
                end = idx - gap
                append_line_box(work, row_counts, boxes, start, end, width, height)
                start = None
                gap = 0
    if start is not None:
        append_line_box(work, row_counts, boxes, start, height - 1, width, height)
    return merge_small_line_boxes(boxes, width, height)


def split_tall_band(row_counts, top, bottom, height):
    max_height = max(80, int(height * 0.12))
    if bottom - top + 1 <= max_height:
        return [(top, bottom)]
    segments = []
    cursor = top
    while cursor <= bottom:
        target = min(bottom, cursor + max_height)
        search_start = min(bottom, cursor + max(24, int(max_height * 0.45)))
        if search_start < target:
            local = row_counts[search_start : target + 1]
            if local.size:
                valley = int(local.argmin()) + search_start
                if row_counts[valley] <= max(3, row_counts[top : bottom + 1].mean() * 0.35):
                    target = valley
        if target - cursor >= 12:
            segments.append((cursor, target))
        cursor = target + 1
    return segments


def append_line_box(mask, row_counts, boxes, top, bottom, width, height):
    if bottom <= top:
        return
    segments = split_tall_band(row_counts, top, bottom, height)
    for seg_top, seg_bottom in segments:
        append_single_line_box(mask, boxes, seg_top, seg_bottom, width, height)


def append_single_line_box(mask, boxes, top, bottom, width, height):
    band = mask[top : bottom + 1, :]
    cols = np.where((band > 0).any(axis=0))[0]
    if cols.size == 0:
        return
    left = int(cols.min())
    right = int(cols.max()) + 1
    pad_x = max(8, int(width * 0.018))
    pad_y = max(6, int(height * 0.012))
    box = [
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(width, right + pad_x),
        min(height, bottom + 1 + pad_y),
    ]
    area = (box[2] - box[0]) * (box[3] - box[1])
    if box[3] - box[1] >= 10 and area >= 260:
        boxes.append(box)


def merge_small_line_boxes(boxes, width, height):
    if not boxes:
        return []
    boxes = sorted(boxes, key=lambda item: item[1])
    merged = []
    for box in boxes:
        if not merged:
            merged.append(box)
            continue
        prev = merged[-1]
        gap = box[1] - prev[3]
        very_short = (prev[3] - prev[1] < 18) or (box[3] - box[1] < 18)
        if gap <= max(4, int(height * 0.01)) and very_short:
            merged[-1] = [
                min(prev[0], box[0]),
                min(prev[1], box[1]),
                max(prev[2], box[2]),
                max(prev[3], box[3]),
            ]
        else:
            merged.append(box)
    return [
        box
        for box in merged
        if (box[2] - box[0]) >= max(24, int(width * 0.04))
    ]


def save_rgb(path, rgb):
    Image.fromarray(rgb).save(path)


def crop_rgb(rgb, bbox):
    return rgb[bbox[1] : bbox[3], bbox[0] : bbox[2]].copy()


def image_stats(rgb):
    mask = ink_mask(rgb)
    return {
        "width": int(rgb.shape[1]),
        "height": int(rgb.shape[0]),
        "ink_ratio": float((mask > 0).mean()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-image", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    source = Path(args.source_image)
    out_dir = Path(args.output_dir)
    crops_dir = out_dir / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(source) as pil:
        pil = pil.convert("RGB")
        full = np.array(pil)

    manifest = {
        "source_image": str(source),
        "template": "xiamen_no6_physics_3page_2024_auto_v1",
        "image": {"width": int(full.shape[1]), "height": int(full.shape[0]), "mode": "RGB"},
        "regions": [],
    }

    for region in REGIONS:
        region_id = region["id"]
        region_dir = crops_dir / region_id
        lines_dir = region_dir / "lines"
        lines_dir.mkdir(parents=True, exist_ok=True)
        bbox = scale_bbox(region["bbox"], full.shape[1], full.shape[0])
        crop = full[bbox[1] : bbox[3], bbox[0] : bbox[2]].copy()
        mask = red_mask_rgb(crop)
        clean = remove_red(crop, mask)
        red = red_only(crop, mask)

        original_path = region_dir / "original.png"
        clean_path = region_dir / "clean_without_red.png"
        red_path = region_dir / "red_marks.png"
        save_rgb(original_path, crop)
        save_rgb(clean_path, clean)
        save_rgb(red_path, red)
        ocr_source = clean
        ocr_source_origin = [0, 0]

        entry = {
            "id": region_id,
            "label": region["label"],
            "bbox": bbox,
            "region_type": "long_answer" if region_id in LONG_ANSWER_IDS else "short_answer",
            "original_crop": str(original_path),
            "clean_crop": str(clean_path),
            "red_marks_crop": str(red_path),
            "stats": image_stats(clean),
            "subareas": {},
            "lines": [],
        }

        if region_id in LONG_ANSWER_IDS:
            subareas = long_answer_subareas(clean.shape[1], clean.shape[0])
            for name, sub_bbox in subareas.items():
                sub_path = region_dir / f"{name}.png"
                save_rgb(sub_path, crop_rgb(clean, sub_bbox))
                entry["subareas"][name] = {
                    "bbox": sub_bbox,
                    "path": str(sub_path),
                }
            answer_bbox = subareas["student_answer_area"]
            ocr_source = crop_rgb(clean, answer_bbox)
            ocr_source_origin = [answer_bbox[0], answer_bbox[1]]
            line_boxes = find_line_boxes(ocr_source)
        else:
            line_boxes = [[0, 0, ocr_source.shape[1], ocr_source.shape[0]]]

        for index, line_box in enumerate(line_boxes, start=1):
            line = ocr_source[line_box[1] : line_box[3], line_box[0] : line_box[2]].copy()
            line_path = lines_dir / f"{region_id}_l{index:03d}.png"
            save_rgb(line_path, line)
            region_bbox = [
                line_box[0] + ocr_source_origin[0],
                line_box[1] + ocr_source_origin[1],
                line_box[2] + ocr_source_origin[0],
                line_box[3] + ocr_source_origin[1],
            ]
            entry["lines"].append(
                {
                    "line_id": f"{region_id}_l{index:03d}",
                    "bbox": line_box,
                    "region_bbox": region_bbox,
                    "path": str(line_path),
                    "stats": image_stats(line),
                }
            )
        manifest["regions"].append(entry)

    manifest_path = out_dir / "auto_inputs_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "manifest": str(manifest_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
