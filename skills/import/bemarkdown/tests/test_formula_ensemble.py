from __future__ import annotations

import hashlib
import struct
import subprocess
import sys
import tempfile
import types
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from PIL import Image


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from formula_ensemble import (  # noqa: E402
    PADDLE_BATCH_SIZE,
    PP_FORMULANET_MODEL_DIR,
    TEXTELLER_BATCH_SIZE,
    TEXTELLER_MODEL_DIR,
    _construct_formula_model,
    _run_texteller,
    _run_texteller_batch,
    _run_stream_worker,
    _run_subprocess,
    _texteller_model,
    append_jsonl,
    load_stream_records,
    merge_engine_reports,
    run_formula_ensemble,
    worker_formula_stream,
    worker_paddle_stream,
)


def write_png(path: Path) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"\x00\xff\xff\xff\xff" * 8
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 8, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class FormulaEnsembleTests(unittest.TestCase):
    def test_gpu_microbatch_sizes_match_verified_runtime_benchmarks(self) -> None:
        self.assertEqual(PADDLE_BATCH_SIZE, 16)
        self.assertEqual(TEXTELLER_BATCH_SIZE, 32)

    def test_paddle_uses_bundled_model_dir_instead_of_model_name_download(self) -> None:
        captured: dict[str, object] = {}

        class FakeFormulaRecognition:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        paddleocr = types.ModuleType("paddleocr")
        paddleocr.FormulaRecognition = FakeFormulaRecognition
        with patch.dict(sys.modules, {"paddleocr": paddleocr}):
            _construct_formula_model()

        self.assertEqual(captured["model_dir"], str(PP_FORMULANET_MODEL_DIR))
        self.assertEqual(captured["model_name"], "PP-FormulaNet_plus-L")

    def test_texteller_uses_bundled_model_dir_for_model_and_tokenizer(self) -> None:
        calls: list[tuple[str, str]] = []
        package = types.ModuleType("texteller")
        package.__path__ = []
        api = types.ModuleType("texteller.api")
        utils = types.ModuleType("texteller.utils")
        utils.__path__ = []
        image_utils = types.ModuleType("texteller.utils.image")
        image_utils.trim_white_border = lambda image: image
        utils.image = image_utils
        api.load_model = lambda path: calls.append(("model", path)) or object()
        api.load_tokenizer = lambda path: calls.append(("tokenizer", path)) or object()
        api.img2latex = object()
        with patch.dict(
            sys.modules,
            {"texteller": package, "texteller.api": api, "texteller.utils": utils, "texteller.utils.image": image_utils},
        ):
            _texteller_model()

        expected = str(TEXTELLER_MODEL_DIR)
        self.assertEqual(calls, [("model", expected), ("tokenizer", expected)])

    def test_texteller_composites_transparent_wmf_render_on_white_without_resizing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "透明 公式.png"
            rgba = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
            for x in range(5, 35):
                rgba.putpixel((x, 10), (0, 0, 0, 255))
            rgba.save(image)

            def fake_img2latex(*, images, **_kwargs):
                prepared = Image.open(images[0]).convert("RGB")
                self.assertEqual(prepared.size, (40, 20))
                self.assertEqual(prepared.getpixel((0, 0)), (255, 255, 255))
                self.assertEqual(prepared.getpixel((10, 10)), (0, 0, 0))
                return ["x"]

            self.assertEqual(_run_texteller((object(), object(), fake_img2latex), str(image)), "x")

    def test_texteller_microbatch_preserves_input_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "a.png"
            second = Path(directory) / "b.png"
            Image.new("RGB", (20, 10), "white").save(first)
            Image.new("RGB", (20, 10), "white").save(second)
            calls: list[list[str]] = []

            def fake_img2latex(*, images, **_kwargs):
                calls.append(list(images))
                return ["a", "b"]

            result = _run_texteller_batch((object(), object(), fake_img2latex), [str(first), str(second)])

            self.assertEqual(result, ["a", "b"])
            self.assertEqual(len(calls), 1)

    def test_texteller_pads_ultrathin_formula_without_resizing_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "thin.png"
            thin = Image.new("RGB", (1498, 3), "white")
            for x in range(100, 1400):
                thin.putpixel((x, 1), (0, 0, 0))
            thin.save(image)

            def fake_img2latex(*, images, **_kwargs):
                prepared = Image.open(images[0]).convert("RGB")
                self.assertEqual(prepared.size, (1498, 7))
                self.assertEqual(prepared.getpixel((500, 3)), (0, 0, 0))
                return ["x"]

            self.assertEqual(_run_texteller((object(), object(), fake_img2latex), str(image)), "x")

    def test_stream_cache_keeps_latest_complete_hash_bound_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "公式 image.png"
            write_png(image)
            output = root / "engine.jsonl"
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            append_jsonl(output, {"source_image": str(image), "source_sha256": digest, "status": "ok", "text": "x"})
            append_jsonl(output, {"source_image": str(image), "source_sha256": digest, "status": "error", "error": "later crash"})

            records = load_stream_records(output)

            self.assertEqual(records[str(image.resolve())]["text"], "x")

    def test_stream_worker_resumes_only_unfinished_images_after_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.png"
            second = root / "second.png"
            write_png(first)
            write_png(second)
            second.write_bytes(second.read_bytes() + b"distinct-input")
            task = root / "task.json"
            output = root / "engine.jsonl"
            submitted: list[list[str]] = []

            def fake_run(command, **kwargs):
                payload = __import__("json").loads(task.read_text(encoding="utf-8"))
                submitted.append(list(payload["images"]))
                image = Path(payload["images"][0])
                append_jsonl(
                    output,
                    {
                        "source_image": str(image),
                        "source_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                        "status": "ok",
                        "text": image.stem,
                    },
                )
                if len(submitted) == 1:
                    raise subprocess.TimeoutExpired(command, kwargs["timeout"], output="", stderr="timeout")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("formula_ensemble._run_subprocess", side_effect=fake_run):
                interrupted = _run_stream_worker(
                    Path(sys.executable), "worker-paddle-stream", [first, second], task, output, 1
                )
                resumed = _run_stream_worker(
                    Path(sys.executable), "worker-paddle-stream", [first, second], task, output, 1
                )

            self.assertEqual(interrupted["worker_returncode"], 124)
            self.assertEqual(interrupted["pending_count"], 1)
            self.assertTrue(resumed["ok"])
            self.assertEqual(resumed["pending_count"], 0)
            self.assertEqual(submitted[0], [str(first), str(second)])
            self.assertEqual(submitted[1], [str(second)])

    def test_stream_worker_infers_identical_png_bytes_once_and_propagates_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.png"
            second = root / "second.png"
            write_png(first)
            second.write_bytes(first.read_bytes())
            task = root / "task.json"
            output = root / "engine.jsonl"
            submitted: list[list[str]] = []

            def fake_run(command, **_kwargs):
                payload = __import__("json").loads(task.read_text(encoding="utf-8"))
                submitted.append(list(payload["images"]))
                image = Path(payload["images"][0])
                append_jsonl(
                    output,
                    {
                        "source_image": str(image),
                        "source_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                        "status": "ok",
                        "text": "F=ma",
                    },
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("formula_ensemble._run_subprocess", side_effect=fake_run):
                result = _run_stream_worker(
                    Path(sys.executable), "worker-paddle-stream", [first, second], task, output, 1
                )

            self.assertTrue(result["ok"])
            self.assertEqual(submitted, [[str(first)]])
            self.assertEqual(result["unique_hash_count"], 1)
            self.assertEqual(result["deduplicated_count"], 1)
            self.assertEqual(
                [record["source_image"] for record in result["records"]],
                [str(first.resolve()), str(second.resolve())],
            )
            self.assertEqual({record["text"] for record in result["records"]}, {"F=ma"})
            self.assertEqual(result["records"][1]["deduplicated_from"], str(first.resolve()))

    def test_stream_worker_blocks_conflicting_cached_evidence_for_identical_png_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.png"
            second = root / "second.png"
            write_png(first)
            second.write_bytes(first.read_bytes())
            digest = hashlib.sha256(first.read_bytes()).hexdigest()
            output = root / "engine.jsonl"
            append_jsonl(
                output,
                {"source_image": str(first), "source_sha256": digest, "status": "ok", "text": "F=ma"},
            )
            append_jsonl(
                output,
                {"source_image": str(second), "source_sha256": digest, "status": "ok", "text": "F=mv"},
            )

            with self.assertRaisesRegex(RuntimeError, "conflicting completed model evidence"):
                _run_stream_worker(
                    Path(sys.executable),
                    "worker-paddle-stream",
                    [first, second],
                    root / "task.json",
                    output,
                    1,
                )

    def test_paddle_stream_uses_verified_batch_size_and_preserves_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = []
            for index in range(PADDLE_BATCH_SIZE + 1):
                image = root / f"paddle-{index:02d}.png"
                write_png(image)
                image.write_bytes(image.read_bytes() + bytes([index]))
                images.append(str(image))
            task = root / "task.json"
            output = root / "paddle.jsonl"
            task.write_text(__import__("json").dumps({"images": images}), encoding="utf-8")
            calls: list[tuple[list[str], int]] = []

            class FakeModel:
                def predict(self, *, input, batch_size):
                    batch = list(input) if isinstance(input, list) else [input]
                    calls.append((batch, batch_size))
                    return [{"rec_formula": Path(path).stem} for path in batch]

            with patch("formula_ensemble._construct_formula_model", return_value=FakeModel()):
                returncode = worker_paddle_stream(task, output)

            self.assertEqual(returncode, 0)
            self.assertEqual([len(batch) for batch, _ in calls], [PADDLE_BATCH_SIZE, 1])
            self.assertEqual([size for _, size in calls], [PADDLE_BATCH_SIZE, 1])
            records = [__import__("json").loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([record["source_image"] for record in records], images)

    def test_texteller_stream_uses_verified_batch_size_and_preserves_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = []
            for index in range(TEXTELLER_BATCH_SIZE + 1):
                image = root / f"texteller-{index:02d}.png"
                write_png(image)
                image.write_bytes(image.read_bytes() + bytes([index]))
                images.append(str(image))
            task = root / "task.json"
            output = root / "texteller.jsonl"
            task.write_text(__import__("json").dumps({"images": images}), encoding="utf-8")
            calls: list[list[str]] = []

            def fake_batch(_runtime, batch):
                calls.append(list(batch))
                return [Path(path).stem for path in batch]

            with (
                patch("formula_ensemble._texteller_model", return_value=(object(), object(), object())),
                patch("formula_ensemble._run_texteller_batch", side_effect=fake_batch),
            ):
                returncode = worker_formula_stream(task, output)

            self.assertEqual(returncode, 0)
            self.assertEqual([len(batch) for batch in calls], [TEXTELLER_BATCH_SIZE, 1])
            records = [__import__("json").loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([record["source_image"] for record in records], images)

    def test_worker_timeout_kills_the_full_process_tree(self) -> None:
        class FakeProcess:
            returncode = None

            def __init__(self):
                self.calls = 0

            def communicate(self, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired(["python"], timeout, output="partial", stderr="waiting")
                self.returncode = 1
                return "", "terminated"

        process = FakeProcess()
        with (
            patch("formula_ensemble.subprocess.Popen", return_value=process),
            patch("formula_ensemble._kill_process_tree") as kill_tree,
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                _run_subprocess(["python"], timeout=1, env={})

        kill_tree.assert_called_once_with(process)

    def test_formula_engines_run_sequentially_with_one_total_timeout_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "formula.png"
            write_png(image)
            calls: list[tuple[str, int]] = []

            def fake_stream(_python, mode, images, _task, _output, timeout):
                calls.append((mode, timeout))
                if mode == "worker-paddle-stream":
                    records = [{"source_image": str(images[0]), "status": "ok", "text": "F=ma"}]
                else:
                    records = [{
                        "source_image": str(images[0]),
                        "status": "ok",
                        "candidates": [{"engine": "texteller", "text": "F=ma", "confidence": None}],
                        "errors": [],
                    }]
                return {"ok": True, "records": records, "worker_returncode": 0, "worker_stderr": ""}

            with (
                patch("formula_ensemble._run_stream_worker", side_effect=fake_stream),
                patch("formula_ensemble.time.monotonic", side_effect=[100.0, 112.2]),
            ):
                result = run_formula_ensemble([image], root / "out", timeout=100)

            self.assertTrue(result["ok"])
            self.assertEqual(calls, [("worker-paddle-stream", 100), ("worker-formula-stream", 87)])

    def test_merges_two_enabled_engines_into_hash_bound_consensus(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "formula.png"
            write_png(image)
            paddle = {"records": [{"source_image": str(image), "text": "F=ma", "confidence": 0.98}]}
            formula = {"records": [{
                "source_image": str(image),
                "candidates": [
                    {"engine": "texteller", "text": "F = ma", "confidence": None},
                ],
                "errors": [],
            }]}
            result = merge_engine_reports([image], paddle, formula)[0]
            self.assertEqual(result["source_sha256"], hashlib.sha256(image.read_bytes()).hexdigest())
            self.assertEqual(result["resolution"]["status"], "machine_final")
            self.assertEqual(result["resolution"]["final_latex"], "F=ma")

    def test_geometry_issue_forces_vlm_even_when_engines_agree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "formula.png"
            write_png(image)
            paddle = {"records": [{"source_image": str(image), "text": "F=ma", "confidence": 0.98}]}
            formula = {"records": [{
                "source_image": str(image),
                "candidates": [{"engine": "texteller", "text": "F=ma", "confidence": None}],
                "errors": [],
            }]}
            key = str(image.resolve())
            result = merge_engine_reports(
                [image], paddle, formula, geometry_by_image={key: ["content_occupancy_too_low"]}
            )[0]
            self.assertEqual(result["resolution"]["status"], "needs_vlm")


if __name__ == "__main__":
    unittest.main()
