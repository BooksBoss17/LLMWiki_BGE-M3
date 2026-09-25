#!/usr/bin/env python
"""Check MCP/plugin requirements declared by LLMWiki skill manifests.

Stdlib-only. It reads skills/_registry/mcp_servers.catalog.yaml and any
manifest.yaml files that declare `mcp_servers`, then compares them with
`hermes mcp list` when Hermes CLI is available.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "_registry" / "mcp_servers.catalog.yaml"


def parse_catalog(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    servers: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    current_list_key: str | None = None
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"\s*-\s+name:\s*(.+)$", line)
        if m:
            current = {"name": m.group(1).strip()}
            servers[current["name"]] = current
            current_list_key = None
            continue
        if current is None:
            continue
        # Only parse first-level server fields. Nested smoke-test dictionaries are
        # human-readable metadata and are intentionally ignored by this checker.
        kv = re.match(r"\s+([a-zA-Z0-9_]+):\s*(.*)$", line)
        if kv and indent == 4:
            key, value = kv.group(1), kv.group(2).strip()
            if value == "":
                current[key] = []
                current_list_key = key
            else:
                current[key] = value
                current_list_key = None
            continue
        li = re.match(r"\s+-\s+(.+)$", line)
        if li and current_list_key and indent >= 6:
            current.setdefault(current_list_key, []).append(li.group(1).strip())
    return servers


def parse_manifest_mcp(manifest: Path) -> list[str]:
    names: list[str] = []
    in_mcp = False
    for raw in manifest.read_text(encoding="utf-8", errors="ignore").splitlines():
        if re.match(r"^mcp_servers:\s*$", raw):
            in_mcp = True
            continue
        if in_mcp and raw and not raw.startswith(" "):
            in_mcp = False
        if in_mcp:
            m = re.match(r"\s*-\s+name:\s*(.+)$", raw)
            if m:
                names.append(m.group(1).strip())
    return names


def manifest_requirements() -> dict[str, list[str]]:
    required: dict[str, list[str]] = {}
    for manifest in ROOT.rglob("manifest.yaml"):
        rel = manifest.relative_to(ROOT).as_posix()
        if "/runtime/" in rel or rel.startswith(("_archive/", "_incoming/", "_runtime/")):
            continue
        names = parse_manifest_mcp(manifest)
        if names:
            required[rel] = names
    return required


def hermes_mcp_list() -> tuple[set[str], str | None]:
    try:
        proc = subprocess.run(
            ["hermes", "mcp", "list"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except Exception as exc:  # Hermes CLI may not exist for non-Hermes agents.
        return set(), str(exc)
    if proc.returncode != 0:
        return set(), proc.stderr.strip() or proc.stdout.strip() or f"exit={proc.returncode}"
    names: set[str] = set()
    for line in proc.stdout.splitlines():
        m = re.match(r"\s*([A-Za-z0-9_.-]+)\s{2,}", line)
        if m and m.group(1) not in {"Name", "MCP"}:
            names.add(m.group(1))
    return names, None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--no-live", action="store_true", help="do not call `hermes mcp list`")
    args = parser.parse_args()

    catalog = parse_catalog(CATALOG)
    required_by_manifest = manifest_requirements()
    required_names = sorted({name for names in required_by_manifest.values() for name in names})
    catalog_missing = [name for name in required_names if name not in catalog]
    live_error = None
    installed: set[str] = set()
    live_missing: list[str] = []
    if not args.no_live:
        installed, live_error = hermes_mcp_list()
        if not live_error:
            live_missing = [name for name in required_names if name not in installed]

    result = {
        "ok": not catalog_missing and not live_missing and not live_error,
        "required": required_by_manifest,
        "required_names": required_names,
        "catalog_missing": catalog_missing,
        "installed": sorted(installed),
        "live_missing": live_missing,
        "live_error": live_error,
        "catalog": {name: catalog.get(name, {}) for name in required_names},
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
