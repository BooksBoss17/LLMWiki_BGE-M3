from __future__ import annotations

import hashlib
import base64
import json
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch


SHARED_SCRIPTS = Path(__file__).resolve().parents[3] / "_shared" / "scripts"
sys.path.insert(0, str(SHARED_SCRIPTS))

from libreoffice_runner import (  # noqa: E402
    LibreOfficeRenderError,
    _crop_libreoffice_svg_to_source_bounds,
    _filter_wmf_non_drawing_comments,
    _run_batik_wmf_batch,
    _run_native_wmf_task,
    _run_native_wmf_batches,
    render_vector_full_frame,
    render_vectors_full_frame,
    select_soffice,
    select_powershell,
)


def write_png(path: Path, width: int, height: int) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    scanline = b"\x00" + (b"\xff\xff\xff\xff" * width)
    payload = b"\x89PNG\r\n\x1a\n"
    payload += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    payload += chunk(b"IDAT", zlib.compress(scanline * height))
    payload += chunk(b"IEND", b"")
    path.write_bytes(payload)


def write_placeable_wmf(path: Path, width: int, height: int) -> None:
    # The renderer adapter in this test does not parse WMF records, but the
    # public runner must preserve and report the placeable logical bounds.
    path.write_bytes(
        struct.pack("<IHhhhhHIH", 0x9AC6CDD7, 0, 0, 0, width, height, 1440, 0, 0)
        + b"synthetic-wmf-records"
    )


class LibreOfficeRunnerTests(unittest.TestCase):
    def test_powershell_discovery_prefers_modern_pwsh(self) -> None:
        def fake_which(name: str) -> str | None:
            return {
                "pwsh.exe": r"C:\Program Files\PowerShell\7\pwsh.exe",
                "powershell.exe": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            }.get(name)

        with patch("libreoffice_runner.shutil.which", side_effect=fake_which):
            self.assertEqual(select_powershell(), r"C:\Program Files\PowerShell\7\pwsh.exe")

    WMF_FORMULA_FIXTURE = (
        "183GmgAAAAAAAAADAAICCQAAAAATXwEACQAAAz0BAAACAJcAAAAAAAUAAAACAQEAAAAFAAAAAQL///8ABQAAAC4BGQAAAAUAAAALAgAAAAAFAAAADAIAAgADCwAAACYGDwAMAE1hdGhUeXBlAABQABIAAAAmBg8AGgD/////AAAQAAAAwP///8b////AAgAAxgEAAAUAAAAJAgAAAAIFAAAAFAJgAUwAHAAAAPsCgP4AAAAAAACQAQEAAAAAAgAQVGltZXMgTmV3IFJvbWFuANzXGACAk8t2gAHPdsAYZs0EAAAALQEAAAwAAAAyCgAAAAADAAAAeE95AKgAFAEAA5cAAAAmBg8AIwFBcHBzTUZDQwEA/AAAAC4AAABBcHBzLVFCTUEAAQsAIMrgEJTUsIi+2iEvFskruPCGciQMThMWlPgNDT9ZSBwAb2RlUGFnZXMAEQVUaW1lcyBOZXcgUm9tYW4AEQNTeW1ib2wAEQVDb3VyaWVyIE5ldwARBE1UIEV4dHJhABNXaW5BbGxDb2RlUGFnZXMAEQbLzszlABIACCEvRY9EL0FQ9BAPR19BUPIfHkFQ9BUPQQD0RfQl9I9CX0EA9BAPQ19BAPSPRfQqX0j0j0EA9BAPQPSPQX9I9BAPQSpfRF9F9F9F9F9BDwwBAAEAAQICAgIAAgABAQEAAwABAAQABQAKAQAQBAAAAAAAAEJsYWNrAA8BAgCDeAACAINPAAIAg3kAAAANCgAAACYGDwAKAP////8BAAAAAAAcAAAA+wIQAAcAAAAAALwCAAAAhgECAiJTeXN0ZW0AAMAYZs0AAAoAOACKAQAAAAD/////DOIYAAQAAAAtAQEABAAAAPABAAADAAAAAAA="
    )

    def test_windows_discovery_prefers_command_line_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            program = Path(directory)
            (program / "soffice.exe").write_bytes(b"exe")
            (program / "soffice.com").write_bytes(b"com")

            selected = select_soffice(platform_name="win32", program_dir=program)

            self.assertEqual(selected, (program / "soffice.com").resolve())

    def test_windows_discovery_prefers_portable_runtime_before_system_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            portable = Path(directory) / "portable" / "soffice.com"
            portable.parent.mkdir(parents=True)
            portable.write_bytes(b"portable")

            with patch("libreoffice_runner.portable_soffice", return_value=portable):
                selected = select_soffice(platform_name="win32")

            self.assertEqual(selected, portable.resolve())

    def test_batik_batch_uses_argfile_instead_of_long_windows_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "中文 长路径"
            root.mkdir(parents=True)
            destinations = [root / f"render-{index:03d}.png" for index in range(64)]
            jobs = [
                (
                    root / f"source-{index:03d}.wmf",
                    destination,
                    {"width": 320, "height": 80, "units_per_inch": 1440},
                )
                for index, destination in enumerate(destinations)
            ]
            observed: dict[str, object] = {}

            class FakeProcess:
                returncode = 0

                def __init__(self, command, **_kwargs):
                    observed["command"] = command
                    argfile = Path(command[-1])
                    observed["argfile"] = argfile.read_text(encoding="utf-8")

                def communicate(self, timeout=None):
                    observed["timeout"] = timeout
                    for destination in destinations:
                        destination.write_bytes(b"png")
                    return "", ""

            with patch("libreoffice_runner.subprocess.Popen", FakeProcess):
                command, _, _ = _run_batik_wmf_batch(jobs, timeout=45)

            launched = observed["command"]
            self.assertLess(len(subprocess.list2cmdline(launched)), 4096)
            self.assertEqual(launched[-2], "--java-argfile")
            self.assertIn('"org.llmwiki.bemarkdown.BatikBridge"', observed["argfile"])
            self.assertIn("中文 长路径", observed["argfile"])
            self.assertIn("source-063.wmf", observed["argfile"])
            self.assertEqual(observed["timeout"], 45)
            self.assertTrue(command[-1].startswith("sha256:"))

    def test_native_batch_bisects_timeout_poison_item(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = [
                {"source": f"source-{index}", "output": str(root / f"out-{index}.png"), "width": 10, "height": 10}
                for index in range(4)
            ]
            calls: list[list[str]] = []

            def fake_task(chunk, _timeout):
                names = [job["source"] for job in chunk]
                calls.append(names)
                if "source-2" in names:
                    return ["native"], "", "timeout", False
                for job in chunk:
                    Path(job["output"]).write_bytes(b"png")
                return ["native"], "", "", True

            with patch("libreoffice_runner._run_native_wmf_task", side_effect=fake_task):
                results = _run_native_wmf_batches(jobs, timeout=30)

            self.assertIsInstance(results["source-2"], Exception)
            self.assertFalse(Path(jobs[2]["output"]).exists())
            for index in (0, 1, 3):
                self.assertIsInstance(results[f"source-{index}"], tuple)
                self.assertTrue(Path(jobs[index]["output"]).is_file())
            self.assertGreater(len(calls), 1)

    def test_filter_removes_mathtype_comments_and_repairs_wmf_header(self) -> None:
        original = base64.b64decode(self.WMF_FORMULA_FIXTURE)

        filtered, skipped = _filter_wmf_non_drawing_comments(original)

        self.assertGreater(skipped, 0)
        self.assertLess(len(filtered), len(original))
        self.assertEqual(struct.unpack_from("<I", filtered, 28)[0], (len(filtered) - 22) // 2)
        self.assertNotIn(b"AppsMFCC", filtered)

    def test_libreoffice_svg_bbox_crop_rejects_source_aspect_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.svg"
            source.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 21000 29700">'
                '<rect class="BoundingBox" x="100" y="200" width="400" height="200"/>'
                '</svg>',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(LibreOfficeRenderError, "aspect mismatch"):
                _crop_libreoffice_svg_to_source_bounds(
                    source,
                    root / "cropped.svg",
                    {"width": 100, "height": 100, "aspect": 1.0, "units_per_inch": 1440},
                )
    def test_full_frame_render_preserves_source_and_reports_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "formula.wmf"
            output = root / "out"
            adapter = root / "fake_soffice.py"
            write_placeable_wmf(source, width=320, height=80)
            original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            adapter.write_text(
                textwrap.dedent(
                    """
                    import json
                    import struct
                    import sys
                    import zlib
                    from pathlib import Path

                    def chunk(kind, payload):
                        body = kind + payload
                        return struct.pack('>I', len(payload)) + body + struct.pack('>I', zlib.crc32(body) & 0xffffffff)

                    args = sys.argv[1:]
                    out = Path(args[args.index('--outdir') + 1])
                    source = Path(args[-1])
                    out.mkdir(parents=True, exist_ok=True)
                    width, height = 320, 80
                    first = b'\\x00' + (b'\\x00\\x00\\x00\\xff' * 20) + (b'\\xff\\xff\\xff\\xff' * (width - 20))
                    scanline = b'\\x00' + (b'\\xff\\xff\\xff\\xff' * width)
                    png = b'\\x89PNG\\r\\n\\x1a\\n'
                    png += chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
                    png += chunk(b'IDAT', zlib.compress((first * 10) + (scanline * (height - 10))))
                    png += chunk(b'IEND', b'')
                    (out / (source.stem + '.png')).write_bytes(png)
                    print(json.dumps({'ok': True}))
                    """
                ),
                encoding="utf-8",
            )

            result = render_vector_full_frame(
                source,
                output,
                output_name="formula-full.png",
                command=[sys.executable, str(adapter)],
                timeout=10,
            )

            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original_hash)
            self.assertEqual(result["source_sha256"], original_hash)
            self.assertEqual(result["source_bounds"]["width"], 320)
            self.assertEqual(result["source_bounds"]["height"], 80)
            self.assertEqual(result["render_geometry"]["width"], 320)
            self.assertEqual(result["render_geometry"]["height"], 80)
            self.assertEqual(result["render_geometry"]["aspect_delta"], 0.0)
            self.assertFalse(result["cropped"])
            self.assertFalse(result["resized"])
            self.assertTrue(result["full_resolution"])
            self.assertEqual(result["geometry_issues"], [])
            self.assertTrue(Path(result["output_path"]).is_file())

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows process-tree cleanup regression")
    def test_timeout_leaves_no_adapter_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "formula.wmf"
            adapter = root / "slow_adapter.py"
            pid_file = root / "pid.txt"
            write_placeable_wmf(source, width=320, height=80)
            adapter.write_text(
                "import os,time\n"
                f"open({str(pid_file)!r},'w').write(str(os.getpid()))\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            with self.assertRaises(LibreOfficeRenderError):
                render_vector_full_frame(
                    source,
                    root / "out",
                    command=[sys.executable, str(adapter)],
                    timeout=1,
                )
            pid = pid_file.read_text(encoding="utf-8").strip()
            tasklist = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )
            self.assertNotIn(f'"{pid}"', tasklist.stdout)

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows native WMF renderer regression")
    def test_real_wmf_uses_native_bounds_instead_of_a4_canvas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "formula.wmf"
            source.write_bytes(base64.b64decode(self.WMF_FORMULA_FIXTURE))

            result = render_vector_full_frame(source, root / "out", timeout=20)

            self.assertEqual(result["source_bounds"]["aspect"], 1.5)
            self.assertLessEqual(result["render_geometry"]["aspect_delta"], 0.01)
            self.assertGreaterEqual(result["render_geometry"]["width"], 300)
            self.assertNotIn("source_render_aspect_mismatch", result["geometry_issues"])

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows native WMF renderer regression")
    def test_real_mathtype_wmf_batch_uses_filtered_native_gdi_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [root / "formula-a.wmf", root / "formula-b.wmf"]
            for source in sources:
                source.write_bytes(base64.b64decode(self.WMF_FORMULA_FIXTURE))
            results = render_vectors_full_frame(sources, root / "out", timeout=30)
            for source in sources:
                result = results[str(source.resolve())]
                self.assertIsInstance(result, dict)
                self.assertEqual(result["renderer_adapter"], "windows_pillow_gdi_filtered")
                self.assertIsNone(result["renderer_fallback_reason"])
                self.assertEqual(result["geometry_issues"], [])
                self.assertLessEqual(result["render_geometry"]["aspect_delta"], 0.01)

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows native WMF renderer regression")
    def test_native_adapter_completes_filtered_mathtype_wmf_without_hanging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "formula.wmf"
            output = root / "formula.png"
            source.write_bytes(base64.b64decode(self.WMF_FORMULA_FIXTURE))

            _command, _stdout, stderr, success = _run_native_wmf_task(
                [{"source": str(source), "output": str(output), "width": 360, "height": 240}],
                45,
            )

            self.assertTrue(success, stderr)
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
