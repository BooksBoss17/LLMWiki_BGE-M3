from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "skills" / "_shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import migrate_layout_references  # noqa: E402
import verify_migration  # noqa: E402


class LayoutMigrationToolsTests(unittest.TestCase):
    def test_reference_migration_dry_run_then_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "manifest.yaml"
            source.write_text(
                "path: skills/_shared/model-runtime/runtime/models\n"
                "deliverable: LLMWiki_BGE-M3/output/report.md\n"
                "source: 原件/exam.pdf\n"
                "guard: codex_helpers/windows_utf8_guard.py\n"
                "download: codex_helpers/video_download_ascii_staging/job\n"
                "task: 临时代码/codex_tasks/task.json\n"
                'escaped: "C:\\\\kb\\\\临时代码\\\\任务状态\\\\job"\n',
                encoding="utf-8",
            )
            dry = migrate_layout_references.migrate(root, apply=False)
            self.assertEqual(dry["changed_count"], 1)
            self.assertIn("model-runtime", source.read_text(encoding="utf-8"))
            applied = migrate_layout_references.migrate(root, apply=True)
            self.assertEqual(applied["changed_count"], 1)
            self.assertIn("model-tools", source.read_text(encoding="utf-8"))
            self.assertIn("LLMWiki_BGE-M3/output/report.md", source.read_text(encoding="utf-8"))
            self.assertIn("source-library/exam.pdf", source.read_text(encoding="utf-8"))
            self.assertIn(".codex/helpers/windows_utf8_guard.py", source.read_text(encoding="utf-8"))
            self.assertIn("tmp/downloads/video-download-ascii-staging/job", source.read_text(encoding="utf-8"))
            self.assertIn("tmp/tasks/codex/task.json", source.read_text(encoding="utf-8"))
            self.assertIn(r"tmp\\logs\\legacy-task-status\\job", source.read_text(encoding="utf-8"))

    def test_verify_manifest_with_rename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            content = b"registry"
            (target / "registry.yaml").write_bytes(content)
            manifest = root / "manifest.jsonl"
            record = {
                "root_id": "legacy.model-runtime",
                "relative_path": "model-registry.yaml",
                "bytes": len(content),
                "sha256": verify_migration.sha256_file(target / "registry.yaml"),
            }
            manifest.write_text(json.dumps(record) + "\n", encoding="utf-8")
            result = verify_migration.verify(
                manifest,
                "legacy.model-runtime",
                target,
                renames={"model-registry.yaml": "registry.yaml"},
                allow_extra=False,
            )
            self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
