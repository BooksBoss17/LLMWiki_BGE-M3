from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "skills" / "_shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import project_paths  # noqa: E402


class ProjectPathsTests(unittest.TestCase):
    def test_find_project_root_from_nested_directory(self) -> None:
        nested = ROOT / "skills" / "_shared" / "tests"
        self.assertEqual(project_paths.find_project_root(nested), ROOT)

    def test_resolve_raw_library(self) -> None:
        self.assertEqual(project_paths.resolve_path("library.raw", start=ROOT), ROOT / "raw")

    def test_model_runtime_environment_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"LLMWIKI_MODEL_RUNTIME_ROOT": temporary}):
                self.assertEqual(
                    project_paths.resolve_path("skills.model-tools.runtime", start=ROOT),
                    Path(temporary).resolve(),
                )

    def test_unknown_id_is_actionable(self) -> None:
        with self.assertRaisesRegex(project_paths.ProjectLayoutError, "未知路径 ID"):
            project_paths.resolve_path("missing.path", start=ROOT)

    def test_cli_json(self) -> None:
        command = [
            sys.executable,
            str(SCRIPTS / "project_paths.py"),
            "--root",
            str(ROOT),
            "resolve",
            "library.sql",
            "--json",
        ]
        result = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
        payload = json.loads(result.stdout)
        self.assertEqual(Path(payload["path"]), ROOT / "StudentDataSQL")


if __name__ == "__main__":
    unittest.main()
