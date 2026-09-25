import importlib.util
import json
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "mcp_call.py"
SPEC = importlib.util.spec_from_file_location("mcp_call", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_cli_reports_mcp_tool_error_as_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        MODULE,
        "call_tool",
        lambda *_args, **_kwargs: {
            "content": [{"type": "text", "text": "rate limited"}],
            "isError": True,
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["mcp_call.py", "--server", "demo", "--tool", "lookup", "--json"],
    )

    exit_code = MODULE.main()
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["result"]["isError"] is True
