import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "model_runtime.py"
SPEC = importlib.util.spec_from_file_location("shared_model_runtime", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_registry_is_json_compatible_yaml_and_has_required_paddle_components():
    registry = json.loads((ROOT / "registry.yaml").read_text(encoding="utf-8"))
    required = {
        "paddlepaddle-gpu",
        "paddleocr",
        "paddlex",
        "pp-ocrv6-medium-det",
        "pp-ocrv6-medium-rec",
        "pp-formulanet-plus-l",
        "paddleocr-vl-1.6",
        "pp-doclayoutv3",
    }
    assert required <= set(registry["components"])


def test_portable_registry_includes_locked_role_d_environment():
    registry = json.loads((ROOT / "registry.yaml").read_text(encoding="utf-8"))
    environment = registry["environments"]["role-d-learning"]
    requirements = ROOT / environment["requirements"]

    assert "role-d-learning" in registry["portable_package"]["environment_ids"]
    assert environment["python"] == "Scripts/python.exe"
    assert requirements.is_file()
    pins = [line for line in requirements.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    assert pins and all("==" in line for line in pins)


def test_runtime_override_and_resolve(monkeypatch, tmp_path):
    monkeypatch.setenv("LLMWIKI_MODEL_RUNTIME_ROOT", str(tmp_path))
    resolved = MODULE.resolve_component("faster-whisper-large-v3")
    assert Path(resolved["resolved_path"]).is_relative_to(tmp_path)
    assert resolved["python"].endswith("document-stt-py310\\python.exe")


def test_environment_uses_ascii_native_alias_for_unicode_runtime(monkeypatch, tmp_path):
    if os.name != "nt":
        return
    runtime = tmp_path / "中文 运行库"
    (runtime / "models").mkdir(parents=True)
    monkeypatch.setenv("LLMWIKI_MODEL_RUNTIME_ROOT", str(runtime))

    env = MODULE.environment_variables()
    alias = Path(env["LLMWIKI_MODEL_RUNTIME_ROOT"])
    try:
        resolved = MODULE.resolve_component("faster-whisper-large-v3")
        assert alias.samefile(runtime)
        assert str(alias).isascii()
        assert env["PADDLE_PDX_CACHE_HOME"].isascii()
        assert resolved["resolved_path"].isascii()
        assert resolved["python"].isascii()
    finally:
        if len(alias.drive) == 2 and alias.anchor == f"{alias.drive}\\" and alias.drive.upper() in {f"{letter}:" for letter in "RSTUVWXYZ"}:
            subprocess.run(["subst", alias.drive, "/D"], check=False)


def test_unknown_component_fails():
    try:
        MODULE.resolve_component("not-a-model")
    except KeyError as exc:
        assert "未知的共享模型组件" in str(exc)
    else:
        raise AssertionError("未知模型 ID 应当失败")


def test_exec_runs_registered_portable_tool(monkeypatch, tmp_path):
    monkeypatch.setenv("LLMWIKI_MODEL_RUNTIME_ROOT", str(tmp_path))
    tool = tmp_path / "tools" / "where" / "where.exe"
    tool.parent.mkdir(parents=True)
    shutil.copy2(Path(os.environ["SystemRoot"]) / "System32" / "where.exe", tool)
    monkeypatch.setattr(
        MODULE,
        "load_registry",
        lambda: {
            "components": {
                    "portable-where": {
                        "kind": "tool",
                        "path": "tools/where/where.exe",
                }
            },
            "environments": {},
        },
    )
    completed = MODULE.execute_component(
        "portable-where",
        ["cmd.exe"],
    )

    assert completed.returncode == 0


def test_resolve_rejects_component_path_outside_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("LLMWIKI_MODEL_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setattr(
        MODULE,
        "load_registry",
        lambda: {
            "components": {"escaped": {"kind": "tool", "path": "../outside.exe"}},
            "environments": {},
        },
    )

    try:
        MODULE.resolve_component("escaped")
    except ValueError as exc:
        assert "运行库之外" in str(exc)
    else:
        raise AssertionError("组件路径不得逃逸共享运行库")


def test_profiles_only_reference_registered_components():
    registry = MODULE.load_registry()["components"]
    payload = json.loads((ROOT / "profiles.yaml").read_text(encoding="utf-8"))
    profiles = payload["profiles"]
    disabled = set(payload.get("disabled_components", {}))
    for profile in profiles.values():
        values = []
        for key in ("primary", "detector"):
            if profile.get(key):
                values.append(profile[key])
        for key in ("required_dependencies", "candidates", "fallbacks"):
            values.extend(profile.get(key, []))
        assert set(values) <= set(registry)
        assert not (set(values) & disabled)


def test_doctor_requires_inference_proof_for_model_components(monkeypatch, tmp_path):
    monkeypatch.setenv("LLMWIKI_MODEL_RUNTIME_ROOT", str(tmp_path))
    model = tmp_path / "models" / "faster-whisper" / "faster-whisper-medium"
    model.mkdir(parents=True)
    (model / "model.bin").write_bytes(b"test")
    env = tmp_path / "envs" / "document-stt-py310"
    env.mkdir(parents=True)
    (env / "python.exe").write_bytes(b"test")
    inventory = {
        "components": {"faster-whisper-medium": {"weights_verified": True}}
    }
    (tmp_path / "inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
    result = MODULE.doctor(False, "faster-whisper-medium")
    assert not result["ok"]
    assert result["components"][0]["inference_required"]
    assert not result["components"][0]["inference_verified"]


def test_doctor_reports_nonfatal_runtime_warnings_from_validation_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("LLMWIKI_MODEL_RUNTIME_ROOT", str(tmp_path))
    model = tmp_path / "models" / "demo"
    model.mkdir(parents=True)
    (model / "model.bin").write_bytes(b"test")
    (tmp_path / "inventory.json").write_text(
        json.dumps({"components": {"demo-model": {"weights_verified": True}}}), encoding="utf-8"
    )
    (tmp_path / "validation-lock.json").write_text(
        json.dumps(
            {
                "components": {
                    "demo-model": {
                        "inference_verified": True,
                        "device": "gpu:0",
                        "warnings": ["compiled cuDNN differs from the machine runtime"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        MODULE,
        "load_registry",
        lambda: {
            "components": {"demo-model": {"kind": "model", "path": "models/demo"}},
            "environments": {},
        },
    )

    result = MODULE.doctor(False, "demo-model")

    assert result["ok"]
    assert result["warnings_present"]
    assert result["components"][0]["warning_count"] == 1
    assert "cuDNN" in result["components"][0]["runtime_warnings"][0]


def test_doctor_rejects_portable_tool_hash_drift(monkeypatch, tmp_path):
    monkeypatch.setenv("LLMWIKI_MODEL_RUNTIME_ROOT", str(tmp_path))
    tool = tmp_path / "tools" / "demo" / "demo.exe"
    tool.parent.mkdir(parents=True)
    tool.write_bytes(b"changed")
    monkeypatch.setattr(
        MODULE,
        "load_registry",
        lambda: {
            "components": {
                "demo": {
                    "kind": "tool",
                    "path": "tools/demo/demo.exe",
                    "artifact_sha256": "0" * 64,
                }
            },
            "environments": {},
        },
    )

    result = MODULE.doctor(False, "demo")

    assert not result["ok"]
    assert not result["components"][0]["hash_verified"]


def test_doctor_accepts_non_executable_registered_artifact(monkeypatch, tmp_path):
    artifact = tmp_path / "tools" / "server" / "index.js"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("// mcp", encoding="utf-8")
    monkeypatch.setenv("LLMWIKI_MODEL_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setattr(
        MODULE,
        "load_registry",
        lambda: {
            "components": {"mcp": {"kind": "mcp_server", "path": "tools/server/index.js"}},
            "environments": {},
        },
    )
    (tmp_path / "inventory.json").write_text(
        json.dumps({"components": {"mcp": {"weights_verified": True}}}), encoding="utf-8"
    )

    result = MODULE.doctor(False, "mcp")

    assert result["ok"]
    assert result["components"][0]["installed"]


def test_doctor_uses_portable_package_manifest_after_relocation(monkeypatch, tmp_path):
    runtime = tmp_path / "Skills" / "_shared" / "model-tools" / "runtime"
    model = runtime / "models" / "demo"
    model.mkdir(parents=True)
    (model / "model.bin").write_bytes(b"model")
    (tmp_path / "PORTABLE_RUNTIME_MANIFEST.json").write_text(
        json.dumps(
            {
                "files": [
                    {"path": "Skills/_shared/model-tools/runtime/models/demo/model.bin", "sha256": "unused"}
                ],
                "validation_lock": {
                    "components": {"demo-model": {"inference_verified": True, "device": "cpu", "validated_at": "now"}}
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LLMWIKI_MODEL_RUNTIME_ROOT", str(runtime))
    monkeypatch.setattr(
        MODULE,
        "load_registry",
        lambda: {
            "components": {"demo-model": {"kind": "model", "path": "models/demo"}},
            "environments": {},
        },
    )

    result = MODULE.doctor(False, "demo-model")

    assert result["ok"]
    assert result["portable_manifest_present"]
    assert result["components"][0]["weights_status"] == "portable_package_manifest"
