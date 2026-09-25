#!/usr/bin/env python3
"""Static capability lookup only; v3 does not create platform evidence."""

from __future__ import annotations

import argparse
import json


CODEX_POLICY = {
    "bounded": {"model": "gpt-5.6-luna", "reasoning": "xhigh"},
    "ambiguous": {"model": "gpt-5.6-sol", "reasoning": "medium"},
    "complex": {"model": "gpt-5.6-sol", "reasoning": "high"},
    "critical": {"model": "gpt-5.6-sol", "reasoning": "xhigh"},
}


def resolve_runtime(capability: str, *, platform: str = "codex") -> dict[str, str]:
    if platform != "codex":
        raise ValueError(f"unsupported platform: {platform}")
    if capability not in CODEX_POLICY:
        raise ValueError(f"unsupported capability: {capability}")
    return {"platform": platform, "capability": capability, **CODEX_POLICY[capability]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve the static worker model policy")
    parser.add_argument("--capability", choices=tuple(CODEX_POLICY), required=True)
    parser.add_argument("--platform", default="codex")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        payload = {"ok": True, **resolve_runtime(args.capability, platform=args.platform)}
        code = 0
    except ValueError as exc:
        payload = {"ok": False, "error": "policy_not_found", "message": str(exc)}
        code = 1
    print(json.dumps(payload, ensure_ascii=False, indent=None if args.json else 2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
