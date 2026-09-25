import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "configure_agent_mcp.py"
SPEC = importlib.util.spec_from_file_location("configure_agent_mcp", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_native_and_fallback_agents_share_one_portable_interface(monkeypatch, tmp_path):
    python = tmp_path / "skills" / "_shared" / "model-tools" / "runtime" / "python" / "python.exe"
    launcher = tmp_path / "skills" / "_shared" / "model-tools" / "scripts" / "mcp_server.py"
    python.parent.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    launcher.write_text("# launcher", encoding="utf-8")
    monkeypatch.setattr(MODULE, "portable_python", lambda: python)

    codex = MODULE.build_agent_plan("codex", tmp_path)
    aider = MODULE.build_agent_plan("aider", tmp_path)

    assert codex["mode"] == "native_mcp"
    assert codex["command"] == [str(python), str(launcher), "--server", "bilibili-search"]
    assert aider["mode"] == "cli_fallback"
    assert aider["command"][1].endswith("mcp_call.py")


def test_codex_apply_is_backed_up_and_idempotent(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('model = "demo"\n', encoding="utf-8")
    plan = {
        "server": "bilibili-search",
        "command": ["C:/portable/python.exe", "C:/repo/mcp_server.py", "--server", "bilibili-search"],
    }

    first = MODULE.apply_codex(plan, config)
    second = MODULE.apply_codex(plan, config)

    text = config.read_text(encoding="utf-8")
    assert first["changed"] and Path(first["backup"]).is_file()
    assert not second["changed"] and second["backup"] is None
    assert text.count("[mcp_servers.bilibili-search]") == 1
    assert "npx" not in text.lower()


def test_opencode_apply_preserves_existing_config_and_is_idempotent(tmp_path):
    config = tmp_path / "opencode.jsonc"
    config.write_text('{"$schema":"https://opencode.ai/config.json","plugin":[]}', encoding="utf-8")
    plan = {
        "server": "bilibili-search",
        "command": ["C:/portable/python.exe", "C:/repo/mcp_server.py", "--server", "bilibili-search"],
    }

    first = MODULE.apply_opencode(plan, config)
    second = MODULE.apply_opencode(plan, config)
    payload = MODULE.load_jsonc(config)

    assert first["changed"] and Path(first["backup"]).is_file()
    assert not second["changed"]
    assert payload["plugin"] == []
    assert payload["mcp"]["bilibili-search"]["command"] == plan["command"]


def test_uninstalled_agent_fixtures_are_serializable_and_never_use_npx(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    python.write_bytes(b"python")
    monkeypatch.setattr(MODULE, "portable_python", lambda: python)

    for agent in ("claude-code", "gemini-cli", "cursor", "cline", "roo", "continue", "copilot-chat"):
        plan = MODULE.build_agent_plan(agent, tmp_path)
        fixture = MODULE.render_fixture(plan)
        rendered = __import__("json").dumps(fixture)
        assert "bilibili-search" in rendered
        assert "npx" not in rendered.lower()


def test_hermes_yaml_replaces_legacy_npx_block_idempotently(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "mcp_servers:\n  bilibili-search:\n    command: npx\n    args:\n      - bilibili-mcp-js\n    enabled: true\nplatforms:\n  demo: true\n",
        encoding="utf-8",
    )
    plan = {
        "server": "bilibili-search",
        "command": ["C:/portable/python.exe", "C:/repo/mcp_server.py", "--server", "bilibili-search"],
    }

    first = MODULE.apply_hermes_yaml(plan, config)
    second = MODULE.apply_hermes_yaml(plan, config)
    text = config.read_text(encoding="utf-8")
    parsed = __import__("yaml").safe_load(text)

    assert first["changed"] and not second["changed"]
    assert "npx" not in text
    assert "platforms:" in text
    assert parsed["mcp_servers"]["bilibili-search"]["command"] == plan["command"][0]
