import importlib.util
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "validate_skill_library.py"
SPEC = importlib.util.spec_from_file_location("validate_skill_library", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_canonical_skill_scan_prunes_portable_runtime(tmp_path, monkeypatch):
    canonical = tmp_path / "import" / "demo" / "SKILL.md"
    runtime = tmp_path / "_shared" / "model-tools" / "runtime" / "vendor" / "SKILL.md"
    canonical.parent.mkdir(parents=True)
    runtime.parent.mkdir(parents=True)
    canonical.write_text("---\nname: demo\n---\n", encoding="utf-8")
    runtime.write_text("not skill source", encoding="utf-8")
    monkeypatch.setattr(MODULE, "ROOT", tmp_path)

    discovered = list(MODULE.iter_canonical_skill_files())

    assert discovered == [canonical]
