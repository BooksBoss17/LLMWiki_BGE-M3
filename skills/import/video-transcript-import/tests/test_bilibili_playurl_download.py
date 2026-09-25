from __future__ import annotations

import sys
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bilibili_playurl_download as downloader  # noqa: E402


class NetworkPolicyTests(unittest.TestCase):
    def test_direct_mode_disables_proxy_for_api_and_curl(self) -> None:
        network = downloader.resolve_network_context("direct")
        self.assertEqual(downloader.urllib_proxy_mapping(network), {})
        self.assertEqual(downloader.curl_network_args(network), ["--noproxy", "*"])

    def test_proxy_mode_uses_same_proxy_for_api_and_curl(self) -> None:
        with mock.patch.object(downloader, "system_proxy_url", return_value="http://user:secret@127.0.0.1:10809"):
            network = downloader.resolve_network_context("system-proxy")
        self.assertEqual(
            downloader.urllib_proxy_mapping(network),
            {"http": network["proxy_url"], "https": network["proxy_url"]},
        )
        self.assertEqual(downloader.curl_network_args(network), ["--proxy", network["proxy_url"]])

    def test_direct_fallback_switches_whole_network_context(self) -> None:
        with mock.patch.object(downloader, "system_proxy_url", return_value="http://127.0.0.1:10809"):
            contexts = downloader.network_mode_sequence("direct", "system-proxy")
        self.assertEqual([item["mode"] for item in contexts], ["direct", "system-proxy"])
        self.assertIsNone(contexts[0]["proxy_url"])
        self.assertEqual(contexts[1]["proxy_url"], "http://127.0.0.1:10809")

    def test_proxy_credentials_and_signed_query_are_redacted(self) -> None:
        proxy = "http://user:secret@127.0.0.1:10809"
        text = f"proxy={proxy} media=https://cdn.example/video.m4s?deadline=1&token=secret"
        cleaned = downloader.sanitize_status_text(text, proxy)
        self.assertNotIn("user", cleaned)
        self.assertNotIn("secret", cleaned)
        self.assertNotIn("deadline", cleaned)
        self.assertIn("127.0.0.1:10809", cleaned)

    def test_default_media_download_has_no_max_time_and_forces_direct(self) -> None:
        captured = {}

        def fake_run(cmd, timeout=None):
            captured["cmd"] = cmd
            captured["timeout"] = timeout
            output = Path(cmd[cmd.index("-o") + 1])
            output.write_bytes(b"x" * 2048)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp, mock.patch.object(downloader, "run", side_effect=fake_run):
            ok, _ = downloader.curl_download(
                ["https://cdn.example/media.m4s?token=secret"], Path(temp) / "media.m4s", "BV1",
                {"mode": "direct", "proxy_url": None}, deadline=None,
            )
        self.assertTrue(ok)
        self.assertIn("--noproxy", captured["cmd"])
        self.assertNotIn("--proxy", captured["cmd"])
        self.assertNotIn("--max-time", captured["cmd"])
        self.assertIsNone(captured["timeout"])


class CdnProbeTests(unittest.TestCase):
    def test_fastest_valid_cdn_is_selected(self) -> None:
        def fake_probe(url: str, **kwargs):
            speed = {"slow.example": 1_000_000, "fast.example": 7_000_000}[url.split("/")[2]]
            return {
                "host": url.split("/")[2], "remote_ip": "1.2.3.4", "url_index": kwargs["url_index"],
                "http_status": 206, "bytes": 2_097_152, "elapsed_sec": 1.0,
                "speed_bytes_sec": speed, "range_valid": True, "ok": True, "selected": False,
            }

        with tempfile.TemporaryDirectory() as temp, mock.patch.object(downloader, "probe_cdn_url", side_effect=fake_probe):
            urls, records = downloader.probe_stream_urls(
                ["https://slow.example/a?token=1", "https://fast.example/a?token=2"],
                bvid="BV1", network={"mode": "direct", "proxy_url": None}, work=Path(temp),
                requested_bytes=2_097_152, probe_timeout_sec=8, deadline=None,
            )
        self.assertIn("fast.example", urls[0])
        self.assertEqual([item["host"] for item in records if item["selected"]], ["fast.example"])
        self.assertTrue(all(key not in {"url", "signed_url"} for item in records for key in item))

    def test_partial_and_no_range_responses_are_rejected(self) -> None:
        self.assertFalse(downloader.probe_response_is_valid(200, None, 2_097_152, 2_097_152))
        self.assertFalse(
            downloader.probe_response_is_valid(
                206, "Content-Range: bytes 0-2097151/9999999", 1_000_000, 2_097_152
            )
        )
        self.assertTrue(
            downloader.probe_response_is_valid(
                206, "Content-Range: bytes 0-2097151/9999999", 2_097_152, 2_097_152
            )
        )

    def test_all_probe_failures_refresh_playurl_once(self) -> None:
        probes = [{"host": "bad.example", "ok": False, "selected": False}]
        with mock.patch.object(downloader, "fetch_playurl", return_value={"dash": {"video": [{"id": 1}], "audio": [{"id": 2, "bandwidth": 128000}]}}) as fetch, mock.patch.object(
            downloader, "download_checked", side_effect=downloader.CdnProbeError("video", probes)
        ):
            with tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(downloader.CdnProbeError):
                    downloader.try_download_profile(
                        {"bvid": "BV1", "cid": 1, "duration_sec": 10}, Path(temp), Path(temp), "UP",
                        "standard", 96000, False, {"mode": "direct", "proxy_url": None}, 2_097_152, 8,
                    )
        self.assertEqual(fetch.call_count, 2)


class DeadlineAndStagingTests(unittest.TestCase):
    def test_default_has_no_total_deadline(self) -> None:
        self.assertEqual(downloader.DEFAULT_PER_VIDEO_TIMEOUT_SEC, 0)
        self.assertIsNone(downloader.remaining_seconds(None, "media_download"))

    def test_expired_deadline_reports_exact_stage(self) -> None:
        with self.assertRaises(downloader.PerVideoDeadlineExceeded) as caught:
            downloader.remaining_seconds(time.monotonic() - 1, "merge")
        self.assertEqual(caught.exception.stage, "merge")

    def test_deadline_limited_subprocess_reports_exact_stage(self) -> None:
        with mock.patch.object(downloader, "run", side_effect=subprocess.TimeoutExpired(["ffmpeg"], 0.1)):
            with self.assertRaises(downloader.PerVideoDeadlineExceeded) as caught:
                downloader.run_for_stage(
                    ["ffmpeg"], deadline=time.monotonic() + 0.1, stage="final_audio_decode", cap=900
                )
        self.assertEqual(caught.exception.stage, "final_audio_decode")

    def test_ascii_staging_agent_platform_matrix(self) -> None:
        ascii_path = Path("C:/kb/work")
        chinese_path = Path("C:/知识库/work")
        self.assertTrue(downloader.should_use_ascii_staging("on", ascii_path, ascii_path, platform_name="posix", codex_runtime=False))
        self.assertFalse(downloader.should_use_ascii_staging("auto", chinese_path, chinese_path, platform_name="posix", codex_runtime=True))
        self.assertTrue(downloader.should_use_ascii_staging("auto", chinese_path, chinese_path, platform_name="nt", codex_runtime=True))
        self.assertFalse(downloader.should_use_ascii_staging("auto", chinese_path, chinese_path, platform_name="nt", codex_runtime=False))
        self.assertFalse(downloader.should_use_ascii_staging("off", chinese_path, chinese_path, platform_name="nt", codex_runtime=True))

    def test_network_fallback_clears_only_current_bvid_intermediate_media(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            current = [work / "BV1_video.m4s", work / "BV1_lowrate_audio.m4s"]
            other = work / "BV2_video.m4s"
            for path in [*current, other]:
                path.write_bytes(b"stream")
            removed = downloader.clear_bvid_intermediate_media(work, "BV1")
            self.assertEqual(set(removed), {"BV1_video.m4s", "BV1_lowrate_audio.m4s"})
            self.assertFalse(any(path.exists() for path in current))
            self.assertTrue(other.exists())


if __name__ == "__main__":
    unittest.main()
