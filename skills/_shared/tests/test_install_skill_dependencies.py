import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_skill_dependencies.py"
SPEC = importlib.util.spec_from_file_location("install_skill_dependencies", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_rag_runtime_paths_use_current_project_layout(tmp_path):
    paths = MODULE.rag_runtime_paths(tmp_path)

    assert paths["venv"] == tmp_path / "BGE-M3" / "runtime" / "env"
    assert paths["models"] == tmp_path / "BGE-M3" / "runtime" / "models" / "BAAI" / "bge-m3"
    assert paths["requirements"].name == "rag-py311.txt"
    assert "model-tools" in paths["requirements"].as_posix()


def test_role_d_runtime_uses_shared_locked_requirements():
    role_d = next(item for item in MODULE.SKILL_RUNTIMES if item["name"] == "role-d-learning-tools")

    assert role_d["requirements"] == "_shared/model-tools/requirements/role-d-learning-py311.txt"
    assert role_d["venv"].endswith("runtime/envs/role-d-learning")
