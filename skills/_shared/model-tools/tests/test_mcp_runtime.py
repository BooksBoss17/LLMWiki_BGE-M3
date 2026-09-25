import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "mcp_runtime.py"
SPEC = importlib.util.spec_from_file_location("shared_mcp_runtime", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_server_command_uses_only_registered_portable_components(monkeypatch, tmp_path):
    node = tmp_path / "tools" / "node" / "node.exe"
    server = tmp_path / "tools" / "bilibili" / "dist" / "index.js"
    node.parent.mkdir(parents=True)
    server.parent.mkdir(parents=True)
    node.write_bytes(b"node")
    server.write_text("// server", encoding="utf-8")

    def fake_resolve(component_id: str):
        return {
            "node-24.16.0": {"resolved_path": str(node)},
            "bilibili-mcp-js-0.1.3": {"resolved_path": str(server)},
        }[component_id]

    monkeypatch.setattr(MODULE, "resolve_component", fake_resolve)
    monkeypatch.setattr(
        MODULE,
        "load_server_catalog",
        lambda: {
            "bilibili-search": {
                "runtime_node_component": "node-24.16.0",
                "runtime_server_component": "bilibili-mcp-js-0.1.3",
            }
        },
    )

    command = MODULE.server_command("bilibili-search")

    assert command == [str(node), str(server)]
    assert all("npx" not in part.lower() for part in command)


def test_cli_fallback_initializes_and_calls_tool(monkeypatch, tmp_path):
    fake = tmp_path / "fake_mcp.py"
    fake.write_text(
        """
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get('method') == 'initialize':
        print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':{'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':{'name':'fake','version':'1'}}}), flush=True)
    elif message.get('method') == 'tools/call':
        print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':{'content':[{'type':'text','text':'called:' + message['params']['name']}]}}), flush=True)
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(MODULE, "server_command", lambda _name: [sys.executable, str(fake)])

    result = MODULE.call_tool("demo", "lookup", {"mid": "1"}, timeout=5)

    assert result["content"][0]["text"] == "called:lookup"
