#!/usr/bin/env python
"""Start one bundled stdio MCP server by canonical catalog name."""
from __future__ import annotations

import argparse
import subprocess

from mcp_runtime import server_command, server_environment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True)
    args = parser.parse_args()
    command = server_command(args.server)
    return subprocess.run(command, env=server_environment(command)).returncode


if __name__ == "__main__":
    raise SystemExit(main())
