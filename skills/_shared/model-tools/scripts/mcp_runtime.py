#!/usr/bin/env python
"""Portable MCP runtime shared by native agents and the CLI fallback."""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from model_runtime import environment_variables, resolve_component


SKILLS_ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = SKILLS_ROOT / "_registry" / "mcp_servers.catalog.yaml"


def load_server_catalog(path: Path = CATALOG_PATH) -> dict[str, dict[str, Any]]:
    servers: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^  - name:\s*(.+?)\s*$", raw)
        if match:
            current = {"name": match.group(1)}
            servers[current["name"]] = current
            continue
        if current is None:
            continue
        field = re.match(r"^    ([a-zA-Z0-9_]+):\s*(.+?)\s*$", raw)
        if field:
            current[field.group(1)] = field.group(2).strip('"\'')
    return servers


def server_command(server_name: str) -> list[str]:
    catalog = load_server_catalog()
    if server_name not in catalog:
        raise KeyError(f"未知 MCP server：{server_name}")
    server = catalog[server_name]
    node_id = server.get("runtime_node_component")
    package_id = server.get("runtime_server_component")
    if not node_id or not package_id:
        raise ValueError(f"MCP server 未声明 portable runtime：{server_name}")
    node = Path(resolve_component(str(node_id)).get("resolved_path") or "")
    entrypoint = Path(resolve_component(str(package_id)).get("resolved_path") or "")
    if not node.is_file():
        raise FileNotFoundError(f"缺少 bundled Node：{node}")
    if not entrypoint.is_file():
        raise FileNotFoundError(f"缺少 bundled MCP server：{entrypoint}")
    return [str(node), str(entrypoint)]


def server_environment(command: list[str]) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.update(environment_variables())
    env["PATH"] = os.pathsep.join([str(Path(command[0]).parent), env.get("PATH", "")])
    return env


def call_tool(server_name: str, tool: str, arguments: dict[str, Any], *, timeout: float = 30) -> dict[str, Any]:
    """Call one MCP tool through stdio without requiring native Agent MCP support."""
    command = server_command(server_name)
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=server_environment(command),
        bufsize=1,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    responses: queue.Queue[dict[str, Any]] = queue.Queue()
    stderr_lines: list[str] = []

    def read_stdout() -> None:
        for line in process.stdout:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                responses.put(payload)

    def read_stderr() -> None:
        for line in process.stderr:
            stderr_lines.append(line.rstrip())

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()

    def send(payload: dict[str, Any]) -> None:
        process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def wait_for(request_id: int) -> dict[str, Any]:
        while True:
            try:
                payload = responses.get(timeout=timeout)
            except queue.Empty as exc:
                detail = "\n".join(stderr_lines[-20:])
                raise TimeoutError(f"MCP 请求超时 id={request_id}: {detail}") from exc
            if payload.get("id") != request_id:
                continue
            if "error" in payload:
                raise RuntimeError(f"MCP 请求失败：{payload['error']}")
            result = payload.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(f"MCP 返回无效 result：{payload}")
            return result

    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "llmwiki-portable-mcp-client", "version": "1.0"},
                },
            }
        )
        wait_for(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
        )
        return wait_for(2)
    finally:
        try:
            process.stdin.close()
        except Exception:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
