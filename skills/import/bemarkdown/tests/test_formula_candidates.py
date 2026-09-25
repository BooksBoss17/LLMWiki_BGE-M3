from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fix_chinese_subscripts import fix_garbled_subscripts  # noqa: E402
from formula_candidate_utils import (  # noqa: E402
    assess_formula_candidate,
    make_formula_review_task,
    resolve_formula_consensus,
)


class FormulaCandidateTests(unittest.TestCase):
    def test_single_engine_never_becomes_final(self) -> None:
        result = assess_formula_candidate("F=ma", 0.97, engine="texteller")
        self.assertEqual(result["status"], "machine_candidate")
        self.assertEqual(result["risk_flags"], [])

    def test_two_clean_engines_must_agree_before_machine_final(self) -> None:
        result = resolve_formula_consensus(
            [
                {"engine": "pp_formulanet", "text": r"F=ma", "confidence": 0.95},
                {"engine": "texteller", "text": r"F = ma", "confidence": None},
            ]
        )
        self.assertEqual(result["status"], "machine_final")
        self.assertEqual(result["final_latex"], "F=ma")
        self.assertEqual(result["agreed_engines"], ["pp_formulanet", "texteller"])

    def test_two_engines_without_native_scores_can_still_form_consensus(self) -> None:
        result = resolve_formula_consensus([
            {"engine": "pp_formulanet", "text": "F=ma", "confidence": None},
            {"engine": "texteller", "text": "F=ma", "confidence": None},
        ])
        self.assertEqual(result["status"], "machine_final")

    def test_engine_conflict_requires_vlm(self) -> None:
        result = resolve_formula_consensus(
            [
                {"engine": "pp_formulanet", "text": "F=mv", "confidence": 0.98},
                {"engine": "texteller", "text": "F=mg", "confidence": 0.96},
            ]
        )
        self.assertEqual(result["status"], "needs_vlm")
        self.assertIn("engine_disagreement", result["risk_flags"])

    def test_single_character_fraction_root_and_geometry_require_vlm(self) -> None:
        for latex in ("x", r"\frac{a}{b}", r"\sqrt{x}"):
            result = resolve_formula_consensus(
                [
                    {"engine": "pp_formulanet", "text": latex, "confidence": 0.99},
                    {"engine": "texteller", "text": latex, "confidence": None},
                ]
            )
            self.assertEqual(result["status"], "needs_vlm")
        geometry = resolve_formula_consensus(
            [
                {"engine": "pp_formulanet", "text": "F=ma", "confidence": 0.99},
                {"engine": "texteller", "text": "F=ma", "confidence": None},
            ],
            geometry_issues=["content_occupancy_too_low"],
        )
        self.assertEqual(geometry["status"], "needs_vlm")
        self.assertIn("render_geometry_risk", geometry["risk_flags"])

    def test_risky_formula_requires_multi_engine_review(self) -> None:
        result = assess_formula_candidate(r"F_{合}=m\vec{a}", 0.98, engine="texteller")
        self.assertEqual(result["status"], "machine_candidate")
        self.assertIn("chinese_formula_text", result["risk_flags"])
        self.assertIn("vector_notation", result["risk_flags"])
        task = make_formula_review_task("f1", "formula.png", result, source_sha256="a" * 64)
        self.assertEqual(task["required_engines"], ["pp_formulanet", "texteller"])
        self.assertEqual(task["source_sha256"], "a" * 64)
        self.assertIn("visual_evidence", task["expected_result_schema"])

    def test_empty_or_very_low_score_candidate_abstains(self) -> None:
        self.assertEqual(assess_formula_candidate("", 0.0, engine="texteller")["status"], "machine_abstain")

    def test_surya_specific_symbols_require_marker_engine(self) -> None:
        latex = r"t_{\perp}=\frac{v_0}{a}"
        marker, changed, _ = fix_garbled_subscripts(latex, engine="marker")
        self.assertTrue(changed)
        self.assertIn(r"\text{上}", marker)


if __name__ == "__main__":
    unittest.main()
