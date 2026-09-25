"""仅加载一次共享 TexTeller 快照，并绕过失效的 Windows CLI shim 执行推理。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image


def model_dir() -> Path:
    root = os.environ.get("LLMWIKI_MODEL_RUNTIME_ROOT")
    if not root:
        raise RuntimeError("TexTeller 需要 LLMWIKI_MODEL_RUNTIME_ROOT")
    path = Path(root) / "models" / "huggingface" / "OleehyO" / "TexTeller"
    if not (path / "config.json").exists():
        raise FileNotFoundError(f"共享 TexTeller 快照不完整：{path}")
    return path


@lru_cache(maxsize=1)
def load_runtime():
    from texteller import load_model, load_tokenizer

    path = model_dir()
    return load_model(model_dir=str(path)), load_tokenizer(tokenizer_dir=str(path))


def infer_formula(image_path: str | Path) -> dict[str, str]:
    from texteller import img2latex

    model, tokenizer = load_runtime()
    with Image.open(image_path) as image:
        rgb = np.asarray(image.convert("RGB"))
    latex = img2latex(model, tokenizer, [rgb], out_format="latex")[0]
    if not latex.strip():
        raise RuntimeError("TexTeller 返回了空公式")
    return {"latex": latex, "device": str(model.device)}
