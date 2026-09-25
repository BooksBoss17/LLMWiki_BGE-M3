#!/usr/bin/env python
"""Generate or apply portable MCP configuration for supported agents."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from project_paths import find_project_root


NATIVE_AGENTS = {
    "hermes": "hermes-cli",
    "codex": "codex-toml",
    "opencode": "opencode-json",
    "claude-code": "mcp-json",
    "gemini-cli": "mcp-json",
    "cursor": "mcp-json",
    "cline": "mcp-json",
    "roo": "mcp-json",
    "continue": "mcp-json",
    "copilot-chat": "vscode-mcp-json",
}
FALLBACK_AGENTS = {"aider"}
CODEX_START = "# --- LLMWiki portable MCP: bilibili-search ---"
CODEX_END = "# --- end LLMWiki portable MCP: bilibili-search ---"


def portable_python() -> Path:
    scripts = Path(__file__).resolve().parents[1] / "model-tools" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from model_runtime import resolve_component

    return Path(resolve_component("python-3.11.15-win-x64")["resolved_path"])


def build_agent_plan(agent: str, kb_root: Path) -> dict[str, Any]:
    normalized = agent.strip().lower()
    supported = set(NATIVE_AGENTS) | FALLBACK_AGENTS
    if normalized not in supported:
        raise ValueError(f"不支持的 agent：{agent}")
    python = portable_python()
    model_scripts = kb_root / "skills" / "_shared" / "model-tools" / "scripts"
    if normalized in FALLBACK_AGENTS:
        command = [
            str(python),
            str(model_scripts / "mcp_call.py"),
            "--server",
            "bilibili-search",
            "--tool",
            "<tool>",
            "--args-json",
            "<json>",
            "--json",
        ]
        return {"agent": normalized, "mode": "cli_fallback", "command": command, "apply_supported": False}
    command = [str(python), str(model_scripts / "mcp_server.py"), "--server", "bilibili-search"]
    return {
        "agent": normalized,
        "mode": "native_mcp",
        "adapter": NATIVE_AGENTS[normalized],
        "server": "bilibili-search",
        "command": command,
        "apply_supported": normalized in {"hermes", "codex", "opencode"},
    }


def render_fixture(plan: dict[str, Any]) -> dict[str, Any] | str:
    if plan["mode"] == "cli_fallback":
        return {"mode": "cli_fallback", "command": plan["command"]}
    command = list(plan["command"])
    server = plan["server"]
    adapter = plan["adapter"]
    if adapter == "opencode-json":
        return {"mcp": {server: {"type": "local", "command": command, "enabled": True}}}
    if adapter == "vscode-mcp-json":
        return {"servers": {server: {"type": "stdio", "command": command[0], "args": command[1:]}}}
    if adapter == "mcp-json":
        return {"mcpServers": {server: {"command": command[0], "args": command[1:]}}}
    if adapter == "codex-toml":
        return "\n".join(
            [f"[mcp_servers.{server}]", f"command = {json.dumps(command[0])}", f"args = {json.dumps(command[1:], ensure_ascii=False)}"]
        )
    return {"server": server, "command": command}


def backup_file(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.copy2(path, backup)
    return backup


def apply_codex(plan: dict[str, Any], config_path: Path) -> dict[str, Any]:
    command = list(plan["command"])
    block = "\n".join(
        [
            CODEX_START,
            f"[mcp_servers.{plan['server']}]",
            f"command = {json.dumps(command[0])}",
            f"args = {json.dumps(command[1:], ensure_ascii=False)}",
            "startup_timeout_sec = 120",
            CODEX_END,
        ]
    )
    original = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    if CODEX_START in original and CODEX_END in original:
        before, remainder = original.split(CODEX_START, 1)
        _old, after = remainder.split(CODEX_END, 1)
        updated = before.rstrip() + "\n\n" + block + after
    else:
        updated = original.rstrip() + ("\n\n" if original.strip() else "") + block + "\n"
    if updated == original:
        return {"changed": False, "backup": None, "config": str(config_path)}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    backup = backup_file(config_path) if config_path.exists() else None
    config_path.write_text(updated, encoding="utf-8", newline="\n")
    return {"changed": True, "backup": str(backup) if backup else None, "config": str(config_path)}


def load_jsonc(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    output: list[str] = []
    i = 0
    in_string = False
    escaped = False
    while i < len(text):
        char = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            i += 1
            continue
        if char == "/" and nxt == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        if char == "/" and nxt == "*":
            i += 2
            while i + 1 < len(text) and text[i : i + 2] != "*/":
                i += 1
            i += 2
            continue
        output.append(char)
        i += 1
    cleaned = re.sub(r",\s*([}\]])", r"\1", "".join(output))
    payload = json.loads(cleaned or "{}")
    if not isinstance(payload, dict):
        raise ValueError(f"Agent config 必须是 JSON object：{path}")
    return payload


def apply_opencode(plan: dict[str, Any], config_path: Path) -> dict[str, Any]:
    payload = load_jsonc(config_path) if config_path.exists() else {"$schema": "https://opencode.ai/config.json"}
    mcp = payload.setdefault("mcp", {})
    if not isinstance(mcp, dict):
        raise ValueError("OpenCode config 的 mcp 字段必须是 object")
    desired = {"type": "local", "command": list(plan["command"]), "enabled": True}
    if mcp.get(plan["server"]) == desired:
        return {"changed": False, "backup": None, "config": str(config_path)}
    mcp[plan["server"]] = desired
    config_path.parent.mkdir(parents=True, exist_ok=True)
    backup = backup_file(config_path) if config_path.exists() else None
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return {"changed": True, "backup": str(backup) if backup else None, "config": str(config_path)}


def render_hermes_block(plan: dict[str, Any]) -> str:
    command = list(plan["command"])
    yaml_scalar = lambda value: "'" + str(value).replace("'", "''") + "'"
    lines = [f"  {plan['server']}:", f"    command: {yaml_scalar(command[0])}", "    args:"]
    lines.extend(f"      - {yaml_scalar(item)}" for item in command[1:])
    lines.extend(["    connect_timeout: 120", "    enabled: true"])
    return "\n".join(lines)


def apply_hermes_yaml(plan: dict[str, Any], config_path: Path) -> dict[str, Any]:
    original = config_path.read_text(encoding="utf-8")
    block = render_hermes_block(plan)
    pattern = re.compile(rf"(?ms)^  {re.escape(plan['server'])}:\n.*?(?=^  [A-Za-z0-9_.-]+:\s*$|^[A-Za-z0-9_.-]+:|\Z)")
    if pattern.search(original):
        updated = pattern.sub(lambda _match: block + "\n", original, count=1)
    else:
        marker = re.search(r"(?m)^mcp_servers:\s*$", original)
        if not marker:
            updated = original.rstrip() + "\n\nmcp_servers:\n" + block + "\n"
        else:
            insert_at = marker.end()
            updated = original[:insert_at] + "\n" + block + original[insert_at:]
    if updated == original:
        return {"changed": False, "backup": None, "config": str(config_path)}
    backup = backup_file(config_path)
    config_path.write_text(updated, encoding="utf-8", newline="\n")
    return {"changed": True, "backup": str(backup), "config": str(config_path)}


def apply_hermes(plan: dict[str, Any], config_path: Path | None = None) -> dict[str, Any]:
    config_path = config_path or (Path.home() / "AppData" / "Local" / "hermes" / "config.yaml")
    listed = subprocess.run(
        ["hermes", "mcp", "list"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60
    )
    config_paths = [config_path]
    profiles_root = config_path.parent / "profiles"
    if profiles_root.exists():
        config_paths.extend(sorted(profiles_root.glob("*/config.yaml")))
    changes: list[dict[str, Any]] = []
    try:
        changes = [apply_hermes_yaml(plan, path) for path in config_paths if path.is_file()]
        verified = subprocess.run(
            ["hermes", "mcp", "test", plan["server"]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if verified.returncode != 0 or "Connected" not in verified.stdout or "Tools discovered" not in verified.stdout:
            raise RuntimeError("Hermes MCP transport verification failed")
        return {"changed": any(item["changed"] for item in changes), "configs": changes, "stdout": verified.stdout.strip()}
    except Exception:
        for item in changes:
            backup = Path(item["backup"]) if item.get("backup") else None
            if backup and backup.exists():
                shutil.copy2(backup, Path(item["config"]))
        raise


def apply_agent_plan(plan: dict[str, Any]) -> dict[str, Any]:
    agent = plan["agent"]
    if not plan.get("apply_supported"):
        raise RuntimeError(f"{agent} 当前只提供可验证 fixture/CLI fallback，不支持直接 apply")
    if agent == "codex":
        return apply_codex(plan, Path.home() / ".codex" / "config.toml")
    if agent == "opencode":
        return apply_opencode(plan, Path.home() / ".config" / "opencode" / "opencode.jsonc")
    if agent == "hermes":
        return apply_hermes(plan)
    raise RuntimeError(f"缺少 apply adapter：{agent}")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True, choices=sorted(set(NATIVE_AGENTS) | FALLBACK_AGENTS))
    parser.add_argument("--kb-root")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--user-authorized", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.kb_root).resolve() if args.kb_root else find_project_root(__file__)
    if args.apply and not args.user_authorized:
        payload = {"ok": False, "mode": "blocked", "error": "--apply requires --user-authorized"}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    try:
        plan = build_agent_plan(args.agent, root)
        result = apply_agent_plan(plan) if args.apply else None
        payload = {"ok": True, "mode": "applied" if args.apply else "dry-run", **plan}
        payload["fixture"] = render_fixture(plan)
        if result is not None:
            payload["result"] = result
    except Exception as exc:
        payload = {"ok": False, "mode": "error", "agent": args.agent, "error": str(exc)}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
