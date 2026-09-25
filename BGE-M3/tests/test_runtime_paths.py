from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "skills" / "_shared" / "scripts"))

from project_paths import resolve_path  # noqa: E402


class BgeRuntimePathsTests(unittest.TestCase):
    def test_runtime_paths_are_under_bge_runtime(self) -> None:
        runtime = resolve_path("rag.runtime", start=PROJECT_ROOT)
        for path_id in ("rag.env", "rag.models", "rag.data", "rag.index", "rag.logs"):
            self.assertIn(runtime, resolve_path(path_id, start=PROJECT_ROOT).parents)

    def test_legacy_runtime_roots_are_absent(self) -> None:
        rag = resolve_path("library.rag", start=PROJECT_ROOT)
        for name in (".venv", "models", "data", "output"):
            self.assertFalse((rag / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
