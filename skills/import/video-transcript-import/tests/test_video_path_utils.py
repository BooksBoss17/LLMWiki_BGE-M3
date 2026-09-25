from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from video_path_utils import normalize_queue_data, normalize_storage_title  # noqa: E402


class VideoPathUtilsTests(unittest.TestCase):
    def test_storage_title_normalizes_windows_edge_cases(self) -> None:
        self.assertEqual(normalize_storage_title('核能:裂变/聚变?*<>|"'), "核能-裂变-聚变")
        self.assertEqual(normalize_storage_title("line\x00\x1fbreak... "), "line-break")
        self.assertEqual(normalize_storage_title("CON"), "_CON")
        self.assertEqual(normalize_storage_title("nul.txt"), "_nul.txt")
        self.assertEqual(normalize_storage_title("   ", fallback="BV123"), "BV123")

    def test_object_and_array_queue_are_normalized_to_arrays(self) -> None:
        one = normalize_queue_data({"bvid": "BV1", "title": "A:B"})
        many = normalize_queue_data([{"bvid": "BV1", "title": "A:B"}])
        self.assertEqual(one, many)
        self.assertEqual(one[0]["original_title"], "A:B")
        self.assertEqual(one[0]["storage_title"], "A-B")
        self.assertEqual(one[0]["import_title"], "A-B")
        self.assertEqual(one[0]["wiki_title"], "A-B")

    def test_cli_always_emits_json_array(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.json"
            output = root / "output.json"
            source.write_text(json.dumps({"bvid": "BV1", "title": "A/B"}), encoding="utf-8")
            process = subprocess.run(
                [sys.executable, str(SCRIPTS / "normalize_video_queue.py"), "--input", str(source), "--output", str(output)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIsInstance(json.loads(output.read_text(encoding="utf-8")), list)


if __name__ == "__main__":
    unittest.main()
