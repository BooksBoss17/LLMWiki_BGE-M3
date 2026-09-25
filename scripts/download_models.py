#!/usr/bin/env python
"""下载 BGE-M3 模型权重到 BGE-M3/runtime/models/BAAI/bge-m3。

特性：
- 幂等：模型已存在且文件齐全时直接跳过；
- 断点续传：huggingface_hub snapshot_download 自带续传，可重复执行；
- 校验：下载后检查必需文件是否齐全；
- 镜像：尊重 HF_ENDPOINT 环境变量（例如 https://hf-mirror.com），
  仅允许 https 且拒绝 localhost/环回/私有地址。

用法（在项目 venv 中）：
    python scripts/download_models.py
"""

from __future__ import annotations

import ipaddress
import os
import sys
import urllib.parse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHARED_SCRIPTS = PROJECT_ROOT / "skills" / "_shared" / "scripts"
if str(SHARED_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_SCRIPTS))

from project_paths import resolve_path  # noqa: E402

REPO_ID = "BAAI/bge-m3"
REQUIRED_FILES = [
    "config.json",
    "pytorch_model.bin",
    "sentencepiece.bpe.model",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "colbert_linear.pt",
    "sparse_linear.pt",
    "1_Pooling/config.json",
]
ALLOW_PATTERNS = [
    "*.json",
    "*.txt",
    "*.md",
    "pytorch_model.bin",
    "colbert_linear.pt",
    "sparse_linear.pt",
    "sentencepiece.bpe.model",
]


def validate_endpoint(endpoint: str) -> str:
    """校验 HF_ENDPOINT：仅 https，且拒绝 localhost/环回/私有/保留地址。"""
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "https":
        raise ValueError(f"HF_ENDPOINT 必须使用 https：{endpoint}")
    host = parsed.hostname or ""
    if not host:
        raise ValueError(f"HF_ENDPOINT 缺少主机名：{endpoint}")
    if host in ("localhost",) or host.endswith(".localhost"):
        raise ValueError("HF_ENDPOINT 不允许指向 localhost")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return endpoint  # 普通域名
    if not addr.is_global:
        raise ValueError(f"HF_ENDPOINT 不允许指向私有/保留地址：{endpoint}")
    return endpoint


def model_target_dir() -> Path:
    return resolve_path("rag.models", start=PROJECT_ROOT) / "BAAI" / "bge-m3"


def model_is_complete(target: Path) -> bool:
    return all((target / name).is_file() and (target / name).stat().st_size > 0
               for name in REQUIRED_FILES)


def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def main() -> int:
    target = model_target_dir()
    print(f"模型目标目录: {target}")

    if model_is_complete(target):
        size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
        print(f"✅ BGE-M3 模型已存在（{human_size(size)}），跳过下载。")
        return 0

    endpoint = os.environ.get("HF_ENDPOINT", "").strip()
    if endpoint:
        validate_endpoint(endpoint)
        print(f"使用 HF_ENDPOINT: {endpoint}")

    target.mkdir(parents=True, exist_ok=True)

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("❌ 缺少 huggingface-hub，请先运行 setup.bat 安装依赖。")
        return 2

    print(f"开始下载 {REPO_ID}（约 2.3 GB，支持断点续传，可重复执行本脚本）...")
    try:
        snapshot_download(
            repo_id=REPO_ID,
            local_dir=str(target),
            local_dir_use_symlinks=False,
            allow_patterns=ALLOW_PATTERNS,
        )
    except Exception as exc:  # 网络错误等，保留现场以便续传
        print(f"❌ 下载失败: {exc}")
        print("   请检查网络后重新运行本脚本，已下载部分会自动续传。")
        print("   网络受限环境可设置镜像后重试，例如（PowerShell）:")
        print('   $env:HF_ENDPOINT = "https://hf-mirror.com"')
        return 3

    if not model_is_complete(target):
        missing = [n for n in REQUIRED_FILES if not (target / n).is_file()]
        print(f"❌ 下载后校验失败，缺少文件: {missing}")
        return 4

    size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    print(f"✅ BGE-M3 模型下载完成（{human_size(size)}），必需文件校验通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
