#!/usr/bin/env python
"""Compatibility wrapper for the isolated skill retriever.

Older agent instructions call this file directly. Keep its CLI stable while
delegating routing to skill_retriever.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from skill_retriever import route_query  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="*", help="task description")
    ap.add_argument("--json", action="store_true", help="machine-readable JSON")
    ap.add_argument("--top-k", type=int, default=5)
    args = ap.parse_args()
    query = " ".join(args.query).strip()
    if not query:
        print("Usage: agent_skill_router.py [--json] <task description>", file=sys.stderr)
        return 2

    # The legacy router should stay fast and deterministic, so semantic scoring
    # is disabled here. Use skill_retriever.py directly for hybrid retrieval.
    result = route_query(query, top_k=max(1, args.top_k), use_semantic=False)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Registry: {result['registry']}")
        print(f"Primary skill: {result['primary']}  (confidence={result['confidence']})")
        print(f"Role: {result['role']}")
        print(f"Reason: {result['reason']}")
        if result["queued"]:
            print("Queued next-phase skills:")
            for path in result["queued"]:
                print(f"- {path}")
        if result["alternatives"]:
            print("Alternatives:")
            for item in result["alternatives"]:
                print(f"- {item['path']} (score={item['score']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
