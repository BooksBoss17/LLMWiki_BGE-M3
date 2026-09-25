from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
ROOT = SKILL.parents[2]
sys.path.insert(0, str(SKILL / "scripts"))

from validate_diagram_asset_separation import validate_manifest  # noqa: E402


class DiagramAssetSeparationTests(unittest.TestCase):
    def setUp(self) -> None:
        (ROOT / "tmp").mkdir(exist_ok=True)
        self.output = Path(tempfile.mkdtemp(prefix="diagram-separation-", dir=ROOT / "tmp"))

    def tearDown(self) -> None:
        shutil.rmtree(self.output, ignore_errors=True)

    def write_json(self, path: Path, payload: dict) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def make_asset(self, name: str, purpose: str, style: str, *, analysis: bool = False) -> dict:
        root = self.output / "assets" / name
        item = root / "main"
        provenance = item / "provenance"
        provenance.mkdir(parents=True)
        asset = item / "diagram.png"
        asset.write_bytes((name + purpose).encode("utf-8"))
        vectors = []
        if analysis:
            vectors.append({"id": "mg", "semantic_role": "analysis"})
        self.write_json(
            provenance / "diagram_spec.json",
            {"schema_version": 3, "purpose": purpose, "style_profile": style, "vectors": vectors},
        )
        self.write_json(provenance / "geometry_report.json", {"schema_version": 1, "ok": True, "failed_assertions": []})
        completion = self.write_json(root / "completion.json", {"schema_version": 2, "ok": True, "items": {"main": {}}})
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()
        return {
            "asset_path": asset.relative_to(self.output).as_posix(),
            "asset_sha256": digest,
            "completion_path": completion.relative_to(self.output).as_posix(),
            "item_id": "main",
        }

    def manifest(self, *, question_analysis: bool = False, leak_solution_into_practice: bool = False) -> Path:
        question = self.make_asset("question", "question", "exam-monochrome", analysis=question_analysis)
        solution = self.make_asset("solution", "solution", "solution-color", analysis=True)
        practice_image = solution["asset_path"] if leak_solution_into_practice else question["asset_path"]
        practice = self.output / "练习.md"
        answer = self.output / "练习答案.md"
        practice.write_text(f"![题图]({practice_image})\n", encoding="utf-8")
        answer.write_text(f"![解析图]({solution['asset_path']})\n", encoding="utf-8")
        return self.write_json(
            self.output / "diagram_assets.json",
            {
                "schema_version": 1,
                "practice_markdown": practice.relative_to(self.output).as_posix(),
                "answer_markdown": answer.relative_to(self.output).as_posix(),
                "entries": [
                    {"question_id": "Q1", "usage": "question_image", **question},
                    {"question_id": "Q1", "usage": "solution_image", **solution},
                ],
            },
        )

    def test_clean_question_and_solution_assets_pass(self) -> None:
        result = validate_manifest(self.manifest(), output_root=self.output)
        self.assertTrue(result["ok"])
        self.assertEqual(result["entry_count"], 2)

    def test_solution_image_in_practice_fails(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "non-question"):
            validate_manifest(self.manifest(leak_solution_into_practice=True), output_root=self.output)

    def test_analysis_overlay_in_question_asset_fails(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "clean monochrome"):
            validate_manifest(self.manifest(question_analysis=True), output_root=self.output)


if __name__ == "__main__":
    unittest.main()
