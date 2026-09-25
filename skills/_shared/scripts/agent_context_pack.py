#!/usr/bin/env python
"""Print a compact context pack for agents that do not auto-read project rules."""
from pathlib import Path
import argparse
ROOT = Path(__file__).resolve().parents[3]
FILES = [
    ROOT / "AGENTS.md",
    ROOT / "skills" / "registry.yaml",
]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--max-chars", type=int, default=18000)
    args=ap.parse_args()
    out=[]
    used=0
    for p in FILES:
        text=p.read_text(encoding="utf-8", errors="ignore") if p.exists() else f"MISSING: {p}\n"
        block=f"\n\n===== {p.relative_to(ROOT)} =====\n{text}\n"
        if used + len(block) > args.max_chars:
            remain=max(0,args.max_chars-used)
            block=block[:remain] + "\n[TRUNCATED]\n"
        out.append(block)
        used += len(block)
        if used >= args.max_chars:
            break
    print("".join(out))

if __name__ == "__main__":
    main()
