from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from check_and_fix import (  # noqa: E402
    apply_reviewed_replacements,
    apply_safe_fixes,
    repair_tasks,
    scan_file,
)


class TextbookCheckAndFixTests(unittest.TestCase):
    def test_safe_fix_never_deletes_duplicate_or_garbled_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "chapter.md"
            repeated = "物理规律重复文本" * 5
            path.write_text(f"# 教材\n\n## 第一节\n\n{repeated}\n\n## 第一节\n", encoding="utf-8")
            before = path.read_text(encoding="utf-8")
            apply_safe_fixes(path)
            after = path.read_text(encoding="utf-8")
            self.assertIn(repeated, after)
            self.assertEqual(after.count("## 第一节"), 2)
            self.assertNotIn("待人工校对", after)
            issues = scan_file(path)
            self.assertIn("repeated_ocr_noise", {item["kind"] for item in issues})
            self.assertIn("duplicate_h2", {item["kind"] for item in issues})
            self.assertGreaterEqual(len(before), len(repeated))

    def test_reviewed_replacements_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "chapter.md"
            path.write_text("# 教材\n\n## 第1章\n\n猪论\n", encoding="utf-8")
            apply_safe_fixes(path)
            self.assertIn("猪论", path.read_text(encoding="utf-8"))
            changes = apply_reviewed_replacements(path, {"猪论": "绪论"}, {"1": "运动的描述"})
            self.assertEqual(changes, 2)
            text = path.read_text(encoding="utf-8")
            self.assertIn("绪论", text)
            self.assertIn("## 第1章 运动的描述", text)

    def test_repair_tasks_remain_unresolved(self) -> None:
        issues = [{"path": "chapter.md", "line": 8, "kind": "placeholder", "text": "OCR乱码"}]
        tasks = repair_tasks(issues)
        self.assertEqual(tasks[0]["status"], "unresolved")
        self.assertIn("placeholders cannot pass", tasks[0]["recommended_action"])
        json.dumps(tasks, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
