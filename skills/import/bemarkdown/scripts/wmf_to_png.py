"""Render WMF assets to full-frame PNG evidence without crop or raster resize."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


SHARED_SCRIPTS = Path(__file__).resolve().parents[3] / "_shared" / "scripts"
if str(SHARED_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_SCRIPTS))

from libreoffice_runner import (  # noqa: E402
    LibreOfficeRenderError,
    render_vector_full_frame,
    render_vectors_full_frame,
)


def convert_wmf_to_png_libreoffice(
    wmf_path: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    soffice_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Render one WMF through the shared source-preserving module."""
    source = Path(wmf_path)
    return render_vector_full_frame(
        source,
        output_dir,
        output_name=f"{source.stem}.png",
        soffice=soffice_path,
    )


def batch_convert_wmf(
    media_dir: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    soffice_path: str | os.PathLike[str] | None = None,
) -> dict[str, str | None]:
    """Render every WMF and write a hash/geometry manifest for downstream gates."""
    media_root = Path(media_dir)
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    wmf_files = sorted(media_root.glob("*.wmf"), key=lambda value: value.name.lower())
    print(f"Found {len(wmf_files)} WMF files")

    results: dict[str, str | None] = {}
    manifest: list[dict[str, Any]] = []
    if soffice_path:
        rendered_items = []
        for source in wmf_files:
            try:
                rendered_items.append((source, convert_wmf_to_png_libreoffice(source, output_root, soffice_path)))
            except (OSError, ValueError, LibreOfficeRenderError) as exc:
                rendered_items.append((source, exc))
    else:
        batch_results = render_vectors_full_frame(wmf_files, output_root, reuse_existing=True)
        rendered_items = [(source, batch_results[str(source.resolve())]) for source in wmf_files]

    for index, (source, rendered) in enumerate(rendered_items, start=1):
        if isinstance(rendered, dict):
            metadata = rendered
            metadata["status"] = "blocked" if metadata["geometry_issues"] else "rendered"
            manifest.append(metadata)
            if metadata["geometry_issues"]:
                results[source.name] = None
                print(
                    f"  BLOCKED [{index}/{len(wmf_files)}] {source.name}: "
                    f"geometry blocked ({','.join(metadata['geometry_issues'])})"
                )
            else:
                output = Path(metadata["output_path"])
                width = metadata["render_geometry"]["width"]
                height = metadata["render_geometry"]["height"]
                results[source.name] = str(output)
                print(
                    f"  OK [{index}/{len(wmf_files)}] {source.name} -> {output.name} "
                    f"({width}x{height}, full-frame)"
                )
        else:
            exc = rendered
            results[source.name] = None
            manifest.append({
                "source_path": str(source.resolve()),
                "status": "failed",
                "error": str(exc),
                "cropped": False,
                "resized": False,
                "full_resolution": True,
            })
            print(f"  FAILED [{index}/{len(wmf_files)}] {source.name}: {exc}")

    manifest_path = output_root / "wmf_render_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return results


if __name__ == "__main__":
    media_dir = sys.argv[1] if len(sys.argv) > 1 else r".\media"
    output_dir = sys.argv[2] if len(sys.argv) > 2 else r".\wmf_png"
    explicit = sys.argv[3] if len(sys.argv) > 3 else None
    converted = batch_convert_wmf(media_dir, output_dir, explicit)
    success = sum(value is not None for value in converted.values())
    print(f"\nOK {success}/{len(converted)} WMF files converted successfully")
