"""Resolve FFmpeg-family tools through the shared portable runtime."""
from __future__ import annotations

import sys
from pathlib import Path


MODEL_SCRIPTS = Path(__file__).resolve().parents[1] / "model-tools" / "scripts"
if str(MODEL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(MODEL_SCRIPTS))
from model_runtime import resolve_component


COMPONENT_IDS = {"ffmpeg": "ffmpeg-8.1.1", "ffprobe": "ffprobe-8.1.1"}


def media_command(name: str) -> str:
    if name not in COMPONENT_IDS:
        raise ValueError(f"未知媒体工具：{name}")
    path = Path(resolve_component(COMPONENT_IDS[name]).get("resolved_path") or "")
    if not path.is_file():
        raise FileNotFoundError(f"缺少共享媒体工具：{path}")
    return str(path)
