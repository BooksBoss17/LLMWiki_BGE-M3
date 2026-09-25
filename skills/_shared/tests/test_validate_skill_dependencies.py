import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_skill_dependencies.py"
SPEC = importlib.util.spec_from_file_location("validate_skill_dependencies", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_registry_audit_rejects_unknown_and_blocked_package_components(tmp_path):
    registry = {
        "components": {
            "good": {"path": "tools/good.exe", "license": "MIT", "redistribution": "allowed_with_notice"},
            "blocked": {"path": "tools/blocked.exe", "license": "unknown", "redistribution": "blocked_until_verified"},
        },
        "environments": {},
        "portable_package": {"component_ids": ["good", "missing", "blocked"], "environment_ids": []},
    }

    issues = MODULE.audit_registry(registry)

    assert any(item["code"] == "unknown_portable_component" for item in issues)
    assert any(item["code"] == "redistribution_blocked" for item in issues)


def test_registry_audit_rejects_unlicensed_or_blocked_portable_environment():
    registry = {
        "components": {},
        "environments": {
            "blocked": {"path": "envs/blocked", "python": "Scripts/python.exe", "redistribution": "blocked_until_verified"}
        },
        "portable_package": {"component_ids": [], "environment_ids": ["blocked"]},
    }

    issues = MODULE.audit_registry(registry)

    assert any(item["code"] == "environment_license_missing" for item in issues)
    assert any(item["code"] == "environment_redistribution_blocked" for item in issues)


def test_nvidia_driver_is_probed_with_nvidia_smi():
    assert MODULE.host_command_candidates("nvidia-driver") == ["nvidia-smi"]
