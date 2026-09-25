"""转写控制器使用的共享 OCR/VLM/STT 运行库路径。"""
from __future__ import annotations

import importlib.util
from pathlib import Path


COMPONENT_IDS = {
    "paddle": "paddleocr-vl-1.6",
    "formula": "texteller-1.0.2",
    "document-stt": "faster-whisper-large-v3",
}


def resolver(skill_root: Path):
    skills_root = skill_root.resolve().parents[1]
    script = skills_root / "_shared" / "model-tools" / "scripts" / "model_runtime.py"
    spec = importlib.util.spec_from_file_location("llmwiki_shared_model_runtime", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载共享模型解析器：{script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runtime_root(skill_root: Path) -> Path:
    return resolver(skill_root).runtime_root()


def environment_python(skill_root: Path, name: str) -> Path:
    module = resolver(skill_root)
    if name == "cli":
        environment = module.load_registry()["environments"]["cli-py311"]
        return module.runtime_root() / environment["path"] / environment["python"]
    component_id = COMPONENT_IDS[name]
    return Path(module.resolve_component(component_id)["python"])
