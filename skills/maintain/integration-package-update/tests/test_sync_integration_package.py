from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_integration_package.py"
SPEC = importlib.util.spec_from_file_location("sync_integration_package", SCRIPT)
assert SPEC and SPEC.loader
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


class SyncIntegrationPackageTests(unittest.TestCase):
    def test_ops_runtime_is_never_exported(self) -> None:
        reason = sync.skill_dir_skip(Path("_ops/runtime/logs"), set())
        self.assertEqual(reason, "skills operations runtime/log/state/report data")

    def test_apply_plan_removes_stale_file_and_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            stale_file = package / "raw" / "private.md"
            stale_dir = package / "tmp" / "state"
            stale_file.parent.mkdir(parents=True)
            stale_dir.mkdir(parents=True)
            stale_file.write_text("private", encoding="utf-8")
            (stale_dir / "task.json").write_text("{}", encoding="utf-8")
            deletes = [
                {"target": str(stale_file), "rel_target": "raw/private.md", "reason": "test"},
                {"target": str(stale_dir.parent), "rel_target": "tmp", "reason": "test"},
            ]

            sync.apply_plan([], deletes, package)

            self.assertFalse(stale_file.exists())
            self.assertFalse(stale_dir.parent.exists())

    def test_apply_plan_removes_directory_with_windows_long_path_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            stale_root = package / "Skills" / "_shared" / "model-tools" / "runtime"
            long_child = stale_root.joinpath(*(["segment_" + "x" * 32] * 7), "header_file.h")
            accessible_child = sync.accessible_path(long_child)
            accessible_child.parent.mkdir(parents=True, exist_ok=True)
            accessible_child.write_text("stale runtime", encoding="utf-8")
            self.assertGreater(len(str(long_child.resolve())), 260)

            sync.apply_plan(
                [],
                [
                    {
                        "target": str(stale_root),
                        "rel_target": "Skills/_shared/model-tools/runtime",
                        "reason": "test long path removal",
                    }
                ],
                package,
            )

            self.assertFalse(sync.accessible_path(stale_root).exists())

    def test_runtime_selection_uses_allowlist_and_excludes_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skills_runtime = root / "skills-runtime"
            bge_runtime = root / "bge-runtime"
            (skills_runtime / "tools" / "node" / "v1").mkdir(parents=True)
            (skills_runtime / "tools" / "node" / "v1" / "node.exe").write_bytes(b"node")
            (skills_runtime / "tools" / "node" / "v1" / "__pycache__").mkdir()
            (skills_runtime / "tools" / "node" / "v1" / "__pycache__" / "x.pyc").write_bytes(b"cache")
            (skills_runtime / "models" / "disabled").mkdir(parents=True)
            (skills_runtime / "models" / "disabled" / "model.bin").write_bytes(b"disabled")
            (bge_runtime / "env").mkdir(parents=True)
            (bge_runtime / "env" / "python.exe").write_bytes(b"python")
            (bge_runtime / "data").mkdir()
            (bge_runtime / "data" / "chunks.json").write_text("[]", encoding="utf-8")
            registry = {
                "components": {"node": {"package_path": "tools/node/v1"}},
                "environments": {},
                "portable_package": {"component_ids": ["node"], "environment_ids": [], "excluded_names": ["__pycache__"]},
            }

            entries = sync.portable_runtime_entries(registry, skills_runtime, bge_runtime)
            destinations = {item["dest"] for item in entries}

            self.assertIn("Skills/_shared/model-tools/runtime/tools/node/v1/node.exe", destinations)
            self.assertIn("BGE-M3/runtime/env/python.exe", destinations)
            self.assertFalse(any("__pycache__" in item for item in destinations))
            self.assertFalse(any("disabled" in item or "/data/" in item for item in destinations))

    def test_runtime_license_gate_covers_portable_environments(self) -> None:
        registry = {
            "components": {},
            "environments": {
                "good": {"license": "MIT", "redistribution": "allowed_with_notice"},
                "blocked": {"license": "unknown", "redistribution": "blocked_until_verified"},
            },
            "portable_package": {"component_ids": [], "environment_ids": ["good", "blocked"]},
        }

        result = sync.runtime_license_gate(registry)

        self.assertFalse(result["ok"])
        self.assertEqual(result["environments"]["good"]["license"], "MIT")
        self.assertTrue(any(item["component"] == "environment:blocked" for item in result["issues"]))


if __name__ == "__main__":
    unittest.main()
