from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "skills" / "_shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import import_handoff  # noqa: E402


class ImportHandoffVerifyTests(unittest.TestCase):
    def _write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_verify_rejects_missing_hashed_file_and_removes_stale_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            kb_root = Path(temporary)
            handoff_dir = kb_root / "handoff"
            handoff_path = handoff_dir / "import_handoff.json"
            graph_path = handoff_dir / "graph_update_result.json"
            completion_path = handoff_dir / "handoff_completion.json"
            completion_path.parent.mkdir(parents=True, exist_ok=True)
            completion_path.write_text('{"ok": true}', encoding="utf-8")

            self._write_json(
                handoff_path,
                {
                    "schema_version": 1,
                    "handoff_id": "missing-file",
                    "source_skill": "exercise-bank-import",
                    "requires_graph": True,
                    "changed_raw_files": ["raw/missing.md"],
                    "changed_wiki_files": [],
                    "question_ids": [],
                    "kp_ids": [],
                    "quality_gates": {"passed": True, "unresolved_issues": 0, "reports": []},
                    "file_hashes": {"raw/missing.md": "0" * 64},
                    "graph_changes": [],
                },
            )
            self._write_json(
                graph_path,
                {
                    "handoff_id": "missing-file",
                    "ok": True,
                    "validated": True,
                    "completion_ready": True,
                    "status": "no_change",
                },
            )

            with patch.object(import_handoff, "DEFAULT_KB_ROOT", kb_root):
                result = import_handoff.main(
                    ["verify", "--handoff", str(handoff_path), "--graph-result", str(graph_path)]
                )

            self.assertEqual(result, 2)
            self.assertFalse(completion_path.exists())

    def test_verify_rejects_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            kb_root = Path(temporary)
            raw_path = kb_root / "raw" / "present.md"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text("current content", encoding="utf-8")
            handoff_dir = kb_root / "handoff"
            handoff_path = handoff_dir / "import_handoff.json"
            graph_path = handoff_dir / "graph_update_result.json"

            self._write_json(
                handoff_path,
                {
                    "schema_version": 1,
                    "handoff_id": "hash-mismatch",
                    "source_skill": "exercise-bank-import",
                    "requires_graph": True,
                    "changed_raw_files": ["raw/present.md"],
                    "changed_wiki_files": [],
                    "question_ids": [],
                    "kp_ids": [],
                    "quality_gates": {"passed": True, "unresolved_issues": 0, "reports": []},
                    "file_hashes": {"raw/present.md": "0" * 64},
                    "graph_changes": [],
                },
            )
            self._write_json(
                graph_path,
                {
                    "handoff_id": "hash-mismatch",
                    "ok": True,
                    "validated": True,
                    "completion_ready": True,
                    "status": "no_change",
                },
            )

            with patch.object(import_handoff, "DEFAULT_KB_ROOT", kb_root):
                result = import_handoff.main(
                    ["verify", "--handoff", str(handoff_path), "--graph-result", str(graph_path)]
                )

            self.assertEqual(result, 2)
            self.assertFalse((handoff_dir / "handoff_completion.json").exists())

    def test_verify_rejects_changed_file_without_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            kb_root = Path(temporary)
            raw_path = kb_root / "raw" / "unbound.md"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text("content", encoding="utf-8")
            handoff_dir = kb_root / "handoff"
            handoff_path = handoff_dir / "import_handoff.json"
            graph_path = handoff_dir / "graph_update_result.json"

            self._write_json(
                handoff_path,
                {
                    "schema_version": 1,
                    "handoff_id": "missing-hash",
                    "source_skill": "exercise-bank-import",
                    "requires_graph": True,
                    "changed_raw_files": ["raw/unbound.md"],
                    "changed_wiki_files": [],
                    "question_ids": [],
                    "kp_ids": [],
                    "quality_gates": {"passed": True, "unresolved_issues": 0, "reports": []},
                    "file_hashes": {},
                    "graph_changes": [],
                },
            )
            self._write_json(
                graph_path,
                {
                    "handoff_id": "missing-hash",
                    "ok": True,
                    "validated": True,
                    "completion_ready": True,
                    "status": "no_change",
                },
            )

            with patch.object(import_handoff, "DEFAULT_KB_ROOT", kb_root):
                result = import_handoff.main(
                    ["verify", "--handoff", str(handoff_path), "--graph-result", str(graph_path)]
                )

            self.assertEqual(result, 2)
            self.assertFalse((handoff_dir / "handoff_completion.json").exists())

    def test_verify_writes_completion_for_current_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            kb_root = Path(temporary)
            raw_path = kb_root / "raw" / "current.md"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            content = b"current content\n"
            raw_path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            handoff_dir = kb_root / "handoff"
            handoff_path = handoff_dir / "import_handoff.json"
            graph_path = handoff_dir / "graph_update_result.json"

            self._write_json(
                handoff_path,
                {
                    "schema_version": 1,
                    "handoff_id": "current-hashes",
                    "source_skill": "exercise-bank-import",
                    "requires_graph": True,
                    "changed_raw_files": ["raw/current.md"],
                    "changed_wiki_files": [],
                    "question_ids": [],
                    "kp_ids": [],
                    "quality_gates": {"passed": True, "unresolved_issues": 0, "reports": []},
                    "file_hashes": {"raw/current.md": digest},
                    "graph_changes": [],
                },
            )
            self._write_json(
                graph_path,
                {
                    "handoff_id": "current-hashes",
                    "ok": True,
                    "validated": True,
                    "completion_ready": True,
                    "status": "no_change",
                },
            )

            with patch.object(import_handoff, "DEFAULT_KB_ROOT", kb_root):
                result = import_handoff.main(
                    ["verify", "--handoff", str(handoff_path), "--graph-result", str(graph_path)]
                )

            self.assertEqual(result, 0)
            completion = json.loads((handoff_dir / "handoff_completion.json").read_text(encoding="utf-8"))
            self.assertTrue(completion["ok"])
            self.assertEqual(completion["handoff_id"], "current-hashes")


if __name__ == "__main__":
    unittest.main()
