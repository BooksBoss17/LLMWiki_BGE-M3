from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from combine_image_pdf_ocr import (  # noqa: E402
    build_transcriber_command,
    choose_candidate,
    collect_local_candidates,
    collect_marker_candidates,
    collect_structured_result_candidates,
    marker_candidates_for_page,
)


class CombinedImagePdfOcrTests(unittest.TestCase):
    def test_structured_texteller_extracts_latex_from_api_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "combined_result.json").write_text(
                json.dumps(
                    {
                        "engines": [
                            {
                                "engine": "formula",
                                "parsed": {
                                    "steps": [
                                        {
                                            "name": "texteller_whole_page",
                                            "ok": True,
                                            "data": {
                                                "exit_code": 0,
                                                "stdout_tail": "Predicted LaTeX: ```E=mc^2```",
                                                "stderr_tail": "",
                                            },
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            candidates = collect_structured_result_candidates(root, 1)

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].engine, "texteller")
            self.assertEqual(candidates[0].text, "E=mc^2")
            self.assertNotIn("stdout_tail", candidates[0].text)

    def test_structured_combined_result_precedes_legacy_file_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            combined = {
                "engines": [
                    {
                        "engine": "paddle",
                        "parsed": {
                            "steps": [
                                {
                                    "name": "paddleocr_vl_1_6_page",
                                    "ok": True,
                                    "data": [
                                        {
                                            "res": {
                                                "parsing_res_list": [
                                                    {"block_order": 2, "block_label": "text", "block_content": "第二段"},
                                                    {"block_order": 1, "block_label": "title", "block_content": "标题"},
                                                ]
                                            }
                                        }
                                    ],
                                },
                                {
                                    "name": "paddlex_ocr_page",
                                    "ok": True,
                                    "data": [{"res": {"rec_texts": ["甲", "乙"], "rec_scores": [0.9, 0.8]}}],
                                },
                                {
                                    "name": "pp_formulanet_plus_l_whole_page",
                                    "ok": True,
                                    "data": [{"res": {"rec_formula": "x^2"}}],
                                },
                            ]
                        },
                    },
                    {
                        "engine": "formula",
                        "parsed": {"steps": [{"name": "texteller_whole_page", "ok": True, "data": {"latex": "x^{2}"}}]},
                    },
                ]
            }
            (root / "combined_result.json").write_text(json.dumps(combined, ensure_ascii=False), encoding="utf-8")
            legacy = root / "paddle" / "paddleocr_vl"
            legacy.mkdir(parents=True)
            (legacy / "legacy.md").write_text("旧目录内容不应覆盖结构化结果", encoding="utf-8")

            candidates = collect_local_candidates(root, 1)
            engines = [item.engine for item in candidates]
            self.assertEqual(engines.count("paddleocr_vl"), 1)
            self.assertIn("pp_ocr", engines)
            self.assertIn("pp_formulanet", engines)
            self.assertIn("texteller", engines)
            vl = next(item for item in candidates if item.engine == "paddleocr_vl")
            self.assertEqual(vl.text, "标题\n\n第二段")
            self.assertEqual(vl.metadata["source"], "combined_result_json")

    def test_multi_page_marker_is_evidence_for_each_page_not_page_one_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "candidate.md").write_text("整卷 marker 候选", encoding="utf-8")
            candidates, _ = collect_marker_candidates(root, [1, 2])
            self.assertIsNone(candidates[0].page)
            self.assertEqual(len(marker_candidates_for_page(candidates, 2)), 1)
            self.assertIsNone(choose_candidate(2, candidates))

    def test_configurable_transcriber_command_is_platform_neutral(self) -> None:
        command = build_transcriber_command(
            "task_001",
            Path("transcriber"),
            "runner --task {task_id}",
        )
        self.assertEqual(command, ["runner", "--task", "task_001"])


if __name__ == "__main__":
    unittest.main()
