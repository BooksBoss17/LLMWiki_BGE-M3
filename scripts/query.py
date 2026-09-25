#!/usr/bin/env python
"""LLMWiki_BGE-M3 交互式检索入口。

用法（项目 venv 中，通常由 run.bat 调用）：
    python scripts/query.py                     # 交互模式
    python scripts/query.py --query "电容器充电" # 单次查询
    python scripts/query.py --mode sparse --k 3 # 关键词检索
    python scripts/query.py --build             # 先重建索引再进入交互

命令（交互模式内）：
    /mode dense|sparse|hybrid   切换检索模式
    /k N                        设置返回条数
    quit / exit                 退出
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAG_SCRIPTS = PROJECT_ROOT / "BGE-M3" / "scripts"
if str(RAG_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RAG_SCRIPTS))

INDEX_DIR = PROJECT_ROOT / "BGE-M3" / "runtime" / "index"
DATA_DIR = PROJECT_ROOT / "BGE-M3" / "runtime" / "data"
MODEL_DIR = PROJECT_ROOT / "BGE-M3" / "runtime" / "models" / "BAAI" / "bge-m3"


def ensure_utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


def index_exists() -> bool:
    return (
        (INDEX_DIR / "index.faiss").is_file()
        and (INDEX_DIR / "metadata.json").is_file()
        and (INDEX_DIR / "sparse_weights.json").is_file()
        and (DATA_DIR / "chunks.jsonl").is_file()
    )


def model_exists() -> bool:
    return (MODEL_DIR / "pytorch_model.bin").is_file() and (MODEL_DIR / "config.json").is_file()


def build_index() -> int:
    print("未找到现有索引，开始用 raw/ 中的资料重建（BGE-M3/scripts/rag_pipeline.py）...")
    result = subprocess.run(
        [sys.executable, str(RAG_SCRIPTS / "rag_pipeline.py")],
        cwd=str(PROJECT_ROOT),
    )
    return result.returncode


def print_results(results) -> None:
    if not results:
        print("  （无结果）")
        return
    for r in results:
        print(f"  #{r['rank']} score={r['score']:.4f} [{r['source_type']}] {r['title'][:60]}")
        print(f"      {r['text'][:100]}")
        print(f"      来源: {r['source']}")


def main() -> int:
    ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="LLMWiki_BGE-M3 交互式检索")
    parser.add_argument("--query", help="单次查询后退出（省略则进入交互模式）")
    parser.add_argument("--mode", choices=("dense", "sparse", "hybrid"), default="dense")
    parser.add_argument("--k", type=int, default=5, help="返回条数（默认 5）")
    parser.add_argument("--build", action="store_true", help="启动前先重建索引")
    args = parser.parse_args()

    if not model_exists():
        print("❌ 未找到 BGE-M3 模型。请先运行 scripts\\download_models.ps1（或 setup.bat）。")
        print(f"   期望位置: {MODEL_DIR}")
        return 2

    if args.build or not index_exists():
        code = build_index()
        if code != 0:
            print("❌ 索引构建失败，请查看上方错误信息。")
            return code
        if not index_exists():
            print("❌ 索引构建流程结束但索引文件仍缺失（raw/ 中可能没有可用资料）。")
            return 3

    from rag_pipeline import RAGRetriever  # noqa: E402  延迟导入，加快错误提示

    retriever = RAGRetriever()
    print("加载模型与索引（首次加载需几十秒）...")
    retriever.load()

    mode, top_k = args.mode, args.k

    if args.query:
        print(f"\n查询[{mode}]: {args.query}")
        print_results(retriever.search(args.query, k=top_k, mode=mode))
        return 0

    print("\n==== LLMWiki_BGE-M3 交互式检索 ====")
    print("输入问题回车检索；/mode dense|sparse|hybrid 切换模式；/k N 改条数；quit 退出。")
    while True:
        try:
            line = input(f"\n[{mode}|top{top_k}] 查询> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in ("quit", "exit", "q"):
            break
        if line.startswith("/mode"):
            parts = line.split()
            if len(parts) == 2 and parts[1] in ("dense", "sparse", "hybrid"):
                mode = parts[1]
                print(f"已切换到 {mode} 检索。")
            else:
                print("用法: /mode dense|sparse|hybrid")
            continue
        if line.startswith("/k"):
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit() and int(parts[1]) > 0:
                top_k = int(parts[1])
                print(f"返回条数已设为 {top_k}。")
            else:
                print("用法: /k N")
            continue
        try:
            print_results(retriever.search(line, k=top_k, mode=mode))
        except Exception as exc:  # 检索失败不应退出会话
            print(f"检索出错: {exc}")
    print("再见。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
