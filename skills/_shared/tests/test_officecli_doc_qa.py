from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import officecli_doc_qa as qa  # noqa: E402


class OfficeCliPolicyTests(unittest.TestCase):
    def run_policy(self, policy: str, validate_payload: dict, validate_returncode: int) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docx = root / "source.docx"
            docx.write_bytes(b"PK synthetic")
            out = root / "qa"
            out.mkdir()

            def fake_run(_exe: Path, args: list[str], *, timeout: int = 90) -> dict:
                del timeout
                if args[0] == "validate":
                    return {
                        "cmd": args,
                        "returncode": validate_returncode,
                        "stdout": json.dumps(validate_payload),
                        "stderr": "",
                    }
                if "screenshot" in args:
                    Path(args[args.index("-o") + 1]).write_bytes(b"png")
                stdout = "{}" if "--json" in args else "stats"
                return {"cmd": args, "returncode": 0, "stdout": stdout, "stderr": ""}

            detected = {
                "available": True,
                "selected": {"path": str(root / "officecli.exe")},
                "checks": [],
            }
            with patch.object(qa, "detect_officecli", return_value=detected), patch.object(
                qa, "run_officecli", side_effect=fake_run
            ):
                return qa.run_qa(docx, None, out, version="v1.0.135", policy=policy)

    def test_source_audit_does_not_block_legacy_style_order_warning(self) -> None:
        result = self.run_policy(
            "source-audit",
            {"issues": [{"severity": "warning", "message": "w:uiPriority elements are out of order"}]},
            1,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["policy"], "source-audit")
        self.assertEqual(result["blocking_issues"], [])
        self.assertTrue(result["advisories"])

    def test_source_audit_blocks_missing_media_relationship(self) -> None:
        result = self.run_policy(
            "source-audit",
            {"issues": [{"severity": "error", "message": "Missing image relationship rId9 target media/image1.png"}]},
            1,
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result["blocking_issues"])

    def test_final_policy_requires_clean_validation(self) -> None:
        result = self.run_policy(
            "final",
            {"issues": [{"severity": "warning", "message": "w:uiPriority elements are out of order"}]},
            1,
        )
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
