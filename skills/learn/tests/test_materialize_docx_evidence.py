from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "exercise-solution-curation" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from materialize_docx_evidence import replace_evidence_images  # noqa: E402


class MaterializeEvidenceTests(unittest.TestCase):
    def test_hash_bound_formula_and_diagram_replace_without_unconfirmed_guess(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            formula = media / "formula.wmf"
            diagram = media / "diagram.wmf"
            visual = root / "diagram.png"
            formula.write_bytes(b"formula")
            diagram.write_bytes(b"diagram")
            visual.write_bytes(b"png")
            fhash = hashlib.sha256(formula.read_bytes()).hexdigest()
            dhash = hashlib.sha256(diagram.read_bytes()).hexdigest()
            markdown = "量为![图](media/formula.wmf)。\n\n![图](media/diagram.wmf)\n"
            result, stats = replace_evidence_images(
                markdown,
                media,
                {
                    fhash: {"status": "resolved", "final_latex": r"v_0"},
                    dhash: {"status": "not_applicable"},
                },
                {dhash: visual},
                root / "out-media",
            )
            self.assertIn(r"量为$v_0$。", result)
            self.assertIn("![原题图](media/", result)
            self.assertEqual(stats, {"formula": 1, "diagram": 1, "unmatched": 0})


if __name__ == "__main__":
    unittest.main()
