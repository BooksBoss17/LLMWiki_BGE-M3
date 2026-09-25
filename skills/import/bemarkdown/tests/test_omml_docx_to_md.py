from __future__ import annotations

import sys
import unittest
from pathlib import Path

from lxml import etree


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from omml_docx_to_md import get_text_from_run, replace_image_placeholders  # noqa: E402


class ImagePlaceholderTests(unittest.TestCase):
    def test_repeated_placeholders_in_one_paragraph_are_all_preserved(self) -> None:
        source = "A __IMG__rId8__表示时刻，称为第__IMG__rId10__末或第__IMG__rId12__初。"
        result = replace_image_placeholders(
            source,
            {"rId8": "image_0001.wmf", "rId10": "image_0002.wmf", "rId12": "image_0003.wmf"},
        )
        self.assertEqual(result.count("![图](media/"), 3)
        self.assertIn("image_0002.wmf", result)
        self.assertIn("image_0003.wmf", result)

    def test_italic_punctuation_is_not_emitted_as_markdown_emphasis(self) -> None:
        run = etree.fromstring(
            '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:rPr><w:i/></w:rPr><w:t>-</w:t></w:r>'
        )
        self.assertEqual(get_text_from_run(run), "-")


if __name__ == "__main__":
    unittest.main()
