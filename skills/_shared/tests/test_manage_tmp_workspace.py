from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import manage_tmp_workspace  # noqa: E402


class ManageTmpWorkspaceTests(unittest.TestCase):
    def test_dry_run_only_lists_expired_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "tasks" / "old.json"
            fresh = root / "state" / "fresh.json"
            old.parent.mkdir(parents=True)
            fresh.parent.mkdir(parents=True)
            old.write_text("old", encoding="utf-8")
            fresh.write_text("fresh", encoding="utf-8")
            now = time.time()
            os.utime(old, (now - 31 * 86400, now - 31 * 86400))

            report = manage_tmp_workspace.execute(root, days=30, apply=False, now=now)

            self.assertEqual(report["candidate_count"], 1)
            self.assertEqual(report["candidates"][0]["path"], "tasks/old.json")
            self.assertTrue(old.exists())
            self.assertTrue(fresh.exists())

    def test_apply_deletes_only_expired_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "old.bin"
            fresh = root / "fresh.bin"
            old.write_bytes(b"old")
            fresh.write_bytes(b"fresh")
            now = time.time()
            os.utime(old, (now - 45 * 86400, now - 45 * 86400))

            report = manage_tmp_workspace.execute(root, days=30, apply=True, now=now)

            self.assertEqual(report["deleted"], ["old.bin"])
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())


if __name__ == "__main__":
    unittest.main()
