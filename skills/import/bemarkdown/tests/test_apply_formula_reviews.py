from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from apply_formula_reviews import apply_reviews  # noqa: E402


class ApplyFormulaReviewTests(unittest.TestCase):
    def test_applies_latex_and_plain_text_with_hash_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            wmf = media / "image_0001.wmf"
            wmf.write_bytes(b"wmf-source")
            digest = hashlib.sha256(wmf.read_bytes()).hexdigest()
            md = root / "input.md"
            md.write_text("A ![图](media/image_0001.wmf) B", encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps({"media/image_0001.wmf": {"resolution": {"status": "needs_vlm"}}}),
                encoding="utf-8",
            )
            reviews = root / "reviews.jsonl"
            reviews.write_text(
                json.dumps(
                    {
                        "candidate_id": "image_0001.wmf",
                        "status": "resolved",
                        "source_sha256": digest,
                        "replacement_kind": "text",
                        "replacement": "；",
                        "visual_evidence": "Rendered image is a semicolon.",
                        "confidence": 1.0,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            result = apply_reviews(
                md,
                media,
                reviews,
                manifest_path=manifest,
                out_md=root / "out.md",
                out_manifest=root / "out-manifest.json",
                require_all=True,
            )
            self.assertTrue(result["ok"])
            self.assertEqual((root / "out.md").read_text(encoding="utf-8"), "A ； B")
            updated = json.loads((root / "out-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(updated["media/image_0001.wmf"]["resolution"]["status"], "reviewed_final")
            self.assertEqual(updated["media/image_0001.wmf"]["resolution"]["replacement_kind"], "text")

    def test_rejects_stale_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            (media / "image_0001.wmf").write_bytes(b"current")
            md = root / "input.md"
            md.write_text("![图](media/image_0001.wmf)", encoding="utf-8")
            reviews = root / "reviews.jsonl"
            reviews.write_text(
                json.dumps(
                    {
                        "candidate_id": "image_0001.wmf",
                        "status": "resolved",
                        "source_sha256": "0" * 64,
                        "replacement": "?",
                        "replacement_kind": "text",
                        "visual_evidence": "punctuation",
                        "confidence": 1.0,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "source_sha256 mismatch"):
                apply_reviews(md, media, reviews, require_all=True)

    def test_keeps_coordinate_comma_and_unit_from_visual_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            wmf = media / "image_0044.wmf"
            wmf.write_bytes(b"coordinate-formula")
            digest = hashlib.sha256(wmf.read_bytes()).hexdigest()
            md = root / "input.md"
            md.write_text("坐标 ![图](media/image_0044.wmf)", encoding="utf-8")
            reviews = root / "reviews.json"
            reviews.write_text(
                json.dumps(
                    [
                        {
                            "candidate_id": "image_0044.wmf",
                            "status": "resolved",
                            "source_sha256": digest,
                            "replacement_kind": "latex",
                            "final_latex": "(0,5\\,\\mathrm{km})",
                            "visual_evidence": "渲染图显示逗号为坐标分隔符，单位为 km。",
                            "confidence": 1.0,
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = apply_reviews(md, media, reviews, out_md=root / "out.md")
            self.assertTrue(result["ok"])
            self.assertIn("$(0,5\\,\\mathrm{km})$", (root / "out.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
