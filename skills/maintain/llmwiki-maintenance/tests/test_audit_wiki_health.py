from __future__ import annotations

import sys
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ROOT / "skills" / "maintain" / "llmwiki-maintenance" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import audit_wiki_health  # noqa: E402


class WikiVideoParityTests(unittest.TestCase):
    def _write_page(self, path: Path, title: str, bvid: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'---\ntitle: "{title}"\nbvid: "{bvid}"\ntype: video_transcript\n---\n\n# {title}\n',
            encoding="utf-8",
        )

    def test_reports_complete_raw_video_without_wiki_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            kb_root = Path(temporary)
            raw_dir = kb_root / "raw" / "transcripts" / "Provider" / "Topic"
            self._write_page(raw_dir / "Topic.md", "Topic", "BV-missing-wiki")
            self._write_page(raw_dir / "Topic_知识笔记.md", "Topic knowledge", "BV-missing-wiki")
            self._write_page(raw_dir / "Topic_教学简案.md", "Topic lesson", "BV-missing-wiki")
            (kb_root / "LLMWiki" / "concepts").mkdir(parents=True)

            report = audit_wiki_health.build_report(kb_root, ["concepts"], 20_000)

            self.assertEqual(report["video_raw_without_wiki_count"], 1)
            self.assertEqual(report["video_raw_without_wiki"][0]["bvid"], "BV-missing-wiki")

    def test_reports_incomplete_raw_triplet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            kb_root = Path(temporary)
            raw_dir = kb_root / "raw" / "transcripts" / "Provider" / "Topic"
            self._write_page(raw_dir / "Topic.md", "Topic", "BV-incomplete-raw")
            wiki_dir = kb_root / "LLMWiki" / "concepts"
            self._write_page(wiki_dir / "视频-Topic.md", "Topic", "BV-incomplete-raw")
            self._write_page(wiki_dir / "视频-Topic-知识笔记.md", "Topic knowledge", "BV-incomplete-raw")
            self._write_page(wiki_dir / "视频-Topic-教学简案.md", "Topic lesson", "BV-incomplete-raw")

            report = audit_wiki_health.build_report(kb_root, ["concepts"], 20_000)

            self.assertEqual(report["video_raw_triplet_incomplete_count"], 1)
            self.assertEqual(
                report["video_raw_triplet_incomplete"][0]["missing"],
                ["knowledge", "lesson"],
            )

    def test_reports_wiki_video_without_raw_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            kb_root = Path(temporary)
            wiki_dir = kb_root / "LLMWiki" / "concepts"
            self._write_page(wiki_dir / "视频-Orphan.md", "Orphan", "BV-wiki-only")
            self._write_page(wiki_dir / "视频-Orphan-知识笔记.md", "Orphan knowledge", "BV-wiki-only")
            self._write_page(wiki_dir / "视频-Orphan-教学简案.md", "Orphan lesson", "BV-wiki-only")

            report = audit_wiki_health.build_report(kb_root, ["concepts"], 20_000)

            self.assertEqual(report["video_wiki_without_raw_count"], 1)
            self.assertEqual(report["video_wiki_without_raw"][0]["bvid"], "BV-wiki-only")

    def test_matches_duplicate_titles_by_bvid_with_disambiguated_wiki_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            kb_root = Path(temporary)
            wiki_dir = kb_root / "LLMWiki" / "concepts"
            for provider, bvid in (("ProviderA", "BV-duplicate-a"), ("ProviderB", "BV-duplicate-b")):
                raw_dir = kb_root / "raw" / "transcripts" / provider / "SharedTopic"
                self._write_page(raw_dir / "SharedTopic.md", "SharedTopic", bvid)
                self._write_page(raw_dir / "SharedTopic_知识笔记.md", "SharedTopic knowledge", bvid)
                self._write_page(raw_dir / "SharedTopic_教学简案.md", "SharedTopic lesson", bvid)
                wiki_base = f"视频-SharedTopic-{provider}"
                self._write_page(wiki_dir / f"{wiki_base}.md", "SharedTopic", bvid)
                self._write_page(wiki_dir / f"{wiki_base}-知识笔记.md", "SharedTopic knowledge", bvid)
                self._write_page(wiki_dir / f"{wiki_base}-教学简案.md", "SharedTopic lesson", bvid)

            report = audit_wiki_health.build_report(kb_root, ["concepts"], 20_000)

            self.assertEqual(report["video_raw_without_wiki_count"], 0)
            self.assertEqual(report["video_wiki_without_raw_count"], 0)
            self.assertEqual(report["video_raw_triplet_incomplete_count"], 0)

    def test_cli_fail_on_issues_includes_cross_library_video_parity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            kb_root = Path(temporary)
            raw_dir = kb_root / "raw" / "transcripts" / "Provider" / "Topic"
            self._write_page(raw_dir / "Topic.md", "Topic", "BV-cli-missing-wiki")
            self._write_page(raw_dir / "Topic_知识笔记.md", "Topic knowledge", "BV-cli-missing-wiki")
            self._write_page(raw_dir / "Topic_教学简案.md", "Topic lesson", "BV-cli-missing-wiki")
            (kb_root / "LLMWiki" / "concepts").mkdir(parents=True)
            report_path = kb_root / "report.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "audit_wiki_health.py"),
                    "--kb-root",
                    str(kb_root),
                    "--out",
                    str(report_path),
                    "--fail-on-issues",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
