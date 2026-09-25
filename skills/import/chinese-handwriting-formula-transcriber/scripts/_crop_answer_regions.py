import argparse
import json
from pathlib import Path

from PIL import Image


BASE_WIDTH = 1858
BASE_HEIGHT = 1312

REGIONS = [
    {"id": "q09", "label": "9", "bbox": [73, 489, 630, 558]},
    {"id": "q10", "label": "10", "bbox": [73, 552, 630, 635]},
    {"id": "q11", "label": "11", "bbox": [73, 625, 630, 714]},
    {"id": "q12", "label": "12", "bbox": [73, 708, 630, 798]},
    {"id": "q13", "label": "13", "bbox": [73, 792, 630, 922]},
    {"id": "q14", "label": "14", "bbox": [653, 70, 1215, 604]},
    {"id": "q15", "label": "15", "bbox": [653, 603, 1215, 1204]},
    {"id": "q16", "label": "16", "bbox": [1235, 70, 1802, 1205]},
]


def scale_bbox(bbox, width, height):
    sx = width / BASE_WIDTH
    sy = height / BASE_HEIGHT
    left, top, right, bottom = bbox
    return [
        max(0, round(left * sx)),
        max(0, round(top * sy)),
        min(width, round(right * sx)),
        min(height, round(bottom * sy)),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-image", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    source = Path(args.source_image)
    out_dir = Path(args.output_dir)
    crops_dir = out_dir / "regions"
    crops_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "source_image": str(source),
        "template": "xiamen_no6_physics_3page_2024",
        "base_size": {"width": BASE_WIDTH, "height": BASE_HEIGHT},
        "regions": [],
    }

    with Image.open(source) as img:
        img = img.convert("RGB")
        manifest["image"] = {"width": img.width, "height": img.height, "mode": img.mode}
        for region in REGIONS:
            bbox = scale_bbox(region["bbox"], img.width, img.height)
            crop = img.crop(tuple(bbox))
            crop_path = crops_dir / f"{region['id']}.png"
            crop.save(crop_path)
            manifest["regions"].append(
                {
                    "id": region["id"],
                    "label": region["label"],
                    "bbox": bbox,
                    "path": str(crop_path),
                    "width": crop.width,
                    "height": crop.height,
                }
            )

    manifest_path = crops_dir / "regions_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "manifest": str(manifest_path), "regions": len(manifest["regions"])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
