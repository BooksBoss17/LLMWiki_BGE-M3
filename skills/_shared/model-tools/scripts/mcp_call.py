#!/usr/bin/env python
"""Call a bundled MCP tool and print its JSON result."""
from __future__ import annotations

import argparse
import json

from mcp_runtime import call_tool


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--args-json", default="{}")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--json", action="store_true", help="compatibility flag; output is always JSON")
    args = parser.parse_args()
    try:
        arguments = json.loads(args.args_json)
        if not isinstance(arguments, dict):
            raise ValueError("--args-json 必须是 JSON object")
        result = call_tool(args.server, args.tool, arguments, timeout=args.timeout)
        tool_error = isinstance(result, dict) and result.get("isError") is True
        payload = {"ok": not tool_error, "server": args.server, "tool": args.tool, "result": result}
    except Exception as exc:
        payload = {"ok": False, "server": args.server, "tool": args.tool, "error": str(exc)}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
