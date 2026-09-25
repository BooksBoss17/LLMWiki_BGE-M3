import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "relocate_runtime_envs.py"
SPEC = importlib.util.spec_from_file_location("relocate_runtime_envs", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_rewrite_pyvenv_cfg_rebinds_absolute_interpreter_paths(tmp_path):
    cfg = tmp_path / "env" / "pyvenv.cfg"
    cfg.parent.mkdir()
    cfg.write_text(
        "home = C:\\old\\python\nversion = 3.11.15\nexecutable = C:\\old\\python.exe\ncommand = C:\\old\\python.exe -m venv C:\\old\\env\n",
        encoding="utf-8",
    )
    base = tmp_path / "runtime" / "python"

    changed = MODULE.rewrite_pyvenv_cfg(cfg, base, cfg.parent)

    text = cfg.read_text(encoding="utf-8")
    assert changed
    assert f"home = {base}" in text
    assert f"executable = {base / 'python.exe'}" in text
    assert f"command = {base / 'python.exe'} -m venv --upgrade {cfg.parent}" in text
    assert "C:\\old" not in text


def test_target_configs_follow_portable_registry_environments(tmp_path):
    model_tools = tmp_path / "skills" / "_shared" / "model-tools"
    model_tools.mkdir(parents=True)
    (model_tools / "registry.yaml").write_text(
        json.dumps(
            {
                "portable_package": {"environment_ids": ["cli-py311", "role-d-learning", "standalone"]},
                "environments": {
                    "cli-py311": {"path": "envs/cli-py311", "python": "Scripts/python.exe"},
                    "role-d-learning": {"path": "envs/role-d-learning", "python": "Scripts/python.exe"},
                    "standalone": {"path": "envs/standalone", "python": "python.exe"},
                },
            }
        ),
        encoding="utf-8",
    )

    configs = MODULE.target_configs(tmp_path)

    assert model_tools / "runtime" / "envs" / "role-d-learning" / "pyvenv.cfg" in configs
    assert model_tools / "runtime" / "envs" / "standalone" / "pyvenv.cfg" not in configs
    assert tmp_path / "BGE-M3" / "runtime" / "env" / "pyvenv.cfg" in configs


def test_cli_forces_utf8_stdout_for_unicode_paths():
    calls = []

    class Stream:
        def reconfigure(self, **kwargs):
            calls.append(kwargs)

    MODULE.configure_utf8_stdout(Stream())

    assert calls == [{"encoding": "utf-8", "errors": "replace"}]
