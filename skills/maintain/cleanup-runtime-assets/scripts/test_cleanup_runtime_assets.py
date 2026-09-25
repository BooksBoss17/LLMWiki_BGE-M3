from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("cleanup_runtime_assets.py")
SPEC = importlib.util.spec_from_file_location("cleanup_runtime_assets", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class CleanupRuntimeAssetsTests(unittest.TestCase):
    def test_safe_join_rejects_escape_and_root(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises(MODULE.CleanupPlanError):
                MODULE.safe_join(root, "../outside")
            with self.assertRaises(MODULE.CleanupPlanError):
                MODULE.safe_join(root, ".")

    def test_measure_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            payload = root / "payload.bin"
            payload.write_bytes(b"llmwiki-cleanup")
            self.assertEqual(MODULE.measure(root), (15, 1))
            self.assertEqual(
                MODULE.sha256_file(payload),
                "940747d5aca4d4635f7901e3dc55f9954fbe30aa02191b0a7508a5598c6148cd",
            )


if __name__ == "__main__":
    unittest.main()
