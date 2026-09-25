#!/usr/bin/env python3
"""Audit exercise-bank image references and image readability.

The exercise import workflow already checks broken links, but linked images can
still be semantically wrong or unreadable. This script focuses on mechanical
gates that are cheap to run:

- body image references only, not frontmatter `assets`
- missing/data URI/WMF/open-error checks
- `assets` metadata versus actual body `media/...` reference mismatch
- small/flat-image suspects for manual semantic review
- transparent PNGs that render as black in naive viewers but are readable after
  compositing on white
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote


IMAGE_RE = re.compile(r"!\[[^\]]*]\(([^)\n]+)\)")
FM_SPLIT_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
PHYSICS_IMAGE_CONTEXT_RE = re.compile(r"如图|图中|图像|曲线|装置|轨迹|电路|坐标|示意|波形|受力|磁场|电场")
EXPLICIT_FIGURE_REFERENCE_RE = re.compile(
    r"如(?:下)?图(?:所示)?|图示(?:中|所示)?|图中|下图(?:中|所示)?|"
    r"图[甲乙丙丁一二三四五六七八九十0-9]+(?:中|所示)?|见图[甲乙丙丁一二三四五六七八九十0-9]*"
)
REMOTE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")

warnings.filterwarnings(
    "ignore",
    message="Palette images with Transparency expressed in bytes should be converted to RGBA images",
    category=UserWarning,
)


@dataclass(frozen=True)
class MarkdownParts:
    frontmatter: str
    body: str


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def split_frontmatter(text: str) -> MarkdownParts:
    match = FM_SPLIT_RE.match(text)
    if not match:
        return MarkdownParts("", text)
    return MarkdownParts(match.group(1), text[match.end() :])


def parse_assets(frontmatter: str) -> list[str]:
    """Parse a small YAML subset for the `assets:` list.

    The skill does not depend on PyYAML being installed. This parser only needs
    to handle the current Question Markdown Standard shape:

    assets:
      - "media/image.png"
    """

    assets: list[str] = []
    lines = frontmatter.splitlines()
    in_assets = False
    for line in lines:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*:\s*", line) and not line.startswith((" ", "\t")):
            in_assets = line.split(":", 1)[0] == "assets"
            value = line.split(":", 1)[1].strip()
            if in_assets and value and value not in {"[]", "null", "None"}:
                for item in re.findall(r"['\"]([^'\"]+)['\"]", value):
                    assets.append(item.replace("\\", "/"))
            continue
        if not in_assets:
            continue
        item_match = re.match(r"^\s*-\s*(.+?)\s*$", line)
        if item_match:
            value = item_match.group(1).strip().strip("'\"")
            if value:
                assets.append(value.replace("\\", "/"))
    return sorted(dict.fromkeys(assets))


def extract_h2_section(body: str, heading: str) -> str:
    match = re.search(
        rf"(?ms)^##\s+{re.escape(heading)}\s*\r?\n(.*?)(?=^##\s+|\Z)",
        body,
    )
    return match.group(1).strip() if match else ""


def semantic_figure_issues(text: str, file_label: str) -> list[dict[str, Any]]:
    """Return hard issues for explicit figure references with zero image assets.

    This intentionally uses a narrow deictic pattern. Generic phrases such as
    ``图像法`` and ``图像特征`` do not imply that a source figure is missing.
    """

    parts = split_frontmatter(text)
    stem = extract_h2_section(parts.body, "题目")
    match = EXPLICIT_FIGURE_REFERENCE_RE.search(stem)
    if not match:
        return []
    assets = parse_assets(parts.frontmatter)
    body_refs = [normalize_ref(item.group(1)) for item in IMAGE_RE.finditer(parts.body)]
    if assets or body_refs:
        return []
    return [
        {
            "file": file_label,
            "code": "stem_requires_figure_but_assets_empty",
            "signal": match.group(0),
            "context": context_around(stem, match.start(), match.end()),
        }
    ]


def collect_markdown(paths: list[str], kb_root: Path) -> list[Path]:
    if not paths:
        paths = ["raw/exercises"]
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_absolute():
            path = kb_root / path
        path = path.resolve()
        if path.is_dir():
            files.extend(sorted(path.rglob("*.md")))
        elif path.is_file() and path.suffix.lower() == ".md":
            files.append(path)
        else:
            raise FileNotFoundError(f"Exercise path not found or not markdown: {path}")
    return sorted(dict.fromkeys(files))


def relpath(path: Path, kb_root: Path) -> str:
    try:
        return path.resolve().relative_to(kb_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def normalize_ref(target: str) -> str:
    target = target.strip().strip("<>").split("#", 1)[0].split("?", 1)[0]
    return unquote(target).replace("\\", "/")


def ref_to_path(md_path: Path, target: str) -> Path | None:
    normalized = normalize_ref(target)
    if not normalized or normalized.startswith("data:") or REMOTE_RE.match(normalized):
        return None
    path = Path(normalized)
    if not path.is_absolute():
        path = md_path.parent / path
    return path.resolve()


def context_around(body: str, start: int, end: int, window: int = 120) -> str:
    left = max(0, start - window)
    right = min(len(body), end + window)
    return re.sub(r"\s+", " ", body[left:right]).strip()


def mean_luma(image: Any) -> float:
    stat = image.convert("L")
    hist = stat.histogram()
    total = sum(hist)
    if total == 0:
        return 0.0
    return sum(i * count for i, count in enumerate(hist)) / total


def image_metrics(path: Path) -> dict[str, Any]:
    from PIL import Image

    with Image.open(path) as im:
        im.load()
        mode = im.mode
        width, height = im.size
        direct_mean = mean_luma(im)
        has_alpha = mode in {"RGBA", "LA"} or "transparency" in im.info
        white_mean: float | None = None
        transparent_frac: float | None = None
        if has_alpha:
            rgba = im.convert("RGBA")
            alpha = rgba.getchannel("A")
            hist = alpha.histogram()
            total = sum(hist)
            transparent = sum(hist[:250])
            transparent_frac = transparent / total if total else 0.0
            white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            white.alpha_composite(rgba)
            white_mean = mean_luma(white)
        return {
            "width": width,
            "height": height,
            "mode": mode,
            "bytes": path.stat().st_size,
            "direct_mean": round(direct_mean, 2),
            "has_alpha": has_alpha,
            "white_mean": round(white_mean, 2) if white_mean is not None else None,
            "alpha_transparent_frac": round(transparent_frac, 4) if transparent_frac is not None else None,
        }


def alpha_black_risk(metrics: dict[str, Any]) -> bool:
    return bool(
        metrics.get("has_alpha")
        and metrics.get("direct_mean", 255) < 45
        and (metrics.get("white_mean") or 0) > 160
        and (metrics.get("alpha_transparent_frac") or 0) > 0.15
    )


def dark_risk(metrics: dict[str, Any]) -> bool:
    if metrics.get("has_alpha"):
        return False
    width = int(metrics.get("width") or 0)
    height = int(metrics.get("height") or 0)
    # Very small icons can be dark without being unreadable; those are handled
    # by the semantic-suspect review instead of the dark-image gate.
    if width * height < 20_000:
        return False
    return bool(metrics.get("direct_mean", 255) < 45)


def suspect_codes(metrics: dict[str, Any], context: str) -> list[str]:
    width = int(metrics.get("width") or 0)
    height = int(metrics.get("height") or 0)
    byte_count = int(metrics.get("bytes") or 0)
    ratio = width / height if height else math.inf
    codes: list[str] = []
    if byte_count <= 2_500 and ((width <= 180 and height <= 270) or (height <= 60 and width <= 250)):
        codes.append("small_file_image")
    if height <= 45 and width >= 180:
        codes.append("wide_short_text_line")
    if ratio >= 8 or ratio <= 0.14:
        codes.append("extreme_aspect_ratio")
    if codes and PHYSICS_IMAGE_CONTEXT_RE.search(context):
        codes.append("physics_context")
    return codes


def audit_file(md_path: Path, kb_root: Path, fix_alpha_black: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    text = read_text(md_path)
    parts = split_frontmatter(text)
    assets = parse_assets(parts.frontmatter)
    records: list[dict[str, Any]] = []
    file_issues: list[dict[str, Any]] = []
    file_issues.extend(semantic_figure_issues(text, relpath(md_path, kb_root)))

    body_refs: list[str] = []
    for match in IMAGE_RE.finditer(parts.body):
        target = normalize_ref(match.group(1))
        body_refs.append(target)
        record: dict[str, Any] = {
            "file": relpath(md_path, kb_root),
            "target": target,
            "context": context_around(parts.body, match.start(), match.end()),
            "issue_codes": [],
            "suspect_codes": [],
        }
        if target.startswith("data:"):
            record["issue_codes"].append("data_uri")
            records.append(record)
            continue
        if REMOTE_RE.match(target):
            record["issue_codes"].append("remote_image")
            records.append(record)
            continue
        if target.lower().endswith(".wmf"):
            record["issue_codes"].append("wmf_ref")
        image_path = ref_to_path(md_path, target)
        if image_path is None:
            record["issue_codes"].append("unresolved_ref")
            records.append(record)
            continue
        record["image_path"] = relpath(image_path, kb_root)
        if not image_path.exists():
            record["issue_codes"].append("missing_image")
            records.append(record)
            continue
        try:
            metrics = image_metrics(image_path)
            record.update(metrics)
        except Exception as exc:  # pragma: no cover - depends on corrupt files
            record["issue_codes"].append("image_open_error")
            record["error"] = str(exc)
            records.append(record)
            continue

        if alpha_black_risk(record):
            if fix_alpha_black:
                composite_alpha_on_white(image_path)
                record["fixed_alpha_black"] = True
                record.setdefault("resolved_codes", []).append("alpha_black_risk")
                refreshed = image_metrics(image_path)
                record.update({f"after_{key}": value for key, value in refreshed.items()})
            else:
                record["issue_codes"].append("alpha_black_risk")
        if dark_risk(record):
            record["issue_codes"].append("dark_risk")
        record["suspect_codes"] = suspect_codes(record, record["context"])
        records.append(record)

    body_media_refs = sorted(dict.fromkeys(ref for ref in body_refs if ref.startswith("media/")))
    if sorted(assets) != body_media_refs:
        file_issues.append(
            {
                "file": relpath(md_path, kb_root),
                "code": "assets_mismatch",
                "assets_only": sorted(set(assets) - set(body_media_refs)),
                "body_only": sorted(set(body_media_refs) - set(assets)),
            }
        )
    return records, file_issues


def composite_alpha_on_white(path: Path) -> None:
    from PIL import Image

    with Image.open(path) as im:
        rgba = im.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        white.alpha_composite(rgba)
        tmp = path.with_suffix(path.suffix + ".white-tmp")
        white.convert("RGB").save(tmp, format="PNG")
    os.replace(tmp, path)


def require_pillow() -> None:
    try:
        from PIL import Image  # noqa: F401
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Pillow is required for exercise image auditing. Use the project "
            "venv, Codex bundled Python, or install Pillow before running this "
            "script."
        ) from exc


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def make_contact_sheet(records: list[dict[str, Any]], kb_root: Path, out_path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    selected = [item for item in records if item.get("suspect_codes") or item.get("issue_codes")]
    if not selected:
        return
    thumb_w, thumb_h = 220, 160
    label_h = 70
    cols = 4
    rows = math.ceil(len(selected) / cols)
    sheet = Image.new("RGB", (cols * thumb_w, rows * (thumb_h + label_h)), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for idx, item in enumerate(selected):
        x = (idx % cols) * thumb_w
        y = (idx // cols) * (thumb_h + label_h)
        image_rel = item.get("image_path")
        if image_rel:
            image_path = kb_root / image_rel
            try:
                with Image.open(image_path) as im:
                    rgba = im.convert("RGBA")
                    white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                    white.alpha_composite(rgba)
                    thumb = white.convert("RGB")
                    thumb.thumbnail((thumb_w - 16, thumb_h - 16))
                    sheet.paste(thumb, (x + 8, y + 8))
            except Exception:
                draw.text((x + 8, y + 8), "[open error]", fill="red", font=font)
        label = f"{item.get('file','')}\n{Path(str(item.get('target',''))).name}\n{','.join(item.get('issue_codes') or item.get('suspect_codes') or [])}"
        draw.multiline_text((x + 8, y + thumb_h + 4), label[:160], fill="black", font=font, spacing=2)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=90)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    kb_root = Path(args.kb_root).resolve()
    files = collect_markdown(args.paths, kb_root)
    records: list[dict[str, Any]] = []
    file_issues: list[dict[str, Any]] = []
    for md_path in files:
        file_records, issues = audit_file(md_path, kb_root, args.fix_alpha_black)
        records.extend(file_records)
        file_issues.extend(issues)

    hard_issues = [
        item
        for item in records
        if any(code in item.get("issue_codes", []) for code in ["data_uri", "wmf_ref", "unresolved_ref", "missing_image", "image_open_error", "alpha_black_risk", "dark_risk"])
    ]
    suspects = [item for item in records if item.get("suspect_codes")]
    alpha_items = [item for item in records if "alpha_black_risk" in item.get("issue_codes", [])]
    dark_items = [item for item in records if "dark_risk" in item.get("issue_codes", [])]
    issues = file_issues + hard_issues
    summary = {
        "ok": len(issues) == 0,
        "file_count": len(files),
        "image_ref_count": len(records),
        "unique_image_file_count": len({item.get("image_path") for item in records if item.get("image_path")}),
        "issue_count": len(issues),
        "assets_mismatch_count": sum(1 for item in file_issues if item.get("code") == "assets_mismatch"),
        "semantic_missing_figure_count": sum(
            1 for item in file_issues if item.get("code") == "stem_requires_figure_but_assets_empty"
        ),
        "suspect_count": len(suspects),
        "alpha_black_risk_count": len(alpha_items),
        "dark_risk_count": len(dark_items),
        "fixed_alpha_black": bool(args.fix_alpha_black),
    }
    return {
        "summary": summary,
        "records": records,
        "issues": issues,
        "suspects": suspects,
        "alpha_black_risk": alpha_items,
        "dark_risk": dark_items,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit exercise-bank image references and transparent PNG readability.")
    parser.add_argument("paths", nargs="*", help="Exercise Markdown files or directories; defaults to raw/exercises")
    parser.add_argument("--kb-root", default=".", help="LLMWiki_BGE-M3 root")
    parser.add_argument("--out", required=True, help="Output directory for JSON reports")
    parser.add_argument("--json", action="store_true", help="Print summary JSON")
    parser.add_argument("--no-fail", action="store_true", help="Return 0 even if issues are found")
    parser.add_argument("--contact-sheet", action="store_true", help="Write a contact sheet of issue/suspect images")
    parser.add_argument("--fix-alpha-black", action="store_true", help="Composite alpha-black-risk PNGs onto white in place")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        require_pillow()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    kb_root = Path(args.kb_root).resolve()
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = kb_root / out_dir
    report = build_report(args)
    write_json(out_dir / "summary.json", report["summary"])
    write_json(out_dir / "records.json", report["records"])
    write_json(out_dir / "issues.json", report["issues"])
    write_json(out_dir / "suspects.json", report["suspects"])
    write_json(out_dir / "alpha_black_risk.json", report["alpha_black_risk"])
    write_json(out_dir / "dark_risk.json", report["dark_risk"])
    if args.contact_sheet:
        make_contact_sheet(report["records"], kb_root, out_dir / "contact_sheet.jpg")

    if args.json:
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report["summary"], ensure_ascii=False))

    if not report["summary"]["ok"] and not args.no_fail:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
