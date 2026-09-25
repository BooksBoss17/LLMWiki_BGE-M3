import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "media_tools.py"
SPEC = importlib.util.spec_from_file_location("media_tools", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_media_command_prefers_registered_portable_tool(monkeypatch, tmp_path):
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"tool")
    monkeypatch.setattr(MODULE, "resolve_component", lambda _id: {"resolved_path": str(ffmpeg)})

    assert MODULE.media_command("ffmpeg") == str(ffmpeg)
